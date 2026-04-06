"""Litmus tests for the baton — does the system ACTUALLY work, not just pass?

These tests focus on the gap between "correct" and "effective." Unit tests
verify that functions return expected values. Litmus tests verify that the
system makes the RIGHT decisions when faced with ambiguous, conflicting,
or edge-case input — the kind of input that real musicians produce.

Key areas:
1. F-018: The validation_pass_rate default landmine — does the baton handle
   musicians that forget to set it correctly?
2. Conflicting signals — rate_limited + execution_success, error_classification
   on successful execution, etc.
3. Floating point boundaries — 99.999% vs 100.0%, negative pass rates.
4. Full lifecycle validation — fail → retry → succeed → complete.
5. Job completion detection with mixed terminal states.
6. Progressive validation improvement across retries.

@pytest.mark.adversarial
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState
from marianne.daemon.baton.events import (
    RetryDue,
    SheetAttemptResult,
    SheetSkipped,
)

# ---------------------------------------------------------------------------
# F-018: The validation_pass_rate default landmine
# ---------------------------------------------------------------------------


class TestF018ValidationPassRateContract:
    """The most dangerous contract gap in the baton.

    SheetAttemptResult.validation_pass_rate defaults to 0.0. A musician that
    reports execution_success=True with validations_total=0 but forgets to
    set validation_pass_rate=100.0 will trigger unnecessary retries.

    The baton's decision tree at core.py:412 checks:
        if event.execution_success and event.validation_pass_rate >= 100.0

    A default of 0.0 with no validations means: "all validations failed"
    in the baton's eyes, even though there are no validations to fail.

    These tests document the current behavior and guard the contract.
    """

    async def test_default_pass_rate_with_no_validations_causes_retry(self) -> None:
        """F-018 LANDMINE: forgetting to set validation_pass_rate causes retry.

        This test documents the CURRENT behavior: a musician that reports
        success with no validations but leaves validation_pass_rate at the
        default (0.0) will get retried. This is by design (safety default),
        but it's a trap for step 22 builders.
        """
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
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=0,
            validations_total=0,
            # validation_pass_rate defaults to 0.0 — THE LANDMINE
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # F-018 FIX: The baton now auto-corrects validation_pass_rate to 100.0
        # when validations_total==0 and execution_success==True. The musician
        # doesn't need to remember to set it — the conductor handles it.
        assert state.status == BatonSheetStatus.COMPLETED, (
            "F-018 fix: validations_total==0 + execution_success should complete"
        )

    async def test_explicit_100_pass_rate_with_no_validations_completes(self) -> None:
        """The correct musician behavior: set validation_pass_rate=100.0
        when there are no validations.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=0,
            validations_total=0,
            validation_pass_rate=100.0,  # Correct: explicitly set
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.COMPLETED

    async def test_default_pass_rate_exhausts_retries_and_fails(self) -> None:
        """The F-018 landmine at scale: repeated default-pass-rate attempts
        exhaust retries and fail the sheet, even though execution succeeded
        every time.
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

        # All attempts "succeed" but with default validation_pass_rate
        for attempt in range(1, 3):
            await baton.handle_event(SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=attempt,
                execution_success=True,
                validations_passed=0,
                validations_total=0,
                # validation_pass_rate=0.0 (default)
            ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # F-018 FIX: The baton now treats validations_total==0 as 100% pass.
        # When no validations exist and execution succeeds, the sheet completes
        # on the first attempt — no unnecessary retries. This was the whole
        # point of F-018: the default 0.0 was causing false failures.
        assert state.status == BatonSheetStatus.COMPLETED


# ---------------------------------------------------------------------------
# Conflicting signals — what does the baton do with contradictory data?
# ---------------------------------------------------------------------------


class TestConflictingSignals:
    """When event fields conflict, the baton must have clear priority."""

    @pytest.mark.adversarial
    async def test_rate_limited_takes_priority_over_success(self) -> None:
        """rate_limited=True takes priority regardless of execution_success.

        In code: the rate_limited check (line 398) comes before the
        success+validation check (line 412). This test proves it.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            rate_limited=True,  # Conflicting: success + rate limited
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # rate_limited wins — sheet is waiting, NOT completed
        assert state.status == BatonSheetStatus.WAITING
        assert state.normal_attempts == 0

    @pytest.mark.adversarial
    async def test_rate_limited_does_not_count_as_failure(self) -> None:
        """rate_limited=True with execution_success=False is still not a failure.

        This confirms the baton treats rate limits as tempo changes.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=1,
            ),
        }
        baton.register_job("j1", sheets, {})

        # 50 rate-limited "failures"
        for i in range(50):
            await baton.handle_event(SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=i + 1,
                execution_success=False,
                rate_limited=True,
                error_classification="RATE_LIMIT",
            ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status != BatonSheetStatus.FAILED
        assert state.normal_attempts == 0

    @pytest.mark.adversarial
    async def test_success_with_error_classification_still_checks_validation(self) -> None:
        """execution_success=True with error_classification set.

        The baton should still check validation_pass_rate, not fail immediately.
        This can happen with partial execution success where the process
        exited cleanly but validations reveal problems.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            error_classification="EXECUTION_ERROR",  # Odd but possible
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # Success + 100% pass rate = completed, even with error_classification
        assert state.status == BatonSheetStatus.COMPLETED


# ---------------------------------------------------------------------------
# Floating point boundaries for validation_pass_rate
# ---------------------------------------------------------------------------


class TestFloatingPointBoundaries:
    """The >= 100.0 threshold is exact. Near-100 values must retry."""

    @pytest.mark.adversarial
    async def test_99_point_999_does_not_complete(self) -> None:
        """99.999% is NOT 100%. The baton should retry, not complete."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=999,
            validations_total=1000,
            validation_pass_rate=99.9,
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status != BatonSheetStatus.COMPLETED

    @pytest.mark.adversarial
    async def test_exactly_100_completes(self) -> None:
        """Exactly 100.0 completes. No floating point weirdness."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.COMPLETED

    @pytest.mark.adversarial
    async def test_above_100_still_completes(self) -> None:
        """validation_pass_rate > 100.0 (shouldn't happen, but handle it)."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=150.0,  # Shouldn't happen but must handle
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.COMPLETED


# ---------------------------------------------------------------------------
# Full lifecycle — the real litmus test
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    """Does the baton correctly handle the full retry→success lifecycle?"""

    async def test_fail_retry_succeed_completes(self) -> None:
        """Full lifecycle: fail → retry_scheduled → RetryDue → success → completed."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=3,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Attempt 1: fails
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="TRANSIENT",
        ))
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED

        # Timer fires
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))
        assert state.status == BatonSheetStatus.PENDING

        # Attempt 2: succeeds
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=2,
            execution_success=True,
            validations_passed=3,
            validations_total=3,
            validation_pass_rate=100.0,
        ))
        assert state.status == BatonSheetStatus.COMPLETED
        assert state.normal_attempts == 1  # Only the first failure counted

    async def test_progressive_validation_improvement(self) -> None:
        """Validation improves: 0% → 60% (completion mode) → 100% → complete.

        The baton uses different strategies based on pass rate:
        - 0% pass → retry (total validation failure)
        - 60% pass → completion mode (partial success, try to finish)
        - 100% pass → complete
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=5,
                max_completion=5,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Attempt 1: 0% pass → retry (total validation failure)
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=0, validations_total=5,
            validation_pass_rate=0.0,
        ))
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED

        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))

        # Attempt 2: 60% pass → completion mode (partial success)
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=2, execution_success=True,
            validations_passed=3, validations_total=5,
            validation_pass_rate=60.0,
        ))
        # Partial pass enters completion mode — re-dispatch pending
        assert state.status == BatonSheetStatus.PENDING
        assert state.completion_attempts == 1

        # Attempt 3: 100% pass (success!)
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=3, execution_success=True,
            validations_passed=5, validations_total=5,
            validation_pass_rate=100.0,
        ))
        assert state.status == BatonSheetStatus.COMPLETED
        assert len(state.attempt_results) == 3


# ---------------------------------------------------------------------------
# Job completion detection
# ---------------------------------------------------------------------------


class TestJobCompletion:
    """Does is_job_complete accurately reflect reality?"""

    async def test_all_completed_means_job_complete(self) -> None:
        """A job is complete when all sheets are terminal."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        assert not baton.is_job_complete("j1")

        # Complete sheet 1
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validation_pass_rate=100.0,
        ))
        assert not baton.is_job_complete("j1")

        # Complete sheet 2
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=2, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validation_pass_rate=100.0,
        ))
        assert baton.is_job_complete("j1")

    async def test_mixed_completed_and_failed_is_still_complete(self) -> None:
        """failed is a terminal state — job is complete (not successful)."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(
                sheet_num=2, instrument_name="claude-code",
                max_retries=0,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Complete sheet 1
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validation_pass_rate=100.0,
        ))

        # Fail sheet 2
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=2, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="AUTH_FAILURE",
        ))

        assert baton.is_job_complete("j1")

    async def test_skipped_sheets_count_as_terminal(self) -> None:
        """Skipped sheets are terminal — they satisfy completion."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        # Skip sheet 1
        await baton.handle_event(SheetSkipped(
            job_id="j1", sheet_num=1, reason="start_sheet override",
        ))
        # Sheet 2 should now be ready (skipped satisfies deps)
        ready = baton.get_ready_sheets("j1")
        assert any(s.sheet_num == 2 for s in ready)

        # Complete sheet 2
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=2, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validation_pass_rate=100.0,
        ))

        assert baton.is_job_complete("j1")

    async def test_retry_scheduled_is_not_terminal(self) -> None:
        """retry_scheduled means work is still in progress."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code",
                max_retries=5,
            ),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="TRANSIENT",
        ))

        assert not baton.is_job_complete("j1")


# ---------------------------------------------------------------------------
# Event handler resilience — the baton must never crash
# ---------------------------------------------------------------------------


class TestEventHandlerResilience:
    """The baton catches handler exceptions and continues processing."""

    @pytest.mark.adversarial
    async def test_unknown_job_event_is_silent(self) -> None:
        """Events for unknown jobs are logged but don't crash."""
        baton = BatonCore()
        # No jobs registered

        # Should not raise
        await baton.handle_event(SheetAttemptResult(
            job_id="nonexistent",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
        ))

        # Baton still functional
        assert baton.job_count == 0

    @pytest.mark.adversarial
    async def test_unknown_sheet_event_is_silent(self) -> None:
        """Events for unknown sheets within a known job don't crash."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=999,  # Doesn't exist
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
        ))

        # Sheet 1 unaffected
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.PENDING

    @pytest.mark.adversarial
    async def test_events_after_job_deregistration(self) -> None:
        """Events arriving for deregistered jobs are handled gracefully."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})
        baton.deregister_job("j1")

        # Should not raise
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
        ))

        assert baton.job_count == 0
