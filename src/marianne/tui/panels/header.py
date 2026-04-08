"""System summary header bar for the Marianne Monitor TUI.

Displays conductor status, memory bar, CPU bar, GPU indicator,
pressure level, and job/sheet counts in a compact single-line format.
"""

from __future__ import annotations

from textual.widgets import Static

from marianne.daemon.profiler.models import SystemSnapshot


def _bar(pct: float, width: int = 20, color: str = "green") -> str:
    """Render a high-fidelity htop-style bar like [|||||     ] 25.0%."""
    filled = int(round(pct / 100.0 * width))
    filled = max(0, min(width, filled))

    bar_chars = "|" * filled
    empty_chars = " " * (width - filled)

    bar_styled = f"[{color}]{bar_chars}[/]" if bar_chars else ""
    return f"\\[{bar_styled}{empty_chars}] [bold]{pct:4.1f}%[/]"


def _pressure_color(level: str) -> str:
    """Return a Rich color name for the given pressure level."""
    level_lower = level.lower()
    if level_lower in ("critical", "high"):
        return "red"
    elif level_lower == "medium":
        return "yellow"
    return "green"


def _format_uptime(seconds: float) -> str:
    """Format seconds into a human-readable uptime string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h{mins:02d}m"


class HeaderPanel(Static):
    """Renders the system summary header with Rich markup.

    Layout matches the htop style:
    Conductor: UP 2h15m
    CPU [||||      ] 12.0%  Mem [||||||||  ] 45.0%
    Pressure: LOW  Jobs: 2  Sheets: 3 active
    """

    DEFAULT_CSS = """
    HeaderPanel {
        height: 3;
        dock: top;
        background: $surface;
        padding: 0 1;
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
        self._snapshot: SystemSnapshot | None = None
        self._conductor_up: bool = False
        self._uptime_seconds: float = 0.0
        self._anomaly_count: int = 0

    def update_data(
        self,
        snapshot: SystemSnapshot | None,
        conductor_up: bool = False,
        uptime_seconds: float = 0.0,
        anomaly_count: int = 0,
    ) -> None:
        """Update the header with new snapshot data and refresh display."""
        self._snapshot = snapshot
        self._conductor_up = conductor_up
        self._uptime_seconds = uptime_seconds
        self._anomaly_count = anomaly_count
        self._render_header()

    def _render_header(self) -> None:
        """Render the header content with Rich markup."""
        snap = self._snapshot

        # Line 1: Conductor status, uptime
        if self._conductor_up:
            uptime = _format_uptime(self._uptime_seconds)
            status = f"[green]●[/] Conductor: [bold green]UP[/] {uptime}"
        else:
            status = "[red]●[/] Conductor: [bold red]DOWN[/]"

        # Line 2: Resource bars (htop-style)
        if snap is not None:
            mem_total = snap.system_memory_total_mb
            mem_used = snap.system_memory_used_mb
            mem_pct = (mem_used / mem_total * 100.0) if mem_total > 0 else 0.0

            # Use sum of process CPU for bar
            total_cpu = sum(p.cpu_percent for p in snap.processes)
            cpu_pct = min(total_cpu, 100.0)

            mem_bar = _bar(mem_pct, 12, "cyan")
            cpu_bar = _bar(cpu_pct, 12, "green")

            line2 = f"CPU {cpu_bar}  Mem {mem_bar}"

            # Line 3: Pressure, jobs, anomalies
            pressure = snap.pressure_level.upper()
            p_color = _pressure_color(snap.pressure_level)
            pressure_str = f"Pressure: [{p_color}]{pressure}[/]"
            jobs_str = f"Jobs: [bold]{snap.running_jobs}[/]"
            sheets_str = f"Sheets: [bold]{snap.active_sheets}[/] active"

            anomaly_str = ""
            if self._anomaly_count > 0:
                anomaly_str = f"  [bold red]\u26a0 {self._anomaly_count} ANOMALIES[/]"

            line3 = f"{pressure_str}  {jobs_str}  {sheets_str}{anomaly_str}"
        else:
            line2 = "CPU [            ]  \u2014%  Mem [            ]  \u2014%"
            line3 = "Pressure: \u2014  Jobs: \u2014  Sheets: \u2014"

        self.update(f"{status}\n{line2}\n{line3}")


__all__ = ["HeaderPanel"]
