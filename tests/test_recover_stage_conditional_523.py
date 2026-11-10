"""#523: `mzt recover` must not complete a sheet whose validations did not apply.

Recover built its validation context by hand (sheet_num and the prompt
variables). Execution builds it from ``Sheet.template_variables()``, which
also carries ``stage``, ``instance``, ``movement``. Every validation
written ``condition: "stage == N"`` was therefore not applicable during
recovery, zero applicable validations read as "all passed", and a
cascade-skipped sheet whose output never existed became COMPLETED.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus

runner = CliRunner()


def _checkpoint(
    job_id: str, workspace: Path, statuses: dict[int, SheetStatus], validations: list[dict]
) -> CheckpointState:
    return CheckpointState(
        job_id=job_id,
        job_name=job_id,
        total_sheets=len(statuses),
        status=JobStatus.FAILED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        sheets={n: SheetState(sheet_num=n, status=s) for n, s in statuses.items()},
        config_snapshot={
            "name": job_id,
            "workspace": str(workspace),
            "instrument": "claude-code",
            "sheet": {"size": 1, "total_items": len(statuses)},
            "prompt": {"template": "Sheet {{ sheet_num }}"},
            "validations": validations,
        },
    )


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "daemon-state.db"
    monkeypatch.setattr(
        sys.modules["marianne.cli.commands.recover"], "_get_db_path", lambda: db_path
    )

    async def _fake_route(method, params, **kw):  # noqa: ANN001, ANN003
        from marianne.daemon.exceptions import JobSubmissionError

        raise JobSubmissionError(f"Job not found: {params.get('job_id')}")

    monkeypatch.setattr("marianne.daemon.detect.try_daemon_route", _fake_route)
    return db_path


def _store(db_path: Path, cp: CheckpointState) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT, checkpoint_json TEXT)")
    conn.execute(
        "INSERT INTO jobs VALUES (?, ?, ?)", (cp.job_id, cp.status.value, cp.model_dump_json())
    )
    conn.commit()
    conn.close()


def _sheets(db_path: Path, job_id: str) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT checkpoint_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    conn.close()
    return {k: v["status"] for k, v in json.loads(row[0])["sheets"].items()}


STAGE_VALIDATIONS = [
    {"type": "file_exists", "path": "{workspace}/out-1.md", "condition": "stage == 1"},
    {"type": "file_exists", "path": "{workspace}/out-2.md", "condition": "stage == 2"},
    {"type": "file_exists", "path": "{workspace}/out-3.md", "condition": "stage == 3"},
]


def test_skipped_sheets_with_stage_conditional_validations_are_not_completed(
    tmp_path: Path, db: Path
) -> None:
    """Sheet 1 failed, 2 and 3 cascade-skipped; only sheet 1's output exists."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "out-1.md").write_text("done")
    _store(
        db,
        _checkpoint(
            "stagey",
            ws,
            {1: SheetStatus.FAILED, 2: SheetStatus.SKIPPED, 3: SheetStatus.SKIPPED},
            STAGE_VALIDATIONS,
        ),
    )

    result = runner.invoke(app, ["recover", "stagey"])
    assert result.exit_code == 0, result.output

    assert _sheets(db, "stagey") == {"1": "completed", "2": "pending", "3": "pending"}
    assert "Validated: 1" in result.output and "Reset to PENDING: 2" in result.output


def test_stage_conditional_validation_passes_when_the_artifact_exists(
    tmp_path: Path, db: Path
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    for n in (1, 2, 3):
        (ws / f"out-{n}.md").write_text("done")
    _store(
        db,
        _checkpoint(
            "stagey-ok",
            ws,
            {1: SheetStatus.FAILED, 2: SheetStatus.SKIPPED, 3: SheetStatus.SKIPPED},
            STAGE_VALIDATIONS,
        ),
    )
    result = runner.invoke(app, ["recover", "stagey-ok"])
    assert result.exit_code == 0, result.output
    assert set(_sheets(db, "stagey-ok").values()) == {"completed"}


def test_a_sheet_no_validation_applies_to_is_reset_and_reported(tmp_path: Path, db: Path) -> None:
    """Zero applicable validations is not proof of work."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _store(
        db,
        _checkpoint(
            "vacuous",
            ws,
            {1: SheetStatus.COMPLETED, 2: SheetStatus.SKIPPED},
            [{"type": "file_exists", "path": "{workspace}/out-1.md", "condition": "stage == 1"}],
        ),
    )
    result = runner.invoke(app, ["recover", "vacuous"])
    assert result.exit_code == 0, result.output
    assert _sheets(db, "vacuous")["2"] == "pending"
    assert "no validation applies" in result.output


def test_loop_index_reaches_the_recovery_context() -> None:
    """A sheet inside an open loop validates with that loop's iteration."""
    from marianne.cli.commands.recover import _recovery_sheet_contexts
    from marianne.core.config import JobConfig

    config = JobConfig.model_validate(
        {
            "name": "loopy",
            "workspace": "/tmp/ws",
            "instrument": "claude-code",
            "sheet": {"size": 1, "total_items": 3},
            "prompt": {"template": "x"},
        }
    )
    flow = {
        "loops": {
            "2-3": {"index_name": "unit_pass", "iteration": 4, "phase": "running"},
            "1": {"index_name": "done_loop", "iteration": 2, "phase": "completed"},
        }
    }
    contexts = _recovery_sheet_contexts(config, flow)
    assert contexts[2]["unit_pass"] == 4 and contexts[3]["unit_pass"] == 4
    assert "unit_pass" not in contexts[1] and "done_loop" not in contexts[1]
    assert contexts[2]["stage"] == 2
