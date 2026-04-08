"""Run command for Marianne CLI.

This module implements the ``mzt run`` command which routes scores
through a running conductor (``mzt start``).  Direct execution is not
supported — a running conductor is required (like docker requires dockerd).

The only exception is ``--dry-run``, which validates and displays the
execution plan without executing anything (no conductor needed).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
import yaml
from rich.panel import Panel
from rich.table import Table

from marianne.core.logging import get_logger

from ..helpers import await_early_failure, is_quiet
from ..output import console, output_error, output_json
from ._shared import validate_start_sheet

_logger = get_logger("cli.run")

if TYPE_CHECKING:
    from marianne.core.config import JobConfig


def run(
    config_file: Path = typer.Argument(
        ...,
        help="Path to YAML score configuration file",
        exists=True,
        readable=True,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show what would be executed without running",
    ),
    start_sheet: int | None = typer.Option(
        None,
        "--start-sheet",
        "-s",
        help="Override starting sheet number",
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Override workspace directory. Creates the directory if it doesn't exist. "
        "Takes precedence over the workspace defined in the YAML config.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output result as JSON for machine parsing",
    ),
    escalation: bool = typer.Option(
        False,
        "--escalation",
        "-e",
        help="Enable human-in-the-loop escalation for low-confidence sheets",
    ),
    self_healing: bool = typer.Option(
        False,
        "--self-healing",
        "-H",
        help="Enable automatic diagnosis and remediation when retries are exhausted",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Auto-confirm suggested fixes when using --self-healing",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Delete existing state before running, ensuring a fresh start. "
        "Use this for self-chaining scores or when you want to re-run a completed score "
        "from scratch without resuming from previous state.",
    ),
) -> None:
    """Run a score from a YAML configuration file."""
    from marianne.core.config import JobConfig

    try:
        config = JobConfig.from_yaml(config_file)
    except yaml.YAMLError as e:
        if json_output:
            output_json({"error": f"YAML syntax error: {e}"})
        else:
            output_error(
                f"YAML syntax error: {e}",
                hints=[
                    "Check for indentation issues or invalid YAML characters.",
                    f"Validate with: mzt validate {config_file}",
                ],
            )
        raise typer.Exit(1) from None
    except Exception as e:
        if json_output:
            output_json({"error": str(e)})
        else:
            output_error(
                str(e),
                hints=[f"Validate with: mzt validate {config_file}"],
            )
        raise typer.Exit(1) from None

    # Validate start_sheet (must be positive if provided)
    start_sheet = validate_start_sheet(start_sheet)

    # Override workspace from CLI if provided
    if workspace is not None:
        config.workspace = Path(workspace).resolve()

    # In quiet mode, skip the config panel
    if not is_quiet() and not json_output:
        instrument_display = config.instrument or config.backend.type
        console.print(Panel(
            f"[bold]{config.name}[/bold]\n"
            f"{config.description or 'No description'}\n\n"
            f"Instrument: {instrument_display}\n"
            f"Sheets: {config.sheet.total_sheets} "
            f"({config.sheet.size} items each)\n"
            f"Workspace: {config.workspace}",
            title="Score Configuration",
        ))

    # Cost warning — alert users when cost tracking is disabled
    if not is_quiet() and not json_output and not config.cost_limits.enabled:
        console.print(
            "\n[yellow]Note:[/yellow] Cost tracking is disabled for this score. "
            "API calls will not be monitored or limited."
        )
        console.print(
            "  [dim]To enable, add to your score:[/dim] "
            "cost_limits: {enabled: true, max_cost_per_job: 10.00}"
        )

    # Validate flag compatibility
    if escalation:
        _msg = (
            "--escalation requires interactive console prompts which are "
            "not available in daemon mode. Escalation is not currently "
            "supported."
        )
        if json_output:
            output_json({"error": _msg})
        else:
            output_error(
                _msg,
                hints=[
                    "Remove the --escalation flag to run without interactive escalation.",
                    "Escalation requires a human-in-the-loop and is not yet "
                    "supported in daemon mode.",
                ],
            )
        raise typer.Exit(1)

    if dry_run:
        if not json_output:
            console.print("\n[yellow]Dry run - not executing[/yellow]")
            _show_dry_run(config, config_file)
        else:
            output_json({
                "dry_run": True,
                "job_name": config.name,
                "total_sheets": config.sheet.total_sheets,
                "workspace": str(config.workspace),
            })
        return

    # Route through daemon (required)
    routed = asyncio.run(
        _try_daemon_submit(
            config_file, workspace, fresh, self_healing, yes, json_output,
            start_sheet=start_sheet,
        ),
    )
    if routed:
        return

    # Daemon not available or submission failed
    if json_output:
        output_json({
            "error": "Marianne conductor is not running. Start with: mzt start",
        })
    else:
        output_error(
            "Marianne conductor is not running.",
            hints=["Start it with: mzt start"],
        )
    raise typer.Exit(1)


async def _try_daemon_submit(
    config_file: Path,
    workspace: Path | None,
    fresh: bool,
    self_healing: bool,
    auto_confirm: bool,
    json_output: bool,
    *,
    start_sheet: int | None = None,
) -> bool:
    """Submit a job to the running conductor.

    Returns True if the daemon accepted the submission, False if the
    daemon is not reachable or rejected the job.  Never raises — all
    errors return False so the caller can emit an appropriate error.
    """
    try:
        from marianne.daemon.detect import is_daemon_available, try_daemon_route

        if not await is_daemon_available():
            return False

        params: dict[str, Any] = {
            "config_path": str(config_file.resolve()),
            "fresh": fresh,
            "self_healing": self_healing,
            "self_healing_auto_confirm": auto_confirm,
        }
        if workspace is not None:
            params["workspace"] = str(Path(workspace).resolve())
        if start_sheet is not None:
            params["start_sheet"] = start_sheet

        routed, result = await try_daemon_route("job.submit", params)
        if not routed:
            return False

        status = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
        job_id = result.get("job_id", "?") if isinstance(result, dict) else "?"
        msg = result.get("message", "") if isinstance(result, dict) else ""

        if status == "pending":
            _handle_pending_response(job_id=job_id, message=msg, json_output=json_output)
            return True

        if status != "accepted":
            if json_output:
                output_json(result)
            else:
                rejection = (
                    f"Conductor rejected score: {msg}"
                    if msg
                    else "Conductor rejected score."
                )
                output_error(
                    rejection,
                    hints=_rejection_hints(msg, fresh=fresh),
                )
                # Show active rate limits when rejection is pressure-related
                await _show_rate_limits_on_rejection(msg)
            raise typer.Exit(1)

        # Poll briefly to catch early failures (e.g. template errors).
        # Skip when --fresh: old state may still be transitioning and
        # would produce false failure reports from the previous run (#139).
        early: dict[str, Any] | None = None
        if not fresh:
            early = await await_early_failure(job_id)
        early_status = (
            early.get("status", "") if isinstance(early, dict) else ""
        )
        early_failed = early_status in ("failed", "cancelled")

        if json_output:
            if early_failed:
                output_json(early)
                raise typer.Exit(1)
            output_json(result)
        else:
            if early_failed:
                err = early.get("error_message", "") if isinstance(early, dict) else ""
                hints = [f"Run: mzt diagnose {job_id}"]
                if err:
                    hints.insert(0, err)
                output_error(
                    f"Score failed: {job_id}",
                    hints=hints,
                )
                raise typer.Exit(1)
            console.print(f"[green]Score submitted to conductor:[/green] {job_id}")
            if msg:
                console.print(f"  {msg}")
            console.print(
                f"\n[dim]Monitor with:[/dim] mzt status {job_id} --watch"
            )

        return True
    except (OSError, ConnectionError, TimeoutError) as exc:
        _logger.warning("daemon_submit_failed", error=str(exc), exc_info=True)
        return False
    except Exception as exc:
        # F-450: MethodNotFoundError and other DaemonError subclasses
        # re-raised by try_daemon_route should not silently return False.
        from marianne.daemon.exceptions import DaemonError

        if isinstance(exc, DaemonError):
            output_error(
                str(exc),
                hints=["Restart the conductor: mzt restart"],
                json_output=json_output,
            )
            raise typer.Exit(1) from None
        raise


def _rejection_hints(msg: str, *, fresh: bool = False) -> list[str]:
    """Return context-aware hints based on the conductor's rejection reason.

    Args:
        msg: The rejection message from the conductor.
        fresh: Whether the --fresh flag was used (adjusts "already running" hints).
    """
    msg_lower = msg.lower()

    if "shutting down" in msg_lower:
        return [
            "The conductor is shutting down.",
            "Wait for shutdown to complete, then restart: mzt start",
        ]

    if "pressure" in msg_lower:
        return [
            "The system is under heavy load.",
            "Check active rate limits: mzt clear-rate-limits (to view/clear)",
            "Wait for running jobs to complete or reduce concurrent work.",
        ]

    if "already" in msg_lower and (
        "running" in msg_lower or "queued" in msg_lower
    ):
        hints = [
            "A score with this name is already active.",
            "Pause or cancel it first: mzt pause <id> / mzt cancel <id>",
        ]
        if fresh:
            hints.append(
                "Clear the stale entry: mzt clear --score <id>"
            )
        else:
            hints.append("Or wait for it to finish.")
        return hints

    if "parse" in msg_lower or "failed to parse" in msg_lower:
        return [
            "The score file has errors.",
            "Validate it with: mzt validate <file>",
        ]

    if "workspace" in msg_lower and (
        "not exist" in msg_lower or "not writable" in msg_lower
    ):
        return [
            "The workspace path is invalid.",
            "Create the directory or use --workspace to override.",
        ]

    if "not found" in msg_lower and "config" in msg_lower:
        return [
            "The score file could not be found.",
            "Check the file path and try again.",
        ]

    # Fallback: generic but still actionable
    return [
        "The conductor is running but declined this submission.",
        "Check with: mzt conductor-status",
    ]


def _handle_pending_response(
    *,
    job_id: str,
    message: str,
    json_output: bool,
) -> None:
    """Handle a 'pending' response from the conductor.

    When the conductor queues a job as pending (due to rate limits),
    show an informative message instead of an error. The job will
    start automatically when rate limits clear.
    """
    if json_output:
        output_json({
            "job_id": job_id,
            "status": "pending",
            "message": message,
        })
    else:
        display_msg = message or "Score queued as pending — starts when rate limits clear."
        console.print(f"[yellow]Score queued as pending:[/yellow] {job_id}")
        console.print(f"  {display_msg}")
        console.print(
            f"\n[dim]Monitor with:[/dim] mzt status {job_id} --watch"
        )
        console.print(
            "[dim]Cancel with:[/dim]  mzt cancel " + job_id
        )


async def _show_rate_limits_on_rejection(msg: str) -> None:
    """Query and display active rate limits after a rejection.

    Called after a submission is rejected. When the rejection is
    pressure-related, queries the conductor for active rate limits
    and displays them with time-remaining info.

    Fail-open: never raises. If the query fails, nothing is shown.
    """
    msg_lower = msg.lower()
    if "pressure" not in msg_lower:
        return

    try:
        from ..helpers import query_rate_limits
        from ..output import format_rate_limit_info

        backends = await query_rate_limits()
        if not backends:
            return

        lines = format_rate_limit_info(backends)
        if lines:
            console.print()
            console.print("[bold]Active rate limits:[/bold]")
            for line in lines:
                console.print(f"  [yellow]{line}[/yellow]")
    except Exception:
        pass  # Fail-open — extra info, not critical


def _show_dry_run(config: JobConfig, config_path: Path) -> None:
    """Show what would be executed in dry run mode."""
    table = Table(title="Sheet Plan")
    table.add_column("Sheet", style="cyan")
    table.add_column("Items", style="green")
    table.add_column("Validations", style="yellow")

    for sheet_num in range(1, config.sheet.total_sheets + 1):
        start = (sheet_num - 1) * config.sheet.size + config.sheet.start_item
        end = min(start + config.sheet.size - 1, config.sheet.total_items)
        table.add_row(
            str(sheet_num),
            f"{start}-{end}",
            str(len(config.validations)),
        )

    console.print(table)

    # Rendering preview (show sheet 1 as representative)
    from marianne.validation.rendering import generate_preview
    from marianne.validation.reporter import ValidationReporter

    reporter = ValidationReporter(console)
    preview = generate_preview(config, config_path, max_sheets=1)
    reporter.report_rendering_terminal(preview)

    # Show dependency info if configured
    if config.sheet.dependencies:
        console.print("\n[bold]Sheet Dependencies:[/bold]")
        for sheet_num, deps in sorted(config.sheet.dependencies.items()):
            dep_list = ", ".join(str(d) for d in deps)
            console.print(f"  Sheet {sheet_num} depends on: {dep_list}")


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "run",
]
