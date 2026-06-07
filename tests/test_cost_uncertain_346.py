"""#346: cost fallback must not fabricate Claude Sonnet rates.

When an instrument profile carries no per-token pricing, the old code billed
the sheet at hardcoded Sonnet rates ($3/1M in, $15/1M out). For the free-tier /
subscription / local instruments Marianne actually runs, that over-reports a
$0 run and can *falsely* trip `max_cost_per_job`. The honest behaviour: report
`$0` and flag the attempt/sheet `cost_uncertain` so the gap is visible without
inventing a number.

`_pricing_missing` is the single predicate both `_estimate_cost` (to pick the
$0 path) and the caller (to set `cost_uncertain`) share, so the two can never
drift.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from marianne.backends.base import ExecutionResult
from marianne.core.checkpoint import SheetState
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.musician import _estimate_cost, _pricing_missing


def _result(input_tokens: int = 1_000_000, output_tokens: int = 1_000_000) -> ExecutionResult:
    r = MagicMock(spec=ExecutionResult)
    r.input_tokens = input_tokens
    r.output_tokens = output_tokens
    return r


class TestEstimateCostNoFabrication:
    def test_missing_both_prices_yields_zero_not_sonnet(self) -> None:
        # Old behaviour returned $18 (Sonnet). New: $0, no fabrication.
        cost = _estimate_cost(_result(), cost_per_1k_input=None, cost_per_1k_output=None)
        assert cost == 0.0

    def test_missing_input_price_yields_zero(self) -> None:
        cost = _estimate_cost(_result(), cost_per_1k_input=None, cost_per_1k_output=0.03)
        assert cost == 0.0

    def test_missing_output_price_yields_zero(self) -> None:
        cost = _estimate_cost(_result(), cost_per_1k_input=0.01, cost_per_1k_output=None)
        assert cost == 0.0

    def test_present_pricing_still_computes(self) -> None:
        cost = _estimate_cost(
            _result(input_tokens=10_000, output_tokens=2_000),
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
        )
        assert cost == (10_000 * 0.003 / 1_000) + (2_000 * 0.015 / 1_000)


class TestPricingMissingPredicate:
    def test_both_present_is_not_missing(self) -> None:
        assert _pricing_missing(0.003, 0.015) is False

    def test_zero_pricing_is_not_missing(self) -> None:
        # $0/$0 is a DECLARED price (local/free), not missing.
        assert _pricing_missing(0.0, 0.0) is False

    def test_any_none_is_missing(self) -> None:
        assert _pricing_missing(None, 0.015) is True
        assert _pricing_missing(0.003, None) is True
        assert _pricing_missing(None, None) is True


class TestSheetAttemptResultFlag:
    def test_default_is_certain(self) -> None:
        r = SheetAttemptResult(job_id="j", sheet_num=1, instrument_name="x", attempt=1)
        assert r.cost_uncertain is False

    def test_flag_can_be_set(self) -> None:
        r = SheetAttemptResult(
            job_id="j", sheet_num=1, instrument_name="x", attempt=1, cost_uncertain=True
        )
        assert r.cost_uncertain is True


class TestStatusSurfacing:
    """#373: `mzt status` distinguishes a cost-uncertain $0 from a genuine $0."""

    def test_json_status_marks_cost_uncertain_sheet(self, capsys: object) -> None:
        from marianne.cli.commands.status import _output_status_json
        from marianne.core.checkpoint import CheckpointState
        from marianne.core.checkpoint import JobStatus as CPJobStatus

        job = CheckpointState(
            job_id="j1",
            job_name="demo",
            total_sheets=2,
            status=CPJobStatus.RUNNING,
            sheets={
                1: SheetState(sheet_num=1, cost_uncertain=True),
                2: SheetState(sheet_num=2, cost_uncertain=False),
            },
        )
        _output_status_json(job)
        out = capsys.readouterr().out  # type: ignore[attr-defined]
        # Emitted only for the uncertain sheet (set-only-when-True).
        assert out.count("cost_uncertain") == 1

    def test_cost_summary_warns_on_uncertain(self, capsys: object) -> None:
        from marianne.cli.commands.status import _render_cost_summary
        from marianne.core.checkpoint import CheckpointState
        from marianne.core.checkpoint import JobStatus as CPJobStatus

        job = CheckpointState(
            job_id="j1",
            job_name="demo",
            total_sheets=1,
            status=CPJobStatus.RUNNING,
            sheets={1: SheetState(sheet_num=1, cost_uncertain=True)},
        )
        _render_cost_summary(job)
        out = capsys.readouterr().out  # type: ignore[attr-defined]
        assert "no pricing" in out and "$0" in out


class TestSheetStateCostUncertainPropagation:
    def test_record_attempt_marks_sheet_cost_uncertain(self) -> None:
        sheet = SheetState(sheet_num=1)
        assert sheet.cost_uncertain is False
        sheet.record_attempt(
            SheetAttemptResult(
                job_id="j", sheet_num=1, instrument_name="x", attempt=1,
                cost_usd=0.0, cost_uncertain=True,
            )
        )
        assert sheet.cost_uncertain is True

    def test_certain_attempt_leaves_sheet_certain(self) -> None:
        sheet = SheetState(sheet_num=1)
        sheet.record_attempt(
            SheetAttemptResult(
                job_id="j", sheet_num=1, instrument_name="x", attempt=1,
                cost_usd=0.06, cost_uncertain=False,
            )
        )
        assert sheet.cost_uncertain is False

    def test_uncertainty_is_sticky_across_attempts(self) -> None:
        # Once any attempt lacked pricing, the sheet total is uncertain.
        sheet = SheetState(sheet_num=1)
        sheet.record_attempt(
            SheetAttemptResult(
                job_id="j", sheet_num=1, instrument_name="x", attempt=1,
                cost_usd=0.0, cost_uncertain=True,
            )
        )
        sheet.record_attempt(
            SheetAttemptResult(
                job_id="j", sheet_num=1, instrument_name="x", attempt=2,
                cost_usd=0.06, cost_uncertain=False,
            )
        )
        assert sheet.cost_uncertain is True
