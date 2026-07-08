"""Static checks for raw CLI bash prompt contracts."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

from marianne.core.config import JobConfig
from marianne.instruments.loader import InstrumentProfileLoader
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.cli import (
    CliRawPromptBashCheck,
    FanOutAssignmentCoverageCheck,
    PromptValidationContractCheck,
)
from marianne.validation.runner import create_default_checks

BUILTINS = Path(__file__).resolve().parent.parent / "src" / "marianne" / "instruments" / "builtins"


def _config(tmp_path: Path, yaml_text: str) -> tuple[JobConfig, Path, str]:
    path = tmp_path / "score.yaml"
    path.write_text(dedent(yaml_text).strip())
    return JobConfig.from_yaml(path), path, path.read_text()


def _profiles() -> dict:
    return InstrumentProfileLoader.load_directory(BUILTINS)


class TestCliRawPromptBashCheck:
    def test_properties(self) -> None:
        check = CliRawPromptBashCheck()
        assert check.check_id == "V307"
        assert check.severity == ValidationSeverity.ERROR
        assert check.description

    def test_registered_by_default(self) -> None:
        check_ids = {check.check_id for check in create_default_checks()}
        assert "V307" in check_ids

    def test_valid_bash_passes(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: valid-cli
            instrument: cli
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: |
                set -euo pipefail
                mkdir -p "{{ workspace }}"
                printf '%s\\n' "sheet={{ sheet_num }}" > "{{ workspace }}/out.txt"
            """,
        )
        with patch("marianne.instruments.loader.load_all_profiles", return_value=_profiles()):
            issues = CliRawPromptBashCheck().check(config, path, raw)
        assert issues == []

    def test_valid_heredoc_with_markdown_passes(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: heredoc-cli
            instrument: cli
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: |
                cat > "{{ workspace }}/report.md" <<'EOF'
                ## Report
                - Valid markdown inside generated file
                EOF
            """,
        )
        with patch("marianne.instruments.loader.load_all_profiles", return_value=_profiles()):
            issues = CliRawPromptBashCheck().check(config, path, raw)
        assert issues == []

    def test_markdown_heading_fails_for_raw_cli(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: markdown-cli
            instrument: cli
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: |
                ## Build the artifact
                Write a report to {{ workspace }}/report.md.
            """,
        )
        with patch("marianne.instruments.loader.load_all_profiles", return_value=_profiles()):
            issues = CliRawPromptBashCheck().check(config, path, raw)
        assert len(issues) == 1
        assert issues[0].check_id == "V307"
        assert "markdown heading" in issues[0].message

    def test_invalid_bash_fails(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: bad-cli
            instrument: cli
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: |
                if [ -n "{{ workspace }}" ]; then
                  echo ok
            """,
        )
        with patch("marianne.instruments.loader.load_all_profiles", return_value=_profiles()):
            issues = CliRawPromptBashCheck().check(config, path, raw)
        assert len(issues) == 1
        assert "does not parse" in issues[0].message

    def test_alias_to_cli_is_checked(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: alias-cli
            instrument: shell
            instruments:
              shell:
                profile: cli
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: |
                ```bash
                echo bad
                ```
            """,
        )
        with patch("marianne.instruments.loader.load_all_profiles", return_value=_profiles()):
            issues = CliRawPromptBashCheck().check(config, path, raw)
        assert len(issues) == 1
        assert "code fence" in issues[0].message

    def test_non_raw_instrument_skipped(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: agent-score
            instrument: claude-code
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: |
                ## This is agent markdown, not shell
                Write a report.
            """,
        )
        with patch("marianne.instruments.loader.load_all_profiles", return_value=_profiles()):
            issues = CliRawPromptBashCheck().check(config, path, raw)
        assert issues == []

    def test_raw_shell_to_non_raw_fallback_fails(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: cli-fallback
            instrument: cli
            instrument_fallbacks: [claude-code]
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "echo ok"
            """,
        )
        with patch("marianne.instruments.loader.load_all_profiles", return_value=_profiles()):
            issues = CliRawPromptBashCheck().check(config, path, raw)
        assert len(issues) == 1
        assert "fall back to non-raw instruments 'claude-code'" in issues[0].message
        assert issues[0].metadata["fallbacks"] == "claude-code"

    def test_raw_shell_fallback_warning_groups_non_raw_fallbacks(
        self, tmp_path: Path
    ) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: cli-fallback
            instrument: cli
            instrument_fallbacks: [claude-code, opencode]
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "echo ok"
            """,
        )
        with patch("marianne.instruments.loader.load_all_profiles", return_value=_profiles()):
            issues = CliRawPromptBashCheck().check(config, path, raw)

        assert len(issues) == 1
        assert "'claude-code', 'opencode'" in issues[0].message
        assert issues[0].metadata["fallbacks"] == "claude-code,opencode"
        assert issues[0].severity == ValidationSeverity.WARNING

    def test_template_file_is_rendered_and_checked(self, tmp_path: Path) -> None:
        template = tmp_path / "prompt.sh.j2"
        template.write_text("echo {{ sheet_num }}\n")
        config, path, raw = _config(
            tmp_path,
            f"""
            name: template-file-cli
            instrument: cli
            sheet:
              size: 1
              total_items: 1
            prompt:
              template_file: {template.name}
            """,
        )
        with patch("marianne.instruments.loader.load_all_profiles", return_value=_profiles()):
            issues = CliRawPromptBashCheck().check(config, path, raw)
        assert issues == []


class TestFanOutAssignmentCoverageCheck:
    def test_registered_by_default(self) -> None:
        check_ids = {check.check_id for check in create_default_checks()}
        assert "V308" in check_ids

    def test_partial_fanout_assignment_warns(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: partial-fanout
            instrument: claude-code
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 3
              per_sheet_instruments:
                2: gemini-cli
            prompt:
              template: "Do work"
            """,
        )
        issues = FanOutAssignmentCoverageCheck().check(config, path, raw)
        assert len(issues) == 1
        assert issues[0].check_id == "V308"
        assert issues[0].severity == ValidationSeverity.WARNING
        assert "partial concrete instrument coverage" in issues[0].message

    def test_complete_fanout_assignment_passes(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: full-fanout
            instrument: claude-code
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 3
              instrument_map:
                gemini-cli: [2, 3, 4]
            prompt:
              template: "Do work"
            """,
        )
        assert FanOutAssignmentCoverageCheck().check(config, path, raw) == []


class TestPromptValidationContractCheck:
    def test_registered_by_default(self) -> None:
        check_ids = {check.check_id for check in create_default_checks()}
        assert "V309" in check_ids

    def test_missing_exact_section_label_warns(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: contract-gap
            instrument: claude-code
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: |
                Write the final report.
            validations:
              - type: content_contains
                path: "{workspace}/report.md"
                pattern: "VERDICT:"
            """,
        )
        issues = PromptValidationContractCheck().check(config, path, raw)
        assert len(issues) == 1
        assert issues[0].check_id == "V309"
        assert issues[0].severity == ValidationSeverity.WARNING

    def test_present_exact_section_label_passes(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: contract-ok
            instrument: claude-code
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: |
                Write report.md with sections:
                VERDICT:
            validations:
              - type: content_contains
                path: "{workspace}/report.md"
                pattern: "VERDICT:"
            """,
        )
        assert PromptValidationContractCheck().check(config, path, raw) == []

    def test_non_section_pattern_is_ignored(self, tmp_path: Path) -> None:
        config, path, raw = _config(
            tmp_path,
            """
            name: contract-ignore
            instrument: claude-code
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "Write a report"
            validations:
              - type: content_contains
                path: "{workspace}/report.md"
                pattern: "complete"
            """,
        )
        assert PromptValidationContractCheck().check(config, path, raw) == []
