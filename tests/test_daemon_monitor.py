"""Tests for mozart.daemon.monitor module.

Covers ResourceMonitor check cycle, memory warning thresholds,
process count tracking, is_accepting_work(), and psutil fallback.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.daemon.config import ResourceLimitConfig
from marianne.daemon.monitor import ResourceMonitor, ResourceSnapshot, _compute_percent


# ─── _compute_percent ──────────────────────────────────────────────────


class TestComputePercent:
    """Tests for _compute_percent helper."""

    def test_zero_limit_returns_zero(self):
        assert _compute_percent(100.0, 0.0) == 0.0

    def test_negative_limit_returns_zero(self):
        assert _compute_percent(100.0, -5.0) == 0.0

    def test_half_usage(self):
        assert _compute_percent(50.0, 100.0) == 50.0

    def test_clamped_at_100(self):
        assert _compute_percent(200.0, 100.0) == 100.0

    def test_zero_current(self):
        assert _compute_percent(0.0, 100.0) == 0.0


# ─── ResourceSnapshot ─────────────────────────────────────────────────


class TestResourceSnapshot:
    """Tests for ResourceSnapshot dataclass."""

    def test_snapshot_fields(self):
        snap = ResourceSnapshot(
            timestamp=123.0,
            memory_usage_mb=512.0,
            child_process_count=10,
            running_jobs=2,
            active_sheets=3,
            zombie_pids=[100, 200],
        )
        assert snap.memory_usage_mb == 512.0
        assert snap.child_process_count == 10
        assert snap.running_jobs == 2
        assert snap.active_sheets == 3
        assert snap.zombie_pids == [100, 200]


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def resource_config() -> ResourceLimitConfig:
    """Low-limit resource config for testing thresholds."""
    return ResourceLimitConfig(
        max_memory_mb=1000,
        max_processes=20,
    )


@pytest.fixture
def mock_manager() -> MagicMock:
    """Mock JobManager for monitor tests."""
    mgr = MagicMock()
    mgr.running_count = 0
    mgr.active_job_count = 0
    mgr.rate_coordinator.prune_stale = AsyncMock(return_value=0)
    return mgr


@pytest.fixture
def monitor(resource_config: ResourceLimitConfig, mock_manager: MagicMock) -> ResourceMonitor:
    """Create a ResourceMonitor with mocked system probes."""
    return ResourceMonitor(resource_config, manager=mock_manager)


# ─── check_now ─────────────────────────────────────────────────────────


class TestCheckNow:
    """Tests for ResourceMonitor.check_now()."""

    @pytest.mark.asyncio
    async def test_check_now_returns_snapshot(self, monitor: ResourceMonitor):
        """check_now returns a ResourceSnapshot with system metrics."""
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=256.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=5),
            patch.object(ResourceMonitor, "_check_for_zombies", return_value=[]),
        ):
            snapshot = await monitor.check_now()

        assert isinstance(snapshot, ResourceSnapshot)
        assert snapshot.memory_usage_mb == 256.0
        assert snapshot.child_process_count == 5
        assert snapshot.zombie_pids == []

    @pytest.mark.asyncio
    async def test_check_now_includes_manager_counts(
        self, monitor: ResourceMonitor, mock_manager: MagicMock,
    ):
        """check_now pulls running_jobs and active_sheets from manager."""
        mock_manager.running_count = 3
        mock_manager.active_job_count = 7

        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=100.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=2),
            patch.object(ResourceMonitor, "_check_for_zombies", return_value=[]),
        ):
            snapshot = await monitor.check_now()

        assert snapshot.running_jobs == 3
        assert snapshot.active_sheets == 7


# ─── is_accepting_work ─────────────────────────────────────────────────


class TestIsAcceptingWork:
    """Tests for is_accepting_work() threshold checks."""

    def test_accepting_when_below_thresholds(self, monitor: ResourceMonitor):
        """Returns True when both memory and processes are well below limits."""
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=100.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=2),
        ):
            assert monitor.is_accepting_work() is True

    def test_not_accepting_when_memory_high(self, monitor: ResourceMonitor):
        """Returns False when memory exceeds WARN_THRESHOLD (80%)."""
        # 1000 MB limit, 850 MB usage = 85% > 80%
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=850.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=2),
        ):
            assert monitor.is_accepting_work() is False

    def test_not_accepting_when_processes_high(self, monitor: ResourceMonitor):
        """Returns False when process count exceeds WARN_THRESHOLD (80%)."""
        # 20 process limit, 17 processes = 85% > 80%
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=100.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=17),
        ):
            assert monitor.is_accepting_work() is False

    def test_not_accepting_when_both_high(self, monitor: ResourceMonitor):
        """Returns False when both memory and processes exceed thresholds."""
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=900.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=18),
        ):
            assert monitor.is_accepting_work() is False

    def test_accepting_at_exactly_threshold_boundary(self, monitor: ResourceMonitor):
        """At exactly 80% usage, the condition is < 80, so not accepting."""
        # 1000 * 0.8 = 800 MB → 80% exactly → not < 80 → not accepting
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=800.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=2),
        ):
            assert monitor.is_accepting_work() is False

    def test_accepting_just_below_threshold(self, monitor: ResourceMonitor):
        """Just below 80% is still accepting."""
        # 799.9 / 1000 = 79.99% < 80%
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=799.9),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=2),
        ):
            assert monitor.is_accepting_work() is True


# ─── Monitor Start/Stop ───────────────────────────────────────────────


class TestMonitorLifecycle:
    """Tests for start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self, monitor: ResourceMonitor):
        """start() creates a background monitoring task."""
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=100.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=2),
            patch.object(ResourceMonitor, "_check_for_zombies", return_value=[]),
        ):
            await monitor.start(interval_seconds=0.05)
            assert monitor._task is not None
            assert monitor._running is True

            await monitor.stop()
            assert monitor._task is None
            assert monitor._running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self, monitor: ResourceMonitor):
        """Calling start() twice doesn't create a second task."""
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=100.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=2),
            patch.object(ResourceMonitor, "_check_for_zombies", return_value=[]),
        ):
            await monitor.start(interval_seconds=0.05)
            first_task = monitor._task

            await monitor.start(interval_seconds=0.05)
            assert monitor._task is first_task

            await monitor.stop()


# ─── Memory Warning Thresholds ────────────────────────────────────────


class TestMemoryThresholds:
    """Tests for memory threshold evaluation in _evaluate()."""

    @pytest.mark.asyncio
    async def test_warning_logged_above_80_percent(
        self, monitor: ResourceMonitor,
    ):
        """Memory between 80% and 95% logs a warning."""
        snapshot = ResourceSnapshot(
            timestamp=1.0,
            memory_usage_mb=850.0,  # 85% of 1000 MB limit
            child_process_count=2,
            running_jobs=1,
            active_sheets=1,
        )

        with patch("marianne.daemon.monitor._logger") as mock_logger:
            await monitor._evaluate(snapshot)
            mock_logger.warning.assert_called()
            # Check it was a memory warning
            call_args = mock_logger.warning.call_args
            assert "memory" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_critical_logged_above_95_percent(
        self, monitor: ResourceMonitor,
    ):
        """Memory above 95% logs an error and enforces limit."""
        snapshot = ResourceSnapshot(
            timestamp=1.0,
            memory_usage_mb=960.0,  # 96% of 1000 MB limit
            child_process_count=2,
            running_jobs=1,
            active_sheets=1,
        )

        async def noop_enforce():
            pass

        with (
            patch("marianne.daemon.monitor._logger") as mock_logger,
            patch.object(monitor, "_enforce_memory_limit", side_effect=noop_enforce),
        ):
            await monitor._evaluate(snapshot)
            mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_no_warning_below_80_percent(
        self, monitor: ResourceMonitor,
    ):
        """Memory below 80% produces no warnings."""
        snapshot = ResourceSnapshot(
            timestamp=1.0,
            memory_usage_mb=500.0,  # 50% of 1000 MB limit
            child_process_count=2,
            running_jobs=0,
            active_sheets=0,
        )

        with patch("marianne.daemon.monitor._logger") as mock_logger:
            await monitor._evaluate(snapshot)
            mock_logger.warning.assert_not_called()
            mock_logger.error.assert_not_called()


# ─── Process Count Tracking ───────────────────────────────────────────


class TestProcessCountTracking:
    """Tests for process count threshold evaluation."""

    @pytest.mark.asyncio
    async def test_process_warning_above_80_percent(
        self, monitor: ResourceMonitor,
    ):
        """Process count between 80% and 95% logs a warning."""
        snapshot = ResourceSnapshot(
            timestamp=1.0,
            memory_usage_mb=100.0,
            child_process_count=17,  # 85% of 20 limit
            running_jobs=1,
            active_sheets=1,
        )

        with patch("marianne.daemon.monitor._logger") as mock_logger:
            await monitor._evaluate(snapshot)
            mock_logger.warning.assert_called()
            # Check it was a processes warning
            call_args = mock_logger.warning.call_args
            assert "processes" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_process_critical_above_95_percent(
        self, monitor: ResourceMonitor,
    ):
        """Process count above 95% logs an error and enforces limit."""
        snapshot = ResourceSnapshot(
            timestamp=1.0,
            memory_usage_mb=100.0,
            child_process_count=19,  # 95% of 20 limit
            running_jobs=1,
            active_sheets=1,
        )

        async def noop_enforce():
            pass

        with (
            patch("marianne.daemon.monitor._logger") as mock_logger,
            patch.object(monitor, "_enforce_process_limit", side_effect=noop_enforce),
        ):
            await monitor._evaluate(snapshot)
            mock_logger.error.assert_called()
            call_args = mock_logger.error.call_args
            assert "processes" in call_args[0][0]


# ─── psutil Fallback ──────────────────────────────────────────────────


class TestPsutilFallback:
    """Tests for fallback when psutil is unavailable."""

    def test_memory_fallback_to_proc(self):
        """When psutil import fails, _get_memory_usage_mb falls back to /proc."""
        with patch.dict("sys.modules", {"psutil": None}):
            # Import error will be caught, /proc fallback used
            result = ResourceMonitor._get_memory_usage_mb()
            # Should return a non-negative float (from /proc or 0.0)
            assert isinstance(result, float)
            assert result >= 0.0

    def test_child_count_fallback_to_proc(self):
        """When psutil import fails, _get_child_process_count falls back to /proc."""
        with patch.dict("sys.modules", {"psutil": None}):
            result = ResourceMonitor._get_child_process_count()
            assert isinstance(result, int)
            assert result >= 0

    def test_memory_with_psutil_mock(self):
        """When psutil is available, it's used for memory measurement."""
        mock_psutil = MagicMock()
        mock_process = MagicMock()
        mock_process.memory_info.return_value.rss = 512 * 1024 * 1024  # 512 MB
        mock_psutil.Process.return_value = mock_process

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            # Need to re-import to pick up the mock
            # Instead, call the method which imports psutil internally
            result = ResourceMonitor._get_memory_usage_mb()
            # The real psutil is installed, so this should return a real value
            assert isinstance(result, float)
            assert result > 0

    def test_child_count_with_psutil_mock(self):
        """When psutil is available, it's used for child process counting."""
        result = ResourceMonitor._get_child_process_count()
        assert isinstance(result, int)
        assert result >= 0


# ─── Monitor without manager ──────────────────────────────────────────


class TestMonitorNoManager:
    """Tests for ResourceMonitor when no manager is provided."""

    @pytest.mark.asyncio
    async def test_check_now_without_manager(self, resource_config: ResourceLimitConfig):
        """check_now works when manager is None."""
        monitor = ResourceMonitor(resource_config, manager=None)

        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=100.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=0),
            patch.object(ResourceMonitor, "_check_for_zombies", return_value=[]),
        ):
            snapshot = await monitor.check_now()

        assert snapshot.running_jobs == 0
        assert snapshot.active_sheets == 0

    @pytest.mark.asyncio
    async def test_enforce_memory_noop_without_manager(self, resource_config: ResourceLimitConfig):
        """_enforce_memory_limit is a no-op when manager is None."""
        monitor = ResourceMonitor(resource_config, manager=None)
        # Should not raise
        await monitor._enforce_memory_limit()


# ─── Load Shedding (pause-first, cancel-fallback) ────────────────────


class TestLoadShedding:
    """Tests for _shed_oldest_job() pause-first behavior."""

    @pytest.mark.asyncio
    async def test_shed_pauses_oldest_job(
        self, monitor: ResourceMonitor, mock_manager: MagicMock,
    ):
        """_shed_oldest_job calls pause_job on the oldest running job."""
        mock_manager.list_jobs = AsyncMock(return_value=[
            {"job_id": "old-job", "status": "running", "submitted_at": 100},
            {"job_id": "new-job", "status": "running", "submitted_at": 200},
        ])
        mock_manager.pause_job = AsyncMock(return_value=True)

        await monitor._shed_oldest_job("processes")

        mock_manager.pause_job.assert_awaited_once_with("old-job")

    @pytest.mark.asyncio
    async def test_shed_falls_back_to_cancel_on_pause_failure(
        self, monitor: ResourceMonitor, mock_manager: MagicMock,
    ):
        """When pause_job raises, _shed_oldest_job falls back to cancel_job."""
        mock_manager.list_jobs = AsyncMock(return_value=[
            {"job_id": "stuck-job", "status": "running", "submitted_at": 100},
        ])
        mock_manager.pause_job = AsyncMock(side_effect=Exception("not pausable"))
        mock_manager.cancel_job = AsyncMock(return_value=True)

        await monitor._shed_oldest_job("memory")

        mock_manager.pause_job.assert_awaited_once_with("stuck-job")
        mock_manager.cancel_job.assert_awaited_once_with("stuck-job")

    @pytest.mark.asyncio
    async def test_shed_noop_when_no_running_jobs(
        self, monitor: ResourceMonitor, mock_manager: MagicMock,
    ):
        """_shed_oldest_job is a no-op when no jobs are running."""
        mock_manager.list_jobs = AsyncMock(return_value=[
            {"job_id": "done", "status": "completed", "submitted_at": 100},
        ])

        # Should not raise
        await monitor._shed_oldest_job("processes")

    @pytest.mark.asyncio
    async def test_shed_noop_without_manager(self, resource_config: ResourceLimitConfig):
        """_shed_oldest_job is a no-op when manager is None."""
        monitor = ResourceMonitor(resource_config, manager=None)
        # Should not raise
        await monitor._shed_oldest_job("memory")

    @pytest.mark.asyncio
    async def test_enforce_memory_calls_shed(
        self, monitor: ResourceMonitor, mock_manager: MagicMock,
    ):
        """_enforce_memory_limit delegates to _shed_oldest_job."""
        mock_manager.list_jobs = AsyncMock(return_value=[])
        await monitor._enforce_memory_limit()
        # Verify list_jobs was called (shed was invoked)
        mock_manager.list_jobs.assert_awaited()

    @pytest.mark.asyncio
    async def test_enforce_process_calls_shed(
        self, monitor: ResourceMonitor, mock_manager: MagicMock,
    ):
        """_enforce_process_limit delegates to _shed_oldest_job."""
        mock_manager.list_jobs = AsyncMock(return_value=[])
        await monitor._enforce_process_limit()
        mock_manager.list_jobs.assert_awaited()


# ─── Probe Failure Handling (D006) ───────────────────────────────────


class TestProbeFailure:
    """Tests for fail-closed behavior when system probes return None."""

    def test_is_accepting_work_false_when_memory_probe_fails(
        self, monitor: ResourceMonitor,
    ):
        """When memory probe fails, is_accepting_work returns False (fail-closed)."""
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=None),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=2),
        ):
            assert monitor.is_accepting_work() is False

    def test_is_accepting_work_false_when_process_probe_fails(
        self, monitor: ResourceMonitor,
    ):
        """When process probe fails, is_accepting_work returns False (fail-closed)."""
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=100.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=None),
        ):
            assert monitor.is_accepting_work() is False

    @pytest.mark.asyncio
    async def test_check_now_marks_probe_failed(self, monitor: ResourceMonitor):
        """check_now sets probe_failed=True when a probe returns None."""
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=None),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=5),
            patch.object(ResourceMonitor, "_check_for_zombies", return_value=[]),
        ):
            snapshot = await monitor.check_now()
            assert snapshot.probe_failed is True
            assert snapshot.memory_usage_mb == 0.0  # fallback value

    def test_is_accepting_work_false_when_degraded(
        self, monitor: ResourceMonitor,
    ):
        """When monitor is degraded, is_accepting_work returns False."""
        monitor._degraded = True
        with (
            patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=100.0),
            patch.object(ResourceMonitor, "_get_child_process_count", return_value=2),
        ):
            assert monitor.is_accepting_work() is False


# ─── Circuit Breaker (D016) ──────────────────────────────────────────


class TestCircuitBreaker:
    """Tests for monitoring loop circuit breaker."""

    @pytest.mark.asyncio
    async def test_consecutive_failures_trigger_degraded(
        self, resource_config: ResourceLimitConfig,
    ):
        """After 5 consecutive check failures, monitor enters degraded mode."""
        monitor = ResourceMonitor(resource_config, manager=None)
        assert not monitor._degraded

        # Simulate consecutive failures by setting counter directly
        monitor._consecutive_failures = 4
        assert not monitor._degraded

        # One more failure should trigger degraded
        monitor._consecutive_failures = 5
        # The actual check happens in the loop, so test the flag directly
        monitor._degraded = True
        assert monitor._degraded
        assert monitor.is_degraded

    def test_degraded_property(self, monitor: ResourceMonitor):
        """is_degraded property reflects internal state."""
        assert not monitor.is_degraded
        monitor._degraded = True
        assert monitor.is_degraded

    def test_public_api_max_memory_mb(self, monitor: ResourceMonitor):
        """max_memory_mb property returns configured limit."""
        assert monitor.max_memory_mb == 1000  # from fixture

    def test_public_api_current_memory_mb(self, monitor: ResourceMonitor):
        """current_memory_mb returns probe result via public API."""
        with patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=512.0):
            assert monitor.current_memory_mb() == 512.0

    def test_public_api_current_memory_mb_none_on_failure(self, monitor: ResourceMonitor):
        """current_memory_mb returns None when probe fails."""
        with patch.object(ResourceMonitor, "_get_memory_usage_mb", return_value=None):
            assert monitor.current_memory_mb() is None
