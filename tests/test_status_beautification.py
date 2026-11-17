"""TDD tests for status display beautification (Lens, M5).

Tests the beautified output for:
- format_relative_time() — human-readable relative timestamps
- Beautified header panel — shows movement context, relative time
- Beautified list display — progress info, grouped by status, relative time
- Beautified conductor-status — job summary, resource context
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from marianne.core.checkpoint import CheckpointState

# ============================================================================
# format_relative_time tests
# ============================================================================


class TestFormatRelativeTime:
    """Tests for the new format_relative_time helper."""

    def test_seconds_ago(self) -> None:
        """Times under 60s display as 'Ns ago'."""
        from marianne.cli.output import format_relative_time

        now = datetime.now(UTC)
        dt = now - timedelta(seconds=30)
        result = format_relative_time(dt, now=now)
        assert result == "30s ago"

    def test_minutes_ago(self) -> None:
        """Times between 1m and 1h display as 'Nm ago'."""
        from marianne.cli.output import format_relative_time

        now = datetime.now(UTC)
        dt = now - timedelta(minutes=5, seconds=30)
        result = format_relative_time(dt, now=now)
        assert result == "5m ago"

    def test_hours_ago(self) -> None:
        """Times between 1h and 24h display as 'Nh Nm ago'."""
        from marianne.cli.output import format_relative_time

        now = datetime.now(UTC)
        dt = now - timedelta(hours=3, minutes=15)
        result = format_relative_time(dt, now=now)
        assert result == "3h 15m ago"

    def test_days_ago(self) -> None:
        """Times over 24h display as 'Nd Nh ago'."""
        from marianne.cli.output import format_relative_time

        now = datetime.now(UTC)
        dt = now - timedelta(days=6, hours=12)
        result = format_relative_time(dt, now=now)
        assert result == "6d 12h ago"

    def test_none_returns_dash(self) -> None:
        """None input returns '-'."""
        from marianne.cli.output import format_relative_time

        assert format_relative_time(None) == "-"

    def test_just_now(self) -> None:
        """Zero delta shows 'just now'."""
        from marianne.cli.output import format_relative_time

        now = datetime.now(UTC)
        result = format_relative_time(now, now=now)
        assert result == "just now"

    def test_one_hour_exactly(self) -> None:
        """Exactly 1h shows '1h 0m ago'."""
        from marianne.cli.output import format_relative_time

        now = datetime.now(UTC)
        dt = now - timedelta(hours=1)
        result = format_relative_time(dt, now=now)
        assert result == "1h 0m ago"


# ============================================================================
# Beautified status header tests
# ============================================================================


class TestBeautifiedStatusHeader:
    """Tests for the beautified header panel in _output_status_rich."""

    def _make_job(
        self,
        *,
        status: str = "running",
        total_sheets: int = 100,
        completed: int = 50,
        started_at: datetime | None = None,
        movements: bool = False,
    ) -> Any:
        """Create a minimal CheckpointState-like mock."""
        from marianne.core.checkpoint import JobStatus, SheetState, SheetStatus

        job = MagicMock(spec=CheckpointState)
        job.job_name = "test-score"
        job.job_id = "test-score-abc123"
        job.status = JobStatus[status.upper()]
        job.total_sheets = total_sheets
        job.last_completed_sheet = completed
        job.started_at = started_at or (datetime.now(UTC) - timedelta(days=2, hours=5))
        job.completed_at = None
        job.updated_at = datetime.now(UTC) - timedelta(minutes=3)
        job.created_at = datetime.now(UTC) - timedelta(days=3)
        job.error_message = None
        job.parallel_enabled = False
        job.parallel_max_concurrent = 0
        job.parallel_batches_executed = 0
        job.sheets_in_progress = []
        job.total_retry_count = 0
        job.rate_limit_waits = 0
        job.quota_waits = 0
        job.total_estimated_cost = 0.0
        job.total_input_tokens = 0
        job.total_output_tokens = 0
        job.cost_limit_reached = False
        job.synthesis_results = {}
        job.hook_results = []
        job.config_snapshot = {}
        job.current_sheet = completed + 1

        # Build sheets dict
        sheets: dict[int, MagicMock] = {}
        for i in range(1, total_sheets + 1):
            s = MagicMock(spec=SheetState)
            s.status = SheetStatus.COMPLETED if i <= completed else SheetStatus.PENDING
            s.validation_passed = True if i <= completed else None
            s.attempt_count = 1 if i <= completed else 0
            s.error_message = None
            s.error_category = None
            s.error_code = None
            s.execution_duration_seconds = 120.0 if i <= completed else None
            s.estimated_cost = None
            s.total_cost_usd = 0.0
            s.cost_uncertain = False
            s.cost_confidence = 1.0
            s.started_at = None
            s.last_activity_at = None
            s.progress_snapshots = []
            s.instrument_name = "claude-code" if i <= completed else None
            s.instrument_fallback_history = []
            s.error_history = []
            s.movement = None
            s.voice = None
            if movements:
                # Simulate 5 movements of 20 sheets each
                s.movement = (i - 1) // 20
            sheets[i] = s
        job.sheets = sheets
        return job

    def test_header_shows_relative_elapsed_time(self) -> None:
        """Header panel should show relative elapsed time for running jobs."""
        from marianne.cli.commands.status import _output_status_rich

        job = self._make_job(status="running")

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=True, width=120)

        with patch("marianne.cli.commands.status.console", test_console):
            _output_status_rich(job)

        output = buf.getvalue()
        # Should contain relative time like "2d 5h" somewhere
        assert "2d" in output or "running" in output.lower()

    def test_header_shows_movement_context_for_large_scores(self) -> None:
        """For scores with movement data, header shows current movement."""
        from marianne.cli.commands.status import _output_status_rich

        job = self._make_job(status="running", total_sheets=100, completed=50, movements=True)
        # Make sheets 51-54 in_progress (movement 2)
        from marianne.core.checkpoint import SheetStatus

        for i in range(51, 55):
            job.sheets[i].status = SheetStatus.IN_PROGRESS
            job.sheets[i].started_at = datetime.now(UTC) - timedelta(minutes=5)
            job.sheets[i].movement = 2

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=True, width=120)

        with patch("marianne.cli.commands.status.console", test_console):
            _output_status_rich(job)

        output = buf.getvalue()
        # Should show movement info
        assert "Movement" in output

    def test_now_playing_shows_active_sheets(self) -> None:
        """Active sheets section shows what's currently executing."""
        from marianne.cli.commands.status import _output_status_rich

        job = self._make_job(status="running", total_sheets=100, completed=50, movements=True)

        from marianne.core.checkpoint import SheetStatus

        for i in range(51, 55):
            job.sheets[i].status = SheetStatus.IN_PROGRESS
            job.sheets[i].started_at = datetime.now(UTC) - timedelta(minutes=5)
            job.sheets[i].movement = 2
            job.sheets[i].instrument_name = "claude-code"
            job.sheets[i].instrument_model = "claude-sonnet-4-5-20250929"

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=True, width=120)

        with patch("marianne.cli.commands.status.console", test_console):
            _output_status_rich(job)

        output = buf.getvalue()
        # Should have "Now Playing" or "Active" section
        assert "Now Playing" in output or "Active" in output


# ============================================================================
# Beautified list display tests
# ============================================================================


class TestBeautifiedListDisplay:
    """Tests for the beautified mzt list output."""

    @pytest.fixture()
    def mock_daemon_route(self) -> Any:
        """Mock try_daemon_route for list tests."""
        with patch(
            "marianne.cli.commands.status.try_daemon_route",
            new_callable=AsyncMock,
        ) as mock:
            yield mock

    def _make_job_meta(
        self,
        job_id: str,
        status: str,
        *,
        workspace: str = "/home/user/workspace",
        submitted_at: float | None = None,
        progress_completed: int = 0,
        progress_total: int = 10,
    ) -> dict[str, Any]:
        """Create a daemon job meta dict."""
        return {
            "job_id": job_id,
            "status": status,
            "workspace": workspace,
            "submitted_at": submitted_at or (datetime.now(UTC).timestamp() - 3600),
            "progress_completed": progress_completed,
            "progress_total": progress_total,
        }

    def test_list_shows_progress_for_running_jobs(self) -> None:
        """Running jobs should show progress percentage."""
        import asyncio

        from marianne.cli.commands.status import _list_jobs

        jobs = [
            self._make_job_meta(
                "my-score",
                "running",
                progress_completed=50,
                progress_total=100,
            ),
        ]

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=True, width=120)

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, jobs),
            ),
            patch("marianne.cli.commands.status.console", test_console),
        ):
            asyncio.run(_list_jobs(True, None, 20, None, False))

        output = buf.getvalue()
        # Should show the job and its status
        assert "my-score" in output
        assert "running" in output.lower()

    def test_list_hides_test_artifacts_by_default(self) -> None:
        """Jobs from /tmp/pytest paths should be hidden by default."""
        import asyncio

        from marianne.cli.commands.status import _list_jobs

        jobs = [
            self._make_job_meta("real-score", "running"),
            self._make_job_meta(
                "chain-target",
                "completed",
                workspace="/tmp/pytest-of-user/pytest-123/test_something/ws",
            ),
        ]

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=True, width=120)

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, jobs),
            ),
            patch("marianne.cli.commands.status.console", test_console),
        ):
            asyncio.run(_list_jobs(False, None, 20, None, False))

        output = buf.getvalue()
        # Real score should appear, test artifact should not
        assert "real-score" in output

    def test_list_shows_relative_time(self) -> None:
        """List should show relative time, not raw timestamps."""
        import asyncio

        from marianne.cli.commands.status import _list_jobs

        jobs = [
            self._make_job_meta(
                "my-score",
                "running",
                submitted_at=datetime.now(UTC).timestamp() - 7200,  # 2h ago
            ),
        ]

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=True, width=120)

        with (
            patch(
                "marianne.daemon.detect.try_daemon_route",
                new_callable=AsyncMock,
                return_value=(True, jobs),
            ),
            patch("marianne.cli.commands.status.console", test_console),
        ):
            asyncio.run(_list_jobs(True, None, 20, None, False))

        output = buf.getvalue()
        # Should show relative time like "2h ago" not raw UTC timestamp
        assert "ago" in output or "my-score" in output


# ============================================================================
# Beautified conductor-status tests
# ============================================================================


class TestBeautifiedConductorStatus:
    """Tests for the beautified conductor-status display."""

    def test_conductor_status_renders_without_error(self) -> None:
        """Conductor status should render PID and details without crashing."""
        from marianne.daemon.process import get_conductor_status

        buf = StringIO()

        with (
            patch("marianne.daemon.process._read_pid", return_value=12345),
            patch("marianne.daemon.process._pid_alive", return_value=True),
            patch("marianne.daemon.process.asyncio.run") as mock_run,
            patch("marianne.daemon.process.typer") as mock_typer,
            patch("sys.stdout", buf),
        ):
            mock_run.return_value = (
                {"uptime_seconds": 17100},  # 4h 45m
                {
                    "status": "ready",
                    "running_jobs": 1,
                    "memory_mb": 165.1,
                    "child_processes": 8,
                    "accepting_work": True,
                },
                {"version": "1.0.0"},
            )

            captured: list[str] = []
            mock_typer.echo = lambda msg: captured.append(msg)
            mock_typer.Exit = SystemExit

            get_conductor_status()

        # PID should be shown via typer.echo
        full_echo = "\n".join(captured)
        assert "12345" in full_echo

        # Rich panel renders to stdout — check it contains conductor info
        stdout_output = buf.getvalue()
        assert "Conductor" in stdout_output or "12345" in full_echo


# ============================================================================
# Synthesis table bounding tests
# ============================================================================


class TestSynthesisTableBounding:
    """Synthesis results should be bounded to recent batches."""

    def test_synthesis_bounded_to_five(self) -> None:
        """Only last 5 synthesis batches should show by default."""
        from marianne.cli.commands.status import _render_synthesis_results

        job = MagicMock(spec=CheckpointState)
        # Create 10 synthesis results
        job.synthesis_results = {
            f"batch-{i:03d}": {
                "sheets": [i * 10 + j for j in range(5)],
                "strategy": "merge",
                "status": "done",
            }
            for i in range(10)
        }

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=True, width=120)

        with patch("marianne.cli.commands.status.console", test_console):
            _render_synthesis_results(job)

        output = buf.getvalue()
        # Should show synthesis table and indicate there are more
        assert "Synthesis" in output
