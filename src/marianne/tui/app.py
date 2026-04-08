"""Textual TUI application for ``mzt top`` — real-time system monitor.

Provides ``MonitorApp``, a Textual ``App`` subclass with a job-centric layout
matching the design document: header bar, jobs panel, event timeline, and
detail drill-down panel.

Usage:
    app = MonitorApp(reader=reader)
    app.run()
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static

from marianne.core.logging import get_logger
from marianne.daemon.profiler.models import SystemSnapshot
from marianne.tui.panels.detail import DetailPanel
from marianne.tui.panels.header import HeaderPanel
from marianne.tui.panels.jobs import JobsPanel
from marianne.tui.panels.timeline import TimelinePanel
from marianne.tui.reader import MonitorReader

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
    """Real-time Marianne system monitor TUI.

    Reads data from ``MonitorReader`` and renders a job-centric layout
    with live-updating metrics, event timeline, and drill-down detail.
    """

    TITLE = "Marianne Monitor"
    SUB_TITLE = "mzt top"

    CSS = """
    Screen {
        layout: vertical;
    }

    #header-panel {
        height: 3;
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
        Binding("x", "cancel_job", "Kill", show=True),
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
        self._stream_task: asyncio.Task[None] | None = None

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

    async def on_mount(self) -> None:
        """Start the event stream listener on mount."""
        self._mount_time = time.monotonic()
        # Initial data load
        await self.refresh_data()

        # Start background event stream listener
        self._stream_task = asyncio.create_task(
            self._run_event_stream(), name="tui-event-stream"
        )

        # Show empty detail on start
        detail = self.query_one("#detail-panel", DetailPanel)
        detail.show_empty()

    async def on_unmount(self) -> None:
        """Clean up the stream task."""
        if self._stream_task is not None:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

    async def _run_event_stream(self) -> None:
        """Background task that listens for EventBus events via IPC."""
        try:
            async for event in self._reader.stream_events():
                await self._handle_event(event)
        except Exception:
            _logger.debug("event_stream_failed", exc_info=True)
            # Retry after delay
            await asyncio.sleep(5.0)
            self._stream_task = asyncio.create_task(self._run_event_stream())

    async def _handle_event(self, event: dict[str, Any]) -> None:
        """Dispatch an EventBus event to the appropriate panel updates."""
        evt_type = event.get("event", "")

        if evt_type == "monitor.snapshot":
            # Real-time system metrics
            snapshot_data = event.get("data")
            if snapshot_data:
                snapshot = SystemSnapshot(**snapshot_data)
                self._latest_snapshot = snapshot

                # Update header
                header = self.query_one("#header-panel", HeaderPanel)
                header.update_data(
                    snapshot=snapshot,
                    conductor_up=True,
                    uptime_seconds=snapshot.conductor_uptime_seconds,
                )

                # Update jobs panel
                jobs = self.query_one("#jobs-panel", JobsPanel)
                jobs.update_data(snapshot)

        elif evt_type.startswith(("sheet.", "monitor.anomaly", "observer.")):
            # Lifecycle or anomaly events -> update timeline incrementally
            timeline = self.query_one("#timeline-panel", TimelinePanel)
            timeline.add_event(event)

            if evt_type in ("sheet.started", "sheet.completed", "sheet.failed"):
                await self.refresh_data()

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

            uptime = 0.0
            if snapshot is not None:
                uptime = snapshot.conductor_uptime_seconds

            header = self.query_one("#header-panel", HeaderPanel)
            header.update_data(
                snapshot=snapshot,
                conductor_up=self._conductor_up,
                uptime_seconds=uptime,
            )

            # Update timeline with recent events (only on initial load/refresh)
            # Normal updates come through the event stream
            since = time.time() - 300.0
            events = await self._reader.get_events(since, limit=50)
            observer_events = await self._reader.get_observer_events(limit=50)

            observer_file_events = [
                e for e in observer_events
                if e.get("event", "").startswith("observer.file_")
            ]

            jobs = self.query_one("#jobs-panel", JobsPanel)
            jobs.update_data(snapshot, observer_file_events=observer_file_events)

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
        """Cycle sort order: ID -> CPU -> MEM."""
        jobs = self.query_one("#jobs-panel", JobsPanel)
        current = jobs.sort_key
        if current == "job_id":
            new_key = "cpu"
        elif current == "cpu":
            new_key = "mem"
        else:
            new_key = "job_id"

        jobs.sort_key = new_key
        self.notify(f"Sorting by: {new_key.upper()}")

    def action_filter_job(self) -> None:
        """Toggle filter input."""
        # Simple implementation for now - clear filter if present, else notify
        jobs = self.query_one("#jobs-panel", JobsPanel)
        if jobs.filter_query:
            jobs.filter_query = ""
            self.notify("Filter cleared")
        else:
            self.notify("Job filtering enabled (via command line for now)")

    def action_cancel_job(self) -> None:
        """Cancel the selected job."""
        jobs = self.query_one("#jobs-panel", JobsPanel)
        selected = jobs.selected_item
        if selected and selected.get("type") == "job":
            job_id = selected["job_id"]
            self.notify(f"Cancelling job: {job_id}...")
            # Use background task to avoid blocking TUI
            if self._reader._ipc_client is not None:
                asyncio.create_task(self._reader._ipc_client.cancel_job(job_id, ""))
            else:
                self.notify("Not connected to conductor", severity="error")
        else:
            self.notify("Select a job root node to cancel", severity="warning")

    def _update_detail(self) -> None:
        """Update the detail panel with the currently selected item."""
        jobs = self.query_one("#jobs-panel", JobsPanel)
        detail = self.query_one("#detail-panel", DetailPanel)
        detail.show_item(jobs.selected_item)


__all__ = ["MonitorApp"]
