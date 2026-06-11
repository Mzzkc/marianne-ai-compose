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

from marianne.core.constants import STATE_DB_FILENAME
from marianne.core.logging import get_logger

_logger = get_logger(__name__)

if TYPE_CHECKING:
    from marianne.core.config import JobConfig
    from marianne.learning.global_store import GlobalLearningStore
    from marianne.learning.outcomes import OutcomeStore
    from marianne.notifications.base import NotificationManager
    from marianne.state.base import StateBackend


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
        return SQLiteStateBackend(workspace / STATE_DB_FILENAME)
    else:
        return JsonStateBackend(workspace)


__all__ = [
    "create_state_backend",
    "setup_learning",
    "setup_notifications",
]
