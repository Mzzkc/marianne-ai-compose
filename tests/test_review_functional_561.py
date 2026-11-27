"""Independent functional review of the shared submission path (GH #561)."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.cli.commands import agents_cmd

run_module = importlib.import_module("marianne.cli.commands.run")


@pytest.fixture
def score(tmp_path: Path) -> Path:
    path = tmp_path / "score.yaml"
    path.write_text("name: review\nsheet: {size: 1, total_items: 1}\nprompt: {template: hi}\n")
    return path


@pytest.mark.parametrize("status", ["accepted", "pending"])
def test_noisy_submission_returns_job_without_reading_stdout(score: Path, status: str) -> None:
    async def route(method: str, params: dict) -> tuple:
        assert method == "job.submit"
        assert params["escalation"] is True
        print("not JSON: diagnostic emitted during submission")
        return True, {"job_id": "review-job", "status": status}

    poll = AsyncMock(return_value=None)
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=route)),
        patch.object(run_module, "await_early_failure", new=poll),
    ):
        result = agents_cmd._submit_score(score, "review-job")
    assert result.ok and result.job_id == "review-job"
    if status == "pending":
        poll.assert_not_awaited()


def test_fresh_submission_does_not_poll_previous_failure(score: Path) -> None:
    poll = AsyncMock(return_value={"status": "failed", "error_message": "old failure"})
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(return_value=(
            True, {"job_id": "review-job", "status": "accepted"}
        ))),
        patch.object(run_module, "await_early_failure", new=poll),
    ):
        result = asyncio.run(run_module.submit_score(score, fresh=True))
    assert result.status is run_module.SubmitStatus.ACCEPTED
    poll.assert_not_awaited()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize("error_length", [10, 1000])
def test_early_failure_always_keeps_job_and_actionable_diagnosis(
    score: Path, status: str, error_length: int
) -> None:
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(return_value=(
            True, {"job_id": "review-job", "status": "accepted"}
        ))),
        patch.object(run_module, "await_early_failure", new=AsyncMock(return_value={
            "job_id": "review-job", "status": status, "error_message": "E" * error_length,
        })),
    ):
        result = agents_cmd._submit_score(score, "review-job")
    assert not result.ok and result.job_exists
    assert len(result.hint) <= 300
    assert "mzt diagnose review-job" in result.hint


@pytest.mark.parametrize("invalid", ["name: [", "name: review\nsheet: {size: 1}\n"])
def test_shared_submission_validates_score_before_touching_conductor(
    score: Path, invalid: str
) -> None:
    score.write_text(invalid)
    available = AsyncMock(return_value=True)
    route = AsyncMock(return_value=(True, {"job_id": "review-job", "status": "accepted"}))
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=available),
        patch("marianne.daemon.detect.try_daemon_route", new=route),
        patch.object(run_module, "await_early_failure", new=AsyncMock(return_value=None)),
    ):
        result = agents_cmd._submit_score(score, "review-job")
    assert not result.ok, "the shared validate-and-submit function accepted an invalid score"
    assert result.hint
    available.assert_not_awaited()
    route.assert_not_awaited()


def test_bad_id_does_not_even_probe_conductor(score: Path) -> None:
    available = AsyncMock(return_value=True)
    with patch("marianne.daemon.detect.is_daemon_available", new=available):
        result = agents_cmd._submit_score(score, "bad id!")
    assert not result.ok and not result.job_exists and "bad id!" in result.hint
    available.assert_not_awaited()


def test_programming_error_is_not_silently_reported_as_submit_refusal(score: Path) -> None:
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(
            side_effect=RuntimeError("unexpected programming error")
        )),
        pytest.raises(RuntimeError, match="unexpected programming error"),
    ):
        agents_cmd._submit_score(score, "review-job")


def test_fresh_rejection_retains_existing_clear_hint(score: Path) -> None:
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(return_value=(
            True, {"status": "rejected", "message": "score already running"}
        ))),
    ):
        result = CliRunner().invoke(app, ["run", str(score), "--fresh"])
    assert result.exit_code == 1, result.output
    assert "mzt clear --score <id>" in result.output
