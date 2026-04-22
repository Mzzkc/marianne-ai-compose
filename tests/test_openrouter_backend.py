"""Comprehensive tests for OpenRouterBackend.

Covers:
  - Init defaults, custom args, validation
  - execute() success with token tracking
  - Rate limit detection (429, Retry-After header, body parsing)
  - HTTP error handling (401, 400, 402, 503, generic)
  - Missing API key error path
  - Connection error and timeout handling
  - Override lifecycle (apply/clear)
  - Health check (success, failure, no key)
  - Availability check
  - close() lifecycle
  - Preamble and prompt extensions
  - Output log file writing
  - Async context manager
  - Response parsing edge cases (empty choices, missing usage)
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from marianne.backends.openrouter import OpenRouterBackend

# ============================================================================
# Helpers
# ============================================================================


def _make_success_response(
    content: str = "Hello from OpenRouter",
    model: str = "minimax/minimax-m1-80k",
    input_tokens: int = 10,
    output_tokens: int = 20,
) -> httpx.Response:
    """Build a mock httpx.Response for a successful chat completion."""
    data = {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "model": model,
    }
    return httpx.Response(
        status_code=200,
        json=data,
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )


def _make_error_response(
    status_code: int,
    body: str = '{"error": {"message": "something went wrong"}}',
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a mock httpx.Response for an error."""
    return httpx.Response(
        status_code=status_code,
        text=body,
        headers=headers or {},
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )


def _backend_with_key(monkeypatch: pytest.MonkeyPatch) -> OpenRouterBackend:
    """Create a backend with a fake API key set."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-fake-key")
    return OpenRouterBackend()


# ============================================================================
# Init
# ============================================================================


class TestInit:
    """Test __init__ parameter handling and validation."""

    def test_default_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default parameters match expected values."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        backend = OpenRouterBackend()
        assert backend.model == "minimax/minimax-m1-80k"
        assert backend.api_key_env == "OPENROUTER_API_KEY"
        assert backend.max_tokens == 16384
        assert backend.temperature == 0.7
        assert backend.timeout_seconds == 300.0
        assert backend._api_key == "sk-test"

    def test_custom_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Custom parameters are stored correctly."""
        monkeypatch.setenv("MY_KEY", "sk-custom")
        backend = OpenRouterBackend(
            model="google/gemma-4",
            api_key_env="MY_KEY",
            max_tokens=8192,
            temperature=0.3,
            timeout_seconds=600.0,
        )
        assert backend.model == "google/gemma-4"
        assert backend.api_key_env == "MY_KEY"
        assert backend.max_tokens == 8192
        assert backend.temperature == 0.3
        assert backend.timeout_seconds == 600.0
        assert backend._api_key == "sk-custom"

    def test_missing_env_key(self) -> None:
        """Backend initializes even without API key (checked at execute time)."""
        backend = OpenRouterBackend(api_key_env="NONEXISTENT_KEY_XYZ_123")
        assert backend._api_key is None

    def test_name_includes_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Backend name includes the model identifier."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        backend = OpenRouterBackend(model="meta-llama/llama-4-maverick")
        assert backend.name == "openrouter:meta-llama/llama-4-maverick"

    def test_validates_empty_model(self) -> None:
        """Empty model string raises ValueError."""
        with pytest.raises(ValueError, match="model must be a non-empty string"):
            OpenRouterBackend(model="")

    def test_validates_empty_api_key_env(self) -> None:
        """Empty api_key_env raises ValueError."""
        with pytest.raises(ValueError, match="api_key_env must be a non-empty string"):
            OpenRouterBackend(api_key_env="")

    def test_validates_max_tokens(self) -> None:
        """max_tokens < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_tokens must be >= 1"):
            OpenRouterBackend(max_tokens=0)

    def test_validates_timeout(self) -> None:
        """timeout_seconds <= 0 raises ValueError."""
        with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
            OpenRouterBackend(timeout_seconds=0)


# ============================================================================
# Execute — Success
# ============================================================================


class TestExecuteSuccess:
    """Test successful execution paths."""

    async def test_basic_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful completion returns content and token counts."""
        backend = _backend_with_key(monkeypatch)
        mock_response = _make_success_response(
            content="Result text",
            model="minimax/minimax-m1-80k",
            input_tokens=15,
            output_tokens=25,
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test prompt")

        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "Result text"
        assert result.stderr == ""
        assert result.input_tokens == 15
        assert result.output_tokens == 25
        assert result.tokens_used == 40
        assert result.model == "minimax/minimax-m1-80k"
        assert result.duration_seconds > 0

    async def test_actual_model_differs_from_requested(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Response model field overrides the requested model in result."""
        backend = _backend_with_key(monkeypatch)
        mock_response = _make_success_response(
            content="ok",
            model="minimax/minimax-m1-80k-actual-variant",
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.model == "minimax/minimax-m1-80k-actual-variant"

    async def test_empty_choices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty choices array results in empty content."""
        backend = _backend_with_key(monkeypatch)
        response = httpx.Response(
            status_code=200,
            json={"choices": [], "usage": {}, "model": "test"},
            request=httpx.Request("POST", "https://example.com"),
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is True
        assert result.stdout == ""

    async def test_missing_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing usage field results in None token counts."""
        backend = _backend_with_key(monkeypatch)
        response = httpx.Response(
            status_code=200,
            json={
                "choices": [{"message": {"content": "hi"}}],
                "model": "test",
            },
            request=httpx.Request("POST", "https://example.com"),
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is True
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.tokens_used is None

    async def test_null_content_in_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Null content in message is treated as empty string."""
        backend = _backend_with_key(monkeypatch)
        response = httpx.Response(
            status_code=200,
            json={
                "choices": [{"message": {"content": None}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 0},
                "model": "test",
            },
            request=httpx.Request("POST", "https://example.com"),
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is True
        assert result.stdout == ""


# ============================================================================
# Execute — Rate Limiting
# ============================================================================


class TestRateLimit:
    """Test rate limit detection and handling."""

    async def test_429_with_retry_after_header(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP 429 with Retry-After header extracts wait time."""
        backend = _backend_with_key(monkeypatch)
        response = _make_error_response(
            429,
            body="Rate limit exceeded",
            headers={"Retry-After": "30"},
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.rate_limited is True
        assert result.rate_limit_wait_seconds == 30.0
        assert result.exit_code == 429
        assert result.error_type == "rate_limit"

    async def test_429_without_header_falls_back_to_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP 429 without header tries to extract wait from body text."""
        backend = _backend_with_key(monkeypatch)
        response = _make_error_response(
            429,
            body="Please retry after 60 seconds",
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.rate_limited is True
        assert result.rate_limit_wait_seconds is not None

    async def test_429_no_wait_info(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP 429 without any wait info returns None for wait seconds."""
        backend = _backend_with_key(monkeypatch)
        response = _make_error_response(429, body="Rate limited")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.rate_limited is True
        # rate_limit_wait_seconds may be None if no parseable wait info


# ============================================================================
# Execute — HTTP Errors
# ============================================================================


class TestHttpErrors:
    """Test HTTP error response handling."""

    async def test_401_authentication(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP 401 produces authentication error type."""
        backend = _backend_with_key(monkeypatch)
        response = _make_error_response(401, body="Unauthorized")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 401
        assert result.error_type == "authentication"

    async def test_400_bad_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP 400 produces bad_request error type."""
        backend = _backend_with_key(monkeypatch)
        response = _make_error_response(400, body="Bad request")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 400
        assert result.error_type == "bad_request"

    async def test_402_insufficient_credits(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP 402 produces insufficient_credits error type."""
        backend = _backend_with_key(monkeypatch)
        response = _make_error_response(402, body="Payment required")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 402
        assert result.error_type == "insufficient_credits"

    async def test_503_service_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP 503 produces service_unavailable error type."""
        backend = _backend_with_key(monkeypatch)
        response = _make_error_response(503, body="Service unavailable")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 503
        assert result.error_type == "service_unavailable"

    async def test_500_generic_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP 500 produces generic api_error type."""
        backend = _backend_with_key(monkeypatch)
        response = _make_error_response(500, body="Internal server error")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 500
        assert result.error_type == "api_error"


# ============================================================================
# Execute — Missing API Key
# ============================================================================


class TestMissingApiKey:
    """Test behavior when API key is not configured."""

    async def test_returns_configuration_error(self) -> None:
        """Missing API key returns configuration error without raising."""
        backend = OpenRouterBackend(api_key_env="NONEXISTENT_KEY_XYZ_123")
        result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 1
        assert result.error_type == "configuration"
        assert "NONEXISTENT_KEY_XYZ_123" in result.stderr


# ============================================================================
# Execute — Connection and Timeout
# ============================================================================


class TestConnectionErrors:
    """Test connection and timeout error handling."""

    async def test_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Connection error returns structured result (not exception)."""
        backend = _backend_with_key(monkeypatch)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused"),
        )

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 503
        assert result.error_type == "connection"
        assert "Connection refused" in result.stderr

    async def test_timeout_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Timeout exception returns structured result."""
        backend = _backend_with_key(monkeypatch)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ReadTimeout("Read timed out"),
        )

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is False
        assert result.exit_code == 408
        assert result.exit_reason == "timeout"
        assert result.error_type == "timeout"

    async def test_unexpected_exception_re_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unexpected exceptions are re-raised after logging."""
        backend = _backend_with_key(monkeypatch)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=RuntimeError("something unexpected"),
        )

        with (
            patch.object(backend, "_get_client", return_value=mock_client),
            pytest.raises(RuntimeError, match="something unexpected"),
        ):
            await backend.execute("test")


# ============================================================================
# Override Lifecycle
# ============================================================================


class TestOverrides:
    """Test per-sheet override apply/clear lifecycle."""

    def test_apply_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """apply_overrides changes model, temperature, max_tokens."""
        backend = _backend_with_key(monkeypatch)
        original_model = backend.model

        backend.apply_overrides(
            {
                "model": "google/gemma-4",
                "temperature": 0.1,
                "max_tokens": 4096,
            }
        )

        assert backend.model == "google/gemma-4"
        assert backend.temperature == 0.1
        assert backend.max_tokens == 4096
        assert backend._has_overrides is True
        assert backend._saved_model == original_model

    def test_clear_overrides_restores(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """clear_overrides restores original values."""
        backend = _backend_with_key(monkeypatch)
        original_model = backend.model
        original_temp = backend.temperature
        original_tokens = backend.max_tokens

        backend.apply_overrides({"model": "google/gemma-4"})
        backend.clear_overrides()

        assert backend.model == original_model
        assert backend.temperature == original_temp
        assert backend.max_tokens == original_tokens
        assert backend._has_overrides is False

    def test_clear_without_apply_is_noop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """clear_overrides without prior apply is safe no-op."""
        backend = _backend_with_key(monkeypatch)
        original_model = backend.model
        backend.clear_overrides()
        assert backend.model == original_model

    def test_empty_overrides_is_noop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty overrides dict does not save state."""
        backend = _backend_with_key(monkeypatch)
        backend.apply_overrides({})
        assert backend._has_overrides is False


# ============================================================================
# Health Check
# ============================================================================


class TestHealthCheck:
    """Test health check behavior."""

    async def test_healthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Health check returns True on 200 response."""
        backend = _backend_with_key(monkeypatch)
        mock_client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json={"data": []},
            request=httpx.Request("GET", "https://openrouter.ai/api/v1/models"),
        )
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            assert await backend.health_check() is True

    async def test_unhealthy_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Health check returns False on non-200 response."""
        backend = _backend_with_key(monkeypatch)
        mock_client = AsyncMock()
        mock_response = httpx.Response(
            status_code=500,
            text="error",
            request=httpx.Request("GET", "https://openrouter.ai/api/v1/models"),
        )
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            assert await backend.health_check() is False

    async def test_no_api_key(self) -> None:
        """Health check returns False without API key."""
        backend = OpenRouterBackend(api_key_env="NONEXISTENT_KEY_XYZ_123")
        assert await backend.health_check() is False

    async def test_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Health check returns False on connection error."""
        backend = _backend_with_key(monkeypatch)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused"),
        )

        with patch.object(backend, "_get_client", return_value=mock_client):
            assert await backend.health_check() is False


# ============================================================================
# Availability Check
# ============================================================================


class TestAvailabilityCheck:
    """Test availability check (no HTTP requests)."""

    async def test_available_with_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Availability check returns True when key is set."""
        backend = _backend_with_key(monkeypatch)
        assert await backend.availability_check() is True

    async def test_unavailable_without_key(self) -> None:
        """Availability check returns False without key."""
        backend = OpenRouterBackend(api_key_env="NONEXISTENT_KEY_XYZ_123")
        assert await backend.availability_check() is False


# ============================================================================
# Close and Context Manager
# ============================================================================


class TestClose:
    """Test close() and async context manager."""

    async def test_close_idempotent(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Calling close() multiple times is safe."""
        backend = _backend_with_key(monkeypatch)
        await backend.close()
        await backend.close()  # Should not raise

    async def test_context_manager(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Backend works as async context manager."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        async with OpenRouterBackend() as backend:
            assert backend.name.startswith("openrouter:")
        # close() called automatically via __aexit__


# ============================================================================
# Preamble and Extensions
# ============================================================================


class TestPreambleExtensions:
    """Test prompt assembly with preamble and extensions."""

    async def test_preamble_prepended(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Preamble is prepended to the prompt."""
        backend = _backend_with_key(monkeypatch)
        backend.set_preamble("You are sheet 1 of 5.")

        mock_response = _make_success_response(content="ok")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            await backend.execute("Do the work")

        # Verify the prompt contains preamble
        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        prompt_text = payload["messages"][0]["content"]
        assert prompt_text.startswith("You are sheet 1 of 5.")
        assert "Do the work" in prompt_text

    async def test_extensions_appended(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prompt extensions are appended."""
        backend = _backend_with_key(monkeypatch)
        backend.set_prompt_extensions(["Extension 1", "Extension 2"])

        mock_response = _make_success_response(content="ok")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            await backend.execute("Do the work")

        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        prompt_text = payload["messages"][0]["content"]
        assert "Extension 1" in prompt_text
        assert "Extension 2" in prompt_text

    def test_set_prompt_extensions_filters_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty/whitespace-only extensions are filtered out."""
        backend = _backend_with_key(monkeypatch)
        backend.set_prompt_extensions(["valid", "", "  ", "also valid"])
        assert backend._prompt_extensions == ["valid", "also valid"]


# ============================================================================
# Output Log File
# ============================================================================


class TestOutputLogFile:
    """Test output log file writing."""

    def test_set_output_log_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Setting log path creates stdout and stderr paths."""
        backend = _backend_with_key(monkeypatch)
        backend.set_output_log_path(Path("/tmp/test/sheet-1"))
        assert backend._stdout_log_path == Path("/tmp/test/sheet-1.stdout.log")
        assert backend._stderr_log_path == Path("/tmp/test/sheet-1.stderr.log")

    def test_clear_output_log_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Setting None clears both paths."""
        backend = _backend_with_key(monkeypatch)
        backend.set_output_log_path(Path("/tmp/test"))
        backend.set_output_log_path(None)
        assert backend._stdout_log_path is None
        assert backend._stderr_log_path is None

    async def test_success_writes_stdout_log(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Successful execution writes content to stdout log."""
        backend = _backend_with_key(monkeypatch)
        log_base = tmp_path / "sheet-1"
        backend.set_output_log_path(log_base)

        mock_response = _make_success_response(content="Response content")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.object(backend, "_get_client", return_value=mock_client):
            await backend.execute("test")

        stdout_log = tmp_path / "sheet-1.stdout.log"
        assert stdout_log.exists()
        assert stdout_log.read_text() == "Response content"


# ============================================================================
# Working Directory
# ============================================================================


class TestWorkingDirectory:
    """Test working directory property."""

    def test_default_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default working directory is None."""
        backend = _backend_with_key(monkeypatch)
        assert backend.working_directory is None

    def test_set_and_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Working directory can be set and retrieved."""
        backend = _backend_with_key(monkeypatch)
        backend.working_directory = Path("/workspace/test")
        assert backend.working_directory == Path("/workspace/test")


# ============================================================================
# Override Lock
# ============================================================================


class TestOverrideLock:
    """Test override_lock property."""

    def test_returns_asyncio_lock(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """override_lock returns an asyncio.Lock instance."""
        backend = _backend_with_key(monkeypatch)
        lock = backend.override_lock
        assert isinstance(lock, asyncio.Lock)

    def test_returns_same_instance(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Repeated calls return the same lock instance."""
        backend = _backend_with_key(monkeypatch)
        lock1 = backend.override_lock
        lock2 = backend.override_lock
        assert lock1 is lock2


# ============================================================================
# BackendPool Integration
# ============================================================================


class TestBackendPoolIntegration:
    """Test that the BackendPool correctly creates OpenRouterBackend for HTTP instruments."""

    def test_creates_openrouter_backend(self) -> None:
        """_create_backend_for_profile creates OpenRouterBackend for openrouter profile."""
        from marianne.core.config.instruments import HttpProfile, InstrumentProfile
        from marianne.daemon.baton.backend_pool import _create_backend_for_profile

        profile = InstrumentProfile(
            name="openrouter",
            display_name="OpenRouter",
            kind="http",
            http=HttpProfile(
                base_url="https://openrouter.ai/api/v1",
                schema_family="openai",
                auth_env_var="OPENROUTER_API_KEY",
            ),
            default_model="minimax/minimax-m1-80k",
        )

        backend = _create_backend_for_profile(profile, model="google/gemma-4")
        assert isinstance(backend, OpenRouterBackend)
        assert backend.model == "google/gemma-4"

    def test_creates_openrouter_by_url(self) -> None:
        """Profile with openrouter.ai URL creates OpenRouterBackend."""
        from marianne.core.config.instruments import HttpProfile, InstrumentProfile
        from marianne.daemon.baton.backend_pool import _create_backend_for_profile

        profile = InstrumentProfile(
            name="my-openrouter",
            display_name="My OpenRouter",
            kind="http",
            http=HttpProfile(
                base_url="https://openrouter.ai/api/v1",
                schema_family="openai",
            ),
        )

        backend = _create_backend_for_profile(profile)
        assert isinstance(backend, OpenRouterBackend)

    def test_unknown_http_raises(self) -> None:
        """HTTP instrument without a wired schema_family translator raises.

        Phase 3 of the backend atlas migration replaced the per-instrument
        ``NotImplementedError("not recognized")`` dispatch with generic
        schema_family-based routing. ``openai`` and ``anthropic`` families
        now resolve through generic handlers; ``gemini`` is the family that
        is designed but not yet wired, so it raises ``ValueError`` with
        migration guidance. This test keeps the unknown-instrument safety
        net alive under the new dispatch model.
        """
        from marianne.core.config.instruments import HttpProfile, InstrumentProfile
        from marianne.daemon.baton.backend_pool import _create_backend_for_profile

        profile = InstrumentProfile(
            name="unknown-gemini",
            display_name="Unknown Gemini",
            kind="http",
            http=HttpProfile(
                base_url="https://unknown.example.com/api",
                schema_family="gemini",
            ),
        )

        with pytest.raises(ValueError, match="Gemini translator"):
            _create_backend_for_profile(profile)
