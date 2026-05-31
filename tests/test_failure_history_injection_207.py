"""#207: wire failure-history injection into the baton dispatch path.

The producer (FailureHistoryStore / HistoricalFailure) and consumer
(PromptRenderer's failure_history= param) both existed; the baton's render call
never passed it. Per the 4-model lab (unanimous): FailureHistoryStore is
refactored to take the execution-state sheet map directly (it only reads
.sheets), fed from the baton's live job sheets; the adapter builds it via a
testable _build_failure_history helper and passes the result (always — the
store returns [] when there are no prior-sheet failures, so the renderer
no-ops). Prior-sheet only (excludes the current sheet); recent N (limit 3).
"""

from __future__ import annotations

from marianne.core.checkpoint import SheetState, SheetStatus
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.state import SheetExecutionState
from marianne.execution.validation.history import FailureHistoryStore


def _sheet(num: int, *, passed: bool, rule_type: str = "file_exists") -> SheetState:
    return SheetState(
        sheet_num=num,
        status=SheetStatus.COMPLETED if passed else SheetStatus.FAILED,
        validation_passed=passed,
        validation_details=[
            {
                "rule_type": rule_type,
                "description": f"{rule_type} check",
                "passed": passed,
                "failure_reason": None if passed else f"{rule_type} failed",
                "failure_category": None if passed else "validation_failure",
            }
        ],
    )


# ── FailureHistoryStore now accepts a sheets dict (was: CheckpointState) ──


class TestStoreAcceptsSheetsDict:
    def test_query_recent_failures_from_sheets_dict(self) -> None:
        sheets = {1: _sheet(1, passed=False), 2: _sheet(2, passed=True)}
        store = FailureHistoryStore(sheets)
        failures = store.query_recent_failures(current_sheet=3)
        assert len(failures) == 1
        assert failures[0].sheet_num == 1
        assert failures[0].rule_type == "file_exists"
        assert failures[0].failure_reason == "file_exists failed"

    def test_no_prior_failures_returns_empty(self) -> None:
        sheets = {1: _sheet(1, passed=True)}
        store = FailureHistoryStore(sheets)
        assert store.query_recent_failures(current_sheet=2) == []

    def test_current_sheet_excluded(self) -> None:
        sheets = {3: _sheet(3, passed=False)}
        store = FailureHistoryStore(sheets)
        assert store.query_recent_failures(current_sheet=3) == []

    def test_missing_validation_details_is_safe(self) -> None:
        s = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)  # no details
        store = FailureHistoryStore({1: s})
        assert store.query_recent_failures(current_sheet=2) == []


# ── adapter._build_failure_history wiring helper ──────────────────────────


class TestBuildFailureHistory:
    @staticmethod
    def _adapter_with_sheets(states: dict[int, SheetState]) -> BatonAdapter:
        adapter = BatonAdapter()
        sheets = {
            n: SheetExecutionState(sheet_num=n, instrument_name="claude-code")
            for n in states
        }
        adapter._baton.register_job("j", sheets, {})
        # Overlay validation state onto the baton's live sheet objects.
        for n, src in states.items():
            tgt = adapter._baton._jobs["j"].sheets[n]
            tgt.status = src.status
            tgt.validation_passed = src.validation_passed
            tgt.validation_details = src.validation_details
        return adapter

    def test_returns_prior_failures_for_current_sheet(self) -> None:
        adapter = self._adapter_with_sheets(
            {1: _sheet(1, passed=False), 2: _sheet(2, passed=True)}
        )
        result = adapter._build_failure_history("j", 3)
        assert result is not None
        assert [f.sheet_num for f in result] == [1]

    def test_returns_none_when_no_prior_failures(self) -> None:
        adapter = self._adapter_with_sheets({1: _sheet(1, passed=True)})
        # None (not []) so the renderer cleanly no-ops.
        assert adapter._build_failure_history("j", 2) is None

    def test_returns_none_for_unknown_job(self) -> None:
        adapter = BatonAdapter()
        assert adapter._build_failure_history("nope", 1) is None
