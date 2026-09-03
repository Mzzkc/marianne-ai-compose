"""Job manager for the Marianne daemon.

Maps job IDs to asyncio.Tasks, enforces concurrency limits via semaphore,
routes IPC requests to JobService, and cancels all tasks on shutdown.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import traceback
from collections import deque
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from marianne.core.config import JobConfig
    from marianne.instruments.registry import InstrumentRegistry

import yaml

import marianne
from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.core.config.spec import SpecCorpusConfig
from marianne.core.constants import STATE_DB_FILENAME
from marianne.core.logging import get_logger
from marianne.daemon.backpressure import BackpressureController
from marianne.daemon.checkpoint_writer import CheckpointWriter
from marianne.daemon.concurrency import ConcurrencyGate
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
from marianne.daemon.recurrence import RecurrenceController
from marianne.daemon.registry import (
    DaemonJobStatus,
    FailureHooksInProgressError,
    JobRecord,
    JobRegistry,
)
from marianne.daemon.schedule_registry import ScheduleRecord, ScheduleRegistry
from marianne.daemon.scheduler import GlobalSheetScheduler
from marianne.daemon.semantic_analyzer import SemanticAnalyzer
from marianne.daemon.snapshot import SnapshotManager
from marianne.daemon.task_utils import log_task_exception
from marianne.daemon.types import (
    JobDeadlineStatus,
    JobRequest,
    JobResponse,
    JobTimeoutCleanupStatus,
    ObserverEvent,
    ScheduleStatus,
)
from marianne.utils.time import utc_now

_logger = get_logger("daemon.manager")

# Filesystem timestamp tolerance for stale detection (#103).
# Avoids false positives from filesystem granularity differences.
_MTIME_TOLERANCE_SECONDS = 1.0

# Maps a daemon-level job status to the checkpoint-level JobStatus stored in
# live state (which serves `mzt status`). Module-level so a test can assert it
# is exhaustive over DaemonJobStatus (#234): an unmapped member silently
# skipped the live-state update, diverging `mzt status` from `mzt list`. The
# missing PAUSED_AT_CHAIN mapping was exactly that latent bug.
_DAEMON_TO_CHECKPOINT_STATUS: dict[DaemonJobStatus, JobStatus] = {
    DaemonJobStatus.QUEUED: JobStatus.PENDING,
    DaemonJobStatus.PENDING: JobStatus.PENDING,
    DaemonJobStatus.RUNNING: JobStatus.RUNNING,
    DaemonJobStatus.PAUSED: JobStatus.PAUSED,
    DaemonJobStatus.PAUSED_AT_CHAIN: JobStatus.PAUSED_AT_CHAIN,
    DaemonJobStatus.COMPLETED: JobStatus.COMPLETED,
    DaemonJobStatus.FAILED: JobStatus.FAILED,
    DaemonJobStatus.CANCELLED: JobStatus.CANCELLED,
}

_CANCELLED_JOB_STALE_SHEET_STATUSES = {
    SheetStatus.READY.value,
    SheetStatus.DISPATCHED.value,
    SheetStatus.IN_PROGRESS.value,
    SheetStatus.WAITING.value,
    SheetStatus.RETRY_SCHEDULED.value,
    SheetStatus.FERMATA.value,
}


def _normalize_cancelled_checkpoint_status(data: dict[str, Any]) -> dict[str, Any]:
    """Make terminal cancelled checkpoints truthful for status consumers.

    A cancel can be recorded in the registry after the last checkpoint write.
    In that case the job status is authoritative but individual sheets may
    still say "dispatched"/"waiting". Normalize only non-terminal active-ish
    sheet states; preserve completed/failed/skipped evidence and untouched
    pending sheets.
    """
    if data.get("status") != JobStatus.CANCELLED.value:
        return data

    sheets = data.get("sheets")
    if not isinstance(sheets, dict):
        return data

    for raw_sheet in sheets.values():
        if not isinstance(raw_sheet, dict):
            continue
        if raw_sheet.get("status") in _CANCELLED_JOB_STALE_SHEET_STATUSES:
            raw_sheet["status"] = SheetStatus.CANCELLED.value
            raw_sheet.setdefault("error_message", "Job cancelled")
    return data


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


# Resuming from these states preserves terminal sheets (a deliberate halt — its
# completed/failed/skipped sheets reflect decisions the operator wants kept).
# Any other resumable state (FAILED, CANCELLED) triggers intrinsic recovery.
_RESUME_PRESERVE_TERMINAL_STATUSES = frozenset(
    {DaemonJobStatus.PAUSED, DaemonJobStatus.PAUSED_AT_CHAIN}
)
_ACTIVE_DAEMON_STATUSES = frozenset(
    {DaemonJobStatus.QUEUED, DaemonJobStatus.RUNNING}
)
_TERMINAL_CHECKPOINT_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)


def _checkpoint_status_and_updated_at(
    checkpoint_json: str | None,
) -> tuple[str | None, datetime | None]:
    """Extract terminal comparison fields from serialized checkpoint JSON."""
    if not checkpoint_json:
        return None, None
    try:
        data = json.loads(checkpoint_json)
    except (json.JSONDecodeError, TypeError):
        return None, None
    raw_status = data.get("status")
    status = raw_status if isinstance(raw_status, str) else None
    raw_updated_at = data.get("updated_at")
    if not isinstance(raw_updated_at, str):
        return status, None
    try:
        parsed = datetime.fromisoformat(raw_updated_at)
    except ValueError:
        return status, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return status, parsed


def _reset_sheets_for_resume(
    checkpoint: CheckpointState,
    pre_resume_status: DaemonJobStatus | None,
    from_sheet: int | None = None,
) -> int:
    """Reset sheets in-place for an intrinsic-recover resume (#185).

    Makes ``mzt resume`` on a FAILED/CANCELLED job reset its unfinished sheets
    to PENDING (clearing retry budgets) and redispatch — retiring the separate
    ``mzt recover`` step. Returns the number of sheets reset.

    Reset rules:

    * ``from_sheet`` given (explicit operator override): reset every sheet with
      ``sheet_num >= from_sheet`` regardless of status or job state — bypasses
      the PAUSED guard and the cascade/deliberate-skip distinction.
    * ``pre_resume_status`` is None or in PAUSED / PAUSED_AT_CHAIN: reset nothing.
      A paused job's terminal sheets are deliberate and must be preserved; None
      means the caller opted out of intrinsic recovery (legacy/preserve).
    * otherwise (FAILED / CANCELLED): reset FAILED sheets and *cascade*-SKIPPED
      sheets. A SKIPPED sheet is cascade-blocked iff ``error_code is not None``
      (it was blocked by a failed dependency — work never done); a SKIPPED sheet
      with ``error_code is None`` was deliberately skipped (skip_when /
      --start-sheet / escalation-skip) and is preserved. This is the same
      discriminant the baton uses in ``_is_dep_satisfied``.

    Idempotent: only sheets in a resettable status are touched, so re-running on
    an already-reset checkpoint resets nothing.

    The caller must read ``pre_resume_status`` from the job's status *before* the
    resume transitions it to QUEUED/RUNNING — by the time ``_resume_via_baton``
    runs, ``meta.status`` is already RUNNING.
    """
    reset = 0
    for sheet in checkpoint.sheets.values():
        if from_sheet is not None:
            if sheet.sheet_num >= from_sheet and sheet.status != SheetStatus.PENDING:
                sheet.reset_for_retry()
                reset += 1
            continue
        if (
            pre_resume_status is None
            or pre_resume_status in _RESUME_PRESERVE_TERMINAL_STATUSES
        ):
            continue
        # Reset FAILED sheets and cascade-SKIPPED sheets (error_code set =
        # blocked by a failed dependency). Deliberate skips (error_code None:
        # skip_when / --start-sheet / escalation-skip) are preserved.
        is_cascade_skip = (
            sheet.status == SheetStatus.SKIPPED and sheet.error_code is not None
        )
        if sheet.status == SheetStatus.FAILED or is_cascade_skip:
            sheet.reset_for_retry()
            reset += 1
    return reset


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
    schedule_id: str | None = None
    max_wall_seconds: float | None = None
    wall_deadline_at: float | None = None
    terminal_reason: str | None = None
    cleanup_generation: str | None = None
    timeout_cleanup_outcome: JobTimeoutCleanupStatus | None = None
    # Live adapter cleanup evidence while a post-SIGTERM grace callback is
    # pending. Kept on the already-owned JobMeta, not in unbounded adapter maps.
    timeout_cleanup_result_ref: Any | None = field(default=None, repr=False)
    deadline_diagnostic: str | None = None
    hook_config: list[dict[str, Any]] | None = field(default=None, repr=False)
    failure_hook_config: list[dict[str, Any]] | None = field(default=None, repr=False)
    concert_config: dict[str, Any] | None = field(default=None, repr=False)
    completed_new_work: bool = False
    observer: JobObserver | None = field(default=None, repr=False)
    pending_modify: tuple[Path, Path | None] | None = field(default=None, repr=False)
    held_chain_hook: dict[str, Any] | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for JSON-RPC responses."""
        result: dict[str, Any] = {
            "job_id": self.job_id,
            "status": self.status,
            "config_path": str(self.config_path),
            "workspace": str(self.workspace),
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            # #253: emit the same always-present keys as JobRecord.to_dict so
            # list_jobs returns one consistent shape for active and registry
            # jobs (consumers can rely on these keys existing). JobMeta doesn't
            # track these in-memory — None here; live progress (current/total
            # sheet) is available from the checkpoint, not this summary dict.
            "pid": None,
            "completed_at": None,
            "current_sheet": None,
            "total_sheets": None,
        }
        if self.error_message:
            result["error_message"] = self.error_message
        if self.error_traceback:
            result["error_traceback"] = self.error_traceback
        if self.chain_depth is not None:
            result["chain_depth"] = self.chain_depth
        if self.max_wall_seconds is not None:
            result["max_wall_seconds"] = self.max_wall_seconds
        if self.wall_deadline_at is not None:
            result["wall_deadline_at"] = self.wall_deadline_at
        if self.terminal_reason is not None:
            result["terminal_reason"] = self.terminal_reason
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


def _merge_runtime_variables(
    config: JobConfig, runtime_variables: dict[str, str]
) -> JobConfig:
    """Merge #359 runtime variables into config.prompt.variables.

    Module-level (not a method) so the single first-run/resume seam can
    never be shadowed by a selectively-mocked manager — calling it via
    ``self.`` on a MagicMock returns a mock and silently rewrites config
    (the mock-binding trap). CLI variables override YAML on key collision
    (the documented make/terraform contract). No-op when empty.
    """
    if not runtime_variables:
        return config
    merged = dict(config.prompt.variables)
    merged.update(runtime_variables)
    return config.model_copy(
        update={"prompt": config.prompt.model_copy(update={"variables": merged})}
    )


async def _setup_worktree_isolation(
    job_id: str, config: Any, worktrees: dict[str, Path]
) -> Any:
    """#197: per-JOB git worktree isolation. Module-level (not a method)
    so a spec'd-mock manager can't shadow it and silently replace config
    (the #359 mock-binding trap). Returns a config whose workspace points
    at an isolated worktree, or the original config when isolation is off
    / not applicable. On a non-git workspace or creation failure,
    ``fallback_on_error`` decides: continue without isolation (default)
    or fail the job loudly.
    """
    from marianne.core.config.workspace import IsolationMode

    iso = config.isolation
    if not iso.enabled or iso.mode != IsolationMode.WORKTREE:
        return config

    from marianne.isolation.worktree import GitWorktreeManager, WorktreeError

    repo = Path(config.workspace)
    mgr = GitWorktreeManager(repo)
    try:
        if not mgr.is_git_repository():
            raise WorktreeError(
                f"workspace {repo} is not a git repository — worktree "
                "isolation needs one"
            )
        result = await mgr.create_worktree_detached(
            job_id,
            source_ref=iso.source_branch,
            worktree_base=iso.get_worktree_base(repo),
            lock=iso.lock_during_execution,
        )
        if not result.success or result.worktree is None:
            raise WorktreeError(result.error or "worktree creation failed")
    except WorktreeError:
        if iso.fallback_on_error:
            _logger.warning(
                "worktree.isolation_fallback",
                job_id=job_id,
                workspace=str(repo),
                exc_info=True,
            )
            return config
        raise

    wt_path = result.worktree.path
    worktrees[job_id] = wt_path
    _logger.info("worktree.isolation_active", job_id=job_id, worktree=str(wt_path))
    return config.model_copy(update={"workspace": wt_path})


async def _cleanup_worktree_isolation(
    job_id: str, config: Any, worktrees: dict[str, Path], *, success: bool
) -> None:
    """#197: remove a job's worktree per cleanup_on_success/failure.
    Module-level for the same anti-mock-shadow reason. No-op when the job
    had no worktree. Fail-open.
    """
    wt_path = worktrees.pop(job_id, None)
    if wt_path is None:
        return
    iso = config.isolation
    should_remove = iso.cleanup_on_success if success else iso.cleanup_on_failure
    if not should_remove:
        _logger.info("worktree.preserved", job_id=job_id, worktree=str(wt_path))
        return
    try:
        from marianne.isolation.worktree import GitWorktreeManager

        mgr = GitWorktreeManager(Path(config.workspace))
        await mgr.remove_worktree(wt_path, force=True)
        _logger.info("worktree.removed", job_id=job_id, worktree=str(wt_path))
    except Exception:
        _logger.warning(
            "worktree.cleanup_failed",
            job_id=job_id,
            worktree=str(wt_path),
            exc_info=True,
        )


@dataclass
class _JobAdmissionReservation:
    """One task's reentrant ownership of a job-ID activation boundary."""

    owner: asyncio.Task[Any]
    depth: int = 1
    released: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


@dataclass(frozen=True)
class _ScheduleIndex:
    """Prebuilt durable-identity indexes for status and lifecycle lookup."""

    records: tuple[ScheduleRecord, ...]
    by_id: dict[str, ScheduleRecord]
    by_path: dict[Path, ScheduleRecord]
    by_stem: dict[str, ScheduleRecord]


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
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        recurrence_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._start_time = start_time or time.monotonic()
        self._pgroup = pgroup
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._recurrence_clock = recurrence_clock or utc_now

        # Phase 3: Centralized learning hub.
        # Single GlobalLearningStore shared across all jobs — pattern
        # discoveries in Job A are instantly available to Job B.
        self._learning_hub = LearningHub()

        # Deferred to start() where the learning hub's store is available.
        self._service: JobService | None = None
        self._jobs: dict[str, asyncio.Task[Any]] = {}
        self._failure_hook_tasks: dict[str, asyncio.Task[Any]] = {}
        self._job_meta: dict[str, JobMeta] = {}
        self._cleanup_generation_counter = 0
        # Admission is reserved synchronously before recurrence I/O so a
        # rejected same-ID request cannot publish autonomous work.
        self._job_admission_reservations: dict[str, _JobAdmissionReservation] = {}
        # Recurring lifecycle ownership is keyed by the durable schedule ID,
        # not by one presentation anchor or scheduled child job ID.  Like job
        # admission, it is task-owned and reentrant so controller tick calls
        # can submit their child while holding the recurrence lifecycle lock.
        self._schedule_admission_reservations: dict[
            str, _JobAdmissionReservation
        ] = {}
        # Manual scheduled submissions reserve their score-name lineage before
        # recurrence arms a timer and release it only after JobMeta is visible.
        self._schedule_lineage_reservations: dict[str, int] = {}
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
        # #231: a live-resizable gate, not a raw Semaphore — apply_config()
        # adjusts the limit in place so a SIGHUP reload never orphans in-flight
        # acquisitions (replacing the object over-admits; see ConcurrencyGate).
        self._concurrency_semaphore = ConcurrencyGate(
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

        # Fleet management — tracks running fleets for fleet-level operations
        self._fleet_records: dict[str, Any] = {}

        # Phase 3: Backpressure controller.
        # Uses a single ResourceMonitor instance shared with DaemonProcess
        # for both periodic monitoring and point-in-time backpressure checks.
        # When no monitor is injected (e.g. unit tests), a standalone one
        # is created that only does point-in-time reads.
        self._monitor = monitor or ResourceMonitor(config.resource_limits, manager=self)
        self._backpressure = BackpressureController(
            self._monitor,
            self._rate_coordinator,
        )

        # Persistent job registry — survives daemon restarts.
        db_path = config.state_db_path.expanduser()
        self._registry = JobRegistry(db_path)
        self._schedule_registry = ScheduleRegistry(db_path)
        self._recurrence_controller: RecurrenceController | None = None

        # Event bus for routing runner and observer events to consumers.
        self._event_bus = EventBus(
            max_queue_size=config.observer.max_queue_size,
        )

        # Semantic analyzer — LLM-based analysis of sheet completions.
        # Initialized in start() after the event bus is ready.
        self._semantic_analyzer: SemanticAnalyzer | None = None

        # #203: judgment client — automated FERMATA decider. Initialized in
        # start() after the baton adapter exists (it produces decisions via
        # adapter.resolve_fermata).
        self._judgment_client: Any | None = None

        # Phase 4: Completion snapshots — captures workspace artifacts
        # at job completion with TTL-based cleanup.
        self._snapshot_manager = SnapshotManager()

        # Observer event recorder — persists per-job observer events to JSONL.
        # Initialized eagerly, started in start() after event bus.
        self._observer_recorder: ObserverRecorder | None = None

        # Baton adapter — the execution engine for all jobs.
        # Initialized in start(). Import deferred to avoid circular import.
        from marianne.daemon.baton.adapter import BatonAdapter

        self._baton_adapter: BatonAdapter | None = None
        self._mcp_pool: Any | None = None
        # #171: live instrument registry, retained for SIGHUP hot-reload.
        self._instrument_registry: InstrumentRegistry | None = None
        # #197: per-job git worktree paths for isolation cleanup.
        self._job_worktrees: dict[str, Path] = {}
        self._baton_loop_task: asyncio.Task[Any] | None = None
        # #111: ordered, acknowledged checkpoint writer. Created in start()
        # (needs the running loop); replaces fire-and-forget save_checkpoint
        # tasks so per-job persists never reorder.
        self._checkpoint_writer: CheckpointWriter | None = None

    # ─── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start daemon subsystems (learning hub, monitor, etc.)."""
        # Open the async registry connection (tables + WAL mode)
        await self._registry.open()
        await self._schedule_registry.open()

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
                    hook_config = json.loads(hook_json)

                failure_hook_config: list[dict[str, Any]] | None = None
                failure_hook_json = await self._registry.get_failure_hook_config(
                    record.job_id,
                )
                if failure_hook_json:
                    failure_hook_config = json.loads(failure_hook_json)

                concert_config: dict[str, Any] | None = None
                concert_json = await self._registry.get_concert_config(record.job_id)
                if concert_json:
                    concert_config = json.loads(concert_json)

                max_wall_seconds = record.max_wall_seconds
                wall_deadline_at = record.wall_deadline_at
                terminal_reason = record.terminal_reason
                if wall_deadline_at is None and record.checkpoint_json:
                    try:
                        restored_checkpoint = CheckpointState.model_validate_json(
                            record.checkpoint_json
                        )
                    except ValueError:
                        restored_checkpoint = None
                    if (
                        restored_checkpoint is not None
                        and restored_checkpoint.wall_deadline_at is not None
                    ):
                        max_wall_seconds = restored_checkpoint.max_wall_seconds
                        wall_deadline_at = restored_checkpoint.wall_deadline_at
                        terminal_reason = restored_checkpoint.terminal_reason
                        _logger.info(
                            "manager.deadline_restored_from_checkpoint",
                            job_id=record.job_id,
                            wall_deadline_at=wall_deadline_at,
                        )

                self._job_meta[record.job_id] = JobMeta(
                    job_id=record.job_id,
                    config_path=Path(record.config_path),
                    workspace=Path(record.workspace),
                    submitted_at=record.submitted_at,
                    started_at=record.started_at,
                    status=record.status,
                    error_message=record.error_message,
                    max_wall_seconds=max_wall_seconds,
                    wall_deadline_at=wall_deadline_at,
                    terminal_reason=terminal_reason,
                    deadline_diagnostic=record.deadline_diagnostic,
                    hook_config=hook_config,
                    failure_hook_config=failure_hook_config,
                    concert_config=concert_config,
                    chain_depth=record.chain_depth,
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
            event_callback=self._on_event,
            state_publish_callback=self._on_state_published,
            registry=self._registry,
            token_warning_threshold=self._config.preflight.token_warning_threshold,
            token_error_threshold=self._config.preflight.token_error_threshold,
            pgroup_manager=self._pgroup,
        )
        # Build the instrument registry up-front. After Phase 1 of the
        # backend atlas migration, all non-baton backend creation must
        # route through the registry (Doctrine RULE: "All model invocations
        # must route through the instrument plugin system after Phase 1").
        # The semantic analyzer and the baton adapter share a single
        # registry instance so that both observe the same set of profiles.
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.daemon.baton.backend_pool import (
            BackendPool,
            create_backend_for_instrument,
        )
        from marianne.instruments.loader import load_all_profiles
        from marianne.instruments.registry import InstrumentRegistry

        profiles = load_all_profiles()
        registry = InstrumentRegistry()
        for profile in profiles.values():
            registry.register(profile, override=True)
        # #171: retain for SIGHUP hot-reload of instrument profiles.
        self._instrument_registry = registry

        # Start semantic analyzer after event bus (needs bus for subscription).
        # Failure must not prevent the conductor from starting.
        try:
            semantic_backend = create_backend_for_instrument(
                registry,
                self._config.learning.instrument,
                model=self._config.learning.model,
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

        # Initialize the baton execution engine with the shared registry.
        self._baton_adapter = BatonAdapter(
            event_bus=self._event_bus,
            max_concurrent_sheets=self._config.max_concurrent_sheets,
            persist_callback=self._on_baton_persist,
            # #200: feed learned patterns into prompts. The hub is started at
            # the top of this method, so the store is available; guard anyway
            # so a store-less startup keeps the adapter functional (None skips).
            learning_store=(
                self._learning_hub.store
                if self._learning_hub.is_running
                else None
            ),
            # #206: mirror baton rate limits into the daemon coordinator so
            # backpressure / submit-time warnings / the scheduler see them.
            rate_limit_reporter=self._on_rate_limit,
            # #133: runtime diagnostics (observer events + resource state)
            # for retry failure-evidence enrichment.
            diagnostic_snapshot_fn=self._diagnostic_snapshot,
        )
        self._baton_adapter.set_backend_pool(BackendPool(registry, pgroup=self._pgroup))

        recurrence_controller = RecurrenceController(
            self._schedule_registry,
            self.submit_job,
            self._baton_adapter.schedule_cron_tick,
            self._baton_adapter.cancel_cron_tick,
            self._is_schedule_active,
            now=self._recurrence_clock,
        )
        self._baton_adapter.set_cron_handler(recurrence_controller.handle_tick)
        self._recurrence_controller = recurrence_controller

        if self._config.mcp_pool.servers:
            from marianne.daemon.mcp_pool import McpPoolManager

            self._mcp_pool = McpPoolManager(self._config.mcp_pool)
            try:
                await self._mcp_pool.start_all()
                self._baton_adapter.set_mcp_pool(self._mcp_pool)
            except Exception:
                _logger.warning("manager.mcp_pool_start_failed", exc_info=True)
                try:
                    await self._mcp_pool.stop_all()
                except Exception:
                    _logger.warning("manager.mcp_pool_stop_after_start_failed", exc_info=True)
                self._mcp_pool = None

        # Populate per-model concurrency from instrument profiles
        for profile in profiles.values():
            for model in profile.models:
                self._baton_adapter._baton.set_model_concurrency(
                    profile.name,
                    model.name,
                    model.max_concurrent,
                )

        # #111: start the ordered checkpoint writer BEFORE the baton loop, so
        # every persist callback (which fires from that loop) has a consumer and
        # writes are serialized in order.
        checkpoint_writer = CheckpointWriter(self._registry)
        checkpoint_writer.start()
        self._checkpoint_writer = checkpoint_writer

        # Start the baton's event loop as a background task
        self._baton_loop_task = asyncio.create_task(
            self._baton_adapter.run(),
            name="baton-loop",
        )
        _logger.info("manager.baton_adapter_started")

        # Interactive-mode hygiene: tmux sessions survive a daemon crash
        # (the tmux server is not our child). Nothing has dispatched yet,
        # so every mzt-* session on the marianne socket is residue from a
        # previous daemon life — kill them. Best-effort: a sweep failure
        # must never block conductor startup.
        await self._sweep_orphan_interactive_sessions()

        # Restore recurring work only after stale interactive sessions are
        # gone. An overdue latest-policy tick may submit immediately.
        await recurrence_controller.restore()

        # Recover paused orphans through the baton.
        await self._recover_baton_orphans()

        # A conductor may restart after the registry committed FAILED but
        # before the in-process callback claimed its failure hooks. Reconcile
        # those unclaimed terminal failures after all execution services exist.
        for record in all_records:
            if record.status is DaemonJobStatus.FAILED:
                self._schedule_failure_hooks(record.job_id)

        # #203: judgment client — automated FERMATA decider. Started AFTER
        # orphan recovery so its startup reconciliation sees restart-recovered
        # FERMATA sheets. Failure must not prevent the conductor from starting
        # (fail-open: sheets stay composer-resolvable).
        try:
            from marianne.daemon.judgment import JudgmentClient

            adapter = self._baton_adapter

            def _judgment_backend(instrument: str) -> Any:
                return create_backend_for_instrument(registry, instrument)

            judgment_client = JudgmentClient(
                live_states=self._live_states,
                resolve_fn=adapter.resolve_fermata,
                backend_factory=_judgment_backend,
                diagnostic_fn=self._diagnostic_snapshot,
            )
            await judgment_client.start(self._event_bus)
            self._judgment_client = judgment_client
        except Exception:
            _logger.warning(
                "manager.judgment_client_start_failed", exc_info=True
            )
            self._judgment_client = None

        _logger.info(
            "manager.started",
            scheduler_status="lazy_not_wired",
            scheduler_note="Phase 3 scheduler is lazily initialized and not "
            "yet driving execution. Jobs run through the baton engine.",
            semantic_analyzer="active" if self._semantic_analyzer else "unavailable",
            observer_recorder="active" if self._observer_recorder else "unavailable",
            baton_adapter="active",
        )

    async def _sweep_orphan_interactive_sessions(self) -> None:
        """Kill orphaned interactive tmux sessions from a prior daemon life.

        Runs at startup, before any sheet has dispatched — every ``mzt-*``
        session on the marianne socket is therefore an orphan whose job is
        being failed/recovered by the registry orphan pass above. Killing
        them releases the agent processes and their MCP children. Without
        tmux installed (or with no server running) this is a silent no-op.
        """
        from marianne.execution.instruments.interactive.tmux import (
            SESSION_SWEEP_PREFIX,
            TmuxControl,
            TmuxError,
        )

        try:
            tmux = TmuxControl()
            orphans = await tmux.list_sessions(prefix=SESSION_SWEEP_PREFIX)
            for session in orphans:
                await tmux.kill_session(session)
            if orphans:
                _logger.warning(
                    "manager.interactive_orphans_swept",
                    count=len(orphans),
                    sessions=orphans,
                )
        except TmuxError as e:
            _logger.warning(
                "manager.interactive_orphan_sweep_failed",
                error=str(e),
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
        changes that can be safely updated at runtime. Adjusts the
        concurrency gate's limit in place if ``max_concurrent_jobs`` changed.

        #231: the limit is resized via ``ConcurrencyGate.set_limit()`` — NOT by
        replacing the object. Replacing it orphaned in-flight acquisitions (they
        release into the dead object) while the new object started with all
        permits free, so a lower over-admitted. In-flight jobs are unaffected by
        a resize; a lowered limit takes effect as running jobs drain.
        """
        old = self._config

        # Resize the concurrency gate in place if the limit changed.
        if new_config.max_concurrent_jobs != old.max_concurrent_jobs:
            _logger.info(
                "manager.config_reloaded",
                field="max_concurrent_jobs",
                old_value=old.max_concurrent_jobs,
                new_value=new_config.max_concurrent_jobs,
            )
            self._concurrency_semaphore.set_limit(
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

    async def _set_job_status(
        self,
        job_id: str,
        status: DaemonJobStatus,
        *,
        error_message: str | None = None,
        pid: int | None = None,
        snapshot_path: str | None = None,
        terminal_reason: str | None = None,
    ) -> None:
        """Update job status across all three stores atomically.

        Every job status change MUST go through this method. Direct
        assignment to meta.status, live.status, or registry.update_status
        produces divergent state that manifests as contradictory display
        in mzt list vs mzt status.

        The three stores:
        1. _job_meta[job_id].status — DaemonJobStatus, serves mzt list
        2. _live_states[job_id].status — JobStatus, serves mzt status
        3. registry status column — string, serves historical queries

        Args:
            job_id: The job to update.
            status: New DaemonJobStatus.
            error_message: Optional error for FAILED status.
            pid: Optional PID for RUNNING status.
            snapshot_path: Optional snapshot path for terminal statuses.
        """
        # Atomicity (#313): the persistent registry write is the only fallible
        # step (disk full, SQLite lock), so it goes FIRST. If it raises, the
        # in-memory meta/live below are never touched — all three stores stay
        # at the old value and the exception propagates, so we never leave
        # `mzt list` (registry) contradicting `mzt status` (live). The two
        # in-memory assignments that follow are pure and run with no `await`
        # between them, so on the single-threaded event loop they apply
        # atomically — no observer can see a half-updated state.

        # 1. Persistent registry (fallible — do this first).
        await self._registry.update_status(
            job_id,
            status.value,
            error_message=error_message,
            pid=pid,
            snapshot_path=snapshot_path,
            terminal_reason=terminal_reason,
        )

        # 2. In-memory metadata (always available for active jobs).
        meta = self._job_meta.get(job_id)
        if meta is not None:
            object.__setattr__(meta, "status", status)
            if error_message is not None:
                meta.error_message = error_message
            if status not in {
                DaemonJobStatus.COMPLETED,
                DaemonJobStatus.FAILED,
                DaemonJobStatus.CANCELLED,
            }:
                meta.terminal_reason = None
            elif terminal_reason is not None:
                meta.terminal_reason = terminal_reason

        # 3. Live checkpoint state (may not exist yet for queued jobs).
        live = self._live_states.get(job_id)
        if live is not None:
            cp_status = _DAEMON_TO_CHECKPOINT_STATUS.get(status)
            if cp_status is not None:
                object.__setattr__(live, "status", cp_status)
                if status not in {
                    DaemonJobStatus.COMPLETED,
                    DaemonJobStatus.FAILED,
                    DaemonJobStatus.CANCELLED,
                }:
                    live.terminal_reason = None
                elif terminal_reason is not None:
                    live.terminal_reason = terminal_reason
            else:
                # #234: an unmapped status must not silently skip the live
                # update (which would diverge `mzt status` from `mzt list`).
                # The exhaustiveness test guards against this at CI time; this
                # warning surfaces it if one ever slips through at runtime.
                _logger.warning(
                    "job_status.unmapped_daemon_status",
                    job_id=job_id,
                    status=status.value,
                )

        if status is DaemonJobStatus.FAILED:
            self._schedule_failure_hooks(job_id)

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

    def _persist_checkpoint(self, job_id: str, checkpoint_json: str) -> None:
        """Persist a checkpoint via the ordered writer (#111).

        Routes through the serialized ``CheckpointWriter`` so per-job saves never
        reorder. Falls back to a direct fire-and-forget task only if the writer
        isn't running (e.g. a manager constructed without ``start()`` in tests);
        the normal daemon path always has it running.
        """
        writer = self._checkpoint_writer
        if writer is not None and writer.running:
            writer.enqueue(job_id, checkpoint_json)
            return
        asyncio.get_event_loop().create_task(
            self._registry.save_checkpoint(job_id, checkpoint_json),
            name=f"checkpoint-save-{job_id}",
        )

    def _on_baton_persist(self, job_id: str) -> None:
        """Phase 2 persist callback — save CheckpointState to registry.

        The baton writes directly to SheetState objects inside _live_states.
        This callback refreshes CheckpointState-level metadata from the
        current sheet states (so mzt status shows accurate progress and
        timestamps), then serializes and saves to the registry.
        """
        live = self._live_states.get(job_id)
        if live is None:
            return
        try:
            # Refresh checkpoint metadata from sheet states.
            # The baton mutates sheet.status in place but doesn't call
            # CheckpointState convenience methods, so checkpoint-level
            # fields (updated_at, last_completed_sheet, started_at)
            # become stale. Refresh them here before serializing.
            from marianne.core.checkpoint import SheetStatus
            from marianne.utils.time import utc_now

            live.updated_at = utc_now()

            # Count completed sheets — use count, not sequential index,
            # because the baton completes sheets concurrently.
            completed_count = sum(
                1 for s in live.sheets.values() if s.status == SheetStatus.COMPLETED
            )
            live.last_completed_sheet = completed_count

            # Set started_at on first activity if not set or stale
            any_started = any(
                s.status not in (SheetStatus.PENDING, SheetStatus.READY)
                for s in live.sheets.values()
            )
            if any_started and live.started_at is None:
                live.started_at = utc_now()

            checkpoint_json = live.model_dump_json()
            self._persist_checkpoint(job_id, checkpoint_json)
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
        object.__setattr__(sheet_state, "status", status)
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
                    # #195: prefer the structured E-code; fall back to the
                    # classification bucket for synthetic-writer attempts
                    # (STALE/CANCELLED/E505/PROCESS_CRASH) that set no error_code.
                    error_code = last.error_code or last.error_classification
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
                    datetime.now(UTC) + timedelta(seconds=delta) if delta > 0 else None
                )
            else:
                sheet_state.fire_at = None

            # Rate limit expiry from instrument state
            rate_inst = baton_state.instrument_name
            inst_state = (
                self._baton_adapter._baton.get_instrument_state(rate_inst)
                if self._baton_adapter
                else None
            )
            if (
                inst_state
                and inst_state.rate_limited
                and inst_state.rate_limit_expires_at is not None
            ):
                delta_rl = inst_state.rate_limit_expires_at - time.monotonic()
                sheet_state.rate_limit_expires_at = (
                    datetime.now(UTC) + timedelta(seconds=delta_rl) if delta_rl > 0 else None
                )
            else:
                sheet_state.rate_limit_expires_at = None

            sheet_state.healing_attempts = baton_state.healing_attempts

        # Persist to registry on significant transitions for crash recovery.
        if status.is_terminal or status in (
            SheetStatus.DISPATCHED,
            SheetStatus.IN_PROGRESS,
        ):
            try:
                checkpoint_json = live.model_dump_json()
                self._persist_checkpoint(job_id, checkpoint_json)
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

            if not self._try_reserve_job_admission(job_id):
                _logger.info(
                    "baton.orphan_recovery_admission_conflict",
                    job_id=job_id,
                )
                continue

            try:
                # Publish active status while admission is still owned. The
                # recovery task may wait at the concurrency gate, but same-ID
                # submissions must already reject before recurrence mutation.
                await self._set_job_status(job_id, DaemonJobStatus.QUEUED)
                resume_coro = self._resume_job_task(job_id, meta.workspace)
                try:
                    task = asyncio.create_task(
                        resume_coro,
                        name=f"job-recover-{job_id}",
                    )
                except Exception:
                    resume_coro.close()
                    await self._set_job_status(job_id, DaemonJobStatus.PAUSED)
                    raise
                self._jobs[job_id] = task

                def _on_done(
                    t: asyncio.Task[Any],
                    *,
                    _jid: str = job_id,
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
            finally:
                self._release_job_admission(job_id)

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

    def _is_schedule_active(self, schedule_id: str) -> bool:
        """Return whether any queued or running job belongs to this schedule."""
        if self._schedule_lineage_reservations.get(schedule_id, 0) > 0:
            return True
        active_statuses = {
            DaemonJobStatus.PENDING,
            DaemonJobStatus.QUEUED,
            DaemonJobStatus.RUNNING,
        }
        return any(
            meta.schedule_id == schedule_id and meta.status in active_statuses
            for meta in self._job_meta.values()
        )

    def _try_reserve_job_admission(
        self,
        job_id: str,
        *,
        allow_active: bool = False,
    ) -> bool:
        """Reserve one job ID before any recurrence projection is published."""
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("job admission requires a running asyncio task")

        reservation = self._job_admission_reservations.get(job_id)
        if reservation is not None:
            if reservation.owner is not current_task:
                return False
            reservation.depth += 1
            return True

        existing = self._job_meta.get(job_id)
        if not allow_active and existing is not None and existing.status in {
            DaemonJobStatus.PENDING,
            DaemonJobStatus.QUEUED,
            DaemonJobStatus.RUNNING,
        }:
            return False
        self._job_admission_reservations[job_id] = _JobAdmissionReservation(
            owner=current_task,
        )
        return True

    def _release_job_admission(self, job_id: str) -> None:
        """Release one counted job-ID reservation without disturbing peers."""
        reservation = self._job_admission_reservations.get(job_id)
        current_task = asyncio.current_task()
        if reservation is None or reservation.owner is not current_task:
            raise RuntimeError(f"job admission for '{job_id}' released by non-owner")
        reservation.depth -= 1
        if reservation.depth == 0:
            self._job_admission_reservations.pop(job_id)
            reservation.released.set()

    async def _wait_for_job_admission(self, job_id: str) -> None:
        """Own a queued job's activation boundary once its current owner leaves.

        Each reservation carries its own release generation. A waiter that
        wakes after another task has already acquired the job ID re-checks the
        reservation and waits on the new owner's signal instead of crossing
        that owner's boundary.
        """
        while not self._try_reserve_job_admission(job_id, allow_active=True):
            reservation = self._job_admission_reservations.get(job_id)
            if reservation is None:
                raise RuntimeError(
                    f"job admission for '{job_id}' unavailable without an owner"
                )
            await reservation.released.wait()

    def _schedule_admission_available(self, schedule_ids: Sequence[str]) -> bool:
        """Return whether this task could claim every schedule without waiting."""
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("schedule admission requires a running asyncio task")
        return all(
            (reservation := self._schedule_admission_reservations.get(schedule_id))
            is None
            or reservation.owner is current_task
            for schedule_id in schedule_ids
        )

    def _try_reserve_schedule_admission(
        self,
        schedule_ids: Sequence[str],
    ) -> bool:
        """Atomically reserve schedule lifecycle publication for this task."""
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("schedule admission requires a running asyncio task")
        normalized = tuple(dict.fromkeys(schedule_ids))
        if not self._schedule_admission_available(normalized):
            return False
        for schedule_id in normalized:
            reservation = self._schedule_admission_reservations.get(schedule_id)
            if reservation is None:
                self._schedule_admission_reservations[schedule_id] = (
                    _JobAdmissionReservation(owner=current_task)
                )
            else:
                reservation.depth += 1
        return True

    def _release_schedule_admission(self, schedule_ids: Sequence[str]) -> None:
        """Release one counted ownership claim for every supplied schedule."""
        current_task = asyncio.current_task()
        for schedule_id in reversed(tuple(dict.fromkeys(schedule_ids))):
            reservation = self._schedule_admission_reservations.get(schedule_id)
            if reservation is None or reservation.owner is not current_task:
                raise RuntimeError(
                    f"schedule admission for '{schedule_id}' released by non-owner"
                )
            reservation.depth -= 1
            if reservation.depth == 0:
                self._schedule_admission_reservations.pop(schedule_id)

    def _reserve_schedule_lineage(self, schedule_id: str) -> None:
        """Publish one pending manual run before its schedule timer is armed."""
        self._schedule_lineage_reservations[schedule_id] = (
            self._schedule_lineage_reservations.get(schedule_id, 0) + 1
        )

    def _release_schedule_lineage(self, schedule_id: str) -> None:
        """Release one pending manual reservation without disturbing peers."""
        remaining = self._schedule_lineage_reservations.get(schedule_id, 0) - 1
        if remaining > 0:
            self._schedule_lineage_reservations[schedule_id] = remaining
        else:
            self._schedule_lineage_reservations.pop(schedule_id, None)

    def _ensure_workspace_log_path(self, workspace: Path) -> Path | None:
        """Expose the daemon log through the score workspace when configured."""
        daemon_log = self._config.log_file
        if daemon_log is None:
            return None

        workspace_log = workspace / "logs" / "marianne.log"
        target = daemon_log.expanduser()
        if not target.is_absolute():
            target = target.resolve(strict=False)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch(exist_ok=True)
            workspace_log.parent.mkdir(parents=True, exist_ok=True)

            if workspace_log.is_symlink():
                if workspace_log.resolve(strict=False) == target.resolve(strict=False):
                    return workspace_log
                workspace_log.unlink()
            elif workspace_log.exists():
                if workspace_log.is_file() and workspace_log.stat().st_size == 0:
                    workspace_log.unlink()
                else:
                    return workspace_log

            workspace_log.symlink_to(target)
            return workspace_log
        except OSError as exc:
            _logger.warning(
                "manager.workspace_log_path_unavailable",
                workspace=str(workspace),
                log_path=str(workspace_log),
                target=str(target),
                error=str(exc),
            )
            return target

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

        job_id = self._get_job_id(request.job_id or request.config_path.stem)

        # A failure hook can still be producing external side effects after
        # the score itself becomes terminal. Reusing the stable job ID during
        # that window would reset its durable claim beneath the live hook.
        failure_hook_task = self._failure_hook_tasks.get(job_id)
        if failure_hook_task is not None and not failure_hook_task.done():
            return JobResponse(
                job_id=job_id,
                status="rejected",
                message=(
                    f"Job '{job_id}' is still running terminal failure hooks. "
                    "Wait for those hooks to finish before submitting it again."
                ),
            )

        # Validate config exists and resolve workspace BEFORE acquiring the
        # lock. Config parsing is expensive and doesn't need serialization
        # — it's idempotent and job-independent.
        if not request.config_path.exists():
            return JobResponse(
                job_id=job_id,
                status="rejected",
                message=f"Config file not found: {request.config_path}",
            )

        # Fleet detection: route fleet configs to the fleet manager
        from marianne.daemon.fleet import is_fleet_config, submit_fleet

        if is_fleet_config(request.config_path):
            from marianne.core.config.fleet import FleetConfig

            try:
                with open(request.config_path) as f:
                    raw = yaml.safe_load(f)
                fleet_config = FleetConfig.model_validate(raw)
            except Exception as exc:
                return JobResponse(
                    job_id=job_id,
                    status="rejected",
                    message=f"Failed to parse fleet config: {exc}",
                )
            return await submit_fleet(self, request.config_path, fleet_config)

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

        # Resolve relative workspace against client_cwd (working directory fix).
        # When the CLI sends client_cwd, relative workspace paths from the
        # config should resolve against where the user invoked the command,
        # not where the daemon was spawned.
        if request.client_cwd and not workspace.is_absolute():
            workspace = (request.client_cwd / workspace).resolve()
            _logger.debug(
                "manager.workspace_resolved_from_client_cwd",
                job_id=job_id,
                client_cwd=str(request.client_cwd),
                workspace=str(workspace),
            )

        # Extract hook config from parsed config for daemon-owned execution.
        hook_config_list: list[dict[str, Any]] | None = None
        failure_hook_config_list: list[dict[str, Any]] | None = None
        concert_config_dict: dict[str, Any] | None = None
        if parsed_config and parsed_config.on_success:
            hook_config_list = [h.model_dump(mode="json") for h in parsed_config.on_success]
        if parsed_config and parsed_config.on_failure:
            failure_hook_config_list = [h.model_dump(mode="json") for h in parsed_config.on_failure]
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

        # Claim this job ID without awaiting before recurrence can publish a
        # durable schedule or timer. Contenders reject instead of waiting, so
        # no manager lock is held while recurrence performs I/O.
        if not self._try_reserve_job_admission(job_id):
            existing = self._job_meta.get(job_id)
            if existing is not None and existing.status in {
                DaemonJobStatus.PENDING,
                DaemonJobStatus.QUEUED,
                DaemonJobStatus.RUNNING,
            }:
                detail = f"already {existing.status.value}"
            else:
                detail = "already being submitted"
            return JobResponse(
                job_id=job_id,
                status="rejected",
                message=(
                    f"Job '{job_id}' is {detail}. "
                    "Use 'mzt pause' or 'mzt cancel' first, or wait for it to finish."
                ),
            )

        schedule_admission_claims: list[tuple[str, ...]] = []

        def _claim_schedule_admission(schedule_ids: tuple[str, ...]) -> None:
            if not self._try_reserve_schedule_admission(schedule_ids):
                joined = ", ".join(repr(value) for value in schedule_ids)
                raise JobSubmissionError(
                    f"Schedule lifecycle for {joined} is already active"
                )
            schedule_admission_claims.append(schedule_ids)

        def _probe_schedule_admission(schedule_ids: tuple[str, ...]) -> bool:
            if not self._schedule_admission_available(schedule_ids):
                joined = ", ".join(repr(value) for value in schedule_ids)
                raise JobSubmissionError(
                    f"Schedule lifecycle for {joined} is already active"
                )
            return True

        try:
            if request.scheduled_due_at is not None and request.schedule_id is not None:
                try:
                    _claim_schedule_admission((request.schedule_id,))
                except JobSubmissionError as exc:
                    return JobResponse(
                        job_id=job_id,
                        status="rejected",
                        message=str(exc),
                    )

            # A manual submission refreshes the source-owned schedule before
            # its immediate run. This lifecycle lock must be acquired before
            # the manager ID lock; scheduled children skip registration,
            # preserving that one-way lock order.
            reservation_id: str | None = None
            try:
                if (
                    request.scheduled_due_at is None
                    and parsed_config is not None
                    and self._recurrence_controller is not None
                ):
                    if parsed_config.schedule is not None:
                        reservation_id = parsed_config.name
                        self._reserve_schedule_lineage(reservation_id)
                        # A lifecycle command may hold the controller's
                        # registration lock while mutating this source. Probe
                        # every currently known source identity before waiting
                        # so manual submission rejects instead of blocking on
                        # the cross-subsystem lock.
                        source_path = request.config_path.resolve(strict=False)
                        known_ids = {
                            parsed_config.name,
                            *(
                                current.schedule_id
                                for current in await self._recurrence_controller.describe()
                                if current.score_path.resolve(strict=False) == source_path
                            ),
                        }
                        _probe_schedule_admission(tuple(sorted(known_ids)))
                    registered_schedule = await self._recurrence_controller.register(
                        request.config_path,
                        parsed_config,
                        before_wait=_probe_schedule_admission,
                        before_mutation=_claim_schedule_admission,
                    )
                    if registered_schedule is None:
                        if reservation_id is not None:
                            self._release_schedule_lineage(reservation_id)
                        reservation_id = None
                    else:
                        if registered_schedule.schedule_id != reservation_id:
                            self._reserve_schedule_lineage(
                                registered_schedule.schedule_id
                            )
                            if reservation_id is not None:
                                self._release_schedule_lineage(reservation_id)
                            reservation_id = registered_schedule.schedule_id
                        request = request.model_copy(
                            update={"schedule_id": registered_schedule.schedule_id},
                        )
            except JobSubmissionError as exc:
                if reservation_id is not None:
                    self._release_schedule_lineage(reservation_id)
                return JobResponse(
                    job_id=job_id,
                    status="rejected",
                    message=str(exc),
                )
            except BaseException:
                if reservation_id is not None:
                    self._release_schedule_lineage(reservation_id)
                raise

            try:
                # Serialize only the duplicate-check → insert window to prevent
                # TOCTOU races between concurrent submissions.
                async with self._id_gen_lock:
                    # Recheck active metadata after recurrence I/O. The
                    # admission reservation prevents another submitter from
                    # reaching this publication window for the same ID.
                    existing = self._job_meta.get(job_id)
                    if existing and existing.status in (
                        DaemonJobStatus.PENDING,
                        DaemonJobStatus.QUEUED,
                        DaemonJobStatus.RUNNING,
                    ):
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
                            and _should_auto_fresh(
                                request.config_path,
                                record.completed_at,
                            )
                        ):
                            request = request.model_copy(update={"fresh": True})
                            _logger.info(
                                "auto_fresh.score_changed",
                                job_id=job_id,
                                config_path=str(request.config_path),
                                message=(
                                    "Score file modified since last completed run — "
                                    "starting fresh"
                                ),
                            )

                    registered_at = self._wall_clock()
                    max_wall_seconds = (
                        parsed_config.max_wall_seconds
                        if parsed_config is not None
                        else None
                    )
                    wall_deadline_at = (
                        registered_at + max_wall_seconds
                        if max_wall_seconds is not None
                        else None
                    )
                    # Register in DB first — if this fails, no phantom in-memory entry
                    log_path = self._ensure_workspace_log_path(workspace)
                    try:
                        committed = await self._registry.register_job(
                            job_id,
                            request.config_path,
                            workspace,
                            log_path=log_path,
                            submitted_at=registered_at,
                            max_wall_seconds=max_wall_seconds,
                            wall_deadline_at=wall_deadline_at,
                        )
                    except FailureHooksInProgressError as exc:
                        return JobResponse(
                            job_id=job_id,
                            status="rejected",
                            message=str(exc),
                        )
                    meta = JobMeta(
                        job_id=committed.job_id,
                        config_path=Path(committed.config_path),
                        workspace=Path(committed.workspace),
                        submitted_at=committed.submitted_at,
                        chain_depth=request.chain_depth,
                        schedule_id=request.schedule_id,
                        max_wall_seconds=committed.max_wall_seconds,
                        wall_deadline_at=committed.wall_deadline_at,
                        deadline_diagnostic=committed.deadline_diagnostic,
                        hook_config=hook_config_list,
                        failure_hook_config=failure_hook_config_list,
                        concert_config=concert_config_dict,
                    )
                    self._job_meta[job_id] = meta
                    self._bind_cleanup_generation(meta, new_execution=True)

                    if concert_config_dict is not None or request.chain_depth is not None:
                        await self._registry.store_concert_context(
                            job_id,
                            (
                                json.dumps(concert_config_dict)
                                if concert_config_dict is not None
                                else None
                            ),
                            request.chain_depth,
                        )

                    # Persist hook config to registry for restart resilience
                    if hook_config_list:
                        await self._registry.store_hook_config(
                            job_id,
                            json.dumps(hook_config_list),
                        )
                    if failure_hook_config_list:
                        await self._registry.store_failure_hook_config(
                            job_id,
                            json.dumps(failure_hook_config_list),
                        )
            finally:
                if reservation_id is not None:
                    self._release_schedule_lineage(reservation_id)

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
                    job_id,
                    DaemonJobStatus.FAILED,
                    error_message="Task creation failed",
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
                message=(
                    f"Job queued (concurrency limit: "
                    f"{self._config.max_concurrent_jobs})"
                ),
            )
        finally:
            for schedule_ids in reversed(schedule_admission_claims):
                self._release_schedule_admission(schedule_ids)
            self._release_job_admission(job_id)

    async def _queue_pending_job(self, request: JobRequest) -> JobResponse:
        """Accept a job as PENDING during rate-limit backpressure.

        Registers the job in the persistent registry and in-memory metadata,
        but does NOT create an execution task.  The job starts automatically
        when rate limits clear (via ``_start_pending_jobs``).

        Returns a ``pending`` JobResponse with rate limit timing info.
        """
        job_id = self._get_job_id(request.job_id or request.config_path.stem)

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
            request.config_path,
            job_id,
        )
        if workspace is not None:
            registered_at = self._wall_clock()
            max_wall_seconds: float | None = None
            try:
                from marianne.core.config import JobConfig

                max_wall_seconds = JobConfig.from_yaml(
                    request.config_path
                ).max_wall_seconds
            except (ValueError, OSError, KeyError, yaml.YAMLError):
                _logger.debug(
                    "manager.pending_deadline_config_unavailable",
                    job_id=job_id,
                    config_path=str(request.config_path),
                )
            wall_deadline_at = (
                registered_at + max_wall_seconds
                if max_wall_seconds is not None
                else None
            )
            log_path = self._ensure_workspace_log_path(workspace)
            try:
                committed = await self._registry.register_job(
                    job_id,
                    request.config_path,
                    workspace,
                    log_path=log_path,
                    submitted_at=registered_at,
                    max_wall_seconds=max_wall_seconds,
                    wall_deadline_at=wall_deadline_at,
                )
            except FailureHooksInProgressError as exc:
                return JobResponse(
                    job_id=job_id,
                    status="rejected",
                    message=str(exc),
                )
            meta = JobMeta(
                job_id=committed.job_id,
                config_path=Path(committed.config_path),
                workspace=Path(committed.workspace),
                submitted_at=committed.submitted_at,
                status=DaemonJobStatus.PENDING,
                schedule_id=request.schedule_id,
                max_wall_seconds=committed.max_wall_seconds,
                wall_deadline_at=committed.wall_deadline_at,
                deadline_diagnostic=committed.deadline_diagnostic,
            )
            self._job_meta[job_id] = meta
            self._bind_cleanup_generation(meta, new_execution=True)
            if request.chain_depth is not None:
                await self._registry.store_concert_context(
                    job_id,
                    None,
                    request.chain_depth,
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
            # Fire-and-forget, but #330: don't swallow a crash in
            # _start_pending_jobs (would leave jobs PENDING forever, silently).
            task.add_done_callback(self._on_autostart_done)

        return JobResponse(
            job_id=job_id,
            status="pending",
            message=f"Rate limit active ({limit_msg}). Score queued — starts when limits clear.",
        )

    @staticmethod
    def _on_autostart_done(task: asyncio.Task[Any]) -> None:
        """Done-callback for the deferred pending-autostart task (#330).

        Fire-and-forget, but a failure in ``_start_pending_jobs`` must be
        surfaced — otherwise jobs stay PENDING forever with nothing in the log.
        Delegates to the shared task-exception logger (consistent with the
        other ``add_done_callback`` sites).
        """
        log_task_exception(task, _logger, "pending_autostart.failed")

    def _resolve_workspace_from_config(
        self,
        config_path: Path,
        job_id: str,
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

            request = self._pending_jobs.get(job_id)
            if request is None:
                continue
            if not self._try_reserve_job_admission(job_id, allow_active=True):
                continue
            schedule_ids = (request.schedule_id,) if request.schedule_id is not None else ()
            schedule_owned = False
            try:
                if schedule_ids:
                    schedule_owned = self._try_reserve_schedule_admission(schedule_ids)
                    if not schedule_owned:
                        continue

                # Ownership is complete before the activation becomes visible.
                # Keep the request pending until the fallible status write has
                # succeeded, then publish the task without another await.
                await self._set_job_status(job_id, DaemonJobStatus.QUEUED)
                self._pending_jobs.pop(job_id, None)
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
                except RuntimeError:
                    self._pending_jobs[job_id] = request
                    await self._set_job_status(job_id, DaemonJobStatus.PENDING)
                    _logger.error(
                        "pending.task_creation_failed",
                        job_id=job_id,
                        exc_info=True,
                    )
                    continue
                self._jobs[job_id] = task

                def _on_pending_done(
                    t: asyncio.Task[Any],
                    *,
                    _jid: str = job_id,
                ) -> None:
                    self._on_task_done(_jid, t)

                task.add_done_callback(_on_pending_done)
            finally:
                if schedule_owned:
                    self._release_schedule_admission(schedule_ids)
                self._release_job_admission(job_id)

    async def get_job_status(self, job_id: str, workspace: Path | None = None) -> dict[str, Any]:
        """Get full status of a specific job.

        Resolution order (no workspace/disk fallback):
        1. Live in-memory state for active jobs
        2. Registry checkpoint (historical jobs — persisted on every save)
        3. Terminal live-state fallback if no registry checkpoint is available
        4. Basic metadata (jobs that never ran / pre-checkpoint registry)
        """
        _ = workspace  # Unused — daemon is the single source of truth

        schedule_record = await self._schedule_for_job(job_id)
        meta = self._job_meta.get(job_id)
        record: JobRecord | None = None
        if meta is None:
            # Check the persistent registry for historical jobs
            record = await self._registry.get_job(job_id)
            if record is None:
                if schedule_record is not None:
                    return self._schedule_anchor_status(schedule_record)
                raise JobSubmissionError(f"Job '{job_id}' not found")

        # 1. Live in-memory state for active jobs. Terminal live states are a
        # cache: direct recovery and registry reconciliation can update the DB
        # without changing this process' old object, so non-active jobs must
        # read the registry before trusting _live_states.
        live = self._live_states.get(job_id)
        if live is not None and meta is not None and meta.status in _ACTIVE_DAEMON_STATUSES:
            live_data = _normalize_cancelled_checkpoint_status(
                live.model_dump(mode="json"),
            )
            return await self._merge_registry_hook_metadata(
                job_id,
                live_data,
            )

        # 2. Registry checkpoint (historical/terminal jobs)
        #    Skip if meta shows an active status — the checkpoint is stale
        #    between resume acceptance and the first new state save.
        if meta is None or meta.status not in _ACTIVE_DAEMON_STATUSES:
            try:
                if record is None:
                    record = await self._registry.get_job(job_id)
                checkpoint_json = await self._registry.load_checkpoint(job_id)
                if checkpoint_json is not None:
                    import json

                    checkpoint_data: dict[str, Any] = json.loads(checkpoint_json)
                    # Override checkpoint status with the registry's
                    # authoritative status. The checkpoint may have been
                    # persisted before a cancel/fail was recorded in the
                    # registry's status column.
                    authoritative_status = (
                        record.status.value
                        if record is not None
                        else (meta.status.value if meta is not None else None)
                    )
                    if (
                        authoritative_status
                        and checkpoint_data.get("status") != authoritative_status
                    ):
                        checkpoint_data["status"] = authoritative_status
                    checkpoint_data = _normalize_cancelled_checkpoint_status(
                        checkpoint_data,
                    )
                    return await self._merge_registry_hook_metadata(
                        job_id,
                        checkpoint_data,
                    )
            except Exception:
                _logger.debug(
                    "get_job_status.registry_checkpoint_failed",
                    job_id=job_id,
                    exc_info=True,
                )

        # 3. Terminal live-state fallback for a known job whose registry has no
        # checkpoint yet.
        if live is not None:
            live_data = _normalize_cancelled_checkpoint_status(
                live.model_dump(mode="json"),
            )
            return await self._merge_registry_hook_metadata(
                job_id,
                live_data,
            )

        # 3b. Detect stale RUNNING status (no live state + no running task).
        #     This happens when meta was restored from the registry after a
        #     daemon restart but the job's process no longer exists.
        if meta is not None and meta.status == DaemonJobStatus.RUNNING:
            task = self._jobs.get(job_id)
            if task is None or task.done():
                _logger.info(
                    "get_job_status.stale_running_corrected",
                    job_id=job_id,
                )
                await self._set_job_status(
                    job_id,
                    DaemonJobStatus.FAILED,
                )
                # Now fall through to return the corrected checkpoint
                # or metadata below.
                try:
                    checkpoint_json = await self._registry.load_checkpoint(
                        job_id,
                    )
                    if checkpoint_json is not None:
                        import json as _json

                        failed_data = _json.loads(checkpoint_json)
                        # Override the checkpoint's stale status
                        failed_data["status"] = "failed"
                        failed_data = _normalize_cancelled_checkpoint_status(failed_data)
                        return await self._merge_registry_hook_metadata(
                            job_id,
                            failed_data,
                        )
                except Exception:
                    _logger.debug(
                        "get_job_status.checkpoint_load_after_fail",
                        job_id=job_id,
                        exc_info=True,
                    )

        # 4. Basic metadata (job never produced a checkpoint, or active job
        #    whose registry checkpoint is stale)
        if meta is not None:
            return await self._merge_registry_hook_metadata(job_id, meta.to_dict())
        assert record is not None  # guaranteed by the check above
        return await self._merge_registry_hook_metadata(job_id, record.to_dict())

    async def _merge_registry_hook_metadata(
        self,
        job_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge hook config/results columns into status checkpoint data.

        Hook execution results are stored in dedicated registry columns after
        the terminal checkpoint has usually already been persisted. Merging them
        here keeps status, diagnose, dashboard, and API clients aligned with the
        registry's complete job record.
        """
        try:
            hook_results_json = await self._registry.get_hook_results(job_id)
            if hook_results_json and not data.get("hook_results"):
                import json

                parsed_results = json.loads(hook_results_json)
                if isinstance(parsed_results, list):
                    data["hook_results"] = parsed_results
        except (OSError, ValueError, TypeError):
            _logger.debug(
                "get_job_status.hook_results_merge_failed",
                job_id=job_id,
                exc_info=True,
            )

        parsed_config: list[dict[str, Any]] | None = None
        try:
            hook_config_json = await self._registry.get_hook_config(job_id)
            if hook_config_json:
                import json

                raw_config = json.loads(hook_config_json)
                if isinstance(raw_config, list):
                    parsed_config = raw_config
        except (OSError, ValueError, TypeError):
            _logger.debug(
                "get_job_status.hook_config_merge_failed",
                job_id=job_id,
                exc_info=True,
            )

        if parsed_config is None:
            meta = self._job_meta.get(job_id)
            if meta is not None and meta.hook_config:
                parsed_config = meta.hook_config

        if parsed_config:
            snapshot = data.get("config_snapshot")
            if not isinstance(snapshot, dict):
                snapshot = {}
            if not snapshot.get("on_success"):
                snapshot = dict(snapshot)
                snapshot["on_success"] = parsed_config
                data["config_snapshot"] = snapshot

        failure_config: list[dict[str, Any]] | None = None
        try:
            failure_config_json = await self._registry.get_failure_hook_config(job_id)
            if failure_config_json:
                import json

                raw_failure_config = json.loads(failure_config_json)
                if isinstance(raw_failure_config, list):
                    failure_config = raw_failure_config
        except (OSError, ValueError, TypeError):
            _logger.debug(
                "get_job_status.failure_hook_config_merge_failed",
                job_id=job_id,
                exc_info=True,
            )

        if failure_config is None:
            meta = self._job_meta.get(job_id)
            if meta is not None and meta.failure_hook_config:
                failure_config = meta.failure_hook_config

        if failure_config:
            snapshot = data.get("config_snapshot")
            if not isinstance(snapshot, dict):
                snapshot = {}
            if not snapshot.get("on_failure"):
                snapshot = dict(snapshot)
                snapshot["on_failure"] = failure_config
                data["config_snapshot"] = snapshot

        try:
            failure_results_json = await self._registry.get_failure_hook_results(job_id)
            if failure_results_json:
                import json

                failure_results = json.loads(failure_results_json)
                if isinstance(failure_results, list):
                    data["failure_hook_results"] = failure_results
        except (OSError, ValueError, TypeError):
            _logger.debug(
                "get_job_status.failure_hook_results_merge_failed",
                job_id=job_id,
                exc_info=True,
            )

        record = await self._registry.get_job(job_id)
        if record is not None and (
            record.failure_hooks_started_at is not None
            or record.failure_hooks_completed_at is not None
        ):
            data["failure_hook_state"] = {
                "started_at": record.failure_hooks_started_at,
                "completed_at": record.failure_hooks_completed_at,
            }

        data = await self._merge_schedule_status(job_id, data)
        return await self._merge_deadline_status(job_id, data)

    async def _merge_deadline_status(
        self,
        job_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach deadline authority and current remaining time when relevant."""
        meta = self._job_meta.get(job_id)
        if meta is None:
            record = await self._registry.get_job(job_id)
            if record is None:
                return data
            submitted_at = record.submitted_at
            max_wall_seconds = record.max_wall_seconds
            wall_deadline_at = record.wall_deadline_at
            terminal_reason = record.terminal_reason
            cleanup_outcome = None
            diagnostic = record.deadline_diagnostic
        else:
            submitted_at = meta.submitted_at
            max_wall_seconds = meta.max_wall_seconds
            wall_deadline_at = meta.wall_deadline_at
            terminal_reason = meta.terminal_reason
            cleanup_outcome = meta.timeout_cleanup_outcome
            if cleanup_outcome is not None:
                cleanup_outcome = self._refresh_timeout_cleanup_outcome(
                    job_id,
                    cleanup_outcome,
                )
                meta.timeout_cleanup_outcome = cleanup_outcome
            diagnostic = meta.deadline_diagnostic

        now_epoch = self._wall_clock()
        daemon_limit = float(self._config.job_timeout_seconds)
        score_remaining = (
            max(0.0, wall_deadline_at - now_epoch)
            if wall_deadline_at is not None
            else None
        )
        effective_remaining = max(
            0.0,
            min(daemon_limit, score_remaining)
            if score_remaining is not None
            else daemon_limit,
        )
        elapsed = max(0.0, now_epoch - submitted_at)
        projection = JobDeadlineStatus(
            daemon_limit_seconds=daemon_limit,
            score_limit_seconds=max_wall_seconds,
            effective_remaining_seconds=effective_remaining,
            elapsed_seconds=elapsed,
            wall_deadline_at=wall_deadline_at,
            terminal_reason=terminal_reason,
            cleanup_outcome=cleanup_outcome,
            diagnostic=diagnostic,
        )
        if max_wall_seconds is not None:
            data["max_wall_seconds"] = max_wall_seconds
        if wall_deadline_at is not None:
            data["wall_deadline_at"] = wall_deadline_at
        if terminal_reason is not None:
            data["terminal_reason"] = terminal_reason
        data["deadline"] = projection.model_dump(mode="json", exclude_none=True)
        return data

    async def _schedule_for_job(
        self,
        job_id: str,
        *,
        config_path: Path | None = None,
        records: Sequence[ScheduleRecord] | None = None,
        index: _ScheduleIndex | None = None,
    ) -> ScheduleRecord | None:
        """Resolve a job or schedule ID to its durable recurrence projection."""
        controller = getattr(self, "_recurrence_controller", None)
        if controller is None:
            return None

        if index is None:
            schedule_records = (
                list(records) if records is not None else await controller.describe()
            )
            index = self._build_schedule_index(schedule_records)

        meta = self._job_meta.get(job_id)
        if meta is not None and meta.schedule_id is not None:
            matched = index.by_id.get(meta.schedule_id)
            if matched is not None:
                return matched
        matched = index.by_id.get(job_id)
        if matched is not None:
            return matched

        source_path = config_path or (meta.config_path if meta is not None else None)
        if source_path is not None:
            matched = index.by_path.get(source_path.resolve(strict=False))
            if matched is not None:
                return matched
        return index.by_stem.get(job_id)

    @staticmethod
    def _build_schedule_index(records: Sequence[ScheduleRecord]) -> _ScheduleIndex:
        """Build stable ID/path indexes once for a status/list operation."""
        by_id = {record.schedule_id: record for record in records}
        by_path = {
            record.score_path.resolve(strict=False): record for record in records
        }
        stem_groups: dict[str, list[ScheduleRecord]] = {}
        for record in records:
            stem_groups.setdefault(record.score_path.stem, []).append(record)
        by_stem = {
            stem: matches[0]
            for stem, matches in stem_groups.items()
            if len(matches) == 1
        }
        return _ScheduleIndex(tuple(records), by_id, by_path, by_stem)

    @staticmethod
    def _schedule_status(record: ScheduleRecord) -> dict[str, Any]:
        """Return the exact additive public recurrence projection."""
        diagnostic = getattr(record, "diagnostic", None)
        status = ScheduleStatus(
            enabled=record.enabled,
            next_due_at=None if diagnostic is not None else record.next_due_at,
            last_due_at=record.last_due_at,
            last_run_id=record.last_run_id,
            last_outcome=record.last_outcome,
            consecutive_drops=record.consecutive_drops,
            diagnostic=diagnostic,
        ).model_dump(mode="json")
        if diagnostic is None:
            status.pop("diagnostic")
        return status

    @classmethod
    def _schedule_anchor_status(cls, record: ScheduleRecord) -> dict[str, Any]:
        """Synthesize a truthful stable anchor when no job row exists."""
        return {
            "job_id": record.schedule_id,
            "status": "scheduled" if record.enabled else "paused",
            "config_path": str(record.score_path),
            "submitted_at": record.created_at,
            "started_at": None,
            "pid": None,
            "completed_at": None,
            "current_sheet": None,
            "total_sheets": None,
            "schedule": cls._schedule_status(record),
        }

    async def _merge_schedule_status(
        self,
        job_id: str,
        data: dict[str, Any],
        *,
        records: Sequence[ScheduleRecord] | None = None,
        index: _ScheduleIndex | None = None,
    ) -> dict[str, Any]:
        """Add recurrence status only when this job has a durable schedule."""
        raw_config_path = data.get("config_path")
        config_path = Path(raw_config_path) if isinstance(raw_config_path, str) else None
        record = await self._schedule_for_job(
            job_id,
            config_path=config_path,
            records=records,
            index=index,
        )
        if record is None:
            return data
        result = dict(data)
        result["schedule"] = self._schedule_status(record)
        return result

    def _schedule_related_meta(self, record: ScheduleRecord) -> list[JobMeta]:
        """Return every in-memory job whose persisted identity is this schedule."""
        schedule_path = record.score_path.resolve(strict=False)
        related: list[JobMeta] = []
        for meta in self._job_meta.values():
            if (
                meta.schedule_id == record.schedule_id
                or meta.job_id == record.schedule_id
                or meta.config_path.resolve(strict=False) == schedule_path
            ):
                related.append(meta)
        return related

    @staticmethod
    def _is_stable_schedule_anchor(job_id: str, record: ScheduleRecord) -> bool:
        """Identify an anchor from durable identity, never child-ID syntax."""
        return job_id in {record.schedule_id, record.score_path.stem}

    def _claim_lifecycle_admission(
        self,
        claims: list[tuple[str, ...]],
        schedule_ids: tuple[str, ...],
    ) -> None:
        """Claim lifecycle ownership from a controller pre-mutation callback."""
        if not self._try_reserve_schedule_admission(schedule_ids):
            joined = ", ".join(repr(value) for value in schedule_ids)
            raise JobSubmissionError(
                f"Schedule lifecycle for {joined} is already active"
            )
        claims.append(schedule_ids)

    def _release_lifecycle_admission(
        self,
        claims: list[tuple[str, ...]],
    ) -> None:
        """Release all schedule claims acquired during one manager operation."""
        for schedule_ids in reversed(claims):
            self._release_schedule_admission(schedule_ids)

    def _claim_related_job_admissions(
        self,
        record: ScheduleRecord,
        owned_job_ids: list[str],
    ) -> None:
        """Atomically own every active child before a lifecycle mutation."""
        cancellable = {
            DaemonJobStatus.PENDING,
            DaemonJobStatus.QUEUED,
            DaemonJobStatus.RUNNING,
            DaemonJobStatus.PAUSED,
            DaemonJobStatus.PAUSED_AT_CHAIN,
        }
        acquired: list[str] = []
        for related in sorted(
            self._schedule_related_meta(record),
            key=lambda meta: meta.job_id,
        ):
            if related.status not in cancellable:
                continue
            if not self._try_reserve_job_admission(
                related.job_id,
                allow_active=True,
            ):
                for job_id in reversed(acquired):
                    self._release_job_admission(job_id)
                raise JobSubmissionError(
                    f"Score '{related.job_id}' is already being submitted"
                )
            acquired.append(related.job_id)
        owned_job_ids.extend(acquired)

    async def _pause_related_job(
        self,
        meta: JobMeta,
        *,
        defer_baton_event: bool = False,
    ) -> None:
        """Make one scheduled child non-dispatchable and visibly paused."""
        if meta.status is DaemonJobStatus.PENDING:
            await self._set_job_status(meta.job_id, DaemonJobStatus.PAUSED)
            self._pending_jobs.pop(meta.job_id, None)
            return
        if meta.status is DaemonJobStatus.QUEUED:
            task = self._jobs.pop(meta.job_id, None)
            if task is not None and not task.done():
                task.cancel(msg=f"schedule pause requested for {meta.job_id}")
                try:
                    await task
                except asyncio.CancelledError:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
                except Exception:
                    pass
            await self._set_job_status(meta.job_id, DaemonJobStatus.PAUSED)
            return
        if meta.status is DaemonJobStatus.RUNNING:
            await self._pause_active_job(
                meta.job_id,
                defer_baton_event=defer_baton_event,
            )

    async def _re_pause_resumed_jobs(self, resumed: Sequence[JobMeta]) -> None:
        """Best-effort rollback for a partially resumed schedule lifecycle."""
        first_error: Exception | None = None
        for meta in reversed(resumed):
            try:
                await self._pause_related_job(meta)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    async def pause_job(self, job_id: str) -> bool:
        """Pause recurring ticks and any currently running work."""
        record = await JobManager._schedule_for_job(self, job_id)
        if record is None:
            return await self._pause_active_job(job_id)

        controller = getattr(self, "_recurrence_controller", None)
        assert controller is not None
        claims: list[tuple[str, ...]] = []
        authority: ScheduleRecord | None = None
        pause_mutation_entered = False
        owned_job_ids: list[str] = []
        changed_events: list[asyncio.Event] = []
        created_signals: list[Path] = []
        changed_baton: list[tuple[JobMeta, bool, bool, int | None]] = []
        staged_children: list[tuple[JobMeta, DaemonJobStatus]] = []
        pause_commit_started = False

        def claim(schedule_ids: tuple[str, ...]) -> None:
            self._claim_lifecycle_admission(claims, schedule_ids)

        def capture_authority(current: ScheduleRecord) -> None:
            nonlocal authority
            self._claim_related_job_admissions(current, owned_job_ids)
            authority = current

        def mark_pause_mutation(_current: ScheduleRecord) -> None:
            nonlocal pause_mutation_entered
            pause_mutation_entered = True

        try:
            current = await controller.pause(
                record.schedule_id,
                score_path=record.score_path,
                before_mutation=claim,
                on_authority=capture_authority,
                on_mutation=mark_pause_mutation,
            )
            related = self._schedule_related_meta(current)

            def pause_order(meta: JobMeta) -> int:
                if meta.status is DaemonJobStatus.RUNNING:
                    baton_has_job = (
                        self._baton_adapter is not None
                        and self._baton_adapter.has_job(meta.job_id)
                    )
                    # Fallible filesystem pauses precede local signals. A
                    # later failure therefore cannot strand an earlier local
                    # dispatch gate in the paused state.
                    return 1 if baton_has_job or meta.job_id in self._pause_events else 0
                if meta.status is DaemonJobStatus.QUEUED:
                    return 2
                if meta.status is DaemonJobStatus.PENDING:
                    return 3
                return 4

            for meta in sorted(related, key=pause_order):
                if meta.status in {
                    DaemonJobStatus.PENDING,
                    DaemonJobStatus.QUEUED,
                }:
                    prior_status = meta.status
                    staged_children.append((meta, prior_status))
                    await self._set_job_status(meta.job_id, DaemonJobStatus.PAUSED)
                    continue
                event = self._pause_events.get(meta.job_id)
                event_was_set = event.is_set() if event is not None else False
                signal_path = meta.workspace / f".marianne-pause-{meta.job_id}"
                signal_existed = signal_path.exists()
                baton_job = (
                    self._baton_adapter._baton._jobs.get(meta.job_id)
                    if self._baton_adapter is not None
                    and meta.status is DaemonJobStatus.RUNNING
                    else None
                )
                baton_prior = (
                    (
                        baton_job.paused,
                        baton_job.user_paused,
                        baton_job.event_generation,
                    )
                    if baton_job is not None
                    else None
                )
                await self._pause_related_job(
                    meta,
                    defer_baton_event=baton_prior is not None,
                )
                if baton_prior is not None:
                    changed_baton.append((meta, *baton_prior))
                if event is not None and not event_was_set and event.is_set():
                    changed_events.append(event)
                if not signal_existed and signal_path.exists():
                    created_signals.append(signal_path)

            # Every fallible child status transition has succeeded. Commit
            # pending removal and queued cancellation only now, so an earlier
            # child can always be restored without recreating detached work.
            pause_commit_started = True
            queued_tasks: list[asyncio.Task[Any]] = []
            for meta, prior_status in staged_children:
                if prior_status is DaemonJobStatus.PENDING:
                    self._pending_jobs.pop(meta.job_id, None)
                    continue
                task = self._jobs.pop(meta.job_id, None)
                if task is not None and not task.done():
                    task.cancel(msg=f"schedule pause requested for {meta.job_id}")
                    queued_tasks.append(task)
            if queued_tasks:

                async def settle_queued_tasks() -> None:
                    await asyncio.gather(*queued_tasks, return_exceptions=True)

                settlement = asyncio.create_task(
                    settle_queued_tasks(),
                    name=f"schedule-pause-settle-{current.schedule_id}",
                )
                try:
                    await asyncio.shield(settlement)
                except asyncio.CancelledError:
                    # Cancellation remains loud, but every already-cancelled
                    # queued task settles before lifecycle ownership is lost.
                    await settlement
                    raise
            if changed_baton:
                from marianne.daemon.baton.events import PauseJob

                adapter = self._baton_adapter
                assert adapter is not None
                for meta, _paused, _user_paused, event_generation in changed_baton:
                    adapter._baton.inbox.put_nowait(
                        PauseJob(
                            job_id=meta.job_id,
                            event_generation=event_generation,
                        )
                    )
            return True
        except BaseException as exc:
            if pause_commit_started:
                # The pause is fully committed and all queued tasks have been
                # joined. Propagate cancellation/failure loudly without
                # pretending the completed lifecycle transition was undone.
                raise
            child_rollback_error: BaseException | None = None
            for meta, prior_status in reversed(staged_children):
                try:
                    await self._set_job_status(meta.job_id, prior_status)
                except BaseException as rollback_exc:
                    if child_rollback_error is None:
                        child_rollback_error = rollback_exc
            baton_rollback_error: BaseException | None = None
            for meta, paused, user_paused, _generation in reversed(changed_baton):
                baton_job = (
                    self._baton_adapter._baton._jobs.get(meta.job_id)
                    if self._baton_adapter is not None
                    else None
                )
                if baton_job is not None:
                    baton_job.paused = paused
                    baton_job.user_paused = user_paused
                    adapter = self._baton_adapter
                    assert adapter is not None
                    adapter._baton._state_dirty = True
                try:
                    await self._set_job_status(meta.job_id, DaemonJobStatus.RUNNING)
                except BaseException as rollback_exc:
                    if baton_rollback_error is None:
                        baton_rollback_error = rollback_exc
            for event in changed_events:
                event.clear()
            for signal_path in created_signals:
                try:
                    signal_path.unlink(missing_ok=True)
                except OSError:
                    _logger.error(
                        "schedule.pause_signal_rollback_failed",
                        path=str(signal_path),
                        exc_info=True,
                    )
            if (
                pause_mutation_entered
                and authority is not None
                and authority.enabled
                and claims
            ):
                try:
                    await controller.resume(
                        authority.schedule_id,
                        score_path=authority.score_path,
                        before_mutation=claim,
                    )
                except BaseException as rollback_exc:
                    raise RuntimeError(
                        f"Failed to pause active job {job_id!r}; recurrence rollback "
                        f"also failed: {rollback_exc}"
                    ) from exc
            if baton_rollback_error is not None:
                raise RuntimeError(
                    f"Failed to pause active job {job_id!r}; baton rollback "
                    f"also failed: {baton_rollback_error}"
                ) from exc
            if child_rollback_error is not None:
                raise RuntimeError(
                    f"Failed to pause active job {job_id!r}; child rollback "
                    f"also failed: {child_rollback_error}"
                ) from exc
            raise
        finally:
            self._release_lifecycle_admission(claims)
            for owned_job_id in reversed(owned_job_ids):
                self._release_job_admission(owned_job_id)

    async def _pause_active_job(
        self,
        job_id: str,
        *,
        defer_baton_event: bool = False,
    ) -> bool:
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
            raise JobSubmissionError(f"Job '{job_id}' is {meta.status.value}, not running")

        # Baton path FIRST (#162): it works for auto-recovered jobs that have no
        # manager `_jobs` wrapper task — a conductor restart re-runs them in the
        # baton event loop. `request_pause` returns True iff the baton actually
        # has the job (the real "is it running" signal for baton jobs) and closes
        # the dispatch gate SYNCHRONOUSLY (#184) so a sheet completion queued
        # ahead of the pause can't dispatch the next sheet; it is a safe no-op
        # (returns False, no side effect) when the baton doesn't have the job.
        # Trying this before the wrapper-task check below stops a stale-status
        # guard from destructively marking a still-running recovered job FAILED.
        baton_job = (
            self._baton_adapter._baton._jobs.get(job_id)
            if self._baton_adapter is not None
            else None
        )
        baton_prior = (
            (baton_job.paused, baton_job.user_paused, baton_job.event_generation)
            if baton_job is not None
            else None
        )
        if self._baton_adapter is not None and self._baton_adapter._baton.request_pause(job_id):
            from marianne.daemon.baton.events import PauseJob

            try:
                await self._set_job_status(job_id, DaemonJobStatus.PAUSED)
            except BaseException:
                if baton_job is not None and baton_prior is not None:
                    baton_job.paused, baton_job.user_paused, _generation = baton_prior
                    self._baton_adapter._baton._state_dirty = True
                raise
            if not defer_baton_event:
                self._baton_adapter._baton.inbox.put_nowait(
                    PauseJob(
                        job_id=job_id,
                        event_generation=(
                            baton_job.event_generation if baton_job is not None else None
                        ),
                    )
                )
            _logger.info("job.baton_pause_sent", job_id=job_id)
            return True

        # Non-baton / not-in-baton: verify an actual running task. A job the baton
        # doesn't have AND with no wrapper task is genuinely stale ("running"
        # status restored from the registry after a restart with no live work).
        task = self._jobs.get(job_id)
        if task is None or task.done():
            await self._set_job_status(job_id, DaemonJobStatus.FAILED)
            raise JobSubmissionError(
                f"Job '{job_id}' has no running process (stale status after daemon restart)"
            )

        # Prefer in-process event (no filesystem access needed)
        event = self._pause_events.get(job_id)
        if event is not None:
            event.set()
            _logger.info("job.pause_event_set", job_id=job_id)
            return True

        # Fallback: filesystem-based pause via JobService
        _logger.info("job.pause_filesystem_fallback", job_id=job_id)
        return await self._checked_service.pause_job(meta.job_id, meta.workspace)

    async def resume_job(
        self,
        job_id: str,
        workspace: Path | None = None,
        config_path: Path | None = None,
        no_reload: bool = False,
        from_sheet: int | None = None,
        escalation: bool = False,
        self_healing: bool = False,
    ) -> JobResponse:
        """Enable recurring ticks and resume active work when it is paused."""
        record = await JobManager._schedule_for_job(self, job_id)
        if record is None:
            return await JobManager._resume_active_job(
                self,
                job_id,
                workspace=workspace,
                config_path=config_path,
                no_reload=no_reload,
                from_sheet=from_sheet,
                escalation=escalation,
                self_healing=self_healing,
            )

        controller = getattr(self, "_recurrence_controller", None)
        assert controller is not None
        schedule_id = record.schedule_id
        active_statuses = {
            DaemonJobStatus.PENDING,
            DaemonJobStatus.QUEUED,
            DaemonJobStatus.RUNNING,
        }
        schedule_resumable_statuses = {
            DaemonJobStatus.PAUSED,
            DaemonJobStatus.PAUSED_AT_CHAIN,
        }
        related = self._schedule_related_meta(record)
        resumable = [
            meta for meta in related if meta.status in schedule_resumable_statuses
        ]
        held_chains = [
            meta
            for meta in resumable
            if meta.status is DaemonJobStatus.PAUSED_AT_CHAIN
            and meta.held_chain_hook is not None
        ]
        if len(held_chains) > 1:
            return JobResponse(
                job_id=job_id,
                status="rejected",
                message=(
                    "Schedule has multiple held chains; resume each child "
                    "explicitly to preserve chain ordering"
                ),
            )
        # A held chain submission is irreversible once accepted. Run ordinary
        # resumptions first and the sole held chain last, so no later child can
        # fail after its chained job has been published.
        resumable.sort(
            key=lambda meta: meta.status is DaemonJobStatus.PAUSED_AT_CHAIN
        )
        requested_meta = self._job_meta.get(job_id)
        if (
            requested_meta is not None
            and requested_meta in related
            and requested_meta.status
            in {DaemonJobStatus.FAILED, DaemonJobStatus.CANCELLED}
        ):
            resumable.append(requested_meta)
        active = [meta for meta in related if meta.status in active_statuses]
        admission_ids = sorted({meta.job_id for meta in resumable})
        if not admission_ids and not active:
            admission_ids = [job_id]
        owned_job_ids: list[str] = []
        for admission_id in admission_ids:
            if not self._try_reserve_job_admission(admission_id):
                for owned_job_id in reversed(owned_job_ids):
                    self._release_job_admission(owned_job_id)
                raise JobSubmissionError(
                    f"Score '{admission_id}' is already being submitted"
                )
            owned_job_ids.append(admission_id)

        claims: list[tuple[str, ...]] = []
        authority: ScheduleRecord | None = None
        mutation_entered = False
        lineage_ids: list[str] = []
        resumed_meta: list[JobMeta] = []

        def claim(schedule_ids: tuple[str, ...]) -> None:
            self._claim_lifecycle_admission(claims, schedule_ids)

        def capture_authority(current: ScheduleRecord) -> None:
            nonlocal authority, mutation_entered
            authority = current
            mutation_entered = True
            self._reserve_schedule_lineage(current.schedule_id)
            lineage_ids.append(current.schedule_id)

        async def compensate(original_exc: BaseException) -> None:
            """Restore active work and recurrence before surfacing a failed resume."""
            active_rollback_error: BaseException | None = None
            deferred_cancellation: asyncio.CancelledError | None = None
            if resumed_meta:
                rollback_task = asyncio.create_task(
                    self._re_pause_resumed_jobs(resumed_meta),
                    name=(
                        "schedule-resume-rollback-"
                        f"{authority.schedule_id if authority is not None else schedule_id}"
                    ),
                )
                try:
                    await asyncio.shield(rollback_task)
                except asyncio.CancelledError as cancel_exc:
                    deferred_cancellation = cancel_exc
                    try:
                        await rollback_task
                    except BaseException as rollback_exc:
                        active_rollback_error = rollback_exc
                except BaseException as rollback_exc:
                    active_rollback_error = rollback_exc

            recurrence_rollback_error: BaseException | None = None
            if mutation_entered and authority is not None and not authority.enabled:
                try:
                    await controller.pause(
                        authority.schedule_id,
                        score_path=authority.score_path,
                        before_mutation=claim,
                    )
                except BaseException as rollback_exc:
                    recurrence_rollback_error = rollback_exc

            if (
                active_rollback_error is not None
                or recurrence_rollback_error is not None
            ):
                details: list[str] = []
                if active_rollback_error is not None:
                    details.append(f"active rollback failed: {active_rollback_error}")
                if recurrence_rollback_error is not None:
                    details.append(
                        f"recurrence rollback failed: {recurrence_rollback_error}"
                    )
                raise RuntimeError(
                    f"Failed to resume active job {job_id!r}; " + "; ".join(details)
                ) from original_exc
            if deferred_cancellation is not None:
                raise deferred_cancellation

        try:
            try:
                await controller.resume(
                    schedule_id,
                    score_path=record.score_path,
                    before_mutation=claim,
                    on_authority=capture_authority,
                )
                if not resumable:
                    return JobResponse(
                        job_id=job_id,
                        status="accepted",
                        message="Recurring schedule resumed",
                    )
                response: JobResponse | None = None
                for resumable_meta in resumable:
                    is_requested = resumable_meta.job_id == job_id
                    response = await JobManager._resume_active_job(
                        self,
                        resumable_meta.job_id,
                        workspace=workspace if is_requested else None,
                        config_path=config_path if is_requested else None,
                        no_reload=no_reload,
                        from_sheet=from_sheet if is_requested else None,
                        escalation=escalation if is_requested else False,
                        self_healing=self_healing if is_requested else False,
                    )
                    if response.status != "accepted":
                        await compensate(
                            JobSubmissionError(
                                response.message
                                or f"Resume of {resumable_meta.job_id!r} was rejected"
                            )
                        )
                        return response
                    resumed_meta.append(resumable_meta)
                assert response is not None
                return response
            except BaseException as exc:
                await compensate(exc)
                raise
            finally:
                for lineage_id in reversed(lineage_ids):
                    self._release_schedule_lineage(lineage_id)
        finally:
            self._release_lifecycle_admission(claims)
            for owned_job_id in reversed(owned_job_ids):
                self._release_job_admission(owned_job_id)

    async def _resume_active_job(
        self,
        job_id: str,
        workspace: Path | None = None,
        config_path: Path | None = None,
        no_reload: bool = False,
        from_sheet: int | None = None,
        escalation: bool = False,
        self_healing: bool = False,
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
        _resumable = (
            DaemonJobStatus.PAUSED,
            DaemonJobStatus.PAUSED_AT_CHAIN,
            DaemonJobStatus.FAILED,
            DaemonJobStatus.CANCELLED,
        )
        if meta.status not in _resumable:
            raise JobSubmissionError(
                f"Score '{job_id}' is {meta.status.value}, "
                "only PAUSED, PAUSED_AT_CHAIN, FAILED, or CANCELLED scores can be resumed"
            )

        # Own this terminal -> active transition before any awaited resume I/O.
        # Scheduled submission acquires the same synchronous reservation before
        # recurrence publication, preserving recurrence lifecycle -> manager
        # admission as the only cross-subsystem lock direction.
        if not self._try_reserve_job_admission(job_id):
            raise JobSubmissionError(f"Score '{job_id}' is already being submitted")

        try:
            # Capture the pre-resume status NOW, before the QUEUED/RUNNING
            # transitions below overwrite it. The resume task's intrinsic recovery
            # (#185) keys off this to decide whether to reset failed/cascade-skipped
            # sheets (FAILED/CANCELLED) or preserve terminal sheets (PAUSED).
            pre_resume_status = meta.status

            # PAUSED_AT_CHAIN: trigger the held chain instead of normal resume.
            # Keep admission ownership through every submission and rollback path.
            if meta.status == DaemonJobStatus.PAUSED_AT_CHAIN and meta.held_chain_hook:
                return await self._resume_held_chain(job_id, meta)

            # Cancel stale task and WAIT for it to finish before creating the
            # new resume task. Without the await, the old task's CancelledError
            # handler races with the new task's recover_job() and can deregister
            # the freshly-recovered baton state.
            old_task = self._jobs.pop(job_id, None)
            if old_task is not None and not old_task.done():
                old_task.cancel(msg=f"stale task replaced by resume of {job_id}")
                _logger.info("job.resume_cancelled_stale_task", job_id=job_id)
                try:
                    await old_task
                except asyncio.CancelledError:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
                    # Expected: the stale child completed its requested
                    # cancellation while the resume caller remains live.
                except Exception:
                    pass  # Expected — the stale task may already have errored

            # Apply new config path before creating the task (task reads
            # meta.config_path).
            if config_path is not None:
                meta.config_path = config_path

            ws = workspace or meta.workspace
            await self._set_job_status(job_id, DaemonJobStatus.QUEUED)

            task = asyncio.create_task(
                self._resume_job_task(
                    job_id,
                    ws,
                    no_reload=no_reload,
                    pre_resume_status=pre_resume_status,
                    from_sheet=from_sheet,
                    escalation=escalation,
                    self_healing=self_healing,
                ),
                name=f"job-resume-{job_id}",
            )
            self._jobs[job_id] = task
            task.add_done_callback(lambda t: self._on_task_done(job_id, t))

            return JobResponse(
                job_id=job_id,
                status="accepted",
                message="Job resume queued",
            )
        finally:
            self._release_job_admission(job_id)

    async def _resume_held_chain(
        self,
        job_id: str,
        meta: JobMeta,
    ) -> JobResponse:
        """Submit the held chain job after a pause_before_chain intervention.

        When a job is PAUSED_AT_CHAIN, it has a held_chain_hook with the
        fully-resolved chain parameters. Resuming triggers the chained
        job submission and transitions the parent to COMPLETED.
        """
        hook = meta.held_chain_hook
        if hook is None:
            raise JobSubmissionError(
                f"Score '{job_id}' is PAUSED_AT_CHAIN but has no held chain hook"
            )

        job_path = Path(hook["job_path"])
        chained_workspace = Path(hook["workspace"]) if hook.get("workspace") else None
        fresh = hook.get("fresh", False)
        chain_depth = hook.get("chain_depth", 1)

        # Clear the held hook before submitting
        meta.held_chain_hook = None

        # Transition parent back to COMPLETED before submitting chain
        await self._set_job_status(job_id, DaemonJobStatus.COMPLETED)

        request = JobRequest(
            config_path=job_path,
            workspace=chained_workspace,
            fresh=fresh,
            chain_depth=chain_depth,
        )

        response = await self.submit_job(request)
        if response.status == "accepted":
            _logger.info(
                "hook.chain_resumed",
                parent_job_id=job_id,
                chained_job_id=response.job_id,
            )
            return JobResponse(
                job_id=job_id,
                status="accepted",
                message=f"Chain resumed — chained job {response.job_id} submitted",
            )

        _logger.warning(
            "hook.chain_resume_failed",
            parent_job_id=job_id,
            rejection=response.message,
        )
        # Restore paused state so the user can retry
        meta.held_chain_hook = hook
        await self._set_job_status(job_id, DaemonJobStatus.PAUSED_AT_CHAIN)
        return JobResponse(
            job_id=job_id,
            status="rejected",
            message=f"Chain submission failed: {response.message}",
        )

    async def modify_job(
        self,
        job_id: str,
        config_path: Path,
        workspace: Path | None = None,
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
            raise JobSubmissionError(f"Job '{job_id}' is {meta.status.value}, cannot modify")

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

    async def cancel_job(self, job_id: str, *, source: str = "unknown") -> bool:
        """Cancel active work and remove its recurrence, when present."""
        record = await JobManager._schedule_for_job(self, job_id)
        if record is None:
            if not self._try_reserve_job_admission(job_id, allow_active=True):
                raise JobSubmissionError(
                    f"Score '{job_id}' is already being submitted"
                )
            try:
                return await self._cancel_active_job(job_id, source=source)
            finally:
                self._release_job_admission(job_id)

        controller = getattr(self, "_recurrence_controller", None)
        assert controller is not None
        claims: list[tuple[str, ...]] = []
        owned_job_ids: list[str] = []
        removal_entered = False

        def claim(schedule_ids: tuple[str, ...]) -> None:
            self._claim_lifecycle_admission(claims, schedule_ids)

        def capture_authority(current: ScheduleRecord) -> None:
            nonlocal removal_entered
            self._claim_related_job_admissions(current, owned_job_ids)
            removal_entered = True

        try:
            removal_error: BaseException | None = None
            try:
                record = await controller.remove(
                    record.schedule_id,
                    score_path=record.score_path,
                    before_mutation=claim,
                    on_authority=capture_authority,
                )
            except BaseException as exc:
                if not removal_entered:
                    raise
                # Removal failure must never leave future autonomous work enabled.
                # A durable pause is the safe degraded state and the original
                # removal error remains loud to the caller.
                try:
                    await controller.pause(
                        record.schedule_id,
                        score_path=record.score_path,
                        before_mutation=claim,
                    )
                except BaseException as safety_exc:
                    removal_error = RuntimeError(
                        f"Failed to remove recurrence for {job_id!r}; safety pause "
                        f"also failed: {safety_exc}"
                    )
                    removal_error.__cause__ = exc
                else:
                    removal_error = exc

            cleanup = asyncio.create_task(
                self._cancel_schedule_related(record, source=source),
                name=f"schedule-cancel-{record.schedule_id}",
            )
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # Command cancellation may cancel the waiter, never the safety
                # work: recurrence is already removed/paused and every child
                # must reach CANCELLED before schedule admission is released.
                await cleanup
                raise
            if removal_error is not None:
                raise removal_error
            return True
        finally:
            self._release_lifecycle_admission(claims)
            for owned_job_id in reversed(owned_job_ids):
                self._release_job_admission(owned_job_id)

    async def _cancel_schedule_related(
        self,
        record: ScheduleRecord,
        *,
        source: str,
    ) -> None:
        """Cancel every active or paused job carrying one schedule lineage."""
        cancellable = {
            DaemonJobStatus.PENDING,
            DaemonJobStatus.QUEUED,
            DaemonJobStatus.RUNNING,
            DaemonJobStatus.PAUSED,
            DaemonJobStatus.PAUSED_AT_CHAIN,
        }
        first_error: Exception | None = None
        for meta in self._schedule_related_meta(record):
            if meta.status not in cancellable:
                continue
            try:
                self._pending_jobs.pop(meta.job_id, None)
                task = self._jobs.pop(meta.job_id, None)
                if task is not None and not task.done():
                    task.cancel(
                        msg=(
                            f"schedule cancel for {record.schedule_id} "
                            f"requested by {source}"
                        )
                    )
                    try:
                        await task
                    except asyncio.CancelledError:
                        current_task = asyncio.current_task()
                        if current_task is not None and current_task.cancelling():
                            raise
                    except Exception:
                        pass
                if self._baton_adapter is not None and self._baton_adapter.has_job(
                    meta.job_id
                ):
                    self._baton_adapter.deregister_job(meta.job_id)
                await self._set_job_status(meta.job_id, DaemonJobStatus.CANCELLED)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    async def _cancel_active_job(
        self,
        job_id: str,
        *,
        source: str = "unknown",
    ) -> bool:
        """Cancel a running or pending job.

        For running jobs: sends the cancel signal and updates in-memory
        status immediately, then defers heavyweight I/O to a background task.

        For pending jobs (queued during rate limit backpressure): removes
        from the pending queue and updates the registry.
        """
        _logger.info("job.cancel_requested", job_id=job_id, source=source)

        # Check pending jobs first (not yet started)
        if job_id in self._pending_jobs:
            del self._pending_jobs[job_id]
            await self._set_job_status(job_id, DaemonJobStatus.CANCELLED)
            # Defer scheduler cleanup
            cleanup = asyncio.create_task(
                self._cancel_cleanup(job_id),
                name=f"cancel-cleanup-{job_id}",
            )
            cleanup.add_done_callback(
                lambda t: log_task_exception(t, _logger, "cancel_cleanup.failed"),
            )
            _logger.info("job.pending_cancelled", job_id=job_id, source=source)
            return True

        task = self._jobs.get(job_id)
        if task is None:
            # Auto-recovered baton jobs (#162): a conductor restart re-runs
            # orphaned jobs in the baton loop with NO manager wrapper task, so
            # there is nothing to `task.cancel()`. The wrapped path stops a
            # baton job via `adapter.deregister_job` (the CancelledError handler
            # in `_execute_via_baton` — kills subprocess groups, cancels
            # musician tasks, deregisters). Converge the unwrapped path onto
            # the SAME call so both cancel identically; without it cancel was a
            # silent no-op that left the recovered job running. A job the baton
            # doesn't have AND with no wrapper is genuinely absent → False.
            if self._baton_adapter is not None and self._baton_adapter.has_job(job_id):
                self._baton_adapter.deregister_job(job_id)
                await self._set_job_status(job_id, DaemonJobStatus.CANCELLED)
                cleanup = asyncio.create_task(
                    self._cancel_cleanup(job_id),
                    name=f"cancel-cleanup-{job_id}",
                )
                cleanup.add_done_callback(
                    lambda t: log_task_exception(t, _logger, "cancel_cleanup.failed"),
                )
                _logger.info(
                    "job.baton_cancelled_recovered",
                    job_id=job_id,
                    source=source,
                )
                return True
            return False

        task.cancel(msg=f"cancel_job({job_id}) requested by {source}")
        await self._set_job_status(job_id, DaemonJobStatus.CANCELLED)

        # Defer scheduler cleanup so the IPC handler can
        # respond immediately.  In-memory meta is already authoritative.
        cleanup = asyncio.create_task(
            self._cancel_cleanup(job_id),
            name=f"cancel-cleanup-{job_id}",
        )
        cleanup.add_done_callback(
            lambda t: log_task_exception(t, _logger, "cancel_cleanup.failed"),
        )

        _logger.info("job.cancelled", job_id=job_id, source=source)
        return True

    async def _cancel_cleanup(self, job_id: str) -> None:
        """Background cleanup after cancel — registry + scheduler updates.

        Errors are logged but never propagate, since the cancel already
        succeeded from the user's perspective.
        """
        try:
            await self._registry.update_status(job_id, "cancelled")
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error(
                "cancel_cleanup.registry_failed",
                job_id=job_id,
                exc_info=True,
            )

        try:
            if self._scheduler_instance is not None:
                await self._scheduler_instance.deregister_job(job_id)
        except asyncio.CancelledError:
            raise
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
        recurrence_controller = getattr(self, "_recurrence_controller", None)
        schedule_records = (
            await recurrence_controller.describe()
            if recurrence_controller is not None
            else []
        )
        schedule_index = self._build_schedule_index(schedule_records)
        represented_schedules: set[str] = set()
        registry_records = {
            record.job_id: record
            for record in await self._registry.list_jobs()
        }

        # Active jobs first — enrich with live progress. Terminal in-memory
        # metadata is only a cache; direct recovery can repair the registry
        # without mutating this process' stale JobMeta (#391).
        for meta in self._job_meta.values():
            if meta.status in _ACTIVE_DAEMON_STATUSES:
                entry = meta.to_dict()
                live = self._live_states.get(meta.job_id)
                if live is not None:
                    completed = sum(
                        1 for s in live.sheets.values()
                        if s.status.value == "completed"
                    )
                    entry["progress_completed"] = completed
                    entry["progress_total"] = len(live.sheets)
            elif meta.job_id in registry_records:
                entry = registry_records[meta.job_id].to_dict()
            else:
                entry = meta.to_dict()
            merged = await self._merge_schedule_status(
                meta.job_id,
                entry,
                index=schedule_index,
            )
            result.append(merged)
            matched = await self._schedule_for_job(meta.job_id, index=schedule_index)
            if matched is not None and self._is_stable_schedule_anchor(
                meta.job_id,
                matched,
            ):
                represented_schedules.add(matched.schedule_id)
            seen.add(meta.job_id)

        # Historical jobs from registry
        for record in registry_records.values():
            if record.job_id not in seen:
                merged = await self._merge_schedule_status(
                    record.job_id,
                    record.to_dict(),
                    index=schedule_index,
                )
                result.append(merged)
                matched = await self._schedule_for_job(
                    record.job_id,
                    config_path=Path(record.config_path),
                    index=schedule_index,
                )
                if matched is not None and self._is_stable_schedule_anchor(
                    record.job_id,
                    matched,
                ):
                    represented_schedules.add(matched.schedule_id)

        for schedule_record in schedule_index.records:
            if schedule_record.schedule_id not in represented_schedules:
                result.append(self._schedule_anchor_status(schedule_record))

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

        # #111: capture workspaces BEFORE popping so we can also remove the
        # cleared jobs' workspace state below (the registry row + in-memory
        # entry going away isn't enough — stale workspace state would otherwise
        # be resurrected by a later offline read or re-submit).
        to_clean: list[tuple[str, Path]] = []
        for jid in to_remove:
            removed_meta = self._job_meta.get(jid)
            if removed_meta is not None:
                to_clean.append((jid, removed_meta.workspace))
            self._job_meta.pop(jid, None)
            self._live_states.pop(jid, None)

        deleted = await self._registry.delete_jobs(
            job_ids=job_ids,
            statuses=list(safe_statuses),
            older_than_seconds=older_than_seconds,
        )

        # #111: remove each cleared job's workspace state (per job, so a shared
        # workspace keeps other jobs' rows). Best-effort; never fails the clear.
        for jid, ws in to_clean:
            await self._delete_workspace_state(jid, ws)

        _logger.info(
            "manager.clear_jobs",
            in_memory_removed=len(to_remove),
            registry_deleted=deleted,
        )
        return {"deleted": deleted}

    @staticmethod
    async def _delete_workspace_state(job_id: str, workspace: Path) -> None:
        """Remove a cleared job's per-job state from the workspace backends (#111).

        ``clear`` removes a job from the registry and in-memory; this also
        removes its state from ``workspace/.marianne-state.db`` and
        ``workspace/<job_id>.json`` so a later offline read or re-submit can't
        resurrect it. Per-job (a shared-workspace SQLite DB keeps other jobs'
        rows) and best-effort/non-fatal. This implements the "no authoritative
        state in a workspace" invariant for the explicit clear path; the READ
        path is intentionally untouched so workspace-only jobs (no registry
        entry) still read normally.
        """
        if not workspace.exists():
            return
        from marianne.state import JsonStateBackend, SQLiteStateBackend

        sqlite_path = workspace / STATE_DB_FILENAME
        if sqlite_path.exists():
            sqlite_backend = SQLiteStateBackend(sqlite_path)
            try:
                await sqlite_backend.delete(job_id)
            except Exception:
                _logger.warning(
                    "clear.workspace_sqlite_delete_failed", job_id=job_id, exc_info=True
                )
            finally:
                await sqlite_backend.close()

        json_backend = JsonStateBackend(workspace)
        try:
            await json_backend.delete(job_id)
        except Exception:
            _logger.warning(
                "clear.workspace_json_delete_failed", job_id=job_id, exc_info=True
            )
        finally:
            await json_backend.close()

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
        self,
        job_id: str,
        workspace: Path | None = None,
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
        self,
        job_id: str,
        workspace: Path | None = None,
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
        self,
        job_id: str,
        workspace: Path | None = None,
        sheet_num: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get execution history for a specific job.

        Requires the SQLite state backend for history records.
        """
        ws = await self._resolve_job_workspace(job_id, workspace)

        from marianne.state import SQLiteStateBackend

        sqlite_path = ws / STATE_DB_FILENAME
        records: list[dict[str, Any]] = []
        has_history = False

        if sqlite_path.exists():
            backend = SQLiteStateBackend(sqlite_path)
            try:
                if hasattr(backend, "get_execution_history"):
                    records = await backend.get_execution_history(
                        job_id=job_id,
                        sheet_num=sheet_num,
                        limit=limit,
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
        self,
        job_id: str,
        workspace: Path | None = None,
        sheet_num: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Get state for recover operation.

        Returns the job state and workspace for the CLI to run
        validations locally. The actual validation logic stays
        in the CLI command to avoid duplicating ValidationEngine
        setup in the daemon.
        """
        ws = await self._resolve_job_workspace(job_id, workspace)
        # #111: read the authoritative daemon DB, not per-workspace state. Baton
        # jobs don't write workspace state, so the old get_status() read empty
        # (and created an empty workspace .marianne-state.db as a side effect).
        state = await self._load_checkpoint(job_id, ws)
        if state is None:
            raise JobSubmissionError(f"No state found for job '{job_id}'")

        return {
            "state": state.model_dump(mode="json"),
            "workspace": str(ws),
            "dry_run": dry_run,
            "sheet_num": sheet_num,
        }

    def _diagnostic_snapshot(self, job_id: str) -> dict[str, Any] | None:
        """Runtime diagnostics for failure-evidence enrichment (#133).

        Injected into the BatonAdapter as ``diagnostic_snapshot_fn``. Pure
        in-process reads — the ObserverRecorder ring buffer and the
        ResourceMonitor's cached memory figure — so it is safe to call
        synchronously in the dispatch path. Returns None when neither
        source has anything (observer disabled and no memory reading).
        """
        events: list[dict[str, Any]] = []
        if self._observer_recorder is not None:
            try:
                events = [
                    dict(e)
                    for e in self._observer_recorder.get_recent_events(
                        job_id, limit=50
                    )
                ]
            except Exception:
                _logger.debug(
                    "manager.diagnostic_snapshot.observer_read_failed",
                    job_id=job_id,
                    exc_info=True,
                )
        mem = self._monitor.current_memory_mb()
        if not events and mem is None:
            return None
        return {
            "observer_events": events,
            "resources": {"memory_mb": mem} if mem is not None else None,
        }

    @property
    def output_hub(self) -> Any | None:
        """Live per-sheet output streaming hub (#352 inc-3), or None
        before the baton adapter exists. Backs ``job.output.stream``."""
        if self._baton_adapter is None:
            return None
        return self._baton_adapter.output_hub

    def reload_instrument_profiles(self) -> int:
        """Hot-reload instrument profiles from disk (#171/#332).

        Re-runs the single ``load_all_profiles()`` flow (builtins +
        ~/.marianne/instruments + .marianne/instruments — the only load
        path, no duplicate), syncs the LIVE registry in place so the pool
        and adapter keep valid references, and invalidates the pool so new
        acquisitions build from the fresh profiles. In-flight executions
        finish on their loaded profile. Synchronous and lock-light — safe
        from the SIGHUP handler. Returns the profile count after reload.
        """
        from marianne.instruments.loader import load_all_profiles

        if self._instrument_registry is None:
            return 0
        profiles = load_all_profiles()
        self._instrument_registry.replace_all(profiles)
        if (
            self._baton_adapter is not None
            and self._baton_adapter._backend_pool is not None
        ):
            self._baton_adapter._backend_pool.invalidate()
        _logger.info("manager.instruments_reloaded", count=len(profiles))
        return len(profiles)

    async def resolve_escalation(
        self, job_id: str, sheet_num: int, decision: str
    ) -> dict[str, Any]:
        """Resolve a FERMATA sheet via IPC (#361, backs ``mzt resolve``).

        Delegates to the baton adapter, which writes the decision marker
        and triggers immediate consumption through the existing poll path.
        """
        if self._baton_adapter is None:
            return {"resolved": False, "message": "Baton adapter not initialized"}
        ok, message = self._baton_adapter.resolve_fermata(
            job_id, sheet_num, decision
        )
        return {"resolved": ok, "message": message}

    async def get_daemon_status(self) -> dict[str, Any]:
        """Build daemon status summary.

        Returns all fields required by the ``DaemonStatus`` Pydantic model
        so ``DaemonClient.status()`` can deserialize without crashing.
        """
        from marianne.daemon.ipc.protocol import PROTOCOL_VERSION

        mem = self._monitor.current_memory_mb()
        return {
            "pid": os.getpid(),
            "uptime_seconds": round(time.monotonic() - self._start_time, 1),
            "running_jobs": self.running_count,
            "total_jobs_active": self.active_job_count,
            "memory_usage_mb": round(mem, 1) if mem is not None else 0.0,
            "version": getattr(marianne, "__version__", "0.1.0"),
            "protocol_version": PROTOCOL_VERSION,
        }

    # ─── Shutdown ─────────────────────────────────────────────────────

    async def shutdown(self, graceful: bool = True) -> None:
        """Cancel all running jobs, optionally waiting for sheets.

        Deregisters all active jobs from the global sheet scheduler
        to clean up any pending sheets, running-sheet tracking, and
        dependency data before the daemon exits.
        """
        self._shutting_down = True

        # Revoke recurrence-owned handles before waiting for jobs. This blocks
        # new ticks throughout the shutdown window and, critically, before the
        # TimerWheel drains non-cancelled events after ShutdownRequested.
        if self._recurrence_controller is not None:
            try:
                await self._recurrence_controller.shutdown()
            except Exception:
                _logger.warning(
                    "manager.recurrence_stop_failed",
                    exc_info=True,
                )

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
                    running,
                    timeout=timeout,
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
                    *self._jobs.values(),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        _logger.warning(
                            "manager.shutdown_task_exception",
                            error=str(result),
                            error_type=type(result).__name__,
                        )

        await self._settle_failure_hook_tasks(graceful=graceful)

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

                self._baton_adapter._baton.inbox.put_nowait(ShutdownRequested(graceful=graceful))
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
                    "manager.baton_adapter_stop_failed",
                    exc_info=True,
                )

        if self._mcp_pool is not None:
            try:
                await self._mcp_pool.stop_all()
            except Exception:
                _logger.warning("manager.mcp_pool_stop_failed", exc_info=True)
            finally:
                self._mcp_pool = None

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

        # #203: stop the judgment client before the event bus (unsubscribe);
        # in-flight judgments are cancelled — affected sheets simply remain
        # in composer-resolvable FERMATA.
        if self._judgment_client is not None:
            try:
                await self._judgment_client.stop(self._event_bus)
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.warning(
                    "manager.judgment_client_stop_failed", exc_info=True
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

        # #111: stop the ordered checkpoint writer BEFORE the final flush, so
        # the synchronous flush below is the authoritative last write. Any
        # snapshots still queued in the writer are superseded by that flush
        # (which serialises the latest in-memory state), so they are dropped
        # rather than drained — preventing an older queued blob from landing
        # after the newest one.
        if self._checkpoint_writer is not None:
            await self._checkpoint_writer.stop()
            self._checkpoint_writer = None

        # Final checkpoint flush: persist live states to registry before
        # closing. Active states preserve progress; terminal states are skipped
        # if direct recovery already wrote a newer registry checkpoint (#391).
        flushed, skipped_newer_registry = await self._flush_live_checkpoints_on_shutdown()
        if flushed:
            _logger.info("manager.shutdown_checkpoint_flush", flushed=flushed)
        if skipped_newer_registry:
            _logger.info(
                "manager.shutdown_checkpoint_flush_skipped_newer_registry",
                skipped=skipped_newer_registry,
            )

        await self._schedule_registry.close()
        await self._registry.close()
        self._shutdown_event.set()
        _logger.info("manager.shutdown_complete")

    async def _flush_live_checkpoints_on_shutdown(self) -> tuple[int, int]:
        """Persist live checkpoints without overwriting newer terminal registry state."""
        flushed = 0
        skipped_newer_registry = 0
        for jid, live in self._live_states.items():
            try:
                registry_status, registry_updated_at = _checkpoint_status_and_updated_at(
                    await self._registry.load_checkpoint(jid)
                )
                live_status = (
                    live.status.value
                    if hasattr(live.status, "value")
                    else str(live.status)
                )
                if (
                    live.status in _TERMINAL_CHECKPOINT_STATUSES
                    and (
                        (
                            registry_status is not None
                            and registry_status != live_status
                        )
                        or (
                            live.updated_at is not None
                            and registry_updated_at is not None
                            and registry_updated_at > live.updated_at
                        )
                    )
                ):
                    skipped_newer_registry += 1
                    continue
                checkpoint_json = live.model_dump_json()
                await self._registry.save_checkpoint(jid, checkpoint_json)
                await self._registry.update_status(jid, live_status)
                flushed += 1
            except Exception:
                _logger.warning(
                    "manager.shutdown_flush_failed",
                    job_id=jid,
                    exc_info=True,
                )
        return flushed, skipped_newer_registry

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
        return sum(1 for m in self._job_meta.values() if m.status == DaemonJobStatus.RUNNING)

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

        # Ensure the conductor-managed workspace exists before the observer
        # watches it. On a fresh #58 auto-managed workspace the leaf dir is
        # otherwise created lazily at sheet dispatch — after the observer
        # starts — so the filesystem watch and the JSONL recorder both raced
        # ahead of it and logged spurious FileNotFoundError warnings (and
        # dropped the job's earliest persisted events). The conductor owns the
        # workspace lifecycle, so creating it here is the correct seam.
        meta.workspace.mkdir(parents=True, exist_ok=True)

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

    async def _stop_observer_and_unregister(self, job_id: str) -> None:
        """Settle observer resources after any pre-dispatch boundary."""
        await self._stop_observer(job_id)
        if self._observer_recorder is not None:
            self._observer_recorder.unregister_job(job_id)

    async def _start_observer_with_score_deadline(
        self,
        job_id: str,
        meta: JobMeta,
    ) -> bool:
        """Start the observer within the absolute score budget when present."""
        if meta.wall_deadline_at is None or not self._config.observer.enabled:
            await self._start_observer(job_id)
            return True
        remaining = max(0.0, meta.wall_deadline_at - self._wall_clock())
        if remaining <= 0:
            return False
        try:
            await asyncio.wait_for(
                self._start_observer(job_id),
                timeout=remaining,
            )
        except TimeoutError:
            await self._stop_observer_and_unregister(job_id)
            return False
        except BaseException:
            await self._stop_observer_and_unregister(job_id)
            raise
        return True

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
                state.job_id,
                state.job_id,
            )
        if conductor_key != state.job_id:
            state = state.model_copy(update={"job_id": conductor_key})
        meta = self._job_meta.get(conductor_key)
        if meta is not None:
            state.max_wall_seconds = meta.max_wall_seconds
            state.wall_deadline_at = meta.wall_deadline_at
            state.terminal_reason = meta.terminal_reason
        self._live_states[conductor_key] = state

        # Persist to registry via the ordered writer (#111 — never block the
        # runner, and never reorder per-job saves).
        try:
            checkpoint_json = state.model_dump_json()
            self._persist_checkpoint(state.job_id, checkpoint_json)
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
        instrument: str,
        wait_seconds: float,
        job_id: str,
        sheet_num: int,
    ) -> None:
        """Forward baton rate limit events to the coordinator (#206).

        Injected into the BatonAdapter as ``rate_limit_reporter`` — the
        adapter's run loop calls this for every RateLimitHit it processes,
        keeping the daemon-level RateLimitCoordinator a truthful mirror of
        the baton's instrument state. Consumers: backpressure escalation,
        submit-time "clears in Ns" warnings, the (inactive) scheduler.
        """
        await self._rate_coordinator.report_rate_limit(
            instrument=instrument,
            wait_seconds=wait_seconds,
            job_id=job_id,
            sheet_num=sheet_num,
        )

    @staticmethod
    def _cleanup_status_from_baton_result(
        raw_result: Any,
        fallback: JobTimeoutCleanupStatus,
    ) -> JobTimeoutCleanupStatus:
        """Convert Baton's physical cleanup evidence when it is available."""
        if raw_result is None:
            return fallback
        to_dict = getattr(raw_result, "to_dict", None)
        if not callable(to_dict):
            return fallback
        evidence = to_dict()
        if not isinstance(evidence, dict):
            return fallback
        evidence = dict(evidence)
        evidence_generation = evidence.get("cleanup_generation")
        if evidence_generation is None:
            evidence["cleanup_generation"] = fallback.cleanup_generation
        elif (
            fallback.cleanup_generation is not None
            and evidence_generation != fallback.cleanup_generation
        ):
            return fallback
        return JobTimeoutCleanupStatus(
            cleanup_path="baton.deregister_job",
            deregistration_state="attempted",
            **evidence,
        )

    def _refresh_timeout_cleanup_outcome(
        self,
        job_id: str,
        current: JobTimeoutCleanupStatus,
    ) -> JobTimeoutCleanupStatus:
        """Refresh an initial snapshot after Baton's grace callback matures."""
        meta = self._job_meta.get(job_id)
        raw_result = (
            meta.timeout_cleanup_result_ref if meta is not None else None
        )
        if raw_result is None:
            adapter = self._baton_adapter
            if adapter is None:
                return current
            get_result = getattr(adapter, "get_process_group_cleanup_result", None)
            if not callable(get_result):
                return current
            raw_result = (
                get_result(job_id, current.cleanup_generation)
                if current.cleanup_generation is not None
                else get_result(job_id)
            )
        refreshed = self._cleanup_status_from_baton_result(raw_result, current)
        if (
            meta is not None
            and getattr(raw_result, "escalation_state", None) != "pending"
        ):
            meta.timeout_cleanup_result_ref = None
        return refreshed

    def _bind_cleanup_generation(
        self,
        meta: JobMeta,
        *,
        new_execution: bool,
    ) -> str:
        """Bind runtime cleanup evidence before this execution can wait."""
        generation = meta.cleanup_generation
        if new_execution or generation is None:
            self._cleanup_generation_counter = (
                getattr(self, "_cleanup_generation_counter", 0) + 1
            )
            generation = f"manager-{self._cleanup_generation_counter}"
            meta.cleanup_generation = generation
        adapter = self._baton_adapter
        begin_generation = (
            getattr(adapter, "begin_cleanup_generation", None)
            if adapter is not None
            else None
        )
        if callable(begin_generation):
            begin_generation(meta.job_id, generation)
        return generation

    def _timeout_cleanup_via_baton(self, job_id: str) -> JobTimeoutCleanupStatus:
        """Attempt timeout cleanup only through Baton's deregistration seam."""
        meta = self._job_meta.get(job_id)
        cleanup_generation = (
            meta.cleanup_generation if meta is not None else None
        )
        adapter = self._baton_adapter
        if adapter is None:
            return JobTimeoutCleanupStatus(
                cleanup_path="baton.deregister_job",
                deregistration_state="unavailable",
                cleanup_generation=cleanup_generation,
            )
        try:
            raw_result: Any = adapter.deregister_job(job_id)
        except Exception:
            _logger.exception(
                "job.timeout_cleanup_failed",
                job_id=job_id,
                cleanup_path="baton.deregister_job",
            )
            return JobTimeoutCleanupStatus(
                cleanup_path="baton.deregister_job",
                deregistration_state="failed",
                cleanup_generation=cleanup_generation,
            )
        if meta is not None:
            meta.timeout_cleanup_result_ref = raw_result
        fallback = JobTimeoutCleanupStatus(
            cleanup_path="baton.deregister_job",
            deregistration_state="attempted",
            cleanup_generation=cleanup_generation,
        )
        return self._cleanup_status_from_baton_result(raw_result, fallback)

    async def _record_job_timeout(
        self,
        job_id: str,
        meta: JobMeta,
        *,
        effective_timeout: float,
        execution_elapsed: float,
        expired_before_execution: bool,
    ) -> None:
        """Persist one machine-readable timeout and its observed cleanup."""
        cleanup_outcome = self._timeout_cleanup_via_baton(job_id)
        wall_elapsed = max(0.0, self._wall_clock() - meta.submitted_at)
        meta.error_traceback = None
        meta.timeout_cleanup_outcome = cleanup_outcome
        error_msg = (
            f"Job exceeded timeout of {effective_timeout:.0f}s "
            f"(ran for {execution_elapsed:.0f}s)"
        )
        await self._set_job_status(
            job_id,
            DaemonJobStatus.FAILED,
            error_message=error_msg,
            terminal_reason="timed_out",
        )
        live = self._live_states.get(job_id)
        if live is not None:
            checkpoint_json = live.model_dump_json()
            writer = self._checkpoint_writer
            if writer is not None and writer.running:
                await writer.write_and_wait(job_id, checkpoint_json)
            else:
                await self._registry.save_checkpoint(job_id, checkpoint_json)
        self._recent_failures.append(self._monotonic_clock())
        _logger.error(
            "job.timeout",
            job_id=job_id,
            daemon_limit_seconds=float(self._config.job_timeout_seconds),
            score_limit_seconds=meta.max_wall_seconds,
            effective_timeout_seconds=effective_timeout,
            execution_elapsed_seconds=round(execution_elapsed, 3),
            wall_elapsed_seconds=round(wall_elapsed, 3),
            wall_deadline_at=meta.wall_deadline_at,
            terminal_reason="timed_out",
            cleanup_outcome=cleanup_outcome.model_dump(mode="json"),
            expired_before_execution=expired_before_execution,
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
        # This wrapper owns the coroutine until wait_for wraps it in a Task.
        coro_started = False
        try:
            meta = self._job_meta[job_id]
            self._bind_cleanup_generation(meta, new_execution=False)
            daemon_timeout = float(self._config.job_timeout_seconds)
            timeout = daemon_timeout
            execution_started_at: float | None = None

            async with self._concurrency_semaphore:
                await self._wait_for_job_admission(job_id)
                expired_before_execution = False
                try:
                    # A lifecycle owner may have staged this queued job while
                    # its wrapper waited at the concurrency gate. Only QUEUED
                    # work may cross the activation boundary.
                    activation_status = meta.status
                    if activation_status is not DaemonJobStatus.QUEUED:
                        return

                    if meta.wall_deadline_at is not None:
                        score_remaining = max(
                            0.0,
                            meta.wall_deadline_at - self._wall_clock(),
                        )
                        timeout = min(daemon_timeout, score_remaining)
                        if score_remaining <= 0:
                            expired_before_execution = True

                    if expired_before_execution:
                        continue_execution = False
                    else:
                        continue_execution = True

                        # Create in-process pause event for this job
                        pause_event = asyncio.Event()
                        self._pause_events[job_id] = pause_event

                        meta.started_at = self._wall_clock()
                        await self._set_job_status(
                            job_id,
                            DaemonJobStatus.RUNNING,
                            pid=os.getpid(),
                        )
                finally:
                    self._release_job_admission(job_id)

                if not continue_execution:
                    await self._record_job_timeout(
                        job_id,
                        meta,
                        effective_timeout=0.0,
                        execution_elapsed=0.0,
                        expired_before_execution=True,
                    )
                    return

                observer_started = await self._start_observer_with_score_deadline(
                    job_id,
                    meta,
                )
                if not observer_started:
                    await self._record_job_timeout(
                        job_id,
                        meta,
                        effective_timeout=0.0,
                        execution_elapsed=0.0,
                        expired_before_execution=True,
                    )
                    return

                if meta.wall_deadline_at is not None:
                    score_remaining = max(
                        0.0,
                        meta.wall_deadline_at - self._wall_clock(),
                    )
                    timeout = min(daemon_timeout, score_remaining)
                    if score_remaining <= 0:
                        await self._stop_observer_and_unregister(job_id)
                        await self._record_job_timeout(
                            job_id,
                            meta,
                            effective_timeout=0.0,
                            execution_elapsed=0.0,
                            expired_before_execution=True,
                        )
                        return

                execution_started_at = self._monotonic_clock()
                _logger.info(
                    start_event,
                    job_id=job_id,
                    timeout_seconds=timeout,
                    daemon_limit_seconds=daemon_timeout,
                    score_limit_seconds=meta.max_wall_seconds,
                    effective_timeout_seconds=timeout,
                    wall_deadline_at=meta.wall_deadline_at,
                )

                try:
                    coro_started = True
                    result_status = await asyncio.wait_for(coro, timeout=timeout)
                    final_status = (
                        result_status
                        if isinstance(result_status, DaemonJobStatus)
                        else DaemonJobStatus.COMPLETED
                    )

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
                    if final_status in (DaemonJobStatus.COMPLETED, DaemonJobStatus.FAILED):
                        snapshot_path = self._snapshot_manager.capture(
                            job_id,
                            meta.workspace,
                            config_path=meta.config_path,
                        )

                    await self._set_job_status(
                        job_id,
                        final_status,
                        snapshot_path=snapshot_path,
                    )
                    if final_status == DaemonJobStatus.PAUSED:
                        pause_reason = "unknown"
                        if self._baton_adapter:
                            pause_reason = self._baton_adapter._baton.get_job_pause_reason(job_id)
                        _logger.info(
                            "job.paused",
                            job_id=job_id,
                            reason=pause_reason,
                        )
                    else:
                        _logger.info("job.completed", job_id=job_id)

                except TimeoutError:
                    timeout_observed_at = self._monotonic_clock()
                    execution_elapsed = max(
                        0.0,
                        timeout_observed_at
                        - (
                            execution_started_at
                            if execution_started_at is not None
                            else timeout_observed_at
                        ),
                    )
                    await self._record_job_timeout(
                        job_id,
                        meta,
                        effective_timeout=timeout,
                        execution_elapsed=execution_elapsed,
                        expired_before_execution=False,
                    )

                except asyncio.CancelledError as cancel_exc:
                    # cancel_job() already called _set_job_status(CANCELLED).
                    # Only update if it wasn't set yet (e.g. external cancel).
                    if meta.status != DaemonJobStatus.CANCELLED:
                        await self._set_job_status(
                            job_id,
                            DaemonJobStatus.CANCELLED,
                        )
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
                    meta.error_traceback = traceback.format_exc()
                    await self._set_job_status(
                        job_id,
                        DaemonJobStatus.FAILED,
                        error_message=str(exc),
                    )
                    self._recent_failures.append(time.monotonic())
                    _logger.error(fail_event, job_id=job_id, error=str(exc))

                except Exception as exc:
                    # Unexpected programming bugs — log with full traceback
                    meta.error_traceback = traceback.format_exc()
                    await self._set_job_status(
                        job_id,
                        DaemonJobStatus.FAILED,
                        error_message=f"Unexpected internal error: {exc}",
                    )
                    self._recent_failures.append(time.monotonic())
                    _logger.exception(
                        "job.unexpected_error",
                        job_id=job_id,
                    )

                finally:
                    # Stop observer co-task regardless of outcome
                    await self._stop_observer_and_unregister(job_id)
        finally:
            if not coro_started:
                coro.close()

    async def _run_job_task(self, job_id: str, request: JobRequest) -> None:
        """Task coroutine that runs a single job through the baton engine."""

        async def _execute() -> DaemonJobStatus:
            from marianne.core.config import JobConfig

            config = JobConfig.from_yaml(request.config_path)
            if request.workspace:
                config = config.model_copy(
                    update={"workspace": request.workspace},
                )

            # #359: merge per-invocation --var values (CLI overrides YAML).
            config = _merge_runtime_variables(config, request.runtime_variables)

            # Populate explicit config.name → conductor_id mapping so
            # _on_state_published can resolve the correct owner in O(1)
            # when state.job_id == config.name != conductor job_id.
            if config.name != job_id:
                self._config_name_to_conductor_id[config.name] = job_id

            # Apply daemon-level default thinking method if the job
            # doesn't specify its own (GH#77).
            if self._config.default_thinking_method and not config.prompt.thinking_method:
                config = config.model_copy(
                    update={
                        "prompt": config.prompt.model_copy(
                            update={"thinking_method": self._config.default_thinking_method},
                        ),
                    },
                )

            # Route through the baton execution engine.
            return await self._run_via_baton(job_id, config, request)

        await self._run_managed_task(job_id, _execute())

    @staticmethod
    async def _archive_workspace_on_fresh(config: Any, *, fresh: bool) -> None:
        """Archive non-preserved workspace files for a --fresh submit.

        Only fresh submits archive — resume and plain submits must leave
        the workspace untouched. The archiver does synchronous file I/O,
        so it runs off the event loop. ``WorkspaceArchiver.archive``
        already downgrades OSError/ValueError to a logged no-op; anything
        else propagates and fails the job loudly (a "fresh" run that
        silently inherits stale outputs passes validations on work it
        never did).
        """
        if not (fresh and config.workspace_lifecycle.archive_on_fresh):
            return
        from marianne.workspace.lifecycle import WorkspaceArchiver

        archiver = WorkspaceArchiver(config.workspace, config.workspace_lifecycle)
        archive_path = await asyncio.to_thread(archiver.archive)
        if archive_path is not None:
            _logger.info(
                "workspace_archived_on_fresh",
                workspace=str(config.workspace),
                archive=str(archive_path),
            )

    @staticmethod
    async def _load_spec_corpus(
        spec: SpecCorpusConfig, workspace: Path
    ) -> SpecCorpusConfig | None:
        """Load + populate the spec corpus for a job, off the event loop (#204).

        Resolves ``spec.spec_dir`` against the job ``workspace`` (the same base
        every sheet's working directory uses), reads the corpus and the optional
        CLAUDE.md in a single ``asyncio.to_thread`` hop (the #243 contract — the
        loader does synchronous file I/O), and returns a populated frozen
        ``SpecCorpusConfig`` copy. Returns None when ``spec_dir`` is empty
        (opt-in default). Raises ``SpecCorpusError`` when a configured ``spec_dir``
        does not exist — the caller fails the job loudly (correctness >
        reliability: a silently-dropped corpus means the Musician runs without
        its declared context). A missing CLAUDE.md is optional ("include if
        present") and is skipped silently.
        """
        if not spec.spec_dir:
            return None

        from marianne.spec.loader import SpecCorpusLoader

        resolved_spec_dir = (workspace / spec.spec_dir).resolve()

        def _load() -> list[Any]:
            fragments = SpecCorpusLoader.load(resolved_spec_dir)
            if spec.include_claude_md:
                claude_frag = SpecCorpusLoader.load_claude_md(workspace)
                if claude_frag is not None:
                    fragments = fragments + [claude_frag]
            return fragments

        fragments = await asyncio.to_thread(_load)
        return spec.model_copy(update={"fragments": fragments})

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

        # --fresh archives prior workspace files when the score opts in
        # (workspace_lifecycle.archive_on_fresh). The original call site
        # died with job_service's fresh block (9e8d475) and the baton path
        # never reimplemented it — leaving stale outputs from the previous
        # run in place, where file_exists validations would accept them as
        # the new run's work. Loud failure: a contaminated "fresh" run is
        # a correctness bug, so an archive error fails the job diagnosably.
        await self._archive_workspace_on_fresh(config, fresh=request.fresh)

        # #197: per-job worktree isolation (gated on isolation.enabled;
        # default off). Redirects the job's workspace to an isolated
        # worktree so all sheets run there — F-210 cross-sheet context is
        # preserved (sheets share the one worktree). getattr guard: a
        # real manager always has _job_worktrees; absent it (incomplete
        # test mock) isolation is simply skipped.
        _worktrees = getattr(self, "_job_worktrees", None)
        if _worktrees is not None:
            config = await _setup_worktree_isolation(
                job_id, config, _worktrees
            )

        # Build Sheet entities from config
        sheets = build_sheets(config)
        deps = extract_dependencies(config)

        # Extract retry/cost settings from config
        max_retries = config.retry.max_retries
        max_cost: float | None = None
        if config.cost_limits.enabled and config.cost_limits.max_cost_per_job:
            max_cost = config.cost_limits.max_cost_per_job

        # Publish job.started event
        await adapter.publish_job_event(
            job_id,
            "job.started",
            {
                "sheet_count": len(sheets),
                "instrument": config.effective_instrument_name,
            },
        )

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
        # #361: escalation decoupled from healing — either flag enables
        # FERMATA-on-exhaustion; healing keeps it as its designed end state.
        # Persisted on the checkpoint so resume/restart recovery inherit it.
        escalation_enabled = request.escalation or request.self_healing
        meta = self._job_meta.get(job_id)
        initial_state = CheckpointState(
            job_id=job_id,
            job_name=config.name,
            total_sheets=len(sheets),
            status=CPJobStatus.RUNNING,
            started_at=utc_now(),  # F-493: Set started_at so elapsed time displays correctly
            sheets=initial_sheets,
            instruments_used=list({s.instrument_name for s in sheets if s.instrument_name}),
            total_movements=max((s.movement for s in sheets), default=None),
            escalation_enabled=escalation_enabled,
            self_healing_enabled=request.self_healing,
            parallel_enabled=config.parallel.enabled,
            parallel_max_concurrent=(
                config.parallel.max_concurrent if config.parallel.enabled else 1
            ),
            parallel_fail_fast=(
                config.parallel.fail_fast if config.parallel.enabled else False
            ),
            parallel_stagger_delay_ms=(
                config.parallel.stagger_delay_ms if config.parallel.enabled else 0
            ),
            runtime_variables=dict(request.runtime_variables),  # #359 durable
            max_wall_seconds=meta.max_wall_seconds if meta is not None else None,
            wall_deadline_at=meta.wall_deadline_at if meta is not None else None,
            terminal_reason=meta.terminal_reason if meta is not None else None,
        )
        self._live_states[job_id] = initial_state

        # Handle --start-sheet / -s flag: mark sheets before start_sheet
        # as SKIPPED so the baton doesn't dispatch them.
        if request.start_sheet and request.start_sheet > 1:
            from marianne.core.checkpoint import SheetStatus

            for snum in list(initial_sheets):
                if snum < request.start_sheet:
                    initial_sheets[snum] = initial_sheets[snum].model_copy(
                        update={"status": SheetStatus.SKIPPED},
                    )

        # Deregister stale baton state if this job_id was previously
        # registered (e.g., --fresh resubmit, or cancelled job restarted).
        # Without this, register_job returns early on duplicate and the
        # baton uses stale terminal sheets → instant completion with 0 work.
        adapter.deregister_job(job_id)

        # #204: load the spec corpus off-loop before registration. A
        # configured-but-missing spec_dir fails the job loudly (the Musician
        # must not run without its declared spec context).
        from marianne.spec.loader import SpecCorpusError

        try:
            spec_config = await self._load_spec_corpus(
                config.spec, Path(config.workspace)
            )
        except SpecCorpusError as exc:
            _logger.error(
                "manager.spec_load_failed",
                job_id=job_id,
                spec_dir=config.spec.spec_dir,
                error=str(exc),
            )
            await self._set_job_status(job_id, DaemonJobStatus.FAILED)
            return DaemonJobStatus.FAILED

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
            escalation_enabled=escalation_enabled,  # #361: decoupled from healing
            self_healing_enabled=request.self_healing,
            prompt_config=config.prompt,
            learning_config=config.learning,
            parallel_enabled=config.parallel.enabled,
            parallel_max_concurrent=(
                config.parallel.max_concurrent if config.parallel.enabled else 1
            ),
            parallel_fail_fast=config.parallel.fail_fast,
            cross_sheet=config.cross_sheet,  # F-210
            pacing_seconds=float(config.pause_between_sheets_seconds),
            live_sheets=initial_state.sheets,
            techniques=config.techniques or None,
            stale_detection=config.stale_detection,
            spec_config=spec_config,  # #204
            spec_tags=config.sheet.spec_tags or None,  # #204
            stagger_delay_ms=(
                config.parallel.stagger_delay_ms if config.parallel.enabled else 0
            ),  # #340
            skip_when=config.sheet.skip_when or None,  # #360/#119
            code_execution=config.code_execution,  # #209
            agent_card=config.agent_card,
            cleanup_generation=(
                meta.cleanup_generation if meta is not None else None
            ),
        )

        # #196: thread the score's retry backoff (base/exp/max + jitter) into
        # the baton so `retry:` settings are honored, not the hardcoded defaults.
        adapter.configure_retry(
            base_delay=config.retry.base_delay_seconds,
            exponential_base=config.retry.exponential_base,
            max_delay=config.retry.max_delay_seconds,
            jitter=config.retry.jitter,
        )

        job_succeeded = False
        try:
            # Wait for the baton to complete all sheets
            all_success = await adapter.wait_for_completion(job_id)
            job_succeeded = all_success

            # F-145: Set completed_new_work flag for concert chaining.
            # The monolithic path sets this when summary.completed_sheets > 0.
            # The baton path mirrors this by checking if any sheet completed.
            meta = self._job_meta.get(job_id)
            if meta and adapter.has_completed_sheets(job_id):
                meta.completed_new_work = True

            # Update job-level status in the live CheckpointState so
            # Update job-level status so mzt status agrees with mzt list.
            baton_final = DaemonJobStatus.COMPLETED if all_success else DaemonJobStatus.FAILED
            await self._set_job_status(job_id, baton_final)
            # Set completion timestamp on live state
            live = self._live_states.get(job_id)
            if live is not None:
                live.completed_at = utc_now()

            # Publish completion event
            await adapter.publish_job_event(
                job_id,
                "job.completed" if all_success else "job.failed",
                {"all_success": all_success},
            )

            return DaemonJobStatus.COMPLETED if all_success else DaemonJobStatus.FAILED

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
        finally:
            # #197: remove the job's worktree per cleanup policy. A
            # cancelled/failed job counts as not-succeeded (preserve by
            # default for debugging). Fail-open. getattr guard mirrors
            # the setup call (test-mock resilience).
            _worktrees = getattr(self, "_job_worktrees", None)
            if _worktrees is not None:
                await _cleanup_worktree_isolation(
                    job_id, config, _worktrees, success=job_succeeded
                )

    async def _resume_via_baton(
        self,
        job_id: str,
        workspace: Path,
        no_reload: bool = False,
        pre_resume_status: DaemonJobStatus | None = None,
        from_sheet: int | None = None,
        escalation: bool = False,
        self_healing: bool = False,
    ) -> DaemonJobStatus:
        """Resume a job through the baton adapter using checkpoint recovery.

        Step 29: Loads the persisted CheckpointState, rebuilds Sheet entities
        from the config, and registers the recovered state with the baton.

        Terminal sheets are preserved for a PAUSED resume; for a FAILED/CANCELLED
        resume, FAILED + cascade-SKIPPED sheets are reset to PENDING and
        redispatched (#185 — intrinsic recovery, no separate ``mzt recover``).
        ``from_sheet`` forces a reset of every sheet >= N regardless of status.

        Args:
            job_id: Conductor job ID.
            workspace: Job workspace directory.
            no_reload: When True, use config from checkpoint snapshot
                instead of reloading from disk (fix for #98).
            pre_resume_status: The job's status BEFORE resume transitioned it to
                QUEUED/RUNNING — drives intrinsic recovery (#185). When None,
                no terminal-sheet reset is performed (legacy/preserve behavior).
            from_sheet: When set, reset every sheet >= this number (#185).

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

        # Registry registration is the primary deadline authority. A valid
        # checkpoint deadline remains authoritative for databases created by
        # an intermediate/legacy schema that did not yet have the columns.
        # Neither path derives a new deadline during resume.
        if meta.wall_deadline_at is not None:
            checkpoint.max_wall_seconds = meta.max_wall_seconds
            checkpoint.wall_deadline_at = meta.wall_deadline_at
            checkpoint.terminal_reason = meta.terminal_reason
        elif checkpoint.wall_deadline_at is not None:
            meta.max_wall_seconds = checkpoint.max_wall_seconds
            meta.wall_deadline_at = checkpoint.wall_deadline_at
            meta.terminal_reason = checkpoint.terminal_reason

        # #361: inherit the run's persisted escalation/healing options; an
        # explicit resume flag can additionally enable (never disable) them.
        # Write the effective values back so the NEXT resume/restart inherits
        # the upgraded setting too.
        effective_self_healing = bool(
            getattr(checkpoint, "self_healing_enabled", False) or self_healing
        )
        effective_escalation = bool(
            getattr(checkpoint, "escalation_enabled", False)
            or escalation
            or effective_self_healing
        )
        checkpoint.escalation_enabled = effective_escalation
        checkpoint.self_healing_enabled = effective_self_healing

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

        # #359: re-apply persisted runtime variables. The default resume
        # path re-reads the YAML from disk (no config_snapshot), so the
        # --var values would be lost without this — mirrors the
        # escalation_enabled re-apply above (the #361 durability lesson).
        config = _merge_runtime_variables(
            config, dict(checkpoint.runtime_variables)
        )

        # Build sheets and dependencies
        sheets = build_sheets(config)
        deps = extract_dependencies(config)

        # #185: intrinsic recovery. Reset FAILED/cascade-SKIPPED sheets (or all
        # sheets >= from_sheet) to PENDING on the loaded checkpoint BEFORE it is
        # persisted (below) and handed to recover_job — so the daemon, registry,
        # and baton all agree. A PAUSED resume preserves terminal sheets.
        reset_count = _reset_sheets_for_resume(
            checkpoint, pre_resume_status, from_sheet=from_sheet
        )
        if reset_count:
            _logger.info(
                "baton.resume.sheets_reset",
                job_id=job_id,
                reset_count=reset_count,
                pre_resume_status=(
                    pre_resume_status.value if pre_resume_status is not None else None
                ),
                from_sheet=from_sheet,
            )

        # Extract retry/cost settings
        max_retries = config.retry.max_retries
        max_cost: float | None = None
        if config.cost_limits.enabled and config.cost_limits.max_cost_per_job:
            max_cost = config.cost_limits.max_cost_per_job

        # Publish resume event
        await self._baton_adapter.publish_job_event(
            job_id,
            "job.resuming",
            {"sheet_count": len(sheets)},
        )

        # F-255.2: Populate _live_states with recovered checkpoint so
        # status display and state_sync_callback work during resumed execution.
        self._live_states[job_id] = checkpoint

        # Reset started_at for the new run so elapsed time is accurate.
        # The checkpoint's started_at is from the previous run.
        checkpoint.started_at = utc_now()

        # F-518: Clear completed_at to prevent negative elapsed time.
        # A resumed job is running again, not completed. Stale completed_at
        # from the previous run would cause _compute_elapsed() to calculate
        # (old_completed_at - new_started_at) = negative time.
        checkpoint.completed_at = None
        checkpoint.parallel_enabled = config.parallel.enabled
        checkpoint.parallel_max_concurrent = (
            config.parallel.max_concurrent if config.parallel.enabled else 1
        )
        checkpoint.parallel_fail_fast = (
            config.parallel.fail_fast if config.parallel.enabled else False
        )
        checkpoint.parallel_stagger_delay_ms = (
            config.parallel.stagger_delay_ms if config.parallel.enabled else 0
        )

        # F-493: Persist the updated started_at immediately so status queries
        # show correct elapsed time even before the first baton persist cycle.
        checkpoint_json = checkpoint.model_dump_json()
        await self._registry.save_checkpoint(job_id, checkpoint_json)

        # Now that _live_states exists, set RUNNING across all three stores.
        # _run_managed_task already set meta + registry to RUNNING, but
        # _live_states was created after that with the checkpoint's stale
        # status (paused/failed). This ensures consistency.
        await self._set_job_status(
            job_id,
            DaemonJobStatus.RUNNING,
            pid=os.getpid(),
        )

        # #204: reload the spec corpus on resume too — per-job spec state lives
        # in-memory on the adapter and is lost on conductor restart, so it must
        # be re-loaded here or a resumed job silently loses its spec context.
        from marianne.spec.loader import SpecCorpusError

        try:
            spec_config = await self._load_spec_corpus(
                config.spec, Path(config.workspace)
            )
        except SpecCorpusError as exc:
            _logger.error(
                "manager.spec_load_failed",
                job_id=job_id,
                spec_dir=config.spec.spec_dir,
                error=str(exc),
            )
            await self._set_job_status(job_id, DaemonJobStatus.FAILED)
            return DaemonJobStatus.FAILED

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
            escalation_enabled=effective_escalation,  # #361: inherited+flags
            self_healing_enabled=effective_self_healing,
            prompt_config=config.prompt,
            learning_config=config.learning,
            parallel_enabled=config.parallel.enabled,
            parallel_max_concurrent=(
                config.parallel.max_concurrent if config.parallel.enabled else 1
            ),
            parallel_fail_fast=config.parallel.fail_fast,
            cross_sheet=config.cross_sheet,  # F-210
            pacing_seconds=float(config.pause_between_sheets_seconds),
            live_sheets=checkpoint.sheets,
            techniques=config.techniques or None,
            stale_detection=config.stale_detection,
            spec_config=spec_config,  # #204
            spec_tags=config.sheet.spec_tags or None,  # #204
            stagger_delay_ms=(
                config.parallel.stagger_delay_ms if config.parallel.enabled else 0
            ),  # #340
            skip_when=config.sheet.skip_when or None,  # #360/#119
            code_execution=config.code_execution,  # #209
            agent_card=config.agent_card,
        )

        # #196: re-thread retry backoff on resume too — BatonCore is in-memory
        # and reset on restart, so a resumed job must re-apply its `retry:`
        # settings or it would silently fall back to the hardcoded defaults.
        self._baton_adapter.configure_retry(
            base_delay=config.retry.base_delay_seconds,
            exponential_base=config.retry.exponential_base,
            max_delay=config.retry.max_delay_seconds,
            jitter=config.retry.jitter,
        )

        # Reconcile live state with baton's view: recover_job resets
        # in_progress sheets to PENDING (dead musicians), but the live
        # CheckpointState still has them as in_progress with stale times.
        from marianne.core.checkpoint import SheetStatus

        for sheet_num, sheet_state in checkpoint.sheets.items():
            if sheet_state.status == SheetStatus.IN_PROGRESS:
                checkpoint.sheets[sheet_num] = sheet_state.model_copy(
                    update={"status": SheetStatus.PENDING, "started_at": None},
                )
        checkpoint.current_sheet = None

        try:
            # Wait for the baton to complete all sheets
            all_success = await self._baton_adapter.wait_for_completion(job_id)

            # F-145: Set completed_new_work flag for concert chaining.
            if meta and self._baton_adapter.has_completed_sheets(job_id):
                meta.completed_new_work = True

            # Update job-level status across all three stores.
            resume_final = DaemonJobStatus.COMPLETED if all_success else DaemonJobStatus.FAILED
            await self._set_job_status(job_id, resume_final)
            live = self._live_states.get(job_id)
            if live is not None:
                live.completed_at = utc_now()

            await self._baton_adapter.publish_job_event(
                job_id,
                "job.completed" if all_success else "job.failed",
                {"all_success": all_success},
            )

            return DaemonJobStatus.COMPLETED if all_success else DaemonJobStatus.FAILED

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
        self,
        job_id: str,
        workspace: Path,
        no_reload: bool = False,
        pre_resume_status: DaemonJobStatus | None = None,
        from_sheet: int | None = None,
        escalation: bool = False,
        self_healing: bool = False,
    ) -> None:
        """Task coroutine that resumes a paused job."""

        async def _execute() -> DaemonJobStatus:
            # Route through the baton execution engine.
            return await self._resume_via_baton(
                job_id,
                workspace,
                no_reload=no_reload,
                pre_resume_status=pre_resume_status,
                from_sheet=from_sheet,
                escalation=escalation,
                self_healing=self_healing,
            )

        await self._run_managed_task(
            job_id,
            _execute(),
            start_event="job.resuming",
            fail_event="job.resume_failed",
        )

    async def _settle_failure_hook_tasks(self, *, graceful: bool) -> None:
        """Finish or cancel failure-hook tasks before registry shutdown."""
        pending = [task for task in self._failure_hook_tasks.values() if not task.done()]
        if graceful and pending:
            _, still_pending = await asyncio.wait(
                pending,
                timeout=self._config.shutdown_timeout_seconds,
            )
            pending = list(still_pending)
        for task in pending:
            task.cancel(msg="conductor shutdown during failure hooks")
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._failure_hook_tasks.clear()

    def _schedule_failure_hooks(self, job_id: str) -> None:
        """Schedule one durably claimed failure-hook sequence when eligible."""
        meta = self._job_meta.get(job_id)
        if (
            meta is None
            or meta.status is not DaemonJobStatus.FAILED
            or not meta.failure_hook_config
        ):
            return
        existing = self._failure_hook_tasks.get(job_id)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(
            self._execute_failure_hooks_task(job_id),
            name=f"failure-hooks-{job_id}",
        )
        self._failure_hook_tasks[job_id] = task

        def _on_failure_hooks_done(completed: asyncio.Task[Any]) -> None:
            if self._failure_hook_tasks.get(job_id) is completed:
                self._failure_hook_tasks.pop(job_id, None)
            log_task_exception(
                completed,
                _logger,
                "failure_hooks.task_failed",
            )

        task.add_done_callback(_on_failure_hooks_done)

    async def _execute_failure_hooks_task(self, job_id: str) -> None:
        """Run terminal-failure hooks once without changing the failed job."""
        import json

        meta = self._job_meta.get(job_id)
        if (
            meta is None
            or meta.status is not DaemonJobStatus.FAILED
            or not meta.failure_hook_config
        ):
            return
        if not await self._registry.claim_failure_hooks(job_id):
            _logger.info(
                "failure_hooks.already_claimed",
                job_id=job_id,
            )
            return

        hooks = meta.failure_hook_config
        results: list[dict[str, Any]] = []
        _logger.info(
            "failure_hooks.daemon_executing",
            job_id=job_id,
            terminal_status=DaemonJobStatus.FAILED.value,
            hook_count=len(hooks),
        )

        for index, hook in enumerate(hooks):
            hook_type = hook.get("type", "unknown")
            result: dict[str, Any] = {
                "hook_type": hook_type,
                "description": hook.get("description"),
                "success": False,
                "trigger": "failure",
                "job_id": job_id,
                "terminal_status": DaemonJobStatus.FAILED.value,
            }
            try:
                if hook_type == "run_job":
                    if hook.get("pause_before_chain", False):
                        result["error_message"] = (
                            "pause_before_chain is not valid for terminal-failure hooks"
                        )
                    else:
                        executed = await self._execute_hook_run_job(
                            job_id,
                            hook,
                            meta.concert_config,
                            meta,
                        )
                        result.update(executed)
                elif hook_type == "run_command":
                    result.update(
                        await self._execute_hook_command(
                            hook,
                            meta,
                            use_shell=True,
                            job_status=DaemonJobStatus.FAILED.value,
                        )
                    )
                elif hook_type == "run_script":
                    result.update(
                        await self._execute_hook_command(
                            hook,
                            meta,
                            use_shell=False,
                            job_status=DaemonJobStatus.FAILED.value,
                        )
                    )
                else:
                    result["error_message"] = f"Unknown hook type: {hook_type}"
            except asyncio.CancelledError:
                result.update(
                    {
                        "settlement": "cancelled",
                        "error_message": "Conductor shutdown cancelled failure hook",
                        "trigger": "failure",
                        "job_id": job_id,
                        "terminal_status": DaemonJobStatus.FAILED.value,
                    }
                )
                results.append(result)
                await self._registry.settle_failure_hooks(
                    job_id,
                    json.dumps(results),
                )
                raise
            except Exception as exc:
                result["error_message"] = f"Exception: {exc}"
                _logger.error(
                    "failure_hook.daemon_exception",
                    job_id=job_id,
                    hook_index=index + 1,
                    hook_type=hook_type,
                    error=str(exc),
                    exc_info=True,
                )

            # update() above may replace common keys, so bind failure identity
            # after the action returns.
            result["trigger"] = "failure"
            result["job_id"] = job_id
            result["terminal_status"] = DaemonJobStatus.FAILED.value
            results.append(result)

            if not result.get("success") and hook.get("on_failure", "continue") == "abort":
                break

        await self._registry.settle_failure_hooks(job_id, json.dumps(results))
        _logger.info(
            "failure_hooks.daemon_completed",
            job_id=job_id,
            total=len(results),
            succeeded=sum(1 for result in results if result.get("success")),
            failed=sum(1 for result in results if not result.get("success")),
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
                        job_id,
                        hook,
                        concert,
                        meta,
                    )
                elif hook_type == "run_command":
                    result = await self._execute_hook_command(
                        hook,
                        meta,
                        use_shell=True,
                    )
                elif hook_type == "run_script":
                    result = await self._execute_hook_command(
                        hook,
                        meta,
                        use_shell=False,
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
                job_id,
                json.dumps(results),
            )
        except Exception:
            _logger.error(
                "hooks.daemon_store_results_failed",
                job_id=job_id,
                exc_info=True,
            )

        # If any hook failed, downgrade job from COMPLETED to FAILED
        if any_failed and meta.status == DaemonJobStatus.COMPLETED:
            try:
                await self._set_job_status(
                    job_id,
                    DaemonJobStatus.FAILED,
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
        score_dir = (
            meta.config_path.parent if meta.config_path is not None else None
        )
        job_path_str = self._expand_hook_vars(
            job_path_str,
            meta.workspace,
            parent_job_id,
            score_dir=score_dir,
        )
        if "{" in job_path_str or "}" in job_path_str:
            result["error_message"] = (
                f"Unresolved template syntax in job_path: {hook.get('job_path')}"
            )
            return result
        job_path = Path(job_path_str)
        # Relative job_paths anchor to the submitting score's directory —
        # the conductor process CWD is an accident, not a contract.
        if not job_path.is_absolute() and score_dir is not None:
            job_path = (score_dir / job_path).resolve()

        if not job_path.exists():
            result["error_message"] = f"Job config not found: {job_path}"
            return result

        # Concert depth check
        current_depth = meta.chain_depth or 0
        if concert and concert.get("enabled"):
            max_depth = concert.get("max_chain_depth", 5)
            if current_depth >= max_depth:
                # Reaching the configured chain-depth limit is a CLEAN STOP, not a hook
                # failure. Returning success=False here would downgrade the parent job
                # COMPLETED -> FAILED (via _execute_hooks_task's any_failed check),
                # misclassifying a legitimate chain-budget exhaustion as an error and
                # making self-chaining jobs look broken. Mark success so the parent
                # stays COMPLETED; the chained job simply isn't submitted.
                result["success"] = True
                result["output"] = (
                    f"Concert chain depth limit reached ({max_depth}) — clean stop, "
                    "parent remains COMPLETED"
                )
                _logger.info(
                    "hooks.chain_depth_limit_reached",
                    job_id=parent_job_id,
                    max_depth=max_depth,
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
            chained_workspace = Path(
                self._expand_hook_vars(
                    str(raw_ws),
                    meta.workspace,
                    parent_job_id,
                )
            )
        elif concert and concert.get("inherit_workspace", True):
            chained_workspace = meta.workspace

        # pause_before_chain: hold the chain trigger instead of submitting
        if hook.get("pause_before_chain", False):
            # Store the fully-resolved hook for later resumption
            meta.held_chain_hook = {
                "job_path": str(job_path),
                "workspace": str(chained_workspace) if chained_workspace else None,
                "fresh": hook.get("fresh", False),
                "chain_depth": current_depth + 1,
            }
            await self._set_job_status(
                parent_job_id,
                DaemonJobStatus.PAUSED_AT_CHAIN,
            )
            result["success"] = True
            result["output"] = (
                "Chain held at pause point — use 'mzt resume' to trigger the next cycle"
            )
            result["paused_at_chain"] = True
            _logger.info(
                "hook.pause_before_chain",
                job_id=parent_job_id,
                job_path=str(job_path),
            )
            return result

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
            result["error_message"] = f"Chained job rejected: {response.message}"

        return result

    # Patterns that are almost certainly destructive when run as hooks.
    # The composer owns their YAML — this is a safety net, not a sandbox.
    _DESTRUCTIVE_HOOK_PATTERNS: ClassVar[re.Pattern[str]] = re.compile(
        r"(?:rm\s+(?:--?[a-zA-Z][a-zA-Z-]*\s+)+/|"
        r"mkfs\.|dd\s+(?:if|of)=|"
        r":\(\)\s*\{.*\}\s*;|"
        r">\s*/dev/sd|"
        r"chmod\s+-R\s+[0-7]{3,4}\s+/)",
    )
    _MAX_HOOK_COMMAND_LENGTH: ClassVar[int] = 4096

    def _validate_hook_command(self, command: str, *, hook_type: str) -> None:
        """Guard against obviously destructive hook commands.

        This is a best-effort safety check.  It does NOT sandbox or sanitize
        commands — the composer is trusted.  It catches catastrophic typos.
        """
        if len(command) > self._MAX_HOOK_COMMAND_LENGTH:
            raise ValueError(f"{hook_type} command exceeds {self._MAX_HOOK_COMMAND_LENGTH} chars")
        if self._DESTRUCTIVE_HOOK_PATTERNS.search(command):
            raise ValueError(f"{hook_type} command contains destructive pattern")

    async def _execute_hook_command(
        self,
        hook: dict[str, Any],
        meta: JobMeta,
        *,
        use_shell: bool = True,
        job_status: str | None = None,
    ) -> dict[str, Any]:
        """Execute a run_command or run_script hook.

        run_command uses shell execution (intentional — commands come from
        user-authored YAML config, not runtime user input). run_script uses
        subprocess exec (no shell) for cases where shell features aren't needed.
        """
        import shlex

        from marianne.execution.instruments.cli_backend import (
            kill_process_group_if_alive,
        )

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

        effective_job_status = job_status or meta.status.value
        command = self._expand_hook_vars(
            command,
            meta.workspace,
            meta.job_id,
            job_status=effective_job_status,
            score_dir=(
                meta.config_path.parent if meta.config_path is not None else None
            ),
            for_shell=use_shell,
        )
        self._validate_hook_command(command, hook_type=hook_type)
        cwd = hook.get("working_directory") or str(meta.workspace)
        timeout = hook.get("timeout_seconds", 300.0)
        hook_env = os.environ.copy()
        hook_env.update(
            {
                "MARIANNE_JOB_ID": meta.job_id,
                "MARIANNE_JOB_STATUS": effective_job_status,
            }
        )

        proc: asyncio.subprocess.Process | None = None
        pgid: int | None = None
        try:
            if use_shell:
                proc = await asyncio.create_subprocess_shell(  # noqa: S604
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                    env=hook_env,
                    start_new_session=True,
                )
            else:
                args = shlex.split(command)
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                    env=hook_env,
                    start_new_session=True,
                )

            if proc.pid is not None:
                try:
                    pgid = os.getpgid(proc.pid)
                except ProcessLookupError:
                    pgid = None

            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                result["exit_code"] = proc.returncode
                result["success"] = proc.returncode == 0
                result["output"] = stdout[-2000:] if stdout else None
            except TimeoutError:
                result["error_message"] = f"Timeout after {timeout}s"

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result["error_message"] = str(exc)
        finally:
            await kill_process_group_if_alive(proc, pgid)

        return result

    @staticmethod
    def _expand_hook_vars(
        template: str,
        workspace: Path,
        job_id: str,
        *,
        job_status: str | None = None,
        score_dir: Path | None = None,
        for_shell: bool = False,
    ) -> str:
        """Expand template variables in hook paths/commands.

        Delegates to the shared expand_hook_variables() utility in
        utils/hooks.py to avoid reimplementing variable expansion.
        """
        from marianne.utils.hooks import expand_hook_variables

        return expand_hook_variables(
            template,
            workspace=workspace,
            job_id=job_id,
            job_status=job_status,
            score_dir=score_dir,
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
            name for name, cid in self._config_name_to_conductor_id.items() if cid == job_id
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
            DaemonJobStatus.PAUSED,
            DaemonJobStatus.PAUSED_AT_CHAIN,
            DaemonJobStatus.FAILED,
            DaemonJobStatus.COMPLETED,
        ):
            self._live_states.pop(job_id, None)

        # 2. Check for task exception and update metadata/registry
        try:
            exc = log_task_exception(task, _logger, "job.task_failed")
            if exc:
                meta = self._job_meta.get(job_id)
                if meta and meta.status == DaemonJobStatus.RUNNING:
                    # Route through _set_job_status to update meta,
                    # live state, and registry atomically.
                    update_task = asyncio.create_task(
                        self._set_job_status(
                            job_id,
                            DaemonJobStatus.FAILED,
                            error_message=str(exc),
                        ),
                        name=f"status-update-{job_id}",
                    )
                    update_task.add_done_callback(
                        lambda t: log_task_exception(
                            t,
                            _logger,
                            "registry.update_failed",
                        ),
                    )
        except RuntimeError:
            _logger.error(
                "task_done_status_update_failed",
                job_id=job_id,
                exc_info=True,
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
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error(
                "task_done_modify_resume_failed",
                job_id=job_id,
                exc_info=True,
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
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error(
                "task_done_hooks_spawn_failed",
                job_id=job_id,
                exc_info=True,
            )

        # 3. TTL-based snapshot cleanup (runs synchronously, fast)
        try:
            self._snapshot_manager.cleanup(
                max_age_hours=self._config.observer.snapshot_ttl_hours,
            )
        except OSError:
            _logger.error(
                "task_done_snapshot_cleanup_failed",
                job_id=job_id,
                exc_info=True,
            )

        # 4. Prune old completed/failed/cancelled jobs from history
        try:
            self._prune_job_history()
        except RuntimeError:
            _logger.error(
                "task_done_prune_failed",
                job_id=job_id,
                exc_info=True,
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
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.error(
                "task_done_entropy_check_failed",
                job_id=job_id,
                exc_info=True,
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
                (jid, m)
                for jid, m in self._job_meta.items()
                if m.status
                in (
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
