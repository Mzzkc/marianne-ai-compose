"""The Baton — Mozart's event-driven execution heart.

The baton is the conductor's primary tool. It doesn't decide what to play —
the score does. It doesn't decide how to play — the musicians do. The baton
controls **when** and **how much**: tempo, dynamics, cues, and fermatas.

The baton replaces the current monolithic execution model where
``JobService.start_job()`` runs all sheets sequentially. Instead, the baton
manages sheets across all jobs in a single event-driven loop, dispatching
them to execution when they're ready and the system can handle them.

Package layout::

    events.py   — All BatonEvent types (dataclasses)
    (future)    — timer.py, state.py, dispatch.py, core.py
"""

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

__all__ = [
    "BatonEvent",
    "CancelJob",
    "ConfigReloaded",
    "CronTick",
    "DispatchRetry",
    "EscalationNeeded",
    "EscalationResolved",
    "EscalationTimeout",
    "JobTimeout",
    "PacingComplete",
    "PauseJob",
    "ProcessExited",
    "RateLimitExpired",
    "RateLimitHit",
    "ResourceAnomaly",
    "ResumeJob",
    "RetryDue",
    "SheetAttemptResult",
    "SheetSkipped",
    "ShutdownRequested",
    "StaleCheck",
]
