"""Analytics API endpoints.

Exposes aggregated statistics computed by ``DaemonAnalytics`` as JSON
endpoints for the dashboard analytics page.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from mozart.dashboard.services.analytics import DaemonAnalytics

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

# ------------------------------------------------------------------
# Module-level analytics instance (set by app.py on startup)
# ------------------------------------------------------------------

_analytics: DaemonAnalytics | None = None


def get_analytics() -> DaemonAnalytics:
    """Return the module-level analytics instance.

    Raises ``RuntimeError`` if not yet configured.
    """
    if _analytics is None:
        raise RuntimeError(
            "DaemonAnalytics not configured. "
            "Call set_analytics() before serving requests."
        )
    return _analytics


def set_analytics(analytics: DaemonAnalytics) -> None:
    """Configure the module-level analytics instance (called from app.py)."""
    global _analytics
    _analytics = analytics


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/stats")
async def analytics_stats() -> dict[str, Any]:
    """Full dashboard stats: job counts, success rate, spend, throughput."""
    return await get_analytics().get_stats()


@router.get("/costs")
async def analytics_costs() -> dict[str, Any]:
    """Cost rollup: breakdown by job, total spend, avg cost per job."""
    return await get_analytics().cost_rollup()


@router.get("/validations")
async def analytics_validations() -> dict[str, Any]:
    """Validation stats: pass rates by rule type, overall pass rate."""
    return await get_analytics().validation_stats()


@router.get("/errors")
async def analytics_errors() -> dict[str, Any]:
    """Error breakdown: counts by category (transient, rate_limit, permanent)."""
    return await get_analytics().error_breakdown()
