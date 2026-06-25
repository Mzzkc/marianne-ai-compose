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


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/rate-limits")
async def system_rate_limits() -> dict[str, Any]:
    """Current rate limit state per backend."""
    return await get_system_view().rate_limit_state()


@router.get("/pressure")
async def system_pressure() -> dict[str, Any]:
    """Backpressure level from latest system snapshot."""
    return await get_system_view().pressure_level()


@router.get("/learning")
async def system_learning(
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Recent learning insights from the daemon."""
    return await get_system_view().learning_patterns(limit=limit)
