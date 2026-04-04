"""TDD tests for F-151: Instrument name in status display.

The data model (SheetState.instrument_name) is populated in both legacy
runner and baton paths. This test file covers the status display surface:
- Flat per-sheet table: shows Instrument column when any sheet has one
- Flat per-sheet table: suppresses Instrument column when no instruments
- Summary view (50+ sheets): includes instruments in summary
- Movement-grouped view: already shows instruments (verify)
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from mozart.core.checkpoint import CheckpointState, SheetState, SheetStatus


def _make_job(
    total: int = 3,
    instruments: dict[int, str] | None = None,
) -> CheckpointState:
    """Create a CheckpointState with optional instrument assignments."""
    job = CheckpointState(
        job_id="test-job",
        job_name="test-job",
        workspace=Path("/tmp/ws"),
        total_sheets=total,
    )
    for i in range(1, total + 1):
        instr = instruments.get(i) if instruments else None
        job.sheets[i] = SheetState(
            sheet_num=i,
            status=SheetStatus.COMPLETED,
            instrument_name=instr,
        )
    return job


class TestSheetDetailsTableInstrumentColumn:
    """create_sheet_details_table should include Instrument column when needed."""

    def test_instrument_column_present_when_sheets_have_instruments(self) -> None:
        """Table includes Instrument column when has_instruments=True."""
        from mozart.cli.output import create_sheet_details_table

        table = create_sheet_details_table(has_instruments=True)
        col_names = [c.header for c in table.columns]
        assert "Instrument" in col_names

    def test_instrument_column_absent_when_no_instruments(self) -> None:
        """Table omits Instrument column when has_instruments=False (default)."""
        from mozart.cli.output import create_sheet_details_table

        table = create_sheet_details_table()
        col_names = [c.header for c in table.columns]
        assert "Instrument" not in col_names

    def test_instrument_column_absent_by_default(self) -> None:
        """Default create_sheet_details_table has no Instrument column."""
        from mozart.cli.output import create_sheet_details_table

        table = create_sheet_details_table(has_descriptions=True)
        col_names = [c.header for c in table.columns]
        assert "Instrument" not in col_names


class TestRenderSheetDetailsWithInstruments:
    """_render_sheet_details should show instruments when present."""

    def test_renders_instrument_when_sheets_have_instrument_name(self) -> None:
        """When any sheet has instrument_name, the instrument column appears."""
        from mozart.cli.commands.status import _render_sheet_details

        job = _make_job(3, instruments={1: "claude-code", 2: "gemini-cli", 3: "claude-code"})

        buf = StringIO()
        con = Console(file=buf, width=120, force_terminal=True)

        with patch("mozart.cli.commands.status.console", con):
            _render_sheet_details(job)

        output = buf.getvalue()
        assert "claude-code" in output
        assert "gemini-cli" in output

    def test_no_instrument_column_when_no_instruments(self) -> None:
        """When no sheet has instrument_name, the Instrument column is absent."""
        from mozart.cli.commands.status import _render_sheet_details

        job = _make_job(3)

        buf = StringIO()
        con = Console(file=buf, width=120, force_terminal=True)

        with patch("mozart.cli.commands.status.console", con):
            _render_sheet_details(job)

        output = buf.getvalue()
        assert "Instrument" not in output

    def test_renders_instrument_for_single_instrument_job(self) -> None:
        """Even when all sheets use the same instrument, column appears."""
        from mozart.cli.commands.status import _render_sheet_details

        job = _make_job(2, instruments={1: "claude-code", 2: "claude-code"})

        buf = StringIO()
        con = Console(file=buf, width=120, force_terminal=True)

        with patch("mozart.cli.commands.status.console", con):
            _render_sheet_details(job)

        output = buf.getvalue()
        assert "claude-code" in output


class TestSheetSummaryWithInstruments:
    """_render_sheet_summary should include instrument breakdown for 50+ sheet scores."""

    def test_summary_shows_instrument_breakdown(self) -> None:
        """For large scores, summary should list unique instruments."""
        from mozart.cli.commands.status import _render_sheet_summary

        # Create a 55-sheet job with mixed instruments
        instruments = {}
        for i in range(1, 56):
            instruments[i] = "claude-code" if i <= 30 else "gemini-cli"

        job = _make_job(55, instruments=instruments)

        buf = StringIO()
        con = Console(file=buf, width=120, force_terminal=True)

        with patch("mozart.cli.commands.status.console", con):
            _render_sheet_summary(job)

        output = buf.getvalue()
        # Should mention instruments somewhere in the summary
        assert "claude-code" in output or "gemini-cli" in output


class TestMovementGroupedInstrumentDisplay:
    """Movement-grouped view should show instrument per movement."""

    def test_movement_groups_collect_instruments(self) -> None:
        """_build_movement_groups includes instrument data."""
        from mozart.cli.commands.status import _build_movement_groups

        job = _make_job(4)
        # Set movement metadata
        job.sheets[1].movement = 1
        job.sheets[1].instrument_name = "claude-code"
        job.sheets[2].movement = 1
        job.sheets[2].instrument_name = "claude-code"
        job.sheets[3].movement = 2
        job.sheets[3].instrument_name = "gemini-cli"
        job.sheets[4].movement = 2
        job.sheets[4].instrument_name = "gemini-cli"

        groups = _build_movement_groups(job)

        # Movement 1 should have claude-code
        m1 = next(g for g in groups if g["movement"] == 1)
        assert m1["instrument"] == "claude-code"

        # Movement 2 should have gemini-cli
        m2 = next(g for g in groups if g["movement"] == 2)
        assert m2["instrument"] == "gemini-cli"

    def test_movement_groups_multi_instrument(self) -> None:
        """_build_movement_groups handles multiple instruments per movement."""
        from mozart.cli.commands.status import _build_movement_groups

        job = _make_job(2)
        job.sheets[1].movement = 1
        job.sheets[1].instrument_name = "claude-code"
        job.sheets[2].movement = 1
        job.sheets[2].instrument_name = "gemini-cli"

        groups = _build_movement_groups(job)

        m1 = groups[0]
        assert m1["instrument"] is None  # multiple instruments, no single
        assert sorted(m1["instruments"]) == ["claude-code", "gemini-cli"]
