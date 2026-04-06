"""Tests for mozart.learning.error_hooks module.

Tests cover:
- ErrorLearningConfig dataclass defaults and customization
- ErrorLearningContext properties and creation
- ErrorLearningHooks hook registration, pattern recording, and wait adjustment
- wrap_classifier_with_learning helper function
- record_error_recovery convenience function
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from marianne.core.errors import (
    ClassificationResult,
    ClassifiedError,
    ErrorCategory,
    ErrorClassifier,
    ErrorCode,
)
from marianne.learning.error_hooks import (
    ErrorLearningConfig,
    ErrorLearningContext,
    ErrorLearningHooks,
    record_error_recovery,
    wrap_classifier_with_learning,
)


class TestErrorLearningConfig:
    """Tests for ErrorLearningConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ErrorLearningConfig()
        assert config.enabled is True
        assert config.min_samples == 3
        assert config.learning_rate == 0.3
        assert config.max_wait_time == 7200.0
        assert config.min_wait_time == 10.0
        assert config.decay_factor == 0.9

    def test_custom_values(self):
        """Test custom configuration values."""
        config = ErrorLearningConfig(
            enabled=False,
            min_samples=5,
            learning_rate=0.5,
            max_wait_time=3600.0,
            min_wait_time=5.0,
            decay_factor=0.8,
        )
        assert config.enabled is False
        assert config.min_samples == 5
        assert config.learning_rate == 0.5
        assert config.max_wait_time == 3600.0
        assert config.min_wait_time == 5.0
        assert config.decay_factor == 0.8


class TestErrorLearningContext:
    """Tests for ErrorLearningContext dataclass."""

    @pytest.fixture
    def sample_error(self) -> ClassifiedError:
        """Create a sample classified error."""
        return ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limit exceeded",
            error_code=ErrorCode.CAPACITY_EXCEEDED,  # E103
            original_error=None,
            retriable=True,
            suggested_wait_seconds=60.0,
        )

    @pytest.fixture
    def sample_classification_result(self, sample_error) -> ClassificationResult:
        """Create a sample classification result."""
        return ClassificationResult(
            primary=sample_error,
            secondary=[],
        )

    def test_create_with_classified_error(self, sample_error):
        """Test creating context with ClassifiedError."""
        context = ErrorLearningContext(
            error=sample_error,
            job_id="test-job-123",
            sheet_num=5,
            workspace_path=Path("/tmp/workspace"),
            model="claude-3-sonnet",
        )
        assert context.error == sample_error
        assert context.job_id == "test-job-123"
        assert context.sheet_num == 5
        assert context.model == "claude-3-sonnet"
        assert isinstance(context.timestamp, datetime)

    def test_create_with_classification_result(self, sample_classification_result):
        """Test creating context with ClassificationResult."""
        context = ErrorLearningContext(
            error=sample_classification_result,
            job_id="test-job-456",
            sheet_num=3,
            workspace_path=Path("/tmp/workspace"),
        )
        assert context.error == sample_classification_result

    def test_error_code_property_classified_error(self, sample_error):
        """Test error_code property with ClassifiedError."""
        context = ErrorLearningContext(
            error=sample_error,
            job_id="test",
            sheet_num=1,
            workspace_path=Path("/tmp"),
        )
        assert context.error_code == ErrorCode.CAPACITY_EXCEEDED

    def test_error_code_property_classification_result(self, sample_classification_result):
        """Test error_code property with ClassificationResult."""
        context = ErrorLearningContext(
            error=sample_classification_result,
            job_id="test",
            sheet_num=1,
            workspace_path=Path("/tmp"),
        )
        assert context.error_code == ErrorCode.CAPACITY_EXCEEDED

    def test_category_property_classified_error(self, sample_error):
        """Test category property with ClassifiedError."""
        context = ErrorLearningContext(
            error=sample_error,
            job_id="test",
            sheet_num=1,
            workspace_path=Path("/tmp"),
        )
        assert context.category == ErrorCategory.RATE_LIMIT

    def test_category_property_classification_result(self, sample_classification_result):
        """Test category property with ClassificationResult."""
        context = ErrorLearningContext(
            error=sample_classification_result,
            job_id="test",
            sheet_num=1,
            workspace_path=Path("/tmp"),
        )
        assert context.category == ErrorCategory.RATE_LIMIT

    def test_optional_wait_fields(self, sample_error):
        """Test optional wait-related fields."""
        context = ErrorLearningContext(
            error=sample_error,
            job_id="test",
            sheet_num=1,
            workspace_path=Path("/tmp"),
            suggested_wait=60.0,
            actual_wait=45.0,
            recovery_success=True,
        )
        assert context.suggested_wait == 60.0
        assert context.actual_wait == 45.0
        assert context.recovery_success is True


class TestErrorLearningHooksNoStore:
    """Tests for ErrorLearningHooks without global store (no-op mode)."""

    @pytest.fixture
    def sample_error(self) -> ClassifiedError:
        """Create a sample classified error."""
        return ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limit exceeded",
            error_code=ErrorCode.CAPACITY_EXCEEDED,  # E103
            original_error=None,
            retriable=True,
            suggested_wait_seconds=60.0,
        )

    @pytest.fixture
    def sample_context(self, sample_error) -> ErrorLearningContext:
        """Create a sample context."""
        return ErrorLearningContext(
            error=sample_error,
            job_id="test-job",
            sheet_num=1,
            workspace_path=Path("/tmp"),
            model="claude-3-sonnet",
        )

    def test_enabled_without_store(self):
        """Test that hooks are disabled without store."""
        hooks = ErrorLearningHooks(global_store=None)
        assert hooks.enabled is False

    def test_enabled_with_disabled_config(self):
        """Test that hooks are disabled when config says so."""
        mock_store = MagicMock()
        config = ErrorLearningConfig(enabled=False)
        hooks = ErrorLearningHooks(global_store=mock_store, config=config)
        assert hooks.enabled is False

    def test_on_error_classified_no_store(self, sample_error, sample_context):
        """Test on_error_classified returns error unchanged without store."""
        hooks = ErrorLearningHooks(global_store=None)
        result = hooks.on_error_classified(sample_context)
        assert result == sample_error

    def test_on_error_recovered_no_store(self, sample_context):
        """Test on_error_recovered is no-op without store."""
        hooks = ErrorLearningHooks(global_store=None)
        hooks.on_error_recovered(sample_context, success=True)
        assert hooks._store is None
        assert hooks.enabled is False

    def test_on_auth_failure_no_store(self, sample_context):
        """Test on_auth_failure returns default without store."""
        hooks = ErrorLearningHooks(global_store=None)
        is_transient, reason = hooks.on_auth_failure(sample_context)
        assert is_transient is False
        assert "No learning data" in reason

    def test_get_error_stats_no_store(self):
        """Test get_error_stats returns error dict without store."""
        hooks = ErrorLearningHooks(global_store=None)
        stats = hooks.get_error_stats("E103")
        assert "error" in stats
        assert "not enabled" in str(stats["error"])


class TestErrorLearningHooksWithMockStore:
    """Tests for ErrorLearningHooks with mocked global store."""

    @pytest.fixture
    def mock_store(self):
        """Create a mock global store."""
        store = MagicMock()
        store.record_pattern = MagicMock()
        store.record_error_recovery = MagicMock()
        store.get_learned_wait_time = MagicMock(return_value=None)
        return store

    @pytest.fixture
    def sample_error(self) -> ClassifiedError:
        """Create a sample classified error."""
        return ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limit exceeded",
            error_code=ErrorCode.CAPACITY_EXCEEDED,  # E103
            original_error=None,
            retriable=True,
            suggested_wait_seconds=60.0,
        )

    @pytest.fixture
    def sample_context(self, sample_error) -> ErrorLearningContext:
        """Create a sample context."""
        return ErrorLearningContext(
            error=sample_error,
            job_id="test-job",
            sheet_num=1,
            workspace_path=Path("/tmp"),
            model="claude-3-sonnet",
        )

    def test_enabled_with_store(self, mock_store):
        """Test that hooks are enabled with store."""
        hooks = ErrorLearningHooks(global_store=mock_store)
        assert hooks.enabled is True

    def test_on_error_classified_records_pattern(self, mock_store, sample_context):
        """Test on_error_classified records the error pattern."""
        hooks = ErrorLearningHooks(global_store=mock_store)
        hooks.on_error_classified(sample_context)

        mock_store.record_pattern.assert_called_once()
        call_args = mock_store.record_pattern.call_args
        assert call_args.kwargs["pattern_type"] == "error"
        assert "E103" in call_args.kwargs["pattern_name"]
        assert "rate_limit" in call_args.kwargs["pattern_name"]

    def test_on_error_classified_uses_learned_wait(self, mock_store, sample_context):
        """Test on_error_classified uses learned wait time when available."""
        mock_store.get_learned_wait_time.return_value = 90.0  # Learned 90s is better

        hooks = ErrorLearningHooks(global_store=mock_store)
        result = hooks.on_error_classified(sample_context)

        # Should have adjusted the wait time
        assert result.suggested_wait_seconds == 90.0

    def test_on_error_classified_bounds_learned_wait(self, mock_store, sample_context):
        """Test learned wait is bounded by config min/max."""
        mock_store.get_learned_wait_time.return_value = 5.0  # Below min
        config = ErrorLearningConfig(min_wait_time=10.0, max_wait_time=100.0)

        hooks = ErrorLearningHooks(global_store=mock_store, config=config)
        result = hooks.on_error_classified(sample_context)

        # Should be bounded to min
        assert result.suggested_wait_seconds == 10.0

    def test_on_error_recovered_records_recovery(self, mock_store, sample_context):
        """Test on_error_recovered records recovery to store."""
        sample_context.actual_wait = 45.0
        sample_context.suggested_wait = 60.0

        hooks = ErrorLearningHooks(global_store=mock_store)
        hooks.on_error_recovered(sample_context, success=True)

        mock_store.record_error_recovery.assert_called_once_with(
            error_code="E103",
            suggested_wait=60.0,
            actual_wait=45.0,
            recovery_success=True,
            model="claude-3-sonnet",
        )

    def test_on_error_recovered_no_actual_wait(self, mock_store, sample_context):
        """Test on_error_recovered handles missing actual_wait."""
        sample_context.actual_wait = None

        hooks = ErrorLearningHooks(global_store=mock_store)
        hooks.on_error_recovered(sample_context, success=True)

        # Should not record if no actual_wait
        mock_store.record_error_recovery.assert_not_called()


class TestErrorLearningHooksAuthFailure:
    """Tests for auth failure analysis."""

    @pytest.fixture
    def setup_db_with_recoveries(self):
        """Create an in-memory database with test recovery data."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE error_recoveries (
                id INTEGER PRIMARY KEY,
                error_code TEXT,
                suggested_wait REAL,
                actual_wait REAL,
                recovery_success BOOLEAN,
                model TEXT
            )
        """)
        return conn

    def test_auth_failure_insufficient_samples(self, setup_db_with_recoveries):
        """Test auth failure with insufficient samples returns not transient."""
        conn = setup_db_with_recoveries
        # Add only 2 samples (below min_samples=3)
        conn.execute(
            "INSERT INTO error_recoveries VALUES (1, 'E102', 60, 60, 1, 'claude-3')"
        )
        conn.execute(
            "INSERT INTO error_recoveries VALUES (2, 'E102', 60, 60, 1, 'claude-3')"
        )
        conn.commit()

        mock_store = MagicMock()
        mock_store._get_connection.return_value.__enter__ = MagicMock(return_value=conn)
        mock_store._get_connection.return_value.__exit__ = MagicMock(return_value=False)

        hooks = ErrorLearningHooks(global_store=mock_store)

        auth_error = ClassifiedError(
            category=ErrorCategory.AUTH,
            message="Auth failed",
            error_code=ErrorCode.RATE_LIMIT_CLI,  # E102
            original_error=None,
            retriable=False,
        )
        context = ErrorLearningContext(
            error=auth_error,
            job_id="test",
            sheet_num=1,
            workspace_path=Path("/tmp"),
            model="claude-3",
        )

        is_transient, _ = hooks.on_auth_failure(context)
        assert is_transient is False

    def test_auth_failure_high_recovery_rate(self, setup_db_with_recoveries):
        """Test auth failure with high recovery rate returns transient."""
        conn = setup_db_with_recoveries
        # Add 5 samples with 4 successes (80% recovery rate)
        for i in range(5):
            conn.execute(
                "INSERT INTO error_recoveries VALUES "
                f"({i}, 'E102', 60, 60, {1 if i < 4 else 0}, 'claude-3')"
            )
        conn.commit()

        mock_store = MagicMock()
        mock_store._get_connection.return_value.__enter__ = MagicMock(return_value=conn)
        mock_store._get_connection.return_value.__exit__ = MagicMock(return_value=False)

        hooks = ErrorLearningHooks(global_store=mock_store)

        auth_error = ClassifiedError(
            category=ErrorCategory.AUTH,
            message="Auth failed",
            error_code=ErrorCode.RATE_LIMIT_CLI,  # E102
            original_error=None,
            retriable=False,
        )
        context = ErrorLearningContext(
            error=auth_error,
            job_id="test",
            sheet_num=1,
            workspace_path=Path("/tmp"),
            model="claude-3",
        )

        is_transient, reason = hooks.on_auth_failure(context)
        assert is_transient is True
        assert "80%" in reason


class TestErrorLearningHooksStats:
    """Tests for error statistics retrieval."""

    @pytest.fixture
    def setup_db_with_stats(self):
        """Create an in-memory database with test stats data."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE error_recoveries (
                id INTEGER PRIMARY KEY,
                error_code TEXT,
                suggested_wait REAL,
                actual_wait REAL,
                recovery_success BOOLEAN,
                model TEXT
            )
        """)
        # Add test data
        data = [
            (1, "E103", 60, 45, True, "claude-3"),
            (2, "E103", 60, 50, True, "claude-3"),
            (3, "E103", 60, 30, False, "claude-3"),
            (4, "E103", 60, 60, True, "claude-3"),
        ]
        conn.executemany(
            "INSERT INTO error_recoveries VALUES (?, ?, ?, ?, ?, ?)", data
        )
        conn.commit()
        return conn

    def test_get_error_stats(self, setup_db_with_stats):
        """Test getting statistics for an error code."""
        conn = setup_db_with_stats

        mock_store = MagicMock()
        mock_store._get_connection.return_value.__enter__ = MagicMock(return_value=conn)
        mock_store._get_connection.return_value.__exit__ = MagicMock(return_value=False)

        hooks = ErrorLearningHooks(global_store=mock_store)
        stats = hooks.get_error_stats("E103")

        assert stats["error_code"] == "E103"
        assert stats["total_occurrences"] == 4
        assert stats["successful_recoveries"] == 3
        assert stats["recovery_rate"] == 75.0
        # avg of 45, 50, 30, 60 = 185/4 = 46.25
        assert stats["avg_wait_seconds"] == pytest.approx(46.25, rel=0.01)
        assert stats["min_wait_seconds"] == 30.0
        assert stats["max_wait_seconds"] == 60.0

    def test_get_error_stats_no_data(self):
        """Test getting stats for error code with no data."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE error_recoveries (
                id INTEGER PRIMARY KEY,
                error_code TEXT,
                suggested_wait REAL,
                actual_wait REAL,
                recovery_success BOOLEAN,
                model TEXT
            )
        """)

        mock_store = MagicMock()
        mock_store._get_connection.return_value.__enter__ = MagicMock(return_value=conn)
        mock_store._get_connection.return_value.__exit__ = MagicMock(return_value=False)

        hooks = ErrorLearningHooks(global_store=mock_store)
        stats = hooks.get_error_stats("E999")

        assert stats["error_code"] == "E999"
        assert stats["total_occurrences"] == 0


class TestWrapClassifierWithLearning:
    """Tests for wrap_classifier_with_learning helper."""

    def test_wrap_returns_classifier_and_hooks(self):
        """Test wrapper returns both classifier and hooks."""
        classifier = ErrorClassifier()
        wrapped_classifier, hooks = wrap_classifier_with_learning(classifier)

        assert wrapped_classifier is classifier
        assert isinstance(hooks, ErrorLearningHooks)

    def test_wrap_with_store(self):
        """Test wrapper with global store enables hooks."""
        classifier = ErrorClassifier()
        mock_store = MagicMock()

        wrapped_classifier, hooks = wrap_classifier_with_learning(
            classifier, global_store=mock_store
        )

        assert hooks.enabled is True

    def test_wrap_without_store(self):
        """Test wrapper without store disables hooks."""
        classifier = ErrorClassifier()

        wrapped_classifier, hooks = wrap_classifier_with_learning(classifier)

        assert hooks.enabled is False


class TestRecordErrorRecoveryFunction:
    """Tests for record_error_recovery convenience function."""

    @pytest.fixture
    def sample_error(self) -> ClassifiedError:
        """Create a sample classified error."""
        return ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limit exceeded",
            error_code=ErrorCode.CAPACITY_EXCEEDED,  # E103
            original_error=None,
            retriable=True,
            suggested_wait_seconds=60.0,
        )

    def test_record_with_classified_error(self, sample_error):
        """Test recording recovery with ClassifiedError."""
        mock_store = MagicMock()

        record_error_recovery(
            global_store=mock_store,
            error=sample_error,
            actual_wait=45.0,
            success=True,
            model="claude-3",
        )

        mock_store.record_error_recovery.assert_called_once_with(
            error_code="E103",
            suggested_wait=60.0,
            actual_wait=45.0,
            recovery_success=True,
            model="claude-3",
        )

    def test_record_with_classification_result(self, sample_error):
        """Test recording recovery with ClassificationResult."""
        mock_store = MagicMock()
        result = ClassificationResult(
            primary=sample_error,
            secondary=[],
        )

        record_error_recovery(
            global_store=mock_store,
            error=result,
            actual_wait=45.0,
            success=False,
        )

        mock_store.record_error_recovery.assert_called_once()
        call_args = mock_store.record_error_recovery.call_args
        assert call_args.kwargs["error_code"] == "E103"
        assert call_args.kwargs["recovery_success"] is False

    def test_record_without_store_is_noop(self, sample_error):
        """Test recording without store is no-op."""
        result = record_error_recovery(
            global_store=None,
            error=sample_error,
            actual_wait=45.0,
            success=True,
        )
        assert result is None

    def test_record_handles_missing_suggested_wait(self):
        """Test recording handles error without suggested_wait."""
        mock_store = MagicMock()
        error = ClassifiedError(
            category=ErrorCategory.NETWORK,
            message="Connection failed",
            error_code=ErrorCode.NETWORK_CONNECTION_FAILED,  # E901
            original_error=None,
            retriable=True,
            suggested_wait_seconds=None,
        )

        record_error_recovery(
            global_store=mock_store,
            error=error,
            actual_wait=10.0,
            success=True,
        )

        call_args = mock_store.record_error_recovery.call_args
        assert call_args.kwargs["suggested_wait"] == 0  # Default to 0 when None
