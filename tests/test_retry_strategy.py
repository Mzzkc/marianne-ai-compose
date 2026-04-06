"""Tests for adaptive retry strategy.

Tests cover:
- ErrorRecord creation and serialization
- RetryRecommendation validation
- Pattern detection (rapid failures, repeated errors, cascading, rate limits)
- Retry recommendations for each pattern
- Confidence calculations
- Configuration handling
- Delay learning (Evolution: Dynamic Retry Delay Learning)
"""

import time
from datetime import UTC, datetime

import pytest

from marianne.core.errors import (
    ClassificationResult,
    ClassifiedError,
    ErrorCategory,
    ErrorCode,
    RetryBehavior,
)
from marianne.execution.retry_strategy import (
    AdaptiveRetryStrategy,
    DelayHistory,
    DelayOutcome,
    ErrorRecord,
    RetryPattern,
    RetryRecommendation,
    RetryStrategyConfig,
)


class TestErrorRecord:
    """Tests for ErrorRecord dataclass."""

    def test_from_classified_error_basic(self) -> None:
        """Test creating ErrorRecord from a basic ClassifiedError."""
        classified = ClassifiedError(
            category=ErrorCategory.TRANSIENT,
            message="Connection refused",
            error_code=ErrorCode.NETWORK_CONNECTION_FAILED,
            exit_code=1,
            retriable=True,
            suggested_wait_seconds=30.0,
        )

        record = ErrorRecord.from_classified_error(
            error=classified,
            sheet_num=5,
            attempt_num=2,
        )

        assert record.error_code == ErrorCode.NETWORK_CONNECTION_FAILED
        assert record.category == ErrorCategory.TRANSIENT
        assert record.message == "Connection refused"
        assert record.exit_code == 1
        assert record.retriable is True
        assert record.suggested_wait == 30.0
        assert record.sheet_num == 5
        assert record.attempt_num == 2
        assert record.timestamp.tzinfo is not None  # UTC aware

    def test_from_classified_error_signal(self) -> None:
        """Test creating ErrorRecord from a signal-based error."""
        classified = ClassifiedError(
            category=ErrorCategory.SIGNAL,
            message="Process killed by SIGTERM",
            error_code=ErrorCode.EXECUTION_KILLED,
            exit_signal=15,  # SIGTERM
            retriable=True,
        )

        record = ErrorRecord.from_classified_error(error=classified)

        assert record.exit_signal == 15
        assert record.exit_code is None

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        record = ErrorRecord(
            timestamp=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            category=ErrorCategory.TIMEOUT,
            message="Command timed out",
            exit_code=124,
            retriable=True,
            suggested_wait=60.0,
            sheet_num=3,
            attempt_num=2,
        )

        d = record.to_dict()

        assert d["error_code"] == "E001"
        assert d["category"] == "timeout"
        assert d["message"] == "Command timed out"
        assert d["exit_code"] == 124
        assert d["retriable"] is True
        assert d["suggested_wait"] == 60.0
        assert d["sheet_num"] == 3
        assert d["attempt_num"] == 2
        assert "2024-01-15" in str(d["timestamp"])

    def test_from_classification_result_captures_root_cause_info(self) -> None:
        """Test creating ErrorRecord from ClassificationResult captures confidence."""
        primary = ClassifiedError(
            category=ErrorCategory.CONFIGURATION,
            message="Binary not found",
            error_code=ErrorCode.BACKEND_NOT_FOUND,
            exit_code=127,
            retriable=False,
        )
        secondary = ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limit exceeded",
            error_code=ErrorCode.RATE_LIMIT_API,
            retriable=True,
            suggested_wait_seconds=3600.0,
        )
        result = ClassificationResult(
            primary=primary,
            secondary=[secondary],
            confidence=0.85,
            classification_method="structured",
        )

        record = ErrorRecord.from_classification_result(
            result=result,
            sheet_num=3,
            attempt_num=2,
        )

        # Should use primary error's properties
        assert record.error_code == ErrorCode.BACKEND_NOT_FOUND
        assert record.category == ErrorCategory.CONFIGURATION
        assert record.message == "Binary not found"
        assert record.retriable is False

        # Should capture root cause analysis metadata
        assert record.root_cause_confidence == 0.85
        assert record.secondary_error_count == 1
        assert record.sheet_num == 3
        assert record.attempt_num == 2

    def test_from_classification_result_single_error(self) -> None:
        """Test ClassificationResult with only primary error (no secondary)."""
        primary = ClassifiedError(
            category=ErrorCategory.TIMEOUT,
            message="Command timed out",
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            exit_code=124,
            retriable=True,
            suggested_wait_seconds=60.0,
        )
        result = ClassificationResult(
            primary=primary,
            secondary=[],
            confidence=1.0,  # 100% confidence when only one error
            classification_method="exit_code",
        )

        record = ErrorRecord.from_classification_result(result=result)

        assert record.error_code == ErrorCode.EXECUTION_TIMEOUT
        assert record.root_cause_confidence == 1.0
        assert record.secondary_error_count == 0
        assert record.suggested_wait == 60.0

    def test_to_dict_includes_root_cause_fields(self) -> None:
        """Test to_dict includes root_cause_confidence and secondary_error_count."""
        record = ErrorRecord(
            timestamp=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
            error_code=ErrorCode.BACKEND_NOT_FOUND,
            category=ErrorCategory.CONFIGURATION,
            message="ENOENT",
            exit_code=127,
            retriable=False,
            root_cause_confidence=0.75123,  # Use value that needs rounding
            secondary_error_count=2,
        )

        d = record.to_dict()

        assert d["root_cause_confidence"] == 0.751  # Should be rounded to 3 decimals
        assert d["secondary_error_count"] == 2

    def test_from_classification_result_confidence_clamped(self) -> None:
        """Test that out-of-range confidence values are clamped to [0.0, 1.0]."""
        primary = ClassifiedError(
            category=ErrorCategory.TIMEOUT,
            message="Command timed out",
            error_code=ErrorCode.EXECUTION_TIMEOUT,
            exit_code=124,
            retriable=True,
        )
        # Create result with confidence > 1.0 — should be clamped by __post_init__
        result = ClassificationResult(
            primary=primary,
            secondary=[],
            confidence=1.5,
            classification_method="structured",
        )
        assert result.confidence == 1.0  # Clamped by __post_init__

        record = ErrorRecord.from_classification_result(result=result)
        assert record.root_cause_confidence == 1.0

        # Test negative confidence is clamped to 0.0
        result_neg = ClassificationResult(
            primary=primary,
            secondary=[],
            confidence=-0.5,
            classification_method="structured",
        )
        assert result_neg.confidence == 0.0



class TestRetryRecommendation:
    """Tests for RetryRecommendation dataclass."""

    def test_valid_recommendation(self) -> None:
        """Test creating a valid recommendation."""
        rec = RetryRecommendation(
            should_retry=True,
            delay_seconds=15.0,
            reason="Standard retry",
            confidence=0.8,
            detected_pattern=RetryPattern.NONE,
            strategy_used="exponential_backoff",
        )

        assert rec.should_retry is True
        assert rec.delay_seconds == 15.0
        assert rec.confidence == 0.8
        assert rec.detected_pattern == RetryPattern.NONE

    def test_invalid_confidence_high(self) -> None:
        """Test that confidence > 1.0 raises error."""
        with pytest.raises(ValueError, match="confidence must be"):
            RetryRecommendation(
                should_retry=True,
                delay_seconds=10.0,
                reason="Test",
                confidence=1.5,  # Invalid
            )

    def test_invalid_confidence_low(self) -> None:
        """Test that confidence < 0.0 raises error."""
        with pytest.raises(ValueError, match="confidence must be"):
            RetryRecommendation(
                should_retry=True,
                delay_seconds=10.0,
                reason="Test",
                confidence=-0.1,  # Invalid
            )

    def test_invalid_delay_negative(self) -> None:
        """Test that negative delay raises error."""
        with pytest.raises(ValueError, match="delay_seconds must be"):
            RetryRecommendation(
                should_retry=True,
                delay_seconds=-5.0,  # Invalid
                reason="Test",
                confidence=0.5,
            )

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        rec = RetryRecommendation(
            should_retry=True,
            delay_seconds=25.5,
            reason="Rapid failures detected",
            confidence=0.654321,
            detected_pattern=RetryPattern.RAPID_FAILURES,
            strategy_used="rapid_failure_backoff",
        )

        d = rec.to_dict()

        assert d["should_retry"] is True
        assert d["delay_seconds"] == 25.5
        assert d["reason"] == "Rapid failures detected"
        assert d["confidence"] == 0.654  # Rounded to 3 decimal places
        assert d["detected_pattern"] == "rapid_failures"
        assert d["strategy_used"] == "rapid_failure_backoff"



class TestRetryStrategyConfig:
    """Tests for RetryStrategyConfig."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = RetryStrategyConfig()

        assert config.base_delay == 10.0
        assert config.max_delay == 3600.0
        assert config.exponential_base == 2.0
        assert config.rapid_failure_window == 60.0
        assert config.rapid_failure_threshold == 3
        assert config.jitter_factor == 0.25

    def test_custom_config(self) -> None:
        """Test custom configuration values."""
        config = RetryStrategyConfig(
            base_delay=5.0,
            max_delay=600.0,
            exponential_base=1.5,
            rapid_failure_window=30.0,
            rapid_failure_threshold=5,
        )

        assert config.base_delay == 5.0
        assert config.max_delay == 600.0

    @pytest.mark.parametrize("base_delay", [0, -1, -0.5])
    def test_invalid_base_delay(self, base_delay: float) -> None:
        """Test that non-positive base_delay raises error."""
        with pytest.raises(ValueError, match="base_delay must be positive"):
            RetryStrategyConfig(base_delay=base_delay)

    def test_invalid_max_delay(self) -> None:
        """Test that max_delay < base_delay raises error."""
        with pytest.raises(ValueError, match="max_delay must be >= base_delay"):
            RetryStrategyConfig(base_delay=100.0, max_delay=50.0)

    @pytest.mark.parametrize("exp_base", [1.0, 0.5, 0.0, -1.0])
    def test_invalid_exponential_base(self, exp_base: float) -> None:
        """Test that exponential_base <= 1 raises error."""
        with pytest.raises(ValueError, match="exponential_base must be > 1"):
            RetryStrategyConfig(exponential_base=exp_base)



class TestPatternDetection:
    """Tests for pattern detection in AdaptiveRetryStrategy."""

    def test_empty_history_returns_default(self) -> None:
        """Test that empty error history uses default retry."""
        strategy = AdaptiveRetryStrategy()

        rec = strategy.analyze([])

        assert rec.should_retry is True
        assert rec.detected_pattern == RetryPattern.NONE
        assert rec.strategy_used == "default"

    def test_rate_limit_detected(self) -> None:
        """Test that rate limit errors are detected."""
        strategy = AdaptiveRetryStrategy()

        record = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.RATE_LIMIT_API,
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limit exceeded",
            retriable=True,
            suggested_wait=3600.0,
        )

        rec = strategy.analyze([record])

        assert rec.should_retry is True
        assert rec.detected_pattern == RetryPattern.RATE_LIMITED
        assert rec.strategy_used == "rate_limit_wait"
        assert rec.delay_seconds >= 3600.0  # At least the suggested wait

    def test_rapid_failures_detected(self) -> None:
        """Test that rapid consecutive failures are detected."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                rapid_failure_threshold=3,
                rapid_failure_window=60.0,
            )
        )

        # Create 3 errors with very close timestamps (simulating rapid failures)
        now = time.monotonic()
        errors = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.EXECUTION_UNKNOWN,
                category=ErrorCategory.TRANSIENT,
                message=f"Error {i}",
                retriable=True,
                monotonic_time=now + i * 0.1,  # Very close together
            )
            for i in range(3)
        ]

        rec = strategy.analyze(errors)

        assert rec.detected_pattern == RetryPattern.RAPID_FAILURES
        assert rec.strategy_used == "rapid_failure_backoff"

    def test_repeated_error_code_detected(self) -> None:
        """Test that repeated same error codes are detected."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                repeated_error_threshold=2,
                rapid_failure_window=1.0,  # Short window to avoid triggering rapid
            )
        )

        # Create multiple errors with the same code, but spread out
        # The last error's monotonic_time must be far from the window
        base_time = time.monotonic() - 200  # Start in the past
        errors = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.NETWORK_CONNECTION_FAILED,
                category=ErrorCategory.NETWORK,
                message="Connection refused",
                retriable=True,
                monotonic_time=base_time + i * 100,  # Spread out: 0, 100, 200
            )
            for i in range(3)
        ]

        rec = strategy.analyze(errors)

        assert rec.detected_pattern == RetryPattern.REPEATED_ERROR_CODE

    def test_repeated_error_code_aborts_after_threshold(self) -> None:
        """Test that repeated errors eventually recommend not retrying."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                repeated_error_threshold=2,
                repeated_error_strategy_change_threshold=3,
                rapid_failure_window=1.0,  # Short to avoid triggering rapid
            )
        )

        # Create enough repeated errors to trigger abort
        # Spread them far apart to avoid rapid failure detection
        base_time = time.monotonic() - 400  # Start in the past
        errors = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.BACKEND_CONNECTION,
                category=ErrorCategory.NETWORK,
                message="Connection failed",
                retriable=True,
                monotonic_time=base_time + i * 100,  # Spread out: 0, 100, 200, 300
            )
            for i in range(4)
        ]

        rec = strategy.analyze(errors)

        assert rec.should_retry is False
        assert rec.detected_pattern == RetryPattern.REPEATED_ERROR_CODE
        assert rec.strategy_used == "repeated_error_abort"

    def test_cascading_failures_detected(self) -> None:
        """Test that different error types are detected as cascading."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                rapid_failure_window=1.0,  # Short to avoid triggering rapid
            )
        )

        # Create errors with different codes, spread out to avoid rapid failure detection
        base_time = time.monotonic() - 300  # Start in the past
        errors = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.NETWORK_CONNECTION_FAILED,
                category=ErrorCategory.NETWORK,
                message="Connection failed",
                retriable=True,
                monotonic_time=base_time,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.EXECUTION_TIMEOUT,
                category=ErrorCategory.TIMEOUT,
                message="Timeout",
                retriable=True,
                monotonic_time=base_time + 100,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.BACKEND_RESPONSE,
                category=ErrorCategory.TRANSIENT,
                message="Bad response",
                retriable=True,
                monotonic_time=base_time + 200,
            ),
        ]

        rec = strategy.analyze(errors)

        assert rec.detected_pattern == RetryPattern.CASCADING_FAILURES

    def test_non_retriable_error_does_not_retry(self) -> None:
        """Test that non-retriable errors are not retried."""
        strategy = AdaptiveRetryStrategy()

        record = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.BACKEND_AUTH,
            category=ErrorCategory.AUTH,
            message="Authentication failed",
            retriable=False,  # Not retriable
        )

        rec = strategy.analyze([record])

        assert rec.should_retry is False
        assert rec.confidence >= 0.9  # High confidence in not retrying



class TestRetryRecommendations:
    """Tests for retry recommendations."""

    def test_standard_exponential_backoff(self) -> None:
        """Test standard retry using ErrorCode-specific delays (or exponential backoff fallback)."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                base_delay=10.0,
                exponential_base=2.0,
                jitter_factor=0.0,  # No jitter for predictable testing
            )
        )

        # Single transient error - EXECUTION_UNKNOWN has ErrorCode-specific delay of 10s
        record = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.EXECUTION_UNKNOWN,
            category=ErrorCategory.TRANSIENT,
            message="Unknown error",
            retriable=True,
        )

        rec = strategy.analyze([record])

        assert rec.should_retry is True
        assert rec.detected_pattern == RetryPattern.NONE
        # Now uses ErrorCode-specific strategy (EXECUTION_UNKNOWN has 10s delay)
        assert rec.strategy_used == "error_code_specific"
        # EXECUTION_UNKNOWN has base delay of 10.0s
        assert rec.delay_seconds == 10.0

    def test_exponential_backoff_increases(self) -> None:
        """Test that delay increases with more attempts."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                base_delay=10.0,
                exponential_base=2.0,
                jitter_factor=0.0,
            )
        )

        # Multiple errors = later attempts
        base_time = time.monotonic()
        errors = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.EXECUTION_UNKNOWN,
                category=ErrorCategory.TRANSIENT,
                message=f"Error {i}",
                retriable=True,
                monotonic_time=base_time + i * 100,  # Spread out
            )
            for i in range(3)
        ]

        rec = strategy.analyze(errors)

        # 3rd attempt: base * 2^2 = 10 * 4 = 40.0
        # But it might be different due to pattern detection
        assert rec.delay_seconds > 10.0  # Should increase

    def test_max_delay_cap(self) -> None:
        """Test that delay is capped at max_delay."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                base_delay=100.0,
                max_delay=500.0,
                exponential_base=2.0,
                jitter_factor=0.0,
            )
        )

        # Many errors to reach high exponential value
        base_time = time.monotonic()
        errors = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.EXECUTION_UNKNOWN,
                category=ErrorCategory.TRANSIENT,
                message=f"Error {i}",
                retriable=True,
                monotonic_time=base_time + i * 100,
            )
            for i in range(10)  # 10 errors
        ]

        rec = strategy.analyze(errors)

        # Without cap: 100 * 2^9 = 51200.0
        # With cap: should be <= max_delay (500.0) + some jitter (0 here)
        assert rec.delay_seconds <= 500.0

    def test_jitter_applied(self) -> None:
        """Test that jitter adds randomness to delay."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                base_delay=100.0,
                jitter_factor=0.25,
            )
        )

        # Use EXECUTION_UNKNOWN which has 10s ErrorCode-specific delay
        record = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.EXECUTION_UNKNOWN,
            category=ErrorCategory.TRANSIENT,
            message="Error",
            retriable=True,
        )

        # Run multiple times and check for variation
        delays = [strategy.analyze([record]).delay_seconds for _ in range(10)]

        # EXECUTION_UNKNOWN has 10s base, with 25% jitter: 10-12.5s range
        # All delays should be >= ErrorCode-specific delay
        assert all(d >= 10.0 for d in delays)
        assert all(d <= 12.5 for d in delays)
        # With jitter, should have some variation
        # (Though randomness could theoretically give all same values)

    def test_confidence_decreases_with_attempts(self) -> None:
        """Test that confidence decreases as attempts increase."""
        strategy = AdaptiveRetryStrategy()

        # Few errors = higher confidence
        base_time = time.monotonic()
        few_errors = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.EXECUTION_UNKNOWN,
                category=ErrorCategory.TRANSIENT,
                message="Error",
                retriable=True,
                monotonic_time=base_time + i * 100,
            )
            for i in range(2)
        ]

        many_errors = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.EXECUTION_UNKNOWN,
                category=ErrorCategory.TRANSIENT,
                message=f"Error {i}",
                retriable=True,
                monotonic_time=base_time + i * 100,
            )
            for i in range(6)
        ]

        rec_few = strategy.analyze(few_errors)
        rec_many = strategy.analyze(many_errors)

        assert rec_few.confidence > rec_many.confidence

    def test_rate_limit_uses_suggested_wait(self) -> None:
        """Test that rate limits use the suggested wait time."""
        strategy = AdaptiveRetryStrategy()

        record = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.RATE_LIMIT_API,
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limited",
            retriable=True,
            suggested_wait=1800.0,  # 30 minutes
        )

        rec = strategy.analyze([record])

        # Should use suggested_wait with small buffer
        assert rec.delay_seconds >= 1800.0
        assert rec.delay_seconds <= 1800.0 * 1.15  # 10% buffer + some margin



class TestIntegration:
    """Integration tests for AdaptiveRetryStrategy."""

    def test_full_retry_scenario(self) -> None:
        """Test a realistic retry scenario with multiple error types."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                base_delay=5.0,
                max_delay=300.0,
                rapid_failure_threshold=3,
                rapid_failure_window=30.0,
            )
        )

        # Simulate: network error -> timeout -> network error
        base_time = time.monotonic()
        errors = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.NETWORK_CONNECTION_FAILED,
                category=ErrorCategory.NETWORK,
                message="Connection refused",
                retriable=True,
                suggested_wait=30.0,
                sheet_num=1,
                attempt_num=1,
                monotonic_time=base_time,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.EXECUTION_TIMEOUT,
                category=ErrorCategory.TIMEOUT,
                message="Command timed out",
                retriable=True,
                suggested_wait=60.0,
                sheet_num=1,
                attempt_num=2,
                monotonic_time=base_time + 35,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.NETWORK_CONNECTION_FAILED,
                category=ErrorCategory.NETWORK,
                message="Connection refused",
                retriable=True,
                suggested_wait=30.0,
                sheet_num=1,
                attempt_num=3,
                monotonic_time=base_time + 100,
            ),
        ]

        rec = strategy.analyze(errors)

        # Should still retry with some pattern detected
        assert rec.should_retry is True
        assert rec.confidence > 0.3  # Above min threshold

    def test_abort_on_persistent_auth_failure(self) -> None:
        """Test that persistent auth failures are not retried."""
        strategy = AdaptiveRetryStrategy()

        record = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.BACKEND_AUTH,
            category=ErrorCategory.AUTH,
            message="Invalid API key",
            retriable=False,
        )

        rec = strategy.analyze([record])

        assert rec.should_retry is False
        assert "not retriable" in rec.reason.lower()

    def test_classify_and_analyze_flow(self) -> None:
        """Test the full flow from ClassifiedError to recommendation."""
        strategy = AdaptiveRetryStrategy()

        # Create a ClassifiedError like ErrorClassifier would
        classified = ClassifiedError(
            category=ErrorCategory.NETWORK,
            message="DNS resolution failed",
            error_code=ErrorCode.NETWORK_DNS_ERROR,
            exit_code=1,
            retriable=True,
            suggested_wait_seconds=30.0,
        )

        # Convert to ErrorRecord
        record = ErrorRecord.from_classified_error(
            error=classified,
            sheet_num=2,
            attempt_num=1,
        )

        # Get recommendation
        rec = strategy.analyze([record])

        assert rec.should_retry is True
        assert rec.delay_seconds >= 10.0  # At least base delay



class TestRetryBehavior:
    """Tests for RetryBehavior and ErrorCode.get_retry_behavior()."""

    def test_retry_behavior_is_named_tuple(self) -> None:
        """Test that RetryBehavior is a proper NamedTuple."""
        behavior = RetryBehavior(
            delay_seconds=60.0,
            is_retriable=True,
            reason="Test behavior",
        )

        assert behavior.delay_seconds == 60.0
        assert behavior.is_retriable is True
        assert behavior.reason == "Test behavior"
        # NamedTuple supports indexing
        assert behavior[0] == 60.0
        assert behavior[1] is True
        assert behavior[2] == "Test behavior"

    def test_rate_limit_api_has_long_delay(self) -> None:
        """Test that API rate limits have long delay (1 hour)."""
        behavior = ErrorCode.RATE_LIMIT_API.get_retry_behavior()

        assert behavior.delay_seconds == 3600.0  # 1 hour
        assert behavior.is_retriable is True
        assert "quota" in behavior.reason.lower() or "rate" in behavior.reason.lower()

    def test_rate_limit_cli_has_shorter_delay(self) -> None:
        """Test that CLI rate limits have shorter delay than API."""
        api_behavior = ErrorCode.RATE_LIMIT_API.get_retry_behavior()
        cli_behavior = ErrorCode.RATE_LIMIT_CLI.get_retry_behavior()

        assert cli_behavior.delay_seconds < api_behavior.delay_seconds
        assert cli_behavior.delay_seconds == 900.0  # 15 minutes
        assert cli_behavior.is_retriable is True

    def test_execution_timeout_is_retriable(self) -> None:
        """Test that execution timeout is retriable with moderate delay."""
        behavior = ErrorCode.EXECUTION_TIMEOUT.get_retry_behavior()

        assert behavior.is_retriable is True
        assert behavior.delay_seconds == 60.0
        assert "timeout" in behavior.reason.lower()

    def test_execution_crashed_not_retriable(self) -> None:
        """Test that crashes are not retriable."""
        behavior = ErrorCode.EXECUTION_CRASHED.get_retry_behavior()

        assert behavior.is_retriable is False
        assert behavior.delay_seconds == 0.0
        assert "crash" in behavior.reason.lower()

    def test_execution_oom_not_retriable(self) -> None:
        """Test that OOM errors are not retriable."""
        behavior = ErrorCode.EXECUTION_OOM.get_retry_behavior()

        assert behavior.is_retriable is False
        assert "memory" in behavior.reason.lower() or "recur" in behavior.reason.lower()

    @pytest.mark.parametrize("code", [
        ErrorCode.CONFIG_INVALID,
        ErrorCode.CONFIG_MISSING_FIELD,
        ErrorCode.CONFIG_PATH_NOT_FOUND,
        ErrorCode.CONFIG_PARSE_ERROR,
    ])
    def test_config_errors_not_retriable(self, code: ErrorCode) -> None:
        """Test that configuration errors are not retriable."""
        behavior = code.get_retry_behavior()
        assert behavior.is_retriable is False, f"{code} should not be retriable"
        assert behavior.delay_seconds == 0.0, f"{code} should have 0 delay"
        assert "user" in behavior.reason.lower() or "fix" in behavior.reason.lower()

    def test_backend_auth_not_retriable(self) -> None:
        """Test that auth failures are not retriable."""
        behavior = ErrorCode.BACKEND_AUTH.get_retry_behavior()

        assert behavior.is_retriable is False
        assert "auth" in behavior.reason.lower() or "credential" in behavior.reason.lower()

    @pytest.mark.parametrize("code", [
        ErrorCode.NETWORK_CONNECTION_FAILED,
        ErrorCode.NETWORK_DNS_ERROR,
        ErrorCode.NETWORK_SSL_ERROR,
        ErrorCode.NETWORK_TIMEOUT,
    ])
    def test_network_errors_retriable_with_moderate_delay(self, code: ErrorCode) -> None:
        """Test that network errors are retriable with moderate delay."""
        behavior = code.get_retry_behavior()
        assert behavior.is_retriable is True, f"{code} should be retriable"
        assert 30.0 <= behavior.delay_seconds <= 60.0, \
            f"{code} should have 30-60s delay, got {behavior.delay_seconds}"

    @pytest.mark.parametrize("code", [
        ErrorCode.VALIDATION_FILE_MISSING,
        ErrorCode.VALIDATION_CONTENT_MISMATCH,
        ErrorCode.VALIDATION_GENERIC,
    ])
    def test_validation_errors_retriable_with_short_delay(self, code: ErrorCode) -> None:
        """Test that validation errors are retriable with short delay."""
        behavior = code.get_retry_behavior()
        assert behavior.is_retriable is True, f"{code} should be retriable"
        assert behavior.delay_seconds <= 10.0, \
            f"{code} should have short delay, got {behavior.delay_seconds}"

    def test_all_error_codes_have_behavior(self) -> None:
        """Test that all ErrorCodes return a valid RetryBehavior."""
        for code in ErrorCode:
            behavior = code.get_retry_behavior()
            assert isinstance(behavior, RetryBehavior)
            assert isinstance(behavior.delay_seconds, (int, float))
            assert isinstance(behavior.is_retriable, bool)
            assert isinstance(behavior.reason, str)
            assert len(behavior.reason) > 0



class TestErrorCodeSpecificRetry:
    """Tests for ErrorCode-specific retry behavior in AdaptiveRetryStrategy."""

    def test_strategy_uses_error_code_delay(self) -> None:
        """Test that strategy uses ErrorCode-specific delay for standard retry."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                base_delay=10.0,
                jitter_factor=0.0,  # No jitter for predictable testing
            )
        )

        # Network timeout has specific delay of 60s
        record = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.NETWORK_TIMEOUT,
            category=ErrorCategory.NETWORK,
            message="Network timeout",
            retriable=True,
            # No suggested_wait - should use ErrorCode-specific
        )

        rec = strategy.analyze([record])

        assert rec.should_retry is True
        # Should use ErrorCode's 60s, not base_delay's 10s
        assert rec.delay_seconds >= 60.0
        assert rec.strategy_used == "error_code_specific"

    def test_strategy_detects_non_retriable_error_code(self) -> None:
        """Test that strategy respects ErrorCode.is_retriable when no pattern."""
        strategy = AdaptiveRetryStrategy()

        # OOM is not retriable per ErrorCode
        record = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.EXECUTION_OOM,
            category=ErrorCategory.FATAL,  # Category says fatal
            message="Out of memory",
            retriable=True,  # Record says retriable (but ErrorCode overrides)
        )

        rec = strategy.analyze([record])

        # ErrorCode.EXECUTION_OOM.get_retry_behavior() says not retriable
        assert rec.should_retry is False
        assert "E005" in rec.reason or "not retriable" in rec.reason.lower()

    def test_different_error_codes_different_delays(self) -> None:
        """Test that different ErrorCodes produce different delays."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0)
        )

        # Rate limit API: 3600s
        rate_limit_rec = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.RATE_LIMIT_API,
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limited",
            retriable=True,
        )

        # Validation missing: 5s
        validation_rec = ErrorRecord(
            timestamp=datetime.now(UTC),
            error_code=ErrorCode.VALIDATION_FILE_MISSING,
            category=ErrorCategory.VALIDATION,
            message="File missing",
            retriable=True,
        )

        rate_result = strategy.analyze([rate_limit_rec])
        val_result = strategy.analyze([validation_rec])

        # Rate limit uses rate_limit_wait strategy (pattern-based)
        assert rate_result.delay_seconds >= 3600.0

        # Validation uses ErrorCode-specific (5s base)
        assert val_result.delay_seconds < 100.0  # Much shorter
        assert val_result.delay_seconds >= 5.0

    def test_error_code_delay_scales_with_attempts(self) -> None:
        """Test that ErrorCode delay scales mildly with attempt count."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                base_delay=10.0,
                jitter_factor=0.0,
                # Increase threshold to prevent repeated_error pattern
                repeated_error_threshold=5,
                repeated_error_strategy_change_threshold=6,
            )
        )

        base_time = time.monotonic() - 200

        # Backend connection error: 30s base
        # First attempt
        single_error = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.BACKEND_CONNECTION,
                category=ErrorCategory.NETWORK,
                message="Connection failed",
                retriable=True,
                monotonic_time=base_time,
            )
        ]

        # Third attempt (3 errors spread out) - use different error codes to avoid
        # repeated_error pattern, while testing delay scaling
        three_errors = [
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.NETWORK_CONNECTION_FAILED,
                category=ErrorCategory.NETWORK,
                message="Connection failed 1",
                retriable=True,
                monotonic_time=base_time,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.NETWORK_TIMEOUT,
                category=ErrorCategory.NETWORK,
                message="Timeout 2",
                retriable=True,
                monotonic_time=base_time + 100,
            ),
            ErrorRecord(
                timestamp=datetime.now(UTC),
                error_code=ErrorCode.BACKEND_CONNECTION,
                category=ErrorCategory.NETWORK,
                message="Connection failed 3",
                retriable=True,
                monotonic_time=base_time + 200,
            ),
        ]

        rec1 = strategy.analyze(single_error)
        rec3 = strategy.analyze(three_errors)

        # First attempt uses base ErrorCode delay (30s for BACKEND_CONNECTION)
        assert rec1.delay_seconds == 30.0

        # Third attempt should have higher delay due to attempt count scaling
        # or may trigger cascading_failures pattern (which also increases delay)
        assert rec3.should_retry is True
        # Delay should be at least the ErrorCode-specific base
        assert rec3.delay_seconds >= 30.0



class TestDelayOutcome:
    """Tests for DelayOutcome dataclass."""

    def test_create_delay_outcome(self) -> None:
        """Test creating a delay outcome."""
        outcome = DelayOutcome(
            error_code=ErrorCode.NETWORK_TIMEOUT,
            delay_seconds=30.0,
            succeeded_after=True,
        )

        assert outcome.error_code == ErrorCode.NETWORK_TIMEOUT
        assert outcome.delay_seconds == 30.0
        assert outcome.succeeded_after is True
        assert outcome.timestamp.tzinfo is not None


class TestDelayHistory:
    """Tests for DelayHistory class."""

    def test_record_and_query(self) -> None:
        """Test recording and querying delay outcomes."""
        history = DelayHistory()

        # Record some outcomes
        history.record(
            DelayOutcome(
                error_code=ErrorCode.NETWORK_TIMEOUT,
                delay_seconds=30.0,
                succeeded_after=True,
            )
        )
        history.record(
            DelayOutcome(
                error_code=ErrorCode.NETWORK_TIMEOUT,
                delay_seconds=60.0,
                succeeded_after=True,
            )
        )

        # Query for this error code
        outcomes = history.query_for_error_code(ErrorCode.NETWORK_TIMEOUT)
        assert len(outcomes) == 2

        # Query for different error code
        outcomes = history.query_for_error_code(ErrorCode.EXECUTION_TIMEOUT)
        assert len(outcomes) == 0

    def test_get_average_successful_delay(self) -> None:
        """Test computing average successful delay."""
        history = DelayHistory()

        # Add successful outcomes
        history.record(
            DelayOutcome(
                error_code=ErrorCode.NETWORK_TIMEOUT,
                delay_seconds=20.0,
                succeeded_after=True,
            )
        )
        history.record(
            DelayOutcome(
                error_code=ErrorCode.NETWORK_TIMEOUT,
                delay_seconds=40.0,
                succeeded_after=True,
            )
        )
        # Add a failure (should not be counted)
        history.record(
            DelayOutcome(
                error_code=ErrorCode.NETWORK_TIMEOUT,
                delay_seconds=10.0,
                succeeded_after=False,
            )
        )

        avg = history.get_average_successful_delay(ErrorCode.NETWORK_TIMEOUT)
        assert avg == 30.0  # (20 + 40) / 2

    def test_get_average_returns_none_when_no_successes(self) -> None:
        """Test that average returns None when no successful samples."""
        history = DelayHistory()

        # Only failures
        history.record(
            DelayOutcome(
                error_code=ErrorCode.NETWORK_TIMEOUT,
                delay_seconds=30.0,
                succeeded_after=False,
            )
        )

        avg = history.get_average_successful_delay(ErrorCode.NETWORK_TIMEOUT)
        assert avg is None

    def test_get_sample_count(self) -> None:
        """Test counting samples for an error code."""
        history = DelayHistory()

        assert history.get_sample_count(ErrorCode.NETWORK_TIMEOUT) == 0

        history.record(
            DelayOutcome(
                error_code=ErrorCode.NETWORK_TIMEOUT,
                delay_seconds=30.0,
                succeeded_after=True,
            )
        )
        history.record(
            DelayOutcome(
                error_code=ErrorCode.NETWORK_TIMEOUT,
                delay_seconds=60.0,
                succeeded_after=False,
            )
        )

        assert history.get_sample_count(ErrorCode.NETWORK_TIMEOUT) == 2

    def test_record_none_outcome_raises(self) -> None:
        """Test that recording None outcome raises ValueError."""
        history = DelayHistory()

        with pytest.raises(ValueError, match="outcome cannot be None"):
            history.record(None)  # type: ignore[arg-type]

    def test_record_negative_delay_raises(self) -> None:
        """Test that recording negative delay raises ValueError."""
        history = DelayHistory()

        with pytest.raises(ValueError, match="delay_seconds must be >= 0"):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.NETWORK_TIMEOUT,
                    delay_seconds=-1.0,
                    succeeded_after=True,
                )
            )


class TestDelayLearning:
    """Tests for delay learning in AdaptiveRetryStrategy."""

    def test_no_history_uses_static_delay(self) -> None:
        """Test that without history, static delay is used."""
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0)
        )

        delay, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )

        assert delay == 60.0
        assert strategy_name == "static"

    def test_bootstrap_uses_static_delay(self) -> None:
        """Test that with no successful samples, static delay is used."""
        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0),
            delay_history=history,
        )

        delay, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )

        assert delay == 60.0
        assert strategy_name == "static_bootstrap"

    def test_learned_delay_blending(self) -> None:
        """Test that learned delay is blended with static."""
        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0),
            delay_history=history,
        )

        # Add 10 successful samples with average delay of 20s
        for _ in range(10):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.NETWORK_TIMEOUT,
                    delay_seconds=20.0,
                    succeeded_after=True,
                )
            )

        # With 10 samples, weight = 1.0, so fully learned delay
        # learned = 20.0, static = 60.0
        # blended = 1.0 * 20.0 + 0.0 * 60.0 = 20.0
        delay, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )

        assert delay == 20.0
        assert strategy_name == "learned_blend"

    def test_learned_delay_weight_increases_with_samples(self) -> None:
        """Test that learned delay weight increases with sample count."""
        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0),
            delay_history=history,
        )

        # Add 5 samples (weight = 0.5)
        for _ in range(5):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.NETWORK_TIMEOUT,
                    delay_seconds=20.0,
                    succeeded_after=True,
                )
            )

        # weight = 5/10 = 0.5
        # blended = 0.5 * 20.0 + 0.5 * 60.0 = 10.0 + 30.0 = 40.0
        delay, _ = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )

        assert delay == 40.0

    def test_circuit_breaker_reverts_to_static(self) -> None:
        """Test that circuit breaker reverts to static after failures."""
        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0),
            delay_history=history,
        )

        # Add successful samples to enable learned delays
        for _ in range(10):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.NETWORK_TIMEOUT,
                    delay_seconds=20.0,
                    succeeded_after=True,
                )
            )

        # Verify learned delay is being used
        delay, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )
        assert strategy_name == "learned_blend"
        assert delay == 20.0

        # Record 4 consecutive failures (> 3 triggers circuit breaker)
        for _ in range(4):
            strategy.record_delay_outcome(
                ErrorCode.NETWORK_TIMEOUT,
                delay_used=20.0,
                succeeded=False,
            )

        # Now circuit breaker should be tripped
        delay, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )
        assert strategy_name == "static_circuit_breaker"
        assert delay == 60.0

    def test_circuit_breaker_reset(self) -> None:
        """Test that circuit breaker can be reset."""
        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0),
            delay_history=history,
        )

        # Set up learned delay
        for _ in range(10):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.NETWORK_TIMEOUT,
                    delay_seconds=20.0,
                    succeeded_after=True,
                )
            )

        # Trip circuit breaker
        for _ in range(4):
            strategy.record_delay_outcome(
                ErrorCode.NETWORK_TIMEOUT,
                delay_used=20.0,
                succeeded=False,
            )

        # Verify circuit breaker is tripped
        _, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )
        assert strategy_name == "static_circuit_breaker"

        # Reset circuit breaker
        strategy.reset_circuit_breaker(ErrorCode.NETWORK_TIMEOUT)

        # Should use learned delay again
        _, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )
        assert strategy_name == "learned_blend"

    def test_record_delay_outcome_success_resets_failures(self) -> None:
        """Test that successful outcome resets failure count."""
        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0),
            delay_history=history,
        )

        # Record 3 failures (not quite circuit breaker threshold)
        for _ in range(3):
            strategy.record_delay_outcome(
                ErrorCode.NETWORK_TIMEOUT,
                delay_used=20.0,
                succeeded=False,
            )

        # Record a success (should reset count)
        strategy.record_delay_outcome(
            ErrorCode.NETWORK_TIMEOUT,
            delay_used=20.0,
            succeeded=True,
        )

        # Record 3 more failures (should not trip circuit breaker)
        for _ in range(3):
            strategy.record_delay_outcome(
                ErrorCode.NETWORK_TIMEOUT,
                delay_used=20.0,
                succeeded=False,
            )

        # Circuit breaker should NOT be tripped
        assert strategy._learned_delay_failures.get(ErrorCode.NETWORK_TIMEOUT, 0) == 3
        assert strategy._use_learned_delay.get(ErrorCode.NETWORK_TIMEOUT, True) is True


class TestBlendHistoricalDelayGlobalFallthrough:
    """Tests for blend_historical_delay() global store priority fallthrough (B2-07).

    Verifies the 3-level priority chain:
    1. In-memory delay history (highest priority)
    2. Global learning store (cross-workspace)
    3. Static delay (fallback)
    """

    def test_global_store_used_when_no_local_history(self) -> None:
        """Test that global store is queried when in-memory history has no samples."""
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        mock_store.get_learned_wait_time_with_fallback.return_value = (
            25.0, 0.85, "global_learned"
        )

        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0),
            delay_history=history,
            global_learning_store=mock_store,
        )

        delay, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )

        assert delay == 25.0
        assert strategy_name == "global_learned"
        mock_store.get_learned_wait_time_with_fallback.assert_called_once()

    def test_local_history_takes_priority_over_global(self) -> None:
        """Test that in-memory history wins over global store."""
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        mock_store.get_learned_wait_time_with_fallback.return_value = (
            25.0, 0.85, "global_learned"
        )

        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0),
            delay_history=history,
            global_learning_store=mock_store,
        )

        # Add local history — should take priority
        for _ in range(10):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.NETWORK_TIMEOUT,
                    delay_seconds=15.0,
                    succeeded_after=True,
                )
            )

        delay, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )

        assert delay == 15.0
        assert strategy_name == "learned_blend"
        # Global store should NOT have been called
        mock_store.get_learned_wait_time_with_fallback.assert_not_called()

    def test_global_store_static_fallback_ignored(self) -> None:
        """Test that global store's 'static_fallback' result is ignored."""
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        mock_store.get_learned_wait_time_with_fallback.return_value = (
            60.0, 0.0, "static_fallback"
        )

        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0),
            delay_history=history,
            global_learning_store=mock_store,
        )

        delay, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )

        # Should fall through to static_bootstrap since history exists but no samples
        assert delay == 60.0
        assert strategy_name == "static_bootstrap"

    def test_global_store_error_falls_through_gracefully(self) -> None:
        """Test that global store query failure doesn't block retry."""
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        mock_store.get_learned_wait_time_with_fallback.side_effect = RuntimeError("DB error")

        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(jitter_factor=0.0),
            delay_history=history,
            global_learning_store=mock_store,
        )

        delay, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )

        assert delay == 60.0
        assert strategy_name == "static_bootstrap"

    def test_global_delay_clamped_to_config_range(self) -> None:
        """Test that global store delay is clamped to [base_delay, max_delay]."""
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        # Return a delay far exceeding max_delay
        mock_store.get_learned_wait_time_with_fallback.return_value = (
            9999.0, 0.9, "global_learned"
        )

        history = DelayHistory()
        strategy = AdaptiveRetryStrategy(
            config=RetryStrategyConfig(
                jitter_factor=0.0,
                max_delay=300.0,
            ),
            delay_history=history,
            global_learning_store=mock_store,
        )

        delay, strategy_name = strategy.blend_historical_delay(
            ErrorCode.NETWORK_TIMEOUT,
            static_delay=60.0,
        )

        assert delay == 300.0  # Clamped to max_delay
        assert strategy_name == "global_learned"


class TestDelayHistoryPruning:
    """Tests for DelayHistory pruning logic (B2-08).

    The pruning triggers when history exceeds max_history * 10, keeping
    the most recent max_history entries per error code, then re-sorting
    by timestamp for chronological order.
    """

    def test_no_pruning_below_threshold(self) -> None:
        """Test that pruning doesn't happen when under threshold."""
        history = DelayHistory(max_history=10)

        # Add 50 entries (under threshold of 10 * 10 = 100)
        for i in range(50):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.NETWORK_TIMEOUT,
                    delay_seconds=float(i),
                    succeeded_after=True,
                )
            )

        # All 50 should be present
        outcomes = history.query_for_error_code(ErrorCode.NETWORK_TIMEOUT)
        assert len(outcomes) == 50

    def test_pruning_triggers_at_threshold(self) -> None:
        """Test that pruning triggers when threshold is exceeded."""
        history = DelayHistory(max_history=5)

        # Add 51 entries (just above threshold of 5 * 10 = 50)
        for i in range(51):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.NETWORK_TIMEOUT,
                    delay_seconds=float(i),
                    succeeded_after=True,
                )
            )

        # Should be pruned to max_history (5) per error code
        outcomes = history.query_for_error_code(ErrorCode.NETWORK_TIMEOUT)
        assert len(outcomes) == 5

        # Should keep the most recent entries (highest delays)
        delays = [o.delay_seconds for o in outcomes]
        assert delays == [46.0, 47.0, 48.0, 49.0, 50.0]

    def test_pruning_preserves_per_error_code_entries(self) -> None:
        """Test that pruning keeps max_history entries per error code.

        Pruning triggers when len > max_history * 10, keeping the last
        max_history entries per error code. After pruning, entries added
        before the next prune cycle are retained.
        """
        history = DelayHistory(max_history=3)

        # Add 20 NETWORK_TIMEOUT entries, then 11 RATE_LIMIT_API entries
        # to trigger pruning at entry 31 (> 30)
        for i in range(20):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.NETWORK_TIMEOUT,
                    delay_seconds=float(i),
                    succeeded_after=True,
                )
            )
        for i in range(11):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.RATE_LIMIT_API,
                    delay_seconds=float(100 + i),
                    succeeded_after=True,
                )
            )

        # After prune at 31: NETWORK_TIMEOUT=3, RATE_LIMIT_API=3 (total=6)
        timeout_outcomes = history.query_for_error_code(ErrorCode.NETWORK_TIMEOUT)
        rate_limit_outcomes = history.query_for_error_code(ErrorCode.RATE_LIMIT_API)

        assert len(timeout_outcomes) == 3
        assert len(rate_limit_outcomes) == 3

    def test_pruning_maintains_chronological_order(self) -> None:
        """Test that pruning restores chronological order in internal history.

        After pruning, the internal _history list should be sorted by
        timestamp regardless of error codes being interleaved.
        """
        history = DelayHistory(max_history=2)

        # Add interleaved entries — 22 entries total > 20 (2 * 10)
        # Prune triggers at the 21st entry, keeping last 2 per code.
        # The 22nd entry is added after pruning without another prune.
        for i in range(11):
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.NETWORK_TIMEOUT,
                    delay_seconds=float(i),
                    succeeded_after=True,
                )
            )
            history.record(
                DelayOutcome(
                    error_code=ErrorCode.RATE_LIMIT_API,
                    delay_seconds=float(100 + i),
                    succeeded_after=True,
                )
            )

        # After pruning, internal history should be in chronological order
        timestamps = [o.timestamp for o in history._history]
        assert timestamps == sorted(timestamps)

        # Total entries should be far fewer than original 22
        assert len(history._history) < 22
