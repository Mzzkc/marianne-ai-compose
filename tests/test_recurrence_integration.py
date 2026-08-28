"""Integration coverage for schedule registry, baton timer, and JobManager."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from marianne.core.config import JobConfig
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import CronTick
from marianne.daemon.config import DaemonConfig
from marianne.daemon.exceptions import JobSubmissionError
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.recurrence import RecurrenceController
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


async def test_overdue_latest_waits_for_orphan_interactive_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup cannot dispatch a catch-up run before orphan sessions are swept."""
    state_db = tmp_path / "conductor-state.db"
    score_path = tmp_path / "overdue-score.yaml"
    score_path.write_text(
        "name: Overdue Report\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: Write the overdue report.\n"
        "schedule:\n"
        "  interval: 5m\n"
        "  misfire: latest\n",
        encoding="utf-8",
    )
    config = JobConfig.from_yaml(score_path)
    assert config.schedule is not None
    registry = ScheduleRegistry(state_db)
    await registry.open()
    due_at = (datetime.now(tz=UTC) - timedelta(minutes=5)).timestamp()
    await registry.upsert(
        config.name,
        config.name,
        score_path.resolve(),
        config.schedule,
        hashlib.sha256(score_path.read_bytes()).hexdigest(),
        due_at,
    )
    await registry.close()

    daemon_config = DaemonConfig(
        max_concurrent_jobs=2,
        pid_file=tmp_path / "daemon.pid",
        state_db_path=state_db,
    )
    manager = JobManager(daemon_config)
    sweep_entered = asyncio.Event()
    release_sweep = asyncio.Event()
    sweep_finished = asyncio.Event()
    submit_called = asyncio.Event()
    submitted_after_sweep: list[bool] = []

    async def held_sweep() -> None:
        sweep_entered.set()
        await release_sweep.wait()
        sweep_finished.set()

    async def record_submission(request: JobRequest) -> JobResponse:
        submitted_after_sweep.append(sweep_finished.is_set())
        submit_called.set()
        assert request.job_id is not None
        return JobResponse(job_id=request.job_id, status="accepted")

    monkeypatch.setattr(manager, "_sweep_orphan_interactive_sessions", held_sweep)
    monkeypatch.setattr(manager, "submit_job", record_submission)

    start_task = asyncio.create_task(manager.start())
    try:
        await asyncio.wait_for(sweep_entered.wait(), timeout=1.0)
        assert submit_called.is_set() is False
        release_sweep.set()
        await start_task
        await asyncio.wait_for(submit_called.wait(), timeout=1.0)
        assert submitted_after_sweep == [True]
    finally:
        release_sweep.set()
        if not start_task.done():
            await start_task
        await manager.shutdown(graceful=False)


async def test_manual_registration_reserves_lineage_before_metadata_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An armed sub-second due sees the pending manual run as active lineage."""
    state_db = tmp_path / "conductor-state.db"
    score_path = tmp_path / "reserved-score.yaml"
    score_path.write_text(
        "name: Reserved Report\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: Write the reserved report.\n"
        "schedule:\n"
        "  interval: 0.25s\n",
        encoding="utf-8",
    )
    daemon_config = DaemonConfig(
        max_concurrent_jobs=2,
        pid_file=tmp_path / "daemon.pid",
        state_db_path=state_db,
    )
    manager = JobManager(daemon_config)
    await manager._registry.open()
    await manager._schedule_registry.open()
    adapter = BatonAdapter()
    start = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    current = start
    scheduled_submit_entered = asyncio.Event()
    metadata_window = asyncio.Event()
    release_metadata = asyncio.Event()
    release_jobs = asyncio.Event()
    original_submit = manager.submit_job
    original_register_job = manager._registry.register_job

    async def tracked_submit(request: JobRequest) -> JobResponse:
        scheduled_submit_entered.set()
        return await original_submit(request)

    async def held_register_job(
        job_id: str,
        config_path: Path,
        workspace: Path,
        log_path: Path | None = None,
    ) -> None:
        metadata_window.set()
        await release_metadata.wait()
        await original_register_job(
            job_id,
            config_path,
            workspace,
            log_path=log_path,
        )

    async def hold_execution(_job_id: str, _request: JobRequest) -> None:
        await release_jobs.wait()

    controller = RecurrenceController(
        manager._schedule_registry,
        tracked_submit,
        adapter.schedule_cron_tick,
        adapter.cancel_cron_tick,
        manager._is_schedule_active,
        now=lambda: current,
    )
    manager._recurrence_controller = controller
    monkeypatch.setattr(manager._registry, "register_job", held_register_job)
    monkeypatch.setattr(manager, "_run_job_task", hold_execution)

    manual_task = asyncio.create_task(
        original_submit(JobRequest(config_path=score_path))
    )
    tick_task: asyncio.Task[None] | None = None
    submit_wait: asyncio.Task[bool] | None = None
    try:
        await asyncio.wait_for(metadata_window.wait(), timeout=1.0)
        _due_at, handle = next(iter(controller._timers.values()))
        assert isinstance(handle.event, CronTick)
        current = datetime.fromtimestamp(handle.event.due_at or 0.0, tz=UTC)
        tick_task = asyncio.create_task(controller.handle_tick(handle.event))
        submit_wait = asyncio.create_task(scheduled_submit_entered.wait())
        done, _pending = await asyncio.wait(
            {tick_task, submit_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )

        assert tick_task in done
        assert scheduled_submit_entered.is_set() is False
        assert manager._is_schedule_active("Reserved Report") is True
        record = await manager._schedule_registry.get("Reserved Report")
        assert record is not None
        assert record.last_outcome == "overlap_skipped"
    finally:
        release_metadata.set()
        release_jobs.set()
        if submit_wait is not None and not submit_wait.done():
            submit_wait.cancel()
            await asyncio.gather(submit_wait, return_exceptions=True)
        await manual_task
        if tick_task is not None:
            await tick_task
        await manager.shutdown(graceful=False)


async def test_active_unscheduled_duplicate_cannot_publish_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected scheduled request cannot create future autonomous work."""
    state_db = tmp_path / "conductor-state.db"
    active_path = tmp_path / "active" / "shared.yaml"
    scheduled_path = tmp_path / "scheduled" / "shared.yaml"
    active_path.parent.mkdir()
    scheduled_path.parent.mkdir()
    active_path.write_text(
        "name: Active Manual Job\n"
        f"workspace: {tmp_path / 'active-workspace'}\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: Stay active.\n",
        encoding="utf-8",
    )
    scheduled_path.write_text(
        "name: Rejected Schedule\n"
        f"workspace: {tmp_path / 'scheduled-workspace'}\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: Must not recur.\n"
        "schedule:\n"
        "  interval: 5m\n",
        encoding="utf-8",
    )
    manager = JobManager(
        DaemonConfig(
            max_concurrent_jobs=2,
            pid_file=tmp_path / "daemon.pid",
            state_db_path=state_db,
        )
    )
    await manager._registry.open()
    await manager._schedule_registry.open()
    adapter = BatonAdapter()
    controller = RecurrenceController(
        manager._schedule_registry,
        manager.submit_job,
        adapter.schedule_cron_tick,
        adapter.cancel_cron_tick,
        manager._is_schedule_active,
        now=lambda: datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
    )
    manager._recurrence_controller = controller
    active_started = asyncio.Event()
    release_active = asyncio.Event()

    async def hold_execution(_job_id: str, _request: JobRequest) -> None:
        active_started.set()
        await release_active.wait()

    monkeypatch.setattr(manager, "_run_job_task", hold_execution)

    try:
        accepted = await manager.submit_job(JobRequest(config_path=active_path))
        assert accepted.status == "accepted"
        await asyncio.wait_for(active_started.wait(), timeout=1.0)

        rejected = await manager.submit_job(JobRequest(config_path=scheduled_path))

        assert rejected.status == "rejected"
        assert await manager._schedule_registry.get("Rejected Schedule") is None
        assert "Rejected Schedule" not in controller._timers
    finally:
        release_active.set()
        await manager.shutdown(graceful=False)
        await adapter.shutdown()


async def test_concurrent_same_job_id_loser_cannot_replace_recurrence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first admitted request owns recurrence before JobMeta publication."""
    state_db = tmp_path / "conductor-state.db"
    first_path = tmp_path / "first" / "shared.yaml"
    second_path = tmp_path / "second" / "shared.yaml"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_text(
        "name: Shared Schedule\n"
        f"workspace: {tmp_path / 'first-workspace'}\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: First request.\n"
        "schedule:\n"
        "  interval: 5m\n",
        encoding="utf-8",
    )
    second_path.write_text(
        "name: Shared Schedule\n"
        f"workspace: {tmp_path / 'second-workspace'}\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: Losing request.\n"
        "schedule:\n"
        "  interval: 10m\n",
        encoding="utf-8",
    )
    manager = JobManager(
        DaemonConfig(
            max_concurrent_jobs=2,
            pid_file=tmp_path / "daemon.pid",
            state_db_path=state_db,
        )
    )
    await manager._registry.open()
    await manager._schedule_registry.open()
    adapter = BatonAdapter()
    controller = RecurrenceController(
        manager._schedule_registry,
        manager.submit_job,
        adapter.schedule_cron_tick,
        adapter.cancel_cron_tick,
        manager._is_schedule_active,
        now=lambda: datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
    )
    manager._recurrence_controller = controller
    first_published = asyncio.Event()
    release_first = asyncio.Event()
    release_jobs = asyncio.Event()
    original_register = controller.register

    async def hold_first_after_publication(
        score_path: Path,
        config: JobConfig | None = None,
    ) -> ScheduleRecord | None:
        record = await original_register(score_path, config)
        if score_path == first_path:
            first_published.set()
            await release_first.wait()
        return record

    async def hold_execution(_job_id: str, _request: JobRequest) -> None:
        await release_jobs.wait()

    monkeypatch.setattr(controller, "register", hold_first_after_publication)
    monkeypatch.setattr(manager, "_run_job_task", hold_execution)

    first_task = asyncio.create_task(
        manager.submit_job(JobRequest(config_path=first_path))
    )
    try:
        await asyncio.wait_for(first_published.wait(), timeout=1.0)
        before = await manager._schedule_registry.get("Shared Schedule")
        assert before is not None
        first_handle = controller._timers["Shared Schedule"][1]

        second = await asyncio.wait_for(
            manager.submit_job(JobRequest(config_path=second_path)),
            timeout=1.0,
        )
        after = await manager._schedule_registry.get("Shared Schedule")
        assert after is not None
        release_first.set()
        first = await first_task

        assert first.status == "accepted"
        assert second.status == "rejected"
        assert after.score_path == before.score_path == first_path.resolve()
        assert after.schedule_json == before.schedule_json
        assert after.source_digest == before.source_digest
        assert controller._timers["Shared Schedule"][1] is first_handle
    finally:
        release_first.set()
        release_jobs.set()
        if not first_task.done():
            await first_task
        await manager.shutdown(graceful=False)
        await adapter.shutdown()


async def test_scheduled_submit_admission_blocks_concurrent_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal job cannot resume through an admitted scheduled submit."""
    state_db = tmp_path / "conductor-state.db"
    score_path = tmp_path / "shared.yaml"
    workspace = tmp_path / "workspace"
    score_path.write_text(
        "name: Shared Resume Schedule\n"
        f"workspace: {workspace}\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: Submission owns activation.\n"
        "schedule:\n"
        "  interval: 5m\n",
        encoding="utf-8",
    )
    manager = JobManager(
        DaemonConfig(
            max_concurrent_jobs=2,
            pid_file=tmp_path / "daemon.pid",
            state_db_path=state_db,
        )
    )
    await manager._registry.open()
    await manager._schedule_registry.open()
    await manager._registry.register_job("shared", score_path, workspace)
    await manager._registry.update_status("shared", DaemonJobStatus.FAILED)
    manager._job_meta["shared"] = JobMeta(
        job_id="shared",
        config_path=score_path,
        workspace=workspace,
        status=DaemonJobStatus.FAILED,
    )
    adapter = BatonAdapter()
    controller = RecurrenceController(
        manager._schedule_registry,
        manager.submit_job,
        adapter.schedule_cron_tick,
        adapter.cancel_cron_tick,
        manager._is_schedule_active,
        now=lambda: datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
    )
    manager._recurrence_controller = controller
    recurrence_published = asyncio.Event()
    release_submission = asyncio.Event()
    release_jobs = asyncio.Event()
    original_register = controller.register

    async def hold_after_recurrence_publication(
        path: Path,
        config: JobConfig | None = None,
    ) -> ScheduleRecord | None:
        record = await original_register(path, config)
        recurrence_published.set()
        await release_submission.wait()
        return record

    async def hold_execution(*_args: object, **_kwargs: object) -> None:
        await release_jobs.wait()

    monkeypatch.setattr(controller, "register", hold_after_recurrence_publication)
    monkeypatch.setattr(manager, "_run_job_task", hold_execution)
    monkeypatch.setattr(manager, "_resume_job_task", hold_execution)

    submit_task = asyncio.create_task(
        manager.submit_job(JobRequest(config_path=score_path))
    )
    try:
        await asyncio.wait_for(recurrence_published.wait(), timeout=1.0)
        resume_error: JobSubmissionError | None = None
        try:
            await manager.resume_job("shared")
        except JobSubmissionError as exc:
            resume_error = exc
        status_while_submit_owned = manager._job_meta["shared"].status

        release_submission.set()
        submitted = await submit_task

        assert isinstance(resume_error, JobSubmissionError)
        assert "being submitted" in str(resume_error)
        assert status_while_submit_owned is DaemonJobStatus.FAILED
        assert submitted.status == "accepted"
        record = await manager._schedule_registry.get("Shared Resume Schedule")
        assert record is not None
        assert "Shared Resume Schedule" in controller._timers
    finally:
        release_submission.set()
        release_jobs.set()
        if not submit_task.done():
            await submit_task
        await manager.shutdown(graceful=False)
        await adapter.shutdown()


async def test_resume_admission_blocks_concurrent_scheduled_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scheduled submit cannot publish recurrence through an owned resume."""
    state_db = tmp_path / "conductor-state.db"
    score_path = tmp_path / "shared.yaml"
    workspace = tmp_path / "workspace"
    score_path.write_text(
        "name: Rejected During Resume\n"
        f"workspace: {workspace}\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: Resume owns activation.\n"
        "schedule:\n"
        "  interval: 5m\n",
        encoding="utf-8",
    )
    manager = JobManager(
        DaemonConfig(
            max_concurrent_jobs=2,
            pid_file=tmp_path / "daemon.pid",
            state_db_path=state_db,
        )
    )
    await manager._registry.open()
    await manager._schedule_registry.open()
    await manager._registry.register_job("shared", score_path, workspace)
    await manager._registry.update_status("shared", DaemonJobStatus.FAILED)
    manager._job_meta["shared"] = JobMeta(
        job_id="shared",
        config_path=score_path,
        workspace=workspace,
        status=DaemonJobStatus.FAILED,
    )
    adapter = BatonAdapter()
    controller = RecurrenceController(
        manager._schedule_registry,
        manager.submit_job,
        adapter.schedule_cron_tick,
        adapter.cancel_cron_tick,
        manager._is_schedule_active,
        now=lambda: datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
    )
    manager._recurrence_controller = controller
    stale_started = asyncio.Event()
    stale_cancelled = asyncio.Event()
    release_stale = asyncio.Event()
    release_jobs = asyncio.Event()

    async def stale_task() -> None:
        stale_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stale_cancelled.set()
            await release_stale.wait()
            raise

    async def hold_execution(*_args: object, **_kwargs: object) -> None:
        await release_jobs.wait()

    monkeypatch.setattr(manager, "_run_job_task", hold_execution)
    monkeypatch.setattr(manager, "_resume_job_task", hold_execution)
    old_task = asyncio.create_task(stale_task())
    manager._jobs["shared"] = old_task
    await asyncio.wait_for(stale_started.wait(), timeout=1.0)

    resume_task = asyncio.create_task(manager.resume_job("shared"))
    submitted_execution: asyncio.Task[object] | None = None
    try:
        await asyncio.wait_for(stale_cancelled.wait(), timeout=1.0)

        submitted = await manager.submit_job(JobRequest(config_path=score_path))
        if submitted.status == "accepted":
            submitted_execution = manager._jobs.get("shared")
        record_while_resume_owned = await manager._schedule_registry.get(
            "Rejected During Resume"
        )

        release_stale.set()
        resumed = await resume_task

        assert resumed.status == "accepted"
        assert submitted.status == "rejected"
        assert record_while_resume_owned is None
        assert "Rejected During Resume" not in controller._timers
    finally:
        release_stale.set()
        release_jobs.set()
        if not resume_task.done():
            await resume_task
        await asyncio.gather(old_task, return_exceptions=True)
        if submitted_execution is not None:
            await asyncio.gather(submitted_execution, return_exceptions=True)
        await manager.shutdown(graceful=False)
        await adapter.shutdown()


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
