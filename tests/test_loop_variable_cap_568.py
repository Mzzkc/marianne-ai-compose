"""#568: a loop's iteration cap may name a ``prompt.variables`` variable."""

from __future__ import annotations

import json
import textwrap
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config import JobConfig
from marianne.core.config.flow import FlowConfigError, FlowIssue, LoopConfig
from marianne.core.config.job import PromptConfig, merge_runtime_variables

# One entry per shape the resolver must judge.
VARIABLES = textwrap.dedent(
    """\
    n: 3
    as_float: 4.5
    as_bool: true
    as_text: three
    as_empty: ""
    as_negative_text: "-3"
    as_template: "{{ 2 + 2 }}"
    """
)


def _score(loops: str, *, variables: str | None = VARIABLES) -> str:
    prompt = "prompt:\n  template: Write it.\n"
    if variables is not None:
        prompt += "  variables:\n" + textwrap.indent(variables, "    ") + "\n"
    return (
        "name: loop-cap\nworkspace: ./output\n"
        + prompt
        + "sheet:\n  size: 1\n  total_items: 2\n  loops:\n"
        + textwrap.indent(loops, "    ")
    )


def _loop(field: str, value: str, *, variables: str | None = VARIABLES) -> str:
    """One score whose single loop sets ``field`` to ``value``."""
    return _score(
        "1:\n"
        "  index: pass_no\n"
        "  until: 'file(\"done.txt\").exists'\n"
        f"  {field}: {value}\n",
        variables=variables,
    )


def _load(body: str) -> JobConfig:
    return JobConfig.from_yaml_string(body)


def _flow_issues(exc: ValidationError) -> list[FlowIssue]:
    return [
        issue
        for error in exc.errors()
        if isinstance((cause := error.get("ctx", {}).get("error")), FlowConfigError)
        for issue in cause.issues
    ]


# ---- the feature ----------------------------------------------------------------


def test_max_iterations_reference_resolves_from_an_integer_variable() -> None:
    config = _load(_loop("max_iterations", "'{{ n }}'", variables="n: 3\n"))
    cap = config.sheet.loops["1"].max_iterations
    assert cap == 3 and type(cap) is int, cap
    assert config.sheet.loops["1"].index == "pass_no"


def test_max_iterations_reference_resolves_from_a_digit_string_variable() -> None:
    config = _load(_loop("max_iterations", "'{{ n }}'", variables='n: "12"\n'))
    assert config.sheet.loops["1"].max_iterations == 12


def test_whitespace_padded_and_dotted_references_resolve() -> None:
    config = _load(
        _loop("max_iterations", "'{{ book.count }}'", variables="book: {count: ' 4 '}\n")
    )
    assert config.sheet.loops["1"].max_iterations == 4
    padded = _load(_loop("max_iterations", "' {{ n }} '", variables="n: 3\n"))
    assert padded.sheet.loops["1"].max_iterations == 3


@pytest.mark.parametrize("until", ["with-until", "without-until"])
def test_count_reference_resolves(until: str) -> None:
    loops = "1:\n  index: pass_no\n"
    if until == "with-until":
        loops += "  until: 'file(\"done.txt\").exists'\n"
    loops += "  count: '{{ n }}'\n"
    config = _load(_score(loops))
    loop = config.sheet.loops["1"]
    assert loop.count == 3 and type(loop.count) is int, loop
    assert loop.max_iterations == 50


@pytest.mark.parametrize("bound", [10000, "10000"], ids=["int", "digit-string"])
def test_reference_equal_to_the_bound_loads(bound: object) -> None:
    spelling = bound if isinstance(bound, int) else f'"{bound}"'
    config = _load(_loop("max_iterations", "'{{ n }}'", variables=f"n: {spelling}\n"))
    assert config.sheet.loops["1"].max_iterations == 10000
    with pytest.raises(ValidationError) as caught:
        _load(_loop("max_iterations", "'{{ n }}'", variables="n: 10001\n"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-26"], issues
    assert "1-10000" in issues[0].message


async def test_resolved_cap_ends_the_run_at_max_iterations() -> None:
    """The resolved cap is the cap the baton enforces: three passes, not fifty."""
    from marianne.core.checkpoint import CheckpointState, SheetState
    from marianne.core.expressions import FileFacts
    from marianne.daemon.baton.core import BatonCore
    from marianne.daemon.baton.events import LoopFactsReady, SheetAttemptResult

    config = _load(_loop("max_iterations", "'{{ n }}'", variables="n: 3\n"))
    loop = config.sheet.loops["1"]
    assert loop.max_iterations == 3  # resolved, not the default fifty

    state = SheetState(sheet_num=1, instrument_name="cli")
    state.remember_primary_identity()
    checkpoint = CheckpointState(job_id="j", job_name="j", total_sheets=1, sheets={1: state})
    baton = BatonCore()
    baton.register_job("j", checkpoint.sheets, {}, loops={"1": loop}, flow_state=checkpoint.flow)

    def result(epoch: int) -> SheetAttemptResult:
        return SheetAttemptResult(
            job_id="j",
            sheet_num=1,
            instrument_name="cli",
            attempt=1,
            dispatch_epoch=epoch,
            execution_success=True,
            validation_pass_rate=100.0,
        )

    for epoch in range(3):
        await baton.handle_event(result(epoch))
        run = checkpoint.flow.loops["1"]
        assert run.phase == "awaiting_facts", run
        assert run.facts_request_id is not None
        await baton.handle_event(
            LoopFactsReady(
                job_id="j",
                span="1",
                request_id=run.facts_request_id,
                files={"done.txt": FileFacts(False, False, None)},
            )
        )
    run = checkpoint.flow.loops["1"]
    assert run.phase == "completed"
    assert run.completed_reason == "max_iterations"
    assert run.iteration == 3
    assert baton.is_job_complete("j")


# ---- rejections -----------------------------------------------------------------


def test_undeclared_variable_is_a_coded_issue_naming_loop_and_field() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_loop("max_iterations", "'{{ missing }}'"))
    assert all(error["type"] != "int_parsing" for error in caught.value.errors())
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-26"], issues
    assert issues[0].span == "1", issues[0]
    assert "max_iterations" in issues[0].message
    assert "not declared" in issues[0].message


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        (0, "resolves to 0"),
        (-1, "resolves to -1"),
        (10001, "resolves to 10001"),
        ("0", "resolves to 0"),
    ],
    ids=["zero", "negative", "over-cap", "zero-text"],
)
def test_out_of_range_variable_is_rejected(value: object, fragment: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_loop("max_iterations", "'{{ n }}'", variables=f"n: {value!r}\n"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-26"], issues
    assert "1-10000" in issues[0].message, issues[0].message
    assert fragment in issues[0].message


@pytest.mark.parametrize("cap", ["{{ n + 1 }}", "{{ n | int }}", "3 {{ n }}"])
def test_reference_with_arithmetic_or_a_filter_is_rejected(cap: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_loop("max_iterations", f"'{cap}'"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-26"], issues
    fragment = f"{cap!r} is not a loop cap or a '{{{{ variable }}}}' reference"
    assert fragment in issues[0].message, issues[0].message


def test_all_bad_caps_are_reported_in_one_error() -> None:
    body = _score(
        "1:\n"
        "  index: pass_no\n"
        "  max_iterations: '{{ missing }}'\n"
        '"2-3":\n'
        "  index: other\n"
        "  max_iterations: '{{ missing }}'\n"
        "  count: '{{ as_float }}'\n"
    )
    with pytest.raises(ValidationError) as caught:
        _load(body)
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-26"] * 3, issues
    assert [issue.span for issue in issues] == ["1", "2-3", "2-3"]
    assert all("max_iterations" in issue.message for issue in issues[:2])
    assert "count" in issues[2].message


# ---- brace-less strings and literals keep today's behaviour ----------------------


def test_braceless_digit_string_keeps_lax_coercion() -> None:
    config = _load(_loop("max_iterations", "'5'"))
    assert config.sheet.loops["1"].max_iterations == 5
    with pytest.raises(ValidationError) as caught:
        _load(_loop("max_iterations", "'five'"))
    assert not _flow_issues(caught.value)
    assert any(error["type"] == "int_parsing" for error in caught.value.errors())


def test_literal_above_the_bound_keeps_the_pydantic_error() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_loop("max_iterations", "10001"))
    assert not _flow_issues(caught.value)
    assert [error["type"] for error in caught.value.errors()] == ["less_than_equal"]
    large = _load(_score("1:\n  index: pass_no\n  count: 99999999\n"))
    assert large.sheet.loops["1"].count == 99999999


def test_cost_limit_usd_reference_is_out_of_scope() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_loop("cost_limit_usd", "'{{ n }}'"))
    assert not _flow_issues(caught.value)
    assert [error["type"] for error in caught.value.errors()] == ["float_parsing"]


# ---- invariants -----------------------------------------------------------------


def test_runtime_var_cannot_change_a_resolved_cap() -> None:
    from marianne.daemon.baton.flow import FlowPlan

    config = _load(_loop("max_iterations", "'{{ n }}'"))
    merged = merge_runtime_variables(config, {"n": "9"})
    assert merged.prompt.variables["n"] == "9"
    assert merged.sheet.loops["1"].max_iterations == 3
    assert config.sheet.loops["1"].max_iterations == 3
    # ``until``'s ``var.x`` reads prompt.variables at run time, so ``--var``
    # reaches it while the cap stays fixed per run.
    plan = FlowPlan(
        loops={"1": merged.sheet.loops["1"]}, triggers={}, variables=dict(merged.prompt.variables)
    )
    assert plan.variables["n"] == "9"
    assert plan.loops["1"].max_iterations == 3


def test_loop_model_instance_passes_through_the_validator() -> None:
    config = JobConfig.model_validate(
        {
            "name": "x",
            "prompt": {"template": "hi", "variables": {"n": 3}},
            "sheet": {
                "size": 1,
                "total_items": 2,
                "loops": {"1": LoopConfig(count=2, index="pass")},
            },
        }
    )
    assert config.sheet.loops["1"].count == 2


def test_null_loop_value_keeps_todays_schema_error() -> None:
    with pytest.raises(ValidationError) as caught:
        JobConfig.model_validate(
            {
                "name": "x",
                "prompt": {"template": "hi"},
                "sheet": {"size": 1, "total_items": 2, "loops": {1: None}},
            }
        )
    assert [error["type"] for error in caught.value.errors()] == ["model_type"]
    assert not _flow_issues(caught.value)


@pytest.mark.parametrize(
    ("variables", "reason"),
    [
        ({"n": 10001}, "resolves to 10001"),
        ({"n": 4.5}, "whole number"),
        ({"n": "missing"}, "whole number"),
        ({}, "not declared"),
    ],
    ids=["out-of-range", "float", "text", "undeclared"],
)
def test_prompt_model_reports_the_same_reasons_as_a_prompt_dict(
    variables: dict[str, object], reason: str
) -> None:
    sheet: dict[str, object] = {
        "size": 1,
        "total_items": 2,
        "loops": {"1": {"index": "pass", "max_iterations": "{{ n }}"}},
    }

    def reasons(prompt: object) -> list[tuple[str, str | None, str]]:
        try:
            JobConfig.model_validate({"name": "x", "prompt": prompt, "sheet": sheet})
        except ValidationError as exc:
            return [(issue.check, issue.span, issue.message) for issue in _flow_issues(exc)]
        raise AssertionError(f"expected a refusal naming {reason!r} for variables {variables!r}")

    as_dict: dict[str, object] = {"template": "hi", "variables": variables}
    as_model = PromptConfig.model_validate({"template": "hi", "variables": variables})
    from_dict = reasons(as_dict)  # the dict form is refused, so the comparison is not vacuous
    assert from_dict == reasons(as_model)
    assert reason in from_dict[0][2], from_dict


def test_resolving_does_not_mutate_the_callers_input() -> None:
    raw: dict[str, object] = {
        "name": "loop-cap",
        "workspace": "./output",
        "prompt": {"template": "hi", "variables": {"n": 3}},
        "sheet": {
            "size": 1,
            "total_items": 2,
            "loops": {
                "1": {
                    "index": "pass",
                    "until": 'file("done.txt").exists',
                    "max_iterations": "{{ n }}",
                }
            },
        },
    }
    before = deepcopy(raw)
    config = JobConfig.model_validate(raw)
    assert config.sheet.loops["1"].max_iterations == 3
    assert raw == before


# CPython refuses to convert a digit run longer than sys.get_int_max_str_digits()
# (4300 here): int() raises ValueError rather than returning a number. A
# resolver that converts before it bounds therefore raises instead of
# reporting a coded error.
@pytest.mark.parametrize("value", ["9" * 5000, "1" + "0" * 5000], ids=["over-cap", "power-of-ten"])
def test_unconvertible_values_give_a_bounded_coded_message(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_loop("max_iterations", "'{{ n }}'", variables=f"n: '{value}'\n"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-26"], issues
    message = issues[0].message
    assert len(message) <= 160, message
    assert "is out of range" in message
    # Apart from the loop key, the field and the bound, no digit of the value appears.
    reason = message.split(": ", 1)[1]
    assert not any(ch.isdigit() for ch in reason.replace("1-10000", "")), message
    # A valid cap behind more leading zeroes than CPython will convert resolves.
    padded = _load(
        _loop("max_iterations", "'{{ n }}'", variables=f"n: '{'0' * 5000}3'\n")
    )
    assert padded.sheet.loops["1"].max_iterations == 3


# ---- the CLI frame ---------------------------------------------------------------


def _cli(tmp_path: Path, variables: str) -> tuple[int, dict[str, object]]:
    path = tmp_path / "score.yaml"
    path.write_text(_loop("max_iterations", "'{{ n }}'", variables=variables))
    result = CliRunner().invoke(app, ["validate", str(path), "--json"])
    return result.exit_code, json.loads(result.stdout)


def test_cli_reports_a_bad_cap_as_v351_and_exit_one(tmp_path: Path) -> None:
    exit_code, data = _cli(tmp_path, "n: 10001\n")
    assert exit_code == 1, data
    assert data["valid"] is False
    assert [issue["check_id"] for issue in data["issues"]] == ["V351"]
    assert data["summary"]["sheets"] is None  # the score never loaded


def test_cli_reports_a_good_cap_as_a_clean_score(tmp_path: Path) -> None:
    exit_code, data = _cli(tmp_path, 'n: "3"\n')
    assert exit_code == 0, data
    assert data["summary"]["sheets"] == 2
    assert data["summary"]["loops"] == ["1"]


def test_snapshot_round_trip_needs_no_resolution() -> None:
    config = _load(_loop("max_iterations", "'{{ n }}'"))
    again = JobConfig.model_validate_json(config.model_dump_json())
    assert again.model_dump_json() == config.model_dump_json()
    assert again.sheet.loops["1"].max_iterations == 3


# ---- the documented limitation and the fan-out interaction -----------------------


def test_sheet_config_built_directly_still_refuses_a_reference() -> None:
    """Same limitation as fan-out: only ``JobConfig`` resolves, and it says so."""
    from marianne.core.config.job import SheetConfig

    with pytest.raises(ValidationError) as caught:
        SheetConfig.model_validate(
            {
                "size": 1,
                "total_items": 2,
                "loops": {"1": {"index": "pass", "max_iterations": "{{ n }}"}},
            }
        )
    assert [error["type"] for error in caught.value.errors()] == ["int_parsing"]
    assert not _flow_issues(caught.value)


def test_loop_keyed_by_stage_under_variable_fan_out_keeps_its_cap() -> None:
    """A stage-keyed loop under a variable-width fan-out: both resolve."""
    body = (
        "name: stage-loop\nworkspace: ./output\n"
        "prompt:\n  template: hi\n  variables:\n    n: '3'\n    width: '2'\n"
        "sheet:\n  size: 1\n  total_items: 3\n"
        "  fan_out:\n    2: '{{ width }}'\n"
        "  loops:\n"
        "    2: {index: pass, until: 'file(\"done.txt\").exists', max_iterations: '{{ n }}'}\n"
    )
    config = JobConfig.from_yaml_string(body)
    assert config.sheet.total_sheets == 4  # stage 2 widened to two voices
    assert config.sheet.fan_out == {}
    loop = config.sheet.loops["2-3"]  # the stage span re-keyed to concrete sheets
    assert loop.max_iterations == 3
    assert loop.index == "pass"
