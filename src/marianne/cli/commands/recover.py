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
from pydantic import ValidationError
from rich.panel import Panel

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetStatus
from marianne.core.config import JobConfig
from marianne.core.constants import DAEMON_STATE_DB_PATH, SHEET_NUM_KEY
from marianne.core.logging import get_logger
from marianne.execution.validation import SheetValidationResult, ValidationEngine
from marianne.utils.time import utc_now

from ..helpers import configure_global_logging
from ..output import console, output_error

_logger = get_logger("cli.recover")


def _validated_checkpoint_json(checkpoint: dict[str, Any]) -> str:
    """Validate a (hand-mutated) checkpoint dict and return its canonical JSON (#111).

    ``recover`` mutates the loaded checkpoint dict in place, then writes it back
    to the daemon DB — the (de facto sole) source of truth for job state. Writing
    an unvalidated dict can poison the registry: the conductor crashes on the next
    resume when ``CheckpointState.model_validate`` rejects it. Validating here and
    re-serialising through the model guarantees the written state is loadable and
    canonical (stray/legacy keys dropped). Raises ``ValueError`` on invalid input
    so the caller aborts the write rather than corrupt the source of truth.
    """
    try:
        state = CheckpointState.model_validate(checkpoint)
    except ValidationError as exc:
        raise ValueError(f"refusing to write an invalid checkpoint: {exc}") from exc
    return state.model_dump_json()


def _load_recovery_config(
    config_snapshot: dict[str, Any] | None,
    config_path: str | None,
) -> JobConfig | None:
    """Deserialize the job config for validation-based recovery.

    Returns ``None`` when no config is available or it fails to deserialize.
    A deserialization failure is a real degradation — recovery then proceeds
    WITHOUT validation — so it must leave a diagnostic trail rather than be
    silently swallowed (MN-007 forbids ``except Exception: pass``; #229).
    """
    if config_snapshot:
        try:
            return JobConfig.model_validate(config_snapshot)
        except Exception as exc:
            _logger.warning(
                "recover.config_snapshot_invalid",
                error=str(exc),
                detail="config_snapshot failed to deserialize; "
                "recovery will proceed without validation",
            )
            return None
    if config_path and Path(config_path).exists():
        try:
            return JobConfig.from_yaml(Path(config_path))
        except Exception as exc:
            _logger.warning(
                "recover.config_path_invalid",
                config_path=str(config_path),
                error=str(exc),
                detail="config file failed to load; "
                "recovery will proceed without validation",
            )
            return None
    return None


def _get_db_path() -> Path:
    """Return the path to the conductor's registry DB.

    Extracted so tests can monkeypatch it to use a temp DB.
    """
    return DAEMON_STATE_DB_PATH.expanduser()


def _load_checkpoint_row(conn: Any, job_id: str) -> tuple[str, str | None] | None:
    """Load checkpoint JSON plus registry config path when the DB has one.

    Older test and user databases may only have ``checkpoint_json``. Newer
    conductor registries also persist ``jobs.config_path``. Recovery must use
    that registry path as a fallback when legacy checkpoints lack
    ``config_snapshot``/``config_path``; otherwise validation-based recovery
    cannot prove completed work and unnecessarily resets sheets.
    """
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    }
    if "config_path" in columns:
        row = conn.execute(
            "SELECT checkpoint_json, config_path FROM jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if not row:
            return None
        return row[0], row[1]

    row = conn.execute(
        "SELECT checkpoint_json FROM jobs WHERE job_id=?",
        (job_id,),
    ).fetchone()
    if not row:
        return None
    return row[0], None


def _reset_sheet_data_for_retry(sdata: dict[str, Any]) -> None:
    """Reset a serialized sheet dict to PENDING for a fresh recovery attempt.

    A recovered sheet must start over from the TOP of its instrument chain with
    a full retry/completion budget — not resume stuck on whatever fallback it
    died on (#187) with exhausted budgets (which would immediately re-fail).
    Extracted from the two identical reset sites in ``recover`` so the contract
    is unit-testable (the command-level tests are skipped under the F-532
    conductor-mock debt).
    """
    sdata["status"] = SheetStatus.PENDING.value
    sdata.pop("error_message", None)
    sdata.pop("error_code", None)
    sdata.pop("completed_at", None)
    sdata["normal_attempts"] = 0
    sdata["completion_attempts"] = 0
    sdata["attempt_count"] = 0
    sdata["healing_attempts"] = 0
    # #187: restart from the primary instrument, not the dead fallback.
    sdata["current_instrument_index"] = 0
    # #190: drop the stale fallback record so the status display doesn't show a
    # phantom "(was X: rate_limit)" tag after a clean restart.
    sdata["instrument_fallback_history"] = []
    sdata["fallback_attempts"] = {}


def _has_stale_validation_failure(sdata: dict[str, Any]) -> bool:
    """Return true when a completed sheet still carries failed validation data."""
    if sdata.get("status") != SheetStatus.COMPLETED.value:
        return False
    return (
        sdata.get("validation_passed") is False
        or bool(sdata.get("failed_validations"))
    )


def _mark_sheet_validated_completed(
    sdata: dict[str, Any],
    result: SheetValidationResult,
) -> None:
    """Mark a serialized sheet complete after recovery validations pass.

    Successful recovery is a state repair, not only a status flip. A sheet that
    previously failed may carry stale validation metadata; leaving that metadata
    intact makes status and diagnose render a completed sheet as failed (#391).
    """
    validation_details = result.to_dict_list()
    sdata["status"] = SheetStatus.COMPLETED.value
    sdata["completed_at"] = utc_now().isoformat()
    sdata["exit_code"] = 0
    sdata["validation_passed"] = True
    sdata["validation_details"] = validation_details
    sdata["last_pass_percentage"] = float(result.pass_percentage)
    sdata["passed_validations"] = [
        str(detail.get("description") or detail.get("rule_type") or "validation")
        for detail in validation_details
    ]
    sdata["failed_validations"] = []
    sdata.pop("error_message", None)
    sdata.pop("error_code", None)
    sdata.pop("error_category", None)


def _refresh_checkpoint_progress(checkpoint: dict[str, Any]) -> None:
    """Refresh checkpoint-level progress fields after direct sheet mutation."""
    checkpoint["updated_at"] = utc_now().isoformat()
    sheets = checkpoint.get("sheets", {})
    if not isinstance(sheets, dict):
        return
    completed = sum(
        1 for sdata in sheets.values()
        if isinstance(sdata, dict)
        and sdata.get("status") == SheetStatus.COMPLETED.value
    )
    checkpoint["last_completed_sheet"] = completed
    if completed == checkpoint.get("total_sheets"):
        checkpoint["current_sheet"] = None


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
    row = _load_checkpoint_row(conn, job_id)

    if not row or not row[0]:
        conn.close()
        output_error(
            f"No checkpoint found for score '{job_id}'",
            hints=["Run 'mzt list -a' to see available scores."],
        )
        raise typer.Exit(1)

    checkpoint_json, registry_config_path = row
    checkpoint = json.loads(checkpoint_json)
    if registry_config_path and not checkpoint.get("config_path"):
        checkpoint["config_path"] = registry_config_path
    sheets = checkpoint.get("sheets", {})

    # Count before
    before: dict[str, int] = {}
    for sdata in sheets.values():
        st = sdata.get("status", SheetStatus.PENDING.value)
        before[st] = before.get(st, 0) + 1

    # Reset — handle both FAILED and SKIPPED (cascade-skipped) sheets
    reset_count = 0
    for snum_str, sdata in sheets.items():
        snum = int(snum_str)
        status = sdata.get("status")
        if snum >= from_sheet and status in (
            SheetStatus.FAILED.value,
            SheetStatus.SKIPPED.value,
            SheetStatus.DISPATCHED.value,
        ):
            _reset_sheet_data_for_retry(sdata)
            reset_count += 1

    # Count after
    after: dict[str, int] = {}
    for sdata in sheets.values():
        st = sdata.get("status", SheetStatus.PENDING.value)
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
    checkpoint["status"] = JobStatus.PAUSED.value

    # Save — validate through CheckpointState first so a bad mutation can never
    # poison the source of truth (#111).
    try:
        checkpoint_json = _validated_checkpoint_json(checkpoint)
    except ValueError as exc:
        conn.close()
        output_error(str(exc))
        return
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
    row = _load_checkpoint_row(conn, job_id)

    if not row or not row[0]:
        conn.close()
        output_error(
            f"Score not found: {job_id}",
            hints=["Run 'mzt list --all' to see available scores."],
        )
        raise typer.Exit(1)

    import json
    import shutil

    checkpoint_json, registry_config_path = row
    checkpoint = json.loads(checkpoint_json)
    if registry_config_path and not checkpoint.get("config_path"):
        checkpoint["config_path"] = registry_config_path
    sheets = checkpoint.get("sheets", {})

    # Determine which sheets to recover
    sheets_to_reset: list[str] = []
    if sheet_num is not None:
        skey = str(sheet_num)
        if skey in sheets and sheets[skey].get("status") in (
            SheetStatus.FAILED.value,
            SheetStatus.SKIPPED.value,
        ) or (skey in sheets and _has_stale_validation_failure(sheets[skey])):
            sheets_to_reset = [skey]
        elif skey not in sheets:
            conn.close()
            output_error(
                f"Sheet {sheet_num} not found in score '{job_id}'",
            )
            raise typer.Exit(1)
    else:
        for skey, sdata in sheets.items():
            if sdata.get("status") in (
                SheetStatus.FAILED.value,
                SheetStatus.SKIPPED.value,
            ) or _has_stale_validation_failure(sdata):
                sheets_to_reset.append(skey)

    if not sheets_to_reset:
        conn.close()
        console.print("[green]No failed sheets to recover[/green]")
        raise typer.Exit(0)

    # Count before
    before: dict[str, int] = {}
    for sdata in sheets.values():
        st = sdata.get("status", SheetStatus.PENDING.value)
        before[st] = before.get(st, 0) + 1

    # Try validation-based recovery if config is available
    config_snapshot = checkpoint.get("config_snapshot")
    config_path = checkpoint.get("config_path")
    config: JobConfig | None = _load_recovery_config(config_snapshot, config_path)

    # Reset sheets and optionally validate
    reset_count = 0
    validated_count = 0

    for skey in sorted(sheets_to_reset, key=int):
        snum = int(skey)
        sdata = sheets[skey]
        stale_completed = _has_stale_validation_failure(sdata)

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
                    _mark_sheet_validated_completed(sdata, vresult)
                validated_count += 1
                console.print(f"  Sheet {snum}: [green]validations passed → completed[/green]")
                continue
            if stale_completed:
                console.print(
                    f"  Sheet {snum}: [yellow]validations still failing — left unchanged[/yellow]"
                )
                continue
            # Validations failed — fall through to reset

        if stale_completed:
            console.print(
                f"  Sheet {snum}: [yellow]no validation proof available — left unchanged[/yellow]"
            )
            continue

        # No config or validations failed — reset to PENDING for retry
        if not dry_run:
            _reset_sheet_data_for_retry(sdata)
        reset_count += 1

    # Count after
    after: dict[str, int] = {}
    for sdata in sheets.values():
        st = sdata.get("status", SheetStatus.PENDING.value)
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
    _refresh_checkpoint_progress(checkpoint)
    all_complete = all(
        s.get("status") == SheetStatus.COMPLETED.value for s in sheets.values()
    )
    if all_complete:
        checkpoint["status"] = JobStatus.COMPLETED.value
    elif checkpoint.get("status") == JobStatus.FAILED.value:
        checkpoint["status"] = JobStatus.PAUSED.value

    # Save — validate through CheckpointState first so a bad mutation can never
    # poison the source of truth (#111).
    try:
        checkpoint_json = _validated_checkpoint_json(checkpoint)
    except ValueError as exc:
        conn.close()
        output_error(str(exc))
        return
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
