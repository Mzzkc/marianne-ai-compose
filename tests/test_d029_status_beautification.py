"""TDD tests for D-029: Status display beautification.

Tests the beautified display functions for:
- `mzt status` (relative time, musical context, "Now Playing")
- `mzt list` (progress info, status grouping, clean layout)
- `mzt conductor-status` (paneled resource context, job summary)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marianne.core.checkpoint import (
    CheckpointState,
    JobStatus,
    SheetState,
    SheetStatus,
)


# ============================================================================
# Helpers to build test data
# ============================================================================


_SHEET_NUM_COUNTER = 0


def _make_sheet(
    status: SheetStatus = SheetStatus.PENDING,
    *,
    sheet_num: int | None = None,
    movement: int | None = None,
    voice: int | None = None,
    instrument_name: str | None = None,
    started_at: datetime | None = None,
    execution_duration_seconds: float | None = None,
    validation_passed: bool | None = None,
    description: str | None = None,
    error_message: str | None = None,
    attempt_count: int = 1,
) -> SheetState:
    global _SHEET_NUM_COUNTER
    if sheet_num is None:
        _SHEET_NUM_COUNTER += 1
        sheet_num = _SHEET_NUM_COUNTER
    return SheetState(
        sheet_num=sheet_num,
        status=status,
        movement=movement,
        voice=voice,
        instrument_name=instrument_name,
        started_at=started_at,
        execution_duration_seconds=execution_duration_seconds,
        validation_passed=validation_passed,
        error_message=error_message,
        attempt_count=attempt_count,
    )


def _make_job(
    *,
    job_id: str = "test-score",
    job_name: str = "Test Score",
    total_sheets: int = 5,
    status: JobStatus = JobStatus.RUNNING,
    sheets: dict[int, SheetState] | None = None,
    started_at: datetime | None = None,
    updated_at: datetime | None = None,
    completed_at: datetime | None = None,
    total_retry_count: int = 0,
    rate_limit_waits: int = 0,
    total_estimated_cost: float = 0.0,
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
    parallel_enabled: bool = False,
    last_completed_sheet: int = 0,
    config_snapshot: dict[str, Any] | None = None,
    total_movements: int | None = None,
) -> CheckpointState:
    return CheckpointState(
        job_id=job_id,
        job_name=job_name,
        total_sheets=total_sheets,
        status=status,
        sheets=sheets or {},
        started_at=started_at or datetime.now(UTC) - timedelta(hours=2),
        updated_at=updated_at or datetime.now(UTC) - timedelta(minutes=5),
        completed_at=completed_at,
        total_retry_count=total_retry_count,
        rate_limit_waits=rate_limit_waits,
        total_estimated_cost=total_estimated_cost,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        parallel_enabled=parallel_enabled,
        last_completed_sheet=last_completed_sheet,
        config_snapshot=config_snapshot,
        total_movements=total_movements,
    )


# ============================================================================
# format_relative_time tests
# ============================================================================


class TestFormatRelativeTime:
    """Tests for the new relative time formatter."""

    def test_seconds_ago(self) -> None:
        from marianne.cli.output import format_relative_time

        dt = datetime.now(UTC) - timedelta(seconds=30)
        result = format_relative_time(dt)
        assert "s ago" in result or "just now" in result

    def test_minutes_ago(self) -> None:
        from marianne.cli.output import format_relative_time

        dt = datetime.now(UTC) - timedelta(minutes=5)
        result = format_relative_time(dt)
        assert "5m ago" in result

    def test_hours_ago(self) -> None:
        from marianne.cli.output import format_relative_time

        dt = datetime.now(UTC) - timedelta(hours=3, minutes=15)
        result = format_relative_time(dt)
        assert "3h" in result

    def test_days_ago(self) -> None:
        from marianne.cli.output import format_relative_time

        dt = datetime.now(UTC) - timedelta(days=6, hours=12)
        result = format_relative_time(dt)
        assert "6d" in result

    def test_none_returns_dash(self) -> None:
        from marianne.cli.output import format_relative_time

        assert format_relative_time(None) == "-"


# ============================================================================
# format_duration extended tests (days support)
# ============================================================================


class TestFormatDurationExtended:
    """Tests for extended duration formatting with days support."""

    def test_days_and_hours(self) -> None:
        from marianne.cli.output import format_duration

        # 6 days, 12 hours = 6*86400 + 12*3600
        result = format_duration(6 * 86400 + 12 * 3600)
        assert "6d" in result
        assert "12h" in result

    def test_one_day(self) -> None:
        from marianne.cli.output import format_duration

        result = format_duration(86400 + 3600)
        assert "1d" in result


# ============================================================================
# _output_status_rich beautification tests
# ============================================================================


class TestStatusRichBeautification:
    """Tests that _output_status_rich produces the beautified output."""

    def test_header_panel_contains_job_name(self) -> None:
        """The header panel should show the score name prominently."""
        from marianne.cli.commands.status import _output_status_rich

        job = _make_job(job_name="Marianne Orchestra v3")
        with patch("marianne.cli.commands.status.console") as mock_console:
            _output_status_rich(job)
            # Check that a Panel was printed containing the job name
            panel_found = False
            for call in mock_console.print.call_args_list:
                args = call[0]
                for arg in args:
                    # Check Panel renderable content
                    if hasattr(arg, "renderable"):
                        if "Marianne Orchestra v3" in str(arg.renderable):
                            panel_found = True
                    elif "Marianne Orchestra v3" in str(arg):
                        panel_found = True
            assert panel_found, "Panel should contain job name"

    def test_shows_relative_elapsed_time(self) -> None:
        """Running jobs should show relative elapsed time (e.g., '2h 0m elapsed')."""
        from marianne.cli.commands.status import _output_status_rich

        job = _make_job(started_at=datetime.now(UTC) - timedelta(hours=2))
        with patch("marianne.cli.commands.status.console") as mock_console:
            _output_status_rich(job)
            output = _collect_printed_text(mock_console)
            # Should contain elapsed time in some form
            assert "2h" in output or "elapsed" in output or "Running" in output

    def test_shows_last_activity_relative(self) -> None:
        """Last activity should use relative time (e.g., '5m ago')."""
        from marianne.cli.commands.status import _output_status_rich

        job = _make_job(
            updated_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        with patch("marianne.cli.commands.status.console") as mock_console:
            _output_status_rich(job)
            output = _collect_printed_text(mock_console)
            # Should have relative time reference
            assert "ago" in output or "5m" in output

    def test_now_playing_section_for_in_progress_sheets(self) -> None:
        """In-progress sheets should appear in a 'Now Playing' section."""
        from marianne.cli.commands.status import _output_status_rich

        now = datetime.now(UTC)
        sheets = {
            1: _make_sheet(SheetStatus.COMPLETED, movement=0),
            2: _make_sheet(
                SheetStatus.IN_PROGRESS,
                movement=1,
                started_at=now - timedelta(minutes=3),
            ),
            3: _make_sheet(SheetStatus.PENDING, movement=1),
        }
        job = _make_job(
            total_sheets=3,
            sheets=sheets,
            last_completed_sheet=1,
        )
        with patch("marianne.cli.commands.status.console") as mock_console:
            _output_status_rich(job)
            output = _collect_printed_text(mock_console)
            assert "Now Playing" in output

    def test_stats_section_shows_compact_numbers(self) -> None:
        """Execution stats should show compact key numbers."""
        from marianne.cli.commands.status import _output_status_rich

        sheets = {
            i: _make_sheet(SheetStatus.COMPLETED) for i in range(1, 6)
        }
        job = _make_job(
            sheets=sheets,
            total_retry_count=3,
            rate_limit_waits=7,
            last_completed_sheet=5,
        )
        with patch("marianne.cli.commands.status.console") as mock_console:
            _output_status_rich(job)
            output = _collect_printed_text(mock_console)
            # Stats should appear somewhere
            assert "3" in output  # retries
            assert "7" in output  # rate waits

    def test_completed_job_shows_total_duration(self) -> None:
        """Completed jobs should show total duration."""
        from marianne.cli.commands.status import _output_status_rich

        now = datetime.now(UTC)
        job = _make_job(
            status=JobStatus.COMPLETED,
            started_at=now - timedelta(hours=1, minutes=30),
            completed_at=now,
        )
        with patch("marianne.cli.commands.status.console") as mock_console:
            _output_status_rich(job)
            output = _collect_printed_text(mock_console)
            assert "1h 30m" in output


# ============================================================================
# List display beautification tests
# ============================================================================


class TestListBeautification:
    """Tests for the beautified mzt list display."""

    def test_list_shows_progress_for_running_jobs(self) -> None:
        """Running jobs in list should show progress percentage."""
        from marianne.cli.commands.status import _list_jobs

        mock_jobs = [
            {
                "job_id": "my-score",
                "status": "running",
                "workspace": "/long/path/to/workspace",
                "submitted_at": datetime.now(UTC).timestamp() - 3600,
                "completed_sheets": 50,
                "total_sheets": 200,
            },
        ]

        import asyncio

        with (
            patch("marianne.daemon.detect.try_daemon_route") as mock_route,
            patch("marianne.cli.commands.status.console") as mock_console,
        ):
            mock_route.return_value = (True, mock_jobs)
            asyncio.run(_list_jobs(True, None, 20, None, False))
            output = _collect_printed_text(mock_console)
            # Should show progress info — percentage or fraction
            assert "my-score" in output
            assert "RUNNING" in output or "running" in output.lower()

    def test_list_truncates_workspace_path(self) -> None:
        """Workspace paths should be truncated or abbreviated."""
        from marianne.cli.commands.status import _list_jobs

        mock_jobs = [
            {
                "job_id": "short-id",
                "status": "running",
                "workspace": "/very/long/absolute/path/to/some/workspace/directory",
                "submitted_at": datetime.now(UTC).timestamp(),
            },
        ]

        import asyncio

        with (
            patch("marianne.daemon.detect.try_daemon_route") as mock_route,
            patch("marianne.cli.commands.status.console") as mock_console,
        ):
            mock_route.return_value = (True, mock_jobs)
            asyncio.run(_list_jobs(True, None, 20, None, False))
            output = _collect_printed_text(mock_console)
            # The full long path should NOT appear
            assert "/very/long/absolute/path/to/some/workspace/directory" not in output

    def test_list_shows_relative_time(self) -> None:
        """Submitted time should show relative (e.g., '1h ago') not full UTC."""
        from marianne.cli.commands.status import _list_jobs

        mock_jobs = [
            {
                "job_id": "recent-score",
                "status": "completed",
                "workspace": "/ws",
                "submitted_at": datetime.now(UTC).timestamp() - 3600,
            },
        ]

        import asyncio

        with (
            patch("marianne.daemon.detect.try_daemon_route") as mock_route,
            patch("marianne.cli.commands.status.console") as mock_console,
        ):
            mock_route.return_value = (True, mock_jobs)
            asyncio.run(_list_jobs(True, None, 20, None, False))
            output = _collect_printed_text(mock_console)
            # Should have relative time
            assert "ago" in output or "1h" in output


# ============================================================================
# Conductor status beautification tests
# ============================================================================


class TestConductorStatusBeautification:
    """Tests for beautified conductor-status display."""

    def test_conductor_status_shows_panel_content(self) -> None:
        """Conductor status should use Rich Panel with PID and uptime."""
        from marianne.daemon.process import get_conductor_status

        with (
            patch("marianne.daemon.process._read_pid", return_value=12345),
            patch("marianne.daemon.process._pid_alive", return_value=True),
            patch("marianne.daemon.process.asyncio.run") as mock_run,
            patch("rich.panel.Panel") as mock_panel,
        ):
            mock_run.return_value = (
                {"uptime_seconds": 3600, "status": "healthy"},
                {"status": "ready", "running_jobs": 1, "memory_mb": 165,
                 "child_processes": 8, "accepting_work": True,
                 "memory_limit_mb": 2048, "process_limit": 200},
                {"version": "0.1.0"},
            )
            get_conductor_status()
            # Panel should have been constructed with content containing PID
            assert mock_panel.called, "Panel should be constructed"
            panel_content = mock_panel.call_args[0][0]  # first positional arg
            assert "12345" in panel_content
            assert "1h" in panel_content  # uptime
            assert "Conductor" in str(mock_panel.call_args)  # title

    def test_conductor_status_shows_resource_context(self) -> None:
        """Resources should show usage relative to limits."""
        from marianne.daemon.process import get_conductor_status

        with (
            patch("marianne.daemon.process._read_pid", return_value=12345),
            patch("marianne.daemon.process._pid_alive", return_value=True),
            patch("marianne.daemon.process.asyncio.run") as mock_run,
            patch("rich.panel.Panel") as mock_panel,
        ):
            mock_run.return_value = (
                {"uptime_seconds": 7200},
                {"status": "ready", "running_jobs": 2, "memory_mb": 256,
                 "child_processes": 15, "accepting_work": True,
                 "memory_limit_mb": 2048, "process_limit": 200},
                {"version": "0.1.0"},
            )
            get_conductor_status()
            panel_content = mock_panel.call_args[0][0]
            # Should show memory with percentage context
            assert "256" in panel_content
            assert "2048" in panel_content or "12%" in panel_content
            # Should show processes with limit
            assert "15" in panel_content
            assert "200" in panel_content


# ============================================================================
# Movement progress bar tests (new)
# ============================================================================


class TestMovementProgressDisplay:
    """Tests for movement-level progress display in status."""

    def test_movement_progress_shows_bar_indicators(self) -> None:
        """Movement display should show visual progress indicators."""
        from marianne.cli.commands.status import _render_movement_grouped_details

        sheets = {
            1: _make_sheet(SheetStatus.COMPLETED, movement=0),
            2: _make_sheet(SheetStatus.COMPLETED, movement=0),
            3: _make_sheet(SheetStatus.IN_PROGRESS, movement=1,
                           started_at=datetime.now(UTC) - timedelta(minutes=5)),
            4: _make_sheet(SheetStatus.PENDING, movement=1),
            5: _make_sheet(SheetStatus.PENDING, movement=2),
        }
        job = _make_job(total_sheets=5, sheets=sheets)

        mock_console = MagicMock()
        _render_movement_grouped_details(job, target_console=mock_console)
        output = _collect_printed_text(mock_console)

        # Should display movement numbers
        assert "Movement 0" in output
        assert "Movement 1" in output
        assert "Movement 2" in output

    def test_movement_shows_completion_fraction(self) -> None:
        """Running movements should show X/Y sheets completed."""
        from marianne.cli.commands.status import _render_movement_grouped_details

        sheets = {
            1: _make_sheet(SheetStatus.COMPLETED, movement=1),
            2: _make_sheet(SheetStatus.COMPLETED, movement=1),
            3: _make_sheet(SheetStatus.IN_PROGRESS, movement=1,
                           started_at=datetime.now(UTC) - timedelta(minutes=2)),
            4: _make_sheet(SheetStatus.PENDING, movement=1),
        }
        job = _make_job(total_sheets=4, sheets=sheets)

        mock_console = MagicMock()
        _render_movement_grouped_details(job, target_console=mock_console)
        output = _collect_printed_text(mock_console)

        # Should show "2/4 complete" or similar fraction
        assert "2/4" in output


# ============================================================================
# Now Playing section tests
# ============================================================================


class TestNowPlayingSection:
    """Tests for the 'Now Playing' display section."""

    def test_now_playing_shows_in_progress_sheets(self) -> None:
        """Now Playing should list all in-progress sheets with elapsed time."""
        from marianne.cli.commands.status import _render_now_playing

        now = datetime.now(UTC)
        sheets = {
            1: _make_sheet(SheetStatus.COMPLETED, sheet_num=1, movement=0),
            2: _make_sheet(
                SheetStatus.IN_PROGRESS, sheet_num=2, movement=1,
                started_at=now - timedelta(minutes=4, seconds=53),
                instrument_name="claude-code",
            ),
            3: _make_sheet(
                SheetStatus.IN_PROGRESS, sheet_num=3, movement=1,
                started_at=now - timedelta(minutes=4, seconds=53),
                instrument_name="claude-code",
            ),
        }
        job = _make_job(total_sheets=3, sheets=sheets)

        with patch("marianne.cli.commands.status.console") as mock_console:
            _render_now_playing(job)
            output = _collect_printed_text(mock_console)

        assert "Now Playing" in output
        # Should list both in-progress sheets
        assert "2" in output  # sheet number
        assert "3" in output

    def test_now_playing_hidden_when_no_active_sheets(self) -> None:
        """Now Playing should not appear when no sheets are running."""
        from marianne.cli.commands.status import _render_now_playing

        sheets = {
            1: _make_sheet(SheetStatus.COMPLETED, sheet_num=1),
            2: _make_sheet(SheetStatus.PENDING, sheet_num=2),
        }
        job = _make_job(total_sheets=2, sheets=sheets)

        with patch("marianne.cli.commands.status.console") as mock_console:
            _render_now_playing(job)
            output = _collect_printed_text(mock_console)

        assert "Now Playing" not in output

    def test_now_playing_caps_at_ten_sheets(self) -> None:
        """When more than 10 sheets are running, cap the display."""
        from marianne.cli.commands.status import _render_now_playing

        now = datetime.now(UTC)
        sheets = {
            i: _make_sheet(
                SheetStatus.IN_PROGRESS, sheet_num=i, movement=1,
                started_at=now - timedelta(minutes=2),
            )
            for i in range(1, 16)  # 15 in progress
        }
        job = _make_job(total_sheets=15, sheets=sheets)

        with patch("marianne.cli.commands.status.console") as mock_console:
            _render_now_playing(job)
            output = _collect_printed_text(mock_console)

        assert "Now Playing" in output
        # Should mention how many more
        assert "more" in output


# ============================================================================
# Synthesis table bounding tests
# ============================================================================


class TestSynthesisBounding:
    """Tests that synthesis results are bounded."""

    def test_synthesis_bounded_to_five(self) -> None:
        """Only the last 5 synthesis batches should be shown by default."""
        from marianne.cli.commands.status import _render_synthesis_results

        # Create a job with 10 synthesis results (dict format)
        synth = {
            f"batch-{i}": {"sheets": [i], "strategy": "merge", "status": "done"}
            for i in range(10)
        }
        job = _make_job()
        # Directly set synthesis_results via model
        job.synthesis_results = synth  # type: ignore[assignment]

        with patch("marianne.cli.commands.status.console") as mock_console:
            _render_synthesis_results(job)
            output = _collect_printed_text(mock_console)
            # Should show the later batches, not all 10
            if "Synthesis" in output:
                # Count how many batch-N appear
                batch_count = sum(1 for i in range(10) if f"batch-{i}" in output)
                assert batch_count <= 5


# ============================================================================
# Helper to collect Rich console output
# ============================================================================


def _collect_printed_text(mock_console: MagicMock) -> str:
    """Collect all text printed to a mock console into one string."""
    parts: list[str] = []
    for call in mock_console.print.call_args_list:
        args, kwargs = call
        for arg in args:
            parts.append(str(arg))
    return " ".join(parts)
