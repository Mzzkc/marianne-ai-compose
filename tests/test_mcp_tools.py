"""Tests for Marianne MCP Tools - Job Management and Control."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.dashboard.services.job_control import JobActionResult, JobStartResult, ProcessHealth
from marianne.mcp.tools import ArtifactTools, ControlTools, JobTools
from marianne.state.json_backend import JsonStateBackend


class TestJobTools:
    """Test suite for JobTools MCP implementation."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def mock_state_backend(self) -> Mock:
        """Create a mock state backend."""
        backend = Mock(spec=JsonStateBackend)
        backend.load = AsyncMock()
        backend.save = AsyncMock()
        return backend

    @pytest.fixture
    def mock_job_control(self) -> Mock:
        """Create a mock job control service."""
        control = Mock()
        control.start_job = AsyncMock()
        control.verify_process_health = AsyncMock()
        return control

    @pytest.fixture
    def job_tools(self, mock_state_backend: Mock, temp_workspace) -> JobTools:
        """Create JobTools instance with mocked dependencies."""
        tools = JobTools(mock_state_backend, temp_workspace)
        # Mock the job_control service
        tools.job_control = Mock()
        tools.job_control.start_job = AsyncMock()
        tools.job_control.verify_process_health = AsyncMock()
        return tools

    @pytest.fixture
    def sample_checkpoint_state(self) -> CheckpointState:
        """Create a sample checkpoint state for testing."""
        return CheckpointState(
            job_id="test-job-123",
            job_name="Test Job",
            status=JobStatus.RUNNING,
            started_at=datetime.now(),
            updated_at=datetime.now(),
            total_sheets=3,
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    started_at=datetime.now(),
                    completed_at=datetime.now(),
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.IN_PROGRESS,
                    started_at=datetime.now(),
                ),
                3: SheetState(
                    sheet_num=3,
                    status=SheetStatus.PENDING,
                ),
            },
            pid=12345,
        )

    async def test_list_tools(self, job_tools: JobTools):
        """Test that list_tools returns the expected tool definitions."""
        tools = await job_tools.list_tools()

        assert len(tools) == 3
        tool_names = [tool["name"] for tool in tools]
        assert "list_jobs" in tool_names
        assert "get_job" in tool_names
        assert "start_job" in tool_names

        # Verify tool schemas
        start_job_tool = next(tool for tool in tools if tool["name"] == "start_job")
        assert "config_path" in start_job_tool["inputSchema"]["properties"]
        assert start_job_tool["inputSchema"]["required"] == ["config_path"]

    async def test_list_jobs_placeholder(self, job_tools: JobTools):
        """Test list_jobs returns fallback message when daemon is not running."""
        # Mock daemon as not running so we get the deterministic fallback path
        job_tools._daemon_client.is_daemon_running = AsyncMock(return_value=False)
        result = await job_tools.call_tool("list_jobs", {})

        assert "content" in result
        assert len(result["content"]) == 1
        assert "Marianne MCP Job Listing" in result["content"][0]["text"]
        assert "mzt list" in result["content"][0]["text"]

    async def test_get_job_success(self, job_tools: JobTools, mock_state_backend: Mock,
                                  sample_checkpoint_state: CheckpointState):
        """Test successful get_job operation."""
        # Setup mocks
        mock_state_backend.load.return_value = sample_checkpoint_state

        # Mock the job control verify_process_health
        health = ProcessHealth(
            pid=12345,
            is_alive=True,
            is_zombie_state=False,
            process_exists=True,
            cpu_percent=5.2,
            memory_mb=128.5,
            uptime_seconds=3600.0
        )
        job_tools.job_control.verify_process_health.return_value = health

        # Execute
        result = await job_tools.call_tool("get_job", {"job_id": "test-job-123"})

        # Verify
        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "Marianne Job Details: test-job-123" in content_text
        assert "Job Name: Test Job" in content_text
        assert "Status: running" in content_text
        assert "PID: 12345" in content_text
        assert "Is Alive: True" in content_text
        assert "CPU: 5.2%" in content_text
        assert "Memory: 128.5 MB" in content_text
        assert "Uptime: 3600.0 seconds" in content_text
        assert "Progress: 1/3 sheets completed" in content_text

    async def test_get_job_not_found(self, job_tools: JobTools, mock_state_backend: Mock):
        """Test get_job when job doesn't exist."""
        mock_state_backend.load.return_value = None

        result = await job_tools.call_tool("get_job", {"job_id": "nonexistent"})

        assert "isError" in result
        assert result["isError"] is True
        assert "Job not found" in result["content"][0]["text"]

    async def test_start_job_success(self, job_tools: JobTools, temp_workspace: Path):
        """Test successful start_job operation."""
        # Create a test config file
        config_file = temp_workspace / "test-config.yaml"
        config_content = """
name: "Test Job"
description: "A test job"
backend:
  type: claude_cli
sheet:
  total_sheets: 5
"""
        config_file.write_text(config_content)

        # Mock successful job start
        start_result = JobStartResult(
            job_id="new-job-456",
            job_name="Test Job",
            status="running",
            workspace=temp_workspace,
            total_sheets=5,
            pid=54321
        )
        job_tools.job_control.start_job.return_value = start_result

        # Execute
        result = await job_tools.call_tool("start_job", {
            "config_path": str(config_file),
            "start_sheet": 1,
            "self_healing": True
        })

        # Verify
        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "✓ Marianne job started successfully!" in content_text
        assert "Job ID: new-job-456" in content_text
        assert "Job Name: Test Job" in content_text
        assert "Status: running" in content_text
        assert "Process ID: 54321" in content_text
        assert "Self-healing: Enabled" in content_text

        # Verify job_control.start_job was called correctly
        job_tools.job_control.start_job.assert_called_once_with(
            config_path=config_file,
            workspace=None,
            start_sheet=1,
            self_healing=True
        )

    async def test_start_job_config_not_found(self, job_tools: JobTools):
        """Test start_job with non-existent config file."""
        result = await job_tools.call_tool("start_job", {
            "config_path": "/nonexistent/config.yaml"
        })

        assert "isError" in result
        assert result["isError"] is True
        assert "Configuration file not found" in result["content"][0]["text"]

    async def test_start_job_control_failure(self, job_tools: JobTools, temp_workspace: Path):
        """Test start_job when job control service fails."""
        # Create a test config file
        config_file = temp_workspace / "test-config.yaml"
        config_content = """
name: "Test Job"
backend:
  type: claude_cli
sheet:
  total_sheets: 3
"""
        config_file.write_text(config_content)

        # Mock job control failure
        job_tools.job_control.start_job.side_effect = RuntimeError("Backend not available")

        # Execute
        result = await job_tools.call_tool("start_job", {
            "config_path": str(config_file)
        })

        # Verify
        assert "isError" in result
        assert result["isError"] is True
        assert "Failed to start job" in result["content"][0]["text"]


class TestControlTools:
    """Test suite for ControlTools MCP implementation."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def mock_state_backend(self) -> Mock:
        """Create a mock state backend."""
        backend = Mock(spec=JsonStateBackend)
        backend.load = AsyncMock()
        backend.save = AsyncMock()
        return backend

    @pytest.fixture
    def control_tools(self, mock_state_backend: Mock, temp_workspace) -> ControlTools:
        """Create ControlTools instance with mocked dependencies."""
        tools = ControlTools(mock_state_backend, temp_workspace)
        # Mock the job_control service
        tools.job_control = Mock()
        tools.job_control.pause_job = AsyncMock()
        tools.job_control.resume_job = AsyncMock()
        tools.job_control.cancel_job = AsyncMock()
        return tools

    async def test_list_tools(self, control_tools: ControlTools):
        """Test that list_tools returns the expected control tool definitions."""
        tools = await control_tools.list_tools()

        assert len(tools) == 3
        tool_names = [tool["name"] for tool in tools]
        assert "pause_job" in tool_names
        assert "resume_job" in tool_names
        assert "cancel_job" in tool_names

        # Verify all tools only require job_id
        for tool in tools:
            assert tool["inputSchema"]["required"] == ["job_id"]

    async def test_pause_job_success(self, control_tools: ControlTools):
        """Test successful pause_job operation."""
        # Mock successful pause
        pause_result = JobActionResult(
            success=True,
            job_id="test-job-123",
            status="running",
            message="Pause request sent. Job will pause at next sheet boundary."
        )
        control_tools.job_control.pause_job = AsyncMock(return_value=pause_result)

        # Execute
        result = await control_tools.call_tool("pause_job", {"job_id": "test-job-123"})

        # Verify
        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "✓ Pause request sent to job: test-job-123" in content_text
        assert "Status: running" in content_text
        assert "gracefully at the next sheet boundary" in content_text

    async def test_pause_job_failure(self, control_tools: ControlTools):
        """Test pause_job when operation fails."""
        # Mock failed pause
        pause_result = JobActionResult(
            success=False,
            job_id="test-job-123",
            status="failed",
            message="Job not found"
        )
        control_tools.job_control.pause_job = AsyncMock(return_value=pause_result)

        # Execute
        result = await control_tools.call_tool("pause_job", {"job_id": "test-job-123"})

        # Verify
        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "✗ Failed to pause job: test-job-123" in content_text
        assert "Status: failed" in content_text
        assert "Error: Job not found" in content_text

    async def test_resume_job_success(self, control_tools: ControlTools):
        """Test successful resume_job operation."""
        # Mock successful resume
        resume_result = JobActionResult(
            success=True,
            job_id="test-job-123",
            status="running",
            message="Job resumed successfully"
        )
        control_tools.job_control.resume_job = AsyncMock(return_value=resume_result)

        # Execute
        result = await control_tools.call_tool("resume_job", {"job_id": "test-job-123"})

        # Verify
        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "✓ Job resumed successfully: test-job-123" in content_text
        assert "Status: running" in content_text

    async def test_resume_job_failure(self, control_tools: ControlTools):
        """Test resume_job when operation fails."""
        # Mock failed resume
        resume_result = JobActionResult(
            success=False,
            job_id="test-job-123",
            status="paused",
            message="Process not found"
        )
        control_tools.job_control.resume_job = AsyncMock(return_value=resume_result)

        # Execute
        result = await control_tools.call_tool("resume_job", {"job_id": "test-job-123"})

        # Verify
        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "✗ Failed to resume job: test-job-123" in content_text
        assert "Error: Process not found" in content_text

    async def test_cancel_job_success(self, control_tools: ControlTools):
        """Test successful cancel_job operation."""
        # Mock successful cancel
        cancel_result = JobActionResult(
            success=True,
            job_id="test-job-123",
            status="cancelled",
            message="Job cancelled successfully"
        )
        control_tools.job_control.cancel_job = AsyncMock(return_value=cancel_result)

        # Execute
        result = await control_tools.call_tool("cancel_job", {"job_id": "test-job-123"})

        # Verify
        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "✓ Job cancelled successfully: test-job-123" in content_text
        assert "Status: cancelled" in content_text
        assert "permanent and cannot be undone" in content_text

    async def test_cancel_job_failure(self, control_tools: ControlTools):
        """Test cancel_job when operation fails."""
        # Mock failed cancel
        cancel_result = JobActionResult(
            success=False,
            job_id="test-job-123",
            status="running",
            message="Permission denied"
        )
        control_tools.job_control.cancel_job = AsyncMock(return_value=cancel_result)

        # Execute
        result = await control_tools.call_tool("cancel_job", {"job_id": "test-job-123"})

        # Verify
        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "✗ Failed to cancel job: test-job-123" in content_text
        assert "Error: Permission denied" in content_text

    async def test_unknown_tool(self, control_tools: ControlTools):
        """Test calling an unknown tool returns proper error."""
        result = await control_tools.call_tool("unknown_tool", {})

        assert "isError" in result
        assert result["isError"] is True
        assert "Unknown control tool: unknown_tool" in result["content"][0]["text"]

    async def test_control_service_exception(self, control_tools: ControlTools):
        """Test handling of exceptions from job control service."""
        # Mock service exception
        control_tools.job_control.pause_job.side_effect = RuntimeError("Service unavailable")

        # Execute
        result = await control_tools.call_tool("pause_job", {"job_id": "test-job-123"})

        # Verify
        assert "isError" in result
        assert result["isError"] is True
        assert "Failed to pause job: Service unavailable" in result["content"][0]["text"]


class TestArtifactTools:
    """Test suite for ArtifactTools (ensuring no regression)."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def artifact_tools(self, temp_workspace: Path) -> ArtifactTools:
        """Create ArtifactTools instance."""
        return ArtifactTools(temp_workspace)

    async def test_list_tools_complete(self, artifact_tools: ArtifactTools):
        """Test that ArtifactTools includes all expected tools."""
        tools = await artifact_tools.list_tools()

        assert len(tools) == 5
        tool_names = [tool["name"] for tool in tools]
        expected_tools = [
            "marianne_artifact_list",
            "marianne_artifact_read",
            "marianne_artifact_get_logs",
            "marianne_artifact_list_artifacts",
            "marianne_artifact_get_artifact"
        ]
        for expected in expected_tools:
            assert expected in tool_names

    async def test_artifact_tools_still_functional(
        self, artifact_tools: ArtifactTools, temp_workspace: Path,
    ):
        """Test that ArtifactTools are still functional after JobTools/ControlTools changes."""
        # Create a test file
        test_file = temp_workspace / "test.txt"
        test_file.write_text("Test content")

        # Test file listing
        result = await artifact_tools.call_tool(
            "marianne_artifact_list", {"workspace": str(temp_workspace)},
        )
        assert "content" in result
        assert "test.txt" in result["content"][0]["text"]

        # Test file reading
        result = await artifact_tools.call_tool("marianne_artifact_read", {
            "workspace": str(temp_workspace),
            "file_path": "test.txt"
        })
        assert "content" in result
        assert "Test content" in result["content"][0]["text"]


# Integration tests combining multiple tools
class TestMCPToolsIntegration:
    """Integration tests for MCP tools working together."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def mock_state_backend(self) -> Mock:
        """Create a mock state backend."""
        backend = Mock(spec=JsonStateBackend)
        backend.load = AsyncMock()
        backend.save = AsyncMock()
        return backend

    async def test_job_lifecycle_simulation(self, mock_state_backend: Mock, temp_workspace: Path):
        """Test a complete job lifecycle using MCP tools."""
        job_tools = JobTools(mock_state_backend, temp_workspace)
        control_tools = ControlTools(mock_state_backend, temp_workspace)

        # Create config file
        config_file = temp_workspace / "lifecycle-test.yaml"
        config_content = """
name: "Lifecycle Test"
backend:
  type: claude_cli
sheet:
  total_sheets: 3
"""
        config_file.write_text(config_content)

        # 1. Start job
        start_result = JobStartResult(
            job_id="lifecycle-job",
            job_name="Lifecycle Test",
            status="running",
            workspace=temp_workspace,
            total_sheets=3,
            pid=99999
        )
        job_tools.job_control.start_job = AsyncMock(return_value=start_result)

        result = await job_tools.call_tool("start_job", {"config_path": str(config_file)})
        assert "✓ Marianne job started successfully!" in result["content"][0]["text"]

        # 2. Get job status
        checkpoint_state = CheckpointState(
            job_id="lifecycle-job",
            job_name="Lifecycle Test",
            status=JobStatus.RUNNING,
            total_sheets=3,
            started_at=datetime.now(),
            updated_at=datetime.now(),
            sheets={},
            pid=99999,
        )
        mock_state_backend.load.return_value = checkpoint_state
        health = ProcessHealth(pid=99999, is_alive=True, is_zombie_state=False, process_exists=True)
        job_tools.job_control.verify_process_health = AsyncMock(return_value=health)

        result = await job_tools.call_tool("get_job", {"job_id": "lifecycle-job"})
        assert "Job Name: Lifecycle Test" in result["content"][0]["text"]
        assert "Status: running" in result["content"][0]["text"]

        # 3. Pause job
        pause_result = JobActionResult(
            success=True,
            job_id="lifecycle-job",
            status="running",
            message="Pause request sent"
        )
        control_tools.job_control.pause_job = AsyncMock(return_value=pause_result)

        result = await control_tools.call_tool("pause_job", {"job_id": "lifecycle-job"})
        assert "✓ Pause request sent to job: lifecycle-job" in result["content"][0]["text"]

        # 4. Resume job
        resume_result = JobActionResult(
            success=True,
            job_id="lifecycle-job",
            status="running",
            message="Job resumed"
        )
        control_tools.job_control.resume_job = AsyncMock(return_value=resume_result)

        result = await control_tools.call_tool("resume_job", {"job_id": "lifecycle-job"})
        assert "✓ Job resumed successfully: lifecycle-job" in result["content"][0]["text"]

        # 5. Cancel job
        cancel_result = JobActionResult(
            success=True,
            job_id="lifecycle-job",
            status="cancelled",
            message="Job cancelled"
        )
        control_tools.job_control.cancel_job = AsyncMock(return_value=cancel_result)

        result = await control_tools.call_tool("cancel_job", {"job_id": "lifecycle-job"})
        assert "✓ Job cancelled successfully: lifecycle-job" in result["content"][0]["text"]


if __name__ == "__main__":
    pytest.main([__file__])
