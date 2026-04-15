"""Tests for recover command loading from DB directly.

GH#170 (to be filed): `mzt recover` routes through the live conductor,
which only knows active jobs. Completed/failed jobs that were deregistered
aren't reachable. The fix: always load from the SQLite DB.

TDD: Tests written before implementation. Red first, then green.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.checkpoint import (
    CheckpointState,
    JobStatus,
    SheetState,
    SheetStatus,
)

runner = CliRunner()


def _make_checkpoint(
    job_id: str,
    workspace: Path,
    *,
    total_sheets: int = 3,
    failed_sheets: list[int] | None = None,
    completed_sheets: list[int] | None = None,
    include_config: bool = True,
) -> CheckpointState:
    """Create a CheckpointState for testing."""
    failed_sheets = failed_sheets or []
    completed_sheets = completed_sheets or []

    sheets: dict[int, SheetState] = {}
    for i in range(1, total_sheets + 1):
        if i in failed_sheets:
            sheets[i] = SheetState(sheet_num=i, status=SheetStatus.FAILED)
        elif i in completed_sheets:
            sheets[i] = SheetState(sheet_num=i, status=SheetStatus.COMPLETED)
        else:
            sheets[i] = SheetState(sheet_num=i, status=SheetStatus.PENDING)

    config_snapshot = None
    if include_config:
        config_snapshot = {
            "name": job_id,
            "backend": {"type": "claude_cli", "skip_permissions": True},
            "sheet": {"size": 10, "total_items": 30},
            "prompt": {"template": "Sheet {{ sheet_num }}"},
            "workspace": str(workspace),
            "validations": [
                {
                    "type": "file_exists",
                    "path": str(workspace / "output-{sheet_num}.txt"),
                    "description": "Output file exists",
                },
            ],
        }

    return CheckpointState(
        job_id=job_id,
        job_name=job_id,
        total_sheets=total_sheets,
        last_completed_sheet=max(completed_sheets) if completed_sheets else 0,
        status=JobStatus.FAILED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        sheets=sheets,
        config_snapshot=config_snapshot,
    )


def _create_db(db_path: Path, checkpoints: list[CheckpointState]) -> None:
    """Create a SQLite DB with job checkpoints."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobs ("
        "  job_id TEXT PRIMARY KEY,"
        "  status TEXT,"
        "  checkpoint_json TEXT"
        ")"
    )
    for cp in checkpoints:
        conn.execute(
            "INSERT INTO jobs (job_id, status, checkpoint_json) VALUES (?, ?, ?)",
            (cp.job_id, cp.status.value, cp.model_dump_json()),
        )
    conn.commit()
    conn.close()


@pytest.fixture()
def _mock_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provide a temp DB path and prevent conductor routing."""
    db_path = tmp_path / "daemon-state.db"

    # Mock the DB path used by recover.
    # commands/__init__.py re-exports recover as function, shadowing the module.
    # Use sys.modules to get the actual module.
    import sys
    recover_mod = sys.modules["marianne.cli.commands.recover"]
    monkeypatch.setattr(recover_mod, "_get_db_path", lambda: db_path)

    # Prevent live conductor routing
    async def _fake_route(method, params, **kw):
        from marianne.daemon.exceptions import JobSubmissionError
        raise JobSubmissionError(f"Job not found: {params.get('job_id')}")

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route", _fake_route,
    )

    return db_path


class TestRecoverFromDB:
    """Test that recover loads from the DB when conductor doesn't have the job."""

    def test_recover_finds_job_in_db(
        self, tmp_path: Path, _mock_db: Path,
    ) -> None:
        """Recover loads a completed/failed job from DB when conductor doesn't have it."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "output-1.txt").write_text("done")

        cp = _make_checkpoint(
            "old-job", workspace, total_sheets=2,
            failed_sheets=[1], completed_sheets=[2],
        )
        _create_db(_mock_db, [cp])

        result = runner.invoke(app, ["recover", "old-job"])
        assert result.exit_code == 0
        assert "recovered" in result.stdout.lower() or "completed" in result.stdout.lower()

    def test_recover_nonexistent_job_in_db(
        self, tmp_path: Path, _mock_db: Path,
    ) -> None:
        """Recover shows error when job is not in DB either."""
        _create_db(_mock_db, [])

        result = runner.invoke(app, ["recover", "ghost-job"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_recover_dry_run_no_db_modification(
        self, tmp_path: Path, _mock_db: Path,
    ) -> None:
        """Dry-run reads from DB but doesn't write back."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "output-1.txt").write_text("done")

        cp = _make_checkpoint(
            "dry-test", workspace, total_sheets=1, failed_sheets=[1],
        )
        _create_db(_mock_db, [cp])

        result = runner.invoke(app, ["recover", "dry-test", "--dry-run"])
        assert result.exit_code == 0
        assert "dry run" in result.stdout.lower() or "dry-run" in result.stdout.lower()

        # DB should still show failed
        conn = sqlite3.connect(str(_mock_db))
        row = conn.execute(
            "SELECT status FROM jobs WHERE job_id=?", ("dry-test",)
        ).fetchone()
        conn.close()
        assert row[0] == "failed"

    def test_recover_writes_back_to_db(
        self, tmp_path: Path, _mock_db: Path,
    ) -> None:
        """Successful recovery writes updated state back to DB."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "output-1.txt").write_text("done")
        (workspace / "output-2.txt").write_text("done")

        cp = _make_checkpoint(
            "write-back", workspace, total_sheets=2, failed_sheets=[1, 2],
        )
        _create_db(_mock_db, [cp])

        result = runner.invoke(app, ["recover", "write-back"])
        assert result.exit_code == 0

        # Check DB was updated
        conn = sqlite3.connect(str(_mock_db))
        row = conn.execute(
            "SELECT checkpoint_json, status FROM jobs WHERE job_id=?",
            ("write-back",),
        ).fetchone()
        conn.close()

        updated = json.loads(row[0])
        assert updated["sheets"]["1"]["status"] == "completed"
        assert updated["sheets"]["2"]["status"] == "completed"
        assert row[1] == "completed"

    def test_recover_no_config_snapshot_resets_to_pending(
        self, tmp_path: Path, _mock_db: Path,
    ) -> None:
        """Recover resets sheets to PENDING when config snapshot is missing."""
        cp = _make_checkpoint(
            "no-config", tmp_path, failed_sheets=[1], include_config=False,
        )
        _create_db(_mock_db, [cp])

        result = runner.invoke(app, ["recover", "no-config"])
        assert result.exit_code == 0
        assert "reset to pending" in result.stdout.lower() or "recovered" in result.stdout.lower()

        # Check DB was updated — sheet reset to pending
        conn = sqlite3.connect(str(_mock_db))
        row = conn.execute(
            "SELECT checkpoint_json FROM jobs WHERE job_id=?", ("no-config",)
        ).fetchone()
        conn.close()
        updated = json.loads(row[0])
        assert updated["sheets"]["1"]["status"] == "pending"

    def test_recover_cascade_from_sheet(
        self, tmp_path: Path, _mock_db: Path,
    ) -> None:
        """--from-sheet cascade recovery also uses the DB path helper."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        cp = _make_checkpoint(
            "cascade-test", workspace, total_sheets=3,
            completed_sheets=[1], failed_sheets=[2, 3],
        )
        _create_db(_mock_db, [cp])

        result = runner.invoke(
            app, ["recover", "cascade-test", "--from-sheet", "2"]
        )
        assert result.exit_code == 0
        assert "reset" in result.stdout.lower()

        # Check DB was updated
        conn = sqlite3.connect(str(_mock_db))
        row = conn.execute(
            "SELECT checkpoint_json FROM jobs WHERE job_id=?",
            ("cascade-test",),
        ).fetchone()
        conn.close()

        updated = json.loads(row[0])
        assert updated["sheets"]["2"]["status"] == "pending"
        assert updated["sheets"]["3"]["status"] == "pending"
