"""BatonEvent types — the vocabulary of the conductor's execution heart.

Every event the baton can receive is defined here as a frozen dataclass.
Events are the baton's sole input mechanism — it sleeps until an event
arrives, processes it, checks for dispatchable sheets, and sleeps again.

Events come from five sources:

- **Musicians** — sheet execution results (SheetAttemptResult, SheetSkipped)
- **Timer wheel** — scheduled future events (RetryDue, RateLimitExpired,
  StaleCheck, CronTick, JobTimeout, EscalationTimeout, PacingComplete)
- **External commands** — CLI/IPC/dashboard (PauseJob, ResumeJob, CancelJob,
  ConfigReloaded, EscalationResolved, ShutdownRequested)
- **Observer** — runtime monitoring (ProcessExited, ResourceAnomaly)
- **Internal** — dispatch coordination (DispatchRetry)

Design decisions:

- All events are frozen dataclasses — immutable after creation, safe to
  pass between asyncio tasks without copying.
- BatonEvent is the union type used for inbox typing.
- Events carry only the data needed for the baton to make a decision.
  They do not carry the full execution output (that's in CheckpointState).
- Events are compatible with the existing ObserverEvent TypedDict format
  via the ``to_observer_event()`` method for EventBus integration.

See: ``docs/plans/2026-03-26-baton-design.md`` for the full architecture.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from marianne.daemon.types import ObserverEvent

# =============================================================================
# Musician Events — sheet execution results
# =============================================================================


@dataclass(frozen=True)
class SheetAttemptResult:
    """A musician reports the result of a single sheet attempt.

    This is the central event in the baton's event loop. The musician plays
    once and reports in full detail. The conductor (baton) decides what
    happens next — retry, completion mode, healing, escalation, or accept.

    Rate limits are NOT failures. When ``rate_limited`` is True, the baton
    re-queues the sheet for when the instrument recovers. No retry budget
    is consumed.
    """

    job_id: str
    sheet_num: int
    instrument_name: str
    attempt: int

    # Execution outcome
    execution_success: bool = True
    exit_code: int | None = None
    duration_seconds: float = 0.0

    # Validation results
    validations_passed: int = 0
    validations_total: int = 0
    validation_pass_rate: float = 0.0
    """Percentage of validations that passed (0.0-100.0).

    **CRITICAL CONTRACT (F-018):** Set to 100.0 when execution succeeds
    with no validation rules OR when all validations pass. The baton treats
    the default (0.0) as "all validations failed" and will retry.

    A musician that reports ``execution_success=True`` with
    ``validations_total=0`` but leaves this at 0.0 will trigger
    unnecessary retries until max_retries is exhausted.
    """
    validation_details: dict[str, Any] | None = None

    # Error classification (from ErrorClassifier)
    error_classification: str | None = None
    error_message: str | None = None

    # Rate limit signal — NOT a failure
    rate_limited: bool = False

    # Cost tracking — musician calculates, baton enforces limits
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    # Model that actually executed (may differ from config default)
    model_used: str | None = None

    # Truncated output for diagnostics (credential-redacted by musician)
    stdout_tail: str = ""
    stderr_tail: str = ""

    # Timestamp of when the attempt completed
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class SheetSkipped:
    """A sheet was skipped due to skip_when condition or start_sheet override.

    The baton propagates skip state to dependents — downstream sheets that
    depend on a skipped sheet receive a skip sentinel, not empty string.
    """

    job_id: str
    sheet_num: int
    reason: str
    timestamp: float = field(default_factory=time.time)


# =============================================================================
# Rate Limit Events — instrument-level, timer-based recovery
# =============================================================================


@dataclass(frozen=True)
class RateLimitHit:
    """An instrument hit a rate limit. NOT a failure — a tempo change.

    The baton marks the instrument as rate-limited and schedules a timer
    for recovery. Sheets targeting this instrument move to waiting state.
    Other instruments are completely unaffected.
    """

    instrument: str
    wait_seconds: float
    job_id: str
    sheet_num: int
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class RateLimitExpired:
    """Timer fired — check if the rate-limited instrument is available again.

    If the instrument is still unavailable, the baton schedules another
    timer. If available, sheets waiting on this instrument become ready.
    """

    instrument: str
    timestamp: float = field(default_factory=time.time)


# =============================================================================
# Timer Events — all timing flows through the timer wheel
# =============================================================================


@dataclass(frozen=True)
class RetryDue:
    """Timer fired — a previously failed sheet is ready for retry.

    The baton moves the sheet from retry-scheduled to ready state.
    The next dispatch cycle will pick it up.
    """

    job_id: str
    sheet_num: int
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class StaleCheck:
    """Timer fired — check if a running sheet has gone stale.

    If no output progress has been received within the configured idle
    timeout, the baton kills the stale sheet and reschedules or fails it.
    """

    job_id: str
    sheet_num: int
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CronTick:
    """Timer fired — a cron-scheduled job should be submitted.

    The baton submits the configured score as a new job and schedules
    the next tick. If a previous run is still active, this tick is skipped.
    """

    entry_name: str
    score_path: str
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class JobTimeout:
    """Timer fired — a job has exceeded its wall-clock time limit.

    The baton cancels all remaining sheets for this job.
    """

    job_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class PacingComplete:
    """Timer fired — the inter-sheet pacing delay for a job has elapsed.

    The baton clears the pacing flag, allowing the next sheet to dispatch.
    Implements ``pause_between_sheets_seconds`` from score config.
    """

    job_id: str
    timestamp: float = field(default_factory=time.time)


# =============================================================================
# Escalation Events — fermata system
# =============================================================================


@dataclass(frozen=True)
class EscalationNeeded:
    """A sheet execution requires composer judgment — enter fermata.

    The baton pauses the job's dispatch and notifies the composer
    (human or AI) via configured channels. A timeout timer is scheduled.
    """

    job_id: str
    sheet_num: int
    reason: str
    options: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class EscalationResolved:
    """The composer has made a decision on a fermata.

    The baton applies the decision and resumes dispatching for the job.
    Arrives via IPC (``job.resolve_escalation`` method).
    """

    job_id: str
    sheet_num: int
    decision: str
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class EscalationTimeout:
    """Timer fired — no escalation response received within the deadline.

    The baton defaults to the safe action: fail the sheet (not the job)
    and resume dispatching for other sheets.
    """

    job_id: str
    sheet_num: int
    timestamp: float = field(default_factory=time.time)


# =============================================================================
# External Command Events — CLI, IPC, dashboard
# =============================================================================


@dataclass(frozen=True)
class PauseJob:
    """Pause dispatching for a job. In-flight sheets continue to completion.

    No new sheets are dispatched until ResumeJob is received. Retry timers
    are preserved — when resumed, scheduled retries fire normally.
    """

    job_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ResumeJob:
    """Resume dispatching for a paused job, optionally with new config.

    When ``new_config`` is provided, pending sheets are rebuilt from the
    new config. Completed sheets are preserved. Failed sheets being
    retried use the new config for the retry.
    """

    job_id: str
    new_config: dict[str, Any] | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CancelJob:
    """Cancel all sheets for a job and deregister it from the baton.

    In-flight sheet tasks are cancelled. The job is marked as cancelled
    in CheckpointState.
    """

    job_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConfigReloaded:
    """Config has changed for a job (SIGHUP, ``mzt modify``, resume -c).

    The baton rebuilds pending sheets from the new config. Completed
    sheets are preserved. Cost limits may be reset if they changed.
    """

    job_id: str
    new_config: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ShutdownRequested:
    """The conductor is shutting down (SIGTERM, ``mzt stop``).

    When ``graceful`` is True, the baton waits for in-flight sheets
    to complete (up to the configured drain timeout) before stopping.
    When False, sheets are cancelled immediately.
    """

    graceful: bool = True
    timestamp: float = field(default_factory=time.time)


# =============================================================================
# Observer Events — runtime monitoring
# =============================================================================


@dataclass(frozen=True)
class ProcessExited:
    """Observer detected that a backend process died unexpectedly.

    The baton checks if this was a sheet's backend process and, if so,
    marks the sheet as crashed — faster than waiting for timeout.
    """

    job_id: str
    sheet_num: int
    pid: int
    exit_code: int | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ResourceAnomaly:
    """Observer/monitor detected a resource pressure event.

    Critical severity triggers backpressure — the baton stops dispatching
    new sheets and lets running sheets drain.
    """

    severity: str  # "warning", "critical"
    metric: str  # "memory", "cpu", "processes"
    value: float
    timestamp: float = field(default_factory=time.time)


# =============================================================================
# Instrument Fallback Events — fallback chain transitions
# =============================================================================


@dataclass(frozen=True)
class InstrumentFallback:
    """A sheet's instrument was switched to a fallback.

    Emitted when the baton moves a sheet from one instrument to the next
    in its fallback chain. Reasons:

    - ``"unavailable"`` — instrument not installed, binary missing, circuit
      breaker open. Immediate fallback at dispatch time.
    - ``"rate_limit_exhausted"`` — all retries on the current instrument
      hit rate limits with no recovery. Fallback after retry exhaustion.

    This is an INFO-level event. Fallback is the system working correctly,
    not failing. Each fallback instrument gets a fresh retry budget.
    """

    job_id: str
    sheet_num: int
    from_instrument: str
    to_instrument: str
    reason: str  # "unavailable" | "rate_limit_exhausted"
    timestamp: float = field(default_factory=time.time)


# =============================================================================
# Internal Events — dispatch coordination
# =============================================================================


@dataclass(frozen=True)
class DispatchRetry:
    """Internal signal to retry dispatch after a backpressure delay.

    When the baton encounters backpressure during dispatch, it schedules
    this event via the timer wheel rather than blocking.
    """

    timestamp: float = field(default_factory=time.time)


# =============================================================================
# Union type — the baton's inbox accepts any of these
# =============================================================================

BatonEvent = (
    SheetAttemptResult
    | SheetSkipped
    | RateLimitHit
    | RateLimitExpired
    | RetryDue
    | StaleCheck
    | CronTick
    | JobTimeout
    | PacingComplete
    | EscalationNeeded
    | EscalationResolved
    | EscalationTimeout
    | PauseJob
    | ResumeJob
    | CancelJob
    | ConfigReloaded
    | ShutdownRequested
    | ProcessExited
    | ResourceAnomaly
    | InstrumentFallback
    | DispatchRetry
)


# =============================================================================
# EventBus integration — convert baton events to ObserverEvent format
# =============================================================================


def to_observer_event(event: BatonEvent) -> ObserverEvent:
    """Convert a BatonEvent to the ObserverEvent TypedDict format.

    The baton publishes events to the EventBus so that the dashboard,
    learning hub, and notification system can consume them. Baton events
    use the ``baton.*`` namespace to distinguish from runner events.

    Returns a dict matching the ObserverEvent TypedDict shape:
    ``{job_id, sheet_num, event, data, timestamp}``
    """
    match event:
        case SheetAttemptResult():
            return {
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "event": "baton.sheet.attempt_result",
                "data": {
                    "instrument": event.instrument_name,
                    "model": event.model_used,
                    "attempt": event.attempt,
                    "success": event.execution_success,
                    "validation_pass_rate": event.validation_pass_rate,
                    "cost_usd": event.cost_usd,
                    "rate_limited": event.rate_limited,
                    "duration_seconds": event.duration_seconds,
                },
                "timestamp": event.timestamp,
            }

        case SheetSkipped():
            return {
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "event": "baton.sheet.skipped",
                "data": {"reason": event.reason},
                "timestamp": event.timestamp,
            }

        case RateLimitHit():
            return {
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "event": "baton.rate_limit.active",
                "data": {
                    "instrument": event.instrument,
                    "estimated_seconds": event.wait_seconds,
                },
                "timestamp": event.timestamp,
            }

        case RateLimitExpired():
            return {
                "job_id": "",
                "sheet_num": 0,
                "event": "baton.rate_limit.cleared",
                "data": {"instrument": event.instrument},
                "timestamp": event.timestamp,
            }

        case RetryDue():
            return {
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "event": "baton.sheet.retry_scheduled",
                "data": {},
                "timestamp": event.timestamp,
            }

        case StaleCheck():
            return {
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "event": "baton.sheet.stale_check",
                "data": {},
                "timestamp": event.timestamp,
            }

        case CronTick():
            return {
                "job_id": "",
                "sheet_num": 0,
                "event": "baton.cron.fired",
                "data": {
                    "entry_name": event.entry_name,
                    "score_path": event.score_path,
                },
                "timestamp": event.timestamp,
            }

        case JobTimeout():
            return {
                "job_id": event.job_id,
                "sheet_num": 0,
                "event": "baton.job.timeout",
                "data": {},
                "timestamp": event.timestamp,
            }

        case PacingComplete():
            return {
                "job_id": event.job_id,
                "sheet_num": 0,
                "event": "baton.pacing.complete",
                "data": {},
                "timestamp": event.timestamp,
            }

        case EscalationNeeded():
            return {
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "event": "baton.fermata",
                "data": {
                    "reason": event.reason,
                    "options": event.options,
                },
                "timestamp": event.timestamp,
            }

        case EscalationResolved():
            return {
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "event": "baton.fermata.resolved",
                "data": {"decision": event.decision},
                "timestamp": event.timestamp,
            }

        case EscalationTimeout():
            return {
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "event": "baton.fermata.timeout",
                "data": {},
                "timestamp": event.timestamp,
            }

        case PauseJob():
            return {
                "job_id": event.job_id,
                "sheet_num": 0,
                "event": "baton.job.paused",
                "data": {"reason": "user"},
                "timestamp": event.timestamp,
            }

        case ResumeJob():
            return {
                "job_id": event.job_id,
                "sheet_num": 0,
                "event": "baton.job.resumed",
                "data": {"config_changed": event.new_config is not None},
                "timestamp": event.timestamp,
            }

        case CancelJob():
            return {
                "job_id": event.job_id,
                "sheet_num": 0,
                "event": "baton.job.cancelled",
                "data": {},
                "timestamp": event.timestamp,
            }

        case ConfigReloaded():
            return {
                "job_id": event.job_id,
                "sheet_num": 0,
                "event": "baton.config.reloaded",
                "data": {},
                "timestamp": event.timestamp,
            }

        case ShutdownRequested():
            return {
                "job_id": "",
                "sheet_num": 0,
                "event": "baton.shutdown.requested",
                "data": {"graceful": event.graceful},
                "timestamp": event.timestamp,
            }

        case ProcessExited():
            return {
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "event": "baton.process.exited",
                "data": {
                    "pid": event.pid,
                    "exit_code": event.exit_code,
                },
                "timestamp": event.timestamp,
            }

        case ResourceAnomaly():
            return {
                "job_id": "",
                "sheet_num": 0,
                "event": "baton.resource.anomaly",
                "data": {
                    "severity": event.severity,
                    "metric": event.metric,
                    "value": event.value,
                },
                "timestamp": event.timestamp,
            }

        case InstrumentFallback():
            return {
                "job_id": event.job_id,
                "sheet_num": event.sheet_num,
                "event": "baton.instrument.fallback",
                "data": {
                    "from_instrument": event.from_instrument,
                    "to_instrument": event.to_instrument,
                    "reason": event.reason,
                },
                "timestamp": event.timestamp,
            }

        case DispatchRetry():
            return {
                "job_id": "",
                "sheet_num": 0,
                "event": "baton.dispatch.retry",
                "data": {},
                "timestamp": event.timestamp,
            }

    # Unreachable — the match is exhaustive over the union type.
    # If a new event type is added to BatonEvent without a case here,
    # mypy will flag it via type narrowing.
    msg = f"Unknown baton event type: {type(event).__name__}"
    raise ValueError(msg)
