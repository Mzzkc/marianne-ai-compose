"""SQLite-based state backend for dashboard queries and execution history.

Provides a queryable state backend with full execution history tracking,
enabling dashboard views, analytics, and cross-session learning patterns.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.core.constants import SHEET_NUM_KEY
from marianne.core.logging import get_logger
from marianne.state.base import StateBackend
from marianne.utils.time import utc_now

# Module-level logger for state operations
_logger = get_logger("state.sqlite")

# Current schema version for migration support
SCHEMA_VERSION = 4


class SQLiteStateBackend(StateBackend):
    """SQLite-based state storage with execution history.

    Stores job state in a SQLite database with queryable tables for:
    - jobs: Job-level metadata and status
    - sheets: Per-sheet state including attempts and errors
    - execution_history: Detailed record of each execution attempt

    Supports schema migrations for future upgrades.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize SQLite backend.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._init_lock = asyncio.Lock()

    def _db_missing(self) -> bool:
        """True when the database file doesn't exist on disk.

        Pure reads must short-circuit on a missing file instead of
        initializing the schema (#333): read-paths that ran migrations
        left empty ``.marianne-state.db`` files in workspaces, making
        them look like they hold local state. Writers (``save``,
        ``record_execution``) still create the database on first use.
        """
        return not self.db_path.exists()

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get a database connection with foreign keys enabled."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            yield db

    async def _ensure_initialized(self) -> None:
        """Ensure database is initialized with schema."""
        if self._initialized:
            return

        async with self._init_lock:
            # Double-check after acquiring lock (another coroutine may have initialized)
            if self._initialized:
                return

            async with self._connect() as db:
                await self._run_migrations(db)
                self._initialized = True

    async def _get_schema_version(self, db: aiosqlite.Connection) -> int:
        """Get current schema version from database."""
        try:
            cursor = await db.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return row[0] if row else 0
        except aiosqlite.OperationalError:
            # Table doesn't exist yet
            return 0

    async def _run_migrations(self, db: aiosqlite.Connection) -> None:
        """Run database migrations to current schema version."""
        current_version = await self._get_schema_version(db)

        if current_version < 1:
            await self._migrate_v1(db)
            _logger.info("schema_migrated", from_version=0, to_version=1)

        if current_version < 2:
            await self._migrate_v2(db)
            _logger.info("schema_migrated", from_version=1, to_version=2)

        if current_version < 3:
            await self._migrate_v3(db)
            _logger.info("schema_migrated", from_version=2, to_version=3)

        if current_version < 4:
            await self._migrate_v4(db)
            _logger.info("schema_migrated", from_version=3, to_version=4)

    async def _migrate_v1(self, db: aiosqlite.Connection) -> None:
        """Initial schema migration (version 1)."""
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                total_sheets INTEGER NOT NULL,
                last_completed_sheet INTEGER NOT NULL DEFAULT 0,
                current_sheet INTEGER,
                config_hash TEXT,
                config_snapshot TEXT,
                pid INTEGER,
                error_message TEXT,
                total_retry_count INTEGER NOT NULL DEFAULT 0,
                rate_limit_waits INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS sheets (
                job_id TEXT NOT NULL,
                sheet_num INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                exit_code INTEGER,
                error_message TEXT,
                error_category TEXT,
                validation_passed INTEGER,
                validation_details TEXT,
                completion_attempts INTEGER NOT NULL DEFAULT 0,
                passed_validations TEXT,
                failed_validations TEXT,
                last_pass_percentage REAL,
                execution_mode TEXT,
                outcome_data TEXT,
                confidence_score REAL,
                learned_patterns TEXT,
                similar_outcomes_count INTEGER NOT NULL DEFAULT 0,
                success_without_retry INTEGER NOT NULL DEFAULT 0,
                outcome_category TEXT,
                started_at TEXT,
                completed_at TEXT,
                PRIMARY KEY (job_id, sheet_num),
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS execution_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                sheet_num INTEGER NOT NULL,
                attempt_num INTEGER NOT NULL,
                prompt TEXT,
                output TEXT,
                exit_code INTEGER,
                duration_seconds REAL,
                executed_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            )
        """)

        # Create indexes for common queries
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sheets_status ON sheets(status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_job_sheet "
            "ON execution_history(job_id, sheet_num)"
        )

        # Record migration
        await db.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (1, utc_now().isoformat()),
        )

        await db.commit()

    async def _migrate_v2(self, db: aiosqlite.Connection) -> None:
        """Schema migration v2: Add config_path column for resume support.

        Task 3: Config Storage for Resume - enables resume without config file.
        Idempotent: checks column existence before ALTER to handle interrupted migrations.
        """
        # Check if column already exists (handles interrupted migrations)
        cursor = await db.execute("PRAGMA table_info(jobs)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "config_path" not in columns:
            await db.execute(
                "ALTER TABLE jobs ADD COLUMN config_path TEXT"
            )

        # Record migration (INSERT OR IGNORE for idempotency)
        await db.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (2, utc_now().isoformat()),
        )

        await db.commit()

    async def _migrate_v3(self, db: aiosqlite.Connection) -> None:
        """Schema migration v3: Add execution_duration_seconds, exit_signal, exit_reason to sheets.

        These columns exist in the Pydantic SheetState model but were missing from
        the SQLite schema, causing ~40% data loss compared to the JSON backend.
        Idempotent: checks column existence before ALTER.
        """
        cursor = await db.execute("PRAGMA table_info(sheets)")
        columns = {row[1] for row in await cursor.fetchall()}

        if "execution_duration_seconds" not in columns:
            await db.execute(
                "ALTER TABLE sheets ADD COLUMN execution_duration_seconds REAL"
            )
        if "exit_signal" not in columns:
            await db.execute(
                "ALTER TABLE sheets ADD COLUMN exit_signal INTEGER"
            )
        if "exit_reason" not in columns:
            await db.execute(
                "ALTER TABLE sheets ADD COLUMN exit_reason TEXT"
            )

        await db.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (3, utc_now().isoformat()),
        )

        await db.commit()

    async def _migrate_v4(self, db: aiosqlite.Connection) -> None:
        """Schema migration v4: Rename first_attempt_success → success_without_retry.

        Renames the column in the sheets table to match the updated Python
        model field name. Idempotent: checks column existence before ALTER.
        """
        cursor = await db.execute("PRAGMA table_info(sheets)")
        columns = {row[1] for row in await cursor.fetchall()}

        if "first_attempt_success" in columns and "success_without_retry" not in columns:
            await db.execute(
                "ALTER TABLE sheets RENAME COLUMN first_attempt_success TO success_without_retry"
            )

        await db.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (4, utc_now().isoformat()),
        )

        await db.commit()

    def _datetime_to_str(self, dt: datetime | None) -> str | None:
        """Convert datetime to ISO format string."""
        return dt.isoformat() if dt else None

    def _str_to_datetime(self, value: str | None) -> datetime | None:
        """Convert ISO format string to datetime."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            _logger.warning(
                "corrupt_timestamp",
                value=value,
                message="Could not parse ISO timestamp — returning None",
            )
            return None

    def _json_dumps(self, obj: Any) -> str | None:
        """Serialize object to JSON string."""
        if obj is None:
            return None
        return json.dumps(obj, default=str)

    def _json_loads(self, raw: str | None) -> Any:
        """Deserialize JSON string to object."""
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            _logger.warning(
                "json_parse_failed",
                raw_length=len(raw),
                raw_preview=raw[:100],
                error=str(exc),
            )
            return None

    @staticmethod
    def _bool_to_int(value: bool | None) -> int | None:
        """Convert tri-state bool to SQLite integer (True->1, False->0, None->None)."""
        if value is None:
            return None
        return 1 if value else 0

    @staticmethod
    def _int_to_bool(value: int | None) -> bool | None:
        """Convert SQLite integer to tri-state bool (1->True, 0->False, None->None)."""
        if value is None:
            return None
        return bool(value)

    async def load(self, job_id: str) -> CheckpointState | None:
        """Load state for a job from SQLite.

        Automatically detects and recovers zombie jobs (RUNNING status but
        process dead). When a zombie is detected, the state is updated to
        PAUSED and saved before returning.
        """
        if self._db_missing():
            return None
        await self._ensure_initialized()

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row

            # Load job record
            cursor = await db.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            )
            job_row = await cursor.fetchone()

            if not job_row:
                _logger.debug("state_not_found", job_id=job_id)
                return None

            # Load sheet records
            cursor = await db.execute(
                "SELECT * FROM sheets WHERE job_id = ? ORDER BY sheet_num",
                (job_id,),
            )
            sheet_rows = await cursor.fetchall()

            # Reconstruct CheckpointState
            sheets: dict[int, SheetState] = {}
            for row in sheet_rows:
                sheet = SheetState(
                    sheet_num=row[SHEET_NUM_KEY],
                    status=SheetStatus(row["status"]),
                    started_at=self._str_to_datetime(row["started_at"]),
                    completed_at=self._str_to_datetime(row["completed_at"]),
                    attempt_count=row["attempt_count"],
                    exit_code=row["exit_code"],
                    error_message=row["error_message"],
                    error_category=row["error_category"],
                    validation_passed=self._int_to_bool(row["validation_passed"]),
                    validation_details=self._json_loads(row["validation_details"]),
                    completion_attempts=row["completion_attempts"],
                    passed_validations=self._json_loads(row["passed_validations"]) or [],
                    failed_validations=self._json_loads(row["failed_validations"]) or [],
                    last_pass_percentage=row["last_pass_percentage"],
                    execution_mode=row["execution_mode"],
                    outcome_data=self._json_loads(row["outcome_data"]),
                    confidence_score=row["confidence_score"],
                    learned_patterns=self._json_loads(row["learned_patterns"]) or [],
                    similar_outcomes_count=row["similar_outcomes_count"],
                    success_without_retry=bool(row["success_without_retry"]),
                    outcome_category=row["outcome_category"],
                    execution_duration_seconds=(
                        row["execution_duration_seconds"]
                        if "execution_duration_seconds" in row.keys()  # noqa: SIM118
                        else None
                    ),
                    exit_signal=(
                        row["exit_signal"]
                        if "exit_signal" in row.keys()  # noqa: SIM118
                        else None
                    ),
                    exit_reason=(
                        row["exit_reason"]
                        if "exit_reason" in row.keys()  # noqa: SIM118
                        else None
                    ),
                )
                sheets[sheet.sheet_num] = sheet

            # Handle config_path which may not exist in older schemas
            try:
                config_path_value = job_row["config_path"]
            except (IndexError, KeyError):
                config_path_value = None

            state = CheckpointState(
                job_id=job_row["id"],
                job_name=job_row["name"],
                config_hash=job_row["config_hash"],
                config_snapshot=self._json_loads(job_row["config_snapshot"]),
                config_path=config_path_value,
                created_at=self._str_to_datetime(job_row["created_at"])
                or utc_now(),
                updated_at=self._str_to_datetime(job_row["updated_at"])
                or utc_now(),
                started_at=self._str_to_datetime(job_row["started_at"]),
                completed_at=self._str_to_datetime(job_row["completed_at"]),
                total_sheets=job_row["total_sheets"],
                last_completed_sheet=job_row["last_completed_sheet"],
                current_sheet=job_row["current_sheet"],
                status=JobStatus(job_row["status"]),
                sheets=sheets,
                pid=job_row["pid"],
                error_message=job_row["error_message"],
                total_retry_count=job_row["total_retry_count"],
                rate_limit_waits=job_row["rate_limit_waits"],
            )

            # Check for zombie state and auto-recover
            if state.is_zombie():
                _logger.warning(
                    "zombie_auto_recovery",
                    job_id=job_id,
                    pid=state.pid,
                    status=state.status.value,
                )
                state.mark_zombie_detected(
                    reason="Detected on state load - process no longer running"
                )
                # Save the recovered state
                await self.save(state)

            _logger.debug(
                "checkpoint_loaded",
                job_id=job_id,
                status=state.status.value,
                last_completed_sheet=state.last_completed_sheet,
                total_sheets=state.total_sheets,
                sheet_count=len(sheets),
            )
            return state

    async def save(self, state: CheckpointState) -> None:
        """Save job state to SQLite."""
        await self._ensure_initialized()

        state.updated_at = utc_now()

        async with self._connect() as db:
            # Wrap all writes in an explicit transaction so that a crash
            # between the job upsert and the last sheet upsert doesn't
            # leave the database in an inconsistent state.
            await db.execute("BEGIN IMMEDIATE")
            try:
                # Upsert job record
                await db.execute(
                    """
                    INSERT INTO jobs (
                        id, name, description, status, total_sheets,
                        last_completed_sheet, current_sheet, config_hash,
                        config_snapshot, config_path,
                        pid, error_message, total_retry_count, rate_limit_waits,
                        created_at, updated_at, started_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        status = excluded.status,
                        total_sheets = excluded.total_sheets,
                        last_completed_sheet = excluded.last_completed_sheet,
                        current_sheet = excluded.current_sheet,
                        config_hash = excluded.config_hash,
                        config_snapshot = excluded.config_snapshot,
                        config_path = excluded.config_path,
                        pid = excluded.pid,
                        error_message = excluded.error_message,
                        total_retry_count = excluded.total_retry_count,
                        rate_limit_waits = excluded.rate_limit_waits,
                        updated_at = excluded.updated_at,
                        started_at = excluded.started_at,
                        completed_at = excluded.completed_at
                """,
                    (
                        state.job_id,
                        state.job_name,
                        None,  # description - not in CheckpointState currently
                        state.status.value,
                        state.total_sheets,
                        state.last_completed_sheet,
                        state.current_sheet,
                        state.config_hash,
                        self._json_dumps(state.config_snapshot),
                        state.config_path,
                        state.pid,
                        state.error_message,
                        state.total_retry_count,
                        state.rate_limit_waits,
                        self._datetime_to_str(state.created_at),
                        self._datetime_to_str(state.updated_at),
                        self._datetime_to_str(state.started_at),
                        self._datetime_to_str(state.completed_at),
                    ),
                )

                # Upsert sheet records
                for sheet in state.sheets.values():
                    await db.execute(
                        """
                        INSERT INTO sheets (
                            job_id, sheet_num, status, attempt_count, exit_code,
                            error_message, error_category, validation_passed,
                            validation_details, completion_attempts, passed_validations,
                            failed_validations, last_pass_percentage, execution_mode,
                            outcome_data, confidence_score, learned_patterns,
                            similar_outcomes_count, success_without_retry,
                            outcome_category, started_at, completed_at,
                            execution_duration_seconds, exit_signal, exit_reason
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(job_id, sheet_num) DO UPDATE SET
                            status = excluded.status,
                            attempt_count = excluded.attempt_count,
                            exit_code = excluded.exit_code,
                            error_message = excluded.error_message,
                            error_category = excluded.error_category,
                            validation_passed = excluded.validation_passed,
                            validation_details = excluded.validation_details,
                            completion_attempts = excluded.completion_attempts,
                            passed_validations = excluded.passed_validations,
                            failed_validations = excluded.failed_validations,
                            last_pass_percentage = excluded.last_pass_percentage,
                            execution_mode = excluded.execution_mode,
                            outcome_data = excluded.outcome_data,
                            confidence_score = excluded.confidence_score,
                            learned_patterns = excluded.learned_patterns,
                            similar_outcomes_count = excluded.similar_outcomes_count,
                            success_without_retry = excluded.success_without_retry,
                            outcome_category = excluded.outcome_category,
                            started_at = excluded.started_at,
                            completed_at = excluded.completed_at,
                            execution_duration_seconds = excluded.execution_duration_seconds,
                            exit_signal = excluded.exit_signal,
                            exit_reason = excluded.exit_reason
                    """,
                        (
                            state.job_id,
                            sheet.sheet_num,
                            sheet.status.value,
                            sheet.attempt_count,
                            sheet.exit_code,
                            sheet.error_message,
                            sheet.error_category,
                            self._bool_to_int(sheet.validation_passed),
                            self._json_dumps(sheet.validation_details),
                            sheet.completion_attempts,
                            self._json_dumps(sheet.passed_validations),
                            self._json_dumps(sheet.failed_validations),
                            sheet.last_pass_percentage,
                            sheet.execution_mode,
                            self._json_dumps(sheet.outcome_data),
                            sheet.confidence_score,
                            self._json_dumps(sheet.learned_patterns),
                            sheet.similar_outcomes_count,
                            1 if sheet.success_without_retry else 0,
                            sheet.outcome_category,
                            self._datetime_to_str(sheet.started_at),
                            self._datetime_to_str(sheet.completed_at),
                            sheet.execution_duration_seconds,
                            sheet.exit_signal,
                            sheet.exit_reason,
                        ),
                    )

                await db.commit()
            except Exception:
                await db.rollback()
                raise

        _logger.info(
            "checkpoint_saved",
            job_id=state.job_id,
            status=state.status.value,
            last_completed_sheet=state.last_completed_sheet,
            total_sheets=state.total_sheets,
            sheet_count=len(state.sheets),
        )

    async def delete(self, job_id: str) -> bool:
        """Delete state for a job."""
        if self._db_missing():
            return False
        await self._ensure_initialized()

        async with self._connect() as db:
            # Check if job exists
            cursor = await db.execute(
                "SELECT id FROM jobs WHERE id = ?", (job_id,)
            )
            if not await cursor.fetchone():
                return False

            # Delete cascades to sheets and execution_history
            await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            await db.commit()
            return True

    async def list_jobs(self) -> list[CheckpointState]:
        """List all jobs with state."""
        if self._db_missing():
            return []
        await self._ensure_initialized()

        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id FROM jobs ORDER BY updated_at DESC"
            )
            rows = await cursor.fetchall()

        # Load full state for each job
        states = []
        for row in rows:
            state = await self.load(row[0])
            if state:
                states.append(state)

        return states

    async def get_next_sheet(self, job_id: str) -> int | None:
        """Get the next sheet to process for a job."""
        state = await self.load(job_id)
        if state is None:
            return 1  # Start from beginning if no state
        return state.get_next_sheet()

    async def mark_sheet_status(
        self,
        job_id: str,
        sheet_num: int,
        status: SheetStatus,
        error_message: str | None = None,
    ) -> None:
        """Update status of a specific sheet."""
        state = await self.load(job_id)
        if state is None:
            raise ValueError(f"No state found for job {job_id}")

        if status == SheetStatus.COMPLETED:
            state.mark_sheet_completed(sheet_num)
        elif status == SheetStatus.FAILED:
            state.mark_sheet_failed(sheet_num, error_message or "Unknown error")
        elif status == SheetStatus.IN_PROGRESS:
            state.mark_sheet_started(sheet_num)

        await self.save(state)

    # Extended methods for dashboard and analytics

    @property
    def supports_execution_history(self) -> bool:  # noqa: D102
        return True

    async def record_execution(
        self,
        job_id: str,
        sheet_num: int,
        attempt_num: int,
        prompt: str | None = None,
        output: str | None = None,
        exit_code: int | None = None,
        duration_seconds: float | None = None,
    ) -> int | None:
        """Record an execution attempt in history.

        Args:
            job_id: Job identifier
            sheet_num: Sheet number
            attempt_num: Attempt number within the sheet
            prompt: The prompt sent to Claude
            output: The output received
            exit_code: Exit code from the execution
            duration_seconds: Execution duration

        Returns:
            The ID of the inserted record, or None if insertion failed.
        """
        await self._ensure_initialized()

        async with self._connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO execution_history (
                    job_id, sheet_num, attempt_num, prompt, output,
                    exit_code, duration_seconds, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    job_id,
                    sheet_num,
                    attempt_num,
                    prompt,
                    output,
                    exit_code,
                    duration_seconds,
                    utc_now().isoformat(),
                ),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_execution_history(
        self,
        job_id: str,
        sheet_num: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get execution history for a job.

        Args:
            job_id: Job identifier
            sheet_num: Optional sheet number filter
            limit: Maximum records to return

        Returns:
            List of execution history records
        """
        if self._db_missing():
            return []
        await self._ensure_initialized()

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row

            if sheet_num is not None:
                cursor = await db.execute(
                    """
                    SELECT * FROM execution_history
                    WHERE job_id = ? AND sheet_num = ?
                    ORDER BY executed_at DESC
                    LIMIT ?
                """,
                    (job_id, sheet_num, limit),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT * FROM execution_history
                    WHERE job_id = ?
                    ORDER BY executed_at DESC
                    LIMIT ?
                """,
                    (job_id, limit),
                )

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_execution_history_count(self, job_id: str) -> int:
        """Get total count of execution history records for a job.

        Args:
            job_id: Job identifier

        Returns:
            Total number of execution history records
        """
        if self._db_missing():
            return 0
        await self._ensure_initialized()

        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM execution_history WHERE job_id = ?",
                (job_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_job_statistics(self, job_id: str) -> dict[str, Any]:
        """Get aggregate statistics for a job.

        Args:
            job_id: Job identifier

        Returns:
            Dictionary with statistics including:
            - total_executions: Total execution attempts
            - success_rate: Percentage of successful sheets
            - avg_duration: Average execution duration
            - total_retries: Total retry count
        """
        if self._db_missing():
            return {}
        await self._ensure_initialized()

        async with self._connect() as db:
            # Get job data
            cursor = await db.execute(
                "SELECT total_sheets, last_completed_sheet, total_retry_count "
                "FROM jobs WHERE id = ?",
                (job_id,),
            )
            job_row = await cursor.fetchone()

            if not job_row:
                return {}

            # Get execution stats
            cursor = await db.execute(
                """
                SELECT
                    COUNT(*) as total_executions,
                    AVG(duration_seconds) as avg_duration,
                    SUM(CASE WHEN exit_code = 0 THEN 1 ELSE 0 END) as successful
                FROM execution_history
                WHERE job_id = ?
            """,
                (job_id,),
            )
            exec_row = await cursor.fetchone()

            total_sheets = job_row[0]
            completed = job_row[1]
            total_retries = job_row[2]

            return {
                "total_sheets": total_sheets,
                "completed_sheets": completed,
                "success_rate": (completed / total_sheets * 100)
                if total_sheets > 0
                else 0.0,
                "total_retries": total_retries,
                "total_executions": exec_row[0] if exec_row else 0,
                "avg_duration_seconds": exec_row[1] if exec_row else None,
                "successful_executions": exec_row[2] if exec_row else 0,
            }

    async def query_jobs(
        self,
        status: JobStatus | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query jobs with filters for dashboard.

        Args:
            status: Optional status filter
            since: Only return jobs updated since this time
            limit: Maximum results

        Returns:
            List of job summary dictionaries
        """
        if self._db_missing():
            return []
        await self._ensure_initialized()

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row

            conditions = []
            params: list[Any] = []

            if status:
                conditions.append("status = ?")
                params.append(status.value)

            if since:
                conditions.append("updated_at >= ?")
                params.append(since.isoformat())

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            params.append(limit)

            cursor = await db.execute(
                f"""
                SELECT id, name, status, total_sheets, last_completed_sheet,
                       created_at, updated_at, completed_at, error_message
                FROM jobs
                WHERE {where_clause}
                ORDER BY updated_at DESC
                LIMIT ?
            """,
                params,
            )

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
