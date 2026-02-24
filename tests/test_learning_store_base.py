"""Tests for mozart.learning.store.base module.

Covers:
- WhereBuilder: clause accumulation, build with/without clauses, multi-param clauses
- GlobalLearningStoreBase: initialization, schema creation, migration, connection
  management, hash utilities, schema version tracking, batch_connection, clear_all
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from mozart.learning.store.base import (
    DEFAULT_GLOBAL_STORE_PATH,
    GlobalLearningStoreBase,
    WhereBuilder,
)


# ---------------------------------------------------------------------------
# WhereBuilder
# ---------------------------------------------------------------------------


class TestWhereBuilder:
    """Tests for the WhereBuilder helper class."""

    def test_build_empty_returns_tautology(self):
        """Build with no clauses returns '1=1' and empty params."""
        wb = WhereBuilder()
        sql, params = wb.build()
        assert sql == "1=1"
        assert params == ()

    def test_single_clause(self):
        """A single clause is returned verbatim."""
        wb = WhereBuilder()
        wb.add("status = ?", "active")
        sql, params = wb.build()
        assert sql == "status = ?"
        assert params == ("active",)

    def test_multiple_clauses_joined_with_and(self):
        """Multiple clauses are joined with AND."""
        wb = WhereBuilder()
        wb.add("status = ?", "active")
        wb.add("score >= ?", 0.8)
        sql, params = wb.build()
        assert sql == "status = ? AND score >= ?"
        assert params == ("active", 0.8)

    def test_clause_with_multiple_params(self):
        """A clause can bind more than one parameter."""
        wb = WhereBuilder()
        wb.add("created BETWEEN ? AND ?", "2026-01-01", "2026-12-31")
        sql, params = wb.build()
        assert sql == "created BETWEEN ? AND ?"
        assert params == ("2026-01-01", "2026-12-31")

    def test_mixed_param_types(self):
        """Parameters of different types (str, int, float, None) are preserved."""
        wb = WhereBuilder()
        wb.add("name = ?", "test")
        wb.add("count > ?", 5)
        wb.add("score < ?", 0.9)
        wb.add("deleted_at IS ?", None)
        sql, params = wb.build()
        assert "AND" in sql
        assert params == ("test", 5, 0.9, None)

    def test_clause_without_params(self):
        """A clause with zero bind parameters is allowed."""
        wb = WhereBuilder()
        wb.add("1=1")
        sql, params = wb.build()
        assert sql == "1=1"
        assert params == ()

    def test_build_returns_tuple_for_params(self):
        """Params are always returned as a tuple, not list."""
        wb = WhereBuilder()
        wb.add("x = ?", 1)
        _, params = wb.build()
        assert isinstance(params, tuple)

    def test_build_is_idempotent(self):
        """Calling build() multiple times returns the same result."""
        wb = WhereBuilder()
        wb.add("a = ?", 1)
        wb.add("b = ?", 2)
        first = wb.build()
        second = wb.build()
        assert first == second

    def test_usable_in_sqlite_query(self, tmp_path: Path):
        """WhereBuilder output works correctly in a real SQLite query."""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (name TEXT, score REAL)")
        conn.execute("INSERT INTO t VALUES ('a', 0.5)")
        conn.execute("INSERT INTO t VALUES ('b', 0.9)")
        conn.execute("INSERT INTO t VALUES ('c', 0.3)")
        conn.commit()

        wb = WhereBuilder()
        wb.add("score >= ?", 0.4)
        wb.add("name != ?", "a")
        where_sql, params = wb.build()
        rows = conn.execute(
            f"SELECT name FROM t WHERE {where_sql}", params
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == "b"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> GlobalLearningStoreBase:
    """Create a GlobalLearningStoreBase with a temporary database."""
    db_path = tmp_path / "test-learning.db"
    return GlobalLearningStoreBase(db_path=db_path)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return a temporary database path (not yet created)."""
    return tmp_path / "sub" / "dir" / "learning.db"


# ---------------------------------------------------------------------------
# GlobalLearningStoreBase — Initialization
# ---------------------------------------------------------------------------


class TestInitialization:
    """Tests for store initialization and database setup."""

    def test_default_db_path(self):
        """DEFAULT_GLOBAL_STORE_PATH points to ~/.mozart/global-learning.db."""
        assert DEFAULT_GLOBAL_STORE_PATH == Path.home() / ".mozart" / "global-learning.db"

    def test_custom_db_path(self, tmp_path: Path):
        """Custom db_path is stored on the instance."""
        db_path = tmp_path / "custom.db"
        s = GlobalLearningStoreBase(db_path=db_path)
        assert s.db_path == db_path

    def test_creates_parent_directories(self, db_path: Path):
        """Parent directories are created automatically during init."""
        assert not db_path.parent.exists()
        GlobalLearningStoreBase(db_path=db_path)
        assert db_path.parent.exists()

    def test_database_file_created(self, tmp_path: Path):
        """The SQLite database file exists after init."""
        db_path = tmp_path / "test.db"
        GlobalLearningStoreBase(db_path=db_path)
        assert db_path.exists()

    def test_reinitialize_existing_db(self, tmp_path: Path):
        """Reinitializing on an existing database is safe (idempotent)."""
        db_path = tmp_path / "test.db"
        s1 = GlobalLearningStoreBase(db_path=db_path)
        # Insert some data to verify it persists
        with s1._get_connection() as conn:
            conn.execute(
                "INSERT INTO patterns (id, pattern_type, pattern_name) VALUES (?, ?, ?)",
                ("p1", "test", "test_pattern"),
            )
        # Re-init on same db
        s2 = GlobalLearningStoreBase(db_path=db_path)
        with s2._get_connection() as conn:
            row = conn.execute("SELECT * FROM patterns WHERE id = 'p1'").fetchone()
        assert row is not None
        assert row["pattern_name"] == "test_pattern"


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------


class TestSchemaCreation:
    """Tests for database schema creation."""

    EXPECTED_TABLES = [
        "schema_version",
        "executions",
        "patterns",
        "pattern_applications",
        "error_recoveries",
        "workspace_clusters",
        "rate_limit_events",
        "escalation_decisions",
        "pattern_discovery_events",
        "evolution_trajectory",
        "exploration_budget",
        "entropy_responses",
        "pattern_entropy_history",
    ]

    def test_all_tables_created(self, store: GlobalLearningStoreBase):
        """All expected tables exist after initialization."""
        with store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row["name"] for row in cursor.fetchall()}

        for table in self.EXPECTED_TABLES:
            assert table in tables, f"Missing table: {table}"

    def test_indexes_created(self, store: GlobalLearningStoreBase):
        """Key indexes exist after initialization."""
        with store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
            indexes = {row["name"] for row in cursor.fetchall()}

        # Spot-check a few important indexes
        expected_indexes = [
            "idx_exec_workspace_job",
            "idx_exec_status",
            "idx_patterns_type",
            "idx_patterns_priority",
            "idx_patterns_quarantine",
            "idx_patterns_trust",
            "idx_app_pattern",
            "idx_recovery_code",
            "idx_rate_limit_code",
            "idx_escalation_job",
            "idx_entropy_history_calculated",
        ]
        for idx in expected_indexes:
            assert idx in indexes, f"Missing index: {idx}"

    def test_executions_table_columns(self, store: GlobalLearningStoreBase):
        """Executions table has the expected columns."""
        with store._get_connection() as conn:
            cols = store._get_existing_columns(conn, "executions")
        assert cols is not None
        expected = {
            "id", "workspace_hash", "job_hash", "sheet_num",
            "started_at", "completed_at", "duration_seconds",
            "status", "retry_count", "success_without_retry",
            "validation_pass_rate", "confidence_score", "model", "error_codes",
        }
        assert expected.issubset(cols)

    def test_patterns_table_columns(self, store: GlobalLearningStoreBase):
        """Patterns table includes quarantine and trust columns."""
        with store._get_connection() as conn:
            cols = store._get_existing_columns(conn, "patterns")
        assert cols is not None
        quarantine_cols = {
            "quarantine_status", "provenance_job_hash", "provenance_sheet_num",
            "quarantined_at", "validated_at", "quarantine_reason",
            "trust_score", "trust_calculation_date",
            "success_factors", "success_factors_updated_at",
        }
        assert quarantine_cols.issubset(cols)

    def test_pattern_applications_has_grounding_confidence(self, store: GlobalLearningStoreBase):
        """pattern_applications table has the grounding_confidence column."""
        with store._get_connection() as conn:
            cols = store._get_existing_columns(conn, "pattern_applications")
        assert cols is not None
        assert "grounding_confidence" in cols
        assert "pattern_led_to_success" in cols


# ---------------------------------------------------------------------------
# Schema version tracking
# ---------------------------------------------------------------------------


class TestSchemaVersionTracking:
    """Tests for schema version management."""

    def test_schema_version_recorded(self, store: GlobalLearningStoreBase):
        """Schema version is recorded in schema_version table."""
        with store._get_connection() as conn:
            cursor = conn.execute("SELECT version FROM schema_version LIMIT 1")
            row = cursor.fetchone()
        assert row is not None
        assert row["version"] == GlobalLearningStoreBase.SCHEMA_VERSION

    def test_schema_version_matches_constant(self, store: GlobalLearningStoreBase):
        """The stored version matches the class constant."""
        with store._get_connection() as conn:
            cursor = conn.execute("SELECT version FROM schema_version")
            row = cursor.fetchone()
        assert row["version"] == 13  # current SCHEMA_VERSION

    def test_schema_version_single_row(self, store: GlobalLearningStoreBase):
        """Only one row exists in schema_version after init."""
        with store._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) as cnt FROM schema_version")
            count = cursor.fetchone()["cnt"]
        assert count == 1

    def test_migration_bumps_version(self, tmp_path: Path):
        """Re-initializing after a version bump updates the stored version."""
        db_path = tmp_path / "test.db"
        store = GlobalLearningStoreBase(db_path=db_path)

        # Manually lower the schema version to simulate an old db
        with store._get_connection() as conn:
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (5,))

        # Re-init triggers migration
        store2 = GlobalLearningStoreBase(db_path=db_path)
        with store2._get_connection() as conn:
            cursor = conn.execute("SELECT version FROM schema_version LIMIT 1")
            row = cursor.fetchone()
        assert row["version"] == GlobalLearningStoreBase.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    """Tests for column migration on existing databases."""

    def test_get_existing_columns_for_existing_table(self, store: GlobalLearningStoreBase):
        """_get_existing_columns returns column names for a real table."""
        with store._get_connection() as conn:
            cols = store._get_existing_columns(conn, "executions")
        assert cols is not None
        assert isinstance(cols, set)
        assert "id" in cols

    def test_get_existing_columns_for_nonexistent_table(self, store: GlobalLearningStoreBase):
        """_get_existing_columns returns None for a table that does not exist."""
        with store._get_connection() as conn:
            cols = store._get_existing_columns(conn, "nonexistent_table")
        assert cols is None

    def test_column_migration_adds_missing_columns(self, tmp_path: Path):
        """Missing columns are added during migration."""
        db_path = tmp_path / "test.db"

        # Create an older patterns table that has the core columns needed for
        # indexes (e.g. priority_score) but is missing the migration-only
        # columns (quarantine_status, trust_score, success_factors, etc.).
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY)
        """)
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.execute("""
            CREATE TABLE patterns (
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
                priority_score REAL DEFAULT 0.5
            )
        """)
        conn.execute("""
            CREATE TABLE pattern_applications (
                id TEXT PRIMARY KEY,
                pattern_id TEXT,
                execution_id TEXT,
                applied_at TIMESTAMP,
                pattern_led_to_success BOOLEAN,
                retry_count_before INTEGER,
                retry_count_after INTEGER
            )
        """)
        conn.commit()
        conn.close()

        # Init store — should migrate and add missing columns
        store = GlobalLearningStoreBase(db_path=db_path)
        with store._get_connection() as conn:
            cols = store._get_existing_columns(conn, "patterns")
        assert cols is not None
        assert "quarantine_status" in cols
        assert "trust_score" in cols
        assert "success_factors" in cols

    def test_column_migration_idempotent(self, tmp_path: Path):
        """Running migration twice does not raise errors."""
        db_path = tmp_path / "test.db"
        s1 = GlobalLearningStoreBase(db_path=db_path)
        # Force re-migration by lowering version
        with s1._get_connection() as conn:
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        # Second init triggers migration again
        GlobalLearningStoreBase(db_path=db_path)
        # No exception means idempotent


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


class TestConnectionManagement:
    """Tests for _get_connection and batch_connection."""

    def test_connection_uses_wal_mode(self, store: GlobalLearningStoreBase):
        """Connection is configured with WAL journal mode."""
        with store._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_connection_enables_foreign_keys(self, store: GlobalLearningStoreBase):
        """Connection enables foreign key constraints."""
        with store._get_connection() as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_connection_has_row_factory(self, store: GlobalLearningStoreBase):
        """Connection uses sqlite3.Row as row_factory."""
        with store._get_connection() as conn:
            assert conn.row_factory is sqlite3.Row

    def test_connection_commits_on_success(self, store: GlobalLearningStoreBase):
        """Successful operations are auto-committed."""
        with store._get_connection() as conn:
            conn.execute(
                "INSERT INTO patterns (id, pattern_type, pattern_name) VALUES (?, ?, ?)",
                ("commit_test", "test", "commit_pattern"),
            )
        # Verify data persists in a new connection
        with store._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM patterns WHERE id = 'commit_test'"
            ).fetchone()
        assert row is not None

    def test_connection_rollback_on_error(self, store: GlobalLearningStoreBase):
        """Failed operations are rolled back."""
        try:
            with store._get_connection() as conn:
                conn.execute(
                    "INSERT INTO patterns (id, pattern_type, pattern_name) VALUES (?, ?, ?)",
                    ("rollback_test", "test", "rollback_pattern"),
                )
                # Force an error
                raise ValueError("deliberate error")
        except ValueError:
            pass

        # Data should not have been committed
        with store._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM patterns WHERE id = 'rollback_test'"
            ).fetchone()
        assert row is None

    def test_batch_connection_reused(self, store: GlobalLearningStoreBase):
        """Within batch_connection, _get_connection reuses the same connection."""
        connection_ids = []
        with store.batch_connection() as batch_conn:
            connection_ids.append(id(batch_conn))
            with store._get_connection() as inner_conn:
                connection_ids.append(id(inner_conn))
            with store._get_connection() as inner_conn2:
                connection_ids.append(id(inner_conn2))
        # All should be the same object
        assert connection_ids[0] == connection_ids[1] == connection_ids[2]

    def test_batch_connection_commits_once(self, store: GlobalLearningStoreBase):
        """batch_connection commits all operations at the end."""
        with store.batch_connection():
            with store._get_connection() as conn:
                conn.execute(
                    "INSERT INTO patterns (id, pattern_type, pattern_name) VALUES (?, ?, ?)",
                    ("batch1", "test", "batch_pattern_1"),
                )
            with store._get_connection() as conn:
                conn.execute(
                    "INSERT INTO patterns (id, pattern_type, pattern_name) VALUES (?, ?, ?)",
                    ("batch2", "test", "batch_pattern_2"),
                )

        # Both should be persisted
        with store._get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM patterns WHERE id IN ('batch1', 'batch2')"
            ).fetchall()
        assert len(rows) == 2

    def test_batch_connection_rollback_on_error(self, store: GlobalLearningStoreBase):
        """batch_connection rolls back everything on error."""
        try:
            with store.batch_connection():
                with store._get_connection() as conn:
                    conn.execute(
                        "INSERT INTO patterns (id, pattern_type, pattern_name) VALUES (?, ?, ?)",
                        ("batch_fail", "test", "should_not_persist"),
                    )
                raise RuntimeError("batch failure")
        except RuntimeError:
            pass

        with store._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM patterns WHERE id = 'batch_fail'"
            ).fetchone()
        assert row is None

    def test_batch_connection_does_not_leak(self, store: GlobalLearningStoreBase):
        """After batch_connection exits, _get_connection creates fresh connections."""
        with store.batch_connection() as batch_conn:
            batch_id = id(batch_conn)

        with store._get_connection() as fresh_conn:
            fresh_id = id(fresh_conn)

        assert batch_id != fresh_id

    def test_close_is_noop(self, store: GlobalLearningStoreBase):
        """close() completes without error (it's a no-op)."""
        store.close()
        # Store should still work after close()
        with store._get_connection() as conn:
            conn.execute("SELECT 1")


# ---------------------------------------------------------------------------
# Hash utilities
# ---------------------------------------------------------------------------


class TestHashUtilities:
    """Tests for hash_workspace and hash_job static methods."""

    def test_hash_workspace_returns_16_hex_chars(self, tmp_path: Path):
        """hash_workspace returns a 16-character hex string."""
        h = GlobalLearningStoreBase.hash_workspace(tmp_path)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_workspace_deterministic(self, tmp_path: Path):
        """Same path always produces the same hash."""
        h1 = GlobalLearningStoreBase.hash_workspace(tmp_path)
        h2 = GlobalLearningStoreBase.hash_workspace(tmp_path)
        assert h1 == h2

    def test_hash_workspace_different_paths(self, tmp_path: Path):
        """Different paths produce different hashes."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        h_a = GlobalLearningStoreBase.hash_workspace(dir_a)
        h_b = GlobalLearningStoreBase.hash_workspace(dir_b)
        assert h_a != h_b

    def test_hash_workspace_resolves_path(self, tmp_path: Path):
        """hash_workspace resolves the path before hashing."""
        resolved = tmp_path.resolve()
        expected = hashlib.sha256(str(resolved).encode()).hexdigest()[:16]
        actual = GlobalLearningStoreBase.hash_workspace(tmp_path)
        assert actual == expected

    def test_hash_job_returns_16_hex_chars(self):
        """hash_job returns a 16-character hex string."""
        h = GlobalLearningStoreBase.hash_job("my-job")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_job_deterministic(self):
        """Same job name always produces the same hash."""
        h1 = GlobalLearningStoreBase.hash_job("my-job")
        h2 = GlobalLearningStoreBase.hash_job("my-job")
        assert h1 == h2

    def test_hash_job_different_names(self):
        """Different job names produce different hashes."""
        h1 = GlobalLearningStoreBase.hash_job("job-a")
        h2 = GlobalLearningStoreBase.hash_job("job-b")
        assert h1 != h2

    def test_hash_job_with_config_hash(self):
        """Config hash changes the job hash."""
        h_no_config = GlobalLearningStoreBase.hash_job("my-job")
        h_with_config = GlobalLearningStoreBase.hash_job("my-job", config_hash="abc123")
        assert h_no_config != h_with_config

    def test_hash_job_config_hash_none_same_as_omitted(self):
        """Passing config_hash=None produces the same hash as omitting it."""
        h1 = GlobalLearningStoreBase.hash_job("my-job")
        h2 = GlobalLearningStoreBase.hash_job("my-job", config_hash=None)
        assert h1 == h2

    def test_hash_job_matches_expected_algorithm(self):
        """hash_job uses sha256 of 'name:config_hash'."""
        expected = hashlib.sha256("my-job:cfg".encode()).hexdigest()[:16]
        actual = GlobalLearningStoreBase.hash_job("my-job", config_hash="cfg")
        assert actual == expected


# ---------------------------------------------------------------------------
# clear_all
# ---------------------------------------------------------------------------


class TestClearAll:
    """Tests for the clear_all method."""

    def test_clear_all_removes_data(self, store: GlobalLearningStoreBase):
        """clear_all removes all data from clearable tables."""
        # Insert data into several tables
        with store._get_connection() as conn:
            conn.execute(
                "INSERT INTO patterns (id, pattern_type, pattern_name) VALUES (?, ?, ?)",
                ("p1", "test", "test_pattern"),
            )
            conn.execute(
                "INSERT INTO executions (id, workspace_hash, job_hash, sheet_num) "
                "VALUES (?, ?, ?, ?)",
                ("e1", "ws1", "j1", 1),
            )
            conn.execute(
                "INSERT INTO error_recoveries (id, error_code) VALUES (?, ?)",
                ("r1", "E101"),
            )

        store.clear_all()

        with store._get_connection() as conn:
            for table in ["patterns", "executions", "error_recoveries",
                          "pattern_applications", "workspace_clusters",
                          "rate_limit_events", "escalation_decisions",
                          "pattern_discovery_events", "evolution_trajectory"]:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                assert count == 0, f"Table {table} should be empty after clear_all"

    def test_clear_all_preserves_schema_version(self, store: GlobalLearningStoreBase):
        """clear_all does not touch the schema_version table."""
        store.clear_all()
        with store._get_connection() as conn:
            cursor = conn.execute("SELECT version FROM schema_version LIMIT 1")
            row = cursor.fetchone()
        assert row is not None
        assert row["version"] == GlobalLearningStoreBase.SCHEMA_VERSION

    def test_clear_all_allows_reinsertion(self, store: GlobalLearningStoreBase):
        """After clear_all, new data can be inserted normally."""
        store.clear_all()
        with store._get_connection() as conn:
            conn.execute(
                "INSERT INTO patterns (id, pattern_type, pattern_name) VALUES (?, ?, ?)",
                ("new_p", "test", "new_pattern"),
            )
        with store._get_connection() as conn:
            row = conn.execute("SELECT * FROM patterns WHERE id = 'new_p'").fetchone()
        assert row is not None
