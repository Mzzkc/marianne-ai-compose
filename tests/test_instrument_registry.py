"""Generic InstrumentRegistry behavior after native bridge removal."""

from __future__ import annotations

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.instruments.registry import InstrumentRegistry


def _profile(name: str, *, executable: str = "test") -> InstrumentProfile:
    return InstrumentProfile(
        name=name,
        display_name=name.upper(),
        kind="cli",
        cli=CliProfile(
            command=CliCommand(executable=executable),
            output=CliOutputConfig(format="text"),
        ),
    )


def test_empty_registry_has_no_profiles() -> None:
    registry = InstrumentRegistry()

    assert len(registry) == 0
    assert registry.get("anything") is None


def test_register_and_get_profile() -> None:
    registry = InstrumentRegistry()
    profile = _profile("test-cli")

    registry.register(profile)

    assert registry.get("test-cli") is profile


def test_register_duplicate_rejects_accidental_collision() -> None:
    registry = InstrumentRegistry()
    profile = _profile("duplicate")
    registry.register(profile)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(profile)


def test_explicit_override_replaces_existing_profile() -> None:
    registry = InstrumentRegistry()
    first = _profile("duplicate", executable="first")
    second = _profile("duplicate", executable="second")
    registry.register(first)

    registry.register(second, override=True)

    assert registry.get("duplicate") is second


def test_list_all_is_sorted_by_profile_name() -> None:
    registry = InstrumentRegistry()
    for name in ["zzz", "aaa", "mmm"]:
        registry.register(_profile(name))

    assert [profile.name for profile in registry.list_all()] == ["aaa", "mmm", "zzz"]


def test_membership_uses_exact_profile_names() -> None:
    registry = InstrumentRegistry()
    registry.register(_profile("exists"))

    assert "exists" in registry
    assert "missing" not in registry


def test_replace_all_updates_registry_in_place() -> None:
    registry = InstrumentRegistry()
    original = _profile("original")
    replacement = _profile("replacement")
    registry.register(original)

    registry.replace_all({replacement.name: replacement})

    assert registry.get("original") is None
    assert registry.get("replacement") is replacement
    assert len(registry) == 1
