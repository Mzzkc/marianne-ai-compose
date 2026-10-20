"""The Linux instrument supervisor: every descendant of an instrument dies with it.

Pallas, 20 Oct 2026: an agent's filesystem search helpers (a Glob from ``/``)
outlived their sheet at ppid 1 for hours and saturated the disk, because the
backend's kill reaches the instrument's process group while a grandchild whose
parent already exited had re-parented to init. The supervisor is a child
subreaper in the instrument's own group: such grandchildren re-parent to it,
and it ends and reaps them when the instrument exits or the attempt is torn
down.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.utils import instrument_supervisor
from marianne.utils.process import supervised_argv
from marianne.utils.run_supervisor import subreaper_available

pytestmark = pytest.mark.skipif(
    not subreaper_available(), reason="Linux PR_SET_CHILD_SUBREAPER required",
)

SUPERVISOR = str(instrument_supervisor.__file__)


def _state(pid: int) -> str | None:
    try:
        return open(f"/proc/{pid}/stat").read().split(") ", 1)[1][0]
    except FileNotFoundError:
        return None


def _gone(pid: int) -> bool:
    return _state(pid) in (None, "Z")


async def _wait_gone(pid: int, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _gone(pid):
            return True
        await asyncio.sleep(0.03)
    return False


def _run(*command: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", SUPERVISOR, *command],
        capture_output=True, text=True, timeout=30, check=False, **kwargs,  # type: ignore[arg-type]
    )


# The leak shape from the handoff: the instrument spawns a detached helper
# (its own session, closed stdio) and exits before the helper does.
_DISOWNING_INSTRUMENT = (
    "import subprocess, sys\n"
    "helper = subprocess.Popen(['sleep', '60'], start_new_session=True,\n"
    "                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
    "                          stderr=subprocess.DEVNULL)\n"
    "print('helper', helper.pid, flush=True)\n"
    "sys.exit(3)\n"
)


def test_disowned_grandchild_dies_with_the_instrument(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    result = _run("--report", str(report), "--", sys.executable, "-c", _DISOWNING_INSTRUMENT)
    helper_pid = int(result.stdout.split()[1])
    assert result.returncode == 3, "the instrument's own exit status passes through"
    assert _gone(helper_pid), "the disowned helper was adopted and ended"
    payload = json.loads(report.read_text())
    assert payload["exit_code"] == 3 and payload["signal"] is None
    assert payload["adopted_during_run"] == 1
    assert payload["swept_term"] == 1 and payload["unreaped"] == 0
    assert payload["io_priority"] == "low"


def test_stdio_and_exit_status_pass_through() -> None:
    result = _run("--", "cat", input="through the supervisor\n")
    assert result.stdout == "through the supervisor\n" and result.returncode == 0
    assert _run("--", "false").returncode == 1


def test_signal_to_the_supervisor_ends_the_instrument_and_propagates() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-I", SUPERVISOR, "--", "sleep", "60"],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 3
        kids: list[int] = []
        while time.monotonic() < deadline and not kids:
            kids = [int(line) for line in os.popen(f"pgrep -P {proc.pid}").read().split()]
            time.sleep(0.02)
        assert kids, "instrument did not start under the supervisor"
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=5) == -signal.SIGTERM
        assert _gone(kids[0])
    finally:
        if proc.poll() is None:
            proc.kill()


def test_instrument_killed_by_signal_reports_the_same_signal() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-I", SUPERVISOR, "--", "sleep", "60"],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 3
        kids: list[int] = []
        while time.monotonic() < deadline and not kids:
            kids = [int(line) for line in os.popen(f"pgrep -P {proc.pid}").read().split()]
            time.sleep(0.02)
        assert kids
        os.kill(kids[0], signal.SIGKILL)
        assert proc.wait(timeout=5) == -signal.SIGKILL
    finally:
        if proc.poll() is None:
            proc.kill()


def test_missing_executable_is_127_with_a_named_reason(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    result = _run("--report", str(report), "--", "/nonexistent/agent")
    assert result.returncode == 127
    assert "cannot start '/nonexistent/agent'" in result.stderr
    assert "spawn_error" in json.loads(report.read_text())


def test_instrument_shares_the_supervisor_group_and_inherits_low_io() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-I", SUPERVISOR, "--io", "low", "--", "sleep", "60"],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 3
        kids: list[int] = []
        while time.monotonic() < deadline and not kids:
            kids = [int(line) for line in os.popen(f"pgrep -P {proc.pid}").read().split()]
            time.sleep(0.02)
        assert kids
        assert os.getpgid(kids[0]) == os.getpgid(proc.pid) == proc.pid, (
            "the backend's group signal must still reach the instrument"
        )
        ioprio = os.popen(f"ionice -p {kids[0]}").read().strip()
        assert ioprio == "best-effort: prio 7", ioprio
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_supervised_argv_wraps_under_isolated_python() -> None:
    argv = supervised_argv(["claude", "-p", "-"], io_priority="idle")
    assert argv[:2] == [sys.executable, "-I"]
    assert argv[2] == SUPERVISOR
    assert argv[3:5] == ["--io", "idle"]
    assert argv[-4:] == ["--", "claude", "-p", "-"]


def _profile(*, supervise: bool) -> InstrumentProfile:
    return InstrumentProfile(
        name="supervised-cli",
        display_name="Supervised CLI",
        kind="cli",
        models=[ModelCapacity(name="test", context_window=1000,
                              cost_per_1k_input=0, cost_per_1k_output=0)],
        default_model="test",
        cli=CliProfile(
            command=CliCommand(executable="python3", prompt_flag="-c",
                               prompt_via_stdin=False, supervise=supervise),
            output=CliOutputConfig(format="text"),
            errors=CliErrorConfig(success_exit_codes=[0, 3]),
            post_exit_drain_grace_seconds=0.5,
        ),
    )


@pytest.mark.asyncio
async def test_plugin_cli_backend_does_not_leak_a_disowned_helper() -> None:
    """The backend path end to end: the helper is dead when the attempt returns."""
    backend = PluginCliBackend(_profile(supervise=True))
    result = await backend.execute(_DISOWNING_INSTRUMENT, timeout_seconds=10)
    helper_pid = int(result.stdout.split("helper", 1)[1].split()[0])
    assert result.exit_reason == "completed"
    assert result.exit_code == 0, "success_exit_codes normalises 3 to 0"
    assert await _wait_gone(helper_pid, 2.0), "helper outlived the supervised attempt"


@pytest.mark.asyncio
async def test_supervisor_ends_the_helper_before_the_backend_reaper_runs(monkeypatch) -> None:
    """The backend's own reaper (GH #406) finds nothing left to signal."""
    from marianne.execution.instruments import cli_backend

    events: list[tuple[str, dict]] = []

    class Recorder:
        def __getattr__(self, level):
            def record(event, **fields):
                events.append((event, fields))
            return record

    monkeypatch.setattr(cli_backend, "_logger", Recorder())
    backend = PluginCliBackend(_profile(supervise=True))
    result = await backend.execute(_DISOWNING_INSTRUMENT, timeout_seconds=10)
    helper_pid = int(result.stdout.split("helper", 1)[1].split()[0])
    assert await _wait_gone(helper_pid, 2.0)
    reaped = [fields for event, fields in events if event == "plugin_cli_descendants_reaped"]
    assert len(reaped) == 1 and reaped[0]["signalled"] == 0
    complete = [fields for event, fields in events if event == "plugin_cli_execute_complete"]
    assert complete[0]["post_exit_grace_fired"] is False, (
        "no grandchild held the pipes past the instrument's exit"
    )


@pytest.mark.asyncio
async def test_unsupervised_profile_keeps_the_direct_spawn() -> None:
    backend = PluginCliBackend(_profile(supervise=False))
    spawned: list[int] = []
    backend._on_process_spawned = spawned.append
    result = await backend.execute("print('direct')", timeout_seconds=10)
    assert result.stdout.strip() == "direct"
    # The direct child was python itself, not the supervisor.
    assert spawned and result.exit_code == 0


def test_profile_defaults_supervise_with_low_io() -> None:
    command = CliCommand(executable="x")
    assert command.supervise is True and command.io_priority == "low"
    with pytest.raises(ValueError):
        CliCommand(executable="x", io_priority="high")  # type: ignore[arg-type]
