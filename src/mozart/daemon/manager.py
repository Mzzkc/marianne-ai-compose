"""Job manager for the Mozart daemon.

Maps job IDs to asyncio.Tasks, enforces concurrency limits via semaphore,
routes IPC requests to JobService, and cancels all tasks on shutdown.
"""

from __future__ import annotations

import asyncio
import os
import time
import traceback
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

import mozart
from mozart.core.checkpoint import CheckpointState
from mozart.core.logging import get_logger
from mozart.daemon.backpressure import BackpressureController
from mozart.daemon.config import DaemonConfig
from mozart.daemon.event_bus import EventBus
from mozart.daemon.exceptions import DaemonError, JobSubmissionError
from mozart.daemon.job_service import JobService
from mozart.daemon.learning_hub import LearningHub
from mozart.daemon.monitor import ResourceMonitor
from mozart.daemon.observer import JobObserver
from mozart.daemon.observer_recorder import ObserverRecorder
from mozart.daemon.output import StructuredOutput
from mozart.daemon.rate_coordinator import RateLimitCoordinator
from mozart.daemon.registry import DaemonJobStatus, JobRecord, JobRegistry
from mozart.daemon.scheduler import GlobalSheetScheduler
from mozart.daemon.semantic_analyzer import SemanticAnalyzer
from mozart.daemon.snapshot import SnapshotManager
from mozart.daemon.task_utils import log_task_exception
from mozart.daemon.types import JobRequest, JobResponse, ObserverEvent

_logger = get_logger("daemon.manager")


@dataclass
class JobMeta:
    """Metadata tracked per job in the manager."""

    job_id: str
    config_path: Path
    workspace: Path
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    status: DaemonJobStatus = DaemonJobStatus.QUEUED
    error_message: str | None = None
    error_traceback: str | None = None
    chain_depth: int | None = None
    hook_config: list[dict[str, Any]] | None = field(default=None, repr=False)
    concert_config: dict[str, Any] | None = field(default=None, repr=False)
    completed_new_work: bool = False
    observer: JobObserver | None = field(default=None, repr=False)
    pending_modify: tuple[Path, Path | None] | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for JSON-RPC responses."""
        result: dict[str, Any] = {
            "job_id": self.job_id,
            "status": self.status,
            "config_path": str(self.config_path),
            "workspace": str(self.workspace),
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
        }
        if self.error_message:
            result["error_message"] = self.error_message
        if self.error_traceback:
            result["error_traceback"] = self.error_traceback
        if self.chain_depth is not None:
            result["chain_depth"] = self.chain_depth
        return result


class JobManager:
    """Manages concurrent job execution within the daemon.

    Wraps JobService with task tracking and concurrency control.
    Each submitted job becomes an asyncio.Task that the manager
    tracks from start to completion/cancellation.
    """

    def __init__(
        self,
        config: DaemonConfig,
        *,
        start_time: float | None = None,
        monitor: ResourceMonitor | None = None,
    ) -> None:
        self._config = config
        self._start_time = start_time or time.monotonic()

        # Phase 3: Centralized learning hub.
        # Single GlobalLearningStore shared across all jobs — pattern
        # discoveries in Job A are instantly available to Job B.
        self._learning_hub = LearningHub()

        # Deferred to start() where the learning hub's store is available.
        self._service: JobService | None = None
        self._jobs: dict[str, asyncio.Task[Any]] = {}
        self._job_meta: dict[str, JobMeta] = {}
        # Live CheckpointState per running job — populated by
        # _PublishingBackend on every state_backend.save() so the
        # conductor can serve status from memory, not disk.
        # Keyed by conductor job_id (which may be deduplicated, e.g.
        # "issue-solver-2"), not the config's name field.
        self._live_states: dict[str, CheckpointState] = {}
        # In-process pause events per job — set by pause_job(), checked by
        # the runner at sheet boundaries.  Keyed by conductor job_id.
        self._pause_events: dict[str, asyncio.Event] = {}
        # Explicit config.name → conductor_id mapping.  Populated in
        # _run_job_task when the config is parsed (config.name becomes
        # known).  Used by _on_state_published as a fallback when
        # state.job_id doesn't match any _job_meta key — O(1) lookup
        # instead of the fragile linear scan it replaces.
        self._config_name_to_conductor_id: dict[str, str] = {}
        self._concurrency_semaphore = asyncio.Semaphore(
            config.max_concurrent_jobs,
        )
        self._id_gen_lock = asyncio.Lock()
        self._shutting_down = False
        self._shutdown_event = asyncio.Event()
        self._recent_failures: deque[float] = deque()
        # v25: Optional entropy check callback set by process.py after health checker init
        self._entropy_check_callback: Callable[[], None] | None = None

        # Phase 3: Global sheet scheduler — lazily initialized via property.
        # Infrastructure is built and tested but not yet wired into the
        # execution path.  Currently, jobs run monolithically via
        # JobService.start_job().  Lazy init avoids allocating resources
        # until Phase 3 is actually wired.
        self._scheduler_instance: GlobalSheetScheduler | None = None

        # Phase 3: Cross-job rate limit coordination.
        # Built and tested; wired into the scheduler so next_sheet()
        # skips rate-limited backends.  Not yet active because the
        # scheduler itself is not yet driving execution.
        self._rate_coordinator = RateLimitCoordinator()

        # Phase 3: Backpressure controller.
        # Uses a single ResourceMonitor instance shared with DaemonProcess
        # for both periodic monitoring and point-in-time backpressure checks.
        # When no monitor is injected (e.g. unit tests), a standalone one
        # is created that only does point-in-time reads.
        self._monitor = monitor or ResourceMonitor(config.resource_limits, manager=self)
        self._backpressure = BackpressureController(
            self._monitor, self._rate_coordinator,
        )

        # Persistent job registry — survives daemon restarts.
        db_path = config.state_db_path.expanduser()
        self._registry = JobRegistry(db_path)

        # Event bus for routing runner and observer events to consumers.
        self._event_bus = EventBus(
            max_queue_size=config.observer.max_queue_size,
        )

        # Semantic analyzer — LLM-based analysis of sheet completions.
        # Initialized in start() after the event bus is ready.
        self._semantic_analyzer: SemanticAnalyzer | None = None

        # Phase 4: Completion snapshots — captures workspace artifacts
        # at job completion with TTL-based cleanup.
        self._snapshot_manager = SnapshotManager()

        # Observer event recorder — persists per-job observer events to JSONL.
        # Initialized eagerly, started in start() after event bus.
        self._observer_recorder: ObserverRecorder | None = None

    # ─── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start daemon subsystems (learning hub, monitor, etc.)."""
        # Open the async registry connection (tables + WAL mode)
        await self._registry.open()

        # Recover orphaned jobs (left running/queued from previous daemon).
        # Pause-aware: check each orphan's checkpoint to distinguish truly
        # running jobs from those that were mid-pause when the daemon died.
        orphans = await self._registry.get_orphaned_jobs()
        if orphans:
            failed_count = 0
            paused_count = 0
            for orphan in orphans:
                target_status = self._classify_orphan(orphan)
                await self._registry.update_status(
                    orphan.job_id,
                    target_status,
                    error_message=(
                        "Daemon restarted while job was active"
                        if target_status == DaemonJobStatus.FAILED
                        else None
                    ),
                )
                if target_status == DaemonJobStatus.PAUSED:
                    paused_count += 1
                else:
                    failed_count += 1
            _logger.info(
                "manager.orphans_recovered",
                count=len(orphans),
                failed=failed_count,
                paused=paused_count,
            )

        # Restore ALL job metadata from registry into memory so that
        # RPC handlers (status, resume, pause, errors, …) work for
        # jobs from previous daemon sessions without per-method fallback.
        all_records = await self._registry.list_jobs(limit=10_000)
        for record in all_records:
            if record.job_id not in self._job_meta:
                self._job_meta[record.job_id] = JobMeta(
                    job_id=record.job_id,
                    config_path=Path(record.config_path),
                    workspace=Path(record.workspace),
                    submitted_at=record.submitted_at,
                    started_at=record.started_at,
                    status=record.status,
                    error_message=record.error_message,
                )
        if all_records:
            _logger.info(
                "manager.registry_restored",
                total=len(all_records),
                loaded=len(self._job_meta),
            )

        await self._learning_hub.start()
        await self._event_bus.start()
        # Create service with shared store now that the hub is initialized
        self._service = JobService(
            output=StructuredOutput(event_bus=self._event_bus),
            global_learning_store=self._learning_hub.store,
            rate_limit_callback=self._on_rate_limit,
            event_callback=self._on_event,
            state_publish_callback=self._on_state_published,
            registry=self._registry,
        )
        # Start semantic analyzer after event bus (needs bus for subscription).
        # Failure must not prevent the conductor from starting.
        try:
            from mozart.execution.setup import create_backend_from_config

            semantic_backend = create_backend_from_config(
                self._config.learning.backend,
            )
            self._semantic_analyzer = SemanticAnalyzer(
                config=self._config.learning,
                backend=semantic_backend,
                learning_hub=self._learning_hub,
                live_states=self._live_states,
            )
            await self._semantic_analyzer.start(self._event_bus)
        except (OSError, ValueError, RuntimeError, TypeError):
            _logger.warning(
                "manager.semantic_analyzer_start_failed",
                exc_info=True,
            )
            self._semantic_analyzer = None

        # Start observer recorder after event bus (needs bus for subscription).
        # Guard: observer.enabled, NOT persist_events. The ring buffer serves
        # mozart top even when persistence is off.
        if self._config.observer.enabled:
            self._observer_recorder = ObserverRecorder(
                config=self._config.observer,
            )
            await self._observer_recorder.start(self._event_bus)

        _logger.info(
            "manager.started",
            scheduler_status="lazy_not_wired",
            scheduler_note="Phase 3 scheduler is lazily initialized and not "
            "yet driving execution. Jobs run monolithically via JobService.",
            semantic_analyzer="active" if self._semantic_analyzer else "unavailable",
            observer_recorder="active" if self._observer_recorder else "unavailable",
        )

    @property
    def _scheduler(self) -> GlobalSheetScheduler:
        """Lazily create the Phase 3 scheduler on first access."""
        if self._scheduler_instance is None:
            self._scheduler_instance = GlobalSheetScheduler(self._config)
            self._scheduler_instance.set_rate_limiter(self._rate_coordinator)
            self._scheduler_instance.set_backpressure(self._backpressure)
        return self._scheduler_instance

    @property
    def _checked_service(self) -> JobService:
        """Get the job service, raising if not yet started."""
        if self._service is None:
            raise RuntimeError("JobManager not started — call start() first")
        return self._service

    def apply_config(self, new_config: DaemonConfig) -> None:
        """Hot-apply reloadable config fields from a SIGHUP reload.

        Compares the new config against the current one and applies
        changes that can be safely updated at runtime.  Rebuilds the
        concurrency semaphore if ``max_concurrent_jobs`` changed.

        Safe because asyncio is single-threaded — this runs in the
        event loop, so no concurrent access to ``_config`` or
        ``_concurrency_semaphore`` is possible.
        """
        old = self._config

        # Rebuild semaphore if concurrency limit changed
        if new_config.max_concurrent_jobs != old.max_concurrent_jobs:
            _logger.info(
                "manager.config_reloaded",
                field="max_concurrent_jobs",
                old_value=old.max_concurrent_jobs,
                new_value=new_config.max_concurrent_jobs,
            )
            self._concurrency_semaphore = asyncio.Semaphore(
                new_config.max_concurrent_jobs,
            )

        # Log other changed reloadable fields
        _reloadable_fields = [
            "job_timeout_seconds",
            "shutdown_timeout_seconds",
            "max_job_history",
            "monitor_interval_seconds",
        ]
        for field_name in _reloadable_fields:
            old_val = getattr(old, field_name)
            new_val = getattr(new_config, field_name)
            if old_val != new_val:
                _logger.info(
                    "manager.config_reloaded",
                    field=field_name,
                    old_value=old_val,
                    new_value=new_val,
                )

        self._config = new_config

    def update_job_config_metadata(
        self,
        job_id: str,
        *,
        config_path: Path | None = None,
        workspace: Path | None = None,
    ) -> None:
        """Update config-derived metadata in the in-memory job map."""
        meta = self._job_meta.get(job_id)
        if meta is None:
            return
        if config_path is not None:
            meta.config_path = config_path
        if workspace is not None:
            meta.workspace = workspace

    # ─── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _classify_orphan(orphan: JobRecord) -> DaemonJobStatus:
        """Determine the correct recovery status for an orphaned job.

        Checks the job's checkpoint file to see if it was paused at the time
        the daemon died. Jobs that were paused should stay paused (resumable)
        rather than being marked as failed.
        """
        import json

        try:
            workspace = Path(orphan.workspace)
            # Check for checkpoint file in workspace
            safe_id = "".join(
                c if c.isalnum() or c in "-_" else "_" for c in orphan.job_id
            )
            state_file = workspace / f"{safe_id}.json"
            if state_file.exists():
                data = json.loads(state_file.read_text(encoding="utf-8"))
                if data.get("status") == "paused":
                    return DaemonJobStatus.PAUSED
        except (OSError, json.JSONDecodeError, ValueError):
            _logger.warning(
                "manager.orphan_classify_failed",
                job_id=orphan.job_id,
                workspace=orphan.workspace,
                exc_info=True,
            )
        return DaemonJobStatus.FAILED

    def _get_job_id(self, base_name: str) -> str:
        """Return the job ID for a config name.

        Job name IS the job ID — no deduplication suffixes.  If a job
        with this name is already active, ``submit_job()`` rejects the
        submission rather than inventing a new ID.
        """
        return base_name

    # ─── RPC Handlers ─────────────────────────────────────────────────

    async def submit_job(self, request: JobRequest) -> JobResponse:
        """Validate config, create task, return immediately."""
        if self._shutting_down:
            return JobResponse(
                job_id="",
                status="rejected",
                message="Daemon is shutting down",
            )

        if not self._backpressure.should_accept_job():
            return JobResponse(
                job_id="",
                status="rejected",
                message="System under high pressure — try again later",
            )

        job_id = self._get_job_id(request.config_path.stem)

        # Validate config exists and resolve workspace BEFORE acquiring the
        # lock. Config parsing is expensive and doesn't need serialization
        # — it's idempotent and job-independent.
        if not request.config_path.exists():
            return JobResponse(
                job_id=job_id,
                status="rejected",
                message=f"Config file not found: {request.config_path}",
            )

        # Parse config for workspace resolution and hook extraction.
        # When workspace is provided explicitly, parsing is best-effort
        # (hooks won't be available if it fails, but the job still runs).
        from mozart.core.config import JobConfig

        parsed_config: JobConfig | None = None
        if request.workspace:
            workspace = request.workspace
            try:
                parsed_config = JobConfig.from_yaml(request.config_path)
            except (ValueError, OSError, KeyError, yaml.YAMLError):
                _logger.debug(
                    "manager.config_parse_for_hooks_failed",
                    job_id=job_id,
                    config_path=str(request.config_path),
                )
        else:
            try:
                parsed_config = JobConfig.from_yaml(request.config_path)
                workspace = parsed_config.workspace
            except (ValueError, OSError, KeyError, yaml.YAMLError) as exc:
                _logger.error(
                    "manager.config_parse_failed",
                    job_id=job_id,
                    config_path=str(request.config_path),
                    exc_info=True,
                )
                return JobResponse(
                    job_id=job_id,
                    status="rejected",
                    message=(
                        f"Failed to parse config file: "
                        f"{request.config_path} ({exc}). "
                        "Cannot determine workspace. "
                        "Fix the config or pass --workspace explicitly."
                    ),
                )

        # Extract hook config from parsed config for daemon-owned execution.
        hook_config_list: list[dict[str, Any]] | None = None
        concert_config_dict: dict[str, Any] | None = None
        if parsed_config and parsed_config.on_success:
            hook_config_list = [
                h.model_dump(mode="json") for h in parsed_config.on_success
            ]
        if parsed_config and parsed_config.concert.enabled:
            concert_config_dict = parsed_config.concert.model_dump(mode="json")

        # Early workspace validation: reject jobs whose workspace parent
        # doesn't exist or isn't writable, instead of failing deep in
        # JobService.start_job(). Workspace itself may not exist yet —
        # it gets created by JobService — but the parent must be valid.
        ws_parent = workspace.parent
        if not ws_parent.exists():
            return JobResponse(
                job_id=job_id,
                status="rejected",
                message=(
                    f"Workspace parent directory does not exist: {ws_parent}. "
                    "Create the parent directory or change the workspace path."
                ),
            )
        if not os.access(ws_parent, os.W_OK):
            return JobResponse(
                job_id=job_id,
                status="rejected",
                message=(
                    f"Workspace parent directory is not writable: {ws_parent}. "
                    "Fix permissions or change the workspace path."
                ),
            )

        # Serialize only the duplicate-check → register → insert window
        # to prevent TOCTOU races between concurrent submissions.
        async with self._id_gen_lock:
            # Reject if a job with this name is already active
            existing = self._job_meta.get(job_id)
            if existing and existing.status in (DaemonJobStatus.QUEUED, DaemonJobStatus.RUNNING):
                return JobResponse(
                    job_id=job_id,
                    status="rejected",
                    message=(
                        f"Job '{job_id}' is already {existing.status.value}. "
                        "Use 'mozart pause' or 'mozart cancel' first, or wait for it to finish."
                    ),
                )

            meta = JobMeta(
                job_id=job_id,
                config_path=request.config_path,
                workspace=workspace,
                chain_depth=request.chain_depth,
                hook_config=hook_config_list,
                concert_config=concert_config_dict,
            )
            # Register in DB first — if this fails, no phantom in-memory entry
            await self._registry.register_job(job_id, request.config_path, workspace)
            self._job_meta[job_id] = meta

            # Persist hook config to registry for restart resilience
            if hook_config_list:
                import json
                await self._registry.store_hook_config(
                    job_id, json.dumps(hook_config_list),
                )

        try:
            task = asyncio.create_task(
                self._run_job_task(job_id, request),
                name=f"job-{job_id}",
            )
        except RuntimeError:
            # Clean up metadata if task creation fails
            # RuntimeError is raised by asyncio when no running event loop
            self._job_meta.pop(job_id, None)
            await self._registry.update_status(
                job_id, DaemonJobStatus.FAILED, error_message="Task creation failed",
            )
            raise
        self._jobs[job_id] = task
        task.add_done_callback(lambda t: self._on_task_done(job_id, t))

        _logger.info(
            "job.submitted",
            job_id=job_id,
            config_path=str(request.config_path),
        )

        return JobResponse(
            job_id=job_id,
            status="accepted",
            message=f"Job queued (concurrency limit: {self._config.max_concurrent_jobs})",
        )

    async def get_job_status(self, job_id: str, workspace: Path | None = None) -> dict[str, Any]:
        """Get full status of a specific job.

        Resolution order (no workspace/disk fallback):
        1. Live in-memory state (running jobs)
        2. Registry checkpoint (historical jobs — persisted on every save)
        3. Basic metadata (jobs that never ran / pre-checkpoint registry)
        """
        _ = workspace  # Unused — daemon is the single source of truth

        meta = self._job_meta.get(job_id)
        record: JobRecord | None = None
        if meta is None:
            # Check the persistent registry for historical jobs
            record = await self._registry.get_job(job_id)
            if record is None:
                raise JobSubmissionError(f"Job '{job_id}' not found")

        # 1. Live in-memory state (running jobs)
        live = self._live_states.get(job_id)
        if live is not None:
            return live.model_dump(mode="json")

        # 2. Registry checkpoint (historical/terminal jobs)
        #    Skip if meta shows an active status — the checkpoint is stale
        #    between resume acceptance and the first new state save.
        _active = (DaemonJobStatus.QUEUED, DaemonJobStatus.RUNNING)
        if meta is None or meta.status not in _active:
            try:
                checkpoint_json = await self._registry.load_checkpoint(job_id)
                if checkpoint_json is not None:
                    import json
                    data: dict[str, Any] = json.loads(checkpoint_json)
                    return data
            except Exception:
                _logger.debug(
                    "get_job_status.registry_checkpoint_failed",
                    job_id=job_id,
                    exc_info=True,
                )

        # 2b. Detect stale RUNNING status (no live state + no running task).
        #     This happens when meta was restored from the registry after a
        #     daemon restart but the job's process no longer exists.
        if meta is not None and meta.status == DaemonJobStatus.RUNNING:
            task = self._jobs.get(job_id)
            if task is None or task.done():
                _logger.info(
                    "get_job_status.stale_running_corrected",
                    job_id=job_id,
                )
                meta.status = DaemonJobStatus.FAILED
                await self._registry.update_status(
                    job_id, DaemonJobStatus.FAILED.value,
                )
                # Now fall through to return the corrected checkpoint
                # or metadata below.
                try:
                    checkpoint_json = await self._registry.load_checkpoint(
                        job_id,
                    )
                    if checkpoint_json is not None:
                        import json as _json
                        data = _json.loads(checkpoint_json)
                        # Override the checkpoint's stale status
                        data["status"] = "failed"
                        return data
                except Exception:
                    pass

        # 3. Basic metadata (job never produced a checkpoint, or active job
        #    whose registry checkpoint is stale)
        if meta is not None:
            return meta.to_dict()
        assert record is not None  # guaranteed by the check above
        return record.to_dict()

    async def pause_job(self, job_id: str) -> bool:
        """Send pause signal to a running job via in-process event.

        Prefers the in-process ``_pause_events`` dict (set during
        ``_run_managed_task``).  Falls back to ``JobService.pause_job``
        when no event exists (shouldn't happen in daemon mode, but
        guards against edge cases).
        """
        meta = self._job_meta.get(job_id)
        if meta is None:
            raise JobSubmissionError(f"Job '{job_id}' not found")
        if meta.status != DaemonJobStatus.RUNNING:
            raise JobSubmissionError(
                f"Job '{job_id}' is {meta.status.value}, not running"
            )

        # Verify there's an actual running task (guards against stale
        # "running" status restored from registry after daemon restart)
        task = self._jobs.get(job_id)
        if task is None or task.done():
            meta.status = DaemonJobStatus.FAILED
            await self._registry.update_status(job_id, DaemonJobStatus.FAILED.value)
            raise JobSubmissionError(
                f"Job '{job_id}' has no running process "
                f"(stale status after daemon restart)"
            )

        # Prefer in-process event (no filesystem access needed)
        event = self._pause_events.get(job_id)
        if event is not None:
            event.set()
            _logger.info("job.pause_event_set", job_id=job_id)
            return True

        # Fallback: filesystem-based pause via JobService
        return await self._checked_service.pause_job(meta.job_id, meta.workspace)

    async def resume_job(
        self,
        job_id: str,
        workspace: Path | None = None,
        config_path: Path | None = None,
    ) -> JobResponse:
        """Resume a paused or failed job by creating a new task.

        If an old task for this job is still running (e.g., not yet fully
        paused), it is cancelled before the new resume task is created to
        prevent detached/duplicate execution.

        Args:
            job_id: ID of the job to resume.
            workspace: Optional workspace override.
            config_path: Optional new config file path. When provided, updates
                meta.config_path so the resume task loads the new config.
        """
        meta = self._job_meta.get(job_id)
        if meta is None:
            raise JobSubmissionError(f"Job '{job_id}' not found")
        _resumable = (DaemonJobStatus.PAUSED, DaemonJobStatus.FAILED, DaemonJobStatus.CANCELLED)
        if meta.status not in _resumable:
            raise JobSubmissionError(
                f"Job '{job_id}' is {meta.status.value}, "
                "only PAUSED, FAILED, or CANCELLED jobs can be resumed"
            )

        # Cancel stale task to prevent detached execution
        old_task = self._jobs.pop(job_id, None)
        if old_task is not None and not old_task.done():
            old_task.cancel(msg=f"stale task replaced by resume of {job_id}")
            _logger.info("job.resume_cancelled_stale_task", job_id=job_id)

        # Apply new config path before creating the task (task reads meta.config_path)
        if config_path is not None:
            meta.config_path = config_path

        ws = workspace or meta.workspace
        meta.status = DaemonJobStatus.QUEUED

        task = asyncio.create_task(
            self._resume_job_task(job_id, ws),
            name=f"job-resume-{job_id}",
        )
        self._jobs[job_id] = task
        task.add_done_callback(lambda t: self._on_task_done(job_id, t))

        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="Job resume queued",
        )

    async def modify_job(
        self, job_id: str, config_path: Path, workspace: Path | None = None,
    ) -> JobResponse:
        """Pause a running job and queue automatic resume with new config.

        If the job is already paused/failed/cancelled, resume immediately.
        If running, send pause signal and store pending_modify — _on_task_done
        will trigger the resume when the task completes (pauses).
        """
        meta = self._job_meta.get(job_id)
        if meta is None:
            raise JobSubmissionError(f"Job '{job_id}' not found")

        ws = workspace or meta.workspace

        # Already resumable — resume immediately with new config
        _resumable = (
            DaemonJobStatus.PAUSED,
            DaemonJobStatus.FAILED,
            DaemonJobStatus.CANCELLED,
        )
        if meta.status in _resumable:
            meta.config_path = config_path
            return await self.resume_job(job_id, ws)

        if meta.status != DaemonJobStatus.RUNNING:
            raise JobSubmissionError(
                f"Job '{job_id}' is {meta.status.value}, cannot modify"
            )

        # Send pause signal via in-process event
        await self.pause_job(job_id)

        # Store pending action — _on_task_done will resume when the job pauses
        meta.pending_modify = (config_path, ws)

        return JobResponse(
            job_id=job_id,
            status="accepted",
            message=f"Pause signal sent. Will resume with {config_path.name} when paused.",
        )

    async def _deferred_resume(self, job_id: str, workspace: Path) -> None:
        """Resume a job after a brief delay (used by modify).

        The delay lets task cleanup finish before re-submitting.
        """
        await asyncio.sleep(0.5)
        try:
            await self.resume_job(job_id, workspace)
        except Exception:
            _logger.error("modify.deferred_resume_failed", job_id=job_id, exc_info=True)

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job task.

        Sends the cancel signal and updates in-memory status immediately,
        then defers heavyweight I/O (registry write, scheduler cleanup)
        to a background task so the IPC response is never delayed.
        """
        task = self._jobs.get(job_id)
        if task is None:
            return False

        task.cancel(msg=f"explicit cancel_job({job_id}) via IPC")
        meta = self._job_meta.get(job_id)
        if meta:
            meta.status = DaemonJobStatus.CANCELLED

        # Defer registry + scheduler cleanup so the IPC handler can
        # respond immediately.  In-memory meta is already authoritative.
        cleanup = asyncio.create_task(
            self._cancel_cleanup(job_id),
            name=f"cancel-cleanup-{job_id}",
        )
        cleanup.add_done_callback(
            lambda t: log_task_exception(t, _logger, "cancel_cleanup.failed"),
        )

        _logger.info("job.cancelled", job_id=job_id)
        return True

    async def _cancel_cleanup(self, job_id: str) -> None:
        """Background cleanup after cancel — registry + scheduler updates.

        Errors are logged but never propagate, since the cancel already
        succeeded from the user's perspective.
        """
        try:
            await self._registry.update_status(job_id, "cancelled")
        except Exception:
            _logger.error(
                "cancel_cleanup.registry_failed",
                job_id=job_id,
                exc_info=True,
            )

        try:
            if self._scheduler_instance is not None:
                await self._scheduler_instance.deregister_job(job_id)
        except Exception:
            _logger.error(
                "cancel_cleanup.scheduler_failed",
                job_id=job_id,
                exc_info=True,
            )

    async def list_jobs(self) -> list[dict[str, Any]]:
        """List all jobs from the persistent registry.

        In-memory ``_job_meta`` is authoritative for active jobs (has
        live status). The registry fills in historical jobs.
        """
        seen: set[str] = set()
        result: list[dict[str, Any]] = []

        # Active jobs first (in-memory is most current)
        for meta in self._job_meta.values():
            result.append(meta.to_dict())
            seen.add(meta.job_id)

        # Historical jobs from registry
        for record in await self._registry.list_jobs():
            if record.job_id not in seen:
                result.append(record.to_dict())

        return result

    async def clear_jobs(
        self,
        statuses: list[str] | None = None,
        older_than_seconds: float | None = None,
        job_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Clear terminal jobs from registry and in-memory metadata.

        Args:
            statuses: Status filter (defaults to terminal statuses).
            older_than_seconds: Age filter in seconds.
            job_ids: Only clear these specific job IDs.

        Returns:
            Dict with "deleted" count.
        """
        safe_statuses = set(statuses or ["completed", "failed", "cancelled"])
        safe_statuses -= {"queued", "running"}  # Never clear active jobs

        to_remove: list[str] = []
        now = time.time()
        for jid, meta in self._job_meta.items():
            if job_ids is not None and jid not in job_ids:
                continue
            if meta.status.value not in safe_statuses:
                continue
            if older_than_seconds is not None:
                if (now - meta.submitted_at) < older_than_seconds:
                    continue
            to_remove.append(jid)

        for jid in to_remove:
            self._job_meta.pop(jid, None)
            self._live_states.pop(jid, None)

        deleted = await self._registry.delete_jobs(
            job_ids=job_ids,
            statuses=list(safe_statuses),
            older_than_seconds=older_than_seconds,
        )

        _logger.info(
            "manager.clear_jobs",
            in_memory_removed=len(to_remove),
            registry_deleted=deleted,
        )
        return {"deleted": deleted}

    async def _resolve_job_workspace(
        self, job_id: str, workspace: Path | None = None,
    ) -> Path:
        """Resolve workspace for a job, checking in-memory meta then registry.

        Raises JobSubmissionError if the job is unknown to both.
        """
        meta = self._job_meta.get(job_id)
        if meta is not None:
            return workspace or meta.workspace

        # Fallback: historical job in the persistent registry
        record = await self._registry.get_job(job_id)
        if record is not None:
            return workspace or Path(record.workspace)

        raise JobSubmissionError(f"Job '{job_id}' not found")

    async def get_job_errors(self, job_id: str, workspace: Path | None = None) -> dict[str, Any]:
        """Get errors for a specific job.

        Loads the full CheckpointState and returns it for the CLI to
        extract error information from sheet states.
        """
        ws = await self._resolve_job_workspace(job_id, workspace)
        state = await self._checked_service.get_status(job_id, ws)
        if state is None:
            raise JobSubmissionError(f"No state found for job '{job_id}'")

        return {"state": state.model_dump(mode="json")}

    async def get_diagnostic_report(
        self, job_id: str, workspace: Path | None = None,
    ) -> dict[str, Any]:
        """Get diagnostic data for a specific job.

        Returns the full CheckpointState plus workspace path for the CLI
        to build the diagnostic report locally.
        """
        ws = await self._resolve_job_workspace(job_id, workspace)
        state = await self._checked_service.get_status(job_id, ws)
        if state is None:
            raise JobSubmissionError(f"No state found for job '{job_id}'")

        return {
            "state": state.model_dump(mode="json"),
            "workspace": str(ws),
        }

    async def get_execution_history(
        self, job_id: str, workspace: Path | None = None,
        sheet_num: int | None = None, limit: int = 50,
    ) -> dict[str, Any]:
        """Get execution history for a specific job.

        Requires the SQLite state backend for history records.
        """
        ws = await self._resolve_job_workspace(job_id, workspace)

        from mozart.state import SQLiteStateBackend

        sqlite_path = ws / ".mozart-state.db"
        records: list[dict[str, Any]] = []
        has_history = False

        if sqlite_path.exists():
            backend = SQLiteStateBackend(sqlite_path)
            try:
                if hasattr(backend, 'get_execution_history'):
                    records = await backend.get_execution_history(
                        job_id=job_id, sheet_num=sheet_num, limit=limit,
                    )
                    has_history = True
            finally:
                await backend.close()

        return {
            "job_id": job_id,
            "records": records,
            "has_history": has_history,
        }

    async def recover_job(
        self, job_id: str, workspace: Path | None = None,
        sheet_num: int | None = None, dry_run: bool = False,
    ) -> dict[str, Any]:
        """Get state for recover operation.

        Returns the job state and workspace for the CLI to run
        validations locally. The actual validation logic stays
        in the CLI command to avoid duplicating ValidationEngine
        setup in the daemon.
        """
        ws = await self._resolve_job_workspace(job_id, workspace)
        state = await self._checked_service.get_status(job_id, ws)
        if state is None:
            raise JobSubmissionError(f"No state found for job '{job_id}'")

        return {
            "state": state.model_dump(mode="json"),
            "workspace": str(ws),
            "dry_run": dry_run,
            "sheet_num": sheet_num,
        }

    async def get_daemon_status(self) -> dict[str, Any]:
        """Build daemon status summary.

        Returns all fields required by the ``DaemonStatus`` Pydantic model
        so ``DaemonClient.status()`` can deserialize without crashing.
        """
        mem = self._monitor.current_memory_mb()
        return {
            "pid": os.getpid(),
            "uptime_seconds": round(time.monotonic() - self._start_time, 1),
            "running_jobs": self.running_count,
            "total_jobs_active": self.active_job_count,
            "memory_usage_mb": round(mem, 1) if mem is not None else 0.0,
            "version": getattr(mozart, "__version__", "0.1.0"),
        }

    # ─── Shutdown ─────────────────────────────────────────────────────

    async def shutdown(self, graceful: bool = True) -> None:
        """Cancel all running jobs, optionally waiting for sheets.

        Deregisters all active jobs from the global sheet scheduler
        to clean up any pending sheets, running-sheet tracking, and
        dependency data before the daemon exits.
        """
        self._shutting_down = True

        if graceful:
            timeout = self._config.shutdown_timeout_seconds
            _logger.info(
                "manager.shutting_down",
                graceful=True,
                timeout=timeout,
                running_jobs=self.running_count,
            )

            # Wait for running tasks to complete (up to timeout)
            running = [t for t in self._jobs.values() if not t.done()]
            if running:
                _, pending = await asyncio.wait(
                    running, timeout=timeout,
                )
                for task in pending:
                    task.cancel(msg="graceful shutdown timeout exceeded")
                if pending:
                    results = await asyncio.gather(*pending, return_exceptions=True)
                    for result in results:
                        if isinstance(result, BaseException):
                            _logger.warning(
                                "manager.shutdown_task_exception",
                                error=str(result),
                                error_type=type(result).__name__,
                            )
        else:
            _logger.info("manager.shutting_down", graceful=False)
            for task in self._jobs.values():
                if not task.done():
                    task.cancel(msg="non-graceful shutdown")
            if self._jobs:
                results = await asyncio.gather(
                    *self._jobs.values(), return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        _logger.warning(
                            "manager.shutdown_task_exception",
                            error=str(result),
                            error_type=type(result).__name__,
                        )

        # Deregister all known jobs from the scheduler to clean up
        # heap entries, running-sheet tracking, and dependency data.
        # Uses _job_meta (not _jobs) because task done-callbacks may
        # have already cleared entries from _jobs during cancellation.
        # Guard: only touch the scheduler if it was ever initialized.
        if self._scheduler_instance is not None:
            for job_id in list(self._job_meta.keys()):
                await self._scheduler_instance.deregister_job(job_id)

        self._jobs.clear()

        # Stop all observers for any remaining jobs
        for jid in list(self._job_meta.keys()):
            await self._stop_observer(jid)

        # Stop observer recorder before event bus (needs bus for unsubscribe).
        if self._observer_recorder is not None:
            try:
                await self._observer_recorder.stop(self._event_bus)
            except (OSError, RuntimeError):
                _logger.warning(
                    "manager.observer_recorder_stop_failed",
                    exc_info=True,
                )

        # Stop semantic analyzer before event bus (needs bus for unsubscribe,
        # and learning hub for final writes during drain).
        if self._semantic_analyzer is not None:
            try:
                await self._semantic_analyzer.stop(self._event_bus)
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError):
                _logger.warning(
                    "manager.semantic_analyzer_stop_failed",
                    exc_info=True,
                )

        # Shutdown event bus
        await self._event_bus.shutdown()

        # Stop centralized learning hub (final persist + cleanup)
        await self._learning_hub.stop()

        await self._registry.close()
        self._shutdown_event.set()
        _logger.info("manager.shutdown_complete")

    async def wait_for_shutdown(self) -> None:
        """Block until shutdown is complete."""
        await self._shutdown_event.wait()

    # ─── Properties ───────────────────────────────────────────────────

    @property
    def uptime_seconds(self) -> float:
        """Seconds since the daemon started (monotonic clock)."""
        return time.monotonic() - self._start_time

    @property
    def shutting_down(self) -> bool:
        """Whether the manager is in the process of shutting down."""
        return self._shutting_down

    @property
    def running_count(self) -> int:
        """Number of currently running jobs."""
        return sum(
            1 for m in self._job_meta.values() if m.status == DaemonJobStatus.RUNNING
        )

    @property
    def active_job_count(self) -> int:
        """Number of concurrently executing jobs (used for fair-share scheduling).

        Currently returns ``running_count`` (job-level granularity).
        Phase 3 will replace this with ``self._scheduler.active_count``
        for sheet-level granularity once per-sheet dispatch is wired.
        """
        # TODO(Phase 3): return self._scheduler.active_count when wired
        return self.running_count

    _FAILURE_RATE_WINDOW = 60.0  # seconds
    _FAILURE_RATE_THRESHOLD = 3

    @property
    def failure_rate_elevated(self) -> bool:
        """Whether the recent job failure rate is elevated.

        Returns True if more than ``_FAILURE_RATE_THRESHOLD`` unexpected
        exceptions have occurred within the last ``_FAILURE_RATE_WINDOW``
        seconds.  Used by ``HealthChecker.readiness()`` to degrade the
        health signal when systemic failures are occurring.
        """
        now = time.monotonic()
        cutoff = now - self._FAILURE_RATE_WINDOW
        # Prune expired entries from the left
        while self._recent_failures and self._recent_failures[0] < cutoff:
            self._recent_failures.popleft()
        return len(self._recent_failures) > self._FAILURE_RATE_THRESHOLD

    @property
    def notifications_degraded(self) -> bool:
        """Whether notification delivery is degraded (forwarded from JobService)."""
        if self._service is None:
            return False
        return self._service.notifications_degraded

    @property
    def scheduler(self) -> GlobalSheetScheduler:
        """Access the global sheet scheduler for cross-job coordination."""
        return self._scheduler

    @property
    def rate_coordinator(self) -> RateLimitCoordinator:
        """Access the rate limit coordinator for cross-job rate limiting."""
        return self._rate_coordinator

    @property
    def backpressure(self) -> BackpressureController:
        """Access the backpressure controller for load management."""
        return self._backpressure

    @property
    def learning_hub(self) -> LearningHub:
        """Access the centralized learning hub."""
        return self._learning_hub

    @property
    def observer_recorder(self) -> ObserverRecorder | None:
        """Access the observer event recorder for IPC."""
        return self._observer_recorder

    @property
    def event_bus(self) -> EventBus:
        """Access the event bus for subscribing to events."""
        return self._event_bus

    # ─── Internal ─────────────────────────────────────────────────────

    async def _start_observer(self, job_id: str) -> None:
        """Start a JobObserver co-task for a running job.

        Called when a job transitions to RUNNING state. The observer
        monitors the workspace filesystem and process tree independently
        of the runner's self-reports.
        """
        meta = self._job_meta.get(job_id)
        if meta is None or not self._config.observer.enabled:
            return

        observer = JobObserver(
            job_id=job_id,
            workspace=meta.workspace,
            pid=os.getpid(),
            event_bus=self._event_bus,
            watch_interval=self._config.observer.watch_interval_seconds,
        )
        meta.observer = observer
        await observer.start()

        if self._observer_recorder is not None:
            self._observer_recorder.register_job(job_id, meta.workspace)

    async def _stop_observer(self, job_id: str) -> None:
        """Stop the JobObserver co-task for a job."""
        meta = self._job_meta.get(job_id)
        if meta is None or meta.observer is None:
            return
        await meta.observer.stop()
        meta.observer = None

    def _on_state_published(self, state: CheckpointState) -> None:
        """Receive live CheckpointState from a running job's state backend.

        Called synchronously by ``_PublishingBackend.save()`` on every
        checkpoint.  Stores the latest state in memory AND persists it
        to the registry so ``get_job_status()`` works after daemon restart
        without any disk/workspace fallback.

        Identity: ``state.job_id`` is normally set by JobService to the
        conductor's ``conductor_job_id``.  When it doesn't match any
        ``_job_meta`` key (e.g. legacy code paths), the explicit
        ``_config_name_to_conductor_id`` mapping provides an O(1) fallback.
        """
        conductor_key = state.job_id
        if conductor_key not in self._job_meta:
            conductor_key = self._config_name_to_conductor_id.get(
                state.job_id, state.job_id,
            )
        if conductor_key != state.job_id:
            state = state.model_copy(update={"job_id": conductor_key})
        self._live_states[conductor_key] = state

        # Persist to registry (fire-and-forget — never block the runner)
        try:
            checkpoint_json = state.model_dump_json()
            task = asyncio.create_task(
                self._registry.save_checkpoint(state.job_id, checkpoint_json),
                name=f"checkpoint-save-{state.job_id}",
            )
            task.add_done_callback(
                lambda t: log_task_exception(
                    t, _logger, "registry.checkpoint_save_failed",
                ),
            )
        except Exception:
            _logger.debug(
                "state_published.checkpoint_serialize_failed",
                job_id=state.job_id,
                exc_info=True,
            )

    async def _on_event(
        self,
        job_id: str,
        sheet_num: int,
        event: str,
        data: dict[str, Any] | None,
    ) -> None:
        """Handle runner lifecycle events for registry updates, logging, and bus."""
        _logger.info(
            "runner.event",
            job_id=job_id,
            sheet_num=sheet_num,
            event_type=event,
        )

        # Publish to event bus for downstream consumers
        bus_event: ObserverEvent = {
            "job_id": job_id,
            "sheet_num": sheet_num,
            "event": event,
            "data": data,
            "timestamp": time.time(),
        }
        await self._event_bus.publish(bus_event)

        # Update registry progress on sheet events
        if event.startswith("sheet."):
            meta = self._job_meta.get(job_id)
            if meta:
                total = data.get("total_sheets") if data else None
                await self._registry.update_progress(
                    job_id,
                    current_sheet=sheet_num,
                    total_sheets=total or 0,
                )

    async def _on_rate_limit(
        self,
        backend_type: str,
        wait_seconds: float,
        job_id: str,
        sheet_num: int,
    ) -> None:
        """Forward rate limit events from runners to the coordinator.

        Wiring prerequisite (P025): This callback is passed through
        JobService → RunnerContext → RecoveryMixin._handle_rate_limit()
        so that rate limit detections from any running job feed into the
        daemon's centralized RateLimitCoordinator.  The coordinator
        then informs the scheduler to skip the limited backend.
        """
        await self._rate_coordinator.report_rate_limit(
            backend_type=backend_type,
            wait_seconds=wait_seconds,
            job_id=job_id,
            sheet_num=sheet_num,
        )

    async def _run_managed_task(
        self,
        job_id: str,
        coro: Coroutine[Any, Any, DaemonJobStatus | None],
        *,
        start_event: str = "job.started",
        fail_event: str = "job.failed",
    ) -> None:
        """Shared lifecycle wrapper for job tasks.

        Acquires the concurrency semaphore, tracks status transitions,
        and handles CancelledError / TimeoutError / Exception uniformly.

        Jobs are guarded by ``job_timeout_seconds`` — if a job exceeds
        this wall-clock limit, it is cancelled with FAILED status and a
        descriptive error message.

        Args:
            job_id: The job being executed.
            coro: Awaitable that performs the actual work. May return a
                ``DaemonJobStatus`` to override the default COMPLETED
                status on success (e.g. PAUSED).
            start_event: Structlog event name for the start log.
            fail_event: Structlog event name for the failure log.
        """
        meta = self._job_meta[job_id]
        timeout = self._config.job_timeout_seconds

        async with self._concurrency_semaphore:
            # Create in-process pause event for this job
            pause_event = asyncio.Event()
            self._pause_events[job_id] = pause_event

            meta.status = DaemonJobStatus.RUNNING
            meta.started_at = time.time()
            await self._registry.update_status(
                job_id, "running", pid=os.getpid(),
            )
            _logger.info(start_event, job_id=job_id, timeout_seconds=timeout)

            # Start observer co-task for filesystem/process monitoring
            await self._start_observer(job_id)

            try:
                result_status = await asyncio.wait_for(coro, timeout=timeout)
                if isinstance(result_status, DaemonJobStatus):
                    meta.status = result_status
                else:
                    meta.status = DaemonJobStatus.COMPLETED

                # Flush observer recorder to ensure JSONL is complete before snapshot
                if self._observer_recorder is not None:
                    try:
                        self._observer_recorder.flush(job_id)
                    except Exception:
                        _logger.warning(
                            "observer_recorder.flush_failed",
                            job_id=job_id,
                            exc_info=True,
                        )

                # Capture completion snapshot for terminal statuses
                snapshot_path: str | None = None
                if meta.status in (DaemonJobStatus.COMPLETED, DaemonJobStatus.FAILED):
                    snapshot_path = self._snapshot_manager.capture(
                        job_id, meta.workspace,
                        config_path=meta.config_path,
                    )

                await self._registry.update_status(
                    job_id, meta.status.value,
                    snapshot_path=snapshot_path,
                )
                _logger.info(
                    "job.paused" if meta.status == DaemonJobStatus.PAUSED
                    else "job.completed",
                    job_id=job_id,
                )

            except TimeoutError:
                meta.status = DaemonJobStatus.FAILED
                elapsed = time.monotonic() - (meta.started_at or 0)
                meta.error_message = (
                    f"Job exceeded timeout of {timeout:.0f}s "
                    f"(ran for {elapsed:.0f}s)"
                )
                await self._registry.update_status(
                    job_id, "failed", error_message=meta.error_message,
                )
                self._recent_failures.append(time.monotonic())
                _logger.error(
                    "job.timeout",
                    job_id=job_id,
                    timeout_seconds=timeout,
                    elapsed_seconds=round(elapsed, 1),
                )

            except asyncio.CancelledError as cancel_exc:
                # cancel_job() already set meta.status = CANCELLED and
                # deferred the registry write to _cancel_cleanup().
                # Only update meta if it wasn't set yet (e.g. external cancel).
                if meta.status != DaemonJobStatus.CANCELLED:
                    meta.status = DaemonJobStatus.CANCELLED
                cancel_reason = str(cancel_exc) if str(cancel_exc) else "unknown"
                _logger.error(
                    "job.cancelled_during_execution",
                    job_id=job_id,
                    reason=cancel_reason,
                )
                raise

            except (OSError, ValueError, DaemonError) as exc:
                # Expected operational errors: workspace issues, config errors,
                # permission denied, missing directories, etc.
                meta.status = DaemonJobStatus.FAILED
                meta.error_message = str(exc)
                meta.error_traceback = traceback.format_exc()
                await self._registry.update_status(
                    job_id, "failed", error_message=meta.error_message,
                )
                self._recent_failures.append(time.monotonic())
                _logger.error(fail_event, job_id=job_id, error=str(exc))

            except Exception as exc:
                # Unexpected programming bugs — log with full traceback
                meta.status = DaemonJobStatus.FAILED
                meta.error_message = f"Unexpected internal error: {exc}"
                meta.error_traceback = traceback.format_exc()
                await self._registry.update_status(
                    job_id, "failed", error_message=meta.error_message,
                )
                self._recent_failures.append(time.monotonic())
                _logger.exception(
                    "job.unexpected_error", job_id=job_id,
                )

            finally:
                # Stop observer co-task regardless of outcome
                await self._stop_observer(job_id)
                if self._observer_recorder is not None:
                    self._observer_recorder.unregister_job(job_id)

    @staticmethod
    def _map_job_status(final_status: Any) -> DaemonJobStatus:
        """Map runner JobStatus to DaemonJobStatus."""
        from mozart.core.checkpoint import JobStatus

        if final_status == JobStatus.PAUSED:
            return DaemonJobStatus.PAUSED
        if final_status == JobStatus.FAILED:
            return DaemonJobStatus.FAILED
        return DaemonJobStatus.COMPLETED

    async def _run_job_task(self, job_id: str, request: JobRequest) -> None:
        """Task coroutine that runs a single job.

        Currently runs jobs monolithically via ``JobService.start_job()``.

        TODO(Phase 3 — scheduler integration): Replace monolithic execution
        with per-sheet dispatch through ``self._scheduler``:
          1. Parse sheets from config: ``config.sheets``
          2. Build SheetInfo list + dependency DAG
          3. ``await self._scheduler.register_job(job_id, sheets, deps)``
          4. Dispatch loop: ``entry = await self._scheduler.next_sheet()``
          5. Spawn per-sheet tasks, call ``mark_complete()`` on finish
          6. On error/cancel: ``await self._scheduler.deregister_job(job_id)``
        """

        async def _execute() -> DaemonJobStatus:
            from mozart.core.config import JobConfig

            config = JobConfig.from_yaml(request.config_path)
            if request.workspace:
                config = config.model_copy(
                    update={"workspace": request.workspace},
                )

            # Populate explicit config.name → conductor_id mapping so
            # _on_state_published can resolve the correct owner in O(1)
            # when state.job_id == config.name != conductor job_id.
            if config.name != job_id:
                self._config_name_to_conductor_id[config.name] = job_id

            # Apply daemon-level default thinking method if the job
            # doesn't specify its own (GH#77).
            if (
                self._config.default_thinking_method
                and not config.prompt.thinking_method
            ):
                config = config.model_copy(
                    update={
                        "prompt": config.prompt.model_copy(
                            update={"thinking_method": self._config.default_thinking_method},
                        ),
                    },
                )

            summary = await self._checked_service.start_job(
                config,
                conductor_job_id=job_id,
                fresh=request.fresh,
                start_sheet=request.start_sheet,
                self_healing=request.self_healing,
                self_healing_auto_confirm=request.self_healing_auto_confirm,
                dry_run=request.dry_run,
                pause_event=self._pause_events.get(job_id),
                config_path=str(request.config_path),
            )

            # Track whether this run actually completed new sheets
            # (used by zero-work guard to prevent infinite self-chaining)
            meta = self._job_meta.get(job_id)
            if meta and summary.completed_sheets > 0:
                meta.completed_new_work = True

            return self._map_job_status(summary.final_status)

        await self._run_managed_task(job_id, _execute())

    async def _resume_job_task(self, job_id: str, workspace: Path) -> None:
        """Task coroutine that resumes a paused job."""

        async def _execute() -> DaemonJobStatus:
            meta = self._job_meta.get(job_id)
            summary = await self._checked_service.resume_job(
                job_id, workspace,
                conductor_job_id=job_id,
                config_path=meta.config_path if meta else None,
                pause_event=self._pause_events.get(job_id),
            )

            # Track whether this run actually completed new sheets
            # (used by zero-work guard to prevent infinite self-chaining)
            if meta and summary.completed_sheets > 0:
                meta.completed_new_work = True

            # Update in-memory JobMeta with any config-derived changes.
            # The registry was already updated by JobService.resume_job()
            # during reconciliation; this keeps the in-memory map in sync.
            live = self._live_states.get(job_id)
            if live and live.config_snapshot:
                snap_ws = live.config_snapshot.get("workspace")
                snap_cp = live.config_path
                self.update_job_config_metadata(
                    job_id,
                    workspace=Path(snap_ws) if snap_ws else None,
                    config_path=Path(snap_cp) if snap_cp else None,
                )

            return self._map_job_status(summary.final_status)

        await self._run_managed_task(
            job_id, _execute(),
            start_event="job.resuming",
            fail_event="job.resume_failed",
        )

    async def _execute_hooks_task(self, job_id: str) -> None:
        """Execute post-success hooks for a completed job.

        Spawned as a separate async task from _on_task_done() when:
        - Job status is COMPLETED
        - Job has hook_config (on_success hooks defined)

        For run_job hooks: submits chained jobs via self.submit_job()
        directly (same process, no IPC). For run_command/run_script:
        uses asyncio subprocess APIs.

        If any hook fails: downgrades the parent job from COMPLETED
        to FAILED in both meta and registry.
        """
        import json

        meta = self._job_meta.get(job_id)
        if meta is None or not meta.hook_config:
            return

        hooks = meta.hook_config
        concert = meta.concert_config

        _logger.info(
            "hooks.daemon_executing",
            job_id=job_id,
            hook_count=len(hooks),
        )

        results: list[dict[str, Any]] = []
        any_failed = False

        for i, hook in enumerate(hooks):
            hook_type = hook.get("type", "unknown")
            description = hook.get("description")

            _logger.info(
                "hook.daemon_executing",
                job_id=job_id,
                hook_index=i + 1,
                hook_type=hook_type,
                description=description or "(no description)",
            )

            result: dict[str, Any] = {
                "hook_type": hook_type,
                "description": description,
                "success": False,
            }

            try:
                if hook_type == "run_job":
                    result = await self._execute_hook_run_job(
                        job_id, hook, concert, meta,
                    )
                elif hook_type == "run_command":
                    result = await self._execute_hook_command(
                        hook, meta, use_shell=True,
                    )
                elif hook_type == "run_script":
                    result = await self._execute_hook_command(
                        hook, meta, use_shell=False,
                    )
                else:
                    result["error_message"] = f"Unknown hook type: {hook_type}"

            except Exception as exc:
                result["error_message"] = f"Exception: {exc}"
                _logger.error(
                    "hook.daemon_exception",
                    job_id=job_id,
                    hook_type=hook_type,
                    error=str(exc),
                    exc_info=True,
                )

            results.append(result)

            if result.get("success"):
                _logger.info(
                    "hook.daemon_succeeded",
                    job_id=job_id,
                    hook_type=hook_type,
                )
            else:
                any_failed = True
                _logger.warning(
                    "hook.daemon_failed",
                    job_id=job_id,
                    hook_type=hook_type,
                    error=result.get("error_message"),
                )

                on_failure = hook.get("on_failure", "continue")
                if on_failure == "abort":
                    break

                if concert and concert.get("abort_concert_on_hook_failure"):
                    break

        # Store results in registry
        try:
            await self._registry.store_hook_results(
                job_id, json.dumps(results),
            )
        except Exception:
            _logger.error(
                "hooks.daemon_store_results_failed",
                job_id=job_id,
                exc_info=True,
            )

        # If any hook failed, downgrade job from COMPLETED to FAILED
        if any_failed and meta.status == DaemonJobStatus.COMPLETED:
            meta.status = DaemonJobStatus.FAILED
            meta.error_message = "Post-success hook failed"
            try:
                await self._registry.update_status(
                    job_id, "failed",
                    error_message="Post-success hook failed",
                )
            except Exception:
                _logger.error(
                    "hooks.daemon_status_downgrade_failed",
                    job_id=job_id,
                    exc_info=True,
                )

        _logger.info(
            "hooks.daemon_completed",
            job_id=job_id,
            total=len(results),
            succeeded=sum(1 for r in results if r.get("success")),
            failed=sum(1 for r in results if not r.get("success")),
        )

    async def _execute_hook_run_job(
        self,
        parent_job_id: str,
        hook: dict[str, Any],
        concert: dict[str, Any] | None,
        meta: JobMeta,
    ) -> dict[str, Any]:
        """Execute a run_job hook by submitting a chained job directly."""
        result: dict[str, Any] = {
            "hook_type": "run_job",
            "description": hook.get("description"),
            "success": False,
        }

        job_path_str = hook.get("job_path")
        if not job_path_str:
            result["error_message"] = "job_path is required for run_job hooks"
            return result

        # Expand template variables
        job_path_str = self._expand_hook_vars(
            job_path_str, meta.workspace, parent_job_id,
        )
        job_path = Path(job_path_str)

        if not job_path.exists():
            result["error_message"] = f"Job config not found: {job_path}"
            return result

        # Concert depth check
        current_depth = meta.chain_depth or 0
        if concert and concert.get("enabled"):
            max_depth = concert.get("max_chain_depth", 5)
            if current_depth >= max_depth:
                result["error_message"] = (
                    f"Concert chain depth limit reached ({max_depth})"
                )
                return result

        # Cooldown before submission
        if concert and concert.get("cooldown_between_jobs_seconds", 0) > 0:
            cooldown = concert["cooldown_between_jobs_seconds"]
            _logger.info("hooks.daemon_cooldown", seconds=cooldown)
            await asyncio.sleep(cooldown)

        # Determine workspace for chained job
        chained_workspace: Path | None = None
        raw_ws = hook.get("job_workspace")
        if raw_ws:
            chained_workspace = Path(self._expand_hook_vars(
                str(raw_ws), meta.workspace, parent_job_id,
            ))
        elif concert and concert.get("inherit_workspace", True):
            chained_workspace = meta.workspace

        # Submit chained job directly (no IPC — same process)
        fresh = hook.get("fresh", False)
        request = JobRequest(
            config_path=job_path,
            workspace=chained_workspace,
            fresh=fresh,
            chain_depth=current_depth + 1,
        )

        response = await self.submit_job(request)
        if response.status == "accepted":
            result["success"] = True
            result["output"] = f"Chained job submitted (job_id={response.job_id})"
            result["chained_job_id"] = response.job_id
        else:
            result["error_message"] = (
                f"Chained job rejected: {response.message}"
            )

        return result

    async def _execute_hook_command(
        self,
        hook: dict[str, Any],
        meta: JobMeta,
        *,
        use_shell: bool = True,
    ) -> dict[str, Any]:
        """Execute a run_command or run_script hook.

        run_command uses shell execution (intentional — commands come from
        user-authored YAML config, not runtime user input). run_script uses
        subprocess exec (no shell) for cases where shell features aren't needed.
        """
        import shlex

        hook_type = "run_command" if use_shell else "run_script"
        result: dict[str, Any] = {
            "hook_type": hook_type,
            "description": hook.get("description"),
            "success": False,
        }

        command = hook.get("command")
        if not command:
            result["error_message"] = f"command is required for {hook_type} hooks"
            return result

        command = self._expand_hook_vars(command, meta.workspace, meta.job_id)
        cwd = hook.get("working_directory") or str(meta.workspace)
        timeout = hook.get("timeout_seconds", 300.0)

        try:
            if use_shell:
                proc = await asyncio.create_subprocess_shell(  # noqa: S604
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                )
            else:
                args = shlex.split(command)
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                )

            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                result["exit_code"] = proc.returncode
                result["success"] = (proc.returncode == 0)
                result["output"] = stdout[-2000:] if stdout else None
            except TimeoutError:
                proc.kill()
                await proc.wait()
                result["error_message"] = f"Timeout after {timeout}s"

        except Exception as exc:
            result["error_message"] = str(exc)

        return result

    @staticmethod
    def _expand_hook_vars(
        template: str, workspace: Path, job_id: str,
    ) -> str:
        """Expand template variables in hook paths/commands.

        Delegates to the shared expand_hook_variables() utility in
        execution/hooks.py to avoid reimplementing variable expansion.
        """
        from mozart.execution.hooks import expand_hook_variables

        return expand_hook_variables(
            template, workspace=workspace, job_id=job_id,
        )

    def _on_task_done(self, job_id: str, task: asyncio.Task[Any]) -> None:
        """Callback when a job task completes (success, error, or cancel).

        Each cleanup step is isolated so a failure in one (e.g. registry
        update) cannot prevent the others (snapshot cleanup, history prune)
        from running.  asyncio silently drops exceptions in
        Task.add_done_callback handlers, so every step must be guarded.
        """
        # 1. Remove from active jobs — always runs first
        self._jobs.pop(job_id, None)
        self._pause_events.pop(job_id, None)

        # Clean up config.name → conductor_id mapping entries for this job
        stale_names = [
            name for name, cid in self._config_name_to_conductor_id.items()
            if cid == job_id
        ]
        for name in stale_names:
            del self._config_name_to_conductor_id[name]

        # Retain live state for paused, failed, and completed jobs so
        # status queries show full sheet-level details without disk
        # fallback.  The fire-and-forget registry checkpoint save may not
        # have finished yet, so popping live state too early creates a
        # window where get_job_status() returns stale data.
        meta = self._job_meta.get(job_id)
        if meta is None or meta.status not in (
            DaemonJobStatus.PAUSED, DaemonJobStatus.FAILED,
            DaemonJobStatus.COMPLETED,
        ):
            self._live_states.pop(job_id, None)

        # 2. Check for task exception and update metadata/registry
        try:
            exc = log_task_exception(task, _logger, "job.task_failed")
            if exc:
                meta = self._job_meta.get(job_id)
                if meta and meta.status == DaemonJobStatus.RUNNING:
                    meta.status = DaemonJobStatus.FAILED
                    meta.error_message = str(exc)
                    update_task = asyncio.create_task(
                        self._registry.update_status(
                            job_id, "failed", error_message=str(exc),
                        ),
                        name=f"registry-update-{job_id}",
                    )
                    update_task.add_done_callback(
                        lambda t: log_task_exception(
                            t, _logger, "registry.update_failed",
                        ),
                    )
        except RuntimeError:
            _logger.error(
                "task_done_status_update_failed", job_id=job_id, exc_info=True,
            )

        # 2.5. Check for pending modify (pause→resume with new config)
        try:
            if meta and meta.pending_modify is not None:
                config_path, ws = meta.pending_modify
                meta.pending_modify = None
                if meta.status in (DaemonJobStatus.PAUSED, DaemonJobStatus.FAILED):
                    meta.config_path = config_path
                    asyncio.create_task(
                        self._deferred_resume(job_id, ws or meta.workspace),
                        name=f"modify-resume-{job_id}",
                    )
        except Exception:
            _logger.error(
                "task_done_modify_resume_failed", job_id=job_id, exc_info=True,
            )

        # 2.6. Execute post-success hooks (daemon-owned)
        # Zero-work guard: skip hooks if the job was already completed when
        # loaded (no new sheets executed this run). This prevents infinite
        # self-chaining loops — mirrors lifecycle.py's loaded_as_completed check.
        try:
            if (
                meta
                and meta.status == DaemonJobStatus.COMPLETED
                and meta.hook_config
                and meta.completed_new_work
            ):
                asyncio.create_task(
                    self._execute_hooks_task(job_id),
                    name=f"hooks-{job_id}",
                )
            elif (
                meta
                and meta.status == DaemonJobStatus.COMPLETED
                and meta.hook_config
                and not meta.completed_new_work
            ):
                _logger.info(
                    "hooks.skipped_zero_work",
                    job_id=job_id,
                    reason="Job completed no new sheets — skipping hooks to prevent infinite self-chaining",
                )
        except Exception:
            _logger.error(
                "task_done_hooks_spawn_failed", job_id=job_id, exc_info=True,
            )

        # 3. TTL-based snapshot cleanup (runs synchronously, fast)
        try:
            self._snapshot_manager.cleanup(
                max_age_hours=self._config.observer.snapshot_ttl_hours,
            )
        except OSError:
            _logger.error(
                "task_done_snapshot_cleanup_failed", job_id=job_id, exc_info=True,
            )

        # 4. Prune old completed/failed/cancelled jobs from history
        try:
            self._prune_job_history()
        except RuntimeError:
            _logger.error(
                "task_done_prune_failed", job_id=job_id, exc_info=True,
            )

        # 5. Auto-promote ready patterns (v25 evolution: Pattern Lifecycle)
        # Only run after completed/failed jobs (not paused/cancelled) to ensure
        # patterns have been applied and measured.
        if meta and meta.status in (DaemonJobStatus.COMPLETED, DaemonJobStatus.FAILED):
            try:
                self._promote_ready_patterns()
            except (RuntimeError, OSError):
                _logger.warning(
                    "task_done_pattern_promotion_failed",
                    job_id=job_id,
                    exc_info=True,
                )

        # 6. v25: Trigger entropy check callback if set (Entropy Response Activation)
        # Runs after every job completion to track count for periodic checks
        try:
            if self._entropy_check_callback is not None:
                self._entropy_check_callback()
        except Exception:
            _logger.error(
                "task_done_entropy_check_failed", job_id=job_id, exc_info=True,
            )

    def _promote_ready_patterns(self) -> None:
        """Auto-promote patterns from PENDING to ACTIVE/QUARANTINED based on effectiveness.

        v25 Evolution: Pattern Lifecycle Validation Feedback Loop.
        After each job completion, check if any patterns have enough applications
        to be promoted from PENDING → VALIDATED (high effectiveness) or
        PENDING → QUARANTINED (low effectiveness).
        """
        if not self._learning_hub.is_running:
            return

        store = self._learning_hub.store
        try:
            result = store.promote_ready_patterns()
            if result["promoted"] or result["quarantined"] or result["degraded"]:
                _logger.info(
                    "pattern_lifecycle.promotion_cycle",
                    promoted=len(result["promoted"]),
                    quarantined=len(result["quarantined"]),
                    degraded=len(result["degraded"]),
                )
        except Exception:
            _logger.warning(
                "pattern_lifecycle.promotion_failed",
                exc_info=True,
            )

    def _prune_job_history(self) -> None:
        """Evict oldest terminal jobs when history exceeds max_job_history."""
        max_history = self._config.max_job_history
        terminal = sorted(
            (
                (jid, m) for jid, m in self._job_meta.items()
                if m.status in (
                    DaemonJobStatus.COMPLETED,
                    DaemonJobStatus.FAILED,
                    DaemonJobStatus.CANCELLED,
                )
            ),
            key=lambda x: x[1].submitted_at,
        )
        excess = len(terminal) - max_history
        if excess > 0:
            pruned_ids = [jid for jid, _ in terminal[:excess]]
            for jid in pruned_ids:
                self._job_meta.pop(jid, None)
                self._live_states.pop(jid, None)
            _logger.debug(
                "manager.job_history_pruned",
                pruned_count=excess,
                oldest_pruned=pruned_ids[0],
            )


__all__ = ["DaemonJobStatus", "JobManager", "JobMeta"]
