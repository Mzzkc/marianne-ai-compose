"""Tests for marianne.daemon.registry module.

Covers the async JobRegistry: connection lifecycle, CRUD operations,
orphan recovery, job deletion, and error handling.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from marianne.core.checkpoint import (
    CheckpointState,
    JobStatus,
    SheetState,
    SheetStatus,
)
from marianne.daemon.registry import DaemonJobStatus, JobRecord, JobRegistry

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def registry(tmp_path: Path) -> AsyncIterator[JobRegistry]:
    """Create and open a JobRegistry for testing."""
    reg = JobRegistry(tmp_path / "test-registry.db")
    await reg.open()
    yield reg
    await reg.close()


# ─── Connection Lifecycle ──────────────────────────────────────────────


class TestLifecycle:
    """Tests for JobRegistry open/close lifecycle."""

    @pytest.mark.asyncio
    async def test_open_creates_db_file(self, tmp_path: Path):
        """Opening a registry creates the SQLite database file."""
        db_path = tmp_path / "new-registry.db"
        reg = JobRegistry(db_path)
        await reg.open()
        assert db_path.exists()
        await reg.close()

    @pytest.mark.asyncio
    async def test_open_creates_parent_directories(self, tmp_path: Path):
        """JobRegistry creates parent directories if missing."""
        db_path = tmp_path / "nested" / "dir" / "registry.db"
        reg = JobRegistry(db_path)
        await reg.open()
        assert db_path.parent.exists()
        await reg.close()

    @pytest.mark.asyncio
    async def test_close_idempotent(self, tmp_path: Path):
        """Closing an already-closed registry does not raise."""
        reg = JobRegistry(tmp_path / "registry.db")
        await reg.open()
        await reg.close()
        await reg.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_use_before_open_raises(self, tmp_path: Path):
        """Using the registry before open() raises RuntimeError."""
        reg = JobRegistry(tmp_path / "registry.db")
        with pytest.raises(RuntimeError, match="not opened"):
            await reg.has_active_job("test")

    @pytest.mark.asyncio
    async def test_async_context_manager(self, tmp_path: Path):
        """Registry works as an async context manager."""
        db_path = tmp_path / "ctx-registry.db"
        async with JobRegistry(db_path) as reg:
            await reg.register_job("ctx-job", Path("/tmp/c.yaml"), Path("/tmp/ws"))
            job = await reg.get_job("ctx-job")
            assert job is not None
            assert job.job_id == "ctx-job"

        # After context exit, registry should be closed
        assert reg._conn is None


# ─── Register & Get ───────────────────────────────────────────────────


class TestRegisterAndGet:
    """Tests for register_job and get_job."""

    @pytest.mark.asyncio
    async def test_register_and_get_job(self, registry: JobRegistry):
        """A registered job can be retrieved by ID."""
        await registry.register_job("job-1", Path("/tmp/config.yaml"), Path("/tmp/ws"))
        job = await registry.get_job("job-1")

        assert job is not None
        assert job.job_id == "job-1"
        assert job.config_path == "/tmp/config.yaml"
        assert job.workspace == "/tmp/ws"
        assert job.status == DaemonJobStatus.QUEUED

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, registry: JobRegistry):
        """Getting a nonexistent job returns None."""
        job = await registry.get_job("nonexistent")
        assert job is None

    @pytest.mark.asyncio
    async def test_register_replaces_existing(self, registry: JobRegistry):
        """Registering a job with the same ID replaces the old entry."""
        await registry.register_job("dup", Path("/tmp/old.yaml"), Path("/tmp/ws1"))
        await registry.register_job("dup", Path("/tmp/new.yaml"), Path("/tmp/ws2"))

        job = await registry.get_job("dup")
        assert job is not None
        assert job.config_path == "/tmp/new.yaml"
        assert job.workspace == "/tmp/ws2"


# ─── Update Status ───────────────────────────────────────────────────


class TestUpdateStatus:
    """Tests for update_status transitions."""

    @pytest.mark.asyncio
    async def test_update_to_running_sets_pid_and_started_at(self, registry: JobRegistry):
        """Transitioning to running sets PID and started_at."""
        await registry.register_job("j1", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("j1", "running", pid=12345)

        job = await registry.get_job("j1")
        assert job is not None
        assert job.status == DaemonJobStatus.RUNNING
        assert job.pid == 12345
        assert job.started_at is not None

    @pytest.mark.asyncio
    async def test_update_to_failed_sets_completed_at_and_error(self, registry: JobRegistry):
        """Transitioning to failed sets completed_at and error_message."""
        await registry.register_job("j2", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("j2", "failed", error_message="boom")

        job = await registry.get_job("j2")
        assert job is not None
        assert job.status == DaemonJobStatus.FAILED
        assert job.completed_at is not None
        assert job.error_message == "boom"

    @pytest.mark.asyncio
    async def test_update_to_completed_sets_completed_at(self, registry: JobRegistry):
        """Transitioning to completed sets completed_at."""
        await registry.register_job("j3", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("j3", "completed")

        job = await registry.get_job("j3")
        assert job is not None
        assert job.status == DaemonJobStatus.COMPLETED
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_update_pid_only(self, registry: JobRegistry):
        """Updating PID without changing status works."""
        await registry.register_job("j4", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("j4", "queued", pid=99)

        job = await registry.get_job("j4")
        assert job is not None
        assert job.pid == 99
        assert job.status == DaemonJobStatus.QUEUED


# ─── List Jobs ────────────────────────────────────────────────────────


class TestListJobs:
    """Tests for list_jobs query."""

    @pytest.mark.asyncio
    async def test_list_empty(self, registry: JobRegistry):
        """Empty registry returns empty list."""
        jobs = await registry.list_jobs()
        assert jobs == []

    @pytest.mark.asyncio
    async def test_list_returns_all_jobs(self, registry: JobRegistry):
        """List returns all registered jobs."""
        for i in range(5):
            await registry.register_job(f"j-{i}", Path(f"/tmp/{i}.yaml"), Path(f"/tmp/ws{i}"))
        jobs = await registry.list_jobs()
        assert len(jobs) == 5

    @pytest.mark.asyncio
    async def test_list_with_status_filter(self, registry: JobRegistry):
        """List with status filter returns only matching jobs."""
        await registry.register_job("j-q", Path("/tmp/q.yaml"), Path("/tmp/ws"))
        await registry.register_job("j-r", Path("/tmp/r.yaml"), Path("/tmp/ws"))
        await registry.update_status("j-r", "running", pid=1)

        queued = await registry.list_jobs(status="queued")
        assert len(queued) == 1
        assert queued[0].job_id == "j-q"

    @pytest.mark.asyncio
    async def test_list_respects_limit(self, registry: JobRegistry):
        """List respects the limit parameter."""
        for i in range(10):
            await registry.register_job(f"j-{i}", Path(f"/tmp/{i}.yaml"), Path(f"/tmp/ws{i}"))
        jobs = await registry.list_jobs(limit=3)
        assert len(jobs) == 3

    @pytest.mark.asyncio
    async def test_list_orders_by_submitted_desc(self, registry: JobRegistry):
        """List returns jobs ordered by submitted_at descending."""
        for i in range(3):
            await registry.register_job(f"j-{i}", Path(f"/tmp/{i}.yaml"), Path(f"/tmp/ws{i}"))
        jobs = await registry.list_jobs()
        # Most recently submitted should be first
        assert jobs[0].job_id == "j-2"


# ─── Has Active Job ──────────────────────────────────────────────────


class TestHasActiveJob:
    """Tests for has_active_job."""

    @pytest.mark.asyncio
    async def test_queued_is_active(self, registry: JobRegistry):
        """A queued job is considered active."""
        await registry.register_job("j", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        assert await registry.has_active_job("j") is True

    @pytest.mark.asyncio
    async def test_running_is_active(self, registry: JobRegistry):
        """A running job is considered active."""
        await registry.register_job("j", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("j", "running", pid=1)
        assert await registry.has_active_job("j") is True

    @pytest.mark.asyncio
    async def test_completed_is_not_active(self, registry: JobRegistry):
        """A completed job is not considered active."""
        await registry.register_job("j", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("j", "completed")
        assert await registry.has_active_job("j") is False

    @pytest.mark.asyncio
    async def test_nonexistent_is_not_active(self, registry: JobRegistry):
        """A nonexistent job is not considered active."""
        assert await registry.has_active_job("nope") is False


# ─── Orphan Recovery ─────────────────────────────────────────────────


class TestOrphanRecovery:
    """Tests for orphan detection and recovery."""

    @pytest.mark.asyncio
    async def test_get_orphaned_jobs(self, registry: JobRegistry):
        """get_orphaned_jobs returns queued and running jobs."""
        await registry.register_job("q", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.register_job("r", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("r", "running", pid=1)
        await registry.register_job("c", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("c", "completed")

        orphans = await registry.get_orphaned_jobs()
        ids = {o.job_id for o in orphans}
        assert ids == {"q", "r"}

    @pytest.mark.asyncio
    async def test_mark_orphans_failed(self, registry: JobRegistry):
        """mark_orphans_failed transitions orphans to failed."""
        await registry.register_job("o1", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.register_job("o2", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("o2", "running", pid=1)

        count = await registry.mark_orphans_failed()
        assert count == 2

        j1 = await registry.get_job("o1")
        j2 = await registry.get_job("o2")
        assert j1 is not None and j1.status == DaemonJobStatus.FAILED
        assert j2 is not None and j2.status == DaemonJobStatus.FAILED
        assert "Daemon restarted" in (j1.error_message or "")

    @pytest.mark.asyncio
    async def test_mark_orphans_returns_zero_when_none(self, registry: JobRegistry):
        """mark_orphans_failed returns 0 when no orphans exist."""
        count = await registry.mark_orphans_failed()
        assert count == 0


# ─── Delete Jobs ──────────────────────────────────────────────────────


class TestDeleteJobs:
    """Tests for delete_jobs cleanup."""

    @pytest.mark.asyncio
    async def test_delete_terminal_jobs(self, registry: JobRegistry):
        """delete_jobs removes completed/failed/cancelled jobs."""
        await registry.register_job("c", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("c", "completed")
        await registry.register_job("f", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("f", "failed", error_message="err")
        await registry.register_job("q", Path("/tmp/c.yaml"), Path("/tmp/ws"))

        count = await registry.delete_jobs()
        assert count == 2
        # Queued job should survive
        assert await registry.get_job("q") is not None
        assert await registry.get_job("c") is None

    @pytest.mark.asyncio
    async def test_delete_never_removes_active_jobs(self, registry: JobRegistry):
        """delete_jobs never removes queued or running jobs, even if requested."""
        await registry.register_job("q", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.register_job("r", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("r", "running", pid=1)

        # Explicitly request deletion of queued and running — should be ignored
        count = await registry.delete_jobs(statuses=["queued", "running"])
        assert count == 0
        assert await registry.get_job("q") is not None
        assert await registry.get_job("r") is not None

    @pytest.mark.asyncio
    async def test_delete_with_status_filter(self, registry: JobRegistry):
        """delete_jobs respects status filter."""
        await registry.register_job("c", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("c", "completed")
        await registry.register_job("f", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("f", "failed", error_message="err")

        count = await registry.delete_jobs(statuses=["failed"])
        assert count == 1
        # Completed should survive
        assert await registry.get_job("c") is not None
        assert await registry.get_job("f") is None

    @pytest.mark.asyncio
    async def test_delete_with_age_filter(self, registry: JobRegistry):
        """delete_jobs respects older_than_seconds filter."""
        await registry.register_job("new", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        await registry.update_status("new", "completed")

        # Anything older than 0 seconds would include all jobs,
        # but "older than 1000000" should exclude recent jobs
        count = await registry.delete_jobs(older_than_seconds=1_000_000)
        assert count == 0


# ─── JobRecord ────────────────────────────────────────────────────────


class TestJobRecord:
    """Tests for JobRecord serialization."""

    def test_to_dict(self):
        """JobRecord.to_dict produces expected output."""
        record = JobRecord(
            job_id="test",
            config_path="/tmp/config.yaml",
            workspace="/tmp/ws",
            status=DaemonJobStatus.RUNNING,
            pid=123,
            submitted_at=1000.0,
            started_at=1001.0,
        )
        d = record.to_dict()
        assert d["job_id"] == "test"
        assert d["pid"] == 123
        assert "error_message" not in d  # None → omitted

    def test_to_dict_with_error(self):
        """JobRecord.to_dict includes error_message when present."""
        record = JobRecord(
            job_id="test",
            config_path="/tmp/config.yaml",
            workspace="/tmp/ws",
            status=DaemonJobStatus.FAILED,
            error_message="something broke",
        )
        d = record.to_dict()
        assert d["error_message"] == "something broke"


# ─── Persistence Across Reopen ────────────────────────────────────────


class TestPersistence:
    """Tests that data persists across close/reopen."""

    @pytest.mark.asyncio
    async def test_data_survives_reopen(self, tmp_path: Path):
        """Registered jobs survive closing and reopening the registry."""
        db_path = tmp_path / "persist.db"

        async with JobRegistry(db_path) as reg:
            await reg.register_job("persist-me", Path("/tmp/c.yaml"), Path("/tmp/ws"))
            await reg.update_status("persist-me", "completed")

        # Reopen with a new instance
        async with JobRegistry(db_path) as reg:
            job = await reg.get_job("persist-me")
            assert job is not None
            assert job.status == DaemonJobStatus.COMPLETED


# ─── Hook Config Storage ────────────────────────────────────────────


class TestHookConfigStorage:
    """Tests for hook config and results storage in the registry."""

    @pytest.mark.asyncio
    async def test_store_and_get_hook_config(self, registry: JobRegistry):
        """Hook config roundtrips through store/get."""
        await registry.register_job("hook-job", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        hook_json = '[{"type": "run_job", "job_path": "next.yaml"}]'
        await registry.store_hook_config("hook-job", hook_json)

        result = await registry.get_hook_config("hook-job")
        assert result == hook_json

    @pytest.mark.asyncio
    async def test_get_hook_config_returns_none_when_unset(self, registry: JobRegistry):
        """get_hook_config returns None when no config was stored."""
        await registry.register_job("no-hooks", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        result = await registry.get_hook_config("no-hooks")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_hook_config_returns_none_for_unknown_job(self, registry: JobRegistry):
        """get_hook_config returns None for nonexistent job."""
        result = await registry.get_hook_config("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_store_hook_results(self, registry: JobRegistry):
        """Hook results can be stored and are persisted."""
        await registry.register_job("results-job", Path("/tmp/c.yaml"), Path("/tmp/ws"))
        results_json = '[{"hook": "run_job", "success": true}]'
        await registry.store_hook_results("results-job", results_json)

        # Verify via raw SQL (no dedicated get_hook_results method yet)
        cursor = await registry._db.execute(
            "SELECT hook_results_json FROM jobs WHERE job_id = ?",
            ("results-job",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["hook_results_json"] == results_json

    @pytest.mark.asyncio
    async def test_migration_adds_hook_columns(self, tmp_path: Path):
        """Migration adds hook columns to an existing database."""
        db_path = tmp_path / "migrate-hooks.db"

        # Create a registry (creates tables with columns)
        async with JobRegistry(db_path) as reg:
            await reg.register_job("old-job", Path("/tmp/c.yaml"), Path("/tmp/ws"))

        # Reopen — migration should succeed idempotently
        async with JobRegistry(db_path) as reg:
            # Store hook config on old job — column must exist
            await reg.store_hook_config("old-job", "[]")
            result = await reg.get_hook_config("old-job")
            assert result == "[]"


# ─── Checkpoint Blob Round-Trip (Invariant pin for #223) ──────────────


class TestCheckpointRoundTrip:
    """The daemon resume path persists the WHOLE ``CheckpointState`` as a JSON
    blob in the registry's ``checkpoint_json`` column and reconstructs it on
    resume — it does NOT use the column-mapped ``SQLiteStateBackend``.

    These tests pin the invariant behind #223: baton-scheduling, cost-tracking,
    and worktree fields survive a daemon ``save_checkpoint`` → ``load_checkpoint``
    round-trip. #223 ("100+ fields silently lost on restart") was filed against
    the lossy ``SQLiteStateBackend`` column projection, but that backend is not
    on the daemon resume path; the blob round-trip below is. If this test ever
    fails, the daemon resume path has genuinely become lossy.
    """

    @pytest.mark.asyncio
    async def test_save_load_preserves_baton_cost_worktree_fields(
        self, registry: JobRegistry
    ) -> None:
        """A full CheckpointState round-trips through the registry blob lossless
        for every persisted baton/cost/worktree field.
        """
        await registry.register_job("rt", Path("/tmp/c.yaml"), Path("/tmp/ws"))

        sheet = SheetState(
            sheet_num=1,
            status=SheetStatus.IN_PROGRESS,
            # Baton scheduling (persisted)
            normal_attempts=3,
            max_retries=6,
            max_completion=4,
            completion_attempts=1,
            healing_attempts=2,
            total_cost_usd=7.89,
            total_duration_seconds=123.4,
            model="glm-5.1",
            fallback_chain=["claude", "goose", "gemini"],
            current_instrument_index=2,
            fallback_attempts={"claude": 2, "goose": 1},
            sheet_timeout_seconds=300.0,
            instrument_fallback_history=[
                {"from": "claude", "to": "goose", "reason": "timeout"}
            ],
            # Cost tracking (persisted)
            input_tokens=15000,
            output_tokens=8000,
            estimated_cost=0.045,
            cost_confidence=0.9,
        )
        state = CheckpointState(
            job_id="rt",
            job_name="round-trip",
            total_sheets=1,
            status=JobStatus.RUNNING,
            sheets={1: sheet},
            # Aggregate cost + worktree (persisted on CheckpointState)
            total_estimated_cost=5.67,
            total_input_tokens=15000,
            total_output_tokens=8000,
            instruments_used=["claude", "goose"],
            worktree_path="/tmp/worktree-rt",
            worktree_branch="sheet-1-attempt-3",
            worktree_locked=True,
            worktree_base_commit="abc123def",
            circuit_breaker_history=[{"tripped_at": "2026-05-30"}],
        )

        await registry.save_checkpoint("rt", state.model_dump_json())
        blob = await registry.load_checkpoint("rt")
        assert blob is not None
        restored = CheckpointState.model_validate(json.loads(blob))

        s = restored.sheets[1]
        # Baton scheduling
        assert s.normal_attempts == 3
        assert s.max_retries == 6
        assert s.max_completion == 4
        assert s.completion_attempts == 1
        assert s.healing_attempts == 2
        assert s.total_cost_usd == pytest.approx(7.89)
        assert s.total_duration_seconds == pytest.approx(123.4)
        assert s.model == "glm-5.1"
        assert s.fallback_chain == ["claude", "goose", "gemini"]
        assert s.current_instrument_index == 2
        assert s.fallback_attempts == {"claude": 2, "goose": 1}
        assert s.sheet_timeout_seconds == pytest.approx(300.0)
        assert s.instrument_fallback_history == [
            {"from": "claude", "to": "goose", "reason": "timeout"}
        ]
        # Cost tracking
        assert s.input_tokens == 15000
        assert s.output_tokens == 8000
        assert s.estimated_cost == pytest.approx(0.045)
        assert s.cost_confidence == pytest.approx(0.9)
        # Aggregate + worktree
        assert restored.total_estimated_cost == pytest.approx(5.67)
        assert restored.total_input_tokens == 15000
        assert restored.total_output_tokens == 8000
        assert restored.instruments_used == ["claude", "goose"]
        assert restored.worktree_path == "/tmp/worktree-rt"
        assert restored.worktree_branch == "sheet-1-attempt-3"
        assert restored.worktree_locked is True
        assert restored.worktree_base_commit == "abc123def"
        assert restored.circuit_breaker_history == [{"tripped_at": "2026-05-30"}]

    @pytest.mark.asyncio
    async def test_transient_fields_are_intentionally_dropped(
        self, registry: JobRegistry
    ) -> None:
        """Fields marked ``exclude=True`` (timers, in-flight attempt results) are
        intentionally NOT persisted — they reset to defaults on restart by design.

        This documents the one place the round-trip is deliberately lossy so a
        future reader does not mistake it for the #223 bug.
        """
        await registry.register_job("tr", Path("/tmp/c.yaml"), Path("/tmp/ws"))

        sheet = SheetState(
            sheet_num=1,
            status=SheetStatus.IN_PROGRESS,
            next_retry_at=999.0,
            dispatched_at=888.0,
            attempt_results=[{"attempt": 1}],
        )
        state = CheckpointState(
            job_id="tr",
            job_name="transient",
            total_sheets=1,
            status=JobStatus.RUNNING,
            sheets={1: sheet},
        )

        await registry.save_checkpoint("tr", state.model_dump_json())
        blob = await registry.load_checkpoint("tr")
        assert blob is not None
        restored = CheckpointState.model_validate(json.loads(blob))

        s = restored.sheets[1]
        assert s.next_retry_at is None
        assert s.dispatched_at is None
        assert s.attempt_results == []


# ─── Status grouping coherence (#268) ─────────────────────────────────


class TestStatusGroupings:
    """The terminal/active status sets are derived from DaemonJobStatus (#268)
    so a value rename can't leave a stale string literal behind. Pin the
    semantic grouping so a future edit can't silently misclassify a status.
    """

    def test_groupings_are_valid_enum_values(self) -> None:
        from marianne.daemon.registry import (
            _ACTIVE_STATUSES,
            _TERMINAL_STATUSES,
            DaemonJobStatus,
        )

        valid = {s.value for s in DaemonJobStatus}
        assert valid >= _TERMINAL_STATUSES
        assert valid >= _ACTIVE_STATUSES

    def test_terminal_and_active_are_disjoint(self) -> None:
        from marianne.daemon.registry import _ACTIVE_STATUSES, _TERMINAL_STATUSES

        assert _TERMINAL_STATUSES.isdisjoint(_ACTIVE_STATUSES)

    def test_expected_membership(self) -> None:
        from marianne.daemon.registry import (
            _ACTIVE_STATUSES,
            _TERMINAL_STATUSES,
            DaemonJobStatus,
        )

        assert {
            DaemonJobStatus.COMPLETED.value,
            DaemonJobStatus.FAILED.value,
            DaemonJobStatus.CANCELLED.value,
        } == _TERMINAL_STATUSES
        assert {
            DaemonJobStatus.QUEUED.value,
            DaemonJobStatus.RUNNING.value,
        } == _ACTIVE_STATUSES
        # PAUSED / PAUSED_AT_CHAIN / PENDING are deliberately in NEITHER set.
        for st in (
            DaemonJobStatus.PAUSED,
            DaemonJobStatus.PAUSED_AT_CHAIN,
            DaemonJobStatus.PENDING,
        ):
            assert st.value not in _TERMINAL_STATUSES
            assert st.value not in _ACTIVE_STATUSES
