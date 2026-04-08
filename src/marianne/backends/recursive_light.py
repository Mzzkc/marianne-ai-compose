"""Recursive Light backend using HTTP API.

Connects Marianne to Recursive Light Framework for TDF-aligned
judgment and confidence scoring via HTTP API bridge.

Phase 3: Language Bridge implementation.
"""

import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from marianne.core.config import BackendConfig

from marianne.backends.base import Backend, ExecutionResult, HttpxClientMixin
from marianne.core.logging import get_logger
from marianne.utils.time import utc_now

_logger = get_logger("backend.recursive_light")


class RecursiveLightBackend(HttpxClientMixin, Backend):
    """Execute prompts via Recursive Light HTTP API.

    Uses httpx.AsyncClient to communicate with the Recursive Light
    server for TDF-aligned processing with confidence scoring,
    domain activations, and boundary state tracking.

    The RL server provides dual-LLM processing:
    - LLM #1 (unconscious): Confidence assessment and domain activation
    - LLM #2 (conscious): Response generation with accumulated wisdom

    Attributes:
        rl_endpoint: Base URL for the Recursive Light API.
        user_id: Unique identifier for this Marianne instance.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        rl_endpoint: str = "http://localhost:8080",
        user_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Initialize Recursive Light backend.

        Args:
            rl_endpoint: Base URL for the Recursive Light API server.
                Defaults to localhost:8080 for local development.
            user_id: Unique identifier for this Marianne instance.
                Generates a UUID if not provided.
            timeout: Request timeout in seconds. Defaults to 30.0.
        """
        self.rl_endpoint = rl_endpoint.rstrip("/")
        self.user_id = user_id or str(uuid.uuid4())
        self.timeout = timeout
        self._working_directory: Path | None = None

        # HTTP client lifecycle via shared mixin
        self._init_httpx_mixin(
            self.rl_endpoint,
            self.timeout,
            headers={
                "Content-Type": "application/json",
                "X-Marianne-User-ID": self.user_id,
            },
        )

    @classmethod
    def from_config(cls, config: "BackendConfig") -> "RecursiveLightBackend":
        """Create backend from configuration."""
        rl_config = config.recursive_light
        return cls(
            rl_endpoint=rl_config.endpoint,
            user_id=rl_config.user_id,
            timeout=rl_config.timeout,
        )

    @property
    def name(self) -> str:
        """Human-readable backend name."""
        return "recursive-light"

    async def execute(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute a prompt through Recursive Light API.

        Sends the prompt to RL's /api/process endpoint and parses
        the response for text output plus RL-specific metadata
        (confidence, domain activations, boundary states, quality).

        Args:
            prompt: The prompt to send to Recursive Light.
            timeout_seconds: Per-call timeout override. RL backend uses its
                own HTTP timeout from ``__init__``; per-call override is
                logged but not enforced.

        Returns:
            ExecutionResult with output text and RL metadata populated.
            On connection errors, returns a failed result with graceful
            error handling (not raising exceptions).
        """
        if timeout_seconds is not None:
            _logger.debug(
                "timeout_override_ignored",
                backend="recursive_light",
                requested=timeout_seconds,
                actual=self.timeout,
            )
        start_time = time.monotonic()
        started_at = utc_now()

        # Log HTTP request details at DEBUG level
        _logger.debug(
            "http_request",
            endpoint=f"{self.rl_endpoint}/api/process",
            user_id=self.user_id,
            timeout=self.timeout,
            prompt_length=len(prompt),
        )

        try:
            client = await self._get_client()

            # Build request payload
            payload = {
                "user_id": self.user_id,
                "message": prompt,
            }

            # POST to RL process endpoint
            response = await client.post("/api/process", json=payload)
            duration = time.monotonic() - start_time

            if response.status_code != 200:
                _logger.error(
                    "api_error_response",
                    duration_seconds=duration,
                    status_code=response.status_code,
                    response_text=response.text[:500] if response.text else None,
                )
                return ExecutionResult(
                    success=False,
                    exit_code=response.status_code,
                    stdout="",
                    stderr=f"RL API error: {response.status_code} - {response.text}",
                    duration_seconds=duration,
                    started_at=started_at,
                    error_type="api_error",
                    error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                )

            # Parse response JSON
            data = response.json()

            # Parse response into ExecutionResult
            result = self._parse_rl_response(data, duration, started_at)

            _logger.info(
                "http_response",
                duration_seconds=duration,
                status_code=response.status_code,
                response_length=len(result.stdout) if result.stdout else 0,
            )

            return result

        except httpx.ConnectError as e:
            duration = time.monotonic() - start_time
            _logger.warning(
                "connection_error",
                duration_seconds=duration,
                endpoint=self.rl_endpoint,
                error_message=str(e),
            )
            return ExecutionResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr=f"Connection error: {e}",
                duration_seconds=duration,
                started_at=started_at,
                error_type="connection_error",
                error_message=f"Failed to connect to RL at {self.rl_endpoint}: {e}",
            )

        except httpx.TimeoutException as e:
            duration = time.monotonic() - start_time
            _logger.warning(
                "request_timeout",
                duration_seconds=duration,
                timeout_seconds=self.timeout,
                endpoint=self.rl_endpoint,
            )
            return ExecutionResult(
                success=False,
                exit_code=124,  # Timeout exit code
                stdout="",
                stderr=f"Request timed out: {e}",
                duration_seconds=duration,
                started_at=started_at,
                error_type="timeout",
                error_message=f"Timed out after {self.timeout}s",
            )

        except Exception as e:
            duration = time.monotonic() - start_time
            _logger.exception(
                "unexpected_error",
                duration_seconds=duration,
                endpoint=self.rl_endpoint,
                error_message=str(e),
            )
            raise

    def _parse_rl_response(
        self,
        data: dict[str, Any],
        duration: float,
        started_at: datetime,
    ) -> ExecutionResult:
        """Parse Recursive Light API response into ExecutionResult.

        Extracts the response text from the RL API JSON response.

        Args:
            data: JSON response from RL API.
            duration: Execution duration in seconds.
            started_at: When execution started.

        Returns:
            ExecutionResult with response text as stdout.
        """
        response_text = data.get("response", "")
        if not isinstance(response_text, str):
            response_text = str(response_text)

        return ExecutionResult(
            success=True,
            exit_code=0,
            stdout=response_text,
            stderr="",
            duration_seconds=duration,
            started_at=started_at,
        )

    async def health_check(self) -> bool:
        """Check if Recursive Light server is available and responding.

        Attempts to reach the RL health endpoint (or root) to verify
        connectivity before starting a job.

        Returns:
            True if RL server is healthy and responding, False otherwise.
        """
        try:
            client = await self._get_client()

            # Try health endpoint first, then fall back to root
            for endpoint in ("/health", "/api/health", "/"):
                try:
                    response = await client.get(endpoint)
                    if response.status_code == 200:
                        return True
                except httpx.HTTPStatusError:
                    continue

            return False

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            _logger.debug("health_check_unreachable", error=f"{type(e).__name__}: {e}")
            return False
        except (httpx.HTTPError, OSError, RuntimeError) as e:
            _logger.warning("health_check_failed", error=f"{type(e).__name__}: {e}")
            return False

    async def close(self) -> None:
        """Close the HTTP client connection.

        Should be called when done using the backend to clean up resources.
        """
        await self._close_httpx_client()


