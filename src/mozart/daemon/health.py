"""Health check infrastructure for daemon readiness and liveness probes.

Provides ``HealthChecker`` with two probe types:

- **Liveness**: Is the daemon process alive and responsive?  Always returns
  OK if the daemon can execute the handler at all.
- **Readiness**: Is the daemon ready to accept new jobs?  Checks resource
  limits via ``ResourceMonitor`` to implement backpressure signaling.

These are registered as JSON-RPC methods (``daemon.health``, ``daemon.ready``)
so clients (CLI, orchestrators) can query them over the Unix socket.

Evolution v25: Entropy Response Activation - periodic entropy checks trigger
automatic diversity injection when pattern collapse is detected.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from mozart.core.logging import get_logger

if TYPE_CHECKING:
    from mozart.daemon.manager import JobManager
    from mozart.daemon.monitor import ResourceMonitor
    from mozart.learning.store import GlobalLearningStore

_logger = get_logger("daemon.health")


class HealthChecker:
    """Daemon health and readiness probes.

    Evolution v25: Entropy Response Activation - added periodic entropy
    monitoring and automatic diversity injection when collapse is detected.

    Parameters
    ----------
    manager:
        The ``JobManager`` instance for job count queries.
    monitor:
        The ``ResourceMonitor`` instance for resource threshold checks.
    learning_store:
        Optional learning store for entropy monitoring. If None, entropy
        checks are disabled.
    """

    def __init__(
        self,
        manager: JobManager,
        monitor: ResourceMonitor,
        *,
        start_time: float | None = None,
        learning_store: GlobalLearningStore | None = None,
    ) -> None:
        self._manager = manager
        self._monitor = monitor
        self._start_time = start_time or time.monotonic()
        self._learning_store = learning_store
        self._last_entropy_check = 0.0
        self._entropy_check_task: asyncio.Task[None] | None = None
        self._completed_jobs_since_check = 0

    async def liveness(self) -> dict[str, Any]:
        """Is the daemon process alive and responsive?

        This is the cheapest possible check — if the daemon can execute
        this handler and return a response, it's alive.  No resource
        checks or I/O are performed.
        """
        return {
            "status": "ok",
            "pid": os.getpid(),
            "uptime_seconds": round(time.monotonic() - self._start_time, 1),
            "shutting_down": self._manager.shutting_down,
        }

    async def readiness(self) -> dict[str, Any]:
        """Is the daemon ready to accept new jobs?

        Checks resource thresholds via the monitor, job failure rate
        via the manager, and notification health.  Returns ``"ready"``
        when resources are within limits, the failure rate is not
        elevated, and notifications are functional; ``"not_ready"``
        otherwise.
        """
        snapshot = await self._monitor.check_now()
        accepting = self._monitor.is_accepting_work()
        failure_elevated = self._manager.failure_rate_elevated
        notif_degraded = self._manager.notifications_degraded

        shutting_down = self._manager.shutting_down
        is_ready = (
            accepting
            and not shutting_down
            and not failure_elevated
            and not notif_degraded
        )
        return {
            "status": "ready" if is_ready else "not_ready",
            "running_jobs": self._manager.running_count,
            "memory_mb": round(snapshot.memory_usage_mb, 1),
            "child_processes": snapshot.child_process_count,
            "accepting_work": is_ready,
            "shutting_down": shutting_down,
            "failure_rate_elevated": failure_elevated,
            "notifications_degraded": notif_degraded,
            "uptime_seconds": round(time.monotonic() - self._start_time, 1),
        }

    def on_job_completed(self) -> None:
        """Called by JobManager when a job completes.

        Evolution v25: Entropy Response Activation - tracks completed jobs
        to trigger entropy checks every 10 completions.
        """
        self._completed_jobs_since_check += 1

        # Check entropy every 10 completed jobs (in addition to time-based checks)
        if self._completed_jobs_since_check >= 10:
            self._completed_jobs_since_check = 0
            # Schedule async check without blocking
            if self._learning_store is not None:
                asyncio.create_task(self._check_entropy_and_respond())

    async def _check_entropy_and_respond(self) -> None:
        """Check entropy and trigger response if below threshold.

        Evolution v25: Entropy Response Activation - called periodically and
        after every 10 job completions to detect pattern collapse.
        """
        if self._learning_store is None:
            return

        try:
            # Get config from manager
            config = self._manager._config.learning
            threshold = config.entropy_threshold
            cooldown = config.entropy_check_interval_seconds

            # Check if response is needed (includes cooldown check)
            needs_response, current_entropy, reason = (
                self._learning_store.check_entropy_response_needed(
                    job_hash="daemon-entropy-check",
                    entropy_threshold=threshold,
                    cooldown_seconds=int(cooldown),
                )
            )

            if needs_response:
                _logger.warning(
                    "entropy.collapse_detected",
                    entropy=round(current_entropy, 4) if current_entropy is not None else None,
                    threshold=threshold,
                    reason=reason,
                )

                # Trigger response
                from mozart.learning.store.budget import (
                    EntropyResponseConfig,
                    EntropyTriggerContext,
                )
                trigger = EntropyTriggerContext(
                    job_hash="daemon-entropy-check",
                    entropy_at_trigger=current_entropy or 0.0,
                    threshold_used=threshold,
                )
                response_config = EntropyResponseConfig(
                    boost_budget=True,
                    revisit_quarantine=True,
                    max_quarantine_revisits=3,
                    budget_floor=0.05,
                    budget_ceiling=0.50,
                    budget_boost_amount=config.exploration_budget,
                )

                response = self._learning_store.trigger_entropy_response(
                    trigger=trigger,
                    config=response_config,
                )

                _logger.info(
                    "entropy.response_triggered",
                    response_id=response.id,
                    actions_taken=response.actions_taken,
                    budget_boosted=response.budget_boosted,
                    quarantine_revisits=response.quarantine_revisits,
                )
            else:
                _logger.debug(
                    "entropy.check_passed",
                    entropy=round(current_entropy, 4) if current_entropy is not None else None,
                    threshold=threshold,
                    reason=reason,
                )

            self._last_entropy_check = time.monotonic()

        except Exception:
            _logger.error(
                "entropy.check_failed",
                exc_info=True,
            )

    async def start_periodic_checks(self) -> None:
        """Start background task for periodic entropy checks.

        Evolution v25: Entropy Response Activation - runs entropy checks
        at configured intervals (default 1 hour).
        """
        if self._learning_store is None:
            return

        async def _check_loop() -> None:
            config = self._manager._config.learning
            interval = config.entropy_check_interval_seconds

            while not self._manager.shutting_down:
                await asyncio.sleep(interval)
                await self._check_entropy_and_respond()

        self._entropy_check_task = asyncio.create_task(_check_loop())

    async def stop_periodic_checks(self) -> None:
        """Stop the periodic entropy check task.

        Evolution v25: Entropy Response Activation - cleanly shuts down
        the entropy monitoring loop.
        """
        if self._entropy_check_task is not None:
            self._entropy_check_task.cancel()
            try:
                await self._entropy_check_task
            except asyncio.CancelledError:
                pass
            self._entropy_check_task = None


__all__ = ["HealthChecker"]
