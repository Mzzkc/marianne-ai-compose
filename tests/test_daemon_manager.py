"""Tests for marianne.daemon.manager module.

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

from marianne.daemon.config import DaemonConfig
from marianne.daemon.exceptions import JobSubmissionError
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.types import JobRequest

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
        # Meta is updated synchronously before cancel_job returns
        assert manager._job_meta["test-job-1"].status == "cancelled"

        # Give event loop a chance to process the cancellation
        # and the deferred cleanup background task
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled()
        # Let the background cleanup task complete
        await asyncio.sleep(0)


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
    async def test_pause_running_job_sets_event(self, manager: JobManager):
        """pause_job sets the in-process event for running jobs with event."""
        event = asyncio.Event()
        manager._pause_events["job-1"] = event
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

        result = await manager.pause_job("job-1")

        assert result is True
        assert event.is_set()

    @pytest.mark.asyncio
    async def test_pause_falls_back_to_service_when_no_event(self, manager: JobManager):
        """pause_job falls back to service.pause_job when no event exists."""
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
            # Observer recorder still subscribes (1 subscriber), but analyzer does not
            assert mgr._semantic_analyzer._anomaly_sub_id is None
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
            "marianne.daemon.manager.SemanticAnalyzer.start",
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


class TestObserverRecorderIntegration:
    """Tests for ObserverRecorder wiring in JobManager lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_observer_recorder(self, tmp_path: Path):
        """manager.start() creates and starts the ObserverRecorder."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        try:
            await mgr.start()
            assert mgr._observer_recorder is not None
            assert mgr._observer_recorder._sub_id is not None
        finally:
            await mgr.shutdown(graceful=False)

    @pytest.mark.asyncio
    async def test_start_uses_enabled_guard_not_persist_events(self, tmp_path: Path):
        """REVIEW FIX 1: Recorder is created when observer.enabled=True,
        regardless of persist_events. persist_events=False should NOT
        prevent recorder creation (TUI still needs ring buffer)."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
            observer={"enabled": True, "persist_events": False},
        )
        mgr = JobManager(config)

        try:
            await mgr.start()
            assert mgr._observer_recorder is not None
        finally:
            await mgr.shutdown(graceful=False)

    @pytest.mark.asyncio
    async def test_disabled_observer_no_recorder(self, tmp_path: Path):
        """When observer.enabled=False, no recorder is created."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
            observer={"enabled": False},
        )
        mgr = JobManager(config)

        try:
            await mgr.start()
            assert mgr._observer_recorder is None
        finally:
            await mgr.shutdown(graceful=False)

    @pytest.mark.asyncio
    async def test_shutdown_stops_recorder(self, tmp_path: Path):
        """manager.shutdown() stops the ObserverRecorder and unsubscribes."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        await mgr.start()
        assert mgr._observer_recorder is not None

        await mgr.shutdown(graceful=False)
        assert mgr._observer_recorder._sub_id is None

    @pytest.mark.asyncio
    async def test_observer_recorder_property(self, tmp_path: Path):
        """observer_recorder property exposes the recorder."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        try:
            await mgr.start()
            assert mgr.observer_recorder is mgr._observer_recorder
        finally:
            await mgr.shutdown(graceful=False)

    @pytest.mark.asyncio
    async def test_start_observer_registers_with_recorder(self, tmp_path: Path):
        """_start_observer calls recorder.register_job for the job."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            from marianne.daemon.observer_recorder import ObserverRecorder

            mock_recorder = MagicMock(spec=ObserverRecorder)
            mgr._observer_recorder = mock_recorder

            ws = tmp_path / "workspace"
            ws.mkdir()
            mgr._job_meta["test-job"] = JobMeta(
                job_id="test-job",
                config_path=Path("/tmp/test.yaml"),
                workspace=ws,
                status=DaemonJobStatus.RUNNING,
            )

            await mgr._start_observer("test-job")
            mock_recorder.register_job.assert_called_once_with("test-job", ws)
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_run_managed_task_flushes_before_snapshot(self, tmp_path: Path):
        """_run_managed_task calls recorder.flush() before snapshot capture."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            from marianne.daemon.observer_recorder import ObserverRecorder

            mock_recorder = MagicMock(spec=ObserverRecorder)
            mgr._observer_recorder = mock_recorder

            meta = JobMeta(
                job_id="flush-test",
                config_path=Path("/tmp/test.yaml"),
                workspace=tmp_path / "workspace",
            )
            mgr._job_meta["flush-test"] = meta

            async def _fast():
                return None

            await mgr._run_managed_task("flush-test", _fast())

            # flush should have been called before snapshot
            mock_recorder.flush.assert_called_once_with("flush-test")
            mock_recorder.unregister_job.assert_called_once_with("flush-test")
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_run_managed_task_unregisters_on_failure(self, tmp_path: Path):
        """_run_managed_task's finally block calls recorder.unregister_job on error."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            from marianne.daemon.observer_recorder import ObserverRecorder

            mock_recorder = MagicMock(spec=ObserverRecorder)
            mgr._observer_recorder = mock_recorder

            meta = JobMeta(
                job_id="fail-unreg",
                config_path=Path("/tmp/test.yaml"),
                workspace=tmp_path / "workspace",
            )
            mgr._job_meta["fail-unreg"] = meta

            async def _error():
                raise ValueError("boom")

            await mgr._run_managed_task("fail-unreg", _error())

            # unregister_job should still be called in finally
            mock_recorder.unregister_job.assert_called_once_with("fail-unreg")
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_started_log_includes_observer_recorder(self, tmp_path: Path):
        """REVIEW FIX 3: The manager.started log includes observer_recorder status."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        try:
            with patch("marianne.daemon.manager._logger") as mock_logger:
                await mgr.start()
                # Find the manager.started log call
                started_calls = [
                    c for c in mock_logger.info.call_args_list
                    if c[0] and c[0][0] == "manager.started"
                ]
                assert len(started_calls) == 1
                kwargs = started_calls[0][1]
                assert "observer_recorder" in kwargs
                assert kwargs["observer_recorder"] == "active"
        finally:
            await mgr.shutdown(graceful=False)

    @pytest.mark.asyncio
    async def test_shutdown_stop_iterates_snapshot_of_keys(self, tmp_path: Path):
        """REVIEW FIX 2: stop() iterates list(self._jobs.keys()) to avoid RuntimeError."""
        config = DaemonConfig(
            max_concurrent_jobs=1,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)

        await mgr.start()
        assert mgr._observer_recorder is not None

        # Register two jobs in the recorder
        ws1, ws2 = tmp_path / "ws1", tmp_path / "ws2"
        ws1.mkdir()
        ws2.mkdir()
        mgr._observer_recorder.register_job("j1", ws1)
        mgr._observer_recorder.register_job("j2", ws2)

        # Shutdown should unregister all without RuntimeError
        await mgr.shutdown(graceful=False)
        assert len(mgr._observer_recorder._jobs) == 0


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


# ─── Hook Config Extraction ──────────────────────────────────────────


class TestHookConfigExtraction:
    """Tests for hook config extraction at submit_job() time."""

    @pytest.fixture
    def config_with_hooks(self, tmp_path: Path) -> Path:
        """Create a config file with on_success hooks."""
        config = tmp_path / "hooks-job.yaml"
        config.write_text(
            "name: hooks-job\n"
            "sheet:\n"
            "  size: 1\n"
            "  total_items: 1\n"
            "prompt:\n"
            "  template: test prompt\n"
            "on_success:\n"
            "  - type: run_job\n"
            "    job_path: next.yaml\n"
            "    fresh: true\n"
            "    description: Chain to next iteration\n"
            "concert:\n"
            "  enabled: true\n"
            "  max_chain_depth: 3\n"
            "  cooldown_between_jobs_seconds: 10\n"
        )
        return config

    @pytest.mark.asyncio
    async def test_submit_stores_hook_config_in_meta(
        self, manager: JobManager, config_with_hooks: Path, tmp_path: Path,
    ):
        """submit_job extracts hook config into JobMeta."""
        request = JobRequest(
            config_path=config_with_hooks,
            workspace=tmp_path / "ws",
        )
        response = await manager.submit_job(request)
        assert response.status == "accepted"

        meta = manager._job_meta[response.job_id]
        assert meta.hook_config is not None
        assert len(meta.hook_config) == 1
        assert meta.hook_config[0]["type"] == "run_job"
        assert meta.hook_config[0]["fresh"] is True

    @pytest.mark.asyncio
    async def test_submit_stores_concert_config_in_meta(
        self, manager: JobManager, config_with_hooks: Path, tmp_path: Path,
    ):
        """submit_job extracts concert config into JobMeta when enabled."""
        request = JobRequest(
            config_path=config_with_hooks,
            workspace=tmp_path / "ws",
        )
        response = await manager.submit_job(request)
        assert response.status == "accepted"

        meta = manager._job_meta[response.job_id]
        assert meta.concert_config is not None
        assert meta.concert_config["max_chain_depth"] == 3

    @pytest.mark.asyncio
    async def test_submit_stores_hook_config_in_registry(
        self, manager: JobManager, config_with_hooks: Path, tmp_path: Path,
    ):
        """submit_job persists hook config to registry for restart resilience."""
        import json

        request = JobRequest(
            config_path=config_with_hooks,
            workspace=tmp_path / "ws",
        )
        response = await manager.submit_job(request)
        assert response.status == "accepted"

        stored = await manager._registry.get_hook_config(response.job_id)
        assert stored is not None
        hooks = json.loads(stored)
        assert len(hooks) == 1
        assert hooks[0]["type"] == "run_job"

    @pytest.mark.asyncio
    async def test_submit_without_hooks_stores_none(
        self, manager: JobManager, sample_config_file: Path, tmp_path: Path,
    ):
        """submit_job stores None hook config for configs without on_success."""
        request = JobRequest(
            config_path=sample_config_file,
            workspace=tmp_path / "ws",
        )
        response = await manager.submit_job(request)
        assert response.status == "accepted"

        meta = manager._job_meta[response.job_id]
        assert meta.hook_config is None
        assert meta.concert_config is None

        stored = await manager._registry.get_hook_config(response.job_id)
        assert stored is None


# ─── Daemon Hook Execution ───────────────────────────────────────────


class TestDaemonHookExecution:
    """Tests for daemon-owned hook execution (_execute_hooks_task)."""

    @pytest.mark.asyncio
    async def test_hooks_fire_on_completed_job(self, tmp_path: Path):
        """_execute_hooks_task fires for COMPLETED jobs with hooks."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            # Create a config file the chained job will reference
            next_config = tmp_path / "next.yaml"
            next_config.write_text(
                "name: next-job\n"
                "sheet:\n  size: 1\n  total_items: 1\n"
                "prompt:\n  template: test\n"
            )

            await mgr._registry.register_job(
                "parent-job", Path("/tmp/parent.yaml"), tmp_path / "ws",
            )
            meta = JobMeta(
                job_id="parent-job",
                config_path=Path("/tmp/parent.yaml"),
                workspace=tmp_path / "ws",
                status=DaemonJobStatus.COMPLETED,
                hook_config=[{
                    "type": "run_job",
                    "job_path": str(next_config),
                    "fresh": True,
                    "description": "Chain to next",
                }],
                concert_config={
                    "enabled": True,
                    "max_chain_depth": 5,
                    "cooldown_between_jobs_seconds": 0,
                    "inherit_workspace": True,
                },
            )
            mgr._job_meta["parent-job"] = meta

            # Create workspace parent for the chained job
            (tmp_path / "ws").mkdir(exist_ok=True)

            await mgr._execute_hooks_task("parent-job")

            # The chained job should have been submitted
            # Job ID is derived from config file stem ("next"), not config name
            assert "next" in mgr._job_meta
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_hooks_do_not_fire_on_failed_job(self, tmp_path: Path):
        """_execute_hooks_task does nothing for non-COMPLETED jobs."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            meta = JobMeta(
                job_id="failed-job",
                config_path=Path("/tmp/test.yaml"),
                workspace=tmp_path / "ws",
                status=DaemonJobStatus.FAILED,
                hook_config=[{"type": "run_job", "job_path": "next.yaml"}],
            )
            mgr._job_meta["failed-job"] = meta

            # The _on_task_done check should NOT spawn hooks for FAILED
            # (this tests the guard, not _execute_hooks_task directly)
            task = MagicMock(spec=asyncio.Task)
            task.cancelled.return_value = False
            task.exception.return_value = None
            mgr._on_task_done("failed-job", task)

            # No hooks task should have been created
            # (we check by looking for any hooks-* task name)
            await asyncio.sleep(0.05)
            assert "next" not in mgr._job_meta
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_hooks_do_not_fire_without_hook_config(self, tmp_path: Path):
        """_execute_hooks_task returns early when no hook_config."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            await mgr._registry.register_job(
                "no-hooks", Path("/tmp/test.yaml"), tmp_path / "ws",
            )
            meta = JobMeta(
                job_id="no-hooks",
                config_path=Path("/tmp/test.yaml"),
                workspace=tmp_path / "ws",
                status=DaemonJobStatus.COMPLETED,
                hook_config=None,  # No hooks
            )
            mgr._job_meta["no-hooks"] = meta

            await mgr._execute_hooks_task("no-hooks")
            # No error, no crash, just a no-op
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_concert_depth_enforcement(self, tmp_path: Path):
        """Chained job is rejected when concert depth limit is reached."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            next_config = tmp_path / "next.yaml"
            next_config.write_text(
                "name: next-job\n"
                "sheet:\n  size: 1\n  total_items: 1\n"
                "prompt:\n  template: test\n"
            )

            await mgr._registry.register_job(
                "depth-job", Path("/tmp/depth.yaml"), tmp_path / "ws",
            )
            meta = JobMeta(
                job_id="depth-job",
                config_path=Path("/tmp/depth.yaml"),
                workspace=tmp_path / "ws",
                status=DaemonJobStatus.COMPLETED,
                chain_depth=3,  # At limit
                hook_config=[{
                    "type": "run_job",
                    "job_path": str(next_config),
                }],
                concert_config={
                    "enabled": True,
                    "max_chain_depth": 3,  # Limit = 3, depth = 3 → blocked
                    "cooldown_between_jobs_seconds": 0,
                },
            )
            mgr._job_meta["depth-job"] = meta

            await mgr._execute_hooks_task("depth-job")

            # Hook should have failed, job downgraded to FAILED
            assert meta.status == DaemonJobStatus.FAILED
            assert meta.error_message == "Post-success hook failed"

            # Verify the specific depth limit error is in the stored hook results
            import json
            cursor = await mgr._registry._db.execute(
                "SELECT hook_results_json FROM jobs WHERE job_id = ?",
                ("depth-job",),
            )
            row = await cursor.fetchone()
            assert row is not None
            hook_results = json.loads(row["hook_results_json"])
            assert "depth limit" in hook_results[0]["error_message"].lower()
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_hook_failure_downgrades_job(self, tmp_path: Path):
        """Hook failure downgrades parent job from COMPLETED to FAILED."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            await mgr._registry.register_job(
                "fail-hook", Path("/tmp/test.yaml"), tmp_path / "ws",
            )
            meta = JobMeta(
                job_id="fail-hook",
                config_path=Path("/tmp/test.yaml"),
                workspace=tmp_path / "ws",
                status=DaemonJobStatus.COMPLETED,
                hook_config=[{
                    "type": "run_job",
                    "job_path": "/nonexistent/job.yaml",
                    "description": "Will fail",
                }],
            )
            mgr._job_meta["fail-hook"] = meta

            await mgr._execute_hooks_task("fail-hook")

            assert meta.status == DaemonJobStatus.FAILED
            assert meta.error_message == "Post-success hook failed"
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_on_task_done_spawns_hooks(self, tmp_path: Path):
        """_on_task_done spawns hooks task for COMPLETED job with hooks."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            await mgr._registry.register_job(
                "hook-spawn", Path("/tmp/test.yaml"), tmp_path / "ws",
            )
            meta = JobMeta(
                job_id="hook-spawn",
                config_path=Path("/tmp/test.yaml"),
                workspace=tmp_path / "ws",
                status=DaemonJobStatus.COMPLETED,
                completed_new_work=True,  # Zero-work guard: must be True for hooks to fire
                hook_config=[{
                    "type": "run_job",
                    "job_path": "/nonexistent.yaml",
                }],
            )
            mgr._job_meta["hook-spawn"] = meta

            # Simulate task completion
            task = MagicMock(spec=asyncio.Task)
            task.cancelled.return_value = False
            task.exception.return_value = None
            mgr._on_task_done("hook-spawn", task)

            # Let the hooks task run (use polling, not fixed sleep)
            deadline = asyncio.get_event_loop().time() + 10.0
            while asyncio.get_event_loop().time() < deadline:
                if meta.status == DaemonJobStatus.FAILED:
                    break
                await asyncio.sleep(0.1)

            # Hook should have attempted to run (and failed on missing file)
            assert meta.status == DaemonJobStatus.FAILED
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_zero_work_guard_skips_hooks(self, tmp_path: Path):
        """_on_task_done skips hooks when completed_new_work is False."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            await mgr._registry.register_job(
                "zero-work", Path("/tmp/test.yaml"), tmp_path / "ws",
            )
            meta = JobMeta(
                job_id="zero-work",
                config_path=Path("/tmp/test.yaml"),
                workspace=tmp_path / "ws",
                status=DaemonJobStatus.COMPLETED,
                completed_new_work=False,  # No new sheets — hooks should be skipped
                hook_config=[{
                    "type": "run_job",
                    "job_path": "/nonexistent.yaml",
                }],
            )
            mgr._job_meta["zero-work"] = meta

            task = MagicMock(spec=asyncio.Task)
            task.cancelled.return_value = False
            task.exception.return_value = None
            mgr._on_task_done("zero-work", task)

            # Give time for any spawned task to run
            await asyncio.sleep(0.2)

            # Status should remain COMPLETED (hooks did not fire)
            assert meta.status == DaemonJobStatus.COMPLETED
        finally:
            await mgr._registry.close()

    @pytest.mark.asyncio
    async def test_cooldown_sleep_is_respected(self, tmp_path: Path):
        """Concert cooldown delays chained job submission."""
        config = DaemonConfig(
            max_concurrent_jobs=2,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            next_config = tmp_path / "next.yaml"
            next_config.write_text(
                "name: next-job\n"
                "sheet:\n  size: 1\n  total_items: 1\n"
                "prompt:\n  template: test\n"
            )

            await mgr._registry.register_job(
                "cooldown-job", Path("/tmp/parent.yaml"), tmp_path / "ws",
            )
            meta = JobMeta(
                job_id="cooldown-job",
                config_path=Path("/tmp/parent.yaml"),
                workspace=tmp_path / "ws",
                status=DaemonJobStatus.COMPLETED,
                hook_config=[{
                    "type": "run_job",
                    "job_path": str(next_config),
                    "fresh": True,
                }],
                concert_config={
                    "enabled": True,
                    "max_chain_depth": 5,
                    "cooldown_between_jobs_seconds": 0.5,
                    "inherit_workspace": True,
                },
            )
            mgr._job_meta["cooldown-job"] = meta
            (tmp_path / "ws").mkdir(exist_ok=True)

            import time
            start = time.monotonic()
            await mgr._execute_hooks_task("cooldown-job")
            elapsed = time.monotonic() - start

            # Should have waited at least 0.5s for cooldown
            assert elapsed >= 0.4, f"Expected >=0.4s cooldown, got {elapsed:.2f}s"
            # Chained job should still have been submitted
            assert "next" in mgr._job_meta
        finally:
            await mgr._registry.close()


# =============================================================================
# Bug #131 — resume -c Does Not Force Config Reload in Running Conductor
# =============================================================================


class TestResumeConductorConfigReload131:
    """Regression tests for Bug #131: resume_job must accept and apply config_path."""

    @pytest.mark.asyncio
    async def test_131_a_resume_job_updates_config_path(self, manager: JobManager) -> None:
        """TEST-131-A: resume_job(config_path=...) updates meta.config_path before task."""
        manager._job_meta["job-failed"] = JobMeta(
            job_id="job-failed",
            config_path=Path("/old/config.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.FAILED,
        )
        manager._service.resume_job = AsyncMock()

        await manager.resume_job("job-failed", config_path=Path("/new/config.yaml"))

        assert manager._job_meta["job-failed"].config_path == Path("/new/config.yaml")

        # Cleanup
        manager._jobs["job-failed"].cancel()
        try:
            await manager._jobs["job-failed"]
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_131_b_resume_job_no_config_path_leaves_unchanged(
        self, manager: JobManager
    ) -> None:
        """TEST-131-B: resume_job without config_path leaves meta.config_path unchanged."""
        manager._job_meta["job-paused"] = JobMeta(
            job_id="job-paused",
            config_path=Path("/original/config.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.PAUSED,
        )
        manager._service.resume_job = AsyncMock()

        await manager.resume_job("job-paused")

        assert manager._job_meta["job-paused"].config_path == Path("/original/config.yaml")

        # Cleanup
        manager._jobs["job-paused"].cancel()
        try:
            await manager._jobs["job-paused"]
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_131_c_config_path_updated_before_task_queued(
        self, manager: JobManager
    ) -> None:
        """TEST-131-C: config_path is applied before asyncio.create_task is called."""
        manager._job_meta["job-failed"] = JobMeta(
            job_id="job-failed",
            config_path=Path("/old/config.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.FAILED,
        )
        manager._service.resume_job = AsyncMock()
        observed_config: list[Path] = []

        original_create_task = asyncio.create_task

        def capture_task(coro, **kwargs):
            # meta.config_path must be updated before the task is created
            observed_config.append(manager._job_meta["job-failed"].config_path)
            return original_create_task(coro, **kwargs)

        with patch("asyncio.create_task", side_effect=capture_task):
            await manager.resume_job("job-failed", config_path=Path("/new/config.yaml"))

        assert observed_config == [Path("/new/config.yaml")]

        # Cleanup
        manager._jobs["job-failed"].cancel()
        try:
            await manager._jobs["job-failed"]
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_131_d_modify_job_no_regression_from_signature_change(
        self, manager: JobManager
    ) -> None:
        """TEST-131-F: modify_job still works — config_path=None default is backward compat."""
        manager._job_meta["job-paused"] = JobMeta(
            job_id="job-paused",
            config_path=Path("/original.yaml"),
            workspace=Path("/tmp/workspace"),
            status=DaemonJobStatus.PAUSED,
        )
        manager._service.resume_job = AsyncMock()

        # modify_job sets meta.config_path THEN calls resume_job (no config_path arg)
        # The None default must not overwrite what modify_job already set
        response = await manager.modify_job(
            "job-paused", config_path=Path("/modified.yaml")
        )
        assert response.status == "accepted"
        assert manager._job_meta["job-paused"].config_path == Path("/modified.yaml")

        # Cleanup
        manager._jobs["job-paused"].cancel()
        try:
            await manager._jobs["job-paused"]
        except (asyncio.CancelledError, Exception):
            pass


# ─── F-077: Hook Config Restoration After Restart ────────────────────


class TestHookConfigRestoration:
    """Tests for F-077: hook_config must be restored from registry on restart.

    When the conductor restarts, the restoration loop in start() constructs
    JobMeta from registry records. Before the fix, hook_config was not loaded,
    so on_success hooks never fired for jobs that survived a restart.
    """

    @pytest.fixture
    def config_with_hooks(self, tmp_path: Path) -> Path:
        """Create a config file with on_success hooks and concert config."""
        config = tmp_path / "chain-job.yaml"
        config.write_text(
            "name: chain-job\n"
            "sheet:\n"
            "  size: 1\n"
            "  total_items: 1\n"
            "prompt:\n"
            "  template: test prompt\n"
            "on_success:\n"
            "  - type: run_job\n"
            "    job_path: next.yaml\n"
            "    fresh: true\n"
            "    description: Chain to next iteration\n"
            "concert:\n"
            "  enabled: true\n"
            "  max_chain_depth: 5\n"
            "  cooldown_between_jobs_seconds: 10\n"
        )
        return config

    @pytest.mark.asyncio
    async def test_hook_config_restored_after_restart(
        self, daemon_config: DaemonConfig, config_with_hooks: Path, tmp_path: Path,
    ):
        """hook_config is loaded from registry when manager restarts."""
        # Phase 1: Submit a job with hooks via a "first" manager
        mgr1 = JobManager(daemon_config)
        await mgr1._registry.open()
        mgr1._service = MagicMock()
        request = JobRequest(
            config_path=config_with_hooks,
            workspace=tmp_path / "ws",
        )
        response = await mgr1.submit_job(request)
        assert response.status == "accepted"
        job_id = response.job_id

        # Verify hooks were stored in registry
        stored = await mgr1._registry.get_hook_config(job_id)
        assert stored is not None

        # Close the first manager (simulates conductor shutdown)
        await mgr1._registry.close()

        # Phase 2: Create a "second" manager (simulates restart)
        mgr2 = JobManager(daemon_config)
        await mgr2.start()

        # Verify hook_config was restored from registry
        assert job_id in mgr2._job_meta
        meta = mgr2._job_meta[job_id]
        assert meta.hook_config is not None, (
            "F-077: hook_config not restored from registry after restart"
        )
        assert len(meta.hook_config) == 1
        assert meta.hook_config[0]["type"] == "run_job"
        assert meta.hook_config[0]["fresh"] is True
        assert meta.hook_config[0]["description"] == "Chain to next iteration"

        await mgr2.shutdown()

    @pytest.mark.asyncio
    async def test_hook_config_none_when_no_hooks_stored(
        self, daemon_config: DaemonConfig, tmp_path: Path,
    ):
        """Jobs without hooks have hook_config=None after restart."""
        # Submit a job WITHOUT hooks
        config = tmp_path / "no-hooks.yaml"
        config.write_text(
            "name: no-hooks\n"
            "sheet:\n"
            "  size: 1\n"
            "  total_items: 1\n"
            "prompt:\n"
            "  template: test prompt\n"
        )
        mgr1 = JobManager(daemon_config)
        await mgr1._registry.open()
        mgr1._service = MagicMock()
        request = JobRequest(config_path=config, workspace=tmp_path / "ws")
        response = await mgr1.submit_job(request)
        assert response.status == "accepted"
        await mgr1._registry.close()

        # Restart
        mgr2 = JobManager(daemon_config)
        await mgr2.start()
        meta = mgr2._job_meta[response.job_id]
        assert meta.hook_config is None
        await mgr2.shutdown()
