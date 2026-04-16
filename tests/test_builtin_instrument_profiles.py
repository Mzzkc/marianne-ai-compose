"""Tests for built-in instrument profile YAML files.

Validates that all YAML files in src/marianne/instruments/builtins/
parse correctly into InstrumentProfile models. This ensures the
shipped profiles won't fail at conductor startup.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from marianne.core.config.instruments import InstrumentProfile

BUILTINS_DIR = Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"


def _load_all_profiles() -> list[tuple[str, InstrumentProfile]]:
    """Load all built-in profiles, returning (filename, profile) pairs."""
    profiles = []
    for f in sorted(BUILTINS_DIR.glob("*.yaml")):
        with open(f) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        profiles.append((f.name, profile))
    return profiles


class TestBuiltinProfilesExist:
    """Verify expected profiles are shipped."""

    def test_builtins_directory_exists(self) -> None:
        """The builtins directory exists."""
        assert BUILTINS_DIR.is_dir(), f"Missing builtins directory: {BUILTINS_DIR}"

    def test_expected_profiles_present(self) -> None:
        """All 8 expected instrument profiles are present."""
        expected = {
            "claude-code.yaml",
            "gemini-cli.yaml",
            "codex-cli.yaml",
            "cline-cli.yaml",
            "aider.yaml",
            "goose.yaml",
            "opencode.yaml",
            "crush.yaml",
        }
        actual = {f.name for f in BUILTINS_DIR.glob("*.yaml")}
        missing = expected - actual
        assert not missing, f"Missing profiles: {missing}"


class TestBuiltinProfilesValid:
    """Verify all profiles parse into valid InstrumentProfile instances."""

    @pytest.fixture(params=[p[0] for p in _load_all_profiles()], ids=lambda x: x)
    def profile_name(self, request: pytest.FixtureRequest) -> str:
        """Parameterize over all profile filenames."""
        return request.param  # type: ignore[return-value]

    def test_profile_parses(self, profile_name: str) -> None:
        """Each profile YAML parses into a valid InstrumentProfile."""
        path = BUILTINS_DIR / profile_name
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        assert profile.name
        assert profile.display_name
        assert profile.kind == "cli"
        assert profile.cli is not None
        assert profile.cli.command.executable

    def test_profile_has_executable(self, profile_name: str) -> None:
        """Each profile has a non-empty executable name."""
        path = BUILTINS_DIR / profile_name
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        assert len(profile.cli.command.executable) > 0  # type: ignore[union-attr]


class TestProfileDetails:
    """Test specific profile details for correctness."""

    def test_claude_code_has_mcp_support(self) -> None:
        """Claude Code profile declares MCP capability."""
        path = BUILTINS_DIR / "claude-code.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        assert "mcp" in profile.capabilities
        assert profile.cli is not None
        assert profile.cli.command.mcp_config_flag is not None

    def test_gemini_has_models(self) -> None:
        """Gemini CLI profile has model definitions with costs."""
        path = BUILTINS_DIR / "gemini-cli.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        assert len(profile.models) >= 2
        pro = next(m for m in profile.models if "pro" in m.name)
        assert pro.context_window == 1_000_000
        assert pro.cost_per_1k_input > 0

    def test_aider_has_text_output(self) -> None:
        """Aider profile uses text output (no JSON mode available)."""
        path = BUILTINS_DIR / "aider.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        assert profile.cli is not None
        assert profile.cli.output.format == "text"

    def test_codex_uses_positional_prompt(self) -> None:
        """Codex CLI uses positional prompt (prompt_flag is None)."""
        path = BUILTINS_DIR / "codex-cli.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        assert profile.cli is not None
        assert profile.cli.command.prompt_flag is None
        assert profile.cli.command.subcommand == "exec"

    def test_opencode_has_mcp_and_free_models(self) -> None:
        """OpenCode profile declares MCP capability and free-tier models."""
        path = BUILTINS_DIR / "opencode.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        assert "mcp" in profile.capabilities
        assert "tool_use" in profile.capabilities
        assert "structured_output" in profile.capabilities
        assert profile.cli is not None
        assert profile.cli.command.executable == "opencode"
        assert profile.cli.command.prompt_flag is None
        assert profile.cli.command.output_format_flag == "--format"
        assert profile.cli.command.output_format_value == "json"
        assert profile.cli.output.format == "jsonl"
        # All models should be free-tier
        assert len(profile.models) >= 5
        for model in profile.models:
            assert model.cost_per_1k_input == 0.0, f"{model.name} is not free"
            assert model.cost_per_1k_output == 0.0, f"{model.name} is not free"

    def test_all_profiles_have_rate_limit_patterns(self) -> None:
        """Every API-backed profile should have rate limit detection patterns."""
        skip_no_rate_limit = {"cli.yaml"}
        for f in BUILTINS_DIR.glob("*.yaml"):
            with open(f) as fh:
                data = yaml.safe_load(fh)
            profile = InstrumentProfile.model_validate(data)
            assert profile.cli is not None
            if f.name in skip_no_rate_limit:
                continue
            assert len(profile.cli.errors.rate_limit_patterns) > 0, (
                f"{f.name} has no rate limit patterns"
            )
