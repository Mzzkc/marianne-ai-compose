"""OpenRouter HTTP backend for direct API access.

Sends prompts to the OpenRouter API (OpenAI-compatible) for execution
with any of 300+ models. Extends the existing HTTP backend pattern
(same shape as AnthropicApiBackend, OllamaBackend).

Key capabilities:
- Model specified per-request (the backend routes to any OpenRouter model)
- Rate limit detection from response headers and error codes
- Token usage from response usage field
- Free-tier model support (no cost for many models)

Security: API keys are NEVER logged. The key is read from environment
and passed only in the Authorization header.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from marianne.backends.base import Backend, ExecutionResult
from marianne.core.config import BackendConfig
from marianne.core.errors import ErrorClassifier
from marianne.core.logging import get_logger
from marianne.utils.time import utc_now

_logger = get_logger("backend.openrouter")

# OpenRouter API base URL
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterBackend(Backend):
    """Run prompts via the OpenRouter API (OpenAI-compatible).

    Provides direct HTTP access to 300+ models including free-tier options.
    Uses the same pattern as AnthropicApiBackend for consistency.

    The backend sends prompts as chat completion requests and parses
    the OpenAI-compatible response format.
    """

    def __init__(
        self,
        model: str = "minimax/minimax-2.5",
        api_key_env: str = "OPENROUTER_API_KEY",
        max_tokens: int = 16384,
        temperature: float = 0.7,
        timeout_seconds: float = 300.0,
        base_url: str = _OPENROUTER_BASE_URL,
    ) -> None:
        """Initialize OpenRouter backend.

        Args:
            model: Model ID (e.g., 'minimax/minimax-2.5', 'google/gemma-4')
            api_key_env: Environment variable containing API key
            max_tokens: Maximum tokens for response
            temperature: Sampling temperature (0.0-1.0)
            timeout_seconds: Maximum time for API request
            base_url: OpenRouter API endpoint URL
        """
        self.model = model
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url
        self._working_directory: Path | None = None

        # Real-time output logging paths (set per-sheet by runner)
        self._stdout_log_path: Path | None = None
        self._stderr_log_path: Path | None = None

        # Get API key from environment
        self._api_key = os.environ.get(api_key_env)

        # Lazy HTTP client
        self._client: object | None = None
        self._client_lock = asyncio.Lock()

        # Shared error classifier
        self._error_classifier = ErrorClassifier()

        # Per-sheet overrides
        self._saved_model: str | None = None
        self._saved_temperature: float | None = None
        self._saved_max_tokens: int | None = None
        self._has_overrides: bool = False

    @classmethod
    def from_config(cls, config: BackendConfig) -> OpenRouterBackend:
        """Create backend from configuration."""
        return cls(
            model=config.model or "minimax/minimax-2.5",
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout_seconds=config.timeout_seconds,
        )

    @property
    def name(self) -> str:
        """Backend identifier for logging and diagnostics."""
        return "openrouter"

    def set_working_directory(self, path: Path) -> None:
        """Set working directory for execution."""
        self._working_directory = path

    def set_output_log_paths(
        self, stdout_path: Path | None, stderr_path: Path | None,
    ) -> None:
        """Set real-time output log paths for observability."""
        self._stdout_log_path = stdout_path
        self._stderr_log_path = stderr_path

    def apply_overrides(self, overrides: dict[str, object]) -> None:
        """Apply per-sheet instrument config overrides."""
        if self._has_overrides:
            self.clear_overrides()

        self._saved_model = self.model
        self._saved_temperature = self.temperature
        self._saved_max_tokens = self.max_tokens
        self._has_overrides = True

        if "model" in overrides:
            self.model = str(overrides["model"])
        if "temperature" in overrides:
            self.temperature = float(str(overrides["temperature"]))
        if "max_tokens" in overrides:
            self.max_tokens = int(str(overrides["max_tokens"]))

    def clear_overrides(self) -> None:
        """Restore original config after per-sheet overrides."""
        if self._has_overrides:
            assert self._saved_model is not None
            assert self._saved_temperature is not None
            assert self._saved_max_tokens is not None
            self.model = self._saved_model
            self.temperature = self._saved_temperature
            self.max_tokens = self._saved_max_tokens
            self._has_overrides = False

    async def execute(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute a prompt via the OpenRouter API.

        Sends a chat completion request and returns the result. Rate limit
        detection uses response headers and HTTP status codes.

        Args:
            prompt: The user prompt to send
            timeout_seconds: Override timeout for this request

        Returns:
            ExecutionResult with the model's response
        """
        timeout = timeout_seconds or self.timeout_seconds
        start_time = time.monotonic()
        started_at = utc_now()

        if not self._api_key:
            _logger.error(
                "openrouter_no_api_key",
                env_var=self.api_key_env,
            )
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"No API key found in ${self.api_key_env}",
                duration_seconds=time.monotonic() - start_time,
                exit_code=1,
                exit_reason="error",
                started_at=started_at,
                error_type="E401",
            )

        # Build messages
        messages: list[dict[str, str]] = []
        messages.append({"role": "user", "content": prompt})

        # Build request payload
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    self.base_url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "HTTP-Referer": "https://github.com/Mzzkc/marianne-ai-compose",
                        "X-Title": "Marianne AI Compose",
                        "Content-Type": "application/json",
                    },
                )

            duration = time.monotonic() - start_time

            # Rate limit detection
            rate_limited = response.status_code == 429
            rate_limit_wait: float | None = None
            if rate_limited:
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    try:
                        rate_limit_wait = float(retry_after)
                    except (ValueError, TypeError):
                        pass

            if response.status_code >= 400:
                error_body = response.text
                _logger.warning(
                    "openrouter_api_error",
                    status_code=response.status_code,
                    model=self.model,
                    duration_seconds=duration,
                )
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=error_body[:10240],
                    duration_seconds=duration,
                    exit_code=response.status_code,
                    exit_reason="error",
                    started_at=started_at,
                    rate_limited=rate_limited,
                    rate_limit_wait_seconds=rate_limit_wait,
                    error_type="E101" if rate_limited else "E500",
                )

            # Parse successful response
            data = response.json()
            choices = data.get("choices", [])
            content = ""
            if choices:
                message = choices[0].get("message", {})
                content = message.get("content", "")

            # Extract token usage
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

            _logger.info(
                "openrouter_execution_complete",
                model=self.model,
                duration_seconds=duration,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

            return ExecutionResult(
                success=True,
                stdout=content,
                stderr="",
                duration_seconds=duration,
                exit_code=200,
                exit_reason="completed",
                started_at=started_at,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        except TimeoutError:
            duration = time.monotonic() - start_time
            _logger.warning(
                "openrouter_timeout",
                model=self.model,
                timeout_seconds=timeout,
                duration_seconds=duration,
            )
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Request timed out after {timeout}s",
                duration_seconds=duration,
                exit_reason="timeout",
                started_at=started_at,
                error_type="E001",
            )

        except Exception as exc:
            duration = time.monotonic() - start_time
            _logger.error(
                "openrouter_execution_error",
                model=self.model,
                error=str(exc),
                duration_seconds=duration,
            )
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=str(exc)[:10240],
                duration_seconds=duration,
                exit_code=1,
                exit_reason="error",
                started_at=started_at,
                error_type="E900",
            )

    async def health_check(self) -> bool:
        """Check if the OpenRouter API is reachable.

        Performs a lightweight models list request to verify connectivity.
        Does NOT consume API quota.
        """
        if not self._api_key:
            return False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            return response.status_code == 200
        except Exception:
            return False

    async def cleanup(self) -> None:
        """Clean up resources."""
        self._client = None
