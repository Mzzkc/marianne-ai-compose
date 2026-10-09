"""Load-time flow shape and position contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from marianne.core.config.flow import FlowConfigError, TriggerAction
from marianne.core.config.job import JobConfig, SheetConfig


def sheet(**extra: object) -> SheetConfig:
    return SheetConfig.model_validate({"size": 1, "total_items": 6, **extra})


def flow_issues(exc: ValidationError) -> tuple[str, ...]:
    found = [error.get("ctx", {}).get("error") for error in exc.errors()]
    return tuple(
        issue.check
        for error in found
        if isinstance(error, FlowConfigError)
        for issue in error.issues
    )


def test_span_canonicalization_and_snapshot() -> None:
    parsed = sheet(loops={"02-04": {"count": 2, "index": "pass_no"}})
    assert list(parsed.loops or {}) == ["2-4"]
    assert SheetConfig.model_validate_json(parsed.model_dump_json()).loops == parsed.loops


@pytest.mark.parametrize("bad", ["5-2", "2 - 5", "2–5", "0", "0x3", True])
def test_invalid_span_refused(bad: object) -> None:
    with pytest.raises(ValidationError):
        sheet(loops={bad: {"count": 2, "index": "pass_no"}})


def test_duplicate_canonical_spans_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        sheet(loops={"3": {"count": 2, "index": "a"}, "3-3": {"count": 2, "index": "b"}})
    assert "same span" in str(caught.value)


def test_collects_independent_load_errors() -> None:
    with pytest.raises(ValidationError) as caught:
        sheet(
            loops={"2-5": {"count": 2, "index": "x"}, "4-6": {"count": 2, "index": "x"}},
            triggers={"1": {"on_success": [{"goto": 99}]}},
        )
    assert {"V-FLOW-11", "V-FLOW-09", "V-FLOW-08"} <= set(flow_issues(caught.value))


def test_fanout_stage_keys_expand_once() -> None:
    parsed = sheet(
        fan_out={2: 3},
        loops={"2-3": {"count": 2, "index": "i"}},
        triggers={2: {"on_fail": [{"goto": 3}, {"skip": "2-3"}]}},
    )
    assert list(parsed.loops or {}) == ["2-5"]
    assert list(parsed.triggers or {}) == ["2-4"]
    actions = parsed.triggers["2-4"].on_fail if parsed.triggers else None
    assert actions is not None and actions[0].goto == 5 and actions[1].skip == "2-5"
    assert SheetConfig.model_validate_json(parsed.model_dump_json()).loops == parsed.loops


def test_trigger_has_exactly_one_action() -> None:
    with pytest.raises(ValidationError):
        TriggerAction.model_validate({"goto": 2, "pause": True})
    with pytest.raises(ValidationError):
        TriggerAction.model_validate({})


def test_reserved_syntax_refused_at_load() -> None:
    with pytest.raises(ValidationError) as caught:
        sheet(loops={"1": {"until": 'validation("x").passed', "index": "i"}})
    assert "reserved" in str(caught.value)


def test_absent_flow_preserves_legacy_dump_and_descriptions() -> None:
    parsed = sheet()
    assert "loops" not in parsed.model_dump()
    assert "triggers" not in parsed.model_dump()
    assert all(field.description for field in SheetConfig.model_fields.values())


def test_validation_loop_index_must_be_scoped_to_its_span(tmp_path: Path) -> None:
    base = {
        "name": "flow-scope", "workspace": tmp_path, "instrument": "cli",
        "sheet": {"size": 1, "total_items": 3,
                  "loops": {"1-2": {"count": 2, "index": "pass_no"}}},
        "prompt": {"template": "echo ok"},
    }
    with pytest.raises(ValidationError, match="outside loop 1-2"):
        JobConfig.model_validate({**base, "validations": [
            {"type": "file_exists", "path": "{workspace}/{pass_no}.txt"}
        ]})
    scoped = JobConfig.model_validate({**base, "validations": [
        {"type": "file_exists", "path": "{workspace}/{pass_no}.txt", "sheet": 2}
    ]})
    assert scoped.validations[0].condition == "sheet_num == 2"


def test_loop_index_cannot_shadow_prompt_variable(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="prompt.variables"):
        JobConfig.model_validate({
            "name": "flow-shadow", "workspace": tmp_path, "instrument": "cli",
            "sheet": {"size": 1, "total_items": 1,
                      "loops": {1: {"count": 2, "index": "pass_no"}}},
            "prompt": {"template": "echo ok", "variables": {"pass_no": 9}},
        })
