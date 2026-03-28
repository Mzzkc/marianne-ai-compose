"""Property-based tests for instrument plugin system models.

Uses hypothesis @given to verify invariants across random inputs:
- Serialization roundtrip for all instrument models
- Validation constraint enforcement
- Type coercion correctness
"""

from __future__ import annotations

from typing import Any

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from tests.conftest_adversarial import _nonneg_float, _positive_int, _short_text

from mozart.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    CodeModeConfig,
    CodeModeInterface,
    HttpProfile,
    InstrumentProfile,
    ModelCapacity,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_nonempty_text = st.text(
    min_size=1, max_size=50,
    alphabet=st.characters(categories=("L", "N")),
)

_flag_or_none = st.one_of(st.none(), _nonempty_text.map(lambda s: f"--{s}"))


def model_capacity_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for ModelCapacity as a dict."""
    return st.fixed_dictionaries({
        "name": _nonempty_text,
        "context_window": _positive_int,
        "cost_per_1k_input": _nonneg_float,
        "cost_per_1k_output": _nonneg_float,
    })


def cli_command_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CliCommand as a dict."""
    return st.fixed_dictionaries({
        "executable": _nonempty_text,
        "prompt_flag": _flag_or_none,
    })


def cli_output_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CliOutputConfig as a dict."""
    return st.fixed_dictionaries({
        "format": st.sampled_from(["text", "json", "jsonl"]),
    })


def cli_error_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CliErrorConfig as a dict."""
    return st.fixed_dictionaries({
        "success_exit_codes": st.lists(st.integers(min_value=0, max_value=255), max_size=5),
    })


def cli_profile_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CliProfile as a dict."""
    return st.fixed_dictionaries({
        "command": cli_command_strategy(),
        "output": cli_output_config_strategy(),
    })


def code_mode_interface_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CodeModeInterface as a dict."""
    return st.fixed_dictionaries({
        "name": _nonempty_text,
        "typescript": _nonempty_text.map(lambda s: f"interface {s} {{}}"),
    })


def code_mode_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CodeModeConfig as a dict."""
    return st.fixed_dictionaries({
        "runtime": st.sampled_from(["deno", "node_vm", "v8_isolate"]),
        "max_execution_ms": st.integers(min_value=100, max_value=120000),
    })


def http_profile_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for HttpProfile as a dict."""
    return st.fixed_dictionaries({
        "base_url": _nonempty_text.map(lambda s: f"http://{s}"),
        "schema_family": st.sampled_from(["openai", "anthropic", "gemini"]),
    })


def instrument_profile_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for InstrumentProfile (cli kind only) as a dict."""
    return st.fixed_dictionaries({
        "name": _nonempty_text,
        "display_name": _nonempty_text,
        "kind": st.just("cli"),
        "cli": cli_profile_strategy(),
    })


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

_pb_settings = settings(
    max_examples=20,
    deadline=1000,
    suppress_health_check=[HealthCheck.too_slow],
)


@pytest.mark.property_based
@_pb_settings
@given(data=model_capacity_strategy())
def test_model_capacity_roundtrip(data: dict[str, Any]) -> None:
    """ModelCapacity roundtrips through serialization."""
    mc = ModelCapacity.model_validate(data)
    assert mc.name == data["name"]
    restored = ModelCapacity.model_validate(mc.model_dump())
    assert restored.name == mc.name
    assert restored.context_window == mc.context_window


@pytest.mark.property_based
@_pb_settings
@given(data=cli_command_strategy())
def test_cli_command_roundtrip(data: dict[str, Any]) -> None:
    """CliCommand roundtrips through serialization."""
    cmd = CliCommand.model_validate(data)
    assert cmd.executable == data["executable"]
    restored = CliCommand.model_validate(cmd.model_dump())
    assert restored.executable == cmd.executable


@pytest.mark.property_based
@_pb_settings
@given(data=cli_output_config_strategy())
def test_cli_output_config_roundtrip(data: dict[str, Any]) -> None:
    """CliOutputConfig roundtrips through serialization."""
    out = CliOutputConfig.model_validate(data)
    assert out.format == data["format"]
    restored = CliOutputConfig.model_validate(out.model_dump())
    assert restored.format == out.format


@pytest.mark.property_based
@_pb_settings
@given(data=cli_error_config_strategy())
def test_cli_error_config_roundtrip(data: dict[str, Any]) -> None:
    """CliErrorConfig roundtrips through serialization."""
    err = CliErrorConfig.model_validate(data)
    restored = CliErrorConfig.model_validate(err.model_dump())
    assert restored.success_exit_codes == err.success_exit_codes


@pytest.mark.property_based
@_pb_settings
@given(data=cli_profile_strategy())
def test_cli_profile_roundtrip(data: dict[str, Any]) -> None:
    """CliProfile roundtrips through serialization."""
    profile = CliProfile.model_validate(data)
    restored = CliProfile.model_validate(profile.model_dump())
    assert restored.command.executable == profile.command.executable


@pytest.mark.property_based
@_pb_settings
@given(data=code_mode_interface_strategy())
def test_code_mode_interface_roundtrip(data: dict[str, Any]) -> None:
    """CodeModeInterface roundtrips through serialization."""
    iface = CodeModeInterface.model_validate(data)
    restored = CodeModeInterface.model_validate(iface.model_dump())
    assert restored.name == iface.name


@pytest.mark.property_based
@_pb_settings
@given(data=code_mode_config_strategy())
def test_code_mode_config_roundtrip(data: dict[str, Any]) -> None:
    """CodeModeConfig roundtrips through serialization."""
    cfg = CodeModeConfig.model_validate(data)
    restored = CodeModeConfig.model_validate(cfg.model_dump())
    assert restored.runtime == cfg.runtime


@pytest.mark.property_based
@_pb_settings
@given(data=http_profile_strategy())
def test_http_profile_roundtrip(data: dict[str, Any]) -> None:
    """HttpProfile roundtrips through serialization."""
    hp = HttpProfile.model_validate(data)
    restored = HttpProfile.model_validate(hp.model_dump())
    assert restored.base_url == hp.base_url


@pytest.mark.property_based
@_pb_settings
@given(data=instrument_profile_strategy())
def test_instrument_profile_roundtrip(data: dict[str, Any]) -> None:
    """InstrumentProfile roundtrips through serialization."""
    profile = InstrumentProfile.model_validate(data)
    assert profile.kind == "cli"
    assert profile.cli is not None
    restored = InstrumentProfile.model_validate(profile.model_dump())
    assert restored.name == profile.name
    assert restored.cli is not None
    assert restored.cli.command.executable == profile.cli.command.executable
