"""Tests for F-149: Backpressure cross-instrument rejection fix.

F-149: Backpressure rejects ALL new jobs when ANY single instrument is
rate-limited. This is wrong — a rate limit on instrument A should not
block jobs targeting instrument B.

Fix: should_accept_job() and rejection_reason() only consider resource
pressure (memory, processes). Rate limits are per-instrument and handled
at the sheet dispatch level by the baton and scheduler.

current_level() still includes rate limits for sheet-level dispatch delay.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from marianne.daemon.backpressure import (
    BackpressureController,
    PressureLevel,
)
from marianne.daemon.config import ResourceLimitConfig
from marianne.daemon.monitor import ResourceMonitor
from marianne.daemon.rate_coordinator import RateLimitCoordinator


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def resource_config() -> ResourceLimitConfig:
    return ResourceLimitConfig(max_memory_mb=1000, max_processes=20)


@pytest.fixture
def monitor(resource_config: ResourceLimitConfig) -> ResourceMonitor:
    return ResourceMonitor(resource_config)


@pytest.fixture
def coordinator() -> RateLimitCoordinator:
    return RateLimitCoordinator()


@pytest.fixture
def controller(
    monitor: ResourceMonitor, coordinator: RateLimitCoordinator,
) -> BackpressureController:
    return BackpressureController(monitor, coordinator)


# ─── Core F-149: Rate limits do NOT block job submissions ──────────────


class TestF149RateLimitsDoNotBlockJobs:
    """Rate limits alone should not cause job rejection.

    This is the core of F-149: a rate limit on one instrument must not
    block jobs targeting any instrument. Job-level gating should only
    consider system resource pressure (memory, processes).
    """

    @pytest.mark.asyncio
    async def test_job_accepted_with_active_rate_limit(
        self,
        controller: BackpressureController,
        coordinator: RateLimitCoordinator,
    ):
        """Job submissions accepted even when an instrument is rate-limited."""
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "is_accepting_work", return_value=True,
        ):
            await coordinator.report_rate_limit(
                backend_type="claude_cli",
                wait_seconds=60.0,
                job_id="job-a",
                sheet_num=1,
            )

            # Jobs MUST be accepted — rate limits handled at sheet level
            assert controller.should_accept_job() is True

    @pytest.mark.asyncio
    async def test_job_accepted_with_multiple_rate_limits(
        self,
        controller: BackpressureController,
        coordinator: RateLimitCoordinator,
    ):
        """Even multiple rate-limited instruments don't block job submission."""
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "is_accepting_work", return_value=True,
        ):
            await coordinator.report_rate_limit(
                backend_type="claude_cli",
                wait_seconds=60.0,
                job_id="job-a",
                sheet_num=1,
            )
            await coordinator.report_rate_limit(
                backend_type="gemini_cli",
                wait_seconds=120.0,
                job_id="job-b",
                sheet_num=2,
            )

            # Jobs still accepted — both rate limits are per-instrument
            assert controller.should_accept_job() is True

    @pytest.mark.asyncio
    async def test_rejection_reason_none_with_rate_limit_only(
        self,
        controller: BackpressureController,
        coordinator: RateLimitCoordinator,
    ):
        """rejection_reason returns None when only rate limits are active."""
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "is_accepting_work", return_value=True,
        ):
            await coordinator.report_rate_limit(
                backend_type="openai",
                wait_seconds=30.0,
                job_id="job-b",
                sheet_num=2,
            )

            # No rejection — rate limits don't cause rejection
            assert controller.rejection_reason() is None

    @pytest.mark.asyncio
    async def test_resource_pressure_still_rejects(
        self,
        controller: BackpressureController,
        coordinator: RateLimitCoordinator,
    ):
        """High memory pressure still rejects regardless of rate limits."""
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=900.0,
        ), patch.object(
            ResourceMonitor, "is_accepting_work", return_value=True,
        ):
            # High memory → reject even without rate limits
            assert controller.should_accept_job() is False
            assert controller.rejection_reason() == "resource"

    @pytest.mark.asyncio
    async def test_resource_pressure_plus_rate_limit_still_rejects(
        self,
        controller: BackpressureController,
        coordinator: RateLimitCoordinator,
    ):
        """Both memory pressure and rate limits → resource rejection."""
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=900.0,
        ), patch.object(
            ResourceMonitor, "is_accepting_work", return_value=True,
        ):
            await coordinator.report_rate_limit(
                backend_type="claude_cli",
                wait_seconds=60.0,
                job_id="job-a",
                sheet_num=1,
            )

            # Resource pressure dominates
            assert controller.should_accept_job() is False
            assert controller.rejection_reason() == "resource"


# ─── Sheet-level dispatch still considers rate limits ──────────────────


class TestSheetDispatchStillConsidersRateLimits:
    """current_level() and can_start_sheet() still factor in rate limits.

    The fix is scoped to job submission. Sheet dispatch delay is still
    governed by current_level() which includes rate limits — this gives
    the scheduler/baton a signal to pace sheet launches.
    """

    @pytest.mark.asyncio
    async def test_current_level_still_includes_rate_limits(
        self,
        controller: BackpressureController,
        coordinator: RateLimitCoordinator,
    ):
        """current_level() still returns HIGH when rate limits active."""
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "is_accepting_work", return_value=True,
        ):
            await coordinator.report_rate_limit(
                backend_type="claude_cli",
                wait_seconds=60.0,
                job_id="job-a",
                sheet_num=1,
            )

            # Sheet-level dispatch still considers rate limits
            assert controller.current_level() == PressureLevel.HIGH

    @pytest.mark.asyncio
    async def test_can_start_sheet_delayed_by_rate_limit(
        self,
        controller: BackpressureController,
        coordinator: RateLimitCoordinator,
    ):
        """Sheets get HIGH-level delay when rate limits are active."""
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "is_accepting_work", return_value=True,
        ):
            await coordinator.report_rate_limit(
                backend_type="claude_cli",
                wait_seconds=60.0,
                job_id="job-a",
                sheet_num=1,
            )

            allowed, delay = await controller.can_start_sheet()
            # Sheets allowed with delay (HIGH level allows, delays)
            assert allowed is True
            assert delay > 0


# ─── Degraded/critical paths unaffected ────────────────────────────────


class TestCriticalPathsUnaffected:
    """System health checks still gate job submission correctly."""

    def test_degraded_monitor_still_rejects(
        self, controller: BackpressureController, monitor: ResourceMonitor,
    ):
        """Degraded monitor → reject regardless of rate limits."""
        monitor._degraded = True
        with patch.object(
            ResourceMonitor, "current_memory_mb", return_value=100.0,
        ):
            assert controller.should_accept_job() is False
            assert controller.rejection_reason() == "resource"
        monitor._degraded = False

    def test_process_limit_still_rejects(
        self, controller: BackpressureController,
    ):
        """Process limit exceeded → reject via is_accepting_work."""
        with patch.object(
            ResourceMonitor, "_get_memory_usage_mb", return_value=100.0,
        ), patch.object(
            ResourceMonitor, "is_accepting_work", return_value=False,
        ):
            assert controller.should_accept_job() is False

    def test_probe_failure_still_rejects(
        self, controller: BackpressureController,
    ):
        """Memory probe failure → reject."""
        with patch.object(
            ResourceMonitor, "current_memory_mb", return_value=None,
        ):
            assert controller.should_accept_job() is False
            assert controller.rejection_reason() == "resource"
