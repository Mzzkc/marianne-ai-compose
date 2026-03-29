"""Tests for the baton's retry state machine — the conductor's decision tree.

The retry state machine is the most critical piece of the baton. It determines
what happens after every sheet attempt: complete, retry, completion mode,
healing, escalation, or failure. Getting this wrong means either:
- Wasting money on unnecessary retries
- Failing prematurely when retry would have worked
- Losing rate-limit-related progress by counting rate limits as failures

These tests attack the decision tree with adversarial inputs and boundary
conditions that the happy-path tests in test_baton_core.py don't cover.

Tests follow TDD: written before the improvements they validate.

@pytest.mark.adversarial
"""

from __future__ import annotations

import pytest

from mozart.daemon.baton.core import BatonCore, SheetExecutionState
from mozart.daemon.baton.events import (
    PauseJob,
    RetryDue,
    SheetAttemptResult,
)


# ---------------------------------------------------------------------------
# Retry exhaustion boundary tests
# ---------------------------------------------------------------------------


class TestRetryExhaustion:
    """Tests for the boundary between 'retry' and 'fail'."""

    async def test_exact_max_retries_fails(self) -> None:
        """Sheet fails exactly when normal_attempts reaches max_retries."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=3,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Send exactly max_retries failed attempts
        for attempt in range(1, 4):  # 1, 2, 3
            await baton.handle_event(SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=attempt,
                execution_success=False,
                error_classification="EXECUTION_ERROR",
            ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "failed"
        assert state.normal_attempts == 3

    async def test_one_before_max_retries_still_schedulable(self) -> None:
        """Sheet at max_retries - 1 is still retry_scheduled, not failed."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=3,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Send max_retries - 1 failed attempts
        for attempt in range(1, 3):  # 1, 2
            await baton.handle_event(SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=attempt,
                execution_success=False,
                error_classification="EXECUTION_ERROR",
            ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "retry_scheduled"
        assert state.normal_attempts == 2

    async def test_zero_max_retries_fails_immediately(self) -> None:
        """With max_retries=0, any failure means immediate failure."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=0,
            ),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="EXECUTION_ERROR",
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "failed"


# ---------------------------------------------------------------------------
# Rate limit tests — rate limits must NEVER count as failures
# ---------------------------------------------------------------------------


class TestRateLimitHandling:
    """Rate limits are tempo changes, not failures."""

    @pytest.mark.adversarial
    async def test_rate_limit_does_not_increment_attempts(self) -> None:
        """Rate-limited attempts must not count toward retry budget."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Send 100 rate-limited attempts — none should count
        for i in range(100):
            await baton.handle_event(SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=i + 1,
                execution_success=False,
                rate_limited=True,
            ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.normal_attempts == 0
        assert state.status != "failed"

    @pytest.mark.adversarial
    async def test_rate_limit_followed_by_real_failure(self) -> None:
        """After rate limits, a real failure still counts correctly."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
            ),
        }
        baton.register_job("j1", sheets, {})

        # 5 rate limits (don't count)
        for i in range(5):
            await baton.handle_event(SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=i + 1,
                execution_success=False,
                rate_limited=True,
            ))

        # 1 real failure (counts)
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=6,
            execution_success=False,
            error_classification="TRANSIENT",
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.normal_attempts == 1
        assert state.status == "retry_scheduled"

    @pytest.mark.adversarial
    async def test_rate_limit_then_success(self) -> None:
        """Rate limits followed by success marks sheet as completed."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Rate limited first
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            rate_limited=True,
        ))

        # Then succeeds
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

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "completed"
        assert state.normal_attempts == 0


# ---------------------------------------------------------------------------
# Auth failure tests — auth failures must be immediately fatal
# ---------------------------------------------------------------------------


class TestAuthFailure:
    """AUTH_FAILURE errors skip retry entirely."""

    async def test_auth_failure_immediate_fail(self) -> None:
        """Auth failure is fatal regardless of remaining retries."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=100,  # Lots of retries available
            ),
        }
        baton.register_job("j1", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="AUTH_FAILURE",
            error_message="Invalid API key",
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "failed"
        # Auth failure should not increment normal_attempts
        # (it bypasses the retry logic entirely)


# ---------------------------------------------------------------------------
# Validation pass rate boundary tests
# ---------------------------------------------------------------------------


class TestValidationPassRate:
    """Tests for the validation_pass_rate decision boundaries."""

    async def test_100_percent_pass_rate_completes(self) -> None:
        """100% validation pass rate means success."""
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
            validations_passed=5,
            validations_total=5,
            validation_pass_rate=100.0,
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "completed"

    async def test_zero_percent_pass_rate_retries(self) -> None:
        """0% validation pass rate triggers retry."""
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
            validations_total=5,
            validation_pass_rate=0.0,
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status in ("retry_scheduled", "pending")

    async def test_partial_pass_rate_retries(self) -> None:
        """Partial validation triggers retry (future: completion mode)."""
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
            validations_passed=3,
            validations_total=5,
            validation_pass_rate=60.0,
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # Should retry (not fail, not complete)
        assert state.status in ("retry_scheduled", "pending")

    async def test_no_validations_still_completes(self) -> None:
        """0 validations total with success and 100% pass rate completes."""
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
            validation_pass_rate=100.0,  # No validations = 100% pass
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "completed"


# ---------------------------------------------------------------------------
# Attempt history tracking
# ---------------------------------------------------------------------------


class TestAttemptHistory:
    """The baton preserves full attempt history for diagnostics."""

    async def test_all_attempts_recorded(self) -> None:
        """Every attempt result is stored in the sheet's history."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=5,
            ),
        }
        baton.register_job("j1", sheets, {})

        for i in range(3):
            await baton.handle_event(SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=i + 1,
                execution_success=False,
                error_classification="TRANSIENT",
            ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert len(state.attempt_results) == 3
        assert state.attempt_results[0].attempt == 1
        assert state.attempt_results[2].attempt == 3

    async def test_rate_limited_attempts_also_recorded(self) -> None:
        """Rate-limited attempts are recorded in history (for diagnostics)."""
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
            execution_success=False,
            rate_limited=True,
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert len(state.attempt_results) == 1
        assert state.attempt_results[0].rate_limited is True


# ---------------------------------------------------------------------------
# Multi-job interaction tests
# ---------------------------------------------------------------------------


class TestMultiJobInteraction:
    """Tests for correct isolation between jobs."""

    @pytest.mark.adversarial
    async def test_failure_in_one_job_does_not_affect_another(self) -> None:
        """A sheet failing in job A doesn't impact job B."""
        baton = BatonCore()
        sheets1 = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        sheets2 = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets1, {})
        baton.register_job("j2", sheets2, {})

        # Fail j1's sheet
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="AUTH_FAILURE",
        ))

        # j2's sheet should be unaffected
        state2 = baton.get_sheet_state("j2", 1)
        assert state2 is not None
        assert state2.status == "pending"

    @pytest.mark.adversarial
    async def test_pause_one_job_does_not_pause_another(self) -> None:
        """Pausing job A doesn't affect job B's ready sheets."""
        baton = BatonCore()
        sheets1 = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        sheets2 = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets1, {})
        baton.register_job("j2", sheets2, {})

        await baton.handle_event(PauseJob(job_id="j1"))

        # j1 has no ready sheets (paused)
        assert len(baton.get_ready_sheets("j1")) == 0
        # j2 still has ready sheets
        assert len(baton.get_ready_sheets("j2")) == 1


# ---------------------------------------------------------------------------
# Dependency resolution edge cases
# ---------------------------------------------------------------------------


class TestDependencyEdgeCases:
    """Edge cases in dependency resolution."""

    async def test_diamond_dependency(self) -> None:
        """Diamond: A → B, A → C, B → D, C → D. D ready only when B AND C done."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
            4: SheetExecutionState(sheet_num=4, instrument_name="claude-code"),
        }
        deps = {2: [1], 3: [1], 4: [2, 3]}
        baton.register_job("j1", sheets, deps)

        # Complete A
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1, validation_pass_rate=100.0,
        ))

        ready = baton.get_ready_sheets("j1")
        ready_nums = sorted(s.sheet_num for s in ready)
        assert ready_nums == [2, 3]  # B and C ready, D not yet

        # Complete B only
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=2, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1, validation_pass_rate=100.0,
        ))

        ready = baton.get_ready_sheets("j1")
        ready_nums = sorted(s.sheet_num for s in ready)
        assert ready_nums == [3]  # Only C ready; D still waiting on C

        # Complete C
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=3, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1, validation_pass_rate=100.0,
        ))

        ready = baton.get_ready_sheets("j1")
        ready_nums = sorted(s.sheet_num for s in ready)
        assert ready_nums == [4]  # Now D is ready

    async def test_wide_fan_out_all_ready_simultaneously(self) -> None:
        """A single dependency fans out to many sheets — all become ready at once."""
        baton = BatonCore()
        fan_out_width = 50
        sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")}
        deps: dict[int, list[int]] = {}
        for i in range(2, fan_out_width + 2):
            sheets[i] = SheetExecutionState(
                sheet_num=i, instrument_name="gemini-cli"
            )
            deps[i] = [1]
        baton.register_job("j1", sheets, deps)

        # Complete the root
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1, validation_pass_rate=100.0,
        ))

        ready = baton.get_ready_sheets("j1")
        assert len(ready) == fan_out_width

    @pytest.mark.adversarial
    async def test_missing_dependency_treated_as_satisfied(self) -> None:
        """If a dependency references a non-existent sheet, treat as satisfied (defensive)."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        # Sheet 2 depends on sheet 99 (which doesn't exist)
        deps = {2: [99]}
        baton.register_job("j1", sheets, deps)

        ready = baton.get_ready_sheets("j1")
        ready_nums = sorted(s.sheet_num for s in ready)
        # Sheet 2 should be ready — missing dep is treated as satisfied
        assert 2 in ready_nums


# ---------------------------------------------------------------------------
# RetryDue integration tests
# ---------------------------------------------------------------------------


class TestRetryDueIntegration:
    """Tests for RetryDue timer events moving sheets back to dispatchable."""

    async def test_retry_due_moves_sheet_to_pending(self) -> None:
        """RetryDue event moves a retry_scheduled sheet back to pending."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=3,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Fail the sheet (goes to retry_scheduled)
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
        assert state.status == "retry_scheduled"

        # Timer fires — sheet becomes pending
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "pending"

    async def test_retry_due_for_non_retry_scheduled_is_noop(self) -> None:
        """RetryDue for a sheet not in retry_scheduled is ignored."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})

        # Sheet is still pending
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == "pending"  # Unchanged

    async def test_full_retry_cycle(self) -> None:
        """Fail → retry_scheduled → RetryDue → pending → (dispatchable)."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=3,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Fail
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="TRANSIENT",
        ))
        assert baton.get_sheet_state("j1", 1).status == "retry_scheduled"  # type: ignore[union-attr]

        # Timer fires
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))
        assert baton.get_sheet_state("j1", 1).status == "pending"  # type: ignore[union-attr]

        # Now the sheet should be in the ready list
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 1
        assert ready[0].sheet_num == 1
