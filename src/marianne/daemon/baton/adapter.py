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

7. **Lifecycle** (Surface 8)
   The adapter is always initialized by the conductor at startup

Design decisions:
- Checkpoint is source of truth. Baton rebuilds from checkpoint on restart.
- Save checkpoint FIRST, then update baton state (prevents re-execution).
- The adapter does NOT own the baton's main loop — the manager runs it.
- Concert support: sequential score submission (option 1 from wiring analysis).

See: ``workspaces/v1-beta-v3/movement-2/step-28-wiring-analysis.md``
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from marianne.core.constants import VALIDATION_PASS_RATE_KEY
from marianne.core.sheet import Sheet
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import (
    DispatchRetry,
    SheetAttemptResult,
    SheetSkipped,
    StaleCheck,
)
from marianne.daemon.baton.musician import sheet_task
from marianne.daemon.baton.state import (
    AttemptContext,
    AttemptMode,
    BatonSheetStatus,
    SheetExecutionState,
)
from marianne.daemon.technique_router import TechniqueRouter
from marianne.utils.process import safe_killpg as _safe_killpg

if TYPE_CHECKING:
    from marianne.core.checkpoint import CheckpointState
    from marianne.core.config.job import PromptConfig
    from marianne.core.config.workspace import CrossSheetConfig
    from marianne.daemon.baton.backend_pool import BackendPool
    from marianne.daemon.baton.prompt import PromptRenderer
    from marianne.daemon.event_bus import EventBus
    from marianne.daemon.types import ObserverEvent

from marianne.core.logging import get_logger

_logger = get_logger("daemon.baton.adapter")

# Type alias for the persist callback.
# Called after significant baton state transitions to persist the
# CheckpointState to the registry for crash recovery.
# Phase 2: replaces the old StateSyncCallback that mapped between
# two state representations. Now there's one shared SheetState —
# persistence is all that's needed.
PersistCallback = Callable[[str], None]

# Backward compat aliases — tests may import these
StateSyncCallback = Callable[[str, int, str, SheetExecutionState | None], None]

# Phase 1 process lifecycle: grace window between SIGTERM and SIGKILL when
# preempt-killing process groups for a deregistering job. The backend's own
# finally block uses the same interval independently (belt and suspenders).
# See docs/specs/2026-04-16-process-lifecycle-design.md (Change 3).
_KILL_GRACE_SECONDS = 2.0


# Phase 2: identity mappings — kept for test backward compat
_BATON_TO_CHECKPOINT: dict[BatonSheetStatus, str] = {
    s: s.value for s in BatonSheetStatus
}
_CHECKPOINT_TO_BATON: dict[str, BatonSheetStatus] = {
    s.value: s for s in BatonSheetStatus
}


def baton_to_checkpoint_status(status: BatonSheetStatus) -> str:
    """Identity mapping — Phase 2 unified the enums.

    Kept for backward compatibility with tests that import this function.
    Since BatonSheetStatus IS SheetStatus, this is just status.value.
    """
    return status.value


def checkpoint_to_baton_status(status: str) -> BatonSheetStatus:
    """Reconstruct SheetStatus from string — Phase 2 unified the enums.

    Kept for backward compatibility with tests that import this function.
    """
    return BatonSheetStatus(status)


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
            VALIDATION_PASS_RATE_KEY: result.validation_pass_rate,
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
        raw_model = sheet.instrument_config.get("model")
        states[sheet.num] = SheetExecutionState(
            sheet_num=sheet.num,
            instrument_name=sheet.instrument_name,
            model=str(raw_model) if raw_model is not None else None,
            max_retries=max_retries,
            max_completion=max_completion,
            fallback_chain=list(sheet.instrument_fallbacks),
            sheet_timeout_seconds=sheet.timeout_seconds,
        )
    return states


def extract_dependencies(config: Any) -> dict[int, list[int]]:
    """Extract baton-compatible dependency graph from a JobConfig.

    The baton expects: ``{sheet_num: [dep_sheet_num, ...]}``

    When ``config.sheet.dependencies`` is set (non-empty), it is used
    as the authoritative DAG.  Stage-level dependencies are expanded to
    sheet-level: if stage S has a fan-out of 3 (sheets 4,5,6) and
    depends on stage T (sheets 1,2,3), each of 4/5/6 depends on all of
    1/2/3.  Stages not listed in the dependencies map are treated as
    having no dependencies (independent).

    When ``config.sheet.dependencies`` is empty or absent, falls back to
    the legacy linear chain: all sheets in stage N+1 depend on all sheets
    in stage N.

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

    raw_deps = getattr(config.sheet, "dependencies", None)
    yaml_deps: dict[int, list[int]] = (
        raw_deps if isinstance(raw_deps, dict) else {}
    )

    if yaml_deps:
        # Check if dependencies are already expanded to sheet-level.
        # The config model expands stage-level deps to sheet-level at parse
        # time (SheetConfig._expand_fan_out) and clears fan_out={} to prevent
        # re-expansion.  When that has happened, the dep keys are sheet nums
        # (potentially > total_stages) and the values reference sheet nums.
        # We must NOT re-expand already-expanded deps — that produces wrong
        # results (GH#167 variant: double expansion).
        fan_out = getattr(config.sheet, "fan_out", None)
        already_expanded = not fan_out  # fan_out cleared → deps are sheet-level

        if already_expanded:
            _logger.debug(
                "extract_dependencies.using_pre_expanded",
                extra={
                    "sheet_count": total,
                    "dep_entries": len(yaml_deps),
                },
            )
            # Deps are already sheet-level.  Ensure every sheet has an entry
            # (sheets not in the map have no dependencies).
            deps: dict[int, list[int]] = {}
            for num in range(1, total + 1):
                deps[num] = list(yaml_deps.get(num, []))
            return deps

        # Dependencies are still stage-level (fan_out not yet applied, or
        # no fan-out declared).  Expand each stage-level dep to sheet level.
        _logger.debug(
            "extract_dependencies.expanding_stage_deps",
            extra={"stage_count": len(stage_sheets), "dep_edges": len(yaml_deps)},
        )
        deps = {}
        for stage, sheet_nums in stage_sheets.items():
            stage_deps: list[int] = yaml_deps.get(stage, [])
            # Expand: replace each dep-stage with ALL sheets in that stage
            expanded: list[int] = []
            for dep_stage in stage_deps:
                expanded.extend(stage_sheets.get(dep_stage, [dep_stage]))
            for sn in sheet_nums:
                deps[sn] = list(expanded)
        return deps

    # Fallback: linear chain (legacy behavior for scores without
    # explicit dependencies).
    _logger.debug(
        "extract_dependencies.linear_fallback",
        extra={"stage_count": len(stage_sheets)},
    )
    sorted_stages = sorted(stage_sheets.keys())
    deps = {}

    for i, stage in enumerate(sorted_stages):
        if i == 0:
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
        persist_callback: PersistCallback | None = None,
    ) -> None:
        """Initialize the BatonAdapter.

        Args:
            event_bus: Optional EventBus for publishing events to subscribers.
            max_concurrent_sheets: Global concurrency ceiling for dispatch.
            state_sync_callback: Deprecated — kept for backward compat.
                Phase 2 uses persist_callback instead.
            persist_callback: Called with job_id after significant state
                transitions (terminal, dispatch) to persist CheckpointState
                to the registry. Replaces the sync layer.
        """
        from marianne.daemon.baton.timer import TimerWheel

        # Shared inbox breaks the circular dependency: TimerWheel needs
        # the inbox to deliver fired events, BatonCore needs the timer
        # to schedule them. Create the inbox first, pass to both.
        inbox: asyncio.Queue[Any] = asyncio.Queue()
        self._timer_wheel = TimerWheel(inbox)
        self._baton = BatonCore(timer=self._timer_wheel, inbox=inbox)
        self._event_bus = event_bus
        self._max_concurrent_sheets = max_concurrent_sheets
        self._persist_callback = persist_callback
        # Deprecated compat attributes — tests set/read these directly
        self._state_sync_callback = state_sync_callback
        self._synced_status: dict[tuple[str, int], str] = {}

        # Job → Sheet mapping for prompt rendering
        self._job_sheets: dict[str, dict[int, Sheet]] = {}

        # Active musician tasks: (job_id, sheet_num) → Task
        self._active_tasks: dict[tuple[str, int], asyncio.Task[Any]] = {}

        # Phase 1 process lifecycle: in-memory PID/PGID tracking per
        # dispatched sheet. Populated by the backend's
        # ``_on_process_group_spawned`` callback (wired by
        # ``_musician_wrapper``), cleared by the wrapper's finally and by
        # ``_on_musician_done``. Read by ``deregister_job`` to drive
        # preemptive killpg. In-memory only — lost on conductor restart,
        # which is Phase 4 recovery's job.
        #
        # Phase 3 replaces this with SheetState.process_pid/pgid; this
        # dict may then be kept as a fast-path cache or removed entirely.
        # See docs/specs/2026-04-16-process-lifecycle-design.md (Change 3).
        self._active_pids: dict[tuple[str, int], tuple[int, int]] = {}

        # BackendPool — injected via set_backend_pool()
        self._backend_pool: BackendPool | None = None

        # Per-job PromptRenderer — created when prompt_config is provided
        self._job_renderers: dict[str, PromptRenderer] = {}

        # Per-job CrossSheetConfig — enables cross-sheet context (F-210)
        self._job_cross_sheet: dict[str, CrossSheetConfig] = {}

        # Per-job completion events — set when all sheets reach terminal state
        self._completion_events: dict[str, asyncio.Event] = {}

        # Per-job TechniqueRouter — created when the job declares techniques.
        # The router classifies musician output (prose, code, tool calls, A2A)
        # and is consumed by sheet_task() at dispatch time. Jobs without
        # techniques have no router (backward compat — classification skipped).
        self._job_routers: dict[str, TechniqueRouter] = {}

        # Per-job technique declarations — stored when the job declares
        # techniques so the dispatch loop can resolve them per-sheet into a
        # manifest for prompt injection. Jobs without techniques are absent
        # from this map (backward compat — resolution skipped, manifest None).
        self._job_techniques: dict[str, dict[str, Any]] = {}

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
        cross_sheet: CrossSheetConfig | None = None,
        pacing_seconds: float = 0.0,
        live_sheets: dict[int, SheetExecutionState] | None = None,
        techniques: dict[str, Any] | None = None,
    ) -> None:
        """Register a job with the baton for event-driven execution.

        Converts Sheet entities to SheetExecutionState and registers
        them with the baton's sheet registry.

        Phase 2: when ``live_sheets`` is provided, uses those SheetState
        objects directly instead of creating new ones. This ensures the
        baton writes to the same objects that live in ``_live_states``,
        eliminating the need for a sync layer.

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
            cross_sheet: Optional CrossSheetConfig for cross-sheet context
                (F-210). When provided, the adapter collects previous sheet
                outputs and workspace files at dispatch time.
        """
        # Store sheets for prompt rendering at dispatch time
        self._job_sheets[job_id] = {s.num: s for s in sheets}

        # Store cross-sheet config (F-210)
        if cross_sheet is not None:
            self._job_cross_sheet[job_id] = cross_sheet

        # Create PromptRenderer if config is available (F-104)
        if prompt_config is not None:
            from marianne.daemon.baton.prompt import PromptRenderer

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

        # Stage 2a: instantiate a TechniqueRouter when techniques are declared.
        # The router is shared across all sheets in the job (it is stateless —
        # pure regex classification, no per-sheet setup). Jobs that declare
        # zero techniques (or omit the argument entirely) do not get a
        # router, so sheet_task() skips classification and output_kind stays
        # None. This preserves backward compatibility for scores written
        # before techniques existed.
        if techniques:
            self._job_routers[job_id] = TechniqueRouter()
            self._job_techniques[job_id] = techniques

        # Phase 2: use live_sheets (shared with _live_states) when provided.
        # This makes the baton write directly to the same SheetState objects
        # the manager serves via get_job_status — no sync layer needed.
        if live_sheets is not None:
            # Enrich existing SheetState objects with baton scheduling fields
            for sheet in sheets:
                s = live_sheets.get(sheet.num)
                if s is not None:
                    s.instrument_name = sheet.instrument_name
                    raw_m = sheet.instrument_config.get("model")
                    if raw_m is not None:
                        s.model = str(raw_m)
                    s.max_retries = max_retries
                    s.max_completion = max_completion
                    s.fallback_chain = list(sheet.instrument_fallbacks)
                    s.sheet_timeout_seconds = sheet.timeout_seconds
            states = live_sheets
        else:
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
            pacing_seconds=pacing_seconds,
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

    def get_router(self, job_id: str) -> TechniqueRouter | None:
        """Return the per-job TechniqueRouter, or None if not activated.

        The router is created by :meth:`register_job` when the job declares
        one or more techniques. Jobs without techniques (the default for
        legacy scores) have no router and classification is skipped.

        Args:
            job_id: The job to look up.

        Returns:
            The job's TechniqueRouter if one was activated, else None.
        """
        return self._job_routers.get(job_id)

    def _kill_active_pgroups(self, job_id: str) -> None:
        """Preempt-kill process groups for all active sheets of a job.

        Phase 1 item 5: sends SIGTERM to each tracked ``pgid`` synchronously,
        then schedules a follow-up SIGKILL after a grace window. The
        backend's own finally block performs the same escalation
        independently (belt and suspenders: preempt for responsiveness,
        ``finally`` for correctness). Both kill paths are idempotent.

        Daemon-own-group guard: never killpg a pgid matching
        ``os.getpgid(0)``. If ``start_new_session`` silently failed at
        spawn the subprocess could land in the daemon's own group;
        killing it would take the daemon down.

        Entries for this job are popped from ``_active_pids`` as they are
        processed so follow-on ``deregister_job`` work cannot re-kill.

        Args:
            job_id: The job whose active sheets should have their process
                groups torn down.
        """
        keys = [k for k in self._active_pids if k[0] == job_id]
        if not keys:
            return

        try:
            daemon_pgid: int | None = os.getpgid(0)
        except OSError:
            daemon_pgid = None

        sigterm_pgids: list[int] = []
        for key in keys:
            entry = self._active_pids.pop(key, None)
            if entry is None:
                continue
            _pid, pgid = entry
            if pgid == daemon_pgid:
                # Suspicious: the spawned process shares our group.
                # Refuse to kill — the spawn-site safety check should have
                # caught this at spawn time, but guard here too.
                _logger.warning(
                    "adapter.deregister.pgid_matches_daemon",
                    extra={
                        "job_id": job_id,
                        "sheet_num": key[1],
                        "pgid": pgid,
                    },
                )
                continue
            try:
                if _safe_killpg(pgid, signal.SIGTERM, context="adapter.deregister_sigterm"):
                    sigterm_pgids.append(pgid)
            except (ProcessLookupError, PermissionError):
                # Process already gone (natural exit, prior kill) — OK.
                pass

        if not sigterm_pgids:
            return

        # Schedule SIGKILL escalation after grace. Fire-and-forget: if the
        # processes already exited (most likely — SIGTERM usually wins),
        # killpg raises ProcessLookupError, which we swallow.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — ``deregister_job`` was called from a
            # non-async context (tests, shutdown). SIGTERM alone suffices
            # for those paths; the backend's finally will escalate.
            return

        def _sigkill_pgroups(
            pgids: list[int] = sigterm_pgids,
            _daemon: int | None = daemon_pgid,
        ) -> None:
            for pgid in pgids:
                if pgid == _daemon:
                    continue
                try:
                    _safe_killpg(pgid, signal.SIGKILL, context="adapter.deregister_sigkill")
                except (ProcessLookupError, PermissionError):
                    pass

        loop.call_later(_KILL_GRACE_SECONDS, _sigkill_pgroups)

    def deregister_job(self, job_id: str) -> None:
        """Remove a job from the adapter and baton.

        Cleans up all per-job state including active tasks.

        Args:
            job_id: The job to remove.
        """
        # Phase 1 item 5: preempt-kill subprocess groups BEFORE cancelling
        # asyncio tasks. Task.cancel() delivers CancelledError through the
        # `await proc.communicate()` in the backend, whose finally block
        # will then kill the same group — but only when the await actually
        # wakes. A process blocked in an uninterruptible syscall would
        # leave the finally stuck. Preempt-SIGTERM forces the process to
        # exit, which unblocks communicate, which runs finally. Belt and
        # suspenders.
        self._kill_active_pgroups(job_id)

        # Cancel active musician tasks for this job
        keys_to_cancel = [
            key for key in self._active_tasks if key[0] == job_id
        ]
        for key in keys_to_cancel:
            task = self._active_tasks.pop(key)
            task.cancel(msg=f"deregister_job({job_id})")
        if keys_to_cancel:
            _logger.info(
                "adapter.deregister_job.tasks_cancelled",
                extra={
                    "job_id": job_id,
                    "cancelled_sheets": [k[1] for k in keys_to_cancel],
                },
            )

        # Remove from baton
        self._baton.deregister_job(job_id)

        # Remove sheet mapping, renderer, cross-sheet config, and completion tracking
        self._job_sheets.pop(job_id, None)
        self._job_renderers.pop(job_id, None)
        self._job_cross_sheet.pop(job_id, None)
        self._completion_events.pop(job_id, None)
        self._completion_results.pop(job_id, None)
        self._job_routers.pop(job_id, None)
        self._job_techniques.pop(job_id, None)

        # Clean up vestigial _synced_status entries (Phase 2: sync layer
        # removed but dict retained for compatibility). Defensive cleanup
        # prevents memory leaks if anything accidentally populates it.
        if hasattr(self, "_synced_status") and self._synced_status:
            self._synced_status = {
                k: v for k, v in self._synced_status.items() if k[0] != job_id
            }

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
        cross_sheet: CrossSheetConfig | None = None,
        pacing_seconds: float = 0.0,
        live_sheets: dict[int, SheetExecutionState] | None = None,
        techniques: dict[str, Any] | None = None,
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
            cross_sheet: Optional CrossSheetConfig for cross-sheet context (F-210).
        """
        # Store sheets for prompt rendering
        self._job_sheets[job_id] = {s.num: s for s in sheets}

        # Store cross-sheet config (F-210)
        if cross_sheet is not None:
            self._job_cross_sheet[job_id] = cross_sheet

        # Create PromptRenderer if config is available
        if prompt_config is not None:
            from marianne.daemon.baton.prompt import PromptRenderer

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

        # Restore technique state for resumed jobs so Stage 1 resolution and
        # Stage 2a classification survive a conductor restart. Without this
        # the resumed job would run without its declared techniques — the
        # manifest would never inject and the router would never classify.
        if techniques:
            self._job_routers[job_id] = TechniqueRouter()
            self._job_techniques[job_id] = techniques

        # Build SheetExecutionState with recovered statuses and attempt counts
        states: dict[int, SheetExecutionState] = {}
        for sheet in sheets:
            cp_sheet = checkpoint.sheets.get(sheet.num)

            if cp_sheet is not None:
                # On restart, active/transient sheets reset to PENDING:
                # - in_progress/dispatched: musician was killed on restart
                # - waiting: rate limit timers lost on restart
                # - retry_scheduled: retry timers lost on restart
                # - fermata: re-evaluate escalation on restart
                # Terminal sheets (completed, failed, skipped, cancelled)
                # keep their status. Pending/ready stay as-is.
                _RESET_ON_RESTART = frozenset({
                    BatonSheetStatus.IN_PROGRESS,
                    BatonSheetStatus.DISPATCHED,
                    BatonSheetStatus.WAITING,
                    BatonSheetStatus.RETRY_SCHEDULED,
                    BatonSheetStatus.FERMATA,
                })
                baton_status = (
                    BatonSheetStatus.PENDING
                    if cp_sheet.status in _RESET_ON_RESTART
                    else cp_sheet.status
                )

                # Carry forward attempt counts to avoid infinite retries
                normal_attempts = cp_sheet.attempt_count
                completion_attempts = cp_sheet.completion_attempts
            else:
                # Sheet not in checkpoint — treat as fresh PENDING
                baton_status = BatonSheetStatus.PENDING
                normal_attempts = 0
                completion_attempts = 0

            # Phase 2: update the live SheetState in-place when available,
            # so the baton operates on the same objects as _live_states.
            if live_sheets is not None and sheet.num in live_sheets:
                state = live_sheets[sheet.num]
            else:
                raw_model = sheet.instrument_config.get("model")
                state = SheetExecutionState(
                    sheet_num=sheet.num,
                    instrument_name=sheet.instrument_name,
                    model=str(raw_model) if raw_model is not None else None,
                )

            # Always populate instrument identity from the Sheet entity.
            # The checkpoint may have instrument_name=None for sheets that
            # were never dispatched (e.g., dependency-cascaded failures).
            state.instrument_name = sheet.instrument_name
            raw_model = sheet.instrument_config.get("model")
            if raw_model is not None:
                state.model = str(raw_model)
            state.max_retries = max_retries
            state.max_completion = max_completion
            state.fallback_chain = list(sheet.instrument_fallbacks)
            state.sheet_timeout_seconds = sheet.timeout_seconds
            state.status = baton_status
            state.normal_attempts = normal_attempts
            state.completion_attempts = completion_attempts
            # Reset instrument fallback position for non-terminal sheets.
            # A recovered/resumed sheet must start from the primary
            # instrument, not stay stuck on whatever fallback it died on.
            # Terminal sheets (COMPLETED, SKIPPED) keep their instrument
            # history for diagnostics.
            _TERMINAL = {
                BatonSheetStatus.COMPLETED,
                BatonSheetStatus.SKIPPED,
                BatonSheetStatus.CANCELLED,
            }
            if baton_status not in _TERMINAL:
                state.current_instrument_index = 0

            states[sheet.num] = state

        # Register with baton using the recovered states
        self._baton.register_job(
            job_id,
            states,
            dependencies,
            escalation_enabled=escalation_enabled,
            self_healing_enabled=self_healing_enabled,
            pacing_seconds=pacing_seconds,
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
    # =========================================================================
    # Completion Mode Prompt
    # =========================================================================

    @staticmethod
    def _build_completion_suffix(state: SheetExecutionState) -> str:
        """Build a specific completion suffix from the last attempt's results.

        Instead of the generic "some validations passed but not all," this
        extracts which validations failed from the last attempt's result
        so the agent knows exactly what to fix. Without this specificity,
        agents produce the same partial output every completion attempt.

        Args:
            state: The sheet's execution state with attempt_results.

        Returns:
            Completion prompt suffix with specific failure details.
        """
        base = (
            "Some validations passed but not all. "
            "Review what's done and complete the remaining work."
        )

        # Try to extract specific failure details from the last attempt
        if not state.attempt_results:
            return base

        last = state.attempt_results[-1]
        details = getattr(last, "validation_details", None)
        if not details or not isinstance(details, dict):
            return base

        # Build a specific message listing what failed
        parts = [base, "", "Specifically, these validations FAILED:"]

        # The validation_details dict may have structured results
        # from the ValidationEngine, or simple pass/fail counts.
        # Extract what we can.
        failed_count = details.get("failed", 0)
        passed_count = details.get("passed", 0)
        if failed_count > 0:
            parts.append(
                f"  {passed_count} passed, {failed_count} failed "
                f"(pass rate: {details.get('pass_percentage', 0):.0f}%)"
            )

        # If the ValidationEngine included per-rule results, show them
        results = details.get("results")
        if isinstance(results, list):
            for r in results:
                if isinstance(r, dict) and not r.get("passed", True):
                    desc = r.get("description", "unknown check")
                    path = r.get("path", "")
                    pattern = r.get("pattern", "")
                    msg = f"  - FAILED: {desc}"
                    if path:
                        msg += f" (path: {path})"
                    if pattern:
                        msg += f" (expected: {pattern})"
                    parts.append(msg)

        parts.append("")
        parts.append(
            "Focus on fixing the FAILED validations above. "
            "Do not redo work that already passed."
        )

        return "\n".join(parts)

    # Cross-Sheet Context — F-210
    # =========================================================================

    def _collect_cross_sheet_context(
        self,
        job_id: str,
        current_sheet_num: int,
    ) -> tuple[dict[int, str], dict[str, str]]:
        """Collect cross-sheet context for a sheet about to be dispatched.

        Reads completed sheets' stdout from baton state and workspace files
        matching capture_files patterns. This replicates the legacy runner's
        ContextBuildingMixin._populate_cross_sheet_context() for the baton path.

        Args:
            job_id: The job this sheet belongs to.
            current_sheet_num: The sheet about to be dispatched.

        Returns:
            Tuple of (previous_outputs, previous_files).
            previous_outputs: {sheet_num: stdout_tail} from completed sheets.
            previous_files: {file_path: content} from captured workspace files.
        """
        import glob as glob_module
        from pathlib import Path

        previous_outputs: dict[int, str] = {}
        previous_files: dict[str, str] = {}

        cross_sheet = self._job_cross_sheet.get(job_id)
        if cross_sheet is None:
            return previous_outputs, previous_files

        # --- Auto-capture stdout from completed sheets ---
        if cross_sheet.auto_capture_stdout:
            job_state = self._baton._jobs.get(job_id)
            if job_state is not None:
                # Determine lookback window
                if cross_sheet.lookback_sheets > 0:
                    start_sheet = max(
                        1, current_sheet_num - cross_sheet.lookback_sheets
                    )
                else:
                    start_sheet = 1

                max_chars = cross_sheet.max_output_chars

                for prev_num in range(start_sheet, current_sheet_num):
                    prev_state = job_state.sheets.get(prev_num)
                    if prev_state is None:
                        continue

                    # F-251: Inject [SKIPPED] placeholder for skipped
                    # upstream sheets (#120 parity with legacy runner).
                    # Fan-in prompts see explicit gaps instead of silent
                    # omissions.
                    if prev_state.status == BatonSheetStatus.SKIPPED:
                        previous_outputs[prev_num] = "[SKIPPED]"
                        continue

                    # Only collect stdout from completed sheets
                    if prev_state.status != BatonSheetStatus.COMPLETED:
                        continue
                    # Get stdout from the last successful attempt
                    stdout = self._get_completed_stdout(prev_state)
                    if stdout:
                        if len(stdout) > max_chars:
                            stdout = stdout[:max_chars] + "\n... [truncated]"
                        previous_outputs[prev_num] = stdout

        # --- Capture files from workspace ---
        if cross_sheet.capture_files:
            job_sheets = self._job_sheets.get(job_id, {})
            # Use the current sheet's workspace for pattern expansion
            current_sheet = job_sheets.get(current_sheet_num)
            workspace = current_sheet.workspace if current_sheet else None
            if workspace is not None:
                max_chars = cross_sheet.max_output_chars
                template_vars = {
                    "workspace": str(workspace),
                    "sheet_num": current_sheet_num,
                }
                for pattern in cross_sheet.capture_files:
                    try:
                        expanded = pattern
                        for var, val in template_vars.items():
                            expanded = expanded.replace(
                                f"{{{{ {var} }}}}", str(val)
                            )
                            expanded = expanded.replace(
                                f"{{{{{var}}}}}", str(val)
                            )
                        if not Path(expanded).is_absolute():
                            expanded = str(workspace / expanded)
                        for file_path in glob_module.glob(expanded):
                            path = Path(file_path)
                            if path.is_file():
                                try:
                                    content = path.read_text(encoding="utf-8")
                                    # F-250: Redact credentials BEFORE
                                    # truncation. Workspace files may contain
                                    # API keys written by agents — redact
                                    # before injecting into prompts.
                                    from marianne.utils.credential_scanner import (
                                        redact_credentials,
                                    )

                                    content = (
                                        redact_credentials(content) or content
                                    )
                                    if len(content) > max_chars:
                                        content = (
                                            content[:max_chars]
                                            + "\n... [truncated]"
                                        )
                                    previous_files[str(path)] = content
                                except (OSError, UnicodeDecodeError) as e:
                                    _logger.warning(
                                        "adapter.cross_sheet.file_read_error",
                                        extra={
                                            "path": str(path),
                                            "error": str(e),
                                        },
                                    )
                    except Exception as e:
                        _logger.warning(
                            "adapter.cross_sheet.pattern_error",
                            extra={"pattern": pattern, "error": str(e)},
                        )

        return previous_outputs, previous_files

    @staticmethod
    def _get_completed_stdout(state: SheetExecutionState) -> str:
        """Extract stdout_tail from the last successful attempt of a sheet.

        Walks attempt_results in reverse to find the most recent successful
        attempt and returns its stdout_tail.

        Args:
            state: The sheet's execution state.

        Returns:
            The stdout_tail from the last successful attempt, or empty string.
        """
        for result in reversed(state.attempt_results):
            if result.execution_success and result.stdout_tail:
                return str(result.stdout_tail)
        return ""

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

    def _persist_dirty_jobs(self) -> None:
        """Persist all jobs that have dirty state to the registry.

        Phase 2: replaces the sync layer. The baton writes directly to
        SheetState objects in _live_states. This method tells the manager
        to serialize and save the CheckpointState for each active job.
        """
        if self._persist_callback is None:
            return
        for job_id in self._baton._jobs:
            try:
                self._persist_callback(job_id)
            except Exception:
                _logger.warning(
                    "adapter.persist_failed",
                    extra={"job_id": job_id},
                    exc_info=True,
                )

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
                sheet_statuses = {
                    sn: s.status.value
                    for sn, s in job.sheets.items()
                } if job else {}
                _logger.info(
                    "adapter.job_complete",
                    extra={
                        "job_id": job_id,
                        "all_success": all_success,
                        "sheet_statuses": sheet_statuses,
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
                state.instrument_name or "",
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

        # Use the execution state's instrument_name, not the Sheet entity's.
        # After instrument fallback, state.instrument_name reflects the
        # fallback instrument while sheet.instrument_name still has the
        # original. Without this, the baton thinks it switched instruments
        # but the backend pool acquires the original instrument's backend.
        effective_instrument = state.instrument_name or ""

        try:
            # Acquire backend from pool, passing model from the execution state.
            # F-150: instrument_config.model was silently ignored at dispatch.
            # GH#337: read model from state.model (not sheet.instrument_config)
            # so that after advance_fallback clears the model, the fallback
            # instrument acquires a backend with its profile's default_model
            # instead of inheriting the primary's (possibly incompatible) model.
            model_override = state.model
            backend = await self._backend_pool.acquire(
                effective_instrument,
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
                    "instrument": effective_instrument,
                    "error": str(exc),
                },
            )
            self._send_dispatch_failure(
                job_id, sheet_num,
                effective_instrument,
                f"Backend acquisition failed for instrument "
                f"'{effective_instrument}': {exc}",
                state=state,
            )
            return

        # Clear stale validation/error details from previous attempts.
        # Without this, status display shows old validation errors for the
        # current attempt's failure (misreporting).
        state.validation_passed = None
        state.validation_details = None
        state.error_message = None
        state.error_code = None

        # Build attempt context
        attempt_number = state.normal_attempts + state.completion_attempts + 1
        mode = AttemptMode.NORMAL
        completion_suffix: str | None = None

        if state.completion_attempts > 0 and state.can_complete:
            mode = AttemptMode.COMPLETION
            # Build a specific completion suffix that tells the agent exactly
            # which validations failed. Without this, the agent produces the
            # same partial output every completion attempt because it has no
            # feedback about what specifically is missing.
            completion_suffix = self._build_completion_suffix(state)
        elif state.healing_attempts > 0:
            mode = AttemptMode.HEALING

        # F-210: Collect cross-sheet context before building AttemptContext.
        # This populates previous_outputs and previous_files from completed
        # sheets' stdout and workspace file patterns, matching the legacy
        # runner's ContextBuildingMixin._populate_cross_sheet_context().
        prev_outputs, prev_files = self._collect_cross_sheet_context(
            job_id, sheet_num
        )

        context = AttemptContext(
            attempt_number=attempt_number,
            mode=mode,
            completion_prompt_suffix=completion_suffix,
            previous_outputs=prev_outputs,
            previous_files=prev_files,
        )

        # Resolve techniques for this sheet's phase before prompt rendering.
        # Produces a manifest AND skill documents for prompt injection.
        # Phase is derived from the sheet's movement number (stringified).
        # Scores that declare techniques with phases=["all"] match regardless;
        # phase-scoped techniques match only their declared movement.
        #
        # Skill-kind techniques auto-discover their documents from known
        # locations (~/.marianne/techniques/, .marianne/techniques/, or
        # config.path) and inject the content as skill-category items.
        # This makes `techniques:` a shorthand injection — name the
        # technique, content gets wired automatically.
        technique_manifest: str | None = None
        technique_skill_docs: list[str] = []
        job_techniques = self._job_techniques.get(job_id)
        if job_techniques:
            from marianne.daemon.baton.techniques import resolve_techniques_for_sheet

            phase = str(sheet.movement) if sheet.movement else str(sheet_num)
            resolved = resolve_techniques_for_sheet(job_techniques, phase)
            if resolved.manifest:
                technique_manifest = resolved.manifest
            # Collect discovered skill documents for injection
            for _tech_name, doc_content in resolved.skill_docs.items():
                technique_skill_docs.append(doc_content)

        # Spawn musician task
        task = asyncio.create_task(
            self._musician_wrapper(
                job_id=job_id,
                sheet=sheet,
                backend=backend,
                context=context,
                effective_instrument=effective_instrument,
                technique_manifest=technique_manifest,
                technique_skill_docs=technique_skill_docs,
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
        effective_instrument: str | None = None,
        technique_manifest: str | None = None,
        technique_skill_docs: list[str] | None = None,
    ) -> None:
        """Wrapper around sheet_task that handles backend release.

        The musician plays once and reports. This wrapper ensures the
        backend is always released, even if the musician crashes.

        Args:
            job_id: The job identifier.
            sheet: The sheet to execute.
            backend: The acquired backend.
            context: The attempt context from the baton.
            effective_instrument: The instrument name to use for acquire/
                release and attempt result reporting. After instrument
                fallback, this differs from sheet.instrument_name.
            technique_manifest: Optional technique manifest text resolved
                by the dispatch loop for this sheet's phase. Passed through
                to PromptRenderer.render() for Stage 1 prompt injection.
        """
        # Resolve the instrument name for this execution before try/finally.
        # After fallback, effective_instrument differs from sheet.instrument_name.
        # Must be bound before try so it's always available in finally for release.
        actual_instrument = effective_instrument or sheet.instrument_name

        # Phase 1 item 4: wire the per-dispatch process-group callback so
        # _active_pids captures (pid, pgid) at subprocess spawn time. The
        # closure binds (job_id, sheet.num) at wire time so backend reuse
        # (same backend instance serving different sheets sequentially)
        # cannot smear PID/PGID across sheet keys. HTTP backends do not
        # spawn subprocesses and lack this slot — feature-detect.
        if hasattr(backend, "_on_process_group_spawned"):
            _jid = job_id
            _snum = sheet.num

            def _track_process(pid: int, pgid: int) -> None:
                self._active_pids[(_jid, _snum)] = (pid, pgid)

            backend._on_process_group_spawned = _track_process

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
                rendered = renderer.render(
                    sheet, context,
                    technique_manifest=technique_manifest,
                    technique_skill_docs=technique_skill_docs,
                )
                pre_rendered = rendered.prompt
                pre_preamble = rendered.preamble

            # Resolve profile pricing from instrument registry (F-180)
            cost_input: float | None = None
            cost_output: float | None = None
            if self._backend_pool is not None:
                try:
                    registry = getattr(self._backend_pool, "_registry", None)
                    if registry is not None:
                        profile = registry.get(actual_instrument)
                        if (
                            profile is not None
                            and hasattr(profile, "models")
                            and profile.models
                        ):
                            # Use default model pricing, or first model
                            model_cap = None
                            if profile.default_model:
                                model_cap = next(
                                    (m for m in profile.models
                                     if m.name == profile.default_model),
                                    None,
                                )
                            if model_cap is None:
                                model_cap = profile.models[0]
                            cost_input = model_cap.cost_per_1k_input
                            cost_output = model_cap.cost_per_1k_output
                except Exception:
                    # Profile pricing is best-effort — fall back to hardcoded
                    pass

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
                cost_per_1k_input=cost_input,
                cost_per_1k_output=cost_output,
                instrument_override=actual_instrument,
            )
        finally:
            # Phase 1 item 4: idempotent clear of the PID/PGID entry. The
            # entry may have been cleared already by deregister_job's
            # preempt-kill path or by _on_musician_done; pop with default
            # is safe either way. Running here ensures cleanup even when
            # _musician_wrapper is awaited directly (tests) rather than
            # scheduled as a task with a done callback.
            self._active_pids.pop((job_id, sheet.num), None)
            # Detach the closure so a backend recycled through the free
            # list does not smear stale state onto its next dispatch.
            if hasattr(backend, "_on_process_group_spawned"):
                backend._on_process_group_spawned = None

            # Always release the backend — use the same instrument name
            # that was used for acquire (actual_instrument), not the Sheet
            # entity's original instrument_name which doesn't change after
            # fallback. Mismatched acquire/release corrupts the pool's
            # in_flight counts.
            #
            # Shield from cancellation: if the task is cancelled during
            # shutdown/modify, CancelledError could interrupt the release
            # await, leaving the pool's in_flight counter permanently
            # inflated. Shield ensures the release completes even during
            # task cancellation.
            if self._backend_pool is not None:
                try:
                    await asyncio.shield(
                        self._backend_pool.release(
                            actual_instrument, backend
                        )
                    )
                except (Exception, asyncio.CancelledError):
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
        # Phase 1 item 4: idempotent clear. The spawn-site finally already
        # runs kill-on-exit; this pop handles the case where no subprocess
        # was ever spawned (HTTP backend, early error) or deregister_job
        # already preempt-killed and cleared.
        self._active_pids.pop((job_id, sheet_num), None)

        if task.cancelled():
            # Cancelled tasks never report to the baton — the sheet stays
            # DISPATCHED, consuming a concurrency slot. Inject a synthetic
            # failure so the baton's state machine handles cleanup.
            # Only needed if the job is still registered (not deregistered
            # during cancel/shutdown, which removes all sheets).
            state = self._baton.get_sheet_state(job_id, sheet_num)
            if state is not None and state.status == BatonSheetStatus.DISPATCHED:
                from marianne.daemon.baton.events import SheetAttemptResult as SAR
                self._baton.inbox.put_nowait(SAR(
                    job_id=job_id,
                    sheet_num=sheet_num,
                    instrument_name=state.instrument_name or "",
                    attempt=state.normal_attempts + 1,
                    execution_success=False,
                    error_classification="CANCELLED",
                    error_message="Musician task cancelled",
                ))
            _logger.warning(
                "adapter.musician.cancelled",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                    "instrument": state.instrument_name if state else "unknown",
                },
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

    async def _publish_fallback_events(self) -> None:
        """Drain and publish InstrumentFallback events from the baton core.

        Called after each event cycle in the main loop. The baton core
        collects InstrumentFallback events as side effects of event
        processing and dispatch. This method drains them and publishes
        to the EventBus for dashboard, learning hub, and notifications.
        """
        events = self._baton.drain_fallback_events()
        if not events:
            return

        if self._event_bus is None:
            return

        from marianne.daemon.baton.events import to_observer_event

        for fallback_ev in events:
            obs_event = to_observer_event(fallback_ev)
            try:
                await self._event_bus.publish(obs_event)
            except Exception:
                _logger.warning(
                    "adapter.fallback_event_publish_failed",
                    extra={
                        "job_id": fallback_ev.job_id,
                        "sheet_num": fallback_ev.sheet_num,
                        "from_instrument": fallback_ev.from_instrument,
                        "to_instrument": fallback_ev.to_instrument,
                    },
                    exc_info=True,
                )

    # =========================================================================
    # Main Loop
    # =========================================================================

    async def run(self) -> None:
        """Run the baton's event loop with dispatch integration.

        Processes events from the inbox, updates state, dispatches
        ready sheets, and publishes events to the EventBus.

        Runs until the baton receives a ShutdownRequested event.
        """
        from marianne.daemon.baton.dispatch import dispatch_ready

        self._running = True
        _logger.info("adapter.started")

        # Start the timer wheel drain task — fires scheduled events
        # (rate limit expiry, retry delays) into the baton's inbox.
        # Wrapped in a restart loop so a crash doesn't silently kill
        # all timer-based recovery (rate limit expiry, retry backoff).
        async def _timer_with_restart() -> None:
            while not self._baton._shutting_down:
                try:
                    await self._timer_wheel.run()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _logger.error(
                        "adapter.timer_wheel_crashed",
                        exc_info=True,
                    )
                    await asyncio.sleep(1.0)  # Brief pause before restart

        timer_task = asyncio.create_task(
            _timer_with_restart(), name="baton-timer-wheel"
        )

        try:
            # Initial dispatch cycle — pick up PENDING sheets from resume/restart
            # without waiting for the first event. Without this, sheets mapped
            # from WAITING→PENDING on restart sit idle until an unrelated event
            # (e.g., a completion from another job) triggers dispatch.
            config = self._baton.build_dispatch_config(
                max_concurrent_sheets=self._max_concurrent_sheets,
            )
            initial_result = await dispatch_ready(
                self._baton, config, self._dispatch_callback
            )
            if initial_result.dispatched_sheets:
                _logger.info(
                    "adapter.initial_dispatch",
                    extra={
                        "count": len(initial_result.dispatched_sheets),
                        "sheets": [
                            f"{jid}:{sn}"
                            for jid, sn in initial_result.dispatched_sheets
                        ],
                    },
                )
                for d_job_id, _d_sheet_num in initial_result.dispatched_sheets:
                    if self._persist_callback:
                        self._persist_callback(d_job_id)

            while not self._baton._shutting_down:
                event = await self._baton.inbox.get()

                _logger.debug(
                    "adapter.event_loop.received",
                    event_type=type(event).__name__,
                    queue_size=self._baton.inbox.qsize(),
                )

                # Intercept StaleCheck: only fail if the musician task
                # is actually dead. If the task is alive, the sheet isn't
                # stale — the backend is still executing. Reschedule.
                if isinstance(event, StaleCheck):
                    task_key = (event.job_id, event.sheet_num)
                    task = self._active_tasks.get(task_key)
                    if task is not None and not task.done():
                        # Task alive — not stale, reschedule in 60s
                        self._timer_wheel.schedule(
                            60.0,
                            StaleCheck(
                                job_id=event.job_id,
                                sheet_num=event.sheet_num,
                            ),
                        )
                        # Still pass to baton for logging
                        await self._baton.handle_event(event)
                    else:
                        # Task dead with no result — actually stale
                        state = self._baton.get_sheet_state(
                            event.job_id, event.sheet_num,
                        )
                        if (
                            state is not None
                            and state.status == BatonSheetStatus.DISPATCHED
                        ):
                            _logger.warning(
                                "adapter.stale_check.task_dead",
                                extra={
                                    "job_id": event.job_id,
                                    "sheet_num": event.sheet_num,
                                },
                            )
                            # Inject synthetic failure
                            from marianne.daemon.baton.events import (
                                SheetAttemptResult as SAR,
                            )
                            self._baton.inbox.put_nowait(SAR(
                                job_id=event.job_id,
                                sheet_num=event.sheet_num,
                                instrument_name=state.instrument_name or "",
                                attempt=state.normal_attempts + 1,
                                execution_success=False,
                                error_classification="STALE",
                                error_message=(
                                    f"Sheet {event.sheet_num}: musician task "
                                    f"dead with no result reported"
                                ),
                            ))
                        await self._baton.handle_event(event)
                else:
                    await self._baton.handle_event(event)

                # Phase 2: persist to registry if state changed.
                # The baton writes directly to SheetState objects in
                # _live_states — no sync needed. Just persist.
                if self._baton._state_dirty and self._persist_callback:
                    self._persist_dirty_jobs()
                    self._baton._state_dirty = False

                # Dispatch ready sheets after every event
                config = self._baton.build_dispatch_config(
                    max_concurrent_sheets=self._max_concurrent_sheets,
                )
                dispatch_result = await dispatch_ready(
                    self._baton, config, self._dispatch_callback
                )

                # Sync dispatched sheets so status display shows in_progress
                if dispatch_result.dispatched_sheets:
                    _logger.info(
                        "adapter.dispatch_sync",
                        extra={
                            "count": len(dispatch_result.dispatched_sheets),
                            "sheets": [
                                f"{jid}:{sn}"
                                for jid, sn in dispatch_result.dispatched_sheets
                            ],
                        },
                    )
                for d_job_id, d_sheet_num in dispatch_result.dispatched_sheets:
                    # Persist dispatch state (sheet moved to DISPATCHED)
                    if self._persist_callback:
                        self._persist_callback(d_job_id)
                    # Schedule stale detection using per-sheet timeout.
                    # If the sheet completes normally, the StaleCheck handler
                    # finds it non-DISPATCHED and is a no-op.
                    d_state = self._baton.get_sheet_state(d_job_id, d_sheet_num)
                    stale_delay = (
                        getattr(d_state, "sheet_timeout_seconds", 1800.0)
                        if d_state else 1800.0
                    ) + 60.0  # buffer beyond timeout
                    self._timer_wheel.schedule(
                        stale_delay,
                        StaleCheck(job_id=d_job_id, sheet_num=d_sheet_num),
                    )

                # Publish any fallback events to EventBus
                await self._publish_fallback_events()

                # Check for job completions after dispatch
                self._check_completions()

        except asyncio.CancelledError:
            _logger.info("adapter.cancelled")
            raise
        finally:
            timer_task.cancel()
            await self._timer_wheel.shutdown()
            self._running = False
            _logger.info("adapter.stopped")

    async def shutdown(self) -> None:
        """Gracefully shut down the adapter.

        Cancels all active musician tasks and closes the backend pool.
        """
        # Cancel all active tasks
        if self._active_tasks:
            affected = [
                {"job_id": k[0], "sheet_num": k[1]}
                for k in self._active_tasks
            ]
            _logger.warning(
                "adapter.shutdown.cancelling_tasks",
                extra={"tasks": affected, "count": len(affected)},
            )
        for key, task in self._active_tasks.items():
            task.cancel(msg=f"adapter shutdown (job={key[0]}, sheet={key[1]})")
        self._active_tasks.clear()

        # Close backend pool
        if self._backend_pool is not None:
            try:
                await self._backend_pool.close_all()
            except Exception:
                _logger.warning("adapter.pool_close_failed", exc_info=True)

        _logger.info("adapter.shutdown_complete")
