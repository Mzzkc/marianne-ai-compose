"""#340: stagger dispatch of same-instrument sheets that become ready together.

When multiple sheets sharing an instrument become ready simultaneously (parallel
deps resolve at once), the baton dispatched them all in one cycle → a burst of
concurrent API calls to the same provider → rate limits (the composer's #1
multi-track reliability pain). `parallel.stagger_delay_ms` was parsed but
UNCONSUMED.

Per the 4-model lab (Opus/Gemini convergent, GLM/GPT-5.5 concurring on scope):
a per-instrument **last-dispatch-time GATE** — NOT pre-scheduled deferred sheets
(a stall hazard: fire-once timers lost on pause). In `dispatch_ready`, skip a
same-instrument sheet if `now - instrument.last_dispatch_at < stagger`; set
`last_dispatch_at` on dispatch; and when any sheet is stagger-skipped, schedule a
single delayed `DispatchRetry` wake so the loop re-evaluates after the interval
(liveness — no deferred state, sheets stay in the pool). Default 0 = today's
behavior. Per-instrument only (per-provider-family deferred — no primitive).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.dispatch import dispatch_ready
from marianne.daemon.baton.events import DispatchRetry


def _sheet(num: int, instrument: str = "ember") -> Sheet:
    return Sheet(
        num=num, movement=1, voice=None, voice_count=1,
        workspace=Path("/tmp/ws"), instrument_name=instrument,
        prompt_template="t", timeout_seconds=60.0,
    )


def _setup(instruments: list[str], stagger_ms: int, clock_start: float = 1000.0):
    adapter = BatonAdapter()
    sheets = [_sheet(i + 1, inst) for i, inst in enumerate(instruments)]
    adapter.register_job("j", sheets, dependencies={})
    for inst in set(instruments):
        adapter.baton.register_instrument(inst, max_concurrent=10)
    dispatched: list[int] = []

    async def cb(job_id: str, sheet_num: int, state: object) -> None:
        dispatched.append(sheet_num)

    clock = [clock_start]
    config = adapter.baton.build_dispatch_config(
        max_concurrent_sheets=10, stagger_delay_ms=stagger_ms
    )
    config.time_fn = lambda: clock[0]
    return adapter, config, cb, dispatched, clock


class TestStaggerDispatch:
    @pytest.mark.asyncio
    async def test_same_instrument_burst_staggered(self) -> None:
        adapter, config, cb, dispatched, _clock = _setup(["ember"] * 4, stagger_ms=200)
        await dispatch_ready(adapter.baton, config, cb)
        # Only the first same-instrument sheet dispatches; the rest are gated.
        assert dispatched == [1]

    @pytest.mark.asyncio
    async def test_dispatches_next_after_interval(self) -> None:
        adapter, config, cb, dispatched, clock = _setup(["ember"] * 4, stagger_ms=200)
        await dispatch_ready(adapter.baton, config, cb)
        assert dispatched == [1]
        clock[0] += 0.2  # advance past the stagger window
        await dispatch_ready(adapter.baton, config, cb)
        assert dispatched == [1, 2]  # one more, still gated for the rest

    @pytest.mark.asyncio
    async def test_zero_stagger_is_noop(self) -> None:
        adapter, config, cb, dispatched, _clock = _setup(["ember"] * 4, stagger_ms=0)
        await dispatch_ready(adapter.baton, config, cb)
        assert sorted(dispatched) == [1, 2, 3, 4]  # all dispatch in one cycle

    @pytest.mark.asyncio
    async def test_different_instruments_not_staggered(self) -> None:
        adapter, config, cb, dispatched, _clock = _setup(["ember", "forge"], stagger_ms=200)
        await dispatch_ready(adapter.baton, config, cb)
        # Independent instruments have independent gates → both dispatch.
        assert sorted(dispatched) == [1, 2]

    @pytest.mark.asyncio
    async def test_stagger_skip_schedules_wake(self) -> None:
        # Liveness: a stagger-skip must schedule a delayed DispatchRetry so the
        # loop re-evaluates after the interval (no other event may arrive).
        adapter, config, cb, dispatched, _clock = _setup(["ember"] * 4, stagger_ms=200)
        adapter.baton._timer = MagicMock()
        await dispatch_ready(adapter.baton, config, cb)
        assert adapter.baton._timer.schedule.called
        delay, event = adapter.baton._timer.schedule.call_args.args
        assert isinstance(event, DispatchRetry)
        assert delay == pytest.approx(0.2)
