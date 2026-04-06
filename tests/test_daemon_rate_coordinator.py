"""Tests for mozart.daemon.rate_coordinator module.

Covers RateLimitCoordinator: reporting, querying via the RateLimitChecker
protocol, event pruning, input validation (NaN/inf/negative/huge waits),
concurrent stress (parallel reports, queries, and prune), report-expire-
re-report cycles, and integration with the GlobalSheetScheduler.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from marianne.daemon.rate_coordinator import RateLimitCoordinator, RateLimitEvent


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def coordinator() -> RateLimitCoordinator:
    """Fresh coordinator instance."""
    return RateLimitCoordinator()


# ─── Basic Reporting ───────────────────────────────────────────────────


class TestReporting:
    """Tests for report_rate_limit."""

    @pytest.mark.asyncio
    async def test_report_creates_active_limit(
        self, coordinator: RateLimitCoordinator,
    ):
        """Reporting a limit makes the backend rate-limited."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=30.0,
            job_id="job-a",
            sheet_num=1,
        )

        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is True
        assert remaining > 0
        assert remaining <= 30.0

    @pytest.mark.asyncio
    async def test_report_extends_existing_limit(
        self, coordinator: RateLimitCoordinator,
    ):
        """A longer wait replaces a shorter existing limit."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=10.0,
            job_id="job-a",
            sheet_num=1,
        )
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=60.0,
            job_id="job-b",
            sheet_num=2,
        )

        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is True
        # Should reflect the longer limit, not the shorter one
        assert remaining > 10.0

    @pytest.mark.asyncio
    async def test_report_doesnt_shorten_limit(
        self, coordinator: RateLimitCoordinator,
    ):
        """A shorter wait does NOT reduce an existing longer limit."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=60.0,
            job_id="job-a",
            sheet_num=1,
        )
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=5.0,
            job_id="job-b",
            sheet_num=2,
        )

        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is True
        assert remaining > 5.0  # Still waiting for the longer one

    @pytest.mark.asyncio
    async def test_different_backends_independent(
        self, coordinator: RateLimitCoordinator,
    ):
        """Limits on one backend don't affect others."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=30.0,
            job_id="job-a",
            sheet_num=1,
        )

        is_limited, remaining = await coordinator.is_rate_limited("openai")
        assert is_limited is False
        assert remaining == 0.0

    @pytest.mark.asyncio
    async def test_report_stores_event(
        self, coordinator: RateLimitCoordinator,
    ):
        """Reported events are stored and accessible."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=30.0,
            job_id="job-a",
            sheet_num=3,
        )

        events = coordinator.recent_events
        assert len(events) == 1
        assert events[0].backend_type == "claude_cli"
        assert events[0].job_id == "job-a"
        assert events[0].sheet_num == 3
        assert events[0].suggested_wait_seconds == 30.0


# ─── Protocol Compliance ──────────────────────────────────────────────


class TestProtocolCompliance:
    """Tests that is_rate_limited satisfies the RateLimitChecker protocol."""

    @pytest.mark.asyncio
    async def test_returns_tuple_when_limited(
        self, coordinator: RateLimitCoordinator,
    ):
        """Returns (True, float) when limited."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=30.0,
            job_id="job-a",
            sheet_num=1,
        )

        result = await coordinator.is_rate_limited("claude_cli")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result[0] is True
        assert isinstance(result[1], float)

    @pytest.mark.asyncio
    async def test_returns_tuple_when_not_limited(
        self, coordinator: RateLimitCoordinator,
    ):
        """Returns (False, 0.0) when not limited."""
        result = await coordinator.is_rate_limited("claude_cli")
        assert result == (False, 0.0)

    @pytest.mark.asyncio
    async def test_accepts_model_parameter(
        self, coordinator: RateLimitCoordinator,
    ):
        """The model parameter is accepted (protocol compat)."""
        result = await coordinator.is_rate_limited(
            "claude_cli", model="claude-sonnet-4-5-20250929",
        )
        assert result == (False, 0.0)


# ─── Properties ──────────────────────────────────────────────────────


class TestProperties:
    """Tests for public properties."""

    @pytest.mark.asyncio
    async def test_active_limits_property(
        self, coordinator: RateLimitCoordinator,
    ):
        """active_limits returns only currently active limits."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=30.0,
            job_id="job-a",
            sheet_num=1,
        )
        await coordinator.report_rate_limit(
            backend_type="openai",
            wait_seconds=60.0,
            job_id="job-b",
            sheet_num=2,
        )

        limits = coordinator.active_limits
        assert "claude_cli" in limits
        assert "openai" in limits
        assert limits["claude_cli"] > 0
        assert limits["openai"] > 0
        assert limits["openai"] > limits["claude_cli"]

    @pytest.mark.asyncio
    async def test_active_limits_excludes_expired(
        self, coordinator: RateLimitCoordinator,
    ):
        """Expired limits are not included in active_limits."""
        # Report a limit that expires almost immediately
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=0.01,
            job_id="job-a",
            sheet_num=1,
        )
        await asyncio.sleep(0.02)  # Let it expire

        limits = coordinator.active_limits
        assert "claude_cli" not in limits

    @pytest.mark.asyncio
    async def test_recent_events_ordered_newest_first(
        self, coordinator: RateLimitCoordinator,
    ):
        """recent_events returns newest events first."""
        for i in range(3):
            await coordinator.report_rate_limit(
                backend_type="claude_cli",
                wait_seconds=float(i + 1),
                job_id=f"job-{i}",
                sheet_num=i + 1,
            )

        events = coordinator.recent_events
        assert len(events) == 3
        # Most recent first
        assert events[0].job_id == "job-2"
        assert events[-1].job_id == "job-0"


# ─── Event Pruning ────────────────────────────────────────────────────


class TestEventPruning:
    """Tests for event list housekeeping."""

    @pytest.mark.asyncio
    async def test_old_events_pruned_on_report(
        self, coordinator: RateLimitCoordinator,
    ):
        """Events older than 1 hour are pruned when new events arrive."""
        # Manually inject an old event
        old_event = RateLimitEvent(
            backend_type="claude_cli",
            detected_at=time.monotonic() - 7200,  # 2 hours ago
            suggested_wait_seconds=30.0,
            job_id="old-job",
            sheet_num=1,
        )
        coordinator._events.append(old_event)
        assert len(coordinator._events) == 1

        # Report a new event — old one should be pruned
        await coordinator.report_rate_limit(
            backend_type="openai",
            wait_seconds=10.0,
            job_id="new-job",
            sheet_num=1,
        )

        assert len(coordinator._events) == 1
        assert coordinator._events[0].job_id == "new-job"


# ─── P008: prune_stale() Direct Tests ────────────────────────────────


class TestPruneStale:
    """Direct tests for prune_stale() — the primary pruning path."""

    @pytest.mark.asyncio
    async def test_prune_removes_old_events(
        self, coordinator: RateLimitCoordinator,
    ):
        """prune_stale() removes events older than 1 hour."""
        old_event = RateLimitEvent(
            backend_type="claude_cli",
            detected_at=time.monotonic() - 7200,
            suggested_wait_seconds=30.0,
            job_id="old-job",
            sheet_num=1,
        )
        coordinator._events.append(old_event)

        removed = await coordinator.prune_stale()
        assert removed == 1
        assert len(coordinator._events) == 0

    @pytest.mark.asyncio
    async def test_prune_keeps_recent_events(
        self, coordinator: RateLimitCoordinator,
    ):
        """prune_stale() preserves events from the last hour."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=30.0,
            job_id="recent-job",
            sheet_num=1,
        )

        removed = await coordinator.prune_stale()
        assert removed == 0
        assert len(coordinator._events) == 1

    @pytest.mark.asyncio
    async def test_prune_removes_expired_active_limits(
        self, coordinator: RateLimitCoordinator,
    ):
        """prune_stale() clears expired entries from _active_limits."""
        # Inject an expired limit
        coordinator._active_limits["old_backend"] = time.monotonic() - 10

        await coordinator.prune_stale()
        assert "old_backend" not in coordinator._active_limits

    @pytest.mark.asyncio
    async def test_prune_keeps_active_limits(
        self, coordinator: RateLimitCoordinator,
    ):
        """prune_stale() preserves still-active limits."""
        coordinator._active_limits["active_backend"] = time.monotonic() + 60

        await coordinator.prune_stale()
        assert "active_backend" in coordinator._active_limits

    @pytest.mark.asyncio
    async def test_prune_returns_correct_count(
        self, coordinator: RateLimitCoordinator,
    ):
        """prune_stale() returns the number of events removed."""
        now = time.monotonic()
        for i in range(5):
            coordinator._events.append(RateLimitEvent(
                backend_type="cli",
                detected_at=now - 7200,  # 2 hours ago
                suggested_wait_seconds=10.0,
                job_id=f"old-{i}",
                sheet_num=i,
            ))
        coordinator._events.append(RateLimitEvent(
            backend_type="cli",
            detected_at=now,  # fresh
            suggested_wait_seconds=10.0,
            job_id="fresh",
            sheet_num=99,
        ))

        removed = await coordinator.prune_stale()
        assert removed == 5
        assert len(coordinator._events) == 1
        assert coordinator._events[0].job_id == "fresh"

    @pytest.mark.asyncio
    async def test_prune_on_empty_coordinator(
        self, coordinator: RateLimitCoordinator,
    ):
        """prune_stale() on empty state is a no-op."""
        removed = await coordinator.prune_stale()
        assert removed == 0


# ─── P004/P005: Input Validation Tests ────────────────────────────────


class TestInputValidation:
    """Tests for wait_seconds clamping (P004 upper cap, P005 non-positive)."""

    @pytest.mark.asyncio
    async def test_negative_wait_clamped_to_zero(
        self, coordinator: RateLimitCoordinator,
    ):
        """Negative wait_seconds is clamped to 0 (no blocking)."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=-10.0,
            job_id="job-a",
            sheet_num=1,
        )

        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        # With wait=0, the resume time is now, so it should not be limited
        assert is_limited is False or remaining <= 0.01

    @pytest.mark.asyncio
    async def test_zero_wait_not_blocking(
        self, coordinator: RateLimitCoordinator,
    ):
        """wait_seconds=0 should not create a blocking limit."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=0.0,
            job_id="job-a",
            sheet_num=1,
        )

        is_limited, _ = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is False

    @pytest.mark.asyncio
    async def test_huge_wait_capped(
        self, coordinator: RateLimitCoordinator,
    ):
        """wait_seconds > MAX_WAIT_SECONDS is capped."""
        from marianne.daemon.rate_coordinator import MAX_WAIT_SECONDS

        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=999999.0,  # ~11.5 days
            job_id="job-a",
            sheet_num=1,
        )

        _, remaining = await coordinator.is_rate_limited("claude_cli")
        # Should be capped at MAX_WAIT_SECONDS
        assert remaining <= MAX_WAIT_SECONDS


# ─── P009: NaN/Inf wait_seconds Handling ─────────────────────────────


class TestNonFiniteWaitSeconds:
    """Tests for NaN and inf wait_seconds handling (validates P001).

    Backend response parsing can produce NaN or inf values.  The
    coordinator must not propagate these into the resume-time
    calculation, which would cause permanent or undefined blocking.
    """

    @pytest.mark.asyncio
    async def test_nan_wait_treated_as_zero(
        self, coordinator: RateLimitCoordinator,
    ):
        """NaN wait_seconds is clamped to 0 — no blocking."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=float("nan"),
            job_id="job-a",
            sheet_num=1,
        )

        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is False or remaining <= 0.01

    @pytest.mark.asyncio
    async def test_inf_wait_treated_as_zero(
        self, coordinator: RateLimitCoordinator,
    ):
        """Positive inf wait_seconds is clamped to 0 — no permanent blocking."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=float("inf"),
            job_id="job-a",
            sheet_num=1,
        )

        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is False or remaining <= 0.01

    @pytest.mark.asyncio
    async def test_negative_inf_wait_treated_as_zero(
        self, coordinator: RateLimitCoordinator,
    ):
        """Negative inf wait_seconds is clamped to 0."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=float("-inf"),
            job_id="job-a",
            sheet_num=1,
        )

        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is False or remaining <= 0.01

    @pytest.mark.asyncio
    async def test_nan_event_still_recorded(
        self, coordinator: RateLimitCoordinator,
    ):
        """NaN wait results in an event recorded with wait=0."""
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=float("nan"),
            job_id="job-a",
            sheet_num=1,
        )

        events = coordinator.recent_events
        assert len(events) == 1
        assert events[0].suggested_wait_seconds == 0.0


# ─── Scheduler Integration ────────────────────────────────────────────


# ─── P021: Concurrent report_rate_limit() Stress ─────────────────────


class TestConcurrentRateLimitStress:
    """Stress tests: concurrent report_rate_limit() and is_rate_limited()."""

    @pytest.mark.asyncio
    async def test_concurrent_reports_no_data_loss(
        self, coordinator: RateLimitCoordinator,
    ):
        """Many concurrent report_rate_limit() calls don't lose events."""
        num_reports = 50

        async def reporter(idx: int):
            await coordinator.report_rate_limit(
                backend_type="claude_cli",
                wait_seconds=float(idx % 10 + 1),
                job_id=f"job-{idx}",
                sheet_num=idx,
            )

        tasks = [asyncio.create_task(reporter(i)) for i in range(num_reports)]
        await asyncio.gather(*tasks)

        events = coordinator.recent_events
        assert len(events) == num_reports

    @pytest.mark.asyncio
    async def test_concurrent_reports_and_queries(
        self, coordinator: RateLimitCoordinator,
    ):
        """Concurrent reports + is_rate_limited queries don't crash or deadlock."""
        stop = asyncio.Event()

        async def reporter():
            for i in range(30):
                await coordinator.report_rate_limit(
                    backend_type=f"backend-{i % 3}",
                    wait_seconds=0.1,
                    job_id=f"job-{i}",
                    sheet_num=i,
                )
                await asyncio.sleep(0.001)
            stop.set()

        query_count = 0

        async def querier():
            nonlocal query_count
            while not stop.is_set():
                await coordinator.is_rate_limited("backend-0")
                await coordinator.is_rate_limited("backend-1")
                await coordinator.is_rate_limited("backend-2")
                query_count += 1
                await asyncio.sleep(0.001)

        # Launch 1 reporter and 3 queriers
        tasks = [
            asyncio.create_task(reporter()),
            asyncio.create_task(querier()),
            asyncio.create_task(querier()),
            asyncio.create_task(querier()),
        ]

        await asyncio.wait(tasks, timeout=10.0)

        for t in tasks:
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        # Reporter finished all 30 reports
        assert len(coordinator.recent_events) == 30
        assert query_count > 0

    @pytest.mark.asyncio
    async def test_concurrent_reports_different_backends(
        self, coordinator: RateLimitCoordinator,
    ):
        """Concurrent reports to different backends maintain correct per-backend limits."""
        num_backends = 10
        reports_per_backend = 10

        async def report_backend(backend_idx: int):
            for j in range(reports_per_backend):
                await coordinator.report_rate_limit(
                    backend_type=f"backend-{backend_idx}",
                    wait_seconds=5.0,
                    job_id=f"job-{backend_idx}-{j}",
                    sheet_num=j,
                )

        tasks = [
            asyncio.create_task(report_backend(i))
            for i in range(num_backends)
        ]
        await asyncio.gather(*tasks)

        # All backends should be limited
        for i in range(num_backends):
            is_limited, remaining = await coordinator.is_rate_limited(f"backend-{i}")
            assert is_limited is True
            assert remaining > 0

        # Unlisted backend should not be limited
        is_limited, _ = await coordinator.is_rate_limited("nonexistent")
        assert is_limited is False

    @pytest.mark.asyncio
    async def test_concurrent_prune_and_report(
        self, coordinator: RateLimitCoordinator,
    ):
        """Concurrent prune_stale() + report_rate_limit() don't corrupt state."""
        stop = asyncio.Event()

        async def reporter():
            for i in range(30):
                await coordinator.report_rate_limit(
                    backend_type="claude_cli",
                    wait_seconds=0.05,
                    job_id=f"job-{i}",
                    sheet_num=i,
                )
                await asyncio.sleep(0.001)
            stop.set()

        async def pruner():
            while not stop.is_set():
                await coordinator.prune_stale()
                await asyncio.sleep(0.005)

        tasks = [
            asyncio.create_task(reporter()),
            asyncio.create_task(pruner()),
            asyncio.create_task(pruner()),
        ]

        await asyncio.wait(tasks, timeout=10.0)
        for t in tasks:
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        # Events list should still be valid (no crashes)
        events = coordinator.recent_events
        assert isinstance(events, list)


# ─── P016: Report → Expire → Re-report Cycle ───────────────────────


class TestReportExpireReportCycle:
    """Test report → expire → re-report cycle on the same backend (P016).

    Validates that a backend can transition through:
    1. Limited (after report)
    2. Not limited (after expiry)
    3. Limited again (after re-report)
    without stale state leaking between cycles.
    """

    @pytest.mark.asyncio
    async def test_full_cycle_single_backend(
        self, coordinator: RateLimitCoordinator,
    ):
        """Report → wait for expiry → verify clear → re-report → verify active."""
        # Phase 1: Report a short limit
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=0.03,
            job_id="job-a",
            sheet_num=1,
        )
        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is True
        assert remaining > 0

        # Phase 2: Wait for expiry
        await asyncio.sleep(0.05)
        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is False
        assert remaining == 0.0

        # Phase 3: Re-report on the same backend
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=0.03,
            job_id="job-b",
            sheet_num=2,
        )
        is_limited, remaining = await coordinator.is_rate_limited("claude_cli")
        assert is_limited is True
        assert remaining > 0

        # Events should contain both reports
        events = coordinator.recent_events
        assert len(events) == 2
        assert events[0].job_id == "job-b"  # most recent first
        assert events[1].job_id == "job-a"

    @pytest.mark.asyncio
    async def test_cycle_multiple_backends_independent(
        self, coordinator: RateLimitCoordinator,
    ):
        """Expire/re-report on one backend doesn't affect another."""
        # Report on both backends
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=0.03,
            job_id="job-a",
            sheet_num=1,
        )
        await coordinator.report_rate_limit(
            backend_type="openai",
            wait_seconds=60.0,
            job_id="job-b",
            sheet_num=1,
        )

        # Wait for claude_cli to expire
        await asyncio.sleep(0.05)

        # claude_cli expired, openai still active
        is_limited_cli, _ = await coordinator.is_rate_limited("claude_cli")
        is_limited_oai, _ = await coordinator.is_rate_limited("openai")
        assert is_limited_cli is False
        assert is_limited_oai is True

        # Re-report claude_cli — openai unchanged
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=30.0,
            job_id="job-a",
            sheet_num=2,
        )
        is_limited_cli, _ = await coordinator.is_rate_limited("claude_cli")
        is_limited_oai, _ = await coordinator.is_rate_limited("openai")
        assert is_limited_cli is True
        assert is_limited_oai is True

    @pytest.mark.asyncio
    async def test_rapid_report_expire_cycles(
        self, coordinator: RateLimitCoordinator,
    ):
        """Multiple rapid report/expire cycles don't accumulate stale state."""
        for i in range(10):
            await coordinator.report_rate_limit(
                backend_type="claude_cli",
                wait_seconds=0.01,
                job_id=f"job-{i}",
                sheet_num=i,
            )
            await asyncio.sleep(0.02)

            is_limited, _ = await coordinator.is_rate_limited("claude_cli")
            assert is_limited is False, f"Cycle {i}: should have expired"

        # All 10 events should be recorded
        events = coordinator.recent_events
        assert len(events) == 10

        # No active limits remain
        assert len(coordinator.active_limits) == 0


class TestSchedulerIntegration:
    """Test that RateLimitCoordinator works with GlobalSheetScheduler."""

    @pytest.mark.asyncio
    async def test_coordinator_as_rate_limiter(self):
        """Coordinator satisfies RateLimitChecker protocol in scheduler."""
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.scheduler import GlobalSheetScheduler, SheetInfo

        config = DaemonConfig(max_concurrent_sheets=5, max_concurrent_jobs=5)
        scheduler = GlobalSheetScheduler(config)
        coordinator = RateLimitCoordinator()

        # Wire up
        scheduler.set_rate_limiter(coordinator)

        # Register sheets with different backends
        sheets = [
            SheetInfo(job_id="job-a", sheet_num=1, backend_type="claude_cli"),
            SheetInfo(job_id="job-a", sheet_num=2, backend_type="openai"),
        ]
        await scheduler.register_job("job-a", sheets)

        # Rate-limit claude_cli
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=60.0,
            job_id="job-a",
            sheet_num=1,
        )

        # Only the openai sheet should dispatch
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.backend_type == "openai"

        # claude_cli sheet stays in queue
        assert scheduler.queued_count == 1

    @pytest.mark.asyncio
    async def test_coordinator_clears_after_expiry(self):
        """After limit expires, scheduler can dispatch that backend."""
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.scheduler import GlobalSheetScheduler, SheetInfo

        config = DaemonConfig(max_concurrent_sheets=5, max_concurrent_jobs=5)
        scheduler = GlobalSheetScheduler(config)
        coordinator = RateLimitCoordinator()
        scheduler.set_rate_limiter(coordinator)

        sheets = [
            SheetInfo(job_id="job-a", sheet_num=1, backend_type="claude_cli"),
        ]
        await scheduler.register_job("job-a", sheets)

        # Rate-limit for a very short time
        await coordinator.report_rate_limit(
            backend_type="claude_cli",
            wait_seconds=0.01,
            job_id="job-a",
            sheet_num=1,
        )

        # Wait for limit to expire
        await asyncio.sleep(0.02)

        # Now it should dispatch
        entry = await scheduler.next_sheet()
        assert entry is not None
        assert entry.info.backend_type == "claude_cli"
