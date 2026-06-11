"""Tests for Marianne execution backends.

Tests cover:
- AnthropicApiBackend: API client, error handling, rate limit detection
- ClaudeCliBackend: Basic structure (CLI tests need integration tests)
- Backend logging: Verifies appropriate log levels and content
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

from marianne.backends.anthropic_api import AnthropicApiBackend

# ============================================================================
# AnthropicApiBackend Tests
# ============================================================================


class TestAnthropicApiBackendInit:
    """Test AnthropicApiBackend initialization."""

    def test_init_defaults(self) -> None:
        """Test default initialization values."""
        backend = AnthropicApiBackend()
        assert backend.model == "claude-sonnet-4-5-20250929"
        assert backend.api_key_env == "ANTHROPIC_API_KEY"
        assert backend.max_tokens == 16384
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
        assert result.model == "claude-sonnet-4-5-20250929"
        assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_execute_rate_limit_error(self, backend_with_key: AnthropicApiBackend) -> None:
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
    async def test_execute_bad_request_error(self, backend_with_key: AnthropicApiBackend) -> None:
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
    async def test_execute_timeout_error(self, backend_with_key: AnthropicApiBackend) -> None:
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
    async def test_execute_connection_error(self, backend_with_key: AnthropicApiBackend) -> None:
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

    def test_exit_code_zero_stdout_not_scanned(
        self,
        backend: AnthropicApiBackend,
    ) -> None:
        """On exit_code=0, stdout is NOT scanned for rate limits (F-497).

        Tools handle rate limits internally and exit 0, leaving rate limit
        text in stdout. Scanning stdout on success causes false positives
        that block dispatch for all jobs sharing the instrument profile.
        """
        assert backend._detect_rate_limit("capacity exceeded", "", exit_code=0) is False
        assert backend._detect_rate_limit("rate limit exceeded", "", exit_code=0) is False
        assert backend._detect_rate_limit("429 error", "", exit_code=0) is False

    def test_exit_code_zero_stderr_still_scanned(
        self,
        backend: AnthropicApiBackend,
    ) -> None:
        """On exit_code=0, stderr IS still scanned for rate limits.

        If the tool writes rate limit warnings to stderr while succeeding,
        that's a real signal worth capturing.
        """
        assert backend._detect_rate_limit("", "rate limit exceeded", exit_code=0) is True

    def test_exit_code_one_with_rate_limit_text(self, backend: AnthropicApiBackend) -> None:
        """Failed execution with rate limit text should still be detected."""
        assert backend._detect_rate_limit("rate limit exceeded", "", exit_code=1) is True

    def test_exit_code_none_not_rate_limited(self, backend: AnthropicApiBackend) -> None:
        """When exit_code is None (e.g. signal kill), don't falsely classify as rate-limited."""
        assert backend._detect_rate_limit("rate limit exceeded", "", exit_code=None) is False

    def test_exit_code_none_all_patterns_not_rate_limited(
        self,
        backend: AnthropicApiBackend,
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
    async def test_health_check_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
    async def test_health_check_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test health check failure on API error."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        backend = AnthropicApiBackend()

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APIError(
                "API error",
                request=httpx.Request("POST", "https://api.anthropic.com"),
                body=None,
            )
        )

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.health_check()

        assert result is False


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
    def configure_test_logging(self, captured_logs: list[dict[str, Any]]) -> None:
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
        assert request_logs[0]["model"] == "claude-sonnet-4-5-20250929"
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
        rate_limit_logs = [log for log in captured_logs if log.get("event") == "rate_limit_error"]
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
        auth_logs = [log for log in captured_logs if log.get("event") == "authentication_error"]
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
        with patch.object(backend, "_get_client", side_effect=RuntimeError("API key error")):
            await backend.execute("test prompt")

        # Check ALL log entries to ensure the API key never appears
        all_log_content = str(captured_logs)
        assert test_key not in all_log_content
# ============================================================================
# Per-Sheet Backend Override Tests (GH#78)
# ============================================================================


class TestAnthropicApiBackendOverrides:
    """Test per-sheet override application for AnthropicApiBackend."""

    def test_apply_overrides_model(self) -> None:
        """Apply model override changes model attribute."""
        backend = AnthropicApiBackend(model="claude-sonnet-4-5-20250929")
        backend.apply_overrides({"model": "claude-opus-4-6"})
        assert backend.model == "claude-opus-4-6"

    def test_clear_overrides_restores_original(self) -> None:
        """Clear overrides restores original model."""
        backend = AnthropicApiBackend(model="claude-sonnet-4-5-20250929", temperature=0.7)
        backend.apply_overrides({"model": "claude-opus-4-6", "temperature": 0.2})
        assert backend.model == "claude-opus-4-6"
        assert backend.temperature == 0.2
        backend.clear_overrides()
        assert backend.model == "claude-sonnet-4-5-20250929"
        assert backend.temperature == 0.7

    def test_apply_overrides_partial(self) -> None:
        """Apply only some overrides leaves others unchanged."""
        backend = AnthropicApiBackend(model="claude-sonnet-4-5-20250929", temperature=0.7)
        backend.apply_overrides({"temperature": 0.0})
        assert backend.model == "claude-sonnet-4-5-20250929"  # Unchanged
        assert backend.temperature == 0.0

    def test_clear_overrides_noop_without_apply(self) -> None:
        """Clearing overrides without applying is a no-op."""
        backend = AnthropicApiBackend(model="claude-sonnet-4-5-20250929")
        backend.clear_overrides()  # Should not raise
        assert backend.model == "claude-sonnet-4-5-20250929"

    def test_apply_overrides_max_tokens(self) -> None:
        """Apply max_tokens override."""
        backend = AnthropicApiBackend(max_tokens=8192)
        backend.apply_overrides({"max_tokens": 16384})
        assert backend.max_tokens == 16384
        backend.clear_overrides()
        assert backend.max_tokens == 8192

    def test_apply_empty_overrides_noop(self) -> None:
        """Empty overrides dict is a no-op."""
        backend = AnthropicApiBackend(model="claude-sonnet-4-5-20250929")
        backend.apply_overrides({})
        assert backend.model == "claude-sonnet-4-5-20250929"
        assert not backend._has_overrides
