"""Job-centric process tree panel for the Marianne Monitor TUI.

Renders a tree of jobs with their sheets and running processes,
including inline metrics (CPU, MEM, age) and syscall summaries.

Jobs are collapsible — collapsed by default when many jobs exist,
expandable on click or Enter to reveal the process tree.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static, Tree

from marianne.daemon.profiler.models import JobProgress, ProcessMetric, SystemSnapshot


def _format_bytes_mb(mb: float) -> str:
    """Format MB as a human-readable string (e.g., 512M, 1.2G)."""
    if mb >= 1024:
        return f"{mb / 1024:.1f}G"
    return f"{mb:.0f}M"


def _format_age(seconds: float) -> str:
    """Format age in seconds as e.g., '4m30s' or '1h05m'."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m{secs:02d}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h{mins:02d}m"


def _format_progress_bar(done: int, total: int, width: int = 15) -> str:
    """Render a progress bar like ██████████░░░░░ 50%."""
    if total == 0:
        return "\u2591" * width + " 0%"
    pct = done / total
    filled = int(round(pct * width))
    filled = max(0, min(width, filled))
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    return f"{bar} {pct * 100:.0f}%"


def _top_syscalls(proc: ProcessMetric, top_n: int = 3) -> str:
    """Format top N syscalls by time percentage."""
    if not proc.syscall_time_pct:
        return ""
    sorted_sc = sorted(
        proc.syscall_time_pct.items(), key=lambda x: x[1], reverse=True
    )[:top_n]
    parts = [f"{name} {pct:.0f}%" for name, pct in sorted_sc]
    return " | ".join(parts)


def _state_label(state: str) -> str:
    """Return a display label for a process state."""
    if state == "Z":
        return "[ZOMBIE]"
    elif state in ("R", "S", "D"):
        return "[RUNNING]"
    elif state == "T":
        return "[STOPPED]"
    return f"[{state}]"


# Threshold: auto-collapse jobs when this many are present
_AUTO_COLLAPSE_THRESHOLD = 3


class JobsPanel(VerticalScroll):
    """Renders the job tree with per-process metrics in a scrollable container.

    Jobs are displayed as collapsible tree nodes. When there are many jobs,
    they start collapsed; with few jobs, they start expanded.
    """

    DEFAULT_CSS = """
    JobsPanel {
        height: 1fr;
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
        self._selected_index: int = 0
        self._items: list[dict[str, Any]] = []
        self._tree: Tree[dict[str, Any]] | None = None
        self._empty_label: Static | None = None
        self._observer_file_events: list[dict[str, Any]] = []
        self._sort_key: str = "job_id"
        self._filter_query: str = ""

    def compose(self) -> Any:
        """Build the widget tree with a Tree for collapsible jobs."""
        self._empty_label = Static("[dim]No active jobs[/]", id="jobs-empty")
        yield self._empty_label
        tree: Tree[dict[str, Any]] = Tree("Jobs", id="jobs-tree")
        tree.show_root = False
        tree.guide_depth = 3
        self._tree = tree
        yield tree

    @property
    def sort_key(self) -> str:
        return self._sort_key

    @sort_key.setter
    def sort_key(self, value: str) -> None:
        self._sort_key = value
        self._render_jobs()

    @property
    def filter_query(self) -> str:
        return self._filter_query

    @filter_query.setter
    def filter_query(self, value: str) -> None:
        self._filter_query = value.lower()
        self._render_jobs()

    @property
    def selected_item(self) -> dict[str, Any] | None:
        """Return the currently selected item, if any."""
        if self._tree is not None:
            node = self._tree.cursor_node
            if node is not None and node.data is not None:
                return node.data
        # Fallback to index-based selection
        if 0 <= self._selected_index < len(self._items):
            return self._items[self._selected_index]
        return None

    def select_next(self) -> None:
        """Move selection down in the tree."""
        if self._tree is not None:
            self._tree.action_cursor_down()
        if self._items:
            self._selected_index = min(
                self._selected_index + 1, len(self._items) - 1
            )

    def select_prev(self) -> None:
        """Move selection up in the tree."""
        if self._tree is not None:
            self._tree.action_cursor_up()
        if self._items:
            self._selected_index = max(self._selected_index - 1, 0)

    def update_data(
        self,
        snapshot: SystemSnapshot | None,
        observer_file_events: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update the panel with new snapshot data."""
        self._snapshot = snapshot
        if observer_file_events is not None:
            self._observer_file_events = observer_file_events
        self._render_jobs()

    def _render_jobs(self) -> None:
        """Render the job tree using Textual's Tree widget."""
        snap = self._snapshot
        tree = self._tree
        empty = self._empty_label

        if tree is None:
            return

        if snap is None or not snap.processes:
            tree.display = False
            if empty is not None:
                empty.display = True
                empty.update("[dim]No active jobs[/]")
            self._items = []
            return

        # Apply filtering
        filtered_processes = snap.processes
        if self._filter_query:
            filtered_processes = [
                p for p in snap.processes
                if (p.job_id and self._filter_query in p.job_id.lower()) or
                   (p.command and self._filter_query in p.command.lower())
            ]

        if not filtered_processes and self._filter_query:
            tree.display = False
            if empty is not None:
                empty.display = True
                empty.update(f"[yellow]No jobs matching '{self._filter_query}'[/]")
            return

        if empty is not None:
            empty.display = False
        tree.display = True

        # Group processes by job_id
        by_job: dict[str, list[ProcessMetric]] = defaultdict(list)
        orphans: list[ProcessMetric] = []

        for proc in filtered_processes:
            if proc.job_id:
                by_job[proc.job_id].append(proc)
            else:
                orphans.append(proc)

        items: list[dict[str, Any]] = []
        auto_collapse = len(by_job) >= _AUTO_COLLAPSE_THRESHOLD

        tree.clear()

        # Index job progress by job_id for O(1) lookup
        progress_by_job: dict[str, JobProgress] = {
            jp.job_id: jp for jp in snap.job_progress
        }

        # Sorting logic
        sorted_job_ids = sorted(by_job.keys())
        if self._sort_key == "cpu":
            sorted_job_ids = sorted(
                by_job.keys(),
                key=lambda jid: sum(p.cpu_percent for p in by_job[jid]),
                reverse=True
            )
        elif self._sort_key == "mem":
            sorted_job_ids = sorted(
                by_job.keys(),
                key=lambda jid: sum(p.rss_mb for p in by_job[jid]),
                reverse=True
            )

        for job_id in sorted_job_ids:
            procs = by_job[job_id]
            jp = progress_by_job.get(job_id)
            if jp and jp.total_sheets > 0:
                progress = _format_progress_bar(jp.last_completed_sheet, jp.total_sheets)
                sheet_label = f"Sheet {jp.current_sheet or '-'}/{jp.total_sheets}"
            else:
                # Fallback: no live state available, show process count
                sheet_nums = {p.sheet_num for p in procs if p.sheet_num is not None}
                total_sheets = max(len(sheet_nums), 1)
                running = [p for p in procs if p.state in ("R", "S", "D")]
                progress = _format_progress_bar(len(running), total_sheets)
                sheet_label = f"Sheet {len(running)}/{total_sheets}"

            job_label = Text.from_markup(
                f"[bold]\u25b6 {job_id}[/bold]  "
                f"{sheet_label} {progress}"
            )
            # Attach observer file events filtered for this job
            job_file_events = [
                e for e in self._observer_file_events
                if e.get("job_id") == job_id
                and e.get("event", "").startswith("observer.file_")
            ]
            job_data: dict[str, Any] = {
                "type": "job",
                "job_id": job_id,
                "processes": procs,
                "observer_file_events": job_file_events,
            }
            job_node = tree.root.add(job_label, data=job_data, expand=not auto_collapse)
            items.append(job_data)

            # Process children under this job
            sorted_procs = sorted(procs, key=lambda p: (p.sheet_num or 0))
            for proc in sorted_procs:
                state_str = _state_label(proc.state)
                sheet_label = f"S{proc.sheet_num}" if proc.sheet_num is not None else "S?"

                proc_text = (
                    f"{sheet_label} {state_str}  "
                    f"PID {proc.pid}  CPU {proc.cpu_percent:.0f}%  "
                    f"MEM {_format_bytes_mb(proc.rss_mb)}  "
                    f"{_format_age(proc.age_seconds)}"
                )
                sc_str = _top_syscalls(proc)
                if sc_str:
                    proc_text += f"\n    syscalls: {sc_str}"

                proc_data: dict[str, Any] = {
                    "type": "process",
                    "process": proc,
                    "job_id": job_id,
                }
                job_node.add_leaf(Text.from_markup(proc_text), data=proc_data)
                items.append(proc_data)

        # Orphan processes (no job_id)
        if orphans:
            orphan_node = tree.root.add(
                Text.from_markup("[dim]Unassociated processes[/]"),
                data={"type": "orphan_header"},
            )
            for proc in orphans:
                proc_data = {"type": "process", "process": proc, "job_id": None}
                orphan_node.add_leaf(
                    Text.from_markup(
                        f"PID {proc.pid}  {proc.command[:40]}  "
                        f"CPU {proc.cpu_percent:.0f}%  MEM {_format_bytes_mb(proc.rss_mb)}"
                    ),
                    data=proc_data,
                )
                items.append(proc_data)

        self._items = items


__all__ = ["JobsPanel"]
