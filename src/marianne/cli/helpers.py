"""Shared utilities for Marianne CLI commands.

This module contains helpers used across multiple CLI command modules:
- Logger setup and configuration
- Backend creation helpers
- Config loading utilities
- Console/Rich setup
- State backend discovery
- Common utility functions
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn

import typer
from rich.console import Console

from marianne.core.checkpoint import CheckpointState
from marianne.core.constants import DAEMON_STATE_DB_PATH
from marianne.core.errors.codes import ErrorCode
from marianne.core.logging import configure_logging, get_logger
from marianne.daemon.clone import active_registry_db_path
from marianne.state import StateBackend

if TYPE_CHECKING:
    from datetime import datetime


_logger = get_logger("cli")


def live_conductor_pid() -> int | None:
    """The PID the active conductor's PID file names, if that process is alive.

    Clone-aware. This is the evidence that separates "busy" from "absent"
    (GH #455): a socket that does not answer while this PID is alive is a
    conductor blocked on disk, never one to start or restart. Reads only;
    never asserts or unlinks a PID file.
    """
    from marianne.daemon.clone import get_clone_name, resolve_clone_paths
    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.process import _pid_alive, _read_pid, _resolve_live_pid_file

    clone_name = get_clone_name()
    pid_file = (
        resolve_clone_paths(clone_name).pid_file if clone_name is not None
        else _resolve_live_pid_file(DaemonConfig().pid_file)
    )
    pid = _read_pid(pid_file)
    if pid is None or not _pid_alive(pid):
        return None
    return pid


def conductor_socket_present() -> bool:
    """Whether the socket the CLI would route through exists (clone-aware)."""
    from marianne.daemon.detect import _resolve_socket_path
    from marianne.daemon.ipc.client import _socket_exists

    try:
        return _socket_exists(_resolve_socket_path(None))
    except OSError:
        return False


BUSY_HINTS = [
    "Do not start or restart it: wait for host I/O pressure to clear "
    "(cat /proc/pressure/io) and retry a read-only command.",
    "Inspect it with: mzt conductor-status",
]


def busy_conductor_message(pid: int, detail: str = "") -> str:
    """The one sentence every CLI path uses for a live conductor that did not answer."""
    tail = f": {detail}" if detail else ""
    return (
        f"Marianne conductor busy or unresponsive (PID {pid} alive); "
        f"running state is unknown{tail}"
    )


def report_daemon_route_error(exc: Exception, *, json_output: bool) -> NoReturn:
    """Report an IPC outcome once, without changing its running-state meaning.

    An absent verdict needs two proofs: the connect was refused or the socket
    is missing, AND no live PID is held. With a live PID the same failure is
    reported busy (GH #455), and no path here ever suggests ``mzt start``,
    ``mzt restart`` or ``mzt stop --force`` for a conductor that may be alive.
    """
    from marianne.cli.output import output_error
    from marianne.daemon.exceptions import (
        DaemonAccessDeniedError,
        DaemonNotRunningError,
        DaemonProtocolError,
        DaemonUnresponsiveError,
        MethodNotFoundError,
    )

    pid = None
    if isinstance(exc, DaemonUnresponsiveError):
        pid = live_conductor_pid()
    elif isinstance(exc, DaemonNotRunningError) and conductor_socket_present():
        # A missing socket proves absence on its own; a refused connect on a
        # socket that still exists does so only without a live PID.
        pid = live_conductor_pid()
    if isinstance(exc, DaemonAccessDeniedError):
        message = str(exc)
        hints = ["Check whether your current user is permitted to access the conductor socket."]
        running_state = "unknown"
    elif isinstance(exc, DaemonUnresponsiveError) and pid is not None:
        message = busy_conductor_message(pid, str(exc))
        hints = list(BUSY_HINTS)
        running_state = "unknown"
    elif isinstance(exc, DaemonUnresponsiveError):
        message = f"Marianne conductor did not respond; running state is unknown: {exc}"
        hints = ["Check the conductor logs and retry a read-only status check."]
        running_state = "unknown"
    elif isinstance(exc, DaemonProtocolError):
        message = f"Marianne conductor returned an invalid response: {exc}"
        hints = ["Check the conductor logs and CLI protocol version."]
        running_state = "unknown"
    elif isinstance(exc, DaemonNotRunningError) and pid is not None:
        # Refused or vanished socket, but the PID file names a live process:
        # that is a conductor between bind and listen, or one blocked before
        # it could answer. Absence is not proven.
        message = busy_conductor_message(pid, "the socket did not accept a connection")
        hints = list(BUSY_HINTS)
        running_state = "unknown"
    elif isinstance(exc, DaemonNotRunningError):
        message = "Marianne conductor is not running."
        hints = ["Start it with: mzt start"]
        running_state = "absent"
    elif isinstance(exc, MethodNotFoundError):
        # The conductor answered and does not know the method: the CLI is
        # newer than the running conductor. This is the one conductor error
        # a restart actually fixes.
        message = str(exc)
        hints = ["Pause or finish active scores before restarting: mzt restart"]
        running_state = "unknown"
    else:
        # A DaemonError raised after the conductor answered its health probe
        # (connection lost mid-request, admission refused). Restarting fixes
        # none of these; the outcome of a write is unknown.
        message = str(exc)
        hints = ["Check conductor status: mzt conductor-status"]
        running_state = "unknown"

    if pid is not None and running_state == "unknown":
        output_error(
            message,
            hints=hints,
            json_output=json_output,
            error_type=type(exc).__name__,
            running_state=running_state,
            conductor_pid=pid,
        )
    else:
        output_error(
            message,
            hints=hints,
            json_output=json_output,
            error_type=type(exc).__name__,
            running_state=running_state,
        )
    raise typer.Exit(1) from None


def report_daemon_access_denied(exc: Exception, *, json_output: bool) -> NoReturn:
    """Preserve the established denial reporting entry point."""
    report_daemon_route_error(exc, json_output=json_output)


class ErrorMessages:
    """Constants for CLI error messages.

    Centralizing error messages ensures consistent user-facing output
    and simplifies potential localization.
    """

    JOB_NOT_FOUND = "Score not found"
    CONFIG_LOAD_ERROR = "Error loading config"
    WORKSPACE_NOT_FOUND = "Workspace not found"
    STATE_FILE_NOT_FOUND = "State file not found"


class OutputLevel(str, Enum):
    """Output verbosity level."""

    QUIET = "quiet"  # Minimal output (errors only)
    NORMAL = "normal"  # Default output
    VERBOSE = "verbose"  # Detailed output


# Global output level state
_output_level: OutputLevel = OutputLevel.NORMAL



def set_output_level(level: OutputLevel) -> None:
    """Set the output level.

    Args:
        level: The new output level.
    """
    global _output_level
    _output_level = level


def is_verbose() -> bool:
    """Check if verbose output is enabled."""
    return _output_level == OutputLevel.VERBOSE


def is_quiet() -> bool:
    """Check if quiet output is enabled."""
    return _output_level == OutputLevel.QUIET


@dataclass
class CliLoggingConfig:
    """Centralized CLI logging configuration state.

    Replaces 4 module-level variables with a single typed dataclass.
    All getter/setter functions below delegate to this instance.
    """

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "WARNING"
    file: Path | None = None
    format: Literal["json", "console", "both"] = "console"
    configured: bool = False


# Single global config instance
_log_config = CliLoggingConfig()


def set_log_level(level: str) -> None:
    """Set the log level.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
    """
    _log_config.level = level  # type: ignore[assignment]


def set_log_file(path: Path | None) -> None:
    """Set the log file path.

    When a log file is specified, logs are written to the file in
    console format. Rich CLI output (progress bars, status tables)
    is separate from structured logging and still displays on console.

    Args:
        path: Path for log file output, or None to disable file logging.
    """
    _log_config.file = path
    if path:
        _log_config.format = "console"


def set_log_format(fmt: str) -> None:
    """Set the log format.

    Args:
        fmt: Log format string (json, console, both).
    """
    _log_config.format = fmt  # type: ignore[assignment]


def configure_global_logging(console: Console) -> None:
    """Configure logging based on global CLI options.

    This is called after all callbacks have processed their options.
    Only configures once per session.

    Args:
        console: Rich console for error output.

    Raises:
        typer.Exit: If logging configuration fails.
    """
    if _log_config.configured:
        return

    try:
        configure_logging(
            level=_log_config.level,
            format=_log_config.format,
            file_path=_log_config.file,
        )
        _log_config.configured = True
        # Note: Intentionally not logging here to avoid polluting --json output
        # If debugging is needed, use --log-file to redirect logs
    except ValueError as e:
        # Handle configuration errors (e.g., format="both" without file_path)
        from .output import output_error

        output_error(
            f"Logging configuration error: {e}",
            hints=[
                "Check --log-format and --log-file options.",
                "format='both' requires --log-file to be set.",
            ],
        )
        raise typer.Exit(1) from None


# Default state directory when no config is available
DEFAULT_STATE_DIR = Path.home() / ".marianne" / "state"


# Re-exported from notifications module for backwards compatibility.
# The canonical implementation lives in marianne.notifications.factory.
from marianne.notifications.factory import (
    create_notifiers_from_config as create_notifiers_from_config,  # noqa: F401, E501
)


async def _try_registry_state(
    job_id: str,
) -> tuple[CheckpointState, StateBackend] | None:
    """Read job state from the authoritative daemon DB, registry-first (#111).

    Returns ``(state, backend)`` with an OPEN registry-backed ``StateBackend``
    (the caller closes it) when the daemon DB exists and lists this job — so
    offline reads see the CURRENT state. Returns ``None`` (registry closed)
    when there is no daemon DB or the job isn't in it. #50: there is no
    workspace fallback anywhere — the registry is the ONLY state source;
    a job absent here does not exist as far as state is concerned.
    """
    db_path = _offline_registry_db_path()
    if not db_path.exists():
        return None

    from marianne.daemon.registry import JobRegistry
    from marianne.daemon.registry_backend import RegistryStateBackend

    registry = JobRegistry(db_path)
    try:
        await registry.open()
        backend = RegistryStateBackend(registry)
        state = await backend.load(job_id)
        if state is not None:
            return state, backend  # caller closes backend → closes registry
        await registry.close()
        return None
    except Exception:  # noqa: BLE001 — structured logger reports find_job_state.registry_close_failed
        try:
            await registry.close()
        except Exception:  # noqa: BLE001 — structured logger reports find_job_state.registry_close_failed
            _logger.debug("find_job_state.registry_close_failed", job_id=job_id)
        _logger.warning(
            "find_job_state.registry_read_failed", job_id=job_id, exc_info=True
        )
        return None


def _offline_registry_db_path() -> Path:
    """Registry DB for conductor-down reads: the active clone's (GH #401),
    else this module's ``DAEMON_STATE_DB_PATH`` (a patch seam for tests)."""
    from marianne.daemon.clone import is_clone_active

    if is_clone_active():
        return active_registry_db_path()
    return DAEMON_STATE_DB_PATH.expanduser()


async def _find_job_state_fs(
    job_id: str,
    workspace: Path | None,
) -> tuple[CheckpointState | None, StateBackend | None]:
    """Find job state in the authoritative daemon DB — conductor-ONLY (#50).

    Private helper used when the conductor is unavailable: reads the daemon
    registry DB directly. There is NO workspace fallback — per-workspace
    state files were removed as a source of truth (composer ruling:
    "workspaces are for work to occur only, not for state tracking").
    A job absent from the registry does not exist as far as state is
    concerned; the caller surfaces a conductor-oriented error.

    The ``workspace`` parameter is retained for call-signature stability
    but no longer consulted for state reads.
    """
    del workspace  # #50: no workspace state reads, ever
    registry_result = await _try_registry_state(job_id)
    if registry_result is not None:
        return registry_result
    return None, None


async def _find_job_state_direct(
    job_id: str,
    workspace: Path | None,
    *,
    json_output: bool = False,
) -> tuple[CheckpointState, StateBackend]:
    """Find job state or exit with error (filesystem fallback).

    Private helper combining find + error handling, used by CLI
    command fallback paths when conductor is unavailable.

    Raises:
        typer.Exit(1): If workspace doesn't exist or job not found.
    """
    from .output import output_error

    if workspace and not workspace.exists():
        output_error(
            f"{ErrorMessages.WORKSPACE_NOT_FOUND}: {workspace}",
            error_code=ErrorCode.BACKEND_CONNECTION.value,
            hints=["Check the workspace path exists"],
            json_output=json_output,
        )
        raise typer.Exit(1)

    found_state, found_backend = await _find_job_state_fs(job_id, workspace)

    if found_state is None or found_backend is None:
        output_error(
            f"{ErrorMessages.JOB_NOT_FOUND}: {job_id}",
            error_code=ErrorCode.BACKEND_CONNECTION.value,
            hints=[
                "Use --workspace to specify the directory containing the score state",
                "Run 'mzt list' to see available scores",
            ],
            json_output=json_output,
            job_id=job_id,
        )
        raise typer.Exit(1)

    return found_state, found_backend


def require_conductor(
    routed: bool,
    *,
    json_output: bool = False,
) -> None:
    """Exit with a clear error when the conductor is not running.

    Used by CLI commands that route through the conductor IPC. If the
    conductor was not reachable, this prints "not running" with the hint
    "Start it with: mzt start" only when no live PID is held; a live PID
    makes the same outcome "busy or unresponsive" (GH #455).

    Args:
        routed: Whether ``try_daemon_route`` succeeded.
        json_output: If True, output error as JSON instead of Rich markup.

    Raises:
        typer.Exit(1): If the conductor is not running.
    """
    if routed:
        return

    from marianne.daemon.exceptions import DaemonNotRunningError

    report_daemon_route_error(DaemonNotRunningError(), json_output=json_output)


async def await_early_failure(
    job_id: str,
    *,
    timeout: float = 1.5,
    poll_interval: float = 0.2,
) -> dict[str, Any] | None:
    """Poll job status briefly to detect early failures after submission.

    After a job is submitted and accepted, template rendering errors or
    other immediate failures happen within milliseconds.  This function
    polls the daemon for up to ``timeout`` seconds so the CLI can report
    the failure inline instead of printing a cheerful "Job queued".

    Fail-open: any exception returns ``None`` so this never blocks the CLI.
    """
    client = None
    try:
        from marianne.daemon.detect import _resolve_socket_path
        from marianne.daemon.ipc.client import DaemonClient

        socket_path = _resolve_socket_path(None)
        client = DaemonClient(socket_path)

        _terminal_states = {"failed", "cancelled"}
        _active_states = {"running", "queued"}

        # The deadline includes each RPC, not just sleeps between polls.
        async with asyncio.timeout(timeout):
            while True:
                await asyncio.sleep(poll_interval)
                result = await client.call("job.status", {"job_id": job_id})
                if not isinstance(result, dict):
                    continue

                status = result.get("status", "")
                if status in _terminal_states or status == "completed":
                    return result
                if status in _active_states:
                    continue

        return None
    except Exception:  # noqa: BLE001 — submission must survive failure of the advisory status probe
        _logger.debug("cli.early_failure_probe_failed", exc_info=True)
        return None
    finally:
        if client is not None:
            await client.close()


async def query_rate_limits() -> dict[str, dict[str, float]] | None:
    """Query the conductor for active rate limit information.

    Returns the ``backends`` dict from the ``daemon.rate_limits`` IPC
    response, e.g. ``{"claude-cli": {"seconds_remaining": 120.0}}``.

    Returns ``None`` if the conductor is unreachable or an error occurs.
    Fail-open: this never raises — callers can safely display extra info
    when available and skip it when not.
    """
    try:
        from marianne.daemon.detect import try_daemon_route

        routed, result = await try_daemon_route("daemon.rate_limits", {})
        if not routed or not isinstance(result, dict):
            return None
        backends = result.get("backends")
        if isinstance(backends, dict):
            return backends
        return None
    except Exception:  # noqa: BLE001 — optional rate limit display must not block the CLI
        _logger.debug("cli.rate_limits_unavailable", exc_info=True)
        return None


def check_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is running.

    Uses ``os.kill(pid, 0)`` — signal 0 checks existence without sending
    a signal.

    Returns:
        ``True`` if the process exists (even if owned by another user).
        ``False`` if the process does not exist.
    """
    import os

    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # Process exists but owned by another user
        return True
    except OSError:
        # Process does not exist
        return False


def get_last_activity_time(job: CheckpointState) -> datetime | None:
    """Get the most recent activity timestamp from the job.

    Checks sheet last_activity_at fields and updated_at.

    Args:
        job: CheckpointState to check.

    Returns:
        datetime of last activity, or None if not available.
    """
    candidates = [
        ts for ts in (
            job.updated_at,
            *(sheet.last_activity_at for sheet in job.sheets.values()),
        )
        if ts is not None
    ]
    return max(candidates) if candidates else None


__all__ = [
    # Error messages
    "ErrorMessages",
    # Output level
    "OutputLevel",
    "set_output_level",
    "is_verbose",
    "is_quiet",
    # Logging
    "set_log_level",
    "set_log_file",
    "set_log_format",
    "configure_global_logging",
    # Paths
    "DEFAULT_STATE_DIR",
    # Backend creation
    "create_notifiers_from_config",
    # Conductor helpers
    "require_conductor",
    # Early failure detection
    "await_early_failure",
    # Utilities
    "get_last_activity_time",
]
