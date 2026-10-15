"""A scheduled tick window must not turn into a one-shot score deadline."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from marianne.core.config import JobConfig
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import JobManager
from marianne.daemon.registry import DaemonJobStatus
from marianne.daemon.types import JobRequest
from marianne.validation.checks.reserved import DeprecatedSpellingCheck


def _write_score(path: Path, *, window: str) -> None:
    path.write_text(
        "name: tick-window-proof\n"
        "workspace: ./output\n"
        "sheet: {size: 1, total_items: 1}\n"
        "prompt: {template: hi}\n"
        + window,
        encoding="utf-8",
    )


def test_tick_window_moves_with_legacy_alias_and_warning(tmp_path: Path) -> None:
    score = tmp_path / "tick.yaml"
    _write_score(score, window="schedule: {interval: 1h}\nmax_wall_seconds: 60\n")
    legacy = JobConfig.from_yaml(score)
    assert legacy.tick_wall_seconds == 60
    issues = DeprecatedSpellingCheck().check(legacy, score, score.read_text())
    assert len(issues) == 1
    assert "schedule.max_wall_seconds" in issues[0].message
    assert "instrument_config.timeout_seconds" in issues[0].message
    assert "stale_detection" in issues[0].message

    _write_score(score, window="schedule: {interval: 1h, max_wall_seconds: 120}\n")
    current = JobConfig.from_yaml(score)
    assert current.tick_wall_seconds == 120


@pytest.mark.asyncio
async def test_one_shot_root_alias_has_no_deadline_at_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    score = tmp_path / "one-shot.yaml"
    score.write_text(
        "name: one-shot-window-proof\nworkspace: ./output\n"
        "sheet: {size: 1, total_items: 1}\nprompt: {template: hi}\n"
        "max_wall_seconds: 60\n",
        encoding="utf-8",
    )
    manager = JobManager(
        DaemonConfig(state_db_path=tmp_path / "jobs.db", observer={"enabled": False}),
    )
    await manager._registry.open()

    async def finish(_job_id: str, _request: JobRequest) -> None:
        return None

    monkeypatch.setattr(manager, "_run_job_task", finish)
    try:
        result = await manager.submit_job(JobRequest(config_path=score, job_id="one-shot"))
        assert result.status == "accepted"
        await manager._jobs["one-shot"]
        record = await manager._registry.get_job("one-shot")
        assert record is not None
        assert record.max_wall_seconds is None
        assert record.wall_deadline_at is None
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_daemon_budget_counts_running_intervals_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Clock:
        value = 1000.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    config = DaemonConfig(
        state_db_path=tmp_path / "jobs.db", observer={"enabled": False}
    ).model_copy(update={"job_timeout_seconds": 100.0})
    manager = JobManager(config, wall_clock=clock, monotonic_clock=clock)
    await manager._registry.open()
    score = tmp_path / "score.yaml"
    score.write_text(
        "name: running-budget\nworkspace: ./output\n"
        "sheet: {size: 1, total_items: 1}\nprompt: {template: hi}\n",
        encoding="utf-8",
    )
    selected: list[float | None] = []
    original_wait_for = asyncio.wait_for

    async def capture_wait(coro: object, timeout: float | None) -> object:
        selected.append(timeout)
        return await original_wait_for(coro, timeout)

    monkeypatch.setattr("marianne.daemon.manager.asyncio.wait_for", capture_wait)
    held = asyncio.Event()

    async def hold_run(_job_id: str, _request: JobRequest) -> None:
        await held.wait()

    monkeypatch.setattr(manager, "_run_job_task", hold_run)
    try:
        result = await manager.submit_job(JobRequest(config_path=score, job_id="budget"))
        assert result.status == "accepted"
        # Replace only the job task at this seam; each managed execution uses
        # the same persistent registration and a deterministic clock.
        manager._jobs["budget"].cancel()
        try:
            await manager._jobs["budget"]
        except asyncio.CancelledError:
            pass
        await manager._set_job_status("budget", DaemonJobStatus.QUEUED)

        async def first_run() -> DaemonJobStatus:
            clock.value += 30.0
            return DaemonJobStatus.PAUSED

        await manager._run_managed_task("budget", first_run())
        record = await manager._registry.get_job("budget")
        assert record is not None and record.running_seconds == 30.0

        clock.value += 10_000.0  # pause and conductor downtime
        await manager._set_job_status("budget", DaemonJobStatus.QUEUED)

        async def second_run() -> DaemonJobStatus:
            clock.value += 5.0
            return DaemonJobStatus.COMPLETED

        await manager._run_managed_task("budget", second_run())
        record = await manager._registry.get_job("budget")
        assert selected[-2:] == [100.0, 70.0]
        assert record is not None and record.running_seconds == 35.0
    finally:
        held.set()
        await manager._registry.close()
