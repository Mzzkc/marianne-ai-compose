"""Job lifecycle control service."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mozart.core.checkpoint import CheckpointState, JobStatus
from mozart.core.config import JobConfig
from mozart.core.logging import get_logger
from mozart.daemon.exceptions import DaemonNotRunningError
from mozart.daemon.ipc.client import DaemonClient
from mozart.daemon.types import JobRequest
from mozart.state.base import StateBackend

logger = get_logger("job_control")


@dataclass
class JobStartResult:
    """Result of starting a job."""
    job_id: str
    job_name: str
    status: str
    workspace: Path
    total_sheets: int
    pid: int | None = None
    via_daemon: bool = False


@dataclass
class JobActionResult:
    """Result of a job action (pause/resume/cancel)."""
    success: bool
    job_id: str
    status: str
    message: str
    via_daemon: bool = False


@dataclass
class ProcessHealth:
    """Process health check result."""
    pid: int | None
    is_alive: bool
    is_zombie_state: bool
    process_exists: bool
    cpu_percent: float | None = None
    memory_mb: float | None = None
    uptime_seconds: float | None = None


class JobControlService:
    """Service for controlling job lifecycle.

    Supports two execution modes:
    - **Conductor mode**: When the conductor is running, routes operations through
      it via DaemonClient IPC for centralized management.
    - **Subprocess mode**: Falls back to direct subprocess execution
      when the daemon is not available (original behavior).
    """

    def __init__(self, state_backend: StateBackend, workspace_root: Path | None = None):
        self._state_backend = state_backend
        self._workspace_root = workspace_root or Path.cwd()
        self._running_processes: dict[str, asyncio.subprocess.Process] = {}
        self._process_start_times: dict[str, float] = {}  # Track when processes started
        try:
            from mozart.daemon.config import DaemonConfig
            self._daemon_client = DaemonClient(DaemonConfig().socket.path)
        except Exception:
            from pathlib import Path as _Path
            default_socket = _Path.home() / ".mozart" / "daemon.sock"
            self._daemon_client = DaemonClient(default_socket)

    async def is_daemon_available(self) -> bool:
        """Check if the Mozart conductor is running and reachable."""
        try:
            return await self._daemon_client.is_daemon_running()
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            # Expected: daemon socket doesn't exist or isn't accepting connections
            return False
        except Exception:
            logger.warning("daemon_availability_check_failed", exc_info=True)
            return False

    async def start_job(
        self,
        config_path: Path | None = None,
        config_content: str | None = None,
        workspace: Path | None = None,
        start_sheet: int = 1,
        self_healing: bool = False,
    ) -> JobStartResult:
        """Start a new Mozart job execution.

        Tries the daemon first if available, then falls back to subprocess.

        Args:
            config_path: Path to YAML config file.
            config_content: Inline YAML config content.
            workspace: Override workspace directory.
            start_sheet: Starting sheet number.
            self_healing: Enable self-healing mode.

        Returns:
            JobStartResult with job details and process info.

        Raises:
            ValueError: If neither config_path nor config_content provided.
            FileNotFoundError: If config_path doesn't exist.
            RuntimeError: If job execution fails to start.
        """
        if not config_path and not config_content:
            raise ValueError("Must provide either config_path or config_content")

        if config_path:
            resolved = config_path.resolve()
            # Validate suffix to prevent arbitrary file execution
            if resolved.suffix not in ('.yaml', '.yml'):
                raise ValueError(
                    f"Config path must be a YAML file (.yaml/.yml): {config_path}"
                )
            # Path traversal check — reject paths containing ".." components
            # to prevent directory traversal attacks (e.g., "../../../etc/shadow.yaml").
            # We check the original path's parts, not the resolved path, to catch
            # deliberate traversal attempts even when the resolved target exists.
            if ".." in config_path.parts:
                raise ValueError(
                    f"Config path must not contain '..' traversal: {config_path}"
                )
            if not resolved.exists():
                raise FileNotFoundError(f"Config file not found: {config_path}")

        # Try daemon first (only for file-based configs — daemon doesn't support inline)
        if config_path and await self.is_daemon_available():
            try:
                return await self._start_job_via_daemon(
                    config_path, workspace, self_healing,
                )
            except DaemonNotRunningError:
                logger.debug("daemon_unavailable_during_start", fallback="subprocess")

        # Generate unique job ID
        job_id = str(uuid.uuid4())[:8]  # Short ID for readability

        temp_path: str | None = None
        try:
            # Build command arguments - using create_subprocess_exec for security
            cmd_args = [sys.executable, "-m", "mozart.cli", "run"]

            if config_content:
                # For inline content, we'll write to a temp file
                # This ensures proper YAML parsing through the CLI
                import tempfile
                temp_fd, temp_path = tempfile.mkstemp(suffix='.yaml', text=True)
                os.fchmod(temp_fd, 0o600)
                try:
                    with open(temp_fd, 'w') as f:
                        f.write(config_content)
                    cmd_args.append(temp_path)
                except Exception:
                    os.close(temp_fd)
                    raise
            else:
                cmd_args.append(str(config_path))

            # Add optional arguments
            if workspace:
                cmd_args.extend(["--workspace", str(workspace)])
            if start_sheet > 1:
                cmd_args.extend(["--start-sheet", str(start_sheet)])
            if self_healing:
                cmd_args.append("--self-healing")

            # Start subprocess with individual arguments (no shell=True)
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace_root),
            )

            # Clean up temp file after subprocess has had time to read it
            if temp_path is not None:
                async def _cleanup_temp(path: str) -> None:
                    await asyncio.sleep(5)  # Give subprocess time to read config
                    try:
                        os.unlink(path)
                    except OSError as exc:
                        logger.warning("temp_config_cleanup_failed", path=path, error=str(exc))

                asyncio.create_task(_cleanup_temp(temp_path))

            # Track the process and its start time
            self._running_processes[job_id] = process
            self._process_start_times[job_id] = time.time()

            # Parse config to get job details
            if config_content:
                config = JobConfig.from_yaml_string(config_content)
            else:
                # config_path is guaranteed to exist due to earlier check
                if config_path is None:
                    raise ValueError("config_path must not be None when config_content is empty")
                config = JobConfig.from_yaml(config_path)

            # Determine workspace
            if not workspace:
                workspace = Path(config.workspace) if config.workspace else self._workspace_root

            # Create and save initial state to backend for dashboard tracking
            # The runner will update this state as it executes.
            # Store resolved workspace in worktree_path so _get_job_workspace()
            # can find it later (prevents pause signals landing in cwd).
            initial_state = CheckpointState(
                job_id=job_id,
                job_name=config.name,
                total_sheets=config.sheet.total_sheets,
                status=JobStatus.RUNNING,
                pid=process.pid,
                started_at=datetime.now(),
                worktree_path=str(workspace.resolve()),
            )
            await self._state_backend.save(initial_state)

            logger.info(
                "job_started",
                job_id=job_id,
                job_name=config.name,
                pid=process.pid,
                workspace=str(workspace),
                total_sheets=config.sheet.total_sheets,
                start_sheet=start_sheet,
                self_healing=self_healing,
            )

            return JobStartResult(
                job_id=job_id,
                job_name=config.name,
                status=JobStatus.RUNNING.value,
                workspace=workspace,
                total_sheets=config.sheet.total_sheets,
                pid=process.pid,
            )

        except Exception as e:
            # Clean up temp config file immediately on failure, since the
            # fire-and-forget cleanup task may not run if we're raising.
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass  # Best-effort cleanup
                temp_path = None  # Prevent double-cleanup

            # Clean up on failure
            if job_id in self._running_processes:
                process = self._running_processes.pop(job_id)
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except (TimeoutError, ProcessLookupError) as cleanup_err:
                    logger.warning(
                        "job_cleanup_failed",
                        job_id=job_id,
                        error=str(cleanup_err),
                    )

            logger.error(
                "job_start_failed",
                job_id=job_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise RuntimeError(f"Failed to start job: {e}") from e

    # ------------------------------------------------------------------
    # Daemon-routed operations
    # ------------------------------------------------------------------

    async def _start_job_via_daemon(
        self,
        config_path: Path,
        workspace: Path | None,
        self_healing: bool,
    ) -> JobStartResult:
        """Submit a job to the daemon and return a JobStartResult."""
        request = JobRequest(
            config_path=config_path.resolve(),
            workspace=workspace.resolve() if workspace else None,
            self_healing=self_healing,
        )
        response = await self._daemon_client.submit_job(request)

        # Parse the config to get job metadata for the result
        config = JobConfig.from_yaml(config_path)
        if workspace:
            ws = workspace
        elif config.workspace:
            ws = Path(config.workspace)
        else:
            ws = self._workspace_root

        logger.info(
            "job_started_via_daemon",
            job_id=response.job_id,
            job_name=config.name,
            status=response.status,
        )

        return JobStartResult(
            job_id=response.job_id,
            job_name=config.name,
            status=response.status,
            workspace=ws,
            total_sheets=config.sheet.total_sheets,
            via_daemon=True,
        )

    async def _pause_job_via_daemon(self, job_id: str, workspace: Path) -> JobActionResult:
        """Pause a job via the daemon."""
        await self._daemon_client.pause_job(job_id, str(workspace))
        return JobActionResult(
            success=True,
            job_id=job_id,
            status=JobStatus.RUNNING.value,
            message=f"Pause request sent to daemon for job {job_id}",
            via_daemon=True,
        )

    async def _resume_job_via_daemon(self, job_id: str, workspace: Path) -> JobActionResult:
        """Resume a job via the daemon."""
        await self._daemon_client.resume_job(job_id, str(workspace))
        return JobActionResult(
            success=True,
            job_id=job_id,
            status=JobStatus.RUNNING.value,
            message=f"Resume request sent to daemon for job {job_id}",
            via_daemon=True,
        )

    async def _cancel_job_via_daemon(self, job_id: str, workspace: Path) -> JobActionResult:
        """Cancel a job via the daemon."""
        await self._daemon_client.cancel_job(job_id, str(workspace))
        return JobActionResult(
            success=True,
            job_id=job_id,
            status=JobStatus.CANCELLED.value,
            message=f"Cancel request sent to daemon for job {job_id}",
            via_daemon=True,
        )

    # ------------------------------------------------------------------
    # Workspace resolution
    # ------------------------------------------------------------------

    def _get_job_workspace(self, state: CheckpointState) -> Path:
        """Get the workspace path for a job state.

        Args:
            state: Job checkpoint state.

        Returns:
            Path to the job's workspace directory.
        """
        # Use worktree path if available (for isolated jobs, or set by dashboard start_job)
        if state.worktree_path:
            return Path(state.worktree_path)

        # Extract workspace from config snapshot (stored by runner on first save)
        if state.config_snapshot and "workspace" in state.config_snapshot:
            ws = Path(state.config_snapshot["workspace"])
            if ws.is_absolute() and ws.exists():
                return ws

        # Fall back to workspace root
        return self._workspace_root

    async def pause_job(self, job_id: str) -> JobActionResult:
        """Pause a running job gracefully using signal files.

        Routes through daemon if available, otherwise uses local signal files.

        Args:
            job_id: Job identifier.

        Returns:
            JobActionResult with operation status.
        """
        # Try daemon first
        state = await self._state_backend.load(job_id)
        if state and await self.is_daemon_available():
            try:
                workspace = self._get_job_workspace(state)
                return await self._pause_job_via_daemon(job_id, workspace)
            except DaemonNotRunningError:
                logger.debug("daemon_unavailable_during_pause", fallback="local")

        if not state:
            return JobActionResult(
                success=False,
                job_id=job_id,
                status=JobStatus.FAILED.value,
                message=f"Job not found: {job_id}"
            )

        if state.status != JobStatus.RUNNING:
            return JobActionResult(
                success=False,
                job_id=job_id,
                status=state.status.value,
                message=f"Job is not running (status: {state.status.value})"
            )

        # Create pause signal file in job's workspace
        try:
            workspace_path = self._get_job_workspace(state)
            pause_signal_file = workspace_path / f".mozart-pause-{job_id}"

            # Create the pause signal file
            pause_signal_file.touch()

            logger.info(
                "job_pause_requested",
                job_id=job_id,
                workspace=str(workspace_path),
                signal_file=str(pause_signal_file),
            )

            return JobActionResult(
                success=True,
                job_id=job_id,
                status=JobStatus.RUNNING.value,  # Still running until runner processes signal
                message=f"Pause request sent to job {job_id}. "
                        f"Job will pause at next sheet boundary."
            )

        except OSError as e:
            logger.error(
                "pause_signal_creation_failed",
                job_id=job_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return JobActionResult(
                success=False,
                job_id=job_id,
                status=state.status.value,
                message=f"Failed to create pause signal: {e}"
            )

    async def resume_job(self, job_id: str) -> JobActionResult:
        """Resume a paused job and clean up pause signals.

        Routes through daemon if available, otherwise uses local process management.

        Args:
            job_id: Job identifier.

        Returns:
            JobActionResult with operation status.
        """
        # Try daemon first
        state = await self._state_backend.load(job_id)
        if state and await self.is_daemon_available():
            try:
                workspace = self._get_job_workspace(state)
                return await self._resume_job_via_daemon(job_id, workspace)
            except DaemonNotRunningError:
                logger.debug("daemon_unavailable_during_resume", fallback="local")

        if not state:
            return JobActionResult(
                success=False,
                job_id=job_id,
                status=JobStatus.FAILED.value,
                message=f"Job not found: {job_id}"
            )

        if state.status != JobStatus.PAUSED:
            return JobActionResult(
                success=False,
                job_id=job_id,
                status=state.status.value,
                message=f"Job is not paused (status: {state.status.value})"
            )

        pid = await self.get_job_pid(job_id)
        if not pid:
            # Try to restart the job instead of resuming a dead process
            return await self._restart_job_execution(job_id, state)

        try:
            # Clean up pause signal files (non-blocking - failure shouldn't block resume)
            workspace_path = self._get_job_workspace(state)
            pause_signal_file = workspace_path / f".mozart-pause-{job_id}"
            signal_cleaned = False
            if pause_signal_file.exists():
                try:
                    pause_signal_file.unlink()
                    signal_cleaned = True
                except OSError:
                    logger.debug("Failed to clean pause signal on resume", exc_info=True)

            # Update state to running
            state.status = JobStatus.RUNNING
            state.updated_at = datetime.now()
            await self._state_backend.save(state)

            logger.info(
                "job_resumed",
                job_id=job_id,
                pid=pid,
                pause_signal_cleaned=signal_cleaned,
            )

            return JobActionResult(
                success=True,
                job_id=job_id,
                status=JobStatus.RUNNING.value,
                message=f"Job {job_id} resumed successfully"
            )

        except ProcessLookupError:
            # Process is dead - clean up signals and attempt restart
            workspace_path = self._get_job_workspace(state)
            pause_signal_file = workspace_path / f".mozart-pause-{job_id}"
            if pause_signal_file.exists():
                try:
                    pause_signal_file.unlink()
                except OSError:
                    logger.debug(
                        "Failed to clean pause signal on dead process resume",
                        exc_info=True,
                    )
            return await self._restart_job_execution(job_id, state)

        except PermissionError:
            return JobActionResult(
                success=False,
                job_id=job_id,
                status=state.status.value,
                message=f"Permission denied to resume job {job_id}"
            )

        except OSError as e:
            logger.error(
                "job_resume_failed",
                job_id=job_id,
                pid=pid,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return JobActionResult(
                success=False,
                job_id=job_id,
                status=state.status.value,
                message=f"Failed to resume job: {e}"
            )

    async def cancel_job(self, job_id: str) -> JobActionResult:
        """Cancel a job by sending SIGTERM.

        Routes through daemon if available, otherwise uses local SIGTERM.

        Args:
            job_id: Job identifier.

        Returns:
            JobActionResult with operation status.
        """
        # Try daemon first
        state = await self._state_backend.load(job_id)
        if state and await self.is_daemon_available():
            try:
                workspace = self._get_job_workspace(state)
                return await self._cancel_job_via_daemon(job_id, workspace)
            except DaemonNotRunningError:
                logger.debug("daemon_unavailable_during_cancel", fallback="local")

        if not state:
            return JobActionResult(
                success=False,
                job_id=job_id,
                status=JobStatus.FAILED.value,
                message=f"Job not found: {job_id}"
            )

        if state.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
            return JobActionResult(
                success=False,
                job_id=job_id,
                status=state.status.value,
                message=f"Job already finished (status: {state.status.value})"
            )

        pid = await self.get_job_pid(job_id)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)

                # Give process time to handle SIGTERM gracefully
                await asyncio.sleep(2.0)

                # Check if process is still running
                try:
                    os.kill(pid, 0)  # Check if process exists
                    # Still running - send SIGKILL
                    os.kill(pid, signal.SIGKILL)
                    logger.warning(
                        "job_force_killed",
                        job_id=job_id,
                        pid=pid,
                        reason="SIGTERM timeout"
                    )
                except ProcessLookupError:
                    # Process exited gracefully
                    pass

            except ProcessLookupError:
                # Process already dead
                pass
            except (PermissionError, OSError) as e:
                logger.error(
                    "job_kill_failed",
                    job_id=job_id,
                    pid=pid,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

        # Update state to cancelled regardless of kill success
        state.status = JobStatus.CANCELLED
        state.completed_at = datetime.now()
        state.updated_at = datetime.now()
        state.pid = None  # Clear PID
        await self._state_backend.save(state)

        # Clean up any pause signal files
        try:
            workspace_path = self._get_job_workspace(state)
            pause_signal_file = workspace_path / f".mozart-pause-{job_id}"
            if pause_signal_file.exists():
                pause_signal_file.unlink()
        except OSError:
            logger.debug("Failed to clean pause signal on cancel", exc_info=True)

        # Clean up process tracking
        self._running_processes.pop(job_id, None)
        self._process_start_times.pop(job_id, None)

        logger.info(
            "job_cancelled",
            job_id=job_id,
            pid=pid,
        )

        return JobActionResult(
            success=True,
            job_id=job_id,
            status=JobStatus.CANCELLED.value,
            message=f"Job {job_id} cancelled successfully"
        )

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job record from state.

        Args:
            job_id: Job identifier.

        Returns:
            True if deleted, False if not found or still running.
        """
        state = await self._state_backend.load(job_id)
        if not state:
            return False

        # Prevent deletion of running jobs
        if state.status == JobStatus.RUNNING:
            pid = await self.get_job_pid(job_id)
            if pid:
                try:
                    os.kill(pid, 0)  # Check if process exists
                    # Process is still running
                    logger.warning(
                        "delete_blocked_running_job",
                        job_id=job_id,
                        pid=pid,
                    )
                    return False
                except ProcessLookupError:
                    # Process is dead but state shows running - safe to delete
                    pass

        # Delete from backend
        deleted = await self._state_backend.delete(job_id)

        # Clean up process tracking
        self._running_processes.pop(job_id, None)
        self._process_start_times.pop(job_id, None)

        if deleted:
            logger.info(
                "job_deleted",
                job_id=job_id,
                status=state.status.value,
            )

        return deleted

    async def get_job_pid(self, job_id: str) -> int | None:
        """Get PID of running job process.

        Args:
            job_id: Job identifier.

        Returns:
            Process ID if found and alive, None otherwise.
        """
        # Check tracked processes first
        if job_id in self._running_processes:
            process = self._running_processes[job_id]
            if process.returncode is None:  # Process still running
                return process.pid
            # Process has exited — evict stale tracking entry
            del self._running_processes[job_id]
            self._process_start_times.pop(job_id, None)

        # Check state backend
        state = await self._state_backend.load(job_id)
        if state and state.pid:
            try:
                os.kill(state.pid, 0)  # Check if process exists
                return state.pid
            except ProcessLookupError:
                # Process is dead but recorded in state
                logger.debug(
                    "dead_pid_in_state",
                    job_id=job_id,
                    pid=state.pid,
                )
                return None

        return None

    async def verify_process_health(self, job_id: str) -> ProcessHealth:
        """Verify comprehensive health of a job's process.

        Performs deep process verification including:
        - PID existence and liveness
        - Zombie state detection via CheckpointState
        - Process metrics (CPU, memory if available)
        - Uptime calculation

        Args:
            job_id: Job identifier.

        Returns:
            ProcessHealth result with comprehensive process status.
        """
        # Get PID from various sources
        pid = await self.get_job_pid(job_id)

        # Get state for zombie detection
        state = await self._state_backend.load(job_id)
        is_zombie_state = state.is_zombie() if state else False

        # Check if process exists in system
        process_exists = False
        if pid:
            try:
                os.kill(pid, 0)  # Signal 0 checks existence
                process_exists = True
            except (ProcessLookupError, PermissionError, OSError):
                process_exists = False

        # Calculate uptime if we have start time
        uptime_seconds = None
        if job_id in self._process_start_times and process_exists:
            uptime_seconds = time.time() - self._process_start_times[job_id]

        # Try to get process metrics (optional, may fail due to permissions)
        cpu_percent = None
        memory_mb = None
        if pid and process_exists:
            try:
                # Use psutil if available for detailed metrics
                import psutil
            except ImportError:
                # psutil not available - skip metrics collection
                pass
            else:
                try:
                    proc = psutil.Process(pid)
                    cpu_percent = proc.cpu_percent(interval=0.1)
                    memory_mb = proc.memory_info().rss / (1024 * 1024)  # Convert bytes to MB
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    # Process metrics inaccessible (permissions, no such process, zombie, etc.)
                    pass

        is_alive = process_exists and not is_zombie_state

        logger.debug(
            "process_health_checked",
            job_id=job_id,
            pid=pid,
            is_alive=is_alive,
            is_zombie_state=is_zombie_state,
            process_exists=process_exists,
            uptime_seconds=uptime_seconds,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
        )

        return ProcessHealth(
            pid=pid,
            is_alive=is_alive,
            is_zombie_state=is_zombie_state,
            process_exists=process_exists,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            uptime_seconds=uptime_seconds,
        )

    async def cleanup_orphaned_processes(self) -> list[str]:
        """Clean up orphaned process references.

        Removes entries from _running_processes where the process has died
        but we're still tracking it. This prevents memory leaks and stale
        references.

        Returns:
            List of job IDs that had orphaned processes cleaned up.
        """
        orphaned_jobs = []

        for job_id, process in list(self._running_processes.items()):
            if process.returncode is not None:  # Process has exited
                del self._running_processes[job_id]
                self._process_start_times.pop(job_id, None)
                orphaned_jobs.append(job_id)

                logger.info(
                    "orphaned_process_cleaned",
                    job_id=job_id,
                    pid=process.pid,
                    return_code=process.returncode,
                )

        return orphaned_jobs

    async def detect_and_recover_zombies(self) -> list[str]:
        """Detect and recover jobs in zombie state.

        Scans all tracked jobs, detects zombie states using CheckpointState.is_zombie(),
        and marks them for recovery. This integrates with Mozart's built-in zombie
        detection system.

        Returns:
            List of job IDs that were detected and marked as zombies.
        """
        zombie_jobs = []

        # Get all job states that might be zombies
        # Note: This would need a method to list all jobs from state backend
        # For now, we'll check tracked jobs and any we can load by ID
        # Create a copy of keys to avoid "dictionary changed size during iteration" error
        for job_id in list(self._running_processes.keys()):
            try:
                state = await self._state_backend.load(job_id)
                if state and state.is_zombie():
                    # Mark zombie detected using built-in method
                    state.mark_zombie_detected("Dashboard detected dead PID")
                    await self._state_backend.save(state)

                    # Clean up our tracking
                    self._running_processes.pop(job_id, None)
                    self._process_start_times.pop(job_id, None)

                    zombie_jobs.append(job_id)

                    logger.warning(
                        "zombie_job_recovered",
                        job_id=job_id,
                        pid=state.pid,
                        job_name=state.job_name,
                    )

            except Exception as e:
                logger.error(
                    "zombie_detection_error",
                    job_id=job_id,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

        return zombie_jobs

    async def _restart_job_execution(
        self,
        job_id: str,
        state: CheckpointState
    ) -> JobActionResult:
        """Restart job execution from current state.

        Used when resuming a job whose process is dead.
        """
        try:
            # Clean up any pause signal files before restart
            workspace_path = self._get_job_workspace(state)
            pause_signal_file = workspace_path / f".mozart-pause-{job_id}"
            if pause_signal_file.exists():
                try:
                    pause_signal_file.unlink()
                except OSError:
                    logger.debug("Failed to clean pause signal on restart", exc_info=True)

            # Use mozart resume command with parameterized arguments
            cmd_args = [sys.executable, "-m", "mozart.cli", "resume", job_id]

            # Add workspace if available in state
            if state.worktree_path:
                cmd_args.extend(["--workspace", str(state.worktree_path)])

            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace_root),
            )

            # Track the new process and its start time
            self._running_processes[job_id] = process
            self._process_start_times[job_id] = time.time()

            # Update state
            state.status = JobStatus.RUNNING
            state.pid = process.pid
            state.updated_at = datetime.now()
            await self._state_backend.save(state)

            logger.info(
                "job_restarted",
                job_id=job_id,
                new_pid=process.pid,
            )

            return JobActionResult(
                success=True,
                job_id=job_id,
                status=JobStatus.RUNNING.value,
                message=f"Job {job_id} restarted with new process"
            )

        except Exception as e:
            logger.error(
                "job_restart_failed",
                job_id=job_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return JobActionResult(
                success=False,
                job_id=job_id,
                status=state.status.value,
                message=f"Failed to restart job: {e}"
            )
