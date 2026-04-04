"""BatonAdapter — wires the baton into the conductor.

Step 28: Replace monolithic execution with the baton's event-driven model.

The adapter is the bridge between the conductor (JobManager) and the baton
(BatonCore). It handles:

1. **Job submission → baton registration** (Surface 1)
   JobConfig → Sheet[] → SheetExecutionState[] → BatonCore.register_job()

2. **Dispatch callback → backend acquisition** (Surface 2)
   BatonCore dispatches → adapter acquires backend → spawns musician task

3. **Prompt assembly** (Surface 3)
   Creates SheetContext, calls PromptBuilder.build_sheet_prompt()

4. **State synchronization** (Surface 4)
   BatonSheetStatus ↔ CheckpointState.SheetStatus mapping

5. **EventBus integration** (Surface 5)
   Baton events → ObserverEvent format → EventBus.publish()

6. **Rate limit callback bridge** (Surface 6)
   Musician extracts wait time → SheetAttemptResult → baton handles

7. **Feature flag** (Surface 8)
   DaemonConfig.use_baton controls whether the adapter is active

Design decisions:
- Checkpoint is source of truth. Baton rebuilds from checkpoint on restart.
- Save checkpoint FIRST, then update baton state (prevents re-execution).
- The adapter does NOT own the baton's main loop — the manager runs it.
- Concert support: sequential score submission (option 1 from wiring analysis).

See: ``workspaces/v1-beta-v3/movement-2/step-28-wiring-analysis.md``
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from mozart.core.sheet import Sheet
from mozart.daemon.baton.core import BatonCore
from mozart.daemon.baton.events import (
    BatonEvent,
    DispatchRetry,
    SheetAttemptResult,
    SheetSkipped,
)
from mozart.daemon.baton.musician import sheet_task
from mozart.daemon.baton.state import (
    AttemptContext,
    AttemptMode,
    BatonSheetStatus,
    SheetExecutionState,
)

if TYPE_CHECKING:
    from mozart.core.checkpoint import CheckpointState
    from mozart.core.config.job import PromptConfig
    from mozart.daemon.baton.backend_pool import BackendPool
    from mozart.daemon.baton.prompt import PromptRenderer
    from mozart.daemon.event_bus import EventBus
    from mozart.daemon.types import ObserverEvent

_logger = logging.getLogger(__name__)

# Type alias for the state sync callback.
# Called after each baton event that changes sheet status:
#   (job_id, sheet_num, checkpoint_status_string) → None
StateSyncCallback = Callable[[str, int, str], None]


# =============================================================================
# State mapping — Surface 4
# =============================================================================

# BatonSheetStatus → CheckpointState status string
# The baton tracks 11 states; CheckpointState tracks 5.
_BATON_TO_CHECKPOINT: dict[BatonSheetStatus, str] = {
    BatonSheetStatus.PENDING: "pending",
    BatonSheetStatus.READY: "pending",
    BatonSheetStatus.DISPATCHED: "in_progress",
    BatonSheetStatus.RUNNING: "in_progress",
    BatonSheetStatus.COMPLETED: "completed",
    BatonSheetStatus.FAILED: "failed",
    BatonSheetStatus.SKIPPED: "skipped",
    BatonSheetStatus.CANCELLED: "failed",  # No "cancelled" in CheckpointState
    BatonSheetStatus.WAITING: "in_progress",  # Rate limited
    BatonSheetStatus.RETRY_SCHEDULED: "pending",  # Awaiting retry
    BatonSheetStatus.FERMATA: "in_progress",  # Escalation pause
}

# CheckpointState status string → BatonSheetStatus (for resume)
_CHECKPOINT_TO_BATON: dict[str, BatonSheetStatus] = {
    "pending": BatonSheetStatus.PENDING,
    "in_progress": BatonSheetStatus.DISPATCHED,
    "completed": BatonSheetStatus.COMPLETED,
    "failed": BatonSheetStatus.FAILED,
    "skipped": BatonSheetStatus.SKIPPED,
}


def baton_to_checkpoint_status(status: BatonSheetStatus) -> str:
    """Map a BatonSheetStatus to the equivalent CheckpointState status string.

    Every BatonSheetStatus value has an entry in the mapping.
    The baton tracks more granular states — this function collapses them
    to the 5 states CheckpointState understands.

    Args:
        status: The baton's sheet status.

    Returns:
        The CheckpointState status string.
    """
    return _BATON_TO_CHECKPOINT[status]


def checkpoint_to_baton_status(status: str) -> BatonSheetStatus:
    """Map a CheckpointState status string to BatonSheetStatus.

    Used during resume to rebuild baton state from a checkpoint.

    Args:
        status: The CheckpointState status string.

    Returns:
        The equivalent BatonSheetStatus.

    Raises:
        KeyError: If the status string is not recognized.
    """
    return _CHECKPOINT_TO_BATON[status]


# =============================================================================
# EventBus integration — Surface 5
# =============================================================================


def attempt_result_to_observer_event(
    result: SheetAttemptResult,
) -> ObserverEvent:
    """Convert a SheetAttemptResult to the ObserverEvent format.

    Maps baton musician results to the event names the EventBus
    subscribers expect (dashboard, learning hub, notifications).

    Args:
        result: The musician's execution report.

    Returns:
        Dict matching the ObserverEvent TypedDict shape.
    """
    if result.rate_limited:
        event_name = "rate_limit.active"
    elif result.execution_success and result.validation_pass_rate >= 100.0:
        event_name = "sheet.completed"
    elif result.execution_success:
        event_name = "sheet.partial"
    else:
        event_name = "sheet.failed"

    return {
        "job_id": result.job_id,
        "sheet_num": result.sheet_num,
        "event": event_name,
        "data": {
            "instrument": result.instrument_name,
            "attempt": result.attempt,
            "success": result.execution_success,
            "validation_pass_rate": result.validation_pass_rate,
            "cost_usd": result.cost_usd,
            "duration_seconds": result.duration_seconds,
            "rate_limited": result.rate_limited,
            "error_classification": result.error_classification,
            "model_used": result.model_used,
        },
        "timestamp": result.timestamp,
    }


def skipped_to_observer_event(event: SheetSkipped) -> ObserverEvent:
    """Convert a SheetSkipped event to ObserverEvent format.

    Args:
        event: The sheet skip event.

    Returns:
        Dict matching the ObserverEvent TypedDict shape.
    """
    return {
        "job_id": event.job_id,
        "sheet_num": event.sheet_num,
        "event": "sheet.skipped",
        "data": {"reason": event.reason},
        "timestamp": event.timestamp,
    }


# =============================================================================
# Sheet conversion utilities
# =============================================================================


def sheets_to_execution_states(
    sheets: list[Sheet],
    *,
    max_retries: int = 3,
    max_completion: int = 5,
) -> dict[int, SheetExecutionState]:
    """Convert Sheet entities to SheetExecutionState dict for baton registration.

    Each Sheet becomes a SheetExecutionState with the baton's extended
    tracking fields. The sheet_num is the dict key.

    Args:
        sheets: List of Sheet entities from build_sheets().
        max_retries: Maximum normal retry attempts per sheet.
        max_completion: Maximum completion mode attempts per sheet.

    Returns:
        Dict of sheet_num → SheetExecutionState.
    """
    states: dict[int, SheetExecutionState] = {}
    for sheet in sheets:
        states[sheet.num] = SheetExecutionState(
            sheet_num=sheet.num,
            instrument_name=sheet.instrument_name,
            max_retries=max_retries,
            max_completion=max_completion,
        )
    return states


def extract_dependencies(config: Any) -> dict[int, list[int]]:
    """Extract baton-compatible dependency graph from a JobConfig.

    The baton expects: ``{sheet_num: [dep_sheet_num, ...]}``

    Dependencies are stage-based: all sheets in stage N+1 depend on
    all sheets in stage N. Sheets within the same stage (fan-out voices)
    have no internal dependencies.

    Args:
        config: Parsed JobConfig with sheet.get_fan_out_metadata().

    Returns:
        Dict of sheet_num → list of dependency sheet_nums.
    """
    total = config.sheet.total_sheets

    # Group sheets by stage
    stage_sheets: dict[int, list[int]] = {}
    for num in range(1, total + 1):
        meta = config.sheet.get_fan_out_metadata(num)
        stage = meta.stage
        stage_sheets.setdefault(stage, []).append(num)

    # Build dependency map: each sheet depends on ALL sheets in the
    # previous stage.
    sorted_stages = sorted(stage_sheets.keys())
    deps: dict[int, list[int]] = {}

    for i, stage in enumerate(sorted_stages):
        if i == 0:
            # First stage: no dependencies
            for num in stage_sheets[stage]:
                deps[num] = []
        else:
            prev_stage = sorted_stages[i - 1]
            prev_sheets = stage_sheets[prev_stage]
            for num in stage_sheets[stage]:
                deps[num] = list(prev_sheets)

    return deps


# =============================================================================
# BatonAdapter — the central wiring module
# =============================================================================


class BatonAdapter:
    """Bridges the conductor (JobManager) and the baton (BatonCore).

    The adapter owns:
    - A BatonCore instance (event loop, sheet registry, state machine)
    - A mapping of job_id → Sheet[] (for prompt rendering at dispatch time)
    - Active musician tasks (asyncio.Task per dispatched sheet)

    The adapter does NOT own:
    - The BackendPool (injected by the manager)
    - The EventBus (injected by the manager)
    - The CheckpointState (managed by the manager's state backend)

    Usage::

        adapter = BatonAdapter(event_bus=bus)
        adapter.set_backend_pool(pool)

        # Register a job
        adapter.register_job("j1", sheets, deps)

        # Run the baton (blocks until shutdown)
        await adapter.run()
    """

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        max_concurrent_sheets: int = 10,
        state_sync_callback: StateSyncCallback | None = None,
    ) -> None:
        """Initialize the BatonAdapter.

        Args:
            event_bus: Optional EventBus for publishing events to subscribers.
            max_concurrent_sheets: Global concurrency ceiling for dispatch.
            state_sync_callback: Optional callback invoked after each baton
                event that changes sheet status. Receives
                (job_id, sheet_num, checkpoint_status_string). Used by the
                manager to sync baton state changes to CheckpointState.
        """
        self._baton = BatonCore()
        self._event_bus = event_bus
        self._max_concurrent_sheets = max_concurrent_sheets
        self._state_sync_callback = state_sync_callback

        # Job → Sheet mapping for prompt rendering
        self._job_sheets: dict[str, dict[int, Sheet]] = {}

        # Active musician tasks: (job_id, sheet_num) → Task
        self._active_tasks: dict[tuple[str, int], asyncio.Task[Any]] = {}

        # BackendPool — injected via set_backend_pool()
        self._backend_pool: BackendPool | None = None

        # Per-job PromptRenderer — created when prompt_config is provided
        self._job_renderers: dict[str, PromptRenderer] = {}

        # Per-job completion events — set when all sheets reach terminal state
        self._completion_events: dict[str, asyncio.Event] = {}

        # Per-job completion status: True = all sheets completed, False = has failures
        self._completion_results: dict[str, bool] = {}

        # Running state
        self._running = False
        self._baton_task: asyncio.Task[Any] | None = None

    @property
    def baton(self) -> BatonCore:
        """The underlying BatonCore instance."""
        return self._baton

    @property
    def is_running(self) -> bool:
        """Whether the baton's event loop is running."""
        return self._running

    def set_backend_pool(self, pool: BackendPool) -> None:
        """Inject the BackendPool for backend acquisition.

        Must be called before dispatching any sheets.

        Args:
            pool: The backend pool from the manager.
        """
        self._backend_pool = pool

    # =========================================================================
    # Job Registration — Surface 1
    # =========================================================================

    def register_job(
        self,
        job_id: str,
        sheets: list[Sheet],
        dependencies: dict[int, list[int]],
        *,
        max_cost_usd: float | None = None,
        max_retries: int = 3,
        max_completion: int = 5,
        escalation_enabled: bool = False,
        self_healing_enabled: bool = False,
        prompt_config: PromptConfig | None = None,
        parallel_enabled: bool = False,
    ) -> None:
        """Register a job with the baton for event-driven execution.

        Converts Sheet entities to SheetExecutionState and registers
        them with the baton's sheet registry.

        Args:
            job_id: Unique job identifier (conductor job_id).
            sheets: Sheet entities from build_sheets().
            dependencies: Dependency graph {sheet_num: [dep_nums]}.
            max_cost_usd: Optional per-job cost limit.
            max_retries: Max normal retry attempts per sheet.
            max_completion: Max completion mode attempts per sheet.
            escalation_enabled: Enter fermata on exhaustion.
            self_healing_enabled: Try self-healing on exhaustion.
            prompt_config: Optional PromptConfig for full prompt rendering.
                When provided, creates a PromptRenderer for this job that
                handles the complete 9-layer prompt assembly pipeline.
            parallel_enabled: Whether parallel execution is enabled
                (for preamble concurrency warning).
        """
        # Store sheets for prompt rendering at dispatch time
        self._job_sheets[job_id] = {s.num: s for s in sheets}

        # Create PromptRenderer if config is available (F-104)
        if prompt_config is not None:
            from mozart.daemon.baton.prompt import PromptRenderer

            total_sheets = len(sheets)
            total_stages = len({s.movement for s in sheets}) or 1
            self._job_renderers[job_id] = PromptRenderer(
                prompt_config=prompt_config,
                total_sheets=total_sheets,
                total_stages=total_stages,
                parallel_enabled=parallel_enabled,
            )

        # Create completion event for this job
        self._completion_events[job_id] = asyncio.Event()

        # Convert to execution states
        states = sheets_to_execution_states(
            sheets,
            max_retries=max_retries,
            max_completion=max_completion,
        )

        # Register with baton
        self._baton.register_job(
            job_id,
            states,
            dependencies,
            escalation_enabled=escalation_enabled,
            self_healing_enabled=self_healing_enabled,
        )

        # Set cost limits if configured
        if max_cost_usd is not None:
            self._baton.set_job_cost_limit(job_id, max_cost_usd)

        _logger.info(
            "adapter.job_registered",
            extra={
                "job_id": job_id,
                "sheet_count": len(sheets),
                "dependency_count": len(dependencies),
                "max_retries": max_retries,
                "max_completion": max_completion,
            },
        )

        # Kick the event loop so dispatch_ready runs for the newly registered
        # sheets.  Without this the loop blocks on inbox.get() forever
        # because no musician or timer has produced an event yet.
        self._baton.inbox.put_nowait(DispatchRetry())

    def deregister_job(self, job_id: str) -> None:
        """Remove a job from the adapter and baton.

        Cleans up all per-job state including active tasks.

        Args:
            job_id: The job to remove.
        """
        # Cancel active musician tasks for this job
        keys_to_cancel = [
            key for key in self._active_tasks if key[0] == job_id
        ]
        for key in keys_to_cancel:
            task = self._active_tasks.pop(key)
            task.cancel()

        # Remove from baton
        self._baton.deregister_job(job_id)

        # Remove sheet mapping, renderer, and completion tracking
        self._job_sheets.pop(job_id, None)
        self._job_renderers.pop(job_id, None)
        self._completion_events.pop(job_id, None)
        self._completion_results.pop(job_id, None)

        _logger.info("adapter.job_deregistered", extra={"job_id": job_id})

    # =========================================================================
    # Job Recovery — Step 29: Restart Recovery
    # =========================================================================

    def recover_job(
        self,
        job_id: str,
        sheets: list[Sheet],
        dependencies: dict[int, list[int]],
        checkpoint: CheckpointState,
        *,
        max_cost_usd: float | None = None,
        max_retries: int = 3,
        max_completion: int = 5,
        escalation_enabled: bool = False,
        self_healing_enabled: bool = False,
        prompt_config: PromptConfig | None = None,
        parallel_enabled: bool = False,
    ) -> None:
        """Recover a job from a checkpoint after conductor restart.

        Rebuilds baton state from the persisted CheckpointState. Terminal
        sheets (completed, failed, skipped) keep their status. In-progress
        sheets are reset to PENDING because their musicians died when the
        conductor restarted. Attempt counts are preserved to avoid infinite
        retries.

        Design invariant: Checkpoint is the source of truth. The baton
        rebuilds from checkpoint, not the reverse.

        Args:
            job_id: Unique job identifier.
            sheets: Sheet entities from build_sheets() — same config
                that produced the original job.
            dependencies: Dependency graph {sheet_num: [dep_nums]}.
            checkpoint: Persisted CheckpointState loaded from workspace.
            max_cost_usd: Optional per-job cost limit.
            max_retries: Max normal retry attempts per sheet.
            max_completion: Max completion mode attempts per sheet.
            escalation_enabled: Enter fermata on exhaustion.
            self_healing_enabled: Try self-healing on exhaustion.
            prompt_config: Optional PromptConfig for prompt rendering.
            parallel_enabled: Whether parallel execution is enabled.
        """
        # Store sheets for prompt rendering
        self._job_sheets[job_id] = {s.num: s for s in sheets}

        # Create PromptRenderer if config is available
        if prompt_config is not None:
            from mozart.daemon.baton.prompt import PromptRenderer

            total_sheets = len(sheets)
            total_stages = len({s.movement for s in sheets}) or 1
            self._job_renderers[job_id] = PromptRenderer(
                prompt_config=prompt_config,
                total_sheets=total_sheets,
                total_stages=total_stages,
                parallel_enabled=parallel_enabled,
            )

        # Create completion event
        self._completion_events[job_id] = asyncio.Event()

        # Build SheetExecutionState with recovered statuses and attempt counts
        states: dict[int, SheetExecutionState] = {}
        for sheet in sheets:
            cp_sheet = checkpoint.sheets.get(sheet.num)

            if cp_sheet is not None:
                # Map checkpoint status to baton status.
                # Critical: in_progress sheets are reset to PENDING because
                # their executing musician was killed on restart.
                cp_status = cp_sheet.status.value
                if cp_status == "in_progress":
                    baton_status = BatonSheetStatus.PENDING
                else:
                    baton_status = checkpoint_to_baton_status(cp_status)

                # Carry forward attempt counts to avoid infinite retries
                normal_attempts = cp_sheet.attempt_count
                completion_attempts = cp_sheet.completion_attempts
            else:
                # Sheet not in checkpoint — treat as fresh PENDING
                baton_status = BatonSheetStatus.PENDING
                normal_attempts = 0
                completion_attempts = 0

            state = SheetExecutionState(
                sheet_num=sheet.num,
                instrument_name=sheet.instrument_name,
                max_retries=max_retries,
                max_completion=max_completion,
            )
            state.status = baton_status
            state.normal_attempts = normal_attempts
            state.completion_attempts = completion_attempts

            states[sheet.num] = state

        # Register with baton using the recovered states
        self._baton.register_job(
            job_id,
            states,
            dependencies,
            escalation_enabled=escalation_enabled,
            self_healing_enabled=self_healing_enabled,
        )

        # Set cost limits if configured
        if max_cost_usd is not None:
            self._baton.set_job_cost_limit(job_id, max_cost_usd)

        _logger.info(
            "adapter.job_recovered",
            extra={
                "job_id": job_id,
                "sheet_count": len(sheets),
                "recovered_terminal": sum(
                    1 for s in states.values()
                    if s.status in (
                        BatonSheetStatus.COMPLETED,
                        BatonSheetStatus.FAILED,
                        BatonSheetStatus.SKIPPED,
                    )
                ),
                "recovered_pending": sum(
                    1 for s in states.values()
                    if s.status == BatonSheetStatus.PENDING
                ),
            },
        )

        # Kick the event loop so dispatch_ready runs for recovered sheets
        self._baton.inbox.put_nowait(DispatchRetry())

    def get_sheet(self, job_id: str, sheet_num: int) -> Sheet | None:
        """Get a Sheet entity for a registered job.

        Args:
            job_id: The job identifier.
            sheet_num: The sheet number.

        Returns:
            The Sheet entity, or None if not found.
        """
        job_sheets = self._job_sheets.get(job_id)
        if job_sheets is None:
            return None
        return job_sheets.get(sheet_num)

    # =========================================================================
    # Completion Signaling
    # =========================================================================

    async def wait_for_completion(self, job_id: str) -> bool:
        """Wait until a job reaches terminal state.

        Blocks until all sheets in the job are completed, failed, skipped,
        or cancelled. Used by the manager's _run_job_task to await baton
        execution.

        Args:
            job_id: The job to wait for.

        Returns:
            True if all sheets completed successfully, False if any failed.

        Raises:
            KeyError: If the job is not registered.
        """
        event = self._completion_events.get(job_id)
        if event is None:
            raise KeyError(f"Job '{job_id}' is not registered with the adapter")
        await event.wait()
        return self._completion_results.get(job_id, False)

    def has_completed_sheets(self, job_id: str) -> bool:
        """Check if any sheet in the job reached COMPLETED status.

        F-145: Used by the manager to set completed_new_work after baton
        execution. The zero-work guard for concert chaining needs to know
        whether any sheet completed new work — not just whether all
        sheets succeeded.

        Args:
            job_id: The job to check.

        Returns:
            True if at least one sheet completed, False otherwise.
        """
        job = self._baton._jobs.get(job_id)
        if job is None:
            return False
        return any(
            s.status == BatonSheetStatus.COMPLETED
            for s in job.sheets.values()
        )

    def clear_instrument_rate_limit(
        self,
        instrument: str | None = None,
    ) -> int:
        """Clear instrument rate limit state in the baton core.

        Delegates to ``BatonCore.clear_instrument_rate_limit()``.  Also
        moves WAITING sheets back to PENDING so they can be re-dispatched.

        Args:
            instrument: Instrument name to clear, or ``None`` for all.

        Returns:
            Number of instruments whose rate limit was cleared.
        """
        return self._baton.clear_instrument_rate_limit(instrument)

    def _check_completions(self) -> None:
        """Check all registered jobs for completion and signal waiters.

        Called after every event in the baton's main loop. When a job's
        sheets are all terminal, sets the completion event so
        wait_for_completion() unblocks.
        """
        for job_id, event in list(self._completion_events.items()):
            if event.is_set():
                continue  # Already signaled
            if self._baton.is_job_complete(job_id):
                # Determine success: all sheets COMPLETED (not failed/cancelled)
                job = self._baton._jobs.get(job_id)
                all_success = True
                if job is not None:
                    all_success = all(
                        s.status == BatonSheetStatus.COMPLETED
                        for s in job.sheets.values()
                    )
                self._completion_results[job_id] = all_success
                event.set()
                _logger.info(
                    "adapter.job_complete",
                    extra={
                        "job_id": job_id,
                        "all_success": all_success,
                    },
                )

    # =========================================================================
    # Dispatch Callback — Surface 2
    # =========================================================================

    def _send_dispatch_failure(
        self,
        job_id: str,
        sheet_num: int,
        instrument_name: str,
        error_msg: str,
        state: SheetExecutionState | None = None,
    ) -> None:
        """Send a SheetAttemptResult failure event when dispatch cannot proceed.

        F-152: Without this, backend acquisition failures cause either:
        - Infinite dispatch loops (sheet stays READY, re-dispatched every cycle)
        - Silent deadlocks (sheet set to DISPATCHED but no musician spawned)

        The failure event enters the baton's normal state machine, which handles
        retry decisions, dependent sheet propagation, and terminal state transition.

        Args:
            job_id: The job this sheet belongs to.
            sheet_num: The sheet that failed to dispatch.
            instrument_name: The instrument that was requested.
            error_msg: Description of why dispatch failed.
            state: Optional execution state for accurate attempt number.
        """
        attempt = 1
        if state is not None:
            attempt = state.normal_attempts + state.completion_attempts + 1

        failure = SheetAttemptResult(
            job_id=job_id,
            sheet_num=sheet_num,
            instrument_name=instrument_name,
            attempt=attempt,
            execution_success=False,
            error_classification="E505",
            error_message=error_msg,
        )
        self._baton.inbox.put_nowait(failure)
        _logger.warning(
            "adapter.dispatch.failure_event_sent",
            extra={
                "job_id": job_id,
                "sheet_num": sheet_num,
                "instrument": instrument_name,
                "error": error_msg,
            },
        )

    async def _dispatch_callback(
        self,
        job_id: str,
        sheet_num: int,
        state: SheetExecutionState,
    ) -> None:
        """Dispatch a sheet for execution.

        Called by dispatch_ready() when a sheet is ready to execute.
        Acquires a backend, creates an AttemptContext, and spawns a
        musician task.

        F-152: All early-return paths now send a SheetAttemptResult failure
        event to the baton inbox. This ensures the baton state machine
        handles the failure (retry, propagation, terminal transition)
        instead of leaving the sheet in READY or DISPATCHED forever.

        Args:
            job_id: The job this sheet belongs to.
            sheet_num: The sheet to dispatch.
            state: The sheet's current execution state.
        """
        sheet = self.get_sheet(job_id, sheet_num)
        if sheet is None:
            _logger.error(
                "adapter.dispatch.sheet_not_found",
                extra={"job_id": job_id, "sheet_num": sheet_num},
            )
            self._send_dispatch_failure(
                job_id, sheet_num,
                state.instrument_name,
                f"Sheet {sheet_num} not found in adapter registry",
                state=state,
            )
            return

        if self._backend_pool is None:
            _logger.error(
                "adapter.dispatch.no_backend_pool",
                extra={"job_id": job_id, "sheet_num": sheet_num},
            )
            self._send_dispatch_failure(
                job_id, sheet_num,
                sheet.instrument_name,
                "No backend pool available — adapter not fully initialized",
                state=state,
            )
            return

        try:
            # Acquire backend from pool, passing model from instrument_config
            # if the score author specified one. F-150: this was missing —
            # instrument_config.model was silently ignored at dispatch time.
            model_override = sheet.instrument_config.get("model")
            backend = await self._backend_pool.acquire(
                sheet.instrument_name,
                model=str(model_override) if model_override is not None else None,
                working_directory=sheet.workspace,
            )
        except Exception as exc:
            # F-152: Catch ALL exceptions, not just ValueError/RuntimeError.
            # NotImplementedError (unsupported instrument kind) previously
            # escaped and caused infinite dispatch loops.
            _logger.error(
                "adapter.dispatch.backend_acquire_failed",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                    "instrument": sheet.instrument_name,
                    "error": str(exc),
                },
            )
            self._send_dispatch_failure(
                job_id, sheet_num,
                sheet.instrument_name,
                f"Backend acquisition failed for instrument "
                f"'{sheet.instrument_name}': {exc}",
                state=state,
            )
            return

        # Build attempt context
        attempt_number = state.normal_attempts + state.completion_attempts + 1
        mode = AttemptMode.NORMAL
        completion_suffix: str | None = None

        if state.completion_attempts > 0:
            mode = AttemptMode.COMPLETION
            completion_suffix = (
                "Some validations passed but not all. "
                "Review what's done and complete the remaining work."
            )
        elif state.healing_attempts > 0:
            mode = AttemptMode.HEALING

        context = AttemptContext(
            attempt_number=attempt_number,
            mode=mode,
            completion_prompt_suffix=completion_suffix,
        )

        # Spawn musician task
        task = asyncio.create_task(
            self._musician_wrapper(
                job_id=job_id,
                sheet=sheet,
                backend=backend,
                context=context,
            ),
            name=f"musician-{job_id}-s{sheet_num}",
        )
        self._active_tasks[(job_id, sheet_num)] = task
        task.add_done_callback(
            lambda t: self._on_musician_done(job_id, sheet_num, t)
        )

        _logger.info(
            "adapter.dispatch.spawned",
            extra={
                "job_id": job_id,
                "sheet_num": sheet_num,
                "instrument": sheet.instrument_name,
                "attempt": attempt_number,
                "mode": mode.value,
            },
        )

    async def _musician_wrapper(
        self,
        *,
        job_id: str,
        sheet: Sheet,
        backend: Any,
        context: AttemptContext,
    ) -> None:
        """Wrapper around sheet_task that handles backend release.

        The musician plays once and reports. This wrapper ensures the
        backend is always released, even if the musician crashes.

        Args:
            job_id: The job identifier.
            sheet: The sheet to execute.
            backend: The acquired backend.
            context: The attempt context from the baton.
        """
        try:
            # The baton inbox accepts BatonEvent (union type), but
            # sheet_task is typed to put SheetAttemptResult specifically.
            # This is safe because SheetAttemptResult IS a BatonEvent.
            # We cast to satisfy the invariant Queue type parameter.
            inbox = cast(asyncio.Queue[SheetAttemptResult], self._baton.inbox)

            # Compute job-level totals for template rendering (F-104)
            job_sheets = self._job_sheets.get(job_id, {})
            total_sheets = len(job_sheets)
            # Count distinct movements across all sheets
            total_movements = len({s.movement for s in job_sheets.values()}) or 1

            # Use PromptRenderer if available (full 9-layer pipeline)
            renderer = self._job_renderers.get(job_id)
            pre_rendered: str | None = None
            pre_preamble: str | None = None
            if renderer is not None:
                rendered = renderer.render(sheet, context)
                pre_rendered = rendered.prompt
                pre_preamble = rendered.preamble

            await sheet_task(
                job_id=job_id,
                sheet=sheet,
                backend=backend,
                attempt_context=context,
                inbox=inbox,
                total_sheets=total_sheets,
                total_movements=total_movements,
                rendered_prompt=pre_rendered,
                preamble=pre_preamble,
            )
        finally:
            # Always release the backend
            if self._backend_pool is not None:
                try:
                    await self._backend_pool.release(
                        sheet.instrument_name, backend
                    )
                except Exception:
                    _logger.warning(
                        "adapter.backend_release_failed",
                        extra={
                            "job_id": job_id,
                            "sheet_num": sheet.num,
                        },
                        exc_info=True,
                    )

    def _on_musician_done(
        self,
        job_id: str,
        sheet_num: int,
        task: asyncio.Task[Any],
    ) -> None:
        """Callback when a musician task completes.

        Removes the task from tracking. Any unhandled exceptions are
        logged but do not crash the adapter.

        Args:
            job_id: The job identifier.
            sheet_num: The sheet number.
            task: The completed task.
        """
        self._active_tasks.pop((job_id, sheet_num), None)

        if task.cancelled():
            _logger.debug(
                "adapter.musician.cancelled",
                extra={"job_id": job_id, "sheet_num": sheet_num},
            )
        elif task.exception():
            _logger.error(
                "adapter.musician.exception",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                    "error": str(task.exception()),
                },
            )

    # =========================================================================
    # EventBus Publishing — Surface 5
    # =========================================================================

    async def publish_attempt_result(
        self, result: SheetAttemptResult
    ) -> None:
        """Publish a sheet attempt result to the EventBus.

        Args:
            result: The musician's execution report.
        """
        if self._event_bus is None:
            return

        event = attempt_result_to_observer_event(result)
        try:
            await self._event_bus.publish(event)
        except Exception:
            _logger.warning(
                "adapter.event_publish_failed",
                extra={
                    "job_id": result.job_id,
                    "sheet_num": result.sheet_num,
                },
                exc_info=True,
            )

    async def publish_sheet_skipped(self, event: SheetSkipped) -> None:
        """Publish a sheet skip event to the EventBus.

        Args:
            event: The sheet skip event.
        """
        if self._event_bus is None:
            return

        obs_event = skipped_to_observer_event(event)
        try:
            await self._event_bus.publish(obs_event)
        except Exception:
            _logger.warning(
                "adapter.event_publish_failed",
                extra={
                    "job_id": event.job_id,
                    "sheet_num": event.sheet_num,
                },
                exc_info=True,
            )

    async def publish_job_event(
        self,
        job_id: str,
        event_name: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Publish a job-level event to the EventBus.

        Args:
            job_id: The job identifier.
            event_name: Event name (e.g., "job.started", "job.completed").
            data: Optional event data.
        """
        if self._event_bus is None:
            return

        event: ObserverEvent = {
            "job_id": job_id,
            "sheet_num": 0,
            "event": event_name,
            "data": data or {},
            "timestamp": time.time(),
        }
        try:
            await self._event_bus.publish(event)
        except Exception:
            _logger.warning(
                "adapter.job_event_publish_failed",
                extra={"job_id": job_id, "event": event_name},
                exc_info=True,
            )

    # =========================================================================
    # Main Loop
    # =========================================================================

    def _sync_sheet_status(
        self,
        event: BatonEvent,
    ) -> None:
        """Sync baton sheet status changes to the checkpoint callback.

        Called after each event is handled. Determines which sheet was
        affected by the event and invokes the state_sync_callback with
        the current baton status mapped to checkpoint status.

        Args:
            event: The event that was just processed.
        """
        if self._state_sync_callback is None:
            return

        # Extract job_id and sheet_num from events that affect sheet status
        if isinstance(event, (SheetAttemptResult, SheetSkipped)):
            job_id = event.job_id
            sheet_num = event.sheet_num
        else:
            return

        # Look up current baton status and map to checkpoint status
        state = self._baton.get_sheet_state(job_id, sheet_num)
        if state is None:
            return

        checkpoint_status = baton_to_checkpoint_status(state.status)
        try:
            self._state_sync_callback(job_id, sheet_num, checkpoint_status)
        except Exception:
            _logger.warning(
                "adapter.state_sync_failed",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                },
                exc_info=True,
            )

    async def run(self) -> None:
        """Run the baton's event loop with dispatch integration.

        Processes events from the inbox, updates state, dispatches
        ready sheets, and publishes events to the EventBus.

        Runs until the baton receives a ShutdownRequested event.
        """
        from mozart.daemon.baton.dispatch import dispatch_ready

        self._running = True
        _logger.info("adapter.started")

        try:
            while not self._baton._shutting_down:
                event = await self._baton.inbox.get()
                await self._baton.handle_event(event)

                # Step 29: Sync state changes to checkpoint
                self._sync_sheet_status(event)

                # Dispatch ready sheets after every event
                config = self._baton.build_dispatch_config(
                    max_concurrent_sheets=self._max_concurrent_sheets,
                )
                await dispatch_ready(
                    self._baton, config, self._dispatch_callback
                )

                # Check for job completions after dispatch
                self._check_completions()

        except asyncio.CancelledError:
            _logger.info("adapter.cancelled")
            raise
        finally:
            self._running = False
            _logger.info("adapter.stopped")

    async def shutdown(self) -> None:
        """Gracefully shut down the adapter.

        Cancels all active musician tasks and closes the backend pool.
        """
        # Cancel all active tasks
        for task in self._active_tasks.values():
            task.cancel()
        self._active_tasks.clear()

        # Close backend pool
        if self._backend_pool is not None:
            try:
                await self._backend_pool.close_all()
            except Exception:
                _logger.warning("adapter.pool_close_failed", exc_info=True)

        _logger.info("adapter.shutdown_complete")
