"""GH #455: a live conductor that does not answer is busy, never absent.

Absence needs two proofs (socket missing, or connect refused with no live
PID). With a live PID the CLI names it, reports ``running_state: unknown``
and never suggests ``mzt start``, ``mzt restart`` or ``mzt stop --force``.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

import marianne.cli.helpers as helpers
from marianne.cli import app
from marianne.daemon import detect

FORBIDDEN = ("mzt start", "mzt restart", "stop --force", "not running")


def _live(monkeypatch: pytest.MonkeyPatch, pid: int = 4242) -> None:
    monkeypatch.setattr(helpers, "live_conductor_pid", lambda: pid)
    monkeypatch.setattr(helpers, "conductor_socket_present", lambda: True)


def _score(tmp_path: Path) -> Path:
    config = tmp_path / "score.yaml"
    config.write_text(
        "name: busy-455\nsheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: Test only\n"
    )
    return config


def _args(command: str, tmp_path: Path) -> list[str]:
    if command == "run":
        return ["run", str(_score(tmp_path))]
    if command in ("errors", "diagnose", "history", "status-job"):
        return [command.replace("-job", ""), "some-job"]
    return [command]


@pytest.mark.parametrize("failure", ["unresponsive", "refused"])
@pytest.mark.parametrize("json_output", [True, False])
@pytest.mark.parametrize(
    "command", ["list", "status", "status-job", "errors", "diagnose", "history", "run"],
)
def test_live_pid_reads_as_busy_on_every_routed_read(
    monkeypatch, tmp_path, command, json_output, failure,
):
    from marianne.daemon.exceptions import DaemonUnresponsiveError

    _live(monkeypatch)

    async def route(*args, **kwargs):
        if failure == "unresponsive":
            raise DaemonUnresponsiveError("first write failed (EINVAL)")
        return False, None

    monkeypatch.setattr(detect, "try_daemon_route", route)
    if command == "run":
        async def available(*args, **kwargs):
            return failure == "unresponsive"

        monkeypatch.setattr(detect, "is_daemon_available", available)
    args = _args(command, tmp_path)
    if command == "run" and failure == "unresponsive":
        # Submission reaches try_daemon_route only after the availability probe.
        pass
    result = CliRunner().invoke(app, [*args, *(["--json"] if json_output else [])])
    assert result.exit_code == 1, result.output
    lowered = result.output.lower()
    for word in FORBIDDEN:
        assert word not in lowered, (word, result.output)
    assert "pid 4242" in lowered, result.output
    if json_output:
        payload = json.loads(result.output)
        assert payload["running_state"] == "unknown"
        assert payload["conductor_pid"] == 4242
        assert payload["error_type"] == (
            "DaemonUnresponsiveError" if failure == "unresponsive" or command == "run"
            else "DaemonNotRunningError"
        )


def test_refused_without_live_pid_is_still_absent(monkeypatch):
    async def route(*args, **kwargs):
        return False, None

    monkeypatch.setattr(detect, "try_daemon_route", route)
    monkeypatch.setattr(helpers, "conductor_socket_present", lambda: True)
    monkeypatch.setattr(helpers, "live_conductor_pid", lambda: None)
    result = CliRunner().invoke(app, ["list", "--json"])
    payload = json.loads(result.output)
    assert payload["running_state"] == "absent"
    assert "mzt start" in " ".join(payload["hints"])


def test_missing_socket_is_absent_even_with_a_live_pid(monkeypatch):
    """A PID with no socket is a conductor that has not bound yet or has
    torn down; the acceptance text makes a missing socket proof of absence."""
    async def route(*args, **kwargs):
        return False, None

    monkeypatch.setattr(detect, "try_daemon_route", route)
    monkeypatch.setattr(helpers, "conductor_socket_present", lambda: False)
    monkeypatch.setattr(helpers, "live_conductor_pid", lambda: 4242)
    result = CliRunner().invoke(app, ["list", "--json"])
    assert json.loads(result.output)["running_state"] == "absent"


def test_repeated_reads_never_drift_from_busy_to_absent(monkeypatch):
    """Each probe that times out parks a connect in the queue; the next read
    sees a fuller queue, never a deader conductor, while the PID is alive."""
    from marianne.daemon.exceptions import DaemonUnresponsiveError

    _live(monkeypatch)
    calls = {"n": 0}

    async def route(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 6:
            raise DaemonUnresponsiveError("timeout")
        return False, None  # try_daemon_route's shape for a refused connect

    monkeypatch.setattr(detect, "try_daemon_route", route)
    for _ in range(8):
        result = CliRunner().invoke(app, ["list", "--json"])
        assert result.output.lstrip().startswith("{"), result.output
        assert json.loads(result.output)["running_state"] == "unknown", result.output


def test_watch_names_the_pid_instead_of_mzt_start(monkeypatch):
    from marianne.daemon.exceptions import DaemonNotRunningError
    from marianne.daemon.ipc import client as client_module

    _live(monkeypatch)

    class _Client:
        def __init__(self, *a, **k): ...
        async def close(self): ...
        async def stream(self, *a, **k):
            raise DaemonNotRunningError("refused")
            yield  # pragma: no cover

    monkeypatch.setattr(client_module, "DaemonClient", _Client)
    result = CliRunner().invoke(app, ["watch", "some-job"])
    assert result.exit_code == 1
    assert "pid 4242" in result.output.lower()
    assert "mzt start" not in result.output


def test_conductor_status_socket_only_reports_pid_alive_when_silent(
    tmp_path, monkeypatch, capsys,
):
    import marianne.daemon.process as process_module

    pid_file = tmp_path / "clone.pid"
    pid_file.write_text("4242")
    monkeypatch.setattr(process_module, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        process_module.asyncio, "run",
        lambda coro: (coro.close(), (None, None, None))[1],
    )
    with pytest.raises(process_module.typer.Exit):
        process_module.get_conductor_status(
            pid_file=pid_file, socket_path=tmp_path / "clone.sock", as_json=True,
        )
    captured = capsys.readouterr()
    assert "PID 4242 is alive" in captured.err
    assert "restart" in captured.err and "Do not start" in captured.err
    payload = json.loads(captured.out)
    assert payload == {
        **payload, "pid": 4242, "alive": True,
        "socket_answered": False, "running_state": "unknown",
    }
    assert "io_pressure" in payload


def test_conductor_status_default_mode_names_pid_and_pressure_when_silent(
    tmp_path, monkeypatch, capsys,
):
    import marianne.daemon.process as process_module

    pid_file = tmp_path / "mzt.pid"
    pid_file.write_text("4242")
    monkeypatch.setattr(process_module, "_pid_alive", lambda pid: True)

    def _run(coro):
        coro.close()
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(process_module.asyncio, "run", _run)
    from marianne.daemon import system_probe

    monkeypatch.setattr(
        system_probe, "read_io_pressure",
        lambda: {"some_avg10": 62.5, "full_avg10": 42.0},
    )
    process_module.get_conductor_status(pid_file=pid_file)
    out = capsys.readouterr().out
    assert "PID 4242 is alive" in out
    assert "full 42.0%" in out
    assert "mzt start" not in out and "mzt restart" not in out


def test_read_io_pressure_parses_psi(tmp_path):
    from marianne.daemon.system_probe import read_io_pressure

    psi = tmp_path / "io"
    psi.write_text(
        "some avg10=62.55 avg60=25.81 avg300=24.07 total=95309149073\n"
        "full avg10=42.07 avg60=16.71 avg300=12.56 total=70374757242\n"
    )
    assert read_io_pressure(psi) == {"some_avg10": 62.55, "full_avg10": 42.07}
    assert read_io_pressure(tmp_path / "absent") is None


@pytest.mark.asyncio
async def test_readiness_carries_loop_lag_and_io_pressure():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from marianne.daemon.config import ResourceLimitConfig
    from marianne.daemon.health import HealthChecker
    from marianne.daemon.monitor import ResourceMonitor

    manager = MagicMock()
    manager.running_count = 0
    manager.active_job_count = 0
    manager.failure_rate_elevated = False
    manager.notifications_degraded = False
    manager.shutting_down = False
    monitor = ResourceMonitor(ResourceLimitConfig(), manager=manager)
    monitor._evaluate = AsyncMock()  # type: ignore[method-assign]
    monitor.LAG_SAMPLE_SECONDS = 0.01
    await monitor.start(interval_seconds=60.0)
    try:
        deadline = time.monotonic() + 5.0
        while monitor.event_loop_lag_ms is None and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert monitor.event_loop_lag_ms is not None
        assert monitor.event_loop_lag_peak_ms >= monitor.event_loop_lag_ms
        # Block the loop; a following sample must show it.
        time.sleep(0.15)
        deadline = time.monotonic() + 5.0
        while (monitor.event_loop_lag_peak_ms or 0.0) < 100.0 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert monitor.event_loop_lag_peak_ms >= 100.0
        with (
            patch.object(monitor, "_get_memory_usage_mb", return_value=1.0),
            patch.object(monitor, "_get_child_process_count", return_value=0),
            patch.object(monitor, "_check_for_zombies", return_value=[]),
        ):
            ready = await HealthChecker(manager, monitor).readiness()
        assert ready["event_loop_lag_ms"] == monitor.event_loop_lag_ms
        assert "io_pressure" in ready
    finally:
        await monitor.stop()
    assert monitor._lag_task is None


def test_log_rollover_compresses_off_the_logging_thread(tmp_path):
    from marianne.core.logging import CompressingRotatingFileHandler

    log_file = tmp_path / "conductor.log"
    handler = CompressingRotatingFileHandler(log_file, maxBytes=200, backupCount=3)
    logging_thread = threading.current_thread().name
    seen: list[str] = []
    original = handler._compress

    def spy(plain: str, compressed: str) -> None:
        seen.append(threading.current_thread().name)
        original(plain, compressed)

    handler._compress = spy  # type: ignore[method-assign]
    record = logging.LogRecord("t", logging.INFO, "", 0, "x" * 120, (), None)
    for _ in range(6):
        handler.emit(record)
    handler.close()  # joins the compression thread
    assert seen and all(name != logging_thread for name in seen)
    assert handler._compress_thread is None
    backups = sorted(p.name for p in tmp_path.glob("conductor.log.*"))
    assert backups and all(name.endswith(".gz") for name in backups), backups
    with gzip.open(tmp_path / "conductor.log.1.gz", "rb") as fh:
        assert b"x" * 120 in fh.read()


def test_socket_backlog_default_clears_a_crowd_of_readers():
    from marianne.daemon.config import SocketConfig

    assert SocketConfig().backlog >= 64
