"""SSE endpoint for real-time system monitor snapshots.

Streams SystemSnapshot NDJSON events from the profiler's JSONL file or
SQLite storage to connected dashboard clients.  Each event is a complete
``SystemSnapshot`` serialized as JSON — the same format produced by
``mzt top --json``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from marianne.core.logging import get_logger

_logger = get_logger("dashboard.monitor")

router = APIRouter(prefix="/api/monitor", tags=["Monitor"])

# Default paths (match ProfilerConfig defaults)
_DEFAULT_JSONL_PATH = Path("~/.marianne/monitor.jsonl").expanduser()
_DEFAULT_DB_PATH = Path("~/.marianne/monitor.db").expanduser()

# Cached MonitorStorage instance (avoids re-init on every request/stream)
_monitor_storage_cache: dict[str, Any] = {}


async def get_monitor_storage(db_path: Path | None = None) -> Any:
    """Get or create a cached MonitorStorage instance.

    Returns an initialized MonitorStorage that can be reused across
    requests instead of creating a new instance per call.
    """
    from marianne.daemon.profiler.storage import MonitorStorage

    path = db_path or _DEFAULT_DB_PATH
    key = str(path)
    cached = _monitor_storage_cache.get(key)
    if cached is not None:
        return cached

    storage = MonitorStorage(db_path=path)
    await storage.initialize()
    _monitor_storage_cache[key] = storage
    return storage


async def _tail_jsonl(
    jsonl_path: Path,
    poll_interval: float = 1.0,
) -> AsyncIterator[str]:
    """Tail the monitor JSONL file and yield NDJSON lines as SSE events.

    Each line in the JSONL file is a serialized SystemSnapshot.  This
    generator tails the file (similar to ``tail -f``) and yields new
    lines as SSE ``data:`` events with event type ``snapshot``.

    Args:
        jsonl_path: Path to the JSONL streaming log.
        poll_interval: Seconds between file polls for new content.

    Yields:
        SSE-formatted strings (``event: snapshot\\ndata: {...}\\n\\n``).
    """
    # Send initial connection event
    yield f"event: connected\ndata: {json.dumps({'source': 'jsonl', 'path': str(jsonl_path)})}\n\n"

    # Seek to end of file for live tailing
    last_position: int = 0
    if jsonl_path.exists():
        last_position = jsonl_path.stat().st_size

    heartbeat_counter = 0

    try:
        while True:
            try:
                if not jsonl_path.exists():
                    await asyncio.sleep(poll_interval)
                    heartbeat_counter += 1
                    if heartbeat_counter >= 30:
                        yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"
                        heartbeat_counter = 0
                    continue

                current_size = jsonl_path.stat().st_size

                if current_size < last_position:
                    # File was rotated — start from beginning of new file
                    last_position = 0

                if current_size > last_position:
                    with open(jsonl_path, encoding="utf-8", errors="replace") as f:
                        f.seek(last_position)
                        new_content = f.read()

                    last_position = current_size

                    for line in new_content.strip().splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        # Validate it's proper JSON before sending
                        try:
                            json.loads(line)
                            yield f"event: snapshot\ndata: {line}\n\n"
                        except json.JSONDecodeError:
                            _logger.debug("monitor_stream.invalid_json_line")
                            continue

                    heartbeat_counter = 0
                else:
                    heartbeat_counter += 1
                    if heartbeat_counter >= 30:
                        yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"
                        heartbeat_counter = 0

                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                break
            except OSError:
                _logger.debug("monitor_stream.file_read_error", exc_info=True)
                await asyncio.sleep(poll_interval * 2)

    except asyncio.CancelledError:
        pass


async def _stream_from_sqlite(
    db_path: Path,
    poll_interval: float = 2.0,
    lookback_seconds: float = 30.0,
) -> AsyncIterator[str]:
    """Poll SQLite for recent snapshots and yield as SSE events.

    Fallback when the JSONL file is not available.  Queries the most
    recent snapshot(s) from the ``snapshots`` table at each interval.

    Args:
        db_path: Path to the SQLite monitor database.
        poll_interval: Seconds between database polls.
        lookback_seconds: Initial lookback window for the first query.

    Yields:
        SSE-formatted strings with event type ``snapshot``.
    """
    try:
        storage = await get_monitor_storage(db_path)
    except ImportError:
        yield f"event: error\ndata: {json.dumps({'error': 'aiosqlite not available'})}\n\n"
        return

    yield f"event: connected\ndata: {json.dumps({'source': 'sqlite', 'path': str(db_path)})}\n\n"

    last_ts = time.time() - lookback_seconds
    heartbeat_counter = 0

    try:
        while True:
            try:
                snapshots = await storage.read_snapshots(since=last_ts, limit=10)

                for snap in snapshots:
                    snap_json = json.dumps(snap.model_dump(mode="json"), separators=(",", ":"))
                    yield f"event: snapshot\ndata: {snap_json}\n\n"
                    if snap.timestamp > last_ts:
                        last_ts = snap.timestamp + 0.001

                if not snapshots:
                    heartbeat_counter += 1
                    if heartbeat_counter >= 15:
                        yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"
                        heartbeat_counter = 0
                else:
                    heartbeat_counter = 0

                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                break
            except Exception:
                _logger.debug("monitor_stream.sqlite_read_error", exc_info=True)
                await asyncio.sleep(poll_interval * 2)

    except asyncio.CancelledError:
        pass


async def _stream_from_daemon(
    poll_interval: float = 2.0,
) -> AsyncIterator[str]:
    """Poll DaemonSystemView for snapshots and yield as SSE events.

    Used when the daemon service layer is wired in ``app.py``.
    """
    from marianne.dashboard.routes.system import get_system_view

    system_view = get_system_view()

    yield f"event: connected\ndata: {json.dumps({'source': 'daemon'})}\n\n"

    heartbeat_counter = 0

    try:
        while True:
            try:
                snap = await system_view.get_snapshot()
                if snap is not None:
                    snap_json = json.dumps(snap, separators=(",", ":"), default=str)
                    yield f"event: snapshot\ndata: {snap_json}\n\n"
                    heartbeat_counter = 0
                else:
                    heartbeat_counter += 1
                    if heartbeat_counter >= 15:
                        yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"
                        heartbeat_counter = 0
            except asyncio.CancelledError:
                break
            except Exception:
                _logger.debug("monitor_stream.daemon_poll_error", exc_info=True)
                heartbeat_counter += 1
                if heartbeat_counter >= 15:
                    yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"
                    heartbeat_counter = 0

            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        pass


def _daemon_available() -> bool:
    """Check if DaemonSystemView is configured."""
    try:
        from marianne.dashboard.routes.system import get_system_view
        get_system_view()
        return True
    except RuntimeError:
        return False


@router.get("/stream")
async def stream_monitor(
    source: str = Query(
        default="auto",
        description=(
            "Data source: 'daemon', 'jsonl', 'sqlite', "
            "or 'auto' (tries daemon, then jsonl, then sqlite)"
        ),
    ),
    poll_interval: float = Query(
        default=1.0,
        ge=0.5,
        le=30.0,
        description="Poll interval in seconds",
    ),
) -> StreamingResponse:
    """Stream real-time system monitor snapshots via Server-Sent Events.

    Each event contains a complete SystemSnapshot as JSON — the same
    format used by ``mzt top --json``.

    **Source selection:**

    - ``auto`` (default): Uses daemon IPC if configured, then JSONL, then SQLite.
    - ``daemon``: Polls ``DaemonSystemView`` via IPC.
    - ``jsonl``: Tails ``~/.marianne/monitor.jsonl`` directly.
    - ``sqlite``: Polls ``~/.marianne/monitor.db`` for recent snapshots.

    Returns:
        SSE stream of ``snapshot`` events, with periodic ``heartbeat``
        events to keep the connection alive.
    """
    if poll_interval < 0.5 or poll_interval > 30.0:
        raise HTTPException(
            status_code=400,
            detail="poll_interval must be between 0.5 and 30.0 seconds",
        )

    jsonl_path = _DEFAULT_JSONL_PATH
    db_path = _DEFAULT_DB_PATH

    # Resolve source
    if source == "auto":
        if _daemon_available():
            source = "daemon"
        elif jsonl_path.exists():
            source = "jsonl"
        elif db_path.exists():
            source = "sqlite"
        else:
            raise HTTPException(
                status_code=503,
                detail="No monitor data available. Is the conductor running with profiler enabled?",
            )

    if source == "daemon":
        generator = _stream_from_daemon(poll_interval=poll_interval)
    elif source == "jsonl":
        generator = _tail_jsonl(jsonl_path, poll_interval=poll_interval)
    elif source == "sqlite":
        generator = _stream_from_sqlite(db_path, poll_interval=poll_interval)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source: {source}. Must be 'auto', 'daemon', 'jsonl', or 'sqlite'.",
        )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/snapshot")
async def get_latest_snapshot() -> dict[str, object]:
    """Get the most recent system snapshot (one-shot, not streaming).

    When ``DaemonSystemView`` is configured, fetches a live snapshot via
    IPC.  Falls back to reading ``monitor.db`` when the daemon service
    layer isn't wired.
    """
    # Try DaemonSystemView first (set by app.py when daemon is available)
    try:
        from marianne.dashboard.routes.system import get_system_view
        system_view = get_system_view()
        snap = await system_view.get_snapshot()
        if snap is not None:
            return snap
    except RuntimeError:
        pass  # SystemView not configured — fall through to file-based path
    except Exception:
        _logger.debug("monitor_snapshot.daemon_error", exc_info=True)

    # Fallback: read from monitor.db
    db_path = _DEFAULT_DB_PATH

    if not db_path.exists():
        raise HTTPException(
            status_code=503,
            detail="No monitor data available. Is the conductor running with profiler enabled?",
        )

    try:
        storage = await get_monitor_storage(db_path)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="aiosqlite not available",
        ) from None

    # Get the most recent snapshot
    since = time.time() - 60  # Last minute
    snapshots = await storage.read_snapshots(since=since, limit=1)

    if not snapshots:
        raise HTTPException(
            status_code=404,
            detail="No recent snapshots found",
        )

    result: dict[str, object] = snapshots[-1].model_dump(mode="json")
    return result
