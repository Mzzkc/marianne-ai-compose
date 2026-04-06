"""TDD tests for F-140: _handle_resume_job must re-check cost limits.

When a user resumes a job that has exceeded its cost limit, the resume
handler should re-check cost limits immediately after unpausing. Without
this, a cost-paused job can dispatch one or more sheets before the next
attempt result triggers a cost re-check.

This is the same class of bug as F-067 (escalation unpause overrides
cost-enforcement pause), now found in the user resume path.

Found by: Axiom, Movement 2 (backward-trace invariant analysis)
"""

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    PauseJob,
    ResumeJob,
    SheetAttemptResult,
)
from marianne.daemon.baton.state import SheetExecutionState


def _make_sheet(
    sheet_num: int, instrument: str = "claude-code"
) -> SheetExecutionState:
    return SheetExecutionState(
        sheet_num=sheet_num,
        instrument_name=instrument,
    )


class TestResumeJobCostLimitRecheck:
    """Verify _handle_resume_job re-checks cost limits after unpausing."""

    @pytest.mark.asyncio
    async def test_resume_repauses_when_cost_exceeded(self) -> None:
        """A cost-exceeded job must remain paused even after user resume.

        Scenario:
        1. Job registered with $10 cost limit
        2. Sheet succeeds, costing $15 → job paused by cost enforcement
        3. User resumes the job
        4. Expected: job re-pauses immediately (cost still exceeded)
        """
        baton = BatonCore()
        sheets = {1: _make_sheet(1), 2: _make_sheet(2)}
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 10.0)

        # Sheet 1 completes with cost exceeding the limit
        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            validations_total=1,
            cost_usd=15.0,
            duration_seconds=60.0,
        )
        await baton.handle_event(result)

        # Verify job is cost-paused
        job = baton._jobs["j1"]
        assert job.paused is True, "Job should be paused by cost enforcement"
        assert job.user_paused is False, "This is a cost pause, not user pause"

        # User resumes
        await baton.handle_event(ResumeJob(job_id="j1"))

        # The bug: without the fix, job.paused=False and sheet 2 can dispatch.
        # With the fix: _check_job_cost_limit re-pauses because cost ($15) > limit ($10).
        assert job.paused is True, (
            "Job must re-pause after resume because cost ($15) exceeds limit ($10). "
            "Without this, one dispatch cycle can bypass cost enforcement."
        )

    @pytest.mark.asyncio
    async def test_resume_succeeds_when_cost_under_limit(self) -> None:
        """A job under its cost limit should resume normally.

        This is the happy path — ensure the re-check doesn't prevent
        legitimate resumes.
        """
        baton = BatonCore()
        sheets = {1: _make_sheet(1), 2: _make_sheet(2)}
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 100.0)

        # Sheet 1 completes with cost under limit
        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            validations_total=1,
            cost_usd=5.0,
            duration_seconds=30.0,
        )
        await baton.handle_event(result)

        # User pauses then resumes
        await baton.handle_event(PauseJob(job_id="j1"))
        job = baton._jobs["j1"]
        assert job.paused is True

        await baton.handle_event(ResumeJob(job_id="j1"))
        assert job.paused is False, "Job should resume when cost is under limit"
        assert job.user_paused is False

    @pytest.mark.asyncio
    async def test_resume_works_without_cost_limit(self) -> None:
        """Jobs without cost limits should resume normally (no regression)."""
        baton = BatonCore()
        sheets = {1: _make_sheet(1)}
        baton.register_job("j1", sheets, {})

        # User pauses then resumes — no cost limit set
        await baton.handle_event(PauseJob(job_id="j1"))
        job = baton._jobs["j1"]
        assert job.paused is True

        await baton.handle_event(ResumeJob(job_id="j1"))
        assert job.paused is False
        assert job.user_paused is False

    @pytest.mark.asyncio
    async def test_resume_cost_check_with_user_pause_and_cost_pause(self) -> None:
        """Both user and cost enforcement paused — resume should still re-check.

        Scenario:
        1. Job exceeds cost limit → cost pause
        2. User also pauses → user_paused=True
        3. User resumes → user_paused=False, but cost should re-pause
        """
        baton = BatonCore()
        sheets = {1: _make_sheet(1), 2: _make_sheet(2)}
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 10.0)

        # Sheet completes over budget
        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            validations_total=1,
            cost_usd=20.0,
            duration_seconds=90.0,
        )
        await baton.handle_event(result)
        job = baton._jobs["j1"]
        assert job.paused is True  # cost pause

        # User also pauses
        await baton.handle_event(PauseJob(job_id="j1"))
        assert job.user_paused is True

        # User resumes
        await baton.handle_event(ResumeJob(job_id="j1"))
        assert job.user_paused is False
        assert job.paused is True, (
            "Cost enforcement should re-pause the job even after user resume"
        )
