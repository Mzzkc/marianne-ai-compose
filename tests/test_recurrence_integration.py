"""Integration coverage for schedule registry, baton timer, and JobManager."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from marianne.daemon.baton.events import CronTick
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager
from marianne.daemon.schedule_registry import ScheduleRecord, ScheduleRegistry
from marianne.daemon.types import JobRequest, JobResponse


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


async def _wait_for_outcome(
    registry: ScheduleRegistry,
    schedule_id: str,
    outcome: str,
) -> ScheduleRecord:
    async with asyncio.timeout(1.0):
        while True:
            record = await registry.get(schedule_id)
            if record is not None and record.last_outcome == outcome:
                return record
            await asyncio.sleep(0)


def _write_scheduled_score(path: Path, workspace: Path) -> None:
    path.write_text(
        "name: Daily Report\n"
        f"workspace: {workspace}\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: Write the report.\n"
        "schedule:\n"
        "  interval: 5m\n",
        encoding="utf-8",
    )


async def test_manager_submits_due_child_and_restart_restores_one_timer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real baton tick reaches normal submission once and survives restart."""
    state_db = tmp_path / "conductor-state.db"
    score_path = tmp_path / "recurring-score.yaml"
    _write_scheduled_score(score_path, tmp_path / "workspace")
    daemon_config = DaemonConfig(
        max_concurrent_jobs=2,
        pid_file=tmp_path / "daemon.pid",
        state_db_path=state_db,
    )
    manager = JobManager(daemon_config)
    submitted: list[JobRequest] = []
    immediate_started = asyncio.Event()
    release_immediate = asyncio.Event()
    release_child = asyncio.Event()
    original_submit = manager.submit_job

    async def recording_submit(request: JobRequest) -> JobResponse:
        submitted.append(request)
        return await original_submit(request)

    async def complete_without_execution(job_id: str, request: JobRequest) -> None:
        if request.scheduled_due_at is None:
            immediate_started.set()
            await release_immediate.wait()
        else:
            await release_child.wait()
        await manager._set_job_status(job_id, DaemonJobStatus.COMPLETED)

    monkeypatch.setattr(manager, "submit_job", recording_submit)
    monkeypatch.setattr(manager, "_run_job_task", complete_without_execution)

    await manager.start()
    try:
        controller = manager._recurrence_controller
        adapter = manager._baton_adapter
        assert controller is not None
        assert adapter is not None

        start = datetime.now(tz=UTC).replace(microsecond=0)
        controller._now = lambda: start
        response = await manager.submit_job(JobRequest(config_path=score_path))
        assert response.status == "accepted"
        assert response.job_id == "recurring-score"
        await asyncio.wait_for(immediate_started.wait(), timeout=1.0)

        immediate = manager._job_meta[response.job_id]
        assert immediate.schedule_id == "Daily Report"
        assert manager._is_schedule_active("Daily Report") is True

        first = await manager._schedule_registry.get("Daily Report")
        assert first is not None
        first_due = first.next_due_at
        first_handle = controller._timers["Daily Report"][1]
        assert adapter.cancel_cron_tick(first_handle) is True
        controller._now = lambda: datetime.fromtimestamp(first_due, tz=UTC)
        adapter.schedule_cron_tick(
            0.0,
            CronTick(
                entry_name="Daily Report",
                score_path=str(score_path),
                due_at=first_due,
                timestamp=first_due,
            ),
        )

        overlapped = await _wait_for_outcome(
            manager._schedule_registry,
            "Daily Report",
            "overlap_skipped",
        )
        assert [request.scheduled_due_at for request in submitted] == [None]

        release_immediate.set()
        await _wait_until(
            lambda: manager._job_meta[response.job_id].status
            is DaemonJobStatus.COMPLETED
        )

        second_due = overlapped.next_due_at
        second_handle = controller._timers["Daily Report"][1]
        assert adapter.cancel_cron_tick(second_handle) is True
        controller._now = lambda: datetime.fromtimestamp(second_due, tz=UTC)
        adapter.schedule_cron_tick(
            0.0,
            CronTick(
                entry_name="Daily Report",
                score_path=str(score_path),
                due_at=second_due,
                timestamp=second_due,
            ),
        )

        advanced = await _wait_for_outcome(
            manager._schedule_registry,
            "Daily Report",
            "submitted",
        )
        child = next(
            request for request in submitted if request.scheduled_due_at is not None
        )
        expected_stamp = datetime.fromtimestamp(second_due, tz=UTC).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        assert child.job_id == f"Daily-Report--scheduled--{expected_stamp}"
        assert child.schedule_id == "Daily Report"
        assert child.scheduled_due_at == second_due
        assert advanced.last_run_id == child.job_id
        next_due = advanced.next_due_at
        release_child.set()
    finally:
        release_immediate.set()
        release_child.set()
        await manager.shutdown(graceful=False)

    restarted = JobManager(daemon_config)
    restarted_submissions: list[JobRequest] = []
    restarted_submit = restarted.submit_job

    async def record_restart_submit(request: JobRequest) -> JobResponse:
        restarted_submissions.append(request)
        return await restarted_submit(request)

    async def unexpected_execution(_job_id: str, _request: JobRequest) -> None:
        raise AssertionError("Restart must not replay the prior due identity")

    monkeypatch.setattr(restarted, "submit_job", record_restart_submit)
    monkeypatch.setattr(restarted, "_run_job_task", unexpected_execution)
    await restarted.start()
    try:
        adapter = restarted._baton_adapter
        assert adapter is not None
        cron_events = [
            event
            for _fire_at, event in adapter._timer_wheel.snapshot()
            if isinstance(event, CronTick)
        ]
        assert len(cron_events) == 1
        assert cron_events[0].due_at == next_due
        await asyncio.sleep(0)
        assert restarted_submissions == []
    finally:
        await restarted.shutdown(graceful=False)
