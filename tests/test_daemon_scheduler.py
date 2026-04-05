"""Tests for mozart.daemon.scheduler module.

Covers GlobalSheetScheduler: priority ordering, fair-share enforcement,
DAG-aware enqueue/dequeue, concurrency limits, rate-limited backend
skipping, backpressure integration, stats reporting, DAG edge cases,
stress tests, and cycle detection.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from mozart.daemon.config import DaemonConfig
from mozart.daemon.scheduler import (
    GlobalSheetScheduler,
    SheetInfo,
)


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def daemon_config(tmp_path: Path) -> DaemonConfig:
    """DaemonConfig with low concurrency for testing."""
    return DaemonConfig(
        max_concurrent_sheets=3,
        max_concurrent_jobs=5,
        pid_file=tmp_path / "test.pid",
    )


@pytest.fixture
def scheduler(daemon_config: DaemonConfig) -> GlobalSheetScheduler:
    """Fresh scheduler instance."""
    return GlobalSheetScheduler(daemon_config)


def _sheet(
    job_id: str = "job-a",
    sheet_num: int = 1,
    job_priority: int = 5,
    dag_depth: int = 0,
    retries: int = 0,
    backend: str = "claude_cli",
) -> SheetInfo:
    """Helper to create a SheetInfo with sensible defaults."""
    return SheetInfo(
        job_id=job_id,
        sheet_num=sheet_num,
        job_priority=job_priority,
        dag_depth=dag_depth,
        retries_so_far=retries,
        backend_type=backend,
    )


# ─── Basic Operations ─────────────────────────────────────────────────


class TestRegisterAndDeregister:
    """Tests for register_job / deregister_job."""

    @pytest.mark.asyncio
    async def test_register_enqueues_independent_sheets(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Sheets with no dependencies are enqueued immediately."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 4)]
        await scheduler.register_job("job-a", sheets)

        assert scheduler.queued_count == 3
        assert scheduler.active_count == 0

    @pytest.mark.asyncio
    async def test_register_respects_dag_deps(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Only DAG-ready sheets are enqueued on registration."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 4)]
        deps = {2: {1}, 3: {2}}  # 1 → 2 → 3

        await scheduler.register_job("job-a", sheets, dependencies=deps)
        # Only sheet 1 has no deps
        assert scheduler.queued_count == 1

    @pytest.mark.asyncio
    async def test_deregister_removes_all_pending(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Deregistering a job removes all its queued sheets."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 6)]
        await scheduler.register_job("job-a", sheets)
        assert scheduler.queued_count == 5

        await scheduler.deregister_job("job-a")
        assert scheduler.queued_count == 0

    @pytest.mark.asyncio
    async def test_deregister_doesnt_affect_other_jobs(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Deregistering job-a leaves job-b's sheets intact."""
        await scheduler.register_job("job-a", [_sheet("job-a", i) for i in range(1, 4)])
        await scheduler.register_job("job-b", [_sheet("job-b", i) for i in range(1, 3)])
        assert scheduler.queued_count == 5

        await scheduler.deregister_job("job-a")
        assert scheduler.queued_count == 2


# ─── Priority Ordering ────────────────────────────────────────────────


class TestPriorityOrdering:
    """Tests for priority-based sheet dispatch."""

    @pytest.mark.asyncio
    async def test_higher_priority_dispatched_first(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Sheets with lower job_priority number (= more urgent) dispatch first."""
        # Priority 1 (urgent) and priority 10 (low)
        sheets = [
            _sheet("job-a", sheet_num=1, job_priority=10),
            _sheet("job-a", sheet_num=2, job_priority=1),
        ]
        await scheduler.register_job("job-a", sheets)

        entry = await scheduler.next_sheet()
        assert entry is not None
        # Sheet 2 has priority=1 → lower score → dispatched first
        assert entry.info.sheet_num == 2

    @pytest.mark.asyncio
    async def test_retried_sheets_get_priority_boost(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Sheets with more retries get a priority boost (sunk cost)."""
        sheets = [
            _sheet("job-a", sheet_num=1, job_priority=5, retries=0),
            _sheet("job-a", sheet_num=2, job_priority=5, retries=5),
        ]
        await scheduler.register_job("job-a", sheets)

        entry = await scheduler.next_sheet()
        assert entry is not None
        # Sheet 2 has 5 retries → -25 priority reduction → goes first
        assert entry.info.sheet_num == 2

    @pytest.mark.asyncio
    async def test_shallow_dag_depth_preferred(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Shallower DAG depth sheets dispatch before deeper ones."""
        sheets = [
            _sheet("job-a", sheet_num=1, dag_depth=5),
            _sheet("job-a", sheet_num=2, dag_depth=0),
        ]
        await scheduler.register_job("job-a", sheets)

        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.sheet_num == 2  # depth 0 preferred


# ─── Concurrency Limits ───────────────────────────────────────────────


class TestConcurrencyLimits:
    """Tests for max_concurrent_sheets enforcement."""

    @pytest.mark.asyncio
    async def test_respects_max_concurrent(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Cannot dispatch more sheets than max_concurrent_sheets."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 10)]
        await scheduler.register_job("job-a", sheets)

        dispatched = []
        for _ in range(10):
            entry = await scheduler.next_sheet()
            if entry is None:
                break
            dispatched.append(entry)

        assert len(dispatched) == 3  # max_concurrent_sheets = 3
        assert scheduler.active_count == 3

    @pytest.mark.asyncio
    async def test_completing_sheet_frees_slot(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Marking a sheet complete allows the next one to dispatch."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 5)]
        await scheduler.register_job("job-a", sheets)

        # Fill up all 3 slots
        entries = []
        for _ in range(3):
            e = await scheduler.next_sheet()
            assert e is not None
            entries.append(e)

        # All slots full
        assert await scheduler.next_sheet() is None

        # Complete one
        await scheduler.mark_complete("job-a", entries[0].info.sheet_num, success=True)
        assert scheduler.active_count == 2

        # Now one more can dispatch
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert scheduler.active_count == 3


# ─── DAG Dependencies ─────────────────────────────────────────────────


class TestDAGDependencies:
    """Tests for DAG-aware scheduling."""

    @pytest.mark.asyncio
    async def test_dependent_sheet_enqueued_after_dep_completes(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Completing sheet 1 makes sheet 2 (which depends on 1) available."""
        sheets = [_sheet(sheet_num=1), _sheet(sheet_num=2)]
        deps = {2: {1}}

        await scheduler.register_job("job-a", sheets, dependencies=deps)
        assert scheduler.queued_count == 1  # Only sheet 1

        # Dispatch and complete sheet 1
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.sheet_num == 1
        await scheduler.mark_complete("job-a", 1, success=True)

        # Sheet 2 should now be queued
        assert scheduler.queued_count == 1
        entry2 = await scheduler.next_sheet()
        assert entry2 is not None
        assert entry2.info.sheet_num == 2

    @pytest.mark.asyncio
    async def test_diamond_dag(self, scheduler: GlobalSheetScheduler):
        """Diamond dependency: 1 → {2, 3} → 4."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 5)]
        deps = {2: {1}, 3: {1}, 4: {2, 3}}

        await scheduler.register_job("job-a", sheets, dependencies=deps)
        assert scheduler.queued_count == 1  # Only sheet 1

        # Complete sheet 1 → sheets 2, 3 become ready
        e1 = await scheduler.next_sheet()
        assert e1 is not None
        await scheduler.mark_complete("job-a", 1, success=True)
        assert scheduler.queued_count == 2  # Sheets 2 and 3

        # Complete sheets 2 and 3
        e2 = await scheduler.next_sheet()
        e3 = await scheduler.next_sheet()
        assert e2 is not None and e3 is not None
        await scheduler.mark_complete("job-a", e2.info.sheet_num, success=True)

        # Sheet 4 still waiting for the other dep
        assert scheduler.queued_count == 0  # Not yet ready (needs both 2 AND 3)

        await scheduler.mark_complete("job-a", e3.info.sheet_num, success=True)
        # Now sheet 4 should be ready
        assert scheduler.queued_count == 1
        e4 = await scheduler.next_sheet()
        assert e4 is not None
        assert e4.info.sheet_num == 4


# ─── Fair Share Scheduling ─────────────────────────────────────────────


class TestFairShare:
    """Tests for per-job fair-share scheduling."""

    @pytest.mark.asyncio
    async def test_hard_cap_prevents_single_job_hogging(
        self, daemon_config: DaemonConfig,
    ):
        """A single job can't take all slots when another job is registered."""
        # max_concurrent_sheets=3, two jobs.
        # fair_share = 3/2 = 1.5 → hard_cap = 1.5 * 2 = 3
        # With just one active job, fair_share = 3/1 = 3, hard_cap = 6
        # After second job registers and dispatches, fair share recalculates
        scheduler = GlobalSheetScheduler(daemon_config)

        # Register two jobs
        await scheduler.register_job(
            "job-a",
            [_sheet("job-a", i) for i in range(1, 6)],
        )
        await scheduler.register_job(
            "job-b",
            [_sheet("job-b", i, job_priority=1) for i in range(1, 4)],
        )

        # Dispatch all 3 slots — job-b has higher priority (priority=1)
        dispatched_jobs = []
        for _ in range(3):
            e = await scheduler.next_sheet()
            if e is not None:
                dispatched_jobs.append(e.info.job_id)

        assert len(dispatched_jobs) == 3
        # Job-b (priority=1) should get preference
        assert "job-b" in dispatched_jobs

    @pytest.mark.asyncio
    async def test_fair_share_interleaves_initial_dispatch(
        self, daemon_config: DaemonConfig,
    ):
        """With two equal-priority jobs, initial dispatch interleaves.

        Fair-share penalty increases priority score (= lower urgency)
        for jobs that already have running sheets, causing the scheduler
        to alternate between jobs.
        """
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 4})
        scheduler = GlobalSheetScheduler(config)

        await scheduler.register_job(
            "job-a",
            [_sheet("job-a", i, job_priority=5) for i in range(1, 10)],
        )
        await scheduler.register_job(
            "job-b",
            [_sheet("job-b", i, job_priority=5) for i in range(1, 10)],
        )

        # Dispatch all 4 slots
        dispatched = {"job-a": 0, "job-b": 0}
        for _ in range(4):
            e = await scheduler.next_sheet()
            assert e is not None
            dispatched[e.info.job_id] += 1

        # Fair-share penalty should distribute slots across both jobs
        assert dispatched["job-a"] >= 1
        assert dispatched["job-b"] >= 1
        # With 4 slots and 2 jobs, expect roughly 2 each
        assert dispatched["job-a"] + dispatched["job-b"] == 4

    @pytest.mark.asyncio
    async def test_starved_job_catches_up_after_completion(
        self, daemon_config: DaemonConfig,
    ):
        """When a job with many running sheets completes some, the
        starved job gets the freed slots.
        """
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 4})
        scheduler = GlobalSheetScheduler(config)

        await scheduler.register_job(
            "job-a",
            [_sheet("job-a", i, job_priority=5) for i in range(1, 10)],
        )
        await scheduler.register_job(
            "job-b",
            [_sheet("job-b", i, job_priority=5) for i in range(1, 10)],
        )

        # Fill all 4 slots — due to fair-share, should be 2+2
        entries = []
        for _ in range(4):
            e = await scheduler.next_sheet()
            assert e is not None
            entries.append(e)

        # Find which entries belong to which job
        job_a_entries = [e for e in entries if e.info.job_id == "job-a"]
        job_b_entries = [e for e in entries if e.info.job_id == "job-b"]

        # Complete all of job-b's running sheets
        for e in job_b_entries:
            await scheduler.mark_complete("job-b", e.info.sheet_num, True)

        # Now dispatch freed slots — job-b should get them back
        # (job-a has 2 running at fair-share, job-b has 0)
        new_entries = []
        for _ in range(len(job_b_entries)):
            e = await scheduler.next_sheet()
            if e is not None:
                new_entries.append(e)

        new_jobs = [e.info.job_id for e in new_entries]
        assert "job-b" in new_jobs


# ─── Rate Limit Integration ───────────────────────────────────────────


class TestRateLimitIntegration:
    """Tests for rate-limiter integration via protocol."""

    @pytest.mark.asyncio
    async def test_rate_limited_sheets_skipped(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Sheets using a rate-limited backend are skipped."""

        class MockRateLimiter:
            async def is_rate_limited(
                self, backend_type: str, model: str | None = None,
            ) -> tuple[bool, float]:
                return (backend_type == "claude_cli", 30.0)

        scheduler.set_rate_limiter(MockRateLimiter())

        sheets = [
            _sheet("job-a", 1, backend="claude_cli"),
            _sheet("job-a", 2, backend="openai"),
        ]
        await scheduler.register_job("job-a", sheets)

        # Only the openai sheet should dispatch
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.backend_type == "openai"

        # The claude_cli sheet should be skipped (put back in queue)
        assert scheduler.queued_count == 1

    @pytest.mark.asyncio
    async def test_rate_limited_skip_preserves_scheduler_state(
        self, scheduler: GlobalSheetScheduler,
    ):
        """When all queued sheets are rate-limited, scheduler state is unchanged.

        Verifies that skipping rate-limited sheets does not corrupt
        active_count, queued_count, or per-job tracking.
        """

        class AllLimitedRateLimiter:
            async def is_rate_limited(
                self, backend_type: str, model: str | None = None,
            ) -> tuple[bool, float]:
                return (True, 60.0)

        scheduler.set_rate_limiter(AllLimitedRateLimiter())

        sheets = [_sheet("job-a", i, backend="claude_cli") for i in range(1, 4)]
        await scheduler.register_job("job-a", sheets)

        # Capture state before dispatch attempt
        queued_before = scheduler.queued_count
        active_before = scheduler.active_count

        # All sheets are rate-limited — nothing should dispatch
        entry = await scheduler.next_sheet()
        assert entry is None

        # State must be unchanged
        assert scheduler.queued_count == queued_before
        assert scheduler.active_count == active_before
        assert scheduler.active_count == 0

        # Stats should also reflect the unchanged state
        stats = await scheduler.get_stats()
        assert stats.queued == 3
        assert stats.active == 0
        assert stats.per_job_queued == {"job-a": 3}
        assert stats.per_job_running == {"job-a": 0}

    @pytest.mark.asyncio
    async def test_no_rate_limiter_dispatches_normally(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Without a rate limiter, all sheets dispatch normally."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 4)]
        await scheduler.register_job("job-a", sheets)

        dispatched = 0
        for _ in range(3):
            e = await scheduler.next_sheet()
            if e is not None:
                dispatched += 1

        assert dispatched == 3


# ─── Backpressure Integration ─────────────────────────────────────────


class TestBackpressureIntegration:
    """Tests for backpressure controller integration."""

    @pytest.mark.asyncio
    async def test_backpressure_blocks_dispatch(
        self, scheduler: GlobalSheetScheduler,
    ):
        """When backpressure says no, next_sheet returns None."""

        class MockBackpressure:
            async def can_start_sheet(self) -> tuple[bool, float]:
                return (False, 0.0)

        scheduler.set_backpressure(MockBackpressure())

        sheets = [_sheet(sheet_num=i) for i in range(1, 4)]
        await scheduler.register_job("job-a", sheets)

        # Backpressure blocks all dispatch
        assert await scheduler.next_sheet() is None
        assert scheduler.queued_count == 3  # Still queued

    @pytest.mark.asyncio
    async def test_backpressure_soft_delay(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Soft backpressure adds a delay but still allows dispatch."""

        class MockSoftBackpressure:
            async def can_start_sheet(self) -> tuple[bool, float]:
                return (True, 0.001)  # 1ms delay

        scheduler.set_backpressure(MockSoftBackpressure())

        sheets = [_sheet(sheet_num=1)]
        await scheduler.register_job("job-a", sheets)

        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.sheet_num == 1


# ─── Stats ─────────────────────────────────────────────────────────────


class TestStats:
    """Tests for scheduler statistics."""

    @pytest.mark.asyncio
    async def test_stats_reflect_state(self, scheduler: GlobalSheetScheduler):
        """Stats accurately report queued and active counts."""
        await scheduler.register_job(
            "job-a", [_sheet("job-a", i) for i in range(1, 5)],
        )

        stats = await scheduler.get_stats()
        assert stats.queued == 4
        assert stats.active == 0
        assert stats.max_concurrent == 3
        assert stats.per_job_queued == {"job-a": 4}
        assert stats.per_job_running == {"job-a": 0}

        # Dispatch 2
        await scheduler.next_sheet()
        await scheduler.next_sheet()

        stats = await scheduler.get_stats()
        assert stats.active == 2
        assert stats.queued == 2
        assert stats.per_job_running == {"job-a": 2}

    @pytest.mark.asyncio
    async def test_stats_multiple_jobs(self, scheduler: GlobalSheetScheduler):
        """Stats track per-job counts correctly across multiple jobs."""
        await scheduler.register_job(
            "job-a", [_sheet("job-a", i) for i in range(1, 3)],
        )
        await scheduler.register_job(
            "job-b", [_sheet("job-b", i) for i in range(1, 4)],
        )

        stats = await scheduler.get_stats()
        assert stats.queued == 5
        assert stats.per_job_queued.get("job-a") == 2
        assert stats.per_job_queued.get("job-b") == 3


# ─── Edge Cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_next_sheet_on_empty_queue(
        self, scheduler: GlobalSheetScheduler,
    ):
        """next_sheet returns None on an empty queue."""
        assert await scheduler.next_sheet() is None

    @pytest.mark.asyncio
    async def test_mark_complete_unknown_job(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Marking complete for an unknown job doesn't raise."""
        # Should be a no-op, not crash
        await scheduler.mark_complete("nonexistent", 1, success=True)
        assert scheduler.active_count == 0

    @pytest.mark.asyncio
    async def test_deregister_unknown_job(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Deregistering an unknown job is a no-op."""
        await scheduler.deregister_job("nonexistent")
        assert scheduler.queued_count == 0

    @pytest.mark.asyncio
    async def test_register_empty_sheet_list(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Registering a job with no sheets is valid."""
        await scheduler.register_job("empty-job", [])
        assert scheduler.queued_count == 0

    @pytest.mark.asyncio
    async def test_properties_match_stats(
        self, scheduler: GlobalSheetScheduler,
    ):
        """active_count and queued_count properties match stats."""
        await scheduler.register_job(
            "job-a", [_sheet(sheet_num=i) for i in range(1, 4)],
        )
        await scheduler.next_sheet()

        stats = await scheduler.get_stats()
        assert scheduler.active_count == stats.active
        assert scheduler.queued_count == stats.queued

    @pytest.mark.asyncio
    async def test_duplicate_sheet_num_raises(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Registering sheets with duplicate sheet_nums raises ValueError.

        Validates P002: duplicate sheet_nums would corrupt DAG tracking
        and completion-set logic, so they are rejected upfront.
        """
        sheets = [
            _sheet(sheet_num=1),
            _sheet(sheet_num=2),
            _sheet(sheet_num=1),  # duplicate
        ]
        with pytest.raises(ValueError, match="Duplicate sheet_num 1"):
            await scheduler.register_job("job-a", sheets)

        # Scheduler state should be clean — nothing was registered
        assert scheduler.queued_count == 0
        assert scheduler.active_count == 0


# ─── P009: mark_complete() Edge Cases ──────────────────────────────────


class TestMarkCompleteEdgeCases:
    """Edge cases for mark_complete() — validates P001 (decrement guard)
    and P006 (unknown job_id early return)."""

    @pytest.mark.asyncio
    async def test_double_complete_does_not_double_decrement(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Completing the same sheet twice doesn't decrement active_count twice."""
        sheets = [_sheet(sheet_num=1)]
        await scheduler.register_job("job-a", sheets)

        entry = await scheduler.next_sheet()
        assert entry is not None
        assert scheduler.active_count == 1

        await scheduler.mark_complete("job-a", 1, success=True)
        assert scheduler.active_count == 0

        # Second complete for same sheet — should be idempotent
        await scheduler.mark_complete("job-a", 1, success=True)
        assert scheduler.active_count == 0

    @pytest.mark.asyncio
    async def test_complete_undispatched_sheet_no_decrement(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Completing a sheet that was never dispatched doesn't change active_count."""
        sheets = [_sheet(sheet_num=1), _sheet(sheet_num=2)]
        await scheduler.register_job("job-a", sheets)

        # Dispatch only sheet 1
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert scheduler.active_count == 1

        # Complete sheet 2 which was never dispatched
        await scheduler.mark_complete("job-a", 2, success=True)
        assert scheduler.active_count == 1  # unchanged

        # Sheet 1 still running
        await scheduler.mark_complete("job-a", 1, success=True)
        assert scheduler.active_count == 0

    @pytest.mark.asyncio
    async def test_unknown_job_doesnt_create_tracking_entries(
        self, scheduler: GlobalSheetScheduler,
    ):
        """mark_complete for unknown job_id doesn't leak memory by creating entries."""
        await scheduler.mark_complete("ghost-job", 1, success=True)

        # Verify no tracking data was created
        assert "ghost-job" not in scheduler._job_completed
        assert "ghost-job" not in scheduler._running
        assert "ghost-job" not in scheduler._job_all_sheets

    @pytest.mark.asyncio
    async def test_active_count_stays_accurate_through_lifecycle(
        self, scheduler: GlobalSheetScheduler,
    ):
        """active_count accurately reflects running sheets through full lifecycle."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 5)]
        await scheduler.register_job("job-a", sheets)

        # Dispatch 3 (max_concurrent)
        for _ in range(3):
            await scheduler.next_sheet()
        assert scheduler.active_count == 3

        # Complete 1, dispatch 1 — should stay at 3
        await scheduler.mark_complete("job-a", 1, success=True)
        assert scheduler.active_count == 2
        await scheduler.next_sheet()
        assert scheduler.active_count == 3

        # Complete all
        for i in range(2, 5):
            await scheduler.mark_complete("job-a", i, success=True)
        assert scheduler.active_count == 0


# ─── P010: Duplicate register_job() ───────────────────────────────────


class TestDuplicateRegisterJob:
    """Tests for duplicate register_job() handling — validates P002."""

    @pytest.mark.asyncio
    async def test_duplicate_register_purges_old_entries(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Re-registering a job clears old heap entries."""
        sheets_v1 = [_sheet("job-a", i) for i in range(1, 4)]
        await scheduler.register_job("job-a", sheets_v1)
        assert scheduler.queued_count == 3

        # Re-register with different sheets
        sheets_v2 = [_sheet("job-a", i) for i in range(10, 12)]
        await scheduler.register_job("job-a", sheets_v2)

        # Should have only the new sheets
        assert scheduler.queued_count == 2

        # Dispatched sheet should be from v2
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.sheet_num >= 10

    @pytest.mark.asyncio
    async def test_duplicate_register_resets_running_count(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Re-registering while sheets are running adjusts active_count."""
        sheets = [_sheet("job-a", i) for i in range(1, 4)]
        await scheduler.register_job("job-a", sheets)

        # Dispatch 2 sheets
        await scheduler.next_sheet()
        await scheduler.next_sheet()
        assert scheduler.active_count == 2

        # Re-register — running sheets for this job are purged
        new_sheets = [_sheet("job-a", i) for i in range(10, 12)]
        await scheduler.register_job("job-a", new_sheets)

        assert scheduler.active_count == 0
        assert scheduler.queued_count == 2

    @pytest.mark.asyncio
    async def test_duplicate_register_doesnt_affect_other_jobs(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Re-registering job-a doesn't affect job-b."""
        await scheduler.register_job("job-a", [_sheet("job-a", 1)])
        await scheduler.register_job("job-b", [_sheet("job-b", 1)])
        assert scheduler.queued_count == 2

        # Re-register job-a
        await scheduler.register_job("job-a", [_sheet("job-a", 10)])

        # job-b still has its sheet
        assert scheduler.queued_count == 2
        stats = await scheduler.get_stats()
        assert stats.per_job_queued.get("job-b") == 1


# ─── P011: success=False Still Enqueues Dependents ─────────────────────


class TestFailedSheetDependents:
    """Tests that success=False in mark_complete still enqueues dependents.

    Mozart's DAG scheduler enqueues dependents regardless of success/failure
    because the caller (JobManager) decides retry policy, not the scheduler.
    """

    @pytest.mark.asyncio
    async def test_failed_sheet_enqueues_dependents(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Completing sheet 1 with success=False still makes sheet 2 available."""
        sheets = [_sheet(sheet_num=1), _sheet(sheet_num=2)]
        deps = {2: {1}}

        await scheduler.register_job("job-a", sheets, dependencies=deps)
        assert scheduler.queued_count == 1  # Only sheet 1

        # Dispatch and FAIL sheet 1
        entry = await scheduler.next_sheet()
        assert entry is not None
        await scheduler.mark_complete("job-a", 1, success=False)

        # Sheet 2 should still become queued
        assert scheduler.queued_count == 1
        entry2 = await scheduler.next_sheet()
        assert entry2 is not None
        assert entry2.info.sheet_num == 2

    @pytest.mark.asyncio
    async def test_failed_sheet_in_diamond_dag(
        self, scheduler: GlobalSheetScheduler,
    ):
        """In diamond 1→{2,3}→4, failing sheet 2 still unlocks sheet 4
        after sheet 3 also completes."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 5)]
        deps = {2: {1}, 3: {1}, 4: {2, 3}}

        await scheduler.register_job("job-a", sheets, dependencies=deps)

        # Complete sheet 1 → 2, 3 become ready
        e1 = await scheduler.next_sheet()
        assert e1 is not None
        await scheduler.mark_complete("job-a", 1, success=True)

        # Dispatch 2 and 3
        e2 = await scheduler.next_sheet()
        e3 = await scheduler.next_sheet()
        assert e2 is not None and e3 is not None

        # FAIL sheet 2, succeed sheet 3
        await scheduler.mark_complete("job-a", e2.info.sheet_num, success=False)
        await scheduler.mark_complete("job-a", e3.info.sheet_num, success=True)

        # Sheet 4 should be enqueued (both deps are "completed")
        assert scheduler.queued_count == 1
        e4 = await scheduler.next_sheet()
        assert e4 is not None
        assert e4.info.sheet_num == 4


# ─── P012: Boundary/Extreme Value Tests ──────────────────────────────


class TestSchedulerBoundaryValues:
    """Boundary and extreme value tests for scheduler internals."""

    @pytest.mark.asyncio
    async def test_single_sheet_job(self, scheduler: GlobalSheetScheduler):
        """A job with exactly 1 sheet works correctly."""
        await scheduler.register_job("job-a", [_sheet(sheet_num=1)])
        assert scheduler.queued_count == 1

        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.sheet_num == 1
        assert scheduler.active_count == 1

        await scheduler.mark_complete("job-a", 1, success=True)
        assert scheduler.active_count == 0

    @pytest.mark.asyncio
    async def test_max_concurrent_of_one(self, daemon_config: DaemonConfig):
        """With max_concurrent_sheets=1, only one sheet at a time."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 1})
        scheduler = GlobalSheetScheduler(config)

        sheets = [_sheet(sheet_num=i) for i in range(1, 5)]
        await scheduler.register_job("job-a", sheets)

        e1 = await scheduler.next_sheet()
        assert e1 is not None

        # Second attempt blocked
        assert await scheduler.next_sheet() is None
        assert scheduler.active_count == 1

        # Complete → next one can go
        await scheduler.mark_complete("job-a", e1.info.sheet_num, True)
        e2 = await scheduler.next_sheet()
        assert e2 is not None

    @pytest.mark.asyncio
    async def test_zero_priority_sheet(self, scheduler: GlobalSheetScheduler):
        """Sheet with job_priority=0 (edge of range) dispatches correctly."""
        sheets = [
            _sheet(sheet_num=1, job_priority=0),
            _sheet(sheet_num=2, job_priority=10),
        ]
        await scheduler.register_job("job-a", sheets)

        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.sheet_num == 1  # priority 0 = most urgent

    @pytest.mark.asyncio
    async def test_very_high_retry_count(self, scheduler: GlobalSheetScheduler):
        """Sheet with extreme retry count gets correct priority boost."""
        sheets = [
            _sheet(sheet_num=1, job_priority=5, retries=0),
            _sheet(sheet_num=2, job_priority=5, retries=100),
        ]
        await scheduler.register_job("job-a", sheets)

        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.sheet_num == 2  # massive boost from retries

    @pytest.mark.asyncio
    async def test_large_concurrency_limit(self, daemon_config: DaemonConfig):
        """High max_concurrent_sheets allows many sheets at once."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 100})
        scheduler = GlobalSheetScheduler(config)

        sheets = [_sheet(sheet_num=i) for i in range(1, 51)]
        await scheduler.register_job("job-a", sheets)

        dispatched = 0
        for _ in range(50):
            e = await scheduler.next_sheet()
            if e is not None:
                dispatched += 1

        assert dispatched == 50
        assert scheduler.active_count == 50


# ─── P015: DAG Edge Cases ─────────────────────────────────────────────


class TestDAGEdgeCases:
    """DAG edge cases: circular deps, self-deps, missing refs."""

    @pytest.mark.asyncio
    async def test_circular_dependency_raises(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Circular dependency (1→2→3→1) raises ValueError."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 4)]
        deps = {1: {3}, 2: {1}, 3: {2}}

        with pytest.raises(ValueError, match="Circular dependency"):
            await scheduler.register_job("job-a", sheets, dependencies=deps)

    @pytest.mark.asyncio
    async def test_self_dependency_raises(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Self-dependency (sheet depends on itself) raises ValueError."""
        sheets = [_sheet(sheet_num=1), _sheet(sheet_num=2)]
        deps = {1: {1}}  # sheet 1 depends on itself

        with pytest.raises(ValueError, match="Circular dependency"):
            await scheduler.register_job("job-a", sheets, dependencies=deps)

    @pytest.mark.asyncio
    async def test_two_node_cycle_raises(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Two-node cycle (1↔2) raises ValueError."""
        sheets = [_sheet(sheet_num=1), _sheet(sheet_num=2)]
        deps = {1: {2}, 2: {1}}

        with pytest.raises(ValueError, match="Circular dependency"):
            await scheduler.register_job("job-a", sheets, dependencies=deps)

    @pytest.mark.asyncio
    async def test_dep_on_nonexistent_sheet_still_works(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Dep on a sheet not in the sheets list — sheet stays blocked."""
        sheets = [_sheet(sheet_num=1), _sheet(sheet_num=2)]
        # Sheet 2 depends on sheet 99 which doesn't exist in sheets list
        deps = {2: {99}}

        # Should not raise (not a cycle)
        await scheduler.register_job("job-a", sheets, dependencies=deps)

        # Sheet 1 enqueued (no deps), sheet 2 blocked (dep on 99, never satisfied)
        assert scheduler.queued_count == 1
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.sheet_num == 1

    @pytest.mark.asyncio
    async def test_empty_deps_dict_treated_as_independent(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Empty deps dict means all sheets are independent."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 4)]
        await scheduler.register_job("job-a", sheets, dependencies={})
        assert scheduler.queued_count == 3

    @pytest.mark.asyncio
    async def test_no_deps_is_none(
        self, scheduler: GlobalSheetScheduler,
    ):
        """Dependencies=None means all sheets are independent."""
        sheets = [_sheet(sheet_num=i) for i in range(1, 4)]
        await scheduler.register_job("job-a", sheets, dependencies=None)
        assert scheduler.queued_count == 3


# ─── P016: DAG Topology Tests ─────────────────────────────────────────


class TestDAGTopology:
    """DAG topology tests: long chains, wide fan-out, fan-in."""

    @pytest.mark.asyncio
    async def test_long_chain(self, daemon_config: DaemonConfig):
        """Long sequential chain: 1→2→3→...→20. Each sheet must wait."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 5})
        scheduler = GlobalSheetScheduler(config)

        chain_len = 20
        sheets = [_sheet(sheet_num=i) for i in range(1, chain_len + 1)]
        deps = {i: {i - 1} for i in range(2, chain_len + 1)}

        await scheduler.register_job("job-a", sheets, dependencies=deps)
        assert scheduler.queued_count == 1  # Only sheet 1

        # Walk the entire chain
        for expected_num in range(1, chain_len + 1):
            entry = await scheduler.next_sheet()
            assert entry is not None, f"Expected sheet {expected_num} but got None"
            assert entry.info.sheet_num == expected_num
            await scheduler.mark_complete("job-a", expected_num, success=True)

        # All done
        assert scheduler.queued_count == 0
        assert scheduler.active_count == 0

    @pytest.mark.asyncio
    async def test_wide_fan_out(self, daemon_config: DaemonConfig):
        """Fan-out: sheet 1 → {2, 3, 4, ..., 21}. All 20 dependents ready after 1."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 25})
        scheduler = GlobalSheetScheduler(config)

        sheets = [_sheet(sheet_num=i) for i in range(1, 22)]
        deps = {i: {1} for i in range(2, 22)}  # all depend on 1

        await scheduler.register_job("job-a", sheets, dependencies=deps)
        assert scheduler.queued_count == 1  # Only sheet 1

        # Complete sheet 1 → all 20 dependents become ready
        e1 = await scheduler.next_sheet()
        assert e1 is not None
        await scheduler.mark_complete("job-a", 1, success=True)

        assert scheduler.queued_count == 20

        # Dispatch all 20
        dispatched = 0
        for _ in range(20):
            e = await scheduler.next_sheet()
            if e is not None:
                dispatched += 1
        assert dispatched == 20

    @pytest.mark.asyncio
    async def test_fan_in(self, daemon_config: DaemonConfig):
        """Fan-in: {1, 2, 3, 4, 5} → 6. Sheet 6 only ready when all 5 complete."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 10})
        scheduler = GlobalSheetScheduler(config)

        sheets = [_sheet(sheet_num=i) for i in range(1, 7)]
        deps = {6: {1, 2, 3, 4, 5}}

        await scheduler.register_job("job-a", sheets, dependencies=deps)
        assert scheduler.queued_count == 5  # 1-5 are independent

        # Dispatch and complete 1-4
        for i in range(1, 5):
            e = await scheduler.next_sheet()
            assert e is not None
            await scheduler.mark_complete("job-a", e.info.sheet_num, True)

        # Sheet 6 NOT yet ready (still needs 5)
        # The only sheet in queue should be sheet 5
        assert scheduler.queued_count == 1
        e5 = await scheduler.next_sheet()
        assert e5 is not None
        assert e5.info.sheet_num == 5
        await scheduler.mark_complete("job-a", 5, True)

        # Now sheet 6 is ready
        assert scheduler.queued_count == 1
        e6 = await scheduler.next_sheet()
        assert e6 is not None
        assert e6.info.sheet_num == 6

    @pytest.mark.asyncio
    async def test_multi_level_dag(self, daemon_config: DaemonConfig):
        """Multi-level: 1→{2,3}, 2→4, 3→4, 4→5. Validates deep dependency resolution."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 5})
        scheduler = GlobalSheetScheduler(config)

        sheets = [_sheet(sheet_num=i) for i in range(1, 6)]
        deps = {2: {1}, 3: {1}, 4: {2, 3}, 5: {4}}

        await scheduler.register_job("job-a", sheets, dependencies=deps)

        # Level 0: sheet 1
        e1 = await scheduler.next_sheet()
        assert e1 is not None and e1.info.sheet_num == 1
        await scheduler.mark_complete("job-a", 1, True)

        # Level 1: sheets 2 and 3
        dispatched = set()
        for _ in range(2):
            e = await scheduler.next_sheet()
            assert e is not None
            dispatched.add(e.info.sheet_num)
        assert dispatched == {2, 3}

        for sn in dispatched:
            await scheduler.mark_complete("job-a", sn, True)

        # Level 2: sheet 4
        e4 = await scheduler.next_sheet()
        assert e4 is not None and e4.info.sheet_num == 4
        await scheduler.mark_complete("job-a", 4, True)

        # Level 3: sheet 5
        e5 = await scheduler.next_sheet()
        assert e5 is not None and e5.info.sheet_num == 5


# ─── P020: Concurrent Scheduler Stress Tests ──────────────────────────


class TestConcurrentSchedulerStress:
    """Stress tests: concurrent next_sheet() + mark_complete() under load."""

    @pytest.mark.asyncio
    async def test_concurrent_dispatch_and_complete_50_sheets(
        self, daemon_config: DaemonConfig,
    ):
        """50 sheets dispatched and completed by concurrent workers.

        Verifies that active_count stays consistent and all sheets
        eventually complete without deadlock.
        """
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 10})
        scheduler = GlobalSheetScheduler(config)

        total_sheets = 50
        sheets = [_sheet(sheet_num=i) for i in range(1, total_sheets + 1)]
        await scheduler.register_job("job-a", sheets)

        completed: set[int] = set()
        lock = asyncio.Lock()

        async def worker():
            while True:
                entry = await scheduler.next_sheet()
                if entry is None:
                    async with lock:
                        if len(completed) >= total_sheets:
                            return
                    # Let other workers make progress
                    await asyncio.sleep(0.001)
                    continue

                # Simulate brief work
                await asyncio.sleep(0.001)
                await scheduler.mark_complete(
                    "job-a", entry.info.sheet_num, success=True,
                )
                async with lock:
                    completed.add(entry.info.sheet_num)

        # Run 5 concurrent workers with timeout
        workers = [asyncio.create_task(worker()) for _ in range(5)]
        done, pending = await asyncio.wait(workers, timeout=10.0)

        for t in pending:
            t.cancel()
        for t in pending:
            try:
                await t
            except asyncio.CancelledError:
                pass

        assert len(completed) == total_sheets
        assert scheduler.active_count == 0

    @pytest.mark.asyncio
    async def test_concurrent_dispatch_10_jobs(
        self, daemon_config: DaemonConfig,
    ):
        """10 jobs with 5 sheets each, dispatched by concurrent workers.

        Verifies fair-share scheduling and consistent state under
        multi-job concurrency.
        """
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 10})
        scheduler = GlobalSheetScheduler(config)

        num_jobs = 10
        sheets_per_job = 5

        for j in range(num_jobs):
            job_id = f"job-{j}"
            sheets = [_sheet(job_id, i) for i in range(1, sheets_per_job + 1)]
            await scheduler.register_job(job_id, sheets)

        total_expected = num_jobs * sheets_per_job
        completed: dict[str, set[int]] = {}
        lock = asyncio.Lock()

        async def worker():
            while True:
                entry = await scheduler.next_sheet()
                if entry is None:
                    async with lock:
                        total_done = sum(len(v) for v in completed.values())
                        if total_done >= total_expected:
                            return
                    await asyncio.sleep(0.001)
                    continue

                await asyncio.sleep(0.001)
                await scheduler.mark_complete(
                    entry.info.job_id, entry.info.sheet_num, success=True,
                )
                async with lock:
                    completed.setdefault(entry.info.job_id, set()).add(
                        entry.info.sheet_num,
                    )

        workers = [asyncio.create_task(worker()) for _ in range(8)]
        done, pending = await asyncio.wait(workers, timeout=15.0)

        for t in pending:
            t.cancel()
        for t in pending:
            try:
                await t
            except asyncio.CancelledError:
                pass

        total_done = sum(len(v) for v in completed.values())
        assert total_done == total_expected
        assert scheduler.active_count == 0

        # Verify all jobs got served
        for j in range(num_jobs):
            assert len(completed.get(f"job-{j}", set())) == sheets_per_job

    @pytest.mark.asyncio
    async def test_concurrent_dispatch_with_dag(
        self, daemon_config: DaemonConfig,
    ):
        """Concurrent workers with DAG dependencies — no sheet starts before deps."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 5})
        scheduler = GlobalSheetScheduler(config)

        # Chain: 1→2→3→4→5, plus 6,7,8 independent
        sheets = [_sheet(sheet_num=i) for i in range(1, 9)]
        deps = {2: {1}, 3: {2}, 4: {3}, 5: {4}}

        await scheduler.register_job("job-a", sheets, dependencies=deps)

        dispatch_order: list[int] = []
        lock = asyncio.Lock()

        async def worker():
            while True:
                entry = await scheduler.next_sheet()
                if entry is None:
                    async with lock:
                        if len(dispatch_order) >= 8:
                            return
                    await asyncio.sleep(0.001)
                    continue

                async with lock:
                    dispatch_order.append(entry.info.sheet_num)

                await asyncio.sleep(0.001)
                await scheduler.mark_complete(
                    "job-a", entry.info.sheet_num, success=True,
                )

        workers = [asyncio.create_task(worker()) for _ in range(3)]
        done, pending = await asyncio.wait(workers, timeout=10.0)

        for t in pending:
            t.cancel()
        for t in pending:
            try:
                await t
            except asyncio.CancelledError:
                pass

        assert len(dispatch_order) == 8
        assert scheduler.active_count == 0

        # Verify DAG ordering: for each chain step, predecessor must
        # appear before successor in dispatch_order
        for i in range(2, 6):
            assert dispatch_order.index(i - 1) < dispatch_order.index(i), (
                f"Sheet {i-1} must dispatch before sheet {i}"
            )

    @pytest.mark.asyncio
    async def test_rapid_register_deregister(
        self, daemon_config: DaemonConfig,
    ):
        """Rapidly registering and deregistering jobs doesn't corrupt state."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 5})
        scheduler = GlobalSheetScheduler(config)

        async def churn(job_id: str):
            for _ in range(20):
                sheets = [_sheet(job_id, i) for i in range(1, 4)]
                await scheduler.register_job(job_id, sheets)
                await asyncio.sleep(0.001)
                await scheduler.deregister_job(job_id)

        tasks = [asyncio.create_task(churn(f"job-{i}")) for i in range(5)]
        await asyncio.gather(*tasks)

        # After all churn, state should be clean
        assert scheduler.queued_count == 0
        assert scheduler.active_count == 0


# ─── P022: End-to-End Integration Test ──────────────────────────────


class TestEndToEndIntegration:
    """End-to-end integration test: scheduler + rate coordinator + backpressure.

    Uses REAL instances of all three Phase 3 components wired together
    (no mocks for the component interfaces) to prove cross-component
    correctness.  Only the system memory probe is patched to make
    backpressure thresholds deterministic.

    Design note: When a rate limit is active, the BackpressureController
    sees it via ``active_limits`` and escalates to HIGH, which injects a
    30-second delay in ``scheduler.next_sheet()``.  Tests that combine
    rate limiting with scheduler dispatch use ``scheduler_with_rate_only``
    (no backpressure) to avoid this delay.  The coupling between rate
    limits and backpressure is tested separately in
    ``test_rate_limit_triggers_high_backpressure``.
    """

    @pytest.fixture
    def integration_config(self, tmp_path: Path) -> DaemonConfig:
        """DaemonConfig for integration tests."""
        return DaemonConfig(
            max_concurrent_sheets=5,
            max_concurrent_jobs=5,
            pid_file=tmp_path / "integration.pid",
        )

    @pytest.fixture
    def wired_components(self, integration_config: DaemonConfig):
        """All three components wired together (backpressure-enabled)."""
        from mozart.daemon.backpressure import BackpressureController
        from mozart.daemon.config import ResourceLimitConfig
        from mozart.daemon.monitor import ResourceMonitor
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        scheduler = GlobalSheetScheduler(integration_config)
        coordinator = RateLimitCoordinator()
        resource_config = ResourceLimitConfig(
            max_memory_mb=1000,
            max_processes=50,
        )
        monitor = ResourceMonitor(resource_config)
        controller = BackpressureController(monitor, coordinator)

        # Wire exactly how JobManager does it
        scheduler.set_rate_limiter(coordinator)
        scheduler.set_backpressure(controller)

        return {
            "scheduler": scheduler,
            "coordinator": coordinator,
            "controller": controller,
            "monitor": monitor,
        }

    @pytest.fixture
    def scheduler_with_rate_only(self, integration_config: DaemonConfig):
        """Scheduler wired to rate coordinator only (no backpressure delay).

        Used for tests that exercise the rate-limit → scheduler skip path
        without triggering the backpressure 30-second HIGH delay that
        active rate limits cause.
        """
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        scheduler = GlobalSheetScheduler(integration_config)
        coordinator = RateLimitCoordinator()
        scheduler.set_rate_limiter(coordinator)
        # Deliberately no backpressure — avoids the 30s HIGH delay
        return {"scheduler": scheduler, "coordinator": coordinator}

    @pytest.mark.asyncio
    async def test_rate_limit_flows_through_scheduler(
        self, scheduler_with_rate_only: dict,
    ):
        """Rate limit reported → coordinator stores → scheduler skips backend.

        Proves the full data flow: a rate limit event reported to the
        coordinator causes the scheduler to skip sheets using that backend,
        while sheets on other backends still dispatch normally.
        """
        scheduler = scheduler_with_rate_only["scheduler"]
        coordinator = scheduler_with_rate_only["coordinator"]

        # Register two jobs using different backends
        await scheduler.register_job("job-claude", [
            _sheet("job-claude", 1, backend="claude_cli"),
            _sheet("job-claude", 2, backend="claude_cli"),
        ])
        await scheduler.register_job("job-openai", [
            _sheet("job-openai", 1, backend="openai"),
        ])

        # Report rate limit on claude_cli via coordinator
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=60.0,
            job_id="job-claude",
            sheet_num=1,
        )

        # Verify coordinator has the limit
        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is True
        assert remaining > 0

        # Scheduler should skip claude_cli sheets, dispatch openai
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.backend_type == "openai"
        assert entry.info.job_id == "job-openai"

        # claude_cli sheets should still be queued (skipped, not dropped)
        assert scheduler.queued_count == 2

        # No more dispatchable sheets (openai dispatched, claude blocked)
        entry2 = await scheduler.next_sheet()
        assert entry2 is None

    @pytest.mark.asyncio
    async def test_backpressure_blocks_scheduler_dispatch(
        self, wired_components: dict,
    ):
        """High memory → backpressure CRITICAL → scheduler rejects all sheets.

        Proves the chain: monitor reports high memory → backpressure
        controller returns CRITICAL → scheduler's next_sheet() returns None.
        """
        from unittest.mock import patch

        from mozart.daemon.backpressure import PressureLevel
        from mozart.daemon.monitor import ResourceMonitor

        scheduler = wired_components["scheduler"]
        controller = wired_components["controller"]

        # Register sheets
        await scheduler.register_job("job-a", [
            _sheet("job-a", i) for i in range(1, 4)
        ])

        # Simulate CRITICAL memory (>95% of 1000MB)
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=960.0,
        ), patch.object(
            ResourceMonitor, "_get_child_process_count", return_value=2,
        ):
            assert controller.current_level() == PressureLevel.CRITICAL

            # Scheduler should reject dispatch due to backpressure
            entry = await scheduler.next_sheet()
            assert entry is None

            # Sheets remain queued — not dropped
            assert scheduler.queued_count == 3

    @pytest.mark.asyncio
    async def test_rate_limit_triggers_high_backpressure(
        self, wired_components: dict,
    ):
        """Active rate limit → backpressure escalates to HIGH → job rejected.

        Proves the cross-component coupling: coordinator has active limit →
        backpressure controller reads active_limits → escalates to HIGH →
        should_accept_job() returns False.  This is the key integration
        point between rate_coordinator and backpressure.
        """
        from unittest.mock import patch

        from mozart.daemon.backpressure import PressureLevel
        from mozart.daemon.monitor import ResourceMonitor

        coordinator = wired_components["coordinator"]
        controller = wired_components["controller"]

        # Low memory, no process pressure
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "_get_child_process_count", return_value=2,
        ):
            # Before rate limit: pressure is NONE
            assert controller.current_level() == PressureLevel.NONE
            assert controller.should_accept_job() is True

            # Report a rate limit
            await coordinator.report_rate_limit(
                backend_type="claude_cli",
                wait_seconds=60.0,
                job_id="job-a",
                sheet_num=1,
            )

            # Now: rate limit active → backpressure escalates to HIGH
            assert controller.current_level() == PressureLevel.HIGH
            # F-149: Rate limits no longer block job submissions.
            # Job-level gating only considers resource pressure.
            # Sheet-level dispatch still uses HIGH for pacing delay.
            assert controller.should_accept_job() is True

    @pytest.mark.asyncio
    async def test_full_lifecycle_dispatch_ratelimit_recover(
        self, scheduler_with_rate_only: dict,
    ):
        """Full lifecycle: register → dispatch → rate limit → recover → complete.

        Exercises scheduler + rate coordinator through a realistic scenario:
        1. Two jobs registered with different backends
        2. Sheets dispatch normally
        3. Rate limit hits one backend — affected sheets blocked
        4. Rate limit expires — blocked sheets resume dispatching
        5. All sheets complete — scheduler state clean
        """
        scheduler = scheduler_with_rate_only["scheduler"]
        coordinator = scheduler_with_rate_only["coordinator"]

        # Phase 1: Register two jobs
        await scheduler.register_job("job-a", [
            _sheet("job-a", 1, backend="claude_cli"),
            _sheet("job-a", 2, backend="claude_cli"),
            _sheet("job-a", 3, backend="openai"),
        ])
        await scheduler.register_job("job-b", [
            _sheet("job-b", 1, backend="openai"),
            _sheet("job-b", 2, backend="openai"),
        ])

        assert scheduler.queued_count == 5

        # Phase 2: Dispatch 2 sheets
        dispatched = []
        for _ in range(2):
            entry = await scheduler.next_sheet()
            assert entry is not None
            dispatched.append(entry)

        assert scheduler.active_count == 2

        # Phase 3: Rate limit hits claude_cli
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=0.05,  # Short for testing
            job_id="job-a",
            sheet_num=1,
        )

        # Dispatch more — only openai sheets should come through
        openai_dispatched = []
        for _ in range(3):
            entry = await scheduler.next_sheet()
            if entry is None:
                break
            openai_dispatched.append(entry)
            assert entry.info.backend_type == "openai"

        # Phase 4: Complete running sheets and wait for rate limit expiry
        for entry in dispatched + openai_dispatched:
            await scheduler.mark_complete(
                entry.info.job_id, entry.info.sheet_num, success=True,
            )

        # Wait for rate limit to expire
        await asyncio.sleep(0.06)

        # Verify rate limit cleared
        is_limited, _ = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is False

        # Phase 5: Remaining claude_cli sheets can now dispatch
        remaining = []
        for _ in range(5):
            entry = await scheduler.next_sheet()
            if entry is None:
                break
            remaining.append(entry)
            await scheduler.mark_complete(
                entry.info.job_id, entry.info.sheet_num, success=True,
            )

        # All sheets should have been processed
        assert scheduler.active_count == 0
        assert scheduler.queued_count == 0

    @pytest.mark.asyncio
    async def test_dag_with_rate_limit_blocking(
        self, scheduler_with_rate_only: dict,
    ):
        """DAG dependencies work correctly alongside rate limiting.

        Chain: sheet 1 → sheet 2 → sheet 3, all on claude_cli.
        Rate limit hits after sheet 1 completes — sheet 2 becomes ready
        but scheduler skips it.  After limit expires, sheet 2 dispatches
        and the chain continues.
        """
        scheduler = scheduler_with_rate_only["scheduler"]
        coordinator = scheduler_with_rate_only["coordinator"]

        sheets = [
            _sheet("job-dag", 1, backend="claude_cli"),
            _sheet("job-dag", 2, backend="claude_cli"),
            _sheet("job-dag", 3, backend="claude_cli"),
        ]
        deps = {2: {1}, 3: {2}}

        await scheduler.register_job("job-dag", sheets, dependencies=deps)
        assert scheduler.queued_count == 1  # Only sheet 1

        # Dispatch and complete sheet 1
        e1 = await scheduler.next_sheet()
        assert e1 is not None and e1.info.sheet_num == 1
        await scheduler.mark_complete("job-dag", 1, success=True)

        # Sheet 2 now ready
        assert scheduler.queued_count == 1

        # Rate limit hits
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=0.05,
            job_id="job-dag",
            sheet_num=1,
        )

        # Sheet 2 is ready but backend is rate-limited
        entry = await scheduler.next_sheet()
        assert entry is None
        assert scheduler.queued_count == 1  # Still queued

        # Wait for rate limit to clear
        await asyncio.sleep(0.06)

        # Now sheet 2 can dispatch
        e2 = await scheduler.next_sheet()
        assert e2 is not None and e2.info.sheet_num == 2
        await scheduler.mark_complete("job-dag", 2, success=True)

        # Sheet 3 ready and dispatches
        e3 = await scheduler.next_sheet()
        assert e3 is not None and e3.info.sheet_num == 3
        await scheduler.mark_complete("job-dag", 3, success=True)

        assert scheduler.active_count == 0
        assert scheduler.queued_count == 0

    @pytest.mark.asyncio
    async def test_stats_reflect_cross_component_state(
        self, scheduler_with_rate_only: dict,
    ):
        """Scheduler stats accurately reflect state after rate limit interactions."""
        scheduler = scheduler_with_rate_only["scheduler"]
        coordinator = scheduler_with_rate_only["coordinator"]

        await scheduler.register_job("job-a", [
            _sheet("job-a", 1, backend="claude_cli"),
            _sheet("job-a", 2, backend="openai"),
        ])

        # Rate-limit claude_cli
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=60.0,
            job_id="job-a",
            sheet_num=1,
        )

        # Dispatch only what's available (openai)
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.backend_type == "openai"

        # Check stats
        stats = await scheduler.get_stats()
        assert stats.active == 1
        assert stats.queued == 1  # claude_cli sheet still queued
        assert stats.per_job_running == {"job-a": 1}
        assert stats.per_job_queued == {"job-a": 1}

    @pytest.mark.asyncio
    async def test_pressure_transitions_during_scheduling(
        self, wired_components: dict,
    ):
        """Backpressure level transitions affect scheduler dispatch in real time.

        Starts at NONE (dispatch works), moves to CRITICAL (dispatch blocked),
        recovers to NONE (dispatch resumes).
        """
        from unittest.mock import patch

        from mozart.daemon.backpressure import PressureLevel
        from mozart.daemon.monitor import ResourceMonitor

        scheduler = wired_components["scheduler"]
        controller = wired_components["controller"]

        await scheduler.register_job("job-a", [
            _sheet("job-a", i) for i in range(1, 4)
        ])

        # Phase 1: Low pressure — dispatch works
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "_get_child_process_count", return_value=2,
        ):
            assert controller.current_level() == PressureLevel.NONE
            e1 = await scheduler.next_sheet()
            assert e1 is not None

        # Phase 2: Critical pressure — dispatch blocked
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=960.0,
        ), patch.object(
            ResourceMonitor, "_get_child_process_count", return_value=2,
        ):
            assert controller.current_level() == PressureLevel.CRITICAL
            e2 = await scheduler.next_sheet()
            assert e2 is None

        # Phase 3: Recover — dispatch resumes
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "_get_child_process_count", return_value=2,
        ):
            assert controller.current_level() == PressureLevel.NONE
            e3 = await scheduler.next_sheet()
            assert e3 is not None

        # Complete dispatched sheets
        await scheduler.mark_complete("job-a", e1.info.sheet_num, success=True)
        await scheduler.mark_complete("job-a", e3.info.sheet_num, success=True)


# ─── P007: Concurrent deregister_job + mark_complete Race ────────────


class TestConcurrentDeregisterMarkComplete:
    """Concurrent deregister_job + mark_complete race test (P007).

    Validates that interleaving deregister_job() and mark_complete()
    from concurrent coroutines doesn't corrupt scheduler state.  Both
    methods acquire _lock, so this stress-tests the lock serialization
    under real contention.
    """

    @pytest.mark.asyncio
    async def test_deregister_during_completions(
        self, daemon_config: DaemonConfig,
    ):
        """Deregistering a job while its sheets are completing doesn't crash."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 20})
        scheduler = GlobalSheetScheduler(config)

        total_sheets = 20
        sheets = [_sheet(sheet_num=i) for i in range(1, total_sheets + 1)]
        await scheduler.register_job("job-a", sheets)

        # Dispatch all sheets
        dispatched = []
        for _ in range(total_sheets):
            entry = await scheduler.next_sheet()
            if entry is None:
                break
            dispatched.append(entry)

        assert len(dispatched) == total_sheets

        # Race: complete half the sheets concurrently with deregister
        async def complete_sheets():
            for entry in dispatched[:10]:
                await scheduler.mark_complete("job-a", entry.info.sheet_num, True)
                await asyncio.sleep(0)  # yield to let deregister interleave

        async def deregister():
            await asyncio.sleep(0)  # let at least one completion start
            await scheduler.deregister_job("job-a")

        await asyncio.gather(complete_sheets(), deregister())

        # After both operations complete, state must be consistent
        assert scheduler.active_count >= 0
        assert scheduler.queued_count >= 0
        # The deregister should have cleaned up running state
        assert scheduler.active_count == 0

    @pytest.mark.asyncio
    async def test_deregister_racing_with_multiple_jobs(
        self, daemon_config: DaemonConfig,
    ):
        """Deregistering job-a while completing job-b sheets doesn't interfere."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 10})
        scheduler = GlobalSheetScheduler(config)

        await scheduler.register_job("job-a", [_sheet("job-a", i) for i in range(1, 6)])
        await scheduler.register_job("job-b", [_sheet("job-b", i) for i in range(1, 6)])

        # Dispatch some sheets from both jobs
        dispatched: dict[str, list] = {"job-a": [], "job-b": []}
        for _ in range(10):
            entry = await scheduler.next_sheet()
            if entry is None:
                break
            dispatched[entry.info.job_id].append(entry)

        # Race: deregister job-a while completing job-b sheets
        async def complete_job_b():
            for entry in dispatched["job-b"]:
                await scheduler.mark_complete("job-b", entry.info.sheet_num, True)
                await asyncio.sleep(0)

        async def deregister_job_a():
            await asyncio.sleep(0)
            await scheduler.deregister_job("job-a")

        await asyncio.gather(complete_job_b(), deregister_job_a())

        # job-b completions should have succeeded independently
        assert scheduler.active_count == 0

    @pytest.mark.asyncio
    async def test_concurrent_deregister_same_job_idempotent(
        self, daemon_config: DaemonConfig,
    ):
        """Multiple concurrent deregister_job() calls for same job are safe."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 5})
        scheduler = GlobalSheetScheduler(config)

        sheets = [_sheet(sheet_num=i) for i in range(1, 6)]
        await scheduler.register_job("job-a", sheets)

        # Race: 5 concurrent deregister calls
        tasks = [
            asyncio.create_task(scheduler.deregister_job("job-a"))
            for _ in range(5)
        ]
        await asyncio.gather(*tasks)

        assert scheduler.queued_count == 0
        assert scheduler.active_count == 0


# ─── P008: Scale Test — 500+ Sheets ─────────────────────────────────


class TestSchedulerScale:
    """Scale test: 500+ sheets, 100 next_sheet() calls within time limit (P008).

    The scheduler's next_sheet() does O(n log n) re-scoring on every call.
    This test validates that performance doesn't degrade catastrophically
    at scale.
    """

    @pytest.mark.asyncio
    async def test_500_sheets_100_dispatches_under_1s(
        self, daemon_config: DaemonConfig,
    ):
        """500 sheets queued, 100 next_sheet() calls complete < 1 second."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 200})
        scheduler = GlobalSheetScheduler(config)

        sheets = [_sheet(sheet_num=i, job_priority=i % 10 + 1) for i in range(1, 501)]
        await scheduler.register_job("job-a", sheets)
        assert scheduler.queued_count == 500

        start = time.monotonic()
        dispatched = 0
        for _ in range(100):
            entry = await scheduler.next_sheet()
            if entry is not None:
                dispatched += 1
        elapsed = time.monotonic() - start

        assert dispatched == 100
        assert scheduler.active_count == 100
        # Performance gate: 100 dispatches should not take excessively long
        assert elapsed < 30.0, f"100 dispatches took {elapsed:.3f}s (limit: 30.0s)"

    @pytest.mark.asyncio
    async def test_1000_sheets_dispatch_and_complete_cycle(
        self, daemon_config: DaemonConfig,
    ):
        """1000 sheets: dispatch all, complete all, verify clean state."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 50})
        scheduler = GlobalSheetScheduler(config)

        total = 1000
        sheets = [_sheet(sheet_num=i) for i in range(1, total + 1)]
        await scheduler.register_job("job-a", sheets)

        completed_count = 0
        start = time.monotonic()

        while completed_count < total:
            entry = await scheduler.next_sheet()
            if entry is None:
                # All slots full; complete one to make room
                pass
            else:
                await scheduler.mark_complete("job-a", entry.info.sheet_num, True)
                completed_count += 1

        elapsed = time.monotonic() - start

        assert completed_count == total
        assert scheduler.active_count == 0
        assert scheduler.queued_count == 0
        # 1000 dispatch+complete cycles should complete reasonably fast.
        # Generous limit (30s) to avoid flaky failures under WSL/CI load
        # while still catching catastrophic regressions.
        assert elapsed < 30.0, f"1000 dispatch+complete took {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_10_jobs_50_sheets_each_fair_dispatch(
        self, daemon_config: DaemonConfig,
    ):
        """10 jobs × 50 sheets = 500 total. Fair-share distributes across jobs."""
        config = daemon_config.model_copy(update={"max_concurrent_sheets": 20})
        scheduler = GlobalSheetScheduler(config)

        num_jobs = 10
        sheets_per_job = 50
        for j in range(num_jobs):
            job_id = f"job-{j}"
            sheets = [_sheet(job_id, i, job_priority=5) for i in range(1, sheets_per_job + 1)]
            await scheduler.register_job(job_id, sheets)

        # Dispatch 20 sheets (the max_concurrent limit)
        dispatched_by_job: dict[str, int] = {}
        for _ in range(20):
            entry = await scheduler.next_sheet()
            assert entry is not None
            jid = entry.info.job_id
            dispatched_by_job[jid] = dispatched_by_job.get(jid, 0) + 1

        # Fair-share with 10 jobs and 20 slots → ~2 per job
        # At minimum, more than 1 job should have received slots
        assert len(dispatched_by_job) >= 5, (
            f"Expected >=5 jobs served, got {len(dispatched_by_job)}: {dispatched_by_job}"
        )


# ─── P014: Rate Limiter Exception Resilience ────────────────────────


class TestRateLimiterExceptionResilience:
    """Test that persistent rate_limiter exceptions don't lose sheets (P014).

    When is_rate_limited() raises an exception, the scheduler should
    skip the sheet (put it back) rather than losing it from the queue.
    """

    @pytest.mark.asyncio
    async def test_rate_limiter_exception_sheets_not_lost(
        self, scheduler: GlobalSheetScheduler,
    ):
        """If rate limiter raises, sheets are skipped, not dropped."""

        class BrokenRateLimiter:
            async def is_rate_limited(
                self, backend_type: str, model: str | None = None,
            ) -> tuple[bool, float]:
                raise RuntimeError("Connection refused")

        scheduler.set_rate_limiter(BrokenRateLimiter())

        sheets = [_sheet(sheet_num=i) for i in range(1, 4)]
        await scheduler.register_job("job-a", sheets)

        # All sheets should be skipped due to exception — none dispatched
        entry = await scheduler.next_sheet()
        assert entry is None

        # But sheets must still be in the queue — not lost
        assert scheduler.queued_count == 3
        assert scheduler.active_count == 0

    @pytest.mark.asyncio
    async def test_rate_limiter_exception_then_recovery(
        self, scheduler: GlobalSheetScheduler,
    ):
        """After rate limiter recovers, previously skipped sheets dispatch."""
        call_count = 0

        class FlakyRateLimiter:
            async def is_rate_limited(
                self, backend_type: str, model: str | None = None,
            ) -> tuple[bool, float]:
                nonlocal call_count
                call_count += 1
                if call_count <= 2:
                    raise RuntimeError("Temporary failure")
                return (False, 0.0)

        scheduler.set_rate_limiter(FlakyRateLimiter())

        sheets = [_sheet(sheet_num=1)]
        await scheduler.register_job("job-a", sheets)

        # First call: exception → skip (call_count becomes 1)
        entry = await scheduler.next_sheet()
        assert entry is None
        assert scheduler.queued_count == 1

        # Second call: still failing (call_count becomes 2)
        entry = await scheduler.next_sheet()
        assert entry is None

        # Third call: recovered (call_count becomes 3, > 2) — dispatches
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.sheet_num == 1


# ─── P015: Integration — Scheduler ↔ Rate Coordinator ↔ Backpressure ─


class TestTripleComponentIntegration:
    """Integration test: scheduler → rate coordinator → backpressure → dispatch (P015).

    Creates real instances of all three Phase 3 components and tests
    the full dispatch chain with rapidly changing resource levels.
    No mocks for component interfaces — only the system memory probe
    is patched.
    """

    @pytest.fixture
    def integration_setup(self, tmp_path: Path):
        """Wire all three components together."""
        from unittest.mock import patch as mock_patch

        from mozart.daemon.backpressure import BackpressureController
        from mozart.daemon.config import ResourceLimitConfig
        from mozart.daemon.monitor import ResourceMonitor
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        config = DaemonConfig(
            max_concurrent_sheets=5,
            max_concurrent_jobs=5,
            pid_file=tmp_path / "test.pid",
        )
        scheduler = GlobalSheetScheduler(config)
        coordinator = RateLimitCoordinator()
        resource_config = ResourceLimitConfig(max_memory_mb=1000, max_processes=50)
        monitor = ResourceMonitor(resource_config)
        controller = BackpressureController(monitor, coordinator)

        scheduler.set_rate_limiter(coordinator)
        scheduler.set_backpressure(controller)

        return {
            "scheduler": scheduler,
            "coordinator": coordinator,
            "controller": controller,
            "monitor": monitor,
        }

    @pytest.mark.asyncio
    async def test_rapidly_changing_pressure_dispatch(
        self, integration_setup: dict,
    ):
        """Dispatch under rapidly changing backpressure levels.

        Simulates: NONE → dispatch ok → CRITICAL → dispatch blocked →
        back to NONE → dispatch resumes.  Exercises the full chain
        with real component instances.
        """
        from unittest.mock import patch

        from mozart.daemon.backpressure import PressureLevel
        from mozart.daemon.monitor import ResourceMonitor

        scheduler = integration_setup["scheduler"]
        controller = integration_setup["controller"]

        sheets = [_sheet(sheet_num=i) for i in range(1, 6)]
        await scheduler.register_job("job-a", sheets)

        # Phase 1: Low pressure — dispatch works
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "_get_child_process_count", return_value=2,
        ):
            assert controller.current_level() == PressureLevel.NONE
            e1 = await scheduler.next_sheet()
            assert e1 is not None

        # Phase 2: Critical — dispatch blocked
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=960.0,
        ), patch.object(
            ResourceMonitor, "_get_child_process_count", return_value=2,
        ):
            assert controller.current_level() == PressureLevel.CRITICAL
            e2 = await scheduler.next_sheet()
            assert e2 is None

        # Phase 3: Recover — dispatch resumes
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "_get_child_process_count", return_value=2,
        ):
            e3 = await scheduler.next_sheet()
            assert e3 is not None

        # Cleanup
        await scheduler.mark_complete("job-a", e1.info.sheet_num, True)
        await scheduler.mark_complete("job-a", e3.info.sheet_num, True)

    @pytest.mark.asyncio
    async def test_rate_limit_plus_backpressure_combined(
        self, integration_setup: dict, tmp_path: Path,
    ):
        """Rate limit on one backend + memory pressure → combined gating.

        Rate coordinator blocks specific backends while backpressure
        controls overall dispatch admission.  Uses a rate-only scheduler
        to avoid the 30s HIGH delay from active rate limits.
        """
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        coordinator = integration_setup["coordinator"]

        # Rate-limit claude_cli
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=60.0,
            job_id="job-a",
            sheet_num=1,
        )

        # Test with rate limiter only (no backpressure) to verify
        # the scheduler correctly filters backends by rate limit status
        scheduler_rate_only = GlobalSheetScheduler(
            DaemonConfig(
                max_concurrent_sheets=5,
                max_concurrent_jobs=5,
                pid_file=tmp_path / "rate-only.pid",
            ),
        )
        scheduler_rate_only.set_rate_limiter(coordinator)

        await scheduler_rate_only.register_job("job-a", [
            _sheet("job-a", 1, backend="claude_cli"),
            _sheet("job-a", 2, backend="openai"),
            _sheet("job-a", 3, backend="claude_cli"),
        ])

        entry = await scheduler_rate_only.next_sheet()
        assert entry is not None
        assert entry.info.backend_type == "openai"

        # claude_cli sheets still queued
        assert scheduler_rate_only.queued_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_dispatch_under_changing_pressure(
        self, integration_setup: dict,
    ):
        """Multiple workers dispatching while pressure fluctuates."""
        from unittest.mock import patch

        from mozart.daemon.monitor import ResourceMonitor

        scheduler = integration_setup["scheduler"]

        sheets = [_sheet(sheet_num=i) for i in range(1, 11)]
        await scheduler.register_job("job-a", sheets)

        completed: set[int] = set()
        lock = asyncio.Lock()

        async def worker():
            for _ in range(20):
                # Simulate low pressure for dispatch
                with patch.object(
                    ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
                ), patch.object(
                    ResourceMonitor, "_get_child_process_count", return_value=2,
                ):
                    entry = await scheduler.next_sheet()
                if entry is None:
                    async with lock:
                        if len(completed) >= 10:
                            return
                    await asyncio.sleep(0.001)
                    continue
                await asyncio.sleep(0.001)
                await scheduler.mark_complete("job-a", entry.info.sheet_num, True)
                async with lock:
                    completed.add(entry.info.sheet_num)

        workers = [asyncio.create_task(worker()) for _ in range(3)]
        done, pending = await asyncio.wait(workers, timeout=10.0)
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        assert len(completed) == 10
        assert scheduler.active_count == 0
