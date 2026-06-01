"""#362: preflight detection of bash `${#...}` length syntax in templates.

Jinja2 tokenizes the literal two-char sequence `{#` as a comment opener.
Bash array/string length expansion `${#ARR[@]}` / `${#var}` contains `{#`, so
Jinja silently consumes everything from `{#` to the next `#}` — truncating the
rendered prompt mid-token. The `cli` instrument then runs a broken script and
fails in milliseconds with a bare "Exit code 2" and no captured stderr — 30+
minutes to root-cause in production.

V305 (WARNING) scans `prompt.template` (and `template_file`) for `${#` and warns
before the run, recommending a length-free idiom. There is no legitimate `${#`
in a Jinja template (it always opens a comment), so detection is zero-false-
positive. (Issue option 1 — capturing the cli instrument's parse-time stderr —
is a separate, heavier runtime change, deferred.)
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.jinja import BashArrayLengthCheck


def _run(template: str, tmp_path: Path) -> list:
    yaml_str = (
        "name: test\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n"
        f"  template: {template!r}\n"
    )
    p = tmp_path / "t.yaml"
    p.write_text(yaml_str)
    cfg = JobConfig.from_yaml(p)
    return BashArrayLengthCheck().check(cfg, p, yaml_str)


class TestBashArrayLengthCheck:
    def test_properties(self) -> None:
        c = BashArrayLengthCheck()
        assert c.check_id == "V305"
        assert c.severity == ValidationSeverity.WARNING
        assert c.description

    def test_array_length_flagged(self, tmp_path: Path) -> None:
        issues = _run('echo "${#ARR[@]}"', tmp_path)
        assert len(issues) == 1
        assert issues[0].check_id == "V305"
        assert issues[0].severity == ValidationSeverity.WARNING
        assert issues[0].suggestion is not None

    def test_string_length_flagged(self, tmp_path: Path) -> None:
        issues = _run('n=${#myvar}; echo "$n"', tmp_path)
        assert len(issues) == 1

    def test_clean_template_no_issue(self, tmp_path: Path) -> None:
        # The workaround idiom uses no `${#`.
        assert _run('if [ -z "${ARR[0]:-}" ]; then echo empty; fi', tmp_path) == []

    def test_plain_param_expansion_clean(self, tmp_path: Path) -> None:
        # `${var}` and `${var:-default}` do not contain `{#`.
        assert _run('echo "${HOME}/${USER:-nobody}"', tmp_path) == []

    def test_template_file_scanned(self, tmp_path: Path) -> None:
        tf = tmp_path / "tmpl.j2"
        tf.write_text('set -e\ncount="${#FILES[@]}"\n')
        yaml_str = (
            "name: test\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n"
            f"  template_file: {tf.name}\n"
        )
        p = tmp_path / "t.yaml"
        p.write_text(yaml_str)
        cfg = JobConfig.from_yaml(p)
        issues = BashArrayLengthCheck().check(cfg, p, yaml_str)
        assert len(issues) == 1
        assert issues[0].check_id == "V305"
