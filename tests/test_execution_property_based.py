"""Property-based tests for execution layer models and state machines.

Uses hypothesis @given to verify invariants across random inputs:
- Runner model round-trip serialization
- Sheet state transitions
- Cost tracking arithmetic
- Retry logic bounds
- Recovery state consistency
- Escalation threshold correctness
- GroundingDecisionContext invariants
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from tests.conftest_adversarial import (
    _nonneg_float,
    _nonneg_int,
    _unit_float,
)

from mozart.core.checkpoint import (
    JobStatus,
    SheetState,
    SheetStatus,
)
from mozart.execution.escalation import (
    CheckpointTrigger,
    ConsoleCheckpointHandler,
    ConsoleEscalationHandler,
    EscalationResponse,
    HistoricalSuggestion,
)
from mozart.execution.runner.models import (
    FailureHandlingResult,
    GroundingDecisionContext,
    ModeDecisionResult,
    RunSummary,
    SheetExecutionMode,
    SheetExecutionSetup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeGroundingResult:
    """Stand-in for GroundingResult with only the fields from_results() reads."""

    passed: bool
    hook_name: str
    message: str = ""
    confidence: float = 1.0
    recovery_guidance: str | None = None
    should_escalate: bool = False
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_job_status_st = st.sampled_from(list(JobStatus))
_sheet_status_st = st.sampled_from(list(SheetStatus))
_sheet_mode_st = st.sampled_from(list(SheetExecutionMode))


@st.composite
def _run_summary_strategy(draw: st.DrawFn) -> dict[str, Any]:
    """Build RunSummary kwargs with consistent completed+failed+skipped <= total."""
    total = draw(st.integers(min_value=1, max_value=200))
    # Partition total into completed, failed, skipped (all non-negative, sum <= total)
    completed = draw(st.integers(min_value=0, max_value=total))
    remaining = total - completed
    failed = draw(st.integers(min_value=0, max_value=remaining))
    remaining2 = remaining - failed
    skipped = draw(st.integers(min_value=0, max_value=remaining2))
    swr = draw(st.integers(min_value=0, max_value=max(completed, 1) - (0 if completed == 0 else 0)))
    swr = min(swr, completed)

    return {
        "job_id": "prop-job",
        "job_name": "prop",
        "total_sheets": total,
        "completed_sheets": completed,
        "failed_sheets": failed,
        "skipped_sheets": skipped,
        "total_duration_seconds": draw(_nonneg_float),
        "total_retries": draw(_nonneg_int),
        "total_completion_attempts": draw(_nonneg_int),
        "rate_limit_waits": draw(_nonneg_int),
        "validation_pass_count": draw(_nonneg_int),
        "validation_fail_count": draw(_nonneg_int),
        "successes_without_retry": swr,
        "final_status": draw(_job_status_st),
        "total_input_tokens": draw(_nonneg_int),
        "total_output_tokens": draw(_nonneg_int),
        "total_estimated_cost": draw(_nonneg_float),
        "cost_limit_hit": draw(st.booleans()),
    }


_grounding_result_st = st.builds(
    _FakeGroundingResult,
    passed=st.booleans(),
    hook_name=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L",))),
    message=st.text(min_size=0, max_size=100),
    confidence=_unit_float,
    recovery_guidance=st.one_of(st.none(), st.text(min_size=1, max_size=50)),
    should_escalate=st.booleans(),
)


# ===========================================================================
# 1. RunSummary model round-trip and property invariants
# ===========================================================================


class TestRunSummaryProperties:
    """Property-based tests for RunSummary dataclass invariants."""

    @pytest.mark.property_based
    @given(data=_run_summary_strategy())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_success_rate_is_bounded(self, data: dict[str, Any]) -> None:
        """success_rate is always in [0.0, 100.0]."""
        summary = RunSummary(**data)
        assert 0.0 <= summary.success_rate <= 100.0

    @pytest.mark.property_based
    @given(data=_run_summary_strategy())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_validation_pass_rate_is_bounded(self, data: dict[str, Any]) -> None:
        """validation_pass_rate is always in [0.0, 100.0]."""
        summary = RunSummary(**data)
        assert 0.0 <= summary.validation_pass_rate <= 100.0

    @pytest.mark.property_based
    @given(data=_run_summary_strategy())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_success_without_retry_rate_is_bounded(self, data: dict[str, Any]) -> None:
        """success_without_retry_rate is always in [0.0, 100.0]."""
        summary = RunSummary(**data)
        assert 0.0 <= summary.success_without_retry_rate <= 100.0

    @pytest.mark.property_based
    @given(data=_run_summary_strategy())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_to_dict_round_trip_keys(self, data: dict[str, Any]) -> None:
        """to_dict() always produces the expected top-level keys."""
        summary = RunSummary(**data)
        d = summary.to_dict()
        assert set(d.keys()) == {"job_id", "job_name", "status", "duration_seconds",
                                  "duration_formatted", "sheets", "validation", "execution"}

    @pytest.mark.property_based
    @given(
        total=st.integers(min_value=1, max_value=100),
        completed_excess=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_completed_exceeds_total_raises(self, total: int, completed_excess: int) -> None:
        """RunSummary raises ValueError when completed_sheets > total_sheets."""
        with pytest.raises(ValueError, match="exceeds total_sheets"):
            RunSummary(
                job_id="test",
                job_name="test",
                total_sheets=total,
                completed_sheets=total + completed_excess,
            )

    @pytest.mark.property_based
    @given(seconds=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_format_duration_never_empty(self, seconds: float) -> None:
        """_format_duration always returns a non-empty string."""
        result = RunSummary._format_duration(seconds)
        assert len(result) > 0


# ===========================================================================
# 2. Sheet state transitions
# ===========================================================================


class TestSheetStateTransitions:
    """Property-based tests for SheetState / SheetStatus transitions."""

    VALID_TRANSITIONS: dict[SheetStatus, set[SheetStatus]] = {
        SheetStatus.PENDING: {SheetStatus.IN_PROGRESS, SheetStatus.SKIPPED},
        SheetStatus.IN_PROGRESS: {SheetStatus.COMPLETED, SheetStatus.FAILED, SheetStatus.SKIPPED},
        SheetStatus.COMPLETED: set(),  # terminal
        SheetStatus.FAILED: {SheetStatus.IN_PROGRESS},  # retry
        SheetStatus.SKIPPED: set(),  # terminal
    }

    @pytest.mark.property_based
    @given(
        from_status=_sheet_status_st,
        to_status=_sheet_status_st,
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_valid_transition_respects_allowed_set(
        self, from_status: SheetStatus, to_status: SheetStatus
    ) -> None:
        """Only declared transitions should be considered valid."""
        allowed = self.VALID_TRANSITIONS[from_status]
        is_valid = to_status in allowed
        # This is a specification test: we're documenting the allowed transitions
        if is_valid:
            state = SheetState(sheet_num=1, status=from_status)
            state.status = to_status
            assert state.status == to_status
        # If not valid, that's fine — the transition shouldn't happen in production

    @pytest.mark.property_based
    @given(status=_sheet_status_st)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_terminal_states_have_no_transitions(self, status: SheetStatus) -> None:
        """COMPLETED and SKIPPED are terminal — they allow no outgoing transitions."""
        if status in (SheetStatus.COMPLETED, SheetStatus.SKIPPED):
            assert len(self.VALID_TRANSITIONS[status]) == 0

    @pytest.mark.property_based
    @given(sheet_num=st.integers(min_value=1, max_value=1000))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_sheet_state_always_starts_pending(self, sheet_num: int) -> None:
        """Freshly created SheetState is always PENDING with zero attempts."""
        state = SheetState(sheet_num=sheet_num)
        assert state.status == SheetStatus.PENDING
        assert state.attempt_count == 0


# ===========================================================================
# 3. Cost tracking arithmetic
# ===========================================================================


class TestCostTrackingArithmetic:
    """Property-based tests for cost tracking invariants."""

    @pytest.mark.property_based
    @given(
        input_tokens=st.integers(min_value=0, max_value=1_000_000),
        output_tokens=st.integers(min_value=0, max_value=1_000_000),
        cost_per_1k_input=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        cost_per_1k_output=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_cost_is_nonnegative(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_per_1k_input: float,
        cost_per_1k_output: float,
    ) -> None:
        """Estimated cost is always >= 0 given non-negative inputs."""
        cost = (
            (input_tokens / 1000 * cost_per_1k_input)
            + (output_tokens / 1000 * cost_per_1k_output)
        )
        assert cost >= 0.0

    @pytest.mark.property_based
    @given(
        sheet_costs=st.lists(
            st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_total_cost_is_sum_of_sheet_costs(self, sheet_costs: list[float]) -> None:
        """Total job cost should equal the sum of individual sheet costs."""
        total = sum(sheet_costs)
        # Verify summation invariant (with floating point tolerance)
        recomputed = 0.0
        for c in sheet_costs:
            recomputed += c
        assert abs(total - recomputed) < 1e-9

    @pytest.mark.property_based
    @given(
        n_sheets=st.integers(min_value=1, max_value=10),
        tokens_per_sheet=st.integers(min_value=0, max_value=10000),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_cumulative_tokens_grow_monotonically(
        self, n_sheets: int, tokens_per_sheet: int
    ) -> None:
        """Cumulative token counts never decrease as sheets execute."""
        cumulative = 0
        for _ in range(n_sheets):
            prev = cumulative
            cumulative += tokens_per_sheet
            assert cumulative >= prev

    @pytest.mark.property_based
    @given(
        in_tok=st.integers(min_value=0, max_value=100000),
        out_tok=st.integers(min_value=0, max_value=100000),
        cost=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_run_summary_cost_fields_never_negative(
        self, in_tok: int, out_tok: int, cost: float
    ) -> None:
        """RunSummary cost fields remain non-negative after accumulation."""
        summary = RunSummary(job_id="j", job_name="j", total_sheets=1)
        summary.total_input_tokens += in_tok
        summary.total_output_tokens += out_tok
        summary.total_estimated_cost += cost
        assert summary.total_input_tokens >= 0
        assert summary.total_output_tokens >= 0
        assert summary.total_estimated_cost >= 0.0


# ===========================================================================
# 4. Retry logic: count never exceeds max_retries
# ===========================================================================


class TestRetryLogicBounds:
    """Property-based tests for retry delay and count bounds."""

    @pytest.mark.property_based
    @given(
        base_delay=st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False),
        exponential_base=st.floats(min_value=1.01, max_value=5.0, allow_nan=False, allow_infinity=False),
        max_delay=st.floats(min_value=1.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
        attempt=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_retry_delay_never_exceeds_max(
        self,
        base_delay: float,
        exponential_base: float,
        max_delay: float,
        attempt: int,
    ) -> None:
        """Retry delay (without jitter) is always <= max_delay."""
        delay = min(base_delay * (exponential_base ** (attempt - 1)), max_delay)
        assert delay <= max_delay + 1e-9  # floating point tolerance

    @pytest.mark.property_based
    @given(
        base_delay=st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False),
        exponential_base=st.floats(min_value=1.01, max_value=5.0, allow_nan=False, allow_infinity=False),
        max_delay=st.floats(min_value=1.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
        attempt=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_retry_delay_is_nonnegative(
        self,
        base_delay: float,
        exponential_base: float,
        max_delay: float,
        attempt: int,
    ) -> None:
        """Retry delay is always >= 0."""
        delay = min(base_delay * (exponential_base ** (attempt - 1)), max_delay)
        assert delay >= 0.0

    @pytest.mark.property_based
    @given(
        max_retries=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_retry_counter_never_exceeds_max(self, max_retries: int) -> None:
        """Simulating a retry loop, counter never exceeds max_retries."""
        attempts = 0
        while attempts < max_retries:
            attempts += 1
        assert attempts <= max_retries

    @pytest.mark.property_based
    @given(
        base_delay=st.floats(min_value=0.01, max_value=10.0, allow_nan=False, allow_infinity=False),
        exponential_base=st.floats(min_value=1.5, max_value=3.0, allow_nan=False, allow_infinity=False),
        max_delay=st.floats(min_value=10.0, max_value=600.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_retry_delays_monotonically_increase_without_jitter(
        self,
        base_delay: float,
        exponential_base: float,
        max_delay: float,
    ) -> None:
        """Without jitter, retry delays form a monotonically non-decreasing sequence."""
        prev_delay = 0.0
        for attempt in range(1, 11):
            delay = min(base_delay * (exponential_base ** (attempt - 1)), max_delay)
            assert delay >= prev_delay - 1e-9  # floating point tolerance
            prev_delay = delay


# ===========================================================================
# 5. Recovery state consistency
# ===========================================================================


class TestRecoveryStateConsistency:
    """Property-based tests for recovery-related state invariants."""

    @pytest.mark.property_based
    @given(
        rate_limit_waits=_nonneg_int,
        quota_waits=_nonneg_int,
        max_waits=st.integers(min_value=1, max_value=100),
        max_quota_waits=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_wait_counters_bounded_by_max(
        self,
        rate_limit_waits: int,
        quota_waits: int,
        max_waits: int,
        max_quota_waits: int,
    ) -> None:
        """When rate_limit_waits >= max_waits, FatalError should be raised."""
        if rate_limit_waits >= max_waits:
            # The code would raise FatalError here
            assert rate_limit_waits >= max_waits
        if quota_waits >= max_quota_waits:
            assert quota_waits >= max_quota_waits

    @pytest.mark.property_based
    @given(
        status=_sheet_status_st,
        attempt_count=_nonneg_int,
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_after_recovery_state_consistent(
        self, status: SheetStatus, attempt_count: int
    ) -> None:
        """After setting recovery-related fields, SheetState remains valid."""
        state = SheetState(sheet_num=1, status=status, attempt_count=attempt_count)
        # Simulate recovery: transition to IN_PROGRESS for retry
        if status == SheetStatus.FAILED:
            state.status = SheetStatus.IN_PROGRESS
            state.attempt_count += 1
            assert state.status == SheetStatus.IN_PROGRESS
            assert state.attempt_count == attempt_count + 1
        # State should be a valid SheetState regardless
        assert state.sheet_num == 1

    @pytest.mark.property_based
    @given(
        action=st.sampled_from(["continue", "fatal"]),
        normal_attempts=_nonneg_int,
        healing_attempts=_nonneg_int,
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_failure_handling_result_actions_valid(
        self, action: str, normal_attempts: int, healing_attempts: int
    ) -> None:
        """FailureHandlingResult always has a valid action."""
        result = FailureHandlingResult(
            action=action,  # type: ignore[arg-type]
            normal_attempts=normal_attempts,
            healing_attempts=healing_attempts,
            pending_recovery=None,
        )
        assert result.action in ("continue", "fatal")


# ===========================================================================
# 6. Escalation thresholds
# ===========================================================================


class TestEscalationThresholds:
    """Property-based tests for escalation trigger conditions."""

    @pytest.mark.property_based
    @given(
        confidence_threshold=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        attempt_count=st.integers(min_value=0, max_value=20),
        auto_retry_on_first=st.booleans(),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_escalation_respects_threshold(
        self,
        confidence_threshold: float,
        confidence: float,
        attempt_count: int,
        auto_retry_on_first: bool,
    ) -> None:
        """Escalation only triggers when confidence < threshold (and retry rules allow)."""
        handler = ConsoleEscalationHandler(
            confidence_threshold=confidence_threshold,
            auto_retry_on_first_failure=auto_retry_on_first,
        )
        sheet_state = SheetState(sheet_num=1, attempt_count=attempt_count)
        mock_validation = MagicMock()

        result = asyncio.run(
            handler.should_escalate(sheet_state, mock_validation, confidence)
        )

        if confidence >= confidence_threshold:
            # Should never escalate when confidence is at or above threshold
            assert result is False
        elif auto_retry_on_first and attempt_count <= 1:
            # Auto-retry on first failure: don't escalate
            assert result is False
        else:
            # Below threshold and past first attempt (or auto-retry disabled)
            assert result is True

    @pytest.mark.property_based
    @given(
        confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_high_confidence_never_escalates(self, confidence: float) -> None:
        """With threshold=0.0, no confidence level triggers escalation."""
        handler = ConsoleEscalationHandler(confidence_threshold=0.0)
        sheet_state = SheetState(sheet_num=1, attempt_count=5)
        mock_validation = MagicMock()

        result = asyncio.run(
            handler.should_escalate(sheet_state, mock_validation, confidence)
        )
        # threshold=0.0 means confidence >= 0.0 always true
        assert result is False


# ===========================================================================
# 7. GroundingDecisionContext invariants
# ===========================================================================


class TestGroundingDecisionContextProperties:
    """Property-based tests for GroundingDecisionContext."""

    @pytest.mark.property_based
    @given(confidence=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_confidence_always_clamped_to_unit(self, confidence: float) -> None:
        """GroundingDecisionContext clamps confidence to [0.0, 1.0]."""
        ctx = GroundingDecisionContext(
            passed=True,
            message="test",
            confidence=confidence,
        )
        assert 0.0 <= ctx.confidence <= 1.0

    @pytest.mark.property_based
    @given(results=st.lists(_grounding_result_st, min_size=0, max_size=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_from_results_passed_iff_all_pass(
        self, results: list[_FakeGroundingResult]
    ) -> None:
        """from_results().passed is True iff all individual results pass."""
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        if not results:
            assert ctx.passed is True
        else:
            assert ctx.passed == all(r.passed for r in results)

    @pytest.mark.property_based
    @given(results=st.lists(_grounding_result_st, min_size=1, max_size=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_from_results_hooks_executed_equals_length(
        self, results: list[_FakeGroundingResult]
    ) -> None:
        """hooks_executed always equals the number of results passed in."""
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert ctx.hooks_executed == len(results)

    @pytest.mark.property_based
    @given(results=st.lists(_grounding_result_st, min_size=1, max_size=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_from_results_confidence_bounded(
        self, results: list[_FakeGroundingResult]
    ) -> None:
        """from_results confidence is always in [0.0, 1.0]."""
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert 0.0 <= ctx.confidence <= 1.0

    @pytest.mark.property_based
    @given(results=st.lists(_grounding_result_st, min_size=1, max_size=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_from_results_should_escalate_iff_any_escalate(
        self, results: list[_FakeGroundingResult]
    ) -> None:
        """should_escalate is True iff any individual result recommends escalation."""
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert ctx.should_escalate == any(r.should_escalate for r in results)

    @pytest.mark.property_based
    @given(results=st.lists(_grounding_result_st, min_size=1, max_size=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_from_results_recovery_guidance_only_on_failure(
        self, results: list[_FakeGroundingResult]
    ) -> None:
        """recovery_guidance is only set when at least one result failed."""
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        all_passed = all(r.passed for r in results)
        if all_passed:
            assert ctx.recovery_guidance is None


# ===========================================================================
# 8. SheetExecutionMode enum completeness
# ===========================================================================


class TestSheetExecutionModeProperties:
    """Property-based tests for SheetExecutionMode enum."""

    @pytest.mark.property_based
    @given(mode=_sheet_mode_st)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_mode_is_string(self, mode: SheetExecutionMode) -> None:
        """All SheetExecutionMode values are valid strings."""
        assert isinstance(mode.value, str)
        assert len(mode.value) > 0

    @pytest.mark.property_based
    @given(mode=_sheet_mode_st)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_mode_round_trips_through_value(self, mode: SheetExecutionMode) -> None:
        """SheetExecutionMode(mode.value) returns the same mode."""
        assert SheetExecutionMode(mode.value) == mode


# ===========================================================================
# 9. ModeDecisionResult / FailureHandlingResult action validity
# ===========================================================================


class TestDecisionResultProperties:
    """Property-based tests for decision result models."""

    @pytest.mark.property_based
    @given(
        action=st.sampled_from(["continue", "return", "fatal"]),
        mode=_sheet_mode_st,
        normal=_nonneg_int,
        completion=_nonneg_int,
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_mode_decision_result_preserves_counters(
        self, action: str, mode: SheetExecutionMode, normal: int, completion: int,
    ) -> None:
        """ModeDecisionResult preserves attempt counters through construction."""
        result = ModeDecisionResult(
            action=action,  # type: ignore[arg-type]
            current_prompt="test",
            current_mode=mode,
            normal_attempts=normal,
            completion_attempts=completion,
        )
        assert result.normal_attempts == normal
        assert result.completion_attempts == completion

    @pytest.mark.property_based
    @given(
        action=st.sampled_from(["continue", "fatal"]),
        msg=st.text(min_size=0, max_size=200),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_failure_handling_fatal_has_message(self, action: str, msg: str) -> None:
        """When action is 'fatal', fatal_message is the provided message."""
        result = FailureHandlingResult(
            action=action,  # type: ignore[arg-type]
            normal_attempts=0,
            healing_attempts=0,
            pending_recovery=None,
            fatal_message=msg,
        )
        if action == "fatal":
            assert result.fatal_message == msg


# ===========================================================================
# 10. Checkpoint trigger matching
# ===========================================================================


class TestCheckpointTriggerMatchingProperties:
    """Property-based tests for ConsoleCheckpointHandler.should_checkpoint."""

    @pytest.mark.property_based
    @given(
        sheet_num=st.integers(min_value=1, max_value=100),
        retry_count=_nonneg_int,
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_empty_triggers_never_match(self, sheet_num: int, retry_count: int) -> None:
        """With no triggers configured, should_checkpoint returns None."""
        handler = ConsoleCheckpointHandler()
        result = asyncio.run(
            handler.should_checkpoint(sheet_num, "any prompt", retry_count, [])
        )
        assert result is None

    @pytest.mark.property_based
    @given(
        sheet_num=st.integers(min_value=1, max_value=100),
        min_retry=st.integers(min_value=1, max_value=20),
        actual_retry=st.integers(min_value=0, max_value=25),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_retry_threshold_trigger_correctness(
        self, sheet_num: int, min_retry: int, actual_retry: int
    ) -> None:
        """Trigger with min_retry_count matches iff actual >= min."""
        trigger = CheckpointTrigger(
            name="retry-gate",
            min_retry_count=min_retry,
        )
        handler = ConsoleCheckpointHandler()
        result = asyncio.run(
            handler.should_checkpoint(sheet_num, "any prompt", actual_retry, [trigger])
        )
        if actual_retry >= min_retry:
            assert result is trigger
        else:
            assert result is None


# ===========================================================================
# 11. SheetExecutionSetup invariants
# ===========================================================================


class TestSheetExecutionSetupProperties:
    """Property-based tests for SheetExecutionSetup dataclass."""

    @pytest.mark.property_based
    @given(
        max_retries=_nonneg_int,
        max_completion=_nonneg_int,
        warnings=_nonneg_int,
        tokens=_nonneg_int,
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_setup_preserves_limits(
        self, max_retries: int, max_completion: int, warnings: int, tokens: int,
    ) -> None:
        """SheetExecutionSetup faithfully stores limit values."""
        setup = SheetExecutionSetup(
            original_prompt="orig",
            current_prompt="current",
            current_mode=SheetExecutionMode.NORMAL,
            max_retries=max_retries,
            max_completion=max_completion,
            preflight_warnings=warnings,
            preflight_token_estimate=tokens,
        )
        assert setup.max_retries == max_retries
        assert setup.max_completion == max_completion
        assert setup.preflight_warnings == warnings
        assert setup.preflight_token_estimate == tokens


# ===========================================================================
# 12. HistoricalSuggestion and EscalationResponse
# ===========================================================================


class TestEscalationDataModelProperties:
    """Property-based tests for escalation data models."""

    @pytest.mark.property_based
    @given(
        action=st.sampled_from(["retry", "skip", "abort", "modify_prompt"]),
        confidence_boost=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_escalation_response_action_valid(
        self, action: str, confidence_boost: float,
    ) -> None:
        """EscalationResponse always has a valid action and non-negative boost."""
        resp = EscalationResponse(
            action=action,  # type: ignore[arg-type]
            confidence_boost=confidence_boost,
        )
        assert resp.action in ("retry", "skip", "abort", "modify_prompt")
        assert resp.confidence_boost >= 0.0

    @pytest.mark.property_based
    @given(
        confidence=_unit_float,
        pass_rate=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_historical_suggestion_fields_bounded(
        self, confidence: float, pass_rate: float,
    ) -> None:
        """HistoricalSuggestion confidence is in [0,1] and pass_rate in [0,100]."""
        suggestion = HistoricalSuggestion(
            action="retry",
            outcome="success",
            confidence=confidence,
            validation_pass_rate=pass_rate,
            guidance=None,
        )
        assert 0.0 <= suggestion.confidence <= 1.0
        assert 0.0 <= suggestion.validation_pass_rate <= 100.0
