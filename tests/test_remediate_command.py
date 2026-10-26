"""CLI remediation behavior with isolated registry lookup and real healing."""

import importlib
import json
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus

runner = CliRunner()


@pytest.fixture
def job(monkeypatch, tmp_path):
    module = importlib.import_module("marianne.cli.commands.remediate")
    state = CheckpointState(
        job_id="repair",
        job_name="repair",
        total_sheets=1,
        status=JobStatus.FAILED,
        sheets={
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                error_code="E304",
                error_message="'sheet_nuum' is undefined",
            )
        },
    )
    backend = AsyncMock()
    monkeypatch.setattr(module, "_find_job_state_direct", AsyncMock(return_value=(state, backend)))
    return state, backend


@pytest.mark.parametrize(
    "flags,user_input,applied",
    [
        (["--yes"], "", True),
        ([], "y\n", True),
        ([], "n\n", False),
        (["--dry-run"], "", False),
    ],
)
def test_confirmation_and_json(job, flags, user_input, applied):
    result = runner.invoke(app, ["remediate", "repair", "--json", *flags], input=user_input)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    report = payload["reports"][0]
    assert bool(report["actions_taken"]) is applied
    if not applied:
        assert report["actions_skipped"]
    if user_input == "n\n":
        assert report["diagnostic_outputs"]
    job[1].close.assert_awaited_once()


def test_dry_run_automatic_no_side_effects(job, tmp_path):
    workspace = tmp_path / "new"
    # Workspace overrides may point to a directory the remedy should create.
    job[0].sheets[1].error_code = "E601"
    job[0].sheets[1].error_message = "missing workspace"
    result = runner.invoke(app, ["remediate", "repair", "-w", str(workspace), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert not workspace.exists()
    assert "Dry run" in result.output


def test_no_failures_message(job):
    job[0].sheets[1].status = SheetStatus.COMPLETED
    result = runner.invoke(app, ["remediate", "repair"])
    assert result.exit_code == 0
    assert "No failed sheets" in result.output


def test_active_job_requires_dry_run(job):
    job[0].status = JobStatus.RUNNING
    result = runner.invoke(app, ["remediate", "repair", "--yes", "--json"])
    assert result.exit_code == 1
    assert "terminal" in json.loads(result.stdout)["error"]
    job[1].close.assert_awaited_once()


def test_not_found_is_json_error(monkeypatch, tmp_path):
    monkeypatch.setattr("marianne.cli.helpers.DAEMON_STATE_DB_PATH", tmp_path / "absent.db")
    result = runner.invoke(app, ["remediate", "absent", "--json"])
    assert result.exit_code == 1
    assert "absent" in result.stdout
    json.loads(result.stdout)


def test_registered_job_resolves_without_workspace_files(monkeypatch, tmp_path):
    import asyncio

    from marianne.daemon.registry import JobRegistry

    db_path = tmp_path / "registry.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = CheckpointState(
        job_id="stored",
        job_name="stored",
        total_sheets=1,
        status=JobStatus.FAILED,
        config_snapshot={
            "name": "stored",
            "workspace": str(workspace),
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "work"},
        },
        sheets={
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                error_code="E901",
                error_message="claude command not found",
            )
        },
    )

    async def store():
        registry = JobRegistry(db_path)
        await registry.open()
        try:
            await registry.register_job("stored", tmp_path / "score.yaml", workspace)
            await registry.save_checkpoint("stored", state.model_dump_json())
        finally:
            await registry.close()

    asyncio.run(store())
    monkeypatch.setattr("marianne.cli.helpers.DAEMON_STATE_DB_PATH", db_path)
    result = runner.invoke(app, ["remediate", "stored", "--json", "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["reports"][0]["sheet_number"] == 1
    assert payload["reports"][0]["diagnostic_outputs"]
    assert payload["reports"][0]["context"]["workspace"] == str(workspace)


def test_approved_guidance_does_not_promise_retry_or_repair(job):
    result = runner.invoke(app, ["remediate", "repair", "--yes"])
    assert result.exit_code == 0, result.output
    assert "HEALED" not in result.stdout
    assert "Retrying sheet" not in result.stdout
    assert "No retry was scheduled" in result.stdout
    assert "Suggested fix" in result.stdout


def test_workspace_created_without_promising_retry(job, tmp_path):
    workspace = tmp_path / "missing"
    job[0].sheets[1].error_code = "E601"
    job[0].sheets[1].error_message = "missing workspace"
    result = runner.invoke(app, ["remediate", "repair", "-w", str(workspace)])
    assert result.exit_code == 0, result.output
    assert workspace.is_dir()
    assert "Retrying sheet" not in result.stdout
    assert "No retry was scheduled" in result.stdout
