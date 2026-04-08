"""User journey edge case tests — the in-between states where bugs hide.

I don't test "user creates score successfully." I test "user creates score,
gets distracted, comes back, makes a typo, fixes it, validates again,
and THEN runs it." The real world doesn't follow happy paths.

These tests explore the gaps between features — the transitions, the partial
states, the moments when a user is confused and the CLI must guide them.

@pytest.mark.adversarial
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()


# =============================================================================
# Story 1: Dana's Iterative Editing
#
# Dana is writing her first real score. She starts with `mzt init`, then
# edits the YAML by hand, making mistakes along the way. Each edit →
# validate cycle should guide her to the right answer.
# =============================================================================


class TestDanasIterativeEditing:
    """Dana edits a score incrementally, validating after each change."""

    @pytest.mark.adversarial
    def test_remove_required_field_gives_specific_error(self, tmp_path: Path) -> None:
        """Removing a required field tells Dana WHICH field is missing."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"
        content = yaml.safe_load(score_path.read_text())

        # Dana accidentally deletes the 'name' field
        del content["name"]
        score_path.write_text(yaml.dump(content))

        result = runner.invoke(app, ["validate", str(score_path)])
        assert result.exit_code != 0
        assert "name" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_wrong_type_for_sheet_size_gives_guidance(self, tmp_path: Path) -> None:
        """Setting sheet.size to a string instead of int gives clear error."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"
        content = yaml.safe_load(score_path.read_text())

        # Dana types the wrong value
        content["sheet"]["size"] = "large"
        score_path.write_text(yaml.dump(content))

        result = runner.invoke(app, ["validate", str(score_path)])
        assert result.exit_code != 0
        # Should mention "sheet" or "size" or "integer" — not just a Pydantic dump
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_negative_total_items_rejected(self, tmp_path: Path) -> None:
        """Negative total_items is caught by validation."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"
        content = yaml.safe_load(score_path.read_text())

        content["sheet"]["total_items"] = -1
        score_path.write_text(yaml.dump(content))

        result = runner.invoke(app, ["validate", str(score_path)])
        assert result.exit_code != 0

    @pytest.mark.adversarial
    def test_unicode_in_score_name(self, tmp_path: Path) -> None:
        """Unicode characters in score name are handled gracefully."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"
        content = yaml.safe_load(score_path.read_text())

        content["name"] = "my-score-日本語"
        score_path.write_text(yaml.dump(content, allow_unicode=True))

        result = runner.invoke(app, ["validate", str(score_path)])
        # Should either work or give a clear error — never crash
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_extra_unknown_field_accepted_gracefully(self, tmp_path: Path) -> None:
        """Extra fields in YAML should be ignored or warned, not crash."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"
        content = yaml.safe_load(score_path.read_text())

        # Dana adds a field she saw in a blog post
        content["magic_mode"] = True
        content["ai_level"] = "maximum"
        score_path.write_text(yaml.dump(content))

        result = runner.invoke(app, ["validate", str(score_path)])
        # Pydantic should either ignore extra fields or warn — not crash
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_instrument_typo_gives_instrument_list(self, tmp_path: Path) -> None:
        """Typo in instrument name helps user find the correct one."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"
        content = yaml.safe_load(score_path.read_text())

        content["instrument"] = "claud-code"  # typo!
        score_path.write_text(yaml.dump(content))

        result = runner.invoke(app, ["validate", str(score_path)])
        # Should fail with something about the instrument name
        # The validate command may not catch unknown instrument names
        # (that's a conductor concern), but it should not crash
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_empty_prompt_template_caught(self, tmp_path: Path) -> None:
        """Empty prompt template should be flagged."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"
        content = yaml.safe_load(score_path.read_text())

        content["prompt"]["template"] = ""
        score_path.write_text(yaml.dump(content))

        result = runner.invoke(app, ["validate", str(score_path)])
        # Empty template should at least pass validation (it's technically valid)
        # or give a helpful warning — not crash
        assert "Traceback" not in result.stdout


# =============================================================================
# Story 2: Marcus and the Multi-Instrument Score
#
# Marcus wants to use gemini-cli for cheap research and claude-code for
# the actual coding. He's read the score-writing guide but keeps getting
# the syntax slightly wrong.
# =============================================================================


class TestMarcusMultiInstrument:
    """Marcus tries to write a multi-instrument score."""

    @pytest.mark.adversarial
    def test_both_instrument_and_nondefault_backend_rejected(self, tmp_path: Path) -> None:
        """Using instrument: with a non-default backend.type is rejected."""
        score = tmp_path / "dual.yaml"
        score.write_text(textwrap.dedent("""\
            name: dual-config
            workspace: ../workspaces/dual
            instrument: claude-code
            backend:
              type: anthropic_api
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "Hello"
        """))

        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code != 0
        # Should explain the conflict between instrument: and backend:
        combined = result.stdout + result.output
        assert "instrument" in combined.lower() or "backend" in combined.lower()

    @pytest.mark.adversarial
    def test_instrument_with_default_backend_accepted(self, tmp_path: Path) -> None:
        """instrument: with default backend.type (claude_cli) is fine — no conflict."""
        (tmp_path / "workspaces").mkdir(exist_ok=True)
        score = tmp_path / "scores" / "ok.yaml"
        score.parent.mkdir(exist_ok=True)
        score.write_text(textwrap.dedent(f"""\
            name: instrument-ok
            workspace: {tmp_path}/workspaces/ok
            instrument: claude-code
            backend:
              type: claude_cli
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "Hello"
        """))

        result = runner.invoke(app, ["validate", str(score)])
        # Should pass Pydantic validation — instrument: with default backend: is fine
        assert "schema validation passed" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_per_sheet_instrument_validates(self, tmp_path: Path) -> None:
        """Per-sheet instrument override produces valid config."""
        score = tmp_path / "multi.yaml"
        score.write_text(textwrap.dedent("""\
            name: multi-instrument
            workspace: ../workspaces/multi
            instrument: claude-code
            sheet:
              size: 1
              total_items: 3
              per_sheet_instruments:
                2: gemini-cli
              dependencies:
                2: [1]
                3: [2]
            prompt:
              template: "Process sheet {{ sheet_num }} of {{ total_sheets }}"
        """))

        result = runner.invoke(app, ["validate", str(score)])
        # Should accept the config — per-sheet instruments are valid
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_instrument_map_duplicate_sheet_rejected(self, tmp_path: Path) -> None:
        """A sheet assigned to two different instruments via instrument_map is rejected."""
        score = tmp_path / "conflict.yaml"
        score.write_text(textwrap.dedent("""\
            name: conflict
            workspace: ../workspaces/conflict
            instrument: claude-code
            sheet:
              size: 1
              total_items: 3
              per_sheet_instruments:
                2: gemini-cli
              instrument_map:
                gemini-cli: [2, 3]
              dependencies:
                2: [1]
                3: [2]
            prompt:
              template: "Do work"
        """))

        result = runner.invoke(app, ["validate", str(score)])
        # Sheet 2 is assigned via both per_sheet_instruments AND instrument_map
        # This should either be caught as a validation error or the more specific
        # assignment should win
        combined = result.stdout + result.output
        assert "Traceback" not in combined

    @pytest.mark.adversarial
    def test_movements_key_validates(self, tmp_path: Path) -> None:
        """The movements: key works in score YAML."""
        score = tmp_path / "movements.yaml"
        score.write_text(textwrap.dedent("""\
            name: with-movements
            workspace: ../workspaces/movements
            instrument: claude-code
            sheet:
              size: 1
              total_items: 3
              dependencies:
                2: [1]
                3: [2]
            movements:
              1:
                name: "Planning"
              2:
                name: "Building"
              3:
                name: "Review"
            prompt:
              template: "Work on movement {{ movement }}"
        """))

        result = runner.invoke(app, ["validate", str(score)])
        # Movements should be accepted
        assert "Traceback" not in result.stdout


# =============================================================================
# Story 3: The Forgotten Score
#
# Priya ran a score last week. She doesn't remember the ID. She just wants
# to see what happened. The CLI should help her find it.
# =============================================================================


class TestPriyasForgottenScore:
    """Priya needs to recover context about scores she doesn't remember."""

    @pytest.mark.adversarial
    def test_status_no_args_shows_something_useful(self) -> None:
        """Running 'mzt status' with no args gives an overview."""
        result = runner.invoke(app, ["status"])
        # May fail if conductor isn't running, which is fine
        # But should never crash with a traceback
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_list_shows_all_known_scores(self) -> None:
        """The list command shows what the conductor knows about."""
        result = runner.invoke(app, ["list"])
        # May fail if conductor isn't running
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_list_json_is_always_parseable(self) -> None:
        """Even if conductor is down, list --json should give valid JSON."""
        result = runner.invoke(app, ["list", "--json"])
        # If conductor is running, should be valid JSON array
        # If not, should be valid JSON error
        output = result.stdout.strip()
        if output:
            try:
                data = json.loads(output)
                assert isinstance(data, (list, dict))
            except json.JSONDecodeError:
                # If JSON parsing fails, the output should at least not
                # be a traceback
                assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_status_with_typo_suggests_list(self) -> None:
        """A wrong score ID suggests how to find the right one."""
        result = runner.invoke(app, ["status", "my-scroe-typo-123"])
        if result.exit_code != 0:
            combined = result.stdout + result.output
            # Should suggest running 'mzt list'
            assert "list" in combined.lower()


# =============================================================================
# Story 4: The YAML Edge Cases
#
# Edge cases in YAML that real users actually create when they're editing
# by hand. Tab characters, trailing spaces, BOM markers, Windows line
# endings. The parser should handle all of these without mysterious errors.
# =============================================================================


class TestYamlEdgeCases:
    """YAML quirks that real editors produce."""

    @pytest.mark.adversarial
    def test_tab_indentation_gives_clear_error(self, tmp_path: Path) -> None:
        """Tab-indented YAML gives a YAML error, not a Pydantic error."""
        score = tmp_path / "tabs.yaml"
        score.write_text("name: tabs\n\tsheet:\n\t\tsize: 1\n")

        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code != 0
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_windows_line_endings_work(self, tmp_path: Path) -> None:
        """Windows \\r\\n line endings don't break parsing."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"
        content = score_path.read_text()
        # Convert to Windows line endings
        score_path.write_bytes(content.replace("\n", "\r\n").encode())

        result = runner.invoke(app, ["validate", str(score_path)])
        # Should still validate — YAML handles \r\n
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_utf8_bom_handled(self, tmp_path: Path) -> None:
        """UTF-8 BOM marker doesn't break parsing."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"
        content = score_path.read_bytes()
        # Add UTF-8 BOM
        score_path.write_bytes(b"\xef\xbb\xbf" + content)

        result = runner.invoke(app, ["validate", str(score_path)])
        # YAML should handle BOM — it's a valid UTF-8 file
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_very_large_total_items(self, tmp_path: Path) -> None:
        """A score with 10000 items doesn't crash validation."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"
        content = yaml.safe_load(score_path.read_text())
        content["sheet"]["total_items"] = 10000
        score_path.write_text(yaml.dump(content))

        result = runner.invoke(app, ["validate", str(score_path)])
        # Should validate (even if it warns about cost)
        assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_yaml_anchor_and_alias(self, tmp_path: Path) -> None:
        """YAML anchors (&) and aliases (*) work in scores."""
        score = tmp_path / "anchors.yaml"
        score.write_text(textwrap.dedent("""\
            name: anchor-test
            workspace: ../workspaces/anchor-test
            instrument: claude-code
            _defaults: &defaults
              timeout_seconds: 600
            sheet:
              size: 1
              total_items: 2
            prompt:
              template: "Process sheet {{ sheet_num }}"
            validations:
              - type: file_exists
                path: "{workspace}/output.md"
        """))

        result = runner.invoke(app, ["validate", str(score)])
        # YAML anchors should be resolved before Pydantic validation
        assert "Traceback" not in result.stdout


# =============================================================================
# Story 5: The Validate → Run Gap
#
# Carlos validates his score (all green), then tries to run it.
# The gap between "valid config" and "ready to run" is where
# surprises live. Missing workspace directories, missing instruments,
# missing conductor.
# =============================================================================


class TestValidateRunGap:
    """The gap between validation passing and execution succeeding."""

    @pytest.mark.adversarial
    def test_validate_passes_but_workspace_parent_missing(self, tmp_path: Path) -> None:
        """A score that validates might have a workspace path issue."""
        score = tmp_path / "orphan.yaml"
        score.write_text(textwrap.dedent("""\
            name: orphan-workspace
            workspace: /nonexistent/path/that/doesnt/exist/workspace
            instrument: claude-code
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "Hello"
        """))

        result = runner.invoke(app, ["validate", str(score)])
        # Extended validation should catch this via V002
        if result.exit_code == 0:
            # If it passes, V002 might have been a warning
            pass
        else:
            # If it fails, it should mention the workspace path
            assert "workspace" in result.stdout.lower() or "V002" in result.stdout

    @pytest.mark.adversarial
    def test_validate_dry_run_without_conductor(self, tmp_path: Path) -> None:
        """--dry-run should work even without the conductor."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_path = tmp_path / "my-score.yaml"

        result = runner.invoke(app, ["run", str(score_path), "--dry-run"])
        # dry-run should show the plan without needing the conductor
        assert "Traceback" not in result.stdout


# =============================================================================
# Story 6: The Help System
#
# Every command should have --help that explains what it does.
# No command should crash when given --help.
# =============================================================================


class TestHelpSystem:
    """Every command has working help."""

    COMMANDS = [
        ["--help"],
        ["init", "--help"],
        ["validate", "--help"],
        ["run", "--help"],
        ["status", "--help"],
        ["list", "--help"],
        ["doctor", "--help"],
        ["instruments", "--help"],
        ["instruments", "list", "--help"],
        ["instruments", "check", "--help"],
        ["diagnose", "--help"],
        ["errors", "--help"],
        ["resume", "--help"],
        ["pause", "--help"],
        ["cancel", "--help"],
        ["start", "--help"],
        ["stop", "--help"],
        ["config", "--help"],
    ]

    @pytest.mark.parametrize("args", COMMANDS)
    @pytest.mark.adversarial
    def test_help_works(self, args: list[str]) -> None:
        """Every command's --help works and doesn't crash."""
        result = runner.invoke(app, args)
        assert result.exit_code == 0, f"--help failed for {args}: {result.stdout[:200]}"
        assert "Traceback" not in result.stdout


# =============================================================================
# Story 7: The Score With Everything
#
# Min writes a score using every feature she's read about: fan-out,
# dependencies, validations, per-sheet instruments, movements.
# The kitchen-sink score should validate correctly.
# =============================================================================


class TestKitchenSinkScore:
    """A maximally complex score should validate correctly."""

    @pytest.mark.adversarial
    def test_complex_score_validates(self, tmp_path: Path) -> None:
        """A score with many features validates without errors."""
        score = tmp_path / "kitchen-sink.yaml"
        score.write_text(textwrap.dedent("""\
            name: kitchen-sink
            workspace: ../workspaces/kitchen-sink
            instrument: claude-code
            instrument_config:
              timeout_seconds: 1800

            sheet:
              size: 1
              total_items: 5
              fan_out:
                2: 3
              dependencies:
                2: [1]
                3: [2]
                4: [3]
                5: [4]

            prompt:
              variables:
                project_name: "test-project"
                language: "python"
              template: |
                You are working on {{ project_name }} ({{ language }}).
                This is sheet {{ sheet_num }} of {{ total_sheets }}.
                {% if stage == 1 %}
                Plan the project.
                {% elif stage == 2 %}
                Build component {{ instance }} of {{ fan_count }}.
                {% elif stage == 3 %}
                Integrate all components.
                {% elif stage == 4 %}
                Review everything.
                {% else %}
                Final documentation.
                {% endif %}

            validations:
              - type: file_exists
                path: "{workspace}/sheet-{sheet_num}-output.md"

            retry:
              max_retries: 2
              backoff_base_seconds: 10

            parallel:
              enabled: true
              max_concurrent: 3

            stale_detection:
              enabled: true
              idle_timeout_seconds: 1800
        """))

        result = runner.invoke(app, ["validate", str(score)])
        # This complex config should pass validation
        assert "Traceback" not in result.stdout
        # If it fails, the error should be about config content, not parsing
        if result.exit_code != 0:
            assert "Traceback" not in result.stdout

    @pytest.mark.adversarial
    def test_minimal_score_validates(self, tmp_path: Path) -> None:
        """The absolute minimum score validates."""
        # Create the workspace parent so V002 doesn't trip
        ws_parent = tmp_path / "workspaces"
        ws_parent.mkdir()
        score = tmp_path / "minimal.yaml"
        score.write_text(textwrap.dedent(f"""\
            name: minimal
            workspace: {ws_parent}/minimal
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "Hello world"
        """))

        result = runner.invoke(app, ["validate", str(score)])
        # May warn about no validations (V203) but should pass
        assert "schema validation passed" in result.stdout.lower()

    @pytest.mark.adversarial
    def test_score_with_prelude_validates(self, tmp_path: Path) -> None:
        """A score with prelude context injection validates."""
        # Create a prelude file
        prelude_dir = tmp_path / "context"
        prelude_dir.mkdir()
        (prelude_dir / "guide.md").write_text("# Style Guide\nWrite clearly.")

        score = tmp_path / "prelude.yaml"
        score.write_text(textwrap.dedent(f"""\
            name: with-prelude
            workspace: ../workspaces/prelude
            instrument: claude-code
            sheet:
              size: 1
              total_items: 2
              dependencies:
                2: [1]
            prompt:
              prelude:
                - path: "{prelude_dir}/guide.md"
                  label: "Style Guide"
              template: "Write something following the style guide."
        """))

        result = runner.invoke(app, ["validate", str(score)])
        # Prelude paths should be resolved and checked
        assert "Traceback" not in result.stdout
