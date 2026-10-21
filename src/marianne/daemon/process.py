"""Marianne daemon process.

Long-running orchestration service that manages job execution,
resources, and cross-job coordination.  Provides core functions
for starting, stopping, and checking conductor status.

The entry point is ``mzt start/stop/restart`` via
``cli/commands/conductor.py``.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import math
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import typer

from marianne.core.constants import SHEET_NUM_KEY
from marianne.core.logging import get_logger
from marianne.daemon.config import (
    LEGACY_PID_PATH,
    LEGACY_SOCKET_PATH,
    DaemonConfig,
    DaemonLoggingConfig,
)
from marianne.daemon.pgroup import ProcessGroupManager
from marianne.daemon.task_utils import log_task_exception

if TYPE_CHECKING:
    from marianne.daemon.health import HealthChecker
    from marianne.daemon.ipc.handler import RequestHandler
    from marianne.daemon.ipc.server import DaemonServer
    from marianne.daemon.manager import JobManager
    from marianne.daemon.types import ConfigReloadResult, ObserverEvent

_logger = get_logger("conductor")

# Advisory lock file descriptor — held for daemon lifetime to prevent
# concurrent starts.  Set by _write_pid(), released on process exit.
_pid_lock_fd: int | None = None


def _daemon_logging_config(config: DaemonConfig) -> DaemonLoggingConfig:
    """Return the validated daemon logging policy for startup/reload."""
    logging_config = getattr(config, "logging", None)
    if isinstance(logging_config, DaemonLoggingConfig):
        return logging_config
    return DaemonLoggingConfig()


def _configure_daemon_logging(
    config: DaemonConfig,
    *,
    format: str,  # noqa: A002
) -> None:
    """Configure process logging from the conductor-owned logging policy."""
    from marianne.core.logging import configure_logging

    logging_config = _daemon_logging_config(config)
    configure_logging(
        level=config.log_level.upper(),  # type: ignore[arg-type]
        format=format,  # type: ignore[arg-type]
        file_path=config.log_file,
        max_file_size_mb=logging_config.max_file_size_mb,
        backup_count=logging_config.backup_count,
        compress_logs=logging_config.compress,
        include_timestamps=True,
    )


def _read_proc_text(pid: int, name: str) -> str | None:
    """Read a small procfs field for IPC provenance."""
    try:
        return Path(f"/proc/{pid}/{name}").read_text(errors="replace").strip()
    except (FileNotFoundError, OSError, PermissionError):
        return None


def _ipc_peer_metadata(writer: asyncio.StreamWriter) -> dict[str, Any]:
    """Best-effort Unix socket peer metadata for control-plane audit logs."""
    from marianne.daemon.ipc.peercred import read_peer_credentials

    creds = read_peer_credentials(writer)
    if creds is None:
        return {}

    metadata: dict[str, Any] = {
        "client_pid": creds.pid,
        "client_uid": creds.uid,
        "client_gid": creds.gid,
    }

    comm = _read_proc_text(creds.pid, "comm")
    if comm:
        metadata["client_comm"] = comm

    try:
        metadata["client_exe"] = os.readlink(f"/proc/{creds.pid}/exe")
    except (FileNotFoundError, OSError, PermissionError):
        pass

    return metadata


def _cancel_source_from_peer(metadata: dict[str, Any]) -> str:
    """Compact cancel source string for task cancellation reasons."""
    pid = metadata.get("client_pid")
    if pid is None:
        return "ipc"
    parts = [f"ipc:pid={pid}"]
    uid = metadata.get("client_uid")
    if uid is not None:
        parts.append(f"uid={uid}")
    comm = metadata.get("client_comm")
    if comm:
        parts.append(f"comm={comm}")
    return ",".join(parts)


# ─── Core Functions (used by cli/commands/conductor.py) ───────────────
CONDUCTOR_PROBE_TIMEOUT = 2.0
# Bounded budget for a superseding `mzt start` to out-wait a draining
# conductor before starting (matches `mzt restart`'s established wait).
CONDUCTOR_SUPERSEDE_TIMEOUT = 30.0


def start_conductor(
    config_file: Path | None = None,
    foreground: bool = False,
    log_level: str = "info",
    profile: str | None = None,
    clone_name: str | None = None,
) -> None:
    """Start the Marianne conductor process.

    Called by ``mzt start`` via ``cli/commands/conductor.py``.

    A live conductor that is *serving* is reported as already running
    (exit 0, GH #459). A live conductor that is *draining* (graceful
    shutdown) is superseded (GH #115): this waits up to
    ``CONDUCTOR_SUPERSEDE_TIMEOUT`` for the old process to exit, then
    starts a fresh conductor; it never spawns a second daemon beside a
    draining one, which holds the PID-file flock and socket lock.

    When *clone_name* is provided, the conductor runs with isolated
    paths (socket, PID file, state DB, log) so it can coexist with the
    production conductor.  The base config is loaded normally, then
    clone-specific paths are applied on top.
    """
    config = _load_config(config_file, profile=profile)
    config.log_level = cast(Any, log_level)

    # Apply clone path overrides when running as a clone conductor
    if clone_name is not None:
        from marianne.daemon.clone import build_clone_config

        config = build_clone_config(clone_name, base_config=config)
        config.log_level = cast(Any, log_level)

    # #227: probe the legacy /tmp PID too — a conductor started before the
    # path move must block a second `mzt start` (both would share the state
    # DB). Clones keep their own explicit paths.
    pid_probe_file = (
        config.pid_file
        if clone_name is not None
        else _resolve_live_pid_file(config.pid_file)
    )
    pid = _read_pid(pid_probe_file)
    if pid is not None:
        if _pid_alive(pid):
            label = "clone conductor" if clone_name is not None else "Marianne conductor"
            legacy_note = (
                " (on legacy /tmp paths — `mzt stop && mzt start` to migrate)"
                if pid_probe_file != config.pid_file
                else ""
            )
            # A live PID may be a conductor that is *serving* or one that is
            # *draining* (graceful shutdown can take up to
            # shutdown_timeout_seconds, default 300s). Only a serving one is
            # "already running" — a draining one must be superseded (GH #115).
            # Probe before deciding; on a silent probe be conservative and
            # treat it as running (GH #455: slow is not dead).
            # The #227 legacy PID fallback must probe that daemon's socket,
            # rather than an explicit modern path that bypasses IPC fallback.
            socket_probe_path = (
                LEGACY_SOCKET_PATH
                if pid_probe_file != config.pid_file
                else config.socket.path
            )
            shutting_down = _probe_shutting_down(socket_probe_path)
            if shutting_down is not True:
                # Serving (False) or silent (None): treat as running.
                #
                # Exit 0, not 1: a conductor being up is the state `mzt start`
                # exists to reach. A supervisor with Restart=on-failure (the
                # shipped systemd user unit) read exit 1 as a crash and
                # restarted every 30 s for three days against a hand-started
                # conductor (Pallas, GH #459). Exit 0 ends that loop; the
                # message still names the PID so a human sees what happened.
                typer.echo(f"{label} is already running (PID {pid}){legacy_note}")
                raise typer.Exit(0)
            # Draining conductor: wait (bounded) for it to exit, then start.
            # Never spawn a second daemon beside it — the dying one holds the
            # PID-file flock and socket lock for its whole life.
            typer.echo(
                f"{label} is shutting down (PID {pid}); "
                f"superseding it (waiting up to {CONDUCTOR_SUPERSEDE_TIMEOUT:.0f}s)"
            )
            if not wait_for_conductor_exit(
                pid_probe_file, timeout=CONDUCTOR_SUPERSEDE_TIMEOUT
            ):
                typer.echo(
                    f"Cannot start: {label} (PID {pid}) did not exit within "
                    f"{CONDUCTOR_SUPERSEDE_TIMEOUT:.0f}s. It may still be draining "
                    "active work. Check active scores: mzt status; retry later; "
                    "or use mzt stop --force only when you accept orphaning "
                    "active work."
                )
                raise typer.Exit(1)
            typer.echo("Drained conductor exited; starting a new one")
            # Fall through to a normal start below.
        else:
            # Stale PID file — process is dead, clean up before starting
            typer.echo(f"Cleaned up stale PID file (PID {pid} is no longer running)")
            pid_probe_file.unlink(missing_ok=True)

    # Detect concurrent start race via advisory lock
    if config.pid_file.exists() and not config.pid_file.is_symlink():
        try:
            probe_fd = os.open(str(config.pid_file), os.O_RDONLY)
            try:
                fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe_fd, fcntl.LOCK_UN)
            finally:
                os.close(probe_fd)
        except OSError:
            typer.echo("Marianne conductor is starting up (PID file locked)")
            raise typer.Exit(1) from None

    log_fmt = "console" if foreground else "json"
    _configure_daemon_logging(config, format=log_fmt)

    if not foreground:
        _daemonize(config)
    else:
        _logger.info(
            "daemon.starting",
            pid=os.getpid(),
            foreground=True,
            clone=clone_name,
        )

    daemon = DaemonProcess(config, start_profile=profile)
    asyncio.run(daemon.run())


def _probe_shutting_down(socket_path: Path | None = None) -> bool | None:
    """Ask a live conductor whether it is draining (``daemon.ready``).

    Returns ``True`` when the daemon reports ``shutting_down``, ``False``
    when it is serving, and ``None`` when the probe fails (no socket,
    timeout, malformed answer). Callers must treat ``None`` conservatively
    — a healthy conductor under I/O pressure may not answer within the
    probe budget (GH #455: slow is not dead).
    """
    from marianne.daemon.detect import _resolve_socket_path
    from marianne.daemon.ipc.client import DaemonClient

    resolved = _resolve_socket_path(socket_path)

    async def _probe() -> bool | None:
        client = None
        try:
            client = DaemonClient(resolved, timeout=CONDUCTOR_PROBE_TIMEOUT)
            async with asyncio.timeout(CONDUCTOR_PROBE_TIMEOUT):
                ready = await client.readiness()
            shutting_down = ready.get("shutting_down")
            if not isinstance(shutting_down, bool):
                return None
            return shutting_down
        except Exception:
            return None
        finally:
            if client is not None:
                await client.close()

    try:
        return asyncio.run(_probe())
    except Exception:
        return None


def _check_running_jobs(
    socket_path: Path | None = None,
) -> dict[str, Any] | None:
    """Probe the conductor via IPC for running job count.

    Returns a dict with ``running_jobs`` and ``job_ids``, or None
    if the IPC call fails (conductor unresponsive).
    """
    from marianne.daemon.detect import _resolve_socket_path
    from marianne.daemon.ipc.client import DaemonClient

    resolved = _resolve_socket_path(socket_path)

    async def _probe() -> dict[str, Any] | None:
        client = None
        try:
            client = DaemonClient(resolved, timeout=CONDUCTOR_PROBE_TIMEOUT)
            async with asyncio.timeout(CONDUCTOR_PROBE_TIMEOUT):
                ready = await client.readiness()
            running = ready.get("running_jobs")
            if type(running) is not int or running < 0:
                return None
            return {
                "running_jobs": running,
                "job_ids": ready.get("job_ids", []),
            }
        except Exception:
            return None
        finally:
            if client is not None:
                await client.close()

    try:
        return asyncio.run(_probe())
    except Exception:
        return None


def stop_conductor(
    pid_file: Path | None = None,
    force: bool = False,
    socket_path: Path | None = None,
    wait: float | None = None,
) -> None:
    """Stop the running Marianne conductor (daemon) process.

    Called by ``mzt stop`` via ``cli/commands/conductor.py``.

    Non-blocking by default: sends the signal and returns without
    waiting for the process to exit. Pass *wait* (seconds) to block
    until the conductor exits; ``wait=0`` keeps the default
    signal-and-return behavior.

    When jobs are actively running and ``--force`` is not set, warns
    the user and asks for confirmation before proceeding (#94).
    Force mode (``--force``) skips the safety check entirely.
    """
    if wait is not None and (not math.isfinite(wait) or wait < 0):
        raise ValueError("wait must be finite and >= 0")

    # Explicit pid_file always wins; only the default probes legacy (#227).
    resolved_pid_file = (
        pid_file
        if pid_file is not None
        else _resolve_live_pid_file(DaemonConfig().pid_file)
    )
    pid = _read_pid(resolved_pid_file)
    if pid is None or not _pid_alive(pid):
        typer.echo("Marianne conductor is not running")
        resolved_pid_file.unlink(missing_ok=True)
        raise typer.Exit(1)

    # Safety guard: check for running jobs unless --force (#94)
    if not force:
        running_info = _check_running_jobs(socket_path)
        if running_info is None:
            typer.echo(
                "Cannot determine active work: conductor is unresponsive. "
                "Stop refused; verify work independently before using --force."
            )
            raise typer.Exit(1)
        if running_info is not None and running_info["running_jobs"] > 0:
            count = running_info["running_jobs"]
            typer.echo(
                f"⚠ Warning: {count} job(s) currently running. "
                "Stopping the conductor will orphan active agents and "
                "may corrupt job state.",
            )
            if not typer.confirm("Proceed with stop?", default=False):
                typer.echo("Aborted.")
                raise typer.Exit(1)

    sig = signal.SIGKILL if force else signal.SIGTERM
    os.kill(pid, sig)
    typer.echo(
        f"Sent {'SIGKILL' if force else 'SIGTERM'} to Marianne conductor (PID {pid})",
    )

    if wait is not None and wait > 0:
        if wait_for_conductor_exit(resolved_pid_file, timeout=wait):
            typer.echo("Marianne conductor stopped")
        else:
            typer.echo(
                f"Marianne conductor (PID {pid}) did not exit within "
                f"{wait:.0f}s. It may still be draining active work. "
                "Check active scores: mzt status; retry later; or use "
                "mzt stop --force only when you accept orphaning active work."
            )
            raise typer.Exit(1)


def get_conductor_status(
    pid_file: Path | None = None,
    socket_path: Path | None = None,
    as_json: bool = False,
) -> None:
    """Check Marianne conductor (daemon) status via health probes.

    Called by ``mzt conductor-status`` via ``cli/commands/conductor.py``.
    With ``as_json``, stdout carries ONLY the JSON document (human
    commentary goes to stderr) so ``mzt conductor-status --json | jq .``
    works. With an explicit ``socket_path``, no PID file is read,
    asserted, or unlinked — the daemon's own payload is the pid
    authority (#408 landing fixes).
    """
    _defaults = DaemonConfig()
    # #408 landing P2: explicit --socket means socket-only probing — the
    # default pid file must never be read, asserted, or UNLINKED as a
    # side effect of inspecting a clone.
    socket_only = socket_path is not None
    resolved_socket: Path = (
        socket_path if socket_path is not None else _defaults.socket.path
    )

    def _note(message: str) -> None:
        # In --json mode stdout is reserved for the JSON document alone;
        # human commentary goes to stderr so pipes stay parseable.
        if as_json:
            typer.secho(message, err=True)
        else:
            typer.echo(message)

    pid: int | None = None
    pid_alive: bool | None = None
    if socket_only and pid_file is not None:
        # A clone's own PID file was named explicitly (conductor.py resolves
        # both clone paths). Reading it is not the #408 hazard, which was the
        # DEFAULT file being read or unlinked while inspecting a clone. It is
        # the evidence that tells busy from dead when the socket is silent
        # (GH #455); it is never asserted or unlinked here.
        pid = _read_pid(pid_file)
        pid_alive = _pid_alive(pid) if pid is not None else None
    if not socket_only:
        # Explicit pid_file always wins; only the default probes legacy (#227).
        resolved_pid_file = (
            pid_file
            if pid_file is not None
            else _resolve_live_pid_file(_defaults.pid_file)
        )
        # #227 transitional: a pre-move conductor serves on the legacy socket.
        if not resolved_socket.exists():
            from marianne.daemon.config import LEGACY_SOCKET_PATH

            if LEGACY_SOCKET_PATH.exists():
                resolved_socket = LEGACY_SOCKET_PATH

        pid = _read_pid(resolved_pid_file)
        if pid is None or not _pid_alive(pid):
            _note("Marianne conductor is not running")
            resolved_pid_file.unlink(missing_ok=True)
            raise typer.Exit(1)
        pid_alive = True

        _note(f"Marianne conductor is running (PID {pid})")

    from marianne.daemon.ipc.client import DaemonClient

    client = DaemonClient(resolved_socket, timeout=CONDUCTOR_PROBE_TIMEOUT)

    from marianne.daemon.exceptions import DaemonError

    async def _probe(method: str) -> dict[str, Any] | None:
        try:
            result: dict[str, Any] = await asyncio.wait_for(
                client.call(method), CONDUCTOR_PROBE_TIMEOUT,
            )
            return result
        except (OSError, DaemonError) as e:
            _logger.info(f"probe.{method.split('.')[-1]}_failed", error=str(e))
            return None

    async def _get_health() -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        try:
            health, ready, daemon_info = await asyncio.gather(
                _probe("daemon.health"), _probe("daemon.ready"), _probe("daemon.status"),
            )
            return health, ready, daemon_info
        finally:
            await client.close()

    def _silent_socket() -> None:
        """The socket did not answer. Say what the PID file and the host prove
        (GH #455): a live PID under I/O pressure is busy, not dead, and the
        remedy is to wait, never `mzt start`/`mzt restart`.
        """
        from marianne.daemon.system_probe import read_io_pressure

        pressure = read_io_pressure()
        if pid is not None and pid_alive:
            _note(
                f"Marianne conductor PID {pid} is alive but its socket "
                f"{resolved_socket} did not answer: busy or unresponsive"
            )
            if pressure is not None:
                _note(
                    f"  Host I/O pressure (avg10): some {pressure.get('some_avg10', 0.0)}% "
                    f"full {pressure.get('full_avg10', 0.0)}%"
                )
            _note("  Do not start or restart it; wait for pressure to clear and retry")
            if as_json:
                import json as _json

                typer.echo(_json.dumps({
                    "pid": pid,
                    "alive": True,
                    "socket_answered": False,
                    "running_state": "unknown",
                    "io_pressure": pressure,
                }, indent=2, sort_keys=True))
        elif pid is not None:
            _note(f"Marianne conductor PID {pid} from the PID file is not alive")
            _note("  (Could not connect to conductor socket for details)")
        else:
            _note("  (Could not connect to conductor socket for details)")

    try:
        health, ready, daemon_info = asyncio.run(_get_health())
    except (OSError, DaemonError):
        _silent_socket()
        if as_json or socket_only:
            # Machine consumers never get a half-valid full document (the
            # live-PID case above emits its own small one); socket-only mode
            # has no further authority to consult.
            raise typer.Exit(1) from None
        return

    if socket_only and daemon_info is None:
        # The daemon itself did not answer — with an explicit --socket it
        # is the only authority for the full payload, so fail honestly
        # instead of printing one with a foreign or missing pid.
        _silent_socket()
        raise typer.Exit(1)

    if as_json:
        import json

        payload: dict[str, Any] = {"pid": pid}
        if daemon_info is not None:
            payload.update(daemon_info)
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    from rich.console import Console
    from rich.panel import Panel

    con = Console()

    # Socket-only mode has no pid-file pid; the daemon payload is
    # authoritative wherever it answered.
    display_pid: Any = (
        pid if pid is not None
        else (daemon_info or {}).get("pid", "unknown")
    )
    if socket_only:
        typer.echo(f"Marianne conductor is running (PID {display_pid})")

    # Build uptime string
    uptime_str = ""
    if health:
        uptime = health.get("uptime_seconds", 0)
        hours, remainder = divmod(int(uptime), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours >= 24:
            days = hours // 24
            remaining_hours = hours % 24
            uptime_str = f"{days}d {remaining_hours}h {minutes}m"
        else:
            uptime_str = f"{hours}h {minutes}m {seconds}s"

    # Build panel content
    lines: list[str] = []
    lines.append(
        f"PID {display_pid} [dim]\u00b7[/dim] Up {uptime_str}"
        if uptime_str else f"PID {display_pid}"
    )

    if ready:
        status_str = ready.get("status", "unknown")
        accepting = ready.get("accepting_work", False)
        if status_str == "ready" and accepting:
            lines.append("[green]Ready[/green] [dim]\u00b7[/dim] Accepting work")
        elif status_str == "ready":
            lines.append("[yellow]Ready[/yellow] [dim]\u00b7[/dim] Not accepting work")
        else:
            lines.append(f"Status: {status_str}")

        running_jobs = ready.get("running_jobs", 0)
        lines.append("")
        if running_jobs > 0:
            lines.append(f"Jobs: [bold]{running_jobs}[/bold] running")
        else:
            lines.append("[dim]No active jobs[/dim]")

        # Resources with context
        lines.append("")
        lines.append("[bold]Resources[/bold]")
        memory_mb = ready.get("memory_mb", 0)
        memory_limit = ready.get("memory_limit_mb", 0)
        child_procs = ready.get("child_processes", 0)
        process_limit = ready.get("process_limit", 0)

        if memory_limit and memory_limit > 0:
            pct = int(memory_mb / memory_limit * 100)
            lines.append(f"  Memory: {memory_mb} MB ({pct}% of {memory_limit} MB)")
        else:
            lines.append(f"  Memory: {memory_mb} MB")

        if process_limit and process_limit > 0:
            lines.append(f"  Processes: {child_procs} / {process_limit}")
        else:
            lines.append(f"  Processes: {child_procs}")

        # Pressure indicator
        pressure = ready.get("pressure", "NONE")
        if isinstance(pressure, str) and pressure.upper() != "NONE":
            lines.append(f"  Pressure: [yellow]{pressure}[/yellow]")
        else:
            lines.append("  Pressure: [green]NONE[/green]")

        # GH #455: how late the loop wakes (accept() runs there) and whether
        # the host is stalled on I/O. Slow is told apart from dead here.
        lag = ready.get("event_loop_lag_ms")
        peak = ready.get("event_loop_lag_peak_ms")
        if isinstance(lag, (int, float)):
            colour = "red" if lag >= 1000 else "yellow" if lag >= 100 else "green"
            peak_text = f" (peak {peak} ms / 60 s)" if isinstance(peak, (int, float)) else ""
            lines.append(f"  Event loop lag: [{colour}]{lag} ms[/{colour}]{peak_text}")
        io_pressure = ready.get("io_pressure")
        if isinstance(io_pressure, dict):
            some = io_pressure.get("some_avg10", 0.0)
            full = io_pressure.get("full_avg10", 0.0)
            colour = "red" if full >= 20 else "yellow" if some >= 20 else "green"
            lines.append(
                f"  Host I/O pressure: [{colour}]some {some}% full {full}%[/{colour}] (avg10)"
            )

    # Hot-reload observability (GH #408, #450): the last reload's outcome is
    # the only place an operator learns a profile on disk is not the one the
    # conductor runs, and whether the remedy is an edit or a restart.
    if daemon_info:
        lines.append("")
        lines.append("[bold]Config[/bold]")
        generation = daemon_info.get("config_generation")
        loaded_at = daemon_info.get("config_loaded_at")
        lines.append(
            f"  Generation: {generation}"
            + (f" [dim]\u00b7[/dim] loaded {loaded_at}" if loaded_at else "")
        )
        last = daemon_info.get("last_config_reload")
        if isinstance(last, dict):
            state = (
                "failed" if not last.get("success")
                else "partial" if last.get("partial")
                else "applied"
            )
            lines.append(f"  Last reload: {state} ({last.get('reason', '?')})")
            for failed in last.get("failed_profiles") or []:
                retained = failed.get("retained") or ""
                keep = f" — previous entry '{retained}' retained" if retained else ""
                lines.append(
                    f"  [yellow]![/yellow] {failed.get('path')}: "
                    f"{failed.get('reason_code')}{keep}"
                )
            if last.get("schema_stale"):
                lines.append(
                    "  [yellow]![/yellow] conductor predates a profile's schema: "
                    "the file is valid for newer code; restart at a job-free boundary"
                )

    border_style = "green" if ready and ready.get("status") == "ready" else "yellow"
    con.print(Panel("\n".join(lines), title="Conductor", border_style=border_style))

    if daemon_info:
        version = daemon_info.get("version", "?")
        con.print(f"[dim]Version: {version}[/dim]")


# ─── DaemonProcess ────────────────────────────────────────────────────


class DaemonProcess:
    """Long-running Marianne daemon process.

    Composes DaemonServer (IPC), JobManager (job tracking),
    and ResourceMonitor (limits) into a single lifecycle.
    """

    def __init__(self, config: DaemonConfig, *, start_profile: str | None = None) -> None:
        self._config = config
        # #408: the start-time operational profile must survive a reload —
        # a daemon started with ``--profile dev`` reloads with the same
        # overlay instead of silently dropping it.
        self._start_profile = start_profile
        self._signal_received = asyncio.Event()
        self._pgroup = ProcessGroupManager()
        self._start_time = time.monotonic()
        self._signal_tasks: list[asyncio.Task[Any]] = []
        self._profiler: Any = None  # Set in run() step 8.5
        self._correlation: Any = None  # Set in run() step 8.6
        self._config_watcher: Any = None  # Set in run() step 8.7

    async def run(self) -> None:
        """Main daemon lifecycle: boot, serve, shutdown."""
        # 1. Write PID file
        _write_pid(self._config.pid_file)

        try:
            # 2. Set up process group (fixes issue #38 — orphan prevention)
            self._pgroup.setup()

            # 2.5. Reap orphaned backend children from previous runs.
            # Claude CLI spawns LSP/MCP servers that outlive it; if a
            # previous daemon or CLI session crashed, these accumulate.
            startup_reaped = self._pgroup.reap_orphaned_backends()
            if startup_reaped:
                _logger.info(
                    "daemon.startup_orphan_reap",
                    count=len(startup_reaped),
                    pids=startup_reaped,
                )

            # 3. Create components — single ResourceMonitor shared
            #    between periodic monitoring and backpressure checks.
            from marianne.daemon.ipc.handler import RequestHandler
            from marianne.daemon.ipc.server import DaemonServer
            from marianne.daemon.manager import JobManager
            from marianne.daemon.monitor import ResourceMonitor

            # Create monitor first (without manager ref — set after).
            self._monitor = ResourceMonitor(
                self._config.resource_limits,
                pgroup=self._pgroup,
            )
            # Pass the single monitor into JobManager for backpressure.
            self._manager = JobManager(
                self._config,
                start_time=self._start_time,
                monitor=self._monitor,
                pgroup=self._pgroup,
            )
            # Now wire the manager back into the monitor for job counts.
            self._monitor.set_manager(self._manager)
            await self._manager.start()

            # Warn about unenforced / reserved config fields.
            # Each entry: (field, current_value, default, event, message)
            _unenforced_fields: list[tuple[str, object, object, str, str]] = [
                (
                    "max_api_calls_per_minute",
                    self._config.resource_limits.max_api_calls_per_minute,
                    60,
                    "config.unenforced_rate_limit",
                    "max_api_calls_per_minute is set but NOT YET ENFORCED. "
                    "Rate limiting currently works through externally-reported "
                    "events via RateLimitCoordinator.",
                ),
                (
                    "state_backend_type",
                    self._config.state_backend_type,
                    "sqlite",
                    "config.reserved_field_ignored",
                    "state_backend_type is fixed to SQLite; alternative state "
                    "backends are not supported.",
                ),
            ]
            for field_name, current, default, event, msg in _unenforced_fields:
                if current != default:
                    _logger.warning(event, field=field_name, value=current, message=msg)

            handler = RequestHandler()

            # 4. Create health checker
            from marianne.daemon.health import HealthChecker

            health = HealthChecker(
                self._manager,
                self._monitor,
                start_time=self._start_time,
                learning_store=(
                    self._manager._learning_hub.store if self._manager._learning_hub else None
                ),
            )

            # 5. Register RPC methods (adapt JobManager to handler signature)
            self._register_methods(handler, self._manager, health)

            # 6. Start server
            server = DaemonServer(
                self._config.socket.path,
                handler,
                permissions=self._config.socket.permissions,
                backlog=self._config.socket.backlog,
                max_connections=self._config.socket.max_connections,
                max_concurrent_requests=self._config.socket.max_concurrent_requests,
                max_streams=self._config.socket.max_streams,
                request_timeout=self._config.socket.request_timeout,
                write_timeout=self._config.socket.write_timeout,
                enforce_peer_uid=self._config.socket.enforce_peer_uid,
            )
            await server.start()

            # 7. Install signal handlers (tracked to surface exceptions)
            loop = asyncio.get_running_loop()

            from collections.abc import Callable

            def _make_signal_callback(
                signum: signal.Signals,
            ) -> Callable[[], None]:
                """Create a signal callback that captures ``signum`` by value."""

                def _cb() -> None:
                    self._track_signal_task(
                        asyncio.create_task(
                            self._handle_signal(signum, self._manager, server),
                        ),
                    )

                return _cb

            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, _make_signal_callback(sig))

            def _sighup_callback() -> None:
                self._track_signal_task(
                    asyncio.create_task(self._handle_sighup()),
                )

            loop.add_signal_handler(signal.SIGHUP, _sighup_callback)

            # 8. Start resource monitor
            interval = self._config.monitor_interval_seconds
            await self._monitor.start(interval_seconds=interval)

            # 8.4. Start entropy monitoring (v25 evolution)
            # Wire health checker callback into manager for job completion tracking
            self._manager._entropy_check_callback = health.on_job_completed
            await health.start_periodic_checks()

            # 8.5. Start profiler collector (after monitor, needs event bus)
            if self._config.profiler.enabled:
                from marianne.daemon.profiler.collector import ProfilerCollector

                self._profiler = ProfilerCollector(
                    config=self._config.profiler,
                    monitor=self._monitor,
                    pgroup=self._pgroup,
                    event_bus=self._manager.event_bus,
                    manager=self._manager,
                )
                await self._profiler.start()

            # 8.6. Start correlation analyzer (after profiler, needs storage + learning hub)
            if self._config.profiler.enabled and self._profiler is not None:
                from marianne.daemon.profiler.correlation import CorrelationAnalyzer

                self._correlation = CorrelationAnalyzer(
                    storage=self._profiler._storage,
                    learning_hub=self._manager.learning_hub,
                    config=self._config.profiler.correlation,
                )
                await self._correlation.start(self._manager.event_bus)

            # 8.7. Start the config hot-reload watcher (#408) — watches the
            # config file and the instrument profile source dirs (the same
            # dirs the manager loaded from) and funnels changes into the
            # single reload path. Opt-in via hot_reload.enabled (default on).
            if self._config.hot_reload.enabled:
                from marianne.daemon.hot_reload import ConfigWatcher
                from marianne.instruments.classes import class_source_paths

                self._config_watcher = ConfigWatcher(
                    config_file=self._config.config_file,
                    profile_dirs=list(self._manager.profile_source_paths),
                    class_files=list(class_source_paths()),
                    reload_fn=self.reload_configuration,
                    enabled_fn=lambda: self._config.hot_reload.enabled,
                    debounce_fn=lambda: self._config.hot_reload.debounce_seconds,
                )
                await self._config_watcher.start()
                _logger.info(
                    "daemon.config_watcher_started",
                    config_file=str(self._config.config_file),
                )

            # 9. Run until shutdown
            _logger.info(
                "daemon.started",
                pid=os.getpid(),
                socket=str(self._config.socket.path),
            )
            await self._manager.wait_for_shutdown()

            # 10. Cleanup — watcher, correlation, profiler, entropy, monitor
            if self._config_watcher is not None:
                await self._config_watcher.stop()
                self._config_watcher = None
            if self._correlation is not None:
                await self._correlation.stop()
            if self._profiler is not None:
                await self._profiler.stop()
            await health.stop_periodic_checks()
            await self._monitor.stop()
            await server.stop()

            # 11. Kill remaining children in process group (issue #38)
            self._pgroup.kill_all_children()
            orphans = self._pgroup.cleanup_orphans()
            if orphans:
                _logger.info(
                    "daemon.shutdown_orphans_cleaned",
                    count=len(orphans),
                )

            _logger.info("daemon.stopped")
        finally:
            # Always remove PID file, even on crash
            self._config.pid_file.unlink(missing_ok=True)

    def _register_methods(
        self,
        handler: RequestHandler,
        manager: JobManager,
        health: HealthChecker | None = None,
    ) -> None:
        """Wire JSON-RPC methods to JobManager and HealthChecker.

        Handler params arrive as ``dict[str, Any]`` from JSON-RPC.
        See ``daemon/types.py`` for the expected parameter shapes:
        JobSubmitParams, JobIdentifyParams, JobCancelParams,
        DaemonShutdownParams.
        """
        from marianne.daemon.types import JobRequest

        def _require_schedule_peer(writer: Any) -> None:
            """The venue methods fail closed if kernel peer identity is unavailable."""
            from marianne.daemon.exceptions import DaemonAccessDeniedError
            from marianne.daemon.ipc.peercred import read_peer_credentials

            if not isinstance(writer, asyncio.StreamWriter):
                raise DaemonAccessDeniedError()
            credentials = read_peer_credentials(writer)
            if credentials is None or credentials.uid != os.getuid():
                raise DaemonAccessDeniedError()

        def _workspace_path(raw: str | None) -> Path | None:
            return Path(raw) if raw else None

        async def handle_submit(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            request = JobRequest(**params)
            # Provenance: attempts are logged before the manager verdict so a
            # rogue or accidental same-UID submission is attributable after
            # the fact.  Peer credentials are user-granular — this identifies
            # *which process* asked, it does not authorize anything.
            _logger.info(
                "ipc_job_submitted",
                **_ipc_peer_metadata(_w),
                config_path=str(request.config_path),
            )
            response = await manager.submit_job(request)
            return response.model_dump()

        async def handle_job_status(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            # Let JobSubmissionError propagate — the JSON-RPC protocol maps it
            # to a JOB_NOT_FOUND error code, and the client re-raises it.
            return await manager.get_job_status(
                params["job_id"],
                _workspace_path(params.get("workspace")),
            )

        async def handle_pause(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            from marianne.daemon.exceptions import JobSubmissionError

            try:
                ok = await manager.pause_job(params["job_id"])
                return {"paused": ok}
            except JobSubmissionError as e:
                return {"paused": False, "error": str(e)}

        async def handle_resume(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            from marianne.daemon.exceptions import JobSubmissionError

            try:
                config_path_str = params.get("config_path")
                config_path = Path(config_path_str) if config_path_str else None
                no_reload = bool(params.get("no_reload", False))
                from_sheet_raw = params.get("from_sheet")
                from_sheet = int(from_sheet_raw) if from_sheet_raw is not None else None
                response = await manager.resume_job(
                    params["job_id"],
                    _workspace_path(params.get("workspace")),
                    config_path=config_path,
                    no_reload=no_reload,
                    from_sheet=from_sheet,
                    # #361: resume can additionally enable (never disable)
                    # escalation/healing; checkpoint values are inherited.
                    escalation=bool(params.get("escalation", False)),
                    self_healing=bool(params.get("self_healing", False)),
                )
                return response.model_dump()
            except JobSubmissionError as e:
                return {"job_id": params.get("job_id", ""), "status": "rejected", "message": str(e)}

        async def handle_cancel(params: dict[str, Any], writer: Any) -> dict[str, Any]:
            job_id = params["job_id"]
            peer_metadata = (
                _ipc_peer_metadata(writer)
                if isinstance(writer, asyncio.StreamWriter)
                else {}
            )
            source = _cancel_source_from_peer(peer_metadata)
            _logger.info(
                "job.cancel_rpc_received",
                job_id=job_id,
                source=source,
                **peer_metadata,
            )
            ok = await manager.cancel_job(job_id, source=source)
            return {"cancelled": ok}

        async def handle_resolve_escalation(
            params: dict[str, Any], _w: Any
        ) -> dict[str, Any]:
            # #361: backs `mzt resolve` — conductor-side marker write +
            # immediate consumption through the existing fermata poll.
            return await manager.resolve_escalation(
                params["job_id"],
                int(params["sheet_num"]),
                str(params["decision"]),
            )

        async def handle_list(_p: dict[str, Any], _w: Any) -> list[dict[str, Any]]:
            return await manager.list_jobs()

        async def handle_daemon_status(_p: dict[str, Any], _w: Any) -> dict[str, Any]:
            return await manager.get_daemon_status()

        async def handle_schedule_register(
            params: dict[str, Any], writer: Any,
        ) -> dict[str, Any]:
            _require_schedule_peer(writer)
            config_path = params["config_path"]
            if not isinstance(config_path, str) or not config_path:
                raise ValueError("config_path must be a non-empty string")
            return await manager.register_schedule(Path(config_path))

        async def handle_schedule_current(
            params: dict[str, Any], writer: Any,
        ) -> dict[str, Any]:
            _require_schedule_peer(writer)
            return await manager.current_schedule(
                str(params["schedule_id"]), str(params["registry_identity"]),
                str(params["source_digest"]),
            )

        async def handle_schedule_cancel(
            params: dict[str, Any], writer: Any,
        ) -> dict[str, Any]:
            _require_schedule_peer(writer)
            return await manager.cancel_schedule(
                str(params["schedule_id"]), str(params["registry_identity"]),
                str(params["source_digest"]),
            )

        async def handle_schedule_register_v2(
            params: dict[str, Any], writer: Any,
        ) -> dict[str, Any]:
            _require_schedule_peer(writer)
            config_path = params.get("config_path")
            if not isinstance(config_path, str) or not config_path:
                raise ValueError("config_path must be a non-empty string")
            return await manager.register_schedule_v2(Path(config_path))

        async def handle_schedule_current_v2(
            params: dict[str, Any], writer: Any,
        ) -> dict[str, Any]:
            _require_schedule_peer(writer)
            return await manager.current_schedule_v2(
                str(params.get("schedule_id", "")),
                str(params.get("registry_identity", "")),
                str(params.get("source_digest", "")),
                str(params.get("registration_id", "")),
                params.get("receipt_version", 0),
            )

        async def handle_schedule_cancel_v2(
            params: dict[str, Any], writer: Any,
        ) -> dict[str, Any]:
            _require_schedule_peer(writer)
            return await manager.cancel_schedule_v2(
                str(params.get("schedule_id", "")),
                str(params.get("registry_identity", "")),
                str(params.get("source_digest", "")),
                str(params.get("registration_id", "")),
                params.get("receipt_version", 0),
            )

        async def handle_shutdown(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            graceful = params.get("graceful", True)
            task = asyncio.create_task(
                manager.shutdown(graceful=graceful),
                name="daemon-shutdown",
            )
            self._track_signal_task(task)
            return {"shutting_down": True}

        async def handle_errors(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            return await manager.get_job_errors(
                params["job_id"],
                _workspace_path(params.get("workspace")),
            )

        async def handle_diagnose(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            return await manager.get_diagnostic_report(
                params["job_id"],
                _workspace_path(params.get("workspace")),
            )

        async def handle_history(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            return await manager.get_execution_history(
                params["job_id"],
                _workspace_path(params.get("workspace")),
                sheet_num=params.get(SHEET_NUM_KEY),
                limit=params.get("limit", 50),
            )

        async def handle_recover(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            return await manager.recover_job(
                params["job_id"],
                _workspace_path(params.get("workspace")),
                sheet_num=params.get(SHEET_NUM_KEY),
                dry_run=params.get("dry_run", False),
            )

        async def handle_config(_p: dict[str, Any], _w: Any) -> dict[str, Any]:
            return self._config.model_dump(mode="json")

        async def handle_reload(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            # #408: IPC trigger for the single reload path. Returns the
            # applied/declined record so `mzt conductor reload` can show
            # exactly what changed.
            reason = str(params.get("reason") or "ipc")
            result = await self.reload_configuration(reason)
            return result.model_dump()

        async def handle_clear_jobs(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            return await manager.clear_jobs(
                statuses=params.get("statuses"),
                older_than_seconds=params.get("older_than_seconds"),
                job_ids=params.get("job_ids"),
            )

        async def handle_modify(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            from marianne.daemon.exceptions import JobSubmissionError

            config_path = params.get("config_path")
            if not config_path:
                return {
                    "job_id": params.get("job_id", ""),
                    "status": "rejected",
                    "message": "config_path required",
                }
            try:
                response = await manager.modify_job(
                    params["job_id"],
                    Path(config_path),
                    _workspace_path(params.get("workspace")),
                )
                return response.model_dump()
            except JobSubmissionError as e:
                return {"job_id": params.get("job_id", ""), "status": "rejected", "message": str(e)}

        handler.register("job.submit", handle_submit)
        handler.register("job.status", handle_job_status)
        handler.register("job.pause", handle_pause)
        handler.register("job.resume", handle_resume)
        handler.register("job.resolve_escalation", handle_resolve_escalation)
        handler.register("job.modify", handle_modify)
        handler.register("job.cancel", handle_cancel)
        handler.register("job.list", handle_list)
        handler.register("job.clear", handle_clear_jobs)
        handler.register("job.errors", handle_errors)
        handler.register("job.diagnose", handle_diagnose)
        handler.register("job.history", handle_history)
        handler.register("job.recover", handle_recover)

        handler.register("daemon.status", handle_daemon_status)
        handler.register("schedule.register", handle_schedule_register)
        handler.register("schedule.current", handle_schedule_current)
        handler.register("schedule.cancel", handle_schedule_cancel)
        handler.register("schedule.register_v2", handle_schedule_register_v2)
        handler.register("schedule.current_v2", handle_schedule_current_v2)
        handler.register("schedule.cancel_v2", handle_schedule_cancel_v2)
        handler.register("daemon.shutdown", handle_shutdown)
        handler.register("daemon.config", handle_config)
        handler.register("daemon.reload", handle_reload)

        # Health check probes
        if health is not None:

            async def handle_health(_p: dict[str, Any], _w: Any) -> dict[str, Any]:
                return await health.liveness()

            async def handle_ready(_p: dict[str, Any], _w: Any) -> dict[str, Any]:
                return await health.readiness()

            handler.register("daemon.health", handle_health)
            handler.register("daemon.ready", handle_ready)

        # Profiler IPC methods — delegate to ProfilerCollector when available
        async def handle_top(_p: dict[str, Any], _w: Any) -> dict[str, Any]:
            if self._profiler is None:
                return {"error": "profiler not enabled"}
            snapshot = self._profiler.get_latest_snapshot()
            return snapshot or {"error": "no snapshot available yet"}

        async def handle_top_stream(_p: dict[str, Any], _w: Any) -> dict[str, Any]:
            if self._profiler is None:
                return {"error": "profiler not enabled"}
            path = self._profiler.get_jsonl_path()
            return {"jsonl_path": path} if path else {"error": "JSONL not configured"}

        async def handle_events(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            if self._profiler is None:
                return {"events": [], "error": "profiler not enabled"}
            limit = params.get("limit", 50)
            return {"events": self._profiler.get_recent_events(limit=limit)}

        async def handle_monitor_stream(_p: dict[str, Any], writer: asyncio.StreamWriter) -> None:
            """Stream all EventBus events to the client as JSON-RPC notifications."""
            queue: asyncio.Queue[ObserverEvent] = asyncio.Queue(maxsize=100)

            def _on_event(event: ObserverEvent) -> None:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

            sub_id = manager.event_bus.subscribe(_on_event)
            try:
                while True:
                    event = await queue.get()
                    # Wrap EventBus event in a JSON-RPC notification
                    notification = {
                        "jsonrpc": "2.0",
                        "method": "monitor.event",
                        "params": event,
                    }
                    writer.write(json.dumps(notification).encode() + b"\n")
                    await asyncio.wait_for(writer.drain(), self._config.socket.write_timeout)
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            finally:
                manager.event_bus.unsubscribe(sub_id)

        async def handle_output_stream(
            params: dict[str, Any], writer: asyncio.StreamWriter
        ) -> None:
            """#352 inc-3: stream a sheet's (or whole job's) live output
            as JSON-RPC notifications — backs ``mzt watch``.

            Subscribe FIRST, snapshot second: a line landing between the
            two may arrive twice at the seam, which is harmless for a
            live tail; the reverse order would silently LOSE lines.
            Backpressure never blocks the daemon — slow readers get a
            ``[dropped N lines]`` marker from the bounded queue.
            """
            job_id = str(params["job_id"])
            raw_sheet = params.get("sheet_num")
            sheet_num = int(raw_sheet) if raw_sheet is not None else None

            def _notify(line: str) -> None:
                notification = {
                    "jsonrpc": "2.0",
                    # Same name as the subscribed stream method (GH #379): an
                    # observer that routes by method sees one logical stream.
                    "method": "job.output.stream",
                    "params": {
                        "job_id": job_id,
                        "sheet_num": sheet_num,
                        "line": line,
                    },
                }
                writer.write(json.dumps(notification).encode() + b"\n")

            hub = manager.output_hub
            if hub is None:
                _notify("[stream unavailable: conductor still starting]")
                await asyncio.wait_for(writer.drain(), self._config.socket.write_timeout)
                return

            sub = hub.subscribe(job_id, sheet_num)
            try:
                for line in hub.snapshot(job_id, sheet_num):
                    _notify(line)
                await asyncio.wait_for(writer.drain(), self._config.socket.write_timeout)
                while True:
                    line = await sub.queue.get()
                    _notify(line)
                    await asyncio.wait_for(writer.drain(), self._config.socket.write_timeout)
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            finally:
                hub.unsubscribe(job_id, sheet_num, sub)

        handler.register("daemon.top", handle_top)
        handler.register("daemon.top.stream", handle_top_stream)
        handler.register("daemon.events", handle_events)
        handler.register("daemon.monitor.stream", handle_monitor_stream, streaming=True)
        handler.register("job.output.stream", handle_output_stream, streaming=True)

        # Observer event recorder IPC — per-job behavioral events
        async def handle_observer_events(
            params: dict[str, Any],
            _w: Any,
        ) -> dict[str, Any]:
            if manager.observer_recorder is None:
                return {"events": []}
            job_id = params.get("job_id")
            limit = params.get("limit", 50)
            events = manager.observer_recorder.get_recent_events(
                job_id,
                limit=limit,
            )
            return {"events": events}

        handler.register("daemon.observer_events", handle_observer_events)

        # Orchestra view (GH #468): public-safe fleet snapshot, recent beats,
        # a live stream, and one day's replay. Read-only, no paths, no prompts.
        async def handle_orchestra_snapshot(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            orchestra = manager.orchestra
            if orchestra is None:
                return {"enabled": False, "jobs": [], "recent": []}
            limit = int(params.get("limit", 200))
            snapshot = orchestra.snapshot()
            snapshot["enabled"] = True
            snapshot["recent"] = orchestra.recent(limit=limit)
            return snapshot

        async def handle_orchestra_replay(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            from marianne.daemon.orchestra.recorder import read_day

            orchestra = manager.orchestra
            if orchestra is None:
                return {"enabled": False, "day": None, "events": []}
            day = params.get("day")
            if day is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)):
                raise ValueError("day must be YYYY-MM-DD")
            path = orchestra.day_path(str(day) if day else None)
            limit = params.get("limit")
            events = read_day(path, limit=int(limit) if limit else None)
            return {"enabled": True, "day": path.stem, "events": events}

        async def handle_orchestra_stream(
            _p: dict[str, Any], writer: asyncio.StreamWriter
        ) -> None:
            orchestra = manager.orchestra
            if orchestra is None:
                return
            queue = orchestra.watch()
            try:
                while True:
                    beat = await queue.get()
                    notification = {
                        "jsonrpc": "2.0",
                        "method": "orchestra.beat",
                        "params": beat,
                    }
                    writer.write(json.dumps(notification).encode() + b"\n")
                    await asyncio.wait_for(writer.drain(), self._config.socket.write_timeout)
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            finally:
                orchestra.unwatch(queue)

        handler.register("daemon.orchestra.snapshot", handle_orchestra_snapshot)
        handler.register("daemon.orchestra.replay", handle_orchestra_replay)
        handler.register("daemon.orchestra.stream", handle_orchestra_stream, streaming=True)

        # Rate limit and learning IPC methods — used by dashboard bridge
        async def handle_rate_limits(
            _p: dict[str, Any],
            _w: Any,
        ) -> dict[str, Any]:
            coordinator = manager.rate_coordinator
            active = coordinator.active_limits
            return {
                "backends": {k: {"seconds_remaining": v} for k, v in active.items()},
                "active_limits": len(active),
                "recent_events_count": len(coordinator.recent_events),
            }

        async def handle_learning_patterns(
            params: dict[str, Any],
            _w: Any,
        ) -> dict[str, Any]:
            hub = manager.learning_hub
            if not hub.is_running:
                return {"patterns": []}
            limit = params.get("limit", 20)
            store = hub.store
            records = store.get_patterns(min_priority=0.0, limit=limit)
            return {
                "patterns": [
                    {
                        "id": r.id,
                        "pattern_type": r.pattern_type,
                        "pattern_name": r.pattern_name,
                        "description": r.description,
                        "effectiveness_score": r.effectiveness_score,
                        "priority_score": r.priority_score,
                        "occurrence_count": r.occurrence_count,
                        "last_seen": r.last_seen.isoformat(),
                        "quarantine_status": r.quarantine_status.value,
                        "trust_score": r.trust_score,
                    }
                    for r in records
                ],
            }

        async def handle_clear_rate_limits(
            params: dict[str, Any],
            _w: Any,
        ) -> dict[str, Any]:
            instrument = params.get("instrument")
            return await manager.clear_rate_limits(instrument=instrument)

        handler.register("daemon.rate_limits", handle_rate_limits)
        handler.register("daemon.clear_rate_limits", handle_clear_rate_limits)
        handler.register("daemon.learning.patterns", handle_learning_patterns)

    def _track_signal_task(self, task: asyncio.Task[Any]) -> None:
        """Store a signal-spawned task and attach an error callback."""
        self._signal_tasks.append(task)
        task.add_done_callback(self._on_signal_task_done)

    def _on_signal_task_done(self, task: asyncio.Task[Any]) -> None:
        """Log errors from signal handler tasks instead of losing them."""
        self._signal_tasks = [t for t in self._signal_tasks if not t.done()]
        log_task_exception(task, _logger, "daemon.signal_task_failed")

    async def _handle_signal(
        self,
        sig: signal.Signals,
        manager: JobManager,
        server: DaemonServer,
    ) -> None:
        """Handle shutdown signals (SIGTERM, SIGINT).

        Guards against duplicate signals — a second SIGTERM/SIGINT while
        shutdown is already in progress is logged but does not re-enter
        ``manager.shutdown()``.
        """
        if self._signal_received.is_set():
            _logger.info("daemon.signal_ignored_already_shutting_down", signal=sig.name)
            return
        self._signal_received.set()
        _logger.info("daemon.signal_received", signal=sig.name)
        await manager.shutdown(graceful=True)

    async def reload_configuration(self, reason: str) -> ConfigReloadResult:
        """Single process-level reload entry (#408).

        SIGHUP, the ``daemon.reload`` IPC method, and the config file
        watcher all call this. It delegates the actual reload to
        ``JobManager.reload_configuration`` (the one owner of config and
        profile re-reading) and then applies the two process-owned effects:
        syncing this process's config view and reconfiguring the log level
        when the reload says it changed.
        """
        if not hasattr(self, "_manager") or self._manager is None:
            from marianne.daemon.types import ConfigReloadResult

            result = ConfigReloadResult(
                success=False,
                reason=reason,
                config_generation=0,
                error="manager not initialized",
            )
            return result

        result = await self._manager.reload_configuration(
            reason, profile=self._start_profile
        )
        if result.success:
            self._config = self._manager.config
            if "log_level" in result.applied:
                _configure_daemon_logging(
                    self._config,
                    format="json" if self._config.log_file is not None else "console",
                )
        return result

    async def _handle_sighup(self) -> None:
        """Handle SIGHUP by reloading config from disk (#408).

        Thin delegate — the reload orchestration (config re-read, profile
        swap, cap diff, generation bookkeeping) is owned by
        ``JobManager.reload_configuration``; SIGHUP is just one trigger.
        """
        _logger.info("daemon.sighup_reload_start")
        result = await self.reload_configuration("sighup")
        _logger.info(
            "daemon.sighup_reload_complete",
            success=result.success,
            generation=result.config_generation,
            applied=result.applied,
            declined=result.declined,
            error=result.error,
        )


# ─── Helpers ──────────────────────────────────────────────────────────


def _load_config(
    config_file: Path | None,
    profile: str | None = None,
) -> DaemonConfig:
    """Load DaemonConfig from YAML file or return defaults.

    If *config_file* is ``None``, the well-known default location
    ``~/.marianne/conductor.yaml`` is checked automatically.

    When *profile* is given, the named built-in profile is deep-merged
    on top of the base config data before Pydantic validation.
    Resolution order: defaults → config file → profile.

    When loading from a file, the resulting config's ``config_file``
    field is set to the resolved path so that SIGHUP reload knows
    which file to re-read.
    """
    import yaml

    from marianne.daemon.profiles import deep_merge, get_profile

    # Auto-discover default config when none explicitly provided
    if config_file is None:
        default_path = Path("~/.marianne/conductor.yaml").expanduser()
        if default_path.exists():
            config_file = default_path

    data: dict[str, object] = {}
    resolved_config_file: Path | None = None

    if config_file and config_file.exists():
        with open(config_file) as f:
            data = yaml.safe_load(f) or {}
        resolved_config_file = config_file.resolve()

    # Apply profile on top of config file data
    if profile is not None:
        profile_data = get_profile(profile)
        data = deep_merge(data, profile_data)

    # Strip removed fields for backward compatibility with older configs.
    for _removed_field in ("use" + "_baton",):
        data.pop(_removed_field, None)

    config = DaemonConfig.model_validate(data)
    if resolved_config_file is not None:
        config.config_file = resolved_config_file
    return config


def _write_pid(pid_file: Path) -> None:
    """Write current PID to file atomically with advisory lock.

    Uses fcntl.flock() to prevent TOCTOU races when two ``mzt start``
    invocations run concurrently.  Also rejects symlinks to avoid a local
    attacker redirecting the write to an arbitrary file.
    """
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    # Reject symlinks (parity with socket symlink check in DaemonServer)
    if pid_file.is_symlink():
        raise OSError(f"PID file is a symlink (possible attack): {pid_file}")

    tmp = pid_file.with_suffix(".tmp")
    tmp.write_text(str(os.getpid()))
    tmp.rename(pid_file)

    # Advisory lock — held for daemon lifetime (released on fd close / exit)
    # If locking fails, another daemon may already hold the lock, so we
    # MUST abort to prevent two daemons corrupting the same socket.
    try:
        fd = os.open(str(pid_file), os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Store fd so the lock persists until the process exits
        global _pid_lock_fd
        _pid_lock_fd = fd
    except OSError as exc:
        from marianne.daemon.exceptions import DaemonError

        raise DaemonError(
            f"Cannot acquire PID file lock ({pid_file}): {exc}. "
            "Another daemon instance may be starting."
        ) from exc


def _read_pid(pid_file: Path) -> int | None:
    """Read PID from file, returning None if missing or invalid."""
    try:
        return int(pid_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Process exists but we can't signal it


def _resolve_live_pid_file(preferred: Path) -> Path:
    """Resolve the PID file that points at a LIVE conductor (#227).

    Prefers ``preferred`` (the configured/new default). Transitional: when
    the preferred file holds no live PID but the legacy ``/tmp`` PID file
    does — a conductor started before the /tmp → ~/.config/mzt move —
    return the legacy path so ``mzt stop``/``conductor-status`` still see
    it and ``mzt start`` refuses to spawn a second conductor beside it.
    Self-eliminating once the conductor restarts on the new paths.
    """
    pid = _read_pid(preferred)
    if pid is not None and _pid_alive(pid):
        return preferred
    legacy_pid = _read_pid(LEGACY_PID_PATH)
    if legacy_pid is not None and _pid_alive(legacy_pid):
        return LEGACY_PID_PATH
    return preferred


def wait_for_conductor_exit(
    pid_file: Path | None = None,
    timeout: float = 30.0,
) -> bool:
    """Wait for the conductor process to exit.

    Polls ``_pid_alive`` until the process is gone or *timeout* elapses.
    Cleans up the stale PID file once the process is confirmed dead.

    Returns ``True`` if the process exited within the timeout.
    """
    # Explicit pid_file always wins; only the default probes legacy (#227).
    resolved = (
        pid_file
        if pid_file is not None
        else _resolve_live_pid_file(DaemonConfig().pid_file)
    )
    pid = _read_pid(resolved)
    if pid is None:
        return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            resolved.unlink(missing_ok=True)
            return True
        time.sleep(0.1)
    return False


def _daemonize(config: DaemonConfig) -> None:
    """Double-fork to detach from terminal."""
    # First fork
    pid = os.fork()
    if pid > 0:
        sys.exit(0)  # Parent exits

    # New session
    os.setsid()

    # Second fork
    pid = os.fork()
    if pid > 0:
        sys.exit(0)  # First child exits

    # Redirect stdio to /dev/null
    devnull = os.open(os.devnull, os.O_RDWR)
    try:
        os.dup2(devnull, 0)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
    finally:
        os.close(devnull)

    _logger.info(
        "daemon.daemonized",
        pid=os.getpid(),
        sid=os.getsid(0),
    )


__all__ = [
    "DaemonProcess",
    "start_conductor",
    "stop_conductor",
    "get_conductor_status",
    "wait_for_conductor_exit",
]
