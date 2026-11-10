"""A sheet is priced at the model it ran, and cache traffic at the cache rate (GH #522).

Before this, the adapter priced every sheet at the profile's default model's
row (a claude-code seat pinned to Sonnet on a GLM-default host read as $0; a
codex-cli seat pinned to gpt-6-astra was billed as gpt-6-sol), and the
estimate charged prompt-cache reads at the input rate, about 5x the bill on
an 88%-cache agentic sheet.
"""

from __future__ import annotations

from marianne.core.config.instruments import ModelCapacity
from marianne.daemon.baton.adapter import _price_row_for
from marianne.daemon.baton.musician import _estimate_cost
from marianne.execution.base import ExecutionResult


def _row(name: str, inp: float, out: float, **kw: float | None) -> ModelCapacity:
    return ModelCapacity(
        name=name,
        context_window=200_000,
        cost_per_1k_input=inp,
        cost_per_1k_output=out,
        cost_per_1k_cache_read=kw.get("cost_per_1k_cache_read"),
        cost_per_1k_cache_write=kw.get("cost_per_1k_cache_write"),
    )


ROWS = [
    _row("glm-5.3[1m]", 0.0, 0.0),
    _row(
        "claude-sonnet-5", 0.002, 0.01,
        cost_per_1k_cache_read=0.0002, cost_per_1k_cache_write=0.0025,
    ),
    _row("claude-opus-5", 0.005, 0.025),
]


def test_pinned_model_is_priced_at_its_own_row() -> None:
    row = _price_row_for(ROWS, "claude-sonnet-5", "glm-5.3[1m]")
    assert row.name == "claude-sonnet-5"


def test_unpinned_seat_is_priced_at_the_default_row() -> None:
    assert _price_row_for(ROWS, None, "glm-5.3[1m]").name == "glm-5.3[1m]"


def test_one_million_suffix_matches_either_way() -> None:
    assert _price_row_for(ROWS, "glm-5.3", "x").name == "glm-5.3[1m]"
    assert _price_row_for(ROWS, "claude-opus-5[1m]", None).name == "claude-opus-5"


def test_unknown_pin_falls_back_to_default_then_first() -> None:
    assert _price_row_for(ROWS, "not-a-model", "claude-opus-5").name == "claude-opus-5"
    assert _price_row_for(ROWS, "not-a-model", "also-not").name == "glm-5.3[1m]"


def _result(
    input_tokens: int = 0, output_tokens: int = 0,
    cache_read_tokens: int = 0, cache_write_tokens: int = 0,
) -> ExecutionResult:
    return ExecutionResult(
        success=True, stdout="", stderr="", duration_seconds=1.0,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
    )


def test_cache_reads_bill_at_the_cache_rate_when_priced() -> None:
    cost = _estimate_cost(
        _result(input_tokens=1_000, output_tokens=0, cache_read_tokens=9_000),
        cost_per_1k_input=0.002, cost_per_1k_output=0.01,
        cost_per_1k_cache_read=0.0002, cost_per_1k_cache_write=0.0025,
    )
    assert abs(cost - (1 * 0.002 + 9 * 0.0002)) < 1e-12


def test_cache_reads_bill_at_the_input_rate_when_unpriced() -> None:
    cost = _estimate_cost(
        _result(input_tokens=1_000, output_tokens=0, cache_read_tokens=9_000),
        cost_per_1k_input=0.002, cost_per_1k_output=0.01,
    )
    assert abs(cost - 10 * 0.002) < 1e-12


def test_cache_writes_are_charged() -> None:
    cost = _estimate_cost(
        _result(input_tokens=0, output_tokens=0, cache_write_tokens=4_000),
        cost_per_1k_input=0.002, cost_per_1k_output=0.01, cost_per_1k_cache_write=0.0025,
    )
    assert abs(cost - 4 * 0.0025) < 1e-12


def test_missing_pricing_still_reports_zero_and_uncertain_path() -> None:
    assert _estimate_cost(_result(input_tokens=100, cache_read_tokens=900)) == 0.0
