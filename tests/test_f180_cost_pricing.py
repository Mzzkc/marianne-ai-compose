"""Tests for F-180: Wire instrument profile pricing into cost estimation.

The baton's _estimate_cost() uses hardcoded Claude Sonnet pricing ($3/1M input,
$15/1M output). This should use the instrument profile's ModelCapacity pricing
when available, falling back to the hardcoded estimate when not.

Root cause 2 and 3 from F-180: baton hardcoded pricing and instrument profile
pricing unused.

TDD: Tests define the contract. Implementation fulfills it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from mozart.backends.base import ExecutionResult
from mozart.daemon.baton.musician import _estimate_cost


class TestEstimateCostWithProfilePricing:
    """_estimate_cost uses profile pricing when provided."""

    def test_hardcoded_fallback_when_no_pricing(self) -> None:
        """Without profile pricing, falls back to hardcoded Claude Sonnet rates."""
        result = MagicMock(spec=ExecutionResult)
        result.input_tokens = 1_000_000
        result.output_tokens = 100_000

        cost = _estimate_cost(result)
        # Hardcoded: $3/1M input + $15/1M output
        expected = (1_000_000 * 3.0 / 1_000_000) + (100_000 * 15.0 / 1_000_000)
        assert abs(cost - expected) < 0.001

    def test_uses_profile_pricing_when_provided(self) -> None:
        """When profile pricing is given, uses that instead of hardcoded rates."""
        result = MagicMock(spec=ExecutionResult)
        result.input_tokens = 1_000_000
        result.output_tokens = 100_000

        # Opus pricing: $15/1M input, $75/1M output (via cost_per_1k: 0.015, 0.075)
        cost = _estimate_cost(
            result,
            cost_per_1k_input=0.015,
            cost_per_1k_output=0.075,
        )
        # $15/1M input = $15 for 1M tokens, $75/1M output = $7.5 for 100K
        expected = (1_000_000 * 0.015 / 1_000) + (100_000 * 0.075 / 1_000)
        assert abs(cost - expected) < 0.001
        # Should be different from hardcoded rates
        hardcoded_cost = (1_000_000 * 3.0 / 1_000_000) + (100_000 * 15.0 / 1_000_000)
        assert cost != hardcoded_cost

    def test_zero_pricing_for_local_models(self) -> None:
        """Local/free instruments report $0 cost via profile pricing."""
        result = MagicMock(spec=ExecutionResult)
        result.input_tokens = 500_000
        result.output_tokens = 50_000

        cost = _estimate_cost(
            result,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
        )
        assert cost == 0.0

    def test_zero_tokens_returns_zero(self) -> None:
        """Zero tokens = zero cost regardless of pricing."""
        result = MagicMock(spec=ExecutionResult)
        result.input_tokens = 0
        result.output_tokens = 0

        cost = _estimate_cost(result, cost_per_1k_input=0.015, cost_per_1k_output=0.075)
        assert cost == 0.0

    def test_none_tokens_treated_as_zero(self) -> None:
        """None tokens (backend didn't report) treated as zero."""
        result = MagicMock(spec=ExecutionResult)
        result.input_tokens = None
        result.output_tokens = None

        cost = _estimate_cost(result, cost_per_1k_input=0.015, cost_per_1k_output=0.075)
        assert cost == 0.0

    def test_haiku_pricing(self) -> None:
        """Haiku's cheaper pricing should produce lower costs."""
        result = MagicMock(spec=ExecutionResult)
        result.input_tokens = 1_000_000
        result.output_tokens = 100_000

        # Haiku: $0.0008/1K input, $0.004/1K output (from profile)
        cost = _estimate_cost(
            result,
            cost_per_1k_input=0.0008,
            cost_per_1k_output=0.004,
        )
        # $0.8 for 1M input + $0.4 for 100K output
        expected = (1_000_000 * 0.0008 / 1_000) + (100_000 * 0.004 / 1_000)
        assert abs(cost - expected) < 0.001
        assert cost < 2.0  # Much cheaper than Sonnet
