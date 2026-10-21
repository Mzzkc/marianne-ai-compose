"""The orchestra view (GH #468): a public-safe, replayable projection of the fleet."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from marianne.daemon.config import DaemonConfig, OrchestraConfig
from marianne.daemon.event_bus import EventBus
from marianne.daemon.orchestra import OrchestraRecorder, project_event
from marianne.daemon.orchestra.recorder import read_day
from marianne.daemon.types import ObserverEvent


def _ev(event: str, job: str = "score-a", sheet: int = 0, **data: Any) -> ObserverEvent:
    return {
        "job_id": job, "sheet_num": sheet, "event": event, "data": data, "timestamp": time.time(),
    }


class TestProjection:
    def test_only_whitelisted_fields_survive(self) -> None:
        raw = _ev(
            "sheet.completed", sheet=3, instrument="codex-cli", attempt=2,
            workspace="/home/me/secret", prompt="do the thing", error_message="boom",
        )
        out = project_event(raw)
        assert out == {
            "ts": raw["timestamp"], "job": "score-a", "sheet": 3, "kind": "sheet.ended",
            "instrument": "codex-cli", "outcome": "completed", "note": "attempt 2",
        }
        assert "secret" not in json.dumps(out) and "boom" not in json.dumps(out)

    def test_unknown_events_are_dropped(self) -> None:
        assert project_event(_ev("observer.file_created", path="/x")) is None
        assert project_event(_ev("baton.stale_check")) is None

    def test_fallback_note_and_trigger_chain(self) -> None:
        fb = project_event(_ev("baton.instrument.fallback", sheet=4,
                               from_instrument="claude-code", to_instrument="codex-cli"))
        assert fb is not None and fb["kind"] == "sheet.fallback"
        assert fb["note"] == "claude-code -> codex-cli" and fb["instrument"] == "codex-cli"
        chained = project_event(_ev("baton.flow.trigger_fired", sheet=13,
                                    actions=[{"concert": {"score": "x.yaml"}}]))
        assert chained is not None and chained["kind"] == "job.chained"
        goto_only = _ev("baton.flow.trigger_fired", sheet=13, actions=[{"goto": 2}])
        assert project_event(goto_only) is None


@pytest.mark.asyncio
async def test_recorder_projects_persists_and_streams(tmp_path: Path) -> None:
    config = OrchestraConfig(directory=tmp_path / "orch", ring_size=100, keep_ended_seconds=0.0)
    bus = EventBus(max_queue_size=100)
    await bus.start()
    recorder = OrchestraRecorder(config)
    await recorder.start(bus)
    queue = recorder.watch()
    try:
        recorder.seed_job("score-a", total_sheets=3, started_at=time.time() - 5)
        await bus.publish(_ev("job.started"))
        await bus.publish(_ev("sheet.dispatched", sheet=1, instrument="claude-code", attempt=1))
        await bus.publish(_ev("sheet.completed", sheet=1, instrument="claude-code", attempt=1))
        await bus.publish(_ev("sheet.dispatched", sheet=2, instrument="codex-cli", attempt=1))
        await bus.publish(_ev("observer.file_created", path="/private"))
        beats = []
        for _ in range(4):
            beats.append(await asyncio.wait_for(queue.get(), 5))
        assert [b["kind"] for b in beats] == [
            "job.started", "sheet.dispatched", "sheet.ended", "sheet.dispatched",
        ]
        snap = recorder.snapshot()
        job = next(j for j in snap["jobs"] if j["job"] == "score-a")
        assert job["status"] == "running" and job["total_sheets"] == 3
        by_num = {s["sheet"]: s for s in job["sheets"]}
        assert by_num[1]["status"] == "completed" and by_num[2]["status"] == "playing"
        assert by_num[2]["instrument"] == "codex-cli"
        assert len(recorder.recent()) == 4

        await bus.publish(_ev("job.completed"))
        await asyncio.wait_for(queue.get(), 5)
        day = recorder.day_path()
        lines = [json.loads(line) for line in day.read_text().splitlines()]
        assert [line["kind"] for line in lines][-1] == "job.ended"
        keys = {"ts", "job", "sheet", "kind", "instrument", "outcome", "note"}
        assert all(set(line) == keys for line in lines)
        assert "/private" not in day.read_text()
        replayed = read_day(day)
        assert len(replayed) == 5 and replayed[0]["kind"] == "job.started"
        # keep_ended_seconds=0: the ended job leaves the snapshot on the next prune
        recorder._prune_ended()
        assert all(j["job"] != "score-a" for j in recorder.snapshot()["jobs"])
    finally:
        recorder.unwatch(queue)
        await recorder.stop(bus)
        await bus.shutdown()


def test_read_day_skips_garbage(tmp_path: Path) -> None:
    path = tmp_path / "2026-10-20.jsonl"
    path.write_text('{"kind":"job.started","job":"a","ts":1}\nnot json\n{"nope":1}\n')
    assert [e["kind"] for e in read_day(path)] == ["job.started"]
    assert read_day(tmp_path / "missing.jsonl") == []


def test_orchestra_config_is_part_of_the_daemon_config() -> None:
    cfg = DaemonConfig()
    assert cfg.orchestra.enabled and cfg.orchestra.persist
    assert cfg.orchestra.directory == Path("~/.marianne/orchestra")


class TestIpc:
    def test_methods_registered_and_snapshot_replay_shape(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from marianne.daemon.process import DaemonProcess

        config = DaemonConfig()
        dp = DaemonProcess(config)
        handler = MagicMock()
        manager = MagicMock()
        recorder = OrchestraRecorder(OrchestraConfig(directory=tmp_path, persist=False))
        recorder.seed_job("score-a", total_sheets=2, started_at=1.0)
        manager.orchestra = recorder
        dp._register_methods(handler, manager, health=None)
        registered = {c.args[0]: c.args[1] for c in handler.register.call_args_list}
        wanted = {"daemon.orchestra.snapshot", "daemon.orchestra.replay", "daemon.orchestra.stream"}
        assert wanted <= set(registered)
        snap = asyncio.run(registered["daemon.orchestra.snapshot"]({"limit": 10}, None))
        assert snap["enabled"] is True and snap["recent"] == []
        assert snap["jobs"][0]["job"] == "score-a"
        line = {"kind": "job.started", "job": "a", "ts": 1, "sheet": None,
                "instrument": None, "outcome": None, "note": None}
        (tmp_path / "2026-10-20.jsonl").write_text(json.dumps(line) + "\n")
        replay = asyncio.run(registered["daemon.orchestra.replay"]({"day": "2026-10-20"}, None))
        assert replay["day"] == "2026-10-20" and replay["events"][0]["job"] == "a"
        with pytest.raises(ValueError):
            asyncio.run(registered["daemon.orchestra.replay"]({"day": "../etc"}, None))


class TestDashboard:
    def test_page_and_api_without_conductor(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from marianne.dashboard.app import create_app
        from marianne.state.json_backend import JsonStateBackend

        app = create_app(state_backend=JsonStateBackend(tmp_path), cors_origins=["*"])
        client = TestClient(app)
        page = client.get("/orchestra")
        assert page.status_code == 200 and "Orchestra" in page.text
        snap = client.get("/api/orchestra/snapshot").json()
        assert snap["enabled"] is False and snap["jobs"] == []
        assert client.get("/api/orchestra/replay?day=2026-10-20").json()["events"] == []
        assert client.get("/api/orchestra/replay?day=nope").status_code == 422

    def test_api_with_view(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        from fastapi.testclient import TestClient

        from marianne.dashboard.app import create_app
        from marianne.dashboard.routes import orchestra as route
        from marianne.dashboard.services.orchestra_view import OrchestraView
        from marianne.state.json_backend import JsonStateBackend

        app = create_app(state_backend=JsonStateBackend(tmp_path), cors_origins=["*"])
        fake_client = AsyncMock()
        fake_client.call = AsyncMock(
            return_value={"enabled": True, "jobs": [{"job": "s"}], "recent": []},
        )
        route.set_orchestra_view(OrchestraView(fake_client))
        try:
            snap = TestClient(app).get("/api/orchestra/snapshot?limit=5").json()
            assert snap["jobs"] == [{"job": "s"}]
            fake_client.call.assert_awaited_with("daemon.orchestra.snapshot", {"limit": 5})
        finally:
            route.set_orchestra_view(None)
