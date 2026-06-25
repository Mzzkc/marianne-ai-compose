"""StateBackend adapter that reads job state from the running daemon via IPC.

Wraps ``DaemonClient`` to satisfy the ``StateBackend`` ABC so the dashboard
can consume live daemon data without touching the filesystem directly.
All write methods raise ``NotImplementedError`` — the dashboard is read-only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetStatus
from marianne.daemon.exceptions import DaemonError
from marianne.daemon.ipc.client import DaemonClient
from marianne.state.base import StateBackend
from marianne.utils.time import utc_now

_logger = logging.getLogger(__name__)

DAEMON_STATE_READ_TIMEOUT_SECONDS = 2.0
DAEMON_STATE_ENRICH_TIMEOUT_SECONDS = 0.5
DAEMON_STATE_MAX_ENRICHED_JOBS = 10


class DaemonStateAdapter(StateBackend):
    """Read-only ``StateBackend`` backed by live daemon IPC calls.

    Parameters
    ----------
    client:
        An already-configured ``DaemonClient`` instance.
    """

    def __init__(self, client: DaemonClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    async def load(self, job_id: str) -> CheckpointState | None:
        """Load state for a job from the daemon.

        Returns ``None`` if the job is not found (``DaemonError``).
        """
        try:
            data = await asyncio.wait_for(
                self._client.get_job_status(job_id, ""),
                timeout=DAEMON_STATE_READ_TIMEOUT_SECONDS,
            )
            return CheckpointState(**data)
        except (DaemonError, ConnectionError, TimeoutError, OSError):
            _logger.debug("load_job_not_found", extra={"job_id": job_id})
            return None

    async def list_jobs(self) -> list[CheckpointState]:
        """List all jobs by querying the daemon roster then enriching each."""
        try:
            roster: list[dict[str, Any]] = await asyncio.wait_for(
                self._client.list_jobs(),
                timeout=DAEMON_STATE_READ_TIMEOUT_SECONDS,
            )
        except (DaemonError, ConnectionError, TimeoutError, OSError) as exc:
            _logger.warning(
                "list_jobs_daemon_unavailable",
                extra={"error_type": type(exc).__name__},
            )
            return []

        results: list[CheckpointState] = []

        for index, entry in enumerate(roster):
            job_id = entry.get("job_id", "")
            if index < DAEMON_STATE_MAX_ENRICHED_JOBS:
                try:
                    data = await asyncio.wait_for(
                        self._client.get_job_status(job_id, ""),
                        timeout=DAEMON_STATE_ENRICH_TIMEOUT_SECONDS,
                    )
                    results.append(CheckpointState(**data))
                    continue
                except (DaemonError, ConnectionError, TimeoutError, OSError):
                    _logger.debug(
                        "list_jobs_fallback",
                        extra={"job_id": job_id},
                    )

            results.append(_checkpoint_from_roster_entry(entry))

        return results

    # ------------------------------------------------------------------
    # Write methods — not supported (dashboard is read-only)
    # ------------------------------------------------------------------

    async def save(self, state: CheckpointState) -> None:
        raise NotImplementedError("Dashboard is read-only")

    async def delete(self, job_id: str) -> bool:
        raise NotImplementedError("Dashboard is read-only")

    async def get_next_sheet(self, job_id: str) -> int | None:
        raise NotImplementedError("Dashboard is read-only")

    async def mark_sheet_status(
        self,
        job_id: str,
        sheet_num: int,
        status: SheetStatus,
        error_message: str | None = None,
    ) -> None:
        raise NotImplementedError("Dashboard is read-only")

    async def close(self) -> None:
        """No-op — DaemonClient uses per-request connections."""


def _checkpoint_from_roster_entry(entry: dict[str, Any]) -> CheckpointState:
    """Build a conservative dashboard summary from daemon roster metadata."""
    raw_status = entry.get("status", "pending")
    status = JobStatus(getattr(raw_status, "value", raw_status))
    total_sheets = entry.get("total_sheets") or 1
    current_sheet = entry.get("current_sheet")
    completed_at = entry.get("completed_at")
    last_completed_sheet = total_sheets if status == JobStatus.COMPLETED else 0
    if isinstance(current_sheet, int) and status in {JobStatus.RUNNING, JobStatus.FAILED}:
        last_completed_sheet = max(0, current_sheet - 1)

    return CheckpointState(
        job_id=entry.get("job_id", ""),
        job_name=entry.get("job_name") or entry.get("job_id", ""),
        total_sheets=total_sheets,
        status=status,
        last_completed_sheet=last_completed_sheet,
        current_sheet=current_sheet,
        created_at=entry.get("submitted_at") or utc_now(),
        started_at=entry.get("started_at"),
        completed_at=completed_at,
        error_message=entry.get("error_message"),
        worktree_path=entry.get("workspace"),
        config_path=entry.get("config_path"),
        pid=entry.get("pid"),
    )
