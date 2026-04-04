"""Tests for failure propagation during baton recovery.

F-440: State sync gap — _propagate_failure_to_dependents() modifies sheet
status directly (not via events), so _sync_sheet_status() never fires for
cascaded failures. On restart recovery, dependent sheets revert to PENDING
while their upstream dependency is FAILED → zombie job (same class as F-039).

The fix: recover_job() must re-run failure propagation for any sheet that
recovers as FAILED, ensuring dependents are correctly cascaded.
"""

from __future__ import annotations

from pathlib import Path

from mozart.daemon.baton.adapter import BatonAdapter
from mozart.daemon.baton.core import BatonCore
from mozart.daemon.baton.state import BatonSheetStatus, SheetExecutionState


def _make_states(
    statuses: dict[int, BatonSheetStatus],
    instrument: str = "claude-cli",
) -> dict[int, SheetExecutionState]:
    """Create SheetExecutionState objects from a status dict."""
    states: dict[int, SheetExecutionState] = {}
    for num, status in statuses.items():
        s = SheetExecutionState(
            sheet_num=num,
            instrument_name=instrument,
            max_retries=3,
        )
        s.status = status
        states[num] = s
    return states


class TestRecoveryRerunsFailurePropagation:
    """F-440: Recovery must re-propagate failure to dependents."""

    def test_failed_sheet_dependents_marked_failed_on_recovery(self) -> None:
        """When a FAILED sheet has PENDING dependents, recovery cascades failure."""
        baton = BatonCore()

        # Simulate recovery: sheet 1 FAILED, sheet 2 (depends on 1) PENDING
        # This is what happens when failure propagation wasn't synced
        states = _make_states({
            1: BatonSheetStatus.FAILED,
            2: BatonSheetStatus.PENDING,  # Should have been FAILED
            3: BatonSheetStatus.COMPLETED,
        })
        deps = {2: [1], 3: []}  # Sheet 2 depends on sheet 1

        baton.register_job("test-job", states, deps)

        # After registration, the baton should see sheet 2 as FAILED
        # (propagated from sheet 1's failure)
        sheet2 = baton.get_sheet_state("test-job", 2)
        assert sheet2 is not None
        assert sheet2.status == BatonSheetStatus.FAILED, (
            "Sheet 2 should be FAILED because its dependency (sheet 1) is FAILED. "
            f"Got: {sheet2.status}"
        )

    def test_transitive_failure_propagation_on_recovery(self) -> None:
        """Chain: 1→2→3. Sheet 1 FAILED, 2 and 3 PENDING → both FAILED."""
        baton = BatonCore()

        states = _make_states({
            1: BatonSheetStatus.FAILED,
            2: BatonSheetStatus.PENDING,
            3: BatonSheetStatus.PENDING,
        })
        deps = {2: [1], 3: [2]}  # 3 depends on 2, 2 depends on 1

        baton.register_job("chain-job", states, deps)

        sheet2 = baton.get_sheet_state("chain-job", 2)
        sheet3 = baton.get_sheet_state("chain-job", 3)
        assert sheet2 is not None and sheet2.status == BatonSheetStatus.FAILED
        assert sheet3 is not None and sheet3.status == BatonSheetStatus.FAILED

    def test_completed_dependents_not_affected(self) -> None:
        """Sheet 1 FAILED, sheet 2 COMPLETED → sheet 2 stays COMPLETED."""
        baton = BatonCore()

        states = _make_states({
            1: BatonSheetStatus.FAILED,
            2: BatonSheetStatus.COMPLETED,
        })
        deps = {2: [1]}

        baton.register_job("safe-job", states, deps)

        sheet2 = baton.get_sheet_state("safe-job", 2)
        assert sheet2 is not None and sheet2.status == BatonSheetStatus.COMPLETED

    def test_no_propagation_when_no_failures(self) -> None:
        """All sheets PENDING with no failures → no propagation occurs."""
        baton = BatonCore()

        states = _make_states({
            1: BatonSheetStatus.PENDING,
            2: BatonSheetStatus.PENDING,
        })
        deps = {2: [1]}

        baton.register_job("fresh-job", states, deps)

        sheet1 = baton.get_sheet_state("fresh-job", 1)
        sheet2 = baton.get_sheet_state("fresh-job", 2)
        assert sheet1 is not None and sheet1.status == BatonSheetStatus.PENDING
        assert sheet2 is not None and sheet2.status == BatonSheetStatus.PENDING

    def test_multiple_failed_sheets_propagate_independently(self) -> None:
        """Multiple failures propagate to all their respective dependents."""
        baton = BatonCore()

        # Two independent failure chains
        states = _make_states({
            1: BatonSheetStatus.FAILED,
            2: BatonSheetStatus.PENDING,  # Depends on 1
            3: BatonSheetStatus.FAILED,
            4: BatonSheetStatus.PENDING,  # Depends on 3
            5: BatonSheetStatus.PENDING,  # No dependencies
        })
        deps = {2: [1], 4: [3], 5: []}

        baton.register_job("multi-fail", states, deps)

        sheet2 = baton.get_sheet_state("multi-fail", 2)
        sheet4 = baton.get_sheet_state("multi-fail", 4)
        sheet5 = baton.get_sheet_state("multi-fail", 5)
        assert sheet2 is not None and sheet2.status == BatonSheetStatus.FAILED
        assert sheet4 is not None and sheet4.status == BatonSheetStatus.FAILED
        assert sheet5 is not None and sheet5.status == BatonSheetStatus.PENDING

    def test_diamond_dependency_failure_propagation(self) -> None:
        """Diamond: 1→{2,3}→4. Sheet 1 FAILED → 2, 3, 4 all FAILED."""
        baton = BatonCore()

        states = _make_states({
            1: BatonSheetStatus.FAILED,
            2: BatonSheetStatus.PENDING,
            3: BatonSheetStatus.PENDING,
            4: BatonSheetStatus.PENDING,
        })
        # 2 depends on 1, 3 depends on 1, 4 depends on both 2 and 3
        deps = {2: [1], 3: [1], 4: [2, 3]}

        baton.register_job("diamond-job", states, deps)

        for num in [2, 3, 4]:
            sheet = baton.get_sheet_state("diamond-job", num)
            assert sheet is not None and sheet.status == BatonSheetStatus.FAILED, (
                f"Sheet {num} should be FAILED (dependency chain from sheet 1). "
                f"Got: {sheet.status}"
            )

    def test_job_completes_after_recovery_propagation(self) -> None:
        """After propagation, is_job_complete returns True (no zombies)."""
        baton = BatonCore()

        states = _make_states({
            1: BatonSheetStatus.FAILED,
            2: BatonSheetStatus.PENDING,
            3: BatonSheetStatus.COMPLETED,
        })
        deps = {2: [1], 3: []}

        baton.register_job("zombie-check", states, deps)

        # Job should now be complete: sheet 1 FAILED, sheet 2 FAILED
        # (propagated), sheet 3 COMPLETED — all terminal
        assert baton.is_job_complete("zombie-check"), (
            "Job should be complete after recovery propagation. "
            "Without the fix, sheet 2 stays PENDING → zombie job."
        )


class TestAdapterRecoveryPropagation:
    """Verify the adapter's recover_job path includes propagation."""

    def test_adapter_recover_propagates_failures(self) -> None:
        """BatonAdapter.recover_job() propagates failures from checkpoint."""
        from mozart.core.checkpoint import CheckpointState, SheetState, SheetStatus
        from mozart.core.sheet import Sheet

        adapter = BatonAdapter(max_concurrent_sheets=4)

        # Create minimal sheets
        sheets = [
            Sheet(
                num=1,
                workspace=Path("/tmp/test"),
                instrument_name="claude-cli",
                movement=1,
                voice=1,
                voice_count=2,
            ),
            Sheet(
                num=2,
                workspace=Path("/tmp/test"),
                instrument_name="claude-cli",
                movement=1,
                voice=2,
                voice_count=2,
            ),
        ]

        # Create checkpoint where sheet 1 is failed but sheet 2 is pending
        # (simulates unsynced failure propagation)
        cp = CheckpointState(
            job_id="recovery-test",
            job_name="test",
            total_sheets=2,
            config_snapshot={},
        )
        cp.sheets[1] = SheetState(sheet_num=1)
        cp.sheets[1].status = SheetStatus.FAILED
        cp.sheets[1].attempt_count = 3
        cp.sheets[2] = SheetState(sheet_num=2)
        cp.sheets[2].status = SheetStatus.PENDING

        deps = {2: [1]}

        adapter.recover_job(
            job_id="recovery-test",
            sheets=sheets,
            dependencies=deps,
            checkpoint=cp,
            max_retries=3,
        )

        # Sheet 2 should be FAILED after recovery propagation
        state = adapter._baton.get_sheet_state("recovery-test", 2)
        assert state is not None
        assert state.status == BatonSheetStatus.FAILED, (
            f"Sheet 2 should be FAILED via recovery propagation. Got: {state.status}"
        )
