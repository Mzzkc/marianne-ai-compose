"""TDD tests for dispatch_ready() fallback wiring.

The dispatch path must call _check_and_fallback_unavailable() when an
instrument is unavailable at dispatch time (circuit breaker OPEN or
instrument not registered). Currently dispatch_ready() just skips these
sheets — the fallback chain is never invoked.

Tests:
1. Circuit breaker OPEN + fallback chain → sheet falls back, not skipped
2. Unregistered instrument + fallback chain → sheet falls back, not skipped
3. Rate-limited instrument → sheet is skipped (no fallback — transient)
4. Circuit breaker OPEN + no fallback chain → sheet stays skipped
5. Fallback instrument itself is rate-limited → original skipped, no loop

TDD: Red first, then green.
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import DispatchConfig, dispatch_ready
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    CircuitBreakerState,
    InstrumentState,
    SheetExecutionState,
)


async def _noop_callback(job_id: str, sheet_num: int, state: SheetExecutionState) -> None:
    """Do nothing — used when we don't expect dispatch to reach the callback."""
    pass


dispatched: list[tuple[str, int]] = []


async def _tracking_callback(job_id: str, sheet_num: int, state: SheetExecutionState) -> None:
    """Track which sheets were dispatched."""
    dispatched.append((job_id, sheet_num))


@pytest.fixture(autouse=True)
def _clear_dispatched() -> None:
    dispatched.clear()


class TestDispatchFallbackOnCircuitBreaker:
    """When a circuit breaker is OPEN and a fallback chain exists,
    dispatch_ready() should invoke the fallback, not just skip."""

    async def test_circuit_breaker_open_triggers_fallback(self) -> None:
        """Sheet with fallback chain should switch instrument when primary
        has an open circuit breaker, then become dispatchable."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=3,
            fallback_chain=["gemini-cli"],
        )
        baton.register_job("j1", {1: sheet}, {})

        # Mark claude-code as having open circuit breaker
        baton._instruments["claude-code"] = InstrumentState(
            name="claude-code",
            max_concurrent=4,
        )
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN

        # Register gemini-cli as available
        baton._instruments["gemini-cli"] = InstrumentState(
            name="gemini-cli",
            max_concurrent=4,
        )

        # Make the sheet ready
        s = baton._jobs["j1"].sheets[1]
        s.status = BatonSheetStatus.READY

        config = DispatchConfig(
            max_concurrent_sheets=10,
            open_circuit_breakers={"claude-code"},
        )

        await dispatch_ready(baton, config, _tracking_callback)

        # The sheet should have fallen back to gemini-cli and been dispatched
        assert s.instrument_name == "gemini-cli"
        assert ("j1", 1) in dispatched

    async def test_circuit_breaker_no_fallback_stays_skipped(self) -> None:
        """Sheet without fallback chain should be skipped normally when
        circuit breaker is open."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=3,
            # No fallback chain
        )
        baton.register_job("j1", {1: sheet}, {})

        baton._instruments["claude-code"] = InstrumentState(
            name="claude-code",
            max_concurrent=4,
        )
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN

        s = baton._jobs["j1"].sheets[1]
        s.status = BatonSheetStatus.READY

        config = DispatchConfig(
            max_concurrent_sheets=10,
            open_circuit_breakers={"claude-code"},
        )

        result = await dispatch_ready(baton, config, _noop_callback)

        # Should be skipped, not dispatched
        assert result.dispatched_count == 0
        assert "circuit_breaker:claude-code" in result.skipped_reasons


class TestDispatchFallbackOnRateLimit:
    """Rate-limited instruments should NOT trigger fallback at dispatch time.
    Rate limits are transient — the baton waits for recovery."""

    async def test_rate_limited_with_fallback_advances_at_dispatch(self) -> None:
        """A rate-limited primary with a fallback advances at dispatch (GH #521).

        Until #521 this test pinned the opposite ("skipped, not fallen back"),
        while the in-flight rate-limit handler already advanced the same sheet
        to its fallback; a loop member reopened on its limited primary waited
        out the whole limit beside idle fallback seats. Dispatch and the
        in-flight handler now make the same transition. A sheet with no
        fallback still waits (test_reopened_rate_limited_fallback_521).
        """
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=3,
            fallback_chain=["gemini-cli"],
        )
        baton.register_job("j1", {1: sheet}, {})
        baton.register_instrument("gemini-cli")

        s = baton._jobs["j1"].sheets[1]
        s.status = BatonSheetStatus.READY

        config = DispatchConfig(
            max_concurrent_sheets=10,
            rate_limited_instruments={"claude-code"},
        )

        result = await dispatch_ready(baton, config, _noop_callback)

        assert result.dispatched_count == 1
        assert s.instrument_name == "gemini-cli"


class TestDispatchFallbackChainMultiple:
    """Fallback chain with multiple entries works through the chain."""

    async def test_fallback_through_two_instruments(self) -> None:
        """First fallback also unavailable → falls back to second."""
        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=3,
            fallback_chain=["gemini-cli", "ollama"],
        )
        baton.register_job("j1", {1: sheet}, {})

        # Mark both claude-code and gemini-cli as circuit breaker OPEN
        baton._instruments["claude-code"] = InstrumentState(
            name="claude-code",
            max_concurrent=4,
        )
        baton._instruments["claude-code"].circuit_breaker = CircuitBreakerState.OPEN

        baton._instruments["gemini-cli"] = InstrumentState(
            name="gemini-cli",
            max_concurrent=4,
        )
        baton._instruments["gemini-cli"].circuit_breaker = CircuitBreakerState.OPEN

        baton._instruments["ollama"] = InstrumentState(
            name="ollama",
            max_concurrent=4,
        )

        s = baton._jobs["j1"].sheets[1]
        s.status = BatonSheetStatus.READY

        config = DispatchConfig(
            max_concurrent_sheets=10,
            open_circuit_breakers={"claude-code", "gemini-cli"},
        )

        await dispatch_ready(baton, config, _tracking_callback)

        # Should have fallen back through claude-code → gemini-cli → ollama
        assert s.instrument_name == "ollama"
        assert ("j1", 1) in dispatched
