"""Shared execution setup — pure functions with no UI dependencies.

Consolidates the component creation logic that was duplicated between
``cli/commands/_shared.py`` and ``daemon/job_service.py``.  Both paths
now call these functions:

- **CLI** wraps them with Rich console output for verbosity.
- **Daemon** calls them directly (no console).

This eliminates the "mirrors _shared.py" comments in job_service.py and
ensures that adding a new backend type only requires updating one place.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marianne.backends.base import Backend
    from marianne.core.config import BackendConfig, JobConfig
    from marianne.learning.global_store import GlobalLearningStore
    from marianne.learning.outcomes import OutcomeStore
    from marianne.notifications.base import NotificationManager
    from marianne.state.base import StateBackend


def create_backend_from_config(backend_config: BackendConfig) -> Backend:
    """Create the appropriate execution backend from a BackendConfig.

    Supports: claude_cli, anthropic_api, ollama. (Phase 4a: recursive_light
    removed — use the instrument: path with an HTTP profile.)

    Args:
        backend_config: Backend configuration with type and settings.

    Returns:
        Configured Backend instance.
    """
    from marianne.backends.anthropic_api import AnthropicApiBackend
    from marianne.backends.claude_cli import ClaudeCliBackend
    from marianne.backends.ollama import OllamaBackend

    if backend_config.type == "recursive_light":
        raise ValueError(
            "Backend type 'recursive_light' was removed in Phase 4 of the "
            "backend atlas migration. The native RecursiveLightBackend has "
            "been deleted. Migrate to the 'instrument:' path with a "
            "registered HTTP instrument profile."
        )
    elif backend_config.type == "anthropic_api":
        return AnthropicApiBackend.from_config(backend_config)
    elif backend_config.type == "ollama":
        return OllamaBackend.from_config(backend_config)
    else:
        return ClaudeCliBackend.from_config(backend_config)


def create_backend(config: JobConfig) -> Backend:
    """Create the appropriate execution backend from job config.

    Convenience wrapper around ``create_backend_from_config`` that
    extracts the backend config from a full job config.

    Args:
        config: Job configuration with backend settings.

    Returns:
        Configured Backend instance.
    """
    return create_backend_from_config(config.backend)


def setup_learning(
    config: JobConfig,
    *,
    global_learning_store_override: GlobalLearningStore | None = None,
) -> tuple[OutcomeStore | None, GlobalLearningStore | None]:
    """Setup outcome store and global learning store if learning is enabled.

    Args:
        config: Job configuration with learning settings.
        global_learning_store_override: If provided, use this store instead
            of the module-level singleton.  The daemon injects its shared
            LearningHub store here to avoid opening a second SQLite connection.

    Returns:
        Tuple of (outcome_store, global_learning_store), either may be None.
    """
    if not config.learning.enabled:
        return None, None

    from marianne.learning.outcomes import JsonOutcomeStore

    outcome_store: OutcomeStore | None = None
    outcome_store_path = config.get_outcome_store_path()
    if config.learning.outcome_store_type == "json":
        outcome_store = JsonOutcomeStore(outcome_store_path)

    # Prefer injected store (from daemon LearningHub) over the
    # module-level singleton.  This avoids opening a second
    # SQLite connection when the daemon already owns one.
    if global_learning_store_override is not None:
        global_learning_store = global_learning_store_override
    else:
        from marianne.learning.global_store import get_global_store

        global_learning_store = get_global_store()

    return outcome_store, global_learning_store


def setup_notifications(config: JobConfig) -> NotificationManager | None:
    """Setup notification manager from config.

    Args:
        config: Job configuration with notification settings.

    Returns:
        NotificationManager if notifications configured, else None.
    """
    if not config.notifications:
        return None

    from marianne.notifications import NotificationManager
    from marianne.notifications.factory import create_notifiers_from_config

    notifiers = create_notifiers_from_config(config.notifications)
    if not notifiers:
        return None

    return NotificationManager(notifiers)


def create_state_backend(
    workspace: Path,
    backend_type: str = "json",
) -> StateBackend:
    """Create state persistence backend.

    Args:
        workspace: Workspace directory for state files.
        backend_type: "json" or "sqlite".

    Returns:
        Configured StateBackend instance.
    """
    from marianne.state import JsonStateBackend, SQLiteStateBackend

    if backend_type == "sqlite":
        return SQLiteStateBackend(workspace / ".marianne-state.db")
    else:
        return JsonStateBackend(workspace)


__all__ = [
    "create_backend",
    "create_backend_from_config",
    "create_state_backend",
    "setup_learning",
    "setup_notifications",
]
