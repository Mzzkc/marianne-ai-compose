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
        """All expected instrument profiles are present."""
        expected = {
            "antigravity.yaml",
            "claude-code.yaml",
            "gemini-cli.yaml",
            "qwen-code.yaml",
            "openclaw.yaml",
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

    def test_standalone_gpt_5_6_profile_is_removed(self) -> None:
        """GPT-5.6 is a Codex model family, not an instrument."""
        assert not (BUILTINS_DIR / "gpt-5.6.yaml").exists()


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
        assert profile.kind in {"cli", "http"}
        if profile.kind == "cli":
            assert profile.cli is not None
            assert profile.cli.command.executable
        else:
            assert profile.http is not None

    def test_profile_has_executable(self, profile_name: str) -> None:
        """Each profile has a non-empty executable name."""
        path = BUILTINS_DIR / profile_name
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        if profile.kind == "cli":
            assert len(profile.cli.command.executable) > 0  # type: ignore[union-attr]
        else:
            assert profile.http is not None
            assert profile.http.endpoint == "/chat/completions"


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
        assert profile.cli.command.mcp_config_prefix_args == ["--strict-mcp-config"]

    def test_claude_code_has_glm_5_3_max_reasoning(self) -> None:
        """Claude Code exposes only the 1M GLM 5.3 route at max effort."""
        path = BUILTINS_DIR / "claude-code.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)

        models = {model.name: model for model in profile.models}
        assert "glm-5.3[1m]" in models
        assert "glm-5.2[1m]" not in models
        assert models["glm-5.3[1m]"].context_window == 1_000_000
        assert models["glm-5.3[1m]"].max_output_tokens == 131_072
        assert profile.cli is not None
        assert profile.cli.command.extra_flags[-2:] == ["--effort", "max"]

    def test_claude_code_lists_the_5_5_generation_and_fable_5_2(self) -> None:
        """GH #552: Sonnet 5.5, Haiku 5.5 and Fable 5.2 are listed beside Opus 5.5.

        Listing Fable is a profile fact; "no Fable for subagents and Anthropic
        score seats" is a casting rule, enforced where seats are cast.
        """
        path = BUILTINS_DIR / "claude-code.yaml"
        with open(path) as fh:
            profile = InstrumentProfile.model_validate(yaml.safe_load(fh))
        models = {model.name: model for model in profile.models}
        for name in (
            "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2",
        ):
            assert name in models, f"{name} missing from claude-code"
            assert models[name].context_window == 1_000_000
            assert models[name].max_output_tokens == 128_000
        # Superseded-but-served models stay so existing scores keep resolving.
        for name in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5-1"):
            assert name in models

    def test_codex_lists_gpt_6_1_sol_and_terra(self) -> None:
        """GH #552: codex 0.166.0's models_cache lists 6.1 Sol/Terra at 500000 context."""
        path = BUILTINS_DIR / "codex-cli.yaml"
        with open(path) as fh:
            profile = InstrumentProfile.model_validate(yaml.safe_load(fh))
        models = {model.name: model for model in profile.models}
        assert profile.default_model is None
        for name in ("gpt-6.1-sol", "gpt-6.1-terra"):
            assert models[name].context_window == 500_000
            assert models[name].max_output_tokens == 128_000
        # gpt-reserve and codex-auto-review are `hide` rows in codex's cache and
        # the catalog does not call them coding models: not listed.
        assert "gpt-reserve" not in models and "codex-auto-review" not in models
        for name in ("gpt-6-sol", "gpt-6-astra", "gpt-6-luna", "gpt-5.6-sol"):
            assert name in models

    def test_opencode_default_is_a_listed_live_free_model(self) -> None:
        """GH #552: the default is a free model the profile lists, not a dead OpenRouter id."""
        path = BUILTINS_DIR / "opencode.yaml"
        with open(path) as fh:
            profile = InstrumentProfile.model_validate(yaml.safe_load(fh))
        names = {model.name for model in profile.models}
        assert profile.default_model == "opencode/glm-5.3-flash-free"
        assert profile.default_model in names
        dead = {"qwen/qwen3-coder:free", "z-ai/glm-4.5-air:free"}
        assert not (names & dead)
        # opencode's own ids: a bare OpenRouter id (no provider prefix) is
        # rejected by opencode ("Unexpected server error").
        for name in names:
            assert name.startswith(("opencode/", "openrouter/", "zai-coding-plan/")), name

    def test_gemini_has_models(self) -> None:
        """Gemini CLI profile has model definitions and direct MCP settings merge."""
        path = BUILTINS_DIR / "gemini-cli.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        assert "mcp" in profile.capabilities
        assert profile.cli is not None
        assert profile.cli.command.mcp_config_workspace_path == ".gemini/settings.json"
        assert profile.cli.command.mcp_config_workspace_merge_key == "mcpServers"
        assert "--skip-trust" in profile.cli.command.extra_flags
        assert profile.execution_status == "unsupported"
        assert profile.execution_status_detail is not None
        assert "UNSUPPORTED_CLIENT" in profile.execution_status_detail
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

    def test_antigravity_defaults_to_interactive_mode(self) -> None:
        """Antigravity profile uses tmux interactive mode by default."""
        path = BUILTINS_DIR / "antigravity.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        assert "mcp" in profile.capabilities
        assert profile.cli is not None
        assert profile.cli.command.executable == "agy"
        assert profile.cli.command.prompt_flag == "-p"
        assert profile.cli.command.model_flag == "--model"
        assert profile.cli.command.mcp_config_workspace_path == ".agents/mcp_config.json"
        # Print-mode prompt delivery is ONE stream-json line on stdin (2026-10-14):
        # argv dies at the kernel per-argument limit for 100 KB+ preludes, and
        # agy has no raw-text stdin form. Interactive launch omits all of this.
        assert profile.cli.command.prompt_via_stdin is True
        assert profile.cli.command.stdin_sentinel == ""
        assert profile.cli.command.stdin_json_template == {
            "event": "user",
            "message": {"role": "user", "content": "{prompt}"},
        }
        assert profile.cli.command.output_format_value == "stream-json"
        assert "--input-format" in profile.cli.command.extra_flags
        assert profile.cli.interactive is not None
        assert profile.cli.interactive.enabled_by_default is True
        assert profile.cli.interactive.ready_pattern == r"(?m)^>\s*$"
        assert any(
            "Individual quota reached" in pattern
            for pattern in profile.cli.interactive.rate_limit_screen_patterns
        )
        assert any(
            "bubbletea" in pattern.lower()
            for pattern in profile.cli.errors.crash_patterns
        )
        assert profile.cli.output.format == "jsonl"
        assert profile.cli.output.event_type_key == "event"
        assert profile.cli.output.completion_event_type == "result"
        assert profile.cli.output.result_path == "result.response"

    def test_codex_uses_positional_prompt(self) -> None:
        """Codex CLI uses positional prompt (prompt_flag is None)."""
        path = BUILTINS_DIR / "codex-cli.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)
        assert profile.cli is not None
        assert profile.cli.command.prompt_flag is None
        assert profile.cli.command.subcommand == "exec"

    def test_codex_uses_current_unattended_exec_flag(self) -> None:
        """Codex exec uses the unattended flag exposed by current releases."""
        path = BUILTINS_DIR / "codex-cli.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)

        assert profile.cli is not None
        assert (
            profile.cli.command.auto_approve_flag
            == "--dangerously-bypass-approvals-and-sandbox"
        )

    def test_codex_owns_complete_gpt_5_6_family_without_default(self) -> None:
        """Codex carries GPT-5.6 metadata but lets the client choose its default."""
        path = BUILTINS_DIR / "codex-cli.yaml"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        profile = InstrumentProfile.model_validate(data)

        assert profile.default_model is None
        assert {"vision", "thinking"} <= set(profile.capabilities)
        assert profile.cli is not None
        assert profile.cli.command.model_flag == "--model"

        expected = {
            # Prices reconciled against models.dev 2026-11-10 (GH #522).
            "gpt-5.6-sol": (272_000, 0.004, 0.02, 128_000, 4),
            "gpt-5.6-sol[1m]": (1_050_000, 0.004, 0.02, 128_000, 4),
            "gpt-5.6-terra": (272_000, 0.002, 0.012, 128_000, 6),
            "gpt-5.6-terra[1m]": (1_050_000, 0.002, 0.012, 128_000, 6),
            "gpt-5.6-luna": (272_000, 0.0002, 0.0012, 128_000, 8),
            "gpt-5.6-luna[1m]": (1_050_000, 0.0002, 0.0012, 128_000, 8),
        }
        actual = {
            model.name: (
                model.context_window,
                model.cost_per_1k_input,
                model.cost_per_1k_output,
                model.max_output_tokens,
                model.max_concurrent,
            )
            for model in profile.models
            if model.name.startswith("gpt-5.6-")
        }
        assert actual == expected

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
        model_names = {model.name.lower() for model in profile.models}
        assert not any("kimi" in name for name in model_names)
        assert not any("llama-4-maverick" in name for name in model_names)
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
            if profile.kind == "http":
                assert profile.http is not None
                continue
            assert profile.cli is not None
            if f.name in skip_no_rate_limit:
                continue
            assert len(profile.cli.errors.rate_limit_patterns) > 0, (
                f"{f.name} has no rate limit patterns"
            )


# =============================================================================
# Antigravity model set — pinned to the exact Gemini slugs advertised by the
# installed AGY 1.1.13 CLI. Hermetic: hard-codes the expected slugs rather than
# shelling out to `agy models`, so the gate stays green on a machine without a
# live Antigravity installation. The live CLI is the authority at commissioning
# time only (see plan integration gate); these unit tests pin the contract.
# =============================================================================


# The Gemini slugs reported by `agy models` on agy 1.5.10 (2026-11-17, GH
# #552). The -low/-medium/-high (Hydrogen also -xhigh) suffix is an AGY effort
# tier over each family, not a distinct model. gemini-pro-orchestrator-g1-pro
# is an orchestration wrapper, deliberately not listed. The 3.7/3.6/3.5/3.1-pro
# slugs were removed because agy no longer serves them.
ANTIGRAVITY_EXPECTED_SLUGS: frozenset[str] = frozenset(
    {
        "gemini-4-hydrogen-xhigh",
        "gemini-4-hydrogen-high",
        "gemini-4-hydrogen-medium",
        "gemini-4-hydrogen-low",
        "gemini-4-argon-high",
        "gemini-4-argon-medium",
        "gemini-4-argon-low",
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-low",
    }
)

# agy also brokers cross-vendor models through the same Google subscription
# seat (`agy models`, agy 1.5.10, 2026-11-17).
ANTIGRAVITY_CROSS_VENDOR_SLUGS: frozenset[str] = frozenset(
    {
        "claude-opus-5-5-high",
        "claude-opus-5-5-medium",
        "claude-opus-5-5-low",
        "claude-sonnet-5-5-high",
        "claude-sonnet-5-5-medium",
        "claude-sonnet-5-5-low",
        "gpt-oss-120b-medium",
    }
)

# Slugs agy 1.5.10 no longer lists; they must not come back.
ANTIGRAVITY_REMOVED_SLUGS: frozenset[str] = frozenset(
    {
        "gemini-3.7-flash-high",
        "gemini-3.7-flash-medium",
        "gemini-3.7-flash-low",
        "gemini-3.6-flash-high",
        "gemini-3.6-flash-medium",
        "gemini-3.6-flash-low",
        "gemini-3.5-flash-high",
        "gemini-3.5-flash-medium",
        "gemini-3.5-flash-low",
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-low",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
    }
)

# Unsuffixed / pre-effort-tier aliases that must NOT survive the refresh.
ANTIGRAVITY_STALE_ALIASES: frozenset[str] = frozenset(
    {
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.5-pro",
    }
)


def _load_antigravity_profile() -> InstrumentProfile:
    """Load and validate the shipped antigravity profile from disk."""
    path = BUILTINS_DIR / "antigravity.yaml"
    with open(path) as fh:
        data = yaml.safe_load(fh)
    return InstrumentProfile.model_validate(data)


class TestAntigravityModelSet:
    """Pin the exact Antigravity model set, default, and registry membership.

    The Antigravity profile represents the installed subscription-backed CLI
    (ChatGPT/Google subscription capacity), not direct API billing, so per-token
    costs are intentionally zero.
    """

    def test_exact_model_slug_set(self) -> None:
        """The profile exposes exactly the agy 1.5.10 slugs (Gemini + brokers)."""
        profile = _load_antigravity_profile()
        model_names = {m.name for m in profile.models}
        expected = ANTIGRAVITY_EXPECTED_SLUGS | ANTIGRAVITY_CROSS_VENDOR_SLUGS
        assert model_names == expected, (
            f"Antigravity model set drifted from `agy models` (agy 1.5.10).\n"
            f"  expected: {sorted(ANTIGRAVITY_EXPECTED_SLUGS)}\n"
            f"  actual:   {sorted(model_names)}\n"
            f"  missing:  {sorted(ANTIGRAVITY_EXPECTED_SLUGS - model_names)}\n"
            f"  extra:    {sorted(model_names - ANTIGRAVITY_EXPECTED_SLUGS)}"
        )

    def test_balanced_default_is_medium_flash(self) -> None:
        """The balanced current default is gemini-3.8-flash-medium."""
        profile = _load_antigravity_profile()
        assert profile.default_model == "gemini-3.8-flash-medium", (
            f"Default model is {profile.default_model!r}, expected "
            f"'gemini-3.8-flash-medium' (balanced current tier)."
        )
        # The default must also be a declared model.
        declared = {m.name for m in profile.models}
        assert "gemini-3.8-flash-medium" in declared, (
            "Default model gemini-3.8-flash-medium is not in the declared "
            "model set."
        )

    def test_no_stale_unsuffixed_aliases(self) -> None:
        """No pre-effort-tier (unsuffixed) model aliases survive the refresh."""
        profile = _load_antigravity_profile()
        model_names = {m.name for m in profile.models}
        stale = model_names & ANTIGRAVITY_STALE_ALIASES
        assert not stale, (
            f"Stale unsuffixed aliases still present: {sorted(stale)}. "
            f"These pre-date AGY effort tiers and no longer resolve in "
            f"`agy models` (AGY 1.1.13)."
        )

    def test_slugs_agy_no_longer_serves_are_gone(self) -> None:
        """Slugs absent from `agy models` (agy 1.5.10) are not advertised."""
        profile = _load_antigravity_profile()
        model_names = {m.name for m in profile.models}
        assert not (model_names & ANTIGRAVITY_REMOVED_SLUGS), sorted(
            model_names & ANTIGRAVITY_REMOVED_SLUGS
        )

    def test_gemini_4_family_is_listed(self) -> None:
        """Gemini 4 Argon and Hydrogen carry the catalog's capacity figures."""
        profile = _load_antigravity_profile()
        by_name = {m.name: m for m in profile.models}
        for name, model in by_name.items():
            if name.startswith("gemini-4-hydrogen-"):
                assert (model.context_window, model.max_output_tokens) == (1_048_576, 65_536)
            if name.startswith("gemini-4-argon-"):
                assert (model.context_window, model.max_output_tokens) == (1_048_576, 32_768)
        assert "gemini-4-hydrogen-xhigh" in by_name

    def test_costs_are_zero_subscription_backed(self) -> None:
        """Per-token costs are zero: subscription-backed CLI, not API billing."""
        profile = _load_antigravity_profile()
        for model in profile.models:
            assert model.cost_per_1k_input == 0.0, (
                f"{model.name} has non-zero input cost "
                f"({model.cost_per_1k_input}); this profile models the "
                f"installed subscription-backed CLI, not API billing."
            )
            assert model.cost_per_1k_output == 0.0, (
                f"{model.name} has non-zero output cost "
                f"({model.cost_per_1k_output}); this profile models the "
                f"installed subscription-backed CLI, not API billing."
            )

    def test_capacity_metadata_matches_current_and_historical_contracts(self) -> None:
        """Gemini 4 and 3.8 carry catalog capacity; cross-vendor rows follow their
        direct-vendor contract."""
        profile = _load_antigravity_profile()
        cross_vendor = {
            "claude-opus-5-5-high": (1_000_000, 128_000),
            "claude-opus-5-5-medium": (1_000_000, 128_000),
            "claude-opus-5-5-low": (1_000_000, 128_000),
            "claude-sonnet-5-5-high": (1_000_000, 128_000),
            "claude-sonnet-5-5-medium": (1_000_000, 128_000),
            "claude-sonnet-5-5-low": (1_000_000, 128_000),
            "gpt-oss-120b-medium": (131_072, 32_768),
        }
        for model in profile.models:
            if model.name in ANTIGRAVITY_CROSS_VENDOR_SLUGS:
                assert (model.context_window, model.max_output_tokens) == cross_vendor[
                    model.name
                ], f"{model.name} capacity drifted from the catalog contract."
                continue
            if model.name.startswith("gemini-4-argon-"):
                continue  # pinned in test_gemini_4_family_is_listed
            assert model.context_window == 1_048_576, model.name
            assert model.max_output_tokens == 65_536, (
                f"{model.name} max_output_tokens={model.max_output_tokens}, "
                f"expected the conservative 65,536."
            )

    def test_registry_membership(self) -> None:
        """Antigravity loads via the loader and resolves through the registry.

        Semantic membership: the profile is discoverable by name and carries
        the exact expected model set + default, independent of how many other
        built-in profiles happen to ship alongside it.
        """
        from marianne.instruments.loader import InstrumentProfileLoader
        from marianne.instruments.registry import InstrumentRegistry

        profiles = InstrumentProfileLoader.load_directory(BUILTINS_DIR)
        assert "antigravity" in profiles, "antigravity profile missing from builtins"

        registry = InstrumentRegistry()
        for shipped in profiles.values():
            registry.register(shipped)

        resolved = registry.get("antigravity")
        assert resolved is not None, "antigravity did not resolve through the registry"
        assert resolved.default_model == "gemini-3.8-flash-medium"
        assert {m.name for m in resolved.models} == set(
        ANTIGRAVITY_EXPECTED_SLUGS | ANTIGRAVITY_CROSS_VENDOR_SLUGS
    )
