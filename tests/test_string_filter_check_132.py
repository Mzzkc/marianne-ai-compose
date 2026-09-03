"""#132: preflight detection of `dict[X | string]` on int-normalized dicts.

A per-instance dict with int-like string keys (`{"1": ...}`) has its keys
normalized to ints at render time (`_normalize_dict_keys`). A template that
looks it up with `dict[instance | string]` re-stringifies the int `instance`
to `"1"`, which no longer matches the int key `1` → `UndefinedError`, crashing
every fan-out instance (~80ms) and cascading via fail_fast — with the real
cause only in the conductor log.

Per the 4-model lab (unanimous on CORRELATED detection): V102 (WARNING) resolves
the subscript's base Name to `config.prompt.variables`, and warns ONLY when that
variable is a dict whose keys are ALL integers after normalization (int, or
int-normalizable strings). `| string` is correct for genuinely string-keyed
dicts, so pure-AST detection would false-positive — correlated detection has
zero false positives (a string index never matches an all-int-keyed dict). No
restriction on the filtered Name (the dict's key types are the precondition).
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.jinja import FanOutStringFilterCheck


def _score(template: str, variables_block: str) -> str:
    """Assemble a valid score YAML with `variables_block` nested under prompt.

    ``variables_block`` lines must already be indented for placement under
    ``prompt.variables:`` (i.e. start at 6 spaces).
    """
    return (
        "name: test\n"
        "sheet:\n"
        "  size: 2\n"
        "  total_items: 2\n"
        "prompt:\n"
        f'  template: "{template}"\n'
        "  variables:\n"
        f"{variables_block}"
    )


def _run(yaml_str: str, tmp_path: Path) -> list:
    p = tmp_path / "t.yaml"
    p.write_text(yaml_str)
    cfg = JobConfig.from_yaml(p)
    return FanOutStringFilterCheck().check(cfg, p, yaml_str)


_LENSES_INT_STRING = (
    '    lenses:\n'
    '      "1": {name: foo}\n'
    '      "2": {name: bar}\n'
)


class TestFanOutStringFilterCheck:
    def test_properties(self) -> None:
        c = FanOutStringFilterCheck()
        assert c.check_id == "V102"
        assert c.severity == ValidationSeverity.WARNING
        assert c.description

    def test_buggy_pattern_flagged(self, tmp_path: Path) -> None:
        yaml_str = _score("{{ lenses[instance | string].name }}", _LENSES_INT_STRING)
        issues = _run(yaml_str, tmp_path)
        assert len(issues) == 1
        assert issues[0].check_id == "V102"
        assert issues[0].severity == ValidationSeverity.WARNING
        assert issues[0].suggestion is not None
        assert issues[0].metadata.get("variable") == "lenses"

    def test_int_keyed_yaml_also_flagged(self, tmp_path: Path) -> None:
        # YAML int keys are already int; `| string` still re-stringifies the
        # subscript → misses the int key. Same bug class.
        block = "    lenses:\n      1: {name: foo}\n      2: {name: bar}\n"
        yaml_str = _score("{{ lenses[instance | string] }}", block)
        assert len(_run(yaml_str, tmp_path)) == 1

    def test_chained_filter_flagged(self, tmp_path: Path) -> None:
        yaml_str = _score("{{ lenses[instance | string | trim] }}", _LENSES_INT_STRING)
        assert len(_run(yaml_str, tmp_path)) == 1

    def test_string_keyed_dict_is_clean(self, tmp_path: Path) -> None:
        # `| string` is CORRECT for genuinely string-keyed dicts.
        block = "    roles:\n      alpha: {name: foo}\n      beta: {name: bar}\n"
        yaml_str = _score("{{ roles[instance | string] }}", block)
        assert _run(yaml_str, tmp_path) == []

    def test_mixed_keys_stay_silent(self, tmp_path: Path) -> None:
        # Mixed int-string + genuine-string keys: `| string` legitimately hits
        # the string keys, so flagging would be a false positive. Stay silent.
        block = '    data:\n      "1": {name: foo}\n      alpha: {name: bar}\n'
        yaml_str = _score("{{ data[instance | string] }}", block)
        assert _run(yaml_str, tmp_path) == []

    def test_no_filter_is_clean(self, tmp_path: Path) -> None:
        yaml_str = _score("{{ lenses[instance].name }}", _LENSES_INT_STRING)
        assert _run(yaml_str, tmp_path) == []

    def test_non_dict_variable_is_clean(self, tmp_path: Path) -> None:
        yaml_str = _score("{{ count[instance | string] }}", "    count: 5\n")
        assert _run(yaml_str, tmp_path) == []

    def test_unresolvable_base_is_clean(self, tmp_path: Path) -> None:
        # Base not in prompt.variables → can't prove int-keyed → decline.
        yaml_str = _score("{{ unknown[instance | string] }}", _LENSES_INT_STRING)
        assert _run(yaml_str, tmp_path) == []
