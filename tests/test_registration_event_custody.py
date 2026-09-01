"""Adversarial custody checks for reused baton job identifiers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import dispatch_ready
from marianne.daemon.baton.events import (
    CancelJob,
    EscalationNeeded,
    EscalationResolved,
    EscalationTimeout,
    FermataCheck,
    JobTimeout,
    PauseJob,
    ProcessExited,
    ResumeJob,
    SheetAttemptResult,
    SheetDispatched,
    SheetSkipped,
    StaleCheck,
)
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState


def _states(
    *, status: BatonSheetStatus = BatonSheetStatus.PENDING
) -> dict[int, SheetExecutionState]:
    return {
        1: SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            status=status,
        )
    }


def _sheets(workspace: Path = Path("/tmp/event-custody")) -> list[Sheet]:
    return [
        Sheet(
            num=1,
            movement=1,
            voice=1,
            voice_count=1,
            workspace=workspace,
            instrument_name="claude-code",
            prompt_template="test",
            timeout_seconds=60.0,
        )
    ]


def _managed_reuse(
    *, replacement_status: BatonSheetStatus
) -> tuple[BatonAdapter, int, SheetExecutionState]:
    adapter = BatonAdapter()
    adapter.register_job("reused", _sheets(), {})
    old_generation = adapter.baton.get_job_generation("reused")
    assert isinstance(old_generation, int)
    adapter.deregister_job("reused")
    replacement = _states(status=replacement_status)
    adapter.register_job("reused", _sheets(), {}, live_sheets=replacement)
    assert adapter.baton.get_job_generation("reused") != old_generation
    return adapter, old_generation, replacement[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_factory", "initial_status"),
    [
        (
            lambda generation: SheetSkipped(
                job_id="reused",
                sheet_num=1,
                reason="old skip",
                event_generation=generation,
            ),
            BatonSheetStatus.PENDING,
        ),
        (
            lambda generation: SheetDispatched(
                job_id="reused",
                sheet_num=1,
                instrument="claude-code",
                event_generation=generation,
            ),
            BatonSheetStatus.PENDING,
        ),
        (
            lambda generation: JobTimeout(
                job_id="reused",
                event_generation=generation,
            ),
            BatonSheetStatus.PENDING,
        ),
        (
            lambda generation: EscalationNeeded(
                job_id="reused",
                sheet_num=1,
                reason="old escalation",
                event_generation=generation,
            ),
            BatonSheetStatus.PENDING,
        ),
        (
            lambda generation: EscalationResolved(
                job_id="reused",
                sheet_num=1,
                decision="accept",
                event_generation=generation,
            ),
            BatonSheetStatus.FERMATA,
        ),
        (
            lambda generation: EscalationTimeout(
                job_id="reused",
                sheet_num=1,
                event_generation=generation,
            ),
            BatonSheetStatus.FERMATA,
        ),
        (
            lambda generation: ProcessExited(
                job_id="reused",
                sheet_num=1,
                pid=123,
                event_generation=generation,
            ),
            BatonSheetStatus.DISPATCHED,
        ),
    ],
)
async def test_old_managed_events_cannot_mutate_replacement(
    event_factory: object,
    initial_status: BatonSheetStatus,
) -> None:
    adapter, old_generation, replacement = _managed_reuse(
        replacement_status=initial_status
    )

    await adapter.baton.handle_event(event_factory(old_generation))  # type: ignore[operator]

    assert replacement.status == initial_status
    assert replacement.normal_attempts == 0


@pytest.mark.asyncio
async def test_old_stale_check_cannot_mint_replacement_generation_result() -> None:
    adapter, old_generation, replacement = _managed_reuse(
        replacement_status=BatonSheetStatus.DISPATCHED
    )
    await adapter._handle_stale_check(
        StaleCheck(
            job_id="reused",
            sheet_num=1,
            event_generation=old_generation,
        )
    )

    assert replacement.status == BatonSheetStatus.DISPATCHED
    assert not any(
        isinstance(queued, SheetAttemptResult)
        for queued in adapter.baton.inbox._queue
    )


@pytest.mark.asyncio
async def test_old_managed_operator_commands_cannot_target_replacement() -> None:
    adapter, old_generation, replacement = _managed_reuse(
        replacement_status=BatonSheetStatus.PENDING
    )
    job = adapter.baton._jobs["reused"]

    await adapter.baton.handle_event(
        PauseJob(job_id="reused", event_generation=old_generation)
    )
    assert job.paused is False

    job.paused = True
    job.user_paused = True
    await adapter.baton.handle_event(
        ResumeJob(job_id="reused", event_generation=old_generation)
    )
    assert job.paused is True
    assert job.user_paused is True

    await adapter.baton.handle_event(
        CancelJob(job_id="reused", event_generation=old_generation)
    )
    assert adapter.baton._jobs["reused"] is job
    assert replacement.status == BatonSheetStatus.PENDING

@pytest.mark.asyncio
async def test_old_fermata_check_cannot_resolve_after_filesystem_await(
    tmp_path: Path,
) -> None:
    adapter = BatonAdapter()
    adapter.register_job("reused", _sheets(tmp_path), {})
    old_generation = adapter.baton.get_job_generation("reused")
    assert isinstance(old_generation, int)
    old_event = FermataCheck(
        job_id="reused",
        sheet_num=1,
        event_generation=old_generation,
    )
    old_state = adapter.baton.get_sheet_state("reused", 1)
    assert old_state is not None
    old_state.status = BatonSheetStatus.FERMATA
    marker_dir = tmp_path / "markers" / "fermata" / "reused"
    marker_dir.mkdir(parents=True)
    marker = marker_dir / "sheet-1.accept"
    marker.touch()

    scan_entered = asyncio.Event()
    scan_allowed = asyncio.Event()
    original_scan = adapter._scan_fermata_markers

    def blocking_scan(path: Path, sheet_num: int) -> list[Path]:
        loop.call_soon_threadsafe(scan_entered.set)
        future = asyncio.run_coroutine_threadsafe(scan_allowed.wait(), loop)
        future.result()
        return original_scan(path, sheet_num)

    loop = asyncio.get_running_loop()
    adapter._scan_fermata_markers = blocking_scan  # type: ignore[method-assign]
    task = asyncio.create_task(adapter._handle_fermata_check(old_event))
    await scan_entered.wait()

    adapter.deregister_job("reused")
    replacement = _states(status=BatonSheetStatus.FERMATA)
    adapter.register_job(
        "reused",
        _sheets(tmp_path),
        {},
        live_sheets=replacement,
    )
    scan_allowed.set()
    await task

    assert replacement[1].status == BatonSheetStatus.FERMATA
    assert not any(
        isinstance(queued, EscalationResolved)
        for queued in adapter.baton.inbox._queue
    )


@pytest.mark.asyncio
async def test_direct_legacy_generationless_event_is_accepted_while_live() -> None:
    baton = BatonCore()
    states = _states(status=BatonSheetStatus.DISPATCHED)
    baton.register_job("legacy", states, {})

    assert baton.get_job_generation("legacy") is None
    await baton.handle_event(
        SheetAttemptResult(
            job_id="legacy",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
        )
    )

    assert states[1].status == BatonSheetStatus.COMPLETED


@pytest.mark.asyncio
async def test_private_token_blocks_legacy_none_to_none_reuse_during_callback() -> None:
    baton = BatonCore()
    baton.register_instrument("claude-code", max_concurrent=2)
    original = _states()
    baton.register_job("legacy", original, {})
    old_token = baton.get_job_registration_token("legacy")
    assert baton.get_job_generation("legacy") is None

    async def replace_during_callback(
        job_id: str,
        sheet_num: int,
        state: SheetExecutionState,
    ) -> bool:
        del job_id, sheet_num, state
        baton.deregister_job("legacy")
        baton.register_job("legacy", _states(), {})
        await asyncio.sleep(0)
        return True

    result = await dispatch_ready(
        baton,
        baton.build_dispatch_config(max_concurrent_sheets=2),
        replace_during_callback,
    )

    assert baton.get_job_generation("legacy") is None
    assert baton.get_job_registration_token("legacy") != old_token
    assert result.dispatched_sheets == []
    assert baton.get_sheet_state("legacy", 1).status == BatonSheetStatus.PENDING  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_private_token_blocks_reuse_during_core_healing_await() -> None:
    timer = MagicMock()
    baton = BatonCore(timer=timer)
    old = _states(status=BatonSheetStatus.DISPATCHED)
    old[1].max_retries = 0
    baton.register_job(
        "healing",
        old,
        {},
        self_healing_enabled=True,
        event_generation=1,
    )
    healing_entered = asyncio.Event()
    healing_allowed = asyncio.Event()

    async def blocked_healing(*_args: object) -> None:
        healing_entered.set()
        await healing_allowed.wait()

    baton._run_healing = AsyncMock(side_effect=blocked_healing)
    handling = asyncio.create_task(
        baton.handle_event(
            SheetAttemptResult(
                job_id="healing",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                event_generation=1,
                execution_success=False,
            )
        )
    )
    await healing_entered.wait()
    baton.deregister_job("healing")
    replacement = _states()
    baton.register_job("healing", replacement, {}, event_generation=2)
    healing_allowed.set()
    await handling

    assert replacement[1].status == BatonSheetStatus.PENDING
    timer.schedule.assert_not_called()


def test_cleanup_tracking_is_bounded_across_unique_recurrence_ids() -> None:
    adapter = BatonAdapter()

    for number in range(2_000):
        job_id = f"recurrence-{number}"
        adapter.register_job(job_id, _sheets(), {})
        adapter.deregister_job(job_id)

    assert adapter._cleanup_generations == {}
    assert adapter._cleanup_results == {}
