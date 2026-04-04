"""Clear rate limits command for Mozart CLI.

Provides `mozart clear-rate-limits` to manually clear stale rate limits
on one or all instruments. Useful when a backend's rate limit has expired
but the conductor still caches it, or when an operator wants to force
a retry after a rate limit event.
"""

from __future__ import annotations

import asyncio

import typer

from mozart.daemon.exceptions import DaemonError

from ..helpers import configure_global_logging
from ..output import console, output_error, output_json


def clear_rate_limits(
    instrument: str | None = typer.Option(
        None,
        "--instrument",
        "-i",
        help="Clear rate limit for a specific instrument only (e.g. claude-cli)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output result as JSON",
    ),
) -> None:
    """Clear stale rate limits on instruments.

    When a backend rate limit expires but the conductor still has it cached,
    sheets may stay blocked unnecessarily. This command clears the cached
    limit so dispatch resumes immediately.

    Clears both the rate limit coordinator (used by the scheduler) and
    the baton's per-instrument state (used by the dispatch loop).

    Examples:
        mozart clear-rate-limits                    # Clear all
        mozart clear-rate-limits -i claude-cli      # Clear one instrument
        mozart clear-rate-limits --json             # JSON output
    """
    asyncio.run(_clear_rate_limits(instrument=instrument, json_output=json_output))


async def _clear_rate_limits(
    *,
    instrument: str | None,
    json_output: bool,
) -> None:
    from mozart.daemon.detect import try_daemon_route

    configure_global_logging(console)

    params = {"instrument": instrument}
    try:
        routed, result = await try_daemon_route(
            "daemon.clear_rate_limits", params,
        )
    except (OSError, ConnectionError, DaemonError) as exc:
        output_error(
            str(exc),
            hints=["Check conductor status: mozart conductor-status"],
            json_output=json_output,
        )
        raise typer.Exit(1) from None

    if not routed:
        output_error(
            "Mozart conductor is not running",
            hints=[
                "Start the conductor: mozart start",
                "Check status: mozart conductor-status",
            ],
            json_output=json_output,
        )
        raise typer.Exit(1)

    if json_output:
        output_json(result)
        return

    cleared = result.get("cleared", 0)
    target = instrument or "all instruments"
    if cleared > 0:
        console.print(
            f"[green]Cleared {cleared} rate limit(s) on {target}[/green]",
        )
    else:
        console.print(
            f"[dim]No active rate limits on {target}[/dim]",
        )
