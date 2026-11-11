"""Score-language key ``memory:`` (GH #496): schema slice."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings
from pydantic import ValidationError

from marianne.core.config import JobConfig
from marianne.core.config.memory import (
    MemoryBackendDecl,
    MemoryCaps,
    MemoryConfig,
    MemoryDefaults,
)

_pb_settings = settings(
    max_examples=20,
    deadline=1000,
    suppress_health_check=[HealthCheck.too_slow],
)

BASE = "name: t\nworkspace: {ws}\nsheet:\n  size: 1\n  total_items: 1\nprompt:\n  template: hi\n"


def _load(tmp_path: Path, extra: str = "") -> JobConfig:
    score = tmp_path / "score.yaml"
    score.write_text(BASE.format(ws=tmp_path.as_posix()) + extra, encoding="utf-8")
    return JobConfig.from_yaml(score)


def test_score_without_key_loads_with_none(tmp_path: Path) -> None:
    assert _load(tmp_path).memory is None


def test_empty_memory_key_is_legal(tmp_path: Path) -> None:
    config = _load(tmp_path, "memory: {}\n")
    assert config.memory == MemoryConfig()
    assert config.memory.backends == {}


def test_full_key_parses(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        "memory:\n"
        "  backends:\n"
        "    main:\n"
        "      system: files\n"
        "      scope: person/legion\n"
        "      access: inherit\n"
        "      trust: gateway-only\n"
        "      trust_native: false\n"
        "      phases: ['all']\n"
        "      caps: {recall_cap_bytes: 16384, provenance: required}\n"
        "      settings: {root: x}\n"
        "  defaults: {recall_cap_bytes: 32768, provenance: required, lock_timeout_seconds: 10.0}\n",
    )
    assert config.memory is not None
    decl = config.memory.backends["main"]
    assert decl.system == "files"
    assert decl.scope == "person/legion"
    assert decl.caps.recall_cap_bytes == 16384
    assert decl.settings == {"root": "x"}


def test_defaults_per_spec() -> None:
    decl = MemoryBackendDecl(system="files")
    assert decl.scope is None
    assert decl.access == "inherit"
    assert decl.trust == "gateway-only"
    assert decl.trust_native is False
    assert decl.phases == ["all"]
    assert decl.caps == MemoryCaps()
    d = MemoryDefaults()
    assert (d.recall_cap_bytes, d.provenance, d.lock_timeout_seconds) == (32768, "required", 10.0)


@pytest.mark.parametrize("name", ["Main", "1x", "a_b", "", "a" * 33, "x.y"])
def test_bad_backend_name_refused(name: str) -> None:
    with pytest.raises(ValidationError, match="backend name"):
        MemoryConfig.model_validate({"backends": {name: {"system": "files"}}})


@pytest.mark.parametrize("name", ["a", "main", "a-1", "a" * 32])
def test_good_backend_name_accepted(name: str) -> None:
    assert name in MemoryConfig.model_validate({"backends": {name: {"system": "files"}}}).backends


def test_unknown_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        MemoryBackendDecl.model_validate({"system": "files", "bogus": 1})
    with pytest.raises(ValidationError):
        MemoryConfig.model_validate({"nope": 1})


def test_trust_enum_enforced() -> None:
    with pytest.raises(ValidationError):
        MemoryBackendDecl.model_validate({"system": "files", "trust": "everyone"})


def test_provenance_off_normalized_to_optional() -> None:
    assert MemoryCaps.model_validate({"provenance": "off"}).provenance == "optional"
    assert MemoryDefaults.model_validate({"provenance": "off"}).provenance == "optional"


def test_phases_ints_coerced_to_strings() -> None:
    decl = MemoryBackendDecl.model_validate({"system": "files", "phases": [2, 3]})
    assert decl.phases == ["2", "3"]


def test_every_field_has_description() -> None:
    for model in (MemoryConfig, MemoryBackendDecl, MemoryCaps, MemoryDefaults):
        for name, info in model.model_fields.items():
            assert info.description, f"{model.__name__}.{name}"


# ------------------------------------------------------------ property tests

_names = st.from_regex(r"^[a-z][a-z0-9-]{0,31}$", fullmatch=True)


@pytest.mark.property_based
@_pb_settings
@given(
    cap=st.integers(min_value=1, max_value=2**24),
    prov=st.sampled_from(["required", "optional", "off"]),
)
def test_memory_caps_roundtrip(cap: int, prov: str) -> None:
    caps = MemoryCaps(recall_cap_bytes=cap, provenance=prov)
    assert MemoryCaps.model_validate(caps.model_dump()) == caps


@pytest.mark.property_based
@_pb_settings
@given(
    cap=st.integers(min_value=1, max_value=2**24),
    lock=st.floats(min_value=0.001, max_value=3600, allow_nan=False),
    prov=st.sampled_from(["required", "optional", "off"]),
)
def test_memory_defaults_roundtrip(cap: int, lock: float, prov: str) -> None:
    d = MemoryDefaults(recall_cap_bytes=cap, lock_timeout_seconds=lock, provenance=prov)
    assert MemoryDefaults.model_validate(d.model_dump()) == d


@pytest.mark.property_based
@_pb_settings
@given(
    system=st.text(min_size=1, max_size=20),
    trust=st.sampled_from(["gateway-only", "backend", "trusted"]),
    native=st.booleans(),
    phases=st.lists(st.text(min_size=1, max_size=8), max_size=4),
    settings_=st.dictionaries(st.text(min_size=1, max_size=5), st.integers(), max_size=3),
)
def test_backend_decl_roundtrip(
    system: str,
    trust: Literal["gateway-only", "backend", "trusted"],
    native: bool,
    phases: list[str],
    settings_: dict[str, Any],
) -> None:
    decl = MemoryBackendDecl(
        system=system, trust=trust, trust_native=native, phases=phases, settings=settings_
    )
    assert MemoryBackendDecl.model_validate(decl.model_dump()) == decl


@pytest.mark.property_based
@_pb_settings
@given(names=st.lists(_names, min_size=0, max_size=4, unique=True))
def test_memory_config_roundtrip(names: list[str]) -> None:
    cfg = MemoryConfig(backends={n: MemoryBackendDecl(system="files") for n in names})
    assert MemoryConfig.model_validate(cfg.model_dump()) == cfg
