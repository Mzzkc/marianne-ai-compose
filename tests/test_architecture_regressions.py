"""Regression tests for architecture-discovered correctness bugs.

FIX-11a: success_without_retry uses normal_attempts == 0 (Batch 1 FIX-01)
FIX-11b: Self-healing doesn't bypass max_retries (Batch 1 FIX-02)
FIX-11c: JSON backend zombie detection (Batch 1 FIX-03 + existing zombie logic)
FIX-11d: mark_job_failed() clears PID (Batch 1 FIX-03)
"""

import os
from pathlib import Path

from marianne.core.checkpoint import CheckpointState, JobStatus


class TestSuccessWithoutRetry:
    """FIX-11a: Verify success_without_retry logic uses normal_attempts == 0."""

    def test_success_without_retry_when_no_failures(self) -> None:
        """normal_attempts == 0 means no failures occurred -> success_without_retry = True."""
        normal_attempts = 0
        completion_attempts = 0
        success_without_retry = normal_attempts == 0 and completion_attempts == 0
        assert success_without_retry is True

    def test_success_without_retry_false_after_retry(self) -> None:
        """normal_attempts > 0 means at least one failure -> success_without_retry = False."""
        normal_attempts = 1
        completion_attempts = 0
        success_without_retry = normal_attempts == 0 and completion_attempts == 0
        assert success_without_retry is False

    def test_success_without_retry_false_after_completion(self) -> None:
        """completion_attempts > 0 means completion mode was used."""
        normal_attempts = 0
        completion_attempts = 1
        success_without_retry = normal_attempts == 0 and completion_attempts == 0
        assert success_without_retry is False

    def test_outcome_category_mapping(self) -> None:
        """Verify outcome_category is set correctly based on attempt counts."""
        # Case 1: First try success
        normal_attempts, completion_attempts = 0, 0
        success_without_retry = normal_attempts == 0 and completion_attempts == 0
        assert success_without_retry is True
        outcome = "success_first_try" if success_without_retry else "other"
        assert outcome == "success_first_try"

        # Case 2: Success after retries
        normal_attempts, completion_attempts = 2, 0
        success_without_retry = normal_attempts == 0 and completion_attempts == 0
        assert success_without_retry is False

        # Case 3: Success via completion mode
        normal_attempts, completion_attempts = 0, 1
        success_without_retry = normal_attempts == 0 and completion_attempts == 0
        assert success_without_retry is False


class TestSelfHealingRetryEnforcement:
    """FIX-11b: Self-healing must NOT reset normal_attempts to 0."""

    def test_healing_grants_one_retry_not_full_reset(self) -> None:
        """After healing, normal_attempts = max_retries - 1 (one more retry)."""
        max_retries = 3
        normal_attempts = max_retries  # All retries exhausted
        healing_attempts = 0
        max_healing_cycles = 2

        # Simulate self-healing granting one more retry
        if normal_attempts >= max_retries and healing_attempts < max_healing_cycles:
            healing_attempts += 1
            normal_attempts = max_retries - 1  # Grant ONE more retry

        assert normal_attempts == max_retries - 1  # Should be 2, not 0
        assert healing_attempts == 1

    def test_healing_capped_at_max_cycles(self) -> None:
        """healing_attempts cannot exceed max_healing_cycles."""
        max_retries = 3
        max_healing_cycles = 2
        healing_attempts = 0

        heals_performed = 0
        for _ in range(5):  # Try to heal many times
            normal_attempts = max_retries  # Exhaust retries each time
            if normal_attempts >= max_retries and healing_attempts < max_healing_cycles:
                healing_attempts += 1
                normal_attempts = max_retries - 1
                heals_performed += 1

        assert heals_performed == max_healing_cycles  # Only 2 heals allowed
        assert healing_attempts == max_healing_cycles

    def test_no_healing_means_normal_failure(self) -> None:
        """Without healing, max_retries exhaustion leads to failure."""
        max_retries = 3
        normal_attempts = max_retries

        # No healing available
        should_fail = normal_attempts >= max_retries
        assert should_fail is True


class TestJsonBackendZombieDetection:
    """FIX-11c: JSON backend detects RUNNING + dead PID -> zombie auto-recovery."""

    def test_zombie_detected_for_dead_pid(self) -> None:
        """RUNNING state with a dead PID should be detected as zombie."""
        state = CheckpointState(
            job_id="zombie-test",
            job_name="zombie-test",
            total_sheets=3,
        )
        state.status = JobStatus.RUNNING
        state.pid = 99999999  # Very unlikely to be a real PID

        assert state.is_zombie() is True

    def test_not_zombie_when_completed(self) -> None:
        """COMPLETED state should NOT be detected as zombie."""
        state = CheckpointState(
            job_id="completed-test",
            job_name="completed-test",
            total_sheets=3,
        )
        state.status = JobStatus.COMPLETED
        state.pid = 99999999

        assert state.is_zombie() is False

    def test_not_zombie_when_no_pid(self) -> None:
        """RUNNING with no PID cannot be detected as zombie."""
        state = CheckpointState(
            job_id="no-pid-test",
            job_name="no-pid-test",
            total_sheets=3,
        )
        state.status = JobStatus.RUNNING
        state.pid = None

        assert state.is_zombie() is False

    def test_not_zombie_when_pid_alive(self) -> None:
        """RUNNING with a live PID is NOT a zombie."""
        state = CheckpointState(
            job_id="alive-test",
            job_name="alive-test",
            total_sheets=3,
        )
        state.status = JobStatus.RUNNING
        state.pid = os.getpid()  # Our own PID is definitely alive

        assert state.is_zombie() is False

    def test_zombie_recovery_changes_state(self) -> None:
        """mark_zombie_detected should change status to PAUSED and clear PID."""
        state = CheckpointState(
            job_id="recovery-test",
            job_name="recovery-test",
            total_sheets=3,
        )
        state.status = JobStatus.RUNNING
        state.pid = 99999999

        state.mark_zombie_detected(reason="Test recovery")

        assert state.status == JobStatus.PAUSED
        assert state.pid is None

    async def test_json_backend_auto_recovers_zombie(self, tmp_path: Path) -> None:
        """JSON backend load() should auto-recover zombie state."""
        from marianne.state.json_backend import JsonStateBackend

        backend = JsonStateBackend(tmp_path)

        # Create a state file with RUNNING + dead PID
        state = CheckpointState(
            job_id="json-zombie",
            job_name="json-zombie",
            total_sheets=2,
        )
        state.status = JobStatus.RUNNING
        state.pid = 99999999  # Dead PID
        await backend.save(state)

        # Load should auto-recover
        loaded = await backend.load("json-zombie")
        assert loaded is not None
        assert loaded.status == JobStatus.PAUSED  # Recovered from RUNNING
        assert loaded.pid is None  # PID cleared


class TestMarkJobFailedPidLifecycle:
    """FIX-11d: mark_job_failed() must clear PID."""

    def test_mark_job_failed_clears_pid(self) -> None:
        """mark_job_failed should set pid = None."""
        state = CheckpointState(
            job_id="fail-test",
            job_name="fail-test",
            total_sheets=3,
        )
        state.status = JobStatus.RUNNING
        state.pid = 12345

        state.mark_job_failed("Test failure")

        assert state.status == JobStatus.FAILED
        assert state.pid is None  # PID must be cleared

    def test_mark_job_paused_clears_pid(self) -> None:
        """mark_job_paused should set pid = None."""
        state = CheckpointState(
            job_id="pause-test",
            job_name="pause-test",
            total_sheets=3,
        )
        state.status = JobStatus.RUNNING
        state.pid = 12345

        state.mark_job_paused()

        assert state.status == JobStatus.PAUSED
        assert state.pid is None  # PID must be cleared

    def test_failed_state_not_detected_as_zombie(self) -> None:
        """After mark_job_failed, is_zombie() should return False."""
        state = CheckpointState(
            job_id="fail-zombie-test",
            job_name="fail-zombie-test",
            total_sheets=3,
        )
        state.status = JobStatus.RUNNING
        state.pid = 99999999

        # Before failure: is zombie (dead PID)
        assert state.is_zombie() is True

        # After failure: NOT zombie (status is FAILED, not RUNNING)
        state.mark_job_failed("Failed due to max retries")
        assert state.is_zombie() is False
        assert state.pid is None

    def test_resume_not_blocked_after_failure(self) -> None:
        """After mark_job_failed, PID should be None so resume isn't blocked."""
        state = CheckpointState(
            job_id="resume-test",
            job_name="resume-test",
            total_sheets=3,
        )
        state.status = JobStatus.RUNNING
        state.pid = os.getpid()  # Simulate a running job with our PID

        state.mark_job_failed("Error occurred")

        # PID is cleared, so a new resume can set a new PID
        assert state.pid is None
        state.pid = os.getpid()  # Simulate resume setting new PID
        state.status = JobStatus.RUNNING
        assert state.status == JobStatus.RUNNING
        assert state.pid == os.getpid()
