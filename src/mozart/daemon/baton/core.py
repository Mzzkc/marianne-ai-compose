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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mozart.daemon.baton.dispatch import DispatchConfig

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
from mozart.daemon.baton.state import (
    _DISPATCHABLE_BATON_STATUSES,
    _SATISFIED_BATON_STATUSES,
    _TERMINAL_BATON_STATUSES,
    BatonSheetStatus,
    CircuitBreakerState,
    InstrumentState,
    SheetExecutionState,
)

_logger = logging.getLogger(__name__)


@dataclass
class _JobRecord:
    """Internal tracking for a registered job."""

    job_id: str
    sheets: dict[int, SheetExecutionState]
    dependencies: dict[int, list[int]]
    paused: bool = False
    user_paused: bool = False  # Tracks user-initiated pause (PauseJob)
    escalation_enabled: bool = False  # Enter fermata on exhaustion
    self_healing_enabled: bool = False  # Try healing on exhaustion
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

    _DEFAULT_INSTRUMENT_CONCURRENCY: int = 4
    """Default max concurrent sheets per instrument when not specified."""

    _DEFAULT_MAX_HEALING: int = 1
    """Default maximum healing attempts before falling through."""

    def __init__(
        self,
        *,
        timer: Any | None = None,
    ) -> None:
        """Initialize the baton core.

        Args:
            timer: Optional TimerWheel for scheduling retry delays.
                When None, retries are set to RETRY_SCHEDULED without
                actual timer events (tests or manual event injection).
        """
        self._inbox: asyncio.Queue[BatonEvent] = asyncio.Queue()
        self._jobs: dict[str, _JobRecord] = {}
        self._instruments: dict[str, InstrumentState] = {}
        self._job_cost_limits: dict[str, float] = {}
        self._sheet_cost_limits: dict[tuple[str, int], float] = {}
        self._shutting_down = False
        self._running = False
        self._state_dirty = False
        self._timer = timer

        # Retry backoff configuration (from RetryConfig defaults)
        self._base_retry_delay: float = 10.0
        self._retry_exponential_base: float = 2.0
        self._max_retry_delay: float = 3600.0

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
            if sheet.status == BatonSheetStatus.DISPATCHED
        )

    # =========================================================================
    # Instrument Registry
    # =========================================================================

    def register_instrument(
        self, name: str, *, max_concurrent: int = 4
    ) -> InstrumentState:
        """Register an instrument for tracking.

        If already registered, returns the existing state (idempotent).

        Args:
            name: Instrument name (matches InstrumentProfile.name).
            max_concurrent: Maximum concurrent sheets on this instrument.

        Returns:
            The InstrumentState for the instrument.
        """
        if name in self._instruments:
            return self._instruments[name]

        state = InstrumentState(name=name, max_concurrent=max_concurrent)
        self._instruments[name] = state
        _logger.debug(
            "baton.instrument_registered",
            extra={"instrument": name, "max_concurrent": max_concurrent},
        )
        return state

    def get_instrument_state(self, name: str) -> InstrumentState | None:
        """Get the tracking state for a specific instrument."""
        return self._instruments.get(name)

    def build_dispatch_config(
        self, *, max_concurrent_sheets: int = 10
    ) -> DispatchConfig:
        """Build a DispatchConfig from the current instrument state.

        This bridges the gap between the baton's instrument tracking
        and the dispatch logic's configuration needs. Called before
        each dispatch cycle.

        Args:
            max_concurrent_sheets: Global concurrency ceiling.

        Returns:
            DispatchConfig with rate-limited instruments, open circuit
            breakers, and per-instrument concurrency limits derived
            from the current InstrumentState.
        """
        # Deferred import to break circular dependency
        # (dispatch.py imports BatonCore at runtime)
        from mozart.daemon.baton.dispatch import DispatchConfig  # noqa: N814

        rate_limited: set[str] = set()
        open_breakers: set[str] = set()
        concurrency: dict[str, int] = {}

        for name, inst in self._instruments.items():
            if inst.rate_limited:
                rate_limited.add(name)
            if inst.circuit_breaker == CircuitBreakerState.OPEN:
                open_breakers.add(name)
            concurrency[name] = inst.max_concurrent

        return DispatchConfig(
            max_concurrent_sheets=max_concurrent_sheets,
            instrument_concurrency=concurrency,
            rate_limited_instruments=rate_limited,
            open_circuit_breakers=open_breakers,
        )

    def set_job_cost_limit(self, job_id: str, max_cost_usd: float) -> None:
        """Set a per-job cost limit. The baton pauses the job when exceeded.

        Args:
            job_id: The job to set the limit for.
            max_cost_usd: Maximum total cost in USD.
        """
        self._job_cost_limits[job_id] = max_cost_usd

    def get_rate_limited_instruments(self) -> set[str]:
        """Get the set of currently rate-limited instrument names.

        Used by dispatch logic to skip rate-limited instruments.
        """
        return {
            name for name, inst in self._instruments.items()
            if inst.rate_limited
        }

    def clear_instrument_rate_limit(
        self,
        instrument: str | None = None,
    ) -> int:
        """Clear rate limit state on one or all instruments.

        Resets ``rate_limited`` to ``False`` and ``rate_limit_expires_at``
        to ``None``.  Also moves any WAITING sheets on the cleared
        instrument(s) back to PENDING so they can be re-dispatched.

        Args:
            instrument: Instrument name to clear, or ``None`` for all.

        Returns:
            Number of instruments whose rate limit was cleared.
        """
        cleared = 0
        targets = (
            [self._instruments[instrument]]
            if instrument and instrument in self._instruments
            else list(self._instruments.values())
        )
        for inst in targets:
            if inst.rate_limited:
                inst.rate_limited = False
                inst.rate_limit_expires_at = None
                cleared += 1
                # Move WAITING sheets on this instrument back to PENDING
                for job in self._jobs.values():
                    for sheet in job.sheets.values():
                        if (
                            sheet.status == BatonSheetStatus.WAITING
                            and sheet.instrument_name == inst.name
                        ):
                            sheet.status = BatonSheetStatus.PENDING
        if cleared > 0:
            self._state_dirty = True
        return cleared

    def get_open_circuit_breakers(self) -> set[str]:
        """Get the set of instruments with open circuit breakers.

        Used by dispatch logic to skip unhealthy instruments.
        """
        return {
            name for name, inst in self._instruments.items()
            if inst.circuit_breaker == CircuitBreakerState.OPEN
        }

    def _auto_register_instruments(
        self, sheets: dict[int, SheetExecutionState]
    ) -> None:
        """Auto-register instruments for any sheets using untracked instruments."""
        for sheet in sheets.values():
            if sheet.instrument_name not in self._instruments:
                self.register_instrument(
                    sheet.instrument_name,
                    max_concurrent=self._DEFAULT_INSTRUMENT_CONCURRENCY,
                )

    def _update_instrument_on_success(self, instrument_name: str) -> None:
        """Record a successful execution on an instrument."""
        inst = self._instruments.get(instrument_name)
        if inst is not None:
            inst.record_success()

    def _update_instrument_on_failure(self, instrument_name: str) -> None:
        """Record a failed execution on an instrument."""
        inst = self._instruments.get(instrument_name)
        if inst is not None:
            inst.record_failure()

    def _check_job_cost_limit(self, job_id: str) -> None:
        """Check if a job has exceeded its cost limit and pause if so."""
        limit = self._job_cost_limits.get(job_id)
        if limit is None:
            return

        job = self._jobs.get(job_id)
        if job is None:
            return

        total_cost = sum(s.total_cost_usd for s in job.sheets.values())
        if total_cost > limit:
            job.paused = True
            self._state_dirty = True
            _logger.warning(
                "baton.job.cost_limit_exceeded",
                extra={
                    "job_id": job_id,
                    "total_cost": total_cost,
                    "limit": limit,
                },
            )

    def set_sheet_cost_limit(
        self, job_id: str, sheet_num: int, max_cost_usd: float
    ) -> None:
        """Set a per-sheet cost limit. The baton fails the sheet when exceeded.

        Args:
            job_id: The job containing the sheet.
            sheet_num: The sheet number.
            max_cost_usd: Maximum cost in USD for this sheet.
        """
        self._sheet_cost_limits[(job_id, sheet_num)] = max_cost_usd

    def _check_sheet_cost_limit(
        self, job_id: str, sheet_num: int, sheet: SheetExecutionState
    ) -> bool:
        """Check if a sheet has exceeded its cost limit.

        Returns:
            True if the sheet exceeded its cost limit and was failed.
        """
        limit = self._sheet_cost_limits.get((job_id, sheet_num))
        if limit is None:
            return False

        if sheet.total_cost_usd > limit:
            sheet.status = BatonSheetStatus.FAILED
            self._state_dirty = True
            _logger.warning(
                "baton.sheet.cost_limit_exceeded",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                    "total_cost": sheet.total_cost_usd,
                    "limit": limit,
                },
            )
            self._propagate_failure_to_dependents(job_id, sheet_num)
            return True
        return False

    def calculate_retry_delay(self, attempt: int) -> float:
        """Calculate retry delay using exponential backoff.

        Args:
            attempt: 0-based attempt index (0 = first retry).

        Returns:
            Delay in seconds, clamped to ``_max_retry_delay``.
        """
        delay = self._base_retry_delay * (
            self._retry_exponential_base ** attempt
        )
        return min(delay, self._max_retry_delay)

    def _schedule_retry(
        self, job_id: str, sheet_num: int, sheet: SheetExecutionState
    ) -> None:
        """Schedule a retry via the timer wheel with backoff delay.

        Sets the sheet to RETRY_SCHEDULED. If a timer wheel is available,
        schedules a RetryDue event. Otherwise, the sheet sits in
        RETRY_SCHEDULED until a RetryDue is manually injected.

        The backoff delay is based on the number of normal attempts so far.
        """
        sheet.status = BatonSheetStatus.RETRY_SCHEDULED
        self._state_dirty = True

        attempt_index = max(0, sheet.normal_attempts - 1)
        delay = self.calculate_retry_delay(attempt_index)

        if self._timer is not None:
            event = RetryDue(job_id=job_id, sheet_num=sheet_num)
            self._timer.schedule(delay, event)
            sheet.next_retry_at = time.monotonic() + delay
            _logger.info(
                "baton.retry.scheduled",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                    "delay_seconds": delay,
                    "attempt": sheet.normal_attempts,
                },
            )

    def _handle_exhaustion(
        self, job_id: str, sheet_num: int, sheet: SheetExecutionState
    ) -> None:
        """Handle retry/completion budget exhaustion.

        The decision tree when budgets are exhausted:
        1. Self-healing enabled → schedule a healing attempt
        2. Escalation enabled → enter FERMATA (pause job, await decision)
        3. Neither → FAILED (propagate to dependents)
        """
        job = self._jobs.get(job_id)
        if job is None:
            sheet.status = BatonSheetStatus.FAILED
            self._state_dirty = True
            return

        # Path 1: Self-healing — try to diagnose and fix
        if (
            job.self_healing_enabled
            and sheet.healing_attempts < self._DEFAULT_MAX_HEALING
        ):
            sheet.healing_attempts += 1
            self._schedule_retry(job_id, sheet_num, sheet)
            _logger.info(
                "baton.sheet.healing_attempt",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                    "healing_attempt": sheet.healing_attempts,
                },
            )
            return

        # Path 2: Escalation — pause for composer decision
        if job.escalation_enabled:
            sheet.status = BatonSheetStatus.FERMATA
            job.paused = True
            self._state_dirty = True
            _logger.info(
                "baton.sheet.escalated",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                    "normal_attempts": sheet.normal_attempts,
                    "healing_attempts": sheet.healing_attempts,
                },
            )
            return

        # Path 3: No recovery — fail
        sheet.status = BatonSheetStatus.FAILED
        self._state_dirty = True
        _logger.warning(
            "baton.sheet.retries_exhausted",
            extra={
                "job_id": job_id,
                "sheet_num": sheet_num,
                "attempts": sheet.normal_attempts,
            },
        )
        self._propagate_failure_to_dependents(job_id, sheet_num)

    # =========================================================================
    # Sheet Registry
    # =========================================================================

    def register_job(
        self,
        job_id: str,
        sheets: dict[int, SheetExecutionState],
        dependencies: dict[int, list[int]],
        *,
        escalation_enabled: bool = False,
        self_healing_enabled: bool = False,
    ) -> None:
        """Register a job's sheets with the baton for scheduling.

        Args:
            job_id: Unique job identifier.
            sheets: Map of sheet_num → SheetExecutionState.
            dependencies: Map of sheet_num → list of dependency sheet_nums.
                Sheets not in this map have no dependencies.
            escalation_enabled: Whether to enter fermata on exhaustion.
            self_healing_enabled: Whether to try healing on exhaustion.
        """
        if job_id in self._jobs:
            _logger.warning(
                "baton.register_job.duplicate",
                extra={"job_id": job_id},
            )
            return

        # Auto-register any instruments used by the job's sheets
        self._auto_register_instruments(sheets)

        self._jobs[job_id] = _JobRecord(
            job_id=job_id,
            sheets=sheets,
            dependencies=dependencies,
            escalation_enabled=escalation_enabled,
            self_healing_enabled=self_healing_enabled,
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
        """Remove a job from the baton's tracking.

        Cleans up all per-job state including cost limit entries to
        prevent memory leaks in long-running conductors (F-062).
        """
        if job_id in self._jobs:
            del self._jobs[job_id]
            # F-062: Clean up cost limit dicts to prevent memory leaks
            self._job_cost_limits.pop(job_id, None)
            # Remove sheet cost limits for this job
            sheet_keys_to_remove = [
                key for key in self._sheet_cost_limits
                if key[0] == job_id
            ]
            for key in sheet_keys_to_remove:
                del self._sheet_cost_limits[key]
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
            sheet.status in _TERMINAL_BATON_STATUSES
            for sheet in job.sheets.values()
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
            if sheet.status not in _DISPATCHABLE_BATON_STATUSES:
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
        return dep_sheet.status in _SATISFIED_BATON_STATUSES

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
                    _logger.warning(
                        "baton.event.unimplemented",
                        extra={"event_type": "StaleCheck"},
                    )

                case CronTick():
                    _logger.warning(
                        "baton.event.unimplemented",
                        extra={"event_type": "CronTick"},
                    )

                case JobTimeout():
                    self._handle_job_timeout(event)

                case PacingComplete():
                    _logger.warning(
                        "baton.event.unimplemented",
                        extra={"event_type": "PacingComplete"},
                    )

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
                    _logger.warning(
                        "baton.event.unimplemented",
                        extra={"event_type": "ConfigReloaded"},
                    )

                case ShutdownRequested():
                    self._handle_shutdown(event)

                # === Observer events ===
                case ProcessExited():
                    self._handle_process_exited(event)

                case ResourceAnomaly():
                    _logger.warning(
                        "baton.event.unimplemented",
                        extra={"event_type": "ResourceAnomaly"},
                    )

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
        if sheet.status in _TERMINAL_BATON_STATUSES:
            _logger.debug(
                "baton.attempt_result.terminal_noop",
                extra={
                    "job_id": event.job_id,
                    "sheet_num": event.sheet_num,
                    "status": sheet.status.value,
                },
            )
            return

        # Record the attempt (tracks cost, duration, and increments
        # normal_attempts for non-rate-limited results).
        sheet.record_attempt(event)

        if event.rate_limited:
            # Rate limit — NOT a failure. record_attempt() already
            # skipped normal_attempts increment for rate-limited results.
            sheet.status = BatonSheetStatus.WAITING
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
            sheet.status = BatonSheetStatus.COMPLETED
            self._update_instrument_on_success(event.instrument_name)
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
            # Cost enforcement: even on success, check if job cost exceeded
            self._check_job_cost_limit(event.job_id)
            return

        if event.execution_success and effective_pass_rate > 0:
            # Partial validation pass — completion mode
            # The musician succeeded in execution but some validations failed.
            # Try re-dispatching with "finish your work" context.
            self._update_instrument_on_success(event.instrument_name)
            sheet.completion_attempts += 1
            if sheet.can_complete:
                sheet.status = BatonSheetStatus.PENDING
                self._state_dirty = True
                _logger.info(
                    "baton.sheet.completion_mode",
                    extra={
                        "job_id": event.job_id,
                        "sheet_num": event.sheet_num,
                        "pass_rate": effective_pass_rate,
                        "completion_attempt": sheet.completion_attempts,
                        "max_completion": sheet.max_completion,
                    },
                )
            else:
                # Completion budget exhausted — escalate, heal, or fail
                _logger.warning(
                    "baton.sheet.completion_exhausted",
                    extra={
                        "job_id": event.job_id,
                        "sheet_num": event.sheet_num,
                        "completion_attempts": sheet.completion_attempts,
                    },
                )
                self._handle_exhaustion(
                    event.job_id, event.sheet_num, sheet
                )
            self._check_job_cost_limit(event.job_id)
            return

        if not event.execution_success:
            # Execution failed — update instrument failure tracking
            self._update_instrument_on_failure(event.instrument_name)

            if event.error_classification == "AUTH_FAILURE":
                sheet.status = BatonSheetStatus.FAILED
                self._state_dirty = True
                _logger.error(
                    "baton.sheet.auth_failure",
                    extra={
                        "job_id": event.job_id,
                        "sheet_num": event.sheet_num,
                    },
                )
                self._propagate_failure_to_dependents(
                    event.job_id, event.sheet_num
                )
                self._check_job_cost_limit(event.job_id)
                return

        # F-065: execution_success + 0% validation is a complete validation
        # failure. record_attempt() only counts execution failures toward
        # retry budget, so we must manually count this case. Without this,
        # the sheet retries forever since normal_attempts never increments.
        if event.execution_success and effective_pass_rate == 0:
            sheet.normal_attempts += 1

        # Per-sheet cost enforcement — fail before retrying if over budget
        if self._check_sheet_cost_limit(event.job_id, event.sheet_num, sheet):
            self._check_job_cost_limit(event.job_id)
            return

        # Validation failure (pass_rate == 0) or execution failure
        # Check if retries exhausted (record_attempt already incremented)
        if not sheet.can_retry:
            self._handle_exhaustion(event.job_id, event.sheet_num, sheet)
        else:
            # Schedule retry via timer wheel with backoff
            self._schedule_retry(event.job_id, event.sheet_num, sheet)

        self._check_job_cost_limit(event.job_id)

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
        if sheet.status in _TERMINAL_BATON_STATUSES:
            _logger.debug(
                "baton.sheet_skipped.terminal_noop",
                extra={
                    "job_id": event.job_id,
                    "sheet_num": event.sheet_num,
                    "status": sheet.status.value,
                },
            )
            return
        sheet.status = BatonSheetStatus.SKIPPED
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

        Updates both the per-sheet status (to WAITING for dispatched/running
        sheets) and the per-instrument state (rate_limited=True). This ensures
        the dispatch logic knows to skip this instrument for all jobs.

        Only transition sheets that are currently dispatched or running.
        Pending/ready sheets haven't been sent to an instrument yet;
        terminal sheets must never regress.
        """
        # Update instrument-level state
        inst = self._instruments.get(event.instrument)
        if inst is not None:
            inst.rate_limited = True
            inst.rate_limit_expires_at = time.monotonic() + event.wait_seconds

        # Update sheet-level state — ALL dispatched/running sheets on this
        # instrument across ALL jobs move to waiting, not just the one that
        # triggered the rate limit. Rate limits are per-instrument, not per-sheet.
        for job in self._jobs.values():
            for sheet in job.sheets.values():
                if (
                    sheet.instrument_name == event.instrument
                    and sheet.status in (
                        BatonSheetStatus.DISPATCHED,
                        BatonSheetStatus.RUNNING,
                    )
                ):
                    sheet.status = BatonSheetStatus.WAITING
        self._state_dirty = True

    def _handle_rate_limit_expired(self, event: RateLimitExpired) -> None:
        """Rate limit cleared — unmark instrument and move waiting sheets back to pending.

        Clears the instrument-level rate limit flag so the dispatch logic
        will resume dispatching sheets to this instrument.
        """
        # Clear instrument-level rate limit
        inst = self._instruments.get(event.instrument)
        if inst is not None:
            inst.rate_limited = False
            inst.rate_limit_expires_at = None

        # Move waiting sheets back to pending
        for job in self._jobs.values():
            for sheet in job.sheets.values():
                if (
                    sheet.status == BatonSheetStatus.WAITING
                    and sheet.instrument_name == event.instrument
                ):
                    sheet.status = BatonSheetStatus.PENDING
        self._state_dirty = True

    def _handle_retry_due(self, event: RetryDue) -> None:
        """Timer fired — sheet is ready for retry."""
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is not None and sheet.status == BatonSheetStatus.RETRY_SCHEDULED:
            sheet.status = BatonSheetStatus.PENDING
            self._state_dirty = True

    def _handle_job_timeout(self, event: JobTimeout) -> None:
        """Job wall-clock timeout — cancel remaining sheets."""
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        for sheet in job.sheets.values():
            if sheet.status not in _TERMINAL_BATON_STATUSES:
                sheet.status = BatonSheetStatus.CANCELLED
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
        if sheet is not None and sheet.status not in _TERMINAL_BATON_STATUSES:
            sheet.status = BatonSheetStatus.FERMATA
        job.paused = True
        self._state_dirty = True

    def _handle_escalation_resolved(self, event: EscalationResolved) -> None:
        """Composer made a decision on a fermata.

        Only unpauses the job if:
        1. The user didn't also pause it (user_paused)
        2. No other sheets are still in FERMATA (F-066)
        3. Cost limits aren't exceeded (F-067)
        """
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is not None and sheet.status == BatonSheetStatus.FERMATA:
            # Apply decision
            if event.decision == "retry":
                sheet.status = BatonSheetStatus.PENDING
            elif event.decision == "skip":
                sheet.status = BatonSheetStatus.SKIPPED
            elif event.decision == "accept":
                sheet.status = BatonSheetStatus.COMPLETED
            else:
                sheet.status = BatonSheetStatus.FAILED
                self._propagate_failure_to_dependents(
                    event.job_id, event.sheet_num
                )
        # F-066: Only unpause if no sheets are still in FERMATA.
        # F-067: Re-check cost limits after unpausing.
        if not job.user_paused:
            any_fermata = any(
                s.status == BatonSheetStatus.FERMATA
                for s in job.sheets.values()
            )
            if not any_fermata:
                job.paused = False
                # F-067: re-check cost limits — may re-pause
                self._check_job_cost_limit(event.job_id)
        self._state_dirty = True

    def _handle_escalation_timeout(self, event: EscalationTimeout) -> None:
        """No escalation response — default to safe action (fail sheet).

        Only unpauses if:
        1. The user didn't also pause it (user_paused)
        2. No other sheets are still in FERMATA (F-066)
        3. Cost limits aren't exceeded (F-067)
        """
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is not None and sheet.status == BatonSheetStatus.FERMATA:
            sheet.status = BatonSheetStatus.FAILED
            self._propagate_failure_to_dependents(
                event.job_id, event.sheet_num
            )
        # F-066: Only unpause if no sheets are still in FERMATA.
        # F-067: Re-check cost limits after unpausing.
        if not job.user_paused:
            any_fermata = any(
                s.status == BatonSheetStatus.FERMATA
                for s in job.sheets.values()
            )
            if not any_fermata:
                job.paused = False
                # F-067: re-check cost limits — may re-pause
                self._check_job_cost_limit(event.job_id)
        self._state_dirty = True

    def _handle_pause_job(self, event: PauseJob) -> None:
        """Pause dispatching for a job (user-initiated)."""
        job = self._jobs.get(event.job_id)
        if job is not None:
            job.paused = True
            job.user_paused = True
            self._state_dirty = True

    def _handle_resume_job(self, event: ResumeJob) -> None:
        """Resume dispatching for a paused job (user-initiated).

        After clearing the user pause, re-checks cost limits (F-140).
        Without this, a cost-paused job would resume and dispatch sheets
        before the next attempt result triggers a cost re-check. This is
        the same pattern as F-067 (escalation unpause overrides cost pause).
        """
        job = self._jobs.get(event.job_id)
        if job is not None:
            job.paused = False
            job.user_paused = False
            # F-140: Re-check cost limits — may re-pause if cost exceeded.
            # Without this, one dispatch cycle can bypass cost enforcement.
            self._check_job_cost_limit(event.job_id)
            self._state_dirty = True

    def _handle_cancel_job(self, event: CancelJob) -> None:
        """Cancel all sheets and deregister the job."""
        job = self._jobs.get(event.job_id)
        if job is not None:
            for sheet in job.sheets.values():
                if sheet.status not in _TERMINAL_BATON_STATUSES:
                    sheet.status = BatonSheetStatus.CANCELLED
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
                    if sheet.status not in _TERMINAL_BATON_STATUSES:
                        sheet.status = BatonSheetStatus.CANCELLED
        _logger.info(
            "baton.shutdown",
            extra={"graceful": event.graceful},
        )

    def _handle_process_exited(self, event: ProcessExited) -> None:
        """Backend process died — mark sheet as crashed if running.

        Process crashes are treated like execution failures: they consume
        retry budget and route through the same exhaustion/healing/escalation
        paths as regular failures.

        F-063: Uses record_attempt() to maintain the single-point-of-accounting
        invariant. A synthetic SheetAttemptResult preserves cost/duration tracking
        and ensures crash attempts appear in the attempt history.
        """
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is not None and sheet.status == BatonSheetStatus.DISPATCHED:
            # F-063: Create a synthetic attempt result for the crash.
            # record_attempt() handles cost, duration, and attempt counting.
            crash_result = SheetAttemptResult(
                job_id=event.job_id,
                sheet_num=event.sheet_num,
                instrument_name=sheet.instrument_name,
                attempt=sheet.normal_attempts + 1,
                execution_success=False,
                exit_code=event.exit_code,
                duration_seconds=0.0,
                cost_usd=0.0,
                error_classification="PROCESS_CRASH",
                error_message=f"Backend process {event.pid} exited unexpectedly"
                + (f" with code {event.exit_code}" if event.exit_code is not None else ""),
            )
            sheet.record_attempt(crash_result)
            self._update_instrument_on_failure(sheet.instrument_name)
            if not sheet.can_retry:
                self._handle_exhaustion(
                    event.job_id, event.sheet_num, sheet
                )
            else:
                self._schedule_retry(event.job_id, event.sheet_num, sheet)
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

        # Build a reverse dependency map: sheet_num → list of sheets
        # that depend on it
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
            if sheet.status not in _TERMINAL_BATON_STATUSES:
                sheet.status = BatonSheetStatus.FAILED
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
                # dispatch_ready() would be called here when wired to
                # conductor
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
            status_key = sheet.status.value
            status_counts[status_key] = status_counts.get(status_key, 0) + 1
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
