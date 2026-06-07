"""#162: pause must not destroy auto-recovered baton jobs.

After a conductor restart, orphaned baton jobs auto-recover and run in the baton
event loop with NO manager `_jobs` wrapper task. `pause_job`'s wrapper-task check
fired first and DESTRUCTIVELY marked the still-running job FAILED. The baton-pause
path (`request_pause` + `PauseJob`) works without the wrapper, so it must be tried
first — only a job the baton doesn't know about is genuinely stale.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from marianne.daemon.config import DaemonConfig
from marianne.daemon.exceptions import JobSubmissionError
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta


@pytest.fixture
def daemon_config(tmp_path: Path) -> DaemonConfig:
    return DaemonConfig(
        max_concurrent_jobs=2,
        pid_file=tmp_path / "test.pid",
        state_db_path=tmp_path / "registry.db",
    )


@pytest.fixture
async def manager(daemon_config: DaemonConfig) -> AsyncIterator[JobManager]:
    mgr = JobManager(daemon_config)
    await mgr._registry.open()
    mgr._service = MagicMock()
    yield mgr
    await mgr._registry.close()


def _running_meta(job_id: str) -> JobMeta:
    return JobMeta(
        job_id=job_id,
        config_path=Path("/tmp/x.yaml"),
        workspace=Path("/tmp/ws"),
        status=DaemonJobStatus.RUNNING,
    )


def _baton_adapter(job_present: bool) -> MagicMock:
    adapter = MagicMock()
    adapter._baton.request_pause.return_value = job_present
    adapter.has_job.return_value = job_present
    adapter._baton.inbox = asyncio.Queue()
    return adapter


class TestPauseRecoveredJob:
    async def test_pause_recovered_baton_job_is_not_destructive(
        self, manager: JobManager
    ) -> None:
        # Auto-recovered state: RUNNING meta, baton HAS the job, no _jobs wrapper.
        manager._job_meta["j"] = _running_meta("j")
        manager._baton_adapter = _baton_adapter(job_present=True)
        assert "j" not in manager._jobs

        ok = await manager.pause_job("j")

        assert ok is True
        assert manager._job_meta["j"].status == DaemonJobStatus.PAUSED  # NOT FAILED

    async def test_pause_genuinely_stale_job_still_fails(self, manager: JobManager) -> None:
        # Not in the baton and no wrapper → genuinely stale → existing behavior.
        manager._job_meta["s"] = _running_meta("s")
        manager._baton_adapter = _baton_adapter(job_present=False)

        with pytest.raises(JobSubmissionError):
            await manager.pause_job("s")
        assert manager._job_meta["s"].status == DaemonJobStatus.FAILED


class TestCancelRecoveredJob:
    """#162 (cancel half): cancel must stop auto-recovered baton jobs.

    The wrapped path cancels a baton job by cancelling the manager wrapper
    task; the CancelledError handler in `_execute_via_baton` then calls
    `adapter.deregister_job` (kills subprocess groups + cancels musician tasks
    + deregisters). Auto-recovered jobs have no wrapper, so `cancel_job`'s
    `self._jobs.get` was None and it returned False — a silent no-op that left
    the job running. The unwrapped path must converge on the SAME adapter
    deregister the wrapped path reaches.
    """

    async def test_cancel_recovered_baton_job_deregisters(
        self, manager: JobManager
    ) -> None:
        # Auto-recovered: RUNNING meta, baton HAS the job, no _jobs wrapper.
        manager._job_meta["j"] = _running_meta("j")
        manager._baton_adapter = _baton_adapter(job_present=True)
        assert "j" not in manager._jobs

        ok = await manager.cancel_job("j")

        assert ok is True
        manager._baton_adapter.deregister_job.assert_called_once_with("j")
        assert manager._job_meta["j"].status == DaemonJobStatus.CANCELLED

    async def test_cancel_unknown_job_with_no_wrapper_returns_false(
        self, manager: JobManager
    ) -> None:
        # Not pending, no wrapper, baton doesn't have it → genuinely absent.
        manager._job_meta["s"] = _running_meta("s")
        manager._baton_adapter = _baton_adapter(job_present=False)

        ok = await manager.cancel_job("s")

        assert ok is False
        manager._baton_adapter.deregister_job.assert_not_called()
