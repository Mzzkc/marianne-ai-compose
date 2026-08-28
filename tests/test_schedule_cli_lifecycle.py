"""Recurring-score lifecycle coverage through the existing manager surface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.daemon.config import DaemonConfig
from marianne.daemon.exceptions import JobSubmissionError
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.types import JobRequest


class FakeRecurrenceController:
    """Deterministic lifecycle fake with durable-state-shaped projections."""

    def __init__(self, schedule_id: str = "recurring-job") -> None:
        self.records: dict[str, SimpleNamespace] = {
            schedule_id: SimpleNamespace(
                schedule_id=schedule_id,
                score_path=Path(f"/tmp/{schedule_id}.yaml"),
                enabled=True,
                next_due_at=2_000_000_000.0,
                last_due_at=None,
                last_run_id=None,
                last_outcome=None,
                consecutive_drops=0,
            )
        }
        self.calls: list[tuple[str, str]] = []
        self.fail: set[str] = set()

    async def register(
        self,
        score_path: Path,
        config: Any,
        *,
        before_wait: Any = None,
        before_mutation: Any = None,
    ) -> SimpleNamespace:
        schedule_ids = (config.name,)
        if before_wait is not None:
            assert before_wait(schedule_ids)
        if before_mutation is not None:
            before_mutation(schedule_ids)
        self.calls.append(("register", config.name))
        return self.records[config.name]

    async def describe(self, schedule_id: str | None = None) -> list[SimpleNamespace]:
        if schedule_id is None:
            return list(self.records.values())
        record = self.records.get(schedule_id)
        return [] if record is None else [record]

    async def pause(
        self,
        schedule_id: str,
        *,
        score_path: Path | None = None,
        before_mutation: Any = None,
        on_authority: Any = None,
        on_mutation: Any = None,
    ) -> SimpleNamespace:
        _ = score_path
        if before_mutation is not None:
            before_mutation((schedule_id,))
        record = self.records[schedule_id]
        if on_authority is not None:
            on_authority(record)
        self.calls.append(("pause", schedule_id))
        if "pause" in self.fail:
            raise RuntimeError("pause failed")
        updated = SimpleNamespace(**vars(record))
        updated.enabled = False
        self.records[schedule_id] = updated
        if on_mutation is not None:
            on_mutation(record)
        return updated

    async def resume(
        self,
        schedule_id: str,
        *,
        score_path: Path | None = None,
        before_mutation: Any = None,
        on_authority: Any = None,
    ) -> SimpleNamespace:
        _ = score_path
        if before_mutation is not None:
            before_mutation((schedule_id,))
        record = self.records[schedule_id]
        if on_authority is not None:
            on_authority(record)
        self.calls.append(("resume", schedule_id))
        if "resume" in self.fail:
            raise RuntimeError("resume failed")
        updated = SimpleNamespace(**vars(record))
        updated.enabled = True
        self.records[schedule_id] = updated
        return updated

    async def remove(
        self,
        schedule_id: str,
        *,
        score_path: Path | None = None,
        before_mutation: Any = None,
        on_authority: Any = None,
    ) -> SimpleNamespace:
        _ = score_path
        if before_mutation is not None:
            before_mutation((schedule_id,))
        record = self.records[schedule_id]
        if on_authority is not None:
            on_authority(record)
        self.calls.append(("remove", schedule_id))
        if "remove" in self.fail:
            raise RuntimeError("remove failed")
        self.records.pop(schedule_id, None)
        return record


@pytest.fixture
async def lifecycle_manager(tmp_path: Path):
    manager = JobManager(
        DaemonConfig(
            state_db_path=tmp_path / "state.db",
            pid_file=tmp_path / "daemon.pid",
        )
    )
    await manager._registry.open()
    manager._service = MagicMock()
    controller = FakeRecurrenceController()
    manager._recurrence_controller = controller  # type: ignore[assignment]
    yield manager, controller
    for task in manager._jobs.values():
        task.cancel()
    if manager._jobs:
        await asyncio.gather(*manager._jobs.values(), return_exceptions=True)
    await manager._registry.close()


def _scheduled_score(path: Path, *, interval: str = "5m") -> Path:
    path.write_text(
        "name: recurring-job\n"
        "workspace: ./workspace\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: test\n"
        f"schedule:\n  interval: {interval}\n"
    )
    return path


def _active_meta(manager: JobManager, job_id: str, status: DaemonJobStatus) -> None:
    manager._job_meta[job_id] = JobMeta(
        job_id=job_id,
        config_path=Path("/tmp/recurring.yaml"),
        workspace=Path("/tmp/workspace"),
        status=status,
        schedule_id="recurring-job",
    )


@pytest.mark.asyncio
async def test_scheduled_run_registers_once_and_starts_immediately(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, controller = lifecycle_manager
    release = asyncio.Event()

    async def hold_job(*_args: Any, **_kwargs: Any) -> None:
        await release.wait()

    monkeypatch.setattr(manager, "_run_job_task", hold_job)
    score = _scheduled_score(tmp_path / "recurring.yaml")

    response = await manager.submit_job(JobRequest(config_path=score, fresh=True))

    assert response.status == "accepted"
    assert controller.calls == [("register", "recurring-job")]
    assert manager._job_meta[response.job_id].schedule_id == "recurring-job"
    assert response.job_id in manager._jobs
    release.set()


@pytest.mark.asyncio
async def test_fresh_trigger_now_replaces_without_duplicate_registration(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, controller = lifecycle_manager

    async def finish_job(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(manager, "_run_job_task", finish_job)
    score = _scheduled_score(tmp_path / "recurring.yaml", interval="10m")

    response = await manager.submit_job(JobRequest(config_path=score, fresh=True))
    await manager._jobs[response.job_id]

    assert response.status == "accepted"
    assert controller.calls.count(("register", "recurring-job")) == 1
    assert list(controller.records) == ["recurring-job"]


@pytest.mark.asyncio
async def test_pause_pauses_recurrence_and_active_work(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
) -> None:
    manager, controller = lifecycle_manager
    _active_meta(manager, "recurring-job", DaemonJobStatus.RUNNING)
    manager._pause_events["recurring-job"] = asyncio.Event()
    manager._jobs["recurring-job"] = asyncio.create_task(asyncio.Event().wait())

    assert await manager.pause_job("recurring-job") is True
    assert manager._pause_events["recurring-job"].is_set()
    assert controller.calls == [("pause", "recurring-job")]
    assert controller.records["recurring-job"].enabled is False


@pytest.mark.asyncio
async def test_pause_and_resume_recurrence_without_active_work(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
) -> None:
    manager, controller = lifecycle_manager

    assert await manager.pause_job("recurring-job") is True
    response = await manager.resume_job("recurring-job")

    assert response.status == "accepted"
    assert controller.calls == [
        ("pause", "recurring-job"),
        ("resume", "recurring-job"),
    ]
    assert controller.records["recurring-job"].enabled is True


@pytest.mark.asyncio
async def test_resume_enables_recurrence_and_resumes_paused_active_work(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, controller = lifecycle_manager
    _active_meta(manager, "recurring-job", DaemonJobStatus.PAUSED)
    release = asyncio.Event()

    async def hold_resume(*_args: Any, **_kwargs: Any) -> None:
        await release.wait()

    monkeypatch.setattr(manager, "_resume_job_task", hold_resume)

    response = await manager.resume_job("recurring-job")

    assert response.status == "accepted"
    assert manager._job_meta["recurring-job"].status is DaemonJobStatus.QUEUED
    assert controller.calls == [("resume", "recurring-job")]
    release.set()


@pytest.mark.asyncio
async def test_resume_completed_immediate_run_only_enables_future_ticks(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
) -> None:
    manager, controller = lifecycle_manager
    controller.records["recurring-job"].enabled = False
    _active_meta(manager, "recurring-job", DaemonJobStatus.COMPLETED)

    response = await manager.resume_job("recurring-job")

    assert response.status == "accepted"
    assert response.message == "Recurring schedule resumed"
    assert manager._job_meta["recurring-job"].status is DaemonJobStatus.COMPLETED
    assert controller.calls == [("resume", "recurring-job")]
    assert controller.records["recurring-job"].enabled is True


@pytest.mark.asyncio
async def test_cancel_removes_recurrence_with_or_without_active_work(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
) -> None:
    manager, controller = lifecycle_manager

    assert await manager.cancel_job("recurring-job") is True
    assert controller.calls == [("remove", "recurring-job")]
    assert controller.records == {}


@pytest.mark.asyncio
async def test_cancel_stops_active_work_before_removing_recurrence(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
) -> None:
    manager, controller = lifecycle_manager
    _active_meta(manager, "recurring-job", DaemonJobStatus.RUNNING)
    task = asyncio.create_task(asyncio.Event().wait())
    manager._jobs["recurring-job"] = task

    assert await manager.cancel_job("recurring-job") is True
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert manager._job_meta["recurring-job"].status is DaemonJobStatus.CANCELLED
    assert controller.calls == [("remove", "recurring-job")]
    assert controller.records == {}


@pytest.mark.asyncio
async def test_unknown_non_recurring_lifecycle_keeps_existing_failures(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
) -> None:
    manager, _controller = lifecycle_manager

    with pytest.raises(JobSubmissionError, match="not found"):
        await manager.pause_job("unknown")
    with pytest.raises(JobSubmissionError, match="not found"):
        await manager.resume_job("unknown")
    assert await manager.cancel_job("unknown") is False


@pytest.mark.asyncio
async def test_restored_job_resolves_schedule_by_authoritative_score_path(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
) -> None:
    manager, controller = lifecycle_manager
    score_path = Path("/tmp/file-stem.yaml")
    controller.records["recurring-job"].score_path = score_path
    manager._job_meta["file-stem"] = JobMeta(
        job_id="file-stem",
        config_path=score_path,
        workspace=Path("/tmp/workspace"),
        status=DaemonJobStatus.COMPLETED,
        schedule_id=None,
    )

    status = await manager.get_job_status("file-stem")

    assert status["schedule"] == {
        "enabled": True,
        "next_due_at": 2_000_000_000.0,
        "last_due_at": None,
        "last_run_id": None,
        "last_outcome": None,
        "consecutive_drops": 0,
    }


@pytest.mark.asyncio
async def test_failed_active_pause_restores_enabled_recurrence(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
) -> None:
    manager, controller = lifecycle_manager
    _active_meta(manager, "recurring-job", DaemonJobStatus.RUNNING)
    manager._jobs["recurring-job"] = asyncio.create_task(asyncio.Event().wait())
    manager._service.pause_job = AsyncMock(side_effect=RuntimeError("active pause failed"))

    with pytest.raises(RuntimeError, match="active pause failed"):
        await manager.pause_job("recurring-job")

    assert controller.calls == [
        ("pause", "recurring-job"),
        ("resume", "recurring-job"),
    ]
    assert controller.records["recurring-job"].enabled is True


@pytest.mark.asyncio
async def test_failed_active_resume_restores_paused_recurrence(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, controller = lifecycle_manager
    controller.records["recurring-job"].enabled = False
    _active_meta(manager, "recurring-job", DaemonJobStatus.PAUSED)

    async def fail_status(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("active resume failed")

    monkeypatch.setattr(manager, "_set_job_status", fail_status)

    with pytest.raises(RuntimeError, match="active resume failed"):
        await manager.resume_job("recurring-job")

    assert controller.calls == [
        ("resume", "recurring-job"),
        ("pause", "recurring-job"),
    ]
    assert controller.records["recurring-job"].enabled is False


@pytest.mark.asyncio
async def test_failed_cancel_removal_pauses_recurrence_and_fails_loudly(
    lifecycle_manager: tuple[JobManager, FakeRecurrenceController],
) -> None:
    manager, controller = lifecycle_manager
    controller.fail.add("remove")

    with pytest.raises(RuntimeError, match="remove failed"):
        await manager.cancel_job("recurring-job")

    assert controller.calls == [
        ("remove", "recurring-job"),
        ("pause", "recurring-job"),
    ]
    assert controller.records["recurring-job"].enabled is False
