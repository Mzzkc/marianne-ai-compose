"""Terminal registry visibility must follow its acknowledged final checkpoint."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.core.config import JobConfig
from marianne.daemon.checkpoint_writer import CheckpointWriter
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import JobManager, JobMeta
from marianne.daemon.registry import DaemonJobStatus
from marianne.daemon.types import JobRequest


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("ordered_writer", [True, False])
@pytest.mark.parametrize("refuse_terminal_save", [True, False])
@pytest.mark.parametrize("resumed", [True, False])
async def test_first_terminal_status_contains_final_checkpoint(
    tmp_path, success, ordered_writer, refuse_terminal_save, resumed,
):
    """Publishing status before persisting timestamp/last-attempt data breaks this."""
    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "registry.db"))
    adapter = MagicMock()
    adapter.publish_job_event = AsyncMock()
    manager._baton_adapter = adapter
    config = JobConfig.model_validate({
        "name": "terminal", "instrument": "ollama", "workspace": str(tmp_path / "ws"),
        "sheet": {"size": 1, "total_items": 1}, "prompt": {"template": "fictional"},
        "spec": {"spec_dir": ""},
    })
    seen = []
    final = "completed" if success else "failed"
    await manager._registry.open()
    writer = CheckpointWriter(manager._registry) if ordered_writer else None
    manager._checkpoint_writer = writer
    if writer is not None:
        writer.start()
    await manager._registry.register_job("terminal", tmp_path / "score.yaml", tmp_path / "ws")
    await manager._registry.update_status("terminal", "running")
    if resumed:
        manager._job_meta["terminal"] = JobMeta(
            job_id="terminal", config_path=tmp_path / "score.yaml", workspace=tmp_path / "ws",
            status=DaemonJobStatus.RUNNING,
        )
        saved = CheckpointState(
            job_id="terminal", job_name="terminal", total_sheets=1,
            status=JobStatus.PAUSED, sheets={1: SheetState(sheet_num=1)},
            config_snapshot=config.model_dump(mode="json"),
        )
        await manager._registry.save_checkpoint("terminal", saved.model_dump_json())
    original_update = manager._registry.update_status

    async def observe_publication(job_id, status, **kwargs):
        await original_update(job_id, status, **kwargs)
        if status == final:
            public = await manager.get_job_status(job_id)
            # An active JobMeta may still advertise RUNNING until the status
            # setter returns. Only a terminal observation makes this promise.
            if public["status"] == final:
                seen.append(public)

    manager._registry.update_status = observe_publication
    if refuse_terminal_save:
        import json

        original_save = manager._registry.save_checkpoint

        async def fail_final_save(job_id, payload):
            if json.loads(payload).get("completed_at") is not None:
                raise OSError("fixture final checkpoint unavailable")
            await original_save(job_id, payload)

        manager._registry.save_checkpoint = fail_final_save

    async def complete(_job_id):
        live = manager._live_states["terminal"]
        live.status = JobStatus.RUNNING
        live.sheets[1].status = SheetStatus.DISPATCHED
        await manager._registry.save_checkpoint("terminal", live.model_dump_json())
        # The asynchronous writer can still have an older snapshot queued.
        if writer is not None:
            writer.enqueue("terminal", live.model_dump_json())
        live.sheets[1].status = SheetStatus.COMPLETED if success else SheetStatus.FAILED
        live.sheets[1].model_echo_status = "observed"
        live.sheets[1].model_requested = "fixture-model"
        live.sheets[1].model_observed = "fixture-model"
        return success

    adapter.wait_for_completion = complete
    try:
        if resumed:
            result = await manager._resume_via_baton("terminal", tmp_path / "ws", no_reload=True)
        else:
            result = await manager._run_via_baton(
                "terminal", config, JobRequest(config_path=tmp_path / "score.yaml"),
            )
        if refuse_terminal_save:
            assert result is DaemonJobStatus.FAILED
            assert seen == []
            assert manager._live_states["terminal"].completed_at is None
            assert (await manager.get_job_status("terminal"))["status"] == "running"
            return
        assert result is (DaemonJobStatus.COMPLETED if success else DaemonJobStatus.FAILED)
        if not seen:
            seen.append(await manager.get_job_status("terminal"))
        assert len(seen) == 1
        first = seen[0]
        assert first["status"] == final
        assert first["completed_at"] is not None
        assert first["sheets"]["1"]["status"] == final
        assert first["sheets"]["1"]["model_echo_status"] == "observed"
        assert first["sheets"]["1"]["model_observed"] == "fixture-model"
        if writer is not None:
            await writer.drain()
        manager._live_states.clear()
        assert (await manager.get_job_status("terminal"))["completed_at"] == first["completed_at"]
    finally:
        if writer is not None:
            await writer.stop()
        await manager._registry.close()
