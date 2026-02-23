"""Abstract base for Claude execution backends."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import httpx

from mozart.core.errors import ErrorCategory, ErrorClassifier
from mozart.utils.time import utc_now

# Type alias for exit reasons - provides exhaustive pattern matching
ExitReason = Literal["completed", "timeout", "killed", "error"]


@dataclass
class ExecutionResult:
    """Result of executing a prompt through a backend.

    Captures all relevant output and metadata for validation and debugging.

    Note: Fields are ordered with required fields first, then optional fields
    with defaults, as required by Python dataclasses.
    """

    # Required fields (no defaults) - must come first
    success: bool
    """Whether the execution completed without error (exit code 0)."""

    stdout: str
    """Standard output from the command."""

    stderr: str
    """Standard error from the command."""

    duration_seconds: float
    """Execution duration in seconds."""

    # Optional fields with defaults
    exit_code: int | None = None
    """Process exit code or HTTP status code. None if killed by signal."""

    exit_signal: int | None = None
    """Signal number if process was killed by a signal (e.g., 9=SIGKILL, 15=SIGTERM).

    On Unix, when a process is killed by a signal, returncode = -signal_number.
    This field extracts that signal for clearer diagnostics.
    """

    exit_reason: ExitReason = "completed"
    """Why the execution ended:
    - completed: Normal exit (exit_code set)
    - timeout: Process was killed due to timeout
    - killed: Process was killed by external signal
    - error: Internal error prevented execution
    """

    started_at: datetime = field(default_factory=utc_now)
    """When execution started."""

    # Error classification hints
    rate_limited: bool = False
    """Whether rate limiting was detected."""

    error_type: str | None = None
    """Classified error type if failed."""

    error_message: str | None = None
    """Human-readable error message."""

    # Metadata
    model: str | None = None
    """Model used for execution."""

    tokens_used: int | None = None
    """Total tokens consumed (API backend only).

    .. deprecated::
        Use ``input_tokens + output_tokens`` instead. This field will be
        removed in a future version.
        Equivalent: ``tokens_used == (input_tokens or 0) + (output_tokens or 0)``.
    """

    input_tokens: int | None = None
    """Input tokens consumed (prompt tokens). None if not available from backend."""

    output_tokens: int | None = None
    """Output tokens consumed (completion tokens). None if not available from backend."""

    def __post_init__(self) -> None:
        """Validate invariant: success=True requires exit_code 0 or None."""
        if self.success and self.exit_code is not None and self.exit_code != 0:
            raise ValueError(
                f"Inconsistent ExecutionResult: success=True but exit_code={self.exit_code}. "
                "success=True should only be set when exit_code is 0 or None."
            )

    @property
    def output(self) -> str:
        """Combined stdout and stderr."""
        return f"{self.stdout}\n{self.stderr}".strip()


class Backend(ABC):
    """Abstract base class for Claude execution backends.

    Backends handle the actual execution of prompts through Claude,
    whether via CLI subprocess or direct API calls.
    """

    @abstractmethod
    async def execute(
        self, prompt: str, *, timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute a prompt and return the result.

        Args:
            prompt: The prompt to send to Claude
            timeout_seconds: Per-call timeout override. If provided, overrides
                the backend's default timeout for this single execution.

        Returns:
            ExecutionResult with output and metadata
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the backend is available and working.

        Used to verify connectivity before starting a job,
        and to check if rate limits have lifted.

        Returns:
            True if backend is ready, False otherwise
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name."""
        ...

    @property
    def working_directory(self) -> Path | None:
        """Working directory for backend execution.

        Subprocess-based backends (e.g. ClaudeCliBackend) use this as the cwd
        for child processes. API-based backends store it but don't use it directly.

        Returns None if no working directory is set, meaning the process CWD is used.

        Thread/Concurrency Safety:
            This property is NOT safe to mutate while executions are in-flight.
            The worktree isolation layer sets it *before* any sheet execution
            starts, and restores it in the finally block *after* all sheets
            complete. During parallel execution, all concurrent sheets share
            the same working directory (the worktree path), so the value is
            read-only while sheets are running. Never change this property
            from a concurrent task mid-execution.
        """
        return self._working_directory

    @working_directory.setter
    def working_directory(self, value: Path | None) -> None:
        """Set working directory for backend execution.

        Called by worktree isolation to override the working directory at runtime.
        Must only be called when no executions are in-flight (see property docstring).
        """
        self._working_directory = value

    async def close(self) -> None:  # noqa: B027
        """Close the backend and release resources.

        Override in subclasses that hold persistent connections or resources.
        Default implementation is a no-op for backends without cleanup needs.

        This method should be idempotent - calling it multiple times should be safe.
        """

    async def __aenter__(self) -> Backend:
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Async context manager exit — ensures close() is called."""
        await self.close()

    def _detect_rate_limit(
        self, stdout: str = "", stderr: str = "", exit_code: int | None = None,
    ) -> bool:
        """Check output for rate limit indicators.

        Uses the shared ErrorClassifier to ensure consistent detection
        across all backends.

        Args:
            stdout: Standard output text.
            stderr: Standard error text.
            exit_code: Process exit code. If None or 0, returns False.

        Returns:
            True if rate limiting was detected.
        """
        if exit_code is None or exit_code == 0:
            return False

        if not hasattr(self, "_error_classifier"):
            self._error_classifier = ErrorClassifier()

        # Pass output_format to avoid false E009 for text-mode exit code 1
        output_format = getattr(self, "output_format", None)
        classified = self._error_classifier.classify(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            output_format=output_format,
        )
        return classified.category == ErrorCategory.RATE_LIMIT

    @property
    def override_lock(self) -> asyncio.Lock:
        """Lock for serializing apply_overrides → execute → clear_overrides cycles.

        Parallel sheet execution must acquire this lock around the entire
        override lifecycle to prevent concurrent sheets from stomping on each
        other's saved originals.
        """
        if not hasattr(self, "_override_lock"):
            self._override_lock = asyncio.Lock()
        return self._override_lock

    def apply_overrides(self, overrides: dict[str, object]) -> None:  # noqa: B027
        """Apply per-sheet parameter overrides for the next execution.

        Called per-sheet by the runner when ``sheet_overrides`` is configured.
        Subclasses store the overrides and apply them in ``execute()``.
        ``clear_overrides()`` is called after execution to restore defaults.

        Callers MUST hold ``override_lock`` for the entire
        apply → execute → clear window when parallel execution is possible.

        Default implementation is a no-op for backends without override support.

        Args:
            overrides: Dict of parameter name → value. Only non-None values
                from ``SheetBackendOverride`` are included.
        """

    def clear_overrides(self) -> None:  # noqa: B027
        """Clear per-sheet parameter overrides, restoring defaults.

        Called after each sheet execution to ensure the next sheet uses
        global config. Default implementation is a no-op.
        """

    def set_preamble(self, _preamble: str | None) -> None:  # noqa: B027
        """Set the dynamic preamble for the next execution.

        Called per-sheet by the runner with a context-aware preamble built by
        ``build_preamble()``. The preamble includes sheet identity, position,
        workspace, and retry status.

        Override in subclasses that support prompt injection.
        Default implementation is a no-op for backends without this capability.

        Args:
            _preamble: Preamble text to prepend, or None to clear.
        """

    def set_prompt_extensions(self, _extensions: list[str]) -> None:  # noqa: B027
        """Set prompt extensions for the next execution.

        Extensions are additional directive blocks injected after the
        preamble. Called per-sheet by the runner to apply score-level and
        sheet-level prompt extensions (GH#76).

        Override in subclasses that support prompt injection.
        Default implementation is a no-op for backends without this capability.

        Args:
            _extensions: List of extension text blocks.
        """

    def set_output_log_path(self, _path: Path | None) -> None:  # noqa: B027
        """Set base path for real-time output logging.

        Called per-sheet by runner to enable streaming output to log files.
        This provides visibility into backend output during long executions.

        Uses industry-standard separate files for stdout and stderr:
        - {path}.stdout.log - standard output
        - {path}.stderr.log - standard error

        Override in subclasses that support real-time output streaming.
        Default implementation is a no-op for backends without this capability.

        Args:
            _path: Base path for log files (without extension), or None to disable.
        """


class HttpxClientMixin:
    """Shared lazy httpx.AsyncClient lifecycle for HTTP-based backends.

    Provides `_get_client()` for lazy initialization with connection pooling
    and `_close_httpx_client()` for cleanup. Both Ollama and RecursiveLight
    backends use this same pattern.
    """

    _client: httpx.AsyncClient | None
    _httpx_base_url: str
    _httpx_timeout: httpx.Timeout
    _httpx_headers: dict[str, str]

    def _init_httpx_mixin(
        self,
        base_url: str,
        timeout: float,
        *,
        connect_timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize mixin state. Call from subclass __init__."""
        import httpx as _httpx

        self._client = None
        self._client_lock: asyncio.Lock | None = None
        self._httpx_base_url = base_url
        self._httpx_timeout = _httpx.Timeout(timeout, connect=connect_timeout)
        self._httpx_headers = headers or {}

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with connection pooling.

        Uses an asyncio.Lock to prevent duplicate client creation when
        multiple coroutines call this concurrently.
        """
        import httpx as _httpx

        if self._client_lock is None:
            self._client_lock = asyncio.Lock()

        async with self._client_lock:
            if self._client is None or self._client.is_closed:
                self._client = _httpx.AsyncClient(
                    base_url=self._httpx_base_url,
                    timeout=self._httpx_timeout,
                    headers=self._httpx_headers if self._httpx_headers else None,
                )
            return self._client

    async def _close_httpx_client(self) -> None:
        """Close the HTTP client if open."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
