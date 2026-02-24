"""Central profiler orchestrator for the Mozart daemon.

``ProfilerCollector`` is the heart of the profiling subsystem.  It runs a
periodic collection loop that gathers system metrics, per-process data,
GPU stats, and strace summaries into ``SystemSnapshot`` objects.  These
are persisted (SQLite + JSONL), fed to the ``AnomalyDetector``, and
published to the ``EventBus`` for downstream consumers.

Lifecycle::

    collector = ProfilerCollector(config, monitor, pgroup, event_bus, manager)
    await collector.start()
    ...
    snapshot = await collector.collect_snapshot()
    ...
    await collector.stop()
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from mozart.core.logging import get_logger
from mozart.daemon.profiler.anomaly import AnomalyDetector
from mozart.daemon.profiler.gpu_probe import GpuProbe
from mozart.daemon.profiler.models import (
    Anomaly,
    EventType,
    JobProgress,
    ProcessEvent,
    ProcessMetric,
    ProfilerConfig,
    SystemSnapshot,
)
from mozart.daemon.profiler.storage import MonitorStorage
from mozart.daemon.profiler.strace_manager import StraceManager
from mozart.daemon.system_probe import SystemProbe
from mozart.daemon.types import ObserverEvent

if TYPE_CHECKING:
    from mozart.daemon.event_bus import EventBus
    from mozart.daemon.manager import JobManager
    from mozart.daemon.monitor import ResourceMonitor
    from mozart.daemon.pgroup import ProcessGroupManager

_logger = get_logger("daemon.profiler.collector")

# psutil is optional — probe availability once at import time
try:
    import psutil as _psutil
except ImportError:
    _psutil = None  # type: ignore[assignment]

# Maximum snapshots to retain in memory for anomaly detection history
_HISTORY_SIZE = 60


class ProfilerCollector:
    """Central orchestrator for the daemon profiler subsystem.

    Coordinates:
    - Periodic metric collection (system + per-process + GPU + strace)
    - SQLite + JSONL persistence via ``MonitorStorage``
    - Heuristic anomaly detection via ``AnomalyDetector``
    - EventBus integration for ``monitor.anomaly`` events
    - Process lifecycle tracking via ``sheet.started``/``completed``/``failed``

    Parameters
    ----------
    config:
        Profiler configuration (interval, storage paths, thresholds).
    monitor:
        The daemon's ``ResourceMonitor`` for system-level metrics.
    pgroup:
        The daemon's ``ProcessGroupManager`` for child process enumeration.
    event_bus:
        The daemon's ``EventBus`` for publishing anomaly events and
        subscribing to sheet lifecycle events.
    manager:
        Optional ``JobManager`` for mapping PIDs to job_id/sheet_num
        and reading running job / active sheet counts.
    """

    def __init__(
        self,
        config: ProfilerConfig,
        monitor: ResourceMonitor,
        pgroup: ProcessGroupManager,
        event_bus: EventBus,
        manager: JobManager | None = None,
    ) -> None:
        self._config = config
        self._monitor = monitor
        self._pgroup = pgroup
        self._event_bus = event_bus
        self._manager = manager

        # Storage
        self._storage = MonitorStorage(
            db_path=config.storage_path.expanduser(),
            jsonl_path=config.jsonl_path.expanduser(),
            jsonl_max_bytes=config.jsonl_max_bytes,
        )

        # Strace management
        self._strace = StraceManager(enabled=config.strace_enabled)

        # Anomaly detection
        self._anomaly_detector = AnomalyDetector(config=config.anomaly)

        # State
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None
        self._history: list[SystemSnapshot] = []
        self._latest_snapshot: SystemSnapshot | None = None
        self._known_pids: set[int] = set()
        self._sub_ids: list[str] = []
        self._recent_events: list[ProcessEvent] = []
        self._max_recent_events = 200

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialize storage, subscribe to events, start collection loop."""
        if self._running:
            return

        if not self._config.enabled:
            _logger.info("profiler.disabled")
            return

        await self._storage.initialize()

        # Subscribe to sheet lifecycle events
        self._sub_ids.append(
            self._event_bus.subscribe(
                self._on_sheet_started,
                event_filter=lambda e: e.get("event") == "sheet.started",
            )
        )
        self._sub_ids.append(
            self._event_bus.subscribe(
                self._on_sheet_completed,
                event_filter=lambda e: e.get("event") in (
                    "sheet.completed", "sheet.failed"
                ),
            )
        )

        self._running = True
        self._loop_task = asyncio.create_task(
            self._collection_loop(), name="profiler-collection-loop"
        )
        _logger.info(
            "profiler.started",
            interval=self._config.interval_seconds,
            strace=self._strace.enabled,
            gpu=self._config.gpu_enabled,
        )

    async def stop(self) -> None:
        """Stop collection loop, detach strace, unsubscribe from events."""
        self._running = False

        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        # Detach all strace processes
        await self._strace.detach_all()

        # Unsubscribe from event bus
        for sub_id in self._sub_ids:
            self._event_bus.unsubscribe(sub_id)
        self._sub_ids.clear()

        _logger.info("profiler.stopped")

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    async def collect_snapshot(self) -> SystemSnapshot:
        """Gather all metrics into a single SystemSnapshot.

        Steps:
        1. System memory via SystemProbe
        2. Per-process metrics via psutil (with PID → job mapping)
        3. GPU metrics via GpuProbe
        4. Load average via os.getloadavg()
        5. Strace summaries for attached PIDs
        6. Pressure level from BackpressureController
        7. Running jobs / active sheets from JobManager
        """
        now = time.time()
        daemon_pid = os.getpid()

        # 1. System memory
        system_total = 0.0
        system_available = 0.0
        system_used = 0.0
        daemon_rss = SystemProbe.get_memory_mb() or 0.0

        try:
            system_total, system_available, system_used = self._get_system_memory()
        except Exception:
            _logger.debug("profiler.system_memory_probe_failed", exc_info=True)

        # 2. Per-process metrics
        processes, zombie_count, zombie_pids = await self._collect_process_metrics()

        # 3. GPU metrics
        gpus_raw: list[Any] = []
        if self._config.gpu_enabled:
            try:
                gpus_raw = await GpuProbe.get_gpu_metrics_async()
            except Exception:
                _logger.debug("profiler.gpu_probe_failed", exc_info=True)

        # Convert gpu_probe.GpuMetric (dataclass) → profiler.models.GpuMetric (Pydantic)
        from mozart.daemon.profiler.models import GpuMetric as PydanticGpuMetric
        gpus = [
            PydanticGpuMetric(
                index=g.index,
                utilization_pct=g.utilization_pct,
                memory_used_mb=g.memory_used_mb,
                memory_total_mb=g.memory_total_mb,
                temperature_c=g.temperature_c,
            )
            for g in gpus_raw
        ]

        # 4. Load average
        load1 = load5 = load15 = 0.0
        try:
            load1, load5, load15 = os.getloadavg()
        except OSError:
            pass

        # 5. Strace summaries — merge into process metrics
        for proc in processes:
            if proc.pid in self._strace.attached_pids:
                # Don't detach — just note it's being traced.
                # The summary is collected on detach (process exit).
                pass

        # 6. Pressure level
        pressure_level = "none"
        if self._manager is not None:
            try:
                pressure_level = self._manager.backpressure.current_level().value
            except Exception:
                _logger.debug("profiler.pressure_level_failed", exc_info=True)

        # 7. Running jobs / active sheets
        running_jobs = 0
        active_sheets = 0
        if self._manager is not None:
            running_jobs = self._manager.running_count
            active_sheets = self._manager.active_job_count

        # 8. Per-job progress from live CheckpointState
        job_progress_list: list[JobProgress] = []
        if self._manager is not None:
            for jid, state in self._manager._live_states.items():
                job_progress_list.append(
                    JobProgress(
                        job_id=jid,
                        total_sheets=getattr(state, "total_sheets", 1),
                        last_completed_sheet=getattr(state, "last_completed_sheet", 0),
                        current_sheet=getattr(state, "current_sheet", None),
                        status=getattr(state, "status", "unknown"),
                    )
                )

        # 9. Conductor uptime
        conductor_uptime = 0.0
        if self._manager is not None:
            conductor_uptime = self._manager.uptime_seconds

        snapshot = SystemSnapshot(
            timestamp=now,
            daemon_pid=daemon_pid,
            system_memory_total_mb=system_total,
            system_memory_available_mb=system_available,
            system_memory_used_mb=system_used,
            daemon_rss_mb=daemon_rss,
            load_avg_1=load1,
            load_avg_5=load5,
            load_avg_15=load15,
            processes=processes,
            gpus=gpus,
            pressure_level=pressure_level,
            running_jobs=running_jobs,
            active_sheets=active_sheets,
            zombie_count=zombie_count,
            zombie_pids=zombie_pids,
            job_progress=job_progress_list,
            conductor_uptime_seconds=conductor_uptime,
        )

        self._latest_snapshot = snapshot
        return snapshot

    # ------------------------------------------------------------------
    # Collection loop
    # ------------------------------------------------------------------

    async def _collection_loop(self) -> None:
        """Periodic loop: collect → store → detect → publish → manage strace."""
        while self._running:
            try:
                # 1. Collect snapshot
                snapshot = await self.collect_snapshot()

                # 2. Write to storage
                try:
                    await self._storage.write_snapshot(snapshot)
                    self._storage.append_jsonl(snapshot)
                except Exception:
                    _logger.warning("profiler.storage_write_failed", exc_info=True)

                # 3. Run anomaly detection
                anomalies = self._anomaly_detector.detect(snapshot, self._history)

                # 4. Publish anomalies to EventBus
                for anomaly in anomalies:
                    await self._publish_anomaly(anomaly)

                # 5. Update history buffer
                self._history.append(snapshot)
                if len(self._history) > _HISTORY_SIZE:
                    self._history = self._history[-_HISTORY_SIZE:]

                # 6. Manage strace for new/exited child processes
                await self._manage_strace_attachments(snapshot)

                # 7. Sleep for interval
                await asyncio.sleep(self._config.interval_seconds)

            except asyncio.CancelledError:
                break
            except Exception:
                _logger.warning("profiler.collection_loop_error", exc_info=True)
                # Back off on error to avoid tight error loops
                try:
                    await asyncio.sleep(self._config.interval_seconds * 2)
                except asyncio.CancelledError:
                    break

    # ------------------------------------------------------------------
    # Process metrics collection
    # ------------------------------------------------------------------

    async def _collect_process_metrics(
        self,
    ) -> tuple[list[ProcessMetric], int, list[int]]:
        """Collect per-process metrics for all daemon child processes.

        Returns:
            (processes, zombie_count, zombie_pids)
        """
        processes: list[ProcessMetric] = []
        zombie_count = 0
        zombie_pids: list[int] = []

        if _psutil is None:
            return processes, zombie_count, zombie_pids

        try:
            daemon_proc = _psutil.Process(os.getpid())
            children = daemon_proc.children(recursive=True)
        except (_psutil.NoSuchProcess, _psutil.AccessDenied, OSError):
            return processes, zombie_count, zombie_pids

        # Build PID → (job_id, sheet_num) map once per collection cycle
        pid_job_map = self._build_pid_job_map()

        for child in children:
            try:
                with child.oneshot():
                    status = child.status()
                    if status == _psutil.STATUS_ZOMBIE:
                        zombie_count += 1
                        zombie_pids.append(child.pid)
                        continue

                    mem_info = child.memory_info()
                    create_time = child.create_time()
                    age = time.time() - create_time

                    try:
                        cmdline = " ".join(child.cmdline())
                    except (_psutil.AccessDenied, _psutil.ZombieProcess):
                        cmdline = ""

                    try:
                        num_fds = child.num_fds()
                    except (_psutil.AccessDenied, OSError):
                        num_fds = 0

                    # Look up job mapping from the pre-built map
                    job_id, sheet_num = pid_job_map.get(
                        child.pid, (None, None),
                    )

                    proc_metric = ProcessMetric(
                        pid=child.pid,
                        ppid=child.ppid(),
                        command=cmdline[:500],  # Truncate long cmdlines
                        state=status[0].upper() if status else "S",
                        cpu_percent=child.cpu_percent(),
                        rss_mb=mem_info.rss / (1024 * 1024),
                        vms_mb=mem_info.vms / (1024 * 1024),
                        threads=child.num_threads(),
                        open_fds=num_fds,
                        age_seconds=age,
                        job_id=job_id,
                        sheet_num=sheet_num,
                    )
                    processes.append(proc_metric)

            except (_psutil.NoSuchProcess, _psutil.AccessDenied, _psutil.ZombieProcess):
                continue

        return processes, zombie_count, zombie_pids

    # ------------------------------------------------------------------
    # PID → Job mapping
    # ------------------------------------------------------------------

    def _build_pid_job_map(self) -> dict[int, tuple[str | None, int | None]]:
        """Build a PID → (job_id, sheet_num) map for all daemon children.

        Uses workspace-path matching instead of the broken PID-based
        approach.  In daemon mode, ``CheckpointState.pid`` is always the
        daemon PID (all jobs share one process), making PID matching
        useless.

        Algorithm:
        1. Get direct children of the daemon process.
        2. For each direct child, read its command line and check if it
           contains a running job's workspace path.
        3. When matched, that child and ALL its descendants belong to
           that job.  The sheet_num comes from the job's
           ``current_sheet`` in ``_live_states``.
        4. Unmatched processes get ``(None, None)`` (shown as orphans).

        Called once per collection cycle — O(children * jobs) string
        comparisons instead of the old O(children * children) psutil
        walks.
        """
        result: dict[int, tuple[str | None, int | None]] = {}

        if self._manager is None or _psutil is None:
            return result

        # Collect job workspace paths for matching
        job_workspaces: list[tuple[str, str, int | None]] = []
        for job_id, meta in self._manager._job_meta.items():
            ws_path = str(meta.workspace)
            state = self._manager._live_states.get(job_id)
            current_sheet = getattr(state, "current_sheet", None) if state else None
            job_workspaces.append((job_id, ws_path, current_sheet))

        if not job_workspaces:
            return result

        try:
            daemon_proc = _psutil.Process(os.getpid())
            direct_children = daemon_proc.children(recursive=False)
        except (_psutil.NoSuchProcess, _psutil.AccessDenied, OSError):
            return result

        for child in direct_children:
            try:
                cmdline = " ".join(child.cmdline())
            except (_psutil.NoSuchProcess, _psutil.AccessDenied, _psutil.ZombieProcess):
                continue

            # Match this direct child's cmdline against job workspaces
            matched_job_id: str | None = None
            matched_sheet: int | None = None
            for job_id, ws_path, current_sheet in job_workspaces:
                if ws_path in cmdline:
                    matched_job_id = job_id
                    matched_sheet = current_sheet
                    break

            if matched_job_id is not None:
                result[child.pid] = (matched_job_id, matched_sheet)
                # All descendants of this child belong to the same job
                try:
                    for desc in child.children(recursive=True):
                        result[desc.pid] = (matched_job_id, matched_sheet)
                except (_psutil.NoSuchProcess, _psutil.AccessDenied, OSError):
                    pass

        return result

    # ------------------------------------------------------------------
    # System memory
    # ------------------------------------------------------------------

    @staticmethod
    def _get_system_memory() -> tuple[float, float, float]:
        """Get system memory stats: (total_mb, available_mb, used_mb).

        Uses psutil if available, falls back to /proc/meminfo.
        """
        if _psutil is not None:
            try:
                vm = _psutil.virtual_memory()
                return (
                    vm.total / (1024 * 1024),
                    vm.available / (1024 * 1024),
                    vm.used / (1024 * 1024),
                )
            except Exception:
                pass

        # Fallback: /proc/meminfo
        total = available = 0.0
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total = int(line.split()[1]) / 1024  # kB → MB
                    elif line.startswith("MemAvailable:"):
                        available = int(line.split()[1]) / 1024
        except (OSError, ValueError):
            pass

        used = total - available
        return total, available, used

    # ------------------------------------------------------------------
    # Strace management
    # ------------------------------------------------------------------

    async def _manage_strace_attachments(self, snapshot: SystemSnapshot) -> None:
        """Attach strace to new child processes, detach from exited ones."""
        if not self._strace.enabled:
            return

        current_pids = {p.pid for p in snapshot.processes}

        # Attach to new processes
        new_pids = current_pids - self._known_pids
        for pid in new_pids:
            await self._strace.attach(pid)

        # Detach from exited processes and collect summaries
        exited_pids = self._known_pids - current_pids
        for pid in exited_pids:
            summary = await self._strace.detach(pid)
            if summary:
                _logger.debug(
                    "profiler.strace_summary_collected",
                    pid=pid,
                    syscall_count=len(summary.get("syscall_counts", {})),
                )

        self._known_pids = current_pids

    # ------------------------------------------------------------------
    # EventBus integration
    # ------------------------------------------------------------------

    async def _publish_anomaly(self, anomaly: Anomaly) -> None:
        """Publish an anomaly as a ``monitor.anomaly`` event on the EventBus."""
        event: ObserverEvent = {
            "job_id": anomaly.job_id or "",
            "sheet_num": anomaly.sheet_num or 0,
            "event": "monitor.anomaly",
            "data": {
                "anomaly_type": anomaly.anomaly_type.value,
                "severity": anomaly.severity.value,
                "description": anomaly.description,
                "pid": anomaly.pid,
                "metric_value": anomaly.metric_value,
                "threshold": anomaly.threshold,
            },
            "timestamp": anomaly.timestamp,
        }
        await self._event_bus.publish(event)
        _logger.info(
            "profiler.anomaly_detected",
            anomaly_type=anomaly.anomaly_type.value,
            severity=anomaly.severity.value,
            description=anomaly.description,
        )

    def _on_sheet_started(self, event: ObserverEvent) -> None:
        """Handle sheet.started — record spawn, attach strace, enrich with resource context."""
        job_id = event.get("job_id", "")
        sheet_num = event.get("sheet_num", 0)
        data = event.get("data") or {}
        pid = data.get("pid")

        # Always record the timeline event (pid=0 fallback when runner
        # doesn't send a PID, which is the common case).
        proc_event = ProcessEvent(
            pid=pid or 0,
            event_type=EventType.SPAWN,
            job_id=job_id,
            sheet_num=sheet_num,
            details=f"Sheet {sheet_num} started",
        )
        self._record_event(proc_event)

        if pid:
            # Attach strace (non-blocking — fire and forget via task)
            if self._strace.enabled:
                asyncio.create_task(
                    self._strace.attach(pid),
                    name=f"strace-attach-{pid}",
                )

            # Enrich the event with resource context for downstream consumers
            resource_ctx = self.get_resource_context_for_pid(pid)
            if data is not None:
                data["resource_context"] = resource_ctx
        else:
            # No PID — still attach general resource context
            resource_ctx = self.get_resource_context()
            if data is not None:
                data["resource_context"] = resource_ctx

    def _on_sheet_completed(self, event: ObserverEvent) -> None:
        """Handle sheet.completed/failed — record exit, detach strace, enrich resource context."""
        job_id = event.get("job_id", "")
        sheet_num = event.get("sheet_num", 0)
        data = event.get("data") or {}
        pid = data.get("pid")
        exit_code = data.get("exit_code")
        event_name = event.get("event", "")

        # Enrich the event with resource context BEFORE detaching strace
        if pid:
            resource_ctx = self.get_resource_context_for_pid(pid)
        else:
            resource_ctx = self.get_resource_context()
        if data is not None:
            data["resource_context"] = resource_ctx

        # Always record the timeline event (pid=0 fallback when runner
        # doesn't send a PID, which is the common case).
        proc_event = ProcessEvent(
            pid=pid or 0,
            event_type=EventType.EXIT,
            exit_code=exit_code,
            job_id=job_id,
            sheet_num=sheet_num,
            details=f"Sheet {sheet_num} "
            f"{'completed' if 'completed' in event_name else 'failed'}",
        )
        self._record_event(proc_event)

        if pid:
            # Detach strace and collect summary
            if self._strace.enabled:
                asyncio.create_task(
                    self._strace.detach(pid),
                    name=f"strace-detach-{pid}",
                )

    def _record_event(self, event: ProcessEvent) -> None:
        """Record a process event in memory and persist to storage."""
        self._recent_events.append(event)
        if len(self._recent_events) > self._max_recent_events:
            self._recent_events = self._recent_events[-self._max_recent_events:]

        # Async write — fire and forget
        asyncio.create_task(
            self._write_event_safe(event),
            name=f"write-event-{event.pid}",
        )

    async def _write_event_safe(self, event: ProcessEvent) -> None:
        """Write a process event to storage, swallowing errors."""
        try:
            await self._storage.write_event(event)
        except Exception:
            _logger.debug("profiler.event_write_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Resource context for log enrichment
    # ------------------------------------------------------------------

    def get_resource_context_for_pid(self, pid: int) -> dict[str, Any]:
        """Get current resource context for a specific PID.

        Returns a dict suitable for embedding in sheet event data:
        ``rss_mb``, ``cpu_pct``, ``syscall_hotspot``, ``anomalies_active``.

        If the PID is not found in the latest snapshot, returns a dict
        with all values set to None/empty.
        """
        context: dict[str, Any] = {
            "rss_mb": None,
            "cpu_pct": None,
            "syscall_hotspot": None,
            "anomalies_active": [],
        }

        snapshot = self._latest_snapshot
        if snapshot is None:
            return context

        # Add daemon-level RSS as fallback context
        context["daemon_rss_mb"] = snapshot.daemon_rss_mb

        # Find the process in the latest snapshot
        proc_match: ProcessMetric | None = None
        for proc in snapshot.processes:
            if proc.pid == pid:
                proc_match = proc
                break

        if proc_match is not None:
            context["rss_mb"] = round(proc_match.rss_mb, 1)
            context["cpu_pct"] = round(proc_match.cpu_percent, 1)

            # Build syscall hotspot summary from time percentages
            if proc_match.syscall_time_pct:
                top_syscall = max(
                    proc_match.syscall_time_pct.items(),
                    key=lambda x: x[1],
                )
                context["syscall_hotspot"] = (
                    f"{top_syscall[0]} {top_syscall[1]:.0%}"
                )

        # Collect active anomalies from the most recent detection cycle.
        # We check the anomaly detector against the latest snapshot and history.
        try:
            anomalies = self._anomaly_detector.detect(snapshot, self._history)
            context["anomalies_active"] = [
                a.anomaly_type.value for a in anomalies
            ]
        except Exception:
            _logger.debug("profiler.anomaly_check_for_context_failed", exc_info=True)

        return context

    def get_resource_context(self) -> dict[str, Any]:
        """Get general resource context (not PID-specific).

        Useful when no specific PID is available for the event.
        """
        context: dict[str, Any] = {
            "rss_mb": None,
            "cpu_pct": None,
            "syscall_hotspot": None,
            "anomalies_active": [],
        }

        snapshot = self._latest_snapshot
        if snapshot is None:
            return context

        context["daemon_rss_mb"] = snapshot.daemon_rss_mb
        context["pressure_level"] = snapshot.pressure_level
        context["running_jobs"] = snapshot.running_jobs
        context["active_sheets"] = snapshot.active_sheets

        try:
            anomalies = self._anomaly_detector.detect(snapshot, self._history)
            context["anomalies_active"] = [
                a.anomaly_type.value for a in anomalies
            ]
        except Exception:
            _logger.debug("profiler.anomaly_check_for_context_failed", exc_info=True)

        return context

    # ------------------------------------------------------------------
    # IPC methods
    # ------------------------------------------------------------------

    def get_latest_snapshot(self) -> dict[str, Any] | None:
        """Return the latest snapshot as a JSON-serializable dict.

        Used by the ``daemon.top`` IPC method.
        """
        if self._latest_snapshot is None:
            return None
        return self._latest_snapshot.model_dump(mode="json")

    def get_jsonl_path(self) -> str | None:
        """Return the JSONL streaming log path.

        Used by the ``daemon.top.stream`` IPC method.
        """
        if self._config.jsonl_path:
            return str(self._config.jsonl_path.expanduser())
        return None

    def get_recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent process events as JSON-serializable dicts.

        Used by the ``daemon.events`` IPC method.
        """
        events = self._recent_events[-limit:]
        return [e.model_dump(mode="json") for e in events]


__all__ = ["ProfilerCollector"]
