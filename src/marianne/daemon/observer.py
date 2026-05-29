"""Job observer — filesystem and process tree monitoring co-task.

Watches each running job's workspace for file changes (via watchfiles)
and monitors the process tree (via psutil) to produce ObserverEvents
independently of the baton's self-reports.  Events are published to
the EventBus for downstream consumers (SSE dashboard, learning hub).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marianne.core.logging import get_logger

if TYPE_CHECKING:
    from marianne.daemon.event_bus import EventBus
    from marianne.daemon.types import ObserverEvent

_logger = get_logger("daemon.observer")


class JobObserver:
    """Async co-task that monitors a job's workspace and process tree.

    Lifecycle:
        observer = JobObserver(job_id, workspace, pid, event_bus)
        await observer.start()
        # ... runs until job completes ...
        await observer.stop()

    Produces events:
        - ``observer.file_created`` / ``observer.file_modified`` / ``observer.file_deleted``
        - ``observer.process_spawned`` / ``observer.process_exited``
    """

    def __init__(
        self,
        job_id: str,
        workspace: Path,
        pid: int,
        event_bus: EventBus,
        *,
        watch_interval: float = 2.0,
    ) -> None:
        self._job_id = job_id
        self._workspace = workspace
        self._pid = pid
        self._event_bus = event_bus
        self._watch_interval = watch_interval
        self._stop_event = asyncio.Event()
        self._fs_task: asyncio.Task[None] | None = None
        self._proc_task: asyncio.Task[None] | None = None
        self._running = False
        self._start_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        """Whether the observer is currently running."""
        return self._running

    async def start(self) -> None:
        """Start filesystem and process monitoring tasks."""
        async with self._start_lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()

        self._fs_task = asyncio.create_task(
            self._watch_filesystem(),
            name=f"observer-fs-{self._job_id}",
        )
        self._proc_task = asyncio.create_task(
            self._watch_processes(),
            name=f"observer-proc-{self._job_id}",
        )
        _logger.info(
            "observer.started",
            job_id=self._job_id,
            workspace=str(self._workspace),
            pid=self._pid,
        )

    async def stop(self) -> None:
        """Stop both monitoring tasks gracefully."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

        tasks = [t for t in (self._fs_task, self._proc_task) if t is not None]
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._fs_task = None
        self._proc_task = None
        _logger.info("observer.stopped", job_id=self._job_id)

    async def _publish(self, event_name: str, data: dict[str, Any] | None = None) -> None:
        """Publish an observer event to the event bus."""
        evt: ObserverEvent = {
            "job_id": self._job_id,
            "sheet_num": 0,  # Observer events are job-level, not sheet-specific
            "event": event_name,
            "data": data,
            "timestamp": time.time(),
        }
        await self._event_bus.publish(evt)

    # ── Filesystem monitoring ──────────────────────────────────────────

    _FS_WATCH_MAX_RETRIES = 3
    _FS_WATCH_BASE_DELAY = 2.0  # seconds, doubled each retry

    async def _watch_filesystem(self) -> None:
        """Watch workspace for file changes using watchfiles.

        Retries with exponential backoff on transient errors so that a
        temporary filesystem hiccup doesn't permanently kill monitoring.
        """
        try:
            import watchfiles
        except ImportError:
            _logger.warning(
                "observer.watchfiles_unavailable",
                job_id=self._job_id,
                message="watchfiles not installed — filesystem monitoring disabled",
            )
            return

        event_map = {
            watchfiles.Change.added: "observer.file_created",
            watchfiles.Change.modified: "observer.file_modified",
            watchfiles.Change.deleted: "observer.file_deleted",
        }

        retries = 0
        while self._running:
            try:
                async for changes in watchfiles.awatch(
                    self._workspace,
                    stop_event=self._stop_event,
                    step=int(self._watch_interval * 1000),
                ):
                    if not self._running:
                        return
                    retries = 0  # reset on successful iteration
                    for change_type, path_str in changes:
                        try:
                            rel_path = str(Path(path_str).relative_to(self._workspace))
                        except ValueError:
                            rel_path = path_str

                        event_name = event_map.get(change_type, "observer.file_changed")
                        await self._publish(event_name, {"path": rel_path})
                # awatch iterator exhausted normally (stop_event set)
                return

            except asyncio.CancelledError:
                return
            except Exception:
                retries += 1
                if retries >= self._FS_WATCH_MAX_RETRIES:
                    _logger.error(
                        "observer.fs_watch_retries_exhausted",
                        job_id=self._job_id,
                        retries=retries,
                        exc_info=True,
                    )
                    return
                delay = self._FS_WATCH_BASE_DELAY * (2 ** (retries - 1))
                _logger.warning(
                    "observer.fs_watch_error_retrying",
                    job_id=self._job_id,
                    retry=retries,
                    max_retries=self._FS_WATCH_MAX_RETRIES,
                    backoff_seconds=delay,
                    exc_info=True,
                )
                await asyncio.sleep(delay)

    # ── Process tree monitoring ────────────────────────────────────────

    async def _watch_processes(self) -> None:
        """Poll process tree for child process changes."""
        try:
            import psutil
        except ImportError:
            _logger.warning(
                "observer.psutil_unavailable",
                job_id=self._job_id,
                message="psutil not installed — process monitoring disabled",
            )
            return

        known_pids: set[int] = set()

        try:
            while self._running:
                try:
                    proc = psutil.Process(self._pid)
                    current_children = proc.children(recursive=True)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    # Process exited — observer will be stopped by manager
                    await self._publish(
                        "observer.process_exited",
                        {"pid": self._pid, "role": "main"},
                    )
                    break

                current_pids = {c.pid for c in current_children}

                # Detect new child processes
                new_pids = current_pids - known_pids
                for child in current_children:
                    if child.pid in new_pids:
                        try:
                            name = child.name()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            name = "unknown"
                        await self._publish(
                            "observer.process_spawned",
                            {"pid": child.pid, "name": name},
                        )

                # Detect exited child processes
                exited_pids = known_pids - current_pids
                for pid in exited_pids:
                    await self._publish(
                        "observer.process_exited",
                        {"pid": pid, "role": "child"},
                    )

                known_pids = current_pids

                # Wait for next poll interval or stop signal
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._watch_interval,
                    )
                    break  # stop_event was set
                except TimeoutError:
                    continue

        except asyncio.CancelledError:
            return


__all__ = ["JobObserver"]
