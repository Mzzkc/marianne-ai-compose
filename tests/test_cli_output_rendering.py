"""Tests for mozart.cli.output module — Rich output rendering.

Covers formatters, table builders, panel builders, error output, and
status helpers using the StringIO + Console capture pattern (no ANSI).

GH#82 — CLI output rendering at 0% direct coverage.
Q008 — Extended coverage for run summary panel, error details, job
status line, timing section, and progress builders.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

from marianne.cli.output import (
    StatusColors,
    create_diagnostic_panel,
    create_errors_table,
    create_execution_progress,
    create_header_panel,
    create_jobs_table,
    create_patterns_table,
    create_run_summary_panel,
    create_server_panel,
    create_sheet_details_table,
    format_sheet_display_status,
    create_sheet_plan_table,
    create_simple_table,
    create_status_progress,
    create_synthesis_table,
    create_timeline_table,
    format_bytes,
    format_duration,
    format_error_details,
    format_job_status_line,
    format_timestamp,
    format_validation_status,
    infer_error_type,
    output_error,
    print_job_status_header,
    print_timing_section,
)
from marianne.core.checkpoint import JobStatus, SheetStatus


def _render(renderable: object, width: int = 120) -> str:
    """Render a Rich object to plain text (no ANSI)."""
    buf = StringIO()
    console = Console(file=buf, color_system=None, width=width)
    console.print(renderable)
    return buf.getvalue()


def _capture_console() -> tuple[Console, StringIO]:
    """Create a plain-text Console and its backing buffer."""
    buf = StringIO()
    console = Console(file=buf, color_system=None, width=120)
    return console, buf


# ---------------------------------------------------------------------------
# Formatter tests
# ---------------------------------------------------------------------------


class TestFormatDuration:
    """Tests for format_duration()."""

    def test_none_returns_na(self) -> None:
        assert format_duration(None) == "N/A"

    def test_under_minute(self) -> None:
        assert format_duration(5.2) == "5.2s"

    def test_zero_seconds(self) -> None:
        assert format_duration(0.0) == "0.0s"

    def test_exact_minute(self) -> None:
        assert format_duration(60) == "1m 0s"

    def test_minutes_and_seconds(self) -> None:
        assert format_duration(195) == "3m 15s"

    def test_exact_hour(self) -> None:
        assert format_duration(3600) == "1h 0m"

    def test_hours_and_minutes(self) -> None:
        assert format_duration(5430) == "1h 30m"


class TestFormatBytes:
    """Tests for format_bytes()."""

    def test_bytes_range(self) -> None:
        assert format_bytes(512) == "512B"

    def test_zero_bytes(self) -> None:
        assert format_bytes(0) == "0B"

    def test_kilobytes(self) -> None:
        assert "KB" in format_bytes(1536)

    def test_megabytes(self) -> None:
        assert "MB" in format_bytes(2 * 1024 * 1024)


class TestFormatTimestamp:
    """Tests for format_timestamp()."""

    def test_none_returns_dash(self) -> None:
        assert format_timestamp(None) == "-"

    def test_with_timezone(self) -> None:
        dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = format_timestamp(dt, include_tz=True)
        assert "2026-01-15" in result
        assert "10:30:00" in result
        assert "UTC" in result

    def test_without_timezone(self) -> None:
        dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = format_timestamp(dt, include_tz=False)
        assert "UTC" not in result
        assert "2026-01-15" in result


class TestFormatValidationStatus:
    """Tests for format_validation_status()."""

    def test_none_returns_dash(self) -> None:
        assert format_validation_status(None) == "-"

    def test_passed(self) -> None:
        assert "Pass" in format_validation_status(True)

    def test_failed(self) -> None:
        assert "Fail" in format_validation_status(False)


class TestInferErrorType:
    """Tests for infer_error_type()."""

    def test_none_is_permanent(self) -> None:
        assert infer_error_type(None) == "permanent"

    def test_rate_limit(self) -> None:
        assert infer_error_type("rate_limit") == "rate_limit"
        assert infer_error_type("Rate Limit Exceeded") == "rate_limit"

    def test_transient(self) -> None:
        assert infer_error_type("transient") == "transient"
        assert infer_error_type("timeout") == "transient"
        assert infer_error_type("network") == "transient"

    def test_unknown_is_permanent(self) -> None:
        assert infer_error_type("something_weird") == "permanent"


# ---------------------------------------------------------------------------
# StatusColors tests
# ---------------------------------------------------------------------------


class TestStatusColors:
    """Tests for StatusColors class."""

    def test_all_job_statuses_have_colors(self) -> None:
        for status in JobStatus:
            color = StatusColors.get_job_color(status)
            assert isinstance(color, str) and color

    def test_all_sheet_statuses_have_colors(self) -> None:
        for status in SheetStatus:
            color = StatusColors.get_sheet_color(status)
            assert isinstance(color, str) and color

    def test_unknown_error_type_fallback(self) -> None:
        assert StatusColors.get_error_color("nonexistent_type") == "white"

    def test_error_type_colors(self) -> None:
        assert StatusColors.get_error_color("permanent") == "red"
        assert StatusColors.get_error_color("transient") == "yellow"
        assert StatusColors.get_error_color("rate_limit") == "blue"
        assert StatusColors.get_error_color("unknown") == "white"


class TestFormatSheetDisplayStatus:
    """Tests for format_sheet_display_status (F-045).

    'completed' + validation_failed should show as 'failed' to avoid
    confusing users who read 'completed' as meaning 'done successfully'.
    """

    def test_completed_with_validation_passed_shows_completed(self) -> None:
        label, color = format_sheet_display_status(SheetStatus.COMPLETED, True)
        assert label == "completed"
        assert color == "green"

    def test_completed_with_validation_failed_shows_failed(self) -> None:
        """Core F-045 fix: retry-exhausted sheets show 'failed' not 'completed'."""
        label, color = format_sheet_display_status(SheetStatus.COMPLETED, False)
        assert label == "failed"
        assert color == "red"

    def test_completed_with_validation_none_shows_completed(self) -> None:
        """Sheets with no validations (None) keep 'completed'."""
        label, color = format_sheet_display_status(SheetStatus.COMPLETED, None)
        assert label == "completed"
        assert color == "green"

    def test_failed_status_unchanged(self) -> None:
        label, color = format_sheet_display_status(SheetStatus.FAILED, None)
        assert label == "failed"
        assert color == "red"

    def test_pending_status_unchanged(self) -> None:
        label, color = format_sheet_display_status(SheetStatus.PENDING, None)
        assert label == "pending"
        assert color == "yellow"

    def test_in_progress_status_unchanged(self) -> None:
        label, color = format_sheet_display_status(SheetStatus.IN_PROGRESS, None)
        assert label == "in_progress"
        assert color == "blue"

    def test_skipped_status_unchanged(self) -> None:
        label, color = format_sheet_display_status(SheetStatus.SKIPPED, None)
        assert label == "skipped"
        assert color == "dim"


# ---------------------------------------------------------------------------
# Table builder tests
# ---------------------------------------------------------------------------


class TestTableBuilders:
    """Tests for table factory functions rendered to plain text."""

    def test_jobs_table_has_columns(self) -> None:
        table = create_jobs_table()
        table.add_row("test-job", "running", "/tmp/ws", "2026-01-01")
        output = _render(table)
        assert "test-job" in output
        assert "running" in output

    def test_sheet_plan_table(self) -> None:
        table = create_sheet_plan_table()
        table.add_row("1", "Write code", "3 checks")
        assert "Write code" in _render(table)

    def test_sheet_details_without_descriptions(self) -> None:
        table = create_sheet_details_table(has_descriptions=False)
        table.add_row("1", "completed", "2", "✓ Pass", "")
        assert "completed" in _render(table)

    def test_sheet_details_with_descriptions(self) -> None:
        table = create_sheet_details_table(has_descriptions=True)
        table.add_row("1", "Setup env", "completed", "1", "✓ Pass", "")
        assert "Setup env" in _render(table)

    def test_synthesis_table(self) -> None:
        table = create_synthesis_table()
        table.add_row("batch-1", "1,2,3", "merge", "done")
        output = _render(table)
        assert "batch-1" in output
        assert "merge" in output

    def test_errors_table(self) -> None:
        table = create_errors_table(title="Recent Errors")
        table.add_row("3", "transient", "E201", "2", "Connection timeout")
        output = _render(table)
        assert "Connection timeout" in output
        assert "Recent Errors" in output

    def test_errors_table_no_title(self) -> None:
        table = create_errors_table(title="")
        assert "Sheet" in _render(table)

    def test_timeline_table(self) -> None:
        table = create_timeline_table()
        table.add_row("1", "completed", "5.2s", "1", "standard", "success")
        assert "5.2s" in _render(table)

    def test_patterns_table(self) -> None:
        table = create_patterns_table(title="My Patterns")
        table.add_row("retry-on-timeout", "0.85", "12", "75%")
        output = _render(table)
        assert "retry-on-timeout" in output
        assert "My Patterns" in output

    def test_simple_table_no_header(self) -> None:
        table = create_simple_table(show_header=False)
        table.add_column("Key")
        table.add_column("Value")
        table.add_row("name", "test-job")
        assert "test-job" in _render(table)


# ---------------------------------------------------------------------------
# Panel builder tests
# ---------------------------------------------------------------------------


class TestPanelBuilders:
    """Tests for panel factory functions rendered to plain text."""

    def test_header_panel(self) -> None:
        panel = create_header_panel(
            lines=["Line 1", "Line 2"],
            title="Test Header",
            border_style="cyan",
        )
        output = _render(panel)
        assert "Line 1" in output
        assert "Line 2" in output
        assert "Test Header" in output

    def test_diagnostic_panel(self) -> None:
        panel = create_diagnostic_panel(
            job_name="my-job",
            job_id="job-123",
            status=JobStatus.FAILED,
        )
        output = _render(panel)
        assert "my-job" in output
        assert "job-123" in output
        assert "FAILED" in output
        assert "Diagnostic Report" in output

    def test_server_panel(self) -> None:
        panel = create_server_panel(
            title="Server Info",
            server_name="Mozart Conductor",
            info_lines=["Socket: /tmp/test.sock", "PID: 12345"],
        )
        output = _render(panel)
        assert "Mozart Conductor" in output
        assert "Socket: /tmp/test.sock" in output
        assert "Ctrl+C" in output


# ---------------------------------------------------------------------------
# Error output tests
# ---------------------------------------------------------------------------


class TestOutputError:
    """Tests for output_error() in both Rich and JSON modes."""

    def test_rich_error_output(self) -> None:
        console, buf = _capture_console()
        output_error("Something broke", console_instance=console)
        output = buf.getvalue()
        assert "Error:" in output
        assert "Something broke" in output

    def test_rich_warning_output(self) -> None:
        console, buf = _capture_console()
        output_error("Be careful", severity="warning", console_instance=console)
        output = buf.getvalue()
        assert "Warning:" in output
        assert "Be careful" in output

    def test_rich_error_with_code(self) -> None:
        console, buf = _capture_console()
        output_error("Bad config", error_code="E501", console_instance=console)
        assert "E501" in buf.getvalue()

    def test_rich_error_with_hints(self) -> None:
        console, buf = _capture_console()
        output_error(
            "Missing file",
            hints=["Check the path", "Run validate first"],
            console_instance=console,
        )
        output = buf.getvalue()
        assert "Hints:" in output
        assert "Check the path" in output
        assert "Run validate first" in output

    def test_json_error_output(self) -> None:
        console, buf = _capture_console()
        output_error(
            "Bad config",
            error_code="E501",
            hints=["Fix it"],
            json_output=True,
            console_instance=console,
        )
        parsed = json.loads(buf.getvalue())
        assert parsed["success"] is False
        assert parsed["message"] == "Bad config"
        assert parsed["error_code"] == "E501"
        assert parsed["hints"] == ["Fix it"]

    def test_json_error_with_extras(self) -> None:
        console, buf = _capture_console()
        output_error(
            "Error",
            json_output=True,
            console_instance=console,
            job_id="test-123",
        )
        parsed = json.loads(buf.getvalue())
        assert parsed["job_id"] == "test-123"


# ---------------------------------------------------------------------------
# Run summary panel tests (Q008 — GH#82)
# ---------------------------------------------------------------------------


class TestRunSummaryPanel:
    """Tests for create_run_summary_panel()."""

    def test_completed_job_green_border(self) -> None:
        summary = MagicMock(
            job_name="test-job",
            completed_sheets=10,
            total_sheets=10,
            failed_sheets=0,
            skipped_sheets=0,
            validation_passed=True,
            duration_seconds=120.5,
        )
        panel = create_run_summary_panel(summary, JobStatus.COMPLETED)
        output = _render(panel)
        assert "test-job" in output
        assert "10/10" in output
        assert "Run Summary" in output

    def test_failed_job_yellow_border(self) -> None:
        summary = MagicMock(
            job_name="failing-job",
            completed_sheets=5,
            total_sheets=10,
            failed_sheets=3,
            skipped_sheets=0,
            validation_passed=False,
            duration_seconds=60.0,
        )
        panel = create_run_summary_panel(summary, JobStatus.FAILED)
        output = _render(panel)
        assert "failing-job" in output
        assert "Failed" in output or "3" in output

    def test_summary_without_validation(self) -> None:
        summary = MagicMock(
            job_name="no-val-job",
            completed_sheets=5,
            total_sheets=10,
            failed_sheets=0,
            skipped_sheets=0,
            spec=["job_name", "completed_sheets", "total_sheets",
                  "failed_sheets", "skipped_sheets"],
        )
        # Remove optional attributes
        del summary.validation_passed
        del summary.duration_seconds
        panel = create_run_summary_panel(summary, JobStatus.COMPLETED)
        output = _render(panel)
        assert "no-val-job" in output


# ---------------------------------------------------------------------------
# Error details formatting tests (Q008 — GH#82)
# ---------------------------------------------------------------------------


class TestFormatErrorDetails:
    """Tests for format_error_details()."""

    def test_basic_error_details(self) -> None:
        error = MagicMock(
            error_message="Something went wrong",
            error_type="permanent",
            error_code="E201",
            attempt_number=2,
            timestamp=datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC),
            context={"sheet_num": 3, "backend": "claude_cli"},
            stdout_tail=None,
            stderr_tail=None,
            stack_trace=None,
        )
        result = format_error_details(error)
        assert "Something went wrong" in result
        assert "permanent" in result
        assert "E201" in result
        assert "2" in result

    def test_error_with_stdout_tail(self) -> None:
        error = MagicMock(
            error_message="Failed",
            error_type="transient",
            error_code="E301",
            attempt_number=1,
            timestamp=None,
            context={},
            stdout_tail="Error: connection refused\nRetrying...",
            stderr_tail=None,
            stack_trace=None,
        )
        result = format_error_details(error)
        assert "connection refused" in result

    def test_error_with_stderr_tail(self) -> None:
        error = MagicMock(
            error_message="Process crashed",
            error_type="permanent",
            error_code="E501",
            attempt_number=1,
            timestamp=None,
            context={},
            stdout_tail=None,
            stderr_tail="Traceback (most recent call last):\n  File...",
            stack_trace=None,
        )
        result = format_error_details(error)
        assert "Traceback" in result

    def test_error_with_stack_trace(self) -> None:
        error = MagicMock(
            error_message="Crash",
            error_type="permanent",
            error_code="E999",
            attempt_number=1,
            timestamp=None,
            context={},
            stdout_tail=None,
            stderr_tail=None,
            stack_trace="ValueError: invalid literal",
        )
        result = format_error_details(error)
        assert "Stack Trace" in result
        assert "ValueError" in result

    def test_error_with_none_context_values(self) -> None:
        error = MagicMock(
            error_message="Error",
            error_type="permanent",
            error_code="E100",
            attempt_number=1,
            timestamp=None,
            context={"key": None, "other": "value"},
            stdout_tail=None,
            stderr_tail=None,
            stack_trace=None,
        )
        result = format_error_details(error)
        assert "other=value" in result


# ---------------------------------------------------------------------------
# Job status line and header tests (Q008 — GH#82)
# ---------------------------------------------------------------------------


class TestJobStatusLine:
    """Tests for format_job_status_line()."""

    def test_running_job(self) -> None:
        job = MagicMock(
            status=JobStatus.RUNNING,
            job_id="my-job",
            last_completed_sheet=5,
            total_sheets=10,
        )
        result = format_job_status_line(job)
        assert "running" in result
        assert "my-job" in result
        assert "5/10" in result

    def test_completed_job(self) -> None:
        job = MagicMock(
            status=JobStatus.COMPLETED,
            job_id="done-job",
            last_completed_sheet=10,
            total_sheets=10,
        )
        result = format_job_status_line(job)
        assert "completed" in result
        assert "10/10" in result


class TestPrintJobStatusHeader:
    """Tests for print_job_status_header()."""

    def test_running_job_with_elapsed(self) -> None:
        now = datetime.now(UTC)
        job = MagicMock(
            job_name="active-job",
            job_id="job-123",
            status=JobStatus.RUNNING,
            started_at=now - timedelta(minutes=5),
            completed_at=None,
            updated_at=now,
        )
        console, buf = _capture_console()
        print_job_status_header(job, console_instance=console)
        output = buf.getvalue()
        assert "active-job" in output
        assert "RUNNING" in output

    def test_completed_job_with_duration(self) -> None:
        start = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
        end = datetime(2026, 1, 15, 10, 5, 0, tzinfo=UTC)
        job = MagicMock(
            job_name="done-job",
            job_id="job-456",
            status=JobStatus.COMPLETED,
            started_at=start,
            completed_at=end,
            updated_at=end,
        )
        console, buf = _capture_console()
        print_job_status_header(job, console_instance=console)
        output = buf.getvalue()
        assert "done-job" in output
        assert "5m 0s" in output

    def test_pending_job_no_timing(self) -> None:
        job = MagicMock(
            job_name="pending-job",
            job_id="job-789",
            status=JobStatus.PENDING,
            started_at=None,
            completed_at=None,
            updated_at=None,
        )
        console, buf = _capture_console()
        print_job_status_header(job, console_instance=console)
        output = buf.getvalue()
        assert "pending-job" in output
        assert "PENDING" in output


class TestPrintTimingSection:
    """Tests for print_timing_section()."""

    def test_full_timing(self) -> None:
        job = MagicMock(
            created_at=datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC),
            started_at=datetime(2026, 1, 15, 10, 0, 5, tzinfo=UTC),
            updated_at=datetime(2026, 1, 15, 10, 5, 0, tzinfo=UTC),
            completed_at=datetime(2026, 1, 15, 10, 5, 0, tzinfo=UTC),
        )
        console, buf = _capture_console()
        print_timing_section(job, console_instance=console)
        output = buf.getvalue()
        assert "Created" in output
        assert "Started" in output
        assert "Completed" in output

    def test_partial_timing(self) -> None:
        job = MagicMock(
            created_at=datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC),
            started_at=None,
            updated_at=None,
            completed_at=None,
        )
        console, buf = _capture_console()
        print_timing_section(job, console_instance=console)
        output = buf.getvalue()
        assert "Created" in output
        assert "Started" not in output


# ---------------------------------------------------------------------------
# Progress builder tests (Q008 — GH#82)
# ---------------------------------------------------------------------------


class TestProgressBuilders:
    """Tests for progress bar factory functions."""

    def test_execution_progress_creates_instance(self) -> None:
        progress = create_execution_progress()
        assert progress is not None

    def test_execution_progress_with_custom_console(self) -> None:
        console, _ = _capture_console()
        progress = create_execution_progress(console_instance=console)
        assert progress is not None

    def test_status_progress_creates_instance(self) -> None:
        progress = create_status_progress()
        assert progress is not None

    def test_status_progress_with_custom_console(self) -> None:
        console, _ = _capture_console()
        progress = create_status_progress(console_instance=console)
        assert progress is not None
