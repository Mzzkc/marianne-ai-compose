"""Functional review tests for Issue #468: Orchestra view.

Verifies:
- Whitelist projection safety (no sensitive fields survive, non-whitelisted events dropped)
- Event vocabulary completeness and mapping
- Ring buffer bounded eviction for late joiners
- Replay input validation and path traversal rejection (IPC + HTTP)
- read_day robustness on missing/corrupt data
- Public auth isolation (MZT_PUBLIC_ORCHESTRA) and prefix safety
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from marianne.daemon.config import DaemonConfig, OrchestraConfig
from marianne.daemon.orchestra import OrchestraRecorder, project_event
from marianne.daemon.orchestra.recorder import ORCHESTRA_EVENTS, read_day
from marianne.daemon.process import DaemonProcess
from marianne.daemon.types import ObserverEvent
from marianne.dashboard.app import create_app
from marianne.state.json_backend import JsonStateBackend


def _raw_event(
    event: str,
    job: str = "orchestra-job",
    sheet: int = 1,
    **data: Any,
) -> ObserverEvent:
    return {
        "job_id": job,
        "sheet_num": sheet,
        "event": event,
        "data": data,
        "timestamp": 1729500000.0,
    }


class TestFunctionalProjectionSafety:
    """Validate public safety and strict whitelist adherence."""

    def test_complete_orchestra_vocabulary_coverage(self) -> None:
        """Every required event type in the issue shape is represented."""
        required = {
            "job.started",
            "job.ended",
            "job.paused",
            "job.resumed",
            "job.chained",
            "sheet.dispatched",
            "sheet.ended",
            "sheet.fallback",
            "sheet.retry",
            "sheet.skipped",
        }
        assert required <= set(ORCHESTRA_EVENTS)

    def test_sensitive_payloads_thoroughly_scrubbed(self) -> None:
        """Prompts, file paths, env variables, secrets and errors are excluded."""
        leak_payload = {
            "instrument": "claude-code",
            "attempt": 1,
            "workspace": "/home/user/super_secret_dir",
            "prompt": "SELECT * FROM secrets WHERE api_key = 'xyz';",
            "error_message": "Database connection refused at 10.0.0.1:5432",
            "stdout": "Loaded password hash: $6$rounds=5000$...",
            "env": {"OPENAI_API_KEY": "sk-secret-12345"},
            "custom_metadata": {"auth_token": "bearer-abc"},
        }
        raw = _raw_event("sheet.completed", sheet=2, **leak_payload)
        projected = project_event(raw)
        assert projected is not None
        serialized = json.dumps(projected)

        # Ensure no sensitive values leak into projected beat
        for leak in (
            "/home/user/super_secret_dir",
            "api_key",
            "Database connection refused",
            "password hash",
            "sk-secret-12345",
            "bearer-abc",
        ):
            assert leak not in serialized

        # Allowed keys only
        allowed_keys = {"ts", "job", "sheet", "kind", "instrument", "outcome", "note"}
        assert set(projected.keys()) == allowed_keys

    def test_disallowed_events_strictly_dropped(self) -> None:
        """Non-lifecycle bus events produce None and never emit beats."""
        assert project_event(_raw_event("observer.file_created", path="/etc/passwd")) is None
        assert project_event(_raw_event("daemon.process.spawned", pid=1234)) is None
        assert project_event(_raw_event("security.audit.failure", rule="R1")) is None
        assert project_event(_raw_event("baton.checkpoint_saved", path="/tmp/chk")) is None


class TestFunctionalRecorderLifecycle:
    """Validate ring buffer bounding, late joiner streaming, and day file persistence."""

    def test_ring_buffer_eviction_under_load(self, tmp_path: Path) -> None:
        """Ring buffer strictly respects maxlen to bound daemon memory."""
        ring_limit = 100
        config = OrchestraConfig(directory=tmp_path / "orch", ring_size=ring_limit, persist=True)
        recorder = OrchestraRecorder(config)

        # Publish 110 events directly through handler
        for i in range(110):
            recorder._handle_event(_raw_event("sheet.dispatched", sheet=i, instrument=f"inst-{i}"))

        recent = recorder.recent()
        assert len(recent) == ring_limit
        # Should have the 100 most recent events (sheets 10 through 109)
        sheet_nums = [b["sheet"] for b in recent]
        assert sheet_nums == list(range(10, 110))

    def test_read_day_corrupted_jsonl_recovery(self, tmp_path: Path) -> None:
        """read_day ignores corrupt records and recovers valid JSON lines."""
        day_file = tmp_path / "2026-10-21.jsonl"
        valid_beat = {
            "ts": 1729500000.0,
            "job": "score-test",
            "sheet": 1,
            "kind": "sheet.dispatched",
            "instrument": "codex-cli",
            "outcome": None,
            "note": None,
        }
        lines = [
            json.dumps(valid_beat),
            "NOT_JSON_AT_ALL{",
            json.dumps({"incomplete": "dict_without_required_keys"}),
            json.dumps(valid_beat),
            "",
            "   ",
            json.dumps(valid_beat),
        ]
        day_file.write_text("\n".join(lines))

        beats = read_day(day_file)
        assert len(beats) == 3
        assert all(b["kind"] == "sheet.dispatched" for b in beats)


class TestFunctionalReplayValidation:
    """Validate day argument sanitization against directory traversal in IPC and HTTP."""

    def test_ipc_replay_rejects_path_traversal(self, tmp_path: Path) -> None:
        config = DaemonConfig()
        dp = DaemonProcess(config)
        handler = MagicMock()
        manager = MagicMock()
        recorder = OrchestraRecorder(OrchestraConfig(directory=tmp_path, persist=False))
        manager.orchestra = recorder
        dp._register_methods(handler, manager, health=None)
        methods = {c.args[0]: c.args[1] for c in handler.register.call_args_list}

        replay_fn = methods["daemon.orchestra.replay"]

        # Traversal and malformed day strings must raise ValueError
        invalid_days = [
            "../etc/passwd",
            "../../orchestra",
            "2026-10-20/evil",
            "2026/10/20",
            "2026-1-1",
            "2026-10-20.jsonl",
            "2026-10-20; cat /etc/shadow",
            " 2026-10-20 ",
        ]
        for bad_day in invalid_days:
            with pytest.raises(ValueError):
                asyncio.run(replay_fn({"day": bad_day}, None))

    def test_http_api_replay_rejects_path_traversal(self, tmp_path: Path) -> None:
        app = create_app(state_backend=JsonStateBackend(tmp_path), cors_origins=["*"])
        client = TestClient(app)

        # Invalid day query parameters must return 422 Unprocessable Entity
        for bad_day in ["../etc", "2026-1-1", "invalid-date", "2026/10/20"]:
            res = client.get(f"/api/orchestra/replay?day={bad_day}")
            assert res.status_code == 422


class TestFunctionalDashboardPublicGate:
    """Validate MZT_PUBLIC_ORCHESTRA auth boundary."""

    def test_public_orchestra_enabled_exposes_only_orchestra(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MZT_AUTH_MODE", "localhost_only")
        monkeypatch.setenv("MZT_PUBLIC_ORCHESTRA", "true")
        app = create_app(state_backend=JsonStateBackend(tmp_path), cors_origins=["*"])
        # Simulate non-localhost remote peer
        client = TestClient(app, client=("203.0.113.9", 51234))

        # Allowed routes under public orchestra
        assert client.get("/orchestra").status_code == 200
        assert client.get("/api/orchestra/snapshot").status_code == 200
        assert client.get("/api/orchestra/replay?day=2026-10-20").status_code == 200

        # Private routes must be rejected (403 Forbidden)
        res_jobs = client.get("/api/jobs")
        assert res_jobs.status_code == 403

        # Exact prefix boundary check (/orchestral is not /orchestra)
        res_prefix = client.get("/orchestral")
        assert res_prefix.status_code == 403
