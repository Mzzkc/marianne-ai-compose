"""Resource monitoring for the Marianne daemon.

Periodic background checks on memory, child process count, and zombie
detection.  Emits structured log warnings when limits are approached and
triggers backpressure (e.g. job cancellation) when hard limits are exceeded.

Delegates system probing to ``SystemProbe`` (system_probe.py) which
consolidates the psutil / /proc fallback pattern in one place.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marianne.core.logging import get_logger
from marianne.daemon.config import ResourceLimitConfig
from marianne.daemon.system_probe import SystemProbe
from marianne.daemon.task_utils import log_task_exception

if TYPE_CHECKING:
    from marianne.daemon.manager import JobManager
    from marianne.daemon.pgroup import ProcessGroupManager

_logger = get_logger("daemon.monitor")


@dataclass
class ResourceSnapshot:
    """Point-in-time resource usage reading."""

    timestamp: float
    memory_usage_mb: float
    child_process_count: int
    running_jobs: int
    active_sheets: int
    zombie_pids: list[int] = field(default_factory=list)
    probe_failed: bool = False


def _compute_percent(current: float, limit: float) -> float:
    """Compute percentage of limit, clamped to [0, 100]."""
    if limit <= 0:
        return 0.0
    return min((current / limit) * 100.0, 100.0)


class ResourceMonitor:
    """Periodic resource monitoring for the daemon.

    Checks memory usage, child process count, and zombie processes
    on a configurable interval.  Emits structured log warnings when
    approaching limits and can trigger hard actions (job cancellation)
    when hard limits are exceeded.
    """

    # Threshold percentages
    WARN_THRESHOLD = 80.0
    HARD_THRESHOLD = 95.0

    def __init__(
        self,
        config: ResourceLimitConfig,
        manager: JobManager | None = None,
        pgroup: ProcessGroupManager | None = None,
    ) -> None:
        self._config = config
        self._manager = manager
        self._pgroup = pgroup
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._consecutive_failures = 0
        self._degraded = False
        self._prune_consecutive_failures = 0
        self._prune_disabled = False
        self._last_successful_check: float = 0.0

    async def start(self, interval_seconds: float = 15.0) -> None:
        """Start the periodic monitoring loop."""
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(interval_seconds))
        self._task.add_done_callback(self._on_loop_done)
        _logger.info("monitor.started", interval=interval_seconds)

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        _logger.info("monitor.stopped")

    async def check_now(self) -> ResourceSnapshot:
        """Run an immediate resource check and return snapshot."""
        running_jobs = 0
        active_sheets = 0
        if self._manager is not None:
            running_jobs = self._manager.running_count
            active_sheets = self._manager.active_job_count

        mem = self._get_memory_usage_mb()
        procs = self._get_child_process_count()
        zombie_pids = self._check_for_zombies()
        probe_failed = mem is None or procs is None

        snapshot = ResourceSnapshot(
            timestamp=time.monotonic(),
            memory_usage_mb=mem if mem is not None else 0.0,
            child_process_count=procs if procs is not None else 0,
            running_jobs=running_jobs,
            active_sheets=active_sheets,
            zombie_pids=zombie_pids,
            probe_failed=probe_failed,
        )
        return snapshot

    @property
    def seconds_since_last_check(self) -> float:
        """Seconds since the last successful monitoring check.

        Returns ``float('inf')`` if no check has succeeded yet.
        Consumers can use this to detect stale resource data.
        """
        if self._last_successful_check == 0.0:
            return float("inf")
        return time.monotonic() - self._last_successful_check

    def is_accepting_work(self) -> bool:
        """Check if resource usage is below warning thresholds.

        Returns True when both memory and process counts are below
        ``WARN_THRESHOLD`` percent of their configured limits.  Used by
        ``HealthChecker.readiness()`` to signal backpressure.

        Fail-closed: returns False when probes fail or monitor is degraded.
        """
        if self._degraded:
            return False
        mem = self._get_memory_usage_mb()
        procs = self._get_child_process_count()
        if mem is None or procs is None:
            return False
        mem_pct = _compute_percent(mem, self._config.max_memory_mb)
        proc_pct = _compute_percent(procs, self._config.max_processes)
        return mem_pct < self.WARN_THRESHOLD and proc_pct < self.WARN_THRESHOLD

    # ─── Public API for BackpressureController ──────────────────────

    @property
    def max_memory_mb(self) -> int:
        """Configured maximum memory in MB."""
        return self._config.max_memory_mb

    def current_memory_mb(self) -> float | None:
        """Current RSS memory in MB, or None if probes fail."""
        return self._get_memory_usage_mb()

    @property
    def is_degraded(self) -> bool:
        """Whether the monitor has entered degraded mode due to repeated failures."""
        return self._degraded

    def update_limits(self, new_limits: ResourceLimitConfig) -> None:
        """Hot-apply new resource limits from a SIGHUP config reload.

        Replaces the internal ``_config`` reference.  Safe because the
        monitor only reads ``_config`` during periodic checks, and
        asyncio's single-threaded event loop prevents concurrent access.
        """
        old = self._config
        if (
            old.max_memory_mb != new_limits.max_memory_mb
            or old.max_processes != new_limits.max_processes
        ):
            _logger.info(
                "monitor.limits_updated",
                old_memory_mb=old.max_memory_mb,
                new_memory_mb=new_limits.max_memory_mb,
                old_max_processes=old.max_processes,
                new_max_processes=new_limits.max_processes,
            )
        self._config = new_limits

    def set_manager(self, manager: JobManager) -> None:
        """Wire up the job manager reference after construction.

        Called by DaemonProcess after both the monitor and manager are
        created, avoiding the circular dependency of needing both at init.
        """
        self._manager = manager

    def _on_loop_done(self, task: asyncio.Task[None]) -> None:
        """Log errors if the monitoring loop dies unexpectedly."""
        exc = log_task_exception(task, _logger, "monitor.loop_died_unexpectedly")
        if exc is not None:
            self._degraded = True

    # ─── Internal ─────────────────────────────────────────────────────

    _CIRCUIT_BREAKER_THRESHOLD = 5
    _MAX_BACKOFF_SECONDS = 300.0

    async def _loop(self, interval: float) -> None:
        """Periodic monitoring loop with circuit breaker and backoff."""
        while self._running:
            try:
                snapshot = await self.check_now()
                await self._evaluate(snapshot)
                self._last_successful_check = time.monotonic()
                if self._consecutive_failures > 0:
                    _logger.info(
                        "monitor.recovered",
                        after_failures=self._consecutive_failures,
                    )
                self._consecutive_failures = 0
                if self._degraded:
                    self._degraded = False
                    _logger.info("monitor.degraded_cleared")
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except (OSError, RuntimeError, ValueError):
                self._consecutive_failures += 1
                _logger.exception(
                    "monitor.check_failed",
                    consecutive_failures=self._consecutive_failures,
                )
                if (
                    self._consecutive_failures >= self._CIRCUIT_BREAKER_THRESHOLD
                    and not self._degraded
                ):
                    self._degraded = True
                    _logger.error(
                        "monitor.degraded",
                        consecutive_failures=self._consecutive_failures,
                        message="Monitor entering degraded mode — "
                        "health checks will report not-ready",
                    )
                # Exponential backoff once degraded to avoid log spam
                if self._degraded:
                    exponent = self._consecutive_failures - self._CIRCUIT_BREAKER_THRESHOLD
                    backoff = min(
                        interval * (2 ** max(exponent, 0)),
                        self._MAX_BACKOFF_SECONDS,
                    )
                    await asyncio.sleep(backoff)
                else:
                    await asyncio.sleep(interval)

    async def _evaluate(self, snapshot: ResourceSnapshot) -> None:
        """Log warnings and enforce hard limits based on snapshot."""
        mem_pct = _compute_percent(
            snapshot.memory_usage_mb, self._config.max_memory_mb,
        )
        proc_pct = _compute_percent(
            snapshot.child_process_count, self._config.max_processes,
        )

        # Memory warnings / enforcement
        if mem_pct >= self.HARD_THRESHOLD:
            _logger.error(
                "monitor.memory_critical",
                usage_mb=snapshot.memory_usage_mb,
                limit_mb=self._config.max_memory_mb,
                percent=round(mem_pct, 1),
            )
            await self._enforce_memory_limit()
        elif mem_pct >= self.WARN_THRESHOLD:
            _logger.warning(
                "monitor.memory_warning",
                usage_mb=snapshot.memory_usage_mb,
                limit_mb=self._config.max_memory_mb,
                percent=round(mem_pct, 1),
            )

        # Process count warnings / enforcement
        if proc_pct >= self.HARD_THRESHOLD:
            _logger.error(
                "monitor.processes_critical",
                count=snapshot.child_process_count,
                limit=self._config.max_processes,
                percent=round(proc_pct, 1),
            )
            await self._enforce_process_limit()
        elif proc_pct >= self.WARN_THRESHOLD:
            _logger.warning(
                "monitor.processes_warning",
                count=snapshot.child_process_count,
                limit=self._config.max_processes,
                percent=round(proc_pct, 1),
            )

        # Zombie reaping
        if snapshot.zombie_pids:
            _logger.warning(
                "monitor.zombies_detected",
                zombie_pids=snapshot.zombie_pids,
                count=len(snapshot.zombie_pids),
            )

        # Periodic orphan cleanup via process group manager
        if self._pgroup is not None:
            self._pgroup.cleanup_orphans()
            # System-wide scan for orphaned backend children (LSP servers,
            # MCP servers, etc.) that escaped the daemon's process tree.
            # These are processes reparented to init after their parent
            # (Claude CLI) exited without cleaning them up.
            self._pgroup.reap_orphaned_backends()

        # Prune stale rate limit events to prevent unbounded memory growth.
        # Circuit breaker: after 3 consecutive failures, disable pruning
        # to avoid log spam and wasted cycles.
        if self._manager is not None and not self._prune_disabled:
            try:
                await self._manager.rate_coordinator.prune_stale()
                if self._prune_consecutive_failures > 0:
                    _logger.info(
                        "monitor.prune_recovered",
                        after_failures=self._prune_consecutive_failures,
                    )
                    self._prune_consecutive_failures = 0
            except (OSError, RuntimeError, ValueError):
                self._prune_consecutive_failures += 1
                _logger.warning(
                    "monitor.prune_stale_failed",
                    consecutive_failures=self._prune_consecutive_failures,
                    exc_info=True,
                )
                if self._prune_consecutive_failures >= 3:
                    self._prune_disabled = True
                    _logger.error(
                        "monitor.prune_disabled",
                        consecutive_failures=self._prune_consecutive_failures,
                        message="Pruning disabled after repeated failures. "
                        "Rate limit event list may grow unbounded.",
                        exc_info=True,
                    )

    async def _enforce_memory_limit(self) -> None:
        """Pause the oldest running job when memory exceeds hard limit."""
        await self._shed_oldest_job("memory")

    async def _enforce_process_limit(self) -> None:
        """Pause the oldest running job when process count exceeds hard limit."""
        await self._shed_oldest_job("processes")

    async def _shed_oldest_job(self, reason: str) -> None:
        """Pause the oldest running job to reduce resource pressure.

        Uses graceful pause (signal file) so the job finishes its current
        sheet and saves state cleanly, remaining resumable.  Falls back to
        cancel only if pausing fails (e.g. job is in a non-pausable state).
        """
        if self._manager is None:
            return
        jobs = await self._manager.list_jobs()
        running = [j for j in jobs if j.get("status") == "running"]
        if running:
            oldest = min(running, key=lambda j: j.get("submitted_at", 0))
            job_id = oldest.get("job_id")
            if job_id:
                try:
                    _logger.warning(
                        "monitor.pausing_oldest_job",
                        job_id=job_id,
                        reason=reason,
                    )
                    await self._manager.pause_job(job_id)
                except Exception:
                    _logger.warning(
                        "monitor.pause_failed_falling_back_to_cancel",
                        job_id=job_id,
                        reason=reason,
                        exc_info=True,
                    )
                    await self._manager.cancel_job(job_id)

    # ─── System Probes (delegated to SystemProbe) ──────────────────────

    @staticmethod
    def _get_memory_usage_mb() -> float | None:
        """Get current RSS memory in MB.  Delegates to SystemProbe."""
        return SystemProbe.get_memory_mb()

    @staticmethod
    def _get_child_process_count() -> int | None:
        """Count child processes.  Delegates to SystemProbe."""
        return SystemProbe.get_child_count()

    @staticmethod
    def _check_for_zombies() -> list[int]:
        """Detect and reap zombie child processes.  Delegates to SystemProbe."""
        return SystemProbe.reap_zombies()


__all__ = ["ResourceMonitor", "ResourceSnapshot"]
