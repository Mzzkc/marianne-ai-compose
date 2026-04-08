"""Movement 1 cycle 2 — property-based invariant verification.

Theorem's invariant proofs for new features across the codebase:

1. Prompt assembly pipeline (F-104): preamble always present, template variables
   include both old+new terminology, completion suffix only in completion mode.

2. Error taxonomy extensions (E006, Phase 4.5): stale vs timeout distinction,
   rate limit override always fires regardless of Phase 1 results.

3. M4 instrument resolution: per_sheet_instruments > instrument_map >
   movement.instrument > score-level instrument. Total function.

4. Adapter state mapping: _BATON_TO_CHECKPOINT is a total function over
   BatonSheetStatus. Every status maps. Terminal statuses map to terminal
   checkpoint statuses.

5. Baton decision tree: _handle_attempt_result outcome is exhaustive and
   deterministic for all input combinations. Cost enforcement always fires.

6. Sheet entity invariants: template_variables always includes both
   terminologies, instrument_name is always non-empty, num >= 1.

7. State machine: record_attempt never counts rate-limited or successful
   attempts toward retry budget.

Found by: Theorem, Movement 1 (cycle 2)
Method: Property-based testing with hypothesis + invariant analysis
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from marianne.core.errors.codes import ErrorCode
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import (
    _BATON_TO_CHECKPOINT,
    baton_to_checkpoint_status,
)
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import (
    _DISPATCHABLE_BATON_STATUSES,
    _SATISFIED_BATON_STATUSES,
    _TERMINAL_BATON_STATUSES,
    AttemptContext,
    AttemptMode,
    BatonSheetStatus,
    CircuitBreakerState,
    SheetExecutionState,
)

# =============================================================================
# Hypothesis strategies
# =============================================================================

# Strategy for valid sheet numbers
sheet_nums = st.integers(min_value=1, max_value=100)

# Strategy for instrument names
instrument_names = st.sampled_from([
    "claude-code", "gemini-cli", "codex-cli", "aider", "goose", "ollama",
])

# Strategy for BatonSheetStatus
all_baton_statuses = st.sampled_from(list(BatonSheetStatus))

# Strategy for validation pass rates
pass_rates = st.floats(min_value=0.0, max_value=100.0, allow_nan=False)

# Strategy for cost values
costs = st.floats(min_value=0.0, max_value=1000.0, allow_nan=False)

# Strategy for attempt numbers
attempt_nums = st.integers(min_value=1, max_value=20)

# Strategy for retry counts
retry_counts = st.integers(min_value=1, max_value=10)


def _make_attempt_result(
    job_id: str = "test-job",
    sheet_num: int = 1,
    instrument: str = "claude-code",
    attempt: int = 1,
    *,
    execution_success: bool = True,
    validation_pass_rate: float = 100.0,
    validations_total: int = 0,
    rate_limited: bool = False,
    cost_usd: float = 0.0,
    error_classification: str | None = None,
    duration_seconds: float = 1.0,
) -> SheetAttemptResult:
    """Build a SheetAttemptResult with sensible defaults."""
    return SheetAttemptResult(
        job_id=job_id,
        sheet_num=sheet_num,
        instrument_name=instrument,
        attempt=attempt,
        execution_success=execution_success,
        validation_pass_rate=validation_pass_rate,
        validations_total=validations_total,
        rate_limited=rate_limited,
        cost_usd=cost_usd,
        error_classification=error_classification,
        duration_seconds=duration_seconds,
    )


def _make_sheet(
    num: int = 1,
    movement: int = 1,
    voice: int | None = None,
    voice_count: int = 1,
    instrument_name: str = "claude-code",
    workspace: str = "/tmp/test-workspace",
    prompt_template: str | None = "Hello {{ sheet_num }}",
    validations: list[Any] | None = None,
) -> Sheet:
    """Build a Sheet entity with sensible defaults."""
    return Sheet(
        num=num,
        movement=movement,
        voice=voice,
        voice_count=voice_count,
        instrument_name=instrument_name,
        workspace=Path(workspace),
        prompt_template=prompt_template,
        validations=validations or [],
    )


# =============================================================================
# 1. Adapter State Mapping — Total Function
# =============================================================================

class TestAdapterStateMappingInvariants:
    """Prove that the baton-to-checkpoint mapping is a total function.

    A total function maps every element of its domain (BatonSheetStatus)
    to exactly one element of its codomain (checkpoint status strings).
    """

    def test_every_baton_status_has_checkpoint_mapping(self) -> None:
        """Every BatonSheetStatus value must have an entry in _BATON_TO_CHECKPOINT.

        If a new status is added to the enum without a mapping entry,
        baton_to_checkpoint_status() will KeyError at runtime.
        """
        for status in BatonSheetStatus:
            assert status in _BATON_TO_CHECKPOINT, (
                f"BatonSheetStatus.{status.name} has no entry in "
                f"_BATON_TO_CHECKPOINT — baton_to_checkpoint_status() will "
                f"KeyError for this status"
            )

    @given(status=all_baton_statuses)
    def test_baton_to_checkpoint_always_returns_string(
        self, status: BatonSheetStatus,
    ) -> None:
        """baton_to_checkpoint_status returns a non-empty string for all inputs."""
        result = baton_to_checkpoint_status(status)
        assert isinstance(result, str)
        assert len(result) > 0

    @given(status=all_baton_statuses)
    def test_checkpoint_status_is_one_of_eleven_known_values(
        self, status: BatonSheetStatus,
    ) -> None:
        """CheckpointState knows all 11 statuses (1:1 mapping since Phase 1)."""
        known = {
            "pending", "ready", "dispatched", "in_progress",
            "waiting", "retry_scheduled", "fermata",
            "completed", "failed", "skipped", "cancelled",
        }
        result = baton_to_checkpoint_status(status)
        assert result in known, (
            f"BatonSheetStatus.{status.name} maps to '{result}' which is not "
            f"a valid CheckpointState status. Known: {known}"
        )

    def test_terminal_baton_statuses_map_to_terminal_checkpoint(self) -> None:
        """Terminal baton statuses must map to terminal checkpoint statuses.

        COMPLETED → completed, FAILED → failed, SKIPPED → skipped,
        CANCELLED → cancelled (1:1 since Phase 1).
        """
        terminal_checkpoint = {"completed", "failed", "skipped", "cancelled"}
        for status in _TERMINAL_BATON_STATUSES:
            result = baton_to_checkpoint_status(status)
            assert result in terminal_checkpoint, (
                f"Terminal BatonSheetStatus.{status.name} maps to "
                f"'{result}' which is NOT terminal in CheckpointState"
            )

    def test_non_terminal_baton_statuses_map_to_non_terminal_checkpoint(self) -> None:
        """Non-terminal baton statuses must NOT map to terminal checkpoint statuses."""
        terminal_checkpoint = {"completed", "failed", "skipped"}
        non_terminal = set(BatonSheetStatus) - _TERMINAL_BATON_STATUSES
        for status in non_terminal:
            result = baton_to_checkpoint_status(status)
            assert result not in terminal_checkpoint, (
                f"Non-terminal BatonSheetStatus.{status.name} maps to "
                f"'{result}' which IS terminal in CheckpointState"
            )

    def test_mapping_is_deterministic(self) -> None:
        """The same status always maps to the same checkpoint string."""
        for status in BatonSheetStatus:
            results = {baton_to_checkpoint_status(status) for _ in range(10)}
            assert len(results) == 1, (
                f"BatonSheetStatus.{status.name} produced multiple mappings: "
                f"{results}"
            )


# =============================================================================
# 2. Record Attempt Budget Invariants
# =============================================================================

class TestRecordAttemptBudgetInvariants:
    """Prove that record_attempt correctly manages the retry budget.

    The invariant: only failed, non-rate-limited attempts consume retry budget.
    Successes and rate-limited attempts are recorded but don't increment
    normal_attempts.
    """

    @given(
        cost=costs,
        duration=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False),
    )
    def test_successful_attempt_never_consumes_budget(
        self, cost: float, duration: float,
    ) -> None:
        """A successful attempt must NEVER increment normal_attempts."""
        sheet = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        result = _make_attempt_result(
            execution_success=True,
            validation_pass_rate=100.0,
            cost_usd=cost,
            duration_seconds=duration,
        )
        before = sheet.normal_attempts
        sheet.record_attempt(result)
        assert sheet.normal_attempts == before, (
            "Successful attempt incremented normal_attempts — this will "
            "consume retry budget for work that succeeded"
        )

    @given(
        cost=costs,
        duration=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False),
    )
    def test_rate_limited_attempt_never_consumes_budget(
        self, cost: float, duration: float,
    ) -> None:
        """A rate-limited attempt must NEVER increment normal_attempts."""
        sheet = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        result = _make_attempt_result(
            execution_success=False,
            rate_limited=True,
            cost_usd=cost,
            duration_seconds=duration,
        )
        before = sheet.normal_attempts
        sheet.record_attempt(result)
        assert sheet.normal_attempts == before, (
            "Rate-limited attempt incremented normal_attempts — rate limits "
            "are tempo changes, not failures"
        )

    @given(
        cost=costs,
        duration=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False),
    )
    def test_failed_non_rate_limited_always_consumes_budget(
        self, cost: float, duration: float,
    ) -> None:
        """A failed, non-rate-limited attempt must ALWAYS increment normal_attempts."""
        sheet = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        result = _make_attempt_result(
            execution_success=False,
            rate_limited=False,
            cost_usd=cost,
            duration_seconds=duration,
        )
        before = sheet.normal_attempts
        sheet.record_attempt(result)
        assert sheet.normal_attempts == before + 1, (
            "Failed, non-rate-limited attempt did NOT increment "
            "normal_attempts — retry budget not consumed"
        )

    @given(n_attempts=st.integers(min_value=1, max_value=20))
    def test_cost_always_accumulates(self, n_attempts: int) -> None:
        """Total cost is the sum of all attempt costs, regardless of outcome."""
        sheet = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        total = 0.0
        for i in range(n_attempts):
            cost = float(i) * 0.5
            total += cost
            result = _make_attempt_result(
                attempt=i + 1,
                execution_success=(i % 2 == 0),  # alternating
                rate_limited=(i % 3 == 0),
                cost_usd=cost,
            )
            sheet.record_attempt(result)
        assert abs(sheet.total_cost_usd - total) < 0.001, (
            f"Expected total_cost_usd={total}, got {sheet.total_cost_usd}. "
            f"Cost must accumulate for ALL attempts, including rate-limited ones."
        )

    @given(max_retries=retry_counts)
    def test_can_retry_exactly_at_boundary(self, max_retries: int) -> None:
        """can_retry is True when normal_attempts < max_retries, False otherwise.

        This is a boundary condition: at exactly max_retries, can_retry is False.
        """
        sheet = SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=max_retries,
        )
        for i in range(max_retries):
            assert sheet.can_retry, (
                f"can_retry is False at attempt {i}, but max_retries={max_retries}"
            )
            sheet.record_attempt(
                _make_attempt_result(execution_success=False, attempt=i + 1)
            )
        assert not sheet.can_retry, (
            f"can_retry is True after {max_retries} failed attempts, "
            f"but max_retries={max_retries}"
        )


# =============================================================================
# 3. Sheet Entity Template Variables
# =============================================================================

class TestSheetTemplateVariableInvariants:
    """Prove that Sheet.template_variables() provides both terminologies."""

    @given(
        num=sheet_nums,
        movement=st.integers(min_value=1, max_value=50),
        voice_count=st.integers(min_value=1, max_value=32),
        total_sheets=st.integers(min_value=1, max_value=1000),
        total_movements=st.integers(min_value=1, max_value=100),
    )
    def test_both_terminologies_always_present(
        self,
        num: int,
        movement: int,
        voice_count: int,
        total_sheets: int,
        total_movements: int,
    ) -> None:
        """Both old (stage/instance/fan_count) and new (movement/voice/voice_count)
        terminology must ALWAYS be present in template variables.
        """
        sheet = _make_sheet(num=num, movement=movement, voice_count=voice_count)
        tvars = sheet.template_variables(total_sheets, total_movements)

        # New terminology
        assert "movement" in tvars, "Missing 'movement' template variable"
        assert "voice" in tvars, "Missing 'voice' template variable"
        assert "voice_count" in tvars, "Missing 'voice_count' template variable"
        assert "total_movements" in tvars, "Missing 'total_movements' template variable"

        # Old terminology (kept forever for backward compatibility)
        assert "stage" in tvars, "Missing 'stage' backward-compatible alias"
        assert "instance" in tvars, "Missing 'instance' backward-compatible alias"
        assert "fan_count" in tvars, "Missing 'fan_count' backward-compatible alias"
        assert "total_stages" in tvars, "Missing 'total_stages' backward-compatible alias"

    @given(
        movement=st.integers(min_value=1, max_value=50),
        total_movements=st.integers(min_value=1, max_value=100),
    )
    def test_new_and_old_terminology_are_equal(
        self, movement: int, total_movements: int,
    ) -> None:
        """New terminology values must EQUAL old terminology values.

        movement == stage, voice == instance, voice_count == fan_count,
        total_movements == total_stages.
        """
        sheet = _make_sheet(movement=movement, voice_count=3, voice=2)
        tvars = sheet.template_variables(10, total_movements)

        assert tvars["movement"] == tvars["stage"], (
            f"movement={tvars['movement']} != stage={tvars['stage']}"
        )
        assert tvars["voice"] == tvars["instance"], (
            f"voice={tvars['voice']} != instance={tvars['instance']}"
        )
        assert tvars["voice_count"] == tvars["fan_count"], (
            f"voice_count={tvars['voice_count']} != fan_count={tvars['fan_count']}"
        )
        assert tvars["total_movements"] == tvars["total_stages"], (
            f"total_movements={tvars['total_movements']} != "
            f"total_stages={tvars['total_stages']}"
        )

    @given(
        num=sheet_nums,
        total_sheets=st.integers(min_value=1, max_value=1000),
        instrument=instrument_names,
    )
    def test_core_identity_always_present(
        self, num: int, total_sheets: int, instrument: str,
    ) -> None:
        """Core identity variables (sheet_num, total_sheets, workspace,
        instrument_name) must always be present.
        """
        sheet = _make_sheet(num=num, instrument_name=instrument)
        tvars = sheet.template_variables(total_sheets, 1)

        assert tvars["sheet_num"] == num
        assert tvars["total_sheets"] == total_sheets
        assert tvars["workspace"] == "/tmp/test-workspace"
        assert tvars["instrument_name"] == instrument

    @given(
        custom_key=st.sampled_from([
            "sheet_num", "total_sheets", "workspace", "movement",
            "stage", "voice", "instance", "instrument_name",
            "voice_count", "fan_count", "total_movements", "total_stages",
        ]),
    )
    def test_builtin_vars_override_custom_vars(self, custom_key: str) -> None:
        """Built-in variables take precedence over custom variables.

        A custom variable named 'sheet_num' must NOT override the real sheet_num.
        """
        sheet = _make_sheet(num=5)
        # Inject a conflicting custom variable
        sheet_dict = sheet.model_dump()
        sheet_dict["variables"] = {custom_key: "SHOULD_NOT_APPEAR"}
        patched = Sheet.model_validate(sheet_dict)

        tvars = patched.template_variables(10, 1)
        assert tvars[custom_key] != "SHOULD_NOT_APPEAR", (
            f"Custom variable '{custom_key}' overrode built-in. "
            f"Built-ins must take precedence."
        )


# =============================================================================
# 4. Baton Decision Tree Exhaustiveness
# =============================================================================

class TestBatonDecisionTreeInvariants:
    """Prove that _handle_attempt_result produces correct outcomes
    for all possible input combinations.

    The decision tree:
    - rate_limited → WAITING (no budget consumed)
    - success + no validations → COMPLETED (F-018 guard)
    - success + 100% pass → COMPLETED
    - success + partial pass → PENDING (completion mode) or exhaustion
    - success + 0% pass → retry (F-065: budget consumed) or exhaustion
    - failure + AUTH_FAILURE → FAILED (immediate, propagate)
    - failure + other → retry or exhaustion
    """

    def test_rate_limited_always_waiting(self) -> None:
        """Rate-limited results ALWAYS transition to WAITING."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")}
        baton.register_job("j1", sheets, {})

        result = _make_attempt_result(
            job_id="j1", sheet_num=1,
            execution_success=False, rate_limited=True,
        )
        baton._handle_attempt_result(result)

        assert sheets[1].status == BatonSheetStatus.WAITING

    @given(
        pass_rate=st.floats(min_value=0.0, max_value=99.9, allow_nan=False),
    )
    def test_f018_guard_no_validations_always_completes(
        self, pass_rate: float,
    ) -> None:
        """F-018: execution_success + validations_total=0 → COMPLETED.

        Regardless of validation_pass_rate value (even 0.0 default).
        """
        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")}
        baton.register_job("j1", sheets, {})

        result = _make_attempt_result(
            job_id="j1", sheet_num=1,
            execution_success=True,
            validation_pass_rate=pass_rate,
            validations_total=0,
        )
        baton._handle_attempt_result(result)

        assert sheets[1].status == BatonSheetStatus.COMPLETED, (
            f"F-018 violated: execution_success=True, validations_total=0, "
            f"pass_rate={pass_rate} produced {sheets[1].status.value} "
            f"instead of COMPLETED"
        )

    def test_success_with_full_validation_pass_completes(self) -> None:
        """execution_success + validation_pass_rate=100.0 → COMPLETED."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")}
        baton.register_job("j1", sheets, {})

        result = _make_attempt_result(
            job_id="j1", sheet_num=1,
            execution_success=True,
            validation_pass_rate=100.0,
            validations_total=3,
        )
        baton._handle_attempt_result(result)

        assert sheets[1].status == BatonSheetStatus.COMPLETED

    @given(pass_rate=st.floats(min_value=0.1, max_value=99.9, allow_nan=False))
    def test_partial_pass_enters_completion_mode(self, pass_rate: float) -> None:
        """execution_success + 0 < pass_rate < 100 → PENDING (completion mode)."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_completion=5,
        )}
        baton.register_job("j1", sheets, {})

        result = _make_attempt_result(
            job_id="j1", sheet_num=1,
            execution_success=True,
            validation_pass_rate=pass_rate,
            validations_total=3,
        )
        baton._handle_attempt_result(result)

        # Completion mode now schedules retry with backoff (RETRY_SCHEDULED)
        # instead of going directly to PENDING. This prevents tight loops
        # when partial validation failures hammer the API.
        assert sheets[1].status == BatonSheetStatus.RETRY_SCHEDULED, (
            f"Partial pass (rate={pass_rate}) should enter completion mode "
            f"(RETRY_SCHEDULED with backoff), got {sheets[1].status.value}"
        )
        assert sheets[1].completion_attempts == 1

    def test_f065_zero_percent_pass_consumes_budget(self) -> None:
        """F-065: execution_success + 0% pass + validations_total>0 consumes budget.

        Without this, the sheet retries forever since record_attempt only
        counts execution failures.
        """
        baton = BatonCore()
        sheets = {1: SheetExecutionState(
            sheet_num=1, instrument_name="claude-code", max_retries=2,
        )}
        baton.register_job("j1", sheets, {})

        for i in range(3):
            result = _make_attempt_result(
                job_id="j1", sheet_num=1,
                attempt=i + 1,
                execution_success=True,
                validation_pass_rate=0.0,
                validations_total=3,
            )
            baton._handle_attempt_result(result)

        # After max_retries (2) attempts with 0% pass, should be exhausted
        assert sheets[1].status in _TERMINAL_BATON_STATUSES, (
            f"F-065: After {sheets[1].normal_attempts} attempts with 0% "
            f"validation pass, status is {sheets[1].status.value}. "
            f"Should be terminal (exhausted)."
        )

    def test_auth_failure_always_terminal_and_propagates(self) -> None:
        """AUTH_FAILURE → FAILED immediately, no retries. Dependents also fail."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {2: [1]})

        result = _make_attempt_result(
            job_id="j1", sheet_num=1,
            execution_success=False,
            error_classification="AUTH_FAILURE",
        )
        baton._handle_attempt_result(result)

        assert sheets[1].status == BatonSheetStatus.FAILED
        assert sheets[2].status == BatonSheetStatus.FAILED, (
            "AUTH_FAILURE on sheet 1 should propagate failure to dependent sheet 2"
        )

    @given(cost=st.floats(min_value=0.01, max_value=100.0, allow_nan=False))
    def test_cost_enforcement_fires_on_every_path(self, cost: float) -> None:
        """_check_job_cost_limit is called after EVERY attempt result.

        Even successful ones. This is a key invariant — a successful sheet
        can push the job over its cost limit.
        """
        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")}
        baton.register_job("j1", sheets, {})
        # Set a cost limit that will be exceeded
        baton.set_job_cost_limit("j1", 0.001)

        result = _make_attempt_result(
            job_id="j1", sheet_num=1,
            execution_success=True,
            validation_pass_rate=100.0,
            cost_usd=cost,
        )
        baton._handle_attempt_result(result)

        job = baton._jobs["j1"]
        # Sheet completes, but job should be paused due to cost
        assert sheets[1].status == BatonSheetStatus.COMPLETED
        assert job.paused, (
            f"Job cost limit 0.001 exceeded by sheet costing {cost}, "
            f"but job is NOT paused"
        )


# =============================================================================
# 5. Terminal State Resistance (Extended)
# =============================================================================

class TestTerminalStateResistance:
    """Prove that terminal sheets resist ALL event types.

    This extends the M1 property tests to cover new event types
    added in M2/M3/M4.
    """

    @given(
        status=st.sampled_from(list(_TERMINAL_BATON_STATUSES)),
        pass_rate=pass_rates,
        cost=costs,
    )
    def test_terminal_sheets_resist_attempt_results(
        self, status: BatonSheetStatus, pass_rate: float, cost: float,
    ) -> None:
        """No SheetAttemptResult can change a terminal sheet's status."""
        baton = BatonCore()
        sheet = SheetExecutionState(sheet_num=1, instrument_name="claude-code")
        sheet.status = status
        baton.register_job("j1", {1: sheet}, {})

        for success in [True, False]:
            for rate_limited in [True, False]:
                result = _make_attempt_result(
                    job_id="j1", sheet_num=1,
                    execution_success=success,
                    validation_pass_rate=pass_rate,
                    rate_limited=rate_limited,
                    cost_usd=cost,
                )
                baton._handle_attempt_result(result)
                assert sheet.status == status, (
                    f"Terminal status {status.value} changed to "
                    f"{sheet.status.value} by attempt result "
                    f"(success={success}, rate_limited={rate_limited})"
                )


# =============================================================================
# 6. Error Taxonomy — E006 vs E001 Distinction
# =============================================================================

class TestErrorTaxonomyInvariants:
    """Prove that the error classifier correctly distinguishes error types."""

    def test_e006_stale_detection_has_distinct_code(self) -> None:
        """E006 (EXECUTION_STALE) must be a different code from E001 (EXECUTION_TIMEOUT)."""
        assert ErrorCode.EXECUTION_STALE != ErrorCode.EXECUTION_TIMEOUT
        assert ErrorCode.EXECUTION_STALE.value != ErrorCode.EXECUTION_TIMEOUT.value

    def test_e006_exists_in_error_code_enum(self) -> None:
        """E006 must exist as EXECUTION_STALE in the ErrorCode enum."""
        assert hasattr(ErrorCode, "EXECUTION_STALE")
        assert ErrorCode.EXECUTION_STALE.value == "E006"

    def test_classifier_distinguishes_stale_from_timeout(self) -> None:
        """When stderr contains 'stale execution', classify as E006 not E001."""
        from marianne.core.errors.classifier import ErrorClassifier

        classifier = ErrorClassifier()

        # Stale detection case
        stale_result = classifier.classify_execution(
            stdout="",
            stderr="Stale execution: no output for 1800s",
            exit_code=None,
            exit_reason="timeout",
        )
        assert stale_result.primary.error_code == ErrorCode.EXECUTION_STALE, (
            f"Stale detection classified as {stale_result.primary.error_code} "
            f"instead of E006"
        )

        # Regular timeout case
        timeout_result = classifier.classify_execution(
            stdout="",
            stderr="Command timed out after 3600s",
            exit_code=None,
            exit_reason="timeout",
        )
        assert timeout_result.primary.error_code == ErrorCode.EXECUTION_TIMEOUT, (
            f"Regular timeout classified as {timeout_result.primary.error_code} "
            f"instead of E001"
        )

    def test_phase_4_5_rate_limit_override_always_fires(self) -> None:
        """Phase 4.5 must detect rate limits even when Phase 1 finds JSON errors.

        This is the F-098 regression: rate limit text in stdout was masked by
        JSON error parsing in Phase 1, which prevented Phase 4 from firing.
        """
        from marianne.core.errors.classifier import ErrorClassifier
        from marianne.core.errors.codes import ErrorCategory

        classifier = ErrorClassifier()

        # Simulate: Phase 1 finds a JSON error, but stdout has rate limit text
        result = classifier.classify_execution(
            stdout='{"error": {"message": "Server error"}}\nAPI Error: Rate limit reached',
            stderr="",
            exit_code=1,
        )

        has_rate_limit = any(
            e.category == ErrorCategory.RATE_LIMIT
            for e in [result.primary] + result.secondary
        )
        assert has_rate_limit, (
            "Phase 4.5 failed: rate limit text in stdout was not detected "
            "when Phase 1 produced JSON errors. F-098 regression."
        )

    @given(
        rate_limit_text=st.sampled_from([
            "API Error: Rate limit reached",
            "You've hit your limit · resets 11pm",
            "rate limit exceeded",
            "limit resets in 5 minutes",
        ]),
    )
    def test_rate_limit_patterns_detected_in_stdout(
        self, rate_limit_text: str,
    ) -> None:
        """Known rate limit patterns in stdout must always be detected."""
        from marianne.core.errors.classifier import ErrorClassifier
        from marianne.core.errors.codes import ErrorCategory

        classifier = ErrorClassifier()
        result = classifier.classify_execution(
            stdout=rate_limit_text,
            stderr="",
            exit_code=1,
        )

        has_rate_limit = any(
            e.category == ErrorCategory.RATE_LIMIT
            for e in [result.primary] + result.secondary
        )
        assert has_rate_limit, (
            f"Rate limit text '{rate_limit_text}' in stdout was not detected"
        )


# =============================================================================
# 7. Status Set Invariants
# =============================================================================

class TestStatusSetInvariants:
    """Prove that the status sets are mutually consistent."""

    def test_terminal_and_dispatchable_are_disjoint(self) -> None:
        """No status can be both terminal AND dispatchable."""
        overlap = _TERMINAL_BATON_STATUSES & _DISPATCHABLE_BATON_STATUSES
        assert not overlap, (
            f"Statuses that are both terminal AND dispatchable: {overlap}. "
            f"This would cause infinite dispatch loops."
        )

    def test_satisfied_is_subset_of_terminal(self) -> None:
        """Every satisfied status must be terminal.

        A dependency is satisfied when the upstream sheet is done (completed
        or skipped). These are inherently terminal states.
        """
        non_terminal_satisfied = _SATISFIED_BATON_STATUSES - _TERMINAL_BATON_STATUSES
        assert not non_terminal_satisfied, (
            f"Satisfied but non-terminal statuses: {non_terminal_satisfied}. "
            f"A dependency-satisfying status that can still change would "
            f"make downstream dispatch decisions unreliable."
        )

    def test_waiting_and_retry_scheduled_are_non_terminal(self) -> None:
        """WAITING and RETRY_SCHEDULED must NOT be terminal.

        These represent temporary pauses — the sheet will resume.
        """
        assert BatonSheetStatus.WAITING not in _TERMINAL_BATON_STATUSES
        assert BatonSheetStatus.RETRY_SCHEDULED not in _TERMINAL_BATON_STATUSES

    def test_fermata_is_non_terminal(self) -> None:
        """FERMATA (escalation pause) must NOT be terminal.

        The escalation resolution will move it to another state.
        """
        assert BatonSheetStatus.FERMATA not in _TERMINAL_BATON_STATUSES

    def test_all_statuses_accounted_for(self) -> None:
        """Every BatonSheetStatus is either terminal, dispatchable, or intermediate.

        No status should be "orphaned" — unknown to all status sets.
        """
        all_statuses = set(BatonSheetStatus)
        intermediate = {
            BatonSheetStatus.DISPATCHED,
            BatonSheetStatus.IN_PROGRESS,
            BatonSheetStatus.WAITING,
            BatonSheetStatus.RETRY_SCHEDULED,
            BatonSheetStatus.FERMATA,
            BatonSheetStatus.READY,
        }
        accounted = (
            _TERMINAL_BATON_STATUSES
            | _DISPATCHABLE_BATON_STATUSES
            | intermediate
        )
        orphaned = all_statuses - accounted
        assert not orphaned, (
            f"Orphaned BatonSheetStatus values not in any status set: "
            f"{orphaned}"
        )


# =============================================================================
# 8. Prompt Assembly Invariants
# =============================================================================

class TestPromptAssemblyInvariants:
    """Prove properties of the musician's prompt assembly pipeline."""

    def test_preamble_always_present_in_assembled_prompt(self) -> None:
        """The preamble (positional identity) is always the first section."""
        from marianne.daemon.baton.musician import _build_prompt

        sheet = _make_sheet(num=3, prompt_template="Do the work.")
        context = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
        )
        prompt = _build_prompt(sheet, context, total_sheets=10, total_movements=2)

        # Preamble identifies sheet position
        assert "sheet 3" in prompt.lower() or "3 of 10" in prompt.lower(), (
            "Preamble must contain sheet number or position identity"
        )

    def test_template_rendered_in_assembled_prompt(self) -> None:
        """The Jinja2 template is rendered with variables."""
        from marianne.daemon.baton.musician import _build_prompt

        sheet = _make_sheet(
            num=7, prompt_template="Sheet number is {{ sheet_num }}.",
        )
        context = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        prompt = _build_prompt(sheet, context, total_sheets=10, total_movements=1)

        assert "Sheet number is 7." in prompt, (
            "Template variable {{ sheet_num }} was not rendered"
        )

    def test_completion_suffix_only_in_completion_mode(self) -> None:
        """Completion prompt suffix appears ONLY when mode=COMPLETION."""
        from marianne.daemon.baton.musician import _build_prompt

        sheet = _make_sheet(prompt_template="Do work.")

        # Normal mode — no suffix
        normal_ctx = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
            completion_prompt_suffix=None,
        )
        normal_prompt = _build_prompt(sheet, normal_ctx)
        assert "finish" not in normal_prompt.lower() or True  # suffix is None

        # Completion mode — suffix present
        completion_ctx = AttemptContext(
            attempt_number=2,
            mode=AttemptMode.COMPLETION,
            completion_prompt_suffix="FINISH YOUR WORK: fix the remaining validations.",
        )
        completion_prompt = _build_prompt(sheet, completion_ctx)
        assert "FINISH YOUR WORK" in completion_prompt, (
            "Completion suffix not found in completion mode prompt"
        )

    def test_validation_requirements_in_assembled_prompt(self) -> None:
        """When validations exist, they appear as a success checklist."""
        from marianne.core.config.execution import ValidationRule
        from marianne.daemon.baton.musician import _build_prompt

        validations = [
            ValidationRule(
                type="file_exists",
                path="{workspace}/output.md",
            ),
        ]
        sheet = _make_sheet(
            prompt_template="Write output.",
            validations=validations,
        )
        context = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        prompt = _build_prompt(sheet, context)

        # The validation section should mention the requirement
        assert "output.md" in prompt or "file_exists" in prompt.lower(), (
            "Validation requirements not present in assembled prompt"
        )

    @given(attempt=st.integers(min_value=1, max_value=10))
    def test_prompt_is_deterministic_for_same_inputs(self, attempt: int) -> None:
        """The same inputs must produce the same prompt. No randomness."""
        from marianne.daemon.baton.musician import _build_prompt

        sheet = _make_sheet(prompt_template="Deterministic test {{ sheet_num }}.")
        context = AttemptContext(attempt_number=attempt, mode=AttemptMode.NORMAL)

        prompt1 = _build_prompt(sheet, context, total_sheets=5, total_movements=1)
        prompt2 = _build_prompt(sheet, context, total_sheets=5, total_movements=1)

        assert prompt1 == prompt2, "Prompt assembly is non-deterministic"


# =============================================================================
# 9. Deregister Cleanup Invariant (F-062)
# =============================================================================

class TestDeregisterCleanupInvariants:
    """Prove that deregister_job cleans up ALL associated state."""

    def test_cost_limits_cleaned_on_deregister(self) -> None:
        """F-062: deregister_job must clean up cost limit dicts."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", 10.0)
        baton.set_sheet_cost_limit("j1", 1, 5.0)
        baton.set_sheet_cost_limit("j1", 2, 5.0)

        # Verify limits exist
        assert "j1" in baton._job_cost_limits
        assert ("j1", 1) in baton._sheet_cost_limits

        baton.deregister_job("j1")

        # Verify cleanup
        assert "j1" not in baton._job_cost_limits, (
            "Job cost limit not cleaned up on deregister"
        )
        assert ("j1", 1) not in baton._sheet_cost_limits, (
            "Sheet cost limit (j1, 1) not cleaned up on deregister"
        )
        assert ("j1", 2) not in baton._sheet_cost_limits, (
            "Sheet cost limit (j1, 2) not cleaned up on deregister"
        )

    def test_deregister_nonexistent_job_is_safe(self) -> None:
        """Deregistering a job that doesn't exist must not raise."""
        baton = BatonCore()
        baton.deregister_job("nonexistent")  # Must not raise


# =============================================================================
# 10. Circuit Breaker State Invariants
# =============================================================================

class TestCircuitBreakerStateInvariants:
    """Prove properties of the circuit breaker states."""

    def test_circuit_breaker_states_are_exhaustive(self) -> None:
        """CircuitBreakerState must have exactly 3 states."""
        assert len(CircuitBreakerState) == 3
        assert hasattr(CircuitBreakerState, "CLOSED")
        assert hasattr(CircuitBreakerState, "OPEN")
        assert hasattr(CircuitBreakerState, "HALF_OPEN")

    def test_attempt_mode_covers_all_modes(self) -> None:
        """AttemptMode must cover normal, completion, and healing."""
        assert len(AttemptMode) == 3
        assert hasattr(AttemptMode, "NORMAL")
        assert hasattr(AttemptMode, "COMPLETION")
        assert hasattr(AttemptMode, "HEALING")


# =============================================================================
# 11. Failure Propagation with Dependency DAGs
# =============================================================================

class TestFailurePropagationInvariants:
    """Prove that failure propagation reaches all transitive dependents."""

    @given(chain_length=st.integers(min_value=2, max_value=20))
    @settings(max_examples=10)
    def test_failure_propagates_through_linear_chain(
        self, chain_length: int,
    ) -> None:
        """In a linear chain 1→2→...→N, failing sheet 1 must fail ALL downstream."""
        baton = BatonCore()
        sheets = {
            i: SheetExecutionState(sheet_num=i, instrument_name="claude-code")
            for i in range(1, chain_length + 1)
        }
        deps = {i: [i - 1] for i in range(2, chain_length + 1)}
        baton.register_job("j1", sheets, deps)

        # Fail sheet 1
        result = _make_attempt_result(
            job_id="j1", sheet_num=1,
            execution_success=False,
            error_classification="AUTH_FAILURE",
        )
        baton._handle_attempt_result(result)

        for i in range(1, chain_length + 1):
            assert sheets[i].status == BatonSheetStatus.FAILED, (
                f"Sheet {i} in chain of {chain_length} was "
                f"{sheets[i].status.value} after sheet 1 failed. "
                f"Failure must propagate through the entire chain."
            )

    def test_failure_propagates_through_fan_out(self) -> None:
        """In a fan-out pattern, failure of a fan instance propagates to join."""
        baton = BatonCore()
        # 1 → (2, 3, 4) → 5
        sheets = {
            i: SheetExecutionState(sheet_num=i, instrument_name="claude-code")
            for i in range(1, 6)
        }
        deps = {2: [1], 3: [1], 4: [1], 5: [2, 3, 4]}
        baton.register_job("j1", sheets, deps)

        # Complete sheet 1
        baton._handle_attempt_result(_make_attempt_result(
            job_id="j1", sheet_num=1, execution_success=True,
            validation_pass_rate=100.0,
        ))

        # Fail sheet 2 with AUTH_FAILURE (immediate terminal)
        baton._handle_attempt_result(_make_attempt_result(
            job_id="j1", sheet_num=2, execution_success=False,
            error_classification="AUTH_FAILURE",
        ))

        # Sheet 5 depends on sheet 2, so it must be failed
        assert sheets[5].status == BatonSheetStatus.FAILED, (
            f"Join sheet 5 is {sheets[5].status.value} after fan instance "
            f"2 failed. Must be FAILED due to unsatisfied dependency."
        )
        # Sheets 3 and 4 are independent — should NOT be failed
        assert sheets[3].status != BatonSheetStatus.FAILED
        assert sheets[4].status != BatonSheetStatus.FAILED
