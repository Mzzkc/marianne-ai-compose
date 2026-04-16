"""Tests for movement-grouped status display (M3 step 31).

Movement grouping transforms the flat sheet table into a hierarchical view:
- Sheets with `movement` populated are grouped by movement number
- Voices within a movement are shown as sub-items
- Legacy jobs (no movement data) fall through to the existing flat display
- Large scores with movements get the grouped view, not the summary view
- JSON output includes movement grouping metadata

TDD: these tests are written BEFORE the implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from marianne.core.checkpoint import (
    CheckpointState,
    JobStatus,
    SheetState,
    SheetStatus,
)


def _make_sheet(
    num: int,
    status: SheetStatus = SheetStatus.COMPLETED,
    movement: int | None = None,
    voice: int | None = None,
    instrument_name: str | None = None,
    validation_passed: bool | None = True,
    duration: float | None = 5.0,
    error_message: str | None = None,
) -> SheetState:
    """Helper to build a SheetState with movement metadata."""
    return SheetState(
        sheet_num=num,
        status=status,
        movement=movement,
        voice=voice,
        instrument_name=instrument_name,
        validation_passed=validation_passed,
        execution_duration_seconds=duration,
        error_message=error_message,
        started_at=datetime(2026, 3, 29, 12, 0, 0, tzinfo=UTC),
        completed_at=(
            datetime(2026, 3, 29, 12, 0, int(duration or 0), tzinfo=UTC)
            if status == SheetStatus.COMPLETED
            else None
        ),
    )


def _make_job(
    sheets: dict[int, SheetState],
    total_movements: int | None = None,
) -> CheckpointState:
    """Helper to build a CheckpointState with sheets."""
    completed = sum(
        1
        for s in sheets.values()
        if s.status == SheetStatus.COMPLETED and s.validation_passed is not False
    )
    return CheckpointState(
        job_id="test-job",
        job_name="test-job",
        status=JobStatus.RUNNING,
        total_sheets=len(sheets),
        last_completed_sheet=completed,
        current_sheet=max(sheets.keys()) if sheets else 0,
        sheets=sheets,
        total_movements=total_movements,
        created_at=datetime(2026, 3, 29, 12, 0, 0, tzinfo=UTC),
        started_at=datetime(2026, 3, 29, 12, 0, 0, tzinfo=UTC),
    )


def _capture() -> tuple[Console, StringIO]:
    """Create a capture console for testing."""
    buf = StringIO()
    con = Console(file=buf, color_system=None, width=120)
    return con, buf


class TestMovementGrouping:
    """Tests for _render_movement_grouped_details."""

    def test_three_movement_job_shows_movement_headers(self) -> None:
        """A 3-movement job with movement metadata shows grouped display."""
        from marianne.cli.commands.status import _render_movement_grouped_details

        sheets = {
            1: _make_sheet(1, SheetStatus.COMPLETED, movement=1),
            2: _make_sheet(2, SheetStatus.COMPLETED, movement=2),
            3: _make_sheet(3, SheetStatus.IN_PROGRESS, movement=3),
        }
        job = _make_job(sheets, total_movements=3)

        con, buf = _capture()
        _render_movement_grouped_details(job, con)
        output = buf.getvalue()

        assert "Movement 1" in output
        assert "Movement 2" in output
        assert "Movement 3" in output

    def test_voices_shown_as_sub_items(self) -> None:
        """Voices within a movement appear as indented sub-items."""
        from marianne.cli.commands.status import _render_movement_grouped_details

        sheets = {
            1: _make_sheet(1, SheetStatus.COMPLETED, movement=1),
            2: _make_sheet(2, SheetStatus.COMPLETED, movement=2, voice=1),
            3: _make_sheet(3, SheetStatus.COMPLETED, movement=2, voice=2),
            4: _make_sheet(4, SheetStatus.COMPLETED, movement=2, voice=3),
            5: _make_sheet(5, SheetStatus.PENDING, movement=3),
        }
        job = _make_job(sheets, total_movements=3)

        con, buf = _capture()
        _render_movement_grouped_details(job, con)
        output = buf.getvalue()

        assert "3 voices" in output
        assert "Voice 1" in output
        assert "Voice 2" in output
        assert "Voice 3" in output

    def test_completed_movement_shows_checkmark(self) -> None:
        """Completed movements display a completion indicator."""
        from marianne.cli.commands.status import _render_movement_grouped_details

        sheets = {
            1: _make_sheet(1, SheetStatus.COMPLETED, movement=1),
            2: _make_sheet(2, SheetStatus.PENDING, movement=2),
        }
        job = _make_job(sheets, total_movements=2)

        con, buf = _capture()
        _render_movement_grouped_details(job, con)
        output = buf.getvalue()

        # Movement 1 should have a completion indicator
        lines = output.split("\n")
        m1_line = [line for line in lines if "Movement 1" in line][0]
        assert "completed" in m1_line.lower() or "\u2713" in m1_line

    def test_running_movement_shows_progress(self) -> None:
        """A movement with in-progress sheets shows running indicator."""
        from marianne.cli.commands.status import _render_movement_grouped_details

        sheets = {
            1: _make_sheet(1, SheetStatus.COMPLETED, movement=1),
            2: _make_sheet(2, SheetStatus.IN_PROGRESS, movement=2, voice=1),
            3: _make_sheet(3, SheetStatus.COMPLETED, movement=2, voice=2),
            4: _make_sheet(4, SheetStatus.PENDING, movement=2, voice=3),
        }
        job = _make_job(sheets, total_movements=2)

        con, buf = _capture()
        _render_movement_grouped_details(job, con)
        output = buf.getvalue()

        lines = output.split("\n")
        m2_line = [line for line in lines if "Movement 2" in line][0]
        # Should show partial completion: 1/3 or similar
        assert "1/3" in m2_line or "running" in m2_line.lower()

    def test_instrument_shown_when_heterogeneous(self) -> None:
        """Instrument name shown when movements use different instruments."""
        from marianne.cli.commands.status import _render_movement_grouped_details

        sheets = {
            1: _make_sheet(
                1,
                SheetStatus.COMPLETED,
                movement=1,
                instrument_name="claude-code",
            ),
            2: _make_sheet(
                2,
                SheetStatus.PENDING,
                movement=2,
                instrument_name="gemini-cli",
            ),
        }
        job = _make_job(sheets, total_movements=2)

        con, buf = _capture()
        _render_movement_grouped_details(job, con)
        output = buf.getvalue()

        assert "claude-code" in output
        assert "gemini-cli" in output

    def test_instrument_hidden_when_uniform(self) -> None:
        """Instrument name NOT shown when all movements use the same one."""
        from marianne.cli.commands.status import _render_movement_grouped_details

        sheets = {
            1: _make_sheet(
                1,
                SheetStatus.COMPLETED,
                movement=1,
                instrument_name="claude-code",
            ),
            2: _make_sheet(
                2,
                SheetStatus.PENDING,
                movement=2,
                instrument_name="claude-code",
            ),
        }
        job = _make_job(sheets, total_movements=2)

        con, buf = _capture()
        _render_movement_grouped_details(job, con)
        output = buf.getvalue()

        # Instrument should not clutter the display when all the same
        # Count occurrences — the name may appear in the header but not per-movement
        lines = [line for line in output.split("\n") if "Movement" in line]
        instrument_mentions = sum(1 for line in lines if "claude-code" in line)
        assert instrument_mentions == 0

    def test_failed_movement_shows_error(self) -> None:
        """Failed movements show the error status clearly."""
        from marianne.cli.commands.status import _render_movement_grouped_details

        sheets = {
            1: _make_sheet(1, SheetStatus.COMPLETED, movement=1),
            2: _make_sheet(
                2,
                SheetStatus.COMPLETED,
                movement=2,
                validation_passed=False,
                error_message="Tests did not pass",
            ),
        }
        job = _make_job(sheets, total_movements=2)

        con, buf = _capture()
        _render_movement_grouped_details(job, con)
        output = buf.getvalue()

        lines = output.split("\n")
        m2_line = [line for line in lines if "Movement 2" in line][0]
        assert "failed" in m2_line.lower()

    def test_descriptions_from_config_snapshot(self) -> None:
        """Sheet descriptions from config_snapshot appear in grouped view."""
        from marianne.cli.commands.status import _render_movement_grouped_details

        sheets = {
            1: _make_sheet(1, SheetStatus.COMPLETED, movement=1),
            2: _make_sheet(2, SheetStatus.PENDING, movement=2),
        }
        job = _make_job(sheets, total_movements=2)
        job.config_snapshot = {
            "sheet": {
                "descriptions": {
                    "1": "Architecture planning",
                    "2": "Implementation",
                },
            },
        }

        con, buf = _capture()
        _render_movement_grouped_details(job, con)
        output = buf.getvalue()

        assert "Architecture planning" in output or "Implementation" in output


class TestMovementGroupingIntegration:
    """Tests for integration of movement grouping into the rendering pipeline."""

    def test_has_movement_data_returns_true(self) -> None:
        """_has_movement_data returns True when sheets have movement populated."""
        from marianne.cli.commands.status import _has_movement_data

        sheets = {
            1: _make_sheet(1, movement=1),
            2: _make_sheet(2, movement=2),
        }
        job = _make_job(sheets, total_movements=2)
        assert _has_movement_data(job) is True

    def test_has_movement_data_returns_false_for_legacy(self) -> None:
        """_has_movement_data returns False for legacy jobs without movement."""
        from marianne.cli.commands.status import _has_movement_data

        sheets = {
            1: _make_sheet(1),
            2: _make_sheet(2),
        }
        job = _make_job(sheets)
        assert _has_movement_data(job) is False

    def test_large_score_with_movements_uses_grouped_not_summary(self) -> None:
        """A score with 100+ sheets but movement data uses grouped view."""
        from marianne.cli.commands.status import _has_movement_data

        sheets = {}
        for i in range(1, 101):
            movement = (i - 1) // 10 + 1  # 10 sheets per movement
            sheets[i] = _make_sheet(i, movement=movement)
        job = _make_job(sheets, total_movements=10)

        assert _has_movement_data(job) is True


class TestMovementGroupedJSON:
    """Tests for movement grouping in JSON output."""

    def test_json_includes_movements_array(self) -> None:
        """JSON output includes a movements array when movement data exists."""
        from marianne.cli.commands.status import _build_movement_groups

        sheets = {
            1: _make_sheet(1, SheetStatus.COMPLETED, movement=1),
            2: _make_sheet(2, SheetStatus.COMPLETED, movement=2, voice=1),
            3: _make_sheet(3, SheetStatus.COMPLETED, movement=2, voice=2),
            4: _make_sheet(4, SheetStatus.PENDING, movement=3),
        }
        job = _make_job(sheets, total_movements=3)

        movements = _build_movement_groups(job)

        assert len(movements) == 3
        assert movements[0]["movement"] == 1
        assert movements[0]["sheet_count"] == 1
        assert movements[1]["movement"] == 2
        assert movements[1]["voice_count"] == 2
        assert movements[2]["movement"] == 3

    def test_movement_group_status_derived_correctly(self) -> None:
        """Each movement group has a derived status based on its sheets."""
        from marianne.cli.commands.status import _build_movement_groups

        sheets = {
            1: _make_sheet(1, SheetStatus.COMPLETED, movement=1),
            2: _make_sheet(2, SheetStatus.IN_PROGRESS, movement=2),
            3: _make_sheet(3, SheetStatus.PENDING, movement=3),
        }
        job = _make_job(sheets, total_movements=3)

        movements = _build_movement_groups(job)

        assert movements[0]["status"] == "completed"
        assert movements[1]["status"] == "running"
        assert movements[2]["status"] == "pending"
