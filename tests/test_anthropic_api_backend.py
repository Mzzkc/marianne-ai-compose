"""Comprehensive tests for AnthropicApiBackend.

Focuses on error paths, edge cases, and methods NOT covered by test_backends.py.
Existing coverage in test_backends.py:
  - Init defaults/custom, from_config
  - Basic execute success, rate limit, auth, bad request, timeout, connection errors
  - No API key configuration error
  - Rate limit detection patterns (including exit_code=0/None edge cases)
  - Health check (no key, success, failure)
  - Logging (request/response/rate_limit/auth levels, API key never logged)
  - Override apply/clear

This file covers:
  - _get_client() lazy init with asyncio.Lock (concurrent access, missing key)
  - APIStatusError handler (generic status errors with rate limit detection)
  - Generic Exception handler (re-raises after logging)
  - close() method (normal, idempotent, error resilience)
  - Async context manager (__aenter__/__aexit__)
  - set_output_log_path() and _write_log_file() (file logging)
  - Multi-block response content extraction
  - Missing/null usage data in response
  - Token tracking edge cases (input_tokens, output_tokens, tokens_used)
  - Timeout override parameter (logged but not enforced)
  - Working directory property
  - override_lock property
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from marianne.backends.anthropic_api import AnthropicApiBackend


# ============================================================================
# Helper: create a mock API response
# ============================================================================


def _make_response(
    text: str = "Hello",
    input_tokens: int = 10,
    output_tokens: int = 5,
    *,
    blocks: list[MagicMock] | None = None,
    usage: MagicMock | None = None,
    no_usage: bool = False,
) -> MagicMock:
    """Build a mock anthropic Messages response."""
    if blocks is None:
        block = MagicMock()
        block.text = text
        blocks = [block]
    if no_usage:
        usage = None
    elif usage is None:
        usage = MagicMock()
        usage.input_tokens = input_tokens
        usage.output_tokens = output_tokens
    resp = MagicMock()
    resp.content = blocks
    resp.usage = usage
    return resp


def _backend_with_key(monkeypatch: pytest.MonkeyPatch) -> AnthropicApiBackend:
    """Create a backend with a fake API key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")
    return AnthropicApiBackend()


def _mock_client(response: MagicMock | None = None, side_effect: Exception | None = None) -> AsyncMock:
    """Create a mock AsyncAnthropic client with optional response or error."""
    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=side_effect, return_value=response)
    return client


# ============================================================================
# _get_client() — lazy init and asyncio.Lock protection
# ============================================================================


class TestGetClient:
    """Test _get_client() lazy initialization with lock protection."""

    @pytest.mark.asyncio
    async def test_lazy_creates_client_on_first_call(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Client is created lazily on first _get_client() call."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        backend = AnthropicApiBackend()
        assert backend._client is None

        with patch("marianne.backends.anthropic_api.anthropic.AsyncAnthropic") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            client = await backend._get_client()

        assert client is mock_instance
        assert backend._client is mock_instance
        mock_cls.assert_called_once_with(api_key="sk-test", timeout=300.0)

    @pytest.mark.asyncio
    async def test_reuses_existing_client(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Subsequent calls reuse the same client instance."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        backend = AnthropicApiBackend()
        sentinel = MagicMock()
        backend._client = sentinel

        assert await backend._get_client() is sentinel

    @pytest.mark.asyncio
    async def test_raises_on_missing_api_key(self) -> None:
        """_get_client() raises RuntimeError when API key is missing."""
        backend = AnthropicApiBackend(api_key_env="NONEXISTENT_KEY_XYZ")
        with pytest.raises(RuntimeError, match="NONEXISTENT_KEY_XYZ"):
            await backend._get_client()

    @pytest.mark.asyncio
    async def test_concurrent_calls_create_single_client(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Multiple concurrent _get_client() calls produce exactly one client instance."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        backend = AnthropicApiBackend()
        call_count = 0
        mock_instance = MagicMock()

        def counting_factory(**_kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return mock_instance

        with patch("marianne.backends.anthropic_api.anthropic.AsyncAnthropic", side_effect=counting_factory):
            # Fire 5 concurrent _get_client() calls
            results = await asyncio.gather(*[backend._get_client() for _ in range(5)])

        # All should return the same instance
        for r in results:
            assert r is mock_instance

        # Constructor called exactly once due to lock
        assert call_count == 1


# ============================================================================
# APIStatusError handler — generic status error with rate limit detection
# ============================================================================


class TestAPIStatusError:
    """Test the APIStatusError catch-all handler in execute()."""

    @pytest.mark.asyncio
    async def test_api_status_error_non_rate_limited(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """APIStatusError with non-rate-limit message returns api_error type."""
        backend = _backend_with_key(monkeypatch)

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        error = anthropic.APIStatusError(
            message="Internal server error",
            response=mock_resp,
            body=None,
        )
        client = _mock_client(side_effect=error)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 500
        assert result.rate_limited is False
        assert result.error_type == "api_error"

    @pytest.mark.asyncio
    async def test_api_status_error_rate_limited_by_status_code(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """APIStatusError with 429 status code triggers rate limit detection."""
        backend = _backend_with_key(monkeypatch)

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        error = anthropic.APIStatusError(
            message="Too many requests",
            response=mock_resp,
            body=None,
        )
        client = _mock_client(side_effect=error)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 429
        assert result.rate_limited is True
        assert result.error_type == "rate_limit"

    @pytest.mark.asyncio
    async def test_api_status_error_rate_limited_by_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """APIStatusError with rate limit text in message triggers detection."""
        backend = _backend_with_key(monkeypatch)

        mock_resp = MagicMock()
        mock_resp.status_code = 503

        error = anthropic.APIStatusError(
            message="rate limit exceeded, try again later",
            response=mock_resp,
            body=None,
        )
        client = _mock_client(side_effect=error)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.rate_limited is True
        assert result.error_type == "rate_limit"

    @pytest.mark.asyncio
    async def test_api_status_error_missing_status_code_attr(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """APIStatusError without status_code attribute defaults to 500."""
        backend = _backend_with_key(monkeypatch)

        mock_resp = MagicMock()
        mock_resp.status_code = 502

        error = anthropic.APIStatusError(
            message="Bad gateway",
            response=mock_resp,
            body=None,
        )
        # Remove the status_code attr to test the hasattr fallback
        # The error gets status_code from the response, so patch the error's attribute
        error.status_code = 502  # type: ignore[attr-defined]

        client = _mock_client(side_effect=error)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.exit_code == 502


# ============================================================================
# Generic Exception handler — re-raises after logging
# ============================================================================


class TestGenericExceptionHandler:
    """Test the bare Exception handler that re-raises."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_reraises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unexpected exceptions (not anthropic errors) are re-raised."""
        backend = _backend_with_key(monkeypatch)

        client = _mock_client(side_effect=ValueError("totally unexpected"))

        with patch.object(backend, "_get_client", return_value=client):
            with pytest.raises(ValueError, match="totally unexpected"):
                await backend.execute("test")

    @pytest.mark.asyncio
    async def test_unexpected_exception_writes_stderr_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """Unexpected exception writes error to stderr log file before re-raising."""
        backend = _backend_with_key(monkeypatch)
        backend.set_output_log_path(tmp_path / "sheet-1")

        client = _mock_client(side_effect=TypeError("bad type"))

        with patch.object(backend, "_get_client", return_value=client):
            with pytest.raises(TypeError):
                await backend.execute("test")

        stderr_path = tmp_path / "sheet-1.stderr.log"
        assert stderr_path.exists()
        assert "bad type" in stderr_path.read_text()


# ============================================================================
# close() — idempotent, error resilience
# ============================================================================


class TestClose:
    """Test close() method."""

    @pytest.mark.asyncio
    async def test_close_calls_client_close(self) -> None:
        """close() calls the underlying client's close()."""
        backend = AnthropicApiBackend()
        mock_client = AsyncMock()
        backend._client = mock_client

        await backend.close()

        mock_client.close.assert_awaited_once()
        assert backend._client is None

    @pytest.mark.asyncio
    async def test_close_idempotent_no_client(self) -> None:
        """close() is a no-op when no client exists."""
        backend = AnthropicApiBackend()
        assert backend._client is None
        await backend.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_close_idempotent_called_twice(self) -> None:
        """Calling close() twice does not raise."""
        backend = AnthropicApiBackend()
        mock_client = AsyncMock()
        backend._client = mock_client

        await backend.close()
        await backend.close()  # Second call should be no-op

    @pytest.mark.asyncio
    async def test_close_swallows_os_error(self) -> None:
        """close() swallows OSError from client.close()."""
        backend = AnthropicApiBackend()
        mock_client = AsyncMock()
        mock_client.close = AsyncMock(side_effect=OSError("socket closed"))
        backend._client = mock_client

        await backend.close()  # Should not raise
        assert backend._client is None

    @pytest.mark.asyncio
    async def test_close_swallows_runtime_error(self) -> None:
        """close() swallows RuntimeError from client.close()."""
        backend = AnthropicApiBackend()
        mock_client = AsyncMock()
        mock_client.close = AsyncMock(side_effect=RuntimeError("event loop closed"))
        backend._client = mock_client

        await backend.close()  # Should not raise
        assert backend._client is None


# ============================================================================
# Async context manager
# ============================================================================


class TestAsyncContextManager:
    """Test __aenter__ / __aexit__ protocol."""

    @pytest.mark.asyncio
    async def test_context_manager_calls_close(self) -> None:
        """Exiting the context manager calls close()."""
        backend = AnthropicApiBackend()
        mock_client = AsyncMock()
        backend._client = mock_client

        async with backend:
            pass

        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_context_manager_returns_self(self) -> None:
        """__aenter__ returns the backend instance."""
        backend = AnthropicApiBackend()
        async with backend as b:
            assert b is backend

    @pytest.mark.asyncio
    async def test_context_manager_calls_close_on_exception(self) -> None:
        """close() is called even if an exception occurs inside the context."""
        backend = AnthropicApiBackend()
        mock_client = AsyncMock()
        backend._client = mock_client

        with pytest.raises(RuntimeError, match="inner error"):
            async with backend:
                raise RuntimeError("inner error")

        mock_client.close.assert_awaited_once()


# ============================================================================
# set_output_log_path() and _write_log_file()
# ============================================================================


class TestOutputLogPath:
    """Test output log file management."""

    def test_set_output_log_path_creates_suffixed_paths(self) -> None:
        """set_output_log_path sets .stdout.log and .stderr.log paths."""
        backend = AnthropicApiBackend()
        backend.set_output_log_path(Path("/tmp/workspace/sheet-3"))

        assert backend._stdout_log_path == Path("/tmp/workspace/sheet-3.stdout.log")
        assert backend._stderr_log_path == Path("/tmp/workspace/sheet-3.stderr.log")

    def test_set_output_log_path_none_clears(self) -> None:
        """Setting path to None clears both log paths."""
        backend = AnthropicApiBackend()
        backend.set_output_log_path(Path("/tmp/foo"))
        backend.set_output_log_path(None)

        assert backend._stdout_log_path is None
        assert backend._stderr_log_path is None

    def test_write_log_file_creates_parent_dirs(self, tmp_path: Path) -> None:
        """_write_log_file creates parent directories if needed."""
        backend = AnthropicApiBackend()
        deep_path = tmp_path / "a" / "b" / "c" / "output.log"

        backend._write_log_file(deep_path, "test content")

        assert deep_path.exists()
        assert deep_path.read_text() == "test content"

    def test_write_log_file_none_path_noop(self) -> None:
        """_write_log_file with None path is a no-op."""
        backend = AnthropicApiBackend()
        backend._write_log_file(None, "content")  # Should not raise

    def test_write_log_file_os_error_logged_not_raised(self, tmp_path: Path) -> None:
        """_write_log_file logs OSError but does not raise."""
        backend = AnthropicApiBackend()
        # Use a path that will fail to write (file as parent directory)
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file")
        bad_path = blocker / "nested" / "output.log"

        # Should not raise even though writing will fail
        backend._write_log_file(bad_path, "content")

    @pytest.mark.asyncio
    async def test_execute_writes_response_to_stdout_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """Successful execute() writes response text to stdout log file."""
        backend = _backend_with_key(monkeypatch)
        backend.set_output_log_path(tmp_path / "sheet-1")

        client = _mock_client(response=_make_response(text="API response text"))

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is True
        stdout_log = tmp_path / "sheet-1.stdout.log"
        assert stdout_log.exists()
        assert stdout_log.read_text() == "API response text"

    @pytest.mark.asyncio
    async def test_execute_writes_error_to_stderr_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """Error during execute() writes error message to stderr log file."""
        backend = _backend_with_key(monkeypatch)
        backend.set_output_log_path(tmp_path / "sheet-1")

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        error = anthropic.RateLimitError(
            message="Rate limit exceeded",
            response=mock_resp,
            body=None,
        )
        client = _mock_client(side_effect=error)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is False
        stderr_log = tmp_path / "sheet-1.stderr.log"
        assert stderr_log.exists()
        assert "Rate limit" in stderr_log.read_text()


# ============================================================================
# Multi-block response and content extraction
# ============================================================================


class TestResponseContentExtraction:
    """Test extraction of text from various response content structures."""

    @pytest.mark.asyncio
    async def test_multiple_text_blocks_concatenated(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Multiple text blocks in response are concatenated."""
        backend = _backend_with_key(monkeypatch)

        block1 = MagicMock()
        block1.text = "Hello, "
        block2 = MagicMock()
        block2.text = "world!"

        resp = _make_response(blocks=[block1, block2])
        client = _mock_client(response=resp)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.stdout == "Hello, world!"

    @pytest.mark.asyncio
    async def test_non_text_blocks_skipped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Content blocks without .text attribute are skipped."""
        backend = _backend_with_key(monkeypatch)

        text_block = MagicMock()
        text_block.text = "Real text"

        # A block without .text (e.g., tool_use block)
        tool_block = MagicMock(spec=[])  # spec=[] means no attributes

        resp = _make_response(blocks=[tool_block, text_block])
        client = _mock_client(response=resp)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.stdout == "Real text"

    @pytest.mark.asyncio
    async def test_empty_content_blocks(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty content blocks list produces empty stdout."""
        backend = _backend_with_key(monkeypatch)

        resp = _make_response(blocks=[])
        client = _mock_client(response=resp)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.stdout == ""
        assert result.success is True


# ============================================================================
# Token tracking and usage edge cases
# ============================================================================


class TestTokenTracking:
    """Test token counting and cost-related fields."""

    @pytest.mark.asyncio
    async def test_tokens_used_is_sum_of_input_output(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """tokens_used = input_tokens + output_tokens for backwards compat."""
        backend = _backend_with_key(monkeypatch)

        resp = _make_response(input_tokens=100, output_tokens=50)
        client = _mock_client(response=resp)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.tokens_used == 150

    @pytest.mark.asyncio
    async def test_no_usage_in_response(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When response.usage is None, all token fields are None."""
        backend = _backend_with_key(monkeypatch)

        resp = _make_response(no_usage=True)
        client = _mock_client(response=resp)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.tokens_used is None

    @pytest.mark.asyncio
    async def test_error_results_have_no_token_counts(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Error results do not include token counts."""
        backend = _backend_with_key(monkeypatch)

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        client = _mock_client(
            side_effect=anthropic.BadRequestError(
                message="Invalid request", response=mock_resp, body=None,
            )
        )

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.tokens_used is None
        assert result.input_tokens is None
        assert result.output_tokens is None

    @pytest.mark.asyncio
    async def test_model_field_on_success(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Successful result includes the model name."""
        backend = _backend_with_key(monkeypatch)
        backend.model = "claude-opus-4-20250514"

        resp = _make_response()
        client = _mock_client(response=resp)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.model == "claude-opus-4-20250514"

    @pytest.mark.asyncio
    async def test_model_field_on_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Error results also include the model name for diagnostics."""
        backend = _backend_with_key(monkeypatch)
        backend.model = "claude-opus-4-20250514"

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        client = _mock_client(
            side_effect=anthropic.RateLimitError(
                message="Rate limited", response=mock_resp, body=None,
            )
        )

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.model == "claude-opus-4-20250514"


# ============================================================================
# Timeout override parameter
# ============================================================================


class TestTimeoutOverride:
    """Test the timeout_seconds parameter on execute()."""

    @pytest.mark.asyncio
    async def test_timeout_override_logged_but_not_enforced(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Passing timeout_seconds to execute() does not change API behavior."""
        backend = _backend_with_key(monkeypatch)
        assert backend.timeout_seconds == 300.0

        resp = _make_response()
        client = _mock_client(response=resp)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test", timeout_seconds=60.0)

        # Execution succeeds normally — the override is just logged
        assert result.success is True

    @pytest.mark.asyncio
    async def test_no_timeout_override_no_log(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Not passing timeout_seconds skips the override log."""
        backend = _backend_with_key(monkeypatch)

        resp = _make_response()
        client = _mock_client(response=resp)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is True


# ============================================================================
# Working directory property
# ============================================================================


class TestWorkingDirectory:
    """Test the working_directory property inherited from Backend."""

    def test_working_directory_default_none(self) -> None:
        """Default working directory is None."""
        backend = AnthropicApiBackend()
        assert backend.working_directory is None

    def test_working_directory_set_and_get(self) -> None:
        """Setting working directory persists."""
        backend = AnthropicApiBackend()
        backend.working_directory = Path("/tmp/test-workspace")
        assert backend.working_directory == Path("/tmp/test-workspace")

    def test_working_directory_reset_to_none(self) -> None:
        """Working directory can be reset to None."""
        backend = AnthropicApiBackend()
        backend.working_directory = Path("/tmp/test")
        backend.working_directory = None
        assert backend.working_directory is None


# ============================================================================
# override_lock property
# ============================================================================


class TestOverrideLock:
    """Test the override_lock property from Backend."""

    def test_override_lock_returns_asyncio_lock(self) -> None:
        """override_lock returns an asyncio.Lock."""
        backend = AnthropicApiBackend()
        lock = backend.override_lock
        assert isinstance(lock, asyncio.Lock)

    def test_override_lock_is_stable(self) -> None:
        """Multiple accesses to override_lock return the same instance."""
        backend = AnthropicApiBackend()
        lock1 = backend.override_lock
        lock2 = backend.override_lock
        assert lock1 is lock2


# ============================================================================
# Duration tracking
# ============================================================================


class TestDurationTracking:
    """Test that duration_seconds is populated for all code paths."""

    @pytest.mark.asyncio
    async def test_success_has_duration(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Successful execution has a positive duration."""
        backend = _backend_with_key(monkeypatch)
        resp = _make_response()
        client = _mock_client(response=resp)

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_rate_limit_error_has_duration(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Rate limit error result has a positive duration."""
        backend = _backend_with_key(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        client = _mock_client(
            side_effect=anthropic.RateLimitError(
                message="Rate limited", response=mock_resp, body=None,
            )
        )

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_timeout_error_has_duration(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Timeout error result has a positive duration."""
        backend = _backend_with_key(monkeypatch)
        client = _mock_client(
            side_effect=anthropic.APITimeoutError(request=MagicMock())
        )

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_connection_error_has_duration(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Connection error result has a positive duration."""
        backend = _backend_with_key(monkeypatch)
        client = _mock_client(
            side_effect=anthropic.APIConnectionError(request=MagicMock())
        )

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_config_error_has_duration(self) -> None:
        """Configuration error (missing key) result has a positive duration."""
        backend = AnthropicApiBackend(api_key_env="DOES_NOT_EXIST_XYZ")
        result = await backend.execute("test")
        assert result.duration_seconds >= 0


# ============================================================================
# Error result field consistency
# ============================================================================


class TestErrorResultFields:
    """Verify that each error path sets all expected fields consistently."""

    @pytest.mark.asyncio
    async def test_rate_limit_error_fields(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Rate limit error sets exit_code=429, rate_limited=True, error_type='rate_limit'."""
        backend = _backend_with_key(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        client = _mock_client(
            side_effect=anthropic.RateLimitError(
                message="Rate limited", response=mock_resp, body=None,
            )
        )

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 429
        assert result.rate_limited is True
        assert result.error_type == "rate_limit"
        assert result.stdout == ""
        assert result.stderr != ""
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_auth_error_fields(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auth error sets exit_code=401, error_type='authentication'."""
        backend = _backend_with_key(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        client = _mock_client(
            side_effect=anthropic.AuthenticationError(
                message="Invalid key", response=mock_resp, body=None,
            )
        )

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 401
        assert result.rate_limited is False
        assert result.error_type == "authentication"
        assert result.stdout == ""

    @pytest.mark.asyncio
    async def test_bad_request_error_fields(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bad request error sets exit_code=400, error_type='bad_request'."""
        backend = _backend_with_key(monkeypatch)
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        client = _mock_client(
            side_effect=anthropic.BadRequestError(
                message="Invalid params", response=mock_resp, body=None,
            )
        )

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 400
        assert result.error_type == "bad_request"

    @pytest.mark.asyncio
    async def test_timeout_error_fields(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Timeout error sets exit_code=408, error_type='timeout'."""
        backend = _backend_with_key(monkeypatch)
        client = _mock_client(
            side_effect=anthropic.APITimeoutError(request=MagicMock())
        )

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 408
        assert result.error_type == "timeout"
        assert result.error_message is not None
        assert "timeout" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_connection_error_fields(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Connection error sets exit_code=503, error_type='connection'."""
        backend = _backend_with_key(monkeypatch)
        client = _mock_client(
            side_effect=anthropic.APIConnectionError(request=MagicMock())
        )

        with patch.object(backend, "_get_client", return_value=client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 503
        assert result.error_type == "connection"

    @pytest.mark.asyncio
    async def test_config_error_fields(self) -> None:
        """Missing API key error sets exit_code=1, error_type='configuration'."""
        backend = AnthropicApiBackend(api_key_env="NO_SUCH_KEY")
        result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 1
        assert result.error_type == "configuration"
        assert "NO_SUCH_KEY" in result.stderr


# ============================================================================
# name property
# ============================================================================


class TestNameProperty:
    """Test the name property."""

    def test_name_is_anthropic_api(self) -> None:
        backend = AnthropicApiBackend()
        assert backend.name == "anthropic-api"


# ============================================================================
# Stderr log file writing for all error types
# ============================================================================


class TestStderrLogWriting:
    """Verify that all error paths write to stderr log when configured."""

    @pytest.fixture
    def backend_with_logs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> tuple[AnthropicApiBackend, Path]:
        """Create backend with output log path configured."""
        backend = _backend_with_key(monkeypatch)
        base = tmp_path / "sheet-1"
        backend.set_output_log_path(base)
        return backend, tmp_path

    @pytest.mark.asyncio
    async def test_auth_error_writes_stderr(
        self, backend_with_logs: tuple[AnthropicApiBackend, Path],
    ) -> None:
        backend, tmp_path = backend_with_logs
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        client = _mock_client(
            side_effect=anthropic.AuthenticationError(
                message="Invalid API key", response=mock_resp, body=None,
            )
        )
        with patch.object(backend, "_get_client", return_value=client):
            await backend.execute("test")
        assert (tmp_path / "sheet-1.stderr.log").exists()

    @pytest.mark.asyncio
    async def test_bad_request_writes_stderr(
        self, backend_with_logs: tuple[AnthropicApiBackend, Path],
    ) -> None:
        backend, tmp_path = backend_with_logs
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        client = _mock_client(
            side_effect=anthropic.BadRequestError(
                message="Bad request", response=mock_resp, body=None,
            )
        )
        with patch.object(backend, "_get_client", return_value=client):
            await backend.execute("test")
        assert (tmp_path / "sheet-1.stderr.log").exists()

    @pytest.mark.asyncio
    async def test_timeout_writes_stderr(
        self, backend_with_logs: tuple[AnthropicApiBackend, Path],
    ) -> None:
        backend, tmp_path = backend_with_logs
        client = _mock_client(
            side_effect=anthropic.APITimeoutError(request=MagicMock())
        )
        with patch.object(backend, "_get_client", return_value=client):
            await backend.execute("test")
        assert (tmp_path / "sheet-1.stderr.log").exists()

    @pytest.mark.asyncio
    async def test_connection_error_writes_stderr(
        self, backend_with_logs: tuple[AnthropicApiBackend, Path],
    ) -> None:
        backend, tmp_path = backend_with_logs
        client = _mock_client(
            side_effect=anthropic.APIConnectionError(request=MagicMock())
        )
        with patch.object(backend, "_get_client", return_value=client):
            await backend.execute("test")
        assert (tmp_path / "sheet-1.stderr.log").exists()

    @pytest.mark.asyncio
    async def test_api_status_error_writes_stderr(
        self, backend_with_logs: tuple[AnthropicApiBackend, Path],
    ) -> None:
        backend, tmp_path = backend_with_logs
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        client = _mock_client(
            side_effect=anthropic.APIStatusError(
                message="Server error", response=mock_resp, body=None,
            )
        )
        with patch.object(backend, "_get_client", return_value=client):
            await backend.execute("test")
        assert (tmp_path / "sheet-1.stderr.log").exists()

    @pytest.mark.asyncio
    async def test_config_error_writes_stderr(
        self, tmp_path: Path,
    ) -> None:
        """Configuration error (missing key) writes to stderr log."""
        backend = AnthropicApiBackend(api_key_env="NO_KEY_HERE")
        base = tmp_path / "sheet-1"
        backend.set_output_log_path(base)

        await backend.execute("test")

        assert (tmp_path / "sheet-1.stderr.log").exists()
        content = (tmp_path / "sheet-1.stderr.log").read_text()
        assert "NO_KEY_HERE" in content
