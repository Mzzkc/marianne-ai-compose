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
import os
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import typer

from marianne.core.logging import get_logger
from marianne.daemon.config import DaemonConfig
from marianne.daemon.pgroup import ProcessGroupManager
from marianne.daemon.task_utils import log_task_exception

if TYPE_CHECKING:
    from marianne.daemon.health import HealthChecker
    from marianne.daemon.ipc.handler import RequestHandler
    from marianne.daemon.ipc.server import DaemonServer
    from marianne.daemon.manager import JobManager
    from marianne.daemon.types import ObserverEvent

_logger = get_logger("conductor")

# Advisory lock file descriptor — held for daemon lifetime to prevent
# concurrent starts.  Set by _write_pid(), released on process exit.
_pid_lock_fd: int | None = None


# ─── Core Functions (used by cli/commands/conductor.py) ───────────────


def start_conductor(
    config_file: Path | None = None,
    foreground: bool = False,
    log_level: str = "info",
    profile: str | None = None,
    clone_name: str | None = None,
) -> None:
    """Start the Marianne conductor process.

    Called by ``mzt start`` via ``cli/commands/conductor.py``.

    When *clone_name* is provided, the conductor runs with isolated
    paths (socket, PID file, state DB, log) so it can coexist with the
    production conductor.  The base config is loaded normally, then
    clone-specific paths are applied on top.
    """
    config = _load_config(config_file, profile=profile)
    config.log_level = cast(Any, log_level)

    # Apply clone path overrides when running as a clone conductor
    if clone_name is not None:
        from marianne.daemon.clone import resolve_clone_paths

        clone_paths = resolve_clone_paths(clone_name)
        config_dict = config.model_dump()
        config_dict["socket"] = {"path": str(clone_paths.socket)}
        config_dict["pid_file"] = str(clone_paths.pid_file)
        config_dict["state_db_path"] = str(clone_paths.state_db)
        config = DaemonConfig.model_validate(config_dict)
        config.log_level = cast(Any, log_level)
        config.log_file = clone_paths.log_file

    pid = _read_pid(config.pid_file)
    if pid is not None:
        if _pid_alive(pid):
            label = (
                "clone conductor"
                if clone_name is not None
                else "Marianne conductor"
            )
            typer.echo(f"{label} is already running (PID {pid})")
            raise typer.Exit(1)
        else:
            # Stale PID file — process is dead, clean up before starting
            typer.echo(
                f"Cleaned up stale PID file "
                f"(PID {pid} is no longer running)"
            )
            config.pid_file.unlink(missing_ok=True)

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

    from marianne.core.logging import configure_logging

    log_fmt = "console" if foreground else "json"
    configure_logging(
        level=log_level.upper(),  # type: ignore[arg-type]
        format=log_fmt,  # type: ignore[arg-type]
        file_path=config.log_file,
        include_timestamps=True,
    )

    if not foreground:
        _daemonize(config)
    else:
        _logger.info(
            "daemon.starting",
            pid=os.getpid(),
            foreground=True,
            clone=clone_name,
        )

    daemon = DaemonProcess(config)
    asyncio.run(daemon.run())


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
        try:
            client = DaemonClient(resolved, timeout=5.0)
            ready = await client.readiness()
            running = ready.get("running_jobs", 0)
            return {
                "running_jobs": running,
                "job_ids": ready.get("job_ids", []),
            }
        except Exception:
            return None

    try:
        return asyncio.run(_probe())
    except Exception:
        return None


def stop_conductor(
    pid_file: Path | None = None,
    force: bool = False,
    socket_path: Path | None = None,
) -> None:
    """Stop the running Marianne conductor (daemon) process.

    Called by ``mzt stop`` via ``cli/commands/conductor.py``.

    When jobs are actively running and ``--force`` is not set, warns
    the user and asks for confirmation before proceeding (#94).
    Force mode (``--force``) skips the safety check entirely.
    """
    resolved_pid_file = pid_file or DaemonConfig().pid_file
    pid = _read_pid(resolved_pid_file)
    if pid is None or not _pid_alive(pid):
        typer.echo("Marianne conductor is not running")
        resolved_pid_file.unlink(missing_ok=True)
        raise typer.Exit(1)

    # Safety guard: check for running jobs unless --force (#94)
    if not force:
        running_info = _check_running_jobs(socket_path)
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


def get_conductor_status(
    pid_file: Path | None = None,
    socket_path: Path | None = None,
) -> None:
    """Check Marianne conductor (daemon) status via health probes.

    Called by ``mzt conductor-status`` via ``cli/commands/conductor.py``.
    """
    _defaults = DaemonConfig()
    resolved_pid_file = pid_file or _defaults.pid_file
    resolved_socket = socket_path or _defaults.socket.path

    pid = _read_pid(resolved_pid_file)
    if pid is None or not _pid_alive(pid):
        typer.echo("Marianne conductor is not running")
        resolved_pid_file.unlink(missing_ok=True)
        raise typer.Exit(1)

    typer.echo(f"Marianne conductor is running (PID {pid})")

    from marianne.daemon.ipc.client import DaemonClient

    client = DaemonClient(resolved_socket)

    from marianne.daemon.exceptions import DaemonError

    async def _probe(method: str) -> dict[str, Any] | None:
        try:
            result: dict[str, Any] = await client.call(method)
            return result
        except (OSError, DaemonError) as e:
            _logger.info(f"probe.{method.split('.')[-1]}_failed", error=str(e))
            return None

    async def _get_health() -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        health = await _probe("daemon.health")
        ready = await _probe("daemon.ready")
        daemon_info = await _probe("daemon.status")
        return health, ready, daemon_info

    try:
        health, ready, daemon_info = asyncio.run(_get_health())
    except (OSError, DaemonError):
        typer.echo("  (Could not connect to conductor socket for details)")
        return

    from rich.console import Console
    from rich.panel import Panel

    con = Console()

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
    lines.append(f"PID {pid} [dim]\u00b7[/dim] Up {uptime_str}" if uptime_str else f"PID {pid}")

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

    def __init__(self, config: DaemonConfig) -> None:
        self._config = config
        self._signal_received = asyncio.Event()
        self._pgroup = ProcessGroupManager()
        self._start_time = time.monotonic()
        self._signal_tasks: list[asyncio.Task[Any]] = []
        self._profiler: Any = None  # Set in run() step 8.5
        self._correlation: Any = None  # Set in run() step 8.6

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
                self._config.resource_limits, pgroup=self._pgroup,
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
                    self._config.resource_limits.max_api_calls_per_minute, 60,
                    "config.unenforced_rate_limit",
                    "max_api_calls_per_minute is set but NOT YET ENFORCED. "
                    "Rate limiting currently works through externally-reported "
                    "events via RateLimitCoordinator.",
                ),
                (
                    "state_backend_type",
                    self._config.state_backend_type, "sqlite",
                    "config.reserved_field_ignored",
                    "state_backend_type is reserved for future use and has no "
                    "effect. Daemon state persistence is not yet implemented.",
                ),
                (
                    "state_db_path",
                    str(self._config.state_db_path), "~/.marianne/daemon-state.db",
                    "config.reserved_field_ignored",
                    "state_db_path is reserved for future use and has no "
                    "effect. Daemon state persistence is not yet implemented.",
                ),
                (
                    "max_concurrent_sheets",
                    self._config.max_concurrent_sheets, 10,
                    "config.reserved_field_ignored",
                    "max_concurrent_sheets is reserved for Phase 3 scheduler "
                    "and has no effect. Jobs currently run monolithically "
                    "via JobService.",
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
                    self._manager._learning_hub.store
                    if self._manager._learning_hub
                    else None
                ),
            )

            # 5. Register RPC methods (adapt JobManager to handler signature)
            self._register_methods(handler, self._manager, health)

            # 6. Start server
            server = DaemonServer(
                self._config.socket.path,
                handler,
                permissions=self._config.socket.permissions,
                max_connections=self._config.socket.backlog,
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

            # 9. Run until shutdown
            _logger.info(
                "daemon.started",
                pid=os.getpid(),
                socket=str(self._config.socket.path),
            )
            await self._manager.wait_for_shutdown()

            # 10. Cleanup — correlation, profiler, entropy, monitor
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

        def _workspace_path(raw: str | None) -> Path | None:
            return Path(raw) if raw else None

        async def handle_submit(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            request = JobRequest(**params)
            response = await manager.submit_job(request)
            return response.model_dump()

        async def handle_job_status(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            # Let JobSubmissionError propagate — the JSON-RPC protocol maps it
            # to a JOB_NOT_FOUND error code, and the client re-raises it.
            return await manager.get_job_status(
                params["job_id"], _workspace_path(params.get("workspace")),
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
                response = await manager.resume_job(
                    params["job_id"],
                    _workspace_path(params.get("workspace")),
                    config_path=config_path,
                    no_reload=no_reload,
                )
                return response.model_dump()
            except JobSubmissionError as e:
                return {"job_id": params.get("job_id", ""), "status": "rejected", "message": str(e)}

        async def handle_cancel(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            ok = await manager.cancel_job(params["job_id"])
            return {"cancelled": ok}

        async def handle_list(_p: dict[str, Any], _w: Any) -> list[dict[str, Any]]:
            return await manager.list_jobs()

        async def handle_daemon_status(_p: dict[str, Any], _w: Any) -> dict[str, Any]:
            return await manager.get_daemon_status()

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
                params["job_id"], _workspace_path(params.get("workspace")),
            )

        async def handle_diagnose(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            return await manager.get_diagnostic_report(
                params["job_id"], _workspace_path(params.get("workspace")),
            )

        async def handle_history(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            return await manager.get_execution_history(
                params["job_id"], _workspace_path(params.get("workspace")),
                sheet_num=params.get("sheet_num"),
                limit=params.get("limit", 50),
            )

        async def handle_recover(params: dict[str, Any], _w: Any) -> dict[str, Any]:
            return await manager.recover_job(
                params["job_id"], _workspace_path(params.get("workspace")),
                sheet_num=params.get("sheet_num"),
                dry_run=params.get("dry_run", False),
            )

        async def handle_config(_p: dict[str, Any], _w: Any) -> dict[str, Any]:
            return self._config.model_dump(mode="json")

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
        handler.register("job.modify", handle_modify)
        handler.register("job.cancel", handle_cancel)
        handler.register("job.list", handle_list)
        handler.register("job.clear", handle_clear_jobs)
        handler.register("job.errors", handle_errors)
        handler.register("job.diagnose", handle_diagnose)
        handler.register("job.history", handle_history)
        handler.register("job.recover", handle_recover)

        handler.register("daemon.status", handle_daemon_status)
        handler.register("daemon.shutdown", handle_shutdown)
        handler.register("daemon.config", handle_config)

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
                    await writer.drain()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            finally:
                manager.event_bus.unsubscribe(sub_id)

        handler.register("daemon.top", handle_top)
        handler.register("daemon.top.stream", handle_top_stream)
        handler.register("daemon.events", handle_events)
        handler.register("daemon.monitor.stream", handle_monitor_stream)

        # Observer event recorder IPC — per-job behavioral events
        async def handle_observer_events(
            params: dict[str, Any], _w: Any,
        ) -> dict[str, Any]:
            if manager.observer_recorder is None:
                return {"events": []}
            job_id = params.get("job_id")
            limit = params.get("limit", 50)
            events = manager.observer_recorder.get_recent_events(
                job_id, limit=limit,
            )
            return {"events": events}

        handler.register("daemon.observer_events", handle_observer_events)

        # Rate limit and learning IPC methods — used by dashboard bridge
        async def handle_rate_limits(
            _p: dict[str, Any], _w: Any,
        ) -> dict[str, Any]:
            coordinator = manager.rate_coordinator
            active = coordinator.active_limits
            return {
                "backends": {
                    k: {"seconds_remaining": v} for k, v in active.items()
                },
                "active_limits": len(active),
                "recent_events_count": len(coordinator.recent_events),
            }

        async def handle_learning_patterns(
            params: dict[str, Any], _w: Any,
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
            params: dict[str, Any], _w: Any,
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

    async def _handle_sighup(self) -> None:
        """Handle SIGHUP by reloading config from disk.

        Re-reads the config file and hot-applies reloadable fields to
        running components.  Non-reloadable fields (socket.*, pid_file)
        are detected and logged as warnings.
        """
        config_file = self._config.config_file
        if config_file is None:
            _logger.warning(
                "daemon.sighup_no_config_file",
                message="No config file recorded — started with defaults. "
                "SIGHUP reload has no effect.",
            )
            return

        _logger.info("daemon.sighup_reload_start", config_file=str(config_file))

        try:
            new_config = _load_config(config_file)
        except Exception:
            _logger.exception(
                "daemon.sighup_reload_failed",
                config_file=str(config_file),
                message="Config reload failed — keeping current config.",
            )
            return

        # Warn about non-reloadable field changes
        _non_reloadable = [
            ("socket.path", self._config.socket.path, new_config.socket.path),
            ("socket.permissions", self._config.socket.permissions, new_config.socket.permissions),
            ("socket.backlog", self._config.socket.backlog, new_config.socket.backlog),
            ("pid_file", self._config.pid_file, new_config.pid_file),
        ]
        for field_name, old_val, new_val in _non_reloadable:
            if old_val != new_val:
                _logger.warning(
                    "daemon.sighup_non_reloadable_changed",
                    field=field_name,
                    old_value=str(old_val),
                    new_value=str(new_val),
                    message=f"{field_name} changed but requires restart to take effect.",
                )

        # Hot-apply reloadable fields
        if hasattr(self, '_manager') and self._manager is not None:
            self._manager.apply_config(new_config)

        if hasattr(self, '_monitor') and self._monitor is not None:
            self._monitor.update_limits(new_config.resource_limits)

        # Reconfigure logging if log_level or log_file changed
        if (
            new_config.log_level != self._config.log_level
            or new_config.log_file != self._config.log_file
        ):
            from marianne.core.logging import configure_logging

            configure_logging(
                level=new_config.log_level.upper(),  # type: ignore[arg-type]
                file_path=new_config.log_file,
                include_timestamps=True,
            )
            _logger.info(
                "daemon.sighup_logging_changed",
                old_level=self._config.log_level,
                new_level=new_config.log_level,
                old_log_file=str(self._config.log_file),
                new_log_file=str(new_config.log_file),
            )

        self._config = new_config
        _logger.info("daemon.sighup_reload_complete")


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


def wait_for_conductor_exit(
    pid_file: Path | None = None,
    timeout: float = 30.0,
) -> bool:
    """Wait for the conductor process to exit.

    Polls ``_pid_alive`` until the process is gone or *timeout* elapses.
    Cleans up the stale PID file once the process is confirmed dead.

    Returns ``True`` if the process exited within the timeout.
    """
    resolved = pid_file or DaemonConfig().pid_file
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
