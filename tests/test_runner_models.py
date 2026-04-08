"""Tests for marianne.execution.runner.models and related error models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from marianne.core.checkpoint import JobStatus
from marianne.core.errors.codes import ErrorCategory, ErrorCode
from marianne.core.errors.models import ClassificationResult, ClassifiedError, ErrorChain
from marianne.execution.runner.models import (
    FatalError,
    GracefulShutdownError,
    GroundingDecisionContext,
    RunSummary,
    SheetExecutionMode,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeGroundingResult:
    """Lightweight stand-in for GroundingResult (avoids importing the full
    grounding module). Only the fields read by from_results() are included."""

    passed: bool
    hook_name: str
    message: str = ""
    confidence: float = 1.0
    recovery_guidance: str | None = None
    should_escalate: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def _make_error(
    category: ErrorCategory = ErrorCategory.TRANSIENT,
    message: str = "test error",
    code: ErrorCode = ErrorCode.UNKNOWN,
    retriable: bool = True,
) -> ClassifiedError:
    return ClassifiedError(
        category=category,
        message=message,
        error_code=code,
        retriable=retriable,
    )


# ===================================================================
# RunSummary
# ===================================================================


class TestRunSummarySuccessRate:

    def test_zero_total_sheets_returns_zero(self) -> None:
        summary = RunSummary(job_id="j1", job_name="test", total_sheets=0)
        assert summary.success_rate == 0.0

    def test_all_completed(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=5,
            completed_sheets=5,
        )
        assert summary.success_rate == 100.0

    def test_partial_completed(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=4,
            completed_sheets=3,
        )
        assert summary.success_rate == 75.0

    def test_none_completed(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=10,
            completed_sheets=0,
        )
        assert summary.success_rate == 0.0

    def test_skipped_sheets_excluded_from_denominator(self) -> None:
        """Skipped sheets don't drag down success rate (GH#: success_rate bug)."""
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=19,
            completed_sheets=15,
            skipped_sheets=4,
        )
        # 15 completed / (19 - 4 skipped) = 15/15 = 100%
        assert summary.success_rate == 100.0

    def test_all_sheets_skipped_returns_zero(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=5,
            skipped_sheets=5,
        )
        assert summary.success_rate == 0.0


class TestRunSummaryValidationPassRate:

    def test_no_validations_returns_100(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=1,
            validation_pass_count=0,
            validation_fail_count=0,
        )
        assert summary.validation_pass_rate == 100.0

    def test_all_pass(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=1,
            validation_pass_count=10,
            validation_fail_count=0,
        )
        assert summary.validation_pass_rate == 100.0

    def test_mixed_results(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=1,
            validation_pass_count=3,
            validation_fail_count=1,
        )
        assert summary.validation_pass_rate == 75.0

    def test_all_fail(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=1,
            validation_pass_count=0,
            validation_fail_count=5,
        )
        assert summary.validation_pass_rate == 0.0


class TestRunSummarySuccessWithoutRetryRate:

    def test_zero_completed_returns_zero(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=5,
            completed_sheets=0,
            successes_without_retry=0,
        )
        assert summary.success_without_retry_rate == 0.0

    def test_all_first_try(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=5,
            completed_sheets=5,
            successes_without_retry=5,
        )
        assert summary.success_without_retry_rate == 100.0

    def test_some_needed_retry(self) -> None:
        summary = RunSummary(
            job_id="j1",
            job_name="test",
            total_sheets=10,
            completed_sheets=8,
            successes_without_retry=6,
        )
        assert summary.success_without_retry_rate == 75.0


class TestRunSummaryToDict:

    def test_returns_correct_structure(self) -> None:
        summary = RunSummary(
            job_id="job-42",
            job_name="my-job",
            total_sheets=10,
            completed_sheets=8,
            failed_sheets=1,
            skipped_sheets=1,
            total_duration_seconds=125.456,
            total_retries=3,
            total_completion_attempts=2,
            rate_limit_waits=1,
            validation_pass_count=7,
            validation_fail_count=1,
            successes_without_retry=6,
            final_status=JobStatus.COMPLETED,
        )
        d = summary.to_dict()

        assert d["job_id"] == "job-42"
        assert d["job_name"] == "my-job"
        assert d["status"] == "completed"
        assert d["duration_seconds"] == 125.46
        assert isinstance(d["duration_formatted"], str)

        sheets = d["sheets"]
        assert sheets["total"] == 10
        assert sheets["completed"] == 8
        assert sheets["failed"] == 1
        assert sheets["skipped"] == 1
        assert sheets["success_rate"] == 88.9  # 8 / (10 - 1 skipped) = 88.9%

        validation = d["validation"]
        assert validation["passed"] == 7
        assert validation["failed"] == 1
        assert validation["pass_rate"] == 87.5

        execution = d["execution"]
        assert execution["total_retries"] == 3
        assert execution["completion_attempts"] == 2
        assert execution["rate_limit_waits"] == 1
        assert execution["successes_without_retry"] == 6
        assert execution["success_without_retry_rate"] == 75.0

    def test_default_values_produce_valid_dict(self) -> None:
        summary = RunSummary(job_id="j", job_name="n", total_sheets=0)
        d = summary.to_dict()
        assert d["status"] == "pending"
        assert d["sheets"]["success_rate"] == 0.0
        assert d["validation"]["pass_rate"] == 100.0
        assert d["execution"]["success_without_retry_rate"] == 0.0


class TestRunSummaryFormatDuration:

    def test_seconds_only(self) -> None:
        assert RunSummary._format_duration(45.3) == "45.3s"

    def test_zero_seconds(self) -> None:
        assert RunSummary._format_duration(0.0) == "0.0s"

    def test_just_under_a_minute(self) -> None:
        assert RunSummary._format_duration(59.9) == "59.9s"

    def test_exactly_one_minute(self) -> None:
        assert RunSummary._format_duration(60.0) == "1m 0s"

    def test_minutes_and_seconds(self) -> None:
        assert RunSummary._format_duration(125.0) == "2m 5s"

    def test_just_under_an_hour(self) -> None:
        assert RunSummary._format_duration(3599.0) == "59m 59s"

    def test_exactly_one_hour(self) -> None:
        assert RunSummary._format_duration(3600.0) == "1h 0m"

    def test_hours_and_minutes(self) -> None:
        assert RunSummary._format_duration(7380.0) == "2h 3m"

    def test_large_value(self) -> None:
        # 10 hours 30 minutes
        assert RunSummary._format_duration(37800.0) == "10h 30m"


# ===================================================================
# GroundingDecisionContext
# ===================================================================


class TestGroundingDecisionContextFromResults:

    def test_empty_list_passed_true_zero_hooks(self) -> None:
        ctx = GroundingDecisionContext.from_results([])
        assert ctx.passed is True
        assert ctx.hooks_executed == 0
        assert "No grounding hooks" in ctx.message

    def test_all_pass(self) -> None:
        results = [
            _FakeGroundingResult(passed=True, hook_name="h1", message="ok1"),
            _FakeGroundingResult(passed=True, hook_name="h2", message="ok2"),
        ]
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert ctx.passed is True
        assert ctx.hooks_executed == 2
        assert "All 2" in ctx.message
        assert ctx.recovery_guidance is None

    def test_some_fail(self) -> None:
        results = [
            _FakeGroundingResult(passed=True, hook_name="h1", message="ok"),
            _FakeGroundingResult(passed=False, hook_name="h2", message="bad output"),
            _FakeGroundingResult(passed=False, hook_name="h3", message="missing file"),
        ]
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert ctx.passed is False
        assert ctx.hooks_executed == 3
        assert "2/3" in ctx.message
        assert "h2" in ctx.message
        assert "h3" in ctx.message
        assert "bad output" in ctx.message
        assert "missing file" in ctx.message

    def test_recovery_guidance_collected(self) -> None:
        results = [
            _FakeGroundingResult(
                passed=False,
                hook_name="h1",
                message="fail",
                recovery_guidance="Try A",
            ),
            _FakeGroundingResult(
                passed=False,
                hook_name="h2",
                message="fail",
                recovery_guidance="Try B",
            ),
        ]
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert ctx.recovery_guidance is not None
        assert "Try A" in ctx.recovery_guidance
        assert "Try B" in ctx.recovery_guidance
        # The parts are joined by "; "
        assert "; " in ctx.recovery_guidance

    def test_recovery_guidance_none_when_no_guidance(self) -> None:
        results = [
            _FakeGroundingResult(passed=False, hook_name="h1", message="fail"),
        ]
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert ctx.recovery_guidance is None

    def test_confidence_averaging(self) -> None:
        results = [
            _FakeGroundingResult(passed=True, hook_name="h1", confidence=0.8),
            _FakeGroundingResult(passed=True, hook_name="h2", confidence=0.6),
        ]
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert ctx.confidence == pytest.approx(0.7)

    def test_confidence_single_result(self) -> None:
        results = [
            _FakeGroundingResult(passed=True, hook_name="h1", confidence=0.9),
        ]
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert ctx.confidence == pytest.approx(0.9)

    def test_escalation_detection_when_present(self) -> None:
        results = [
            _FakeGroundingResult(passed=False, hook_name="h1", should_escalate=True),
            _FakeGroundingResult(passed=True, hook_name="h2", should_escalate=False),
        ]
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert ctx.should_escalate is True

    def test_no_escalation_when_absent(self) -> None:
        results = [
            _FakeGroundingResult(passed=True, hook_name="h1", should_escalate=False),
            _FakeGroundingResult(passed=True, hook_name="h2", should_escalate=False),
        ]
        ctx = GroundingDecisionContext.from_results(results)  # type: ignore[arg-type]
        assert ctx.should_escalate is False


class TestGroundingDecisionContextDisabled:

    def test_disabled_context(self) -> None:
        ctx = GroundingDecisionContext.disabled()
        assert ctx.passed is True
        assert ctx.hooks_executed == 0
        assert "not enabled" in ctx.message.lower()
        assert ctx.confidence == 1.0
        assert ctx.should_escalate is False
        assert ctx.recovery_guidance is None


# ===================================================================
# SheetExecutionMode
# ===================================================================


class TestSheetExecutionMode:

    def test_enum_values(self) -> None:
        assert SheetExecutionMode.NORMAL.value == "normal"
        assert SheetExecutionMode.COMPLETION.value == "completion"
        assert SheetExecutionMode.RETRY.value == "retry"
        assert SheetExecutionMode.ESCALATE.value == "escalate"

    def test_is_str_subclass(self) -> None:
        """SheetExecutionMode(str, Enum) should be usable as a string."""
        assert isinstance(SheetExecutionMode.NORMAL, str)

    def test_string_comparison(self) -> None:
        assert SheetExecutionMode.NORMAL == "normal"
        assert SheetExecutionMode.RETRY == "retry"

    def test_all_members(self) -> None:
        members = set(SheetExecutionMode)
        assert len(members) == 4


# ===================================================================
# FatalError / GracefulShutdownError
# ===================================================================


class TestExceptionClasses:

    def test_fatal_error_is_exception(self) -> None:
        assert issubclass(FatalError, Exception)

    def test_graceful_shutdown_error_is_exception(self) -> None:
        assert issubclass(GracefulShutdownError, Exception)

    def test_fatal_error_carries_message(self) -> None:
        err = FatalError("something broke")
        assert str(err) == "something broke"

    def test_graceful_shutdown_error_carries_message(self) -> None:
        err = GracefulShutdownError("ctrl-c pressed")
        assert str(err) == "ctrl-c pressed"

    def test_fatal_error_catchable(self) -> None:
        with pytest.raises(FatalError):
            raise FatalError("boom")

    def test_graceful_shutdown_error_catchable(self) -> None:
        with pytest.raises(GracefulShutdownError):
            raise GracefulShutdownError("shutdown")

    def test_fatal_error_not_subclass_of_graceful(self) -> None:
        assert not issubclass(FatalError, GracefulShutdownError)
        assert not issubclass(GracefulShutdownError, FatalError)


# ===================================================================
# ErrorChain (from core.errors.models)
# ===================================================================


class TestErrorChain:

    def test_post_init_clamps_confidence_above_one(self) -> None:
        err = _make_error()
        chain = ErrorChain(errors=[err], root_cause=err, confidence=1.5)
        assert chain.confidence == 1.0

    def test_post_init_clamps_confidence_below_zero(self) -> None:
        err = _make_error()
        chain = ErrorChain(errors=[err], root_cause=err, confidence=-0.3)
        assert chain.confidence == 0.0

    def test_post_init_preserves_valid_confidence(self) -> None:
        err = _make_error()
        chain = ErrorChain(errors=[err], root_cause=err, confidence=0.75)
        assert chain.confidence == 0.75

    def test_default_confidence_is_one(self) -> None:
        err = _make_error()
        chain = ErrorChain(errors=[err], root_cause=err)
        assert chain.confidence == 1.0

    def test_symptoms_default_empty(self) -> None:
        err = _make_error()
        chain = ErrorChain(errors=[err], root_cause=err)
        assert chain.symptoms == []


# ===================================================================
# ClassificationResult (from core.errors.models)
# ===================================================================


class TestClassificationResult:

    def test_all_errors_includes_primary_and_secondary(self) -> None:
        primary = _make_error(message="root")
        sec1 = _make_error(message="symptom1")
        sec2 = _make_error(message="symptom2")
        result = ClassificationResult(primary=primary, secondary=[sec1, sec2])

        all_errors = result.all_errors
        assert len(all_errors) == 3
        assert all_errors[0] is primary
        assert all_errors[1] is sec1
        assert all_errors[2] is sec2

    def test_all_errors_primary_only(self) -> None:
        primary = _make_error()
        result = ClassificationResult(primary=primary)
        assert result.all_errors == [primary]

    def test_backward_compat_category(self) -> None:
        primary = _make_error(category=ErrorCategory.RATE_LIMIT)
        result = ClassificationResult(primary=primary)
        assert result.category == ErrorCategory.RATE_LIMIT

    def test_backward_compat_message(self) -> None:
        primary = _make_error(message="rate limit hit")
        result = ClassificationResult(primary=primary)
        assert result.message == "rate limit hit"

    def test_backward_compat_error_code(self) -> None:
        primary = _make_error(code=ErrorCode.RATE_LIMIT_API)
        result = ClassificationResult(primary=primary)
        assert result.error_code == ErrorCode.RATE_LIMIT_API

    def test_backward_compat_retriable(self) -> None:
        primary = _make_error(retriable=False)
        result = ClassificationResult(primary=primary)
        assert result.retriable is False

    def test_backward_compat_should_retry_true(self) -> None:
        primary = _make_error(
            category=ErrorCategory.TRANSIENT,
            retriable=True,
        )
        result = ClassificationResult(primary=primary)
        assert result.should_retry is True

    def test_backward_compat_should_retry_false_for_auth(self) -> None:
        primary = _make_error(
            category=ErrorCategory.AUTH,
            retriable=True,
        )
        result = ClassificationResult(primary=primary)
        # should_retry checks both retriable AND category not in (AUTH, FATAL)
        assert result.should_retry is False

    def test_backward_compat_should_retry_false_for_fatal(self) -> None:
        primary = _make_error(
            category=ErrorCategory.FATAL,
            retriable=True,
        )
        result = ClassificationResult(primary=primary)
        assert result.should_retry is False

    def test_error_codes_list(self) -> None:
        primary = _make_error(code=ErrorCode.RATE_LIMIT_API)
        sec = _make_error(code=ErrorCode.NETWORK_TIMEOUT)
        result = ClassificationResult(primary=primary, secondary=[sec])
        assert result.error_codes == ["E101", "E904"]

    def test_to_error_chain(self) -> None:
        primary = _make_error(message="root cause")
        sec = _make_error(message="symptom")
        result = ClassificationResult(
            primary=primary,
            secondary=[sec],
            confidence=0.85,
        )
        chain = result.to_error_chain()

        assert isinstance(chain, ErrorChain)
        assert chain.root_cause is primary
        assert chain.symptoms == [sec]
        assert chain.confidence == 0.85
        assert len(chain.errors) == 2
        assert chain.errors[0] is primary
        assert chain.errors[1] is sec

    def test_to_error_chain_no_secondary(self) -> None:
        primary = _make_error()
        result = ClassificationResult(primary=primary)
        chain = result.to_error_chain()

        assert chain.root_cause is primary
        assert chain.symptoms == []
        assert chain.errors == [primary]

    def test_confidence_clamped_above_one(self) -> None:
        primary = _make_error()
        result = ClassificationResult(primary=primary, confidence=2.0)
        assert result.confidence == 1.0

    def test_confidence_clamped_below_zero(self) -> None:
        primary = _make_error()
        result = ClassificationResult(primary=primary, confidence=-0.5)
        assert result.confidence == 0.0

    def test_classification_method_default(self) -> None:
        primary = _make_error()
        result = ClassificationResult(primary=primary)
        assert result.classification_method == "structured"
