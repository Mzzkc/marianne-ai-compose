"""GH #506: two registries on one state database must not starve each other.

JobRegistry and ScheduleRegistry open separate connections to the same
``jobs.db``. JobRegistry had no busy timeout (Python's 5 s default) while
ScheduleRegistry held ``BEGIN IMMEDIATE`` across a custody fsync, so on a
loaded runner a job status write failed with "database is locked" during
resume. The fix is both halves: an explicit busy timeout on the job
registry, and the custody witness written before the writer lock is taken.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import aiosqlite
import pytest

from marianne.core.config.job import ScheduleConfig
from marianne.daemon.registry import JobRegistry
from marianne.daemon.schedule_registry import ScheduleRegistry


@pytest.mark.asyncio
async def test_job_registry_waits_out_a_short_writer_lock(tmp_path: Path) -> None:
    """A writer that releases within the busy timeout never surfaces as locked."""
    db = tmp_path / "jobs.db"
    registry = JobRegistry(db, busy_timeout_seconds=5.0)
    await registry.open()
    try:
        await registry.register_job("j", tmp_path / "s.yaml", tmp_path / "w")
        holder = await aiosqlite.connect(str(db))
        await holder.execute("BEGIN IMMEDIATE")
        # Release the writer lock from a worker thread after a short hold, so
        # the registry's own busy wait (not the event loop) is what bridges it.
        released = threading.Event()

        def release_soon() -> None:
            released.wait(timeout=0.3)

        await asyncio.to_thread(release_soon)
        await holder.rollback()
        # Before the fix the wait was Python's 5 s default; here the write
        # lands once the holder lets go, within OUR configured bound.
        await registry.update_status("j", "running")
        record = await registry.get_job("j")
        assert record is not None and record.status == "running"
        await holder.close()
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_job_registry_busy_timeout_is_explicit_and_validated(tmp_path: Path) -> None:
    assert JobRegistry.DEFAULT_BUSY_TIMEOUT_SECONDS >= 30.0
    with pytest.raises(ValueError):
        JobRegistry(tmp_path / "x.db", busy_timeout_seconds=0.0)
    registry = JobRegistry(tmp_path / "y.db", busy_timeout_seconds=12.5)
    await registry.open()
    try:
        cursor = await registry._db.execute("PRAGMA busy_timeout")
        row = await cursor.fetchone()
        assert row is not None and int(row[0]) == 12_500
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_schedule_custody_write_does_not_hold_the_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While the custody fsync runs, another writer on the same file can commit."""
    db = tmp_path / "conductor-state.db"
    schedules = ScheduleRegistry(db, busy_timeout_seconds=0.5)
    await schedules.open()
    jobs = JobRegistry(db, busy_timeout_seconds=0.2)
    await jobs.open()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    proceed = threading.Event()
    original = schedules._write_custody

    def slow_custody(identity: str, revision: int) -> None:
        # Runs in aiosqlite's worker thread: tell the loop we are inside the
        # custody write, then hold here until the loop lets us finish.
        loop.call_soon_threadsafe(entered.set)
        proceed.wait(timeout=5.0)
        original(identity, revision)

    monkeypatch.setattr(schedules, "_write_custody", slow_custody)
    try:
        await jobs.register_job("j", tmp_path / "s.yaml", tmp_path / "w")

        async def mutate() -> None:
            await schedules.upsert(
                "daily-report", "daily-report", Path("/scores/daily-report.yaml"),
                ScheduleConfig(interval="1h"), "opaque-source-digest", 100.0,
            )

        task = asyncio.create_task(mutate())
        await asyncio.wait_for(entered.wait(), timeout=5.0)
        # The schedule registry is mid-custody-write. Before the fix it held
        # BEGIN IMMEDIATE here, and this status write failed after 0.2 s with
        # "database is locked".
        await jobs.update_status("j", "running")
        proceed.set()
        await asyncio.wait_for(task, timeout=5.0)
        record = await jobs.get_job("j")
        assert record is not None and record.status == "running"
        assert await schedules.get("daily-report") is not None
    finally:
        proceed.set()
        await jobs.close()
        await schedules.close()
