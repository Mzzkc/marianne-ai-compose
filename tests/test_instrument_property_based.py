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

from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    CodeModeConfig,
    CodeModeInterface,
    EffortMapping,
    HttpProfile,
    InstrumentProfile,
    InteractiveCliConfig,
    InteractiveGate,
    InteractiveUsageConfig,
    ModelCapacity,
)
from marianne.core.config.job import InstrumentDef, MovementDef
from tests.conftest_adversarial import _nonneg_float, _positive_int

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_nonempty_text = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(categories=("L", "N")),
)

_flag_or_none = st.one_of(st.none(), _nonempty_text.map(lambda s: f"--{s}"))


def model_capacity_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for ModelCapacity as a dict."""
    return st.fixed_dictionaries(
        {
            "name": _nonempty_text,
            "context_window": _positive_int,
            "cost_per_1k_input": _nonneg_float,
            "cost_per_1k_output": _nonneg_float,
        }
    )


def cli_command_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CliCommand as a dict."""
    return st.fixed_dictionaries(
        {
            "executable": _nonempty_text,
            "prompt_flag": _flag_or_none,
        }
    )


def cli_output_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CliOutputConfig as a dict."""
    return st.fixed_dictionaries(
        {
            "format": st.sampled_from(["text", "json", "jsonl"]),
        }
    )


def cli_error_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CliErrorConfig as a dict."""
    return st.fixed_dictionaries(
        {
            "success_exit_codes": st.lists(st.integers(min_value=0, max_value=255), max_size=5),
        }
    )


def cli_profile_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CliProfile as a dict."""
    return st.fixed_dictionaries(
        {
            "command": cli_command_strategy(),
            "output": cli_output_config_strategy(),
        }
    )


def interactive_gate_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for InteractiveGate as a dict.

    Patterns are built from letters/digits only so they always compile —
    invalid-regex rejection has its own dedicated test below.
    """
    return st.fixed_dictionaries(
        {
            "pattern": _nonempty_text,
            "keys": st.lists(
                st.sampled_from(["Enter", "Down", "Up", "Escape", "y"]),
                min_size=1,
                max_size=3,
            ),
        }
    )


def interactive_cli_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for InteractiveCliConfig as a dict."""
    return st.fixed_dictionaries(
        {
            "ready_pattern": _nonempty_text,
        },
        optional={
            "busy_patterns": st.lists(_nonempty_text, max_size=3),
            "startup_gates": st.lists(interactive_gate_strategy(), max_size=2),
            "quiet_seconds": st.floats(min_value=0.1, max_value=600.0),
            "poll_interval_seconds": st.floats(min_value=0.1, max_value=60.0),
            "startup_timeout_seconds": st.floats(min_value=1.0, max_value=600.0),
            "terminal_width": st.integers(min_value=40, max_value=500),
            "terminal_height": st.integers(min_value=10, max_value=200),
            "volatile_tail_lines": st.integers(min_value=0, max_value=10),
        },
    )


def code_mode_interface_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CodeModeInterface as a dict."""
    return st.fixed_dictionaries(
        {
            "name": _nonempty_text,
            "typescript": _nonempty_text.map(lambda s: f"interface {s} {{}}"),
        }
    )


def code_mode_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CodeModeConfig as a dict."""
    return st.fixed_dictionaries(
        {
            "runtime": st.sampled_from(["deno", "node_vm", "v8_isolate"]),
            "max_execution_ms": st.integers(min_value=100, max_value=120000),
        }
    )


def http_profile_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for HttpProfile as a dict."""
    return st.fixed_dictionaries(
        {
            "base_url": _nonempty_text.map(lambda s: f"http://{s}"),
            "schema_family": st.just("openai"),
        }
    )


def instrument_profile_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for InstrumentProfile (cli kind only) as a dict."""
    return st.fixed_dictionaries(
        {
            "name": _nonempty_text,
            "display_name": _nonempty_text,
            "kind": st.just("cli"),
            "cli": cli_profile_strategy(),
        }
    )


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
@given(data=interactive_gate_strategy())
def test_interactive_gate_roundtrip(data: dict[str, Any]) -> None:
    """InteractiveGate roundtrips through serialization."""
    gate = InteractiveGate.model_validate(data)
    assert gate.pattern == data["pattern"]
    restored = InteractiveGate.model_validate(gate.model_dump())
    assert restored.pattern == gate.pattern
    assert restored.keys == gate.keys


@pytest.mark.property_based
@_pb_settings
@given(data=interactive_cli_config_strategy())
def test_interactive_cli_config_roundtrip(data: dict[str, Any]) -> None:
    """InteractiveCliConfig roundtrips through serialization."""
    cfg = InteractiveCliConfig.model_validate(data)
    assert cfg.ready_pattern == data["ready_pattern"]
    restored = InteractiveCliConfig.model_validate(cfg.model_dump())
    assert restored.ready_pattern == cfg.ready_pattern
    assert restored.busy_patterns == cfg.busy_patterns
    assert restored.quiet_seconds == cfg.quiet_seconds
    assert len(restored.startup_gates) == len(cfg.startup_gates)


_USAGE_PATH = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_.", min_size=1, max_size=24)


@st.composite
def interactive_usage_config_strategy(draw: st.DrawFn) -> dict[str, Any]:
    """A valid interactive usage block: a session-id template, an input or output
    token path (the model refuses a cache-only block), and optional cache paths."""
    required = draw(st.lists(
        st.sampled_from(["input_tokens_path", "output_tokens_path"]), min_size=1, unique=True,
    ))
    optional = draw(st.lists(
        st.sampled_from(["cache_read_tokens_path", "cache_write_tokens_path"]), unique=True,
    ))
    chosen = [*required, *optional]
    data: dict[str, Any] = {
        "session_id_flag": "--" + draw(st.text(alphabet="abcdefghij-", min_size=1, max_size=12)),
        "record_path": draw(_USAGE_PATH) + "/{session_id}.jsonl",
        "record_filter": draw(st.dictionaries(_USAGE_PATH, _USAGE_PATH, max_size=2)),
        "ignore_models": draw(st.lists(_USAGE_PATH, max_size=2)),
    }
    for name in chosen:
        data[name] = draw(_USAGE_PATH)
    return data


@pytest.mark.property_based
@_pb_settings
@given(data=interactive_usage_config_strategy())
def test_interactive_usage_config_roundtrip(data: dict[str, Any]) -> None:
    """InteractiveUsageConfig roundtrips, and its two invariants hold (GH #533)."""
    cfg = InteractiveUsageConfig.model_validate(data)
    assert "{session_id}" in cfg.record_path
    restored = InteractiveUsageConfig.model_validate(cfg.model_dump())
    assert restored == cfg


@pytest.mark.property_based
@_pb_settings
@given(data=interactive_usage_config_strategy())
def test_interactive_usage_config_refuses_a_block_that_measures_nothing(
    data: dict[str, Any],
) -> None:
    """No token path, or a record path without the session id, is refused."""
    cache_only = {
        k: v for k, v in data.items()
        if k not in ("input_tokens_path", "output_tokens_path")
    }
    cache_only["cache_read_tokens_path"] = "usage.cache"
    with pytest.raises(ValueError):
        InteractiveUsageConfig.model_validate(cache_only)
    with pytest.raises(ValueError):
        InteractiveUsageConfig.model_validate({**data, "record_path": "no-template.jsonl"})


_EFFORT_LEVELS = ["minimal", "low", "medium", "high", "xhigh", "max"]
_NATIVE_WORD = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8)


@st.composite
def effort_mapping_strategy(draw: st.DrawFn) -> dict[str, Any]:
    """A whole effort mapping for each delivery shape (GH #535)."""
    delivery = draw(st.sampled_from(["flag", "config_override", "body_field"]))
    levels = draw(st.dictionaries(st.sampled_from(_EFFORT_LEVELS), _NATIVE_WORD, min_size=1))
    data: dict[str, Any] = {"delivery": delivery, "levels": levels}
    if delivery in ("flag", "config_override"):
        data["flag"] = "--" + draw(_NATIVE_WORD)
    if delivery in ("config_override", "body_field"):
        data["key"] = draw(_NATIVE_WORD)
    if draw(st.booleans()):
        data["schema_levels"] = draw(
            st.lists(st.sampled_from(sorted(levels)), min_size=1, unique=True),
        )
    data["interactive"] = draw(st.booleans())
    return data


@pytest.mark.property_based
@_pb_settings
@given(data=effort_mapping_strategy())
def test_effort_mapping_roundtrip(data: dict[str, Any]) -> None:
    """EffortMapping roundtrips for every delivery shape, and a mapped level stays mapped."""
    mapping = EffortMapping.model_validate(data)
    assert mapping.delivery == data["delivery"]
    assert dict(mapping.levels) == data["levels"]
    restored = EffortMapping.model_validate(mapping.model_dump())
    assert restored == mapping


@pytest.mark.property_based
@_pb_settings
@given(data=effort_mapping_strategy())
def test_effort_mapping_refuses_a_half_declared_shape(data: dict[str, Any]) -> None:
    """A delivery shape missing the field it needs is refused, never guessed."""
    needed = "key" if data["delivery"] == "body_field" else "flag"
    broken = {k: v for k, v in data.items() if k != needed}
    with pytest.raises(ValueError):
        EffortMapping.model_validate(broken)


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


# ---------------------------------------------------------------------------
# M4: InstrumentDef and MovementDef strategies and tests
# ---------------------------------------------------------------------------


def instrument_def_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for InstrumentDef as a dict."""
    return st.fixed_dictionaries(
        {
            "profile": _nonempty_text,
            "config": st.fixed_dictionaries(
                {},
                optional={
                    "model": _nonempty_text,
                    "timeout_seconds": _nonneg_float,
                },
            ),
        }
    )


def movement_def_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for MovementDef as a dict."""
    return st.fixed_dictionaries(
        {},
        optional={
            "name": _nonempty_text,
            "instrument": _nonempty_text,
            "instrument_config": st.fixed_dictionaries(
                {},
                optional={
                    "model": _nonempty_text,
                },
            ),
            "voices": _positive_int,
        },
    )


@pytest.mark.property_based
@_pb_settings
@given(data=instrument_def_strategy())
def test_instrument_def_roundtrip(data: dict[str, Any]) -> None:
    """InstrumentDef roundtrips through serialization."""
    idef = InstrumentDef.model_validate(data)
    assert idef.profile == data["profile"]
    restored = InstrumentDef.model_validate(idef.model_dump())
    assert restored.profile == idef.profile
    assert restored.config == idef.config


@pytest.mark.property_based
@_pb_settings
@given(data=movement_def_strategy())
def test_movement_def_roundtrip(data: dict[str, Any]) -> None:
    """MovementDef roundtrips through serialization."""
    mdef = MovementDef.model_validate(data)
    restored = MovementDef.model_validate(mdef.model_dump())
    assert restored.name == mdef.name
    assert restored.instrument == mdef.instrument
    assert restored.voices == mdef.voices
