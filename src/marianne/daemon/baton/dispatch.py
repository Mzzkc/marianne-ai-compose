"""Baton dispatch logic — ready sheet resolution and dispatch.

Called after every event in the baton's main loop. Finds sheets that
are ready to execute and dispatches them via a callback, respecting:

- Global concurrency limit (max_concurrent_sheets)
- Per-instrument concurrency limits
- Instrument rate limit state
- Circuit breaker state
- Cost limits (future — checked but not enforced here)
- Backpressure (checked via baton._shutting_down)

The dispatch function is stateless — it reads baton state, makes
decisions, and calls the dispatch callback. It does not own any
state of its own.

Design notes:
- dispatch_ready() is a free function, not a method on BatonCore.
  This keeps the core focused on state management and event handling.
  The conductor calls dispatch_ready() after the baton processes each event.
- The dispatch callback is async to allow backend acquisition and task creation.
- Sheets are marked as 'dispatched' by the callback, not by dispatch_ready().
  This ensures that only actually-dispatched sheets consume concurrency slots.

See: ``docs/plans/2026-03-26-baton-design.md`` — Dispatch Logic section
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from marianne.core.logging import get_logger
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

_logger = get_logger("daemon.baton.dispatch")

# Type alias for the dispatch callback.
# Signature: async def dispatch(job_id: str, sheet_num: int, state: SheetExecutionState) -> None
DispatchCallback = Callable[[str, int, SheetExecutionState], Awaitable[None]]


@dataclass
class DispatchConfig:
    """Configuration for the dispatch logic.

    Attributes:
        max_concurrent_sheets: Global ceiling on concurrent sheet tasks.
        instrument_concurrency: Per-instrument concurrency limits (fallback
            when no model-specific limit exists).
        model_concurrency: Per-(instrument, model) concurrency limits.
            Keys are ``"instrument:model"`` strings. Takes priority over
            instrument_concurrency when the sheet has a known model.
        rate_limited_instruments: Set of instrument names currently rate-limited.
        open_circuit_breakers: Set of instrument names with open circuit breakers.
    """

    max_concurrent_sheets: int = 10
    instrument_concurrency: dict[str, int] = field(default_factory=dict)
    model_concurrency: dict[str, int] = field(default_factory=dict)
    rate_limited_instruments: set[str] = field(default_factory=set)
    open_circuit_breakers: set[str] = field(default_factory=set)


@dataclass
class DispatchResult:
    """Result of a dispatch_ready() call.

    Provides feedback on what happened: how many sheets were dispatched,
    which ones, and why others were skipped.
    """

    dispatched_count: int = 0
    dispatched_sheets: list[tuple[str, int]] = field(default_factory=list)
    skipped_reasons: dict[str, int] = field(default_factory=dict)

    def record_dispatch(self, job_id: str, sheet_num: int) -> None:
        """Record a successful dispatch."""
        self.dispatched_count += 1
        self.dispatched_sheets.append((job_id, sheet_num))

    def record_skip(self, reason: str) -> None:
        """Record a skipped sheet with reason."""
        self.skipped_reasons[reason] = self.skipped_reasons.get(reason, 0) + 1


async def dispatch_ready(
    baton: BatonCore,
    config: DispatchConfig,
    callback: DispatchCallback,
) -> DispatchResult:
    """Find and dispatch all sheets that are ready to execute.

    Called after every event in the baton's main loop. This is the
    only place where sheets move from 'pending'/'ready' to 'dispatched'.

    Args:
        baton: The baton core (provides sheet state and job registry).
        config: Dispatch configuration (concurrency limits, etc.).
        callback: Async function called for each sheet to dispatch.
            Receives (job_id, sheet_num, sheet_state).

    Returns:
        DispatchResult with counts of dispatched and skipped sheets.
    """
    result = DispatchResult()

    # Don't dispatch during shutdown
    if baton._shutting_down:
        return result

    # Track running counts per model key (instrument:model or just instrument)
    model_running: dict[str, int] = _count_dispatched_per_model(baton)
    global_running = baton.running_sheet_count

    for job_id in list(baton._jobs.keys()):
        job = baton._jobs.get(job_id)
        if job is None:
            continue

        if job.paused:
            continue

        ready = baton.get_ready_sheets(job_id)
        if ready:
            _logger.info(
                "dispatch.ready_sheets",
                extra={
                    "job_id": job_id,
                    "ready_count": len(ready),
                    "global_running": global_running,
                    "max_concurrent": config.max_concurrent_sheets,
                    "rate_limited": list(config.rate_limited_instruments),
                },
            )
        for sheet in ready:
            # Check global concurrency
            if global_running >= config.max_concurrent_sheets:
                result.record_skip("global_concurrency")
                return result  # Hard stop — can't dispatch more

            # Check per-model concurrency (falls back to per-instrument)
            instrument = sheet.instrument_name or ""
            model_key = f"{instrument}:{sheet.model}" if sheet.model else instrument
            model_limit = config.model_concurrency.get(model_key)
            if model_limit is None:
                model_limit = config.instrument_concurrency.get(instrument)
            model_count = model_running.get(model_key, 0)
            if model_limit is not None and model_count >= model_limit:
                result.record_skip(f"model_concurrency:{model_key}")
                continue

            # Check instrument rate limit (transient — don't fallback)
            if instrument in config.rate_limited_instruments:
                result.record_skip(f"rate_limited:{instrument}")
                continue

            # Check instrument availability — try fallback chain when
            # circuit breaker is OPEN or instrument is unregistered.
            # Loop to handle chains where multiple fallbacks are also
            # unavailable (e.g., claude-code→gemini-cli→ollama when
            # both claude-code and gemini-cli are OPEN).
            _skipped = False
            while (
                instrument in config.open_circuit_breakers
                or instrument not in baton._instruments
            ):
                if baton._check_and_fallback_unavailable(sheet, job_id):
                    instrument = sheet.instrument_name or ""
                    # Check if the new instrument is rate-limited
                    if instrument in config.rate_limited_instruments:
                        result.record_skip(f"rate_limited:{instrument}")
                        _skipped = True
                        break
                    # Loop continues to check the new instrument
                else:
                    result.record_skip(f"circuit_breaker:{instrument}")
                    _skipped = True
                    break
            if _skipped:
                continue

            # Dispatch!
            try:
                await callback(job_id, sheet.sheet_num, sheet)
                sheet.status = BatonSheetStatus.DISPATCHED
                sheet.dispatched_at = time.monotonic()
                result.record_dispatch(job_id, sheet.sheet_num)
                global_running += 1
                model_running[model_key] = model_count + 1
            except Exception:
                _logger.error(
                    "baton.dispatch.callback_failed",
                    extra={
                        "job_id": job_id,
                        "sheet_num": sheet.sheet_num,
                        "instrument": instrument,
                    },
                    exc_info=True,
                )

            # Recheck global limit after each dispatch
            if global_running >= config.max_concurrent_sheets:
                return result

    if result.dispatched_count > 0:
        _logger.info(
            "baton.dispatch.cycle_complete",
            extra={
                "dispatched": result.dispatched_count,
                "skipped": sum(result.skipped_reasons.values()),
            },
        )

    return result


def _count_dispatched_per_model(baton: BatonCore) -> dict[str, int]:
    """Count sheets currently in 'dispatched' status per model key.

    Keys are ``"instrument:model"`` when model is known, or just
    ``"instrument"`` when no model is set. This supports per-model
    concurrency limits while falling back to per-instrument for
    sheets without model info.
    """
    counts: dict[str, int] = {}
    for job in baton._jobs.values():
        for sheet in job.sheets.values():
            if sheet.status == BatonSheetStatus.DISPATCHED:
                inst = sheet.instrument_name or ""
                key = (
                    f"{inst}:{sheet.model}"
                    if sheet.model
                    else inst
                )
                counts[key] = counts.get(key, 0) + 1
    return counts
