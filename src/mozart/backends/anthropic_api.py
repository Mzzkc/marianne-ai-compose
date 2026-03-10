"""Anthropic API backend using the official SDK.

Direct API access for Claude models without needing the CLI installed.
Provides rate limit detection, token tracking, and graceful error handling.

Security Note: API keys are NEVER logged. The logging infrastructure uses
SENSITIVE_PATTERNS to automatically redact fields containing 'api_key', 'token',
'secret', etc.
"""

import asyncio
import os
import time
from pathlib import Path

import anthropic

from mozart.backends.base import Backend, ExecutionResult
from mozart.core.config import BackendConfig
from mozart.core.errors import ErrorClassifier
from mozart.core.logging import get_logger

_logger = get_logger("backend.anthropic_api")


class AnthropicApiBackend(Backend):
    """Run prompts directly via the Anthropic API.

    Uses the official anthropic SDK for direct API access.
    Supports all Claude models available through the API.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250929",
        api_key_env: str = "ANTHROPIC_API_KEY",
        max_tokens: int = 16384,
        temperature: float = 0.7,
        timeout_seconds: float = 300.0,  # 5 minute default for API
    ):
        """Initialize API backend.

        Args:
            model: Model ID to use (e.g., claude-sonnet-4-5-20250929)
            api_key_env: Environment variable containing API key
            max_tokens: Maximum tokens for response
            temperature: Sampling temperature (0.0-1.0)
            timeout_seconds: Maximum time for API request
        """
        self.model = model
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self._working_directory: Path | None = None

        # Real-time output logging paths (set per-sheet by runner)
        # Matches ClaudeCliBackend pattern for observability parity
        self._stdout_log_path: Path | None = None
        self._stderr_log_path: Path | None = None

        # Get API key from environment
        self._api_key = os.environ.get(api_key_env)

        # Create async client (lazily initialized in execute)
        self._client: anthropic.AsyncAnthropic | None = None
        self._client_lock = asyncio.Lock()

        # Use shared ErrorClassifier for consistent error detection
        self._error_classifier = ErrorClassifier()

        # Per-sheet overrides (GH#78) — saved originals for clear_overrides()
        self._saved_model: str | None = None
        self._saved_temperature: float | None = None
        self._saved_max_tokens: int | None = None
        self._has_overrides: bool = False

    @classmethod
    def from_config(cls, config: BackendConfig) -> "AnthropicApiBackend":
        """Create backend from configuration."""
        return cls(
            model=config.model,
            api_key_env=config.api_key_env,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout_seconds=config.timeout_seconds,
        )

    @property
    def name(self) -> str:
        return "anthropic-api"

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

    def set_output_log_path(self, path: Path | None) -> None:
        """Set base path for real-time output logging.

        Called per-sheet by runner to enable writing API responses to log files.
        Provides observability parity with ClaudeCliBackend.

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

        Failures are logged as warnings rather than silently swallowed,
        providing visibility into filesystem issues.

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

    async def _get_client(self) -> anthropic.AsyncAnthropic:
        """Get or create the async Anthropic client.

        Uses an asyncio.Lock to prevent concurrent coroutines from
        creating duplicate clients (race on lazy initialization).
        """
        async with self._client_lock:
            if self._client is None:
                if not self._api_key:
                    raise RuntimeError(
                        f"API key not found in environment variable: {self.api_key_env}"
                    )
                self._client = anthropic.AsyncAnthropic(
                    api_key=self._api_key,
                    timeout=self.timeout_seconds,
                )
            return self._client

    async def execute(
        self, prompt: str, *, timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute a prompt via the Anthropic API.

        Args:
            prompt: The prompt to send to Claude
            timeout_seconds: Per-call timeout override. API backend uses its
                own HTTP timeout from ``__init__``; per-call override is
                logged but not enforced.

        Returns:
            ExecutionResult with API response and metadata
        """
        if timeout_seconds is not None:
            _logger.debug(
                "timeout_override_ignored",
                backend="anthropic_api",
                requested=timeout_seconds,
                actual=self.timeout_seconds,
            )
        start_time = time.monotonic()

        # Log API request at DEBUG level (never log prompt content or API keys)
        _logger.debug(
            "api_request",
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            prompt_length=len(prompt),
            # Note: prompt preview intentionally omitted for security
        )

        try:
            client = await self._get_client()

            response = await client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            duration = time.monotonic() - start_time

            # Extract response text
            content_blocks = response.content
            response_text = ""
            for block in content_blocks:
                if hasattr(block, "text"):
                    response_text += block.text

            # Calculate tokens used
            input_tokens = response.usage.input_tokens if response.usage else None
            output_tokens = response.usage.output_tokens if response.usage else None
            tokens_used = (
                input_tokens + output_tokens
                if input_tokens is not None and output_tokens is not None
                else None
            )

            # Log successful response at INFO level
            _logger.info(
                "api_response",
                duration_seconds=duration,
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=tokens_used,
                response_length=len(response_text),
            )

            # Write API response to log file for post-mortem analysis
            self._write_log_file(self._stdout_log_path, response_text)

            return ExecutionResult(
                success=True,
                exit_code=0,
                stdout=response_text,
                stderr="",
                duration_seconds=duration,
                model=self.model,
                tokens_used=tokens_used,  # Legacy field for backwards compatibility
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        except anthropic.RateLimitError as e:
            duration = time.monotonic() - start_time
            _logger.warning(
                "rate_limit_error",
                duration_seconds=duration,
                model=self.model,
                error_message=str(e),
            )
            self._write_log_file(self._stderr_log_path, str(e))
            return ExecutionResult(
                success=False,
                exit_code=429,
                stdout="",
                stderr=str(e),
                duration_seconds=duration,
                rate_limited=True,
                error_type="rate_limit",
                error_message=f"Rate limited: {e}",
                model=self.model,
            )

        except anthropic.AuthenticationError as e:
            duration = time.monotonic() - start_time
            # Note: Never log API key details, just that auth failed
            _logger.error(
                "authentication_error",
                duration_seconds=duration,
                model=self.model,
                api_key_env=self.api_key_env,  # Only log env var name, not value
            )
            self._write_log_file(self._stderr_log_path, str(e))
            return ExecutionResult(
                success=False,
                exit_code=401,
                stdout="",
                stderr=str(e),
                duration_seconds=duration,
                error_type="authentication",
                error_message=f"Authentication failed: {e}",
                model=self.model,
            )

        except anthropic.BadRequestError as e:
            duration = time.monotonic() - start_time
            _logger.error(
                "bad_request_error",
                duration_seconds=duration,
                model=self.model,
                error_message=str(e),
            )
            self._write_log_file(self._stderr_log_path, str(e))
            return ExecutionResult(
                success=False,
                exit_code=400,
                stdout="",
                stderr=str(e),
                duration_seconds=duration,
                error_type="bad_request",
                error_message=f"Bad request: {e}",
                model=self.model,
            )

        except anthropic.APITimeoutError as e:
            duration = time.monotonic() - start_time
            _logger.error(
                "api_timeout_error",
                duration_seconds=duration,
                timeout_seconds=self.timeout_seconds,
                model=self.model,
            )
            self._write_log_file(self._stderr_log_path, str(e))
            return ExecutionResult(
                success=False,
                exit_code=408,
                stdout="",
                stderr=str(e),
                duration_seconds=duration,
                error_type="timeout",
                error_message=f"API timeout after {self.timeout_seconds}s: {e}",
                model=self.model,
            )

        except anthropic.APIConnectionError as e:
            duration = time.monotonic() - start_time
            _logger.error(
                "api_connection_error",
                duration_seconds=duration,
                model=self.model,
                error_message=str(e),
            )
            self._write_log_file(self._stderr_log_path, str(e))
            return ExecutionResult(
                success=False,
                exit_code=503,
                stdout="",
                stderr=str(e),
                duration_seconds=duration,
                error_type="connection",
                error_message=f"Connection error: {e}",
                model=self.model,
            )

        except anthropic.APIStatusError as e:
            duration = time.monotonic() - start_time
            status_code = e.status_code if hasattr(e, "status_code") else 500
            # Check if this is a rate limit error by status code or message
            rate_limited = self._detect_rate_limit(stderr=str(e), exit_code=status_code)

            if rate_limited:
                _logger.warning(
                    "rate_limit_error",
                    duration_seconds=duration,
                    status_code=status_code,
                    model=self.model,
                    error_message=str(e),
                )
            else:
                _logger.error(
                    "api_status_error",
                    duration_seconds=duration,
                    status_code=status_code,
                    model=self.model,
                    error_message=str(e),
                )

            self._write_log_file(self._stderr_log_path, str(e))
            return ExecutionResult(
                success=False,
                exit_code=status_code,
                stdout="",
                stderr=str(e),
                duration_seconds=duration,
                rate_limited=rate_limited,
                error_type="rate_limit" if rate_limited else "api_error",
                error_message=str(e),
                model=self.model,
            )

        except RuntimeError as e:
            # API key not found
            duration = time.monotonic() - start_time
            _logger.error(
                "configuration_error",
                duration_seconds=duration,
                error_message=str(e),
                api_key_env=self.api_key_env,  # Only log env var name, not value
            )
            self._write_log_file(self._stderr_log_path, str(e))
            return ExecutionResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr=str(e),
                duration_seconds=duration,
                error_type="configuration",
                error_message=str(e),
                model=self.model,
            )

        except Exception as e:
            duration = time.monotonic() - start_time
            _logger.exception(
                "unexpected_error",
                duration_seconds=duration,
                model=self.model,
                error_message=str(e),
            )
            self._write_log_file(self._stderr_log_path, str(e))
            raise

    async def health_check(self) -> bool:
        """Check if the API is available and authenticated.

        Uses a minimal prompt to verify connectivity.
        """
        if not self._api_key:
            _logger.warning(
                "health_check_failed",
                error_type="MissingAPIKey",
                error="No API key configured — set ANTHROPIC_API_KEY",
            )
            return False

        try:
            client = await self._get_client()
            # Minimal prompt to verify API access
            response = await client.messages.create(
                model=self.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Reply with only: ok"}],
            )
            # Check we got a response
            return len(response.content) > 0
        except (anthropic.APIError, TimeoutError, OSError) as e:
            _logger.warning("health_check_failed", error_type=type(e).__name__, error=str(e))
            return False

    async def availability_check(self) -> bool:
        """Check if the API client can be created without making an API call.

        Unlike health_check(), this does NOT send a request or consume
        API quota. Used after quota exhaustion waits.
        """
        if not self._api_key:
            return False
        try:
            await self._get_client()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close the async client connection (idempotent)."""
        if self._client is not None:
            client = self._client
            self._client = None
            try:
                await client.close()
            except (OSError, RuntimeError, anthropic.APIError):
                _logger.debug("client_close_error", exc_info=True)
