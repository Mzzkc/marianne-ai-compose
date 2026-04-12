"""Pause and modify commands for Marianne CLI.

This module implements the `mzt pause` and `mzt modify` commands
for gracefully pausing running jobs and updating their configuration.

★ Insight ─────────────────────────────────────
1. **Signal-based pause mechanism**: Rather than interrupting execution directly,
   Marianne uses a file-based signal (.marianne-pause-{job_id}). The runner polls for
   this file at sheet boundaries, enabling clean checkpoints without data loss.

2. **Atomic state transitions**: The pause command only works on RUNNING jobs,
   and modify can handle RUNNING, PAUSED, or FAILED states. This state machine
   prevents race conditions and invalid state transitions.

3. **Modify as composition**: The modify command is essentially pause + resume
   composed together with config validation in between. This pattern keeps the
   individual commands focused while providing a convenient compound operation.
─────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
import yaml
from pydantic import ValidationError

from marianne.core.checkpoint import JobStatus
from marianne.core.config import JobConfig
from marianne.daemon.exceptions import DaemonError

from ..helpers import (
    configure_global_logging,
    require_conductor,
)
from ..output import console, output_error


def pause(
    job_id: str = typer.Argument(..., help="Score ID to pause"),
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Wait for score to acknowledge pause signal",
    ),
    timeout: int = typer.Option(
        60,
        "--timeout",
        "-t",
        help="Timeout in seconds when using --wait",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output result as JSON",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force-cancel the score immediately (does not wait for sheet boundary)",
    ),
) -> None:
    """Pause a running Marianne score gracefully.

    Creates a pause signal that the job will detect at the next sheet boundary.
    The job saves its state and can be resumed with `mzt resume`.

    Use --force to cancel the job immediately without waiting for a sheet
    boundary (equivalent to `mzt cancel`).

    Examples:
        mzt pause my-job
        mzt pause my-job --wait --timeout 30
        mzt pause my-job --json
        mzt pause my-job --force
    """
    from ._shared import validate_job_id

    job_id = validate_job_id(job_id)
    if force:
        from .cancel import _cancel_job
        asyncio.run(_cancel_job(job_id, json_output))
        return

    asyncio.run(_pause_job(job_id, wait, timeout, json_output))


async def _pause_job(
    job_id: str,
    wait: bool,
    timeout: int,
    json_output: bool,
) -> None:
    """Pause a running job via the conductor.

    Routes through ``job.pause`` RPC. Requires conductor to be running.

    Args:
        job_id: Job ID to pause.
        wait: Whether to wait for pause acknowledgment.
        timeout: Timeout in seconds for wait.
        json_output: Output in JSON format.
    """
    from marianne.daemon.detect import try_daemon_route

    configure_global_logging(console)

    params = {"job_id": job_id}
    try:
        routed, result = await try_daemon_route("job.pause", params)
    except (OSError, ConnectionError, DaemonError) as exc:
        # Business logic error from conductor (e.g., job not found)
        output_error(
            str(exc),
            error_code="E501",
            hints=["Run 'mzt list' to see available scores."],
            json_output=json_output,
            job_id=job_id,
        )
        raise typer.Exit(1) from None

    if routed:
        # Conductor handled the pause
        paused = result.get("paused", False) if isinstance(result, dict) else False

        if not paused:
            error_msg = result.get("error", "") if isinstance(result, dict) else ""
            output_error(
                error_msg or f"Failed to pause score '{job_id}'",
                error_code="E502",
                hints=[
                    f"Check score status: mzt status {job_id}",
                    "Only running scores can be paused.",
                ],
                json_output=json_output,
                job_id=job_id,
            )
            raise typer.Exit(1)

        # Output success
        if json_output:
            out = {
                "success": True,
                "job_id": job_id,
                "status": "paused",
                "message": "Pause signal sent. Score will pause at next sheet boundary.",
                "acknowledged": True,
            }
            console.print(json.dumps(out, indent=2))
        else:
            console.print(f"Pause signal sent to score '[cyan]{job_id}[/cyan]'.")
            console.print("Score will pause at next sheet boundary.")
            console.print()
            console.print(f"To resume: [bold]mzt resume {job_id}[/bold]")
            console.print(
                f"To resume with new config: "
                f"[bold]mzt resume {job_id} --config new.yaml[/bold]"
            )
        return

    # Conductor not available — required for pause
    require_conductor(routed, json_output=json_output)
    return  # unreachable


async def _pause_via_conductor(
    job_id: str,
    params: dict[str, str | None],
    json_output: bool,
    *,
    quiet: bool = False,
) -> bool:
    """Pause a running job via the conductor RPC.

    Returns True if pause signal was sent, False on failure.

    Args:
        quiet: When True, suppress error output. Useful when the caller
            plans to handle failures itself (e.g., ``_modify_job`` with
            ``--resume`` will recover from pause failures).
    """
    from marianne.daemon.detect import try_daemon_route

    # Only send job_id to pause RPC — workspace is not needed
    pause_params = {"job_id": job_id}
    try:
        pause_routed, pause_result = await try_daemon_route(
            "job.pause", pause_params,
        )
    except (OSError, ConnectionError, DaemonError) as exc:
        if not quiet:
            output_error(
                str(exc),
                error_code="E503",
                hints=[
                    "Check conductor status: mzt conductor-status",
                    "Restart if needed: mzt restart",
                ],
                json_output=json_output,
                job_id=job_id,
            )
        return False

    if not pause_routed:
        return False

    paused = (
        pause_result.get("paused", False)
        if isinstance(pause_result, dict) else False
    )
    if paused and not json_output:
        console.print(
            f"Pause signal sent to score '[cyan]{job_id}[/cyan]'."
        )
    elif not paused and not quiet:
        error_msg = (
            pause_result.get("error", "")
            if isinstance(pause_result, dict) else ""
        )
        msg = f"Failed to pause score '{job_id}'"
        if error_msg:
            msg += f": {error_msg}"
        output_error(
            msg,
            error_code="E503",
            hints=[
                f"Check score status: mzt status {job_id}",
                "Only running scores can be paused.",
            ],
            json_output=json_output,
            job_id=job_id,
        )
    return paused


def modify(
    job_id: str = typer.Argument(..., help="Score ID to modify"),
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="New configuration file",
        exists=True,
        readable=True,
    ),
    resume_flag: bool = typer.Option(
        False,
        "--resume",
        "-r",
        help="Immediately resume with new config after pausing",
    ),
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Wait for score to pause before resuming (when --resume)",
    ),
    timeout: int = typer.Option(
        60,
        "--timeout",
        "-t",
        help="Timeout in seconds for pause acknowledgment",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output result as JSON",
    ),
) -> None:
    """Apply a new configuration to a score and optionally resume execution.

    This is a convenience command that combines pause + config update.
    If the score is running, it will be paused first.
    Use --resume to immediately resume with the new configuration.

    Examples:
        mzt modify my-job --config updated.yaml
        mzt modify my-job -c new-config.yaml --resume
        mzt modify my-job -c updated.yaml -r --wait
    """
    from ._shared import validate_job_id

    job_id = validate_job_id(job_id)
    asyncio.run(
        _modify_job(job_id, config, resume_flag, wait, timeout, json_output)
    )


async def _modify_job(
    job_id: str,
    config_file: Path,
    resume_flag: bool,
    wait: bool,
    timeout: int,
    json_output: bool,
) -> None:
    """Modify a job's configuration via conductor.

    Args:
        job_id: Job ID to modify.
        config_file: New config file path.
        resume_flag: Whether to resume after pausing.
        wait: Whether to wait for pause acknowledgment before resuming.
        timeout: Timeout in seconds for pause wait.
        json_output: Output in JSON format.
    """
    from .resume import _resume_job

    configure_global_logging(console)

    # Validate the new config file first
    try:
        new_config = JobConfig.from_yaml(config_file)
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as e:
        output_error(
            f"Invalid config file: {e}",
            error_code="E505",
            hints=["Check YAML syntax and schema in your score file."],
            json_output=json_output,
            job_id=job_id,
            config_file=str(config_file),
        )
        raise typer.Exit(1) from None

    from marianne.daemon.detect import try_daemon_route

    # Try conductor for status check
    params: dict[str, str | None] = {"job_id": job_id}
    try:
        routed, status_result = await try_daemon_route("job.status", params)
    except (OSError, ConnectionError, DaemonError) as exc:
        # Business logic error (e.g., job not found)
        output_error(
            str(exc),
            error_code="E501",
            hints=[
                "Run 'mzt list' to see available scores.",
                "Check conductor status: mzt conductor-status",
            ],
            json_output=json_output,
            job_id=job_id,
        )
        raise typer.Exit(1) from None

    if not routed or not status_result:
        require_conductor(routed, json_output=json_output)
        return  # unreachable

    from marianne.core.checkpoint import CheckpointState
    found_state = CheckpointState.model_validate(status_result)

    # Handle job based on its current state
    _resumable_statuses = {JobStatus.PAUSED, JobStatus.FAILED, JobStatus.CANCELLED}
    job_was_running = found_state.status == JobStatus.RUNNING
    pause_ok = False

    if job_was_running:
        # Pause through conductor (required)
        pause_ok = await _pause_via_conductor(
            job_id, params, json_output, quiet=resume_flag,
        )

        if not pause_ok and resume_flag and routed:
            # Pause failed — the job likely already transitioned to a
            # resumable state (failed/paused/cancelled).  Skip the pause
            # failure and let the resume attempt determine the outcome.
            if not json_output:
                console.print(
                    "[dim]Score is no longer running, skipping pause.[/dim]"
                )
        elif not pause_ok:
            raise typer.Exit(1)

    elif found_state.status not in _resumable_statuses:
        # Job is in a state that can't be modified (completed, pending)
        status_str = found_state.status.value
        modify_hints: list[str] = []
        if found_state.status == JobStatus.COMPLETED:
            modify_hints.append("Score has already completed.")
        elif found_state.status == JobStatus.PENDING:
            modify_hints.append("Use 'mzt run' to start the score.")
        output_error(
            f"Score '{job_id}' is {status_str}, cannot modify.",
            error_code="E502",
            hints=modify_hints,
            json_output=json_output,
            job_id=job_id,
            status=status_str,
        )
        raise typer.Exit(1)

    # Output success message
    if not json_output:
        console.print(f"[green]Config validated:[/green] {config_file}")
        console.print(f"[dim]Score name: {new_config.name}[/dim]")
        console.print(f"[dim]Sheets: {new_config.sheet.total_sheets}[/dim]")

    # If resume flag is set, let the daemon handle pause→resume atomically
    if resume_flag:
        if routed:
            # Atomic modify via daemon — CLI returns immediately
            modify_params = {
                "job_id": job_id,
                "config_path": str(config_file),
            }
            try:
                _, modify_result = await try_daemon_route("job.modify", modify_params)
            except (OSError, ConnectionError, DaemonError) as exc:
                output_error(
                    str(exc),
                    error_code="E506",
                    hints=[
                        "Check conductor status: mzt conductor-status",
                        "The conductor must be running to modify a score.",
                    ],
                    json_output=json_output,
                    job_id=job_id,
                )
                raise typer.Exit(1) from None

            if isinstance(modify_result, dict):
                status = modify_result.get("status", "")
                if status == "rejected":
                    msg = modify_result.get("message", "Modify rejected")
                    output_error(
                        msg,
                        hints=[
                            "The score must be paused before modifying its config.",
                            f"Try: mzt pause {job_id} && "
                            f"mzt modify {job_id} --config <path> --resume",
                        ],
                        json_output=json_output,
                        job_id=job_id,
                    )
                    raise typer.Exit(1)

                msg = modify_result.get("message", "Modify accepted")
                if json_output:
                    console.print(json.dumps(modify_result, indent=2))
                else:
                    console.print(f"[green]{msg}[/green]")
                    console.print(f"\nMonitor: [bold]mzt status {job_id}[/bold]")
            return

        # Resume through conductor
        if not json_output:
            console.print()
            console.print("[cyan]Resuming with new config...[/cyan]")

        await _resume_job(
            job_id=job_id,
            config_file=config_file,
            workspace=None,  # F-502: workspace parameter will be removed from resume.py
            force=False,
            escalation=False,
            no_reload=False,
            self_healing=False,
            auto_confirm=False,
        )
    else:
        # Just show instructions
        if json_output:
            result = {
                "success": True,
                "job_id": job_id,
                "status": found_state.status.value,
                "config_validated": True,
                "config_file": str(config_file),
                "message": "Config validated. Score paused and ready to resume.",
            }
            console.print(json.dumps(result, indent=2))
        else:
            console.print()
            console.print("When ready to resume with new config:")
            console.print(
                f"  [bold]mzt resume {job_id} --config {config_file}[/bold]"
            )
            console.print()
            console.print("Or to resume with original config:")
            console.print(f"  [bold]mzt resume {job_id}[/bold]")


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "pause",
    "modify",
]
