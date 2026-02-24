"""Observer event recorder — persists per-job observer events to JSONL.

Subscribes to ``observer.*`` events on the EventBus, applies path
exclusion and coalescing, then writes to per-job JSONL files inside
each job's workspace. Also maintains an in-memory ring buffer for
real-time TUI consumption via IPC.
"""

from __future__ import annotations

import fnmatch
import json
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mozart.core.logging import get_logger
from mozart.daemon.config import ObserverConfig

if TYPE_CHECKING:
    from mozart.daemon.event_bus import EventBus
    from mozart.daemon.types import ObserverEvent

_logger = get_logger("daemon.observer_recorder")

_FILE_EVENTS = frozenset({
    "observer.file_created",
    "observer.file_modified",
    "observer.file_deleted",
})

_PROCESS_EVENTS = frozenset({
    "observer.process_spawned",
    "observer.process_exited",
})

_ALL_OBSERVER_EVENTS = _FILE_EVENTS | _PROCESS_EVENTS


class ObserverRecorder:
    """EventBus subscriber that persists observer events to per-job JSONL files."""

    def __init__(self, config: ObserverConfig, event_bus: EventBus) -> None:
        self._config = config
        self._event_bus = event_bus
        self._sub_id: str | None = None
        self._jobs: dict[str, _JobRecorderState] = {}

    def register_job(self, job_id: str, workspace: Path) -> None:
        """Start recording events for a job."""
        if job_id in self._jobs:
            return
        state = _JobRecorderState(job_id, workspace)
        if self._config.persist_events:
            jsonl_path = workspace / ".mozart-observer.jsonl"
            try:
                state.file_handle = open(jsonl_path, "a", encoding="utf-8")  # noqa: SIM115
            except OSError:
                _logger.warning(
                    "observer_recorder.open_failed",
                    job_id=job_id,
                    path=str(jsonl_path),
                    exc_info=True,
                )
        self._jobs[job_id] = state
        _logger.info("observer_recorder.registered", job_id=job_id)

    def unregister_job(self, job_id: str) -> None:
        """Stop recording events for a job, flush and close file."""
        state = self._jobs.pop(job_id, None)
        if state is None:
            return
        self._flush_state(state)
        self._close_state(state)
        _logger.info("observer_recorder.unregistered", job_id=job_id)

    def flush(self, job_id: str) -> None:
        """Flush coalesce buffer and file handle for a job."""
        state = self._jobs.get(job_id)
        if state is None:
            return
        self._flush_state(state)

    def _write_event(self, job_id: str, event: ObserverEvent) -> None:
        """Write a single event to JSONL and ring buffer (if not excluded)."""
        state = self._jobs.get(job_id)
        if state is None:
            _logger.debug(
                "observer_recorder.event_for_unregistered_job",
                job_id=job_id,
                event_type=event.get("event", ""),
            )
            return

        # Check path exclusion for file events
        event_type = event.get("event", "")
        if event_type in _FILE_EVENTS:
            data = event.get("data") or {}
            rel_path = data.get("path", "")
            if rel_path and self._should_exclude(rel_path):
                return

        # REVIEW FIX: Add to ring buffer BEFORE file write attempt.
        # This ensures TUI gets events regardless of disk errors.
        state.recent_events.append(event)

        # Write to JSONL
        if state.file_handle is not None:
            try:
                line = json.dumps(event, separators=(",", ":")) + "\n"
                state.file_handle.write(line)
            except OSError:
                _logger.warning(
                    "observer_recorder.write_failed",
                    job_id=job_id,
                    exc_info=True,
                )

    def _flush_state(self, state: _JobRecorderState) -> None:
        """Flush coalesce buffer and file handle."""
        # Drain coalesce buffer
        for _path, (_ts, event) in state.coalesce_buffer.items():
            if state.file_handle is not None:
                try:
                    line = json.dumps(event, separators=(",", ":")) + "\n"
                    state.file_handle.write(line)
                except OSError:
                    pass
        state.coalesce_buffer.clear()

        # Flush file to OS page cache
        if state.file_handle is not None:
            try:
                state.file_handle.flush()
            except OSError:
                pass

    def _close_state(self, state: _JobRecorderState) -> None:
        """Close the file handle."""
        if state.file_handle is not None:
            try:
                state.file_handle.close()
            except OSError:
                pass
            state.file_handle = None

    def _should_exclude(self, rel_path: str) -> bool:
        """Check if a relative path matches any exclusion pattern."""
        for pattern in self._config.exclude_patterns:
            # Match against full path
            if fnmatch.fnmatch(rel_path, pattern):
                return True
            # Match with wildcard prefix (e.g. */pattern)
            if fnmatch.fnmatch(rel_path, f"*/{pattern}"):
                return True
            # Check each path component (for directory patterns like ".git/")
            parts = rel_path.replace("\\", "/").split("/")
            for part in parts:
                if fnmatch.fnmatch(part + "/", pattern):
                    return True
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False


class _JobRecorderState:
    """Per-job state for the observer recorder."""

    __slots__ = ("job_id", "workspace", "file_handle", "recent_events", "coalesce_buffer")

    def __init__(self, job_id: str, workspace: Path) -> None:
        self.job_id = job_id
        self.workspace = workspace
        self.file_handle: Any = None
        self.recent_events: deque[ObserverEvent] = deque(maxlen=200)
        self.coalesce_buffer: dict[str, tuple[float, ObserverEvent]] = {}


__all__ = ["ObserverRecorder"]
