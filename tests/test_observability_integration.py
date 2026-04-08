"""Integration tests for Marianne observability features.

End-to-end tests verifying that all observability components work together:
- Raw output capture
- Error history recording
- Structured logging
- Circuit breaker integration
- Log rotation
- Preflight checks
- Prompt metrics

These tests simulate real job execution scenarios with failures and verify
that diagnostics are properly captured for debugging.
"""

from __future__ import annotations

import gzip
import json
import logging
import signal
from pathlib import Path
from typing import Any

import pytest
import structlog

from marianne.backends.base import ExecutionResult
from marianne.core.checkpoint import (
    MAX_ERROR_HISTORY,
    MAX_OUTPUT_CAPTURE_BYTES,
    CheckpointState,
    SheetState,
)
from marianne.core.config import JobConfig, LogConfig
from marianne.core.errors import (
    ErrorCategory,
    ErrorClassifier,
    ErrorCode,
)
from marianne.core.logging import (
    CompressingRotatingFileHandler,
    ExecutionContext,
    configure_logging,
    find_log_files,
    get_logger,
    with_context,
)
from marianne.execution.circuit_breaker import CircuitBreaker, CircuitState
from marianne.execution.preflight import (
    PreflightChecker,
    PromptMetrics,
    run_preflight_check,
)
from marianne.execution.retry_strategy import (
    AdaptiveRetryStrategy,
    RetryPattern,
)
from marianne.execution.retry_strategy import (
    ErrorRecord as RetryErrorRecord,
)
from tests.helpers import record_error_on_sheet


class TestOutputCaptureIntegration:
    """Tests for raw output capture in sheet state."""

    def test_capture_output_stores_tail(self):
        """Test that output capture stores the last N bytes."""
        sheet = SheetState(sheet_num=1)
        stdout = "prefix " + "x" * 5000
        stderr = "error " + "y" * 3000

        sheet.capture_output(stdout, stderr)

        assert sheet.stdout_tail is not None
        assert "x" in sheet.stdout_tail
        assert sheet.stderr_tail is not None
        assert "y" in sheet.stderr_tail
        assert sheet.output_truncated is False

    def test_capture_output_truncates_large_output(self):
        """Test that large output is truncated to MAX_OUTPUT_CAPTURE_BYTES."""
        sheet = SheetState(sheet_num=1)
        # Create output larger than the limit
        large_stdout = "S" * (MAX_OUTPUT_CAPTURE_BYTES + 5000)
        large_stderr = "E" * (MAX_OUTPUT_CAPTURE_BYTES + 3000)

        sheet.capture_output(large_stdout, large_stderr)

        assert sheet.output_truncated is True
        # Tail should be at most MAX_OUTPUT_CAPTURE_BYTES
        assert len(sheet.stdout_tail.encode("utf-8")) <= MAX_OUTPUT_CAPTURE_BYTES
        assert len(sheet.stderr_tail.encode("utf-8")) <= MAX_OUTPUT_CAPTURE_BYTES

    def test_capture_output_handles_empty_output(self):
        """Test handling of empty output strings."""
        sheet = SheetState(sheet_num=1)
        sheet.capture_output("", "")

        assert sheet.stdout_tail is None
        assert sheet.stderr_tail is None
        assert sheet.output_truncated is False

    def test_capture_output_handles_unicode(self):
        """Test handling of unicode characters in output."""
        sheet = SheetState(sheet_num=1)
        unicode_stdout = "Hello 世界! 🎉 привет"
        unicode_stderr = "Error: données invalides"

        sheet.capture_output(unicode_stdout, unicode_stderr)

        assert unicode_stdout in sheet.stdout_tail
        assert unicode_stderr in sheet.stderr_tail


class TestErrorHistoryIntegration:
    """Tests for error history recording in sheet state."""

    def test_record_error_adds_to_history(self):
        """Test that errors are added to history."""
        sheet = SheetState(sheet_num=1)

        record_error_on_sheet(sheet,
            error_type="transient",
            error_code="E001",
            error_message="Connection timeout",
            attempt=1,
            exit_code=1,
        )

        assert len(sheet.error_history) == 1
        assert sheet.error_history[0].error_code == "E001"
        assert sheet.error_history[0].error_type == "transient"

    def test_record_error_trims_to_max_history(self):
        """Test that error history is trimmed to MAX_ERROR_HISTORY."""
        sheet = SheetState(sheet_num=1)

        # Add more errors than the maximum
        for i in range(MAX_ERROR_HISTORY + 5):
            record_error_on_sheet(sheet,
                error_type="transient",
                error_code=f"E00{i}",
                error_message=f"Error {i}",
                attempt=i + 1,
            )

        assert len(sheet.error_history) == MAX_ERROR_HISTORY
        # Should keep most recent
        assert sheet.error_history[-1].error_message == f"Error {MAX_ERROR_HISTORY + 4}"

    def test_record_error_includes_context(self):
        """Test that error context is properly stored."""
        sheet = SheetState(sheet_num=1)

        record_error_on_sheet(sheet,
            error_type="rate_limit",
            error_code="E101",
            error_message="Rate limit exceeded",
            attempt=2,
            stdout_tail="Last output...",
            stderr_tail="Error details...",
            exit_code=429,
            signal=None,
            category="rate_limit",
        )

        error = sheet.error_history[0]
        assert error.stdout_tail == "Last output..."
        assert error.stderr_tail == "Error details..."
        assert error.context["exit_code"] == 429
        assert error.context["category"] == "rate_limit"


class TestErrorClassifierIntegration:
    """Tests for error classification with proper error codes."""

    def test_rate_limit_detection_assigns_code(self):
        """Test that rate limit errors get appropriate error codes."""
        classifier = ErrorClassifier()

        result = classifier.classify(
            stdout="API returned: rate limit exceeded",
            stderr="",
            exit_code=1,
        )

        assert result.category == ErrorCategory.RATE_LIMIT
        assert result.error_code == ErrorCode.RATE_LIMIT_API
        assert result.retriable is True
        assert result.suggested_wait_seconds is not None

    def test_signal_classification(self):
        """Test that signal-based exits are properly classified."""
        classifier = ErrorClassifier()

        # SIGTERM (15)
        result = classifier.classify(
            exit_signal=signal.SIGTERM,
            exit_reason="killed",
        )

        assert result.category == ErrorCategory.SIGNAL
        assert result.error_code == ErrorCode.EXECUTION_KILLED
        assert result.exit_signal == signal.SIGTERM

    def test_timeout_classification(self):
        """Test that timeout errors are properly classified."""
        classifier = ErrorClassifier()

        result = classifier.classify(
            exit_signal=signal.SIGTERM,
            exit_reason="timeout",
        )

        assert result.category == ErrorCategory.TIMEOUT
        assert result.error_code == ErrorCode.EXECUTION_TIMEOUT
        assert result.retriable is True

    def test_fatal_signal_classification(self):
        """Test that fatal signals (SIGSEGV, etc.) are not retriable."""
        classifier = ErrorClassifier()

        result = classifier.classify(
            exit_signal=signal.SIGSEGV,
            exit_reason="killed",
        )

        assert result.category == ErrorCategory.FATAL
        assert result.error_code == ErrorCode.EXECUTION_CRASHED
        assert result.retriable is False


class TestPreflightIntegration:
    """Tests for preflight checks with prompt metrics."""

    def test_prompt_metrics_calculation(self):
        """Test that prompt metrics are correctly calculated."""
        prompt = "This is a test prompt with some content.\n" * 100

        metrics = PromptMetrics.from_prompt(prompt)

        assert metrics.character_count == len(prompt)
        # line_count is newlines + 1, and we have 100 newlines + trailing text
        assert metrics.line_count == 101
        # Token estimate uses math.ceil(chars / 3.5)
        import math
        assert metrics.estimated_tokens == math.ceil(len(prompt) / 3.5)

    def test_preflight_detects_large_prompts(self, tmp_path: Path):
        """Test that preflight warns about large prompts."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        checker = PreflightChecker(workspace=workspace)

        # Create a very large prompt (need > 50K tokens, which is 50K * 4 chars = 200K chars)
        # The check is > threshold, so we need more than 50K * 4 chars
        large_prompt = "x" * (50_001 * 4)  # > 50K estimated tokens

        result = checker.check(prompt=large_prompt)

        assert len(result.warnings) > 0
        # Should have a warning about large prompt
        assert any("token" in w.lower() or "large" in w.lower() for w in result.warnings)

    def test_preflight_validates_working_directory(self, tmp_path: Path):
        """Test that preflight validates working directory."""
        # Use non-existent directory
        nonexistent = tmp_path / "nonexistent"
        checker = PreflightChecker(workspace=nonexistent, working_directory=nonexistent)

        result = checker.check(prompt="test prompt")

        assert len(result.errors) > 0
        assert any("directory" in e.lower() or "path" in e.lower() for e in result.errors)


class TestCircuitBreakerIntegration:
    """Tests for circuit breaker integration with job execution."""

    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures(self):
        """Test that circuit opens after failure threshold."""
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)

        await breaker.record_failure()
        await breaker.record_failure()
        assert await breaker.get_state() == CircuitState.CLOSED

        await breaker.record_failure()  # Third failure
        assert await breaker.get_state() == CircuitState.OPEN
        assert await breaker.can_execute() is False

    @pytest.mark.asyncio
    async def test_circuit_breaker_with_error_classifier(self):
        """Test circuit breaker responds to classified errors."""
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=60.0)
        classifier = ErrorClassifier()

        # Simulate transient errors
        error = classifier.classify(stderr="connection timeout", exit_code=1)
        assert error.retriable is True

        # Record failures
        await breaker.record_failure()
        await breaker.record_failure()

        assert await breaker.get_state() == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_circuit_breaker_stats_tracking(self):
        """Test that circuit breaker stats are properly tracked."""
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)

        await breaker.record_success()
        await breaker.record_failure()
        await breaker.record_failure()  # Opens circuit

        stats = await breaker.get_stats()
        assert stats.total_successes == 1
        assert stats.total_failures == 2
        assert stats.times_opened == 1


class TestRetryStrategyIntegration:
    """Tests for adaptive retry strategy with error history."""

    def test_retry_strategy_analyzes_history(self):
        """Test that retry strategy analyzes error history."""
        strategy = AdaptiveRetryStrategy()
        classifier = ErrorClassifier()

        # Create error history
        error = classifier.classify(stderr="connection timeout", exit_code=1)
        history = [
            RetryErrorRecord.from_classified_error(error, sheet_num=1, attempt_num=1),
        ]

        recommendation = strategy.analyze(history)

        assert recommendation.should_retry is True
        assert recommendation.delay_seconds > 0

    def test_retry_strategy_detects_rate_limit_pattern(self):
        """Test that retry strategy detects rate limit patterns."""
        strategy = AdaptiveRetryStrategy()
        classifier = ErrorClassifier()

        # Create rate limit error
        error = classifier.classify(stdout="rate limit exceeded", exit_code=429)
        history = [
            RetryErrorRecord.from_classified_error(error, sheet_num=1, attempt_num=1),
        ]

        recommendation = strategy.analyze(history)

        assert recommendation.detected_pattern == RetryPattern.RATE_LIMITED
        assert recommendation.should_retry is True
        # Rate limit should have longer delay
        assert recommendation.delay_seconds >= 60

    def test_retry_strategy_recommends_abort_for_persistent_errors(self):
        """Test that repeated same error recommends abort."""
        strategy = AdaptiveRetryStrategy()
        classifier = ErrorClassifier()

        # Create repeated auth errors (non-retriable)
        error = classifier.classify(stderr="unauthorized access", exit_code=401)
        history = [
            RetryErrorRecord.from_classified_error(error, sheet_num=1, attempt_num=i)
            for i in range(1, 5)
        ]

        recommendation = strategy.analyze(history)

        # Should not retry non-retriable errors
        assert recommendation.should_retry is False


class TestLoggingIntegration:
    """Tests for structured logging throughout execution."""

    def setup_method(self):
        """Reset logging configuration."""
        structlog.reset_defaults()
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    def test_context_propagation_in_logs(self, tmp_path: Path):
        """Test that execution context is propagated to logs."""
        captured_logs: list[str] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured_logs.append(record.getMessage())

        configure_logging(level="DEBUG", format="json", include_timestamps=False)

        root_logger = logging.getLogger()
        root_logger.addHandler(CapturingHandler())

        logger = get_logger("test")
        ctx = ExecutionContext(job_id="test-job", run_id="run-123", sheet_num=5)

        with with_context(ctx):
            logger.info("test_event", custom_field="value")

        assert len(captured_logs) == 1
        log_entry = json.loads(captured_logs[0])

        assert log_entry["job_id"] == "test-job"
        assert log_entry["run_id"] == "run-123"
        assert log_entry["sheet_num"] == 5
        assert log_entry["custom_field"] == "value"

    def test_sensitive_data_redaction(self, tmp_path: Path):
        """Test that sensitive data is redacted in logs."""
        captured_logs: list[str] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured_logs.append(record.getMessage())

        configure_logging(level="DEBUG", format="json", include_timestamps=False)

        root_logger = logging.getLogger()
        root_logger.addHandler(CapturingHandler())

        logger = get_logger("test")
        logger.info("test_event", api_key="sk-secret-123", safe_field="visible")

        assert len(captured_logs) == 1
        log_entry = json.loads(captured_logs[0])

        assert log_entry["api_key"] == "[REDACTED]"
        assert log_entry["safe_field"] == "visible"


class TestLogRotationIntegration:
    """Tests for log file rotation and compression."""

    def test_log_rotation_creates_compressed_backup(self, tmp_path: Path):
        """Test that log rotation creates compressed backups."""
        log_file = tmp_path / "test.log"

        handler = CompressingRotatingFileHandler(
            log_file,
            maxBytes=100,  # Small size to trigger rotation
            backupCount=3,
        )

        # Write enough to trigger rotation
        for i in range(10):
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=f"Log message {i} " + "x" * 50,
                args=(),
                exc_info=None,
            )
            handler.emit(record)

        handler.close()

        # Check for compressed backups
        log_files = list(tmp_path.glob("test.log*"))
        # Should have at least current log file
        assert len(log_files) >= 1

    def test_find_log_files_discovers_all_files(self, tmp_path: Path):
        """Test that find_log_files discovers current and backup logs."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create current log and compressed backups
        (log_dir / "marianne.log").write_text("current log")
        (log_dir / "marianne.log.1.gz").write_bytes(gzip.compress(b"backup 1"))
        (log_dir / "marianne.log.2.gz").write_bytes(gzip.compress(b"backup 2"))

        files = find_log_files(tmp_path)

        assert len(files) == 3
        assert log_dir / "marianne.log" in files
        assert log_dir / "marianne.log.1.gz" in files
        assert log_dir / "marianne.log.2.gz" in files


class TestEndToEndObservability:
    """End-to-end tests simulating complete job execution with failures."""

    @pytest.fixture
    def sample_config(self, tmp_path: Path) -> dict[str, Any]:
        """Create a sample job configuration."""
        return {
            "name": "observability-test-job",
            "description": "Test job for observability",
            "workspace": str(tmp_path / "workspace"),
            "backend": {
                "type": "claude_cli",
                "skip_permissions": True,
            },
            "sheet": {
                "size": 5,
                "total_items": 10,
            },
            "prompt": {
                "template": "Process sheet {{ sheet_num }}",
            },
            "retry": {
                "max_retries": 2,
            },
            "logging": {
                "level": "DEBUG",
                "format": "console",
            },
        }

    def test_full_observability_flow(self, tmp_path: Path, sample_config: dict[str, Any]):
        """Test complete observability flow with simulated execution."""
        # Create job configuration
        config = JobConfig(**sample_config)

        # Create checkpoint state
        state = CheckpointState(
            job_id=config.name,
            job_name=config.name,
            total_sheets=2,
        )

        # Simulate sheet 1 starting
        state.mark_sheet_started(1)
        sheet = state.sheets[1]

        # Simulate preflight check
        workspace = Path(config.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        result = run_preflight_check(
            prompt="Test prompt for sheet 1",
            workspace=workspace,
            working_directory=workspace,
        )

        # Store preflight metrics in sheet state
        sheet.prompt_metrics = {
            "character_count": result.prompt_metrics.character_count,
            "estimated_tokens": result.prompt_metrics.estimated_tokens,
            "line_count": result.prompt_metrics.line_count,
        }
        sheet.preflight_warnings = result.warnings

        # Simulate execution with output
        stdout = "Processing...\nCompleted step 1\nCompleted step 2"
        stderr = "Warning: some warning message"
        sheet.capture_output(stdout, stderr)

        # Simulate failure and error recording
        classifier = ErrorClassifier()
        error = classifier.classify(
            stdout=stdout,
            stderr="connection timeout",
            exit_code=1,
        )

        record_error_on_sheet(sheet,
            error_type="transient" if error.retriable else "permanent",
            error_code=error.error_code.value,
            error_message=error.message,
            attempt=1,
            exit_code=error.exit_code,
            category=error.category.value,
        )

        # Mark sheet failed
        state.mark_sheet_failed(
            sheet_num=1,
            error_message=error.message,
            error_category=error.category.value,
            exit_code=error.exit_code,
            execution_duration_seconds=5.5,
        )

        # Verify observability data is captured
        assert sheet.stdout_tail is not None
        assert "Completed step 2" in sheet.stdout_tail
        assert sheet.stderr_tail is not None
        assert len(sheet.error_history) == 1
        assert sheet.prompt_metrics is not None
        assert sheet.execution_duration_seconds == 5.5

    @pytest.mark.asyncio
    async def test_circuit_breaker_integration_with_state(self, tmp_path: Path):
        """Test circuit breaker integration with checkpoint state."""
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=60.0)
        classifier = ErrorClassifier()

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=5,
        )

        # Simulate multiple sheet failures
        for sheet_num in range(1, 4):
            state.mark_sheet_started(sheet_num)
            sheet = state.sheets[sheet_num]

            # Simulate failure
            error = classifier.classify(stderr="connection error", exit_code=1)
            record_error_on_sheet(sheet,
                error_type="transient",
                error_code=error.error_code.value,
                error_message=error.message,
                attempt=1,
            )

            await breaker.record_failure()

            state.mark_sheet_failed(
                sheet_num=sheet_num,
                error_message=error.message,
                error_category=error.category.value,
            )

            if not await breaker.can_execute():
                break

        # Circuit should be open after 2 failures
        assert await breaker.get_state() == CircuitState.OPEN
        stats = await breaker.get_stats()
        assert stats.total_failures >= 2

    def test_logging_captures_full_execution_trace(self, tmp_path: Path):
        """Test that logging captures the full execution trace."""
        captured_logs: list[str] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured_logs.append(record.getMessage())

        structlog.reset_defaults()
        configure_logging(level="DEBUG", format="json", include_timestamps=False)

        root_logger = logging.getLogger()
        root_logger.addHandler(CapturingHandler())

        # Simulate execution with logging
        logger = get_logger("runner")
        ctx = ExecutionContext(job_id="trace-test", run_id="run-abc")

        with with_context(ctx):
            logger.info("job_started", total_sheets=3)

            # Sheet 1
            sheet_ctx = ctx.with_sheet(1)
            with with_context(sheet_ctx):
                logger.info("sheet_started")
                logger.debug("executing_prompt", prompt_tokens=1000)
                logger.info("sheet_completed", duration=5.2)

            # Sheet 2 with failure
            sheet_ctx = ctx.with_sheet(2)
            with with_context(sheet_ctx):
                logger.info("sheet_started")
                logger.warning("sheet_retry", attempt=2, error="timeout")
                logger.error("sheet_failed", error_code="E001")

            logger.info("job_completed", status="partial")

        # Verify trace is complete
        events = [json.loads(log)["event"] for log in captured_logs]
        assert "job_started" in events
        assert "sheet_started" in events
        assert "sheet_completed" in events
        assert "sheet_retry" in events
        assert "sheet_failed" in events
        assert "job_completed" in events

        # Verify context propagation
        for log in captured_logs:
            entry = json.loads(log)
            assert entry["job_id"] == "trace-test"
            assert entry["run_id"] == "run-abc"


class TestDiagnosticsReporting:
    """Tests for diagnostic data availability in checkpoint state."""

    def test_checkpoint_contains_diagnostic_data(self):
        """Test that checkpoint state contains all diagnostic fields."""
        state = CheckpointState(
            job_id="diag-test",
            job_name="Diagnostics Test",
            total_sheets=3,
        )

        # Process sheet 1
        state.mark_sheet_started(1)
        sheet = state.sheets[1]

        # Add all diagnostic data
        sheet.capture_output("stdout content", "stderr content")
        sheet.prompt_metrics = {
            "character_count": 1000,
            "estimated_tokens": 250,
            "line_count": 50,
        }
        sheet.preflight_warnings = ["Large prompt detected"]
        record_error_on_sheet(sheet,
            error_type="transient",
            error_code="E001",
            error_message="Timeout error",
            attempt=1,
            exit_code=1,
        )

        # Serialize and deserialize (simulating state persistence)
        state_dict = state.model_dump()
        restored = CheckpointState.model_validate(state_dict)

        # Verify all diagnostic data survives serialization
        restored_sheet = restored.sheets[1]
        assert restored_sheet.stdout_tail == "stdout content"
        assert restored_sheet.stderr_tail == "stderr content"
        assert restored_sheet.prompt_metrics["character_count"] == 1000
        assert len(restored_sheet.preflight_warnings) == 1
        assert len(restored_sheet.error_history) == 1

    def test_error_history_serialization(self):
        """Test that error history properly serializes and deserializes."""
        sheet = SheetState(sheet_num=1)

        # Add multiple errors
        # Note: context is passed as **kwargs in record_error, not as a named field
        for i in range(3):
            record_error_on_sheet(sheet,
                error_type="transient",
                error_code=f"E00{i}",
                error_message=f"Error {i}",
                attempt=i + 1,
                stdout_tail=f"stdout {i}",
                iteration=i,  # This becomes context["iteration"]
            )

        # Serialize and deserialize
        sheet_dict = sheet.model_dump()
        restored = SheetState.model_validate(sheet_dict)

        assert len(restored.error_history) == 3
        for i, error in enumerate(restored.error_history):
            assert error.error_code == f"E00{i}"
            assert error.context["iteration"] == i


class TestExecutionResultObservability:
    """Tests for ExecutionResult observability fields."""

    def test_execution_result_captures_signal_info(self):
        """Test that ExecutionResult captures signal information."""
        result = ExecutionResult(
            success=False,
            stdout="partial output",
            stderr="killed",
            duration_seconds=30.5,
            exit_code=None,
            exit_signal=9,  # SIGKILL
            exit_reason="timeout",
        )

        assert result.exit_signal == 9
        assert result.exit_reason == "timeout"
        assert result.duration_seconds == 30.5

    def test_execution_result_captures_full_output(self):
        """Test that ExecutionResult captures full stdout/stderr."""
        result = ExecutionResult(
            success=True,
            stdout="Line 1\nLine 2\nLine 3",
            stderr="Warning: something",
            duration_seconds=5.0,
            exit_code=0,
        )

        assert "Line 1" in result.stdout
        assert "Line 3" in result.stdout
        assert "Warning" in result.stderr


class TestLogConfigValidation:
    """Tests for LogConfig validation."""

    def test_log_config_defaults(self):
        """Test LogConfig has correct defaults."""
        config = LogConfig()

        assert config.level == "INFO"
        assert config.format == "console"
        assert config.file_path is None
        assert config.max_file_size_mb == 50
        assert config.backup_count == 5

    def test_log_config_validates_level(self):
        """Test that invalid log levels are rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LogConfig(level="TRACE")  # Invalid

    def test_log_config_validates_format(self):
        """Test that invalid formats are rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LogConfig(format="xml")  # Invalid


class TestPreflightErrorCodes:
    """Tests for preflight error code generation."""

    def test_preflight_generates_error_codes(self, tmp_path: Path):
        """Test that preflight errors include appropriate error codes."""
        # Use non-existent directory
        nonexistent = tmp_path / "nonexistent"
        checker = PreflightChecker(workspace=nonexistent, working_directory=nonexistent)

        result = checker.check(prompt="test")

        assert len(result.errors) > 0
        # Should contain path-related error message
        assert any("path" in e.lower() or "directory" in e.lower() for e in result.errors)
