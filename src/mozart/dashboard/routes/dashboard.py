"""Dashboard overview API endpoints.

Provides aggregate statistics and recent activity data for the
dashboard landing page.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from mozart.core.checkpoint import JobStatus
from mozart.dashboard.app import get_state_backend, get_templates
from mozart.state.base import StateBackend

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


# ============================================================================
# Response Models
# ============================================================================


class DashboardStats(BaseModel):
    """Aggregate job statistics for the overview page."""

    total_jobs: int
    running_jobs: int
    completed_jobs: int
    failed_jobs: int
    paused_jobs: int
    cancelled_jobs: int
    success_rate: float  # completed / (completed + failed), 0.0 if no terminal jobs


class RecentJob(BaseModel):
    """Lightweight job summary for the recent activity list."""

    job_id: str
    job_name: str
    status: JobStatus
    progress_percent: float
    updated_at: datetime


# ============================================================================
# API Endpoints
# ============================================================================


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    backend: StateBackend = Depends(get_state_backend),
) -> DashboardStats:
    """Get aggregate job statistics for the dashboard overview.

    When ``DaemonAnalytics`` is configured (daemon mode), delegates to it
    for richer stats with caching.  Falls back to manual counting from
    the state backend when analytics is unavailable (test / local mode).
    """
    # Try DaemonAnalytics first (set by app.py when daemon is available)
    try:
        from mozart.dashboard.routes.analytics import get_analytics
        analytics = get_analytics()
        data = await analytics.get_stats()
        return DashboardStats(
            total_jobs=data.get("total_jobs", 0),
            running_jobs=data.get("running_jobs", 0),
            completed_jobs=data.get("completed_jobs", 0),
            failed_jobs=data.get("failed_jobs", 0),
            paused_jobs=data.get("paused_jobs", 0),
            cancelled_jobs=data.get("cancelled_jobs", 0),
            success_rate=data.get("success_rate", 0.0),
        )
    except RuntimeError:
        pass  # Analytics not configured — fall through to manual counting

    all_jobs = await backend.list_jobs()

    counts: dict[str, int] = {
        "running": 0,
        "completed": 0,
        "failed": 0,
        "paused": 0,
        "cancelled": 0,
    }
    for job in all_jobs:
        status_val = job.status.value if hasattr(job.status, "value") else str(job.status)
        if status_val in counts:
            counts[status_val] += 1

    terminal = counts["completed"] + counts["failed"]
    success_rate = (counts["completed"] / terminal * 100.0) if terminal > 0 else 0.0

    return DashboardStats(
        total_jobs=len(all_jobs),
        running_jobs=counts["running"],
        completed_jobs=counts["completed"],
        failed_jobs=counts["failed"],
        paused_jobs=counts["paused"],
        cancelled_jobs=counts["cancelled"],
        success_rate=round(success_rate, 1),
    )


@router.get("/recent", response_model=list[RecentJob])
async def get_recent_jobs(
    limit: int = 10,
    backend: StateBackend = Depends(get_state_backend),
) -> list[RecentJob]:
    """Get the most recently updated jobs.

    Returns up to ``limit`` jobs sorted by updated_at descending.
    """
    all_jobs = await backend.list_jobs()

    # Sort by updated_at descending (most recent first)
    sorted_jobs = sorted(all_jobs, key=lambda j: j.updated_at, reverse=True)

    return [
        RecentJob(
            job_id=job.job_id,
            job_name=job.job_name,
            status=job.status,
            progress_percent=job.get_progress_percent(),
            updated_at=job.updated_at,
        )
        for job in sorted_jobs[:limit]
    ]


@router.get("/stats/partial", response_class=HTMLResponse)
async def dashboard_stats_partial(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
    backend: StateBackend = Depends(get_state_backend),
) -> HTMLResponse:
    """Render the dashboard stats cards as an HTML partial (htmx target)."""
    stats = await get_dashboard_stats(backend)
    return templates.TemplateResponse(
        "partials/dashboard_stats.html",
        {"request": request, "stats": stats},
    )


@router.get("/recent/partial", response_class=HTMLResponse)
async def dashboard_recent_partial(
    request: Request,
    limit: int = 10,
    templates: Jinja2Templates = Depends(get_templates),
    backend: StateBackend = Depends(get_state_backend),
) -> HTMLResponse:
    """Render the recent activity list as an HTML partial (htmx target)."""
    recent = await get_recent_jobs(limit=limit, backend=backend)
    return templates.TemplateResponse(
        "partials/recent_activity.html",
        {"request": request, "recent_jobs": recent},
    )


@router.get("/system/partial", response_class=HTMLResponse)
async def dashboard_system_partial(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    """Render system resource metrics as an HTML partial (htmx target).

    When ``DaemonSystemView`` is configured, fetches a live snapshot via
    IPC.  Falls back to reading ``monitor.db`` directly when the daemon
    service layer isn't wired (test / local mode).
    """
    snapshot_data: dict[str, object] | None = None

    # Try DaemonSystemView first (set by app.py when daemon is available)
    try:
        from mozart.dashboard.routes.system import get_system_view
        system_view = get_system_view()
        snap_dict = await system_view.get_snapshot()
        if snap_dict is not None:
            mem_total = snap_dict.get("system_memory_total_mb", 0) or 1
            mem_used = snap_dict.get("system_memory_used_mb", 0)
            mem_pct = min((mem_used / mem_total) * 100, 100) if mem_total else 0
            snapshot_data = {
                "mem_used_mb": round(mem_used),
                "mem_total_mb": round(mem_total),
                "mem_pct": round(mem_pct, 1),
                "load_1m": round(snap_dict.get("system_load_1m", 0), 2),
                "process_count": snap_dict.get("mozart_process_count", 0),
                "pressure": snap_dict.get("pressure_level", "none") or "none",
            }
    except RuntimeError:
        pass  # SystemView not configured — fall through to file-based path
    except Exception:
        _logger.debug("dashboard.system_partial.daemon_error", exc_info=True)

    # Fallback: read from monitor.db directly
    if snapshot_data is None:
        db_path = Path("~/.mozart/monitor.db").expanduser()
        if db_path.exists():
            try:
                from mozart.dashboard.routes.monitor import get_monitor_storage

                storage = await get_monitor_storage(db_path)
                snapshots = await storage.read_snapshots(since=time.time() - 60, limit=1)
                if snapshots:
                    snap = snapshots[-1]
                    mem_total = getattr(snap, "system_memory_total_mb", 0) or 1
                    mem_used = getattr(snap, "system_memory_used_mb", 0)
                    mem_pct = min((mem_used / mem_total) * 100, 100)
                    snapshot_data = {
                        "mem_used_mb": round(mem_used),
                        "mem_total_mb": round(mem_total),
                        "mem_pct": round(mem_pct, 1),
                        "load_1m": round(getattr(snap, "system_load_1m", 0), 2),
                        "process_count": getattr(snap, "mozart_process_count", 0),
                        "pressure": getattr(snap, "pressure_level", "none") or "none",
                    }
            except Exception:
                _logger.debug("dashboard.system_partial.read_error", exc_info=True)

    return templates.TemplateResponse(
        "partials/system_resources.html",
        {"request": request, "snapshot": snapshot_data},
    )
