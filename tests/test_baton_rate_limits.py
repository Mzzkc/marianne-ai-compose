"""Tests for baton rate limit handling — instrument-level, timer-based.

Step 25: Rate limits are per-instrument, timer-based recovery. The baton
tracks InstrumentState for each instrument and uses the timer wheel to
schedule recovery checks. Rate limits are NOT failures — they're tempo
changes. No retry budget is consumed.

TDD: Tests written before implementation. Red first, then green.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    RateLimitExpired,
    RateLimitHit,
    SheetAttemptResult,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    InstrumentState,
    SheetExecutionState,
)
from marianne.daemon.baton.timer import TimerWheel


def _make_sheets(
    count: int = 3, instrument: str = "claude-code"
) -> dict[int, SheetExecutionState]:
    """Create a set of sheet execution states."""
    return {
        i: SheetExecutionState(sheet_num=i, instrument_name=instrument)
        for i in range(1, count + 1)
    }


def _make_mixed_instrument_sheets() -> dict[int, SheetExecutionState]:
    """Create sheets on different instruments."""
    return {
        1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        3: SheetExecutionState(sheet_num=3, instrument_name="gemini-cli"),
        4: SheetExecutionState(sheet_num=4, instrument_name="gemini-cli"),
    }


# =========================================================================
# Test: Instrument state tracking
# =========================================================================


class TestInstrumentStateTracking:
    """Test that the baton tracks per-instrument state."""

    def test_register_instrument_creates_state(self) -> None:
        """Registering an instrument creates InstrumentState."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)

        state = baton.get_instrument_state("claude-code")
        assert state is not None
        assert state.name == "claude-code"
        assert state.max_concurrent == 4
        assert state.rate_limited is False

    def test_register_instrument_idempotent(self) -> None:
        """Registering the same instrument twice doesn't overwrite."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        baton.register_instrument("claude-code", max_concurrent=8)

        state = baton.get_instrument_state("claude-code")
        assert state is not None
        # First registration wins
        assert state.max_concurrent == 4

    def test_unknown_instrument_returns_none(self) -> None:
        """Getting state for an unknown instrument returns None."""
        baton = BatonCore()
        assert baton.get_instrument_state("unknown") is None

    def test_multiple_instruments_tracked_independently(self) -> None:
        """Each instrument has its own state."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        baton.register_instrument("gemini-cli", max_concurrent=2)

        claude = baton.get_instrument_state("claude-code")
        gemini = baton.get_instrument_state("gemini-cli")

        assert claude is not None
        assert gemini is not None
        assert claude.max_concurrent == 4
        assert gemini.max_concurrent == 2


# =========================================================================
# Test: RateLimitHit marks instrument as rate-limited
# =========================================================================


class TestRateLimitHit:
    """Test the RateLimitHit event handler."""

    @pytest.mark.asyncio
    async def test_rate_limit_marks_instrument_limited(self) -> None:
        """RateLimitHit marks the instrument as rate-limited."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(3, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        event = RateLimitHit(
            instrument="claude-code",
            wait_seconds=60.0,
            job_id="j1",
            sheet_num=1,
        )
        await baton.handle_event(event)

        state = baton.get_instrument_state("claude-code")
        assert state is not None
        assert state.rate_limited is True

    @pytest.mark.asyncio
    async def test_rate_limit_moves_dispatched_sheets_to_waiting(self) -> None:
        """Dispatched sheets for the rate-limited instrument move to waiting."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(3, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        sheets[2].status = BatonSheetStatus.DISPATCHED
        # Sheet 3 is still pending — should NOT be affected
        baton.register_job("j1", sheets, {})

        event = RateLimitHit(
            instrument="claude-code",
            wait_seconds=30.0,
            job_id="j1",
            sheet_num=1,
        )
        await baton.handle_event(event)

        # Dispatched sheets → waiting
        assert sheets[1].status == BatonSheetStatus.WAITING
        assert sheets[2].status == BatonSheetStatus.WAITING
        # Pending sheets are NOT affected
        assert sheets[3].status == BatonSheetStatus.PENDING

    @pytest.mark.asyncio
    async def test_rate_limit_only_affects_target_instrument(self) -> None:
        """Rate limit on claude-code does NOT affect gemini-cli sheets."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        baton.register_instrument("gemini-cli", max_concurrent=2)
        sheets = _make_mixed_instrument_sheets()
        sheets[1].status = BatonSheetStatus.DISPATCHED  # claude
        sheets[3].status = BatonSheetStatus.DISPATCHED  # gemini
        baton.register_job("j1", sheets, {})

        event = RateLimitHit(
            instrument="claude-code",
            wait_seconds=60.0,
            job_id="j1",
            sheet_num=1,
        )
        await baton.handle_event(event)

        # Claude sheets affected
        assert sheets[1].status == BatonSheetStatus.WAITING
        # Gemini sheets NOT affected
        assert sheets[3].status == BatonSheetStatus.DISPATCHED

        gemini_state = baton.get_instrument_state("gemini-cli")
        assert gemini_state is not None
        assert gemini_state.rate_limited is False

    @pytest.mark.asyncio
    async def test_rate_limit_doesnt_regress_terminal_sheets(self) -> None:
        """Terminal sheets must not be moved to waiting by rate limits."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(3, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        sheets[2].status = BatonSheetStatus.COMPLETED  # Terminal
        sheets[3].status = BatonSheetStatus.FAILED  # Terminal
        baton.register_job("j1", sheets, {})

        event = RateLimitHit(
            instrument="claude-code",
            wait_seconds=30.0,
            job_id="j1",
            sheet_num=1,
        )
        await baton.handle_event(event)

        # Terminal sheets unchanged
        assert sheets[2].status == BatonSheetStatus.COMPLETED
        assert sheets[3].status == BatonSheetStatus.FAILED


# =========================================================================
# Test: RateLimitExpired recovers instrument
# =========================================================================


class TestRateLimitExpired:
    """Test the RateLimitExpired event handler."""

    @pytest.mark.asyncio
    async def test_rate_limit_expired_clears_instrument(self) -> None:
        """RateLimitExpired clears the rate limit on the instrument."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(2, "claude-code")
        sheets[1].status = BatonSheetStatus.WAITING
        baton.register_job("j1", sheets, {})

        # Mark as rate-limited first
        inst_state = baton.get_instrument_state("claude-code")
        assert inst_state is not None
        inst_state.rate_limited = True

        event = RateLimitExpired(instrument="claude-code")
        await baton.handle_event(event)

        assert inst_state.rate_limited is False

    @pytest.mark.asyncio
    async def test_rate_limit_expired_moves_waiting_to_pending(self) -> None:
        """Waiting sheets for the recovered instrument move back to pending."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(3, "claude-code")
        sheets[1].status = BatonSheetStatus.WAITING
        sheets[2].status = BatonSheetStatus.WAITING
        sheets[3].status = BatonSheetStatus.COMPLETED  # Terminal
        baton.register_job("j1", sheets, {})

        event = RateLimitExpired(instrument="claude-code")
        await baton.handle_event(event)

        # Waiting sheets → pending (ready for dispatch)
        assert sheets[1].status == BatonSheetStatus.PENDING
        assert sheets[2].status == BatonSheetStatus.PENDING
        # Terminal unchanged
        assert sheets[3].status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_rate_limit_expired_only_affects_target_instrument(self) -> None:
        """Rate limit expiry only moves sheets for the target instrument."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        baton.register_instrument("gemini-cli", max_concurrent=2)
        sheets = _make_mixed_instrument_sheets()
        sheets[1].status = BatonSheetStatus.WAITING  # claude
        sheets[3].status = BatonSheetStatus.WAITING  # gemini
        baton.register_job("j1", sheets, {})

        # Only claude's rate limit expires
        event = RateLimitExpired(instrument="claude-code")
        await baton.handle_event(event)

        # Claude recovers
        assert sheets[1].status == BatonSheetStatus.PENDING
        # Gemini still waiting
        assert sheets[3].status == BatonSheetStatus.WAITING


# =========================================================================
# Test: Circuit breaker updates from attempt results
# =========================================================================


class TestCircuitBreakerIntegration:
    """Test that attempt results update instrument circuit breaker state."""

    @pytest.mark.asyncio
    async def test_successful_attempt_resets_instrument_failures(self) -> None:
        """A successful execution resets the instrument's failure counter."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(1, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        # Simulate some prior failures
        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.consecutive_failures = 3

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
        )
        await baton.handle_event(event)

        assert inst.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_failed_attempt_increments_instrument_failures(self) -> None:
        """A failed execution increments the instrument's failure counter."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(1, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        assert inst.consecutive_failures == 0

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            exit_code=1,
        )
        await baton.handle_event(event)

        assert inst.consecutive_failures == 1


# =========================================================================
# Test: Rate-limited instruments affect dispatch
# =========================================================================


class TestRateLimitDispatchIntegration:
    """Test that rate-limited instruments are excluded from dispatch config."""

    def test_get_rate_limited_instruments(self) -> None:
        """get_rate_limited_instruments returns currently limited instruments."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        baton.register_instrument("gemini-cli", max_concurrent=2)

        # Initially none rate-limited
        assert baton.get_rate_limited_instruments() == set()

        # Mark claude as rate-limited
        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.rate_limited = True

        limited = baton.get_rate_limited_instruments()
        assert "claude-code" in limited
        assert "gemini-cli" not in limited

    def test_get_open_circuit_breakers(self) -> None:
        """get_open_circuit_breakers returns instruments with open breakers."""
        from marianne.daemon.baton.state import CircuitBreakerState

        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        baton.register_instrument("gemini-cli", max_concurrent=2)

        # Initially none open
        assert baton.get_open_circuit_breakers() == set()

        # Trip claude's circuit breaker
        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.circuit_breaker = CircuitBreakerState.OPEN

        breakers = baton.get_open_circuit_breakers()
        assert "claude-code" in breakers
        assert "gemini-cli" not in breakers


# =========================================================================
# Test: Cross-job rate limit isolation
# =========================================================================


class TestCrossJobRateLimits:
    """Rate limits are per-instrument, NOT per-job. They affect all jobs."""

    @pytest.mark.asyncio
    async def test_rate_limit_affects_all_jobs_on_instrument(self) -> None:
        """A rate limit from job A affects dispatched sheets in job B."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)

        sheets_a = _make_sheets(1, "claude-code")
        sheets_a[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("job-a", sheets_a, {})

        sheets_b = _make_sheets(1, "claude-code")
        sheets_b[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("job-b", sheets_b, {})

        # Rate limit from job-a
        event = RateLimitHit(
            instrument="claude-code",
            wait_seconds=60.0,
            job_id="job-a",
            sheet_num=1,
        )
        await baton.handle_event(event)

        # Both jobs' sheets affected
        assert sheets_a[1].status == BatonSheetStatus.WAITING
        assert sheets_b[1].status == BatonSheetStatus.WAITING

        # Instrument is rate-limited
        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        assert inst.rate_limited is True
