"""Per-score parallel policy remains owned by its job across dispatch/recovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.checkpoint import CheckpointState
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import dispatch_ready
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState


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
    adapter.baton._stagger_wake_pending.add("recovered")
    adapter.deregister_job("recovered")
    assert not any(key[0] == "recovered" for key in adapter.baton._job_last_dispatch_at)
    assert "recovered" not in adapter.baton._stagger_wake_pending


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
