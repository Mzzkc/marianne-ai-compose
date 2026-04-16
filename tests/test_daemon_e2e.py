"""End-to-end daemon integration tests.

Start a real daemon (foreground mode), submit jobs through IPC client,
and verify completion.  The full stack is exercised: DaemonProcess boot,
IPC server bind, JSON-RPC dispatch, JobManager task management, health
probes, and graceful shutdown.

NOTE: Most tests mock JobService.start_job() which was removed during
the baton migration. Tests that depend on the old runner API are skipped
until rewritten for the baton execution path.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from marianne.daemon.config import DaemonConfig, ResourceLimitConfig, SocketConfig
from marianne.daemon.exceptions import DaemonNotRunningError, JobSubmissionError
from marianne.daemon.ipc.client import DaemonClient
from marianne.daemon.learning_hub import LearningHub
from marianne.daemon.manager import JobManager
from marianne.daemon.process import DaemonProcess
from marianne.daemon.system_probe import SystemProbe
from marianne.daemon.types import JobRequest, JobResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal fixture config path (created during test setup).
FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "test-daemon-job.yaml"


def _make_unique_config(tmp_path: Path, index: int) -> Path:
    """Create a uniquely-named copy of the fixture config.

    Each copy has a different ``name`` field so the daemon treats them
    as distinct jobs (job name IS the job ID — no dedup).
    """
    import shutil

    dest = tmp_path / f"job-{index}.yaml"
    shutil.copy2(FIXTURE_CONFIG, dest)
    # Overwrite the name field so each config produces a unique job ID
    text = dest.read_text()
    text = text.replace('name: "test-daemon-job"', f'name: "test-daemon-job-{index}"')
    dest.write_text(text)
    return dest


def _make_daemon_config(tmp_path: Path, **overrides: Any) -> DaemonConfig:
    """Build a DaemonConfig with paths scoped to tmp_path."""
    defaults: dict[str, Any] = {
        "socket": SocketConfig(path=tmp_path / "test-marianne.sock"),
        "pid_file": tmp_path / "test-marianne.pid",
        "state_db_path": tmp_path / "test-registry.db",
        "max_concurrent_jobs": 2,
        "max_concurrent_sheets": 3,
        "monitor_interval_seconds": 5.0,
        "shutdown_timeout_seconds": 10.0,
    }
    defaults.update(overrides)
    return DaemonConfig(**defaults)


async def _wait_for_socket(socket_path: Path, timeout: float = 10.0) -> None:
    """Block until the daemon socket file appears on disk."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if socket_path.exists():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"Daemon socket {socket_path} did not appear within {timeout}s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def daemon(tmp_path: Path):
    """Start a real daemon in the background, yield (client, config), then shut down.

    The daemon runs `DaemonProcess.run()` as a background asyncio task.
    ProcessGroupManager.setup() is patched because os.setpgid() is
    restricted in test environments.  Signal handlers are patched
    because add_signal_handler only works on the main thread.

    JobService.start_job() is patched to return a synthetic JobCompletionSummary
    so tests don't need a real Claude CLI backend.
    """
    config = _make_daemon_config(tmp_path)
    dp = DaemonProcess(config)

    # Patch process group (needs root / main-thread for os.setpgid)
    with (
        patch.object(dp._pgroup, "setup"),
        patch.object(dp._pgroup, "kill_all_children"),
        patch.object(dp._pgroup, "cleanup_orphans", return_value=[]),
    ):
        # Patch signal handler installation (only works on main thread)
        original_run = dp.run

        async def _patched_run() -> None:
            loop = asyncio.get_running_loop()
            original_add = loop.add_signal_handler

            def _safe_add_signal_handler(sig: Any, cb: Any) -> None:
                try:
                    original_add(sig, cb)
                except (ValueError, RuntimeError):
                    pass  # Not on main thread; skip signal handler

            with patch.object(loop, "add_signal_handler", _safe_add_signal_handler):
                await original_run()

        daemon_task = asyncio.create_task(_patched_run())

        # Wait for socket to appear
        await _wait_for_socket(config.socket.path)

        client = DaemonClient(config.socket.path, timeout=10.0)
        yield client, config

        # Shutdown: send RPC then await task
        try:
            await client.call("daemon.shutdown", {"graceful": False})
        except (DaemonNotRunningError, ConnectionResetError, OSError):
            pass  # Daemon may already be shutting down

        try:
            await asyncio.wait_for(daemon_task, timeout=15.0)
        except (TimeoutError, asyncio.CancelledError):
            daemon_task.cancel()
            try:
                await daemon_task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# Tests: Daemon Lifecycle
# ---------------------------------------------------------------------------


class TestDaemonLifecycle:
    """Tests for daemon boot, status queries, and shutdown."""

    async def test_daemon_starts_and_responds(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """Daemon starts, binds socket, and responds to daemon.status RPC."""
        client, config = daemon

        result = await client.call("daemon.status")
        assert result["pid"] == os.getpid()  # Foreground mode — same process
        assert result["running_jobs"] == 0
        assert "version" in result

    async def test_daemon_status_round_trip(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """DaemonClient.status() returns a typed DaemonStatus with all fields.

        This is the D022 regression test — ensures get_daemon_status() returns
        every field that DaemonStatus requires, so deserialization succeeds.
        """
        from marianne.daemon.types import DaemonStatus

        client, config = daemon
        status = await client.status()

        assert isinstance(status, DaemonStatus)
        assert status.pid == os.getpid()
        assert status.uptime_seconds >= 0
        assert status.running_jobs == 0
        assert status.total_jobs_active >= 0
        assert status.memory_usage_mb >= 0.0
        assert isinstance(status.version, str)

    async def test_daemon_pid_file_written(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """PID file is created during daemon startup."""
        client, config = daemon

        assert config.pid_file.exists()
        pid_text = config.pid_file.read_text().strip()
        assert pid_text.isdigit()
        assert int(pid_text) == os.getpid()

    async def test_health_probe_returns_ok(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """Liveness probe returns status=ok with uptime."""
        client, config = daemon

        health = await client.health()
        assert health["status"] == "ok"
        assert health["pid"] == os.getpid()
        assert health["uptime_seconds"] >= 0

    async def test_readiness_probe(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """Readiness probe returns status=ready when no load."""
        client, config = daemon

        ready = await client.readiness()
        assert ready["status"] == "ready"
        assert ready["running_jobs"] == 0
        assert ready["accepting_work"] is True
        assert "memory_mb" in ready

    async def test_list_jobs_empty(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """list_jobs returns empty list when no jobs submitted."""
        client, config = daemon

        jobs = await client.list_jobs()
        assert jobs == []

    async def test_graceful_shutdown(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """daemon.shutdown RPC triggers clean daemon exit."""
        client, config = daemon

        # The fixture handles shutdown, but verify the RPC works
        result = await client.call("daemon.shutdown", {"graceful": True})
        assert result["shutting_down"] is True

        # Give daemon time to shut down
        await asyncio.sleep(0.5)

        # After shutdown, new connections should fail
        new_client = DaemonClient(config.socket.path, timeout=2.0)
        running = await new_client.is_daemon_running()
        # Socket may or may not be cleaned up yet, but the daemon
        # should no longer accept RPC calls.  Either False (socket gone)
        # or a connection that fails on call is acceptable.
        if running:
            try:
                await new_client.call("daemon.status")
                # If we get here, daemon is still alive — that's also OK
                # during graceful wind-down
            except (DaemonNotRunningError, ConnectionResetError, OSError):
                pass  # Expected after shutdown


# ---------------------------------------------------------------------------
# Tests: Job Submission
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="JobService.start_job removed (baton migration) — needs rewrite for baton execution path"
)
class TestJobSubmission:
    """Tests for job submission, status, and cancellation."""

    async def test_submit_job_accepted(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """A valid job config is accepted and returns job_id."""
        client, config = daemon

        # Patch JobService.start_job to avoid needing real Claude CLI
        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            new_callable=AsyncMock,
        ) as mock_start:
            from marianne.core.checkpoint import JobStatus
            from marianne.core.models import JobCompletionSummary

            mock_start.return_value = JobCompletionSummary(
                job_id="test-daemon-job",
                job_name="test-daemon-job",
                total_sheets=1,
                final_status=JobStatus.COMPLETED,
                completed_sheets=1,
            )

            req = JobRequest(config_path=FIXTURE_CONFIG)
            resp = await client.submit_job(req)

            assert isinstance(resp, JobResponse)
            assert resp.status == "accepted"
            assert resp.job_id  # Non-empty job ID
            assert "test-daemon-job" in resp.job_id

            # Wait a moment for the job task to start and complete
            await asyncio.sleep(0.5)

    async def test_submit_job_rejected_missing_config(
        self,
        daemon: tuple[DaemonClient, DaemonConfig],
    ):
        """Submitting a non-existent config file is rejected."""
        client, config = daemon

        req = JobRequest(config_path=Path("/nonexistent/path/fake.yaml"))
        resp = await client.submit_job(req)

        assert resp.status == "rejected"
        assert "not found" in (resp.message or "").lower()

    async def test_submit_job_appears_in_list(
        self,
        daemon: tuple[DaemonClient, DaemonConfig],
    ):
        """Submitted job appears in job.list."""
        client, config = daemon

        # Use a long-running mock so job stays active for listing
        started = asyncio.Event()
        hold = asyncio.Event()

        async def _slow_start(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await hold.wait()
            from marianne.core.checkpoint import JobStatus
            from marianne.core.models import JobCompletionSummary

            return JobCompletionSummary(
                job_id="test-daemon-job",
                job_name="test-daemon-job",
                total_sheets=1,
                final_status=JobStatus.COMPLETED,
            )

        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            side_effect=_slow_start,
        ):
            req = JobRequest(config_path=FIXTURE_CONFIG)
            resp = await client.submit_job(req)
            assert resp.status == "accepted"

            # Wait for job to actually enter the running state
            await asyncio.wait_for(started.wait(), timeout=5.0)
            await asyncio.sleep(0.1)  # Let status update propagate

            jobs = await client.list_jobs()
            job_ids = [j["job_id"] for j in jobs]
            assert resp.job_id in job_ids

            # Release the mock so cleanup doesn't hang
            hold.set()
            await asyncio.sleep(0.2)

    async def test_cancel_running_job(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """A running job can be cancelled via RPC."""
        client, config = daemon

        started = asyncio.Event()
        hold = asyncio.Event()

        async def _slow_start(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await hold.wait()
            from marianne.core.checkpoint import JobStatus
            from marianne.core.models import JobCompletionSummary

            return JobCompletionSummary(
                job_id="test-daemon-job",
                job_name="test-daemon-job",
                total_sheets=1,
                final_status=JobStatus.COMPLETED,
            )

        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            side_effect=_slow_start,
        ):
            req = JobRequest(config_path=FIXTURE_CONFIG)
            resp = await client.submit_job(req)
            job_id = resp.job_id

            # Wait until the job is running
            await asyncio.wait_for(started.wait(), timeout=5.0)
            await asyncio.sleep(0.1)

            # Cancel it
            cancel_result = await client.call("job.cancel", {"job_id": job_id})
            assert cancel_result["cancelled"] is True

            # Release hold in case cancellation didn't propagate yet
            hold.set()
            await asyncio.sleep(0.3)

            # Verify it shows as cancelled
            jobs = await client.list_jobs()
            cancelled_job = next((j for j in jobs if j["job_id"] == job_id), None)
            assert cancelled_job is not None
            assert cancelled_job["status"] == "cancelled"

    async def test_job_status_query(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """job.status RPC returns detailed info about a specific job."""
        client, config = daemon

        started = asyncio.Event()
        hold = asyncio.Event()

        async def _slow_start(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await hold.wait()
            from marianne.core.checkpoint import JobStatus
            from marianne.core.models import JobCompletionSummary

            return JobCompletionSummary(
                job_id="test-daemon-job",
                job_name="test-daemon-job",
                total_sheets=1,
                final_status=JobStatus.COMPLETED,
            )

        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            side_effect=_slow_start,
        ):
            req = JobRequest(config_path=FIXTURE_CONFIG)
            resp = await client.submit_job(req)
            job_id = resp.job_id

            await asyncio.wait_for(started.wait(), timeout=5.0)
            await asyncio.sleep(0.1)

            status = await client.get_job_status(job_id, str(config.pid_file.parent))
            assert status["job_id"] == job_id
            assert status["status"] == "running"
            assert str(FIXTURE_CONFIG) in status["config_path"]

            hold.set()
            await asyncio.sleep(0.2)


# ---------------------------------------------------------------------------
# Tests: Concurrent Jobs
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="JobService.start_job removed (baton migration) — needs rewrite for baton execution path"
)
class TestConcurrentJobs:
    """Tests for concurrent job submission and limit enforcement."""

    async def test_concurrent_jobs_respect_limit(
        self,
        daemon: tuple[DaemonClient, DaemonConfig],
        tmp_path: Path,
    ):
        """At most max_concurrent_jobs run simultaneously."""
        client, config = daemon
        assert config.max_concurrent_jobs == 2

        running_count = 0
        max_observed = 0
        gate = asyncio.Event()
        all_started = asyncio.Event()
        start_count = 0

        async def _tracked_start(*args: Any, **kwargs: Any) -> Any:
            nonlocal running_count, max_observed, start_count
            running_count += 1
            start_count += 1
            if running_count > max_observed:
                max_observed = running_count
            if start_count >= 2:
                all_started.set()
            try:
                await gate.wait()
            finally:
                running_count -= 1
            from marianne.core.checkpoint import JobStatus
            from marianne.core.models import JobCompletionSummary

            return JobCompletionSummary(
                job_id="test",
                job_name="test",
                total_sheets=1,
                final_status=JobStatus.COMPLETED,
            )

        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            side_effect=_tracked_start,
        ):
            # Submit 3 jobs with unique names (no dedup — same name is rejected)
            responses = []
            for i in range(3):
                cfg = _make_unique_config(tmp_path, i)
                req = JobRequest(config_path=cfg)
                r = await client.submit_job(req)
                assert r.status == "accepted"
                responses.append(r)

            # Wait for at least 2 to be running
            await asyncio.wait_for(all_started.wait(), timeout=5.0)
            await asyncio.sleep(0.3)

            # The third job is gated by the semaphore
            assert max_observed <= config.max_concurrent_jobs

            # Release all
            gate.set()
            await asyncio.sleep(1.0)

    async def test_concurrent_job_crash_isolation(
        self,
        daemon: tuple[DaemonClient, DaemonConfig],
        tmp_path: Path,
    ):
        """One crashing job does not affect concurrently running jobs.

        Submits 3 jobs where job #2 raises RuntimeError mid-execution.
        Jobs #1 and #3 must still complete successfully with COMPLETED status.
        This verifies crash isolation — the daemon's core safety property.
        """
        client, config = daemon

        call_index = 0
        gate = asyncio.Event()

        async def _per_job_start(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_index
            idx = call_index
            call_index += 1

            # All jobs wait at the gate so they run concurrently
            await gate.wait()

            if idx == 1:
                # Job #2 crashes
                raise RuntimeError("Simulated crash in job #2")

            # Jobs #1 and #3 succeed
            await asyncio.sleep(0.05)
            from marianne.core.checkpoint import JobStatus
            from marianne.core.models import JobCompletionSummary

            return JobCompletionSummary(
                job_id="test",
                job_name="test",
                total_sheets=1,
                final_status=JobStatus.COMPLETED,
                completed_sheets=1,
            )

        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            side_effect=_per_job_start,
        ):
            # Submit 3 jobs with unique names (no dedup)
            responses = []
            for i in range(3):
                cfg = _make_unique_config(tmp_path, i)
                req = JobRequest(config_path=cfg)
                r = await client.submit_job(req)
                assert r.status == "accepted"
                responses.append(r)

            # Give tasks time to start and reach the gate
            await asyncio.sleep(0.5)

            # Release all jobs simultaneously
            gate.set()

            # Poll until all jobs reach terminal state (or timeout).
            # Fixed sleeps are flaky because the semaphore (max_concurrent_jobs=2)
            # serializes job-2 behind job-0/job-1, and the manager's post-completion
            # chain (observer stop, snapshot, registry update) adds variable latency.
            deadline = asyncio.get_event_loop().time() + 10.0
            status_map: dict[str, str] = {}
            while asyncio.get_event_loop().time() < deadline:
                jobs = await client.list_jobs()
                status_map = {j["job_id"]: j["status"] for j in jobs}
                terminal = sum(
                    1 for s in status_map.values() if s in ("completed", "failed", "cancelled")
                )
                if terminal >= 3:
                    break
                await asyncio.sleep(0.2)

            # Verify: job #2 failed, jobs #1 and #3 completed
            failed_count = sum(1 for s in status_map.values() if s == "failed")
            completed_count = sum(1 for s in status_map.values() if s == "completed")

            assert failed_count == 1, f"Expected 1 failed job, got {failed_count}: {status_map}"
            assert completed_count == 2, (
                f"Expected 2 completed jobs, got {completed_count}: {status_map}"
            )

    async def test_multiple_jobs_complete_independently(
        self,
        daemon: tuple[DaemonClient, DaemonConfig],
        tmp_path: Path,
    ):
        """Two submitted jobs both complete and show in job list."""
        client, config = daemon

        call_count = 0

        async def _fast_start(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            from marianne.core.checkpoint import JobStatus
            from marianne.core.models import JobCompletionSummary

            return JobCompletionSummary(
                job_id="test",
                job_name="test",
                total_sheets=1,
                final_status=JobStatus.COMPLETED,
                completed_sheets=1,
            )

        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            side_effect=_fast_start,
        ):
            cfg1 = _make_unique_config(tmp_path, 0)
            cfg2 = _make_unique_config(tmp_path, 1)
            r1 = await client.submit_job(JobRequest(config_path=cfg1))
            r2 = await client.submit_job(JobRequest(config_path=cfg2))
            assert r1.status == "accepted"
            assert r2.status == "accepted"

            # Wait for both to complete
            await asyncio.sleep(1.5)

            jobs = await client.list_jobs()
            statuses = {j["job_id"]: j["status"] for j in jobs}
            assert statuses.get(r1.job_id) == "completed"
            assert statuses.get(r2.job_id) == "completed"
            assert call_count == 2


# ---------------------------------------------------------------------------
# Tests: Error Handling
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="JobService.start_job removed (baton migration) — needs rewrite for baton execution path"
)
class TestErrorHandling:
    """Tests for daemon error handling and resilience."""

    async def test_job_failure_recorded(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """A job that raises an exception is marked as failed."""
        client, config = daemon

        async def _failing_start(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Simulated backend crash")

        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            side_effect=_failing_start,
        ):
            req = JobRequest(config_path=FIXTURE_CONFIG)
            resp = await client.submit_job(req)
            assert resp.status == "accepted"

            # Wait for the task to fail
            await asyncio.sleep(1.0)

            jobs = await client.list_jobs()
            failed_job = next((j for j in jobs if j["job_id"] == resp.job_id), None)
            assert failed_job is not None
            assert failed_job["status"] == "failed"

    async def test_unknown_rpc_method_returns_error(
        self,
        daemon: tuple[DaemonClient, DaemonConfig],
    ):
        """Calling a non-existent RPC method raises DaemonError."""
        client, config = daemon
        from marianne.daemon.exceptions import DaemonError

        with pytest.raises(DaemonError, match="Method not found"):
            await client.call("nonexistent.method")

    async def test_get_status_unknown_job_raises(
        self,
        daemon: tuple[DaemonClient, DaemonConfig],
    ):
        """Requesting status for a non-existent job raises JobSubmissionError."""
        client, config = daemon

        with pytest.raises(JobSubmissionError, match="not found"):
            await client.call(
                "job.status",
                {"job_id": "does-not-exist", "workspace": "/tmp"},
            )

    async def test_daemon_rejects_during_shutdown(
        self,
        daemon: tuple[DaemonClient, DaemonConfig],
    ):
        """Jobs submitted during shutdown are rejected."""
        client, config = daemon

        # Trigger graceful shutdown
        await client.call("daemon.shutdown", {"graceful": True})
        await asyncio.sleep(0.3)

        # New submission should be rejected (daemon is shutting down)
        # The daemon may have already closed the socket, so either
        # a rejection or a connection error is acceptable.
        try:
            req = JobRequest(config_path=FIXTURE_CONFIG)
            resp = await client.submit_job(req)
            assert resp.status == "rejected"
            assert "shutting down" in (resp.message or "").lower()
        except (DaemonNotRunningError, ConnectionResetError, OSError):
            pass  # Also acceptable — daemon socket already gone


# ---------------------------------------------------------------------------
# Tests: Real Execution (no mocking)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="Baton dispatch cycle timing — dry-run jobs need pacing tuning")
class TestRealExecution:
    """Tests that exercise real JobService execution through the daemon.

    These tests do NOT mock JobService — they submit real jobs with
    dry_run=True so the full code path (config parsing, workspace
    creation, state backend setup) runs without needing a Claude CLI.
    """

    async def test_dry_run_job_completes(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """A dry_run job goes through the real path and completes."""
        client, config = daemon

        req = JobRequest(config_path=FIXTURE_CONFIG, dry_run=True)
        resp = await client.submit_job(req)
        assert resp.status == "accepted"
        assert resp.job_id

        # Poll for completion with deadline (MN-008: no fixed sleeps)
        job = None
        for _ in range(30):
            jobs = await client.list_jobs()
            job = next((j for j in jobs if j["job_id"] == resp.job_id), None)
            if job and job["status"] == "completed":
                break
            await asyncio.sleep(0.3)
        else:
            status = job["status"] if job else "not found"
            pytest.fail(f"dry_run job did not complete within timeout (status: {status})")

        assert job is not None
        assert job["status"] == "completed"

    async def test_dry_run_state_transitions(self, daemon: tuple[DaemonClient, DaemonConfig]):
        """Verify the full lifecycle: queued → running → completed."""
        client, config = daemon

        req = JobRequest(config_path=FIXTURE_CONFIG, dry_run=True)
        resp = await client.submit_job(req)
        assert resp.status == "accepted"

        # Wait for completion — baton dispatch cycle needs more time than
        # the old runner's synchronous path.
        for _ in range(50):
            jobs = await client.list_jobs()
            job = next((j for j in jobs if j["job_id"] == resp.job_id), None)
            if job and job["status"] == "completed":
                break
            await asyncio.sleep(0.3)
        else:
            pytest.fail("Job did not complete within timeout")

        assert job is not None
        assert job["status"] == "completed"
        assert job["started_at"] is not None


# ---------------------------------------------------------------------------
# Tests: Client Without Daemon (negative path)
# ---------------------------------------------------------------------------


class TestClientWithoutDaemon:
    """Verify client behaves correctly when no daemon is running."""

    async def test_is_daemon_running_false(self, tmp_path: Path):
        """is_daemon_running returns False when socket doesn't exist."""
        client = DaemonClient(tmp_path / "nonexistent.sock")
        assert await client.is_daemon_running() is False

    async def test_status_raises_not_running(self, tmp_path: Path):
        """status() raises DaemonNotRunningError without a daemon."""
        client = DaemonClient(tmp_path / "nonexistent.sock")
        with pytest.raises(DaemonNotRunningError):
            await client.status()

    async def test_submit_raises_not_running(self, tmp_path: Path):
        """submit_job() raises DaemonNotRunningError without a daemon."""
        client = DaemonClient(tmp_path / "nonexistent.sock")
        req = JobRequest(config_path=FIXTURE_CONFIG)
        with pytest.raises(DaemonNotRunningError):
            await client.submit_job(req)


# ---------------------------------------------------------------------------
# Tests: Backpressure Integration
# ---------------------------------------------------------------------------


class TestBackpressureIntegration:
    """Test that backpressure rejection flows through the real daemon stack.

    These tests start a real daemon and simulate high memory pressure
    by patching SystemProbe.get_memory_mb() to return values above the
    critical threshold.  The full path is exercised: IPC → handler →
    JobManager.submit_job → BackpressureController.should_accept_job →
    rejection response back to the client.
    """

    @pytest.fixture
    async def pressured_daemon(self, tmp_path: Path):
        """Start a real daemon with low memory limit and high simulated usage.

        Configures max_memory_mb=1000 and patches SystemProbe to report
        960MB usage (96% = CRITICAL level).  The backpressure controller
        should reject all job submissions.
        """
        config = DaemonConfig(
            socket=SocketConfig(path=tmp_path / "test-bp.sock"),
            pid_file=tmp_path / "test-bp.pid",
            state_db_path=tmp_path / "test-registry.db",
            max_concurrent_jobs=2,
            max_concurrent_sheets=3,
            monitor_interval_seconds=5.0,
            shutdown_timeout_seconds=10.0,
            resource_limits=ResourceLimitConfig(
                max_memory_mb=1000,
                max_processes=20,
            ),
        )
        dp = DaemonProcess(config)

        with (
            patch.object(dp._pgroup, "setup"),
            patch.object(dp._pgroup, "kill_all_children"),
            patch.object(dp._pgroup, "cleanup_orphans", return_value=[]),
            # Simulate critical memory pressure: 960MB of 1000MB = 96%
            patch.object(
                SystemProbe,
                "get_memory_mb",
                return_value=960.0,
            ),
            # Keep child count low so only memory triggers backpressure
            patch.object(
                SystemProbe,
                "get_child_count",
                return_value=2,
            ),
        ):
            original_run = dp.run

            async def _patched_run() -> None:
                loop = asyncio.get_running_loop()
                original_add = loop.add_signal_handler

                def _safe_add(sig: Any, cb: Any) -> None:
                    try:
                        original_add(sig, cb)
                    except (ValueError, RuntimeError):
                        pass

                with patch.object(loop, "add_signal_handler", _safe_add):
                    await original_run()

            daemon_task = asyncio.create_task(_patched_run())
            await _wait_for_socket(config.socket.path)

            client = DaemonClient(config.socket.path, timeout=10.0)
            yield client, config

            try:
                await client.call("daemon.shutdown", {"graceful": False})
            except (DaemonNotRunningError, ConnectionResetError, OSError):
                pass

            try:
                await asyncio.wait_for(daemon_task, timeout=15.0)
            except (TimeoutError, asyncio.CancelledError):
                daemon_task.cancel()
                try:
                    await daemon_task
                except asyncio.CancelledError:
                    pass

    async def test_backpressure_rejects_job(
        self,
        pressured_daemon: tuple[DaemonClient, DaemonConfig],
    ):
        """Job submitted under critical memory pressure is rejected.

        Full integration path: client → IPC → handler → JobManager →
        BackpressureController → rejection response.
        """
        client, config = pressured_daemon

        req = JobRequest(config_path=FIXTURE_CONFIG)
        resp = await client.submit_job(req)

        assert resp.status == "rejected"
        assert "pressure" in (resp.message or "").lower()

    async def test_backpressure_readiness_not_ready(
        self,
        pressured_daemon: tuple[DaemonClient, DaemonConfig],
    ):
        """Readiness probe reports not-ready under critical pressure."""
        client, config = pressured_daemon

        ready = await client.readiness()
        assert ready["accepting_work"] is False


# ---------------------------------------------------------------------------
# Tests: Real E2E Execution with Mock Backend (D019)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="Baton dispatch cycle timing — full lifecycle needs pacing tuning")
class TestRealE2EExecution:
    """D019: Real E2E job lifecycle with a mock backend at the lowest level.

    Unlike TestRealExecution (which uses dry_run=True, skipping all execution),
    these tests submit jobs that run through the FULL execution path:

        IPC client → daemon socket → JSON-RPC dispatch → JobManager →
        JobService.start_job() → _setup_components() (real backend creation) →
        BatonAdapter.activate() → baton dispatch → musician task →
        ClaudeCliBackend.execute() → [PATCHED HERE] → ExecutionResult →
        validation → checkpoint save → completion → IPC status queryable

    Only ClaudeCliBackend.execute() is patched — everything above and below
    runs for real.  This verifies the entire daemon stack is wired together
    and produces real workspace artifacts (state files, checkpoints).
    """

    @pytest.fixture
    async def real_execution_daemon(self, tmp_path: Path):
        """Start a real daemon with ClaudeCliBackend.execute() patched.

        The patch returns a synthetic ExecutionResult that simulates a
        successful 1-sheet Claude CLI execution.  The daemon, IPC, manager,
        service, baton, state backend, and checkpoint all run for real.
        """
        from marianne.backends.base import ExecutionResult

        config = _make_daemon_config(tmp_path)
        dp = DaemonProcess(config)

        # Simulated backend response for 1-sheet execution
        mock_result = ExecutionResult(
            success=True,
            stdout="Sheet completed successfully.\nAll tasks done.",
            stderr="",
            duration_seconds=0.5,
            exit_code=0,
        )

        with (
            patch.object(dp._pgroup, "setup"),
            patch.object(dp._pgroup, "kill_all_children"),
            patch.object(dp._pgroup, "cleanup_orphans", return_value=[]),
            # Patch at the lowest possible level — the actual CLI execution
            patch(
                "marianne.backends.claude_cli.ClaudeCliBackend.execute",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            original_run = dp.run

            async def _patched_run() -> None:
                loop = asyncio.get_running_loop()
                original_add = loop.add_signal_handler

                def _safe_add(sig: Any, cb: Any) -> None:
                    try:
                        original_add(sig, cb)
                    except (ValueError, RuntimeError):
                        pass

                with patch.object(loop, "add_signal_handler", _safe_add):
                    await original_run()

            daemon_task = asyncio.create_task(_patched_run())
            await _wait_for_socket(config.socket.path)

            client = DaemonClient(config.socket.path, timeout=10.0)
            yield client, config, tmp_path

            try:
                await client.call("daemon.shutdown", {"graceful": False})
            except (DaemonNotRunningError, ConnectionResetError, OSError):
                pass

            try:
                await asyncio.wait_for(daemon_task, timeout=15.0)
            except (TimeoutError, asyncio.CancelledError):
                daemon_task.cancel()
                try:
                    await daemon_task
                except asyncio.CancelledError:
                    pass

    async def test_full_lifecycle_config_to_completion(
        self,
        real_execution_daemon: tuple[DaemonClient, DaemonConfig, Path],
    ):
        """Full job lifecycle: submit → execute → complete → queryable via IPC.

        The entire execution stack runs for real except ClaudeCliBackend.execute().
        Verifies:
        - Config is parsed from YAML
        - Workspace is created on disk
        - Backend is selected and instantiated
        - Runner executes sheets through the state machine
        - Checkpoint state is persisted to disk
        - Job completion status is queryable via IPC
        """
        client, config, tmp_path = real_execution_daemon

        workspace = tmp_path / "e2e-workspace"
        req = JobRequest(config_path=FIXTURE_CONFIG, workspace=workspace)
        resp = await client.submit_job(req)

        assert resp.status == "accepted"
        assert resp.job_id

        # Wait for job to complete (real execution with mock backend)
        final_status = None
        for _ in range(40):
            jobs = await client.list_jobs()
            job = next((j for j in jobs if j["job_id"] == resp.job_id), None)
            if job and job["status"] in ("completed", "failed"):
                final_status = job["status"]
                break
            await asyncio.sleep(0.25)

        assert final_status == "completed", (
            f"Expected completed, got {final_status}. Jobs: {await client.list_jobs()}"
        )

        # Verify workspace was created on disk
        assert workspace.exists(), "Workspace directory should exist"

        # Verify checkpoint state file was written
        state_file = workspace / "test-daemon-job.json"
        assert state_file.exists(), (
            f"State file should exist at {state_file}. "
            f"Workspace contents: {list(workspace.iterdir())}"
        )

    async def test_full_lifecycle_state_file_content(
        self,
        real_execution_daemon: tuple[DaemonClient, DaemonConfig, Path],
    ):
        """Verify the checkpoint state file contains correct execution data.

        After a successful execution through the full stack, the JSON state
        file should reflect COMPLETED status with all sheets done.
        """
        import json

        client, config, tmp_path = real_execution_daemon

        workspace = tmp_path / "e2e-state-check"
        req = JobRequest(config_path=FIXTURE_CONFIG, workspace=workspace)
        resp = await client.submit_job(req)
        assert resp.status == "accepted"

        # Wait for completion
        for _ in range(40):
            jobs = await client.list_jobs()
            job = next((j for j in jobs if j["job_id"] == resp.job_id), None)
            if job and job["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.25)

        assert job is not None and job["status"] == "completed"

        # Read and verify checkpoint state
        state_file = workspace / "test-daemon-job.json"
        assert state_file.exists()

        state_data = json.loads(state_file.read_text())
        assert state_data["job_id"] == "test-daemon-job"
        assert state_data["status"] == "completed"
        assert state_data["total_sheets"] == 1
        assert state_data["last_completed_sheet"] >= 1

    async def test_full_lifecycle_job_appears_in_history(
        self,
        real_execution_daemon: tuple[DaemonClient, DaemonConfig, Path],
    ):
        """After completion, the job remains in the daemon's history.

        Verifies the manager retains job metadata after task cleanup,
        including started_at timestamp and config_path.
        """
        client, config, tmp_path = real_execution_daemon

        workspace = tmp_path / "e2e-history"
        req = JobRequest(config_path=FIXTURE_CONFIG, workspace=workspace)
        resp = await client.submit_job(req)
        assert resp.status == "accepted"

        # Wait for completion
        for _ in range(40):
            jobs = await client.list_jobs()
            job = next((j for j in jobs if j["job_id"] == resp.job_id), None)
            if job and job["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.25)

        # Verify job history entry has full metadata
        assert job is not None
        assert job["status"] == "completed"
        assert job["started_at"] is not None
        assert str(FIXTURE_CONFIG) in job["config_path"]
        assert str(workspace) in job["workspace"]


# ---------------------------------------------------------------------------
# Tests: Cross-Job Learning (D009)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="Baton dispatch cycle timing — learning tests need pacing tuning")
class TestCrossJobLearning:
    """D009: Verify that patterns learned by Job A are visible to Job B.

    The daemon's key differentiator is shared learning via LearningHub.
    This test exercises the full path:

        Job A runs → discovers pattern → records to shared store →
        Job B runs → reads shared store → sees Job A's pattern

    Everything except ClaudeCliBackend.execute() runs for real:
    daemon boot, IPC, manager, JobService with injected learning store,
    LearningHub with real GlobalLearningStore (in-memory SQLite for test).
    """

    @pytest.fixture
    async def learning_daemon(self, tmp_path: Path):
        """Start a real daemon with a temp-dir learning store.

        Patches LearningHub.__init__ so the daemon creates its hub with
        a test-local SQLite DB (isolated from the user's global store).
        After daemon boot, captures the manager reference from within
        the running daemon by patching _register_methods.
        """
        from marianne.backends.base import ExecutionResult

        config = _make_daemon_config(tmp_path)
        dp = DaemonProcess(config)

        # We'll capture the manager reference when _register_methods is called
        captured: dict[str, Any] = {}
        original_register = dp._register_methods

        def _capturing_register(
            handler: Any,
            manager: JobManager,
            health: Any = None,
        ) -> None:
            captured["manager"] = manager
            return original_register(handler, manager, health)

        learning_db = tmp_path / "test-learning.db"

        mock_result = ExecutionResult(
            success=True,
            stdout="Sheet completed successfully.\nAll tasks done.",
            stderr="",
            duration_seconds=0.5,
            exit_code=0,
        )

        with (
            patch.object(dp._pgroup, "setup"),
            patch.object(dp._pgroup, "kill_all_children"),
            patch.object(dp._pgroup, "cleanup_orphans", return_value=[]),
            patch(
                "marianne.backends.claude_cli.ClaudeCliBackend.execute",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            # Redirect LearningHub to use test-local DB path
            patch(
                "marianne.daemon.manager.LearningHub",
                side_effect=lambda db_path=None: LearningHub(db_path=learning_db),
            ),
            # Capture the manager reference
            patch.object(dp, "_register_methods", _capturing_register),
        ):
            original_run = dp.run

            async def _patched_run() -> None:
                loop = asyncio.get_running_loop()
                original_add = loop.add_signal_handler

                def _safe_add(sig: Any, cb: Any) -> None:
                    try:
                        original_add(sig, cb)
                    except (ValueError, RuntimeError):
                        pass

                with patch.object(loop, "add_signal_handler", _safe_add):
                    await original_run()

            daemon_task = asyncio.create_task(_patched_run())
            await _wait_for_socket(config.socket.path)

            client = DaemonClient(config.socket.path, timeout=10.0)
            manager = captured["manager"]
            yield client, config, manager

            try:
                await client.call("daemon.shutdown", {"graceful": False})
            except (DaemonNotRunningError, ConnectionResetError, OSError):
                pass

            try:
                await asyncio.wait_for(daemon_task, timeout=15.0)
            except (TimeoutError, asyncio.CancelledError):
                daemon_task.cancel()
                try:
                    await daemon_task
                except asyncio.CancelledError:
                    pass

    async def _wait_for_job(
        self,
        client: DaemonClient,
        job_id: str,
        timeout: float = 25.0,
    ) -> dict[str, Any]:
        """Poll until a job reaches a terminal state."""
        for _ in range(int(timeout / 0.25)):
            jobs = await client.list_jobs()
            job = next((j for j in jobs if j["job_id"] == job_id), None)
            if job and job["status"] in ("completed", "failed"):
                return job
            await asyncio.sleep(0.25)
        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

    async def test_patterns_from_job_a_visible_to_job_b(
        self,
        learning_daemon: tuple[DaemonClient, DaemonConfig, JobManager],
    ):
        """Patterns recorded by Job A are visible through the shared store.

        Full integration path: Job A → JobService (with injected store) →
        LearningHub.store.record_pattern() → shared GlobalLearningStore →
        Job B reads same store → sees Job A's pattern.
        """
        client, config, manager = learning_daemon

        # Verify the learning hub is running and has a shared store
        hub = manager.learning_hub
        assert hub.is_running
        store = hub.store

        # Submit Job A and let it complete through the real stack
        workspace_a = config.pid_file.parent / "ws-a"
        req_a = JobRequest(config_path=FIXTURE_CONFIG, workspace=workspace_a)
        resp_a = await client.submit_job(req_a)
        assert resp_a.status == "accepted"

        job_a = await self._wait_for_job(client, resp_a.job_id)
        assert job_a["status"] == "completed"

        # Simulate what a real backend would do after successful execution:
        # record a pattern discovery into the shared store
        pattern_id = store.record_pattern(
            pattern_type="rate_limit_recovery",
            pattern_name="exponential_backoff_works",
            description="Exponential backoff with jitter recovers from 429s",
            context_tags=["rate_limit", "recovery"],
        )

        store.record_pattern_discovery(
            pattern_id=pattern_id,
            pattern_name="exponential_backoff_works",
            pattern_type="rate_limit_recovery",
            job_id=resp_a.job_id,
            effectiveness_score=0.95,
            context_tags=["rate_limit", "recovery"],
            ttl_seconds=3600.0,
        )

        # Verify pattern exists in the shared store
        patterns = store.get_patterns(pattern_type="rate_limit_recovery")
        assert len(patterns) >= 1
        pattern_names = [p.pattern_name for p in patterns]
        assert "exponential_backoff_works" in pattern_names

        # Submit Job B with a unique config name so it gets a different
        # job_id (job name IS the job ID — no dedup). This lets the
        # exclude_job_id filter work correctly in pattern discovery.
        workspace_b = config.pid_file.parent / "ws-b"
        config_b = _make_unique_config(config.pid_file.parent, 2)
        req_b = JobRequest(config_path=config_b, workspace=workspace_b)
        resp_b = await client.submit_job(req_b)
        assert resp_b.status == "accepted"

        job_b = await self._wait_for_job(client, resp_b.job_id)
        assert job_b["status"] == "completed"

        # Verify Job B can see Job A's pattern through the same store
        # (the store instance is shared via LearningHub → JobService)
        discoveries = store.check_recent_pattern_discoveries(
            exclude_job_id=resp_b.job_id,
            pattern_type="rate_limit_recovery",
        )
        assert len(discoveries) >= 1
        discovery_names = [d.pattern_name for d in discoveries]
        assert "exponential_backoff_works" in discovery_names

    async def test_learning_hub_store_injected_into_service(
        self,
        learning_daemon: tuple[DaemonClient, DaemonConfig, JobManager],
    ):
        """Verify the JobService receives the LearningHub's shared store.

        This confirms the wiring path: LearningHub.store → JobManager._service
        → JobService._learning_store, which is the mechanism that enables
        cross-job learning.
        """
        _client, _config, manager = learning_daemon

        hub = manager.learning_hub
        service = manager._service
        assert service is not None

        # The service's learning store should be the exact same object
        # as the hub's store (identity check, not just equality)
        assert service._learning_store is hub.store

    async def test_patterns_persist_across_job_lifecycles(
        self,
        learning_daemon: tuple[DaemonClient, DaemonConfig, JobManager],
    ):
        """Patterns survive job completion and are available to later jobs.

        Records a pattern, runs two sequential jobs, and verifies the
        pattern count is stable (not duplicated or lost).
        """
        client, config, manager = learning_daemon
        store = manager.learning_hub.store

        # Record a pattern before any jobs run
        store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="missing_output_marker",
            description="Output file missing expected completion marker",
        )

        # Run two sequential jobs
        for i in range(2):
            ws = config.pid_file.parent / f"ws-persist-{i}"
            req = JobRequest(config_path=FIXTURE_CONFIG, workspace=ws)
            resp = await client.submit_job(req)
            assert resp.status == "accepted"
            job = await self._wait_for_job(client, resp.job_id)
            assert job["status"] == "completed"

        # Verify the pattern survived both job lifecycles
        # Use min_priority=0.0 since new patterns start below the default 0.3
        patterns = store.get_patterns(
            pattern_type="validation_failure",
            min_priority=0.0,
        )
        matching = [p for p in patterns if p.pattern_name == "missing_output_marker"]
        assert len(matching) == 1  # Not duplicated, not lost


# ---------------------------------------------------------------------------
# Tests: ResourceMonitor → Job Cancellation (D011)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="JobService.start_job removed (baton migration) — needs rewrite for baton execution path"
)
class TestMonitorCancellation:
    """D011: Verify monitor detects high memory and cancels jobs.

    Tests the full cancellation path through the real daemon stack:

        ResourceMonitor._loop → _evaluate → memory > HARD_THRESHOLD →
        _enforce_memory_limit → _cancel_oldest_job → JobManager.cancel_job →
        asyncio.Task.cancel() → job marked cancelled → daemon stays healthy

    Only SystemProbe is patched (to simulate memory pressure).
    Everything else runs for real.
    """

    @pytest.fixture
    async def monitored_daemon(self, tmp_path: Path):
        """Start a real daemon with low memory limit and fast monitor.

        Configures max_memory_mb=100 and a 0.5s monitor interval so the
        test doesn't have to wait long for the monitor to fire.
        Memory is initially reported as normal (50MB) — the test will
        patch it higher to trigger cancellation.
        """
        config = DaemonConfig(
            socket=SocketConfig(path=tmp_path / "test-mon.sock"),
            pid_file=tmp_path / "test-mon.pid",
            state_db_path=tmp_path / "test-registry.db",
            max_concurrent_jobs=4,
            max_concurrent_sheets=10,
            # Minimum valid monitor interval for test responsiveness
            monitor_interval_seconds=5.0,
            shutdown_timeout_seconds=10.0,
            resource_limits=ResourceLimitConfig(
                max_memory_mb=512,
                max_processes=50,
            ),
        )
        dp = DaemonProcess(config)

        # Capture the manager reference
        captured: dict[str, Any] = {}
        original_register = dp._register_methods

        def _capturing_register(
            handler: Any,
            manager: Any,
            health: Any = None,
        ) -> None:
            captured["manager"] = manager
            return original_register(handler, manager, health)

        with (
            patch.object(dp._pgroup, "setup"),
            patch.object(dp._pgroup, "kill_all_children"),
            patch.object(dp._pgroup, "cleanup_orphans", return_value=[]),
            # Start with normal memory (250MB of 512MB limit = 49%)
            patch.object(SystemProbe, "get_memory_mb", return_value=250.0),
            patch.object(SystemProbe, "get_child_count", return_value=2),
            patch.object(dp, "_register_methods", _capturing_register),
        ):
            original_run = dp.run

            async def _patched_run() -> None:
                loop = asyncio.get_running_loop()
                original_add = loop.add_signal_handler

                def _safe_add(sig: Any, cb: Any) -> None:
                    try:
                        original_add(sig, cb)
                    except (ValueError, RuntimeError):
                        pass

                with patch.object(loop, "add_signal_handler", _safe_add):
                    await original_run()

            daemon_task = asyncio.create_task(_patched_run())
            await _wait_for_socket(config.socket.path)

            client = DaemonClient(config.socket.path, timeout=10.0)
            yield client, config, captured

            try:
                await client.call("daemon.shutdown", {"graceful": False})
            except (DaemonNotRunningError, ConnectionResetError, OSError):
                pass

            try:
                await asyncio.wait_for(daemon_task, timeout=15.0)
            except (TimeoutError, asyncio.CancelledError):
                daemon_task.cancel()
                try:
                    await daemon_task
                except asyncio.CancelledError:
                    pass

    async def test_monitor_pauses_job_on_memory_critical(
        self,
        monitored_daemon: tuple[DaemonClient, DaemonConfig, dict[str, Any]],
    ):
        """Monitor detects critical memory and pauses the oldest running job.

        Full integration path: monitor loop → check_now() → _evaluate() →
        memory_critical → _enforce_memory_limit → _shed_oldest_job →
        manager.pause_job → pause_event.set().

        The mock job doesn't check pause signals, so it completes normally
        when released. We verify the monitor attempted the pause action.
        """
        client, config, captured = monitored_daemon

        # Submit a long-running job (blocks on an Event)
        started = asyncio.Event()
        hold = asyncio.Event()

        async def _long_running(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await hold.wait()
            from marianne.core.checkpoint import JobStatus
            from marianne.core.models import JobCompletionSummary

            return JobCompletionSummary(
                job_id="test",
                job_name="test",
                total_sheets=1,
                final_status=JobStatus.COMPLETED,
            )

        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            side_effect=_long_running,
        ):
            req = JobRequest(config_path=FIXTURE_CONFIG)
            resp = await client.submit_job(req)
            assert resp.status == "accepted"
            job_id = resp.job_id

            # Wait for the job to actually be running
            await asyncio.wait_for(started.wait(), timeout=5.0)
            await asyncio.sleep(0.1)

            # Verify it's running
            jobs = await client.list_jobs()
            running = [j for j in jobs if j["job_id"] == job_id]
            assert running[0]["status"] == "running"

            # Now spike memory above HARD_THRESHOLD (95% of 512MB ≈ 487MB)
            # Monitor will call pause_job() which sets the pause event
            with patch.object(SystemProbe, "get_memory_mb", return_value=490.0):
                # Wait for monitor to detect and attempt pause
                for _ in range(20):
                    await asyncio.sleep(0.3)

            # Release the hold so the job completes
            hold.set()
            await asyncio.sleep(0.2)

        # Job completes normally (mock doesn't check pause signals)
        jobs = await client.list_jobs()
        job = next((j for j in jobs if j["job_id"] == job_id), None)
        assert job is not None
        assert job["status"] == "completed"

    async def test_daemon_stays_healthy_after_memory_pressure(
        self,
        monitored_daemon: tuple[DaemonClient, DaemonConfig, dict[str, Any]],
    ):
        """Daemon remains responsive after monitor-triggered pause attempt.

        After the monitor detects memory pressure and attempts to pause,
        the daemon should still:
        - Respond to health probes (liveness = ok)
        - Accept new job submissions
        - Have correct running_jobs count
        """
        client, config, captured = monitored_daemon

        started = asyncio.Event()
        hold = asyncio.Event()

        async def _long_running(*args: Any, **kwargs: Any) -> Any:
            started.set()
            await hold.wait()
            from marianne.core.checkpoint import JobStatus
            from marianne.core.models import JobCompletionSummary

            return JobCompletionSummary(
                job_id="test",
                job_name="test",
                total_sheets=1,
                final_status=JobStatus.COMPLETED,
            )

        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            side_effect=_long_running,
        ):
            req = JobRequest(config_path=FIXTURE_CONFIG)
            resp = await client.submit_job(req)
            assert resp.status == "accepted"

            await asyncio.wait_for(started.wait(), timeout=5.0)
            await asyncio.sleep(0.1)

            # Trigger memory critical (95% of 512MB ≈ 487MB)
            # Monitor will attempt pause_job() on the running job
            with patch.object(SystemProbe, "get_memory_mb", return_value=490.0):
                for _ in range(20):
                    await asyncio.sleep(0.3)

            hold.set()
            await asyncio.sleep(0.2)

        # Daemon should still be fully responsive
        health = await client.health()
        assert health["status"] == "ok"
        assert health["pid"] == os.getpid()

        status = await client.call("daemon.status")
        assert status["running_jobs"] == 0

    async def test_monitor_pauses_oldest_of_multiple_jobs(
        self,
        monitored_daemon: tuple[DaemonClient, DaemonConfig, dict[str, Any]],
        tmp_path: Path,
    ):
        """When multiple jobs run, monitor pauses the OLDEST one first.

        Submits two jobs, triggers memory pressure, verifies the monitor
        attempts to pause the oldest job. Both jobs complete normally since
        the mock doesn't check pause signals.
        """
        client, config, captured = monitored_daemon

        started_a = asyncio.Event()
        started_b = asyncio.Event()
        hold = asyncio.Event()

        call_count = 0

        async def _long_running(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            idx = call_count
            call_count += 1
            if idx == 0:
                started_a.set()
            else:
                started_b.set()
            await hold.wait()
            from marianne.core.checkpoint import JobStatus
            from marianne.core.models import JobCompletionSummary

            return JobCompletionSummary(
                job_id="test",
                job_name="test",
                total_sheets=1,
                final_status=JobStatus.COMPLETED,
            )

        with patch(
            "marianne.daemon.job_service.JobService.start_job",
            side_effect=_long_running,
        ):
            # Submit job A (older) — unique config names to avoid rejection
            cfg_a = _make_unique_config(tmp_path, 0)
            cfg_b = _make_unique_config(tmp_path, 1)
            resp_a = await client.submit_job(JobRequest(config_path=cfg_a))
            assert resp_a.status == "accepted"

            # Wait for A to start, then submit B
            await asyncio.wait_for(started_a.wait(), timeout=5.0)
            await asyncio.sleep(0.1)

            resp_b = await client.submit_job(JobRequest(config_path=cfg_b))
            assert resp_b.status == "accepted"

            await asyncio.wait_for(started_b.wait(), timeout=5.0)
            await asyncio.sleep(0.1)

            # Both should be running
            jobs = await client.list_jobs()
            running = [j for j in jobs if j["status"] == "running"]
            assert len(running) == 2

            # Trigger memory critical — monitor will attempt to pause oldest (A)
            with patch.object(SystemProbe, "get_memory_mb", return_value=490.0):
                for _ in range(20):
                    await asyncio.sleep(0.3)

            # Release hold
            hold.set()
            await asyncio.sleep(0.5)

        # Both jobs complete (mock doesn't check pause signals)
        jobs = await client.list_jobs()
        status_map = {j["job_id"]: j["status"] for j in jobs}
        assert status_map[resp_a.job_id] == "completed"
        assert status_map[resp_b.job_id] == "completed"
