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
from marianne.daemon.baton.state import SheetExecutionState
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

    def replace_schedule_name(self, schedule_id: str) -> None:
        self.score_path.write_text(
            f"name: {schedule_id}\n"
            f"workspace: {self.workspace}\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: test\n"
            "schedule:\n  interval: 5m\n",
            encoding="utf-8",
        )

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
async def test_pause_revalidates_completed_identity_replacement_at_mutation(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    lookup_complete = asyncio.Event()
    release_lifecycle = asyncio.Event()
    original_lookup = JobManager._schedule_for_job

    async def held_lookup(
        manager: JobManager,
        job_id: str,
        **kwargs: object,
    ):
        record = await original_lookup(manager, job_id, **kwargs)  # type: ignore[arg-type]
        if asyncio.current_task() is pause:
            lookup_complete.set()
            await release_lifecycle.wait()
        return record

    monkeypatch.setattr(JobManager, "_schedule_for_job", held_lookup)
    pause = asyncio.create_task(life.manager.pause_job(life.score_path.stem))
    await asyncio.wait_for(lookup_complete.wait(), timeout=1.0)
    life.replace_schedule_name("Replacement Schedule")
    replacement = await life.controller.register(life.score_path)
    assert replacement is not None
    assert await life.manager._schedule_registry.get(life.schedule_id) is None
    release_lifecycle.set()

    assert await pause is True
    current = await life.manager._schedule_registry.get("Replacement Schedule")
    assert current is not None and current.enabled is False


@pytest.mark.asyncio
async def test_cancel_revalidates_completed_identity_replacement_at_mutation(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    lookup_complete = asyncio.Event()
    release_lifecycle = asyncio.Event()
    original_lookup = JobManager._schedule_for_job

    async def held_lookup(
        manager: JobManager,
        job_id: str,
        **kwargs: object,
    ):
        record = await original_lookup(manager, job_id, **kwargs)  # type: ignore[arg-type]
        if asyncio.current_task() is cancel:
            lookup_complete.set()
            await release_lifecycle.wait()
        return record

    monkeypatch.setattr(JobManager, "_schedule_for_job", held_lookup)
    cancel = asyncio.create_task(life.manager.cancel_job(life.score_path.stem))
    await asyncio.wait_for(lookup_complete.wait(), timeout=1.0)
    life.replace_schedule_name("Replacement Schedule")
    replacement = await life.controller.register(life.score_path)
    assert replacement is not None
    assert await life.manager._schedule_registry.get(life.schedule_id) is None
    release_lifecycle.set()

    assert await cancel is True
    assert await life.manager._schedule_registry.get("Replacement Schedule") is None


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
@pytest.mark.parametrize("operation", ["pause", "cancel"])
async def test_pending_activation_owner_rejects_concurrent_lifecycle(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """A starter owns job and schedule publication before removing PENDING work."""
    life = real_lifecycle
    child_id = f"Anchor Schedule--scheduled--starter-first-{operation}"
    life.add_meta(child_id, DaemonJobStatus.PENDING)
    life.manager._pending_jobs[child_id] = JobRequest(
        config_path=life.score_path,
        job_id=child_id,
        schedule_id=life.schedule_id,
        scheduled_due_at=1.0,
    )
    life.manager._backpressure.should_accept_job = MagicMock(return_value=True)
    status_entered = asyncio.Event()
    release_status = asyncio.Event()
    original_set_status = life.manager._set_job_status

    async def held_set_status(job_id: str, status: DaemonJobStatus, **kwargs: object) -> None:
        if asyncio.current_task() is starter and job_id == child_id:
            status_entered.set()
            await release_status.wait()
        await original_set_status(job_id, status, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(life.manager, "_set_job_status", held_set_status)
    starter = asyncio.create_task(life.manager._start_pending_jobs())
    await asyncio.wait_for(status_entered.wait(), timeout=1.0)

    try:
        lifecycle = (
            life.manager.pause_job(life.schedule_id)
            if operation == "pause"
            else life.manager.cancel_job(life.schedule_id)
        )
        with pytest.raises(JobSubmissionError, match="already active"):
            await asyncio.wait_for(lifecycle, timeout=1.0)
    finally:
        release_status.set()
        await starter


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pause", "cancel"])
async def test_lifecycle_owner_keeps_pending_activation_nondispatchable(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """A held lifecycle mutation prevents pending pop, status, and task publication."""
    life = real_lifecycle
    child_id = f"Anchor Schedule--scheduled--lifecycle-first-{operation}"
    request = JobRequest(
        config_path=life.score_path,
        job_id=child_id,
        schedule_id=life.schedule_id,
        scheduled_due_at=1.0,
    )
    life.add_meta(child_id, DaemonJobStatus.PENDING)
    life.manager._pending_jobs[child_id] = request
    life.manager._backpressure.should_accept_job = MagicMock(return_value=True)
    mutation_entered = asyncio.Event()
    release_mutation = asyncio.Event()
    registry_operation = "pause" if operation == "pause" else "remove"
    registry_mutation = getattr(life.manager._schedule_registry, registry_operation)

    async def held_mutation(schedule_id: str) -> None:
        mutation_entered.set()
        await release_mutation.wait()
        await registry_mutation(schedule_id)

    monkeypatch.setattr(
        life.manager._schedule_registry,
        registry_operation,
        held_mutation,
    )
    lifecycle = asyncio.create_task(
        life.manager.pause_job(life.schedule_id)
        if operation == "pause"
        else life.manager.cancel_job(life.schedule_id)
    )
    await asyncio.wait_for(mutation_entered.wait(), timeout=1.0)

    try:
        await life.manager._start_pending_jobs()

        assert life.manager._pending_jobs[child_id] is request
        assert life.manager._job_meta[child_id].status is DaemonJobStatus.PENDING
        assert child_id not in life.manager._jobs
    finally:
        release_mutation.set()
        await lifecycle


@pytest.mark.asyncio
async def test_ordinary_pending_starter_owner_rejects_concurrent_cancel(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-recurring cancel cannot remove work while its starter owns publication."""
    life = real_lifecycle
    job_id = "ordinary-pending-starter-first"
    config_path = life.score_path.parent / "ordinary.yaml"
    request = JobRequest(config_path=config_path, job_id=job_id)
    meta = JobMeta(
        job_id=job_id,
        config_path=config_path,
        workspace=life.workspace,
        status=DaemonJobStatus.PENDING,
    )
    life.manager._job_meta[job_id] = meta
    life.manager._pending_jobs[job_id] = request
    life.manager._backpressure.should_accept_job = MagicMock(return_value=True)
    status_entered = asyncio.Event()
    release_status = asyncio.Event()
    release_execution = asyncio.Event()
    original_set_status = life.manager._set_job_status

    async def held_set_status(
        current_job_id: str,
        status: DaemonJobStatus,
        **kwargs: object,
    ) -> None:
        if asyncio.current_task() is starter and current_job_id == job_id:
            status_entered.set()
            await release_status.wait()
        await original_set_status(current_job_id, status, **kwargs)  # type: ignore[arg-type]

    async def hold_execution(*_args: object, **_kwargs: object) -> None:
        await release_execution.wait()

    monkeypatch.setattr(life.manager, "_set_job_status", held_set_status)
    monkeypatch.setattr(life.manager, "_run_job_task", hold_execution)
    starter = asyncio.create_task(life.manager._start_pending_jobs())
    await asyncio.wait_for(status_entered.wait(), timeout=1.0)

    try:
        with pytest.raises(JobSubmissionError, match="already being submitted"):
            await life.manager.cancel_job(job_id)
    finally:
        release_status.set()
        await starter

    assert meta.status is DaemonJobStatus.QUEUED
    assert job_id in life.manager._jobs
    assert life.manager._job_admission_reservations == {}
    release_execution.set()


@pytest.mark.asyncio
async def test_ordinary_cancel_owner_blocks_pending_starter_publication(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel-first ownership leaves pending work untouched until cancel commits."""
    life = real_lifecycle
    job_id = "ordinary-pending-cancel-first"
    config_path = life.score_path.parent / "ordinary.yaml"
    request = JobRequest(config_path=config_path, job_id=job_id)
    meta = JobMeta(
        job_id=job_id,
        config_path=config_path,
        workspace=life.workspace,
        status=DaemonJobStatus.PENDING,
    )
    life.manager._job_meta[job_id] = meta
    life.manager._pending_jobs[job_id] = request
    life.manager._backpressure.should_accept_job = MagicMock(return_value=True)
    cancel_entered = asyncio.Event()
    release_cancel = asyncio.Event()
    original_cancel = life.manager._cancel_active_job

    async def held_cancel(current_job_id: str, *, source: str = "unknown") -> bool:
        cancel_entered.set()
        await release_cancel.wait()
        return await original_cancel(current_job_id, source=source)

    monkeypatch.setattr(life.manager, "_cancel_active_job", held_cancel)
    cancel = asyncio.create_task(life.manager.cancel_job(job_id))
    await asyncio.wait_for(cancel_entered.wait(), timeout=1.0)

    try:
        await life.manager._start_pending_jobs()
        assert life.manager._pending_jobs[job_id] is request
        assert meta.status is DaemonJobStatus.PENDING
        assert job_id not in life.manager._jobs
    finally:
        release_cancel.set()
        assert await cancel is True

    assert meta.status is DaemonJobStatus.CANCELLED
    assert life.manager._job_admission_reservations == {}


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
async def test_cancel_owns_swept_child_admission_until_command_returns(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact child cannot resume after its recurrence row has been removed."""
    life = real_lifecycle
    child_id = "Anchor Schedule--scheduled--swept-before-cancel-return"
    child = life.add_meta(child_id, DaemonJobStatus.PAUSED)
    sweep_complete = asyncio.Event()
    release_cancel = asyncio.Event()
    original_sweep = life.manager._cancel_schedule_related

    async def held_sweep(record: object, *, source: str) -> None:
        await original_sweep(record, source=source)  # type: ignore[arg-type]
        sweep_complete.set()
        await release_cancel.wait()

    monkeypatch.setattr(life.manager, "_cancel_schedule_related", held_sweep)
    cancel = asyncio.create_task(life.manager.cancel_job(life.schedule_id))
    await asyncio.wait_for(sweep_complete.wait(), timeout=1.0)
    assert await life.manager._schedule_registry.get(life.schedule_id) is None
    assert child.status is DaemonJobStatus.CANCELLED

    try:
        with pytest.raises(JobSubmissionError, match="already being submitted"):
            await life.manager.resume_job(child_id)
    finally:
        release_cancel.set()
        assert await cancel is True

    assert child.status is DaemonJobStatus.CANCELLED
    assert child_id not in life.manager._jobs
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}


@pytest.mark.asyncio
async def test_exact_child_resume_owner_rejects_concurrent_anchor_cancel(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reverse arrival order rejects cancel before recurrence removal."""
    life = real_lifecycle
    child_id = "Anchor Schedule--scheduled--resume-first"
    life.add_meta(child_id, DaemonJobStatus.PAUSED)
    resume_entered = asyncio.Event()
    release_resume = asyncio.Event()

    async def held_resume(
        _manager: JobManager,
        job_id: str,
        **_kwargs: object,
    ) -> JobResponse:
        resume_entered.set()
        await release_resume.wait()
        return JobResponse(job_id=job_id, status="accepted")

    monkeypatch.setattr(JobManager, "_resume_active_job", held_resume)
    resume = asyncio.create_task(life.manager.resume_job(child_id))
    await asyncio.wait_for(resume_entered.wait(), timeout=1.0)

    try:
        with pytest.raises(JobSubmissionError, match="already active"):
            await life.manager.cancel_job(life.schedule_id)
    finally:
        release_resume.set()
        assert (await resume).status == "accepted"

    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is True
    assert life.schedule_id in life.controller._timers
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}


@pytest.mark.asyncio
async def test_cancel_child_admission_rejection_releases_its_schedule_claim(
    real_lifecycle: RealLifecycle,
) -> None:
    """A pre-mutation child conflict cannot leak cancel's schedule ownership."""
    life = real_lifecycle
    child_id = "Anchor Schedule--scheduled--owned-child"
    life.add_meta(child_id, DaemonJobStatus.PAUSED)
    owner_ready = asyncio.Event()
    release_owner = asyncio.Event()

    async def own_child() -> None:
        assert life.manager._try_reserve_job_admission(child_id, allow_active=True)
        owner_ready.set()
        await release_owner.wait()
        life.manager._release_job_admission(child_id)

    owner = asyncio.create_task(own_child())
    await asyncio.wait_for(owner_ready.wait(), timeout=1.0)

    with pytest.raises(JobSubmissionError, match="already being submitted"):
        await life.manager.cancel_job(life.schedule_id)

    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is True
    assert life.schedule_id in life.controller._timers
    reservation = life.manager._job_admission_reservations[child_id]
    assert reservation.owner is owner and reservation.depth == 1
    assert life.manager._schedule_admission_reservations == {}
    release_owner.set()
    await owner
    assert life.manager._job_admission_reservations == {}


@pytest.mark.asyncio
async def test_pause_child_admission_rejection_does_not_rearm_recurrence(
    real_lifecycle: RealLifecycle,
) -> None:
    """Pre-mutation pause rejection preserves the exact durable timer identity."""
    life = real_lifecycle
    child_id = "Anchor Schedule--scheduled--pause-owned-child"
    life.add_meta(child_id, DaemonJobStatus.PAUSED)
    owner_ready = asyncio.Event()
    release_owner = asyncio.Event()

    async def own_child() -> None:
        assert life.manager._try_reserve_job_admission(child_id, allow_active=True)
        owner_ready.set()
        await release_owner.wait()
        life.manager._release_job_admission(child_id)

    owner = asyncio.create_task(own_child())
    await asyncio.wait_for(owner_ready.wait(), timeout=1.0)
    before = await life.manager._schedule_registry.get(life.schedule_id)
    assert before is not None
    before_due, before_handle = life.controller._timers[life.schedule_id]

    try:
        with pytest.raises(JobSubmissionError, match="already being submitted"):
            await life.manager.pause_job(life.schedule_id)

        after = await life.manager._schedule_registry.get(life.schedule_id)
        assert after is not None and after.enabled is True
        assert after.updated_at == before.updated_at
        assert after.next_due_at == before.next_due_at
        after_due, after_handle = life.controller._timers[life.schedule_id]
        assert after_due == before_due
        assert after_handle is before_handle
        reservation = life.manager._job_admission_reservations[child_id]
        assert reservation.owner is owner and reservation.depth == 1
        assert life.manager._schedule_admission_reservations == {}
    finally:
        release_owner.set()
        await owner

    assert life.manager._job_admission_reservations == {}


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
async def test_pause_partial_failure_restores_pending_request_and_status(
    real_lifecycle: RealLifecycle,
) -> None:
    """A later RUNNING failure cannot consume earlier pending work."""
    life = real_lifecycle
    pending_id = "Anchor Schedule--scheduled--pending-before-failure"
    running_id = "Anchor Schedule--scheduled--running-failure"
    pending = life.add_meta(pending_id, DaemonJobStatus.PENDING)
    life.add_meta(running_id, DaemonJobStatus.RUNNING)
    request = JobRequest(
        config_path=life.score_path,
        job_id=pending_id,
        schedule_id=life.schedule_id,
        scheduled_due_at=1.0,
    )
    life.manager._pending_jobs[pending_id] = request
    life.manager._jobs[running_id] = asyncio.create_task(asyncio.Event().wait())
    life.manager._service.pause_job = AsyncMock(side_effect=RuntimeError("pause failed"))

    with pytest.raises(RuntimeError, match="pause failed"):
        await life.manager.pause_job(life.schedule_id)

    assert life.manager._pending_jobs[pending_id] is request
    assert pending.status is DaemonJobStatus.PENDING
    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is True
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}


@pytest.mark.asyncio
async def test_second_pending_pause_failure_restores_first_pending_child(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later PENDING status failure restores every earlier staged child."""
    life = real_lifecycle
    first_id = "Anchor Schedule--scheduled--pending-first"
    second_id = "Anchor Schedule--scheduled--pending-second"
    first = life.add_meta(first_id, DaemonJobStatus.PENDING)
    second = life.add_meta(second_id, DaemonJobStatus.PENDING)
    requests = {
        job_id: JobRequest(
            config_path=life.score_path,
            job_id=job_id,
            schedule_id=life.schedule_id,
            scheduled_due_at=float(index),
        )
        for index, job_id in enumerate((first_id, second_id), start=1)
    }
    life.manager._pending_jobs.update(requests)
    original_set_status = life.manager._set_job_status

    async def fail_second(
        job_id: str,
        status: DaemonJobStatus,
        **kwargs: object,
    ) -> None:
        if job_id == second_id and status is DaemonJobStatus.PAUSED:
            raise RuntimeError("second pending pause failed")
        await original_set_status(job_id, status, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(life.manager, "_set_job_status", fail_second)

    with pytest.raises(RuntimeError, match="second pending pause failed"):
        await life.manager.pause_job(life.schedule_id)

    assert first.status is DaemonJobStatus.PENDING
    assert second.status is DaemonJobStatus.PENDING
    assert life.manager._pending_jobs[first_id] is requests[first_id]
    assert life.manager._pending_jobs[second_id] is requests[second_id]
    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is True
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}


@pytest.mark.asyncio
async def test_second_queued_pause_failure_keeps_every_queued_task(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued tasks are not cancelled until all fallible pause writes succeed."""
    life = real_lifecycle
    first_id = "Anchor Schedule--scheduled--queued-first"
    second_id = "Anchor Schedule--scheduled--queued-second"
    first = life.add_meta(first_id, DaemonJobStatus.QUEUED)
    second = life.add_meta(second_id, DaemonJobStatus.QUEUED)
    tasks = {
        first_id: asyncio.create_task(asyncio.Event().wait()),
        second_id: asyncio.create_task(asyncio.Event().wait()),
    }
    life.manager._jobs.update(tasks)
    original_set_status = life.manager._set_job_status

    async def fail_second(
        job_id: str,
        status: DaemonJobStatus,
        **kwargs: object,
    ) -> None:
        if job_id == second_id and status is DaemonJobStatus.PAUSED:
            raise RuntimeError("second queued pause failed")
        await original_set_status(job_id, status, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(life.manager, "_set_job_status", fail_second)

    with pytest.raises(RuntimeError, match="second queued pause failed"):
        await life.manager.pause_job(life.schedule_id)

    assert first.status is DaemonJobStatus.QUEUED
    assert second.status is DaemonJobStatus.QUEUED
    assert life.manager._jobs[first_id] is tasks[first_id]
    assert life.manager._jobs[second_id] is tasks[second_id]
    assert not tasks[first_id].done()
    assert not tasks[second_id].done()
    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is True
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}


@pytest.mark.asyncio
async def test_real_queued_activation_waits_for_successful_schedule_pause(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A staged queued wrapper settles PAUSED and remains resumable."""
    life = real_lifecycle
    queued_id = "Anchor Schedule--scheduled--queued-activation"
    later_id = "Anchor Schedule--scheduled--pending-after-queued"
    queued = life.add_meta(queued_id, DaemonJobStatus.QUEUED)
    life.add_meta(later_id, DaemonJobStatus.PENDING)
    life.manager._pending_jobs[later_id] = JobRequest(
        config_path=life.score_path,
        job_id=later_id,
        schedule_id=life.schedule_id,
        scheduled_due_at=2.0,
    )

    gate = life.manager._concurrency_semaphore
    gate.set_limit(1)
    await gate.acquire()
    gate_wait_entered = asyncio.Event()
    original_gate_acquire = gate.acquire

    async def observe_gate_wait() -> None:
        gate_wait_entered.set()
        await original_gate_acquire()

    monkeypatch.setattr(gate, "acquire", observe_gate_wait)

    activation_boundary = asyncio.Event()
    execution_started = asyncio.Event()
    execution_release = asyncio.Event()
    queued_transitions: list[DaemonJobStatus] = []
    later_stage_entered = asyncio.Event()
    release_later_stage = asyncio.Event()

    async def queued_execution() -> DaemonJobStatus:
        execution_started.set()
        activation_boundary.set()
        await execution_release.wait()
        return DaemonJobStatus.PAUSED

    queued_task: asyncio.Task[None] | None = None
    original_reserve = life.manager._try_reserve_job_admission

    def observe_activation_reservation(
        job_id: str,
        *,
        allow_active: bool = False,
    ) -> bool:
        reserved = original_reserve(job_id, allow_active=allow_active)
        if job_id == queued_id and asyncio.current_task() is queued_task:
            activation_boundary.set()
        return reserved

    monkeypatch.setattr(
        life.manager,
        "_try_reserve_job_admission",
        observe_activation_reservation,
    )
    original_set_status = life.manager._set_job_status

    async def hold_later_stage(
        job_id: str,
        status: DaemonJobStatus,
        **kwargs: object,
    ) -> None:
        if job_id == queued_id:
            queued_transitions.append(status)
        if job_id == later_id and status is DaemonJobStatus.PAUSED:
            later_stage_entered.set()
            await release_later_stage.wait()
        await original_set_status(job_id, status, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(life.manager, "_set_job_status", hold_later_stage)

    queued_task = asyncio.create_task(
        life.manager._run_managed_task(queued_id, queued_execution()),
        name=f"job-{queued_id}",
    )
    life.manager._jobs[queued_id] = queued_task
    queued_task.add_done_callback(
        lambda task: life.manager._on_task_done(queued_id, task)
    )
    await asyncio.wait_for(gate_wait_entered.wait(), timeout=1.0)

    pause = asyncio.create_task(life.manager.pause_job(life.schedule_id))
    await asyncio.wait_for(later_stage_entered.wait(), timeout=1.0)
    reservation = life.manager._job_admission_reservations[queued_id]
    assert reservation.owner is pause
    assert queued.status is DaemonJobStatus.PAUSED

    gate.release()
    await asyncio.wait_for(activation_boundary.wait(), timeout=1.0)
    release_later_stage.set()

    assert await asyncio.wait_for(pause, timeout=1.0) is True
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(queued_task), timeout=1.0)

    record = await life.manager._schedule_registry.get(life.schedule_id)
    assert record is not None and record.enabled is False
    assert queued.status is DaemonJobStatus.PAUSED
    assert DaemonJobStatus.CANCELLED not in queued_transitions
    assert execution_started.is_set() is False
    assert queued_task.done()
    assert queued_id not in life.manager._jobs
    assert gate.acquired == 0
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}

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
    assert resumed == [queued_id, later_id]
    record = await life.manager._schedule_registry.get(life.schedule_id)
    assert record is not None and record.enabled is True


@pytest.mark.asyncio
async def test_real_queued_activation_restarts_after_failed_schedule_pause(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback restores QUEUED before a blocked wrapper can run."""
    life = real_lifecycle
    queued_id = "Anchor Schedule--scheduled--queued-rollback"
    later_id = "Anchor Schedule--scheduled--pending-failure"
    queued = life.add_meta(queued_id, DaemonJobStatus.QUEUED)
    life.add_meta(later_id, DaemonJobStatus.PENDING)
    life.manager._pending_jobs[later_id] = JobRequest(
        config_path=life.score_path,
        job_id=later_id,
        schedule_id=life.schedule_id,
        scheduled_due_at=2.0,
    )

    gate = life.manager._concurrency_semaphore
    gate.set_limit(1)
    await gate.acquire()
    gate_wait_entered = asyncio.Event()
    original_gate_acquire = gate.acquire

    async def observe_gate_wait() -> None:
        gate_wait_entered.set()
        await original_gate_acquire()

    monkeypatch.setattr(gate, "acquire", observe_gate_wait)

    activation_boundary = asyncio.Event()
    execution_started = asyncio.Event()
    execution_release = asyncio.Event()
    queued_transitions: list[DaemonJobStatus] = []
    later_stage_entered = asyncio.Event()
    release_later_stage = asyncio.Event()

    async def queued_execution() -> DaemonJobStatus:
        execution_started.set()
        activation_boundary.set()
        await execution_release.wait()
        return DaemonJobStatus.PAUSED

    queued_task: asyncio.Task[None] | None = None
    original_reserve = life.manager._try_reserve_job_admission

    def observe_activation_reservation(
        job_id: str,
        *,
        allow_active: bool = False,
    ) -> bool:
        reserved = original_reserve(job_id, allow_active=allow_active)
        if job_id == queued_id and asyncio.current_task() is queued_task:
            activation_boundary.set()
        return reserved

    monkeypatch.setattr(
        life.manager,
        "_try_reserve_job_admission",
        observe_activation_reservation,
    )
    original_set_status = life.manager._set_job_status

    async def fail_later_stage(
        job_id: str,
        status: DaemonJobStatus,
        **kwargs: object,
    ) -> None:
        if job_id == queued_id:
            queued_transitions.append(status)
        if job_id == later_id and status is DaemonJobStatus.PAUSED:
            later_stage_entered.set()
            await release_later_stage.wait()
            raise RuntimeError("later pause stage failed")
        await original_set_status(job_id, status, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(life.manager, "_set_job_status", fail_later_stage)

    queued_task = asyncio.create_task(
        life.manager._run_managed_task(queued_id, queued_execution()),
        name=f"job-{queued_id}",
    )
    life.manager._jobs[queued_id] = queued_task
    queued_cleanup_done = asyncio.Event()

    def clean_up_queued_task(task: asyncio.Task[None]) -> None:
        life.manager._on_task_done(queued_id, task)
        queued_cleanup_done.set()

    queued_task.add_done_callback(clean_up_queued_task)
    await asyncio.wait_for(gate_wait_entered.wait(), timeout=1.0)

    pause = asyncio.create_task(life.manager.pause_job(life.schedule_id))
    await asyncio.wait_for(later_stage_entered.wait(), timeout=1.0)
    reservation = life.manager._job_admission_reservations[queued_id]
    assert reservation.owner is pause
    assert queued.status is DaemonJobStatus.PAUSED

    gate.release()
    await asyncio.wait_for(activation_boundary.wait(), timeout=1.0)
    started_before_release = execution_started.is_set()
    release_later_stage.set()

    with pytest.raises(RuntimeError, match="later pause stage failed"):
        await asyncio.wait_for(pause, timeout=1.0)
    await asyncio.wait_for(execution_started.wait(), timeout=1.0)

    record = await life.manager._schedule_registry.get(life.schedule_id)
    assert record is not None and record.enabled is True
    assert started_before_release is False
    assert queued_transitions == [
        DaemonJobStatus.PAUSED,
        DaemonJobStatus.QUEUED,
        DaemonJobStatus.RUNNING,
    ]
    assert queued.status is DaemonJobStatus.RUNNING
    assert life.manager._jobs[queued_id] is queued_task
    assert queued_task.done() is False
    assert gate.acquired == 1
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}

    execution_release.set()
    await asyncio.wait_for(queued_task, timeout=1.0)
    await asyncio.wait_for(queued_cleanup_done.wait(), timeout=1.0)
    assert queued.status is DaemonJobStatus.PAUSED
    assert queued_id not in life.manager._jobs
    assert gate.acquired == 0


@pytest.mark.asyncio
async def test_pending_pause_caller_cancellation_restores_staged_children(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller cancellation remains loud after staged child state is restored."""
    life = real_lifecycle
    first_id = "Anchor Schedule--scheduled--pending-cancel-first"
    second_id = "Anchor Schedule--scheduled--pending-cancel-second"
    first = life.add_meta(first_id, DaemonJobStatus.PENDING)
    second = life.add_meta(second_id, DaemonJobStatus.PENDING)
    requests = {
        job_id: JobRequest(
            config_path=life.score_path,
            job_id=job_id,
            schedule_id=life.schedule_id,
            scheduled_due_at=float(index),
        )
        for index, job_id in enumerate((first_id, second_id), start=1)
    }
    life.manager._pending_jobs.update(requests)
    second_entered = asyncio.Event()
    release_second = asyncio.Event()
    original_set_status = life.manager._set_job_status

    async def hold_second(
        job_id: str,
        status: DaemonJobStatus,
        **kwargs: object,
    ) -> None:
        if job_id == second_id and status is DaemonJobStatus.PAUSED:
            second_entered.set()
            await release_second.wait()
        await original_set_status(job_id, status, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(life.manager, "_set_job_status", hold_second)
    pause = asyncio.create_task(life.manager.pause_job(life.schedule_id))
    await asyncio.wait_for(second_entered.wait(), timeout=1.0)
    pause.cancel()
    release_second.set()

    with pytest.raises(asyncio.CancelledError):
        await pause

    assert first.status is DaemonJobStatus.PENDING
    assert second.status is DaemonJobStatus.PENDING
    assert life.manager._pending_jobs[first_id] is requests[first_id]
    assert life.manager._pending_jobs[second_id] is requests[second_id]
    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is True
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}


@pytest.mark.asyncio
async def test_pending_pause_rollback_failure_is_loud(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed child rollback is reported after recurrence safety restoration."""
    life = real_lifecycle
    first_id = "Anchor Schedule--scheduled--pending-rollback-first"
    second_id = "Anchor Schedule--scheduled--pending-rollback-second"
    life.add_meta(first_id, DaemonJobStatus.PENDING)
    life.add_meta(second_id, DaemonJobStatus.PENDING)
    for index, job_id in enumerate((first_id, second_id), start=1):
        life.manager._pending_jobs[job_id] = JobRequest(
            config_path=life.score_path,
            job_id=job_id,
            schedule_id=life.schedule_id,
            scheduled_due_at=float(index),
        )
    original_set_status = life.manager._set_job_status

    async def fail_pause_and_rollback(
        job_id: str,
        status: DaemonJobStatus,
        **kwargs: object,
    ) -> None:
        if job_id == second_id and status is DaemonJobStatus.PAUSED:
            raise RuntimeError("second pending pause failed")
        if job_id == first_id and status is DaemonJobStatus.PENDING:
            raise RuntimeError("pending rollback failed")
        await original_set_status(job_id, status, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(life.manager, "_set_job_status", fail_pause_and_rollback)

    with pytest.raises(RuntimeError, match="rollback.*failed"):
        await life.manager.pause_job(life.schedule_id)

    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is True
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}


@pytest.mark.asyncio
async def test_pause_partial_failure_does_not_cancel_queued_child(
    real_lifecycle: RealLifecycle,
) -> None:
    """Fallible running pauses complete before queued work is changed."""
    life = real_lifecycle
    queued_id = "Anchor Schedule--scheduled--queued-before-failure"
    running_id = "Anchor Schedule--scheduled--running-failure"
    queued = life.add_meta(queued_id, DaemonJobStatus.QUEUED)
    life.add_meta(running_id, DaemonJobStatus.RUNNING)
    queued_task = asyncio.create_task(asyncio.Event().wait())
    life.manager._jobs[queued_id] = queued_task
    life.manager._jobs[running_id] = asyncio.create_task(asyncio.Event().wait())
    life.manager._service.pause_job = AsyncMock(side_effect=RuntimeError("pause failed"))

    with pytest.raises(RuntimeError, match="pause failed"):
        await life.manager.pause_job(life.schedule_id)

    assert life.manager._jobs[queued_id] is queued_task
    assert not queued_task.done()
    assert queued.status is DaemonJobStatus.QUEUED
    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is True
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}


@pytest.mark.asyncio
async def test_pause_partial_failure_clears_earlier_running_pause_request(
    real_lifecycle: RealLifecycle,
) -> None:
    """An earlier in-process pause signal is compensated on later failure."""
    life = real_lifecycle
    first_id = "Anchor Schedule--scheduled--running-first"
    second_id = "Anchor Schedule--scheduled--running-failure"
    life.add_meta(first_id, DaemonJobStatus.RUNNING)
    life.add_meta(second_id, DaemonJobStatus.RUNNING)
    first_event = asyncio.Event()
    life.manager._pause_events[first_id] = first_event
    life.manager._jobs[first_id] = asyncio.create_task(asyncio.Event().wait())
    life.manager._jobs[second_id] = asyncio.create_task(asyncio.Event().wait())
    life.manager._service.pause_job = AsyncMock(side_effect=RuntimeError("pause failed"))

    with pytest.raises(RuntimeError, match="pause failed"):
        await life.manager.pause_job(life.schedule_id)

    assert not first_event.is_set()
    assert life.manager._job_meta[first_id].status is DaemonJobStatus.RUNNING
    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is True
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}


@pytest.mark.asyncio
async def test_pause_partial_failure_restores_baton_gate_and_running_status(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later child failure compensates an already-paused baton child exactly."""
    life = real_lifecycle
    baton_id = "Anchor Schedule--scheduled--baton-first"
    pending_id = "Anchor Schedule--scheduled--pending-failure"
    baton_meta = life.add_meta(baton_id, DaemonJobStatus.RUNNING)
    pending_meta = life.add_meta(pending_id, DaemonJobStatus.PENDING)
    request = JobRequest(
        config_path=life.score_path,
        job_id=pending_id,
        schedule_id=life.schedule_id,
        scheduled_due_at=1.0,
    )
    life.manager._pending_jobs[pending_id] = request
    life.manager._baton_adapter = life.adapter
    life.adapter._baton.register_job(
        baton_id,
        {1: SheetExecutionState(sheet_num=1, instrument_name="test")},
        {},
    )
    original_set_status = life.manager._set_job_status

    async def fail_pending(
        job_id: str,
        status: DaemonJobStatus,
        **kwargs: object,
    ) -> None:
        if job_id == pending_id and status is DaemonJobStatus.PAUSED:
            raise RuntimeError("pending pause failed")
        await original_set_status(job_id, status, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(life.manager, "_set_job_status", fail_pending)

    with pytest.raises(RuntimeError, match="pending pause failed"):
        await life.manager.pause_job(life.schedule_id)

    baton_state = life.adapter._baton._jobs[baton_id]
    assert baton_state.paused is False
    assert baton_state.user_paused is False
    assert baton_meta.status is DaemonJobStatus.RUNNING
    assert pending_meta.status is DaemonJobStatus.PENDING
    assert life.manager._pending_jobs[pending_id] is request
    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is True
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}


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
async def test_resume_post_enable_controller_failure_restores_disabled_recurrence(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    await life.controller.pause(life.schedule_id)
    original_get = life.manager._schedule_registry.get
    injected = False

    async def fail_enabled_suffix(schedule_id: str):  # type: ignore[no-untyped-def]
        nonlocal injected
        record = await original_get(schedule_id)
        if (
            asyncio.current_task() is resume
            and record is not None
            and record.enabled
            and not injected
        ):
            injected = True
            raise RuntimeError("post-enable suffix failed")
        return record

    monkeypatch.setattr(life.manager._schedule_registry, "get", fail_enabled_suffix)
    resume = asyncio.create_task(life.manager.resume_job(life.schedule_id))

    with pytest.raises(RuntimeError, match="post-enable suffix failed"):
        await resume

    current = await original_get(life.schedule_id)
    assert current is not None and current.enabled is False


@pytest.mark.asyncio
async def test_resume_cancellation_in_post_enable_suffix_restores_owner_and_state(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    await life.controller.pause(life.schedule_id)
    suffix_entered = asyncio.Event()
    release_suffix = asyncio.Event()
    original_get = life.manager._schedule_registry.get

    async def hold_enabled_suffix(schedule_id: str):  # type: ignore[no-untyped-def]
        record = await original_get(schedule_id)
        if (
            asyncio.current_task() is resume
            and record is not None
            and record.enabled
        ):
            suffix_entered.set()
            await release_suffix.wait()
        return record

    monkeypatch.setattr(life.manager._schedule_registry, "get", hold_enabled_suffix)
    resume = asyncio.create_task(life.manager.resume_job(life.schedule_id))
    await asyncio.wait_for(suffix_entered.wait(), timeout=1.0)
    reservation = life.manager._schedule_admission_reservations[life.schedule_id]
    assert reservation.owner is resume and reservation.depth == 1
    resume.cancel()
    release_suffix.set()

    with pytest.raises(asyncio.CancelledError):
        await resume

    current = await original_get(life.schedule_id)
    assert current is not None and current.enabled is False
    assert life.manager._schedule_admission_reservations == {}


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
async def test_second_child_resume_failure_repauses_first_and_recurrence(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    await life.controller.pause(life.schedule_id)
    first_id = "Anchor Schedule--scheduled--paused-first"
    second_id = "Anchor Schedule--scheduled--paused-second"
    first_meta = life.add_meta(first_id, DaemonJobStatus.PAUSED)
    life.add_meta(second_id, DaemonJobStatus.PAUSED)

    async def fail_second_resume(
        manager: JobManager,
        job_id: str,
        **_kwargs: object,
    ) -> JobResponse:
        if job_id == second_id:
            raise RuntimeError("second resume failed")
        first_meta.status = DaemonJobStatus.QUEUED
        manager._jobs[job_id] = asyncio.create_task(asyncio.Event().wait())
        return JobResponse(job_id=job_id, status="accepted")

    monkeypatch.setattr(JobManager, "_resume_active_job", fail_second_resume)

    with pytest.raises(RuntimeError, match="second resume failed"):
        await life.manager.resume_job(life.schedule_id)

    first_task = life.manager._jobs.get(first_id)
    assert first_task is None or first_task.cancelled()
    assert first_meta.status is DaemonJobStatus.PAUSED
    record = await life.manager._schedule_registry.get(life.schedule_id)
    assert record is not None and record.enabled is False


@pytest.mark.asyncio
async def test_rejected_held_chain_resume_restores_disabled_recurrence(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-accepted child response is a failed schedule transaction."""
    life = real_lifecycle
    await life.controller.pause(life.schedule_id)
    child_id = "Anchor Schedule--scheduled--held-chain"
    child = life.add_meta(child_id, DaemonJobStatus.PAUSED_AT_CHAIN)
    child.held_chain_hook = {
        "job_path": str(life.score_path),
        "workspace": str(life.workspace),
    }

    async def reject_resume(
        _manager: JobManager,
        job_id: str,
        **_kwargs: object,
    ) -> JobResponse:
        return JobResponse(job_id=job_id, status="rejected", message="chain rejected")

    monkeypatch.setattr(JobManager, "_resume_active_job", reject_resume)

    response = await life.manager.resume_job(life.schedule_id)

    assert response.status == "rejected"
    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is False
    assert child.status is DaemonJobStatus.PAUSED_AT_CHAIN


@pytest.mark.asyncio
async def test_multiple_held_chains_reject_before_any_irreversible_resume(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only one held chain may be released by a schedule-wide command."""
    life = real_lifecycle
    await life.controller.pause(life.schedule_id)
    calls: list[str] = []
    for suffix in ("first", "second"):
        child = life.add_meta(
            f"Anchor Schedule--scheduled--held-{suffix}",
            DaemonJobStatus.PAUSED_AT_CHAIN,
        )
        child.held_chain_hook = {
            "job_path": str(life.score_path),
            "workspace": str(life.workspace),
        }

    async def record_resume(
        _manager: JobManager,
        job_id: str,
        **_kwargs: object,
    ) -> JobResponse:
        calls.append(job_id)
        return JobResponse(job_id=job_id, status="accepted")

    monkeypatch.setattr(JobManager, "_resume_active_job", record_resume)

    response = await life.manager.resume_job(life.schedule_id)

    assert response.status == "rejected"
    assert calls == []
    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is False


@pytest.mark.asyncio
async def test_cancellation_during_active_rollback_cannot_skip_recurrence_rollback(
    real_lifecycle: RealLifecycle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = real_lifecycle
    await life.controller.pause(life.schedule_id)
    first_id = "Anchor Schedule--scheduled--paused-first"
    second_id = "Anchor Schedule--scheduled--paused-second"
    first_meta = life.add_meta(first_id, DaemonJobStatus.PAUSED)
    life.add_meta(second_id, DaemonJobStatus.PAUSED)
    rollback_entered = asyncio.Event()
    release_rollback = asyncio.Event()
    original_rollback = life.manager._re_pause_resumed_jobs

    async def fail_second_resume(
        manager: JobManager,
        job_id: str,
        **_kwargs: object,
    ) -> JobResponse:
        if job_id == second_id:
            raise RuntimeError("second resume failed")
        first_meta.status = DaemonJobStatus.QUEUED
        manager._jobs[job_id] = asyncio.create_task(asyncio.Event().wait())
        return JobResponse(job_id=job_id, status="accepted")

    async def held_rollback(resumed: object) -> None:
        rollback_entered.set()
        await release_rollback.wait()
        await original_rollback(resumed)  # type: ignore[arg-type]

    monkeypatch.setattr(JobManager, "_resume_active_job", fail_second_resume)
    monkeypatch.setattr(life.manager, "_re_pause_resumed_jobs", held_rollback)
    resume = asyncio.create_task(life.manager.resume_job(life.schedule_id))
    await asyncio.wait_for(rollback_entered.wait(), timeout=1.0)
    reservation = life.manager._schedule_admission_reservations[life.schedule_id]
    assert reservation.owner is resume and reservation.depth == 1
    resume.cancel()
    release_rollback.set()

    with pytest.raises(asyncio.CancelledError):
        await resume

    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is False
    assert first_meta.status is DaemonJobStatus.PAUSED
    assert life.manager._schedule_admission_reservations == {}


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
async def test_exact_schedule_id_with_child_marker_is_still_one_stable_anchor(
    real_lifecycle: RealLifecycle,
) -> None:
    """Anchor visibility uses durable identity, never a child-ID substring."""
    life = real_lifecycle
    replacement_id = "Stable--scheduled--Anchor"
    life.replace_schedule_name(replacement_id)
    replacement = await life.controller.register(life.score_path)
    assert replacement is not None
    life.schedule_id = replacement_id
    life.add_meta(replacement_id, DaemonJobStatus.COMPLETED)

    jobs = await life.manager.list_jobs()

    assert [job["job_id"] for job in jobs].count(replacement_id) == 1


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
    publication_entered = asyncio.Event()
    release_publication = asyncio.Event()
    release_job = asyncio.Event()
    original_lock = life.controller._lock_schedules
    original_register_job = life.manager._registry.register_job

    @asynccontextmanager
    async def held_lock(*schedule_ids: str):  # type: ignore[no-untyped-def]
        current = asyncio.current_task()
        if current is not None and current.get_name() == "raced-submit":
            register_after_probe.set()
            await release_register.wait()
        async with original_lock(*schedule_ids):
            yield

    async def held_register_job(*args: object, **kwargs: object) -> None:
        publication_entered.set()
        await release_publication.wait()
        await original_register_job(*args, **kwargs)  # type: ignore[arg-type]

    async def hold_execution(*_args: object, **_kwargs: object) -> None:
        await release_job.wait()

    child_id = "Anchor Schedule--scheduled--running"
    life.add_meta(child_id, DaemonJobStatus.RUNNING)
    life.manager._jobs[child_id] = asyncio.create_task(asyncio.Event().wait())
    life.manager._pause_events[child_id] = asyncio.Event()
    monkeypatch.setattr(life.controller, "_lock_schedules", held_lock)
    monkeypatch.setattr(life.manager._registry, "register_job", held_register_job)
    monkeypatch.setattr(life.manager, "_run_job_task", hold_execution)

    submit = asyncio.create_task(
        life.manager.submit_job(JobRequest(config_path=life.score_path, fresh=True)),
        name="raced-submit",
    )
    await asyncio.wait_for(register_after_probe.wait(), timeout=1.0)
    assert life.manager._schedule_admission_reservations == {}
    raced_pause = asyncio.create_task(life.manager.pause_job(life.schedule_id))
    release_register.set()
    await asyncio.wait_for(publication_entered.wait(), timeout=1.0)

    try:
        with pytest.raises(JobSubmissionError, match="already active"):
            await asyncio.wait_for(raced_pause, timeout=1.0)
        reservation = life.manager._schedule_admission_reservations[life.schedule_id]
        assert reservation.owner is submit and reservation.depth == 1
    finally:
        release_publication.set()

    submitted = await submit
    assert submitted.status == "accepted"
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}

    assert await life.manager.pause_job(life.schedule_id) is True
    current = await life.manager._schedule_registry.get(life.schedule_id)
    assert current is not None and current.enabled is False
    assert life.manager._pause_events[child_id].is_set()
    assert life.manager._job_meta[submitted.job_id].status is DaemonJobStatus.PAUSED
    assert life.manager._job_admission_reservations == {}
    assert life.manager._schedule_admission_reservations == {}
    release_job.set()


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
