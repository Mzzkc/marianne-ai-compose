"""Conductor commands — ``mzt start/stop/restart/conductor-status``.

These commands consolidate
all daemon lifecycle management into the main ``marianne`` CLI.
The core logic lives in ``marianne.daemon.process`` (shared functions);
this module provides thin Typer command wrappers.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..output import output_error


def start(
    config_file: Path | None = typer.Option(None, "--config", "-c", help="YAML config file"),
    foreground: bool = typer.Option(False, "--foreground", "-f", help="Run in foreground"),
    log_level: str = typer.Option("info", "--log-level", "-l", help="Log level"),
    profile: str | None = typer.Option(
        None, "--profile", "-p",
        help="Daemon operational profile (dev, intensive, minimal). "
        "Overrides config file defaults.",
    ),
) -> None:
    """Start the Marianne conductor."""
    from marianne.daemon.clone import get_clone_name, is_clone_active
    from marianne.daemon.process import start_conductor

    start_conductor(
        config_file=config_file,
        foreground=foreground,
        log_level=log_level,
        profile=profile,
        clone_name=get_clone_name() if is_clone_active() else None,
    )


def stop(
    pid_file: Path | None = typer.Option(None, "--pid-file", help="PID file path"),
    force: bool = typer.Option(False, "--force", help="Send SIGKILL instead of SIGTERM"),
) -> None:
    """Stop the Marianne conductor.

    When jobs are actively running, warns and asks for confirmation.
    Use --force to skip the safety check and send SIGKILL.
    """
    from marianne.daemon.clone import is_clone_active
    from marianne.daemon.process import stop_conductor

    socket_path: Path | None = None
    if is_clone_active() and pid_file is None:
        from marianne.daemon.clone import get_clone_name, resolve_clone_paths

        clone = resolve_clone_paths(get_clone_name())
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
        help="Daemon operational profile (dev, intensive, minimal). "
        "Overrides config file defaults.",
    ),
) -> None:
    """Restart the Marianne conductor (stop + start)."""
    from marianne.daemon.clone import get_clone_name, is_clone_active
    from marianne.daemon.process import (
        start_conductor,
        stop_conductor,
        wait_for_conductor_exit,
    )

    # When clone is active, redirect PID file to clone's PID
    clone_name: str | None = None
    if is_clone_active():
        from marianne.daemon.clone import resolve_clone_paths

        clone_name = get_clone_name()
        if pid_file is None:
            pid_file = resolve_clone_paths(clone_name).pid_file

    # Stop (ignore exit if not running)
    try:
        stop_conductor(pid_file=pid_file)
    except SystemExit:
        pass

    # Wait for old process to fully exit before starting the new one.
    # Without this, start_conductor sees the dying process and says
    # "already running" (race condition).
    if not wait_for_conductor_exit(pid_file, timeout=30.0):
        output_error(
            "Old conductor did not exit within 30 seconds.",
            hints=["Try 'mzt stop --force' to send SIGKILL."],
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

    get_conductor_status(pid_file=pid_file, socket_path=socket_path)
