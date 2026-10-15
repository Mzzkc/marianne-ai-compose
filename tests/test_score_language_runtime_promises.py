"""Score keys promised by the schema reach their real consumers."""

from __future__ import annotations

import asyncio
from pathlib import Path

from marianne.core.checkpoint import CheckpointState, SheetState
from marianne.core.config import JobConfig
from marianne.core.sheet import Sheet, build_sheets
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState


def test_movement_voices_expand_through_score_loader() -> None:
    config = JobConfig.from_yaml_string(
        "name: voices\nworkspace: ./output\n"
        "movements:\n  2: {voices: 3}\n"
        "sheet:\n  size: 1\n  total_items: 3\n"
        "  dependencies: {2: [1], 3: [2]}\n"
        "prompt: {template: hi}\n"
    )
    sheets = build_sheets(config)
    assert len(sheets) == 5
    assert [(sheet.movement, sheet.voice) for sheet in sheets] == [
        (1, None), (2, 1), (2, 2), (2, 3), (3, None),
    ]
    assert config.sheet.dependencies[5] == [2, 3, 4]


def test_explicit_fan_out_wins_over_movement_voices() -> None:
    config = JobConfig.from_yaml_string(
        "name: voices\nworkspace: ./output\n"
        "movements: {2: {voices: 3}}\n"
        "sheet: {size: 1, total_items: 2, fan_out: {2: 2}}\n"
        "prompt: {template: hi}\n"
    )
    assert len(build_sheets(config)) == 3


def _sheet(num: int) -> Sheet:
    return Sheet(
        num=num, movement=num, voice=None, voice_count=1,
        workspace=Path("/tmp/score-language-promise"),
        instrument_name="cli", prompt_template="hi", timeout_seconds=30,
    )


def test_sheet_cost_limit_is_armed_at_adapter_registration() -> None:
    adapter = BatonAdapter()
    adapter.register_job("cost", [_sheet(1), _sheet(2)], {}, max_sheet_cost_usd=0.5)
    assert adapter._baton._sheet_cost_limits == {("cost", 1): 0.5, ("cost", 2): 0.5}
    states = adapter._baton._jobs["cost"].sheets
    event = SheetAttemptResult(
        job_id="cost", sheet_num=1, instrument_name="cli", attempt=1,
        execution_success=True, validation_pass_rate=100.0,
        validations_total=1, cost_usd=0.6,
        event_generation=adapter._baton._jobs["cost"].event_generation,
    )
    asyncio.run(adapter._baton._handle_attempt_result(event))
    assert states[1].status == BatonSheetStatus.FAILED


def test_sheet_cost_and_completion_gate_survive_adapter_recovery() -> None:
    adapter = BatonAdapter()
    checkpoint = CheckpointState(
        job_id="recover", job_name="recover", total_sheets=1,
        sheets={1: SheetState(sheet_num=1)},
    )
    adapter.recover_job(
        "recover", [_sheet(1)], {}, checkpoint,
        max_sheet_cost_usd=0.5, completion_threshold_percent=50,
    )
    assert adapter._baton._sheet_cost_limits[("recover", 1)] == 0.5
    assert adapter._baton._jobs["recover"].completion_threshold_percent == 50


def test_below_completion_threshold_terminates_with_normal_retry_budget() -> None:
    baton = BatonCore()
    state = SheetExecutionState(
        sheet_num=1, instrument_name="cli", max_retries=3, max_completion=3,
    )
    baton.register_job("threshold", {1: state}, {}, completion_threshold_percent=50)
    for attempt in range(1, 4):
        event = SheetAttemptResult(
            job_id="threshold", sheet_num=1, instrument_name="cli",
            attempt=attempt, execution_success=True,
            validation_pass_rate=20.0, validations_total=5,
        )
        asyncio.run(baton._handle_attempt_result(event))
    assert state.status == BatonSheetStatus.FAILED
    assert state.normal_attempts == 3
    assert state.completion_attempts == 0


def test_above_completion_threshold_enters_completion_mode() -> None:
    baton = BatonCore()
    state = SheetExecutionState(sheet_num=1, instrument_name="cli", max_retries=3)
    baton.register_job("threshold", {1: state}, {}, completion_threshold_percent=50)
    event = SheetAttemptResult(
        job_id="threshold", sheet_num=1, instrument_name="cli", attempt=1,
        execution_success=True, validation_pass_rate=60.0, validations_total=5,
    )
    asyncio.run(baton._handle_attempt_result(event))
    assert state.status == BatonSheetStatus.RETRY_SCHEDULED
    assert state.completion_attempts == 1
    assert state.normal_attempts == 0
