"""Watch command for Marianne CLI (#352 increment 3).

``mzt watch SCORE [SHEET]`` — live-tail musician output as it streams
from the conductor. Shows what each LLM is producing while it works,
credential-redacted, with bounded memory (per-sheet ring buffers on the
conductor side; a slow terminal drops lines with a ``[dropped]`` marker
rather than ever slowing the conductor down).

The stream is a live view: it starts with the ring's retained recent
lines, then follows new output until Ctrl-C. Conductor restarts clear
the rings — persisted evidence lives in each sheet's recorded output
tails (``mzt diagnose``).
"""

from __future__ import annotations

import asyncio

import typer

from marianne.daemon.exceptions import DaemonError, DaemonNotRunningError

from ..helpers import configure_global_logging
from ..output import console, output_error


def watch(
    job_id: str = typer.Argument(
        ...,
        help="Score ID to tail (see `mzt status`)",
    ),
    sheet_num: int | None = typer.Argument(
        None,
        help="Sheet number to tail. Omit to tail ALL sheets "
        "(lines arrive tagged [s<sheet>]).",
    ),
) -> None:
    """Live-tail musician output for a running score.

    Examples:
        mzt watch my-score        # All sheets, lines tagged [s<n>]
        mzt watch my-score 3      # Only sheet 3
    """
    try:
        asyncio.run(_watch(job_id=job_id, sheet_num=sheet_num))
    except KeyboardInterrupt:
        console.print("\n[dim]watch stopped[/dim]")


async def _watch(*, job_id: str, sheet_num: int | None) -> None:
    from marianne.daemon.detect import _resolve_socket_path
    from marianne.daemon.ipc.client import DaemonClient

    configure_global_logging(console)

    client = DaemonClient(_resolve_socket_path(None))
    params: dict[str, object] = {"job_id": job_id}
    if sheet_num is not None:
        params["sheet_num"] = sheet_num

    target = f"{job_id}" if sheet_num is None else f"{job_id} sheet {sheet_num}"
    console.print(f"[dim]watching {target} — Ctrl-C to stop[/dim]")

    try:
        async for notification in client.stream("job.output.stream", params):
            line = notification.get("line")
            if line is not None:
                console.print(line, markup=False, highlight=False)
    except DaemonNotRunningError:
        output_error(
            "Marianne conductor is not running",
            hints=[
                "Start the conductor: mzt start",
                "Check status: mzt conductor-status",
            ],
        )
        raise typer.Exit(1) from None
    except (OSError, ConnectionError, DaemonError) as exc:
        output_error(
            str(exc),
            hints=["Check conductor status: mzt conductor-status"],
        )
        raise typer.Exit(1) from None
    finally:
        await client.close()
