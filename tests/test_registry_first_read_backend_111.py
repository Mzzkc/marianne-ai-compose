"""#111: RegistryFirstReadBackend — registry-first reads with workspace fallback.

For read-only consumers (MCP, dashboard offline). load() prefers the
authoritative daemon DB and falls back to the workspace for jobs absent from the
registry — so stale per-workspace state is bypassed for registry-present jobs,
while workspace-only/pre-daemon jobs still read.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.checkpoint import CheckpointState
from marianne.daemon.registry import JobRegistry
from marianne.daemon.registry_backend import RegistryFirstReadBackend
from marianne.state import JsonStateBackend


def _ckpt(job_id: str, last_completed: int) -> CheckpointState:
    return CheckpointState(
        job_id=job_id, job_name=job_id, total_sheets=3, last_completed_sheet=last_completed
    )


async def _registry_with(db: Path, job_id: str, ws: Path, last_completed: int) -> None:
    async with JobRegistry(db) as reg:
        await reg.register_job(job_id, ws / "cfg.yaml", ws)
        await reg.save_checkpoint(job_id, _ckpt(job_id, last_completed).model_dump_json())


class TestRegistryFirstReadBackend:
    async def test_registry_present_wins_over_stale_workspace(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        jb = JsonStateBackend(ws)
        await jb.save(_ckpt("j1", last_completed=1))  # stale
        await jb.close()
        db = tmp_path / "daemon-state.db"
        await _registry_with(db, "j1", ws, last_completed=3)  # current

        backend = RegistryFirstReadBackend(ws, db_path=db)
        state = await backend.load("j1")
        assert state is not None and state.last_completed_sheet == 3
        await backend.close()

    async def test_absent_from_registry_falls_back(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        jb = JsonStateBackend(ws)
        await jb.save(_ckpt("only-ws", last_completed=2))
        await jb.close()
        db = tmp_path / "daemon-state.db"
        await _registry_with(db, "other", ws, last_completed=3)

        backend = RegistryFirstReadBackend(ws, db_path=db)
        state = await backend.load("only-ws")
        assert state is not None and state.last_completed_sheet == 2  # workspace fallback
        await backend.close()

    async def test_no_registry_db_reads_workspace(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        jb = JsonStateBackend(ws)
        await jb.save(_ckpt("legacy", last_completed=1))
        await jb.close()

        backend = RegistryFirstReadBackend(ws, db_path=tmp_path / "nonexistent.db")
        state = await backend.load("legacy")
        assert state is not None and state.last_completed_sheet == 1
        await backend.close()

    async def test_missing_everywhere_returns_none(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        backend = RegistryFirstReadBackend(ws, db_path=tmp_path / "nonexistent.db")
        assert await backend.load("nope") is None
        await backend.close()
