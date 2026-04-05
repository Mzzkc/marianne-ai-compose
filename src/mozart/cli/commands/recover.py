"""Recover command for Mozart CLI.

This module implements the hidden `mozart recover` command for recovering
sheets that completed work but were incorrectly marked as failed.

★ Insight ─────────────────────────────────────
1. **Non-destructive recovery**: The recover command re-runs validations without
   re-executing the backend. This is useful when work was completed but the
   process failed afterwards (e.g., transient network error after writing files).

2. **State machine transitions**: The command can transition sheets from FAILED
   to COMPLETED, and the job from FAILED to PAUSED. This allows the job to be
   resumed normally after recovery.

3. **Dry-run safety**: The --dry-run flag runs validations without modifying
   state. This lets users preview what would be recovered before committing.
─────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import typer
from rich.panel import Panel

from mozart.core.checkpoint import CheckpointState, JobStatus, SheetStatus
from mozart.core.config import JobConfig
from mozart.execution.validation import ValidationEngine

from ..helpers import configure_global_logging, require_conductor
from ..output import console, output_error


def recover(
    job_id: str = typer.Argument(..., help="Score ID to recover"),
    sheet: int | None = typer.Option(
        None,
        "--sheet",
        "-s",
        help="Specific sheet number to recover (default: all failed sheets)",
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory containing score state (debug override)",
        hidden=True,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Check validations without modifying state",
    ),
) -> None:
    """Recover sheets that completed work but were incorrectly marked as failed.

    This command runs validations for failed sheets without re-executing them.
    If validations pass, the sheet is marked as complete.

    This is useful when:
    - Claude CLI returned a non-zero exit code but the work was done
    - A transient error caused failure after files were created
    - You want to check if a failed sheet actually succeeded

    Examples:
        mozart recover my-job                    # Recover all failed sheets
        mozart recover my-job --sheet 6         # Recover specific sheet
        mozart recover my-job --dry-run         # Check without modifying
    """
    from ._shared import validate_job_id

    job_id = validate_job_id(job_id)
    asyncio.run(_recover_job(job_id, sheet, workspace, dry_run))


async def _recover_job(
    job_id: str,
    sheet_num: int | None,
    workspace: Path | None,
    dry_run: bool,
) -> None:
    """Recover sheets by running validations without re-executing.

    Routes through the conductor to locate job state. Falls back to
    direct filesystem access when --workspace is explicitly provided.
    """
    configure_global_logging(console)

    # Route through conductor
    from mozart.daemon.detect import try_daemon_route
    from mozart.daemon.exceptions import JobSubmissionError

    ws_str = str(workspace) if workspace else None
    params = {
        "job_id": job_id, "workspace": ws_str,
        "sheet_num": sheet_num, "dry_run": dry_run,
    }
    try:
        routed, result = await try_daemon_route("job.recover", params)
    except JobSubmissionError as err:
        output_error(
            f"Score not found: {job_id}",
            hints=["Run 'mozart list' to see available scores."],
        )
        raise typer.Exit(1) from err

    state: CheckpointState | None = None
    state_backend = None

    if routed and result:
        state_data = result.get("state")
        if state_data:
            state = CheckpointState.model_validate(state_data)
    elif not routed and workspace is not None:
        # Fallback to filesystem with workspace override
        from ..helpers import _find_job_state_direct
        state, state_backend = await _find_job_state_direct(job_id, workspace)
    else:
        require_conductor(routed)
        return

    if state is None:
        output_error(
            f"Score not found: {job_id}",
            hints=["Run 'mozart list' to see available scores."],
        )
        raise typer.Exit(1)

    # Reconstruct config from snapshot
    if not state.config_snapshot:
        output_error(
            "No config snapshot in state — cannot run validations",
            hints=["The score may need to be re-run with 'mozart run'."],
        )
        raise typer.Exit(1)

    config = JobConfig.model_validate(state.config_snapshot)

    # Determine which sheets to check
    sheets_to_check: list[int] = []
    if sheet_num is not None:
        sheets_to_check = [sheet_num]
    else:
        # Find all failed sheets
        for snum, sheet_state in state.sheets.items():
            if sheet_state.status == SheetStatus.FAILED:
                sheets_to_check.append(int(snum))

    if not sheets_to_check:
        console.print("[green]No failed sheets to recover[/green]")
        raise typer.Exit(0)

    console.print(Panel(
        f"[bold]Recover Job: {job_id}[/bold]\n"
        f"Sheets to check: {sheets_to_check}\n"
        f"Dry run: {dry_run}",
        title="Recovery",
    ))

    recovered_count = 0
    for snum in sorted(sheets_to_check):
        console.print(f"\n[bold]Sheet {snum}:[/bold]")

        # Create validation engine for this sheet.
        # Include user-defined prompt.variables so {my_var} works in paths.
        user_vars: dict[str, Any] = {
            str(k): v for k, v in config.prompt.variables.items()
        }
        sheet_context: dict[str, Any] = {
            **user_vars,
            "sheet_num": snum,
            "start_item": None,
            "end_item": None,
        }
        validation_engine = ValidationEngine(
            workspace=config.workspace,
            sheet_context=sheet_context,
        )
        vresult = await validation_engine.run_validations(config.validations)

        # Show results
        for vr in vresult.results:
            vstatus = "[green]✓[/green]" if vr.passed else "[red]✗[/red]"
            console.print(f"  {vstatus} {vr.rule.description}")

        if vresult.all_passed:
            console.print(f"  [green]All {len(vresult.results)} validations passed![/green]")

            if not dry_run:
                # Update state to mark sheet as completed
                state.sheets[snum].status = SheetStatus.COMPLETED
                state.sheets[snum].validation_passed = True
                state.sheets[snum].validation_details = vresult.to_dict_list()
                state.sheets[snum].error_message = None
                state.sheets[snum].error_category = None
                state.sheets[snum].error_code = None

                # Update last_completed_sheet if this extends it
                if snum > state.last_completed_sheet:
                    state.last_completed_sheet = snum

                recovered_count += 1
                console.print("  [blue]→ Marked as completed[/blue]")
            else:
                console.print("  [yellow]→ Would mark as completed (dry-run)[/yellow]")
        else:
            failed_count = len([r for r in vresult.results if not r.passed])
            console.print(
                f"  [red]{failed_count} validation(s) failed - cannot recover[/red]"
            )

    # Save state if not dry run
    if not dry_run and recovered_count > 0:
        # Update job status if all sheets now complete
        all_complete = all(
            s.status == SheetStatus.COMPLETED
            for s in state.sheets.values()
        )
        if all_complete:
            state.status = JobStatus.COMPLETED
        elif state.status == JobStatus.FAILED:
            state.status = JobStatus.PAUSED  # Allow resume

        if state_backend is not None:
            await state_backend.save(state)
        console.print(f"\n[green]Recovered {recovered_count} sheet(s)[/green]")
    elif dry_run:
        console.print("\n[yellow]Dry run complete - no changes made[/yellow]")
    else:
        console.print("\n[yellow]No sheets could be recovered[/yellow]")


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "recover",
]
