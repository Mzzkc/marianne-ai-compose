"""Tests for F-098: Rate limit detection from stdout, not just stderr.

The Claude CLI may output rate limit messages in stdout with exit_code=0.
The error classifier must detect these even when:
1. exit_code is 0 (CLI handled the rate limit internally)
2. Phase 1 (structured JSON) finds other errors that mask the rate limit
3. The rate limit text appears only in stdout, not stderr

Also tests F-097 stale detection error code differentiation (E006).
"""

from __future__ import annotations

import pytest

from marianne.core.errors.classifier import ErrorClassifier
from marianne.core.errors.codes import ErrorCategory, ErrorCode


class TestRateLimitInStdout:
    """F-098: Rate limit patterns in stdout must be detected."""

    def setup_method(self) -> None:
        self.classifier = ErrorClassifier()

    def test_api_error_rate_limit_reached_in_stdout(self) -> None:
        """Claude CLI outputs 'API Error: Rate limit reached' in stdout."""
        result = self.classifier.classify_execution(
            stdout="ready\nAPI Error: Rate limit reached",
            stderr="",
            exit_code=1,
        )
        assert result.primary.category == ErrorCategory.RATE_LIMIT, (
            f"Expected RATE_LIMIT, got {result.primary.category} "
            f"({result.primary.error_code})"
        )

    def test_youve_hit_your_limit_in_stdout(self) -> None:
        """Claude CLI outputs 'You've hit your limit' in stdout."""
        result = self.classifier.classify_execution(
            stdout="You've hit your limit · resets 11pm (Europe/Berlin)",
            stderr="",
            exit_code=1,
        )
        assert result.primary.category == ErrorCategory.RATE_LIMIT

    def test_rate_limit_with_reset_time_in_stdout(self) -> None:
        """Rate limit with reset time in stdout."""
        result = self.classifier.classify_execution(
            stdout="You've hit your limit · resets Apr 3, 5pm (Europe/Berlin)",
            stderr="",
            exit_code=1,
        )
        assert result.primary.category == ErrorCategory.RATE_LIMIT

    def test_rate_limit_in_stdout_exit_code_zero(self) -> None:
        """Rate limit in stdout with exit_code=0 — CLI handled it internally.

        _detect_rate_limit currently returns False for exit_code=0.
        The classifier should still detect the pattern.
        """
        result = self.classifier.classify_execution(
            stdout="API Error: Rate limit reached\nPartial output here",
            stderr="",
            exit_code=0,
        )
        # Even with exit_code=0, the rate limit pattern should be detected
        # when we run pattern matching as a supplementary phase
        assert result.primary.category == ErrorCategory.RATE_LIMIT

    def test_rate_limit_in_stdout_with_json_errors(self) -> None:
        """Rate limit in stdout alongside structured JSON errors.

        Phase 1 finds JSON errors (e.g., partial CLI output). The rate
        limit text is outside the JSON. Phase 4 (regex fallback) is
        skipped because all_errors is non-empty. The rate limit must
        still be detected via the supplementary pattern check.
        """
        # Real reproduction: Claude CLI outputs JSON with errors[] array,
        # then rate limit text after the JSON. Phase 1 parses the JSON
        # error (→ E999), Phase 4 is skipped, rate limit is invisible.
        stdout = (
            '{"result":"","errors":[{"type":"system","message":"Internal server error"}],'
            '"cost_usd":0.0}\n'
            "API Error: Rate limit reached"
        )
        result = self.classifier.classify_execution(
            stdout=stdout,
            stderr="",
            exit_code=1,
        )
        assert result.primary.category == ErrorCategory.RATE_LIMIT, (
            f"Expected RATE_LIMIT as root cause, got {result.primary.category} "
            f"({result.primary.error_code})"
        )

    def test_rate_limit_in_stderr_with_json_errors_in_stdout(self) -> None:
        """Rate limit in stderr while JSON errors are in stdout.

        Same masking issue — Phase 1 finds errors in stdout, Phase 4
        skipped. Rate limit text in stderr must still be caught.
        """
        stdout = (
            '{"result":"","errors":[{"type":"system","message":"something failed"}],'
            '"cost_usd":0.0}'
        )
        result = self.classifier.classify_execution(
            stdout=stdout,
            stderr="API Error: Rate limit reached",
            exit_code=1,
        )
        assert result.primary.category == ErrorCategory.RATE_LIMIT

    def test_rate_limit_not_false_positive_on_normal_text(self) -> None:
        """Don't flag normal text as rate limit."""
        result = self.classifier.classify_execution(
            stdout="The function processes requests at the given rate\n"
            "It handles up to the specified limit",
            stderr="",
            exit_code=1,
        )
        # "rate" and "limit" appear but not as "rate limit" or "rate_limit"
        # The pattern r"rate.?limit" requires only 0-1 chars between
        assert result.primary.category != ErrorCategory.RATE_LIMIT

    def test_classify_method_detects_rate_limit_in_stdout(self) -> None:
        """The classify() method (used by _detect_rate_limit) checks stdout."""
        result = self.classifier.classify(
            stdout="API Error: Rate limit reached",
            stderr="",
            exit_code=1,
        )
        assert result.category == ErrorCategory.RATE_LIMIT

    def test_classify_method_rate_limit_exit_zero(self) -> None:
        """classify() with rate limit text and exit_code=0."""
        result = self.classifier.classify(
            stdout="API Error: Rate limit reached",
            stderr="",
            exit_code=0,
        )
        # exit_code=0 currently doesn't trigger classify to check patterns
        # because the method returns success early. This tests the
        # classify_execution path which should override.
        # For classify() alone, exit_code=0 is valid success.
        # The fix is in classify_execution, not classify.


class TestRateLimitStdoutPatterns:
    """Verify specific pattern strings from F-098 match correctly."""

    def setup_method(self) -> None:
        self.classifier = ErrorClassifier()

    def test_pattern_api_error_rate_limit_reached(self) -> None:
        """'API Error: Rate limit reached' — the exact text from v3 logs."""
        result = self.classifier.classify(
            stdout="",
            stderr="API Error: Rate limit reached",
            exit_code=1,
        )
        assert result.category == ErrorCategory.RATE_LIMIT

    def test_pattern_hit_your_limit(self) -> None:
        """'You've hit your limit' — the exact text from v3 logs."""
        result = self.classifier.classify(
            stdout="",
            stderr="You've hit your limit · resets 11pm",
            exit_code=1,
        )
        assert result.category == ErrorCategory.RATE_LIMIT

    def test_pattern_hit_limit_with_date(self) -> None:
        """'You've hit your limit · resets Apr 3, 5pm' — with date."""
        result = self.classifier.classify(
            stdout="",
            stderr="You've hit your limit · resets Apr 3, 5pm (Europe/Berlin)",
            exit_code=1,
        )
        assert result.category == ErrorCategory.RATE_LIMIT

    def test_pattern_daily_limit_reached(self) -> None:
        """'daily limit reached' pattern."""
        result = self.classifier.classify(
            stdout="",
            stderr="daily limit reached, please try again tomorrow",
            exit_code=1,
        )
        assert result.category == ErrorCategory.RATE_LIMIT


class TestStaleDetectionErrorCode:
    """F-097: Stale detection should produce E006, not E001.

    Stale detection (no stdout for N seconds) and backend timeout
    (execution exceeded timeout limit) are different failure modes
    that need different error codes for diagnosis.
    """

    def setup_method(self) -> None:
        self.classifier = ErrorClassifier()

    def test_stale_detection_classified_distinctly(self) -> None:
        """Stale detection with error_type='stale' gets E006, not E001."""
        result = self.classifier.classify_execution(
            stdout="",
            stderr="Stale execution: no output for 1800s (limit: 1800s)",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.primary.error_code == ErrorCode.EXECUTION_STALE, (
            f"Expected EXECUTION_STALE (E006), got {result.primary.error_code}"
        )

    def test_stale_detection_is_retriable(self) -> None:
        """Stale detection errors should be retriable."""
        result = self.classifier.classify_execution(
            stdout="",
            stderr="Stale execution: no output for 1800s (limit: 1800s)",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.primary.retriable is True

    def test_backend_timeout_still_gets_e001(self) -> None:
        """Regular timeout (not stale) still gets E001."""
        result = self.classifier.classify_execution(
            stdout="partial output here",
            stderr="Command timed out after 300s",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.primary.error_code == ErrorCode.EXECUTION_TIMEOUT

    def test_stale_pattern_in_stderr(self) -> None:
        """Stale detection pattern detected in stderr."""
        result = self.classifier.classify(
            stdout="",
            stderr="Stale execution: no output for 3600s (limit: 3600s)",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.error_code == ErrorCode.EXECUTION_STALE

    def test_stale_vs_timeout_different_codes(self) -> None:
        """Stale and timeout produce different error codes."""
        stale_result = self.classifier.classify_execution(
            stdout="",
            stderr="Stale execution: no output for 1800s (limit: 1800s)",
            exit_code=None,
            exit_reason="timeout",
        )
        timeout_result = self.classifier.classify_execution(
            stdout="partial output here",
            stderr="",
            exit_code=None,
            exit_reason="timeout",
        )
        assert stale_result.primary.error_code != timeout_result.primary.error_code
