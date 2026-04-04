"""Tests for mozart clear-rate-limits CLI command and IPC pipeline.

The clear-rate-limits command allows operators to manually clear stale
rate limits on instruments. This is needed when rate limits expire in
the backend but the coordinator still has them cached, or when an
operator wants to force-clear a limit to retry work.

Tests cover:
1. RateLimitCoordinator.clear_limits() method
2. JobManager.clear_rate_limits() integration (coordinator + baton)
3. IPC handler in process.py
4. CLI command (clear_rate_limits)
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. RateLimitCoordinator.clear_limits()
# ---------------------------------------------------------------------------


class TestRateLimitCoordinatorClearLimits:
    """RateLimitCoordinator must support clearing active limits."""

    @pytest.mark.asyncio
    async def test_clear_all_limits(self) -> None:
        """clear_limits() with no instrument clears all active limits."""
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        coord = RateLimitCoordinator()
        await coord.report_rate_limit("claude-cli", 300.0, "j1", 1)
        await coord.report_rate_limit("gemini-cli", 600.0, "j2", 2)

        assert len(coord.active_limits) == 2

        cleared = await coord.clear_limits()
        assert cleared == 2
        assert len(coord.active_limits) == 0

    @pytest.mark.asyncio
    async def test_clear_specific_instrument(self) -> None:
        """clear_limits(instrument='X') clears only that instrument's limit."""
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        coord = RateLimitCoordinator()
        await coord.report_rate_limit("claude-cli", 300.0, "j1", 1)
        await coord.report_rate_limit("gemini-cli", 600.0, "j2", 2)

        cleared = await coord.clear_limits(instrument="claude-cli")
        assert cleared == 1
        assert "claude-cli" not in coord.active_limits
        assert "gemini-cli" in coord.active_limits

    @pytest.mark.asyncio
    async def test_clear_nonexistent_instrument(self) -> None:
        """clear_limits() on an instrument with no active limit returns 0."""
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        coord = RateLimitCoordinator()
        await coord.report_rate_limit("claude-cli", 300.0, "j1", 1)

        cleared = await coord.clear_limits(instrument="nonexistent")
        assert cleared == 0
        assert "claude-cli" in coord.active_limits

    @pytest.mark.asyncio
    async def test_clear_already_empty(self) -> None:
        """clear_limits() when no limits are active returns 0."""
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        coord = RateLimitCoordinator()
        cleared = await coord.clear_limits()
        assert cleared == 0

    @pytest.mark.asyncio
    async def test_clear_preserves_event_history(self) -> None:
        """clear_limits() removes active limits but preserves event history."""
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        coord = RateLimitCoordinator()
        await coord.report_rate_limit("claude-cli", 300.0, "j1", 1)

        events_before = len(coord.recent_events)
        await coord.clear_limits()

        assert len(coord.active_limits) == 0
        assert len(coord.recent_events) == events_before


# ---------------------------------------------------------------------------
# 2. JobManager.clear_rate_limits() integration
# ---------------------------------------------------------------------------


class TestManagerClearRateLimits:
    """JobManager must clear both coordinator and baton instrument state."""

    @pytest.mark.asyncio
    async def test_clears_coordinator_limits(self) -> None:
        """clear_rate_limits() calls coordinator.clear_limits()."""
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        coord = RateLimitCoordinator()
        await coord.report_rate_limit("claude-cli", 300.0, "j1", 1)

        mgr = MagicMock()
        mgr.rate_coordinator = coord
        mgr._baton_adapter = None
        mgr._start_pending_jobs = AsyncMock()

        from mozart.daemon.manager import JobManager

        result = await JobManager.clear_rate_limits.__get__(mgr, JobManager)(
            instrument=None,
        )

        assert result["cleared"] == 1
        assert len(coord.active_limits) == 0

    @pytest.mark.asyncio
    async def test_clears_baton_instrument_state(self) -> None:
        """clear_rate_limits() clears baton InstrumentState when baton is active."""
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        coord = RateLimitCoordinator()
        await coord.report_rate_limit("claude-cli", 300.0, "j1", 1)

        # Mock baton adapter with instrument state
        baton_adapter = MagicMock()
        baton_adapter.clear_instrument_rate_limit = MagicMock(return_value=1)

        mgr = MagicMock()
        mgr.rate_coordinator = coord
        mgr._baton_adapter = baton_adapter
        mgr._start_pending_jobs = AsyncMock()

        from mozart.daemon.manager import JobManager

        result = await JobManager.clear_rate_limits.__get__(mgr, JobManager)(
            instrument="claude-cli",
        )

        baton_adapter.clear_instrument_rate_limit.assert_called_once_with("claude-cli")
        # 1 from coordinator + 1 from baton
        assert result["cleared"] == 2

    @pytest.mark.asyncio
    async def test_returns_summary(self) -> None:
        """clear_rate_limits() returns a summary dict."""
        from mozart.daemon.rate_coordinator import RateLimitCoordinator

        coord = RateLimitCoordinator()
        await coord.report_rate_limit("claude-cli", 300.0, "j1", 1)
        await coord.report_rate_limit("gemini-cli", 600.0, "j2", 2)

        mgr = MagicMock()
        mgr.rate_coordinator = coord
        mgr._baton_adapter = None
        mgr._start_pending_jobs = AsyncMock()

        from mozart.daemon.manager import JobManager

        result = await JobManager.clear_rate_limits.__get__(mgr, JobManager)(
            instrument=None,
        )

        assert "cleared" in result
        assert "instrument" in result
        assert result["instrument"] is None
        assert result["cleared"] == 2


# ---------------------------------------------------------------------------
# 3. BatonCore.clear_instrument_rate_limit()
# ---------------------------------------------------------------------------


class TestBatonCoreClearRateLimit:
    """BatonCore must clear InstrumentState rate limit flags."""

    def test_clear_specific_instrument(self) -> None:
        """clear_instrument_rate_limit('X') clears that instrument's rate limit."""
        from mozart.daemon.baton.core import BatonCore
        from mozart.daemon.baton.state import InstrumentState

        core = BatonCore()
        core._instruments = {
            "claude-cli": InstrumentState(
                name="claude-cli",
                max_concurrent=5,
                rate_limited=True,
                rate_limit_expires_at=time.monotonic() + 300,
            ),
            "gemini-cli": InstrumentState(
                name="gemini-cli",
                max_concurrent=3,
                rate_limited=True,
                rate_limit_expires_at=time.monotonic() + 600,
            ),
        }

        result = core.clear_instrument_rate_limit("claude-cli")

        assert result == 1
        assert not core._instruments["claude-cli"].rate_limited
        assert core._instruments["claude-cli"].rate_limit_expires_at is None
        # gemini-cli untouched
        assert core._instruments["gemini-cli"].rate_limited

    def test_clear_all_instruments(self) -> None:
        """clear_instrument_rate_limit(None) clears all instruments."""
        from mozart.daemon.baton.core import BatonCore
        from mozart.daemon.baton.state import InstrumentState

        core = BatonCore()
        core._instruments = {
            "claude-cli": InstrumentState(
                name="claude-cli",
                max_concurrent=5,
                rate_limited=True,
                rate_limit_expires_at=time.monotonic() + 300,
            ),
            "gemini-cli": InstrumentState(
                name="gemini-cli",
                max_concurrent=3,
                rate_limited=True,
                rate_limit_expires_at=time.monotonic() + 600,
            ),
        }

        result = core.clear_instrument_rate_limit(None)

        assert result == 2
        assert not core._instruments["claude-cli"].rate_limited
        assert not core._instruments["gemini-cli"].rate_limited

    def test_clear_not_rate_limited(self) -> None:
        """Clearing a non-rate-limited instrument returns 0."""
        from mozart.daemon.baton.core import BatonCore
        from mozart.daemon.baton.state import InstrumentState

        core = BatonCore()
        core._instruments = {
            "claude-cli": InstrumentState(
                name="claude-cli",
                max_concurrent=5,
                rate_limited=False,
            ),
        }

        result = core.clear_instrument_rate_limit("claude-cli")

        assert result == 0

    def test_clear_moves_waiting_sheets_to_pending(self) -> None:
        """Clearing a rate limit moves WAITING sheets back to PENDING."""
        from mozart.daemon.baton.core import BatonCore, _JobRecord
        from mozart.daemon.baton.state import (
            BatonSheetStatus,
            InstrumentState,
            SheetExecutionState,
        )

        core = BatonCore()
        core._instruments = {
            "claude-cli": InstrumentState(
                name="claude-cli",
                max_concurrent=5,
                rate_limited=True,
                rate_limit_expires_at=time.monotonic() + 300,
            ),
        }
        core._jobs = {
            "job1": _JobRecord(
                job_id="job1",
                sheets={
                    1: SheetExecutionState(
                        sheet_num=1,
                        instrument_name="claude-cli",
                        status=BatonSheetStatus.WAITING,
                    ),
                    2: SheetExecutionState(
                        sheet_num=2,
                        instrument_name="claude-cli",
                        status=BatonSheetStatus.COMPLETED,
                    ),
                },
                dependencies={},
            ),
        }

        core.clear_instrument_rate_limit("claude-cli")

        assert core._jobs["job1"].sheets[1].status == BatonSheetStatus.PENDING
        # Terminal sheet untouched
        assert core._jobs["job1"].sheets[2].status == BatonSheetStatus.COMPLETED


# ---------------------------------------------------------------------------
# 4. IPC handler (process.py)
# ---------------------------------------------------------------------------


class TestIpcClearRateLimits:
    """IPC handler must parse params and delegate to manager."""

    @pytest.mark.asyncio
    async def test_ipc_clear_all(self) -> None:
        """daemon.clear_rate_limits with no instrument clears all."""
        mgr = MagicMock()
        mgr.clear_rate_limits = AsyncMock(
            return_value={"cleared": 2, "instrument": None},
        )

        # Simulate what process.py does
        params: dict[str, Any] = {}
        instrument = params.get("instrument")
        result = await mgr.clear_rate_limits(instrument=instrument)

        mgr.clear_rate_limits.assert_called_once_with(instrument=None)
        assert result["cleared"] == 2

    @pytest.mark.asyncio
    async def test_ipc_clear_specific(self) -> None:
        """daemon.clear_rate_limits with instrument param clears specific."""
        mgr = MagicMock()
        mgr.clear_rate_limits = AsyncMock(
            return_value={"cleared": 1, "instrument": "claude-cli"},
        )

        params: dict[str, Any] = {"instrument": "claude-cli"}
        instrument = params.get("instrument")
        result = await mgr.clear_rate_limits(instrument=instrument)

        mgr.clear_rate_limits.assert_called_once_with(instrument="claude-cli")
        assert result["cleared"] == 1


# ---------------------------------------------------------------------------
# 5. CLI command
# ---------------------------------------------------------------------------


class TestCliClearRateLimits:
    """CLI clear-rate-limits command must route through IPC."""

    @pytest.mark.asyncio
    async def test_clear_all_via_cli(self) -> None:
        """clear_rate_limits with no --instrument clears all."""
        from mozart.cli.commands.rate_limits import _clear_rate_limits

        with (
            patch(
                "mozart.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, {"cleared": 2, "instrument": None}),
            ) as mock_route,
            patch("mozart.cli.commands.rate_limits.configure_global_logging"),
        ):
            await _clear_rate_limits(instrument=None, json_output=False)

        mock_route.assert_called_once_with(
            "daemon.clear_rate_limits",
            {"instrument": None},
        )

    @pytest.mark.asyncio
    async def test_clear_specific_via_cli(self) -> None:
        """clear_rate_limits with --instrument routes correctly."""
        from mozart.cli.commands.rate_limits import _clear_rate_limits

        with (
            patch(
                "mozart.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, {"cleared": 1, "instrument": "claude-cli"}),
            ) as mock_route,
            patch("mozart.cli.commands.rate_limits.configure_global_logging"),
        ):
            await _clear_rate_limits(instrument="claude-cli", json_output=False)

        mock_route.assert_called_once_with(
            "daemon.clear_rate_limits",
            {"instrument": "claude-cli"},
        )

    @pytest.mark.asyncio
    async def test_json_output(self) -> None:
        """--json flag produces JSON output."""
        from mozart.cli.commands.rate_limits import _clear_rate_limits

        with (
            patch(
                "mozart.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, {"cleared": 1, "instrument": "claude-cli"}),
            ),
            patch("mozart.cli.commands.rate_limits.configure_global_logging"),
            patch("mozart.cli.commands.rate_limits.output_json") as mock_json,
        ):
            await _clear_rate_limits(instrument="claude-cli", json_output=True)

        mock_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_conductor_not_running(self) -> None:
        """When conductor is not running, shows error and exits."""
        from mozart.cli.commands.rate_limits import _clear_rate_limits

        with (
            patch(
                "mozart.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(False, None),
            ),
            patch("mozart.cli.commands.rate_limits.configure_global_logging"),
            patch("mozart.cli.commands.rate_limits.output_error") as mock_err,
            pytest.raises((SystemExit, Exception)),
        ):
            await _clear_rate_limits(instrument=None, json_output=False)

        mock_err.assert_called_once()
