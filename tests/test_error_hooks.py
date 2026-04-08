"""Tests for marianne.learning.error_hooks module.

Covers ErrorLearningConfig, ErrorLearningContext, ErrorLearningHooks
(on_error_classified, on_error_recovered, on_auth_failure, get_error_stats),
wrap_classifier_with_learning, and record_error_recovery convenience function.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from marianne.core.errors import (
    ClassificationResult,
    ClassifiedError,
    ErrorCategory,
    ErrorCode,
)
from marianne.learning.error_hooks import (
    ErrorLearningConfig,
    ErrorLearningContext,
    ErrorLearningHooks,
    record_error_recovery,
    wrap_classifier_with_learning,
)

# ─── Fixtures ──────────────────────────────────────────────────────────


def _stub_store_connection(
    mock_store: MagicMock,
    fetchone_result: dict | None,
) -> None:
    """Wire a mock GlobalLearningStore so _get_connection yields a fake cursor.

    This avoids repeating the contextmanager + mock_conn + mock_cursor
    boilerplate in every test that exercises on_auth_failure / get_error_stats.
    """
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = fetchone_result
    mock_conn.execute.return_value = mock_cursor

    @contextmanager
    def fake_connection():
        yield mock_conn

    mock_store._get_connection = fake_connection


def _make_error(
    *,
    category: ErrorCategory = ErrorCategory.RATE_LIMIT,
    error_code: ErrorCode = ErrorCode.RATE_LIMIT_API,
    message: str = "Rate limited",
    retriable: bool = True,
    suggested_wait: float | None = 60.0,
) -> ClassifiedError:
    """Create a ClassifiedError for testing."""
    return ClassifiedError(
        category=category,
        message=message,
        error_code=error_code,
        retriable=retriable,
        suggested_wait_seconds=suggested_wait,
    )


def _make_context(
    error: ClassifiedError | ClassificationResult | None = None,
    **kwargs,
) -> ErrorLearningContext:
    """Create an ErrorLearningContext for testing."""
    return ErrorLearningContext(
        error=error or _make_error(),
        job_id=kwargs.get("job_id", "test-job"),
        sheet_num=kwargs.get("sheet_num", 1),
        workspace_path=kwargs.get("workspace_path", Path("/tmp/test")),
        model=kwargs.get("model", "claude-3"),
        suggested_wait=kwargs.get("suggested_wait"),
        actual_wait=kwargs.get("actual_wait"),
        recovery_success=kwargs.get("recovery_success"),
    )


@pytest.fixture
def mock_store() -> MagicMock:
    """Create a mock GlobalLearningStore."""
    store = MagicMock()
    store.record_pattern = MagicMock()
    store.record_error_recovery = MagicMock()
    store.get_learned_wait_time = MagicMock(return_value=None)
    return store


@pytest.fixture
def hooks(mock_store: MagicMock) -> ErrorLearningHooks:
    """Create ErrorLearningHooks with mock store."""
    return ErrorLearningHooks(global_store=mock_store)


# ─── ErrorLearningConfig ─────────────────────────────────────────────


class TestErrorLearningConfig:
    """Tests for ErrorLearningConfig defaults."""

    def test_defaults(self):
        config = ErrorLearningConfig()
        assert config.enabled is True
        assert config.min_samples == 3
        assert config.learning_rate == 0.3
        assert config.max_wait_time == 7200.0
        assert config.min_wait_time == 10.0
        assert config.decay_factor == 0.9

    def test_custom_values(self):
        config = ErrorLearningConfig(enabled=False, min_samples=10)
        assert config.enabled is False
        assert config.min_samples == 10


# ─── ErrorLearningContext ─────────────────────────────────────────────


class TestErrorLearningContext:
    """Tests for ErrorLearningContext properties."""

    def test_error_code_from_classified_error(self):
        error = _make_error(error_code=ErrorCode.RATE_LIMIT_API)
        ctx = _make_context(error=error)
        assert ctx.error_code == ErrorCode.RATE_LIMIT_API  # E101

    def test_error_code_from_classification_result(self):
        primary = _make_error(error_code=ErrorCode.EXECUTION_TIMEOUT)
        result = ClassificationResult(primary=primary)
        ctx = _make_context(error=result)
        assert ctx.error_code == ErrorCode.EXECUTION_TIMEOUT

    def test_category_from_classified_error(self):
        error = _make_error(category=ErrorCategory.RATE_LIMIT)
        ctx = _make_context(error=error)
        assert ctx.category == ErrorCategory.RATE_LIMIT

    def test_category_from_classification_result(self):
        primary = _make_error(category=ErrorCategory.AUTH)
        result = ClassificationResult(primary=primary)
        ctx = _make_context(error=result)
        assert ctx.category == ErrorCategory.AUTH

    def test_timestamp_defaults(self):
        ctx = _make_context()
        assert isinstance(ctx.timestamp, datetime)


# ─── ErrorLearningHooks.enabled ──────────────────────────────────────


class TestHooksEnabled:
    """Tests for ErrorLearningHooks.enabled property."""

    def test_enabled_with_store(self, hooks: ErrorLearningHooks):
        assert hooks.enabled is True

    def test_disabled_without_store(self):
        h = ErrorLearningHooks(global_store=None)
        assert h.enabled is False

    def test_disabled_via_config(self, mock_store: MagicMock):
        config = ErrorLearningConfig(enabled=False)
        h = ErrorLearningHooks(global_store=mock_store, config=config)
        assert h.enabled is False


# ─── on_error_classified ─────────────────────────────────────────────


class TestOnErrorClassified:
    """Tests for ErrorLearningHooks.on_error_classified()."""

    def test_disabled_returns_error_unchanged(self):
        h = ErrorLearningHooks(global_store=None)
        error = _make_error()
        ctx = _make_context(error=error)
        result = h.on_error_classified(ctx)
        assert result.error_code == error.error_code
        assert result.suggested_wait_seconds == error.suggested_wait_seconds

    def test_records_pattern(self, hooks: ErrorLearningHooks, mock_store: MagicMock):
        ctx = _make_context()
        hooks.on_error_classified(ctx)
        mock_store.record_pattern.assert_called_once()

    def test_rate_limit_checks_learned_wait(self, hooks: ErrorLearningHooks, mock_store: MagicMock):
        """Rate limit errors query for learned wait times."""
        mock_store.get_learned_wait_time.return_value = None
        error = _make_error(category=ErrorCategory.RATE_LIMIT)
        ctx = _make_context(error=error)
        hooks.on_error_classified(ctx)
        mock_store.get_learned_wait_time.assert_called_once()

    def test_rate_limit_applies_learned_wait(self, hooks: ErrorLearningHooks, mock_store: MagicMock):
        """When learned wait is available, it replaces the original."""
        mock_store.get_learned_wait_time.return_value = 120.0
        error = _make_error(
            category=ErrorCategory.RATE_LIMIT,
            suggested_wait=60.0,
        )
        ctx = _make_context(error=error)
        result = hooks.on_error_classified(ctx)
        assert result.suggested_wait_seconds == 120.0

    def test_learned_wait_bounded_by_min(self, mock_store: MagicMock):
        """Learned wait is clamped to min_wait_time."""
        config = ErrorLearningConfig(min_wait_time=30.0)
        h = ErrorLearningHooks(global_store=mock_store, config=config)
        mock_store.get_learned_wait_time.return_value = 5.0  # Below min
        error = _make_error(category=ErrorCategory.RATE_LIMIT)
        ctx = _make_context(error=error)
        result = h.on_error_classified(ctx)
        assert result.suggested_wait_seconds == 30.0

    def test_learned_wait_bounded_by_max(self, mock_store: MagicMock):
        """Learned wait is clamped to max_wait_time."""
        config = ErrorLearningConfig(max_wait_time=100.0)
        h = ErrorLearningHooks(global_store=mock_store, config=config)
        mock_store.get_learned_wait_time.return_value = 200.0  # Above max
        error = _make_error(category=ErrorCategory.RATE_LIMIT)
        ctx = _make_context(error=error)
        result = h.on_error_classified(ctx)
        assert result.suggested_wait_seconds == 100.0

    def test_non_rate_limit_no_learned_wait(self, hooks: ErrorLearningHooks, mock_store: MagicMock):
        """Non-rate-limit errors don't query learned wait."""
        error = _make_error(category=ErrorCategory.AUTH, error_code=ErrorCode.BACKEND_AUTH)
        ctx = _make_context(error=error)
        hooks.on_error_classified(ctx)
        mock_store.get_learned_wait_time.assert_not_called()

    def test_tracks_pending_context(self, hooks: ErrorLearningHooks, mock_store: MagicMock):
        """Non-rate-limit errors are tracked in pending contexts."""
        error = _make_error(category=ErrorCategory.AUTH, error_code=ErrorCode.BACKEND_AUTH)
        ctx = _make_context(error=error)
        hooks.on_error_classified(ctx)
        assert len(hooks._pending_contexts) == 1

    def test_classification_result_unwrapped(self, hooks: ErrorLearningHooks, mock_store: MagicMock):
        """ClassificationResult is properly unwrapped to ClassifiedError."""
        mock_store.get_learned_wait_time.return_value = None
        primary = _make_error(category=ErrorCategory.RATE_LIMIT)
        result = ClassificationResult(primary=primary)
        ctx = _make_context(error=result)
        returned = hooks.on_error_classified(ctx)
        assert returned.error_code == primary.error_code


# ─── on_error_recovered ──────────────────────────────────────────────


class TestOnErrorRecovered:
    """Tests for ErrorLearningHooks.on_error_recovered()."""

    def test_disabled_noop(self):
        h = ErrorLearningHooks(global_store=None)
        ctx = _make_context(actual_wait=30.0)
        h.on_error_recovered(ctx, success=True)  # Should not raise

    def test_records_recovery(self, hooks: ErrorLearningHooks, mock_store: MagicMock):
        ctx = _make_context(actual_wait=45.0, suggested_wait=60.0)
        hooks.on_error_recovered(ctx, success=True)
        mock_store.record_error_recovery.assert_called_once_with(
            error_code="E101",
            suggested_wait=60.0,
            actual_wait=45.0,
            recovery_success=True,
            model="claude-3",
        )

    def test_no_actual_wait_skips_record(self, hooks: ErrorLearningHooks, mock_store: MagicMock):
        ctx = _make_context(actual_wait=None)
        hooks.on_error_recovered(ctx, success=True)
        mock_store.record_error_recovery.assert_not_called()

    def test_cleans_up_pending_context(self, hooks: ErrorLearningHooks, mock_store: MagicMock):
        # First classify to add to pending
        error = _make_error(category=ErrorCategory.AUTH, error_code=ErrorCode.BACKEND_AUTH)
        ctx = _make_context(error=error)
        hooks.on_error_classified(ctx)
        assert len(hooks._pending_contexts) == 1

        # Then recover to clean up
        ctx.actual_wait = 10.0
        hooks.on_error_recovered(ctx, success=True)
        assert len(hooks._pending_contexts) == 0


# ─── on_auth_failure ─────────────────────────────────────────────────


class TestOnAuthFailure:
    """Tests for ErrorLearningHooks.on_auth_failure()."""

    def test_disabled_returns_non_transient(self):
        h = ErrorLearningHooks(global_store=None)
        error = _make_error(category=ErrorCategory.AUTH, error_code=ErrorCode.BACKEND_AUTH)
        ctx = _make_context(error=error)
        is_transient, reason = h.on_auth_failure(ctx)
        assert not is_transient
        assert "No learning data" in reason

    def test_transient_with_high_recovery(self, mock_store: MagicMock):
        """Auth failure is transient if historical recovery rate > 30%."""
        _stub_store_connection(mock_store, {"successes": 5, "total": 10})
        h = ErrorLearningHooks(global_store=mock_store)

        error = _make_error(category=ErrorCategory.AUTH, error_code=ErrorCode.BACKEND_AUTH)
        ctx = _make_context(error=error)
        is_transient, reason = h.on_auth_failure(ctx)
        assert is_transient
        assert "50%" in reason

    def test_not_transient_with_low_recovery(self, mock_store: MagicMock):
        """Auth failure is not transient if recovery rate <= 30%."""
        _stub_store_connection(mock_store, {"successes": 1, "total": 10})
        h = ErrorLearningHooks(global_store=mock_store)

        error = _make_error(category=ErrorCategory.AUTH, error_code=ErrorCode.BACKEND_AUTH)
        ctx = _make_context(error=error)
        is_transient, _reason = h.on_auth_failure(ctx)
        assert not is_transient

    def test_not_transient_with_few_samples(self, mock_store: MagicMock):
        """Not enough samples → not transient."""
        _stub_store_connection(mock_store, {"successes": 2, "total": 2})
        h = ErrorLearningHooks(global_store=mock_store)

        error = _make_error(category=ErrorCategory.AUTH, error_code=ErrorCode.BACKEND_AUTH)
        ctx = _make_context(error=error)
        is_transient, reason = h.on_auth_failure(ctx)
        assert not is_transient
        assert "Insufficient" in reason


# ─── get_error_stats ──────────────────────────────────────────────────


class TestGetErrorStats:
    """Tests for ErrorLearningHooks.get_error_stats()."""

    def test_disabled_returns_error(self):
        h = ErrorLearningHooks(global_store=None)
        result = h.get_error_stats("E103")
        assert "error" in result

    def test_with_data(self, mock_store: MagicMock):
        _stub_store_connection(mock_store, {
            "total_occurrences": 10,
            "recoveries": 8,
            "avg_wait": 45.123,
            "min_wait": 10.0,
            "max_wait": 120.0,
        })
        h = ErrorLearningHooks(global_store=mock_store)

        result = h.get_error_stats("E103")
        assert result["error_code"] == "E103"
        assert result["total_occurrences"] == 10
        assert result["successful_recoveries"] == 8
        assert result["recovery_rate"] == 80.0
        assert result["avg_wait_seconds"] == 45.1

    def test_no_data(self, mock_store: MagicMock):
        _stub_store_connection(mock_store, None)
        h = ErrorLearningHooks(global_store=mock_store)

        result = h.get_error_stats("E999")
        assert result["error_code"] == "E999"
        assert result["total_occurrences"] == 0


# ─── wrap_classifier_with_learning ───────────────────────────────────


class TestWrapClassifier:
    """Tests for wrap_classifier_with_learning."""

    def test_returns_classifier_and_hooks(self):
        from marianne.core.errors import ErrorClassifier
        classifier = ErrorClassifier()
        c, hooks = wrap_classifier_with_learning(classifier)
        assert c is classifier
        assert isinstance(hooks, ErrorLearningHooks)

    def test_hooks_disabled_without_store(self):
        from marianne.core.errors import ErrorClassifier
        _, hooks = wrap_classifier_with_learning(ErrorClassifier(), global_store=None)
        assert not hooks.enabled


# ─── record_error_recovery convenience ────────────────────────────────


class TestRecordErrorRecovery:
    """Tests for the module-level record_error_recovery function."""

    def test_noop_without_store(self):
        error = _make_error()
        record_error_recovery(None, error, actual_wait=30.0, success=True)

    def test_records_with_classified_error(self, mock_store: MagicMock):
        error = _make_error(
            error_code=ErrorCode.RATE_LIMIT_API,
            suggested_wait=60.0,
        )
        record_error_recovery(mock_store, error, actual_wait=45.0, success=True, model="claude-3")
        mock_store.record_error_recovery.assert_called_once_with(
            error_code="E101",
            suggested_wait=60.0,
            actual_wait=45.0,
            recovery_success=True,
            model="claude-3",
        )

    def test_records_with_classification_result(self, mock_store: MagicMock):
        primary = _make_error(
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            suggested_wait=30.0,
        )
        result = ClassificationResult(primary=primary)
        record_error_recovery(mock_store, result, actual_wait=25.0, success=False)
        mock_store.record_error_recovery.assert_called_once_with(
            error_code="E001",
            suggested_wait=30.0,
            actual_wait=25.0,
            recovery_success=False,
            model=None,
        )

    def test_records_with_no_suggested_wait(self, mock_store: MagicMock):
        error = _make_error(suggested_wait=None)
        record_error_recovery(mock_store, error, actual_wait=10.0, success=True)
        mock_store.record_error_recovery.assert_called_once()
        call_kwargs = mock_store.record_error_recovery.call_args[1]
        assert call_kwargs["suggested_wait"] == 0


# ─── Context Key ──────────────────────────────────────────────────────


class TestContextKey:
    """Tests for _get_context_key."""

    def test_unique_per_job_sheet_error(self):
        hooks = ErrorLearningHooks()
        ctx1 = _make_context(job_id="a", sheet_num=1)
        ctx2 = _make_context(job_id="a", sheet_num=2)
        ctx3 = _make_context(job_id="b", sheet_num=1)
        keys = {hooks._get_context_key(c) for c in [ctx1, ctx2, ctx3]}
        assert len(keys) == 3
