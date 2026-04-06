"""Core job execution service — decoupled from CLI.

Extracted from CLI commands to enable both CLI and daemon usage.
The CLI becomes a thin wrapper around this service.

The service encapsulates the run/resume/pause/status lifecycle
without any dependency on Rich, Typer, or CLI-level globals.
All user-facing output goes through OutputProtocol.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from mozart.core.checkpoint import CheckpointState, JobStatus
from mozart.core.logging import get_logger
from mozart.daemon.exceptions import JobSubmissionError
from mozart.daemon.output import NullOutput, OutputProtocol

if TYPE_CHECKING:
    from mozart.backends.base import Backend
    from mozart.core.config import JobConfig
    from mozart.daemon.pgroup import ProcessGroupManager
    from mozart.daemon.registry import JobRegistry
    from mozart.execution.grounding import GroundingEngine
    from mozart.execution.parallel import ResourceChecker
    from mozart.execution.runner import JobRunner
    from mozart.execution.runner.models import RunSummary
    from mozart.learning.global_store import GlobalLearningStore
    from mozart.learning.outcomes import OutcomeStore
    from mozart.notifications.base import NotificationManager
    from mozart.state.base import StateBackend

_logger = get_logger("daemon.job_service")

# Type alias for rate-limit callbacks: (backend_type, wait_seconds, job_id, sheet_num)
# Matches RunnerContext.rate_limit_callback signature.
RateLimitCallback = Callable[[str, float, str, int], Any]

# Type alias for event callbacks: (job_id, sheet_num, event, data)
EventCallback = Callable[[str, int, str, dict[str, Any] | None], Any]

# Type alias for state-publish callbacks: (CheckpointState) → None
# Fired on every state_backend.save() so the conductor tracks live state.
StatePublishCallback = Callable[[CheckpointState], Any]


class _PublishingBackend:
    """StateBackend wrapper that publishes state to the conductor on save.

    Decorates the real backend transparently — the runner never knows the
    difference.  Every ``save()`` call first persists to the real backend,
    then fires the publish callback so the conductor's in-memory state
    stays current.  Callback failures are logged but never propagate
    (they must not interfere with job execution).
    """

    def __init__(
        self,
        inner: StateBackend,
        callback: StatePublishCallback,
    ) -> None:
        self._inner = inner
        self._callback = callback

    async def save(self, state: CheckpointState) -> None:
        await self._inner.save(state)
        try:
            result = self._callback(state)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            _logger.warning(
                "state_publish_callback.error",
                job_id=state.job_id,
                exc_info=True,
            )

    # ── Delegate everything else ──────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _JobComponents(TypedDict):
    """Typed container for execution components created by _setup_components."""

    backend: Backend
    outcome_store: OutcomeStore | None
    global_learning_store: GlobalLearningStore | None
    notification_manager: NotificationManager | None
    escalation_handler: None
    grounding_engine: GroundingEngine | None


class JobService:
    """Core job execution service.

    Encapsulates the logic from CLI run/resume/pause commands into
    a reusable service that can be called from CLI, daemon, dashboard,
    or MCP server.

    All user-facing output goes through the OutputProtocol abstraction,
    allowing different frontends (Rich console, structlog, SSE, null)
    to receive execution events without code changes.
    """

    _NOTIFICATION_DEGRADED_THRESHOLD = 3

    def __init__(
        self,
        *,
        output: OutputProtocol | None = None,
        global_learning_store: GlobalLearningStore | None = None,
        rate_limit_callback: RateLimitCallback | None = None,
        event_callback: EventCallback | None = None,
        state_publish_callback: StatePublishCallback | None = None,
        registry: JobRegistry | None = None,
        token_warning_threshold: int | None = None,
        token_error_threshold: int | None = None,
        pgroup_manager: ProcessGroupManager | None = None,
    ) -> None:
        self._output = output or NullOutput()
        self._learning_store = global_learning_store
        self._rate_limit_callback = rate_limit_callback
        self._event_callback = event_callback
        self._state_publish_callback = state_publish_callback
        self._registry = registry
        self._token_warning_threshold = token_warning_threshold
        self._token_error_threshold = token_error_threshold
        self._pgroup_manager = pgroup_manager
        self._notification_consecutive_failures = 0
        self._notifications_degraded = False

    @property
    def notifications_degraded(self) -> bool:
        """Whether notification delivery is degraded.

        Returns True after ``_NOTIFICATION_DEGRADED_THRESHOLD`` consecutive
        notification failures.  Readable by ``HealthChecker.readiness()``
        to signal degraded notification capability to operators.
        """
        return self._notifications_degraded

    # ─── Job Lifecycle ───────────────────────────────────────────────────

    async def start_job(
        self,
        config: JobConfig,
        *,
        conductor_job_id: str | None = None,
        fresh: bool = False,
        start_sheet: int | None = None,
        self_healing: bool = False,
        self_healing_auto_confirm: bool = False,
        dry_run: bool = False,
        pause_event: asyncio.Event | None = None,
        config_path: str | None = None,
        resource_checker: ResourceChecker | None = None,
    ) -> RunSummary:
        """Start a job from config.

        Mirrors the logic in cli/commands/run.py::_run_job():
        1. Ensure workspace exists
        2. Handle --fresh (delete state, archive workspace)
        3. Setup backends and components via _setup_components()
        4. Create RunnerContext and JobRunner
        5. Call runner.run()

        Args:
            config: Validated job configuration.
            fresh: Delete existing state before running.
            self_healing: Enable automatic diagnosis and remediation.
            self_healing_auto_confirm: Auto-confirm suggested fixes.
            dry_run: If True, validate and return without executing.

        Returns:
            RunSummary with execution statistics.

        Raises:
            JobSubmissionError: If config is invalid or workspace setup fails.
            DaemonError: For unexpected daemon-level failures.
        """
        job_id = conductor_job_id or config.name

        self._output.job_event(job_id, "starting", {
            "total_sheets": config.sheet.total_sheets,
            "fresh": fresh,
        })

        # Ensure workspace exists
        config.workspace.mkdir(parents=True, exist_ok=True)

        # Create state backend
        state_backend = self._create_state_backend(config.workspace, config.state_backend)

        # Handle --fresh: delete existing state
        if fresh:
            was_deleted = await state_backend.delete(config.name)
            if was_deleted:
                self._output.log("info", "Deleted existing state for fresh start",
                                 job_id=job_id)

            # Archive workspace files if configured
            if config.workspace_lifecycle.archive_on_fresh:
                from mozart.workspace.lifecycle import WorkspaceArchiver

                archiver = WorkspaceArchiver(config.workspace, config.workspace_lifecycle)
                archive_path = archiver.archive()
                if archive_path:
                    self._output.log("info", "Archived workspace",
                                     job_id=job_id, archive=archive_path.name)

        if dry_run:
            # Return a minimal summary for dry runs
            from mozart.execution.runner.models import RunSummary

            return RunSummary(
                job_id=job_id,
                job_name=config.name,
                total_sheets=config.sheet.total_sheets,
                final_status=JobStatus.PENDING,
            )

        # Setup all execution components (backend, learning, notifications, etc.)
        components = self._setup_components(config)
        notification_manager = components["notification_manager"]

        # Wrap backend so every checkpoint publishes to the conductor
        runner_backend = self._wrap_state_backend(state_backend)

        try:
            runner = self._create_runner(
                config, components, runner_backend,
                job_id=job_id,
                self_healing=self_healing,
                self_healing_auto_confirm=self_healing_auto_confirm,
                pause_event=pause_event,
                resource_checker=resource_checker,
            )

            return await self._execute_runner(
                runner=runner,
                job_id=job_id,
                job_name=config.name,
                total_sheets=config.sheet.total_sheets,
                notification_manager=notification_manager,
                start_sheet=start_sheet,
                config_path=config_path,
            )
        finally:
            if notification_manager:
                await self._safe_notify(
                    notification_manager,
                    notification_manager.close(),
                    "notification_cleanup_on_setup_failure",
                )
            await state_backend.close()

    async def resume_job(
        self,
        job_id: str,
        workspace: Path,
        *,
        conductor_job_id: str | None = None,
        config: JobConfig | None = None,
        config_path: Path | None = None,
        no_reload: bool = False,
        self_healing: bool = False,
        self_healing_auto_confirm: bool = False,
        pause_event: asyncio.Event | None = None,
        resource_checker: ResourceChecker | None = None,
    ) -> RunSummary:
        """Resume a paused or failed job.

        Mirrors the logic in cli/commands/resume.py::_resume_job():
        1. Find and validate job state
        2. Reconstruct config (auto-reload from file, snapshot fallback)
        3. Calculate resume point
        4. Setup backends and components
        5. Create runner and call runner.run(start_sheet=...)

        Args:
            job_id: Job identifier to resume.
            workspace: Workspace directory containing job state.
            config: Optional explicit JobConfig (overrides snapshot).
            config_path: Path to config file for reload.
            no_reload: If True, skip auto-reload and use cached snapshot.
            self_healing: Enable automatic diagnosis and remediation.
            self_healing_auto_confirm: Auto-confirm suggested fixes.

        Returns:
            RunSummary with execution statistics.

        Raises:
            JobSubmissionError: If job state not found or not resumable.
            DaemonError: For unexpected failures.
        """
        # Phase 1: Find job state (ERROR severity if fallback used during resume)
        found_state, found_backend = await self._find_job_state(
            job_id, workspace, for_resume=True,
        )

        # Use conductor's ID for runtime identity (state publish, events).
        # The disk lookup above still uses the original job_id (config.name).
        runtime_id = conductor_job_id or found_state.job_id

        # Validate resumable state
        resumable_statuses = {JobStatus.PAUSED, JobStatus.FAILED, JobStatus.RUNNING}
        if found_state.status not in resumable_statuses:
            if found_state.status == JobStatus.COMPLETED:
                raise JobSubmissionError(
                    f"Job '{job_id}' is already completed. "
                    "Cannot resume a completed job without force flag."
                )
            elif found_state.status == JobStatus.PENDING:
                raise JobSubmissionError(
                    f"Job '{job_id}' has not been started yet. Use start_job() instead."
                )

        # Phase 2: Reconstruct config (auto-reload from file by default)
        resolved_config, was_reloaded = self._reconstruct_config(
            found_state, config=config, config_path=config_path,
            no_reload=no_reload,
        )

        # Reconcile stale state only when config was actually reloaded
        if was_reloaded:
            from mozart.execution.reconciliation import reconcile_config

            report = reconcile_config(found_state, resolved_config)
            # Always update snapshot with latest config
            found_state.config_snapshot = resolved_config.model_dump(mode="json")
            if report.has_changes:
                self._output.job_event(runtime_id, "config_reconciled", {
                    "sections_changed": report.sections_changed,
                    "fields_reset": report.fields_reset,
                })

            # Update persistent registry metadata if workspace or config_path changed
            if self._registry is not None:
                new_ws = str(resolved_config.workspace)
                new_cp = str(config_path) if config_path else found_state.config_path
                await self._registry.update_config_metadata(
                    runtime_id,
                    config_path=new_cp,
                    workspace=new_ws,
                )

        # Calculate resume point
        resume_sheet = found_state.last_completed_sheet + 1
        if resume_sheet > found_state.total_sheets:
            resume_sheet = found_state.total_sheets

        self._output.job_event(runtime_id, "resuming", {
            "resume_sheet": resume_sheet,
            "total_sheets": found_state.total_sheets,
            "previous_status": found_state.status.value,
            "previous_error": found_state.error_message,
            "config_reloaded": was_reloaded,
        })

        # Wrap backend early so the initial state save publishes to the
        # conductor, making ``mozart status`` available immediately.
        runner_backend = self._wrap_state_backend(found_backend)

        # Reset job status to RUNNING
        found_state.status = JobStatus.RUNNING
        found_state.error_message = None
        found_state.pid = None  # Clear stale PID so runner doesn't false-positive
        # Update found_state.job_id to the conductor's runtime identity so
        # all downstream publishes use the correct key.  When the IDs
        # differ, delete the old row first — otherwise the upsert creates
        # a duplicate (different PK) and the orphaned row causes state
        # drift on future resumes.  See issue #111.
        original_id = found_state.job_id
        found_state.job_id = runtime_id
        if original_id != runtime_id:
            await found_backend.delete(original_id)
        await runner_backend.save(found_state)

        # Phase 3: Setup components and run
        components = self._setup_components(resolved_config)
        notification_manager = components["notification_manager"]

        remaining = found_state.total_sheets - found_state.last_completed_sheet
        stored_config_path = (
            str(config_path) if config_path else found_state.config_path
        )

        try:
            runner = self._create_runner(
                resolved_config, components, runner_backend,
                job_id=runtime_id,
                self_healing=self_healing,
                self_healing_auto_confirm=self_healing_auto_confirm,
                pause_event=pause_event,
                resource_checker=resource_checker,
            )

            return await self._execute_runner(
                runner=runner,
                job_id=runtime_id,
                job_name=resolved_config.name,
                total_sheets=resolved_config.sheet.total_sheets,
                notification_manager=notification_manager,
                notify_total_sheets=remaining,
                start_sheet=resume_sheet,
                config_path=stored_config_path,
            )
        finally:
            if notification_manager:
                await self._safe_notify(
                    notification_manager,
                    notification_manager.close(),
                    "notification_cleanup_on_setup_failure",
                )
            await found_backend.close()

    async def pause_job(self, job_id: str, workspace: Path) -> bool:
        """Pause a running job via signal file.

        Mirrors the logic in cli/commands/pause.py::_pause_job():
        Creates a pause signal file that the runner polls at sheet boundaries.

        Args:
            job_id: Job identifier to pause.
            workspace: Workspace directory containing job state.

        Returns:
            True if pause signal was created successfully.

        Raises:
            JobSubmissionError: If job not found or not in a pausable state.
        """
        found_state, found_backend = await self._find_job_state(job_id, workspace)
        await found_backend.close()

        if found_state.status != JobStatus.RUNNING:
            raise JobSubmissionError(
                f"Job '{job_id}' is {found_state.status.value}, not running. "
                "Only running jobs can be paused."
            )

        # Create pause signal file
        signal_file = workspace / f".mozart-pause-{job_id}"
        signal_file.touch()

        self._output.job_event(job_id, "pause_signal_sent", {
            "signal_file": str(signal_file),
        })

        return True

    async def get_status(
        self,
        job_id: str,
        workspace: Path,
        backend_type: str = "sqlite",
    ) -> CheckpointState | None:
        """Get job status from state backend.

        Args:
            job_id: Job identifier.
            workspace: Workspace directory containing job state.
            backend_type: State backend type (default "sqlite" for daemon).

        Returns:
            CheckpointState if found, None if job doesn't exist.
        """
        state_backend = self._create_state_backend(workspace, backend_type)
        try:
            return await state_backend.load(job_id)
        finally:
            await state_backend.close()

    # ─── Internal Helpers ────────────────────────────────────────────────

    def _wrap_state_backend(self, backend: StateBackend) -> StateBackend:
        """Wrap a state backend with publish-on-save if a callback is set.

        When the conductor provides a ``state_publish_callback``, every
        ``save()`` call on the returned backend also publishes the
        ``CheckpointState`` to the conductor's in-memory live-state map.
        Without a callback (e.g. CLI usage), returns the backend unchanged.
        """
        if self._state_publish_callback is None:
            return backend
        return _PublishingBackend(backend, self._state_publish_callback)  # type: ignore[return-value]

    def _create_runner(
        self,
        config: JobConfig,
        components: _JobComponents,
        state_backend: StateBackend,
        *,
        job_id: str,
        self_healing: bool = False,
        self_healing_auto_confirm: bool = False,
        pause_event: asyncio.Event | None = None,
        resource_checker: ResourceChecker | None = None,
    ) -> JobRunner:
        """Create a configured JobRunner from components."""
        from mozart.execution.runner import JobRunner as JR
        from mozart.execution.runner import RunnerContext

        def _progress_callback(completed: int, total: int, eta_seconds: float | None) -> None:
            self._output.progress(job_id, completed, total, eta_seconds)

        runner_context = RunnerContext(
            outcome_store=components["outcome_store"],
            escalation_handler=None,
            progress_callback=_progress_callback,
            global_learning_store=components["global_learning_store"] or self._learning_store,
            grounding_engine=components["grounding_engine"],
            rate_limit_callback=self._rate_limit_callback,
            event_callback=self._event_callback,
            self_healing_enabled=self_healing,
            self_healing_auto_confirm=self_healing_auto_confirm,
            pause_event=pause_event,
            daemon_managed=True,
            token_warning_threshold=self._token_warning_threshold,
            token_error_threshold=self._token_error_threshold,
            resource_checker=resource_checker,
        )

        return JR(
            config=config,
            backend=components["backend"],
            state_backend=state_backend,
            context=runner_context,
        )

    @staticmethod
    def _get_or_create_summary(
        runner: JobRunner,
        job_id: str,
        job_name: str,
        total_sheets: int,
        status: JobStatus,
    ) -> RunSummary:
        """Get summary from runner or create a minimal fallback."""
        from mozart.execution.runner.models import RunSummary

        summary = runner.get_summary()
        if summary:
            summary.final_status = status
            return summary
        return RunSummary(
            job_id=job_id,
            job_name=job_name,
            total_sheets=total_sheets,
            final_status=status,
        )

    async def _safe_notify(
        self,
        manager: NotificationManager | None,
        coro: Coroutine[Any, Any, Any],
        context: str,
    ) -> None:
        """Await a notification coroutine, logging exceptions as warnings.

        Tracks consecutive failures and sets ``notifications_degraded``
        after ``_NOTIFICATION_DEGRADED_THRESHOLD`` consecutive failures.
        Resets the counter on any success.

        Callers must guard with ``if manager:`` before constructing the
        coroutine.  The None check here is a defensive fallback only.
        """
        if manager is None:
            _logger.warning(
                "notification_manager_none",
                context=context,
                hint="Caller should check `if manager:` before creating coroutine",
            )
            coro.close()
            return
        try:
            await coro
            if self._notification_consecutive_failures > 0:
                _logger.info(
                    "notification_recovered",
                    after_failures=self._notification_consecutive_failures,
                )
                self._notification_consecutive_failures = 0
                self._notifications_degraded = False
        except (OSError, ConnectionError, TimeoutError):
            self._notification_consecutive_failures += 1
            if (
                self._notification_consecutive_failures
                >= self._NOTIFICATION_DEGRADED_THRESHOLD
                and not self._notifications_degraded
            ):
                self._notifications_degraded = True
                _logger.error(
                    "notifications_degraded",
                    consecutive_failures=self._notification_consecutive_failures,
                    message="Notification delivery degraded — "
                    "health probes will report this condition.",
                    exc_info=True,
                )
            else:
                _logger.warning(context, exc_info=True)

    async def _execute_runner(
        self,
        runner: JobRunner,
        job_id: str,
        job_name: str,
        total_sheets: int,
        notification_manager: NotificationManager | None,
        notify_total_sheets: int | None = None,
        start_sheet: int | None = None,
        config_path: str | None = None,
    ) -> RunSummary:
        """Execute a runner with unified error handling.

        Handles GracefulShutdownError (→ PAUSED), FatalError (→ FAILED),
        and notification lifecycle (start, complete/fail, close).
        """
        from mozart.execution.runner import (
            FatalError,
            GracefulShutdownError,
            RateLimitExhaustedError,
        )

        _notify = self._safe_notify

        try:
            if notification_manager:
                await _notify(
                    notification_manager,
                    notification_manager.notify_job_start(
                        job_id=job_id,
                        job_name=job_name,
                        total_sheets=notify_total_sheets or total_sheets,
                    ),
                    "notification_failed_during_start",
                )

            run_kwargs: dict[str, Any] = {}
            if start_sheet is not None:
                run_kwargs["start_sheet"] = start_sheet
            if config_path is not None:
                run_kwargs["config_path"] = config_path

            state, summary = await runner.run(**run_kwargs)

            if notification_manager:
                if state.status == JobStatus.COMPLETED:
                    await _notify(
                        notification_manager,
                        notification_manager.notify_job_complete(
                            job_id=job_id,
                            job_name=job_name,
                            success_count=summary.completed_sheets,
                            failure_count=summary.failed_sheets,
                            duration_seconds=summary.total_duration_seconds,
                        ),
                        "notification_failed_during_completion",
                    )
                elif state.status == JobStatus.FAILED:
                    await _notify(
                        notification_manager,
                        notification_manager.notify_job_failed(
                            job_id=job_id,
                            job_name=job_name,
                            error_message=f"Job failed with status: {state.status.value}",
                            sheet_num=state.current_sheet,
                        ),
                        "notification_failed_during_job_failure",
                    )

            self._output.job_event(job_id, "completed", {
                "status": state.status.value,
                "completed_sheets": summary.completed_sheets,
            })

            return summary

        except GracefulShutdownError:
            self._output.job_event(job_id, "paused")
            return self._get_or_create_summary(
                runner, job_id, job_name, total_sheets, JobStatus.PAUSED,
            )

        except RateLimitExhaustedError as e:
            # Rate limit exhaustion → PAUSED (not FAILED).
            # The lifecycle mixin already saved PAUSED state; we just
            # need to surface the right summary to the daemon.
            self._output.log(
                "warning",
                f"Rate limit exhausted: {e}",
                job_id=job_id,
            )
            self._output.job_event(job_id, "paused", {
                "reason": "rate_limit_exhausted",
                "resume_at": e.resume_after.isoformat() if e.resume_after else None,
            })
            if notification_manager:
                await _notify(
                    notification_manager,
                    notification_manager.notify_job_failed(
                        job_id=job_id,
                        job_name=job_name,
                        error_message=f"Rate limit exhausted (paused): {e}",
                    ),
                    "notification_failed_during_rate_limit_pause",
                )
            return self._get_or_create_summary(
                runner, job_id, job_name, total_sheets, JobStatus.PAUSED,
            )

        except FatalError as e:
            self._output.log("error", f"Fatal error: {e}", job_id=job_id)
            if notification_manager:
                await _notify(
                    notification_manager,
                    notification_manager.notify_job_failed(
                        job_id=job_id,
                        job_name=job_name,
                        error_message=str(e),
                    ),
                    "notification_failed_during_fatal_error",
                )
            return self._get_or_create_summary(
                runner, job_id, job_name, total_sheets, JobStatus.FAILED,
            )

        except Exception as e:
            try:
                self._output.log("error", f"Unexpected error: {e}", job_id=job_id)
            except Exception:
                _logger.warning("output_log_failed_during_error", exc_info=True)
            if notification_manager:
                await _notify(
                    notification_manager,
                    notification_manager.notify_job_failed(
                        job_id=job_id,
                        job_name=job_name,
                        error_message=f"Unexpected error: {e}",
                    ),
                    "notification_failed_during_unexpected_error",
                )
            raise

        finally:
            if notification_manager:
                await _notify(
                    notification_manager,
                    notification_manager.close(),
                    "notification_cleanup_failed",
                )

    @staticmethod
    def _create_state_backend(
        workspace: Path,
        backend_type: str = "json",
    ) -> StateBackend:
        """Create state persistence backend.

        Delegates to ``execution.setup.create_state_backend()``.
        """
        from mozart.execution.setup import create_state_backend

        return create_state_backend(workspace, backend_type)

    def _setup_components(self, config: JobConfig) -> _JobComponents:
        """Setup all execution components for a job.

        Delegates to shared ``execution.setup`` functions, which are also
        used by the CLI (``cli/commands/_shared.py``).  This eliminates
        the duplicated "mirrors _shared.py" setup logic.
        """
        from mozart.execution.setup import (
            create_backend,
            setup_grounding,
            setup_learning,
            setup_notifications,
        )

        backend = create_backend(config)

        # Wire PID tracking for orphan detection when running under daemon.
        if self._pgroup_manager is not None:
            if hasattr(backend, "_on_process_spawned"):
                backend._on_process_spawned = self._pgroup_manager.track_backend_pid
            if hasattr(backend, "_on_process_exited"):
                backend._on_process_exited = self._pgroup_manager.untrack_backend_pid

        outcome_store, global_learning_store = setup_learning(
            config,
            global_learning_store_override=self._learning_store,
        )

        notification_manager = setup_notifications(config)

        grounding_engine = setup_grounding(config)

        return {
            "backend": backend,
            "outcome_store": outcome_store,
            "global_learning_store": global_learning_store,
            "notification_manager": notification_manager,
            "escalation_handler": None,  # Interactive-only
            "grounding_engine": grounding_engine,
        }

    async def _find_job_state(
        self,
        job_id: str,
        workspace: Path,
        *,
        for_resume: bool = False,
    ) -> tuple[CheckpointState, StateBackend]:
        """Find and return job state from workspace.

        Mirrors cli/helpers.py::find_job_state() and require_job_state()
        but raises DaemonError instead of calling typer.Exit().

        Args:
            job_id: Job identifier.
            workspace: Workspace directory containing job state.
            for_resume: If True, fallback recovery is logged at ERROR
                level because stale state during resume risks replaying
                already-completed sheets.  Status queries use WARNING.

        Returns:
            Tuple of (CheckpointState, StateBackend).

        Raises:
            JobSubmissionError: If workspace doesn't exist or job not found.
        """
        from mozart.state import JsonStateBackend, SQLiteStateBackend

        if not workspace.exists():
            raise JobSubmissionError(f"Workspace not found: {workspace}")

        backends: list[tuple[str, StateBackend]] = []

        # Check for SQLite first (preferred for concurrent access)
        sqlite_path = workspace / ".mozart-state.db"
        if sqlite_path.exists():
            backends.append(("sqlite", SQLiteStateBackend(sqlite_path)))

        # JSON backend as fallback
        backends.append(("json", JsonStateBackend(workspace)))

        preferred_name = backends[0][0] if backends else None
        failed_backends: list[str] = []
        chosen_backend: StateBackend | None = None

        try:
            for name, backend in backends:
                try:
                    state = await backend.load(job_id)
                    if state is not None:
                        if failed_backends:
                            # Resume operations risk replaying completed sheets
                            # if the fallback state is stale → ERROR severity.
                            # Status queries are read-only → WARNING suffices.
                            log_fn = _logger.error if for_resume else _logger.warning
                            if for_resume:
                                detail = (
                                    "RESUME RISK: fallback state may be stale, "
                                    "risking replay of completed sheets."
                                )
                            else:
                                detail = "Verify state is current."
                            log_fn(
                                "state_recovered_from_fallback_backend",
                                job_id=job_id,
                                preferred_backend=preferred_name,
                                failed_backends=failed_backends,
                                recovered_from=name,
                                operation="resume" if for_resume else "status",
                                message=(
                                    "Preferred backend failed — state loaded "
                                    f"from fallback. {detail}"
                                ),
                            )
                        chosen_backend = backend
                        return state, backend

                    # Exact ID miss — the conductor's job_id (e.g. "score")
                    # may differ from config.name stored in the DB (e.g.
                    # "company-in-a-box").  Each workspace holds a single
                    # job, so listing and picking the sole entry is safe.
                    all_jobs = await backend.list_jobs()
                    if len(all_jobs) == 1:
                        state = all_jobs[0]
                        _logger.info(
                            "state_found_by_workspace_scan",
                            requested_id=job_id,
                            found_id=state.job_id,
                            backend=name,
                        )
                        chosen_backend = backend
                        return state, backend

                except (OSError, sqlite3.Error) as e:
                    failed_backends.append(name)
                    _logger.warning(
                        "error_querying_backend",
                        job_id=job_id,
                        backend=name,
                        error=str(e),
                        exc_info=True,
                    )
                    continue

            raise JobSubmissionError(
                f"Job '{job_id}' not found in workspace: {workspace}"
            )
        finally:
            # Close backends that weren't chosen (resource cleanup)
            for _, backend in backends:
                if backend is not chosen_backend:
                    await backend.close()

    def _reconstruct_config(
        self,
        state: CheckpointState,
        *,
        config: JobConfig | None = None,
        config_path: Path | None = None,
        no_reload: bool = False,
    ) -> tuple[JobConfig, bool]:
        """Reconstruct JobConfig for resume using auto-reload priority.

        Mirrors cli/commands/resume.py::_reconstruct_config() but raises
        exceptions instead of calling typer.Exit().

        Priority order:
        1. Provided config object (explicit override)
        2. Auto-reload from config_path or stored path (default)
        3. Cached config_snapshot (fallback)
        4. Error

        Returns:
            Tuple of (JobConfig, was_reloaded). ``was_reloaded`` is True
            when the config came from a file (priorities 1 or 2), False
            when restored from the cached snapshot (priority 3).

        Raises:
            JobSubmissionError: If no config source is available.
        """
        from mozart.core.config import JobConfig as JC

        # Priority 1: Explicit config
        if config is not None:
            return config, True

        # Priority 2: Auto-reload from file (unless no_reload)
        if not no_reload:
            path = config_path or (Path(state.config_path) if state.config_path else None)
            if path and path.exists():
                try:
                    return JC.from_yaml(path), True
                except Exception as e:
                    raise JobSubmissionError(
                        f"Error reloading config from {path}: {e}"
                    ) from e

        # Priority 3: Config snapshot from state
        if state.config_snapshot:
            try:
                return JC.model_validate(state.config_snapshot), False
            except Exception as e:
                raise JobSubmissionError(
                    f"Error reconstructing config from snapshot: {e}"
                ) from e

        raise JobSubmissionError(
            "Cannot resume: no config available. "
            "Provide a config object or ensure state has a config_snapshot."
        )


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "JobService",
]
