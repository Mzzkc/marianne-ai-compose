"""#111: clearing a job removes its workspace state so it can't be resurrected.

The desync repro: `mzt clear` removed the registry row + in-memory state but
left the per-workspace `.marianne-state.db` / `{job_id}.json`, so a later offline
read or re-submit resurrected stale state. `clear` now also deletes the cleared
job's workspace state — per job, so a shared-workspace SQLite DB keeps other
jobs' rows. The READ path is untouched (workspace-only jobs with no registry
entry still read normally).
"""

from __future__ import annotations

from marianne.core.checkpoint import CheckpointState
from marianne.core.constants import STATE_DB_FILENAME
from marianne.daemon.manager import JobManager
from marianne.state import JsonStateBackend, SQLiteStateBackend


def _ckpt(job_id: str) -> CheckpointState:
    return CheckpointState(job_id=job_id, job_name=job_id, total_sheets=1)


class TestDeleteWorkspaceState:
    async def test_sqlite_removes_only_target_job(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        db = ws / STATE_DB_FILENAME
        backend = SQLiteStateBackend(db)
        await backend.save(_ckpt("A"))
        await backend.save(_ckpt("B"))
        await backend.close()

        await JobManager._delete_workspace_state("A", ws)

        check = SQLiteStateBackend(db)
        try:
            assert await check.load("A") is None  # cleared job removed
            assert await check.load("B") is not None  # other job preserved
        finally:
            await check.close()

    async def test_json_state_removed(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        backend = JsonStateBackend(ws)
        await backend.save(_ckpt("A"))
        await backend.close()

        await JobManager._delete_workspace_state("A", ws)

        check = JsonStateBackend(ws)
        try:
            assert await check.load("A") is None
        finally:
            await check.close()

    async def test_missing_workspace_is_noop(self, tmp_path) -> None:
        # No workspace dir / no state files → best-effort no-op, no raise.
        await JobManager._delete_workspace_state("A", tmp_path / "nonexistent")
