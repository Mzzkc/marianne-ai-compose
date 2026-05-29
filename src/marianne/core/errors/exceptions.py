"""Exception classes for Marianne execution errors.

Contains the exception hierarchy used across CLI, daemon, and execution layers
for non-recoverable errors, rate limit exhaustion, and graceful shutdown.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class FatalError(Exception):
    """Non-recoverable error that should stop the job."""

    pass


class RateLimitExhaustedError(FatalError):
    """Rate limit or quota exhaustion — job should PAUSE, not FAIL.

    Subclasses FatalError for backward compatibility: existing
    ``except FatalError`` blocks still catch it, but more specific
    ``except RateLimitExhaustedError`` blocks intercept first when
    ordered before ``except FatalError``.

    Attributes:
        resume_after: When the rate limit resets (ISO datetime), or None.
        backend_type: Which backend hit the limit (e.g., "claude-cli").
        quota_exhaustion: True if daily/monthly quota is exhausted,
            False if it's a per-minute rate limit.
    """

    def __init__(
        self,
        message: str,
        resume_after: datetime | Any | None = None,
        backend_type: str = "unknown",
        quota_exhaustion: bool = False,
    ) -> None:
        super().__init__(message)
        self.resume_after = resume_after
        self.backend_type = backend_type
        self.quota_exhaustion = quota_exhaustion


class GracefulShutdownError(Exception):
    """Raised when Ctrl+C is pressed to trigger graceful shutdown.

    This exception is caught by the baton to save state before exiting.
    """

    pass
