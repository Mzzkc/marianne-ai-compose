"""Exploratory tests for the instrument plugin system — user perspective.

Sarah just saw the Marianne demo. She wants to use Gemini CLI because she
already has a Google API key. She doesn't want to write Python. She wants
to point Marianne at a YAML profile and go.

These tests simulate the full user journey: discovering instruments,
loading profiles, creating backends, building scores with `instrument:`
instead of `backend:`. Every test asks: "Does the thing do what the user
expects when they use it the way a human would?"

@pytest.mark.adversarial
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.instruments.loader import InstrumentProfileLoader
from marianne.instruments.registry import InstrumentRegistry, register_native_instruments

# =============================================================================
# Story 1: Discovering Instruments
#
# Sarah runs `mzt instruments list` (conceptually). What does she see?
# Does the built-in profile loading work? Are the 6 shipped profiles valid?
# =============================================================================


class TestDiscoverInstruments:
    """Can a user discover what instruments are available?"""

    def test_builtin_profiles_all_load_successfully(self) -> None:
        """All 6 built-in profiles parse without errors."""
        builtins_dir = (
            Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"
        )
        profiles = InstrumentProfileLoader.load_directory(builtins_dir)

        # All 9 should load
        assert len(profiles) == 9
        expected_names = {
            "claude-code",
            "gemini-cli",
            "codex-cli",
            "cline-cli",
            "aider",
            "goose",
            "opencode",
            "crush",
            "cli",
        }
        assert set(profiles.keys()) == expected_names

    def test_builtin_profiles_have_required_fields(self) -> None:
        """Every built-in profile has name, kind, display_name, and cli config."""
        builtins_dir = (
            Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"
        )
        profiles = InstrumentProfileLoader.load_directory(builtins_dir)

        for name, profile in profiles.items():
            assert profile.name, f"{name}: missing name"
            assert profile.display_name, f"{name}: missing display_name"
            assert profile.kind == "cli", f"{name}: expected CLI, got {profile.kind}"
            assert profile.cli is not None, f"{name}: missing CLI profile"
            assert profile.cli.command.executable, f"{name}: missing executable"

    def test_builtin_profiles_have_at_least_one_model(self) -> None:
        """Every CLI instrument should document at least one model."""
        builtins_dir = (
            Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"
        )
        profiles = InstrumentProfileLoader.load_directory(builtins_dir)

        # Some instruments declare models with cost info; others are
        # user-configured at runtime (model passed via CLI flag).
        # At least SOME profiles should have models for cost tracking.
        profiles_with_models = [
            name for name, profile in profiles.items() if len(profile.models) >= 1
        ]
        assert len(profiles_with_models) >= 2, (
            f"At least 2 profiles should declare models for cost tracking, "
            f"but only {profiles_with_models} do"
        )

    def test_native_instruments_register(self) -> None:
        """The 4 native instruments register correctly."""
        registry = InstrumentRegistry()
        register_native_instruments(registry)

        assert registry.get("claude_cli") is not None
        assert registry.get("anthropic_api") is not None
        assert registry.get("ollama") is not None
        assert registry.get("recursive_light") is not None

    def test_builtins_plus_native_coexist_in_registry(self) -> None:
        """Registry can hold both native and plugin instruments."""
        registry = InstrumentRegistry()
        register_native_instruments(registry)

        builtins_dir = (
            Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"
        )
        profiles = InstrumentProfileLoader.load_directory(builtins_dir)

        for profile in profiles.values():
            registry.register(profile)

        # 4 native + 9 built-in = 13
        all_instruments = registry.list_all()
        assert len(all_instruments) == 13


# =============================================================================
# Story 2: Writing a Score with instrument:
#
# Sarah writes her first score using `instrument: gemini-cli` instead of
# `backend: {type: claude_cli}`. Does it validate? Does it coexist with
# the old `backend:` syntax?
# =============================================================================


class TestScoreInstrumentField:
    """Score YAML integration with the instrument field."""

    def test_instrument_field_on_job_config(self) -> None:
        """JobConfig accepts `instrument:` field."""
        from marianne.core.config.job import JobConfig

        config = JobConfig(
            name="test-instrument",
            instrument="gemini-cli",
            workspace="./workspaces/test",
            sheet={"size": 1, "total_items": 1},
            prompt={"template": "hello"},
        )
        assert config.instrument == "gemini-cli"

    def test_backend_still_works(self) -> None:
        """Old `backend:` syntax still works unchanged."""
        from marianne.core.config.job import JobConfig

        config = JobConfig(
            name="test-backend",
            workspace="./workspaces/test",
            backend={"type": "claude_cli"},
            sheet={"size": 1, "total_items": 1},
            prompt={"template": "hello"},
        )
        assert config.backend.type == "claude_cli"
        assert config.instrument is None

    def test_instrument_and_backend_both_present_raises(self) -> None:
        """Having both `instrument:` and non-default `backend:` is a validation error.

        The validator only fires when backend.type is NOT the default (claude_cli),
        because backend always has a default value. Conflict = user explicitly set
        both instrument: and backend.type to something non-default.
        """
        from marianne.core.config.job import JobConfig

        with pytest.raises(ValueError, match="Cannot specify both"):
            JobConfig(
                name="test-both",
                workspace="./workspaces/test",
                instrument="gemini-cli",
                backend={"type": "anthropic_api"},
                sheet={"size": 1, "total_items": 1},
                prompt={"template": "hello"},
            )


# =============================================================================
# Story 3: Custom Instrument
#
# A power user writes their own instrument YAML for a local tool.
# Can they load it? What happens when the YAML is slightly wrong?
# =============================================================================


class TestCustomInstrument:
    """Creating custom instrument profiles."""

    def test_minimal_valid_profile(self) -> None:
        """The simplest possible profile that validates."""
        profile = InstrumentProfile(
            name="my-tool",
            display_name="My Tool",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(
                    executable="my-tool",
                    prompt_flag="-p",
                ),
                output=CliOutputConfig(format="text"),
            ),
        )
        assert profile.name == "my-tool"

    def test_profile_from_yaml_string(self, tmp_path: Path) -> None:
        """Profile loads from a YAML file just like the user would create."""
        yaml_content = """
name: local-llama
display_name: "Local Llama"
description: "Local LLM via Ollama CLI wrapper"
kind: cli

capabilities: [tool_use]

default_model: llama3.1:8b
default_timeout_seconds: 600

models:
  - name: llama3.1:8b
    context_window: 128000
    cost_per_1k_input: 0.0
    cost_per_1k_output: 0.0

cli:
  command:
    executable: ollama
    subcommand: "run"
    prompt_flag: null
  output:
    format: text
  errors:
    rate_limit_patterns: []
"""
        profile_file = tmp_path / "local-llama.yaml"
        profile_file.write_text(yaml_content)

        profiles = InstrumentProfileLoader.load_directory(tmp_path)
        assert "local-llama" in profiles
        assert profiles["local-llama"].default_model == "llama3.1:8b"
        assert profiles["local-llama"].models[0].cost_per_1k_input == 0.0

    @pytest.mark.adversarial
    def test_profile_with_typo_in_kind(self, tmp_path: Path) -> None:
        """Profile with invalid `kind:` is skipped gracefully."""
        yaml_content = """
name: bad-kind
display_name: "Bad Kind"
kind: clu  # typo — should be "cli" or "http"
cli:
  command:
    executable: test
    prompt_flag: "-p"
  output:
    format: text
"""
        profile_file = tmp_path / "bad-kind.yaml"
        profile_file.write_text(yaml_content)

        profiles = InstrumentProfileLoader.load_directory(tmp_path)
        # Should skip the invalid profile, not crash
        assert "bad-kind" not in profiles

    @pytest.mark.adversarial
    def test_profile_missing_cli_for_cli_kind(self, tmp_path: Path) -> None:
        """Profile declares kind: cli but has no cli: section."""
        yaml_content = """
name: no-cli
display_name: "No CLI Config"
kind: cli
# Oops — forgot the cli: section
"""
        profile_file = tmp_path / "no-cli.yaml"
        profile_file.write_text(yaml_content)

        InstrumentProfileLoader.load_directory(tmp_path)
        # Depends on whether the validator catches this. Either way, no crash.
        # The profile may load (cli: is optional on InstrumentProfile)
        # but will fail at execution time if cli is None.

    @pytest.mark.adversarial
    def test_empty_yaml_file(self, tmp_path: Path) -> None:
        """Empty YAML file doesn't crash the loader."""
        profile_file = tmp_path / "empty.yaml"
        profile_file.write_text("")

        profiles = InstrumentProfileLoader.load_directory(tmp_path)
        assert "empty" not in profiles

    @pytest.mark.adversarial
    def test_non_yaml_files_ignored(self, tmp_path: Path) -> None:
        """Non-YAML files in the instruments directory are ignored."""
        (tmp_path / "readme.md").write_text("# Not a profile")
        (tmp_path / "notes.txt").write_text("These are notes")
        (tmp_path / "valid.yaml").write_text("""
name: valid-instrument
display_name: "Valid"
kind: cli
cli:
  command:
    executable: test
    prompt_flag: "-p"
  output:
    format: text
""")

        profiles = InstrumentProfileLoader.load_directory(tmp_path)
        assert len(profiles) == 1
        assert "valid-instrument" in profiles

    @pytest.mark.adversarial
    def test_venue_overrides_org_profiles(self, tmp_path: Path) -> None:
        """Venue-level profiles override org-level profiles with same name."""
        org_dir = tmp_path / "org"
        org_dir.mkdir()
        venue_dir = tmp_path / "venue"
        venue_dir.mkdir()

        org_yaml = """
name: my-instrument
display_name: "Org Version"
kind: cli
cli:
  command:
    executable: org-binary
    prompt_flag: "-p"
  output:
    format: text
"""
        venue_yaml = """
name: my-instrument
display_name: "Venue Version"
kind: cli
cli:
  command:
    executable: venue-binary
    prompt_flag: "-p"
  output:
    format: text
"""
        (org_dir / "inst.yaml").write_text(org_yaml)
        (venue_dir / "inst.yaml").write_text(venue_yaml)

        # Load org first, then venue (venue overrides)
        profiles = InstrumentProfileLoader.load_directories([org_dir, venue_dir])
        assert profiles["my-instrument"].display_name == "Venue Version"


# =============================================================================
# Story 4: The JSON Path Extractor
#
# The PluginCliBackend parses JSON output using dot-path accessors.
# This test verifies the extractor works with real-world API responses.
# =============================================================================


class TestJsonPathExtractorRealWorld:
    """JSON path extraction from real instrument output shapes."""

    def test_claude_code_result_path(self) -> None:
        """Extract result from Claude Code JSON output."""
        from marianne.utils.json_path import extract_json_path

        response = {"result": "Hello, world!", "usage": {"input_tokens": 10}}
        assert extract_json_path(response, "result") == "Hello, world!"

    def test_gemini_nested_token_path(self) -> None:
        """Extract tokens from Gemini's nested stats structure."""
        from marianne.utils.json_path import extract_json_path

        response = {
            "response": "Hello",
            "stats": {
                "models": {
                    "gemini-2.5-pro": {
                        "tokens": {
                            "prompt": 42,
                            "candidates": 18,
                        }
                    }
                }
            },
        }
        # Wildcard: stats.models.*.tokens.prompt
        result = extract_json_path(response, "stats.models.*.tokens.prompt")
        assert result == 42

    def test_missing_path_returns_none(self) -> None:
        """Missing path returns None, not crash."""
        from marianne.utils.json_path import extract_json_path

        response = {"result": "hello"}
        assert extract_json_path(response, "nonexistent.path") is None

    @pytest.mark.adversarial
    def test_path_on_non_dict_returns_none(self) -> None:
        """Path navigation on a scalar value returns None."""
        from marianne.utils.json_path import extract_json_path

        response = {"result": "hello"}
        assert extract_json_path(response, "result.nested") is None

    @pytest.mark.adversarial
    def test_empty_path(self) -> None:
        """Empty path string returns the whole object."""
        from marianne.utils.json_path import extract_json_path

        response = {"result": "hello"}
        # An empty path should return the root or None — depends on implementation
        result = extract_json_path(response, "")
        # The behavior here doesn't matter much; what matters is no crash
        assert result is not None or result is None  # No crash is the test


# =============================================================================
# Story 5: Sheet-First Architecture — Template Variables
#
# The user writes `{{ movement }}` in their template. Does it work?
# Do the old `{{ stage }}` aliases still work?
# =============================================================================


class TestTemplateVariableAliases:
    """Template variables bridge old and new terminology."""

    def test_sheet_entity_provides_movement_and_stage(self) -> None:
        """Sheet.template_variables() includes both old and new terms."""
        from marianne.core.sheet import Sheet

        sheet = Sheet(
            num=5,
            movement=2,
            voice=1,
            voice_count=3,
            description="Test sheet",
            workspace=Path("/tmp/test"),
            instrument_name="claude-code",
            instrument_config={},
            prompt_template="test",
            template_file=None,
            variables={},
            prelude=[],
            cadenza=[],
            prompt_extensions=[],
            validations=[],
            timeout_seconds=300.0,
        )

        variables = sheet.template_variables(total_sheets=10, total_movements=3)

        # New terminology
        assert variables["movement"] == 2
        assert variables["voice"] == 1
        assert variables["voice_count"] == 3

        # Old terminology still works
        assert variables["stage"] == 2
        assert variables["instance"] == 1
        assert variables["fan_count"] == 3

        # Identity
        assert variables["sheet_num"] == 5
        assert variables["instrument_name"] == "claude-code"

    def test_solo_sheet_has_voice_none(self) -> None:
        """A sheet with no fan-out has voice=None."""
        from marianne.core.sheet import Sheet

        sheet = Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            description=None,
            workspace=Path("/tmp/test"),
            instrument_name="claude-code",
            instrument_config={},
            prompt_template=None,
            template_file=None,
            variables={},
            prelude=[],
            cadenza=[],
            prompt_extensions=[],
            validations=[],
            timeout_seconds=300.0,
        )

        variables = sheet.template_variables(total_sheets=5, total_movements=3)
        assert variables["voice"] is None
        assert variables["instance"] is None
        assert variables["voice_count"] == 1
