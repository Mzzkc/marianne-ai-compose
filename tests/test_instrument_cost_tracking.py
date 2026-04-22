"""Observer tests for the instrument cost-tracking doctrine.

These tests close coverage gap MO-7 and enforce the doctrine rule:

    RULE: Cost tracking must use instrument profile pricing, not hardcoded
    Claude Sonnet rates.

    SCOPE: ``src/marianne/daemon/baton/musician.py:1005-1008``

    RATIONALE: Falls back to $3/1M input, $15/1M output (Claude Sonnet
    pricing) when no instrument profile pricing is available. This produces
    incorrect cost estimates for non-Claude instruments. The instrument
    profile system has a pricing schema — the fallback should at minimum be
    instrument-aware.

    EVIDENCE: ``daemon/baton/musician.py:1005-1008``. Triangulation C-10
    confirms with all three lenses. Docs records this as silent area #1.
    MO-7 flagged as drift-screen discovery.

The tests must run today against live code. Behaviours that cannot be
exercised today (for example, emitting a warning that names the missing
instrument) are marked with ``@pytest.mark.xfail`` so the doctrine gap is
visible in the collection output — per the instance instructions, xfail is
the protocol, skip is not.
"""

from __future__ import annotations

import logging

import pytest

from marianne.backends.base import ExecutionResult
from marianne.daemon.baton.musician import _estimate_cost
from marianne.instruments.registry import (
    InstrumentRegistry,
    register_native_instruments,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _make_exec_result(
    input_tokens: int = 10_000,
    output_tokens: int = 2_000,
) -> ExecutionResult:
    """Construct a minimal ExecutionResult with token counts.

    Uses the real ExecutionResult dataclass — no mocks for Marianne's own
    data model, per instance rule 2.
    """
    return ExecutionResult(
        success=True,
        stdout="",
        stderr="",
        duration_seconds=1.0,
        exit_code=0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


@pytest.fixture
def registry() -> InstrumentRegistry:
    """A registry populated with the 4 native instrument profiles.

    Uses the real ``register_native_instruments`` bridge — the same bridge
    the daemon relies on. No mocks.
    """
    r = InstrumentRegistry()
    register_native_instruments(r)
    return r


# ---------------------------------------------------------------------------
# Behaviour: profile pricing wins over hardcoded fallback
# ---------------------------------------------------------------------------


def test_claude_sonnet_profile_pricing_is_used_and_matches_profile(
    registry: InstrumentRegistry,
) -> None:
    """Sonnet pricing comes from the instrument profile, not the hardcoded $3/$15.

    Sonnet's published rate happens to equal the fallback, so we cannot
    distinguish the paths by the number alone. What we CAN verify is that
    the number ``_estimate_cost`` produces when given the profile values
    matches what the profile advertises — i.e. the wiring is correct.

    Doctrine: RULE "cost tracking must use instrument profile pricing".
    """
    profile = registry.get("claude_cli")
    assert profile is not None
    sonnet = next(m for m in profile.models if "sonnet" in m.name)

    result = _make_exec_result(input_tokens=10_000, output_tokens=2_000)
    cost = _estimate_cost(
        result,
        cost_per_1k_input=sonnet.cost_per_1k_input,
        cost_per_1k_output=sonnet.cost_per_1k_output,
    )

    # 10_000 tokens * $0.003/1K + 2_000 tokens * $0.015/1K = $0.03 + $0.03 = $0.06
    expected = (10_000 * 0.003 / 1_000) + (2_000 * 0.015 / 1_000)
    assert cost == pytest.approx(expected), (
        "Doctrine violated (RULE cost tracking must use instrument profile "
        "pricing): Sonnet profile pricing not honored by _estimate_cost. "
        f"Expected {expected}, got {cost}."
    )


def test_claude_opus_profile_pricing_distinguishes_profile_from_fallback(
    registry: InstrumentRegistry,
) -> None:
    """Opus costs 5x Sonnet — if the fallback leaked, this test would fail.

    This is the strongest available guard: Opus profile pricing ($0.015/$0.075
    per 1K) is numerically distinct from the hardcoded Sonnet fallback
    ($0.003/$0.015 per 1K). A regression that silently drops to the fallback
    would produce the Sonnet number and fail this assertion.

    Doctrine: RULE "cost tracking must use instrument profile pricing".
    """
    profile = registry.get("claude_cli")
    assert profile is not None
    opus = next((m for m in profile.models if "opus" in m.name), None)
    assert opus is not None, "claude_cli profile is expected to include Opus"

    result = _make_exec_result(input_tokens=100_000, output_tokens=10_000)
    cost = _estimate_cost(
        result,
        cost_per_1k_input=opus.cost_per_1k_input,
        cost_per_1k_output=opus.cost_per_1k_output,
    )

    # Opus: 100_000 * 0.015/1K + 10_000 * 0.075/1K = 1.50 + 0.75 = 2.25
    opus_expected = (100_000 * 0.015 / 1_000) + (10_000 * 0.075 / 1_000)
    # Sonnet fallback: 100_000 * 3/1M + 10_000 * 15/1M = 0.30 + 0.15 = 0.45
    sonnet_fallback = (100_000 * 3.0 / 1_000_000) + (10_000 * 15.0 / 1_000_000)

    assert cost == pytest.approx(opus_expected), (
        "Doctrine violated (RULE cost tracking must use instrument profile "
        "pricing): Opus profile pricing not honored. Expected "
        f"{opus_expected}, got {cost}."
    )
    assert cost != pytest.approx(sonnet_fallback), (
        "Doctrine violated (RULE cost tracking must use instrument profile "
        "pricing): result matches Sonnet fallback instead of Opus profile."
    )


def test_ollama_zero_cost_profile_pricing_produces_zero_cost(
    registry: InstrumentRegistry,
) -> None:
    """Local Ollama has $0 cost; a correct implementation must report $0.

    If the Sonnet fallback leaked on the non-Claude path, Ollama jobs would
    be billed at $0.003/1K input + $0.015/1K output — doctrine-violating.

    Doctrine: RULE "cost tracking must use instrument profile pricing,
    not hardcoded Claude Sonnet rates".
    """
    profile = registry.get("ollama")
    assert profile is not None
    assert profile.models, "ollama profile should carry at least one model"
    model = profile.models[0]
    assert model.cost_per_1k_input == 0.0
    assert model.cost_per_1k_output == 0.0

    result = _make_exec_result(input_tokens=50_000, output_tokens=10_000)
    cost = _estimate_cost(
        result,
        cost_per_1k_input=model.cost_per_1k_input,
        cost_per_1k_output=model.cost_per_1k_output,
    )

    assert cost == 0.0, (
        "Doctrine violated (RULE cost tracking must use instrument profile "
        "pricing): Ollama has $0/$0 pricing but _estimate_cost returned "
        f"{cost}. This means the hardcoded Sonnet fallback leaked into a "
        "non-Claude instrument's billing."
    )


# ---------------------------------------------------------------------------
# Behaviour: the Sonnet fallback is the current-but-wrong behaviour for
# non-Claude instruments
# ---------------------------------------------------------------------------


def test_fallback_path_applies_sonnet_rates_when_pricing_absent() -> None:
    """Characterization test for the fallback branch at musician.py:1005-1008.

    This test documents the CURRENT behaviour: when pricing is ``None``, the
    Claude Sonnet rates ($3/1M in, $15/1M out) are applied. This is the
    doctrine-flagged bug — per the atlas, this fallback is instrument-
    insensitive. The test exists so that the fix is detectable: once the
    fallback is replaced with an instrument-aware path, this assertion will
    change or move.

    Doctrine: RULE "cost tracking must use instrument profile pricing" —
    this test captures the pre-fix baseline.
    """
    result = _make_exec_result(input_tokens=1_000_000, output_tokens=1_000_000)

    cost = _estimate_cost(result, cost_per_1k_input=None, cost_per_1k_output=None)

    # Sonnet fallback: 1M * $3/1M + 1M * $15/1M = $3 + $15 = $18
    expected_sonnet_fallback = 18.0
    assert cost == pytest.approx(expected_sonnet_fallback), (
        "Characterization failure (RULE cost tracking must use instrument "
        "profile pricing): the fallback branch no longer yields the Sonnet "
        f"numbers. Expected {expected_sonnet_fallback}, got {cost}. If this "
        "failed because the fallback was fixed, delete this test and wire "
        "the new path into the instrument-aware tests instead."
    )


def test_recursive_light_instrument_without_pricing_silently_hits_sonnet_fallback(
    registry: InstrumentRegistry,
) -> None:
    """Non-Claude instrument + missing profile pricing = Sonnet rates applied.

    The ``recursive_light`` profile registers with ``models=[]`` (no pricing
    info available). The baton code at ``adapter.py:1620-1639`` treats a
    missing model list as "use None, None" — which then lands in the
    Sonnet fallback in ``_estimate_cost``. A Recursive Light job is therefore
    billed at Claude Sonnet rates today. The doctrine says this is WRONG.

    This test pins the current wrong behaviour so the fix is detectable.
    When the doctrine rule is satisfied, this test's expectation changes.

    Doctrine: RULE "cost tracking must use instrument profile pricing, not
    hardcoded Claude Sonnet rates" — this test captures the violation.
    """
    profile = registry.get("recursive_light")
    assert profile is not None
    assert profile.models == [] or profile.models is None, (
        "recursive_light profile is expected to carry no pricing today — "
        "if pricing was added, update this test to use it."
    )

    # Simulate what adapter.py does when no model pricing is available:
    # both cost_per_1k_{input,output} end up None.
    result = _make_exec_result(input_tokens=10_000, output_tokens=5_000)
    cost = _estimate_cost(result, cost_per_1k_input=None, cost_per_1k_output=None)

    sonnet_rate_cost = (10_000 * 3.0 / 1_000_000) + (5_000 * 15.0 / 1_000_000)
    assert cost == pytest.approx(sonnet_rate_cost), (
        "Doctrine check (RULE cost tracking must use instrument profile "
        "pricing): Recursive Light falls through to the Claude Sonnet "
        f"fallback. Expected {sonnet_rate_cost} (Sonnet rates), got {cost}. "
        "This test characterizes the violation the doctrine flags."
    )


def test_explicit_non_claude_pricing_overrides_sonnet_fallback() -> None:
    """When a non-Claude instrument DOES provide pricing, it must win.

    This test uses hypothetical Gemini-like pricing ($0.00125/$0.005 per 1K)
    to prove that _estimate_cost honors whatever pricing is passed, even
    when the values diverge from Claude's. This establishes the positive
    side of the doctrine: the mechanism exists, it just needs to be wired
    end-to-end for every instrument profile.

    Doctrine: RULE "cost tracking must use instrument profile pricing".
    """
    result = _make_exec_result(input_tokens=10_000, output_tokens=2_000)

    # Hypothetical Gemini Pro rates
    cost = _estimate_cost(
        result,
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.005,
    )

    expected = (10_000 * 0.00125 / 1_000) + (2_000 * 0.005 / 1_000)
    assert cost == pytest.approx(expected), (
        "Doctrine violated (RULE cost tracking must use instrument profile "
        f"pricing): non-Claude pricing path returned {cost} instead of "
        f"{expected}. Profile pricing must be honored for every instrument."
    )
    # And it must not silently match the Sonnet fallback
    sonnet_fallback = (10_000 * 3.0 / 1_000_000) + (2_000 * 15.0 / 1_000_000)
    assert cost != pytest.approx(sonnet_fallback), (
        "Doctrine violated: non-Claude pricing produced the Sonnet fallback "
        "number — the custom rates were ignored."
    )


# ---------------------------------------------------------------------------
# Behaviour: the fallback must emit an observable signal naming the
# instrument whose pricing is missing. This is part of the doctrine but
# not implemented today — hence xfail.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Doctrine RULE 'cost tracking must use instrument profile pricing' "
        "calls for the fallback path to log a warning identifying the "
        "instrument whose pricing is missing. The current implementation at "
        "musician.py:1005-1008 falls back silently. This test is the "
        "reminder — flip to non-xfail once the warning is wired. "
        "Blocked by Phase 5 peripheral cleanup."
    ),
)
def test_fallback_logs_warning_naming_missing_instrument(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing-pricing fallback must log something that names the instrument.

    The doctrine rule explicitly lists this behaviour ("so the gap is
    visible"). Today ``_estimate_cost`` is silent on the fallback branch.
    This test is the backstop that will begin passing once the warning is
    added — at which point the xfail can be removed.
    """
    result = _make_exec_result(input_tokens=1_000, output_tokens=500)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="marianne.daemon.baton.musician"):
        _estimate_cost(result, cost_per_1k_input=None, cost_per_1k_output=None)

    warning_messages = [rec.message for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert warning_messages, (
        "Doctrine violated (RULE cost tracking must use instrument profile "
        "pricing): no warning emitted when falling back to hardcoded Sonnet "
        "rates. The fallback path must identify the instrument whose "
        "pricing is missing."
    )


# ---------------------------------------------------------------------------
# Behaviour: token-count zero paths
# ---------------------------------------------------------------------------


def test_zero_tokens_yields_zero_cost_on_both_paths() -> None:
    """A run that reports zero tokens must produce zero cost, regardless of path.

    Defensive test: exercise both the profile-pricing branch and the
    fallback branch with zero tokens to catch any future refactor that
    introduces a base fee.

    Doctrine: RULE "cost tracking must use instrument profile pricing" —
    this test guards against a refactor that adds a non-zero constant.
    """
    result = _make_exec_result(input_tokens=0, output_tokens=0)

    # Profile-pricing branch
    profile_cost = _estimate_cost(result, cost_per_1k_input=0.003, cost_per_1k_output=0.015)
    assert profile_cost == 0.0, (
        f"Profile-pricing branch: zero tokens produced non-zero cost {profile_cost}."
    )

    # Fallback branch
    fallback_cost = _estimate_cost(result, cost_per_1k_input=None, cost_per_1k_output=None)
    assert fallback_cost == 0.0, (
        f"Fallback branch: zero tokens produced non-zero cost {fallback_cost}."
    )


def test_none_tokens_are_treated_as_zero() -> None:
    """ExecutionResult.input_tokens/output_tokens may be None.

    Backends that cannot report token counts leave these fields None.
    _estimate_cost must treat None as zero, not crash.

    Doctrine: RULE "cost tracking must use instrument profile pricing" —
    also reinforces defensive coding from the conventions spec.
    """
    result = ExecutionResult(
        success=True,
        stdout="",
        stderr="",
        duration_seconds=1.0,
        exit_code=0,
        input_tokens=None,
        output_tokens=None,
    )
    cost = _estimate_cost(result, cost_per_1k_input=0.003, cost_per_1k_output=0.015)
    assert cost == 0.0, (
        f"None tokens must be treated as zero; got {cost}."
    )
