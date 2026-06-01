"""#328: test coverage for the SQLite state-backend schema migrations (v1→v4).

The migration logic in ``state/sqlite_backend.py`` (``SCHEMA_VERSION = 4``,
``_run_migrations`` + ``_migrate_v1..v4``) had no direct coverage: existing
tests exercise the *final* schema (round-tripping migrated columns) but never
the migration *stepping*. So "users upgrading across versions risk state
corruption" was deployed on faith.

These tests pin:
- a fresh DB reaches the current version with every migrated column present;
- migrations are idempotent (re-running is a safe no-op);
- stepwise migration from each intermediate start version reaches v4;
- user data survives a v1→v4 migration;
- the v4 column rename works for a legacy ``first_attempt_success`` DB AND is a
  safe no-op for a fresh DB that already has ``success_without_retry``.

Out of scope (noted on #328): concurrent writes *during* migration and
corrupted-DB recovery — those need dedicated concurrency/fault-injection
harnesses; the version-stepping + data-preservation core is the corruption risk
this issue flags.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.state.sqlite_backend import SCHEMA_VERSION, SQLiteStateBackend


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


async def _version(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    )
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _build_db_at_version(backend: SQLiteStateBackend, target: int) -> None:
    """Construct a DB at exactly schema version ``target`` (1..4)."""
    async with aiosqlite.connect(backend.db_path) as db:
        await backend._migrate_v1(db)
        if target >= 2:
            await backend._migrate_v2(db)
        if target >= 3:
            await backend._migrate_v3(db)
        if target >= 4:
            await backend._migrate_v4(db)


class TestFreshAndIdempotent:
    async def test_fresh_db_reaches_current_version(self, tmp_path: Path) -> None:
        backend = SQLiteStateBackend(tmp_path / "state.db")
        await backend._ensure_initialized()
        async with aiosqlite.connect(backend.db_path) as db:
            assert await _version(db) == SCHEMA_VERSION

    async def test_fresh_db_has_all_migrated_columns(self, tmp_path: Path) -> None:
        backend = SQLiteStateBackend(tmp_path / "state.db")
        await backend._ensure_initialized()
        async with aiosqlite.connect(backend.db_path) as db:
            jobs = await _columns(db, "jobs")
            sheets = await _columns(db, "sheets")
        assert "config_path" in jobs  # v2
        assert {"execution_duration_seconds", "exit_signal", "exit_reason"} <= sheets  # v3
        assert "success_without_retry" in sheets  # v1/v4
        assert "first_attempt_success" not in sheets  # renamed away

    async def test_migrations_idempotent(self, tmp_path: Path) -> None:
        backend = SQLiteStateBackend(tmp_path / "state.db")
        await backend._ensure_initialized()
        # Re-running must not raise and must leave the version unchanged.
        async with aiosqlite.connect(backend.db_path) as db:
            await backend._run_migrations(db)
            assert await _version(db) == SCHEMA_VERSION


class TestStepwiseMigration:
    @pytest.mark.parametrize("start_version", [1, 2, 3])
    async def test_stepwise_from_intermediate_reaches_v4(
        self, tmp_path: Path, start_version: int
    ) -> None:
        backend = SQLiteStateBackend(tmp_path / "state.db")
        await _build_db_at_version(backend, start_version)

        async with aiosqlite.connect(backend.db_path) as db:
            assert await _version(db) == start_version

        # A fresh backend on the same file steps up to the current version.
        await backend._ensure_initialized()
        async with aiosqlite.connect(backend.db_path) as db:
            assert await _version(db) == SCHEMA_VERSION
            sheets = await _columns(db, "sheets")
            jobs = await _columns(db, "jobs")
        assert "config_path" in jobs
        assert {"execution_duration_seconds", "exit_signal", "exit_reason"} <= sheets

    async def test_user_data_survives_v1_to_v4(self, tmp_path: Path) -> None:
        backend = SQLiteStateBackend(tmp_path / "state.db")
        await _build_db_at_version(backend, 1)

        # Insert a job + sheet using only v1 columns.
        async with aiosqlite.connect(backend.db_path) as db:
            await db.execute(
                "INSERT INTO jobs (id, name, total_sheets, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("job-1", "Legacy Job", 3, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
            )
            await db.execute(
                "INSERT INTO sheets (job_id, sheet_num, status) VALUES (?, ?, ?)",
                ("job-1", 1, "completed"),
            )
            await db.commit()

        await backend._ensure_initialized()

        # The job/sheet survive, and the migration is loadable through the API.
        loaded = await backend.load("job-1")
        assert loaded is not None
        assert loaded.job_name == "Legacy Job"
        assert 1 in loaded.sheets


class TestV4ColumnRename:
    async def test_v4_renames_legacy_first_attempt_success(self, tmp_path: Path) -> None:
        backend = SQLiteStateBackend(tmp_path / "state.db")
        # A legacy DB whose sheets table predates the rename.
        async with aiosqlite.connect(backend.db_path) as db:
            await db.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)"
            )
            await db.execute(
                "CREATE TABLE sheets (job_id TEXT, sheet_num INTEGER, "
                "first_attempt_success INTEGER, PRIMARY KEY (job_id, sheet_num))"
            )
            await db.execute(
                "INSERT INTO sheets (job_id, sheet_num, first_attempt_success) VALUES (?, ?, ?)",
                ("j", 1, 1),
            )
            await db.commit()
            await backend._migrate_v4(db)

            cols = await _columns(db, "sheets")
            assert "success_without_retry" in cols
            assert "first_attempt_success" not in cols
            cursor = await db.execute(
                "SELECT success_without_retry FROM sheets WHERE job_id='j' AND sheet_num=1"
            )
            row = await cursor.fetchone()
            assert row is not None and row[0] == 1  # value preserved across rename

    async def test_v4_noop_when_already_renamed(self, tmp_path: Path) -> None:
        backend = SQLiteStateBackend(tmp_path / "state.db")
        # Fresh v1 already has success_without_retry (no first_attempt_success).
        await _build_db_at_version(backend, 1)
        async with aiosqlite.connect(backend.db_path) as db:
            await backend._migrate_v4(db)  # must not raise
            cols = await _columns(db, "sheets")
            assert "success_without_retry" in cols
            assert await _version(db) == 4


class TestMigrationRoundTrip:
    async def test_save_load_after_full_migration(self, tmp_path: Path) -> None:
        """A migrated DB round-trips a full CheckpointState through the API."""
        backend = SQLiteStateBackend(tmp_path / "state.db")
        await _build_db_at_version(backend, 1)  # start legacy, then migrate via API
        state = CheckpointState(
            job_id="rt", job_name="RoundTrip", total_sheets=1, status=JobStatus.RUNNING,
            sheets={1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED,
                                  success_without_retry=True, exit_signal=9)},
        )
        await backend.save(state)
        loaded = await backend.load("rt")
        assert loaded is not None
        assert loaded.sheets[1].success_without_retry is True
        assert loaded.sheets[1].exit_signal == 9
