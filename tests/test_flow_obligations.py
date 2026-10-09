"""Adversarial flow obligations against checkpoint-owned baton state."""

import ast
import inspect
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.config.flow import LoopConfig, SheetTriggerConfig, TriggerAction
from marianne.core.config.instruments import InstrumentRouteBinding
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    CancelJob,
    FlowConcertSubmitted,
    JobTimeout,
    SheetAttemptResult,
    SheetSkipped,
)


def _job(size: int = 1) -> CheckpointState:
    return CheckpointState(
        job_id="j", job_name="j", total_sheets=size,
        sheets={n: SheetState(sheet_num=n, instrument_name="cli") for n in range(1, size + 1)},
    )


def _result(
    num: int, epoch: int = 0, *, cost: float = 0, success: bool = True,
) -> SheetAttemptResult:
    return SheetAttemptResult(
        job_id="j", sheet_num=num, instrument_name="cli", attempt=1,
        dispatch_epoch=epoch, execution_success=success,
        validation_pass_rate=100.0 if success else 0.0, cost_usd=cost,
    )


async def test_loop_cost_cap_counts_across_resets_and_marks_uncertainty() -> None:
    checkpoint = _job()
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        loops={"1": LoopConfig(count=10, cost_limit_usd=1.0, index="pass_no")},
    )
    for epoch in range(3):
        await baton.handle_event(replace(_result(1, epoch, cost=0.4), cost_uncertain=True))
    loop = checkpoint.flow.loops["1"]
    assert loop.iteration == 3
    assert loop.completed_reason == "cost_limit_exceeded"
    assert checkpoint.sheets[1].total_cost_usd == 1.2000000000000002
    assert any(
        type(event).__name__ == "LoopIterating" and event.cost_uncertain
        for event in baton.drain_flow_events()
    )


async def test_stale_epoch_cannot_refire_trigger_but_still_charges_money() -> None:
    checkpoint = _job()
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(goto=1)])},
    )
    await baton.handle_event(_result(1, cost=0.2))
    assert checkpoint.sheets[1].dispatch_epoch == 1
    baton.drain_flow_events()
    await baton.handle_event(_result(1, cost=0.3))
    assert checkpoint.sheets[1].dispatch_epoch == 1
    assert checkpoint.sheets[1].status == SheetStatus.PENDING
    assert checkpoint.sheets[1].total_cost_usd == 0.5
    assert baton.drain_flow_events() == []


async def test_resume_reconciles_all_terminal_loop_once() -> None:
    checkpoint = _job()
    checkpoint.sheets[1].status = SheetStatus.COMPLETED
    restored = CheckpointState.model_validate_json(checkpoint.model_dump_json())
    baton = BatonCore()
    loops = {"1": LoopConfig(count=2, index="pass_no")}
    baton.register_job("j", restored.sheets, {}, loops=loops, flow_state=restored.flow)
    assert restored.flow.loops["1"].iteration == 2
    assert restored.sheets[1].status == SheetStatus.PENDING
    assert [type(event).__name__ for event in baton.drain_flow_events()] == ["LoopIterating"]
    next_checkpoint = CheckpointState.model_validate_json(restored.model_dump_json())
    baton2 = BatonCore()
    baton2.register_job(
        "j", next_checkpoint.sheets, {}, loops=loops, flow_state=next_checkpoint.flow,
    )
    assert next_checkpoint.flow.loops["1"].iteration == 2
    assert baton2.drain_flow_events() == []


async def test_runtime_variable_in_until_is_checked_at_boundary() -> None:
    loops = {"1": LoopConfig(until="var.target == 3", index="pass_no")}
    supplied = _job()
    baton = BatonCore()
    baton.register_job(
        "j", supplied.sheets, {}, loops=loops, flow_state=supplied.flow,
        flow_variables={"target": 3},
    )
    await baton.handle_event(_result(1))
    assert supplied.flow.loops["1"].completed_reason == "condition_met"

    missing = _job()
    baton2 = BatonCore()
    baton2.register_job("j", missing.sheets, {}, loops=loops, flow_state=missing.flow)
    await baton2.handle_event(_result(1))
    assert missing.flow.loops["1"].completed_reason == "condition_error"
    assert missing.sheets[1].status == SheetStatus.FAILED
    assert "target" in (missing.sheets[1].error_message or "")


async def test_forward_goto_skips_intervening_sheets_and_bypasses_target_dependency() -> None:
    checkpoint = _job(7)
    checkpoint.sheets[1].status = SheetStatus.COMPLETED
    checkpoint.sheets[2].status = SheetStatus.COMPLETED
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {7: [5]}, flow_state=checkpoint.flow,
        triggers={"3": SheetTriggerConfig(on_success=[TriggerAction(goto=7)])},
    )
    await baton.handle_event(_result(3))
    assert {checkpoint.sheets[n].status for n in (4, 5, 6)} == {SheetStatus.SKIPPED}
    assert all(checkpoint.sheets[n].error_code is None for n in (4, 5, 6))
    assert 7 in checkpoint.flow.goto_bypass
    assert 7 in {sheet.sheet_num for sheet in baton.get_ready_sheets("j")}
    await baton.handle_event(_result(7))
    assert 7 not in checkpoint.flow.goto_bypass


async def test_forward_goto_over_loop_completes_skipped_without_iteration() -> None:
    checkpoint = _job(6)
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        loops={"3-4": LoopConfig(count=3, index="pass_no")},
        triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(goto=6)])},
    )
    await baton.handle_event(_result(1))
    assert checkpoint.flow.loops["3-4"].completed_reason == "skipped"
    assert checkpoint.flow.loops["3-4"].iteration == 1
    assert checkpoint.sheets[3].status == checkpoint.sheets[4].status == SheetStatus.SKIPPED


async def test_matching_triggers_order_narrowest_span_first() -> None:
    checkpoint = _job(9)
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={
            "1-9": SheetTriggerConfig(on_success=[TriggerAction(run="echo wide")]),
            "2-5": SheetTriggerConfig(on_success=[TriggerAction(run="echo middle")]),
            "3": SheetTriggerConfig(on_success=[TriggerAction(run="echo narrow")]),
        },
    )
    await baton.handle_event(_result(3))
    assert [action.run for action in checkpoint.flow.chains[0].actions] == [
        "echo narrow", "echo middle", "echo wide",
    ]


async def test_guarded_failure_cannot_fire_goto_trigger() -> None:
    checkpoint = _job()
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
        triggers={"1": SheetTriggerConfig(on_fail=[TriggerAction(goto=1)])},
    )
    await baton.handle_event(_result(1, success=False))
    assert checkpoint.sheets[1].status == SheetStatus.FAILED
    assert checkpoint.sheets[1].dispatch_epoch == 0
    assert checkpoint.flow.chains == []


def test_terminal_status_writes_have_one_funnel_owner() -> None:
    source_path = inspect.getsourcefile(BatonCore)
    assert source_path is not None
    source = ast.parse(Path(source_path).read_text())
    writes: list[str] = []
    terminal_calls: list[str] = []
    terminal_names = {"COMPLETED", "FAILED", "SKIPPED", "CANCELLED"}
    for method in (node for node in ast.walk(source) if isinstance(node, ast.FunctionDef)):
        for node in ast.walk(method):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Attribute) and target.attr == "status"
                for target in node.targets
            ) and (isinstance(node.value, ast.Attribute)
                   and node.value.attr in terminal_names
                   or isinstance(node.value, ast.Name) and node.value.id == "status"):
                writes.append(method.name)
            if isinstance(node, ast.Call) and any(
                isinstance(arg, ast.Attribute)
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "BatonSheetStatus"
                and arg.attr in terminal_names
                for arg in node.args
            ):
                assert isinstance(node.func, ast.Attribute)
                terminal_calls.append(node.func.attr)
    assert terminal_calls
    assert set(terminal_calls) == {"_set_sheet_terminal_status"}, terminal_calls
    assert set(writes) == {"_set_sheet_terminal_status"}, writes


async def test_non_attempt_skip_and_cancel_settle_loop_and_queued_cleanup() -> None:
    checkpoint = _job(2)
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        loops={"1-2": LoopConfig(count=2, index="pass_no")},
    )
    checkpoint.flow.queued_skips[2] = "queued in-flight skip"
    checkpoint.flow.goto_bypass.append(2)
    await baton.handle_event(SheetSkipped(job_id="j", sheet_num=2, reason="skip_when"))
    assert checkpoint.flow.queued_skips == {}
    assert checkpoint.flow.goto_bypass == []
    assert checkpoint.sheets[2].status == SheetStatus.SKIPPED
    await baton.handle_event(CancelJob(job_id="j"))
    assert checkpoint.flow.loops["1-2"].completed_reason == "range_failed"


async def test_job_wall_timeout_stops_an_infinite_goto_after_resets() -> None:
    checkpoint = _job()
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(goto=1)])},
    )
    for epoch in range(2):
        await baton.handle_event(_result(1, epoch, cost=0.1))
        assert checkpoint.sheets[1].status == SheetStatus.PENDING
    await baton.handle_event(JobTimeout(job_id="j"))
    assert baton.get_ready_sheets("j") == []
    assert checkpoint.sheets[1].status == SheetStatus.CANCELLED
    await baton.handle_event(_result(1, 2, cost=0.1))
    assert checkpoint.sheets[1].status == SheetStatus.CANCELLED


async def test_queued_in_flight_skip_settles_when_sheet_returns() -> None:
    checkpoint = _job(2)
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(skip="2")])},
    )
    checkpoint.sheets[2].status = SheetStatus.IN_PROGRESS
    await baton.handle_event(_result(1))
    assert 2 in checkpoint.flow.queued_skips
    await baton.handle_event(_result(2))
    assert checkpoint.flow.queued_skips == {}
    assert checkpoint.sheets[2].status == SheetStatus.COMPLETED


async def test_concert_action_waits_for_submission_result_before_next_action() -> None:
    checkpoint = _job()
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {}, flow_state=checkpoint.flow,
        triggers={"1": SheetTriggerConfig(on_success=[
            TriggerAction(concert="child.yaml"), TriggerAction(goto=1),
        ])},
    )
    await baton.handle_event(_result(1))
    chain = checkpoint.flow.chains[0]
    assert chain.phase == "awaiting_concert"
    assert checkpoint.sheets[1].status == SheetStatus.COMPLETED
    await baton.handle_event(FlowConcertSubmitted(
        job_id="j", chain_id=chain.chain_id, cursor=chain.cursor,
        accepted=True, child_job_id="child-1", message=None,
    ))
    assert checkpoint.flow.chains == []
    assert checkpoint.sheets[1].status == SheetStatus.PENDING


async def test_failed_member_cascades_range_failed_without_reopening_loop() -> None:
    checkpoint = _job(2)
    baton = BatonCore()
    baton.register_job(
        "j", checkpoint.sheets, {2: [1]}, flow_state=checkpoint.flow,
        loops={"1-2": LoopConfig(count=3, index="pass_no")},
        triggers={"1": SheetTriggerConfig(on_fail=[TriggerAction(continue_=True)])},
    )
    await baton.handle_event(_result(1, success=False))
    assert checkpoint.sheets[1].status == SheetStatus.FAILED
    assert checkpoint.sheets[2].status == SheetStatus.SKIPPED
    assert checkpoint.flow.loops["1-2"].completed_reason == "range_failed"
    assert checkpoint.flow.loops["1-2"].iteration == 1


def test_expression_core_import_boundary_is_daemon_free() -> None:
    import marianne.core.expressions as expressions

    root = Path(expressions.__file__).parent
    files = sorted(root.rglob("*.py"))
    assert files
    forbidden = ("marianne.daemon", "marianne.execution", "marianne.ipc")
    for path in files:
        source = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(source):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module or ""]
            else:
                continue
            assert not any(
                module == prefix or module.startswith(prefix + ".")
                for module in modules for prefix in forbidden
            ), (path, modules)
