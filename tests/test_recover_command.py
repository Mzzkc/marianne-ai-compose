"""Tests for the Mozart recover CLI command.

Tests cover:
- State file discovery (workspace vs search paths)
- Dry-run mode (no state modification)
- Sheet recovery (FAILED → COMPLETED transitions)
- Validation pass/fail handling
- Job status updates after recovery
- Error paths (missing state, no config snapshot, no failed sheets)
"""

import json
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


def _make_state(
    job_id: str,
    workspace: Path,
    *,
    total_sheets: int = 3,
    failed_sheets: list[int] | None = None,
    completed_sheets: list[int] | None = None,
    include_config: bool = True,
) -> CheckpointState:
    """Create a CheckpointState with specified sheet statuses."""
    failed_sheets = failed_sheets or []
    completed_sheets = completed_sheets or []

    sheets: dict[int, SheetState] = {}
    for i in range(1, total_sheets + 1):
        if i in failed_sheets:
            status = SheetStatus.FAILED
        elif i in completed_sheets:
            status = SheetStatus.COMPLETED
        else:
            status = SheetStatus.PENDING
        sheets[i] = SheetState(sheet_num=i, status=status)

    last_completed = max(completed_sheets) if completed_sheets else 0

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
        last_completed_sheet=last_completed,
        status=JobStatus.FAILED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        sheets=sheets,
        config_snapshot=config_snapshot,
    )


def _write_state(state: CheckpointState, directory: Path) -> Path:
    """Write state to a JSON file and return the path."""
    state_file = directory / f"{state.job_id}.json"
    state_file.write_text(json.dumps(state.model_dump(mode="json"), default=str))
    return state_file


@pytest.fixture(autouse=True)
def _no_daemon_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent CLI tests from routing through a live conductor."""
    async def _fake_route(
        method: str, params: dict, *, socket_path=None,
    ) -> tuple[bool, None]:
        return False, None

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route", _fake_route,
    )


class TestRecoverCommand:
    """Tests for the `mozart recover` command."""

    def test_recover_nonexistent_job(self, tmp_path: Path) -> None:
        """Recover exits with error when job state not found."""
        result = runner.invoke(
            app, ["recover", "ghost-job", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_recover_no_failed_sheets(self, tmp_path: Path) -> None:
        """Recover exits cleanly when no sheets are failed."""
        state = _make_state(
            "all-good",
            tmp_path,
            total_sheets=2,
            completed_sheets=[1, 2],
        )
        state.status = JobStatus.COMPLETED
        _write_state(state, tmp_path)

        result = runner.invoke(
            app, ["recover", "all-good", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "no failed sheets" in result.stdout.lower()

    def test_recover_no_config_snapshot(self, tmp_path: Path) -> None:
        """Recover exits with error when state has no config snapshot."""
        state = _make_state(
            "no-config",
            tmp_path,
            failed_sheets=[1],
            include_config=False,
        )
        _write_state(state, tmp_path)

        result = runner.invoke(
            app, ["recover", "no-config", "--workspace", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "config snapshot" in result.stdout.lower()

    def test_recover_dry_run_does_not_modify_state(self, tmp_path: Path) -> None:
        """Dry-run checks validations but doesn't change state file."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        state = _make_state(
            "dry-test",
            workspace,
            total_sheets=2,
            failed_sheets=[1],
            completed_sheets=[2],
        )
        state_file = _write_state(state, tmp_path)

        # Create the output file so validation passes
        (workspace / "output-1.txt").write_text("done")

        original_content = state_file.read_text()

        result = runner.invoke(
            app,
            ["recover", "dry-test", "--workspace", str(tmp_path), "--dry-run"],
        )
        assert result.exit_code == 0
        assert "dry run" in result.stdout.lower() or "dry-run" in result.stdout.lower()

        # State file should NOT have changed
        assert state_file.read_text() == original_content

    def test_recover_successful_recovery(self, tmp_path: Path) -> None:
        """Recover marks sheet as completed when validations pass."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        state = _make_state(
            "recover-me",
            workspace,
            total_sheets=2,
            failed_sheets=[1, 2],
        )
        _write_state(state, tmp_path)

        # Create output files so validations pass
        (workspace / "output-1.txt").write_text("result 1")
        (workspace / "output-2.txt").write_text("result 2")

        result = runner.invoke(
            app,
            ["recover", "recover-me", "--workspace", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "recovered" in result.stdout.lower()

        # Verify state was updated
        updated = json.loads((tmp_path / "recover-me.json").read_text())
        assert updated["sheets"]["1"]["status"] == "completed"
        assert updated["sheets"]["2"]["status"] == "completed"
        assert updated["status"] == "completed"

    def test_recover_specific_sheet(self, tmp_path: Path) -> None:
        """Recover only the specified sheet, not all failed sheets."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        state = _make_state(
            "specific",
            workspace,
            total_sheets=3,
            failed_sheets=[1, 2, 3],
        )
        _write_state(state, tmp_path)

        # Only create output for sheet 2
        (workspace / "output-2.txt").write_text("done")

        result = runner.invoke(
            app,
            ["recover", "specific", "--workspace", str(tmp_path), "--sheet", "2"],
        )
        assert result.exit_code == 0

        updated = json.loads((tmp_path / "specific.json").read_text())
        assert updated["sheets"]["2"]["status"] == "completed"
        # Sheet 1 and 3 should still be failed
        assert updated["sheets"]["1"]["status"] == "failed"
        assert updated["sheets"]["3"]["status"] == "failed"
        # Job should transition to PAUSED (not COMPLETED) since sheets remain failed
        assert updated["status"] == "paused"

    def test_recover_validation_failure(self, tmp_path: Path) -> None:
        """Recover reports failure when validations don't pass."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        state = _make_state(
            "fail-val",
            workspace,
            total_sheets=1,
            failed_sheets=[1],
        )
        _write_state(state, tmp_path)

        # Don't create the output file — validation will fail

        result = runner.invoke(
            app,
            ["recover", "fail-val", "--workspace", str(tmp_path)],
        )
        assert result.exit_code == 0
        lower_out = result.stdout.lower()
        assert "cannot recover" in lower_out or "no sheets could be recovered" in lower_out

        # State should not change
        updated = json.loads((tmp_path / "fail-val.json").read_text())
        assert updated["sheets"]["1"]["status"] == "failed"

    def test_recover_updates_last_completed_sheet(self, tmp_path: Path) -> None:
        """Recovery extends last_completed_sheet when higher sheets are recovered."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        state = _make_state(
            "extend",
            workspace,
            total_sheets=3,
            completed_sheets=[1],
            failed_sheets=[2, 3],
        )
        _write_state(state, tmp_path)

        # Create output for sheet 3 but not 2
        (workspace / "output-3.txt").write_text("done")

        result = runner.invoke(
            app,
            ["recover", "extend", "--workspace", str(tmp_path), "--sheet", "3"],
        )
        assert result.exit_code == 0

        updated = json.loads((tmp_path / "extend.json").read_text())
        assert updated["sheets"]["3"]["status"] == "completed"
        assert updated["last_completed_sheet"] == 3
