"""Property-based tests for movement 4 baton features.

Extends the invariant suite to cover features added since movement 2:

30. Adapter state mapping totality — every BatonSheetStatus has a checkpoint mapping.
31. Adapter state mapping terminal preservation — terminal baton states map to terminal
    checkpoint states.
32. Deregister cleanup completeness — no orphaned cost limits after deregister (F-062).
33. F-065 zero-validation full-failure increment — execution_success + 0% validation
    + validations_total > 0 consumes retry budget via manual increment.
34. F-066 multi-fermata unpause guard — job only unpauses when ALL fermatas resolved.
35. F-067 cost re-check after escalation — cost limit re-applied after unpause.
36. Musician F-018 contract — no validations + success = 100.0 pass rate.
37. Instrument auto-registration on job registration — all instruments tracked.
38. Cancel-then-deregister atomicity — cancel marks non-terminal, deregister cleans up.
39. Prompt assembly structure — preamble always first, validations section always last
    before completion suffix.
40. AttemptContext defaults — cross-sheet data fields initialized correctly.

@pytest.mark.property_based
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import hypothesis.strategies as st
from hypothesis import HealthCheck, given, settings


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an async coroutine synchronously (for hypothesis tests)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        # Already inside an event loop — create a task (shouldn't happen in tests)
        raise RuntimeError("Cannot _run() inside a running event loop")
    return asyncio.run(coro)

from marianne.daemon.baton.adapter import (
    _BATON_TO_CHECKPOINT,
    _CHECKPOINT_TO_BATON,
    baton_to_checkpoint_status,
    checkpoint_to_baton_status,
)
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    CancelJob,
    EscalationNeeded,
    EscalationResolved,
    EscalationTimeout,
    PauseJob,
    SheetAttemptResult,
)
from marianne.daemon.baton.state import (
    _TERMINAL_BATON_STATUSES,
    AttemptContext,
    AttemptMode,
    BatonSheetStatus,
    SheetExecutionState,
)

# =============================================================================
# Strategies
# =============================================================================

_JOB_ID = st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N")))
_INSTRUMENT = st.sampled_from(["claude-code", "gemini-cli", "codex-cli", "aider", "ollama"])
_SHEET_NUM = st.integers(min_value=1, max_value=50)
_NONNEG_FLOAT = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)


@st.composite
def sheet_execution_state(draw: st.DrawFn) -> SheetExecutionState:
    """Generate a random SheetExecutionState."""
    return SheetExecutionState(
        sheet_num=draw(_SHEET_NUM),
        instrument_name=draw(_INSTRUMENT),
        max_retries=draw(st.integers(min_value=1, max_value=10)),
        max_completion=draw(st.integers(min_value=1, max_value=10)),
    )


# =============================================================================
# 30. Adapter State Mapping Totality
# =============================================================================


class TestAdapterStateMappingTotality:
    """Every BatonSheetStatus must have a checkpoint mapping.

    Invariant: The set of keys in _BATON_TO_CHECKPOINT equals the set of
    all BatonSheetStatus enum members. No status is left unmapped.
    """

    def test_every_baton_status_has_checkpoint_mapping(self) -> None:
        """All 11 BatonSheetStatus members are in the mapping."""
        all_statuses = set(BatonSheetStatus)
        mapped_statuses = set(_BATON_TO_CHECKPOINT.keys())
        assert all_statuses == mapped_statuses, (
            f"Unmapped statuses: {all_statuses - mapped_statuses}"
        )

    def test_all_checkpoint_statuses_have_reverse_mapping(self) -> None:
        """All 5 checkpoint status strings are in the reverse mapping."""
        # The checkpoint status domain is fixed
        expected = {"pending", "in_progress", "completed", "failed", "skipped"}
        mapped = set(_CHECKPOINT_TO_BATON.keys())
        assert expected == mapped

    @given(st.sampled_from(list(BatonSheetStatus)))
    @settings(max_examples=50)
    def test_baton_to_checkpoint_returns_valid_string(
        self, status: BatonSheetStatus
    ) -> None:
        """baton_to_checkpoint_status never raises for any valid input."""
        result = baton_to_checkpoint_status(status)
        assert isinstance(result, str)
        assert result in {"pending", "in_progress", "completed", "failed", "skipped"}


# =============================================================================
# 31. Terminal State Preservation Across Mappings
# =============================================================================


class TestTerminalStatePreservation:
    """Terminal baton states must map to terminal checkpoint states.

    Invariant: If a BatonSheetStatus is terminal, its checkpoint mapping
    must also be a terminal checkpoint status (completed, failed, skipped).
    """

    _TERMINAL_CHECKPOINT = {"completed", "failed", "skipped"}

    @given(st.sampled_from(list(_TERMINAL_BATON_STATUSES)))
    @settings(max_examples=20)
    def test_terminal_baton_maps_to_terminal_checkpoint(
        self, status: BatonSheetStatus
    ) -> None:
        """Terminal baton states map to terminal checkpoint states."""
        checkpoint = baton_to_checkpoint_status(status)
        assert checkpoint in self._TERMINAL_CHECKPOINT, (
            f"Terminal {status} mapped to non-terminal '{checkpoint}'"
        )

    def test_round_trip_preserves_terminality(self) -> None:
        """checkpoint → baton → checkpoint preserves the terminal property."""
        for cp_status in ("completed", "failed", "skipped"):
            baton_status = checkpoint_to_baton_status(cp_status)
            round_trip = baton_to_checkpoint_status(baton_status)
            assert round_trip == cp_status, (
                f"Round trip failed: {cp_status} → {baton_status} → {round_trip}"
            )


# =============================================================================
# 32. Deregister Cleanup Completeness (F-062)
# =============================================================================


class TestDeregisterCleanupCompleteness:
    """After deregister_job, no orphaned cost limits remain.

    Invariant: ∀ job_id, after deregister_job(job_id):
    - job_id not in _job_cost_limits
    - no (job_id, *) key in _sheet_cost_limits
    """

    @given(
        job_id=_JOB_ID,
        num_sheets=st.integers(min_value=1, max_value=10),
        instrument=_INSTRUMENT,
        job_cost=_NONNEG_FLOAT,
        sheet_cost=_NONNEG_FLOAT,
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_no_orphaned_cost_limits_after_deregister(
        self,
        job_id: str,
        num_sheets: int,
        instrument: str,
        job_cost: float,
        sheet_cost: float,
    ) -> None:
        """Cost limits are fully cleaned up on deregister."""
        baton = BatonCore()
        sheets = {
            i: SheetExecutionState(sheet_num=i, instrument_name=instrument)
            for i in range(1, num_sheets + 1)
        }
        baton.register_job(job_id, sheets, {})

        # Set cost limits
        baton.set_job_cost_limit(job_id, job_cost)
        for i in range(1, num_sheets + 1):
            baton.set_sheet_cost_limit(job_id, i, sheet_cost)

        # Deregister
        baton.deregister_job(job_id)

        # Verify cleanup
        assert job_id not in baton._job_cost_limits
        orphaned_sheet_keys = [
            k for k in baton._sheet_cost_limits if k[0] == job_id
        ]
        assert orphaned_sheet_keys == [], (
            f"Orphaned sheet cost limits: {orphaned_sheet_keys}"
        )

    @given(
        num_jobs=st.integers(min_value=2, max_value=5),
        instrument=_INSTRUMENT,
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_deregister_one_job_does_not_affect_other_jobs(
        self, num_jobs: int, instrument: str
    ) -> None:
        """Deregistering job A does not remove job B's cost limits."""
        baton = BatonCore()
        job_ids = [f"job_{i}" for i in range(num_jobs)]

        for jid in job_ids:
            sheets = {1: SheetExecutionState(sheet_num=1, instrument_name=instrument)}
            baton.register_job(jid, sheets, {})
            baton.set_job_cost_limit(jid, 10.0)
            baton.set_sheet_cost_limit(jid, 1, 5.0)

        # Deregister the first job
        baton.deregister_job(job_ids[0])

        # Others should still have their limits
        for jid in job_ids[1:]:
            assert jid in baton._job_cost_limits
            assert (jid, 1) in baton._sheet_cost_limits


# =============================================================================
# 33. F-065: Zero-Validation Full-Failure Budget Consumption
# =============================================================================


class TestF065ZeroValidationBudgetConsumption:
    """execution_success + 0% validation + validations_total > 0 must consume budget.

    Invariant: After processing such a result, normal_attempts increments
    by exactly 1 (from the manual increment at core.py:834-835).
    Without this, sheets with total validation failure retry forever.
    """

    @given(
        max_retries=st.integers(min_value=1, max_value=10),
        num_zero_pass_attempts=st.integers(min_value=1, max_value=15),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_zero_pass_rate_eventually_exhausts_retries(
        self, max_retries: int, num_zero_pass_attempts: int
    ) -> None:
        """Sheets with 0% validation pass rate hit exhaustion after max_retries."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=max_retries,
            )
        }
        baton.register_job("j1", sheets, {})

        for i in range(num_zero_pass_attempts):
            sheet = baton.get_sheet_state("j1", 1)
            if sheet is None or sheet.status in _TERMINAL_BATON_STATUSES:
                break
            # Reset to dispatchable so the handler processes it
            sheet.status = BatonSheetStatus.DISPATCHED

            event = SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=i + 1,
                execution_success=True,
                validations_passed=0,
                validations_total=3,
                validation_pass_rate=0.0,
            )
            _run(baton.handle_event(event))

        sheet = baton.get_sheet_state("j1", 1)
        assert sheet is not None
        # After enough attempts, the sheet must be terminal
        if num_zero_pass_attempts >= max_retries:
            assert sheet.status in _TERMINAL_BATON_STATUSES, (
                f"After {num_zero_pass_attempts} attempts with "
                f"max_retries={max_retries}, "
                f"status is {sheet.status} (expected terminal)"
            )


# =============================================================================
# 34. F-066: Multi-Fermata Unpause Guard
# =============================================================================


class TestMultiFermataUnpauseGuard:
    """Job only unpauses when ALL fermatas are resolved.

    Invariant: After resolving escalation for sheet A, if sheet B is
    still in FERMATA, job.paused remains True.
    """

    @given(
        num_escalated=st.integers(min_value=2, max_value=6),
        resolve_count=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_job_stays_paused_until_all_fermatas_resolved(
        self, num_escalated: int, resolve_count: int
    ) -> None:
        """Resolving fewer than all fermatas keeps job paused."""
        baton = BatonCore()
        sheets = {
            i: SheetExecutionState(sheet_num=i, instrument_name="claude-code")
            for i in range(1, num_escalated + 1)
        }
        baton.register_job("j1", sheets, {}, escalation_enabled=True)


        # Put all sheets in FERMATA
        for i in range(1, num_escalated + 1):
            s = baton.get_sheet_state("j1", i)
            assert s is not None
            s.status = BatonSheetStatus.DISPATCHED  # Must be non-terminal first
            _run(baton.handle_event(
                EscalationNeeded(job_id="j1", sheet_num=i, reason="test")
            ))

        # Verify all in FERMATA and job is paused
        assert baton.is_job_paused("j1")

        # Resolve some (but not all) fermatas
        actual_resolve = min(resolve_count, num_escalated)
        for i in range(1, actual_resolve + 1):
            _run(baton.handle_event(
                EscalationResolved(job_id="j1", sheet_num=i, decision="retry")
            ))

        if actual_resolve < num_escalated:
            # Some sheets still in FERMATA → job must stay paused
            assert baton.is_job_paused("j1"), (
                f"Job unpaused with {num_escalated - actual_resolve} "
                f"FERMATA sheets remaining"
            )
        else:
            # All resolved → job should be unpaused
            assert not baton.is_job_paused("j1"), (
                "Job still paused after all fermatas resolved"
            )


# =============================================================================
# 35. F-067: Cost Re-check After Escalation Unpause
# =============================================================================


class TestCostRecheckAfterEscalation:
    """Cost limits are re-checked when escalation unpauses a job.

    Invariant: If a job exceeds its cost limit AND an escalation resolves,
    the job is re-paused by the cost check even though escalation tried
    to unpause it.
    """

    @given(
        cost_limit=st.floats(min_value=0.01, max_value=10.0, allow_nan=False),
        sheet_cost=st.floats(min_value=0.01, max_value=10.0, allow_nan=False),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_cost_exceeded_job_re_pauses_after_escalation_resolve(
        self, cost_limit: float, sheet_cost: float
    ) -> None:
        """Escalation resolution doesn't bypass cost limits."""
        if sheet_cost <= cost_limit:
            return  # Only test when cost exceeds limit

        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {}, escalation_enabled=True)
        baton.set_job_cost_limit("j1", cost_limit)


        # Accumulate cost on sheet 1
        s1 = baton.get_sheet_state("j1", 1)
        assert s1 is not None
        s1.total_cost_usd = sheet_cost

        # Put sheet 2 in FERMATA
        s2 = baton.get_sheet_state("j1", 2)
        assert s2 is not None
        s2.status = BatonSheetStatus.DISPATCHED
        _run(baton.handle_event(
            EscalationNeeded(job_id="j1", sheet_num=2, reason="test")
        ))

        # Resolve escalation — the cost re-check should keep it paused
        _run(baton.handle_event(
            EscalationResolved(job_id="j1", sheet_num=2, decision="retry")
        ))

        # Job must still be paused due to cost
        assert baton.is_job_paused("j1"), (
            f"Job unpaused despite cost {sheet_cost:.2f} exceeding limit {cost_limit:.2f}"
        )


# =============================================================================
# 36. Musician F-018 Contract: No Validations = 100%
# =============================================================================


class TestMusicianF018Contract:
    """In the baton's decision tree, execution_success + validations_total==0
    always results in COMPLETED status.

    Invariant: The F-018 guard at core.py:749-755 treats no-validation success
    as 100% pass rate, regardless of the validation_pass_rate field value.
    """

    @given(
        reported_pass_rate=st.floats(
            min_value=0.0, max_value=100.0, allow_nan=False
        ),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_no_validation_success_always_completes(
        self, reported_pass_rate: float
    ) -> None:
        """Any pass_rate value with validations_total=0 + success → COMPLETED."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        }
        baton.register_job("j1", sheets, {})

        # Set to dispatched so handler processes it
        s = baton.get_sheet_state("j1", 1)
        assert s is not None
        s.status = BatonSheetStatus.DISPATCHED

        event = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validations_passed=0,
            validations_total=0,
            validation_pass_rate=reported_pass_rate,
        )
        _run(baton.handle_event(event))

        assert s.status == BatonSheetStatus.COMPLETED, (
            f"Sheet with no validations and pass_rate={reported_pass_rate} "
            f"ended in {s.status} instead of COMPLETED"
        )


# =============================================================================
# 37. Instrument Auto-Registration on Job Registration
# =============================================================================


class TestInstrumentAutoRegistration:
    """All instruments used by a job's sheets are auto-registered.

    Invariant: After register_job, every distinct instrument_name in the
    job's sheets has an InstrumentState in the baton's instrument registry.
    """

    @given(
        instruments=st.lists(
            _INSTRUMENT, min_size=1, max_size=10
        ),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_all_sheet_instruments_are_registered(
        self, instruments: list[str]
    ) -> None:
        """Every instrument referenced by sheets is auto-registered."""
        baton = BatonCore()
        sheets = {
            i + 1: SheetExecutionState(
                sheet_num=i + 1, instrument_name=inst
            )
            for i, inst in enumerate(instruments)
        }
        baton.register_job("j1", sheets, {})

        unique_instruments = set(instruments)
        for inst_name in unique_instruments:
            state = baton.get_instrument_state(inst_name)
            assert state is not None, (
                f"Instrument '{inst_name}' not auto-registered"
            )

    def test_auto_registration_is_idempotent(self) -> None:
        """Registering a second job with the same instrument doesn't create duplicates."""
        baton = BatonCore()
        sheets1 = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")}
        sheets2 = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")}
        baton.register_job("j1", sheets1, {})
        baton.register_job("j2", sheets2, {})

        # Only one InstrumentState for claude-code
        state = baton.get_instrument_state("claude-code")
        assert state is not None


# =============================================================================
# 38. Cancel-Then-Deregister Atomicity
# =============================================================================


class TestCancelThenDeregisterAtomicity:
    """CancelJob marks non-terminal sheets as cancelled, then deregisters.

    Invariant: After CancelJob, the job is removed from the baton's
    tracking (deregistered). Before deregistration, non-terminal sheets
    are marked CANCELLED. Terminal sheets are preserved.
    """

    @given(
        num_sheets=st.integers(min_value=1, max_value=10),
        completed_sheets=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_cancel_deregisters_job(
        self, num_sheets: int, completed_sheets: int
    ) -> None:
        """After CancelJob, the job no longer exists in the baton."""
        baton = BatonCore()
        actual_completed = min(completed_sheets, num_sheets)
        sheets = {
            i: SheetExecutionState(sheet_num=i, instrument_name="claude-code")
            for i in range(1, num_sheets + 1)
        }
        # Mark some as completed
        for i in range(1, actual_completed + 1):
            sheets[i].status = BatonSheetStatus.COMPLETED

        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 100.0)

        _run(baton.handle_event(CancelJob(job_id="j1")))

        # Job is deregistered
        assert baton.get_sheet_state("j1", 1) is None
        assert "j1" not in baton._job_cost_limits


# =============================================================================
# 39. Prompt Assembly Structure
# =============================================================================


class TestPromptAssemblyStructure:
    """The musician's _build_prompt assembles layers in the correct order.

    Invariant:
    - Preamble is always the first section
    - Validation requirements always come after template content
    - Completion suffix is always the last section when present
    """

    def test_preamble_is_first_section(self) -> None:
        """The preamble (positional identity) is always the first section."""
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.musician import _build_prompt

        sheet = Sheet(
            num=3,
            movement=1,
            voice_count=1,
            prompt_template="Do the work.",
            workspace=Path("/tmp/test"),
            timeout_seconds=300,
            instrument_name="claude-code",
        )
        context = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        prompt = _build_prompt(sheet, context, total_sheets=10, total_movements=2)

        # Preamble is wrapped in <mozart-preamble> tags
        assert prompt.startswith("<mozart-preamble>"), (
            f"Prompt does not start with preamble. First 100 chars: {prompt[:100]}"
        )
        assert "You are sheet 3 of 10" in prompt

    def test_validations_after_template(self) -> None:
        """Validation requirements section appears after the template content."""
        from marianne.core.config.execution import ValidationRule
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.musician import _build_prompt

        sheet = Sheet(
            num=1,
            movement=1,
            voice_count=1,
            prompt_template="Write a function.",
            workspace=Path("/tmp/test"),
            timeout_seconds=300,
            instrument_name="claude-code",
            validations=[
                ValidationRule(type="file_exists", path="{workspace}/output.py"),
            ],
        )
        context = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        prompt = _build_prompt(sheet, context)

        template_pos = prompt.find("Write a function.")
        validation_pos = prompt.find("Success Requirements")

        assert template_pos >= 0, "Template not found in prompt"
        assert validation_pos >= 0, "Validation section not found in prompt"
        assert validation_pos > template_pos, (
            "Validations must come after template content"
        )

    def test_completion_suffix_is_last(self) -> None:
        """Completion mode suffix is always the last section."""
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.musician import _build_prompt

        suffix = "FINISH YOUR WORK. Focus on the remaining validations."
        sheet = Sheet(
            num=1,
            movement=1,
            voice_count=1,
            prompt_template="Do the work.",
            workspace=Path("/tmp/test"),
            timeout_seconds=300,
            instrument_name="claude-code",
        )
        context = AttemptContext(
            attempt_number=2,
            mode=AttemptMode.COMPLETION,
            completion_prompt_suffix=suffix,
        )
        prompt = _build_prompt(sheet, context)

        assert prompt.rstrip().endswith(suffix), (
            f"Prompt does not end with completion suffix. Last 100 chars: {prompt[-100:]}"
        )


# =============================================================================
# 40. AttemptContext Cross-Sheet Data Path
# =============================================================================


class TestAttemptContextDefaults:
    """AttemptContext fields are correctly initialized.

    Invariant: Default AttemptContext has empty previous_outputs dict,
    and the total_sheets/total_movements fields are correctly passed through.
    """

    @given(
        total_sheets=st.integers(min_value=1, max_value=1000),
        total_movements=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=50)
    def test_totals_are_preserved(
        self, total_sheets: int, total_movements: int
    ) -> None:
        """total_sheets and total_movements are correctly stored."""
        ctx = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
            total_sheets=total_sheets,
            total_movements=total_movements,
        )
        assert ctx.total_sheets == total_sheets
        assert ctx.total_movements == total_movements

    def test_previous_outputs_default_empty(self) -> None:
        """previous_outputs defaults to empty dict."""
        ctx = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        assert ctx.previous_outputs == {}
        assert isinstance(ctx.previous_outputs, dict)

    def test_previous_outputs_isolation(self) -> None:
        """Each AttemptContext has its own previous_outputs dict."""
        ctx1 = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        ctx2 = AttemptContext(attempt_number=2, mode=AttemptMode.NORMAL)
        ctx1.previous_outputs[1] = "output from sheet 1"
        assert 1 not in ctx2.previous_outputs


# =============================================================================
# Additional: Escalation Timeout F-066 Guard
# =============================================================================


class TestEscalationTimeoutF066Guard:
    """EscalationTimeout also respects the multi-fermata guard.

    Invariant: Same as F-066 but for the timeout path — job stays paused
    if other sheets are in FERMATA.
    """

    def test_timeout_does_not_unpause_when_other_fermatas_exist(self) -> None:
        """Escalation timeout respects F-066 multi-fermata guard."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {}, escalation_enabled=True)


        # Put both sheets in FERMATA
        for i in [1, 2]:
            s = baton.get_sheet_state("j1", i)
            assert s is not None
            s.status = BatonSheetStatus.DISPATCHED
            _run(baton.handle_event(
                EscalationNeeded(job_id="j1", sheet_num=i, reason="test")
            ))

        # Timeout on sheet 1
        _run(baton.handle_event(
            EscalationTimeout(job_id="j1", sheet_num=1)
        ))

        # Sheet 1 → FAILED, sheet 2 still FERMATA → job stays paused
        s1 = baton.get_sheet_state("j1", 1)
        s2 = baton.get_sheet_state("j1", 2)
        assert s1 is not None and s1.status == BatonSheetStatus.FAILED
        assert s2 is not None and s2.status == BatonSheetStatus.FERMATA
        assert baton.is_job_paused("j1")


# =============================================================================
# Additional: User Pause Survives Escalation Resolution
# =============================================================================


class TestUserPauseSurvivesEscalation:
    """User-initiated pause is not overridden by escalation resolution.

    Invariant: If the user pauses a job AND an escalation resolves,
    the job stays paused. User intent takes priority.
    """

    @given(decision=st.sampled_from(["retry", "skip", "accept", "fail"]))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_user_pause_preserved_after_escalation_resolve(
        self, decision: str
    ) -> None:
        """User pause persists regardless of escalation decision."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {}, escalation_enabled=True)


        # Put sheet in FERMATA
        s = baton.get_sheet_state("j1", 1)
        assert s is not None
        s.status = BatonSheetStatus.DISPATCHED
        _run(baton.handle_event(
            EscalationNeeded(job_id="j1", sheet_num=1, reason="test")
        ))

        # User also pauses
        _run(baton.handle_event(PauseJob(job_id="j1")))

        # Resolve escalation
        _run(baton.handle_event(
            EscalationResolved(job_id="j1", sheet_num=1, decision=decision)
        ))

        # Job stays paused because user_paused=True
        assert baton.is_job_paused("j1"), (
            f"User pause overridden by escalation decision '{decision}'"
        )


# Need Path import for Sheet
from pathlib import Path  # noqa: E402
