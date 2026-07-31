"""Contracts for profile-driven HTTP instrument dispatch.

OpenAI-compatible profiles share one generic transport. Other wire schemas
are rejected with an actionable error until a schema codec exists; they do not
gain provider-specific Python backend classes. Source-level guards pin that
dispatch contains neither provider-name special cases nor stub exceptions.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from marianne.core.config.instruments import (
    HttpProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.daemon.baton import backend_pool as backend_pool_module
from marianne.daemon.baton.backend_pool import (
    BackendPool,
    _create_backend_for_profile,
)
from marianne.instruments.registry import InstrumentRegistry

# ---------------------------------------------------------------------------
# Helpers — real profiles, no mocks for the module under test.
# ---------------------------------------------------------------------------


GENERIC_HTTP_RULE = "OpenAI-compatible profiles use one profile-driven transport."


def _make_http_profile(
    name: str,
    *,
    base_url: str,
    schema_family: str,
    endpoint: str = "/v1/chat/completions",
    auth_env_var: str | None = None,
) -> InstrumentProfile:
    """Build a minimal HTTP InstrumentProfile using real Pydantic models.

    No mocking of the config layer — integration test posture. External
    service endpoints are pointed at non-routable / local URLs so that
    nothing actually exercises the network.
    """
    return InstrumentProfile(
        name=name,
        display_name=f"Test HTTP ({name})",
        kind="http",
        capabilities={"structured_output"},
        models=[
            ModelCapacity(
                name=f"{name}-model",
                context_window=32_000,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            ),
        ],
        default_model=f"{name}-model",
        http=HttpProfile(
            base_url=base_url,
            endpoint=endpoint,
            schema_family=schema_family,  # type: ignore[arg-type]
            auth_env_var=auth_env_var,
        ),
    )


def _registry_with(*profiles: InstrumentProfile) -> InstrumentRegistry:
    registry = InstrumentRegistry()
    for p in profiles:
        registry.register(p)
    return registry


# ---------------------------------------------------------------------------
# A named provider profile is ordinary OpenAI-compatible configuration.
# ---------------------------------------------------------------------------


def test_openrouter_http_profile_still_routes_to_backend() -> None:
    """An OpenRouter profile routes through the generic HTTP contract."""
    profile = _make_http_profile(
        "openrouter",
        base_url="https://openrouter.ai/api/v1",
        schema_family="openai",
        auth_env_var="OPENROUTER_API_KEY",
    )

    backend = _create_backend_for_profile(profile)

    assert backend is not None, (
        "OpenRouter HTTP profile failed to resolve to a backend. "
        f"Regression guard for: {GENERIC_HTTP_RULE}"
    )


# ---------------------------------------------------------------------------
# Generic dispatch is selected by schema, not provider name.
# ---------------------------------------------------------------------------


def test_openai_family_non_openrouter_profile_acquires_backend() -> None:
    """Any OpenAI-compatible profile yields the generic backend."""
    profile = _make_http_profile(
        "openai-compat",
        base_url="http://localhost:9001/v1",
        schema_family="openai",
        auth_env_var="FAKE_OPENAI_API_KEY",
    )

    backend = _create_backend_for_profile(profile)

    assert backend is not None, (
        "OpenAI-family (non-OpenRouter) HTTP dispatch missing. "
        f"Contract: {GENERIC_HTTP_RULE}"
    )


@pytest.mark.parametrize("schema_family", ["anthropic", "gemini"])
def test_non_openai_wire_schema_is_rejected_by_profile(
    schema_family: str,
) -> None:
    """Profiles cannot advertise a wire contract the runtime cannot execute."""
    with pytest.raises(ValidationError, match="schema_family"):
        _make_http_profile(
            f"{schema_family}-http",
            base_url="https://example.invalid",
            schema_family=schema_family,
        )


# ---------------------------------------------------------------------------
# Source-level invariants keep generic dispatch free of provider branches.
# ---------------------------------------------------------------------------


def _backend_pool_source() -> str:
    """Load backend_pool.py source once for the invariants below."""
    path = Path(inspect.getfile(backend_pool_module))
    return path.read_text(encoding="utf-8")


def test_backend_pool_has_no_hardcoded_openrouter_name_check() -> None:
    """Generic dispatch must not branch on the OpenRouter profile name."""
    source = _backend_pool_source()
    assert 'profile.name == "openrouter"' not in source, (
        "backend_pool.py still contains hardcoded OpenRouter name check. "
        f"Generic dispatch violated. Rule: {GENERIC_HTTP_RULE}"
    )


def test_backend_pool_has_no_notimplementederror_for_http_dispatch() -> None:
    """HTTP dispatch must use domain errors, not stub exceptions."""
    source = _backend_pool_source()
    assert "raise NotImplementedError" not in source, (
        "backend_pool.py still raises NotImplementedError for HTTP "
        "dispatch. "
        f"Rule: {GENERIC_HTTP_RULE}"
    )


# ---------------------------------------------------------------------------
# Pool-level integration exercises the path called by the baton.
# ---------------------------------------------------------------------------


async def test_pool_acquire_end_to_end_for_openai_family_profile() -> None:
    """End-to-end integration: pool.acquire() must work for generic HTTP.

    This is the shape the baton actually calls — ``await pool.acquire(
    instrument_name)``. It validates that the wiring through
    ``_acquire_locked`` → ``_create_backend_for_profile`` all succeeds
    for a non-OpenRouter OpenAI-compatible profile.
    """
    profile = _make_http_profile(
        "generic-openai",
        base_url="http://localhost:9002/v1",
        schema_family="openai",
        auth_env_var="FAKE_OPENAI_API_KEY",
    )
    registry = _registry_with(profile)
    pool = BackendPool(registry)

    try:
        backend = await pool.acquire(profile.name)
        assert backend is not None, (
            f"Pool returned None backend for '{profile.name}'. "
            f"Contract: {GENERIC_HTTP_RULE}"
        )
        # HTTP is singleton — a second acquire should reuse the same
        # instance and not explode.
        backend2 = await pool.acquire(profile.name)
        assert backend2 is backend, (
            "HTTP backends must be singletons per instrument. "
            f"Contract: {GENERIC_HTTP_RULE}"
        )
    finally:
        await pool.close_all()
