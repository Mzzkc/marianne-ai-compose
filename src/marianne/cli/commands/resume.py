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

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.config import JobConfig
from marianne.state import StateBackend

from ..helpers import (
    _find_job_state_direct as require_job_state,
)
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
        help="Enable human-in-the-loop escalation for low-confidence sheets",
    ),
    no_reload: bool = typer.Option(
        False,
        "--no-reload",
        help="Use cached config snapshot instead of auto-reloading from YAML file. "
        "By default, Marianne reloads from the original config path if the file exists.",
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
            no_reload, self_healing, yes
        )
    )


async def _find_job_state(
    job_id: str,
    force: bool,
) -> tuple[CheckpointState, StateBackend]:
    """Find and validate job state from available backends.

    Uses require_job_state for the find+error pattern, then validates
    that the job is in a resumable status.

    Args:
        job_id: Job ID to find.
        force: Allow resuming completed jobs.

    Returns:
        Tuple of (found_state, found_backend).

    Raises:
        typer.Exit: If job not found or not in resumable state.
    """
    found_state, found_backend = await require_job_state(job_id, None)

    # Check if job is in a resumable state
    resumable_statuses = {
        JobStatus.PAUSED, JobStatus.FAILED, JobStatus.RUNNING, JobStatus.CANCELLED,
    }
    if found_state.status not in resumable_statuses:
        if found_state.status == JobStatus.COMPLETED and not force:
            output_error(
                f"Score '{job_id}' is already completed",
                severity="warning",
                hints=["Use --force to resume anyway (will restart from last sheet)."],
            )
            raise typer.Exit(1)
        elif found_state.status == JobStatus.PENDING:
            output_error(
                f"Score '{job_id}' has not been started yet",
                severity="warning",
                hints=["Use 'mzt run' to start the score."],
            )
            raise typer.Exit(1)

    return found_state, found_backend


def _reconstruct_config(
    found_state: CheckpointState,
    config_file: Path | None,
    no_reload: bool,
) -> tuple[JobConfig, bool]:
    """Reconstruct JobConfig using priority fallback with auto-reload.

    Priority order:
    1. Provided --config file (always wins)
    2. Auto-reload from stored config_path (default, if file exists)
    3. Cached config_snapshot (fallback when file gone or --no-reload)
    4. Error (nothing available)

    Args:
        found_state: Job checkpoint state with config_snapshot/config_path.
        config_file: Optional explicit config file path.
        no_reload: If True, skip auto-reload and use cached snapshot.

    Returns:
        Tuple of (config, was_reloaded).

    Raises:
        typer.Exit: If no config source available or loading fails.
    """
    # Priority 1: Use provided config file (always takes precedence)
    if config_file:
        try:
            config = JobConfig.from_yaml(config_file)
            console.print(f"[dim]Using config from: {config_file}[/dim]")
            return config, True
        except Exception as e:
            output_error(
                f"Error loading config file: {e}",
                hints=[
                    f"Check the file with: mzt validate {config_file}",
                    "Ensure the YAML syntax is valid and all required fields are present.",
                ],
            )
            raise typer.Exit(1) from None

    # Priority 2: Auto-reload from stored config_path (unless --no-reload)
    if not no_reload and found_state.config_path:
        config_path = Path(found_state.config_path)
        if config_path.exists():
            try:
                config = JobConfig.from_yaml(config_path)
                console.print(
                    f"[cyan]Config reloaded from:[/cyan] {config_path}"
                )
                return config, True
            except Exception as e:
                output_error(
                    f"Error reloading config: {e}",
                    hints=[
                        f"The stored config path is: {config_path}",
                        "Use --config to provide a corrected config file.",
                        "Use --no-reload to resume with the cached config snapshot.",
                    ],
                )
                raise typer.Exit(1) from None
        else:
            # File doesn't exist — fall through to snapshot
            console.print(
                f"[dim]Config file not found on disk: {config_path}[/dim]"
            )

    # Priority 3: Cached config_snapshot (fallback)
    if found_state.config_snapshot:
        try:
            config = JobConfig.model_validate(found_state.config_snapshot)
            if no_reload:
                console.print("[dim]Using cached config snapshot (--no-reload)[/dim]")
            else:
                console.print("[dim]Using cached config snapshot[/dim]")
            return config, False
        except Exception as e:
            output_error(
                f"Error reconstructing config from snapshot: {e}",
                hints=["Provide a config file with --config flag."],
            )
            raise typer.Exit(1) from None

    output_error(
        "Cannot resume: No config available.",
        hints=[
            "The score state doesn't contain a config snapshot.",
            "Provide a config file with --config flag.",
        ],
    )
    raise typer.Exit(1)


async def _resume_job(
    job_id: str,
    config_file: Path | None,
    force: bool,
    escalation: bool = False,
    no_reload: bool = False,
    self_healing: bool = False,
    auto_confirm: bool = False,
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
