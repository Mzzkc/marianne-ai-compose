"""Movement 2 adversarial tests — exhaustion paths, cost enforcement,
completion mode, failure propagation, and cross-system interaction.

Written by Breakpoint, Movement 2. These tests target the M2 baton
additions that were NOT covered by Movement 1 adversarial testing:

1. Exhaustion decision tree (healing → escalation → fail) edge cases
2. Cost enforcement at per-sheet and per-job levels
3. Completion mode → exhaustion → handler interaction
4. Failure propagation through complex dependency topologies
5. Multi-job cost isolation
6. Process crash + exhaustion path interaction
7. Rate-limited attempts and cost accumulation
8. Serialization round-trips for M2 fields
9. Concurrent event races (cancel + escalation, pause + cost)
10. Instrument state bridge under adversarial conditions

TDD: Tests written to prove edge cases that WILL appear in production
with 706-sheet concerts. Each test documents a specific failure mode.
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    CancelJob,
    EscalationNeeded,
    EscalationResolved,
    JobTimeout,
    PauseJob,
    ProcessExited,
    RateLimitHit,
    ResumeJob,
    RetryDue,
    SheetAttemptResult,
    SheetSkipped,
    ShutdownRequested,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    CircuitBreakerState,
    InstrumentState,
    SheetExecutionState,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_sheets(
    count: int = 3, instrument: str = "claude-code", max_retries: int = 3
) -> dict[int, SheetExecutionState]:
    """Create a set of sheet execution states."""
    return {
        i: SheetExecutionState(
            sheet_num=i,
            instrument_name=instrument,
            max_retries=max_retries,
        )
        for i in range(1, count + 1)
    }


def _make_multi_instrument_sheets() -> dict[int, SheetExecutionState]:
    """Create sheets spanning two instruments."""
    return {
        1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
        3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
        4: SheetExecutionState(sheet_num=4, instrument_name="gemini-cli"),
    }


def _fail_event(
    job_id: str = "j1",
    sheet_num: int = 1,
    instrument: str = "claude-code",
    cost: float = 0.0,
    attempt: int = 1,
) -> SheetAttemptResult:
    """Create a failure attempt result."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name=instrument,
        attempt=attempt,
        execution_success=False,
        cost_usd=cost,
        duration_seconds=10.0,
    )


def _success_event(
    job_id: str = "j1",
    sheet_num: int = 1,
    instrument: str = "claude-code",
    cost: float = 0.0,
    attempt: int = 1,
    pass_rate: float = 100.0,
    validations_total: int = 1,
) -> SheetAttemptResult:
    """Create a success attempt result."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name=instrument,
        attempt=attempt,
        execution_success=True,
        validation_pass_rate=pass_rate,
        validations_total=validations_total,
        cost_usd=cost,
        duration_seconds=5.0,
    )


def _partial_success_event(
    job_id: str = "j1",
    sheet_num: int = 1,
    instrument: str = "claude-code",
    pass_rate: float = 50.0,
    attempt: int = 1,
    cost: float = 0.0,
) -> SheetAttemptResult:
    """Create a partial validation pass result (completion mode trigger)."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name=instrument,
        attempt=attempt,
        execution_success=True,
        validation_pass_rate=pass_rate,
        validations_total=2,
        validations_passed=1,
        cost_usd=cost,
        duration_seconds=5.0,
    )


# =============================================================================
# 1. Exhaustion Decision Tree — healing → escalation → fail
# =============================================================================


class TestExhaustionDecisionTree:
    """Test the 3-path exhaustion handler priority:
    healing first, then escalation, then fail."""

    @pytest.mark.asyncio
    async def test_healing_takes_priority_over_escalation(self) -> None:
        """When both self_healing and escalation are enabled,
        healing should be tried first."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=1)
        baton.register_job(
            "j1", sheets, {},
            self_healing_enabled=True,
            escalation_enabled=True,
        )

        # First attempt fails, exhausting retry budget
        await baton.handle_event(_fail_event())

        # Should be in RETRY_SCHEDULED (healing path) not FERMATA
        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheet.healing_attempts == 1

    @pytest.mark.asyncio
    async def test_escalation_after_healing_exhausted(self) -> None:
        """After healing attempts are used up, escalation should kick in."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=1)
        baton.register_job(
            "j1", sheets, {},
            self_healing_enabled=True,
            escalation_enabled=True,
        )

        # Exhaust retry budget
        await baton.handle_event(_fail_event(attempt=1))
        # Now sheet is in RETRY_SCHEDULED (healing)
        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.healing_attempts == 1

        # Simulate retry timer firing, return to pending
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))
        assert sheet.status == BatonSheetStatus.PENDING

        # Second failure after healing — retries already exhausted,
        # healing already used (1 >= _DEFAULT_MAX_HEALING=1)
        await baton.handle_event(_fail_event(attempt=2))

        # Should now be in FERMATA (escalation path)
        assert sheet.status == BatonSheetStatus.FERMATA
        assert baton.is_job_paused("j1")

    @pytest.mark.asyncio
    async def test_fail_when_neither_healing_nor_escalation(self) -> None:
        """When both are disabled, exhaustion means failure."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=1)
        baton.register_job("j1", sheets, {})

        await baton.handle_event(_fail_event())

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.status == BatonSheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_exhaustion_with_orphaned_job(self) -> None:
        """If the job is somehow gone during exhaustion, the sheet
        should still be marked FAILED (not crash)."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=0)
        baton.register_job("j1", sheets, {},
                           self_healing_enabled=True)

        # Deregister the job manually
        baton.deregister_job("j1")

        # Sheet state still exists in our local reference but job is gone
        # The _handle_exhaustion should handle this gracefully
        # We can verify indirectly: the event doesn't crash
        await baton.handle_event(_fail_event())
        # No crash = success. Sheet was in deregistered job.


# =============================================================================
# 2. Cost Enforcement Edge Cases
# =============================================================================


class TestCostEnforcementAdversarial:
    """Adversarial cost enforcement tests — boundary conditions,
    interactions with other subsystems."""

    @pytest.mark.asyncio
    async def test_cost_limit_exactly_at_boundary(self) -> None:
        """Cost exactly equal to limit should NOT pause (> not >=)."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 1.0)

        # Succeed with exactly $1.00
        await baton.handle_event(_success_event(cost=1.0))

        # Sheet completed, job NOT paused (cost == limit, not >)
        assert not baton.is_job_paused("j1")
        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_cost_limit_exceeded_by_penny(self) -> None:
        """Cost just over the limit SHOULD pause."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 1.0)

        await baton.handle_event(_success_event(cost=1.01))

        assert baton.is_job_paused("j1")

    @pytest.mark.asyncio
    async def test_per_sheet_cost_limit_fails_and_propagates(self) -> None:
        """Per-sheet cost limit exceeded → sheet fails → dependents fail."""
        baton = BatonCore()
        sheets = _make_sheets(3, max_retries=3)
        deps = {2: [1], 3: [2]}  # Chain: 1 → 2 → 3
        baton.register_job("j1", sheets, deps)
        baton.set_sheet_cost_limit("j1", 1, 0.50)

        # Sheet 1 costs too much
        await baton.handle_event(_fail_event(cost=0.60))

        sheet1 = baton.get_sheet_state("j1", 1)
        sheet2 = baton.get_sheet_state("j1", 2)
        sheet3 = baton.get_sheet_state("j1", 3)
        assert sheet1 is not None
        assert sheet2 is not None
        assert sheet3 is not None
        assert sheet1.status == BatonSheetStatus.FAILED
        assert sheet2.status == BatonSheetStatus.SKIPPED, (
            "Sheet 2 should be SKIPPED (blocked by failed dependency)"
        )
        assert sheet3.status == BatonSheetStatus.SKIPPED, (
            "Sheet 3 should be SKIPPED (blocked by failed dependency)"
        )

    @pytest.mark.asyncio
    async def test_cost_accumulates_across_retries(self) -> None:
        """Cost from multiple attempts should accumulate for limit checks."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=5)
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 1.0)

        # Three failures at $0.30 each = $0.90 total — within limit
        for i in range(3):
            await baton.handle_event(_fail_event(cost=0.30, attempt=i + 1))

        assert not baton.is_job_paused("j1")

        # Fourth failure at $0.30 = $1.20 total — over limit
        await baton.handle_event(_fail_event(cost=0.30, attempt=4))

        assert baton.is_job_paused("j1")

    @pytest.mark.asyncio
    async def test_multi_job_cost_isolation(self) -> None:
        """Cost on job A must NOT affect cost limit on job B."""
        baton = BatonCore()
        sheets_a = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code"
        )}
        sheets_b = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code"
        )}
        baton.register_job("a", sheets_a, {})
        baton.register_job("b", sheets_b, {})
        baton.set_job_cost_limit("a", 1.0)
        baton.set_job_cost_limit("b", 1.0)

        # Job A spends $2.00
        await baton.handle_event(_success_event(
            job_id="a", cost=2.0
        ))
        # Job B spends $0.50
        await baton.handle_event(_success_event(
            job_id="b", cost=0.50
        ))

        # Only job A should be paused
        assert baton.is_job_paused("a")
        assert not baton.is_job_paused("b")

    @pytest.mark.asyncio
    async def test_cost_enforced_even_on_success(self) -> None:
        """A successful sheet can still trigger job cost limit pause."""
        baton = BatonCore()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 1.0)

        # Sheet 1 succeeds but costs $1.50
        await baton.handle_event(
            _success_event(sheet_num=1, cost=1.50)
        )

        # Job should be paused even though sheet succeeded
        assert baton.is_job_paused("j1")

    @pytest.mark.asyncio
    async def test_rate_limited_attempt_still_accumulates_cost(self) -> None:
        """Even rate-limited attempts can have cost (partial execution).
        Cost should still accumulate."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=5)
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 1.0)

        # Rate-limited attempt with some cost
        rl_event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            rate_limited=True,
            cost_usd=0.80,
            duration_seconds=5.0,
        )
        await baton.handle_event(rl_event)

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.total_cost_usd == pytest.approx(0.80)

        # Next attempt with more cost — should push over limit
        await baton.handle_event(_fail_event(cost=0.30, attempt=2))

        assert baton.is_job_paused("j1")

    @pytest.mark.asyncio
    async def test_zero_cost_limit_pauses_on_any_cost(self) -> None:
        """A cost limit of $0.00 means ANY cost exceeds it."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 0.0)

        # Even $0.01 exceeds a $0.00 limit
        await baton.handle_event(_success_event(cost=0.01))

        assert baton.is_job_paused("j1")


# =============================================================================
# 3. Completion Mode Adversarial
# =============================================================================


class TestCompletionModeAdversarial:
    """Test completion mode edge cases — partial pass → retry → exhaust."""

    @pytest.mark.asyncio
    async def test_completion_mode_exhaustion_goes_to_handler(self) -> None:
        """When completion budget runs out, the exhaustion handler runs."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code",
            max_retries=1, max_completion=2,
        )}
        baton.register_job("j1", sheets, {})

        # Exhaust retry budget first
        await baton.handle_event(_fail_event())
        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.status == BatonSheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_partial_pass_enters_completion_mode(self) -> None:
        """50% pass rate with execution success enters completion mode."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code",
            max_retries=3, max_completion=5,
        )}
        baton.register_job("j1", sheets, {})

        await baton.handle_event(_partial_success_event())

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        # Completion mode now schedules retry with backoff (not direct PENDING)
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheet.completion_attempts == 1

    @pytest.mark.asyncio
    async def test_completion_exhaustion_then_escalation(self) -> None:
        """Completion budget exhausted → escalation (if enabled)."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code",
            max_retries=3, max_completion=2,
        )}
        baton.register_job(
            "j1", sheets, {},
            escalation_enabled=True,
        )

        # Two partial successes exhaust completion budget
        await baton.handle_event(_partial_success_event(attempt=1))
        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED

        await baton.handle_event(_partial_success_event(attempt=2))
        # max_completion=2, completion_attempts now 2, can_complete=False
        # → exhaustion handler → escalation
        assert sheet.status == BatonSheetStatus.FERMATA
        assert baton.is_job_paused("j1")

    @pytest.mark.asyncio
    async def test_completion_mode_does_not_consume_retry_budget(self) -> None:
        """Partial success attempts should not consume normal retry budget."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code",
            max_retries=3, max_completion=5,
        )}
        baton.register_job("j1", sheets, {})

        # 3 partial successes
        for i in range(3):
            await baton.handle_event(_partial_success_event(attempt=i + 1))

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        # record_attempt doesn't count successes as normal_attempts
        assert sheet.normal_attempts == 0
        assert sheet.completion_attempts == 3
        assert sheet.can_retry  # Retry budget untouched

    @pytest.mark.asyncio
    async def test_completion_then_full_success(self) -> None:
        """Partial success → re-dispatch → full success completes sheet."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code",
            max_retries=3, max_completion=5,
        )}
        baton.register_job("j1", sheets, {})

        # First: partial success → completion mode (scheduled for retry with backoff)
        await baton.handle_event(_partial_success_event(attempt=1))
        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED

        # Timer fires — sheet moves to PENDING for dispatch
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))

        # Second: full success → completed
        await baton.handle_event(_success_event(attempt=2))
        assert sheet.status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_zero_pass_rate_with_execution_success_retries(self) -> None:
        """execution_success=True with validation_pass_rate=0.0 and
        validations_total>0 means all validations failed — should retry,
        NOT enter completion mode."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code",
            max_retries=3, max_completion=5,
        )}
        baton.register_job("j1", sheets, {})

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=0.0,
            validations_total=3,
            validations_passed=0,
            duration_seconds=5.0,
        )
        await baton.handle_event(event)

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        # 0% pass rate + execution success → NOT completion mode (need > 0)
        # → goes to retry path
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheet.completion_attempts == 0


# =============================================================================
# 4. Failure Propagation — Complex Topologies
# =============================================================================


class TestFailurePropagationAdversarial:
    """Test failure propagation through non-trivial dependency graphs."""

    @pytest.mark.asyncio
    async def test_diamond_dependency_propagation(self) -> None:
        """Diamond: 1 → 2, 1 → 3, 2 → 4, 3 → 4. Failing 1 kills all."""
        baton = BatonCore()
        sheets = _make_sheets(4, max_retries=1)
        deps = {2: [1], 3: [1], 4: [2, 3]}
        baton.register_job("j1", sheets, deps)

        # Fail sheet 1 — should propagate to 2, 3, 4
        await baton.handle_event(_fail_event(sheet_num=1))

        sheet1 = baton.get_sheet_state("j1", 1)
        assert sheet1 is not None
        assert sheet1.status == BatonSheetStatus.FAILED, (
            "Sheet 1 should be FAILED (primary failure)"
        )
        for i in range(2, 5):
            sheet = baton.get_sheet_state("j1", i)
            assert sheet is not None
            assert sheet.status == BatonSheetStatus.SKIPPED, (
                f"Sheet {i} should be SKIPPED (blocked by failed dependency)"
            )

    @pytest.mark.asyncio
    async def test_propagation_skips_already_completed(self) -> None:
        """Completed sheets downstream should NOT be overwritten by propagation."""
        baton = BatonCore()
        sheets = _make_sheets(3, max_retries=1)
        deps = {2: [1], 3: [1]}
        baton.register_job("j1", sheets, deps)

        # Complete sheet 2 first
        sheet2 = baton.get_sheet_state("j1", 2)
        assert sheet2 is not None
        sheet2.status = BatonSheetStatus.COMPLETED

        # Fail sheet 1 — should propagate to 3 but NOT 2
        await baton.handle_event(_fail_event(sheet_num=1))

        assert sheet2.status == BatonSheetStatus.COMPLETED
        sheet3 = baton.get_sheet_state("j1", 3)
        assert sheet3 is not None
        assert sheet3.status == BatonSheetStatus.SKIPPED, (
            "Sheet 3 should be SKIPPED (blocked by failed dependency)"
        )

    @pytest.mark.asyncio
    async def test_wide_fan_out_propagation(self) -> None:
        """1 → (2,3,4,...,20). Failing 1 propagates to all 19."""
        baton = BatonCore()
        sheets = _make_sheets(20, max_retries=1)
        deps = {i: [1] for i in range(2, 21)}
        baton.register_job("j1", sheets, deps)

        await baton.handle_event(_fail_event(sheet_num=1))

        sheet1 = baton.get_sheet_state("j1", 1)
        assert sheet1 is not None
        assert sheet1.status == BatonSheetStatus.FAILED, (
            "Sheet 1 should be FAILED (primary failure)"
        )
        for i in range(2, 21):
            sheet = baton.get_sheet_state("j1", i)
            assert sheet is not None
            assert sheet.status == BatonSheetStatus.SKIPPED, (
                f"Sheet {i} should be SKIPPED (blocked by failed dependency)"
            )

    @pytest.mark.asyncio
    async def test_concurrent_failures_in_parallel_branches(self) -> None:
        """Two independent branches fail simultaneously.
        1 → 3, 2 → 3. Both 1 and 2 fail. Sheet 3 should fail once."""
        baton = BatonCore()
        sheets = _make_sheets(3, max_retries=1)
        deps = {3: [1, 2]}
        baton.register_job("j1", sheets, deps)

        # Both parents fail
        await baton.handle_event(_fail_event(sheet_num=1))
        await baton.handle_event(_fail_event(sheet_num=2))

        sheet3 = baton.get_sheet_state("j1", 3)
        assert sheet3 is not None
        assert sheet3.status == BatonSheetStatus.SKIPPED, (
            "Sheet 3 should be SKIPPED (blocked by failed dependency)"
        )

    @pytest.mark.asyncio
    async def test_propagation_through_intermediate_terminal(self) -> None:
        """1 → 2 → 3. Sheet 2 is already cancelled. Failing 1 should
        still propagate THROUGH 2 to reach 3."""
        baton = BatonCore()
        sheets = _make_sheets(3, max_retries=1)
        deps = {2: [1], 3: [2]}
        baton.register_job("j1", sheets, deps)

        # Cancel sheet 2 first
        sheet2 = baton.get_sheet_state("j1", 2)
        assert sheet2 is not None
        sheet2.status = BatonSheetStatus.CANCELLED

        # Fail sheet 1
        await baton.handle_event(_fail_event(sheet_num=1))

        # Sheet 2 stays cancelled (terminal), sheet 3 should become failed
        assert sheet2.status == BatonSheetStatus.CANCELLED
        sheet3 = baton.get_sheet_state("j1", 3)
        assert sheet3 is not None
        assert sheet3.status == BatonSheetStatus.SKIPPED, (
            "Sheet 3 should be SKIPPED (blocked by failed dependency)"
        )

    @pytest.mark.asyncio
    async def test_auth_failure_triggers_propagation(self) -> None:
        """AUTH_FAILURE is an immediate fail — should propagate."""
        baton = BatonCore()
        sheets = _make_sheets(2, max_retries=3)
        deps = {2: [1]}
        baton.register_job("j1", sheets, deps)

        auth_fail = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            error_classification="AUTH_FAILURE",
            duration_seconds=1.0,
        )
        await baton.handle_event(auth_fail)

        sheet1 = baton.get_sheet_state("j1", 1)
        sheet2 = baton.get_sheet_state("j1", 2)
        assert sheet1 is not None
        assert sheet2 is not None
        assert sheet1.status == BatonSheetStatus.FAILED
        assert sheet2.status == BatonSheetStatus.SKIPPED, (
            "Sheet 2 should be SKIPPED (blocked by failed dependency)"
        )


# =============================================================================
# 5. Process Crash + Exhaustion Interaction
# =============================================================================


class TestProcessCrashExhaustion:
    """Test that process crashes route through the exhaustion handler."""

    @pytest.mark.asyncio
    async def test_crash_exhausts_retries_then_fails(self) -> None:
        """Process crash when retries are already at max → FAILED."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=1)
        baton.register_job("j1", sheets, {})

        # Set sheet to dispatched (crash only works on dispatched sheets)
        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        # Crash — this increments normal_attempts to 1, matching max_retries
        await baton.handle_event(ProcessExited(
            job_id="j1", sheet_num=1, pid=12345, exit_code=137
        ))

        assert sheet.status == BatonSheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_crash_with_healing_enabled(self) -> None:
        """Process crash + retries exhausted + healing → RETRY_SCHEDULED."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=1)
        baton.register_job(
            "j1", sheets, {},
            self_healing_enabled=True,
        )

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(ProcessExited(
            job_id="j1", sheet_num=1, pid=12345, exit_code=137
        ))

        # Should go to healing (RETRY_SCHEDULED) not FAILED
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheet.healing_attempts == 1

    @pytest.mark.asyncio
    async def test_crash_on_non_dispatched_is_noop(self) -> None:
        """Process crash for a pending sheet should be ignored."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.status == BatonSheetStatus.PENDING

        await baton.handle_event(ProcessExited(
            job_id="j1", sheet_num=1, pid=12345, exit_code=1
        ))

        # Should remain pending — crash only affects dispatched sheets
        assert sheet.status == BatonSheetStatus.PENDING

    @pytest.mark.asyncio
    async def test_crash_propagates_failure_to_dependents(self) -> None:
        """Crash + exhaustion → failure propagation."""
        baton = BatonCore()
        sheets = _make_sheets(2, max_retries=1)
        deps = {2: [1]}
        baton.register_job("j1", sheets, deps)

        sheet1 = baton.get_sheet_state("j1", 1)
        assert sheet1 is not None
        sheet1.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(ProcessExited(
            job_id="j1", sheet_num=1, pid=12345, exit_code=137
        ))

        assert sheet1.status == BatonSheetStatus.FAILED
        sheet2 = baton.get_sheet_state("j1", 2)
        assert sheet2 is not None
        assert sheet2.status == BatonSheetStatus.SKIPPED, (
            "Sheet 2 should be SKIPPED (blocked by failed dependency)"
        )


# =============================================================================
# 6. Concurrent Event Races
# =============================================================================


class TestConcurrentEventRaces:
    """Test event ordering that would be adversarial in production."""

    @pytest.mark.asyncio
    async def test_cancel_then_escalation_resolved(self) -> None:
        """Cancel a job, then an escalation resolution arrives late.
        The resolution should be a no-op (job deregistered)."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job(
            "j1", sheets, {},
            escalation_enabled=True,
        )

        # Cancel the job (deregisters it)
        await baton.handle_event(CancelJob(job_id="j1"))

        # Late escalation resolution — job is gone
        await baton.handle_event(EscalationResolved(
            job_id="j1", sheet_num=1, decision="retry"
        ))
        # No crash = success. The job doesn't exist anymore.

    @pytest.mark.asyncio
    async def test_timeout_then_late_success(self) -> None:
        """Job times out, then a success arrives for a cancelled sheet.
        Terminal guard should prevent resurrection."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})

        # Timeout cancels all sheets
        await baton.handle_event(JobTimeout(job_id="j1"))

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.status == BatonSheetStatus.CANCELLED

        # Late success — should be ignored (terminal guard)
        await baton.handle_event(_success_event())

        assert sheet.status == BatonSheetStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_pause_during_cost_limit_pause(self) -> None:
        """User pauses during a cost-limit pause.
        Resume clears user_paused, but cost enforcement re-pauses (F-140).
        Cost limits are a safety mechanism — resume should not bypass them.
        Consistent with F-067: escalation unpause also re-checks cost."""
        baton = BatonCore()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 1.0)

        # Sheet 1 succeeds, triggers cost pause
        await baton.handle_event(_success_event(
            sheet_num=1, cost=2.0
        ))
        assert baton.is_job_paused("j1")

        # User also explicitly pauses
        await baton.handle_event(PauseJob(job_id="j1"))

        # Resume clears user_paused, but cost enforcement re-pauses.
        # F-140: User must increase cost limit to proceed, not just resume.
        await baton.handle_event(ResumeJob(job_id="j1"))
        job = baton._jobs["j1"]
        assert job.user_paused is False, "User pause should be cleared"
        assert baton.is_job_paused("j1"), (
            "Cost enforcement should re-pause (F-140, same class as F-067)"
        )

    @pytest.mark.asyncio
    async def test_skip_after_retry_scheduled(self) -> None:
        """A skip event arrives for a sheet that's in RETRY_SCHEDULED.
        Skip should take effect (not terminal yet)."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=3)
        baton.register_job("j1", sheets, {})

        # Fail → RETRY_SCHEDULED
        await baton.handle_event(_fail_event())
        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED

        # Skip event arrives
        await baton.handle_event(SheetSkipped(
            job_id="j1", sheet_num=1, reason="skip_when condition met"
        ))

        assert sheet.status == BatonSheetStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_escalation_needed_during_waiting(self) -> None:
        """Sheet is WAITING (rate limited) when escalation arrives.
        Should transition to FERMATA since WAITING is not terminal."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {},
                           escalation_enabled=True)

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.WAITING

        await baton.handle_event(EscalationNeeded(
            job_id="j1", sheet_num=1, reason="manual"
        ))

        assert sheet.status == BatonSheetStatus.FERMATA
        assert baton.is_job_paused("j1")


# =============================================================================
# 7. Retry Delay Calculation
# =============================================================================


class TestRetryDelayCalculation:
    """Test the exponential backoff calculation."""

    def test_first_retry_uses_base_delay(self) -> None:
        """Attempt 0 (first retry) → base delay (10s)."""
        baton = BatonCore()
        assert baton.calculate_retry_delay(0) == pytest.approx(10.0)

    def test_exponential_growth(self) -> None:
        """Each retry doubles the delay."""
        baton = BatonCore()
        assert baton.calculate_retry_delay(0) == pytest.approx(10.0)
        assert baton.calculate_retry_delay(1) == pytest.approx(20.0)
        assert baton.calculate_retry_delay(2) == pytest.approx(40.0)
        assert baton.calculate_retry_delay(3) == pytest.approx(80.0)

    def test_clamped_at_max(self) -> None:
        """Delay is clamped to max_retry_delay (3600s = 1 hour)."""
        baton = BatonCore()
        # 10 * 2^20 = 10,485,760 → clamped to 3600
        assert baton.calculate_retry_delay(20) == pytest.approx(3600.0)

    def test_negative_attempt_doesnt_crash(self) -> None:
        """Negative attempt index shouldn't crash (defensive)."""
        baton = BatonCore()
        # calculate_retry_delay uses max(0, attempt-1) in _schedule_retry,
        # but the method itself accepts any int
        delay = baton.calculate_retry_delay(-1)
        assert delay >= 0


# =============================================================================
# 8. Serialization Round-Trips for M2 Fields
# =============================================================================


class TestSerializationM2Fields:
    """Test that M2-added fields survive serialization round-trips."""

    def test_sheet_state_with_healing_and_completion(self) -> None:
        """healing_attempts and completion_attempts survive round-trip."""
        state = SheetExecutionState(
            sheet_num=5,
            instrument_name="gemini-cli",
            max_retries=10,
            max_completion=8,
        )
        state.status = BatonSheetStatus.FERMATA
        state.normal_attempts = 3
        state.completion_attempts = 4
        state.healing_attempts = 1
        state.total_cost_usd = 2.50
        state.total_duration_seconds = 120.5
        state.next_retry_at = 12345.678

        data = state.to_dict()
        restored = SheetExecutionState.from_dict(data)

        assert restored.sheet_num == 5
        assert restored.instrument_name == "gemini-cli"
        assert restored.status == BatonSheetStatus.FERMATA
        assert restored.normal_attempts == 3
        assert restored.completion_attempts == 4
        assert restored.healing_attempts == 1
        assert restored.max_retries == 10
        assert restored.max_completion == 8
        assert restored.total_cost_usd == pytest.approx(2.50)
        assert restored.total_duration_seconds == pytest.approx(120.5)
        # next_retry_at is transient (exclude=True) — not persisted, resets to None
        assert restored.next_retry_at is None

    def test_instrument_state_with_open_breaker(self) -> None:
        """InstrumentState with open circuit breaker and rate limit
        survives round-trip."""
        state = InstrumentState(name="claude-code", max_concurrent=2)
        state.rate_limited = True
        state.rate_limit_expires_at = 99999.0
        state.circuit_breaker = CircuitBreakerState.OPEN
        state.consecutive_failures = 5
        state.circuit_breaker_threshold = 3
        state.circuit_breaker_recovery_at = 88888.0

        data = state.to_dict()
        restored = InstrumentState.from_dict(data)

        assert restored.name == "claude-code"
        assert restored.max_concurrent == 2
        assert restored.rate_limited is True
        assert restored.rate_limit_expires_at == pytest.approx(99999.0)
        assert restored.circuit_breaker == CircuitBreakerState.OPEN
        assert restored.consecutive_failures == 5
        assert restored.circuit_breaker_threshold == 3
        assert restored.circuit_breaker_recovery_at == pytest.approx(88888.0)

    def test_from_dict_missing_m2_fields_uses_defaults(self) -> None:
        """Pre-M2 persisted data (without completion/healing fields)
        should load with safe defaults."""
        # Simulate a pre-M2 serialized state
        data = {
            "sheet_num": 1,
            "instrument_name": "claude-code",
            "status": "pending",
            "normal_attempts": 0,
        }
        restored = SheetExecutionState.from_dict(data)

        assert restored.completion_attempts == 0
        assert restored.healing_attempts == 0
        assert restored.max_retries == 3  # default
        assert restored.max_completion == 5  # default
        assert restored.total_cost_usd == 0.0
        assert restored.total_duration_seconds == 0.0
        assert restored.next_retry_at is None


# =============================================================================
# 9. Instrument State Bridge Under Adversarial Conditions
# =============================================================================


class TestInstrumentStateBridgeAdversarial:
    """Test the instrument state ↔ baton core integration edge cases."""

    @pytest.mark.asyncio
    async def test_auto_register_on_job_registration(self) -> None:
        """Instruments used by sheets should be auto-registered."""
        baton = BatonCore()
        sheets = _make_multi_instrument_sheets()
        baton.register_job("j1", sheets, {})

        # Both instruments should be registered
        assert baton.get_instrument_state("claude-code") is not None
        assert baton.get_instrument_state("gemini-cli") is not None

    @pytest.mark.asyncio
    async def test_success_resets_circuit_breaker_from_half_open(self) -> None:
        """The circuit breaker state machine: OPEN → HALF_OPEN → success → CLOSED.
        A success when HALF_OPEN closes the breaker and resets failures."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=10)
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None

        # 5 failures trips the breaker (default threshold=5)
        for i in range(5):
            await baton.handle_event(_fail_event(attempt=i + 1))

        assert inst.circuit_breaker == CircuitBreakerState.OPEN

        # Simulate recovery: manually set to HALF_OPEN (in production, a timer does this)
        inst.circuit_breaker = CircuitBreakerState.HALF_OPEN

        # Retry due → pending
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))
        # Success in half-open state should close the breaker
        await baton.handle_event(_success_event(attempt=6))

        assert inst.consecutive_failures == 0
        assert inst.circuit_breaker == CircuitBreakerState.CLOSED

    @pytest.mark.asyncio
    async def test_failure_in_half_open_reopens_breaker(self) -> None:
        """A failure while HALF_OPEN should reopen the circuit breaker."""
        baton = BatonCore()
        sheets = _make_sheets(1, max_retries=10)
        baton.register_job("j1", sheets, {})

        inst = baton.get_instrument_state("claude-code")
        assert inst is not None

        # Trip the breaker
        for i in range(5):
            await baton.handle_event(_fail_event(attempt=i + 1))
        assert inst.circuit_breaker == CircuitBreakerState.OPEN

        # Move to HALF_OPEN (probe)
        inst.circuit_breaker = CircuitBreakerState.HALF_OPEN

        # Retry due → pending
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))
        # Failure → back to OPEN
        await baton.handle_event(_fail_event(attempt=6))

        assert inst.circuit_breaker == CircuitBreakerState.OPEN

    @pytest.mark.asyncio
    async def test_rate_limit_across_multiple_jobs(self) -> None:
        """Rate limit on an instrument affects ALL jobs using it."""
        baton = BatonCore()
        sheets_a = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code"
        )}
        sheets_b = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code"
        )}
        baton.register_job("a", sheets_a, {})
        baton.register_job("b", sheets_b, {})

        # Dispatch both
        a1 = baton.get_sheet_state("a", 1)
        b1 = baton.get_sheet_state("b", 1)
        assert a1 is not None
        assert b1 is not None
        a1.status = BatonSheetStatus.DISPATCHED
        b1.status = BatonSheetStatus.DISPATCHED

        # Rate limit claude-code (triggered by job a, sheet 1)
        await baton.handle_event(RateLimitHit(
            instrument="claude-code", wait_seconds=60.0,
            job_id="a", sheet_num=1,
        ))

        # Both should be WAITING
        assert a1.status == BatonSheetStatus.WAITING
        assert b1.status == BatonSheetStatus.WAITING

    @pytest.mark.asyncio
    async def test_build_dispatch_config_reflects_state(self) -> None:
        """build_dispatch_config should accurately reflect instrument state."""
        baton = BatonCore()
        sheets = _make_multi_instrument_sheets()
        baton.register_job("j1", sheets, {})

        # Rate limit claude-code
        inst_cc = baton.get_instrument_state("claude-code")
        assert inst_cc is not None
        inst_cc.rate_limited = True

        # Open circuit breaker on gemini-cli
        inst_gem = baton.get_instrument_state("gemini-cli")
        assert inst_gem is not None
        inst_gem.circuit_breaker = CircuitBreakerState.OPEN

        config = baton.build_dispatch_config()
        assert "claude-code" in config.rate_limited_instruments
        assert "gemini-cli" in config.open_circuit_breakers

    @pytest.mark.asyncio
    async def test_failure_on_unknown_instrument_doesnt_crash(self) -> None:
        """If an instrument isn't registered, failure tracking is a no-op."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})

        # Manually remove the instrument registration
        baton._instruments.clear()

        # Should not crash
        await baton.handle_event(_fail_event())


# =============================================================================
# 10. Job Completion Detection
# =============================================================================


class TestJobCompletionDetection:
    """Test is_job_complete under various terminal/non-terminal mixes."""

    def test_all_completed_is_complete(self) -> None:
        """All sheets COMPLETED → job complete."""
        baton = BatonCore()
        sheets = _make_sheets(3)
        baton.register_job("j1", sheets, {})
        for s in sheets.values():
            s.status = BatonSheetStatus.COMPLETED
        assert baton.is_job_complete("j1")

    def test_mixed_terminal_is_complete(self) -> None:
        """Mix of completed, failed, skipped, cancelled → job complete."""
        baton = BatonCore()
        sheets = _make_sheets(4)
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.COMPLETED
        sheets[2].status = BatonSheetStatus.FAILED
        sheets[3].status = BatonSheetStatus.SKIPPED
        sheets[4].status = BatonSheetStatus.CANCELLED
        assert baton.is_job_complete("j1")

    def test_one_waiting_is_not_complete(self) -> None:
        """One WAITING sheet → job NOT complete."""
        baton = BatonCore()
        sheets = _make_sheets(3)
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.COMPLETED
        sheets[2].status = BatonSheetStatus.COMPLETED
        sheets[3].status = BatonSheetStatus.WAITING
        assert not baton.is_job_complete("j1")

    def test_fermata_is_not_complete(self) -> None:
        """FERMATA sheet → job NOT complete (awaiting human decision)."""
        baton = BatonCore()
        sheets = _make_sheets(2)
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.COMPLETED
        sheets[2].status = BatonSheetStatus.FERMATA
        assert not baton.is_job_complete("j1")

    def test_retry_scheduled_is_not_complete(self) -> None:
        """RETRY_SCHEDULED sheet → job NOT complete."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.RETRY_SCHEDULED
        assert not baton.is_job_complete("j1")

    def test_unknown_job_is_not_complete(self) -> None:
        """Unknown job → False, not crash."""
        baton = BatonCore()
        assert not baton.is_job_complete("nonexistent")


# =============================================================================
# 11. Escalation Decision Variants
# =============================================================================


class TestEscalationDecisionVariants:
    """Test all possible escalation resolution decisions."""

    @pytest.mark.asyncio
    async def test_escalation_accept_completes_sheet(self) -> None:
        """Decision 'accept' → COMPLETED (even though validations failed)."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {},
                           escalation_enabled=True)

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.FERMATA

        await baton.handle_event(EscalationResolved(
            job_id="j1", sheet_num=1, decision="accept"
        ))
        assert sheet.status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_escalation_skip_marks_skipped(self) -> None:
        """Decision 'skip' → SKIPPED."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {},
                           escalation_enabled=True)

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.FERMATA

        await baton.handle_event(EscalationResolved(
            job_id="j1", sheet_num=1, decision="skip"
        ))
        assert sheet.status == BatonSheetStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_escalation_retry_returns_to_pending(self) -> None:
        """Decision 'retry' → PENDING."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {},
                           escalation_enabled=True)

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.FERMATA

        await baton.handle_event(EscalationResolved(
            job_id="j1", sheet_num=1, decision="retry"
        ))
        assert sheet.status == BatonSheetStatus.PENDING

    @pytest.mark.asyncio
    async def test_escalation_fail_propagates_to_dependents(self) -> None:
        """Decision 'fail' → FAILED + propagation."""
        baton = BatonCore()
        sheets = _make_sheets(2)
        deps = {2: [1]}
        baton.register_job("j1", sheets, deps,
                           escalation_enabled=True)

        sheet1 = baton.get_sheet_state("j1", 1)
        assert sheet1 is not None
        sheet1.status = BatonSheetStatus.FERMATA

        await baton.handle_event(EscalationResolved(
            job_id="j1", sheet_num=1, decision="fail"
        ))

        assert sheet1.status == BatonSheetStatus.FAILED
        sheet2 = baton.get_sheet_state("j1", 2)
        assert sheet2 is not None
        assert sheet2.status == BatonSheetStatus.SKIPPED, (
            "Sheet 2 should be SKIPPED (blocked by failed dependency)"
        )

    @pytest.mark.asyncio
    async def test_escalation_resolved_on_non_fermata_is_noop(self) -> None:
        """Resolution for a sheet not in FERMATA does nothing to the sheet."""
        baton = BatonCore()
        sheets = _make_sheets(1)
        baton.register_job("j1", sheets, {},
                           escalation_enabled=True)

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        # Sheet is PENDING, not FERMATA
        assert sheet.status == BatonSheetStatus.PENDING

        await baton.handle_event(EscalationResolved(
            job_id="j1", sheet_num=1, decision="retry"
        ))
        # Status unchanged — resolution only works on FERMATA sheets
        # BUT: job pause state is still updated by the handler
        assert sheet.status == BatonSheetStatus.PENDING


# =============================================================================
# 12. Shutdown Behavior Under Load
# =============================================================================


class TestShutdownBehavior:
    """Test shutdown interactions with various in-flight states."""

    @pytest.mark.asyncio
    async def test_graceful_shutdown_preserves_all_terminal(self) -> None:
        """Graceful shutdown: terminal sheets untouched, others stay."""
        baton = BatonCore()
        sheets = _make_sheets(5)
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.COMPLETED
        sheets[2].status = BatonSheetStatus.FAILED
        sheets[3].status = BatonSheetStatus.DISPATCHED
        sheets[4].status = BatonSheetStatus.RETRY_SCHEDULED
        sheets[5].status = BatonSheetStatus.FERMATA

        await baton.handle_event(ShutdownRequested(graceful=True))

        # Graceful: nothing changes
        assert sheets[1].status == BatonSheetStatus.COMPLETED
        assert sheets[2].status == BatonSheetStatus.FAILED
        assert sheets[3].status == BatonSheetStatus.DISPATCHED
        assert sheets[4].status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheets[5].status == BatonSheetStatus.FERMATA

    @pytest.mark.asyncio
    async def test_forced_shutdown_cancels_non_terminal(self) -> None:
        """Non-graceful shutdown: non-terminal → CANCELLED."""
        baton = BatonCore()
        sheets = _make_sheets(5)
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.COMPLETED
        sheets[2].status = BatonSheetStatus.FAILED
        sheets[3].status = BatonSheetStatus.DISPATCHED
        sheets[4].status = BatonSheetStatus.RETRY_SCHEDULED
        sheets[5].status = BatonSheetStatus.FERMATA

        await baton.handle_event(ShutdownRequested(graceful=False))

        # Terminal: preserved
        assert sheets[1].status == BatonSheetStatus.COMPLETED
        assert sheets[2].status == BatonSheetStatus.FAILED
        # Non-terminal: cancelled
        assert sheets[3].status == BatonSheetStatus.CANCELLED
        assert sheets[4].status == BatonSheetStatus.CANCELLED
        assert sheets[5].status == BatonSheetStatus.CANCELLED
