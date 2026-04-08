"""Conductor detection and CLI routing — safe fallback to direct execution.

This module is used by CLI commands to auto-detect a running Marianne
conductor and route operations through it. When no conductor is detected,
the caller falls back to direct execution (existing behavior).

SAFETY: Every public function catches ALL exceptions and returns a
"not routed" result. This ensures that conductor bugs never break the CLI.
The CLI wiring wraps calls in try/except as a second layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from marianne.core.logging import get_logger

_logger = get_logger("daemon.detect")


def _resolve_socket_path(socket_path: Path | None) -> Path:
    """Resolve socket path, falling back to clone path or SocketConfig default.

    Resolution order:
    1. Explicit socket_path parameter (always wins)
    2. Clone socket path (if --conductor-clone is active)
    3. SocketConfig default (production path)
    """
    if socket_path is not None:
        return socket_path

    # Check if a clone is active
    from marianne.daemon.clone import get_clone_name, resolve_clone_paths

    clone_name = get_clone_name()
    if clone_name is not None:
        return resolve_clone_paths(clone_name).socket

    from marianne.daemon.config import SocketConfig

    return SocketConfig().path


async def is_daemon_available(socket_path: Path | None = None) -> bool:
    """Check if the Marianne conductor is running. Safe: returns False on any error."""
    resolved = _resolve_socket_path(socket_path)
    try:
        from marianne.daemon.ipc.client import DaemonClient

        client = DaemonClient(resolved)
        return await client.is_daemon_running()
    except (OSError, ConnectionError) as e:
        # Connection/socket errors — conductor not reachable.
        level = "info" if resolved.exists() else "debug"
        getattr(_logger, level)("daemon_detection_failed", error=str(e))
        return False
    except ImportError:
        _logger.debug("daemon_detection_import_error")
        return False
    except Exception as e:
        # Check if this is a known DaemonError (guard against missing module)
        is_daemon_error = False
        try:
            from marianne.daemon.exceptions import DaemonError
            is_daemon_error = isinstance(e, DaemonError)
        except ImportError:
            pass

        if is_daemon_error:
            _logger.debug("daemon_detection_failed", error=str(e))
        else:
            _logger.warning("daemon_detection_unexpected", error=str(e), exc_info=True)
        return False


async def try_daemon_route(
    method: str,
    params: dict[str, Any],
    *,
    socket_path: Path | None = None,
) -> tuple[bool, Any]:
    """Try routing a CLI command through the conductor.

    Returns:
        (True, result) if conductor handled the request.
        (False, None) if conductor is not running or a connection error occurred.

    Raises:
        JobSubmissionError, ResourceExhaustedError: Business logic errors from
            a running conductor are re-raised so callers can handle them
            (e.g., "job not found" is different from "daemon not running").

    Connection-level errors never raise — they return (False, None).
    Response-level timeouts (daemon confirmed running but slow to respond)
    raise ``DaemonError`` so callers can show an accurate message instead
    of the misleading "conductor not running."
    """
    resolved = _resolve_socket_path(socket_path)
    # Track whether the daemon was confirmed alive so we can distinguish
    # "daemon not reachable" from "daemon running but slow" on timeout.
    daemon_confirmed_running = False
    try:
        from marianne.daemon.ipc.client import DaemonClient

        client = DaemonClient(resolved)
        if not await client.is_daemon_running():
            return False, None
        daemon_confirmed_running = True
        result = await client.call(method, params)
        return True, result
    except TimeoutError:
        if daemon_confirmed_running:
            # Daemon IS running but didn't respond in time — raise so
            # callers show "conductor busy" instead of "not running".
            from marianne.daemon.exceptions import DaemonError

            raise DaemonError(
                f"Conductor is running but did not respond to '{method}' "
                f"in time. The conductor may be busy with a long operation."
            ) from None
        # Timeout during is_daemon_running() itself — genuinely unreachable.
        _logger.debug("daemon_route_failed", method=method, error="connection timeout")
        return False, None
    except (OSError, ConnectionError) as e:
        _logger.debug("daemon_route_failed", method=method, error=str(e))
        return False, None
    except json.JSONDecodeError as e:
        # Malformed JSON from a running daemon — genuine protocol error.
        # Logged at WARNING because the daemon IS running but
        # misbehaving — operators should notice.
        _logger.warning(
            "daemon_route_protocol_error", method=method, error=str(e),
        )
        return False, None
    except ValueError as e:
        # ValueErrors from readline() indicate response exceeded the
        # StreamReader buffer limit (e.g. large CheckpointState payload).
        # The daemon IS running — re-raise as DaemonError so callers
        # don't fall through to "conductor not running" messaging.
        error_msg = str(e)
        is_limit_error = "chunk exceed the limit" in error_msg
        _logger.warning(
            "daemon_route_protocol_error",
            method=method,
            error=error_msg,
            error_type="ValueError",
            is_limit_error=is_limit_error,
        )
        if is_limit_error:
            from marianne.daemon.exceptions import DaemonError

            raise DaemonError(
                f"Response too large for '{method}' — the job's checkpoint "
                f"exceeds the IPC buffer limit"
            ) from e
        return False, {"error": error_msg, "error_type": type(e).__name__}
    except Exception as e:
        from marianne.daemon.exceptions import JobSubmissionError, ResourceExhaustedError

        if isinstance(e, (JobSubmissionError, ResourceExhaustedError)):
            # Business logic errors from a *running* conductor — re-raise so
            # callers can distinguish "daemon unavailable" from "daemon rejected
            # the request" (e.g., "job not found" vs "conductor not running").
            raise

        from marianne.daemon.exceptions import MethodNotFoundError

        if isinstance(e, MethodNotFoundError):
            # F-450: The conductor IS running but doesn't recognize this
            # method. Re-raise with restart guidance instead of the
            # misleading "conductor not running."
            raise MethodNotFoundError(
                f"Conductor does not support '{method}'. "
                f"Restart the conductor to pick up code changes: "
                f"mzt restart"
            ) from e

        from marianne.daemon.exceptions import DaemonError

        if isinstance(e, DaemonError):
            # All other daemon errors (not running, already running, unknown
            # method, protocol errors) — treat as "daemon not reachable".
            _logger.debug("daemon_route_failed", method=method, error=str(e))
            return False, None

        _logger.warning(
            "daemon_route_unexpected_error",
            method=method,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        return False, None
