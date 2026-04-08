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
from typing import TYPE_CHECKING, Any, Literal

import typer
from rich.console import Console

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.logging import configure_logging, get_logger
from marianne.state import JsonStateBackend, SQLiteStateBackend, StateBackend

if TYPE_CHECKING:
    from datetime import datetime


_logger = get_logger("cli")


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


def _find_job_workspace(job_id: str, hint: Path | None = None) -> Path | None:
    """Find workspace containing a job's state file.

    Private fallback used when conductor is unavailable and --workspace
    is explicitly provided.
    """
    search_paths: list[Path] = []

    if hint:
        search_paths.append(hint)

    cwd = Path.cwd()
    search_paths.append(cwd)
    search_paths.append(cwd / "workspace")
    search_paths.append(cwd / f"{job_id}-workspace")

    for path in search_paths:
        if not path.exists():
            continue

        json_state = path / f"{job_id}.json"
        if json_state.exists():
            return path

        sqlite_state = path / ".marianne-state.db"
        if sqlite_state.exists():
            return path

    return None


async def _close_backends(
    backends: list[StateBackend],
    *,
    keep: StateBackend | None = None,
) -> None:
    """Close backends, optionally keeping one open."""
    for backend in backends:
        if backend is not keep:
            await backend.close()


async def _find_job_state_fs(
    job_id: str,
    workspace: Path | None,
) -> tuple[CheckpointState | None, StateBackend | None]:
    """Find job state in available backends (filesystem fallback).

    Private helper used when conductor is unavailable.
    """
    backends: list[StateBackend] = []

    if workspace:
        if not workspace.exists():
            return None, None
        sqlite_path = workspace / ".marianne-state.db"
        if sqlite_path.exists():
            backends.append(SQLiteStateBackend(sqlite_path))
        backends.append(JsonStateBackend(workspace))
    else:
        cwd = Path.cwd()
        backends.append(JsonStateBackend(cwd))
        sqlite_cwd = cwd / ".marianne-state.db"
        if sqlite_cwd.exists():
            backends.append(SQLiteStateBackend(sqlite_cwd))

    for backend in backends:
        try:
            job = await backend.load(job_id)
            if job:
                await _close_backends(backends, keep=backend)
                return job, backend
        except (OSError, ValueError, KeyError) as e:
            _logger.warning(
                "error_querying_backend",
                job_id=job_id,
                backend=type(backend).__name__,
                error=str(e),
                exc_info=True,
            )
            continue
        except Exception as e:
            _logger.error(
                "unexpected_error_querying_backend",
                job_id=job_id,
                backend=type(backend).__name__,
                error=str(e),
                exc_info=True,
            )
            continue

    await _close_backends(backends)
    return None, None


async def _find_job_state_from_registry(
    job_id: str,
) -> CheckpointState | None:
    """Read job state from the daemon registry DB directly.

    Used when the conductor is down but the registry DB exists.
    The registry DB is always up to date (Phase 2 persist callback
    writes after every state-dirty event). This replaces workspace
    file fallback for baton-managed jobs.

    Returns:
        CheckpointState if found in registry, None otherwise.
    """
    import json

    db_path = Path("~/.marianne/daemon-state.db").expanduser()
    if not db_path.exists():
        return None

    try:
        from marianne.daemon.registry import JobRegistry

        async with JobRegistry(db_path) as registry:
            checkpoint_json = await registry.load_checkpoint(job_id)
            if checkpoint_json is None:
                return None
            data = json.loads(checkpoint_json)
            return CheckpointState.model_validate(data)
    except Exception:
        return None


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
            error_code="E501",
            hints=["Check the workspace path exists"],
            json_output=json_output,
        )
        raise typer.Exit(1)

    found_state, found_backend = await _find_job_state_fs(job_id, workspace)

    if found_state is None or found_backend is None:
        output_error(
            f"{ErrorMessages.JOB_NOT_FOUND}: {job_id}",
            error_code="E501",
            hints=[
                "Use --workspace to specify the directory containing the score state",
                "Run 'mzt list' to see available scores",
            ],
            json_output=json_output,
            job_id=job_id,
        )
        raise typer.Exit(1)

    return found_state, found_backend


def _create_pause_signal(workspace: Path, job_id: str) -> Path:
    """Create pause signal file for a job (filesystem fallback)."""
    signal_file = workspace / f".marianne-pause-{job_id}"
    signal_file.touch()
    return signal_file


async def _wait_for_pause_ack(
    state_backend: StateBackend,
    job_id: str,
    timeout: int,
) -> bool:
    """Wait for job to acknowledge pause signal (filesystem fallback)."""
    start_time = asyncio.get_event_loop().time()
    poll_interval = 1.0

    while True:
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed >= timeout:
            return False

        try:
            state = await state_backend.load(job_id)
            if state and state.status == JobStatus.PAUSED:
                return True
            if state and state.status not in {JobStatus.RUNNING, JobStatus.PAUSED}:
                return True
        except Exception:
            _logger.warning("Error polling pause state", exc_info=True)

        await asyncio.sleep(poll_interval)


def require_conductor(
    routed: bool,
    *,
    json_output: bool = False,
) -> None:
    """Exit with a clear error when the conductor is not running.

    Used by CLI commands that route through the conductor IPC. If the
    conductor was not reachable, this prints a helpful message directing
    the user to ``mzt start``.

    Args:
        routed: Whether ``try_daemon_route`` succeeded.
        json_output: If True, output error as JSON instead of Rich markup.

    Raises:
        typer.Exit(1): If the conductor is not running.
    """
    if routed:
        return

    from .output import output_error

    output_error(
        "Marianne conductor is not running.",
        hints=["Start it with: mzt start"],
        json_output=json_output,
    )
    raise typer.Exit(1)


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
    try:
        from marianne.daemon.detect import _resolve_socket_path
        from marianne.daemon.ipc.client import DaemonClient

        socket_path = _resolve_socket_path(None)
        client = DaemonClient(socket_path)

        _terminal_states = {"failed", "cancelled"}
        _active_states = {"running", "queued"}

        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            result = await client.call("job.status", {"job_id": job_id})
            if not isinstance(result, dict):
                continue

            status = result.get("status", "")
            if status in _terminal_states:
                return result
            if status == "completed":
                return result
            if status in _active_states:
                continue

        return None
    except Exception:
        return None


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
    except Exception:
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
