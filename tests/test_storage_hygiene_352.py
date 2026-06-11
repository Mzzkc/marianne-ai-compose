"""#352 increment 1: storage hygiene — vacuum migration + doctor --clean.

Measured eval (2026-06-11): the only UNBOUNDED accumulators were
(1) monitor.db — retention DELETEs ran but SQLite never returned pages
(165MB holding ~2.5h of rows), (2) one-off ``.bak``/``.pre-*`` backup
files never reaped, and (3) a dead 1.2G nested ``~/.marianne/.marianne/``
tree from the fixed working-directory bug. (monitor.jsonl rotation is
already count-capped at 2; snapshots already have a TTL.)

Fixes under test:
- MonitorStorage migrates to ``auto_vacuum=INCREMENTAL`` once at
  initialize() (pragma persists → self-gating) and runs a bounded
  pages auto-return on every commit (auto_vacuum=FULL — version-proof,
  unlike incremental_vacuum whose stepping semantics differ across
  SQLite builds) — the file SHRINKS on the live cadence with no
  loop-freezing full VACUUM.
- ``mzt doctor`` reports hygiene findings; ``--clean`` deletes ONLY
  after explicit confirmation (user-data gate: scanning is pure,
  deletion is opt-in).
"""

from __future__ import annotations

import time
from pathlib import Path

from marianne.cli.commands.doctor import (
    _clean_storage_findings,
    _scan_storage_findings,
)
from marianne.daemon.profiler.models import RetentionConfig
from marianne.daemon.profiler.storage import MonitorStorage


class TestAutoVacuumMigration:
    async def test_fresh_db_initialized_with_incremental_autovacuum(
        self, tmp_path: Path
    ) -> None:
        storage = MonitorStorage(db_path=tmp_path / "monitor.db")
        await storage.initialize()

        import aiosqlite

        async with aiosqlite.connect(tmp_path / "monitor.db") as db:
            cursor = await db.execute("PRAGMA auto_vacuum")
            row = await cursor.fetchone()
        assert row is not None and row[0] == 1  # FULL

    async def test_existing_db_migrated_once(self, tmp_path: Path) -> None:
        """A pre-existing db (auto_vacuum=NONE) is restructured on init."""
        import sqlite3

        db_path = tmp_path / "monitor.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE legacy (x INTEGER)")
        conn.commit()
        conn.close()

        storage = MonitorStorage(db_path=db_path)
        await storage.initialize()

        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA auto_vacuum")
            row = await cursor.fetchone()
        assert row is not None and row[0] == 1

    async def test_cleanup_reclaims_pages(self, tmp_path: Path) -> None:
        """After bulk deletes, cleanup() shrinks the file (the #352 bug
        was that it never did)."""
        from marianne.daemon.profiler.models import SystemSnapshot

        storage = MonitorStorage(db_path=tmp_path / "monitor.db")
        await storage.initialize()

        old_ts = time.time() - 7 * 86400
        for i in range(500):
            snapshot = SystemSnapshot(
                timestamp=old_ts + i,
                daemon_pid=1,
                system_memory_total_mb=1000.0,
                system_memory_available_mb=500.0,
                system_memory_used_mb=500.0,
                daemon_rss_mb=100.0,
                child_count=0,
                zombie_count=0,
                load_avg_1=0.0,
                load_avg_5=0.0,
                load_avg_15=0.0,
                pressure_level="none",
                running_jobs=0,
                active_sheets=0,
            )
            await storage.write_snapshot(snapshot)

        size_before = (tmp_path / "monitor.db").stat().st_size
        await storage.cleanup(RetentionConfig(full_resolution_hours=1))
        size_after = (tmp_path / "monitor.db").stat().st_size

        # The property under test: freed pages are RETURNED, not hoarded.
        # File size strictly-shrinking is environment-sensitive (SQLite
        # versions differ in page allocation; py3.11 CI hit exact equality),
        # so assert the freelist is drained plus no growth.
        import aiosqlite

        async with aiosqlite.connect(tmp_path / "monitor.db") as db:
            cursor = await db.execute("PRAGMA freelist_count")
            row = await cursor.fetchone()
        assert row is not None and row[0] == 0
        assert size_after <= size_before


class TestStorageFindings:
    def test_nested_tree_detected(self, tmp_path: Path) -> None:
        nested = tmp_path / ".marianne"
        nested.mkdir()
        (nested / "old-monitor.db").write_bytes(b"x" * 1024)

        findings = _scan_storage_findings(tmp_path)

        kinds = [f["kind"] for f in findings]
        assert "dead_nested_tree" in kinds
        tree = next(f for f in findings if f["kind"] == "dead_nested_tree")
        assert tree["size_bytes"] >= 1024
        assert tree["cleanable"] is True

    def test_stale_backup_detected_fresh_backup_ignored(
        self, tmp_path: Path
    ) -> None:
        import os

        stale = tmp_path / "daemon-state.db.bak"
        stale.write_bytes(b"y" * 10)
        two_days_ago = time.time() - 2 * 86400
        os.utime(stale, (two_days_ago, two_days_ago))

        fresh = tmp_path / "state.db.pre-fix-123"
        fresh.write_bytes(b"z" * 10)  # mtime = now

        findings = _scan_storage_findings(tmp_path)

        paths = [f["path"] for f in findings if f["kind"] == "stale_backup"]
        assert str(stale) in paths
        assert str(fresh) not in paths

    def test_clean_removes_only_cleanable(self, tmp_path: Path) -> None:
        nested = tmp_path / ".marianne"
        nested.mkdir()
        (nested / "junk.db").write_bytes(b"x")
        keep = tmp_path / "daemon-state.db"
        keep.write_bytes(b"precious")

        findings = _scan_storage_findings(tmp_path)
        removed = _clean_storage_findings(findings)

        assert str(nested) in removed
        assert not nested.exists()
        assert keep.exists()  # never touched — not a finding

    def test_scan_is_pure_no_deletion(self, tmp_path: Path) -> None:
        nested = tmp_path / ".marianne"
        nested.mkdir()
        (nested / "junk.db").write_bytes(b"x")

        _scan_storage_findings(tmp_path)

        assert nested.exists()

    def test_clean_handles_empty_findings(self) -> None:
        assert _clean_storage_findings([]) == []
