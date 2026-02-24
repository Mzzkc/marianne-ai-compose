"""Event stream API endpoints.

Provides SSE and JSON endpoints backed by ``DaemonEventBridge``.
Router registration happens in ``app.py`` (stage 6).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from mozart.dashboard.services.event_bridge import DaemonEventBridge

router = APIRouter(tags=["Events"])


def _format_sse(event: dict[str, Any]) -> str:
    """Format a dict with ``event`` and ``data`` keys as an SSE frame."""
    lines: list[str] = []
    lines.append(f"event: {event.get('event', 'message')}")
    data = event.get("data", "{}")
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    lines.append("")  # trailing blank line
    return "\n".join(lines) + "\n"


async def _sse_generator(
    bridge: DaemonEventBridge,
    limit: int,
) -> AsyncIterator[str]:
    """Wrap ``bridge.all_events`` into SSE wire format."""
    async for evt in bridge.all_events(limit=limit):
        yield _format_sse(evt)


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

_event_bridge: DaemonEventBridge | None = None


def get_event_bridge() -> DaemonEventBridge:
    """Return the module-level event bridge instance.

    Raises ``RuntimeError`` if not yet configured (bridge is set when
    the router is registered in ``app.py`` — stage 6).
    """
    if _event_bridge is None:
        raise RuntimeError(
            "DaemonEventBridge not configured. "
            "Set event_bridge via set_event_bridge() before serving requests."
        )
    return _event_bridge


def set_event_bridge(bridge: DaemonEventBridge) -> None:
    """Configure the module-level event bridge (called from app.py)."""
    global _event_bridge
    _event_bridge = bridge


@router.get("/api/events/stream")
async def stream_all_events(
    limit: int = Query(default=50, ge=1, le=200),
) -> StreamingResponse:
    """SSE stream of events across all active jobs.

    Used by the global event timeline on the dashboard index page.
    """
    bridge = get_event_bridge()
    return StreamingResponse(
        _sse_generator(bridge, limit),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/jobs/{job_id}/observer")
async def get_observer_events(
    job_id: str,
    limit: int = Query(default=20, ge=1, le=200),
) -> list[dict[str, Any]]:
    """JSON endpoint returning recent observer events for a job."""
    bridge = get_event_bridge()
    events = await bridge.observer_events(job_id, limit=limit)
    return events
