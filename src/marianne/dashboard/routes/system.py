"""System health API endpoints.

Exposes live daemon system health data via ``DaemonSystemView`` as JSON
endpoints for the dashboard monitor page.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from marianne.dashboard.services.system_view import DaemonSystemView

router = APIRouter(prefix="/api/system", tags=["System"])

# ------------------------------------------------------------------
# Module-level system view instance (set by app.py on startup)
# ------------------------------------------------------------------

_system_view: DaemonSystemView | None = None


def get_system_view() -> DaemonSystemView:
    """Return the module-level system view instance.

    Raises ``RuntimeError`` if not yet configured.
    """
    if _system_view is None:
        raise RuntimeError(
            "DaemonSystemView not configured. "
            "Call set_system_view() before serving requests."
        )
    return _system_view


def set_system_view(view: DaemonSystemView | None) -> None:
    """Configure the module-level system view (called from app.py)."""
    global _system_view
    _system_view = view


def _system_unavailable(resource: str) -> dict[str, Any]:
    return {
        "connected": False,
        "state": "unavailable",
        "message": f"{resource} unavailable: dashboard is not connected to the conductor",
    }


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/rate-limits")
async def system_rate_limits() -> dict[str, Any]:
    """Current rate limit state per backend."""
    try:
        return await get_system_view().rate_limit_state()
    except RuntimeError:
        return {
            **_system_unavailable("Rate limit state"),
            "backends": {},
            "active_limits": 0,
        }


@router.get("/pressure")
async def system_pressure() -> dict[str, Any]:
    """Backpressure level from latest system snapshot."""
    try:
        return await get_system_view().pressure_level()
    except RuntimeError:
        return {
            **_system_unavailable("System pressure"),
            "level": "unavailable",
            "color": "gray",
        }


@router.get("/learning")
async def system_learning(
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]] | dict[str, Any]:
    """Recent learning insights from the daemon."""
    try:
        return await get_system_view().learning_patterns(limit=limit)
    except RuntimeError:
        return {
            **_system_unavailable("Learning patterns"),
            "patterns": [],
        }
