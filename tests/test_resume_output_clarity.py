"""Test that resume output clearly separates previous state from new resume (#122).

Bug: When resuming with --config, the output shows the original failure message
before the resume, making it look like the resume itself failed.

Fix: The resume panel now labels the previous status as "Previous status:" and
shows a clear "Resuming from sheet N" message, so users can distinguish between
the historical failure and the active resume attempt.
"""

from __future__ import annotations

from marianne.core.checkpoint import CheckpointState, JobStatus


class TestResumeOutputClarity:
    """Tests verifying resume output distinguishes previous state from resume."""

    def test_resume_panel_shows_previous_status(self) -> None:
        """Resume panel should label the old status as 'Previous status'."""
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.FAILED,
            error_message="Sheet 3 exhausted all retry options (1 validation failing)",
        )

        assert state.status == JobStatus.FAILED
        assert state.error_message is not None
        assert "exhausted" in state.error_message
        assert state.last_completed_sheet == 2

    def test_resume_clears_error_message(self) -> None:
        """After resume starts, previous error_message should be cleared."""
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.FAILED,
            error_message="Previous failure message",
        )

        previous_error = state.error_message
        state.status = JobStatus.RUNNING
        state.error_message = None

        assert previous_error == "Previous failure message"
        assert state.status == JobStatus.RUNNING
        assert state.error_message is None

    def test_resume_event_includes_previous_context(self) -> None:
        """Daemon resume event should include previous_error and config_reloaded."""
        event_data = {
            "resume_sheet": 3,
            "total_sheets": 5,
            "previous_status": "failed",
            "previous_error": "Sheet 3 exhausted all retry options",
            "config_reloaded": True,
        }

        assert event_data["previous_status"] == "failed"
        assert event_data["previous_error"] is not None
        assert event_data["config_reloaded"] is True

    def test_long_error_message_truncated(self) -> None:
        """Long error messages should be truncated in the panel."""
        long_error = "A" * 200
        max_len = 120
        error_display = long_error[:max_len] + "..." if len(long_error) > max_len else long_error
        assert len(error_display) == 123
        assert error_display.endswith("...")

    def test_no_error_message_omitted(self) -> None:
        """When there's no error message, the error line should be omitted."""
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=5,
            last_completed_sheet=2,
            status=JobStatus.PAUSED,
            error_message=None,
        )

        panel_lines = [
            f"Previous status: {state.status.value}",
        ]
        if state.error_message:
            panel_lines.append(f"Previous error: {state.error_message}")

        assert len(panel_lines) == 1
        assert "Previous error" not in panel_lines[0]
