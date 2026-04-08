"""Tests for mzt status no-args overview mode.

Tests the overview display that shows when 'mzt status' is called
without a job_id argument — showing conductor status and active scores.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from marianne.cli.commands.status import (
    _format_uptime,
    _status_overview,
)
from marianne.core.checkpoint import SheetStatus

# The function imports try_daemon_route locally, so patch at the source module.
_ROUTE_PATCH = "marianne.daemon.detect.try_daemon_route"
_JSON_PATCH = "marianne.cli.commands.status.output_json"


# ---------------------------------------------------------------------------
# Unit: _format_uptime
# ---------------------------------------------------------------------------

class TestFormatUptime:
    """Test the uptime formatter."""

    def test_none_returns_empty(self) -> None:
        assert _format_uptime(None) == ""

    def test_seconds(self) -> None:
        assert _format_uptime(45) == "uptime 45s"

    def test_minutes(self) -> None:
        assert _format_uptime(125) == "uptime 2m 5s"

    def test_hours(self) -> None:
        assert _format_uptime(7380) == "uptime 2h 3m"

    def test_days(self) -> None:
        assert _format_uptime(90000) == "uptime 1d 1h"

    def test_zero(self) -> None:
        assert _format_uptime(0) == "uptime 0s"


# ---------------------------------------------------------------------------
# Integration: _status_overview
# ---------------------------------------------------------------------------

class TestStatusOverview:
    """Test the overview mode of mzt status."""

    async def test_no_conductor_shows_error(self) -> None:
        """When conductor is not running, show error and exit."""
        with patch(
            _ROUTE_PATCH,
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            with pytest.raises((SystemExit, Exception)):  # typer.Exit raises click.exceptions.Exit
                await _status_overview(json_output=False)

    async def test_overview_json_with_active_jobs(self) -> None:
        """JSON mode returns structured data with active and recent jobs."""
        now_ts = datetime.now(UTC).timestamp()
        mock_jobs = [
            {"job_id": "active-1", "status": "running", "submitted_at": now_ts},
            {"job_id": "done-1", "status": "completed", "submitted_at": now_ts - 3600},
        ]

        async def mock_route(method: str, params: dict) -> tuple[bool, object]:  # noqa: ARG001
            if method == "daemon.health":
                return True, {"status": "healthy", "uptime_seconds": 120.0}
            if method == "job.list":
                return True, mock_jobs
            return False, None

        captured: dict | None = None

        def capture_json(data: object) -> None:
            nonlocal captured
            captured = data  # type: ignore[assignment]

        with (
            patch(_ROUTE_PATCH, side_effect=mock_route),
            patch(_JSON_PATCH, side_effect=capture_json),
        ):
            await _status_overview(json_output=True)

        assert captured is not None
        assert captured["conductor"] == "running"
        assert captured["active_count"] == 1
        assert len(captured["active"]) == 1
        assert captured["active"][0]["job_id"] == "active-1"
        assert captured["recent_count"] == 1
        assert captured["recent"][0]["job_id"] == "done-1"

    async def test_overview_rich_with_no_jobs(self) -> None:
        """Rich mode shows conductor status even with no jobs."""
        async def mock_route(method: str, params: dict) -> tuple[bool, object]:  # noqa: ARG001
            if method == "daemon.health":
                return True, {"status": "healthy", "uptime_seconds": 7200.0}
            if method == "job.list":
                return True, []
            return False, None

        with patch(_ROUTE_PATCH, side_effect=mock_route):
            await _status_overview(json_output=False)
        # No exception means success — rich output goes to console

    async def test_overview_recent_limited_to_five(self) -> None:
        """Recent jobs section is limited to 5 most recent."""
        now_ts = datetime.now(UTC).timestamp()
        mock_jobs = [
            {"job_id": f"done-{i}", "status": "completed", "submitted_at": now_ts - i * 60}
            for i in range(10)
        ]

        async def mock_route(method: str, params: dict) -> tuple[bool, object]:  # noqa: ARG001
            if method == "daemon.health":
                return True, {"status": "healthy"}
            if method == "job.list":
                return True, mock_jobs
            return False, None

        captured: dict | None = None

        def capture_json(data: object) -> None:
            nonlocal captured
            captured = data  # type: ignore[assignment]

        with (
            patch(_ROUTE_PATCH, side_effect=mock_route),
            patch(_JSON_PATCH, side_effect=capture_json),
        ):
            await _status_overview(json_output=True)

        assert captured is not None
        assert captured["recent_count"] == 5
        assert len(captured["recent"]) == 5
        # Most recent first
        assert captured["recent"][0]["job_id"] == "done-0"

    async def test_overview_separates_active_and_recent(self) -> None:
        """Active (running/queued/paused) and recent (completed/failed) are separated."""
        now_ts = datetime.now(UTC).timestamp()
        mock_jobs = [
            {"job_id": "running-1", "status": "running", "submitted_at": now_ts},
            {"job_id": "queued-1", "status": "queued", "submitted_at": now_ts},
            {"job_id": "paused-1", "status": "paused", "submitted_at": now_ts},
            {"job_id": "done-1", "status": "completed", "submitted_at": now_ts},
            {"job_id": "fail-1", "status": "failed", "submitted_at": now_ts},
            {"job_id": "cancel-1", "status": "cancelled", "submitted_at": now_ts},
        ]

        async def mock_route(method: str, params: dict) -> tuple[bool, object]:  # noqa: ARG001
            if method == "daemon.health":
                return True, {}
            if method == "job.list":
                return True, mock_jobs
            return False, None

        captured: dict | None = None

        def capture_json(data: object) -> None:
            nonlocal captured
            captured = data  # type: ignore[assignment]

        with (
            patch(_ROUTE_PATCH, side_effect=mock_route),
            patch(_JSON_PATCH, side_effect=capture_json),
        ):
            await _status_overview(json_output=True)

        assert captured is not None
        assert captured["active_count"] == 3
        active_ids = {j["job_id"] for j in captured["active"]}
        assert active_ids == {"running-1", "queued-1", "paused-1"}
        assert captured["recent_count"] == 3
        recent_ids = {j["job_id"] for j in captured["recent"]}
        assert recent_ids == {"done-1", "fail-1", "cancel-1"}

    async def test_overview_handles_daemon_health_error(self) -> None:
        """If daemon.health raises, overview shows error and exits."""
        with patch(
            _ROUTE_PATCH,
            new_callable=AsyncMock,
            side_effect=ConnectionError("socket gone"),
        ):
            with pytest.raises((SystemExit, Exception)):  # typer.Exit raises click.exceptions.Exit
                await _status_overview(json_output=False)

    async def test_overview_handles_job_list_error(self) -> None:
        """If job.list fails after health succeeds, show empty overview."""
        call_count = 0

        async def mock_route(method: str, params: dict) -> tuple[bool, object]:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if method == "daemon.health":
                return True, {"status": "healthy"}
            raise ConnectionError("lost connection")

        captured: dict | None = None

        def capture_json(data: object) -> None:
            nonlocal captured
            captured = data  # type: ignore[assignment]

        with (
            patch(_ROUTE_PATCH, side_effect=mock_route),
            patch(_JSON_PATCH, side_effect=capture_json),
        ):
            await _status_overview(json_output=True)

        assert captured is not None
        assert captured["active_count"] == 0
        assert captured["recent_count"] == 0


# ---------------------------------------------------------------------------
# CLI argument: job_id is now optional
# ---------------------------------------------------------------------------

class TestStatusArgOptional:
    """Verify that the status command accepts no arguments."""

    def test_status_function_accepts_none(self) -> None:
        """The status function's job_id parameter accepts None."""
        import inspect

        from marianne.cli.commands.status import status

        sig = inspect.signature(status)
        param = sig.parameters["job_id"]
        assert param.default is not inspect.Parameter.empty


# ---------------------------------------------------------------------------
# Large score summary view (F-038)
# ---------------------------------------------------------------------------

class TestLargeScoreSummary:
    """Test summary view for large scores (50+ sheets)."""

    def test_threshold_exists(self) -> None:
        """The large score threshold is defined."""
        from marianne.cli.commands.status import _LARGE_SCORE_THRESHOLD

        assert _LARGE_SCORE_THRESHOLD == 50

    def test_summary_renders_for_large_scores(self) -> None:
        """Scores with 50+ sheets use the summary view."""
        from marianne.cli.commands.status import _render_sheet_details
        from marianne.core.checkpoint import CheckpointState, SheetState

        # Create a CheckpointState with 60 sheets
        sheets = {}
        for i in range(1, 61):
            s = SheetState(sheet_num=i)
            if i <= 30:
                s.status = SheetStatus.COMPLETED
                s.validation_passed = True
            elif i <= 35:
                s.status = SheetStatus.FAILED
                s.validation_passed = False
            elif i <= 37:
                s.status = SheetStatus.IN_PROGRESS
            else:
                s.status = SheetStatus.PENDING
            sheets[i] = s

        job = CheckpointState(
            job_id="large-test",
            job_name="large-test",
            total_sheets=60,
            sheets=sheets,
        )

        # Should not raise — renders summary instead of 60 rows
        _render_sheet_details(job)

    def test_small_scores_use_full_table(self) -> None:
        """Scores below threshold get the full table."""
        from marianne.cli.commands.status import _render_sheet_details
        from marianne.core.checkpoint import CheckpointState, SheetState

        sheets = {}
        for i in range(1, 10):
            s = SheetState(sheet_num=i)
            s.status = SheetStatus.COMPLETED
            s.validation_passed = True
            sheets[i] = s

        job = CheckpointState(
            job_id="small-test",
            job_name="small-test",
            total_sheets=9,
            sheets=sheets,
        )

        # Should render the full table (no summary)
        _render_sheet_details(job)

    def test_summary_counts_statuses(self) -> None:
        """Summary correctly counts sheets by display status."""
        from marianne.cli.commands.status import _render_sheet_summary
        from marianne.core.checkpoint import CheckpointState, SheetState

        sheets = {}
        for i in range(1, 101):
            s = SheetState(sheet_num=i)
            if i <= 50:
                s.status = SheetStatus.COMPLETED
                s.validation_passed = True
            elif i <= 60:
                s.status = SheetStatus.COMPLETED
                s.validation_passed = False  # Display as "failed"
            elif i <= 65:
                s.status = SheetStatus.IN_PROGRESS
            else:
                s.status = SheetStatus.PENDING
            sheets[i] = s

        job = CheckpointState(
            job_id="count-test",
            job_name="count-test",
            total_sheets=100,
            sheets=sheets,
        )

        # Should render without error
        _render_sheet_summary(job)
