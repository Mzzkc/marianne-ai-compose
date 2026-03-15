"""Targeted coverage tests for execution runner and escalation modules.

Fills specific coverage gaps identified by coverage analysis:
- runner/lifecycle.py: parallel mode, post-success hooks, cleanup finally
- runner/sheet.py: stale detection, checkpoint actions, grounding
- runner/isolation.py: worktree setup, cleanup, fallback paths
- runner/patterns.py: pattern query, dedup, risk assessment, feedback
- runner/context.py: cross-sheet context, injections, file capture
- runner/recovery.py: broadcast polling, cross-workspace rate limits
- escalation.py: console handlers, prompt modification, checkpoint triggers
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from mozart.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from mozart.core.config import JobConfig
from mozart.execution.escalation import (
    CheckpointContext,
    CheckpointResponse,
    CheckpointTrigger,
    ConsoleCheckpointHandler,
    ConsoleEscalationHandler,
    EscalationContext,
    EscalationResponse,
    HistoricalSuggestion,
)
from mozart.execution.runner.models import FatalError, RunSummary
from mozart.execution.runner.patterns import (
    PatternFeedbackContext,
    PatternsMixin,
    _deduplicate_patterns,
    _normalize_for_dedup,
    _unknown_risk,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_config(**overrides: Any) -> JobConfig:
    """Build a minimal JobConfig for testing."""
    data: dict[str, Any] = {
        "name": "test-cov",
        "description": "Coverage gap tests",
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 10, "total_items": 30},
        "prompt": {"template": "Process {{ sheet_num }}."},
        "retry": {
            "max_retries": 2,
            "base_delay_seconds": 0.01,
            "max_delay_seconds": 0.1,
            "jitter": False,
        },
        "validations": [],
        "pause_between_sheets_seconds": 0,
    }
    data.update(overrides)
    return JobConfig(**data)


def _make_state(
    job_id: str = "test-job",
    total_sheets: int = 3,
    status: JobStatus = JobStatus.RUNNING,
) -> CheckpointState:
    """Build a minimal CheckpointState."""
    state = CheckpointState(
        job_id=job_id,
        job_name="test-cov",
        total_sheets=total_sheets,
        status=status,
    )
    return state


# =============================================================================
# Pattern Utilities (runner/patterns.py)
# =============================================================================


class TestNormalizeForDedup:
    """Tests for _normalize_for_dedup function."""

    def test_lowercases_input(self) -> None:
        result = _normalize_for_dedup("FILE NOT FOUND")
        assert result == result.lower()

    def test_replaces_underscores_with_spaces(self) -> None:
        result = _normalize_for_dedup("file_not_found")
        assert "_" not in result

    def test_strips_noise_words(self) -> None:
        result = _normalize_for_dedup("The error in a file")
        # noise words like 'the', 'error', 'a' stripped
        assert "the" not in result.split()
        assert "error" not in result.split()

    def test_truncates_to_30_chars(self) -> None:
        long_text = "x" * 100
        result = _normalize_for_dedup(long_text)
        assert len(result) <= 30

    def test_similar_patterns_produce_same_key(self) -> None:
        key1 = _normalize_for_dedup("File not created")
        key2 = _normalize_for_dedup("file_not_created")
        key3 = _normalize_for_dedup("File Not Created Error")
        # All should normalize similarly
        assert key1 == key2
        assert key1 == key3


class TestDeduplicatePatterns:
    """Tests for _deduplicate_patterns function."""

    def test_keeps_highest_scored_pattern(self) -> None:
        p1 = MagicMock(description="file not found", pattern_name="p1", effectiveness_score=0.3)
        p2 = MagicMock(description="file not found", pattern_name="p2", effectiveness_score=0.9)
        result = _deduplicate_patterns([p1, p2])
        assert len(result) == 1
        assert result[0].effectiveness_score == 0.9

    def test_keeps_distinct_patterns(self) -> None:
        p1 = MagicMock(description="timeout issue", pattern_name="p1", effectiveness_score=0.5)
        p2 = MagicMock(description="memory leak", pattern_name="p2", effectiveness_score=0.5)
        result = _deduplicate_patterns([p1, p2])
        assert len(result) == 2

    def test_uses_pattern_name_when_description_is_none(self) -> None:
        p1 = MagicMock(description=None, pattern_name="timeout_fix", effectiveness_score=0.5)
        result = _deduplicate_patterns([p1])
        assert len(result) == 1

    def test_empty_list(self) -> None:
        result = _deduplicate_patterns([])
        assert result == []


class TestUnknownRisk:
    """Tests for _unknown_risk helper."""

    def test_returns_unknown_risk_dict(self) -> None:
        result = _unknown_risk(["some reason"])
        assert result["risk_level"] == "unknown"
        assert result["confidence"] == 0.0
        assert result["factors"] == ["some reason"]
        assert result["recommended_adjustments"] == []


class TestPatternFeedbackContext:
    """Tests for PatternFeedbackContext dataclass."""

    def test_defaults(self) -> None:
        ctx = PatternFeedbackContext(
            validation_passed=True,
            success_without_retry=True,
            sheet_num=1,
        )
        assert ctx.grounding_confidence is None
        assert ctx.validation_types is None
        assert ctx.error_categories is None
        assert ctx.prior_sheet_status is None
        assert ctx.retry_iteration == 0
        assert ctx.escalation_was_pending is False

    def test_custom_values(self) -> None:
        ctx = PatternFeedbackContext(
            validation_passed=False,
            success_without_retry=False,
            sheet_num=5,
            grounding_confidence=0.85,
            validation_types=["file_check"],
            error_categories=["E101"],
            prior_sheet_status="failed",
            retry_iteration=2,
            escalation_was_pending=True,
        )
        assert ctx.grounding_confidence == 0.85
        assert ctx.validation_types == ["file_check"]
        assert ctx.retry_iteration == 2
        assert ctx.escalation_was_pending is True


# =============================================================================
# PatternsMixin (runner/patterns.py)
# =============================================================================


class MockPatternRunner(PatternsMixin):
    """Minimal mock runner for PatternsMixin tests."""

    def __init__(self) -> None:
        self.config = _make_config()
        self._logger = MagicMock()
        self.console = MagicMock()
        self._global_learning_store = None
        self.outcome_store = None
        self._exploration_pattern_ids: list[str] = []
        self._exploitation_pattern_ids: list[str] = []


class TestPatternsMixin:
    """Tests for PatternsMixin methods."""

    def test_query_patterns_no_store(self) -> None:
        """No global store returns empty."""
        runner = MockPatternRunner()
        descriptions, ids = runner._query_relevant_patterns("job-1", 1)
        assert descriptions == []
        assert ids == []

    def test_query_patterns_empty_store(self) -> None:
        """Global store with no patterns returns empty."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_patterns_for_auto_apply.return_value = []
        store.get_patterns.return_value = []
        runner._global_learning_store = store
        runner.config = _make_config()

        descriptions, ids = runner._query_relevant_patterns("job-1", 1)
        assert descriptions == []
        assert ids == []

    def test_query_patterns_with_results(self) -> None:
        """Patterns are formatted with effectiveness indicators."""
        runner = MockPatternRunner()
        store = MagicMock()
        pattern = MagicMock(
            id="p-1",
            description="Use completion mode",
            pattern_name="completion_mode",
            effectiveness_score=0.8,
            occurrence_count=5,
            priority_score=0.5,
        )
        store.get_patterns_for_auto_apply.return_value = []
        store.get_patterns.return_value = [pattern]
        runner._global_learning_store = store

        with patch("mozart.execution.runner.patterns.random") as mock_random:
            mock_random.random.return_value = 0.99  # exploitation mode
            descriptions, ids = runner._query_relevant_patterns("job-1", 1)

        assert len(descriptions) == 1
        assert "✓" in descriptions[0]  # high effectiveness
        assert "p-1" in ids

    def test_query_patterns_low_effectiveness_indicator(self) -> None:
        """Low effectiveness patterns get ⚠ indicator."""
        runner = MockPatternRunner()
        store = MagicMock()
        pattern = MagicMock(
            id="p-low",
            description="Untested pattern",
            pattern_name="untested",
            effectiveness_score=0.2,
            occurrence_count=1,
            priority_score=0.1,
        )
        store.get_patterns_for_auto_apply.return_value = []
        store.get_patterns.return_value = [pattern]
        runner._global_learning_store = store

        with patch("mozart.execution.runner.patterns.random") as mock_random:
            mock_random.random.return_value = 0.01  # exploration mode
            descriptions, ids = runner._query_relevant_patterns("job-1", 1)

        assert len(descriptions) == 1
        assert "⚠" in descriptions[0]

    def test_query_patterns_moderate_effectiveness_indicator(self) -> None:
        """Moderate effectiveness patterns get ○ indicator."""
        runner = MockPatternRunner()
        store = MagicMock()
        pattern = MagicMock(
            id="p-mod",
            description="Moderate pattern",
            pattern_name="moderate",
            effectiveness_score=0.5,
            occurrence_count=3,
            priority_score=0.5,
        )
        store.get_patterns_for_auto_apply.return_value = []
        store.get_patterns.return_value = [pattern]
        runner._global_learning_store = store

        with patch("mozart.execution.runner.patterns.random") as mock_random:
            mock_random.random.return_value = 0.99
            descriptions, ids = runner._query_relevant_patterns("job-1", 1)

        assert "○" in descriptions[0]

    def test_query_patterns_sqlite_integrity_error(self) -> None:
        """sqlite3.IntegrityError is caught and returns empty."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_patterns_for_auto_apply.return_value = []
        store.get_patterns.side_effect = sqlite3.IntegrityError("FK violation")
        runner._global_learning_store = store

        with patch("mozart.execution.runner.patterns.random") as mock_random:
            mock_random.random.return_value = 0.99
            descriptions, ids = runner._query_relevant_patterns("job-1", 1)
        assert descriptions == []
        assert ids == []
        runner.console.print.assert_called_once()  # warning printed

    def test_query_patterns_generic_sqlite_error(self) -> None:
        """Generic sqlite3.Error is caught and returns empty."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_patterns_for_auto_apply.return_value = []
        store.get_patterns.side_effect = sqlite3.OperationalError("db locked")
        runner._global_learning_store = store

        with patch("mozart.execution.runner.patterns.random") as mock_random:
            mock_random.random.return_value = 0.99
            descriptions, ids = runner._query_relevant_patterns("job-1", 1)
        assert descriptions == []
        assert ids == []

    def test_query_patterns_with_context_tags(self) -> None:
        """Custom context tags are passed through to the first query."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_patterns_for_auto_apply.return_value = []
        store.get_patterns.return_value = []
        runner._global_learning_store = store

        with patch("mozart.execution.runner.patterns.random") as mock_random:
            mock_random.random.return_value = 0.99
            runner._query_relevant_patterns("job-1", 1, context_tags=["custom:tag"])

        # The first call passes context_tags; the fallback call doesn't
        first_call_args = store.get_patterns.call_args_list[0]
        assert first_call_args.kwargs.get("context_tags") == ["custom:tag"]

    def test_assess_failure_risk_no_store(self) -> None:
        """No global store returns unknown risk."""
        runner = MockPatternRunner()
        result = runner._assess_failure_risk("job-1", 1)
        assert result["risk_level"] == "unknown"

    def test_assess_failure_risk_insufficient_data(self) -> None:
        """Few executions returns unknown with low confidence."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_execution_stats.return_value = {
            "success_without_retry_rate": 0.5,
            "total_executions": 5,
        }
        store.is_rate_limited.return_value = (False, 0)
        runner._global_learning_store = store

        result = runner._assess_failure_risk("job-1", 1)
        assert result["risk_level"] == "unknown"
        assert result["confidence"] == 0.2

    def test_assess_failure_risk_low_risk(self) -> None:
        """High success rate returns low risk."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_execution_stats.return_value = {
            "success_without_retry_rate": 0.85,
            "total_executions": 50,
        }
        store.is_rate_limited.return_value = (False, 0)
        runner._global_learning_store = store

        result = runner._assess_failure_risk("job-1", 1)
        assert result["risk_level"] == "low"

    def test_assess_failure_risk_medium_risk(self) -> None:
        """Moderate success rate returns medium risk."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_execution_stats.return_value = {
            "success_without_retry_rate": 0.55,
            "total_executions": 50,
        }
        store.is_rate_limited.return_value = (False, 0)
        runner._global_learning_store = store

        result = runner._assess_failure_risk("job-1", 1)
        assert result["risk_level"] == "medium"
        assert "monitor validation confidence" in str(result["recommended_adjustments"])

    def test_assess_failure_risk_high_risk(self) -> None:
        """Low success rate returns high risk."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_execution_stats.return_value = {
            "success_without_retry_rate": 0.2,
            "total_executions": 50,
        }
        store.is_rate_limited.return_value = (False, 0)
        runner._global_learning_store = store

        result = runner._assess_failure_risk("job-1", 1)
        assert result["risk_level"] == "high"
        assert len(result["recommended_adjustments"]) > 0

    def test_assess_failure_risk_with_rate_limit(self) -> None:
        """Active rate limit upgrades risk to high."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_execution_stats.return_value = {
            "success_without_retry_rate": 0.85,
            "total_executions": 50,
        }
        store.is_rate_limited.return_value = (True, 30.0)
        runner._global_learning_store = store

        result = runner._assess_failure_risk("job-1", 1)
        assert result["risk_level"] == "high"
        assert any("rate limit" in f for f in result["factors"])

    def test_assess_failure_risk_integrity_error(self) -> None:
        """IntegrityError returns unknown risk."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_execution_stats.side_effect = sqlite3.IntegrityError("test")
        runner._global_learning_store = store

        result = runner._assess_failure_risk("job-1", 1)
        assert result["risk_level"] == "unknown"

    def test_assess_failure_risk_generic_error(self) -> None:
        """Generic error returns unknown risk."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.get_execution_stats.side_effect = OSError("disk error")
        runner._global_learning_store = store

        result = runner._assess_failure_risk("job-1", 1)
        assert result["risk_level"] == "unknown"

    @pytest.mark.asyncio
    async def test_record_pattern_feedback_no_store(self) -> None:
        """No global store skips recording."""
        runner = MockPatternRunner()
        ctx = PatternFeedbackContext(
            validation_passed=True, success_without_retry=True, sheet_num=1,
        )
        await runner._record_pattern_feedback(["p-1"], ctx)
        # Should not error

    @pytest.mark.asyncio
    async def test_record_pattern_feedback_empty_ids(self) -> None:
        """Empty pattern IDs skips recording."""
        runner = MockPatternRunner()
        runner._global_learning_store = MagicMock()
        ctx = PatternFeedbackContext(
            validation_passed=True, success_without_retry=True, sheet_num=1,
        )
        await runner._record_pattern_feedback([], ctx)
        runner._global_learning_store.record_pattern_application.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_pattern_feedback_success(self) -> None:
        """Successful pattern feedback records and updates success factors."""
        runner = MockPatternRunner()
        store = MagicMock()
        runner._global_learning_store = store
        runner._exploration_pattern_ids = ["p-1"]
        runner._exploitation_pattern_ids = []

        ctx = PatternFeedbackContext(
            validation_passed=True,
            success_without_retry=True,
            sheet_num=1,
            grounding_confidence=0.9,
            validation_types=["file_check"],
        )
        await runner._record_pattern_feedback(["p-1"], ctx)

        store.record_pattern_application.assert_called_once()
        store.update_success_factors.assert_called_once()
        call_kwargs = store.record_pattern_application.call_args.kwargs
        assert call_kwargs["application_mode"] == "exploration"
        assert call_kwargs["pattern_led_to_success"] is True

    @pytest.mark.asyncio
    async def test_record_pattern_feedback_failure(self) -> None:
        """Failed pattern feedback records but does not update success factors."""
        runner = MockPatternRunner()
        store = MagicMock()
        runner._global_learning_store = store
        runner._exploration_pattern_ids = []
        runner._exploitation_pattern_ids = ["p-2"]

        ctx = PatternFeedbackContext(
            validation_passed=False,
            success_without_retry=False,
            sheet_num=2,
        )
        await runner._record_pattern_feedback(["p-2"], ctx)

        store.record_pattern_application.assert_called_once()
        store.update_success_factors.assert_not_called()
        call_kwargs = store.record_pattern_application.call_args.kwargs
        assert call_kwargs["application_mode"] == "exploitation"
        assert call_kwargs["pattern_led_to_success"] is False

    @pytest.mark.asyncio
    async def test_record_pattern_feedback_integrity_error(self) -> None:
        """IntegrityError prints warning on console."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.record_pattern_application.side_effect = sqlite3.IntegrityError("FK")
        runner._global_learning_store = store

        ctx = PatternFeedbackContext(
            validation_passed=True, success_without_retry=True, sheet_num=1,
        )
        await runner._record_pattern_feedback(["p-1"], ctx)
        runner.console.print.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_pattern_feedback_transient_error(self) -> None:
        """Transient errors are logged as warnings."""
        runner = MockPatternRunner()
        store = MagicMock()
        store.record_pattern_application.side_effect = OSError("disk full")
        runner._global_learning_store = store

        ctx = PatternFeedbackContext(
            validation_passed=True, success_without_retry=True, sheet_num=1,
        )
        await runner._record_pattern_feedback(["p-1"], ctx)
        runner._logger.warning.assert_called()


# =============================================================================
# IsolationMixin (runner/isolation.py)
# =============================================================================


class TestIsolationMixin:
    """Tests for IsolationMixin worktree setup and cleanup."""

    @pytest.mark.asyncio
    async def test_setup_isolation_disabled(self) -> None:
        """Disabled isolation returns None immediately."""
        from mozart.execution.runner.isolation import IsolationMixin

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config()
        mixin._logger = MagicMock()
        state = _make_state()

        result = await mixin._setup_isolation(state)
        assert result is None

    @pytest.mark.asyncio
    async def test_setup_isolation_unsupported_mode(self) -> None:
        """Non-worktree mode returns None with warning."""
        from mozart.execution.runner.isolation import IsolationMixin

        mixin = IsolationMixin.__new__(IsolationMixin)
        config = _make_config(isolation={"enabled": True, "mode": "worktree"})
        mixin.config = config
        mixin._logger = MagicMock()

        # Patch the mode to something unsupported
        with patch.object(config.isolation, "mode") as mock_mode:
            mock_mode.value = "unsupported"
            # Since IsolationMode only has worktree, we'll test the exact code path
            # by making mode != IsolationMode.WORKTREE
            from mozart.core.config import IsolationMode
            mock_mode.__eq__ = lambda self, other: False
            mock_mode.__ne__ = lambda self, other: True

            state = _make_state()
            result = await mixin._setup_isolation(state)
            assert result is None

    @pytest.mark.asyncio
    async def test_setup_isolation_reuses_existing_worktree(self) -> None:
        """Existing worktree path is reused on resume."""
        from mozart.execution.runner.isolation import IsolationMixin

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(isolation={"enabled": True, "mode": "worktree"})
        mixin._logger = MagicMock()

        state = _make_state()
        # Simulate existing worktree
        with patch.object(Path, "exists", return_value=True):
            state.worktree_path = "/tmp/existing-worktree"
            result = await mixin._setup_isolation(state)
            assert result == Path("/tmp/existing-worktree")

    @pytest.mark.asyncio
    async def test_setup_isolation_not_git_repo_fallback(self) -> None:
        """Non-git repo with fallback_on_error returns None."""
        from mozart.execution.runner.isolation import IsolationMixin

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(
            isolation={"enabled": True, "mode": "worktree", "fallback_on_error": True}
        )
        mixin._logger = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.working_directory = None

        state = _make_state()

        with patch("mozart.isolation.worktree.GitWorktreeManager") as MockManager:
            MockManager.return_value.is_git_repository.return_value = False
            result = await mixin._setup_isolation(state)
            assert result is None
            assert state.isolation_fallback_used is True

    @pytest.mark.asyncio
    async def test_setup_isolation_not_git_repo_no_fallback(self) -> None:
        """Non-git repo without fallback raises FatalError."""
        from mozart.execution.runner.isolation import IsolationMixin

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(
            isolation={"enabled": True, "mode": "worktree", "fallback_on_error": False}
        )
        mixin._logger = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.working_directory = None

        state = _make_state()

        with patch("mozart.isolation.worktree.GitWorktreeManager") as MockManager:
            MockManager.return_value.is_git_repository.return_value = False
            with pytest.raises(FatalError, match="git repository"):
                await mixin._setup_isolation(state)

    @pytest.mark.asyncio
    async def test_setup_isolation_creation_failure_with_fallback(self) -> None:
        """Worktree creation failure with fallback returns None."""
        from mozart.execution.runner.isolation import IsolationMixin

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(
            isolation={"enabled": True, "mode": "worktree", "fallback_on_error": True}
        )
        mixin._logger = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.working_directory = None

        state = _make_state()

        with patch("mozart.isolation.worktree.GitWorktreeManager") as MockManager:
            mgr = MockManager.return_value
            mgr.is_git_repository.return_value = True
            result_mock = MagicMock(success=False, error="creation failed", worktree=None)
            mgr.create_worktree_detached = AsyncMock(return_value=result_mock)
            result = await mixin._setup_isolation(state)
            assert result is None
            assert state.isolation_fallback_used is True

    @pytest.mark.asyncio
    async def test_setup_isolation_creation_success(self) -> None:
        """Successful worktree creation returns path."""
        from mozart.execution.runner.isolation import IsolationMixin

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(
            isolation={"enabled": True, "mode": "worktree", "fallback_on_error": True}
        )
        mixin._logger = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.working_directory = None

        state = _make_state()

        with patch("mozart.isolation.worktree.GitWorktreeManager") as MockManager:
            mgr = MockManager.return_value
            mgr.is_git_repository.return_value = True
            worktree_mock = MagicMock(
                path=Path("/tmp/wt-123"),
                branch="detached",
                locked=True,
                commit="abc123",
            )
            result_mock = MagicMock(success=True, worktree=worktree_mock)
            mgr.create_worktree_detached = AsyncMock(return_value=result_mock)
            result = await mixin._setup_isolation(state)
            assert result == Path("/tmp/wt-123")
            assert state.worktree_path == "/tmp/wt-123"
            assert state.isolation_mode == "worktree"

    @pytest.mark.asyncio
    async def test_cleanup_isolation_no_worktree(self) -> None:
        """No worktree path returns immediately."""
        from mozart.execution.runner.isolation import IsolationMixin

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config()
        mixin._logger = MagicMock()
        state = _make_state()
        state.worktree_path = None
        await mixin._cleanup_isolation(state)

    @pytest.mark.asyncio
    async def test_cleanup_isolation_worktree_already_removed(self) -> None:
        """Missing worktree path logs and returns."""
        from mozart.execution.runner.isolation import IsolationMixin

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config()
        mixin._logger = MagicMock()
        state = _make_state()
        state.worktree_path = "/nonexistent/path"
        await mixin._cleanup_isolation(state)
        mixin._logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_isolation_preserves_on_failure(self) -> None:
        """Failed job with cleanup_on_failure=False preserves worktree."""
        from mozart.execution.runner.isolation import IsolationMixin
        import tempfile

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(
            isolation={
                "enabled": True,
                "mode": "worktree",
                "cleanup_on_success": True,
                "cleanup_on_failure": False,
            }
        )
        mixin._logger = MagicMock()

        state = _make_state(status=JobStatus.FAILED)
        with tempfile.TemporaryDirectory() as tmpdir:
            state.worktree_path = tmpdir
            await mixin._cleanup_isolation(state)
            # Should NOT clean up
            mixin._logger.info.assert_called()
            assert "worktree_preserved" in str(mixin._logger.info.call_args)

    @pytest.mark.asyncio
    async def test_cleanup_isolation_preserves_on_pause(self) -> None:
        """Paused job never cleans up worktree."""
        from mozart.execution.runner.isolation import IsolationMixin
        import tempfile

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(
            isolation={
                "enabled": True,
                "mode": "worktree",
                "cleanup_on_success": True,
                "cleanup_on_failure": True,
            }
        )
        mixin._logger = MagicMock()

        state = _make_state(status=JobStatus.PAUSED)
        with tempfile.TemporaryDirectory() as tmpdir:
            state.worktree_path = tmpdir
            await mixin._cleanup_isolation(state)
            mixin._logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_isolation_removes_on_success(self) -> None:
        """Completed job with cleanup_on_success cleans up worktree."""
        from mozart.execution.runner.isolation import IsolationMixin
        import tempfile

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(
            isolation={
                "enabled": True,
                "mode": "worktree",
                "cleanup_on_success": True,
                "cleanup_on_failure": False,
            }
        )
        mixin._logger = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.working_directory = None

        state = _make_state(status=JobStatus.COMPLETED)

        with tempfile.TemporaryDirectory() as tmpdir:
            state.worktree_path = tmpdir
            state.worktree_locked = True

            with patch("mozart.isolation.worktree.GitWorktreeManager") as MockManager:
                mgr = MockManager.return_value
                mgr.unlock_worktree = AsyncMock(return_value=MagicMock(success=True))
                mgr.remove_worktree = AsyncMock(return_value=MagicMock(success=True))

                await mixin._cleanup_isolation(state)

                mgr.unlock_worktree.assert_awaited_once()
                mgr.remove_worktree.assert_awaited_once()
                assert state.worktree_path is None

    @pytest.mark.asyncio
    async def test_cleanup_isolation_unlock_failure(self) -> None:
        """Unlock failure is warned but cleanup continues."""
        from mozart.execution.runner.isolation import IsolationMixin
        import tempfile

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(
            isolation={
                "enabled": True,
                "mode": "worktree",
                "cleanup_on_success": True,
            }
        )
        mixin._logger = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.working_directory = None

        state = _make_state(status=JobStatus.COMPLETED)

        with tempfile.TemporaryDirectory() as tmpdir:
            state.worktree_path = tmpdir
            state.worktree_locked = True

            with patch("mozart.isolation.worktree.GitWorktreeManager") as MockManager:
                mgr = MockManager.return_value
                mgr.unlock_worktree = AsyncMock(
                    return_value=MagicMock(success=False, error="lock error")
                )
                mgr.remove_worktree = AsyncMock(return_value=MagicMock(success=True))

                await mixin._cleanup_isolation(state)
                mixin._logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_isolation_remove_failure(self) -> None:
        """Removal failure is warned but doesn't fail job."""
        from mozart.execution.runner.isolation import IsolationMixin
        import tempfile

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(
            isolation={
                "enabled": True,
                "mode": "worktree",
                "cleanup_on_success": True,
            }
        )
        mixin._logger = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.working_directory = None

        state = _make_state(status=JobStatus.COMPLETED)

        with tempfile.TemporaryDirectory() as tmpdir:
            state.worktree_path = tmpdir
            state.worktree_locked = False

            with patch("mozart.isolation.worktree.GitWorktreeManager") as MockManager:
                mgr = MockManager.return_value
                mgr.remove_worktree = AsyncMock(
                    return_value=MagicMock(success=False, error="busy")
                )

                await mixin._cleanup_isolation(state)
                mixin._logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_isolation_exception_caught(self) -> None:
        """Exceptions during cleanup operations are caught and logged."""
        from mozart.execution.runner.isolation import IsolationMixin
        import tempfile

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config(
            isolation={
                "enabled": True,
                "mode": "worktree",
                "cleanup_on_success": True,
            }
        )
        mixin._logger = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.working_directory = None

        state = _make_state(status=JobStatus.COMPLETED)

        with tempfile.TemporaryDirectory() as tmpdir:
            state.worktree_path = tmpdir
            state.worktree_locked = False

            with patch("mozart.isolation.worktree.GitWorktreeManager") as MockManager:
                mgr = MockManager.return_value
                # Make the remove operation raise inside the try/except
                mgr.remove_worktree = AsyncMock(side_effect=RuntimeError("unexpected"))

                await mixin._cleanup_isolation(state)
                mixin._logger.warning.assert_called()

    def test_get_effective_working_directory_with_worktree(self) -> None:
        """Active worktree path is used as working directory."""
        from mozart.execution.runner.isolation import IsolationMixin
        import tempfile

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config()

        state = _make_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            state.worktree_path = tmpdir
            result = mixin._get_effective_working_directory(state)
            assert result == Path(tmpdir)

    def test_get_effective_working_directory_no_worktree(self) -> None:
        """No worktree falls back to config workspace."""
        from mozart.execution.runner.isolation import IsolationMixin

        mixin = IsolationMixin.__new__(IsolationMixin)
        mixin.config = _make_config()

        state = _make_state()
        state.worktree_path = None
        result = mixin._get_effective_working_directory(state)
        assert result == mixin.config.workspace


# =============================================================================
# ContextBuildingMixin (runner/context.py)
# =============================================================================


class TestContextBuildingMixin:
    """Tests for ContextBuildingMixin methods."""

    def _make_mixin(self) -> Any:
        from mozart.execution.runner.context import ContextBuildingMixin

        mixin = ContextBuildingMixin.__new__(ContextBuildingMixin)
        mixin.config = _make_config()
        mixin._logger = MagicMock()

        # Mock prompt builder
        mock_builder = MagicMock()
        context_mock = MagicMock()
        context_mock.injected_context = []
        context_mock.injected_skills = []
        context_mock.injected_tools = []
        context_mock.previous_outputs = {}
        context_mock.previous_files = {}
        context_mock.to_dict.return_value = {"sheet_num": 1, "workspace": "/tmp"}
        mock_builder.build_sheet_context.return_value = context_mock
        mixin.prompt_builder = mock_builder
        return mixin

    def test_build_sheet_context_basic(self) -> None:
        """Basic context building works."""
        mixin = self._make_mixin()
        ctx = mixin._build_sheet_context(1)
        assert ctx is not None

    def test_resolve_injections_empty(self) -> None:
        """No injection items is a no-op."""
        mixin = self._make_mixin()
        context_mock = MagicMock()
        context_mock.to_dict.return_value = {}
        mixin._resolve_injections(context_mock, 1)

    def test_resolve_injections_missing_context_file(self) -> None:
        """Missing context file logs warning and skips."""
        from mozart.core.config.job import InjectionCategory, InjectionItem

        mixin = self._make_mixin()
        # Add a prelude injection pointing to nonexistent file
        item = InjectionItem(file="/nonexistent/file.txt", as_=InjectionCategory.CONTEXT)
        mixin.config.sheet.prelude = [item]

        context_mock = MagicMock()
        context_mock.injected_context = []
        context_mock.injected_skills = []
        context_mock.injected_tools = []
        context_mock.to_dict.return_value = {"sheet_num": 1}
        mixin._resolve_injections(context_mock, 1)
        mixin._logger.warning.assert_called()

    def test_resolve_injections_missing_skill_file(self) -> None:
        """Missing skill file logs error."""
        from mozart.core.config.job import InjectionCategory, InjectionItem

        mixin = self._make_mixin()
        item = InjectionItem(file="/nonexistent/skill.md", as_=InjectionCategory.SKILL)
        mixin.config.sheet.prelude = [item]

        context_mock = MagicMock()
        context_mock.injected_context = []
        context_mock.injected_skills = []
        context_mock.injected_tools = []
        context_mock.to_dict.return_value = {"sheet_num": 1}
        mixin._resolve_injections(context_mock, 1)
        mixin._logger.error.assert_called()

    def test_resolve_injections_reads_valid_file(self) -> None:
        """Valid file is read and injected into context."""
        import tempfile
        from mozart.core.config.job import InjectionCategory, InjectionItem

        mixin = self._make_mixin()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            f.flush()
            item = InjectionItem(file=f.name, as_=InjectionCategory.CONTEXT)
            mixin.config.sheet.prelude = [item]

            context_mock = MagicMock()
            context_mock.injected_context = []
            context_mock.injected_skills = []
            context_mock.injected_tools = []
            context_mock.to_dict.return_value = {"sheet_num": 1}
            mixin._resolve_injections(context_mock, 1)
            assert "test content" in context_mock.injected_context

            Path(f.name).unlink()

    def test_resolve_injections_jinja_error(self) -> None:
        """Jinja template error in path logs warning."""
        from mozart.core.config.job import InjectionCategory, InjectionItem

        mixin = self._make_mixin()
        item = InjectionItem(file="{{ invalid }", as_=InjectionCategory.CONTEXT)
        mixin.config.sheet.prelude = [item]

        context_mock = MagicMock()
        context_mock.injected_context = []
        context_mock.injected_skills = []
        context_mock.injected_tools = []
        context_mock.to_dict.return_value = {"sheet_num": 1}
        mixin._resolve_injections(context_mock, 1)
        mixin._logger.warning.assert_called()

    def test_populate_cross_sheet_context_stdout(self) -> None:
        """Cross-sheet stdout capture works."""
        from mozart.execution.runner.context import ContextBuildingMixin

        mixin = self._make_mixin()
        state = _make_state(total_sheets=3)

        # Set up sheet 1 with stdout
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        state.sheets[1].stdout_tail = "output from sheet 1"

        cross_sheet = MagicMock()
        cross_sheet.auto_capture_stdout = True
        cross_sheet.lookback_sheets = 0
        cross_sheet.max_output_chars = 1000
        cross_sheet.capture_files = []

        context_mock = MagicMock()
        context_mock.previous_outputs = {}
        context_mock.previous_files = {}

        mixin._populate_cross_sheet_context(context_mock, state, 2, cross_sheet)
        assert 1 in context_mock.previous_outputs
        assert context_mock.previous_outputs[1] == "output from sheet 1"

    def test_populate_cross_sheet_context_truncation(self) -> None:
        """Long stdout is truncated."""
        mixin = self._make_mixin()
        state = _make_state(total_sheets=3)

        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        state.sheets[1].stdout_tail = "x" * 200

        cross_sheet = MagicMock()
        cross_sheet.auto_capture_stdout = True
        cross_sheet.lookback_sheets = 0
        cross_sheet.max_output_chars = 50
        cross_sheet.capture_files = []

        context_mock = MagicMock()
        context_mock.previous_outputs = {}
        context_mock.previous_files = {}

        mixin._populate_cross_sheet_context(context_mock, state, 2, cross_sheet)
        output = context_mock.previous_outputs[1]
        assert "[truncated]" in output

    def test_populate_cross_sheet_lookback(self) -> None:
        """Lookback limit restricts which sheets are captured."""
        mixin = self._make_mixin()
        state = _make_state(total_sheets=5)

        for i in range(1, 5):
            state.sheets[i] = SheetState(sheet_num=i, status=SheetStatus.COMPLETED)
            state.sheets[i].stdout_tail = f"output {i}"

        cross_sheet = MagicMock()
        cross_sheet.auto_capture_stdout = True
        cross_sheet.lookback_sheets = 2  # Only last 2 sheets
        cross_sheet.max_output_chars = 1000
        cross_sheet.capture_files = []

        context_mock = MagicMock()
        context_mock.previous_outputs = {}
        context_mock.previous_files = {}

        mixin._populate_cross_sheet_context(context_mock, state, 5, cross_sheet)
        # Should only have sheets 3 and 4 (lookback 2 from sheet 5)
        assert 1 not in context_mock.previous_outputs
        assert 2 not in context_mock.previous_outputs
        assert 3 in context_mock.previous_outputs
        assert 4 in context_mock.previous_outputs


# =============================================================================
# Escalation Console Handlers (escalation.py)
# =============================================================================


class TestConsoleEscalationHandler:
    """Tests for ConsoleEscalationHandler interactive methods."""

    def _make_handler(self) -> ConsoleEscalationHandler:
        return ConsoleEscalationHandler()

    def _make_context(self) -> EscalationContext:
        return EscalationContext(
            job_id="test-job",
            sheet_num=1,
            confidence=0.3,
            retry_count=2,
            validation_results=[
                {"passed": True, "description": "file exists"},
                {"passed": False, "description": "content check", "error_message": "mismatch"},
            ],
            error_history=["Error 1", "Error 2"],
            output_summary="Some output text",
            prompt_used="Test prompt for sheet 1",
            historical_suggestions=[],
        )

    def test_print_context_summary(self) -> None:
        """Print context summary without error."""
        handler = self._make_handler()
        ctx = self._make_context()
        handler._print_context_summary(ctx)

    def test_print_context_summary_with_suggestions(self) -> None:
        """Print context with historical suggestions."""
        handler = self._make_handler()
        ctx = self._make_context()
        ctx.historical_suggestions = [
            HistoricalSuggestion(
                action="retry",
                outcome="success",
                confidence=0.5,
                validation_pass_rate=80.0,
                guidance="Try with more context",
            ),
            HistoricalSuggestion(
                action="skip",
                outcome="failed",
                confidence=0.3,
                validation_pass_rate=40.0,
                guidance=None,
            ),
            HistoricalSuggestion(
                action="abort",
                outcome=None,
                confidence=0.2,
                validation_pass_rate=20.0,
                guidance="x" * 100,  # long guidance is truncated
            ),
        ]
        handler._print_context_summary(ctx)

    def test_print_context_summary_long_output(self) -> None:
        """Long output summary is truncated."""
        handler = self._make_handler()
        ctx = self._make_context()
        ctx.output_summary = "x" * 300
        handler._print_context_summary(ctx)

    def test_print_context_summary_long_errors(self) -> None:
        """Long error messages are truncated."""
        handler = self._make_handler()
        ctx = self._make_context()
        ctx.error_history = ["x" * 200]
        handler._print_context_summary(ctx)

    @pytest.mark.asyncio
    async def test_prompt_retry_action(self) -> None:
        """Input 'r' returns retry action."""
        handler = self._make_handler()
        ctx = self._make_context()
        with patch("builtins.input", side_effect=["r", ""]):
            response = await handler._prompt_for_action(ctx)
        assert response.action == "retry"

    @pytest.mark.asyncio
    async def test_prompt_skip_action(self) -> None:
        """Input 's' returns skip action."""
        handler = self._make_handler()
        ctx = self._make_context()
        with patch("builtins.input", side_effect=["s", ""]):
            response = await handler._prompt_for_action(ctx)
        assert response.action == "skip"

    @pytest.mark.asyncio
    async def test_prompt_abort_action(self) -> None:
        """Input 'a' returns abort action."""
        handler = self._make_handler()
        ctx = self._make_context()
        with patch("builtins.input", side_effect=["a"]):
            response = await handler._prompt_for_action(ctx)
        assert response.action == "abort"
        assert "User aborted" in (response.guidance or "")

    @pytest.mark.asyncio
    async def test_prompt_modify_action(self) -> None:
        """Input 'm' returns modify_prompt action."""
        handler = self._make_handler()
        ctx = self._make_context()
        with patch("builtins.input", side_effect=["m", "Add more detail", ""]):
            response = await handler._prompt_for_action(ctx)
        assert response.action == "modify_prompt"
        assert response.modified_prompt is not None

    @pytest.mark.asyncio
    async def test_prompt_modify_cancel(self) -> None:
        """Modify then cancel returns to prompt."""
        handler = self._make_handler()
        ctx = self._make_context()
        with patch("builtins.input", side_effect=["m", "cancel", "a"]):
            response = await handler._prompt_for_action(ctx)
        assert response.action == "abort"

    @pytest.mark.asyncio
    async def test_prompt_invalid_then_valid(self) -> None:
        """Invalid input prompts again."""
        handler = self._make_handler()
        ctx = self._make_context()
        with patch("builtins.input", side_effect=["x", "a"]):
            response = await handler._prompt_for_action(ctx)
        assert response.action == "abort"

    @pytest.mark.asyncio
    async def test_prompt_eof_error(self) -> None:
        """EOFError defaults to abort."""
        handler = self._make_handler()
        ctx = self._make_context()
        with patch("builtins.input", side_effect=EOFError):
            response = await handler._prompt_for_action(ctx)
        assert response.action == "abort"

    def test_get_optional_guidance_with_input(self) -> None:
        """Guidance input returns text."""
        handler = self._make_handler()
        with patch("builtins.input", return_value="some notes"):
            result = handler._get_optional_guidance()
        assert result == "some notes"

    def test_get_optional_guidance_empty(self) -> None:
        """Empty guidance returns None."""
        handler = self._make_handler()
        with patch("builtins.input", return_value=""):
            result = handler._get_optional_guidance()
        assert result is None

    def test_get_optional_guidance_eof(self) -> None:
        """EOFError returns None."""
        handler = self._make_handler()
        with patch("builtins.input", side_effect=EOFError):
            result = handler._get_optional_guidance()
        assert result is None

    def test_get_modified_prompt_prefix(self) -> None:
        """PREFIX: instruction prepends to original."""
        handler = self._make_handler()
        with patch("builtins.input", return_value="PREFIX:Be verbose"):
            result = handler._get_modified_prompt("original prompt")
        assert result is not None
        assert result.startswith("Be verbose")
        assert "original prompt" in result

    def test_get_modified_prompt_replace(self) -> None:
        """REPLACE: instruction replaces entirely."""
        handler = self._make_handler()
        with patch("builtins.input", return_value="REPLACE:New prompt entirely"):
            result = handler._get_modified_prompt("original prompt")
        assert result == "New prompt entirely"

    def test_get_modified_prompt_default_suffix(self) -> None:
        """Default instruction appends as additional guidance."""
        handler = self._make_handler()
        with patch("builtins.input", return_value="be more careful"):
            result = handler._get_modified_prompt("original prompt")
        assert result is not None
        assert "Additional guidance: be more careful" in result

    def test_get_modified_prompt_cancel(self) -> None:
        """'cancel' returns None."""
        handler = self._make_handler()
        with patch("builtins.input", return_value="cancel"):
            result = handler._get_modified_prompt("original prompt")
        assert result is None

    def test_get_modified_prompt_eof(self) -> None:
        """EOFError returns None."""
        handler = self._make_handler()
        with patch("builtins.input", side_effect=EOFError):
            result = handler._get_modified_prompt("original prompt")
        assert result is None

    def test_get_modified_prompt_long_original(self) -> None:
        """Long original prompt is displayed truncated (500 chars)."""
        handler = self._make_handler()
        long_prompt = "x" * 600
        with patch("builtins.input", return_value="REPLACE:short"):
            result = handler._get_modified_prompt(long_prompt)
        assert result == "short"

    @pytest.mark.asyncio
    async def test_should_escalate_auto_retry_first_attempt(self) -> None:
        """With auto_retry_on_first_failure, first attempt does not escalate."""
        handler = self._make_handler()
        handler.auto_retry_on_first_failure = True
        sheet_state = MagicMock()
        sheet_state.attempt_count = 1
        validation_result = MagicMock()
        result = await handler.should_escalate(sheet_state, validation_result, 0.1)
        assert result is False

    @pytest.mark.asyncio
    async def test_should_escalate_auto_retry_second_attempt(self) -> None:
        """With auto_retry_on_first_failure, second attempt escalates."""
        handler = self._make_handler()
        handler.auto_retry_on_first_failure = True
        sheet_state = MagicMock()
        sheet_state.attempt_count = 2
        validation_result = MagicMock()
        result = await handler.should_escalate(sheet_state, validation_result, 0.1)
        assert result is True

    @pytest.mark.asyncio
    async def test_should_escalate_confidence_above_threshold(self) -> None:
        """High confidence does not escalate."""
        handler = self._make_handler()
        sheet_state = MagicMock()
        sheet_state.attempt_count = 5
        validation_result = MagicMock()
        result = await handler.should_escalate(sheet_state, validation_result, 0.9)
        assert result is False

    @pytest.mark.asyncio
    async def test_escalate_calls_prompt(self) -> None:
        """Escalate prints context and prompts for action."""
        handler = self._make_handler()
        ctx = self._make_context()
        with patch("builtins.input", side_effect=["a"]):
            response = await handler.escalate(ctx)
        assert response.action == "abort"


# =============================================================================
# ConsoleCheckpointHandler (escalation.py)
# =============================================================================


class TestConsoleCheckpointHandler:
    """Tests for ConsoleCheckpointHandler trigger matching and prompting."""

    def _make_handler(self) -> ConsoleCheckpointHandler:
        return ConsoleCheckpointHandler()

    @pytest.mark.asyncio
    async def test_should_checkpoint_sheet_num_match(self) -> None:
        """Trigger matches specific sheet number."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="test", sheet_nums=[3])
        result = await handler.should_checkpoint(3, "some prompt", 0, [trigger])
        assert result is trigger

    @pytest.mark.asyncio
    async def test_should_checkpoint_sheet_num_no_match(self) -> None:
        """Trigger doesn't match different sheet number."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="test", sheet_nums=[3])
        result = await handler.should_checkpoint(5, "some prompt", 0, [trigger])
        assert result is None

    @pytest.mark.asyncio
    async def test_should_checkpoint_prompt_contains_match(self) -> None:
        """Trigger matches prompt keyword (case-insensitive)."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="test", prompt_contains=["DELETE"])
        result = await handler.should_checkpoint(1, "please delete files", 0, [trigger])
        assert result is trigger

    @pytest.mark.asyncio
    async def test_should_checkpoint_prompt_no_match(self) -> None:
        """Trigger doesn't match different prompt."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="test", prompt_contains=["DELETE"])
        result = await handler.should_checkpoint(1, "create files", 0, [trigger])
        assert result is None

    @pytest.mark.asyncio
    async def test_should_checkpoint_min_retry_match(self) -> None:
        """Trigger matches when retry count exceeds threshold."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="test", min_retry_count=2)
        result = await handler.should_checkpoint(1, "prompt", 3, [trigger])
        assert result is trigger

    @pytest.mark.asyncio
    async def test_should_checkpoint_min_retry_no_match(self) -> None:
        """Trigger doesn't match when retry count below threshold."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="test", min_retry_count=5)
        result = await handler.should_checkpoint(1, "prompt", 2, [trigger])
        assert result is None

    @pytest.mark.asyncio
    async def test_should_checkpoint_no_triggers(self) -> None:
        """Empty trigger list returns None."""
        handler = self._make_handler()
        result = await handler.should_checkpoint(1, "prompt", 0, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_should_checkpoint_all_conditions_match(self) -> None:
        """Trigger with multiple conditions all matching."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(
            name="strict",
            sheet_nums=[1, 2, 3],
            prompt_contains=["dangerous"],
            min_retry_count=1,
        )
        result = await handler.should_checkpoint(2, "dangerous operation", 2, [trigger])
        assert result is trigger

    @pytest.mark.asyncio
    async def test_checkpoint_no_confirmation(self) -> None:
        """Warning-only checkpoint auto-proceeds."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="warn", requires_confirmation=False)
        ctx = CheckpointContext(
            job_id="job-1",
            sheet_num=1,
            prompt="test",
            trigger=trigger,
            retry_count=0,
            previous_errors=[],
        )
        response = await handler.checkpoint(ctx)
        assert response.action == "proceed"

    @pytest.mark.asyncio
    async def test_checkpoint_proceed_action(self) -> None:
        """Input 'p' returns proceed."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="confirm", requires_confirmation=True, message="Check!")
        ctx = CheckpointContext(
            job_id="job-1",
            sheet_num=1,
            prompt="x" * 400,  # long prompt is truncated
            trigger=trigger,
            retry_count=0,
            previous_errors=["prev error"],
        )
        with patch("builtins.input", side_effect=["p", ""]):
            response = await handler.checkpoint(ctx)
        assert response.action == "proceed"

    @pytest.mark.asyncio
    async def test_checkpoint_skip_action(self) -> None:
        """Input 's' returns skip."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="confirm", requires_confirmation=True)
        ctx = CheckpointContext(
            job_id="job-1", sheet_num=1, prompt="test",
            trigger=trigger, retry_count=0, previous_errors=[],
        )
        with patch("builtins.input", side_effect=["s", ""]):
            response = await handler.checkpoint(ctx)
        assert response.action == "skip"

    @pytest.mark.asyncio
    async def test_checkpoint_abort_action(self) -> None:
        """Input 'a' returns abort."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="confirm", requires_confirmation=True)
        ctx = CheckpointContext(
            job_id="job-1", sheet_num=1, prompt="test",
            trigger=trigger, retry_count=0, previous_errors=[],
        )
        with patch("builtins.input", side_effect=["a"]):
            response = await handler.checkpoint(ctx)
        assert response.action == "abort"

    @pytest.mark.asyncio
    async def test_checkpoint_modify_action(self) -> None:
        """Input 'm' returns modify_prompt."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="confirm", requires_confirmation=True)
        ctx = CheckpointContext(
            job_id="job-1", sheet_num=1, prompt="test prompt",
            trigger=trigger, retry_count=0, previous_errors=[],
        )
        with patch("builtins.input", side_effect=["m", "REPLACE:new prompt", ""]):
            response = await handler.checkpoint(ctx)
        assert response.action == "modify_prompt"
        assert response.modified_prompt == "new prompt"

    @pytest.mark.asyncio
    async def test_checkpoint_eof_input(self) -> None:
        """EOFError defaults to abort."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="confirm", requires_confirmation=True)
        ctx = CheckpointContext(
            job_id="job-1", sheet_num=1, prompt="test",
            trigger=trigger, retry_count=0, previous_errors=[],
        )
        with patch("builtins.input", side_effect=EOFError):
            response = await handler.checkpoint(ctx)
        assert response.action == "abort"

    @pytest.mark.asyncio
    async def test_checkpoint_invalid_then_valid(self) -> None:
        """Invalid input retries until valid."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="confirm", requires_confirmation=True)
        ctx = CheckpointContext(
            job_id="job-1", sheet_num=1, prompt="test",
            trigger=trigger, retry_count=0, previous_errors=[],
        )
        with patch("builtins.input", side_effect=["z", "p", ""]):
            response = await handler.checkpoint(ctx)
        assert response.action == "proceed"

    @pytest.mark.asyncio
    async def test_checkpoint_modify_cancel_then_abort(self) -> None:
        """Modify cancel cycles back to prompt."""
        handler = self._make_handler()
        trigger = CheckpointTrigger(name="confirm", requires_confirmation=True)
        ctx = CheckpointContext(
            job_id="job-1", sheet_num=1, prompt="test",
            trigger=trigger, retry_count=0, previous_errors=[],
        )
        with patch("builtins.input", side_effect=["m", "cancel", "a"]):
            response = await handler.checkpoint(ctx)
        assert response.action == "abort"


# =============================================================================
# RecoveryMixin - broadcast polling (runner/recovery.py)
# =============================================================================


class TestRecoveryBroadcastPolling:
    """Tests for _poll_broadcast_discoveries in RecoveryMixin."""

    def _make_mixin(self) -> Any:
        from mozart.execution.runner.recovery import RecoveryMixin

        mixin = RecoveryMixin.__new__(RecoveryMixin)
        mixin.config = _make_config()
        mixin._logger = MagicMock()
        mixin.console = MagicMock()
        mixin._global_learning_store = None
        mixin.outcome_store = None
        mixin.error_classifier = MagicMock()
        mixin.backend = MagicMock()
        mixin.backend.name = "claude_cli"
        mixin.rate_limit_callback = None
        return mixin

    @pytest.mark.asyncio
    async def test_poll_broadcast_no_store(self) -> None:
        """No global store returns immediately."""
        mixin = self._make_mixin()
        await mixin._poll_broadcast_discoveries(job_id="j-1", sheet_num=1)

    @pytest.mark.asyncio
    async def test_poll_broadcast_with_discoveries(self) -> None:
        """Discoveries are logged and printed to console."""
        mixin = self._make_mixin()
        store = MagicMock()
        discovery = MagicMock(
            pattern_id="p-1",
            pattern_name="fix_timeout",
            pattern_type="WORKAROUND",
            effectiveness_score=0.8,
            context_tags=["tag1"],
        )
        store.check_recent_pattern_discoveries.return_value = [discovery]
        mixin._global_learning_store = store

        await mixin._poll_broadcast_discoveries(job_id="j-1", sheet_num=1)
        mixin._logger.info.assert_called()
        mixin.console.print.assert_called()

    @pytest.mark.asyncio
    async def test_poll_broadcast_empty_discoveries(self) -> None:
        """No discoveries is silent."""
        mixin = self._make_mixin()
        store = MagicMock()
        store.check_recent_pattern_discoveries.return_value = []
        mixin._global_learning_store = store

        await mixin._poll_broadcast_discoveries(job_id="j-1", sheet_num=1)
        mixin.console.print.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_broadcast_error_caught(self) -> None:
        """Errors during polling are caught and warned."""
        mixin = self._make_mixin()
        store = MagicMock()
        store.check_recent_pattern_discoveries.side_effect = sqlite3.OperationalError("locked")
        mixin._global_learning_store = store

        await mixin._poll_broadcast_discoveries(job_id="j-1", sheet_num=1)
        mixin._logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_record_rate_limit_cross_workspace_disabled(self) -> None:
        """Cross-workspace disabled returns early."""
        mixin = self._make_mixin()
        mixin.config = _make_config(
            circuit_breaker={"enabled": True, "cross_workspace_coordination": False}
        )
        state = _make_state()
        await mixin._record_rate_limit_to_global_store(
            state=state, error_code="E101", wait_seconds=30.0,
        )

    @pytest.mark.asyncio
    async def test_record_rate_limit_cross_workspace_success(self) -> None:
        """Cross-workspace rate limit recording works."""
        mixin = self._make_mixin()
        mixin.config = _make_config(
            circuit_breaker={"enabled": True, "cross_workspace_coordination": True}
        )
        store = MagicMock()
        mixin._global_learning_store = store
        state = _make_state()

        with patch.object(mixin, "_get_effective_model", return_value="claude-3-opus"):
            await mixin._record_rate_limit_to_global_store(
                state=state, error_code="E101", wait_seconds=30.0,
            )
        store.record_rate_limit_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_rate_limit_cross_workspace_no_model(self) -> None:
        """No model skips recording."""
        mixin = self._make_mixin()
        mixin.config = _make_config(
            circuit_breaker={"enabled": True, "cross_workspace_coordination": True}
        )
        mixin._global_learning_store = MagicMock()
        state = _make_state()

        with patch.object(mixin, "_get_effective_model", return_value=None):
            await mixin._record_rate_limit_to_global_store(
                state=state, error_code="E101", wait_seconds=30.0,
            )
        mixin._global_learning_store.record_rate_limit_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_rate_limit_cross_workspace_error(self) -> None:
        """Store error is caught and warned."""
        mixin = self._make_mixin()
        mixin.config = _make_config(
            circuit_breaker={"enabled": True, "cross_workspace_coordination": True}
        )
        store = MagicMock()
        store.record_rate_limit_event.side_effect = sqlite3.OperationalError("locked")
        mixin._global_learning_store = store
        state = _make_state()

        with patch.object(mixin, "_get_effective_model", return_value="claude-3-opus"):
            await mixin._record_rate_limit_to_global_store(
                state=state, error_code="E101", wait_seconds=30.0,
            )
        mixin._logger.warning.assert_called()


# =============================================================================
# Lifecycle uncovered paths (runner/lifecycle.py)
# =============================================================================


class TestLifecycleParallelMode:
    """Tests for lifecycle parallel execution mode and post-success hooks."""

    def _make_runner(self) -> Any:
        from mozart.execution.runner.lifecycle import LifecycleMixin

        runner = LifecycleMixin.__new__(LifecycleMixin)
        runner.config = _make_config()
        runner.backend = MagicMock()
        runner.state_backend = MagicMock()
        runner.console = MagicMock()
        runner._logger = MagicMock()
        runner._parallel_executor = None
        runner._dependency_dag = None
        runner._global_learning_store = None
        runner._summary = None
        runner._run_start_time = time.monotonic()
        runner._current_state = None
        runner._sheet_times: list[float] = []
        runner._execution_context = None
        runner._shutdown_requested = False
        return runner

    @pytest.mark.asyncio
    async def test_execute_parallel_mode_fallback(self) -> None:
        """Parallel mode with None executor falls back to sequential."""
        runner = self._make_runner()
        runner._parallel_executor = None

        state = _make_state(total_sheets=1)
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)

        # Mock sequential mode so it doesn't actually run
        runner._execute_sequential_mode = AsyncMock()
        await runner._execute_parallel_mode(state)
        runner._execute_sequential_mode.assert_awaited_once()

    def test_finalize_summary_on_completed_job(self) -> None:
        """Finalize summary computes metrics from completed state."""
        runner = self._make_runner()
        runner._run_start_time = time.monotonic() - 10.0
        runner._sheet_times = [2.0, 3.0]
        runner._summary = RunSummary(job_id="test-job", job_name="test-cov", total_sheets=2)  # Must be pre-created (normally done by run())

        state = _make_state(total_sheets=2, status=JobStatus.COMPLETED)
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        state.sheets[1].validation_passed = True
        state.sheets[1].attempt_count = 1
        state.sheets[2] = SheetState(sheet_num=2, status=SheetStatus.FAILED)
        state.sheets[2].validation_passed = False
        state.sheets[2].attempt_count = 3

        runner._finalize_summary(state)
        assert runner._summary is not None
        assert runner._summary.completed_sheets == 1
        assert runner._summary.failed_sheets == 1
        assert runner._summary.total_retries == 2  # 3 attempts - 1

    def test_get_summary_returns_none_before_run(self) -> None:
        """get_summary returns None before any run."""
        runner = self._make_runner()
        assert runner.get_summary() is None

    def test_get_summary_returns_summary_after_finalize(self) -> None:
        """get_summary returns summary after finalize."""
        runner = self._make_runner()
        runner._run_start_time = time.monotonic() - 1.0
        runner._sheet_times = [1.0]
        runner._summary = RunSummary(job_id="test-job", job_name="test-cov", total_sheets=2)

        state = _make_state(total_sheets=1, status=JobStatus.COMPLETED)
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        state.sheets[1].validation_passed = True
        state.sheets[1].attempt_count = 1

        runner._finalize_summary(state)
        summary = runner.get_summary()
        assert summary is not None
        assert summary.completed_sheets == 1


# =============================================================================
# Lifecycle: parallel mode, hooks, aggregation (runner/lifecycle.py)
# =============================================================================


class TestLifecyclePostSuccessHooks:
    """Tests for _execute_post_success_hooks."""

    def _make_runner(self) -> Any:
        from mozart.execution.runner.lifecycle import LifecycleMixin

        runner = LifecycleMixin.__new__(LifecycleMixin)
        runner.config = _make_config()
        runner.backend = MagicMock()
        runner.state_backend = AsyncMock()
        runner.console = MagicMock()
        runner._logger = MagicMock()
        runner._summary = RunSummary(job_id="test-job", job_name="test-cov", total_sheets=2)
        return runner

    @pytest.mark.asyncio
    async def test_no_on_success_hooks(self) -> None:
        """No on_success hooks returns early."""
        runner = self._make_runner()
        runner.config = _make_config()
        state = _make_state(status=JobStatus.COMPLETED)
        await runner._execute_post_success_hooks(state)
        runner.state_backend.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_hook_execution(self) -> None:
        """Hooks execute and results are recorded."""
        runner = self._make_runner()
        runner.config = _make_config(on_success=[{"type": "run_command", "command": "echo done"}])

        state = _make_state(status=JobStatus.COMPLETED)

        hook_result = MagicMock(
            success=True,
            hook_type="command",
            description="echo done",
            exit_code=0,
            error_message=None,
            duration_seconds=0.1,
            chained_job_path=None,
            chained_job_info=None,
        )

        with patch("mozart.execution.runner.lifecycle.HookExecutor") as MockExec:
            MockExec.return_value.execute_hooks = AsyncMock(return_value=[hook_result])
            await runner._execute_post_success_hooks(state)

        runner.state_backend.save.assert_called()
        assert runner._summary.hooks_executed == 1
        assert runner._summary.hooks_succeeded == 1

    @pytest.mark.asyncio
    async def test_failed_hook_downgrades_status(self) -> None:
        """Failed hooks downgrade job status from COMPLETED to FAILED."""
        runner = self._make_runner()
        runner.config = _make_config(on_success=[{"type": "run_command", "command": "fail-cmd"}])

        state = _make_state(status=JobStatus.COMPLETED)

        hook_result = MagicMock(
            success=False,
            hook_type="command",
            description="fail-cmd",
            exit_code=1,
            error_message="Command failed",
            duration_seconds=0.1,
            chained_job_path=None,
            chained_job_info=None,
        )

        with patch("mozart.execution.runner.lifecycle.HookExecutor") as MockExec:
            MockExec.return_value.execute_hooks = AsyncMock(return_value=[hook_result])
            await runner._execute_post_success_hooks(state)

        assert state.status == JobStatus.FAILED
        assert "on_success hook" in state.error_message
        runner._logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_empty_hook_results(self) -> None:
        """Empty hook results returns early."""
        runner = self._make_runner()
        runner.config = _make_config(on_success=[{"type": "run_command", "command": "echo"}])

        state = _make_state(status=JobStatus.COMPLETED)

        with patch("mozart.execution.runner.lifecycle.HookExecutor") as MockExec:
            MockExec.return_value.execute_hooks = AsyncMock(return_value=[])
            await runner._execute_post_success_hooks(state)

        runner.state_backend.save.assert_not_called()


class TestLifecycleParallelBatches:
    """Tests for _execute_parallel_mode batch processing."""

    def _make_runner(self) -> Any:
        from mozart.execution.runner.lifecycle import LifecycleMixin

        runner = LifecycleMixin.__new__(LifecycleMixin)
        runner.config = _make_config(
            parallel={"enabled": True, "max_concurrent": 2}
        )
        runner.backend = MagicMock()
        runner.state_backend = AsyncMock()
        runner.console = MagicMock()
        runner._logger = MagicMock()
        runner._summary = RunSummary(job_id="test-job", job_name="test-cov", total_sheets=2)
        runner._run_start_time = time.monotonic()
        runner._sheet_times: list[float] = []
        runner._shutdown_requested = False
        return runner

    @pytest.mark.asyncio
    async def test_parallel_mode_with_executor(self) -> None:
        """Parallel mode executes batches via ParallelExecutor."""
        runner = self._make_runner()
        state = _make_state(total_sheets=2)

        executor = MagicMock()
        batch_result = MagicMock(
            sheets=[1, 2],
            completed=[1, 2],
            failed=[],
            error_details={},
            sheet_outputs={},
            synthesis_ready=False,
        )
        # First call returns batch, second returns empty (done)
        executor.get_next_parallel_batch.side_effect = [[1, 2], []]
        executor.execute_batch = AsyncMock(return_value=batch_result)
        runner._parallel_executor = executor

        # Mock methods called in parallel mode
        runner._check_pause_signal = MagicMock(return_value=False)
        runner._should_skip_sheet = AsyncMock(return_value=None)
        runner._update_progress = MagicMock()
        runner._synthesize_batch_outputs = AsyncMock()
        runner._interruptible_sleep = AsyncMock()

        def _get_completed(s: Any) -> list[int]:
            return [1, 2]
        runner._get_completed_sheets = _get_completed

        await runner._execute_parallel_mode(state)

        executor.execute_batch.assert_awaited_once()
        runner._logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_parallel_mode_fail_fast(self) -> None:
        """Parallel mode with fail_fast raises FatalError on first failure."""
        runner = self._make_runner()
        runner.config = _make_config(
            parallel={"enabled": True, "max_concurrent": 2, "fail_fast": True}
        )
        state = _make_state(total_sheets=2)

        executor = MagicMock()
        batch_result = MagicMock(
            sheets=[1, 2],
            completed=[1],
            failed=[2],
            error_details={2: "Sheet 2 error"},
            sheet_outputs={},
            synthesis_ready=False,
        )
        executor.get_next_parallel_batch.return_value = [1, 2]
        executor.execute_batch = AsyncMock(return_value=batch_result)
        runner._parallel_executor = executor

        runner._check_pause_signal = MagicMock(return_value=False)
        runner._should_skip_sheet = AsyncMock(return_value=None)
        runner._update_progress = MagicMock()
        runner._synthesize_batch_outputs = AsyncMock()
        runner._finalize_summary = MagicMock()

        with pytest.raises(FatalError, match="Sheet 2"):
            await runner._execute_parallel_mode(state)

    @pytest.mark.asyncio
    async def test_parallel_mode_no_fail_fast(self) -> None:
        """Parallel mode without fail_fast marks sheets as permanently failed."""
        runner = self._make_runner()
        runner.config = _make_config(
            parallel={"enabled": True, "max_concurrent": 2, "fail_fast": False}
        )
        state = _make_state(total_sheets=2)

        executor = MagicMock()
        executor._permanently_failed = set()
        batch_result = MagicMock(
            sheets=[1, 2],
            completed=[1],
            failed=[2],
            error_details={2: "Sheet 2 error"},
            sheet_outputs={},
            synthesis_ready=False,
        )
        executor.get_next_parallel_batch.side_effect = [[1, 2], []]
        executor.execute_batch = AsyncMock(return_value=batch_result)
        runner._parallel_executor = executor

        runner._check_pause_signal = MagicMock(return_value=False)
        runner._should_skip_sheet = AsyncMock(return_value=None)
        runner._update_progress = MagicMock()
        runner._synthesize_batch_outputs = AsyncMock()
        runner._interruptible_sleep = AsyncMock()

        def _get_completed(s: Any) -> list[int]:
            return [1]
        runner._get_completed_sheets = _get_completed

        await runner._execute_parallel_mode(state)

        assert 2 in executor._permanently_failed

    @pytest.mark.asyncio
    async def test_parallel_mode_skip_sheets(self) -> None:
        """Parallel mode skips sheets matching skip conditions."""
        runner = self._make_runner()
        state = _make_state(total_sheets=2)

        executor = MagicMock()
        executor.get_next_parallel_batch.side_effect = [[1, 2], []]
        executor.execute_batch = AsyncMock()
        runner._parallel_executor = executor

        runner._check_pause_signal = MagicMock(return_value=False)
        # All sheets are skipped
        runner._should_skip_sheet = AsyncMock(return_value="already done")
        runner._update_progress = MagicMock()
        runner._interruptible_sleep = AsyncMock()

        await runner._execute_parallel_mode(state)
        # execute_batch should NOT be called since all sheets skipped
        executor.execute_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_parallel_mode_shutdown(self) -> None:
        """Parallel mode respects shutdown request."""
        runner = self._make_runner()
        runner._shutdown_requested = True
        state = _make_state(total_sheets=2)

        executor = MagicMock()
        runner._parallel_executor = executor
        runner._handle_graceful_shutdown = AsyncMock()

        await runner._execute_parallel_mode(state)
        runner._handle_graceful_shutdown.assert_awaited_once()


class TestLifecycleSynthesizer:
    """Tests for _synthesize_batch_outputs."""

    def _make_runner(self) -> Any:
        from mozart.execution.runner.lifecycle import LifecycleMixin

        runner = LifecycleMixin.__new__(LifecycleMixin)
        runner._logger = MagicMock()
        return runner

    @pytest.mark.asyncio
    async def test_synthesize_no_completed_sheets(self) -> None:
        """No completed sheets returns early."""
        runner = self._make_runner()
        batch_result = MagicMock(completed=[], sheets=[1, 2], failed=[1, 2])
        state = _make_state()
        await runner._synthesize_batch_outputs(batch_result, state)

    @pytest.mark.asyncio
    async def test_synthesize_no_outputs(self) -> None:
        """Completed sheets with no stdout returns early."""
        runner = self._make_runner()
        batch_result = MagicMock(completed=[1], sheets=[1], failed=[])
        state = _make_state()
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        state.sheets[1].stdout_tail = ""
        await runner._synthesize_batch_outputs(batch_result, state)
        runner._logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_synthesize_with_outputs(self) -> None:
        """Synthesizer runs on completed sheets with output."""
        runner = self._make_runner()
        batch_result = MagicMock(completed=[1], sheets=[1], failed=[])
        state = _make_state()
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        state.sheets[1].stdout_tail = "some output"

        with patch("mozart.execution.synthesizer.ResultSynthesizer") as MockSynth:
            with patch("mozart.execution.synthesizer.SynthesisConfig"):
                synth_instance = MockSynth.return_value
                synth_result = MagicMock(
                    status="ready", batch_id="b-1", to_dict=MagicMock(return_value={})
                )
                synth_instance.prepare_synthesis.return_value = synth_result
                synth_instance.execute_synthesis.return_value = synth_result

                await runner._synthesize_batch_outputs(batch_result, state)

                synth_instance.prepare_synthesis.assert_called_once()
                runner._logger.info.assert_called()


class TestLifecycleGlobalAggregation:
    """Tests for _aggregate_to_global_store."""

    def _make_runner(self) -> Any:
        from mozart.execution.runner.lifecycle import LifecycleMixin

        runner = LifecycleMixin.__new__(LifecycleMixin)
        runner.config = _make_config()
        runner.console = MagicMock()
        runner._logger = MagicMock()
        runner._global_learning_store = None
        return runner

    @pytest.mark.asyncio
    async def test_aggregate_no_store(self) -> None:
        """No global store returns early."""
        runner = self._make_runner()
        state = _make_state()
        await runner._aggregate_to_global_store(state)

    @pytest.mark.asyncio
    async def test_aggregate_with_outcomes(self) -> None:
        """Outcomes are aggregated to global store."""
        runner = self._make_runner()
        store = MagicMock()
        runner._global_learning_store = store

        state = _make_state(total_sheets=2, status=JobStatus.COMPLETED)
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        state.sheets[1].validation_passed = True
        state.sheets[1].attempt_count = 1
        state.sheets[1].success_without_retry = True
        state.sheets[2] = SheetState(sheet_num=2, status=SheetStatus.FAILED)
        state.sheets[2].validation_passed = False
        state.sheets[2].attempt_count = 2

        with patch("mozart.learning.aggregator.EnhancedPatternAggregator") as MockAgg:
            agg_result = MagicMock(
                outcomes_recorded=2,
                patterns_detected=1,
                patterns_merged=0,
                priorities_updated=1,
            )
            MockAgg.return_value.aggregate_outcomes.return_value = agg_result

            await runner._aggregate_to_global_store(state)

            MockAgg.return_value.aggregate_outcomes.assert_called_once()
            runner.console.print.assert_called()

    @pytest.mark.asyncio
    async def test_aggregate_no_sheets(self) -> None:
        """State with no populated sheets returns early."""
        runner = self._make_runner()
        runner._global_learning_store = MagicMock()
        state = _make_state(total_sheets=1)
        # Don't populate state.sheets — empty dict
        state.sheets.clear()
        await runner._aggregate_to_global_store(state)

    @pytest.mark.asyncio
    async def test_aggregate_validation_none(self) -> None:
        """Sheet with None validation maps to 50% pass rate."""
        runner = self._make_runner()
        store = MagicMock()
        runner._global_learning_store = store

        state = _make_state(total_sheets=1, status=JobStatus.COMPLETED)
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        state.sheets[1].validation_passed = None  # Unknown
        state.sheets[1].attempt_count = 1

        with patch("mozart.learning.aggregator.EnhancedPatternAggregator") as MockAgg:
            agg_result = MagicMock(
                outcomes_recorded=1,
                patterns_detected=0,
                patterns_merged=0,
                priorities_updated=0,
            )
            MockAgg.return_value.aggregate_outcomes.return_value = agg_result

            await runner._aggregate_to_global_store(state)

            # Verify the SheetOutcome was created with validation_pass_rate=50.0
            call_args = MockAgg.return_value.aggregate_outcomes.call_args
            outcomes = call_args.kwargs.get("outcomes") or call_args.args[0]
            assert outcomes[0].validation_pass_rate == 50.0


class TestLifecycleSkipWhenCommand:
    """Tests for _should_skip_sheet with command-based conditions."""

    def _make_runner(self) -> Any:
        from mozart.execution.runner.lifecycle import LifecycleMixin

        runner = LifecycleMixin.__new__(LifecycleMixin)
        runner.config = _make_config()
        runner._logger = MagicMock()
        runner.console = MagicMock()
        runner._dependency_dag = None
        return runner

    @pytest.mark.asyncio
    async def test_skip_when_command_success(self, tmp_path: Path) -> None:
        """Command returning 0 triggers skip."""
        runner = self._make_runner()
        runner.config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 10,
                "total_items": 30,
                "skip_when_command": {
                    1: {"command": "true", "timeout_seconds": 5, "description": "Already done"},
                },
            }
        )

        state = _make_state()
        result = await runner._should_skip_sheet(1, state)
        assert result is not None
        assert "Already done" in result

    @pytest.mark.asyncio
    async def test_skip_when_command_failure(self) -> None:
        """Command returning non-zero does NOT trigger skip."""
        runner = self._make_runner()
        runner.config = _make_config(
            sheet={
                "size": 10,
                "total_items": 30,
                "skip_when_command": {
                    1: {"command": "false", "timeout_seconds": 5},
                },
            }
        )

        state = _make_state()
        result = await runner._should_skip_sheet(1, state)
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_when_command_timeout(self) -> None:
        """Command timeout does NOT trigger skip."""
        runner = self._make_runner()
        runner.config = _make_config(
            sheet={
                "size": 10,
                "total_items": 30,
                "skip_when_command": {
                    1: {"command": "sleep 60", "timeout_seconds": 0.1},
                },
            }
        )

        state = _make_state()
        result = await runner._should_skip_sheet(1, state)
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_when_no_command_for_sheet(self) -> None:
        """No command configured for this sheet number returns None."""
        runner = self._make_runner()
        runner.config = _make_config(
            sheet={
                "size": 10,
                "total_items": 30,
                "skip_when_command": {
                    5: {"command": "true", "timeout_seconds": 5},
                },
            }
        )

        state = _make_state()
        result = await runner._should_skip_sheet(1, state)
        assert result is None


class TestLifecycleSequentialMode:
    """Tests for sequential mode edge cases."""

    def _make_runner(self) -> Any:
        from mozart.execution.runner.lifecycle import LifecycleMixin

        runner = LifecycleMixin.__new__(LifecycleMixin)
        runner.config = _make_config()
        runner.backend = MagicMock()
        runner.state_backend = AsyncMock()
        runner.console = MagicMock()
        runner._logger = MagicMock()
        runner._summary = RunSummary(job_id="test-job", job_name="test-cov", total_sheets=2)
        runner._run_start_time = time.monotonic()
        runner._sheet_times: list[float] = []
        runner._shutdown_requested = False
        runner._parallel_executor = None
        runner._dependency_dag = None
        return runner

    @pytest.mark.asyncio
    async def test_sequential_cancel_error(self) -> None:
        """CancelledError during sheet execution pauses job."""
        runner = self._make_runner()
        state = _make_state(total_sheets=1)

        runner._get_next_sheet_dag_aware = MagicMock(return_value=1)
        runner._check_pause_signal = MagicMock(return_value=False)
        runner._should_skip_sheet = AsyncMock(return_value=None)
        runner._execute_sheet_with_recovery = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with pytest.raises(asyncio.CancelledError):
            await runner._execute_sequential_mode(state)

        assert state.status == JobStatus.PAUSED

    @pytest.mark.asyncio
    async def test_sequential_fatal_error(self) -> None:
        """FatalError during sheet execution fails job."""
        runner = self._make_runner()
        state = _make_state(total_sheets=1)

        runner._get_next_sheet_dag_aware = MagicMock(return_value=1)
        runner._check_pause_signal = MagicMock(return_value=False)
        runner._should_skip_sheet = AsyncMock(return_value=None)
        runner._execute_sheet_with_recovery = AsyncMock(
            side_effect=FatalError("Unrecoverable")
        )
        runner._finalize_summary = MagicMock()
        runner._update_progress = MagicMock()
        runner._fire_event = AsyncMock()

        with pytest.raises(FatalError):
            await runner._execute_sequential_mode(state)

        assert state.status == JobStatus.FAILED


# =============================================================================
# Auto-apply patterns in query (runner/patterns.py)
# =============================================================================


class TestPatternsAutoApply:
    """Tests for auto-apply pattern integration in query."""

    def test_auto_apply_patterns_merged(self) -> None:
        """Auto-apply patterns are merged with regular patterns."""
        runner = MockPatternRunner()
        # Enable auto-apply in config
        runner.config = _make_config(
            learning={
                "enabled": True,
                "auto_apply": {
                    "enabled": True,
                    "trust_threshold": 0.8,
                    "max_patterns_per_sheet": 3,
                    "require_validated_status": False,
                    "log_applications": True,
                },
            }
        )

        store = MagicMock()
        auto_pattern = MagicMock(
            id="auto-1",
            description="Auto-applied pattern",
            pattern_name="auto",
            effectiveness_score=0.95,
            occurrence_count=10,
            priority_score=0.9,
        )
        regular_pattern = MagicMock(
            id="reg-1",
            description="Regular pattern",
            pattern_name="regular",
            effectiveness_score=0.6,
            occurrence_count=5,
            priority_score=0.5,
        )
        store.get_patterns_for_auto_apply.return_value = [auto_pattern]
        store.get_patterns.return_value = [regular_pattern]
        runner._global_learning_store = store

        with patch("mozart.execution.runner.patterns.random") as mock_random:
            mock_random.random.return_value = 0.99
            descriptions, ids = runner._query_relevant_patterns("job-1", 1)

        assert len(ids) == 2
        assert ids[0] == "auto-1"  # auto-apply first
        assert ids[1] == "reg-1"

    def test_auto_apply_dedup_with_regular(self) -> None:
        """Auto-apply patterns deduplicated against regular patterns."""
        runner = MockPatternRunner()
        runner.config = _make_config(
            learning={
                "enabled": True,
                "auto_apply": {
                    "enabled": True,
                    "trust_threshold": 0.8,
                    "max_patterns_per_sheet": 3,
                    "log_applications": True,
                },
            }
        )

        store = MagicMock()
        shared_pattern = MagicMock(
            id="shared-1",
            description="Shared pattern",
            pattern_name="shared",
            effectiveness_score=0.9,
            occurrence_count=10,
            priority_score=0.9,
        )
        store.get_patterns_for_auto_apply.return_value = [shared_pattern]
        # Same pattern returned from regular query
        store.get_patterns.return_value = [shared_pattern]
        runner._global_learning_store = store

        with patch("mozart.execution.runner.patterns.random") as mock_random:
            mock_random.random.return_value = 0.99
            descriptions, ids = runner._query_relevant_patterns("job-1", 1)

        # Should not duplicate
        assert ids.count("shared-1") == 1


# =============================================================================
# SheetExecutionMixin coverage gaps (runner/sheet.py)
# =============================================================================


class TestSheetCheckpointAndStale:
    """Tests for _check_proactive_checkpoint and stale detection in sheet.py."""

    def _make_sheet_mixin(self) -> Any:
        """Build a minimal SheetExecutionMixin for testing."""
        from mozart.execution.runner.sheet import SheetExecutionMixin

        mixin = SheetExecutionMixin.__new__(SheetExecutionMixin)
        mixin.config = _make_config()
        mixin._logger = MagicMock()
        mixin.console = MagicMock()
        mixin.checkpoint_handler = None
        mixin._global_learning_store = None
        mixin.state_backend = AsyncMock()
        return mixin

    @pytest.mark.asyncio
    async def test_checkpoint_disabled(self) -> None:
        """Checkpoints disabled returns None."""
        mixin = self._make_sheet_mixin()
        state = _make_state()
        result = await mixin._check_proactive_checkpoint(
            state=state, sheet_num=1, prompt="test", retry_count=0,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_checkpoint_enabled_with_triggers_no_match(self) -> None:
        """Checkpoint with non-matching trigger returns None."""
        mixin = self._make_sheet_mixin()
        mixin.config = _make_config(
            checkpoints={
                "enabled": True,
                "triggers": [{"name": "test-trigger", "sheet_nums": [99]}],
            }
        )
        state = _make_state()
        result = await mixin._check_proactive_checkpoint(
            state=state, sheet_num=1, prompt="test", retry_count=0,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_checkpoint_enabled_with_matching_trigger(self) -> None:
        """Checkpoint with matching trigger returns response."""
        mixin = self._make_sheet_mixin()
        mixin.config = _make_config(
            checkpoints={
                "enabled": True,
                "triggers": [
                    {
                        "name": "critical-sheet",
                        "sheet_nums": [1],
                        "requires_confirmation": False,
                    }
                ],
            }
        )
        state = _make_state()
        result = await mixin._check_proactive_checkpoint(
            state=state, sheet_num=1, prompt="test prompt", retry_count=0,
        )
        assert result is not None
        assert result.action == "proceed"  # auto-proceed for non-confirmation

    @pytest.mark.asyncio
    async def test_checkpoint_with_confirmation_proceed(self) -> None:
        """Checkpoint requiring confirmation, user proceeds."""
        mixin = self._make_sheet_mixin()
        mixin.config = _make_config(
            checkpoints={
                "enabled": True,
                "triggers": [
                    {
                        "name": "dangerous",
                        "prompt_contains": ["delete"],
                        "requires_confirmation": True,
                        "message": "This is dangerous!",
                    }
                ],
            }
        )
        state = _make_state()
        with patch("builtins.input", side_effect=["p", ""]):
            result = await mixin._check_proactive_checkpoint(
                state=state, sheet_num=1, prompt="delete everything", retry_count=0,
            )
        assert result is not None
        assert result.action == "proceed"

    @pytest.mark.asyncio
    async def test_checkpoint_with_previous_errors(self) -> None:
        """Checkpoint captures previous errors from sheet state."""
        mixin = self._make_sheet_mixin()
        mixin.config = _make_config(
            checkpoints={
                "enabled": True,
                "triggers": [
                    {
                        "name": "retry-check",
                        "min_retry_count": 1,
                        "requires_confirmation": False,
                    }
                ],
            }
        )
        state = _make_state()
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.IN_PROGRESS)
        state.sheets[1].error_message = "Previous failure"

        result = await mixin._check_proactive_checkpoint(
            state=state, sheet_num=1, prompt="test", retry_count=2,
        )
        assert result is not None

    def test_stale_execution_error_attrs(self) -> None:
        """_StaleExecutionError stores idle_seconds and timeout."""
        from mozart.execution.runner.sheet import _StaleExecutionError

        err = _StaleExecutionError(idle_seconds=120.5, timeout=60.0)
        assert err.idle_seconds == 120.5
        assert err.timeout == 60.0
        assert "120.5" in str(err)

    def test_sheet_skipped_exception(self) -> None:
        """_SheetSkipped is a plain exception."""
        from mozart.execution.runner.sheet import _SheetSkipped

        err = _SheetSkipped()
        assert isinstance(err, Exception)


# =============================================================================
# Bug #120 — Fan-in sheets get silent empty inputs from skipped upstream instances
# =============================================================================


class TestFanInSkippedUpstream120:
    """Tests that fan-in sheets emit a warning when upstream instances are skipped.

    The FILTER (skipped sheets absent from previous_outputs) was already correct.
    The fix adds a structured WARNING log when upstream sheets have SKIPPED status.

    All tests use fresh MagicMock() for _logger to prevent cross-test accumulation.
    """

    def _make_mixin(self) -> Any:
        from mozart.execution.runner.context import ContextBuildingMixin

        mixin = ContextBuildingMixin.__new__(ContextBuildingMixin)
        mixin.config = _make_config()
        mixin._logger = MagicMock()
        mock_builder = MagicMock()
        context_mock = MagicMock()
        context_mock.previous_outputs = {}
        context_mock.previous_files = {}
        mock_builder.build_sheet_context.return_value = context_mock
        mixin.prompt_builder = mock_builder
        return mixin

    def _make_cross_sheet(self) -> MagicMock:
        cs = MagicMock()
        cs.auto_capture_stdout = True
        cs.lookback_sheets = 0
        cs.max_output_chars = 1000
        cs.capture_files = []
        return cs

    def test_skipped_upstream_excluded_from_previous_outputs(self) -> None:
        """Skipped upstream excluded; completed upstream included (TEST-120-A).

        FILTER was already correct — this is a regression gate.
        """
        mixin = self._make_mixin()
        state = _make_state(total_sheets=3)
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.SKIPPED)
        state.sheets[2] = SheetState(sheet_num=2, status=SheetStatus.COMPLETED)
        state.sheets[2].stdout_tail = "output from sheet 2"

        context_mock = MagicMock()
        context_mock.previous_outputs = {}
        context_mock.previous_files = {}

        mixin._populate_cross_sheet_context(
            context_mock, state, 3, self._make_cross_sheet()
        )

        assert 1 not in context_mock.previous_outputs
        assert 2 in context_mock.previous_outputs
        assert context_mock.previous_outputs[2] == "output from sheet 2"

    def test_warning_emitted_when_one_upstream_skipped(self) -> None:
        """Structured warning fires when one fan-out upstream is skipped (TEST-120-B).

        Primary fail-before-pass test — the fix IS this warning.
        """
        mixin = self._make_mixin()
        state = _make_state(total_sheets=3)
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.SKIPPED)
        state.sheets[2] = SheetState(sheet_num=2, status=SheetStatus.COMPLETED)
        state.sheets[2].stdout_tail = "output 2"

        context_mock = MagicMock()
        context_mock.previous_outputs = {}
        context_mock.previous_files = {}

        mixin._populate_cross_sheet_context(
            context_mock, state, 3, self._make_cross_sheet()
        )

        mixin._logger.warning.assert_called_once()
        call_args = mixin._logger.warning.call_args
        assert call_args[0][0] == "fan_in_upstream_skipped"
        assert call_args[1]["fan_in_sheet"] == 3
        assert call_args[1]["skipped_sheets"] == [1]
        assert call_args[1]["received_inputs"] == 1

    def test_no_warning_when_all_upstreams_completed(self) -> None:
        """No warning fires when all upstreams completed normally (TEST-120-C)."""
        mixin = self._make_mixin()
        state = _make_state(total_sheets=3)
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        state.sheets[1].stdout_tail = "output 1"
        state.sheets[2] = SheetState(sheet_num=2, status=SheetStatus.COMPLETED)
        state.sheets[2].stdout_tail = "output 2"

        context_mock = MagicMock()
        context_mock.previous_outputs = {}
        context_mock.previous_files = {}

        mixin._populate_cross_sheet_context(
            context_mock, state, 3, self._make_cross_sheet()
        )

        mixin._logger.warning.assert_not_called()

    def test_all_upstreams_skipped_empty_outputs_with_warning(self) -> None:
        """All skipped: previous_outputs empty + warning lists all skipped (TEST-120-D)."""
        mixin = self._make_mixin()
        state = _make_state(total_sheets=4)
        for i in range(1, 4):
            state.sheets[i] = SheetState(sheet_num=i, status=SheetStatus.SKIPPED)

        context_mock = MagicMock()
        context_mock.previous_outputs = {}
        context_mock.previous_files = {}

        mixin._populate_cross_sheet_context(
            context_mock, state, 4, self._make_cross_sheet()
        )

        assert context_mock.previous_outputs == {}

        mixin._logger.warning.assert_called_once()
        call_args = mixin._logger.warning.call_args
        assert call_args[0][0] == "fan_in_upstream_skipped"
        assert call_args[1]["skipped_sheets"] == [1, 2, 3]
        assert call_args[1]["received_inputs"] == 0

    def test_no_false_warning_for_completed_sheets_with_empty_stdout(self) -> None:
        """Completed sheets with empty stdout do NOT trigger warning (TEST-120-E).

        Regression test for #120 — the discriminant is SKIPPED status, not empty output.
        """
        mixin = self._make_mixin()
        state = _make_state(total_sheets=3)
        # Completed sheets that ran but produced no output
        state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        state.sheets[1].stdout_tail = ""
        state.sheets[2] = SheetState(sheet_num=2, status=SheetStatus.COMPLETED)
        state.sheets[2].stdout_tail = ""

        context_mock = MagicMock()
        context_mock.previous_outputs = {}
        context_mock.previous_files = {}

        mixin._populate_cross_sheet_context(
            context_mock, state, 3, self._make_cross_sheet()
        )

        # Empty output from completed sheets must NOT trigger the warning
        mixin._logger.warning.assert_not_called()
