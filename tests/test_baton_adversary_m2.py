"""Movement 2 adversarial tests — Axiom fix verification, intersection
bugs, F-062/F-063 fix proofs, and edge cases at feature boundaries.

These tests target the SPECIFIC bugs fixed in M2 and the intersections
between those fixes. Each test is designed to catch regressions in:

1. F-062: deregister_job memory leak (cost limit dict cleanup)
2. F-063: _handle_process_exited record_attempt contract
3. F-065: execution_success + 0% validation infinite retry
4. F-066: escalation unpause with multiple FERMATA sheets
5. F-067: escalation unpause overriding cost-enforcement pause
6. Intersection: F-065 + cost limits, F-066 + F-067, process crash + cost

Written by Adversary, Movement 2.
"""

from __future__ import annotations

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    CancelJob,
    EscalationNeeded,
    EscalationResolved,
    EscalationTimeout,
    ProcessExited,
    SheetAttemptResult,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    SheetExecutionState,
)


# =====================================================================
# Helpers
# =====================================================================

def _make_sheets(
    count: int = 3,
    instrument: str = "claude-code",
    max_retries: int = 3,
    max_completion: int = 5,
) -> dict[int, SheetExecutionState]:
    """Create a set of sheet execution states."""
    return {
        i: SheetExecutionState(
            sheet_num=i,
            instrument_name=instrument,
            max_retries=max_retries,
            max_completion=max_completion,
        )
        for i in range(1, count + 1)
    }


def _make_baton_with_job(
    job_id: str = "test-job",
    sheet_count: int = 3,
    instrument: str = "claude-code",
    max_retries: int = 3,
    max_completion: int = 5,
    deps: dict[int, list[int]] | None = None,
    escalation_enabled: bool = False,
    self_healing_enabled: bool = False,
) -> BatonCore:
    """Create a BatonCore with a registered job."""
    baton = BatonCore()
    sheets = _make_sheets(
        count=sheet_count,
        instrument=instrument,
        max_retries=max_retries,
        max_completion=max_completion,
    )
    baton.register_job(
        job_id,
        sheets,
        deps or {},
        escalation_enabled=escalation_enabled,
        self_healing_enabled=self_healing_enabled,
    )
    return baton


def _attempt_result(
    job_id: str = "test-job",
    sheet_num: int = 1,
    instrument: str = "claude-code",
    attempt: int = 1,
    execution_success: bool = True,
    validation_pass_rate: float = 100.0,
    validations_total: int = 5,
    cost_usd: float = 0.10,
    duration_seconds: float = 10.0,
    rate_limited: bool = False,
    error_classification: str | None = None,
) -> SheetAttemptResult:
    """Create a SheetAttemptResult with sensible defaults."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name=instrument,
        attempt=attempt,
        execution_success=execution_success,
        validation_pass_rate=validation_pass_rate,
        validations_total=validations_total,
        validations_passed=int(validations_total * validation_pass_rate / 100),
        cost_usd=cost_usd,
        duration_seconds=duration_seconds,
        rate_limited=rate_limited,
        error_classification=error_classification,
    )


# =====================================================================
# F-062: deregister_job cleans up cost limit dicts
# =====================================================================


class TestF062DeregisterJobCleanup:
    """Prove that deregister_job no longer leaks cost limit entries."""

    @pytest.mark.asyncio
    async def test_job_cost_limit_cleaned_on_deregister(self) -> None:
        """Job cost limit entry is removed when job is deregistered."""
        baton = _make_baton_with_job()
        baton.set_job_cost_limit("test-job", 100.0)
        assert "test-job" in baton._job_cost_limits

        baton.deregister_job("test-job")
        assert "test-job" not in baton._job_cost_limits

    @pytest.mark.asyncio
    async def test_sheet_cost_limits_cleaned_on_deregister(self) -> None:
        """All sheet cost limit entries for a job are removed."""
        baton = _make_baton_with_job(sheet_count=5)
        for i in range(1, 6):
            baton.set_sheet_cost_limit("test-job", i, 10.0)
        assert len(baton._sheet_cost_limits) == 5

        baton.deregister_job("test-job")
        assert len(baton._sheet_cost_limits) == 0

    @pytest.mark.asyncio
    async def test_other_job_cost_limits_preserved(self) -> None:
        """Deregistering one job doesn't affect another job's cost limits."""
        baton = _make_baton_with_job(job_id="job-a", sheet_count=2)
        # Register second job manually
        sheets_b = _make_sheets(count=2)
        baton.register_job("job-b", sheets_b, {})
        baton.set_job_cost_limit("job-a", 100.0)
        baton.set_job_cost_limit("job-b", 200.0)
        baton.set_sheet_cost_limit("job-a", 1, 10.0)
        baton.set_sheet_cost_limit("job-b", 1, 20.0)

        baton.deregister_job("job-a")

        assert "job-a" not in baton._job_cost_limits
        assert "job-b" in baton._job_cost_limits
        assert ("job-a", 1) not in baton._sheet_cost_limits
        assert ("job-b", 1) in baton._sheet_cost_limits

    @pytest.mark.asyncio
    async def test_deregister_without_cost_limits_is_safe(self) -> None:
        """Deregistering a job that never had cost limits doesn't crash."""
        baton = _make_baton_with_job()
        baton.deregister_job("test-job")
        # No KeyError, no crash
        assert baton.job_count == 0

    @pytest.mark.asyncio
    async def test_cancel_then_deregister_cleans_everything(self) -> None:
        """The cancel→deregister pattern cleans up all state."""
        baton = _make_baton_with_job()
        baton.set_job_cost_limit("test-job", 50.0)
        baton.set_sheet_cost_limit("test-job", 1, 5.0)
        baton.set_sheet_cost_limit("test-job", 2, 5.0)

        await baton.handle_event(CancelJob(job_id="test-job"))
        assert baton.job_count == 0
        assert len(baton._job_cost_limits) == 0
        assert len(baton._sheet_cost_limits) == 0


# =====================================================================
# F-063: _handle_process_exited uses record_attempt
# =====================================================================


class TestF063ProcessExitedRecordAttempt:
    """Prove that process crashes are recorded through record_attempt()."""

    @pytest.mark.asyncio
    async def test_crash_appears_in_attempt_history(self) -> None:
        """A process crash creates an entry in attempt_results."""
        baton = _make_baton_with_job()
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            ProcessExited(job_id="test-job", sheet_num=1, pid=12345, exit_code=137)
        )

        assert len(sheet.attempt_results) == 1
        result = sheet.attempt_results[0]
        assert result.execution_success is False
        assert result.exit_code == 137
        assert result.error_classification == "PROCESS_CRASH"
        assert "12345" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_crash_increments_normal_attempts(self) -> None:
        """Process crash consumes retry budget (via record_attempt)."""
        baton = _make_baton_with_job(max_retries=2)
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            ProcessExited(job_id="test-job", sheet_num=1, pid=100)
        )
        assert sheet.normal_attempts == 1

    @pytest.mark.asyncio
    async def test_crash_without_exit_code_recorded(self) -> None:
        """Process crash with no exit code still records properly."""
        baton = _make_baton_with_job()
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            ProcessExited(job_id="test-job", sheet_num=1, pid=999, exit_code=None)
        )

        assert len(sheet.attempt_results) == 1
        result = sheet.attempt_results[0]
        assert result.exit_code is None
        assert "unexpectedly" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_crash_exhaustion_with_attempt_history(self) -> None:
        """After max crashes, all attempts are in the history."""
        baton = _make_baton_with_job(max_retries=2)
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None

        for i in range(2):
            sheet.status = BatonSheetStatus.DISPATCHED
            await baton.handle_event(
                ProcessExited(job_id="test-job", sheet_num=1, pid=100 + i)
            )

        assert len(sheet.attempt_results) == 2
        assert sheet.normal_attempts == 2
        assert sheet.status == BatonSheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_crash_does_not_track_cost(self) -> None:
        """Crash attempts record $0 cost (process died, no API call)."""
        baton = _make_baton_with_job()
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            ProcessExited(job_id="test-job", sheet_num=1, pid=42)
        )
        assert sheet.total_cost_usd == 0.0
        assert sheet.attempt_results[0].cost_usd == 0.0


# =====================================================================
# F-065: execution_success + 0% validation consumes retry budget
# =====================================================================


class TestF065ZeroValidationRetryBudget:
    """Prove that execution_success=True with 0% validation consumes budget."""

    @pytest.mark.asyncio
    async def test_zero_percent_validation_increments_budget(self) -> None:
        """execution_success + 0% pass rate increments normal_attempts."""
        baton = _make_baton_with_job(max_retries=3)
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            _attempt_result(
                execution_success=True,
                validation_pass_rate=0.0,
                validations_total=5,
            )
        )

        # record_attempt does NOT increment (execution_success=True).
        # F-065 fix DOES increment. Total should be 1.
        assert sheet.normal_attempts == 1

    @pytest.mark.asyncio
    async def test_zero_percent_exhausts_after_max_retries(self) -> None:
        """Sheet fails after max_retries of 0% validation results."""
        baton = _make_baton_with_job(max_retries=2)
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None

        for attempt in range(2):
            sheet.status = BatonSheetStatus.DISPATCHED
            await baton.handle_event(
                _attempt_result(
                    attempt=attempt + 1,
                    execution_success=True,
                    validation_pass_rate=0.0,
                    validations_total=5,
                )
            )

        assert sheet.normal_attempts == 2
        assert sheet.status == BatonSheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_zero_validations_total_bypasses_f065(self) -> None:
        """execution_success + 0 validations_total → F-018 guard → COMPLETED."""
        baton = _make_baton_with_job()
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            _attempt_result(
                execution_success=True,
                validation_pass_rate=0.0,
                validations_total=0,
            )
        )

        # F-018 guard sets effective_pass_rate to 100.0 → COMPLETED
        assert sheet.status == BatonSheetStatus.COMPLETED
        assert sheet.normal_attempts == 0  # No F-065 increment

    @pytest.mark.asyncio
    async def test_zero_percent_does_not_double_count_execution_failure(self) -> None:
        """execution_success=False already increments via record_attempt.
        F-065 must NOT add a second increment for failed executions."""
        baton = _make_baton_with_job(max_retries=3)
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            _attempt_result(
                execution_success=False,
                validation_pass_rate=0.0,
                validations_total=5,
            )
        )

        # record_attempt increments for execution_success=False.
        # F-065 condition (execution_success=True) does NOT fire.
        assert sheet.normal_attempts == 1

    @pytest.mark.asyncio
    async def test_partial_pass_does_not_trigger_f065(self) -> None:
        """execution_success + 50% validation → completion mode, NOT F-065."""
        baton = _make_baton_with_job()
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            _attempt_result(
                execution_success=True,
                validation_pass_rate=50.0,
                validations_total=10,
            )
        )

        # Partial pass → completion mode (scheduled for retry with backoff)
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheet.normal_attempts == 0
        assert sheet.completion_attempts == 1


# =====================================================================
# F-066: Escalation unpause respects multiple FERMATA sheets
# =====================================================================


class TestF066MultipleFermataSheets:
    """Prove that resolving one escalation doesn't unpause if others remain."""

    @pytest.mark.asyncio
    async def test_resolve_one_of_two_fermata_stays_paused(self) -> None:
        """Resolving sheet 1's escalation while sheet 2 is in FERMATA → paused."""
        baton = _make_baton_with_job(
            sheet_count=3, escalation_enabled=True, max_retries=1
        )
        # Exhaust sheets 1 and 2 to trigger escalation
        for sn in [1, 2]:
            sheet = baton.get_sheet_state("test-job", sn)
            assert sheet is not None
            sheet.status = BatonSheetStatus.DISPATCHED
            await baton.handle_event(
                _attempt_result(
                    sheet_num=sn, execution_success=False, attempt=1,
                    validation_pass_rate=0.0, validations_total=0,
                )
            )

        # Both sheets should be in FERMATA
        s1 = baton.get_sheet_state("test-job", 1)
        s2 = baton.get_sheet_state("test-job", 2)
        assert s1 is not None and s1.status == BatonSheetStatus.FERMATA
        assert s2 is not None and s2.status == BatonSheetStatus.FERMATA
        assert baton.is_job_paused("test-job")

        # Resolve only sheet 1
        await baton.handle_event(
            EscalationResolved(job_id="test-job", sheet_num=1, decision="retry")
        )

        # Job should still be paused — sheet 2 is still FERMATA
        assert baton.is_job_paused("test-job")
        assert s1.status == BatonSheetStatus.PENDING
        assert s2.status == BatonSheetStatus.FERMATA

    @pytest.mark.asyncio
    async def test_resolve_last_fermata_unpauses(self) -> None:
        """Resolving the last FERMATA sheet unpauses the job."""
        baton = _make_baton_with_job(
            sheet_count=2, escalation_enabled=True, max_retries=1
        )
        for sn in [1, 2]:
            sheet = baton.get_sheet_state("test-job", sn)
            assert sheet is not None
            sheet.status = BatonSheetStatus.DISPATCHED
            await baton.handle_event(
                _attempt_result(
                    sheet_num=sn, execution_success=False, attempt=1,
                    validation_pass_rate=0.0, validations_total=0,
                )
            )

        # Resolve both
        await baton.handle_event(
            EscalationResolved(job_id="test-job", sheet_num=1, decision="skip")
        )
        assert baton.is_job_paused("test-job")  # Still paused — sheet 2

        await baton.handle_event(
            EscalationResolved(job_id="test-job", sheet_num=2, decision="accept")
        )
        assert not baton.is_job_paused("test-job")  # Now unpaused

    @pytest.mark.asyncio
    async def test_timeout_one_of_two_fermata_stays_paused(self) -> None:
        """Timeout on one escalation while another is pending → still paused."""
        baton = _make_baton_with_job(
            sheet_count=2, escalation_enabled=True, max_retries=1
        )
        for sn in [1, 2]:
            sheet = baton.get_sheet_state("test-job", sn)
            assert sheet is not None
            sheet.status = BatonSheetStatus.DISPATCHED
            await baton.handle_event(
                _attempt_result(
                    sheet_num=sn, execution_success=False, attempt=1,
                    validation_pass_rate=0.0, validations_total=0,
                )
            )

        # Timeout on sheet 1
        await baton.handle_event(
            EscalationTimeout(job_id="test-job", sheet_num=1)
        )

        s1 = baton.get_sheet_state("test-job", 1)
        assert s1 is not None and s1.status == BatonSheetStatus.FAILED
        assert baton.is_job_paused("test-job")  # Sheet 2 still FERMATA

    @pytest.mark.asyncio
    async def test_user_paused_preserved_after_all_fermata_resolved(self) -> None:
        """User pause survives even after all escalations resolve."""
        baton = _make_baton_with_job(
            sheet_count=2, escalation_enabled=True, max_retries=1
        )
        for sn in [1, 2]:
            sheet = baton.get_sheet_state("test-job", sn)
            assert sheet is not None
            sheet.status = BatonSheetStatus.DISPATCHED
            await baton.handle_event(
                _attempt_result(
                    sheet_num=sn, execution_success=False, attempt=1,
                    validation_pass_rate=0.0, validations_total=0,
                )
            )

        # User also pauses the job
        job = baton._jobs["test-job"]
        job.user_paused = True

        # Resolve both escalations
        await baton.handle_event(
            EscalationResolved(job_id="test-job", sheet_num=1, decision="skip")
        )
        await baton.handle_event(
            EscalationResolved(job_id="test-job", sheet_num=2, decision="skip")
        )

        # Job still paused — user_paused takes precedence
        assert baton.is_job_paused("test-job")

    @pytest.mark.asyncio
    async def test_duplicate_escalation_resolved_is_safe(self) -> None:
        """Double-resolving the same sheet doesn't crash or corrupt state."""
        baton = _make_baton_with_job(
            sheet_count=1, escalation_enabled=True, max_retries=1
        )
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            _attempt_result(
                sheet_num=1, execution_success=False, attempt=1,
                validation_pass_rate=0.0, validations_total=0,
            )
        )
        assert sheet.status == BatonSheetStatus.FERMATA

        # Resolve once
        await baton.handle_event(
            EscalationResolved(job_id="test-job", sheet_num=1, decision="retry")
        )
        assert sheet.status == BatonSheetStatus.PENDING

        # Resolve again — sheet is no longer FERMATA, so no status change
        await baton.handle_event(
            EscalationResolved(job_id="test-job", sheet_num=1, decision="skip")
        )
        assert sheet.status == BatonSheetStatus.PENDING  # Unchanged


# =====================================================================
# F-067: Escalation unpause respects cost enforcement
# =====================================================================


class TestF067EscalationRespectsCost:
    """Prove that escalation resolution re-checks cost limits."""

    @pytest.mark.asyncio
    async def test_resolve_escalation_with_exceeded_cost_stays_paused(self) -> None:
        """Cost-exceeded job stays paused after escalation resolves."""
        baton = _make_baton_with_job(
            sheet_count=2, escalation_enabled=True, max_retries=1
        )
        baton.set_job_cost_limit("test-job", 0.50)

        # Run sheet 1 successfully with high cost
        s1 = baton.get_sheet_state("test-job", 1)
        assert s1 is not None
        s1.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            _attempt_result(sheet_num=1, cost_usd=0.60)
        )
        # Sheet 1 completed but job is over cost
        assert s1.status == BatonSheetStatus.COMPLETED
        assert baton.is_job_paused("test-job")

        # Exhaust sheet 2 to trigger escalation
        s2 = baton.get_sheet_state("test-job", 2)
        assert s2 is not None
        s2.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            _attempt_result(
                sheet_num=2, execution_success=False, attempt=1,
                validation_pass_rate=0.0, validations_total=0,
                cost_usd=0.10,
            )
        )

        # Resolve the escalation
        await baton.handle_event(
            EscalationResolved(job_id="test-job", sheet_num=2, decision="retry")
        )

        # Job should STILL be paused — cost limit exceeded
        assert baton.is_job_paused("test-job")

    @pytest.mark.asyncio
    async def test_resolve_escalation_under_cost_unpauses(self) -> None:
        """Job under cost limit unpauses after escalation resolves."""
        baton = _make_baton_with_job(
            sheet_count=2, escalation_enabled=True, max_retries=1
        )
        baton.set_job_cost_limit("test-job", 100.0)

        # Exhaust sheet 1
        s1 = baton.get_sheet_state("test-job", 1)
        assert s1 is not None
        s1.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            _attempt_result(
                sheet_num=1, execution_success=False, attempt=1,
                cost_usd=0.10, validation_pass_rate=0.0, validations_total=0,
            )
        )
        assert s1.status == BatonSheetStatus.FERMATA

        await baton.handle_event(
            EscalationResolved(job_id="test-job", sheet_num=1, decision="skip")
        )

        # Under cost → unpaused
        assert not baton.is_job_paused("test-job")

    @pytest.mark.asyncio
    async def test_timeout_with_exceeded_cost_stays_paused(self) -> None:
        """Escalation timeout with cost exceeded → still paused."""
        baton = _make_baton_with_job(
            sheet_count=1, escalation_enabled=True, max_retries=1
        )
        baton.set_job_cost_limit("test-job", 0.01)

        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            _attempt_result(
                sheet_num=1, execution_success=False, attempt=1,
                cost_usd=0.50, validation_pass_rate=0.0, validations_total=0,
            )
        )
        assert sheet.status == BatonSheetStatus.FERMATA

        await baton.handle_event(
            EscalationTimeout(job_id="test-job", sheet_num=1)
        )

        # Sheet failed + cost exceeded → job stays paused
        assert baton.is_job_paused("test-job")


# =====================================================================
# Intersection: F-065 + cost limits
# =====================================================================


class TestF065CostIntersection:
    """Attack the intersection of 0% validation retry with cost enforcement."""

    @pytest.mark.asyncio
    async def test_zero_percent_validation_with_sheet_cost_limit(self) -> None:
        """Sheet fails from cost limit before F-065 can trigger retry."""
        baton = _make_baton_with_job(max_retries=5)
        baton.set_sheet_cost_limit("test-job", 1, 0.15)

        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        # First attempt: $0.10, 0% validation
        await baton.handle_event(
            _attempt_result(
                execution_success=True,
                validation_pass_rate=0.0,
                validations_total=5,
                cost_usd=0.10,
            )
        )
        # F-065 increments normal_attempts, then retry scheduled
        assert sheet.normal_attempts == 1
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED

        # Second attempt: $0.10, total now $0.20 > $0.15 limit
        sheet.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            _attempt_result(
                attempt=2,
                execution_success=True,
                validation_pass_rate=0.0,
                validations_total=5,
                cost_usd=0.10,
            )
        )
        # Cost limit exceeded → FAILED, regardless of retry budget
        assert sheet.status == BatonSheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_zero_percent_with_job_cost_limit_pauses(self) -> None:
        """Job pauses when 0% validation attempts accumulate past job cost."""
        baton = _make_baton_with_job(max_retries=10)
        baton.set_job_cost_limit("test-job", 0.25)

        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None

        for attempt in range(3):
            sheet.status = BatonSheetStatus.DISPATCHED
            await baton.handle_event(
                _attempt_result(
                    attempt=attempt + 1,
                    execution_success=True,
                    validation_pass_rate=0.0,
                    validations_total=5,
                    cost_usd=0.10,
                )
            )

        # Total cost: $0.30 > $0.25 limit → job paused
        assert baton.is_job_paused("test-job")


# =====================================================================
# Intersection: F-066 + F-067 — multiple escalations + cost
# =====================================================================


class TestF066F067EscalationCostIntersection:
    """Attack the intersection of multiple escalations and cost limits."""

    @pytest.mark.asyncio
    async def test_resolve_all_fermata_but_cost_exceeded(self) -> None:
        """All FERMATA sheets resolved but job over cost → still paused."""
        baton = _make_baton_with_job(
            sheet_count=3, escalation_enabled=True, max_retries=1
        )
        baton.set_job_cost_limit("test-job", 0.01)

        # Exhaust all 3 sheets (with cost)
        for sn in [1, 2, 3]:
            sheet = baton.get_sheet_state("test-job", sn)
            assert sheet is not None
            sheet.status = BatonSheetStatus.DISPATCHED
            await baton.handle_event(
                _attempt_result(
                    sheet_num=sn, execution_success=False, attempt=1,
                    cost_usd=0.10, validation_pass_rate=0.0, validations_total=0,
                )
            )

        # All in FERMATA, job paused
        assert baton.is_job_paused("test-job")

        # Resolve all three
        for sn in [1, 2, 3]:
            await baton.handle_event(
                EscalationResolved(
                    job_id="test-job", sheet_num=sn, decision="retry"
                )
            )

        # All FERMATA resolved, but cost > limit → still paused
        assert baton.is_job_paused("test-job")


# =====================================================================
# Intersection: process crash + cost limit
# =====================================================================


class TestProcessCrashCostIntersection:
    """Process crashes with cost limits set."""

    @pytest.mark.asyncio
    async def test_crash_does_not_affect_cost_tracking(self) -> None:
        """Crash records $0 cost — doesn't push over cost limit."""
        baton = _make_baton_with_job(max_retries=5)
        baton.set_sheet_cost_limit("test-job", 1, 0.50)

        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None

        # 5 crashes — all $0 cost
        for i in range(5):
            sheet.status = BatonSheetStatus.DISPATCHED
            await baton.handle_event(
                ProcessExited(job_id="test-job", sheet_num=1, pid=100 + i)
            )

        # Cost is still $0 — crashes don't generate API costs
        assert sheet.total_cost_usd == 0.0
        # But retries are exhausted
        assert sheet.status == BatonSheetStatus.FAILED
        assert sheet.normal_attempts == 5


# =====================================================================
# Edge cases: unknown escalation decisions
# =====================================================================


class TestEscalationDecisionEdgeCases:
    """Probe escalation decision handling for unexpected inputs."""

    @pytest.mark.asyncio
    async def test_unknown_decision_fails_sheet(self) -> None:
        """Unknown escalation decision defaults to FAILED."""
        baton = _make_baton_with_job(
            sheet_count=1, escalation_enabled=True, max_retries=1
        )
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            _attempt_result(
                sheet_num=1, execution_success=False, attempt=1,
                validation_pass_rate=0.0, validations_total=0,
            )
        )
        assert sheet.status == BatonSheetStatus.FERMATA

        await baton.handle_event(
            EscalationResolved(
                job_id="test-job", sheet_num=1, decision="invalid_decision"
            )
        )

        # Unknown decision → FAILED (the else branch)
        assert sheet.status == BatonSheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_accept_decision_marks_completed(self) -> None:
        """'accept' decision marks the sheet as COMPLETED."""
        baton = _make_baton_with_job(
            sheet_count=1, escalation_enabled=True, max_retries=1
        )
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            _attempt_result(
                sheet_num=1, execution_success=False, attempt=1,
                validation_pass_rate=0.0, validations_total=0,
            )
        )

        await baton.handle_event(
            EscalationResolved(
                job_id="test-job", sheet_num=1, decision="accept"
            )
        )
        assert sheet.status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_fail_decision_propagates_to_dependents(self) -> None:
        """'fail' decision (unknown) propagates failure to dependents."""
        baton = _make_baton_with_job(
            sheet_count=3,
            escalation_enabled=True,
            max_retries=1,
            deps={2: [1], 3: [1]},
        )
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED
        await baton.handle_event(
            _attempt_result(
                sheet_num=1, execution_success=False, attempt=1,
                validation_pass_rate=0.0, validations_total=0,
            )
        )

        await baton.handle_event(
            EscalationResolved(
                job_id="test-job", sheet_num=1, decision="fail"
            )
        )

        # Sheet 1 failed, sheets 2 and 3 should be SKIPPED (blocked by failed dependency)
        s2 = baton.get_sheet_state("test-job", 2)
        s3 = baton.get_sheet_state("test-job", 3)
        assert s2 is not None and s2.status == BatonSheetStatus.SKIPPED
        assert s3 is not None and s3.status == BatonSheetStatus.SKIPPED


# =====================================================================
# Edge cases: zero cost limit
# =====================================================================


class TestZeroCostLimit:
    """Probe behavior when cost limit is set to 0."""

    @pytest.mark.asyncio
    async def test_zero_job_cost_limit_pauses_on_first_cost(self) -> None:
        """Job with $0 cost limit pauses on the first non-zero cost attempt."""
        baton = _make_baton_with_job()
        baton.set_job_cost_limit("test-job", 0.0)

        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            _attempt_result(cost_usd=0.01)
        )
        # $0.01 > $0.00 → job paused
        assert baton.is_job_paused("test-job")

    @pytest.mark.asyncio
    async def test_zero_sheet_cost_limit_fails_on_retry_path(self) -> None:
        """Sheet with $0 cost limit fails when a retry would exceed it.

        NOTE: Per-sheet cost limits only apply on the retry/failure path,
        not the success path. A successful execution completes regardless
        of per-sheet cost limits. This is asymmetric with per-job cost
        limits, which apply on ALL paths. See F-080.
        """
        baton = _make_baton_with_job(max_retries=5)
        baton.set_sheet_cost_limit("test-job", 1, 0.0)

        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        # Execution FAILS with $0.01 cost → retry path → cost check
        await baton.handle_event(
            _attempt_result(
                execution_success=False,
                validation_pass_rate=0.0,
                validations_total=0,
                cost_usd=0.01,
            )
        )
        # $0.01 > $0.00 → sheet FAILED from cost enforcement
        assert sheet.status == BatonSheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_zero_sheet_cost_limit_does_not_block_success(self) -> None:
        """A successful execution completes even with $0 per-sheet cost limit.

        Per-sheet cost limits only apply on the retry path. This is by design
        but is an asymmetry worth documenting (F-080).
        """
        baton = _make_baton_with_job()
        baton.set_sheet_cost_limit("test-job", 1, 0.0)

        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            _attempt_result(cost_usd=0.50)  # High cost, but succeeds
        )
        # Success path bypasses per-sheet cost check
        assert sheet.status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_zero_cost_attempt_survives_zero_limit(self) -> None:
        """A $0 cost attempt doesn't exceed a $0 limit (not strictly >)."""
        baton = _make_baton_with_job()
        baton.set_sheet_cost_limit("test-job", 1, 0.0)

        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.DISPATCHED

        await baton.handle_event(
            _attempt_result(cost_usd=0.0)
        )
        # $0.00 is NOT > $0.00 → sheet completes normally
        assert sheet.status == BatonSheetStatus.COMPLETED


# =====================================================================
# Edge: escalation on terminal sheet
# =====================================================================


class TestEscalationTerminalGuard:
    """Escalation events on terminal sheets must be no-ops."""

    @pytest.mark.asyncio
    async def test_escalation_needed_on_completed_sheet(self) -> None:
        """EscalationNeeded on a completed sheet doesn't change status."""
        baton = _make_baton_with_job(escalation_enabled=True)
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.COMPLETED

        await baton.handle_event(
            EscalationNeeded(job_id="test-job", sheet_num=1, reason="test")
        )
        assert sheet.status == BatonSheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_escalation_resolved_on_failed_sheet(self) -> None:
        """EscalationResolved on a FAILED sheet doesn't change it to retry."""
        baton = _make_baton_with_job(escalation_enabled=True)
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.FAILED

        await baton.handle_event(
            EscalationResolved(
                job_id="test-job", sheet_num=1, decision="retry"
            )
        )
        # Still FAILED — was not in FERMATA
        assert sheet.status == BatonSheetStatus.FAILED


# =====================================================================
# Edge: double deregister
# =====================================================================


class TestDoubleDeregister:
    """Deregistering a job twice must be idempotent."""

    @pytest.mark.asyncio
    async def test_double_deregister_is_safe(self) -> None:
        """Second deregister_job call is a no-op."""
        baton = _make_baton_with_job()
        baton.set_job_cost_limit("test-job", 100.0)
        baton.deregister_job("test-job")
        assert baton.job_count == 0

        # Second call — should not crash
        baton.deregister_job("test-job")
        assert baton.job_count == 0

    @pytest.mark.asyncio
    async def test_deregister_nonexistent_job(self) -> None:
        """Deregistering a job that was never registered is safe."""
        baton = BatonCore()
        baton.deregister_job("phantom-job")
        assert baton.job_count == 0


# =====================================================================
# Edge: process crash on non-DISPATCHED sheet
# =====================================================================


class TestProcessCrashStatusGuard:
    """Process crashes on sheets not in DISPATCHED status are ignored."""

    @pytest.mark.asyncio
    async def test_crash_on_pending_sheet_ignored(self) -> None:
        """A crash event for a PENDING sheet is a no-op."""
        baton = _make_baton_with_job()
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        assert sheet.status == BatonSheetStatus.PENDING

        await baton.handle_event(
            ProcessExited(job_id="test-job", sheet_num=1, pid=1)
        )
        assert sheet.status == BatonSheetStatus.PENDING
        assert sheet.normal_attempts == 0

    @pytest.mark.asyncio
    async def test_crash_on_completed_sheet_ignored(self) -> None:
        """A crash event for a COMPLETED sheet is a no-op."""
        baton = _make_baton_with_job()
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.COMPLETED

        await baton.handle_event(
            ProcessExited(job_id="test-job", sheet_num=1, pid=1)
        )
        assert sheet.status == BatonSheetStatus.COMPLETED
        assert sheet.normal_attempts == 0

    @pytest.mark.asyncio
    async def test_crash_on_unknown_job_ignored(self) -> None:
        """A crash event for an unknown job is silently ignored."""
        baton = BatonCore()
        await baton.handle_event(
            ProcessExited(job_id="phantom", sheet_num=1, pid=1)
        )
        # No crash, no error
        assert baton.job_count == 0

    @pytest.mark.asyncio
    async def test_crash_on_retry_scheduled_ignored(self) -> None:
        """A crash for a RETRY_SCHEDULED sheet is a no-op.
        The old process may die after the retry was already scheduled."""
        baton = _make_baton_with_job()
        sheet = baton.get_sheet_state("test-job", 1)
        assert sheet is not None
        sheet.status = BatonSheetStatus.RETRY_SCHEDULED

        await baton.handle_event(
            ProcessExited(job_id="test-job", sheet_num=1, pid=1)
        )
        assert sheet.status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheet.normal_attempts == 0
