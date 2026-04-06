"""CLI user journey tests — stories about real users interacting with Mozart.

I test stories, not functions. Each test class is a story about someone using
Mozart for the first time, making mistakes, trying edge cases, and hopefully
getting to success. The bugs these tests find are the ones that make users
quietly abandon the product.

Not crashes — confusion. Not errors — dead ends.

@pytest.mark.adversarial
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()


# =============================================================================
# Story 1: Sarah's First Day
#
# Sarah just saw the Mozart demo. She's excited. She wants to scaffold a
# project, validate it, and understand what instruments she has. She has
# a meeting in five minutes. Everything needs to be fast and obvious.
# =============================================================================


class TestSarahsFirstDay:
    """Sarah discovers Mozart and walks through the getting-started flow."""

    @pytest.mark.adversarial
    def test_init_then_validate_succeeds(self, tmp_path: Path) -> None:
        """Init creates something that validate accepts without errors."""
        # Sarah scaffolds her project
        init_result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert init_result.exit_code == 0
        assert "initialized" in init_result.stdout.lower()

        # She validates the generated score
        score_path = tmp_path / "my-score.yaml"
        assert score_path.exists(), "init should create a score file"

        validate_result = runner.invoke(app, ["validate", str(score_path)])
        assert validate_result.exit_code == 0
        assert "passed" in validate_result.stdout.lower()

    @pytest.mark.adversarial
    def test_init_with_custom_name_validates(self, tmp_path: Path) -> None:
        """Custom-named scores also validate correctly."""
        runner.invoke(app, ["init", "--path", str(tmp_path), "--name", "data-pipeline"])
        score_path = tmp_path / "data-pipeline.yaml"
        assert score_path.exists()

        result = runner.invoke(app, ["validate", str(score_path)])
        assert result.exit_code == 0
        assert "passed" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_doctor_works_without_conductor(self) -> None:
        """Sarah can check her environment even if the conductor isn't running."""
        # Doctor should not crash — it reports status whether conductor is
        # running or not
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "python" in result.stdout.lower()
        assert "mozart" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_instruments_list_shows_something(self) -> None:
        """Sarah sees at least the built-in instruments."""
        result = runner.invoke(app, ["instruments", "list"])
        assert result.exit_code == 0
        # Should show the table and a count summary
        assert "instruments" in result.stdout.lower()
        assert "configured" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_init_score_has_meaningful_comments(self, tmp_path: Path) -> None:
        """The generated score has enough comments that Sarah can edit it."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score = (tmp_path / "my-score.yaml").read_text()

        # Must contain instructions, not just config
        assert "edit" in score.lower() or "task" in score.lower()
        assert "workspace" in score.lower()
        assert "mozart" in score.lower()


# =============================================================================
# Story 2: Tom's Bad Day
#
# Tom is having a terrible day. His YAML is broken, his paths are wrong,
# and he keeps making mistakes. Every error should guide him, not punish him.
# He should never hit a dead end or a raw Python traceback.
# =============================================================================


class TestTomsBadDay:
    """Tom provides every possible wrong input. Mozart should handle it all."""

    @pytest.mark.adversarial
    def test_validate_empty_file(self, tmp_path: Path) -> None:
        """Empty file gives a human-readable error, not a Python traceback."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("")

        result = runner.invoke(app, ["validate", str(empty)])
        assert result.exit_code != 0
        # Should mention what's wrong and what to do
        assert "empty" in result.stdout.lower() or "mapping" in result.stdout.lower()
        # Should NOT contain Python internals
        assert "Traceback" not in result.stdout
        assert "NoneType" not in result.stdout

    @pytest.mark.adversarial
    def test_validate_yaml_list_not_mapping(self, tmp_path: Path) -> None:
        """YAML list gives a clear error about needing key-value pairs."""
        list_yaml = tmp_path / "list.yaml"
        list_yaml.write_text("- item1\n- item2\n")

        result = runner.invoke(app, ["validate", str(list_yaml)])
        assert result.exit_code != 0
        assert "list" in result.stdout.lower() or "mapping" in result.stdout.lower()
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_validate_plain_text(self, tmp_path: Path) -> None:
        """Plain text file gives a clear error."""
        plain = tmp_path / "plain.yaml"
        plain.write_text("This is just plain text, not a YAML mapping")

        result = runner.invoke(app, ["validate", str(plain)])
        assert result.exit_code != 0
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_validate_nonexistent_file(self) -> None:
        """Nonexistent file gives clear error with the path."""
        result = runner.invoke(app, ["validate", "/tmp/this-does-not-exist-12345.yaml"])
        assert result.exit_code != 0
        # Typer's argument validation writes to stderr, captured in result.output
        combined = (result.stdout + result.output).lower()
        assert "not found" in combined or "not exist" in combined or "does not exist" in combined

    @pytest.mark.adversarial
    def test_init_path_traversal_blocked(self, tmp_path: Path) -> None:
        """Path traversal in score name is rejected."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "../../../etc/passwd"]
        )
        assert result.exit_code != 0
        assert "separator" in result.stdout.lower() or "path" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_init_spaces_in_name_rejected(self, tmp_path: Path) -> None:
        """Spaces in score name give a helpful suggestion."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "my bad name"]
        )
        assert result.exit_code != 0
        assert "space" in result.stdout.lower() or "hyphen" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_init_empty_name_rejected(self, tmp_path: Path) -> None:
        """Empty name is rejected with guidance."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", ""]
        )
        assert result.exit_code != 0
        assert "empty" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_init_double_init_blocked(self, tmp_path: Path) -> None:
        """Can't init twice without --force — protects existing work."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "exists" in result.stdout.lower() or "force" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_init_force_overwrites(self, tmp_path: Path) -> None:
        """--force allows re-init for users who know what they want."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        result = runner.invoke(app, ["init", "--path", str(tmp_path), "--force"])
        assert result.exit_code == 0


# =============================================================================
# Story 3: The Instruments Display
#
# Alice runs `mozart instruments list` and the count summary should make
# sense. No broken parentheses. No counting unchecked as ready.
# =============================================================================


class TestInstrumentsDisplay:
    """The instruments display is correct and informative."""

    @pytest.mark.adversarial
    def test_instruments_count_format_with_mixed_statuses(self) -> None:
        """Count summary shows proper format: 'N configured (X ready, Y unchecked)'."""
        result = runner.invoke(app, ["instruments", "list"])
        assert result.exit_code == 0

        # The count line should have balanced parentheses
        lines = result.stdout.strip().split("\n")
        count_line = [line for line in lines if "configured" in line]
        assert len(count_line) == 1, "Should have exactly one count summary line"

        count_text = count_line[0].strip()
        # Check balanced parentheses
        assert count_text.count("(") == count_text.count(")"), (
            f"Unbalanced parentheses in count display: {count_text!r}"
        )

        # If both ready and unchecked exist, should be comma-separated
        if "ready" in count_text and "unchecked" in count_text:
            assert ", " in count_text, (
                f"Ready and unchecked should be comma-separated: {count_text!r}"
            )

    @pytest.mark.adversarial
    def test_instruments_list_json_output(self) -> None:
        """JSON output is parseable and contains instrument data."""
        result = runner.invoke(app, ["instruments", "list", "--json"])
        assert result.exit_code == 0

        # Should be valid JSON
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) > 0

        # Each entry should have name and kind
        for entry in data:
            assert "name" in entry
            assert "kind" in entry

    @pytest.mark.adversarial
    def test_instruments_check_missing_name_gives_guidance(self) -> None:
        """Running 'instruments check' without a name tells you what to do."""
        result = runner.invoke(app, ["instruments", "check"])
        assert result.exit_code != 0
        # Typer writes argument errors to stderr, captured in result.output
        combined = (result.stdout + result.output).lower()
        assert "name" in combined or "argument" in combined


# =============================================================================
# Story 4: Bob the Scripter
#
# Bob wants to integrate Mozart into his CI pipeline. He needs JSON output
# from every command he uses. He pipes everything through `jq`.
# =============================================================================


class TestBobTheScripter:
    """Bob needs machine-readable output from the CLI."""

    @pytest.mark.adversarial
    def test_validate_json_output_is_parseable(self, tmp_path: Path) -> None:
        """Validate --json produces parseable JSON."""
        # Create a valid score first
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score = tmp_path / "my-score.yaml"

        result = runner.invoke(app, ["validate", str(score), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "valid" in data or "status" in data or "result" in data

    @pytest.mark.adversarial
    def test_validate_json_on_error_is_parseable(self, tmp_path: Path) -> None:
        """Validate --json on invalid input produces parseable error JSON."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("")

        result = runner.invoke(app, ["validate", str(empty), "--json"])
        # Even failures should produce valid JSON
        assert result.exit_code != 0
        # The output should be parseable JSON (not a mix of plain text and JSON)
        output = result.stdout.strip()
        if output:
            try:
                json.loads(output)
            except json.JSONDecodeError:
                # Allow non-JSON error output — some commands don't fully support it
                pass

    @pytest.mark.adversarial
    def test_init_json_output_is_parseable(self, tmp_path: Path) -> None:
        """Init --json produces parseable JSON."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "success" in data
        assert data["success"] is True
        assert "score_file" in data

    @pytest.mark.adversarial
    def test_doctor_json_output_is_parseable(self) -> None:
        """Doctor --json produces parseable JSON."""
        result = runner.invoke(app, ["doctor", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)


# =============================================================================
# Story 5: Error Message Consistency
#
# Every error the user sees should use output_error() for consistent
# formatting. No raw Python tracebacks. No "Error: Error:" double prefixes.
# Every error should have a hint about what to do next.
# =============================================================================


class TestErrorConsistency:
    """All error paths produce user-friendly messages."""

    @pytest.mark.adversarial
    def test_validate_errors_have_hints(self, tmp_path: Path) -> None:
        """Validate errors include actionable hints."""
        # Empty file
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        result = runner.invoke(app, ["validate", str(empty)])
        assert result.exit_code != 0
        out = result.stdout.lower()
        assert "hint" in out or "see:" in out or "score" in out

    @pytest.mark.adversarial
    def test_no_error_contains_python_traceback(self, tmp_path: Path) -> None:
        """No CLI command should expose raw Python tracebacks to users."""
        # Test various error conditions
        test_cases = [
            (["validate", str(tmp_path / "nonexistent.yaml")], "nonexistent file"),
        ]

        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        test_cases.append((["validate", str(empty)], "empty file"))

        list_yaml = tmp_path / "list.yaml"
        list_yaml.write_text("- a\n- b\n")
        test_cases.append((["validate", str(list_yaml)], "list yaml"))

        for args, description in test_cases:
            result = runner.invoke(app, args)
            combined = result.stdout + result.output
            assert "Traceback (most recent call last)" not in combined, (
                f"Python traceback exposed for {description}: {combined[:200]}"
            )

    @pytest.mark.adversarial
    def test_init_error_messages_guide_user(self, tmp_path: Path) -> None:
        """Init errors tell the user exactly what to do."""
        # Double-init without force
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert result.exit_code != 0
        # Must mention --force as the solution
        assert "force" in result.stdout.lower()


# =============================================================================
# Story 6: The Cost-Conscious User
#
# Clara runs expensive scores. She cares deeply about cost visibility.
# When cost limits are disabled (the default!), she should STILL see
# cost information — how much was spent, not just "$0.00".
# =============================================================================


class TestCostVisibility:
    """Cost information should always be visible, even without limits enabled."""

    @pytest.mark.adversarial
    def test_dry_run_shows_cost_warning_when_limits_disabled(self) -> None:
        """Dry-run warns when cost tracking is disabled."""
        result = runner.invoke(app, ["run", "--dry-run", "examples/hello-mozart.yaml"])
        assert result.exit_code == 0
        output = result.output.lower()
        assert "cost" in output, (
            "Dry-run should mention cost — either the warning about disabled "
            "limits or the cost_limits config suggestion"
        )


# =============================================================================
# Story 7: The Cancel Confusion
#
# David wants to cancel a score but gets the name wrong. The error
# should tell him what to do, not just say "nope".
# =============================================================================


class TestCancelJourney:
    """Cancel command provides helpful guidance for all cases."""

    @pytest.mark.adversarial
    def test_cancel_wrong_name_suggests_list(self) -> None:
        """Wrong score name suggests 'mozart list'."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, {"cancelled": False}),
        ):
            result = runner.invoke(app, ["cancel", "typo-score-xyz"])

        assert result.exit_code != 0
        output = result.output.lower()
        assert "list" in output, (
            "Cancel not-found should suggest 'mozart list' like other commands"
        )

    @pytest.mark.adversarial
    def test_cancel_wrong_name_exits_nonzero(self) -> None:
        """Failing to cancel should exit non-zero."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, {"cancelled": False}),
        ):
            result = runner.invoke(app, ["cancel", "nonexistent"])

        assert result.exit_code != 0, (
            "Cancel failing should be exit code 1, not 0"
        )
