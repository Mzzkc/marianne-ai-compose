"""Adversarial physical coverage for durable recurring-score runtime behavior."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from marianne.daemon.baton.events import CronTick
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager
from marianne.daemon.types import JobRequest


def _manager(tmp_path: Path) -> JobManager:
    return JobManager(
        DaemonConfig(
            max_concurrent_jobs=1,
            pid_file=tmp_path / "conductor.pid",
            state_db_path=tmp_path / "conductor-state.db",
        )
    )


def _write_score(
    score_path: Path,
    workspace: Path,
    *,
    name: str = "corrupt-runtime-score",
    schedule: str = "  interval: 1h\n",
    command: str = "true",
    max_wall_seconds: float | None = None,
) -> None:
    deadline = "" if max_wall_seconds is None else f"max_wall_seconds: {max_wall_seconds}\n"
    score_path.write_text(
        f"name: {name}\n"
        f"workspace: {workspace}\n"
        "instrument: cli\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        f"  template: {command!r}\n"
        "schedule:\n"
        f"{schedule}"
        f"{deadline}",
        encoding="utf-8",
    )


async def _wait_for_terminal(manager: JobManager, job_id: str) -> None:
    async with asyncio.timeout(8.0):
        while manager._job_meta[job_id].status not in {
            DaemonJobStatus.COMPLETED,
            DaemonJobStatus.FAILED,
        }:
            await asyncio.sleep(0)


async def _tick(manager: JobManager, schedule_id: str, due_at: float) -> None:
    """Drive the manager-wired CronTick at one deterministic due identity."""
    controller = manager._recurrence_controller
    assert controller is not None
    current = datetime.fromtimestamp(due_at, tz=UTC)
    controller._now = lambda: current
    await controller.handle_tick(
        CronTick(
            entry_name=schedule_id,
            score_path="ignored-by-registry.yaml",
            due_at=due_at,
            timestamp=due_at,
        )
    )


@pytest.mark.adversarial
async def test_corrupt_schedule_row_is_disabled_without_blocking_restart(
    tmp_path: Path,
) -> None:
    """One corrupt row is loud and disabled; it cannot prevent recovery of the manager."""
    score_path = tmp_path / "score.yaml"
    _write_score(score_path, tmp_path / "workspace")

    manager = _manager(tmp_path)
    await manager.start()
    try:
        response = await manager.submit_job(JobRequest(config_path=score_path))
        assert response.status == "accepted"
        await _wait_for_terminal(manager, response.job_id)
    finally:
        await manager.shutdown(graceful=False)

    db_path = tmp_path / "conductor-state.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE schedules SET schedule_json = ? WHERE schedule_id = ?",
            ('{"interval":"not-a-duration"}', "corrupt-runtime-score"),
        )
        connection.commit()

    restarted = _manager(tmp_path)
    await restarted.start()
    try:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT enabled, last_outcome FROM schedules WHERE schedule_id = ?",
                ("corrupt-runtime-score",),
            ).fetchone()
        assert row == (0, "registry_data_error")
        assert restarted._recurrence_controller is not None
        assert restarted._recurrence_controller._timers == {}
    finally:
        await restarted.shutdown(graceful=False)


@pytest.mark.adversarial
async def test_deleted_or_symlink_replaced_score_never_replays_old_due(
    tmp_path: Path,
) -> None:
    """A source mutation consumes one lease as removal or identity replacement."""
    score_path = tmp_path / "score.yaml"
    _write_score(score_path, tmp_path / "workspace", name="original-score")
    manager = _manager(tmp_path)
    await manager.start()
    try:
        response = await manager.submit_job(JobRequest(config_path=score_path))
        assert response.status == "accepted"
        record = await manager._schedule_registry.get("original-score")
        assert record is not None

        replacement = tmp_path / "replacement.yaml"
        _write_score(replacement, tmp_path / "replacement-workspace", name="replacement-score")
        score_path.unlink()
        score_path.symlink_to(replacement)
        await _tick(manager, "original-score", record.next_due_at)

        assert await manager._schedule_registry.get("original-score") is None
        changed = await manager._schedule_registry.get("replacement-score")
        assert changed is not None
        assert changed.score_path == score_path.resolve()
        assert changed.last_due_at is None
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.adversarial
async def test_deleted_score_disables_future_ticks_without_child_submission(
    tmp_path: Path,
) -> None:
    """Deleting the declared YAML removes its schedule after one claimed due time."""
    score_path = tmp_path / "deleted.yaml"
    _write_score(score_path, tmp_path / "workspace", name="deleted-score")
    manager = _manager(tmp_path)
    await manager.start()
    try:
        response = await manager.submit_job(JobRequest(config_path=score_path))
        record = await manager._schedule_registry.get("deleted-score")
        assert response.status == "accepted"
        assert record is not None
        score_path.unlink()
        await _tick(manager, "deleted-score", record.next_due_at)
        assert await manager._schedule_registry.get("deleted-score") is None
        assert not any("--scheduled--" in job_id for job_id in manager._job_meta)
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.adversarial
async def test_changed_schedule_replaces_one_projection_before_due_child(
    tmp_path: Path,
) -> None:
    """The due child uses the reread interval and one source-owned identity."""
    score_path = tmp_path / "changed.yaml"
    _write_score(score_path, tmp_path / "workspace", name="changed-score")
    manager = _manager(tmp_path)
    await manager.start()
    try:
        response = await manager.submit_job(JobRequest(config_path=score_path))
        record = await manager._schedule_registry.get("changed-score")
        assert response.status == "accepted"
        assert record is not None
        await _wait_for_terminal(manager, response.job_id)
        _write_score(
            score_path,
            tmp_path / "workspace",
            name="changed-score",
            schedule="  interval: 2h\n",
        )
        await _tick(manager, "changed-score", record.next_due_at)
        updated = await manager._schedule_registry.get("changed-score")
        assert updated is not None
        assert '"interval":"2h"' in updated.schedule_json
        assert updated.last_outcome == "submitted"
        assert updated.next_due_at == record.next_due_at + 7200.0
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.adversarial
async def test_concurrent_manual_run_skips_due_child_without_overlap(
    tmp_path: Path,
) -> None:
    """A real active manual runner makes its due child a recorded skip."""
    fifo = tmp_path / "hold.fifo"
    os.mkfifo(fifo)
    score_path = tmp_path / "overlap.yaml"
    _write_score(
        score_path,
        tmp_path / "workspace",
        name="overlap-score",
        command=f"read _ < {fifo}",
    )
    manager = _manager(tmp_path)
    await manager.start()
    try:
        response = await manager.submit_job(JobRequest(config_path=score_path))
        record = await manager._schedule_registry.get("overlap-score")
        assert response.status == "accepted"
        assert record is not None
        async with asyncio.timeout(8.0):
            while manager._job_meta[response.job_id].status is DaemonJobStatus.QUEUED:
                await asyncio.sleep(0)
        await _tick(manager, "overlap-score", record.next_due_at)
        skipped = await manager._schedule_registry.get("overlap-score")
        assert skipped is not None
        assert skipped.last_outcome == "overlap_skipped"
        assert skipped.last_run_id is None
        assert not any("--scheduled--" in job_id for job_id in manager._job_meta)
    finally:
        await manager.shutdown(graceful=False)


class _Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


@pytest.mark.adversarial
async def test_expired_score_deadline_fails_before_cli_dispatch(tmp_path: Path) -> None:
    """A child that waits behind admission consumes its original wall deadline."""
    score_path = tmp_path / "deadline.yaml"
    marker = tmp_path / "must-not-exist"
    _write_score(
        score_path,
        tmp_path / "workspace",
        name="deadline-score",
        command=f"touch {marker}",
        max_wall_seconds=60.0,
    )
    clock = _Clock(1000.0)
    config = DaemonConfig(
        max_concurrent_jobs=1,
        pid_file=tmp_path / "conductor.pid",
        state_db_path=tmp_path / "conductor-state.db",
    )
    manager = JobManager(config, wall_clock=clock, monotonic_clock=clock)
    await manager.start()
    try:
        response = await manager.submit_job(JobRequest(config_path=score_path))
        clock.value = 1061.0
        await _wait_for_terminal(manager, response.job_id)
        record = await manager._registry.get_job(response.job_id)
        assert record is not None
        assert record.terminal_reason == "timed_out"
        assert marker.exists() is False
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.adversarial
async def test_downtime_latest_and_pending_child_collapse_to_one_action(
    tmp_path: Path,
) -> None:
    """Latest recovery and pending admission retain one exact child identity."""
    score_path = tmp_path / "latest.yaml"
    _write_score(
        score_path,
        tmp_path / "workspace",
        name="latest-score",
        schedule="  interval: 1h\n  misfire: latest\n",
    )
    manager = _manager(tmp_path)
    await manager.start()
    try:
        response = await manager.submit_job(JobRequest(config_path=score_path))
        await _wait_for_terminal(manager, response.job_id)
        record = await manager._schedule_registry.get("latest-score")
        controller = manager._recurrence_controller
        assert record is not None
        assert controller is not None
        controller._now = lambda: datetime.fromtimestamp(
            record.next_due_at + 8 * 3600, tz=UTC
        )
        await controller.restore()
        recovered = await manager._schedule_registry.get("latest-score")
        children = [job_id for job_id in manager._job_meta if "--scheduled--" in job_id]
        assert recovered is not None
        assert recovered.last_outcome == "submitted"
        assert len(children) == 1

        pending = await manager._queue_pending_job(
            JobRequest(
                config_path=score_path,
                job_id="latest-score--scheduled--pending",
                schedule_id="latest-score",
                scheduled_due_at=recovered.next_due_at,
                fresh=True,
            )
        )
        assert pending.status == "pending"
        assert manager._job_meta[pending.job_id].status is DaemonJobStatus.PENDING
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.adversarial
async def test_timezone_cron_keeps_dst_due_in_declared_zone(tmp_path: Path) -> None:
    """Registration calculates a Melbourne cron due from the declared timezone."""
    score_path = tmp_path / "dst.yaml"
    _write_score(
        score_path,
        tmp_path / "workspace",
        name="dst-score",
        schedule="  cron: '30 3 * * *'\n  timezone: Australia/Melbourne\n",
    )
    manager = _manager(tmp_path)
    await manager.start()
    try:
        controller = manager._recurrence_controller
        assert controller is not None
        controller._now = lambda: datetime(2026, 10, 3, 16, 0, tzinfo=UTC)
        response = await manager.submit_job(JobRequest(config_path=score_path))
        record = await manager._schedule_registry.get("dst-score")
        assert response.status == "accepted"
        assert record is not None
        assert record.next_due_at == datetime(2026, 10, 3, 16, 30, tzinfo=UTC).timestamp()
    finally:
        await manager.shutdown(graceful=False)
