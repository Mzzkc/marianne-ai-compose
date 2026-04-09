"""Tests for baton event flow unification.

Verifies: transitive cascade propagation, SheetDispatched event,
exhaustion path ordering, and shared object identity.
"""

from pathlib import Path

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult, SheetDispatched
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState


class TestCascadeTransitivePropagation:
    """Cascade-SKIPPED sheets must propagate to their own dependents."""

    def test_linear_chain_propagates_through_skipped(self) -> None:
        """In a chain 1->2->3, failing sheet 1 should SKIP both 2 and 3."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, max_retries=0),
            2: SheetExecutionState(sheet_num=2, max_retries=0),
            3: SheetExecutionState(sheet_num=3, max_retries=0),
        }
        deps = {2: [1], 3: [2]}
        baton.register_job("j1", sheets, deps)

        # Fail sheet 1
        baton._handle_attempt_result(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="test",
            attempt=1, execution_success=False,
            error_message="failed",
        ))

        assert sheets[1].status == BatonSheetStatus.FAILED
        assert sheets[2].status == BatonSheetStatus.SKIPPED
        assert sheets[3].status == BatonSheetStatus.SKIPPED, (
            "Sheet 3 must be SKIPPED transitively through cascade-SKIPPED sheet 2"
        )

    def test_deep_chain_four_levels(self) -> None:
        """Chain 1->2->3->4: failing 1 cascades all the way to 4."""
        baton = BatonCore()
        sheets = {
            i: SheetExecutionState(sheet_num=i, max_retries=0)
            for i in range(1, 5)
        }
        deps = {2: [1], 3: [2], 4: [3]}
        baton.register_job("j1", sheets, deps)

        baton._handle_attempt_result(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="test",
            attempt=1, execution_success=False,
            error_message="failed",
        ))

        for i in range(2, 5):
            assert sheets[i].status == BatonSheetStatus.SKIPPED, (
                f"Sheet {i} must be transitively SKIPPED"
            )

    def test_fan_out_waits_for_all_deps(self) -> None:
        """Fan-out: voice1+voice2 -> synthesis. One voice fails, one completes.
        Synthesis should be SKIPPED (blocked by failed voice)."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, max_retries=0),
            2: SheetExecutionState(sheet_num=2, max_retries=0),
            3: SheetExecutionState(sheet_num=3, max_retries=0),
        }
        deps = {3: [1, 2]}
        baton.register_job("j1", sheets, deps)

        # Voice 1 completes
        baton._handle_attempt_result(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="test",
            attempt=1, execution_success=True,
            validation_pass_rate=100.0,
        ))
        # Synthesis should NOT be skipped yet (voice 2 still pending)
        assert sheets[3].status == BatonSheetStatus.PENDING

        # Voice 2 fails
        baton._handle_attempt_result(SheetAttemptResult(
            job_id="j1", sheet_num=2, instrument_name="test",
            attempt=1, execution_success=False,
            error_message="failed",
        ))
        # NOW synthesis should be SKIPPED (all deps terminal, one failed)
        assert sheets[3].status == BatonSheetStatus.SKIPPED

    def test_cascade_skipped_does_not_satisfy_dispatch(self) -> None:
        """Cascade-SKIPPED sheets must NOT satisfy dependencies for dispatch."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, max_retries=0),
            2: SheetExecutionState(sheet_num=2, max_retries=0),
            3: SheetExecutionState(sheet_num=3, max_retries=0),
        }
        deps = {2: [1], 3: [2]}
        baton.register_job("j1", sheets, deps)

        # Fail sheet 1 -> cascades to 2 and 3
        baton._handle_attempt_result(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="test",
            attempt=1, execution_success=False,
            error_message="failed",
        ))

        # No sheets should be ready (all terminal)
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 0

    def test_user_skipped_still_satisfies_deps(self) -> None:
        """User-skipped sheets (no error_code) must still satisfy deps."""
        from marianne.daemon.baton.events import SheetSkipped

        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, max_retries=0),
            2: SheetExecutionState(sheet_num=2, max_retries=0),
        }
        deps = {2: [1]}
        baton.register_job("j1", sheets, deps)

        # User-skip sheet 1 (no error_code)
        baton._handle_sheet_skipped(SheetSkipped(
            job_id="j1", sheet_num=1, reason="skip_when_command",
        ))

        assert sheets[1].status == BatonSheetStatus.SKIPPED
        assert sheets[1].error_code is None  # User skip has no error code

        # Sheet 2 should be ready (user-skipped satisfies deps)
        ready = baton.get_ready_sheets("j1")
        assert any(s.sheet_num == 2 for s in ready)


class TestSheetDispatchedEvent:
    """DISPATCHED status must be set through an event, not directly."""

    def test_dispatched_event_sets_status(self) -> None:
        """SheetDispatched event should set sheet status to DISPATCHED."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, max_retries=0)}
        baton.register_job("j1", sheets, {})

        baton._handle_sheet_dispatched(SheetDispatched(
            job_id="j1", sheet_num=1, instrument="test",
        ))

        assert sheets[1].status == BatonSheetStatus.DISPATCHED
        assert sheets[1].dispatched_at is not None

    def test_dispatched_event_terminal_guard(self) -> None:
        """SheetDispatched on a terminal sheet is a no-op."""
        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, max_retries=0)}
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.COMPLETED

        baton._handle_sheet_dispatched(SheetDispatched(
            job_id="j1", sheet_num=1, instrument="test",
        ))

        assert sheets[1].status == BatonSheetStatus.COMPLETED


class TestExhaustionPathOrder:
    """Exhaustion handler must try targeted recovery before normal retries."""

    def test_fallback_before_normal_retry(self) -> None:
        """When completion exhausts with fallback AND normal retries available,
        fallback should be tried first."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
                max_completion=1,
                fallback_chain=["gemini-cli"],
            ),
        }
        baton.register_job("j1", sheets, {})
        sheets[1].completion_attempts = 1  # At max_completion

        baton._handle_exhaustion("j1", 1, sheets[1])

        assert sheets[1].instrument_name == "gemini-cli", (
            "Fallback should be tried before normal retry"
        )
        assert sheets[1].status == BatonSheetStatus.PENDING

    def test_escalation_before_normal_retry(self) -> None:
        """When completion exhausts with escalation enabled AND normal retries
        available (but no fallback), escalation should fire."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
                max_completion=1,
            ),
        }
        baton.register_job("j1", sheets, {}, escalation_enabled=True)
        sheets[1].completion_attempts = 1

        baton._handle_exhaustion("j1", 1, sheets[1])

        assert sheets[1].status == BatonSheetStatus.FERMATA, (
            "Escalation should fire before normal retry"
        )

    def test_normal_retry_as_last_resort(self) -> None:
        """When completion exhausts with normal retries but no fallback,
        no healing, no escalation -- normal retry fires."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
                max_completion=1,
            ),
        }
        baton.register_job("j1", sheets, {})
        sheets[1].completion_attempts = 1

        baton._handle_exhaustion("j1", 1, sheets[1])

        assert sheets[1].status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheets[1].normal_attempts == 1


class TestSharedObjectIdentity:
    """Phase 2: baton and _live_states must share the same SheetState objects."""

    def test_register_shares_objects(self) -> None:
        """After register_job with live_sheets, baton's sheet objects must be
        the same Python objects (same id()) as the live_sheets dict values."""
        from marianne.core.checkpoint import CheckpointState, SheetState
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter(event_bus=None, max_concurrent_sheets=4)

        # Simulate what manager.py does
        initial_sheets: dict[int, SheetState] = {
            1: SheetState(sheet_num=1),
            2: SheetState(sheet_num=2),
        }
        initial_state = CheckpointState(
            job_id="test-shared",
            job_name="test-shared",
            total_sheets=2,
            sheets=initial_sheets,
        )

        # Create minimal Sheet entities
        from marianne.core.sheet import Sheet

        sheets = [
            Sheet(num=1, instrument_name="test", instrument_config={},
                  movement=1, voice_count=1, workspace=Path("/tmp/test")),
            Sheet(num=2, instrument_name="test", instrument_config={},
                  movement=1, voice_count=1, workspace=Path("/tmp/test")),
        ]

        adapter.register_job(
            "test-shared",
            sheets,
            dependencies={2: [1]},
            live_sheets=initial_state.sheets,
        )

        # Verify same objects
        baton_sheets = adapter._baton._jobs["test-shared"].sheets
        live_sheets_from_state = initial_state.sheets

        for sheet_num in [1, 2]:
            assert id(baton_sheets[sheet_num]) == id(live_sheets_from_state[sheet_num]), (
                f"Sheet {sheet_num}: baton and live_states must share the same object. "
                f"baton id={id(baton_sheets[sheet_num])}, "
                f"live id={id(live_sheets_from_state[sheet_num])}"
            )

    def test_mutations_visible_across_boundary(self) -> None:
        """Mutations on baton's sheet objects must be visible through live_states."""
        from marianne.core.checkpoint import CheckpointState, SheetState
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter(event_bus=None, max_concurrent_sheets=4)

        initial_sheets: dict[int, SheetState] = {
            1: SheetState(sheet_num=1),
        }
        initial_state = CheckpointState(
            job_id="test-vis",
            job_name="test-vis",
            total_sheets=1,
            sheets=initial_sheets,
        )

        sheets = [
            Sheet(num=1, instrument_name="test", instrument_config={},
                  movement=1, voice_count=1, workspace=Path("/tmp/test")),
        ]

        adapter.register_job(
            "test-vis", sheets, {},
            live_sheets=initial_state.sheets,
        )

        # Mutate through baton
        baton_sheet = adapter._baton._jobs["test-vis"].sheets[1]
        baton_sheet.status = BatonSheetStatus.COMPLETED

        # Verify visible through live_states
        live_sheet = initial_state.sheets[1]
        assert live_sheet.status == BatonSheetStatus.COMPLETED, (
            "Mutation on baton's sheet must be visible through live_states"
        )

        # Verify visible through model_dump (what mzt status calls)
        dumped = initial_state.model_dump(mode="json")
        assert dumped["sheets"]["1"]["status"] == "completed", (
            "model_dump must reflect in-place mutations"
        )
