"""Tests for F-150: instrument_config.model wired through to backends.

The instrument_config.model key allows score authors to override the default
model at score-level, movement-level, and per-sheet level. Before this fix,
the model key was silently ignored — the backend always used the profile's
default_model. This test suite ensures the full pipeline works:

1. PluginCliBackend.apply_overrides({"model": ...}) actually changes _model
2. PluginCliBackend.clear_overrides() restores the original default_model
3. BackendPool.acquire() passes model to the backend
4. BackendPool.release() clears overrides to prevent cross-sheet contamination
5. BatonAdapter extracts model from sheet.instrument_config at dispatch time
6. The model override appears in _build_command() output
7. The model override flows through to ExecutionResult.model

TDD: Tests define the contract. Implementation fulfills it.
"""

from __future__ import annotations

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_profile(
    *,
    name: str = "test-instrument",
    default_model: str = "default-model",
    model_flag: str = "--model",
    executable: str = "echo",
    prompt_flag: str | None = "-p",
) -> InstrumentProfile:
    """Create a minimal InstrumentProfile for model override testing."""
    return InstrumentProfile(
        name=name,
        display_name="Test Instrument",
        kind="cli",
        models=[
            ModelCapacity(
                name=default_model,
                context_window=128000,
                cost_per_1k_input=0.01,
                cost_per_1k_output=0.03,
            ),
        ],
        default_model=default_model,
        cli=CliProfile(
            command=CliCommand(
                executable=executable,
                prompt_flag=prompt_flag,
                model_flag=model_flag,
            ),
            output=CliOutputConfig(format="text"),
            errors=CliErrorConfig(success_exit_codes=[0]),
        ),
    )


# ---------------------------------------------------------------------------
# PluginCliBackend: apply_overrides / clear_overrides
# ---------------------------------------------------------------------------


class TestPluginCliBackendModelOverride:
    """PluginCliBackend must support model override via apply_overrides."""

    def test_default_model_from_profile(self) -> None:
        """Backend initializes with profile's default_model."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(default_model="gemini-2.5-pro")
        backend = PluginCliBackend(profile)
        assert backend._model == "gemini-2.5-pro"

    def test_apply_overrides_changes_model(self) -> None:
        """apply_overrides with 'model' key changes the active model."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(default_model="gemini-2.5-pro")
        backend = PluginCliBackend(profile)

        backend.apply_overrides({"model": "gemini-2.5-flash"})
        assert backend._model == "gemini-2.5-flash"

    def test_clear_overrides_restores_default(self) -> None:
        """clear_overrides restores the profile's default_model."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(default_model="gemini-2.5-pro")
        backend = PluginCliBackend(profile)

        backend.apply_overrides({"model": "gemini-2.5-flash"})
        assert backend._model == "gemini-2.5-flash"

        backend.clear_overrides()
        assert backend._model == "gemini-2.5-pro"

    def test_apply_overrides_without_model_is_noop(self) -> None:
        """apply_overrides with unrelated keys doesn't change model."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(default_model="gemini-2.5-pro")
        backend = PluginCliBackend(profile)

        backend.apply_overrides({"timeout_seconds": 300})
        assert backend._model == "gemini-2.5-pro"

    def test_clear_overrides_without_apply_is_safe(self) -> None:
        """clear_overrides without prior apply_overrides is a no-op."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(default_model="gemini-2.5-pro")
        backend = PluginCliBackend(profile)

        backend.clear_overrides()  # Should not raise
        assert backend._model == "gemini-2.5-pro"

    def test_apply_overrides_empty_dict_is_noop(self) -> None:
        """apply_overrides with empty dict doesn't change model."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(default_model="gemini-2.5-pro")
        backend = PluginCliBackend(profile)

        backend.apply_overrides({})
        assert backend._model == "gemini-2.5-pro"

    def test_model_override_appears_in_build_command(self) -> None:
        """Overridden model appears in the CLI command arguments."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            default_model="gemini-2.5-pro",
            model_flag="--model",
        )
        backend = PluginCliBackend(profile)

        # Before override: default model in command
        cmd_before = backend._build_command("test", timeout_seconds=None)
        model_idx = cmd_before.index("--model")
        assert cmd_before[model_idx + 1] == "gemini-2.5-pro"

        # After override: new model in command
        backend.apply_overrides({"model": "gemini-2.5-flash"})
        cmd_after = backend._build_command("test", timeout_seconds=None)
        model_idx = cmd_after.index("--model")
        assert cmd_after[model_idx + 1] == "gemini-2.5-flash"

        # After clear: back to default
        backend.clear_overrides()
        cmd_restored = backend._build_command("test", timeout_seconds=None)
        model_idx = cmd_restored.index("--model")
        assert cmd_restored[model_idx + 1] == "gemini-2.5-pro"

    def test_model_override_flows_to_execution_result(self) -> None:
        """Overridden model is reported in ExecutionResult.model."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(default_model="gemini-2.5-pro")
        backend = PluginCliBackend(profile)

        backend.apply_overrides({"model": "gemini-2.5-flash"})
        result = backend._parse_output("output text", "", exit_code=0)
        assert result.model == "gemini-2.5-flash"

    def test_sequential_overrides_are_independent(self) -> None:
        """Each apply/clear cycle is independent — no state leaks."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(default_model="default")
        backend = PluginCliBackend(profile)

        # First override cycle
        backend.apply_overrides({"model": "model-a"})
        assert backend._model == "model-a"
        backend.clear_overrides()
        assert backend._model == "default"

        # Second override cycle — should start clean
        backend.apply_overrides({"model": "model-b"})
        assert backend._model == "model-b"
        backend.clear_overrides()
        assert backend._model == "default"

    def test_model_override_with_none_profile_default(self) -> None:
        """Override works even when profile has no default_model."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(default_model="default")
        # Manually set default_model to None to simulate no-default profile
        profile_dict = profile.model_dump()
        profile_dict["default_model"] = None
        profile_no_default = InstrumentProfile(**profile_dict)
        backend = PluginCliBackend(profile_no_default)

        assert backend._model is None

        backend.apply_overrides({"model": "explicit-model"})
        assert backend._model == "explicit-model"

        backend.clear_overrides()
        assert backend._model is None


# ---------------------------------------------------------------------------
# BackendPool: model pass-through and cleanup on release
# ---------------------------------------------------------------------------


class TestBackendPoolModelOverride:
    """BackendPool must pass model to backends and clear on release."""

    @pytest.mark.asyncio
    async def test_acquire_passes_model_to_new_backend(self) -> None:
        """When creating a new backend, model override is applied."""
        from marianne.daemon.baton.backend_pool import BackendPool
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        profile = _make_profile(name="test-instr", default_model="default")
        registry.register(profile)

        pool = BackendPool(registry)
        backend = await pool.acquire("test-instr", model="override-model")
        assert backend._model == "override-model"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_acquire_passes_model_to_reused_backend(self) -> None:
        """When reusing a backend from free list, model override is applied."""
        from marianne.daemon.baton.backend_pool import BackendPool
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        profile = _make_profile(name="test-instr", default_model="default")
        registry.register(profile)

        pool = BackendPool(registry)

        # Acquire and release — puts backend in free list
        backend1 = await pool.acquire("test-instr", model="model-a")
        await pool.release("test-instr", backend1)

        # Re-acquire with different model
        backend2 = await pool.acquire("test-instr", model="model-b")
        assert backend2._model == "model-b"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_release_clears_model_override(self) -> None:
        """Releasing a backend restores its default model."""
        from marianne.daemon.baton.backend_pool import BackendPool
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        profile = _make_profile(name="test-instr", default_model="default")
        registry.register(profile)

        pool = BackendPool(registry)
        backend = await pool.acquire("test-instr", model="override")
        assert backend._model == "override"  # type: ignore[attr-defined]

        await pool.release("test-instr", backend)
        # After release, model should be restored to default
        assert backend._model == "default"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_acquire_without_model_uses_profile_default(self) -> None:
        """When no model is passed to acquire, profile default is used."""
        from marianne.daemon.baton.backend_pool import BackendPool
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        profile = _make_profile(name="test-instr", default_model="profile-default")
        registry.register(profile)

        pool = BackendPool(registry)
        backend = await pool.acquire("test-instr")
        assert backend._model == "profile-default"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_reused_backend_without_model_uses_default(self) -> None:
        """Reused backend with no model arg retains profile default after release clear."""
        from marianne.daemon.baton.backend_pool import BackendPool
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        profile = _make_profile(name="test-instr", default_model="default")
        registry.register(profile)

        pool = BackendPool(registry)

        # First acquire with override
        backend1 = await pool.acquire("test-instr", model="override")
        await pool.release("test-instr", backend1)

        # Re-acquire without model — should get profile default, not previous override
        backend2 = await pool.acquire("test-instr")
        assert backend2._model == "default"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Sheet instrument_config.model resolution in build_sheets()
# ---------------------------------------------------------------------------


class TestBuildSheetsInstrumentConfigModel:
    """build_sheets correctly propagates model in instrument_config."""

    def test_score_level_model_in_instrument_config(self) -> None:
        """Score-level instrument_config.model flows to Sheet."""
        from marianne.core.config.job import JobConfig

        config = JobConfig.from_yaml_string("""
            name: model-override-test
            workspace: ./ws
            instrument: gemini-cli
            instrument_config:
              model: gemini-2.5-flash
            sheet:
              size: 1
              total_items: 2
            prompt:
              template: "Do something"
        """)
        from marianne.core.sheet import build_sheets
        sheets = build_sheets(config)

        for sheet in sheets:
            assert sheet.instrument_config.get("model") == "gemini-2.5-flash"

    def test_movement_level_model_overrides_score(self) -> None:
        """Movement-level instrument_config.model overrides score-level."""
        from marianne.core.config.job import JobConfig

        config = JobConfig.from_yaml_string("""
            name: movement-model-test
            workspace: ./ws
            instrument: gemini-cli
            instrument_config:
              model: gemini-2.5-pro
            movements:
              1:
                instrument_config:
                  model: gemini-2.5-flash
            sheet:
              size: 1
              total_items: 2
            prompt:
              template: "Do something"
        """)
        from marianne.core.sheet import build_sheets
        sheets = build_sheets(config)

        # Sheet 1 is in movement 1 — should get movement-level override
        sheet_1 = next(s for s in sheets if s.num == 1)
        assert sheet_1.instrument_config.get("model") == "gemini-2.5-flash"

        # Sheet 2 is in movement 2 — should get score-level default
        sheet_2 = next(s for s in sheets if s.num == 2)
        assert sheet_2.instrument_config.get("model") == "gemini-2.5-pro"

    def test_per_sheet_model_overrides_everything(self) -> None:
        """Per-sheet instrument_config.model overrides both score and movement."""
        from marianne.core.config.job import JobConfig

        config = JobConfig.from_yaml_string("""
            name: per-sheet-model-test
            workspace: ./ws
            instrument: gemini-cli
            instrument_config:
              model: gemini-2.5-pro
            sheet:
              size: 1
              total_items: 3
              per_sheet_instrument_config:
                2:
                  model: gemini-2.5-flash
            prompt:
              template: "Do something"
        """)
        from marianne.core.sheet import build_sheets
        sheets = build_sheets(config)

        sheet_1 = next(s for s in sheets if s.num == 1)
        assert sheet_1.instrument_config.get("model") == "gemini-2.5-pro"

        sheet_2 = next(s for s in sheets if s.num == 2)
        assert sheet_2.instrument_config.get("model") == "gemini-2.5-flash"

        sheet_3 = next(s for s in sheets if s.num == 3)
        assert sheet_3.instrument_config.get("model") == "gemini-2.5-pro"

    def test_instrument_def_config_model(self) -> None:
        """Score-level instrument aliases with config.model flow to sheets."""
        from marianne.core.config.job import JobConfig

        config = JobConfig.from_yaml_string("""
            name: alias-model-test
            workspace: ./ws
            instruments:
              fast-gemini:
                profile: gemini-cli
                config:
                  model: gemini-2.5-flash
            instrument: fast-gemini
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "Do something"
        """)
        from marianne.core.sheet import build_sheets
        sheets = build_sheets(config)

        assert sheets[0].instrument_config.get("model") == "gemini-2.5-flash"
