"""Adversarial tests for the baton infrastructure.

These tests attack the baton's decision-making code with boundary
conditions, malformed inputs, race-condition-like sequences, and
edge cases that happy-path tests don't cover. Every test here
represents a production failure mode that was NOT caught by the
existing test suite.

Categories:
- F-018 validation_pass_rate contract landmine
- State serialization round-trip adversarial
- Circuit breaker state machine boundaries
- Dispatch logic edge cases
- Event handler safety (unknown jobs, already-terminal sheets)
- Timer wheel adversarial conditions
- BatonJobState edge cases
- Multi-event interleaving scenarios

@pytest.mark.adversarial
"""

from __future__ import annotations

import asyncio
import time

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import (
    DispatchConfig,
    dispatch_ready,
)
from marianne.daemon.baton.events import (
    CancelJob,
    EscalationNeeded,
    EscalationResolved,
    EscalationTimeout,
    JobTimeout,
    PauseJob,
    ProcessExited,
    RateLimitExpired,
    RateLimitHit,
    ResumeJob,
    RetryDue,
    SheetAttemptResult,
    SheetSkipped,
    ShutdownRequested,
)
from marianne.daemon.baton.state import (
    BatonJobState,
    BatonSheetStatus,
    CircuitBreakerState,
    InstrumentState,
    SheetExecutionState,
)
from marianne.daemon.baton.state import (
    SheetExecutionState as RichSheetExecutionState,
)
from marianne.daemon.baton.timer import TimerWheel

# ===========================================================================
# F-018: validation_pass_rate contract — the landmine
# ===========================================================================


class TestF018ValidationPassRateContract:
    """F-018: The default validation_pass_rate=0.0 is a landmine.

    When a musician reports execution_success=True with no validations
    but forgets to set validation_pass_rate=100.0, the baton treats
    it as "all validations failed" and retries unnecessarily.

    These tests document the exact behavior so step 22's builder
    knows the contract.
    """

    @pytest.mark.adversarial
    async def test_default_pass_rate_with_no_validations_causes_retry(self) -> None:
        """The F-018 landmine: success + default 0.0 rate = unnecessary retry."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=3,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Musician reports success but forgets to set validation_pass_rate
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_passed=0,
                validations_total=0,
                validation_pass_rate=0.0,  # DEFAULT — the landmine
            )
        )

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # F-018 FIXED (Axiom, Movement 1): The baton now treats
        # execution_success=True with validations_total=0 as 100% pass
        # rate, regardless of the default validation_pass_rate value.
        # This prevents unnecessary retries when no validations exist.
        assert state.status == "completed", (
            "Sheet with no validations and execution_success=True should "
            "complete (F-018 guard)"
        )
        assert state.normal_attempts == 0

    @pytest.mark.adversarial
    async def test_explicit_100_with_no_validations_completes(self) -> None:
        """The correct musician behavior: set 100.0 when no validations."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_passed=0,
                validations_total=0,
                validation_pass_rate=100.0,  # CORRECT — musician sets this
            )
        )

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "completed"

    @pytest.mark.adversarial
    async def test_pass_rate_just_below_100_retries(self) -> None:
        """99.99 is not 100.0 — boundary test."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_passed=99,
                validations_total=100,
                validation_pass_rate=99.99,
            )
        )

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # 99.99 < 100.0, so this is partial pass — retries
        assert state.status != "completed"

    @pytest.mark.adversarial
    async def test_pass_rate_above_100_still_completes(self) -> None:
        """Malformed rate > 100 — should still be treated as complete."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=150.0,  # Malformed but >= 100
            )
        )

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "completed"

    @pytest.mark.adversarial
    async def test_f018_no_validations_completes_on_first_attempt(self) -> None:
        """F-018 FIXED: no validations + default rate completes immediately.

        Previously this would exhaust all retries and fail. Now the baton
        recognizes validations_total=0 with execution_success=True as
        100% pass rate.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
            ),
        }
        baton.register_job("j1", sheets, {})

        # First attempt succeeds with no validations — should complete
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_total=0,
                validation_pass_rate=0.0,  # Default — no longer a landmine
            )
        )

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # Sheet completes on first attempt (F-018 guard active)
        assert state.status == "completed"
        assert state.normal_attempts == 0


# ===========================================================================
# State serialization adversarial tests
# ===========================================================================


class TestSheetExecutionStateSerialization:
    """Adversarial tests for SheetExecutionState to_dict/from_dict."""

    def test_roundtrip_preserves_all_fields(self) -> None:
        """Every field survives a round-trip through dict serialization."""
        state = RichSheetExecutionState(
            sheet_num=42,
            instrument_name="gemini-cli",
            status=BatonSheetStatus.FERMATA,
            normal_attempts=5,
            completion_attempts=2,
            healing_attempts=1,
            max_retries=10,
            max_completion=8,
            total_cost_usd=3.14159,
            total_duration_seconds=999.999,
            next_retry_at=12345.678,
        )
        restored = RichSheetExecutionState.from_dict(state.to_dict())
        assert restored.sheet_num == 42
        assert restored.instrument_name == "gemini-cli"
        assert restored.status == BatonSheetStatus.FERMATA
        assert restored.normal_attempts == 5
        assert restored.completion_attempts == 2
        assert restored.healing_attempts == 1
        assert restored.max_retries == 10
        assert restored.max_completion == 8
        assert restored.total_cost_usd == pytest.approx(3.14159)
        assert restored.total_duration_seconds == pytest.approx(999.999)
        # next_retry_at is transient (monotonic time, exclude=True) —
        # not preserved across serialization roundtrips. This is correct:
        # timers are lost on restart and must be rescheduled.
        assert restored.next_retry_at is None

    @pytest.mark.adversarial
    def test_from_dict_missing_optional_fields_uses_defaults(self) -> None:
        """Minimal dict (only required fields) uses safe defaults."""
        data = {
            "sheet_num": 1,
            "instrument_name": "claude-code",
        }
        state = RichSheetExecutionState.from_dict(data)
        assert state.sheet_num == 1
        assert state.instrument_name == "claude-code"
        assert state.status == BatonSheetStatus.PENDING
        assert state.normal_attempts == 0
        assert state.max_retries == 3
        assert state.total_cost_usd == 0.0
        assert state.next_retry_at is None

    @pytest.mark.adversarial
    def test_from_dict_invalid_status_raises(self) -> None:
        """Invalid status string raises ValueError."""
        data = {
            "sheet_num": 1,
            "instrument_name": "claude-code",
            "status": "on_fire",
        }
        with pytest.raises(ValueError, match="on_fire"):
            RichSheetExecutionState.from_dict(data)

    @pytest.mark.adversarial
    def test_from_dict_with_extra_keys_ignored(self) -> None:
        """Extra keys in the dict are silently ignored — forward compat."""
        data = {
            "sheet_num": 1,
            "instrument_name": "claude-code",
            "future_field": "some_value",
            "another_future": 42,
        }
        # Should not raise
        state = RichSheetExecutionState.from_dict(data)
        assert state.sheet_num == 1

    @pytest.mark.adversarial
    def test_zero_cost_and_duration_preserved(self) -> None:
        """Zero values are preserved, not treated as missing."""
        state = RichSheetExecutionState(
            sheet_num=1,
            instrument_name="test",
            total_cost_usd=0.0,
            total_duration_seconds=0.0,
        )
        restored = RichSheetExecutionState.from_dict(state.to_dict())
        assert restored.total_cost_usd == 0.0
        assert restored.total_duration_seconds == 0.0

    @pytest.mark.adversarial
    def test_attempt_results_not_serialized(self) -> None:
        """attempt_results are NOT in to_dict — they're too large for SQLite."""
        state = RichSheetExecutionState(sheet_num=1, instrument_name="test")
        d = state.to_dict()
        assert "attempt_results" not in d


class TestInstrumentStateSerialization:
    """Adversarial tests for InstrumentState to_dict/from_dict."""

    def test_roundtrip_preserves_all_fields(self) -> None:
        """Full round-trip fidelity."""
        state = InstrumentState(
            name="claude-code",
            max_concurrent=4,
            rate_limited=True,
            rate_limit_expires_at=99999.0,
            circuit_breaker=CircuitBreakerState.HALF_OPEN,
            consecutive_failures=3,
            circuit_breaker_threshold=10,
            circuit_breaker_recovery_at=88888.0,
        )
        restored = InstrumentState.from_dict(state.to_dict())
        assert restored.name == "claude-code"
        assert restored.max_concurrent == 4
        assert restored.rate_limited is True
        assert restored.rate_limit_expires_at == pytest.approx(99999.0)
        assert restored.circuit_breaker == CircuitBreakerState.HALF_OPEN
        assert restored.consecutive_failures == 3
        assert restored.circuit_breaker_threshold == 10
        assert restored.circuit_breaker_recovery_at == pytest.approx(88888.0)

    @pytest.mark.adversarial
    def test_from_dict_minimal_uses_defaults(self) -> None:
        """Minimal dict uses safe defaults."""
        data = {"name": "test-instrument"}
        state = InstrumentState.from_dict(data)
        assert state.name == "test-instrument"
        assert state.max_concurrent == 4  # default
        assert state.rate_limited is False
        assert state.circuit_breaker == CircuitBreakerState.CLOSED
        assert state.consecutive_failures == 0

    @pytest.mark.adversarial
    def test_from_dict_invalid_circuit_breaker_raises(self) -> None:
        """Invalid circuit breaker string raises ValueError."""
        data = {
            "name": "test",
            "max_concurrent": 4,
            "circuit_breaker": "exploded",
        }
        with pytest.raises(ValueError, match="exploded"):
            InstrumentState.from_dict(data)


# ===========================================================================
# Circuit breaker state machine adversarial tests
# ===========================================================================


class TestCircuitBreakerAdversarial:
    """Edge cases in the circuit breaker state transitions."""

    @pytest.mark.adversarial
    def test_threshold_of_one_trips_on_first_failure(self) -> None:
        """With threshold=1, a single failure opens the breaker."""
        state = InstrumentState(
            name="fragile",
            max_concurrent=4,
            circuit_breaker_threshold=1,
        )
        assert state.circuit_breaker == CircuitBreakerState.CLOSED
        state.record_failure()
        assert state.circuit_breaker == CircuitBreakerState.OPEN
        assert state.consecutive_failures == 1

    @pytest.mark.adversarial
    def test_success_resets_failure_count_before_threshold(self) -> None:
        """A success at threshold - 1 resets to 0, preventing trip."""
        state = InstrumentState(
            name="resilient",
            max_concurrent=4,
            circuit_breaker_threshold=5,
        )
        # 4 failures (one short of threshold)
        for _ in range(4):
            state.record_failure()
        assert state.consecutive_failures == 4
        assert state.circuit_breaker == CircuitBreakerState.CLOSED

        # One success resets
        state.record_success()
        assert state.consecutive_failures == 0
        assert state.circuit_breaker == CircuitBreakerState.CLOSED

    @pytest.mark.adversarial
    def test_record_failure_when_already_open_stays_open(self) -> None:
        """Additional failures after breaker opens don't change state."""
        state = InstrumentState(
            name="broken",
            max_concurrent=4,
            circuit_breaker_threshold=2,
        )
        state.record_failure()
        state.record_failure()
        assert state.circuit_breaker == CircuitBreakerState.OPEN

        # 100 more failures — still open
        for _ in range(100):
            state.record_failure()
        assert state.circuit_breaker == CircuitBreakerState.OPEN
        assert state.consecutive_failures == 102

    @pytest.mark.adversarial
    def test_half_open_failure_reopens(self) -> None:
        """A probe failure in HALF_OPEN goes back to OPEN."""
        state = InstrumentState(
            name="probing",
            max_concurrent=4,
            circuit_breaker=CircuitBreakerState.HALF_OPEN,
            consecutive_failures=5,
        )
        state.record_failure()
        assert state.circuit_breaker == CircuitBreakerState.OPEN

    @pytest.mark.adversarial
    def test_half_open_success_closes(self) -> None:
        """A successful probe in HALF_OPEN closes the breaker."""
        state = InstrumentState(
            name="recovering",
            max_concurrent=4,
            circuit_breaker=CircuitBreakerState.HALF_OPEN,
            consecutive_failures=10,
        )
        state.record_success()
        assert state.circuit_breaker == CircuitBreakerState.CLOSED
        assert state.consecutive_failures == 0

    @pytest.mark.adversarial
    def test_success_when_closed_is_noop_on_breaker(self) -> None:
        """Success in CLOSED state — breaker stays closed."""
        state = InstrumentState(
            name="healthy",
            max_concurrent=4,
        )
        state.record_success()
        assert state.circuit_breaker == CircuitBreakerState.CLOSED
        assert state.consecutive_failures == 0

    @pytest.mark.adversarial
    def test_at_capacity_with_zero_max_concurrent(self) -> None:
        """max_concurrent=0 means always at capacity."""
        state = InstrumentState(
            name="zero-capacity",
            max_concurrent=0,
        )
        assert state.at_capacity is True

    @pytest.mark.adversarial
    def test_is_available_checks_rate_limit_before_circuit_breaker(self) -> None:
        """Rate-limited instrument is unavailable even if circuit is closed."""
        state = InstrumentState(
            name="rate-limited",
            max_concurrent=4,
            rate_limited=True,
            circuit_breaker=CircuitBreakerState.CLOSED,
        )
        assert state.is_available is False


# ===========================================================================
# Dispatch logic adversarial tests
# ===========================================================================


class TestDispatchAdversarial:
    """Edge cases in the dispatch_ready function."""

    @pytest.mark.adversarial
    async def test_zero_max_concurrent_dispatches_nothing(self) -> None:
        """max_concurrent_sheets=0 means no sheets ever dispatch."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        dispatched: list[tuple[str, int]] = []

        async def callback(
            job_id: str, sheet_num: int, _state: SheetExecutionState
        ) -> None:
            dispatched.append((job_id, sheet_num))

        config = DispatchConfig(max_concurrent_sheets=0)
        result = await dispatch_ready(baton, config, callback)

        assert result.dispatched_count == 0
        assert len(dispatched) == 0

    @pytest.mark.adversarial
    async def test_callback_exception_does_not_crash_dispatch(self) -> None:
        """A failing callback for one sheet doesn't prevent other dispatches."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
        }
        baton.register_job("j1", sheets, {})

        call_order: list[int] = []

        async def failing_callback(
            _job_id: str, sheet_num: int, _state: SheetExecutionState
        ) -> None:
            call_order.append(sheet_num)
            if sheet_num == 1:
                msg = "Backend unavailable"
                raise RuntimeError(msg)

        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, failing_callback)

        # Sheet 1 failed but sheet 2 should still dispatch
        assert 2 in [s for _, s in result.dispatched_sheets]

    @pytest.mark.adversarial
    async def test_rate_limited_instrument_skipped(self) -> None:
        """Sheets on rate-limited instruments are skipped."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
        }
        baton.register_job("j1", sheets, {})

        dispatched: list[tuple[str, int]] = []

        async def callback(
            job_id: str, sheet_num: int, _state: SheetExecutionState
        ) -> None:
            dispatched.append((job_id, sheet_num))

        config = DispatchConfig(
            max_concurrent_sheets=10,
            rate_limited_instruments={"claude-code"},
        )
        result = await dispatch_ready(baton, config, callback)

        # Only gemini-cli sheet should dispatch
        dispatched_sheet_nums = [s for _, s in dispatched]
        assert 1 not in dispatched_sheet_nums
        assert 2 in dispatched_sheet_nums
        assert "rate_limited:claude-code" in result.skipped_reasons

    @pytest.mark.adversarial
    async def test_circuit_breaker_skips_instrument(self) -> None:
        """Sheets on instruments with open circuit breakers are skipped."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="broken-inst"),
        }
        baton.register_job("j1", sheets, {})

        dispatched: list[tuple[str, int]] = []

        async def callback(
            job_id: str, sheet_num: int, _state: SheetExecutionState
        ) -> None:
            dispatched.append((job_id, sheet_num))

        config = DispatchConfig(
            max_concurrent_sheets=10,
            open_circuit_breakers={"broken-inst"},
        )
        result = await dispatch_ready(baton, config, callback)

        assert result.dispatched_count == 0
        assert "circuit_breaker:broken-inst" in result.skipped_reasons

    @pytest.mark.adversarial
    async def test_all_instruments_blocked_dispatches_nothing(self) -> None:
        """Every instrument blocked (rate limit + circuit breaker) = no dispatch."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="inst-a"),
            2: SheetExecutionState(sheet_num=2, instrument_name="inst-b"),
        }
        baton.register_job("j1", sheets, {})

        dispatched: list[tuple[str, int]] = []

        async def callback(
            job_id: str, sheet_num: int, _state: SheetExecutionState
        ) -> None:
            dispatched.append((job_id, sheet_num))

        config = DispatchConfig(
            max_concurrent_sheets=10,
            rate_limited_instruments={"inst-a"},
            open_circuit_breakers={"inst-b"},
        )
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 0

    @pytest.mark.adversarial
    async def test_dispatch_during_shutdown(self) -> None:
        """No dispatches after ShutdownRequested."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(ShutdownRequested(graceful=True))

        dispatched: list[tuple[str, int]] = []

        async def callback(
            job_id: str, sheet_num: int, _state: SheetExecutionState
        ) -> None:
            dispatched.append((job_id, sheet_num))

        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 0


# ===========================================================================
# Event handler safety — unknown jobs, already-terminal sheets
# ===========================================================================


class TestEventHandlerSafety:
    """Events targeting non-existent jobs or already-terminal sheets."""

    @pytest.mark.adversarial
    async def test_attempt_result_unknown_job_is_safe(self) -> None:
        """SheetAttemptResult for non-existent job doesn't crash."""
        baton = BatonCore()
        # No jobs registered
        await baton.handle_event(
            SheetAttemptResult(
                job_id="ghost-job",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )
        # Should not crash — just returns

    @pytest.mark.adversarial
    async def test_attempt_result_unknown_sheet_is_safe(self) -> None:
        """SheetAttemptResult for non-existent sheet doesn't crash."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=999,  # Does not exist
                instrument_name="claude-code",
                attempt=1,
                execution_success=False,
            )
        )
        # Should not crash

    @pytest.mark.adversarial
    async def test_retry_due_for_completed_sheet_is_noop(self) -> None:
        """RetryDue for an already-completed sheet does nothing."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        # Complete the sheet
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )
        assert baton.get_sheet_state("j1", 1).status == "completed"
        # Late RetryDue arrives — should not change status
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))
        assert baton.get_sheet_state("j1", 1).status == "completed"
    @pytest.mark.adversarial
    async def test_process_exited_for_non_dispatched_sheet_is_noop(self) -> None:
        """ProcessExited for a sheet not in 'dispatched' status is ignored."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})
        assert baton.get_sheet_state("j1", 1).status == "pending"
        await baton.handle_event(
            ProcessExited(job_id="j1", sheet_num=1, pid=12345, exit_code=1)
        )

        # Status should be unchanged — not dispatched, so process exit is irrelevant
        assert baton.get_sheet_state("j1", 1).status == "pending"
    @pytest.mark.adversarial
    async def test_cancel_nonexistent_job_is_safe(self) -> None:
        """CancelJob for a non-existent job doesn't crash."""
        baton = BatonCore()
        await baton.handle_event(CancelJob(job_id="no-such-job"))

    @pytest.mark.adversarial
    async def test_pause_nonexistent_job_is_safe(self) -> None:
        """PauseJob for a non-existent job doesn't crash."""
        baton = BatonCore()
        await baton.handle_event(PauseJob(job_id="no-such-job"))

    @pytest.mark.adversarial
    async def test_resume_nonexistent_job_is_safe(self) -> None:
        """ResumeJob for a non-existent job doesn't crash."""
        baton = BatonCore()
        await baton.handle_event(ResumeJob(job_id="no-such-job"))

    @pytest.mark.adversarial
    async def test_rate_limit_expired_with_no_waiting_sheets(self) -> None:
        """RateLimitExpired when no sheets are waiting — no crash, no state change."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code", status=BatonSheetStatus.PENDING
            ),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(RateLimitExpired(instrument="claude-code"))
        # Pending sheet should still be pending (not affected)
        assert baton.get_sheet_state("j1", 1).status == "pending"
    @pytest.mark.adversarial
    async def test_escalation_resolved_for_non_fermata_sheet_is_noop(self) -> None:
        """EscalationResolved for a sheet not in fermata does nothing."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(
            EscalationResolved(
                job_id="j1", sheet_num=1, decision="retry"
            )
        )
        # Sheet is pending, not fermata — decision should be ignored
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "pending"

    @pytest.mark.adversarial
    async def test_escalation_unknown_decision_fails_sheet(self) -> None:
        """An unknown escalation decision defaults to failing the sheet."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        # Put sheet in fermata
        await baton.handle_event(
            EscalationNeeded(job_id="j1", sheet_num=1, reason="test")
        )
        assert baton.get_sheet_state("j1", 1).status == "fermata"
        # Resolve with unknown decision
        await baton.handle_event(
            EscalationResolved(
                job_id="j1", sheet_num=1, decision="unknown_decision"
            )
        )
        assert baton.get_sheet_state("j1", 1).status == "failed"
    @pytest.mark.adversarial
    async def test_double_register_job_is_noop(self) -> None:
        """Registering the same job_id twice is silently ignored."""
        baton = BatonCore()
        sheets1 = {
            1: SheetExecutionState(sheet_num=1, instrument_name="inst-a"),
        }
        sheets2 = {
            1: SheetExecutionState(sheet_num=1, instrument_name="inst-b"),
        }
        baton.register_job("j1", sheets1, {})
        baton.register_job("j1", sheets2, {})  # duplicate

        # Original registration wins
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.instrument_name == "inst-a"


# ===========================================================================
# Multi-event interleaving and state machine stress
# ===========================================================================


class TestMultiEventInterleaving:
    """Complex event sequences that test state machine correctness."""

    @pytest.mark.adversarial
    async def test_job_timeout_preserves_completed_sheets(self) -> None:
        """Timeout cancels non-terminal sheets but leaves completed ones alone."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        # Complete sheet 1
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )

        # Timeout the job
        await baton.handle_event(JobTimeout(job_id="j1"))

        assert baton.get_sheet_state("j1", 1).status == "completed"
        assert baton.get_sheet_state("j1", 2).status == "cancelled"
        assert baton.get_sheet_state("j1", 3).status == "cancelled"

    @pytest.mark.adversarial
    async def test_graceful_shutdown_does_not_cancel_in_flight(self) -> None:
        """Graceful shutdown only sets flag — doesn't cancel dispatched sheets."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                status=BatonSheetStatus.DISPATCHED,
            ),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(ShutdownRequested(graceful=True))

        # Dispatched sheet should still be dispatched (graceful)
        assert baton.get_sheet_state("j1", 1).status == "dispatched"
        # Pending sheet is also not cancelled in graceful mode
        assert baton.get_sheet_state("j1", 2).status == "pending"

    @pytest.mark.adversarial
    async def test_non_graceful_shutdown_cancels_everything(self) -> None:
        """Non-graceful shutdown cancels all non-terminal sheets."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                status=BatonSheetStatus.DISPATCHED,
            ),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(
                sheet_num=3,
                instrument_name="claude-code",
                status=BatonSheetStatus.COMPLETED,
            ),
        }
        # Manually set sheet 3 as completed
        sheets[3].status = BatonSheetStatus.COMPLETED
        baton.register_job("j1", sheets, {})

        await baton.handle_event(ShutdownRequested(graceful=False))

        assert baton.get_sheet_state("j1", 1).status == "cancelled"
        assert baton.get_sheet_state("j1", 2).status == "cancelled"
        assert baton.get_sheet_state("j1", 3).status == "completed"
    @pytest.mark.adversarial
    async def test_rate_limit_then_expired_restores_sheets(self) -> None:
        """Rate limit → waiting → expired → pending. Full cycle.

        Note: RateLimitHit only affects dispatched/running sheets (Axiom fix).
        A pending sheet should not transition to waiting.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="gemini-cli"),
        }
        baton.register_job("j1", sheets, {})

        # Sheet 1 must be dispatched for rate limit to transition it
        sheets[1].status = BatonSheetStatus.DISPATCHED

        # Rate limit on claude-code
        await baton.handle_event(
            RateLimitHit(
                instrument="claude-code",
                wait_seconds=60.0,
                job_id="j1",
                sheet_num=1,
            )
        )
        assert baton.get_sheet_state("j1", 1).status == "waiting"
        # gemini-cli sheet should be unaffected
        assert baton.get_sheet_state("j1", 3).status == "pending"
        # Rate limit expires
        await baton.handle_event(RateLimitExpired(instrument="claude-code"))

        # Claude sheets restored to pending; gemini unaffected
        assert baton.get_sheet_state("j1", 1).status == "pending"
        assert baton.get_sheet_state("j1", 3).status == "pending"
    @pytest.mark.adversarial
    async def test_rate_limit_expired_only_affects_matching_instrument(self) -> None:
        """RateLimitExpired for inst-A doesn't restore inst-B waiting sheets."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="inst-a",
                status=BatonSheetStatus.WAITING,
            ),
            2: SheetExecutionState(
                sheet_num=2,
                instrument_name="inst-b",
                status=BatonSheetStatus.WAITING,
            ),
        }
        sheets[1].status = BatonSheetStatus.WAITING
        sheets[2].status = BatonSheetStatus.WAITING
        baton.register_job("j1", sheets, {})

        # Only clear inst-a
        await baton.handle_event(RateLimitExpired(instrument="inst-a"))

        assert baton.get_sheet_state("j1", 1).status == "pending"
        assert baton.get_sheet_state("j1", 2).status == "waiting"
    @pytest.mark.adversarial
    async def test_process_exit_exhausts_retries(self) -> None:
        """ProcessExited counts toward retry budget and can exhaust it."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
                status=BatonSheetStatus.DISPATCHED,
            ),
        }
        sheets[1].status = BatonSheetStatus.DISPATCHED
        baton.register_job("j1", sheets, {})

        # First crash — retry scheduled
        await baton.handle_event(
            ProcessExited(job_id="j1", sheet_num=1, pid=111, exit_code=-9)
        )
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.normal_attempts == 1
        assert state.status == "retry_scheduled"

        # Move back to dispatched for second crash
        state.status = BatonSheetStatus.DISPATCHED

        # Second crash — retries exhausted
        await baton.handle_event(
            ProcessExited(job_id="j1", sheet_num=1, pid=222, exit_code=-9)
        )
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "failed"
        assert state.normal_attempts == 2

    @pytest.mark.adversarial
    async def test_pause_resume_pause_cycle(self) -> None:
        """Rapid pause-resume-pause cycle maintains correct state."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        assert not baton.is_job_paused("j1")
        await baton.handle_event(PauseJob(job_id="j1"))
        assert baton.is_job_paused("j1")
        await baton.handle_event(ResumeJob(job_id="j1"))
        assert not baton.is_job_paused("j1")
        await baton.handle_event(PauseJob(job_id="j1"))
        assert baton.is_job_paused("j1")

    @pytest.mark.adversarial
    async def test_escalation_full_lifecycle(self) -> None:
        """Escalation: need → fermata → resolve → back to pending."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        # Enter escalation
        await baton.handle_event(
            EscalationNeeded(job_id="j1", sheet_num=1, reason="ambiguous output")
        )
        assert baton.get_sheet_state("j1", 1).status == "fermata"
        assert baton.is_job_paused("j1")

        # Resolve with retry
        await baton.handle_event(
            EscalationResolved(job_id="j1", sheet_num=1, decision="retry")
        )
        assert baton.get_sheet_state("j1", 1).status == "pending"
        assert not baton.is_job_paused("j1")

    @pytest.mark.adversarial
    async def test_escalation_timeout_fails_sheet_and_unpauses(self) -> None:
        """Escalation timeout: fermata → failed, job unpaused."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(
            EscalationNeeded(job_id="j1", sheet_num=1, reason="test")
        )
        assert baton.is_job_paused("j1")

        await baton.handle_event(
            EscalationTimeout(job_id="j1", sheet_num=1)
        )
        assert baton.get_sheet_state("j1", 1).status == "failed"
        assert not baton.is_job_paused("j1")

    @pytest.mark.adversarial
    async def test_cancel_job_deregisters_after_cancelling(self) -> None:
        """CancelJob cancels non-terminal sheets then deregisters the job."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})
        assert baton.job_count == 1

        await baton.handle_event(CancelJob(job_id="j1"))
        assert baton.job_count == 0
        assert baton.get_sheet_state("j1", 1) is None


# ===========================================================================
# Timer wheel adversarial tests
# ===========================================================================


class TestTimerWheelAdversarial:
    """Edge cases for the timer wheel scheduling."""

    @pytest.mark.adversarial
    async def test_negative_delay_fires_immediately(self) -> None:
        """Negative delay is clamped to 0 and fires immediately."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        event = RetryDue(job_id="j1", sheet_num=1)
        handle = wheel.schedule(-5.0, event)
        assert handle.fire_at <= time.monotonic()

    @pytest.mark.adversarial
    async def test_cancel_already_cancelled_returns_false(self) -> None:
        """Cancelling twice returns False on second attempt."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        event = RetryDue(job_id="j1", sheet_num=1)
        handle = wheel.schedule(60.0, event)

        assert wheel.cancel(handle) is True
        assert wheel.cancel(handle) is False

    @pytest.mark.adversarial
    async def test_snapshot_excludes_cancelled(self) -> None:
        """Snapshot only includes non-cancelled timers."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        h1 = wheel.schedule(10.0, RetryDue(job_id="j1", sheet_num=1))
        wheel.schedule(20.0, RetryDue(job_id="j1", sheet_num=2))
        wheel.schedule(30.0, RetryDue(job_id="j1", sheet_num=3))

        wheel.cancel(h1)

        snap = wheel.snapshot()
        assert len(snap) == 2
        # h1 should not be in snapshot
        sheet_nums = [e.sheet_num for _, e in snap if hasattr(e, "sheet_num")]
        assert 1 not in sheet_nums

    @pytest.mark.adversarial
    async def test_many_immediate_timers_all_fire(self) -> None:
        """100 timers with delay=0 all fire."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        for i in range(100):
            wheel.schedule(0.0, RetryDue(job_id="j1", sheet_num=i))

        # Run briefly to let them fire
        task = asyncio.create_task(wheel.run())
        await asyncio.sleep(0.1)

        fired = 0
        while not inbox.empty():
            await inbox.get()
            fired += 1

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert fired == 100

    @pytest.mark.adversarial
    async def test_shutdown_fires_all_remaining(self) -> None:
        """Shutdown fires every pending timer into the inbox."""
        inbox: asyncio.Queue[object] = asyncio.Queue()
        wheel = TimerWheel(inbox)

        for i in range(10):
            wheel.schedule(999.0, RetryDue(job_id="j1", sheet_num=i))

        # Cancel 3 of them
        # Schedule new ones and cancel them is complex, let's just test shutdown fires rest
        await wheel.shutdown()

        fired = 0
        while not inbox.empty():
            await inbox.get()
            fired += 1

        assert fired == 10


# ===========================================================================
# BatonJobState edge cases
# ===========================================================================


class TestBatonJobStateAdversarial:
    """Edge cases in the BatonJobState aggregate model."""

    @pytest.mark.adversarial
    def test_empty_job_is_not_complete(self) -> None:
        """A job with no sheets is never 'complete'."""
        job = BatonJobState(job_id="j1", total_sheets=0)
        assert job.is_complete is False

    @pytest.mark.adversarial
    def test_cost_aggregation_with_zero_cost_sheets(self) -> None:
        """Cost aggregation works even when all sheets cost $0.00."""
        job = BatonJobState(job_id="j1", total_sheets=3)
        for i in range(1, 4):
            job.register_sheet(
                RichSheetExecutionState(
                    sheet_num=i,
                    instrument_name="test",
                    total_cost_usd=0.0,
                )
            )
        assert job.total_cost_usd == pytest.approx(0.0)

    @pytest.mark.adversarial
    def test_mixed_terminal_states_count_correctly(self) -> None:
        """completed + failed + skipped all count as terminal."""
        job = BatonJobState(job_id="j1", total_sheets=4)

        s1 = RichSheetExecutionState(sheet_num=1, instrument_name="t")
        s1.status = BatonSheetStatus.COMPLETED
        s2 = RichSheetExecutionState(sheet_num=2, instrument_name="t")
        s2.status = BatonSheetStatus.FAILED
        s3 = RichSheetExecutionState(sheet_num=3, instrument_name="t")
        s3.status = BatonSheetStatus.SKIPPED
        s4 = RichSheetExecutionState(sheet_num=4, instrument_name="t")
        s4.status = BatonSheetStatus.PENDING

        for s in [s1, s2, s3, s4]:
            job.register_sheet(s)

        assert job.terminal_count == 3
        assert job.completed_count == 1
        assert job.is_complete is False  # s4 is still pending
        assert job.has_any_failed is True

    @pytest.mark.adversarial
    def test_running_sheets_property(self) -> None:
        """running_sheets returns only RUNNING status sheets."""
        job = BatonJobState(job_id="j1", total_sheets=3)
        for i, status in enumerate(
            [BatonSheetStatus.IN_PROGRESS, BatonSheetStatus.PENDING, BatonSheetStatus.IN_PROGRESS],
            start=1,
        ):
            s = RichSheetExecutionState(sheet_num=i, instrument_name="t")
            s.status = status
            job.register_sheet(s)

        running = job.running_sheets
        assert len(running) == 2
        assert all(s.status == BatonSheetStatus.IN_PROGRESS for s in running)


# ===========================================================================
# Dependency resolution stress tests
# ===========================================================================


class TestDependencyStress:
    """Stress tests for dependency resolution with large graphs."""

    @pytest.mark.adversarial
    async def test_long_chain_dependency(self) -> None:
        """100-sheet linear chain: each depends on the previous."""
        baton = BatonCore()
        chain_length = 100
        sheets = {
            i: SheetExecutionState(sheet_num=i, instrument_name="claude-code")
            for i in range(1, chain_length + 1)
        }
        deps = {i: [i - 1] for i in range(2, chain_length + 1)}
        baton.register_job("j1", sheets, deps)

        # Only sheet 1 should be ready initially
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 1
        assert ready[0].sheet_num == 1

        # Complete each sheet in sequence — next becomes ready
        for i in range(1, chain_length + 1):
            await baton.handle_event(
                SheetAttemptResult(
                    job_id="j1",
                    sheet_num=i,
                    instrument_name="claude-code",
                    attempt=1,
                    execution_success=True,
                    validation_pass_rate=100.0,
                )
            )
            if i < chain_length:
                ready = baton.get_ready_sheets("j1")
                assert len(ready) == 1
                assert ready[0].sheet_num == i + 1

        assert baton.is_job_complete("j1")

    @pytest.mark.adversarial
    async def test_failed_dependency_blocks_downstream(self) -> None:
        """A failed dependency keeps downstream sheets non-ready."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=1,
            ),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        deps = {2: [1]}
        baton.register_job("j1", sheets, deps)

        # Fail sheet 1 (exhaust retries)
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=False,
                error_classification="PERSISTENT",
            )
        )

        # Sheet 2 should NOT be ready (failed dep is not satisfied)
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 0

    @pytest.mark.adversarial
    async def test_skipped_dependency_satisfies_downstream(self) -> None:
        """A skipped dependency satisfies downstream sheets."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        deps = {2: [1]}
        baton.register_job("j1", sheets, deps)

        # Skip sheet 1
        await baton.handle_event(
            SheetSkipped(job_id="j1", sheet_num=1, reason="skip_when matched")
        )

        # Sheet 2 should be ready
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 1
        assert ready[0].sheet_num == 2


# ===========================================================================
# RichSheetExecutionState method adversarial tests
# ===========================================================================


class TestRichSheetExecutionStateAdversarial:
    """Edge cases in the state.py SheetExecutionState methods."""

    @pytest.mark.adversarial
    def test_record_attempt_accumulates_cost(self) -> None:
        """Cost accumulates correctly across multiple attempts."""
        state = RichSheetExecutionState(
            sheet_num=1, instrument_name="claude-code"
        )
        for i in range(5):
            result = SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=i + 1,
                execution_success=False,
                cost_usd=0.10,
                duration_seconds=30.0,
            )
            state.record_attempt(result)

        assert state.total_cost_usd == pytest.approx(0.50)
        assert state.total_duration_seconds == pytest.approx(150.0)
        assert state.normal_attempts == 5
        assert len(state.attempt_results) == 5

    @pytest.mark.adversarial
    def test_record_rate_limited_attempt_no_count_increment(self) -> None:
        """Rate-limited attempts are recorded but don't increment normal_attempts."""
        state = RichSheetExecutionState(
            sheet_num=1, instrument_name="claude-code"
        )
        for i in range(10):
            result = SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=i + 1,
                execution_success=False,
                rate_limited=True,
                cost_usd=0.0,
                duration_seconds=1.0,
            )
            state.record_attempt(result)

        assert state.normal_attempts == 0
        assert len(state.attempt_results) == 10
        assert state.total_duration_seconds == pytest.approx(10.0)
        assert state.can_retry is True

    @pytest.mark.adversarial
    def test_is_exhausted_requires_both_budgets_spent(self) -> None:
        """is_exhausted is True only when BOTH retry and completion budgets are done."""
        state = RichSheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            max_retries=2,
            max_completion=3,
        )
        state.normal_attempts = 2
        assert state.is_exhausted is False  # completion budget remains

        state.completion_attempts = 3
        assert state.is_exhausted is True

    @pytest.mark.adversarial
    def test_total_attempts_across_all_modes(self) -> None:
        """total_attempts sums normal + completion + healing."""
        state = RichSheetExecutionState(
            sheet_num=1, instrument_name="claude-code"
        )
        state.normal_attempts = 3
        state.completion_attempts = 2
        state.healing_attempts = 1
        assert state.total_attempts == 6
