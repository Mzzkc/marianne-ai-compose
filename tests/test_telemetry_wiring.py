"""Both real baton terminal routes stage only with explicit user permission."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.core.config import JobConfig
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import JobManager, JobMeta
from marianne.daemon.registry import DaemonJobStatus
from marianne.daemon.types import JobRequest
from marianne.telemetry import ConsentRecord


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("success", [False, True])
@pytest.mark.parametrize("permission", ["unset", "granted", "revoked", "broken-collector"])
async def test_no_collection_without_consent(tmp_path, monkeypatch, resumed, success, permission):
    monkeypatch.setenv("HOME", str(tmp_path))
    consent = ConsentRecord()
    if permission != "unset":
        consent.grant()
    if permission == "revoked":
        consent.revoke()
    if permission == "broken-collector":
        from marianne.telemetry.collector import TelemetryCollector

        def fail(*args, **kwargs):
            raise OSError("fixture disk unavailable")

        monkeypatch.setattr(TelemetryCollector, "collect", fail)
    spool_path = tmp_path / "spool.jsonl"
    config = JobConfig.model_validate({
        "name": "telemetry", "instrument": "ollama", "workspace": str(tmp_path / "ws"),
        "sheet": {"size": 1, "total_items": 1}, "prompt": {"template": "fictional"},
        "spec": {"spec_dir": ""},
        "telemetry": {"enabled": True, "spool_path": str(spool_path)},
    })
    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "registry.db"))
    adapter = MagicMock()
    adapter.publish_job_event = AsyncMock()
    manager._baton_adapter = adapter
    await manager._registry.open()
    await manager._registry.register_job("job", tmp_path / "score.yaml", tmp_path / "ws")
    if resumed:
        manager._job_meta["job"] = JobMeta(
            job_id="job", config_path=tmp_path / "score.yaml", workspace=tmp_path / "ws",
            status=DaemonJobStatus.RUNNING,
        )
        checkpoint = CheckpointState(
            job_id="job", job_name="telemetry", total_sheets=1, status=JobStatus.PAUSED,
            sheets={1: SheetState(sheet_num=1)}, config_snapshot=config.model_dump(mode="json"),
        )
        await manager._registry.save_checkpoint("job", checkpoint.model_dump_json())

    async def complete(job_id):
        sheet = manager._live_states[job_id].sheets[1]
        sheet.status = SheetStatus.COMPLETED if success else SheetStatus.FAILED
        sheet.attempt_count = 2
        sheet.error_message = "could not read /home/alice/private.txt TOKEN=fictional"
        sheet.learned_patterns = ["retry"]
        return success

    adapter.wait_for_completion = complete
    try:
        if resumed:
            result = await manager._resume_via_baton("job", tmp_path / "ws", no_reload=True)
        else:
            result = await manager._run_via_baton(
                "job", config, JobRequest(config_path=tmp_path / "score.yaml"),
            )
        assert result is (DaemonJobStatus.COMPLETED if success else DaemonJobStatus.FAILED)
        if permission == "granted":
            envelope, = [json.loads(line) for line in spool_path.read_text().splitlines()]
            assert envelope["payload"]["job"]["status"] == result.value
            sheet, = envelope["payload"]["sheets"]
            assert sheet["attempt_count"] == 2
            assert sheet["status"] == result.value
            assert sheet["learned_patterns"] == ["retry"]
            assert "/home/alice" not in spool_path.read_text()
            assert "TOKEN=fictional" not in spool_path.read_text()
        else:
            assert not spool_path.exists()
    finally:
        await manager._registry.close()
