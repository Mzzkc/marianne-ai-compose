"""GH #476: a graceful drain names what it is waiting on.

Eight jobs paused at a sheet boundary before ``mzt stop --wait 300`` held the
drain for the full 300 s with ``running_jobs=0`` in the log, and the CLI then
reported "did not exit" four seconds before the daemon logged ``daemon.stopped``.
The drain was correct; nothing named the in-flight sheets it waited on.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.config import DaemonConfig
from marianne.daemon.health import HealthChecker
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.process import _format_in_flight

runner = CliRunner()


@pytest.fixture
def daemon_config(tmp_path: Path) -> DaemonConfig:
    return DaemonConfig(
        max_concurrent_jobs=2,
        pid_file=tmp_path / "test.pid",
        state_db_path=tmp_path / "test-registry.db",
    )


async def _blocked() -> None:
    await asyncio.Event().wait()


# ─── adapter ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adapter_lists_live_musicians_with_instrument_model_and_elapsed() -> None:
    adapter = BatonAdapter()
    live = asyncio.create_task(_blocked())
    done = asyncio.create_task(asyncio.sleep(0))
    await done
    try:
        adapter._active_tasks[("solver", 6)] = live
        adapter._active_execution_details[("solver", 6)] = ("claude-code", "claude-opus-4-7")
        adapter._active_started_at[("solver", 6)] = time.monotonic() - 42.0
        adapter._active_tasks[("solver", 2)] = done  # finished: not in flight
        adapter._active_tasks[("cut4f", 9)] = asyncio.create_task(_blocked())

        rows = adapter.active_musicians()

        assert [(r["job_id"], r["sheet_num"]) for r in rows] == [("cut4f", 9), ("solver", 6)]
        solver = rows[1]
        assert solver["instrument"] == "claude-code"
        assert solver["model"] == "claude-opus-4-7"
        assert 41.0 <= solver["elapsed_seconds"] <= 60.0
        # No dispatch record (a task registered outside dispatch): fields are
        # empty rather than wrong.
        assert rows[0]["instrument"] == ""
        assert rows[0]["elapsed_seconds"] is None
    finally:
        for task in list(adapter._active_tasks.values()):
            task.cancel()
        await asyncio.gather(*adapter._active_tasks.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_adapter_forgets_start_time_when_the_musician_finishes() -> None:
    adapter = BatonAdapter()
    adapter._active_started_at[("j", 1)] = time.monotonic()
    task = asyncio.create_task(asyncio.sleep(0))
    adapter._active_tasks[("j", 1)] = task
    await task
    adapter._on_musician_done("j", 1, task, event_generation=None, dispatch_epoch=0)
    assert ("j", 1) not in adapter._active_started_at
    assert adapter.active_musicians() == []


# ─── manager ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drain_timeout_logs_the_sheets_it_waited_on(
    daemon_config: DaemonConfig,
) -> None:
    daemon_config.shutdown_timeout_seconds = 0.2
    mgr = JobManager(daemon_config)
    await mgr._registry.open()
    mgr._service = MagicMock()

    musician = asyncio.create_task(_blocked())
    adapter = MagicMock()
    adapter._active_tasks = {("paused-job", 3): musician}
    adapter.active_musicians.return_value = [
        {"job_id": "paused-job", "sheet_num": 3, "instrument": "codex-cli",
         "model": None, "elapsed_seconds": 12.0},
    ]
    adapter.shutdown = AsyncMock()
    adapter._baton = MagicMock()
    adapter._baton.inbox = MagicMock()
    mgr._baton_adapter = adapter

    wrapper = asyncio.create_task(_blocked())
    mgr._jobs["paused-job"] = wrapper
    mgr._job_meta["paused-job"] = JobMeta(
        job_id="paused-job",
        config_path=Path("/tmp/test.yaml"),
        workspace=Path("/tmp/workspace"),
        status=DaemonJobStatus.PAUSED,  # pause requested, sheet 3 still playing
    )
    assert mgr.in_flight_sheets[0]["sheet_num"] == 3

    with patch("marianne.daemon.manager._logger") as log:
        await mgr.shutdown(graceful=True)

    starts = [c for c in log.info.call_args_list if c.args[0] == "manager.shutting_down"]
    assert starts and starts[0].kwargs["in_flight"][0]["job_id"] == "paused-job"
    exceeded = [
        c for c in log.warning.call_args_list
        if c.args[0] == "manager.shutdown_timeout_exceeded"
    ]
    assert exceeded, "the drain timeout must name what held it"
    assert exceeded[0].kwargs["held_by"][0]["instrument"] == "codex-cli"
    musician.cancel()
    await asyncio.gather(musician, return_exceptions=True)


def test_manager_without_adapter_has_nothing_in_flight(daemon_config: DaemonConfig) -> None:
    mgr = JobManager(daemon_config)
    mgr._baton_adapter = None
    assert mgr.in_flight_sheets == []


# ─── readiness ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_readiness_carries_in_flight_rows() -> None:
    manager = MagicMock()
    manager.running_count = 0
    manager.failure_rate_elevated = False
    manager.notifications_degraded = False
    manager.shutting_down = True
    manager.in_flight_sheets = [
        {"job_id": "j", "sheet_num": 4, "instrument": "opencode",
         "model": "ollama/gemma4:26b", "elapsed_seconds": 3.0},
    ]
    monitor = MagicMock()
    snapshot = MagicMock(memory_usage_mb=1.0, child_process_count=0)
    monitor.check_now = AsyncMock(return_value=snapshot)
    monitor.is_accepting_work.return_value = True
    monitor.event_loop_lag_ms = 0.0
    monitor.event_loop_lag_peak_ms = 0.0
    ready = await HealthChecker(manager, monitor).readiness()
    assert ready["running_jobs"] == 0
    assert ready["shutting_down"] is True
    assert ready["in_flight"] == manager.in_flight_sheets


# ─── CLI ─────────────────────────────────────────────────────────────────

_IN_FLIGHT = [
    {"job_id": "issue-solver", "sheet_num": 6, "instrument": "claude-code",
     "model": "claude-opus-4-7", "elapsed_seconds": 1234.0},
    {"job_id": "cut4f", "sheet_num": 9, "instrument": "codex-cli",
     "model": None, "elapsed_seconds": None},
]


def test_format_in_flight_names_job_sheet_instrument_model_elapsed() -> None:
    lines = _format_in_flight(_IN_FLIGHT)
    assert lines == [
        "  issue-solver sheet 6 on claude-code/claude-opus-4-7 for 1234s",
        "  cut4f sheet 9 on codex-cli",
    ]


def test_stop_names_in_flight_sheets_before_waiting(tmp_path: Path) -> None:
    pid_file = tmp_path / "marianne.pid"
    pid_file.write_text("12345")
    with (
        patch("marianne.daemon.process._pid_alive", return_value=True),
        patch("marianne.daemon.process.os.kill"),
        patch(
            "marianne.daemon.process._check_running_jobs",
            return_value={"running_jobs": 0, "job_ids": [], "in_flight": _IN_FLIGHT},
        ),
        patch("marianne.daemon.process.wait_for_conductor_exit", return_value=True),
    ):
        result = runner.invoke(app, ["stop", "--pid-file", str(pid_file), "--wait=5"])
    assert result.exit_code == 0, result.output
    assert "waiting on 2 in-flight sheet(s)" in result.output
    assert "issue-solver sheet 6 on claude-code/claude-opus-4-7 for 1234s" in result.output
    assert "re-runs on resume" in result.output
    assert "Marianne conductor stopped" in result.output


def test_stop_timeout_reprobes_and_names_what_still_holds_the_drain(tmp_path: Path) -> None:
    pid_file = tmp_path / "marianne.pid"
    pid_file.write_text("12345")
    probes = [
        {"running_jobs": 0, "job_ids": [], "in_flight": _IN_FLIGHT},
        {"running_jobs": 0, "job_ids": [], "in_flight": _IN_FLIGHT[:1]},
    ]
    with (
        patch("marianne.daemon.process._pid_alive", return_value=True),
        patch("marianne.daemon.process.os.kill"),
        patch("marianne.daemon.process._check_running_jobs", side_effect=probes) as probe,
        patch("marianne.daemon.process.wait_for_conductor_exit", return_value=False),
    ):
        result = runner.invoke(app, ["stop", "--pid-file", str(pid_file), "--wait=5"])
    assert result.exit_code == 1
    assert probe.call_count == 2
    assert "did not exit within 5s. It is still draining." in result.output
    assert "Waiting on 1 in-flight sheet(s):" in result.output
    assert "issue-solver sheet 6" in result.output
    assert "cut4f sheet 9" not in result.output.split("still draining", 1)[1]
    assert "shutdown_timeout_seconds" in result.output


def test_stop_timeout_with_silent_socket_still_reports_truthfully(tmp_path: Path) -> None:
    """The drain's last seconds close the socket; the message must not invent rows."""
    pid_file = tmp_path / "marianne.pid"
    pid_file.write_text("12345")
    probes = [{"running_jobs": 0, "job_ids": [], "in_flight": []}, None]
    with (
        patch("marianne.daemon.process._pid_alive", return_value=True),
        patch("marianne.daemon.process.os.kill"),
        patch("marianne.daemon.process._check_running_jobs", side_effect=probes),
        patch("marianne.daemon.process.wait_for_conductor_exit", return_value=False),
    ):
        result = runner.invoke(app, ["stop", "--pid-file", str(pid_file), "--wait="])
    assert result.exit_code == 1
    assert "did not exit within 30s. It is still draining." in result.output
    assert "Waiting on" not in result.output


def test_stop_with_nothing_in_flight_prints_no_drain_block(tmp_path: Path) -> None:
    pid_file = tmp_path / "marianne.pid"
    pid_file.write_text("12345")
    with (
        patch("marianne.daemon.process._pid_alive", return_value=True),
        patch("marianne.daemon.process.os.kill"),
        patch(
            "marianne.daemon.process._check_running_jobs",
            return_value={"running_jobs": 0, "job_ids": [], "in_flight": []},
        ),
    ):
        result = runner.invoke(app, ["stop", "--pid-file", str(pid_file)])
    assert result.exit_code == 0
    assert "in-flight" not in result.output


def test_check_running_jobs_tolerates_a_conductor_without_in_flight(tmp_path: Path) -> None:
    """An older conductor's daemon.ready has no in_flight key: the probe still works."""
    from marianne.daemon.process import _check_running_jobs

    client = MagicMock(
        readiness=AsyncMock(return_value={"running_jobs": 1, "job_ids": ["j"]}),
        close=AsyncMock(),
    )
    with patch("marianne.daemon.ipc.client.DaemonClient", return_value=client):
        info = _check_running_jobs(tmp_path / "x.sock")
    assert info == {"running_jobs": 1, "job_ids": ["j"], "in_flight": []}
