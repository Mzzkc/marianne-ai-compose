"""Detail drill-down panel for the Marianne Monitor TUI.

Shows expanded information for the currently selected item:
processes, completed sheets, or anomalies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from textual.containers import VerticalScroll
from textual.widgets import Static

from marianne.daemon.profiler.models import Anomaly, ProcessMetric


def _format_bytes_mb(mb: float) -> str:
    """Format MB as a human-readable string."""
    if mb >= 1024:
        return f"{mb / 1024:.1f}G"
    return f"{mb:.0f}M"


class DetailPanel(VerticalScroll):
    """Renders details for the selected item in a scrollable container.

    Content varies based on selected item type:
    - **Process**: strace summary, open FDs, full command, environment
    - **Completed sheet**: validation results, stdout tail, retry history
    - **Anomaly**: description, affected resources, historical context
    """

    DEFAULT_CSS = """
    DetailPanel {
        height: auto;
        min-height: 3;
        max-height: 10;
        padding: 0 1;
        border-top: solid $primary;
    }
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._content: Static | None = None

    def compose(self) -> Any:
        """Build the scrollable content area."""
        self._content = Static("", id="detail-content")
        yield self._content

    def _set_content(self, markup: str) -> None:
        """Update the inner Static widget's content."""
        if self._content is not None:
            self._content.update(markup)

    def show_empty(self) -> None:
        """Show the default empty state."""
        self._set_content(
            "[dim]Select a process/event to see: full strace, logs, "
            "validation details, resource history, or learning correlations[/]"
        )

    def show_process(self, proc: ProcessMetric) -> None:
        """Show detailed process information."""
        lines: list[str] = []

        # Header
        lines.append(f"[bold]Process Detail: PID {proc.pid}[/]")
        lines.append("")

        # Command
        cmd_display = proc.command if proc.command else "[dim]unknown[/]"
        lines.append(f"  Command:  {cmd_display}")
        lines.append(f"  State:    {proc.state}")
        lines.append(
            f"  CPU:      {proc.cpu_percent:.1f}%    "
            f"Memory: {_format_bytes_mb(proc.rss_mb)} RSS / {_format_bytes_mb(proc.vms_mb)} VMS"
        )
        lines.append(f"  Threads:  {proc.threads}    Open FDs: {proc.open_fds}")

        if proc.job_id:
            sheet_str = f"  Sheet: S{proc.sheet_num}" if proc.sheet_num is not None else ""
            lines.append(f"  Job:      {proc.job_id}{sheet_str}")

        # Syscall summary
        if proc.syscall_counts:
            lines.append("")
            lines.append("  [bold]Syscall Summary:[/]")
            sorted_sc = sorted(
                proc.syscall_counts.items(), key=lambda x: x[1], reverse=True
            )[:10]
            for sc_name, count in sorted_sc:
                time_pct = proc.syscall_time_pct.get(sc_name, 0.0)
                lines.append(f"    {sc_name:<16s} count={count:>8,}  time={time_pct:.1f}%")
        elif proc.syscall_time_pct:
            lines.append("")
            lines.append("  [bold]Syscall Time:[/]")
            sorted_time = sorted(
                proc.syscall_time_pct.items(), key=lambda x: x[1], reverse=True
            )[:10]
            for sc_name, pct in sorted_time:
                lines.append(f"    {sc_name:<16s} {pct:.1f}%")

        self._set_content("\n".join(lines))

    def show_anomaly(self, anomaly: Anomaly) -> None:
        """Show detailed anomaly information."""
        lines: list[str] = []

        sev_color = {
            "low": "green",
            "medium": "yellow",
            "high": "bold yellow",
            "critical": "bold red",
        }.get(anomaly.severity.value, "white")

        lines.append(f"[bold]Anomaly: {anomaly.anomaly_type.value}[/]")
        lines.append("")
        lines.append(f"  Severity:  [{sev_color}]{anomaly.severity.value.upper()}[/]")
        lines.append(f"  Value:     {anomaly.metric_value:.1f}")
        lines.append(f"  Threshold: {anomaly.threshold:.1f}")

        if anomaly.pid is not None:
            lines.append(f"  PID:       {anomaly.pid}")
        if anomaly.job_id:
            sheet_str = f"  Sheet: S{anomaly.sheet_num}" if anomaly.sheet_num is not None else ""
            lines.append(f"  Job:       {anomaly.job_id}{sheet_str}")

        if anomaly.description:
            lines.append("")
            lines.append(f"  {anomaly.description}")

        self._set_content("\n".join(lines))

    def show_file_activity(self, events: list[dict[str, Any]]) -> None:
        """Show recent file activity from observer events.

        Args:
            events: Observer events filtered to ``observer.file_*`` types.
        """
        if not events:
            self._set_content("[dim]No file activity[/]")
            return

        lines: list[str] = []
        lines.append("[bold]File Activity[/]")
        lines.append("")

        # Show most recent events (already newest-first from recorder)
        for evt in events[:20]:
            evt_name = evt.get("event", "")
            data = evt.get("data") or {}
            path = data.get("path", "unknown")
            ts = evt.get("timestamp", 0)
            ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "??:??:??"

            if "created" in evt_name:
                action = "[green]+[/]"
            elif "deleted" in evt_name:
                action = "[red]-[/]"
            else:
                action = "[yellow]~[/]"

            lines.append(f"  {ts_str}  {action} {path}")

        self._set_content("\n".join(lines))

    def show_item(self, item: dict[str, Any] | None) -> None:
        """Show details for a generic selected item.

        The item dict should have a 'type' key ('process', 'anomaly', 'job').
        """
        if item is None:
            self.show_empty()
            return

        item_type = item.get("type")
        if item_type == "process" and "process" in item:
            self.show_process(item["process"])
        elif item_type == "anomaly" and "anomaly" in item:
            self.show_anomaly(item["anomaly"])
        elif item_type == "job":
            job_id = item.get("job_id", "unknown")
            procs = item.get("processes", [])
            total_cpu = sum(p.cpu_percent for p in procs)
            total_mem = sum(p.rss_mb for p in procs)
            lines = [
                f"[bold]Job: {job_id}[/]",
                "",
                f"  Processes: {len(procs)}",
                f"  Total CPU: {total_cpu:.1f}%",
                f"  Total MEM: {_format_bytes_mb(total_mem)}",
            ]
            # Append file activity if observer events are present
            file_events = item.get("observer_file_events", [])
            if file_events:
                lines.append("")
                lines.append("[bold]File Activity[/]")
                for evt in file_events[:10]:
                    evt_name = evt.get("event", "")
                    data = evt.get("data") or {}
                    path = data.get("path", "unknown")
                    ts = evt.get("timestamp", 0)
                    ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "??:??:??"
                    if "created" in evt_name:
                        action = "[green]+[/]"
                    elif "deleted" in evt_name:
                        action = "[red]-[/]"
                    else:
                        action = "[yellow]~[/]"
                    lines.append(f"  {ts_str}  {action} {path}")
            self._set_content("\n".join(lines))
        else:
            self.show_empty()


__all__ = ["DetailPanel"]
