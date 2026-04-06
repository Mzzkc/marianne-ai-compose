"""Tests for structured error codes (Task 11: Structured Error Codes).

This module tests the ErrorCode enum and its integration with ErrorClassifier.
Tests verify:
- ErrorCode enum values and properties
- Error code assignment in ErrorClassifier.classify()
- Error code assignment in signal classification
- Integration with ClassifiedError dataclass
"""

import signal

import pytest

from marianne.core.errors import (
    ClassifiedError,
    ErrorCategory,
    ErrorClassifier,
    ErrorCode,
)
from tests.helpers import record_error_on_sheet

# ============================================================================
# ErrorCode Enum Tests
# ============================================================================


class TestErrorCodeEnum:
    """Tests for the ErrorCode enum."""

    def test_error_code_values_unique(self) -> None:
        """Test that all error code values are unique."""
        values = [code.value for code in ErrorCode]
        assert len(values) == len(set(values)), "Duplicate error code values found"

    def test_error_code_format(self) -> None:
        """Test that error codes follow E<digit><digit><digit> format."""
        import re

        pattern = re.compile(r"^E\d{3}$")
        for code in ErrorCode:
            assert pattern.match(code.value), f"{code.name} has invalid format: {code.value}"

    def test_execution_codes_start_with_0(self) -> None:
        """Test execution error codes are in E0xx range."""
        execution_codes = [
            ErrorCode.EXECUTION_TIMEOUT,
            ErrorCode.EXECUTION_KILLED,
            ErrorCode.EXECUTION_CRASHED,
            ErrorCode.EXECUTION_INTERRUPTED,
            ErrorCode.EXECUTION_OOM,
            ErrorCode.EXECUTION_UNKNOWN,
        ]
        for code in execution_codes:
            assert code.value.startswith("E0"), f"{code.name} should be in E0xx range"

    def test_rate_limit_codes_start_with_1(self) -> None:
        """Test rate limit codes are in E1xx range."""
        rate_limit_codes = [
            ErrorCode.RATE_LIMIT_API,
            ErrorCode.RATE_LIMIT_CLI,
            ErrorCode.CAPACITY_EXCEEDED,
            ErrorCode.QUOTA_EXHAUSTED,
        ]
        for code in rate_limit_codes:
            assert code.value.startswith("E1"), f"{code.name} should be in E1xx range"

    def test_validation_codes_start_with_2(self) -> None:
        """Test validation codes are in E2xx range."""
        validation_codes = [
            ErrorCode.VALIDATION_FILE_MISSING,
            ErrorCode.VALIDATION_CONTENT_MISMATCH,
            ErrorCode.VALIDATION_COMMAND_FAILED,
            ErrorCode.VALIDATION_TIMEOUT,
            ErrorCode.VALIDATION_GENERIC,
        ]
        for code in validation_codes:
            assert code.value.startswith("E2"), f"{code.name} should be in E2xx range"

    def test_config_codes_start_with_3(self) -> None:
        """Test configuration codes are in E3xx range."""
        config_codes = [
            ErrorCode.CONFIG_INVALID,
            ErrorCode.CONFIG_MISSING_FIELD,
            ErrorCode.CONFIG_PATH_NOT_FOUND,
            ErrorCode.CONFIG_PARSE_ERROR,
        ]
        for code in config_codes:
            assert code.value.startswith("E3"), f"{code.name} should be in E3xx range"

    def test_state_codes_start_with_4(self) -> None:
        """Test state codes are in E4xx range."""
        state_codes = [
            ErrorCode.STATE_CORRUPTION,
            ErrorCode.STATE_LOAD_FAILED,
            ErrorCode.STATE_SAVE_FAILED,
            ErrorCode.STATE_VERSION_MISMATCH,
        ]
        for code in state_codes:
            assert code.value.startswith("E4"), f"{code.name} should be in E4xx range"

    def test_backend_codes_start_with_5(self) -> None:
        """Test backend codes are in E5xx range."""
        backend_codes = [
            ErrorCode.BACKEND_CONNECTION,
            ErrorCode.BACKEND_AUTH,
            ErrorCode.BACKEND_RESPONSE,
            ErrorCode.BACKEND_TIMEOUT,
            ErrorCode.BACKEND_NOT_FOUND,
        ]
        for code in backend_codes:
            assert code.value.startswith("E5"), f"{code.name} should be in E5xx range"

    def test_preflight_codes_start_with_6(self) -> None:
        """Test preflight codes are in E6xx range."""
        preflight_codes = [
            ErrorCode.PREFLIGHT_PATH_MISSING,
            ErrorCode.PREFLIGHT_PROMPT_TOO_LARGE,
            ErrorCode.PREFLIGHT_WORKING_DIR_INVALID,
            ErrorCode.PREFLIGHT_VALIDATION_SETUP,
        ]
        for code in preflight_codes:
            assert code.value.startswith("E6"), f"{code.name} should be in E6xx range"

    def test_network_codes_start_with_9(self) -> None:
        """Test network/transient codes are in E9xx range."""
        network_codes = [
            ErrorCode.NETWORK_CONNECTION_FAILED,
            ErrorCode.NETWORK_DNS_ERROR,
            ErrorCode.NETWORK_SSL_ERROR,
            ErrorCode.NETWORK_TIMEOUT,
        ]
        for code in network_codes:
            assert code.value.startswith("E9"), f"{code.name} should be in E9xx range"


class TestErrorCodeCategory:
    """Tests for ErrorCode.category property."""

    def test_execution_category(self) -> None:
        """Test execution codes return 'execution' category."""
        assert ErrorCode.EXECUTION_TIMEOUT.category == "execution"
        assert ErrorCode.EXECUTION_KILLED.category == "execution"
        assert ErrorCode.EXECUTION_CRASHED.category == "execution"

    def test_rate_limit_category(self) -> None:
        """Test rate limit codes return 'rate_limit' category."""
        assert ErrorCode.RATE_LIMIT_API.category == "rate_limit"
        assert ErrorCode.CAPACITY_EXCEEDED.category == "rate_limit"

    def test_validation_category(self) -> None:
        """Test validation codes return 'validation' category."""
        assert ErrorCode.VALIDATION_FILE_MISSING.category == "validation"
        assert ErrorCode.VALIDATION_GENERIC.category == "validation"

    def test_configuration_category(self) -> None:
        """Test config codes return 'configuration' category."""
        assert ErrorCode.CONFIG_INVALID.category == "configuration"
        assert ErrorCode.CONFIG_PARSE_ERROR.category == "configuration"

    def test_state_category(self) -> None:
        """Test state codes return 'state' category."""
        assert ErrorCode.STATE_CORRUPTION.category == "state"
        assert ErrorCode.STATE_LOAD_FAILED.category == "state"

    def test_backend_category(self) -> None:
        """Test backend codes return 'backend' category."""
        assert ErrorCode.BACKEND_AUTH.category == "backend"
        assert ErrorCode.BACKEND_NOT_FOUND.category == "backend"

    def test_preflight_category(self) -> None:
        """Test preflight codes return 'preflight' category."""
        assert ErrorCode.PREFLIGHT_PATH_MISSING.category == "preflight"
        assert ErrorCode.PREFLIGHT_PROMPT_TOO_LARGE.category == "preflight"

    def test_transient_category(self) -> None:
        """Test network codes return 'transient' category."""
        assert ErrorCode.NETWORK_CONNECTION_FAILED.category == "transient"
        assert ErrorCode.NETWORK_DNS_ERROR.category == "transient"


class TestErrorCodeRetriable:
    """Tests for ErrorCode.is_retriable property."""

    def test_execution_timeout_retriable(self) -> None:
        """Test EXECUTION_TIMEOUT is retriable."""
        assert ErrorCode.EXECUTION_TIMEOUT.is_retriable is True

    def test_execution_killed_retriable(self) -> None:
        """Test EXECUTION_KILLED is retriable."""
        assert ErrorCode.EXECUTION_KILLED.is_retriable is True

    def test_execution_crashed_not_retriable(self) -> None:
        """Test EXECUTION_CRASHED is not retriable."""
        assert ErrorCode.EXECUTION_CRASHED.is_retriable is False

    def test_execution_interrupted_not_retriable(self) -> None:
        """Test EXECUTION_INTERRUPTED is not retriable."""
        assert ErrorCode.EXECUTION_INTERRUPTED.is_retriable is False

    def test_execution_oom_not_retriable(self) -> None:
        """Test EXECUTION_OOM is not retriable."""
        assert ErrorCode.EXECUTION_OOM.is_retriable is False

    def test_rate_limit_retriable(self) -> None:
        """Test rate limit codes are retriable."""
        assert ErrorCode.RATE_LIMIT_API.is_retriable is True
        assert ErrorCode.CAPACITY_EXCEEDED.is_retriable is True

    def test_auth_not_retriable(self) -> None:
        """Test BACKEND_AUTH is not retriable."""
        assert ErrorCode.BACKEND_AUTH.is_retriable is False

    def test_config_errors_not_retriable(self) -> None:
        """Test config errors are not retriable."""
        assert ErrorCode.CONFIG_INVALID.is_retriable is False
        assert ErrorCode.CONFIG_MISSING_FIELD.is_retriable is False
        assert ErrorCode.CONFIG_PARSE_ERROR.is_retriable is False

    def test_state_corruption_not_retriable(self) -> None:
        """Test STATE_CORRUPTION is not retriable."""
        assert ErrorCode.STATE_CORRUPTION.is_retriable is False

    def test_network_errors_retriable(self) -> None:
        """Test network errors are retriable."""
        assert ErrorCode.NETWORK_CONNECTION_FAILED.is_retriable is True
        assert ErrorCode.NETWORK_DNS_ERROR.is_retriable is True
        assert ErrorCode.NETWORK_SSL_ERROR.is_retriable is True


# ============================================================================
# ClassifiedError Error Code Tests
# ============================================================================


class TestClassifiedErrorWithCode:
    """Tests for ClassifiedError with error_code field."""

    def test_default_error_code(self) -> None:
        """Test ClassifiedError defaults to UNKNOWN error code."""
        error = ClassifiedError(
            category=ErrorCategory.FATAL,
            message="Test error",
        )
        assert error.error_code == ErrorCode.UNKNOWN
        assert error.code == "E999"

    def test_explicit_error_code(self) -> None:
        """Test ClassifiedError with explicit error code."""
        error = ClassifiedError(
            category=ErrorCategory.TIMEOUT,
            message="Timed out",
            error_code=ErrorCode.EXECUTION_TIMEOUT,
        )
        assert error.error_code == ErrorCode.EXECUTION_TIMEOUT
        assert error.code == "E001"

    def test_code_property(self) -> None:
        """Test the code property returns string value."""
        error = ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            message="Rate limit",
            error_code=ErrorCode.RATE_LIMIT_API,
        )
        assert error.code == "E101"
        assert isinstance(error.code, str)


# ============================================================================
# ErrorClassifier Error Code Assignment Tests
# ============================================================================


class TestErrorClassifierErrorCodes:
    """Tests for ErrorClassifier error code assignment."""

    @pytest.fixture
    def classifier(self) -> ErrorClassifier:
        """Create default classifier."""
        return ErrorClassifier()

    # Timeout classifications
    def test_timeout_exit_reason(self, classifier: ErrorClassifier) -> None:
        """Test timeout exit_reason gets E001."""
        result = classifier.classify(exit_reason="timeout")
        assert result.error_code == ErrorCode.EXECUTION_TIMEOUT
        assert result.code == "E001"

    def test_timeout_exit_code_124(self, classifier: ErrorClassifier) -> None:
        """Test exit code 124 (timeout command) gets E001."""
        result = classifier.classify(exit_code=124)
        assert result.error_code == ErrorCode.EXECUTION_TIMEOUT

    # Rate limit classifications
    def test_rate_limit_api(self, classifier: ErrorClassifier) -> None:
        """Test rate limit patterns get E101."""
        result = classifier.classify(stderr="Error: rate limit exceeded", exit_code=1)
        assert result.error_code == ErrorCode.RATE_LIMIT_API
        assert result.code == "E101"

    def test_rate_limit_429(self, classifier: ErrorClassifier) -> None:
        """Test 429 status gets rate limit code."""
        result = classifier.classify(stderr="HTTP 429 Too Many Requests")
        assert result.error_code == ErrorCode.RATE_LIMIT_API

    def test_capacity_exceeded(self, classifier: ErrorClassifier) -> None:
        """Test capacity/overload patterns get E103."""
        result = classifier.classify(stderr="Service overloaded, try again later")
        assert result.error_code == ErrorCode.CAPACITY_EXCEEDED
        assert result.code == "E103"

    # Auth classifications
    def test_auth_failure(self, classifier: ErrorClassifier) -> None:
        """Test auth patterns get E502."""
        result = classifier.classify(stderr="401 Unauthorized")
        assert result.error_code == ErrorCode.BACKEND_AUTH
        assert result.code == "E502"

    def test_permission_denied(self, classifier: ErrorClassifier) -> None:
        """Test permission denied gets auth code."""
        result = classifier.classify(stderr="Permission denied for this resource")
        assert result.error_code == ErrorCode.BACKEND_AUTH

    # Network classifications
    def test_connection_refused(self, classifier: ErrorClassifier) -> None:
        """Test connection refused gets E901."""
        result = classifier.classify(stderr="ECONNREFUSED: connection refused")
        assert result.error_code == ErrorCode.NETWORK_CONNECTION_FAILED
        assert result.code == "E901"

    def test_dns_error(self, classifier: ErrorClassifier) -> None:
        """Test DNS resolution failure gets E902."""
        result = classifier.classify(stderr="getaddrinfo ENOTFOUND api.example.com")
        assert result.error_code == ErrorCode.NETWORK_DNS_ERROR
        assert result.code == "E902"

    def test_ssl_error(self, classifier: ErrorClassifier) -> None:
        """Test SSL errors get E903."""
        result = classifier.classify(stderr="SSL_ERROR_HANDSHAKE_FAILURE")
        assert result.error_code == ErrorCode.NETWORK_SSL_ERROR
        assert result.code == "E903"

    def test_certificate_error(self, classifier: ErrorClassifier) -> None:
        """Test certificate errors get SSL code."""
        result = classifier.classify(stderr="certificate verify failed")
        assert result.error_code == ErrorCode.NETWORK_SSL_ERROR

    # Exit code classifications
    def test_exit_code_0_validation(self, classifier: ErrorClassifier) -> None:
        """Test exit code 0 gets validation code."""
        result = classifier.classify(exit_code=0)
        assert result.error_code == ErrorCode.VALIDATION_GENERIC
        assert result.code == "E209"

    def test_exit_code_1_unknown(self, classifier: ErrorClassifier) -> None:
        """Test exit code 1 gets E009 (execution unknown)."""
        result = classifier.classify(exit_code=1)
        assert result.error_code == ErrorCode.EXECUTION_UNKNOWN
        assert result.code == "E009"

    def test_exit_code_127_not_found(self, classifier: ErrorClassifier) -> None:
        """Test exit code 127 gets backend not found code."""
        result = classifier.classify(exit_code=127)
        assert result.error_code == ErrorCode.BACKEND_NOT_FOUND
        assert result.code == "E505"

    def test_unknown_exit_code(self, classifier: ErrorClassifier) -> None:
        """Test unknown exit code gets E999."""
        result = classifier.classify(exit_code=42)
        assert result.error_code == ErrorCode.UNKNOWN
        assert result.code == "E999"


class TestErrorClassifierSignalCodes:
    """Tests for signal-based error code assignment."""

    @pytest.fixture
    def classifier(self) -> ErrorClassifier:
        """Create default classifier."""
        return ErrorClassifier()

    def test_timeout_signal(self, classifier: ErrorClassifier) -> None:
        """Test timeout with signal gets E001."""
        result = classifier.classify(
            exit_signal=signal.SIGKILL,
            exit_reason="timeout",
        )
        assert result.error_code == ErrorCode.EXECUTION_TIMEOUT

    def test_fatal_signal_segv(self, classifier: ErrorClassifier) -> None:
        """Test SIGSEGV gets E003 (crashed)."""
        result = classifier.classify(
            exit_signal=signal.SIGSEGV,
            exit_reason="killed",
        )
        assert result.error_code == ErrorCode.EXECUTION_CRASHED
        assert result.code == "E003"

    def test_fatal_signal_abrt(self, classifier: ErrorClassifier) -> None:
        """Test SIGABRT gets crashed code."""
        result = classifier.classify(
            exit_signal=signal.SIGABRT,
            exit_reason="killed",
        )
        assert result.error_code == ErrorCode.EXECUTION_CRASHED

    def test_sigint_interrupted(self, classifier: ErrorClassifier) -> None:
        """Test SIGINT gets E004 (interrupted)."""
        result = classifier.classify(
            exit_signal=signal.SIGINT,
            exit_reason="killed",
        )
        assert result.error_code == ErrorCode.EXECUTION_INTERRUPTED
        assert result.code == "E004"

    def test_sigkill_oom(self, classifier: ErrorClassifier) -> None:
        """Test SIGKILL with OOM indicator gets E005."""
        result = classifier.classify(
            stderr="oom-killer invoked",
            exit_signal=signal.SIGKILL,
            exit_reason="killed",
        )
        assert result.error_code == ErrorCode.EXECUTION_OOM
        assert result.code == "E005"

    def test_sigkill_without_oom(self, classifier: ErrorClassifier) -> None:
        """Test SIGKILL without OOM gets E002 (killed)."""
        result = classifier.classify(
            exit_signal=signal.SIGKILL,
            exit_reason="killed",
        )
        assert result.error_code == ErrorCode.EXECUTION_KILLED
        assert result.code == "E002"

    def test_sigterm_killed(self, classifier: ErrorClassifier) -> None:
        """Test SIGTERM gets E002."""
        result = classifier.classify(
            exit_signal=signal.SIGTERM,
            exit_reason="killed",
        )
        assert result.error_code == ErrorCode.EXECUTION_KILLED

    def test_sighup_killed(self, classifier: ErrorClassifier) -> None:
        """Test SIGHUP gets killed code."""
        result = classifier.classify(
            exit_signal=signal.SIGHUP,
            exit_reason="killed",
        )
        assert result.error_code == ErrorCode.EXECUTION_KILLED

    def test_unknown_signal(self, classifier: ErrorClassifier) -> None:
        """Test unknown signal gets killed code."""
        result = classifier.classify(
            exit_signal=64,  # SIGRTMAX
            exit_reason="killed",
        )
        assert result.error_code == ErrorCode.EXECUTION_KILLED


class TestErrorCodeBackwardsCompatibility:
    """Tests for backwards compatibility with existing code."""

    @pytest.fixture
    def classifier(self) -> ErrorClassifier:
        """Create default classifier."""
        return ErrorClassifier()

    def test_category_still_works(self, classifier: ErrorClassifier) -> None:
        """Test that category classification still works."""
        result = classifier.classify(stderr="rate limit exceeded")
        assert result.category == ErrorCategory.RATE_LIMIT
        assert result.error_code is not None

    def test_retriable_still_works(self, classifier: ErrorClassifier) -> None:
        """Test that retriable flag still works."""
        result = classifier.classify(stderr="401 Unauthorized")
        assert result.retriable is False
        assert result.error_code == ErrorCode.BACKEND_AUTH

    def test_signal_name_still_works(self, classifier: ErrorClassifier) -> None:
        """Test that signal_name property still works."""
        result = classifier.classify(
            exit_signal=signal.SIGTERM,
            exit_reason="killed",
        )
        assert result.signal_name == "SIGTERM"
        assert result.error_code == ErrorCode.EXECUTION_KILLED

    def test_should_retry_still_works(self, classifier: ErrorClassifier) -> None:
        """Test that should_retry property still works."""
        result = classifier.classify(exit_code=1)
        assert result.should_retry is True
        assert result.error_code == ErrorCode.EXECUTION_UNKNOWN


# ============================================================================
# Integration Tests
# ============================================================================


class TestErrorCodeIntegration:
    """Integration tests for error codes with rest of system."""

    def test_error_code_in_logging(self) -> None:
        """Test that error codes appear in structured logs."""
        # This is implicitly tested by the classifier - logging includes error_code
        classifier = ErrorClassifier()
        result = classifier.classify(exit_code=124)
        # The classifier logs error_code=result.error_code.value
        assert result.error_code.value == "E001"

    def test_error_record_uses_error_code(self) -> None:
        """Test that ErrorRecord can store error codes."""
        from marianne.core.checkpoint import SheetState

        state = SheetState(sheet_num=1)
        record_error_on_sheet(
            state,
            error_type="transient",
            error_code=ErrorCode.EXECUTION_TIMEOUT.value,
            error_message="Command timed out",
            attempt=1,
        )

        assert state.error_history[0].error_code == "E001"

    def test_error_codes_cover_all_categories(self) -> None:
        """Test that we have error codes for all major scenarios."""
        classifier = ErrorClassifier()

        # Execution errors
        assert classifier.classify(exit_reason="timeout").error_code.value.startswith("E0")
        assert classifier.classify(
            exit_signal=signal.SIGSEGV, exit_reason="killed"
        ).error_code.value.startswith("E0")

        # Rate limits
        assert classifier.classify(
            stderr="rate limit"
        ).error_code.value.startswith("E1")

        # Validation
        assert classifier.classify(exit_code=0).error_code.value.startswith("E2")

        # Backend errors
        assert classifier.classify(
            stderr="401 unauthorized"
        ).error_code.value.startswith("E5")

        # Network errors
        assert classifier.classify(
            stderr="connection refused"
        ).error_code.value.startswith("E9")


# ============================================================================
# Quota Exhaustion Tests (E104)
# ============================================================================


class TestQuotaExhaustion:
    """Tests for token quota exhaustion handling (E104)."""

    @pytest.fixture
    def classifier(self) -> ErrorClassifier:
        """Create default classifier."""
        return ErrorClassifier()

    def test_quota_exhausted_error_code_exists(self) -> None:
        """Test QUOTA_EXHAUSTED error code exists and is E104."""
        assert ErrorCode.QUOTA_EXHAUSTED.value == "E104"

    def test_quota_exhausted_in_rate_limit_range(self) -> None:
        """Test QUOTA_EXHAUSTED is in E1xx range (rate limit category)."""
        assert ErrorCode.QUOTA_EXHAUSTED.value.startswith("E1")

    def test_quota_exhausted_is_retriable(self) -> None:
        """Test QUOTA_EXHAUSTED is retriable."""
        assert ErrorCode.QUOTA_EXHAUSTED.is_retriable is True

    def test_quota_exhausted_category(self) -> None:
        """Test QUOTA_EXHAUSTED returns rate_limit category."""
        assert ErrorCode.QUOTA_EXHAUSTED.category == "rate_limit"

    # Detection tests
    def test_detect_tokens_exhausted(self, classifier: ErrorClassifier) -> None:
        """Test detection of 'tokens exhausted' message."""
        result = classifier.classify(stdout="Your tokens are exhausted", exit_code=1)
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED

    def test_detect_token_budget_exhausted(self, classifier: ErrorClassifier) -> None:
        """Test detection of 'token budget exhausted' message."""
        result = classifier.classify(stdout="Token budget has been exhausted", exit_code=1)
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED

    def test_detect_usage_will_reset(self, classifier: ErrorClassifier) -> None:
        """Test detection of 'usage will reset' message."""
        result = classifier.classify(stdout="Your usage will reset at 9pm", exit_code=1)
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED

    def test_detect_resets_time_format(self, classifier: ErrorClassifier) -> None:
        """Test detection of 'resets Xpm' message."""
        result = classifier.classify(stdout="limit · resets 9pm", exit_code=1)
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED

    def test_detect_resets_in_hours(self, classifier: ErrorClassifier) -> None:
        """Test detection of 'resets in X hours' message."""
        result = classifier.classify(stdout="Token limit reached. Resets in 3 hours.", exit_code=1)
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED

    def test_detect_daily_token_limit(self, classifier: ErrorClassifier) -> None:
        """Test detection of 'daily token limit' message."""
        result = classifier.classify(stdout="You've hit your daily token limit", exit_code=1)
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED

    def test_detect_no_credits_left(self, classifier: ErrorClassifier) -> None:
        """Test detection of 'no credits left' message."""
        result = classifier.classify(stdout="No credits left for this period", exit_code=1)
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED

    # Non-match tests (should be regular rate limits)
    def test_regular_rate_limit_not_quota(self, classifier: ErrorClassifier) -> None:
        """Test regular rate limit is NOT quota exhaustion."""
        result = classifier.classify(stderr="Rate limit exceeded. Please retry.", exit_code=1)
        assert result.error_code != ErrorCode.QUOTA_EXHAUSTED
        assert result.error_code in (ErrorCode.RATE_LIMIT_API, ErrorCode.CAPACITY_EXCEEDED)

    def test_429_not_quota(self, classifier: ErrorClassifier) -> None:
        """Test 429 error is NOT quota exhaustion."""
        result = classifier.classify(stderr="429 Too Many Requests", exit_code=1)
        assert result.error_code != ErrorCode.QUOTA_EXHAUSTED

    # Reset time parsing tests
    def test_parse_reset_time_9pm(self, classifier: ErrorClassifier) -> None:
        """Test parsing 'resets at 9pm'."""
        wait = classifier.parse_reset_time("Usage resets at 9pm")
        assert wait is not None
        assert wait >= 300.0  # At least 5 minutes

    def test_parse_reset_time_in_hours(self, classifier: ErrorClassifier) -> None:
        """Test parsing 'resets in 3 hours'."""
        wait = classifier.parse_reset_time("Token limit resets in 3 hours")
        assert wait is not None
        assert 10700 <= wait <= 10900  # ~3 hours

    def test_parse_reset_time_in_minutes(self, classifier: ErrorClassifier) -> None:
        """Test parsing 'resets in 30 minutes'."""
        wait = classifier.parse_reset_time("Resets in 30 minutes")
        assert wait is not None
        assert 1700 <= wait <= 1900  # ~30 minutes

    def test_parse_reset_time_24hr_format(self, classifier: ErrorClassifier) -> None:
        """Test parsing 'resets at 21:00'."""
        wait = classifier.parse_reset_time("Usage resets at 21:00")
        assert wait is not None
        assert wait >= 300.0

    def test_parse_reset_time_no_match(self, classifier: ErrorClassifier) -> None:
        """Test no reset time when pattern doesn't match."""
        wait = classifier.parse_reset_time("Some random error message")
        assert wait is None

    # Wait time in ClassifiedError
    def test_quota_uses_parsed_wait_time(self, classifier: ErrorClassifier) -> None:
        """Test quota exhaustion uses parsed wait time."""
        result = classifier.classify(
            stdout="Token budget exhausted. Resets in 2 hours.",
            exit_code=1
        )
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED
        # Should be ~7200 seconds (2 hours)
        assert 7100 <= result.suggested_wait_seconds <= 7300

    def test_quota_default_wait_when_no_time(self, classifier: ErrorClassifier) -> None:
        """Test quota exhaustion uses default wait when no time parsed."""
        result = classifier.classify(
            stdout="Your daily token limit has been reached.",
            exit_code=1
        )
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED
        assert result.suggested_wait_seconds == 3600.0  # 1 hour default

    # Integration with is_rate_limit
    def test_quota_is_rate_limit_true(self, classifier: ErrorClassifier) -> None:
        """Test quota exhaustion has is_rate_limit=True for runner handling."""
        result = classifier.classify(
            stdout="tokens exhausted, resets 9pm",
            exit_code=1
        )
        assert result.is_rate_limit is True
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED
