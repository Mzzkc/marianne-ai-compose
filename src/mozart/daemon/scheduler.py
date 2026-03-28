"""Global sheet scheduler — cross-job concurrency control.

Phase 3 infrastructure — fully built and tested, not yet wired into
the execution path.

Currently, ``JobManager._run_job_task()`` delegates to
``JobService.start_job()`` which runs jobs monolithically (all sheets
sequentially within one task).  When this scheduler is wired in, the
manager will instead decompose jobs into individual sheets, register
them via ``register_job()``, and use ``next_sheet()`` /
``mark_complete()`` to drive per-sheet dispatch with cross-job
fair-share, DAG ordering, and rate-limit awareness.

Manages a priority min-heap of sheets from ALL active daemon jobs.
Enforces global concurrency limits, per-job fair-share scheduling,
DAG dependency awareness, and integrates with rate limiting and
backpressure controllers.

Lock ordering (daemon-wide):
  1. GlobalSheetScheduler._lock
  2. RateLimitCoordinator._lock
  3. BackpressureController  (lock-free — reads are atomic)
  4. CentralLearningStore._lock    (future — Stage 5)
"""

from __future__ import annotations

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from typing import Protocol

from mozart.core.logging import get_logger
from mozart.daemon.config import DaemonConfig

_logger = get_logger("daemon.scheduler")


# ─── Protocols for downstream integration ──────────────────────────


class RateLimitChecker(Protocol):
    """Protocol for rate limit checking (satisfied by RateLimitCoordinator)."""

    async def is_rate_limited(
        self, backend_type: str, model: str | None = None,
    ) -> tuple[bool, float]: ...


class BackpressureChecker(Protocol):
    """Protocol for backpressure checking (satisfied by BackpressureController)."""

    async def can_start_sheet(self) -> tuple[bool, float]: ...


# ─── Data models ───────────────────────────────────────────────────


@dataclass
class SheetInfo:
    """Metadata about a sheet waiting to be scheduled."""

    job_id: str
    sheet_num: int
    backend_type: str = "claude_cli"
    # Optional model identifier for per-model rate limit tracking.
    # When set, the scheduler forwards it to ``RateLimitChecker.is_rate_limited()``
    # so rate limits can be tracked per model (e.g. claude-sonnet vs claude-haiku)
    # rather than per backend type only.
    model: str | None = None
    # Expected range 0-100 (relative units).  The priority formula applies
    # a 0.1× weight, so a cost of 100 adds +10 priority — comparable to
    # one priority tier.  Values >1000 may overwhelm the base priority
    # range (10-100), causing cost to dominate scheduling order.
    estimated_cost: float = 0.0
    dag_depth: int = 0
    job_priority: int = 5  # 1=highest, 10=lowest; default 5
    retries_so_far: int = 0


@dataclass(order=True)
class SheetEntry:
    """Priority queue entry for a schedulable sheet.

    Ordered by (priority, submitted_at) — lower priority value = higher
    urgency.  The ``info`` field is excluded from comparison so that
    heapq ordering is based solely on the numeric sort keys.
    """

    priority: float
    submitted_at: float = field(compare=True)
    info: SheetInfo = field(compare=False)
    rate_limit_skip_count: int = field(default=0, compare=False)


@dataclass
class SchedulerStats:
    """Statistics snapshot from the scheduler."""

    queued: int
    active: int
    max_concurrent: int
    per_job_running: dict[str, int]
    per_job_queued: dict[str, int]


# ─── Scheduler ─────────────────────────────────────────────────────


class GlobalSheetScheduler:
    """Cross-job sheet-level scheduler using a priority min-heap.

    **Status: Phase 3 infrastructure — built and tested, not yet
    wired into the execution path.**

    Manages a global priority queue of sheets from all active jobs.
    Enforces:
    - ``max_concurrent_sheets`` from DaemonConfig
    - Per-job fair-share limits (penalty-based, not hard-block)
    - Per-job hard cap at ``fair_share * max_per_job_multiplier``
    - DAG dependency awareness via completed-set tracking

    The scheduler does NOT own sheet execution — it decides *which*
    sheet should run next and the ``JobManager`` performs the actual
    execution.

    Integration plan (future):
        1. ``JobManager._run_job_task()`` calls ``register_job()`` with
           all sheets parsed from the ``JobConfig``.
        2. A dispatch loop calls ``next_sheet()`` and spawns per-sheet
           tasks instead of calling ``JobService.start_job()`` monolithically.
        3. Each per-sheet task calls ``mark_complete()`` on finish.
        4. ``cancel_job()`` / shutdown calls ``deregister_job()`` for cleanup.
    """

    def __init__(self, config: DaemonConfig) -> None:
        self._config = config
        self._max_concurrent = config.max_concurrent_sheets

        # Priority heap
        self._queue: list[SheetEntry] = []

        # Tracking
        self._running: dict[str, set[int]] = {}  # job_id → running sheet_nums
        self._active_count = 0

        # Per-job DAG tracking
        self._job_completed: dict[str, set[int]] = {}
        self._job_deps: dict[str, dict[int, set[int]]] = {}  # job_id → {sheet → {deps}}
        self._job_all_sheets: dict[str, list[SheetInfo]] = {}  # for re-enqueue

        # Fair-share tuning
        self._fair_share_overage_penalty = 20.0
        self._max_per_job_multiplier = 2.0

        # Rate-limiter error tolerance: after this many consecutive
        # rate-limiter errors, a sheet is dropped from the queue to
        # prevent infinite cycling.
        self._max_rate_limit_skips = 10

        # Optional downstream integrations (set via setters)
        self._rate_limiter: RateLimitChecker | None = None
        self._backpressure: BackpressureChecker | None = None

        # Concurrency control
        self._lock = asyncio.Lock()

    # ─── Integration setters ───────────────────────────────────────

    def set_rate_limiter(self, rate_limiter: RateLimitChecker) -> None:
        """Wire up the rate limit coordinator (called once during init)."""
        self._rate_limiter = rate_limiter

    def set_backpressure(self, backpressure: BackpressureChecker) -> None:
        """Wire up the backpressure controller (called once during init)."""
        self._backpressure = backpressure

    # ─── Public API ────────────────────────────────────────────────

    async def register_job(
        self,
        job_id: str,
        sheets: list[SheetInfo],
        dependencies: dict[int, set[int]] | None = None,
    ) -> None:
        """Register a job's sheets with the scheduler.

        Enqueues sheets whose dependencies are already satisfied (or have
        no dependencies).  Remaining sheets are enqueued as their deps
        complete via ``mark_complete()``.

        Args:
            job_id: Unique job identifier.
            sheets: All sheets for this job.
            dependencies: Optional DAG — ``{sheet_num: {dependency_sheet_nums}}``.
                          If None, all sheets are independent.

        Raises:
            ValueError: If dependencies contain a cycle (would deadlock).
        """
        if dependencies:
            cycle = self._detect_cycle(dependencies)
            if cycle:
                raise ValueError(
                    f"Circular dependency detected in job {job_id}: "
                    f"{' → '.join(str(n) for n in cycle)}"
                )

        # Validate sheet_num uniqueness — duplicates would silently corrupt
        # DAG tracking and completion-set logic.
        seen_nums: set[int] = set()
        for info in sheets:
            if info.sheet_num in seen_nums:
                raise ValueError(
                    f"Duplicate sheet_num {info.sheet_num} in job {job_id}"
                )
            seen_nums.add(info.sheet_num)

        # Warn on dependencies referencing sheet_nums not in the sheets list.
        # These deps can never be satisfied, so affected sheets will stay
        # blocked forever.  This is a config error, not a hard failure.
        if dependencies:
            for sheet_num, dep_set in dependencies.items():
                phantom_deps = dep_set - seen_nums
                if phantom_deps:
                    _logger.warning(
                        "scheduler.phantom_dependencies",
                        job_id=job_id,
                        sheet_num=sheet_num,
                        missing_deps=sorted(phantom_deps),
                        msg="Dependencies reference non-existent sheet_nums; "
                            "affected sheet will never become ready",
                    )

        async with self._lock:
            # Guard against duplicate registration: remove stale heap
            # entries from any prior registration of this job_id.
            if job_id in self._job_all_sheets:
                self._queue = [
                    e for e in self._queue if e.info.job_id != job_id
                ]
                heapq.heapify(self._queue)
                # Adjust active count for any running sheets being discarded
                old_running = self._running.pop(job_id, set())
                self._active_count = max(0, self._active_count - len(old_running))
                _logger.warning(
                    "scheduler.duplicate_register",
                    job_id=job_id,
                    msg="Re-registering job; previous entries purged",
                )

            deps = dependencies or {}
            self._job_deps[job_id] = deps
            self._job_completed[job_id] = set()
            self._running[job_id] = set()
            self._job_all_sheets[job_id] = list(sheets)

            # Enqueue sheets whose deps are already met
            enqueued = 0
            for info in sheets:
                sheet_deps = deps.get(info.sheet_num, set())
                if not sheet_deps:
                    entry = self._make_entry(info)
                    heapq.heappush(self._queue, entry)
                    enqueued += 1

            _logger.info(
                "scheduler.job_registered",
                job_id=job_id,
                total_sheets=len(sheets),
                immediately_ready=enqueued,
            )

    async def deregister_job(self, job_id: str) -> None:
        """Remove all pending sheets for a cancelled/completed job."""
        async with self._lock:
            # Early return for unknown job_ids — avoids needless O(n) queue
            # rebuild and ensures the log message reflects real deregistrations.
            if job_id not in self._job_all_sheets:
                _logger.debug(
                    "scheduler.deregister_unknown_job",
                    job_id=job_id,
                )
                return

            # Rebuild queue without this job's sheets
            self._queue = [
                e for e in self._queue if e.info.job_id != job_id
            ]
            heapq.heapify(self._queue)

            self._running.pop(job_id, None)
            self._job_completed.pop(job_id, None)
            self._job_deps.pop(job_id, None)
            self._job_all_sheets.pop(job_id, None)

            # Recount active
            self._active_count = sum(
                len(s) for s in self._running.values()
            )

            _logger.info("scheduler.job_deregistered", job_id=job_id)

    async def next_sheet(self) -> SheetEntry | None:
        """Pop the highest-priority ready sheet, respecting limits.

        Returns None if no sheet can run (concurrency full, backpressure
        active, queue empty, or all queued sheets are rate-limited).

        **Single-caller constraint:** The backpressure delay (``asyncio.sleep``)
        runs *outside* the scheduler lock.  If multiple coroutines call
        ``next_sheet()`` concurrently, they may all pass the backpressure
        check before any acquires the lock, effectively bypassing the delay
        for concurrent callers.  The intended usage is a single dispatch
        loop calling ``next_sheet()`` sequentially — the ``JobManager``
        dispatch loop satisfies this constraint.
        """
        # Check backpressure first (outside scheduler lock per lock ordering).
        # TOCTOU note: backpressure state may change between this check and
        # the lock acquisition below, but this is acceptable — backpressure
        # is advisory and the delay is best-effort.
        if self._backpressure is not None:
            allowed, delay = await self._backpressure.can_start_sheet()
            if not allowed:
                return None
            if delay > 0:
                await asyncio.sleep(delay)

        async with self._lock:
            if self._active_count >= self._max_concurrent:
                return None

            # Re-score all queued entries against current running state.
            # Priorities depend on fair-share (which changes as sheets
            # dispatch), so enqueue-time scores go stale.  With typical
            # queue sizes of 10-100 entries, the O(n log n) rebuild is
            # negligible compared to API call latency.
            refreshed: list[SheetEntry] = []
            for entry in self._queue:
                rescored = SheetEntry(
                    priority=self._calculate_priority(entry.info),
                    submitted_at=entry.submitted_at,
                    info=entry.info,
                    rate_limit_skip_count=entry.rate_limit_skip_count,
                )
                refreshed.append(rescored)
            heapq.heapify(refreshed)
            self._queue = refreshed

            skipped: list[SheetEntry] = []
            dropped: list[SheetEntry] = []
            result: SheetEntry | None = None

            while self._queue:
                entry = heapq.heappop(self._queue)
                job_id = entry.info.job_id

                # Check per-job hard cap
                job_running = len(self._running.get(job_id, set()))
                registered_job_count = len(self._job_all_sheets)
                fair_share = self._fair_share(registered_job_count)
                hard_cap = int(fair_share * self._max_per_job_multiplier)
                hard_cap = max(1, hard_cap)

                if job_running >= hard_cap:
                    skipped.append(entry)
                    continue

                # Check rate limiting (outside-lock would violate ordering,
                # but rate_limiter._lock is #2, scheduler._lock is #1 — OK
                # to call while holding #1)
                if self._rate_limiter is not None:
                    try:
                        is_limited, _ = await self._rate_limiter.is_rate_limited(
                            entry.info.backend_type,
                            model=entry.info.model,
                        )
                    except (RuntimeError, ValueError, OSError) as e:
                        entry.rate_limit_skip_count += 1
                        if entry.rate_limit_skip_count >= self._max_rate_limit_skips:
                            _logger.error(
                                "scheduler.rate_limiter_error_exhausted",
                                job_id=job_id,
                                sheet_num=entry.info.sheet_num,
                                skip_count=entry.rate_limit_skip_count,
                                max_skips=self._max_rate_limit_skips,
                                error=str(e),
                                error_type=type(e).__name__,
                            )
                            dropped.append(entry)
                        else:
                            _logger.warning(
                                "scheduler.rate_limiter_error",
                                job_id=job_id,
                                sheet_num=entry.info.sheet_num,
                                skip_count=entry.rate_limit_skip_count,
                                max_skips=self._max_rate_limit_skips,
                                error=str(e),
                                error_type=type(e).__name__,
                            )
                            skipped.append(entry)
                        continue
                    if is_limited:
                        skipped.append(entry)
                        continue

                # This sheet can run
                result = entry
                break

            # Put skipped entries back
            for s in skipped:
                heapq.heappush(self._queue, s)

            if result is not None:
                job_id = result.info.job_id
                self._running.setdefault(job_id, set()).add(
                    result.info.sheet_num,
                )
                self._active_count += 1

                _logger.debug(
                    "scheduler.sheet_dispatched",
                    job_id=job_id,
                    sheet_num=result.info.sheet_num,
                    priority=round(result.priority, 2),
                    active=self._active_count,
                )

            return result

    async def mark_complete(
        self, job_id: str, sheet_num: int, success: bool,
    ) -> None:
        """Mark a sheet as done and enqueue newly-ready dependents.

        Args:
            job_id: The job that owns this sheet.
            sheet_num: Which sheet completed.
            success: Whether execution succeeded.
        """
        async with self._lock:
            # Early-return for unknown job_id to prevent memory leaks
            # from stale or misrouted completion messages.
            if job_id not in self._job_all_sheets:
                _logger.debug(
                    "scheduler.mark_complete_unknown_job",
                    job_id=job_id,
                    sheet_num=sheet_num,
                )
                return

            running = self._running.get(job_id)
            actually_was_running = False
            if running is not None and sheet_num in running:
                running.discard(sheet_num)
                actually_was_running = True

            if actually_was_running:
                self._active_count = max(0, self._active_count - 1)

            completed = self._job_completed.setdefault(job_id, set())
            completed.add(sheet_num)

            _logger.debug(
                "scheduler.sheet_completed",
                job_id=job_id,
                sheet_num=sheet_num,
                success=success,
                active=self._active_count,
            )

            # Enqueue newly-ready dependent sheets
            self._enqueue_ready_dependents(job_id)

    async def get_stats(self) -> SchedulerStats:
        """Return a snapshot of scheduler state."""
        async with self._lock:
            per_job_running = {
                jid: len(sheets) for jid, sheets in self._running.items()
            }
            per_job_queued: dict[str, int] = {}
            for entry in self._queue:
                jid = entry.info.job_id
                per_job_queued[jid] = per_job_queued.get(jid, 0) + 1

            return SchedulerStats(
                queued=len(self._queue),
                active=self._active_count,
                max_concurrent=self._max_concurrent,
                per_job_running=per_job_running,
                per_job_queued=per_job_queued,
            )

    # ─── Properties ────────────────────────────────────────────────

    @property
    def active_count(self) -> int:
        """Number of sheets currently executing."""
        return self._active_count

    @property
    def queued_count(self) -> int:
        """Number of sheets waiting in the queue."""
        return len(self._queue)

    # ─── Internal ──────────────────────────────────────────────────

    def _make_entry(self, info: SheetInfo) -> SheetEntry:
        """Create a priority-scored SheetEntry from SheetInfo."""
        priority = self._calculate_priority(info)
        return SheetEntry(
            priority=priority,
            submitted_at=time.monotonic(),
            info=info,
        )

    # Maximum retry boost: 10 retries × 5.0 = -50 priority.  Beyond this,
    # additional retries don't further boost priority — prevents sheets
    # with extreme retry counts from starving all other sheets.
    _MAX_RETRY_BOOST: float = 50.0

    def _calculate_priority(self, info: SheetInfo) -> float:
        """Compute priority score (lower = more urgent).

        Formula:
          base = job_priority * 10.0  (user-specified, default 5 → 50)
          + dag_depth * 1.0           (prefer shallow/early sheets)
          - min(retries_so_far * 5.0, 50.0)  (boost retried sheets, capped)
          + estimated_cost * 0.1      (slightly deprioritize expensive)
          + fair_share_penalty         (penalize jobs hogging slots)
        """
        base = info.job_priority * 10.0
        base += info.dag_depth * 1.0
        base -= min(info.retries_so_far * 5.0, self._MAX_RETRY_BOOST)
        base += info.estimated_cost * 0.1

        # Fair-share penalty (use registered jobs, not just running)
        job_running = len(self._running.get(info.job_id, set()))
        registered_jobs = len(self._job_all_sheets)
        fair_share = self._fair_share(registered_jobs)

        if fair_share > 0 and job_running >= fair_share:
            overage = job_running - fair_share + 1
            base += overage * self._fair_share_overage_penalty

        return base

    def _fair_share(self, active_job_count: int) -> float:
        """Calculate per-job fair share of concurrent slots."""
        if active_job_count <= 0:
            return float(self._max_concurrent)
        return max(1.0, self._max_concurrent / active_job_count)

    @staticmethod
    def _detect_cycle(deps: dict[int, set[int]]) -> list[int] | None:
        """Detect a cycle in the dependency graph using iterative DFS.

        Uses an explicit stack instead of recursion to handle large DAGs
        (thousands of sheets) without hitting Python's recursion limit.
        Follows the same pattern as ``SheetDAG._validate_no_cycles()``
        in ``execution/dag.py``.

        Returns the cycle as a list of sheet numbers (e.g. [1, 2, 3, 1])
        if found, or None if the graph is acyclic.
        """
        # Build full node set from both keys and values
        all_nodes: set[int] = set(deps.keys())
        for dep_set in deps.values():
            all_nodes.update(dep_set)

        # Build forward adjacency: prerequisite → [dependents]
        adjacency: dict[int, list[int]] = {}
        for node, prerequisites in deps.items():
            for prereq in prerequisites:
                adjacency.setdefault(prereq, []).append(node)

        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[int, int] = dict.fromkeys(all_nodes, WHITE)

        # Track path for cycle reconstruction
        path: list[int] = []
        path_set: set[int] = set()

        for start in all_nodes:
            if color[start] != WHITE:
                continue

            # Stack holds (node, successors_list, index_into_successors)
            color[start] = GRAY
            path.append(start)
            path_set.add(start)
            successors = adjacency.get(start, [])
            stack: list[tuple[int, list[int], int]] = [
                (start, successors, 0),
            ]

            while stack:
                node, succs, idx = stack[-1]

                if idx < len(succs):
                    # Advance to next successor
                    stack[-1] = (node, succs, idx + 1)
                    child = succs[idx]

                    if color[child] == GRAY:
                        # Back edge — cycle found. Reconstruct from path.
                        cycle_start = path.index(child)
                        cycle = path[cycle_start:] + [child]
                        return cycle

                    if color[child] == WHITE:
                        color[child] = GRAY
                        path.append(child)
                        path_set.add(child)
                        child_succs = adjacency.get(child, [])
                        stack.append((child, child_succs, 0))
                else:
                    # All successors visited — mark complete
                    stack.pop()
                    color[node] = BLACK
                    path.pop()
                    path_set.discard(node)

        return None

    def _enqueue_ready_dependents(self, job_id: str) -> None:
        """Enqueue sheets whose dependencies are now satisfied.

        Must be called while holding ``self._lock``.
        """
        deps = self._job_deps.get(job_id, {})
        completed = self._job_completed.get(job_id, set())
        all_sheets = self._job_all_sheets.get(job_id, [])

        # Find sheets that are already queued or running
        already_queued = {
            e.info.sheet_num
            for e in self._queue
            if e.info.job_id == job_id
        }
        already_running = self._running.get(job_id, set())

        for info in all_sheets:
            sn = info.sheet_num
            # Skip if already completed, queued, or running
            if sn in completed or sn in already_queued or sn in already_running:
                continue

            sheet_deps = deps.get(sn, set())
            if sheet_deps and sheet_deps.issubset(completed):
                entry = self._make_entry(info)
                heapq.heappush(self._queue, entry)
                _logger.debug(
                    "scheduler.dependent_sheet_ready",
                    job_id=job_id,
                    sheet_num=sn,
                    satisfied_deps=sorted(sheet_deps),
                )


__all__ = [
    "BackpressureChecker",
    "GlobalSheetScheduler",
    "RateLimitChecker",
    "SchedulerStats",
    "SheetEntry",
    "SheetInfo",
]
