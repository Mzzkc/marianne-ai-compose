"""OpenAI-compatible HTTP backend used by schema_family='openai' instruments.

Relocated from ``marianne.backends.openrouter`` in Phase 4b of the backend
atlas migration. The class name ``OpenRouterBackend`` is preserved for
compatibility with tests and internal callers, but conceptually this is
the generic OpenAI-compatible handler: it speaks the standard OpenAI
chat/completions protocol and works against any HTTP service that exposes
one (OpenRouter, OpenAI proper, self-hosted vLLM, etc.).

Usage is internal only — it is constructed by
``marianne.daemon.baton.backend_pool._build_openai_family_backend``
when dispatching an ``HttpProfile`` whose ``schema_family`` is ``openai``.

Key design decisions:
- Extends Backend ABC with HttpxClientMixin for lazy httpx client lifecycle.
- Model is specified per-request, allowing per-sheet instrument overrides.
- Rate limit detection from HTTP 429 status and Retry-After header.
- Token usage extracted from the standard OpenAI usage response field.
- Free-tier model support (no cost for many models).

Security: API keys are NEVER logged. The key is read from environment
and passed only in the Authorization header. The logging infrastructure
uses SENSITIVE_PATTERNS to automatically redact fields containing
'api_key', 'token', 'secret', etc.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx

from marianne.backends.base import Backend, ExecutionResult, HttpxClientMixin
from marianne.core.errors import ErrorClassifier
from marianne.core.logging import get_logger
from marianne.utils.time import utc_now

_logger = get_logger("backend.openrouter")

# OpenRouter API base URL (without trailing endpoint path)
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Default model — free-tier on OpenRouter
_DEFAULT_MODEL = "minimax/minimax-m1-80k"


class OpenRouterBackend(HttpxClientMixin, Backend):
    """Run prompts via the OpenRouter API (OpenAI-compatible).

    Provides direct HTTP access to 300+ models including free-tier options.
    Uses HttpxClientMixin for lazy, connection-pooled httpx client lifecycle.

    Example usage::

        backend = OpenRouterBackend(
            model="minimax/minimax-m1-80k",
            api_key_env="OPENROUTER_API_KEY",
        )
        result = await backend.execute("Explain quicksort")
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key_env: str = "OPENROUTER_API_KEY",
        max_tokens: int = 16384,
        temperature: float = 0.7,
        timeout_seconds: float = 300.0,
        base_url: str = _OPENROUTER_BASE_URL,
    ) -> None:
        """Initialize OpenRouter backend.

        Args:
            model: Model ID (e.g., 'minimax/minimax-m1-80k', 'google/gemma-4').
            api_key_env: Environment variable containing API key.
            max_tokens: Maximum tokens for response.
            temperature: Sampling temperature (0.0-2.0).
            timeout_seconds: Maximum time for API request.
            base_url: OpenRouter API base URL (without endpoint path).
        """
        if not model:
            raise ValueError("model must be a non-empty string")
        if not api_key_env:
            raise ValueError("api_key_env must be a non-empty string")
        if max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1, got {max_tokens}")
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {timeout_seconds}")

        self.model = model
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self._working_directory: Path | None = None

        # Read API key from environment (may be None — checked at execute time)
        self._api_key: str | None = os.environ.get(api_key_env)

        # Error classifier for rate limit wait extraction
        self._error_classifier = ErrorClassifier()

        # Per-sheet overrides — saved originals for clear_overrides()
        self._saved_model: str | None = None
        self._saved_temperature: float | None = None
        self._saved_max_tokens: int | None = None
        self._has_overrides: bool = False

        # Real-time output logging (set per-sheet by runner)
        self._stdout_log_path: Path | None = None
        self._stderr_log_path: Path | None = None

        # Preamble and extensions for prompt injection
        self._preamble: str | None = None
        self._prompt_extensions: list[str] = []

        # Build auth headers
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Mzzkc/marianne-ai-compose",
            "X-Title": "Marianne AI Compose",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        # HTTP client lifecycle via shared mixin
        self._init_httpx_mixin(
            base_url.rstrip("/"),
            timeout_seconds,
            connect_timeout=10.0,
            headers=headers,
        )

    @classmethod
    def from_config(cls, config: object) -> OpenRouterBackend:
        """Create backend from a BackendConfig.

        Args:
            config: A BackendConfig instance (typed as object to avoid
                circular import — BackendConfig lives in core.config).

        Returns:
            Configured OpenRouterBackend instance.
        """
        model = getattr(config, "model", _DEFAULT_MODEL) or _DEFAULT_MODEL
        timeout = getattr(config, "timeout_seconds", 300.0)
        api_key_env = getattr(config, "api_key_env", "OPENROUTER_API_KEY")
        max_tokens = getattr(config, "max_tokens", 16384)
        temperature = getattr(config, "temperature", 0.7)
        return cls(
            model=model,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout,
        )

    @property
    def name(self) -> str:
        """Human-readable backend name including model."""
        return f"openrouter:{self.model}"

    def apply_overrides(self, overrides: dict[str, object]) -> None:
        """Apply per-sheet overrides for the next execution."""
        if not overrides:
            return
        self._saved_model = self.model
        self._saved_temperature = self.temperature
        self._saved_max_tokens = self.max_tokens
        self._has_overrides = True
        if "model" in overrides:
            self.model = str(overrides["model"])
        if "temperature" in overrides:
            self.temperature = float(overrides["temperature"])  # type: ignore[arg-type]
        if "max_tokens" in overrides:
            self.max_tokens = int(overrides["max_tokens"])  # type: ignore[call-overload]

    def clear_overrides(self) -> None:
        """Restore original backend parameters after per-sheet execution."""
        if not self._has_overrides:
            return
        self.model = self._saved_model  # type: ignore[assignment]
        self.temperature = self._saved_temperature  # type: ignore[assignment]
        self.max_tokens = self._saved_max_tokens  # type: ignore[assignment]
        self._saved_model = None
        self._saved_temperature = None
        self._saved_max_tokens = None
        self._has_overrides = False

    def set_preamble(self, preamble: str | None) -> None:
        """Set the dynamic preamble for the next execution."""
        self._preamble = preamble

    def set_prompt_extensions(self, extensions: list[str]) -> None:
        """Set prompt extensions for the next execution."""
        self._prompt_extensions = [e for e in extensions if e.strip()]

    def set_output_log_path(self, path: Path | None) -> None:
        """Set base path for real-time output logging.

        Args:
            path: Base path for log files (without extension), or None to disable.
        """
        if path is None:
            self._stdout_log_path = None
            self._stderr_log_path = None
        else:
            self._stdout_log_path = path.with_suffix(".stdout.log")
            self._stderr_log_path = path.with_suffix(".stderr.log")

    def _write_log_file(self, log_path: Path | None, content: str) -> None:
        """Write content to a log file if path is set.

        Failures are logged as warnings rather than silently swallowed.

        Args:
            log_path: Path to write to, or None to skip.
            content: Text content to write.
        """
        if log_path is None:
            return
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(content, encoding="utf-8")
        except OSError as e:
            _logger.warning("log_write_failed", path=str(log_path), error=str(e))

    def _build_prompt(self, prompt: str) -> str:
        """Assemble the full prompt with preamble and extensions.

        Args:
            prompt: The base prompt text.

        Returns:
            Assembled prompt string.
        """
        if not self._preamble and not self._prompt_extensions:
            return prompt
        parts: list[str] = []
        if self._preamble:
            parts.append(self._preamble)
        parts.append(prompt)
        if self._prompt_extensions:
            parts.append("\n".join(self._prompt_extensions))
        return "\n".join(parts)

    async def execute(
        self, prompt: str, *, timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute a prompt via the OpenRouter API.

        Sends a chat completion request to OpenRouter's OpenAI-compatible
        endpoint and returns the result.

        Args:
            prompt: The prompt to send.
            timeout_seconds: Per-call timeout override. Logged but not
                enforced (httpx client timeout from __init__ is used).

        Returns:
            ExecutionResult with API response and metadata.
        """
        if timeout_seconds is not None:
            _logger.debug(
                "timeout_override_ignored",
                backend="openrouter",
                requested=timeout_seconds,
                actual=self.timeout_seconds,
            )

        start_time = time.monotonic()
        started_at = utc_now()

        _logger.debug(
            "openrouter_execute_start",
            model=self.model,
            prompt_length=len(prompt),
            max_tokens=self.max_tokens,
        )

        # Check API key before making request
        if not self._api_key:
            duration = time.monotonic() - start_time
            msg = (
                f"API key not found in environment variable: {self.api_key_env}. "
                "Set it with: export OPENROUTER_API_KEY=your-key"
            )
            _logger.error(
                "configuration_error",
                api_key_env=self.api_key_env,
            )
            self._write_log_file(self._stderr_log_path, msg)
            return ExecutionResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr=msg,
                duration_seconds=duration,
                started_at=started_at,
                error_type="configuration",
                error_message=msg,
                model=self.model,
            )

        assembled_prompt = self._build_prompt(prompt)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": assembled_prompt}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        try:
            client = await self._get_client()

            response = await client.post(
                "/chat/completions",
                json=payload,
            )

            duration = time.monotonic() - start_time

            # Handle rate limiting via HTTP status
            if response.status_code == 429:
                return self._handle_rate_limit(response, duration, started_at)

            # Handle other HTTP errors
            if response.status_code >= 400:
                return self._handle_http_error(response, duration, started_at)

            # Parse successful response
            data = response.json()
            return self._parse_success_response(data, duration, started_at)

        except httpx.ConnectError as e:
            duration = time.monotonic() - start_time
            _logger.error("openrouter_connection_error", error=str(e))
            self._write_log_file(self._stderr_log_path, str(e))
            return ExecutionResult(
                success=False,
                exit_code=503,
                stdout="",
                stderr=f"Connection error: {e}",
                duration_seconds=duration,
                started_at=started_at,
                error_type="connection",
                error_message=str(e),
                model=self.model,
            )

        except httpx.TimeoutException as e:
            duration = time.monotonic() - start_time
            _logger.error(
                "openrouter_timeout",
                timeout_seconds=self.timeout_seconds,
                error=str(e),
            )
            self._write_log_file(self._stderr_log_path, str(e))
            return ExecutionResult(
                success=False,
                exit_code=408,
                stdout="",
                stderr=f"Timeout after {self.timeout_seconds}s: {e}",
                duration_seconds=duration,
                started_at=started_at,
                exit_reason="timeout",
                error_type="timeout",
                error_message=f"API timeout after {self.timeout_seconds}s: {e}",
                model=self.model,
            )

        except Exception as e:
            duration = time.monotonic() - start_time
            _logger.exception(
                "openrouter_execute_error",
                model=self.model,
                error=str(e),
            )
            self._write_log_file(self._stderr_log_path, str(e))
            raise

    def _handle_rate_limit(
        self,
        response: httpx.Response,
        duration: float,
        started_at: Any,
    ) -> ExecutionResult:
        """Handle HTTP 429 rate limit response.

        Extracts wait time from Retry-After header or response body.

        Args:
            response: The HTTP 429 response.
            duration: Elapsed time in seconds.
            started_at: Execution start timestamp.

        Returns:
            ExecutionResult with rate limit details.
        """
        body_text = response.text

        # Try Retry-After header first (standard HTTP)
        retry_after = response.headers.get("Retry-After")
        wait_seconds: float | None = None
        if retry_after:
            try:
                wait_seconds = float(retry_after)
            except ValueError:
                pass

        # Fall back to body text parsing
        if wait_seconds is None:
            wait_seconds = self._error_classifier.extract_rate_limit_wait(body_text)

        _logger.warning(
            "openrouter_rate_limited",
            model=self.model,
            retry_after_header=retry_after,
            parsed_wait_seconds=wait_seconds,
            response_length=len(body_text),
        )
        self._write_log_file(self._stderr_log_path, body_text)

        return ExecutionResult(
            success=False,
            exit_code=429,
            stdout="",
            stderr=body_text,
            duration_seconds=duration,
            started_at=started_at,
            rate_limited=True,
            rate_limit_wait_seconds=wait_seconds,
            error_type="rate_limit",
            error_message=f"Rate limited: {body_text[:200]}",
            model=self.model,
        )

    def _handle_http_error(
        self,
        response: httpx.Response,
        duration: float,
        started_at: Any,
    ) -> ExecutionResult:
        """Handle non-429 HTTP error responses.

        Maps common HTTP status codes to error types for the error
        classification system.

        Args:
            response: The error response.
            duration: Elapsed time in seconds.
            started_at: Execution start timestamp.

        Returns:
            ExecutionResult with classified error details.
        """
        status = response.status_code
        body_text = response.text

        # Map status codes to error types
        if status == 401:
            error_type = "authentication"
            _logger.error(
                "openrouter_auth_error",
                api_key_env=self.api_key_env,
                status_code=status,
            )
        elif status == 400:
            error_type = "bad_request"
            _logger.error(
                "openrouter_bad_request",
                model=self.model,
                status_code=status,
                response_length=len(body_text),
            )
        elif status == 402:
            error_type = "insufficient_credits"
            _logger.error(
                "openrouter_insufficient_credits",
                model=self.model,
                status_code=status,
            )
        elif status == 503:
            error_type = "service_unavailable"
            _logger.error(
                "openrouter_service_unavailable",
                model=self.model,
                status_code=status,
            )
        else:
            error_type = "api_error"
            _logger.error(
                "openrouter_http_error",
                model=self.model,
                status_code=status,
                response_length=len(body_text),
            )

        self._write_log_file(self._stderr_log_path, body_text)

        return ExecutionResult(
            success=False,
            exit_code=status,
            stdout="",
            stderr=body_text,
            duration_seconds=duration,
            started_at=started_at,
            error_type=error_type,
            error_message=f"HTTP {status}: {body_text[:200]}",
            model=self.model,
        )

    def _parse_success_response(
        self,
        data: dict[str, Any],
        duration: float,
        started_at: Any,
    ) -> ExecutionResult:
        """Parse a successful OpenRouter API response.

        Extracts content text and token usage from the OpenAI-compatible
        response format.

        Args:
            data: Parsed JSON response.
            duration: Elapsed time in seconds.
            started_at: Execution start timestamp.

        Returns:
            ExecutionResult with response content and token usage.
        """
        # Extract content from choices array (OpenAI format)
        choices = data.get("choices", [])
        content = ""
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "") or ""

        # Extract token usage (OpenAI format)
        usage = data.get("usage", {})
        input_tokens: int | None = usage.get("prompt_tokens") if usage else None
        output_tokens: int | None = usage.get("completion_tokens") if usage else None
        tokens_used: int | None = None
        if input_tokens is not None and output_tokens is not None:
            tokens_used = input_tokens + output_tokens

        # The model actually used may differ from what was requested
        actual_model = data.get("model", self.model)

        _logger.info(
            "openrouter_execute_complete",
            model=actual_model,
            duration_seconds=duration,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=tokens_used,
            response_length=len(content),
        )

        self._write_log_file(self._stdout_log_path, content)

        return ExecutionResult(
            success=True,
            exit_code=0,
            stdout=content,
            stderr="",
            duration_seconds=duration,
            started_at=started_at,
            model=actual_model,
            tokens_used=tokens_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def health_check(self) -> bool:
        """Check if the OpenRouter API is reachable and authenticated.

        Uses the /models endpoint (lightweight, no token consumption)
        to verify connectivity and authentication.

        Returns:
            True if healthy, False otherwise.
        """
        if not self._api_key:
            _logger.warning(
                "health_check_failed",
                error_type="MissingAPIKey",
                error=f"No API key configured — set {self.api_key_env}",
            )
            return False

        try:
            client = await self._get_client()
            response = await client.get("/models", timeout=10.0)
            if response.status_code == 200:
                return True
            _logger.warning(
                "openrouter_health_check_failed",
                status_code=response.status_code,
            )
            return False
        except (httpx.HTTPError, OSError, ValueError) as e:
            _logger.warning(
                "openrouter_health_check_error",
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    async def availability_check(self) -> bool:
        """Check if the backend can be initialized without consuming API quota.

        Verifies that the API key is present and the httpx client can be
        created. Does NOT make any HTTP requests.
        """
        if not self._api_key:
            return False
        try:
            await self._get_client()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close HTTP client and release resources."""
        await self._close_httpx_client()
