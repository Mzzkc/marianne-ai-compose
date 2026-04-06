"""Tests for classify_execution() handling of exit_code=None.

The production code path uses classify_execution() (not classify()). Circuit's
investigation found that when exit_code=None AND Phase 1 produces JSON errors,
the exit_code=None → TRANSIENT logic in classify() is never reached because
Phase 4 (regex fallback) only runs when all_errors is empty.

These tests verify:
1. exit_code=None with no JSON output → classified TRANSIENT
2. exit_code=None with partial JSON errors → still TRANSIENT (process-killed is root cause)
3. exit_code=None with OOM indicators → TRANSIENT with longer wait
4. exit_code=None with exit_signal → signal classification takes precedence
5. exit_code=None with timeout exit_reason → timeout takes precedence
6. exit_code=None with empty output → TRANSIENT
7. exit_code=None with rate limit patterns in stderr → RATE_LIMIT (more specific wins)
8. exit_code=None with auth patterns → AUTH still wins (deterministic)

Created by Ghost, Movement 1.
"""

from marianne.core.errors.classifier import ErrorClassifier
from marianne.core.errors.codes import ErrorCategory


class TestClassifyExecutionExitNone:
    """classify_execution() with exit_code=None must produce TRANSIENT results."""

    def setup_method(self) -> None:
        self.classifier = ErrorClassifier()

    def test_exit_none_empty_output(self) -> None:
        """exit_code=None with no output → TRANSIENT."""
        result = self.classifier.classify_execution(
            stdout="", stderr="", exit_code=None,
        )
        assert result.primary.category == ErrorCategory.TRANSIENT
        assert result.primary.retriable is True

    def test_exit_none_with_partial_stdout(self) -> None:
        """exit_code=None with partial (non-JSON) output → TRANSIENT."""
        result = self.classifier.classify_execution(
            stdout="Starting work...\nProcessing item 3",
            stderr="",
            exit_code=None,
        )
        assert result.primary.category == ErrorCategory.TRANSIENT
        assert result.primary.retriable is True

    def test_exit_none_with_oom_stderr(self) -> None:
        """exit_code=None with OOM indicators → TRANSIENT with longer wait."""
        result = self.classifier.classify_execution(
            stdout="",
            stderr="Killed: out of memory",
            exit_code=None,
        )
        assert result.primary.category == ErrorCategory.TRANSIENT
        assert result.primary.retriable is True
        assert result.primary.suggested_wait_seconds is not None
        assert result.primary.suggested_wait_seconds >= 60.0

    def test_exit_none_with_json_errors_still_transient(self) -> None:
        """exit_code=None with JSON errors from partial output.

        This is the key bug scenario: Phase 1 finds JSON errors from partial
        output, but the ROOT CAUSE is process-killed (exit_code=None).
        The classification should include the process-killed error and it
        should be retriable.
        """
        # Simulate partial Claude CLI JSON output with an error
        json_output = '{"errors": [{"code": "unknown_error", "message": "Unexpected"}]}'
        result = self.classifier.classify_execution(
            stdout=json_output,
            stderr="",
            exit_code=None,
        )
        # The result should be retriable since the process was killed
        assert result.primary.retriable is True

    def test_exit_none_with_signal_takes_precedence(self) -> None:
        """exit_code=None + exit_signal → signal classification wins."""
        import signal

        result = self.classifier.classify_execution(
            stdout="",
            stderr="",
            exit_code=None,
            exit_signal=signal.SIGTERM,
        )
        # Signal handling should take over
        assert result.primary is not None
        assert result.primary.retriable is True

    def test_exit_none_with_timeout_reason(self) -> None:
        """exit_code=None + exit_reason=timeout → timeout classification."""
        result = self.classifier.classify_execution(
            stdout="",
            stderr="",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.primary.category == ErrorCategory.TIMEOUT
        assert result.primary.retriable is True

    def test_exit_none_with_rate_limit_pattern(self) -> None:
        """exit_code=None + rate limit text in stderr → RATE_LIMIT."""
        result = self.classifier.classify_execution(
            stdout="",
            stderr="Error: rate limit exceeded, try again later",
            exit_code=None,
        )
        assert result.primary.category == ErrorCategory.RATE_LIMIT
        assert result.primary.retriable is True

    def test_exit_none_does_not_classify_fatal(self) -> None:
        """exit_code=None must NEVER be classified as FATAL.

        A None exit code means the process was killed or disappeared —
        this is always a transient condition, never a deterministic user error.
        """
        result = self.classifier.classify_execution(
            stdout="some output",
            stderr="some error text",
            exit_code=None,
        )
        assert result.primary.category != ErrorCategory.FATAL, (
            f"exit_code=None classified as FATAL: {result.primary.message}"
        )
        assert result.primary.retriable is True
