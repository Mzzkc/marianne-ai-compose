"""Base class for GlobalLearningStore with connection and schema management.

This module provides the foundational `GlobalLearningStoreBase` class that handles:
- SQLite database connection management with WAL mode
- Schema creation and migration
- Static hashing utilities for workspace and job identification

Extracted from global_store.py as part of the modularization effort.
Mixins inherit from this base to add domain-specific functionality.
"""

import contextvars
import hashlib
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from marianne.core.logging import get_logger

# Module-level logger for global learning store
_logger = get_logger("learning.global_store")

# Default location following XDG conventions
DEFAULT_GLOBAL_STORE_PATH = Path.home() / ".marianne" / "global-learning.db"

# Type alias for SQLite query parameters — matches sqlite3.execute() signature.
# SQLite accepts str, int, float, bytes, and None as bind parameters.
SQLParam = str | int | float | bytes | None


class WhereBuilder:
    """Accumulates SQL WHERE clauses and their bound parameters.

    Provides a consistent pattern for building dynamic SQL queries across
    learning store mixins. Clauses are joined with AND.

    Usage::

        wb = WhereBuilder()
        wb.add("status = ?", status)
        wb.add("score >= ?", min_score)
        where_sql, params = wb.build()
        conn.execute(f"SELECT * FROM t WHERE {where_sql}", params)
    """

    __slots__ = ("_clauses", "_params")

    def __init__(self) -> None:
        self._clauses: list[str] = []
        self._params: list[SQLParam] = []

    def add(self, clause: str, *params: SQLParam) -> None:
        """Append a WHERE clause with its bound parameters."""
        self._clauses.append(clause)
        self._params.extend(params)

    def build(self) -> tuple[str, tuple[SQLParam, ...]]:
        """Return the combined WHERE fragment and parameter tuple.

        Returns ``("1=1", ())`` when no clauses have been added.
        """
        if not self._clauses:
            return "1=1", ()
        return " AND ".join(self._clauses), tuple(self._params)


class GlobalLearningStoreBase:
    """SQLite-based global learning store base class.

    Provides persistent storage infrastructure for execution outcomes, detected patterns,
    and error recovery data across all Marianne workspaces. Uses WAL mode for safe
    concurrent access.

    This base class handles:
    - Database connection lifecycle
    - Schema version management
    - Migration and schema creation
    - Hashing utilities

    Subclasses (via mixins) add domain-specific methods for patterns, executions,
    rate limits, drift detection, escalation, and budget management.

    Attributes:
        db_path: Path to the SQLite database file.
        _logger: Module logger instance for consistent logging.
    """

    # Schema version - increment when schema changes
    # v9: Added exploration_budget and entropy_responses tables for v23 evolutions
    # v10: Added proper column migration for existing tables
    # v11: Renamed outcome_improved → pattern_led_to_success in pattern_applications
    # v12: Renamed first_attempt_success → success_without_retry in executions
    # v13: Repair unapplied pattern priorities crushed by frequency factor
    # v14: Added active (soft delete), content_hash (dedup), instrument_name (scoping)
    # v15: Recreate pattern_applications without FK constraints (#129)
    SCHEMA_VERSION = 15

    # Expected columns for tables that may need migration
    # Format: {table_name: [(column_name, column_definition), ...]}
    # Only include columns added after initial table creation
    _COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
        "patterns": [
            # v19 additions
            ("quarantine_status", "TEXT DEFAULT 'pending'"),
            ("provenance_job_hash", "TEXT"),
            ("provenance_sheet_num", "INTEGER"),
            ("quarantined_at", "TIMESTAMP"),
            ("validated_at", "TIMESTAMP"),
            ("quarantine_reason", "TEXT"),
            ("trust_score", "REAL DEFAULT 0.5"),
            ("trust_calculation_date", "TIMESTAMP"),
            # v22 additions
            ("success_factors", "TEXT"),
            ("success_factors_updated_at", "TIMESTAMP"),
            # v14 (cycle 2) additions: soft delete, content dedup, instrument scoping
            ("active", "INTEGER DEFAULT 1"),
            ("content_hash", "TEXT"),
            ("instrument_name", "TEXT"),
        ],
        "pattern_applications": [
            # v12 addition
            ("grounding_confidence", "REAL"),
        ],
    }

    # Column renames: {table: [(old_name, new_name), ...]}
    # Applied during migration for databases with older schema
    _COLUMN_RENAMES: dict[str, list[tuple[str, str]]] = {
        "pattern_applications": [
            # v11: Clarify semantics — not "did outcome improve vs baseline"
            # but "did the pattern lead to execution success"
            ("outcome_improved", "pattern_led_to_success"),
        ],
        "executions": [
            # v12: Clarify semantics — succeeded without needing any retry,
            # not necessarily "first attempt"
            ("first_attempt_success", "success_without_retry"),
        ],
    }

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize the global learning store.

        Creates the database directory if needed, establishes the connection,
        and runs any necessary migrations.

        Args:
            db_path: Path to the SQLite database file.
                    Defaults to ~/.marianne/global-learning.db
        """
        self.db_path = db_path or DEFAULT_GLOBAL_STORE_PATH
        self._logger = _logger
        # ContextVar scopes the batch connection per-asyncio-task, preventing
        # a race where Task A's batch_connection() leaks into Task B's
        # _get_connection() calls.  Each task sees its own (or no) batch conn.
        self._batch_conn: contextvars.ContextVar[sqlite3.Connection | None] = (
            contextvars.ContextVar("_batch_conn", default=None)
        )
        self._ensure_db_exists()
        self._migrate_if_needed()

    def _ensure_db_exists(self) -> None:
        """Ensure the database directory and file exist.

        Creates parent directories with parents=True to handle nested paths.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with proper configuration.

        If called inside a ``batch_connection()`` context, reuses the cached
        connection (no commit/close per call — the batch handles that).
        Otherwise creates a fresh connection per call.

        Uses WAL mode for better concurrent access and enables foreign keys.

        Yields:
            A configured sqlite3.Connection instance.

        Raises:
            sqlite3.Error: If connection or configuration fails.
        """
        # Reuse batch connection if available (scoped per asyncio task)
        batch = self._batch_conn.get()
        if batch is not None:
            yield batch
            return

        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            _logger.warning(
                f"Database operation failed on {self.db_path}: {type(e).__name__}: {e}"
            )
            raise
        finally:
            conn.close()

    @contextmanager
    def batch_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Reuse a single connection across multiple operations.

        While this context manager is active, all ``_get_connection()`` calls
        will reuse the same connection, avoiding repeated open/close overhead.
        The connection is committed once on successful exit or rolled back on error.

        Example::

            with store.batch_connection():
                patterns = store.get_patterns(min_priority=0.01)
                for p in patterns:
                    store.update_trust_score(p.pattern_id, ...)

        Yields:
            The shared sqlite3.Connection instance.
        """
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        token = self._batch_conn.set(conn)
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            _logger.warning(
                f"Batch operation failed on {self.db_path}: {type(e).__name__}: {e}"
            )
            raise
        finally:
            self._batch_conn.reset(token)
            conn.close()

    def close(self) -> None:  # noqa: B027 — intentional concrete no-op default
        """Close any persistent resources.

        No-op: connections are managed per-operation via _get_connection().
        This method exists for API compatibility so callers can unconditionally
        call ``store.close()`` without checking the backend type.
        """

    def _migrate_if_needed(self) -> None:
        """Create or migrate the database schema.

        Checks the current schema version and runs migration if needed.
        Migration is idempotent - running it multiple times is safe.
        """
        with self._get_connection() as conn:
            # Check current version
            try:
                cursor = conn.execute(
                    "SELECT version FROM schema_version LIMIT 1"
                )
                row = cursor.fetchone()
                current_version = row["version"] if row else 0
            except sqlite3.OperationalError:
                current_version = 0

            if current_version < self.SCHEMA_VERSION:
                # First migrate columns (for existing tables)
                self._migrate_columns(conn)
                # Data migrations for specific version transitions
                if current_version < 13:
                    self._repair_unapplied_pattern_priorities(conn)
                if current_version < 15:
                    self._drop_pattern_applications_fk_constraints(conn)
                # Then create/update schema (creates new tables and indexes)
                self._create_schema(conn)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Create the database schema.

        Creates all tables and indexes needed by the global learning store.
        Uses IF NOT EXISTS for idempotent operation. Each table is created
        via a dedicated helper for maintainability.

        Args:
            conn: Active database connection.
        """
        self._create_schema_version_table(conn)
        self._create_executions_table(conn)
        self._create_patterns_table(conn)
        self._create_pattern_applications_table(conn)
        self._create_error_recoveries_table(conn)
        self._create_workspace_clusters_table(conn)
        self._create_rate_limit_events_table(conn)
        self._create_escalation_decisions_table(conn)
        self._create_pattern_discovery_events_table(conn)
        self._create_evolution_trajectory_table(conn)
        self._create_exploration_budget_table(conn)
        self._create_entropy_responses_table(conn)
        self._create_pattern_entropy_history_table(conn)

        # Update schema version
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (self.SCHEMA_VERSION,),
        )

        self._logger.info("schema_created", version=self.SCHEMA_VERSION)

    @staticmethod
    def _create_schema_version_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        """)

    @staticmethod
    def _create_executions_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY,
                workspace_hash TEXT NOT NULL,
                job_hash TEXT NOT NULL,
                sheet_num INTEGER NOT NULL,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                duration_seconds REAL,
                status TEXT,
                retry_count INTEGER DEFAULT 0,
                success_without_retry BOOLEAN,
                validation_pass_rate REAL,
                confidence_score REAL,
                model TEXT,
                error_codes TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exec_workspace_job "
            "ON executions(workspace_hash, job_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exec_status ON executions(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exec_started ON executions(started_at)"
        )

    @staticmethod
    def _create_patterns_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS patterns (
                id TEXT PRIMARY KEY,
                pattern_type TEXT NOT NULL,
                pattern_name TEXT NOT NULL,
                description TEXT,
                occurrence_count INTEGER DEFAULT 1,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_confirmed TIMESTAMP,
                led_to_success_count INTEGER DEFAULT 0,
                led_to_failure_count INTEGER DEFAULT 0,
                effectiveness_score REAL DEFAULT 0.5,
                variance REAL DEFAULT 0.0,
                suggested_action TEXT,
                context_tags TEXT,
                priority_score REAL DEFAULT 0.5,
                quarantine_status TEXT DEFAULT 'pending',
                provenance_job_hash TEXT,
                provenance_sheet_num INTEGER,
                quarantined_at TIMESTAMP,
                validated_at TIMESTAMP,
                quarantine_reason TEXT,
                trust_score REAL DEFAULT 0.5,
                trust_calculation_date TIMESTAMP,
                success_factors TEXT,
                success_factors_updated_at TIMESTAMP,
                active INTEGER DEFAULT 1,
                content_hash TEXT,
                instrument_name TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_patterns_type ON patterns(pattern_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_patterns_priority "
            "ON patterns(priority_score DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_patterns_confirmed "
            "ON patterns(last_confirmed)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_patterns_quarantine "
            "ON patterns(quarantine_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_patterns_trust "
            "ON patterns(trust_score)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_patterns_content_hash "
            "ON patterns(content_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_patterns_instrument "
            "ON patterns(instrument_name)"
        )

    @staticmethod
    def _create_pattern_applications_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_applications (
                id TEXT PRIMARY KEY,
                pattern_id TEXT,
                execution_id TEXT,
                applied_at TIMESTAMP,
                pattern_led_to_success BOOLEAN,
                retry_count_before INTEGER,
                retry_count_after INTEGER,
                grounding_confidence REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_app_pattern "
            "ON pattern_applications(pattern_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_app_execution "
            "ON pattern_applications(execution_id)"
        )

    @staticmethod
    def _create_error_recoveries_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS error_recoveries (
                id TEXT PRIMARY KEY,
                error_code TEXT NOT NULL,
                suggested_wait REAL,
                actual_wait REAL,
                recovery_success BOOLEAN,
                recorded_at TIMESTAMP,
                model TEXT,
                time_of_day INTEGER
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_code "
            "ON error_recoveries(error_code)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_success "
            "ON error_recoveries(recovery_success)"
        )

    @staticmethod
    def _create_workspace_clusters_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspace_clusters (
                workspace_hash TEXT PRIMARY KEY,
                cluster_id TEXT,
                assigned_at TIMESTAMP
            )
        """)

    @staticmethod
    def _create_rate_limit_events_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_events (
                id TEXT PRIMARY KEY,
                error_code TEXT NOT NULL,
                model TEXT,
                recorded_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                source_job_hash TEXT NOT NULL,
                duration_seconds REAL NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_code "
            "ON rate_limit_events(error_code)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_expires "
            "ON rate_limit_events(expires_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_model "
            "ON rate_limit_events(model)"
        )

    @staticmethod
    def _create_escalation_decisions_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS escalation_decisions (
                id TEXT PRIMARY KEY,
                job_hash TEXT NOT NULL,
                sheet_num INTEGER NOT NULL,
                confidence REAL NOT NULL,
                action TEXT NOT NULL,
                guidance TEXT,
                validation_pass_rate REAL NOT NULL,
                retry_count INTEGER NOT NULL,
                outcome_after_action TEXT,
                recorded_at TIMESTAMP NOT NULL,
                model TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_escalation_job "
            "ON escalation_decisions(job_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_escalation_action "
            "ON escalation_decisions(action)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_escalation_confidence "
            "ON escalation_decisions(confidence)"
        )

    @staticmethod
    def _create_pattern_discovery_events_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_discovery_events (
                id TEXT PRIMARY KEY,
                pattern_id TEXT NOT NULL,
                pattern_name TEXT NOT NULL,
                pattern_type TEXT NOT NULL,
                source_job_hash TEXT NOT NULL,
                recorded_at TIMESTAMP NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                effectiveness_score REAL NOT NULL,
                context_tags TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pattern_discovery_expires "
            "ON pattern_discovery_events(expires_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pattern_discovery_source "
            "ON pattern_discovery_events(source_job_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pattern_discovery_type "
            "ON pattern_discovery_events(pattern_type)"
        )

    @staticmethod
    def _create_evolution_trajectory_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evolution_trajectory (
                id TEXT PRIMARY KEY,
                cycle INTEGER NOT NULL UNIQUE,
                recorded_at TIMESTAMP NOT NULL,
                evolutions_completed INTEGER NOT NULL,
                evolutions_deferred INTEGER NOT NULL,
                issue_classes TEXT NOT NULL,
                cv_avg REAL NOT NULL,
                implementation_loc INTEGER NOT NULL,
                test_loc INTEGER NOT NULL,
                loc_accuracy REAL NOT NULL,
                research_candidates_resolved INTEGER DEFAULT 0,
                research_candidates_created INTEGER DEFAULT 0,
                notes TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trajectory_cycle "
            "ON evolution_trajectory(cycle)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trajectory_recorded "
            "ON evolution_trajectory(recorded_at)"
        )

    @staticmethod
    def _create_exploration_budget_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS exploration_budget (
                id TEXT PRIMARY KEY,
                job_hash TEXT NOT NULL,
                recorded_at TIMESTAMP NOT NULL,
                budget_value REAL NOT NULL,
                entropy_at_time REAL,
                adjustment_type TEXT NOT NULL,
                adjustment_reason TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_budget_job "
            "ON exploration_budget(job_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_budget_recorded "
            "ON exploration_budget(recorded_at)"
        )

    @staticmethod
    def _create_entropy_responses_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entropy_responses (
                id TEXT PRIMARY KEY,
                job_hash TEXT NOT NULL,
                recorded_at TIMESTAMP NOT NULL,
                entropy_at_trigger REAL NOT NULL,
                threshold_used REAL NOT NULL,
                actions_taken TEXT NOT NULL,
                budget_boosted INTEGER DEFAULT 0,
                quarantine_revisits INTEGER DEFAULT 0,
                patterns_revisited TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entropy_response_job "
            "ON entropy_responses(job_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entropy_response_recorded "
            "ON entropy_responses(recorded_at)"
        )

    @staticmethod
    def _create_pattern_entropy_history_table(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_entropy_history (
                id TEXT PRIMARY KEY,
                calculated_at TIMESTAMP NOT NULL,
                shannon_entropy REAL NOT NULL,
                max_possible_entropy REAL NOT NULL,
                diversity_index REAL NOT NULL,
                unique_pattern_count INTEGER NOT NULL,
                effective_pattern_count INTEGER NOT NULL,
                total_applications INTEGER NOT NULL,
                dominant_pattern_share REAL NOT NULL,
                threshold_exceeded INTEGER DEFAULT 0
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entropy_history_calculated "
            "ON pattern_entropy_history(calculated_at DESC)"
        )

    @staticmethod
    def _get_existing_columns(
        conn: sqlite3.Connection, table_name: str,
    ) -> set[str] | None:
        """Get the column names for a table, or None if the table does not exist.

        Args:
            conn: Active database connection.
            table_name: Name of the table to inspect.

        Returns:
            Set of column names, or None if the table has not been created yet.
        """
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if not cursor.fetchone():
            return None
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        return {row["name"] for row in cursor.fetchall()}

    def _migrate_columns(self, conn: sqlite3.Connection) -> None:
        """Add missing columns and rename outdated columns in existing tables.

        This handles the case where a database was created with an older schema
        and needs new columns added. Uses ALTER TABLE which is idempotent-safe
        (we check for column existence before adding).

        Only migrates tables that already exist -- new tables are handled by
        _create_schema which runs after this method.

        Args:
            conn: Active database connection.
        """
        # Phase 1: Add missing columns
        for table_name, columns in self._COLUMN_MIGRATIONS.items():
            existing = self._get_existing_columns(conn, table_name)
            if existing is None:
                continue

            for column_name, column_def in columns:
                if column_name not in existing:
                    try:
                        conn.execute(
                            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"
                        )
                        self._logger.info(
                            f"Added column {column_name} to {table_name}"
                        )
                    except sqlite3.OperationalError as e:
                        if "duplicate column name" not in str(e).lower():
                            raise

        # Phase 2: Column renames (SQLite 3.25.0+)
        for table_name, renames in self._COLUMN_RENAMES.items():
            existing = self._get_existing_columns(conn, table_name)
            if existing is None:
                continue

            for old_name, new_name in renames:
                if old_name in existing and new_name not in existing:
                    try:
                        conn.execute(
                            f"ALTER TABLE {table_name} RENAME COLUMN {old_name} TO {new_name}"
                        )
                        self._logger.info(
                            f"Renamed column {old_name} → {new_name} in {table_name}"
                        )
                    except sqlite3.OperationalError as e:
                        err_msg = str(e).lower()
                        if "near" in err_msg or "syntax error" in err_msg:
                            # SQLite < 3.25.0: RENAME COLUMN not supported.
                            self._logger.warning(
                                f"Cannot rename {old_name} → {new_name} in {table_name} "
                                f"(SQLite too old). Column will keep old name."
                            )
                        else:
                            raise

    def _repair_unapplied_pattern_priorities(
        self, conn: sqlite3.Connection
    ) -> None:
        """Restore initial priority for patterns that were never applied.

        v12 and earlier: ``_update_all_priorities()`` in the aggregator
        recalculated ALL pattern priorities after every job completion,
        including patterns with zero applications. The frequency factor
        (``log(2)/log(100) ≈ 0.15``) crushed single-occurrence patterns
        from 0.5 → ~0.075, below the old default ``min_priority=0.3`` query
        threshold (now 0.01). This made them invisible to the injection path, so
        per-sheet feedback never fired and priorities never recovered.

        This one-time repair restores such patterns to 0.5 so they can
        be queried, injected, and receive real feedback.
        """
        try:
            cursor = conn.execute(
                """
                UPDATE patterns SET priority_score = 0.5
                WHERE (led_to_success_count + led_to_failure_count) = 0
                  AND priority_score < 0.3
                """
            )
            repaired = cursor.rowcount
            if repaired > 0:
                self._logger.info(
                    "repaired_unapplied_pattern_priorities",
                    count=repaired,
                )
        except sqlite3.OperationalError:
            # patterns table may not exist yet on fresh DBs
            pass

    def _drop_pattern_applications_fk_constraints(
        self, conn: sqlite3.Connection,
    ) -> None:
        """Recreate pattern_applications without FOREIGN KEY constraints.

        Issue #129: Legacy databases created pattern_applications with
        ``REFERENCES patterns(id)`` and ``REFERENCES executions(id)``.
        The code no longer defines these FK constraints (since they cause
        IntegrityErrors when pattern IDs from local stores or synthetic
        execution IDs are recorded), but ``CREATE TABLE IF NOT EXISTS``
        does not alter existing tables.

        This migration recreates the table without FK constraints,
        preserving all existing data. SQLite does not support
        ``ALTER TABLE ... DROP CONSTRAINT``, so we must copy-and-swap.
        """
        try:
            # Check if the table has FK constraints
            fk_list = conn.execute(
                "PRAGMA foreign_key_list(pattern_applications)"
            ).fetchall()
            if not fk_list:
                return  # No FK constraints — nothing to do

            self._logger.info(
                "migrating_pattern_applications_drop_fk",
                fk_count=len(fk_list),
            )

            # Disable FK enforcement during migration
            conn.execute("PRAGMA foreign_keys=OFF")

            # Copy data to temp table
            conn.execute("""
                CREATE TABLE pattern_applications_new (
                    id TEXT PRIMARY KEY,
                    pattern_id TEXT,
                    execution_id TEXT,
                    applied_at TIMESTAMP,
                    pattern_led_to_success BOOLEAN,
                    retry_count_before INTEGER,
                    retry_count_after INTEGER,
                    grounding_confidence REAL
                )
            """)
            conn.execute("""
                INSERT INTO pattern_applications_new
                SELECT id, pattern_id, execution_id, applied_at,
                       pattern_led_to_success, retry_count_before,
                       retry_count_after, grounding_confidence
                FROM pattern_applications
            """)

            # Swap tables
            conn.execute("DROP TABLE pattern_applications")
            conn.execute(
                "ALTER TABLE pattern_applications_new "
                "RENAME TO pattern_applications"
            )

            # Recreate indexes
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_pattern "
                "ON pattern_applications(pattern_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_execution "
                "ON pattern_applications(execution_id)"
            )

            # Re-enable FK enforcement
            conn.execute("PRAGMA foreign_keys=ON")

            row_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM pattern_applications"
            ).fetchone()
            self._logger.info(
                "migrated_pattern_applications_drop_fk",
                rows_preserved=row_count["cnt"] if row_count else 0,
            )

        except sqlite3.OperationalError as e:
            # Table might not exist yet on fresh DBs
            if "no such table" in str(e).lower():
                return
            raise

    @staticmethod
    def hash_workspace(workspace_path: Path) -> str:
        """Generate a stable hash for a workspace path.

        Creates a reproducible 16-character hex hash from the resolved
        absolute path. This allows pattern matching across sessions
        while preserving privacy (paths are not stored directly).

        Args:
            workspace_path: The absolute path to the workspace.

        Returns:
            A hex string hash of the workspace path (16 characters).
        """
        normalized = str(workspace_path.resolve())
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    @staticmethod
    def hash_job(job_name: str, config_hash: str | None = None) -> str:
        """Generate a stable hash for a job.

        Creates a reproducible 16-character hex hash from the job name
        and optional config hash. The config hash enables version-awareness:
        the same job with different configs will have different hashes.

        Args:
            job_name: The job name.
            config_hash: Optional hash of the job config for versioning.

        Returns:
            A hex string hash of the job (16 characters).
        """
        combined = f"{job_name}:{config_hash or ''}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    _DATA_TABLES = (
        "pattern_applications",
        "error_recoveries",
        "patterns",
        "executions",
        "workspace_clusters",
        "rate_limit_events",
        "escalation_decisions",
        "pattern_discovery_events",
        "evolution_trajectory",
        "exploration_budget",
        "entropy_responses",
        "pattern_entropy_history",
    )
    """All data tables (excludes schema_version) cleared by ``clear_all()``."""

    def clear_all(self) -> None:
        """Clear all data from the global store.

        WARNING: This is destructive and should only be used for testing.
        """
        with self._get_connection() as conn:
            for table in self._DATA_TABLES:
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 -- table names are hardcoded constants

        _logger.warning("Cleared all data from global learning store")


# Export public API
__all__ = [
    "GlobalLearningStoreBase",
    "DEFAULT_GLOBAL_STORE_PATH",
    "SQLParam",
    "WhereBuilder",
    "_logger",
]
