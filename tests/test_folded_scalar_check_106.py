"""#106: preflight detection of folded YAML scalars on command fields.

A YAML folded block scalar (`>`, `>-`, `>+`) collapses newlines into spaces.
For a multi-line shell command (e.g. `python3 -c` with several statements) this
produces a guaranteed `SyntaxError` at runtime — the `command_succeeds`
validation can never pass regardless of content. The bug is invisible until
runtime because the YAML parses fine; only the RAW yaml reveals the `>` scalar.
V303 warns at preflight and recommends a literal block scalar (`|`).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from marianne.core.config import JobConfig
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.config import FoldedCommandScalarCheck


def _make_config(yaml_str: str, config_path: Path) -> JobConfig:
    config_path.write_text(yaml_str)
    return JobConfig.from_yaml(config_path)


class TestFoldedCommandScalarCheck:
    def test_properties(self) -> None:
        check = FoldedCommandScalarCheck()
        assert check.check_id == "V303"
        assert check.severity == ValidationSeverity.WARNING
        assert "command" in check.description.lower()

    def test_detects_folded_command(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "Work"
            validations:
              - type: command_succeeds
                command: >-
                  python3 -c "
                  import re
                  assert True
                  "
        """)
        config = _make_config(yaml_str, tmp_path / "t.yaml")
        issues = FoldedCommandScalarCheck().check(config, tmp_path / "t.yaml", yaml_str)
        assert len(issues) == 1
        assert issues[0].check_id == "V303"
        assert issues[0].severity == ValidationSeverity.WARNING
        assert issues[0].suggestion is not None and "|" in issues[0].suggestion
        # line points at the `command: >-` line (line 9 in this doc)
        assert issues[0].line == 9

    def test_literal_block_scalar_is_clean(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "Work"
            validations:
              - type: command_succeeds
                command: |
                  python3 -c "
                  import re
                  assert True
                  "
        """)
        config = _make_config(yaml_str, tmp_path / "t.yaml")
        issues = FoldedCommandScalarCheck().check(config, tmp_path / "t.yaml", yaml_str)
        assert issues == []

    def test_plain_command_is_clean(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "Work"
            validations:
              - type: command_succeeds
                command: "test -f {workspace}/out.txt"
        """)
        config = _make_config(yaml_str, tmp_path / "t.yaml")
        issues = FoldedCommandScalarCheck().check(config, tmp_path / "t.yaml", yaml_str)
        assert issues == []

    def test_multiple_folded_commands(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "Work"
            validations:
              - type: command_succeeds
                command: >
                  echo one
              - type: command_succeeds
                command: >-
                  echo two
        """)
        config = _make_config(yaml_str, tmp_path / "t.yaml")
        issues = FoldedCommandScalarCheck().check(config, tmp_path / "t.yaml", yaml_str)
        assert len(issues) == 2
