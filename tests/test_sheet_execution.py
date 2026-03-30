"""Tests for SheetExecutionMixin (execution/runner/sheet.py).

This module covers the core sheet execution logic including:
- Decision-making: _decide_next_action, _map_judgment_to_mode
- Retry delay calculation: _get_retry_delay
- Helper methods: _build_judgment_query, _query_historical_failures
- Context building: _build_sheet_context
- The main _execute_sheet_with_recovery state machine (via mock integration)
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozart.backends.base import ExecutionResult
from mozart.core.checkpoint import CheckpointState, SheetStatus
from mozart.core.config import JobConfig
from mozart.core.errors import ClassificationResult, ClassifiedError, ErrorCategory, ErrorCode
from mozart.execution.runner.models import (
    ExecutionFailureContext,
    FatalError,
    GracefulShutdownError,
    GroundingDecisionContext,
    SheetExecutionMode,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path, overrides: dict[str, Any] | None = None) -> JobConfig:
    """Build a minimal JobConfig for testing."""
    base = {
        "name": "test-job",
        "description": "Unit test job",
        "workspace": str(tmp_path / "workspace"),
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 5, "total_items": 10},
        "prompt": {"template": "Process sheet {{ sheet_num }}."},
        "retry": {
            "max_retries": 3,
            "max_completion_attempts": 2,
            "base_delay_seconds": 1.0,
            "exponential_base": 2.0,
            "max_delay_seconds": 60.0,
            "jitter": False,
            "completion_threshold_percent": 60,
        },
        "validations": [],
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                base[k].update(v)
            else:
                base[k] = v
    (tmp_path / "workspace").mkdir(exist_ok=True)
    return JobConfig(**base)


def _make_validation_result(
    *,
    all_passed: bool = False,
    pass_pct: float = 50.0,
    confidence: float = 0.7,
    results: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Build a mock SheetValidationResult."""
    vr = MagicMock()
    vr.all_passed = all_passed
    vr.executed_pass_percentage = pass_pct
    vr.aggregate_confidence = confidence
    vr.pass_percentage = pass_pct
    vr.results = results or []
    vr.to_dict_list.return_value = results or []
    vr.get_semantic_summary.return_value = {"dominant_category": "missing_output"}
    vr.get_actionable_hints.return_value = ["Check output files"]
    return vr


def _make_execution_result(
    *,
    success: bool = True,
    exit_code: int = 0,
    stdout: str = "done",
    stderr: str = "",
    duration: float = 5.0,
) -> ExecutionResult:
    """Build an ExecutionResult for testing."""
    return ExecutionResult(
        success=success,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
    )


class _MockMixin:
    """Minimal mock of the mixin host attributes expected by SheetExecutionMixin."""

    def __init__(self, config: JobConfig) -> None:
        from rich.console import Console

        from mozart.core.errors import ErrorClassifier
        from mozart.core.logging import get_logger
        from mozart.execution.preflight import PreflightChecker
        from mozart.execution.retry_strategy import AdaptiveRetryStrategy, RetryStrategyConfig
        from mozart.prompts.templating import PromptBuilder

        self.config = config
        self.backend = MagicMock()
        self.backend.execute = AsyncMock(
            return_value=_make_execution_result()
        )
        self.backend.set_output_log_path = MagicMock()

        self.state_backend = AsyncMock()
        self.state_backend.save = AsyncMock()

        self.console = Console(quiet=True)
        self.outcome_store = None
        self.escalation_handler = None
        self.checkpoint_handler = None
        self.judgment_client = None

        self.prompt_builder = PromptBuilder(config.prompt)
        self.error_classifier = ErrorClassifier.from_config([])
        self.preflight_checker = PreflightChecker(
            workspace=config.workspace,
            working_directory=config.workspace,
        )

        self._logger = get_logger("test")
        self._circuit_breaker = None
        self._global_learning_store = None
        self._grounding_engine = None
        self._healing_coordinator = None
        self._retry_strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                base_delay=1.0,
                max_delay=60.0,
                exponential_base=2.0,
                jitter_factor=0.0,
            ),
        )

        self._current_sheet_num: int | None = None
        self._execution_progress_snapshots: list[dict[str, Any]] = []
        self._current_sheet_patterns: list[str] = []
        self._applied_pattern_ids: list[str] = []
        self._exploration_pattern_ids: list[str] = []
        self._exploitation_pattern_ids: list[str] = []
        self._shutdown_requested = False
        self._summary = None
        self._self_healing_enabled = False
        self.event_callback = None

    async def _fire_event(
        self, event: str, sheet_num: int, data: dict[str, Any] | None = None,
    ) -> None:
        """No-op event callback stub for tests."""
        if self.event_callback is None:
            return
        self.event_callback(self.config.name, sheet_num, event, data)

    async def _interruptible_sleep(self, seconds: float) -> None:
        pass  # no-op in tests

    def _query_relevant_patterns(
        self, job_id: str, sheet_num: int, context_tags: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        return [], []

    async def _record_pattern_feedback(self, pattern_ids, context) -> None:
        pass

    async def _try_self_healing(
        self, result, error, config_path, sheet_num, retry_count, max_retries,
    ):
        return None

    async def _handle_rate_limit(self, state, error_code="E101", suggested_wait_seconds=None):
        pass

    async def _track_cost(self, result, sheet_state, state) -> None:
        pass

    def _check_cost_limits(self, sheet_state, state) -> tuple[bool, str | None]:
        return False, None


# Dynamically compose the mixin for testing
from mozart.execution.runner.context import ContextBuildingMixin
from mozart.execution.runner.recovery import RecoveryMixin
from mozart.execution.runner.sheet import SheetExecutionMixin


class _TestableSheetMixin(_MockMixin, SheetExecutionMixin, ContextBuildingMixin, RecoveryMixin):
    """Concrete class that combines the mixin with mock infrastructure."""
    pass


@pytest.fixture
def mixin(tmp_path: Path) -> _TestableSheetMixin:
    config = _make_config(tmp_path)
    return _TestableSheetMixin(config)


@pytest.fixture
def mixin_with_jitter(tmp_path: Path) -> _TestableSheetMixin:
    config = _make_config(tmp_path, overrides={"retry": {"jitter": True}})
    return _TestableSheetMixin(config)


def _make_state(job_id: str = "test-job", total_sheets: int = 2) -> CheckpointState:
    """Build a minimal CheckpointState."""
    return CheckpointState(
        job_id=job_id,
        job_name="test-job",
        total_sheets=total_sheets,
    )


# ===========================================================================
# Tests: _decide_next_action
# ===========================================================================

class TestDecideNextAction:
    """Tests for the local decision logic (_decide_next_action)."""

    def test_high_confidence_majority_passed_returns_completion(self, mixin: _TestableSheetMixin):
        """High confidence + majority passed → COMPLETION mode."""
        vr = _make_validation_result(confidence=0.95, pass_pct=75.0)
        mode, reason, hints = mixin._decide_next_action(
            vr, normal_attempts=0, completion_attempts=0,
        )
        assert mode == SheetExecutionMode.COMPLETION
        assert "high confidence" in reason

    def test_high_confidence_completion_exhausted_returns_retry(self, mixin: _TestableSheetMixin):
        """When completion attempts exhausted, falls back to RETRY."""
        vr = _make_validation_result(confidence=0.95, pass_pct=75.0)
        mode, reason, hints = mixin._decide_next_action(
            vr, normal_attempts=0, completion_attempts=2,
        )
        assert mode == SheetExecutionMode.RETRY
        assert "completion attempts exhausted" in reason

    def test_low_confidence_with_escalation_returns_escalate(self, mixin: _TestableSheetMixin):
        """Low confidence with escalation enabled → ESCALATE."""
        mixin.config.learning.escalation_enabled = True
        mixin.escalation_handler = MagicMock()
        vr = _make_validation_result(confidence=0.15, pass_pct=30.0)
        mode, reason, hints = mixin._decide_next_action(
            vr, normal_attempts=0, completion_attempts=0,
        )
        assert mode == SheetExecutionMode.ESCALATE
        assert "low confidence" in reason

    def test_low_confidence_without_escalation_returns_retry(self, mixin: _TestableSheetMixin):
        """Low confidence without escalation → RETRY."""
        mixin.config.learning.escalation_enabled = False
        vr = _make_validation_result(confidence=0.15, pass_pct=30.0)
        mode, reason, hints = mixin._decide_next_action(
            vr, normal_attempts=0, completion_attempts=0,
        )
        assert mode == SheetExecutionMode.RETRY
        assert "escalation not available" in reason

    def test_medium_confidence_above_threshold_returns_completion(self, mixin: _TestableSheetMixin):
        """Medium confidence with pass_pct above completion threshold → COMPLETION."""
        vr = _make_validation_result(confidence=0.55, pass_pct=75.0)
        mode, reason, hints = mixin._decide_next_action(
            vr, normal_attempts=0, completion_attempts=0,
        )
        assert mode == SheetExecutionMode.COMPLETION
        assert "medium confidence" in reason

    def test_medium_confidence_below_threshold_returns_retry(self, mixin: _TestableSheetMixin):
        """Medium confidence with pass_pct below threshold → RETRY."""
        vr = _make_validation_result(confidence=0.55, pass_pct=40.0)
        mode, reason, hints = mixin._decide_next_action(
            vr, normal_attempts=0, completion_attempts=0,
        )
        assert mode == SheetExecutionMode.RETRY
        assert "full retry needed" in reason

    def test_grounding_escalation_overrides_decision(self, mixin: _TestableSheetMixin):
        """Grounding hook requesting escalation overrides normal decision."""
        mixin.config.learning.escalation_enabled = True
        mixin.escalation_handler = MagicMock()
        vr = _make_validation_result(confidence=0.9, pass_pct=80.0)
        grounding = GroundingDecisionContext(
            passed=False,
            message="Grounding failed",
            confidence=0.3,
            should_escalate=True,
            hooks_executed=1,
        )
        mode, reason, hints = mixin._decide_next_action(
            vr, normal_attempts=0, completion_attempts=0, grounding_context=grounding,
        )
        assert mode == SheetExecutionMode.ESCALATE
        assert "grounding hook requests escalation" in reason

    def test_grounding_confidence_blended_into_decision(self, mixin: _TestableSheetMixin):
        """Grounding confidence is weighted at 30% with validation at 70%."""
        vr = _make_validation_result(confidence=0.9, pass_pct=75.0)
        # Grounding with low confidence drags combined confidence below threshold
        grounding = GroundingDecisionContext(
            passed=True,
            message="OK",
            confidence=0.1,
            hooks_executed=1,
        )
        mode, reason, hints = mixin._decide_next_action(
            vr, normal_attempts=0, completion_attempts=0, grounding_context=grounding,
        )
        # 0.9 * 0.7 + 0.1 * 0.3 = 0.66 (medium confidence)
        assert mode in (SheetExecutionMode.COMPLETION, SheetExecutionMode.RETRY)


# ===========================================================================
# Tests: _get_retry_delay
# ===========================================================================

class TestGetRetryDelay:
    """Tests for retry delay calculation."""

    def test_first_attempt_returns_base_delay(self, mixin: _TestableSheetMixin):
        delay = mixin._get_retry_delay(1)
        assert delay == 1.0  # base_delay_seconds

    def test_second_attempt_applies_exponential_backoff(self, mixin: _TestableSheetMixin):
        delay = mixin._get_retry_delay(2)
        assert delay == 2.0  # 1.0 * 2^(2-1) = 2.0

    def test_third_attempt_applies_exponential_backoff(self, mixin: _TestableSheetMixin):
        delay = mixin._get_retry_delay(3)
        assert delay == 4.0  # 1.0 * 2^(3-1) = 4.0

    def test_delay_capped_at_max(self, mixin: _TestableSheetMixin):
        delay = mixin._get_retry_delay(100)
        assert delay == 60.0  # max_delay_seconds

    def test_jitter_varies_delay(self, mixin_with_jitter: _TestableSheetMixin):
        """Jitter should vary the delay within a range."""
        delays = {mixin_with_jitter._get_retry_delay(1) for _ in range(20)}
        # With jitter, delay = base * (0.5 + random()) so range is [0.5, 1.5]
        assert len(delays) > 1, "Jitter should produce varied delays"
        for d in delays:
            assert 0.5 <= d <= 1.5


# ===========================================================================
# Tests: _map_judgment_to_mode
# ===========================================================================

class TestMapJudgmentToMode:
    """Tests for mapping Recursive Light judgment responses to modes."""

    def _make_response(self, action: str, reasoning: str = "test") -> MagicMock:
        resp = MagicMock()
        resp.recommended_action = action
        resp.reasoning = reasoning
        resp.confidence = 0.8
        resp.patterns_learned = []
        resp.prompt_modifications = None
        return resp

    def test_proceed_maps_to_normal(self, mixin: _TestableSheetMixin):
        mode = mixin._map_judgment_to_mode(self._make_response("proceed"), 0)
        assert mode == SheetExecutionMode.NORMAL

    def test_retry_maps_to_retry(self, mixin: _TestableSheetMixin):
        mode = mixin._map_judgment_to_mode(self._make_response("retry"), 0)
        assert mode == SheetExecutionMode.RETRY

    def test_completion_maps_to_completion(self, mixin: _TestableSheetMixin):
        mode = mixin._map_judgment_to_mode(self._make_response("completion"), 0)
        assert mode == SheetExecutionMode.COMPLETION

    def test_completion_falls_back_to_retry_when_exhausted(self, mixin: _TestableSheetMixin):
        mode = mixin._map_judgment_to_mode(self._make_response("completion"), 2)
        assert mode == SheetExecutionMode.RETRY

    def test_escalate_maps_to_escalate(self, mixin: _TestableSheetMixin):
        mode = mixin._map_judgment_to_mode(self._make_response("escalate"), 0)
        assert mode == SheetExecutionMode.ESCALATE

    def test_abort_raises_fatal_error(self, mixin: _TestableSheetMixin):
        with pytest.raises(FatalError, match="abort"):
            mixin._map_judgment_to_mode(self._make_response("abort"), 0)

    def test_unknown_action_defaults_to_retry(self, mixin: _TestableSheetMixin):
        mode = mixin._map_judgment_to_mode(self._make_response("unknown_action"), 0)
        assert mode == SheetExecutionMode.RETRY


# ===========================================================================
# Tests: _build_judgment_query
# ===========================================================================

class TestBuildJudgmentQuery:
    """Tests for building judgment queries from execution state."""

    def test_query_captures_error_patterns(self, mixin: _TestableSheetMixin):
        history: Sequence[ExecutionResult] = [
            _make_execution_result(success=False, stderr="FileNotFoundError: /path\ndetails"),
            _make_execution_result(success=True),
        ]
        vr = _make_validation_result()
        query = mixin._build_judgment_query(
            sheet_num=1, validation_result=vr,
            execution_history=history, normal_attempts=1,
        )
        assert query.sheet_num == 1
        assert query.retry_count == 1
        assert "FileNotFoundError" in query.error_patterns[0]

    def test_query_deduplicates_error_patterns(self, mixin: _TestableSheetMixin):
        history = [
            _make_execution_result(success=False, stderr="Error: timeout"),
            _make_execution_result(success=False, stderr="Error: timeout"),
        ]
        vr = _make_validation_result()
        query = mixin._build_judgment_query(
            sheet_num=1, validation_result=vr,
            execution_history=history, normal_attempts=2,
        )
        assert len(query.error_patterns) == 1

    def test_query_captures_execution_history(self, mixin: _TestableSheetMixin):
        history = [_make_execution_result(success=True, duration=3.0)]
        vr = _make_validation_result(confidence=0.85)
        query = mixin._build_judgment_query(
            sheet_num=2, validation_result=vr,
            execution_history=history, normal_attempts=0,
        )
        assert query.confidence == 0.85
        assert len(query.execution_history) == 1
        assert query.execution_history[0]["success"] is True


# ===========================================================================
# Tests: _build_sheet_context
# ===========================================================================

class TestBuildSheetContext:
    """Tests for sheet context construction."""

    def test_builds_basic_context(self, mixin: _TestableSheetMixin):
        ctx = mixin._build_sheet_context(sheet_num=1)
        d = ctx.to_dict()
        assert d["sheet_num"] == 1
        assert d["total_sheets"] == 2  # from config total_items=10, size=5
        assert "workspace" in d

    def test_context_without_state_has_no_cross_sheet(self, mixin: _TestableSheetMixin):
        ctx = mixin._build_sheet_context(sheet_num=1, state=None)
        assert not ctx.previous_outputs


# ===========================================================================
# Tests: _query_historical_failures
# ===========================================================================

class TestQueryHistoricalFailures:
    """Tests for historical failure querying."""

    def test_returns_empty_for_first_sheet(self, mixin: _TestableSheetMixin):
        state = _make_state()
        failures = mixin._query_historical_failures(state, sheet_num=1)
        assert failures == []

    def test_returns_failures_from_prior_sheets(self, mixin: _TestableSheetMixin):
        state = _make_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.sheets[1].validation_details = [
            {"description": "File exists", "passed": False, "message": "Not found"},
        ]
        state.mark_sheet_failed(1, "validation failed", "validation")
        failures = mixin._query_historical_failures(state, sheet_num=2)
        # FailureHistoryStore looks at validation_details of failed sheets
        assert isinstance(failures, list)


# ===========================================================================
# Tests: _gather_learned_patterns
# ===========================================================================

class TestGatherLearnedPatterns:
    """Tests for the pattern gathering method."""

    @pytest.mark.asyncio
    async def test_no_stores_returns_empty(self, mixin: _TestableSheetMixin):
        state = _make_state()
        patterns = await mixin._gather_learned_patterns(state, sheet_num=1)
        assert patterns == []
        assert mixin._current_sheet_patterns == []

    @pytest.mark.asyncio
    async def test_local_patterns_returned(self, mixin: _TestableSheetMixin):
        mixin.outcome_store = AsyncMock()
        mixin.outcome_store.get_relevant_patterns = AsyncMock(
            return_value=["Use explicit file paths"]
        )
        state = _make_state()
        patterns = await mixin._gather_learned_patterns(state, sheet_num=1)
        assert "Use explicit file paths" in patterns

    @pytest.mark.asyncio
    async def test_global_patterns_deduplicated(self, mixin: _TestableSheetMixin):
        """Global patterns should not duplicate local ones."""
        mixin.outcome_store = AsyncMock()
        mixin.outcome_store.get_relevant_patterns = AsyncMock(
            return_value=["Pattern A"]
        )
        # Mock global pattern query to return same + new pattern
        mixin._query_relevant_patterns = MagicMock(
            return_value=(["Pattern A", "Pattern B"], ["id-a", "id-b"])
        )
        state = _make_state()
        patterns = await mixin._gather_learned_patterns(state, sheet_num=1)
        # Should have Pattern A once and Pattern B
        assert patterns.count("Pattern A") == 1
        assert "Pattern B" in patterns


# ===========================================================================
# Tests: _decide_with_judgment
# ===========================================================================

class TestDecideWithJudgment:
    """Tests for judgment-integrated decision making."""

    @pytest.mark.asyncio
    async def test_no_judgment_client_uses_local_decision(self, mixin: _TestableSheetMixin):
        """Without judgment client, falls back to _decide_next_action."""
        vr = _make_validation_result(confidence=0.55, pass_pct=75.0)
        mode, reason, mods = await mixin._decide_with_judgment(
            sheet_num=1, validation_result=vr,
            execution_history=[], normal_attempts=0, completion_attempts=0,
        )
        assert mode in (SheetExecutionMode.COMPLETION, SheetExecutionMode.RETRY)
        assert mods is not None  # semantic_hints from local decision

    @pytest.mark.asyncio
    async def test_judgment_client_error_falls_back(self, mixin: _TestableSheetMixin):
        """Judgment client error falls back to local decision."""
        mixin.judgment_client = AsyncMock()
        mixin.judgment_client.get_judgment = AsyncMock(side_effect=RuntimeError("timeout"))
        vr = _make_validation_result(confidence=0.55, pass_pct=75.0)
        mode, reason, mods = await mixin._decide_with_judgment(
            sheet_num=1, validation_result=vr,
            execution_history=[], normal_attempts=0, completion_attempts=0,
        )
        assert "judgment fallback" in reason


# ===========================================================================
# Tests: _can_auto_apply
# ===========================================================================

class TestCanAutoApply:
    """Tests for the auto-apply trust check."""

    def test_disabled_global_patterns_returns_false(self, mixin: _TestableSheetMixin):
        mixin.config.learning.use_global_patterns = False
        assert mixin._can_auto_apply(0.8) is False

    def test_no_global_store_returns_false(self, mixin: _TestableSheetMixin):
        mixin.config.learning.use_global_patterns = True
        mixin._global_learning_store = None
        assert mixin._can_auto_apply(0.8) is False

    def test_store_with_patterns_returns_true(self, mixin: _TestableSheetMixin):
        mixin.config.learning.use_global_patterns = True
        store = MagicMock()
        store.get_patterns_for_auto_apply.return_value = [MagicMock()]
        mixin._global_learning_store = store
        assert mixin._can_auto_apply(0.8) is True

    def test_store_exception_returns_false(self, mixin: _TestableSheetMixin):
        mixin.config.learning.use_global_patterns = True
        store = MagicMock()
        store.get_patterns_for_auto_apply.side_effect = RuntimeError("db error")
        mixin._global_learning_store = store
        assert mixin._can_auto_apply(0.8) is False


# ===========================================================================
# Tests: _update_escalation_outcome
# ===========================================================================

class TestUpdateEscalationOutcome:
    """Tests for escalation outcome recording."""

    def test_no_global_store_is_noop(self, mixin: _TestableSheetMixin):
        sheet_state = MagicMock()
        assert mixin._global_learning_store is None
        mixin._update_escalation_outcome(sheet_state, "success", 1)
        assert mixin._global_learning_store is None

    def test_no_escalation_record_id_is_noop(self, mixin: _TestableSheetMixin):
        mixin._global_learning_store = MagicMock()
        sheet_state = MagicMock()
        sheet_state.outcome_data = {}
        mixin._update_escalation_outcome(sheet_state, "success", 1)
        mixin._global_learning_store.update_escalation_outcome.assert_not_called()

    def test_updates_outcome_when_record_exists(self, mixin: _TestableSheetMixin):
        store = MagicMock()
        store.update_escalation_outcome.return_value = True
        mixin._global_learning_store = store
        sheet_state = MagicMock()
        sheet_state.outcome_data = {"escalation_record_id": "esc-123"}
        mixin._update_escalation_outcome(sheet_state, "success", 1)
        store.update_escalation_outcome.assert_called_once_with(
            escalation_id="esc-123", outcome_after_action="success",
        )


# ===========================================================================
# Tests: _poll_broadcast_discoveries
# ===========================================================================

class TestPollBroadcastDiscoveries:
    """Tests for pattern broadcast polling during retry waits."""

    @pytest.mark.asyncio
    async def test_no_global_store_is_noop(self, mixin: _TestableSheetMixin):
        assert mixin._global_learning_store is None
        await mixin._poll_broadcast_discoveries("job-1", 1)
        # Completed without error and no store was accessed

    @pytest.mark.asyncio
    async def test_logs_discoveries(self, mixin: _TestableSheetMixin):
        discovery = MagicMock()
        discovery.pattern_id = "p-1"
        discovery.pattern_name = "retry-on-timeout"
        discovery.pattern_type = "retry"
        discovery.effectiveness_score = 0.85
        discovery.context_tags = []

        store = MagicMock()
        store.check_recent_pattern_discoveries.return_value = [discovery]
        mixin._global_learning_store = store

        await mixin._poll_broadcast_discoveries("job-1", 1)
        store.check_recent_pattern_discoveries.assert_called_once()


# ===========================================================================
# Tests: _record_sheet_outcome
# ===========================================================================

class TestRecordSheetOutcome:
    """Tests for outcome recording to the learning store."""

    @pytest.mark.asyncio
    async def test_no_outcome_store_is_noop(self, mixin: _TestableSheetMixin):
        assert mixin.outcome_store is None
        vr = _make_validation_result()
        await mixin._record_sheet_outcome(
            sheet_num=1, job_id="test", validation_result=vr,
            execution_duration=5.0, normal_attempts=1, completion_attempts=0,
            success_without_retry=True, final_status=SheetStatus.COMPLETED,
        )
        # Completed without error — no store to call

    @pytest.mark.asyncio
    async def test_records_outcome_when_store_available(self, mixin: _TestableSheetMixin):
        mixin.outcome_store = AsyncMock()
        mixin.outcome_store.record = AsyncMock()
        vr = _make_validation_result(pass_pct=100.0)
        await mixin._record_sheet_outcome(
            sheet_num=1, job_id="test", validation_result=vr,
            execution_duration=5.0, normal_attempts=1, completion_attempts=0,
            success_without_retry=True, final_status=SheetStatus.COMPLETED,
        )
        mixin.outcome_store.record.assert_called_once()


# ===========================================================================
# Tests: _check_proactive_checkpoint
# ===========================================================================

class TestCheckProactiveCheckpoint:
    """Tests for the proactive checkpoint system."""

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self, mixin: _TestableSheetMixin):
        state = _make_state()
        result = await mixin._check_proactive_checkpoint(state, 1, "prompt", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_triggers_returns_none(self, mixin: _TestableSheetMixin):
        mixin.config.checkpoints.enabled = True
        state = _make_state()
        result = await mixin._check_proactive_checkpoint(state, 1, "prompt", 0)
        assert result is None


# ===========================================================================
# Tests: _handle_escalation
# ===========================================================================

class TestHandleEscalation:
    """Tests for escalation handling."""

    @pytest.mark.asyncio
    async def test_no_handler_raises_fatal(self, mixin: _TestableSheetMixin):
        state = _make_state()
        state.mark_sheet_started(1)
        vr = _make_validation_result()
        with pytest.raises(FatalError, match="no handler configured"):
            await mixin._handle_escalation(
                state=state, sheet_num=1, validation_result=vr,
                current_prompt="test", error_history=[], normal_attempts=1,
            )

    @pytest.mark.asyncio
    async def test_escalation_returns_response(self, mixin: _TestableSheetMixin):
        from mozart.execution.escalation import EscalationResponse

        handler = AsyncMock()
        handler.escalate = AsyncMock(
            return_value=EscalationResponse(action="retry", guidance="try again")
        )
        mixin.escalation_handler = handler

        state = _make_state()
        state.mark_sheet_started(1)
        vr = _make_validation_result()
        response = await mixin._handle_escalation(
            state=state, sheet_num=1, validation_result=vr,
            current_prompt="test", error_history=[], normal_attempts=1,
        )
        assert response.action == "retry"
        assert response.guidance == "try again"


# ===========================================================================
# Tests: _execute_sheet_with_recovery (integration)
# ===========================================================================

class TestExecuteSheetWithRecovery:
    """Integration tests for the main sheet execution state machine."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self, mixin: _TestableSheetMixin):
        """Happy path: backend succeeds, all validations pass."""
        state = _make_state()

        # Mock validation to pass
        mock_vr = _make_validation_result(all_passed=True)
        with patch(
            "mozart.execution.runner.sheet.ValidationEngine"
        ) as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=mock_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        # Sheet should be marked completed
        assert state.sheets[1].status == SheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_preflight_failure_raises_fatal(self, mixin: _TestableSheetMixin):
        """Preflight check failure → FatalError, sheet marked failed."""
        state = _make_state()

        # Mock preflight to fail
        from mozart.execution.preflight import PreflightResult, PromptMetrics

        failed_preflight = PreflightResult(
            errors=["workspace does not exist"],
            warnings=[],
            prompt_metrics=PromptMetrics(
                estimated_tokens=100, line_count=5,
                character_count=500, has_file_references=False,
            ),
        )
        mixin.preflight_checker.check = MagicMock(return_value=failed_preflight)

        with patch(
            "mozart.execution.runner.sheet.ValidationEngine"
        ), pytest.raises(FatalError, match="Preflight check failed"):
            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises_fatal(self, mixin: _TestableSheetMixin):
        """All retries exhausted → FatalError."""
        state = _make_state()

        # Backend returns failure
        mixin.backend.execute = AsyncMock(
            return_value=_make_execution_result(
                success=False, exit_code=1, stderr="Error occurred"
            )
        )

        mock_vr = _make_validation_result(
            all_passed=False, pass_pct=0.0, confidence=0.5,
        )

        with patch(
            "mozart.execution.runner.sheet.ValidationEngine"
        ) as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=mock_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            with pytest.raises(FatalError):
                await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_cost_limit_raises_graceful_shutdown(self, mixin: _TestableSheetMixin):
        """Cost limit exceeded → GracefulShutdownError."""
        state = _make_state()
        mixin.config.cost_limits.enabled = True
        mixin._check_cost_limits = MagicMock(return_value=(True, "Job cost $5.00 > limit $1.00"))

        mock_vr = _make_validation_result(all_passed=True)

        with patch(
            "mozart.execution.runner.sheet.ValidationEngine"
        ) as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=mock_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            with pytest.raises(GracefulShutdownError, match="Cost limit exceeded"):
                await mixin._execute_sheet_with_recovery(state, sheet_num=1)

    @pytest.mark.asyncio
    async def test_success_after_retry(self, mixin: _TestableSheetMixin):
        """Backend fails first, succeeds on retry."""
        state = _make_state()

        # First call fails, second succeeds
        mixin.backend.execute = AsyncMock(side_effect=[
            _make_execution_result(success=False, exit_code=1, stderr="Error"),
            _make_execution_result(success=True, exit_code=0),
        ])

        # First validation fails, second passes
        fail_vr = _make_validation_result(all_passed=False, pass_pct=0.0, confidence=0.5)
        pass_vr = _make_validation_result(all_passed=True)

        with patch(
            "mozart.execution.runner.sheet.ValidationEngine"
        ) as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(side_effect=[fail_vr, pass_vr])
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.COMPLETED


# ===========================================================================
# Tests: GroundingDecisionContext
# ===========================================================================

class TestGroundingDecisionContext:
    """Tests for the GroundingDecisionContext data class."""

    def test_disabled_context(self):
        ctx = GroundingDecisionContext.disabled()
        assert ctx.passed is True
        assert ctx.hooks_executed == 0

    def test_from_empty_results(self):
        ctx = GroundingDecisionContext.from_results([])
        assert ctx.passed is True
        assert ctx.hooks_executed == 0

    def test_from_passing_results(self):
        result = MagicMock()
        result.passed = True
        result.confidence = 0.95
        result.should_escalate = False
        result.recovery_guidance = None
        result.hook_name = "test"
        result.message = "OK"

        ctx = GroundingDecisionContext.from_results([result])
        assert ctx.passed is True
        assert ctx.confidence == 0.95
        assert ctx.hooks_executed == 1

    def test_from_failing_results(self):
        result = MagicMock()
        result.passed = False
        result.confidence = 0.3
        result.should_escalate = True
        result.recovery_guidance = "Check output format"
        result.hook_name = "format_check"
        result.message = "Format mismatch"

        ctx = GroundingDecisionContext.from_results([result])
        assert ctx.passed is False
        assert ctx.should_escalate is True
        assert ctx.recovery_guidance is not None
        assert "Check output format" in ctx.recovery_guidance
        assert "format_check" in ctx.message


# ===========================================================================
# Tests: _execute_sheet_with_recovery — additional paths
# ===========================================================================


class TestCircuitBreakerBlocking:
    """Tests for circuit breaker blocking execution."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_then_allows(self, mixin: _TestableSheetMixin):
        """Circuit breaker open -> waits -> allows execution -> succeeds."""
        state = _make_state()

        # Circuit breaker: block on first call, allow on second
        cb = MagicMock()
        cb.can_execute = AsyncMock(side_effect=[False, True])
        cb.time_until_retry = AsyncMock(return_value=0.01)
        # First get_state call: during blocking log (open)
        # Second get_state call: after record_success for persistence (closed)
        cb.get_state = AsyncMock(
            side_effect=[MagicMock(value="open"), MagicMock(value="closed")]
        )
        cb.get_stats = AsyncMock(return_value=MagicMock(consecutive_failures=0))
        cb.record_success = AsyncMock()
        mixin._circuit_breaker = cb

        mock_vr = _make_validation_result(all_passed=True)
        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=mock_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.COMPLETED
        cb.record_success.assert_called_once()


class TestCompletionModeFlow:
    """Tests for completion mode transitions."""

    @pytest.mark.asyncio
    async def test_partial_pass_enters_completion_then_succeeds(self, mixin: _TestableSheetMixin):
        """Validations partially pass -> completion mode -> all pass on retry."""
        state = _make_state()

        # First: validations partially pass (above threshold)
        partial_vr = _make_validation_result(
            all_passed=False, pass_pct=75.0, confidence=0.9,
        )
        # Second: all pass
        full_vr = _make_validation_result(all_passed=True)

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(side_effect=[partial_vr, full_vr])
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_completion_attempts_exhausted_falls_to_retry(self, mixin: _TestableSheetMixin):
        """Completion attempts exhausted -> falls back to RETRY -> eventually fails."""
        state = _make_state()

        # All validations partially pass with high confidence (triggers completion)
        # but never fully pass, exhausting both completion attempts and retries
        partial_vr = _make_validation_result(
            all_passed=False, pass_pct=75.0, confidence=0.95,
        )

        # Backend always succeeds but validations never fully pass
        mixin.backend.execute = AsyncMock(
            return_value=_make_execution_result(success=True, exit_code=0)
        )

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            # Return partial results for more iterations than max_retries + max_completion
            ve_instance.run_validations = AsyncMock(return_value=partial_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            with pytest.raises(FatalError, match="exhausted all retry options"):
                await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.FAILED


class TestEscalationModeFlow:
    """Tests for escalation mode transitions."""

    @pytest.mark.asyncio
    async def test_escalation_skip_marks_completed(self, mixin: _TestableSheetMixin):
        """Escalation with skip action -> sheet completed (with validation_passed=False)."""
        from mozart.execution.escalation import EscalationResponse

        state = _make_state()
        mixin.config.learning.escalation_enabled = True
        handler = AsyncMock()
        handler.escalate = AsyncMock(
            return_value=EscalationResponse(action="skip", guidance="acceptable")
        )
        mixin.escalation_handler = handler

        # Low confidence triggers escalation
        low_vr = _make_validation_result(
            all_passed=False, pass_pct=20.0, confidence=0.1,
        )

        mixin.backend.execute = AsyncMock(
            return_value=_make_execution_result(success=True, exit_code=0)
        )

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=low_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        # Sheet completed via skip (not failed)
        assert state.sheets[1].status == SheetStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_escalation_abort_raises_fatal(self, mixin: _TestableSheetMixin):
        """Escalation with abort action -> FatalError raised."""
        from mozart.execution.escalation import EscalationResponse

        state = _make_state()
        mixin.config.learning.escalation_enabled = True
        handler = AsyncMock()
        handler.escalate = AsyncMock(
            return_value=EscalationResponse(action="abort", guidance="give up")
        )
        mixin.escalation_handler = handler

        low_vr = _make_validation_result(
            all_passed=False, pass_pct=20.0, confidence=0.1,
        )
        mixin.backend.execute = AsyncMock(
            return_value=_make_execution_result(success=True, exit_code=0)
        )

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=low_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            with pytest.raises(FatalError, match="aborted via escalation"):
                await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.FAILED

    @pytest.mark.asyncio
    async def test_escalation_modify_prompt_retries(self, mixin: _TestableSheetMixin):
        """Escalation with modify_prompt -> retries with new prompt -> succeeds."""
        from mozart.execution.escalation import EscalationResponse

        state = _make_state()
        mixin.config.learning.escalation_enabled = True
        handler = AsyncMock()
        handler.escalate = AsyncMock(
            return_value=EscalationResponse(
                action="modify_prompt",
                guidance="be more specific",
                modified_prompt="Revised prompt with more details",
            )
        )
        mixin.escalation_handler = handler

        low_vr = _make_validation_result(
            all_passed=False, pass_pct=20.0, confidence=0.1,
        )
        pass_vr = _make_validation_result(all_passed=True)

        mixin.backend.execute = AsyncMock(
            return_value=_make_execution_result(success=True, exit_code=0)
        )

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(side_effect=[low_vr, pass_vr])
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.COMPLETED


class TestAdaptiveRetryAbort:
    """Tests for adaptive retry strategy aborting early."""

    @pytest.mark.asyncio
    async def test_adaptive_strategy_aborts_early(self, mixin: _TestableSheetMixin):
        """Adaptive retry recommends stopping -> FatalError with abort reason."""
        from mozart.execution.retry_strategy import RetryPattern, RetryRecommendation

        state = _make_state()

        # Override retry strategy to recommend abort after first attempt
        abort_rec = RetryRecommendation(
            should_retry=False,
            delay_seconds=0,
            reason="Repeating identical error (deterministic failure)",
            confidence=0.95,
            detected_pattern=RetryPattern.REPEATED_ERROR_CODE,
            strategy_used="pattern_detection",
        )
        mixin._retry_strategy.analyze = MagicMock(return_value=abort_rec)

        mixin.backend.execute = AsyncMock(
            return_value=_make_execution_result(
                success=False, exit_code=1, stderr="deterministic error"
            )
        )

        fail_vr = _make_validation_result(
            all_passed=False, pass_pct=0.0, confidence=0.5,
        )

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=fail_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            with pytest.raises(FatalError, match="aborted"):
                await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.FAILED


class TestRateLimitHandling:
    """Tests for rate limit detection and handling during execution."""

    @pytest.mark.asyncio
    async def test_rate_limit_on_execution_retries_automatically(self, mixin: _TestableSheetMixin):
        """Rate limited result -> skip validations -> handle_rate_limit -> retry.

        F-076: Rate limit check now happens BEFORE validations. When
        rate_limited=True, validations are skipped entirely (they'd run
        against garbage output). Only the successful retry gets validated.
        """
        state = _make_state()

        # First result is rate limited, second succeeds
        rate_limited_result = _make_execution_result(
            success=False, exit_code=1, stderr="Rate limit exceeded"
        )
        rate_limited_result.rate_limited = True

        success_result = _make_execution_result(success=True, exit_code=0)
        success_result.rate_limited = False

        mixin.backend.execute = AsyncMock(
            side_effect=[rate_limited_result, success_result]
        )

        # F-076: Only one validation call happens — the rate-limited
        # attempt skips validation entirely.
        pass_vr = _make_validation_result(all_passed=True)

        # Mock classify to return rate limit error
        rate_limit_error = MagicMock()
        rate_limit_error.is_rate_limit = True
        rate_limit_error.error_code = MagicMock(value="E101")
        rate_limit_error.suggested_wait_seconds = 5.0
        rate_limit_class = MagicMock()
        rate_limit_class.primary = rate_limit_error
        mixin._classify_execution = MagicMock(return_value=rate_limit_class)

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(side_effect=[pass_vr])
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.COMPLETED


class TestCrossWorkspaceRateLimit:
    """Tests for cross-workspace rate limit honoring."""

    @pytest.mark.asyncio
    async def test_honors_cross_workspace_rate_limit(self, mixin: _TestableSheetMixin):
        """Cross-workspace rate limit honored -> waits -> then executes."""
        state = _make_state()

        # Enable cross-workspace coordination
        mixin.config.circuit_breaker.cross_workspace_coordination = True
        mixin.config.circuit_breaker.honor_other_jobs_rate_limits = True
        # Set cli_model so effective_model is not None
        mixin.config.backend.cli_model = "claude-sonnet-4-5-20250929"

        # Global store says rate limited first call, not limited second
        store = MagicMock()
        store.is_rate_limited = MagicMock(
            side_effect=[(True, 0.01), (False, None)]
        )
        mixin._global_learning_store = store

        mock_vr = _make_validation_result(all_passed=True)
        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=mock_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.COMPLETED
        # Verify the store was checked
        store.is_rate_limited.assert_called()


class TestNonRetriableError:
    """Tests for non-retriable error handling."""

    @pytest.mark.asyncio
    async def test_fatal_error_not_retriable(self, mixin: _TestableSheetMixin):
        """Non-retriable error -> immediate FatalError without retry."""
        state = _make_state()

        mixin.backend.execute = AsyncMock(
            return_value=_make_execution_result(
                success=False, exit_code=127, stderr="Command not found"
            )
        )

        fail_vr = _make_validation_result(
            all_passed=False, pass_pct=0.0, confidence=0.5,
        )

        # Mock classify to return non-retriable error
        fatal_error = MagicMock()
        fatal_error.is_rate_limit = False
        fatal_error.should_retry = False
        fatal_error.message = "CLI not found"
        fatal_error.category = MagicMock(value="cli_error")
        fatal_error.error_code = MagicMock(value="E501")
        fatal_error.suggested_wait_seconds = None
        fatal_class = MagicMock()
        fatal_class.primary = fatal_error
        fatal_class.confidence = 1.0
        fatal_class.secondary = []
        fatal_class.raw_errors = []
        fatal_class.classification_method = "exit_code"
        fatal_class.all_errors = [fatal_error]
        mixin._classify_execution = MagicMock(return_value=fatal_class)

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=fail_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            with pytest.raises(FatalError, match="CLI not found"):
                await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        assert state.sheets[1].status == SheetStatus.FAILED
        # Should fail on first attempt, no retries
        assert mixin.backend.execute.call_count == 1


class TestSheetExecutionParametrized:
    """Parametrized tests for sheet execution decision paths."""

    @pytest.mark.parametrize("confidence,pass_pct,expected_mode", [
        (0.95, 80.0, SheetExecutionMode.COMPLETION),   # High confidence + high pass
        (0.55, 80.0, SheetExecutionMode.COMPLETION),   # Medium confidence + high pass
        (0.55, 40.0, SheetExecutionMode.RETRY),         # Medium confidence + low pass
    ])
    def test_decision_mode_selection(
        self,
        mixin: _TestableSheetMixin,
        confidence: float,
        pass_pct: float,
        expected_mode: SheetExecutionMode,
    ):
        """Parametrized decision mode selection based on confidence and pass rate."""
        vr = _make_validation_result(confidence=confidence, pass_pct=pass_pct)
        mode, _, _ = mixin._decide_next_action(vr, normal_attempts=0, completion_attempts=0)
        assert mode == expected_mode


# ===========================================================================
# Tests: _handle_execution_failure (extracted method)
# ===========================================================================


def _make_failure_ctx(
    mixin: _TestableSheetMixin,
    state: CheckpointState,
    *,
    success: bool = False,
    exit_code: int = 1,
    normal_attempts: int = 0,
    max_retries: int = 3,
    healing_attempts: int = 0,
    max_healing_cycles: int = 2,
    pending_recovery: dict[str, Any] | None = None,
) -> ExecutionFailureContext:
    """Build an ExecutionFailureContext for testing _handle_execution_failure."""
    result = _make_execution_result(success=success, exit_code=exit_code, stdout="error output")
    vr = _make_validation_result(all_passed=False, pass_pct=0.0)
    vr.get_passed_results.return_value = []
    vr.get_failed_results.return_value = []
    grounding_ctx = GroundingDecisionContext(
        passed=True, message="No hooks", hooks_executed=0,
    )
    return ExecutionFailureContext(
        state=state,
        sheet_num=1,
        result=result,
        validation_result=vr,
        passed_count=0,
        failed_count=0,
        error_history=[],
        normal_attempts=normal_attempts,
        max_retries=max_retries,
        healing_attempts=healing_attempts,
        max_healing_cycles=max_healing_cycles,
        pending_recovery=pending_recovery,
        grounding_ctx=grounding_ctx,
    )


def _make_classification(
    *,
    category: ErrorCategory = ErrorCategory.TRANSIENT,
    error_code: ErrorCode = ErrorCode.EXECUTION_TIMEOUT,
    message: str = "Timeout",
    retriable: bool = True,
    exit_code: int = 1,
    suggested_wait: float | None = None,
) -> ClassificationResult:
    """Build a real ClassificationResult for testing."""
    error = ClassifiedError(
        category=category,
        message=message,
        error_code=error_code,
        retriable=retriable,
        exit_code=exit_code,
        suggested_wait_seconds=suggested_wait,
    )
    return ClassificationResult(primary=error, confidence=0.9)


class TestHandleExecutionFailure:
    """Tests for the extracted _handle_execution_failure method."""

    @pytest.mark.asyncio
    async def test_rate_limit_returns_continue(self, mixin: _TestableSheetMixin):
        """Rate-limited errors should return continue action without consuming retries."""
        state = _make_state()
        state.mark_sheet_started(1)
        ctx = _make_failure_ctx(mixin, state, normal_attempts=0)

        classification = _make_classification(
            category=ErrorCategory.RATE_LIMIT,
            error_code=ErrorCode.RATE_LIMIT_API,
            message="Rate limited",
            suggested_wait=5.0,
        )
        with patch.object(mixin, '_classify_execution', return_value=classification):
            result = await mixin._handle_execution_failure(ctx)

        assert result.action == "continue"
        assert result.normal_attempts == 0  # Not incremented for rate limits

    @pytest.mark.asyncio
    async def test_non_retryable_returns_fatal(self, mixin: _TestableSheetMixin):
        """Non-retryable errors should return fatal action."""
        state = _make_state()
        state.mark_sheet_started(1)
        ctx = _make_failure_ctx(mixin, state, normal_attempts=0)

        classification = _make_classification(
            category=ErrorCategory.CONFIGURATION,
            error_code=ErrorCode.CONFIG_INVALID,
            message="Authentication failed",
            retriable=False,
        )
        with patch.object(mixin, '_classify_execution', return_value=classification):
            result = await mixin._handle_execution_failure(ctx)

        assert result.action == "fatal"
        assert "Authentication failed" in result.fatal_message

    @pytest.mark.asyncio
    async def test_retryable_under_limit_returns_continue(self, mixin: _TestableSheetMixin):
        """Retryable error under max retries should return continue with incremented attempts."""
        state = _make_state()
        state.mark_sheet_started(1)
        ctx = _make_failure_ctx(mixin, state, normal_attempts=0, max_retries=3)

        classification = _make_classification(
            category=ErrorCategory.TRANSIENT,
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            message="Timeout",
            retriable=True,
            suggested_wait=10.0,
        )
        with (
            patch.object(mixin, '_classify_execution', return_value=classification),
            patch('asyncio.sleep', new_callable=AsyncMock),
        ):
            result = await mixin._handle_execution_failure(ctx)

        assert result.action == "continue"
        assert result.normal_attempts == 1  # Incremented from 0

    @pytest.mark.asyncio
    async def test_retries_exhausted_returns_fatal(self, mixin: _TestableSheetMixin):
        """When max retries reached and no healing, should return fatal."""
        state = _make_state()
        state.mark_sheet_started(1)
        ctx = _make_failure_ctx(mixin, state, normal_attempts=2, max_retries=3)

        classification = _make_classification(
            category=ErrorCategory.TRANSIENT,
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            message="Timeout",
            retriable=True,
        )
        with patch.object(mixin, '_classify_execution', return_value=classification):
            result = await mixin._handle_execution_failure(ctx)

        assert result.action == "fatal"
        assert "failed after 3 retries" in result.fatal_message

    @pytest.mark.asyncio
    async def test_pending_recovery_recorded_on_failure(self, mixin: _TestableSheetMixin):
        """When pending_recovery exists, it should be recorded and cleared."""
        state = _make_state()
        state.mark_sheet_started(1)

        mock_store = MagicMock()
        mock_store.check_recent_pattern_discoveries.return_value = []
        mixin._global_learning_store = mock_store

        # Set cli_model so _get_effective_model() returns a value
        mixin.config.backend.cli_model = "claude-sonnet-4-5-20250929"

        pending = {
            "error_code": "E301",
            "suggested_wait": 5.0,
            "actual_wait": 10.0,
        }
        ctx = _make_failure_ctx(
            mixin, state, normal_attempts=0, max_retries=3,
            pending_recovery=pending,
        )

        classification = _make_classification(
            category=ErrorCategory.TRANSIENT,
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            message="Timeout",
            retriable=True,
        )
        with (
            patch.object(mixin, '_classify_execution', return_value=classification),
            patch('asyncio.sleep', new_callable=AsyncMock),
        ):
            result = await mixin._handle_execution_failure(ctx)

        mock_store.record_error_recovery.assert_called_once_with(
            error_code="E301",
            suggested_wait=5.0,
            actual_wait=10.0,
            recovery_success=False,
            model="claude-sonnet-4-5-20250929",
        )
        assert result.action == "continue"


# ---------------------------------------------------------------------------
# Per-Sheet Timeout Override Tests
# ---------------------------------------------------------------------------

class TestPerSheetTimeoutOverride:
    """Test that per-sheet timeout overrides are resolved and passed to backend."""

    def test_timeout_overrides_resolved_from_config(self, tmp_path: Path) -> None:
        """Test that timeout_overrides dict is accessible on config."""
        config = _make_config(tmp_path, overrides={
            "backend": {
                "type": "claude_cli",
                "skip_permissions": True,
                "timeout_seconds": 2400.0,
                "timeout_overrides": {7: 28800.0, 1: 60.0},
            },
        })
        assert config.backend.timeout_overrides == {7: 28800.0, 1: 60.0}
        assert config.backend.timeout_seconds == 2400.0

    def test_timeout_override_lookup_for_sheet(self, tmp_path: Path) -> None:
        """Test per-sheet lookup returns correct value or None."""
        config = _make_config(tmp_path, overrides={
            "backend": {
                "type": "claude_cli",
                "skip_permissions": True,
                "timeout_overrides": {3: 120.0},
            },
        })
        overrides = config.backend.timeout_overrides
        assert overrides.get(3) == 120.0
        assert overrides.get(1) is None
        assert overrides.get(99) is None

    def test_empty_overrides_returns_none_for_all(self, tmp_path: Path) -> None:
        """Test that empty overrides dict yields None for any sheet."""
        config = _make_config(tmp_path)
        assert config.backend.timeout_overrides == {}
        assert config.backend.timeout_overrides.get(1) is None


# ===========================================================================
# Tests: Runner Callback Events (Issue #49, Phase 2)
# ===========================================================================


class TestSheetStartedEvent:
    """Verify sheet.started event fires with correct data."""

    @pytest.mark.asyncio
    async def test_sheet_started_event_fires(self, mixin: _TestableSheetMixin):
        """sheet.started event fires after preflight passes."""
        events: list[tuple[str, int, str, dict[str, Any] | None]] = []
        mixin.event_callback = lambda job_id, sheet_num, event, data: events.append(
            (job_id, sheet_num, event, data)
        )

        state = _make_state()
        mock_vr = _make_validation_result(all_passed=True)
        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=mock_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        started_events = [(j, s, e, d) for j, s, e, d in events if e == "sheet.started"]
        assert len(started_events) >= 1
        _, sheet_num, event_name, data = started_events[0]
        assert sheet_num == 1
        assert event_name == "sheet.started"
        assert "execution_mode" in data
        assert "prompt_tokens" in data


class TestSheetCompletedEvent:
    """Verify sheet.completed event fires with correct data."""

    @pytest.mark.asyncio
    async def test_sheet_completed_event_fires(self, mixin: _TestableSheetMixin):
        """sheet.completed event fires when sheet succeeds."""
        events: list[tuple[str, int, str, dict[str, Any] | None]] = []
        mixin.event_callback = lambda job_id, sheet_num, event, data: events.append(
            (job_id, sheet_num, event, data)
        )

        state = _make_state()
        mock_vr = _make_validation_result(all_passed=True)
        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=mock_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        completed_events = [(j, s, e, d) for j, s, e, d in events if e == "sheet.completed"]
        assert len(completed_events) == 1
        _, sheet_num, _, data = completed_events[0]
        assert sheet_num == 1
        assert "duration_seconds" in data
        assert "exit_code" in data
        assert data["validation_pass_rate"] == 100.0
        assert "outcome_category" in data


class TestSheetFailedEvent:
    """Verify sheet.failed event fires with correct data."""

    @pytest.mark.asyncio
    async def test_sheet_failed_event_on_fatal_error(self, mixin: _TestableSheetMixin):
        """sheet.failed event fires when sheet fails fatally."""
        events: list[tuple[str, int, str, dict[str, Any] | None]] = []
        mixin.event_callback = lambda job_id, sheet_num, event, data: events.append(
            (job_id, sheet_num, event, data)
        )

        state = _make_state()
        mixin.backend.execute = AsyncMock(
            return_value=_make_execution_result(
                success=False, exit_code=127, stderr="Command not found"
            )
        )

        fail_vr = _make_validation_result(all_passed=False, pass_pct=0.0, confidence=0.5)

        # Mock classify to return non-retriable error
        fatal_error = MagicMock()
        fatal_error.is_rate_limit = False
        fatal_error.should_retry = False
        fatal_error.message = "CLI not found"
        fatal_error.category = MagicMock(value="cli_error")
        fatal_error.error_code = MagicMock(value="E501")
        fatal_error.suggested_wait_seconds = None
        fatal_class = MagicMock()
        fatal_class.primary = fatal_error
        fatal_class.confidence = 1.0
        fatal_class.secondary = []
        fatal_class.raw_errors = []
        fatal_class.classification_method = "exit_code"
        fatal_class.all_errors = [fatal_error]
        mixin._classify_execution = MagicMock(return_value=fatal_class)

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=fail_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            with pytest.raises(FatalError):
                await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        failed_events = [(j, s, e, d) for j, s, e, d in events if e == "sheet.failed"]
        assert len(failed_events) >= 1
        _, sheet_num, _, data = failed_events[0]
        assert sheet_num == 1
        assert "error_category" in data
        assert "exit_code" in data
        assert "duration_seconds" in data

    @pytest.mark.asyncio
    async def test_sheet_failed_event_on_retries_exhausted(self, mixin: _TestableSheetMixin):
        """sheet.failed event fires with retries_exhausted=True when retries are exhausted."""
        events: list[tuple[str, int, str, dict[str, Any] | None]] = []
        mixin.event_callback = lambda job_id, sheet_num, event, data: events.append(
            (job_id, sheet_num, event, data)
        )

        state = _make_state()
        mixin.backend.execute = AsyncMock(
            return_value=_make_execution_result(
                success=False, exit_code=1, stderr="Error occurred"
            )
        )
        fail_vr = _make_validation_result(all_passed=False, pass_pct=0.0, confidence=0.5)

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=fail_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            with pytest.raises(FatalError):
                await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        # There should be at least one sheet.failed event with retries_exhausted
        failed_events = [(j, s, e, d) for j, s, e, d in events if e == "sheet.failed"]
        assert len(failed_events) >= 1
        # The last failed event should have retries_exhausted=True
        last_failed = failed_events[-1]
        _, sheet_num, _, data = last_failed
        assert sheet_num == 1
        assert data.get("retries_exhausted") is True


class TestSheetValidationPassedEvent:
    """Verify sheet.validation_passed event fires with correct data."""

    @pytest.mark.asyncio
    async def test_sheet_validation_passed_event_fires(self, mixin: _TestableSheetMixin):
        """sheet.validation_passed fires when all validations pass."""
        events: list[tuple[str, int, str, dict[str, Any] | None]] = []
        mixin.event_callback = lambda job_id, sheet_num, event, data: events.append(
            (job_id, sheet_num, event, data)
        )

        state = _make_state()
        mock_vr = _make_validation_result(all_passed=True, pass_pct=100.0)
        # Set concrete values for passed_count, failed_count, pass_percentage
        mock_vr.passed_count = 3
        mock_vr.failed_count = 0
        mock_vr.pass_percentage = 100.0

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(return_value=mock_vr)
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        val_passed = [(j, s, e, d) for j, s, e, d in events if e == "sheet.validation_passed"]
        assert len(val_passed) >= 1
        _, sheet_num, _, data = val_passed[0]
        assert sheet_num == 1
        assert data["passed_count"] == 3
        assert data["failed_count"] == 0
        assert data["pass_rate"] == 100.0


class TestSheetValidationFailedEvent:
    """Verify sheet.validation_failed event fires with correct data."""

    @pytest.mark.asyncio
    async def test_sheet_validation_failed_event_fires(self, mixin: _TestableSheetMixin):
        """sheet.validation_failed fires when validations fail."""
        events: list[tuple[str, int, str, dict[str, Any] | None]] = []
        mixin.event_callback = lambda job_id, sheet_num, event, data: events.append(
            (job_id, sheet_num, event, data)
        )

        state = _make_state()
        # First validation fails, second passes (triggers retry that succeeds)
        fail_vr = _make_validation_result(all_passed=False, pass_pct=50.0, confidence=0.5)
        fail_vr.passed_count = 1
        fail_vr.failed_count = 1
        fail_vr.pass_percentage = 50.0
        pass_vr = _make_validation_result(all_passed=True, pass_pct=100.0)
        pass_vr.passed_count = 2
        pass_vr.failed_count = 0
        pass_vr.pass_percentage = 100.0

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(side_effect=[fail_vr, pass_vr])
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        val_failed = [(j, s, e, d) for j, s, e, d in events if e == "sheet.validation_failed"]
        assert len(val_failed) >= 1
        _, sheet_num, _, data = val_failed[0]
        assert sheet_num == 1
        assert data["passed_count"] == 1
        assert data["failed_count"] == 1
        assert data["pass_rate"] == 50.0


class TestSheetRetryingEvent:
    """Verify sheet.retrying event fires with correct data."""

    @pytest.mark.asyncio
    async def test_sheet_retrying_event_fires(self, mixin: _TestableSheetMixin):
        """sheet.retrying fires on transient retry with attempt info."""
        events: list[tuple[str, int, str, dict[str, Any] | None]] = []
        mixin.event_callback = lambda job_id, sheet_num, event, data: events.append(
            (job_id, sheet_num, event, data)
        )

        state = _make_state()
        # First call fails, second succeeds
        mixin.backend.execute = AsyncMock(side_effect=[
            _make_execution_result(success=False, exit_code=1, stderr="Error"),
            _make_execution_result(success=True, exit_code=0),
        ])

        fail_vr = _make_validation_result(all_passed=False, pass_pct=0.0, confidence=0.5)
        pass_vr = _make_validation_result(all_passed=True)

        with patch("mozart.execution.runner.sheet.ValidationEngine") as MockVE:
            ve_instance = MockVE.return_value
            ve_instance.get_applicable_rules.return_value = []
            ve_instance.run_validations = AsyncMock(side_effect=[fail_vr, pass_vr])
            ve_instance.snapshot_mtime_files = MagicMock()

            await mixin._execute_sheet_with_recovery(state, sheet_num=1)

        retry_events = [(j, s, e, d) for j, s, e, d in events if e == "sheet.retrying"]
        assert len(retry_events) >= 1
        _, sheet_num, _, data = retry_events[0]
        assert sheet_num == 1
        assert "attempt" in data
        assert "max_retries" in data
        assert "reason" in data
        assert "wait_seconds" in data


class TestPerSheetBackendOverride:
    """Test per-sheet backend parameter overrides (GH#78)."""

    def test_sheet_overrides_resolved_from_config(self, tmp_path: Path) -> None:
        """Test that sheet_overrides dict is accessible on config."""
        config = _make_config(tmp_path, overrides={
            "backend": {
                "type": "anthropic_api",
                "model": "claude-sonnet-4-5-20250929",
                "sheet_overrides": {
                    1: {"model": "claude-opus-4-6", "temperature": 0.0},
                    5: {"timeout_seconds": 600.0},
                },
            },
        })
        assert len(config.backend.sheet_overrides) == 2
        assert config.backend.sheet_overrides[1].model == "claude-opus-4-6"
        assert config.backend.sheet_overrides[1].temperature == 0.0
        assert config.backend.sheet_overrides[5].timeout_seconds == 600.0

    def test_sheet_overrides_lookup_for_sheet(self, tmp_path: Path) -> None:
        """Test per-sheet lookup returns correct override or None."""
        config = _make_config(tmp_path, overrides={
            "backend": {
                "type": "claude_cli",
                "sheet_overrides": {
                    3: {"cli_model": "claude-opus-4-6"},
                },
            },
        })
        override = config.backend.sheet_overrides.get(3)
        assert override is not None
        assert override.cli_model == "claude-opus-4-6"
        assert config.backend.sheet_overrides.get(1) is None

    def test_empty_sheet_overrides(self, tmp_path: Path) -> None:
        """Test that empty sheet_overrides dict yields None for any sheet."""
        config = _make_config(tmp_path)
        assert config.backend.sheet_overrides == {}
        assert config.backend.sheet_overrides.get(1) is None

    def test_sheet_override_timeout_takes_precedence(self, tmp_path: Path) -> None:
        """Test timeout in sheet_overrides takes precedence over timeout_overrides."""
        config = _make_config(tmp_path, overrides={
            "backend": {
                "type": "claude_cli",
                "timeout_overrides": {1: 60.0},
                "sheet_overrides": {
                    1: {"timeout_seconds": 120.0},
                },
            },
        })
        # Both are set, but runner logic gives sheet_overrides precedence
        assert config.backend.timeout_overrides[1] == 60.0
        assert config.backend.sheet_overrides[1].timeout_seconds == 120.0
