"""Stdlib file watcher backing conductor config hot reload (#408).

Polls the config file and the instrument profile source directories for
CONTENT changes (sha256, not bare mtime — survives editors' atomic
rename-save and in-place rewrites that keep mtime granularity ambiguous)
and funnels detected changes into the single reload path
(``DaemonProcess.reload_configuration`` → ``JobManager.reload_configuration``).

Deliberately dependency-free (MN-005): an asyncio task on a short poll
interval with a configurable debounce. Three watched directories do not
justify watchdog/inotify.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from marianne.core.logging import get_logger

_logger = get_logger("daemon.hot_reload")

_MISSING = "<missing>"
_UNREADABLE = "<unreadable>"

ReloadFn = Callable[[str], Awaitable[Any]]


class ConfigWatcher:
    """Content-hash poller over the config file + profile directories.

    Args:
        config_file: The conductor config file (None watches only profiles).
        profile_dirs: Instrument profile source directories — the same
            directories ``load_all_profiles`` reads (builtins, organization,
            venue). Missing directories are tracked so later creation fires.
        reload_fn: Async reload entry — always the single reload path.
        enabled_fn: Live off-switch read every cycle (``hot_reload.enabled``).
        debounce_fn: Live settle window in seconds read every cycle
            (``hot_reload.debounce_seconds``).
        poll_interval: Seconds between scans. Kept short so the debounce
            window, not the poll cadence, defines detection latency.
    """

    def __init__(
        self,
        *,
        config_file: Path | None,
        profile_dirs: list[Path],
        class_files: list[Path] | None = None,
        reload_fn: ReloadFn,
        enabled_fn: Callable[[], bool] | None = None,
        debounce_fn: Callable[[], float] | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self._config_file = config_file
        self._profile_dirs = list(profile_dirs)
        self._class_files = list(class_files or [])
        self._reload_fn = reload_fn
        self._enabled_fn = enabled_fn or (lambda: True)
        self._debounce_fn = debounce_fn or (lambda: 2.0)
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._snapshot: dict[Path, str] = {}

    # ─── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        """Capture the baseline snapshot and start the poll loop."""
        self._snapshot = self._scan()
        self._task = asyncio.create_task(self._run(), name="config-watcher")

    async def stop(self) -> None:
        """Cancel the poll loop. Safe to call more than once."""
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ─── Scanning ──────────────────────────────────────────────

    def _scan(self) -> dict[Path, str]:
        """Hash every watched file. Absent files are recorded as changes-in-waiting."""
        files: dict[Path, str] = {}
        candidates: list[Path] = []
        if self._config_file is not None:
            candidates.append(self._config_file)
        candidates.extend(self._class_files)
        for directory in self._profile_dirs:
            for pattern in ("*.yaml", "*.yml"):
                candidates.extend(sorted(directory.glob(pattern)))
        for path in candidates:
            resolved = path.resolve()
            if not path.is_file():
                files[resolved] = _MISSING
                continue
            try:
                files[resolved] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                files[resolved] = _UNREADABLE
        return files

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            if not self._enabled_fn():
                # Off-switch honored live: `hot_reload.enabled: false` in a
                # reloaded config stops triggering; re-enabling resumes.
                continue
            if self._scan() == self._snapshot:
                continue

            # Change detected — debounce so an editor's atomic rename or a
            # multi-file profile edit settles before we read.
            debounce = max(0.05, float(self._debounce_fn()))
            await asyncio.sleep(debounce)
            settled = self._scan()
            if settled == self._snapshot:
                # Transient churn that reverted during the settle window.
                self._snapshot = settled
                continue
            self._snapshot = settled  # re-baseline either way (no re-fire loop)
            try:
                result = await self._reload_fn("watcher")
            except Exception:
                # The reload itself is fail-closed; a raising callback must
                # never kill the watcher.
                _logger.warning("watcher.reload_raised", exc_info=True)
                continue
            success = bool(getattr(result, "success", False))
            event = "watcher.reload_applied" if success else "watcher.reload_declined"
            _logger.info(
                event,
                applied=list(getattr(result, "applied", []) or []),
                declined=list(getattr(result, "declined", []) or []),
                generation=int(getattr(result, "config_generation", 0)),
            )
            # Re-baseline AFTER the reload so changes written during the
            # reload window re-arm instead of being swallowed.
            self._snapshot = self._scan()
