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

from marianne.core.config import JobConfig
from marianne.core.constants import SHEET_NUM_KEY
from marianne.execution.validation import ValidationEngine

from ..helpers import configure_global_logging
from ..output import console, output_error


def _get_db_path() -> Path:
    """Return the path to the conductor's registry DB.

    Extracted so tests can monkeypatch it to use a temp DB.
    """
    return Path("~/.marianne/daemon-state.db").expanduser()


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

    db_path = _get_db_path()
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

    # Reset — handle both FAILED and SKIPPED (cascade-skipped) sheets
    reset_count = 0
    for snum_str, sdata in sheets.items():
        snum = int(snum_str)
        status = sdata.get("status")
        if snum >= from_sheet and status in ("failed", "skipped", "dispatched"):
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
            # Reset instrument to primary. A recovered sheet MUST start
            # from the top of the instrument chain, not stay stuck on
            # whatever fallback it died on.
            sdata["current_instrument_index"] = 0
            reset_count += 1

    # Count after
    after: dict[str, int] = {}
    for sdata in sheets.values():
        st = sdata.get("status", "pending")
        after[st] = after.get(st, 0) + 1

    console.print(Panel(
        f"[bold]Cascade Recovery: {job_id}[/bold]\n"
        f"Reset sheets >= {from_sheet} from FAILED/SKIPPED to PENDING\n\n"
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

    Loads the checkpoint directly from the conductor's registry DB.
    This works for all jobs — active, completed, or failed — because
    the DB is the source of truth, not the conductor's in-memory state.
    """
    import sqlite3

    configure_global_logging(console)

    db_path = _get_db_path()
    if not db_path.exists():
        output_error(
            "Conductor registry DB not found",
            hints=["Start the conductor at least once: mzt start"],
        )
        raise typer.Exit(1)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT checkpoint_json FROM jobs WHERE job_id=?", (job_id,),
    ).fetchone()

    if not row or not row[0]:
        conn.close()
        output_error(
            f"Score not found: {job_id}",
            hints=["Run 'mzt list --all' to see available scores."],
        )
        raise typer.Exit(1)

    import json
    import shutil

    checkpoint = json.loads(row[0])
    sheets = checkpoint.get("sheets", {})

    # Determine which sheets to recover
    sheets_to_reset: list[str] = []
    if sheet_num is not None:
        skey = str(sheet_num)
        if skey in sheets and sheets[skey].get("status") in ("failed", "skipped"):
            sheets_to_reset = [skey]
        elif skey not in sheets:
            conn.close()
            output_error(
                f"Sheet {sheet_num} not found in score '{job_id}'",
            )
            raise typer.Exit(1)
    else:
        for skey, sdata in sheets.items():
            if sdata.get("status") in ("failed", "skipped"):
                sheets_to_reset.append(skey)

    if not sheets_to_reset:
        conn.close()
        console.print("[green]No failed sheets to recover[/green]")
        raise typer.Exit(0)

    # Count before
    before: dict[str, int] = {}
    for sdata in sheets.values():
        st = sdata.get("status", "pending")
        before[st] = before.get(st, 0) + 1

    # Try validation-based recovery if config is available
    config_snapshot = checkpoint.get("config_snapshot")
    config_path = checkpoint.get("config_path")
    config: JobConfig | None = None

    if config_snapshot:
        try:
            config = JobConfig.model_validate(config_snapshot)
        except Exception:
            pass
    elif config_path and Path(config_path).exists():
        try:
            config = JobConfig.from_yaml(Path(config_path))
        except Exception:
            pass

    # Reset sheets and optionally validate
    reset_count = 0
    validated_count = 0

    for skey in sorted(sheets_to_reset, key=int):
        snum = int(skey)
        sdata = sheets[skey]

        if config and config.validations:
            # Run validations to check if work was actually done
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

            if vresult.all_passed:
                if not dry_run:
                    sdata["status"] = "completed"
                    sdata.pop("error_message", None)
                    sdata.pop("error_code", None)
                validated_count += 1
                console.print(f"  Sheet {snum}: [green]validations passed → completed[/green]")
                continue
            # Validations failed — fall through to reset

        # No config or validations failed — reset to PENDING for retry
        if not dry_run:
            sdata["status"] = "pending"
            sdata.pop("error_message", None)
            sdata.pop("error_code", None)
            sdata.pop("completed_at", None)
            sdata["normal_attempts"] = 0
            sdata["completion_attempts"] = 0
            sdata["attempt_count"] = 0
            sdata["healing_attempts"] = 0
            sdata["current_instrument_index"] = 0
        reset_count += 1

    # Count after
    after: dict[str, int] = {}
    for sdata in sheets.values():
        st = sdata.get("status", "pending")
        after[st] = after.get(st, 0) + 1

    total_recovered = reset_count + validated_count
    console.print(Panel(
        f"[bold]Recovery: {job_id}[/bold]\n"
        f"Before: {dict(sorted(before.items()))}\n"
        f"After:  {dict(sorted(after.items()))}\n\n"
        f"Validated: {validated_count}, Reset to PENDING: {reset_count}\n"
        f"Dry run: {dry_run}",
        title="Recovery",
    ))

    if dry_run:
        conn.close()
        console.print("\n[yellow]Dry run — no changes made[/yellow]")
        return

    if total_recovered == 0:
        conn.close()
        console.print("\n[yellow]No sheets could be recovered[/yellow]")
        return

    # Backup
    backup = _get_db_path().with_suffix(".db.bak")
    shutil.copy2(_get_db_path(), backup)

    # Update job status
    all_complete = all(
        s.get("status") == "completed" for s in sheets.values()
    )
    if all_complete:
        checkpoint["status"] = "completed"
    elif checkpoint.get("status") == "failed":
        checkpoint["status"] = "paused"

    # Save
    checkpoint_json = json.dumps(checkpoint)
    conn.execute(
        "UPDATE jobs SET checkpoint_json=?, status=? WHERE job_id=?",
        (checkpoint_json, checkpoint["status"], job_id),
    )
    conn.commit()
    conn.close()

    console.print(f"\n[green]Recovered {total_recovered} sheet(s). Resume with:[/green]")
    console.print(f"  [bold]mzt resume {job_id}[/bold]")


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "recover",
]
