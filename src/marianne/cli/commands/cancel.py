"""Cancel command for Marianne CLI.

Provides `mzt cancel` to immediately cancel a running job via
asyncio task cancellation. Unlike `pause`, this interrupts mid-sheet
and does not wait for a clean boundary. Use `pause` for graceful
stops; use `cancel` when the job must stop now.
"""

from __future__ import annotations

import asyncio

import typer

from marianne.daemon.exceptions import DaemonError

from ..helpers import configure_global_logging, require_conductor
from ..output import console, output_error, output_json


def cancel(
    job_id: str = typer.Argument(..., help="Score ID to cancel"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output result as JSON",
    ),
) -> None:
    """Cancel a running Marianne score immediately.

    Unlike `pause`, this does not wait for a sheet boundary. The score's
    asyncio task is cancelled, in-progress work is rolled back, and the
    score is marked as CANCELLED. Use `pause` for graceful stops.

    Examples:
        mzt cancel my-job
        mzt cancel my-job --json
    """
    from ._shared import validate_job_id

    job_id = validate_job_id(job_id)
    asyncio.run(_cancel_job(job_id, json_output))


async def _cancel_job(job_id: str, json_output: bool) -> None:
    from marianne.daemon.detect import try_daemon_route

    configure_global_logging(console)

    params = {"job_id": job_id}
    try:
        routed, result = await try_daemon_route("job.cancel", params)
    except (OSError, ConnectionError, DaemonError) as exc:
        output_error(
            str(exc),
            hints=["Check conductor status: mzt conductor-status"],
            json_output=json_output,
        )
        raise typer.Exit(1) from None

    if not routed:
        require_conductor(routed, json_output=json_output)
        return

    cancelled = result.get("cancelled", False)
    if cancelled:
        if json_output:
            output_json({"success": True, "job_id": job_id})
        else:
            console.print(f"[green]Score '{job_id}' cancelled.[/green]")
    else:
        output_error(
            f"Score '{job_id}' not found or already stopped.",
            hints=["Run 'mzt list' to see available scores."],
            json_output=json_output,
        )
        raise typer.Exit(1)
