"""Integration tests for Marianne Dashboard.

Tests full workflows combining multiple services and components.
"""
import json
import signal
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.dashboard.app import create_app
from marianne.dashboard.services.job_control import JobControlService
from marianne.dashboard.services.sse_manager import SSEEvent, SSEManager
from marianne.state.json_backend import JsonStateBackend


@pytest.fixture
def temp_state_dir():
    """Create temporary directory for state backend."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def temp_workspace():
    """Create temporary workspace directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def backend(temp_state_dir):
    """Create test state backend."""
    return JsonStateBackend(temp_state_dir)


@pytest.fixture
def app(backend):
    """Create test app with backend."""
    return create_app(state_backend=backend, cors_origins=["*"])


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def sample_config_content():
    """Sample Marianne YAML config for testing."""
    return """
name: "test-integration-job"
description: "Integration test job"
workspace: "./workspace"

backend:
  type: claude_cli
  timeout_seconds: 300

sheet:
  size: 1
  total_items: 2
  start_item: 1

prompt:
  template: |
    Create a simple Python hello world script in hello.py

    This is sheet {{sheet_num}} of {{total_sheets}} processing item {{item_num}}.

    Expected output: A Python script that prints hello world.
"""


@pytest.fixture
async def job_control_service(backend, temp_workspace):
    """Create job control service."""
    return JobControlService(backend, temp_workspace)


@pytest.fixture
def sse_manager():
    """Create SSE manager."""
    return SSEManager()


class TestJobLifecycleIntegration:
    """Test complete job lifecycle through API."""

    @patch('marianne.dashboard.services.job_control.asyncio.create_subprocess_exec')
    async def test_start_pause_resume_cancel_flow(
        self,
        mock_subprocess,
        client,
        sample_config_content,
        temp_workspace,
    ):
        """Test complete job lifecycle: start → pause → resume → cancel.

        Uses file-based pause mechanism (signal files) instead of SIGSTOP/SIGCONT.
        """
        # Mock subprocess for job execution
        mock_process = AsyncMock()
        mock_process.pid = 12345
        mock_process.returncode = None  # Still running
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.terminate = AsyncMock()
        mock_process.kill = AsyncMock()
        mock_subprocess.return_value = mock_process

        # Write the config content to a real temp file
        import tempfile as real_tempfile
        with real_tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(sample_config_content)
            temp_config_path = f.name

        # Track state for mock os.kill behavior
        kill_call_count = 0
        process_is_dead = False

        def mock_kill_handler(pid, sig):
            """Mock os.kill to make mock process appear alive."""
            nonlocal kill_call_count, process_is_dead
            if sig == 0:  # Process existence check
                kill_call_count += 1
                if process_is_dead:
                    raise ProcessLookupError()  # Process is dead
                return  # Process exists
            elif sig == signal.SIGTERM:  # Terminate signal
                return  # Success (process will be marked dead after this)
            # For other signals, just succeed

        # Patch os.kill throughout the entire test
        with patch('os.kill', side_effect=mock_kill_handler):
            try:
                # 1. Start job using config path
                job_workspace = temp_workspace / "test-job"
                job_workspace.mkdir(exist_ok=True)
                start_response = client.post(
                    "/api/jobs",
                    json={
                        "config_path": temp_config_path,
                        "workspace": str(job_workspace),
                    }
                )

                assert start_response.status_code == 200
                start_data = start_response.json()
                assert start_data["success"] is True
                assert "job_id" in start_data
                assert start_data["status"] == "running"
                assert start_data["total_sheets"] == 2

                job_id = start_data["job_id"]

                # Verify state is saved by start_job
                from marianne.dashboard.app import get_state_backend
                backend = get_state_backend()
                job_state = await backend.load(job_id)
                assert job_state is not None
                assert job_state.status == JobStatus.RUNNING

                # 2. Pause job (file-based)
                pause_response = client.post(f"/api/jobs/{job_id}/pause")

                assert pause_response.status_code == 200
                pause_data = pause_response.json()
                assert pause_data["success"] is True
                assert pause_data["job_id"] == job_id
                # File-based pause keeps status as RUNNING until runner processes signal
                assert pause_data["status"] == JobStatus.RUNNING.value
                assert "Pause request sent" in pause_data["message"]

                # Manually update state to PAUSED to simulate runner processing the signal
                job_state = await backend.load(job_id)
                assert job_state is not None
                job_state.status = JobStatus.PAUSED
                await backend.save(job_state)

                # 3. Resume job (file-based - cleans up signal files)
                resume_response = client.post(f"/api/jobs/{job_id}/resume")

                assert resume_response.status_code == 200
                resume_data = resume_response.json()
                assert resume_data["success"] is True
                assert resume_data["job_id"] == job_id

                # 4. Cancel job - mark process as dead after SIGTERM
                # Reset call count for cancel phase
                kill_call_count = 0

                # After SIGTERM, the process should appear dead
                original_handler = mock_kill_handler

                def mock_kill_cancel(pid, sig):
                    nonlocal process_is_dead
                    if sig == signal.SIGTERM:
                        process_is_dead = True  # Mark dead after terminate
                    return original_handler(pid, sig)

                with patch('os.kill', side_effect=mock_kill_cancel):
                    cancel_response = client.post(f"/api/jobs/{job_id}/cancel")

                    assert cancel_response.status_code == 200
                    cancel_data = cancel_response.json()
                    assert cancel_data["success"] is True
                    assert cancel_data["job_id"] == job_id

            finally:
                # Clean up temp config file
                import os as os_mod
                if os_mod.path.exists(temp_config_path):
                    os_mod.unlink(temp_config_path)

    @pytest.mark.skip(reason="SSE needs async client")
    async def test_sse_receives_job_updates(self, client, sse_manager, backend):
        """Test that SSE stream receives job status updates."""
        # Create a sample job state
        job_id = "test-sse-job"
        job_state = CheckpointState(
            job_id=job_id,
            job_name="SSE Test Job",
            status=JobStatus.RUNNING,
            total_sheets=3,
            last_completed_sheet=1,
            current_sheet=2,
            worktree_path="/tmp/test",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        await backend.save(job_state)

        # Connect to SSE stream (correct URL: /api/jobs/{job_id}/stream)
        sse_response = client.get(f"/api/jobs/{job_id}/stream")
        assert sse_response.status_code == 200
        assert sse_response.headers["content-type"] == "text/event-stream"

        # Simulate job status updates through the SSE manager
        test_events = [
            SSEEvent(
                event="sheet_started",
                data=json.dumps({
                    "job_id": job_id,
                    "sheet": 2,
                    "timestamp": datetime.now().isoformat()
                }),
                id="event-1"
            ),
            SSEEvent(
                event="sheet_completed",
                data=json.dumps({
                    "job_id": job_id,
                    "sheet": 2,
                    "timestamp": datetime.now().isoformat()
                }),
                id="event-2"
            )
        ]

        # Broadcast events
        for event in test_events:
            await sse_manager.broadcast_to_job(job_id, event)

        # Read SSE response content
        sse_content = sse_response.content.decode()

        # Verify events were included
        assert "event: sheet_started" in sse_content
        assert "event: sheet_completed" in sse_content
        assert job_id in sse_content

    async def test_artifact_listing_after_job_run(
        self,
        client,
        temp_workspace,
        backend,
        sample_config_content
    ):
        """Test artifact listing after a simulated job run."""
        # Create workspace with some test artifacts
        job_workspace = temp_workspace / "artifact-test-job"
        job_workspace.mkdir(parents=True)

        # Create sample artifacts
        (job_workspace / "hello.py").write_text("print('Hello, World!')")
        (job_workspace / "output.txt").write_text("Job execution log")
        (job_workspace / "result.json").write_text('{"status": "completed"}')

        # Create job state
        job_id = "artifact-test-job"
        job_state = CheckpointState(
            job_id=job_id,
            job_name="Artifact Test Job",
            status=JobStatus.COMPLETED,
            total_sheets=2,
            last_completed_sheet=2,
            current_sheet=2,
            worktree_path=str(job_workspace),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        await backend.save(job_state)

        # List artifacts through API
        artifacts_response = client.get(f"/api/jobs/{job_id}/artifacts")

        assert artifacts_response.status_code == 200
        artifacts_data = artifacts_response.json()

        assert "files" in artifacts_data
        artifacts = artifacts_data["files"]

        # Check that our test artifacts are listed
        artifact_names = [artifact["name"] for artifact in artifacts]
        assert "hello.py" in artifact_names
        assert "output.txt" in artifact_names
        assert "result.json" in artifact_names

        # Verify artifact details
        hello_artifact = next(a for a in artifacts if a["name"] == "hello.py")
        assert hello_artifact["type"] == "file"
        assert hello_artifact["size"] > 0

    @pytest.mark.skip(reason="fchmod PermissionError in mock")
    @patch('marianne.dashboard.services.job_control.asyncio.create_subprocess_exec')
    async def test_concurrent_job_starts(
        self,
        mock_subprocess,
        client,
        sample_config_content,
        temp_workspace
    ):
        """Test starting multiple jobs concurrently."""
        # Mock subprocess for all job executions
        def create_mock_process(pid_base):
            mock_process = AsyncMock()
            mock_process.pid = pid_base
            mock_process.returncode = None
            mock_process.wait = AsyncMock(return_value=0)
            mock_process.terminate = AsyncMock()
            return mock_process

        mock_processes = [
            create_mock_process(10001),
            create_mock_process(10002),
            create_mock_process(10003)
        ]
        mock_subprocess.side_effect = mock_processes

        # Mock tempfile operations for all jobs
        with patch('tempfile.mkstemp') as mock_mkstemp, \
             patch('builtins.open'), \
             patch('os.close'):

            # Return different temp file paths for each job
            mock_mkstemp.side_effect = [
                (3, "/tmp/job1.yaml"),
                (4, "/tmp/job2.yaml"),
                (5, "/tmp/job3.yaml")
            ]

            # Start 3 jobs concurrently
            start_requests = []
            for i in range(3):
                workspace_path = temp_workspace / f"concurrent-job-{i}"
                start_requests.append({
                    "config_content": sample_config_content,
                    "workspace": str(workspace_path)
                })

            # Send requests concurrently
            responses = []
            for request in start_requests:
                response = client.post("/api/jobs", json=request)
                responses.append(response)

        # Verify all jobs started successfully
        job_ids = []
        for _, response in enumerate(responses):
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["status"] == "running"
            job_ids.append(data["job_id"])

        # Verify all job IDs are unique
        assert len(set(job_ids)) == 3

        # Verify all subprocesses were created
        assert mock_subprocess.call_count == 3

        # Test that we can manage each job independently
        for job_id in job_ids:
            # Each job should be pausable
            pause_response = client.post(f"/api/jobs/{job_id}/pause")
            assert pause_response.status_code == 200


class TestCrossServiceIntegration:
    """Test integration between different dashboard services."""

    @pytest.mark.skip(reason="SSE manager not wired in mock")
    async def test_job_control_updates_sse_manager(self, backend, temp_workspace, sse_manager):
        """Test that job control operations trigger SSE events."""
        job_service = JobControlService(backend, temp_workspace)

        # Mock SSE manager methods to track calls
        sse_manager.broadcast_to_job = AsyncMock()

        # Create a job state for testing
        job_id = "cross-service-test"
        job_state = CheckpointState(
            job_id=job_id,
            job_name="Cross Service Test",
            status=JobStatus.RUNNING,
            total_sheets=2,
            last_completed_sheet=0,
            current_sheet=1,
            worktree_path=str(temp_workspace / "test"),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        await backend.save(job_state)

        # Test pause operation
        with patch('os.kill'), patch('psutil.pid_exists', return_value=True):
            result = await job_service.pause_job(job_id)
            assert result.success is True

        # Test resume operation
        with patch('os.kill'), patch('psutil.pid_exists', return_value=True):
            result = await job_service.resume_job(job_id)
            assert result.success is True

    async def test_error_handling_across_services(self, client, temp_workspace):
        """Test error handling when services interact."""
        # Test starting job with invalid config
        invalid_config = "invalid: yaml: content: [unclosed"

        response = client.post(
            "/api/jobs",
            json={
                "config_content": invalid_config,
                "workspace": str(temp_workspace / "error-test")
            }
        )

        # Should handle YAML parsing errors gracefully
        assert response.status_code in [400, 500]
        data = response.json()
        assert "detail" in data

        # Test operations on non-existent job
        fake_job_id = "nonexistent-job-123"

        pause_response = client.post(f"/api/jobs/{fake_job_id}/pause")
        assert pause_response.status_code == 404

        resume_response = client.post(f"/api/jobs/{fake_job_id}/resume")
        assert resume_response.status_code == 404

        cancel_response = client.post(f"/api/jobs/{fake_job_id}/cancel")
        assert cancel_response.status_code == 404

        artifacts_response = client.get(f"/api/artifacts/{fake_job_id}")
        assert artifacts_response.status_code == 404

        sse_response = client.get(f"/api/stream/{fake_job_id}")
        assert sse_response.status_code == 404


class TestPerformanceAndReliability:
    """Test performance characteristics and reliability."""

    async def test_large_artifact_listing(self, client, backend, temp_workspace):
        """Test artifact listing with many files."""
        # Create workspace with many files
        job_workspace = temp_workspace / "large-artifacts-job"
        job_workspace.mkdir(parents=True)

        # Create 100 test files
        for i in range(100):
            (job_workspace / f"file_{i:03d}.txt").write_text(f"Content of file {i}")

        # Create subdirectories with files
        sub_dir = job_workspace / "subdir"
        sub_dir.mkdir()
        for i in range(20):
            (sub_dir / f"sub_file_{i}.txt").write_text(f"Sub content {i}")

        # Create job state
        job_id = "large-artifacts-job"
        job_state = CheckpointState(
            job_id=job_id,
            job_name="Large Artifacts Job",
            status=JobStatus.COMPLETED,
            total_sheets=1,
            last_completed_sheet=1,
            current_sheet=1,
            worktree_path=str(job_workspace),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        await backend.save(job_state)

        # Test artifact listing performance
        start_time = time.time()
        response = client.get(f"/api/jobs/{job_id}/artifacts")
        end_time = time.time()

        assert response.status_code == 200
        # Should complete within reasonable time (< 1 second for 120 files)
        assert (end_time - start_time) < 1.0

        data = response.json()
        artifacts = data["files"]

        # Should list all files including subdirectory files
        assert len(artifacts) >= 120  # 100 main + 20 sub + directories

    @pytest.mark.skip(reason="SSE needs async client")
    async def test_sse_connection_cleanup(self, client, sse_manager):
        """Test that SSE connections are properly cleaned up."""
        job_id = "cleanup-test-job"

        # Start an SSE connection
        response = client.get(f"/api/jobs/{job_id}/stream")
        assert response.status_code == 200

        # Simulate connection cleanup (this would happen when client disconnects)
        # In real scenarios, the SSE manager should clean up stale connections
        _initial_connections = len(sse_manager._connections.get(job_id, {}))

        # Broadcast an event to trigger connection health checks
        test_event = SSEEvent(
            event="test",
            data='{"test": "data"}',
            id="cleanup-test"
        )

        await sse_manager.broadcast_to_job(job_id, test_event)

        # Connection management is handled by the SSE stream endpoint
        # Verify the connection registry exists and can track connections
        assert hasattr(sse_manager, "_connections"), "SSE manager should have connection registry"
        assert isinstance(sse_manager._connections, dict), "Connection registry should be a dict"
