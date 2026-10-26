"""Interactive access to the shared conductor remediation engine."""

import asyncio
import json
import sys
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path

import typer

from marianne.cli.helpers import _find_job_state_direct
from marianne.cli.output import console
from marianne.core.checkpoint import JobStatus
from marianne.healing.remediation import RemediationService


def remediate(
    job_id: str = typer.Argument(..., help="Job ID to diagnose and remediate."),
    workspace: Path | None = typer.Option(
        None, "--workspace", "-w", help="Remedy workspace override."
    ),
    sheet: int | None = typer.Option(None, "--sheet", min=1, help="Select one sheet."),
    yes: bool = typer.Option(False, "--yes", help="Approve suggested remedies."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without applying or prompting."),
    json_output: bool = typer.Option(False, "--json", help="Write a machine-readable report."),
) -> None:
    """Diagnose failed sheets and propose or apply fixes using the conductor's remedies."""
    asyncio.run(_remediate_job(job_id, workspace, sheet, yes, dry_run, json_output))


async def _remediate_job(
    job_id: str,
    workspace: Path | None,
    sheet: int | None,
    yes: bool,
    dry_run: bool,
    json_output: bool,
) -> None:
    # The registry owns state; a workspace override can itself be missing
    # (creating it is one of the automatic remedies).
    job, backend = await _find_job_state_direct(job_id, None, json_output=json_output)
    try:
        if not dry_run and job.status not in {
            JobStatus.FAILED,
            JobStatus.COMPLETED,
            JobStatus.CANCELLED,
        }:
            message = (
                "Applying offline remediation requires a terminal job; use --dry-run to inspect."
            )
            if json_output:
                typer.echo(json.dumps({"error": message, "job_id": job_id}))
            else:
                console.print(message, markup=False)
            raise typer.Exit(1)
        snapshot = job.config_snapshot or {}
        effective_workspace = str(workspace or snapshot.get("workspace") or "")
        if not json_output:
            console.print(f"Job {job_id}: {job.status.value}", markup=False)
            console.print(
                f"Recorded attempts: {sum(len(s.attempt_history) for s in job.sheets.values())}",
                markup=False,
            )
        # Prompts belong on stderr so stdout stays valid JSON even when interactive.
        with redirect_stdout(sys.stderr if json_output else sys.stdout):
            result = await RemediationService().run(
                job,
                effective_workspace,
                sheet_filter=sheet,
                auto_confirm=yes,
                dry_run=dry_run,
            )
        if json_output:
            reports = []
            for report in result.reports:
                reports.append(
                    {
                        "sheet_number": report.error_context.sheet_number,
                        "context": report.error_context.get_context_summary(),
                        "diagnoses": [asdict(d) for d in report.diagnoses],
                        "actions_taken": [
                            (name, asdict(action)) for name, action in report.actions_taken
                        ],
                        "actions_skipped": report.actions_skipped,
                        "diagnostic_outputs": report.diagnostic_outputs,
                    }
                )
            typer.echo(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": job.status.value,
                        "reports": reports,
                        "warnings": result.warnings,
                    },
                    default=str,
                )
            )
        else:
            for warning in result.warnings:
                console.print(warning, markup=False)
            if result.empty:
                console.print("No failed sheets selected.")
            for report in result.reports:
                for diagnosis in report.diagnoses:
                    console.print(diagnosis.format_full(), markup=False)
                # The shared formatter describes the conductor's retry action.
                # This offline command only reports remedies and guidance.
                rendered = "\n".join(
                    line
                    for line in report.format(verbose=True).splitlines()
                    if not line.startswith("Result:")
                )
                console.print(rendered, markup=False)
                console.print("Remediation finished. No retry was scheduled.")
    finally:
        await backend.close()
