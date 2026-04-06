"""User journey tests for the baton state machine — Movement 2.

These tests tell stories. Each test class is a user scenario: a real person
sitting at a terminal, running a Mozart score, and encountering situations
that the acceptance criteria don't mention.

The baton is the conductor's heart. These tests verify it beats correctly
when life intervenes — when users pause and resume in the middle of
escalations, when jobs get cancelled during rate limits, when process
crashes coincide with cost limits, when the conductor restarts and needs
to reconcile state.

Found by: Journey, Movement 2
Method: Exploratory testing — trace each state transition from the user's
        perspective, not the developer's. Ask "what would happen if..."
        and then prove it.
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState
from marianne.daemon.baton.events import (
    CancelJob,
    EscalationResolved,
    PauseJob,
    ProcessExited,
    RateLimitExpired,
    RateLimitHit,
    ResumeJob,
    SheetAttemptResult,
    ShutdownRequested,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_baton() -> BatonCore:
    """Create a basic baton with no timer."""
    return BatonCore()


def _make_sheets(
    count: int,
    instrument: str = "claude-code",
    max_retries: int = 3,
) -> dict[int, SheetExecutionState]:
    """Create N sheets with given config."""
    return {
        i: SheetExecutionState(
            sheet_num=i,
            instrument_name=instrument,
            max_retries=max_retries,
        )
        for i in range(1, count + 1)
    }


def _attempt_result(
    job_id: str,
    sheet_num: int,
    *,
    execution_success: bool = True,
    pass_rate: float = 100.0,
    validations_total: int = 0,
    instrument: str = "claude-code",
    attempt: int = 1,
    cost_usd: float = 0.50,
    rate_limited: bool = False,
) -> SheetAttemptResult:
    """Create a SheetAttemptResult with sensible defaults."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name=instrument,
        attempt=attempt,
        execution_success=execution_success,
        validation_pass_rate=pass_rate,
        validations_total=validations_total,
        validations_passed=int(validations_total * pass_rate / 100.0),
        duration_seconds=10.0,
        cost_usd=cost_usd,
        rate_limited=rate_limited,
    )


# =============================================================================
# Story 1: Sarah pauses her job, an escalation fires, then she resumes
# =============================================================================


class TestUserPauseDuringEscalation:
    """Sarah starts a 3-sheet job with escalation enabled. Sheet 1 fails
    all retries and enters FERMATA. Before she notices, she pauses the
    whole job because she needs to update the prompt. Now the job is paused
    for TWO reasons: user pause AND escalation. What happens when she
    resumes? Does the escalation get lost? Does the FERMATA sheet get
    properly handled?"""

    @pytest.mark.asyncio
    async def test_user_resume_allows_escalation_resolution(self) -> None:
        """After user resume, the FERMATA sheet should still be in FERMATA
        and escalation resolution should still work."""
        baton = _make_baton()
        sheets = _make_sheets(3)
        baton.register_job(
            "j1", sheets, {},
            escalation_enabled=True,
        )

        # Sheet 1 exhausts retries → FERMATA
        sheets[1].status = BatonSheetStatus.FERMATA
        job = baton._jobs["j1"]
        job.paused = True  # Escalation paused it

        # Sarah pauses the job manually
        await baton.handle_event(PauseJob(job_id="j1"))
        assert job.user_paused is True
        assert job.paused is True

        # Sarah resumes — she updated the prompt
        await baton.handle_event(ResumeJob(job_id="j1"))
        assert job.user_paused is False
        assert job.paused is False

        # FERMATA sheet is still in FERMATA
        assert sheets[1].status == BatonSheetStatus.FERMATA

        # Escalation can still be resolved
        await baton.handle_event(
            EscalationResolved(job_id="j1", sheet_num=1, decision="retry")
        )
        # Sheet should be retryable after escalation resolution
        assert sheets[1].status == BatonSheetStatus.PENDING

    @pytest.mark.asyncio
    async def test_double_pause_both_flags_set(self) -> None:
        """Pausing an already-paused job (escalation) should set user_paused."""
        baton = _make_baton()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {}, escalation_enabled=True)

        sheets[1].status = BatonSheetStatus.FERMATA
        job = baton._jobs["j1"]
        job.paused = True  # Escalation pause

        # User pause on top of escalation pause
        await baton.handle_event(PauseJob(job_id="j1"))

        # Both flags should be true
        assert job.paused is True
        assert job.user_paused is True


# =============================================================================
# Story 2: Marcus's job hits a rate limit during cancellation
# =============================================================================


class TestCancelDuringRateLimit:
    """Marcus has a 10-sheet job. Three sheets are dispatched to claude-code
    when a rate limit hits. They move to WAITING. Marcus decides to cancel
    the whole job. Do the WAITING sheets get properly cancelled? What about
    the rate limit state for the instrument?"""

    @pytest.mark.asyncio
    async def test_cancel_clears_waiting_sheets(self) -> None:
        """Cancelling a job should transition WAITING sheets to CANCELLED."""
        baton = _make_baton()
        sheets = _make_sheets(5)
        baton.register_job("j1", sheets, {})

        # Three sheets dispatched, then rate limited
        sheets[1].status = BatonSheetStatus.WAITING
        sheets[2].status = BatonSheetStatus.WAITING
        sheets[3].status = BatonSheetStatus.DISPATCHED
        # sheets 4, 5 still PENDING

        await baton.handle_event(CancelJob(job_id="j1"))

        # Job should be deregistered — can't check sheets directly
        # because deregister_job removes the job record
        assert "j1" not in baton._jobs

    @pytest.mark.asyncio
    async def test_rate_limit_expired_after_cancel_is_harmless(self) -> None:
        """A rate limit expiry event arriving after job cancellation
        should not crash or cause unexpected state changes."""
        baton = _make_baton()
        sheets = _make_sheets(3)
        baton.register_job("j1", sheets, {})

        sheets[1].status = BatonSheetStatus.WAITING
        sheets[1].instrument_name = "claude-code"

        # Cancel the job
        await baton.handle_event(CancelJob(job_id="j1"))

        # Rate limit expires — job is gone
        await baton.handle_event(
            RateLimitExpired(instrument="claude-code")
        )
        # Should not crash, no state change
        assert "j1" not in baton._jobs


# =============================================================================
# Story 3: Process crash during cost-limited sheet
# =============================================================================


class TestProcessCrashWithCostLimits:
    """Amira's job has per-sheet cost limits. Sheet 2 is dispatched and
    has already spent $4.50 of a $5.00 limit. The process crashes. The
    crash handler increments normal_attempts and schedules a retry. But
    the retry would push the cost over the limit. Does the cost limit
    enforcement catch this before the retry runs?"""

    @pytest.mark.asyncio
    async def test_crash_on_near_limit_sheet_still_retries(self) -> None:
        """Process crash near cost limit should still schedule retry —
        cost is checked on attempt RESULT, not on dispatch."""
        baton = _make_baton()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {})
        baton.set_sheet_cost_limit("j1", 2, 5.00)

        sheets[2].status = BatonSheetStatus.DISPATCHED
        sheets[2].total_cost_usd = 4.50  # Close to limit

        # Process crashes
        await baton.handle_event(
            ProcessExited(job_id="j1", sheet_num=2, pid=12345, exit_code=137)
        )

        # Sheet should be in RETRY_SCHEDULED (crash consumed a retry)
        assert sheets[2].status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheets[2].normal_attempts == 1

    @pytest.mark.asyncio
    async def test_crash_only_affects_dispatched_sheets(self) -> None:
        """Process crash for a PENDING sheet should be ignored —
        only DISPATCHED sheets can crash."""
        baton = _make_baton()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {})

        sheets[1].status = BatonSheetStatus.PENDING

        await baton.handle_event(
            ProcessExited(job_id="j1", sheet_num=1, pid=12345, exit_code=137)
        )

        # Should not change — PENDING sheets can't crash
        assert sheets[1].status == BatonSheetStatus.PENDING
        assert sheets[1].normal_attempts == 0


# =============================================================================
# Story 4: The deregister cleanup gap
# =============================================================================


class TestDeregisterCleanup:
    """When a job is cancelled or completes, deregister_job removes it
    from self._jobs. Cost limits stored in separate dicts must also be
    cleaned up to prevent memory leaks in long-running conductors.

    Found by: Journey, Movement 2
    Related: F-062 (Prism filed — deregister_job memory leak, NOW FIXED)
    """

    @pytest.mark.asyncio
    async def test_cost_limits_cleaned_after_deregister(self) -> None:
        """Verify that cost limit entries are removed on deregistration.
        F-062 is resolved — deregister_job now cleans up cost dicts."""
        baton = _make_baton()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 10.0)
        baton.set_sheet_cost_limit("j1", 1, 5.0)

        # Cancel → deregister
        await baton.handle_event(CancelJob(job_id="j1"))

        # Cost limits should be cleaned up (F-062 fix in core.py:508-522)
        assert "j1" not in baton._job_cost_limits, (
            "F-062 regression: job cost limit should be cleaned after deregister"
        )
        assert ("j1", 1) not in baton._sheet_cost_limits, (
            "F-062 regression: sheet cost limit should be cleaned after deregister"
        )

    @pytest.mark.asyncio
    async def test_instrument_state_persists_after_deregister(self) -> None:
        """Instruments registered by a job should survive deregistration.
        Other jobs might use the same instrument."""
        baton = _make_baton()
        sheets = _make_sheets(1, instrument="claude-code")
        baton.register_job("j1", sheets, {})

        assert "claude-code" in baton._instruments

        baton.deregister_job("j1")

        # Instrument state should persist — other jobs may use it
        assert "claude-code" in baton._instruments


# =============================================================================
# Story 5: Graceful vs ungraceful shutdown
# =============================================================================


class TestShutdownBehavior:
    """The conductor is shutting down. Some sheets are mid-execution.
    Does graceful shutdown leave them running? Does ungraceful cancel
    everything? What about sheets in FERMATA — waiting for a human
    who will never come?"""

    @pytest.mark.asyncio
    async def test_graceful_shutdown_preserves_in_flight(self) -> None:
        """Graceful shutdown should NOT cancel dispatched sheets."""
        baton = _make_baton()
        sheets = _make_sheets(3)
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.DISPATCHED
        sheets[2].status = BatonSheetStatus.COMPLETED

        await baton.handle_event(
            ShutdownRequested(graceful=True)
        )

        # Dispatched sheet should still be dispatched
        assert sheets[1].status == BatonSheetStatus.DISPATCHED
        # Pending sheet should still be pending
        assert sheets[3].status == BatonSheetStatus.PENDING

    @pytest.mark.asyncio
    async def test_ungraceful_shutdown_cancels_everything(self) -> None:
        """Ungraceful shutdown should cancel all non-terminal sheets."""
        baton = _make_baton()
        sheets = _make_sheets(4)
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.DISPATCHED
        sheets[2].status = BatonSheetStatus.COMPLETED
        sheets[3].status = BatonSheetStatus.FERMATA

        await baton.handle_event(
            ShutdownRequested(graceful=False)
        )

        assert sheets[1].status == BatonSheetStatus.CANCELLED
        assert sheets[2].status == BatonSheetStatus.COMPLETED  # Terminal, unchanged
        assert sheets[3].status == BatonSheetStatus.CANCELLED
        assert sheets[4].status == BatonSheetStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_graceful_shutdown_leaves_fermata_waiting(self) -> None:
        """Graceful shutdown should leave FERMATA sheets in FERMATA.
        The user can still resolve them after restart (once restart
        recovery is implemented in step 29)."""
        baton = _make_baton()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {}, escalation_enabled=True)
        sheets[1].status = BatonSheetStatus.FERMATA

        await baton.handle_event(
            ShutdownRequested(graceful=True)
        )

        assert sheets[1].status == BatonSheetStatus.FERMATA


# =============================================================================
# Story 6: Rate limit affects multiple jobs
# =============================================================================


class TestRateLimitCrossJobIsolation:
    """Two jobs use claude-code. A rate limit hits on job A's sheet.
    Does job B's sheets on the same instrument also get affected?
    The baton's rate limit handler iterates ALL jobs — verify this
    is correct and isolated to the right sheets."""

    @pytest.mark.asyncio
    async def test_rate_limit_affects_dispatched_sheets_across_jobs(self) -> None:
        """Rate limit on claude-code should WAITING all dispatched
        claude-code sheets regardless of which job they belong to."""
        baton = _make_baton()
        sheets_a = _make_sheets(2, instrument="claude-code")
        sheets_b = _make_sheets(2, instrument="claude-code")
        baton.register_job("a", sheets_a, {})
        baton.register_job("b", sheets_b, {})

        sheets_a[1].status = BatonSheetStatus.DISPATCHED
        sheets_b[1].status = BatonSheetStatus.DISPATCHED
        sheets_b[2].status = BatonSheetStatus.PENDING  # Not dispatched

        await baton.handle_event(
            RateLimitHit(instrument="claude-code", wait_seconds=60, job_id="a", sheet_num=1)
        )

        assert sheets_a[1].status == BatonSheetStatus.WAITING
        assert sheets_b[1].status == BatonSheetStatus.WAITING
        # PENDING sheet should NOT be affected
        assert sheets_b[2].status == BatonSheetStatus.PENDING

    @pytest.mark.asyncio
    async def test_rate_limit_doesnt_affect_other_instruments(self) -> None:
        """Rate limit on claude-code should not affect gemini-cli sheets."""
        baton = _make_baton()
        sheets_a = _make_sheets(1, instrument="claude-code")
        sheets_b = _make_sheets(1, instrument="gemini-cli")
        baton.register_job("a", sheets_a, {})
        baton.register_job("b", sheets_b, {})

        sheets_a[1].status = BatonSheetStatus.DISPATCHED
        sheets_b[1].status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            RateLimitHit(instrument="claude-code", wait_seconds=60, job_id="a", sheet_num=1)
        )

        assert sheets_a[1].status == BatonSheetStatus.WAITING
        assert sheets_b[1].status == BatonSheetStatus.DISPATCHED  # Unaffected

    @pytest.mark.asyncio
    async def test_rate_limit_expiry_resumes_across_jobs(self) -> None:
        """Rate limit expiry should move WAITING sheets back to PENDING
        across all jobs."""
        baton = _make_baton()
        sheets_a = _make_sheets(1, instrument="claude-code")
        sheets_b = _make_sheets(1, instrument="claude-code")
        baton.register_job("a", sheets_a, {})
        baton.register_job("b", sheets_b, {})

        sheets_a[1].status = BatonSheetStatus.WAITING
        sheets_b[1].status = BatonSheetStatus.WAITING

        await baton.handle_event(
            RateLimitExpired(instrument="claude-code")
        )

        assert sheets_a[1].status == BatonSheetStatus.PENDING
        assert sheets_b[1].status == BatonSheetStatus.PENDING


# =============================================================================
# Story 7: The edge between completion mode and total failure
# =============================================================================


class TestCompletionModeEdgeCases:
    """A sheet gets 60% validation pass rate — enters completion mode.
    It keeps getting 60% for 5 more attempts. Now the completion budget
    is exhausted. What happens? Does the exhaustion handler properly
    trigger? What if escalation is also enabled?"""

    @pytest.mark.asyncio
    async def test_completion_exhaustion_triggers_escalation(self) -> None:
        """When completion mode exhausts AND escalation is enabled,
        the sheet should enter FERMATA, not FAILED."""
        baton = _make_baton()
        sheets = _make_sheets(1, max_retries=3)
        baton.register_job(
            "j1", sheets, {},
            escalation_enabled=True,
        )

        # Exhaust completion budget with partial passes
        for i in range(1, 7):  # More than max_completion (5)
            sheets[1].status = BatonSheetStatus.DISPATCHED
            result = _attempt_result(
                "j1", 1,
                pass_rate=60.0,
                validations_total=5,
                attempt=i,
            )
            await baton.handle_event(result)

            if sheets[1].status in (
                BatonSheetStatus.FERMATA,
                BatonSheetStatus.FAILED,
            ):
                break

        # Should escalate, not fail
        assert sheets[1].status == BatonSheetStatus.FERMATA

    @pytest.mark.asyncio
    async def test_completion_exhaustion_fails_without_escalation(self) -> None:
        """When completion mode exhausts WITHOUT escalation,
        the sheet should FAIL."""
        baton = _make_baton()
        sheets = _make_sheets(1, max_retries=3)
        baton.register_job("j1", sheets, {})

        for i in range(1, 7):
            sheets[1].status = BatonSheetStatus.DISPATCHED
            result = _attempt_result(
                "j1", 1,
                pass_rate=60.0,
                validations_total=5,
                attempt=i,
            )
            await baton.handle_event(result)

            if sheets[1].status.is_terminal:
                break

        assert sheets[1].status == BatonSheetStatus.FAILED


# =============================================================================
# Story 8: Late-arriving events after terminal state
# =============================================================================


class TestLateArrivingEvents:
    """A sheet completes successfully. Then a stale SheetAttemptResult
    arrives from a previous retry that was still in flight. The terminal
    guard should reject it. But what about other event types?"""

    @pytest.mark.asyncio
    async def test_late_result_after_completion_ignored(self) -> None:
        """A SheetAttemptResult for a COMPLETED sheet is a no-op."""
        baton = _make_baton()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})

        sheets[1].status = BatonSheetStatus.COMPLETED

        result = _attempt_result("j1", 1, execution_success=False)
        await baton.handle_event(result)

        # Still completed — terminal guard held
        assert sheets[1].status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_late_crash_after_completion_ignored(self) -> None:
        """A ProcessExited for a COMPLETED sheet is a no-op."""
        baton = _make_baton()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})

        sheets[1].status = BatonSheetStatus.COMPLETED

        await baton.handle_event(
            ProcessExited(job_id="j1", sheet_num=1, pid=12345, exit_code=137)
        )

        assert sheets[1].status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_late_result_after_cancel_ignored(self) -> None:
        """A SheetAttemptResult for a CANCELLED sheet is a no-op."""
        baton = _make_baton()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})

        sheets[1].status = BatonSheetStatus.CANCELLED

        result = _attempt_result("j1", 1)
        await baton.handle_event(result)

        assert sheets[1].status == BatonSheetStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_events_for_unknown_job_harmless(self) -> None:
        """Events referencing a non-existent job should not crash."""
        baton = _make_baton()

        # No jobs registered — these should all be no-ops
        await baton.handle_event(
            _attempt_result("ghost-job", 1)
        )
        await baton.handle_event(
            ProcessExited(job_id="ghost-job", sheet_num=1, pid=99999, exit_code=1)
        )
        await baton.handle_event(
            PauseJob(job_id="ghost-job")
        )
        await baton.handle_event(
            ResumeJob(job_id="ghost-job")
        )
        await baton.handle_event(
            CancelJob(job_id="ghost-job")
        )
        # No crash = success


# =============================================================================
# Story 9: Dependency propagation in diamond topology
# =============================================================================


class TestDiamondDependencyPropagation:
    """Job with diamond dependency:
        1 → 2
        1 → 3
        2 → 4
        3 → 4
    Sheet 1 fails. Do sheets 2, 3, and 4 all get failed? What if
    sheet 2 was already completed when sheet 1 failed?"""

    @pytest.mark.asyncio
    async def test_diamond_failure_propagation(self) -> None:
        """Failure at root of diamond should propagate to all dependents."""
        baton = _make_baton()
        sheets = _make_sheets(4, max_retries=1)
        deps = {2: [1], 3: [1], 4: [2, 3]}
        baton.register_job("j1", sheets, deps)

        sheets[1].status = BatonSheetStatus.DISPATCHED
        # Sheet 1 fails (execution failure, 1 retry = exhausted)
        result = _attempt_result(
            "j1", 1, execution_success=False, attempt=1,
        )
        await baton.handle_event(result)

        # Sheet 1 should be FAILED (max_retries=1, already consumed)
        assert sheets[1].status == BatonSheetStatus.FAILED
        # All dependents should be FAILED
        assert sheets[2].status == BatonSheetStatus.FAILED
        assert sheets[3].status == BatonSheetStatus.FAILED
        assert sheets[4].status == BatonSheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_diamond_partial_completion_preserved(self) -> None:
        """If a dependent is already COMPLETED, failure propagation
        should not regress it."""
        baton = _make_baton()
        sheets = _make_sheets(4, max_retries=1)
        deps = {2: [1], 3: [1], 4: [2, 3]}
        baton.register_job("j1", sheets, deps)

        # Sheet 2 already completed before sheet 1 fails
        sheets[2].status = BatonSheetStatus.COMPLETED
        sheets[1].status = BatonSheetStatus.DISPATCHED

        result = _attempt_result(
            "j1", 1, execution_success=False, attempt=1,
        )
        await baton.handle_event(result)

        assert sheets[1].status == BatonSheetStatus.FAILED
        # Sheet 2 must stay COMPLETED (terminal guard)
        assert sheets[2].status == BatonSheetStatus.COMPLETED
        # Sheet 3 should be FAILED (depends on failed sheet 1)
        assert sheets[3].status == BatonSheetStatus.FAILED
        # Sheet 4 can't run because sheet 3 is failed
        assert sheets[4].status == BatonSheetStatus.FAILED
