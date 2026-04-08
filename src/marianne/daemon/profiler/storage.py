"""SQLite + JSONL storage for profiler time-series data.

Provides ``MonitorStorage`` which writes system snapshots and process
events to a SQLite database and optionally appends NDJSON lines for
streaming consumers.  All SQLite I/O uses ``aiosqlite`` to avoid
blocking the event loop.

Read methods reconstruct Pydantic models from the stored rows,
and a ``cleanup()`` method enforces the configured retention policy.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from marianne.core.logging import get_logger
from marianne.daemon.profiler.models import (
    EventType,
    GpuMetric,
    ProcessEvent,
    ProcessMetric,
    RetentionConfig,
    SystemSnapshot,
)

_logger = get_logger("daemon.profiler.storage")

# ---------------------------------------------------------------------------
# SQL schema (matches the design doc exactly)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    timestamp REAL NOT NULL,
    daemon_pid INTEGER,
    system_memory_total_mb REAL,
    system_memory_available_mb REAL,
    system_memory_used_mb REAL,
    daemon_rss_mb REAL,
    child_count INTEGER,
    zombie_count INTEGER,
    load_avg_1 REAL,
    load_avg_5 REAL,
    load_avg_15 REAL,
    gpu_count INTEGER,
    gpu_utilization_pct TEXT,
    gpu_memory_used_mb TEXT,
    gpu_temperature TEXT,
    pressure_level TEXT,
    running_jobs INTEGER,
    active_sheets INTEGER
);

CREATE TABLE IF NOT EXISTS process_metrics (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER REFERENCES snapshots(id),
    pid INTEGER NOT NULL,
    ppid INTEGER,
    command TEXT,
    state TEXT,
    cpu_percent REAL,
    rss_mb REAL,
    vms_mb REAL,
    threads INTEGER,
    open_fds INTEGER,
    age_seconds REAL,
    job_id TEXT,
    sheet_num INTEGER,
    syscall_counts TEXT,
    syscall_time_pct TEXT
);

CREATE TABLE IF NOT EXISTS process_events (
    id INTEGER PRIMARY KEY,
    timestamp REAL NOT NULL,
    pid INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    exit_code INTEGER,
    signal_num INTEGER,
    job_id TEXT,
    sheet_num INTEGER,
    details TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_process_metrics_snapshot ON process_metrics(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_process_metrics_job ON process_metrics(job_id);
CREATE INDEX IF NOT EXISTS idx_process_events_ts ON process_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_process_events_job ON process_events(job_id);
"""


class MonitorStorage:
    """Async SQLite + JSONL storage for profiler time-series data.

    Uses aiosqlite for non-blocking database access and WAL mode
    for safe concurrent reads (``mzt top``) while the daemon writes.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Parent directories are
        created automatically.
    jsonl_path:
        Optional path for the NDJSON streaming log.  When provided,
        each snapshot is also appended as a single JSON line.
    jsonl_max_bytes:
        Maximum JSONL file size before rotation (default 50 MB).
    """

    def __init__(
        self,
        db_path: Path,
        jsonl_path: Path | None = None,
        jsonl_max_bytes: int = 52_428_800,
    ) -> None:
        self._db_path = Path(db_path).expanduser()
        self._jsonl_path = Path(jsonl_path).expanduser() if jsonl_path else None
        self._jsonl_max_bytes = jsonl_max_bytes
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._pool: aiosqlite.Connection | None = None

    # ── Connection helper ─────────────────────────────────────────────

    async def _get_connection(self) -> aiosqlite.Connection:
        """Return the pooled connection, creating it if needed.

        Reuses a single aiosqlite connection (and its backing thread)
        for the lifetime of this storage instance.  Previous code created
        a fresh connection per call, spawning a new thread each time —
        under WSL2 this exhausted the thread pool and cascaded into
        SIGABRT kills across the daemon (173 thread-exhaustion errors,
        3214 SIGABRT crashes in production logs).
        """
        if self._pool is not None:
            try:
                # Quick liveness check — if the thread died, recreate.
                await self._pool.execute("SELECT 1")
                return self._pool
            except Exception:
                await self._close_pool()

        db = await aiosqlite.connect(self._db_path)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = aiosqlite.Row
        self._pool = db
        return db

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get the pooled database connection with WAL mode and foreign keys."""
        db = await self._get_connection()
        yield db

    async def _close_pool(self) -> None:
        """Close the pooled connection if open."""
        if self._pool is not None:
            try:
                await self._pool.close()
            except Exception:
                pass
            self._pool = None

    async def close(self) -> None:
        """Close the pooled connection. Call on shutdown."""
        await self._close_pool()

    # ── Initialization ────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create tables and indexes if they don't exist."""
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            self._db_path.parent.mkdir(parents=True, exist_ok=True)

            async with self._connect() as db:
                await db.executescript(_SCHEMA_SQL)
                await db.commit()

            self._initialized = True
            _logger.info(
                "storage.initialized",
                db_path=str(self._db_path),
                jsonl_path=str(self._jsonl_path) if self._jsonl_path else None,
            )

    async def _ensure_initialized(self) -> None:
        """Lazy initialization on first use."""
        if not self._initialized:
            await self.initialize()

    # ── Write operations ──────────────────────────────────────────────

    async def write_snapshot(self, snapshot: SystemSnapshot) -> int:
        """Insert a snapshot and its process metrics into the database.

        Returns the snapshot row ID for cross-referencing.
        """
        await self._ensure_initialized()

        # Serialize GPU lists as JSON arrays
        gpu_util = json.dumps([g.utilization_pct for g in snapshot.gpus])
        gpu_mem = json.dumps([g.memory_used_mb for g in snapshot.gpus])
        gpu_temp = json.dumps([g.temperature_c for g in snapshot.gpus])

        async with self._connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO snapshots (
                    timestamp, daemon_pid,
                    system_memory_total_mb, system_memory_available_mb,
                    system_memory_used_mb, daemon_rss_mb,
                    child_count, zombie_count,
                    load_avg_1, load_avg_5, load_avg_15,
                    gpu_count, gpu_utilization_pct, gpu_memory_used_mb,
                    gpu_temperature, pressure_level,
                    running_jobs, active_sheets
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.timestamp,
                    snapshot.daemon_pid,
                    snapshot.system_memory_total_mb,
                    snapshot.system_memory_available_mb,
                    snapshot.system_memory_used_mb,
                    snapshot.daemon_rss_mb,
                    len(snapshot.processes),
                    snapshot.zombie_count,
                    snapshot.load_avg_1,
                    snapshot.load_avg_5,
                    snapshot.load_avg_15,
                    len(snapshot.gpus),
                    gpu_util,
                    gpu_mem,
                    gpu_temp,
                    snapshot.pressure_level,
                    snapshot.running_jobs,
                    snapshot.active_sheets,
                ),
            )
            snapshot_id = cursor.lastrowid
            assert snapshot_id is not None

            # Insert per-process metrics
            for proc in snapshot.processes:
                await db.execute(
                    """
                    INSERT INTO process_metrics (
                        snapshot_id, pid, ppid, command, state,
                        cpu_percent, rss_mb, vms_mb, threads, open_fds,
                        age_seconds, job_id, sheet_num,
                        syscall_counts, syscall_time_pct
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        proc.pid,
                        proc.ppid,
                        proc.command,
                        proc.state,
                        proc.cpu_percent,
                        proc.rss_mb,
                        proc.vms_mb,
                        proc.threads,
                        proc.open_fds,
                        proc.age_seconds,
                        proc.job_id,
                        proc.sheet_num,
                        json.dumps(proc.syscall_counts) if proc.syscall_counts else "{}",
                        json.dumps(proc.syscall_time_pct) if proc.syscall_time_pct else "{}",
                    ),
                )

            await db.commit()

        return snapshot_id

    async def write_event(self, event: ProcessEvent) -> None:
        """Insert a process lifecycle event."""
        await self._ensure_initialized()

        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO process_events (
                    timestamp, pid, event_type,
                    exit_code, signal_num,
                    job_id, sheet_num, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp,
                    event.pid,
                    (event.event_type.value
                     if isinstance(event.event_type, EventType)
                     else event.event_type),
                    event.exit_code,
                    event.signal_num,
                    event.job_id,
                    event.sheet_num,
                    event.details,
                ),
            )
            await db.commit()

    # ── Read operations ───────────────────────────────────────────────

    async def read_snapshots(
        self, since: float, limit: int = 100
    ) -> list[SystemSnapshot]:
        """Read snapshots since the given unix timestamp.

        Returns snapshots in chronological order, most recent last.
        Process metrics are reconstructed for each snapshot.
        """
        await self._ensure_initialized()

        snapshots: list[SystemSnapshot] = []

        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT id, timestamp, daemon_pid,
                       system_memory_total_mb, system_memory_available_mb,
                       system_memory_used_mb, daemon_rss_mb,
                       child_count, zombie_count,
                       load_avg_1, load_avg_5, load_avg_15,
                       gpu_count, gpu_utilization_pct, gpu_memory_used_mb,
                       gpu_temperature, pressure_level,
                       running_jobs, active_sheets
                FROM snapshots
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (since, limit),
            )
            rows = await cursor.fetchall()

            for row in rows:
                snapshot_id = row[0]

                # Reconstruct GPU metrics from JSON arrays
                gpu_count = row[12] or 0
                gpu_util = json.loads(row[13]) if row[13] else []
                gpu_mem = json.loads(row[14]) if row[14] else []
                gpu_temp = json.loads(row[15]) if row[15] else []

                gpus = [
                    GpuMetric(
                        index=i,
                        utilization_pct=gpu_util[i] if i < len(gpu_util) else 0.0,
                        memory_used_mb=gpu_mem[i] if i < len(gpu_mem) else 0.0,
                        temperature_c=gpu_temp[i] if i < len(gpu_temp) else 0.0,
                    )
                    for i in range(gpu_count)
                ]

                # Load process metrics for this snapshot
                proc_cursor = await db.execute(
                    """
                    SELECT pid, ppid, command, state,
                           cpu_percent, rss_mb, vms_mb, threads, open_fds,
                           age_seconds, job_id, sheet_num,
                           syscall_counts, syscall_time_pct
                    FROM process_metrics
                    WHERE snapshot_id = ?
                    """,
                    (snapshot_id,),
                )
                proc_rows = await proc_cursor.fetchall()

                processes = [
                    ProcessMetric(
                        pid=pr[0],
                        ppid=pr[1] or 0,
                        command=pr[2] or "",
                        state=pr[3] or "S",
                        cpu_percent=pr[4] or 0.0,
                        rss_mb=pr[5] or 0.0,
                        vms_mb=pr[6] or 0.0,
                        threads=pr[7] or 1,
                        open_fds=pr[8] or 0,
                        age_seconds=pr[9] or 0.0,
                        job_id=pr[10],
                        sheet_num=pr[11],
                        syscall_counts=json.loads(pr[12]) if pr[12] else {},
                        syscall_time_pct=json.loads(pr[13]) if pr[13] else {},
                    )
                    for pr in proc_rows
                ]

                snapshots.append(
                    SystemSnapshot(
                        timestamp=row[1],
                        daemon_pid=row[2] or 0,
                        system_memory_total_mb=row[3] or 0.0,
                        system_memory_available_mb=row[4] or 0.0,
                        system_memory_used_mb=row[5] or 0.0,
                        daemon_rss_mb=row[6] or 0.0,
                        load_avg_1=row[9] or 0.0,
                        load_avg_5=row[10] or 0.0,
                        load_avg_15=row[11] or 0.0,
                        processes=processes,
                        gpus=gpus,
                        pressure_level=row[16] or "none",
                        running_jobs=row[17] or 0,
                        active_sheets=row[18] or 0,
                        zombie_count=row[8] or 0,
                    )
                )

        return snapshots

    async def read_events(
        self, since: float, limit: int = 100
    ) -> list[ProcessEvent]:
        """Read process events since the given unix timestamp."""
        await self._ensure_initialized()

        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT timestamp, pid, event_type,
                       exit_code, signal_num,
                       job_id, sheet_num, details
                FROM process_events
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (since, limit),
            )
            rows = await cursor.fetchall()

        return [
            ProcessEvent(
                timestamp=row[0],
                pid=row[1],
                event_type=EventType(row[2]),
                exit_code=row[3],
                signal_num=row[4],
                job_id=row[5],
                sheet_num=row[6],
                details=row[7] or "",
            )
            for row in rows
        ]

    async def read_process_history(
        self, pid: int, since: float
    ) -> list[ProcessMetric]:
        """Read historical metrics for a specific process."""
        await self._ensure_initialized()

        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT pm.pid, pm.ppid, pm.command, pm.state,
                       pm.cpu_percent, pm.rss_mb, pm.vms_mb,
                       pm.threads, pm.open_fds, pm.age_seconds,
                       pm.job_id, pm.sheet_num,
                       pm.syscall_counts, pm.syscall_time_pct
                FROM process_metrics pm
                JOIN snapshots s ON s.id = pm.snapshot_id
                WHERE pm.pid = ? AND s.timestamp >= ?
                ORDER BY s.timestamp ASC
                """,
                (pid, since),
            )
            rows = await cursor.fetchall()

        return [
            ProcessMetric(
                pid=row[0],
                ppid=row[1] or 0,
                command=row[2] or "",
                state=row[3] or "S",
                cpu_percent=row[4] or 0.0,
                rss_mb=row[5] or 0.0,
                vms_mb=row[6] or 0.0,
                threads=row[7] or 1,
                open_fds=row[8] or 0,
                age_seconds=row[9] or 0.0,
                job_id=row[10],
                sheet_num=row[11],
                syscall_counts=json.loads(row[12]) if row[12] else {},
                syscall_time_pct=json.loads(row[13]) if row[13] else {},
            )
            for row in rows
        ]

    async def read_job_resource_profile(self, job_id: str) -> dict[str, Any]:
        """Aggregate resource profile for a specific job.

        Returns a dict with peak memory, total CPU-time, process spawn
        count, and syscall hotspots — useful for scheduling hints and
        ``mzt diagnose --resources``.
        """
        await self._ensure_initialized()

        profile: dict[str, Any] = {
            "job_id": job_id,
            "peak_rss_mb": 0.0,
            "total_cpu_seconds": 0.0,
            "process_spawn_count": 0,
            "unique_pids": set(),
            "syscall_hotspots": {},
            "sheet_metrics": {},
        }

        async with self._connect() as db:
            # Per-process metrics aggregated by job
            cursor = await db.execute(
                """
                SELECT pm.pid, pm.sheet_num, pm.rss_mb, pm.cpu_percent,
                       pm.syscall_time_pct, s.timestamp
                FROM process_metrics pm
                JOIN snapshots s ON s.id = pm.snapshot_id
                WHERE pm.job_id = ?
                ORDER BY s.timestamp ASC
                """,
                (job_id,),
            )
            rows = await cursor.fetchall()

            for row in rows:
                pid = row[0]
                sheet_num = row[1]
                rss_mb = row[2] or 0.0
                cpu_pct = row[3] or 0.0
                syscall_pct_json = row[4]

                profile["unique_pids"].add(pid)
                if rss_mb > profile["peak_rss_mb"]:
                    profile["peak_rss_mb"] = rss_mb

                # Accumulate syscall time across all observations
                if syscall_pct_json:
                    for sc, pct in json.loads(syscall_pct_json).items():
                        profile["syscall_hotspots"][sc] = (
                            profile["syscall_hotspots"].get(sc, 0.0) + pct
                        )

                # Track per-sheet peak memory
                if sheet_num is not None:
                    key = str(sheet_num)
                    if key not in profile["sheet_metrics"]:
                        profile["sheet_metrics"][key] = {
                            "peak_rss_mb": 0.0,
                            "max_cpu_pct": 0.0,
                        }
                    sm = profile["sheet_metrics"][key]
                    if rss_mb > sm["peak_rss_mb"]:
                        sm["peak_rss_mb"] = rss_mb
                    if cpu_pct > sm["max_cpu_pct"]:
                        sm["max_cpu_pct"] = cpu_pct

            # Count spawn events for this job
            event_cursor = await db.execute(
                """
                SELECT COUNT(*) FROM process_events
                WHERE job_id = ? AND event_type = ?
                """,
                (job_id, EventType.SPAWN.value),
            )
            spawn_row = await event_cursor.fetchone()
            profile["process_spawn_count"] = spawn_row[0] if spawn_row else 0

        # Convert set to count for JSON serialization
        profile["unique_pid_count"] = len(profile["unique_pids"])
        del profile["unique_pids"]

        return profile

    # ── Retention / cleanup ───────────────────────────────────────────

    async def cleanup(self, retention: RetentionConfig) -> None:
        """Apply retention policy by deleting old data.

        - Snapshots + process_metrics older than full_resolution_hours
        - Process events older than events_days
        """
        await self._ensure_initialized()

        now = time.time()
        snapshot_cutoff = now - (retention.full_resolution_hours * 3600)
        event_cutoff = now - (retention.events_days * 86400)

        async with self._connect() as db:
            # Delete process metrics for old snapshots first (FK constraint)
            await db.execute(
                """
                DELETE FROM process_metrics
                WHERE snapshot_id IN (
                    SELECT id FROM snapshots WHERE timestamp < ?
                )
                """,
                (snapshot_cutoff,),
            )

            # Delete old snapshots
            await db.execute(
                "DELETE FROM snapshots WHERE timestamp < ?",
                (snapshot_cutoff,),
            )

            # Delete old events
            await db.execute(
                "DELETE FROM process_events WHERE timestamp < ?",
                (event_cutoff,),
            )

            await db.commit()

        _logger.info(
            "storage.cleanup_complete",
            snapshot_cutoff_hours=retention.full_resolution_hours,
            event_cutoff_days=retention.events_days,
        )

    # ── JSONL streaming ───────────────────────────────────────────────

    def append_jsonl(self, snapshot: SystemSnapshot) -> None:
        """Append one NDJSON line for the given snapshot.

        Synchronous I/O — callers should wrap in ``run_in_executor``
        if strict non-blocking is required.  In practice the writes are
        small and fast enough for the daemon's collection loop.

        Performs size-based rotation: when the file exceeds
        ``jsonl_max_bytes``, renames it with a ``.1`` suffix (keeping
        at most 2 rotated files) and starts a new file.
        """
        if self._jsonl_path is None:
            return

        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)

        # Rotate if needed
        if self._jsonl_path.exists():
            try:
                size = self._jsonl_path.stat().st_size
            except OSError:
                size = 0
            if size >= self._jsonl_max_bytes:
                self._rotate_jsonl()

        # Serialize using Pydantic's model_dump for reliable output
        line = json.dumps(snapshot.model_dump(), separators=(",", ":"))
        try:
            with self._jsonl_path.open("a") as f:
                f.write(line + "\n")
        except OSError:
            _logger.warning("storage.jsonl_write_failed", exc_info=True)

    def _rotate_jsonl(self) -> None:
        """Rotate JSONL file, keeping at most 2 old copies."""
        if self._jsonl_path is None:
            return

        rotated_2 = self._jsonl_path.with_suffix(".jsonl.2")
        rotated_1 = self._jsonl_path.with_suffix(".jsonl.1")

        try:
            if rotated_2.exists():
                rotated_2.unlink()
            if rotated_1.exists():
                rotated_1.rename(rotated_2)
            self._jsonl_path.rename(rotated_1)
        except OSError:
            _logger.warning("storage.jsonl_rotate_failed", exc_info=True)


async def generate_resource_report(job_id: str, storage: MonitorStorage) -> str:
    """Generate comprehensive resource report for a job.

    Produces a text report designed for AI consumption (``mzt diagnose
    --resources``).  Aggregates peak memory per sheet, total CPU-time,
    process spawn count, signal/kill events, zombie/OOM events, syscall
    hotspots, and anomaly history.

    Args:
        job_id: The job ID to generate the report for.
        storage: An initialized ``MonitorStorage`` instance.

    Returns:
        Multi-line text report.  Returns a short "no data" message if the
        job has no profiler data.
    """
    profile = await storage.read_job_resource_profile(job_id)

    if not profile or profile.get("unique_pid_count", 0) == 0:
        return f"Resource Profile for job '{job_id}':\n  No profiler data available.\n"

    lines: list[str] = [f"Resource Profile for job '{job_id}':"]

    # -- Summary metrics --
    peak_rss = profile.get("peak_rss_mb", 0.0)
    spawn_count = profile.get("process_spawn_count", 0)
    unique_pids = profile.get("unique_pid_count", 0)
    lines.append(f"  Peak memory: {peak_rss:.1f} MB")
    lines.append(f"  Process spawns: {spawn_count}")
    lines.append(f"  Unique PIDs observed: {unique_pids}")

    # -- Per-sheet breakdown --
    sheet_metrics = profile.get("sheet_metrics", {})
    if sheet_metrics:
        lines.append("")
        lines.append("  Per-sheet resource peaks:")
        for sheet_num in sorted(sheet_metrics.keys(), key=lambda x: int(x)):
            sm = sheet_metrics[sheet_num]
            peak = sm.get("peak_rss_mb", 0.0)
            cpu = sm.get("max_cpu_pct", 0.0)
            lines.append(f"    Sheet {sheet_num}: peak RSS {peak:.1f} MB, max CPU {cpu:.0f}%")

    # -- Syscall hotspots --
    hotspots = profile.get("syscall_hotspots", {})
    if hotspots:
        sorted_sc = sorted(hotspots.items(), key=lambda x: x[1], reverse=True)
        lines.append("")
        lines.append("  Syscall hotspots (cumulative time %):")
        for name, pct in sorted_sc[:10]:
            lines.append(f"    {name}: {pct:.1f}%")

    # -- Process events (signals, kills, OOM) --
    # Read events for this job from the last 7 days
    events = await storage.read_events(since=time.time() - 86400 * 7, limit=10000)
    job_events = [e for e in events if e.job_id == job_id]

    if job_events:
        signal_events = [
            e for e in job_events
            if e.event_type in (EventType.SIGNAL, EventType.KILL)
        ]
        oom_events = [e for e in job_events if e.event_type == EventType.OOM]
        exit_events = [e for e in job_events if e.event_type == EventType.EXIT]

        if signal_events:
            lines.append("")
            lines.append(f"  Signals sent: {len(signal_events)}")
            for evt in signal_events[:10]:
                sig = f"signal={evt.signal_num}" if evt.signal_num else ""
                lines.append(f"    PID {evt.pid} {sig} {evt.details}")

        if oom_events:
            lines.append("")
            lines.append(f"  OOM events: {len(oom_events)}")
            for evt in oom_events[:5]:
                lines.append(f"    PID {evt.pid}: {evt.details}")

        # Retry count (spawn events > exit events indicates retries)
        spawn_events = [e for e in job_events if e.event_type == EventType.SPAWN]
        total_spawns = len(spawn_events)
        total_exits = len(exit_events)
        if total_spawns > 0:
            lines.append("")
            lines.append(f"  Process lifecycle: {total_spawns} spawns, {total_exits} exits")

    lines.append("")
    return "\n".join(lines)


__all__ = ["MonitorStorage", "generate_resource_report"]
