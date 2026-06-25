"""Daemon-level mirror of instrument rate limits.

Cross-job dispatch backoff does NOT live here — it is structural in the
baton: ``BatonCore._instruments`` is shared by every job, so a
``RateLimitHit`` from any job marks the instrument baton-wide, moves all
jobs' affected sheets to WAITING, and blocks dispatch until the baton's
own ``RateLimitExpired`` timer fires. The baton is the single authority
for dispatch decisions.

This coordinator is the daemon-level OBSERVABILITY mirror of that state,
fed by a write-through in the baton adapter's run loop
(``BatonAdapter._report_rate_limit_cross_job`` →
``JobManager._on_rate_limit`` → ``report_rate_limit()``, #206).
Consumers:

- ``BackpressureController`` — escalates pressure to HIGH while any
  instrument is rate-limited;
- submit-time warnings (``JobManager.submit_job``) — "X clears in Ns";
- ``GlobalSheetScheduler`` via ``is_rate_limited()`` — built and tested
  but not yet driving execution (#238).

Keys are instrument NAMES — the same key space the baton's
``InstrumentState`` uses and that ``mzt clear-rate-limits --instrument``
passes (the manager clears the baton and this mirror with the same
string). Do not key by provider/backend type: that would split the key
space and break the shared clear contract.

Satisfies the ``RateLimitChecker`` protocol defined in ``scheduler.py``
so the ``GlobalSheetScheduler`` can query limits before dispatching.

Lock ordering (daemon-wide):
  1. GlobalSheetScheduler._lock
  2. RateLimitCoordinator._lock   ← this module
  3. BackpressureController  (lock-free — reads are atomic)
  4. CentralLearningStore._lock    (future — Stage 5)
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass

from marianne.core.constants import RESET_TIME_MAXIMUM_WAIT_SECONDS
from marianne.core.logging import get_logger

_logger = get_logger("daemon.rate_coordinator")

# Maximum wait that report_rate_limit() will accept.  Anything above
# this is clamped — prevents misparsed backend responses from blocking
# all jobs on a backend for unreasonable durations.
MAX_WAIT_SECONDS: float = RESET_TIME_MAXIMUM_WAIT_SECONDS


@dataclass
class RateLimitEvent:
    """A single rate limit event reported by a job."""

    instrument: str
    detected_at: float
    suggested_wait_seconds: float
    job_id: str
    sheet_num: int


class RateLimitCoordinator:
    """In-memory daemon-level mirror of instrument rate limits.

    Fed by the baton adapter's write-through (#206): every
    ``RateLimitHit`` the baton processes is reported here via
    ``JobManager._on_rate_limit``. The baton itself already enforces
    cross-job dispatch backoff (its instrument state is baton-wide);
    this mirror exists so daemon-level consumers — backpressure,
    submit-time warnings, the scheduler — see the same limits.

    Keys are instrument names; entries self-expire via per-entry
    resume times (``active_limits`` filters, ``prune_stale`` removes).

    The async ``is_rate_limited`` method satisfies the
    ``RateLimitChecker`` protocol so the scheduler can consult
    limits before dispatching sheets.
    """

    def __init__(self) -> None:
        self._events: list[RateLimitEvent] = []
        self._active_limits: dict[str, float] = {}  # instrument → resume_at (monotonic)
        self._lock = asyncio.Lock()

    # ─── Reporting ─────────────────────────────────────────────────

    async def report_rate_limit(
        self,
        instrument: str,
        wait_seconds: float,
        job_id: str,
        sheet_num: int,
    ) -> None:
        """Record a rate limit hit for an instrument.

        If a limit is already active for the instrument, the resume time
        is extended to whichever is later (existing or newly reported).

        Args:
            instrument: Instrument that was rate-limited (e.g.
                ``"claude-code"`` — the same name the baton's
                InstrumentState and ``mzt clear-rate-limits`` use).
            wait_seconds: Suggested wait duration in seconds.
            job_id: Job that encountered the limit.
            sheet_num: Sheet that triggered the limit.
        """
        # Guard against NaN/inf from misparsed backend responses.
        # math.isfinite returns False for NaN, inf, and -inf.
        if not math.isfinite(wait_seconds):
            _logger.warning(
                "rate_limit.invalid_wait_seconds",
                wait_seconds=wait_seconds,
                job_id=job_id,
                sheet_num=sheet_num,
                msg="Clamping non-finite wait_seconds to 0",
            )
            wait_seconds = 0.0

        # Clamp to [0, MAX_WAIT_SECONDS] — negative or zero values are
        # no-ops for the resume time, and huge values are capped.
        wait_seconds = max(0.0, min(wait_seconds, MAX_WAIT_SECONDS))

        now = time.monotonic()
        async with self._lock:
            resume_at = now + wait_seconds
            self._active_limits[instrument] = max(
                self._active_limits.get(instrument, 0.0),
                resume_at,
            )
            self._events.append(RateLimitEvent(
                instrument=instrument,
                detected_at=now,
                suggested_wait_seconds=wait_seconds,
                job_id=job_id,
                sheet_num=sheet_num,
            ))
            # Prune events older than 1 hour
            cutoff = now - 3600
            self._events = [e for e in self._events if e.detected_at > cutoff]

        _logger.warning(
            "rate_limit.reported",
            instrument=instrument,
            wait_seconds=wait_seconds,
            job_id=job_id,
            sheet_num=sheet_num,
        )

    # ─── Querying (satisfies RateLimitChecker protocol) ────────────

    async def is_rate_limited(
        self,
        instrument: str,
        model: str | None = None,
    ) -> tuple[bool, float]:
        """Check if an instrument is currently rate-limited.

        Satisfies the ``RateLimitChecker`` protocol used by
        ``GlobalSheetScheduler.next_sheet()``.

        The ``model`` parameter is accepted for protocol compatibility
        but currently unused — limits are tracked per instrument.

        Args:
            instrument: Instrument to check.
            model: Unused; accepted for protocol compatibility.

        Returns:
            ``(is_limited, seconds_remaining)``.  When not limited,
            ``seconds_remaining`` is ``0.0``.
        """
        del model  # accepted for RateLimitChecker protocol compatibility
        async with self._lock:
            resume_at = self._active_limits.get(instrument, 0.0)
        remaining = resume_at - time.monotonic()
        if remaining > 0:
            return True, remaining
        return False, 0.0

    @property
    def active_limits(self) -> dict[str, float]:
        """Currently active limits as ``{instrument: seconds_remaining}``."""
        now = time.monotonic()
        return {
            instrument: round(resume_at - now, 1)
            for instrument, resume_at in self._active_limits.items()
            if resume_at > now
        }

    @property
    def recent_events(self) -> list[RateLimitEvent]:
        """Events from the last hour (most recent first)."""
        now = time.monotonic()
        cutoff = now - 3600
        return sorted(
            (e for e in self._events if e.detected_at > cutoff),
            key=lambda e: e.detected_at,
            reverse=True,
        )

    # ─── Clearing ──────────────────────────────────────────────────

    async def clear_limits(
        self,
        instrument: str | None = None,
    ) -> int:
        """Clear active rate limits, optionally for a specific instrument.

        Removes the active limit entry so ``is_rate_limited()`` will
        return ``False`` immediately.  Event history is preserved for
        diagnostics — only the active limit is removed.

        Args:
            instrument: If provided, clear only this instrument's limit.
                If ``None``, clear all active limits.

        Returns:
            Number of limits cleared.
        """
        async with self._lock:
            if instrument is not None:
                if instrument in self._active_limits:
                    del self._active_limits[instrument]
                    _logger.info(
                        "rate_limit.cleared",
                        instrument=instrument,
                    )
                    return 1
                return 0
            else:
                count = len(self._active_limits)
                self._active_limits.clear()
                if count > 0:
                    _logger.info(
                        "rate_limit.cleared_all",
                        count=count,
                    )
                return count

    # ─── Maintenance ──────────────────────────────────────────────

    async def prune_stale(self) -> int:
        """Remove expired events and limits.

        Called periodically by ``ResourceMonitor._loop()`` to prevent
        unbounded memory growth.  ``report_rate_limit()`` also prunes
        on each call via the active write path, but this periodic
        prune ensures cleanup even during quiet periods.

        Returns:
            Number of stale events removed.
        """
        now = time.monotonic()
        async with self._lock:
            before = len(self._events)
            cutoff = now - 3600
            self._events = [e for e in self._events if e.detected_at > cutoff]
            # Remove expired limits
            self._active_limits = {
                k: v for k, v in self._active_limits.items() if v > now
            }
            return before - len(self._events)


__all__ = ["RateLimitCoordinator", "RateLimitEvent"]
