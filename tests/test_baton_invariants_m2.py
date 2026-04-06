"""Movement 2 invariant verification tests for the baton core.

These tests prove three invariant violations found by backward-tracing
the M2 retry state machine, completion mode, and escalation changes.

Bug 1 (F-065): Infinite retry loop when execution succeeds but ALL
validations fail (pass_rate=0, validations_total>0). record_attempt()
only increments normal_attempts for execution failures, so the retry
budget is never consumed.

Bug 2 (F-066): Escalation unpause ignores other FERMATA sheets. When
multiple sheets are in FERMATA and one escalation is resolved, the
handler unconditionally unpauses the job — even though other sheets
are still awaiting escalation decisions.

Bug 3 (F-067): Escalation unpause overrides cost-enforcement pause.
_check_job_cost_limit sets job.paused=True, but escalation resolution
sets job.paused=False without re-checking cost limits. A resolved
escalation can silently lift a cost-enforcement pause.

Found by: Axiom, Movement 2
Method: Backward tracing from _handle_attempt_result, _handle_escalation_resolved,
        _handle_escalation_timeout through to state transitions and pause model.
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState
from marianne.daemon.baton.events import (
    EscalationResolved,
    EscalationTimeout,
    SheetAttemptResult,
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


def _success_result(
    job_id: str,
    sheet_num: int,
    pass_rate: float = 100.0,
    validations_total: int = 0,
    instrument: str = "claude-code",
    attempt: int = 1,
) -> SheetAttemptResult:
    """Create a successful SheetAttemptResult with given validation results."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name=instrument,
        attempt=attempt,
        execution_success=True,
        validation_pass_rate=pass_rate,
        validations_total=validations_total,
        validations_passed=int(validations_total * pass_rate / 100.0),
        duration_seconds=10.0,
        cost_usd=0.50,
    )


# =============================================================================
# Bug 1: Infinite retry on execution_success + 0% validation pass rate
# (F-065)
# =============================================================================


class TestZeroPassRateRetryBudget:
    """Prove that execution_success=True with pass_rate=0 consumes retry budget.

    The scenario: an agent runs successfully (exit code 0) but ALL validation
    checks fail. The baton should treat this as a failure for retry budget
    purposes — the agent did work, but the work was completely wrong.
    """

    @pytest.mark.asyncio
    async def test_zero_pass_rate_consumes_retry_budget(self) -> None:
        """A sheet with execution_success=True, pass_rate=0, validations_total>0
        must increment normal_attempts so the retry budget is consumed."""
        baton = _make_baton()
        sheets = _make_sheets(1, max_retries=3)
        baton.register_job("j1", sheets, {})

        # Dispatch the sheet (simulate)
        sheets[1].status = BatonSheetStatus.DISPATCHED

        # Report: execution succeeded but ALL validations failed
        result = _success_result(
            "j1", 1, pass_rate=0.0, validations_total=5
        )
        await baton.handle_event(result)

        # normal_attempts MUST have been incremented
        assert sheets[1].normal_attempts >= 1, (
            "execution_success + 0% validation should consume retry budget"
        )

    @pytest.mark.asyncio
    async def test_zero_pass_rate_eventually_exhausts_retries(self) -> None:
        """Repeated execution_success + 0% validation should exhaust retries,
        not loop forever."""
        baton = _make_baton()
        sheets = _make_sheets(1, max_retries=3)
        baton.register_job("j1", sheets, {})

        last_attempt = 0
        for attempt_num in range(1, 10):  # Way more than max_retries
            last_attempt = attempt_num
            sheets[1].status = BatonSheetStatus.DISPATCHED
            result = _success_result(
                "j1", 1, pass_rate=0.0, validations_total=5,
                attempt=attempt_num,
            )
            await baton.handle_event(result)

            # If the sheet reached a terminal state, we're done
            if sheets[1].status.is_terminal:
                break

        # Sheet MUST have reached a terminal state (FAILED or escalated)
        # It must NOT still be retrying after max_retries+1 failures
        assert sheets[1].status.is_terminal or sheets[1].status == BatonSheetStatus.FERMATA, (
            f"Expected terminal/fermata after max_retries, "
            f"got {sheets[1].status.value} after {last_attempt} attempts"
        )

    @pytest.mark.asyncio
    async def test_zero_pass_rate_fails_after_max_retries(self) -> None:
        """With no healing or escalation, max_retries=3 means 3 attempts then FAILED."""
        baton = _make_baton()
        sheets = _make_sheets(1, max_retries=3)
        baton.register_job("j1", sheets, {})

        for i in range(1, 5):
            if sheets[1].status.is_terminal:
                break
            sheets[1].status = BatonSheetStatus.DISPATCHED
            result = _success_result(
                "j1", 1, pass_rate=0.0, validations_total=5, attempt=i,
            )
            await baton.handle_event(result)

        assert sheets[1].status == BatonSheetStatus.FAILED
        # Should take exactly max_retries (3) failed attempts
        assert sheets[1].normal_attempts == 3

    @pytest.mark.asyncio
    async def test_partial_pass_rate_uses_completion_not_retry_budget(self) -> None:
        """Partial pass rate (>0%, <100%) uses completion budget, not retry budget.
        Contrast with 0% which uses retry budget."""
        baton = _make_baton()
        sheets = _make_sheets(1, max_retries=3)
        baton.register_job("j1", sheets, {})

        sheets[1].status = BatonSheetStatus.DISPATCHED
        result = _success_result(
            "j1", 1, pass_rate=50.0, validations_total=4,
        )
        await baton.handle_event(result)

        # Partial pass uses completion budget, not retry budget
        assert sheets[1].completion_attempts > 0
        # Status should be PENDING (ready for completion mode redispatch)
        assert sheets[1].status == BatonSheetStatus.PENDING


# =============================================================================
# Bug 2: Escalation unpause ignores other FERMATA sheets
# (F-066)
# =============================================================================


class TestEscalationMultipleFermata:
    """Prove that resolving one escalation doesn't unpause a job with other
    sheets still in FERMATA."""

    @pytest.mark.asyncio
    async def test_resolve_one_keeps_job_paused_when_other_fermata(self) -> None:
        """Two sheets in FERMATA. Resolve one. Job must stay paused because
        the other is still awaiting escalation."""
        baton = _make_baton()
        sheets = _make_sheets(3)
        baton.register_job(
            "j1", sheets, {},
            escalation_enabled=True,
        )

        # Put sheets 1 and 2 into FERMATA (simulate exhaustion → escalation)
        sheets[1].status = BatonSheetStatus.FERMATA
        sheets[2].status = BatonSheetStatus.FERMATA
        # Job is paused due to escalation
        job = baton._jobs["j1"]
        job.paused = True

        # Resolve sheet 1's escalation
        await baton.handle_event(
            EscalationResolved(
                job_id="j1", sheet_num=1, decision="retry",
            )
        )

        # Sheet 1 should be PENDING (retry decision)
        assert sheets[1].status == BatonSheetStatus.PENDING
        # Sheet 2 is still in FERMATA
        assert sheets[2].status == BatonSheetStatus.FERMATA
        # Job MUST stay paused — sheet 2 still needs escalation resolution
        assert job.paused is True, (
            "Job should stay paused while other sheets are still in FERMATA"
        )

    @pytest.mark.asyncio
    async def test_resolve_last_fermata_unpauses_job(self) -> None:
        """When the last FERMATA sheet is resolved, job should unpause."""
        baton = _make_baton()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {})

        # Only sheet 1 in FERMATA
        sheets[1].status = BatonSheetStatus.FERMATA
        sheets[2].status = BatonSheetStatus.COMPLETED
        job = baton._jobs["j1"]
        job.paused = True

        await baton.handle_event(
            EscalationResolved(
                job_id="j1", sheet_num=1, decision="skip",
            )
        )

        assert sheets[1].status == BatonSheetStatus.SKIPPED
        # No more FERMATA sheets → job should be unpaused
        assert job.paused is False

    @pytest.mark.asyncio
    async def test_timeout_one_keeps_job_paused_when_other_fermata(self) -> None:
        """Escalation timeout for one sheet shouldn't unpause if another is
        still in FERMATA."""
        baton = _make_baton()
        sheets = _make_sheets(3)
        baton.register_job("j1", sheets, {})

        sheets[1].status = BatonSheetStatus.FERMATA
        sheets[2].status = BatonSheetStatus.FERMATA
        job = baton._jobs["j1"]
        job.paused = True

        await baton.handle_event(
            EscalationTimeout(job_id="j1", sheet_num=1)
        )

        # Sheet 1 should be FAILED (timeout default)
        assert sheets[1].status == BatonSheetStatus.FAILED
        # Sheet 2 still in FERMATA
        assert sheets[2].status == BatonSheetStatus.FERMATA
        # Job MUST stay paused
        assert job.paused is True

    @pytest.mark.asyncio
    async def test_user_pause_preserved_when_all_fermata_resolved(self) -> None:
        """If user explicitly paused AND escalation occurred, resolving
        all escalations should NOT unpause — user pause takes priority."""
        baton = _make_baton()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})

        sheets[1].status = BatonSheetStatus.FERMATA
        job = baton._jobs["j1"]
        job.paused = True
        job.user_paused = True

        await baton.handle_event(
            EscalationResolved(
                job_id="j1", sheet_num=1, decision="retry",
            )
        )

        # User pause must be preserved
        assert job.paused is True
        assert job.user_paused is True


# =============================================================================
# Bug 3: Escalation unpause overrides cost-enforcement pause
# (F-067)
# =============================================================================


class TestEscalationCostPauseInteraction:
    """Prove that escalation resolution doesn't lift a cost-enforcement pause."""

    @pytest.mark.asyncio
    async def test_escalation_resolve_preserves_cost_pause(self) -> None:
        """Job paused by cost enforcement. Escalation resolved. Job must
        stay paused because cost limit is still exceeded."""
        baton = _make_baton()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 1.0)  # $1 limit

        # Sheet 1 in FERMATA with costs that exceed the limit
        sheets[1].status = BatonSheetStatus.FERMATA
        sheets[1].total_cost_usd = 1.50  # Over limit
        sheets[2].status = BatonSheetStatus.PENDING

        job = baton._jobs["j1"]
        job.paused = True  # Paused by both escalation and cost

        # Resolve the escalation
        await baton.handle_event(
            EscalationResolved(
                job_id="j1", sheet_num=1, decision="skip",
            )
        )

        assert sheets[1].status == BatonSheetStatus.SKIPPED
        # Job must stay paused — cost limit still exceeded
        assert job.paused is True, (
            "Cost enforcement pause must survive escalation resolution"
        )

    @pytest.mark.asyncio
    async def test_escalation_timeout_preserves_cost_pause(self) -> None:
        """Same as above but with escalation timeout instead of resolve."""
        baton = _make_baton()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 1.0)

        sheets[1].status = BatonSheetStatus.FERMATA
        sheets[1].total_cost_usd = 1.50
        sheets[2].status = BatonSheetStatus.PENDING
        job = baton._jobs["j1"]
        job.paused = True

        await baton.handle_event(
            EscalationTimeout(job_id="j1", sheet_num=1)
        )

        assert sheets[1].status == BatonSheetStatus.FAILED
        assert job.paused is True, (
            "Cost enforcement pause must survive escalation timeout"
        )
