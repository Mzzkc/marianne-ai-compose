"""Resume command for Mozart CLI.

This module implements the `mozart resume` command which continues
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.panel import Panel
from rich.progress import TaskID

from mozart.core.checkpoint import CheckpointState, JobStatus
from mozart.core.config import JobConfig
from mozart.state import StateBackend

from ..helpers import (
    _find_job_state_direct as require_job_state,
)
from ..helpers import (
    await_early_failure,
    configure_global_logging,
    is_quiet,
    require_conductor,
)
from ..output import console, format_duration
from ._shared import (
    create_progress_bar,
    handle_job_completion,
    setup_all,
)


@dataclass
class ResumeContext:
    """Bundled parameters for direct resume execution."""

    job_id: str
    config_file: Path | None
    workspace: Path | None
    force: bool
    escalation: bool = False
    no_reload: bool = False
    self_healing: bool = False
    auto_confirm: bool = False

_logger = logging.getLogger(__name__)


def resume(
    job_id: str = typer.Argument(..., help="Job ID to resume"),
    config_file: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file (optional if config_snapshot exists in state)",
        exists=True,
        readable=True,
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory to search for job state (debug override)",
        hidden=True,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force resume even if job appears completed",
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
        "By default, Mozart reloads from the original config path if the file exists.",
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
    """Resume a paused or failed job.

    Loads the job state and continues execution from where it left off.
    By default, Mozart auto-reloads config from the original YAML file
    if it still exists on disk. Use --no-reload to use the cached snapshot.

    Examples:
        mozart resume my-job
        mozart resume my-job --config job.yaml
        mozart resume my-job --no-reload  # Use cached config snapshot
    """
    asyncio.run(
        _resume_job(
            job_id, config_file, workspace, force, escalation,
            no_reload, self_healing, yes
        )
    )


async def _find_job_state(
    job_id: str,
    workspace: Path | None,
    force: bool,
) -> tuple[CheckpointState, StateBackend]:
    """Find and validate job state from available backends.

    Uses require_job_state for the find+error pattern, then validates
    that the job is in a resumable status.

    Args:
        job_id: Job ID to find.
        workspace: Optional workspace directory to search.
        force: Allow resuming completed jobs.

    Returns:
        Tuple of (found_state, found_backend).

    Raises:
        typer.Exit: If job not found or not in resumable state.
    """
    found_state, found_backend = await require_job_state(job_id, workspace)

    # Check if job is in a resumable state
    resumable_statuses = {
        JobStatus.PAUSED, JobStatus.FAILED, JobStatus.RUNNING, JobStatus.CANCELLED,
    }
    if found_state.status not in resumable_statuses:
        if found_state.status == JobStatus.COMPLETED and not force:
            console.print(
                f"[yellow]Job '{job_id}' is already completed.[/yellow]"
            )
            console.print(
                "[dim]Use --force to resume anyway (will restart from last sheet).[/dim]"
            )
            raise typer.Exit(1)
        elif found_state.status == JobStatus.PENDING:
            console.print(
                f"[yellow]Job '{job_id}' has not been started yet.[/yellow]"
            )
            console.print("[dim]Use 'mozart run' to start the job.[/dim]")
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
            console.print(f"[red]Error loading config file:[/red] {e}")
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
                # Report changes if snapshot exists for comparison
                if found_state.config_snapshot:
                    _report_config_changes(found_state.config_snapshot, config)
                return config, True
            except Exception as e:
                console.print(f"[red]Error reloading config:[/red] {e}")
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
            console.print(f"[red]Error reconstructing config from snapshot:[/red] {e}")
            console.print(
                "[dim]Hint: Provide a config file with --config flag.[/dim]"
            )
            raise typer.Exit(1) from None

    console.print(
        "[red]Cannot resume: No config available.[/red]\n"
        "The job state doesn't contain a config snapshot.\n"
        "Please provide a config file with --config flag."
    )
    raise typer.Exit(1)


def _report_config_changes(
    old_snapshot: dict[str, Any],
    new_config: JobConfig,
) -> None:
    """Report config changes between cached snapshot and reloaded config."""
    new_snapshot = new_config.model_dump(mode="json")
    changed_sections: list[str] = []
    for key in set(old_snapshot) | set(new_snapshot):
        if old_snapshot.get(key) != new_snapshot.get(key):
            changed_sections.append(key)
    if changed_sections:
        console.print(
            f"[dim]Config changed: {len(changed_sections)} section(s) updated "
            f"({', '.join(sorted(changed_sections))})[/dim]"
        )


async def _resume_job(
    job_id: str,
    config_file: Path | None,
    workspace: Path | None,
    force: bool,
    escalation: bool = False,
    no_reload: bool = False,
    self_healing: bool = False,
    auto_confirm: bool = False,
) -> None:
    """Resume a paused or failed job.

    Routes through the conductor by default. The conductor's
    ``JobManager.resume_job()`` handles the full execution lifecycle.
    Falls back to direct execution only with explicit --workspace.

    Args:
        job_id: Job ID to resume.
        config_file: Optional path to config file.
        workspace: Optional workspace directory to search.
        force: Force resume even if job appears completed.
        escalation: Enable human-in-the-loop escalation for low-confidence sheets.
        no_reload: If True, skip auto-reload and use cached config snapshot.
        self_healing: Enable automatic diagnosis and remediation.
        auto_confirm: Auto-confirm suggested fixes.
    """
    from mozart.daemon.detect import try_daemon_route

    configure_global_logging(console)

    # Try conductor first (unless workspace override forces direct execution)
    ws_str = str(workspace) if workspace else None
    params = {"job_id": job_id, "workspace": ws_str}
    try:
        routed, result = await try_daemon_route("job.resume", params)
    except Exception as exc:
        # Business logic error from conductor (e.g., job not found)
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from None

    if routed:
        # Conductor handled the resume
        if isinstance(result, dict):
            status = result.get("status", "unknown")
            message = result.get("message", "")
            if status == "accepted":
                # Poll briefly to catch early failures
                job_id_result = result.get("job_id", job_id)
                early = await await_early_failure(job_id_result)
                early_status = early.get("status", "") if isinstance(early, dict) else ""
                if early_status in ("failed", "cancelled"):
                    err = early.get("error_message", "") if isinstance(early, dict) else ""
                    console.print(f"[red]Job failed after resume:[/red] {job_id_result}")
                    if err:
                        console.print(f"  {err}")
                    console.print(
                        f"\n[dim]Full details:[/dim] mozart diagnose {job_id_result}"
                    )
                    raise typer.Exit(1)

                console.print(
                    f"[green]Resume accepted for job '[cyan]{job_id}[/cyan]'.[/green]"
                )
                if message:
                    console.print(f"[dim]{message}[/dim]")
                console.print(
                    f"\nMonitor progress: [bold]mozart status {job_id}[/bold]"
                )
                return
            else:
                console.print(
                    f"[red]Error:[/red] {message or f'Resume rejected for {job_id!r}'}"
                )
                raise typer.Exit(1)
        return

    # Conductor not available
    if workspace is None:
        # No workspace override — conductor is required
        require_conductor(routed)
        return  # unreachable

    # Fallback to direct execution with workspace override
    ctx = ResumeContext(
        job_id=job_id,
        config_file=config_file,
        workspace=workspace,
        force=force,
        escalation=escalation,
        no_reload=no_reload,
        self_healing=self_healing,
        auto_confirm=auto_confirm,
    )
    await _resume_job_direct(ctx)


async def _resume_job_direct(ctx: ResumeContext) -> None:
    """Direct resume execution (fallback when conductor is unavailable).

    This is the original resume logic, used when --workspace is explicitly
    provided as a debug override.
    """
    from mozart.execution.runner import FatalError, GracefulShutdownError, JobRunner

    # Phase 1: Find and validate job state
    found_state, found_backend = await _find_job_state(ctx.job_id, ctx.workspace, ctx.force)

    # Phase 2: Reconstruct config
    config, config_was_reloaded = _reconstruct_config(
        found_state, ctx.config_file, ctx.no_reload
    )

    # Update config_snapshot in state if config was reloaded
    if config_was_reloaded:
        found_state.config_snapshot = config.model_dump(mode="json")
        console.print("[dim]Updated cached config snapshot[/dim]")

    # Calculate resume point
    resume_sheet = found_state.last_completed_sheet + 1
    if resume_sheet > found_state.total_sheets:
        if ctx.force:
            # For force resume, restart from last sheet
            resume_sheet = found_state.total_sheets
            console.print(
                f"[yellow]Job was completed. Force restarting sheet {resume_sheet}.[/yellow]"
            )
        else:
            console.print("[green]Job is already fully completed.[/green]")
            return

    # Display resume info
    console.print(Panel(
        f"[bold]{config.name}[/bold]\n"
        f"Status: {found_state.status.value}\n"
        f"Progress: {found_state.last_completed_sheet}/{found_state.total_sheets} sheets\n"
        f"Resuming from sheet: {resume_sheet}",
        title="Resume Job",
    ))

    # Reset job status to RUNNING for resume
    found_state.status = JobStatus.RUNNING
    found_state.error_message = None  # Clear previous error
    await found_backend.save(found_state)

    # Phase 3: Setup backends and features for execution (shared with run.py)
    components = setup_all(
        config, escalation=ctx.escalation, console=console,
    )
    backend = components.backend
    outcome_store = components.outcome_store
    global_learning_store = components.global_learning_store
    notification_manager = components.notification_manager
    escalation_handler = components.escalation_handler
    grounding_engine = components.grounding_engine

    # Create progress bar for sheet tracking
    progress = create_progress_bar(console=console)

    # Progress callback to update the progress bar
    progress_task_id: TaskID | None = None

    def update_progress(completed: int, total: int, eta_seconds: float | None) -> None:
        """Update progress bar with current sheet progress."""
        nonlocal progress_task_id
        if progress_task_id is not None:
            eta_str = format_duration(eta_seconds) if eta_seconds else "calculating..."
            progress.update(
                progress_task_id,
                completed=completed,
                total=total,
                eta=eta_str,
            )

    # Create runner context with all optional components
    from mozart.execution.runner import RunnerContext
    runner_context = RunnerContext(
        console=console,
        outcome_store=outcome_store,
        escalation_handler=escalation_handler,
        progress_callback=update_progress,
        global_learning_store=global_learning_store,
        grounding_engine=grounding_engine,
        self_healing_enabled=ctx.self_healing,
        self_healing_auto_confirm=ctx.auto_confirm,
    )

    # Create runner with progress callback
    runner = JobRunner(
        config=config,
        backend=backend,
        state_backend=found_backend,
        context=runner_context,
    )

    try:
        # Send job resume notification (use job_start event)
        if notification_manager:
            remaining_sheets = found_state.total_sheets - found_state.last_completed_sheet
            await notification_manager.notify_job_start(
                job_id=ctx.job_id,
                job_name=config.name,
                total_sheets=remaining_sheets,
            )

        # Start progress display
        progress.start()
        progress_task_id = progress.add_task(
            f"[cyan]{config.name}[/cyan] (resuming)",
            total=found_state.total_sheets,
            completed=found_state.last_completed_sheet,
            eta="calculating...",
        )

        # Resume from the next sheet
        if not is_quiet():
            console.print(f"\n[green]Resuming from sheet {resume_sheet}...[/green]")
        state, summary = await runner.run(
            start_sheet=resume_sheet,
            config_path=str(ctx.config_file) if ctx.config_file else found_state.config_path,
        )

        # Stop progress and show final state
        progress.stop()

        await handle_job_completion(
            state=state,
            summary=summary,
            notification_manager=notification_manager,
            job_id=ctx.job_id,
            job_name=config.name,
            console=console,
        )

    except GracefulShutdownError:
        # Graceful shutdown already saved state and printed resume hint
        progress.stop()
        console.print("[yellow]Job paused. Exiting gracefully.[/yellow]")
        raise typer.Exit(0) from None

    except FatalError as e:
        progress.stop()
        console.print(f"[red]Fatal error: {e}[/red]")

        # Send failure notification (must not mask the original error)
        if notification_manager:
            try:
                await notification_manager.notify_job_failed(
                    job_id=ctx.job_id,
                    job_name=config.name,
                    error_message=str(e),
                )
            except Exception:
                _logger.debug("Notification failed during error handling", exc_info=True)

        raise typer.Exit(1) from None

    finally:
        # Ensure progress is stopped
        if progress.live.is_started:
            progress.stop()

        if notification_manager:
            try:
                await notification_manager.close()
            except OSError:
                _logger.debug("notification manager close failed", exc_info=True)


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "resume",
]
