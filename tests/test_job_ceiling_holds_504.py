"""GH #504: the conductor ceiling charges RUNNING time only.

``job_timeout_seconds`` is documented as accumulated RUNNING time: a pause
or a FERMATA hold does not spend it. The wrapper's plain ``wait_for`` bound
counted wall-clock from activation, so the spec-forge memory commission,
PAUSED in FERMATA for 23 h 54 m, was failed at 24 h as "exceeded timeout".
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta


class MutableClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _manager(tmp_path: Path, clock: MutableClock, ceiling: float) -> JobManager:
    JobManager._HOLD_AWARE_SAMPLE_SECONDS = 0.05
    config = DaemonConfig(
        state_db_path=tmp_path / "jobs.db",
        observer={"enabled": False},
        learning={"enabled": False},
    ).model_copy(update={"job_timeout_seconds": ceiling})
    return JobManager(config, wall_clock=clock, monotonic_clock=clock)


@pytest.mark.asyncio
async def test_a_paused_job_outlives_the_ceiling(tmp_path: Path) -> None:
    """Held longer than the ceiling, then resumed: the job completes."""
    clock = MutableClock()
    manager = _manager(tmp_path, clock, ceiling=100.0)
    await manager._registry.open()
    try:
        meta = JobMeta(job_id="held", config_path=tmp_path / "s.yaml", workspace=tmp_path / "w")
        manager._job_meta["held"] = meta
        await manager._registry.register_job("held", tmp_path / "s.yaml", tmp_path / "w")
        released = asyncio.Event()

        async def execution() -> None:
            # Run 10 s, hold for 500 s (five ceilings), run 10 s more.
            clock.value += 10.0
            await manager._set_job_status("held", DaemonJobStatus.PAUSED)
            clock.value += 500.0
            await released.wait()
            await manager._set_job_status("held", DaemonJobStatus.RUNNING)
            clock.value += 10.0

        task = asyncio.create_task(manager._run_managed_task("held", execution()))
        # Let the watchdog sample during the hold.
        for _ in range(5):
            await asyncio.sleep(0)
        released.set()
        await asyncio.wait_for(task, timeout=5.0)

        assert meta.status is DaemonJobStatus.COMPLETED, meta.error_message
        assert meta.running_seconds == pytest.approx(20.0)
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_a_running_job_still_hits_the_ceiling(tmp_path: Path) -> None:
    clock = MutableClock()
    manager = _manager(tmp_path, clock, ceiling=100.0)
    await manager._registry.open()
    try:
        meta = JobMeta(job_id="runs", config_path=tmp_path / "s.yaml", workspace=tmp_path / "w")
        manager._job_meta["runs"] = meta
        await manager._registry.register_job("runs", tmp_path / "s.yaml", tmp_path / "w")

        async def execution() -> None:
            clock.value += 150.0  # past the ceiling while RUNNING
            await asyncio.Event().wait()

        await asyncio.wait_for(manager._run_managed_task("runs", execution()), timeout=5.0)
        assert meta.status is DaemonJobStatus.FAILED
        assert meta.terminal_reason == "timed_out"
        assert "exceeded timeout" in (meta.error_message or "").lower()
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_resumed_charge_counts_prior_running_time(tmp_path: Path) -> None:
    """A job that already spent 90 s RUNNING before a restart has 10 s left."""
    clock = MutableClock()
    manager = _manager(tmp_path, clock, ceiling=100.0)
    await manager._registry.open()
    try:
        meta = JobMeta(
            job_id="resumed", config_path=tmp_path / "s.yaml", workspace=tmp_path / "w",
            running_seconds=90.0,
        )
        manager._job_meta["resumed"] = meta
        await manager._registry.register_job("resumed", tmp_path / "s.yaml", tmp_path / "w")

        async def execution() -> None:
            clock.value += 30.0
            await asyncio.Event().wait()

        await asyncio.wait_for(manager._run_managed_task("resumed", execution()), timeout=5.0)
        assert meta.status is DaemonJobStatus.FAILED
        assert meta.terminal_reason == "timed_out"
    finally:
        await manager._registry.close()
