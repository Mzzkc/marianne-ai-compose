"""Tests for the unified migration runner (Phase 2)."""

from __future__ import annotations

import pytest
import aiosqlite

from marianne.schema.migrate import apply_migrations, get_version, set_version


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _memory_db() -> aiosqlite.Connection:
    """Create a fresh in-memory database."""
    db = await aiosqlite.connect(":memory:")
    return db


# ---------------------------------------------------------------------------
# get_version / set_version
# ---------------------------------------------------------------------------


class TestGetSetVersion:
    @pytest.mark.asyncio
    async def test_fresh_db_version_is_zero(self) -> None:
        db = await _memory_db()
        assert await get_version(db) == 0
        await db.close()

    @pytest.mark.asyncio
    async def test_set_and_get(self) -> None:
        db = await _memory_db()
        await set_version(db, 5)
        assert await get_version(db) == 5
        await db.close()

    @pytest.mark.asyncio
    async def test_set_version_overwrites(self) -> None:
        db = await _memory_db()
        await set_version(db, 3)
        await set_version(db, 7)
        assert await get_version(db) == 7
        await db.close()


# ---------------------------------------------------------------------------
# apply_migrations — basic functionality
# ---------------------------------------------------------------------------


class TestApplyMigrations:
    @pytest.mark.asyncio
    async def test_applies_all_on_empty_db(self) -> None:
        db = await _memory_db()
        migrations = [
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY)",
            "CREATE TABLE t2 (id INTEGER PRIMARY KEY)",
            "CREATE TABLE t3 (id INTEGER PRIMARY KEY)",
        ]
        result = await apply_migrations(db, migrations, db_name="test")
        assert result == 3
        assert await get_version(db) == 3

        # Verify tables were created
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        assert tables == {"t1", "t2", "t3"}
        await db.close()

    @pytest.mark.asyncio
    async def test_idempotent(self) -> None:
        """Running apply_migrations twice produces the same result."""
        db = await _memory_db()
        migrations = [
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY)",
        ]
        await apply_migrations(db, migrations, db_name="test")
        # Run again — should be a no-op
        result = await apply_migrations(db, migrations, db_name="test")
        assert result == 1
        assert await get_version(db) == 1
        await db.close()

    @pytest.mark.asyncio
    async def test_skips_applied_migrations(self) -> None:
        """Only applies migrations after the current version."""
        db = await _memory_db()
        # Apply first migration
        migrations_v1 = [
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY)",
        ]
        await apply_migrations(db, migrations_v1, db_name="test")

        # Now add more migrations
        migrations_v3 = [
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY)",
            "CREATE TABLE t2 (id INTEGER PRIMARY KEY)",
            "ALTER TABLE t1 ADD COLUMN name TEXT",
        ]
        result = await apply_migrations(db, migrations_v3, db_name="test")
        assert result == 3
        assert await get_version(db) == 3

        # Verify t2 was created and t1 has the name column
        cursor = await db.execute("PRAGMA table_info(t1)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "name" in columns
        await db.close()

    @pytest.mark.asyncio
    async def test_no_migrations_is_noop(self) -> None:
        db = await _memory_db()
        result = await apply_migrations(db, [], db_name="test")
        assert result == 0
        assert await get_version(db) == 0
        await db.close()


# ---------------------------------------------------------------------------
# Forward version (database newer than app)
# ---------------------------------------------------------------------------


class TestForwardVersion:
    @pytest.mark.asyncio
    async def test_forward_version_warns_not_errors(self) -> None:
        """A database from a newer Mozart version should produce a warning, not fail."""
        db = await _memory_db()
        await set_version(db, 10)

        # Only 3 migrations known — version 10 is "from the future"
        migrations = [
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY)",
            "CREATE TABLE t2 (id INTEGER PRIMARY KEY)",
            "CREATE TABLE t3 (id INTEGER PRIMARY KEY)",
        ]
        result = await apply_migrations(db, migrations, db_name="test")
        # Should return current version (10), not target (3)
        assert result == 10
        assert await get_version(db) == 10
        await db.close()


# ---------------------------------------------------------------------------
# Rollback on failure
# ---------------------------------------------------------------------------


class TestMigrationFailure:
    @pytest.mark.asyncio
    async def test_failed_migration_rolls_back(self) -> None:
        """A failed migration should not leave partial schema changes."""
        db = await _memory_db()
        migrations = [
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY)",
            # This migration will fail (t1 already exists, IF NOT EXISTS not used)
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY)",
        ]
        with pytest.raises(Exception):
            await apply_migrations(db, migrations, db_name="test")

        # First migration should have succeeded (version = 1)
        assert await get_version(db) == 1
        await db.close()

    @pytest.mark.asyncio
    async def test_version_not_advanced_on_failure(self) -> None:
        """Version should stay at the last successful migration."""
        db = await _memory_db()
        migrations = [
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY)",
            "CREATE TABLE t2 (id INTEGER PRIMARY KEY)",
            # Third migration fails with invalid SQL
            "THIS IS NOT VALID SQL",
        ]
        with pytest.raises(Exception):
            await apply_migrations(db, migrations, db_name="test")

        # Should stop at version 2
        assert await get_version(db) == 2
        await db.close()


# ---------------------------------------------------------------------------
# Callable migrations
# ---------------------------------------------------------------------------


class TestCallableMigrations:
    @pytest.mark.asyncio
    async def test_python_callable_migration(self) -> None:
        """Async callables can be used for complex data transforms."""
        db = await _memory_db()

        async def create_and_seed(conn: aiosqlite.Connection) -> None:
            await conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
            await conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?)",
                ("version", "1.0.0"),
            )

        migrations = [create_and_seed]
        await apply_migrations(db, migrations, db_name="test")

        cursor = await db.execute("SELECT value FROM config WHERE key = 'version'")
        row = await cursor.fetchone()
        assert row[0] == "1.0.0"
        assert await get_version(db) == 1
        await db.close()

    @pytest.mark.asyncio
    async def test_mixed_sql_and_callable(self) -> None:
        """SQL strings and callables can be mixed freely."""
        db = await _memory_db()

        async def seed_data(conn: aiosqlite.Connection) -> None:
            await conn.execute("INSERT INTO t1 (id, name) VALUES (1, 'test')")

        migrations = [
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY, name TEXT)",
            seed_data,
            "ALTER TABLE t1 ADD COLUMN active INTEGER DEFAULT 1",
        ]
        await apply_migrations(db, migrations, db_name="test")

        cursor = await db.execute("SELECT name, active FROM t1 WHERE id = 1")
        row = await cursor.fetchone()
        assert row[0] == "test"
        assert row[1] == 1
        assert await get_version(db) == 3
        await db.close()

    @pytest.mark.asyncio
    async def test_callable_failure_rolls_back(self) -> None:
        db = await _memory_db()

        async def failing_migration(conn: aiosqlite.Connection) -> None:
            await conn.execute("CREATE TABLE temp_table (id INTEGER)")
            raise RuntimeError("Simulated migration failure")

        migrations = [
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY)",
            failing_migration,
        ]
        with pytest.raises(RuntimeError, match="Simulated migration failure"):
            await apply_migrations(db, migrations, db_name="test")

        assert await get_version(db) == 1
        # temp_table should not exist (rolled back)
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='temp_table'"
        )
        assert await cursor.fetchone() is None
        await db.close()


# ---------------------------------------------------------------------------
# Multi-statement SQL migrations
# ---------------------------------------------------------------------------


class TestMultiStatementMigrations:
    @pytest.mark.asyncio
    async def test_semicolon_separated_statements(self) -> None:
        db = await _memory_db()
        migrations = [
            (
                "CREATE TABLE t1 (id INTEGER PRIMARY KEY);"
                "CREATE TABLE t2 (id INTEGER PRIMARY KEY);"
                "CREATE INDEX idx_t1 ON t1(id)"
            ),
        ]
        await apply_migrations(db, migrations, db_name="test")

        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        assert "t1" in tables
        assert "t2" in tables
        assert await get_version(db) == 1
        await db.close()
