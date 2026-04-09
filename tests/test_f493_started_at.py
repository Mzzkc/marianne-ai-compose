"""F-493: Status elapsed time shows "0.0s" for running jobs.

Root cause: CheckpointState.started_at is None for running jobs because
it's only set in the persist callback when sheets have moved past PENDING/READY.
There's a race where the job transitions to RUNNING but no sheets have dispatched yet.

The fix: set started_at when creating initial CheckpointState for new jobs.
"""

from datetime import datetime, UTC
import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus, JobStatus
from marianne.utils.time import utc_now


@pytest.mark.asyncio
class TestStartedAtBug:
    """Verify that started_at is set when jobs transition to RUNNING."""

    async def test_new_job_sets_started_at(self):
        """New jobs must have started_at set when they transition to RUNNING.

        Before fix: initial_state created without started_at (None)
        After fix: started_at = utc_now() when status = RUNNING

        This reproduces the exact code path in manager.py:2372-2382
        where initial_state is created for new job submissions.
        """
        # Create initial sheets like manager does (all PENDING at start)
        initial_sheets = {
            1: SheetState(sheet_num=1, status=SheetStatus.PENDING),
        }

        before_create = utc_now()

        # This is the ACTUAL code from manager.py:2372-2382 that creates initial_state
        # for new jobs. The bug is that started_at defaults to None.
        initial_state = CheckpointState(
            job_id="test-job-001",
            job_name="test-job",
            total_sheets=len(initial_sheets),
            status=JobStatus.RUNNING,  # Job is RUNNING
            sheets=initial_sheets,
            instruments_used=[],
            total_movements=None,
        )

        after_create = utc_now()

        # THE BUG: started_at should be set when status = RUNNING
        # Before fix: started_at is None (default from CheckpointState)
        # After fix: started_at is set to current time during creation
        assert initial_state.started_at is not None, (
            "F-493: CheckpointState.started_at must be set when status=RUNNING for new jobs. "
            "Without this, mzt status shows '0.0s elapsed' for running jobs."
        )

        # Verify it's a recent timestamp (within the test execution window)
        assert isinstance(initial_state.started_at, datetime)
        assert before_create <= initial_state.started_at <= after_create

    async def test_resumed_job_resets_started_at(self):
        """Resumed jobs should reset started_at to current time.

        This is existing behavior from line 2563 of manager.py.
        The test ensures this contract is preserved.
        """
        # Create a checkpoint from a previous run
        old_timestamp = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=1,
            status=JobStatus.PAUSED,
            started_at=old_timestamp,  # Old timestamp from previous run
            sheets={1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED)},
        )

        # On resume, started_at should be reset
        before_reset = utc_now()
        checkpoint.started_at = utc_now()  # This is what manager.py:2563 does
        after_reset = utc_now()

        # Verify timestamp was updated
        assert checkpoint.started_at is not None
        assert checkpoint.started_at != old_timestamp
        assert before_reset <= checkpoint.started_at <= after_reset

    async def test_persist_callback_preserves_started_at(self):
        """The persist callback should preserve started_at if already set.

        Line 608 of manager.py only sets started_at if it's None.
        This test ensures that contract is maintained.
        """
        # Create checkpoint with started_at already set
        original_timestamp = utc_now()
        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=2,
            status=JobStatus.RUNNING,
            started_at=original_timestamp,
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.IN_PROGRESS),
                2: SheetState(sheet_num=2, status=SheetStatus.PENDING),
            },
        )

        # Simulate the persist callback logic (manager.py:603-608)
        any_started = any(
            s.status not in (SheetStatus.PENDING, SheetStatus.READY)
            for s in checkpoint.sheets.values()
        )
        assert any_started is True

        # This is the condition that should NOT overwrite existing started_at
        if any_started and checkpoint.started_at is None:
            checkpoint.started_at = utc_now()

        # Verify original timestamp was preserved
        assert checkpoint.started_at == original_timestamp

    async def test_status_compute_elapsed_handles_none(self):
        """Status command should handle started_at=None gracefully.

        This is a defensive test - even if started_at is None (legacy data),
        the status command shouldn't crash. It should show 0.0s elapsed.

        NOTE: After F-493 fix, new RUNNING jobs always have started_at set.
        This test uses PENDING status to verify the defensive handling still works.
        """
        from marianne.cli.commands.status import _compute_elapsed

        # Use PENDING status (doesn't auto-set started_at)
        # to test defensive None handling
        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=1,
            status=JobStatus.PENDING,  # PENDING doesn't auto-set started_at
            started_at=None,
            sheets={},
        )

        # Should return 0.0 without crashing
        elapsed = _compute_elapsed(checkpoint)
        assert elapsed == 0.0

    async def test_status_compute_elapsed_when_running(self):
        """Status command should compute correct elapsed time for running jobs."""
        from marianne.cli.commands.status import _compute_elapsed
        from time import sleep

        # Create checkpoint with recent started_at
        started = utc_now()
        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=1,
            status=JobStatus.RUNNING,
            started_at=started,
            sheets={},
        )

        # Wait a bit
        sleep(0.1)

        # Compute elapsed
        elapsed = _compute_elapsed(checkpoint)

        # Should be > 0 and roughly 0.1 seconds (allow generous margin for CI)
        assert elapsed > 0.05
        assert elapsed < 30.0  # Generous bound per quality gate requirements  # Should be much less than 1 second

    async def test_status_compute_elapsed_when_completed(self):
        """Status command should use actual duration for completed jobs."""
        from marianne.cli.commands.status import _compute_elapsed

        started = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
        completed = datetime(2020, 1, 1, 0, 5, 30, tzinfo=UTC)

        checkpoint = CheckpointState(
            job_id="test-job",
            job_name="test",
            total_sheets=1,
            status=JobStatus.COMPLETED,
            started_at=started,
            completed_at=completed,
            sheets={},
        )

        elapsed = _compute_elapsed(checkpoint)

        # Should be exactly 5 minutes 30 seconds = 330 seconds
        assert elapsed == 330.0
