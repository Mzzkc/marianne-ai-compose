"""A StateBackend backed by the daemon registry DB (#111).

Lets offline readers (CLI commands when the conductor is down) get the *current*
job state from the authoritative daemon DB (`~/.marianne/daemon-state.db`)
instead of the stale, possibly-lossy per-workspace state files. The daemon DB is
the source of truth; this adapter exposes it through the standard
``StateBackend`` interface so existing offline-read callers (which expect a
``(CheckpointState, StateBackend)`` pair) work unchanged.

Lives in ``daemon/`` (not ``state/``) because it bridges ``state.StateBackend``
onto ``daemon.JobRegistry`` — ``state/`` must not import ``daemon/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from marianne.core.checkpoint import CheckpointState, SheetStatus
from marianne.core.constants import DAEMON_STATE_DB_PATH
from marianne.core.logging import get_logger
from marianne.state.base import StateBackend

if TYPE_CHECKING:
    from marianne.daemon.registry import JobRegistry

_logger = get_logger("daemon.registry_backend")


class RegistryStateBackend(StateBackend):
    """Expose the daemon registry's checkpoint store as a ``StateBackend``.

    Wraps an *already-open* ``JobRegistry``; ``close()`` closes it, matching the
    "caller closes the returned backend" contract of the offline-read helpers.
    """

    def __init__(self, registry: JobRegistry) -> None:
        self._registry = registry

    async def load(self, job_id: str) -> CheckpointState | None:
        checkpoint_json = await self._registry.load_checkpoint(job_id)
        if not checkpoint_json:
            return None
        return CheckpointState.model_validate_json(checkpoint_json)

    async def save(self, state: CheckpointState) -> None:
        await self._registry.save_checkpoint(state.job_id, state.model_dump_json())

    async def delete(self, job_id: str) -> bool:
        deleted = await self._registry.delete_jobs(job_ids=[job_id])
        return deleted > 0

    async def list_jobs(self) -> list[CheckpointState]:
        states: list[CheckpointState] = []
        for record in await self._registry.list_jobs(limit=1000):
            checkpoint_json = await self._registry.load_checkpoint(record.job_id)
            if not checkpoint_json:
                continue
            try:
                states.append(CheckpointState.model_validate_json(checkpoint_json))
            except Exception:
                # A single corrupt checkpoint must not break listing the rest.
                _logger.warning(
                    "registry_backend.list_skip_invalid", job_id=record.job_id, exc_info=True
                )
        return states

    async def get_next_sheet(self, job_id: str) -> int | None:
        state = await self.load(job_id)
        if state is None:
            return 1  # start from the beginning if no state (mirrors SQLite backend)
        return state.get_next_sheet()

    async def mark_sheet_status(
        self,
        job_id: str,
        sheet_num: int,
        status: SheetStatus,
        error_message: str | None = None,
    ) -> None:
        state = await self.load(job_id)
        if state is None:
            raise ValueError(f"No state found for job {job_id}")
        if status == SheetStatus.COMPLETED:
            state.mark_sheet_completed(sheet_num)
        elif status == SheetStatus.FAILED:
            state.mark_sheet_failed(sheet_num, error_message or "Unknown error")
        elif status == SheetStatus.IN_PROGRESS:
            state.mark_sheet_started(sheet_num)
        await self.save(state)

    async def close(self) -> None:
        await self._registry.close()


class RegistryFirstReadBackend(StateBackend):
    """Read the authoritative daemon DB first, fall back to the workspace (#111).

    For read-only consumers (the MCP server, the dashboard's offline fallback)
    that previously read stale per-workspace state: ``load()`` tries the daemon
    registry first and falls back to the per-workspace backend for jobs absent
    from the registry (workspace-only / pre-daemon). The registry is opened
    transiently per ``load`` (no long-lived handle to leak), and absence falls
    back rather than suppresses — so legitimate workspace-only reads are
    preserved. All non-load operations delegate to the workspace fallback (these
    consumers only ever call ``load``).
    """

    def __init__(self, workspace: Path, *, db_path: Path | None = None) -> None:
        from marianne.state.json_backend import JsonStateBackend

        self._fallback: StateBackend = JsonStateBackend(workspace)
        self._db_path = (db_path or DAEMON_STATE_DB_PATH).expanduser()

    async def load(self, job_id: str) -> CheckpointState | None:
        if self._db_path.exists():
            try:
                from marianne.daemon.registry import JobRegistry

                async with JobRegistry(self._db_path) as registry:
                    state = await RegistryStateBackend(registry).load(job_id)
                if state is not None:
                    return state
            except Exception:
                _logger.warning(
                    "registry_first.read_failed", job_id=job_id, exc_info=True
                )
        return await self._fallback.load(job_id)

    async def save(self, state: CheckpointState) -> None:
        await self._fallback.save(state)

    async def delete(self, job_id: str) -> bool:
        return await self._fallback.delete(job_id)

    async def list_jobs(self) -> list[CheckpointState]:
        return await self._fallback.list_jobs()

    async def get_next_sheet(self, job_id: str) -> int | None:
        return await self._fallback.get_next_sheet(job_id)

    async def mark_sheet_status(
        self,
        job_id: str,
        sheet_num: int,
        status: SheetStatus,
        error_message: str | None = None,
    ) -> None:
        await self._fallback.mark_sheet_status(job_id, sheet_num, status, error_message)

    async def close(self) -> None:
        await self._fallback.close()
