"""Observer test — generic HTTP instrument dispatch (Phase 3 enforcement).

This file is an OBSERVER test written before the Phase 3 migration begins.
Its job is not to test current behavior; its job is to lock in the
Doctrine Rule that generic HTTP dispatch must work for every
``HttpProfile`` schema family after Phase 3 lands. When Phase 3 ships,
the xfail-marked tests must flip to passing — that is the signal that
the doctrine rule has been honoured.

Coverage gap addressed: G-1 (Critical)
Target module: ``src/marianne/daemon/baton/backend_pool.py:78-123``

Doctrine rule enforced
----------------------
RULE: Generic HTTP instrument dispatch must work for all HttpProfile
schema families.

SCOPE: ``daemon/baton/backend_pool.py:78-123``

RATIONALE: The current implementation only supports OpenRouter via a
hardcoded name check at ``:90`` and raises ``NotImplementedError`` for
all other HTTP instruments at ``:118``. The ``HttpProfile`` schema family
field (``Literal["openai", "anthropic", "gemini"]``) at
``core/config/instruments.py:261`` was designed to drive generic routing
— implementation was deferred. Phase 3 must deliver this.

EVIDENCE:
- ``daemon/baton/backend_pool.py:90-116`` (OpenRouter hardcoded)
- ``daemon/baton/backend_pool.py:118-123`` (NotImplementedError)
- ``core/config/instruments.py:396`` (schema family literal)
- Triangulation C-3 confirms.

Audit hooks enforced by this file
---------------------------------
- AUDIT-INV-3: backend_pool.py has no ``NotImplementedError`` for HTTP dispatch
- AUDIT-INV-4: the hardcoded ``profile.name == "openrouter"`` check is gone

Why xfail and not skip
----------------------
Per the observer-test authoring rules: a test that cannot be expressed
today because the feature is not wired must be marked ``xfail``, not
``skip``. Xfail is visible in ``pytest`` reporting — when the
implementation lands and the test starts passing, ``pytest`` will raise
``XPASS`` and force the author to remove the xfail marker. Skip would
silently drop the test from the run and the doctrine rule would decay.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

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


DOCTRINE_RULE = (
    "Generic HTTP instrument dispatch must work for all HttpProfile schema "
    "families (doctrine RULE: 'Generic HTTP instrument dispatch must work for "
    "all HttpProfile schema families', audit hooks AUDIT-INV-3 / AUDIT-INV-4). "
    "See `docs/plans/` Atlas Doctrine and `docs/research/"
    "2026-03-26-universal-instrument-api-research.md`."
)


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
# 1. Regression guard — the currently-working OpenRouter path must not
#    regress while Phase 3 rewires the dispatch. This test should PASS
#    today and continue to pass after Phase 3.
# ---------------------------------------------------------------------------


def test_openrouter_http_profile_still_routes_to_backend() -> None:
    """OpenRouter profile must acquire *some* HTTP backend, pre- and post-Phase-3.

    The currently-shipping code creates ``OpenRouterBackend`` via a hardcoded
    name check. Phase 3 replaces that with generic ``HttpProfile``-based
    routing. Either way, the pool must return a non-None Backend. This
    test asserts only the invariant — no coupling to the concrete class —
    so it survives the migration and acts as a regression guard.
    """
    profile = _make_http_profile(
        "openrouter",
        base_url="https://openrouter.ai/api/v1",
        schema_family="openai",
        auth_env_var="OPENROUTER_API_KEY",
    )

    backend = _create_backend_for_profile(profile)

    assert backend is not None, (
        "OpenRouter HTTP profile failed to resolve to a backend. "
        f"Regression guard for: {DOCTRINE_RULE}"
    )


# ---------------------------------------------------------------------------
# 2. Schema-family dispatch — one test per HttpProfile schema family.
#    Each asserts that a non-OpenRouter profile in that family produces a
#    working backend. All three xfail today (NotImplementedError is raised
#    at backend_pool.py:118-123). When Phase 3 lands, these flip.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "Blocked by Phase 3: generic HttpProfile dispatch for schema_family="
        "'openai' is not implemented. Today backend_pool.py:118-123 raises "
        "NotImplementedError for any HTTP profile that isn't OpenRouter. "
        "Doctrine RULE: 'Generic HTTP instrument dispatch must work for all "
        "HttpProfile schema families'."
    ),
    strict=True,
    raises=NotImplementedError,
)
def test_openai_family_non_openrouter_profile_acquires_backend() -> None:
    """OpenAI-family HTTP profile (not OpenRouter) must yield a backend.

    After Phase 3, a profile like a hypothetical ``openai-compat`` or
    ``groq`` — anything with ``schema_family='openai'`` — must route
    through generic dispatch. Today the pool raises NotImplementedError
    because only the OpenRouter name / host is recognised.
    """
    profile = _make_http_profile(
        "openai-compat",
        base_url="http://localhost:9001/v1",
        schema_family="openai",
        auth_env_var="FAKE_OPENAI_API_KEY",
    )

    backend = _create_backend_for_profile(profile)

    assert backend is not None, (
        "OpenAI-family (non-OpenRouter) HTTP dispatch missing. "
        f"Doctrine: {DOCTRINE_RULE}"
    )


@pytest.mark.xfail(
    reason=(
        "Blocked by Phase 3: generic HttpProfile dispatch for schema_family="
        "'anthropic' is not implemented. The native AnthropicApiBackend "
        "handles this today via the legacy factory path, not via "
        "backend_pool._create_backend_for_profile. Doctrine RULE: 'Generic "
        "HTTP instrument dispatch must work for all HttpProfile schema "
        "families'."
    ),
    strict=True,
    raises=NotImplementedError,
)
def test_anthropic_family_http_profile_acquires_backend() -> None:
    """Anthropic-family HTTP profile must yield a backend through the pool.

    The native ``anthropic_api`` profile has ``schema_family='anthropic'``
    and is registered by ``register_native_instruments()``. After Phase 3
    it should also resolve via ``_create_backend_for_profile`` rather than
    requiring a separate SDK-specific code path.
    """
    profile = _make_http_profile(
        "anthropic-http",
        base_url="https://api.anthropic.com",
        endpoint="/v1/messages",
        schema_family="anthropic",
        auth_env_var="ANTHROPIC_API_KEY",
    )

    backend = _create_backend_for_profile(profile)

    assert backend is not None, (
        "Anthropic-family HTTP dispatch missing. "
        f"Doctrine: {DOCTRINE_RULE}"
    )


@pytest.mark.xfail(
    reason=(
        "Blocked by Phase 3: generic HttpProfile dispatch for schema_family="
        "'gemini' is not implemented. No Gemini HTTP backend exists today. "
        "Doctrine RULE: 'Generic HTTP instrument dispatch must work for all "
        "HttpProfile schema families'."
    ),
    strict=True,
    raises=NotImplementedError,
)
def test_gemini_family_http_profile_acquires_backend() -> None:
    """Gemini-family HTTP profile must yield a backend after Phase 3.

    The ``HttpProfile.schema_family`` literal already enumerates
    ``'gemini'`` — the schema is designed for this. Phase 3 must deliver
    a translator that lets a profile with ``schema_family='gemini'``
    route through the generic HTTP path.
    """
    profile = _make_http_profile(
        "gemini-http",
        base_url="https://generativelanguage.googleapis.com",
        endpoint="/v1beta/models/gemini-2.5-pro:generateContent",
        schema_family="gemini",
        auth_env_var="GEMINI_API_KEY",
    )

    backend = _create_backend_for_profile(profile)

    assert backend is not None, (
        "Gemini-family HTTP dispatch missing. "
        f"Doctrine: {DOCTRINE_RULE}"
    )


# ---------------------------------------------------------------------------
# 3. Error shape — an unrecognised HTTP profile must fail with an actionable
#    error, NOT a bare NotImplementedError. The doctrine calls this out
#    specifically: the current error is exactly what Phase 3 is meant to
#    replace.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "Blocked by Phase 3: today backend_pool.py:118-123 raises a bare "
        "NotImplementedError. After Phase 3, an unrecognised / unwired HTTP "
        "profile must raise an actionable error that names the instrument "
        "and suggests a fix (register a profile, enable a family, etc.). "
        "Doctrine audit AUDIT-INV-3 requires the NotImplementedError branch "
        "to be gone."
    ),
    strict=True,
)
def test_unrecognised_http_profile_raises_actionable_error_not_notimplementederror() -> None:
    """Unknown HTTP schema/endpoint must surface a useful diagnostic.

    The doctrine is explicit: ``NotImplementedError`` is the smell this
    phase removes. After Phase 3, an unsupported HTTP instrument must
    raise a domain error (e.g. ``ValueError`` / a Marianne config error)
    whose message names the instrument and tells the user what to do.
    """
    profile = _make_http_profile(
        "totally-unknown",
        base_url="http://localhost:1/nope",
        schema_family="openai",  # arbitrary — family isn't the problem
    )

    # Today this raises NotImplementedError; Phase 3 should replace it
    # with a typed, actionable error. We assert on *shape* — whatever
    # the new error is, it must NOT be NotImplementedError.
    raised: BaseException | None = None
    try:
        _create_backend_for_profile(profile)
    except BaseException as exc:  # noqa: BLE001 — observer test, capture all
        raised = exc

    assert raised is not None, (
        "Unrecognised HTTP profile must raise *some* error — returning a "
        f"silent None is worse than NotImplementedError. Doctrine: {DOCTRINE_RULE}"
    )
    assert not isinstance(raised, NotImplementedError), (
        "Unrecognised HTTP profile raised NotImplementedError, violating "
        f"doctrine AUDIT-INV-3 and the rule: {DOCTRINE_RULE}. "
        f"Message was: {raised!s}"
    )
    message = str(raised)
    assert profile.name in message, (
        "Actionable error must name the instrument. "
        f"Got: {type(raised).__name__}: {message!r}. Doctrine: {DOCTRINE_RULE}"
    )


# ---------------------------------------------------------------------------
# 4. Source-level invariants — pin AUDIT-INV-3 and AUDIT-INV-4 as unit tests
#    so the static audit hooks become regression alarms rather than
#    developer-memory items.
# ---------------------------------------------------------------------------


def _backend_pool_source() -> str:
    """Load backend_pool.py source once for the invariants below."""
    path = Path(inspect.getfile(backend_pool_module))
    return path.read_text(encoding="utf-8")


@pytest.mark.xfail(
    reason=(
        "AUDIT-INV-4 (Atlas Doctrine): the hardcoded "
        "`profile.name == \"openrouter\"` branch at backend_pool.py:90-116 "
        "is still present. Phase 3 replaces it with generic HttpProfile "
        "dispatch driven by schema_family."
    ),
    strict=True,
)
def test_backend_pool_has_no_hardcoded_openrouter_name_check() -> None:
    """Source-level guard for AUDIT-INV-4.

    Phase 3 must remove the hardcoded name check. The doctrine hook
    greps for ``profile.name == "openrouter"``; we mirror it here so the
    check runs under ``pytest`` as well as CI grep.
    """
    source = _backend_pool_source()
    assert 'profile.name == "openrouter"' not in source, (
        "backend_pool.py still contains hardcoded OpenRouter name check. "
        f"Doctrine AUDIT-INV-4 violated. Rule: {DOCTRINE_RULE}"
    )


@pytest.mark.xfail(
    reason=(
        "AUDIT-INV-3 (Atlas Doctrine): backend_pool.py:118-123 still raises "
        "NotImplementedError for unsupported HTTP instruments. Phase 3 "
        "replaces this with generic dispatch plus actionable errors."
    ),
    strict=True,
)
def test_backend_pool_has_no_notimplementederror_for_http_dispatch() -> None:
    """Source-level guard for AUDIT-INV-3.

    The doctrine requires that the NotImplementedError branch be removed
    once generic HTTP dispatch is wired. Search the module text for the
    string ``NotImplementedError`` — a hit means the branch is still
    there.
    """
    source = _backend_pool_source()
    assert "NotImplementedError" not in source, (
        "backend_pool.py still raises/mentions NotImplementedError for "
        "HTTP dispatch. Doctrine AUDIT-INV-3 violated. "
        f"Rule: {DOCTRINE_RULE}"
    )


# ---------------------------------------------------------------------------
# 5. Pool-level integration — exercise the live `BackendPool.acquire` code
#    path (not just the private factory). Phase 3 must make this work for
#    every schema family registered in the registry.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "Blocked by Phase 3: BackendPool.acquire() surfaces the same "
        "NotImplementedError for non-OpenRouter HTTP profiles because "
        "_acquire_locked delegates to _create_backend_for_profile. "
        "Doctrine RULE: 'Generic HTTP instrument dispatch must work for "
        "all HttpProfile schema families'."
    ),
    strict=True,
    raises=NotImplementedError,
)
async def test_pool_acquire_end_to_end_for_openai_family_profile() -> None:
    """End-to-end integration: pool.acquire() must work for generic HTTP.

    This is the shape the baton actually calls — ``await pool.acquire(
    instrument_name)``. It validates that the wiring through
    ``_acquire_locked`` → ``_create_backend_for_profile`` all succeeds
    for a non-OpenRouter HTTP profile after Phase 3.
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
            f"Doctrine: {DOCTRINE_RULE}"
        )
        # HTTP is singleton — a second acquire should reuse the same
        # instance and not explode.
        backend2 = await pool.acquire(profile.name)
        assert backend2 is backend, (
            "HTTP backends must be singletons per instrument. "
            f"Doctrine: {DOCTRINE_RULE}"
        )
    finally:
        await pool.close_all()
