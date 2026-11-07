"""GH #505: ``mzt run --job-id`` names a run of a shipped score.

One shipped score must serve two commissions side by side without a renamed
copy. The CLI sends the name; the conductor uses it as the job ID and, when
the score omits ``workspace:``, derives the auto-managed workspace from it.
An explicit ``workspace:`` in the score still wins.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config import JobConfig
from marianne.core.config.workspace import WORKSPACE_ROOT_ENV
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import JobManager
from marianne.daemon.types import JobRequest

runner = CliRunner()

SCORE = (
    "name: shipped\n"
    "instrument: cli\n"
    "sheet:\n  size: 1\n  total_items: 1\n"
    "prompt:\n  template: sleep 30\n"
)


@pytest.fixture
def managed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "managed"
    monkeypatch.setenv(WORKSPACE_ROOT_ENV, str(root))
    return root


class TestLoaderRecordsDerivedWorkspace:
    def test_omitted_workspace_is_auto_managed(self, tmp_path: Path, managed_root: Path) -> None:
        score = tmp_path / "shipped.yaml"
        score.write_text(SCORE)
        config = JobConfig.from_yaml(score)
        assert config.workspace_auto_managed is True
        assert config.workspace == managed_root / "shipped"

    def test_explicit_workspace_is_not_auto_managed(
        self, tmp_path: Path, managed_root: Path
    ) -> None:
        score = tmp_path / "shipped.yaml"
        score.write_text(SCORE + "workspace: ./ws\n")
        config = JobConfig.from_yaml(score)
        assert config.workspace_auto_managed is False
        assert config.workspace == tmp_path / "ws"

    def test_bytes_and_string_loaders_agree(self, tmp_path: Path, managed_root: Path) -> None:
        score = tmp_path / "shipped.yaml"
        score.write_text(SCORE)
        assert JobConfig.from_yaml_bytes(
            SCORE.encode(), source_path=score
        ).workspace_auto_managed is True
        assert JobConfig.from_yaml_string(SCORE).workspace_auto_managed is True
        assert JobConfig.from_yaml_string(
            SCORE + "workspace: /tmp/x\n"
        ).workspace_auto_managed is False


class TestCliSendsJobId:
    def _submit(self, args: list[str]) -> dict[str, object]:
        route = AsyncMock(return_value=(True, {"job_id": "x", "status": "accepted"}))
        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("marianne.daemon.detect.try_daemon_route", route),
            patch(
                "marianne.cli.commands.run.await_early_failure",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = runner.invoke(app, ["run", *args])
        assert result.exit_code == 0, result.output
        method, params = route.call_args.args
        assert method == "job.submit"
        assert isinstance(params, dict)
        return params

    def test_job_id_is_sent(self, tmp_path: Path, managed_root: Path) -> None:
        score = tmp_path / "shipped.yaml"
        score.write_text(SCORE)
        params = self._submit([str(score), "--job-id", "commission-b"])
        assert params["job_id"] == "commission-b"

    def test_no_job_id_sends_none(self, tmp_path: Path, managed_root: Path) -> None:
        score = tmp_path / "shipped.yaml"
        score.write_text(SCORE)
        params = self._submit([str(score)])
        assert "job_id" not in params

    def test_invalid_job_id_is_refused_before_submit(
        self, tmp_path: Path, managed_root: Path
    ) -> None:
        score = tmp_path / "shipped.yaml"
        score.write_text(SCORE)
        route = AsyncMock()
        with patch("marianne.daemon.detect.try_daemon_route", route):
            result = runner.invoke(app, ["run", str(score), "--job-id", "../escape"])
        assert result.exit_code != 0
        assert "Invalid score ID" in result.output
        route.assert_not_called()


@pytest.mark.asyncio
async def test_two_named_runs_of_one_score_get_distinct_ids_and_workspaces(
    tmp_path: Path, managed_root: Path
) -> None:
    manager = JobManager(
        DaemonConfig(pid_file=tmp_path / "c.pid", state_db_path=tmp_path / "jobs.db")
    )
    await manager.start()
    try:
        score = tmp_path / "shipped.yaml"
        score.write_text(SCORE)
        first = await manager.submit_job(JobRequest(config_path=score, job_id="commission-a"))
        second = await manager.submit_job(JobRequest(config_path=score, job_id="commission-b"))
        assert first.status == "accepted", first.message
        assert second.status == "accepted", second.message
        assert {first.job_id, second.job_id} == {"commission-a", "commission-b"}
        assert manager._job_meta["commission-a"].workspace == managed_root / "commission-a"
        assert manager._job_meta["commission-b"].workspace == managed_root / "commission-b"

        # An explicit workspace: in the score wins over the run's name.
        pinned = tmp_path / "pinned.yaml"
        pinned.write_text(SCORE.replace("shipped", "pinned") + "workspace: ./pinned-ws\n")
        third = await manager.submit_job(JobRequest(config_path=pinned, job_id="commission-c"))
        assert third.status == "accepted", third.message
        assert manager._job_meta["commission-c"].workspace == tmp_path / "pinned-ws"

        # Without --job-id nothing changes: the filename stem and the
        # score-name workspace, exactly as before.
        plain = tmp_path / "plain.yaml"
        plain.write_text(SCORE.replace("shipped", "plain"))
        fourth = await manager.submit_job(JobRequest(config_path=plain))
        assert fourth.job_id == "plain"
        assert manager._job_meta["plain"].workspace == managed_root / "plain"
    finally:
        await manager.shutdown(graceful=False)
