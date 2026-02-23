"""Tests for Mozart execution backends.

Tests cover:
- AnthropicApiBackend: API client, error handling, rate limit detection
- ClaudeCliBackend: Basic structure (CLI tests need integration tests)
- Backend logging: Verifies appropriate log levels and content
"""

import asyncio
import contextlib
import signal
import signal as signal_module
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

from mozart.backends.anthropic_api import AnthropicApiBackend
from mozart.backends.base import ExecutionResult
from mozart.backends.claude_cli import ClaudeCliBackend
from mozart.core.config import BackendConfig

# ============================================================================
# AnthropicApiBackend Tests
# ============================================================================


class TestAnthropicApiBackendInit:
    """Test AnthropicApiBackend initialization."""

    def test_init_defaults(self) -> None:
        """Test default initialization values."""
        backend = AnthropicApiBackend()
        assert backend.model == "claude-sonnet-4-20250514"
        assert backend.api_key_env == "ANTHROPIC_API_KEY"
        assert backend.max_tokens == 8192
        assert backend.temperature == 0.7
        assert backend.timeout_seconds == 300.0
        assert backend.name == "anthropic-api"

    def test_init_custom_values(self) -> None:
        """Test initialization with custom values."""
        backend = AnthropicApiBackend(
            model="claude-opus-4-20250514",
            api_key_env="MY_API_KEY",
            max_tokens=4096,
            temperature=0.5,
            timeout_seconds=60.0,
        )
        assert backend.model == "claude-opus-4-20250514"
        assert backend.api_key_env == "MY_API_KEY"
        assert backend.max_tokens == 4096
        assert backend.temperature == 0.5
        assert backend.timeout_seconds == 60.0

    def test_from_config(self) -> None:
        """Test creating backend from config."""
        config = BackendConfig(
            type="anthropic_api",
            model="claude-opus-4-20250514",
            api_key_env="CUSTOM_KEY",
            max_tokens=2048,
            temperature=0.3,
        )
        backend = AnthropicApiBackend.from_config(config)
        assert backend.model == "claude-opus-4-20250514"
        assert backend.api_key_env == "CUSTOM_KEY"
        assert backend.max_tokens == 2048
        assert backend.temperature == 0.3


class TestAnthropicApiBackendExecute:
    """Test AnthropicApiBackend execute method."""

    @pytest.fixture
    def backend_with_key(self, monkeypatch: pytest.MonkeyPatch) -> AnthropicApiBackend:
        """Create backend with API key set."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
        return AnthropicApiBackend()

    @pytest.mark.asyncio
    async def test_execute_success(self, backend_with_key: AnthropicApiBackend) -> None:
        """Test successful API execution."""
        # Mock the response
        mock_content_block = MagicMock()
        mock_content_block.text = "Hello, world!"

        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5

        mock_response = MagicMock()
        mock_response.content = [mock_content_block]
        mock_response.usage = mock_usage

        # Mock the client
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(backend_with_key, "_get_client", return_value=mock_client):
            result = await backend_with_key.execute("Say hello")

        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "Hello, world!"
        assert result.stderr == ""
        assert result.tokens_used == 15
        assert result.model == "claude-sonnet-4-20250514"
        assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_execute_rate_limit_error(
        self, backend_with_key: AnthropicApiBackend
    ) -> None:
        """Test rate limit error handling."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.RateLimitError(
                message="Rate limit exceeded",
                response=mock_response,
                body=None,
            )
        )

        with patch.object(backend_with_key, "_get_client", return_value=mock_client):
            result = await backend_with_key.execute("Test prompt")

        assert result.success is False
        assert result.exit_code == 429
        assert result.rate_limited is True
        assert result.error_type == "rate_limit"
        assert "rate limit" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_authentication_error(
        self, backend_with_key: AnthropicApiBackend
    ) -> None:
        """Test authentication error handling."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="Invalid API key",
                response=mock_response,
                body=None,
            )
        )

        with patch.object(backend_with_key, "_get_client", return_value=mock_client):
            result = await backend_with_key.execute("Test prompt")

        assert result.success is False
        assert result.exit_code == 401
        assert result.error_type == "authentication"
        assert "authentication" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_bad_request_error(
        self, backend_with_key: AnthropicApiBackend
    ) -> None:
        """Test bad request error handling."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.BadRequestError(
                message="Invalid request",
                response=mock_response,
                body=None,
            )
        )

        with patch.object(backend_with_key, "_get_client", return_value=mock_client):
            result = await backend_with_key.execute("Test prompt")

        assert result.success is False
        assert result.exit_code == 400
        assert result.error_type == "bad_request"

    @pytest.mark.asyncio
    async def test_execute_timeout_error(
        self, backend_with_key: AnthropicApiBackend
    ) -> None:
        """Test timeout error handling."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APITimeoutError(request=MagicMock())
        )

        with patch.object(backend_with_key, "_get_client", return_value=mock_client):
            result = await backend_with_key.execute("Test prompt")

        assert result.success is False
        assert result.exit_code == 408
        assert result.error_type == "timeout"

    @pytest.mark.asyncio
    async def test_execute_connection_error(
        self, backend_with_key: AnthropicApiBackend
    ) -> None:
        """Test connection error handling."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APIConnectionError(request=MagicMock())
        )

        with patch.object(backend_with_key, "_get_client", return_value=mock_client):
            result = await backend_with_key.execute("Test prompt")

        assert result.success is False
        assert result.exit_code == 503
        assert result.error_type == "connection"

    @pytest.mark.asyncio
    async def test_execute_no_api_key(self) -> None:
        """Test error when API key is not set."""
        backend = AnthropicApiBackend(api_key_env="NONEXISTENT_KEY")
        result = await backend.execute("Test prompt")

        assert result.success is False
        assert result.exit_code == 1
        assert result.error_type == "configuration"
        assert "NONEXISTENT_KEY" in result.error_message


class TestAnthropicApiBackendRateLimitDetection:
    """Test rate limit detection patterns."""

    @pytest.fixture
    def backend(self) -> AnthropicApiBackend:
        """Create backend for testing."""
        return AnthropicApiBackend()

    def test_detect_rate_limit_patterns(self, backend: AnthropicApiBackend) -> None:
        """Test various rate limit patterns are detected with non-zero exit code."""
        patterns = [
            "rate limit exceeded",
            "Rate-Limit: exceeded",
            "usage limit reached",
            "quota exceeded",
            "too many requests",
            "error 429",
            "capacity exceeded",
            "try again later",
        ]
        for pattern in patterns:
            assert backend._detect_rate_limit(pattern, exit_code=1) is True, (
                f"Failed for: {pattern}"
            )

    def test_no_rate_limit_detected(self, backend: AnthropicApiBackend) -> None:
        """Test that normal messages don't trigger rate limit detection."""
        normal_messages = [
            "Success",
            "Hello, world!",
            "Error: invalid input",
            "Connection failed",
        ]
        for message in normal_messages:
            assert backend._detect_rate_limit(message, exit_code=1) is False, (
                f"Failed for: {message}"
            )

    def test_exit_code_zero_never_rate_limited(self, backend: AnthropicApiBackend) -> None:
        """Successful execution (exit_code=0) should never be classified as rate-limited."""
        assert backend._detect_rate_limit("capacity exceeded", "", exit_code=0) is False
        assert backend._detect_rate_limit("rate limit exceeded", "", exit_code=0) is False
        assert backend._detect_rate_limit("429 error", "", exit_code=0) is False

    def test_exit_code_one_with_rate_limit_text(self, backend: AnthropicApiBackend) -> None:
        """Failed execution with rate limit text should still be detected."""
        assert backend._detect_rate_limit("rate limit exceeded", "", exit_code=1) is True

    def test_exit_code_none_not_rate_limited(self, backend: AnthropicApiBackend) -> None:
        """When exit_code is None (e.g. signal kill), don't falsely classify as rate-limited."""
        assert backend._detect_rate_limit("rate limit exceeded", "", exit_code=None) is False

    def test_exit_code_none_all_patterns_not_rate_limited(
        self, backend: AnthropicApiBackend,
    ) -> None:
        """All rate limit patterns must return False when exit_code is None (signal/timeout)."""
        patterns = [
            "rate limit exceeded",
            "usage limit reached",
            "quota exceeded",
            "too many requests",
            "error 429",
            "capacity exceeded",
            "try again later",
        ]
        for pattern in patterns:
            assert backend._detect_rate_limit(pattern, "", exit_code=None) is False, (
                f"Pattern '{pattern}' should not trigger rate limit with exit_code=None"
            )

    def test_exit_code_none_stderr_not_rate_limited(self, backend: AnthropicApiBackend) -> None:
        """Rate limit text in stderr should also not trigger when exit_code is None."""
        assert backend._detect_rate_limit("", "rate limit exceeded", exit_code=None) is False
        assert backend._detect_rate_limit("", "429 Too Many Requests", exit_code=None) is False


class TestAnthropicApiBackendHealthCheck:
    """Test AnthropicApiBackend health check."""

    @pytest.mark.asyncio
    async def test_health_check_no_api_key(self) -> None:
        """Test health check fails without API key."""
        backend = AnthropicApiBackend(api_key_env="NONEXISTENT_KEY")
        result = await backend.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test successful health check."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        backend = AnthropicApiBackend()

        mock_content_block = MagicMock()
        mock_content_block.text = "ok"

        mock_response = MagicMock()
        mock_response.content = [mock_content_block]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test health check failure on API error."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        backend = AnthropicApiBackend()

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APIError(
                "API error", request=httpx.Request("POST", "https://api.anthropic.com"), body=None,
            )
        )

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.health_check()

        assert result is False


# ============================================================================
# ClaudeCliBackend Tests
# ============================================================================


class TestClaudeCliBackendInit:
    """Test ClaudeCliBackend initialization."""

    def test_init_defaults(self) -> None:
        """Test default initialization values."""
        backend = ClaudeCliBackend()
        assert backend.skip_permissions is True
        assert backend.disable_mcp is True  # Default for faster execution
        assert backend.output_format == "text"  # Default for human-readable output
        assert backend.cli_model is None
        assert backend.allowed_tools is None
        assert backend.system_prompt_file is None
        assert backend.working_directory is None
        assert backend.timeout_seconds == 1800.0
        assert backend.name == "claude-cli"

    def test_init_custom_values(self) -> None:
        """Test initialization with custom values."""
        from pathlib import Path

        backend = ClaudeCliBackend(
            skip_permissions=False,
            output_format="json",
            working_directory=Path("/tmp"),
            timeout_seconds=60.0,
        )
        assert backend.skip_permissions is False
        assert backend.output_format == "json"
        assert backend.working_directory == Path("/tmp")
        assert backend.timeout_seconds == 60.0

    def test_from_config(self) -> None:
        """Test creating backend from config."""
        from pathlib import Path

        config = BackendConfig(
            type="claude_cli",
            skip_permissions=False,
            output_format="json",
            working_directory=Path("/tmp"),
            timeout_seconds=120.0,
        )
        backend = ClaudeCliBackend.from_config(config)
        assert backend.skip_permissions is False
        assert backend.output_format == "json"
        assert backend.working_directory == Path("/tmp")
        assert backend.timeout_seconds == 120.0


class TestClaudeCliBackendTimeoutOverride:
    """Test ClaudeCliBackend per-call timeout override."""

    @pytest.fixture
    def backend(self) -> ClaudeCliBackend:
        """Create backend for testing."""
        return ClaudeCliBackend(timeout_seconds=1800.0)

    @pytest.mark.asyncio
    async def test_execute_passes_timeout_to_impl(self, backend: ClaudeCliBackend) -> None:
        """Test that execute() forwards timeout_seconds to _execute_impl."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(
            backend, "_execute_impl", new_callable=AsyncMock, return_value=mock_result,
        ) as mock_impl:
            await backend.execute("test prompt", timeout_seconds=60.0)
            mock_impl.assert_called_once_with("test prompt", timeout_seconds=60.0)

    @pytest.mark.asyncio
    async def test_execute_default_timeout_passes_none(self, backend: ClaudeCliBackend) -> None:
        """Test that execute() without timeout override passes None."""
        mock_result = MagicMock()
        mock_result.success = True

        with patch.object(
            backend, "_execute_impl", new_callable=AsyncMock, return_value=mock_result,
        ) as mock_impl:
            await backend.execute("test prompt")
            mock_impl.assert_called_once_with("test prompt", timeout_seconds=None)

    @pytest.mark.asyncio
    async def test_execute_impl_uses_override_timeout(self, backend: ClaudeCliBackend) -> None:
        """Test that _execute_impl uses override when provided."""
        # Mock subprocess to avoid real CLI calls
        mock_process = AsyncMock()
        mock_process.pid = 12345
        mock_process.returncode = 0

        captured_timeout: list[float | None] = []

        async def _capture_stream(_proc, _start, _notify, *, effective_timeout=None):
            captured_timeout.append(effective_timeout)
            return (b"output", b"")

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch.object(backend, "_build_command", return_value=["claude", "-p", "test"]),
            patch.object(backend, "_prepare_log_files"),
            patch.object(backend, "_stream_with_progress", side_effect=_capture_stream),
        ):
            with contextlib.suppress(Exception):
                await backend._execute_impl("test", timeout_seconds=60.0)
            assert captured_timeout == [60.0]


class TestClaudeCliBackendTimeoutOutputPreservation:
    """Test that partial output survives timeout (Q013 / GH#8)."""

    @pytest.fixture
    def backend(self) -> ClaudeCliBackend:
        return ClaudeCliBackend(timeout_seconds=5.0)

    @pytest.mark.asyncio
    async def test_partial_output_preserved_on_timeout(self, backend: ClaudeCliBackend) -> None:
        """When execution times out, any output collected before timeout is preserved."""
        mock_process = AsyncMock()
        mock_process.pid = 12345
        mock_process.returncode = None

        # Simulate streaming that collects partial output then times out
        async def _timeout_stream(_proc, _start, _notify, *, effective_timeout=None):
            # Simulate partial output accumulated before timeout
            backend._partial_stdout_chunks = [b"partial ", b"output here"]
            backend._partial_stderr_chunks = [b"some error"]
            raise TimeoutError("Execution timeout exceeded")

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch.object(backend, "_build_command", return_value=["claude", "-p", "test"]),
            patch.object(backend, "_prepare_log_files"),
            patch.object(backend, "_stream_with_progress", side_effect=_timeout_stream),
            patch.object(backend, "_handle_execution_timeout") as mock_timeout,
        ):
            mock_timeout.return_value = ExecutionResult(
                success=False, exit_code=None, exit_signal=None,
                exit_reason="timeout", stdout="partial output here",
                stderr="some error\nCommand timed out after 5.0s",
                duration_seconds=5.0, error_type="timeout",
                error_message="Timed out after 5.0s",
            )
            result = await backend._execute_impl("test")
            assert result.stdout == "partial output here"
            assert "some error" in result.stderr

    @pytest.mark.asyncio
    async def test_no_progress_callback_still_streams(self, backend: ClaudeCliBackend) -> None:
        """Without progress_callback, streaming path is still used (not communicate())."""
        assert backend.progress_callback is None  # No callback set

        mock_process = AsyncMock()
        mock_process.pid = 12345
        mock_process.returncode = 0

        stream_called = False

        async def _track_stream(_proc, _start, _notify, *, effective_timeout=None):
            nonlocal stream_called
            stream_called = True
            return (b"output", b"")

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch.object(backend, "_build_command", return_value=["claude", "-p", "test"]),
            patch.object(backend, "_prepare_log_files"),
            patch.object(backend, "_stream_with_progress", side_effect=_track_stream),
        ):
            await backend._execute_impl("test")
            assert stream_called, "Streaming path must be used even without progress_callback"


    @pytest.mark.asyncio
    async def test_timeout_with_empty_output(self, backend: ClaudeCliBackend) -> None:
        """Timeout with no output collected should return empty strings, not crash."""
        result = await backend._handle_execution_timeout(
            process=AsyncMock(
                terminate=MagicMock(),
                wait=AsyncMock(),
                kill=MagicMock(),
                returncode=-9,
            ),
            start_time=time.monotonic() - 10.0,
            bytes_received=0,
            lines_received=0,
        )
        assert result.success is False
        assert result.exit_reason == "timeout"
        assert result.stdout == ""
        assert "timed out" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_timeout_preserves_large_partial_output(self, backend: ClaudeCliBackend) -> None:
        """Partial output >10KB is preserved without truncation on timeout."""
        # Simulate large output accumulated across many chunks
        large_chunk = b"x" * 5000
        backend._partial_stdout_chunks = [large_chunk, large_chunk, large_chunk]
        backend._partial_stderr_chunks = [b"err"]

        result = await backend._handle_execution_timeout(
            process=AsyncMock(
                terminate=MagicMock(),
                wait=AsyncMock(),
                kill=MagicMock(),
                returncode=-9,
            ),
            start_time=time.monotonic() - 30.0,
            bytes_received=15001,
            lines_received=0,
        )
        assert len(result.stdout) == 15000
        assert result.stderr.startswith("err")

    @pytest.mark.asyncio
    async def test_graceful_termination_before_kill(self, backend: ClaudeCliBackend) -> None:
        """Timeout handler tries SIGTERM before SIGKILL."""
        mock_process = AsyncMock()
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        # Process exits after SIGTERM (no escalation to SIGKILL)
        mock_process.wait = AsyncMock(return_value=0)

        backend._partial_stdout_chunks = []
        backend._partial_stderr_chunks = []

        result = await backend._handle_execution_timeout(
            process=mock_process,
            start_time=time.monotonic() - 5.0,
            bytes_received=0,
            lines_received=0,
        )
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_not_called()
        assert result.exit_signal == signal.SIGTERM


class TestClaudeCliBackendRateLimitDetection:
    """Test ClaudeCliBackend rate limit detection."""

    @pytest.fixture
    def backend(self) -> ClaudeCliBackend:
        """Create backend for testing."""
        return ClaudeCliBackend()

    def test_detect_rate_limit_patterns(self, backend: ClaudeCliBackend) -> None:
        """Test various rate limit patterns are detected with non-zero exit code."""
        patterns = [
            "rate limit exceeded",
            "usage limit reached",
            "quota exceeded",
            "too many requests",
            "429",
            "capacity exceeded",
            "try again later",
        ]
        for pattern in patterns:
            assert (
                backend._detect_rate_limit(pattern, "", exit_code=1) is True
            ), f"Failed for: {pattern}"

    def test_no_rate_limit_detected(self, backend: ClaudeCliBackend) -> None:
        """Test that normal messages don't trigger rate limit detection."""
        normal_messages = [
            "Success",
            "Hello, world!",
            "Error: invalid input",
        ]
        for message in normal_messages:
            assert (
                backend._detect_rate_limit(message, "") is False
            ), f"Failed for: {message}"

    def test_exit_code_zero_never_rate_limited(self, backend: ClaudeCliBackend) -> None:
        """Successful execution (exit_code=0) should never be classified as rate-limited."""
        assert backend._detect_rate_limit("capacity exceeded", "", exit_code=0) is False
        assert backend._detect_rate_limit("rate limit exceeded", "", exit_code=0) is False
        assert backend._detect_rate_limit("429 error", "", exit_code=0) is False

    def test_exit_code_one_with_rate_limit_text(self, backend: ClaudeCliBackend) -> None:
        """Failed execution with rate limit text should still be detected."""
        assert backend._detect_rate_limit("rate limit exceeded", "", exit_code=1) is True

    def test_exit_code_none_not_rate_limited(self, backend: ClaudeCliBackend) -> None:
        """When exit_code is None (e.g. signal kill), don't falsely classify as rate-limited."""
        assert backend._detect_rate_limit("rate limit exceeded", "", exit_code=None) is False

    def test_exit_code_none_all_patterns_not_rate_limited(self, backend: ClaudeCliBackend) -> None:
        """All rate limit patterns must return False when exit_code is None (signal/timeout)."""
        patterns = [
            "rate limit exceeded",
            "usage limit reached",
            "quota exceeded",
            "too many requests",
            "429",
            "capacity exceeded",
            "try again later",
        ]
        for pattern in patterns:
            assert backend._detect_rate_limit(pattern, "", exit_code=None) is False, (
                f"Pattern '{pattern}' should not trigger rate limit with exit_code=None"
            )

    def test_exit_code_none_stderr_not_rate_limited(self, backend: ClaudeCliBackend) -> None:
        """Rate limit text in stderr should also not trigger when exit_code is None."""
        assert backend._detect_rate_limit("", "rate limit exceeded", exit_code=None) is False
        assert backend._detect_rate_limit("", "429 Too Many Requests", exit_code=None) is False


class TestClaudeCliBackendPreamble:
    """Test dynamic preamble injection via set_preamble()."""

    @pytest.fixture
    def backend(self) -> ClaudeCliBackend:
        """Create backend for testing."""
        return ClaudeCliBackend()

    def test_preamble_injected_when_set(self, backend: ClaudeCliBackend) -> None:
        """Test that set_preamble() content is prepended to prompts."""
        backend.set_preamble("<mozart-preamble>Test preamble</mozart-preamble>")
        result = backend._inject_preamble_and_extensions("Do something")

        assert "Test preamble" in result
        assert "Do something" in result
        assert result.index("Test preamble") < result.index("Do something")

    def test_no_preamble_no_wrapping(self, backend: ClaudeCliBackend) -> None:
        """Without set_preamble(), prompt is returned unchanged."""
        result = backend._inject_preamble_and_extensions("My prompt")
        assert result == "My prompt"

    def test_preamble_cleared_with_none(self, backend: ClaudeCliBackend) -> None:
        """set_preamble(None) clears any previous preamble."""
        backend.set_preamble("<mozart-preamble>First</mozart-preamble>")
        backend.set_preamble(None)
        result = backend._inject_preamble_and_extensions("My prompt")
        assert result == "My prompt"

    def test_build_command_includes_preamble(self, backend: ClaudeCliBackend) -> None:
        """Test that _build_command injects the preamble."""
        backend._claude_path = "/usr/bin/claude"
        backend.set_preamble("<mozart-preamble>Dynamic</mozart-preamble>")

        cmd = backend._build_command("My original prompt")
        prompt_arg = cmd[cmd.index("-p") + 1]

        assert "<mozart-preamble>" in prompt_arg
        assert "My original prompt" in prompt_arg


class TestClaudeCliBackendPromptExtensions:
    """Test prompt extension injection (GH#76)."""

    @pytest.fixture
    def backend(self) -> ClaudeCliBackend:
        """Create backend for testing."""
        return ClaudeCliBackend()

    def test_extensions_default_empty(self, backend: ClaudeCliBackend) -> None:
        """Backend starts with no prompt extensions."""
        assert backend._prompt_extensions == []

    def test_set_prompt_extensions(self, backend: ClaudeCliBackend) -> None:
        """set_prompt_extensions stores non-empty extensions."""
        backend.set_prompt_extensions(["Be thorough", "Write tests"])
        assert len(backend._prompt_extensions) == 2

    def test_set_prompt_extensions_filters_empty(self, backend: ClaudeCliBackend) -> None:
        """set_prompt_extensions filters out empty/whitespace-only strings."""
        backend.set_prompt_extensions(["Valid", "", "  ", "Also valid"])
        assert len(backend._prompt_extensions) == 2
        assert "Valid" in backend._prompt_extensions

    def test_extensions_injected_into_prompt(self, backend: ClaudeCliBackend) -> None:
        """Extensions appear in the injected prompt."""
        backend.set_prompt_extensions(["Always review edge cases"])
        result = backend._inject_preamble_and_extensions("Do the task")
        assert "Always review edge cases" in result
        assert "Do the task" in result

    def test_no_extensions_no_extra_content(self, backend: ClaudeCliBackend) -> None:
        """Without extensions or preamble, prompt is unchanged."""
        result = backend._inject_preamble_and_extensions("Do the task")
        assert result == "Do the task"

    def test_preamble_and_extensions_together(self, backend: ClaudeCliBackend) -> None:
        """Preamble + extensions + prompt are all present in correct order."""
        backend.set_preamble("<mozart-preamble>Context</mozart-preamble>")
        backend.set_prompt_extensions(["Custom directive"])
        result = backend._inject_preamble_and_extensions("My prompt")
        assert result.index("Context") < result.index("My prompt") < result.index("Custom directive")

    def test_extensions_in_build_command(self, backend: ClaudeCliBackend) -> None:
        """Extensions flow through _build_command to the CLI args."""
        backend._claude_path = "/usr/bin/claude"
        backend.set_prompt_extensions(["Custom directive"])
        cmd = backend._build_command("My prompt")
        prompt_arg = cmd[cmd.index("-p") + 1]
        assert "Custom directive" in prompt_arg
        assert "My prompt" in prompt_arg


class TestClaudeCliBackendHealthCheck:
    """Test ClaudeCliBackend.health_check()."""

    @pytest.mark.asyncio
    async def test_health_check_no_claude_path(self) -> None:
        """health_check returns False when claude CLI path is not found."""
        backend = ClaudeCliBackend()
        backend._claude_path = None
        assert await backend.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        """health_check returns True when CLI responds with 'ready'."""
        backend = ClaudeCliBackend()
        backend._claude_path = "/usr/bin/claude"
        mock_result = ExecutionResult(
            success=True, stdout="ready", stderr="", duration_seconds=0.5
        )
        backend._execute_impl = AsyncMock(return_value=mock_result)
        assert await backend.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure_not_ready(self) -> None:
        """health_check returns False when CLI responds without 'ready'."""
        backend = ClaudeCliBackend()
        backend._claude_path = "/usr/bin/claude"
        mock_result = ExecutionResult(
            success=True, stdout="something else", stderr="", duration_seconds=0.5
        )
        backend._execute_impl = AsyncMock(return_value=mock_result)
        assert await backend.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_exception(self) -> None:
        """health_check returns False when _execute_impl raises."""
        backend = ClaudeCliBackend()
        backend._claude_path = "/usr/bin/claude"
        backend._execute_impl = AsyncMock(side_effect=RuntimeError("connection failed"))
        assert await backend.health_check() is False


# ============================================================================
# Backend Logging Tests
# ============================================================================


class TestBackendLogging:
    """Test that backends produce appropriate log messages.

    Uses structlog's testing utilities to capture and verify log output.
    These tests verify that:
    - Correct log levels are used for different scenarios
    - Sensitive data is NOT logged (API keys, tokens)
    - Appropriate context is included (duration, error types, etc.)
    """

    @pytest.fixture
    def captured_logs(self) -> list[dict[str, Any]]:
        """Return a list that will capture log entries."""
        return []

    @pytest.fixture
    def configure_test_logging(
        self, captured_logs: list[dict[str, Any]]
    ) -> None:
        """Configure structlog to capture logs for testing."""
        import structlog
        from structlog.types import EventDict, WrappedLogger

        def capture_to_list(
            logger: WrappedLogger, method_name: str, event_dict: EventDict
        ) -> EventDict:
            """Processor that captures logs to a list."""
            captured_logs.append({"level": method_name, **event_dict})
            raise structlog.DropEvent

        structlog.configure(
            processors=[capture_to_list],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=False,
        )

    @pytest.mark.asyncio
    async def test_anthropic_api_logs_request_at_debug(
        self,
        monkeypatch: pytest.MonkeyPatch,
        configure_test_logging: None,
        captured_logs: list[dict[str, Any]],
    ) -> None:
        """Test that API requests are logged at DEBUG level."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        backend = AnthropicApiBackend()

        # Mock successful response
        mock_content = MagicMock()
        mock_content.text = "response"
        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_response.usage = mock_usage

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            await backend.execute("test prompt")

        # Find the api_request log entry
        request_logs = [log for log in captured_logs if log.get("event") == "api_request"]
        assert len(request_logs) == 1
        assert request_logs[0]["level"] == "debug"
        assert request_logs[0]["model"] == "claude-sonnet-4-20250514"
        assert request_logs[0]["prompt_length"] == 11  # len("test prompt")
        # Ensure API key is NOT logged
        assert "api_key" not in request_logs[0]
        assert "test-key" not in str(request_logs[0])

    @pytest.mark.asyncio
    async def test_anthropic_api_logs_response_at_info(
        self,
        monkeypatch: pytest.MonkeyPatch,
        configure_test_logging: None,
        captured_logs: list[dict[str, Any]],
    ) -> None:
        """Test that successful API responses are logged at INFO level."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        backend = AnthropicApiBackend()

        mock_content = MagicMock()
        mock_content.text = "response text"
        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_response.usage = mock_usage

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            await backend.execute("test prompt")

        # Find the api_response log entry
        response_logs = [log for log in captured_logs if log.get("event") == "api_response"]
        assert len(response_logs) == 1
        assert response_logs[0]["level"] == "info"
        assert response_logs[0]["input_tokens"] == 10
        assert response_logs[0]["output_tokens"] == 5
        assert response_logs[0]["total_tokens"] == 15

    @pytest.mark.asyncio
    async def test_anthropic_api_logs_rate_limit_at_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        configure_test_logging: None,
        captured_logs: list[dict[str, Any]],
    ) -> None:
        """Test that rate limit errors are logged at WARNING level."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        backend = AnthropicApiBackend()

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.RateLimitError(
                message="Rate limit exceeded",
                response=mock_response,
                body=None,
            )
        )

        with patch.object(backend, "_get_client", return_value=mock_client):
            await backend.execute("test prompt")

        # Find the rate_limit_error log entry
        rate_limit_logs = [
            log for log in captured_logs if log.get("event") == "rate_limit_error"
        ]
        assert len(rate_limit_logs) == 1
        assert rate_limit_logs[0]["level"] == "warning"

    @pytest.mark.asyncio
    async def test_anthropic_api_logs_auth_error_at_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        configure_test_logging: None,
        captured_logs: list[dict[str, Any]],
    ) -> None:
        """Test that authentication errors are logged at ERROR level."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        backend = AnthropicApiBackend()

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="Invalid API key",
                response=mock_response,
                body=None,
            )
        )

        with patch.object(backend, "_get_client", return_value=mock_client):
            await backend.execute("test prompt")

        # Find the authentication_error log entry
        auth_logs = [
            log for log in captured_logs if log.get("event") == "authentication_error"
        ]
        assert len(auth_logs) == 1
        assert auth_logs[0]["level"] == "error"
        # Ensure only env var name is logged, not the actual key
        assert auth_logs[0].get("api_key_env") == "ANTHROPIC_API_KEY"
        assert "test-key" not in str(auth_logs[0])

    @pytest.mark.asyncio
    async def test_anthropic_api_never_logs_api_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
        configure_test_logging: None,
        captured_logs: list[dict[str, Any]],
    ) -> None:
        """Test that API keys are NEVER logged in any scenario."""
        test_key = "sk-ant-test-key-12345"
        monkeypatch.setenv("ANTHROPIC_API_KEY", test_key)
        backend = AnthropicApiBackend()

        # Trigger a configuration error by mocking client creation to fail
        with patch.object(
            backend, "_get_client", side_effect=RuntimeError("API key error")
        ):
            await backend.execute("test prompt")

        # Check ALL log entries to ensure the API key never appears
        all_log_content = str(captured_logs)
        assert test_key not in all_log_content


class TestRecursiveLightBackendLogging:
    """Test Recursive Light backend logging."""

    @pytest.fixture
    def captured_logs(self) -> list[dict[str, Any]]:
        """Return a list that will capture log entries."""
        return []

    @pytest.fixture
    def configure_test_logging(
        self, captured_logs: list[dict[str, Any]]
    ) -> None:
        """Configure structlog to capture logs for testing."""
        import structlog
        from structlog.types import EventDict, WrappedLogger

        def capture_to_list(
            logger: WrappedLogger, method_name: str, event_dict: EventDict
        ) -> EventDict:
            """Processor that captures logs to a list."""
            captured_logs.append({"level": method_name, **event_dict})
            raise structlog.DropEvent

        structlog.configure(
            processors=[capture_to_list],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=False,
        )

    @pytest.mark.asyncio
    async def test_recursive_light_logs_request_at_debug(
        self,
        configure_test_logging: None,
        captured_logs: list[dict[str, Any]],
    ) -> None:
        """Test that HTTP requests are logged at DEBUG level."""
        from mozart.backends.recursive_light import RecursiveLightBackend

        backend = RecursiveLightBackend(rl_endpoint="http://test:8080")

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "test response", "confidence": 0.9}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            await backend.execute("test prompt")

        # Find the http_request log entry
        request_logs = [log for log in captured_logs if log.get("event") == "http_request"]
        assert len(request_logs) == 1
        assert request_logs[0]["level"] == "debug"
        assert "test:8080" in request_logs[0]["endpoint"]
        assert request_logs[0]["prompt_length"] == 11

    @pytest.mark.asyncio
    async def test_recursive_light_logs_response(
        self,
        configure_test_logging: None,
        captured_logs: list[dict[str, Any]],
    ) -> None:
        """Test that successful responses are logged at INFO level."""
        from mozart.backends.recursive_light import RecursiveLightBackend

        backend = RecursiveLightBackend(rl_endpoint="http://test:8080")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "test response",
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            await backend.execute("test prompt")

        # Find the http_response log entry
        response_logs = [log for log in captured_logs if log.get("event") == "http_response"]
        assert len(response_logs) == 1
        assert response_logs[0]["level"] == "info"
        assert response_logs[0]["response_length"] == len("test response")

    @pytest.mark.asyncio
    async def test_recursive_light_logs_connection_error_at_warning(
        self,
        configure_test_logging: None,
        captured_logs: list[dict[str, Any]],
    ) -> None:
        """Test that connection errors are logged at WARNING level."""
        from mozart.backends.recursive_light import RecursiveLightBackend

        backend = RecursiveLightBackend(rl_endpoint="http://test:8080")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with patch.object(backend, "_get_client", return_value=mock_client):
            await backend.execute("test prompt")

        # Find the connection_error log entry
        conn_logs = [log for log in captured_logs if log.get("event") == "connection_error"]
        assert len(conn_logs) == 1
        assert conn_logs[0]["level"] == "warning"
        assert "test:8080" in conn_logs[0]["endpoint"]


# ============================================================================
# ClaudeCliBackend._build_command() Tests (FIX-43)
# ============================================================================


class TestBuildCommand:
    """Tests for ClaudeCliBackend._build_command() subprocess command construction."""

    def _make_backend(self, **kwargs: Any) -> ClaudeCliBackend:
        """Create a backend with a fake claude path for testing."""
        backend = ClaudeCliBackend(**kwargs)
        backend._claude_path = "/usr/local/bin/claude"
        return backend

    def test_basic_command_structure(self) -> None:
        """Verify basic command: claude -p <prompt> --output-format text."""
        backend = self._make_backend()
        cmd = backend._build_command("hello world")
        assert cmd[0] == "/usr/local/bin/claude"
        assert "-p" in cmd
        p_idx = cmd.index("-p")
        assert "hello world" in cmd[p_idx + 1]

    def test_skip_permissions_flag(self) -> None:
        """Verify --dangerously-skip-permissions when skip_permissions=True."""
        backend = self._make_backend(skip_permissions=True)
        cmd = backend._build_command("test")
        assert "--dangerously-skip-permissions" in cmd

    def test_no_skip_permissions_flag(self) -> None:
        """Verify flag absent when skip_permissions=False."""
        backend = self._make_backend(skip_permissions=False)
        cmd = backend._build_command("test")
        assert "--dangerously-skip-permissions" not in cmd

    def test_output_format(self) -> None:
        """Verify --output-format is set correctly."""
        backend = self._make_backend(output_format="json")
        cmd = backend._build_command("test")
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"

    def test_disable_mcp(self) -> None:
        """Verify MCP disabled with --strict-mcp-config flag."""
        backend = self._make_backend(disable_mcp=True)
        cmd = backend._build_command("test")
        assert "--strict-mcp-config" in cmd
        assert "--mcp-config" in cmd

    def test_mcp_not_disabled(self) -> None:
        """Verify MCP flags absent when disable_mcp=False."""
        backend = self._make_backend(disable_mcp=False)
        cmd = backend._build_command("test")
        assert "--strict-mcp-config" not in cmd

    def test_model_selection(self) -> None:
        """Verify --model flag with custom model."""
        backend = self._make_backend(cli_model="claude-opus-4-6")
        cmd = backend._build_command("test")
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4-6"

    def test_allowed_tools(self) -> None:
        """Verify --allowedTools flag."""
        backend = self._make_backend(allowed_tools=["Bash", "Read"])
        cmd = backend._build_command("test")
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Bash,Read"

    def test_system_prompt_file(self) -> None:
        """Verify --system-prompt flag."""
        from pathlib import Path
        backend = self._make_backend(system_prompt_file=Path("/tmp/prompt.md"))
        cmd = backend._build_command("test")
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "/tmp/prompt.md"

    def test_extra_args_appended_last(self) -> None:
        """Verify cli_extra_args are appended at the end."""
        backend = self._make_backend(cli_extra_args=["--verbose", "--debug"])
        cmd = backend._build_command("test")
        assert cmd[-2] == "--verbose"
        assert cmd[-1] == "--debug"

    def test_raises_without_claude_path(self) -> None:
        """Verify RuntimeError when claude CLI not found."""
        backend = ClaudeCliBackend()
        backend._claude_path = None
        with pytest.raises(RuntimeError, match="claude CLI not found"):
            backend._build_command("test")

    def test_preamble_injected(self) -> None:
        """Verify the preamble is injected into the prompt."""
        backend = self._make_backend()
        backend.set_preamble("<mozart-preamble>Test</mozart-preamble>")
        cmd = backend._build_command("user prompt")
        p_idx = cmd.index("-p")
        full_prompt = cmd[p_idx + 1]
        assert "<mozart-preamble>" in full_prompt
        assert "user prompt" in full_prompt


# ============================================================================
# ClaudeCliBackend._execute_impl() Tests (FIX-49)
# ============================================================================


def _make_stream_reader(data: bytes) -> AsyncMock:
    """Create a mock StreamReader that yields data then EOF."""
    reader = AsyncMock()
    chunks = [data] if data else []
    call_count = 0

    async def _read(n: int = -1) -> bytes:
        nonlocal call_count
        if call_count < len(chunks):
            chunk = chunks[call_count]
            call_count += 1
            return chunk
        return b""

    reader.read = _read
    return reader


def _make_mock_process(
    returncode: int | None = 0,
    stdout: bytes = b"output text",
    stderr: bytes = b"",
    pid: int = 12345,
) -> MagicMock:
    """Create a mock asyncio.subprocess.Process."""
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.returncode = returncode
    proc.pid = pid
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = MagicMock()
    proc.terminate = MagicMock()
    # Stream readers for the streaming path (always used after Q013 fix)
    proc.stdout = _make_stream_reader(stdout)
    proc.stderr = _make_stream_reader(stderr)
    return proc


class TestExecuteImpl:
    """Tests for ClaudeCliBackend._execute_impl() subprocess execution."""

    def _make_backend(self, **kwargs: Any) -> ClaudeCliBackend:
        backend = ClaudeCliBackend(**kwargs)
        backend._claude_path = "/usr/local/bin/claude"
        return backend

    @pytest.mark.asyncio
    async def test_successful_execution(self) -> None:
        """Test normal execution with exit_code=0."""
        backend = self._make_backend()
        proc = _make_mock_process(returncode=0, stdout=b"hello world", stderr=b"")

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await backend._execute_impl("test prompt")

        assert result.success is True
        assert result.exit_code == 0
        assert result.exit_signal is None
        assert result.exit_reason == "completed"
        assert result.stdout == "hello world"
        assert result.stderr == ""
        assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self) -> None:
        """Test failed execution with exit_code=1."""
        backend = self._make_backend()
        proc = _make_mock_process(returncode=1, stdout=b"", stderr=b"error occurred")

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await backend._execute_impl("test prompt")

        assert result.success is False
        assert result.exit_code == 1
        assert result.exit_signal is None
        assert result.exit_reason == "completed"
        assert result.stderr == "error occurred"

    @pytest.mark.asyncio
    async def test_signal_kill_negative_returncode(self) -> None:
        """Test process killed by signal (returncode < 0)."""
        backend = self._make_backend()
        # -9 means SIGKILL
        proc = _make_mock_process(returncode=-9, stdout=b"partial", stderr=b"")

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await backend._execute_impl("test prompt")

        assert result.success is False
        assert result.exit_code is None
        assert result.exit_signal == 9
        assert result.exit_reason == "killed"
        assert "SIGKILL" in result.stderr

    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_result(self) -> None:
        """Test timeout produces correct result with exit_reason='timeout'."""
        backend = self._make_backend(timeout_seconds=1)
        proc = _make_mock_process()
        proc.communicate = AsyncMock(side_effect=TimeoutError)
        proc.returncode = None  # Still running when timeout hits

        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            patch("asyncio.wait_for", side_effect=TimeoutError),
        ):
            result = await backend._execute_impl("test prompt")

        assert result.success is False
        assert result.exit_reason == "timeout"
        assert result.exit_signal == signal_module.SIGKILL
        assert "timed out" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_file_not_found_returns_127(self) -> None:
        """Test missing CLI binary returns exit_code=127."""
        backend = self._make_backend()

        with patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("claude not found")),
        ):
            result = await backend._execute_impl("test prompt")

        assert result.success is False
        assert result.exit_code == 127
        assert result.exit_reason == "error"
        assert result.error_type == "not_found"

    @pytest.mark.asyncio
    async def test_general_exception_kills_orphan(self) -> None:
        """Test that orphaned process is killed on unexpected exception."""
        backend = self._make_backend()
        proc = _make_mock_process()
        proc.returncode = None  # Process still running

        # Make the stdout stream raise during read — this triggers the
        # outer Exception handler in _execute_impl which kills orphans.
        async def _failing_read(_n: int = -1) -> bytes:
            raise RuntimeError("unexpected")

        proc.stdout.read = _failing_read
        proc.stdout.readline = _failing_read

        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            patch("os.killpg") as mock_killpg,
            patch("os.getpgid", return_value=12345),
        ):
            result = await backend._execute_impl("test prompt")

        assert result.success is False
        assert result.error_type == "exception"
        assert result.error_message is not None
        assert "unexpected" in result.error_message
        # Should have attempted to kill the orphaned process group
        mock_killpg.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limit_detection_in_output(self) -> None:
        """Test that rate_limited flag is set when output contains rate limit patterns."""
        backend = self._make_backend()
        proc = _make_mock_process(
            returncode=1, stdout=b"Error: rate limit exceeded", stderr=b""
        )

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await backend._execute_impl("test prompt")

        assert result.rate_limited is True
        assert result.error_type == "rate_limit"

    @pytest.mark.asyncio
    async def test_none_returncode_handled(self) -> None:
        """Test that None returncode (shouldn't happen) is handled gracefully."""
        backend = self._make_backend()
        proc = _make_mock_process(returncode=0, stdout=b"ok", stderr=b"")
        # Simulate None returncode after communicate
        proc.returncode = None

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await backend._execute_impl("test prompt")

        # Should handle gracefully as error
        assert result.exit_reason == "error"
        assert result.exit_code is None
        assert result.exit_signal is None


# ============================================================================
# Per-Sheet Backend Override Tests (GH#78)
# ============================================================================


class TestAnthropicApiBackendOverrides:
    """Test per-sheet override application for AnthropicApiBackend."""

    def test_apply_overrides_model(self) -> None:
        """Apply model override changes model attribute."""
        backend = AnthropicApiBackend(model="claude-sonnet-4-20250514")
        backend.apply_overrides({"model": "claude-opus-4-6"})
        assert backend.model == "claude-opus-4-6"

    def test_clear_overrides_restores_original(self) -> None:
        """Clear overrides restores original model."""
        backend = AnthropicApiBackend(model="claude-sonnet-4-20250514", temperature=0.7)
        backend.apply_overrides({"model": "claude-opus-4-6", "temperature": 0.2})
        assert backend.model == "claude-opus-4-6"
        assert backend.temperature == 0.2
        backend.clear_overrides()
        assert backend.model == "claude-sonnet-4-20250514"
        assert backend.temperature == 0.7

    def test_apply_overrides_partial(self) -> None:
        """Apply only some overrides leaves others unchanged."""
        backend = AnthropicApiBackend(model="claude-sonnet-4-20250514", temperature=0.7)
        backend.apply_overrides({"temperature": 0.0})
        assert backend.model == "claude-sonnet-4-20250514"  # Unchanged
        assert backend.temperature == 0.0

    def test_clear_overrides_noop_without_apply(self) -> None:
        """Clearing overrides without applying is a no-op."""
        backend = AnthropicApiBackend(model="claude-sonnet-4-20250514")
        backend.clear_overrides()  # Should not raise
        assert backend.model == "claude-sonnet-4-20250514"

    def test_apply_overrides_max_tokens(self) -> None:
        """Apply max_tokens override."""
        backend = AnthropicApiBackend(max_tokens=8192)
        backend.apply_overrides({"max_tokens": 16384})
        assert backend.max_tokens == 16384
        backend.clear_overrides()
        assert backend.max_tokens == 8192

    def test_apply_empty_overrides_noop(self) -> None:
        """Empty overrides dict is a no-op."""
        backend = AnthropicApiBackend(model="claude-sonnet-4-20250514")
        backend.apply_overrides({})
        assert backend.model == "claude-sonnet-4-20250514"
        assert not backend._has_overrides


class TestClaudeCliBackendOverrides:
    """Test per-sheet override application for ClaudeCliBackend."""

    def _make_backend(self, **kwargs: Any) -> ClaudeCliBackend:
        return ClaudeCliBackend(**kwargs)

    def test_apply_overrides_cli_model(self) -> None:
        """Apply cli_model override changes model attribute."""
        backend = self._make_backend(cli_model="claude-sonnet-4-20250514")
        backend.apply_overrides({"cli_model": "claude-opus-4-6"})
        assert backend.cli_model == "claude-opus-4-6"

    def test_clear_overrides_restores_cli_model(self) -> None:
        """Clear overrides restores original cli_model."""
        backend = self._make_backend(cli_model="claude-sonnet-4-20250514")
        backend.apply_overrides({"cli_model": "claude-opus-4-6"})
        assert backend.cli_model == "claude-opus-4-6"
        backend.clear_overrides()
        assert backend.cli_model == "claude-sonnet-4-20250514"

    def test_clear_overrides_noop_without_apply(self) -> None:
        """Clearing overrides without applying is a no-op."""
        backend = self._make_backend(cli_model="claude-sonnet-4-20250514")
        backend.clear_overrides()
        assert backend.cli_model == "claude-sonnet-4-20250514"

    def test_apply_empty_overrides_noop(self) -> None:
        """Empty overrides dict is a no-op."""
        backend = self._make_backend(cli_model="claude-sonnet-4-20250514")
        backend.apply_overrides({})
        assert backend.cli_model == "claude-sonnet-4-20250514"
        assert not backend._has_overrides
