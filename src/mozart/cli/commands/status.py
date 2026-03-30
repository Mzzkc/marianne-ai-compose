"""Status commands for Mozart CLI.

This module implements job status display commands:
- `mozart status <job-id>` - Show detailed status for a specific job
- `mozart list` (list_jobs) - List all jobs in the workspace

★ Insight ─────────────────────────────────────
1. **Dual output modes**: Both JSON and Rich output are supported for all
   status commands. JSON mode enables scripting and CI/CD integration while
   Rich mode provides a human-friendly interactive experience.

2. **Watch mode implementation**: The status --watch flag uses a polling loop
   with screen clearing. This is simpler than event-driven updates but works
   universally across state backends (JSON files or SQLite).

3. **Circuit breaker inference**: The actual CircuitBreaker is a runtime object
   not persisted to state. We infer its likely state from failure patterns in
   the persisted sheet states to give operators visibility into retry behavior.
─────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
)

from mozart.core.checkpoint import (
    CheckpointState,
    ErrorRecord,
    JobStatus,
    SheetState,
    SheetStatus,
    ValidationDetailDict,
)
from mozart.core.logging import get_logger

from ..helpers import (
    ErrorMessages,
    get_last_activity_time,
    require_conductor,
)
from ..helpers import (
    _find_job_state_direct as require_job_state,
)
from ..helpers import (
    _find_job_state_fs as find_job_state,
)
from ..output import (
    StatusColors,
    console,
    create_sheet_details_table,
    create_synthesis_table,
    format_duration,
    format_sheet_display_status,
    format_timestamp,
    format_validation_status,
    output_error,
    output_json,
)
from ..output import (
    infer_error_type as _infer_error_type,
)

_logger = get_logger("cli.status")

# Default circuit breaker failure threshold from CircuitBreakerConfig
_DEFAULT_CB_THRESHOLD: int = 5  # Must match CircuitBreakerConfig.failure_threshold default


class CircuitBreakerInference(TypedDict):
    """Inferred circuit breaker state from job failure patterns."""

    state: Literal["open", "closed"]
    consecutive_failures: int
    reason: str

# =============================================================================
# CLI Commands
# =============================================================================


def status(
    job_id: str | None = typer.Argument(
        None,
        help="Score ID to check status for. Omit to see all active scores.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output status as JSON for machine parsing",
    ),
    watch: bool = typer.Option(
        False,
        "--watch",
        "-W",
        help="Continuously monitor status with live updates",
    ),
    watch_interval: int = typer.Option(
        5,
        "--interval",
        "-i",
        help="Refresh interval in seconds for --watch mode (default: 5)",
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory to search for score state (debug override)",
        hidden=True,
    ),
) -> None:
    """Show score status. Run with no arguments for an overview of all scores.

    With no arguments: shows conductor status and all active scores.
    With a score ID: shows detailed status for that specific score.

    Examples:
        mozart status
        mozart status my-job
        mozart status my-job --json
        mozart status my-job --watch
    """
    if job_id is None:
        asyncio.run(_status_overview(json_output))
        return

    from ._shared import validate_job_id

    job_id = validate_job_id(job_id)
    if watch:
        asyncio.run(_status_job_watch(job_id, json_output, watch_interval, workspace))
    else:
        asyncio.run(_status_job(job_id, json_output, workspace))


def list_jobs(
    all_jobs: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Show all scores including completed, failed, and cancelled",
    ),
    status_filter: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by score status (queued, running, completed, failed, paused)",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of scores to display",
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Reserved for future per-workspace filtering.",
        hidden=True,
    ),
) -> None:
    """List scores from the conductor.

    By default shows only active scores (queued, running, paused).
    Use --all to include completed, failed, and cancelled scores.
    """
    asyncio.run(_list_jobs(all_jobs, status_filter, limit, workspace))


# =============================================================================
# Async Implementation Functions
# =============================================================================


async def _status_job(
    job_id: str,
    json_output: bool,
    workspace: Path | None,
) -> None:
    """Asynchronously get and display status for a specific job.

    Routes through the conductor by default. Falls back to direct filesystem
    access only if --workspace is explicitly provided (debug override).
    """
    from mozart.daemon.detect import try_daemon_route
    from mozart.daemon.exceptions import JobSubmissionError

    # Try conductor first (unless workspace override is given)
    ws_str = str(workspace) if workspace else None

    params = {"job_id": job_id, "workspace": ws_str}
    try:
        routed, result = await try_daemon_route("job.status", params)
    except JobSubmissionError:
        # Conductor confirmed: job not found.
        output_error(
            f"Score not found: {job_id}",
            hints=["Run 'mozart list' to see available scores."],
            json_output=json_output,
        )
        raise typer.Exit(1) from None
    except Exception as exc:
        # Conductor error (crash, resource exhaustion, etc.) — report truthfully.
        error_detail = f"{type(exc).__name__}: {exc}"
        _logger.error("status_conductor_error", error=error_detail, exc_info=True)
        output_error(
            f"Conductor error: {error_detail}",
            hints=["Check conductor logs: tail -f ~/.mozart/mozart.log"],
            json_output=json_output,
        )
        raise typer.Exit(1) from None

    if routed and result:
        # Conductor returns CheckpointState when state file exists,
        # or JobMeta dict for queued jobs before first sheet runs.
        try:
            found_job = CheckpointState.model_validate(result)
        except Exception:
            _logger.debug("checkpoint_model_validate_fallback", exc_info=True)
            _output_meta_status(result, json_output)
            return
    elif routed and not result:
        # Conductor returned None — shouldn't happen with current protocol,
        # but handle gracefully.
        output_error(
            f"Score not found: {job_id}",
            hints=["Run 'mozart list' to see available scores."],
            json_output=json_output,
        )
        raise typer.Exit(1)
    else:
        # Conductor not available — require it unless workspace is given
        if workspace is None:
            require_conductor(routed, json_output=json_output)
            return  # unreachable, require_conductor raises
        # Fallback to direct filesystem access with workspace override
        found_job, _ = await require_job_state(
            job_id, workspace, json_output=json_output,
        )

    # Output as JSON if requested
    if json_output:
        _output_status_json(found_job)
        return

    # Display rich status output
    _output_status_rich(found_job)


async def _status_job_watch(
    job_id: str,
    json_output: bool,
    interval: int,
    workspace: Path | None,
) -> None:
    """Continuously monitor job status with live updates.

    Routes through the conductor for each poll. Falls back to direct
    filesystem access only if --workspace is explicitly provided.

    Args:
        job_id: Job ID to monitor.
        json_output: Output as JSON instead of rich formatting.
        interval: Refresh interval in seconds.
        workspace: Optional workspace directory to search.
    """
    from mozart.daemon.detect import try_daemon_route
    from mozart.daemon.exceptions import JobSubmissionError

    console.print(f"[dim]Watching score [bold]{job_id}[/bold] (Ctrl+C to stop)[/dim]\n")

    try:
        while True:
            found_job: CheckpointState | None = None

            # Try conductor first
            ws_str = str(workspace) if workspace else None
            params = {"job_id": job_id, "workspace": ws_str}
            try:
                routed, result = await try_daemon_route("job.status", params)
            except JobSubmissionError:
                # Conductor confirmed: job not found
                routed, result = True, None
            except Exception as exc:
                # Conductor error — show the real error, not "not found"
                error_detail = f"{type(exc).__name__}: {exc}"
                _logger.warning("watch_daemon_error", error=error_detail)
                console.clear()
                output_error(f"Conductor error: {error_detail}")
                await asyncio.sleep(interval)
                continue

            if routed and result:
                try:
                    found_job = CheckpointState.model_validate(result)
                except Exception:
                    _logger.debug("watch_checkpoint_model_validate_fallback", exc_info=True)
                    console.clear()
                    _output_meta_status(result, json_output)
                    await asyncio.sleep(interval)
                    continue
            elif not routed and workspace is not None:
                # Fallback to filesystem with workspace override
                found_job, _ = await find_job_state(job_id, workspace)
            elif not routed:
                require_conductor(routed, json_output=json_output)
                return  # unreachable

            # Clear screen and show status
            console.clear()

            if not found_job:
                if json_output:
                    err_msg = f"{ErrorMessages.JOB_NOT_FOUND}: {job_id}"
                    output_json({"error": err_msg})
                else:
                    output_error(
                        f"{ErrorMessages.JOB_NOT_FOUND}: {job_id}",
                        hints=["Run 'mozart list' to see available scores."],
                    )
            else:
                if json_output:
                    _output_status_json(found_job)
                else:
                    _output_status_rich(found_job)

                # Show watch mode indicator
                now = datetime.now(UTC)
                console.print(
                    f"\n[dim]Last updated: {now.strftime('%H:%M:%S')} "
                    f"| Refreshing every {interval}s | Press Ctrl+C to stop[/dim]"
                )

                # Exit watch mode if job is completed or failed
                if found_job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                    console.print(
                        f"\n[yellow]Score {found_job.status.value} - exiting watch mode[/yellow]"
                    )
                    break

            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n[dim]Watch mode stopped[/dim]")


_ACTIVE_STATUSES = {"queued", "running", "paused"}
_RECENT_TERMINAL = {"completed", "failed", "cancelled"}


async def _status_overview(json_output: bool) -> None:
    """Show overview of all scores — like 'git status' for Mozart.

    Displays conductor status, active scores, and recent completions.
    """
    from mozart.daemon.detect import try_daemon_route

    # Check conductor
    try:
        routed, result = await try_daemon_route("daemon.health", {})
    except Exception:
        routed = False
        result = None

    if not routed:
        output_error(
            "Mozart conductor is not running.",
            hints=["Start it with: mozart start"],
            json_output=json_output,
        )
        raise typer.Exit(1)

    # Get job list
    try:
        _, jobs_data = await try_daemon_route("job.list", {})
    except Exception:
        jobs_data = []

    jobs: list[dict[str, Any]] = jobs_data if isinstance(jobs_data, list) else []

    # Split into active and recent
    active = [j for j in jobs if str(j.get("status", "")).lower() in _ACTIVE_STATUSES]
    recent = [
        j for j in jobs
        if str(j.get("status", "")).lower() in _RECENT_TERMINAL
    ]
    # Sort recent by submission time descending, limit to 5
    recent.sort(key=lambda j: j.get("submitted_at", 0) or 0, reverse=True)
    recent = recent[:5]

    if json_output:
        output_json({
            "conductor": "running",
            "active_count": len(active),
            "active": active,
            "recent_count": len(recent),
            "recent": recent,
        })
        return

    # Conductor header
    health = result if isinstance(result, dict) else {}
    uptime_str = _format_uptime(health.get("uptime_seconds"))
    console.print(
        f"[bold]Mozart Conductor:[/bold] [green]RUNNING[/green]"
        f"{f'  ({uptime_str})' if uptime_str else ''}"
    )
    console.print()

    # Active scores
    if active:
        console.print("[bold]ACTIVE[/bold]")
        _render_overview_jobs(active, show_elapsed=True)
    else:
        console.print("[dim]No active scores.[/dim]")

    # Recent completions
    if recent:
        console.print("\n[bold]RECENT[/bold]")
        _render_overview_jobs(recent, show_elapsed=False)

    total = len(active)
    summary_parts = []
    if total:
        summary_parts.append(f"{total} score{'s' if total != 1 else ''} active")
    else:
        summary_parts.append("No active scores")

    console.print(f"\n[dim]{'. '.join(summary_parts)}."
                  " Use 'mozart status <score>' for details.[/dim]")


def _render_overview_jobs(
    jobs: list[dict[str, Any]],
    *,
    show_elapsed: bool,
) -> None:
    """Render a compact list of jobs for the overview display."""
    _colors: dict[str, str] = {
        "queued": "yellow",
        "running": "green",
        "completed": "bright_green",
        "paused": "yellow",
        "failed": "red",
        "cancelled": "dim",
    }

    for dj in jobs:
        job_id = dj.get("job_id", "?")
        raw_status = str(dj.get("status", "unknown")).lower()
        color = _colors.get(raw_status, "white")
        status_str = f"[{color}]{raw_status.upper()}[/{color}]"

        # Build info parts
        parts = [f"  {job_id:<24s} {status_str}"]

        # Elapsed or total time
        submitted_at = dj.get("submitted_at")
        if show_elapsed and submitted_at and raw_status == "running":
            elapsed = datetime.now(UTC).timestamp() - submitted_at
            parts.append(f"  {format_duration(elapsed)} elapsed")
        elif not show_elapsed and submitted_at:
            parts.append(f"  {_format_daemon_timestamp(submitted_at)}")

        console.print("".join(parts))


def _format_uptime(seconds: float | None) -> str:
    """Format uptime seconds into a human-readable string."""
    if seconds is None:
        return ""
    s = int(seconds)
    if s < 60:
        return f"uptime {s}s"
    if s < 3600:
        return f"uptime {s // 60}m {s % 60}s"
    hours = s // 3600
    minutes = (s % 3600) // 60
    if hours < 24:
        return f"uptime {hours}h {minutes}m"
    days = hours // 24
    remaining_hours = hours % 24
    return f"uptime {days}d {remaining_hours}h"


async def _list_jobs(
    all_jobs: bool,
    status_filter: str | None,
    limit: int,
    workspace: Path | None,
) -> None:
    """List jobs from the daemon's persistent registry."""
    from mozart.daemon.detect import try_daemon_route

    _ = workspace  # Reserved for future per-workspace filtering

    routed, result = await try_daemon_route("job.list", {})
    if not routed:
        output_error(
            "Mozart conductor is not running.",
            hints=["Start it with: mozart start"],
        )
        raise typer.Exit(1)

    jobs: list[dict[str, Any]] = result if isinstance(result, list) else []

    # Status color map
    _colors: dict[str, str] = {
        "queued": "yellow",
        "running": "green",
        "completed": "bright_green",
        "paused": "yellow",
        "failed": "red",
        "cancelled": "dim",
    }

    # Filter: explicit --status overrides --all, otherwise default to active-only
    if status_filter:
        target = status_filter.lower()
        jobs = [j for j in jobs if str(j.get("status", "")).lower() == target]
    elif not all_jobs:
        jobs = [j for j in jobs if str(j.get("status", "")).lower() in _ACTIVE_STATUSES]

    # Limit
    jobs = jobs[:limit]

    if not jobs:
        if status_filter:
            console.print(f"[dim]No {status_filter} scores found.[/dim]")
        elif all_jobs:
            console.print("[dim]No scores found.[/dim]")
        else:
            console.print(
                "[dim]No active scores.[/dim]"
                " Use [bold]--all[/bold] to see score history."
            )
        return

    # Build rows and compute column widths
    headers = ("SCORE ID", "STATUS", "WORKSPACE", "SUBMITTED")
    rows: list[tuple[str, str, str, str]] = []
    for dj in jobs:
        rows.append((
            dj.get("job_id", "?"),
            str(dj.get("status", "unknown")),
            dj.get("workspace", "-"),
            _format_daemon_timestamp(dj.get("submitted_at")),
        ))

    widths = [len(hdr) for hdr in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))

    gap = "   "
    fmt = gap.join(f"{{:<{col_w}}}" for col_w in widths)

    # Header
    console.print(f"[bold]{fmt.format(*headers)}[/bold]", soft_wrap=True)

    # Rows
    for row in rows:
        color = _colors.get(row[1], "white")
        styled = fmt.format(row[0], row[1], row[2], row[3])
        # Apply color to the status portion only
        plain_status = row[1]
        styled = styled.replace(
            plain_status,
            f"[{color}]{plain_status}[/{color}]",
            1,
        )
        console.print(styled, soft_wrap=True)

    console.print(f"\n[dim]{len(rows)} score(s)[/dim]")


def _format_daemon_timestamp(ts: float | None) -> str:
    """Format a Unix timestamp from daemon JobMeta."""
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M")


# =============================================================================
# Output Formatters
# =============================================================================


def _output_meta_status(meta: dict[str, Any], json_output: bool) -> None:
    """Render basic status from JobMeta when full CheckpointState is unavailable.

    This happens when the conductor has the job registered but the state file
    hasn't been written yet (job just started) or the workspace path doesn't
    resolve to a loadable state backend.
    """
    if json_output:
        output_json(meta)
        return

    job_id = meta.get("job_id", "unknown")
    status = meta.get("status", "unknown")
    color = {"running": "green", "queued": "blue", "failed": "red"}.get(status, "yellow")

    console.print(Panel(
        f"[bold]{job_id}[/bold]\n"
        f"ID: {job_id}\n"
        f"Status: [{color}]{status.upper()}[/{color}]",
        title="Score Status",
        border_style=color,
    ))

    submitted = meta.get("submitted_at")
    config_path = meta.get("config_path", "-")

    console.print(f"\n  Config: {config_path}")
    if submitted:
        console.print(f"  Submitted: {_format_daemon_timestamp(submitted)}")

    console.print(
        "\n[dim]Full status unavailable — state file not yet written "
        "or workspace path mismatch.[/dim]"
    )


def _output_status_json(job: CheckpointState) -> None:
    """Output job status as JSON."""
    # Build a clean JSON representation
    # Use last_completed_sheet for progress since it's more reliable than counting sheets dict
    completed = job.last_completed_sheet
    total = job.total_sheets
    percent = (completed / total * 100) if total > 0 else 0.0

    # Collect recent errors for JSON output
    recent_errors_data: list[dict[str, Any]] = []
    for sheet_num, error in _collect_recent_errors(job, limit=5):
        recent_errors_data.append({
            "sheet_num": sheet_num,
            "timestamp": error.timestamp.isoformat() if error.timestamp else None,
            "error_type": error.error_type,
            "error_code": error.error_code,
            "error_message": error.error_message,
        })

    # Get last activity time
    last_activity = get_last_activity_time(job)

    # Infer circuit breaker state
    cb_state = _infer_circuit_breaker_state(job)

    # Build per-sheet JSON with enhanced fields
    sheets_json: dict[str, dict[str, Any]] = {}
    for num, sheet in job.sheets.items():
        display_label, _ = format_sheet_display_status(
            sheet.status, sheet.validation_passed,
        )
        sheet_data: dict[str, Any] = {
            "status": sheet.status.value,
            "display_status": display_label,
            "attempt_count": sheet.attempt_count,
            "validation_passed": sheet.validation_passed,
            "error_message": sheet.error_message,
            "error_category": sheet.error_category,
        }
        if sheet.execution_duration_seconds is not None:
            sheet_data["execution_duration_seconds"] = sheet.execution_duration_seconds
        if sheet.estimated_cost is not None:
            sheet_data["estimated_cost"] = sheet.estimated_cost
        if sheet.status == SheetStatus.IN_PROGRESS and sheet.started_at:
            elapsed = (datetime.now(UTC) - sheet.started_at).total_seconds()
            sheet_data["elapsed_seconds"] = round(elapsed, 1)
        if sheet.progress_snapshots:
            sheet_data["progress_snapshots"] = sheet.progress_snapshots
        if sheet.last_activity_at:
            sheet_data["last_activity_at"] = sheet.last_activity_at.isoformat()
        sheets_json[str(num)] = sheet_data

    output = {
        "job_id": job.job_id,
        "job_name": job.job_name,
        "status": job.status.value,
        "progress": {
            "completed": completed,
            "total": total,
            "percent": round(percent, 1),
        },
        "timing": {
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "last_activity": last_activity.isoformat() if last_activity else None,
        },
        "execution": {
            "current_sheet": job.current_sheet,
            "total_retry_count": job.total_retry_count,
            "rate_limit_waits": job.rate_limit_waits,
        },
        "cost": {
            "total_estimated_cost": job.total_estimated_cost,
            "total_input_tokens": job.total_input_tokens,
            "total_output_tokens": job.total_output_tokens,
            "cost_limit_reached": job.cost_limit_reached,
        },
        "circuit_breaker": cb_state,
        "hook_results_count": len(job.hook_results),
        "hooks_configured": len(
            job.config_snapshot.get("on_success", []) if job.config_snapshot else []
        ),
        "hooks_interrupted": bool(
            job.status == JobStatus.COMPLETED
            and job.config_snapshot
            and job.config_snapshot.get("on_success")
            and not job.hook_results
        ),
        "hook_failures": [
            {
                "hook_name": h.get("hook_name", h.get("name", "unknown")),
                "event": h.get("event"),
                "error": h.get("error", h.get("error_message", "")),
            }
            for h in job.hook_results
            if not h.get("success", False)
        ],
        "recent_errors": recent_errors_data,
        "error": job.error_message,
        "sheets": sheets_json,
    }

    # Add movement grouping when movement data is available (M3 step 31)
    if _has_movement_data(job):
        output["movements"] = _build_movement_groups(job)

    output_json(output)


_LARGE_SCORE_THRESHOLD = 50  # Switch to summary view above this sheet count


# =============================================================================
# Movement-Grouped Status Display (M3 step 31)
# =============================================================================


def _has_movement_data(job: CheckpointState) -> bool:
    """Check whether any sheet in the job has movement metadata populated.

    Returns True when at least one sheet has a non-None movement field,
    indicating the job was executed with sheet-first architecture entities.
    Legacy jobs (pre-instrument-plugin) will have all movements as None.
    """
    return any(s.movement is not None for s in job.sheets.values())


def _build_movement_groups(
    job: CheckpointState,
) -> list[dict[str, Any]]:
    """Build structured movement group data for JSON output or rendering.

    Groups sheets by their movement number and computes per-movement
    status, voice count, instrument, and descriptions. Movements are
    returned in sorted order.

    Args:
        job: The CheckpointState with sheet-first movement metadata.

    Returns:
        List of dicts, one per movement, with keys:
        - movement: int
        - status: str (completed, running, failed, pending, skipped)
        - sheet_count: int
        - voice_count: int (number of distinct voices, 0 for solo)
        - completed_count: int
        - instrument: str | None
        - description: str | None
        - sheets: list of sheet_num in this movement
    """
    from collections import defaultdict

    groups: dict[int, list[tuple[int, SheetState]]] = defaultdict(list)
    for sheet_num in sorted(job.sheets.keys()):
        sheet = job.sheets[sheet_num]
        mv = sheet.movement if sheet.movement is not None else 0
        groups[mv].append((sheet_num, sheet))

    # Extract descriptions from config snapshot
    descriptions: dict[int, str] = {}
    if job.config_snapshot:
        sheet_cfg = job.config_snapshot.get("sheet", {})
        raw_descs = sheet_cfg.get("descriptions", {})
        descriptions = {int(k): v for k, v in raw_descs.items()}

    result: list[dict[str, Any]] = []
    for mv_num in sorted(groups.keys()):
        mv_sheets = groups[mv_num]
        statuses = []
        voices: set[int] = set()
        instruments: set[str] = set()
        completed_count = 0

        for _sheet_num, sheet in mv_sheets:
            display_label, _ = format_sheet_display_status(
                sheet.status, sheet.validation_passed,
            )
            statuses.append(display_label)
            if sheet.voice is not None:
                voices.add(sheet.voice)
            if sheet.instrument_name:
                instruments.add(sheet.instrument_name)
            if display_label == "completed":
                completed_count += 1

        # Derive movement-level status
        if all(s == "completed" for s in statuses):
            mv_status = "completed"
        elif any(s == "failed" for s in statuses):
            mv_status = "failed"
        elif any(s in ("in_progress", "running") for s in statuses):
            mv_status = "running"
        elif all(s == "skipped" for s in statuses):
            mv_status = "skipped"
        else:
            mv_status = "pending"

        # Find the description — use the first sheet's description if available
        first_sheet_num = mv_sheets[0][0]
        desc = descriptions.get(first_sheet_num)

        result.append({
            "movement": mv_num,
            "status": mv_status,
            "sheet_count": len(mv_sheets),
            "voice_count": len(voices),
            "completed_count": completed_count,
            "instrument": next(iter(instruments)) if len(instruments) == 1 else None,
            "instruments": sorted(instruments) if len(instruments) > 1 else [],
            "description": desc,
            "sheets": [sn for sn, _ in mv_sheets],
        })

    return result


def _render_movement_grouped_details(
    job: CheckpointState,
    target_console: Console | None = None,
) -> None:
    """Render sheet details grouped by movement for rich status output.

    The hierarchy is:
      Movement N: Description    [status, duration]    instrument
        Voice 1: ...             [status, duration]
        Voice 2: ...             [status, duration]

    When all movements use the same instrument, the instrument column
    is suppressed to reduce noise. When there are no voices (solo
    movements), the voice sub-items are omitted.

    Args:
        job: CheckpointState with movement metadata on sheets.
        target_console: Console to print to. Uses module-level console if None.
    """
    con = target_console or console
    groups = _build_movement_groups(job)

    if not groups:
        return

    # Determine if instruments are heterogeneous
    all_instruments: set[str] = set()
    for g in groups:
        if g["instrument"]:
            all_instruments.add(g["instrument"])
        for instr in g.get("instruments", []):
            all_instruments.add(instr)
    show_instruments = len(all_instruments) > 1

    # Status icons
    icons: dict[str, str] = {
        "completed": "\u2713",
        "running": "\u25b6",
        "failed": "\u2717",
        "pending": "\u00b7",
        "skipped": "\u2298",
    }
    colors: dict[str, str] = {
        "completed": "green",
        "running": "blue",
        "failed": "red",
        "pending": "dim",
        "skipped": "dim",
    }

    con.print("\n[bold]Movements[/bold]")

    for g in groups:
        mv_num = g["movement"]
        mv_status = g["status"]
        icon = icons.get(mv_status, " ")
        color = colors.get(mv_status, "white")

        # Build the movement header line
        parts: list[str] = []
        parts.append(f"  [{color}]{icon}[/{color}]")
        parts.append(f" [bold]Movement {mv_num}[/bold]")

        if g["description"]:
            parts.append(f": {g['description']}")

        # Voice count
        if g["voice_count"] > 0:
            parts.append(f" ({g['voice_count']} voices)")

        # Status detail
        status_detail = f"[{color}]{mv_status}[/{color}]"
        if mv_status == "running" and g["voice_count"] > 0:
            status_detail = (
                f"[{color}]{g['completed_count']}/{g['sheet_count']} complete[/{color}]"
            )
        elif mv_status == "completed":
            # Show duration if we can compute it
            durations = []
            for sn in g["sheets"]:
                sheet = job.sheets.get(sn)
                if sheet and sheet.execution_duration_seconds is not None:
                    durations.append(sheet.execution_duration_seconds)
            if durations:
                total_dur = max(durations)  # parallel: max, not sum
                status_detail += f" [dim]({format_duration(total_dur)})[/dim]"

        parts.append(f"    [{color}]{status_detail}[/{color}]" if mv_status != "completed"
                     else f"    {status_detail}")

        # Instrument (only when heterogeneous)
        if show_instruments:
            instr = g["instrument"] or ", ".join(g.get("instruments", []))
            if instr:
                parts.append(f"  [dim]{instr}[/dim]")

        con.print("".join(parts))

        # Show voice sub-items for harmonized movements
        if g["voice_count"] > 1:
            for sn in g["sheets"]:
                sheet = job.sheets.get(sn)
                if sheet is None or sheet.voice is None:
                    continue
                v_label, v_color = format_sheet_display_status(
                    sheet.status, sheet.validation_passed,
                )
                v_icon = icons.get(v_label, " ")

                voice_parts: list[str] = []
                voice_parts.append(f"      [{v_color}]{v_icon}[/{v_color}]")
                voice_parts.append(f" Voice {sheet.voice}")

                # Duration
                if sheet.status == SheetStatus.IN_PROGRESS and sheet.started_at:
                    elapsed = (
                        datetime.now(UTC) - sheet.started_at
                    ).total_seconds()
                    voice_parts.append(
                        f"  [{v_color}]{v_label}[/{v_color}]"
                        f" [dim]({format_duration(elapsed)})[/dim]"
                    )
                elif sheet.execution_duration_seconds is not None:
                    voice_parts.append(
                        f"  [{v_color}]{v_label}[/{v_color}]"
                        f" [dim]({format_duration(sheet.execution_duration_seconds)})[/dim]"
                    )
                else:
                    voice_parts.append(f"  [{v_color}]{v_label}[/{v_color}]")

                con.print("".join(voice_parts))


def _render_sheet_details(job: CheckpointState) -> None:
    """Render the sheet details table for rich status output.

    Shows elapsed time for in-progress sheets and duration for completed ones.
    When the job config includes sheet descriptions (GH#75), a Description
    column is added between the sheet number and status columns.

    For large scores (50+ sheets), shows a summary of counts-by-status
    with only non-pending/non-completed sheets listed individually.

    When sheets have movement metadata (sheet-first architecture), renders
    a movement-grouped hierarchical view instead of a flat table.
    """
    if not job.sheets:
        return

    # Movement-grouped display when movement data is available (M3 step 31)
    if _has_movement_data(job):
        _render_movement_grouped_details(job)
        return

    # Large scores get a summary view to avoid 700+ row tables (F-038)
    if len(job.sheets) >= _LARGE_SCORE_THRESHOLD:
        _render_sheet_summary(job)
        return

    # Extract descriptions from config snapshot (GH#75)
    descriptions: dict[int, str] = {}
    if job.config_snapshot:
        sheet_cfg = job.config_snapshot.get("sheet", {})
        raw_descs = sheet_cfg.get("descriptions", {})
        # Keys may be strings after JSON round-trip
        descriptions = {int(k): v for k, v in raw_descs.items()}

    has_descriptions = bool(descriptions)

    console.print("\n[bold]Sheet Details[/bold]")
    sheet_table = create_sheet_details_table(has_descriptions=has_descriptions)

    for sheet_num in sorted(job.sheets.keys()):
        sheet = job.sheets[sheet_num]
        display_label, sheet_color = format_sheet_display_status(
            sheet.status, sheet.validation_passed,
        )
        val_str = format_validation_status(sheet.validation_passed)

        # Build status string with elapsed time for in-progress sheets
        status_str = f"[{sheet_color}]{display_label}[/{sheet_color}]"
        if sheet.status == SheetStatus.IN_PROGRESS and sheet.started_at:
            elapsed = datetime.now(UTC) - sheet.started_at
            status_str += f" [dim]({format_duration(elapsed.total_seconds())})[/dim]"
        elif sheet.execution_duration_seconds is not None:
            status_str += f" [dim]({format_duration(sheet.execution_duration_seconds)})[/dim]"

        error_str = ""
        if sheet.error_message:
            error_str = sheet.error_message[:50]
            if len(sheet.error_message) > 50:
                error_str += "..."

        row: list[str] = [str(sheet_num)]
        if has_descriptions:
            row.append(descriptions.get(sheet_num, ""))
        row.extend([status_str, str(sheet.attempt_count), val_str, error_str])

        sheet_table.add_row(*row)

    console.print(sheet_table)

    # Show validation failure details for failed sheets
    failures: list[tuple[int, list[ValidationDetailDict]]] = []
    for sheet_num in sorted(job.sheets.keys()):
        sheet = job.sheets[sheet_num]
        if sheet.validation_passed is not False or not sheet.validation_details:
            continue
        failed = [v for v in sheet.validation_details if not v.get("passed")]
        if failed:
            failures.append((sheet_num, failed))

    if failures:
        console.print("\n[bold]Validation Failures[/bold]")
        for sheet_num, failed_validations in failures:
            for v in failed_validations:
                desc = v.get("description") or v.get("rule_type", "unknown")
                detail = v.get("error_message") or ""
                if detail:
                    console.print(f"  Sheet {sheet_num}: [red]{desc}[/red] — {detail}")
                else:
                    console.print(f"  Sheet {sheet_num}: [red]{desc}[/red]")


def _render_sheet_summary(job: CheckpointState) -> None:
    """Render a compact summary for large scores (50+ sheets).

    Instead of listing every sheet, shows counts-by-status and lists
    only sheets that are actively interesting (running, failed, retrying).
    Addresses F-038: status display unusable for large scores.
    """
    # Count sheets by status
    status_counts: dict[str, int] = {}
    interesting_sheets: list[tuple[int, Any]] = []  # (sheet_num, SheetState)

    for sheet_num in sorted(job.sheets.keys()):
        sheet = job.sheets[sheet_num]
        label, _ = format_sheet_display_status(sheet.status, sheet.validation_passed)
        status_counts[label] = status_counts.get(label, 0) + 1

        # Collect "interesting" sheets: running, failed, in_progress
        if sheet.status in (
            SheetStatus.IN_PROGRESS,
            SheetStatus.FAILED,
        ) or (
            sheet.status == SheetStatus.COMPLETED and sheet.validation_passed is False
        ):
            interesting_sheets.append((sheet_num, sheet))

    console.print("\n[bold]Sheet Summary[/bold]")
    total = len(job.sheets)

    # Show counts as a compact line
    # Use display labels from format_sheet_display_status for color
    parts: list[str] = []
    order = ["completed", "failed", "running", "in_progress", "pending", "skipped"]
    for label in order:
        count = status_counts.get(label, 0)
        if count == 0:
            continue
        # Map label back to a color — use the color from format_sheet_display_status
        color_map = {
            "completed": "bright_green",
            "failed": "red",
            "running": "green",
            "in_progress": "blue",
            "pending": "dim",
            "skipped": "dim",
        }
        color = color_map.get(label, "white")
        parts.append(f"[{color}]{count} {label}[/{color}]")

    # Add any status not in the predefined order
    for label, count in status_counts.items():
        if label not in order and count > 0:
            parts.append(f"{count} {label}")

    console.print(f"  {total} sheets: {', '.join(parts)}")

    # Show interesting sheets individually
    if interesting_sheets:
        console.print("\n[bold]Active & Failed Sheets[/bold]")
        for sheet_num, sheet in interesting_sheets[:20]:  # Cap at 20
            display_label, sheet_color = format_sheet_display_status(
                sheet.status, sheet.validation_passed,
            )
            status_str = f"[{sheet_color}]{display_label}[/{sheet_color}]"

            if sheet.status == SheetStatus.IN_PROGRESS and sheet.started_at:
                elapsed = datetime.now(UTC) - sheet.started_at
                status_str += f" [dim]({format_duration(elapsed.total_seconds())})[/dim]"
            elif sheet.execution_duration_seconds is not None:
                status_str += f" [dim]({format_duration(sheet.execution_duration_seconds)})[/dim]"

            error_str = ""
            if sheet.error_message:
                error_str = f" — {sheet.error_message[:40]}..."

            console.print(f"  Sheet {sheet_num:>4d}: {status_str}{error_str}")

        if len(interesting_sheets) > 20:
            console.print(
                f"  [dim]... and {len(interesting_sheets) - 20} more[/dim]"
            )

    # Validation failures for completed-but-failed-validation sheets
    failures: list[tuple[int, list[ValidationDetailDict]]] = []
    for sheet_num in sorted(job.sheets.keys()):
        sheet = job.sheets[sheet_num]
        if sheet.validation_passed is not False or not sheet.validation_details:
            continue
        failed = [v for v in sheet.validation_details if not v.get("passed")]
        if failed:
            failures.append((sheet_num, failed))

    if failures:
        shown = failures[:10]  # Cap validation details too
        console.print("\n[bold]Validation Failures[/bold]")
        for sheet_num, failed_validations in shown:
            for v in failed_validations:
                desc = v.get("description") or v.get("rule_type", "unknown")
                detail = v.get("error_message") or ""
                if detail:
                    console.print(f"  Sheet {sheet_num}: [red]{desc}[/red] — {detail}")
                else:
                    console.print(f"  Sheet {sheet_num}: [red]{desc}[/red]")
        if len(failures) > 10:
            console.print(
                f"\n  [dim]... and {len(failures) - 10} more validation failures. "
                f"Run 'mozart errors {job.job_id} --verbose' for details.[/dim]"
            )


def _render_recent_errors(job: CheckpointState) -> None:
    """Render the recent errors section with error source identity.

    Each error line includes sheet number, attempt number, and backend
    context (if available) so operators can trace exactly where the
    error occurred.
    """
    recent_errors = _collect_recent_errors(job, limit=3)
    if not recent_errors:
        return

    console.print("\n[bold red]Recent Errors[/bold red]")
    for sheet_num, error in recent_errors:
        type_style = StatusColors.get_error_color(error.error_type)

        message = error.error_message or ""
        if len(message) > 60:
            message = message[:57] + "..."

        # Build source identity: sheet + attempt + backend (if known)
        source_parts = [f"Sheet {sheet_num}", f"attempt {error.attempt_number}"]
        backend = error.context.get("backend") if error.context else None
        if backend:
            source_parts.append(str(backend))
        source_str = ", ".join(source_parts)

        console.print(
            f"  [{type_style}]\u2022[/{type_style}] [{type_style}]{error.error_code}[/{type_style}]"
            f" [dim]({source_str})[/dim] - {message}"
        )

    console.print(
        f"\n[dim]  Use 'mozart errors {job.job_id}' for complete error history[/dim]"
    )


def _render_synthesis_results(job: CheckpointState) -> None:
    """Render the synthesis results table for rich status output."""
    if not job.synthesis_results:
        return

    console.print("\n[bold]Synthesis Results[/bold]")
    synth_table = create_synthesis_table()

    for batch_id, result in job.synthesis_results.items():
        sheets = result.get("sheets", [])
        sheets_str = ", ".join(str(s) for s in sheets[:4])
        if len(sheets) > 4:
            sheets_str += f" (+{len(sheets) - 4})"

        strategy = result.get("strategy", "merge")
        synth_status = result.get("status", "pending")
        synth_status_color = StatusColors.SYNTHESIS_STATUS.get(synth_status, "white")

        synth_table.add_row(
            batch_id[:12],
            sheets_str,
            strategy,
            f"[{synth_status_color}]{synth_status}[/{synth_status_color}]",
        )

    console.print(synth_table)


def _render_cost_summary(job: CheckpointState) -> None:
    """Render cost tracking summary — always shown for visibility.

    Cost information is displayed regardless of whether cost limits are
    enabled, so users are always aware of API spend. When cost limits
    are disabled, a warning is shown to encourage configuring them.
    """
    has_job_cost = job.total_estimated_cost > 0
    has_sheet_cost = any(
        s.estimated_cost is not None and s.estimated_cost > 0
        for s in job.sheets.values()
    )

    # Extract cost limit and enabled status from config snapshot
    cost_limit: float | None = None
    cost_limits_enabled = False
    if job.config_snapshot:
        cost_limits_cfg = job.config_snapshot.get("cost_limits", {})
        if isinstance(cost_limits_cfg, dict):
            cost_limit = cost_limits_cfg.get("max_cost_per_job")
            cost_limits_enabled = bool(cost_limits_cfg.get("enabled", False))

    if cost_limit and cost_limits_enabled:
        limit_str = f"(limit: ${cost_limit:.2f})"
    elif cost_limit:
        limit_str = f"(limit: ${cost_limit:.2f}, not enforced)"
    else:
        limit_str = "(no limit set)"

    console.print("\n[bold]Cost Summary[/bold]")
    if has_job_cost:
        console.print(
            f"  Cost: [yellow]${job.total_estimated_cost:.2f}[/yellow] {limit_str}"
        )
        console.print(f"  Input tokens:  {job.total_input_tokens:,}")
        console.print(f"  Output tokens: {job.total_output_tokens:,}")
        if job.cost_limit_reached:
            console.print("  [red]Cost limit reached — score was paused[/red]")
    elif has_sheet_cost:
        # Sum from individual sheets if job-level totals aren't populated
        total = sum(s.estimated_cost for s in job.sheets.values() if s.estimated_cost)
        console.print(f"  Cost: [yellow]${total:.2f}[/yellow] {limit_str} (from sheets)")
    else:
        console.print(f"  Cost: $0.00 {limit_str}")

    if not cost_limits_enabled:
        console.print(
            "  [dim]Tip: Set cost_limits.enabled: true in your score"
            " to prevent unexpected charges[/dim]"
        )


def _render_hook_results(job: CheckpointState) -> None:
    """Render hook execution results if any are recorded.

    Also warns when hooks were configured but no results exist,
    indicating hooks were interrupted (e.g., daemon restart during
    cooldown) or never executed.
    """
    # Check if on_success hooks were configured
    configured_hooks: list[dict[str, Any]] = []
    if job.config_snapshot:
        configured_hooks = job.config_snapshot.get("on_success", [])

    if not job.hook_results and not configured_hooks:
        return

    if configured_hooks and not job.hook_results:
        # Only warn about missing hook results for completed jobs.
        # Running/pending jobs haven't reached hook execution yet.
        if job.status != JobStatus.COMPLETED:
            return
        console.print("\n[bold]Hook Results[/bold]")
        console.print(
            f"  [yellow]WARNING:[/yellow] {len(configured_hooks)} on_success "
            f"hook(s) configured but no results recorded"
        )
        console.print(
            "  [dim]Hooks may have been interrupted (e.g., conductor restart "
            "during cooldown)[/dim]"
        )
        for hook_cfg in configured_hooks:
            hook_type = hook_cfg.get("type", "unknown")
            desc = hook_cfg.get("description", "")
            label = f"{hook_type}: {desc}" if desc else hook_type
            job_path = hook_cfg.get("job_path", "")
            if job_path:
                console.print(f"  [yellow]\u2022[/yellow] {label} \u2192 {job_path}")
            else:
                console.print(f"  [yellow]\u2022[/yellow] {label}")
        return

    if not job.hook_results:
        return

    console.print("\n[bold]Hook Results[/bold]")

    # Show summary counts
    passed = sum(1 for h in job.hook_results if h.get("success", False))
    failed = len(job.hook_results) - passed

    console.print(f"  Total: {len(job.hook_results)} | "
                  f"[green]Passed: {passed}[/green] | "
                  f"[red]Failed: {failed}[/red]")

    # Warn if fewer results than configured hooks
    if configured_hooks and len(job.hook_results) < len(configured_hooks):
        missing = len(configured_hooks) - len(job.hook_results)
        console.print(
            f"  [yellow]WARNING:[/yellow] {missing} hook(s) did not produce results"
        )

    # Show details for failed hooks (most useful for diagnostics)
    failed_hooks = [hr for hr in job.hook_results if not hr.get("success", False)]
    for hook in failed_hooks[-3:]:  # Last 3 failures
        hook_type = hook.get("hook_type", hook.get("hook_name", "unknown"))
        desc = hook.get("description", "")
        error = hook.get("error_message", hook.get("error", ""))
        if len(error) > 80:
            error = error[:77] + "..."
        label = f"{hook_type}: {desc}" if desc else hook_type
        console.print(f"  [red]\u2022[/red] {label} \u2014 {error}")


def _render_progress_snapshots(job: CheckpointState) -> None:
    """Render progress snapshots for any in-progress sheets.

    Shows real-time execution progress: bytes received, lines processed,
    and current phase. Only displays for sheets currently executing.
    """
    for sheet_num in sorted(job.sheets.keys()):
        sheet = job.sheets[sheet_num]
        if sheet.status != SheetStatus.IN_PROGRESS:
            continue
        if not sheet.progress_snapshots:
            continue

        console.print(f"\n[bold]Live Progress — Sheet {sheet_num}[/bold]")
        latest = sheet.progress_snapshots[-1]

        phase = latest.get("phase", "unknown")
        phase_color = {"starting": "yellow", "executing": "blue", "completed": "green"}.get(
            phase, "white"
        )
        console.print(f"  Phase: [{phase_color}]{phase}[/{phase_color}]")

        bytes_recv = latest.get("bytes_received")
        if bytes_recv is not None:
            from mozart.cli.output import format_bytes
            console.print(f"  Output received: {format_bytes(bytes_recv)}")

        lines = latest.get("lines_received")
        if lines is not None:
            console.print(f"  Lines received: {lines:,}")

        elapsed = latest.get("elapsed_seconds")
        if elapsed is not None:
            console.print(f"  Elapsed: {format_duration(elapsed)}")

        if sheet.last_activity_at:
            console.print(f"  Last activity: {format_timestamp(sheet.last_activity_at)}")

        snap_count = len(sheet.progress_snapshots)
        if snap_count > 1:
            console.print(f"  [dim]({snap_count} snapshots captured)[/dim]")


def _output_status_rich(job: CheckpointState) -> None:
    """Output job status with rich formatting."""
    status_color = StatusColors.get_job_color(job.status)

    header_lines = [
        f"[bold]{job.job_name}[/bold]",
        f"ID: [cyan]{job.job_id}[/cyan]",
        f"Status: [{status_color}]{job.status.value.upper()}[/{status_color}]",
    ]

    if job.started_at:
        if job.completed_at:
            duration = job.completed_at - job.started_at
            header_lines.append(f"Duration: {format_duration(duration.total_seconds())}")
        elif job.status == JobStatus.RUNNING and job.updated_at:
            elapsed = datetime.now(UTC) - job.started_at
            header_lines.append(f"Running for: {format_duration(elapsed.total_seconds())}")

    console.print(Panel("\n".join(header_lines), title="Score Status"))

    # Progress bar
    console.print("\n[bold]Progress[/bold]")
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        console=console,
        transient=False,
    ) as progress:
        progress.add_task("Sheets", total=job.total_sheets, completed=job.last_completed_sheet)
        progress.refresh()

    # Parallel execution info
    if job.parallel_enabled:
        console.print("\n[bold]Parallel Execution[/bold]")
        console.print("  Mode: [cyan]Enabled[/cyan]")
        console.print(f"  Max concurrent: {job.parallel_max_concurrent}")
        console.print(f"  Batches executed: {job.parallel_batches_executed}")
        if job.sheets_in_progress:
            in_progress_str = ", ".join(str(s) for s in job.sheets_in_progress)
            console.print(f"  Currently running: [blue]{in_progress_str}[/blue]")

    _render_synthesis_results(job)
    _render_sheet_details(job)

    # Progress snapshots for in-progress sheets
    _render_progress_snapshots(job)

    # Error/info message
    if job.error_message:
        if "Recovered from stale" in job.error_message or "ready to resume" in job.error_message:
            console.print(f"\n[bold cyan]Note:[/bold cyan] {job.error_message}")
        else:
            console.print(f"\n[bold red]Error:[/bold red] {job.error_message}")

    # Timing info
    console.print("\n[bold]Timing[/bold]")
    if job.created_at:
        console.print(f"  Created:  {format_timestamp(job.created_at)}")
    if job.started_at:
        console.print(f"  Started:  {format_timestamp(job.started_at)}")
    if job.updated_at:
        console.print(f"  Updated:  {format_timestamp(job.updated_at)}")
    # F-068: Only show "Completed:" for terminal statuses. Running/paused jobs
    # may have completed_at set (from individual sheet completions) but showing
    # it creates cognitive dissonance with the RUNNING status label.
    _TERMINAL_JOB_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
    if job.completed_at and job.status in _TERMINAL_JOB_STATUSES:
        console.print(f"  Completed: {format_timestamp(job.completed_at)}")

    # Execution stats
    quota_waits = getattr(job, "quota_waits", 0)
    if job.total_retry_count > 0 or job.rate_limit_waits > 0 or quota_waits > 0:
        console.print("\n[bold]Execution Stats[/bold]")
        console.print(f"  Total retries: {job.total_retry_count}")
        console.print(f"  Rate limit waits: {job.rate_limit_waits}")
        if quota_waits > 0:
            console.print(f"  Quota exhaustion waits: {quota_waits}")

    # Cost summary
    _render_cost_summary(job)

    # Hook execution results
    _render_hook_results(job)

    _render_recent_errors(job)

    # Last activity timestamp
    last_activity = get_last_activity_time(job)
    if last_activity:
        console.print("\n[bold]Last Activity[/bold]")
        console.print(f"  {format_timestamp(last_activity)}")

    # Circuit breaker state indicator
    cb_state = _infer_circuit_breaker_state(job)
    if cb_state:
        cb_color = {"open": "red", "half_open": "yellow", "closed": "green"}.get(
            cb_state["state"], "white"
        )
        reason = cb_state.get("reason", "")
        cb_source = "persisted" if reason.startswith("Persisted:") else "inferred"
        console.print(f"\n[bold]Circuit Breaker ({cb_source})[/bold]")
        console.print(f"  State: [{cb_color}]{cb_state['state'].upper()}[/{cb_color}]")
        console.print(f"  Consecutive failures: {cb_state['consecutive_failures']}")
        if reason:
            console.print(f"  [dim]{reason}[/dim]")

    # Suggest diagnose for failed jobs
    if job.status == JobStatus.FAILED:
        console.print(
            f"\n[yellow]Run 'mozart diagnose {job.job_id}' for a full diagnostic report.[/yellow]"
        )


# =============================================================================
# Helper Functions
# =============================================================================


def _collect_recent_errors(
    job: CheckpointState,
    limit: int = 3,
) -> list[tuple[int, ErrorRecord]]:
    """Collect the most recent errors from sheet states.

    Args:
        job: CheckpointState to collect errors from.
        limit: Maximum number of errors to return.

    Returns:
        List of (sheet_num, ErrorRecord) tuples, sorted by timestamp descending.
    """
    all_errors: list[tuple[int, ErrorRecord]] = []

    for sheet_num, sheet in job.sheets.items():
        # Collect from error_history
        for error in sheet.error_history:
            all_errors.append((sheet_num, error))

        # If no history but has error_message, create synthetic record
        if not sheet.error_history and sheet.error_message:
            synthetic = ErrorRecord(
                error_type=_infer_error_type(sheet.error_category),
                error_code=sheet.error_category or "E999",
                error_message=sheet.error_message,
                attempt_number=max(sheet.attempt_count, 1),
                stdout_tail=sheet.stdout_tail,
                stderr_tail=sheet.stderr_tail,
                context={
                    "exit_code": sheet.exit_code,
                    "exit_signal": sheet.exit_signal,
                    "exit_reason": sheet.exit_reason,
                },
            )
            if sheet.completed_at:
                synthetic.timestamp = sheet.completed_at
            all_errors.append((sheet_num, synthetic))

    # Sort by timestamp (most recent first) and take limit
    all_errors.sort(key=lambda x: x[1].timestamp, reverse=True)
    return all_errors[:limit]


def _infer_circuit_breaker_state(job: CheckpointState) -> CircuitBreakerInference | None:
    """Get circuit breaker state from persisted history, falling back to inference.

    Prefers ground-truth from ``circuit_breaker_history`` (populated when
    the runner records CB transitions at runtime). Falls back to the legacy
    heuristic for state files that predate CB persistence.

    Args:
        job: CheckpointState to analyze.

    Returns:
        CircuitBreakerInference with state info, or None if no relevant data.
    """
    # --- Ground truth: persisted circuit breaker history ---
    cb_history = getattr(job, "circuit_breaker_history", None)
    if cb_history:
        latest = cb_history[-1]
        return CircuitBreakerInference(
            state=latest.get("state", "closed"),
            consecutive_failures=latest.get("consecutive_failures", 0),
            reason=f"Persisted: {latest.get('trigger', 'unknown')}",
        )

    # --- Backward-compat fallback: infer from failure patterns ---
    if not job.sheets:
        return None

    # Count consecutive failures from the end
    sorted_sheets = sorted(job.sheets.items(), key=lambda x: x[0], reverse=True)
    consecutive_failures = 0

    for _sheet_num, sheet in sorted_sheets:
        if sheet.status == SheetStatus.FAILED:
            consecutive_failures += 1
        elif sheet.status == SheetStatus.COMPLETED:
            break
        # PENDING/IN_PROGRESS don't count

    if consecutive_failures == 0:
        return None  # No failures, circuit likely closed, nothing special to show

    threshold = _DEFAULT_CB_THRESHOLD

    if consecutive_failures >= threshold:
        return CircuitBreakerInference(
            state="open",
            consecutive_failures=consecutive_failures,
            reason=f"\u2265{threshold} consecutive failures detected (inferred)",
        )
    return CircuitBreakerInference(
        state="closed",
        consecutive_failures=consecutive_failures,
        reason=f"Under threshold ({consecutive_failures}/{threshold}) (inferred)",
    )


# =============================================================================
# Clear Jobs Command
# =============================================================================


def clear(
    job: list[str] | None = typer.Option(
        None,
        "--job",
        "-j",
        help="Specific score ID(s) to clear. Can be repeated.",
    ),
    status_filter: list[str] | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Status(es) to clear: failed, completed, cancelled. "
        "Can be repeated. Defaults to all terminal statuses.",
    ),
    older_than: float | None = typer.Option(
        None,
        "--older-than",
        help="Only clear scores older than this many seconds.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Clear terminal scores from the conductor registry.

    Removes completed, failed, and/or cancelled scores from the conductor's
    tracking. Running and queued scores are never cleared.

    Examples:
        mozart clear                                 # Clear all terminal scores
        mozart clear --job conductor-fix             # Clear a specific score
        mozart clear --status failed                 # Clear only failed scores
        mozart clear --status failed -s cancelled    # Clear failed + cancelled
        mozart clear --older-than 3600               # Terminal scores older than 1h
        mozart clear -y                              # Skip confirmation
    """
    asyncio.run(_clear_jobs(job, status_filter, older_than, yes))


async def _clear_jobs(
    job_ids: list[str] | None,
    status_filter: list[str] | None,
    older_than: float | None,
    yes: bool,
) -> None:
    """Clear terminal jobs from the conductor registry."""
    from mozart.daemon.detect import try_daemon_route

    statuses = status_filter or ["completed", "failed", "cancelled"]
    # Validate status values
    valid = {"completed", "failed", "cancelled", "paused"}
    invalid = set(statuses) - valid
    if invalid:
        console.print(
            f"[red]Error:[/red] Invalid status(es): {', '.join(invalid)}. "
            f"Valid values: {', '.join(sorted(valid))}"
        )
        raise typer.Exit(1)

    # Confirm unless --yes
    if not yes:
        if job_ids:
            target = ", ".join(job_ids)
        else:
            target = f"all [{', '.join(statuses)}] scores"
        age_note = f" older than {older_than}s" if older_than else ""
        if not typer.confirm(f"Clear {target}{age_note}?"):
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(0)

    params: dict[str, Any] = {"statuses": statuses}
    if job_ids is not None:
        params["job_ids"] = job_ids
    if older_than is not None:
        params["older_than_seconds"] = older_than

    try:
        routed, result = await try_daemon_route("job.clear", params)
    except Exception as exc:
        output_error(str(exc))
        raise typer.Exit(1) from None

    if not routed:
        require_conductor(routed)
        return

    deleted = result.get("deleted", 0) if isinstance(result, dict) else 0
    if deleted:
        console.print(f"[green]Cleared {deleted} score(s).[/green]")
    else:
        console.print("[dim]No matching scores to clear.[/dim]")


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "status",
    "list_jobs",
    "clear",
]
