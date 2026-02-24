"""Data reader for the Mozart Monitor TUI.

Provides ``MonitorReader`` which reads system snapshots and process events
from multiple data sources (IPC, SQLite, JSONL) for display in ``mozart top``.

The reader auto-detects the best available data source:
1. IPC (DaemonClient) — live data from the running conductor
2. SQLite (MonitorStorage) — historical data from the profiler database
3. JSONL tail — streaming fallback when neither IPC nor SQLite available
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from mozart.core.logging import get_logger
from mozart.daemon.profiler.models import ProcessEvent, SystemSnapshot
from mozart.daemon.profiler.storage import MonitorStorage

_logger = get_logger("tui.reader")


class MonitorReader:
    """Reads profiler data for display in the TUI.

    Supports three data sources, tried in priority order:

    1. **IPC** — queries the running daemon via ``DaemonClient``
    2. **SQLite** — reads directly from ``MonitorStorage``
    3. **JSONL** — tails the NDJSON streaming log

    Parameters
    ----------
    storage:
        Optional ``MonitorStorage`` instance for SQLite reads.
    jsonl_path:
        Path to the JSONL streaming file.
    ipc_client:
        Optional ``DaemonClient`` for live daemon queries.
    """

    def __init__(
        self,
        storage: MonitorStorage | None = None,
        jsonl_path: Path | None = None,
        ipc_client: Any | None = None,
    ) -> None:
        self._storage = storage
        self._jsonl_path = jsonl_path.expanduser() if jsonl_path else None
        self._ipc_client = ipc_client
        self._source: str = "none"

    @property
    def source(self) -> str:
        """The active data source name: 'ipc', 'sqlite', 'jsonl', or 'none'."""
        return self._source

    # ------------------------------------------------------------------
    # Auto-detection
    # ------------------------------------------------------------------

    async def _detect_source(self) -> str:
        """Detect the best available data source.

        Tries IPC first (live daemon), then SQLite, then JSONL.
        """
        # Try IPC
        if self._ipc_client is not None:
            try:
                running = await self._ipc_client.is_daemon_running()
                if running:
                    return "ipc"
            except Exception:
                _logger.debug("reader.ipc_detect_failed", exc_info=True)

        # Try SQLite
        if self._storage is not None:
            return "sqlite"

        # Try JSONL
        if self._jsonl_path is not None and self._jsonl_path.exists():
            return "jsonl"

        return "none"

    async def _ensure_source(self) -> None:
        """Detect source on first use."""
        if self._source == "none":
            self._source = await self._detect_source()

    # ------------------------------------------------------------------
    # Snapshot reads
    # ------------------------------------------------------------------

    async def get_latest_snapshot(self) -> SystemSnapshot | None:
        """Get the most recent system snapshot.

        Returns None if no data is available from any source.
        """
        await self._ensure_source()

        if self._source == "ipc":
            return await self._get_latest_ipc()
        elif self._source == "sqlite":
            return await self._get_latest_sqlite()
        elif self._source == "jsonl":
            return self._get_latest_jsonl()
        return None

    async def get_snapshots(
        self, since: float, limit: int = 100
    ) -> list[SystemSnapshot]:
        """Get snapshots since the given unix timestamp."""
        await self._ensure_source()

        if self._source == "sqlite" and self._storage is not None:
            return await self._storage.read_snapshots(since, limit)
        elif self._source == "ipc":
            # IPC doesn't support historical queries — return latest only
            latest = await self._get_latest_ipc()
            if latest and latest.timestamp >= since:
                return [latest]
        elif self._source == "jsonl":
            return self._read_jsonl_since(since, limit)
        return []

    # ------------------------------------------------------------------
    # Event reads
    # ------------------------------------------------------------------

    async def get_events(
        self, since: float, limit: int = 50
    ) -> list[ProcessEvent]:
        """Get process lifecycle events since the given unix timestamp."""
        await self._ensure_source()

        if self._source == "sqlite" and self._storage is not None:
            return await self._storage.read_events(since, limit)
        elif self._source == "ipc" and self._ipc_client is not None:
            return await self._get_events_ipc(since, limit)
        return []

    async def get_observer_events(
        self, job_id: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get recent observer events via IPC.

        Returns observer events (file and process activity) from the
        ObserverRecorder's in-memory ring buffer.

        Args:
            job_id: Specific job ID, or None for all jobs.
            limit: Maximum number of events to return.

        Returns:
            List of observer event dicts, newest first. Empty if no
            IPC client or on error.
        """
        await self._ensure_source()
        if self._source == "ipc" and self._ipc_client is not None:
            try:
                result = await self._ipc_client.call(
                    "daemon.observer_events",
                    {"job_id": job_id, "limit": limit},
                )
                if result and isinstance(result, dict):
                    events: list[dict[str, Any]] = result.get("events", [])
                    return events
            except Exception:
                _logger.debug("reader.observer_events_failed", exc_info=True)
        return []

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream_snapshots(self) -> AsyncIterator[SystemSnapshot]:
        """Yield snapshots as they become available.

        For JSONL: tails the file, parsing new lines as they appear.
        For IPC: polls the daemon at a regular interval.
        For SQLite: polls the database at a regular interval.
        """
        await self._ensure_source()

        if self._source == "jsonl":
            async for snapshot in self._tail_jsonl():
                yield snapshot
        elif self._source == "ipc":
            async for snapshot in self._poll_ipc():
                yield snapshot
        elif self._source == "sqlite":
            async for snapshot in self._poll_sqlite():
                yield snapshot

    # ------------------------------------------------------------------
    # IPC source
    # ------------------------------------------------------------------

    async def _get_latest_ipc(self) -> SystemSnapshot | None:
        """Get latest snapshot via IPC daemon.status call."""
        if self._ipc_client is None:
            return None
        try:
            result = await self._ipc_client.call("daemon.top")
            if result and isinstance(result, dict):
                return SystemSnapshot(**result)
        except Exception:
            # Fallback: daemon may not support monitor.snapshot yet
            _logger.debug("reader.ipc_snapshot_failed", exc_info=True)
        return None

    async def _poll_ipc(self) -> AsyncIterator[SystemSnapshot]:
        """Poll IPC for snapshots at regular intervals."""
        while True:
            snapshot = await self._get_latest_ipc()
            if snapshot is not None:
                yield snapshot
            await asyncio.sleep(2.0)

    async def _get_events_ipc(
        self, since: float, limit: int = 50
    ) -> list[ProcessEvent]:
        """Get process events via IPC ``daemon.events`` call."""
        if self._ipc_client is None:
            return []
        try:
            result = await self._ipc_client.call(
                "daemon.events", {"limit": limit}
            )
            if not result or not isinstance(result, dict):
                return []
            raw_events = result.get("events", [])
            events: list[ProcessEvent] = []
            for raw in raw_events:
                if not isinstance(raw, dict):
                    continue
                evt = ProcessEvent(**raw)
                if evt.timestamp >= since:
                    events.append(evt)
            return events
        except Exception:
            _logger.debug("reader.ipc_events_failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # SQLite source
    # ------------------------------------------------------------------

    async def _get_latest_sqlite(self) -> SystemSnapshot | None:
        """Get the most recent snapshot from SQLite."""
        if self._storage is None:
            return None
        # Read last 5 seconds of snapshots, take the latest
        since = time.time() - 10.0
        snapshots = await self._storage.read_snapshots(since, limit=1)
        if snapshots:
            return snapshots[-1]
        # Try wider window if no recent data
        since = time.time() - 3600.0
        snapshots = await self._storage.read_snapshots(since, limit=1)
        return snapshots[-1] if snapshots else None

    async def _poll_sqlite(self) -> AsyncIterator[SystemSnapshot]:
        """Poll SQLite for new snapshots at regular intervals."""
        last_ts = time.time() - 10.0
        while True:
            if self._storage is not None:
                snapshots = await self._storage.read_snapshots(last_ts, limit=10)
                for s in snapshots:
                    if s.timestamp > last_ts:
                        last_ts = s.timestamp
                        yield s
            await asyncio.sleep(2.0)

    # ------------------------------------------------------------------
    # JSONL source
    # ------------------------------------------------------------------

    def _get_latest_jsonl(self) -> SystemSnapshot | None:
        """Read the last line from the JSONL file."""
        if self._jsonl_path is None or not self._jsonl_path.exists():
            return None
        try:
            last_line = ""
            with self._jsonl_path.open("r") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        last_line = stripped
            if last_line:
                return SystemSnapshot(**json.loads(last_line))
        except (json.JSONDecodeError, OSError, ValueError):
            _logger.debug("reader.jsonl_parse_failed", exc_info=True)
        return None

    def _read_jsonl_since(
        self, since: float, limit: int = 100
    ) -> list[SystemSnapshot]:
        """Read JSONL lines with timestamp >= since."""
        if self._jsonl_path is None or not self._jsonl_path.exists():
            return []
        results: list[SystemSnapshot] = []
        try:
            with self._jsonl_path.open("r") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        data = json.loads(stripped)
                        if data.get("timestamp", 0) >= since:
                            results.append(SystemSnapshot(**data))
                            if len(results) >= limit:
                                break
                    except (json.JSONDecodeError, ValueError):
                        continue
        except OSError:
            _logger.debug("reader.jsonl_read_failed", exc_info=True)
        return results

    async def _tail_jsonl(self) -> AsyncIterator[SystemSnapshot]:
        """Tail the JSONL file, yielding new snapshots as lines are appended."""
        if self._jsonl_path is None:
            return

        # Seek to end and watch for new lines
        try:
            f = self._jsonl_path.open("r")
            f.seek(0, 2)  # seek to end
        except OSError:
            return

        try:
            while True:
                line = f.readline()
                if line:
                    stripped = line.strip()
                    if stripped:
                        try:
                            data = json.loads(stripped)
                            yield SystemSnapshot(**data)
                        except (json.JSONDecodeError, ValueError):
                            continue
                else:
                    await asyncio.sleep(0.5)
        finally:
            f.close()


__all__ = ["MonitorReader"]
