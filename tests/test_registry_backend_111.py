"""#111: RegistryStateBackend — read/write job state through the daemon DB.

This adapter is the keystone for routing offline reads to the authoritative
daemon registry (instead of stale per-workspace files). It must satisfy the
StateBackend contract over the registry's checkpoint_json store.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.daemon.registry import JobRegistry
from marianne.daemon.registry_backend import RegistryStateBackend


def _ckpt(job_id: str) -> CheckpointState:
    return CheckpointState(job_id=job_id, job_name=job_id, total_sheets=1)


async def _register(reg: JobRegistry, job_id: str, ws: Path) -> None:
    # save_checkpoint UPDATEs an existing row, so register first.
    await reg.register_job(job_id, ws / "cfg.yaml", ws)


class TestRegistryStateBackend:
    async def test_load_roundtrip(self, tmp_path) -> None:
        async with JobRegistry(tmp_path / "d.db") as reg:
            await _register(reg, "j1", tmp_path)
            await reg.save_checkpoint("j1", _ckpt("j1").model_dump_json())
            backend = RegistryStateBackend(reg)
            state = await backend.load("j1")
            assert state is not None
            assert state.job_id == "j1"

    async def test_load_missing_returns_none(self, tmp_path) -> None:
        async with JobRegistry(tmp_path / "d.db") as reg:
            backend = RegistryStateBackend(reg)
            assert await backend.load("nope") is None

    async def test_save_then_load(self, tmp_path) -> None:
        async with JobRegistry(tmp_path / "d.db") as reg:
            await _register(reg, "j1", tmp_path)
            backend = RegistryStateBackend(reg)
            await backend.save(_ckpt("j1"))
            loaded = await backend.load("j1")
            assert loaded is not None and loaded.job_id == "j1"

    async def test_delete_terminal_job(self, tmp_path) -> None:
        async with JobRegistry(tmp_path / "d.db") as reg:
            await _register(reg, "j1", tmp_path)
            await reg.save_checkpoint("j1", _ckpt("j1").model_dump_json())
            await reg.update_status("j1", "completed")  # registry only deletes terminal jobs
            backend = RegistryStateBackend(reg)
            assert await backend.delete("j1") is True
            assert await backend.load("j1") is None

    async def test_delete_active_job_refused(self, tmp_path) -> None:
        # The registry never deletes active (queued/running) jobs; the adapter
        # reflects that safety invariant.
        async with JobRegistry(tmp_path / "d.db") as reg:
            await _register(reg, "j1", tmp_path)  # status 'queued'
            await reg.save_checkpoint("j1", _ckpt("j1").model_dump_json())
            backend = RegistryStateBackend(reg)
            assert await backend.delete("j1") is False
            assert await backend.load("j1") is not None  # still present

    async def test_mark_sheet_status(self, tmp_path) -> None:
        async with JobRegistry(tmp_path / "d.db") as reg:
            await _register(reg, "j1", tmp_path)
            state = _ckpt("j1")
            state.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.IN_PROGRESS)
            await reg.save_checkpoint("j1", state.model_dump_json())
            backend = RegistryStateBackend(reg)
            await backend.mark_sheet_status("j1", 1, SheetStatus.COMPLETED)
            reloaded = await backend.load("j1")
            assert reloaded is not None
            assert reloaded.sheets[1].status == SheetStatus.COMPLETED

    async def test_list_jobs(self, tmp_path) -> None:
        async with JobRegistry(tmp_path / "d.db") as reg:
            for jid in ("a", "b"):
                await _register(reg, jid, tmp_path)
                await reg.save_checkpoint(jid, _ckpt(jid).model_dump_json())
            backend = RegistryStateBackend(reg)
            ids = {s.job_id for s in await backend.list_jobs()}
            assert ids == {"a", "b"}
