"""Adversarial custody checks for reused baton job identifiers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.config.a2a import AgentCard
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


class _BlockingA2AEventBus:
    def __init__(self, blocked_event: str) -> None:
        self.blocked_event = blocked_event
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if event["event"] == self.blocked_event:
            self.entered.set()
            await self.release.wait()


def _register_a2a_pair(adapter: BatonAdapter, workspace: Path) -> int:
    adapter.register_job(
        "target",
        _sheets(workspace / "target"),
        {},
        agent_card=AgentCard(name="target-agent", description="Target"),
    )
    adapter.register_job(
        "source",
        _sheets(workspace / "source"),
        {},
        agent_card=AgentCard(name="source-agent", description="Source"),
    )
    generation = adapter.baton.get_job_generation("source")
    assert isinstance(generation, int)
    return generation


def _a2a_result(generation: int, *descriptions: str) -> SheetAttemptResult:
    return SheetAttemptResult(
        job_id="source",
        sheet_num=1,
        instrument_name="claude-code",
        attempt=1,
        event_generation=generation,
        execution_success=True,
        a2a_requests=[
            {
                "target_agent": "target-agent",
                "task_description": description,
                "context": {},
            }
            for description in descriptions
        ],
    )


@pytest.mark.asyncio
async def test_source_reuse_during_a2a_submitted_publish_cannot_mutate_target(
    tmp_path: Path,
) -> None:
    event_bus = _BlockingA2AEventBus("baton.a2a.task.submitted")
    persist = MagicMock()
    adapter = BatonAdapter(event_bus=event_bus, persist_callback=persist)
    old_generation = _register_a2a_pair(adapter, tmp_path)

    routing = asyncio.create_task(
        adapter._route_a2a_requests(
            _a2a_result(old_generation, "STALE MUST NOT ROUTE")
        )
    )
    await event_bus.entered.wait()
    adapter.deregister_job("source")
    adapter.register_job(
        "source",
        _sheets(tmp_path / "replacement-source"),
        {},
        agent_card=AgentCard(name="source-agent", description="Replacement"),
    )
    event_bus.release.set()
    await routing

    target = adapter.get_a2a_inbox("target")
    assert target is not None
    assert target.get_pending_tasks() == []
    persist.assert_not_called()
    assert [event["event"] for event in event_bus.events] == [
        "baton.a2a.task.submitted"
    ]


@pytest.mark.asyncio
async def test_source_reuse_during_a2a_routed_publish_stops_later_requests(
    tmp_path: Path,
) -> None:
    event_bus = _BlockingA2AEventBus("baton.a2a.task.routed")
    persist = MagicMock()
    adapter = BatonAdapter(event_bus=event_bus, persist_callback=persist)
    old_generation = _register_a2a_pair(adapter, tmp_path)

    routing = asyncio.create_task(
        adapter._route_a2a_requests(
            _a2a_result(old_generation, "valid first", "STALE SECOND")
        )
    )
    await event_bus.entered.wait()
    adapter.deregister_job("source")
    adapter.register_job(
        "source",
        _sheets(tmp_path / "replacement-source"),
        {},
        agent_card=AgentCard(name="source-agent", description="Replacement"),
    )
    event_bus.release.set()
    await routing

    target = adapter.get_a2a_inbox("target")
    assert target is not None
    pending = target.get_pending_tasks()
    assert [task.description for task in pending] == ["valid first"]
    persist.assert_called_once_with("target")
    assert [event["event"] for event in event_bus.events] == [
        "baton.a2a.task.submitted",
        "baton.a2a.task.routed",
    ]


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
async def test_source_reuse_during_fermata_consume_preserves_live_marker(
    tmp_path: Path,
) -> None:
    adapter = BatonAdapter()
    adapter.register_job("reused", _sheets(tmp_path), {})
    old_generation = adapter.baton.get_job_generation("reused")
    assert isinstance(old_generation, int)
    old_state = adapter.baton.get_sheet_state("reused", 1)
    assert old_state is not None
    old_state.status = BatonSheetStatus.FERMATA
    marker_dir = tmp_path / "markers" / "fermata" / "reused"
    marker_dir.mkdir(parents=True)
    marker = marker_dir / "sheet-1.accept"
    marker.touch()

    consume_entered = asyncio.Event()
    consume_allowed = asyncio.Event()
    original_prepare = adapter._prepare_fermata_marker_consumption

    def blocking_prepare(path: Path) -> Path:
        loop.call_soon_threadsafe(consume_entered.set)
        future = asyncio.run_coroutine_threadsafe(consume_allowed.wait(), loop)
        future.result()
        return original_prepare(path)

    loop = asyncio.get_running_loop()
    adapter._prepare_fermata_marker_consumption = blocking_prepare  # type: ignore[method-assign]
    handling = asyncio.create_task(
        adapter._handle_fermata_check(
            FermataCheck(
                job_id="reused",
                sheet_num=1,
                event_generation=old_generation,
            )
        )
    )
    await consume_entered.wait()
    adapter.deregister_job("reused")
    replacement = _states(status=BatonSheetStatus.FERMATA)
    adapter.register_job(
        "reused",
        _sheets(tmp_path),
        {},
        live_sheets=replacement,
    )
    consume_allowed.set()
    await handling

    assert marker.exists()
    assert not (marker_dir / "consumed" / marker.name).exists()
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
@pytest.mark.parametrize("callback_outcome", ["rejected", "raised"])
async def test_dispatch_stops_old_ready_snapshot_after_reuse_on_all_callback_paths(
    callback_outcome: str,
) -> None:
    baton = BatonCore()
    baton.register_instrument("claude-code", max_concurrent=4)
    original = {
        1: _states()[1],
        2: SheetExecutionState(
            sheet_num=2,
            instrument_name="claude-code",
        ),
    }
    baton.register_job("legacy", original, {})
    called: list[int] = []

    async def replace_on_first_callback(
        job_id: str,
        sheet_num: int,
        state: SheetExecutionState,
    ) -> bool:
        del job_id, state
        called.append(sheet_num)
        if sheet_num == 1:
            baton.deregister_job("legacy")
            replacement = {
                1: _states()[1],
                2: SheetExecutionState(
                    sheet_num=2,
                    instrument_name="claude-code",
                ),
            }
            baton.register_job("legacy", replacement, {2: [1]})
            if callback_outcome == "raised":
                raise RuntimeError("replacement during callback")
            return False
        return True

    result = await dispatch_ready(
        baton,
        baton.build_dispatch_config(max_concurrent_sheets=4),
        replace_on_first_callback,
    )

    assert called == [1]
    assert result.dispatched_sheets == []
    assert baton.get_sheet_state("legacy", 2).status == BatonSheetStatus.PENDING  # type: ignore[union-attr]


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
