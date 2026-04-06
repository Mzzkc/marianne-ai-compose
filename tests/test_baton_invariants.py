"""Invariant verification tests for the baton core.

These tests prove invariant violations and then verify fixes. Each test
traces a specific claim from the baton's design back to its foundation
and shows where the claim breaks.

Categories:
- Dependency failure propagation (zombie jobs)
- Escalation unpause correctness
- RateLimitHit status guard
- F-018 validation_pass_rate contract guard
- cancel_job observability

Found by: Axiom, Movement 1
Method: Backward tracing from outputs to inputs, invariant verification
"""

from __future__ import annotations

import asyncio

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState
from marianne.daemon.baton.dispatch import DispatchConfig, dispatch_ready
from marianne.daemon.baton.events import (
    CancelJob,
    EscalationNeeded,
    EscalationResolved,
    EscalationTimeout,
    PauseJob,
    RateLimitHit,
    ResumeJob,
    SheetAttemptResult,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_baton_with_chain(
    num_sheets: int = 5,
    instrument: str = "claude-code",
) -> BatonCore:
    """Create a baton with a linear dependency chain: 1 → 2 → 3 → ... → N."""
    baton = BatonCore()
    sheets = {
        i: SheetExecutionState(sheet_num=i, instrument_name=instrument)
        for i in range(1, num_sheets + 1)
    }
    # Linear chain: each sheet depends on the previous one
    deps = {i: [i - 1] for i in range(2, num_sheets + 1)}
    baton.register_job("test-job", sheets, deps)
    return baton


def _make_baton_with_fan_out(
    fan_count: int = 3,
    instrument: str = "claude-code",
) -> BatonCore:
    """Create a baton with a fan-out → join pattern.

    Sheet 1 (source) → sheets 2..N (fan-out) → sheet N+1 (join).
    """
    baton = BatonCore()
    total = fan_count + 2  # source + fan_count voices + join
    sheets = {
        i: SheetExecutionState(sheet_num=i, instrument_name=instrument)
        for i in range(1, total + 1)
    }
    # Fan-out: sheets 2..N depend on sheet 1
    deps: dict[int, list[int]] = {}
    for i in range(2, fan_count + 2):
        deps[i] = [1]
    # Join: last sheet depends on all fan-out sheets
    deps[total] = list(range(2, fan_count + 2))
    baton.register_job("test-job", sheets, deps)
    return baton


def _fail_sheet(baton: BatonCore, job_id: str, sheet_num: int) -> None:
    """Simulate a sheet failing after exhausting retries."""
    sheet = baton.get_sheet_state(job_id, sheet_num)
    assert sheet is not None
    sheet.status = BatonSheetStatus.DISPATCHED  # Must be dispatched first
    # Exhaust retries
    for attempt in range(sheet.max_retries):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                baton.handle_event(
                    SheetAttemptResult(
                        job_id=job_id,
                        sheet_num=sheet_num,
                        instrument_name=sheet.instrument_name,
                        attempt=attempt + 1,
                        execution_success=False,
                        error_classification="TRANSIENT",
                    )
                )
            )
        finally:
            loop.close()


async def _dispatch_noop(
    job_id: str, sheet_num: int, state: SheetExecutionState
) -> None:
    """No-op dispatch callback for testing."""
    state.status = BatonSheetStatus.DISPATCHED


# =============================================================================
# 1. Dependency Failure Propagation
# =============================================================================


class TestDependencyFailurePropagation:
    """When a sheet fails, downstream dependent sheets must reach terminal
    state. Without this, `is_job_complete` never returns True — a zombie job.

    INVARIANT: If all executable paths lead to terminal states, then
    `is_job_complete` must eventually return True for any event sequence
    that terminates all independent work.
    """

    async def test_failed_dependency_blocks_downstream_ready_resolution(
        self,
    ) -> None:
        """Prove: a failed sheet's dependents never become ready."""
        baton = _make_baton_with_chain(3)
        # Complete sheet 1
        sheet1 = baton.get_sheet_state("test-job", 1)
        assert sheet1 is not None
        sheet1.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            SheetAttemptResult(
                job_id="test-job",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )
        assert sheet1.status == "completed"

        # Fail sheet 2 (exhaust retries)
        sheet2 = baton.get_sheet_state("test-job", 2)
        assert sheet2 is not None
        sheet2.status = BatonSheetStatus.DISPATCHED
        for attempt in range(sheet2.max_retries):
            await baton.handle_event(
                SheetAttemptResult(
                    job_id="test-job",
                    sheet_num=2,
                    instrument_name="claude-code",
                    attempt=attempt + 1,
                    execution_success=False,
                    error_classification="TRANSIENT",
                )
            )
        assert sheet2.status == "failed"

        # Sheet 3 depends on sheet 2 which failed.
        # Sheet 3 should NOT be in the ready list.
        ready = baton.get_ready_sheets("test-job")
        ready_nums = [s.sheet_num for s in ready]
        assert 3 not in ready_nums, (
            "Sheet 3 should not be ready when its dependency (sheet 2) failed"
        )

    async def test_failed_dependency_propagates_to_dependents(self) -> None:
        """After fix: failed dependency causes dependents to be marked failed."""
        baton = _make_baton_with_chain(5)

        # Complete sheet 1
        sheet1 = baton.get_sheet_state("test-job", 1)
        assert sheet1 is not None
        sheet1.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            SheetAttemptResult(
                job_id="test-job",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )

        # Fail sheet 2
        sheet2 = baton.get_sheet_state("test-job", 2)
        assert sheet2 is not None
        sheet2.status = BatonSheetStatus.DISPATCHED
        for attempt in range(sheet2.max_retries):
            await baton.handle_event(
                SheetAttemptResult(
                    job_id="test-job",
                    sheet_num=2,
                    instrument_name="claude-code",
                    attempt=attempt + 1,
                    execution_success=False,
                    error_classification="TRANSIENT",
                )
            )
        assert sheet2.status == "failed"

        # Sheets 3, 4, 5 all transitively depend on sheet 2.
        # After propagation, they should all be failed (or skipped).
        for num in (3, 4, 5):
            sheet = baton.get_sheet_state("test-job", num)
            assert sheet is not None
            assert sheet.status in ("failed", "skipped"), (
                f"Sheet {num} should be terminal after dependency failure, "
                f"got status={sheet.status}"
            )

        # The job should now be complete
        assert baton.is_job_complete("test-job"), (
            "Job should be complete after all sheets reach terminal state"
        )

    async def test_fan_out_single_failure_propagates_to_join(self) -> None:
        """Fan-out: if one voice fails, the join sheet should fail.

        Pattern: 1 → [2,3,4] → 5
        If sheet 3 fails, sheet 5 (which depends on 2,3,4) can never
        be satisfied.
        """
        baton = _make_baton_with_fan_out(fan_count=3)
        # Complete sheet 1
        sheet1 = baton.get_sheet_state("test-job", 1)
        assert sheet1 is not None
        sheet1.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            SheetAttemptResult(
                job_id="test-job",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )

        # Complete sheets 2 and 4
        for num in (2, 4):
            sheet = baton.get_sheet_state("test-job", num)
            assert sheet is not None
            sheet.status = BatonSheetStatus.DISPATCHED
            await baton.handle_event(
                SheetAttemptResult(
                    job_id="test-job",
                    sheet_num=num,
                    instrument_name="claude-code",
                    attempt=1,
                    execution_success=True,
                    validation_pass_rate=100.0,
                )
            )

        # Fail sheet 3
        sheet3 = baton.get_sheet_state("test-job", 3)
        assert sheet3 is not None
        sheet3.status = BatonSheetStatus.DISPATCHED
        for attempt in range(sheet3.max_retries):
            await baton.handle_event(
                SheetAttemptResult(
                    job_id="test-job",
                    sheet_num=3,
                    instrument_name="claude-code",
                    attempt=attempt + 1,
                    execution_success=False,
                    error_classification="TRANSIENT",
                )
            )
        assert sheet3.status == "failed"

        # Sheet 5 depends on [2,3,4]. Sheet 3 failed.
        # Sheet 5 should be failed (dependency failure propagation)
        sheet5 = baton.get_sheet_state("test-job", 5)
        assert sheet5 is not None
        assert sheet5.status in ("failed", "skipped"), (
            f"Join sheet should fail when a dependency fails, got {sheet5.status}"
        )

        assert baton.is_job_complete("test-job")

    async def test_failure_propagation_does_not_affect_completed_sheets(
        self,
    ) -> None:
        """Completed sheets are never changed by dependency failure propagation."""
        baton = _make_baton_with_chain(3)

        # Complete sheet 1
        sheet1 = baton.get_sheet_state("test-job", 1)
        assert sheet1 is not None
        sheet1.status = BatonSheetStatus.COMPLETED

        # Complete sheet 2
        sheet2 = baton.get_sheet_state("test-job", 2)
        assert sheet2 is not None
        sheet2.status = BatonSheetStatus.COMPLETED

        # Sheet 3 has no dependency that failed, so nothing to propagate
        # But even if we manually set sheet 1 to failed, sheet 2 should
        # remain completed
        sheet1.status = BatonSheetStatus.FAILED
        # Trigger propagation manually if a method exists
        if hasattr(baton, "_propagate_failure_to_dependents"):
            baton._propagate_failure_to_dependents("test-job", 1)

        assert sheet2.status == "completed", (
            "Completed sheets must never be changed by failure propagation"
        )


# =============================================================================
# 2. Escalation Unpause Correctness
# =============================================================================


class TestEscalationUnpauseCorrectness:
    """Escalation resolution should only unpause the escalation-related pause,
    not user-initiated pauses.

    INVARIANT: A user pause (PauseJob) should only be cleared by a user
    resume (ResumeJob), never by an internal escalation resolution.
    """

    async def test_escalation_resolution_unpauses_escalation_paused_job(
        self,
    ) -> None:
        """Normal case: escalation pauses job, resolution unpauses it."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})

        # Escalation pauses the job
        await baton.handle_event(
            EscalationNeeded(
                job_id="test-job", sheet_num=1, reason="test"
            )
        )
        assert baton.is_job_paused("test-job")

        # Resolution should unpause
        await baton.handle_event(
            EscalationResolved(
                job_id="test-job", sheet_num=1, decision="retry"
            )
        )
        assert not baton.is_job_paused("test-job")

    async def test_escalation_resolution_does_not_unpause_user_paused_job(
        self,
    ) -> None:
        """BUG: If user pauses AND escalation pauses, resolution should not
        unpause the user's pause.

        Sequence:
        1. User runs `mozart pause test-job`
        2. Sheet triggers escalation (sets fermata)
        3. Composer resolves escalation
        4. Job should STILL be paused (user's pause is independent)
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})

        # User pauses the job
        await baton.handle_event(PauseJob(job_id="test-job"))
        assert baton.is_job_paused("test-job")

        # Escalation on sheet 1
        await baton.handle_event(
            EscalationNeeded(
                job_id="test-job", sheet_num=1, reason="healing needed"
            )
        )
        assert baton.is_job_paused("test-job")

        # Composer resolves escalation
        await baton.handle_event(
            EscalationResolved(
                job_id="test-job", sheet_num=1, decision="retry"
            )
        )

        # Job should still be paused (user's pause is separate)
        assert baton.is_job_paused("test-job"), (
            "Escalation resolution should not override a user-initiated pause"
        )

    async def test_escalation_timeout_does_not_unpause_user_paused_job(
        self,
    ) -> None:
        """Same bug with timeout: escalation timeout should not unpause user pause."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})

        # User pauses
        await baton.handle_event(PauseJob(job_id="test-job"))
        assert baton.is_job_paused("test-job")

        # Escalation
        await baton.handle_event(
            EscalationNeeded(
                job_id="test-job", sheet_num=1, reason="test"
            )
        )

        # Timeout fires
        await baton.handle_event(
            EscalationTimeout(job_id="test-job", sheet_num=1)
        )

        # Job should still be paused
        assert baton.is_job_paused("test-job"), (
            "Escalation timeout should not override a user-initiated pause"
        )


# =============================================================================
# 3. RateLimitHit Status Guard
# =============================================================================


class TestRateLimitHitStatusGuard:
    """The RateLimitHit handler should only affect dispatched/running sheets.

    INVARIANT: A sheet's status should only be set to 'waiting' if it
    was previously in a state where rate-limiting makes sense (dispatched
    or running). A pending/ready sheet hasn't been sent to an instrument
    yet and should not transition to waiting.
    """

    async def test_rate_limit_hit_dispatched_sheet_becomes_waiting(
        self,
    ) -> None:
        """Normal case: dispatched sheet becomes waiting on rate limit."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        sheets[1].status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            RateLimitHit(
                instrument="claude-code",
                wait_seconds=30.0,
                job_id="test-job",
                sheet_num=1,
            )
        )
        assert sheets[1].status == "waiting"

    async def test_rate_limit_hit_pending_sheet_stays_pending(self) -> None:
        """BUG: A pending sheet should not transition to waiting on RateLimitHit.

        If the RateLimitHit arrives for a sheet that hasn't been dispatched
        yet (e.g., stale event, duplicate event), marking it as 'waiting'
        would remove it from the dispatch pipeline incorrectly.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        assert sheets[1].status == "pending"

        await baton.handle_event(
            RateLimitHit(
                instrument="claude-code",
                wait_seconds=30.0,
                job_id="test-job",
                sheet_num=1,
            )
        )
        # After fix: pending sheet should remain pending
        assert sheets[1].status == "pending", (
            "Pending sheet should not become waiting on rate limit hit"
        )

    async def test_rate_limit_hit_completed_sheet_stays_completed(
        self,
    ) -> None:
        """Completed sheets must never regress to waiting."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        sheets[1].status = BatonSheetStatus.COMPLETED

        await baton.handle_event(
            RateLimitHit(
                instrument="claude-code",
                wait_seconds=30.0,
                job_id="test-job",
                sheet_num=1,
            )
        )
        assert sheets[1].status == "completed", (
            "Completed sheet must not regress to waiting"
        )


# =============================================================================
# 4. F-018 Validation Pass Rate Guard
# =============================================================================


class TestF018ValidationPassRateGuard:
    """The baton must treat execution_success=True with validations_total=0
    as 100% pass rate, not as 0% (the default).

    INVARIANT: If a musician reports success and there are no validation
    rules, the sheet should complete — not retry.
    """

    async def test_no_validations_with_success_completes(self) -> None:
        """A sheet with no validations that succeeds should complete."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        sheets[1].status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            SheetAttemptResult(
                job_id="test-job",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_total=0,
                validations_passed=0,
                validation_pass_rate=100.0,  # Correct: musician sets this
            )
        )
        assert sheets[1].status == "completed"

    async def test_no_validations_with_default_rate_still_completes(
        self,
    ) -> None:
        """F-018: Even if musician forgets to set validation_pass_rate,
        the baton should recognize validations_total=0 as success.

        The current code treats validation_pass_rate=0.0 as failure even
        when validations_total=0. This causes unnecessary retries.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        sheets[1].status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            SheetAttemptResult(
                job_id="test-job",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_total=0,
                validations_passed=0,
                validation_pass_rate=0.0,  # F-018: musician forgot to set it
            )
        )
        assert sheets[1].status == "completed", (
            "Sheet with no validations and execution_success=True should "
            "complete even when validation_pass_rate defaults to 0.0"
        )

    async def test_partial_validations_do_not_auto_complete(self) -> None:
        """When validations exist and only some pass, the sheet should NOT
        complete — this is a partial pass that needs retry."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        sheets[1].status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            SheetAttemptResult(
                job_id="test-job",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_total=3,
                validations_passed=2,
                validation_pass_rate=66.7,
            )
        )
        # Should NOT be completed — only 66.7% passed.
        # With the new design, partial passes use completion mode
        # (not retry budget). The sheet goes back to PENDING for
        # re-dispatch with "finish your work" context.
        assert sheets[1].status != "completed"
        assert sheets[1].normal_attempts == 0  # successes don't consume retries
        assert sheets[1].completion_attempts == 1  # tracked as completion attempt

    async def test_all_validations_pass_completes(self) -> None:
        """When all validations pass, the sheet completes."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        sheets[1].status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            SheetAttemptResult(
                job_id="test-job",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_total=5,
                validations_passed=5,
                validation_pass_rate=100.0,
            )
        )
        assert sheets[1].status == "completed"


# =============================================================================
# 5. cancel_job Observability
# =============================================================================


class TestCancelJobObservability:
    """CancelJob should mark sheets as cancelled before deregistering.
    The sheet statuses should be observable (e.g., for CheckpointState
    persistence) before the job is removed.

    INVARIANT: Cancellation must update state before deletion. Order
    matters — observers that watch state transitions need to see the
    cancelled status.
    """

    async def test_cancel_job_deregisters(self) -> None:
        """After cancel, the job is no longer registered."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})

        await baton.handle_event(CancelJob(job_id="test-job"))
        assert baton.job_count == 0

    async def test_cancel_preserves_already_completed_sheets(self) -> None:
        """Completed sheets should not be changed to cancelled."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        sheets[1].status = BatonSheetStatus.COMPLETED
        baton.register_job("test-job", sheets, {})

        # Capture the status BEFORE deregister
        # (The current code does deregister immediately after marking)
        # We verify the logic is correct by checking the sheet objects
        # we hold a reference to
        await baton.handle_event(CancelJob(job_id="test-job"))

        # sheets[1] was completed — should stay completed
        assert sheets[1].status == "completed"
        # sheets[2] was pending — should be cancelled
        assert sheets[2].status == "cancelled"


# =============================================================================
# 6. Attempt Result for Already-Terminal Sheet
# =============================================================================


class TestAttemptResultForTerminalSheet:
    """The baton must not regress terminal sheets based on late-arriving events.

    INVARIANT: Once a sheet reaches a terminal status (completed, failed,
    skipped, cancelled), no event should transition it away from that status.
    """

    async def test_attempt_result_for_completed_sheet_is_noop(self) -> None:
        """A late SheetAttemptResult for a completed sheet should not change it."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        sheets[1].status = BatonSheetStatus.COMPLETED

        await baton.handle_event(
            SheetAttemptResult(
                job_id="test-job",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=2,
                execution_success=False,
            )
        )
        # The current code DOES process this — it appends to attempt_results
        # and potentially changes status. After fix, it should be a noop.
        assert sheets[1].status == "completed", (
            "Completed sheet must not regress on late attempt result"
        )

    async def test_attempt_result_for_failed_sheet_is_noop(self) -> None:
        """A late SheetAttemptResult for a failed sheet should not change it."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("test-job", sheets, {})
        sheets[1].status = BatonSheetStatus.FAILED

        await baton.handle_event(
            SheetAttemptResult(
                job_id="test-job",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )
        assert sheets[1].status == "failed", (
            "Failed sheet must not be resurrected by late success result"
        )
