"""Tests for diagnose progress count accuracy.

The diagnose command's progress section counts completed/failed sheets.
Sheets that are SheetStatus.COMPLETED but have validation_passed=False
should count as "failed" in the display (matching the timeline display
which already uses format_sheet_display_status for this mapping).

F-065b: diagnose progress counts disagree with timeline display.
"""

from datetime import UTC, datetime, timedelta

from marianne.cli.commands.diagnose import _build_diagnostic_report
from marianne.core.checkpoint import (
    CheckpointState,
    JobStatus,
    SheetState,
    SheetStatus,
)


def _make_job(
    *,
    total_sheets: int = 3,
    status: JobStatus = JobStatus.COMPLETED,
    sheets: dict[int, SheetState] | None = None,
) -> CheckpointState:
    """Build a minimal CheckpointState for diagnose tests."""
    now = datetime.now(UTC)
    if sheets is None:
        sheets = {
            i: SheetState(
                sheet_num=i,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=True,
                started_at=now - timedelta(minutes=total_sheets - i + 1),
                completed_at=now - timedelta(minutes=total_sheets - i),
                execution_duration_seconds=60.0,
            )
            for i in range(1, total_sheets + 1)
        }
    last_completed = sum(
        1 for s in sheets.values() if s.status == SheetStatus.COMPLETED
    )
    return CheckpointState(
        job_id="test-job",
        job_name="Test Job",
        total_sheets=total_sheets,
        last_completed_sheet=last_completed,
        status=status,
        created_at=now - timedelta(hours=1),
        started_at=now - timedelta(minutes=total_sheets + 1),
        updated_at=now,
        completed_at=now if status == JobStatus.COMPLETED else None,
        sheets=sheets,
    )


class TestDiagnoseProgressCountAccuracy:
    """Progress counts should use display status, not raw SheetStatus."""

    def test_completed_with_failed_validation_counted_as_failed(self) -> None:
        """A sheet that is COMPLETED but validation_passed=False should
        appear in the 'failed' count, not the 'completed' count.
        This matches the timeline display (which already uses
        format_sheet_display_status)."""
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=True,
            ),
            2: SheetState(
                sheet_num=2,
                status=SheetStatus.COMPLETED,
                attempt_count=5,
                validation_passed=False,  # Retry-exhausted, marked terminal
            ),
            3: SheetState(
                sheet_num=3,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=True,
            ),
        }
        job = _make_job(total_sheets=3, sheets=sheets)
        report = _build_diagnostic_report(job)

        progress = report["progress"]
        assert progress["completed"] == 2, (
            "Sheet 2 (COMPLETED + validation_passed=False) should NOT count "
            "as completed"
        )
        assert progress["failed"] == 1, (
            "Sheet 2 (COMPLETED + validation_passed=False) should count as "
            "failed"
        )

    def test_all_display_failed_counted_correctly(self) -> None:
        """Multiple sheets with COMPLETED+validation_passed=False
        should all count as failed."""
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=3,
                validation_passed=False,
            ),
            2: SheetState(
                sheet_num=2,
                status=SheetStatus.COMPLETED,
                attempt_count=4,
                validation_passed=False,
            ),
            3: SheetState(
                sheet_num=3,
                status=SheetStatus.FAILED,
                attempt_count=3,
                error_message="Auth failure",
            ),
        }
        job = _make_job(total_sheets=3, status=JobStatus.FAILED, sheets=sheets)
        report = _build_diagnostic_report(job)

        progress = report["progress"]
        assert progress["completed"] == 0, "No sheets truly completed"
        assert progress["failed"] == 3, (
            "All 3 should be failed: 2 display-failed + 1 status-failed"
        )

    def test_progress_percent_uses_display_counts(self) -> None:
        """Progress percentage should reflect display-correct completed count."""
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=True,
            ),
            2: SheetState(
                sheet_num=2,
                status=SheetStatus.COMPLETED,
                attempt_count=5,
                validation_passed=False,  # Display-failed
            ),
        }
        job = _make_job(total_sheets=2, sheets=sheets)
        report = _build_diagnostic_report(job)

        # Only 1 of 2 sheets truly completed
        assert report["progress"]["percent"] == 50.0

    def test_timeline_and_progress_agree(self) -> None:
        """Timeline display status and progress counts must agree."""
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=True,
            ),
            2: SheetState(
                sheet_num=2,
                status=SheetStatus.COMPLETED,
                attempt_count=5,
                validation_passed=False,
            ),
            3: SheetState(
                sheet_num=3,
                status=SheetStatus.FAILED,
                attempt_count=3,
            ),
            4: SheetState(
                sheet_num=4,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=True,
            ),
        }
        job = _make_job(total_sheets=4, status=JobStatus.FAILED, sheets=sheets)
        report = _build_diagnostic_report(job)

        # Count from timeline
        timeline = report["execution_timeline"]
        timeline_completed = sum(
            1 for e in timeline if e["status"] == "completed"
        )
        timeline_failed = sum(
            1 for e in timeline if e["status"] == "failed"
        )

        # Progress counts should match
        assert report["progress"]["completed"] == timeline_completed
        assert report["progress"]["failed"] == timeline_failed

    def test_validation_passed_none_stays_completed(self) -> None:
        """Sheets with validation_passed=None (no validations defined)
        should still count as completed."""
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=None,  # No validations
            ),
        }
        job = _make_job(total_sheets=1, sheets=sheets)
        report = _build_diagnostic_report(job)

        assert report["progress"]["completed"] == 1
        assert report["progress"]["failed"] == 0
