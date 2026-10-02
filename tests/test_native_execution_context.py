"""Native execution facts reach trusted templates, including raw CLI sheets."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from marianne.core.checkpoint import CheckpointState
from marianne.core.config.job import PromptConfig
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.state import AttemptContext, AttemptMode


def test_registered_job_identity_reaches_raw_cli_template(tmp_path: Path) -> None:
    adapter = BatonAdapter()
    sheet = Sheet(
        num=1,
        movement=1,
        voice_count=1,
        workspace=tmp_path,
        instrument_name="cli",
        prompt_template="{{ native_execution | default(none) | tojson }}",
    )
    adapter.register_job("actual-child-17", [sheet], {1: []}, prompt_config=PromptConfig())
    rendered = adapter._job_renderers["actual-child-17"].render(
        sheet, AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL), raw_prompt=True
    )
    assert json.loads(rendered.prompt) == {
        "job_id": "actual-child-17",
        "schedule_id": None,
        "scheduled_due_at": None,
    }


@pytest.mark.parametrize("scheduled", [False, True])
def test_checkpoint_resume_preserves_original_native_identity(
    tmp_path: Path, scheduled: bool,
) -> None:
    payload = {
        "job_id": "old-native-child", "job_name": "score", "total_sheets": 1,
        "sheets": {1: {"sheet_num": 1}},
    }
    if scheduled:
        payload.update(schedule_id="stable-schedule", scheduled_due_at=1700000000.25)
    checkpoint = CheckpointState.model_validate_json(json.dumps(payload))
    # A real serialization boundary, not the live request or a new clock.
    checkpoint = CheckpointState.model_validate_json(checkpoint.model_dump_json())
    sheet = Sheet(
        num=1, movement=1, voice_count=1, workspace=tmp_path, instrument_name="cli",
        prompt_template="{{ native_execution | default(none) | tojson }}",
        variables={"native_execution": {"job_id": "forged-sheet"}},
    )
    adapter = BatonAdapter()
    adapter.recover_job(
        "old-native-child", [sheet], {1: []}, checkpoint,
        prompt_config=PromptConfig(variables={"native_execution": "forged-config"}),
    )
    rendered = adapter._job_renderers["old-native-child"].render(
        sheet, AttemptContext(attempt_number=7, mode=AttemptMode.NORMAL), raw_prompt=True
    )
    assert json.loads(rendered.prompt) == {
        "job_id": "old-native-child",
        "schedule_id": "stable-schedule" if scheduled else None,
        "scheduled_due_at": 1700000000.25 if scheduled else None,
    }


def test_distinct_registered_children_cannot_share_context(tmp_path: Path) -> None:
    adapter = BatonAdapter()
    sheet = Sheet(
        num=1, movement=1, voice_count=1, workspace=tmp_path, instrument_name="cli",
        prompt_template="{{ native_execution | default(none) | tojson }}",
    )
    for job_id, due in [("child-a", 1700000000.25), ("child-b", 1700000300.25)]:
        adapter.register_job(
            job_id, [sheet], {1: []}, prompt_config=PromptConfig(),
            schedule_id="stable-schedule", scheduled_due_at=due,
        )
    for job_id, due in [("child-a", 1700000000.25), ("child-b", 1700000300.25)]:
        rendered = adapter._job_renderers[job_id].render(
            sheet, AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL), raw_prompt=True
        )
        assert json.loads(rendered.prompt) == {
            "job_id": job_id, "schedule_id": "stable-schedule", "scheduled_due_at": due,
        }


@pytest.mark.parametrize("ordered_writer", [False, True])
async def test_manager_persists_identity_before_cli_and_reuses_it_on_resume(
    tmp_path: Path, ordered_writer: bool,
) -> None:
    from marianne.core.config import JobConfig
    from marianne.daemon.checkpoint_writer import CheckpointWriter
    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.manager import JobManager, JobMeta
    from marianne.daemon.registry import DaemonJobStatus
    from marianne.daemon.types import JobRequest

    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "registry.db"))
    adapter = BatonAdapter()
    manager._baton_adapter = adapter
    score_path = tmp_path / "score.yaml"
    config = JobConfig.model_validate({
        "name": "identity", "instrument": "cli", "workspace": str(tmp_path / "ws"),
        "sheet": {"size": 1, "total_items": 1}, "spec": {"spec_dir": ""},
        "prompt": {"template": "printf '%s' '{{ native_execution | default(none) | tojson }}'"},
    })
    score_path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    await manager._registry.open()
    writer = CheckpointWriter(manager._registry) if ordered_writer else None
    manager._checkpoint_writer = writer
    if writer is not None:
        writer.start()
    await manager._registry.register_job("native-child", score_path, tmp_path / "ws")
    manager._job_meta["native-child"] = JobMeta(
        job_id="native-child", config_path=score_path, workspace=tmp_path / "ws",
        status=DaemonJobStatus.RUNNING,
    )
    observed = []

    async def consume(job_id: str) -> bool:
        saved = await manager._registry.load_checkpoint(job_id)
        assert saved is not None, "Native identity must be durable before any CLI execution"
        checkpoint = CheckpointState.model_validate_json(saved)
        assert checkpoint.schedule_id == "original-schedule"
        assert checkpoint.scheduled_due_at == 1700000000.25
        sheet = adapter.get_sheet(job_id, 1)
        assert sheet is not None
        rendered = adapter._job_renderers[job_id].render(
            sheet, AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL), raw_prompt=True
        )
        process = await asyncio.create_subprocess_exec(
            "bash", "-c", rendered.prompt, stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        assert process.returncode == 0
        observed.append(json.loads(stdout))
        return True

    adapter.wait_for_completion = consume
    try:
        result = await manager._run_via_baton("native-child", config, JobRequest(
            config_path=score_path, schedule_id="original-schedule", scheduled_due_at=1700000000.25,
        ))
        assert result is DaemonJobStatus.COMPLETED
        # Discard both adapter and live cache. Ordinary resume reloads the score,
        # but must take original execution identity from the database, not YAML.
        manager._live_states.clear()
        adapter = BatonAdapter()
        adapter.wait_for_completion = consume
        manager._baton_adapter = adapter
        result = await manager._resume_via_baton("native-child", tmp_path / "ws")
        assert result is DaemonJobStatus.COMPLETED
        assert observed == [{
            "job_id": "native-child", "schedule_id": "original-schedule",
            "scheduled_due_at": 1700000000.25,
        }] * 2
    finally:
        if writer is not None:
            await writer.stop()
        await manager._registry.close()


def test_score_validation_recognizes_native_context_without_user_variables(tmp_path: Path) -> None:
    from marianne.core.config import JobConfig
    from marianne.validation.checks.jinja import JinjaUndefinedVariableCheck

    config = JobConfig.model_validate({
        "name": "identity", "workspace": str(tmp_path),
        "sheet": {"size": 1, "total_items": 1},
        "prompt": {
            "template": "{{ native_execution.job_id }} {{ native_execution.scheduled_due_at }}",
        },
    })
    assert JinjaUndefinedVariableCheck().check(config, tmp_path / "score.yaml", "") == []


async def test_checkpoint_save_refusal_prevents_native_registration(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock

    from marianne.core.config import JobConfig
    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.manager import JobManager
    from marianne.daemon.types import JobRequest

    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "registry.db"))
    adapter = BatonAdapter()
    manager._baton_adapter = adapter
    manager._registry.save_checkpoint = AsyncMock(side_effect=OSError("fixture save refused"))
    config = JobConfig.model_validate({
        "name": "identity", "instrument": "cli", "workspace": str(tmp_path / "ws"),
        "sheet": {"size": 1, "total_items": 1}, "spec": {"spec_dir": ""},
        "prompt": {"template": "printf unreachable"},
    })
    with pytest.raises(OSError, match="fixture save refused"):
        await manager._run_via_baton("refused-child", config, JobRequest(
            config_path=tmp_path / "score.yaml", schedule_id="schedule", scheduled_due_at=1.25,
        ))
    assert "refused-child" not in adapter.baton._jobs
    assert "refused-child" not in adapter._job_renderers
