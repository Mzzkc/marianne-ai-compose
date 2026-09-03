"""Failure-hook lifecycle tests for conductor-managed jobs.

These tests exercise the public score configuration and the durable daemon
boundary. Hook subprocesses are real; only unrelated daemon services are
omitted from focused lifecycle fixtures.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from marianne.core.config import JobConfig
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import JobManager, JobMeta
from marianne.daemon.registry import DaemonJobStatus, JobRegistry
from marianne.daemon.types import JobRequest


def _minimal_config(**extra: object) -> dict[str, object]:
    """Return the smallest valid score mapping plus caller-owned fields."""
    config: dict[str, object] = {
        "name": "failure-hook-score",
        "sheet": {"size": 1, "total_items": 1},
        "prompt": {"template": "test prompt"},
    }
    config.update(extra)
    return config


@pytest.fixture
async def manager(tmp_path: Path) -> AsyncIterator[JobManager]:
    """Create a manager with only the registry seam active."""
    config = DaemonConfig(
        max_concurrent_jobs=2,
        state_db_path=tmp_path / "manager-registry.db",
    )
    manager = JobManager(config)
    await manager._registry.open()
    yield manager
    for task in manager._jobs.values():
        task.cancel()
    if manager._jobs:
        await asyncio.gather(*manager._jobs.values(), return_exceptions=True)
    failure_hook_tasks = getattr(manager, "_failure_hook_tasks", {})
    for task in failure_hook_tasks.values():
        task.cancel()
    if failure_hook_tasks:
        await asyncio.gather(
            *failure_hook_tasks.values(),
            return_exceptions=True,
        )
    await manager._registry.close()


async def _register_job_with_failure_hooks(
    manager: JobManager,
    tmp_path: Path,
    hooks: list[dict[str, Any]],
    *,
    job_id: str = "failed-score",
) -> JobMeta:
    workspace = tmp_path / f"{job_id}-workspace"
    workspace.mkdir(exist_ok=True)
    await manager._registry.register_job(
        job_id,
        tmp_path / f"{job_id}.yaml",
        workspace,
    )
    await manager._registry.store_failure_hook_config(job_id, json.dumps(hooks))
    meta = JobMeta(
        job_id=job_id,
        config_path=tmp_path / f"{job_id}.yaml",
        workspace=workspace,
        status=DaemonJobStatus.QUEUED,
        failure_hook_config=hooks,
    )
    manager._job_meta[job_id] = meta
    return meta


async def _wait_for_failure_hooks(manager: JobManager, job_id: str) -> None:
    """Poll durable completion so task scheduling speed is irrelevant."""
    deadline = asyncio.get_running_loop().time() + 5.0
    while asyncio.get_running_loop().time() < deadline:
        record = await manager._registry.get_job(job_id)
        if record is not None and record.failure_hooks_completed_at is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"failure hooks did not complete for {job_id}")


async def _wait_for_failure_task_settlement(manager: JobManager, job_id: str) -> None:
    """Poll manager task custody until the hook callback has settled."""
    deadline = asyncio.get_running_loop().time() + 5.0
    while asyncio.get_running_loop().time() < deadline:
        if job_id not in manager._failure_hook_tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"failure-hook task did not settle for {job_id}")


class TestFailureHookConfig:
    """The top-level on_failure list reuses the existing hook contract."""

    def test_accepts_failure_hook_actions_and_policy(self, tmp_path: Path) -> None:
        config = JobConfig.model_validate(
            _minimal_config(
                on_failure=[
                    {
                        "type": "run_script",
                        "command": str(tmp_path / "report-failure"),
                        "on_failure": "abort",
                    }
                ]
            )
        )

        assert len(config.on_failure) == 1
        assert config.on_failure[0].type == "run_script"
        assert config.on_failure[0].on_failure == "abort"

    @pytest.mark.adversarial
    def test_rejects_malformed_failure_hook(self) -> None:
        with pytest.raises(ValidationError, match="requires 'command'"):
            JobConfig.model_validate(_minimal_config(on_failure=[{"type": "run_script"}]))

    def test_scores_without_failure_hooks_remain_compatible(self) -> None:
        config = JobConfig.model_validate(_minimal_config())

        assert config.on_failure == []


class TestFailureHookRegistryLifecycle:
    """The registry owns one durable claim and completion marker per run."""

    async def test_claim_is_persistent_and_exactly_once(self, tmp_path: Path) -> None:
        registry = JobRegistry(tmp_path / "registry.db")
        await registry.open()
        try:
            await registry.register_job(
                "failed-score",
                tmp_path / "score.yaml",
                tmp_path / "workspace",
            )
            hooks = [{"type": "run_script", "command": "report-failure"}]
            await registry.store_failure_hook_config(
                "failed-score",
                json.dumps(hooks),
            )
            await registry.update_status(
                "failed-score",
                DaemonJobStatus.FAILED.value,
                error_message="sheet failed",
            )

            assert await registry.claim_failure_hooks("failed-score") is True
            assert await registry.claim_failure_hooks("failed-score") is False

            claimed = await registry.get_job("failed-score")
            assert claimed is not None
            assert claimed.failure_hooks_started_at is not None
            assert claimed.failure_hooks_completed_at is None

            await registry.complete_failure_hooks("failed-score")
            completed = await registry.get_job("failed-score")
            assert completed is not None
            assert completed.failure_hooks_started_at is not None
            assert completed.failure_hooks_completed_at is not None
        finally:
            await registry.close()


class TestFailureHookManagerLifecycle:
    """Terminal failure hooks are wired to manager status transitions."""

    async def test_submit_persists_failure_hook_config(
        self,
        manager: JobManager,
        tmp_path: Path,
    ) -> None:
        score = tmp_path / "failure-hook-score.yaml"
        score.write_text(
            "name: failure-hook-score\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: test prompt\n"
            "on_failure:\n"
            "  - type: run_script\n"
            "    command: report-failure\n"
        )

        response = await manager.submit_job(
            JobRequest(config_path=score, workspace=tmp_path / "workspace")
        )

        assert response.status == "accepted"
        meta = manager._job_meta[response.job_id]
        assert meta.failure_hook_config is not None
        assert meta.failure_hook_config[0]["type"] == "run_script"
        stored = await manager._registry.get_failure_hook_config(response.job_id)
        assert stored is not None
        assert json.loads(stored)[0]["command"] == "report-failure"

    async def test_submit_rejects_same_id_while_failure_hook_is_running(
        self,
        manager: JobManager,
        tmp_path: Path,
    ) -> None:
        release = asyncio.Event()
        hook_task = asyncio.create_task(release.wait())
        manager._failure_hook_tasks["failure-hook-score"] = hook_task
        score = tmp_path / "failure-hook-score.yaml"
        score.write_text(
            "name: failure-hook-score\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: test prompt\n"
        )

        try:
            response = await manager.submit_job(
                JobRequest(config_path=score, workspace=tmp_path / "workspace")
            )
        finally:
            release.set()
            await hook_task
            manager._failure_hook_tasks.pop("failure-hook-score", None)

        assert response.status == "rejected"
        assert "failure hooks" in (response.message or "").lower()

    async def test_non_graceful_shutdown_settles_failure_hook_tasks(
        self,
        manager: JobManager,
    ) -> None:
        never = asyncio.Event()
        task = asyncio.create_task(never.wait())
        manager._failure_hook_tasks["failure-hook-score"] = task

        await manager._settle_failure_hook_tasks(graceful=False)

        assert task.cancelled()
        assert manager._failure_hook_tasks == {}

    async def test_failed_status_runs_hook_with_deterministic_identity(
        self,
        manager: JobManager,
        tmp_path: Path,
    ) -> None:
        capture = tmp_path / "capture.py"
        output = tmp_path / "identity.txt"
        capture.write_text(
            "import os, pathlib, sys\n"
            "pathlib.Path(sys.argv[1]).write_text('|'.join((\n"
            "    sys.argv[2], sys.argv[3],\n"
            "    os.environ['MARIANNE_JOB_ID'],\n"
            "    os.environ['MARIANNE_JOB_STATUS'],\n"
            ")))\n"
        )
        command = " ".join(
            (
                shlex.quote(sys.executable),
                shlex.quote(str(capture)),
                shlex.quote(str(output)),
                "{job_id}",
                "{job_status}",
            )
        )
        meta = await _register_job_with_failure_hooks(
            manager,
            tmp_path,
            [{"type": "run_script", "command": command}],
        )

        await manager._set_job_status(
            meta.job_id,
            DaemonJobStatus.FAILED,
            error_message="sheet execution failed",
        )
        await _wait_for_failure_hooks(manager, meta.job_id)

        assert output.read_text() == "failed-score|failed|failed-score|failed"
        record = await manager._registry.get_job(meta.job_id)
        assert record is not None
        assert record.status is DaemonJobStatus.FAILED
        assert record.error_message == "sheet execution failed"
        results_json = await manager._registry.get_failure_hook_results(meta.job_id)
        assert results_json is not None
        assert json.loads(results_json)[0]["success"] is True
        status = await manager.get_job_status(meta.job_id)
        assert status["config_snapshot"]["on_failure"][0]["type"] == "run_script"
        assert status["failure_hook_results"][0]["terminal_status"] == "failed"
        assert status["failure_hook_state"]["started_at"] is not None
        assert status["failure_hook_state"]["completed_at"] is not None

    async def test_failure_hook_identity_uses_terminal_status_snapshot(
        self,
        manager: JobManager,
        tmp_path: Path,
    ) -> None:
        output = tmp_path / "status-snapshot.txt"
        script = tmp_path / "status-snapshot.py"
        script.write_text(
            "import os, pathlib, sys\n"
            "pathlib.Path(sys.argv[1]).write_text(\n"
            "    sys.argv[2] + '|' + os.environ['MARIANNE_JOB_STATUS']\n"
            ")\n"
        )
        workspace = tmp_path / "status-workspace"
        workspace.mkdir()
        meta = JobMeta(
            job_id="status-score",
            config_path=tmp_path / "status.yaml",
            workspace=workspace,
            status=DaemonJobStatus.RUNNING,
        )

        result = await manager._execute_hook_command(
            {
                "type": "run_script",
                "command": (
                    f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} "
                    f"{shlex.quote(str(output))} {{job_status}}"
                ),
            },
            meta,
            use_shell=False,
            job_status="failed",
        )

        assert result["success"] is True
        assert output.read_text() == "failed|failed"

    @pytest.mark.parametrize(
        "status",
        [
            DaemonJobStatus.COMPLETED,
            DaemonJobStatus.PAUSED,
            DaemonJobStatus.CANCELLED,
        ],
    )
    async def test_non_failed_terminal_status_does_not_run_failure_hook(
        self,
        manager: JobManager,
        tmp_path: Path,
        status: DaemonJobStatus,
    ) -> None:
        output = tmp_path / f"{status.value}.txt"
        command = " ".join(
            (
                shlex.quote(sys.executable),
                "-c",
                shlex.quote(f"from pathlib import Path; Path({str(output)!r}).touch()"),
            )
        )
        meta = await _register_job_with_failure_hooks(
            manager,
            tmp_path,
            [{"type": "run_script", "command": command}],
            job_id=f"{status.value}-score",
        )

        await manager._set_job_status(meta.job_id, status)
        await asyncio.sleep(0)

        assert not output.exists()
        record = await manager._registry.get_job(meta.job_id)
        assert record is not None
        assert record.failure_hooks_started_at is None

    @pytest.mark.parametrize(
        ("policy", "second_hook_runs"),
        [("continue", True), ("abort", False)],
    )
    async def test_hook_failure_policy_controls_remaining_hooks_without_status_change(
        self,
        manager: JobManager,
        tmp_path: Path,
        policy: str,
        second_hook_runs: bool,
    ) -> None:
        fail_script = tmp_path / f"fail-{policy}.py"
        fail_script.write_text("raise SystemExit(7)\n")
        output = tmp_path / f"second-{policy}.txt"
        second_command = " ".join(
            (
                shlex.quote(sys.executable),
                "-c",
                shlex.quote(f"from pathlib import Path; Path({str(output)!r}).touch()"),
            )
        )
        meta = await _register_job_with_failure_hooks(
            manager,
            tmp_path,
            [
                {
                    "type": "run_script",
                    "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(fail_script))}",
                    "on_failure": policy,
                },
                {"type": "run_script", "command": second_command},
            ],
            job_id=f"policy-{policy}",
        )

        await manager._set_job_status(
            meta.job_id,
            DaemonJobStatus.FAILED,
            error_message="original failure",
        )
        await _wait_for_failure_hooks(manager, meta.job_id)

        assert output.exists() is second_hook_runs
        record = await manager._registry.get_job(meta.job_id)
        assert record is not None
        assert record.status is DaemonJobStatus.FAILED
        assert record.error_message == "original failure"
        results_json = await manager._registry.get_failure_hook_results(meta.job_id)
        assert results_json is not None
        results = json.loads(results_json)
        assert len(results) == (2 if second_hook_runs else 1)
        assert results[0]["success"] is False

    async def test_existing_success_hook_path_is_unchanged(
        self,
        manager: JobManager,
        tmp_path: Path,
    ) -> None:
        output = tmp_path / "success.txt"
        command = " ".join(
            (
                shlex.quote(sys.executable),
                "-c",
                shlex.quote(f"from pathlib import Path; Path({str(output)!r}).touch()"),
            )
        )
        workspace = tmp_path / "success-workspace"
        workspace.mkdir()
        await manager._registry.register_job(
            "success-score",
            tmp_path / "success.yaml",
            workspace,
        )
        await manager._registry.update_status(
            "success-score",
            DaemonJobStatus.COMPLETED.value,
        )
        manager._job_meta["success-score"] = JobMeta(
            job_id="success-score",
            config_path=tmp_path / "success.yaml",
            workspace=workspace,
            status=DaemonJobStatus.COMPLETED,
            hook_config=[{"type": "run_script", "command": command}],
        )

        await manager._execute_hooks_task("success-score")

        assert output.exists()
        record = await manager._registry.get_job("success-score")
        assert record is not None
        assert record.status is DaemonJobStatus.COMPLETED
        assert await manager._registry.get_hook_results("success-score") is not None
        assert await manager._registry.get_failure_hook_results("success-score") is None

    @pytest.mark.adversarial
    async def test_non_failed_job_cannot_claim_failure_hooks(self, tmp_path: Path) -> None:
        registry = JobRegistry(tmp_path / "registry.db")
        await registry.open()
        try:
            await registry.register_job(
                "completed-score",
                tmp_path / "score.yaml",
                tmp_path / "workspace",
            )
            await registry.store_failure_hook_config(
                "completed-score",
                '[{"type":"run_script","command":"report-failure"}]',
            )
            await registry.update_status(
                "completed-score",
                DaemonJobStatus.COMPLETED.value,
            )

            assert await registry.claim_failure_hooks("completed-score") is False
        finally:
            await registry.close()


class TestFailureHookRestartRecovery:
    """Restart reconciliation claims unstarted hooks and never repeats claims."""

    async def test_restart_runs_unclaimed_failed_job_once(self, tmp_path: Path) -> None:
        config = DaemonConfig(state_db_path=tmp_path / "restart.db")
        output = tmp_path / "restart-output.txt"
        command = " ".join(
            (
                shlex.quote(sys.executable),
                "-c",
                shlex.quote(
                    "from pathlib import Path; "
                    f"p=Path({str(output)!r}); "
                    "p.write_text((p.read_text() if p.exists() else '') + 'run\\n')"
                ),
            )
        )
        registry = JobRegistry(config.state_db_path)
        await registry.open()
        restart_workspace = tmp_path / "restart-workspace"
        restart_workspace.mkdir()
        await registry.register_job(
            "restart-score",
            tmp_path / "restart.yaml",
            restart_workspace,
        )
        await registry.store_failure_hook_config(
            "restart-score",
            json.dumps([{"type": "run_script", "command": command}]),
        )
        await registry.update_status(
            "restart-score",
            DaemonJobStatus.FAILED.value,
            error_message="original restart failure",
        )
        await registry.close()

        restarted = JobManager(config)
        await restarted.start()
        try:
            await _wait_for_failure_hooks(restarted, "restart-score")
            await _wait_for_failure_task_settlement(restarted, "restart-score")

            assert output.read_text() == "run\n"
            assert restarted._job_meta["restart-score"].failure_hook_config is not None
        finally:
            await restarted.shutdown()

    async def test_restart_does_not_repeat_started_hook(self, tmp_path: Path) -> None:
        config = DaemonConfig(state_db_path=tmp_path / "restart.db")
        output = tmp_path / "must-not-exist.txt"
        command = " ".join(
            (
                shlex.quote(sys.executable),
                "-c",
                shlex.quote(f"from pathlib import Path; Path({str(output)!r}).touch()"),
            )
        )
        registry = JobRegistry(config.state_db_path)
        await registry.open()
        claimed_workspace = tmp_path / "claimed-workspace"
        claimed_workspace.mkdir()
        await registry.register_job(
            "claimed-score",
            tmp_path / "claimed.yaml",
            claimed_workspace,
        )
        await registry.store_failure_hook_config(
            "claimed-score",
            json.dumps([{"type": "run_script", "command": command}]),
        )
        await registry.update_status(
            "claimed-score",
            DaemonJobStatus.FAILED.value,
            error_message="original claimed failure",
        )
        assert await registry.claim_failure_hooks("claimed-score") is True
        await registry.close()

        restarted = JobManager(config)
        await restarted.start()
        try:
            await _wait_for_failure_task_settlement(restarted, "claimed-score")

            assert not output.exists()
            record = await restarted._registry.get_job("claimed-score")
            assert record is not None
            assert record.failure_hooks_started_at is not None
            assert record.failure_hooks_completed_at is None
            assert record.status is DaemonJobStatus.FAILED
            assert record.error_message == "original claimed failure"
        finally:
            await restarted.shutdown()

    async def test_new_run_resets_failure_hook_custody(self, tmp_path: Path) -> None:
        registry = JobRegistry(tmp_path / "registry.db")
        await registry.open()
        try:
            score = tmp_path / "score.yaml"
            workspace = tmp_path / "workspace"
            await registry.register_job("score", score, workspace)
            await registry.store_failure_hook_config(
                "score",
                '[{"type":"run_script","command":"old-hook"}]',
            )
            await registry.update_status("score", DaemonJobStatus.FAILED.value)
            assert await registry.claim_failure_hooks("score") is True
            await registry.complete_failure_hooks("score")

            await registry.register_job("score", score, workspace)

            record = await registry.get_job("score")
            assert record is not None
            assert record.failure_hooks_started_at is None
            assert record.failure_hooks_completed_at is None
            assert await registry.get_failure_hook_config("score") is None
            assert await registry.get_failure_hook_results("score") is None
        finally:
            await registry.close()
