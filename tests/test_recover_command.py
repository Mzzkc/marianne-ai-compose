"""Tests for the Marianne recover CLI command.

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
    """Prevent CLI tests from routing through a live conductor.

    NOTE (F-532, Theorem M7): After F-502 workspace fallback removal, recover
    command requires conductor to be running. This fixture mocks daemon routing
    to prevent tests from touching live conductor, but tests need to be rewritten
    to provide proper conductor responses. Currently all tests in this file are
    skipped pending proper conductor mock infrastructure.
    """

    async def _fake_route(
        method: str,
        params: dict,
        *,
        socket_path=None,
    ) -> tuple[bool, None]:
        return False, None

    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        _fake_route,
    )


@pytest.mark.skip(
    reason="F-532: Recover tests need conductor mock infrastructure "
    "after F-502 workspace fallback removal"
)
class TestRecoverCommand:
    """Tests for the `mzt recover` command.

    NOTE (F-532, Theorem M7): All tests in this class are skipped because F-502
    removed workspace fallback from recover command. Tests were written assuming
    filesystem fallback behavior. After F-502, recover routes through conductor
    only. Tests need conductor mock infrastructure that returns proper job state.
    """

    def test_recover_nonexistent_job(self, tmp_path: Path, monkeypatch) -> None:
        """Recover exits with error when job state not found (F-502: conductor-only)."""
        # F-502: workspace parameter removed, command routes through conductor
        result = runner.invoke(app, ["recover", "ghost-job"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_recover_no_failed_sheets(self, tmp_path: Path, monkeypatch) -> None:
        """Recover exits cleanly when no sheets are failed (F-502: conductor-only)."""
        # F-502: workspace parameter removed, command routes through conductor
        state = _make_state(
            "all-good",
            tmp_path,
            total_sheets=2,
            completed_sheets=[1, 2],
        )
        state.status = JobStatus.COMPLETED
        _write_state(state, tmp_path)

        result = runner.invoke(app, ["recover", "all-good"])
        assert result.exit_code == 0
        assert "no failed sheets" in result.stdout.lower()

    def test_recover_no_config_snapshot(self, tmp_path: Path, monkeypatch) -> None:
        """Recover exits with error when state has no config snapshot (F-502: conductor-only)."""
        # F-502: workspace parameter removed, command routes through conductor
        state = _make_state(
            "no-config",
            tmp_path,
            failed_sheets=[1],
            include_config=False,
        )
        _write_state(state, tmp_path)

        result = runner.invoke(app, ["recover", "no-config"])
        assert result.exit_code == 1
        assert "config snapshot" in result.stdout.lower()

    def test_recover_dry_run_does_not_modify_state(self, tmp_path: Path, monkeypatch) -> None:
        """Dry-run checks validations but doesn't change state file (F-502: conductor-only)."""
        # F-502: workspace parameter removed, command routes through conductor
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
            ["recover", "dry-test", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "dry run" in result.stdout.lower() or "dry-run" in result.stdout.lower()

        # State file should NOT have changed
        assert state_file.read_text() == original_content

    def test_recover_successful_recovery(self, tmp_path: Path, monkeypatch) -> None:
        """Recover marks sheet as completed when validations pass (F-502: conductor-only)."""
        # F-502: workspace parameter removed, command routes through conductor
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
            ["recover", "recover-me"],
        )
        assert result.exit_code == 0
        assert "recovered" in result.stdout.lower()

        # Verify state was updated
        updated = json.loads((tmp_path / "recover-me.json").read_text())
        assert updated["sheets"]["1"]["status"] == "completed"
        assert updated["sheets"]["2"]["status"] == "completed"
        assert updated["status"] == "completed"

    def test_recover_specific_sheet(self, tmp_path: Path, monkeypatch) -> None:
        """Recover only the specified sheet, not all failed sheets (F-502: conductor-only)."""
        # F-502: workspace parameter removed, command routes through conductor
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
            ["recover", "specific", "--sheet", "2"],
        )
        assert result.exit_code == 0

        updated = json.loads((tmp_path / "specific.json").read_text())
        assert updated["sheets"]["2"]["status"] == "completed"
        # Sheet 1 and 3 should still be failed
        assert updated["sheets"]["1"]["status"] == "failed"
        assert updated["sheets"]["3"]["status"] == "failed"
        # Job should transition to PAUSED (not COMPLETED) since sheets remain failed
        assert updated["status"] == "paused"

    def test_recover_validation_failure(self, tmp_path: Path, monkeypatch) -> None:
        """Recover reports failure when validations don't pass (F-502: conductor-only)."""
        # F-502: workspace parameter removed, command routes through conductor
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
            ["recover", "fail-val"],
        )
        assert result.exit_code == 0
        lower_out = result.stdout.lower()
        assert "cannot recover" in lower_out or "no sheets could be recovered" in lower_out

        # State should not change
        updated = json.loads((tmp_path / "fail-val.json").read_text())
        assert updated["sheets"]["1"]["status"] == "failed"

    def test_recover_updates_last_completed_sheet(self, tmp_path: Path, monkeypatch) -> None:
        """Recovery extends last_completed_sheet when higher sheets
        are recovered (F-502: conductor-only)."""
        # F-502: workspace parameter removed, command routes through conductor
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
            ["recover", "extend", "--sheet", "3"],
        )
        assert result.exit_code == 0

        updated = json.loads((tmp_path / "extend.json").read_text())
        assert updated["sheets"]["3"]["status"] == "completed"
        assert updated["last_completed_sheet"] == 3


class TestLoadRecoveryConfig:
    """Unit tests for recover._load_recovery_config (#229).

    Deserialization failures must NOT be silently swallowed: recovery proceeds
    without validation, so a degraded recovery has to leave a diagnostic trail
    (MN-007 forbids bare ``except Exception: pass``). These tests run directly
    against the helper because the CLI-level recover tests above are skipped
    pending conductor-mock infrastructure (F-532).
    """

    @staticmethod
    def _valid_snapshot(workspace: Path) -> dict:
        return {
            "name": "ok-job",
            "backend": {"type": "claude_cli", "skip_permissions": True},
            "sheet": {"size": 10, "total_items": 30},
            "prompt": {"template": "Sheet {{ sheet_num }}"},
            "workspace": str(workspace),
        }

    def test_valid_snapshot_returns_config(self, tmp_path: Path) -> None:
        from marianne.cli.commands.recover import _load_recovery_config

        config = _load_recovery_config(self._valid_snapshot(tmp_path), None)
        assert config is not None
        assert config.name == "ok-job"

    def test_invalid_snapshot_returns_none_and_warns(self) -> None:
        from unittest.mock import patch

        from marianne.cli.commands.recover import _load_recovery_config

        with patch("marianne.cli.commands.recover._logger") as logger:
            config = _load_recovery_config({"this": "is not a valid JobConfig"}, None)

        assert config is None
        logger.warning.assert_called_once()
        assert logger.warning.call_args[0][0] == "recover.config_snapshot_invalid"

    def test_no_config_returns_none_without_warning(self) -> None:
        from unittest.mock import patch

        from marianne.cli.commands.recover import _load_recovery_config

        with patch("marianne.cli.commands.recover._logger") as logger:
            config = _load_recovery_config(None, None)

        assert config is None
        logger.warning.assert_not_called()

    def test_invalid_config_path_returns_none_and_warns(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from marianne.cli.commands.recover import _load_recovery_config

        bad = tmp_path / "broken.yaml"
        bad.write_text("{ this is not: valid: yaml: ::")

        with patch("marianne.cli.commands.recover._logger") as logger:
            config = _load_recovery_config(None, str(bad))

        assert config is None
        logger.warning.assert_called_once()
        assert logger.warning.call_args[0][0] == "recover.config_path_invalid"

    def test_missing_config_path_file_returns_none(self, tmp_path: Path) -> None:
        from marianne.cli.commands.recover import _load_recovery_config

        config = _load_recovery_config(None, str(tmp_path / "does-not-exist.yaml"))
        assert config is None


class TestResetSheetDataForRetry:
    """#187: `_reset_sheet_data_for_retry` restarts a recovered sheet from its
    PRIMARY instrument with a fresh budget — not the fallback it died on.

    The command-level recover tests are skipped (F-532 conductor-mock debt), so
    the reset contract is verified directly on the extracted pure helper.
    """

    def test_resets_index_budgets_and_clears_error(self) -> None:
        from marianne.cli.commands.recover import _reset_sheet_data_for_retry

        sdata: dict = {
            "status": "failed",
            "error_message": "boom",
            "error_code": "E500",
            "completed_at": "2026-01-01T00:00:00",
            "normal_attempts": 3,
            "completion_attempts": 2,
            "attempt_count": 5,
            "healing_attempts": 1,
            "current_instrument_index": 2,  # died on the 2nd fallback
            "instrument_fallback_history": [
                {"from": "claude-code", "to": "gemini-cli",
                 "reason": "rate_limit", "timestamp": "T"},
            ],
            "fallback_attempts": {"claude-code": 3},
        }

        _reset_sheet_data_for_retry(sdata)

        assert sdata["status"] == "pending"
        assert sdata["current_instrument_index"] == 0  # #187: back to primary
        assert sdata["normal_attempts"] == 0
        assert sdata["completion_attempts"] == 0
        assert sdata["attempt_count"] == 0
        assert sdata["healing_attempts"] == 0
        assert "error_message" not in sdata
        assert "error_code" not in sdata
        assert "completed_at" not in sdata
        # #190: stale fallback record cleared
        assert sdata["instrument_fallback_history"] == []
        assert sdata["fallback_attempts"] == {}

    def test_tolerates_missing_optional_keys(self) -> None:
        from marianne.cli.commands.recover import _reset_sheet_data_for_retry

        sdata: dict = {"status": "skipped", "current_instrument_index": 1}
        _reset_sheet_data_for_retry(sdata)  # no error_*/completed_at present

        assert sdata["status"] == "pending"
        assert sdata["current_instrument_index"] == 0
