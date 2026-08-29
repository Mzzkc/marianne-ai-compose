"""Adversarial restart, pause, cleanup, and legacy deadline tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.registry import JobRegistry


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class CleanupTrackingBaton:
    """Small double for the existing deregistration/process-group seam."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.active_process_groups = {43210}
        self.deregister_calls = 0

    def has_job(self, job_id: str) -> bool:
        return job_id == self.job_id and bool(self.active_process_groups)

    def deregister_job(self, job_id: str) -> None:
        assert job_id == self.job_id
        self.deregister_calls += 1
        self.active_process_groups.clear()


def _manager(tmp_path: Path, clock: MutableClock, daemon_limit: float) -> JobManager:
    config = DaemonConfig(
        state_db_path=tmp_path / "jobs.db",
        observer={"enabled": False},
        learning={"enabled": False},
    ).model_copy(update={"job_timeout_seconds": daemon_limit})
    return JobManager(config, wall_clock=clock, monotonic_clock=clock)


async def _capture_timeout(
    manager: JobManager,
    monkeypatch: pytest.MonkeyPatch,
    job_id: str,
) -> float:
    selected: list[float | None] = []

    async def wait_for(coro: Any, timeout: float | None) -> Any:
        selected.append(timeout)
        return await coro

    monkeypatch.setattr("marianne.daemon.manager.asyncio.wait_for", wait_for)

    async def finish() -> None:
        return None

    await manager._run_managed_task(job_id, finish())
    assert len(selected) == 1
    assert selected[0] is not None
    return selected[0]


@pytest.mark.asyncio
async def test_active_timeout_reaches_existing_baton_process_group_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(1_000.0)
    manager = _manager(tmp_path, clock, daemon_limit=300.0)
    await manager._registry.open()
    started = asyncio.Event()
    baton = CleanupTrackingBaton("active-timeout")
    manager._baton_adapter = cast(Any, baton)
    try:
        await manager._registry.register_job(
            "active-timeout",
            Path("/tmp/score.yaml"),
            Path("/tmp/workspace"),
            submitted_at=900.0,
            max_wall_seconds=120.0,
        )
        meta = JobMeta(
            job_id="active-timeout",
            config_path=Path("/tmp/score.yaml"),
            workspace=Path("/tmp/workspace"),
            submitted_at=900.0,
            max_wall_seconds=120.0,
            wall_deadline_at=1_020.0,
        )
        manager._job_meta[meta.job_id] = meta

        async def execution() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                baton.deregister_job(meta.job_id)
                raise

        async def deterministic_timeout(coro: Any, timeout: float | None) -> Any:
            assert timeout == 20.0
            task = asyncio.create_task(coro)
            await started.wait()
            clock.value = 1_020.0
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            raise TimeoutError

        monkeypatch.setattr(
            "marianne.daemon.manager.asyncio.wait_for",
            deterministic_timeout,
        )
        await manager._run_managed_task(meta.job_id, execution())

        assert baton.deregister_calls >= 1
        assert baton.active_process_groups == set()
        assert meta.status is DaemonJobStatus.FAILED
        assert meta.terminal_reason == "timed_out"
        assert meta.timeout_cleanup_outcome is not None
        assert meta.timeout_cleanup_outcome.deregistration_state == "attempted"
        assert meta.timeout_cleanup_outcome.residual_check_state == "unverified"
        assert meta.timeout_cleanup_outcome.residual_process_groups is None
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("interactive_state", ["paused", "fermata"])
async def test_paused_and_fermata_time_consume_same_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interactive_state: str,
) -> None:
    clock = MutableClock(2_000.0)
    manager = _manager(tmp_path, clock, daemon_limit=500.0)
    await manager._registry.open()
    try:
        meta = JobMeta(
            job_id=interactive_state,
            config_path=Path("/tmp/score.yaml"),
            workspace=Path("/tmp/workspace"),
            submitted_at=2_000.0,
            status=DaemonJobStatus.PAUSED,
            max_wall_seconds=180.0,
            wall_deadline_at=2_180.0,
        )
        manager._job_meta[meta.job_id] = meta
        if interactive_state == "fermata":
            manager._live_states[meta.job_id] = CheckpointState(
                job_id=meta.job_id,
                job_name=meta.job_id,
                total_sheets=1,
                sheets={1: SheetState(sheet_num=1, status=SheetStatus.FERMATA)},
                max_wall_seconds=180.0,
                wall_deadline_at=2_180.0,
            )

        clock.value = 2_150.0
        meta.status = DaemonJobStatus.QUEUED
        selected = await _capture_timeout(manager, monkeypatch, meta.job_id)

        assert selected == 30.0
        assert meta.wall_deadline_at == 2_180.0
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_daemon_restart_restores_deadline_without_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "jobs.db"
    registry = JobRegistry(db_path)
    await registry.open()
    await registry.register_job(
        "restart",
        Path("/tmp/score.yaml"),
        Path("/tmp/workspace"),
        submitted_at=3_000.0,
        max_wall_seconds=300.0,
    )
    # A terminal row is restored without starting orphan recovery in the test.
    # This isolates restart persistence from the separate recovery-path coverage.
    await registry.update_status("restart", DaemonJobStatus.FAILED.value)
    await registry.close()

    clock = MutableClock(3_240.0)
    manager = _manager(tmp_path, clock, daemon_limit=500.0)
    try:
        await manager.start()
        meta = manager._job_meta["restart"]
        assert meta.wall_deadline_at == 3_300.0
        await manager._set_job_status("restart", DaemonJobStatus.QUEUED)
        with monkeypatch.context() as patcher:
            selected = await _capture_timeout(manager, patcher, "restart")
        assert selected == 60.0
        assert meta.wall_deadline_at == 3_300.0
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_immediate_expiry_precedes_observer_and_musician_dispatch(
    tmp_path: Path,
) -> None:
    clock = MutableClock(4_100.0)
    manager = _manager(tmp_path, clock, daemon_limit=500.0)
    await manager._registry.open()
    observer_start = AsyncMock()
    manager._start_observer = observer_start
    baton = CleanupTrackingBaton("immediate")
    manager._baton_adapter = cast(Any, baton)
    dispatched = False
    try:
        await manager._registry.register_job(
            "immediate",
            Path("/tmp/score.yaml"),
            Path("/tmp/workspace"),
            submitted_at=4_000.0,
            max_wall_seconds=100.0,
        )
        meta = JobMeta(
            job_id="immediate",
            config_path=Path("/tmp/score.yaml"),
            workspace=Path("/tmp/workspace"),
            submitted_at=4_000.0,
            max_wall_seconds=100.0,
            wall_deadline_at=4_100.0,
        )
        manager._job_meta[meta.job_id] = meta

        async def execution() -> None:
            nonlocal dispatched
            dispatched = True

        await manager._run_managed_task(meta.job_id, execution())

        observer_start.assert_not_awaited()
        assert dispatched is False
        assert meta.started_at is None
        assert meta.terminal_reason == "timed_out"
        assert baton.deregister_calls == 1
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_malformed_legacy_fields_fall_back_without_minting_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(5_000.0)
    manager = _manager(tmp_path, clock, daemon_limit=75.0)
    await manager._registry.open()
    try:
        await manager._registry.register_job(
            "malformed",
            Path("/tmp/score.yaml"),
            Path("/tmp/workspace"),
            submitted_at=4_900.0,
        )
        await manager._registry._db.execute(
            "UPDATE jobs SET max_wall_seconds = 'not-a-number', "
            "wall_deadline_at = 'also-bad' WHERE job_id = 'malformed'"
        )
        await manager._registry._db.commit()
        record = await manager._registry.get_job("malformed")
        assert record is not None
        assert record.max_wall_seconds is None
        assert record.wall_deadline_at is None
        assert record.deadline_diagnostic is not None

        meta = JobMeta(
            job_id=record.job_id,
            config_path=Path(record.config_path),
            workspace=Path(record.workspace),
            submitted_at=record.submitted_at,
            deadline_diagnostic=record.deadline_diagnostic,
        )
        manager._job_meta[meta.job_id] = meta
        selected = await _capture_timeout(manager, monkeypatch, meta.job_id)
        status = await manager.get_job_status(meta.job_id)

        assert selected == 75.0
        assert meta.wall_deadline_at is None
        assert status["deadline"]["daemon_limit_seconds"] == 75.0
        assert status["deadline"]["effective_remaining_seconds"] == 75.0
        assert status["deadline"]["diagnostic"] == record.deadline_diagnostic
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_daemon_only_status_exposes_effective_limit_and_elapsed(
    tmp_path: Path,
) -> None:
    clock = MutableClock(6_030.0)
    manager = _manager(tmp_path, clock, daemon_limit=80.0)
    await manager._registry.open()
    try:
        manager._job_meta["legacy-status"] = JobMeta(
            job_id="legacy-status",
            config_path=Path("/tmp/score.yaml"),
            workspace=Path("/tmp/workspace"),
            submitted_at=6_000.0,
        )
        status = await manager.get_job_status("legacy-status")
        assert status["deadline"] == {
            "daemon_limit_seconds": 80.0,
            "effective_remaining_seconds": 80.0,
            "elapsed_seconds": 30.0,
        }
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_timeout_terminal_reason_is_flushed_to_checkpoint(
    tmp_path: Path,
) -> None:
    clock = MutableClock(7_100.0)
    manager = _manager(tmp_path, clock, daemon_limit=500.0)
    await manager._registry.open()
    try:
        await manager._registry.register_job(
            "checkpoint-timeout",
            Path("/tmp/score.yaml"),
            Path("/tmp/workspace"),
            submitted_at=7_000.0,
            max_wall_seconds=100.0,
        )
        meta = JobMeta(
            job_id="checkpoint-timeout",
            config_path=Path("/tmp/score.yaml"),
            workspace=Path("/tmp/workspace"),
            submitted_at=7_000.0,
            max_wall_seconds=100.0,
            wall_deadline_at=7_100.0,
        )
        manager._job_meta[meta.job_id] = meta
        manager._live_states[meta.job_id] = CheckpointState(
            job_id=meta.job_id,
            job_name=meta.job_id,
            total_sheets=1,
            max_wall_seconds=100.0,
            wall_deadline_at=7_100.0,
        )
        await manager._registry.save_checkpoint(
            meta.job_id,
            manager._live_states[meta.job_id].model_dump_json(),
        )

        async def must_not_run() -> None:
            raise AssertionError("expired work dispatched")

        await manager._run_managed_task(meta.job_id, must_not_run())
        raw = await manager._registry.load_checkpoint(meta.job_id)
        assert raw is not None
        restored = CheckpointState.model_validate_json(raw)
        assert restored.terminal_reason == "timed_out"
        assert restored.wall_deadline_at == 7_100.0
    finally:
        await manager._registry.close()
