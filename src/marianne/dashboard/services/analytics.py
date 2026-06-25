"""Aggregated statistics engine for the Marianne Dashboard.

Computes dashboard-wide analytics from daemon job data via
``DaemonStateAdapter``.  All methods use a TTL-based cache to avoid
hammering IPC on concurrent page loads.

Aggregation logic lives in module-level pure functions so it can be
tested independently of the cache and IPC layers.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

from marianne.core.checkpoint import CheckpointState, SheetStatus
from marianne.core.constants import SHEET_NUM_KEY
from marianne.state.base import StateBackend

# ------------------------------------------------------------------
# Pure aggregation functions
# ------------------------------------------------------------------


def _aggregate_stats(jobs: list[CheckpointState]) -> dict[str, Any]:
    counts: dict[str, int] = {
        "running": 0,
        "completed": 0,
        "failed": 0,
        "paused": 0,
        "cancelled": 0,
        "pending": 0,
    }
    total_spend = 0.0
    total_completed_sheets = 0
    total_duration_hours = 0.0

    for job in jobs:
        status_val = job.status.value if hasattr(job.status, "value") else str(job.status)
        if status_val in counts:
            counts[status_val] += 1

        total_spend += job.total_estimated_cost or 0.0

        for sheet in job.sheets.values():
            if sheet.status == SheetStatus.COMPLETED:
                total_completed_sheets += 1
                if sheet.execution_duration_seconds:
                    total_duration_hours += sheet.execution_duration_seconds / 3600.0

    terminal = counts["completed"] + counts["failed"]
    success_rate = (counts["completed"] / terminal * 100.0) if terminal > 0 else 0.0
    throughput = total_completed_sheets / total_duration_hours if total_duration_hours > 0 else 0.0

    return {
        "total_jobs": len(jobs),
        "running_jobs": counts["running"],
        "completed_jobs": counts["completed"],
        "failed_jobs": counts["failed"],
        "paused_jobs": counts["paused"],
        "cancelled_jobs": counts["cancelled"],
        "pending_jobs": counts["pending"],
        "success_rate": round(success_rate, 1),
        "total_spend": round(total_spend, 4),
        "throughput_sheets_per_hour": round(throughput, 2),
    }


def _aggregate_costs(jobs: list[CheckpointState]) -> dict[str, Any]:
    by_job: dict[str, float] = {}
    total_spend = 0.0

    for job in jobs:
        cost = job.total_estimated_cost or 0.0
        by_job[job.job_id] = round(cost, 4)
        total_spend += cost

    jobs_with_cost = [j for j in jobs if (j.total_estimated_cost or 0.0) > 0]
    avg_cost = (total_spend / len(jobs_with_cost)) if jobs_with_cost else 0.0

    return {
        "by_job": by_job,
        "total_spend": round(total_spend, 4),
        "avg_cost_per_job": round(avg_cost, 4),
        "jobs_with_cost": len(jobs_with_cost),
    }


def _aggregate_validations(jobs: list[CheckpointState]) -> dict[str, Any]:
    by_rule: dict[str, dict[str, int]] = {}
    total_checks = 0
    total_passed = 0

    for job in jobs:
        for sheet in job.sheets.values():
            if not sheet.validation_details:
                continue
            for detail in sheet.validation_details:
                total_checks += 1
                passed = detail.get("passed", False)
                if passed:
                    total_passed += 1

                rule_type = detail.get("rule_type", "unknown")
                if rule_type not in by_rule:
                    by_rule[rule_type] = {"passed": 0, "total": 0}
                by_rule[rule_type]["total"] += 1
                if passed:
                    by_rule[rule_type]["passed"] += 1

    by_rule_rates: dict[str, float] = {}
    for rule_type, counts in by_rule.items():
        if counts["total"] > 0:
            by_rule_rates[rule_type] = round(
                counts["passed"] / counts["total"] * 100.0,
                1,
            )
        else:
            by_rule_rates[rule_type] = 0.0

    overall_rate = round(total_passed / total_checks * 100.0, 1) if total_checks > 0 else 0.0

    return {
        "by_rule_type": by_rule_rates,
        "overall_pass_rate": overall_rate,
        "total_checks": total_checks,
        "total_validations": total_checks,
        "total_passed": total_passed,
    }


def _aggregate_errors(jobs: list[CheckpointState]) -> dict[str, Any]:
    categories: dict[str, int] = {
        "transient": 0,
        "rate_limit": 0,
        "permanent": 0,
    }
    total_errors = 0

    for job in jobs:
        for sheet in job.sheets.values():
            for error in sheet.error_history:
                total_errors += 1
                error_type = error.error_type
                if error_type in categories:
                    categories[error_type] += 1
                else:
                    categories["permanent"] += 1

    return {
        "by_category": categories,
        "total_errors": total_errors,
    }


def _aggregate_durations(jobs: list[CheckpointState]) -> dict[str, Any]:
    all_durations: list[float] = []
    slowest: list[dict[str, Any]] = []
    job_durations: dict[str, float] = {}

    for job in jobs:
        job_total = 0.0
        for sheet in job.sheets.values():
            dur = sheet.execution_duration_seconds
            if dur is not None and dur > 0:
                all_durations.append(dur)
                job_total += dur
                slowest.append(
                    {
                        "job_id": job.job_id,
                        SHEET_NUM_KEY: sheet.sheet_num,
                        "duration_seconds": round(dur, 2),
                    }
                )
        if job_total > 0:
            job_durations[job.job_id] = round(job_total, 2)

    avg_duration = round(sum(all_durations) / len(all_durations), 2) if all_durations else 0.0
    median_duration = 0.0
    if all_durations:
        ordered = sorted(all_durations)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            median_duration = ordered[mid]
        else:
            median_duration = (ordered[mid - 1] + ordered[mid]) / 2.0

    slowest.sort(key=lambda s: s["duration_seconds"], reverse=True)

    return {
        "avg_sheet_duration_seconds": avg_duration,
        "median_sheet_duration_seconds": round(median_duration, 2),
        "job_durations": job_durations,
        "slowest_sheets": slowest[:10],
        "total_sheets_with_duration": len(all_durations),
    }


# ------------------------------------------------------------------
# DaemonAnalytics — cache-aware facade over pure aggregators
# ------------------------------------------------------------------


class DaemonAnalytics:
    """Compute aggregate statistics from daemon job data.

    Parameters
    ----------
    adapter:
        A ``StateBackend`` (typically ``DaemonStateAdapter``) used to
        fetch live job data.
    cache_ttl:
        Default time-to-live in seconds for cached results (default 10.0).
    """

    def __init__(self, adapter: StateBackend, cache_ttl: float = 10.0) -> None:
        self._adapter = adapter
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_stats(self) -> dict[str, Any]:
        """Dashboard stats: total, running, completed, failed, success_rate,
        total_spend, throughput_sheets_per_hour.
        """
        return await self._compute("stats", _aggregate_stats)

    async def cost_rollup(self) -> dict[str, Any]:
        """Cost breakdown by job, total spend, avg cost per job."""
        return await self._compute("cost_rollup", _aggregate_costs)

    async def validation_stats(self) -> dict[str, Any]:
        """Validation pass rates by rule type, overall pass rate."""
        return await self._compute("validation_stats", _aggregate_validations)

    async def error_breakdown(self) -> dict[str, Any]:
        """Error counts by category: transient, rate_limit, permanent."""
        return await self._compute("error_breakdown", _aggregate_errors)

    async def duration_stats(self) -> dict[str, Any]:
        """Avg sheet duration, total job durations, slowest sheets."""
        return await self._compute("duration_stats", _aggregate_durations)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _compute(
        self,
        cache_key: str,
        aggregator: Callable[[list[CheckpointState]], dict[str, Any]],
    ) -> dict[str, Any]:
        cached = self._cached(cache_key)
        if cached is not None:
            return cast("dict[str, Any]", cached)

        jobs = await self._list_jobs_cached()
        result = aggregator(jobs)
        self._set_cache(cache_key, result)
        return result

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cached(self, key: str, ttl: float | None = None) -> Any | None:
        """Return cached value if not expired."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        cached_at, value = entry
        effective_ttl = ttl if ttl is not None else self._cache_ttl
        if time.monotonic() - cached_at > effective_ttl:
            del self._cache[key]
            return None
        return value

    def _set_cache(self, key: str, value: Any) -> None:
        """Store a value in the cache with the current timestamp."""
        self._cache[key] = (time.monotonic(), value)

    async def _list_jobs_cached(self) -> list[CheckpointState]:
        """Fetch jobs from the adapter, using cache to avoid repeated IPC calls."""
        cached = self._cached("_jobs")
        if cached is not None:
            return cast("list[CheckpointState]", cached)
        jobs = await self._adapter.list_jobs()
        self._set_cache("_jobs", jobs)
        return jobs


__all__ = ["DaemonAnalytics"]
