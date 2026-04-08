"""Tests for ``mzt init`` command.

Validates project scaffolding: starter score creation, .marianne/ directory
setup, no-overwrite safety, and output messaging.

TDD: These tests define the contract for the init command.
"""

from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Basic command registration
# ---------------------------------------------------------------------------


class TestInitCommandExists:
    """Verify the init command is registered and callable."""

    def test_init_command_registered(self) -> None:
        """The init command appears in the CLI."""
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0
        assert "project" in result.stdout.lower() or "scaffold" in result.stdout.lower()

    def test_init_help_shows_usage(self) -> None:
        """Help text explains what init does."""
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0
        assert "score" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Starter score creation
# ---------------------------------------------------------------------------


class TestInitCreatesStarterScore:
    """Init creates a well-formed starter score YAML."""

    def test_creates_score_file(self, tmp_path: Path) -> None:
        """A YAML score file is created in the target directory."""
        result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        score_file = tmp_path / "my-score.yaml"
        assert score_file.exists(), f"Expected {score_file} to exist"

    def test_score_has_required_fields(self, tmp_path: Path) -> None:
        """The starter score contains name, workspace, backend, sheet, and prompt."""
        import yaml

        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_file = tmp_path / "my-score.yaml"
        with open(score_file) as f:
            config = yaml.safe_load(f)
        assert "name" in config
        assert "workspace" in config
        assert "sheet" in config
        assert "prompt" in config

    def test_score_has_comments(self, tmp_path: Path) -> None:
        """The starter score includes explanatory comments."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_file = tmp_path / "my-score.yaml"
        content = score_file.read_text()
        # Should have comments explaining the fields
        assert "#" in content

    def test_score_uses_relative_workspace(self, tmp_path: Path) -> None:
        """Workspace path is relative, not absolute."""
        import yaml

        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_file = tmp_path / "my-score.yaml"
        with open(score_file) as f:
            config = yaml.safe_load(f)
        workspace = config["workspace"]
        assert not os.path.isabs(workspace), f"Workspace should be relative, got: {workspace}"

    def test_score_is_valid_yaml(self, tmp_path: Path) -> None:
        """The generated score parses as valid YAML."""
        import yaml

        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_file = tmp_path / "my-score.yaml"
        with open(score_file) as f:
            config = yaml.safe_load(f)
        assert isinstance(config, dict)


# ---------------------------------------------------------------------------
# .marianne/ directory creation
# ---------------------------------------------------------------------------


class TestInitCreatesMarianneDir:
    """Init creates .marianne/ project config directory."""

    def test_creates_marianne_directory(self, tmp_path: Path) -> None:
        """The .marianne/ directory is created."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert (tmp_path / ".marianne").is_dir()


# ---------------------------------------------------------------------------
# Output messaging
# ---------------------------------------------------------------------------


class TestInitOutput:
    """Init provides clear, helpful output."""

    def test_shows_created_files(self, tmp_path: Path) -> None:
        """Output lists the files that were created."""
        result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "my-score.yaml" in result.stdout

    def test_shows_next_steps(self, tmp_path: Path) -> None:
        """Output includes next steps for the user."""
        result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "mzt start" in result.stdout or "next" in result.stdout.lower()

    def test_uses_musical_terminology(self, tmp_path: Path) -> None:
        """Output uses Marianne's musical terminology."""
        result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "score" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Safety: no overwrite
# ---------------------------------------------------------------------------


class TestInitSafety:
    """Init refuses to overwrite existing files."""

    def test_refuses_overwrite_existing_score(self, tmp_path: Path) -> None:
        """Won't overwrite an existing score file without --force."""
        score_file = tmp_path / "my-score.yaml"
        score_file.write_text("existing: content\n")
        result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert result.exit_code != 0
        # Original file should be untouched
        assert score_file.read_text() == "existing: content\n"

    def test_force_overwrites(self, tmp_path: Path) -> None:
        """--force allows overwriting existing files."""
        score_file = tmp_path / "my-score.yaml"
        score_file.write_text("old: content\n")
        result = runner.invoke(app, ["init", "--path", str(tmp_path), "--force"])
        assert result.exit_code == 0
        # File should have new content
        assert score_file.read_text() != "old: content\n"

    def test_refuses_overwrite_existing_marianne_dir(self, tmp_path: Path) -> None:
        """Won't overwrite an existing .marianne/ directory without --force."""
        marianne_dir = tmp_path / ".marianne"
        marianne_dir.mkdir()
        (marianne_dir / "existing.txt").write_text("important\n")
        result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert result.exit_code != 0
        # Existing files preserved
        assert (marianne_dir / "existing.txt").read_text() == "important\n"


# ---------------------------------------------------------------------------
# Custom score name
# ---------------------------------------------------------------------------


class TestInitCustomName:
    """Init supports custom score names."""

    def test_custom_name_creates_correct_file(self, tmp_path: Path) -> None:
        """--name creates a score with the given name."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "my-project"]
        )
        assert result.exit_code == 0
        assert (tmp_path / "my-project.yaml").exists()

    def test_custom_name_in_score_config(self, tmp_path: Path) -> None:
        """The custom name appears in the score's name field."""
        import yaml

        runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "data-pipeline"]
        )
        score_file = tmp_path / "data-pipeline.yaml"
        with open(score_file) as f:
            config = yaml.safe_load(f)
        assert config["name"] == "data-pipeline"


# ---------------------------------------------------------------------------
# Harper M2: Schema validation and correctness
# ---------------------------------------------------------------------------


class TestInitSchemaValidation:
    """The generated score passes Pydantic schema validation."""

    def test_generated_score_passes_pydantic(self, tmp_path: Path) -> None:
        """The starter score passes JobConfig schema validation."""
        from marianne.core.config import JobConfig

        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_file = tmp_path / "my-score.yaml"
        config = JobConfig.from_yaml(score_file)
        assert config.name == "my-score"
        assert config.sheet.total_sheets == 3

    def test_uses_validations_plural(self, tmp_path: Path) -> None:
        """The score uses 'validations:' (plural), not 'validation:' (singular)."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        content = (tmp_path / "my-score.yaml").read_text()
        assert "validations:" in content
        # Ensure there's no bare 'validation:' (excluding the word inside comments)
        lines = [l for l in content.split("\n") if not l.strip().startswith("#")]
        non_comment = "\n".join(lines)
        assert "validations:" in non_comment

    def test_validate_command_accepts_generated_score(self, tmp_path: Path) -> None:
        """Running `mzt validate` on the generated score passes with workspace created."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        score_file = tmp_path / "my-score.yaml"
        # Create workspace parent so V002 doesn't fire
        (tmp_path / "workspaces").mkdir(exist_ok=True)
        result = runner.invoke(app, ["validate", str(score_file)])
        # The starter score should validate without errors
        assert result.exit_code == 0

    def test_safety_warnings_use_output_error(self, tmp_path: Path) -> None:
        """Safety warnings use output_error() format, not raw console.print."""
        (tmp_path / "my-score.yaml").write_text("existing content")
        result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert result.exit_code != 0
        # output_error() with severity=warning shows "Warning:" prefix
        assert "Warning:" in result.output or "already exists" in result.output.lower()


# ---------------------------------------------------------------------------
# F-031: Validate rejects non-dict YAML with clear error
# ---------------------------------------------------------------------------


class TestValidateNonDictYaml:
    """Validate catches non-dict YAML before reaching Pydantic (F-031)."""

    def test_plain_text_gives_clear_error(self, tmp_path: Path) -> None:
        """Plain text YAML produces a 'must be a mapping' error, not Pydantic error."""
        score = tmp_path / "bad.yaml"
        score.write_text("this is just plain text")
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code != 0
        assert "mapping" in result.stdout.lower() or "key-value" in result.stdout.lower()
        # Must NOT show Pydantic's generic error
        assert "Input should be a valid dictionary" not in result.stdout

    def test_list_yaml_gives_clear_error(self, tmp_path: Path) -> None:
        """A YAML list produces a 'must be a mapping' error."""
        score = tmp_path / "list.yaml"
        score.write_text("- item1\n- item2\n- item3\n")
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code != 0
        assert "mapping" in result.stdout.lower() or "list" in result.stdout.lower()

    def test_empty_yaml_gives_clear_error(self, tmp_path: Path) -> None:
        """An empty YAML file produces a clear error about being empty."""
        score = tmp_path / "empty.yaml"
        score.write_text("")
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code != 0
        assert "empty" in result.stdout.lower() or "mapping" in result.stdout.lower()

    def test_numeric_yaml_gives_clear_error(self, tmp_path: Path) -> None:
        """A YAML file containing only a number gives a clear error."""
        score = tmp_path / "number.yaml"
        score.write_text("42")
        result = runner.invoke(app, ["validate", str(score)])
        assert result.exit_code != 0
        assert "mapping" in result.stdout.lower()

    def test_valid_yaml_dict_still_works(self, tmp_path: Path) -> None:
        """A valid YAML dict still reaches the schema validation step."""
        score = tmp_path / "partial.yaml"
        score.write_text("name: test\n")
        result = runner.invoke(app, ["validate", str(score)])
        # Will fail schema validation (missing sheet, prompt) but NOT with
        # the "must be a mapping" error — it should reach Pydantic validation
        assert result.exit_code != 0
        assert "mapping" not in result.stdout.lower()

    def test_json_output_mode(self, tmp_path: Path) -> None:
        """Non-dict YAML in JSON mode produces structured error."""
        score = tmp_path / "text.yaml"
        score.write_text("just text")
        result = runner.invoke(app, ["validate", str(score), "--json"])
        assert result.exit_code != 0
        # JSON output should contain the error
        assert "mapping" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Harper M2: Name validation — TDD (red first)
# ---------------------------------------------------------------------------


class TestInitNameValidation:
    """Init validates the --name parameter for safety and correctness."""

    def test_rejects_name_with_path_separator(self, tmp_path: Path) -> None:
        """Names containing path separators are rejected."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "../escape"]
        )
        assert result.exit_code != 0
        assert "invalid" in result.output.lower() or "name" in result.output.lower()

    def test_rejects_name_with_spaces(self, tmp_path: Path) -> None:
        """Names with spaces are rejected — they create awkward file paths."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "my score"]
        )
        assert result.exit_code != 0

    def test_rejects_empty_name(self, tmp_path: Path) -> None:
        """Empty name is rejected."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", ""]
        )
        assert result.exit_code != 0

    def test_rejects_name_with_null_bytes(self, tmp_path: Path) -> None:
        """Names with null bytes are rejected."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "bad\x00name"]
        )
        assert result.exit_code != 0

    def test_accepts_hyphenated_names(self, tmp_path: Path) -> None:
        """Hyphenated names like 'data-pipeline' are valid."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "data-pipeline"]
        )
        assert result.exit_code == 0
        assert (tmp_path / "data-pipeline.yaml").exists()

    def test_accepts_underscored_names(self, tmp_path: Path) -> None:
        """Underscored names like 'my_score' are valid."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "my_score"]
        )
        assert result.exit_code == 0
        assert (tmp_path / "my_score.yaml").exists()

    def test_rejects_name_starting_with_dot(self, tmp_path: Path) -> None:
        """Names starting with dot are rejected — hidden files are confusing."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", ".hidden"]
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Harper M2: Workspaces directory creation
# ---------------------------------------------------------------------------


class TestInitWorkspacesDir:
    """Init creates workspaces/ so validate works immediately."""

    def test_creates_workspaces_directory(self, tmp_path: Path) -> None:
        """The workspaces/ directory is created during init."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert (tmp_path / "workspaces").is_dir()


# ---------------------------------------------------------------------------
# Lens M2: JSON output, doctor mention, instrument terminology
# ---------------------------------------------------------------------------


class TestInitJsonOutput:
    """Init supports --json for machine-parseable output."""

    def test_json_flag_exists(self, tmp_path: Path) -> None:
        """--json is accepted as a flag."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--json"]
        )
        assert result.exit_code == 0

    def test_json_output_is_valid(self, tmp_path: Path) -> None:
        """--json produces parseable JSON."""
        import json

        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)

    def test_json_output_has_required_fields(self, tmp_path: Path) -> None:
        """JSON output includes success, score_file, and next_steps."""
        import json

        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--json"]
        )
        data = json.loads(result.stdout)
        assert data["success"] is True
        assert "score_file" in data
        assert "name" in data

    def test_json_output_on_error(self, tmp_path: Path) -> None:
        """JSON mode shows structured error when init fails."""
        import json

        (tmp_path / "my-score.yaml").write_text("existing")
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--json"]
        )
        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert data["success"] is False

    def test_json_name_validation_error(self, tmp_path: Path) -> None:
        """JSON mode shows structured error for invalid names."""
        import json

        result = runner.invoke(
            app, ["init", "--path", str(tmp_path), "--name", "../bad", "--json"]
        )
        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert data["success"] is False


class TestInitDoctorMention:
    """Init output mentions mzt doctor for environment validation."""

    def test_next_steps_mentions_doctor(self, tmp_path: Path) -> None:
        """Next steps suggest running mzt doctor."""
        result = runner.invoke(app, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "doctor" in result.stdout.lower()


class TestInitInstrumentTerminology:
    """Starter score mentions instrument terminology."""

    def test_score_comments_mention_instrument(self, tmp_path: Path) -> None:
        """The starter score comments explain the instrument: field."""
        runner.invoke(app, ["init", "--path", str(tmp_path)])
        content = (tmp_path / "my-score.yaml").read_text()
        assert "instrument:" in content.lower()


# ---------------------------------------------------------------------------
# Lens M2: Positional argument support (F-067b)
# ---------------------------------------------------------------------------


class TestInitPositionalArgument:
    """mzt init accepts an optional positional argument (like git init).

    F-067b: `mzt init test-project` should work the same as
    `mzt init --name test-project`. Every major CLI tool supports
    this: git init, npm init, cargo init. The positional arg sets
    the score name.
    """

    def test_positional_sets_name(self, tmp_path: Path) -> None:
        """Positional argument creates a score with the given name."""
        result = runner.invoke(
            app, ["init", "data-pipeline", "--path", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert (tmp_path / "data-pipeline.yaml").exists()

    def test_positional_name_in_config(self, tmp_path: Path) -> None:
        """Positional name appears in the generated score config."""
        import yaml

        runner.invoke(
            app, ["init", "my-project", "--path", str(tmp_path)]
        )
        with open(tmp_path / "my-project.yaml") as f:
            config = yaml.safe_load(f)
        assert config["name"] == "my-project"

    def test_positional_only_no_flags(self, tmp_path: Path) -> None:
        """Works with just the positional and --path."""
        result = runner.invoke(
            app, ["init", "simple", "--path", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert (tmp_path / "simple.yaml").exists()

    def test_default_name_without_positional(self, tmp_path: Path) -> None:
        """Without positional arg, default name 'my-score' is used."""
        result = runner.invoke(
            app, ["init", "--path", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert (tmp_path / "my-score.yaml").exists()

    def test_positional_validates_name(self, tmp_path: Path) -> None:
        """Positional argument goes through the same name validation."""
        result = runner.invoke(
            app, ["init", "../escape", "--path", str(tmp_path)]
        )
        assert result.exit_code != 0

    def test_flag_name_overrides_positional(self, tmp_path: Path) -> None:
        """When both positional and --name are given, --name wins."""
        result = runner.invoke(
            app, ["init", "positional-name", "--name", "flag-name",
                  "--path", str(tmp_path)]
        )
        assert result.exit_code == 0
        # --name flag should take precedence
        assert (tmp_path / "flag-name.yaml").exists()
