"""#362: preflight detection of bash `${#...}` length syntax in templates.

Jinja2 tokenizes the literal two-char sequence `{#` as a comment opener.
Bash array/string length expansion `${#ARR[@]}` / `${#var}` contains `{#`, so
Jinja silently consumes everything from `{#` to the next `#}` — truncating the
rendered prompt mid-token. The `cli` instrument then runs a broken script and
fails in milliseconds with a bare "Exit code 2" and no captured stderr — 30+
minutes to root-cause in production.

V305 scans `prompt.template` (and `template_file`) for `${#` and fails before
the run, recommending a length-free idiom. There is no legitimate `${#` in a
Jinja template (it always opens a comment), so detection is zero-false-positive.
Issue option 1 — capturing the cli instrument's parse-time stderr — is a
separate, heavier runtime change, deferred.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.jinja import BashArrayLengthCheck
from marianne.validation.runner import ValidationRunner


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
        assert c.severity == ValidationSeverity.ERROR
        assert c.description

    def test_array_length_flagged(self, tmp_path: Path) -> None:
        issues = _run('echo "${#ARR[@]}"', tmp_path)
        assert len(issues) == 1
        assert issues[0].check_id == "V305"
        assert issues[0].severity == ValidationSeverity.ERROR
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

    def test_array_length_is_launch_gating_error(self, tmp_path: Path) -> None:
        yaml_str = (
            "name: test\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n"
            "  template: 'echo \"${#ARR[@]}\"'\n"
        )
        p = tmp_path / "t.yaml"
        p.write_text(yaml_str)
        cfg = JobConfig.from_yaml(p)
        runner = ValidationRunner([BashArrayLengthCheck()])
        issues = runner.validate(cfg, p, yaml_str)
        assert runner.get_exit_code(issues) == 1

    def test_repo_corpus_has_no_v305_hits(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        paths: list[Path] = []
        for dirname in ("scores", "scores-internal", "examples"):
            root = repo_root / dirname
            if root.exists():
                paths.extend(sorted(root.rglob("*.yaml")))

        hits: list[str] = []
        parsed = 0
        check = BashArrayLengthCheck()
        for path in paths:
            raw_yaml = path.read_text(encoding="utf-8", errors="replace")
            try:
                cfg = JobConfig.from_yaml(path)
            except Exception:
                continue
            parsed += 1
            issues = check.check(cfg, path, raw_yaml)
            hits.extend(f"{path.relative_to(repo_root)}: {issue.message}" for issue in issues)

        assert parsed > 0
        assert hits == []
