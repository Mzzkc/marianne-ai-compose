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

import math
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import aiosqlite

from marianne.core.logging import get_logger

_logger = get_logger("daemon.registry")

class DaemonJobStatus(str, Enum):
    """Status values for daemon-managed jobs.

    Inherits from ``str`` so ``meta.status`` serializes directly as
    a plain string in JSON/dict output — no ``.value`` calls needed.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PAUSED = "paused"
    PAUSED_AT_CHAIN = "paused_at_chain"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PENDING = "pending"


# Status groupings, derived from the enum (#268) so a value rename can't leave a
# stale string literal behind. Terminal = finished job (used for completed_at
# timestamps, orphan detection, delete_jobs safety); active = currently running.
_TERMINAL_STATUSES = frozenset(
    {
        DaemonJobStatus.COMPLETED.value,
        DaemonJobStatus.FAILED.value,
        DaemonJobStatus.CANCELLED.value,
    }
)
_ACTIVE_STATUSES = frozenset(
    {DaemonJobStatus.QUEUED.value, DaemonJobStatus.RUNNING.value}
)


class FailureHooksInProgressError(RuntimeError):
    """Raised when a stable job ID still owns an unsettled hook claim."""


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
    max_wall_seconds: float | None = None
    wall_deadline_at: float | None = None
    terminal_reason: str | None = None
    deadline_diagnostic: str | None = field(default=None, repr=False)
    failure_hooks_started_at: float | None = None
    failure_hooks_completed_at: float | None = None
    chain_depth: int | None = None

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
        if self.max_wall_seconds is not None:
            result["max_wall_seconds"] = self.max_wall_seconds
        if self.wall_deadline_at is not None:
            result["wall_deadline_at"] = self.wall_deadline_at
        if self.terminal_reason is not None:
            result["terminal_reason"] = self.terminal_reason
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
            ("failure_hook_config_json", "TEXT"),
            ("failure_hook_results_json", "TEXT"),
            ("failure_hooks_started_at", "REAL"),
            ("failure_hooks_completed_at", "REAL"),
            ("concert_config_json", "TEXT"),
            ("chain_depth", "INTEGER"),
            ("max_wall_seconds", "REAL"),
            ("wall_deadline_at", "REAL"),
            ("terminal_reason", "TEXT"),
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
        log_path: Path | None = None,
        *,
        submitted_at: float | None = None,
        max_wall_seconds: float | None = None,
        wall_deadline_at: float | None = None,
        concert_config_json: str | None = None,
        chain_depth: int | None = None,
    ) -> JobRecord:
        """Commit and return one newly submitted execution authority."""
        registered_at = time.time() if submitted_at is None else submitted_at
        if not math.isfinite(registered_at):
            raise ValueError("submitted_at must be finite")
        if max_wall_seconds is not None:
            if not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0:
                raise ValueError("max_wall_seconds must be finite and positive")
            if wall_deadline_at is None:
                wall_deadline_at = registered_at + max_wall_seconds
        if wall_deadline_at is not None and (
            not math.isfinite(wall_deadline_at) or wall_deadline_at <= 0
        ):
            raise ValueError("wall_deadline_at must be finite and positive")
        # Reaching this seam means manager admission accepted a new execution.
        # Continuations never call register_job; an accepted stable-ID rerun
        # intentionally replaces the prior execution authority atomically.
        cursor = await self._db.execute(
            """
            INSERT INTO jobs
                (job_id, config_path, workspace, status, submitted_at, log_path,
                 max_wall_seconds, wall_deadline_at, terminal_reason,
                 concert_config_json, chain_depth)
            VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                config_path = excluded.config_path,
                workspace = excluded.workspace,
                status = 'queued',
                pid = NULL,
                started_at = NULL,
                completed_at = NULL,
                error_message = NULL,
                current_sheet = NULL,
                total_sheets = NULL,
                last_event_at = NULL,
                log_path = excluded.log_path,
                snapshot_path = NULL,
                checkpoint_json = NULL,
                hook_config_json = NULL,
                hook_results_json = NULL,
                failure_hook_config_json = NULL,
                failure_hook_results_json = NULL,
                failure_hooks_started_at = NULL,
                failure_hooks_completed_at = NULL,
                concert_config_json = excluded.concert_config_json,
                chain_depth = excluded.chain_depth,
                submitted_at = excluded.submitted_at,
                max_wall_seconds = excluded.max_wall_seconds,
                wall_deadline_at = excluded.wall_deadline_at,
                terminal_reason = NULL
            WHERE jobs.failure_hooks_started_at IS NULL
               OR jobs.failure_hooks_completed_at IS NOT NULL
            """,
            (
                job_id,
                str(config_path),
                str(workspace),
                registered_at,
                str(log_path) if log_path is not None else None,
                max_wall_seconds,
                wall_deadline_at,
                concert_config_json,
                chain_depth,
            ),
        )
        if cursor.rowcount != 1:
            await self._db.rollback()
            raise FailureHooksInProgressError(
                f"Job '{job_id}' is still running terminal failure hooks"
            )
        await self._db.commit()
        committed = await self.get_job(job_id)
        if committed is None:
            raise RuntimeError(f"registered job '{job_id}' was not readable")
        return committed

    async def update_status(
        self,
        job_id: str,
        status: str,
        *,
        pid: int | None = None,
        error_message: str | None = None,
        snapshot_path: str | None = None,
        terminal_reason: str | None = None,
    ) -> None:
        """Update a job's status and optional fields in the registry only.

        Breadcrumb (#263): this writes ONLY the registry row. It does NOT touch
        the daemon's in-memory ``_job_meta`` / ``_live_states``, so calling it
        directly for a live job diverges ``mzt list`` from ``mzt status``.
        Status changes for live jobs MUST go through
        ``JobManager._set_job_status`` (the three-store update). Direct use is
        reserved for paths with no in-memory state (startup orphan recovery,
        task-creation-failure cleanup) or registry/live reconciliation
        (shutdown flush) — see ``test_set_job_status_guard_263`` for the
        enforced allow-list.
        """
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

        if status not in _TERMINAL_STATUSES:
            updates.append("terminal_reason = NULL")
        elif terminal_reason is not None:
            updates.append("terminal_reason = ?")
            params.append(terminal_reason)

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

    async def get_hook_results(self, job_id: str) -> str | None:
        """Load stored hook execution results JSON for a job."""
        cursor = await self._db.execute(
            "SELECT hook_results_json FROM jobs WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result: str | None = row["hook_results_json"]
        return result

    async def store_failure_hook_config(self, job_id: str, config_json: str) -> None:
        """Store terminal-failure hook configuration for restart recovery."""
        await self._db.execute(
            "UPDATE jobs SET failure_hook_config_json = ? WHERE job_id = ?",
            (config_json, job_id),
        )
        await self._db.commit()

    async def get_failure_hook_config(self, job_id: str) -> str | None:
        """Load terminal-failure hook configuration for a job."""
        cursor = await self._db.execute(
            "SELECT failure_hook_config_json FROM jobs WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result: str | None = row["failure_hook_config_json"]
        return result

    async def store_concert_config(self, job_id: str, config_json: str) -> None:
        """Store concert context used by durable run_job hooks."""
        await self._db.execute(
            "UPDATE jobs SET concert_config_json = ? WHERE job_id = ?",
            (config_json, job_id),
        )
        await self._db.commit()

    async def store_concert_context(
        self,
        job_id: str,
        config_json: str | None,
        chain_depth: int | None,
    ) -> None:
        """Persist the concert policy and current depth in one update."""
        await self._db.execute(
            """
            UPDATE jobs
            SET concert_config_json = ?, chain_depth = ?
            WHERE job_id = ?
            """,
            (config_json, chain_depth, job_id),
        )
        await self._db.commit()

    async def get_concert_config(self, job_id: str) -> str | None:
        """Load concert context used by durable run_job hooks."""
        cursor = await self._db.execute(
            "SELECT concert_config_json FROM jobs WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result: str | None = row["concert_config_json"]
        return result

    async def store_failure_hook_results(self, job_id: str, results_json: str) -> None:
        """Store terminal-failure hook results without replacing job failure data."""
        await self._db.execute(
            "UPDATE jobs SET failure_hook_results_json = ? WHERE job_id = ?",
            (results_json, job_id),
        )
        await self._db.commit()

    async def get_failure_hook_results(self, job_id: str) -> str | None:
        """Load terminal-failure hook results for diagnostics."""
        cursor = await self._db.execute(
            "SELECT failure_hook_results_json FROM jobs WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result: str | None = row["failure_hook_results_json"]
        return result

    async def claim_failure_hooks(self, job_id: str) -> bool:
        """Atomically claim a failed job's hook execution exactly once."""
        cursor = await self._db.execute(
            """
            UPDATE jobs
            SET failure_hooks_started_at = ?
            WHERE job_id = ?
              AND status = 'failed'
              AND failure_hook_config_json IS NOT NULL
              AND failure_hooks_started_at IS NULL
            """,
            (time.time(), job_id),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def complete_failure_hooks(self, job_id: str) -> None:
        """Persist completion of a previously claimed failure-hook sequence."""
        await self._db.execute(
            """
            UPDATE jobs
            SET failure_hooks_completed_at = ?
            WHERE job_id = ?
              AND failure_hooks_started_at IS NOT NULL
              AND failure_hooks_completed_at IS NULL
            """,
            (time.time(), job_id),
        )
        await self._db.commit()

    async def settle_failure_hooks(self, job_id: str, results_json: str) -> None:
        """Atomically record hook results and settle their durable claim."""
        await self._db.execute(
            """
            UPDATE jobs
            SET failure_hook_results_json = ?, failure_hooks_completed_at = ?
            WHERE job_id = ?
              AND failure_hooks_started_at IS NOT NULL
              AND failure_hooks_completed_at IS NULL
            """,
            (results_json, time.time(), job_id),
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
        job_id: str = row["job_id"]
        diagnostics: list[str] = []

        def _optional_positive_float(column: str) -> float | None:
            raw: object = row[column]
            if raw is None:
                return None
            if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
                diagnostics.append(f"{column}:not_numeric")
                return None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                diagnostics.append(f"{column}:not_numeric")
                return None
            if not math.isfinite(value) or value <= 0:
                diagnostics.append(f"{column}:not_positive_finite")
                return None
            return value

        max_wall_seconds = _optional_positive_float("max_wall_seconds")
        wall_deadline_at = _optional_positive_float("wall_deadline_at")
        if max_wall_seconds is not None and wall_deadline_at is None:
            diagnostics.append("wall_deadline_at:missing_for_score_limit")

        raw_terminal_reason: object = row["terminal_reason"]
        terminal_reason: str | None
        if raw_terminal_reason is None:
            terminal_reason = None
        elif isinstance(raw_terminal_reason, str):
            terminal_reason = raw_terminal_reason
        else:
            diagnostics.append("terminal_reason:not_text")
            terminal_reason = None

        deadline_diagnostic = ";".join(dict.fromkeys(diagnostics)) or None
        if deadline_diagnostic is not None:
            _logger.warning(
                "registry.deadline_fields_malformed",
                job_id=job_id,
                diagnostic=deadline_diagnostic,
                fallback="daemon_timeout_only" if wall_deadline_at is None else "deadline",
            )
        return JobRecord(
            job_id=job_id,
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
            max_wall_seconds=max_wall_seconds,
            wall_deadline_at=wall_deadline_at,
            terminal_reason=terminal_reason,
            deadline_diagnostic=deadline_diagnostic,
            failure_hooks_started_at=row["failure_hooks_started_at"],
            failure_hooks_completed_at=row["failure_hooks_completed_at"],
            chain_depth=row["chain_depth"],
        )
