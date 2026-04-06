"""Tests for DaemonAnalytics and analytics API routes."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from marianne.core.checkpoint import (
    CheckpointErrorRecord,
    CheckpointState,
    JobStatus,
    SheetState,
    SheetStatus,
)
from marianne.dashboard.app import create_app
from marianne.dashboard.routes.analytics import set_analytics
from marianne.dashboard.services.analytics import DaemonAnalytics
from marianne.state.base import StateBackend

# ============================================================================
# Fixtures
# ============================================================================


class MockStateBackend(StateBackend):
    """In-memory mock StateBackend for analytics tests."""

    def __init__(self, jobs: list[CheckpointState] | None = None) -> None:
        self._jobs = jobs or []

    async def list_jobs(self) -> list[CheckpointState]:
        return list(self._jobs)

    async def load(self, job_id: str) -> CheckpointState | None:
        for j in self._jobs:
            if j.job_id == job_id:
                return j
        return None

    async def save(self, state: CheckpointState) -> None:
        raise NotImplementedError

    async def delete(self, job_id: str) -> bool:
        raise NotImplementedError

    async def get_next_sheet(self, job_id: str) -> int | None:
        raise NotImplementedError

    async def mark_sheet_status(
        self,
        job_id: str,
        sheet_num: int,
        status: SheetStatus,
        error_message: str | None = None,
    ) -> None:
        raise NotImplementedError


def _make_completed_job(
    job_id: str = "job-1",
    job_name: str = "Completed Job",
    total_cost: float = 1.50,
    sheet_durations: list[float] | None = None,
    validation_details: list[dict] | None = None,
    error_history: list[CheckpointErrorRecord] | None = None,
) -> CheckpointState:
    """Build a completed job with configurable sheet data."""
    durations = sheet_durations or [120.0, 180.0, 60.0]
    sheets: dict[int, SheetState] = {}
    for i, dur in enumerate(durations, start=1):
        sheet = SheetState(
            sheet_num=i,
            status=SheetStatus.COMPLETED,
            attempt_count=1,
            validation_passed=True,
            execution_duration_seconds=dur,
            estimated_cost=total_cost / len(durations),
        )
        if validation_details is not None:
            sheet.validation_details = validation_details
        if error_history is not None:
            sheet.error_history = error_history
        sheets[i] = sheet

    return CheckpointState(
        job_id=job_id,
        job_name=job_name,
        total_sheets=len(durations),
        status=JobStatus.COMPLETED,
        last_completed_sheet=len(durations),
        completed_at=datetime.now(UTC),
        total_estimated_cost=total_cost,
        sheets=sheets,
    )


def _make_failed_job(
    job_id: str = "job-fail",
    job_name: str = "Failed Job",
) -> CheckpointState:
    """Build a failed job with error history."""
    sheets: dict[int, SheetState] = {
        1: SheetState(
            sheet_num=1,
            status=SheetStatus.COMPLETED,
            attempt_count=1,
            validation_passed=True,
            execution_duration_seconds=90.0,
        ),
        2: SheetState(
            sheet_num=2,
            status=SheetStatus.FAILED,
            attempt_count=3,
            error_message="Process timeout",
            execution_duration_seconds=300.0,
            error_history=[
                CheckpointErrorRecord(
                    error_type="transient",
                    error_code="E001",
                    error_message="Connection reset",
                    attempt_number=1,
                ),
                CheckpointErrorRecord(
                    error_type="rate_limit",
                    error_code="E301",
                    error_message="Rate limited",
                    attempt_number=2,
                ),
                CheckpointErrorRecord(
                    error_type="permanent",
                    error_code="E501",
                    error_message="Process timeout",
                    attempt_number=3,
                ),
            ],
        ),
    }

    return CheckpointState(
        job_id=job_id,
        job_name=job_name,
        total_sheets=3,
        status=JobStatus.FAILED,
        last_completed_sheet=1,
        total_estimated_cost=0.75,
        sheets=sheets,
    )


def _make_running_job(
    job_id: str = "job-run",
    job_name: str = "Running Job",
) -> CheckpointState:
    return CheckpointState(
        job_id=job_id,
        job_name=job_name,
        total_sheets=5,
        status=JobStatus.RUNNING,
        last_completed_sheet=2,
        current_sheet=3,
        total_estimated_cost=0.30,
        sheets={
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                execution_duration_seconds=100.0,
            ),
            2: SheetState(
                sheet_num=2,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                execution_duration_seconds=150.0,
            ),
            3: SheetState(
                sheet_num=3,
                status=SheetStatus.IN_PROGRESS,
                attempt_count=1,
            ),
        },
    )


@pytest.fixture
def sample_jobs() -> list[CheckpointState]:
    """A mix of completed, failed, and running jobs."""
    return [
        _make_completed_job("job-1", "Completed 1", total_cost=1.50),
        _make_completed_job("job-2", "Completed 2", total_cost=2.00),
        _make_failed_job("job-3", "Failed 1"),
        _make_running_job("job-4", "Running 1"),
    ]


@pytest.fixture
def mock_backend(sample_jobs: list[CheckpointState]) -> MockStateBackend:
    return MockStateBackend(sample_jobs)


@pytest.fixture
def analytics(mock_backend: MockStateBackend) -> DaemonAnalytics:
    return DaemonAnalytics(mock_backend, cache_ttl=0.0)  # No caching in tests


# ============================================================================
# DaemonAnalytics Unit Tests
# ============================================================================


class TestGetStats:
    @pytest.mark.asyncio
    async def test_counts_by_status(self, analytics: DaemonAnalytics) -> None:
        stats = await analytics.get_stats()
        assert stats["total_jobs"] == 4
        assert stats["completed_jobs"] == 2
        assert stats["failed_jobs"] == 1
        assert stats["running_jobs"] == 1

    @pytest.mark.asyncio
    async def test_success_rate(self, analytics: DaemonAnalytics) -> None:
        stats = await analytics.get_stats()
        # 2 completed, 1 failed → 2/3 = 66.7%
        assert stats["success_rate"] == 66.7

    @pytest.mark.asyncio
    async def test_total_spend(self, analytics: DaemonAnalytics) -> None:
        stats = await analytics.get_stats()
        # 1.50 + 2.00 + 0.75 + 0.30 = 4.55
        assert stats["total_spend"] == 4.55

    @pytest.mark.asyncio
    async def test_throughput(self, analytics: DaemonAnalytics) -> None:
        stats = await analytics.get_stats()
        assert stats["throughput_sheets_per_hour"] > 0

    @pytest.mark.asyncio
    async def test_empty_jobs(self) -> None:
        backend = MockStateBackend([])
        analytics = DaemonAnalytics(backend, cache_ttl=0.0)
        stats = await analytics.get_stats()
        assert stats["total_jobs"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["total_spend"] == 0.0
        assert stats["throughput_sheets_per_hour"] == 0.0


class TestCostRollup:
    @pytest.mark.asyncio
    async def test_by_job(self, analytics: DaemonAnalytics) -> None:
        rollup = await analytics.cost_rollup()
        assert "job-1" in rollup["by_job"]
        assert rollup["by_job"]["job-1"] == 1.5
        assert rollup["by_job"]["job-2"] == 2.0

    @pytest.mark.asyncio
    async def test_total_and_avg(self, analytics: DaemonAnalytics) -> None:
        rollup = await analytics.cost_rollup()
        assert rollup["total_spend"] == 4.55
        assert rollup["jobs_with_cost"] == 4
        assert rollup["avg_cost_per_job"] > 0


class TestValidationStats:
    @pytest.mark.asyncio
    async def test_with_validation_details(self) -> None:
        details = [
            {"rule_type": "file_exists", "passed": True},
            {"rule_type": "file_exists", "passed": False},
            {"rule_type": "content_match", "passed": True},
        ]
        job = _make_completed_job(
            "job-val", total_cost=1.0, validation_details=details,
        )
        backend = MockStateBackend([job])
        analytics = DaemonAnalytics(backend, cache_ttl=0.0)

        result = await analytics.validation_stats()
        # 3 sheets × 3 details each = 9 total checks
        assert result["total_checks"] == 9
        # Each sheet has 2 file_exists (1 pass, 1 fail) → 3 sheets → 3 pass / 6 total = 50.0
        assert result["by_rule_type"]["file_exists"] == 50.0
        # Each sheet has 1 content_match pass → 3/3 = 100.0
        assert result["by_rule_type"]["content_match"] == 100.0

    @pytest.mark.asyncio
    async def test_no_validations(self) -> None:
        job = _make_completed_job("job-noval", total_cost=0.5)
        backend = MockStateBackend([job])
        analytics = DaemonAnalytics(backend, cache_ttl=0.0)

        result = await analytics.validation_stats()
        assert result["total_checks"] == 0
        assert result["overall_pass_rate"] == 0.0


class TestErrorBreakdown:
    @pytest.mark.asyncio
    async def test_error_categories(self) -> None:
        job = _make_failed_job()
        backend = MockStateBackend([job])
        analytics = DaemonAnalytics(backend, cache_ttl=0.0)

        result = await analytics.error_breakdown()
        assert result["total_errors"] == 3
        assert result["by_category"]["transient"] == 1
        assert result["by_category"]["rate_limit"] == 1
        assert result["by_category"]["permanent"] == 1

    @pytest.mark.asyncio
    async def test_no_errors(self) -> None:
        job = _make_completed_job("job-clean", total_cost=1.0)
        backend = MockStateBackend([job])
        analytics = DaemonAnalytics(backend, cache_ttl=0.0)

        result = await analytics.error_breakdown()
        assert result["total_errors"] == 0
        assert result["by_category"]["transient"] == 0


class TestDurationStats:
    @pytest.mark.asyncio
    async def test_avg_duration(self) -> None:
        job = _make_completed_job(
            "job-dur", total_cost=1.0, sheet_durations=[100.0, 200.0, 300.0],
        )
        backend = MockStateBackend([job])
        analytics = DaemonAnalytics(backend, cache_ttl=0.0)

        result = await analytics.duration_stats()
        assert result["avg_sheet_duration_seconds"] == 200.0
        assert result["total_sheets_with_duration"] == 3

    @pytest.mark.asyncio
    async def test_slowest_sheets(self) -> None:
        job = _make_completed_job(
            "job-slow", total_cost=1.0, sheet_durations=[10.0, 500.0, 30.0],
        )
        backend = MockStateBackend([job])
        analytics = DaemonAnalytics(backend, cache_ttl=0.0)

        result = await analytics.duration_stats()
        assert result["slowest_sheets"][0]["duration_seconds"] == 500.0
        assert result["slowest_sheets"][0]["sheet_num"] == 2

    @pytest.mark.asyncio
    async def test_no_durations(self) -> None:
        job = CheckpointState(
            job_id="job-empty",
            job_name="Empty",
            total_sheets=1,
            status=JobStatus.PENDING,
        )
        backend = MockStateBackend([job])
        analytics = DaemonAnalytics(backend, cache_ttl=0.0)

        result = await analytics.duration_stats()
        assert result["avg_sheet_duration_seconds"] == 0.0
        assert result["slowest_sheets"] == []


class TestCaching:
    @pytest.mark.asyncio
    async def test_cache_returns_same_result(self) -> None:
        backend = MockStateBackend([_make_completed_job()])
        analytics = DaemonAnalytics(backend, cache_ttl=60.0)  # long TTL

        first = await analytics.get_stats()
        # Swap backend data — should still get cached result
        backend._jobs = []
        second = await analytics.get_stats()
        assert first == second

    @pytest.mark.asyncio
    async def test_expired_cache_refetches(self) -> None:
        backend = MockStateBackend([_make_completed_job()])
        analytics = DaemonAnalytics(backend, cache_ttl=0.0)  # immediate expiry

        await analytics.get_stats()  # prime cache
        backend._jobs = []
        second = await analytics.get_stats()
        assert second["total_jobs"] == 0  # refetched empty list


# ============================================================================
# Route Tests
# ============================================================================


@pytest.fixture
def app_with_analytics(mock_backend: MockStateBackend) -> TestClient:
    """Create a test app with analytics routes wired up."""
    from marianne.dashboard.routes.analytics import router as analytics_router

    app = create_app(state_backend=mock_backend)
    analytics = DaemonAnalytics(mock_backend, cache_ttl=0.0)
    set_analytics(analytics)
    app.include_router(analytics_router)
    return TestClient(app)


class TestAnalyticsRoutes:
    def test_stats_endpoint(self, app_with_analytics: TestClient) -> None:
        resp = app_with_analytics.get("/api/analytics/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_jobs" in data
        assert "success_rate" in data
        assert "total_spend" in data
        assert "throughput_sheets_per_hour" in data

    def test_costs_endpoint(self, app_with_analytics: TestClient) -> None:
        resp = app_with_analytics.get("/api/analytics/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert "by_job" in data
        assert "total_spend" in data
        assert "avg_cost_per_job" in data

    def test_validations_endpoint(self, app_with_analytics: TestClient) -> None:
        resp = app_with_analytics.get("/api/analytics/validations")
        assert resp.status_code == 200
        data = resp.json()
        assert "overall_pass_rate" in data
        assert "total_checks" in data

    def test_errors_endpoint(self, app_with_analytics: TestClient) -> None:
        resp = app_with_analytics.get("/api/analytics/errors")
        assert resp.status_code == 200
        data = resp.json()
        assert "by_category" in data
        assert "total_errors" in data
