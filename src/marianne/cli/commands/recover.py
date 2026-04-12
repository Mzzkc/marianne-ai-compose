"""Recover command for Marianne CLI.

This module implements the hidden `mzt recover` command for recovering
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

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetStatus
from marianne.core.config import JobConfig
from marianne.core.constants import SHEET_NUM_KEY
from marianne.execution.validation import ValidationEngine

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
    from_sheet: int | None = typer.Option(
        None,
        "--from-sheet",
        "-f",
        help="Reset all FAILED sheets >= this number to PENDING (cascade recovery)",
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
    - A cascade failure wiped out downstream sheets after one failure

    Examples:
        mzt recover my-job                    # Recover all failed sheets
        mzt recover my-job --sheet 6         # Recover specific sheet
        mzt recover my-job --dry-run         # Check without modifying
        mzt recover my-job --from-sheet 211  # Reset cascade from sheet 211+
    """
    from ._shared import validate_job_id

    job_id = validate_job_id(job_id)

    if from_sheet is not None:
        asyncio.run(_recover_cascade(job_id, from_sheet, dry_run))
        return

    asyncio.run(_recover_job(job_id, sheet, dry_run))


async def _recover_cascade(
    job_id: str,
    from_sheet: int,
    dry_run: bool,
) -> None:
    """Reset cascaded failures from a specific sheet onward.

    Reads the checkpoint from the conductor registry DB, resets all
    FAILED sheets >= from_sheet to PENDING, clears their error data,
    and sets the job to PAUSED for resume.

    Requires the conductor to be stopped (writes to DB directly).
    """
    import json
    import shutil
    import sqlite3

    configure_global_logging(console)

    db_path = Path("~/.marianne/daemon-state.db").expanduser()
    if not db_path.exists():
        output_error(
            "Conductor registry DB not found",
            hints=["Start the conductor at least once: mzt start"],
        )
        raise typer.Exit(1)

    # Load checkpoint
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT checkpoint_json FROM jobs WHERE job_id=?", (job_id,))
    row = cur.fetchone()

    if not row or not row[0]:
        conn.close()
        output_error(
            f"No checkpoint found for score '{job_id}'",
            hints=["Run 'mzt list -a' to see available scores."],
        )
        raise typer.Exit(1)

    checkpoint = json.loads(row[0])
    sheets = checkpoint.get("sheets", {})

    # Count before
    before: dict[str, int] = {}
    for sdata in sheets.values():
        st = sdata.get("status", "pending")
        before[st] = before.get(st, 0) + 1

    # Reset
    reset_count = 0
    for snum_str, sdata in sheets.items():
        snum = int(snum_str)
        if snum >= from_sheet and sdata.get("status") == "failed":
            sdata["status"] = "pending"
            sdata.pop("error_message", None)
            sdata.pop("error_code", None)
            sdata.pop("completed_at", None)
            # Reset retry and completion budgets so the baton gives these
            # sheets a fresh attempt. Without this, sheets come back as
            # PENDING with exhausted budgets and immediately re-fail on
            # the first partial validation result.
            sdata["normal_attempts"] = 0
            sdata["completion_attempts"] = 0
            sdata["attempt_count"] = 0
            sdata["healing_attempts"] = 0
            reset_count += 1

    # Count after
    after: dict[str, int] = {}
    for sdata in sheets.values():
        st = sdata.get("status", "pending")
        after[st] = after.get(st, 0) + 1

    console.print(Panel(
        f"[bold]Cascade Recovery: {job_id}[/bold]\n"
        f"Reset sheets >= {from_sheet} from FAILED to PENDING\n\n"
        f"Before: {dict(sorted(before.items()))}\n"
        f"After:  {dict(sorted(after.items()))}\n\n"
        f"Reset: {reset_count} sheet(s)\n"
        f"Dry run: {dry_run}",
        title="Recovery",
    ))

    if dry_run:
        conn.close()
        console.print("\n[yellow]Dry run — no changes made[/yellow]")
        return

    if reset_count == 0:
        conn.close()
        console.print("\n[yellow]No FAILED sheets found >= {from_sheet}[/yellow]")
        return

    # Backup
    backup = db_path.with_suffix(".db.bak")
    shutil.copy2(db_path, backup)
    console.print(f"Backup: {backup}")

    # Set job status to paused for clean resume
    checkpoint["status"] = "paused"

    # Save
    checkpoint_json = json.dumps(checkpoint)
    cur.execute(
        "UPDATE jobs SET checkpoint_json=?, status='paused' WHERE job_id=?",
        (checkpoint_json, job_id),
    )
    conn.commit()
    conn.close()

    console.print(f"\n[green]Reset {reset_count} sheet(s). Resume with:[/green]")
    console.print(f"  [bold]mzt resume {job_id}[/bold]")


async def _recover_job(
    job_id: str,
    sheet_num: int | None,
    dry_run: bool,
) -> None:
    """Recover sheets by running validations without re-executing.

    Routes through the conductor to locate job state. Requires conductor
    to be running.
    """
    configure_global_logging(console)

    # Route through conductor
    from marianne.daemon.detect import try_daemon_route
    from marianne.daemon.exceptions import DaemonError, JobSubmissionError

    params = {
        "job_id": job_id, "workspace": None,
        SHEET_NUM_KEY: sheet_num, "dry_run": dry_run,
    }
    try:
        routed, result = await try_daemon_route("job.recover", params)
    except JobSubmissionError as err:
        output_error(
            f"Score not found: {job_id}",
            hints=["Run 'mzt list' to see available scores."],
        )
        raise typer.Exit(1) from err
    except DaemonError as err:
        output_error(
            str(err),
            hints=["Restart the conductor: mzt restart"],
        )
        raise typer.Exit(1) from None

    state: CheckpointState | None = None

    if routed and result:
        state_data = result.get("state")
        if state_data:
            state = CheckpointState.model_validate(state_data)
    else:
        # Conductor not available - require it
        require_conductor(routed)
        return

    if state is None:
        output_error(
            f"Score not found: {job_id}",
            hints=["Run 'mzt list' to see available scores."],
        )
        raise typer.Exit(1)

    # Reconstruct config from snapshot
    if not state.config_snapshot:
        output_error(
            "No config snapshot in state — cannot run validations",
            hints=["The score may need to be re-run with 'mzt run'."],
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

    # Classify failed sheets: cascaded (dependency) vs real (executed)
    cascaded: list[int] = []
    real_failures: list[int] = []
    for snum in sorted(sheets_to_check):
        ss = state.sheets[snum]
        err_msg = ss.error_message or ""
        is_cascade = err_msg.startswith("Dependency failed:")
        if is_cascade:
            cascaded.append(snum)
        else:
            real_failures.append(snum)

    total_failed = len(sheets_to_check)
    console.print(Panel(
        f"[bold]Recover Score: {job_id}[/bold]\n"
        f"Failed sheets: {total_failed}\n"
        f"  Cascaded (dependency): {len(cascaded)}\n"
        f"  Real failures: {len(real_failures)}\n"
        f"Dry run: {dry_run}",
        title="Recovery",
    ))

    recovered_count = 0
    cascade_reset_count = 0

    # Step 1: Reset cascaded failures to PENDING
    if cascaded:
        console.print(f"\n[bold]Resetting {len(cascaded)} cascaded failure(s) to PENDING...[/bold]")
        for snum in cascaded:
            if not dry_run:
                state.sheets[snum].status = SheetStatus.PENDING
                state.sheets[snum].error_message = None
                state.sheets[snum].error_code = None
                state.sheets[snum].completed_at = None
                cascade_reset_count += 1
            else:
                cascade_reset_count += 1
        action = "Would reset" if dry_run else "Reset"
        console.print(f"  [blue]{action} {cascade_reset_count} sheet(s) to PENDING[/blue]")

    # Step 2: Re-run validations on real failures
    for snum in real_failures:
        console.print(f"\n[bold]Sheet {snum}:[/bold]")
        ss = state.sheets[snum]
        console.print(f"  Error: {ss.error_message or '(none)'}")

        # Create validation engine for this sheet
        user_vars: dict[str, Any] = {
            str(k): v for k, v in config.prompt.variables.items()
        }
        sheet_context: dict[str, Any] = {
            **user_vars,
            SHEET_NUM_KEY: snum,
            "start_item": None,
            "end_item": None,
        }
        validation_engine = ValidationEngine(
            workspace=config.workspace,
            sheet_context=sheet_context,
        )
        vresult = await validation_engine.run_validations(config.validations)

        for vr in vresult.results:
            vstatus = "[green]✓[/green]" if vr.passed else "[red]✗[/red]"
            console.print(f"  {vstatus} {vr.rule.description}")

        if vresult.all_passed:
            console.print("  [green]All validations passed![/green]")
            if not dry_run:
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

    # Save state if not dry run and something changed
    total_recovered = recovered_count + cascade_reset_count
    if not dry_run and total_recovered > 0:
        # Update job status
        all_complete = all(
            s.status == SheetStatus.COMPLETED
            for s in state.sheets.values()
        )
        if all_complete:
            state.status = JobStatus.COMPLETED
        elif state.status == JobStatus.FAILED:
            state.status = JobStatus.PAUSED  # Allow resume

        # Write modifications back to conductor's registry DB
        import sqlite3 as _sqlite3

        db_path = Path("~/.marianne/daemon-state.db").expanduser()
        if db_path.exists():
            conn = _sqlite3.connect(str(db_path))
            checkpoint_json = state.model_dump_json()
            conn.execute(
                "UPDATE jobs SET checkpoint_json=?, status=? WHERE job_id=?",
                (checkpoint_json, state.status.value, job_id),
            )
            conn.commit()
            conn.close()

        console.print(f"\n[green]Recovered {total_recovered} sheet(s):[/green]")
        if cascade_reset_count:
            console.print(f"  {cascade_reset_count} cascaded failure(s) reset to PENDING")
        if recovered_count:
            console.print(f"  {recovered_count} sheet(s) validated and marked completed")
        console.print(f"\nResume with: [bold]mzt resume {job_id}[/bold]")
    elif dry_run:
        console.print("\n[yellow]Dry run complete — no changes made[/yellow]")
        if cascade_reset_count:
            console.print(f"  Would reset {cascade_reset_count} cascaded failure(s)")
        if recovered_count:
            console.print(f"  Would recover {recovered_count} validated sheet(s)")
    else:
        console.print("\n[yellow]No sheets could be recovered[/yellow]")


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "recover",
]
