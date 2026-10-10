"""Durable class resolution and route-unavailable dispatch evidence."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from marianne.core.checkpoint import CheckpointState, SheetState
from marianne.core.config.job import JobConfig
from marianne.core.sheet import build_sheets
from marianne.daemon.baton.adapter import BatonAdapter, sheets_to_execution_states
from marianne.daemon.baton.backend_pool import BackendPool, InstrumentNotRegisteredError
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.prompt import RenderedPrompt, write_context_delivery_receipt
from marianne.instruments.classes import load_class_map, resolve_job_classes
from marianne.instruments.registry import InstrumentRegistry


def _job() -> JobConfig:
    return JobConfig.model_validate(
        {
            "name": "class-runtime",
            "workspace": "/tmp/class-runtime",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Work"},
            "instrument": "strong",
        }
    )


def test_checkpoint_roundtrip_keeps_chain_and_resolution(tmp_path: Path) -> None:
    snapshot = resolve_job_classes(
        _job(),
        {"claude-code", "codex-cli", "antigravity", "opencode"},
        load_class_map(user_path=tmp_path / "absent", venue_path=tmp_path / "absent2"),
    )
    assert snapshot is not None
    sheet = build_sheets(_job(), classes=snapshot)[0]
    state = CheckpointState(
        job_id="j",
        job_name="class-runtime",
        total_sheets=1,
        sheets={
            1: SheetState(
                sheet_num=1,
                instrument_name=sheet.instrument_name,
                instrument_resolution=sheet.instrument_resolution,
            )
        },
        instrument_classes=snapshot,
    )
    recovered = CheckpointState.model_validate_json(state.model_dump_json())
    assert recovered.instrument_classes == snapshot
    assert recovered.sheets[1].instrument_resolution == sheet.instrument_resolution
    execution = sheets_to_execution_states([sheet])[1]
    assert execution.instrument_resolution == sheet.instrument_resolution


def test_status_and_receipt_show_requested_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status_module = importlib.import_module("marianne.cli.commands.status")

    snapshot = resolve_job_classes(
        _job(),
        {"claude-code", "codex-cli", "antigravity", "opencode"},
        load_class_map(user_path=tmp_path / "absent", venue_path=tmp_path / "absent2"),
    )
    assert snapshot is not None
    sheet = build_sheets(_job(), classes=snapshot)[0]
    state = CheckpointState(
        job_id="j",
        job_name="class-runtime",
        total_sheets=1,
        instrument_classes=snapshot,
        sheets={
            1: SheetState(
                sheet_num=1,
                instrument_name=sheet.instrument_name,
                instrument_resolution=sheet.instrument_resolution,
            )
        },
    )
    shown: list[dict[str, object]] = []
    monkeypatch.setattr(status_module, "output_json", shown.append)
    status_module._output_status_json(state)
    assert shown[0]["instrument_classes"]["digest"] == snapshot.digest
    assert shown[0]["sheets"]["1"]["instrument_resolution"]["requested"] == "strong"
    assert status_module.format_instrument_with_fallback(state.sheets[1]).startswith(
        "strong → claude-code"
    )
    receipt = write_context_delivery_receipt(
        workspace=tmp_path,
        job_id="j",
        sheet_num=1,
        attempt=1,
        instrument=sheet.instrument_name,
        rendered=RenderedPrompt(prompt="test", preamble="identity"),
        instrument_class="strong",
        class_snapshot_sha256=snapshot.digest,
    )
    document = yaml.safe_load(receipt.read_text())
    assert document["instrument"] == "claude-code"
    assert document["instrument_class"] == "strong"
    assert document["class_snapshot_sha256"] == snapshot.digest


@pytest.mark.asyncio
async def test_registry_miss_is_typed_and_advances_once() -> None:
    registry = InstrumentRegistry()
    pool = BackendPool(registry)
    with pytest.raises(InstrumentNotRegisteredError):
        await pool.acquire("removed")
    await pool.close_all()

    core = BatonCore()
    sheet = build_sheets(
        _job(),
        classes=resolve_job_classes(
            _job(),
            {"claude-code", "codex-cli", "antigravity", "opencode"},
            load_class_map(),
        ),
    )[0]
    core.register_job("j", sheets_to_execution_states([sheet]), {})
    baton = MagicMock()
    baton.inbox = asyncio.Queue()
    baton.get_job_generation.return_value = core.get_job_generation("j")
    adapter = BatonAdapter.__new__(BatonAdapter)
    adapter._baton = baton
    adapter._send_dispatch_failure(
        "j",
        1,
        sheet.instrument_name,
        "backend acquire: removed from registry",
        state=core._jobs["j"].sheets[1],
        unavailable=True,
    )
    event = baton.inbox.get_nowait()
    assert isinstance(event, SheetAttemptResult)
    assert event.error_classification == "INSTRUMENT_UNAVAILABLE"
    assert event.error_code == "E505"
    await core.handle_event(event)
    assert core._jobs["j"].sheets[1].instrument_name == sheet.instrument_fallbacks[0]


@pytest.mark.asyncio
async def test_real_manager_keeps_class_chain_after_user_map_edit(tmp_path: Path) -> None:
    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.manager import JobManager, JobMeta
    from marianne.daemon.registry import DaemonJobStatus
    from marianne.daemon.types import JobRequest
    from marianne.instruments.loader import load_all_profiles

    score_path = tmp_path / "score.yaml"
    workspace = tmp_path / "workspace"
    score = _job().model_copy(update={"workspace": workspace})
    score_path.write_text(yaml.safe_dump(score.model_dump(mode="json")))
    user_map = tmp_path / "classes.yaml"
    user_map.write_text("version: 1\nclasses:\n  strong: [cli, claude-code]\n")
    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "registry.db"))
    registry = InstrumentRegistry()
    for profile in load_all_profiles().values():
        registry.register(profile)
    manager._instrument_registry = registry
    manager._class_map = load_class_map(user_path=user_map, venue_path=tmp_path / "absent")
    adapter = BatonAdapter()
    manager._baton_adapter = adapter
    await manager._registry.open()
    await manager._registry.register_job("class-job", score_path, workspace)
    manager._job_meta["class-job"] = JobMeta(
        job_id="class-job",
        config_path=score_path,
        workspace=workspace,
        status=DaemonJobStatus.RUNNING,
    )
    observed: list[str] = []

    async def consume(job_id: str) -> bool:
        saved = await manager._registry.load_checkpoint(job_id)
        assert saved is not None
        checkpoint = CheckpointState.model_validate_json(saved)
        assert checkpoint.instrument_classes is not None
        observed.append(checkpoint.instrument_classes.classes["strong"].chain[0].profile)
        assert adapter.get_sheet(job_id, 1).instrument_name == "cli"
        return True

    adapter.wait_for_completion = consume  # type: ignore[method-assign]
    try:
        assert await manager._run_via_baton("class-job", score, JobRequest(config_path=score_path))
        user_map.write_text("version: 1\nclasses:\n  strong: [claude-code]\n")
        manager._class_map = load_class_map(user_path=user_map, venue_path=tmp_path / "absent")
        adapter = BatonAdapter()
        adapter.wait_for_completion = consume  # type: ignore[method-assign]
        manager._baton_adapter = adapter
        assert await manager._resume_via_baton("class-job", workspace)
        assert observed == ["cli", "cli"]
    finally:
        await manager._registry.close()


def test_hot_reload_watcher_tracks_class_file_content(tmp_path: Path) -> None:
    from marianne.daemon.hot_reload import ConfigWatcher

    class_file = tmp_path / "classes.yaml"

    async def reload(_reason: str) -> None:
        return None

    watcher = ConfigWatcher(
        config_file=None,
        profile_dirs=[],
        class_files=[class_file],
        reload_fn=reload,
    )
    absent = watcher._scan()
    assert list(absent.values()) == ["<missing>"]
    class_file.write_text("version: 1\nclasses:\n  strong: [cli]\n")
    first = watcher._scan()
    assert first != absent
    class_file.write_text("version: 1\nclasses:\n  strong: [claude-code]\n")
    assert watcher._scan() != first
