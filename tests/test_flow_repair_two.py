"""Regression controls for escalation ownership and queued failure settlement."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.config.flow import SheetTriggerConfig, TriggerAction
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    EscalationResolved,
    EscalationTimeout,
    PauseJob,
    SheetAttemptResult,
)


def job(size: int = 3) -> CheckpointState:
    sheets = {n: SheetState(sheet_num=n, instrument_name="cli") for n in range(1, size + 1)}
    for sheet in sheets.values():
        sheet.remember_primary_identity()
    return CheckpointState(job_id="j", job_name="j", total_sheets=size, sheets=sheets)


def result(
    num: int, *, success: bool = False, cost: float = 0.0, classification: str | None = None
) -> SheetAttemptResult:
    return SheetAttemptResult(
        job_id="j",
        sheet_num=num,
        instrument_name="cli",
        attempt=1,
        dispatch_epoch=0,
        execution_success=success,
        validation_pass_rate=100.0 if success else 0.0,
        cost_usd=cost,
        error_classification=classification,
        error_message="auth denied" if classification else None,
    )


@pytest.mark.parametrize("decision", ["retry", "skip", "timeout"])
@pytest.mark.parametrize("order", [(1, 2), (2, 1)])
async def test_escalation_pause_tracks_each_owner(decision: str, order: tuple[int, int]) -> None:
    checkpoint = job(2)
    baton = BatonCore()
    baton.register_job(
        "j",
        checkpoint.sheets,
        {},
        flow_state=checkpoint.flow,
        max_concurrent=2,
        triggers={
            str(n): SheetTriggerConfig(on_fail=[TriggerAction(escalate=f"check-{n}")])
            for n in (1, 2)
        },
    )
    for n in (1, 2):
        await baton.handle_event(result(n))
    assert checkpoint.flow.escalation_pause_owners == {1, 2}
    for n in order:
        event = (
            EscalationTimeout(job_id="j", sheet_num=n)
            if decision == "timeout"
            else EscalationResolved(job_id="j", sheet_num=n, decision=decision)
        )
        await baton.handle_event(event)
        assert checkpoint.flow.escalation_pause_owners == (
            {1, 2} - set(order[: order.index(n) + 1])
        )
    assert checkpoint.flow.pause_reason is None
    assert not baton.is_job_paused("j")
    assert not baton._jobs["j"].user_paused
    assert all(sheet.status != SheetStatus.FERMATA for sheet in checkpoint.sheets.values())


async def test_escalation_release_preserves_operator_pause() -> None:
    checkpoint = job(1)
    baton = BatonCore()
    baton.register_job(
        "j",
        checkpoint.sheets,
        {},
        flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_fail=[TriggerAction(escalate="check")])},
    )
    await baton.handle_event(result(1))
    assert baton.request_pause("j")
    await baton.handle_event(EscalationResolved(job_id="j", sheet_num=1, decision="retry"))
    assert checkpoint.flow.escalation_pause_owners == set()
    assert checkpoint.flow.pause_reason is None
    assert baton.is_job_paused("j")
    assert baton._jobs["j"].user_paused
    await baton.handle_event(PauseJob(job_id="j"))
    assert baton.is_job_paused("j")


@pytest.mark.parametrize("order", ["escalate_then_pause", "pause_then_escalate"])
async def test_trigger_pause_survives_escalation_resolution(order: str) -> None:
    """Forge repair-2 Inspect P1: `on_fail: [escalate, pause]` must still hold the
    job after `mzt resolve … retry`; only an explicit operator resume releases it."""
    actions = [TriggerAction(escalate="check"), TriggerAction(pause=True)]
    if order == "pause_then_escalate":
        actions.reverse()
    checkpoint = job(1)
    baton = BatonCore()
    baton.register_job(
        "j",
        checkpoint.sheets,
        {},
        flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_fail=actions)},
    )
    await baton.handle_event(result(1))
    assert checkpoint.flow.escalation_pause_owners == {1}
    assert checkpoint.flow.trigger_pause_reason == "trigger on sheet 1"
    assert baton.is_job_paused("j")

    await baton.handle_event(EscalationResolved(job_id="j", sheet_num=1, decision="retry"))
    assert checkpoint.flow.escalation_pause_owners == set()
    assert checkpoint.sheets[1].status is SheetStatus.PENDING
    # The trigger pause is still owned and still displayed.
    assert checkpoint.flow.trigger_pause_reason == "trigger on sheet 1"
    assert checkpoint.flow.pause_reason == "trigger on sheet 1"
    assert baton.is_job_paused("j")
    assert not baton.is_job_complete("j")

    # Only the operator's explicit resume (what `mzt resume` does to the
    # checkpoint before re-registering) releases it.
    checkpoint.flow.pause_reason = None
    checkpoint.flow.trigger_pause_reason = None
    fresh = BatonCore()
    fresh.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_fail=actions)},
    )
    assert not fresh.is_job_paused("j")


async def test_legacy_checkpoint_trigger_pause_is_reowned_not_lost() -> None:
    """A pre-field checkpoint whose pause_reason came from a trigger `pause`
    (no FERMATA sheet) must be re-owned as a trigger pause on load."""
    checkpoint = job(1)
    checkpoint.flow.pause_reason = "trigger on sheet 1"
    serialized = checkpoint.model_dump(mode="json")
    serialized["flow"].pop("escalation_pause_owners", None)
    serialized["flow"].pop("trigger_pause_reason", None)
    restored = CheckpointState.model_validate_json(json.dumps(serialized))
    baton = BatonCore()
    baton.register_job(
        "j", restored.sheets, {}, flow_state=restored.flow,
        triggers={"1": SheetTriggerConfig(on_fail=[TriggerAction(pause=True)])},
    )
    assert restored.flow.trigger_pause_reason == "trigger on sheet 1"
    assert restored.flow.escalation_pause_owners == set()
    assert baton.is_job_paused("j")


async def test_legacy_escalation_checkpoint_recovers_owners() -> None:
    checkpoint = job(2)
    checkpoint.sheets[1].status = SheetStatus.FERMATA
    checkpoint.sheets[1].fermata_reason = "check-one"
    checkpoint.sheets[2].status = SheetStatus.FERMATA
    checkpoint.sheets[2].fermata_reason = "check-two"
    checkpoint.flow.pause_reason = "check-two"
    serialized = checkpoint.model_dump(mode="json")
    serialized["flow"].pop("escalation_pause_owners")
    restored = CheckpointState.model_validate_json(json.dumps(serialized))
    assert restored.flow.escalation_pause_owners == set()
    baton = BatonCore()
    baton.register_job(
        "j",
        restored.sheets,
        {},
        flow_state=restored.flow,
        triggers={
            "1": SheetTriggerConfig(on_fail=[TriggerAction(escalate="check-one")]),
            "2": SheetTriggerConfig(on_fail=[TriggerAction(escalate="check-two")]),
        },
    )
    assert restored.flow.escalation_pause_owners == {1, 2}
    for n in (2, 1):
        await baton.handle_event(EscalationResolved(job_id="j", sheet_num=n, decision="retry"))
    assert restored.flow.pause_reason is None
    assert not baton.is_job_paused("j")


async def test_adapter_resolve_fermata_newest_first_releases_pause(tmp_path: Path) -> None:
    checkpoint = job(2)
    adapter = BatonAdapter()
    adapter.register_job(
        "j",
        [
            Sheet(
                num=n,
                movement=1,
                voice_count=1,
                workspace=tmp_path,
                instrument_name="cli",
                prompt_template="echo done",
            )
            for n in (1, 2)
        ],
        {},
        live_sheets=checkpoint.sheets,
        flow_state=checkpoint.flow,
        triggers={
            str(n): SheetTriggerConfig(on_fail=[TriggerAction(escalate=f"check-{n}")])
            for n in (1, 2)
        },
    )
    generation = adapter.baton.get_job_generation("j")
    for n in (1, 2):
        await adapter.baton.handle_event(replace(result(n), event_generation=generation))
    for n in (2, 1):
        accepted, message = adapter.resolve_fermata("j", n, "retry")
        assert accepted, message
        checks = []
        while not adapter.baton.inbox.empty():
            event = adapter.baton.inbox.get_nowait()
            if type(event).__name__ == "FermataCheck":
                checks.append(event)
        for check in checks:
            await adapter._handle_fermata_check(check)
        while not adapter.baton.inbox.empty():
            event = adapter.baton.inbox.get_nowait()
            if isinstance(event, EscalationResolved):
                await adapter.baton.handle_event(event)
    assert checkpoint.flow.pause_reason is None
    assert checkpoint.flow.escalation_pause_owners == set()
    assert not adapter.baton.is_job_paused("j")


@pytest.mark.parametrize("path", ["exhaustion", "cost_limit", "auth", "resolve_fail", "timeout"])
async def test_queued_skip_is_clean_on_every_failed_path(path: str) -> None:
    checkpoint = job()
    checkpoint.sheets[2].max_retries = 0 if path in {"exhaustion", "resolve_fail", "timeout"} else 3
    baton = BatonCore()
    baton.register_job(
        "j",
        checkpoint.sheets,
        {3: [2]},
        flow_state=checkpoint.flow,
        escalation_enabled=path in {"resolve_fail", "timeout"},
        triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(skip="2")])},
    )
    if path == "cost_limit":
        baton.set_sheet_cost_limit("j", 2, 0.01)
    checkpoint.sheets[2].status = SheetStatus.DISPATCHED
    await baton.handle_event(result(1, success=True))
    assert 2 in checkpoint.flow.queued_skips
    await baton.handle_event(
        result(
            2,
            cost=5.0 if path == "cost_limit" else 0.0,
            classification="AUTH_FAILURE" if path == "auth" else None,
        )
    )
    if path == "resolve_fail":
        await baton.handle_event(EscalationResolved(job_id="j", sheet_num=2, decision="fail"))
    elif path == "timeout":
        await baton.handle_event(EscalationTimeout(job_id="j", sheet_num=2))
    assert checkpoint.sheets[2].status == SheetStatus.SKIPPED
    assert checkpoint.sheets[2].error_code is None
    assert checkpoint.sheets[2].error_message == "trigger skip from 1"
    assert [s.sheet_num for s in baton.get_ready_sheets("j")] == [3]


async def test_unskipped_failure_keeps_error_and_blocks_dependent() -> None:
    checkpoint = job(2)
    checkpoint.sheets[1].max_retries = 0
    baton = BatonCore()
    baton.register_job("j", checkpoint.sheets, {2: [1]}, flow_state=checkpoint.flow)
    await baton.handle_event(result(1))
    assert checkpoint.sheets[1].status == SheetStatus.FAILED
    assert checkpoint.sheets[1].error_code is not None
    assert checkpoint.sheets[2].status == SheetStatus.SKIPPED
    assert baton.get_ready_sheets("j") == []


@pytest.mark.parametrize("path", ["exhaustion", "cost_limit", "auth", "resolve_fail", "timeout"])
async def test_failed_writers_set_error_detail_before_the_terminal_funnel_431(path: str) -> None:
    """GH #431: the queued-skip variants above now settle inside the #428 helper before any
    `_fail_sheet` caller runs, so they no longer prove that each FAILED writer sets
    error detail BEFORE the terminal funnel (the #420 ordering). This pins it directly:
    no queued skip, and the flow engine's `on_terminal` is wrapped to record what the
    sheet carried at the moment the funnel fired."""
    checkpoint = job()
    checkpoint.sheets[2].max_retries = 0 if path in {"exhaustion", "resolve_fail", "timeout"} else 3
    baton = BatonCore()
    baton.register_job(
        "j",
        checkpoint.sheets,
        {3: [2]},
        flow_state=checkpoint.flow,
        escalation_enabled=path in {"resolve_fail", "timeout"},
        # An inert trigger elsewhere gives the job a flow engine (the shape every
        # flow-control score has) without queuing anything against sheet 2.
        triggers={"3": SheetTriggerConfig(on_success=[TriggerAction(pause=True)])},
    )
    if path == "cost_limit":
        baton.set_sheet_cost_limit("j", 2, 0.01)
    flow = baton._jobs["j"].flow
    assert flow is not None
    seen_at_terminal: list[tuple[str | None, str | None, str]] = []
    real_on_terminal = flow.on_terminal

    def recording_on_terminal(sheets, sheet_num, outcome=None):  # type: ignore[no-untyped-def]
        if sheet_num == 2:
            s = sheets[2]
            seen_at_terminal.append((s.error_code, s.error_message, s.status.value))
        return real_on_terminal(sheets, sheet_num, outcome)

    flow.on_terminal = recording_on_terminal  # type: ignore[method-assign]

    checkpoint.sheets[2].status = SheetStatus.DISPATCHED
    await baton.handle_event(
        result(
            2,
            cost=5.0 if path == "cost_limit" else 0.0,
            classification="AUTH_FAILURE" if path == "auth" else None,
        )
    )
    if path == "resolve_fail":
        await baton.handle_event(EscalationResolved(job_id="j", sheet_num=2, decision="fail"))
    elif path == "timeout":
        await baton.handle_event(EscalationTimeout(job_id="j", sheet_num=2))

    assert checkpoint.sheets[2].status == SheetStatus.FAILED
    assert checkpoint.sheets[2].error_code is not None
    assert checkpoint.sheets[2].error_message
    # The funnel saw the sheet FAILED with its detail already written.
    failed_seen = [row for row in seen_at_terminal if row[2] == "failed"]
    assert failed_seen, seen_at_terminal
    code, message, _ = failed_seen[-1]
    assert code == checkpoint.sheets[2].error_code
    assert message == checkpoint.sheets[2].error_message
    # And the dependent was cascaded, not released (sheet 1 never ran and stays ready).
    assert checkpoint.sheets[3].status == SheetStatus.SKIPPED
    assert 3 not in [s.sheet_num for s in baton.get_ready_sheets("j")]
