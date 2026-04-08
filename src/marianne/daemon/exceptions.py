"""Exception hierarchy for the Marianne daemon.

All daemon-specific exceptions inherit from DaemonError, enabling callers
to catch broad (DaemonError) or narrow (e.g., ResourceExhaustedError).
Follows the same flat hierarchy pattern as other Marianne modules
(WorktreeError, ParallelExecutionError, etc.).
"""

from __future__ import annotations


class DaemonError(Exception):
    """Base exception for all daemon-related errors."""


class DaemonNotRunningError(DaemonError):
    """Raised when a client tries to communicate with a daemon that isn't running.

    Typically detected via missing PID file or failed socket connection.
    """


class DaemonAlreadyRunningError(DaemonError):
    """Raised when starting a daemon while another instance is already running.

    Detected via PID file check and process existence verification.
    """


class JobSubmissionError(DaemonError):
    """Raised when a job submission fails validation or cannot be queued.

    Examples: invalid config path, unparseable YAML, workspace conflicts.
    """


class MethodNotFoundError(DaemonError):
    """Raised when the conductor doesn't recognize an IPC method.

    This typically means the CLI is newer than the running conductor.
    The user should restart the conductor to pick up code changes:
    ``pip install -e ".[dev]" && mzt restart``
    """


class ResourceExhaustedError(DaemonError):
    """Raised when daemon resource limits are exceeded.

    Examples: max_concurrent_jobs reached, memory limit exceeded,
    API rate limit hit at the daemon level.
    """
