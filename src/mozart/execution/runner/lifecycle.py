"""Run lifecycle mixin for JobRunner.

Contains the main run() method, state initialization, execution modes
(sequential and parallel), completion handling, and learning aggregation.

This mixin orchestrates the high-level job execution flow:
1. Initialize state (new or resume)
2. Choose execution mode (sequential vs parallel)
3. Execute sheets with recovery
4. Finalize and aggregate learning outcomes

Architecture:
    This mixin requires access to attributes and methods from:
    - JobRunnerBase: config, backend, state_backend, console, etc.
    - IsolationMixin: _setup_isolation(), _cleanup_isolation()
    - SheetMixin: _execute_sheet_with_recovery(), _build_sheet_context()

    It provides:
    - run(): Main entry point
    - _initialize_state(): State setup
    - _execute_sequential_mode(): Sequential execution
    - _execute_parallel_mode(): Parallel execution (v17 evolution)
    - _finalize_summary(): Statistics aggregation
    - _aggregate_to_global_store(): Global learning integration
"""

from __future__ import annotations

import asyncio
import shlex
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mozart.backends.base import Backend
    from mozart.core.config import JobConfig
    from mozart.core.logging import MozartLogger
    from mozart.execution.dag import DependencyDAG
    from mozart.execution.parallel import ParallelBatchResult, ParallelExecutor
    from mozart.learning.global_store import GlobalLearningStore
    from mozart.state.base import StateBackend

    from .models import RunSummary as _RunSummary

from mozart.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from mozart.core.logging import ExecutionContext
from mozart.execution.hooks import ConcertContext, HookExecutor
from mozart.utils.time import utc_now

from .models import FatalError, RateLimitExhaustedError, RunSummary


class LifecycleMixin:
    """Mixin providing job run lifecycle methods.

    Requires attributes from JobRunnerBase:
        - config: JobConfig
        - backend: Backend
        - state_backend: StateBackend
        - console: Console
        - _logger: MozartLogger
        - _parallel_executor: ParallelExecutor | None
        - _dependency_dag: DependencyDAG | None
        - _global_learning_store: GlobalLearningStore | None
        - _summary: RunSummary | None
        - _run_start_time: float
        - _current_state: CheckpointState | None
        - _sheet_times: list[float]
        - _execution_context: ExecutionContext | None
        - _shutdown_requested: bool

    Requires methods from other mixins:
        - _install_signal_handlers(): from JobRunnerBase
        - _remove_signal_handlers(): from JobRunnerBase
        - _handle_graceful_shutdown(): from JobRunnerBase
        - _check_pause_signal(): from JobRunnerBase
        - _handle_pause_request(): from JobRunnerBase
        - _update_progress(): from JobRunnerBase
        - _interruptible_sleep(): from JobRunnerBase
        - _setup_isolation(): from IsolationMixin
        - _cleanup_isolation(): from IsolationMixin
        - _execute_sheet_with_recovery(): from SheetMixin
    """

    # Type declarations for attributes from JobRunnerBase
    if TYPE_CHECKING:
        config: JobConfig
        backend: Backend
        state_backend: StateBackend
        console: Any  # rich.console.Console
        _logger: MozartLogger
        _parallel_executor: ParallelExecutor | None
        _dependency_dag: DependencyDAG | None
        _global_learning_store: GlobalLearningStore | None
        _summary: _RunSummary | None
        _run_start_time: float
        _current_state: CheckpointState | None
        _sheet_times: list[float]
        _execution_context: ExecutionContext | None
        _shutdown_requested: bool

        # Methods from JobRunnerBase
        def _install_signal_handlers(self) -> None: ...
        def _remove_signal_handlers(self) -> None: ...
        async def _handle_graceful_shutdown(self, state: CheckpointState) -> None: ...
        def _check_pause_signal(self, state: CheckpointState) -> bool: ...
        def _clear_pause_signal(self, state: CheckpointState) -> None: ...
        async def _handle_pause_request(
            self, state: CheckpointState, current_sheet: int
        ) -> None: ...
        def _update_progress(self, state: CheckpointState) -> None: ...
        async def _fire_event(
            self, event: str, sheet_num: int, data: dict[str, Any] | None = None,
        ) -> None: ...
        async def _interruptible_sleep(self, seconds: float) -> None: ...

        # Methods from IsolationMixin
        async def _setup_isolation(self, state: CheckpointState) -> Path | None: ...
        async def _cleanup_isolation(self, state: CheckpointState) -> None: ...

        # Methods from SheetExecutionMixin
        async def _execute_sheet_with_recovery(
            self, state: CheckpointState, sheet_num: int
        ) -> None: ...

    async def run(
        self,
        start_sheet: int | None = None,
        config_path: str | None = None,
    ) -> tuple[CheckpointState, RunSummary]:
        """Run the job from start or resume point.

        Args:
            start_sheet: Optional sheet number to start from (overrides state).
            config_path: Optional path to the original config file (for resume).

        Returns:
            Tuple of (CheckpointState, RunSummary) with final job state and
            execution statistics.

        Raises:
            FatalError: If an unrecoverable error occurs.
            GracefulShutdownError: If Ctrl+C was pressed and job was paused.
        """
        # Initialize timing and summary tracking
        self._run_start_time = time.monotonic()
        state = await self._initialize_state(start_sheet, config_path)
        self._current_state = state

        # Track if job was already completed when we loaded state.
        # Used to prevent on_success hooks from firing when zero new work
        # was done (e.g., self-chaining job loads its own completed state).
        loaded_as_completed = state.status == JobStatus.COMPLETED

        # Set running PID for zombie detection
        state.set_running_pid()
        await self.state_backend.save(state)

        # Initialize run summary
        self._summary = RunSummary(
            job_id=state.job_id,
            job_name=state.job_name,
            total_sheets=state.total_sheets,
        )

        # Create execution context for correlation (Task 8: Logging Integration)
        self._execution_context = ExecutionContext(
            job_id=state.job_id,
            component="runner",
        )

        # Clear any stale pause signal from a previous run.
        # Pause files can be orphaned if a process crashed or was killed
        # without going through normal cleanup. We clear on startup so
        # the new run doesn't immediately pause on a stale signal.
        self._clear_pause_signal(state)

        # Install signal handlers for graceful shutdown
        self._install_signal_handlers()

        # Log job start with config summary
        config_summary = self._get_config_summary()
        self._logger.info(
            "job.started",
            job_id=state.job_id,
            total_sheets=state.total_sheets,
            resume_from=start_sheet,
            config=config_summary,
        )
        await self._fire_event("job.started", 0, {
            "total_sheets": state.total_sheets,
            "resume_from": start_sheet,
        })

        # Set up worktree isolation if configured (v2 evolution: Worktree Isolation)
        # Store original working directory for restoration if needed
        original_working_directory: Path | None = self.backend.working_directory
        worktree_path = await self._setup_isolation(state)
        if worktree_path:
            # Override backend working directory to use worktree
            self.backend.working_directory = worktree_path
            await self.state_backend.save(state)  # Persist worktree state

        try:
            # Choose execution mode: parallel or sequential (v17 evolution)
            if self._parallel_executor is not None and self.config.parallel.enabled:
                await self._execute_parallel_mode(state)
            else:
                await self._execute_sequential_mode(state)

            # Mark job complete if we processed all sheets.
            # Only actual FAILED sheets cause the job to fail.  SKIPPED
            # sheets (from skip_when_command or DAG dependency blocking)
            # are not failures — and DAG-blocked skips always have a
            # corresponding FAILED prerequisite, so checking FAILED alone
            # is sufficient.
            if state.status == JobStatus.RUNNING:
                has_failures = any(
                    s.status == SheetStatus.FAILED
                    for s in state.sheets.values()
                )
                state.status = JobStatus.FAILED if has_failures else JobStatus.COMPLETED
                state.pid = None  # Clear PID on completion
                if has_failures:
                    skipped = [
                        n for n, s in state.sheets.items()
                        if s.status == SheetStatus.SKIPPED
                    ]
                    failed = [n for n, s in state.sheets.items() if s.status == SheetStatus.FAILED]
                    parts = []
                    if failed:
                        parts.append(f"sheets {failed} failed")
                    if skipped:
                        parts.append(f"sheets {skipped} blocked by failed dependencies")
                    state.error_message = "; ".join(parts)
                await self.state_backend.save(state)

            # Finalize summary
            self._finalize_summary(state)
            summary = self._summary
            assert summary is not None  # set on line 114

            # Log job completion with summary
            self._logger.info(
                "job.completed",
                job_id=state.job_id,
                status=state.status.value,
                duration_seconds=round(summary.total_duration_seconds, 2),
                completed_sheets=summary.completed_sheets,
                failed_sheets=summary.failed_sheets,
                success_rate=round(summary.success_rate, 1),
                validation_pass_rate=round(summary.validation_pass_rate, 1),
                total_retries=summary.total_retries,
            )
            await self._fire_event("job.completed", 0, {
                "status": state.status.value,
                "completed_sheets": summary.completed_sheets,
                "failed_sheets": summary.failed_sheets,
                "duration_seconds": round(summary.total_duration_seconds, 2),
            })

            # Execute post-success hooks if job completed successfully.
            # When daemon_managed=True, hooks are executed by the daemon's
            # _execute_hooks_task() after the job task returns — NOT here.
            # This prevents duplicate hook execution in daemon mode.
            # Skip hooks if the job was already COMPLETED when we loaded state,
            # meaning zero new sheets were executed this run. This prevents
            # infinite self-chaining loops where a completed job re-triggers
            # its own on_success hook without doing any work.
            if state.status == JobStatus.COMPLETED and self.config.on_success:
                if getattr(self, "_daemon_managed", False):
                    self._logger.info(
                        "hooks.skipped_daemon_managed",
                        job_id=state.job_id,
                        reason="Hooks will be executed by the daemon after this task returns",
                    )
                elif not loaded_as_completed:
                    await self._execute_post_success_hooks(state)
                else:
                    self._logger.info(
                        "hooks.skipped_zero_work",
                        job_id=state.job_id,
                        reason="Job was already completed when loaded — no new sheets executed",
                    )

            return state, summary
        finally:
            # Each cleanup step is independently protected so that a failure
            # in one (e.g. isolation cleanup) doesn't prevent subsequent steps
            # (e.g. backend.close()) from running.
            #
            # Exception handling is narrowed to expected error categories:
            # - OSError: disk/file/network I/O failures
            # - ValueError/RuntimeError: invalid state during cleanup
            # Programming errors (AttributeError, TypeError, NameError) are
            # NOT caught — they indicate bugs that should propagate.

            # Ensure summary is finalized even on failure/shutdown paths.
            # _finalize_summary is idempotent (overwrites, not accumulates),
            # so calling it again if it was already called in the try block
            # or failure handlers is safe.
            try:
                self._finalize_summary(state)
            except (OSError, ValueError, RuntimeError):
                self._logger.warning(
                    "cleanup.finalize_summary_failed",
                    job_id=state.job_id,
                    exc_info=True,
                )

            # Aggregate outcomes to global learning store (Movement IV-B).
            # Placed in finally so learning data is captured from both
            # successful and failed jobs — fixing survivorship bias where
            # only successes contributed to the learning system.
            try:
                await self._aggregate_to_global_store(state)
            except (sqlite3.Error, OSError, ValueError, RuntimeError):
                self._logger.warning(
                    "cleanup.learning_aggregation_failed",
                    job_id=state.job_id,
                    exc_info=True,
                )

            # Clean up worktree isolation if configured (v2 evolution)
            try:
                await self._cleanup_isolation(state)
            except (OSError, RuntimeError):
                self._logger.warning(
                    "cleanup.isolation_failed",
                    job_id=state.job_id,
                    exc_info=True,
                )

            # Restore original working directory if it was overridden
            if worktree_path:
                self.backend.working_directory = original_working_directory

            # Close backend to release resources (connections, subprocesses, etc.)
            try:
                await self.backend.close()
            except (OSError, RuntimeError):
                self._logger.warning(
                    "cleanup.backend_close_failed",
                    job_id=state.job_id,
                    exc_info=True,
                )

            # Clean up any leftover pause signal file for this job.
            # Pause files are normally cleared when a pause is handled,
            # but can be orphaned if the job completes or fails before
            # the pause is processed (or if the process crashed).
            try:
                self._clear_pause_signal(state)
            except OSError:
                self._logger.warning(
                    "cleanup.pause_signal_failed",
                    job_id=state.job_id,
                    exc_info=True,
                )

            # Remove signal handlers
            self._remove_signal_handlers()

    async def _initialize_state(
        self,
        start_sheet: int | None,
        config_path: str | None = None,
    ) -> CheckpointState:
        """Initialize or load job state.

        Args:
            start_sheet: Optional override for starting sheet.
            config_path: Optional path to the original config file.

        Returns:
            CheckpointState ready for execution.
        """
        job_id = self.config.name
        state = await self.state_backend.load(job_id)

        if state is None:
            # Serialize JobConfig for resume capability (Task 3: Config Storage)
            config_snapshot = self.config.model_dump(mode="json")

            state = CheckpointState(
                job_id=job_id,
                job_name=self.config.name,
                total_sheets=self.config.sheet.total_sheets,
                config_snapshot=config_snapshot,
                config_path=config_path,
                config_hash=self._compute_config_hash(config_snapshot),
                spec_corpus_hash=getattr(self, "_spec_corpus_hash", ""),
            )
            await self.state_backend.save(state)
            self.console.print("[green]Created new job state[/green]")
        else:
            # Check if another process is already running this job
            if state.status == JobStatus.RUNNING and state.pid is not None:
                import os

                if state.pid == os.getpid():
                    # Same process — stale PID from a previous coroutine run
                    # in daemon mode.  All jobs share the daemon process, so
                    # os.kill(pid, 0) would succeed even though no runner is
                    # active.  Treat as zombie.
                    state.mark_zombie_detected(
                        "Stale PID matches current process — recovering for resume"
                    )
                    await self.state_backend.save(state)
                else:
                    try:
                        os.kill(state.pid, 0)  # Check if process is alive
                        # Process is alive - refuse to start
                        raise FatalError(
                            f"Job is already running (PID {state.pid}). "
                            f"Use 'mozart status {job_id}' to check, "
                            "or kill the other process first."
                        )
                    except ProcessLookupError:
                        # Process is dead - zombie state, recover it
                        state.mark_zombie_detected(
                            "Detected on state load - process no longer running",
                        )
                        await self.state_backend.save(state)
                    except PermissionError:
                        # Can't check (different user) - warn but continue
                        self._logger.warning(
                            "cannot_check_running_process",
                            job_id=job_id,
                            pid=state.pid,
                            reason="permission denied",
                        )

            # Config hash stale detection for terminal jobs (#103).
            # If the score changed since the last run, a terminal state
            # (COMPLETED or FAILED) is stale and should be restarted.
            # FAILED + changed config: the user modified the score to fix
            # the problem — resuming from stale state defeats the purpose.
            _stale_handled = False
            if state.status in (JobStatus.COMPLETED, JobStatus.FAILED) and state.config_hash:
                config_snapshot = self.config.model_dump(mode="json")
                current_hash = self._compute_config_hash(config_snapshot)
                if current_hash != state.config_hash:
                    self._logger.info(
                        "state.stale_config_detected",
                        job_id=job_id,
                        old_hash=state.config_hash[:12],
                        new_hash=current_hash[:12],
                        previous_status=state.status.value,
                    )
                    self.console.print(
                        "[yellow]Score has changed since last run — "
                        "restarting with fresh state[/yellow]"
                    )
                    state = CheckpointState(
                        job_id=job_id,
                        job_name=self.config.name,
                        total_sheets=self.config.sheet.total_sheets,
                        config_snapshot=config_snapshot,
                        config_path=config_path,
                        config_hash=current_hash,
                    )
                    await self.state_backend.save(state)
                    _stale_handled = True
                elif state.status == JobStatus.COMPLETED:
                    self.console.print(
                        "[green]Job already completed with same config — "
                        "skipping re-execution[/green]"
                    )
                    _stale_handled = True
                # FAILED + same config: _stale_handled stays False → resume

            if not _stale_handled:
                # Reset rate limit counters on resume (Bug #100)
                if state.status == JobStatus.PAUSED:
                    state.rate_limit_waits = 0
                    state.quota_waits = 0
                    state.resume_at = None

                self.console.print(
                    f"[yellow]Resuming from sheet "
                    f"{state.last_completed_sheet + 1}[/yellow]"
                )

        # Override starting sheet if specified
        if start_sheet is not None:
            state.last_completed_sheet = start_sheet - 1
            # Mark sheets 1..start_sheet-1 as COMPLETED in state.sheets so
            # parallel mode's DAG sees their dependencies as satisfied (#42).
            # F-075: Preserve existing terminal status — don't overwrite
            # FAILED/SKIPPED sheets to COMPLETED on resume. Only mark
            # non-terminal sheets as COMPLETED.
            _terminal = (SheetStatus.COMPLETED, SheetStatus.FAILED, SheetStatus.SKIPPED)
            for skipped in range(1, start_sheet):
                if skipped not in state.sheets:
                    state.sheets[skipped] = SheetState(sheet_num=skipped)
                if state.sheets[skipped].status not in _terminal:
                    state.sheets[skipped].status = SheetStatus.COMPLETED

        # Pre-populate all sheets so every sheet 1..total_sheets has a
        # record from the start.  Without this, sheets only get created
        # in mark_sheet_started(), which means a pause before a sheet
        # starts leaves no DB record — causing the sheet to be silently
        # skipped on resume.
        for sheet_num in range(1, state.total_sheets + 1):
            if sheet_num not in state.sheets:
                state.sheets[sheet_num] = SheetState(sheet_num=sheet_num)

        return state

    @staticmethod
    def _compute_config_hash(config: dict[str, Any]) -> str:
        """Compute a content-based hash of a config snapshot.

        Uses SHA-256 with sorted keys to ensure deterministic hashing
        regardless of key insertion order across Python versions.

        Args:
            config: Serialized config dict (from JobConfig.model_dump).

        Returns:
            64-character hex digest of the config content.
        """
        import hashlib
        import json

        content = json.dumps(config, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()

    def _finalize_summary(self, state: CheckpointState) -> None:
        """Finalize run summary with statistics from completed job.

        Args:
            state: Final job state to extract statistics from.
        """
        if self._summary is None:
            return

        # Calculate total duration
        self._summary.total_duration_seconds = time.monotonic() - self._run_start_time
        self._summary.final_status = state.status

        # Reset counters before aggregating so this method is idempotent.
        # It may be called multiple times: once in the try block (success path)
        # or failure handlers, and once in the finally block (guaranteed path).
        self._summary.completed_sheets = 0
        self._summary.failed_sheets = 0
        self._summary.skipped_sheets = 0
        self._summary.successes_without_retry = 0
        self._summary.total_completion_attempts = 0
        self._summary.total_retries = 0
        self._summary.validation_pass_count = 0
        self._summary.validation_fail_count = 0

        # Aggregate sheet statistics
        for sheet_state in state.sheets.values():
            if sheet_state.status == SheetStatus.COMPLETED:
                self._summary.completed_sheets += 1
                if sheet_state.success_without_retry:
                    self._summary.successes_without_retry += 1
                # Track completion attempts
                if sheet_state.completion_attempts:
                    self._summary.total_completion_attempts += sheet_state.completion_attempts
            elif sheet_state.status == SheetStatus.FAILED:
                self._summary.failed_sheets += 1
            elif sheet_state.status == SheetStatus.SKIPPED:
                self._summary.skipped_sheets += 1

            # Track retries (attempts - 1)
            if sheet_state.attempt_count > 1:
                self._summary.total_retries += sheet_state.attempt_count - 1

            # Track validation results
            if sheet_state.validation_passed is True:
                self._summary.validation_pass_count += 1
            elif sheet_state.validation_passed is False:
                self._summary.validation_fail_count += 1

        # Copy rate limit waits from state
        self._summary.rate_limit_waits = state.rate_limit_waits

    async def _execute_post_success_hooks(self, state: CheckpointState) -> None:
        """Execute post-success hooks after job completion.

        This enables concert orchestration where jobs can chain to other jobs.
        Hooks run in Mozart's Python process, not inside Claude CLI.

        Args:
            state: Final job state (must have COMPLETED status).
        """
        if not self.config.on_success:
            return

        self._logger.info(
            "hooks.executing",
            job_id=state.job_id,
            hook_count=len(self.config.on_success),
            concert_enabled=self.config.concert.enabled,
        )

        # Create hook executor with concert context for depth enforcement
        concert_context = None
        if self.config.concert.enabled:
            concert_context = ConcertContext(
                concert_id=state.job_id,
                chain_depth=0,
            )

        executor = HookExecutor(
            config=self.config,
            workspace=self.config.workspace,
            concert_context=concert_context,
        )

        # Execute hooks
        results = await executor.execute_hooks()

        if not results:
            return

        hooks_succeeded = sum(1 for r in results if r.success)
        hooks_failed = len(results) - hooks_succeeded

        # Persist hook results to checkpoint state so `mozart status` can
        # display them and failures aren't silently lost.
        for r in results:
            state.record_hook_result({
                "hook_type": r.hook_type,
                "description": r.description,
                "success": r.success,
                "exit_code": r.exit_code,
                "error_message": r.error_message,
                "duration_seconds": round(r.duration_seconds, 2),
                "chained_job_path": str(r.chained_job_path) if r.chained_job_path else None,
                "chained_job_info": r.chained_job_info,
            })

        # If any hook failed, downgrade job status from COMPLETED to FAILED.
        # A successful job that can't fire its on_success chain is not truly
        # complete — the concert is broken and the user needs to know.
        if hooks_failed > 0:
            failed_descs = [
                r.error_message or r.description or r.hook_type
                for r in results if not r.success
            ]
            state.status = JobStatus.FAILED
            state.error_message = (
                f"Job sheets passed but {hooks_failed} on_success hook(s) failed: "
                + "; ".join(failed_descs[:3])
            )
            self._logger.error(
                "hooks.failure_downgrades_job",
                job_id=state.job_id,
                hooks_failed=hooks_failed,
                errors=failed_descs,
            )

        await self.state_backend.save(state)

        # Update summary with hook results
        if self._summary:
            self._summary.hook_results = results
            self._summary.hooks_executed = len(results)
            self._summary.hooks_succeeded = hooks_succeeded
            self._summary.hooks_failed = hooks_failed

        self._logger.info(
            "hooks.summary",
            job_id=state.job_id,
            hooks_executed=len(results),
            hooks_succeeded=hooks_succeeded,
            hooks_failed=hooks_failed,
        )

    def get_summary(self) -> RunSummary | None:
        """Get the current run summary.

        Returns:
            RunSummary if a run has been executed, None otherwise.
        """
        return self._summary

    def _get_config_summary(self) -> dict[str, Any]:
        """Build a safe config summary for logging.

        Returns a dictionary with key configuration values that are safe
        to log (no sensitive data like API keys or tokens).

        Returns:
            Dictionary with non-sensitive configuration summary.
        """
        return {
            "backend_type": self.config.backend.type,
            "sheet_size": self.config.sheet.size,
            "total_items": self.config.sheet.total_items,
            "max_retries": self.config.retry.max_retries,
            "max_completion_attempts": self.config.retry.max_completion_attempts,
            "workspace": str(self.config.workspace),
            "validation_count": len(self.config.validations),
            "rate_limit_wait_minutes": self.config.rate_limit.wait_minutes,
            "learning_enabled": self.config.learning.enabled,
            "circuit_breaker_enabled": self.config.circuit_breaker.enabled,
            "circuit_breaker_threshold": self.config.circuit_breaker.failure_threshold,
            "isolation_enabled": self.config.isolation.enabled,
            "isolation_mode": (
                self.config.isolation.mode.value
                if self.config.isolation.enabled
                else None
            ),
        }

    # =========================================================================
    # Sheet Dependency DAG (v17 evolution: Sheet Dependency DAG)
    # =========================================================================

    def _get_next_sheet_dag_aware(self, state: CheckpointState) -> int | None:
        """Get the next sheet to execute, respecting DAG dependencies.

        When a dependency DAG is configured, this method ensures sheets only
        run when all their prerequisites are complete. Without a DAG, falls
        back to the default sequential behavior.

        Args:
            state: Current job checkpoint state.

        Returns:
            Next sheet number to execute, or None if all complete or blocked.
            When blocked by failed dependencies, logs a warning with details.
        """

        # If no DAG configured, use default sequential behavior
        if self._dependency_dag is None:
            return state.get_next_sheet()

        # Build sets of sheet numbers by status.
        # Skipped sheets (e.g. from skip_when_command) are terminal — they
        # satisfy dependencies so downstream sheets can proceed.
        status_sets: dict[SheetStatus, set[int]] = {
            SheetStatus.COMPLETED: set(),
            SheetStatus.FAILED: set(),
            SheetStatus.SKIPPED: set(),
        }
        for num, sheet_state in state.sheets.items():
            if sheet_state.status in status_sets:
                status_sets[sheet_state.status].add(num)

        completed = status_sets[SheetStatus.COMPLETED]
        failed = status_sets[SheetStatus.FAILED]
        skipped = status_sets[SheetStatus.SKIPPED]

        # Sheets that satisfy dependencies: completed + skipped
        satisfied = completed | skipped

        # Check for in-progress sheet (resume from crash)
        if state.current_sheet is not None:
            current = state.sheets.get(state.current_sheet)
            if current and current.status == SheetStatus.IN_PROGRESS:
                # Verify dependencies are still satisfied — a dependency
                # may have FAILED since the job was paused/crashed.
                deps = self._dependency_dag.get_dependencies(state.current_sheet)
                if any(d in failed for d in deps):
                    self._logger.warning(
                        "dag.resume_blocked_by_failed_dep",
                        sheet_num=state.current_sheet,
                        failed_deps=sorted(d for d in deps if d in failed),
                    )
                elif all(d in satisfied for d in deps):
                    return state.current_sheet

        # Get ready sheets (all dependencies satisfied)
        ready_sheets = self._dependency_dag.get_ready_sheets(satisfied)

        # Filter out sheets that are already done
        pending_ready = [s for s in ready_sheets if s not in satisfied]

        if pending_ready:
            return pending_ready[0]

        # No ready sheets — distinguish "all done" from "blocked by failed deps"
        all_processed = completed | failed | skipped
        remaining = set(range(1, state.total_sheets + 1)) - all_processed
        if not remaining:
            # All sheets completed or failed — job is done
            return None

        # Some sheets remain but none are ready: blocked by failed dependencies.
        # Mark them as SKIPPED so the summary and job status reflect reality.
        blocked_sheets = sorted(remaining)
        for sheet_num in blocked_sheets:
            state.mark_sheet_skipped(
                sheet_num,
                reason=f"Blocked by failed dependencies: {sorted(failed)}",
            )
        self._logger.warning(
            "dag.sheets_blocked",
            blocked_sheets=blocked_sheets,
            failed_dependencies=sorted(failed),
            completed=sorted(completed),
            message=(
                f"Sheets {blocked_sheets} are blocked because their dependencies "
                f"include failed sheets {sorted(failed)}"
            ),
        )
        return None

    def _get_completed_sheets(self, state: CheckpointState) -> set[int]:
        """Get set of completed sheet numbers.

        Helper method for DAG-aware execution tracking.

        Args:
            state: Current job checkpoint state.

        Returns:
            Set of sheet numbers with COMPLETED status.
        """
        return {
            num for num, s in state.sheets.items()
            if s.status == SheetStatus.COMPLETED
        }

    # =========================================================================
    # Execution Modes
    # =========================================================================

    async def _should_skip_sheet(self, sheet_num: int, state: CheckpointState) -> str | None:
        """Evaluate conditional skip rules for a sheet.

        Checks two types of skip conditions in order:
        1. ``skip_when`` — Python expression conditions (instant, no I/O)
        2. ``skip_when_command`` — Shell command conditions (subprocess with timeout)

        Expression conditions are checked first. If a sheet is already skipped
        by expression, the command is not run.

        For expression conditions (skip_when):
        - Truthy result -> skip the sheet
        - Eval error -> run the sheet (fail-open)

        For command conditions (skip_when_command):
        - Exit 0 -> skip the sheet (with description as reason)
        - Non-zero -> run the sheet
        - Timeout/error -> run the sheet (fail-open for safety)

        Args:
            sheet_num: Sheet number to evaluate.
            state: Current job state for condition context.

        Returns:
            Skip reason string if the sheet should be skipped, None otherwise.
        """
        import asyncio as _asyncio

        # --- Phase 1: Check expression-based skip_when (existing behavior) ---
        skip_conditions = self.config.sheet.skip_when
        if skip_conditions and sheet_num in skip_conditions:
            condition = skip_conditions[sheet_num]
            # Restricted builtins -- only allow safe comparison operations
            safe_builtins = {
                "True": True, "False": False, "None": None,
                "len": len, "bool": bool, "int": int, "str": str,
                "any": any, "all": all, "max": max, "min": min,
            }
            try:
                result = eval(  # noqa: S307 -- operator-controlled config, not user input
                    condition,
                    {"__builtins__": safe_builtins},
                    {"sheets": state.sheets, "job": state},
                )
                if result:
                    return f"Condition met: {condition}"
            except Exception as e:
                self._logger.error(
                    "skip_condition_eval_failed",
                    sheet_num=sheet_num,
                    condition=condition,
                    error=str(e),
                    exc_info=True,
                )
                # Fail-open: return None so the sheet runs rather than being silently skipped.
                # Matches skip_when_command behavior on error/timeout.
                return None

        # --- Phase 2: Check command-based skip_when_command ---
        cmd_conditions = self.config.sheet.skip_when_command
        if not cmd_conditions or sheet_num not in cmd_conditions:
            return None

        skip_cmd = cmd_conditions[sheet_num]
        command = skip_cmd.command.replace(
            "{workspace}", shlex.quote(str(self.config.workspace))
        )

        proc = None
        try:
            proc = await _asyncio.create_subprocess_shell(
                command,
                stdout=_asyncio.subprocess.DEVNULL,
                stderr=_asyncio.subprocess.DEVNULL,
                cwd=str(self.config.workspace),
            )
            returncode = await _asyncio.wait_for(
                proc.wait(), timeout=skip_cmd.timeout_seconds
            )
        except TimeoutError:
            self._logger.warning(
                "skip_when_command_timeout",
                sheet_num=sheet_num,
                command=command,
                timeout=skip_cmd.timeout_seconds,
            )
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
            return None
        except Exception as e:
            self._logger.error(
                "skip_when_command_error",
                sheet_num=sheet_num,
                command=command,
                error=str(e),
                exc_info=True,
            )
            return None

        if returncode == 0:
            reason = skip_cmd.description or f"Command succeeded: {skip_cmd.command}"
            return reason

        return None

    async def _execute_sequential_mode(self, state: CheckpointState) -> None:
        """Execute sheets sequentially (one at a time).

        This is the traditional execution mode, used when parallel
        execution is disabled.

        Args:
            state: Job checkpoint state.
        """
        # Use DAG-aware sheet selection (v17 evolution)
        # Falls back to sequential if no dependencies configured
        next_sheet = self._get_next_sheet_dag_aware(state)
        while next_sheet is not None and next_sheet <= state.total_sheets:
            # Check for shutdown request before starting sheet
            if self._shutdown_requested:
                await self._handle_graceful_shutdown(state)

            # Check for pause signal before starting sheet (Job Control Integration)
            if self._check_pause_signal(state):
                await self._handle_pause_request(state, next_sheet)

            # Evaluate conditional skip rules (GH#13)
            skip_reason = await self._should_skip_sheet(next_sheet, state)
            if skip_reason:
                state.mark_sheet_skipped(next_sheet, reason=skip_reason)
                await self.state_backend.save(state)
                self.console.print(
                    f"[yellow]Sheet {next_sheet}: Skipped ({skip_reason})[/yellow]"
                )
                next_sheet = self._get_next_sheet_dag_aware(state)
                continue

            sheet_start = time.monotonic()

            try:
                await self._execute_sheet_with_recovery(state, next_sheet)
            except asyncio.CancelledError:
                # Roll back in-progress sheet so it can be resumed later
                sheet_state = state.sheets.get(next_sheet)
                if sheet_state and sheet_state.status == SheetStatus.IN_PROGRESS:
                    sheet_state.status = SheetStatus.PENDING
                state.mark_job_paused()
                await self.state_backend.save(state)
                self._logger.warning(
                    "job.cancelled",
                    job_id=state.job_id,
                    sheet_num=next_sheet,
                )
                raise
            except RateLimitExhaustedError as e:
                # Rate limit exhaustion → PAUSE, not FAIL.
                # The job can be resumed after the rate limit resets.
                state.resume_at = (
                    e.resume_after.isoformat() if e.resume_after else None
                )
                state.mark_job_paused()
                await self.state_backend.save(state)
                self._finalize_summary(state)
                self._logger.warning(
                    "job.paused.rate_limit_exhausted",
                    job_id=state.job_id,
                    sheet_num=next_sheet,
                    backend_type=e.backend_type,
                    quota_exhaustion=e.quota_exhaustion,
                    resume_at=state.resume_at,
                    duration_seconds=round(time.monotonic() - self._run_start_time, 2),
                )
                raise
            except FatalError as e:
                state.mark_job_failed(str(e))
                await self.state_backend.save(state)
                # Update summary with failure before raising
                self._finalize_summary(state)
                # Log job failure
                self._logger.error(
                    "job.failed",
                    job_id=state.job_id,
                    sheet_num=next_sheet,
                    error=str(e),
                    duration_seconds=round(time.monotonic() - self._run_start_time, 2),
                    completed_sheets=self._summary.completed_sheets if self._summary else 0,
                )
                raise

            # Track sheet timing for ETA calculation
            sheet_duration = time.monotonic() - sheet_start
            self._sheet_times.append(sheet_duration)

            # Update progress callback
            self._update_progress(state)

            # Fire iteration event (Issue #49: Runner Callbacks)
            completed_count = sum(
                1 for s in state.sheets.values()
                if s.status == SheetStatus.COMPLETED
            )
            await self._fire_event("job.iteration", 0, {
                "current_sheet": next_sheet,
                "sheets_completed": completed_count,
                "total_sheets": state.total_sheets,
            })

            # Pause between sheets (#22: emit visible status event)
            if next_sheet < state.total_sheets:
                pause_secs = self.config.pause_between_sheets_seconds
                if pause_secs > 0:
                    self._logger.info(
                        "lifecycle.inter_sheet_pause",
                        pause_seconds=pause_secs,
                        completed_sheet=next_sheet,
                        job_id=state.job_id,
                    )
                    self.console.print(
                        f"[dim]Pausing {pause_secs}s after sheet"
                        f" {next_sheet} (rate limit cooldown)...[/dim]"
                    )
                    self._update_progress(state)
                await self._interruptible_sleep(pause_secs)

            # Use DAG-aware sheet selection (v17 evolution)
            next_sheet = self._get_next_sheet_dag_aware(state)

    async def _execute_parallel_mode(self, state: CheckpointState) -> None:
        """Execute sheets in parallel when dependencies allow (v17 evolution).

        Uses the ParallelExecutor to run batches of independent sheets
        concurrently. Batches are determined by the dependency DAG.

        Args:
            state: Job checkpoint state.
        """
        if self._parallel_executor is None:
            # Fall back to sequential if executor not configured
            await self._execute_sequential_mode(state)
            return

        self._logger.info(
            "parallel.mode_start",
            total_sheets=state.total_sheets,
            max_concurrent=self.config.parallel.max_concurrent,
        )

        batch_count = 0
        while True:
            # Check for shutdown request
            if self._shutdown_requested:
                await self._handle_graceful_shutdown(state)
                return

            # Check for pause signal (Job Control Integration)
            if self._check_pause_signal(state):
                # For parallel mode, pause after current batch completes
                current_sheet = state.last_completed_sheet + 1
                await self._handle_pause_request(state, current_sheet)

            # Get next batch of sheets that can run in parallel
            batch = self._parallel_executor.get_next_parallel_batch(state)

            if not batch:
                # No more sheets to execute
                break

            # Evaluate skip conditions before executing (GH#71 fix)
            # Same logic as sequential mode but applied to the batch
            runnable: list[int] = []
            for sheet_num in batch:
                skip_reason = await self._should_skip_sheet(sheet_num, state)
                if skip_reason:
                    state.mark_sheet_skipped(sheet_num, reason=skip_reason)
                    await self.state_backend.save(state)
                    self.console.print(
                        f"[yellow]Sheet {sheet_num}: Skipped ({skip_reason})[/yellow]"
                    )
                else:
                    runnable.append(sheet_num)

            if not runnable:
                # All sheets in this batch were skipped; loop to get next batch
                continue

            batch_count += 1
            batch_start = time.monotonic()

            self._logger.info(
                "parallel.batch_executing",
                batch_number=batch_count,
                sheets=runnable,
            )

            # Execute the batch
            result = await self._parallel_executor.execute_batch(runnable, state)

            # Track timing (average per sheet in batch)
            batch_duration = time.monotonic() - batch_start
            if result.completed:
                avg_duration = batch_duration / len(result.completed)
                for _ in result.completed:
                    self._sheet_times.append(avg_duration)

            # Update progress
            self._update_progress(state)

            # Synthesize batch outputs (v18 evolution: Result Synthesizer)
            await self._synthesize_batch_outputs(result, state)

            # Save state after every batch (fix: failed-only batches weren't persisting)
            # This ensures both completed and failed sheets are recorded even if later
            # batches fail or the job is interrupted. Individual sheets also save, but
            # this provides a batch-level checkpoint for resilience.
            if result.completed or result.failed:
                await self.state_backend.save(state)
                self._logger.debug(
                    "parallel.batch_state_saved",
                    completed_sheets=result.completed,
                    failed_sheets=result.failed,
                )

            # Handle failures
            if result.failed:
                if self.config.parallel.fail_fast:
                    first_failed = result.failed[0]
                    error_msg = result.error_details.get(first_failed, "Unknown error")
                    state.mark_job_failed(
                        f"Parallel batch failed: Sheet {first_failed} - {error_msg}"
                    )
                    await self.state_backend.save(state)
                    self._finalize_summary(state)
                    self._logger.error(
                        "parallel.batch_failed",
                        failed_sheets=result.failed,
                        completed_sheets=result.completed,
                    )
                    raise FatalError(f"Sheet {first_failed} failed: {error_msg}")
                else:
                    # Track permanently failed sheets so they're not retried
                    # infinitely. The batch already ran retry logic internally
                    # via _execute_sheet_with_recovery, so failures here are
                    # permanent (retries exhausted).
                    for sheet_num in result.failed:
                        self._parallel_executor._permanently_failed.add(sheet_num)
                    self._logger.warning(
                        "parallel.sheets_permanently_failed",
                        failed_sheets=result.failed,
                        batch_number=batch_count,
                    )

            # Pause between batches
            completed_count = len(self._get_completed_sheets(state))
            if completed_count < state.total_sheets:
                await self._interruptible_sleep(
                    self.config.pause_between_sheets_seconds
                )

        self._logger.info(
            "parallel.mode_complete",
            batches_executed=batch_count,
        )

    async def _synthesize_batch_outputs(
        self,
        result: ParallelBatchResult,
        state: CheckpointState,
    ) -> None:
        """Synthesize outputs from a completed parallel batch (v18 evolution).

        Extracts content from completed sheets and runs synthesis to create
        a unified result. The synthesis result is stored in checkpoint state.

        Args:
            result: ParallelBatchResult from batch execution.
            state: Job checkpoint state.
        """
        from mozart.execution.synthesizer import ResultSynthesizer, SynthesisConfig

        # Only synthesize if we have completed sheets
        if not result.completed:
            return

        # Collect outputs from completed sheets
        sheet_outputs: dict[int, str] = {}
        for sheet_num in result.completed:
            sheet_state = state.sheets.get(sheet_num)
            if sheet_state and sheet_state.stdout_tail:
                # Use stdout_tail as the output reference
                sheet_outputs[sheet_num] = sheet_state.stdout_tail

        if not sheet_outputs:
            self._logger.debug(
                "synthesizer.no_outputs",
                batch_sheets=result.sheets,
                completed=result.completed,
            )
            return

        # Run synthesis
        config = SynthesisConfig()
        synthesizer = ResultSynthesizer(config)

        synthesis_result = synthesizer.prepare_synthesis(
            batch_sheets=result.sheets,
            completed_sheets=result.completed,
            failed_sheets=result.failed,
            sheet_outputs=sheet_outputs,
        )

        if synthesis_result.status == "ready":
            synthesis_result = synthesizer.execute_synthesis(synthesis_result)

        # Store in checkpoint state
        state.add_synthesis(synthesis_result.batch_id, synthesis_result.to_dict())

        # Mark result as synthesis-ready
        result.sheet_outputs = sheet_outputs
        result.synthesis_ready = True

        self._logger.info(
            "synthesizer.batch_complete",
            batch_id=synthesis_result.batch_id,
            status=synthesis_result.status,
            sheets_synthesized=len(sheet_outputs),
        )

    # =========================================================================
    # Global Learning Aggregation (Movement IV-B)
    # =========================================================================

    async def _aggregate_to_global_store(self, state: CheckpointState) -> None:
        """Aggregate job outcomes to the global learning store.

        Called on job completion to record all outcomes and detect patterns
        for cross-workspace learning. This is the key integration point for
        Movement IV-B global learning.

        Args:
            state: Final job state with all sheet outcomes.
        """
        if self._global_learning_store is None:
            return

        # Import here to avoid circular imports
        from mozart.learning.aggregator import EnhancedPatternAggregator
        from mozart.learning.outcomes import SheetOutcome

        # Build SheetOutcome objects from state
        outcomes: list[SheetOutcome] = []
        for sheet_num, sheet_state in state.sheets.items():
            # Map tri-state validation (True/False/None) to pass rate
            if sheet_state.validation_passed is True:
                pass_rate = 100.0
            elif sheet_state.validation_passed is False:
                pass_rate = 0.0
            else:
                pass_rate = 50.0  # Unknown

            outcome = SheetOutcome(
                sheet_id=f"{state.job_id}_sheet_{sheet_num}",
                job_id=state.job_id,
                validation_results=sheet_state.validation_details or [],
                execution_duration=sheet_state.execution_duration_seconds or 0.0,
                retry_count=max(0, sheet_state.attempt_count - 1),
                completion_mode_used=sheet_state.completion_attempts > 0,
                final_status=sheet_state.status,
                validation_pass_rate=pass_rate,
                success_without_retry=sheet_state.success_without_retry,
                timestamp=sheet_state.completed_at or utc_now(),
                stdout_tail=sheet_state.stdout_tail or "",
                stderr_tail=sheet_state.stderr_tail or "",
                patterns_applied=sheet_state.applied_pattern_descriptions,
                error_history=[e.model_dump() for e in sheet_state.error_history],
                grounding_passed=sheet_state.grounding_passed,
                grounding_confidence=sheet_state.grounding_confidence,
                grounding_guidance=sheet_state.grounding_guidance,
            )
            outcomes.append(outcome)

        if not outcomes:
            return

        # Aggregate outcomes (enhanced aggregator extracts output patterns)
        aggregator = EnhancedPatternAggregator(self._global_learning_store)
        result = aggregator.aggregate_outcomes(
            outcomes=outcomes,
            workspace_path=self.config.workspace,
            model=self.config.backend.model,
            instrument_name=self.config.backend.type,
        )

        self._logger.info(
            "learning.global_aggregation",
            job_id=state.job_id,
            outcomes_recorded=result.outcomes_recorded,
            patterns_detected=result.patterns_detected,
            patterns_merged=result.patterns_merged,
            priorities_updated=result.priorities_updated,
        )

        if result.outcomes_recorded > 0:
            self.console.print(
                f"[dim]Global learning: {result.outcomes_recorded} outcomes recorded, "
                f"{result.patterns_detected} patterns detected[/dim]"
            )


# Re-export for convenience
__all__ = [
    "LifecycleMixin",
]
