"""Tests for the baton's complete retry state machine — step 23.

These tests validate the timer-integrated retry scheduling, exponential
backoff calculation, escalation/healing paths after exhaustion, and
per-sheet cost enforcement. Written TDD-style: red first, then green.

The retry state machine is the conductor's decision engine. It determines:
- WHEN to retry (backoff timing via timer wheel)
- WHETHER to retry (budget check, cost check)
- WHAT to do when retries exhaust (escalate, heal, or fail)
- HOW MUCH is too much (per-sheet and per-job cost limits)

@pytest.mark.unit
"""

from __future__ import annotations

import asyncio

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    ProcessExited,
    RetryDue,
    SheetAttemptResult,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    SheetExecutionState,
)
from marianne.daemon.baton.timer import TimerWheel


# ============================================================================
# Helpers
# ============================================================================


def _make_baton(*, with_timer: bool = True) -> tuple[BatonCore, TimerWheel | None]:
    """Create a BatonCore with optional timer wheel."""
    inbox: asyncio.Queue[object] = asyncio.Queue()
    timer: TimerWheel | None = None
    if with_timer:
        timer = TimerWheel(inbox)
        baton = BatonCore(timer=timer)
    else:
        baton = BatonCore()
    return baton, timer


def _register_simple_job(
    baton: BatonCore,
    job_id: str = "j1",
    max_retries: int = 3,
    max_completion: int = 5,
    *,
    escalation_enabled: bool = False,
    self_healing_enabled: bool = False,
    sheet_cost_limit: float | None = None,
) -> SheetExecutionState:
    """Register a single-sheet job and return the sheet state."""
    sheet = SheetExecutionState(
        sheet_num=1,
        instrument_name="claude-code",
        max_retries=max_retries,
        max_completion=max_completion,
    )
    baton.register_job(
        job_id,
        {1: sheet},
        {},
        escalation_enabled=escalation_enabled,
        self_healing_enabled=self_healing_enabled,
    )
    if sheet_cost_limit is not None:
        baton.set_sheet_cost_limit(job_id, 1, sheet_cost_limit)
    return sheet


def _fail_event(
    job_id: str = "j1",
    sheet_num: int = 1,
    attempt: int = 1,
    error_classification: str = "EXECUTION_ERROR",
    cost_usd: float = 0.0,
) -> SheetAttemptResult:
    """Create a failed attempt result."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name="claude-code",
        attempt=attempt,
        execution_success=False,
        error_classification=error_classification,
        cost_usd=cost_usd,
    )


# ============================================================================
# Timer-Integrated Retry Scheduling
# ============================================================================


class TestRetryTimerIntegration:
    """Retry scheduling creates timer events with calculated backoff."""

    async def test_retry_schedules_timer_event(self) -> None:
        """When a retry is decided, a RetryDue timer is scheduled."""
        baton, timer = _make_baton(with_timer=True)
        assert timer is not None
        _register_simple_job(baton, max_retries=3)

        await baton.handle_event(_fail_event(attempt=1))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED
        assert timer.pending_count == 1

    async def test_retry_without_timer_still_works(self) -> None:
        """Without a timer wheel, RETRY_SCHEDULED is set but no timer created."""
        baton, timer = _make_baton(with_timer=False)
        assert timer is None
        _register_simple_job(baton, max_retries=3)

        await baton.handle_event(_fail_event(attempt=1))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED

    async def test_retry_timer_fires_retry_due(self) -> None:
        """The scheduled timer fires a RetryDue event for the correct sheet."""
        baton, timer = _make_baton(with_timer=True)
        assert timer is not None
        _register_simple_job(baton, max_retries=3)

        await baton.handle_event(_fail_event(attempt=1))

        # Check the timer was scheduled with a RetryDue event
        snapshot = timer.snapshot()
        assert len(snapshot) == 1
        _, event = snapshot[0]
        assert isinstance(event, RetryDue)
        assert event.job_id == "j1"
        assert event.sheet_num == 1

    async def test_retry_due_moves_to_pending(self) -> None:
        """RetryDue event transitions RETRY_SCHEDULED → PENDING."""
        baton, _ = _make_baton(with_timer=True)
        _register_simple_job(baton, max_retries=3)

        # Fail once → RETRY_SCHEDULED
        await baton.handle_event(_fail_event(attempt=1))
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED

        # Fire retry due → PENDING
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))
        assert state.status == BatonSheetStatus.PENDING

    async def test_multiple_retries_schedule_multiple_timers(self) -> None:
        """Each retry schedules a new timer; previous timers are consumed."""
        baton, timer = _make_baton(with_timer=True)
        assert timer is not None
        _register_simple_job(baton, max_retries=5)

        # Fail → RETRY_SCHEDULED (timer 1)
        await baton.handle_event(_fail_event(attempt=1))
        assert timer.pending_count == 1

        # Move back to pending (simulating timer fire)
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))
        # Timer was consumed from the wheel's perspective once fired,
        # but snapshot still shows it until drained. Just verify the
        # sheet is PENDING now.
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.PENDING

        # Fail again → RETRY_SCHEDULED (timer 2)
        await baton.handle_event(_fail_event(attempt=2))
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED


# ============================================================================
# Backoff Calculation
# ============================================================================


class TestBackoffCalculation:
    """Exponential backoff with jitter and clamping."""

    def test_backoff_increases_exponentially(self) -> None:
        """Delay doubles (by default) with each attempt."""
        baton, _ = _make_baton()
        # base=10, exponential_base=2: attempt 0→10, 1→20, 2→40
        delay0 = baton.calculate_retry_delay(attempt=0)
        delay1 = baton.calculate_retry_delay(attempt=1)
        delay2 = baton.calculate_retry_delay(attempt=2)

        # Without jitter, exact exponential
        assert delay0 == pytest.approx(10.0)
        assert delay1 == pytest.approx(20.0)
        assert delay2 == pytest.approx(40.0)

    def test_backoff_clamped_to_max(self) -> None:
        """Delay never exceeds max_delay_seconds."""
        baton, _ = _make_baton()
        # Very high attempt should still be clamped
        delay = baton.calculate_retry_delay(attempt=100)
        assert delay <= baton._max_retry_delay

    def test_backoff_with_custom_params(self) -> None:
        """Custom base delay and exponential base are respected."""
        baton, _ = _make_baton()
        baton._base_retry_delay = 5.0
        baton._retry_exponential_base = 3.0
        baton._max_retry_delay = 1000.0

        delay = baton.calculate_retry_delay(attempt=2)
        assert delay == pytest.approx(5.0 * (3.0 ** 2))  # 45.0

    def test_backoff_attempt_zero_is_base_delay(self) -> None:
        """First retry (attempt=0) uses exactly the base delay."""
        baton, _ = _make_baton()
        delay = baton.calculate_retry_delay(attempt=0)
        assert delay == pytest.approx(baton._base_retry_delay)


# ============================================================================
# Escalation Path After Retry Exhaustion
# ============================================================================


class TestEscalationAfterExhaustion:
    """When retries exhaust and escalation is enabled, enter FERMATA."""

    async def test_exhaustion_with_escalation_enters_fermata(self) -> None:
        """Retry exhaustion + escalation_enabled → FERMATA, not FAILED."""
        baton, _ = _make_baton()
        _register_simple_job(baton, max_retries=1, escalation_enabled=True)

        # One failure exhausts retry budget
        await baton.handle_event(_fail_event(attempt=1))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.FERMATA

    async def test_exhaustion_without_escalation_fails(self) -> None:
        """Retry exhaustion without escalation → FAILED (existing behavior)."""
        baton, _ = _make_baton()
        _register_simple_job(baton, max_retries=1, escalation_enabled=False)

        await baton.handle_event(_fail_event(attempt=1))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.FAILED

    async def test_escalation_pauses_job(self) -> None:
        """Entering fermata also pauses the job."""
        baton, _ = _make_baton()
        _register_simple_job(baton, max_retries=1, escalation_enabled=True)

        await baton.handle_event(_fail_event(attempt=1))

        assert baton.is_job_paused("j1")

    async def test_escalation_does_not_propagate_failure(self) -> None:
        """Fermata is NOT terminal — dependents are NOT failed."""
        baton, _ = _make_baton()
        sheet1 = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=1
        )
        sheet2 = SheetExecutionState(
            sheet_num=2, instrument_name="claude-code", max_retries=3
        )
        baton.register_job(
            "j1",
            {1: sheet1, 2: sheet2},
            {2: [1]},  # sheet 2 depends on sheet 1
            escalation_enabled=True,
        )

        # Exhaust sheet 1 → fermata (not failed)
        await baton.handle_event(_fail_event(attempt=1))

        # Sheet 2 should still be pending, not failed
        state2 = baton.get_sheet_state("j1", 2)
        assert state2 is not None
        assert state2.status == BatonSheetStatus.PENDING

    async def test_completion_exhaustion_with_escalation(self) -> None:
        """Completion mode exhaustion with escalation → FERMATA."""
        baton, _ = _make_baton()
        # max_completion=2: first increment allows re-dispatch (1<2),
        # second increment exhausts (2<2 = False)
        _register_simple_job(
            baton, max_retries=3, max_completion=2, escalation_enabled=True
        )

        # First partial pass → completion mode, re-dispatched
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=50.0,
            validations_passed=1,
            validations_total=2,
        ))
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.PENDING
        assert state.completion_attempts == 1

        # Second partial pass — completion budget exhausted → escalation
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=2,
            execution_success=True,
            validation_pass_rate=50.0,
            validations_passed=1,
            validations_total=2,
        ))
        assert state.status == BatonSheetStatus.FERMATA


# ============================================================================
# Self-Healing Path After Retry Exhaustion
# ============================================================================


class TestSelfHealingAfterExhaustion:
    """When retries exhaust and self-healing is enabled, enter healing mode."""

    async def test_exhaustion_with_healing_enters_retry_scheduled(self) -> None:
        """Retry exhaustion + self_healing_enabled → healing attempt scheduled."""
        baton, timer = _make_baton()
        assert timer is not None
        _register_simple_job(
            baton, max_retries=1, self_healing_enabled=True
        )

        await baton.handle_event(_fail_event(attempt=1))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # Self-healing schedules a retry, not immediate failure
        assert state.status in (
            BatonSheetStatus.RETRY_SCHEDULED,
            BatonSheetStatus.PENDING,
        )
        assert state.healing_attempts == 1

    async def test_healing_takes_priority_over_escalation(self) -> None:
        """When both healing and escalation are enabled, try healing first."""
        baton, _ = _make_baton()
        _register_simple_job(
            baton,
            max_retries=1,
            self_healing_enabled=True,
            escalation_enabled=True,
        )

        await baton.handle_event(_fail_event(attempt=1))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # Healing first, escalation if healing also fails
        assert state.healing_attempts == 1
        assert state.status != BatonSheetStatus.FAILED

    async def test_healing_exhausted_falls_to_escalation(self) -> None:
        """After max healing attempts, falls through to escalation."""
        baton, _ = _make_baton()
        _register_simple_job(
            baton,
            max_retries=1,
            self_healing_enabled=True,
            escalation_enabled=True,
        )

        # First failure → healing attempt
        await baton.handle_event(_fail_event(attempt=1))
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.healing_attempts == 1

        # Move back to pending for the healing retry
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))

        # Second failure — healing budget exhausted (default max_healing=1),
        # falls to escalation
        await baton.handle_event(_fail_event(attempt=2))
        assert state.status == BatonSheetStatus.FERMATA

    async def test_healing_exhausted_no_escalation_fails(self) -> None:
        """After max healing attempts without escalation → FAILED."""
        baton, _ = _make_baton()
        _register_simple_job(
            baton,
            max_retries=1,
            self_healing_enabled=True,
            escalation_enabled=False,
        )

        # First failure → healing attempt
        await baton.handle_event(_fail_event(attempt=1))
        state = baton.get_sheet_state("j1", 1)
        assert state is not None

        # Move back to pending
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))

        # Second failure — healing exhausted, no escalation → FAILED
        await baton.handle_event(_fail_event(attempt=2))
        assert state.status == BatonSheetStatus.FAILED


# ============================================================================
# Per-Sheet Cost Enforcement
# ============================================================================


class TestPerSheetCostEnforcement:
    """Individual sheets can have cost limits."""

    async def test_sheet_exceeds_cost_limit_fails(self) -> None:
        """When a sheet's cumulative cost exceeds its limit, it fails."""
        baton, _ = _make_baton()
        _register_simple_job(baton, max_retries=5, sheet_cost_limit=1.00)

        # First attempt: $0.80 — under limit
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="EXECUTION_ERROR",
            cost_usd=0.80,
        ))
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED

        # Move back to pending
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))

        # Second attempt: $0.30 — cumulative $1.10, over limit
        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=2,
            execution_success=False,
            error_classification="EXECUTION_ERROR",
            cost_usd=0.30,
        ))
        assert state.status == BatonSheetStatus.FAILED
        assert state.total_cost_usd == pytest.approx(1.10)

    async def test_sheet_under_cost_limit_retries(self) -> None:
        """When under cost limit, retry proceeds normally."""
        baton, _ = _make_baton()
        _register_simple_job(baton, max_retries=5, sheet_cost_limit=5.00)

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="EXECUTION_ERROR",
            cost_usd=0.50,
        ))
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED

    async def test_sheet_cost_limit_propagates_failure(self) -> None:
        """Cost-exceeded failure propagates to dependents."""
        baton, _ = _make_baton()
        sheet1 = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=5
        )
        sheet2 = SheetExecutionState(
            sheet_num=2, instrument_name="claude-code", max_retries=3
        )
        baton.register_job("j1", {1: sheet1, 2: sheet2}, {2: [1]})
        baton.set_sheet_cost_limit("j1", 1, 0.50)

        await baton.handle_event(SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="EXECUTION_ERROR",
            cost_usd=0.60,
        ))

        state2 = baton.get_sheet_state("j1", 2)
        assert state2 is not None
        assert state2.status == BatonSheetStatus.FAILED


# ============================================================================
# Process Exit Recovery
# ============================================================================


class TestProcessExitRecovery:
    """Process crashes should be handled consistently with attempt results."""

    async def test_process_exit_records_attempt(self) -> None:
        """Process crash is tracked in attempt_results, not just normal_attempts."""
        baton, _ = _make_baton()
        sheet = _register_simple_job(baton, max_retries=3)

        # Simulate dispatch (set to DISPATCHED)
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(ProcessExited(
            job_id="j1", sheet_num=1, pid=12345, exit_code=137
        ))

        assert sheet.normal_attempts == 1
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED

    async def test_process_exit_schedules_timer(self) -> None:
        """Process crash retry should schedule a timer when timer wheel exists."""
        baton, timer = _make_baton(with_timer=True)
        assert timer is not None
        sheet = _register_simple_job(baton, max_retries=3)
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(ProcessExited(
            job_id="j1", sheet_num=1, pid=12345, exit_code=137
        ))

        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED
        assert timer.pending_count == 1

    async def test_process_exit_exhaustion_with_escalation(self) -> None:
        """Process crashes can also trigger escalation when retries exhaust."""
        baton, _ = _make_baton()
        sheet = _register_simple_job(
            baton, max_retries=1, escalation_enabled=True
        )
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(ProcessExited(
            job_id="j1", sheet_num=1, pid=12345, exit_code=137
        ))

        assert sheet.status == BatonSheetStatus.FERMATA


# ============================================================================
# Configuration Wiring
# ============================================================================


class TestRetryConfiguration:
    """Retry config params are wired through register_job."""

    async def test_register_job_with_retry_params(self) -> None:
        """register_job accepts retry configuration parameters."""
        baton, _ = _make_baton()
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=5
        )
        baton.register_job(
            "j1",
            {1: sheet},
            {},
            escalation_enabled=True,
            self_healing_enabled=True,
        )

        job = baton._jobs.get("j1")
        assert job is not None
        assert job.escalation_enabled is True
        assert job.self_healing_enabled is True

    async def test_register_job_defaults_flags_to_false(self) -> None:
        """By default, escalation and healing are disabled."""
        baton, _ = _make_baton()
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=3
        )
        baton.register_job("j1", {1: sheet}, {})

        job = baton._jobs.get("j1")
        assert job is not None
        assert job.escalation_enabled is False
        assert job.self_healing_enabled is False
