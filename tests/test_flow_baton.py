"""Flow transitions against the real baton state and persisted checkpoint."""

from dataclasses import replace
from datetime import UTC, datetime

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.config.flow import LoopConfig, SheetTriggerConfig, TriggerAction
from marianne.core.config.instruments import InstrumentRouteBinding
from marianne.core.expressions import FileFacts
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import FlowRunFinished, LoopFactsReady, SheetAttemptResult


def _checkpoint() -> CheckpointState:
    state = SheetState(sheet_num=1, instrument_name="cli")
    state.remember_primary_identity()
    return CheckpointState(
        job_id="j", job_name="j", total_sheets=1, sheets={1: state}
    )


def _result(epoch: int, *, success: bool = True) -> SheetAttemptResult:
    return SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="cli", attempt=1,
        dispatch_epoch=epoch, execution_success=success,
        validation_pass_rate=100.0 if success else 0.0,
    )


async def test_count_loop_reopens_and_survives_checkpoint_round_trip() -> None:
    checkpoint = _checkpoint()
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {},
        loops={"1": LoopConfig(count=2, index="pass")}, flow_state=checkpoint.flow,
    )
    await baton.handle_event(_result(0))
    assert any(type(event).__name__ == "LoopIterating" for event in baton.drain_flow_events())
    assert checkpoint.sheets[1].status == SheetStatus.PENDING
    assert checkpoint.sheets[1].dispatch_epoch == 1
    assert checkpoint.flow.loops["1"].iteration == 2
    restored = CheckpointState.model_validate_json(checkpoint.model_dump_json())
    baton2 = BatonCore()
    baton2.register_job(
        "j", restored.sheets, {},
        loops={"1": LoopConfig(count=2, index="pass")}, flow_state=restored.flow,
    )
    await baton2.handle_event(_result(1))
    assert any(type(event).__name__ == "LoopCompleted" for event in baton2.drain_flow_events())
    assert restored.sheets[1].status == SheetStatus.COMPLETED
    assert restored.flow.loops["1"].phase == "completed"
    assert restored.flow.loops["1"].completed_reason == "count_reached"
    assert baton2.is_job_complete("j")


async def test_on_fail_goto_self_bypasses_retry_budget() -> None:
    checkpoint = _checkpoint()
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_fail=[TriggerAction(goto=1)])},
    )
    await baton.handle_event(_result(0, success=False))
    assert {type(event).__name__ for event in baton.drain_flow_events()} >= {
        "SheetTriggerFired", "GotoRequested",
    }
    state = checkpoint.sheets[1]
    assert state.status == SheetStatus.PENDING
    assert state.dispatch_epoch == 1
    assert state.normal_attempts == 0
    assert checkpoint.flow.chains == []
    assert 1 in checkpoint.flow.goto_bypass


async def test_file_until_waits_for_facts_and_ignores_stale_reply() -> None:
    checkpoint = _checkpoint()
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        loops={"1": LoopConfig(until='file("marker.txt").contains("done")', index="pass")},
    )
    await baton.handle_event(_result(0))
    request = checkpoint.flow.loops["1"].facts_request_id
    assert request is not None
    assert not baton.is_job_complete("j")
    assert baton.get_ready_sheets("j") == []
    await baton.handle_event(LoopFactsReady(
        job_id="j", span="1", request_id=request,
        files={"marker.txt": FileFacts(True, True, "working")},
    ))
    assert checkpoint.sheets[1].status == SheetStatus.PENDING
    assert checkpoint.flow.loops["1"].iteration == 2
    await baton.handle_event(_result(1))
    assert checkpoint.flow.loops["1"].phase == "awaiting_facts"
    await baton.handle_event(LoopFactsReady(
        job_id="j", span="1", request_id=request,
        files={"marker.txt": FileFacts(True, True, "done")},
    ))
    assert checkpoint.flow.loops["1"].phase == "awaiting_facts"
    await baton.handle_event(LoopFactsReady(
        job_id="j", span="1", request_id=checkpoint.flow.loops["1"].facts_request_id or 0,
        files={"marker.txt": FileFacts(True, True, "done")},
    ))
    assert checkpoint.flow.loops["1"].completed_reason == "condition_met"
    assert baton.is_job_complete("j")


async def test_run_action_holds_chain_until_result_then_goto() -> None:
    checkpoint = _checkpoint()
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_success=[
            TriggerAction(run="printf done"), TriggerAction(goto=1),
        ])},
    )
    await baton.handle_event(_result(0))
    assert checkpoint.sheets[1].status == SheetStatus.COMPLETED
    assert checkpoint.flow.chains[0].phase == "awaiting_run"
    assert checkpoint.flow.chains[0].cursor == 0
    assert not baton.is_job_complete("j")
    assert baton._jobs["j"].flow is not None
    request = baton._jobs["j"].flow.drain_effects()[0]
    assert request.action.run == "printf done"
    await baton.handle_event(FlowRunFinished(
        job_id="j", chain_id=request.chain_id, cursor=request.cursor,
        exit_code=0, timed_out=False, log_path="flow.log",
    ))
    assert checkpoint.flow.chains == []
    assert checkpoint.sheets[1].status == SheetStatus.PENDING
    assert checkpoint.sheets[1].dispatch_epoch == 1


async def test_guarded_route_success_suppresses_trigger() -> None:
    checkpoint = _checkpoint()
    checkpoint.sheets[1].expected_route = InstrumentRouteBinding(
        arm="remote", instrument="cli", kind="cli", profile_origin="venue",
        profile_file_sha256=None, effective_model="reviewed", effective_provider=None,
        model_source="profile", provider_source=None, transport_scheme=None,
        transport_host=None, transport_port=None, transport_endpoint=None,
        resolved_at=datetime.now(UTC),
    )
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(goto=1)])},
    )
    await baton.handle_event(_result(0))
    assert checkpoint.sheets[1].status == SheetStatus.COMPLETED
    assert checkpoint.flow.chains == []
    assert baton.drain_flow_events() == []


async def test_trigger_pause_on_last_sheet_blocks_completion() -> None:
    checkpoint = _checkpoint()
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(pause=True)])},
    )
    await baton.handle_event(_result(0))
    assert checkpoint.flow.pause_reason == "trigger on sheet 1"
    assert checkpoint.sheets[1].status == SheetStatus.COMPLETED
    assert not baton.is_job_complete("j")


async def test_infinite_goto_stops_at_job_cost_limit() -> None:
    checkpoint = _checkpoint()
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(goto=1)])},
    )
    baton.set_job_cost_limit("j", 0.5)
    await baton.handle_event(replace(_result(0), cost_usd=0.3))
    assert checkpoint.sheets[1].status == SheetStatus.PENDING
    await baton.handle_event(replace(_result(1), cost_usd=0.3))
    assert checkpoint.sheets[1].total_cost_usd == 0.6
    assert baton.get_job_pause_reason("j") == "cost_limit_exceeded"
    assert baton.get_ready_sheets("j") == []


async def test_expanded_fanout_span_resets_every_instance() -> None:
    states = {
        num: SheetState(sheet_num=num, instrument_name="cli") for num in (1, 2, 3)
    }
    checkpoint = CheckpointState(
        job_id="j", job_name="j", total_sheets=3, sheets=states,
    )
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        loops={"1-3": LoopConfig(count=2, index="pass_no")},
    )
    for num in (3, 2):
        await baton.handle_event(replace(_result(0), sheet_num=num))
    assert checkpoint.flow.loops["1-3"].iteration == 1
    await baton.handle_event(replace(_result(0), sheet_num=1))
    assert checkpoint.flow.loops["1-3"].iteration == 2
    assert all(state.status == SheetStatus.PENDING for state in states.values())
    assert {state.dispatch_epoch for state in states.values()} == {1}


async def test_file_condition_false_restarts_nested_loop() -> None:
    states = {num: SheetState(sheet_num=num, instrument_name="cli") for num in (1, 2)}
    checkpoint = CheckpointState(job_id="j", job_name="j", total_sheets=2, sheets=states)
    baton = BatonCore()
    baton.register_job(
        "j", states, {}, flow_state=checkpoint.flow,
        loops={
            "1-2": LoopConfig(until='file("marker").exists', index="outer"),
            "1": LoopConfig(count=1, index="inner"),
        },
    )
    await baton.handle_event(replace(_result(0), sheet_num=1))
    assert checkpoint.flow.loops["1"].phase == "completed"
    await baton.handle_event(replace(_result(0), sheet_num=2))
    request = checkpoint.flow.loops["1-2"].facts_request_id
    assert request is not None
    await baton.handle_event(LoopFactsReady(
        job_id="j", span="1-2", request_id=request,
        files={"marker": FileFacts(False, False, None)},
    ))
    assert checkpoint.flow.loops["1-2"].iteration == 2
    assert checkpoint.flow.loops["1"].phase == "running"
    assert checkpoint.flow.loops["1"].iteration == 1
    assert {state.dispatch_epoch for state in states.values()} == {1}
