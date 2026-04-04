"""Backpressure — adaptive load management.

Monitors system resource pressure (memory, rate limits) and throttles
sheet dispatch to prevent overload.  Implements the ``BackpressureChecker``
protocol defined in ``scheduler.py`` so the ``GlobalSheetScheduler`` can
consult backpressure before dispatching each sheet.

Also exposes ``should_accept_job()`` for the ``JobManager`` to reject
new submissions when the system is critically stressed.

Lock ordering (daemon-wide):
  1. GlobalSheetScheduler._lock
  2. RateLimitCoordinator._lock
  3. BackpressureController  (no internal lock — reads are atomic)
  4. CentralLearningStore._lock  (future — Stage 5)
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from mozart.core.logging import get_logger
from mozart.daemon.monitor import ResourceMonitor
from mozart.daemon.rate_coordinator import RateLimitCoordinator

if TYPE_CHECKING:
    from mozart.daemon.learning_hub import LearningHub
    from mozart.daemon.profiler.models import ResourceEstimate

_logger = get_logger("daemon.backpressure")


class PressureLevel(Enum):
    """Graduated pressure levels for adaptive load management."""

    NONE = "none"          # All systems go
    LOW = "low"            # Slight delay between sheets
    MEDIUM = "medium"      # Significant delay between sheets
    HIGH = "high"          # Reject new jobs, wait for relief
    CRITICAL = "critical"  # Emergency: cancel lowest-priority jobs


# Delay in seconds per pressure level, applied before each sheet dispatch
_LEVEL_DELAYS: dict[PressureLevel, float] = {
    PressureLevel.NONE: 0.0,
    PressureLevel.LOW: 2.0,
    PressureLevel.MEDIUM: 10.0,
    PressureLevel.HIGH: 30.0,
    PressureLevel.CRITICAL: 60.0,
}


class BackpressureController:
    """Manages system load through adaptive backpressure.

    Assesses memory usage (as a fraction of the configured limit) and
    active rate limits to determine a ``PressureLevel``.  The scheduler
    calls ``can_start_sheet()`` before dispatching each sheet and gets
    back a (allowed, delay) tuple.

    No internal lock is needed because:
    - ``ResourceMonitor`` methods are thread/coroutine-safe.
    - ``RateLimitCoordinator.active_limits`` is a property that
      reads a dict snapshot.
    - ``PressureLevel`` assessment is a pure function of those reads.
    """

    def __init__(
        self,
        monitor: ResourceMonitor,
        rate_coordinator: RateLimitCoordinator,
        learning_hub: LearningHub | None = None,
    ) -> None:
        self._monitor = monitor
        self._rate_coordinator = rate_coordinator
        self._learning_hub = learning_hub

    def current_level(self) -> PressureLevel:
        """Assess current pressure level from resource metrics.

        Thresholds (memory as % of ``ResourceLimitConfig.max_memory_mb``):
          - probe failure or monitor degraded  → CRITICAL (fail-closed)
          - >95% or monitor not accepting work → CRITICAL
          - >85% or any active rate limit      → HIGH
          - >70%                               → MEDIUM
          - >50%                               → LOW
          - otherwise                          → NONE

        Note: ``is_accepting_work()`` also checks child process count
        against ``ResourceLimitConfig.max_processes``.  When process
        count exceeds 80% of the configured limit, ``is_accepting_work()``
        returns ``False``, which triggers the CRITICAL path here.  This
        means high process counts indirectly escalate backpressure to
        CRITICAL even when memory usage is low.

        TOCTOU fix (P007): memory is read once and reused for all
        threshold checks.  ``is_accepting_work()`` is still called for
        its process-count check, but the memory percentage used in
        threshold comparisons comes from the single snapshot above.
        """
        # Single snapshot of memory — avoids TOCTOU where memory changes
        # between our percentage check and is_accepting_work()'s check.
        current_mem = self._monitor.current_memory_mb()
        if current_mem is None or self._monitor.is_degraded:
            return PressureLevel.CRITICAL

        max_mem = max(self._monitor.max_memory_mb, 1)
        memory_pct = current_mem / max_mem

        # is_accepting_work() re-reads memory internally, but we use our
        # snapshot for threshold decisions; it's only called for its
        # process-count gate (returns False when procs > 80% of limit).
        accepting_work = self._monitor.is_accepting_work()

        if memory_pct > 0.95 or not accepting_work:
            return PressureLevel.CRITICAL
        # Rate limit escalation: when any backend is actively rate-limited,
        # escalate to HIGH even if memory is below the 85% threshold.
        # Data flows via JobManager._on_rate_limit → RateLimitCoordinator.
        if memory_pct > 0.85 or self._rate_coordinator.active_limits:
            return PressureLevel.HIGH
        if memory_pct > 0.70:
            return PressureLevel.MEDIUM
        if memory_pct > 0.50:
            return PressureLevel.LOW
        return PressureLevel.NONE

    # ─── BackpressureChecker protocol ──────────────────────────────

    async def can_start_sheet(self) -> tuple[bool, float]:
        """Whether the scheduler may dispatch a sheet, and any delay.

        Satisfies the ``BackpressureChecker`` protocol used by
        ``GlobalSheetScheduler.next_sheet()``.

        Returns:
            ``(allowed, delay_seconds)``.  At CRITICAL level the sheet
            is rejected (``allowed=False``).  At lower levels a positive
            delay is returned to slow dispatch.
        """
        level = self.current_level()
        delay = _LEVEL_DELAYS[level]

        if level == PressureLevel.CRITICAL:
            _logger.warning(
                "backpressure.sheet_rejected",
                level=level.value,
            )
            return False, delay

        if delay > 0:
            _logger.info(
                "backpressure.sheet_delayed",
                level=level.value,
                delay_seconds=delay,
            )

        return True, delay

    # ─── Job-level gating ──────────────────────────────────────────

    def should_accept_job(self) -> bool:
        """Whether to accept new job submissions.

        Returns ``False`` at HIGH or CRITICAL pressure to prevent
        further resource consumption.
        """
        level = self.current_level()
        if level in (PressureLevel.HIGH, PressureLevel.CRITICAL):
            _logger.info(
                "backpressure.job_rejected",
                level=level.value,
            )
            return False
        return True

    def rejection_reason(self) -> str | None:
        """Why would a job be rejected right now?

        Returns:
            ``None`` if the system would accept the job.
            ``"rate_limit"`` if the only pressure source is active rate limits
            (memory and processes are healthy).
            ``"resource"`` if memory or process pressure is the cause.

        This lets callers distinguish between rate-limit rejections
        (where the job can be queued as PENDING) and resource rejections
        (where accepting more work would be dangerous).
        """
        current_mem = self._monitor.current_memory_mb()
        if current_mem is None or self._monitor.is_degraded:
            return "resource"

        max_mem = max(self._monitor.max_memory_mb, 1)
        memory_pct = current_mem / max_mem
        accepting_work = self._monitor.is_accepting_work()

        # Critical resource pressure — always resource
        if memory_pct > 0.95 or not accepting_work:
            return "resource"

        # High memory (>85%) — resource pressure regardless of rate limits
        if memory_pct > 0.85:
            return "resource"

        # Rate limits active but memory is fine — rate limit only
        if self._rate_coordinator.active_limits:
            return "rate_limit"

        # No pressure
        return None

    # ─── Resource-aware scheduling hints ──────────────────────────

    async def estimate_job_resource_needs(
        self, job_config_hash: str
    ) -> ResourceEstimate | None:
        """Query learned resource patterns for similar job types.

        Looks up ``RESOURCE_CORRELATION`` patterns from the learning store
        that match the given job config hash.  Returns a ``ResourceEstimate``
        if sufficient historical data exists, otherwise ``None``.

        This is advisory — the caller uses it to adjust thresholds, not
        to block jobs outright.

        Args:
            job_config_hash: Stable hash identifying the job type/config.

        Returns:
            ``ResourceEstimate`` with predicted peak memory, CPU-time,
            and confidence, or ``None`` if no data is available.
        """
        if self._learning_hub is None or not self._learning_hub.is_running:
            return None

        from mozart.daemon.profiler.models import ResourceEstimate as RE
        from mozart.learning.patterns import PatternType

        try:
            store = self._learning_hub.store
            patterns = store.get_patterns(
                pattern_type=PatternType.RESOURCE_CORRELATION.value,
                limit=10,
                min_priority=0.0,
            )

            if not patterns:
                return None

            # Aggregate resource estimates from matching patterns
            peak_memory_values: list[float] = []
            cpu_values: list[float] = []
            confidence_sum = 0.0

            for pattern in patterns:
                desc = pattern.description or ""
                # Extract memory hints from description
                if "RSS" in desc or "memory" in desc.lower():
                    # Use effectiveness_score as a rough confidence
                    confidence_sum += pattern.effectiveness_score

                # Look for memory_bin context tags for peak memory hints
                tags = pattern.context_tags or ""
                if isinstance(tags, str):
                    tag_str = tags
                elif isinstance(tags, list):
                    tag_str = ",".join(tags)
                else:
                    tag_str = str(tags)

                # Parse failure_rate from tags for signal strength
                if "memory_bin:" in tag_str:
                    # Presence of memory correlation patterns gives us
                    # a rough estimate based on bin boundaries
                    if "<256MB" in tag_str:
                        peak_memory_values.append(256.0)
                    elif "256-512MB" in tag_str:
                        peak_memory_values.append(512.0)
                    elif "512MB-1GB" in tag_str:
                        peak_memory_values.append(1024.0)
                    elif "1-2GB" in tag_str:
                        peak_memory_values.append(2048.0)
                    elif ">2GB" in tag_str:
                        peak_memory_values.append(4096.0)

            if not peak_memory_values and confidence_sum == 0:
                return None

            avg_memory = (
                sum(peak_memory_values) / len(peak_memory_values)
                if peak_memory_values
                else 0.0
            )
            avg_cpu = (
                sum(cpu_values) / len(cpu_values)
                if cpu_values
                else 0.0
            )
            confidence = min(1.0, confidence_sum / max(len(patterns), 1))

            return RE(
                estimated_peak_memory_mb=avg_memory,
                estimated_cpu_seconds=avg_cpu,
                confidence=confidence,
            )

        except Exception:
            _logger.debug(
                "backpressure.resource_estimate_failed",
                job_config_hash=job_config_hash,
                exc_info=True,
            )
            return None


__all__ = ["BackpressureController", "PressureLevel"]
