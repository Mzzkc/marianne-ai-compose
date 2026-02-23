"""Tests for mozart.daemon.manager module.

Covers JobManager submit/cancel/shutdown, concurrency limit enforcement,
duplicate handling, _on_task_done cleanup, pause/resume, and job history
pruning.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozart.daemon.config import DaemonConfig
from mozart.daemon.exceptions import JobSubmissionError
from mozart.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from mozart.daemon.types import JobRequest

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def daemon_config(tmp_path: Path) -> DaemonConfig:
    """Create a DaemonConfig with low limits for testing."""
    return DaemonConfig(
        max_concurrent_jobs=2,
        pid_file=tmp_path / "test.pid",
        state_db_path=tmp_path / "test-registry.db",
    )


@pytest.fixture
async def manager(daemon_config: DaemonConfig) -> AsyncIterator[JobManager]:
    """Create a JobManager with mocked JobService and opened registry."""
    mgr = JobManager(daemon_config)
    await mgr._registry.open()
    mgr._service = MagicMock()
    yield mgr
    await mgr._registry.close()


@pytest.fixture
def sample_config_file(tmp_path: Path) -> Path:
    """Create a minimal config YAML file for submit tests."""
    config = tmp_path / "test-job.yaml"
    config.write_text(
        "name: test-job\n"
        "sheet:\n"
        "  size: 1\n"
        "  total_items: 1\n"
        "prompt:\n"
        "  template: test prompt\n"
    )
    return config


# ─── Submit Job ────────────────────────────────────────────────────────


class TestSubmitJob:
    """Tests for JobManager.submit_job()."""

    @pytest.mark.asyncio
    async def test_submit_returns_accepted(
        self, manager: JobManager, sample_config_file: Path, tmp_path: Path,
    ):
        """Submitting a valid job returns accepted response."""
        request = JobRequest(config_path=sample_config_file, workspace=tmp_path / "ws")
        response = await manager.submit_job(request)

        assert response.status == "accepted"
        assert response.job_id != ""
        assert "test-job" in response.job_id

    @pytest.mark.asyncio
    async def test_submit_creates_task(
        self, manager: JobManager, sample_config_file: Path, tmp_path: Path,
    ):
        """submit_job creates an asyncio.Task tracked in _jobs."""
        request = JobRequest(config_path=sample_config_file, workspace=tmp_path / "ws")
        response = await manager.submit_job(request)

        # Task was created (may have already completed/failed due to mock)
        assert response.job_id in manager._job_meta

    @pytest.mark.asyncio
    async def test_submit_nonexistent_config_rejected(
        self, manager: JobManager, tmp_path: Path,
    ):
        """Submitting a job with nonexistent config is rejected."""
        request = JobRequest(config_path=tmp_path / "nonexistent.yaml")
        response = await manager.submit_job(request)

        assert response.status == "rejected"
        assert "not found" in (response.message or "")

    @pytest.mark.asyncio
    async def test_submit_unparseable_config_without_workspace_rejected(
        self, manager: JobManager, tmp_path: Path,
    ):
        """Submitting a job with unparseable config and no workspace is rejected."""
        bad_config = tmp_path / "bad-job.yaml"
        bad_config.write_text("name: bad-job\n")
        request = JobRequest(config_path=bad_config)
        response = await manager.submit_job(request)

        assert response.status == "rejected"
        assert "Failed to parse" in (response.message or "")

    @pytest.mark.asyncio
    async def test_submit_during_shutdown_rejected(
        self, manager: JobManager, sample_config_file: Path,
    ):
        """Jobs submitted during shutdown are rejected."""
        manager._shutting_down = True
        request = JobRequest(config_path=sample_config_file)
        response = await manager.submit_job(request)

        assert response.status == "rejected"
        assert "shutting down" in (response.message or "").lower()

    @pytest.mark.asyncio
    async def test_submit_rejects_duplicate_active(
        self, manager: JobManager, sample_config_file: Path, tmp_path: Path,
    ):
        """Submitting same config while active is rejected."""
        r1 = JobRequest(config_path=sample_config_file, workspace=tmp_path / "ws1")
        r2 = JobRequest(config_path=sample_config_file, workspace=tmp_path / "ws2")

        resp1 = await manager.submit_job(r1)
        assert resp1.status == "accepted"

        resp2 = await manager.submit_job(r2)
        assert resp2.status == "rejected"


# ─── Concurrency ──────────────────────────────────────────────────────


class TestConcurrencyLimit:
    """Tests for semaphore-based concurrency enforcement."""

    @pytest.mark.asyncio
    async def test_semaphore_initial_value(self, manager: JobManager):
        """Semaphore is initialized with max_concurrent_jobs."""
        # DaemonConfig has max_concurrent_jobs=2
        assert manager._concurrency_semaphore._value == 2

    @pytest.mark.asyncio
    async def test_concurrency_limits_parallel_execution(self, daemon_config: DaemonConfig):
        """Only max_concurrent_jobs tasks can run simultaneously."""
        mgr = JobManager(daemon_config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            # Track concurrent execution count
            max_concurrent = 0
            current_concurrent = 0
            execution_started = asyncio.Event()

            async def slow_start_job(config, **kwargs):
                nonlocal max_concurrent, current_concurrent
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
                execution_started.set()
                await asyncio.sleep(0.05)
                current_concurrent -= 1

            mgr._service.start_job = slow_start_job

            # Submit 4 jobs (limit is 2)
            tasks = []
            for i in range(4):
                config_file = Path(f"/tmp/test-concurrency-{i}.yaml")
                with patch.object(Path, "exists", return_value=True):
                    request = JobRequest(config_path=config_file)
                    response = await mgr.submit_job(request)
                    tasks.append(response.job_id)

            # Wait for all tasks to complete
            await asyncio.sleep(0.3)

            # Max concurrent should not exceed the limit of 2
            assert max_concurrent <= 2
        finally:
            await mgr._registry.close()


# ─── Cancel Job ────────────────────────────────────────────────────────


class TestCancelJob:
    """Tests for JobManager.cancel_job()."""

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_false(self, manager: JobManager):
        """Cancelling a nonexistent job returns False."""
        result = await manager.cancel_job("nonexistent-job")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_existing_job(self, manager: JobManager):
        """Cancelling an existing job cancels the task and updates meta."""
        # Create a long-running task manually
        async def long_running():
            await asyncio.sleep(100)

        task = asyncio.create_task(long_running())
        manager._jobs["test-job-1"] = task
        manager._job_meta["test-job-1"] = JobMeta(
            job_id="test-job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )

        result = await manager.cancel_job("test-job-1")
        assert result is True
        assert manager._job_meta["test-job-1"].status == "cancelled"

        # Give event loop a chance to process the cancellation
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled()


# ─── Shutdown ──────────────────────────────────────────────────────────


class TestShutdown:
    """Tests for JobManager graceful/forceful shutdown."""

    @pytest.mark.asyncio
    async def test_graceful_shutdown_waits_for_jobs(self, manager: JobManager):
        """Graceful shutdown waits for running tasks before completing."""
        completed = False

        async def short_task():
            nonlocal completed
            await asyncio.sleep(0.05)
            completed = True

        task = asyncio.create_task(short_task())
        manager._jobs["job-1"] = task
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )

        await manager.shutdown(graceful=True)

        assert manager._shutdown_event.is_set()
        assert completed is True

    @pytest.mark.asyncio
    async def test_forceful_shutdown_cancels_tasks(self, manager: JobManager):
        """Forceful shutdown cancels all running tasks."""
        async def long_task():
            await asyncio.sleep(100)

        task = asyncio.create_task(long_task())
        manager._jobs["job-1"] = task
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )

        await manager.shutdown(graceful=False)

        assert manager._shutdown_event.is_set()
        assert task.cancelled() or task.done()
        assert len(manager._jobs) == 0

    @pytest.mark.asyncio
    async def test_shutdown_sets_event(self, manager: JobManager):
        """Shutdown sets the shutdown_event so wait_for_shutdown unblocks."""
        await manager.shutdown(graceful=True)
        assert manager._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_graceful_shutdown_timeout_then_cancels(self, daemon_config: DaemonConfig):
        """Graceful shutdown cancels tasks that exceed timeout."""
        daemon_config.shutdown_timeout_seconds = 0.05
        mgr = JobManager(daemon_config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        async def stuck_task():
            await asyncio.sleep(100)

        task = asyncio.create_task(stuck_task())
        mgr._jobs["stuck-job"] = task
        mgr._job_meta["stuck-job"] = JobMeta(
            job_id="stuck-job",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )

        await mgr.shutdown(graceful=True)

        assert mgr._shutdown_event.is_set()
        assert task.cancelled() or task.done()


# ─── On Task Done ─────────────────────────────────────────────────────


class TestOnTaskDone:
    """Tests for JobManager._on_task_done cleanup callback."""

    @pytest.mark.asyncio
    async def test_on_task_done_removes_from_jobs(self, manager: JobManager):
        """_on_task_done removes the job from the _jobs dict."""
        async def quick():
            pass

        task = asyncio.create_task(quick())
        manager._jobs["job-done"] = task
        manager._job_meta["job-done"] = JobMeta(
            job_id="job-done",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )

        await task  # Let it finish
        manager._on_task_done("job-done", task)

        assert "job-done" not in manager._jobs

    @pytest.mark.asyncio
    async def test_on_task_done_sets_failed_on_exception(self, manager: JobManager):
        """_on_task_done marks status as failed when task has an exception."""
        async def failing():
            raise RuntimeError("boom")

        task = asyncio.create_task(failing())
        manager._jobs["job-fail"] = task
        manager._job_meta["job-fail"] = JobMeta(
            job_id="job-fail",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )

        # Wait for task to fail (suppress the exception)
        try:
            await task
        except RuntimeError:
            pass

        manager._on_task_done("job-fail", task)

        assert "job-fail" not in manager._jobs
        assert manager._job_meta["job-fail"].status == "failed"

    @pytest.mark.asyncio
    async def test_on_task_done_cancelled_does_not_set_failed(self, manager: JobManager):
        """_on_task_done does not mark cancelled tasks as failed."""
        async def cancellable():
            await asyncio.sleep(100)

        task = asyncio.create_task(cancellable())
        manager._jobs["job-cancel"] = task
        manager._job_meta["job-cancel"] = JobMeta(
            job_id="job-cancel",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.CANCELLED,
        )

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        manager._on_task_done("job-cancel", task)

        assert "job-cancel" not in manager._jobs
        # Status should remain "cancelled", not "failed"
        assert manager._job_meta["job-cancel"].status == "cancelled"


# ─── Get Daemon Status ────────────────────────────────────────────────


class TestGetDaemonStatus:
    """Tests for JobManager.get_daemon_status()."""

    @pytest.mark.asyncio
    async def test_get_daemon_status_returns_correct_metrics(self, manager: JobManager):
        """get_daemon_status returns pid, running_jobs, version."""
        status = await manager.get_daemon_status()

        assert "pid" in status
        assert status["pid"] == __import__("os").getpid()
        assert "running_jobs" in status
        assert status["running_jobs"] == 0
        assert "version" in status
        assert "total_jobs_active" in status

    @pytest.mark.asyncio
    async def test_get_daemon_status_counts_running_jobs(self, manager: JobManager):
        """get_daemon_status reflects running_count correctly."""
        # Add some job meta with various statuses
        manager._job_meta["job-a"] = JobMeta(
            job_id="job-a",
            config_path=Path("/tmp/a.yaml"),
            workspace=Path("/tmp/wa"),
            status=DaemonJobStatus.RUNNING,
        )
        manager._job_meta["job-b"] = JobMeta(
            job_id="job-b",
            config_path=Path("/tmp/b.yaml"),
            workspace=Path("/tmp/wb"),
            status=DaemonJobStatus.COMPLETED,
        )
        manager._job_meta["job-c"] = JobMeta(
            job_id="job-c",
            config_path=Path("/tmp/c.yaml"),
            workspace=Path("/tmp/wc"),
            status=DaemonJobStatus.RUNNING,
        )

        status = await manager.get_daemon_status()
        assert status["running_jobs"] == 2


# ─── List Jobs ─────────────────────────────────────────────────────────


class TestListJobs:
    """Tests for JobManager.list_jobs()."""

    @pytest.mark.asyncio
    async def test_list_empty(self, manager: JobManager):
        """list_jobs returns empty when no jobs submitted."""
        result = await manager.list_jobs()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_returns_all_tracked_jobs(self, manager: JobManager):
        """list_jobs returns metadata for all tracked jobs."""
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/a.yaml"),
            workspace=Path("/tmp/wa"),
            status=DaemonJobStatus.RUNNING,
        )
        manager._job_meta["job-2"] = JobMeta(
            job_id="job-2",
            config_path=Path("/tmp/b.yaml"),
            workspace=Path("/tmp/wb"),
            status=DaemonJobStatus.COMPLETED,
        )

        result = await manager.list_jobs()
        assert len(result) == 2
        job_ids = {j["job_id"] for j in result}
        assert job_ids == {"job-1", "job-2"}


# ─── Get Job Status ───────────────────────────────────────────────────


class TestGetJobStatus:
    """Tests for JobManager.get_job_status()."""

    @pytest.mark.asyncio
    async def test_get_status_unknown_job_raises(self, manager: JobManager):
        """get_job_status raises for unknown job IDs."""
        with pytest.raises(JobSubmissionError, match="not found"):
            await manager.get_job_status("nonexistent")

    @pytest.mark.asyncio
    async def test_get_status_returns_meta(self, manager: JobManager):
        """get_job_status returns metadata for a known running job."""
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/a.yaml"),
            workspace=Path("/tmp/wa"),
            status=DaemonJobStatus.RUNNING,
        )
        # Simulate a running asyncio task so the stale-status guard passes
        loop = asyncio.get_event_loop()
        fake_task = loop.create_future()
        manager._jobs["job-1"] = fake_task

        status = await manager.get_job_status("job-1")
        assert status["job_id"] == "job-1"
        assert status["status"] == "running"
        assert status["workspace"] == str(Path("/tmp/wa"))

    @pytest.mark.asyncio
    async def test_get_status_stale_running_corrected(self, manager: JobManager):
        """get_job_status corrects stale RUNNING status to FAILED."""
        manager._job_meta["stale-1"] = JobMeta(
            job_id="stale-1",
            config_path=Path("/tmp/a.yaml"),
            workspace=Path("/tmp/wa"),
            status=DaemonJobStatus.RUNNING,
        )
        # No task in _jobs — simulates daemon restart with stale meta

        status = await manager.get_job_status("stale-1")
        assert status["status"] in ("failed", DaemonJobStatus.FAILED)
        # Meta should also be corrected in place
        assert manager._job_meta["stale-1"].status == DaemonJobStatus.FAILED


# ─── Pause Job ─────────────────────────────────────────────────────────


class TestPauseJob:
    """Tests for JobManager.pause_job() at the manager level."""

    @pytest.mark.asyncio
    async def test_pause_unknown_job_raises(self, manager: JobManager):
        """pause_job raises for unknown job IDs."""
        with pytest.raises(JobSubmissionError, match="not found"):
            await manager.pause_job("nonexistent")

    @pytest.mark.asyncio
    async def test_pause_non_running_job_raises(self, manager: JobManager):
        """pause_job raises when job is not in running state."""
        manager._job_meta["job-done"] = JobMeta(
            job_id="job-done",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.COMPLETED,
        )

        with pytest.raises(JobSubmissionError, match="completed"):
            await manager.pause_job("job-done")

    @pytest.mark.asyncio
    async def test_pause_running_job_delegates_to_service(self, manager: JobManager):
        """pause_job calls service.pause_job for running jobs."""
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )
        # Simulate a running asyncio task so the stale-status guard passes
        loop = asyncio.get_event_loop()
        fake_task = loop.create_future()
        manager._jobs["job-1"] = fake_task
        manager._service.pause_job = AsyncMock(return_value=True)

        result = await manager.pause_job("job-1", workspace=Path("/tmp/workspace"))

        assert result is True
        manager._service.pause_job.assert_awaited_once_with(
            "job-1", Path("/tmp/workspace"),
        )

    @pytest.mark.asyncio
    async def test_pause_uses_meta_workspace_as_fallback(self, manager: JobManager):
        """pause_job uses meta.workspace when no workspace arg is given."""
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/meta-workspace"),
            status=DaemonJobStatus.RUNNING,
        )
        # Simulate a running asyncio task so the stale-status guard passes
        loop = asyncio.get_event_loop()
        fake_task = loop.create_future()
        manager._jobs["job-1"] = fake_task
        manager._service.pause_job = AsyncMock(return_value=True)

        await manager.pause_job("job-1")

        manager._service.pause_job.assert_awaited_once_with(
            "job-1", Path("/tmp/meta-workspace"),
        )


# ─── Resume Job ────────────────────────────────────────────────────────


class TestResumeJob:
    """Tests for JobManager.resume_job() at the manager level."""

    @pytest.mark.asyncio
    async def test_resume_unknown_job_raises(self, manager: JobManager):
        """resume_job raises for unknown job IDs."""
        with pytest.raises(JobSubmissionError, match="not found"):
            await manager.resume_job("nonexistent")

    @pytest.mark.asyncio
    async def test_resume_creates_task_and_returns_accepted(self, manager: JobManager):
        """resume_job creates a task and returns accepted response."""
        manager._job_meta["job-paused"] = JobMeta(
            job_id="job-paused",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.PAUSED,
        )
        manager._service.resume_job = AsyncMock()

        response = await manager.resume_job("job-paused")

        assert response.status == "accepted"
        assert response.job_id == "job-paused"
        assert "job-paused" in manager._jobs

        # Cleanup the spawned task
        manager._jobs["job-paused"].cancel()
        try:
            await manager._jobs["job-paused"]
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_resume_sets_meta_status_to_queued(self, manager: JobManager):
        """resume_job resets meta.status to queued."""
        manager._job_meta["job-paused"] = JobMeta(
            job_id="job-paused",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.PAUSED,
        )
        manager._service.resume_job = AsyncMock()

        await manager.resume_job("job-paused")

        assert manager._job_meta["job-paused"].status == "queued"

        # Cleanup
        manager._jobs["job-paused"].cancel()
        try:
            await manager._jobs["job-paused"]
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_resume_cancelled_job_accepted(self, manager: JobManager):
        """resume_job accepts CANCELLED jobs (e.g. from resource monitor)."""
        manager._job_meta["job-cancelled"] = JobMeta(
            job_id="job-cancelled",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.CANCELLED,
        )
        manager._service.resume_job = AsyncMock()

        response = await manager.resume_job("job-cancelled")

        assert response.status == "accepted"
        assert response.job_id == "job-cancelled"
        assert manager._job_meta["job-cancelled"].status == "queued"

        # Cleanup
        manager._jobs["job-cancelled"].cancel()
        try:
            await manager._jobs["job-cancelled"]
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_resume_running_job_rejected(self, manager: JobManager):
        """resume_job rejects RUNNING jobs."""
        manager._job_meta["job-running"] = JobMeta(
            job_id="job-running",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.RUNNING,
        )

        with pytest.raises(JobSubmissionError, match="running"):
            await manager.resume_job("job-running")

    @pytest.mark.asyncio
    async def test_resume_completed_job_rejected(self, manager: JobManager):
        """resume_job rejects COMPLETED jobs."""
        manager._job_meta["job-done"] = JobMeta(
            job_id="job-done",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.COMPLETED,
        )

        with pytest.raises(JobSubmissionError, match="completed"):
            await manager.resume_job("job-done")


# ─── Job History Pruning ──────────────────────────────────────────────


class TestJobHistoryPruning:
    """Tests for _prune_job_history() eviction logic."""

    @pytest.mark.asyncio
    async def test_prune_removes_oldest_terminal_jobs(self, tmp_path: Path):
        """When terminal jobs exceed max_job_history, oldest are evicted."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            max_job_history=10,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            # Add 13 completed jobs with increasing submit times
            for i in range(13):
                mgr._job_meta[f"job-{i}"] = JobMeta(
                    job_id=f"job-{i}",
                    config_path=Path(f"/tmp/{i}.yaml"),
                    workspace=Path(f"/tmp/ws-{i}"),
                    status=DaemonJobStatus.COMPLETED,
                    submitted_at=float(i),
                )

            mgr._prune_job_history()

            # Should keep only the 10 newest (job-3 through job-12)
            assert len(mgr._job_meta) == 10
            assert "job-0" not in mgr._job_meta
            assert "job-1" not in mgr._job_meta
            assert "job-2" not in mgr._job_meta
            assert "job-3" in mgr._job_meta
            assert "job-12" in mgr._job_meta
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_prune_preserves_running_jobs(self, tmp_path: Path):
        """Pruning never evicts running/queued jobs."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            max_job_history=10,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            # 12 completed + 2 running
            for i in range(12):
                mgr._job_meta[f"done-{i}"] = JobMeta(
                    job_id=f"done-{i}",
                    config_path=Path(f"/tmp/{i}.yaml"),
                    workspace=Path(f"/tmp/ws-{i}"),
                    status=DaemonJobStatus.COMPLETED,
                    submitted_at=float(i),
                )
            for i in range(2):
                mgr._job_meta[f"run-{i}"] = JobMeta(
                    job_id=f"run-{i}",
                    config_path=Path(f"/tmp/run-{i}.yaml"),
                    workspace=Path(f"/tmp/ws-run-{i}"),
                    status=DaemonJobStatus.RUNNING,
                    submitted_at=float(i),
                )

            mgr._prune_job_history()

            # Running jobs must be preserved (not terminal, so not pruned)
            assert "run-0" in mgr._job_meta
            assert "run-1" in mgr._job_meta
            # Only 10 terminal allowed, oldest 2 (done-0, done-1) evicted
            assert "done-0" not in mgr._job_meta
            assert "done-1" not in mgr._job_meta
            assert "done-2" in mgr._job_meta
            assert "done-11" in mgr._job_meta
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_prune_noop_when_under_limit(self, manager: JobManager):
        """No eviction when terminal count is under max_job_history."""
        manager._job_meta["job-1"] = JobMeta(
            job_id="job-1",
            config_path=Path("/tmp/a.yaml"),
            workspace=Path("/tmp/wa"),
            status=DaemonJobStatus.COMPLETED,
        )

        manager._prune_job_history()

        assert "job-1" in manager._job_meta

    @pytest.mark.asyncio
    async def test_on_task_done_triggers_pruning(self, tmp_path: Path):
        """_on_task_done calls _prune_job_history after status update."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            max_job_history=10,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            # Pre-fill with 11 completed jobs so pruning kicks in
            for i in range(11):
                mgr._job_meta[f"old-{i}"] = JobMeta(
                    job_id=f"old-{i}",
                    config_path=Path(f"/tmp/old-{i}.yaml"),
                    workspace=Path(f"/tmp/ws-old-{i}"),
                    status=DaemonJobStatus.COMPLETED,
                    submitted_at=float(i),
                )

            # Simulate a task-done callback (the task itself doesn't matter --
            # the pruning is triggered by the callback)
            async def done():
                pass

            task = asyncio.create_task(done())
            mgr._jobs["trigger"] = task
            await task
            mgr._on_task_done("trigger", task)

            # 11 terminal jobs, max 10 -> oldest (old-0) should be evicted
            assert "old-0" not in mgr._job_meta
            assert "old-1" in mgr._job_meta
            assert "old-10" in mgr._job_meta
        finally:
            await mgr._registry.close()


# ─── Failure Rate ────────────────────────────────────────────────────


class TestFailureRate:
    """Tests for failure_rate_elevated property."""

    @pytest.mark.asyncio
    async def test_not_elevated_initially(self, manager: JobManager):
        """failure_rate_elevated is False with no failures."""
        assert manager.failure_rate_elevated is False

    @pytest.mark.asyncio
    async def test_not_elevated_with_few_failures(self, manager: JobManager):
        """failure_rate_elevated is False with <= threshold failures."""
        import time as _time

        now = _time.monotonic()
        for _ in range(3):
            manager._recent_failures.append(now)

        assert manager.failure_rate_elevated is False

    @pytest.mark.asyncio
    async def test_elevated_above_threshold(self, manager: JobManager):
        """failure_rate_elevated is True with > threshold recent failures."""
        import time as _time

        now = _time.monotonic()
        for _ in range(4):
            manager._recent_failures.append(now)

        assert manager.failure_rate_elevated is True

    @pytest.mark.asyncio
    async def test_old_failures_pruned(self, manager: JobManager):
        """Failures older than the window are pruned and don't count."""
        import time as _time

        now = _time.monotonic()
        # Add 5 failures that are 120 seconds old (well outside 60s window)
        for _ in range(5):
            manager._recent_failures.append(now - 120.0)

        assert manager.failure_rate_elevated is False
        # Verify pruning occurred
        assert len(manager._recent_failures) == 0


# ─── Job Timeout ──────────────────────────────────────────────────────


class TestJobTimeout:
    """Tests for per-job task timeout (D007)."""

    @pytest.mark.asyncio
    async def test_job_exceeding_timeout_is_failed(self, tmp_path: Path):
        """A job that exceeds job_timeout_seconds is marked FAILED."""
        config = DaemonConfig(max_concurrent_jobs=2, state_db_path=tmp_path / "reg.db")
        mgr = JobManager(config)
        await mgr._registry.open()
        # Override for test speed (Pydantic ge=60 prevents construction with low values)
        mgr._config = config.model_copy(update={"job_timeout_seconds": 0.2})

        try:
            meta = JobMeta(
                job_id="timeout-test",
                config_path=Path("/tmp/test.yaml"),
                workspace=Path("/tmp/workspace"),
            )
            mgr._job_meta["timeout-test"] = meta

            async def _slow():
                await asyncio.sleep(5.0)

            await mgr._run_managed_task("timeout-test", _slow())

            assert meta.status == DaemonJobStatus.FAILED
            assert "exceeded timeout" in (meta.error_message or "").lower()
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_job_within_timeout_completes(self, tmp_path: Path):
        """A job that finishes before job_timeout_seconds completes normally."""
        config = DaemonConfig(max_concurrent_jobs=2, state_db_path=tmp_path / "reg.db")
        mgr = JobManager(config)
        await mgr._registry.open()

        try:
            meta = JobMeta(
                job_id="fast-test",
                config_path=Path("/tmp/test.yaml"),
                workspace=Path("/tmp/workspace"),
            )
            mgr._job_meta["fast-test"] = meta

            async def _fast():
                await asyncio.sleep(0.01)
                return None  # Default -> COMPLETED

            await mgr._run_managed_task("fast-test", _fast())

            assert meta.status == DaemonJobStatus.COMPLETED
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_timeout_records_failure(self, tmp_path: Path):
        """Timeout adds to the recent_failures deque (affects failure_rate_elevated)."""
        config = DaemonConfig(max_concurrent_jobs=2, state_db_path=tmp_path / "reg.db")
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._config = config.model_copy(update={"job_timeout_seconds": 0.1})

        try:
            meta = JobMeta(
                job_id="fail-rate-test",
                config_path=Path("/tmp/test.yaml"),
                workspace=Path("/tmp/workspace"),
            )
            mgr._job_meta["fail-rate-test"] = meta

            async def _slow():
                await asyncio.sleep(5.0)

            initial_failures = len(mgr._recent_failures)
            await mgr._run_managed_task("fail-rate-test", _slow())

            assert len(mgr._recent_failures) == initial_failures + 1
        finally:
            await mgr._registry.close()


# ─── Exception Narrowing Tests (Q006) ─────────────────────────────────


class TestExceptionNarrowing:
    """Test that manager exception handlers catch specific types, not broad Exception."""

    @pytest.mark.asyncio
    async def test_config_parse_catches_value_error(
        self, manager: JobManager, tmp_path: Path,
    ) -> None:
        """Config parse failure (ValueError) returns rejected JobResponse."""
        config_path = tmp_path / "bad.yaml"
        config_path.write_text("invalid: yaml: content: [")

        request = JobRequest(
            config_path=config_path,
            workspace=None,
        )
        response = await manager.submit_job(request)
        assert response.status == "rejected"
        assert "Failed to parse config file" in (response.message or "")

    @pytest.mark.asyncio
    async def test_config_parse_catches_os_error(
        self, manager: JobManager, tmp_path: Path,
    ) -> None:
        """Config parse failure (OSError) returns rejected JobResponse."""
        config_path = tmp_path / "unreadable.yaml"
        config_path.write_text("name: test\n")
        config_path.chmod(0o000)

        try:
            request = JobRequest(config_path=config_path, workspace=None)
            response = await manager.submit_job(request)
            assert response.status == "rejected"
            assert "Failed to parse config file" in (response.message or "")
        finally:
            config_path.chmod(0o644)

    @pytest.mark.asyncio
    async def test_on_task_done_handles_expected_exceptions(
        self, manager: JobManager,
    ) -> None:
        """_on_task_done handles KeyError/OSError/RuntimeError without crashing."""
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.return_value = None

        # Should not raise even with missing job_id
        manager._on_task_done("nonexistent-job", task)


# ─── Workspace Validation (Q007 partial) ──────────────────────────────


class TestWorkspaceValidation:
    """Tests for early workspace validation in submit_job()."""

    @pytest.mark.asyncio
    async def test_submit_rejects_nonexistent_workspace_parent(
        self, manager: JobManager, sample_config_file: Path,
    ):
        """Jobs are rejected when workspace parent directory doesn't exist."""
        request = JobRequest(
            config_path=sample_config_file,
            workspace=Path("/nonexistent/parent/workspace"),
        )
        response = await manager.submit_job(request)

        assert response.status == "rejected"
        assert "parent directory does not exist" in (response.message or "")

    @pytest.mark.asyncio
    async def test_submit_rejects_unwritable_workspace_parent(
        self, manager: JobManager, sample_config_file: Path, tmp_path: Path,
    ):
        """Jobs are rejected when workspace parent directory is not writable."""
        # Create a read-only directory
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)

        try:
            request = JobRequest(
                config_path=sample_config_file,
                workspace=readonly_dir / "workspace",
            )
            response = await manager.submit_job(request)

            assert response.status == "rejected"
            assert "not writable" in (response.message or "")
        finally:
            # Restore write permission so tmp_path cleanup works
            readonly_dir.chmod(0o755)

    @pytest.mark.asyncio
    async def test_submit_accepts_valid_workspace_parent(
        self, manager: JobManager, sample_config_file: Path, tmp_path: Path,
    ):
        """Jobs with a valid, writable workspace parent are accepted."""
        request = JobRequest(
            config_path=sample_config_file,
            workspace=tmp_path / "new-workspace",
        )
        response = await manager.submit_job(request)

        assert response.status == "accepted"


# ─── Semantic Analyzer Integration ────────────────────────────────────


class TestSemanticAnalyzerIntegration:
    """Tests for SemanticAnalyzer wiring in JobManager lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_semantic_analyzer(self, tmp_path: Path):
        """manager.start() instantiates and starts the SemanticAnalyzer."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        try:
            await mgr.start()

            assert mgr._semantic_analyzer is not None
            # Analyzer should have subscribed to the event bus
            assert mgr._event_bus.subscriber_count > 0
        finally:
            await mgr.shutdown(graceful=False)

    @pytest.mark.asyncio
    async def test_shutdown_stops_semantic_analyzer(self, tmp_path: Path):
        """manager.shutdown() stops the SemanticAnalyzer and unsubscribes."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        await mgr.start()
        assert mgr._semantic_analyzer is not None

        # Count subscribers before shutdown
        _pre_shutdown_subs = mgr._event_bus.subscriber_count

        await mgr.shutdown(graceful=False)

        # After shutdown the analyzer's sub_id should be cleared
        assert mgr._semantic_analyzer._sub_id is None

    @pytest.mark.asyncio
    async def test_disabled_analyzer_does_not_subscribe(self, tmp_path: Path):
        """When learning.enabled=False, SemanticAnalyzer doesn't subscribe."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
            learning={"enabled": False},
        )
        mgr = JobManager(config)

        try:
            await mgr.start()

            # Analyzer is created but disabled — no subscription
            assert mgr._semantic_analyzer is not None
            assert mgr._semantic_analyzer._sub_id is None
            assert mgr._event_bus.subscriber_count == 0
        finally:
            await mgr.shutdown(graceful=False)

    @pytest.mark.asyncio
    async def test_analyzer_failure_does_not_prevent_start(
        self, tmp_path: Path,
    ):
        """If SemanticAnalyzer startup fails, the manager still starts."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        # Patch SemanticAnalyzer.start to raise
        with patch(
            "mozart.daemon.manager.SemanticAnalyzer.start",
            side_effect=RuntimeError("LLM init failed"),
        ):
            await mgr.start()

        try:
            # Manager should still be operational
            assert mgr._service is not None
            assert mgr._semantic_analyzer is None
        finally:
            await mgr.shutdown(graceful=False)

    @pytest.mark.asyncio
    async def test_analyzer_receives_event_bus_reference(
        self, tmp_path: Path,
    ):
        """SemanticAnalyzer receives the same EventBus as the manager."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        try:
            await mgr.start()

            # The analyzer should be working with the manager's event bus
            # (verified indirectly: it subscribed, so it has the right bus)
            assert mgr._semantic_analyzer is not None
            assert mgr._semantic_analyzer._sub_id is not None
        finally:
            await mgr.shutdown(graceful=False)


# ─── JobMeta ─────────────────────────────────────────────────────────


class TestJobMeta:
    """Tests for JobMeta dataclass and to_dict()."""

    def test_to_dict_basic(self):
        meta = JobMeta(
            job_id="test-1",
            config_path=Path("/tmp/config.yaml"),
            workspace=Path("/tmp/ws"),
            status=DaemonJobStatus.RUNNING,
        )
        d = meta.to_dict()
        assert d["job_id"] == "test-1"
        assert d["status"] == "running"
        assert d["config_path"] == "/tmp/config.yaml"
        assert d["workspace"] == "/tmp/ws"
        assert "error_message" not in d  # Not set

    def test_to_dict_with_error(self):
        meta = JobMeta(
            job_id="test-2",
            config_path=Path("/tmp/config.yaml"),
            workspace=Path("/tmp/ws"),
            status=DaemonJobStatus.FAILED,
            error_message="Something broke",
            error_traceback="Traceback...",
        )
        d = meta.to_dict()
        assert d["error_message"] == "Something broke"
        assert d["error_traceback"] == "Traceback..."

    def test_to_dict_with_chain_depth(self):
        meta = JobMeta(
            job_id="test-3",
            config_path=Path("/tmp/config.yaml"),
            workspace=Path("/tmp/ws"),
            chain_depth=2,
        )
        d = meta.to_dict()
        assert d["chain_depth"] == 2

    def test_to_dict_without_chain_depth(self):
        meta = JobMeta(
            job_id="test-4",
            config_path=Path("/tmp/config.yaml"),
            workspace=Path("/tmp/ws"),
        )
        d = meta.to_dict()
        assert "chain_depth" not in d

    def test_defaults(self):
        meta = JobMeta(
            job_id="test-5",
            config_path=Path("/tmp/config.yaml"),
            workspace=Path("/tmp/ws"),
        )
        assert meta.status == DaemonJobStatus.QUEUED
        assert meta.error_message is None
        assert meta.started_at is None
        assert meta.chain_depth is None


# ─── Wait for Shutdown ───────────────────────────────────────────────


class TestWaitForShutdown:
    """Tests for JobManager.wait_for_shutdown()."""

    @pytest.mark.asyncio
    async def test_wait_unblocks_after_shutdown(self, manager: JobManager):
        """wait_for_shutdown returns after shutdown is called."""
        async def trigger():
            await asyncio.sleep(0.05)
            await manager.shutdown(graceful=True)

        trigger_task = asyncio.create_task(trigger())
        await manager.wait_for_shutdown()

        assert manager._shutdown_event.is_set()
        await trigger_task


# ─── Job ID (no deduplication) ───────────────────────────────────────


class TestGetJobId:
    """Tests for JobManager._get_job_id() — always returns the name as-is."""

    def test_returns_name_as_is(self, manager: JobManager):
        """Job name IS the job ID, no dedup suffixes."""
        assert manager._get_job_id("my-job") == "my-job"

    def test_returns_name_even_with_existing(self, manager: JobManager):
        """Same name is returned even if a terminal job exists."""
        manager._job_meta["my-job"] = JobMeta(
            job_id="my-job",
            config_path=Path("/tmp/test.yaml"),
            workspace=Path("/tmp/ws"),
            status=DaemonJobStatus.COMPLETED,
        )
        assert manager._get_job_id("my-job") == "my-job"


# ─── On Rate Limit ──────────────────────────────────────────────────


class TestOnRateLimit:
    """Tests for JobManager._on_rate_limit()."""

    @pytest.mark.asyncio
    async def test_forwards_to_coordinator(self, manager: JobManager):
        """_on_rate_limit forwards to rate_coordinator.report_rate_limit."""
        manager._rate_coordinator = MagicMock()
        manager._rate_coordinator.report_rate_limit = AsyncMock()

        await manager._on_rate_limit("claude_cli", 60.0, "job-1", 3)

        manager._rate_coordinator.report_rate_limit.assert_awaited_once_with(
            backend_type="claude_cli",
            wait_seconds=60.0,
            job_id="job-1",
            sheet_num=3,
        )


# ─── Backpressure ───────────────────────────────────────────────────


class TestBackpressure:
    """Tests for backpressure-based rejection."""

    @pytest.mark.asyncio
    async def test_rejects_under_pressure(
        self, manager: JobManager, sample_config_file: Path, tmp_path: Path,
    ):
        """Jobs are rejected when backpressure says no."""
        manager._backpressure = MagicMock()
        manager._backpressure.should_accept_job.return_value = False

        request = JobRequest(config_path=sample_config_file, workspace=tmp_path / "ws")
        response = await manager.submit_job(request)

        assert response.status == "rejected"
        assert "pressure" in (response.message or "").lower()

    @pytest.mark.asyncio
    async def test_accepts_when_no_pressure(
        self, manager: JobManager, sample_config_file: Path, tmp_path: Path,
    ):
        """Jobs are accepted when backpressure allows."""
        request = JobRequest(config_path=sample_config_file, workspace=tmp_path / "ws")
        response = await manager.submit_job(request)
        assert response.status == "accepted"


# ─── Start Method ────────────────────────────────────────────────────


class TestStart:
    """Tests for JobManager.start()."""

    @pytest.mark.asyncio
    async def test_start_initializes_service(self, tmp_path: Path):
        """start() creates JobService."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        try:
            await mgr.start()
            assert mgr._service is not None
        finally:
            await mgr.shutdown(graceful=False)

    @pytest.mark.asyncio
    async def test_start_opens_registry(self, tmp_path: Path):
        """start() opens the job registry."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        try:
            await mgr.start()
            # Registry should be open — we can call list
            jobs = await mgr.list_jobs()
            assert isinstance(jobs, list)
        finally:
            await mgr.shutdown(graceful=False)


# ─── Duplicate Job Rejection ─────────────────────────────────────────


class TestDuplicateJobRejection:
    """Tests for duplicate job rejection (one name per job)."""

    @pytest.mark.asyncio
    async def test_rejects_when_active(
        self, manager: JobManager, sample_config_file: Path, tmp_path: Path,
    ):
        """Submitting same config while job is active is rejected."""
        r1 = JobRequest(config_path=sample_config_file, workspace=tmp_path / "ws1")
        resp1 = await manager.submit_job(r1)
        assert resp1.status == "accepted"

        r2 = JobRequest(config_path=sample_config_file, workspace=tmp_path / "ws2")
        resp2 = await manager.submit_job(r2)
        assert resp2.status == "rejected"
        assert resp2.message is not None
        assert "already" in resp2.message.lower()

    @pytest.mark.asyncio
    async def test_reuses_when_terminal(
        self, manager: JobManager, sample_config_file: Path, tmp_path: Path,
    ):
        """Resubmitting after completion reuses the same job ID."""
        r1 = JobRequest(config_path=sample_config_file, workspace=tmp_path / "ws1")
        resp1 = await manager.submit_job(r1)
        assert resp1.status == "accepted"

        # Simulate job completion
        meta = manager._job_meta[resp1.job_id]
        meta.status = DaemonJobStatus.COMPLETED

        r2 = JobRequest(config_path=sample_config_file, workspace=tmp_path / "ws2")
        resp2 = await manager.submit_job(r2)
        assert resp2.status == "accepted"
        assert resp2.job_id == resp1.job_id


# ─── Run Managed Task Edge Cases ─────────────────────────────────────


class TestRunManagedTaskEdgeCases:
    """Additional edge cases for _run_managed_task."""

    @pytest.mark.asyncio
    async def test_task_returning_paused_status(self, tmp_path: Path):
        """Coroutine returning DaemonJobStatus.PAUSED sets meta accordingly."""
        config = DaemonConfig(max_concurrent_jobs=2, state_db_path=tmp_path / "reg.db")
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            meta = JobMeta(
                job_id="pause-test",
                config_path=Path("/tmp/test.yaml"),
                workspace=Path("/tmp/workspace"),
            )
            mgr._job_meta["pause-test"] = meta

            async def _pause():
                return DaemonJobStatus.PAUSED

            await mgr._run_managed_task("pause-test", _pause())

            assert meta.status == DaemonJobStatus.PAUSED
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_task_raising_exception(self, tmp_path: Path):
        """Coroutine that raises Exception is caught and job marked FAILED."""
        config = DaemonConfig(max_concurrent_jobs=2, state_db_path=tmp_path / "reg.db")
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            meta = JobMeta(
                job_id="err-test",
                config_path=Path("/tmp/test.yaml"),
                workspace=Path("/tmp/workspace"),
            )
            mgr._job_meta["err-test"] = meta

            async def _error():
                raise ValueError("Something went wrong")

            await mgr._run_managed_task("err-test", _error())

            assert meta.status == DaemonJobStatus.FAILED
            assert "Something went wrong" in (meta.error_message or "")
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_task_cancelled(self, tmp_path: Path):
        """CancelledError during task sets CANCELLED status."""
        config = DaemonConfig(max_concurrent_jobs=2, state_db_path=tmp_path / "reg.db")
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            meta = JobMeta(
                job_id="cancel-test",
                config_path=Path("/tmp/test.yaml"),
                workspace=Path("/tmp/workspace"),
            )
            mgr._job_meta["cancel-test"] = meta

            async def _cancellable():
                await asyncio.sleep(100)

            # Start the managed task in the background
            task = asyncio.create_task(
                mgr._run_managed_task("cancel-test", _cancellable())
            )
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            assert meta.status == DaemonJobStatus.CANCELLED
        finally:
            await mgr._registry.close()
