"""Conductor clone support — isolated conductor instances for safe testing.

When --conductor-clone is passed to any Mozart CLI command, all daemon
interactions are routed to a clone conductor instead of the production
one. The clone has its own socket, PID file, state DB, and log file.

This enables safe testing of Mozart CLI commands and daemon features
without risking the production conductor. The production conductor
continues running undisturbed.

Usage:
    mozart start --conductor-clone              # Start default clone
    mozart start --conductor-clone=staging      # Start named clone
    mozart run score.yaml --conductor-clone     # Submit to default clone
    mozart status --conductor-clone=staging     # Query named clone

Architecture:
    - A module-level _clone_name stores the active clone (set by CLI callback)
    - _resolve_socket_path() in detect.py checks get_clone_name() before
      falling back to SocketConfig defaults
    - resolve_clone_paths() computes all isolated paths from a clone name
    - build_clone_config() produces a DaemonConfig with clone-specific paths
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mozart.core.logging import get_logger

if TYPE_CHECKING:
    from mozart.daemon.config import DaemonConfig

_logger = get_logger("daemon.clone")

# Module-level clone name — set by the CLI --conductor-clone callback.
# None means no clone (production mode). Empty string means default clone.
_clone_name: str | None = None


def set_clone_name(name: str | None) -> None:
    """Set the active clone name. Called by CLI --conductor-clone callback."""
    global _clone_name
    _clone_name = name


def get_clone_name() -> str | None:
    """Get the active clone name. None = production mode."""
    return _clone_name


def is_clone_active() -> bool:
    """Check if a clone is currently active."""
    return _clone_name is not None


def _sanitize_name(name: str | None) -> str:
    """Sanitize a clone name for use in file paths.

    Replaces non-alphanumeric characters (except hyphens) with hyphens.
    Empty/None produces empty string (default clone with no suffix).
    Truncates to 64 chars to stay within Unix socket path limits (~108).
    """
    if not name:
        return ""
    # Replace spaces, slashes, and other unsafe chars with hyphens.
    # Underscores are filesystem-safe and kept to preserve name uniqueness.
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    # Collapse multiple hyphens
    sanitized = re.sub(r"-+", "-", sanitized)
    # Do NOT strip leading/trailing hyphens — stripping is cosmetic but
    # makes the function lossy, causing path collisions between distinct
    # names (e.g., '0' and '_0' both sanitize to '0'). Hyphens in the
    # middle of path components like /tmp/mozart-clone--test.sock are safe.
    # Truncate to stay within Unix socket path limits (~108 chars).
    # /tmp/mozart-clone-{name}.sock = 21 + len(name) + 5 = 26 + len(name)
    # Cap at 64 chars to leave headroom.
    if len(sanitized) > 64:
        sanitized = sanitized[:64]
    return sanitized


@dataclass(frozen=True)
class ClonePaths:
    """Isolated paths for a conductor clone instance."""

    socket: Path
    pid_file: Path
    state_db: Path
    log_file: Path


def resolve_clone_paths(name: str | None) -> ClonePaths:
    """Compute isolated paths for a clone instance.

    Args:
        name: Clone name (None or empty for default clone, string for named clone).

    Returns:
        ClonePaths with socket, PID, state DB, and log paths.
        All paths are in /tmp for socket/PID (matching production convention)
        and ~/.mozart for state DB/log.
    """
    suffix = _sanitize_name(name)
    tag = f"-{suffix}" if suffix else ""

    mozart_dir = Path.home() / ".mozart"

    return ClonePaths(
        socket=Path(f"/tmp/mozart-clone{tag}.sock"),
        pid_file=Path(f"/tmp/mozart-clone{tag}.pid"),
        state_db=mozart_dir / f"clone{tag}-state.db",
        log_file=Path(f"/tmp/mozart-clone{tag}.log"),
    )


def build_clone_config(
    name: str | None,
    *,
    base_config: DaemonConfig | None = None,
) -> DaemonConfig:
    """Build a DaemonConfig with clone-specific paths.

    Inherits all non-path settings from base_config (or defaults).
    Overrides socket, PID file, and log paths with clone-specific values.

    Args:
        name: Clone name (None for default clone).
        base_config: Production DaemonConfig to inherit from.

    Returns:
        A DaemonConfig with isolated clone paths.
    """
    # Deferred import to avoid circular dependency
    from mozart.daemon.config import DaemonConfig, SocketConfig

    paths = resolve_clone_paths(name)

    if base_config is not None:
        # Clone from existing config — inherit non-path fields
        config_dict = base_config.model_dump()
        config_dict["socket"] = {"path": str(paths.socket)}
        config_dict["pid_file"] = str(paths.pid_file)
        config_dict["state_db_path"] = str(paths.state_db)
        return DaemonConfig.model_validate(config_dict)

    # Build from defaults with clone paths — all isolation fields must be set.
    # Missing state_db_path here caused F-132 (clone opened production DB).
    return DaemonConfig(
        socket=SocketConfig(path=paths.socket),
        pid_file=paths.pid_file,
        state_db_path=paths.state_db,
        log_file=paths.log_file,
    )
