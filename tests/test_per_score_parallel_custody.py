"""Per-score parallel policy remains owned by its job across dispatch/recovery."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.checkpoint import CheckpointState
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import dispatch_ready
from marianne.daemon.baton.events import (
    PacingComplete,
    RateLimitHit,
    RetryDue,
    SheetAttemptResult,
)
from marianne.daemon.baton.state import (
    AttemptMode,
    BatonSheetStatus,
    SheetExecutionState,
)
from marianne.daemon.baton.timer import TimerWheel


def _states(count: int, instrument: str = "claude-code") -> dict[int, SheetExecutionState]:
    return {
        number: SheetExecutionState(sheet_num=number, instrument_name=instrument)
        for number in range(1, count + 1)
    }


def _sheets(count: int, instrument: str = "claude-code") -> list[Sheet]:
    return [
        Sheet(
            num=number,
            movement=1,
            voice=number,
            voice_count=count,
            workspace=Path("/tmp/per-score-policy"),
            instrument_name=instrument,
            prompt_template="test",
            timeout_seconds=60.0,
        )
        for number in range(1, count + 1)
    ]


class _BlockingBackendPool:
    """Controllable backend acquisition/release boundary for race tests."""

    def __init__(self, *, fail_interactive_acquire: bool = False) -> None:
        self.fail_interactive_acquire = fail_interactive_acquire
        self.error_after_release: Exception | None = None
        self.acquire_entered = asyncio.Event()
        self.acquire_release = asyncio.Event()
        self.backend = object()
        self.releases: list[tuple[str, object]] = []

    async def acquire(self, instrument: str, **kwargs: object) -> object:
        if self.fail_interactive_acquire and kwargs.get("interactive") is True:
            raise ValueError("interactive unsupported")
        self.acquire_entered.set()
        await self.acquire_release.wait()
        if self.error_after_release is not None:
            raise self.error_after_release
        return self.backend

    async def release(self, instrument: str, backend: object) -> None:
        self.releases.append((instrument, backend))


class _BlockingReleaseBackendPool:
    """Acquire immediately, then hold cleanup at the observable release seam."""

    def __init__(self) -> None:
        self.backend = object()
        self.release_entered = asyncio.Event()
        self.release_allowed = asyncio.Event()
        self.release_completed = asyncio.Event()

    async def acquire(self, instrument: str, **kwargs: object) -> object:
        return self.backend

    async def release(self, instrument: str, backend: object) -> None:
        self.release_entered.set()
        await self.release_allowed.wait()
        self.release_completed.set()


@pytest.mark.asyncio
async def test_job_at_its_cap_leaves_global_slots_for_another_job() -> None:
    baton = BatonCore()
    baton.register_instrument("claude-code", max_concurrent=10)
    baton.register_job("narrow", _states(3), {}, max_concurrent=1)
    baton.register_job("wide", _states(3), {}, max_concurrent=3)

    callback = AsyncMock()
    result = await dispatch_ready(
        baton,
        baton.build_dispatch_config(max_concurrent_sheets=4),
        callback,
    )

    assert result.dispatched_sheets == [
        ("narrow", 1),
        ("wide", 1),
        ("wide", 2),
        ("wide", 3),
    ]
    assert baton._jobs["narrow"].sheets[2].dispatch_blocked_reason == "job_concurrency"


@pytest.mark.asyncio
async def test_rate_limit_fallback_keeps_physical_tasks_in_both_ceiling_counts() -> None:
    """WAITING/PENDING relabels cannot make live musicians disappear."""
    baton = BatonCore()
    for instrument in ("claude-code", "gemini-cli", "ollama"):
        baton.register_instrument(instrument, max_concurrent=10)

    job_a = {
        1: SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            fallback_chain=["ollama"],
            status=BatonSheetStatus.DISPATCHED,
        ),
        2: SheetExecutionState(
            sheet_num=2,
            instrument_name="gemini-cli",
            status=BatonSheetStatus.DISPATCHED,
        ),
        3: SheetExecutionState(sheet_num=3, instrument_name="gemini-cli"),
    }
    job_b = {
        1: SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            fallback_chain=["ollama"],
            status=BatonSheetStatus.DISPATCHED,
        ),
        2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
    }
    baton.register_job("job-a", job_a, {}, max_concurrent=2)
    baton.register_job("job-b", job_b, {}, max_concurrent=2)

    await baton.handle_event(
        RateLimitHit(
            instrument="claude-code",
            wait_seconds=60,
            job_id="job-a",
            sheet_num=1,
        )
    )
    assert job_a[1].status == BatonSheetStatus.PENDING
    assert job_a[1].instrument_name == "ollama"
    assert job_b[1].status == BatonSheetStatus.PENDING

    physical = {
        ("job-a", 1): ("claude-code", None),
        ("job-a", 2): ("gemini-cli", None),
        ("job-b", 1): ("claude-code", None),
    }
    config = baton.build_dispatch_config(
        max_concurrent_sheets=4,
        active_executions=physical,
    )
    result = await dispatch_ready(baton, config, AsyncMock())

    assert result.dispatched_sheets == [("job-b", 2)]
    assert job_a[3].dispatch_blocked_reason == "job_concurrency"
    assert len(physical) + result.dispatched_count == 4


@pytest.mark.asyncio
async def test_adapter_dispatch_config_uses_live_task_authority() -> None:
    adapter = BatonAdapter(max_concurrent_sheets=4)
    adapter.register_job(
        "live",
        _sheets(1),
        {},
        parallel_enabled=True,
        parallel_max_concurrent=2,
    )
    blocker = asyncio.Event()
    task = asyncio.create_task(blocker.wait())
    key = ("live", 1)
    adapter._active_tasks[key] = task
    adapter._active_execution_details[key] = ("claude-code", "sonnet")
    try:
        config = adapter._build_dispatch_config()
        assert config.active_executions == {
            key: ("claude-code", "sonnet"),
        }
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_fail_fast_preserves_live_pending_fallback_sibling() -> None:
    """A live musician remains running after its sheet is fallback-relabelled."""
    adapter = BatonAdapter()
    sheets = _sheets(3, instrument="other")
    sheets[1] = Sheet(
        num=2,
        movement=1,
        voice=2,
        voice_count=3,
        workspace=Path("/tmp/per-score-policy"),
        instrument_name="primary",
        instrument_fallbacks=["fallback"],
        prompt_template="test",
        timeout_seconds=60.0,
    )
    adapter.register_job(
        "fast",
        sheets,
        {},
        parallel_enabled=True,
        parallel_max_concurrent=3,
        parallel_fail_fast=True,
    )
    states = adapter.baton._jobs["fast"].sheets
    states[1].status = BatonSheetStatus.DISPATCHED
    states[2].status = BatonSheetStatus.DISPATCHED

    blocker = asyncio.Event()
    task = asyncio.create_task(blocker.wait())
    key = ("fast", 2)
    adapter._active_tasks[key] = task
    adapter._active_execution_details[key] = ("primary", None)
    try:
        await adapter.baton.handle_event(
            RateLimitHit(
                instrument="primary",
                wait_seconds=30,
                job_id="fast",
                sheet_num=2,
            )
        )
        assert states[2].status == BatonSheetStatus.PENDING
        assert not task.done()

        await adapter.baton.handle_event(
            SheetAttemptResult(
                job_id="fast",
                sheet_num=1,
                instrument_name="other",
                attempt=1,
                event_generation=adapter.baton.get_job_generation("fast"),
                execution_success=False,
                error_classification="AUTH_FAILURE",
                error_message="bad auth",
            )
        )

        assert states[1].status == BatonSheetStatus.FAILED
        assert states[2].status == BatonSheetStatus.PENDING
        assert not task.done()
        assert states[3].status == BatonSheetStatus.SKIPPED
        skip_events = adapter.baton.drain_skip_events()
        assert {(event.job_id, event.sheet_num) for event in skip_events} == {("fast", 3)}
        assert adapter.baton._state_dirty is True
    finally:
        adapter._active_tasks.pop(key, None)
        adapter._active_execution_details.pop(key, None)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_acquire", [False, True])
async def test_stale_acquisition_cannot_dispatch_into_reused_job(
    fallback_acquire: bool,
) -> None:
    """Every backend-acquire await rechecks immutable job registration identity."""
    pool = _BlockingBackendPool(fail_interactive_acquire=fallback_acquire)
    adapter = BatonAdapter()
    adapter._backend_pool = pool
    sheet = _sheets(1, instrument="primary")[0]
    if fallback_acquire:
        sheet.instrument_config["interactive"] = True
    adapter.register_job(
        "reused",
        [sheet],
        {},
        parallel_enabled=True,
        parallel_max_concurrent=1,
    )
    state = adapter.baton._jobs["reused"].sheets[1]
    if fallback_acquire:
        state.instrument_name = "fallback"
        adapter.baton.register_instrument("fallback", max_concurrent=1)

    musician_gate = asyncio.Event()
    musician = asyncio.create_task(musician_gate.wait())
    adapter._create_musician_task_after_acquire = MagicMock(
        return_value=(musician, AttemptMode.NORMAL)
    )
    old_token = adapter.baton.get_job_registration_token("reused")
    dispatch_task = asyncio.create_task(
        dispatch_ready(
            adapter.baton,
            adapter._build_dispatch_config(),
            adapter._dispatch_callback,
        )
    )
    await pool.acquire_entered.wait()

    adapter.deregister_job("reused")
    replacement = _sheets(1, instrument="replacement")
    adapter.register_job(
        "reused",
        replacement,
        {},
        parallel_enabled=True,
        parallel_max_concurrent=1,
    )
    assert adapter.baton.get_job_registration_token("reused") != old_token

    pool.acquire_release.set()
    result = await dispatch_task
    try:
        assert result.dispatched_sheets == []
        assert adapter.baton._jobs["reused"].sheets[1].status == BatonSheetStatus.PENDING
        assert ("reused", 1) not in adapter._active_tasks
        assert ("reused", 1) not in adapter._active_execution_details
        assert pool.releases == [("fallback" if fallback_acquire else "primary", pool.backend)]
    finally:
        musician.cancel()
        await asyncio.gather(musician, return_exceptions=True)


@pytest.mark.asyncio
async def test_stale_acquisition_failure_cannot_mutate_reused_job() -> None:
    """A late acquire exception belongs to the old registration, not its reuse."""
    pool = _BlockingBackendPool()
    pool.error_after_release = RuntimeError("late pool failure")
    adapter = BatonAdapter()
    adapter._backend_pool = pool
    adapter.register_job(
        "reused",
        _sheets(1, instrument="primary"),
        {},
        parallel_enabled=True,
        parallel_max_concurrent=1,
    )
    dispatch_task = asyncio.create_task(
        dispatch_ready(
            adapter.baton,
            adapter._build_dispatch_config(),
            adapter._dispatch_callback,
        )
    )
    await pool.acquire_entered.wait()

    adapter.deregister_job("reused")
    adapter.register_job(
        "reused",
        _sheets(1, instrument="replacement"),
        {},
        parallel_enabled=True,
        parallel_max_concurrent=1,
    )
    pool.acquire_release.set()
    result = await dispatch_task

    assert result.dispatched_sheets == []
    assert adapter.baton._jobs["reused"].sheets[1].status == BatonSheetStatus.PENDING
    queued_events = []
    while not adapter.baton.inbox.empty():
        queued_events.append(adapter.baton.inbox.get_nowait())
    assert not any(isinstance(event, SheetAttemptResult) for event in queued_events)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_post_acquire_setup_failure_cannot_mutate_reused_job_after_release(
    fallback: bool,
) -> None:
    """Cleanup awaits cannot turn an old setup failure into replacement work."""
    pool = _BlockingReleaseBackendPool()
    adapter = BatonAdapter()
    adapter._backend_pool = pool
    sheet = _sheets(1, instrument="primary")[0]
    adapter.register_job(
        "reused",
        [sheet],
        {},
        parallel_enabled=True,
        parallel_max_concurrent=1,
    )
    old_state = adapter.baton._jobs["reused"].sheets[1]
    if fallback:
        old_state.instrument_name = "fallback"
        adapter.baton.register_instrument("fallback", max_concurrent=1)

    adapter._create_musician_task_after_acquire = MagicMock(
        side_effect=RuntimeError("forced post-acquire setup failure")
    )
    dispatch_task = asyncio.create_task(
        dispatch_ready(
            adapter.baton,
            adapter._build_dispatch_config(),
            adapter._dispatch_callback,
        )
    )
    await pool.release_entered.wait()

    adapter.deregister_job("reused")
    adapter.register_job(
        "reused",
        _sheets(1, instrument="replacement"),
        {},
        parallel_enabled=True,
        parallel_max_concurrent=1,
    )
    replacement = adapter.baton._jobs["reused"].sheets[1]
    pool.release_allowed.set()
    result = await dispatch_task

    assert result.dispatched_sheets == []
    assert replacement.status == BatonSheetStatus.PENDING
    assert pool.release_completed.is_set()
    queued_events = []
    while not adapter.baton.inbox.empty():
        queued_events.append(adapter.baton.inbox.get_nowait())
    assert not any(isinstance(event, SheetAttemptResult) for event in queued_events)


@pytest.mark.asyncio
async def test_cancel_during_post_acquire_cleanup_cannot_touch_reused_job() -> None:
    """Cancellation while shielded cleanup drains leaves replacement untouched."""
    pool = _BlockingReleaseBackendPool()
    adapter = BatonAdapter()
    adapter._backend_pool = pool
    adapter.register_job("reused", _sheets(1, instrument="old"), {})
    adapter._create_musician_task_after_acquire = MagicMock(
        side_effect=RuntimeError("forced post-acquire setup failure")
    )
    dispatch_task = asyncio.create_task(
        dispatch_ready(
            adapter.baton,
            adapter._build_dispatch_config(),
            adapter._dispatch_callback,
        )
    )
    await pool.release_entered.wait()

    dispatch_task.cancel()
    adapter.deregister_job("reused")
    adapter.register_job("reused", _sheets(1, instrument="replacement"), {})
    replacement = adapter.baton._jobs["reused"].sheets[1]
    pool.release_allowed.set()
    with pytest.raises(asyncio.CancelledError):
        await dispatch_task
    await pool.release_completed.wait()

    assert replacement.status == BatonSheetStatus.PENDING
    queued_events = []
    while not adapter.baton.inbox.empty():
        queued_events.append(adapter.baton.inbox.get_nowait())
    assert not any(isinstance(event, SheetAttemptResult) for event in queued_events)


@pytest.mark.asyncio
async def test_dispatcher_rejects_callback_acceptance_from_replaced_registration() -> None:
    """The scheduler itself checks identity after its awaited callback."""
    baton = BatonCore()
    baton.register_instrument("claude-code", max_concurrent=10)
    baton.register_job("reused", _states(1), {}, max_concurrent=1)

    async def replace_during_callback(
        job_id: str,
        sheet_num: int,
        state: SheetExecutionState,
    ) -> bool:
        baton.deregister_job(job_id)
        baton.register_job(job_id, _states(1, instrument="replacement"), {}, max_concurrent=1)
        await asyncio.sleep(0)
        return True

    result = await dispatch_ready(
        baton,
        baton.build_dispatch_config(max_concurrent_sheets=10),
        replace_during_callback,
    )

    assert result.dispatched_sheets == []
    assert baton._jobs["reused"].sheets[1].status == BatonSheetStatus.PENDING


@pytest.mark.asyncio
async def test_cancelled_old_task_callback_cannot_clear_reused_job_task() -> None:
    """A late done callback only cleans the exact task that owned the key."""
    adapter = BatonAdapter()
    adapter.register_job("reused", _sheets(1), {})
    key = ("reused", 1)

    old_gate = asyncio.Event()
    old_task = asyncio.create_task(old_gate.wait())
    adapter._active_tasks[key] = old_task
    adapter._active_execution_details[key] = ("claude-code", None)
    old_task.add_done_callback(lambda task: adapter._on_musician_done("reused", 1, task))

    adapter.deregister_job("reused")
    adapter.register_job("reused", _sheets(1, instrument="replacement"), {})
    replacement_state = adapter.baton._jobs["reused"].sheets[1]
    replacement_state.status = BatonSheetStatus.DISPATCHED
    new_gate = asyncio.Event()
    new_task = asyncio.create_task(new_gate.wait())
    adapter._active_tasks[key] = new_task
    adapter._active_execution_details[key] = ("replacement", None)
    adapter._active_pids[key] = (123, 123)

    await asyncio.gather(old_task, return_exceptions=True)
    await asyncio.sleep(0)
    try:
        assert adapter._active_tasks[key] is new_task
        assert adapter._active_execution_details[key] == ("replacement", None)
        assert adapter._active_pids[key] == (123, 123)
        assert replacement_state.status == BatonSheetStatus.DISPATCHED
        queued_events = []
        while not adapter.baton.inbox.empty():
            queued_events.append(adapter.baton.inbox.get_nowait())
        assert not any(
            isinstance(event, SheetAttemptResult) and event.error_classification == "CANCELLED"
            for event in queued_events
        )
    finally:
        new_task.cancel()
        await asyncio.gather(new_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_one_jobs_stagger_neither_delays_nor_is_updated_by_another_job() -> None:
    baton = BatonCore()
    baton.register_instrument("claude-code", max_concurrent=10)
    baton.register_job("paced", _states(2), {}, max_concurrent=2, stagger_delay_ms=1000)
    baton.register_job("free", _states(2), {}, max_concurrent=2, stagger_delay_ms=0)
    clock = [100.0]
    config = baton.build_dispatch_config(max_concurrent_sheets=10)
    config.time_fn = lambda: clock[0]
    callback = AsyncMock()

    result = await dispatch_ready(baton, config, callback)
    assert result.dispatched_sheets == [("paced", 1), ("free", 1), ("free", 2)]

    baton._jobs["free"].sheets[1].status = BatonSheetStatus.COMPLETED
    baton._jobs["free"].sheets[2].status = BatonSheetStatus.COMPLETED
    clock[0] = 100.5
    result = await dispatch_ready(baton, config, callback)
    assert result.dispatched_sheets == []
    clock[0] = 101.0
    result = await dispatch_ready(baton, config, callback)
    assert result.dispatched_sheets == [("paced", 2)]


@pytest.mark.asyncio
async def test_each_jobs_stagger_gets_its_own_wake_deadline() -> None:
    baton = BatonCore(timer=MagicMock())
    baton.register_instrument("claude-code", max_concurrent=10)
    baton.register_job("slow", _states(2), {}, stagger_delay_ms=1000)
    baton.register_job("quick", _states(2), {}, stagger_delay_ms=100)
    baton._job_last_dispatch_at[("slow", "claude-code")] = 10.0
    baton._job_last_dispatch_at[("quick", "claude-code")] = 10.0
    config = baton.build_dispatch_config(max_concurrent_sheets=10)
    config.time_fn = lambda: 10.0

    await dispatch_ready(baton, config, AsyncMock())

    delays = sorted(call.args[0] for call in baton._timer.schedule.call_args_list)
    assert delays == pytest.approx([0.1, 1.0])


@pytest.mark.asyncio
async def test_stagger_timer_is_cancelled_and_stale_generation_cannot_clear_reuse() -> None:
    wheel = TimerWheel(asyncio.Queue())
    baton = BatonCore(timer=wheel)
    baton.register_instrument("claude-code", max_concurrent=10)
    baton.register_job("reused", _states(1), {}, stagger_delay_ms=1000)
    baton._job_last_dispatch_at[("reused", "claude-code")] = 10.0
    config = baton.build_dispatch_config(max_concurrent_sheets=10)
    config.time_fn = lambda: 10.0
    await dispatch_ready(baton, config, AsyncMock())
    old_handle = baton._stagger_wake_handles["reused"]
    old_event = old_handle.event
    assert wheel.pending_count == 1

    baton.deregister_job("reused")
    assert wheel.pending_count == 0
    baton.register_job("reused", _states(1), {}, stagger_delay_ms=1000)
    baton._job_last_dispatch_at[("reused", "claude-code")] = 10.0
    config = baton.build_dispatch_config(max_concurrent_sheets=10)
    config.time_fn = lambda: 10.0
    await dispatch_ready(baton, config, AsyncMock())
    new_handle = baton._stagger_wake_handles["reused"]
    assert new_handle is not old_handle
    assert wheel.pending_count == 1

    await baton.handle_event(old_event)
    assert baton._stagger_wake_handles["reused"] is new_handle
    assert wheel.pending_count == 1


@pytest.mark.asyncio
async def test_deregister_during_later_callback_drops_stale_stagger_wake() -> None:
    """A callback await may remove a job that recorded a stagger wake earlier."""
    timer = MagicMock()
    baton = BatonCore(timer=timer)
    baton.register_instrument("claude-code", max_concurrent=10)
    baton.register_job("paced", _states(1), {}, stagger_delay_ms=1000)
    baton.register_job("trigger", _states(1), {})
    baton._job_last_dispatch_at[("paced", "claude-code")] = 10.0
    config = baton.build_dispatch_config(max_concurrent_sheets=10)
    config.time_fn = lambda: 10.0

    async def deregister_paced(
        job_id: str,
        sheet_num: int,
        state: SheetExecutionState,
    ) -> bool:
        del sheet_num, state
        assert job_id == "trigger"
        baton.deregister_job("paced")
        await asyncio.sleep(0)
        return True

    result = await dispatch_ready(baton, config, deregister_paced)

    assert result.dispatched_sheets == [("trigger", 1)]
    assert "paced" not in baton._stagger_wake_handles
    timer.schedule.assert_not_called()


@pytest.mark.asyncio
async def test_reuse_during_later_callback_cannot_adopt_old_stagger_wake() -> None:
    """A replacement registration cannot inherit the old generation's delay."""
    timer = MagicMock()
    baton = BatonCore(timer=timer)
    baton.register_instrument("claude-code", max_concurrent=10)
    baton.register_job("paced", _states(1), {}, stagger_delay_ms=1000)
    old_token = baton.get_job_registration_token("paced")
    baton.register_job("trigger", _states(1), {})
    baton._job_last_dispatch_at[("paced", "claude-code")] = 10.0
    config = baton.build_dispatch_config(max_concurrent_sheets=10)
    config.time_fn = lambda: 10.0

    async def replace_paced(
        job_id: str,
        sheet_num: int,
        state: SheetExecutionState,
    ) -> bool:
        del sheet_num, state
        assert job_id == "trigger"
        baton.deregister_job("paced")
        baton.register_job("paced", _states(1), {}, stagger_delay_ms=1000)
        await asyncio.sleep(0)
        return True

    await dispatch_ready(baton, config, replace_paced)

    assert baton.get_job_registration_token("paced") != old_token
    assert "paced" not in baton._stagger_wake_handles
    timer.schedule.assert_not_called()


@pytest.mark.asyncio
async def test_old_generation_events_cannot_mutate_reused_job() -> None:
    """Attempt, retry, and pacing events remain bound to their registration."""
    baton = BatonCore()
    old = _states(1)
    old[1].status = BatonSheetStatus.RETRY_SCHEDULED
    baton.register_job(
        "reused", old, {}, pacing_seconds=10, event_generation=1
    )
    old_generation = baton.get_job_generation("reused")
    assert old_generation is not None
    baton.deregister_job("reused")

    replacement = _states(1, instrument="replacement")
    replacement[1].status = BatonSheetStatus.RETRY_SCHEDULED
    baton.register_job(
        "reused", replacement, {}, pacing_seconds=10, event_generation=2
    )
    baton._jobs["reused"].pacing_active = True

    await baton.handle_event(
        RetryDue(
            job_id="reused",
            sheet_num=1,
            event_generation=old_generation,
        )
    )
    await baton.handle_event(
        PacingComplete(
            job_id="reused",
            event_generation=old_generation,
        )
    )
    await baton.handle_event(
        SheetAttemptResult(
            job_id="reused",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            event_generation=old_generation,
            execution_success=True,
        )
    )
    # Managed registrations reject generation-less events.
    await baton.handle_event(RetryDue(job_id="reused", sheet_num=1))
    await baton.handle_event(PacingComplete(job_id="reused"))
    await baton.handle_event(
        SheetAttemptResult(
            job_id="reused",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
        )
    )

    assert replacement[1].status == BatonSheetStatus.RETRY_SCHEDULED
    assert replacement[1].normal_attempts == 0
    assert replacement[1].completion_attempts == 0
    assert baton._jobs["reused"].pacing_active is True


def test_retry_and_pacing_timers_capture_event_generation() -> None:
    """Every production timer constructor binds the current job generation."""
    timer = MagicMock()
    baton = BatonCore(timer=timer)
    sheets = _states(1)
    baton.register_job(
        "timed", sheets, {}, pacing_seconds=5, event_generation=7
    )
    generation = baton.get_job_generation("timed")
    assert generation is not None

    baton._schedule_retry("timed", 1, sheets[1])
    retry_event = timer.schedule.call_args_list[-1].args[1]
    assert isinstance(retry_event, RetryDue)
    assert retry_event.event_generation == generation

    sheets[1].status = BatonSheetStatus.COMPLETED
    baton._schedule_pacing("timed")
    pacing_event = timer.schedule.call_args_list[-1].args[1]
    assert isinstance(pacing_event, PacingComplete)
    assert pacing_event.event_generation == generation


@pytest.mark.asyncio
async def test_deregister_retains_occupancy_until_cancelled_task_cleanup_finishes() -> None:
    """A replacement cannot over-dispatch while old cleanup still owns a slot."""
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.register_job(
        "reused",
        _sheets(1, instrument="old"),
        {},
        parallel_enabled=True,
        parallel_max_concurrent=1,
    )
    old_generation = adapter.baton.get_job_generation("reused")
    assert old_generation is not None
    cleanup_entered = asyncio.Event()
    cleanup_allowed = asyncio.Event()

    async def old_task_body() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_entered.set()
            await cleanup_allowed.wait()

    key = ("reused", 1)
    old_task = asyncio.create_task(old_task_body())
    adapter._active_tasks[key] = old_task
    adapter._active_execution_details[key] = ("old", None)
    old_task.add_done_callback(
        lambda task: adapter._on_musician_done(
            "reused", 1, task, event_generation=old_generation
        )
    )
    await asyncio.sleep(0)

    adapter.deregister_job("reused")
    await cleanup_entered.wait()
    adapter.register_job(
        "reused",
        _sheets(2, instrument="replacement"),
        {},
        parallel_enabled=True,
        parallel_max_concurrent=2,
    )

    blocked = await dispatch_ready(
        adapter.baton,
        adapter._build_dispatch_config(),
        AsyncMock(),
    )
    assert blocked.dispatched_sheets == []
    assert adapter._active_tasks[key] is old_task

    cleanup_allowed.set()
    await asyncio.gather(old_task, return_exceptions=True)
    await asyncio.sleep(0)
    callback = AsyncMock(return_value=True)
    released = await dispatch_ready(
        adapter.baton,
        adapter._build_dispatch_config(),
        callback,
    )
    assert released.dispatched_sheets == [("reused", 1)]


@pytest.mark.asyncio
async def test_retiring_task_holds_global_slot_against_unrelated_job() -> None:
    """Deregistration does not erase physical occupancy from global accounting."""
    adapter = BatonAdapter(max_concurrent_sheets=1)
    adapter.register_job("retiring", _sheets(1, instrument="old"), {})
    old_generation = adapter.baton.get_job_generation("retiring")
    assert old_generation is not None
    cleanup_entered = asyncio.Event()
    cleanup_allowed = asyncio.Event()

    async def old_task_body() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_entered.set()
            await cleanup_allowed.wait()

    key = ("retiring", 1)
    old_task = asyncio.create_task(old_task_body())
    adapter._active_tasks[key] = old_task
    adapter._active_execution_details[key] = ("old", None)
    old_task.add_done_callback(
        lambda task: adapter._on_musician_done(
            "retiring", 1, task, event_generation=old_generation
        )
    )
    await asyncio.sleep(0)

    adapter.deregister_job("retiring")
    await cleanup_entered.wait()
    adapter.register_job("unrelated", _sheets(1, instrument="replacement"), {})

    blocked = await dispatch_ready(
        adapter.baton,
        adapter._build_dispatch_config(),
        AsyncMock(),
    )
    assert blocked.dispatched_sheets == []
    assert blocked.skipped_reasons == {"global_concurrency": 1}

    cleanup_allowed.set()
    await asyncio.gather(old_task, return_exceptions=True)
    await asyncio.sleep(0)
    released = await dispatch_ready(
        adapter.baton,
        adapter._build_dispatch_config(),
        AsyncMock(return_value=True),
    )
    assert released.dispatched_sheets == [("unrelated", 1)]


def test_fail_fast_terminalizes_only_unstarted_sheets_in_its_job() -> None:
    baton = BatonCore()
    fast = _states(3)
    slow = _states(2)
    fast[1].status = BatonSheetStatus.FAILED
    fast[2].status = BatonSheetStatus.DISPATCHED
    baton.register_job("fast", fast, {}, fail_fast=True)
    baton.register_job("slow", slow, {}, fail_fast=False)

    baton._propagate_failure_to_dependents("fast", 1)

    assert fast[2].status == BatonSheetStatus.DISPATCHED
    assert fast[3].status == BatonSheetStatus.SKIPPED
    assert fast[3].error_code == "E999"
    assert all(sheet.status == BatonSheetStatus.PENDING for sheet in slow.values())
    skip_events = baton.drain_skip_events()
    assert {(event.job_id, event.sheet_num) for event in skip_events} == {("fast", 3)}


def test_fail_fast_false_preserves_independent_pending_work() -> None:
    baton = BatonCore()
    sheets = _states(2)
    sheets[1].status = BatonSheetStatus.FAILED
    baton.register_job("continue", sheets, {}, fail_fast=False)
    baton._propagate_failure_to_dependents("continue", 1)
    assert sheets[2].status == BatonSheetStatus.PENDING


def test_adapter_registration_recovery_and_deregister_preserve_policy() -> None:
    adapter = BatonAdapter(max_concurrent_sheets=8)
    adapter.register_job(
        "fresh",
        _sheets(2),
        {},
        parallel_enabled=True,
        parallel_max_concurrent=2,
        parallel_fail_fast=False,
        stagger_delay_ms=125,
    )
    fresh = adapter.baton._jobs["fresh"]
    assert (fresh.max_concurrent, fresh.fail_fast, fresh.stagger_delay_ms) == (2, False, 125)
    assert adapter.baton.get_diagnostics("fresh")["parallel_policy"] == {
        "max_concurrent": 2,
        "fail_fast": False,
        "stagger_delay_ms": 125,
    }

    checkpoint = CheckpointState(job_id="recovered", job_name="r", total_sheets=2)
    adapter.recover_job(
        "recovered",
        _sheets(2),
        {},
        checkpoint,
        parallel_enabled=True,
        parallel_max_concurrent=1,
        parallel_fail_fast=True,
        stagger_delay_ms=300,
    )
    recovered = adapter.baton._jobs["recovered"]
    assert (recovered.max_concurrent, recovered.fail_fast, recovered.stagger_delay_ms) == (
        1,
        True,
        300,
    )
    adapter.baton._job_last_dispatch_at[("recovered", "claude-code")] = 10.0
    adapter.deregister_job("recovered")
    assert not any(key[0] == "recovered" for key in adapter.baton._job_last_dispatch_at)


def test_parallel_disabled_enforces_serial_policy_on_fresh_and_recovery() -> None:
    adapter = BatonAdapter()
    adapter.register_job(
        "serial-fresh",
        _sheets(1),
        {},
        parallel_enabled=False,
        parallel_max_concurrent=4,
        parallel_fail_fast=True,
        stagger_delay_ms=500,
    )
    fresh = adapter.baton._jobs["serial-fresh"]
    assert (fresh.max_concurrent, fresh.fail_fast, fresh.stagger_delay_ms) == (
        1,
        False,
        0,
    )

    checkpoint = CheckpointState(job_id="serial-recovery", job_name="s", total_sheets=1)
    adapter.recover_job(
        "serial-recovery",
        _sheets(1),
        {},
        checkpoint,
        parallel_enabled=False,
        parallel_max_concurrent=4,
        parallel_fail_fast=True,
        stagger_delay_ms=500,
    )
    recovered = adapter.baton._jobs["serial-recovery"]
    assert (recovered.max_concurrent, recovered.fail_fast, recovered.stagger_delay_ms) == (
        1,
        False,
        0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", [False, True])
async def test_parallel_disabled_dispatches_only_one_independent_sheet(
    recovery: bool,
) -> None:
    """The public score contract stays serial even when its DAG permits a wave."""
    adapter = BatonAdapter(max_concurrent_sheets=10)
    sheets = _sheets(2)
    if recovery:
        checkpoint = CheckpointState(
            job_id="serial",
            job_name="serial",
            total_sheets=2,
        )
        adapter.recover_job(
            "serial",
            sheets,
            {1: [], 2: []},
            checkpoint,
            parallel_enabled=False,
        )
    else:
        adapter.register_job(
            "serial",
            sheets,
            {1: [], 2: []},
            parallel_enabled=False,
        )

    result = await dispatch_ready(
        adapter.baton,
        adapter._build_dispatch_config(),
        AsyncMock(),
    )

    assert result.dispatched_sheets == [("serial", 1)]
    assert adapter.baton._jobs["serial"].sheets[2].status == BatonSheetStatus.PENDING


@pytest.mark.asyncio
async def test_low_level_legacy_registration_without_policy_keeps_global_behavior() -> None:
    """Only the score-facing enabled flag adds serial custody."""
    baton = BatonCore()
    baton.register_instrument("claude-code", max_concurrent=10)
    baton.register_job("legacy", _states(2), {})

    result = await dispatch_ready(
        baton,
        baton.build_dispatch_config(max_concurrent_sheets=10),
        AsyncMock(),
    )

    assert result.dispatched_sheets == [("legacy", 1), ("legacy", 2)]


def test_checkpoint_parallel_policy_is_backward_compatible_and_serializable() -> None:
    legacy = CheckpointState(job_id="legacy", job_name="legacy", total_sheets=1)
    assert legacy.parallel_max_concurrent == 1
    assert legacy.parallel_fail_fast is True
    assert legacy.parallel_stagger_delay_ms == 0

    current = CheckpointState(
        job_id="current",
        job_name="current",
        total_sheets=1,
        parallel_enabled=True,
        parallel_max_concurrent=4,
        parallel_fail_fast=False,
        parallel_stagger_delay_ms=250,
    )
    restored = CheckpointState.model_validate_json(current.model_dump_json())
    assert restored.parallel_max_concurrent == 4
    assert restored.parallel_fail_fast is False
    assert restored.parallel_stagger_delay_ms == 250
