"""Durable recurring-score registrations and exact due-time claims.

The registry is a small projection in the conductor state database. It never
executes score sources or calculates recurrence; callers supply validated
``ScheduleConfig`` instances and their calculated next epoch due time.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
from pydantic import ValidationError

from marianne.core.config import ScheduleConfig


class ScheduleRegistryError(RuntimeError):
    """Base error for a diagnosable schedule-registry failure."""


class ScheduleRegistryBusyError(ScheduleRegistryError):
    """The bounded SQLite wait expired while another writer held the database."""


class ScheduleRegistryDataError(ScheduleRegistryError):
    """A persisted schedule row cannot safely be interpreted as registry state."""


class ScheduleRegistryClaimError(ScheduleRegistryError):
    """A lifecycle update does not match a durable claimed due identity."""


@dataclass(frozen=True)
class ScheduleRecord:
    """Persistent schedule projection visible to the recurrence controller."""

    schedule_id: str
    score_name: str
    score_path: Path
    schedule_json: str
    source_digest: str
    enabled: bool
    next_due_at: float
    created_at: float
    updated_at: float
    last_due_at: float | None
    last_run_id: str | None
    last_outcome: str | None
    consecutive_drops: int


@dataclass(frozen=True)
class DueClaim:
    """The durable identity of a schedule tick claimed by this conductor."""

    schedule_id: str
    due_at: float


class ScheduleRegistry:
    """Async SQLite schedule registry with atomic, durable due-time claims.

    ``last_claimed_due_at`` is intentionally private schema state: it is a
    durable idempotency key, not schedule status exposed to users. A claim is
    never released, so reopening the conductor cannot submit the same due time
    twice.
    """

    def __init__(self, db_path: Path, *, busy_timeout_seconds: float = 0.25) -> None:
        if not math.isfinite(busy_timeout_seconds) or busy_timeout_seconds <= 0:
            raise ValueError("busy_timeout_seconds must be a positive finite duration")
        self._db_path = db_path
        self._busy_timeout_seconds = busy_timeout_seconds
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Open the state database and add the schedules table if necessary."""
        if self._conn is not None:
            return

        await asyncio.to_thread(self._db_path.parent.mkdir, parents=True, exist_ok=True)
        conn = await aiosqlite.connect(
            str(self._db_path), timeout=self._busy_timeout_seconds
        )
        try:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute(
                f"PRAGMA busy_timeout={int(self._busy_timeout_seconds * 1000)}"
            )
            await self._create_tables(conn)
        except sqlite3.Error as exc:
            await conn.close()
            self._raise_database_error("open", exc)
        except Exception:
            await conn.close()
            raise
        self._conn = conn

    @property
    def _db(self) -> aiosqlite.Connection:
        """Return the open connection or fail with an actionable lifecycle error."""
        if self._conn is None:
            raise ScheduleRegistryError("ScheduleRegistry not opened — call open() first")
        return self._conn

    @staticmethod
    async def _create_tables(conn: aiosqlite.Connection) -> None:
        """Create additive schedule storage alongside existing conductor tables."""
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                schedule_id TEXT PRIMARY KEY,
                score_name TEXT NOT NULL,
                score_path TEXT NOT NULL,
                schedule_json TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                next_due_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_due_at REAL,
                last_run_id TEXT,
                last_outcome TEXT,
                consecutive_drops INTEGER NOT NULL DEFAULT 0
                    CHECK (consecutive_drops >= 0),
                last_claimed_due_at REAL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_schedules_due "
            "ON schedules (enabled, next_due_at)"
        )
        await conn.commit()

    async def upsert(
        self,
        schedule_id: str,
        score_name: str,
        score_path: Path,
        schedule: ScheduleConfig,
        source_digest: str,
        next_due_at: float,
    ) -> None:
        """Insert or replace a schedule declaration without losing its creation time."""
        _require_epoch(next_due_at, name="next_due_at")
        now = time.time()
        await self._execute_mutation(
            "upsert",
            """
            INSERT INTO schedules (
                schedule_id, score_name, score_path, schedule_json,
                source_digest, enabled, next_due_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schedule_id) DO UPDATE SET
                score_name = excluded.score_name,
                score_path = excluded.score_path,
                schedule_json = excluded.schedule_json,
                source_digest = excluded.source_digest,
                enabled = excluded.enabled,
                next_due_at = excluded.next_due_at,
                updated_at = excluded.updated_at
            """,
            (
                schedule_id,
                score_name,
                str(score_path),
                schedule.model_dump_json(),
                source_digest,
                int(schedule.enabled),
                next_due_at,
                now,
                now,
            ),
            schedule_id=schedule_id,
        )

    async def get(self, schedule_id: str) -> ScheduleRecord | None:
        """Return one durable schedule record, or ``None`` when it is absent."""
        try:
            cursor = await self._db.execute(
                "SELECT * FROM schedules WHERE schedule_id = ?", (schedule_id,)
            )
            row = await cursor.fetchone()
        except sqlite3.Error as exc:
            self._raise_database_error("get", exc, schedule_id=schedule_id)
        if row is None:
            return None
        return self._row_to_record(row)

    async def list(self) -> list[ScheduleRecord]:
        """List schedule projections in stable schedule-ID order."""
        try:
            cursor = await self._db.execute("SELECT * FROM schedules ORDER BY schedule_id")
            rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            self._raise_database_error("list", exc)
        return [self._row_to_record(row) for row in rows]

    async def pause(self, schedule_id: str) -> None:
        """Disable future claims for a schedule without deleting its history."""
        await self._set_enabled(schedule_id, enabled=False)

    async def resume(self, schedule_id: str) -> None:
        """Enable future claims for a schedule without recalculating recurrence."""
        await self._set_enabled(schedule_id, enabled=True)

    async def _set_enabled(self, schedule_id: str, *, enabled: bool) -> None:
        await self._execute_mutation(
            "set enabled state",
            "UPDATE schedules SET enabled = ?, updated_at = ? WHERE schedule_id = ?",
            (int(enabled), time.time(), schedule_id),
            schedule_id=schedule_id,
        )

    async def remove(self, schedule_id: str) -> None:
        """Remove a registration and all of its durable lease state."""
        await self._execute_mutation(
            "remove",
            "DELETE FROM schedules WHERE schedule_id = ?",
            (schedule_id,),
            schedule_id=schedule_id,
        )

    async def claim_due(
        self,
        schedule_id: str,
        due_at: float,
        *,
        now: float | None = None,
    ) -> DueClaim | None:
        """Atomically claim one exact due identity, if it remains due and enabled."""
        _require_epoch(due_at, name="due_at")
        current_time = time.time() if now is None else now
        _require_epoch(current_time, name="now")
        cursor = await self._execute_mutation(
            "claim due",
            """
            UPDATE schedules
            SET last_claimed_due_at = ?, updated_at = ?
            WHERE schedule_id = ?
              AND enabled = 1
              AND next_due_at = ?
              AND ? <= ?
              AND (last_claimed_due_at IS NULL OR last_claimed_due_at != ?)
            """,
            (due_at, time.time(), schedule_id, due_at, due_at, current_time, due_at),
            schedule_id=schedule_id,
            immediate=True,
        )
        if cursor.rowcount != 1:
            return None
        return DueClaim(schedule_id=schedule_id, due_at=due_at)

    async def record_submission(self, schedule_id: str, due_at: float, run_id: str) -> None:
        """Persist a claimed tick's child identity without advancing recurrence."""
        _require_epoch(due_at, name="due_at")
        cursor = await self._execute_mutation(
            "record submission",
            """
            UPDATE schedules
            SET last_due_at = ?, last_run_id = ?, updated_at = ?
            WHERE schedule_id = ? AND last_claimed_due_at = ?
            """,
            (due_at, run_id, time.time(), schedule_id, due_at),
            schedule_id=schedule_id,
        )
        self._require_claimed_row(cursor.rowcount, schedule_id, due_at)

    async def record_tick_outcome(
        self,
        schedule_id: str,
        due_at: float,
        outcome: str,
        *,
        next_due_at: float,
        dropped: bool,
    ) -> None:
        """Persist a claimed tick outcome and caller-calculated next due time."""
        _require_epoch(due_at, name="due_at")
        _require_epoch(next_due_at, name="next_due_at")
        if next_due_at <= due_at:
            raise ValueError("next_due_at must be later than due_at")
        cursor = await self._execute_mutation(
            "record tick outcome",
            """
            UPDATE schedules
            SET last_due_at = ?,
                last_outcome = ?,
                next_due_at = ?,
                consecutive_drops = CASE
                    WHEN ? THEN consecutive_drops + 1
                    ELSE 0
                END,
                updated_at = ?
            WHERE schedule_id = ? AND last_claimed_due_at = ?
            """,
            (
                due_at,
                outcome,
                next_due_at,
                int(dropped),
                time.time(),
                schedule_id,
                due_at,
            ),
            schedule_id=schedule_id,
        )
        self._require_claimed_row(cursor.rowcount, schedule_id, due_at)

    async def close(self) -> None:
        """Close the async SQLite connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> ScheduleRegistry:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _execute_mutation(
        self,
        operation: str,
        statement: str,
        parameters: tuple[object, ...],
        *,
        schedule_id: str | None = None,
        immediate: bool = False,
    ) -> aiosqlite.Cursor:
        """Execute one mutation, rolling it back before translating every failure."""
        try:
            if immediate:
                await self._db.execute("BEGIN IMMEDIATE")
            cursor = await self._db.execute(statement, parameters)
            await self._db.commit()
        except sqlite3.Error as exc:
            await self._rollback_after_failed_mutation()
            self._raise_database_error(operation, exc, schedule_id=schedule_id)
        return cursor

    async def _rollback_after_failed_mutation(self) -> None:
        """End a partial transaction before reporting its original database error."""
        try:
            await self._db.rollback()
        except sqlite3.Error:
            # The original failure is more actionable when BEGIN IMMEDIATE
            # itself could not acquire a writer lock.
            pass

    @staticmethod
    def _require_claimed_row(rowcount: int, schedule_id: str, due_at: float) -> None:
        """Reject lifecycle writes without a current durable due-time claim."""
        if rowcount != 1:
            raise ScheduleRegistryClaimError(
                f"Schedule {schedule_id!r} does not hold claimed due time {due_at}"
            )

    @staticmethod
    def _row_to_record(row: aiosqlite.Row) -> ScheduleRecord:
        schedule_id = str(row["schedule_id"])
        schedule_json = str(row["schedule_json"])
        _validate_schedule_json(schedule_json, schedule_id)
        return ScheduleRecord(
            schedule_id=schedule_id,
            score_name=str(row["score_name"]),
            score_path=Path(str(row["score_path"])),
            schedule_json=schedule_json,
            source_digest=str(row["source_digest"]),
            enabled=bool(row["enabled"]),
            next_due_at=float(row["next_due_at"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            last_due_at=(
                float(row["last_due_at"]) if row["last_due_at"] is not None else None
            ),
            last_run_id=(str(row["last_run_id"]) if row["last_run_id"] is not None else None),
            last_outcome=(
                str(row["last_outcome"]) if row["last_outcome"] is not None else None
            ),
            consecutive_drops=int(row["consecutive_drops"]),
        )

    @staticmethod
    def _raise_database_error(
        operation: str,
        exc: sqlite3.Error,
        *,
        schedule_id: str | None = None,
    ) -> None:
        context = f" for schedule {schedule_id!r}" if schedule_id is not None else ""
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise ScheduleRegistryBusyError(
                f"Schedule registry busy during {operation}{context}"
            ) from exc
        raise ScheduleRegistryError(
            f"Schedule registry {operation} failed{context}: {exc}"
        ) from exc


def _require_epoch(value: float, *, name: str) -> None:
    """Reject non-finite timestamps before SQLite can apply surprising comparisons."""
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite epoch timestamp")


def _validate_schedule_json(schedule_json: str, schedule_id: str) -> None:
    """Validate persisted configuration without exposing its source content."""
    try:
        ScheduleConfig.model_validate_json(schedule_json)
    except (ValidationError, ValueError) as exc:
        raise ScheduleRegistryDataError(
            f"Invalid schedule configuration for schedule {schedule_id!r}"
        ) from exc
