"""Tests for learning store FK constraint migration (#129).

Verifies that:
1. The v15 migration removes FK constraints from pattern_applications
2. The migration preserves all existing data
3. record_pattern_application handles IntegrityError gracefully
4. Fresh databases have no FK constraints
"""

import sqlite3
from pathlib import Path

import pytest

from mozart.learning.store import GlobalLearningStore


@pytest.fixture()
def temp_db(tmp_path: Path) -> Path:
    """Return a temporary database path."""
    return tmp_path / "test-fk-migration.db"


def _create_legacy_db(db_path: Path) -> None:
    """Create a database with the legacy FK-constrained schema.

    Simulates what older Mozart versions created, matching the actual
    schema found in production databases (issue #129).
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")

    # Schema version table
    conn.execute("""
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY)
    """)
    conn.execute("INSERT INTO schema_version (version) VALUES (14)")

    # Executions table (referenced by pattern_applications)
    conn.execute("""
        CREATE TABLE executions (
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

    # Patterns table (referenced by pattern_applications)
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
            context_tags TEXT DEFAULT '[]',
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

    # pattern_applications WITH FK constraints (the legacy schema)
    conn.execute("""
        CREATE TABLE pattern_applications (
            id TEXT PRIMARY KEY,
            pattern_id TEXT REFERENCES patterns(id),
            execution_id TEXT REFERENCES executions(id),
            applied_at TIMESTAMP,
            pattern_led_to_success BOOLEAN,
            retry_count_before INTEGER,
            retry_count_after INTEGER,
            grounding_confidence REAL
        )
    """)
    conn.execute(
        "CREATE INDEX idx_app_pattern ON pattern_applications(pattern_id)"
    )
    conn.execute(
        "CREATE INDEX idx_app_execution ON pattern_applications(execution_id)"
    )

    # Insert test data
    from datetime import datetime

    now = datetime.now().isoformat()

    conn.execute(
        "INSERT INTO executions (id, workspace_hash, job_hash, sheet_num) "
        "VALUES ('exec1', 'ws1', 'job1', 1)"
    )
    conn.execute(
        "INSERT INTO patterns (id, pattern_type, pattern_name, first_seen, "
        "last_seen, last_confirmed) VALUES ('pat1', 'TEST', 'test', ?, ?, ?)",
        (now, now, now),
    )
    conn.execute(
        "INSERT INTO pattern_applications "
        "(id, pattern_id, execution_id, applied_at, pattern_led_to_success) "
        "VALUES ('app1', 'pat1', 'exec1', ?, 1)",
        (now,),
    )

    conn.commit()
    conn.close()


class TestFKMigration:
    """Tests for the v15 FK constraint removal migration."""

    def test_legacy_db_has_fk_constraints(self, temp_db: Path) -> None:
        """Verify the legacy DB fixture actually has FK constraints."""
        _create_legacy_db(temp_db)

        conn = sqlite3.connect(str(temp_db))
        fk_list = conn.execute(
            "PRAGMA foreign_key_list(pattern_applications)"
        ).fetchall()
        conn.close()

        assert len(fk_list) == 2, (
            f"Legacy DB must have 2 FK constraints, got {len(fk_list)}"
        )

    def test_migration_removes_fk_constraints(self, temp_db: Path) -> None:
        """v15 migration removes FK constraints from pattern_applications."""
        _create_legacy_db(temp_db)

        # Opening the store triggers migration
        store = GlobalLearningStore(db_path=temp_db)

        # Verify FK constraints are gone
        conn = sqlite3.connect(str(temp_db))
        conn.row_factory = sqlite3.Row
        fk_list = conn.execute(
            "PRAGMA foreign_key_list(pattern_applications)"
        ).fetchall()
        conn.close()

        assert len(fk_list) == 0, (
            f"FK constraints must be removed after migration, got {len(fk_list)}"
        )

        # Verify schema version updated
        with store._get_connection() as c:
            row = c.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
        assert row["version"] == 15

    def test_migration_preserves_data(self, temp_db: Path) -> None:
        """v15 migration preserves all existing pattern_applications rows."""
        _create_legacy_db(temp_db)

        store = GlobalLearningStore(db_path=temp_db)

        with store._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM pattern_applications"
            ).fetchone()
            assert row["cnt"] == 1, "Migration must preserve existing rows"

            app = conn.execute(
                "SELECT * FROM pattern_applications WHERE id = 'app1'"
            ).fetchone()
            assert app is not None
            assert app["pattern_id"] == "pat1"
            assert app["execution_id"] == "exec1"
            assert app["pattern_led_to_success"] == 1

    def test_migration_preserves_indexes(self, temp_db: Path) -> None:
        """v15 migration preserves indexes on pattern_applications."""
        _create_legacy_db(temp_db)

        store = GlobalLearningStore(db_path=temp_db)

        with store._get_connection() as conn:
            indexes = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='pattern_applications'"
            ).fetchall()

        index_names = {idx["name"] for idx in indexes}
        assert "idx_app_pattern" in index_names
        assert "idx_app_execution" in index_names

    def test_post_migration_insert_with_synthetic_execution_id(
        self, temp_db: Path,
    ) -> None:
        """After migration, inserting with a non-existent execution_id works."""
        _create_legacy_db(temp_db)

        store = GlobalLearningStore(db_path=temp_db)

        # This would fail with FK constraints because "sheet_42" doesn't
        # exist in the executions table
        app_id = store.record_pattern_application(
            pattern_id="pat1",
            execution_id="sheet_42",
            pattern_led_to_success=True,
        )
        assert app_id is not None

        with store._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM pattern_applications "
                "WHERE execution_id = 'sheet_42'"
            ).fetchone()
            assert row["cnt"] == 1

    def test_fresh_db_has_no_fk_constraints(self, tmp_path: Path) -> None:
        """A fresh database created by GlobalLearningStore has no FK
        constraints on pattern_applications."""
        fresh_db = tmp_path / "fresh.db"
        _store = GlobalLearningStore(db_path=fresh_db)

        conn = sqlite3.connect(str(fresh_db))
        fk_list = conn.execute(
            "PRAGMA foreign_key_list(pattern_applications)"
        ).fetchall()
        conn.close()

        assert len(fk_list) == 0

    def test_integrity_error_caught_in_record_application(
        self, tmp_path: Path,
    ) -> None:
        """record_pattern_application catches IntegrityError for legacy DBs
        that haven't been migrated yet."""
        # Create a DB with FK constraints but DON'T trigger migration
        # by setting schema_version to 15 (pretend it's current)
        db_path = tmp_path / "unmigrated.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version (version) VALUES (15)")

        # Minimal tables with FK constraint
        conn.execute("""
            CREATE TABLE executions (id TEXT PRIMARY KEY,
                workspace_hash TEXT NOT NULL, job_hash TEXT NOT NULL,
                sheet_num INTEGER NOT NULL)
        """)
        conn.execute("""
            CREATE TABLE patterns (id TEXT PRIMARY KEY,
                pattern_type TEXT NOT NULL, pattern_name TEXT NOT NULL,
                description TEXT, occurrence_count INTEGER DEFAULT 1,
                first_seen TIMESTAMP, last_seen TIMESTAMP,
                last_confirmed TIMESTAMP,
                led_to_success_count INTEGER DEFAULT 0,
                led_to_failure_count INTEGER DEFAULT 0,
                effectiveness_score REAL DEFAULT 0.5,
                variance REAL DEFAULT 0.0,
                suggested_action TEXT, context_tags TEXT DEFAULT '[]',
                priority_score REAL DEFAULT 0.5,
                quarantine_status TEXT DEFAULT 'pending',
                trust_score REAL DEFAULT 0.5,
                trust_calculation_date TIMESTAMP,
                active INTEGER DEFAULT 1,
                content_hash TEXT, instrument_name TEXT)
        """)
        conn.execute("""
            CREATE TABLE pattern_applications (
                id TEXT PRIMARY KEY,
                pattern_id TEXT REFERENCES patterns(id),
                execution_id TEXT REFERENCES executions(id),
                applied_at TIMESTAMP, pattern_led_to_success BOOLEAN,
                retry_count_before INTEGER, retry_count_after INTEGER,
                grounding_confidence REAL)
        """)

        from datetime import datetime

        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO patterns (id, pattern_type, pattern_name, "
            "first_seen, last_seen, last_confirmed) "
            "VALUES ('p1', 'T', 'test', ?, ?, ?)",
            (now, now, now),
        )
        conn.commit()
        conn.close()

        store = GlobalLearningStore(db_path=db_path)

        # This should NOT raise — the store catches IntegrityError
        app_id = store.record_pattern_application(
            pattern_id="p1",
            execution_id="sheet_99",  # doesn't exist in executions
            pattern_led_to_success=True,
        )
        assert app_id is not None


class TestFKMigrationIdempotent:
    """Migration is safe to run multiple times."""

    def test_double_migration_is_safe(self, temp_db: Path) -> None:
        """Opening the store twice doesn't break the migration."""
        _create_legacy_db(temp_db)

        # First open — triggers migration
        store1 = GlobalLearningStore(db_path=temp_db)
        del store1

        # Second open — migration should be no-op
        store2 = GlobalLearningStore(db_path=temp_db)

        with store2._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM pattern_applications"
            ).fetchone()
            assert row["cnt"] == 1
