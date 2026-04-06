"""Tests for error taxonomy extensions: E006 stale detection and F-098 rate limit fix.

TDD tests written BEFORE implementation. These define the contract.
Blueprint, Movement 1.
"""

from __future__ import annotations

import pytest

from marianne.core.errors.codes import ErrorCode, RetryBehavior, Severity
from marianne.core.errors.classifier import ErrorClassifier


# =============================================================================
# E006 EXECUTION_STALE — Differentiate stale detection from backend timeout
# =============================================================================


class TestE006StaleErrorCode:
    """E006 must exist, be retriable, and have appropriate retry behavior."""

    def test_e006_exists_in_enum(self) -> None:
        """E006 EXECUTION_STALE is a valid ErrorCode."""
        code = ErrorCode.EXECUTION_STALE
        assert code.value == "E006"

    def test_e006_category_is_execution(self) -> None:
        """E006 belongs to the execution category (E0xx)."""
        assert ErrorCode.EXECUTION_STALE.category == "execution"

    def test_e006_is_retriable(self) -> None:
        """Stale detection is retriable — the agent may just need more time."""
        assert ErrorCode.EXECUTION_STALE.is_retriable is True

    def test_e006_severity_is_warning(self) -> None:
        """Stale detection is WARNING severity — degraded but not fatal."""
        assert ErrorCode.EXECUTION_STALE.get_severity() == Severity.WARNING

    def test_e006_retry_behavior(self) -> None:
        """E006 has appropriate retry behavior: moderate delay, retriable."""
        behavior = ErrorCode.EXECUTION_STALE.get_retry_behavior()
        assert isinstance(behavior, RetryBehavior)
        assert behavior.is_retriable is True
        assert behavior.delay_seconds > 0
        assert "stale" in behavior.reason.lower()


class TestStaleDetectionClassification:
    """Stale detection in stderr must classify as E006, not E001."""

    def setup_method(self) -> None:
        self.classifier = ErrorClassifier()

    def test_stale_stderr_classifies_as_e006(self) -> None:
        """Stale execution error in stderr → E006 EXECUTION_STALE."""
        result = self.classifier.classify(
            stdout="",
            stderr="Stale execution: no output for 1800s (limit: 1800s)",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.error_code == ErrorCode.EXECUTION_STALE

    def test_backend_timeout_still_e001(self) -> None:
        """Regular timeout without stale message → E001 EXECUTION_TIMEOUT."""
        result = self.classifier.classify(
            stdout="",
            stderr="Command timed out after 3600s",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.error_code == ErrorCode.EXECUTION_TIMEOUT

    def test_classify_execution_stale_e006(self) -> None:
        """classify_execution route: stale stderr → E006."""
        result = self.classifier.classify_execution(
            stdout="",
            stderr="Stale execution: no output for 900s (limit: 900s)",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.primary.error_code == ErrorCode.EXECUTION_STALE

    def test_classify_execution_timeout_still_e001(self) -> None:
        """classify_execution route: regular timeout → E001."""
        result = self.classifier.classify_execution(
            stdout="",
            stderr="",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.primary.error_code == ErrorCode.EXECUTION_TIMEOUT

    def test_stale_detection_various_durations(self) -> None:
        """Stale detection with various duration formats."""
        for stderr_msg in [
            "Stale execution: no output for 30s (limit: 30s)",
            "Stale execution: no output for 7200.0s (limit: 7200s)",
            "Stale execution: no output for 1800s (limit: 1800s)",
        ]:
            result = self.classifier.classify(
                stdout="",
                stderr=stderr_msg,
                exit_code=None,
                exit_reason="timeout",
            )
            assert result.error_code == ErrorCode.EXECUTION_STALE, (
                f"Failed for stderr: {stderr_msg}"
            )


# =============================================================================
# F-098: Rate limit patterns must be detected in stdout, not just Phase 4
# =============================================================================


class TestRateLimitStdoutDetection:
    """Rate limits in stdout must be detected even when Phase 1 finds JSON errors."""

    def setup_method(self) -> None:
        self.classifier = ErrorClassifier()

    def test_rate_limit_in_stdout_plain(self) -> None:
        """Rate limit message in stdout with no JSON → detected via regex fallback."""
        result = self.classifier.classify_execution(
            stdout="ready\nAPI Error: Rate limit reached",
            stderr="",
            exit_code=1,
        )
        assert result.primary.error_code in (
            ErrorCode.RATE_LIMIT_API,
            ErrorCode.RATE_LIMIT_CLI,
        )

    def test_rate_limit_hit_your_limit_stdout(self) -> None:
        """'You've hit your limit' message in stdout → rate limit detection."""
        result = self.classifier.classify_execution(
            stdout="You've hit your limit · resets 11pm (Europe/Berlin)",
            stderr="",
            exit_code=1,
        )
        assert result.primary.category.value == "rate_limit"

    def test_rate_limit_in_stdout_with_json_errors(self) -> None:
        """Rate limit in stdout PLUS JSON errors → rate limit should win.

        This is the core F-098 bug: Phase 1 finds JSON errors, Phase 4 is
        skipped, and the rate limit pattern in stdout is never checked.
        """
        # Simulate: Claude CLI outputs JSON error AND rate limit text in stdout
        stdout_with_json_and_rate_limit = (
            '{"error": {"type": "error", "message": "something failed"}}\n'
            "API Error: Rate limit reached"
        )
        result = self.classifier.classify_execution(
            stdout=stdout_with_json_and_rate_limit,
            stderr="",
            exit_code=1,
        )
        # The rate limit should be detected regardless of JSON error
        assert any(
            e.category.value == "rate_limit" for e in result.all_errors
        ), (
            f"Rate limit not detected. Got: "
            f"{[(e.error_code.value, e.category.value) for e in result.all_errors]}"
        )

    def test_rate_limit_reset_time_in_stdout(self) -> None:
        """Rate limit with reset time in stdout."""
        result = self.classifier.classify_execution(
            stdout="You've hit your limit · resets Apr 3, 5pm (Europe/Berlin)",
            stderr="",
            exit_code=1,
        )
        assert result.primary.category.value == "rate_limit"

    def test_no_false_positive_rate_limit(self) -> None:
        """Normal error output should NOT trigger rate limit detection."""
        result = self.classifier.classify_execution(
            stdout="Error: file not found",
            stderr="",
            exit_code=1,
        )
        assert result.primary.category.value != "rate_limit"

    def test_rate_limit_in_stderr_still_works(self) -> None:
        """Rate limit in stderr (existing behavior) still works."""
        result = self.classifier.classify_execution(
            stdout="",
            stderr="rate limit exceeded",
            exit_code=1,
        )
        assert result.primary.category.value == "rate_limit"


# =============================================================================
# F-105: Instrument YAML schema expansion
# =============================================================================


class TestCliErrorConfigExpansion:
    """CliErrorConfig should have timeout/crash/capacity/stale pattern fields."""

    def test_timeout_patterns_field_exists(self) -> None:
        """CliErrorConfig has timeout_patterns field (pre-existing)."""
        from marianne.core.config.instruments import CliErrorConfig

        config = CliErrorConfig(timeout_patterns=["timed?.?out", "deadline exceeded"])
        assert config.timeout_patterns == ["timed?.?out", "deadline exceeded"]

    def test_crash_patterns_field_exists(self) -> None:
        """CliErrorConfig has crash_patterns field (new — F-105)."""
        from marianne.core.config.instruments import CliErrorConfig

        config = CliErrorConfig(crash_patterns=["segfault", "core dumped"])
        assert config.crash_patterns == ["segfault", "core dumped"]

    def test_capacity_patterns_field_exists(self) -> None:
        """CliErrorConfig has capacity_patterns field (pre-existing)."""
        from marianne.core.config.instruments import CliErrorConfig

        config = CliErrorConfig(capacity_patterns=["overloaded", "try again later"])
        assert config.capacity_patterns == ["overloaded", "try again later"]

    def test_stale_patterns_field_exists(self) -> None:
        """CliErrorConfig has stale_patterns field (new — F-105)."""
        from marianne.core.config.instruments import CliErrorConfig

        config = CliErrorConfig(stale_patterns=["no output", "idle"])
        assert config.stale_patterns == ["no output", "idle"]

    def test_new_fields_default_to_empty(self) -> None:
        """All pattern fields default to empty lists (backward compatible)."""
        from marianne.core.config.instruments import CliErrorConfig

        config = CliErrorConfig()
        assert config.timeout_patterns == []
        assert config.crash_patterns == []
        assert config.capacity_patterns == []
        assert config.stale_patterns == []

    def test_all_fields_coexist(self) -> None:
        """All fields — existing and new — work together."""
        from marianne.core.config.instruments import CliErrorConfig

        config = CliErrorConfig(
            success_exit_codes=[0],
            rate_limit_patterns=["429"],
            auth_error_patterns=["unauthorized"],
            timeout_patterns=["timeout"],
            crash_patterns=["segfault"],
            capacity_patterns=["overloaded"],
            stale_patterns=["no output"],
        )
        assert config.success_exit_codes == [0]
        assert config.rate_limit_patterns == ["429"]
        assert config.auth_error_patterns == ["unauthorized"]
        assert config.timeout_patterns == ["timeout"]
        assert config.crash_patterns == ["segfault"]
        assert config.capacity_patterns == ["overloaded"]
        assert config.stale_patterns == ["no output"]
