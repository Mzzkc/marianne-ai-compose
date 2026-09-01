"""Persisted score wall-clock deadline selection and compatibility tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from marianne.core.checkpoint import CheckpointState
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.registry import JobRegistry


class MutableClock:
    """Deterministic epoch/monotonic clock shared by deadline tests."""

    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _manager(tmp_path: Path, clock: MutableClock, daemon_limit: float) -> JobManager:
    config = DaemonConfig(
        state_db_path=tmp_path / "jobs.db",
        observer={"enabled": False},
    ).model_copy(update={"job_timeout_seconds": daemon_limit})
    return JobManager(config, wall_clock=clock, monotonic_clock=clock)


def _queued_meta(
    *,
    job_id: str,
    submitted_at: float,
    max_wall_seconds: float | None = None,
    wall_deadline_at: float | None = None,
) -> JobMeta:
    return JobMeta(
        job_id=job_id,
        config_path=Path("/tmp/score.yaml"),
        workspace=Path("/tmp/workspace"),
        submitted_at=submitted_at,
        max_wall_seconds=max_wall_seconds,
        wall_deadline_at=wall_deadline_at,
    )


async def _capture_wait_timeout(
    manager: JobManager,
    monkeypatch: pytest.MonkeyPatch,
    job_id: str,
) -> float:
    captured: list[float | None] = []

    async def fake_wait_for(coro: Any, timeout: float | None) -> Any:
        captured.append(timeout)
        return await coro

    monkeypatch.setattr("marianne.daemon.manager.asyncio.wait_for", fake_wait_for)

    async def finish() -> None:
        return None

    await manager._run_managed_task(job_id, finish())
    assert len(captured) == 1
    assert captured[0] is not None
    return captured[0]


@pytest.mark.asyncio
async def test_legacy_daemon_only_job_uses_configured_execution_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(1_000.0)
    manager = _manager(tmp_path, clock, daemon_limit=90.0)
    await manager._registry.open()
    try:
        meta = _queued_meta(job_id="legacy", submitted_at=clock.value)
        manager._job_meta[meta.job_id] = meta
        selected = await _capture_wait_timeout(manager, monkeypatch, meta.job_id)
        assert selected == 90.0
        assert meta.status is DaemonJobStatus.COMPLETED
        assert meta.wall_deadline_at is None
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_score_deadline_selects_stricter_remaining_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(2_000.0)
    manager = _manager(tmp_path, clock, daemon_limit=120.0)
    await manager._registry.open()
    try:
        meta = _queued_meta(
            job_id="score-limited",
            submitted_at=1_970.0,
            max_wall_seconds=60.0,
            wall_deadline_at=2_030.0,
        )
        manager._job_meta[meta.job_id] = meta
        selected = await _capture_wait_timeout(manager, monkeypatch, meta.job_id)
        assert selected == 30.0
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_resume_consumes_same_absolute_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(3_010.0)
    manager = _manager(tmp_path, clock, daemon_limit=120.0)
    await manager._registry.open()
    try:
        meta = _queued_meta(
            job_id="resumed",
            submitted_at=3_000.0,
            max_wall_seconds=60.0,
            wall_deadline_at=3_060.0,
        )
        manager._job_meta[meta.job_id] = meta
        first = await _capture_wait_timeout(manager, monkeypatch, meta.job_id)

        clock.value = 3_045.0
        meta.status = DaemonJobStatus.QUEUED
        second = await _capture_wait_timeout(manager, monkeypatch, meta.job_id)

        assert first == 50.0
        assert second == 15.0
        assert meta.wall_deadline_at == 3_060.0
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_expired_score_fails_before_execution_and_persists_reason(
    tmp_path: Path,
) -> None:
    clock = MutableClock(4_060.0)
    manager = _manager(tmp_path, clock, daemon_limit=120.0)
    await manager._registry.open()
    executed = False
    try:
        await manager._registry.register_job(
            "expired",
            Path("/tmp/score.yaml"),
            Path("/tmp/workspace"),
            submitted_at=4_000.0,
            max_wall_seconds=60.0,
        )
        meta = _queued_meta(
            job_id="expired",
            submitted_at=4_000.0,
            max_wall_seconds=60.0,
            wall_deadline_at=4_060.0,
        )
        manager._job_meta[meta.job_id] = meta

        async def execution() -> None:
            nonlocal executed
            executed = True

        await manager._run_managed_task(meta.job_id, execution())

        record = await manager._registry.get_job(meta.job_id)
        assert executed is False
        assert meta.status is DaemonJobStatus.FAILED
        assert meta.terminal_reason == "timed_out"
        assert record is not None
        assert record.status is DaemonJobStatus.FAILED
        assert record.terminal_reason == "timed_out"
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_registry_migrates_legacy_rows_with_nullable_deadline_fields(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                config_path TEXT NOT NULL,
                workspace TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                pid INTEGER,
                submitted_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                error_message TEXT,
                current_sheet INTEGER,
                total_sheets INTEGER,
                last_event_at REAL,
                log_path TEXT,
                snapshot_path TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO jobs (job_id, config_path, workspace, submitted_at) "
            "VALUES ('legacy', '/tmp/score.yaml', '/tmp/workspace', 10.0)"
        )

    registry = JobRegistry(db_path)
    await registry.open()
    try:
        record = await registry.get_job("legacy")
        columns = {
            row[1]
            for row in await (await registry._db.execute("PRAGMA table_info(jobs)")).fetchall()
        }
        assert {"max_wall_seconds", "wall_deadline_at", "terminal_reason"} <= columns
        assert record is not None
        assert record.max_wall_seconds is None
        assert record.wall_deadline_at is None
        assert record.terminal_reason is None
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_registry_close_open_preserves_one_absolute_deadline(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "jobs.db"
    registry = JobRegistry(db_path)
    await registry.open()
    await registry.register_job(
        "durable",
        Path("/tmp/score.yaml"),
        Path("/tmp/workspace"),
        submitted_at=5_000.0,
        max_wall_seconds=180.0,
    )
    await registry.close()

    reopened = JobRegistry(db_path)
    await reopened.open()
    try:
        record = await reopened.get_job("durable")
        assert record is not None
        assert record.submitted_at == 5_000.0
        assert record.max_wall_seconds == 180.0
        assert record.wall_deadline_at == 5_180.0
    finally:
        await reopened.close()


def test_checkpoint_round_trip_carries_deadline_authority() -> None:
    checkpoint = CheckpointState(
        job_id="deadline-checkpoint",
        job_name="deadline-checkpoint",
        total_sheets=1,
        max_wall_seconds=240.0,
        wall_deadline_at=8_240.0,
        terminal_reason="timed_out",
    )

    restored = CheckpointState.model_validate_json(checkpoint.model_dump_json())
    assert restored.max_wall_seconds == 240.0
    assert restored.wall_deadline_at == 8_240.0
    assert restored.terminal_reason == "timed_out"
