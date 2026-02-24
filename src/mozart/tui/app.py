"""Textual TUI application for ``mozart top`` — real-time system monitor.

Provides ``MonitorApp``, a Textual ``App`` subclass with a job-centric layout
matching the design document: header bar, jobs panel, event timeline, and
detail drill-down panel.

Usage:
    app = MonitorApp(reader=reader)
    app.run()
"""

from __future__ import annotations

import time
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static

from mozart.core.logging import get_logger
from mozart.daemon.profiler.models import SystemSnapshot
from mozart.tui.panels.detail import DetailPanel
from mozart.tui.panels.header import HeaderPanel
from mozart.tui.panels.jobs import JobsPanel
from mozart.tui.panels.timeline import TimelinePanel
from mozart.tui.reader import MonitorReader

_logger = get_logger("tui.app")


class SectionLabel(Static):
    """A styled section divider label."""

    DEFAULT_CSS = """
    SectionLabel {
        height: 1;
        background: $primary-background;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    """


class MonitorApp(App[None]):
    """Real-time Mozart system monitor TUI.

    Reads data from ``MonitorReader`` and renders a job-centric layout
    with live-updating metrics, event timeline, and drill-down detail.
    """

    TITLE = "Mozart Monitor"
    SUB_TITLE = "mozart top"

    CSS = """
    Screen {
        layout: vertical;
    }

    #header-panel {
        height: 2;
        dock: top;
    }

    #jobs-section-label {
        height: 1;
    }

    #jobs-panel {
        height: 1fr;
        min-height: 5;
    }

    #timeline-section-label {
        height: 1;
    }

    #timeline-panel {
        height: auto;
        min-height: 5;
        max-height: 12;
    }

    #detail-section-label {
        height: 1;
    }

    #detail-panel {
        height: auto;
        min-height: 3;
        max-height: 10;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("j", "navigate_down", "Down", show=False),
        Binding("down", "navigate_down", "Down", show=False),
        Binding("k", "navigate_up", "Up", show=False),
        Binding("up", "navigate_up", "Up", show=False),
        Binding("enter", "drill_down", "Detail", show=True),
        Binding("s", "cycle_sort", "Sort", show=True),
        Binding("f", "filter_job", "Filter", show=True),
        Binding("l", "show_learning", "Learning", show=True),
    ]

    def __init__(
        self,
        reader: MonitorReader | None = None,
        refresh_interval: float = 2.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._reader = reader or MonitorReader()
        self._refresh_interval = refresh_interval
        self._latest_snapshot: SystemSnapshot | None = None
        self._conductor_up: bool = False
        self._mount_time: float = 0.0

    def compose(self) -> ComposeResult:
        """Build the widget tree."""
        yield Header()
        yield HeaderPanel(id="header-panel")
        yield SectionLabel("Active Jobs", id="jobs-section-label")
        yield JobsPanel(id="jobs-panel")
        yield SectionLabel("Event Timeline", id="timeline-section-label")
        yield TimelinePanel(id="timeline-panel")
        yield SectionLabel("Detail (\u2191\u2193 select, Enter drill)", id="detail-section-label")
        yield DetailPanel(id="detail-panel")
        yield Footer()

    def on_mount(self) -> None:
        """Start the data refresh timer on mount."""
        self._mount_time = time.monotonic()
        self.set_interval(self._refresh_interval, self.refresh_data)
        # Initial refresh
        self.call_later(self.refresh_data)
        # Show empty detail on start
        detail = self.query_one("#detail-panel", DetailPanel)
        detail.show_empty()

    async def refresh_data(self) -> None:
        """Fetch latest data from the reader and update all panels."""
        try:
            snapshot = await self._reader.get_latest_snapshot()
            self._latest_snapshot = snapshot

            # Detect conductor status via IPC client
            if self._reader._ipc_client is not None:
                try:
                    self._conductor_up = await self._reader._ipc_client.is_daemon_running()
                except Exception:
                    self._conductor_up = False
            else:
                self._conductor_up = snapshot is not None

            # Use conductor uptime from the snapshot (actual daemon
            # lifetime) instead of TUI session time.
            uptime = 0.0
            if snapshot is not None:
                uptime = snapshot.conductor_uptime_seconds

            # Update header
            header = self.query_one("#header-panel", HeaderPanel)
            header.update_data(
                snapshot=snapshot,
                conductor_up=self._conductor_up,
                uptime_seconds=uptime,
            )

            # Update jobs panel
            jobs = self.query_one("#jobs-panel", JobsPanel)
            jobs.update_data(snapshot)

            # Update timeline with recent events
            since = time.time() - 300.0  # last 5 minutes
            events = await self._reader.get_events(since, limit=50)

            # REVIEW FIX 3: Sequential IPC call — don't parallel with above
            observer_events = await self._reader.get_observer_events(limit=50)

            timeline = self.query_one("#timeline-panel", TimelinePanel)
            timeline.update_data(events=events, observer_events=observer_events)

        except Exception:
            _logger.debug("refresh_data failed", exc_info=True)

    # ------------------------------------------------------------------
    # Key actions
    # ------------------------------------------------------------------

    def action_navigate_down(self) -> None:
        """Move selection down in the jobs panel."""
        jobs = self.query_one("#jobs-panel", JobsPanel)
        jobs.select_next()
        self._update_detail()

    def action_navigate_up(self) -> None:
        """Move selection up in the jobs panel."""
        jobs = self.query_one("#jobs-panel", JobsPanel)
        jobs.select_prev()
        self._update_detail()

    def action_drill_down(self) -> None:
        """Show detail for the selected item."""
        self._update_detail()

    def action_cycle_sort(self) -> None:
        """Cycle sort order (placeholder for future implementation)."""
        self.notify("Sort: CPU \u2192 MEM \u2192 AGE (not yet implemented)")

    def action_filter_job(self) -> None:
        """Filter by job ID (placeholder for future implementation)."""
        self.notify("Filter by job_id (not yet implemented)")

    def action_show_learning(self) -> None:
        """Show learning insights (placeholder for future implementation)."""
        self.notify("Learning insights (not yet implemented)")

    def _update_detail(self) -> None:
        """Update the detail panel with the currently selected item."""
        jobs = self.query_one("#jobs-panel", JobsPanel)
        detail = self.query_one("#detail-panel", DetailPanel)
        detail.show_item(jobs.selected_item)


__all__ = ["MonitorApp"]
