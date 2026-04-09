"""Movement 3 — property-based invariant verification.

Extends the invariant suite to cover M3 features:

51. Wait cap clamping invariant — _clamp_wait always returns a value in
    [MINIMUM, MAXIMUM] for any float input. No adversarial API response can
    produce an unbounded wait.
52. Clear rate limit specificity — clear_instrument_rate_limit(name) clears
    ONLY that instrument. clear_instrument_rate_limit(None) clears ALL.
    Empty string or unknown names clear NOTHING.
53. Clear rate limit WAITING→PENDING transition — cleared instruments have
    their WAITING sheets moved to PENDING. Other instruments' sheets untouched.
54. Rate limit hit status transition — only DISPATCHED/RUNNING sheets move
    to WAITING. Terminal sheets are never regressed.
55. Observer event classification completeness — attempt_result_to_observer_event
    maps to exactly one of 4 event names. The mapping is deterministic and total.
56. Exhaustion decision tree mutual exclusion — when retries are exhausted,
    exactly one of three paths fires (healing/escalation/fail). Never zero,
    never two.
57. Retry delay monotonicity — calculate_retry_delay is monotonically
    non-decreasing and always clamped to [0, max_retry_delay].
58. State mapping round-trip stability — checkpoint→baton→checkpoint preserves
    the original checkpoint status for all 5 checkpoint status strings.
59. Stagger delay Pydantic bounds enforcement — stagger_delay_ms always
    rejects values outside [0, 5000].
60. Rate limit auto-resume timer scheduling — _handle_rate_limit_hit always
    schedules a RateLimitExpired event when a timer wheel is available.
61. Record attempt budget accounting — record_attempt only increments
    normal_attempts for non-rate-limited, non-successful results.
62. F-018 guard for no-validation success — execution_success=True with
    validations_total=0 always completes, regardless of validation_pass_rate.
63. Terminal state resistance across all handler entry points — sheets in
    terminal status are never mutated by _handle_attempt_result, _handle_sheet_skipped,
    or _handle_rate_limit_hit.
64. Dispatch failure event guarantee (F-152) — _send_dispatch_failure always
    posts a SheetAttemptResult with execution_success=False to the baton inbox.
65. Clear rate limit idempotency — clearing an already-cleared instrument
    returns 0 and changes no state.

Found by: Theorem, Movement 3
Method: Property-based testing with hypothesis + invariant analysis

@pytest.mark.property_based
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, assume, given, settings

# =============================================================================
# Strategies
# =============================================================================

# Positive floats for wait times — covers edge cases at boundaries
_WAIT_SECONDS = st.floats(
    min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False
)

# Negative floats to test floor clamping
_NEGATIVE_FLOATS = st.floats(
    min_value=-1e12, max_value=-0.001, allow_nan=False, allow_infinity=False
)

# All valid float inputs for clamp (excluding nan/inf)
_ALL_FINITE_FLOATS = st.floats(allow_nan=False, allow_infinity=False)

# Baton sheet statuses
_ALL_BATON_STATUSES = st.sampled_from([
    "pending", "ready", "dispatched", "in_progress", "completed",
    "failed", "skipped", "cancelled", "waiting", "retry_scheduled", "fermata",
])

# Non-negative integers for attempt counts
_ATTEMPT_COUNTS = st.integers(min_value=0, max_value=20)

# Validation pass rates
_PASS_RATES = st.floats(min_value=0.0, max_value=100.0, allow_nan=False)

# Cost values
_COSTS = st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False)

# Duration values
_DURATIONS = st.floats(min_value=0.0, max_value=86400.0, allow_nan=False, allow_infinity=False)

# Stagger delay values (wider than valid to test boundary rejection)
_STAGGER_VALUES = st.integers(min_value=-100, max_value=10000)


# =============================================================================
# Invariant 51: Wait cap clamping
# =============================================================================


class TestWaitCapClamping:
    """_clamp_wait always returns a value in [MINIMUM, MAXIMUM]."""

    @given(seconds=_ALL_FINITE_FLOATS)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_clamp_output_bounded(self, seconds: float) -> None:
        """For any finite float, _clamp_wait returns a value in [MIN, MAX]."""
        from marianne.core.constants import (
            RESET_TIME_MAXIMUM_WAIT_SECONDS,
            RESET_TIME_MINIMUM_WAIT_SECONDS,
        )
        from marianne.core.errors.classifier import ErrorClassifier

        result = ErrorClassifier._clamp_wait(seconds)
        assert result >= RESET_TIME_MINIMUM_WAIT_SECONDS, (
            f"_clamp_wait({seconds}) = {result} < MINIMUM {RESET_TIME_MINIMUM_WAIT_SECONDS}"
        )
        assert result <= RESET_TIME_MAXIMUM_WAIT_SECONDS, (
            f"_clamp_wait({seconds}) = {result} > MAXIMUM {RESET_TIME_MAXIMUM_WAIT_SECONDS}"
        )

    @given(seconds=_WAIT_SECONDS)
    @settings(max_examples=100)
    def test_clamp_preserves_valid_range(self, seconds: float) -> None:
        """Values already within bounds are preserved (up to floor clamping)."""
        from marianne.core.constants import (
            RESET_TIME_MAXIMUM_WAIT_SECONDS,
            RESET_TIME_MINIMUM_WAIT_SECONDS,
        )
        from marianne.core.errors.classifier import ErrorClassifier

        result = ErrorClassifier._clamp_wait(seconds)
        if RESET_TIME_MINIMUM_WAIT_SECONDS <= seconds <= RESET_TIME_MAXIMUM_WAIT_SECONDS:
            assert result == seconds, (
                f"_clamp_wait({seconds}) = {result} but should be identity"
            )

    @given(seconds=_NEGATIVE_FLOATS)
    @settings(max_examples=100)
    def test_clamp_floors_negative(self, seconds: float) -> None:
        """Negative values are floored to MINIMUM."""
        from marianne.core.constants import RESET_TIME_MINIMUM_WAIT_SECONDS
        from marianne.core.errors.classifier import ErrorClassifier

        result = ErrorClassifier._clamp_wait(seconds)
        assert result == RESET_TIME_MINIMUM_WAIT_SECONDS

    def test_clamp_idempotent(self) -> None:
        """_clamp_wait(_clamp_wait(x)) == _clamp_wait(x) for representative values."""
        from marianne.core.errors.classifier import ErrorClassifier

        for val in [-1000, -1, 0, 0.5, 5, 30, 3600, 86400, 1e9]:
            first = ErrorClassifier._clamp_wait(val)
            second = ErrorClassifier._clamp_wait(first)
            assert first == second, (
                f"_clamp_wait not idempotent: _clamp_wait({val}) = {first}, "
                f"_clamp_wait({first}) = {second}"
            )


# =============================================================================
# Invariant 52–53: Clear rate limit specificity & WAITING→PENDING
# =============================================================================


class TestClearRateLimitSpecificity:
    """clear_instrument_rate_limit targets correctly and moves WAITING→PENDING."""

    def _make_baton_with_instruments(
        self, names: list[str], rate_limited: list[bool]
    ) -> Any:
        """Create a BatonCore with registered instruments and a job."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        # Register instruments
        for name, limited in zip(names, rate_limited):
            inst = baton.register_instrument(name)
            inst.rate_limited = limited
            if limited:
                inst.rate_limit_expires_at = 99999.0

        # Register a job with sheets on each instrument
        sheets = {}
        for i, name in enumerate(names, 1):
            sheets[i] = SheetExecutionState(
                sheet_num=i,
                instrument_name=name,
                status=BatonSheetStatus.WAITING if rate_limited[i - 1] else BatonSheetStatus.PENDING,
            )
        if sheets:
            baton.register_job("test-job", sheets, {})
        return baton

    @given(
        data=st.data(),
        n_instruments=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_clear_specific_only_affects_target(
        self, data: st.DataObject, n_instruments: int
    ) -> None:
        """Clearing a specific instrument doesn't affect others."""
        names = [f"inst-{i}" for i in range(n_instruments)]
        limited = [data.draw(st.booleans()) for _ in range(n_instruments)]
        assume(any(limited))  # At least one must be rate-limited

        baton = self._make_baton_with_instruments(names, limited)

        # Pick one rate-limited instrument to clear
        limited_names = [n for n, l in zip(names, limited) if l]
        target = data.draw(st.sampled_from(limited_names))

        baton.clear_instrument_rate_limit(target)

        # Target should be cleared
        target_inst = baton.get_instrument_state(target)
        assert target_inst is not None
        assert not target_inst.rate_limited

        # Others should be unchanged
        for name, was_limited in zip(names, limited):
            if name == target:
                continue
            inst = baton.get_instrument_state(name)
            assert inst is not None
            assert inst.rate_limited == was_limited, (
                f"Clearing {target} mutated {name}: "
                f"was_limited={was_limited}, now={inst.rate_limited}"
            )

    @given(n_instruments=st.integers(min_value=1, max_value=5))
    @settings(max_examples=50)
    def test_clear_none_clears_all(self, n_instruments: int) -> None:
        """Clearing with None clears ALL instruments."""
        names = [f"inst-{i}" for i in range(n_instruments)]
        limited = [True] * n_instruments  # All rate-limited

        baton = self._make_baton_with_instruments(names, limited)
        cleared = baton.clear_instrument_rate_limit(None)
        assert cleared == n_instruments

        for name in names:
            inst = baton.get_instrument_state(name)
            assert inst is not None
            assert not inst.rate_limited

    def test_clear_empty_string_clears_nothing(self) -> None:
        """Empty string is NOT treated as None (F-201)."""
        baton = self._make_baton_with_instruments(["a", "b"], [True, True])
        cleared = baton.clear_instrument_rate_limit("")
        assert cleared == 0

        # Both still rate-limited
        for name in ["a", "b"]:
            inst = baton.get_instrument_state(name)
            assert inst is not None
            assert inst.rate_limited

    def test_clear_unknown_clears_nothing(self) -> None:
        """Unknown instrument name returns 0 (F-200)."""
        baton = self._make_baton_with_instruments(["a"], [True])
        cleared = baton.clear_instrument_rate_limit("nonexistent")
        assert cleared == 0
        inst = baton.get_instrument_state("a")
        assert inst is not None
        assert inst.rate_limited

    @given(n_instruments=st.integers(min_value=1, max_value=4))
    @settings(max_examples=50)
    def test_clear_moves_waiting_to_pending(self, n_instruments: int) -> None:
        """Cleared instruments have their WAITING sheets moved to PENDING."""
        from marianne.daemon.baton.state import BatonSheetStatus

        names = [f"inst-{i}" for i in range(n_instruments)]
        limited = [True] * n_instruments
        baton = self._make_baton_with_instruments(names, limited)

        baton.clear_instrument_rate_limit(None)

        # All sheets should now be PENDING
        for job_record in baton._jobs.values():
            for sheet in job_record.sheets.values():
                assert sheet.status == BatonSheetStatus.PENDING, (
                    f"Sheet {sheet.sheet_num} on {sheet.instrument_name} "
                    f"still {sheet.status} after clear"
                )


# =============================================================================
# Invariant 54: Rate limit hit status transition
# =============================================================================


class TestRateLimitHitTransition:
    """Only DISPATCHED/RUNNING sheets transition to WAITING on rate limit."""

    @given(
        status=_ALL_BATON_STATUSES,
        wait_seconds=st.floats(min_value=1.0, max_value=3600.0, allow_nan=False),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_rate_limit_hit_respects_status(
        self, status: str, wait_seconds: float
    ) -> None:
        """_handle_rate_limit_hit only transitions DISPATCHED/RUNNING to WAITING."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import RateLimitHit
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        baton_status = BatonSheetStatus(status)
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="test-inst",
            status=baton_status,
        )
        baton.register_job("j1", {1: sheet}, {})
        baton.register_instrument("test-inst")

        event = RateLimitHit(
            instrument="test-inst",
            wait_seconds=wait_seconds,
            job_id="j1",
            sheet_num=1,
        )
        baton._handle_rate_limit_hit(event)

        if baton_status in (BatonSheetStatus.DISPATCHED, BatonSheetStatus.IN_PROGRESS):
            assert sheet.status == BatonSheetStatus.WAITING
        elif baton_status.is_terminal:
            # Terminal sheets must NEVER regress
            assert sheet.status == baton_status, (
                f"Terminal sheet {baton_status.value} was mutated to {sheet.status.value}"
            )
        else:
            # Non-dispatched, non-terminal sheets are untouched
            assert sheet.status == baton_status


# =============================================================================
# Invariant 55: Observer event classification completeness
# =============================================================================


class TestObserverEventClassification:
    """attempt_result_to_observer_event maps to exactly one of 4 events."""

    _VALID_EVENT_NAMES = frozenset({
        "rate_limit.active",
        "sheet.completed",
        "sheet.partial",
        "sheet.failed",
    })

    @given(
        rate_limited=st.booleans(),
        execution_success=st.booleans(),
        validation_pass_rate=_PASS_RATES,
        cost_usd=_COSTS,
        duration=_DURATIONS,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_classification_is_total_and_deterministic(
        self,
        rate_limited: bool,
        execution_success: bool,
        validation_pass_rate: float,
        cost_usd: float,
        duration: float,
    ) -> None:
        """Every valid SheetAttemptResult maps to exactly one known event."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event
        from marianne.daemon.baton.events import SheetAttemptResult

        result = SheetAttemptResult(
            job_id="test",
            sheet_num=1,
            instrument_name="test-inst",
            attempt=1,
            rate_limited=rate_limited,
            execution_success=execution_success,
            validation_pass_rate=validation_pass_rate,
            cost_usd=cost_usd,
            duration_seconds=duration,
        )

        event = attempt_result_to_observer_event(result)
        event_name = event["event"]
        assert event_name in self._VALID_EVENT_NAMES, (
            f"Unexpected event name: {event_name}"
        )

        # Verify determinism — same input → same output
        event2 = attempt_result_to_observer_event(result)
        assert event["event"] == event2["event"]

    @given(
        execution_success=st.booleans(),
        validation_pass_rate=_PASS_RATES,
    )
    @settings(max_examples=100)
    def test_rate_limited_always_maps_to_rate_limit(
        self, execution_success: bool, validation_pass_rate: float
    ) -> None:
        """rate_limited=True always produces 'rate_limit.active' regardless of other fields."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event
        from marianne.daemon.baton.events import SheetAttemptResult

        result = SheetAttemptResult(
            job_id="test", sheet_num=1, instrument_name="inst", attempt=1,
            rate_limited=True,
            execution_success=execution_success,
            validation_pass_rate=validation_pass_rate,
        )
        event = attempt_result_to_observer_event(result)
        assert event["event"] == "rate_limit.active"


# =============================================================================
# Invariant 56: Exhaustion decision tree mutual exclusion
# =============================================================================


class TestExhaustionDecisionTree:
    """When retries are exhausted, exactly one path fires."""

    @given(
        self_healing=st.booleans(),
        escalation=st.booleans(),
        healing_attempts=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=100)
    def test_exhaustion_exactly_one_path(
        self,
        self_healing: bool,
        escalation: bool,
        healing_attempts: int,
    ) -> None:
        """_handle_exhaustion produces exactly one outcome."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="inst",
            status=BatonSheetStatus.IN_PROGRESS,
            healing_attempts=healing_attempts,
        )
        baton.register_job(
            "j1", {1: sheet}, {},
            self_healing_enabled=self_healing,
            escalation_enabled=escalation,
        )

        baton._handle_exhaustion("j1", 1, sheet)

        # Determine which path was taken
        # Path 1: Fallback (not tested here — no fallback configured)
        # Path 2: Healing — retry_scheduled with healing_attempts incremented
        healing_triggered = sheet.status == BatonSheetStatus.RETRY_SCHEDULED and sheet.healing_attempts > healing_attempts
        # Path 3: Escalation — enters fermata
        escalation_triggered = sheet.status == BatonSheetStatus.FERMATA
        # Path 4: Normal retry (last resort) — retry_scheduled WITHOUT healing increment
        normal_retry_triggered = sheet.status == BatonSheetStatus.RETRY_SCHEDULED and sheet.healing_attempts == healing_attempts
        # Path 5: Failed — no recovery available
        failed = sheet.status == BatonSheetStatus.FAILED

        paths_taken = sum([healing_triggered, escalation_triggered, normal_retry_triggered, failed])
        assert paths_taken == 1, (
            f"Expected exactly 1 path, got {paths_taken}: "
            f"healing={healing_triggered}, escalation={escalation_triggered}, "
            f"normal_retry={normal_retry_triggered}, failed={failed}. "
            f"Status={sheet.status.value}, self_healing={self_healing}, "
            f"escalation={escalation}, healing_attempts={healing_attempts}"
        )

    def test_exhaustion_priority_order(self) -> None:
        """Self-healing takes priority over escalation when both enabled."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="inst",
            status=BatonSheetStatus.IN_PROGRESS,
            healing_attempts=0,  # Budget available
        )
        baton.register_job(
            "j1", {1: sheet}, {},
            self_healing_enabled=True,
            escalation_enabled=True,
        )

        baton._handle_exhaustion("j1", 1, sheet)

        # Healing should take priority
        assert sheet.healing_attempts == 1
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED


# =============================================================================
# Invariant 57: Retry delay monotonicity
# =============================================================================


class TestRetryDelayMonotonicity:
    """calculate_retry_delay is non-decreasing and bounded."""

    @given(attempt=st.integers(min_value=0, max_value=50))
    @settings(max_examples=100)
    def test_delay_bounded_by_max(self, attempt: int) -> None:
        """Delay never exceeds max_retry_delay."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore()
        delay = baton.calculate_retry_delay(attempt)
        assert 0 <= delay <= baton._max_retry_delay, (
            f"Delay {delay} out of bounds for attempt {attempt}"
        )

    @given(attempt=st.integers(min_value=0, max_value=49))
    @settings(max_examples=100)
    def test_delay_non_decreasing(self, attempt: int) -> None:
        """Each subsequent attempt has equal or greater delay."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore()
        d1 = baton.calculate_retry_delay(attempt)
        d2 = baton.calculate_retry_delay(attempt + 1)
        assert d2 >= d1, (
            f"Delay not non-decreasing: attempt {attempt}={d1}, "
            f"attempt {attempt + 1}={d2}"
        )


# =============================================================================
# Invariant 58: State mapping round-trip stability
# =============================================================================


class TestStateMappingRoundTrip:
    """checkpoint→baton→checkpoint preserves status for stable statuses.

    The checkpoint→baton direction intentionally collapses some statuses
    on resume (e.g., waiting→PENDING, in_progress→DISPATCHED) so not all
    11 statuses round-trip. Statuses that DO round-trip are those with a
    1:1 mapping in both directions.
    """

    _ALL_CHECKPOINT_STATUSES = [
        "pending", "ready", "dispatched", "in_progress",
        "waiting", "retry_scheduled", "fermata",
        "completed", "failed", "skipped", "cancelled",
    ]

    # Statuses that survive checkpoint→baton→checkpoint without collapse.
    # Excluded: in_progress (→DISPATCHED→dispatched), waiting (→PENDING→pending),
    # retry_scheduled (→PENDING→pending), fermata (→PENDING→pending).
    _ROUND_TRIP_STABLE = [
        "pending", "ready", "dispatched",
        "completed", "failed", "skipped", "cancelled",
    ]

    @given(status=st.sampled_from(_ROUND_TRIP_STABLE))
    @settings(max_examples=20)
    def test_round_trip_preserves_stable_statuses(self, status: str) -> None:
        """checkpoint_to_baton → baton_to_checkpoint recovers the original for stable statuses."""
        from marianne.daemon.baton.adapter import (
            baton_to_checkpoint_status,
            checkpoint_to_baton_status,
        )

        baton_status = checkpoint_to_baton_status(status)
        recovered = baton_to_checkpoint_status(baton_status)
        assert recovered == status, (
            f"Round trip failed: {status} → {baton_status.value} → {recovered}"
        )

    def test_baton_to_checkpoint_is_total(self) -> None:
        """Every BatonSheetStatus has a checkpoint mapping (totality)."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status
        from marianne.daemon.baton.state import BatonSheetStatus

        for status in BatonSheetStatus:
            result = baton_to_checkpoint_status(status)
            assert isinstance(result, str), (
                f"{status.value} has no checkpoint mapping"
            )
            assert result in self._ALL_CHECKPOINT_STATUSES, (
                f"{status.value} → {result} is not a valid checkpoint status"
            )


# =============================================================================
# Invariant 59: Stagger delay Pydantic bounds
# =============================================================================


class TestStaggerDelayBounds:
    """stagger_delay_ms is always in [0, 5000] or rejected by Pydantic."""

    @given(value=_STAGGER_VALUES)
    @settings(max_examples=100)
    def test_stagger_bounds_enforcement(self, value: int) -> None:
        """Values outside [0, 5000] are rejected; valid values accepted."""
        from pydantic import ValidationError

        from marianne.core.config.execution import ParallelConfig

        if 0 <= value <= 5000:
            config = ParallelConfig(stagger_delay_ms=value)
            assert config.stagger_delay_ms == value
        else:
            with pytest.raises(ValidationError):
                ParallelConfig(stagger_delay_ms=value)


# =============================================================================
# Invariant 60: Rate limit auto-resume timer scheduling
# =============================================================================


class TestRateLimitAutoResumeTimer:
    """_handle_rate_limit_hit schedules a RateLimitExpired timer when available."""

    @given(wait_seconds=st.floats(min_value=0.1, max_value=86400.0, allow_nan=False))
    @settings(max_examples=50)
    def test_timer_scheduled_when_available(self, wait_seconds: float) -> None:
        """When a timer wheel exists, a RateLimitExpired event is scheduled."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import RateLimitExpired, RateLimitHit
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        mock_timer = MagicMock(spec=["schedule"])
        baton = BatonCore(timer=mock_timer)
        baton.register_instrument("inst")

        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="inst",
            status=BatonSheetStatus.DISPATCHED,
        )
        baton.register_job("j1", {1: sheet}, {})

        event = RateLimitHit(
            instrument="inst", wait_seconds=wait_seconds,
            job_id="j1", sheet_num=1,
        )
        baton._handle_rate_limit_hit(event)

        # Timer must have been called
        mock_timer.schedule.assert_called_once()
        call_args = mock_timer.schedule.call_args
        assert call_args[0][0] == wait_seconds
        assert isinstance(call_args[0][1], RateLimitExpired)
        assert call_args[0][1].instrument == "inst"

    def test_no_timer_no_crash(self) -> None:
        """When no timer wheel, rate limit handling still works without crash."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import RateLimitHit
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore(timer=None)
        baton.register_instrument("inst")

        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="inst",
            status=BatonSheetStatus.DISPATCHED,
        )
        baton.register_job("j1", {1: sheet}, {})

        event = RateLimitHit(
            instrument="inst", wait_seconds=60.0,
            job_id="j1", sheet_num=1,
        )
        # Should not raise
        baton._handle_rate_limit_hit(event)
        assert sheet.status == BatonSheetStatus.WAITING


# =============================================================================
# Invariant 61: Record attempt budget accounting
# =============================================================================


class TestRecordAttemptBudget:
    """record_attempt only charges normal_attempts for non-success, non-rate-limited."""

    @given(
        rate_limited=st.booleans(),
        execution_success=st.booleans(),
        cost=_COSTS,
        duration=_DURATIONS,
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_budget_charge_rules(
        self, rate_limited: bool, execution_success: bool,
        cost: float, duration: float,
    ) -> None:
        """Only failed, non-rate-limited attempts increment normal_attempts."""
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.state import SheetExecutionState

        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="inst",
        )
        initial_attempts = sheet.normal_attempts

        result = SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="inst", attempt=1,
            rate_limited=rate_limited,
            execution_success=execution_success,
            cost_usd=cost,
            duration_seconds=duration,
        )
        sheet.record_attempt(result)

        if rate_limited or execution_success:
            assert sheet.normal_attempts == initial_attempts, (
                f"normal_attempts incremented for rate_limited={rate_limited}, "
                f"execution_success={execution_success}"
            )
        else:
            assert sheet.normal_attempts == initial_attempts + 1

        # Cost and duration always accumulate
        assert sheet.total_cost_usd == cost
        assert sheet.total_duration_seconds == duration
        # Result always recorded
        assert len(sheet.attempt_results) == 1


# =============================================================================
# Invariant 62: F-018 guard for no-validation success
# =============================================================================


class TestF018NoValidationGuard:
    """execution_success=True + validations_total=0 always completes."""

    @given(
        pass_rate=st.floats(
            min_value=0.0, max_value=100.0, allow_nan=False,
        ),
    )
    @settings(max_examples=100)
    def test_no_validation_success_completes(self, pass_rate: float) -> None:
        """The F-018 guard ensures no-validation successes complete."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="inst",
            status=BatonSheetStatus.DISPATCHED,
        )
        baton.register_job("j1", {1: sheet}, {})
        baton.register_instrument("inst")

        event = SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="inst", attempt=1,
            execution_success=True,
            validations_total=0,
            validations_passed=0,
            validation_pass_rate=pass_rate,  # Should be overridden by guard
        )
        baton._handle_attempt_result(event)

        assert sheet.status == BatonSheetStatus.COMPLETED, (
            f"No-validation success with pass_rate={pass_rate} "
            f"ended in {sheet.status.value} instead of COMPLETED"
        )


# =============================================================================
# Invariant 63: Terminal state resistance
# =============================================================================


class TestTerminalStateResistance:
    """Terminal sheets are never mutated by any handler."""

    _TERMINAL_STATUSES = ["completed", "failed", "skipped", "cancelled"]

    @given(terminal_status=st.sampled_from(_TERMINAL_STATUSES))
    @settings(max_examples=20)
    def test_attempt_result_terminal_noop(self, terminal_status: str) -> None:
        """_handle_attempt_result ignores terminal sheets."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        baton_status = BatonSheetStatus(terminal_status)
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="inst", status=baton_status,
        )
        baton.register_job("j1", {1: sheet}, {})

        event = SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="inst", attempt=1,
            execution_success=True, validation_pass_rate=100.0,
        )
        baton._handle_attempt_result(event)

        assert sheet.status == baton_status
        assert len(sheet.attempt_results) == 0  # Not even recorded

    @given(terminal_status=st.sampled_from(_TERMINAL_STATUSES))
    @settings(max_examples=20)
    def test_sheet_skipped_terminal_noop(self, terminal_status: str) -> None:
        """_handle_sheet_skipped ignores terminal sheets."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import SheetSkipped
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        baton_status = BatonSheetStatus(terminal_status)
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="inst", status=baton_status,
        )
        baton.register_job("j1", {1: sheet}, {})

        event = SheetSkipped(
            job_id="j1", sheet_num=1, reason="test",
        )
        baton._handle_sheet_skipped(event)

        assert sheet.status == baton_status

    @given(terminal_status=st.sampled_from(_TERMINAL_STATUSES))
    @settings(max_examples=20)
    def test_rate_limit_hit_terminal_noop(self, terminal_status: str) -> None:
        """_handle_rate_limit_hit never regresses terminal sheets."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import RateLimitHit
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        baton_status = BatonSheetStatus(terminal_status)
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="inst", status=baton_status,
        )
        baton.register_job("j1", {1: sheet}, {})
        baton.register_instrument("inst")

        event = RateLimitHit(
            instrument="inst", wait_seconds=60.0,
            job_id="j1", sheet_num=1,
        )
        baton._handle_rate_limit_hit(event)

        assert sheet.status == baton_status


# =============================================================================
# Invariant 64: Dispatch failure event guarantee (F-152)
# =============================================================================


class TestDispatchFailureGuarantee:
    """_send_dispatch_failure always posts a failure event to the inbox."""

    @given(
        sheet_num=st.integers(min_value=1, max_value=100),
        attempt_count=_ATTEMPT_COUNTS,
    )
    @settings(max_examples=50)
    def test_dispatch_failure_posts_event(
        self, sheet_num: int, attempt_count: int
    ) -> None:
        """_send_dispatch_failure always produces a SheetAttemptResult with success=False."""
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.state import SheetExecutionState

        # Create a minimal adapter with a mock baton
        mock_baton = MagicMock()
        mock_baton.inbox = asyncio.Queue()

        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = mock_baton

        state = SheetExecutionState(
            sheet_num=sheet_num,
            instrument_name="test-inst",
            normal_attempts=attempt_count,
        )

        adapter._send_dispatch_failure(
            "j1", sheet_num, "test-inst",
            "test failure reason",
            state=state,
        )

        # An event must have been posted
        assert not mock_baton.inbox.empty()
        event = mock_baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.execution_success is False
        assert event.job_id == "j1"
        assert event.sheet_num == sheet_num
        assert event.error_classification == "E505"
        assert event.attempt == attempt_count + 1


# =============================================================================
# Invariant 65: Clear rate limit idempotency
# =============================================================================


class TestClearRateLimitIdempotency:
    """Clearing an already-cleared instrument is a no-op."""

    @given(n_clears=st.integers(min_value=1, max_value=5))
    @settings(max_examples=20)
    def test_double_clear_returns_zero(self, n_clears: int) -> None:
        """After clearing, subsequent clears return 0."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.state import SheetExecutionState

        baton = BatonCore()
        inst = baton.register_instrument("inst")
        inst.rate_limited = True
        inst.rate_limit_expires_at = 99999.0

        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="inst",
        )
        baton.register_job("j1", {1: sheet}, {})

        # First clear should succeed
        first = baton.clear_instrument_rate_limit("inst")
        assert first == 1

        # Subsequent clears should be no-ops
        for i in range(n_clears):
            result = baton.clear_instrument_rate_limit("inst")
            assert result == 0, f"Clear #{i + 2} returned {result}, expected 0"

    def test_clear_all_idempotent(self) -> None:
        """Clearing all when none are limited returns 0."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore()
        baton.register_instrument("a")
        baton.register_instrument("b")

        cleared = baton.clear_instrument_rate_limit(None)
        assert cleared == 0
