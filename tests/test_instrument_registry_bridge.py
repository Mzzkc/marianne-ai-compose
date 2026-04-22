"""Observer test — the native instrument transition bridge.

This test file is the Phase-B gate for Coverage Gap G-9 (Critical).

Atlas Doctrine Rule enforced:

    RULE: register_native_instruments() must bridge legacy names until
          all native backends are removed.

    SCOPE: ``instruments/registry.py:277-304``

    RATIONALE: The bridge resolves legacy ``backend:`` names and
    ``instrument_fallbacks: [anthropic_api]`` score entries. 25+ example
    scores depend on it. If the bridge silently breaks during Phases
    1-4, scores stop resolving and the failure is only visible at
    job-execution time. Coverage Gap G-9 was flagged Critical because
    factory → registry resolution was previously untested end-to-end.

    EXCEPTIONS: None.

The existing ``tests/test_native_instrument_bridge.py`` covers the
happy-path registration shape. This observer file specifically covers
the TRANSITION GUARANTEES the doctrine cares about during Phases 1-4:

  * All four legacy names resolve to a real, usable InstrumentProfile
    (not ``None``, not a silent fallback to ``claude_cli``).
  * The ``instrument_fallbacks`` score field successfully round-trips
    through the registry so the bridge is exercised end-to-end.
  * Calling the bridge twice fails loudly (duplicate registrations
    must be explicit, not silent) OR becomes idempotent — whichever
    contract is locked in, the test pins it.
  * Names *outside* the bridge do NOT silently become ``claude_cli``.
  * Every legacy profile has the kind/metadata needed by the baton's
    backend pool so Phase 3 / Phase 4 can swap implementations safely.

These tests are integration-style: they use the real
``InstrumentRegistry``, the real ``register_native_instruments``, and
the real Pydantic ``JobConfig`` parser. No mocks of the module under
test — the doctrine explicitly forbids mocking the module under test.
"""

from __future__ import annotations

import pytest

from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.registry import (
    InstrumentRegistry,
    register_native_instruments,
)

DOCTRINE_RULE = (
    "Atlas Doctrine — RULE: register_native_instruments() must bridge "
    "legacy names until all native backends are removed "
    "(instruments/registry.py:277-304, Coverage Gap G-9 Critical)."
)


LEGACY_NAMES: tuple[str, ...] = (
    "claude_cli",
    "anthropic_api",
    "ollama",
    "recursive_light",
)


@pytest.fixture
def bridged_registry() -> InstrumentRegistry:
    """Return a fresh registry with the native bridge applied.

    Using a fresh registry in every test guarantees no cross-test
    contamination from prior registrations — required by the project
    testing conventions (``.marianne/spec/conventions.yaml`` — "No
    cross-test state leakage").
    """
    registry = InstrumentRegistry()
    register_native_instruments(registry)
    return registry


# --- Core transition-bridge guarantees --------------------------------------


@pytest.mark.parametrize("legacy_name", LEGACY_NAMES)
def test_bridge_resolves_every_legacy_name(
    bridged_registry: InstrumentRegistry, legacy_name: str
) -> None:
    """Every legacy ``backend:`` name must resolve to an InstrumentProfile.

    If this fails, 25+ example scores using ``instrument_fallbacks:
    [anthropic_api]`` and similar will silently fail to resolve during
    the migration. Pin this hard.
    """
    profile = bridged_registry.get(legacy_name)
    assert profile is not None, (
        f"Legacy name {legacy_name!r} did not resolve through the bridge. "
        f"{DOCTRINE_RULE}"
    )
    assert isinstance(profile, InstrumentProfile), (
        f"Bridge returned non-InstrumentProfile for {legacy_name!r}. "
        f"{DOCTRINE_RULE}"
    )
    assert profile.name == legacy_name, (
        f"Registered profile name {profile.name!r} does not match lookup "
        f"key {legacy_name!r} — lookup is the score-level contract. "
        f"{DOCTRINE_RULE}"
    )


def test_bridge_registers_exactly_four_legacy_names(
    bridged_registry: InstrumentRegistry,
) -> None:
    """The bridge must register exactly the documented 4 native names.

    Over-registration is a doctrine violation (new native backends are
    forbidden). Under-registration breaks transition scores.
    """
    registered = {p.name for p in bridged_registry.list_all()}
    assert registered == set(LEGACY_NAMES), (
        f"Bridge registered {registered} but doctrine requires exactly "
        f"{set(LEGACY_NAMES)}. "
        f"{DOCTRINE_RULE}"
    )


def test_bridge_profiles_carry_kind_needed_by_backend_pool(
    bridged_registry: InstrumentRegistry,
) -> None:
    """The baton's backend pool routes on ``profile.kind`` (cli vs http).

    If the bridge produces a profile with a missing or wrong ``kind``,
    the baton raises NotImplementedError at dispatch. Pin the kinds.
    """
    expected_kinds = {
        "claude_cli": "cli",
        "anthropic_api": "http",
        "ollama": "http",
        "recursive_light": "http",
    }
    for name, expected_kind in expected_kinds.items():
        profile = bridged_registry.get(name)
        assert profile is not None, f"{name!r} missing from bridge."
        assert profile.kind == expected_kind, (
            f"Native bridge kind drift: {name!r} has kind={profile.kind!r} "
            f"but baton backend_pool expects {expected_kind!r}. "
            f"{DOCTRINE_RULE}"
        )


def test_bridge_profiles_have_required_kind_specific_config(
    bridged_registry: InstrumentRegistry,
) -> None:
    """CLI profiles must carry a ``cli:`` section, HTTP profiles an ``http:``."""
    for profile in bridged_registry.list_all():
        if profile.kind == "cli":
            assert profile.cli is not None, (
                f"{profile.name!r} has kind=cli but no cli section. "
                f"{DOCTRINE_RULE}"
            )
        elif profile.kind == "http":
            assert profile.http is not None, (
                f"{profile.name!r} has kind=http but no http section. "
                f"{DOCTRINE_RULE}"
            )


# --- Silent-fallback prevention ---------------------------------------------


def test_unregistered_name_returns_none_not_silent_claude_cli_fallback(
    bridged_registry: InstrumentRegistry,
) -> None:
    """Unregistered names must return None, NOT silently map to claude_cli.

    Silent fallback to claude_cli would hide misconfigured scores — this
    was the failure mode the factory used to exhibit before the atlas.
    The doctrine forbids regressing to silent fallbacks.
    """
    result = bridged_registry.get("does-not-exist")
    assert result is None, (
        f"Unregistered name silently resolved to {result!r}. This would "
        f"hide typos and misconfigurations. "
        f"{DOCTRINE_RULE}"
    )


def test_empty_registry_without_bridge_does_not_resolve_legacy_names() -> None:
    """Without the bridge, legacy names must not resolve magically.

    If a future change adds lazy-registration or default-injection,
    this test catches it. The bridge must be an explicit call.
    """
    registry = InstrumentRegistry()
    for name in LEGACY_NAMES:
        assert registry.get(name) is None, (
            f"{name!r} resolved from an unbridged registry — someone added "
            f"implicit registration. "
            f"{DOCTRINE_RULE}"
        )


# --- Idempotency contract ---------------------------------------------------


def test_bridge_is_not_idempotent_today_duplicate_call_raises(
    bridged_registry: InstrumentRegistry,
) -> None:
    """Calling ``register_native_instruments`` twice must fail loudly.

    Current contract: duplicate registration raises ValueError. This
    pins the behavior so accidental re-registration during startup is
    caught, not swallowed. If the doctrine later switches to
    idempotent-by-default, update this test AND the xfail below.
    """
    with pytest.raises(ValueError, match="already registered"):
        register_native_instruments(bridged_registry)


@pytest.mark.xfail(
    reason=(
        "Blocked by doctrine evolution: assignment lists "
        "'safe to call twice without duplicate registrations' as a "
        "desired behavior, but current implementation raises on the "
        "second call. Flip to idempotent-by-default (override=True in "
        "register_native_instruments) or ship an explicit "
        "is_registered() guard. Atlas Doctrine — RULE: The transition "
        "bridge must have test coverage before Phase B begins."
    ),
    strict=True,
)
def test_bridge_is_idempotent_aspiration() -> None:
    """Future: calling register_native_instruments twice should be safe.

    Marked xfail per the stage rules — the assignment explicitly lists
    idempotency as a desired behavior, but the current implementation
    does not support it. When Phase B lands the change, remove the
    xfail marker; the test body is already correct.
    """
    registry = InstrumentRegistry()
    register_native_instruments(registry)
    register_native_instruments(registry)  # Should not raise.
    assert len(registry) == len(LEGACY_NAMES)


# --- Score-level fallback round-trip ----------------------------------------


def test_score_instrument_fallbacks_round_trip_through_bridge(
    bridged_registry: InstrumentRegistry,
) -> None:
    """A score declaring ``instrument_fallbacks: [anthropic_api]`` must resolve.

    This is the 25+ example scripts' contract. Parse a realistic score
    config, iterate its declared fallbacks, and confirm each resolves.
    The bridge is what makes this work during the transition — without
    it, fallback resolution returns None and the baton crashes at
    dispatch.
    """
    from marianne.core.config.job import JobConfig

    yaml_like: dict = {
        "name": "bridge-smoke",
        "description": "Exercise fallback resolution via the bridge.",
        "instrument": "claude_cli",
        "instrument_fallbacks": ["anthropic_api", "ollama", "recursive_light"],
        "sheet": {
            "size": 1,
            "total_items": 1,
        },
        "prompt": {
            "template": "echo hi",
        },
    }
    config = JobConfig.model_validate(yaml_like)

    # Every declared fallback (plus the primary) must resolve.
    for declared in [config.instrument, *config.instrument_fallbacks]:
        assert declared is not None
        profile = bridged_registry.get(declared)
        assert profile is not None, (
            f"Declared score instrument/fallback {declared!r} did not "
            f"resolve through the native bridge. "
            f"{DOCTRINE_RULE}"
        )
        assert profile.name == declared


def test_bridge_profiles_are_not_mutated_between_lookups(
    bridged_registry: InstrumentRegistry,
) -> None:
    """The registry must return a stable profile object across lookups.

    Callers (baton, CLI, dashboard, cost tracking) compare by identity
    when caching. If the registry ever switched to returning a fresh
    copy each time, it would silently break those caches.
    """
    first = bridged_registry.get("anthropic_api")
    second = bridged_registry.get("anthropic_api")
    assert first is not None and second is not None
    assert first is second, (
        "Registry returned different profile instances for the same name. "
        "Cache-by-identity consumers would break. "
        f"{DOCTRINE_RULE}"
    )


# --- Capability / model metadata the registry consumers depend on -----------


def test_bridge_anthropic_api_exposes_anthropic_schema_family(
    bridged_registry: InstrumentRegistry,
) -> None:
    """Phase 3 HTTP dispatch routes on ``http.schema_family``.

    If the bridge ever changes the Anthropic profile's schema family,
    generic HTTP dispatch routes it to the wrong translation layer.
    Pin the value.
    """
    profile = bridged_registry.get("anthropic_api")
    assert profile is not None
    assert profile.http is not None
    assert profile.http.schema_family == "anthropic", (
        "Anthropic native bridge schema_family changed — Phase 3 HTTP "
        "dispatch will route through the wrong translation layer. "
        f"{DOCTRINE_RULE}"
    )


def test_bridge_claude_cli_exposes_executable_for_plugin_cli_backend(
    bridged_registry: InstrumentRegistry,
) -> None:
    """PluginCliBackend reads ``profile.cli.command.executable`` to spawn.

    If the bridge loses the executable, Claude CLI jobs silently fail
    with "Command not found". Pin the value.
    """
    profile = bridged_registry.get("claude_cli")
    assert profile is not None
    assert profile.cli is not None
    assert profile.cli.command.executable == "claude", (
        "Claude CLI native bridge executable changed — PluginCliBackend "
        "will fail to spawn subprocesses. "
        f"{DOCTRINE_RULE}"
    )


def test_bridge_claude_cli_lists_at_least_one_model(
    bridged_registry: InstrumentRegistry,
) -> None:
    """Token budget and cost tracking need at least one ModelCapacity.

    If the bridge loses all models, cost estimation falls back to the
    hardcoded Claude Sonnet rates (doctrine RULE: cost tracking must use
    instrument profile pricing), which is exactly the behavior Phase 5
    is trying to eliminate.
    """
    profile = bridged_registry.get("claude_cli")
    assert profile is not None
    assert len(profile.models) >= 1, (
        "Claude CLI bridge lost its ModelCapacity entries — cost tracking "
        "will fall through to hardcoded Sonnet pricing. "
        f"{DOCTRINE_RULE}"
    )
