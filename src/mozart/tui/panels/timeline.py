"""Event timeline panel for the Mozart Monitor TUI.

Displays a chronological, color-coded list of process events,
anomalies, and learning insights in a scrollable log.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from textual.widgets import RichLog

from mozart.daemon.profiler.models import Anomaly, EventType, ProcessEvent


def _format_timestamp(ts: float) -> str:
    """Format a unix timestamp as HH:MM:SS."""
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


# Color scheme per event type
_EVENT_COLORS: dict[str, str] = {
    EventType.SPAWN.value: "blue",
    EventType.EXIT.value: "green",
    EventType.SIGNAL.value: "yellow",
    EventType.KILL.value: "red",
    EventType.OOM.value: "bold red",
    "anomaly": "bold yellow",
    "learning": "magenta",
    "observer_process": "magenta",
    "observer_file_created": "green",
    "observer_file_modified": "yellow",
    "observer_file_deleted": "red",
}


class TimelinePanel(RichLog):
    """Renders a scrollable event timeline with color coding.

    Uses ``RichLog`` for built-in scrolling and append-oriented display.

    Events are color-coded by type:
    - blue: SPAWN
    - green: EXIT
    - yellow: SIGNAL
    - red: KILL/OOM/ANOMALY
    - magenta: LEARNING insights
    """

    DEFAULT_CSS = """
    TimelinePanel {
        height: auto;
        min-height: 5;
        max-height: 12;
        padding: 0 1;
    }
    """

    MAX_VISIBLE = 100

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(
            name=name, id=id, classes=classes, wrap=True, max_lines=self.MAX_VISIBLE, markup=True
        )
        self._events: list[ProcessEvent] = []
        self._anomalies: list[Anomaly] = []
        self._learning_insights: list[dict[str, Any]] = []
        self._observer_events: list[dict[str, Any]] = []

    def update_data(
        self,
        events: list[ProcessEvent] | None = None,
        anomalies: list[Anomaly] | None = None,
        learning_insights: list[dict[str, Any]] | None = None,
        observer_events: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update the timeline with new data and refresh display."""
        if events is not None:
            self._events = events
        if anomalies is not None:
            self._anomalies = anomalies
        if learning_insights is not None:
            self._learning_insights = learning_insights
        if observer_events is not None:
            self._observer_events = observer_events
        self._render_timeline()

    def _render_timeline(self) -> None:
        """Render the timeline with Rich markup."""
        # Merge all entries into a unified timeline
        entries: list[tuple[float, str]] = []

        # Process events
        for evt in self._events:
            color = _EVENT_COLORS.get(evt.event_type.value, "white")
            ts_str = _format_timestamp(evt.timestamp)
            type_label = evt.event_type.value.upper().ljust(6)

            job_label = ""
            if evt.job_id:
                sheet_str = f"/S{evt.sheet_num}" if evt.sheet_num is not None else ""
                job_label = f"{evt.job_id}{sheet_str}"

            detail = f"PID {evt.pid}"
            if evt.event_type == EventType.EXIT and evt.exit_code is not None:
                detail += f"  exit={evt.exit_code}"
            elif evt.event_type == EventType.SIGNAL and evt.signal_num is not None:
                detail += f"  sig={evt.signal_num}"
            if evt.details:
                truncated = evt.details[:40]
                if len(evt.details) > 40:
                    truncated += "..."
                detail += f"  {truncated}"

            line = f"{ts_str}  [{color}]\u25cf {type_label}[/] {job_label:<20s} {detail}"
            entries.append((evt.timestamp, line))

        # Anomalies
        for anom in self._anomalies:
            ts_str = _format_timestamp(anom.timestamp)
            sev = anom.severity.value.upper()
            desc = anom.description[:50] if anom.description else anom.anomaly_type.value
            if anom.description and len(anom.description) > 50:
                desc += "..."
            job_label = ""
            if anom.job_id:
                sheet_str = f"/S{anom.sheet_num}" if anom.sheet_num is not None else ""
                job_label = f"{anom.job_id}{sheet_str}"

            line = f"{ts_str}  [bold yellow]\u26a0 ANOMALY[/] {job_label:<20s} [{sev}] {desc}"
            entries.append((anom.timestamp, line))

        # Observer process events (from JobObserver via ObserverRecorder)
        for obs in self._observer_events:
            evt_name = obs.get("event", "")
            if not evt_name.startswith("observer.process_"):
                continue
            ts = obs.get("timestamp", time.time())
            ts_str = _format_timestamp(ts)
            data = obs.get("data") or {}
            pid = data.get("pid", "?")
            label = data.get("name", data.get("role", ""))
            action = "SPAWN" if "spawned" in evt_name else "EXIT"
            color = _EVENT_COLORS["observer_process"]
            job_label = obs.get("job_id", "")
            line = f"{ts_str}  [{color}]\u2699 {action:<6s}[/] {job_label:<20s} PID {pid} {label}"
            entries.append((ts, line))

        # Observer file events (from JobObserver via ObserverRecorder)
        for obs in self._observer_events:
            evt_name = obs.get("event", "")
            if not evt_name.startswith("observer.file_"):
                continue
            ts = obs.get("timestamp", time.time())
            ts_str = _format_timestamp(ts)
            data = obs.get("data") or {}
            path = data.get("path", "?")
            # Truncate long paths
            if len(path) > 40:
                path = "..." + path[-37:]
            job_label = obs.get("job_id", "")
            if "created" in evt_name:
                action = "CREATE"
                color = _EVENT_COLORS["observer_file_created"]
                icon = "\U0001f4c4"  # page facing up
            elif "deleted" in evt_name:
                action = "DELETE"
                color = _EVENT_COLORS["observer_file_deleted"]
                icon = "\U0001f5d1"  # wastebasket
            else:
                action = "MODIFY"
                color = _EVENT_COLORS["observer_file_modified"]
                icon = "\u270f"  # pencil
            line = f"{ts_str}  [{color}]{icon} {action:<6s}[/] {job_label:<20s} {path}"
            entries.append((ts, line))

        # Learning insights
        for insight in self._learning_insights:
            ts = insight.get("timestamp", time.time())
            ts_str = _format_timestamp(ts)
            text = str(insight.get("text", insight.get("pattern", "")))[:50]
            if len(str(insight.get("text", insight.get("pattern", "")))) > 50:
                text += "..."
            line = f"{ts_str}  [magenta]\U0001f9e0 LEARN [/] {text}"
            entries.append((ts, line))

        # Sort by timestamp descending (newest first) and limit
        entries.sort(key=lambda x: x[0], reverse=True)
        entries = entries[: self.MAX_VISIBLE]

        # Clear and rewrite
        self.clear()
        if not entries:
            self.write("[dim]No events yet[/]")
            return

        for _, line in entries:
            self.write(line)


__all__ = ["TimelinePanel"]
