"""Tests for mozart.core.checkpoint module."""

from datetime import datetime

import pydantic
import pytest

from marianne.core.checkpoint import (
    MAX_OUTPUT_CAPTURE_BYTES,
    CheckpointState,
    JobStatus,
    SheetState,
    SheetStatus,
)
from marianne.core.errors.codes import ErrorCategory


class TestSheetStatus:
    """Tests for SheetStatus enum."""

    def test_status_values(self):
        """Test all status values exist."""
        assert SheetStatus.PENDING == "pending"
        assert SheetStatus.IN_PROGRESS == "in_progress"
        assert SheetStatus.COMPLETED == "completed"
        assert SheetStatus.FAILED == "failed"
        assert SheetStatus.SKIPPED == "skipped"


class TestSheetState:
    """Tests for SheetState model."""

    def test_default_state(self):
        """Test default sheet state."""
        state = SheetState(sheet_num=1)
        assert state.sheet_num == 1
        assert state.status == SheetStatus.PENDING
        assert state.started_at is None
        assert state.completed_at is None
        assert state.attempt_count == 0
        assert state.validation_passed is None
        assert state.completion_attempts == 0

    def test_learning_fields(self):
        """Test learning metadata fields (Phase 1)."""
        state = SheetState(
            sheet_num=1,
            confidence_score=0.85,
            success_without_retry=True,
            outcome_category="success_first_try",
            learned_patterns=["pattern1", "pattern2"],
        )
        assert state.confidence_score == 0.85
        assert state.success_without_retry is True
        assert state.outcome_category == "success_first_try"
        assert len(state.learned_patterns) == 2

    def test_confidence_score_bounds(self):
        """Test confidence score must be between 0 and 1."""
        # Valid values
        SheetState(sheet_num=1, confidence_score=0.0)
        SheetState(sheet_num=1, confidence_score=1.0)
        SheetState(sheet_num=1, confidence_score=0.5)

        # Invalid values should raise validation error
        with pytest.raises(pydantic.ValidationError):
            SheetState(sheet_num=1, confidence_score=1.5)

        with pytest.raises(pydantic.ValidationError):
            SheetState(sheet_num=1, confidence_score=-0.1)


class TestOutputCapture:
    """Tests for raw output capture functionality (Task 1: Raw Output Capture)."""

    def test_output_capture_fields_default(self):
        """Test output capture fields have correct defaults."""
        state = SheetState(sheet_num=1)
        assert state.stdout_tail is None
        assert state.stderr_tail is None
        assert state.output_truncated is False

    def test_capture_output_small_strings(self):
        """Test capturing small output strings without truncation."""
        state = SheetState(sheet_num=1)
        stdout = "Hello, World!"
        stderr = "Some warning message"

        state.capture_output(stdout, stderr)

        assert state.stdout_tail == stdout
        assert state.stderr_tail == stderr
        assert state.output_truncated is False

    def test_capture_output_empty_strings(self):
        """Test capturing empty output strings."""
        state = SheetState(sheet_num=1)

        state.capture_output("", "")

        assert state.stdout_tail is None
        assert state.stderr_tail is None
        assert state.output_truncated is False

    def test_capture_output_only_stdout(self):
        """Test capturing when only stdout has content."""
        state = SheetState(sheet_num=1)

        state.capture_output("stdout content", "")

        assert state.stdout_tail == "stdout content"
        assert state.stderr_tail is None
        assert state.output_truncated is False

    def test_capture_output_only_stderr(self):
        """Test capturing when only stderr has content."""
        state = SheetState(sheet_num=1)

        state.capture_output("", "stderr content")

        assert state.stdout_tail is None
        assert state.stderr_tail == "stderr content"
        assert state.output_truncated is False

    def test_capture_output_truncation_stdout(self):
        """Test stdout truncation when exceeding max bytes."""
        state = SheetState(sheet_num=1)
        # Create output larger than 10KB (default limit)
        large_stdout = "x" * (MAX_OUTPUT_CAPTURE_BYTES + 1000)
        small_stderr = "small"

        state.capture_output(large_stdout, small_stderr)

        # stdout should be truncated to last 10KB
        assert state.stdout_tail is not None
        assert len(state.stdout_tail.encode("utf-8")) == MAX_OUTPUT_CAPTURE_BYTES
        # stderr should be intact
        assert state.stderr_tail == small_stderr
        # Truncation flag should be set
        assert state.output_truncated is True

    def test_capture_output_truncation_stderr(self):
        """Test stderr truncation when exceeding max bytes."""
        state = SheetState(sheet_num=1)
        small_stdout = "small"
        large_stderr = "e" * (MAX_OUTPUT_CAPTURE_BYTES + 500)

        state.capture_output(small_stdout, large_stderr)

        # stdout should be intact
        assert state.stdout_tail == small_stdout
        # stderr should be truncated
        assert state.stderr_tail is not None
        assert len(state.stderr_tail.encode("utf-8")) == MAX_OUTPUT_CAPTURE_BYTES
        # Truncation flag should be set
        assert state.output_truncated is True

    def test_capture_output_truncation_both(self):
        """Test both stdout and stderr truncation."""
        state = SheetState(sheet_num=1)
        large_stdout = "o" * (MAX_OUTPUT_CAPTURE_BYTES * 2)
        large_stderr = "e" * (MAX_OUTPUT_CAPTURE_BYTES * 2)

        state.capture_output(large_stdout, large_stderr)

        # Both should be truncated
        assert state.stdout_tail is not None
        assert state.stderr_tail is not None
        assert len(state.stdout_tail.encode("utf-8")) == MAX_OUTPUT_CAPTURE_BYTES
        assert len(state.stderr_tail.encode("utf-8")) == MAX_OUTPUT_CAPTURE_BYTES
        assert state.output_truncated is True

    def test_capture_output_preserves_tail(self):
        """Test that truncation preserves the tail (end) of output."""
        state = SheetState(sheet_num=1)
        # Create distinctive start and end content
        # Use a small limit for easier testing
        limit = 100
        content = "START_MARKER" + ("x" * 200) + "END_MARKER"

        state.capture_output(content, "", max_bytes=limit)

        # Should contain the end, not the start
        assert state.stdout_tail is not None
        assert "END_MARKER" in state.stdout_tail
        assert "START_MARKER" not in state.stdout_tail
        assert state.output_truncated is True

    def test_capture_output_custom_max_bytes(self):
        """Test capture with custom max_bytes limit."""
        state = SheetState(sheet_num=1)
        content = "A" * 500  # 500 bytes

        # Use smaller limit
        state.capture_output(content, "", max_bytes=100)

        assert state.stdout_tail is not None
        assert len(state.stdout_tail.encode("utf-8")) == 100
        assert state.output_truncated is True

    def test_capture_output_unicode_content(self):
        """Test capturing unicode/multi-byte characters."""
        state = SheetState(sheet_num=1)
        # Mix of ASCII and multi-byte UTF-8 characters
        unicode_content = "Hello 世界 🌍 Здравствуй мир"

        state.capture_output(unicode_content, unicode_content)

        assert state.stdout_tail == unicode_content
        assert state.stderr_tail == unicode_content
        assert state.output_truncated is False

    def test_capture_output_unicode_truncation(self):
        """Test truncation handles unicode character boundaries correctly."""
        state = SheetState(sheet_num=1)
        # Create content with multi-byte chars that might get split
        # Each emoji is 4 bytes, so 30 emojis = 120 bytes
        emoji_content = "🎉" * 30  # 120 bytes total

        # Truncate at 50 bytes - may split in middle of emoji
        state.capture_output(emoji_content, "", max_bytes=50)

        assert state.stdout_tail is not None
        # Should be valid UTF-8 (no decode errors when encoding back)
        state.stdout_tail.encode("utf-8")
        # The result may be slightly larger than max_bytes due to replacement
        # characters (U+FFFD = 3 bytes) for split multi-byte sequences.
        # This is expected and correct - the output is readable and valid UTF-8.
        # The important thing is it's close to max_bytes, not that the full
        # content (120 bytes) was preserved.
        assert len(state.stdout_tail.encode("utf-8")) < 60  # Reasonable upper bound
        assert state.output_truncated is True

    def test_capture_output_overwrites_previous(self):
        """Test that capture_output overwrites previous captured output."""
        state = SheetState(sheet_num=1)

        # First capture
        state.capture_output("first stdout", "first stderr")
        assert state.stdout_tail == "first stdout"
        assert state.stderr_tail == "first stderr"

        # Second capture - should overwrite
        state.capture_output("second stdout", "second stderr")
        assert state.stdout_tail == "second stdout"
        assert state.stderr_tail == "second stderr"

    def test_capture_output_serialization(self):
        """Test that captured output survives JSON serialization."""
        state = SheetState(sheet_num=1)
        state.capture_output("stdout content", "stderr content")

        # Serialize and deserialize
        data = state.model_dump(mode="json")
        loaded = SheetState.model_validate(data)

        assert loaded.stdout_tail == "stdout content"
        assert loaded.stderr_tail == "stderr content"
        assert loaded.output_truncated is False

    def test_capture_output_serialization_with_truncation(self):
        """Test that truncated output survives serialization correctly."""
        state = SheetState(sheet_num=1)
        large_content = "x" * (MAX_OUTPUT_CAPTURE_BYTES + 1000)
        state.capture_output(large_content, "small")

        # Serialize and deserialize
        data = state.model_dump(mode="json")
        loaded = SheetState.model_validate(data)

        # Truncated state should be preserved
        assert loaded.output_truncated is True
        assert loaded.stdout_tail is not None
        assert len(loaded.stdout_tail.encode("utf-8")) == MAX_OUTPUT_CAPTURE_BYTES
        assert loaded.stderr_tail == "small"

    def test_max_output_capture_bytes_constant(self):
        """Test that MAX_OUTPUT_CAPTURE_BYTES is 10KB."""
        assert MAX_OUTPUT_CAPTURE_BYTES == 51200  # 50KB

    def test_backwards_compatibility_missing_fields(self):
        """Test loading old state without output capture fields."""
        # Simulate old state data without new fields
        old_data = {
            "sheet_num": 1,
            "status": "completed",
            "attempt_count": 1,
            # No stdout_tail, stderr_tail, or output_truncated
        }

        # Should load successfully with defaults
        loaded = SheetState.model_validate(old_data)
        assert loaded.stdout_tail is None
        assert loaded.stderr_tail is None
        assert loaded.output_truncated is False


class TestJobStatus:
    """Tests for JobStatus enum."""

    def test_status_values(self):
        """Test all job status values exist."""
        assert JobStatus.PENDING == "pending"
        assert JobStatus.RUNNING == "running"
        assert JobStatus.COMPLETED == "completed"
        assert JobStatus.FAILED == "failed"
        assert JobStatus.PAUSED == "paused"
        assert JobStatus.CANCELLED == "cancelled"


class TestCheckpointState:
    """Tests for CheckpointState model."""

    def _create_state(
        self, job_id: str = "test-job", job_name: str = "Test", total_sheets: int = 3
    ) -> CheckpointState:
        """Helper to create a CheckpointState."""
        state = CheckpointState(
            job_id=job_id,
            job_name=job_name,
            total_sheets=total_sheets,
            sheets={i: SheetState(sheet_num=i) for i in range(1, total_sheets + 1)},
        )
        return state

    def test_create_state(self):
        """Test creating checkpoint state for a new job."""
        state = self._create_state(
            job_id="test-job-123",
            job_name="Test Job",
            total_sheets=5,
        )
        assert state.job_id == "test-job-123"
        assert state.job_name == "Test Job"
        assert state.total_sheets == 5
        assert state.status == JobStatus.PENDING
        assert state.last_completed_sheet == 0
        assert len(state.sheets) == 5

    def test_sheets_initialized(self):
        """Test all sheets are initialized with PENDING status."""
        state = self._create_state(total_sheets=3)
        for i in range(1, 4):
            assert i in state.sheets
            assert state.sheets[i].status == SheetStatus.PENDING

    def test_get_next_sheet_initial(self):
        """Test get_next_sheet returns 1 for new job."""
        state = self._create_state(total_sheets=3)
        assert state.get_next_sheet() == 1

    def test_get_next_sheet_after_completion(self):
        """Test get_next_sheet returns correct sheet after completion."""
        state = self._create_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        assert state.get_next_sheet() == 2

    def test_get_next_sheet_all_completed(self):
        """Test get_next_sheet returns None when all complete."""
        state = self._create_state(total_sheets=2)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        state.mark_sheet_started(2)
        state.mark_sheet_completed(2)
        assert state.get_next_sheet() is None

    def test_mark_sheet_started(self):
        """Test marking a sheet as started."""
        state = self._create_state(total_sheets=3)
        state.mark_sheet_started(1)
        assert state.sheets[1].status == SheetStatus.IN_PROGRESS
        assert state.sheets[1].started_at is not None
        assert state.sheets[1].attempt_count == 1
        assert state.current_sheet == 1
        assert state.status == JobStatus.RUNNING

    def test_mark_sheet_completed(self):
        """Test marking a sheet as completed."""
        state = self._create_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1, validation_passed=True)

        assert state.sheets[1].status == SheetStatus.COMPLETED
        assert state.sheets[1].completed_at is not None
        assert state.sheets[1].validation_passed is True
        assert state.last_completed_sheet == 1

    def test_mark_sheet_failed(self):
        """Test marking a sheet as failed."""
        state = self._create_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_failed(1, error_message="Test error", error_category="fatal")

        assert state.sheets[1].status == SheetStatus.FAILED
        assert state.sheets[1].error_message == "Test error"
        assert state.sheets[1].error_category == ErrorCategory.FATAL

    def test_mark_sheet_failed_with_signal_fields(self):
        """Test marking a sheet as failed with signal differentiation fields."""
        import signal as sig

        state = self._create_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_failed(
            sheet_num=1,
            error_message="Process killed by SIGTERM",
            error_category="signal",
            exit_code=None,  # No exit code when killed by signal
            exit_signal=sig.SIGTERM,
            exit_reason="killed",
            execution_duration_seconds=15.5,
        )

        sheet = state.sheets[1]
        assert sheet.status == SheetStatus.FAILED
        assert sheet.error_message == "Process killed by SIGTERM"
        assert sheet.error_category == "signal"
        assert sheet.exit_code is None
        assert sheet.exit_signal == sig.SIGTERM
        assert sheet.exit_reason == "killed"
        assert sheet.execution_duration_seconds == 15.5

    def test_mark_sheet_failed_with_timeout(self):
        """Test marking a sheet as failed due to timeout."""
        import signal as sig

        state = self._create_state(total_sheets=3)
        state.mark_sheet_started(1)
        state.mark_sheet_failed(
            sheet_num=1,
            error_message="Command timed out after 30s",
            error_category="timeout",
            exit_code=None,
            exit_signal=sig.SIGKILL,
            exit_reason="timeout",
            execution_duration_seconds=30.0,
        )

        sheet = state.sheets[1]
        assert sheet.exit_signal == sig.SIGKILL
        assert sheet.exit_reason == "timeout"
        assert sheet.error_category == "timeout"

    def test_mark_sheet_failed_backwards_compatible(self):
        """Test that mark_sheet_failed works without new optional fields."""
        state = self._create_state(total_sheets=3)
        state.mark_sheet_started(1)
        # Call without new fields (backwards compatible)
        state.mark_sheet_failed(1, "Error occurred")

        sheet = state.sheets[1]
        assert sheet.status == SheetStatus.FAILED
        assert sheet.error_message == "Error occurred"
        # New fields should be None (defaults)
        assert sheet.exit_signal is None
        assert sheet.exit_reason is None
        assert sheet.execution_duration_seconds is None

    def test_job_completes_when_all_sheets_done(self):
        """Test job status updates to COMPLETED when all sheets are done."""
        state = self._create_state(total_sheets=2)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        assert state.status == JobStatus.RUNNING

        state.mark_sheet_started(2)
        state.mark_sheet_completed(2)
        assert state.status == JobStatus.COMPLETED
        assert state.completed_at is not None

    def test_retry_tracking(self):
        """Test retry attempt tracking."""
        state = self._create_state(total_sheets=1)
        # First attempt
        state.mark_sheet_started(1)
        assert state.sheets[1].attempt_count == 1

        # Retry
        state.mark_sheet_started(1)
        assert state.sheets[1].attempt_count == 2

    def test_get_progress(self):
        """Test progress tracking."""
        state = self._create_state(total_sheets=5)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        state.mark_sheet_started(2)
        state.mark_sheet_completed(2)

        completed, total = state.get_progress()
        assert completed == 2
        assert total == 5

    def test_get_progress_percent(self):
        """Test progress percentage calculation."""
        state = self._create_state(total_sheets=4)
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)
        state.mark_sheet_started(2)
        state.mark_sheet_completed(2)

        assert state.get_progress_percent() == 50.0


class TestCheckpointStateSerialization:
    """Tests for CheckpointState serialization."""

    def test_to_dict(self):
        """Test converting state to dictionary."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=2,
            sheets={i: SheetState(sheet_num=i) for i in range(1, 3)},
        )
        data = state.model_dump()
        assert data["job_id"] == "test-job"
        assert data["total_sheets"] == 2
        assert "sheets" in data

    def test_from_dict(self):
        """Test loading state from dictionary."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=2,
            sheets={i: SheetState(sheet_num=i) for i in range(1, 3)},
        )
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1)

        data = state.model_dump()
        loaded = CheckpointState.model_validate(data)

        assert loaded.job_id == state.job_id
        assert loaded.last_completed_sheet == 1
        assert loaded.sheets[1].status == SheetStatus.COMPLETED


class TestConfigSnapshot:
    """Tests for config_snapshot functionality (Task 3: Config Storage)."""

    def test_config_snapshot_field_exists(self):
        """Test that config_snapshot field is available on CheckpointState."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=2,
        )
        assert state.config_snapshot is None
        assert state.config_path is None

    def test_config_snapshot_can_store_dict(self):
        """Test that config_snapshot can store a config dictionary."""
        config_data = {
            "name": "test-job",
            "workspace": "/tmp/workspace",
            "sheet": {"size": 5, "total_items": 10},
            "prompt": {"template": "Process item {{sheet_num}}"},
        }

        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=2,
            config_snapshot=config_data,
            config_path="/path/to/config.yaml",
        )

        assert state.config_snapshot == config_data
        assert state.config_path == "/path/to/config.yaml"

    def test_config_snapshot_serializes_to_json(self):
        """Test that config_snapshot survives JSON serialization."""
        config_data = {
            "name": "test-job",
            "workspace": "/tmp/workspace",
            "sheet": {"size": 5, "total_items": 10},
            "prompt": {"template": "Process item {{sheet_num}}"},
            "retry": {"max_retries": 3, "base_delay_seconds": 10.0},
        }

        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=2,
            config_snapshot=config_data,
            config_path="/path/to/config.yaml",
        )

        # Serialize to dict (JSON mode)
        data = state.model_dump(mode="json")
        assert "config_snapshot" in data
        assert data["config_snapshot"]["name"] == "test-job"
        assert data["config_path"] == "/path/to/config.yaml"

        # Deserialize back
        loaded = CheckpointState.model_validate(data)
        assert loaded.config_snapshot == config_data
        assert loaded.config_path == "/path/to/config.yaml"

    def test_config_snapshot_allows_nested_structures(self):
        """Test that complex nested config structures are preserved."""
        config_data = {
            "name": "complex-job",
            "validations": [
                {"type": "file_exists", "path": "{{workspace}}/output.txt"},
                {"type": "content_contains", "path": "log.txt", "pattern": "SUCCESS"},
            ],
            "notifications": [
                {"type": "slack", "config": {"channel": "#builds"}},
            ],
        }

        state = CheckpointState(
            job_id="complex-job",
            job_name="Complex",
            total_sheets=3,
            config_snapshot=config_data,
        )

        data = state.model_dump(mode="json")
        loaded = CheckpointState.model_validate(data)

        assert len(loaded.config_snapshot["validations"]) == 2
        assert loaded.config_snapshot["notifications"][0]["type"] == "slack"


class TestZombieDetection:
    """Tests for zombie state detection and recovery."""

    def test_is_zombie_returns_false_for_pending_jobs(self):
        """Test that PENDING jobs are never zombies."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.PENDING,
            pid=12345,  # Even with PID set
        )
        assert state.is_zombie() is False

    def test_is_zombie_returns_false_for_completed_jobs(self):
        """Test that COMPLETED jobs are never zombies."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.COMPLETED,
            pid=12345,
        )
        assert state.is_zombie() is False

    def test_is_zombie_returns_false_for_paused_jobs(self):
        """Test that PAUSED jobs are never zombies."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.PAUSED,
            pid=12345,
        )
        assert state.is_zombie() is False

    def test_is_zombie_returns_false_when_no_pid(self):
        """Test that RUNNING jobs without PID are not detected as zombies."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=None,  # No PID recorded
        )
        assert state.is_zombie() is False

    def test_is_zombie_returns_true_for_dead_pid(self):
        """Test that RUNNING job with dead PID is detected as zombie."""
        # Use a PID that definitely doesn't exist (max int)
        dead_pid = 2147483647  # Max 32-bit int, unlikely to be a real process

        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=dead_pid,
        )
        assert state.is_zombie() is True

    def test_is_zombie_returns_false_for_current_process(self):
        """Test that current process PID is not detected as zombie."""
        import os

        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=os.getpid(),  # Current process
        )
        # Current process is alive, so not a zombie
        assert state.is_zombie() is False

    def test_set_running_pid_uses_current_process_by_default(self):
        """Test that set_running_pid uses os.getpid() when no pid provided."""
        import os

        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )
        state.set_running_pid()

        assert state.pid == os.getpid()

    def test_set_running_pid_accepts_explicit_pid(self):
        """Test that set_running_pid can accept an explicit PID."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )
        state.set_running_pid(pid=99999)

        assert state.pid == 99999

    def test_set_running_pid_updates_timestamp(self):
        """Test that set_running_pid updates updated_at."""
        from datetime import UTC, timedelta

        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
        )
        # Set an old timestamp
        old_time = datetime.now(UTC) - timedelta(hours=1)
        state.updated_at = old_time

        state.set_running_pid()

        # Timestamp should be updated to now
        assert state.updated_at > old_time

    def test_mark_zombie_detected_changes_status_to_paused(self):
        """Test that mark_zombie_detected sets status to PAUSED."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        state.mark_zombie_detected()

        assert state.status == JobStatus.PAUSED

    def test_mark_zombie_detected_clears_pid(self):
        """Test that mark_zombie_detected clears the PID."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        state.mark_zombie_detected()

        assert state.pid is None

    def test_mark_zombie_detected_sets_error_message(self):
        """Test that mark_zombie_detected sets an error message."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        state.mark_zombie_detected()

        assert state.error_message is not None
        assert "Recovered from stale running state" in state.error_message
        assert "PID 12345" in state.error_message

    def test_mark_zombie_detected_with_reason(self):
        """Test that mark_zombie_detected includes custom reason."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=12345,
        )
        state.mark_zombie_detected(reason="External SIGKILL detected")

        assert "External SIGKILL detected" in state.error_message

    def test_mark_zombie_detected_preserves_existing_error(self):
        """Test that mark_zombie_detected preserves existing error message.

        When there's already an error message (like a real error condition),
        the zombie recovery info is NOT appended - the original error is preserved.
        This prevents informational zombie recovery from masking real errors.
        """
        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=12345,
            error_message="Rate limit exceeded",
        )
        state.mark_zombie_detected()

        # Existing error message should be preserved unchanged
        assert state.error_message == "Rate limit exceeded"

    def test_is_zombie_alive_pid_never_zombie(self):
        """Test that alive PID is never detected as zombie regardless of update time."""
        import os
        from datetime import UTC, timedelta

        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=os.getpid(),  # Current process, so it's alive
        )

        # Even with very old updated_at, alive PID should not be zombie
        # Jobs can legitimately run for hours or days
        state.updated_at = datetime.now(UTC) - timedelta(hours=24)
        assert state.is_zombie() is False

    def test_is_zombie_fresh_updates_not_zombie(self):
        """Test that alive process with recent updates is not zombie."""
        import os
        from datetime import UTC

        state = CheckpointState(
            job_id="test-job",
            job_name="Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=os.getpid(),
            updated_at=datetime.now(UTC),  # Just updated
        )

        # Alive process should not be zombie
        assert state.is_zombie() is False

    def test_zombie_detection_serialization_roundtrip(self):
        """Test that zombie state survives serialization."""
        state = CheckpointState(
            job_id="zombie-job",
            job_name="Zombie Test",
            total_sheets=3,
            status=JobStatus.RUNNING,
            pid=2147483647,  # Non-existent PID
        )

        # Verify it's a zombie
        assert state.is_zombie() is True

        # Serialize and deserialize
        data = state.model_dump(mode="json")
        loaded = CheckpointState.model_validate(data)

        # Should still be detected as zombie after deserialization
        assert loaded.is_zombie() is True
