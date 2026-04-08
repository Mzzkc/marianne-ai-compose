"""Persistent job registry for the Marianne daemon.

SQLite-backed registry that tracks all jobs submitted to the daemon.
Survives daemon restarts so ``mzt list`` always shows job history.

Separate from the learning store (which tracks patterns across jobs).
This DB tracks operational state: which jobs exist, their workspaces,
PIDs, and statuses.

All database methods are async (via ``aiosqlite``) so they never block
the daemon's asyncio event loop — even under heavy concurrent load.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import aiosqlite

from marianne.core.logging import get_logger

_logger = get_logger("daemon.registry")

# Statuses that represent a finished job (used for completed_at timestamps,
# orphan detection, and delete_jobs safety checks).
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_ACTIVE_STATUSES = frozenset({"queued", "running"})


class DaemonJobStatus(str, Enum):
    """Status values for daemon-managed jobs.

    Inherits from ``str`` so ``meta.status`` serializes directly as
    a plain string in JSON/dict output — no ``.value`` calls needed.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PENDING = "pending"


@dataclass
class JobRecord:
    """A single job's registry entry."""

    job_id: str
    config_path: str
    workspace: str
    status: DaemonJobStatus = DaemonJobStatus.QUEUED
    pid: int | None = None
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    error_message: str | None = None
    current_sheet: int | None = None
    total_sheets: int | None = None
    last_event_at: float | None = None
    log_path: str | None = None
    snapshot_path: str | None = None
    checkpoint_json: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON-RPC responses."""
        result: dict[str, Any] = {
            "job_id": self.job_id,
            "config_path": self.config_path,
            "workspace": self.workspace,
            "status": self.status,
            "pid": self.pid,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_sheet": self.current_sheet,
            "total_sheets": self.total_sheets,
        }
        if self.error_message:
            result["error_message"] = self.error_message
        if self.log_path:
            result["log_path"] = self.log_path
        if self.snapshot_path:
            result["snapshot_path"] = self.snapshot_path
        return result


class JobRegistry:
    """Async SQLite-backed persistent job registry.

    Uses ``aiosqlite`` so all I/O happens off the event loop thread.
    The daemon is single-threaded (asyncio) so contention is minimal,
    but the DB is safe for external readers (e.g. a monitoring tool
    reading the same file).

    Usage::

        registry = JobRegistry(db_path)
        await registry.open()   # creates tables, sets WAL mode
        ...
        await registry.close()

    Or as an async context manager::

        async with JobRegistry(db_path) as registry:
            await registry.register_job(...)
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Open the database connection and create tables."""
        conn = await aiosqlite.connect(str(self._db_path))
        try:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await self._create_tables(conn)
        except Exception:
            await conn.close()
            raise
        self._conn = conn
        _logger.info("registry.opened", path=str(self._db_path))

    @property
    def _db(self) -> aiosqlite.Connection:
        """Get the active connection, raising if not opened."""
        if self._conn is None:
            raise RuntimeError("JobRegistry not opened — call open() first")
        return self._conn

    @staticmethod
    async def _create_tables(conn: aiosqlite.Connection) -> None:
        """Create tables if they don't exist."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                config_path TEXT NOT NULL,
                workspace TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                pid INTEGER,
                submitted_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                error_message TEXT,
                current_sheet INTEGER,
                total_sheets INTEGER,
                last_event_at REAL,
                log_path TEXT,
                snapshot_path TEXT
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_status
            ON jobs (status)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_submitted
            ON jobs (submitted_at DESC)
        """)
        await JobRegistry._migrate_schema(conn)
        await conn.commit()

    @staticmethod
    async def _migrate_schema(conn: aiosqlite.Connection) -> None:
        """Add columns that may be missing from older databases."""
        new_columns = [
            ("current_sheet", "INTEGER"),
            ("total_sheets", "INTEGER"),
            ("last_event_at", "REAL"),
            ("log_path", "TEXT"),
            ("snapshot_path", "TEXT"),
            ("checkpoint_json", "TEXT"),
            ("hook_config_json", "TEXT"),
            ("hook_results_json", "TEXT"),
        ]
        for col_name, col_type in new_columns:
            try:
                await conn.execute(
                    f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}"
                )
            except sqlite3.OperationalError:
                _logger.debug("registry.migrate_column_exists", column=col_name)
            except sqlite3.DatabaseError:
                _logger.warning(
                    "registry.migrate_unexpected_error",
                    column=col_name,
                    exc_info=True,
                )

    async def register_job(
        self,
        job_id: str,
        config_path: Path,
        workspace: Path,
    ) -> None:
        """Register a newly submitted job."""
        await self._db.execute(
            """
            INSERT OR REPLACE INTO jobs
                (job_id, config_path, workspace, status, submitted_at)
            VALUES (?, ?, ?, 'queued', ?)
            """,
            (job_id, str(config_path), str(workspace), time.time()),
        )
        await self._db.commit()

    async def update_status(
        self,
        job_id: str,
        status: str,
        *,
        pid: int | None = None,
        error_message: str | None = None,
        snapshot_path: str | None = None,
    ) -> None:
        """Update a job's status and optional fields."""
        updates = ["status = ?"]
        params: list[Any] = [status]

        if pid is not None:
            updates.append("pid = ?")
            params.append(pid)

        if status == "running" and pid is not None:
            updates.append("started_at = ?")
            params.append(time.time())

        if status in _TERMINAL_STATUSES:
            updates.append("completed_at = ?")
            params.append(time.time())

        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        if snapshot_path is not None:
            updates.append("snapshot_path = ?")
            params.append(snapshot_path)

        params.append(job_id)
        await self._db.execute(
            f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = ?",
            params,
        )
        await self._db.commit()

    async def update_config_metadata(
        self,
        job_id: str,
        *,
        config_path: str | None = None,
        workspace: str | None = None,
    ) -> None:
        """Update config-derived metadata for a job.

        Called during config reconciliation to keep registry in sync
        with the reloaded config.
        """
        updates: list[str] = []
        params: list[Any] = []

        if config_path is not None:
            updates.append("config_path = ?")
            params.append(config_path)
        if workspace is not None:
            updates.append("workspace = ?")
            params.append(workspace)

        if not updates:
            return

        params.append(job_id)
        await self._db.execute(
            f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = ?",
            params,
        )
        await self._db.commit()

    async def update_progress(
        self,
        job_id: str,
        current_sheet: int,
        total_sheets: int,
    ) -> None:
        """Update per-sheet progress counters for a running job."""
        await self._db.execute(
            "UPDATE jobs SET current_sheet = ?, total_sheets = ?, "
            "last_event_at = ? WHERE job_id = ?",
            (current_sheet, total_sheets, time.time(), job_id),
        )
        await self._db.commit()

    async def save_checkpoint(self, job_id: str, checkpoint_json: str) -> None:
        """Persist a serialized CheckpointState for a job.

        Called on every state publish so the registry always has the
        latest checkpoint.  This is the daemon's single source of
        truth for historical job status — no disk fallback needed.
        """
        await self._db.execute(
            "UPDATE jobs SET checkpoint_json = ?, last_event_at = ? "
            "WHERE job_id = ?",
            (checkpoint_json, time.time(), job_id),
        )
        await self._db.commit()

    async def load_checkpoint(self, job_id: str) -> str | None:
        """Load the stored checkpoint JSON for a job.

        Returns the raw JSON string, or None if no checkpoint was saved.
        """
        cursor = await self._db.execute(
            "SELECT checkpoint_json FROM jobs WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result: str | None = row["checkpoint_json"]
        return result

    async def store_hook_config(self, job_id: str, config_json: str) -> None:
        """Store hook configuration for a job at submission time."""
        await self._db.execute(
            "UPDATE jobs SET hook_config_json = ? WHERE job_id = ?",
            (config_json, job_id),
        )
        await self._db.commit()

    async def get_hook_config(self, job_id: str) -> str | None:
        """Load stored hook config JSON for a job."""
        cursor = await self._db.execute(
            "SELECT hook_config_json FROM jobs WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result: str | None = row["hook_config_json"]
        return result

    async def store_hook_results(self, job_id: str, results_json: str) -> None:
        """Store hook execution results for a job."""
        await self._db.execute(
            "UPDATE jobs SET hook_results_json = ? WHERE job_id = ?",
            (results_json, job_id),
        )
        await self._db.commit()

    async def get_job(self, job_id: str) -> JobRecord | None:
        """Get a single job by ID."""
        cursor = await self._db.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[JobRecord]:
        """List jobs, most recent first."""
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY submitted_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM jobs ORDER BY submitted_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [self._row_to_record(r) for r in rows]

    async def has_active_job(self, job_id: str) -> bool:
        """Check if a job ID exists and is in an active state."""
        cursor = await self._db.execute(
            "SELECT 1 FROM jobs WHERE job_id = ? AND status IN ('queued', 'running')",
            (job_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def get_orphaned_jobs(self) -> list[JobRecord]:
        """Find jobs that were running when the daemon last stopped.

        These are jobs with status 'queued' or 'running' — after a daemon
        restart they're orphans since their asyncio tasks no longer exist.
        """
        cursor = await self._db.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'running') "
            "ORDER BY submitted_at DESC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_record(r) for r in rows]

    async def mark_orphans_failed(self) -> int:
        """Mark all orphaned jobs as failed on daemon startup.

        Returns the number of jobs marked.
        """
        cursor = await self._db.execute(
            """
            UPDATE jobs SET
                status = 'failed',
                completed_at = ?,
                error_message = 'Daemon restarted while job was active'
            WHERE status IN ('queued', 'running')
            """,
            (time.time(),),
        )
        await self._db.commit()
        count = cursor.rowcount
        if count > 0:
            _logger.warning("registry.orphans_marked_failed", count=count)
        return count

    async def delete_jobs(
        self,
        *,
        job_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        older_than_seconds: float | None = None,
    ) -> int:
        """Delete terminal jobs from the registry.

        Never deletes active jobs (queued/running) regardless of filter.

        Args:
            job_ids: Only delete these specific job IDs.
            statuses: Only delete jobs with these statuses.
                      Defaults to all terminal statuses.
            older_than_seconds: Only delete jobs older than this many seconds.

        Returns:
            Number of deleted rows.
        """
        safe = set(statuses or _TERMINAL_STATUSES)
        safe -= _ACTIVE_STATUSES

        conditions = ["status IN ({})".format(",".join("?" for _ in safe))]
        params: list[Any] = list(safe)

        if job_ids is not None:
            conditions.append(
                "job_id IN ({})".format(",".join("?" for _ in job_ids))
            )
            params.extend(job_ids)

        if older_than_seconds is not None:
            conditions.append("submitted_at < ?")
            params.append(time.time() - older_than_seconds)

        sql = "DELETE FROM jobs WHERE " + " AND ".join(conditions)
        cursor = await self._db.execute(sql, params)
        await self._db.commit()
        count = cursor.rowcount
        if count > 0:
            _logger.info("registry.delete_jobs", deleted=count)
        return count

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> JobRegistry:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @staticmethod
    def _row_to_record(row: aiosqlite.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            config_path=row["config_path"],
            workspace=row["workspace"],
            status=DaemonJobStatus(row["status"]),
            pid=row["pid"],
            submitted_at=row["submitted_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error_message=row["error_message"],
            current_sheet=row["current_sheet"],
            total_sheets=row["total_sheets"],
            last_event_at=row["last_event_at"],
            log_path=row["log_path"],
            snapshot_path=row["snapshot_path"],
            checkpoint_json=row["checkpoint_json"],
        )
