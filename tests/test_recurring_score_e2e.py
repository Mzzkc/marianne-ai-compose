"""Physical restart and non-overlap proof for autonomous recurring scores."""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Callable
from pathlib import Path

from marianne.daemon.baton.events import CronTick
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager
from marianne.daemon.types import JobRequest


def _write_cli_score(
    score_path: Path,
    run_log: Path,
    workspace: Path,
    hold_fifo: Path,
    held_marker: Path,
    immediate_marker: Path,
) -> None:
    """Write a one-second score whose first scheduled child waits at a FIFO."""
    command = (
        f"if [ ! -e {shlex.quote(str(immediate_marker))} ]; then "
        f"touch {shlex.quote(str(immediate_marker))}; "
        f"printf 'immediate\\n' >> {shlex.quote(str(run_log))}; "
        f"elif [ ! -e {shlex.quote(str(held_marker))} ]; then "
        f"touch {shlex.quote(str(held_marker))}; "
        f"read _ < {shlex.quote(str(hold_fifo))}; "
        f"printf 'scheduled\\n' >> {shlex.quote(str(run_log))}; "
        f"else printf 'scheduled\\n' >> {shlex.quote(str(run_log))}; fi"
    )
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
    """Observe manager terminal transitions without replacing normal execution."""
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


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(12.0):
        while not predicate():
            await asyncio.sleep(0)


async def _redeliver_due(manager: JobManager, due_at: float) -> None:
    """Drive the same manager-wired TimerWheel payload a second time."""
    controller = manager._recurrence_controller
    assert controller is not None
    await controller.handle_tick(
        CronTick(
            entry_name="recurring-runtime-e2e",
            score_path="ignored-by-registry.yaml",
            due_at=due_at,
            timestamp=due_at,
        )
    )


async def test_recurring_cli_score_restarts_without_overlap_or_duplicate_due_child(
    tmp_path: Path,
) -> None:
    """Real manager/Baton execution keeps exactly one active child per due identity."""
    score_path = tmp_path / "recurring-score.yaml"
    run_log = tmp_path / "runs.log"
    hold_fifo = tmp_path / "scheduled-hold.fifo"
    held_marker = tmp_path / "scheduled-held"
    immediate_marker = tmp_path / "immediate-ran"
    os.mkfifo(hold_fifo)
    _write_cli_score(
        score_path,
        run_log,
        tmp_path / "workspace",
        hold_fifo,
        held_marker,
        immediate_marker,
    )

    manager = _manager(tmp_path)
    immediate_done, scheduled_done, scheduled_ids = _completion_barrier(manager)
    await manager.start()
    try:
        response = await manager.submit_job(JobRequest(config_path=score_path))
        assert response.status == "accepted"
        await _wait(immediate_done)
        await _wait_until(held_marker.exists)

        first = await manager._schedule_registry.get("recurring-runtime-e2e")
        assert first is not None
        first_child_id = first.last_run_id
        first_due_at = first.last_due_at
        assert first_child_id is not None
        assert first_due_at is not None
        assert first_child_id != response.job_id
        assert response.job_id not in scheduled_ids()
        for job_id in (response.job_id, first_child_id):
            meta = manager._job_meta[job_id]
            assert meta.schedule_id == "recurring-runtime-e2e"
            assert meta.config_path == score_path.resolve()

        active_lineage = [
            meta
            for meta in manager._job_meta.values()
            if meta.schedule_id == "recurring-runtime-e2e"
            and meta.status
            in {DaemonJobStatus.QUEUED, DaemonJobStatus.RUNNING, DaemonJobStatus.PENDING}
        ]
        peak_active_lineage = len(active_lineage)
        assert peak_active_lineage == 1
        assert peak_active_lineage <= 1

        before_duplicate = await manager._schedule_registry.get("recurring-runtime-e2e")
        await _redeliver_due(manager, first_due_at)
        after_duplicate = await manager._schedule_registry.get("recurring-runtime-e2e")
        assert after_duplicate == before_duplicate
        assert [job_id for job_id in manager._job_meta if "--scheduled--" in job_id] == [
            first_child_id
        ]
        assert run_log.read_text(encoding="utf-8").splitlines() == ["immediate"]

        with hold_fifo.open("w", encoding="utf-8") as writer:
            writer.write("release\n")
        await _wait(scheduled_done)
        assert run_log.read_text(encoding="utf-8").splitlines() == [
            "immediate",
            "scheduled",
        ]
        next_due = await manager._schedule_registry.get("recurring-runtime-e2e")
        assert next_due is not None
        assert next_due.next_due_at > first_due_at
    finally:
        await manager.shutdown(graceful=False)

    restarted = _manager(tmp_path)
    _, restarted_scheduled_done, restarted_scheduled_ids = _completion_barrier(restarted)
    await restarted.start()
    try:
        await _wait(restarted_scheduled_done)
        restored = await restarted._schedule_registry.get("recurring-runtime-e2e")
        assert restored is not None
        assert restored.last_run_id is not None
        assert restored.last_run_id in restarted_scheduled_ids()
        assert restored.last_run_id not in {response.job_id, first_child_id}
        assert restored.last_due_at is not None
        assert restored.last_due_at >= next_due.next_due_at
        assert restored.score_path == score_path.resolve()
        assert restarted._job_meta[restored.last_run_id].schedule_id == (
            "recurring-runtime-e2e"
        )
        assert restarted._job_meta[restored.last_run_id].config_path == score_path.resolve()
    finally:
        await restarted.shutdown(graceful=False)

    assert run_log.read_text(encoding="utf-8").splitlines() == [
        "immediate",
        "scheduled",
        "scheduled",
    ]
