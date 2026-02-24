"""Monitor commands — ``mozart top`` real-time system monitor.

Provides four operating modes:

1. **TUI (default)** — Rich Textual-based terminal UI showing job-centric
   process tree, event timeline, and system metrics.

2. **JSON (``--json``)** — Streams NDJSON snapshots to stdout for piping
   to other tools or AI consumption.

3. **History (``--history 1h``)** — Replays historical snapshots from
   the profiler SQLite database.

4. **Trace (``--trace PID``)** — Attaches full strace to a specific process
   and streams trace output to the terminal.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console

from ..output import console

if TYPE_CHECKING:
    from mozart.daemon.profiler.storage import MonitorStorage

# Separate console for stderr status messages (keeps stdout clean for NDJSON)
_stderr_console = Console(stderr=True)


def _parse_duration(duration_str: str) -> float:
    """Parse a human-readable duration string into seconds.

    Supported formats: '30s', '5m', '1h', '2h30m', '1h30m15s'.
    Bare numbers are treated as minutes.

    Raises:
        typer.BadParameter: If the duration string is unparseable.
    """
    pattern = re.compile(
        r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", re.IGNORECASE
    )
    match = pattern.match(duration_str.strip())
    if match and any(match.groups()):
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    # Try bare number → minutes
    try:
        return float(duration_str) * 60
    except ValueError:
        raise typer.BadParameter(
            f"Invalid duration: '{duration_str}'. "
            "Use formats like '30s', '5m', '1h', '2h30m'."
        ) from None


def top(
    json_output: bool = typer.Option(
        False, "--json", help="Stream JSON snapshots (NDJSON)"
    ),
    history: str | None = typer.Option(
        None, "--history", help="Replay historical data (e.g., '1h', '30m')"
    ),
    trace_pid: int | None = typer.Option(
        None, "--trace", help="Attach full strace to PID and stream output"
    ),
    filter_job: str | None = typer.Option(
        None, "--job", "-j", help="Filter by job ID"
    ),
    interval: float = typer.Option(
        2.0, "--interval", "-i", help="Refresh interval in seconds"
    ),
) -> None:
    """Real-time system monitor for Mozart — like htop for your conductor.

    Shows job-centric process tree, resource metrics, event timeline,
    anomaly detection, and learning insights.

    Examples:
        mozart top                    # Launch TUI monitor
        mozart top --json             # Stream NDJSON snapshots
        mozart top --history 1h       # Replay last hour
        mozart top --job my-review    # Filter by job
        mozart top --interval 5       # 5-second refresh
        mozart top --trace 12345      # Attach full strace to PID
    """
    if trace_pid is not None:
        asyncio.run(_trace_mode(trace_pid))
    elif history is not None:
        duration_seconds = _parse_duration(history)
        if json_output:
            asyncio.run(_history_json(duration_seconds, filter_job=filter_job))
        else:
            asyncio.run(_history_tui(duration_seconds, filter_job=filter_job))
    elif json_output:
        asyncio.run(_json_mode(filter_job=filter_job, interval=interval))
    else:
        _tui_mode(filter_job=filter_job, interval=interval)


# =============================================================================
# Mode 1: TUI (default)
# =============================================================================


def _tui_mode(*, filter_job: str | None, interval: float) -> None:
    """Launch the Textual TUI monitor application."""
    try:
        from mozart.tui.app import MonitorApp
    except ImportError:
        console.print(
            "[red]TUI requires the 'textual' package.[/red]\n"
            "[dim]Install it with: pip install textual[/dim]\n\n"
            "[dim]Alternatively, use --json for NDJSON streaming output.[/dim]"
        )
        raise typer.Exit(1) from None

    from mozart.daemon.detect import _resolve_socket_path
    from mozart.daemon.ipc.client import DaemonClient
    from mozart.tui.reader import MonitorReader

    ipc_client = DaemonClient(_resolve_socket_path(None))
    reader = MonitorReader(ipc_client=ipc_client)
    if filter_job:
        _stderr_console.print(
            f"[dim]Note: --job filter ({filter_job}) is not yet supported "
            f"in TUI mode. Use --json for filtered output.[/dim]"
        )
    app = MonitorApp(reader=reader, refresh_interval=interval)
    app.run()


# =============================================================================
# Mode 2: JSON streaming (--json)
# =============================================================================


async def _json_mode(*, filter_job: str | None, interval: float) -> None:
    """Stream NDJSON snapshots to stdout."""
    from mozart.daemon.detect import try_daemon_route

    # Check if daemon is reachable
    routed, _ = await try_daemon_route("daemon.status", {})

    if not routed:
        # Fallback: try reading JSONL directly
        await _json_from_jsonl(filter_job=filter_job, interval=interval)
        return

    # Stream snapshots from the daemon via periodic polling
    _stderr_console.print(
        "[dim]Streaming NDJSON snapshots (Ctrl+C to stop)...[/dim]"
    )

    try:
        while True:
            routed, snapshot = await try_daemon_route("daemon.top", {})
            if routed and snapshot:
                snapshot_data = _filter_snapshot(snapshot, filter_job)
                if snapshot_data:
                    # Write to stdout (not through Rich console to avoid formatting)
                    sys.stdout.write(json.dumps(snapshot_data, separators=(",", ":")) + "\n")
                    sys.stdout.flush()
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        pass


async def _json_from_jsonl(*, filter_job: str | None, interval: float) -> None:
    """Fallback: tail the JSONL file directly when daemon is unavailable."""
    from mozart.daemon.profiler.models import ProfilerConfig

    jsonl_path = ProfilerConfig().jsonl_path.expanduser()

    if not jsonl_path.exists():
        console.print(
            "[yellow]No monitor data available.[/yellow]\n"
            "[dim]Ensure the conductor is running with profiling enabled: "
            "mozart start[/dim]"
        )
        raise typer.Exit(1)

    _stderr_console.print(
        f"[dim]Tailing {jsonl_path} (Ctrl+C to stop)...[/dim]"
    )

    try:
        with open(jsonl_path, encoding="utf-8") as f:
            # Seek to end
            f.seek(0, 2)

            while True:
                line = f.readline()
                if line:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            filtered = _filter_snapshot(data, filter_job)
                            if filtered:
                                sys.stdout.write(
                                    json.dumps(filtered, separators=(",", ":")) + "\n"
                                )
                                sys.stdout.flush()
                        except json.JSONDecodeError:
                            pass
                else:
                    await asyncio.sleep(interval)
    except KeyboardInterrupt:
        pass


def _filter_snapshot(
    snapshot: dict[str, Any], filter_job: str | None
) -> dict[str, Any] | None:
    """Filter snapshot data by job ID if specified.

    Returns the full snapshot if no filter, or a snapshot with only
    matching processes if a filter is active. Returns None if the
    filter matches nothing.
    """
    if filter_job is None:
        return snapshot

    processes = snapshot.get("processes", [])
    filtered_procs = [
        p for p in processes if p.get("job_id") == filter_job
    ]

    if not filtered_procs and not processes:
        # No process data at all — still return system metrics
        return snapshot

    result = dict(snapshot)
    result["processes"] = filtered_procs
    return result


# =============================================================================
# Mode 3: History replay (--history)
# =============================================================================


async def _history_json(
    duration_seconds: float, *, filter_job: str | None
) -> None:
    """Dump historical snapshots as JSON."""
    storage = _get_storage()

    since = time.time() - duration_seconds
    snapshots = await storage.read_snapshots(since=since, limit=10000)
    events = await storage.read_events(since=since, limit=10000)

    output: dict[str, Any] = {
        "duration_seconds": duration_seconds,
        "snapshot_count": len(snapshots),
        "event_count": len(events),
        "snapshots": [s.model_dump(mode="json") for s in snapshots],
        "events": [e.model_dump(mode="json") for e in events],
    }

    if filter_job:
        output["filter_job"] = filter_job
        # Filter process data by job
        for snap_data in output["snapshots"]:
            snap_data["processes"] = [
                p for p in snap_data.get("processes", [])
                if p.get("job_id") == filter_job
            ]

    sys.stdout.write(json.dumps(output, indent=2) + "\n")


async def _history_tui(
    duration_seconds: float, *, filter_job: str | None
) -> None:
    """Replay historical snapshots in TUI mode.

    Parameters are accepted from the CLI for consistency with ``_history_json``
    but currently only ``filter_job`` is used for display purposes — the TUI
    reader will show data from the full storage window. ``duration_seconds``
    would require a time-bounded reader, which isn't yet supported.
    """
    try:
        from mozart.tui.app import MonitorApp
    except ImportError:
        console.print(
            "[red]TUI requires the 'textual' package.[/red]\n"
            "[dim]Install it with: pip install textual[/dim]\n\n"
            "[dim]Use --json for JSON history output instead.[/dim]"
        )
        raise typer.Exit(1) from None

    from mozart.tui.reader import MonitorReader

    storage = _get_storage()
    reader = MonitorReader(storage=storage)
    app = MonitorApp(reader=reader, refresh_interval=duration_seconds)
    if filter_job:
        _stderr_console.print(
            f"[dim]Note: --job filter ({filter_job}) is not yet supported "
            f"in history TUI mode. Use --json for filtered output.[/dim]"
        )
    app.run()


def _get_storage() -> MonitorStorage:
    """Create a ``MonitorStorage`` instance from default config.

    Raises ``typer.Exit(1)`` if the database doesn't exist.
    """
    from mozart.daemon.profiler.models import ProfilerConfig
    from mozart.daemon.profiler.storage import MonitorStorage

    config = ProfilerConfig()
    db_path = config.storage_path.expanduser()

    if not db_path.exists():
        console.print(
            "[yellow]No monitor database found.[/yellow]\n"
            f"[dim]Expected at: {db_path}[/dim]\n"
            "[dim]Ensure the conductor has been running with profiling enabled.[/dim]"
        )
        raise typer.Exit(1)

    return MonitorStorage(db_path=db_path)


# =============================================================================
# Mode 4: Trace (--trace PID)
# =============================================================================


async def _trace_mode(pid: int) -> None:
    """Attach full strace to a process and stream output to stdout.

    Uses StraceManager to attach ``strace -f -t -p PID``, writing output
    to a temporary file and tailing it to stdout in real-time.
    """
    from mozart.daemon.profiler.strace_manager import StraceManager

    if not StraceManager.is_available():
        console.print(
            "[red]strace is not available on this system.[/red]\n"
            "[dim]Install strace to use the --trace option.[/dim]"
        )
        raise typer.Exit(1)

    mgr = StraceManager(enabled=True)
    trace_dir = Path(tempfile.mkdtemp(prefix="mozart-trace-"))
    trace_file = trace_dir / f"trace-{pid}.log"

    console.print(f"[dim]Attaching strace to PID {pid}...[/dim]")
    console.print(f"[dim]Trace output: {trace_file}[/dim]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

    success = await mgr.attach_full_trace(pid, trace_file)
    if not success:
        console.print(
            f"[red]Failed to attach strace to PID {pid}.[/red]\n"
            "[dim]Possible causes: process not found, permission denied, "
            "or strace not available.[/dim]"
        )
        raise typer.Exit(1)

    # Tail the trace output file
    try:
        # Wait briefly for strace to start writing
        await asyncio.sleep(0.5)

        with open(trace_file, encoding="utf-8", errors="replace") as f:
            while True:
                line = f.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopping trace...[/dim]")
    finally:
        await mgr.detach_all()
        console.print(f"[dim]Trace saved to: {trace_file}[/dim]")


# =============================================================================
# Public API
# =============================================================================

__all__ = ["top"]
