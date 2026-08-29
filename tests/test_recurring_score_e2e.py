"""Physical restart proof for autonomous recurring scores.

The score below uses Marianne's built-in local ``cli`` instrument, so this
exercises normal manager, registry, controller, TimerWheel, Baton, and child
process execution without a network backend or an execution replacement.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable
from pathlib import Path

from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager
from marianne.daemon.types import JobRequest


def _write_cli_score(score_path: Path, run_log: Path, workspace: Path) -> None:
    """Write a one-sheet local score whose completion is visible on disk."""
    command = f"printf 'completed\\n' >> {shlex.quote(str(run_log))}"
    score_path.write_text(
        "name: recurring-runtime-e2e\n"
        f"workspace: {workspace}\n"
        "instrument: cli\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        f"  template: {command!r}\n"
        "schedule:\n"
        "  interval: 1s\n",
        encoding="utf-8",
    )


def _manager(tmp_path: Path) -> JobManager:
    return JobManager(
        DaemonConfig(
            max_concurrent_jobs=1,
            pid_file=tmp_path / "conductor.pid",
            state_db_path=tmp_path / "conductor-state.db",
        )
    )


def _completion_barrier(
    manager: JobManager,
) -> tuple[asyncio.Event, asyncio.Event, Callable[[], list[str]]]:
    """Observe normal terminal transitions without replacing the runner."""
    immediate_done = asyncio.Event()
    scheduled_done = asyncio.Event()
    scheduled_ids: list[str] = []
    original_set_status = manager._set_job_status

    async def observed_set_status(
        job_id: str,
        status: DaemonJobStatus,
        *args: object,
        **kwargs: object,
    ) -> None:
        await original_set_status(job_id, status, *args, **kwargs)
        if status is not DaemonJobStatus.COMPLETED:
            return
        meta = manager._job_meta[job_id]
        if meta.schedule_id != "recurring-runtime-e2e":
            return
        if "--scheduled--" in job_id:
            scheduled_ids.append(job_id)
            scheduled_done.set()
        else:
            immediate_done.set()

    manager._set_job_status = observed_set_status  # type: ignore[method-assign]
    return immediate_done, scheduled_done, lambda: list(scheduled_ids)


async def _wait(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=12.0)


async def test_recurring_cli_score_restarts_without_duplicate_due_child(
    tmp_path: Path,
) -> None:
    """One immediate run and one child survive a manager-only restart exactly once."""
    score_path = tmp_path / "recurring-score.yaml"
    run_log = tmp_path / "runs.log"
    _write_cli_score(score_path, run_log, tmp_path / "workspace")

    manager = _manager(tmp_path)
    immediate_done, scheduled_done, scheduled_ids = _completion_barrier(manager)
    await manager.start()
    try:
        response = await manager.submit_job(JobRequest(config_path=score_path))
        assert response.status == "accepted"
        await _wait(immediate_done)
        await _wait(scheduled_done)

        first = await manager._schedule_registry.get("recurring-runtime-e2e")
        assert first is not None
        first_child_id = first.last_run_id
        first_due_at = first.last_due_at
        next_due_at = first.next_due_at
        assert first_child_id in scheduled_ids()
        assert first.score_path == score_path.resolve()
        assert first_due_at is not None
        assert next_due_at > first_due_at
        assert manager._is_schedule_active("recurring-runtime-e2e") is False
    finally:
        await manager.shutdown(graceful=False)

    restarted = _manager(tmp_path)
    _, restarted_scheduled_done, restarted_scheduled_ids = _completion_barrier(
        restarted
    )
    await restarted.start()
    try:
        await _wait(restarted_scheduled_done)
        restored = await restarted._schedule_registry.get("recurring-runtime-e2e")
        assert restored is not None
        assert restored.score_path == score_path.resolve()
        assert restored.last_run_id in restarted_scheduled_ids()
        assert restored.last_run_id != first_child_id
        assert restored.last_due_at is not None
        assert restored.last_due_at >= next_due_at
        assert len({first_due_at, restored.last_due_at}) == 2
        assert restarted._is_schedule_active("recurring-runtime-e2e") is False
    finally:
        await restarted.shutdown(graceful=False)

    assert run_log.read_text(encoding="utf-8").splitlines() == [
        "completed",
        "completed",
        "completed",
    ]
