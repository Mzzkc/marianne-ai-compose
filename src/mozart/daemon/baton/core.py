"""Baton core — event inbox, main loop, and sheet registry.

The baton core is the event-driven execution heart of the conductor.
It processes events from five sources (musicians, timers, external
commands, observer, internal dispatch), maintains per-sheet execution
state, resolves ready sheets, and coordinates dispatch.

The main loop is simple by design::

    while not shutting_down:
        event = await inbox.get()
        handle(event)        # updates state
        dispatch_ready()     # dispatches sheets if state changed
        persist()            # save if dirty

Everything else — retry logic, rate limit management, cost enforcement —
is done inside event handlers, not in separate subsystems.

See: ``docs/plans/2026-03-26-baton-design.md`` for the full architecture.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from mozart.daemon.baton.events import (
    BatonEvent,
    CancelJob,
    ConfigReloaded,
    CronTick,
    DispatchRetry,
    EscalationNeeded,
    EscalationResolved,
    EscalationTimeout,
    JobTimeout,
    PacingComplete,
    PauseJob,
    ProcessExited,
    RateLimitExpired,
    RateLimitHit,
    ResourceAnomaly,
    ResumeJob,
    RetryDue,
    SheetAttemptResult,
    SheetSkipped,
    ShutdownRequested,
    StaleCheck,
)

_logger = logging.getLogger(__name__)

# Terminal statuses — sheets in these states don't need further action
_TERMINAL_STATUSES = frozenset({"completed", "failed", "skipped", "cancelled"})

# Statuses that satisfy downstream dependencies
_SATISFIED_STATUSES = frozenset({"completed", "skipped"})

# Statuses that indicate a sheet is ready for dispatch
_DISPATCHABLE_STATUSES = frozenset({"pending", "ready"})


@dataclass
class SheetExecutionState:
    """The conductor's per-sheet tracking during a performance.

    This is NOT the same as SheetState in checkpoint.py — that tracks
    outcomes. This tracks the conductor's scheduling decisions: attempt
    counts, retry schedules, current mode.

    Attributes:
        sheet_num: Concrete sheet number (1-indexed).
        instrument_name: Which instrument executes this sheet.
        status: Current scheduling status.
        normal_attempts: Number of execution attempts (not counting
            rate limits, which are not failures).
        completion_attempts: Number of completion-mode re-executions.
        healing_attempts: Number of self-healing attempts.
        max_retries: Maximum normal retries (from RetryConfig).
        max_completion: Maximum completion-mode attempts.
    """

    sheet_num: int
    instrument_name: str
    status: str = "pending"  # pending, ready, dispatched, completed, failed,
    # skipped, cancelled, waiting, retry_scheduled, fermata
    normal_attempts: int = 0
    completion_attempts: int = 0
    healing_attempts: int = 0
    max_retries: int = 3
    max_completion: int = 5
    next_retry_at: float | None = None
    attempt_results: list[SheetAttemptResult] = field(default_factory=list)


@dataclass
class _JobRecord:
    """Internal tracking for a registered job."""

    job_id: str
    sheets: dict[int, SheetExecutionState]
    dependencies: dict[int, list[int]]
    paused: bool = False
    user_paused: bool = False  # Tracks user-initiated pause (PauseJob)
    created_at: float = field(default_factory=time.time)


class BatonCore:
    """The baton's event-driven execution core.

    Manages the event inbox, processes events, tracks sheet state
    across all jobs, resolves ready sheets, and coordinates dispatch.

    The baton does NOT own backend execution — it decides WHEN to
    dispatch, not HOW. Sheet execution is delegated to the musician
    (via dispatch callbacks registered by the conductor).

    Usage::

        baton = BatonCore()
        baton.register_job("j1", sheets, deps)

        # Run the main loop (blocks until shutdown)
        await baton.run()

        # Or process events manually (for testing)
        await baton.handle_event(some_event)
    """

    def __init__(self) -> None:
        self._inbox: asyncio.Queue[BatonEvent] = asyncio.Queue()
        self._jobs: dict[str, _JobRecord] = {}
        self._shutting_down = False
        self._running = False
        self._state_dirty = False

    @property
    def inbox(self) -> asyncio.Queue[BatonEvent]:
        """The event inbox — put events here for the baton to process."""
        return self._inbox

    @property
    def is_running(self) -> bool:
        """Whether the main loop is currently running."""
        return self._running

    @property
    def job_count(self) -> int:
        """Number of registered jobs."""
        return len(self._jobs)

    @property
    def running_sheet_count(self) -> int:
        """Number of sheets currently in 'dispatched' status."""
        return sum(
            1
            for job in self._jobs.values()
            for sheet in job.sheets.values()
            if sheet.status == "dispatched"
        )

    # =========================================================================
    # Sheet Registry
    # =========================================================================

    def register_job(
        self,
        job_id: str,
        sheets: dict[int, SheetExecutionState],
        dependencies: dict[int, list[int]],
    ) -> None:
        """Register a job's sheets with the baton for scheduling.

        Args:
            job_id: Unique job identifier.
            sheets: Map of sheet_num → SheetExecutionState.
            dependencies: Map of sheet_num → list of dependency sheet_nums.
                Sheets not in this map have no dependencies.
        """
        if job_id in self._jobs:
            _logger.warning(
                "baton.register_job.duplicate",
                extra={"job_id": job_id},
            )
            return

        self._jobs[job_id] = _JobRecord(
            job_id=job_id,
            sheets=sheets,
            dependencies=dependencies,
        )
        self._state_dirty = True

        _logger.info(
            "baton.job_registered",
            extra={
                "job_id": job_id,
                "sheet_count": len(sheets),
                "dependency_count": len(dependencies),
            },
        )

    def deregister_job(self, job_id: str) -> None:
        """Remove a job from the baton's tracking."""
        if job_id in self._jobs:
            del self._jobs[job_id]
            self._state_dirty = True
            _logger.info("baton.job_deregistered", extra={"job_id": job_id})

    def get_sheet_state(
        self, job_id: str, sheet_num: int
    ) -> SheetExecutionState | None:
        """Get the scheduling state for a specific sheet."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        return job.sheets.get(sheet_num)

    def is_job_paused(self, job_id: str) -> bool:
        """Check if a job's dispatch is paused."""
        job = self._jobs.get(job_id)
        return job.paused if job is not None else False

    def is_job_complete(self, job_id: str) -> bool:
        """Check if all sheets in a job are in terminal state."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        return all(
            sheet.status in _TERMINAL_STATUSES for sheet in job.sheets.values()
        )

    # =========================================================================
    # Ready Sheet Resolution
    # =========================================================================

    def get_ready_sheets(self, job_id: str) -> list[SheetExecutionState]:
        """Find sheets that are ready to dispatch.

        A sheet is ready when:
        1. Status is 'pending' or 'ready'
        2. All dependencies are satisfied (completed or skipped)
        3. The job is not paused
        """
        job = self._jobs.get(job_id)
        if job is None or job.paused:
            return []

        ready: list[SheetExecutionState] = []
        for sheet_num, sheet in job.sheets.items():
            if sheet.status not in _DISPATCHABLE_STATUSES:
                continue

            # Check dependencies
            deps = job.dependencies.get(sheet_num, [])
            deps_satisfied = all(
                self._is_dependency_satisfied(job, dep) for dep in deps
            )
            if deps_satisfied:
                ready.append(sheet)

        return ready

    def _is_dependency_satisfied(self, job: _JobRecord, dep_num: int) -> bool:
        """Check if a dependency sheet is in a satisfied state."""
        dep_sheet = job.sheets.get(dep_num)
        if dep_sheet is None:
            # Missing dependency — treat as satisfied (defensive)
            _logger.warning(
                "baton.missing_dependency",
                extra={"job_id": job.job_id, "dep_num": dep_num},
            )
            return True
        return dep_sheet.status in _SATISFIED_STATUSES

    # =========================================================================
    # Event Handling
    # =========================================================================

    async def handle_event(self, event: BatonEvent) -> None:
        """Process a single event. Updates state but does NOT dispatch.

        This is the core decision-making method. Each event type has
        a specific handler that updates sheet state.

        Per the baton spec: handler exceptions are logged, not re-raised.
        The baton continues processing subsequent events.
        """
        try:
            match event:
                # === Musician events ===
                case SheetAttemptResult():
                    self._handle_attempt_result(event)

                case SheetSkipped():
                    self._handle_sheet_skipped(event)

                # === Rate limit events ===
                case RateLimitHit():
                    self._handle_rate_limit_hit(event)

                case RateLimitExpired():
                    self._handle_rate_limit_expired(event)

                # === Timer events ===
                case RetryDue():
                    self._handle_retry_due(event)

                case StaleCheck():
                    pass  # TODO: implement stale detection

                case CronTick():
                    pass  # TODO: implement cron scheduling

                case JobTimeout():
                    self._handle_job_timeout(event)

                case PacingComplete():
                    pass  # TODO: implement pacing

                # === Escalation events ===
                case EscalationNeeded():
                    self._handle_escalation_needed(event)

                case EscalationResolved():
                    self._handle_escalation_resolved(event)

                case EscalationTimeout():
                    self._handle_escalation_timeout(event)

                # === External command events ===
                case PauseJob():
                    self._handle_pause_job(event)

                case ResumeJob():
                    self._handle_resume_job(event)

                case CancelJob():
                    self._handle_cancel_job(event)

                case ConfigReloaded():
                    pass  # TODO: implement config reload

                case ShutdownRequested():
                    self._handle_shutdown(event)

                # === Observer events ===
                case ProcessExited():
                    self._handle_process_exited(event)

                case ResourceAnomaly():
                    pass  # TODO: implement backpressure

                # === Internal events ===
                case DispatchRetry():
                    pass  # Dispatch retry — _dispatch_ready handles this

                case _:
                    _logger.warning(
                        "baton.unknown_event",
                        extra={"event_type": type(event).__name__},
                    )

        except Exception:
            _logger.error(
                "baton.event_handler_failed",
                extra={"event_type": type(event).__name__},
                exc_info=True,
            )

    # =========================================================================
    # Event Handlers — private
    # =========================================================================

    def _handle_attempt_result(self, event: SheetAttemptResult) -> None:
        """Process a musician's execution report."""
        job = self._jobs.get(event.job_id)
        if job is None:
            _logger.warning(
                "baton.attempt_result.unknown_job",
                extra={"job_id": event.job_id, "sheet_num": event.sheet_num},
            )
            return

        sheet = job.sheets.get(event.sheet_num)
        if sheet is None:
            _logger.warning(
                "baton.attempt_result.unknown_sheet",
                extra={"job_id": event.job_id, "sheet_num": event.sheet_num},
            )
            return

        # Terminal guard: once a sheet reaches a terminal state, no event
        # can change it. Late-arriving results (e.g., from cancelled tasks)
        # are safely ignored.
        if sheet.status in _TERMINAL_STATUSES:
            _logger.debug(
                "baton.attempt_result.terminal_noop",
                extra={
                    "job_id": event.job_id,
                    "sheet_num": event.sheet_num,
                    "status": sheet.status,
                },
            )
            return

        # Record the attempt
        sheet.attempt_results.append(event)

        if event.rate_limited:
            # Rate limit — NOT a failure, NOT a retry count increment
            sheet.status = "waiting"
            self._state_dirty = True
            _logger.info(
                "baton.sheet.rate_limited",
                extra={
                    "job_id": event.job_id,
                    "sheet_num": event.sheet_num,
                    "instrument": event.instrument_name,
                },
            )
            return

        # F-018 guard: when execution succeeds with no validations,
        # treat as 100% pass rate regardless of the default value.
        # A musician that reports execution_success=True with
        # validations_total=0 should not trigger unnecessary retries.
        effective_pass_rate = event.validation_pass_rate
        if (
            event.execution_success
            and event.validations_total == 0
            and effective_pass_rate < 100.0
        ):
            effective_pass_rate = 100.0

        if event.execution_success and effective_pass_rate >= 100.0:
            # Perfect execution — mark complete
            sheet.status = "completed"
            self._state_dirty = True
            _logger.info(
                "baton.sheet.completed",
                extra={
                    "job_id": event.job_id,
                    "sheet_num": event.sheet_num,
                    "attempt": event.attempt,
                    "cost_usd": event.cost_usd,
                },
            )
            return

        if event.execution_success and effective_pass_rate > 0:
            # Partial pass — could use completion mode in the future
            sheet.normal_attempts += 1
        elif not event.execution_success:
            # Execution failed
            if event.error_classification == "AUTH_FAILURE":
                sheet.status = "failed"
                self._state_dirty = True
                _logger.error(
                    "baton.sheet.auth_failure",
                    extra={
                        "job_id": event.job_id,
                        "sheet_num": event.sheet_num,
                    },
                )
                self._propagate_failure_to_dependents(event.job_id, event.sheet_num)
                return
            sheet.normal_attempts += 1
        else:
            # Validation failure (0% pass rate)
            sheet.normal_attempts += 1

        # Check if retries exhausted
        if sheet.normal_attempts >= sheet.max_retries:
            sheet.status = "failed"
            self._state_dirty = True
            _logger.warning(
                "baton.sheet.retries_exhausted",
                extra={
                    "job_id": event.job_id,
                    "sheet_num": event.sheet_num,
                    "attempts": sheet.normal_attempts,
                },
            )
            self._propagate_failure_to_dependents(event.job_id, event.sheet_num)
        else:
            # Schedule retry
            sheet.status = "retry_scheduled"
            self._state_dirty = True
            _logger.info(
                "baton.sheet.retry_scheduled",
                extra={
                    "job_id": event.job_id,
                    "sheet_num": event.sheet_num,
                    "attempt": sheet.normal_attempts,
                    "max_retries": sheet.max_retries,
                },
            )

    def _handle_sheet_skipped(self, event: SheetSkipped) -> None:
        """Mark a sheet as skipped.

        Terminal guard: completed, failed, skipped, or cancelled sheets
        cannot be re-marked as skipped by a late event.
        """
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is None:
            return
        if sheet.status in _TERMINAL_STATUSES:
            _logger.debug(
                "baton.sheet_skipped.terminal_noop",
                extra={
                    "job_id": event.job_id,
                    "sheet_num": event.sheet_num,
                    "status": sheet.status,
                },
            )
            return
        sheet.status = "skipped"
        self._state_dirty = True
        _logger.info(
            "baton.sheet.skipped",
            extra={
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "reason": event.reason,
            },
        )

    def _handle_rate_limit_hit(self, event: RateLimitHit) -> None:
        """Mark instrument as rate-limited. NOT a sheet failure.

        Only transition sheets that are currently dispatched or running.
        Pending/ready sheets haven't been sent to an instrument yet;
        terminal sheets must never regress.
        """
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is not None and sheet.status in ("dispatched", "running"):
            sheet.status = "waiting"
        self._state_dirty = True

    def _handle_rate_limit_expired(self, event: RateLimitExpired) -> None:
        """Rate limit cleared — move waiting sheets back to pending."""
        for job in self._jobs.values():
            for sheet in job.sheets.values():
                if (
                    sheet.status == "waiting"
                    and sheet.instrument_name == event.instrument
                ):
                    sheet.status = "pending"
        self._state_dirty = True

    def _handle_retry_due(self, event: RetryDue) -> None:
        """Timer fired — sheet is ready for retry."""
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is not None and sheet.status == "retry_scheduled":
            sheet.status = "pending"
            self._state_dirty = True

    def _handle_job_timeout(self, event: JobTimeout) -> None:
        """Job wall-clock timeout — cancel remaining sheets."""
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        for sheet in job.sheets.values():
            if sheet.status not in _TERMINAL_STATUSES:
                sheet.status = "cancelled"
        self._state_dirty = True
        _logger.warning(
            "baton.job.timeout",
            extra={"job_id": event.job_id},
        )

    def _handle_escalation_needed(self, event: EscalationNeeded) -> None:
        """Enter fermata — pause job, await composer decision.

        Terminal sheets are not affected — a completed/failed/skipped sheet
        cannot enter fermata. The job is still paused to await the composer's
        decision about the escalation context.
        """
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is not None and sheet.status not in _TERMINAL_STATUSES:
            sheet.status = "fermata"
        job.paused = True
        self._state_dirty = True

    def _handle_escalation_resolved(self, event: EscalationResolved) -> None:
        """Composer made a decision on a fermata.

        Only unpauses the job if the user didn't also pause it.
        A user-initiated pause (PauseJob) is independent of escalation.
        """
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is not None and sheet.status == "fermata":
            # Apply decision
            if event.decision == "retry":
                sheet.status = "pending"
            elif event.decision == "skip":
                sheet.status = "skipped"
            elif event.decision == "accept":
                sheet.status = "completed"
            else:
                sheet.status = "failed"
                self._propagate_failure_to_dependents(event.job_id, event.sheet_num)
        # Only unpause if the user hasn't independently paused the job
        if not job.user_paused:
            job.paused = False
        self._state_dirty = True

    def _handle_escalation_timeout(self, event: EscalationTimeout) -> None:
        """No escalation response — default to safe action (fail sheet).

        Only unpauses if the user didn't also pause the job.
        """
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is not None and sheet.status == "fermata":
            sheet.status = "failed"
            self._propagate_failure_to_dependents(event.job_id, event.sheet_num)
        # Only unpause if the user hasn't independently paused the job
        if not job.user_paused:
            job.paused = False
        self._state_dirty = True

    def _handle_pause_job(self, event: PauseJob) -> None:
        """Pause dispatching for a job (user-initiated)."""
        job = self._jobs.get(event.job_id)
        if job is not None:
            job.paused = True
            job.user_paused = True
            self._state_dirty = True

    def _handle_resume_job(self, event: ResumeJob) -> None:
        """Resume dispatching for a paused job (user-initiated)."""
        job = self._jobs.get(event.job_id)
        if job is not None:
            job.paused = False
            job.user_paused = False
            self._state_dirty = True

    def _handle_cancel_job(self, event: CancelJob) -> None:
        """Cancel all sheets and deregister the job."""
        job = self._jobs.get(event.job_id)
        if job is not None:
            for sheet in job.sheets.values():
                if sheet.status not in _TERMINAL_STATUSES:
                    sheet.status = "cancelled"
            self.deregister_job(event.job_id)
            _logger.info(
                "baton.job.cancelled",
                extra={"job_id": event.job_id},
            )

    def _handle_shutdown(self, event: ShutdownRequested) -> None:
        """Begin shutdown."""
        self._shutting_down = True
        if not event.graceful:
            # Cancel all non-terminal sheets
            for job in self._jobs.values():
                for sheet in job.sheets.values():
                    if sheet.status not in _TERMINAL_STATUSES:
                        sheet.status = "cancelled"
        _logger.info(
            "baton.shutdown",
            extra={"graceful": event.graceful},
        )

    def _handle_process_exited(self, event: ProcessExited) -> None:
        """Backend process died — mark sheet as crashed if running."""
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is not None and sheet.status == "dispatched":
            sheet.normal_attempts += 1
            if sheet.normal_attempts >= sheet.max_retries:
                sheet.status = "failed"
                self._propagate_failure_to_dependents(event.job_id, event.sheet_num)
            else:
                sheet.status = "retry_scheduled"
            self._state_dirty = True

    # =========================================================================
    # Dependency Failure Propagation
    # =========================================================================

    def _propagate_failure_to_dependents(
        self, job_id: str, failed_sheet_num: int
    ) -> None:
        """Mark all transitive dependents of a failed sheet as failed.

        When a sheet fails (retries exhausted, auth failure, etc.), its
        dependent sheets can never be satisfied — they'd sit in pending
        forever, creating zombie jobs. This method cascades the failure
        to all downstream sheets using iterative BFS.

        Only non-terminal sheets are affected. Completed, skipped, and
        already-failed sheets are left unchanged.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return

        # Build a reverse dependency map: sheet_num → list of sheets that depend on it
        dependents: dict[int, list[int]] = {}
        for sheet_num, deps in job.dependencies.items():
            for dep in deps:
                if dep not in dependents:
                    dependents[dep] = []
                dependents[dep].append(sheet_num)

        # BFS from the failed sheet to all transitive dependents
        queue = list(dependents.get(failed_sheet_num, []))
        visited: set[int] = set()

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            sheet = job.sheets.get(current)
            if sheet is None:
                continue

            # Only fail non-terminal sheets
            if sheet.status not in _TERMINAL_STATUSES:
                sheet.status = "failed"
                _logger.info(
                    "baton.sheet.dependency_failed",
                    extra={
                        "job_id": job_id,
                        "sheet_num": current,
                        "failed_dependency": failed_sheet_num,
                    },
                )

            # Continue propagation to this sheet's dependents
            for downstream in dependents.get(current, []):
                if downstream not in visited:
                    queue.append(downstream)

        if visited:
            self._state_dirty = True

    # =========================================================================
    # Main Loop
    # =========================================================================

    async def run(self) -> None:
        """The baton's main event loop.

        Processes events from the inbox, updates state, and dispatches
        ready sheets. Runs until a ShutdownRequested event is received.
        """
        self._running = True
        _logger.info("baton.started")

        try:
            while not self._shutting_down:
                event = await self._inbox.get()
                await self.handle_event(event)
                # dispatch_ready() would be called here when wired to conductor
        except asyncio.CancelledError:
            _logger.info("baton.cancelled")
            raise
        finally:
            self._running = False
            _logger.info("baton.stopped")

    # =========================================================================
    # Diagnostics
    # =========================================================================

    def get_diagnostics(self, job_id: str) -> dict[str, Any] | None:
        """Get diagnostic information for a job.

        Returns a dict with sheet counts by status, instrument state,
        and other debugging information. Returns None if job not found.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None

        status_counts: dict[str, int] = {}
        instruments_used: set[str] = set()
        for sheet in job.sheets.values():
            status_counts[sheet.status] = status_counts.get(sheet.status, 0) + 1
            instruments_used.add(sheet.instrument_name)

        return {
            "job_id": job_id,
            "paused": job.paused,
            "sheets": {
                "total": len(job.sheets),
                **status_counts,
            },
            "instruments_used": sorted(instruments_used),
        }
