"""Tests for registry expansion in marianne.daemon.registry.

Covers the new Phase 1 columns (current_sheet, total_sheets, last_event_at,
log_path, snapshot_path), the update_progress() method, schema migration
for older databases, and the expanded _row_to_record() mapping.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from marianne.daemon.registry import DaemonJobStatus, JobRecord, JobRegistry


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def registry(tmp_path: Path) -> AsyncIterator[JobRegistry]:
    """Create and open a JobRegistry for testing."""
    reg = JobRegistry(tmp_path / "test-expansion.db")
    await reg.open()
    yield reg
    await reg.close()


async def _register_sample_job(registry: JobRegistry, job_id: str = "test-job") -> None:
    """Helper to register a job for subsequent operations."""
    await registry.register_job(
        job_id=job_id,
        config_path=Path("/tmp/test.yaml"),
        workspace=Path("/tmp/test-workspace"),
    )


# ─── New Columns ──────────────────────────────────────────────────────


class TestNewColumns:
    """Tests for the expanded JobRecord columns."""

    @pytest.mark.asyncio
    async def test_new_columns_default_to_none(self, registry: JobRegistry):
        """Newly registered jobs have None for all expansion columns."""
        await _register_sample_job(registry)
        record = await registry.get_job("test-job")
        assert record is not None
        assert record.current_sheet is None
        assert record.total_sheets is None
        assert record.last_event_at is None
        assert record.log_path is None
        assert record.snapshot_path is None

    @pytest.mark.asyncio
    async def test_to_dict_includes_new_fields(self, registry: JobRegistry):
        """to_dict() includes the expansion fields."""
        await _register_sample_job(registry)
        record = await registry.get_job("test-job")
        assert record is not None
        d = record.to_dict()
        assert "current_sheet" in d
        assert "total_sheets" in d

    @pytest.mark.asyncio
    async def test_to_dict_conditional_fields(self, registry: JobRegistry):
        """to_dict() only includes log_path and snapshot_path when set."""
        await _register_sample_job(registry)
        record = await registry.get_job("test-job")
        assert record is not None
        d = record.to_dict()
        # These are conditionally included only when truthy
        assert "log_path" not in d
        assert "snapshot_path" not in d


# ─── update_progress ──────────────────────────────────────────────────


class TestUpdateProgress:
    """Tests for the update_progress() method."""

    @pytest.mark.asyncio
    async def test_update_progress_sets_fields(self, registry: JobRegistry):
        """update_progress() sets current_sheet and total_sheets."""
        await _register_sample_job(registry)
        await registry.update_progress("test-job", current_sheet=3, total_sheets=10)

        record = await registry.get_job("test-job")
        assert record is not None
        assert record.current_sheet == 3
        assert record.total_sheets == 10

    @pytest.mark.asyncio
    async def test_update_progress_sets_last_event_at(self, registry: JobRegistry):
        """update_progress() timestamps the update."""
        await _register_sample_job(registry)
        before = time.time()
        await registry.update_progress("test-job", current_sheet=1, total_sheets=5)
        after = time.time()

        record = await registry.get_job("test-job")
        assert record is not None
        assert record.last_event_at is not None
        assert before <= record.last_event_at <= after

    @pytest.mark.asyncio
    async def test_update_progress_multiple_times(self, registry: JobRegistry):
        """Calling update_progress() multiple times updates the values."""
        await _register_sample_job(registry)
        await registry.update_progress("test-job", current_sheet=1, total_sheets=10)
        await registry.update_progress("test-job", current_sheet=5, total_sheets=10)
        await registry.update_progress("test-job", current_sheet=10, total_sheets=10)

        record = await registry.get_job("test-job")
        assert record is not None
        assert record.current_sheet == 10
        assert record.total_sheets == 10


# ─── Schema Migration ────────────────────────────────────────────────


class TestSchemaMigration:
    """Tests for _migrate_schema() adding columns to older databases."""

    @pytest.mark.asyncio
    async def test_migration_on_old_schema(self, tmp_path: Path):
        """Opening a registry on a DB with old schema adds new columns."""
        import aiosqlite

        db_path = tmp_path / "old-schema.db"

        # Create a DB with the original minimal schema (no expansion columns)
        async with aiosqlite.connect(str(db_path)) as conn:
            await conn.execute("""
                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    config_path TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    pid INTEGER,
                    submitted_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL,
                    error_message TEXT
                )
            """)
            # Insert a row with the old schema
            await conn.execute(
                "INSERT INTO jobs (job_id, config_path, workspace, submitted_at) "
                "VALUES (?, ?, ?, ?)",
                ("old-job", "/tmp/old.yaml", "/tmp/old-ws", time.time()),
            )
            await conn.commit()

        # Now open via JobRegistry — migration should add columns
        reg = JobRegistry(db_path)
        await reg.open()
        try:
            record = await reg.get_job("old-job")
            assert record is not None
            assert record.current_sheet is None
            assert record.total_sheets is None
            assert record.last_event_at is None
            assert record.log_path is None
            assert record.snapshot_path is None

            # update_progress should work on the migrated schema
            await reg.update_progress("old-job", current_sheet=2, total_sheets=8)
            record = await reg.get_job("old-job")
            assert record is not None
            assert record.current_sheet == 2
        finally:
            await reg.close()

    @pytest.mark.asyncio
    async def test_migration_idempotent(self, tmp_path: Path):
        """Running migration on an already-migrated DB does not fail."""
        db_path = tmp_path / "idempotent.db"

        # Open twice to trigger migration both times
        reg1 = JobRegistry(db_path)
        await reg1.open()
        await reg1.close()

        reg2 = JobRegistry(db_path)
        await reg2.open()  # Should not raise
        await reg2.close()


# ─── list_jobs with expansion fields ─────────────────────────────────


class TestListJobsExpanded:
    """Tests for list_jobs() returning expanded records."""

    @pytest.mark.asyncio
    async def test_list_jobs_returns_progress(self, registry: JobRegistry):
        """list_jobs() includes progress fields in returned records."""
        await _register_sample_job(registry)
        await registry.update_progress("test-job", current_sheet=7, total_sheets=12)

        jobs = await registry.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].current_sheet == 7
        assert jobs[0].total_sheets == 12
