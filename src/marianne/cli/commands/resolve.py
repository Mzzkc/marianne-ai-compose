"""Resolve command for Marianne CLI (#361).

Provides `mzt resolve` — resolve a sheet paused in FERMATA (awaiting a
composer decision after retry exhaustion) without hand-crafting marker
files. The conductor writes the decision marker and consumes it
immediately through the existing fermata poll, so the audit trail
(`consumed/`) and restart safety of the marker mechanism are preserved.
"""

from __future__ import annotations

import asyncio

import typer

from marianne.daemon.exceptions import DaemonError

from ..helpers import configure_global_logging
from ..output import console, output_error, output_json

_DECISION_HELP = (
    "retry (re-run the sheet from scratch), "
    "skip (skip it, continue dependents), "
    "accept (accept the last attempt as success), "
    "fail (fail it, propagate to dependents)"
)


def resolve(
    job_id: str = typer.Argument(
        ...,
        help="Score ID with a sheet paused in FERMATA (see `mzt status`)",
    ),
    sheet_num: int = typer.Argument(
        ...,
        help="Sheet number awaiting the decision",
    ),
    decision: str = typer.Argument(
        ...,
        help=f"Decision: {_DECISION_HELP}",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output result as JSON",
    ),
) -> None:
    """Resolve a sheet paused in FERMATA awaiting a composer decision.

    When escalation is enabled (--escalation or --self-healing), a sheet
    that exhausts its retries pauses in FERMATA instead of failing. This
    command records your decision and the score continues immediately.

    Examples:
        mzt resolve my-score 3 retry     # Re-run sheet 3 from scratch
        mzt resolve my-score 3 accept    # Accept its last attempt
        mzt resolve my-score 3 skip      # Skip it, continue dependents
        mzt resolve my-score 3 fail      # Fail it and its dependents
    """
    asyncio.run(
        _resolve(
            job_id=job_id,
            sheet_num=sheet_num,
            decision=decision,
            json_output=json_output,
        )
    )


async def _resolve(
    *,
    job_id: str,
    sheet_num: int,
    decision: str,
    json_output: bool,
) -> None:
    from marianne.daemon.detect import try_daemon_route

    configure_global_logging(console)

    params = {"job_id": job_id, "sheet_num": sheet_num, "decision": decision}
    try:
        routed, result = await try_daemon_route("job.resolve_escalation", params)
    except (OSError, ConnectionError, DaemonError) as exc:
        output_error(
            str(exc),
            hints=["Check conductor status: mzt conductor-status"],
            json_output=json_output,
        )
        raise typer.Exit(1) from None

    if not routed:
        output_error(
            "Marianne conductor is not running",
            hints=[
                "Start the conductor: mzt start",
                "Check status: mzt conductor-status",
            ],
            json_output=json_output,
        )
        raise typer.Exit(1)

    resolved = bool(result.get("resolved")) if isinstance(result, dict) else False
    message = result.get("message", "") if isinstance(result, dict) else ""

    if json_output:
        output_json({"resolved": resolved, "message": message})
        if not resolved:
            raise typer.Exit(1)
        return

    if resolved:
        console.print(f"[green]✓ {message}[/green]")
    else:
        output_error(
            message or "Resolution rejected",
            hints=[
                "List FERMATA sheets: mzt status <job_id>",
                f"Valid decisions: {_DECISION_HELP}",
            ],
        )
        raise typer.Exit(1)
