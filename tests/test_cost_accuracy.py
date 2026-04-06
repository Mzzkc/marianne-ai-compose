"""Tests for cost accuracy improvements (D-024, Circuit M4).

Root cause investigation found 5 issues in the cost tracking pipeline:
1. ClaudeCliBackend returns zero token data from JSON output
2. Baton musician uses hardcoded Sonnet pricing
3. Instrument profile model pricing never used for cost calculation
4. Cost confidence tracked but never displayed to users
5. PluginCliBackend CAN extract tokens but legacy runner doesn't use it

These tests verify the fixes for issues 1 and 4 (the two that affect
production today).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from marianne.backends.base import ExecutionResult
from marianne.core.checkpoint import CheckpointState, SheetState


# ── ClaudeCliBackend JSON Token Extraction ─────────────────────────────


class TestClaudeCliBackendTokenExtraction:
    """ClaudeCliBackend should extract tokens from JSON output when available."""

    def _make_backend(self, output_format: str = "json") -> "ClaudeCliBackend":  # noqa: F821
        """Create a ClaudeCliBackend with the given output format."""
        from marianne.backends.claude_cli import ClaudeCliBackend

        return ClaudeCliBackend(
            output_format=output_format,
            skip_permissions=True,
            disable_mcp=True,
        )

    def test_json_output_extracts_input_and_output_tokens(self) -> None:
        """When output_format is json and response has usage, tokens are extracted."""
        backend = self._make_backend(output_format="json")
        usage_data = {
            "result": "Hello world",
            "usage": {
                "input_tokens": 15000,
                "output_tokens": 3500,
            },
        }
        stdout = json.dumps(usage_data)
        result = backend._build_completed_result(
            stdout=stdout, stderr="", exit_code=0,
            exit_signal=None, exit_reason="completed", duration=5.0,
        )
        assert result.input_tokens == 15000
        assert result.output_tokens == 3500

    def test_json_output_returns_none_when_no_usage(self) -> None:
        """When JSON output has no usage field, tokens remain None."""
        backend = self._make_backend(output_format="json")
        stdout = json.dumps({"result": "Hello world"})
        result = backend._build_completed_result(
            stdout=stdout, stderr="", exit_code=0,
            exit_signal=None, exit_reason="completed", duration=5.0,
        )
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_text_output_returns_none_tokens(self) -> None:
        """When output_format is text, no token extraction is attempted."""
        backend = self._make_backend(output_format="text")
        result = backend._build_completed_result(
            stdout="Hello world", stderr="", exit_code=0,
            exit_signal=None, exit_reason="completed", duration=5.0,
        )
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_json_output_partial_usage_input_only(self) -> None:
        """When JSON has only input_tokens, output remains None."""
        backend = self._make_backend(output_format="json")
        stdout = json.dumps({
            "result": "test",
            "usage": {"input_tokens": 10000},
        })
        result = backend._build_completed_result(
            stdout=stdout, stderr="", exit_code=0,
            exit_signal=None, exit_reason="completed", duration=5.0,
        )
        assert result.input_tokens == 10000
        assert result.output_tokens is None

    def test_json_output_partial_usage_output_only(self) -> None:
        """When JSON has only output_tokens, input remains None."""
        backend = self._make_backend(output_format="json")
        stdout = json.dumps({
            "result": "test",
            "usage": {"output_tokens": 5000},
        })
        result = backend._build_completed_result(
            stdout=stdout, stderr="", exit_code=0,
            exit_signal=None, exit_reason="completed", duration=5.0,
        )
        assert result.input_tokens is None
        assert result.output_tokens == 5000

    def test_json_output_malformed_json_returns_none(self) -> None:
        """When stdout isn't valid JSON, tokens remain None (graceful fallback)."""
        backend = self._make_backend(output_format="json")
        result = backend._build_completed_result(
            stdout="not json {{{", stderr="", exit_code=0,
            exit_signal=None, exit_reason="completed", duration=5.0,
        )
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_json_output_non_dict_usage_returns_none(self) -> None:
        """When usage field is not a dict, tokens remain None."""
        backend = self._make_backend(output_format="json")
        stdout = json.dumps({
            "result": "test",
            "usage": "not a dict",
        })
        result = backend._build_completed_result(
            stdout=stdout, stderr="", exit_code=0,
            exit_signal=None, exit_reason="completed", duration=5.0,
        )
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_json_output_non_integer_token_values_ignored(self) -> None:
        """When token values aren't integers, they're ignored."""
        backend = self._make_backend(output_format="json")
        stdout = json.dumps({
            "result": "test",
            "usage": {
                "input_tokens": "not a number",
                "output_tokens": None,
            },
        })
        result = backend._build_completed_result(
            stdout=stdout, stderr="", exit_code=0,
            exit_signal=None, exit_reason="completed", duration=5.0,
        )
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_json_output_zero_tokens_still_extracted(self) -> None:
        """Zero is a valid token count (e.g., cached response)."""
        backend = self._make_backend(output_format="json")
        stdout = json.dumps({
            "result": "test",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
            },
        })
        result = backend._build_completed_result(
            stdout=stdout, stderr="", exit_code=0,
            exit_signal=None, exit_reason="completed", duration=5.0,
        )
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    def test_failed_execution_still_extracts_tokens_from_json(self) -> None:
        """Token extraction works even on failed executions (exit_code != 0)."""
        backend = self._make_backend(output_format="json")
        stdout = json.dumps({
            "result": "",
            "error": {"message": "something failed"},
            "usage": {
                "input_tokens": 8000,
                "output_tokens": 200,
            },
        })
        result = backend._build_completed_result(
            stdout=stdout, stderr="error occurred", exit_code=1,
            exit_signal=None, exit_reason="error", duration=3.0,
        )
        assert result.input_tokens == 8000
        assert result.output_tokens == 200


# ── Cost Confidence Display ────────────────────────────────────────────


class TestCostConfidenceDisplay:
    """Cost status display should show confidence level."""

    def _make_job_state(
        self,
        *,
        total_cost: float = 1.50,
        total_input: int = 50000,
        total_output: int = 10000,
        cost_confidence: float = 1.0,
    ) -> CheckpointState:
        """Build a CheckpointState with cost data."""
        state = CheckpointState(
            job_id="test-job",
            job_name="test-score",
            total_sheets=1,
        )
        state.total_estimated_cost = total_cost
        state.total_input_tokens = total_input
        state.total_output_tokens = total_output

        # Add a sheet with cost data
        sheet = SheetState(sheet_num=1)
        sheet.estimated_cost = total_cost
        sheet.cost_confidence = cost_confidence
        sheet.input_tokens = total_input
        sheet.output_tokens = total_output
        state.sheets[1] = sheet

        return state

    def test_low_confidence_cost_has_estimation_indicator(self) -> None:
        """When cost confidence < 0.9, display shows estimation indicator."""
        from marianne.cli.commands.status import _render_cost_summary

        state = self._make_job_state(
            total_cost=0.17,
            cost_confidence=0.7,
        )
        console = MagicMock()
        with patch("marianne.cli.commands.status.console", console):
            _render_cost_summary(state)

        # Collect all print calls as strings
        all_calls = [str(call) for call in console.print.call_args_list]

        # The cost line should indicate estimation
        cost_calls = [c for c in all_calls if "0.17" in c]
        assert len(cost_calls) > 0, f"Expected cost display with 0.17. All calls: {all_calls}"

        # Should have some estimation indicator (~ or est.)
        has_indicator = any("~" in c or "est" in c.lower() for c in cost_calls)
        assert has_indicator, (
            f"Expected estimation indicator (~/$est) for low confidence cost. "
            f"Cost calls: {cost_calls}"
        )

    def test_high_confidence_cost_no_estimation_indicator(self) -> None:
        """When cost confidence >= 0.9, display shows exact cost without indicator."""
        from marianne.cli.commands.status import _render_cost_summary

        state = self._make_job_state(
            total_cost=5.00,
            cost_confidence=1.0,
        )
        console = MagicMock()
        with patch("marianne.cli.commands.status.console", console):
            _render_cost_summary(state)

        all_calls = [str(call) for call in console.print.call_args_list]
        cost_calls = [c for c in all_calls if "5.00" in c]
        assert len(cost_calls) > 0, f"Expected cost display with 5.00. All calls: {all_calls}"

        # Should NOT have estimation indicator
        for call in cost_calls:
            assert "~$" not in call, f"High-confidence cost should not show ~: {call}"


# ── CostMixin Confidence Tracking ─────────────────────────────────────


class TestCostConfidenceTracking:
    """CostMixin correctly sets confidence based on token source."""

    def _make_result(
        self, *, input_tokens: int | None = None,
        output_tokens: int | None = None,
        tokens_used: int | None = None,
        stdout: str = "output text",
    ) -> ExecutionResult:
        return ExecutionResult(
            success=True,
            exit_code=0,
            stdout=stdout,
            stderr="",
            duration_seconds=5.0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_used=tokens_used,
        )

    @pytest.mark.asyncio
    async def test_exact_tokens_give_full_confidence(self) -> None:
        """When both input and output tokens are provided, confidence is 1.0."""
        from marianne.execution.runner.cost import CostMixin

        mixin = MagicMock(spec=CostMixin)
        mixin.config = MagicMock()
        mixin.config.cost_limits.cost_per_1k_input_tokens = 0.003
        mixin.config.cost_limits.cost_per_1k_output_tokens = 0.015
        mixin.config.cost_limits.max_cost_per_job = None
        mixin._circuit_breaker = None
        mixin._summary = None

        async def noop_fire(*args: object, **kwargs: object) -> None:
            pass
        mixin._fire_event = noop_fire

        result = self._make_result(input_tokens=50000, output_tokens=10000)
        sheet = SheetState(sheet_num=1)
        state = CheckpointState(
            job_id="test", job_name="test-score",
            total_sheets=1,
        )

        _, _, _, confidence = await CostMixin._track_cost(mixin, result, sheet, state)
        assert confidence == 1.0
        assert sheet.cost_confidence == 1.0

    @pytest.mark.asyncio
    async def test_character_estimation_gives_low_confidence(self) -> None:
        """When no token data is available, confidence is 0.7."""
        from marianne.execution.runner.cost import CostMixin

        mixin = MagicMock(spec=CostMixin)
        mixin.config = MagicMock()
        mixin.config.cost_limits.cost_per_1k_input_tokens = 0.003
        mixin.config.cost_limits.cost_per_1k_output_tokens = 0.015
        mixin.config.cost_limits.max_cost_per_job = None
        mixin._circuit_breaker = None
        mixin._summary = None

        async def noop_fire(*args: object, **kwargs: object) -> None:
            pass
        mixin._fire_event = noop_fire

        result = self._make_result(stdout="a" * 1000)  # No token data
        sheet = SheetState(sheet_num=1)
        state = CheckpointState(
            job_id="test", job_name="test-score",
            total_sheets=1,
        )

        _, _, _, confidence = await CostMixin._track_cost(mixin, result, sheet, state)
        assert confidence == 0.7
        assert sheet.cost_confidence == 0.7


# ── Baton Musician Cost Estimation ─────────────────────────────────────


class TestBatonMusicianCostEstimation:
    """Baton musician _estimate_cost should handle missing token data."""

    def test_estimate_with_real_tokens(self) -> None:
        """When tokens are provided, cost should be calculated from them."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = ExecutionResult(
            success=True, exit_code=0, stdout="test", stderr="",
            duration_seconds=5.0,
            input_tokens=100000, output_tokens=20000,
        )
        cost = _estimate_cost(result)
        # $3/1M input + $15/1M output (Sonnet pricing)
        expected = (100000 * 3.0 / 1_000_000) + (20000 * 15.0 / 1_000_000)
        assert abs(cost - expected) < 0.001

    def test_estimate_with_zero_tokens_returns_zero(self) -> None:
        """When tokens are None (CLI backend), cost is $0.00."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = ExecutionResult(
            success=True, exit_code=0, stdout="test output", stderr="",
            duration_seconds=5.0,
            input_tokens=None, output_tokens=None,
        )
        cost = _estimate_cost(result)
        assert cost == 0.0

    def test_estimate_with_partial_tokens(self) -> None:
        """When only output tokens are provided, input defaults to 0."""
        from marianne.daemon.baton.musician import _estimate_cost

        result = ExecutionResult(
            success=True, exit_code=0, stdout="test", stderr="",
            duration_seconds=5.0,
            input_tokens=None, output_tokens=5000,
        )
        cost = _estimate_cost(result)
        expected = 5000 * 15.0 / 1_000_000
        assert abs(cost - expected) < 0.001
