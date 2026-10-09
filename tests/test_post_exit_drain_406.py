"""Real subprocess regressions for parent exit with inherited pipes (#406)."""

from __future__ import annotations

import asyncio
import os
import signal
import time

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.execution.base import SheetRequestState
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _profile(*, grace: float = 0.2) -> InstrumentProfile:
    return InstrumentProfile(
        name="drain-406",
        display_name="Drain 406",
        kind="cli",
        models=[ModelCapacity(name="test", context_window=1000,
                              cost_per_1k_input=0, cost_per_1k_output=0)],
        default_model="test",
        cli=CliProfile(
            command=CliCommand(executable="python3", prompt_flag="-c",
                               prompt_via_stdin=False, start_new_session=True),
            output=CliOutputConfig(format="text"),
            post_exit_drain_grace_seconds=grace,
        ),
    )


def _state(pid: int) -> str | None:
    try:
        return open(f"/proc/{pid}/stat").read().split(") ", 1)[1][0]
    except FileNotFoundError:
        return None


async def _wait_dead(pid: int) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if _state(pid) in (None, "Z"):
            return True
        await asyncio.sleep(0.03)
    return False


@pytest.mark.asyncio
async def test_exited_parent_held_pipe_completes_and_reaps_group() -> None:
    backend = PluginCliBackend(_profile())
    parent_pids: list[int] = []
    backend._on_process_spawned = parent_pids.append
    script = (
        "import subprocess, sys\n"
        "child = subprocess.Popen(['sleep', '30'])\n"
        "print(f'done child={child.pid}', flush=True)\n"
    )
    child_pid: int | None = None
    try:
        start = time.monotonic()
        result = await backend.execute(script, timeout_seconds=3)
        elapsed = time.monotonic() - start
        assert result.exit_reason == "completed"
        assert result.exit_code == 0
        assert result.success
        assert elapsed < 30
        assert "done child=" in result.stdout
        child_pid = int(result.stdout.split("child=", 1)[1].split()[0])
        assert await _wait_dead(child_pid)
    finally:
        if parent_pids:
            try:
                os.killpg(parent_pids[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
        if child_pid is not None and _state(child_pid) not in (None, "Z"):
            os.kill(child_pid, signal.SIGKILL)


@pytest.mark.asyncio
async def test_exited_parent_reaps_detached_pipe_owner() -> None:
    backend = PluginCliBackend(_profile(grace=0.1))
    child_pid: int | None = None
    try:
        start = time.monotonic()
        result = await backend.execute(
            "import subprocess, time; c=subprocess.Popen(['sleep','30'],"
            "start_new_session=True); print(f'child={c.pid}', flush=True);"
            "time.sleep(0.05)",
            timeout_seconds=3,
        )
        child_pid = int(result.stdout.split("child=", 1)[1].split()[0])
        assert result.exit_reason == "completed"
        assert result.exit_code == 0
        assert time.monotonic() - start < 30
        assert await _wait_dead(child_pid)
    finally:
        if child_pid is not None and _state(child_pid) not in (None, "Z"):
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
async def test_live_parent_still_times_out_with_partial_output() -> None:
    backend = PluginCliBackend(_profile())
    start = time.monotonic()
    result = await backend.execute(
        "import time; print('partial', flush=True); time.sleep(30)",
        timeout_seconds=0.35,
    )
    elapsed = time.monotonic() - start
    assert result.exit_reason == "timeout"
    assert result.exit_code is None
    assert "partial" in result.stdout
    assert elapsed >= 0.3
    assert elapsed < 30


@pytest.mark.asyncio
async def test_fast_parent_exit_and_nonzero_preserve_output() -> None:
    backend = PluginCliBackend(_profile())
    result = await backend.execute(
        "import sys; print('ordinary'); print('error', file=sys.stderr); sys.exit(4)",
        timeout_seconds=3,
    )
    assert result.exit_code == 4
    assert result.exit_reason == "completed"
    assert result.stdout == "ordinary\n"
    assert result.stderr == "error\n"


def test_grace_override_must_be_positive() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _profile(grace=0)


@pytest.mark.asyncio
async def test_log_events_bind_request_bytes_and_timeout_phase(monkeypatch) -> None:
    from marianne.execution.instruments import cli_backend

    events: list[tuple[str, dict]] = []

    class Recorder:
        def __getattr__(self, level):
            def record(event, **fields):
                events.append((event, fields))
            return record

    monkeypatch.setattr(cli_backend, "_logger", Recorder())
    backend = PluginCliBackend(_profile(grace=0.1))
    request = SheetRequestState(job_id="job-406", sheet_num=3)
    normal = await backend.execute("print('ok')", timeout_seconds=3, request=request)
    held = await backend.execute(
        "import subprocess; subprocess.Popen(['sleep', '30']); print('done', flush=True)",
        timeout_seconds=3, request=request,
    )
    hung = await backend.execute(
        "import time; print('partial', flush=True); time.sleep(30)",
        timeout_seconds=0.2, request=request,
    )
    assert normal.exit_code == 0
    assert held.exit_code == 0 and held.stdout == "done\n"
    assert hung.exit_reason == "timeout"
    complete = [fields for event, fields in events
                if event == "plugin_cli_execute_complete"]
    timeout = [fields for event, fields in events if event == "plugin_cli_timeout"]
    assert len(complete) == 3 and len(timeout) == 1
    assert [item["post_exit_grace_fired"] for item in complete] == [False, True, False]
    assert [item["returncode_at_timeout"] for item in complete] == [None, 0, None]
    assert [item["stdout_bytes"] for item in complete] == [3, 5, 8]
    assert timeout[0]["returncode_at_timeout"] is None
    assert timeout[0]["stdout_bytes"] == 8
    assert timeout[0]["post_exit_grace_fired"] is False
    for fields in [*complete, *timeout]:
        assert fields["job_id"] == "job-406"
        assert fields["sheet_num"] == 3
        assert fields["stderr_bytes"] == 0
