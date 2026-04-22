"""Tests for exit signal differentiation (Task 3).

Tests cover:
- ExecutionResult signal fields
- ClaudeCliBackend signal detection
- ErrorClassifier signal handling
- SheetState signal persistence
"""

import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.backends.base import ExecutionResult, ExitReason
from marianne.execution.instruments.claude_cli_legacy import ClaudeCliBackend
from marianne.core.checkpoint import SheetState, SheetStatus
from marianne.core.errors import (
    FATAL_SIGNALS,
    RETRIABLE_SIGNALS,
    ErrorCategory,
    ErrorClassifier,
    get_signal_name,
)

# ============================================================================
# ExecutionResult Tests
# ============================================================================


class TestExecutionResultSignalFields:
    """Test ExecutionResult with new signal fields."""

    def test_normal_exit_code(self) -> None:
        """Test result with normal exit code."""
        result = ExecutionResult(
            success=True,
            stdout="output",
            stderr="",
            duration_seconds=1.5,
            exit_code=0,
            exit_signal=None,
            exit_reason="completed",
        )
        assert result.exit_code == 0
        assert result.exit_signal is None
        assert result.exit_reason == "completed"
        assert result.success is True

    def test_signal_kill(self) -> None:
        """Test result when process killed by signal."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Process killed",
            duration_seconds=2.0,
            exit_code=None,
            exit_signal=signal.SIGTERM,
            exit_reason="killed",
        )
        assert result.exit_code is None
        assert result.exit_signal == signal.SIGTERM
        assert result.exit_reason == "killed"
        assert result.success is False

    def test_timeout_exit(self) -> None:
        """Test result when process timed out."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Timed out",
            duration_seconds=30.0,
            exit_code=None,
            exit_signal=signal.SIGKILL,
            exit_reason="timeout",
        )
        assert result.exit_code is None
        assert result.exit_signal == signal.SIGKILL
        assert result.exit_reason == "timeout"

    def test_error_exit(self) -> None:
        """Test result when internal error occurred."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Internal error",
            duration_seconds=0.1,
            exit_code=None,
            exit_signal=None,
            exit_reason="error",
        )
        assert result.exit_reason == "error"

    @pytest.mark.parametrize("reason", ["completed", "timeout", "killed", "error"])
    def test_exit_reason_types(self, reason: ExitReason) -> None:
        """Test all valid exit reason types."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="",
            duration_seconds=1.0,
            exit_code=None,
            exit_reason=reason,
        )
        assert result.exit_reason == reason


# ============================================================================
# ClaudeCliBackend Signal Tests
# ============================================================================


class TestClaudeCliBackendSignalNames:
    """Test signal name helper functions."""

    @pytest.mark.parametrize(
        ("sig", "expected"),
        [
            (signal.SIGTERM, "SIGTERM"),
            (signal.SIGKILL, "SIGKILL"),
            (signal.SIGSEGV, "SIGSEGV"),
            (signal.SIGABRT, "SIGABRT"),
            (signal.SIGHUP, "SIGHUP"),
            (signal.SIGINT, "SIGINT"),
            (signal.SIGPIPE, "SIGPIPE"),
        ],
    )
    def test_known_signal_names(self, sig: int, expected: str) -> None:
        """Test that known signals return correct names."""
        assert get_signal_name(sig) == expected

    def test_unknown_signal_name(self) -> None:
        """Test that unknown signals return 'signal N'."""
        # Use a signal number not in SIGNAL_NAMES
        unknown_sig = 99
        assert get_signal_name(unknown_sig) == "signal 99"


class TestClaudeCliBackendSignalDetection:
    """Test ClaudeCliBackend signal detection in execute()."""

    @pytest.fixture
    def backend(self) -> ClaudeCliBackend:
        """Create backend with mock claude path."""
        backend = ClaudeCliBackend(timeout_seconds=5.0)
        backend._claude_path = "/usr/bin/claude"
        return backend

    @pytest.mark.asyncio
    async def test_normal_exit_success(self, backend: ClaudeCliBackend) -> None:
        """Test normal successful exit."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.pid = 12345

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch.object(backend, "_stream_with_progress", return_value=(b"output", b"")),
        ):
            result = await backend.execute("test")

        assert result.success is True
        assert result.exit_code == 0
        assert result.exit_signal is None
        assert result.exit_reason == "completed"

    @pytest.mark.asyncio
    async def test_normal_exit_failure(self, backend: ClaudeCliBackend) -> None:
        """Test normal exit with non-zero code."""
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.pid = 12345

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch.object(backend, "_stream_with_progress", return_value=(b"", b"error")),
        ):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 1
        assert result.exit_signal is None
        assert result.exit_reason == "completed"

    @pytest.mark.asyncio
    async def test_signal_kill_detection(self, backend: ClaudeCliBackend) -> None:
        """Test detection of signal-killed process."""
        mock_process = AsyncMock()
        # Negative returncode means signal: -15 = SIGTERM
        mock_process.returncode = -signal.SIGTERM
        mock_process.pid = 12345

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch.object(backend, "_stream_with_progress", return_value=(b"partial", b"")),
        ):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code is None
        assert result.exit_signal == signal.SIGTERM
        assert result.exit_reason == "killed"
        assert "SIGTERM" in result.stderr

    @pytest.mark.asyncio
    async def test_segfault_detection(self, backend: ClaudeCliBackend) -> None:
        """Test detection of segmentation fault."""
        mock_process = AsyncMock()
        mock_process.returncode = -signal.SIGSEGV
        mock_process.pid = 12345

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch.object(backend, "_stream_with_progress", return_value=(b"", b"")),
        ):
            result = await backend.execute("test")

        assert result.exit_signal == signal.SIGSEGV
        assert result.exit_reason == "killed"
        assert "SIGSEGV" in result.stderr

    @pytest.mark.asyncio
    async def test_timeout_handling(self, backend: ClaudeCliBackend) -> None:
        """Test timeout produces correct signal info."""
        backend.timeout_seconds = 0.001  # Very short timeout

        mock_process = AsyncMock()
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()
        mock_process.pid = 12345
        mock_process.returncode = None

        async def _raise_timeout(*args, **kwargs):
            raise TimeoutError("timeout")

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch.object(backend, "_stream_with_progress", side_effect=_raise_timeout),
        ):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code is None
        # Backend sends SIGTERM first for graceful shutdown; SIGKILL only
        # escalates when the process doesn't exit within the grace period.
        # Mock process.wait() returns immediately, so SIGTERM succeeds.
        assert result.exit_signal == signal.SIGTERM
        assert result.exit_reason == "timeout"
        assert "timed out" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_file_not_found(self, backend: ClaudeCliBackend) -> None:
        """Test FileNotFoundError produces error exit reason."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 127
        assert result.exit_signal is None
        assert result.exit_reason == "error"

    @pytest.mark.asyncio
    async def test_general_exception(self, backend: ClaudeCliBackend) -> None:
        """Test general exception produces error exit reason."""
        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("unexpected")):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code is None
        assert result.exit_signal is None
        assert result.exit_reason == "error"


# ============================================================================
# ErrorClassifier Signal Tests
# ============================================================================


class TestErrorClassifierSignalHandling:
    """Test ErrorClassifier with signal-based exits."""

    @pytest.fixture
    def classifier(self) -> ErrorClassifier:
        """Create default classifier."""
        return ErrorClassifier()

    def test_timeout_signal(self, classifier: ErrorClassifier) -> None:
        """Test timeout with signal is classified as TIMEOUT."""
        result = classifier.classify(
            exit_signal=signal.SIGKILL,
            exit_reason="timeout",
        )
        assert result.category == ErrorCategory.TIMEOUT
        assert result.retriable is True
        assert result.exit_signal == signal.SIGKILL
        assert "timeout" in result.message.lower()

    def test_timeout_reason_without_signal(self, classifier: ErrorClassifier) -> None:
        """Test timeout reason alone is classified as TIMEOUT."""
        result = classifier.classify(
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.category == ErrorCategory.TIMEOUT
        assert result.retriable is True

    @pytest.mark.parametrize(
        ("sig", "expected_name"),
        [
            (signal.SIGSEGV, "SIGSEGV"),
            (signal.SIGABRT, "SIGABRT"),
            (signal.SIGBUS, "SIGBUS"),
        ],
    )
    def test_fatal_signals(
        self,
        classifier: ErrorClassifier,
        sig: int,
        expected_name: str,
    ) -> None:
        """Test fatal signals are classified as FATAL and non-retriable."""
        result = classifier.classify(
            exit_signal=sig,
            exit_reason="killed",
        )
        assert result.category == ErrorCategory.FATAL
        assert result.retriable is False
        assert "crashed" in result.message.lower()
        assert expected_name in result.message

    @pytest.mark.parametrize(
        "sig",
        [signal.SIGTERM, signal.SIGHUP, signal.SIGPIPE],
    )
    def test_retriable_signals(self, classifier: ErrorClassifier, sig: int) -> None:
        """Test retriable signals are classified as SIGNAL and retriable."""
        result = classifier.classify(
            exit_signal=sig,
            exit_reason="killed",
        )
        assert result.category == ErrorCategory.SIGNAL
        assert result.retriable is True
        assert result.suggested_wait_seconds is not None

    def test_sigint_not_retriable(self, classifier: ErrorClassifier) -> None:
        """Test SIGINT (user interrupt) is not retriable."""
        result = classifier.classify(
            exit_signal=signal.SIGINT,
            exit_reason="killed",
        )
        assert result.category == ErrorCategory.FATAL
        assert result.retriable is False
        assert "interrupted" in result.message.lower()

    @pytest.mark.parametrize(
        "oom_message",
        [
            "oom-killer invoked",
            "Out of memory: Kill process",
            "Cannot allocate memory",
            "memory cgroup limit exceeded",
        ],
    )
    def test_sigkill_with_oom_indicator(
        self,
        classifier: ErrorClassifier,
        oom_message: str,
    ) -> None:
        """Test SIGKILL with OOM indicator is FATAL."""
        result = classifier.classify(
            stdout="",
            stderr=oom_message,
            exit_signal=signal.SIGKILL,
            exit_reason="killed",
        )
        assert result.category == ErrorCategory.FATAL
        assert result.retriable is False
        assert "OOM" in result.message

    def test_sigkill_without_oom(self, classifier: ErrorClassifier) -> None:
        """Test SIGKILL without OOM is retriable SIGNAL."""
        result = classifier.classify(
            stdout="",
            stderr="Process killed",
            exit_signal=signal.SIGKILL,
            exit_reason="killed",
        )
        assert result.category == ErrorCategory.SIGNAL
        assert result.retriable is True

    def test_unknown_signal_retriable(self, classifier: ErrorClassifier) -> None:
        """Test unknown signal is conservatively retriable."""
        # Use a signal number not in our known sets
        unknown_sig = 64  # SIGRTMAX on Linux
        result = classifier.classify(
            exit_signal=unknown_sig,
            exit_reason="killed",
        )
        assert result.category == ErrorCategory.SIGNAL
        assert result.retriable is True

    def test_signal_properties(self, classifier: ErrorClassifier) -> None:
        """Test ClassifiedError signal properties."""
        result = classifier.classify(
            exit_signal=signal.SIGTERM,
            exit_reason="killed",
        )
        assert result.is_signal_kill is True
        assert result.signal_name == "SIGTERM"

    def test_non_signal_properties(self, classifier: ErrorClassifier) -> None:
        """Test ClassifiedError properties when not signal."""
        result = classifier.classify(exit_code=1)
        assert result.is_signal_kill is False
        assert result.signal_name is None


class TestErrorClassifierBackwardsCompatibility:
    """Test that existing classification still works."""

    @pytest.fixture
    def classifier(self) -> ErrorClassifier:
        """Create default classifier."""
        return ErrorClassifier()

    @pytest.mark.parametrize(
        ("output_kwargs", "expected_category", "expected_retriable"),
        [
            (
                {"stdout": "Error: rate limit exceeded", "exit_code": 1},
                ErrorCategory.RATE_LIMIT,
                True,
            ),
            ({"stderr": "401 Unauthorized", "exit_code": 1}, ErrorCategory.AUTH, False),
            ({"stderr": "connection refused", "exit_code": 1}, ErrorCategory.NETWORK, True),
            ({"exit_code": 124}, ErrorCategory.TIMEOUT, True),
            (
                {"stderr": "ENOENT: no such file or directory", "exit_code": 1},
                ErrorCategory.CONFIGURATION,
                True,
            ),
        ],
        ids=["rate_limit", "auth", "network", "timeout_124", "enoent"],
    )
    def test_pattern_classification_backwards_compat(
        self,
        classifier: ErrorClassifier,
        output_kwargs: dict,
        expected_category: ErrorCategory,
        expected_retriable: bool,
    ) -> None:
        """Test pattern-based classification is backwards-compatible."""
        result = classifier.classify(**output_kwargs)
        assert result.category == expected_category
        assert result.retriable is expected_retriable


# ============================================================================
# SheetState Signal Fields Tests
# ============================================================================


class TestSheetStateSignalFields:
    """Test SheetState with new signal fields."""

    def test_new_fields_default_none(self) -> None:
        """Test new fields default to None for backwards compatibility."""
        state = SheetState(sheet_num=1)
        assert state.exit_signal is None
        assert state.exit_reason is None
        assert state.execution_duration_seconds is None

    def test_set_signal_fields(self) -> None:
        """Test setting signal fields."""
        state = SheetState(
            sheet_num=1,
            exit_signal=signal.SIGTERM,
            exit_reason="killed",
            execution_duration_seconds=15.5,
        )
        assert state.exit_signal == signal.SIGTERM
        assert state.exit_reason == "killed"
        assert state.execution_duration_seconds == 15.5

    def test_serialization_with_signal(self) -> None:
        """Test JSON serialization includes signal fields."""
        state = SheetState(
            sheet_num=1,
            status=SheetStatus.FAILED,
            exit_signal=9,  # SIGKILL
            exit_reason="timeout",
            execution_duration_seconds=30.0,
        )
        data = state.model_dump()
        assert data["exit_signal"] == 9
        assert data["exit_reason"] == "timeout"
        assert data["execution_duration_seconds"] == 30.0

    def test_deserialization_missing_fields(self) -> None:
        """Test deserialization handles missing new fields (backwards compat)."""
        # Simulate old state file without new fields
        old_data = {
            "sheet_num": 1,
            "status": "completed",
            "exit_code": 0,
        }
        state = SheetState.model_validate(old_data)
        assert state.exit_signal is None
        assert state.exit_reason is None
        assert state.execution_duration_seconds is None

    def test_combined_with_exit_code(self) -> None:
        """Test state can have both exit_code and signal fields."""
        # Normal exit has exit_code, no signal
        state = SheetState(
            sheet_num=1,
            exit_code=0,
            exit_signal=None,
            exit_reason="completed",
            execution_duration_seconds=5.0,
        )
        assert state.exit_code == 0
        assert state.exit_signal is None

        # Signal exit has no exit_code, has signal
        state2 = SheetState(
            sheet_num=2,
            exit_code=None,
            exit_signal=15,
            exit_reason="killed",
            execution_duration_seconds=3.0,
        )
        assert state2.exit_code is None
        assert state2.exit_signal == 15


# ============================================================================
# Integration Tests
# ============================================================================


class TestSignalIntegration:
    """Integration tests for signal handling flow."""

    def test_execution_result_to_classified_error(self) -> None:
        """Test flow from ExecutionResult through ErrorClassifier."""
        # Simulate a SIGTERM kill
        result = ExecutionResult(
            success=False,
            stdout="partial output",
            stderr="[Process killed by SIGTERM]",
            duration_seconds=5.0,
            exit_code=None,
            exit_signal=signal.SIGTERM,
            exit_reason="killed",
        )

        classifier = ErrorClassifier()
        error = classifier.classify(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            exit_signal=result.exit_signal,
            exit_reason=result.exit_reason,
        )

        assert error.category == ErrorCategory.SIGNAL
        assert error.retriable is True
        assert error.exit_signal == signal.SIGTERM
        assert error.signal_name == "SIGTERM"

    def test_timeout_flow(self) -> None:
        """Test timeout flow through the system."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Timed out after 30s",
            duration_seconds=30.0,
            exit_code=None,
            exit_signal=signal.SIGKILL,
            exit_reason="timeout",
        )

        classifier = ErrorClassifier()
        error = classifier.classify(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            exit_signal=result.exit_signal,
            exit_reason=result.exit_reason,
        )

        # Should be TIMEOUT, not SIGNAL, because exit_reason is "timeout"
        assert error.category == ErrorCategory.TIMEOUT
        assert error.retriable is True
        assert error.exit_signal == signal.SIGKILL

    def test_sheet_state_from_execution_result(self) -> None:
        """Test populating SheetState from ExecutionResult."""
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Segmentation fault",
            duration_seconds=2.5,
            exit_code=None,
            exit_signal=signal.SIGSEGV,
            exit_reason="killed",
        )

        state = SheetState(
            sheet_num=1,
            status=SheetStatus.FAILED,
            exit_code=result.exit_code,
            exit_signal=result.exit_signal,
            exit_reason=result.exit_reason,
            execution_duration_seconds=result.duration_seconds,
            error_message="Segmentation fault",
        )

        assert state.exit_signal == signal.SIGSEGV
        assert state.exit_reason == "killed"
        assert state.execution_duration_seconds == 2.5


class TestSignalConstants:
    """Test signal constant definitions."""

    def test_retriable_signals_defined(self) -> None:
        """Test RETRIABLE_SIGNALS contains expected signals."""
        assert signal.SIGTERM in RETRIABLE_SIGNALS
        assert signal.SIGHUP in RETRIABLE_SIGNALS
        assert signal.SIGPIPE in RETRIABLE_SIGNALS
        # Fatal signals should NOT be in retriable
        assert signal.SIGSEGV not in RETRIABLE_SIGNALS

    def test_fatal_signals_defined(self) -> None:
        """Test FATAL_SIGNALS contains expected signals."""
        assert signal.SIGSEGV in FATAL_SIGNALS
        assert signal.SIGBUS in FATAL_SIGNALS
        assert signal.SIGABRT in FATAL_SIGNALS
        # Retriable signals should NOT be in fatal
        assert signal.SIGTERM not in FATAL_SIGNALS

    def test_signal_names_mapping(self) -> None:
        """Test get_signal_name maps correctly."""
        assert get_signal_name(signal.SIGTERM) == "SIGTERM"
        assert get_signal_name(signal.SIGKILL) == "SIGKILL"
