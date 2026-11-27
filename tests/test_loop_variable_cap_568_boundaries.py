"""#568 boundaries: what a variable loop cap refuses, and which bound the baton enforces.

Companion of ``test_loop_variable_cap_568.py``. These cases come from the
independent functional review of GH #568 and are kept so that they outlive it:
the refusal matrix for every value shape on both fields, dotted paths that
break at a step, the per-field bound, copy-on-write when one field fails, a
prebuilt ``SheetConfig``, one CLI issue per bad field, and the runtime
precedence of ``count``, ``max_iterations`` and ``until`` after a snapshot
reload.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.checkpoint import CheckpointState, SheetState
from marianne.core.config import JobConfig
from marianne.core.config.flow import FlowConfigError, FlowIssue
from marianne.core.config.job import PromptConfig, SheetConfig
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult


def _score(variables: dict[str, object], loop: dict[str, object]) -> dict[str, Any]:
    """A raw payload, not YAML, with one loop on sheet 1."""
    return {
        "name": "loop-cap-boundaries",
        "workspace": "./output",
        "prompt": {"template": "hi", "variables": variables},
        "sheet": {"size": 1, "total_items": 2, "loops": {1: {"index": "pass_no", **loop}}},
    }


def _issues(raw: dict[str, Any]) -> list[FlowIssue]:
    """The flow issues of one refusal; fails when the payload loads."""
    with pytest.raises(ValidationError) as caught:
        JobConfig.model_validate(raw)
    found = [
        item
        for detail in caught.value.errors()
        if isinstance((cause := detail.get("ctx", {}).get("error")), FlowConfigError)
        for item in cause.issues
    ]
    assert found, caught.value
    return found


@pytest.mark.parametrize("field", ["count", "max_iterations"])
@pytest.mark.parametrize(
    "value",
    [True, False, 3.0, 3.5, "", "  ", "+3", "-3", "3.0", "1e2", "\u0663", "\uff13", [], {}],
)
def test_non_integer_values_are_coded_for_each_field(field: str, value: object) -> None:
    found = _issues(_score({"n": value}, {"count": 1, field: "{{ n }}"}))
    assert len(found) == 1
    assert (found[0].check, found[0].span) == ("V-FLOW-26", "1")
    assert field in found[0].message
    assert "whole number" in found[0].message


@pytest.mark.parametrize("value", [None, 4, [], {"count": None}, {"other": 3}])
def test_missing_or_non_mapping_dotted_path_is_coded(value: object) -> None:
    found = _issues(_score({"book": value}, {"count": "{{ book.count }}"}))
    assert len(found) == 1
    assert found[0].check == "V-FLOW-26"
    assert "book.count" in found[0].message
    assert "not declared" in found[0].message


@pytest.mark.parametrize("field", ["count", "max_iterations"])
@pytest.mark.parametrize("value", [1, 10000])
def test_each_field_loads_at_the_ends_of_the_loop_bound(field: str, value: int) -> None:
    config = JobConfig.model_validate(_score({"n": value}, {"count": 1, field: "{{ n }}"}))
    assert getattr(config.sheet.loops["1"], field) == value


@pytest.mark.parametrize("field", ["count", "max_iterations"])
@pytest.mark.parametrize("value", [10001, 0, -1])
def test_each_field_refuses_values_outside_the_loop_bound(field: str, value: int) -> None:
    found = _issues(_score({"n": value}, {"count": 1, field: "{{ n }}"}))
    assert len(found) == 1 and found[0].check == "V-FLOW-26"
    assert field in found[0].message
    assert "1-10000" in found[0].message


@pytest.mark.parametrize("model_prompt", [False, True])
def test_both_fields_resolve_together_from_a_deep_path_without_mutation(
    model_prompt: bool,
) -> None:
    raw = _score(
        {"book": {"limits": {"count": " 0003 ", "cap": "05000"}}},
        {"count": "{{ book.limits.count }}", "max_iterations": "{{ book.limits.cap }}"},
    )
    if model_prompt:
        raw["prompt"] = PromptConfig.model_validate(raw["prompt"])
    before = deepcopy(raw)
    config = JobConfig.model_validate(raw)
    assert raw == before
    assert config.sheet.loops["1"].count == 3
    assert config.sheet.loops["1"].max_iterations == 5000
    restored = JobConfig.model_validate_json(config.model_dump_json())
    assert restored.sheet.loops == config.sheet.loops


def test_failure_keeps_successfully_resolved_sibling_input_intact() -> None:
    raw = _score({"good": "3", "bad": True}, {"count": "{{ good }}", "max_iterations": "{{ bad }}"})
    before = deepcopy(raw)
    found = _issues(raw)
    assert len(found) == 1 and "max_iterations" in found[0].message
    assert raw == before


def test_prebuilt_sheet_model_preserves_literal_caps() -> None:
    raw = _score({}, {"count": 99999, "max_iterations": 10000})
    raw["sheet"] = SheetConfig.model_validate(raw["sheet"])
    config = JobConfig.model_validate(raw)
    assert config.sheet.loops["1"].count == 99999
    assert config.sheet.loops["1"].max_iterations == 10000


def test_cli_reports_each_bad_field_with_original_span(tmp_path: Path) -> None:
    raw = _score({"bad": False}, {"count": "{{ bad }}", "max_iterations": "{{ missing }}"})
    loops = raw["sheet"]["loops"]
    loops["2-3"] = loops.pop(1)
    raw["sheet"]["total_items"] = 3
    path = tmp_path / "score.yaml"
    path.write_text(json.dumps(raw))
    result = CliRunner().invoke(app, ["validate", str(path), "--json"])
    assert result.exit_code == 1, result.output
    frame = json.loads(result.stdout)
    assert frame["valid"] is False
    assert frame["summary"]["sheets"] is None
    assert len(frame["issues"]) == 2
    for item, field in zip(frame["issues"], ["max_iterations", "count"], strict=True):
        assert item["check_id"] == "V351"
        assert item["severity"] == "error"
        assert item["metadata"]["span"] == "2-3"
        assert field in item["message"]


@pytest.mark.parametrize(
    ("count", "cap", "goal", "passes", "reason"),
    [
        (3, 5, None, 3, "count_reached"),
        (5, 2, None, 2, "max_iterations"),
        (1, 1, None, 1, "count_reached"),
        (3, 5, 1, 1, "condition_met"),
        (3, 5, 3, 3, "condition_met"),
        (5, 2, 2, 2, "condition_met"),
        (5, 2, 9, 2, "max_iterations"),
    ],
)
async def test_resolved_bounds_enforce_runtime_precedence_after_reload(
    count: int, cap: int, goal: int | None, passes: int, reason: str
) -> None:
    loop: dict[str, object] = {"count": "{{ count }}", "max_iterations": "{{ cap }}"}
    if goal is not None:
        loop["until"] = "loop.pass_no >= var.goal"
    loaded = JobConfig.model_validate(_score({"count": count, "cap": cap, "goal": goal}, loop))
    config = JobConfig.model_validate_json(loaded.model_dump_json())
    state = SheetState(sheet_num=1, instrument_name="cli")
    state.remember_primary_identity()
    checkpoint = CheckpointState(job_id="j", job_name="j", total_sheets=1, sheets={1: state})
    baton = BatonCore()
    baton.register_job(
        "j",
        checkpoint.sheets,
        {},
        loops=config.sheet.loops,
        flow_state=checkpoint.flow,
        flow_variables=config.prompt.variables,
    )
    for epoch in range(passes):
        assert not baton.is_job_complete("j")
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j",
                sheet_num=1,
                instrument_name="cli",
                attempt=1,
                dispatch_epoch=epoch,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )
    run = checkpoint.flow.loops["1"]
    assert run.iteration == passes
    assert run.phase == "completed"
    assert run.completed_reason == reason
    assert baton.is_job_complete("j")
