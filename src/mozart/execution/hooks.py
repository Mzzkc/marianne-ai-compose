"""Post-success hook execution for concert orchestration.

Executes hooks after successful job completion, enabling job chaining
and concert orchestration where jobs can spawn other jobs.

Security Note:
--------------
This module intentionally supports shell command execution via run_command hooks.
This is a DESIGN DECISION because:
1. Commands come from user-authored YAML config files (not user input at runtime)
2. Users explicitly opt-in by adding on_success hooks to their config
3. Shell features (pipes, redirects, env vars) are needed for real-world hooks
4. Mozart runs with the same permissions as the user who invokes it

The run_script hook type uses subprocess_exec (no shell) for cases where
shell features aren't needed.
"""

import asyncio
import os
import re
import shlex
import subprocess as _subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mozart.core.config import JobConfig, PostSuccessHookConfig
from mozart.core.logging import get_logger

_logger = get_logger("hooks")


@dataclass
class HookResult:
    """Result of executing a single hook."""

    hook_type: str
    description: str | None
    success: bool
    exit_code: int | None = None
    error_message: str | None = None
    duration_seconds: float = 0.0
    output: str | None = None

    # For run_job hooks
    chained_job_path: Path | None = None
    chained_job_workspace: Path | None = None

    # Log file path for detached hooks (observability)
    log_path: Path | None = None

    # Structured chained job tracking (observability: chain tracking)
    chained_job_info: dict[str, Any] | None = None


@dataclass
class ConcertContext:
    """Context passed through concert job chains.

    Tracks the concert's progress across multiple jobs to enforce
    safety limits and enable coordinated logging.
    """

    concert_id: str
    chain_depth: int = 0
    parent_job_id: str | None = None
    root_workspace: Path | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Accumulated stats
    total_jobs_run: int = 0
    total_sheets_completed: int = 0
    jobs_in_chain: list[str] = field(default_factory=list)


def get_hook_log_path(workspace: str | Path | None, hook_type: str) -> Path | None:
    """Construct log path for a hook execution.

    Creates a timestamped log file in {workspace}/hooks/ for capturing
    detached hook output that would otherwise go to /dev/null.

    Args:
        workspace: Job workspace directory. Returns None if not set.
        hook_type: Hook type identifier used in filename (e.g., "chain", "command").

    Returns:
        Path to the log file, or None if workspace is not available.
    """
    if workspace is None:
        return None
    hook_log_dir = Path(workspace) / "hooks"
    hook_log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return hook_log_dir / f"{hook_type}-{timestamp}.log"


async def _try_daemon_submit(
    job_path: Path, workspace: Path | None, fresh: bool, chain_depth: int | None,
) -> tuple[bool, str | None]:
    """Attempt to submit a chained job through the daemon IPC.

    Lazy imports avoid circular deps (hooks -> daemon -> runner -> hooks).

    Error handling strategy:
    - Daemon unavailable (DaemonNotRunningError, OSError): fall back to Popen.
    - Daemon rejected the job (JobSubmissionError, ResourceExhaustedError):
      re-raise so the caller reports the real error instead of silently
      falling back and losing rate limiting/registry tracking.
    - Other DaemonError subclasses: fall back with warning (protocol issues).
    - Non-daemon exceptions: re-raise (programming bugs must not be swallowed).
    """
    # Lazy imports — avoid circular deps (hooks -> daemon -> runner -> hooks)
    from mozart.daemon.detect import _resolve_socket_path, is_daemon_available
    from mozart.daemon.exceptions import (
        DaemonError,
        DaemonNotRunningError,
        JobSubmissionError,
        ResourceExhaustedError,
    )
    from mozart.daemon.ipc.client import DaemonClient
    from mozart.daemon.types import JobRequest

    try:
        if not await is_daemon_available():
            return False, None
        client = DaemonClient(_resolve_socket_path(None))
        request = JobRequest(
            config_path=job_path, workspace=workspace,
            fresh=fresh, chain_depth=chain_depth,
        )
        response = await client.submit_job(request)
        if response.status == "accepted":
            _logger.info("hook.daemon_submit_success", job_id=response.job_id)
            return True, response.job_id
        _logger.warning("hook.daemon_submit_rejected", status=response.status)
        return False, None

    except (OSError, ConnectionError, TimeoutError, DaemonNotRunningError) as exc:
        # Daemon unreachable or not running — safe to fall back to Popen
        _logger.warning("hook.daemon_submit_unavailable", error=str(exc))
        return False, None

    except (JobSubmissionError, ResourceExhaustedError) as exc:
        # Daemon is running but REJECTED the job — do NOT fall back.
        # Re-raise so the caller reports the real error.
        _logger.error(
            "hook.daemon_submit_rejected_error",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise

    except DaemonError as exc:
        # Other daemon protocol errors — fall back with warning
        _logger.warning(
            "hook.daemon_submit_failed", error=str(exc), exc_info=True,
        )
        return False, None


_KNOWN_HOOK_VARS = frozenset({"workspace", "job_id", "sheet_count"})


def expand_hook_variables(
    template: str,
    *,
    workspace: str | Path,
    job_id: str,
    sheet_count: int | None = None,
    for_shell: bool = False,
) -> str:
    """Expand template variables in hook paths/commands.

    Shared utility used by both HookExecutor (runner-side) and
    the daemon's _execute_hooks_task (daemon-side).

    Known variables: {workspace}, {job_id}, {sheet_count}.
    Warns on unrecognized {var} patterns that remain after expansion.

    Args:
        template: Template string with {variable} placeholders.
        workspace: Workspace path to substitute.
        job_id: Job identifier to substitute.
        sheet_count: Optional sheet count to substitute.
        for_shell: When True, apply shlex.quote() to variable values
            before substitution. Use this when the expanded result will
            be passed to create_subprocess_shell. Do NOT use when the
            result will be used as a filesystem path (e.g., run_job
            hook's job_path).
    """
    ws_str = str(workspace)
    jid_str = job_id
    if for_shell:
        ws_str = shlex.quote(ws_str)
        jid_str = shlex.quote(jid_str)

    result = template.replace("{workspace}", ws_str).replace("{job_id}", jid_str)
    if sheet_count is not None:
        sc_str = str(sheet_count)
        if for_shell:
            sc_str = shlex.quote(sc_str)
        result = result.replace("{sheet_count}", sc_str)
    # Warn about unrecognized template variables
    for match in re.finditer(r"\{(\w+)\}", result):
        var_name = match.group(1)
        if var_name not in _KNOWN_HOOK_VARS:
            _logger.warning(
                "unknown_template_variable",
                variable=var_name,
                template=template,
                known_vars=sorted(_KNOWN_HOOK_VARS),
            )
    return result


class HookExecutor:
    """Executes post-success hooks and manages concert orchestration.

    Responsible for:
    - Executing hooks after successful job completion
    - Managing job chaining (run_job hooks)
    - Enforcing concert safety limits
    - Logging hook execution

    Hook execution runs in Mozart's Python process, not inside Claude CLI.
    This allows hooks to trigger new Mozart runs without recursion issues.
    """

    def __init__(
        self,
        config: JobConfig,
        workspace: Path,
        concert_context: ConcertContext | None = None,
    ):
        self.config = config
        self.workspace = workspace
        self.concert = config.concert
        self.concert_context = concert_context

        # Track results
        self.hook_results: list[HookResult] = []

    def _expand_hook_variables(
        self, template: str, *, for_shell: bool = False,
    ) -> str:
        """Expand template variables in hook paths/commands.

        Delegates to the module-level expand_hook_variables() function.

        Args:
            template: Template string with {variable} placeholders.
            for_shell: When True, apply shlex.quote() to values for
                safe shell expansion. Use for run_command hooks.
        """
        return expand_hook_variables(
            template,
            workspace=self.workspace,
            job_id=self.config.name,
            sheet_count=self.config.sheet.total_sheets,
            for_shell=for_shell,
        )

    async def execute_hooks(self) -> list[HookResult]:
        """Execute all configured on_success hooks.

        Returns list of HookResults for each hook executed.
        Stops early if a hook fails and on_failure="abort".
        """
        if not self.config.on_success:
            _logger.debug("no_hooks_configured", job_id=self.config.name)
            return []

        _logger.info(
            "hooks.starting",
            job_id=self.config.name,
            hook_count=len(self.config.on_success),
        )

        for i, hook in enumerate(self.config.on_success):
            _logger.info(
                "hook.executing",
                hook_index=i + 1,
                hook_type=hook.type,
                description=hook.description or "(no description)",
            )

            try:
                result = await self._execute_hook(hook)
                self.hook_results.append(result)

                if result.success:
                    _logger.info(
                        "hook.succeeded",
                        hook_type=hook.type,
                        duration_seconds=round(result.duration_seconds, 2),
                    )
                else:
                    _logger.warning(
                        "hook.failed",
                        hook_type=hook.type,
                        error=result.error_message,
                        exit_code=result.exit_code,
                    )

                    # Check if we should abort remaining hooks
                    if hook.on_failure == "abort":
                        _logger.warning(
                            "hooks.aborted",
                            reason="hook failure with on_failure=abort",
                            remaining_hooks=len(self.config.on_success) - i - 1,
                        )
                        break

                    # Check concert-level abort
                    if self.concert.abort_concert_on_hook_failure:
                        _logger.warning(
                            "concert.aborted",
                            reason="hook failure with abort_concert_on_hook_failure=true",
                        )
                        break

            except (OSError, TimeoutError) as e:
                result = HookResult(
                    hook_type=hook.type,
                    description=hook.description,
                    success=False,
                    error_message=f"Exception: {e!s}",
                )
                self.hook_results.append(result)
                _logger.error(
                    "hook.exception",
                    hook_type=hook.type,
                    error=str(e),
                )

                if hook.on_failure == "abort":
                    break

        _logger.info(
            "hooks.completed",
            job_id=self.config.name,
            hooks_run=len(self.hook_results),
            hooks_succeeded=sum(1 for r in self.hook_results if r.success),
            hooks_failed=sum(1 for r in self.hook_results if not r.success),
        )

        return self.hook_results

    async def _execute_hook(self, hook: PostSuccessHookConfig) -> HookResult:
        """Execute a single hook based on its type."""
        start_time = asyncio.get_event_loop().time()

        if hook.type == "run_job":
            result = await self._execute_run_job(hook)
        elif hook.type == "run_command":
            result = await self._execute_shell_command(hook)
        elif hook.type == "run_script":
            result = await self._execute_script(hook)
        else:
            result = HookResult(
                hook_type=hook.type,
                description=hook.description,
                success=False,
                error_message=f"Unknown hook type: {hook.type}",
            )

        result.duration_seconds = asyncio.get_event_loop().time() - start_time
        return result

    async def _execute_run_job(self, hook: PostSuccessHookConfig) -> HookResult:
        """Execute a run_job hook by spawning a new Mozart run.

        This checks concert limits before spawning and prepares
        the context for the chained job.
        """
        if not hook.job_path:
            return HookResult(
                hook_type="run_job",
                description=hook.description,
                success=False,
                error_message="job_path is required for run_job hooks",
            )

        # Expand template variables in job path
        job_path = Path(self._expand_hook_variables(str(hook.job_path)))

        # Check if job config exists
        if not job_path.exists():
            return HookResult(
                hook_type="run_job",
                description=hook.description,
                success=False,
                error_message=f"Job config not found: {job_path}",
                chained_job_path=job_path,
            )

        # Check concert chain depth limit
        if self.concert.enabled:
            current_depth = (
                self.concert_context.chain_depth if self.concert_context else 0
            )
            if current_depth >= self.concert.max_chain_depth:
                return HookResult(
                    hook_type="run_job",
                    description=hook.description,
                    success=False,
                    error_message=(
                        f"Concert chain depth limit reached"
                        f" ({self.concert.max_chain_depth})"
                    ),
                    chained_job_path=job_path,
                )

        # Determine workspace for chained job
        chained_workspace = hook.job_workspace
        if not chained_workspace and self.concert.inherit_workspace:
            chained_workspace = self.workspace

        # Cooldown between jobs
        if self.concert.cooldown_between_jobs_seconds > 0:
            _logger.info(
                "concert.cooldown",
                seconds=self.concert.cooldown_between_jobs_seconds,
            )
            await asyncio.sleep(self.concert.cooldown_between_jobs_seconds)

        # Build mozart command as argument list (safe, no shell injection)
        cmd = ["mozart", "run", str(job_path)]
        if hook.fresh:
            cmd.append("--fresh")
        if chained_workspace:
            cmd.extend(["--workspace", str(chained_workspace)])

        _logger.info(
            "hook.spawning_job",
            job_path=str(job_path),
            workspace=str(chained_workspace) if chained_workspace else "(default)",
            chain_depth=(self.concert_context.chain_depth + 1 if self.concert_context else 1),
        )

        # Run the chained job using subprocess_exec (safe, no shell)
        # Use parent process cwd (not workspace) so relative job_path finds the config
        # This allows on_success hooks to reference sibling config files correctly
        import mozart.daemon.exceptions as _daemon_exc  # lazy: circular dep avoidance

        try:
            # For detached mode, try routing through the daemon first.
            # This keeps chained jobs within the daemon's management scope
            # (registry, rate coordinator, backpressure, learning hub).
            # Falls back to subprocess.Popen if daemon is unavailable.
            if hook.detached:
                chain_depth = (
                    self.concert_context.chain_depth + 1
                    if self.concert_context else 1
                )
                daemon_ok, daemon_job_id = await _try_daemon_submit(
                    job_path=job_path,
                    workspace=chained_workspace,
                    fresh=hook.fresh,
                    chain_depth=chain_depth,
                )
                if daemon_ok:
                    return HookResult(
                        hook_type="run_job",
                        description=hook.description,
                        success=True,
                        output=f"Job submitted to daemon (job_id={daemon_job_id})",
                        chained_job_path=job_path,
                        chained_job_workspace=chained_workspace,
                        chained_job_info={
                            "job_path": str(job_path),
                            "workspace": str(chained_workspace) if chained_workspace else None,
                            "job_id": daemon_job_id,
                            "routed_via": "daemon",
                        },
                    )
                # Create log file for detached hook output instead of DEVNULL.
                # This ensures chained job failures leave a diagnostic trace.
                log_path = get_hook_log_path(self.workspace, "chain")
                log_file = None
                stdout_handle: Any = _subprocess.DEVNULL
                stderr_handle: Any = _subprocess.DEVNULL
                if log_path:
                    log_file = open(log_path, "w")  # noqa: SIM115
                    stdout_handle = log_file
                    stderr_handle = log_file

                try:
                    # Use subprocess.Popen (NOT asyncio.create_subprocess_exec)
                    # for detached children. asyncio wraps children in a
                    # BaseSubprocessTransport whose __del__ sends SIGKILL to the
                    # child PID when the parent's event loop closes — even with
                    # start_new_session=True. Popen.__del__ only warns, so the
                    # child survives the parent's exit.
                    process = _subprocess.Popen(
                        cmd,
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        stdin=_subprocess.DEVNULL,
                        env=os.environ.copy(),
                        start_new_session=True,
                    )
                finally:
                    # Close fd in parent — child inherited it.
                    # Using finally ensures no fd leak if subprocess creation raises.
                    if log_file is not None:
                        log_file.close()

                # Post-spawn liveness check: wait briefly, then verify the
                # child is still alive. Catches immediate death (e.g. exec
                # failure, missing binary) and guards against future regressions.
                # Use Popen.poll() instead of os.kill(pid, 0) because kill()
                # succeeds on zombie processes (exited but not reaped), while
                # poll() calls waitpid(WNOHANG) which correctly detects exit.
                await asyncio.sleep(0.2)
                exit_code = process.poll()
                if exit_code is not None:
                    _logger.error(
                        "hook.detached_job_died_immediately",
                        pid=process.pid,
                        exit_code=exit_code,
                        job_path=str(job_path),
                    )
                    return HookResult(
                        hook_type="run_job",
                        description=hook.description,
                        success=False,
                        error_message=(
                            f"Detached child (PID {process.pid}) exited immediately"
                            f" (exit_code={exit_code})"
                        ),
                        chained_job_path=job_path,
                        chained_job_workspace=chained_workspace,
                        log_path=log_path,
                    )

                _logger.info(
                    "hook.detached_job_spawned",
                    job_path=str(job_path),
                    pid=process.pid,
                    log_path=str(log_path) if log_path else None,
                )
                return HookResult(
                    hook_type="run_job",
                    description=hook.description,
                    success=True,
                    output=f"Detached job spawned (PID {process.pid})",
                    chained_job_path=job_path,
                    chained_job_workspace=chained_workspace,
                    log_path=log_path,
                    chained_job_info={
                        "job_path": str(job_path),
                        "workspace": str(chained_workspace) if chained_workspace else None,
                        "pid": process.pid,
                        "log_path": str(log_path) if log_path else None,
                    },
                )

            # Normal mode - wait for completion
            async_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                # Don't override cwd - inherit from parent process to find config files
                env=os.environ.copy(),
            )

            # Wait for completion with timeout
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    async_proc.communicate(),
                    timeout=hook.timeout_seconds,
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                exit_code = async_proc.returncode
            except TimeoutError:
                async_proc.kill()
                await async_proc.wait()
                return HookResult(
                    hook_type="run_job",
                    description=hook.description,
                    success=False,
                    error_message=f"Timeout after {hook.timeout_seconds}s",
                    chained_job_path=job_path,
                    chained_job_workspace=chained_workspace,
                )

            return HookResult(
                hook_type="run_job",
                description=hook.description,
                success=(exit_code == 0),
                exit_code=exit_code,
                output=stdout[-2000:] if stdout else None,  # Tail of output
                chained_job_path=job_path,
                chained_job_workspace=chained_workspace,
                chained_job_info={
                    "job_path": str(job_path),
                    "workspace": str(chained_workspace) if chained_workspace else None,
                    "pid": async_proc.pid,
                    "log_path": None,
                },
            )

        except FileNotFoundError:
            return HookResult(
                hook_type="run_job",
                description=hook.description,
                success=False,
                error_message="mozart command not found in PATH",
                chained_job_path=job_path,
            )
        except (
            _daemon_exc.JobSubmissionError,
            _daemon_exc.ResourceExhaustedError,
        ) as exc:
            # Daemon rejected the job — report as hook failure
            return HookResult(
                hook_type="run_job",
                description=hook.description,
                success=False,
                error_message=f"Daemon rejected job: {exc}",
                chained_job_path=job_path,
                chained_job_workspace=chained_workspace,
            )

    async def _execute_shell_command(self, hook: PostSuccessHookConfig) -> HookResult:
        """Execute a run_command hook via shell.

        Security: Commands come from user-authored YAML config files,
        not runtime user input. Shell features are intentionally supported
        for pipes, redirects, and environment variable expansion.
        """
        if not hook.command:
            return HookResult(
                hook_type="run_command",
                description=hook.description,
                success=False,
                error_message="command is required for run_command hooks",
            )

        # Expand template variables with shell quoting (F-020 fix)
        command = self._expand_hook_variables(hook.command, for_shell=True)

        # Determine working directory
        cwd = hook.working_directory or self.workspace

        try:
            # Shell execution for run_command (intentional - see module docstring)
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=os.environ.copy(),
            )

            # Wait with timeout
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=hook.timeout_seconds,
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                exit_code = process.returncode
            except TimeoutError:
                process.kill()
                await process.wait()
                return HookResult(
                    hook_type="run_command",
                    description=hook.description,
                    success=False,
                    error_message=f"Timeout after {hook.timeout_seconds}s",
                )

            return HookResult(
                hook_type="run_command",
                description=hook.description,
                success=(exit_code == 0),
                exit_code=exit_code,
                output=stdout[-2000:] if stdout else None,
            )

        except Exception as e:
            _logger.warning(
                "hook.shell_command_failed",
                description=hook.description,
                error=str(e),
                exc_info=True,
            )
            return HookResult(
                hook_type="run_command",
                description=hook.description,
                success=False,
                error_message=str(e),
            )

    async def _execute_script(self, hook: PostSuccessHookConfig) -> HookResult:
        """Execute a run_script hook without shell (safer).

        Uses subprocess_exec which doesn't invoke a shell, making it
        safer for scripts that don't need shell features.
        """
        if not hook.command:
            return HookResult(
                hook_type="run_script",
                description=hook.description,
                success=False,
                error_message="command is required for run_script hooks",
            )

        # Expand template variables
        command = self._expand_hook_variables(hook.command)

        # Determine working directory
        cwd = hook.working_directory or self.workspace

        try:
            # Parse command into arguments (no shell)
            args = shlex.split(command)

            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=os.environ.copy(),
            )

            # Wait with timeout
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=hook.timeout_seconds,
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                exit_code = process.returncode
            except TimeoutError:
                process.kill()
                await process.wait()
                return HookResult(
                    hook_type="run_script",
                    description=hook.description,
                    success=False,
                    error_message=f"Timeout after {hook.timeout_seconds}s",
                )

            return HookResult(
                hook_type="run_script",
                description=hook.description,
                success=(exit_code == 0),
                exit_code=exit_code,
                output=stdout[-2000:] if stdout else None,
            )

        except Exception as e:
            _logger.warning(
                "hook.script_execution_failed",
                description=hook.description,
                error=str(e),
                exc_info=True,
            )
            return HookResult(
                hook_type="run_script",
                description=hook.description,
                success=False,
                error_message=str(e),
            )

    def get_next_job_to_chain(self) -> tuple[Path, Path | None] | None:
        """Get the next job to chain from successful run_job hooks.

        Returns (job_path, workspace) for the first successful run_job hook,
        or None if no chaining should occur.

        Note: This is for the synchronous chaining mode where Mozart
        itself manages the concert. For async/background chaining,
        hooks execute the jobs directly.
        """
        for result in self.hook_results:
            if (
                result.hook_type == "run_job"
                and result.success
                and result.chained_job_path
            ):
                return (result.chained_job_path, result.chained_job_workspace)
        return None
