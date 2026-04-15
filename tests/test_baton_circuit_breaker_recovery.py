"""Tests for circuit breaker recovery in the baton.

GH#169: When all instruments in a fallback chain have open circuit breakers,
sheets stay PENDING forever. The baton needs to:

1. Schedule a recovery timer when a circuit breaker opens (OPEN)
2. Transition OPEN→HALF_OPEN when the timer fires
3. Re-trigger dispatch so blocked sheets get a probe attempt

This mirrors the existing rate limit recovery pattern:
  RateLimitHit → timer → RateLimitExpired → WAITING→PENDING → dispatch

TDD: Tests written before implementation. Red first, then green.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    CircuitBreakerRecovery,
    SheetAttemptResult,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    CircuitBreakerState,
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


# =========================================================================
# Test: Circuit breaker recovery event exists
# =========================================================================


class TestCircuitBreakerRecoveryEvent:
    """Test that the CircuitBreakerRecovery event type exists and works."""

    def test_event_creation(self) -> None:
        """CircuitBreakerRecovery event can be created with instrument name."""
        event = CircuitBreakerRecovery(instrument="claude-code")
        assert event.instrument == "claude-code"
        assert event.timestamp > 0

    def test_event_is_frozen(self) -> None:
        """CircuitBreakerRecovery event is immutable."""
        event = CircuitBreakerRecovery(instrument="claude-code")
        with pytest.raises(AttributeError):
            event.instrument = "something-else"  # type: ignore[misc]


# =========================================================================
# Test: record_failure sets recovery_at and schedules timer
# =========================================================================


class TestCircuitBreakerRecoveryScheduling:
    """Test that tripping a circuit breaker schedules a recovery timer."""

    @pytest.mark.asyncio
    async def test_failure_at_threshold_schedules_recovery(self) -> None:
        """When consecutive failures hit the threshold, a recovery timer is scheduled."""
        inbox = MagicMock()
        inbox.put_nowait = MagicMock()
        timer = TimerWheel(inbox)

        baton = BatonCore(timer=timer)
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(1, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        # Set failures just below threshold
        inst.consecutive_failures = inst.circuit_breaker_threshold - 1

        # This failure should trip the breaker
        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )
        await baton.handle_event(event)

        assert inst.circuit_breaker == CircuitBreakerState.OPEN
        assert inst.circuit_breaker_recovery_at is not None
        assert inst.circuit_breaker_recovery_at > time.monotonic()

    @pytest.mark.asyncio
    async def test_recovery_timer_scheduled_on_timer_wheel(self) -> None:
        """When circuit breaker opens, a CircuitBreakerRecovery event is
        scheduled on the timer wheel."""
        import asyncio

        inbox = asyncio.Queue()
        timer = TimerWheel(inbox)

        baton = BatonCore(timer=timer)
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(1, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.consecutive_failures = inst.circuit_breaker_threshold - 1

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )
        await baton.handle_event(event)

        # Timer wheel should have a scheduled event
        assert timer.pending_count > 0

    @pytest.mark.asyncio
    async def test_half_open_to_open_reschedules_recovery(self) -> None:
        """When a HALF_OPEN probe fails (→ OPEN), a new recovery timer is scheduled."""
        inbox = MagicMock()
        inbox.put_nowait = MagicMock()
        timer = TimerWheel(inbox)

        baton = BatonCore(timer=timer)
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(1, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        # Manually set to HALF_OPEN (simulating recovery timer already fired)
        inst.circuit_breaker = CircuitBreakerState.HALF_OPEN
        inst.consecutive_failures = inst.circuit_breaker_threshold

        # Probe failure → HALF_OPEN → OPEN
        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )
        await baton.handle_event(event)

        assert inst.circuit_breaker == CircuitBreakerState.OPEN
        assert inst.circuit_breaker_recovery_at is not None

    @pytest.mark.asyncio
    async def test_no_timer_scheduled_when_breaker_already_open(self) -> None:
        """Additional failures on an already-OPEN breaker don't schedule new timers."""
        inbox = MagicMock()
        inbox.put_nowait = MagicMock()
        timer = TimerWheel(inbox)

        baton = BatonCore(timer=timer)
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(1, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        # Already OPEN
        inst.circuit_breaker = CircuitBreakerState.OPEN
        inst.consecutive_failures = inst.circuit_breaker_threshold + 5
        original_recovery_at = time.monotonic() + 100
        inst.circuit_breaker_recovery_at = original_recovery_at

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )
        await baton.handle_event(event)

        # Still OPEN, but recovery_at should not change (no new timer)
        assert inst.circuit_breaker == CircuitBreakerState.OPEN


# =========================================================================
# Test: CircuitBreakerRecovery event handler transitions OPEN → HALF_OPEN
# =========================================================================


class TestCircuitBreakerRecoveryHandler:
    """Test the CircuitBreakerRecovery event handler."""

    @pytest.mark.asyncio
    async def test_recovery_transitions_open_to_half_open(self) -> None:
        """CircuitBreakerRecovery transitions instrument from OPEN to HALF_OPEN."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.circuit_breaker = CircuitBreakerState.OPEN
        inst.consecutive_failures = 5
        inst.circuit_breaker_recovery_at = time.monotonic()

        event = CircuitBreakerRecovery(instrument="claude-code")
        await baton.handle_event(event)

        assert inst.circuit_breaker == CircuitBreakerState.HALF_OPEN
        assert inst.circuit_breaker_recovery_at is None

    @pytest.mark.asyncio
    async def test_recovery_noop_if_already_closed(self) -> None:
        """CircuitBreakerRecovery is a no-op if the breaker already recovered."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.circuit_breaker = CircuitBreakerState.CLOSED

        event = CircuitBreakerRecovery(instrument="claude-code")
        await baton.handle_event(event)

        assert inst.circuit_breaker == CircuitBreakerState.CLOSED

    @pytest.mark.asyncio
    async def test_recovery_noop_if_already_half_open(self) -> None:
        """CircuitBreakerRecovery is a no-op if already HALF_OPEN."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.circuit_breaker = CircuitBreakerState.HALF_OPEN

        event = CircuitBreakerRecovery(instrument="claude-code")
        await baton.handle_event(event)

        assert inst.circuit_breaker == CircuitBreakerState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_recovery_for_unknown_instrument_is_safe(self) -> None:
        """CircuitBreakerRecovery for an unregistered instrument doesn't crash."""
        baton = BatonCore()

        event = CircuitBreakerRecovery(instrument="unknown-instrument")
        await baton.handle_event(event)  # Should not raise


# =========================================================================
# Test: Recovery delay uses exponential backoff
# =========================================================================


class TestCircuitBreakerRecoveryDelay:
    """Test that recovery delay increases with consecutive failures."""

    @pytest.mark.asyncio
    async def test_base_recovery_delay(self) -> None:
        """First circuit breaker trip uses the base recovery delay (30s)."""
        inbox = MagicMock()
        inbox.put_nowait = MagicMock()
        timer = TimerWheel(inbox)

        baton = BatonCore(timer=timer)
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(1, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.consecutive_failures = inst.circuit_breaker_threshold - 1

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )
        await baton.handle_event(event)

        assert inst.circuit_breaker_recovery_at is not None
        expected_at = time.monotonic() + 30.0
        # Allow some tolerance for test execution time
        assert abs(inst.circuit_breaker_recovery_at - expected_at) < 2.0

    @pytest.mark.asyncio
    async def test_recovery_delay_increases_with_failures(self) -> None:
        """More failures beyond the threshold increase the recovery delay."""
        inbox = MagicMock()
        inbox.put_nowait = MagicMock()
        timer = TimerWheel(inbox)

        baton = BatonCore(timer=timer)
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(1, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        # HALF_OPEN → OPEN transition (after first recovery attempt failed)
        inst.circuit_breaker = CircuitBreakerState.HALF_OPEN
        inst.consecutive_failures = inst.circuit_breaker_threshold + 1

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )
        await baton.handle_event(event)

        assert inst.circuit_breaker_recovery_at is not None
        # Excess failures = 2 (threshold + 2 - threshold),
        # delay = 30 * 2^2 = 120s
        expected_at = time.monotonic() + 120.0
        assert abs(inst.circuit_breaker_recovery_at - expected_at) < 2.0

    @pytest.mark.asyncio
    async def test_recovery_delay_capped_at_maximum(self) -> None:
        """Recovery delay is capped at 300 seconds regardless of failure count."""
        inbox = MagicMock()
        inbox.put_nowait = MagicMock()
        timer = TimerWheel(inbox)

        baton = BatonCore(timer=timer)
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(1, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.circuit_breaker = CircuitBreakerState.HALF_OPEN
        inst.consecutive_failures = inst.circuit_breaker_threshold + 100

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
        )
        await baton.handle_event(event)

        assert inst.circuit_breaker_recovery_at is not None
        expected_at = time.monotonic() + 300.0
        assert abs(inst.circuit_breaker_recovery_at - expected_at) < 2.0


# =========================================================================
# Test: End-to-end — circuit breaker recovery unblocks stuck sheets
# =========================================================================


class TestCircuitBreakerRecoveryUnblocksSheets:
    """End-to-end: circuit breaker recovery allows previously-blocked sheets
    to be dispatched again."""

    @pytest.mark.asyncio
    async def test_half_open_instrument_no_longer_in_open_set(self) -> None:
        """After recovery, instrument is not in the open circuit breakers set."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.circuit_breaker = CircuitBreakerState.OPEN
        inst.circuit_breaker_recovery_at = time.monotonic()

        # Before recovery
        assert "claude-code" in baton.get_open_circuit_breakers()

        # Fire recovery
        event = CircuitBreakerRecovery(instrument="claude-code")
        await baton.handle_event(event)

        # After recovery
        assert "claude-code" not in baton.get_open_circuit_breakers()

    @pytest.mark.asyncio
    async def test_successful_probe_closes_breaker(self) -> None:
        """A successful attempt after HALF_OPEN closes the breaker fully."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        sheets = _make_sheets(1, "claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None
        inst.circuit_breaker = CircuitBreakerState.HALF_OPEN
        inst.consecutive_failures = 5

        # Successful probe
        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
        )
        await baton.handle_event(event)

        assert inst.circuit_breaker == CircuitBreakerState.CLOSED
        assert inst.consecutive_failures == 0
