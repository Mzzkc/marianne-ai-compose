"""``mzt history`` reads the checkpoint's attempt records (GH #488).

Before: ``job.history`` read a SQLite ``execution_history`` table that no
production code wrote, so every job answered ``has_history: false``.
After: history is a view over each sheet's persisted ``attempt_history``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.checkpoint import (
    CheckpointState,
    JobStatus,
    SheetState,
    execution_history_records,
)
from marianne.daemon.baton.events import SheetAttemptResult

runner = CliRunner()


def _attempt(sheet: int, *, ok: bool, exit_code: int, message: str = "") -> SheetAttemptResult:
    return SheetAttemptResult(
        job_id="j1",
        sheet_num=sheet,
        instrument_name="codex-cli",
        attempt=1,
        execution_success=ok,
        exit_code=exit_code,
        error_message=message or None,
        error_code=None if ok else "E001",
        duration_seconds=2.5,
    )


def _state() -> CheckpointState:
    first = SheetState(sheet_num=1)
    first.record_attempt(_attempt(1, ok=False, exit_code=1, message="boom: sk-redacted"))
    first.record_attempt(_attempt(1, ok=True, exit_code=0))
    second = SheetState(sheet_num=2)
    second.record_attempt(_attempt(2, ok=True, exit_code=0))
    return CheckpointState(
        job_id="j1", job_name="j1", total_sheets=2,
        status=JobStatus.COMPLETED, sheets={1: first, 2: second},
    )


def test_attempt_records_carry_the_exit_code() -> None:
    entries = _state().sheets[1].attempt_history
    assert [e["exit_code"] for e in entries] == [1, 0]


def test_records_are_newest_first_and_filter_by_sheet() -> None:
    rows = execution_history_records(_state())
    assert len(rows) == 3
    stamps = [r["executed_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)
    only_one = execution_history_records(_state(), sheet_num=1)
    assert {r["sheet_num"] for r in only_one} == {1}
    failed = next(r for r in only_one if r["success"] is False)
    assert failed["exit_code"] == 1 and failed["error_code"] == "E001"
    assert "boom" in failed["error_message"]


def test_limit_bounds_the_rows() -> None:
    assert len(execution_history_records(_state(), limit=2)) == 2
    assert execution_history_records(_state(), limit=0) == []


def test_records_survive_a_checkpoint_round_trip() -> None:
    original = _state()
    restored = CheckpointState.model_validate_json(original.model_dump_json())
    assert execution_history_records(restored) == execution_history_records(original)


async def test_manager_reads_history_from_job_status(tmp_path: Path) -> None:
    from marianne.daemon.manager import JobManager

    manager = JobManager.__new__(JobManager)
    status = _state().model_dump(mode="json")
    with patch.object(JobManager, "get_job_status", AsyncMock(return_value=status)):
        result: dict[str, Any] = await manager.get_execution_history("j1", sheet_num=2)
    assert result["has_history"] is True
    assert [r["sheet_num"] for r in result["records"]] == [2]


def test_cli_table_shows_instrument_outcome_and_cause() -> None:
    payload = {
        "job_id": "j1",
        "records": execution_history_records(_state()),
        "has_history": True,
    }
    with patch(
        "marianne.daemon.detect.try_daemon_route",
        new_callable=AsyncMock,
        return_value=(True, payload),
    ):
        result = runner.invoke(app, ["history", "j1"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "codex-cli" in result.output
    assert "failed" in result.output and "E001" in result.output
    assert "SQLite" not in result.output


@pytest.mark.parametrize("records", [[]])
def test_cli_empty_history_says_no_attempt_finished(records: list[Any]) -> None:
    with patch(
        "marianne.daemon.detect.try_daemon_route",
        new_callable=AsyncMock,
        return_value=(True, {"job_id": "j1", "records": records, "has_history": True}),
    ):
        result = runner.invoke(app, ["history", "j1"])
    assert result.exit_code == 0
    assert "no sheet has finished an attempt" in result.output
    assert "SQLite" not in result.output
