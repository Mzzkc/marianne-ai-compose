"""Server-Sent Events (SSE) streaming API endpoints.

Job status streams use the ``DaemonEventBridge`` when a conductor is
available (real-time via ``daemon.monitor.stream``), falling back to
state-backend polling otherwise.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from marianne.core.checkpoint import JobStatus
from marianne.core.logging import get_logger
from marianne.dashboard.app import get_state_backend
from marianne.dashboard.routes import resolve_job_workspace
from marianne.dashboard.services.event_bridge import DaemonEventBridge
from marianne.dashboard.services.sse_manager import SSEEvent, SSEManager
from marianne.state.base import StateBackend

_logger = get_logger("dashboard.stream")

# Tail line count constants for log streaming
DEFAULT_TAIL_LINES: int = 100
MAX_TAIL_LINES: int = 1000
_FALLBACK_POLL_INTERVAL: float = 2.0

router = APIRouter(prefix="/api/jobs", tags=["Streaming"])


# ============================================================================
# Module-level SSE manager instance
# ============================================================================

# Global SSE manager for the application
_sse_manager: SSEManager | None = None


def get_sse_manager() -> SSEManager:
    """Get the SSE manager instance."""
    global _sse_manager
    if _sse_manager is None:
        _sse_manager = SSEManager()
    return _sse_manager


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


# ============================================================================
# Helper Functions
# ============================================================================


async def _get_job_log_file(job_id: str, backend: StateBackend) -> Path:
    """Get the log file path for a job.

    Args:
        job_id: Job identifier
        backend: State backend

    Returns:
        Path to log file

    Raises:
        HTTPException: If job not found or log file not accessible
    """
    state = await backend.load(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Score not found: {job_id}")

    # Determine workspace path for log file location
    workspace = resolve_job_workspace(state, job_id)

    # Standard Marianne log file location
    log_file = workspace / "marianne.log"

    if not log_file.exists():
        raise HTTPException(status_code=404, detail=f"Log file not found: {log_file}")

    return log_file


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
    last_status = initial_state.status.value
    last_progress = initial_state.get_progress_percent()

    completed, total = initial_state.get_progress()
    snapshot = SSEEvent(
        event="job_status",
        data=json.dumps(
            {
                "job_id": job_id,
                "status": last_status,
                "progress_percent": last_progress,
                "completed_sheets": completed,
                "total_sheets": total,
                "current_sheet": initial_state.current_sheet,
                "error_message": initial_state.error_message,
                "updated_at": initial_state.updated_at.isoformat()
                if initial_state.updated_at
                else None,
            }
        ),
        id=f"status-{job_id}-{datetime.now().timestamp()}",
    )
    yield snapshot.format()

    if initial_state.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        final_event = SSEEvent(
            event="job_finished",
            data=json.dumps({"job_id": job_id, "final_status": last_status}),
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

        status = current_state.status.value
        progress = current_state.get_progress_percent()

        if status != last_status or progress != last_progress:
            completed, total = current_state.get_progress()
            status_event = SSEEvent(
                event="job_status",
                data=json.dumps(
                    {
                        "job_id": job_id,
                        "status": status,
                        "progress_percent": progress,
                        "completed_sheets": completed,
                        "total_sheets": total,
                        "current_sheet": current_state.current_sheet,
                        "error_message": current_state.error_message,
                        "updated_at": current_state.updated_at.isoformat()
                        if current_state.updated_at
                        else None,
                    }
                ),
                id=f"status-{job_id}-{datetime.now().timestamp()}",
            )
            yield status_event.format()
            last_status = status
            last_progress = progress

        if current_state.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            final_event = SSEEvent(
                event="job_finished",
                data=json.dumps({"job_id": job_id, "final_status": status}),
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
                completed, total = current_state.get_progress()
                status_data = {
                    "job_id": job_id,
                    "status": current_state.status.value,
                    "progress_percent": current_state.get_progress_percent(),
                    "completed_sheets": completed,
                    "total_sheets": total,
                    "current_sheet": current_state.current_sheet,
                    "error_message": current_state.error_message,
                    "updated_at": current_state.updated_at.isoformat()
                    if current_state.updated_at
                    else None,
                }
                yield SSEEvent(
                    event="job_status",
                    data=json.dumps(status_data),
                    id=f"status-{job_id}-{datetime.now().timestamp()}",
                ).format()
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

    last_status = None
    last_progress = None
    last_update_time = None

    try:
        while True:
            # Reload state for current status
            current_state = await backend.load(job_id)
            if current_state is None:
                # Job was deleted
                deleted_event = SSEEvent(
                    event="job_deleted",
                    data=json.dumps({"job_id": job_id}),
                    id=f"deleted-{datetime.now().timestamp()}",
                )
                yield deleted_event.format()
                break

            # Check for changes
            status_changed = last_status != current_state.status.value
            progress_changed = last_progress != current_state.get_progress_percent()
            time_changed = last_update_time != current_state.updated_at

            if status_changed or progress_changed or time_changed:
                # Send status update
                completed, total = current_state.get_progress()
                status_data = {
                    "job_id": job_id,
                    "status": current_state.status.value,
                    "progress_percent": current_state.get_progress_percent(),
                    "completed_sheets": completed,
                    "total_sheets": total,
                    "current_sheet": current_state.current_sheet,
                    "error_message": current_state.error_message,
                    "updated_at": current_state.updated_at.isoformat(),
                }

                event = SSEEvent(
                    event="job_status",
                    data=json.dumps(status_data),
                    id=f"status-{job_id}-{datetime.now().timestamp()}",
                )
                yield event.format()

                # Update tracking variables
                last_status = current_state.status.value
                last_progress = current_state.get_progress_percent()
                last_update_time = current_state.updated_at

            # Check if job is finished
            if current_state.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                final_event = SSEEvent(
                    event="job_finished",
                    data=json.dumps({"job_id": job_id, "final_status": current_state.status.value}),
                    id=f"finished-{job_id}-{datetime.now().timestamp()}",
                )
                yield final_event.format()
                break

            # Wait before next poll
            await asyncio.sleep(poll_interval)

    except asyncio.CancelledError:
        # Client disconnected — nothing to yield since the consumer is gone.
        return
    except Exception as e:
        # Unexpected error — do not leak internal exception details to clients
        _logger.exception("sse_stream_error", error_type=type(e).__name__, error=str(e))
        error_event = SSEEvent(
            event="error",
            data=json.dumps({"error": "Internal stream error", "error_type": "StreamError"}),
            id=f"error-{datetime.now().timestamp()}",
        )
        yield error_event.format()


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


def _make_log_event(line: str, line_number: int, is_initial_event: bool, event_id: str) -> str:
    """Create a formatted SSE log event."""
    event = SSEEvent(
        event="log",
        data=json.dumps(
            {
                "line": line.rstrip("\n") if is_initial_event else line,
                "line_number": line_number,
                "timestamp": datetime.now().isoformat(),
                "initial": is_initial_event,
            }
        ),
        id=event_id,
    )
    return event.format()


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

    log_file = await _get_job_log_file(job_id, backend)

    return StreamingResponse(
        _log_stream(log_file, follow, tail_lines),
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
    log_file = await _get_job_log_file(job_id, backend)

    # Guard against unbounded memory usage on large log files (50 MB limit)
    max_download_bytes = 50 * 1024 * 1024
    try:
        file_size = log_file.stat().st_size
    except OSError as e:
        raise HTTPException(status_code=403, detail=f"Cannot access log file: {e}") from e

    if file_size > max_download_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Log file too large for download: "
                f"{file_size / (1024 * 1024):.1f} MB "
                f"(limit {max_download_bytes // (1024 * 1024)} MB). "
                f"Use the streaming endpoint instead."
            ),
        )

    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")

        # Add informational header
        lines = content.count("\n")
        size_kb = len(content.encode("utf-8")) / 1024

        header = f"# Marianne Job Logs - Job ID: {job_id}\n"
        header += f"# Generated: {datetime.now().isoformat()}\n"
        header += f"# File: {log_file.name}\n"
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
    log_file = await _get_job_log_file(job_id, backend)

    try:
        stat = log_file.stat()

        # Count lines efficiently
        with open(log_file, "rb") as f:
            lines = sum(1 for _ in f)

        return LogDownloadInfo(
            job_id=job_id,
            log_file=log_file.name,
            size_bytes=stat.st_size,
            lines=lines,
            last_modified=datetime.fromtimestamp(stat.st_mtime),
        )

    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=403, detail=f"Cannot access log file: {e}") from e
