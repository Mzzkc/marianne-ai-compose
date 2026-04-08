"""Tests for marianne.daemon.health module."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from marianne.daemon.config import ResourceLimitConfig
from marianne.daemon.health import HealthChecker
from marianne.daemon.monitor import ResourceMonitor


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_manager():
    """Create a mock JobManager with controllable counts."""
    m = MagicMock()
    m.running_count = 2
    m.active_job_count = 3
    m.shutting_down = False
    m.failure_rate_elevated = False
    m.notifications_degraded = False
    return m


@pytest.fixture
def resource_config():
    return ResourceLimitConfig(max_memory_mb=1024, max_processes=20)


@pytest.fixture
def monitor(resource_config, mock_manager):
    return ResourceMonitor(resource_config, manager=mock_manager)


@pytest.fixture
def health_checker(mock_manager, monitor):
    return HealthChecker(mock_manager, monitor, start_time=time.monotonic() - 120.0)


# ─── HealthChecker.liveness ───────────────────────────────────────────


class TestLiveness:
    """Tests for the liveness probe."""

    @pytest.mark.asyncio
    async def test_liveness_returns_ok(self, health_checker):
        result = await health_checker.liveness()
        assert result["status"] == "ok"
        assert result["pid"] == os.getpid()

    @pytest.mark.asyncio
    async def test_liveness_includes_uptime(self, health_checker):
        result = await health_checker.liveness()
        assert "uptime_seconds" in result
        assert result["uptime_seconds"] >= 120.0

    @pytest.mark.asyncio
    async def test_liveness_is_cheap(self, health_checker):
        """Liveness should be extremely fast — no I/O."""
        start = time.monotonic()
        await health_checker.liveness()
        elapsed = time.monotonic() - start
        assert elapsed < 30.0  # Should be near-instant (no I/O)


# ─── HealthChecker.readiness ──────────────────────────────────────────


class TestReadiness:
    """Tests for the readiness probe."""

    @pytest.mark.asyncio
    async def test_readiness_ready_when_resources_ok(self, health_checker, monitor):
        with patch.object(monitor, "_get_memory_usage_mb", return_value=100.0), \
             patch.object(monitor, "_get_child_process_count", return_value=5), \
             patch.object(monitor, "_check_for_zombies", return_value=[]):
            result = await health_checker.readiness()
            assert result["status"] == "ready"
            assert result["accepting_work"] is True
            assert result["running_jobs"] == 2

    @pytest.mark.asyncio
    async def test_readiness_not_ready_high_memory(self, health_checker, monitor):
        """When memory exceeds WARN_THRESHOLD, should be not_ready."""
        with patch.object(monitor, "_get_memory_usage_mb", return_value=900.0), \
             patch.object(monitor, "_get_child_process_count", return_value=5), \
             patch.object(monitor, "_check_for_zombies", return_value=[]):
            result = await health_checker.readiness()
            assert result["status"] == "not_ready"
            assert result["accepting_work"] is False

    @pytest.mark.asyncio
    async def test_readiness_not_ready_high_processes(self, health_checker, monitor):
        """When process count exceeds WARN_THRESHOLD, should be not_ready."""
        with patch.object(monitor, "_get_memory_usage_mb", return_value=100.0), \
             patch.object(monitor, "_get_child_process_count", return_value=18), \
             patch.object(monitor, "_check_for_zombies", return_value=[]):
            result = await health_checker.readiness()
            assert result["status"] == "not_ready"
            assert result["accepting_work"] is False

    @pytest.mark.asyncio
    async def test_readiness_includes_memory_mb(self, health_checker, monitor):
        with patch.object(monitor, "_get_memory_usage_mb", return_value=256.789), \
             patch.object(monitor, "_get_child_process_count", return_value=3), \
             patch.object(monitor, "_check_for_zombies", return_value=[]):
            result = await health_checker.readiness()
            assert result["memory_mb"] == 256.8  # Rounded to 1 decimal

    @pytest.mark.asyncio
    async def test_readiness_includes_child_processes(self, health_checker, monitor):
        with patch.object(monitor, "_get_memory_usage_mb", return_value=100.0), \
             patch.object(monitor, "_get_child_process_count", return_value=7), \
             patch.object(monitor, "_check_for_zombies", return_value=[]):
            result = await health_checker.readiness()
            assert result["child_processes"] == 7

    @pytest.mark.asyncio
    async def test_readiness_includes_uptime(self, health_checker, monitor):
        with patch.object(monitor, "_get_memory_usage_mb", return_value=100.0), \
             patch.object(monitor, "_get_child_process_count", return_value=3), \
             patch.object(monitor, "_check_for_zombies", return_value=[]):
            result = await health_checker.readiness()
            assert result["uptime_seconds"] >= 120.0

    @pytest.mark.asyncio
    async def test_readiness_not_ready_when_failure_rate_elevated(
        self, health_checker, monitor, mock_manager,
    ):
        """When failure rate is elevated, readiness degrades to not_ready."""
        mock_manager.failure_rate_elevated = True
        with patch.object(monitor, "_get_memory_usage_mb", return_value=100.0), \
             patch.object(monitor, "_get_child_process_count", return_value=5), \
             patch.object(monitor, "_check_for_zombies", return_value=[]):
            result = await health_checker.readiness()
            assert result["status"] == "not_ready"
            assert result["accepting_work"] is False
            assert result["failure_rate_elevated"] is True

    @pytest.mark.asyncio
    async def test_readiness_includes_failure_rate_field(
        self, health_checker, monitor,
    ):
        """Readiness response includes failure_rate_elevated field."""
        with patch.object(monitor, "_get_memory_usage_mb", return_value=100.0), \
             patch.object(monitor, "_get_child_process_count", return_value=5), \
             patch.object(monitor, "_check_for_zombies", return_value=[]):
            result = await health_checker.readiness()
            assert "failure_rate_elevated" in result
            assert result["failure_rate_elevated"] is False

    @pytest.mark.asyncio
    async def test_readiness_not_ready_when_notifications_degraded(
        self, health_checker, monitor, mock_manager,
    ):
        """When notifications are degraded, readiness reports not_ready."""
        mock_manager.notifications_degraded = True
        with patch.object(monitor, "_get_memory_usage_mb", return_value=100.0), \
             patch.object(monitor, "_get_child_process_count", return_value=5), \
             patch.object(monitor, "_check_for_zombies", return_value=[]):
            result = await health_checker.readiness()
            assert result["status"] == "not_ready"
            assert result["accepting_work"] is False
            assert result["notifications_degraded"] is True

    @pytest.mark.asyncio
    async def test_readiness_includes_notifications_degraded_field(
        self, health_checker, monitor,
    ):
        """Readiness response includes notifications_degraded field."""
        with patch.object(monitor, "_get_memory_usage_mb", return_value=100.0), \
             patch.object(monitor, "_get_child_process_count", return_value=5), \
             patch.object(monitor, "_check_for_zombies", return_value=[]):
            result = await health_checker.readiness()
            assert "notifications_degraded" in result
            assert result["notifications_degraded"] is False


# ─── ResourceMonitor.is_accepting_work ────────────────────────────────


class TestIsAcceptingWork:
    """Tests for the is_accepting_work method on ResourceMonitor."""

    def test_accepting_when_below_thresholds(self, monitor):
        with patch.object(monitor, "_get_memory_usage_mb", return_value=100.0), \
             patch.object(monitor, "_get_child_process_count", return_value=5):
            assert monitor.is_accepting_work() is True

    def test_not_accepting_at_memory_warn_threshold(self, monitor):
        """80% of 1024 MB = 819.2 MB — at threshold should reject."""
        with patch.object(monitor, "_get_memory_usage_mb", return_value=820.0), \
             patch.object(monitor, "_get_child_process_count", return_value=5):
            assert monitor.is_accepting_work() is False

    def test_not_accepting_at_process_warn_threshold(self, monitor):
        """80% of 20 processes = 16 — at threshold should reject."""
        with patch.object(monitor, "_get_memory_usage_mb", return_value=100.0), \
             patch.object(monitor, "_get_child_process_count", return_value=16):
            assert monitor.is_accepting_work() is False

    def test_accepting_just_below_threshold(self, monitor):
        """79% of limits should still accept."""
        with patch.object(monitor, "_get_memory_usage_mb", return_value=808.0), \
             patch.object(monitor, "_get_child_process_count", return_value=15):
            assert monitor.is_accepting_work() is True


# ─── HealthChecker default start_time ─────────────────────────────────


class TestHealthCheckerDefaults:
    """Tests for HealthChecker construction defaults."""

    @pytest.mark.asyncio
    async def test_default_start_time(self, mock_manager, monitor):
        """When no start_time given, uptime should be very small."""
        checker = HealthChecker(mock_manager, monitor)
        result = await checker.liveness()
        assert result["uptime_seconds"] < 2.0
