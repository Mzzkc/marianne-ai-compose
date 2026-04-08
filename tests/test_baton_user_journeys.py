"""Exploratory tests for the baton — user journey scenarios and edge cases.

I test stories, not functions. These tests simulate what happens when real
users interact with Marianne through the baton. Not the idealized user who
follows the happy path — the real user who hits the back button at the
worst moment, whose API key expires mid-job, who tries to pause a job
that's already failing.

Each test class tells a story. Each test method is a scene in that story.

@pytest.mark.adversarial
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState
from marianne.daemon.baton.events import (
    CancelJob,
    EscalationNeeded,
    EscalationResolved,
    EscalationTimeout,
    JobTimeout,
    PauseJob,
    ProcessExited,
    RateLimitExpired,
    ResumeJob,
    RetryDue,
    SheetAttemptResult,
    SheetSkipped,
    ShutdownRequested,
)


# =============================================================================
# Story 1: Sarah's First Score
#
# Sarah is a new user. She wrote her first 3-movement score. Movement 1
# works, movement 2 fails once then succeeds, movement 3 hits a rate
# limit mid-execution. Life intervenes at every step.
# =============================================================================


class TestSarahsFirstScore:
    """Sarah is a new user running her first score."""

    @pytest.mark.adversarial
    async def test_full_first_run_journey(self) -> None:
        """Movement 1 completes, 2 fails then retries, 3 succeeds after rate limit."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code", max_retries=3),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code", max_retries=3),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code", max_retries=3),
        }
        deps = {2: [1], 3: [2]}
        baton.register_job("sarah-first", sheets, deps)

        # Movement 1 completes perfectly
        await baton.handle_event(SheetAttemptResult(
            job_id="sarah-first", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=2, validations_total=2, validation_pass_rate=100.0,
        ))

        assert baton.get_sheet_state("sarah-first", 1).status == BatonSheetStatus.COMPLETED  # type: ignore[union-attr]
        ready = baton.get_ready_sheets("sarah-first")
        assert len(ready) == 1
        assert ready[0].sheet_num == 2

        # Movement 2 fails on first attempt — timeout
        await baton.handle_event(SheetAttemptResult(
            job_id="sarah-first", sheet_num=2, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="TRANSIENT", error_message="Process timed out",
        ))

        assert baton.get_sheet_state("sarah-first", 2).status == BatonSheetStatus.RETRY_SCHEDULED  # type: ignore[union-attr]
        # Movement 3 is NOT ready — still depends on 2
        assert len(baton.get_ready_sheets("sarah-first")) == 0

        # Timer fires — retry
        await baton.handle_event(RetryDue(job_id="sarah-first", sheet_num=2))
        assert baton.get_sheet_state("sarah-first", 2).status == BatonSheetStatus.PENDING  # type: ignore[union-attr]

        # Movement 2 succeeds on retry
        await baton.handle_event(SheetAttemptResult(
            job_id="sarah-first", sheet_num=2, instrument_name="claude-code",
            attempt=2, execution_success=True,
            validations_passed=1, validations_total=1, validation_pass_rate=100.0,
        ))

        assert baton.get_sheet_state("sarah-first", 2).status == BatonSheetStatus.COMPLETED  # type: ignore[union-attr]
        ready = baton.get_ready_sheets("sarah-first")
        assert len(ready) == 1
        assert ready[0].sheet_num == 3

        # Movement 3 hits a rate limit — Sarah panics but the system handles it
        await baton.handle_event(SheetAttemptResult(
            job_id="sarah-first", sheet_num=3, instrument_name="claude-code",
            attempt=1, execution_success=False, rate_limited=True,
        ))

        assert baton.get_sheet_state("sarah-first", 3).status == BatonSheetStatus.WAITING  # type: ignore[union-attr]
        assert baton.get_sheet_state("sarah-first", 3).normal_attempts == 0  # type: ignore[union-attr]

        # Rate limit clears
        await baton.handle_event(RateLimitExpired(instrument="claude-code"))
        assert baton.get_sheet_state("sarah-first", 3).status == BatonSheetStatus.PENDING  # type: ignore[union-attr]

        # Movement 3 succeeds
        await baton.handle_event(SheetAttemptResult(
            job_id="sarah-first", sheet_num=3, instrument_name="claude-code",
            attempt=2, execution_success=True,
            validations_passed=3, validations_total=3, validation_pass_rate=100.0,
        ))

        assert baton.get_sheet_state("sarah-first", 3).status == BatonSheetStatus.COMPLETED  # type: ignore[union-attr]
        assert baton.is_job_complete("sarah-first")

    @pytest.mark.adversarial
    async def test_sarah_pauses_mid_failure(self) -> None:
        """Sarah sees movement 2 failing and hits pause out of panic."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code", max_retries=3),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
        }
        deps = {2: [1], 3: [2]}
        baton.register_job("sarah-panic", sheets, deps)

        # Complete movement 1
        await baton.handle_event(SheetAttemptResult(
            job_id="sarah-panic", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1, validation_pass_rate=100.0,
        ))

        # Movement 2 fails
        await baton.handle_event(SheetAttemptResult(
            job_id="sarah-panic", sheet_num=2, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="TRANSIENT",
        ))

        # Sarah hits pause
        await baton.handle_event(PauseJob(job_id="sarah-panic"))
        assert baton.is_job_paused("sarah-panic")
        # No sheets should be ready even after retry timer
        await baton.handle_event(RetryDue(job_id="sarah-panic", sheet_num=2))
        assert len(baton.get_ready_sheets("sarah-panic")) == 0

        # Sarah resumes
        await baton.handle_event(ResumeJob(job_id="sarah-panic"))
        assert not baton.is_job_paused("sarah-panic")
        # Now sheet 2 should be ready for retry
        ready = baton.get_ready_sheets("sarah-panic")
        assert len(ready) == 1
        assert ready[0].sheet_num == 2


# =============================================================================
# Story 2: The Multi-Instrument Orchestra
#
# A power user runs a 5-movement score across two instruments.
# claude-code for creative work, gemini-cli for review.
# claude-code hits rate limits. gemini-cli keeps going.
# =============================================================================


class TestMultiInstrumentOrchestra:
    """A power user with multi-instrument scores."""

    @pytest.mark.adversarial
    async def test_rate_limit_on_one_instrument_doesnt_block_another(self) -> None:
        """claude-code rate limited; gemini-cli sheets keep dispatching."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="gemini-cli"),
            4: SheetExecutionState(sheet_num=4, instrument_name="gemini-cli"),
        }
        # 1 and 3 are independent; 2 depends on 1; 4 depends on 3
        deps = {2: [1], 4: [3]}
        baton.register_job("orchestra", sheets, deps)

        # Sheet 1 (claude-code) hits rate limit
        await baton.handle_event(SheetAttemptResult(
            job_id="orchestra", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=False, rate_limited=True,
        ))

        # claude-code sheet is waiting, but gemini-cli sheet 3 is still ready
        assert baton.get_sheet_state("orchestra", 1).status == BatonSheetStatus.WAITING  # type: ignore[union-attr]
        ready = baton.get_ready_sheets("orchestra")
        ready_nums = sorted(s.sheet_num for s in ready)
        assert 3 in ready_nums  # gemini-cli is unaffected

        # Complete sheet 3 (gemini-cli works fine)
        await baton.handle_event(SheetAttemptResult(
            job_id="orchestra", sheet_num=3, instrument_name="gemini-cli",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1, validation_pass_rate=100.0,
        ))

        # Sheet 4 (gemini-cli) is now ready even though claude-code is still limited
        ready = baton.get_ready_sheets("orchestra")
        ready_nums = sorted(s.sheet_num for s in ready)
        assert 4 in ready_nums

    @pytest.mark.adversarial
    async def test_rate_limit_cleared_only_for_matching_instrument(self) -> None:
        """RateLimitExpired for gemini-cli doesn't unblock claude-code sheets."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
        }
        baton.register_job("inst-test", sheets, {})

        # Both hit rate limits
        await baton.handle_event(SheetAttemptResult(
            job_id="inst-test", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=False, rate_limited=True,
        ))
        await baton.handle_event(SheetAttemptResult(
            job_id="inst-test", sheet_num=2, instrument_name="gemini-cli",
            attempt=1, execution_success=False, rate_limited=True,
        ))

        assert baton.get_sheet_state("inst-test", 1).status == BatonSheetStatus.WAITING  # type: ignore[union-attr]
        assert baton.get_sheet_state("inst-test", 2).status == BatonSheetStatus.WAITING  # type: ignore[union-attr]

        # Only gemini-cli rate limit clears
        await baton.handle_event(RateLimitExpired(instrument="gemini-cli"))

        # gemini sheet is pending, claude sheet is still waiting
        assert baton.get_sheet_state("inst-test", 2).status == BatonSheetStatus.PENDING  # type: ignore[union-attr]
        assert baton.get_sheet_state("inst-test", 1).status == BatonSheetStatus.WAITING  # type: ignore[union-attr]


# =============================================================================
# Story 3: The Interrupted Performance
#
# The user's API key expires mid-job. Some sheets are in-flight.
# The conductor handles it gracefully.
# =============================================================================


class TestInterruptedPerformance:
    """Things that go wrong during a performance."""

    @pytest.mark.adversarial
    async def test_auth_failure_mid_job_only_kills_that_sheet(self) -> None:
        """Auth failure on sheet 3 doesn't kill sheets 1 (completed) or 4 (pending)."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
            4: SheetExecutionState(sheet_num=4, instrument_name="claude-code"),
        }
        deps = {2: [1], 3: [1], 4: [2, 3]}
        baton.register_job("auth-expire", sheets, deps)

        # Complete sheet 1
        await baton.handle_event(SheetAttemptResult(
            job_id="auth-expire", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1, validation_pass_rate=100.0,
        ))

        # Complete sheet 2
        await baton.handle_event(SheetAttemptResult(
            job_id="auth-expire", sheet_num=2, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1, validation_pass_rate=100.0,
        ))

        # Sheet 3 gets auth failure (API key expired!)
        await baton.handle_event(SheetAttemptResult(
            job_id="auth-expire", sheet_num=3, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="AUTH_FAILURE",
            error_message="Invalid API key",
        ))

        # Sheet 3 is failed
        assert baton.get_sheet_state("auth-expire", 3).status == BatonSheetStatus.FAILED  # type: ignore[union-attr]
        # Sheet 1 and 2 are still completed (not rolled back)
        assert baton.get_sheet_state("auth-expire", 1).status == BatonSheetStatus.COMPLETED  # type: ignore[union-attr]
        assert baton.get_sheet_state("auth-expire", 2).status == BatonSheetStatus.COMPLETED  # type: ignore[union-attr]
        # Sheet 4 depends on both 2 and 3 — 3 is failed (not in _SATISFIED_STATUSES)
        # So sheet 4 should NOT be ready
        ready = baton.get_ready_sheets("auth-expire")
        assert len(ready) == 0

    @pytest.mark.adversarial
    async def test_process_crashes_mid_sheet(self) -> None:
        """Backend process dies (SIGKILL, OOM) while executing a sheet."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code",
                status=BatonSheetStatus.DISPATCHED,  # Already dispatched, running
                max_retries=3,
            ),
        }
        baton.register_job("crash-test", sheets, {})

        # Process crashes — observer detects it
        await baton.handle_event(ProcessExited(
            job_id="crash-test", sheet_num=1, pid=12345, exit_code=-9,
        ))

        state = baton.get_sheet_state("crash-test", 1)
        assert state is not None
        # Should retry, not fail (crash = transient)
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED
        assert state.normal_attempts == 1

    @pytest.mark.adversarial
    async def test_timeout_cancels_remaining_sheets(self) -> None:
        """Job timeout cancels all non-terminal sheets but preserves completed ones."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code", status=BatonSheetStatus.COMPLETED),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code", status=BatonSheetStatus.DISPATCHED),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code", status=BatonSheetStatus.PENDING),
        }
        baton.register_job("timeout-test", sheets, {})

        await baton.handle_event(JobTimeout(job_id="timeout-test"))

        assert baton.get_sheet_state("timeout-test", 1).status == BatonSheetStatus.COMPLETED  # type: ignore[union-attr]
        assert baton.get_sheet_state("timeout-test", 2).status == BatonSheetStatus.CANCELLED  # type: ignore[union-attr]
        assert baton.get_sheet_state("timeout-test", 3).status == BatonSheetStatus.CANCELLED  # type: ignore[union-attr]


# =============================================================================
# Story 4: The Escalation Dance
#
# A sheet needs human judgment. The composer is busy. The timeout fires.
# Then the composer responds — but too late.
# =============================================================================


class TestEscalationDance:
    """Fermata scenarios — when the conductor asks the composer to decide."""

    @pytest.mark.adversarial
    async def test_escalation_pauses_job(self) -> None:
        """When escalation is needed, the job pauses and no new sheets dispatch."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("esc-test", sheets, {})

        await baton.handle_event(EscalationNeeded(
            job_id="esc-test", sheet_num=1,
            reason="Low confidence in generated code",
            options=["retry", "accept", "skip"],
        ))

        assert baton.get_sheet_state("esc-test", 1).status == BatonSheetStatus.FERMATA  # type: ignore[union-attr]
        assert baton.is_job_paused("esc-test")
        assert len(baton.get_ready_sheets("esc-test")) == 0

    @pytest.mark.adversarial
    async def test_escalation_resolved_retry(self) -> None:
        """Composer decides to retry — sheet goes back to pending."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("esc-retry", sheets, {})

        await baton.handle_event(EscalationNeeded(
            job_id="esc-retry", sheet_num=1,
            reason="test", options=["retry"],
        ))
        await baton.handle_event(EscalationResolved(
            job_id="esc-retry", sheet_num=1, decision="retry",
        ))

        assert baton.get_sheet_state("esc-retry", 1).status == BatonSheetStatus.PENDING  # type: ignore[union-attr]
        assert not baton.is_job_paused("esc-retry")

    @pytest.mark.adversarial
    async def test_escalation_timeout_fails_sheet(self) -> None:
        """Nobody responds to escalation — sheet fails after timeout."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("esc-timeout", sheets, {})

        await baton.handle_event(EscalationNeeded(
            job_id="esc-timeout", sheet_num=1,
            reason="test", options=["retry"],
        ))
        assert baton.get_sheet_state("esc-timeout", 1).status == BatonSheetStatus.FERMATA  # type: ignore[union-attr]

        # Nobody responds. Timeout fires.
        await baton.handle_event(EscalationTimeout(
            job_id="esc-timeout", sheet_num=1,
        ))

        assert baton.get_sheet_state("esc-timeout", 1).status == BatonSheetStatus.FAILED  # type: ignore[union-attr]
        # Job should be unpaused after timeout resolution
        assert not baton.is_job_paused("esc-timeout")

    @pytest.mark.adversarial
    async def test_late_resolution_after_timeout(self) -> None:
        """Composer responds after timeout — the decision should be a no-op."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("late-resolve", sheets, {})

        await baton.handle_event(EscalationNeeded(
            job_id="late-resolve", sheet_num=1,
            reason="test", options=["retry"],
        ))

        # Timeout fires first
        await baton.handle_event(EscalationTimeout(
            job_id="late-resolve", sheet_num=1,
        ))
        assert baton.get_sheet_state("late-resolve", 1).status == BatonSheetStatus.FAILED  # type: ignore[union-attr]

        # Late resolution arrives — sheet is no longer in fermata
        await baton.handle_event(EscalationResolved(
            job_id="late-resolve", sheet_num=1, decision="retry",
        ))

        # The late resolution should NOT change the failed status
        # because the sheet is no longer in fermata
        assert baton.get_sheet_state("late-resolve", 1).status == BatonSheetStatus.FAILED  # type: ignore[union-attr]


# =============================================================================
# Story 5: The Graceful Shutdown
#
# The user runs `mzt stop` while jobs are in various states.
# Completed work must survive. In-flight work must be cleanly cancelled.
# =============================================================================


class TestGracefulShutdown:
    """Shutdown scenarios — clean exit, no orphans."""

    @pytest.mark.adversarial
    async def test_graceful_shutdown_preserves_completed(self) -> None:
        """Graceful shutdown doesn't cancel already-completed sheets."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code", status=BatonSheetStatus.COMPLETED),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code", status=BatonSheetStatus.DISPATCHED),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code", status=BatonSheetStatus.PENDING),
        }
        baton.register_job("shutdown-test", sheets, {})

        await baton.handle_event(ShutdownRequested(graceful=True))

        # Graceful shutdown doesn't cancel anything — just signals the loop to stop
        assert baton.get_sheet_state("shutdown-test", 1).status == BatonSheetStatus.COMPLETED  # type: ignore[union-attr]
        # Graceful leaves in-flight sheets alone for the conductor to handle
        assert baton._shutting_down  # noqa: SLF001 — test needs internals

    @pytest.mark.adversarial
    async def test_forced_shutdown_cancels_everything(self) -> None:
        """Forced shutdown cancels all non-terminal sheets."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code", status=BatonSheetStatus.COMPLETED),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code", status=BatonSheetStatus.DISPATCHED),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code", status=BatonSheetStatus.PENDING),
            4: SheetExecutionState(sheet_num=4, instrument_name="claude-code", status=BatonSheetStatus.FAILED),
            5: SheetExecutionState(sheet_num=5, instrument_name="claude-code", status=BatonSheetStatus.RETRY_SCHEDULED),
        }
        baton.register_job("force-shutdown", sheets, {})

        await baton.handle_event(ShutdownRequested(graceful=False))

        assert baton.get_sheet_state("force-shutdown", 1).status == BatonSheetStatus.COMPLETED  # type: ignore[union-attr]
        assert baton.get_sheet_state("force-shutdown", 2).status == BatonSheetStatus.CANCELLED  # type: ignore[union-attr]
        assert baton.get_sheet_state("force-shutdown", 3).status == BatonSheetStatus.CANCELLED  # type: ignore[union-attr]
        assert baton.get_sheet_state("force-shutdown", 4).status == BatonSheetStatus.FAILED  # type: ignore[union-attr]
        assert baton.get_sheet_state("force-shutdown", 5).status == BatonSheetStatus.CANCELLED  # type: ignore[union-attr]

    @pytest.mark.adversarial
    async def test_cancel_job_then_deregister(self) -> None:
        """Cancelling a job deregisters it — no ghost state left behind."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("cancel-me", sheets, {})
        assert baton.job_count == 1

        await baton.handle_event(CancelJob(job_id="cancel-me"))

        assert baton.job_count == 0
        assert baton.get_sheet_state("cancel-me", 1) is None


# =============================================================================
# Story 6: The Validation Landmine (F-018)
#
# A musician forgets to set validation_pass_rate to 100.0 when there
# are no validations. The default (0.0) means "all failed." The baton
# retries until exhaustion — the user sees failures for a successful sheet.
# =============================================================================


class TestValidationPassRateLandmine:
    """F-018: The implicit validation_pass_rate contract."""

    @pytest.mark.adversarial
    async def test_forgotten_pass_rate_with_no_validations_completes(
        self,
    ) -> None:
        """F-018 fix: no validations + success = complete, even with default 0.0.

        The F-018 guard in core.py treats validations_total=0 with
        execution_success=True as 100% pass rate. This prevents the
        landmine where a musician forgets to set validation_pass_rate.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code", max_retries=2,
            ),
        }
        baton.register_job("f018", sheets, {})

        # Musician reports success but leaves validation_pass_rate at default (0.0)
        await baton.handle_event(SheetAttemptResult(
            job_id="f018", sheet_num=1, instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=0,
            validations_total=0,
            validation_pass_rate=0.0,  # DEFAULT — no longer a landmine
        ))

        state = baton.get_sheet_state("f018", 1)
        assert state is not None
        # The F-018 guard converts 0.0 → 100.0 when validations_total=0
        assert state.status == BatonSheetStatus.COMPLETED
        assert state.normal_attempts == 0

    @pytest.mark.adversarial
    async def test_correct_pass_rate_with_no_validations(self) -> None:
        """When musician correctly sets pass_rate=100.0 with no validations, completes."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("f018-correct", sheets, {})

        await baton.handle_event(SheetAttemptResult(
            job_id="f018-correct", sheet_num=1, instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=0,
            validations_total=0,
            validation_pass_rate=100.0,  # CORRECT — no validations = pass
        ))

        state = baton.get_sheet_state("f018-correct", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.COMPLETED


# =============================================================================
# Story 7: The Ghost Job
#
# Events arrive for jobs that don't exist, sheets that were never registered,
# or jobs that were already cancelled. The baton must not crash.
# =============================================================================


class TestGhostEvents:
    """Events for non-existent or cancelled entities."""

    @pytest.mark.adversarial
    async def test_attempt_result_for_unknown_job(self) -> None:
        """SheetAttemptResult for non-existent job is silently ignored."""
        baton = BatonCore()

        # No jobs registered — event arrives anyway
        await baton.handle_event(SheetAttemptResult(
            job_id="phantom", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validation_pass_rate=100.0,
        ))
        # Should not crash
        assert baton.get_sheet_state("phantom", 1) is None

    @pytest.mark.adversarial
    async def test_attempt_result_for_unknown_sheet(self) -> None:
        """SheetAttemptResult for a non-existent sheet in a valid job."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("real-job", sheets, {})

        # Sheet 99 doesn't exist
        await baton.handle_event(SheetAttemptResult(
            job_id="real-job", sheet_num=99, instrument_name="claude-code",
            attempt=1, execution_success=False,
        ))
        # Should not crash; existing sheet unaffected
        assert baton.get_sheet_state("real-job", 1).status == BatonSheetStatus.PENDING  # type: ignore[union-attr]

    @pytest.mark.adversarial
    async def test_retry_due_for_cancelled_job(self) -> None:
        """RetryDue arrives after job was cancelled."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("cancel-first", sheets, {})

        # Cancel the job
        await baton.handle_event(CancelJob(job_id="cancel-first"))

        # Late RetryDue arrives — job is gone
        await baton.handle_event(RetryDue(job_id="cancel-first", sheet_num=1))
        # Should not crash
        assert baton.job_count == 0

    @pytest.mark.adversarial
    async def test_pause_unknown_job(self) -> None:
        """Pausing a non-existent job is a no-op."""
        baton = BatonCore()
        await baton.handle_event(PauseJob(job_id="does-not-exist"))
        # Should not crash
        assert not baton.is_job_paused("does-not-exist")

    @pytest.mark.adversarial
    async def test_resume_unknown_job(self) -> None:
        """Resuming a non-existent job is a no-op."""
        baton = BatonCore()
        await baton.handle_event(ResumeJob(job_id="does-not-exist"))
        # Should not crash

    @pytest.mark.adversarial
    async def test_duplicate_job_registration_rejected(self) -> None:
        """Registering the same job_id twice doesn't overwrite."""
        baton = BatonCore()
        sheets1 = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")}
        sheets2 = {1: SheetExecutionState(sheet_num=1, instrument_name="gemini-cli")}

        baton.register_job("dupe", sheets1, {})
        baton.register_job("dupe", sheets2, {})  # Should be rejected

        # Original registration preserved
        state = baton.get_sheet_state("dupe", 1)
        assert state is not None
        assert state.instrument_name == "claude-code"


# =============================================================================
# Story 8: The 706-Sheet Concert
#
# Our own orchestra score — 706 sheets, 65 stages, 17 movements.
# The baton must handle this scale without blowing up.
# =============================================================================


class TestLargeScaleOrchestra:
    """Scale tests — real-world sheet counts."""

    @pytest.mark.adversarial
    async def test_register_706_sheets(self) -> None:
        """The baton handles 706 sheets (our actual concert scale)."""
        baton = BatonCore()
        sheets: dict[int, SheetExecutionState] = {}
        for i in range(1, 707):
            sheets[i] = SheetExecutionState(
                sheet_num=i,
                instrument_name=f"instrument-{i % 5}",
            )

        # Linear dependency chain in groups of 32 (our movement size)
        deps: dict[int, list[int]] = {}
        for i in range(33, 707):
            # Each group depends on the previous group completing
            group_start = ((i - 1) // 32) * 32 + 1
            if group_start > 1:
                # Depend on last sheet of previous group
                deps[i] = [group_start - 1]

        baton.register_job("v3-concert", sheets, deps)

        assert baton.job_count == 1
        # First 32 sheets should be ready (no dependencies)
        ready = baton.get_ready_sheets("v3-concert")
        assert len(ready) >= 32

    @pytest.mark.adversarial
    async def test_diagnostics_on_large_job(self) -> None:
        """Diagnostics work correctly with many sheets."""
        baton = BatonCore()
        sheets: dict[int, SheetExecutionState] = {}
        for i in range(1, 101):
            sheets[i] = SheetExecutionState(
                sheet_num=i,
                instrument_name="claude-code" if i <= 50 else "gemini-cli",
                status=BatonSheetStatus.COMPLETED if i <= 30 else BatonSheetStatus.PENDING,
            )

        baton.register_job("big-job", sheets, {})
        diag = baton.get_diagnostics("big-job")

        assert diag is not None
        assert diag["sheets"]["total"] == 100
        assert diag["sheets"]["completed"] == 30
        assert diag["sheets"]["pending"] == 70
        assert "claude-code" in diag["instruments_used"]
        assert "gemini-cli" in diag["instruments_used"]


# =============================================================================
# Story 9: The Skip Cascade
#
# Movement 2 is skipped. Movement 3 depends on 2. Movement 4 depends on 3.
# Does the skip satisfy dependencies? (It should — skipped = satisfied.)
# =============================================================================


class TestSkipCascade:
    """Skipped sheets and dependency satisfaction."""

    @pytest.mark.adversarial
    async def test_skipped_dependency_is_satisfied(self) -> None:
        """A skipped sheet satisfies downstream dependencies."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
        }
        deps = {2: [1], 3: [2]}
        baton.register_job("skip-chain", sheets, deps)

        # Skip sheet 1 (e.g., --start-sheet 2)
        await baton.handle_event(SheetSkipped(
            job_id="skip-chain", sheet_num=1, reason="start_sheet override",
        ))

        # Sheet 2 should now be ready — skipped satisfies the dependency
        ready = baton.get_ready_sheets("skip-chain")
        assert len(ready) == 1
        assert ready[0].sheet_num == 2

    @pytest.mark.adversarial
    async def test_failed_dependency_blocks_downstream(self) -> None:
        """A failed sheet does NOT satisfy downstream dependencies."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code", max_retries=0),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        deps = {2: [1]}
        baton.register_job("fail-block", sheets, deps)

        # Sheet 1 fails (max_retries=0)
        await baton.handle_event(SheetAttemptResult(
            job_id="fail-block", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="EXECUTION_ERROR",
        ))

        assert baton.get_sheet_state("fail-block", 1).status == BatonSheetStatus.FAILED  # type: ignore[union-attr]
        # Sheet 2 should NOT be ready — failed is not a satisfied state
        ready = baton.get_ready_sheets("fail-block")
        assert len(ready) == 0


# =============================================================================
# Story 10: The Interleaved Chaos
#
# Events arrive out of order. Rate limits clear before the sheet
# even reports them. Retries fire for sheets that already completed.
# The real world is not sequential.
# =============================================================================


class TestInterleavedChaos:
    """Out-of-order and concurrent event scenarios."""

    @pytest.mark.adversarial
    async def test_success_after_retry_scheduled_completes(self) -> None:
        """Sheet was retry_scheduled, but a late success result arrives — it completes."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code", max_retries=3,
            ),
        }
        baton.register_job("race-1", sheets, {})

        # First attempt fails
        await baton.handle_event(SheetAttemptResult(
            job_id="race-1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="TRANSIENT",
        ))
        assert baton.get_sheet_state("race-1", 1).status == BatonSheetStatus.RETRY_SCHEDULED  # type: ignore[union-attr]

        # But a delayed success result from a parallel path arrives
        await baton.handle_event(SheetAttemptResult(
            job_id="race-1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True,
            validations_passed=1, validations_total=1, validation_pass_rate=100.0,
        ))

        # The success should complete the sheet even though it was retry_scheduled
        state = baton.get_sheet_state("race-1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.COMPLETED

    @pytest.mark.adversarial
    async def test_multiple_rapid_failures_count_correctly(self) -> None:
        """N rapid failures increment the counter to exactly N."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code", max_retries=10,
            ),
        }
        baton.register_job("rapid", sheets, {})

        for i in range(7):
            await baton.handle_event(SheetAttemptResult(
                job_id="rapid", sheet_num=1, instrument_name="claude-code",
                attempt=i + 1, execution_success=False,
                error_classification="TRANSIENT",
            ))

        state = baton.get_sheet_state("rapid", 1)
        assert state is not None
        assert state.normal_attempts == 7
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED  # Not failed yet (max=10)
