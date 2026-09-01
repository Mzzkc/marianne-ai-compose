"""Contracts for the current built-in Gemini 3.7 Flash profiles."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from marianne.core.config.instruments import InstrumentProfile
from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.instruments.loader import InstrumentProfileLoader

BUILTINS_DIR = (
    Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"
)


def load_builtin(name: str) -> InstrumentProfile:
    """Load one shipped profile through Marianne's production loader."""
    profiles = InstrumentProfileLoader.load_directory(BUILTINS_DIR)
    return profiles[name]


def test_gemini_cli_defaults_to_stable_3_7() -> None:
    profile = load_builtin("gemini-cli")

    assert profile.default_model == "gemini-3.7-flash"
    model = next(item for item in profile.models if item.name == profile.default_model)
    assert model.context_window == 1_048_576
    assert model.max_output_tokens == 65_536

    # A metadata refresh must not overstate the current individual-OAuth path.
    assert profile.execution_status == "unsupported"
    assert profile.execution_status_detail is not None
    assert "UNSUPPORTED_CLIENT" in profile.execution_status_detail


def test_antigravity_exposes_exact_3_7_effort_family() -> None:
    profile = load_builtin("antigravity")

    assert profile.default_model == "gemini-3.7-flash-medium"
    family = {
        model.name: model
        for model in profile.models
        if model.name.startswith("gemini-3.7-flash-")
    }
    assert set(family) == {
        "gemini-3.7-flash-high",
        "gemini-3.7-flash-medium",
        "gemini-3.7-flash-low",
    }
    for model in family.values():
        assert model.context_window == 1_048_576
        assert model.max_output_tokens == 65_536


def test_gemini_3_7_profiles_expose_supported_schema_capabilities() -> None:
    gemini_cli = load_builtin("gemini-cli")
    antigravity = load_builtin("antigravity")

    assert {"vision", "structured_output", "thinking"} <= gemini_cli.capabilities
    assert "thinking" in antigravity.capabilities


def test_older_gemini_families_remain_available_as_history_and_fallbacks() -> None:
    gemini_cli_names = {model.name for model in load_builtin("gemini-cli").models}
    antigravity_names = {model.name for model in load_builtin("antigravity").models}

    assert {
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
        "gemini-2.5-flash",
    } <= gemini_cli_names
    assert {
        "gemini-3.6-flash-medium",
        "gemini-3.5-flash-medium",
        "gemini-3.1-pro-high",
    } <= antigravity_names


def test_gemini_cli_passes_its_api_keys_and_filters_other_providers() -> None:
    profile = load_builtin("gemini-cli")
    backend = PluginCliBackend(profile)
    google_env = {
        "GEMINI_API_KEY": "gemini-key",
        "GOOGLE_API_KEY": "google-key",
        "GOOGLE_APPLICATION_CREDENTIALS": "/credentials/google.json",
        "GOOGLE_CLOUD_PROJECT": "google-project",
        "CLOUDSDK_CONFIG": "/config/gcloud",
    }

    with patch.dict(
        os.environ,
        {
            **google_env,
            "ANTHROPIC_API_KEY": "anthropic-key",
            "OPENAI_API_KEY": "openai-key",
        },
        clear=True,
    ):
        filtered = backend._build_env()

    assert filtered is not None
    assert {name: filtered[name] for name in google_env} == google_env
    assert "ANTHROPIC_API_KEY" not in filtered
    assert "OPENAI_API_KEY" not in filtered
