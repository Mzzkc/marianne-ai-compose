"""Validate UX journey tests — the paths users actually walk.

These tests follow real users through the validate command, checking
that every error message teaches something useful, that the instrument
terminology is consistent, and that edge cases don't dead-end the user.

@pytest.mark.adversarial
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mozart.cli import app

runner = CliRunner()


# =============================================================================
# Story: Maya Learns the Instrument Terminology
#
# Maya read about instruments in the docs. She tries different
# configurations and expects consistent language from the CLI.
# She should never see "Backend:" — that's legacy jargon.
# =============================================================================


class TestMayaInstrumentTerminology:
    """Every user-facing output uses 'Instrument', not 'Backend'."""

    @pytest.mark.adversarial
    def test_validate_shows_instrument_not_backend_with_instrument(
        self, tmp_path: Path,
    ) -> None:
        """Score with explicit instrument shows 'Instrument:' in summary."""
        score = tmp_path / "with-instrument.yaml"
        score.write_text(
            "name: test\n"
            "instrument: claude-code\n"
            "sheet:\n"
            "  total_items: 1\n"
            "  size: 1\n"
            "prompt:\n"
            '  template: "Hello"\n'
            "workspace: ./ws\n"
            "validations:\n"
            "  - type: file_exists\n"
            "    path: \"{workspace}/out.md\"\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 0
        assert "Instrument: claude-code" in result.stdout
        assert "Backend:" not in result.stdout

    @pytest.mark.adversarial
    def test_validate_shows_instrument_not_backend_without_instrument(
        self, tmp_path: Path,
    ) -> None:
        """Score without explicit instrument still shows 'Instrument:', not 'Backend:'."""
        score = tmp_path / "no-instrument.yaml"
        score.write_text(
            "name: test\n"
            "sheet:\n"
            "  total_items: 1\n"
            "  size: 1\n"
            "prompt:\n"
            '  template: "Hello"\n'
            "workspace: ./ws\n"
            "validations:\n"
            "  - type: file_exists\n"
            "    path: \"{workspace}/out.md\"\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 0
        # Must say "Instrument:" even when falling back to backend type
        assert "Instrument:" in result.stdout
        assert "Backend:" not in result.stdout


# =============================================================================
# Story: Raj Explores Edge Cases
#
# Raj is the security engineer on the team. He pushes buttons that
# shouldn't be pushed. Emojis in names. Unicode in paths. Weird YAML
# constructs. The CLI should handle it all gracefully — no Python
# tracebacks, no cryptic errors.
# =============================================================================


class TestRajEdgeCases:
    """Edge cases that security-minded users try."""

    @pytest.mark.adversarial
    def test_yaml_with_anchors_and_aliases(self, tmp_path: Path) -> None:
        """YAML anchors and aliases are valid YAML — should parse or error cleanly."""
        score = tmp_path / "anchors.yaml"
        score.write_text(
            "name: test\n"
            "defaults: &defaults\n"
            "  total_items: 1\n"
            "  size: 1\n"
            "sheet:\n"
            "  <<: *defaults\n"
            "prompt:\n"
            '  template: "Hello"\n'
            "workspace: ./ws\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        # Should either parse successfully or error cleanly — never traceback
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_very_long_score_name(self, tmp_path: Path) -> None:
        """Score with extremely long name should validate or error cleanly."""
        long_name = "a" * 500
        score = tmp_path / "long-name.yaml"
        score.write_text(
            f"name: {long_name}\n"
            "sheet:\n"
            "  total_items: 1\n"
            "  size: 1\n"
            "prompt:\n"
            '  template: "Hello"\n'
            "workspace: ./ws\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        # Should handle gracefully — no crash
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_zero_sheets_is_handled(self, tmp_path: Path) -> None:
        """Zero sheets should error cleanly, not crash."""
        score = tmp_path / "zero.yaml"
        score.write_text(
            "name: test\n"
            "sheet:\n"
            "  total_sheets: 0\n"
            "  total_items: 0\n"
            "  size: 1\n"
            "prompt:\n"
            '  template: "Hello"\n'
            "workspace: ./ws\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_negative_total_items_is_rejected(self, tmp_path: Path) -> None:
        """Negative total_items should error cleanly."""
        score = tmp_path / "neg.yaml"
        score.write_text(
            "name: test\n"
            "sheet:\n"
            "  total_items: -1\n"
            "  size: 1\n"
            "prompt:\n"
            '  template: "Hello"\n'
            "workspace: ./ws\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        assert "Traceback" not in result.stdout
        # Should be an error — negative items can't be processed
        assert result.exit_code != 0

    @pytest.mark.adversarial
    def test_prompt_as_integer_handled(self, tmp_path: Path) -> None:
        """prompt: 42 should error with helpful hint, not crash."""
        score = tmp_path / "int-prompt.yaml"
        score.write_text(
            "name: test\n"
            "sheet:\n"
            "  total_items: 1\n"
            "  size: 1\n"
            "prompt: 42\n"
            "workspace: ./ws\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 2
        assert "Traceback" not in result.stdout
        # Should mention that prompt needs to be a mapping
        assert "mapping" in result.stdout.lower() or "dictionary" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_prompt_as_list_handled(self, tmp_path: Path) -> None:
        """prompt: [1, 2, 3] should error with helpful hint."""
        score = tmp_path / "list-prompt.yaml"
        score.write_text(
            "name: test\n"
            "sheet:\n"
            "  total_items: 1\n"
            "  size: 1\n"
            "prompt:\n"
            "  - item1\n"
            "  - item2\n"
            "workspace: ./ws\n"
        )
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code == 2
        assert "Traceback" not in result.stdout


# =============================================================================
# Story: The Init-to-Validate Pipeline
#
# A user runs `mozart init`, then immediately validates the generated
# score. This should always work — the init output should validate clean.
# =============================================================================


class TestInitValidatePipeline:
    """The init → validate → run pipeline should work end-to-end."""

    @pytest.mark.adversarial
    def test_init_then_validate_succeeds(self, tmp_path: Path) -> None:
        """Generated score from init should always validate successfully."""
        init_result = runner.invoke(
            app, ["init", "--path", str(tmp_path)]
        )
        assert init_result.exit_code == 0

        score_file = tmp_path / "my-score.yaml"
        assert score_file.exists()

        validate_result = runner.invoke(
            app, ["validate", str(score_file)]
        )
        assert validate_result.exit_code == 0
        assert "✓" in validate_result.stdout

    @pytest.mark.adversarial
    def test_init_with_name_then_validate(self, tmp_path: Path) -> None:
        """Init with custom name produces a validatable score."""
        init_result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "data-pipeline"]
        )
        assert init_result.exit_code == 0

        score_file = tmp_path / "data-pipeline.yaml"
        assert score_file.exists()

        validate_result = runner.invoke(
            app, ["validate", str(score_file)]
        )
        assert validate_result.exit_code == 0
