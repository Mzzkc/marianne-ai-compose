"""Resume and restart preserve the operator's durable pause decision."""

import asyncio
import time
from pathlib import Path

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.core.config.flow import SheetTriggerConfig, TriggerAction
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.types import JobRequest


@pytest.mark.asyncio
async def test_resume_rejects_changed_wall_limit_before_transition(tmp_path: Path) -> None:
    score = tmp_path / "changed.yaml"
    score.write_text(
        "name: Changed\n"
        f"workspace: {tmp_path}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: test\n"
        "max_wall_seconds: 120\n",
        encoding="utf-8",
    )
    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "jobs.db"))
    manager._job_meta["j"] = JobMeta(
        job_id="j",
        config_path=score,
        workspace=tmp_path,
        status=DaemonJobStatus.PAUSED,
        max_wall_seconds=60.0,
        wall_deadline_at=1234.0,
    )

    with pytest.raises(Exception, match="max_wall_seconds is immutable on resume"):
        await manager._resume_active_job("j", config_path=score)
    assert manager._job_meta["j"].status == DaemonJobStatus.PAUSED
    assert manager._job_meta["j"].wall_deadline_at == 1234.0


@pytest.mark.asyncio
@pytest.mark.parametrize("flow_pause", [False, True])
async def test_paused_job_survives_real_manager_restart(
    tmp_path: Path,
    flow_pause: bool,
) -> None:
    score = tmp_path / "paused.yaml"
    score.write_text(
        "name: paused-flow\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: echo ok\n",
        encoding="utf-8",
    )
    config = DaemonConfig(
        pid_file=tmp_path / "conductor.pid",
        state_db_path=tmp_path / "jobs.db",
    )
    seed = JobManager(config)
    await seed._registry.open()
    try:
        await seed._registry.register_job("paused-flow", score, tmp_path / "workspace")
        checkpoint = CheckpointState(
            job_id="paused-flow",
            job_name="paused-flow",
            total_sheets=1,
            status=JobStatus.PAUSED,
            sheets={1: SheetState(sheet_num=1)},
        )
        if flow_pause:
            checkpoint.flow.pause_reason = "trigger on sheet 1"
        await seed._registry.save_checkpoint("paused-flow", checkpoint.model_dump_json())
        await seed._registry.update_status("paused-flow", DaemonJobStatus.PAUSED.value)
    finally:
        await seed._registry.close()

    manager = JobManager(config)
    await manager.start()
    try:
        assert manager._job_meta["paused-flow"].status == DaemonJobStatus.PAUSED
        assert "paused-flow" not in manager._jobs
        persisted = await manager._registry.load_checkpoint("paused-flow")
        assert persisted is not None
        restored = CheckpointState.model_validate_json(persisted)
        assert restored.status == JobStatus.PAUSED
        assert restored.flow.pause_reason == ("trigger on sheet 1" if flow_pause else None)
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_operator_pause_survives_restart_after_real_submission(tmp_path: Path) -> None:
    score = tmp_path / "operator-paused.yaml"
    score.write_text(
        "name: operator-paused\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: sleep 1\n",
        encoding="utf-8",
    )
    config = DaemonConfig(
        pid_file=tmp_path / "conductor.pid",
        state_db_path=tmp_path / "jobs.db",
    )
    first = JobManager(config)
    await first.start()
    try:
        response = await first.submit_job(JobRequest(config_path=score))
        assert response.status == "accepted"

        def registered() -> bool:
            return (
                first._job_meta[response.job_id].status == DaemonJobStatus.RUNNING
                and first._baton_adapter is not None
                and response.job_id in first._baton_adapter._baton._jobs
            )

        deadline = time.monotonic() + 3
        while not registered() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert registered()
        assert await first.pause_job(response.job_id)
        assert first._job_meta[response.job_id].status == DaemonJobStatus.PAUSED
    finally:
        await first.shutdown(graceful=False)

    second = JobManager(config)
    await second.start()
    try:
        assert second._job_meta[response.job_id].status == DaemonJobStatus.PAUSED
        assert response.job_id not in second._jobs
        checkpoint_json = await second._registry.load_checkpoint(response.job_id)
        assert checkpoint_json is not None
        assert CheckpointState.model_validate_json(checkpoint_json).status == JobStatus.PAUSED
    finally:
        await second.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_operator_pause_during_inflight_sheet_survives_graceful_restart(
    tmp_path: Path,
) -> None:
    score = tmp_path / "inflight-paused.yaml"
    score.write_text(
        "name: inflight-paused\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: sleep 2\n",
        encoding="utf-8",
    )
    config = DaemonConfig(
        pid_file=tmp_path / "conductor.pid",
        state_db_path=tmp_path / "jobs.db",
        shutdown_timeout_seconds=10,
    )
    first = JobManager(config)
    await first.start()
    try:
        response = await first.submit_job(JobRequest(config_path=score))
        assert response.status == "accepted"

        def inflight() -> bool:
            adapter = first._baton_adapter
            return (
                adapter is not None
                and response.job_id in adapter._baton._jobs
                and any(
                    sheet.status in {SheetStatus.DISPATCHED, SheetStatus.IN_PROGRESS}
                    for sheet in adapter._baton._jobs[response.job_id].sheets.values()
                )
            )

        deadline = time.monotonic() + 5
        while not inflight() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert inflight()
        assert await first.pause_job(response.job_id)
        assert first._job_meta[response.job_id].status == DaemonJobStatus.PAUSED
    finally:
        await first.shutdown(graceful=True)

    second = JobManager(config)
    await second.start()
    try:
        assert second._job_meta[response.job_id].status == DaemonJobStatus.PAUSED
        assert response.job_id not in second._jobs
        checkpoint_json = await second._registry.load_checkpoint(response.job_id)
        assert checkpoint_json is not None
        assert CheckpointState.model_validate_json(checkpoint_json).status == JobStatus.PAUSED
    finally:
        await second.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_trigger_pause_survives_restart_until_explicit_operator_resume(
    tmp_path: Path,
) -> None:
    score = tmp_path / "trigger-paused.yaml"
    score.write_text(
        "name: trigger-paused\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 2\n  total_items: 2\n"
        "  triggers:\n    1:\n      on_success:\n        - pause: true\n"
        "prompt:\n  template: echo done\n",
        encoding="utf-8",
    )
    config = DaemonConfig(
        pid_file=tmp_path / "conductor.pid",
        state_db_path=tmp_path / "jobs.db",
    )
    seed = JobManager(config)
    await seed._registry.open()
    try:
        await seed._registry.register_job("j", score, tmp_path / "workspace")
        checkpoint = CheckpointState(
            job_id="j",
            job_name="trigger-paused",
            total_sheets=2,
            sheets={
                1: SheetState(sheet_num=1, instrument_name="cli"),
                2: SheetState(sheet_num=2, instrument_name="cli"),
            },
        )
        baton = BatonCore()
        baton.register_job(
            "j",
            checkpoint.sheets,
            {2: [1]},
            flow_state=checkpoint.flow,
            triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(pause=True)])},
        )
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j",
                sheet_num=1,
                instrument_name="cli",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )
        assert checkpoint.sheets[1].status == SheetStatus.COMPLETED
        assert checkpoint.sheets[2].status == SheetStatus.PENDING
        assert checkpoint.flow.pause_reason == "trigger on sheet 1"
        checkpoint.status = JobStatus.PAUSED
        await seed._registry.save_checkpoint("j", checkpoint.model_dump_json())
        await seed._registry.update_status("j", DaemonJobStatus.PAUSED.value)
    finally:
        await seed._registry.close()

    manager = JobManager(config)
    await manager.start()
    try:
        assert manager._job_meta["j"].status == DaemonJobStatus.PAUSED
        assert "j" not in manager._jobs
        before_json = await manager._registry.load_checkpoint("j")
        assert before_json is not None
        assert CheckpointState.model_validate_json(before_json).flow.pause_reason == (
            "trigger on sheet 1"
        )
        response = await manager.resume_job("j")
        assert response.status == "accepted"
        deadline = time.monotonic() + 3
        while (
            manager._job_meta["j"].status
            not in {
                DaemonJobStatus.COMPLETED,
                DaemonJobStatus.FAILED,
            }
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.01)
        after_json = await manager._registry.load_checkpoint("j")
        assert after_json is not None
        assert CheckpointState.model_validate_json(after_json).flow.pause_reason is None
        assert manager._job_meta["j"].status == DaemonJobStatus.COMPLETED
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_concert_submission_is_fire_and_forget_and_refuses_active_duplicate(
    tmp_path: Path,
) -> None:
    parent_score = tmp_path / "parent.yaml"
    parent_score.write_text("name: parent\n", encoding="utf-8")
    child_score = tmp_path / "child.yaml"
    child_score.write_text(
        "name: child\n"
        f"workspace: {tmp_path / 'child-workspace'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: sleep 30\n",
        encoding="utf-8",
    )
    manager = JobManager(
        DaemonConfig(
            pid_file=tmp_path / "conductor.pid",
            state_db_path=tmp_path / "jobs.db",
        )
    )
    await manager.start()
    try:
        manager._job_meta["parent"] = JobMeta(
            job_id="parent",
            config_path=parent_score,
            workspace=tmp_path,
            status=DaemonJobStatus.RUNNING,
        )
        accepted, child_id, message = await manager._submit_flow_concert(
            "parent",
            "child.yaml",
        )
        assert accepted, message
        assert child_id == "child"
        assert manager._job_meta["child"].chain_depth == 1
        duplicate, duplicate_id, reason = await manager._submit_flow_concert(
            "parent",
            "child.yaml",
        )
        assert not duplicate
        assert duplicate_id == "child"
        assert reason is not None and "already" in reason
    finally:
        await manager.shutdown(graceful=False)
