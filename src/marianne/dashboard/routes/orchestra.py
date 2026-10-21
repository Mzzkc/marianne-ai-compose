"""Orchestra view routes (GH #468): the fleet, watchable live and replayable.

Everything served here is the conductor's public-safe projection: score
names, sheet numbers, instruments, outcomes and timestamps. No workspace,
prompt, path or error text ever passes through this router, so the page can
be put behind a public hostname without exposing the venue.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from marianne.dashboard.app import get_templates
from marianne.dashboard.services.orchestra_view import OrchestraView

router = APIRouter(tags=["Orchestra"])

_view: OrchestraView | None = None


def set_orchestra_view(view: OrchestraView | None) -> None:
    global _view
    _view = view


def _unavailable() -> dict[str, Any]:
    return {"enabled": False, "jobs": [], "recent": [], "message": "conductor not connected"}


@router.get("/orchestra", response_class=HTMLResponse)
async def orchestra_page(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    """The orchestra: every score on stage, live, or a day replayed."""
    return templates.TemplateResponse(request, "pages/orchestra.html", {"request": request})


@router.get("/api/orchestra/snapshot")
async def orchestra_snapshot(limit: int = Query(default=200, ge=1, le=2000)) -> dict[str, Any]:
    if _view is None:
        return _unavailable()
    return await _view.snapshot(limit=limit) or _unavailable()


@router.get("/api/orchestra/replay")
async def orchestra_replay(
    day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int | None = Query(default=None, ge=1, le=100_000),
) -> dict[str, Any]:
    if _view is None:
        return {"enabled": False, "day": day, "events": []}
    return await _view.replay(day, limit) or {"enabled": False, "day": day, "events": []}


async def _sse(view: OrchestraView) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    async for beat in view.beats():
        yield f"event: beat\ndata: {json.dumps(beat, separators=(',', ':'))}\n\n"


@router.get("/api/orchestra/stream")
async def orchestra_stream() -> StreamingResponse:
    if _view is None:
        return StreamingResponse(iter(["event: end\ndata: {}\n\n"]), media_type="text/event-stream")
    return StreamingResponse(
        _sse(_view),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
