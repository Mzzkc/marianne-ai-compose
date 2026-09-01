"""Fix-round lifecycle races for deadline durability and cleanup evidence."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.types import JobRequest


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


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


def _write_score(path: Path, workspace: Path) -> None:
    path.write_text(
        "name: Generation Deadline\n"
        f"workspace: {workspace}\n"
        "instrument: claude-code\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: generation proof\n"
        "max_wall_seconds: 60\n",
        encoding="utf-8",
    )


async def _install_terminal_execution(
    manager: JobManager,
    *,
    job_id: str,
    score: Path,
    workspace: Path,
    submitted_at: float,
) -> None:
    await manager._registry.register_job(
        job_id,
        score,
        workspace,
        submitted_at=submitted_at,
        max_wall_seconds=60.0,
    )
    await manager._registry.update_status(
        job_id,
        DaemonJobStatus.COMPLETED.value,
    )
    manager._job_meta[job_id] = JobMeta(
        job_id=job_id,
        config_path=score,
        workspace=workspace,
        submitted_at=submitted_at,
        status=DaemonJobStatus.COMPLETED,
        max_wall_seconds=60.0,
        wall_deadline_at=submitted_at + 60.0,
    )


def _mature_old_cleanup(adapter: BatonAdapter, job_id: str) -> None:
    callbacks: list[Any] = []
    loop = asyncio.get_running_loop()
    adapter._active_pids[(job_id, 1)] = (100, 200)

    def guarded_signal(_pgid: int, sig: int, *, context: str) -> bool:
        assert context.startswith("adapter.deregister_")
        if sig == 0:
            raise ProcessLookupError
        return True

    def capture_later(_delay: float, callback: Any) -> MagicMock:
        callbacks.append(callback)
        return MagicMock()

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
        old = adapter.deregister_job(job_id)
        assert old.tracked_process_groups == 1
        assert len(callbacks) == 1
        callbacks[0]()
        assert old.residual_check_state == "clear"


def _assert_current_empty_cleanup(manager: JobManager, job_id: str) -> None:
    meta = manager._job_meta[job_id]
    cleanup = meta.timeout_cleanup_outcome
    assert cleanup is not None
    assert cleanup.tracked_process_groups == 0
    assert cleanup.sigterm_attempted == 0
    assert cleanup.sigkill_attempted == 0
    assert cleanup.residual_check_state == "unverified"
    assert cleanup.cleanup_generation == meta.cleanup_generation


@pytest.mark.asyncio
async def test_fresh_stable_id_expiry_while_concurrency_queued_is_generation_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(1_000.0)
    manager = _manager(tmp_path, clock)
    await manager._registry.open()
    adapter = BatonAdapter()
    manager._baton_adapter = adapter
    score = tmp_path / "score.yaml"
    workspace = tmp_path / "workspace"
    _write_score(score, workspace)
    await _install_terminal_execution(
        manager,
        job_id="stable-queued",
        score=score,
        workspace=workspace,
        submitted_at=900.0,
    )
    _mature_old_cleanup(adapter, "stable-queued")
    gate = manager._concurrency_semaphore
    gate.set_limit(1)
    await gate.acquire()
    executed = False

    async def run_managed(job_id: str, _request: JobRequest) -> None:
        async def execute() -> None:
            nonlocal executed
            executed = True

        await manager._run_managed_task(job_id, execute())

    monkeypatch.setattr(manager, "_run_job_task", run_managed)
    try:
        response = await manager.submit_job(
            JobRequest(config_path=score, job_id="stable-queued", fresh=True),
        )
        assert response.status == "accepted"
        clock.value = 1_061.0
        gate.release()
        await manager._jobs["stable-queued"]

        assert executed is False
        _assert_current_empty_cleanup(manager, "stable-queued")
        status = await manager.get_job_status("stable-queued")
        public_cleanup = status["deadline"]["cleanup_outcome"]
        assert public_cleanup["tracked_process_groups"] == 0
        assert public_cleanup["cleanup_generation"] == (
            manager._job_meta["stable-queued"].cleanup_generation
        )
    finally:
        if gate.acquired:
            gate.release()
        for task in manager._jobs.values():
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await manager._registry.close()


@pytest.mark.asyncio
async def test_pending_stable_id_expiry_is_generation_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(2_000.0)
    manager = _manager(tmp_path, clock)
    await manager._registry.open()
    adapter = BatonAdapter()
    manager._baton_adapter = adapter
    score = tmp_path / "score.yaml"
    workspace = tmp_path / "workspace"
    _write_score(score, workspace)
    await _install_terminal_execution(
        manager,
        job_id="stable-pending",
        score=score,
        workspace=workspace,
        submitted_at=1_900.0,
    )
    _mature_old_cleanup(adapter, "stable-pending")
    manager._rate_coordinator = MagicMock(active_limits={})
    manager._backpressure = MagicMock()
    manager._backpressure.should_accept_job.return_value = True
    executed = False

    async def run_managed(job_id: str, _request: JobRequest) -> None:
        async def execute() -> None:
            nonlocal executed
            executed = True

        await manager._run_managed_task(job_id, execute())

    monkeypatch.setattr(manager, "_run_job_task", run_managed)
    try:
        response = await manager._queue_pending_job(
            JobRequest(config_path=score, job_id="stable-pending", fresh=True),
        )
        assert response.status == "pending"
        clock.value = 2_061.0
        await manager._start_pending_jobs()
        await manager._jobs["stable-pending"]

        assert executed is False
        _assert_current_empty_cleanup(manager, "stable-pending")
    finally:
        await manager._registry.close()


@pytest.mark.asyncio
async def test_fresh_stable_id_expiry_during_observer_start_is_generation_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(3_000.0)
    manager = _manager(tmp_path, clock, observer_enabled=True)
    await manager._registry.open()
    adapter = BatonAdapter()
    manager._baton_adapter = adapter
    manager._observer_recorder = MagicMock()
    score = tmp_path / "score.yaml"
    workspace = tmp_path / "workspace"
    _write_score(score, workspace)
    await _install_terminal_execution(
        manager,
        job_id="stable-observer",
        score=score,
        workspace=workspace,
        submitted_at=2_900.0,
    )
    _mature_old_cleanup(adapter, "stable-observer")
    entered = asyncio.Event()
    release = asyncio.Event()
    observer = AsyncMock()
    executed = False

    async def held_observer(job_id: str) -> None:
        manager._job_meta[job_id].observer = observer
        entered.set()
        await release.wait()

    async def run_managed(job_id: str, _request: JobRequest) -> None:
        async def execute() -> None:
            nonlocal executed
            executed = True

        await manager._run_managed_task(job_id, execute())

    monkeypatch.setattr(manager, "_start_observer", held_observer)
    monkeypatch.setattr(manager, "_run_job_task", run_managed)
    try:
        response = await manager.submit_job(
            JobRequest(config_path=score, job_id="stable-observer", fresh=True),
        )
        assert response.status == "accepted"
        await entered.wait()
        clock.value = 3_061.0
        release.set()
        await manager._jobs["stable-observer"]

        assert executed is False
        _assert_current_empty_cleanup(manager, "stable-observer")
        observer.stop.assert_awaited_once()
    finally:
        release.set()
        await manager._registry.close()


@pytest.mark.asyncio
async def test_old_grace_callback_cannot_overwrite_new_generation() -> None:
    adapter = BatonAdapter()
    old_generation = adapter.begin_cleanup_generation("race")
    adapter._active_pids[("race", 1)] = (100, 200)
    callbacks: list[Any] = []
    loop = asyncio.get_running_loop()

    def capture_later(_delay: float, callback: Any) -> MagicMock:
        callbacks.append(callback)
        return MagicMock()

    def guarded_signal(_pgid: int, sig: int, *, context: str) -> bool:
        assert context.startswith("adapter.deregister_")
        if sig == 0:
            raise ProcessLookupError
        return True

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
    ), patch.object(
        adapter,
        "_kill_interactive_sessions",
    ), structlog.testing.capture_logs() as captured_logs:
        old = adapter.deregister_job("race")
        assert old.cleanup_generation == old_generation
        assert old.escalation_state == "pending"
        new_generation = adapter.begin_cleanup_generation("race")
        current = adapter.deregister_job("race")
        assert current.cleanup_generation == new_generation
        assert current.tracked_process_groups == 0
        callbacks[0]()

    assert old.escalation_state == "performed"
    assert old.residual_check_state == "clear"
    assert adapter.get_process_group_cleanup_result("race", new_generation) is None
    assert current.escalation_state == "not_needed"
    assert adapter._cleanup_generations == {}
    assert adapter._cleanup_results == {}
    verification = [
        log
        for log in captured_logs
        if log.get("event") == "adapter.deregister.cleanup_verified"
    ]
    assert verification[0]["extra"]["cleanup_generation"] == old_generation


def test_terminal_cleanup_tracking_is_pruned_before_repeated_deregister() -> None:
    adapter = BatonAdapter()
    generation = adapter.begin_cleanup_generation("double")
    adapter._active_pids[("double", 1)] = (100, 200)

    with patch(
        "marianne.daemon.baton.adapter._safe_killpg",
        return_value=False,
    ), patch(
        "marianne.daemon.baton.adapter.os.getpgid",
        return_value=999,
    ), patch.object(adapter, "_kill_interactive_sessions"):
        first = adapter.deregister_job("double")
        second = adapter.deregister_job("double")

    assert first.cleanup_generation == generation
    assert first.tracked_process_groups == 1
    assert first.sigterm_failed == 1
    assert second.cleanup_generation != generation
    assert second.tracked_process_groups == 0
    assert adapter._cleanup_generations == {}
    assert adapter._cleanup_results == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_state", ["unavailable", "failed"])
async def test_cleanup_fallback_cannot_refresh_from_unrelated_generation(
    tmp_path: Path,
    fallback_state: str,
) -> None:
    clock = MutableClock(4_000.0)
    manager = _manager(tmp_path, clock)
    await manager._registry.open()
    meta = JobMeta(
        job_id="fallback",
        config_path=Path("/tmp/score.yaml"),
        workspace=Path("/tmp/workspace"),
        submitted_at=3_900.0,
        status=DaemonJobStatus.FAILED,
        terminal_reason="timed_out",
        cleanup_generation="manager-current",
    )
    manager._job_meta[meta.job_id] = meta
    if fallback_state == "failed":
        failing_adapter = MagicMock()
        failing_adapter.deregister_job.side_effect = RuntimeError("cleanup failed")
        manager._baton_adapter = failing_adapter
    meta.timeout_cleanup_outcome = manager._timeout_cleanup_via_baton(meta.job_id)

    adapter = BatonAdapter()
    old_generation = adapter.begin_cleanup_generation(meta.job_id)
    assert old_generation != meta.cleanup_generation
    adapter._active_pids[(meta.job_id, 1)] = (100, 200)
    with patch(
        "marianne.daemon.baton.adapter._safe_killpg",
        return_value=False,
    ), patch(
        "marianne.daemon.baton.adapter.os.getpgid",
        return_value=999,
    ), patch.object(adapter, "_kill_interactive_sessions"):
        old = adapter.deregister_job(meta.job_id)
    assert old.tracked_process_groups == 1
    manager._baton_adapter = adapter

    try:
        status = await manager.get_job_status(meta.job_id)
        cleanup = status["deadline"]["cleanup_outcome"]
        assert cleanup["deregistration_state"] == fallback_state
        assert cleanup["cleanup_generation"] == meta.cleanup_generation
        assert cleanup["tracked_process_groups"] == 0
    finally:
        await manager._registry.close()
