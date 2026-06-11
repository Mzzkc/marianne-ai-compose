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
    """Read job state from the authoritative daemon DB — conductor-ONLY (#50).

    For read-only consumers (the MCP server, the dashboard's offline
    fallback). #111 gave this class a per-workspace fallback; #50 removed
    it per the composer's ruling ("workspaces are for work to occur only,
    not for state tracking") — ``load()`` reads the daemon registry and
    NOTHING else. The registry is opened transiently per ``load`` (no
    long-lived handle to leak). A job absent from the registry does not
    exist as far as state is concerned.

    The ``workspace`` constructor parameter is retained for call-signature
    stability but no longer consulted. Write/list operations raise — these
    consumers only ever read, and state writes belong to the conductor.
    """

    def __init__(self, workspace: Path, *, db_path: Path | None = None) -> None:
        del workspace  # #50: no workspace state access, ever
        self._db_path = (db_path or DAEMON_STATE_DB_PATH).expanduser()

    async def load(self, job_id: str) -> CheckpointState | None:
        if not self._db_path.exists():
            return None
        try:
            from marianne.daemon.registry import JobRegistry

            async with JobRegistry(self._db_path) as registry:
                return await RegistryStateBackend(registry).load(job_id)
        except Exception:
            _logger.warning(
                "registry_first.read_failed", job_id=job_id, exc_info=True
            )
            return None

    async def save(self, state: CheckpointState) -> None:
        raise NotImplementedError(
            "RegistryFirstReadBackend is read-only (#50): state writes "
            "belong to the conductor."
        )

    async def delete(self, job_id: str) -> bool:
        raise NotImplementedError(
            "RegistryFirstReadBackend is read-only (#50): state writes "
            "belong to the conductor."
        )

    async def list_jobs(self) -> list[CheckpointState]:
        if not self._db_path.exists():
            return []
        try:
            from marianne.daemon.registry import JobRegistry

            async with JobRegistry(self._db_path) as registry:
                records = await registry.list_jobs()
                states: list[CheckpointState] = []
                for record in records:
                    state = await RegistryStateBackend(registry).load(
                        record.job_id
                    )
                    if state is not None:
                        states.append(state)
                return states
        except Exception:
            _logger.warning("registry_first.list_failed", exc_info=True)
            return []

    async def get_next_sheet(self, job_id: str) -> int | None:
        state = await self.load(job_id)
        if state is None:
            return None
        nxt = state.last_completed_sheet + 1
        return nxt if nxt <= state.total_sheets else None

    async def mark_sheet_status(
        self,
        job_id: str,
        sheet_num: int,
        status: SheetStatus,
        error_message: str | None = None,
    ) -> None:
        raise NotImplementedError(
            "RegistryFirstReadBackend is read-only (#50): state writes "
            "belong to the conductor."
        )

    async def close(self) -> None:
        return None
