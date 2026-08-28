"""Adversarial recurring lifecycle tests at the real manager/controller boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.config import JobConfig
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import CronTick
from marianne.daemon.config import DaemonConfig
from marianne.daemon.exceptions import JobSubmissionError
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.recurrence import RecurrenceController
from marianne.daemon.types import JobRequest, JobResponse


class RealLifecycle:
    def __init__(
        self,
        manager: JobManager,
        controller: RecurrenceController,
        adapter: BatonAdapter,
        score_path: Path,
        workspace: Path,
    ) -> None:
        self.manager = manager
        self.controller = controller
        self.adapter = adapter
        self.score_path = score_path
        self.workspace = workspace
        self.schedule_id = "Anchor Schedule"

    def add_meta(self, job_id: str, status: DaemonJobStatus) -> JobMeta:
        meta = JobMeta(
            job_id=job_id,
            config_path=self.score_path,
            workspace=self.workspace,
            status=status,
            schedule_id=self.schedule_id,
        )
        self.manager._job_meta[job_id] = meta
        return meta


@pytest.fixture
async def real_lifecycle(tmp_path: Path) -> AsyncIterator[RealLifecycle]:
    state_db = tmp_path / "state.db"
    score_path = tmp_path / "anchor-score.yaml"
    workspace = tmp_path / "workspace"
    score_path.write_text(
        "name: Anchor Schedule\n"
        f"workspace: {workspace}\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: test\n"
        "schedule:\n  interval: 5m\n",
        encoding="utf-8",
    )
    manager = JobManager(
        DaemonConfig(
            max_concurrent_jobs=4,
            state_db_path=state_db,
            pid_file=tmp_path / "daemon.pid",
        )
    )
    await manager._registry.open()
    await manager._schedule_registry.open()
    manager._service = MagicMock()
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
    config = JobConfig.from_yaml(score_path)
    await controller.register(score_path, config)
    lifecycle = RealLifecycle(manager, controller, adapter, score_path, workspace)
    yield lifecycle
    for task in list(manager._jobs.values()):
        task.cancel()
    if manager._jobs:
        await asyncio.gather(*manager._jobs.values(), return_exceptions=True)
    await manager.shutdown(graceful=False)
    await adapter.shutdown()


@pytest.mark.asyncio
async def test_anchor_pause_controls_running_scheduled_child(
    real_lifecycle: RealLifecycle,
) -> None:
    life = real_lifecycle
    child_id = "Anchor Schedule--scheduled--20260828T120000000000Z"
    life.add_meta(child_id, DaemonJobStatus.RUNNING)
    pause_event = asyncio.Event()
    life.manager._pause_events[child_id] = pause_event
    life.manager._jobs[child_id] = asyncio.create_task(asyncio.Event().wait())

    assert await life.manager.pause_job(life.schedule_id) is True

    record = await life.manager._schedule_registry.get(life.schedule_id)
    assert record is not None and record.enabled is False
    assert pause_event.is_set()


@pytest.mark.asyncio
async def test_anchor_pause_makes_pending_and_queued_children_nondispatchable(
    real_lifecycle: RealLifecycle,
) -> None:
    life = real_lifecycle
    pending_id = "Anchor Schedule--scheduled--pending"
    queued_id = "Anchor Schedule--scheduled--queued"
    life.add_meta(pending_id, DaemonJobStatus.PENDING)
    life.add_meta(queued_id, DaemonJobStatus.QUEUED)
    life.manager._pending_jobs[pending_id] = JobRequest(
        config_path=life.score_path,
        job_id=pending_id,
        schedule_id=life.schedule_id,
        scheduled_due_at=1.0,
    )
    queued_task = asyncio.create_task(asyncio.Event().wait())
    life.manager._jobs[queued_id] = queued_task

    assert await life.manager.pause_job(life.schedule_id) is True

    assert pending_id not in life.manager._pending_jobs
    assert queued_task.cancelled()
    assert life.manager._job_meta[pending_id].status is DaemonJobStatus.PAUSED
    assert life.manager._job_meta[queued_id].status is DaemonJobStatus.PAUSED


@pytest.mark.asyncio
async def test_anchor_cancel_removes_recurrence_and_every_active_child(
    real_lifecycle: RealLifecycle,
) -> None:
    life = real_lifecycle
    child_ids = [
        "Anchor Schedule--scheduled--pending",
        "Anchor Schedule--scheduled--queued",
        "Anchor Schedule--scheduled--running",
        "Anchor Schedule--scheduled--paused",
    ]
    statuses = [
        DaemonJobStatus.PENDING,
        DaemonJobStatus.QUEUED,
        DaemonJobStatus.RUNNING,
        DaemonJobStatus.PAUSED,
    ]
    for child_id, status in zip(child_ids, statuses, strict=True):
        life.add_meta(child_id, status)
    life.manager._pending_jobs[child_ids[0]] = JobRequest(
        config_path=life.score_path,
        job_id=child_ids[0],
        schedule_id=life.schedule_id,
        scheduled_due_at=1.0,
    )
    queued_task = asyncio.create_task(asyncio.Event().wait())
    running_task = asyncio.create_task(asyncio.Event().wait())
    life.manager._jobs[child_ids[1]] = queued_task
    life.manager._jobs[child_ids[2]] = running_task

    assert await life.manager.cancel_job(life.schedule_id) is True

    assert await life.manager._schedule_registry.get(life.schedule_id) is None
    assert life.schedule_id not in life.controller._timers
    assert all(
        life.manager._job_meta[child_id].status is DaemonJobStatus.CANCELLED
        for child_id in child_ids
    )


@pytest.mark.asyncio
async def test_cancel_sweeps_later_children_after_one_status_update_fails(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    first_id = "Anchor Schedule--scheduled--first"
    second_id = "Anchor Schedule--scheduled--second"
    life.add_meta(first_id, DaemonJobStatus.RUNNING)
    life.add_meta(second_id, DaemonJobStatus.RUNNING)
    first_task = asyncio.create_task(asyncio.Event().wait())
    second_task = asyncio.create_task(asyncio.Event().wait())
    life.manager._jobs[first_id] = first_task
    life.manager._jobs[second_id] = second_task
    original_set_status = life.manager._set_job_status

    async def fail_first(job_id: str, status: DaemonJobStatus) -> None:
        if job_id == first_id:
            raise RuntimeError("first status failed")
        await original_set_status(job_id, status)

    monkeypatch.setattr(life.manager, "_set_job_status", fail_first)

    with pytest.raises(RuntimeError, match="first status failed"):
        await life.manager.cancel_job(life.schedule_id)

    assert first_task.cancelled()
    assert second_task.cancelled()
    assert life.manager._job_meta[second_id].status is DaemonJobStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_caller_cancellation_finishes_every_child_before_propagating(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    child_ids = [
        "Anchor Schedule--scheduled--first",
        "Anchor Schedule--scheduled--second",
    ]
    for child_id in child_ids:
        life.add_meta(child_id, DaemonJobStatus.RUNNING)
        life.manager._jobs[child_id] = asyncio.create_task(asyncio.Event().wait())
    status_entered = asyncio.Event()
    release_status = asyncio.Event()
    original_set_status = life.manager._set_job_status

    async def held_set_status(job_id: str, status: DaemonJobStatus) -> None:
        if job_id == child_ids[0]:
            status_entered.set()
            await release_status.wait()
        await original_set_status(job_id, status)

    monkeypatch.setattr(life.manager, "_set_job_status", held_set_status)
    cancel = asyncio.create_task(life.manager.cancel_job(life.schedule_id))
    await asyncio.wait_for(status_entered.wait(), timeout=1.0)
    cancel.cancel()
    release_status.set()

    with pytest.raises(asyncio.CancelledError):
        await cancel

    assert await life.manager._schedule_registry.get(life.schedule_id) is None
    assert all(
        life.manager._job_meta[child_id].status is DaemonJobStatus.CANCELLED
        for child_id in child_ids
    )


@pytest.mark.asyncio
async def test_cancel_remove_failure_leaves_real_recurrence_paused_and_loud(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle

    async def fail_remove(_schedule_id: str) -> None:
        raise RuntimeError("remove failed")

    monkeypatch.setattr(life.manager._schedule_registry, "remove", fail_remove)

    with pytest.raises(RuntimeError, match="remove failed"):
        await life.manager.cancel_job(life.schedule_id)

    record = await life.manager._schedule_registry.get(life.schedule_id)
    assert record is not None and record.enabled is False
    assert life.schedule_id not in life.controller._timers


@pytest.mark.asyncio
async def test_pause_failure_restores_exact_prior_disabled_state(
    real_lifecycle: RealLifecycle,
) -> None:
    life = real_lifecycle
    await life.controller.pause(life.schedule_id)
    life.add_meta(life.schedule_id, DaemonJobStatus.RUNNING)
    life.manager._jobs[life.schedule_id] = asyncio.create_task(asyncio.Event().wait())
    life.manager._service.pause_job = AsyncMock(side_effect=RuntimeError("pause failed"))

    with pytest.raises(RuntimeError, match="pause failed"):
        await life.manager.pause_job(life.schedule_id)

    record = await life.manager._schedule_registry.get(life.schedule_id)
    assert record is not None and record.enabled is False
    assert life.schedule_id not in life.controller._timers


@pytest.mark.asyncio
async def test_resume_failure_preserves_exact_prior_enabled_state(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    life.add_meta(life.schedule_id, DaemonJobStatus.PAUSED)

    async def fail_status(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("resume failed")

    monkeypatch.setattr(life.manager, "_set_job_status", fail_status)

    with pytest.raises(RuntimeError, match="resume failed"):
        await life.manager.resume_job(life.schedule_id)

    record = await life.manager._schedule_registry.get(life.schedule_id)
    assert record is not None and record.enabled is True
    assert life.schedule_id in life.controller._timers


@pytest.mark.asyncio
async def test_anchor_resume_skips_historical_failed_child_but_resumes_paused_child(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    paused_id = "Anchor Schedule--scheduled--paused"
    failed_id = "Anchor Schedule--scheduled--failed-history"
    life.add_meta(paused_id, DaemonJobStatus.PAUSED)
    life.add_meta(failed_id, DaemonJobStatus.FAILED)
    resumed: list[str] = []

    async def record_resume(
        _manager: JobManager,
        job_id: str,
        **_kwargs: object,
    ) -> JobResponse:
        resumed.append(job_id)
        return JobResponse(job_id=job_id, status="accepted")

    monkeypatch.setattr(JobManager, "_resume_active_job", record_resume)

    response = await life.manager.resume_job(life.schedule_id)

    assert response.status == "accepted"
    assert resumed == [paused_id]


@pytest.mark.asyncio
async def test_schedule_only_status_and_list_survive_anchor_clear(
    real_lifecycle: RealLifecycle,
) -> None:
    life = real_lifecycle
    await life.manager._registry.register_job(
        "anchor-score",
        life.score_path,
        life.workspace,
    )
    await life.manager._registry.update_status(
        "anchor-score",
        DaemonJobStatus.COMPLETED,
    )
    await life.manager.clear_jobs(job_ids=["anchor-score"])

    status = await life.manager.get_job_status("anchor-score")
    jobs = await life.manager.list_jobs()

    assert status["job_id"] == life.schedule_id
    assert status["status"] == "scheduled"
    assert set(status["schedule"]) == {
        "enabled",
        "next_due_at",
        "last_due_at",
        "last_run_id",
        "last_outcome",
        "consecutive_drops",
    }
    anchor = next(job for job in jobs if job["job_id"] == life.schedule_id)
    assert anchor["status"] == "scheduled"


@pytest.mark.asyncio
async def test_submit_that_owns_publication_makes_concurrent_pause_reject(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    published = asyncio.Event()
    release_publish = asyncio.Event()
    release_job = asyncio.Event()
    original_register_job = life.manager._registry.register_job

    async def held_register_job(*args: object, **kwargs: object) -> None:
        published.set()
        await release_publish.wait()
        await original_register_job(*args, **kwargs)  # type: ignore[arg-type]

    async def hold_execution(*_args: object, **_kwargs: object) -> None:
        await release_job.wait()

    monkeypatch.setattr(life.manager._registry, "register_job", held_register_job)
    monkeypatch.setattr(life.manager, "_run_job_task", hold_execution)
    submit = asyncio.create_task(
        life.manager.submit_job(JobRequest(config_path=life.score_path, fresh=True))
    )
    try:
        await asyncio.wait_for(published.wait(), timeout=1.0)
        with pytest.raises(JobSubmissionError, match="lifecycle|submitted|active"):
            await life.manager.pause_job(life.schedule_id)
        record = await life.manager._schedule_registry.get(life.schedule_id)
        assert record is not None and record.enabled is True
    finally:
        release_publish.set()
        release_job.set()
        await submit


@pytest.mark.asyncio
async def test_pause_that_owns_lifecycle_rejects_concurrent_submission(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    pause_entered = asyncio.Event()
    release_pause = asyncio.Event()
    original_pause = life.manager._schedule_registry.pause

    async def held_pause(schedule_id: str) -> None:
        pause_entered.set()
        await release_pause.wait()
        await original_pause(schedule_id)

    monkeypatch.setattr(life.manager._schedule_registry, "pause", held_pause)
    pause_task = asyncio.create_task(life.manager.pause_job(life.schedule_id))
    try:
        await asyncio.wait_for(pause_entered.wait(), timeout=1.0)
        submitted = await asyncio.wait_for(
            life.manager.submit_job(JobRequest(config_path=life.score_path, fresh=True)),
            timeout=1.0,
        )
        assert submitted.status == "rejected"
    finally:
        release_pause.set()
        assert await pause_task is True


@pytest.mark.asyncio
async def test_tick_that_enters_first_is_cancelled_before_anchor_cancel_returns(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    record = await life.manager._schedule_registry.get(life.schedule_id)
    assert record is not None
    tick_entered_submit = asyncio.Event()
    release_submit = asyncio.Event()
    release_job = asyncio.Event()
    original_submit = life.manager.submit_job

    async def held_submit(request: JobRequest):  # type: ignore[no-untyped-def]
        tick_entered_submit.set()
        await release_submit.wait()
        return await original_submit(request)

    async def hold_execution(*_args: object, **_kwargs: object) -> None:
        await release_job.wait()

    monkeypatch.setattr(life.controller, "_submit", held_submit)
    monkeypatch.setattr(life.manager, "_run_job_task", hold_execution)
    due = record.next_due_at
    monkeypatch.setattr(
        life.controller,
        "_now",
        lambda: datetime.fromtimestamp(due, tz=UTC),
    )
    tick = asyncio.create_task(
        life.controller.handle_tick(
            CronTick(
                entry_name=life.schedule_id,
                score_path=str(life.score_path),
                due_at=due,
                timestamp=due,
            )
        )
    )
    await asyncio.wait_for(tick_entered_submit.wait(), timeout=1.0)
    cancel = asyncio.create_task(life.manager.cancel_job(life.schedule_id))
    release_submit.set()

    try:
        await tick
        assert await cancel is True
        child_id = life.controller._child_job_id(life.schedule_id, due)
        assert life.manager._job_meta[child_id].status is DaemonJobStatus.CANCELLED
        assert child_id not in life.manager._jobs
        assert await life.manager._schedule_registry.get(life.schedule_id) is None
    finally:
        release_job.set()


@pytest.mark.asyncio
async def test_submit_that_owns_publication_makes_concurrent_cancel_reject(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    published = asyncio.Event()
    release_publish = asyncio.Event()
    release_job = asyncio.Event()
    original_register_job = life.manager._registry.register_job

    async def held_register_job(*args: object, **kwargs: object) -> None:
        published.set()
        await release_publish.wait()
        await original_register_job(*args, **kwargs)  # type: ignore[arg-type]

    async def hold_execution(*_args: object, **_kwargs: object) -> None:
        await release_job.wait()

    monkeypatch.setattr(life.manager._registry, "register_job", held_register_job)
    monkeypatch.setattr(life.manager, "_run_job_task", hold_execution)
    submit = asyncio.create_task(
        life.manager.submit_job(JobRequest(config_path=life.score_path, fresh=True))
    )
    try:
        await asyncio.wait_for(published.wait(), timeout=1.0)
        with pytest.raises(JobSubmissionError, match="lifecycle|active"):
            await life.manager.cancel_job(life.schedule_id)
        record = await life.manager._schedule_registry.get(life.schedule_id)
        assert record is not None
    finally:
        release_publish.set()
        release_job.set()
        await submit


@pytest.mark.asyncio
async def test_cancel_that_owns_lifecycle_rejects_concurrent_submission(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    remove_entered = asyncio.Event()
    release_remove = asyncio.Event()
    original_remove = life.manager._schedule_registry.remove

    async def held_remove(schedule_id: str) -> None:
        remove_entered.set()
        await release_remove.wait()
        await original_remove(schedule_id)

    monkeypatch.setattr(life.manager._schedule_registry, "remove", held_remove)
    cancel = asyncio.create_task(life.manager.cancel_job(life.schedule_id))
    await asyncio.wait_for(remove_entered.wait(), timeout=1.0)
    submitted = await life.manager.submit_job(
        JobRequest(config_path=life.score_path, fresh=True)
    )
    assert submitted.status == "rejected"
    release_remove.set()
    assert await cancel is True


@pytest.mark.asyncio
async def test_probe_race_is_closed_by_inside_lock_admission(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    register_after_probe = asyncio.Event()
    release_register = asyncio.Event()
    pause_action_entered = asyncio.Event()
    release_pause_action = asyncio.Event()
    original_lock = life.controller._lock_schedules
    original_pause_related = life.manager._pause_related_job

    @asynccontextmanager
    async def held_lock(*schedule_ids: str):  # type: ignore[no-untyped-def]
        current = asyncio.current_task()
        if current is not None and current.get_name() == "raced-submit":
            register_after_probe.set()
            await release_register.wait()
        async with original_lock(*schedule_ids):
            yield

    async def held_pause_related(meta: JobMeta) -> None:
        pause_action_entered.set()
        await release_pause_action.wait()
        await original_pause_related(meta)

    child_id = "Anchor Schedule--scheduled--running"
    life.add_meta(child_id, DaemonJobStatus.RUNNING)
    life.manager._jobs[child_id] = asyncio.create_task(asyncio.Event().wait())
    life.manager._pause_events[child_id] = asyncio.Event()
    monkeypatch.setattr(life.controller, "_lock_schedules", held_lock)
    monkeypatch.setattr(life.manager, "_pause_related_job", held_pause_related)

    submit = asyncio.create_task(
        life.manager.submit_job(JobRequest(config_path=life.score_path, fresh=True)),
        name="raced-submit",
    )
    await asyncio.wait_for(register_after_probe.wait(), timeout=1.0)
    assert life.manager._schedule_admission_reservations == {}
    pause = asyncio.create_task(life.manager.pause_job(life.schedule_id))
    await asyncio.wait_for(pause_action_entered.wait(), timeout=1.0)
    release_register.set()
    submitted = await submit
    assert submitted.status == "rejected"
    reservation = life.manager._schedule_admission_reservations[life.schedule_id]
    assert reservation.owner is pause
    assert reservation.depth == 1
    release_pause_action.set()
    assert await pause is True


@pytest.mark.asyncio
async def test_stale_identity_replacement_reserves_old_and_new_schedule_ids(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    life.score_path.write_text(
        "name: Replacement Schedule\n"
        f"workspace: {life.workspace}\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: test\n"
        "schedule:\n  interval: 5m\n",
        encoding="utf-8",
    )
    publication_entered = asyncio.Event()
    release_publication = asyncio.Event()
    release_job = asyncio.Event()
    original_register_job = life.manager._registry.register_job

    async def held_register_job(*args: object, **kwargs: object) -> None:
        publication_entered.set()
        await release_publication.wait()
        await original_register_job(*args, **kwargs)  # type: ignore[arg-type]

    async def hold_execution(*_args: object, **_kwargs: object) -> None:
        await release_job.wait()

    monkeypatch.setattr(life.manager._registry, "register_job", held_register_job)
    monkeypatch.setattr(life.manager, "_run_job_task", hold_execution)
    submit = asyncio.create_task(
        life.manager.submit_job(JobRequest(config_path=life.score_path, fresh=True))
    )
    await asyncio.wait_for(publication_entered.wait(), timeout=1.0)
    assert set(life.manager._schedule_admission_reservations) == {
        life.schedule_id,
        "Replacement Schedule",
    }
    with pytest.raises(JobSubmissionError, match="lifecycle|active"):
        await life.manager.pause_job(life.score_path.stem)
    release_publication.set()
    release_job.set()
    response = await submit
    assert response.status == "accepted"
    assert await life.manager._schedule_registry.get(life.schedule_id) is None
    assert (
        await life.manager._schedule_registry.get("Replacement Schedule") is not None
    )
