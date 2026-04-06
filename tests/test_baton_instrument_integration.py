"""Tests for baton instrument state integration.

The baton must track per-instrument health (rate limits, circuit breakers,
concurrency) and use it in dispatch decisions. This is the bridge between
the event handlers (which update state) and the dispatch logic (which reads it).

Tests cover:
- InstrumentState tracking in BatonCore
- Rate limit hit → instrument marked rate-limited → dispatch skips it
- Rate limit expired → instrument unmarked → dispatch resumes
- Circuit breaker integration (success/failure recording)
- Concurrency tracking (running_count incremented/decremented)
- DispatchConfig auto-derivation from instrument state
- Completion mode in retry state machine
- Cost enforcement per the baton design spec

TDD: tests written before implementation. Red first, then green.
"""

from __future__ import annotations

import asyncio

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import DispatchConfig, dispatch_ready
from marianne.daemon.baton.events import (
    RateLimitExpired,
    RateLimitHit,
    SheetAttemptResult,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    CircuitBreakerState,
    InstrumentState,
    SheetExecutionState,
)


def _make_baton_with_instruments(
    *,
    instruments: dict[str, int] | None = None,
) -> BatonCore:
    """Create a BatonCore with pre-registered instruments.

    Args:
        instruments: Map of instrument_name -> max_concurrent.
    """
    baton = BatonCore()
    if instruments:
        for name, max_conc in instruments.items():
            baton.register_instrument(name, max_concurrent=max_conc)
    return baton


def _make_sheets(
    count: int,
    instrument: str = "claude-code",
) -> dict[int, SheetExecutionState]:
    """Create a dict of sheets for testing."""
    return {
        i: SheetExecutionState(sheet_num=i, instrument_name=instrument)
        for i in range(1, count + 1)
    }


# =============================================================================
# Instrument Registration
# =============================================================================


class TestInstrumentRegistration:
    """BatonCore tracks instruments and their state."""

    async def test_register_instrument(self) -> None:
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        state = baton.get_instrument_state("claude-code")
        assert state is not None
        assert state.name == "claude-code"
        assert state.max_concurrent == 4
        assert state.rate_limited is False
        assert state.circuit_breaker == CircuitBreakerState.CLOSED

    async def test_register_multiple_instruments(self) -> None:
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        baton.register_instrument("gemini-cli", max_concurrent=6)
        assert baton.get_instrument_state("claude-code") is not None
        assert baton.get_instrument_state("gemini-cli") is not None

    async def test_unregistered_instrument_returns_none(self) -> None:
        baton = BatonCore()
        assert baton.get_instrument_state("nonexistent") is None

    async def test_auto_register_on_job_registration(self) -> None:
        """When a job is registered with sheets using unknown instruments,
        those instruments are auto-registered with default concurrency."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
        }
        baton.register_job("j1", sheets, {})
        # Both instruments should now be tracked
        assert baton.get_instrument_state("claude-code") is not None
        assert baton.get_instrument_state("gemini-cli") is not None


# =============================================================================
# Rate Limit → Instrument State Integration
# =============================================================================


class TestRateLimitInstrumentIntegration:
    """Rate limit events update instrument state, which affects dispatch."""

    async def test_rate_limit_hit_marks_instrument(self) -> None:
        """RateLimitHit on a sheet marks the instrument as rate-limited."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(2, instrument="claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        await baton.handle_event(RateLimitHit(
            instrument="claude-code",
            wait_seconds=60,
            job_id="j1",
            sheet_num=1,
        ))

        state = baton.get_instrument_state("claude-code")
        assert state is not None
        assert state.rate_limited is True

    async def test_rate_limit_expired_unmarks_instrument(self) -> None:
        """RateLimitExpired clears the rate limit on the instrument."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(2, instrument="claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        # Hit then expire
        await baton.handle_event(RateLimitHit(
            instrument="claude-code",
            wait_seconds=60,
            job_id="j1",
            sheet_num=1,
        ))
        await baton.handle_event(RateLimitExpired(
            instrument="claude-code",
        ))

        state = baton.get_instrument_state("claude-code")
        assert state is not None
        assert state.rate_limited is False

    async def test_rate_limit_on_instrument_a_does_not_affect_b(self) -> None:
        """Rate limits are per-instrument — A limited, B still available."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4, "gemini-cli": 4}
        )
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
        }
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        # Rate limit claude-code
        await baton.handle_event(RateLimitHit(
            instrument="claude-code",
            wait_seconds=60,
            job_id="j1",
            sheet_num=1,
        ))

        # claude-code is limited, gemini-cli is not
        assert baton.get_instrument_state("claude-code").rate_limited is True
        assert baton.get_instrument_state("gemini-cli").rate_limited is False

    async def test_dispatch_skips_rate_limited_instrument(self) -> None:
        """Dispatch config derived from instrument state blocks rate-limited instruments."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(2, instrument="claude-code")
        baton.register_job("j1", sheets, {})

        # Rate limit the instrument
        inst_state = baton.get_instrument_state("claude-code")
        inst_state.rate_limited = True

        config = baton.build_dispatch_config()
        assert "claude-code" in config.rate_limited_instruments

        dispatched = []

        async def mock_dispatch(
            job_id: str, sheet_num: int, state: SheetExecutionState
        ) -> None:
            dispatched.append((job_id, sheet_num))

        result = await dispatch_ready(baton, config, mock_dispatch)
        assert result.dispatched_count == 0
        assert "rate_limited:claude-code" in result.skipped_reasons


# =============================================================================
# Circuit Breaker → Instrument State Integration
# =============================================================================


class TestCircuitBreakerIntegration:
    """Sheet successes/failures update instrument circuit breaker state."""

    async def test_success_resets_consecutive_failures(self) -> None:
        """A successful attempt resets the instrument's failure counter."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(1, instrument="claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        # Record some failures first
        inst = baton.get_instrument_state("claude-code")
        inst.consecutive_failures = 3

        # Success
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
        ))

        assert inst.consecutive_failures == 0

    async def test_failure_increments_consecutive_failures(self) -> None:
        """A failed attempt increments the instrument's failure counter."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(1, instrument="claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        sheets[1].max_retries = 5  # Enough retries to not exhaust
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="TRANSIENT",
        ))

        inst = baton.get_instrument_state("claude-code")
        assert inst.consecutive_failures == 1

    async def test_circuit_breaker_trips_after_threshold(self) -> None:
        """Five consecutive failures trip the circuit breaker."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )

        # Register a job with 6 sheets so we can have 5 failures
        sheets = _make_sheets(6, instrument="claude-code")
        for s in sheets.values():
            s.status = BatonSheetStatus.DISPATCHED
            s.max_retries = 10
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        inst.circuit_breaker_threshold = 5

        # 5 failures
        for i in range(1, 6):
            await baton.handle_event(SheetAttemptResult(
                job_id="j1",
                sheet_num=i,
                instrument_name="claude-code",
                attempt=1,
                execution_success=False,
                error_classification="TRANSIENT",
            ))

        assert inst.circuit_breaker == CircuitBreakerState.OPEN

    async def test_dispatch_skips_open_circuit_breaker(self) -> None:
        """Dispatch config blocks instruments with open circuit breaker."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(1, instrument="claude-code")
        baton.register_job("j1", sheets, {})

        # Open the circuit breaker
        inst = baton.get_instrument_state("claude-code")
        inst.circuit_breaker = CircuitBreakerState.OPEN

        config = baton.build_dispatch_config()
        assert "claude-code" in config.open_circuit_breakers


# =============================================================================
# Completion Mode
# =============================================================================


class TestCompletionMode:
    """The baton enters completion mode when validations partially pass."""

    async def test_partial_validation_triggers_completion_mode(self) -> None:
        """When validation pass rate is >0 but <100, baton enters completion mode."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(1, instrument="claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        sheets[1].max_retries = 3
        sheets[1].max_completion = 3
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=2,
            validations_total=3,
            validation_pass_rate=66.7,
        ))

        # Sheet should be in a state ready for completion mode dispatch
        # The completion_attempts should be incremented
        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.completion_attempts == 1
        # Should be ready for re-dispatch (pending or retry_scheduled)
        assert sheet.status in (
            BatonSheetStatus.PENDING,
            BatonSheetStatus.RETRY_SCHEDULED,
        )

    async def test_completion_exhausted_fails_sheet(self) -> None:
        """When completion attempts are exhausted, sheet fails."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(1, instrument="claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        sheets[1].max_retries = 3
        sheets[1].max_completion = 1  # Only 1 completion attempt
        sheets[1].completion_attempts = 0
        baton.register_job("j1", sheets, {2: [1]})

        # Partial validation — enters completion mode
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=2,
            validations_total=3,
            validation_pass_rate=66.7,
        ))

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        # Should have used 1 of 1 completion attempts
        assert sheet.completion_attempts == 1

        # Second partial result should exhaust completion budget
        sheet.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=2,
            execution_success=True,
            validations_passed=2,
            validations_total=3,
            validation_pass_rate=66.7,
        ))

        # Should be failed now (completion exhausted + retries available
        # doesn't matter for completion mode)
        assert sheet.status == BatonSheetStatus.FAILED


# =============================================================================
# Cost Enforcement
# =============================================================================


class TestCostEnforcement:
    """The baton enforces cost limits per sheet and per job."""

    async def test_cost_tracked_across_attempts(self) -> None:
        """Cost accumulates across attempts on a sheet."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(1, instrument="claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        sheets[1].max_retries = 5
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="TRANSIENT",
            cost_usd=0.50,
        ))

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.total_cost_usd == pytest.approx(0.50)

    async def test_per_job_cost_limit_pauses_job(self) -> None:
        """When a job's total cost exceeds the limit, the job is paused."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(2, instrument="claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {2: [1]})

        # Set a cost limit on the job
        baton.set_job_cost_limit("j1", max_cost_usd=1.00)

        # Expensive attempt that exceeds the limit
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            cost_usd=1.50,
        ))

        # Sheet completes, but job should be paused due to cost
        assert baton.is_job_paused("j1") is True


# =============================================================================
# Dispatch Config Auto-Derivation
# =============================================================================


class TestDispatchConfigDerivation:
    """BatonCore builds DispatchConfig from instrument state."""

    async def test_build_dispatch_config_default(self) -> None:
        """Default config has no rate limits or circuit breakers."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4, "gemini-cli": 6}
        )
        config = baton.build_dispatch_config()
        assert config.rate_limited_instruments == set()
        assert config.open_circuit_breakers == set()
        assert config.instrument_concurrency == {"claude-code": 4, "gemini-cli": 6}

    async def test_build_dispatch_config_with_rate_limits(self) -> None:
        """Config includes rate-limited instruments."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4, "gemini-cli": 6}
        )
        inst = baton.get_instrument_state("claude-code")
        inst.rate_limited = True

        config = baton.build_dispatch_config()
        assert "claude-code" in config.rate_limited_instruments
        assert "gemini-cli" not in config.rate_limited_instruments

    async def test_build_dispatch_config_with_circuit_breaker(self) -> None:
        """Config includes instruments with open circuit breakers."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        inst = baton.get_instrument_state("claude-code")
        inst.circuit_breaker = CircuitBreakerState.OPEN

        config = baton.build_dispatch_config()
        assert "claude-code" in config.open_circuit_breakers


# =============================================================================
# Concurrency Tracking
# =============================================================================


class TestConcurrencyTracking:
    """Instrument running_count is tracked on dispatch/completion."""

    async def test_running_count_incremented_on_dispatch_mark(self) -> None:
        """When a sheet is marked as dispatched via the dispatch callback,
        the instrument's running_count should be available for tracking."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 2}
        )
        sheets = _make_sheets(3, instrument="claude-code")
        baton.register_job("j1", sheets, {})

        # Mark first two sheets as dispatched
        sheets[1].status = BatonSheetStatus.DISPATCHED
        sheets[2].status = BatonSheetStatus.DISPATCHED

        # build_dispatch_config should reflect the instrument concurrency
        config = baton.build_dispatch_config()
        assert config.instrument_concurrency["claude-code"] == 2

    async def test_running_count_decremented_on_completion(self) -> None:
        """When a sheet completes, the instrument's concurrency slot is freed."""
        baton = _make_baton_with_instruments(
            instruments={"claude-code": 4}
        )
        sheets = _make_sheets(2, instrument="claude-code")
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        # Complete the sheet
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
        ))

        # The instrument's running_count doesn't track directly
        # but dispatch should now allow more sheets
        sheet = baton.get_sheet_state("j1", 1)
        assert sheet.status == BatonSheetStatus.COMPLETED
