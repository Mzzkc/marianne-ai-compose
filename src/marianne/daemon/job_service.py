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
from typing import TYPE_CHECKING, Any

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.logging import get_logger
from marianne.daemon.exceptions import JobSubmissionError
from marianne.daemon.output import NullOutput, OutputProtocol

if TYPE_CHECKING:
    from marianne.core.config import JobConfig
    from marianne.daemon.pgroup import ProcessGroupManager
    from marianne.daemon.registry import JobRegistry
    from marianne.learning.global_store import GlobalLearningStore
    from marianne.notifications.base import NotificationManager
    from marianne.state.base import StateBackend

_logger = get_logger("daemon.job_service")

# Type alias for rate-limit callbacks: (backend_type, wait_seconds, job_id, sheet_num)
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
        signal_file = workspace / f".marianne-pause-{job_id}"
        signal_file.touch()

        self._output.job_event(
            job_id,
            "pause_signal_sent",
            {
                "signal_file": str(signal_file),
            },
        )

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
                self._notification_consecutive_failures >= self._NOTIFICATION_DEGRADED_THRESHOLD
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

    @staticmethod
    def _create_state_backend(
        workspace: Path,
        backend_type: str = "json",
    ) -> StateBackend:
        """Create state persistence backend.

        Delegates to ``execution.setup.create_state_backend()``.
        """
        from marianne.execution.setup import create_state_backend

        return create_state_backend(workspace, backend_type)

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
        from marianne.state import JsonStateBackend, SQLiteStateBackend

        if not workspace.exists():
            raise JobSubmissionError(f"Workspace not found: {workspace}")

        backends: list[tuple[str, StateBackend]] = []

        # Check for SQLite first (preferred for concurrent access)
        sqlite_path = workspace / ".marianne-state.db"
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

            raise JobSubmissionError(f"Job '{job_id}' not found in workspace: {workspace}")
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
        from marianne.core.config import JobConfig as JC

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
                    raise JobSubmissionError(f"Error reloading config from {path}: {e}") from e

        # Priority 3: Config snapshot from state
        if state.config_snapshot:
            try:
                return JC.model_validate(state.config_snapshot), False
            except Exception as e:
                raise JobSubmissionError(f"Error reconstructing config from snapshot: {e}") from e

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
