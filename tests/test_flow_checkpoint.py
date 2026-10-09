"""Persisted state at flow reset boundaries."""

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.flow_state import LoopRunState
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult


def test_loop_state_round_trips_with_checkpoint() -> None:
    checkpoint = CheckpointState(job_id="j", job_name="j", total_sheets=1)
    checkpoint.flow.loops["1"] = LoopRunState(span="1", index_name="pass")
    restored = CheckpointState.model_validate_json(checkpoint.model_dump_json())
    assert restored.flow.loops["1"].iteration == 1
    restored.flow.loops["1"].iteration = 2
    assert restored.flow.loops["1"].iteration == 2


def test_flow_reset_restores_primary_identity_and_keeps_cost() -> None:
    state = SheetState(
        sheet_num=1,
        status=SheetStatus.COMPLETED,
        instrument_name="primary",
        model=None,
        fallback_chain=["backup"],
        fallback_configs=[{"model": "backup-model"}],
        total_cost_usd=1.25,
    )
    state.remember_primary_identity()
    state.advance_fallback("failed")
    assert (state.instrument_name, state.model) == ("backup", "backup-model")
    state.reset_for_flow("loop iteration", {"pass": 1})
    assert (state.instrument_name, state.model, state.instrument_model) == (
        "primary", None, None,
    )
    assert state.current_instrument_index == 0
    assert state.dispatch_epoch == 1
    assert state.total_cost_usd == 1.25
    assert state.flow_history[0].status == SheetStatus.COMPLETED
    assert state.advance_fallback("failed") == "backup"


def test_retry_reset_restores_primary_identity() -> None:
    state = SheetState(
        sheet_num=1,
        instrument_name="primary",
        fallback_chain=["backup"],
        fallback_configs=[{"model": "backup-model"}],
    )
    state.remember_primary_identity()
    state.advance_fallback("failed")
    state.reset_for_retry()
    assert (state.instrument_name, state.model, state.instrument_model) == (
        "primary", None, None,
    )


async def test_stale_epoch_charges_money_without_completing_new_iteration() -> None:
    state = SheetState(sheet_num=1, instrument_name="primary")
    baton = BatonCore()
    baton.register_job("j", {1: state}, {})
    baton.set_job_cost_limit("j", 0.5)
    state.reset_for_flow("loop iteration", {"pass": 1})
    await baton.handle_event(SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="primary", attempt=1,
        dispatch_epoch=0, execution_success=True, cost_usd=0.75,
    ))
    assert state.status == SheetStatus.PENDING
    assert state.total_cost_usd == 0.75
    assert baton.get_job_pause_reason("j") == "cost_limit_exceeded"
