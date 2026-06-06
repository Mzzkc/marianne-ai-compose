"""#111: offline reads prefer the authoritative daemon DB (registry-first).

`_find_job_state_fs` now reads the daemon registry first and falls back to the
workspace. Registry-present jobs return CURRENT registry state; jobs absent from
the registry (workspace-only / pre-daemon) still read from the workspace — the
read is never suppressed, which is what keeps the 51 status tests green.
"""

from __future__ import annotations

from pathlib import Path

from marianne.cli import helpers
from marianne.cli.helpers import _find_job_state_fs
from marianne.core.checkpoint import CheckpointState
from marianne.daemon.registry import JobRegistry
from marianne.state import JsonStateBackend


def _ckpt(job_id: str, *, last_completed: int = 0) -> CheckpointState:
    return CheckpointState(
        job_id=job_id, job_name=job_id, total_sheets=3, last_completed_sheet=last_completed
    )


async def _registry_with_checkpoint(
    db_path: Path, job_id: str, ws: Path, last_completed: int
) -> None:
    async with JobRegistry(db_path) as reg:
        await reg.register_job(job_id, ws / "cfg.yaml", ws)
        payload = _ckpt(job_id, last_completed=last_completed).model_dump_json()
        await reg.save_checkpoint(job_id, payload)


class TestRegistryFirstOfflineRead:
    async def test_registry_present_returns_current_state(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        # Workspace has STALE state (last_completed=1)...
        jb = JsonStateBackend(ws)
        await jb.save(_ckpt("j1", last_completed=1))
        await jb.close()
        # ...registry has CURRENT state (last_completed=3).
        db = tmp_path / "daemon-state.db"
        await _registry_with_checkpoint(db, "j1", ws, last_completed=3)
        monkeypatch.setattr(helpers, "DAEMON_STATE_DB_PATH", db)

        state, backend = await _find_job_state_fs("j1", ws)
        assert state is not None
        assert state.last_completed_sheet == 3  # registry won, not the stale workspace
        if backend is not None:
            await backend.close()

    async def test_absent_falls_back_to_workspace(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        jb = JsonStateBackend(ws)
        await jb.save(_ckpt("only-in-ws", last_completed=2))
        await jb.close()
        db = tmp_path / "daemon-state.db"
        # registry exists but lists a DIFFERENT job
        await _registry_with_checkpoint(db, "other", ws, last_completed=3)
        monkeypatch.setattr(helpers, "DAEMON_STATE_DB_PATH", db)

        state, backend = await _find_job_state_fs("only-in-ws", ws)
        assert state is not None  # not suppressed — workspace fallback
        assert state.last_completed_sheet == 2
        if backend is not None:
            await backend.close()

    async def test_no_registry_db_reads_workspace(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        jb = JsonStateBackend(ws)
        await jb.save(_ckpt("legacy", last_completed=1))
        await jb.close()
        monkeypatch.setattr(helpers, "DAEMON_STATE_DB_PATH", tmp_path / "nonexistent.db")

        state, backend = await _find_job_state_fs("legacy", ws)
        assert state is not None and state.last_completed_sheet == 1
        if backend is not None:
            await backend.close()
