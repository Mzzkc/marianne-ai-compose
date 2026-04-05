"""Schema error hint tests — context-aware guidance for common mistakes.

When a new user writes `prompt: "Hello world"` instead of
`prompt: { template: "Hello world" }`, the error message should tell them
exactly what's wrong and how to fix it — not just "schema validation failed."

These tests verify that the validate command provides context-specific hints
for the most common configuration mistakes new users make.

@pytest.mark.adversarial
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mozart.cli import app
from mozart.cli.commands.validate import _schema_error_hints

runner = CliRunner()


# =============================================================================
# Story: Alex Writes Their First Score
#
# Alex just read the README and wants to try Mozart. They write a minimal
# score file from memory, making the mistakes every newcomer makes.
# The error messages should teach, not just reject.
# =============================================================================


class TestAlexFirstScore:
    """Alex's first score — common newcomer mistakes get helpful hints."""

    @pytest.mark.adversarial
    def test_prompt_as_string_gives_specific_hint(self, tmp_path: Path) -> None:
        """When prompt is a bare string, hint explains the dict format."""
        score = tmp_path / "bad-prompt.yaml"
        score.write_text(
            "name: test\n"
            "sheet:\n"
            "  total_items: 1\n"
            "  size: 1\n"
            'prompt: "Hello world"\n'
            "workspace: ./ws\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 2
        out = result.stdout
        assert "mapping, not a string" in out
        assert "template" in out.lower()

    @pytest.mark.adversarial
    def test_missing_sheet_gives_specific_hint(self, tmp_path: Path) -> None:
        """When sheet section is missing, hint mentions what to add."""
        score = tmp_path / "no-sheet.yaml"
        score.write_text(
            "name: test\n"
            "prompt:\n"
            '  template: "Hello"\n'
            "workspace: ./ws\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 2
        out = result.stdout
        assert "sheet" in out.lower()
        assert "total_items" in out

    @pytest.mark.adversarial
    def test_missing_prompt_gives_specific_hint(self, tmp_path: Path) -> None:
        """When prompt section is missing, hint mentions template."""
        score = tmp_path / "no-prompt.yaml"
        score.write_text(
            "name: test\n"
            "sheet:\n"
            "  total_items: 1\n"
            "  size: 1\n"
            "workspace: ./ws\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 2
        out = result.stdout
        assert "prompt" in out.lower()
        assert "template" in out.lower()

    @pytest.mark.adversarial
    def test_missing_both_gives_both_hints(self, tmp_path: Path) -> None:
        """When both sheet and prompt are missing, both hints appear."""
        score = tmp_path / "minimal.yaml"
        score.write_text("name: test\nworkspace: ./ws\n")
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 2
        out = result.stdout
        assert "sheet" in out.lower()
        assert "prompt" in out.lower()

    @pytest.mark.adversarial
    def test_empty_file_gives_helpful_message(self, tmp_path: Path) -> None:
        """Empty file gets a clear message about what a score needs."""
        score = tmp_path / "empty.yaml"
        score.write_text("")
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 2
        out = result.stdout
        assert "empty" in out.lower() or "mapping" in out.lower()

    @pytest.mark.adversarial
    def test_plain_text_file_gives_helpful_message(self, tmp_path: Path) -> None:
        """Plain text (not YAML) gets a clear message."""
        score = tmp_path / "text.yaml"
        score.write_text("This is just some text, not YAML at all.")
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 2
        out = result.stdout
        assert "mapping" in out.lower() or "key-value" in out.lower()

    @pytest.mark.adversarial
    def test_yaml_list_gives_helpful_message(self, tmp_path: Path) -> None:
        """YAML that's a list instead of a mapping gets a clear message."""
        score = tmp_path / "list.yaml"
        score.write_text("- item1\n- item2\n")
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 2
        out = result.stdout
        assert "mapping" in out.lower() or "key-value" in out.lower()


# =============================================================================
# Unit tests for _schema_error_hints
# =============================================================================


class TestSchemaErrorHints:
    """Direct unit tests for the hint generation function."""

    def test_prompt_config_error_detected(self) -> None:
        """PromptConfig type error triggers specific hint."""
        error = (
            "1 validation error for JobConfig\nprompt\n"
            "  Input should be a valid dictionary or instance of PromptConfig"
        )
        hints = _schema_error_hints(error)
        assert any("mapping, not a string" in h for h in hints)

    def test_missing_sheet_field_detected(self) -> None:
        """Missing 'sheet' field triggers specific hint."""
        error = (
            "1 validation error for JobConfig\nsheet\n"
            "  Field required"
        )
        hints = _schema_error_hints(error)
        assert any("total_items" in h for h in hints)

    def test_missing_prompt_field_detected(self) -> None:
        """Missing 'prompt' field triggers specific hint."""
        error = (
            "1 validation error for JobConfig\nprompt\n"
            "  Field required"
        )
        hints = _schema_error_hints(error)
        assert any("template" in h for h in hints)

    def test_unknown_error_gives_fallback(self) -> None:
        """Unknown error type still gives helpful fallback hint."""
        hints = _schema_error_hints("something completely unexpected")
        assert len(hints) >= 1
        assert any("score-writing-guide" in h for h in hints)

    def test_all_hints_are_strings(self) -> None:
        """Every hint returned is a non-empty string."""
        test_errors = [
            "PromptConfig prompt error",
            "Field required sheet",
            "Field required prompt",
            "unexpected error",
        ]
        for error in test_errors:
            hints = _schema_error_hints(error)
            assert all(isinstance(h, str) and len(h) > 0 for h in hints)
