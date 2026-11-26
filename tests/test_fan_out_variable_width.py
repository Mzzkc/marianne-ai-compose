"""#569: a ``sheet.fan_out`` width may name a ``prompt.variables`` variable."""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from marianne.core.config import JobConfig
from marianne.core.config.flow import FlowConfigError, FlowIssue
from marianne.core.config.job import (
    MAX_VARIABLE_FAN_OUT_WIDTH,
    resolve_fan_out_width,
)
from marianne.core.sheet import build_sheets

# One entry per shape the resolver must judge.
VARIABLES = textwrap.dedent(
    """\
    chapter_count: "10"
    chapters: 4
    book: {chapter_count: 5}
    padded: " 4 "
    as_float: 4.0
    as_float_text: "4.0"
    as_bool: true
    as_text: four
    as_list: [1, 2]
    as_null: null
    as_empty: ""
    as_negative_text: "-3"
    as_signed_text: "+3"
    as_template: "{{ 2 + 2 }}"
    """
)


def _score(sheet: str, *, variables: str | None = VARIABLES, extra: str = "") -> str:
    prompt = "prompt:\n  template: Write {{ chapter_count }} chapters.\n"
    if variables is not None:
        prompt += "  variables:\n" + textwrap.indent(variables, "    ") + "\n"
    return f"name: book\nworkspace: ./output\n{extra}{prompt}sheet:\n" + textwrap.indent(
        sheet, "  "
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


def test_issue_done_when_string_variable_expands_to_that_many_voices() -> None:
    config = _load(
        _score(
            "size: 1\n"
            "total_items: 3\n"
            "fan_out: {2: '{{ chapter_count }}'}\n"
            "dependencies: {2: [1], 3: [2]}\n"
        )
    )
    assert [(s.movement, s.voice) for s in build_sheets(config)] == [
        (1, None),
        *((2, voice) for voice in range(1, 11)),
        (3, None),
    ]
    assert config.sheet.total_sheets == 12
    assert config.sheet.fan_out == {}  # cleared, as for a literal
    assert config.sheet.fan_out_stage_map[2] == {"stage": 2, "instance": 1, "fan_count": 10}
    assert config.sheet.dependencies[12] == list(range(2, 12))  # fan-in, sheet-level


def test_issue_done_when_out_of_range_is_a_coded_validation_error() -> None:
    body = _score(
        "size: 1\ntotal_items: 3\nfan_out: {2: '{{ chapter_count }}'}\n",
        variables="    chapter_count: 0\n",
    )
    with pytest.raises(ValidationError) as caught:
        _load(body)
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25"]
    assert "1-1000" in issues[0].message and "chapter_count" in issues[0].message


def test_integer_variable_expands() -> None:
    config = _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{ chapters }}'}\n"))
    assert config.sheet.total_sheets == 6
    assert config.sheet.fan_out == {}


def test_reference_without_inner_spaces_is_accepted() -> None:
    config = _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{chapters}}'}\n"))
    assert config.sheet.total_sheets == 6


def test_whitespace_around_reference_is_tolerated() -> None:
    config = _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{   chapter_count  }}'}\n"))
    assert config.sheet.total_sheets == 12


def test_whitespace_padded_number_is_trimmed() -> None:
    config = _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{ padded }}'}\n"))
    assert config.sheet.total_sheets == 6


def test_nested_variable_path_resolves() -> None:
    config = _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{ book.chapter_count }}'}\n"))
    assert config.sheet.total_sheets == 7


def test_variable_reference_is_whitespace_stripped() -> None:
    config = _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: ' {{ chapters }} '}\n"))
    assert config.sheet.total_sheets == 6


def test_explicit_templated_fan_out_wins_over_movement_voices() -> None:
    config = _load(
        _score(
            "size: 1\ntotal_items: 3\nfan_out: {2: '{{ chapters }}'}\n",
            extra="movements: {2: {voices: 9}}\n",
        )
    )
    assert config.sheet.fan_out_stage_map[2]["fan_count"] == 4
    assert config.sheet.total_sheets == 6


def test_mixed_literal_and_variable_widths() -> None:
    config = _load(
        _score("size: 1\ntotal_items: 3\nfan_out: {2: 3, 3: '{{ chapters }}'}\n")
    )
    assert config.sheet.total_sheets == 8
    assert [s.movement for s in build_sheets(config)] == [1, 2, 2, 2, 3, 3, 3, 3]


def test_skip_when_stage_key_remaps_across_resolved_voices() -> None:
    config = _load(
        _score(
            "size: 1\ntotal_items: 3\n"
            "fan_out: {2: '{{ chapters }}'}\n"
            "skip_when: {2: {command: 'true'}}\n"
        )
    )
    assert set(config.sheet.skip_when) == {2, 3, 4, 5}


def test_literal_scores_are_unchanged() -> None:
    config = _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: 3}\n"))
    assert config.sheet.total_sheets == 5
    assert config.sheet.fan_out == {}
    assert set(config.sheet.fan_out_stage_map) == set(range(1, 6))
    assert config.sheet.fan_out_stage_map[2] == {"stage": 2, "instance": 1, "fan_count": 3}


def test_empty_fan_out_is_untouched() -> None:
    config = _load(_score("size: 1\ntotal_items: 3\nfan_out: {}\n"))
    assert config.sheet.total_sheets == 3


def test_null_fan_out_keeps_todays_schema_error() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_score("size: 1\ntotal_items: 3\nfan_out:\n"))
    assert not _flow_issues(caught.value)


def test_sheet_model_instance_passes_through() -> None:
    from marianne.core.config.job import PromptConfig, SheetConfig

    config = JobConfig(
        name="book",
        workspace="./output",
        prompt=PromptConfig(template="hi"),
        sheet=SheetConfig(total_items=3, fan_out={2: 2}),
    )
    assert config.sheet.total_sheets == 4
    assert config.sheet.fan_out == {}


def test_literal_width_above_the_variable_cap_still_loads() -> None:
    config = _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: 2000}\n"))
    assert config.sheet.total_sheets == 2002


def test_width_equal_to_the_cap_loads() -> None:
    config = _load(
        _score(
            "size: 1\ntotal_items: 3\nfan_out: {2: '{{ chapter_count }}'}\n",
            variables="    chapter_count: 1000\n",
        )
    )
    assert config.sheet.total_sheets == 1002


# ---- rejections ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variable", "fragment"),
    [
        ("as_float", "whole number"),
        ("as_float_text", "whole number"),
        ("as_bool", "whole number"),
        ("as_text", "whole number"),
        ("as_list", "whole number"),
        ("as_empty", "whole number"),
        ("as_negative_text", "whole number"),
        ("as_signed_text", "whole number"),
        ("as_template", "whole number"),
    ],
)
def test_non_numeric_variable_is_rejected(variable: str, fragment: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_score(f"size: 1\ntotal_items: 3\nfan_out: {{2: '{{{{ {variable} }}}}'}}\n"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25"]
    assert variable in issues[0].message and fragment in issues[0].message


def test_variable_declared_null_is_not_declared() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(
            _score(
                "size: 1\ntotal_items: 3\nfan_out: {2: '{{ chapter_count }}'}\n",
                variables="    chapter_count: null\n",
            )
        )
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25"]
    assert "not declared" in issues[0].message


@pytest.mark.parametrize("value", ["1001", "99999", "-2"])
def test_out_of_range_variable_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _load(
            _score(
                "size: 1\ntotal_items: 3\nfan_out: {2: '{{ chapter_count }}'}\n",
                variables=f"    chapter_count: {value}\n",
            )
        )
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25"]
    assert "1-1000" in issues[0].message and "chapter_count" in issues[0].message


def test_undeclared_variable_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{ nope }}'}\n"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25"]
    assert "not declared" in issues[0].message and "prompt.variables" in issues[0].message
    assert issues[0].hint


def test_no_variables_section_names_the_fix() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{ n }}'}\n", variables=""))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25"]
    assert "not declared" in issues[0].message
    assert "prompt.variables" in (issues[0].message + (issues[0].hint or ""))


def test_missing_nested_path_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{ book.missing }}'}\n"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25"]
    assert "not declared" in issues[0].message and "book.missing" in issues[0].message


def test_dotted_path_through_a_non_mapping_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{ chapter_count.parts }}'}\n"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25"]
    assert "not declared" in issues[0].message


def test_reference_with_surrounding_text_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '3 {{ chapter_count }} copies'}\n"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25"]
    assert "{{ chapter_count }}" in issues[0].message


@pytest.mark.parametrize(
    "width",
    [
        "{{ chapters + 1 }}",
        "{{ chapters | int }}",
        "{{ chapters }",
    ],
)
def test_every_other_templating_syntax_is_rejected(width: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_score(f"size: 1\ntotal_items: 3\nfan_out: {{2: '{width}'}}\n"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25"]
    assert width in issues[0].message


def test_all_bad_widths_are_reported_in_one_error() -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{ nope }}', 3: '{{ as_float }}'}\n"))
    issues = _flow_issues(caught.value)
    assert [issue.check for issue in issues] == ["V-FLOW-25", "V-FLOW-25"]
    assert sorted(issue.span for issue in issues) == ["2", "3"]


# ---- brace-less strings keep today's behaviour ------------------------------------


@pytest.mark.parametrize("width", ["'3'", "'03'", "' 3 '"])
def test_braceless_numeric_string_keeps_todays_lax_coercion(width: str) -> None:
    config = _load(_score("size: 1\ntotal_items: 2\nfan_out: {2: " + width + "}\n"))
    assert config.sheet.total_sheets == 4


@pytest.mark.parametrize("width", ["'three'", "'3 voices'", "'{ chapters }'", "'{% chapters %}'"])
def test_braceless_non_numeric_string_keeps_todays_schema_error(width: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _load(_score("size: 1\ntotal_items: 2\nfan_out: {2: " + width + "}\n"))
    assert not _flow_issues(caught.value)


# ---- invariants -------------------------------------------------------------------


def test_resolver_is_pure_and_total() -> None:
    variables: dict[str, object] = {"n": 3, "book": {"k": 5}, "f": 4.0}
    assert resolve_fan_out_width("{{ n }}", variables) == 3
    assert resolve_fan_out_width("{{ book.k }}", variables) == 5
    assert resolve_fan_out_width(7, variables) == 7  # a literal passes through
    assert resolve_fan_out_width("{{ f }}", variables).check == "V-FLOW-25"  # type: ignore[union-attr]
    assert resolve_fan_out_width("{{ missing }}", variables).check == "V-FLOW-25"  # type: ignore[union-attr]


def test_snapshot_round_trip_needs_no_resolution() -> None:
    config = _load(_score("size: 1\ntotal_items: 3\nfan_out: {2: '{{ chapters }}'}\n"))
    again = JobConfig.model_validate_json(config.model_dump_json())
    assert again.model_dump_json() == config.model_dump_json()
    assert len(build_sheets(again)) == 6


def test_adapter_sees_pre_expanded_dependencies() -> None:
    from marianne.daemon.baton.adapter import extract_dependencies

    config = _load(
        _score(
            "size: 1\ntotal_items: 3\n"
            "fan_out: {2: '{{ chapters }}'}\n"
            "dependencies: {3: [2]}\n"
        )
    )
    deps = extract_dependencies(config)
    assert deps[6] == [2, 3, 4, 5]  # sheet-level branch, not the lossy [1]


def test_cap_constant_is_documented_value() -> None:
    assert MAX_VARIABLE_FAN_OUT_WIDTH == 1000
