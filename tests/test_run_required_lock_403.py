"""GH #403: an opt-in composition-lock admission gate on `mzt run`."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import JobManager, _verify_required_lock
from marianne.daemon.types import JobRequest

runner = CliRunner()

SCORE = (
    "name: locked\n"
    "instrument: cli\n"
    "sheet:\n  size: 1\n  total_items: 1\n"
    "prompt:\n  template: echo hi\n"
)


def _lock_for(score: Path, lock: Path, *, digest: str | None = None) -> None:
    lock.write_text(json.dumps({
        "schema_version": 1,
        "score_sha256": digest or hashlib.sha256(score.read_bytes()).hexdigest(),
        "injections": {},
        "candidate_sha256": "0" * 64,
    }))


class TestVerify:
    def test_valid_lock_passes(self, tmp_path: Path) -> None:
        score = tmp_path / "s.yaml"
        score.write_text(SCORE)
        lock = tmp_path / "composition-lock.json"
        _lock_for(score, lock)
        assert _verify_required_lock(lock, score.read_bytes(), "j") is None

    def test_missing_stale_and_malformed_name_their_reason(self, tmp_path: Path) -> None:
        score = tmp_path / "s.yaml"
        score.write_text(SCORE)
        lock = tmp_path / "composition-lock.json"
        assert "missing" in (_verify_required_lock(lock, score.read_bytes(), "j") or "")
        _lock_for(score, lock, digest="f" * 64)
        assert "stale" in (_verify_required_lock(lock, score.read_bytes(), "j") or "")
        lock.write_text("{not json")
        assert "not valid JSON" in (_verify_required_lock(lock, score.read_bytes(), "j") or "")
        lock.write_text(json.dumps({"candidate_sha256": "0" * 64}))
        assert "score_sha256" in (_verify_required_lock(lock, score.read_bytes(), "j") or "")


@pytest.mark.asyncio
async def test_manager_refuses_failed_gate_and_stale_lock_and_admits_plain(
    tmp_path: Path,
) -> None:
    """Three scores, one per outcome: each refusal must be the lock's, not the
    in-flight guard's (a rejected job id is still briefly 'being submitted')."""
    manager = JobManager(
        DaemonConfig(pid_file=tmp_path / "c.pid", state_db_path=tmp_path / "jobs.db")
    )
    await manager.start()
    try:
        def score_named(name: str) -> Path:
            path = tmp_path / f"{name}.yaml"
            path.write_text(SCORE.replace("echo hi", "sleep 30").replace("locked", name))
            return path

        # A failed gate wrote no lock: refused with the reason.
        missing_lock = tmp_path / "missing-lock.json"
        refused = await manager.submit_job(
            JobRequest(config_path=score_named("gate-failed"), required_lock=missing_lock)
        )
        assert refused.status == "rejected" and "missing" in refused.message
        # The gate passed on different bytes: stale, refused.
        stale_score = score_named("gate-stale")
        stale_lock = tmp_path / "stale-lock.json"
        _lock_for(stale_score, stale_lock, digest="a" * 64)
        stale = await manager.submit_job(
            JobRequest(config_path=stale_score, required_lock=stale_lock)
        )
        assert stale.status == "rejected" and "stale" in stale.message
        # Unlocked submission keeps ordinary behaviour: the gate is opt-in.
        plain = await manager.submit_job(JobRequest(config_path=score_named("plain")))
        assert plain.status == "accepted", plain.message
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_manager_admits_a_valid_lock(tmp_path: Path) -> None:
    score = tmp_path / "locked2.yaml"
    score.write_text(SCORE.replace("echo hi", "sleep 30").replace("locked", "locked2"))
    lock = tmp_path / "composition-lock.json"
    _lock_for(score, lock)
    manager = JobManager(
        DaemonConfig(pid_file=tmp_path / "c.pid", state_db_path=tmp_path / "jobs.db")
    )
    await manager.start()
    try:
        ok = await manager.submit_job(JobRequest(config_path=score, required_lock=lock))
        assert ok.status == "accepted", ok.message
    finally:
        await manager.shutdown(graceful=False)


def test_cli_passes_the_lock_through_and_hints_on_refusal(tmp_path: Path) -> None:
    score = tmp_path / "s.yaml"
    score.write_text(SCORE)
    lock = tmp_path / "composition-lock.json"
    seen: dict[str, object] = {}

    async def route(method: str, params: dict[str, object]) -> tuple[bool, object]:
        seen.update(params)
        return True, {"status": "rejected", "job_id": "s",
                      "message": f"Required composition lock is missing: {lock}"}

    with (
        patch(
            "marianne.daemon.detect.is_daemon_available",
            new_callable=AsyncMock, return_value=True,
        ),
        patch("marianne.daemon.detect.try_daemon_route", side_effect=route),
    ):
        result = runner.invoke(app, ["run", str(score), "--require-lock", str(lock)])
    assert result.exit_code == 1
    assert seen["required_lock"] == str(lock.resolve())
    assert "release gate" in result.output.lower() and "--require-lock" in result.output
