"""Centralized learning hub — single store shared across all daemon jobs.

Instead of each job opening its own connection to the global learning store,
the daemon maintains a single GlobalLearningStore instance and passes it to
every JobService.  This provides three benefits:

1. **Instant cross-job learning.**  Pattern discoveries in Job A are
   immediately visible to Job B because they share the same in-memory
   store backed by one SQLite connection.

2. **No locking contention.**  Multiple jobs writing through the same
   store instance serialise at the Python level rather than fighting
   over SQLite WAL locks across threads/processes.

3. **Lifecycle ownership.**  The daemon controls when the store is
   created (``start``) and when a final persist happens (``stop``),
   ensuring clean shutdown semantics.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from marianne.core.logging import get_logger
from marianne.daemon.task_utils import log_task_exception
from marianne.learning.global_store import GlobalLearningStore
from marianne.learning.store.base import DEFAULT_GLOBAL_STORE_PATH

_logger = get_logger("daemon.learning")


class LearningHub:
    """Centralized learning for daemon mode.

    Instead of each job opening its own SQLite connection to
    ~/.marianne/global-learning.db, the daemon maintains a single
    GlobalLearningStore instance shared across all jobs.

    Benefits:
    - Pattern discoveries in Job A immediately available to Job B
    - No cross-process SQLite locking contention
    - Periodic persistence instead of per-write persistence
    - Cross-job effectiveness tracking
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or DEFAULT_GLOBAL_STORE_PATH
        self._store: GlobalLearningStore | None = None
        self._heartbeat_interval = 60.0  # Persist every 60 seconds
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Initialize store and start persistence loop."""
        self._store = GlobalLearningStore(self._db_path)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._heartbeat_task.add_done_callback(self._on_heartbeat_done)
        _logger.info("learning_hub.started", db_path=str(self._db_path))

    @staticmethod
    def _on_heartbeat_done(task: asyncio.Task[None]) -> None:
        """Log errors from the heartbeat task instead of losing them."""
        log_task_exception(task, _logger, "learning_hub.heartbeat_failed")

    async def stop(self) -> None:
        """Persist final state and stop."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        if self._store:
            # Final persist — the store commits on each _get_connection()
            # exit, so no explicit flush is needed, but we log for
            # observability.
            _logger.info("learning_hub.final_persist")
            self._store.close()
            self._store = None
        _logger.info("learning_hub.stopped")

    @property
    def store(self) -> GlobalLearningStore:
        """Get the shared learning store.

        Raises:
            RuntimeError: If the hub has not been started yet.
        """
        if self._store is None:
            raise RuntimeError("LearningHub not started")
        return self._store

    @property
    def is_running(self) -> bool:
        """Whether the hub has been started and has an active store."""
        return self._store is not None

    async def _heartbeat_loop(self) -> None:
        """Periodically log learning store health.

        The GlobalLearningStore commits per-operation, so this loop
        primarily serves as a heartbeat for observability.
        """
        while True:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                if self._store is not None:
                    _logger.debug(
                        "learning_hub.heartbeat",
                        store_active=True,
                        db_path=str(self._db_path),
                    )
            except asyncio.CancelledError:
                break
            except (OSError, sqlite3.Error):
                _logger.warning("learning_hub.heartbeat_error", exc_info=True)


__all__ = ["LearningHub"]
