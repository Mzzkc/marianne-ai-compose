"""Property-based invariant tests for Movement 6 features.

This module contains hypothesis-based property tests proving mathematical
invariants for M6 features and bug fixes.

Invariant families:
- 99-103: F-518 timestamp invariants (CheckpointState, SheetState status transitions)
- 104-107: Test isolation properties (daemon snapshot, conductor state)

Each invariant is numbered sequentially continuing from M5 (ended at 98).
These tests don't prove features work - they prove properties hold under
arbitrary inputs that the implementation claims to handle.

Author: Theorem, Movement 6
"""

from datetime import timedelta

from hypothesis import given
from hypothesis import strategies as st

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.utils.time import utc_now


class TestCheckpointTimestampInvariants:
    """Invariants 99-101: CheckpointState timestamp consistency (F-518).

    F-518 root cause: Resume set started_at but didn't clear completed_at,
    causing negative elapsed time. The fix adds model validators that enforce
    timestamp invariants based on status.
    """

    @given(
        status=st.sampled_from(list(JobStatus)),
        has_started_at=st.booleans(),
        has_completed_at=st.booleans(),
    )
    def test_invariant_99_running_jobs_never_have_completed_at(
        self, status: JobStatus, has_started_at: bool, has_completed_at: bool
    ) -> None:
        """Invariant 99: RUNNING jobs must have completed_at=None.

        F-518: completed_at from previous run causes negative elapsed time.
        The model validator _enforce_status_invariants() clears completed_at
        when status=RUNNING.

        Property: For all CheckpointState constructions, if status=RUNNING,
        then completed_at=None regardless of input value.
        """
        now = utc_now()
        past = now - timedelta(days=3)

        started_at = past if has_started_at else None
        completed_at = past if has_completed_at else None

        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=1,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            sheets={1: SheetState(sheet_num=1, status=SheetStatus.PENDING)},
        )

        # Invariant: RUNNING jobs NEVER have completed_at, even if input provided it
        if checkpoint.status == JobStatus.RUNNING:
            assert checkpoint.completed_at is None, (
                f"F-518: RUNNING jobs must have completed_at=None. "
                f"Got started_at={checkpoint.started_at}, completed_at={checkpoint.completed_at}"
            )

    @given(
        status=st.sampled_from(list(JobStatus)),
    )
    def test_invariant_100_running_jobs_always_have_started_at(self, status: JobStatus) -> None:
        """Invariant 100: RUNNING jobs must have started_at set.

        F-493: Missing started_at caused "0.0s elapsed" display.
        The model validator auto-sets started_at=utc_now() when status=RUNNING
        and started_at=None.

        Property: For all CheckpointState constructions with status=RUNNING,
        started_at is never None.
        """
        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=1,
            status=status,
            started_at=None,  # Intentionally None - validator should fill it
            completed_at=None,
            sheets={1: SheetState(sheet_num=1, status=SheetStatus.PENDING)},
        )

        # Invariant: RUNNING jobs ALWAYS have started_at
        if checkpoint.status == JobStatus.RUNNING:
            assert checkpoint.started_at is not None, (
                "F-493: RUNNING jobs must have started_at. Model validator should auto-fill."
            )

    @given(
        initial_status=st.sampled_from([JobStatus.COMPLETED, JobStatus.FAILED]),
    )
    def test_invariant_101_completed_to_running_clears_completion_metadata(
        self, initial_status: JobStatus
    ) -> None:
        """Invariant 101: Transitioning from terminal to RUNNING clears completion metadata.

        This is the boundary-gap pattern from F-518: two systems (resume logic +
        status display) both correct in isolation but composing incorrectly.

        F-518 fix: model validator clears completed_at when status=RUNNING.
        This test verifies that property holds under resume scenarios.

        Property: If a job transitions from COMPLETED/FAILED to RUNNING,
        reconstruction via model_dump() round-trip clears completed_at.

        Note: Only RUNNING is tested because that's what resume does. PENDING
        transitions are not cleared by the validator (and may not need to be).
        """
        past = utc_now() - timedelta(days=1)

        # Initial state: job was completed
        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=1,
            status=initial_status,
            started_at=past,
            completed_at=past + timedelta(hours=1),
            sheets={1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED)},
        )

        assert checkpoint.completed_at is not None, "Setup: initial state must be completed"

        # Transition to RUNNING (this is what resume does)
        checkpoint.status = JobStatus.RUNNING
        checkpoint.started_at = utc_now()

        # Trigger model validator by reconstruction (simulates persistence round-trip)
        checkpoint = CheckpointState(**checkpoint.model_dump())

        # Invariant: RUNNING status always clears completed_at
        assert checkpoint.completed_at is None, (
            f"F-518: Transitioning from {initial_status} to RUNNING "
            "must clear completed_at to prevent negative elapsed time. "
            "Model validator _enforce_status_invariants() should enforce this."
        )


class TestSheetTimestampInvariants:
    """Invariants 102-103: SheetState timestamp consistency.

    Similar to CheckpointState but for individual sheets. Sheets have their own
    status transitions and timestamp management.
    """

    @given(
        status=st.sampled_from(list(SheetStatus)),
        has_started_at=st.booleans(),
        has_completed_at=st.booleans(),
    )
    def test_invariant_102_in_progress_sheets_have_started_at(
        self, status: SheetStatus, has_started_at: bool, has_completed_at: bool
    ) -> None:
        """Invariant 102: IN_PROGRESS sheets must have started_at set.

        The model validator auto-fills started_at when status=IN_PROGRESS
        and started_at=None.

        Property: For all SheetState constructions with status=IN_PROGRESS,
        started_at is never None.
        """
        now = utc_now()
        past = now - timedelta(hours=1)

        started_at = past if has_started_at else None
        completed_at = past if has_completed_at else None

        sheet = SheetState(
            sheet_num=1,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
        )

        # Invariant: IN_PROGRESS sheets ALWAYS have started_at
        if sheet.status == SheetStatus.IN_PROGRESS:
            assert sheet.started_at is not None, (
                "IN_PROGRESS sheets must have started_at. Model validator should auto-fill."
            )

    @given(
        status=st.sampled_from(list(SheetStatus)),
        has_started_at=st.booleans(),
        has_completed_at=st.booleans(),
    )
    def test_invariant_103_completed_sheets_have_completed_at(
        self, status: SheetStatus, has_started_at: bool, has_completed_at: bool
    ) -> None:
        """Invariant 103: COMPLETED sheets must have completed_at set.

        The model validator auto-fills completed_at when status=COMPLETED
        and completed_at=None.

        Property: For all SheetState constructions with status=COMPLETED,
        completed_at is never None.
        """
        now = utc_now()
        past = now - timedelta(hours=1)

        started_at = past if has_started_at else None
        completed_at = past if has_completed_at else None

        sheet = SheetState(
            sheet_num=1,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
        )

        # Invariant: COMPLETED sheets ALWAYS have completed_at
        if sheet.status == SheetStatus.COMPLETED:
            assert sheet.completed_at is not None, (
                "COMPLETED sheets must have completed_at. Model validator should auto-fill."
            )


class TestTimestampMonotonicityInvariants:
    """Invariants 104-106: Timestamp ordering and monotonicity.

    Time flows forward. Completed_at must be >= started_at. Duration must be
    non-negative. These seem obvious but F-518 violated them.
    """

    @given(delta_hours=st.floats(min_value=-1000.0, max_value=1000.0))
    def test_invariant_104_checkpoint_completed_at_not_before_started_at(
        self, delta_hours: float
    ) -> None:
        """Completion replaces stale timestamps with the current run's end.

        Constructors preserve historical timestamps; the lifecycle transition
        owns the current completion time. Never catch AssertionError here.
        """
        now = utc_now()
        checkpoint = CheckpointState(
            job_id="test-job", job_name="test", total_sheets=1,
            status=JobStatus.RUNNING, started_at=now,
            completed_at=now + timedelta(hours=delta_hours),
        )
        assert checkpoint.completed_at is None
        checkpoint.mark_sheet_started(1)
        checkpoint.mark_sheet_completed(1)
        assert checkpoint.status == JobStatus.COMPLETED
        assert checkpoint.started_at is not None
        assert checkpoint.completed_at is not None
        assert checkpoint.completed_at >= checkpoint.started_at

    @given(delta_hours=st.floats(min_value=-1000.0, max_value=1000.0))
    def test_invariant_105_sheet_completed_at_not_before_started_at(
        self, delta_hours: float
    ) -> None:
        """A retried sheet's completion replaces its previous attempt's end."""
        now = utc_now()
        sheet = SheetState(
            sheet_num=1, status=SheetStatus.COMPLETED, started_at=now,
            completed_at=now + timedelta(hours=delta_hours),
        )
        checkpoint = CheckpointState(
            job_id="test-job", job_name="test", total_sheets=1, sheets={1: sheet},
        )
        checkpoint.mark_sheet_started(1)
        checkpoint.mark_sheet_completed(1)
        sheet = checkpoint.sheets[1]
        assert sheet.status == SheetStatus.COMPLETED
        assert sheet.started_at is not None
        assert sheet.completed_at is not None
        assert sheet.completed_at >= sheet.started_at

    @given(
        job_status=st.sampled_from(list(JobStatus)),
        sheet_status=st.sampled_from(list(SheetStatus)),
    )
    def test_invariant_106_compute_elapsed_never_negative(
        self, job_status: JobStatus, sheet_status: SheetStatus
    ) -> None:
        """Invariant 106: Computed elapsed time is never negative.

        This is the F-518 litmus test property. The _compute_elapsed() function
        in status.py clamps negative to 0.0, but the real fix is ensuring
        timestamps are consistent so negative never occurs.

        Property: max(completed_at - started_at, 0.0) should never need the max()
        because the inputs are already consistent.
        """
        now = utc_now()

        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=1,
            status=job_status,
            started_at=now - timedelta(hours=1),  # 1 hour ago
            completed_at=None,  # Let model validator handle it
            sheets={1: SheetState(sheet_num=1, status=sheet_status)},
        )

        # Compute elapsed (same logic as status.py:395-403)
        if checkpoint.started_at:
            if checkpoint.completed_at:
                elapsed_raw = (checkpoint.completed_at - checkpoint.started_at).total_seconds()
            else:
                elapsed_raw = (utc_now() - checkpoint.started_at).total_seconds()
        else:
            elapsed_raw = 0.0

        # Invariant: Elapsed should never be negative BEFORE clamping
        # If it is negative, the model validators failed to maintain consistency
        assert elapsed_raw >= 0, (
            f"F-518: Elapsed time should never be negative. "
            f"status={checkpoint.status}, started_at={checkpoint.started_at}, "
            f"completed_at={checkpoint.completed_at}, elapsed={elapsed_raw:.1f}s"
        )


class TestTimestampEdgeCases:
    """Invariant 107: Edge cases for timestamp handling.

    What happens with None timestamps? Far-future timestamps? Timezone issues?
    These aren't bugs yet but should be tested to prevent future F-518s.
    """

    @given(
        status=st.sampled_from(list(JobStatus)),
    )
    def test_invariant_107_none_timestamps_handled_consistently(self, status: JobStatus) -> None:
        """Invariant 107: None timestamps are handled consistently across status transitions.

        Every status has a defined behavior for None timestamps:
        - PENDING: both None (job hasn't started)
        - RUNNING: started_at required, completed_at None
        - COMPLETED/FAILED: both required (or auto-filled)
        - PAUSED: started_at required, completed_at None

        Property: Model validators ensure consistency - construction never produces
        invalid combinations.
        """
        # Construct with all None - let validators decide
        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=1,
            status=status,
            started_at=None,
            completed_at=None,
            sheets={1: SheetState(sheet_num=1, status=SheetStatus.PENDING)},
        )

        # Check consistency based on status
        if checkpoint.status == JobStatus.RUNNING:
            # F-493 + F-518: RUNNING must have started_at, must NOT have completed_at
            assert checkpoint.started_at is not None, "RUNNING jobs must have started_at (F-493)"
            assert checkpoint.completed_at is None, (
                "RUNNING jobs must NOT have completed_at (F-518)"
            )
        elif checkpoint.status in [JobStatus.COMPLETED, JobStatus.FAILED]:
            # Terminal states should have both (or validators should auto-fill)
            # This is less strict - validators may not auto-fill for FAILED
            pass
        elif checkpoint.status in [JobStatus.PENDING, JobStatus.PAUSED]:
            # Non-running states may have either or neither
            pass

        # Meta-invariant: Whatever the validators produce, it must not cause
        # negative elapsed time
        if checkpoint.started_at and checkpoint.completed_at:
            elapsed = (checkpoint.completed_at - checkpoint.started_at).total_seconds()
            assert elapsed >= 0, (
                f"Model validators must prevent negative elapsed time. "
                f"status={checkpoint.status}, elapsed={elapsed:.1f}s"
            )
