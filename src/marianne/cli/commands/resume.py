"""Resume command for Marianne CLI.

This module implements the `mzt resume` command which continues
execution of paused or failed jobs.

★ Insight ─────────────────────────────────────
1. **Config reconstruction hierarchy**: The resume command has a clear priority
   for config sources: (1) provided --config file, (2) auto-reload from stored
   config_path if file exists, (3) cached config_snapshot fallback. This
   fallback chain ensures maximum flexibility while maintaining state consistency.

2. **Resumable state validation**: Only PAUSED, FAILED, and RUNNING jobs can be
   resumed. COMPLETED jobs require --force flag to override. This prevents
   accidental re-execution while still allowing intentional retries.

3. **Progress callback injection**: The same progress_callback pattern from run.py
   is used here, enabling seamless visual continuity between fresh runs and resumes.
─────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer

from ..helpers import (
    configure_global_logging,
    require_conductor,
)
from ..output import console, output_error

_logger = logging.getLogger(__name__)


def resume(
    job_id: str = typer.Argument(..., help="Score ID to resume"),
    config_file: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file (optional if config_snapshot exists in state)",
        exists=True,
        readable=True,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force resume even if score appears completed",
    ),
    escalation: bool = typer.Option(
        False,
        "--escalation",
        "-e",
        help="Pause sheets in FERMATA on retry exhaustion for a composer "
        "decision (resolve with `mzt resolve`). Adds to the run's persisted "
        "setting; cannot disable it.",
    ),
    no_reload: bool = typer.Option(
        False,
        "--no-reload",
        help="Use cached config snapshot instead of auto-reloading from YAML file. "
        "By default, Marianne reloads from the original config path if the file exists.",
    ),
    from_sheet: int | None = typer.Option(
        None,
        "--from-sheet",
        min=1,
        help="Reset every sheet >= N to PENDING and re-run from there, regardless "
        "of status (including COMPLETED). Explicit override — re-runs deliberately "
        "skipped sheets too. Without this, resuming a FAILED score automatically "
        "resets only its failed and cascade-skipped sheets.",
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
) -> None:
    """Resume a paused or failed score.

    Loads the score state and continues execution from where it left off.
    By default, Marianne auto-reloads config from the original YAML file
    if it still exists on disk. Use --no-reload to use the cached snapshot.

    Examples:
        mzt resume my-job
        mzt resume my-job --config job.yaml
        mzt resume my-job --no-reload  # Use cached config snapshot
    """
    from ._shared import validate_job_id

    job_id = validate_job_id(job_id)
    asyncio.run(
        _resume_job(
            job_id, config_file, force, escalation,
            no_reload, self_healing, yes, from_sheet
        )
    )


async def _resume_job(
    job_id: str,
    config_file: Path | None,
    force: bool,
    escalation: bool = False,
    no_reload: bool = False,
    self_healing: bool = False,
    auto_confirm: bool = False,
    from_sheet: int | None = None,
) -> None:
    """Resume a paused or failed job.

    Routes through the conductor. The conductor's ``JobManager.resume_job()``
    handles the full execution lifecycle. Requires conductor to be running.

    Args:
        job_id: Job ID to resume.
        config_file: Optional path to config file.
        force: Force resume even if job appears completed.
        escalation: Enable human-in-the-loop escalation for low-confidence sheets.
        no_reload: If True, skip auto-reload and use cached config snapshot.
        self_healing: Enable automatic diagnosis and remediation.
        auto_confirm: Auto-confirm suggested fixes.
    """
    from marianne.daemon.detect import try_daemon_route

    configure_global_logging(console)

    # Route through conductor
    params = {
        "job_id": job_id,
        "workspace": None,
        "config_path": str(config_file) if config_file else None,
        "no_reload": no_reload,
        "from_sheet": from_sheet,
        # #361: previously collected client-side but never sent — the resume
        # flags were silently dropped. Checkpoint values are inherited; these
        # can additionally enable (never disable).
        "escalation": escalation,
        "self_healing": self_healing,
    }
    try:
        routed, result = await try_daemon_route("job.resume", params)
    except Exception as exc:
        # Business logic error from conductor (e.g., job not found)
        output_error(
            str(exc),
            hints=["Run 'mzt list' to see available scores."],
        )
        raise typer.Exit(1) from None

    if routed:
        # Conductor handled the resume
        if isinstance(result, dict):
            status = result.get("status", "unknown")
            message = result.get("message", "")
            if status == "accepted":
                # Note: we intentionally skip await_early_failure() here.
                # Unlike fresh runs, resumes start from a terminal state
                # (FAILED/PAUSED/CANCELLED). The early failure poll races
                # with the conductor's status transition and catches the
                # *previous* terminal state, misreporting it as a new
                # failure (#122). The conductor already validated the job
                # is resumable before accepting.
                console.print(
                    f"[green]Resume accepted for score '[cyan]{job_id}[/cyan]'.[/green]"
                )
                if message:
                    console.print(f"[dim]{message}[/dim]")
                console.print(
                    f"\nMonitor progress: [bold]mzt status {job_id}[/bold]"
                )
                return
            else:
                # Distinguish "not found" from "not resumable" for hints
                is_not_found = "not found" in (message or "").lower()
                hints = (
                    ["Run 'mzt list' to see available scores."]
                    if is_not_found
                    else [
                        f"Run 'mzt diagnose {job_id}' for details.",
                        "Run 'mzt list' to see available scores.",
                    ]
                )
                output_error(
                    message or f"Resume rejected for score '{job_id}'",
                    hints=hints,
                )
                raise typer.Exit(1)
        return

    # Conductor not available - require it
    require_conductor(routed)
    return  # unreachable


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "resume",
]
