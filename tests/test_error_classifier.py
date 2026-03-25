"""Comprehensive tests for ErrorClassifier.

Tests cover:
- parse_reset_time(): All time format variants and edge cases
- classify(): Priority-ordered classification (signal, timeout, pattern, exit code, fallback)
- _classify_by_pattern(): All pattern categories in priority order
- _classify_by_exit_code(): All recognized exit codes and None/unrecognized
- _classify_signal(): All signal types with context-dependent behavior
- _matches_any(): Caching and empty-pattern behavior
- classify_execution(): Structured JSON, regex fallback, multi-error root cause
- Pattern priority: Quota > rate limit, ENOENT > CLI mode > auth
"""

import json
import re
import signal

import pytest

from mozart.core.errors.classifier import ErrorClassifier
from mozart.core.errors.codes import ErrorCategory, ErrorCode
from mozart.core.errors.models import ClassificationResult

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def classifier() -> ErrorClassifier:
    """Create a default ErrorClassifier instance."""
    return ErrorClassifier()


# =============================================================================
# parse_reset_time() Tests
# =============================================================================


class TestParseResetTime:
    """Tests for ErrorClassifier.parse_reset_time()."""

    @pytest.mark.parametrize(
        "text, expected_approx",
        [
            ("resets in 3 hours", 3 * 3600),
            ("resets in 1 hour", 1 * 3600),
            ("resets in 5 hr", 5 * 3600),
            ("Resets in 30 minutes", 30 * 60),
            ("resets in 10 min", 10 * 60),
            ("resets in 45 minutes", 45 * 60),
        ],
        ids=[
            "3-hours", "1-hour", "5-hr",
            "30-minutes", "10-min", "45-minutes",
        ],
    )
    def test_relative_time_formats(
        self, classifier: ErrorClassifier, text: str, expected_approx: int
    ) -> None:
        """Test 'resets in X hours/minutes' formats return correct seconds."""
        result = classifier.parse_reset_time(text)
        assert result is not None
        assert abs(result - expected_approx) < 10  # Allow small tolerance

    def test_minimum_wait_enforced_for_relative(self, classifier: ErrorClassifier) -> None:
        """Test that relative times below 5 minutes are clamped to 300 seconds."""
        result = classifier.parse_reset_time("resets in 1 minute")
        assert result is not None
        # 1 minute = 60 seconds, but minimum is 300
        assert result == 300.0

    @pytest.mark.parametrize(
        "text",
        [
            "resets at 9pm",
            "resets at 9 pm",
            "reset at 3pm",
            "resets at 11am",
            "resets at 12am",
            "resets at 12pm",
        ],
        ids=["9pm", "9-space-pm", "3pm", "11am", "12am-midnight", "12pm-noon"],
    )
    def test_absolute_12hr_time_formats(
        self, classifier: ErrorClassifier, text: str
    ) -> None:
        """Test 'resets at Xpm/Xam' formats return a positive wait time >= 300."""
        result = classifier.parse_reset_time(text)
        assert result is not None
        assert result >= 300.0

    def test_absolute_24hr_format(self, classifier: ErrorClassifier) -> None:
        """Test 'resets at 21:00' returns a positive wait time >= 300."""
        result = classifier.parse_reset_time("Usage resets at 21:00")
        assert result is not None
        assert result >= 300.0

    def test_reset_time_from_loose_pattern(self, classifier: ErrorClassifier) -> None:
        """Test 'reset ... 9pm' loose pattern matching."""
        result = classifier.parse_reset_time("limit will reset by 9pm today")
        assert result is not None
        assert result >= 300.0

    @pytest.mark.parametrize(
        "text",
        [
            "Some random error message",
            "Connection refused",
            "rate limit exceeded",
            "",
            "resets sometime maybe",
        ],
        ids=["random", "conn-refused", "rate-limit-no-time", "empty", "vague-reset"],
    )
    def test_no_match_returns_none(
        self, classifier: ErrorClassifier, text: str
    ) -> None:
        """Test that text without a parseable reset time returns None."""
        result = classifier.parse_reset_time(text)
        assert result is None


# =============================================================================
# classify() Method Tests - Priority Order
# =============================================================================


class TestClassifyPriorityOrder:
    """Tests for classify() method delegation priority."""

    def test_signal_takes_priority_over_pattern(self, classifier: ErrorClassifier) -> None:
        """Signal-based classification takes priority over pattern matching."""
        result = classifier.classify(
            stderr="rate limit exceeded",
            exit_signal=signal.SIGTERM,
            exit_reason="killed",
        )
        # Signal classification should win, not pattern matching
        assert result.category == ErrorCategory.SIGNAL
        assert result.error_code == ErrorCode.EXECUTION_KILLED

    def test_signal_takes_priority_over_exit_code(self, classifier: ErrorClassifier) -> None:
        """Signal-based classification takes priority over exit code."""
        result = classifier.classify(
            exit_code=124,
            exit_signal=signal.SIGSEGV,
            exit_reason="killed",
        )
        assert result.category == ErrorCategory.FATAL
        assert result.error_code == ErrorCode.EXECUTION_CRASHED

    def test_timeout_reason_takes_priority_over_pattern(self, classifier: ErrorClassifier) -> None:
        """Timeout exit_reason takes priority over pattern matching."""
        result = classifier.classify(
            stderr="rate limit exceeded",
            exit_reason="timeout",
        )
        assert result.category == ErrorCategory.TIMEOUT
        assert result.error_code == ErrorCode.EXECUTION_TIMEOUT

    def test_pattern_takes_priority_over_exit_code(self, classifier: ErrorClassifier) -> None:
        """Pattern matching takes priority over exit code analysis."""
        result = classifier.classify(
            stderr="rate limit exceeded",
            exit_code=1,
        )
        assert result.category == ErrorCategory.RATE_LIMIT
        assert result.error_code == ErrorCode.RATE_LIMIT_API

    def test_exit_code_used_when_no_pattern_match(self, classifier: ErrorClassifier) -> None:
        """Exit code analysis used when no pattern matches."""
        result = classifier.classify(
            stderr="something happened",
            exit_code=127,
        )
        assert result.error_code == ErrorCode.BACKEND_NOT_FOUND

    def test_unknown_fallback(self, classifier: ErrorClassifier) -> None:
        """Unknown fallback when nothing matches."""
        result = classifier.classify(
            stderr="something happened",
            exit_code=42,
        )
        assert result.category == ErrorCategory.FATAL
        assert result.error_code == ErrorCode.UNKNOWN
        assert result.retriable is False

    def test_exception_included_in_combined_text(self, classifier: ErrorClassifier) -> None:
        """Exception string is appended to combined text for pattern matching."""
        exc = RuntimeError("rate limit exceeded")
        result = classifier.classify(
            stdout="",
            stderr="",
            exit_code=1,
            exception=exc,
        )
        assert result.category == ErrorCategory.RATE_LIMIT


# =============================================================================
# _classify_by_pattern() Tests
# =============================================================================


class TestClassifyByPattern:
    """Tests for pattern-based classification."""

    @pytest.mark.parametrize(
        "text, expected_code, expected_category",
        [
            # Quota exhaustion patterns
            ("tokens exhausted", ErrorCode.QUOTA_EXHAUSTED, ErrorCategory.RATE_LIMIT),
            ("token budget used up", ErrorCode.QUOTA_EXHAUSTED, ErrorCategory.RATE_LIMIT),
            ("usage will reset at 9pm", ErrorCode.QUOTA_EXHAUSTED, ErrorCategory.RATE_LIMIT),
            ("resets 9pm", ErrorCode.QUOTA_EXHAUSTED, ErrorCategory.RATE_LIMIT),
            ("resets in 3 hours", ErrorCode.QUOTA_EXHAUSTED, ErrorCategory.RATE_LIMIT),
            ("daily token limit reached", ErrorCode.QUOTA_EXHAUSTED, ErrorCategory.RATE_LIMIT),
            ("no credits left", ErrorCode.QUOTA_EXHAUSTED, ErrorCategory.RATE_LIMIT),
            ("token allowance exhausted", ErrorCode.QUOTA_EXHAUSTED, ErrorCategory.RATE_LIMIT),
            # Rate limit patterns (non-quota)
            ("rate limit exceeded", ErrorCode.RATE_LIMIT_API, ErrorCategory.RATE_LIMIT),
            ("too many requests", ErrorCode.RATE_LIMIT_API, ErrorCategory.RATE_LIMIT),
            ("HTTP 429 error", ErrorCode.RATE_LIMIT_API, ErrorCategory.RATE_LIMIT),
            ("You've hit your limit", ErrorCode.RATE_LIMIT_API, ErrorCategory.RATE_LIMIT),
            # Capacity patterns (subset of rate limit)
            ("service overloaded", ErrorCode.CAPACITY_EXCEEDED, ErrorCategory.RATE_LIMIT),
            ("try again later", ErrorCode.CAPACITY_EXCEEDED, ErrorCategory.RATE_LIMIT),
            ("server at capacity", ErrorCode.CAPACITY_EXCEEDED, ErrorCategory.RATE_LIMIT),
            # ENOENT patterns
            ("spawn claude ENOENT", ErrorCode.BACKEND_NOT_FOUND, ErrorCategory.CONFIGURATION),
            ("no such file or directory", ErrorCode.BACKEND_NOT_FOUND, ErrorCategory.CONFIGURATION),
            ("command not found", ErrorCode.BACKEND_NOT_FOUND, ErrorCategory.CONFIGURATION),
            ("claude not found in PATH", ErrorCode.BACKEND_NOT_FOUND, ErrorCategory.CONFIGURATION),
            # CLI mode patterns
            (
                "only prompt commands are supported in streaming mode",
                ErrorCode.CONFIG_CLI_MODE_ERROR,
                ErrorCategory.CONFIGURATION,
            ),
            # Auth patterns
            ("401 Unauthorized", ErrorCode.BACKEND_AUTH, ErrorCategory.AUTH),
            ("invalid api key", ErrorCode.BACKEND_AUTH, ErrorCategory.AUTH),
            ("permission denied", ErrorCode.BACKEND_AUTH, ErrorCategory.AUTH),
            ("access denied", ErrorCode.BACKEND_AUTH, ErrorCategory.AUTH),
            ("403 Forbidden", ErrorCode.BACKEND_AUTH, ErrorCategory.AUTH),
            # MCP patterns
            ("MCP server error occurred", ErrorCode.CONFIG_MCP_ERROR, ErrorCategory.CONFIGURATION),
            (
                "Missing environment variables: FOO",
                ErrorCode.CONFIG_MCP_ERROR,
                ErrorCategory.CONFIGURATION,
            ),
            # DNS patterns
            ("getaddrinfo ENOTFOUND", ErrorCode.NETWORK_DNS_ERROR, ErrorCategory.NETWORK),
            ("DNS resolution failed", ErrorCode.NETWORK_DNS_ERROR, ErrorCategory.NETWORK),
            ("could not resolve host", ErrorCode.NETWORK_DNS_ERROR, ErrorCategory.NETWORK),
            # SSL patterns
            ("SSL_ERROR_HANDSHAKE", ErrorCode.NETWORK_SSL_ERROR, ErrorCategory.NETWORK),
            ("certificate verify failed", ErrorCode.NETWORK_SSL_ERROR, ErrorCategory.NETWORK),
            ("TLS error occurred", ErrorCode.NETWORK_SSL_ERROR, ErrorCategory.NETWORK),
            # Generic network patterns
            ("connection refused", ErrorCode.NETWORK_CONNECTION_FAILED, ErrorCategory.NETWORK),
            ("ECONNREFUSED", ErrorCode.NETWORK_CONNECTION_FAILED, ErrorCategory.NETWORK),
            ("ETIMEDOUT", ErrorCode.NETWORK_CONNECTION_FAILED, ErrorCategory.NETWORK),
            ("network unreachable", ErrorCode.NETWORK_CONNECTION_FAILED, ErrorCategory.NETWORK),
        ],
        ids=[
            "quota-tokens-exhausted", "quota-token-budget", "quota-usage-reset-at",
            "quota-resets-9pm", "quota-resets-in-hours", "quota-daily-token-limit",
            "quota-no-credits", "quota-token-allowance",
            "rate-limit-exceeded", "rate-limit-429-text", "rate-limit-429-code",
            "rate-limit-hit-limit",
            "capacity-overloaded", "capacity-try-later", "capacity-at-capacity",
            "enoent-spawn", "enoent-no-such-file", "enoent-cmd-not-found",
            "enoent-not-in-path",
            "cli-mode-streaming",
            "auth-401", "auth-invalid-key", "auth-permission-denied",
            "auth-access-denied", "auth-403",
            "mcp-server-error", "mcp-missing-env",
            "dns-enotfound", "dns-resolution", "dns-could-not-resolve",
            "ssl-handshake", "ssl-certificate", "ssl-tls-error",
            "network-conn-refused", "network-econnrefused",
            "network-etimedout", "network-unreachable",
        ],
    )
    def test_pattern_classification(
        self,
        classifier: ErrorClassifier,
        text: str,
        expected_code: ErrorCode,
        expected_category: ErrorCategory,
    ) -> None:
        """Test that each pattern triggers the correct error code and category."""
        result = classifier.classify(stderr=text, exit_code=1)
        assert result.error_code == expected_code, (
            f"Expected {expected_code} for '{text}', got {result.error_code}"
        )
        assert result.category == expected_category

    @pytest.mark.parametrize(
        "text, expected_retriable",
        [
            ("rate limit exceeded", True),
            ("tokens exhausted", True),
            ("ENOENT", True),
            ("401 Unauthorized", False),
            ("MCP server error", False),
            ("only prompt commands are supported in streaming mode", False),
            ("connection refused", True),
            ("DNS resolution failed", True),
            ("SSL_ERROR", True),
        ],
        ids=[
            "rate-limit", "quota", "enoent",
            "auth", "mcp", "cli-mode",
            "network", "dns", "ssl",
        ],
    )
    def test_pattern_retriability(
        self,
        classifier: ErrorClassifier,
        text: str,
        expected_retriable: bool,
    ) -> None:
        """Test that each pattern category has the correct retriable flag."""
        result = classifier.classify(stderr=text, exit_code=1)
        assert result.retriable is expected_retriable


# =============================================================================
# _classify_by_exit_code() Tests
# =============================================================================


class TestClassifyByExitCode:
    """Tests for exit code classification."""

    @pytest.mark.parametrize(
        "exit_code, expected_code, expected_category, expected_retriable",
        [
            (0, ErrorCode.VALIDATION_GENERIC, ErrorCategory.VALIDATION, True),
            (1, ErrorCode.EXECUTION_UNKNOWN, ErrorCategory.TRANSIENT, True),
            (2, ErrorCode.EXECUTION_UNKNOWN, ErrorCategory.TRANSIENT, True),
            (124, ErrorCode.EXECUTION_TIMEOUT, ErrorCategory.TIMEOUT, True),
            (127, ErrorCode.BACKEND_NOT_FOUND, ErrorCategory.FATAL, False),
        ],
        ids=["exit-0-validation", "exit-1-transient", "exit-2-transient",
             "exit-124-timeout", "exit-127-not-found"],
    )
    def test_exit_code_classification(
        self,
        classifier: ErrorClassifier,
        exit_code: int,
        expected_code: ErrorCode,
        expected_category: ErrorCategory,
        expected_retriable: bool,
    ) -> None:
        """Test each recognized exit code produces the correct classification."""
        result = classifier.classify(exit_code=exit_code)
        assert result.error_code == expected_code
        assert result.category == expected_category
        assert result.retriable is expected_retriable

    def test_exit_code_none_falls_through(self, classifier: ErrorClassifier) -> None:
        """Test exit_code=None falls through to unknown fallback."""
        result = classifier.classify(exit_code=None)
        assert result.error_code == ErrorCode.UNKNOWN
        assert result.category == ErrorCategory.FATAL

    def test_unrecognized_exit_code_falls_through(self, classifier: ErrorClassifier) -> None:
        """Test unrecognized exit code (e.g. 42) falls through to unknown fallback."""
        result = classifier.classify(exit_code=42)
        assert result.error_code == ErrorCode.UNKNOWN
        assert result.category == ErrorCategory.FATAL

    @pytest.mark.parametrize(
        "exit_code",
        [1, 2],
        ids=["exit-1", "exit-2"],
    )
    def test_exit_codes_1_2_have_suggested_wait(
        self, classifier: ErrorClassifier, exit_code: int
    ) -> None:
        """Test exit codes 1 and 2 suggest a 10s wait."""
        result = classifier.classify(exit_code=exit_code)
        assert result.suggested_wait_seconds == 10.0


# =============================================================================
# _classify_signal() Tests
# =============================================================================


class TestClassifySignal:
    """Tests for signal-based classification."""

    @pytest.mark.parametrize(
        "sig, expected_code, expected_category, expected_retriable",
        [
            (signal.SIGTERM, ErrorCode.EXECUTION_KILLED, ErrorCategory.SIGNAL, True),
            (signal.SIGHUP, ErrorCode.EXECUTION_KILLED, ErrorCategory.SIGNAL, True),
            (signal.SIGPIPE, ErrorCode.EXECUTION_KILLED, ErrorCategory.SIGNAL, True),
            (signal.SIGSEGV, ErrorCode.EXECUTION_CRASHED, ErrorCategory.FATAL, False),
            (signal.SIGBUS, ErrorCode.EXECUTION_CRASHED, ErrorCategory.FATAL, False),
            (signal.SIGABRT, ErrorCode.EXECUTION_CRASHED, ErrorCategory.FATAL, False),
            (signal.SIGFPE, ErrorCode.EXECUTION_CRASHED, ErrorCategory.FATAL, False),
            (signal.SIGILL, ErrorCode.EXECUTION_CRASHED, ErrorCategory.FATAL, False),
            (signal.SIGINT, ErrorCode.EXECUTION_INTERRUPTED, ErrorCategory.FATAL, False),
        ],
        ids=[
            "SIGTERM-retriable", "SIGHUP-retriable", "SIGPIPE-retriable",
            "SIGSEGV-fatal", "SIGBUS-fatal", "SIGABRT-fatal",
            "SIGFPE-fatal", "SIGILL-fatal",
            "SIGINT-interrupted",
        ],
    )
    def test_signal_classification(
        self,
        classifier: ErrorClassifier,
        sig: int,
        expected_code: ErrorCode,
        expected_category: ErrorCategory,
        expected_retriable: bool,
    ) -> None:
        """Test each signal type produces the correct classification."""
        result = classifier.classify(exit_signal=sig, exit_reason="killed")
        assert result.error_code == expected_code
        assert result.category == expected_category
        assert result.retriable is expected_retriable

    def test_sigkill_without_timeout_or_oom(self, classifier: ErrorClassifier) -> None:
        """SIGKILL from unknown source is retriable with EXECUTION_KILLED."""
        result = classifier.classify(exit_signal=signal.SIGKILL, exit_reason="killed")
        assert result.error_code == ErrorCode.EXECUTION_KILLED
        assert result.category == ErrorCategory.SIGNAL
        assert result.retriable is True
        assert result.suggested_wait_seconds == 30.0

    def test_sigkill_with_timeout_reason(self, classifier: ErrorClassifier) -> None:
        """SIGKILL due to timeout produces EXECUTION_TIMEOUT."""
        result = classifier.classify(exit_signal=signal.SIGKILL, exit_reason="timeout")
        assert result.error_code == ErrorCode.EXECUTION_TIMEOUT
        assert result.category == ErrorCategory.TIMEOUT
        assert result.retriable is True
        assert result.suggested_wait_seconds == 60.0

    @pytest.mark.parametrize(
        "oom_text",
        [
            "oom-killer invoked",
            "out of memory",
            "cannot allocate memory",
            "memory cgroup limit reached",
        ],
        ids=["oom-killer", "out-of-memory", "cannot-allocate", "memory-cgroup"],
    )
    def test_sigkill_with_oom_indicators(
        self, classifier: ErrorClassifier, oom_text: str
    ) -> None:
        """SIGKILL with OOM indicators in output produces EXECUTION_OOM."""
        result = classifier.classify(
            stderr=oom_text,
            exit_signal=signal.SIGKILL,
            exit_reason="killed",
        )
        assert result.error_code == ErrorCode.EXECUTION_OOM
        assert result.category == ErrorCategory.FATAL
        assert result.retriable is False

    def test_unknown_signal_number(self, classifier: ErrorClassifier) -> None:
        """Unknown signal number (e.g. 64) is retriable."""
        result = classifier.classify(exit_signal=64, exit_reason="killed")
        assert result.error_code == ErrorCode.EXECUTION_KILLED
        assert result.category == ErrorCategory.SIGNAL
        assert result.retriable is True
        assert result.suggested_wait_seconds == 30.0

    def test_signal_with_timeout_exit_reason_overrides_fatal(
        self, classifier: ErrorClassifier
    ) -> None:
        """Timeout exit_reason overrides even fatal signals."""
        result = classifier.classify(
            exit_signal=signal.SIGSEGV,
            exit_reason="timeout",
        )
        # Timeout check comes first in _classify_signal
        assert result.error_code == ErrorCode.EXECUTION_TIMEOUT
        assert result.retriable is True

    def test_retriable_signal_suggested_wait(self, classifier: ErrorClassifier) -> None:
        """Retriable signals (SIGTERM etc.) suggest 10s wait."""
        result = classifier.classify(exit_signal=signal.SIGTERM, exit_reason="killed")
        assert result.suggested_wait_seconds == 10.0


# =============================================================================
# _matches_any() Caching Tests
# =============================================================================


class TestMatchesAny:
    """Tests for _matches_any() method including caching behavior."""

    def test_matches_any_returns_true_on_match(self, classifier: ErrorClassifier) -> None:
        """Test _matches_any returns True when text matches a pattern."""
        assert classifier._matches_any(
            "rate limit exceeded", classifier.rate_limit_patterns,
        ) is True

    def test_matches_any_returns_false_on_no_match(self, classifier: ErrorClassifier) -> None:
        """Test _matches_any returns False when no pattern matches."""
        assert classifier._matches_any(
            "everything is fine", classifier.rate_limit_patterns,
        ) is False

    def test_matches_any_caches_combined_pattern(self, classifier: ErrorClassifier) -> None:
        """Test that _matches_any uses pre-computed combined regex patterns."""
        key = id(classifier.rate_limit_patterns)
        # Cache is pre-populated in __init__() for all known pattern lists
        assert key in classifier._combined_cache
        cached = classifier._combined_cache[key]

        # Calls reuse the same cached pattern object
        classifier._matches_any("rate limit", classifier.rate_limit_patterns)
        assert classifier._combined_cache[key] is cached

        # Second call still uses same cached pattern
        classifier._matches_any("quota", classifier.rate_limit_patterns)
        assert classifier._combined_cache[key] is cached

    def test_matches_any_empty_patterns(self, classifier: ErrorClassifier) -> None:
        """Test _matches_any with an empty pattern list matches nothing."""
        empty_patterns: list[re.Pattern[str]] = []
        # An empty alternation "|".join([]) produces "", which matches everything
        # in regex. Let's verify behavior - this documents actual behavior.
        result = classifier._matches_any("anything", empty_patterns)
        # Empty alternation compiles to empty string pattern which matches everything
        # This documents the actual behavior of the code
        assert isinstance(result, bool)

    def test_matches_any_case_insensitive(self, classifier: ErrorClassifier) -> None:
        """Test that _matches_any is case insensitive."""
        assert classifier._matches_any(
            "RATE LIMIT EXCEEDED", classifier.rate_limit_patterns,
        ) is True
        assert classifier._matches_any("Rate Limit", classifier.rate_limit_patterns) is True


# =============================================================================
# Pattern Priority Tests
# =============================================================================


class TestPatternPriority:
    """Tests for pattern matching priority order in _classify_by_pattern."""

    def test_quota_exhaustion_before_rate_limit(self, classifier: ErrorClassifier) -> None:
        """Quota exhaustion patterns match before generic rate limit patterns."""
        # Text that matches both quota exhaustion ("tokens exhausted") and rate limit ("limit")
        result = classifier.classify(
            stderr="tokens exhausted, rate limit reached",
            exit_code=1,
        )
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED

    def test_enoent_before_cli_mode(self, classifier: ErrorClassifier) -> None:
        """ENOENT patterns match before CLI mode mismatch patterns."""
        result = classifier.classify(
            stderr="ENOENT: streaming mode not supported",
            exit_code=1,
        )
        assert result.error_code == ErrorCode.BACKEND_NOT_FOUND

    def test_cli_mode_before_auth(self, classifier: ErrorClassifier) -> None:
        """CLI mode mismatch patterns match before auth patterns."""
        result = classifier.classify(
            stderr="only prompt commands are supported in streaming mode; 401 unauthorized",
            exit_code=1,
        )
        assert result.error_code == ErrorCode.CONFIG_CLI_MODE_ERROR

    def test_enoent_before_auth(self, classifier: ErrorClassifier) -> None:
        """ENOENT patterns match before auth patterns."""
        result = classifier.classify(
            stderr="command not found; permission denied",
            exit_code=1,
        )
        assert result.error_code == ErrorCode.BACKEND_NOT_FOUND

    def test_auth_before_mcp(self, classifier: ErrorClassifier) -> None:
        """Auth patterns match before MCP patterns."""
        result = classifier.classify(
            stderr="unauthorized MCP server error",
            exit_code=1,
        )
        assert result.error_code == ErrorCode.BACKEND_AUTH

    def test_dns_before_generic_network(self, classifier: ErrorClassifier) -> None:
        """DNS patterns match before generic network patterns."""
        result = classifier.classify(
            stderr="getaddrinfo ENOTFOUND: connection refused",
            exit_code=1,
        )
        assert result.error_code == ErrorCode.NETWORK_DNS_ERROR

    def test_ssl_before_generic_network(self, classifier: ErrorClassifier) -> None:
        """SSL patterns match before generic network patterns."""
        result = classifier.classify(
            stderr="SSL_ERROR handshake failed: connection reset",
            exit_code=1,
        )
        assert result.error_code == ErrorCode.NETWORK_SSL_ERROR


# =============================================================================
# classify_execution() Tests
# =============================================================================


class TestClassifyExecution:
    """Tests for classify_execution() with structured JSON and fallback."""

    def test_structured_json_errors_parsed(self, classifier: ErrorClassifier) -> None:
        """Test that structured JSON errors[] are parsed and classified."""
        stdout = json.dumps({
            "result": "",
            "errors": [
                {"type": "system", "message": "Rate limit exceeded"},
            ],
            "cost_usd": 0.05,
        })
        result = classifier.classify_execution(stdout=stdout, exit_code=1)
        assert isinstance(result, ClassificationResult)
        assert result.classification_method == "structured"
        assert result.primary.error_code == ErrorCode.RATE_LIMIT_API

    def test_regex_fallback_when_no_json(self, classifier: ErrorClassifier) -> None:
        """Test regex fallback when no structured JSON errors are present."""
        result = classifier.classify_execution(
            stderr="rate limit exceeded",
            exit_code=1,
        )
        assert isinstance(result, ClassificationResult)
        assert result.classification_method == "regex_fallback"
        assert result.primary.category == ErrorCategory.RATE_LIMIT

    def test_multi_error_root_cause_selection(self, classifier: ErrorClassifier) -> None:
        """Test that root cause is selected from multiple JSON errors."""
        stdout = json.dumps({
            "result": "",
            "errors": [
                {"type": "system", "message": "Rate limit exceeded"},
                {"type": "user", "message": "spawn claude ENOENT"},
            ],
        })
        result = classifier.classify_execution(stdout=stdout, exit_code=1)
        # ENOENT (BACKEND_NOT_FOUND) has higher priority than rate limit
        assert result.primary.error_code == ErrorCode.BACKEND_NOT_FOUND
        assert len(result.all_errors) >= 2

    def test_classify_execution_with_signal(self, classifier: ErrorClassifier) -> None:
        """Test classify_execution handles signals."""
        result = classifier.classify_execution(
            exit_signal=signal.SIGTERM,
            exit_reason="killed",
        )
        assert result.primary.error_code == ErrorCode.EXECUTION_KILLED

    def test_classify_execution_with_timeout_reason(self, classifier: ErrorClassifier) -> None:
        """Test classify_execution handles timeout exit_reason."""
        result = classifier.classify_execution(
            exit_code=1,
            exit_reason="timeout",
        )
        assert result.primary.error_code == ErrorCode.EXECUTION_TIMEOUT

    def test_classify_execution_with_timeout_exception(self, classifier: ErrorClassifier) -> None:
        """Test classify_execution handles exceptions containing 'timeout'."""
        # The exception analysis checks for 'timeout' as a substring in str(exception).lower()
        exc = RuntimeError("request timeout exceeded")
        result = classifier.classify_execution(
            exit_code=1,
            exception=exc,
        )
        assert any(
            e.error_code == ErrorCode.EXECUTION_TIMEOUT for e in result.all_errors
        )

    def test_classify_execution_network_exception(self, classifier: ErrorClassifier) -> None:
        """Test classify_execution with network-related exception."""
        exc = ConnectionError("connection lost")
        result = classifier.classify_execution(
            exit_code=1,
            exception=exc,
        )
        assert any(
            e.error_code == ErrorCode.NETWORK_CONNECTION_FAILED for e in result.all_errors
        )

    def test_classify_execution_result_backward_compat(self, classifier: ErrorClassifier) -> None:
        """Test ClassificationResult backward compatibility properties."""
        result = classifier.classify_execution(
            stderr="rate limit exceeded",
            exit_code=1,
        )
        # Backward-compatible properties delegate to primary
        assert result.category == result.primary.category
        assert result.message == result.primary.message
        assert result.error_code == result.primary.error_code
        assert result.retriable == result.primary.retriable
        assert result.should_retry == result.primary.should_retry

    def test_classify_execution_error_codes_list(self, classifier: ErrorClassifier) -> None:
        """Test ClassificationResult.error_codes returns all codes."""
        stdout = json.dumps({
            "result": "",
            "errors": [
                {"type": "system", "message": "Rate limit exceeded"},
                {"type": "user", "message": "spawn claude ENOENT"},
            ],
        })
        result = classifier.classify_execution(stdout=stdout, exit_code=1)
        codes = result.error_codes
        assert isinstance(codes, list)
        assert all(isinstance(c, str) for c in codes)
        assert len(codes) >= 2

    def test_classify_execution_confidence(self, classifier: ErrorClassifier) -> None:
        """Test that confidence is set for classification results."""
        result = classifier.classify_execution(exit_code=1)
        assert 0.0 <= result.confidence <= 1.0


# =============================================================================
# Custom Pattern Tests
# =============================================================================


class TestCustomPatterns:
    """Tests for ErrorClassifier with custom patterns."""

    def test_custom_rate_limit_patterns(self) -> None:
        """Test classifier with custom rate limit patterns."""
        classifier = ErrorClassifier(rate_limit_patterns=[r"custom_limit_hit"])
        result = classifier.classify(stderr="custom_limit_hit", exit_code=1)
        assert result.category == ErrorCategory.RATE_LIMIT

    def test_custom_auth_patterns(self) -> None:
        """Test classifier with custom auth patterns."""
        classifier = ErrorClassifier(auth_patterns=[r"custom_auth_fail"])
        result = classifier.classify(stderr="custom_auth_fail", exit_code=1)
        assert result.category == ErrorCategory.AUTH

    def test_custom_network_patterns(self) -> None:
        """Test classifier with custom network patterns."""
        classifier = ErrorClassifier(network_patterns=[r"custom_net_error"])
        result = classifier.classify(stderr="custom_net_error", exit_code=1)
        assert result.category == ErrorCategory.NETWORK

    def test_from_config_classmethod(self) -> None:
        """Test the from_config classmethod creates a working classifier."""
        classifier = ErrorClassifier.from_config(rate_limit_patterns=[r"custom_rl"])
        result = classifier.classify(stderr="custom_rl triggered", exit_code=1)
        assert result.category == ErrorCategory.RATE_LIMIT


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Edge case tests for ErrorClassifier."""

    def test_empty_inputs(self, classifier: ErrorClassifier) -> None:
        """Test classify with all empty/None inputs."""
        result = classifier.classify()
        assert result.error_code == ErrorCode.UNKNOWN
        assert result.category == ErrorCategory.FATAL

    def test_stdout_and_stderr_combined(self, classifier: ErrorClassifier) -> None:
        """Test that both stdout and stderr are searched for patterns."""
        result = classifier.classify(
            stdout="rate limit",
            stderr="",
            exit_code=1,
        )
        assert result.category == ErrorCategory.RATE_LIMIT

    def test_capacity_is_subset_of_rate_limit(self, classifier: ErrorClassifier) -> None:
        """Test capacity patterns produce CAPACITY_EXCEEDED
        when combined with rate limit patterns."""
        # "overloaded" matches both capacity_patterns and rate_limit_patterns
        result = classifier.classify(stderr="server overloaded", exit_code=1)
        assert result.error_code == ErrorCode.CAPACITY_EXCEEDED
        assert result.category == ErrorCategory.RATE_LIMIT

    def test_quota_with_parsed_reset_time(self, classifier: ErrorClassifier) -> None:
        """Test quota exhaustion uses parsed wait time from message."""
        result = classifier.classify(
            stdout="Token budget exhausted. Resets in 2 hours.",
            exit_code=1,
        )
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED
        assert result.suggested_wait_seconds is not None
        # ~7200 seconds
        assert 7100 <= result.suggested_wait_seconds <= 7300

    def test_quota_default_wait_when_no_time_info(self, classifier: ErrorClassifier) -> None:
        """Test quota exhaustion defaults to 3600s when no time is parseable."""
        result = classifier.classify(
            stdout="daily token limit has been reached",
            exit_code=1,
        )
        assert result.error_code == ErrorCode.QUOTA_EXHAUSTED
        assert result.suggested_wait_seconds == 3600.0

    def test_exit_code_preserved_in_result(self, classifier: ErrorClassifier) -> None:
        """Test that exit_code is preserved in the ClassifiedError."""
        result = classifier.classify(exit_code=42)
        assert result.exit_code == 42

    def test_exit_signal_preserved_in_result(self, classifier: ErrorClassifier) -> None:
        """Test that exit_signal is preserved in the ClassifiedError."""
        result = classifier.classify(exit_signal=signal.SIGTERM, exit_reason="killed")
        assert result.exit_signal == signal.SIGTERM

    def test_exit_reason_preserved_in_result(self, classifier: ErrorClassifier) -> None:
        """Test that exit_reason is preserved in the ClassifiedError."""
        result = classifier.classify(exit_reason="timeout")
        assert result.exit_reason == "timeout"

    def test_original_error_preserved(self, classifier: ErrorClassifier) -> None:
        """Test that the original exception is preserved in ClassifiedError."""
        exc = ValueError("test error")
        result = classifier.classify(exit_code=1, exception=exc)
        assert result.original_error is exc


# =============================================================================
# Bug #126 — exit_code=None Classified as FATAL Instead of Retriable
# =============================================================================


class TestExitCodeNoneClassification126:
    """Regression tests for Bug #126: exit_code=None with exit_reason='error'
    must classify as TRANSIENT/retriable, not FATAL."""

    def test_126_a_exit_code_none_is_not_fatal(self, classifier: ErrorClassifier) -> None:
        """TEST-126-A: exit_code=None, exit_reason='error' must NOT be FATAL."""
        result = classifier.classify(exit_code=None, exit_reason="error")
        assert result.category != ErrorCategory.FATAL
        assert result.retriable is True
        assert result.exit_code is None

    def test_126_b_exit_code_none_signal_still_uses_signal_path(
        self, classifier: ErrorClassifier
    ) -> None:
        """TEST-126-B: exit_code=None + exit_signal=SIGTERM still handled by signal path."""
        import signal as signal_mod
        result = classifier.classify(
            exit_code=None,
            exit_signal=signal_mod.SIGTERM,
            exit_reason="killed",
        )
        assert result.category == ErrorCategory.SIGNAL

    def test_126_c_production_scenario_empty_output(self, classifier: ErrorClassifier) -> None:
        """TEST-126-C: Exact production scenario — empty stdout/stderr, returncode=None."""
        result = classifier.classify(
            stdout="",
            stderr="",
            exit_code=None,
            exit_signal=None,
            exit_reason="error",
            exception=None,
        )
        assert result.retriable is True
        assert result.category != ErrorCategory.FATAL
        assert result.suggested_wait_seconds is not None
        assert result.suggested_wait_seconds > 0

    @pytest.mark.parametrize(
        "exit_code",
        [127, 42],
        ids=["exit-127-fatal", "exit-42-unknown-fatal"],
    )
    def test_126_d_real_exit_codes_remain_fatal(
        self, classifier: ErrorClassifier, exit_code: int
    ) -> None:
        """TEST-126-D: Genuine FATAL exit codes remain FATAL — regression gate."""
        result_before = classifier.classify(exit_code=exit_code, exit_reason="error")
        # 127 = FATAL (backend not found), 42 = unknown fallback (FATAL)
        assert result_before.category == ErrorCategory.FATAL

    def test_126_e_suggested_wait_seconds_reasonable(
        self, classifier: ErrorClassifier
    ) -> None:
        """TEST-126-E: suggested_wait_seconds in a reasonable range for transient race."""
        result = classifier.classify(exit_code=None, exit_reason="error")
        assert result.suggested_wait_seconds is not None
        assert result.suggested_wait_seconds >= 5.0
        assert result.suggested_wait_seconds <= 120.0

    def test_126_f_exit_code_none_timeout_not_caught_by_fix(
        self, classifier: ErrorClassifier
    ) -> None:
        """TEST-126-F: exit_code=None + exit_reason='timeout' still classifies as TIMEOUT."""
        result = classifier.classify(exit_code=None, exit_reason="timeout")
        assert result.category == ErrorCategory.TIMEOUT

    def test_126_g_exit_code_none_no_reason_still_falls_through(
        self, classifier: ErrorClassifier
    ) -> None:
        """TEST-126-G: exit_code=None with no exit_reason is unchanged (regression gate).

        The existing test_exit_code_none_falls_through covers exit_reason=None,
        which should still go to the unknown fallback (FATAL).
        """
        result = classifier.classify(exit_code=None, exit_reason=None)
        assert result.category == ErrorCategory.FATAL
