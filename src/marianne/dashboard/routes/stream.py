"""Server-Sent Events (SSE) streaming API endpoints.

Job status streams use the ``DaemonEventBridge`` when a conductor is
available (real-time via ``daemon.monitor.stream``), falling back to
state-backend polling otherwise.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.constants import DAEMON_STATE_DB_PATH
from marianne.core.log_sources import (
    LogSource,
    discover_job_log_sources,
    iter_source_lines,
)
from marianne.core.logging import get_logger
from marianne.dashboard.app import get_state_backend
from marianne.dashboard.services.event_bridge import DaemonEventBridge
from marianne.dashboard.services.sse_manager import SSEEvent
from marianne.state.base import StateBackend

_logger = get_logger("dashboard.stream")

# Tail line count constants for log streaming
DEFAULT_TAIL_LINES: int = 100
MAX_TAIL_LINES: int = 1000
MAX_STATIC_LOG_DOWNLOAD_BYTES: int = 50 * 1024 * 1024
_FALLBACK_POLL_INTERVAL: float = 2.0

router = APIRouter(prefix="/api/jobs", tags=["Streaming"])


def _job_status_event(job_id: str, state: Any) -> SSEEvent:
    """Build a status SSE event from checkpoint state."""
    completed, total = state.get_progress()
    return SSEEvent(
        event="job_status",
        data=json.dumps(
            {
                "job_id": job_id,
                "status": state.status.value,
                "progress_percent": state.get_progress_percent(),
                "completed_sheets": completed,
                "total_sheets": total,
                "current_sheet": state.current_sheet,
                "error_message": state.error_message,
                "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            }
        ),
        id=f"status-{job_id}-{datetime.now().timestamp()}",
    )


def _job_status_signature(state: Any) -> tuple[Any, ...]:
    """Fields that must trigger a visible job-status update."""
    completed, total = state.get_progress()
    return (
        state.status.value,
        state.get_progress_percent(),
        completed,
        total,
        state.current_sheet,
        state.error_message,
        state.updated_at,
    )


def _get_event_bridge_safe() -> DaemonEventBridge | None:
    """Get the event bridge if available, without raising."""
    try:
        from marianne.dashboard.routes.events import get_event_bridge

        return get_event_bridge()
    except RuntimeError:
        return None


# ============================================================================
# Response Models
# ============================================================================


class LogDownloadInfo(BaseModel):
    """Information about log file for download."""

    job_id: str
    log_file: str
    size_bytes: int
    lines: int
    last_modified: datetime
    sources: list[str] = Field(default_factory=list)
    download_available: bool = True
    download_limit_bytes: int = MAX_STATIC_LOG_DOWNLOAD_BYTES


# ============================================================================
# Helper Functions
# ============================================================================


def _read_registry_job_metadata(job_id: str) -> dict[str, str | None]:
    """Read job file metadata from the daemon registry when available."""
    db_path = DAEMON_STATE_DB_PATH.expanduser()
    if not db_path.exists() or db_path.stat().st_size == 0:
        return {}

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT workspace, log_path, snapshot_path, config_path
                FROM jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
    except sqlite3.Error:
        _logger.warning("logs.registry_metadata_failed", job_id=job_id, exc_info=True)
        return {}

    return dict(row) if row is not None else {}


def _is_relevant_to_job_window(path: Path, state: CheckpointState) -> bool:
    """Avoid showing stale shared-workspace logs from unrelated old jobs."""
    if state.started_at is None:
        return True

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False

    started = state.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)

    completed = state.completed_at or datetime.now(UTC)
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=UTC)

    # A generous window keeps slow final writes and resume artifacts visible
    # without letting months-old shared workspace logs masquerade as current.
    start_ts = started.timestamp() - 3600
    end_ts = completed.timestamp() + 3600
    return start_ts <= mtime <= end_ts


def _append_log_source(
    sources: list[LogSource],
    seen: set[Path],
    path: Path | str | None,
    label: str,
    *,
    follow: bool = False,
    job_filter: str | None = None,
    state: CheckpointState | None = None,
    require_relevant: bool = False,
    require_matching_lines: bool = False,
) -> None:
    if path is None:
        return

    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return
    if require_relevant and state is not None and not _is_relevant_to_job_window(
        candidate, state
    ):
        return

    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    if resolved in seen:
        return

    source = LogSource(path=resolved, label=label, follow=follow, job_filter=job_filter)
    if require_matching_lines and not _iter_source_lines(source):
        return
    if not follow and candidate.stat().st_size == 0:
        return

    seen.add(resolved)
    sources.append(source)


def _append_workspace_log_sources(
    sources: list[LogSource],
    seen: set[Path],
    workspace: Path,
    state: CheckpointState,
) -> None:
    if not workspace.is_dir():
        return

    _append_log_source(
        sources,
        seen,
        workspace / "marianne.log",
        "workspace/marianne.log",
        follow=True,
        state=state,
        require_relevant=True,
    )
    _append_log_source(
        sources,
        seen,
        workspace / "logs" / "marianne.log",
        "workspace/logs/marianne.log",
        follow=True,
        state=state,
        require_relevant=True,
    )
    _append_log_source(
        sources,
        seen,
        workspace / ".marianne-observer.jsonl",
        "workspace observer events",
        follow=True,
        job_filter=state.job_id,
        state=state,
        require_relevant=True,
        require_matching_lines=True,
    )

    for pattern, label in (
        ("*.log", "workspace log"),
        ("logs/*.log", "workspace logs"),
    ):
        for path in sorted(workspace.glob(pattern)):
            _append_log_source(
                sources,
                seen,
                path,
                f"{label}: {path.name}",
                follow=True,
                state=state,
                require_relevant=True,
            )


def _append_snapshot_log_sources(
    sources: list[LogSource],
    seen: set[Path],
    snapshot_path: Path,
    job_id: str,
) -> None:
    if not snapshot_path.is_dir():
        return

    _append_log_source(
        sources,
        seen,
        snapshot_path / ".marianne-observer.jsonl",
        "snapshot observer events",
        job_filter=job_id,
        require_matching_lines=True,
    )
    for path in sorted(snapshot_path.glob("*.log")):
        _append_log_source(sources, seen, path, f"snapshot log: {path.name}")


_ATTEMPT_LOG_RE = re.compile(r"-s(?P<sheet>\d+)-a(?P<attempt>\d+)\.log$")
_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))"
)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _attempt_log_sort_key(path: Path) -> tuple[int, int, str]:
    match = _ATTEMPT_LOG_RE.search(path.name)
    if match:
        return (int(match.group("sheet")), int(match.group("attempt")), path.name)
    return (9999, 9999, path.name)


def _append_interactive_log_sources(
    sources: list[LogSource],
    seen: set[Path],
    job_id: str,
    state: CheckpointState,
) -> None:
    log_dir = Path("~/.marianne/interactive-logs").expanduser()
    if not log_dir.is_dir():
        return

    for path in sorted(log_dir.glob(f"mzt-{job_id}-s*-a*.log"), key=_attempt_log_sort_key):
        _append_log_source(
            sources,
            seen,
            path,
            f"interactive transcript: {path.name}",
            state=state,
            require_relevant=True,
        )


def _append_conductor_log_source(
    sources: list[LogSource],
    seen: set[Path],
    job_id: str,
    state: CheckpointState,
) -> None:
    log_path = Path("~/.marianne/conductor.log").expanduser()
    if not log_path.is_file():
        return

    source = LogSource(
        path=log_path.resolve(),
        label="conductor events",
        follow=state.status == JobStatus.RUNNING,
        job_filter=job_id,
    )
    has_existing_lines = bool(_iter_source_lines(source))
    if not has_existing_lines and (sources or state.status != JobStatus.RUNNING):
        return

    _append_log_source(
        sources,
        seen,
        source.path,
        source.label,
        follow=source.follow,
        job_filter=source.job_filter,
    )


async def _get_job_log_sources(job_id: str, backend: StateBackend) -> list[LogSource]:
    """Get readable log sources for a job.

    Args:
        job_id: Job identifier
        backend: State backend

    Returns:
        Paths to log files or filtered log streams

    Raises:
        HTTPException: If job not found or log file not accessible
    """
    state = await backend.load(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Score not found: {job_id}")

    sources = discover_job_log_sources(
        job_id,
        state,
        registry_metadata=_read_registry_job_metadata(job_id),
    )

    if not sources:
        raise HTTPException(status_code=404, detail=f"Log file not found for job {job_id}")

    return sources


async def _job_status_stream(
    job_id: str,
    backend: StateBackend,
    bridge: DaemonEventBridge | None = None,
    poll_interval: float = _FALLBACK_POLL_INTERVAL,
) -> AsyncIterator[str]:
    """Generate job status updates as SSE stream.

    Uses the ``DaemonEventBridge`` when available for real-time events
    from the conductor's EventBus.  Falls back to polling the state
    backend when no conductor connection is present.
    """
    state = await backend.load(job_id)
    if state is None:
        error_event = SSEEvent(
            event="error",
            data=json.dumps({"error": f"Score not found: {job_id}"}),
            id=f"error-{datetime.now().timestamp()}",
        )
        yield error_event.format()
        return

    if bridge is not None:
        async for event in _job_status_via_bridge(job_id, state, backend, bridge):
            yield event
    else:
        async for event in _job_status_via_poll(job_id, state, backend, poll_interval):
            yield event


async def _job_status_via_bridge(
    job_id: str,
    initial_state: Any,
    backend: StateBackend,
    bridge: DaemonEventBridge,
) -> AsyncIterator[str]:
    """Stream job status updates from the conductor event bus.

    Sends an initial snapshot, then yields SSE events as they arrive
    from ``daemon.monitor.stream``.
    """
    last_signature = _job_status_signature(initial_state)

    yield _job_status_event(job_id, initial_state).format()

    if initial_state.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        final_event = SSEEvent(
            event="job_finished",
            data=json.dumps({"job_id": job_id, "final_status": initial_state.status.value}),
            id=f"finished-{job_id}-{datetime.now().timestamp()}",
        )
        yield final_event.format()
        return

    async for sse_dict in bridge.job_events(job_id):
        event_name = sse_dict.get("event", "")
        if event_name == "bridge_stopped":
            break

        if event_name == "heartbeat":
            yield SSEEvent(
                event="heartbeat",
                data=sse_dict.get("data", "{}"),
                id=f"hb-{datetime.now().timestamp()}",
            ).format()
            continue

        current_state = await backend.load(job_id)
        if current_state is None:
            yield SSEEvent(
                event="job_deleted",
                data=json.dumps({"job_id": job_id}),
                id=f"deleted-{datetime.now().timestamp()}",
            ).format()
            break

        current_signature = _job_status_signature(current_state)

        if current_signature != last_signature:
            yield _job_status_event(job_id, current_state).format()
            last_signature = current_signature

        if current_state.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            final_event = SSEEvent(
                event="job_finished",
                data=json.dumps(
                    {"job_id": job_id, "final_status": current_state.status.value}
                ),
                id=f"finished-{job_id}-{datetime.now().timestamp()}",
            )
            yield final_event.format()
            break


async def _job_status_via_poll(
    job_id: str,
    initial_state: Any,
    backend: StateBackend,
    poll_interval: float,
) -> AsyncIterator[str]:
    """Fallback: poll the state backend for job status changes."""
    last_status = initial_state.status.value
    last_progress = initial_state.get_progress_percent()
    last_update_time = initial_state.updated_at

    try:
        yield _job_status_event(job_id, initial_state).format()

        while True:
            current_state = await backend.load(job_id)
            if current_state is None:
                yield SSEEvent(
                    event="job_deleted",
                    data=json.dumps({"job_id": job_id}),
                    id=f"deleted-{datetime.now().timestamp()}",
                ).format()
                break

            status_changed = last_status != current_state.status.value
            progress_changed = last_progress != current_state.get_progress_percent()
            time_changed = last_update_time != current_state.updated_at

            if status_changed or progress_changed or time_changed:
                yield _job_status_event(job_id, current_state).format()
                last_status = current_state.status.value
                last_progress = current_state.get_progress_percent()
                last_update_time = current_state.updated_at

            if current_state.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                yield SSEEvent(
                    event="job_finished",
                    data=json.dumps({"job_id": job_id, "final_status": current_state.status.value}),
                    id=f"finished-{job_id}-{datetime.now().timestamp()}",
                ).format()
                break

            await asyncio.sleep(poll_interval)

    except asyncio.CancelledError:
        return
    except Exception as e:
        _logger.exception("sse_stream_error", error_type=type(e).__name__, error=str(e))
        yield SSEEvent(
            event="error",
            data=json.dumps({"error": "Internal stream error", "error_type": "StreamError"}),
            id=f"error-{datetime.now().timestamp()}",
        ).format()
        return


def _read_tail_lines(log_file: Path, tail_lines: int) -> tuple[list[str], int]:
    """Read the last N lines from a log file.

    Uses collections.deque with maxlen to avoid loading the entire file into
    memory — only the last N lines are retained during iteration.

    Returns:
        Tuple of (tail lines, total line count in file).
    """
    from collections import deque

    total = 0
    if tail_lines <= 0:
        # Only count lines without retaining any
        with open(log_file, encoding="utf-8", errors="replace") as f:
            total = sum(1 for _ in f)
        return [], total

    with open(log_file, encoding="utf-8", errors="replace") as f:
        tail: deque[str] = deque(maxlen=tail_lines)
        for line in f:
            tail.append(line)
            total += 1

    return list(tail), total


def _iter_source_lines(source: LogSource) -> list[str]:
    """Read source lines, applying a job filter for shared conductor logs."""
    return iter_source_lines(source)


def _read_tail_lines_from_source(source: LogSource, tail_lines: int) -> tuple[list[str], int]:
    """Read the last N relevant lines from a log source."""
    if source.job_filter is None:
        return _read_tail_lines(source.path, tail_lines)

    from collections import deque

    total = 0
    if tail_lines <= 0:
        return [], len(_iter_source_lines(source))

    tail: deque[str] = deque(maxlen=tail_lines)
    for line in _iter_source_lines(source):
        tail.append(line)
        total += 1
    return list(tail), total


def _make_log_event(line: str, line_number: int, is_initial_event: bool, event_id: str) -> str:
    """Create a formatted SSE log event."""
    sanitized_line = _CONTROL_CHARS_RE.sub("", _ANSI_ESCAPE_RE.sub("", line))
    event = SSEEvent(
        event="log",
        data=json.dumps(
            {
                "line": sanitized_line.rstrip("\n") if is_initial_event else sanitized_line,
                "line_number": line_number,
                "timestamp": datetime.now().isoformat(),
                "initial": is_initial_event,
            }
        ),
        id=event_id,
    )
    return event.format()


async def _follow_log_source(
    source: LogSource,
    start_size: int,
    line_count: int,
) -> AsyncIterator[str]:
    """Yield new lines appended to one followable source."""
    last_size = start_size
    while True:
        try:
            if not source.path.exists():
                await asyncio.sleep(1.0)
                continue

            current_size = source.path.stat().st_size
            if current_size > last_size:
                with open(source.path, encoding="utf-8", errors="replace") as f:
                    f.seek(last_size)
                    new_content = f.read()

                for line in new_content.splitlines():
                    if not line:
                        continue
                    if source.job_filter is not None:
                        needle_spaced = f'"job_id": "{source.job_filter}"'
                        needle_compact = f'"job_id":"{source.job_filter}"'
                        transcript_needle = f"mzt-{source.job_filter}-"
                        if (
                            needle_spaced not in line
                            and needle_compact not in line
                            and transcript_needle not in line
                        ):
                            continue

                    line_count += 1
                    yield _make_log_event(
                        line,
                        line_count,
                        is_initial_event=False,
                        event_id=f"log-{line_count}",
                    )

                last_size = current_size

            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            _logger.exception("log_stream_error", error_type=type(e).__name__, error=str(e))
            yield SSEEvent(
                event="error",
                data=json.dumps(
                    {"error": "Internal log streaming error", "error_type": "StreamError"}
                ),
                id=f"error-{datetime.now().timestamp()}",
            ).format()
            break


async def _log_stream_sources(
    sources: list[LogSource], follow: bool = True, tail_lines: int = DEFAULT_TAIL_LINES
) -> AsyncIterator[str]:
    """Stream one or more log sources as a single SSE feed."""
    line_number = 0
    try:
        per_source_tail = tail_lines
        if tail_lines > 0 and len(sources) > 1:
            per_source_tail = max(5, tail_lines // len(sources))

        for source_index, source in enumerate(sources):
            if len(sources) > 1:
                line_number += 1
                yield _make_log_event(
                    f"--- {source.label}: {source.path} ---",
                    line_number,
                    is_initial_event=True,
                    event_id=f"log-source-{source_index}",
                )

            try:
                lines, _ = _read_tail_lines_from_source(source, per_source_tail)
            except (OSError, PermissionError) as e:
                yield SSEEvent(
                    event="error",
                    data=json.dumps({"error": f"Cannot read log file: {e}"}),
                    id=f"error-{datetime.now().timestamp()}",
                ).format()
                return

            for i, line in enumerate(lines):
                line_number += 1
                yield _make_log_event(
                    line,
                    line_number,
                    is_initial_event=True,
                    event_id=f"log-init-{source_index}-{i}",
                )

        if not follow:
            yield SSEEvent(
                event="log_complete",
                data=json.dumps({"message": "Log file streamed completely"}),
                id=f"complete-{datetime.now().timestamp()}",
            ).format()
            return

        follow_source = next((source for source in sources if source.follow), None)
        if follow_source is None:
            yield SSEEvent(
                event="log_complete",
                data=json.dumps({"message": "Static log sources streamed completely"}),
                id=f"complete-{datetime.now().timestamp()}",
            ).format()
            return

        start_size = follow_source.path.stat().st_size if follow_source.path.exists() else 0
        async for event in _follow_log_source(follow_source, start_size, line_number):
            yield event
    except asyncio.CancelledError:
        pass


async def _log_stream(
    log_file: Path, follow: bool = True, tail_lines: int = DEFAULT_TAIL_LINES
) -> AsyncIterator[str]:
    """Stream log file content as SSE.

    Args:
        log_file: Path to log file
        follow: If true, tail the file for new content
        tail_lines: Number of recent lines to send initially

    Yields:
        SSE formatted log lines
    """
    try:
        # Send initial tail of log file
        if log_file.exists():
            try:
                lines, total = _read_tail_lines(log_file, tail_lines)
                start_num = total - len(lines) + 1
                for i, line in enumerate(lines):
                    yield _make_log_event(
                        line,
                        start_num + i,
                        is_initial_event=True,
                        event_id=f"log-init-{i}",
                    )
            except (OSError, PermissionError) as e:
                error_event = SSEEvent(
                    event="error",
                    data=json.dumps({"error": f"Cannot read log file: {e}"}),
                    id=f"error-{datetime.now().timestamp()}",
                )
                yield error_event.format()
                return

        if not follow:
            complete_event = SSEEvent(
                event="log_complete",
                data=json.dumps({"message": "Log file streamed completely"}),
                id=f"complete-{datetime.now().timestamp()}",
            )
            yield complete_event.format()
            return

        # Follow mode - watch for new log lines
        last_size = log_file.stat().st_size if log_file.exists() else 0
        line_count = 0
        if log_file.exists():
            _, line_count = _read_tail_lines(log_file, tail_lines=0)

        while True:
            try:
                if not log_file.exists():
                    await asyncio.sleep(1.0)
                    continue

                current_size = log_file.stat().st_size
                if current_size > last_size:
                    # File has grown, read new content
                    with open(log_file, encoding="utf-8", errors="replace") as f:
                        f.seek(last_size)
                        new_content = f.read()

                    new_lines = new_content.split("\n")
                    # Remove the last empty element if content ends with newline
                    if new_lines and new_lines[-1] == "":
                        new_lines.pop()

                    for line in new_lines:
                        if line:  # Skip empty lines
                            line_count += 1
                            yield _make_log_event(
                                line,
                                line_count,
                                is_initial_event=False,
                                event_id=f"log-{line_count}",
                            )

                    last_size = current_size

                await asyncio.sleep(0.5)  # Poll every 500ms for new content

            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.exception("log_stream_error", error_type=type(e).__name__, error=str(e))
                error_event = SSEEvent(
                    event="error",
                    data=json.dumps(
                        {"error": "Internal log streaming error", "error_type": "StreamError"}
                    ),
                    id=f"error-{datetime.now().timestamp()}",
                )
                yield error_event.format()
                break

    except asyncio.CancelledError:
        # Clean disconnection
        pass


# ============================================================================
# API Endpoints
# ============================================================================


@router.get("/{job_id}/stream")
async def stream_job_status(
    job_id: str,
    poll_interval: float = 2.0,
    backend: StateBackend = Depends(get_state_backend),
) -> StreamingResponse:
    """Stream real-time job status updates via Server-Sent Events.

    Uses the conductor event bridge for real-time updates when available,
    falling back to state-backend polling otherwise.
    """
    if poll_interval < 0.1 or poll_interval > 30.0:
        raise HTTPException(
            status_code=400,
            detail="Poll interval must be between 0.1 and 30.0 seconds",
        )

    state = await backend.load(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Score not found: {job_id}")

    bridge = _get_event_bridge_safe()

    return StreamingResponse(
        _job_status_stream(job_id, backend, bridge, poll_interval),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{job_id}/logs")
async def stream_logs(
    job_id: str,
    follow: bool = True,
    tail_lines: int = DEFAULT_TAIL_LINES,
    backend: StateBackend = Depends(get_state_backend),
) -> StreamingResponse:
    """Stream job logs via Server-Sent Events.

    Args:
        job_id: Job identifier
        follow: If true, tail the log file for new content
        tail_lines: Number of recent lines to send initially (max MAX_TAIL_LINES)
        backend: State backend (injected)

    Returns:
        SSE stream of log lines

    Raises:
        HTTPException: 404 if job/logs not found, 400 if invalid parameters
    """
    if tail_lines < 0 or tail_lines > MAX_TAIL_LINES:
        raise HTTPException(
            status_code=400, detail=f"tail_lines must be between 0 and {MAX_TAIL_LINES}"
        )

    sources = await _get_job_log_sources(job_id, backend)

    return StreamingResponse(
        _log_stream_sources(sources, follow, tail_lines),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable proxy buffering
        },
    )


@router.get("/{job_id}/logs/static")
async def download_logs(
    job_id: str,
    backend: StateBackend = Depends(get_state_backend),
) -> Response:
    """Download complete log file as plain text.

    Args:
        job_id: Job identifier
        backend: State backend (injected)

    Returns:
        Complete log file content as text/plain

    Raises:
        HTTPException: 404 if job/logs not found
    """
    sources = await _get_job_log_sources(job_id, backend)

    # Guard against unbounded memory usage on large log files (50 MB limit)
    try:
        file_size = sum(source.path.stat().st_size for source in sources)
    except OSError as e:
        raise HTTPException(status_code=403, detail=f"Cannot access log file: {e}") from e

    if file_size > MAX_STATIC_LOG_DOWNLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Log file too large for download: "
                f"{file_size / (1024 * 1024):.1f} MB "
                f"(limit {MAX_STATIC_LOG_DOWNLOAD_BYTES // (1024 * 1024)} MB). "
                f"Use the streaming endpoint instead."
            ),
        )

    try:
        chunks: list[str] = []
        for source in sources:
            chunks.append(f"# Source: {source.label} ({source.path})\n")
            chunks.extend(_iter_source_lines(source))
            if chunks and not chunks[-1].endswith("\n"):
                chunks.append("\n")
            chunks.append("\n")
        content = "".join(chunks)

        # Add informational header
        lines = content.count("\n")
        size_kb = len(content.encode("utf-8")) / 1024

        header = f"# Marianne Job Logs - Job ID: {job_id}\n"
        header += f"# Generated: {datetime.now().isoformat()}\n"
        header += f"# Sources: {len(sources)}\n"
        header += f"# Size: {size_kb:.1f} KB, {lines} lines\n"
        header += "#" + "=" * 60 + "\n\n"

        return PlainTextResponse(
            content=header + content,
            headers={
                "Content-Disposition": f"attachment; filename=marianne-{job_id}-logs.txt",
                "Content-Type": "text/plain; charset=utf-8",
            },
        )

    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=403, detail=f"Cannot read log file: {e}") from e


@router.get("/{job_id}/logs/info", response_model=LogDownloadInfo)
async def get_log_info(
    job_id: str,
    backend: StateBackend = Depends(get_state_backend),
) -> LogDownloadInfo:
    """Get information about job log file.

    Args:
        job_id: Job identifier
        backend: State backend (injected)

    Returns:
        Log file information

    Raises:
        HTTPException: 404 if job/logs not found
    """
    sources = await _get_job_log_sources(job_id, backend)

    try:
        stats = [source.path.stat() for source in sources]
        lines = 0
        for source in sources:
            _, source_lines = _read_tail_lines_from_source(source, tail_lines=0)
            lines += source_lines

        newest_mtime = max(stat.st_mtime for stat in stats)
        log_file_name = sources[0].path.name if len(sources) == 1 else "combined log sources"

        size_bytes = sum(stat.st_size for stat in stats)

        return LogDownloadInfo(
            job_id=job_id,
            log_file=log_file_name,
            size_bytes=size_bytes,
            lines=lines,
            last_modified=datetime.fromtimestamp(newest_mtime),
            sources=[f"{source.label}: {source.path}" for source in sources],
            download_available=size_bytes <= MAX_STATIC_LOG_DOWNLOAD_BYTES,
            download_limit_bytes=MAX_STATIC_LOG_DOWNLOAD_BYTES,
        )

    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=403, detail=f"Cannot access log file: {e}") from e
