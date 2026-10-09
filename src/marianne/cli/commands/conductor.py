"""Conductor commands — ``mzt start/stop/restart/conductor-status``.

These commands consolidate
all daemon lifecycle management into the main ``marianne`` CLI.
The core logic lives in ``marianne.daemon.process`` (shared functions);
this module provides thin Typer command wrappers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..output import output_error

# #408: ``mzt conductor reload`` — sub-app so the conductor namespace can
# grow without flattening more names onto the root CLI.
conductor_app = typer.Typer(
    help="Conductor runtime commands (hot reload).",
    no_args_is_help=True,
)


def start(
    config_file: Path | None = typer.Option(None, "--config", "-c", help="YAML config file"),
    foreground: bool = typer.Option(False, "--foreground", "-f", help="Run in foreground"),
    log_level: str = typer.Option("info", "--log-level", "-l", help="Log level"),
    profile: str | None = typer.Option(
        None, "--profile", "-p",
        help="Conductor operational profile (dev, intensive, minimal). "
        "Overrides config file defaults.",
    ),
    conductor_clone: Annotated[
        str | None,
        typer.Option(
            "--conductor-clone",
            help="Start a clone conductor for safe testing. "
            "Use --conductor-clone= (with equals) for default clone, "
            "or --conductor-clone=NAME for a named clone. "
            "Overrides global --conductor-clone if both are given.",
        ),
    ] = None,
) -> None:
    """Start the Marianne conductor."""
    from marianne.daemon.clone import get_clone_name, is_clone_active, set_clone_name
    from marianne.daemon.process import start_conductor

    # Determine which clone name to use
    clone_to_use: str | None = None
    if conductor_clone is not None:
        # Command-level --conductor-clone overrides global flag
        set_clone_name(conductor_clone)
        clone_to_use = conductor_clone
    elif is_clone_active():
        # Use global --conductor-clone
        clone_to_use = get_clone_name()

    start_conductor(
        config_file=config_file,
        foreground=foreground,
        log_level=log_level,
        profile=profile,
        clone_name=clone_to_use,
    )


def stop(
    pid_file: Path | None = typer.Option(None, "--pid-file", help="PID file path"),
    force: bool = typer.Option(False, "--force", help="Send SIGKILL instead of SIGTERM"),
    conductor_clone: Annotated[
        str | None,
        typer.Option(
            "--conductor-clone",
            help="Stop a clone conductor. "
            "Use --conductor-clone= (with equals) for default clone, "
            "or --conductor-clone=NAME for a named clone. "
            "Overrides global --conductor-clone if both are given.",
        ),
    ] = None,
) -> None:
    """Stop the Marianne conductor.

    When jobs are actively running, warns and asks for confirmation.
    Use --force to skip the safety check and send SIGKILL.
    """
    from marianne.daemon.clone import get_clone_name, is_clone_active, set_clone_name
    from marianne.daemon.process import stop_conductor

    # Determine which clone name to use
    clone_name_to_use: str | None = None
    if conductor_clone is not None:
        # Command-level --conductor-clone overrides global flag
        set_clone_name(conductor_clone)
        clone_name_to_use = conductor_clone
    elif is_clone_active():
        # Use global --conductor-clone
        clone_name_to_use = get_clone_name()

    socket_path: Path | None = None
    if clone_name_to_use is not None and pid_file is None:
        from marianne.daemon.clone import resolve_clone_paths

        clone = resolve_clone_paths(clone_name_to_use)
        pid_file = clone.pid_file
        socket_path = clone.socket

    stop_conductor(pid_file=pid_file, force=force, socket_path=socket_path)


def restart(
    config_file: Path | None = typer.Option(None, "--config", "-c", help="YAML config file"),
    foreground: bool = typer.Option(False, "--foreground", "-f", help="Run in foreground"),
    log_level: str = typer.Option("info", "--log-level", "-l", help="Log level"),
    pid_file: Path | None = typer.Option(None, "--pid-file", help="PID file path"),
    profile: str | None = typer.Option(
        None, "--profile", "-p",
        help="Conductor operational profile (dev, intensive, minimal). "
        "Overrides config file defaults.",
    ),
    conductor_clone: Annotated[
        str | None,
        typer.Option(
            "--conductor-clone",
            help="Restart a clone conductor. "
            "Use --conductor-clone= (with equals) for default clone, "
            "or --conductor-clone=NAME for a named clone. "
            "Overrides global --conductor-clone if both are given.",
        ),
    ] = None,
) -> None:
    """Restart the Marianne conductor after the active-score safety check."""
    from marianne.daemon.clone import get_clone_name, is_clone_active, set_clone_name
    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.process import (
        _pid_alive,
        _read_pid,
        _resolve_live_pid_file,
        start_conductor,
        stop_conductor,
        wait_for_conductor_exit,
    )

    # Determine which clone name to use
    clone_name: str | None = None
    if conductor_clone is not None:
        # Command-level --conductor-clone overrides global flag
        set_clone_name(conductor_clone)
        clone_name = conductor_clone
    elif is_clone_active():
        # Use global --conductor-clone
        clone_name = get_clone_name()

    # When clone is active, redirect PID file to clone's PID
    if clone_name is not None:
        from marianne.daemon.clone import resolve_clone_paths

        if pid_file is None:
            pid_file = resolve_clone_paths(clone_name).pid_file

    def conductor_still_running(path: Path | None) -> bool:
        resolved = (
            path
            if path is not None
            else _resolve_live_pid_file(DaemonConfig().pid_file)
        )
        pid = _read_pid(resolved)
        return pid is not None and _pid_alive(pid)

    # Stop. If no conductor was running, continue into start. If stop refused
    # or failed while a conductor is still alive, do not start a second one.
    try:
        stop_conductor(pid_file=pid_file)
    except (SystemExit, typer.Exit):
        if conductor_still_running(pid_file):
            output_error(
                "Conductor restart aborted before start.",
                hints=[
                    "Pause or finish active scores before restarting.",
                    "Check active scores: mzt status",
                    "Use mzt stop --force only when you accept orphaning active work.",
                ],
            )
            raise typer.Exit(1) from None

    # Wait for old process to fully exit before starting the new one.
    # Without this, start_conductor sees the dying process and says
    # "already running" (race condition).
    if not wait_for_conductor_exit(pid_file, timeout=30.0):
        output_error(
            "Old conductor did not exit within 30 seconds.",
            hints=[
                "Check active scores before retrying: mzt status",
                "Try 'mzt stop --force' only when you accept orphaning active work.",
            ],
        )
        raise typer.Exit(1)

    start_conductor(
        config_file=config_file,
        foreground=foreground,
        log_level=log_level,
        profile=profile,
        clone_name=clone_name,
    )


def conductor_status(
    pid_file: Path | None = typer.Option(None, "--pid-file", help="PID file path"),
    socket_path: Path | None = typer.Option(None, "--socket", help="Unix socket path"),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw daemon.status payload as JSON"
    ),
) -> None:
    """Check Marianne conductor status."""
    from marianne.daemon.clone import get_clone_name, is_clone_active
    from marianne.daemon.process import get_conductor_status

    # When clone is active, redirect to clone PID and socket
    if is_clone_active():
        from marianne.daemon.clone import resolve_clone_paths

        clone_paths = resolve_clone_paths(get_clone_name())
        if pid_file is None:
            pid_file = clone_paths.pid_file
        if socket_path is None:
            socket_path = clone_paths.socket

    get_conductor_status(pid_file=pid_file, socket_path=socket_path, as_json=as_json)


@conductor_app.command("reload")
def conductor_reload(
    socket_path: Path | None = typer.Option(None, "--socket", help="Unix socket path"),
    reason: str = typer.Option("cli", "--reason", help="Trigger reason recorded in the reload log"),
) -> None:
    """Hot-reload conductor config + instrument profiles (#408, GH 408).

    Re-reads the config file and instrument profile directories on the
    RUNNING conductor — same path as SIGHUP — and prints what was
    applied and what was declined (restart-only fields).
    """
    import asyncio
    import json as _json
    from typing import Any

    from marianne.daemon.clone import get_clone_name, is_clone_active
    from marianne.daemon.config import DaemonConfig

    resolved_socket: Path | None = socket_path
    if resolved_socket is None:
        if is_clone_active():
            from marianne.daemon.clone import resolve_clone_paths

            resolved_socket = resolve_clone_paths(get_clone_name()).socket
        else:
            resolved_socket = DaemonConfig().socket.path
            # #227 transitional: a pre-move conductor serves on the legacy socket.
            if not resolved_socket.exists():
                from marianne.daemon.config import LEGACY_SOCKET_PATH

                if LEGACY_SOCKET_PATH.exists():
                    resolved_socket = LEGACY_SOCKET_PATH

    if resolved_socket is None or not resolved_socket.exists():
        output_error(
            f"No conductor socket at {resolved_socket or socket_path} — is the conductor running?",
            hints=["Start it with: mzt start", "Or pass --socket explicitly."],
        )
        raise typer.Exit(1)

    from marianne.daemon.exceptions import DaemonError
    from marianne.daemon.ipc.client import DaemonClient

    async def _reload() -> dict[str, Any]:
        client = DaemonClient(resolved_socket)
        try:
            return await client.reload_config(reason=reason)
        finally:
            await client.close()

    try:
        result = asyncio.run(_reload())
    except (OSError, DaemonError) as exc:
        output_error(
            f"Conductor reload IPC failed: {exc}",
            hints=["Check that the conductor is healthy: mzt conductor-status"],
        )
        raise typer.Exit(1) from None

    if not result.get("success"):
        output_error(
            f"Reload declined — running config unchanged: {result.get('error')}",
            hints=[f"Declined: {result.get('declined')}"],
        )
        raise typer.Exit(1)

    typer.echo(
        f"Config generation {result.get('config_generation')} "
        f"(loaded {result.get('config_loaded_at')})"
    )
    applied = result.get("applied") or []
    declined = result.get("declined") or []
    if applied:
        typer.echo("Applied:")
        for entry in applied:
            typer.echo(f"  + {entry}")
    else:
        typer.echo("Applied: (nothing changed)")
    if declined:
        typer.echo("Declined (restart required, running values kept):")
        for entry in declined:
            typer.echo(f"  - {entry}")
    typer.echo(_json.dumps(result))
