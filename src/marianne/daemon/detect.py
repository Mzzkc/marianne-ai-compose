"""Conductor detection and CLI routing — safe fallback to direct execution.

This module is used by CLI commands to auto-detect a running Marianne
conductor and route operations through it. When no conductor is detected,
the caller falls back to direct execution (existing behavior).

Missing or refused endpoints return a "not routed" result. A conductor that
accepts connections but cannot answer is an error, not permission to execute
an operation again through a direct fallback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from marianne.core.logging import get_logger
from marianne.daemon.config import LEGACY_SOCKET_PATH
from marianne.daemon.exceptions import DaemonError, DaemonUnresponsiveError

_logger = get_logger("daemon.detect")


def _default_socket_path() -> Path:
    """The production socket default (separate fn so tests can patch it)."""
    from marianne.daemon.config import SocketConfig

    return SocketConfig().path


def _resolve_socket_path(socket_path: Path | None) -> Path:
    """Resolve socket path, falling back to clone path or SocketConfig default.

    Resolution order:
    1. Explicit socket_path parameter (always wins)
    2. Clone socket path (if --conductor-clone is active)
    3. SocketConfig default (production path)
    4. #227 transitional: if the new default doesn't exist but a conductor
       started before the /tmp → ~/.config/mzt move is still serving on the
       legacy path, use the legacy path. Self-eliminating — once the
       conductor restarts on the new path the legacy socket is gone.
    """
    if socket_path is not None:
        return socket_path

    # Check if a clone is active
    from marianne.daemon.clone import get_clone_name, resolve_clone_paths

    clone_name = get_clone_name()
    if clone_name is not None:
        return resolve_clone_paths(clone_name).socket

    default = _default_socket_path()
    if not default.exists() and LEGACY_SOCKET_PATH.exists():
        return LEGACY_SOCKET_PATH
    return default


async def is_daemon_available(socket_path: Path | None = None) -> bool:
    """Return False for missing/refused IPC; preserve an unresponsive endpoint."""
    resolved = _resolve_socket_path(socket_path)
    client = None
    try:
        from marianne.daemon.ipc.client import DaemonClient

        client = DaemonClient(resolved)
        return await client.is_daemon_running()
    except TimeoutError as exc:
        raise DaemonUnresponsiveError("Conductor did not respond to the health probe") from exc
    except DaemonError:
        raise
    except (OSError, ConnectionError) as e:
        # Connection/socket errors — conductor not reachable.
        level = "info" if resolved.exists() else "debug"
        getattr(_logger, level)("daemon_detection_failed", error=str(e))
        return False
    except ImportError:
        _logger.debug("daemon_detection_import_error")
        return False
    except Exception as exc:
        _logger.warning("daemon_detection_unexpected", error=str(exc), exc_info=True)
        return False
    finally:
        if client is not None:
            await client.close()


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

    Missing/refused endpoints return (False, None). Unresponsive endpoints,
    protocol errors and failures after a healthy probe raise ``DaemonError``.
    An uncertain operation must never fall back to direct execution.
    """
    from marianne.daemon.exceptions import (
        DaemonError,
        DaemonNotRunningError,
        DaemonProtocolError,
        DaemonUnresponsiveError,
        MethodNotFoundError,
    )
    from marianne.daemon.ipc.client import _SAFE_RETRY_METHODS, DaemonClient

    resolved = _resolve_socket_path(socket_path)
    client = None
    daemon_confirmed_running = False
    try:
        client = DaemonClient(resolved)
        if not await client.is_daemon_running():
            return False, None
        daemon_confirmed_running = True
        result = (
            await client.health() if method == "daemon.health"
            else await client.call(method, params)
        )
        return True, result
    except MethodNotFoundError as exc:
        raise MethodNotFoundError(
            f"Conductor does not support '{method}'. "
            "Restart the conductor to pick up code changes: mzt restart"
        ) from exc
    except TimeoutError as exc:
        raise DaemonUnresponsiveError(
            f"Conductor did not respond to '{method}' in time. "
            + ("The operation outcome is unknown; do not blindly repeat it."
               if method not in _SAFE_RETRY_METHODS else "The conductor may be busy.")
        ) from exc
    except (OSError, DaemonNotRunningError) as exc:
        if not daemon_confirmed_running:
            _logger.debug("daemon_route_failed", method=method, error=str(exc))
            return False, None
        raise DaemonError(
            f"Conductor connection failed during '{method}': {exc}. "
            + ("The operation outcome is unknown; do not blindly repeat it."
               if method not in _SAFE_RETRY_METHODS else "The requested data is unavailable.")
        ) from exc
    except DaemonError:
        # Protocol, admission, unresponsive probe and application errors remain
        # errors. In particular, none authorizes a direct-execution fallback.
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        if "chunk exceed the limit" in message:
            raise DaemonProtocolError(
                f"Response too large for '{method}' — the job's checkpoint "
                "exceeds the IPC buffer limit"
            ) from exc
        raise DaemonProtocolError(f"Invalid conductor response for '{method}': {message}") from exc
    except Exception as exc:
        if daemon_confirmed_running:
            raise DaemonError(f"Conductor request '{method}' failed: {exc}") from exc
        _logger.warning("daemon_route_unexpected_error", method=method, error=str(exc))
        return False, None
    finally:
        if client is not None:
            await client.close()
