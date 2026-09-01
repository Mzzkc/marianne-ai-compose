"""Fix-round boundaries for coherent and observable wall-deadline custody."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine, Generator
from pathlib import Path
from types import TracebackType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.checkpoint_writer import CheckpointWriter
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.types import JobRequest


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class CloseCountingCoroutine(Coroutine[Any, Any, DaemonJobStatus | None]):
    """Expose exact ownership while delegating to a real coroutine."""

    def __init__(
        self,
        coro: Coroutine[Any, Any, DaemonJobStatus | None],
    ) -> None:
        self.coro = coro
        self.close_calls = 0

    def __await__(self) -> Generator[Any, None, DaemonJobStatus | None]:
        return self

    def send(self, value: Any) -> Any:
        return self.coro.send(value)

    def throw(
        self,
        typ: type[BaseException] | BaseException,
        val: BaseException | None = None,
        tb: TracebackType | None = None,
    ) -> Any:
        if val is None:
            return self.coro.throw(typ)
        if tb is None:
            return self.coro.throw(typ, val)
        return self.coro.throw(typ, val, tb)

    def close(self) -> None:
        self.close_calls += 1
        self.coro.close()


class ObserverProbe:
    def __init__(self) -> None:
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


def _manager(
    tmp_path: Path,
    clock: MutableClock,
    *,
    observer_enabled: bool = False,
) -> JobManager:
    config = DaemonConfig(
        state_db_path=tmp_path / "jobs.db",
        observer={"enabled": observer_enabled},
        learning={"enabled": False},
    ).model_copy(update={"job_timeout_seconds": 500.0})
    return JobManager(config, wall_clock=clock, monotonic_clock=clock)


def _write_score(path: Path, workspace: Path, *, limit: float = 300.0) -> None:
    path.write_text(
        "name: Stable Deadline\n"
        f"workspace: {workspace}\n"
        "instrument: claude-code\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: deadline proof\n"
        f"max_wall_seconds: {limit}\n",
        encoding="utf-8",
    )


async def _capture_execution_timeout(
    manager: JobManager,
    monkeypatch: pytest.MonkeyPatch,
    job_id: str,
) -> float:
    selected: list[float | None] = []

    async def capture(coro: Any, timeout: float | None) -> Any:
        selected.append(timeout)
        return await coro

    monkeypatch.setattr("marianne.daemon.manager.asyncio.wait_for", capture)

    async def execute() -> None:
        return None

    await manager._run_managed_task(job_id, execute())
    assert len(selected) == 1
    assert selected[0] is not None
    return selected[0]


@pytest.mark.asyncio
async def test_terminal_fresh_resubmission_publishes_one_new_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(3_240.0)
    manager = _manager(tmp_path, clock)
    await manager._registry.open()
    score = tmp_path / "score.yaml"
    workspace = tmp_path / "workspace"
    _write_score(score, workspace)
    release = asyncio.Event()

    async def hold_run(_job_id: str, _request: JobRequest) -> None:
        await release.wait()

    monkeypatch.setattr(manager, "_run_job_task", hold_run)
    try:
        await manager._registry.register_job(
            "stable",
            score,
            workspace,
            submitted_at=3_000.0,
            max_wall_seconds=300.0,
        )
        await manager._registry.update_status(
            "stable",
            DaemonJobStatus.COMPLETED.value,
        )
        manager._job_meta["stable"] = JobMeta(
            job_id="stable",
            config_path=score,
            workspace=workspace,
            submitted_at=3_000.0,
            status=DaemonJobStatus.COMPLETED,
            max_wall_seconds=300.0,
            wall_deadline_at=3_300.0,
        )

        response = await manager.submit_job(
            JobRequest(config_path=score, job_id="stable", fresh=True),
        )
        record = await manager._registry.get_job("stable")
        meta = manager._job_meta["stable"]
        status = await manager.get_job_status("stable")

        assert response.status == "accepted"
        assert record is not None
        assert record.submitted_at == meta.submitted_at == 3_240.0
        assert record.wall_deadline_at == meta.wall_deadline_at == 3_540.0
        assert status["submitted_at"] == 3_240.0
        assert status["wall_deadline_at"] == 3_540.0

        task = manager._jobs.pop("stable")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        meta.status = DaemonJobStatus.QUEUED
        clock.value = 3_250.0
        selected = await _capture_execution_timeout(manager, monkeypatch, "stable")
        assert selected == 290.0
    finally:
        release.set()
        for task in manager._jobs.values():
            task.cancel()
        await manager._registry.close()


@pytest.mark.asyncio
async def test_rejected_active_duplicate_changes_no_authority(
    tmp_path: Path,
) -> None:
    clock = MutableClock(4_240.0)
    manager = _manager(tmp_path, clock)
    await manager._registry.open()
    score = tmp_path / "score.yaml"
    workspace = tmp_path / "workspace"
    _write_score(score, workspace)
    try:
        await manager._registry.register_job(
            "active",
            score,
            workspace,
            submitted_at=4_000.0,
            max_wall_seconds=300.0,
        )
        meta = JobMeta(
            job_id="active",
            config_path=score,
            workspace=workspace,
            submitted_at=4_000.0,
            status=DaemonJobStatus.RUNNING,
            max_wall_seconds=300.0,
            wall_deadline_at=4_300.0,
        )
        manager._job_meta["active"] = meta

        response = await manager.submit_job(
            JobRequest(config_path=score, job_id="active", fresh=True),
        )
        record = await manager._registry.get_job("active")

        assert response.status == "rejected"
        assert record is not None
        assert record.submitted_at == meta.submitted_at == 4_000.0
        assert record.wall_deadline_at == meta.wall_deadline_at == 4_300.0
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_pending_autostart_uses_first_accepted_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(5_000.0)
    manager = _manager(tmp_path, clock)
    await manager._registry.open()
    score = tmp_path / "score.yaml"
    workspace = tmp_path / "workspace"
    _write_score(score, workspace)
    manager._rate_coordinator = MagicMock(active_limits={})
    manager._backpressure = MagicMock()
    manager._backpressure.should_accept_job.return_value = True
    selected: list[float | None] = []

    async def capture(coro: Any, timeout: float | None) -> Any:
        selected.append(timeout)
        return await coro

    async def run_pending(job_id: str, _request: JobRequest) -> None:
        async def execute() -> None:
            return None

        await manager._run_managed_task(job_id, execute())

    monkeypatch.setattr("marianne.daemon.manager.asyncio.wait_for", capture)
    monkeypatch.setattr(manager, "_run_job_task", run_pending)
    try:
        response = await manager._queue_pending_job(
            JobRequest(config_path=score, job_id="pending", fresh=True),
        )
        record = await manager._registry.get_job("pending")
        assert response.status == "pending"
        assert record is not None
        assert record.wall_deadline_at == 5_300.0
        assert manager._job_meta["pending"].wall_deadline_at == 5_300.0

        clock.value = 5_080.0
        await manager._start_pending_jobs()
        await manager._jobs["pending"]

        restarted = await manager._registry.get_job("pending")
        assert selected == [220.0]
        assert restarted is not None
        assert restarted.wall_deadline_at == 5_300.0
        assert manager._job_meta["pending"].wall_deadline_at == 5_300.0
    finally:
        await manager._registry.close()


async def _install_queued_job(
    manager: JobManager,
    *,
    job_id: str,
    submitted_at: float,
    deadline: float,
) -> JobMeta:
    await manager._registry.register_job(
        job_id,
        Path("/tmp/score.yaml"),
        Path("/tmp/workspace"),
        submitted_at=submitted_at,
        max_wall_seconds=deadline - submitted_at,
    )
    meta = JobMeta(
        job_id=job_id,
        config_path=Path("/tmp/score.yaml"),
        workspace=Path("/tmp/workspace"),
        submitted_at=submitted_at,
        max_wall_seconds=deadline - submitted_at,
        wall_deadline_at=deadline,
    )
    manager._job_meta[job_id] = meta
    return meta


@pytest.mark.asyncio
async def test_observer_start_crossing_deadline_blocks_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(6_000.0)
    manager = _manager(tmp_path, clock, observer_enabled=True)
    await manager._registry.open()
    entered = asyncio.Event()
    release = asyncio.Event()
    executed = False
    probe = ObserverProbe()
    recorder = MagicMock()
    manager._observer_recorder = recorder
    meta = await _install_queued_job(
        manager,
        job_id="observer-cross",
        submitted_at=5_990.0,
        deadline=6_010.0,
    )

    async def held_start(_job_id: str) -> None:
        meta.observer = probe  # type: ignore[assignment]
        entered.set()
        await release.wait()

    async def execute() -> None:
        nonlocal executed
        executed = True

    monkeypatch.setattr(manager, "_start_observer", held_start)
    task = asyncio.create_task(manager._run_managed_task(meta.job_id, execute()))
    try:
        await entered.wait()
        clock.value = 6_011.0
        release.set()
        await task

        assert executed is False
        assert meta.status is DaemonJobStatus.FAILED
        assert meta.terminal_reason == "timed_out"
        assert probe.stop_calls == 1
        recorder.unregister_job.assert_called_once_with(meta.job_id)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await manager._registry.close()


@pytest.mark.asyncio
async def test_observer_start_timeout_closes_unstarted_execution_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(7_000.0)
    manager = _manager(tmp_path, clock, observer_enabled=True)
    await manager._registry.open()
    entered = asyncio.Event()
    timeout_delivered = asyncio.Event()
    probe = ObserverProbe()
    recorder = MagicMock()
    manager._observer_recorder = recorder
    meta = await _install_queued_job(
        manager,
        job_id="observer-hang",
        submitted_at=6_990.0,
        deadline=7_010.0,
    )
    executed = False

    async def never_starts(_job_id: str) -> None:
        meta.observer = probe  # type: ignore[assignment]
        entered.set()
        await asyncio.Event().wait()

    async def deterministic_timeout(coro: Any, timeout: float | None) -> Any:
        assert timeout == 10.0
        child = asyncio.create_task(coro)
        await entered.wait()
        clock.value = 7_010.0
        child.cancel()
        with pytest.raises(asyncio.CancelledError):
            await child
        timeout_delivered.set()
        raise TimeoutError

    async def execute() -> None:
        nonlocal executed
        executed = True

    monkeypatch.setattr(manager, "_start_observer", never_starts)
    monkeypatch.setattr(
        "marianne.daemon.manager.asyncio.wait_for",
        deterministic_timeout,
    )
    execution_coro = execute()
    counted = CloseCountingCoroutine(execution_coro)
    managed_task = asyncio.create_task(
        manager._run_managed_task(meta.job_id, counted),
    )
    try:
        async with asyncio.timeout(0.5):
            await timeout_delivered.wait()
        await managed_task

        assert executed is False
        assert counted.close_calls == 1
        assert execution_coro.cr_frame is None
        assert meta.status is DaemonJobStatus.FAILED
        assert meta.terminal_reason == "timed_out"
        assert probe.stop_calls == 1
        recorder.unregister_job.assert_called_once_with(meta.job_id)
    finally:
        if not managed_task.done():
            managed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await managed_task
        await manager._registry.close()


@pytest.mark.asyncio
async def test_timeout_checkpoint_write_failure_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(8_100.0)
    manager = _manager(tmp_path, clock)
    await manager._registry.open()
    meta = await _install_queued_job(
        manager,
        job_id="durability-failure",
        submitted_at=8_000.0,
        deadline=8_100.0,
    )
    manager._live_states[meta.job_id] = CheckpointState(
        job_id=meta.job_id,
        job_name=meta.job_id,
        total_sheets=1,
        max_wall_seconds=100.0,
        wall_deadline_at=8_100.0,
    )
    await manager._registry.save_checkpoint(
        meta.job_id,
        manager._live_states[meta.job_id].model_dump_json(),
    )

    async def fail_save(_job_id: str, _payload: str) -> None:
        raise RuntimeError("terminal checkpoint unavailable")

    monkeypatch.setattr(manager._registry, "save_checkpoint", fail_save)
    writer = CheckpointWriter(manager._registry)
    writer.start()
    manager._checkpoint_writer = writer

    async def must_not_run() -> None:
        raise AssertionError("expired work dispatched")

    try:
        with pytest.raises(RuntimeError, match="terminal checkpoint unavailable"):
            await manager._run_managed_task(meta.job_id, must_not_run())
        raw = await manager._registry.load_checkpoint(meta.job_id)
        assert raw is not None
        assert CheckpointState.model_validate_json(raw).terminal_reason is None
    finally:
        await writer.stop()
        await manager._registry.close()


@pytest.mark.asyncio
async def test_status_refreshes_matured_post_grace_cleanup_evidence(
    tmp_path: Path,
) -> None:
    clock = MutableClock(9_000.0)
    manager = _manager(tmp_path, clock)
    await manager._registry.open()
    adapter = BatonAdapter()
    adapter._active_pids[("mature", 1)] = (100, 200)
    manager._baton_adapter = adapter
    callbacks: list[Any] = []
    loop = asyncio.get_running_loop()
    meta = JobMeta(
        job_id="mature",
        config_path=Path("/tmp/score.yaml"),
        workspace=Path("/tmp/workspace"),
        submitted_at=8_900.0,
        status=DaemonJobStatus.FAILED,
        terminal_reason="timed_out",
    )
    manager._job_meta[meta.job_id] = meta

    def capture_later(_delay: float, callback: Any) -> MagicMock:
        callbacks.append(callback)
        return MagicMock()

    def guarded_signal(_pgid: int, sig: int, *, context: str) -> bool:
        assert context.startswith("adapter.deregister_")
        if sig == 0:
            raise ProcessLookupError
        return True

    try:
        with patch(
            "marianne.daemon.baton.adapter._safe_killpg",
            side_effect=guarded_signal,
        ), patch(
            "marianne.daemon.baton.adapter.os.getpgid",
            return_value=999,
        ), patch.object(
            loop,
            "call_later",
            side_effect=capture_later,
        ), patch.object(adapter, "_kill_interactive_sessions"):
            meta.timeout_cleanup_outcome = manager._timeout_cleanup_via_baton(
                meta.job_id,
            )
            assert meta.timeout_cleanup_outcome.escalation_state == "pending"
            assert len(callbacks) == 1
            callbacks[0]()

        status = await manager.get_job_status(meta.job_id)
        cleanup = status["deadline"]["cleanup_outcome"]
        assert cleanup["escalation_state"] == "performed"
        assert cleanup["sigkill_attempted"] == 1
        assert cleanup["residual_check_state"] == "clear"
        assert cleanup["residual_process_groups"] == 0
    finally:
        await manager._registry.close()
