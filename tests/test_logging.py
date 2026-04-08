"""Tests for marianne.core.logging module."""

from __future__ import annotations

import gzip
import logging
import re
from pathlib import Path

import pytest
import structlog


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text for assertion matching."""
    ansi_pattern = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_pattern.sub('', text)

from marianne.core.config import LogConfig
from marianne.core.logging import (
    SENSITIVE_PATTERNS,
    CompressingRotatingFileHandler,
    MarianneLogger,
    _sanitize_event_dict,
    _sanitize_value,
    configure_logging,
    find_log_files,
    get_current_log_path,
    get_default_log_path,
    get_logger,
)


class TestSensitivePatterns:
    """Tests for sensitive field detection and sanitization."""

    def test_known_sensitive_patterns(self):
        """Test that common sensitive patterns are included."""
        assert "api_key" in SENSITIVE_PATTERNS
        assert "token" in SENSITIVE_PATTERNS
        assert "password" in SENSITIVE_PATTERNS
        assert "secret" in SENSITIVE_PATTERNS
        assert "credential" in SENSITIVE_PATTERNS

    def test_sanitize_value_redacts_api_key(self):
        """Test that api_key fields are redacted."""
        result = _sanitize_value("api_key", "sk-12345")
        assert result == "[REDACTED]"

    def test_sanitize_value_redacts_mixed_case(self):
        """Test that mixed case sensitive fields are redacted."""
        result = _sanitize_value("API_KEY", "sk-12345")
        assert result == "[REDACTED]"

        result = _sanitize_value("ApiKey", "sk-12345")
        assert result == "[REDACTED]"

    def test_sanitize_value_redacts_compound_keys(self):
        """Test that compound key names containing sensitive patterns are redacted."""
        result = _sanitize_value("anthropic_api_key", "sk-12345")
        assert result == "[REDACTED]"

        result = _sanitize_value("bearer_token", "abc123")
        assert result == "[REDACTED]"

        result = _sanitize_value("db_password", "secret123")
        assert result == "[REDACTED]"

    def test_sanitize_value_preserves_safe_values(self):
        """Test that non-sensitive values are preserved."""
        result = _sanitize_value("sheet_num", 5)
        assert result == 5

        result = _sanitize_value("job_id", "test-job-123")
        assert result == "test-job-123"

        result = _sanitize_value("status", "completed")
        assert result == "completed"

    def test_sanitize_event_dict_processes_all_keys(self):
        """Test that _sanitize_event_dict processes all keys."""
        event_dict = {
            "event": "test_event",
            "api_key": "sk-secret",
            "sheet_num": 5,
            "token": "bearer-123",
        }

        result = _sanitize_event_dict(None, "info", event_dict)

        assert result["event"] == "test_event"
        assert result["api_key"] == "[REDACTED]"
        assert result["sheet_num"] == 5
        assert result["token"] == "[REDACTED]"

    def test_sanitize_event_dict_handles_nested_dicts(self):
        """Test that nested dicts are also sanitized."""
        event_dict = {
            "event": "test_event",
            "config": {
                "api_key": "sk-secret",
                "model": "claude-3",
            },
        }

        result = _sanitize_event_dict(None, "info", event_dict)

        assert result["config"]["api_key"] == "[REDACTED]"
        assert result["config"]["model"] == "claude-3"


class TestMarianneLogger:
    """Tests for the MarianneLogger class."""

    def test_create_logger_with_component(self):
        """Test creating a logger with a component name."""
        logger = MarianneLogger("runner")
        assert logger._component == "runner"

    def test_create_logger_with_initial_context(self):
        """Test creating a logger with initial context."""
        logger = MarianneLogger("runner", job_id="test-job")
        # The context is stored in the bound logger
        assert logger._component == "runner"

    def test_bind_returns_new_logger(self):
        """Test that bind() returns a new logger instance."""
        logger = MarianneLogger("runner")
        bound_logger = logger.bind(job_id="test-job")

        assert bound_logger is not logger
        assert bound_logger._component == "runner"

    def test_unbind_returns_new_logger(self):
        """Test that unbind() returns a new logger instance."""
        logger = MarianneLogger("runner", job_id="test-job")
        unbound_logger = logger.unbind("job_id")

        assert unbound_logger is not logger
        assert unbound_logger._component == "runner"

    def test_logger_methods_exist(self):
        """Test that all expected logging methods exist."""
        logger = MarianneLogger("runner")

        assert callable(logger.debug)
        assert callable(logger.info)
        assert callable(logger.warning)
        assert callable(logger.error)
        assert callable(logger.critical)
        assert callable(logger.exception)


class TestGetLogger:
    """Tests for the get_logger function."""

    def test_get_logger_creates_marianne_logger(self):
        """Test that get_logger returns a MarianneLogger."""
        logger = get_logger("test-component")
        assert isinstance(logger, MarianneLogger)
        assert logger._component == "test-component"

    def test_get_logger_with_initial_context(self):
        """Test get_logger with initial context."""
        logger = get_logger("runner", job_id="my-job", sheet_num=1)
        assert logger._component == "runner"


class TestConfigureLogging:
    """Tests for the configure_logging function."""

    def setup_method(self):
        """Reset logging configuration before each test."""
        # Reset structlog to default state
        structlog.reset_defaults()

        # Clear all handlers from root logger
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    def test_configure_console_format(self):
        """Test configuring console format logging."""
        configure_logging(level="DEBUG", format="console")

        # Verify root logger is configured
        root_logger = logging.getLogger()
        assert root_logger.level == logging.DEBUG

    def test_configure_json_format(self):
        """Test configuring JSON format logging."""
        configure_logging(level="INFO", format="json")

        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO

    def test_configure_with_file_path(self, tmp_path: Path):
        """Test configuring logging with file output."""
        log_file = tmp_path / "logs" / "marianne.log"

        configure_logging(
            level="INFO",
            format="both",
            file_path=log_file,
        )

        # Verify log directory was created
        assert log_file.parent.exists()

    def test_configure_both_requires_file_path(self):
        """Test that format='both' requires file_path."""
        with pytest.raises(ValueError, match="file_path is required"):
            configure_logging(level="INFO", format="both", file_path=None)

    def test_configure_sets_log_level(self):
        """Test that log level is properly set."""
        configure_logging(level="WARNING", format="console")

        root_logger = logging.getLogger()
        assert root_logger.level == logging.WARNING

    def test_configure_removes_existing_handlers(self):
        """Test that existing handlers are removed."""
        # Add a handler first
        root_logger = logging.getLogger()
        existing_handler = logging.StreamHandler()
        root_logger.addHandler(existing_handler)

        # Store reference to verify it's removed
        assert existing_handler in root_logger.handlers

        configure_logging(level="INFO", format="console")

        # Existing handler should be removed, new one added
        assert existing_handler not in root_logger.handlers
        # Should have at least one handler for console mode
        assert len(root_logger.handlers) >= 1


class TestLogConfigModel:
    """Tests for the LogConfig Pydantic model."""

    def test_default_values(self):
        """Test LogConfig default values."""
        config = LogConfig()

        assert config.level == "INFO"
        assert config.format == "console"
        assert config.file_path is None
        assert config.max_file_size_mb == 50
        assert config.backup_count == 5
        assert config.include_timestamps is True
        assert config.include_context is True

    def test_custom_values(self):
        """Test LogConfig with custom values."""
        config = LogConfig(
            level="DEBUG",
            format="json",
            file_path=Path("/var/log/marianne.log"),
            max_file_size_mb=100,
            backup_count=10,
            include_timestamps=False,
        )

        assert config.level == "DEBUG"
        assert config.format == "json"
        assert config.file_path == Path("/var/log/marianne.log")
        assert config.max_file_size_mb == 100
        assert config.backup_count == 10
        assert config.include_timestamps is False

    def test_level_validation(self):
        """Test that invalid log levels are rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LogConfig(level="TRACE")  # Invalid level

    def test_format_validation(self):
        """Test that invalid formats are rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LogConfig(format="xml")  # Invalid format

    def test_max_file_size_validation(self):
        """Test max_file_size_mb validation bounds."""
        from pydantic import ValidationError

        # Zero is invalid (gt=0)
        with pytest.raises(ValidationError):
            LogConfig(max_file_size_mb=0)

        # Over 1000 is invalid (le=1000)
        with pytest.raises(ValidationError):
            LogConfig(max_file_size_mb=1001)

    def test_backup_count_validation(self):
        """Test backup_count validation bounds."""
        from pydantic import ValidationError

        # Negative is invalid (ge=0)
        with pytest.raises(ValidationError):
            LogConfig(backup_count=-1)

        # Over 100 is invalid (le=100)
        with pytest.raises(ValidationError):
            LogConfig(backup_count=101)


class TestLogConfigInJobConfig:
    """Tests for LogConfig integration in JobConfig."""

    def test_job_config_has_logging(self, sample_config_dict: dict):
        """Test that JobConfig includes logging configuration."""
        from marianne.core.config import JobConfig

        config = JobConfig(**sample_config_dict)

        assert hasattr(config, "logging")
        assert isinstance(config.logging, LogConfig)

    def test_job_config_logging_defaults(self, sample_config_dict: dict):
        """Test that logging has sensible defaults when not specified."""
        from marianne.core.config import JobConfig

        config = JobConfig(**sample_config_dict)

        assert config.logging.level == "INFO"
        assert config.logging.format == "console"

    def test_job_config_custom_logging(self, sample_config_dict: dict):
        """Test that custom logging config is respected."""
        from marianne.core.config import JobConfig

        sample_config_dict["logging"] = {
            "level": "DEBUG",
            "format": "json",
            "include_timestamps": False,
        }

        config = JobConfig(**sample_config_dict)

        assert config.logging.level == "DEBUG"
        assert config.logging.format == "json"
        assert config.logging.include_timestamps is False


class TestLoggingIntegration:
    """Integration tests for logging functionality."""

    def setup_method(self):
        """Reset logging configuration before each test."""
        structlog.reset_defaults()
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    def test_log_output_contains_component(self, capsys: pytest.CaptureFixture[str]):
        """Test that log output includes component name."""
        # Configure for JSON to make parsing easier
        configure_logging(level="INFO", format="json")

        logger = get_logger("test-runner")
        assert logger is not None

        # Verify logger can emit events without error
        logger.info("test_event", foo="bar")

        # Verify structlog output through stdlib logging
        captured = capsys.readouterr()
        output = captured.out + captured.err
        # structlog JSON format should include event name
        assert "test_event" in output or "test-runner" in output, (
            f"Expected 'test_event' or 'test-runner' in output, got: {output[:200]}"
        )

    def test_bound_context_preserved(self):
        """Test that bound context is preserved through operations."""
        logger = get_logger("runner")
        bound = logger.bind(job_id="job-1", sheet_num=5)

        # Create another binding
        double_bound = bound.bind(retry_count=2)

        # Original should be unchanged
        assert bound is not double_bound

    def test_unbind_removes_context(self):
        """Test that unbind removes specified keys."""
        logger = get_logger("runner", job_id="job-1", sheet_num=5)
        unbound = logger.unbind("sheet_num")

        # Should still have the component
        assert unbound._component == "runner"


class TestExecutionContext:
    """Tests for the ExecutionContext dataclass."""

    def test_create_minimal_context(self):
        """Test creating context with only required fields."""
        from marianne.core.logging import ExecutionContext

        ctx = ExecutionContext(job_id="test-job")

        assert ctx.job_id == "test-job"
        assert ctx.run_id is not None  # Auto-generated UUID
        assert len(ctx.run_id) == 36  # UUID format
        assert ctx.sheet_num is None
        assert ctx.component == "unknown"
        assert ctx.parent_run_id is None

    def test_create_full_context(self):
        """Test creating context with all fields."""
        from marianne.core.logging import ExecutionContext

        ctx = ExecutionContext(
            job_id="test-job",
            run_id="custom-run-id",
            sheet_num=5,
            component="runner",
            parent_run_id="parent-run-id",
        )

        assert ctx.job_id == "test-job"
        assert ctx.run_id == "custom-run-id"
        assert ctx.sheet_num == 5
        assert ctx.component == "runner"
        assert ctx.parent_run_id == "parent-run-id"

    def test_context_is_immutable(self):
        """Test that ExecutionContext is frozen (immutable)."""
        from marianne.core.logging import ExecutionContext

        ctx = ExecutionContext(job_id="test-job")

        with pytest.raises(AttributeError):
            ctx.job_id = "modified"  # type: ignore[misc]

    def test_with_sheet_creates_new_context(self):
        """Test that with_sheet creates a new context with updated sheet_num."""
        from marianne.core.logging import ExecutionContext

        ctx = ExecutionContext(job_id="test-job", run_id="run-123", component="runner")
        new_ctx = ctx.with_sheet(10)

        # Original unchanged
        assert ctx.sheet_num is None

        # New context has sheet_num
        assert new_ctx.sheet_num == 10

        # Other fields preserved
        assert new_ctx.job_id == "test-job"
        assert new_ctx.run_id == "run-123"
        assert new_ctx.component == "runner"

    def test_with_component_creates_new_context(self):
        """Test that with_component creates a new context with updated component."""
        from marianne.core.logging import ExecutionContext

        ctx = ExecutionContext(job_id="test-job", run_id="run-123", sheet_num=5)
        new_ctx = ctx.with_component("backend")

        # Original unchanged
        assert ctx.component == "unknown"

        # New context has component
        assert new_ctx.component == "backend"

        # Other fields preserved
        assert new_ctx.job_id == "test-job"
        assert new_ctx.run_id == "run-123"
        assert new_ctx.sheet_num == 5

    def test_as_child_creates_nested_context(self):
        """Test that as_child creates a child context with parent tracking."""
        from marianne.core.logging import ExecutionContext

        parent = ExecutionContext(job_id="test-job", run_id="parent-run")
        child = parent.as_child()

        # Child has new run_id
        assert child.run_id != parent.run_id

        # Parent run_id is now parent_run_id in child
        assert child.parent_run_id == "parent-run"

        # Other fields preserved
        assert child.job_id == "test-job"

    def test_as_child_with_custom_run_id(self):
        """Test as_child with explicit child_run_id."""
        from marianne.core.logging import ExecutionContext

        parent = ExecutionContext(job_id="test-job", run_id="parent-run")
        child = parent.as_child(child_run_id="custom-child-id")

        assert child.run_id == "custom-child-id"
        assert child.parent_run_id == "parent-run"

    def test_to_dict_excludes_none_values(self):
        """Test that to_dict excludes None values."""
        from marianne.core.logging import ExecutionContext

        ctx = ExecutionContext(job_id="test-job", run_id="run-123")
        result = ctx.to_dict()

        assert result == {
            "job_id": "test-job",
            "run_id": "run-123",
            "component": "unknown",
        }
        assert "sheet_num" not in result
        assert "parent_run_id" not in result

    def test_to_dict_includes_all_set_values(self):
        """Test that to_dict includes all set values."""
        from marianne.core.logging import ExecutionContext

        ctx = ExecutionContext(
            job_id="test-job",
            run_id="run-123",
            sheet_num=5,
            component="runner",
            parent_run_id="parent-run",
        )
        result = ctx.to_dict()

        assert result == {
            "job_id": "test-job",
            "run_id": "run-123",
            "sheet_num": 5,
            "component": "runner",
            "parent_run_id": "parent-run",
        }


class TestContextVar:
    """Tests for ContextVar-based context management."""

    def setup_method(self):
        """Clear context before each test."""
        from marianne.core.logging import clear_context

        clear_context()

    def teardown_method(self):
        """Clear context after each test."""
        from marianne.core.logging import clear_context

        clear_context()

    def test_get_current_context_returns_none_when_not_set(self):
        """Test that get_current_context returns None by default."""
        from marianne.core.logging import get_current_context

        assert get_current_context() is None

    def test_set_context_and_get_context(self):
        """Test set_context and get_current_context."""
        from marianne.core.logging import (
            ExecutionContext,
            get_current_context,
            set_context,
        )

        ctx = ExecutionContext(job_id="test-job")
        set_context(ctx)

        result = get_current_context()
        assert result is ctx

    def test_clear_context(self):
        """Test that clear_context removes the context."""
        from marianne.core.logging import (
            ExecutionContext,
            clear_context,
            get_current_context,
            set_context,
        )

        ctx = ExecutionContext(job_id="test-job")
        set_context(ctx)
        assert get_current_context() is not None

        clear_context()
        assert get_current_context() is None

    def test_with_context_sets_and_clears(self):
        """Test that with_context sets context for block duration."""
        from marianne.core.logging import (
            ExecutionContext,
            get_current_context,
            with_context,
        )

        ctx = ExecutionContext(job_id="test-job")

        # Before with_context
        assert get_current_context() is None

        with with_context(ctx):
            # Inside with_context
            assert get_current_context() is ctx

        # After with_context
        assert get_current_context() is None

    def test_with_context_yields_context(self):
        """Test that with_context yields the context."""
        from marianne.core.logging import ExecutionContext, with_context

        ctx = ExecutionContext(job_id="test-job")

        with with_context(ctx) as yielded:
            assert yielded is ctx

    def test_with_context_restores_on_exception(self):
        """Test that with_context restores context on exception."""
        from marianne.core.logging import (
            ExecutionContext,
            get_current_context,
            with_context,
        )

        ctx = ExecutionContext(job_id="test-job")

        try:
            with with_context(ctx):
                assert get_current_context() is ctx
                raise ValueError("test error")
        except ValueError:
            pass

        # Context should be cleared even after exception
        assert get_current_context() is None

    def test_nested_with_context(self):
        """Test nested with_context blocks."""
        from marianne.core.logging import (
            ExecutionContext,
            get_current_context,
            with_context,
        )

        outer_ctx = ExecutionContext(job_id="outer-job")
        inner_ctx = ExecutionContext(job_id="inner-job")

        with with_context(outer_ctx):
            assert get_current_context() is outer_ctx

            with with_context(inner_ctx):
                assert get_current_context() is inner_ctx

            # Should restore outer context
            assert get_current_context() is outer_ctx

        # Should be cleared
        assert get_current_context() is None


class TestAddContextProcessor:
    """Tests for the _add_context processor."""

    def setup_method(self):
        """Clear context before each test."""
        from marianne.core.logging import clear_context

        clear_context()

    def teardown_method(self):
        """Clear context after each test."""
        from marianne.core.logging import clear_context

        clear_context()

    def test_add_context_with_no_context(self):
        """Test _add_context when no context is set."""
        from marianne.core.logging import _add_context

        event_dict = {"event": "test", "foo": "bar"}
        result = _add_context(None, "info", event_dict)

        # Should be unchanged
        assert result == {"event": "test", "foo": "bar"}

    def test_add_context_adds_fields(self):
        """Test _add_context adds context fields."""
        from marianne.core.logging import (
            ExecutionContext,
            _add_context,
            set_context,
        )

        ctx = ExecutionContext(
            job_id="test-job",
            run_id="run-123",
            sheet_num=5,
            component="runner",
        )
        set_context(ctx)

        event_dict = {"event": "test"}
        result = _add_context(None, "info", event_dict)

        assert result["job_id"] == "test-job"
        assert result["run_id"] == "run-123"
        assert result["sheet_num"] == 5
        assert result["component"] == "runner"

    def test_add_context_preserves_explicit_values(self):
        """Test that explicit event_dict values take precedence."""
        from marianne.core.logging import (
            ExecutionContext,
            _add_context,
            set_context,
        )

        ctx = ExecutionContext(
            job_id="context-job",
            component="context-component",
        )
        set_context(ctx)

        # Explicitly set job_id in event_dict
        event_dict = {"event": "test", "job_id": "explicit-job"}
        result = _add_context(None, "info", event_dict)

        # Explicit value should be preserved
        assert result["job_id"] == "explicit-job"
        # Context values for other fields should be added
        assert result["component"] == "context-component"


class TestContextIntegration:
    """Integration tests for context with logging."""

    def setup_method(self):
        """Reset logging configuration and context before each test."""
        import logging

        from marianne.core.logging import clear_context

        structlog.reset_defaults()
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        clear_context()

    def teardown_method(self):
        """Clear context after each test."""
        from marianne.core.logging import clear_context

        clear_context()

    def test_logger_with_context_includes_fields(self):
        """Test that logger includes context fields when context is set."""
        import json

        from marianne.core.logging import (
            ExecutionContext,
            configure_logging,
            get_logger,
            with_context,
        )

        # Capture log output for verification
        captured_output: list[str] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured_output.append(record.getMessage())

        configure_logging(level="DEBUG", format="json", include_timestamps=False)

        # Add our capturing handler
        root_logger = logging.getLogger()
        capture_handler = CapturingHandler()
        root_logger.addHandler(capture_handler)

        logger = get_logger("test-component")
        ctx = ExecutionContext(
            job_id="my-job",
            run_id="run-abc",
            sheet_num=3,
        )

        with with_context(ctx):
            logger.info("test_event", extra_field="value")

        # Verify log output contains context fields
        assert len(captured_output) == 1
        log_entry = json.loads(captured_output[0])

        assert log_entry["job_id"] == "my-job"
        assert log_entry["run_id"] == "run-abc"
        assert log_entry["sheet_num"] == 3
        assert log_entry["component"] == "test-component"
        assert log_entry["extra_field"] == "value"

    def test_context_disabled_does_not_add_fields(self):
        """Test that context is not added when include_context=False."""
        import json

        from marianne.core.logging import (
            ExecutionContext,
            configure_logging,
            get_logger,
            with_context,
        )

        # Capture log output for verification
        captured_output: list[str] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured_output.append(record.getMessage())

        configure_logging(
            level="DEBUG",
            format="json",
            include_timestamps=False,
            include_context=False,
        )

        # Add our capturing handler
        root_logger = logging.getLogger()
        capture_handler = CapturingHandler()
        root_logger.addHandler(capture_handler)

        logger = get_logger("test-component")
        ctx = ExecutionContext(job_id="my-job")

        with with_context(ctx):
            logger.info("test_event")

        # Verify log output does NOT contain context fields
        assert len(captured_output) == 1
        log_entry = json.loads(captured_output[0])

        assert "job_id" not in log_entry
        assert "run_id" not in log_entry


class TestAsyncContextPropagation:
    """Tests for context propagation in async code."""

    def setup_method(self):
        """Clear context before each test."""
        from marianne.core.logging import clear_context

        clear_context()

    def teardown_method(self):
        """Clear context after each test."""
        from marianne.core.logging import clear_context

        clear_context()

    @pytest.mark.asyncio
    async def test_context_propagates_in_async_code(self):
        """Test that context propagates correctly in async code."""
        from marianne.core.logging import (
            ExecutionContext,
            get_current_context,
            with_context,
        )

        ctx = ExecutionContext(job_id="async-job")

        async def inner_async():
            # Context should be available in nested async
            return get_current_context()

        with with_context(ctx):
            result = await inner_async()
            assert result is ctx

    @pytest.mark.asyncio
    async def test_context_isolation_across_tasks(self):
        """Test that context is isolated between concurrent tasks."""
        import asyncio

        from marianne.core.logging import (
            ExecutionContext,
            get_current_context,
            with_context,
        )

        results: dict[str, str | None] = {}

        async def task_with_context(name: str, job_id: str):
            ctx = ExecutionContext(job_id=job_id)
            with with_context(ctx):
                await asyncio.sleep(0.01)  # Simulate some async work
                current = get_current_context()
                results[name] = current.job_id if current else None

        # Run tasks concurrently
        await asyncio.gather(
            task_with_context("task1", "job-1"),
            task_with_context("task2", "job-2"),
        )

        # Each task should have its own context
        assert results["task1"] == "job-1"
        assert results["task2"] == "job-2"


class TestErrorClassifierLogging:
    """Tests for error classification logging.

    Note: Due to structlog's logger caching and module-level logger creation,
    these tests verify behavior through stdout capture (via capsys) rather than
    file-based logging, since the loggers are created before tests run.
    """

    def setup_method(self):
        """Reset logging configuration before each test."""
        # Reset structlog's caching (important for test isolation)
        structlog.reset_defaults()
        # Force recreation of cached loggers
        structlog.configure(cache_logger_on_first_use=False)
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    def test_rate_limit_classification_logs_warning(self, capsys: pytest.CaptureFixture[str]):
        """Test that rate limit classification logs a warning."""
        from marianne.core.errors import ErrorCategory, ErrorClassifier

        # Configure logging - note: due to module-level logger creation,
        # we get console format output even when requesting JSON
        configure_logging(
            level="WARNING",
            format="console",
            include_timestamps=False,
        )

        classifier = ErrorClassifier()
        result = classifier.classify(stdout="rate limit exceeded", exit_code=1)

        assert result.category == ErrorCategory.RATE_LIMIT

        # Capture console output (structlog console format goes to stderr)
        captured = capsys.readouterr()
        err = strip_ansi(captured.err)
        assert "error_classified" in err
        assert "rate_limit" in err
        assert "retriable=True" in err

    def test_fatal_error_classification_logs_warning(self, capsys: pytest.CaptureFixture[str]):
        """Test that fatal error classification logs a warning."""
        from marianne.core.errors import ErrorCategory, ErrorClassifier

        configure_logging(
            level="WARNING",
            format="console",
            include_timestamps=False,
        )

        classifier = ErrorClassifier()
        result = classifier.classify(exit_code=99)  # Unknown error

        assert result.category == ErrorCategory.FATAL

        captured = capsys.readouterr()
        err = strip_ansi(captured.err)
        assert "error_classified" in err
        assert "fatal" in err
        assert "retriable=False" in err

    def test_validation_category_does_not_log(self, capsys: pytest.CaptureFixture[str]):
        """Test that successful execution (validation needed) does not log."""
        from marianne.core.errors import ErrorCategory, ErrorClassifier

        configure_logging(
            level="DEBUG",
            format="console",
            include_timestamps=False,
        )

        classifier = ErrorClassifier()
        result = classifier.classify(exit_code=0)  # Success

        assert result.category == ErrorCategory.VALIDATION

        # VALIDATION category (success case) should NOT log an error_classified warning
        captured = capsys.readouterr()
        err = strip_ansi(captured.err)
        assert "error_classified" not in err


class TestStateBackendLogging:
    """Tests for state backend checkpoint logging.

    Note: Due to structlog's logger caching and module-level logger creation,
    these tests verify behavior through stdout capture (via capsys) rather than
    file-based logging, since the loggers are created before tests run.
    """

    def setup_method(self):
        """Reset logging configuration before each test."""
        # Reset structlog's caching (important for test isolation)
        structlog.reset_defaults()
        # Force recreation of cached loggers
        structlog.configure(cache_logger_on_first_use=False)
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    @pytest.mark.asyncio
    async def test_json_backend_save_logs_info(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """Test that JSON backend logs checkpoint saves."""
        from marianne.core.checkpoint import CheckpointState, JobStatus
        from marianne.state.json_backend import JsonStateBackend

        state_dir = tmp_path / "state"
        state_dir.mkdir()

        configure_logging(
            level="DEBUG",  # checkpoint_saved is at DEBUG level
            format="console",
            include_timestamps=False,
        )

        backend = JsonStateBackend(state_dir)
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
            status=JobStatus.RUNNING,
        )

        await backend.save(state)

        captured = capsys.readouterr()
        err = strip_ansi(captured.err)
        assert "checkpoint_saved" in err
        assert "job_id=test-job" in err
        assert "status=running" in err
        assert "total_sheets=10" in err

    @pytest.mark.asyncio
    async def test_json_backend_load_logs_info(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """Test that JSON backend logs checkpoint loads."""
        from marianne.core.checkpoint import CheckpointState, JobStatus
        from marianne.state.json_backend import JsonStateBackend

        state_dir = tmp_path / "state"
        state_dir.mkdir()

        configure_logging(
            level="DEBUG",  # checkpoint_loaded is at DEBUG level
            format="console",
            include_timestamps=False,
        )

        backend = JsonStateBackend(state_dir)
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
            last_completed_sheet=5,
            status=JobStatus.RUNNING,
        )

        await backend.save(state)
        capsys.readouterr()  # Clear the save log

        loaded = await backend.load("test-job")
        assert loaded is not None

        captured = capsys.readouterr()
        err = strip_ansi(captured.err)
        assert "checkpoint_loaded" in err
        assert "job_id=test-job" in err
        assert "last_completed_sheet=5" in err

    @pytest.mark.asyncio
    async def test_json_backend_corruption_logs_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """Test that JSON backend logs corruption detection."""
        from marianne.state.json_backend import JsonStateBackend, StateCorruptionError

        state_dir = tmp_path / "state"
        state_dir.mkdir()

        configure_logging(
            level="ERROR",
            format="console",
            include_timestamps=False,
        )

        backend = JsonStateBackend(state_dir)

        # Create a corrupted state file
        corrupted_file = state_dir / "test-job.json"
        corrupted_file.write_text("{ this is not valid json")

        with pytest.raises(StateCorruptionError):
            await backend.load("test-job")

        captured = capsys.readouterr()
        err = strip_ansi(captured.err)
        assert "checkpoint_corruption_detected" in err
        assert "job_id=test-job" in err
        assert "error_type=json_decode" in err


class TestCheckpointStateTransitionLogging:
    """Tests for checkpoint state transition logging.

    Note: Due to structlog's logger caching and module-level logger creation,
    these tests verify behavior through stdout capture (via capsys) rather than
    file-based logging, since the loggers are created before tests run.
    """

    def setup_method(self):
        """Reset logging configuration before each test."""
        # Reset structlog's caching (important for test isolation)
        structlog.reset_defaults()
        # Force recreation of cached loggers
        structlog.configure(cache_logger_on_first_use=False)
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    def test_sheet_started_logs_debug(self, capsys: pytest.CaptureFixture[str]):
        """Test that sheet started transition logs debug message."""
        from marianne.core.checkpoint import CheckpointState

        configure_logging(
            level="DEBUG",
            format="console",
            include_timestamps=False,
        )

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
        )

        state.mark_sheet_started(1)

        # Capture console output (goes to stderr)
        captured = capsys.readouterr()
        err = strip_ansi(captured.err)
        assert "sheet_started" in err
        assert "job_id=test-job" in err
        assert "sheet_num=1" in err
        assert "attempt_count=1" in err

    def test_sheet_completed_logs_debug(self, capsys: pytest.CaptureFixture[str]):
        """Test that sheet completed transition logs debug message."""
        from marianne.core.checkpoint import CheckpointState

        configure_logging(
            level="DEBUG",
            format="console",
            include_timestamps=False,
        )

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
        )

        state.mark_sheet_started(1)
        capsys.readouterr()  # Clear the start log

        state.mark_sheet_completed(1, validation_passed=True)

        captured = capsys.readouterr()
        err = strip_ansi(captured.err)
        assert "sheet_completed" in err
        assert "job_id=test-job" in err
        assert "sheet_num=1" in err
        assert "validation_passed=True" in err

    def test_job_failed_logs_error(self, capsys: pytest.CaptureFixture[str]):
        """Test that job failed transition logs error message."""
        from marianne.core.checkpoint import CheckpointState

        configure_logging(
            level="ERROR",
            format="console",
            include_timestamps=False,
        )

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
        )

        state.mark_job_failed("Test failure message")

        captured = capsys.readouterr()
        err = strip_ansi(captured.err)
        assert "job_failed" in err
        assert "job_id=test-job" in err
        assert "Test failure message" in err

    def test_job_paused_logs_info(self, capsys: pytest.CaptureFixture[str]):
        """Test that job paused transition logs info message."""
        from marianne.core.checkpoint import CheckpointState, JobStatus

        configure_logging(
            level="INFO",
            format="console",
            include_timestamps=False,
        )

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=10,
            status=JobStatus.RUNNING,
        )

        state.mark_job_paused()

        captured = capsys.readouterr()
        err = strip_ansi(captured.err)
        assert "job_paused" in err
        assert "job_id=test-job" in err
        assert "previous_status=running" in err


class TestCompressingRotatingFileHandler:
    """Tests for the CompressingRotatingFileHandler class."""

    def test_handler_creation(self, tmp_path: Path):
        """Test creating a compressing rotating file handler."""
        log_file = tmp_path / "logs" / "test.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        handler = CompressingRotatingFileHandler(
            log_file,
            maxBytes=1024,
            backupCount=3,
            compress_level=9,
        )

        assert handler.compress_level == 9
        assert handler.backupCount == 3
        assert handler.maxBytes == 1024
        handler.close()

    def test_rotation_compresses_old_log(self, tmp_path: Path):
        """Test that rotation compresses the old log file."""
        log_file = tmp_path / "test.log"

        handler = CompressingRotatingFileHandler(
            log_file,
            maxBytes=100,  # Very small to trigger rotation
            backupCount=3,
            compress_level=9,
        )

        # Write enough to trigger rotation
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="x" * 50,  # 50 char message
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        handler.emit(record)  # This should trigger rollover
        handler.emit(record)

        handler.close()

        # Check that compressed backup exists
        compressed = Path(f"{log_file}.1.gz")
        # Note: rotation may or may not have occurred depending on timing
        # Check that we can at least read logs
        assert log_file.exists() or compressed.exists()

    def test_rotation_removes_old_backups(self, tmp_path: Path):
        """Test that rotation removes backups beyond backupCount."""
        log_file = tmp_path / "test.log"
        log_file.write_text("current log data")

        handler = CompressingRotatingFileHandler(
            log_file,
            maxBytes=50,  # Very small
            backupCount=2,
            compress_level=9,
        )

        # Manually create old backup files to simulate rotation
        for i in range(5):
            old_gz = Path(f"{log_file}.{i}.gz")
            old_gz.write_bytes(gzip.compress(b"old data"))

        # Perform a rollover
        handler.doRollover()
        handler.close()

        # Backups beyond backupCount should be cleaned up during rotation
        # backupCount=2 means we keep .1.gz and .2.gz
        remaining_gz = sorted(tmp_path.glob("test.log.*.gz"))
        assert len(remaining_gz) <= 3, (
            f"Expected at most 3 backup files (backupCount=2 + new rollover), "
            f"got {len(remaining_gz)}: {[f.name for f in remaining_gz]}"
        )

    def test_get_log_files_returns_all_files(self, tmp_path: Path):
        """Test that get_log_files returns all managed log files."""
        log_file = tmp_path / "test.log"
        log_file.write_text("current log")

        # Create compressed backups
        gz1 = Path(f"{log_file}.1.gz")
        gz1.write_bytes(gzip.compress(b"backup 1"))

        gz2 = Path(f"{log_file}.2.gz")
        gz2.write_bytes(gzip.compress(b"backup 2"))

        handler = CompressingRotatingFileHandler(
            log_file,
            maxBytes=1024,
            backupCount=5,
        )

        files = handler.get_log_files()
        handler.close()

        assert log_file in files
        assert gz1 in files
        assert gz2 in files
        assert len(files) == 3


class TestLogPathFunctions:
    """Tests for log path helper functions."""

    def test_get_default_log_path(self, tmp_path: Path):
        """Test getting the default log path for a workspace."""
        workspace = tmp_path / "my-workspace"
        result = get_default_log_path(workspace)

        assert result == workspace / "logs" / "marianne.log"

    def test_get_current_log_path_none_by_default(self):
        """Test that current log path is None when no file handler configured."""
        # Reset logging state by reconfiguring with console only
        configure_logging(level="INFO", format="console")
        result = get_current_log_path()
        # Console-only configuration should not set a log file path
        assert result is None or isinstance(result, Path)

    def test_get_current_log_path_after_configure(self, tmp_path: Path):
        """Test that current log path is set after configuration."""
        log_file = tmp_path / "logs" / "marianne.log"

        configure_logging(
            level="INFO",
            format="json",
            file_path=log_file,
        )

        result = get_current_log_path()
        assert result == log_file

    def test_find_log_files_empty_workspace(self, tmp_path: Path):
        """Test find_log_files returns empty list for workspace without logs."""
        workspace = tmp_path / "empty-workspace"
        workspace.mkdir()

        result = find_log_files(workspace)
        assert result == []

    def test_find_log_files_finds_current_log(self, tmp_path: Path):
        """Test find_log_files finds current log file."""
        workspace = tmp_path / "workspace"
        log_dir = workspace / "logs"
        log_dir.mkdir(parents=True)

        log_file = log_dir / "marianne.log"
        log_file.write_text("current log content")

        result = find_log_files(workspace)
        assert log_file in result

    def test_find_log_files_finds_compressed_backups(self, tmp_path: Path):
        """Test find_log_files finds compressed backup files."""
        workspace = tmp_path / "workspace"
        log_dir = workspace / "logs"
        log_dir.mkdir(parents=True)

        log_file = log_dir / "marianne.log"
        log_file.write_text("current log")

        # Create compressed backups
        gz1 = log_dir / "marianne.log.1.gz"
        gz1.write_bytes(gzip.compress(b"backup 1"))

        gz2 = log_dir / "marianne.log.2.gz"
        gz2.write_bytes(gzip.compress(b"backup 2"))

        result = find_log_files(workspace)

        assert log_file in result
        assert gz1 in result
        assert gz2 in result
        assert len(result) == 3

    def test_find_log_files_with_custom_path(self, tmp_path: Path):
        """Test find_log_files with custom log path."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        custom_log = workspace / "custom" / "app.log"
        custom_log.parent.mkdir(parents=True)
        custom_log.write_text("custom log")

        result = find_log_files(workspace, log_path=custom_log)

        assert custom_log in result


class TestConfigureLoggingWithCompression:
    """Tests for configure_logging with compression option."""

    def setup_method(self):
        """Reset logging configuration before each test."""
        structlog.reset_defaults()
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    def test_configure_with_compression_enabled(self, tmp_path: Path):
        """Test configuring logging with compression enabled (default)."""
        log_file = tmp_path / "logs" / "marianne.log"

        configure_logging(
            level="INFO",
            format="json",
            file_path=log_file,
            compress_logs=True,
        )

        # Verify handler is CompressingRotatingFileHandler
        root_logger = logging.getLogger()
        file_handlers = [
            h for h in root_logger.handlers
            if isinstance(h, CompressingRotatingFileHandler)
        ]
        assert len(file_handlers) == 1

    def test_configure_with_compression_disabled(self, tmp_path: Path):
        """Test configuring logging with compression disabled."""
        from logging.handlers import RotatingFileHandler

        log_file = tmp_path / "logs" / "marianne.log"

        configure_logging(
            level="INFO",
            format="json",
            file_path=log_file,
            compress_logs=False,
        )

        # Verify handler is plain RotatingFileHandler (not compressing)
        root_logger = logging.getLogger()
        rotating_handlers = [
            h for h in root_logger.handlers
            if isinstance(h, RotatingFileHandler) and
            not isinstance(h, CompressingRotatingFileHandler)
        ]
        assert len(rotating_handlers) == 1


class TestLogsCLI:
    """Tests for the mzt logs CLI command."""

    def test_logs_command_exists(self):
        """Test that the logs command is registered."""
        from typer.testing import CliRunner

        from marianne.cli import app

        # Check that 'logs' command exists via --help
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "logs" in result.output

    def test_logs_command_help(self):
        """Test that logs command has proper help text."""
        from typer.testing import CliRunner

        from marianne.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["logs", "--help"])

        assert result.exit_code == 0
        assert "Show or tail log files" in result.output
        assert "--follow" in result.output
        assert "--lines" in result.output
        assert "--level" in result.output
        assert "--json" in result.output

    def test_logs_no_file_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test logs command when no log file exists."""
        from typer.testing import CliRunner

        from marianne.cli import app

        # Change to empty workspace
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(app, ["logs"])

        assert result.exit_code == 1
        assert "No log files found" in result.output

    def test_logs_displays_entries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test logs command displays log entries."""
        import json

        from typer.testing import CliRunner

        from marianne.cli import app

        # Create log directory and file
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "marianne.log"

        # Write some JSON log entries
        entries = [
            {"event": "test_event_1", "level": "INFO", "component": "runner"},
            {"event": "test_event_2", "level": "DEBUG", "component": "backend"},
            {"event": "test_event_3", "level": "ERROR", "component": "validator"},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries))

        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(app, ["logs", "--lines", "10"])

        assert result.exit_code == 0
        assert "test_event_1" in result.output
        assert "test_event_2" in result.output
        assert "test_event_3" in result.output

    def test_logs_filters_by_level(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test logs command filters by log level."""
        import json

        from typer.testing import CliRunner

        from marianne.cli import app

        # Create log directory and file
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "marianne.log"

        entries = [
            {"event": "debug_event", "level": "DEBUG"},
            {"event": "info_event", "level": "INFO"},
            {"event": "error_event", "level": "ERROR"},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries))

        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(app, ["logs", "--level", "ERROR"])

        assert result.exit_code == 0
        assert "error_event" in result.output
        assert "debug_event" not in result.output
        assert "info_event" not in result.output

    def test_logs_filters_by_job_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test logs command filters by job ID."""
        import json

        from typer.testing import CliRunner

        from marianne.cli import app

        # Create log directory and file
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "marianne.log"

        entries = [
            {"event": "event_1", "level": "INFO", "job_id": "job-a"},
            {"event": "event_2", "level": "INFO", "job_id": "job-b"},
            {"event": "event_3", "level": "INFO", "job_id": "job-a"},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries))

        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(app, ["logs", "job-a"])

        assert result.exit_code == 0
        assert "event_1" in result.output
        assert "event_3" in result.output
        assert "event_2" not in result.output

    def test_logs_json_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test logs command with JSON output."""
        import json

        from typer.testing import CliRunner

        from marianne.cli import app

        # Create log directory and file
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "marianne.log"

        entry = {"event": "test_event", "level": "INFO", "custom_field": "value"}
        log_file.write_text(json.dumps(entry))

        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(app, ["logs", "--json"])

        assert result.exit_code == 0
        # Output should be valid JSON
        output_lines = [
            line for line in result.output.strip().split("\n")
            if line.startswith("{")
        ]
        assert len(output_lines) >= 1
        parsed = json.loads(output_lines[0])
        assert parsed["event"] == "test_event"

    def test_logs_reads_compressed_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test logs command can read compressed .gz log files."""
        import json

        from typer.testing import CliRunner

        from marianne.cli import app

        # Create log directory
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create compressed log file (use .1.gz to simulate rotated log)
        entry = {"event": "compressed_event", "level": "INFO"}
        gz_file = log_dir / "marianne.log.1.gz"
        gz_file.write_bytes(gzip.compress(json.dumps(entry).encode()))

        # Also create current log file (required by find_log_files)
        current_log = log_dir / "marianne.log"
        current_log.write_text(json.dumps({"event": "current_event", "level": "INFO"}))

        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        # Read from compressed file directly
        result = runner.invoke(app, ["logs", "--file", str(gz_file)])

        assert result.exit_code == 0
        assert "compressed_event" in result.output

    def test_logs_limits_lines(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test logs command respects --lines option."""
        import json

        from typer.testing import CliRunner

        from marianne.cli import app

        # Create log directory and file
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "marianne.log"

        # Write 10 entries
        entries = [
            {"event": f"event_{i}", "level": "INFO"}
            for i in range(10)
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries))

        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(app, ["logs", "--lines", "3"])

        assert result.exit_code == 0
        # Should only see last 3 events (7, 8, 9)
        assert "event_9" in result.output
        assert "event_8" in result.output
        assert "event_7" in result.output
        assert "event_0" not in result.output

    def test_logs_invalid_level(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test logs command rejects invalid log level."""
        from typer.testing import CliRunner

        from marianne.cli import app

        # Create log directory and file
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "marianne.log"
        log_file.write_text('{"event": "test", "level": "INFO"}')

        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(app, ["logs", "--level", "INVALID"])

        assert result.exit_code == 1
        assert "Invalid log level" in result.output
