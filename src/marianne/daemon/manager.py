"""Job manager for the Marianne daemon.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

import marianne
from marianne.core.checkpoint import CheckpointState, SheetState
from marianne.core.logging import get_logger
from marianne.daemon.backpressure import BackpressureController
from marianne.daemon.config import DaemonConfig
from marianne.daemon.event_bus import EventBus
from marianne.daemon.exceptions import DaemonError, JobSubmissionError
from marianne.daemon.job_service import JobService
from marianne.daemon.learning_hub import LearningHub
from marianne.daemon.monitor import ResourceMonitor
from marianne.daemon.observer import JobObserver
from marianne.daemon.observer_recorder import ObserverRecorder
from marianne.daemon.output import StructuredOutput
from marianne.daemon.pgroup import ProcessGroupManager
from marianne.daemon.rate_coordinator import RateLimitCoordinator
from marianne.daemon.registry import DaemonJobStatus, JobRecord, JobRegistry
from marianne.daemon.scheduler import GlobalSheetScheduler
from marianne.daemon.semantic_analyzer import SemanticAnalyzer
from marianne.daemon.snapshot import SnapshotManager
from marianne.daemon.task_utils import log_task_exception
from marianne.daemon.types import JobRequest, JobResponse, ObserverEvent
from marianne.utils.time import utc_now

_logger = get_logger("daemon.manager")

# Filesystem timestamp tolerance for stale detection (#103).
# Avoids false positives from filesystem granularity differences.
_MTIME_TOLERANCE_SECONDS = 1.0


def _should_auto_fresh(config_path: Path, completed_at: float | None) -> bool:
    """Check if a score file was modified after a completed run.

    Used by submit_job to auto-detect changed score files and start fresh
    instead of reusing stale completed state (#103).

    Args:
        config_path: Path to the score YAML file.
        completed_at: Epoch timestamp when the previous run completed.
            None if unknown (never ran or no timestamp stored).

    Returns:
        True if the score file was modified after the job completed
        (indicating the user wants a fresh run), False otherwise.
    """
    if completed_at is None:
        return False
    try:
        mtime = config_path.stat().st_mtime
    except OSError:
        return False
    # Score was modified more than tolerance seconds after completion
    return mtime > completed_at + _MTIME_TOLERANCE_SECONDS


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


class DaemonResourceChecker:
    """Bridges ResourceMonitor to ParallelExecutor's ResourceChecker protocol.

    Used by JobManager to provide backpressure hints to the parallel
    executor during fanout stages.
    """

    def __init__(self, monitor: ResourceMonitor) -> None:
        self._monitor = monitor

    async def can_start_parallel_sheet(self) -> bool:
        """Check if system resource pressure allows another parallel sheet."""
        return self._monitor.is_accepting_work()


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
        pgroup: ProcessGroupManager | None = None,
    ) -> None:
        self._config = config
        self._start_time = start_time or time.monotonic()
        self._pgroup = pgroup

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
        # Jobs queued as PENDING during rate limit backpressure.
        # Keyed by conductor job_id.  Auto-started when limits clear.
        self._pending_jobs: dict[str, JobRequest] = {}
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

        # Step 28: Baton adapter — feature-flagged replacement for monolithic execution.
        # Lazy-initialized in start() when use_baton is True.
        # Import deferred to avoid circular import at module level.
        from marianne.daemon.baton.adapter import BatonAdapter
        self._baton_adapter: BatonAdapter | None = None
        self._baton_loop_task: asyncio.Task[Any] | None = None

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
        # F-077: Also restore hook_config so on_success hooks fire after restart.
        all_records = await self._registry.list_jobs(limit=10_000)
        for record in all_records:
            if record.job_id not in self._job_meta:
                # Restore hook_config from registry (F-077: was missing,
                # causing on_success hooks to silently stop after restart)
                hook_config: list[dict[str, Any]] | None = None
                hook_json = await self._registry.get_hook_config(
                    record.job_id,
                )
                if hook_json:
                    import json
                    hook_config = json.loads(hook_json)

                self._job_meta[record.job_id] = JobMeta(
                    job_id=record.job_id,
                    config_path=Path(record.config_path),
                    workspace=Path(record.workspace),
                    submitted_at=record.submitted_at,
                    started_at=record.started_at,
                    status=record.status,
                    error_message=record.error_message,
                    hook_config=hook_config,
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
            token_warning_threshold=self._config.preflight.token_warning_threshold,
            token_error_threshold=self._config.preflight.token_error_threshold,
            pgroup_manager=self._pgroup,
        )
        # Start semantic analyzer after event bus (needs bus for subscription).
        # Failure must not prevent the conductor from starting.
        try:
            from marianne.execution.setup import create_backend_from_config

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
        # mzt top even when persistence is off.
        if self._config.observer.enabled:
            self._observer_recorder = ObserverRecorder(
                config=self._config.observer,
            )
            await self._observer_recorder.start(self._event_bus)

        # Step 28: Initialize baton adapter when use_baton is enabled.
        if self._config.use_baton:
            from marianne.daemon.baton.adapter import BatonAdapter
            from marianne.daemon.baton.backend_pool import BackendPool
            from marianne.instruments.loader import load_all_profiles
            from marianne.instruments.registry import InstrumentRegistry

            # Build instrument registry with all available profiles
            profiles = load_all_profiles()
            registry = InstrumentRegistry()
            for profile in profiles.values():
                registry.register(profile, override=True)

            self._baton_adapter = BatonAdapter(
                event_bus=self._event_bus,
                max_concurrent_sheets=self._config.max_concurrent_sheets,
                persist_callback=self._on_baton_persist,
            )
            self._baton_adapter.set_backend_pool(
                BackendPool(registry, pgroup=self._pgroup)
            )

            # Populate per-model concurrency from instrument profiles
            for profile in profiles.values():
                for model in profile.models:
                    self._baton_adapter._baton.set_model_concurrency(
                        profile.name, model.name, model.max_concurrent,
                    )

            # Start the baton's event loop as a background task
            self._baton_loop_task = asyncio.create_task(
                self._baton_adapter.run(),
                name="baton-loop",
            )
            _logger.info("manager.baton_adapter_started")

            # Step 29: Recover paused orphans through the baton.
            # Orphans were classified earlier — PAUSED ones are resumable.
            await self._recover_baton_orphans()

        _logger.info(
            "manager.started",
            scheduler_status="lazy_not_wired",
            scheduler_note="Phase 3 scheduler is lazily initialized and not "
            "yet driving execution. Jobs run monolithically via JobService.",
            semantic_analyzer="active" if self._semantic_analyzer else "unavailable",
            observer_recorder="active" if self._observer_recorder else "unavailable",
            baton_adapter="active" if self._baton_adapter else "disabled",
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

        # Propagate preflight thresholds to job service for new runners
        if self._service is not None:
            self._service._token_warning_threshold = new_config.preflight.token_warning_threshold
            self._service._token_error_threshold = new_config.preflight.token_error_threshold

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

        Checks the registry checkpoint (not workspace files) to see if
        the job was paused when the daemon died. Jobs that were paused
        should stay paused (resumable) rather than being marked as failed.

        The conductor's registry is the single source of truth — workspace
        state files are not read during orphan recovery.
        """
        import json

        if orphan.checkpoint_json:
            try:
                data = json.loads(orphan.checkpoint_json)
                if data.get("status") == "paused":
                    return DaemonJobStatus.PAUSED
            except (json.JSONDecodeError, ValueError):
                _logger.warning(
                    "manager.orphan_classify_failed",
                    job_id=orphan.job_id,
                )
        return DaemonJobStatus.FAILED

    def _on_baton_persist(self, job_id: str) -> None:
        """Phase 2 persist callback — save CheckpointState to registry.

        The baton writes directly to SheetState objects inside _live_states.
        This callback serializes the whole CheckpointState and saves it to
        the registry for crash recovery. No field copying, no status
        mapping — just persist.
        """
        live = self._live_states.get(job_id)
        if live is None:
            return
        try:
            checkpoint_json = live.model_dump_json()
            asyncio.get_event_loop().create_task(
                self._registry.save_checkpoint(job_id, checkpoint_json),
                name=f"baton-persist-{job_id}",
            )
        except Exception:
            _logger.warning(
                "baton.persist_failed",
                extra={"job_id": job_id},
                exc_info=True,
            )

    def _on_baton_state_sync(
        self,
        job_id: str,
        sheet_num: int,
        checkpoint_status: str,
        baton_state: Any | None = None,
    ) -> None:
        """Callback invoked by the baton adapter when a sheet status changes.

        Step 29: Syncs baton state changes to the in-memory live state.
        The live state serves ``mzt status`` and persists to the registry.

        Uses CheckpointState.mark_sheet_* methods so that all derived fields
        (last_completed_sheet, updated_at, job completion) stay consistent.

        Args:
            job_id: The job identifier.
            sheet_num: The sheet number that changed.
            checkpoint_status: The new status as a checkpoint status string.
            baton_state: Optional SheetExecutionState from the baton with rich
                metadata (duration, validation, attempts).
        """
        live = self._live_states.get(job_id)
        if live is None:
            _logger.info(
                "baton.state_sync.no_live_state",
                job_id=job_id,
                sheet_num=sheet_num,
                status=checkpoint_status,
                known_ids=list(self._live_states.keys()),
            )
            return

        from marianne.core.checkpoint import SheetStatus

        try:
            status = SheetStatus(checkpoint_status)
        except ValueError:
            _logger.warning(
                "baton.state_sync.invalid_status",
                job_id=job_id,
                sheet_num=sheet_num,
                status=checkpoint_status,
            )
            return

        _logger.info(
            "baton.state_sync.applying",
            job_id=job_id,
            sheet_num=sheet_num,
            status=checkpoint_status,
        )

        _logger.debug(
            "baton.state_sync.applying",
            job_id=job_id,
            sheet_num=sheet_num,
            status=checkpoint_status,
            has_baton_state=baton_state is not None,
        )

        # Ensure sheet entry exists
        if sheet_num not in live.sheets:
            live.sheets[sheet_num] = SheetState(sheet_num=sheet_num)
        sheet_state = live.sheets[sheet_num]

        # Update status for ALL states (the full 11-state enum)
        sheet_state.status = status
        live.updated_at = utc_now()

        # State-specific field updates
        if status in (SheetStatus.DISPATCHED, SheetStatus.IN_PROGRESS):
            live.mark_sheet_started(sheet_num)
        elif status == SheetStatus.COMPLETED:
            validation_passed = True
            duration: float | None = None
            if baton_state is not None:
                duration = baton_state.total_duration_seconds or None
                if baton_state.attempt_results:
                    last = baton_state.attempt_results[-1]
                    validation_passed = last.validation_pass_rate >= 100.0
            live.mark_sheet_completed(
                sheet_num,
                validation_passed=validation_passed,
                execution_duration_seconds=duration,
            )
        elif status == SheetStatus.FAILED:
            error_msg = "Sheet failed"
            error_code: str | None = None
            duration_f: float | None = None
            exit_code: int | None = None
            if baton_state is not None:
                duration_f = baton_state.total_duration_seconds or None
                if baton_state.attempt_results:
                    last = baton_state.attempt_results[-1]
                    error_msg = last.error_message or error_msg
                    error_code = last.error_classification
                    exit_code = last.exit_code
            live.mark_sheet_failed(
                sheet_num,
                error_message=error_msg,
                error_code=error_code,
                exit_code=exit_code,
                execution_duration_seconds=duration_f,
            )
        elif status == SheetStatus.SKIPPED:
            live.mark_sheet_skipped(sheet_num)
        elif status == SheetStatus.CANCELLED:
            sheet_state.completed_at = utc_now()

        # Write scheduling fields from baton state
        if baton_state is not None:
            # Convert monotonic fire_at to UTC datetime for persistence
            if baton_state.next_retry_at is not None:
                delta = baton_state.next_retry_at - time.monotonic()
                sheet_state.fire_at = (
                    datetime.now(UTC) + timedelta(seconds=delta)
                    if delta > 0 else None
                )
            else:
                sheet_state.fire_at = None

            # Rate limit expiry from instrument state
            rate_inst = baton_state.instrument_name
            inst_state = (
                self._baton_adapter._baton.get_instrument_state(rate_inst)
                if self._baton_adapter else None
            )
            if (
                inst_state
                and inst_state.rate_limited
                and inst_state.rate_limit_expires_at is not None
            ):
                delta_rl = inst_state.rate_limit_expires_at - time.monotonic()
                sheet_state.rate_limit_expires_at = (
                    datetime.now(UTC) + timedelta(seconds=delta_rl)
                    if delta_rl > 0 else None
                )
            else:
                sheet_state.rate_limit_expires_at = None

            sheet_state.healing_attempts = baton_state.healing_attempts

        # Persist to registry on significant transitions for crash recovery.
        if status.is_terminal or status in (
            SheetStatus.DISPATCHED, SheetStatus.IN_PROGRESS,
        ):
            try:
                checkpoint_json = live.model_dump_json()
                asyncio.get_event_loop().create_task(
                    self._registry.save_checkpoint(job_id, checkpoint_json),
                    name=f"baton-checkpoint-{job_id}",
                )
            except Exception:
                _logger.warning(
                    "baton.state_sync.persist_failed",
                    job_id=job_id,
                    sheet_num=sheet_num,
                )

    async def _recover_baton_orphans(self) -> None:
        """Recover paused orphan jobs through the baton after restart.

        Step 29: Called during start() after the baton adapter is initialized.
        Scans job metadata for PAUSED jobs (classified during orphan recovery)
        and attempts to resume them through the baton.

        Each recoverable job gets its own asyncio task that loads the checkpoint,
        rebuilds sheets, and registers with the baton for continued execution.
        """
        if self._baton_adapter is None:
            return

        recovered = 0
        for job_id, meta in list(self._job_meta.items()):
            if meta.status != DaemonJobStatus.PAUSED:
                continue

            # Skip if there's already a running task for this job
            if job_id in self._jobs:
                continue

            _logger.info(
                "baton.recovering_orphan",
                job_id=job_id,
                workspace=str(meta.workspace),
            )

            try:
                # Create a resume task that will run via the baton
                task = asyncio.create_task(
                    self._resume_job_task(job_id, meta.workspace),
                    name=f"job-recover-{job_id}",
                )
                self._jobs[job_id] = task

                def _on_done(
                    t: asyncio.Task[Any], *, _jid: str = job_id,
                ) -> None:
                    self._on_task_done(_jid, t)

                task.add_done_callback(_on_done)
                recovered += 1
            except Exception:
                _logger.error(
                    "baton.orphan_recovery_failed",
                    job_id=job_id,
                    exc_info=True,
                )

        if recovered:
            _logger.info(
                "manager.baton_orphans_recovered",
                recovered=recovered,
            )

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
            # F-149: should_accept_job() only rejects for resource pressure
            # (memory/processes). Rate limits are per-instrument and handled
            # at the sheet dispatch level — they don't block job submission.
            return JobResponse(
                job_id="",
                status="rejected",
                message="System under high resource pressure — try again later",
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
        from marianne.core.config import JobConfig

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
                        "Use 'mzt pause' or 'mzt cancel' first, or wait for it to finish."
                    ),
                )

            # Auto-detect changed score file on re-run (#103).
            # When a COMPLETED job exists and --fresh wasn't set, check
            # if the score file was modified after the last run completed.
            if not request.fresh:
                record = await self._registry.get_job(job_id)
                if (
                    record is not None
                    and record.status == DaemonJobStatus.COMPLETED
                    and _should_auto_fresh(request.config_path, record.completed_at)
                ):
                    request = request.model_copy(update={"fresh": True})
                    _logger.info(
                        "auto_fresh.score_changed",
                        job_id=job_id,
                        config_path=str(request.config_path),
                        message="Score file modified since last completed run — starting fresh",
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

    async def _queue_pending_job(self, request: JobRequest) -> JobResponse:
        """Accept a job as PENDING during rate-limit backpressure.

        Registers the job in the persistent registry and in-memory metadata,
        but does NOT create an execution task.  The job starts automatically
        when rate limits clear (via ``_start_pending_jobs``).

        Returns a ``pending`` JobResponse with rate limit timing info.
        """
        job_id = self._get_job_id(request.config_path.stem)

        # Minimal validation: config must exist
        if not request.config_path.exists():
            return JobResponse(
                job_id=job_id,
                status="rejected",
                message=f"Config file not found: {request.config_path}",
            )

        # Gather rate limit info for the response message
        active = self._rate_coordinator.active_limits
        limit_parts: list[str] = []
        for instrument, secs in active.items():
            if secs > 0:
                mins, s = divmod(int(secs), 60)
                time_str = f"{mins}m {s}s" if mins else f"{s}s"
                limit_parts.append(f"{instrument} clears in {time_str}")

        # Register in persistent storage + in-memory metadata so
        # the job is visible via `mzt list` and `mzt status`.
        workspace = request.workspace or self._resolve_workspace_from_config(
            request.config_path, job_id,
        )
        if workspace is not None:
            await self._registry.register_job(job_id, request.config_path, workspace)
            self._job_meta[job_id] = JobMeta(
                job_id=job_id,
                config_path=request.config_path,
                workspace=workspace,
                status=DaemonJobStatus.PENDING,
            )

        # Store for auto-start
        self._pending_jobs[job_id] = request

        limit_msg = "; ".join(limit_parts) if limit_parts else "active rate limits"
        _logger.info(
            "job.queued_pending",
            job_id=job_id,
            reason="rate_limit",
            config_path=str(request.config_path),
        )

        # Schedule a deferred auto-start check when the longest rate limit
        # is expected to clear.  Adds a small buffer (2s) for timing slop.
        max_wait = max(active.values(), default=0.0)
        if max_wait > 0:
            delay = max_wait + 2.0

            async def _deferred_start() -> None:
                await asyncio.sleep(delay)
                await self._start_pending_jobs()

            task = asyncio.create_task(
                _deferred_start(),
                name=f"pending-autostart-{job_id}",
            )
            # Fire-and-forget — just prevent "task was destroyed" warnings
            task.add_done_callback(lambda t: None)

        return JobResponse(
            job_id=job_id,
            status="pending",
            message=f"Rate limit active ({limit_msg}). Score queued — starts when limits clear.",
        )

    def _resolve_workspace_from_config(
        self, config_path: Path, job_id: str,
    ) -> Path | None:
        """Best-effort workspace resolution from a config file."""
        try:
            from marianne.core.config import JobConfig
            parsed = JobConfig.from_yaml(config_path)
            return parsed.workspace
        except Exception:
            _logger.debug(
                "pending.workspace_resolve_failed",
                job_id=job_id,
                config_path=str(config_path),
            )
            return None

    async def _start_pending_jobs(self) -> None:
        """Start any pending jobs if backpressure has cleared.

        Called when rate limits expire or are cleared manually.
        Jobs are started in insertion order (FIFO).
        """
        if not self._pending_jobs:
            return

        if not self._backpressure.should_accept_job():
            return

        # Copy keys to avoid mutation during iteration
        for job_id in list(self._pending_jobs.keys()):
            if not self._backpressure.should_accept_job():
                break  # Pressure returned — stop starting jobs

            request = self._pending_jobs.pop(job_id)
            # Transition status from PENDING → QUEUED in metadata
            meta = self._job_meta.get(job_id)
            if meta is not None:
                meta.status = DaemonJobStatus.QUEUED
            _logger.info(
                "job.pending_started",
                job_id=job_id,
                config_path=str(request.config_path),
            )
            try:
                task = asyncio.create_task(
                    self._run_job_task(job_id, request),
                    name=f"job-{job_id}",
                )
                self._jobs[job_id] = task
                def _on_pending_done(
                    t: asyncio.Task[Any], *, _jid: str = job_id,
                ) -> None:
                    self._on_task_done(_jid, t)

                task.add_done_callback(_on_pending_done)
            except RuntimeError:
                _logger.error(
                    "pending.task_creation_failed",
                    job_id=job_id,
                    exc_info=True,
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
                    # Override checkpoint status with the registry's
                    # authoritative status. The checkpoint may have been
                    # persisted before a cancel/fail was recorded in the
                    # registry's status column.
                    authoritative_status = (
                        meta.status.value if meta is not None
                        else (record.status.value if record is not None else None)
                    )
                    if authoritative_status and data.get("status") != authoritative_status:
                        data["status"] = authoritative_status
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

        # Baton path: inject PauseJob event directly into the baton.
        # The baton sets job.paused=True immediately, which stops dispatch
        # for this job regardless of whether sheets are active.
        if self._baton_adapter is not None:
            from marianne.daemon.baton.events import PauseJob
            await self._baton_adapter._baton.inbox.put(
                PauseJob(job_id=job_id)
            )
            _logger.info("job.baton_pause_sent", job_id=job_id)
            # Also update registry so CLI shows PAUSED
            meta.status = DaemonJobStatus.PAUSED
            await self._registry.update_status(job_id, DaemonJobStatus.PAUSED.value)
            return True

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
        no_reload: bool = False,
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
            no_reload: If True, skip auto-reload from disk and use cached
                config snapshot. Threaded from CLI ``--no-reload`` flag.
        """
        meta = self._job_meta.get(job_id)
        if meta is None:
            raise JobSubmissionError(f"Score '{job_id}' not found")
        _resumable = (DaemonJobStatus.PAUSED, DaemonJobStatus.FAILED, DaemonJobStatus.CANCELLED)
        if meta.status not in _resumable:
            raise JobSubmissionError(
                f"Score '{job_id}' is {meta.status.value}, "
                "only PAUSED, FAILED, or CANCELLED scores can be resumed"
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
            self._resume_job_task(job_id, ws, no_reload=no_reload),
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

        # Send pause signal
        await self.pause_job(job_id)

        # Store pending action — _on_task_done will resume when the job pauses
        meta.pending_modify = (config_path, ws)

        # Baton path: the old task sits on wait_for_completion() and won't
        # exit just because the baton paused dispatch. Cancel the task so
        # _on_task_done fires and triggers the deferred resume.
        if self._baton_adapter is not None:
            old_task = self._jobs.get(job_id)
            if old_task is not None and not old_task.done():
                old_task.cancel(msg=f"modify: paused for config reload on {job_id}")
                _logger.info("job.modify_cancelled_baton_task", job_id=job_id)

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
        """Cancel a running or pending job.

        For running jobs: sends the cancel signal and updates in-memory
        status immediately, then defers heavyweight I/O to a background task.

        For pending jobs (queued during rate limit backpressure): removes
        from the pending queue and updates the registry.
        """
        # Check pending jobs first (not yet started)
        if job_id in self._pending_jobs:
            del self._pending_jobs[job_id]
            meta = self._job_meta.get(job_id)
            if meta:
                meta.status = DaemonJobStatus.CANCELLED
            # Update registry
            cleanup = asyncio.create_task(
                self._cancel_cleanup(job_id),
                name=f"cancel-cleanup-{job_id}",
            )
            cleanup.add_done_callback(
                lambda t: log_task_exception(t, _logger, "cancel_cleanup.failed"),
            )
            _logger.info("job.pending_cancelled", job_id=job_id)
            return True

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
        """List all jobs with live progress data.

        In-memory ``_job_meta`` is authoritative for active jobs.
        Live state from ``_live_states`` enriches active entries with
        sheet-level progress so ``mzt list`` shows completion counts.
        The registry fills in historical jobs.
        """
        seen: set[str] = set()
        result: list[dict[str, Any]] = []

        # Active jobs first — enrich with live progress
        for meta in self._job_meta.values():
            entry = meta.to_dict()
            live = self._live_states.get(meta.job_id)
            if live is not None:
                completed = sum(
                    1 for s in live.sheets.values()
                    if s.status.value == "completed"
                )
                entry["progress_completed"] = completed
                entry["progress_total"] = len(live.sheets)
            result.append(entry)
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
        safe_statuses -= {"queued", "running", "pending"}  # Never clear active jobs

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

    async def clear_rate_limits(
        self,
        instrument: str | None = None,
    ) -> dict[str, Any]:
        """Clear active rate limits from the coordinator and baton.

        Removes the active rate limit so new sheets can be dispatched
        immediately.  Clears both the ``RateLimitCoordinator`` (used by
        the legacy runner and scheduler) and the baton's per-instrument
        ``InstrumentState`` (used by the baton dispatch loop).

        Args:
            instrument: Instrument name to clear, or ``None`` for all.

        Returns:
            Dict with ``cleared`` count and ``instrument`` filter.
        """
        cleared = await self.rate_coordinator.clear_limits(
            instrument=instrument,
        )
        baton_cleared = 0
        if self._baton_adapter is not None:
            baton_cleared = self._baton_adapter.clear_instrument_rate_limit(
                instrument,
            )
            # Kick the baton event loop so dispatch_ready() runs for the
            # newly-PENDING sheets. Without this, the loop blocks on
            # inbox.get() and cleared sheets sit idle until an unrelated
            # event arrives.
            from marianne.daemon.baton.events import DispatchRetry

            self._baton_adapter._baton.inbox.put_nowait(DispatchRetry())
        _logger.info(
            "manager.clear_rate_limits",
            instrument=instrument,
            coordinator_cleared=cleared,
            baton_cleared=baton_cleared,
        )
        # Start any pending jobs now that limits are cleared
        await self._start_pending_jobs()
        return {
            "cleared": cleared + baton_cleared,
            "instrument": instrument,
        }

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

        Returns the CheckpointState for the CLI to extract error information.
        Uses the same resolution as get_job_status: live state first, then
        registry, never workspace files.
        """
        _ = workspace  # Conductor DB is the sole source of truth
        state_dict = await self.get_job_status(job_id)
        return {"state": state_dict}

    async def get_diagnostic_report(
        self, job_id: str, workspace: Path | None = None,
    ) -> dict[str, Any]:
        """Get diagnostic data for a specific job.

        Returns the CheckpointState plus workspace path. Uses the same
        resolution as get_job_status: live state first, then registry.
        """
        ws = await self._resolve_job_workspace(job_id, workspace)
        state_dict = await self.get_job_status(job_id)
        return {
            "state": state_dict,
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

        from marianne.state import SQLiteStateBackend

        sqlite_path = ws / ".marianne-state.db"
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
            "version": getattr(marianne, "__version__", "0.1.0"),
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

        # Stop the baton adapter: send ShutdownRequested, wait for the
        # event loop to exit, cancel if it doesn't, close backend pool.
        if self._baton_adapter is not None:
            try:
                from marianne.daemon.baton.events import ShutdownRequested
                self._baton_adapter._baton.inbox.put_nowait(
                    ShutdownRequested(graceful=graceful)
                )
                # Wait for the baton loop to exit (bounded by 5s)
                if self._baton_loop_task is not None and not self._baton_loop_task.done():
                    try:
                        await asyncio.wait_for(self._baton_loop_task, timeout=5.0)
                    except (TimeoutError, asyncio.CancelledError):
                        self._baton_loop_task.cancel()
                        try:
                            await self._baton_loop_task
                        except (asyncio.CancelledError, Exception):
                            pass
                # Cancel any remaining musician tasks
                await self._baton_adapter.shutdown()
                _logger.info("manager.baton_adapter_stopped")
            except Exception:
                _logger.warning(
                    "manager.baton_adapter_stop_failed", exc_info=True,
                )

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

        # Final checkpoint flush: persist ALL live states to registry before
        # closing. Fire-and-forget saves from _on_baton_state_sync may be
        # pending — this synchronous flush ensures no progress is lost.
        flushed = 0
        for jid, live in self._live_states.items():
            try:
                checkpoint_json = live.model_dump_json()
                await self._registry.save_checkpoint(jid, checkpoint_json)
                await self._registry.update_status(
                    jid, live.status.value if hasattr(live.status, 'value') else str(live.status),
                )
                flushed += 1
            except Exception:
                _logger.warning(
                    "manager.shutdown_flush_failed",
                    job_id=jid, exc_info=True,
                )
        if flushed:
            _logger.info("manager.shutdown_checkpoint_flush", flushed=flushed)

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
        from marianne.core.checkpoint import JobStatus

        if final_status == JobStatus.PAUSED:
            return DaemonJobStatus.PAUSED
        if final_status == JobStatus.FAILED:
            return DaemonJobStatus.FAILED
        return DaemonJobStatus.COMPLETED

    async def _run_job_task(self, job_id: str, request: JobRequest) -> None:
        """Task coroutine that runs a single job.

        Routes through the BatonAdapter when use_baton is enabled,
        otherwise falls back to monolithic JobService.start_job().
        """

        async def _execute() -> DaemonJobStatus:
            from marianne.core.config import JobConfig

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

            # Step 28: Route through baton when enabled
            if self._baton_adapter is not None:
                return await self._run_via_baton(job_id, config, request)

            # Monolithic execution (default — pre-baton path)
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
                resource_checker=DaemonResourceChecker(self._monitor),
            )

            # Track whether this run actually completed new sheets
            # (used by zero-work guard to prevent infinite self-chaining)
            meta = self._job_meta.get(job_id)
            if meta and summary.completed_sheets > 0:
                meta.completed_new_work = True

            return self._map_job_status(summary.final_status)

        await self._run_managed_task(job_id, _execute())

    async def _run_via_baton(
        self,
        job_id: str,
        config: Any,
        request: JobRequest,
    ) -> DaemonJobStatus:
        """Execute a job through the baton adapter.

        Converts the config into Sheet entities, registers them with the
        baton, and waits for the baton to complete execution.

        Args:
            job_id: Conductor job ID.
            config: Parsed and adjusted JobConfig.
            request: Original job request.

        Returns:
            DaemonJobStatus reflecting the job's outcome.
        """
        from marianne.core.sheet import build_sheets
        from marianne.daemon.baton.adapter import extract_dependencies

        assert self._baton_adapter is not None  # Caller checks this
        adapter = self._baton_adapter

        # Build Sheet entities from config
        sheets = build_sheets(config)
        deps = extract_dependencies(config)

        # Extract retry/cost settings from config
        max_retries = config.retry.max_retries
        max_cost: float | None = None
        if config.cost_limits.enabled and config.cost_limits.max_cost_per_job:
            max_cost = config.cost_limits.max_cost_per_job

        # Publish job.started event
        await adapter.publish_job_event(job_id, "job.started", {
            "sheet_count": len(sheets),
            "instrument": config.backend.type,
        })

        # F-255.2: Create initial CheckpointState in _live_states BEFORE
        # registering with the baton. Without this, _on_baton_state_sync
        # returns early (no live state to update) and get_job_status()
        # shows "Full status unavailable" for baton-managed jobs.
        from marianne.core.checkpoint import JobStatus as CPJobStatus

        initial_sheets: dict[int, SheetState] = {}
        for sheet in sheets:
            # Extract model from instrument_config safely (may be mock in tests)
            model = None
            if isinstance(sheet.instrument_config, dict):
                model = sheet.instrument_config.get("model")
            initial_sheets[sheet.num] = SheetState(
                sheet_num=sheet.num,
                instrument_name=sheet.instrument_name,  # F-151
                instrument_model=model if isinstance(model, str) else None,
            )
        initial_state = CheckpointState(
            job_id=job_id,
            job_name=config.name,
            total_sheets=len(sheets),
            status=CPJobStatus.RUNNING,
            sheets=initial_sheets,
            instruments_used=list({s.instrument_name for s in sheets if s.instrument_name}),
            total_movements=max((s.movement for s in sheets), default=None),
        )
        self._live_states[job_id] = initial_state

        # Deregister stale baton state if this job_id was previously
        # registered (e.g., --fresh resubmit, or cancelled job restarted).
        # Without this, register_job returns early on duplicate and the
        # baton uses stale terminal sheets → instant completion with 0 work.
        adapter.deregister_job(job_id)

        # Register job with the baton
        # F-158: Pass prompt_config and parallel_enabled so the adapter
        # creates a PromptRenderer for the full 9-layer prompt assembly.
        # Without this, baton musicians get raw templates instead of
        # rendered prompts with preamble, injections, and validations.
        # Phase 2: pass the SheetState objects from _live_states so the
        # baton writes directly to them. No sync layer needed.
        adapter.register_job(
            job_id,
            sheets,
            deps,
            max_cost_usd=max_cost,
            max_retries=max_retries,
            escalation_enabled=request.self_healing,
            self_healing_enabled=request.self_healing,
            prompt_config=config.prompt,
            parallel_enabled=config.parallel.enabled,
            cross_sheet=config.cross_sheet,  # F-210
            pacing_seconds=float(config.pause_between_sheets_seconds),
            live_sheets=initial_state.sheets,
        )

        try:
            # Wait for the baton to complete all sheets
            all_success = await adapter.wait_for_completion(job_id)

            # F-145: Set completed_new_work flag for concert chaining.
            # The monolithic path sets this when summary.completed_sheets > 0.
            # The baton path mirrors this by checking if any sheet completed.
            meta = self._job_meta.get(job_id)
            if meta and adapter.has_completed_sheets(job_id):
                meta.completed_new_work = True

            # Update job-level status in the live CheckpointState so
            # mzt status (reads _live_states) agrees with mzt list
            # (reads registry). Without this, live.status stays RUNNING
            # after all sheets are terminal.
            live = self._live_states.get(job_id)
            if live is not None:
                from marianne.core.checkpoint import JobStatus as CPJobStatus

                live.status = (
                    CPJobStatus.COMPLETED if all_success
                    else CPJobStatus.FAILED
                )
                live.completed_at = utc_now()

            # Publish completion event
            await adapter.publish_job_event(
                job_id,
                "job.completed" if all_success else "job.failed",
                {"all_success": all_success},
            )

            return (
                DaemonJobStatus.COMPLETED if all_success
                else DaemonJobStatus.FAILED
            )

        except asyncio.CancelledError:
            # Don't deregister if this is a modify-triggered cancel —
            # the resume needs the baton state intact.
            meta = self._job_meta.get(job_id)
            if meta is None or meta.pending_modify is None:
                adapter.deregister_job(job_id)
            raise
        except Exception:
            _logger.error(
                "baton.job_execution_failed",
                job_id=job_id,
                exc_info=True,
            )
            adapter.deregister_job(job_id)
            return DaemonJobStatus.FAILED

    async def _resume_via_baton(
        self,
        job_id: str,
        workspace: Path,
        no_reload: bool = False,
    ) -> DaemonJobStatus:
        """Resume a job through the baton adapter using checkpoint recovery.

        Step 29: Loads the persisted CheckpointState, rebuilds Sheet entities
        from the config, and registers the recovered state with the baton.
        Terminal sheets are preserved; in-progress sheets are reset to PENDING.

        Args:
            job_id: Conductor job ID.
            workspace: Job workspace directory.
            no_reload: When True, use config from checkpoint snapshot
                instead of reloading from disk (fix for #98).

        Returns:
            DaemonJobStatus reflecting the job's outcome.
        """
        from marianne.core.config import JobConfig
        from marianne.core.sheet import build_sheets
        from marianne.daemon.baton.adapter import extract_dependencies

        assert self._baton_adapter is not None

        meta = self._job_meta.get(job_id)
        if meta is None:
            return DaemonJobStatus.FAILED

        # Load checkpoint from workspace
        checkpoint = await self._load_checkpoint(job_id, workspace)
        if checkpoint is None:
            _logger.error(
                "baton.resume.no_checkpoint",
                job_id=job_id,
                workspace=str(workspace),
            )
            return DaemonJobStatus.FAILED

        # Load config — respect no_reload flag (#98)
        config: JobConfig | None = None
        if no_reload and checkpoint.config_snapshot:
            try:
                config = JobConfig.model_validate(checkpoint.config_snapshot)
                if workspace != config.workspace:
                    config = config.model_copy(update={"workspace": workspace})
            except (ValueError, TypeError) as exc:
                _logger.warning(
                    "baton.resume.snapshot_invalid",
                    job_id=job_id,
                    error=str(exc),
                    msg="Falling back to disk reload",
                )
                config = None

        if config is None:
            try:
                config = JobConfig.from_yaml(meta.config_path)
                if workspace != config.workspace:
                    config = config.model_copy(update={"workspace": workspace})
            except (ValueError, OSError) as exc:
                _logger.error(
                    "baton.resume.config_load_failed",
                    job_id=job_id,
                    error=str(exc),
                )
                return DaemonJobStatus.FAILED

        # Build sheets and dependencies
        sheets = build_sheets(config)
        deps = extract_dependencies(config)

        # Extract retry/cost settings
        max_retries = config.retry.max_retries
        max_cost: float | None = None
        if config.cost_limits.enabled and config.cost_limits.max_cost_per_job:
            max_cost = config.cost_limits.max_cost_per_job

        # Publish resume event
        await self._baton_adapter.publish_job_event(
            job_id, "job.resuming",
            {"sheet_count": len(sheets)},
        )

        # F-255.2: Populate _live_states with recovered checkpoint so
        # status display and state_sync_callback work during resumed execution.
        self._live_states[job_id] = checkpoint

        # Recover job with checkpoint state
        # F-158: Pass prompt_config and parallel_enabled (same as _run_via_baton)
        # Phase 2: pass checkpoint.sheets so the baton writes to the same
        # SheetState objects as _live_states. No sync layer needed.
        self._baton_adapter.recover_job(
            job_id,
            sheets,
            deps,
            checkpoint,
            max_cost_usd=max_cost,
            max_retries=max_retries,
            prompt_config=config.prompt,
            parallel_enabled=config.parallel.enabled,
            cross_sheet=config.cross_sheet,  # F-210
            pacing_seconds=float(config.pause_between_sheets_seconds),
            live_sheets=checkpoint.sheets,
        )

        # Reconcile live state with baton's view: recover_job resets
        # in_progress sheets to PENDING (dead musicians), but the live
        # CheckpointState still has them as in_progress with stale times.
        from marianne.core.checkpoint import SheetStatus
        for _sheet_num, sheet_state in checkpoint.sheets.items():
            if sheet_state.status == SheetStatus.IN_PROGRESS:
                sheet_state.status = SheetStatus.PENDING
                sheet_state.started_at = None
        checkpoint.current_sheet = None

        try:
            # Wait for the baton to complete all sheets
            all_success = await self._baton_adapter.wait_for_completion(job_id)

            # F-145: Set completed_new_work flag for concert chaining.
            if meta and self._baton_adapter.has_completed_sheets(job_id):
                meta.completed_new_work = True

            # Update job-level status in the live CheckpointState (same
            # as _run_via_baton — keeps mzt status and mzt list in sync).
            live = self._live_states.get(job_id)
            if live is not None:
                from marianne.core.checkpoint import JobStatus as CPJobStatus

                live.status = (
                    CPJobStatus.COMPLETED if all_success
                    else CPJobStatus.FAILED
                )
                live.completed_at = utc_now()

            await self._baton_adapter.publish_job_event(
                job_id,
                "job.completed" if all_success else "job.failed",
                {"all_success": all_success},
            )

            return (
                DaemonJobStatus.COMPLETED if all_success
                else DaemonJobStatus.FAILED
            )

        except asyncio.CancelledError:
            self._baton_adapter.deregister_job(job_id)
            raise
        except Exception:
            _logger.error(
                "baton.resume_failed",
                job_id=job_id,
                exc_info=True,
            )
            self._baton_adapter.deregister_job(job_id)
            return DaemonJobStatus.FAILED

    async def _load_checkpoint(
        self,
        job_id: str,
        workspace: Path,
    ) -> CheckpointState | None:
        """Load a persisted CheckpointState from the daemon's registry.

        The daemon DB is the single source of truth for job state.
        No workspace file fallback — if the daemon doesn't have it,
        it doesn't exist.

        Args:
            job_id: The job identifier.
            workspace: The workspace directory (unused, kept for API compat).

        Returns:
            The loaded CheckpointState, or None if not found.
        """
        import json

        _ = workspace  # Daemon DB is the sole source of truth

        checkpoint_json = await self._registry.load_checkpoint(job_id)
        if checkpoint_json is None:
            return None

        try:
            data = json.loads(checkpoint_json)
            return CheckpointState.model_validate(data)
        except (json.JSONDecodeError, ValueError) as exc:
            _logger.warning(
                "baton.checkpoint_load_failed",
                job_id=job_id,
                source="daemon_registry",
                error=str(exc),
            )
            return None

    async def _resume_job_task(
        self, job_id: str, workspace: Path, no_reload: bool = False,
    ) -> None:
        """Task coroutine that resumes a paused job."""

        async def _execute() -> DaemonJobStatus:
            # Step 29: Route through baton when enabled
            if self._baton_adapter is not None:
                return await self._resume_via_baton(
                    job_id, workspace, no_reload=no_reload,
                )

            meta = self._job_meta.get(job_id)
            summary = await self._checked_service.resume_job(
                job_id, workspace,
                conductor_job_id=job_id,
                config_path=meta.config_path if meta else None,
                no_reload=no_reload,
                pause_event=self._pause_events.get(job_id),
                resource_checker=DaemonResourceChecker(self._monitor),
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

        command = self._expand_hook_vars(
            command, meta.workspace, meta.job_id, for_shell=use_shell,
        )
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
        template: str,
        workspace: Path,
        job_id: str,
        *,
        for_shell: bool = False,
    ) -> str:
        """Expand template variables in hook paths/commands.

        Delegates to the shared expand_hook_variables() utility in
        execution/hooks.py to avoid reimplementing variable expansion.
        """
        from marianne.execution.hooks import expand_hook_variables

        return expand_hook_variables(
            template, workspace=workspace, job_id=job_id,
            for_shell=for_shell,
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
                    reason=(
                        "Job completed no new sheets"
                        " — skipping hooks to prevent infinite self-chaining"
                    ),
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
