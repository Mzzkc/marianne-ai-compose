"""Simplified tests for Mozart MCP Tools - Job Management and Control."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.dashboard.services.job_control import JobActionResult, JobStartResult, ProcessHealth
from marianne.mcp.tools import ControlTools, JobTools
from marianne.state.json_backend import JsonStateBackend


class TestJobToolsBasic:
    """Basic test suite for JobTools MCP implementation."""

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

    async def test_list_tools(self, mock_state_backend: Mock, temp_workspace):
        """Test that list_tools returns the expected tool definitions."""
        job_tools = JobTools(mock_state_backend, temp_workspace)
        tools = await job_tools.list_tools()

        assert len(tools) == 3
        tool_names = [tool["name"] for tool in tools]
        assert "list_jobs" in tool_names
        assert "get_job" in tool_names
        assert "start_job" in tool_names

    async def test_list_jobs_placeholder(self, mock_state_backend: Mock, temp_workspace):
        """Test list_jobs returns placeholder message."""
        job_tools = JobTools(mock_state_backend, temp_workspace)
        result = await job_tools.call_tool("list_jobs", {})

        assert "content" in result
        assert len(result["content"]) == 1
        assert "Mozart MCP Job Listing" in result["content"][0]["text"]

    @patch('marianne.mcp.tools.JobControlService')
    async def test_get_job_success(
        self, mock_service_class, mock_state_backend: Mock, temp_workspace,
    ):
        """Test successful get_job operation."""
        # Setup mock state
        mock_state = CheckpointState(
            job_id="test-job-123",
            job_name="Test Job",
            status=JobStatus.RUNNING,
            started_at=datetime.now(),
            updated_at=datetime.now(),
            total_sheets=3,
            sheets={},
            pid=12345,
        )
        mock_state_backend.load.return_value = mock_state

        # Setup mock health
        mock_health = ProcessHealth(
            pid=12345,
            is_alive=True,
            is_zombie_state=False,
            process_exists=True,
            cpu_percent=5.2,
            memory_mb=128.5,
            uptime_seconds=3600.0
        )

        # Mock the service instance
        mock_service = Mock()
        mock_service.verify_process_health = AsyncMock(return_value=mock_health)
        mock_service_class.return_value = mock_service

        job_tools = JobTools(mock_state_backend, temp_workspace)
        result = await job_tools.call_tool("get_job", {"job_id": "test-job-123"})

        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "Mozart Job Details: test-job-123" in content_text
        assert "Job Name: Test Job" in content_text
        assert "Status: running" in content_text

    async def test_get_job_not_found(self, mock_state_backend: Mock, temp_workspace):
        """Test get_job when job doesn't exist."""
        mock_state_backend.load.return_value = None
        job_tools = JobTools(mock_state_backend, temp_workspace)

        result = await job_tools.call_tool("get_job", {"job_id": "nonexistent"})

        assert "isError" in result
        assert result["isError"] is True
        assert "Job not found" in result["content"][0]["text"]

    async def test_start_job_config_not_found(self, mock_state_backend: Mock, temp_workspace):
        """Test start_job with non-existent config file."""
        job_tools = JobTools(mock_state_backend, temp_workspace)
        result = await job_tools.call_tool("start_job", {
            "config_path": "/nonexistent/config.yaml"
        })

        assert "isError" in result
        assert result["isError"] is True
        assert "Configuration file not found" in result["content"][0]["text"]


class TestControlToolsBasic:
    """Basic test suite for ControlTools MCP implementation."""

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

    async def test_list_tools(self, mock_state_backend: Mock, temp_workspace):
        """Test that list_tools returns the expected control tool definitions."""
        control_tools = ControlTools(mock_state_backend, temp_workspace)
        tools = await control_tools.list_tools()

        assert len(tools) == 3
        tool_names = [tool["name"] for tool in tools]
        assert "pause_job" in tool_names
        assert "resume_job" in tool_names
        assert "cancel_job" in tool_names

    @patch('marianne.mcp.tools.JobControlService')
    async def test_pause_job_success(
        self, mock_service_class, mock_state_backend: Mock, temp_workspace,
    ):
        """Test successful pause_job operation."""
        # Mock successful pause result
        pause_result = JobActionResult(
            success=True,
            job_id="test-job-123",
            status="running",
            message="Pause request sent"
        )

        # Mock the service instance
        mock_service = Mock()
        mock_service.pause_job = AsyncMock(return_value=pause_result)
        mock_service_class.return_value = mock_service

        control_tools = ControlTools(mock_state_backend, temp_workspace)
        result = await control_tools.call_tool("pause_job", {"job_id": "test-job-123"})

        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "✓ Pause request sent to job: test-job-123" in content_text

    @patch('marianne.mcp.tools.JobControlService')
    async def test_pause_job_failure(
        self, mock_service_class, mock_state_backend: Mock, temp_workspace,
    ):
        """Test pause_job when operation fails."""
        # Mock failed pause result
        pause_result = JobActionResult(
            success=False,
            job_id="test-job-123",
            status="failed",
            message="Job not found"
        )

        # Mock the service instance
        mock_service = Mock()
        mock_service.pause_job = AsyncMock(return_value=pause_result)
        mock_service_class.return_value = mock_service

        control_tools = ControlTools(mock_state_backend, temp_workspace)
        result = await control_tools.call_tool("pause_job", {"job_id": "test-job-123"})

        assert "content" in result
        content_text = result["content"][0]["text"]
        assert "✗ Failed to pause job: test-job-123" in content_text

    async def test_unknown_tool(self, mock_state_backend: Mock, temp_workspace):
        """Test calling an unknown tool returns proper error."""
        control_tools = ControlTools(mock_state_backend, temp_workspace)
        result = await control_tools.call_tool("unknown_tool", {})

        assert "isError" in result
        assert result["isError"] is True
        assert "Unknown control tool: unknown_tool" in result["content"][0]["text"]


# Coverage tests - ensure we test key paths
class TestMCPCoverage:
    """Test coverage for critical code paths."""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def mock_state_backend(self):
        backend = Mock(spec=JsonStateBackend)
        backend.load = AsyncMock()
        return backend

    @patch('marianne.mcp.tools.JobControlService')
    async def test_start_job_with_workspace_param(
        self, mock_service_class, mock_state_backend, temp_workspace,
    ):
        """Test start_job with workspace parameter."""
        config_file = temp_workspace / "test.yaml"
        config_file.write_text("""
name: "Test Job"
backend:
  type: claude_cli
sheet:
  total_sheets: 3
""")

        start_result = JobStartResult(
            job_id="test-job",
            job_name="Test Job",
            status="running",
            workspace=temp_workspace,
            total_sheets=3,
            pid=12345
        )

        mock_service = Mock()
        mock_service.start_job = AsyncMock(return_value=start_result)
        mock_service_class.return_value = mock_service

        job_tools = JobTools(mock_state_backend, temp_workspace)
        result = await job_tools.call_tool("start_job", {
            "config_path": str(config_file),
            "workspace": str(temp_workspace),
            "self_healing": True
        })

        assert "✓ Mozart job started successfully!" in result["content"][0]["text"]
        assert "Self-healing: Enabled" in result["content"][0]["text"]

    @patch('marianne.mcp.tools.JobControlService')
    async def test_resume_job_success(self, mock_service_class, mock_state_backend, temp_workspace):
        """Test successful resume_job operation."""
        resume_result = JobActionResult(
            success=True,
            job_id="test-job",
            status="running",
            message="Job resumed"
        )

        mock_service = Mock()
        mock_service.resume_job = AsyncMock(return_value=resume_result)
        mock_service_class.return_value = mock_service

        control_tools = ControlTools(mock_state_backend, temp_workspace)
        result = await control_tools.call_tool("resume_job", {"job_id": "test-job"})

        assert "✓ Job resumed successfully: test-job" in result["content"][0]["text"]

    @patch('marianne.mcp.tools.JobControlService')
    async def test_cancel_job_success(self, mock_service_class, mock_state_backend, temp_workspace):
        """Test successful cancel_job operation."""
        cancel_result = JobActionResult(
            success=True,
            job_id="test-job",
            status="cancelled",
            message="Job cancelled"
        )

        mock_service = Mock()
        mock_service.cancel_job = AsyncMock(return_value=cancel_result)
        mock_service_class.return_value = mock_service

        control_tools = ControlTools(mock_state_backend, temp_workspace)
        result = await control_tools.call_tool("cancel_job", {"job_id": "test-job"})

        assert "✓ Job cancelled successfully: test-job" in result["content"][0]["text"]
        assert "permanent and cannot be undone" in result["content"][0]["text"]

    @patch('marianne.mcp.tools.JobControlService')
    async def test_service_exception_handling(
        self, mock_service_class, mock_state_backend, temp_workspace,
    ):
        """Test exception handling from job control service."""
        mock_service = Mock()
        mock_service.pause_job = AsyncMock(side_effect=RuntimeError("Service error"))
        mock_service_class.return_value = mock_service

        control_tools = ControlTools(mock_state_backend, temp_workspace)
        result = await control_tools.call_tool("pause_job", {"job_id": "test-job"})

        assert "isError" in result
        assert result["isError"] is True
        assert "Failed to pause job: Service error" in result["content"][0]["text"]


# ===========================================================================
# Tests: MCP Tool Schema Validation (TEST-06)
# ===========================================================================


class TestMCPToolSchemaValidation:
    """Parametrized tests validating JSON Schema structure of all MCP tools."""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def mock_state_backend(self) -> Mock:
        backend = Mock(spec=JsonStateBackend)
        backend.load = AsyncMock()
        backend.save = AsyncMock()
        return backend

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_class_name,expected_count", [
        ("JobTools", 3),
        ("ControlTools", 3),
        ("ArtifactTools", 5),
        ("ScoreTools", 0),  # Stub tools hidden from discovery
    ])
    async def test_tool_count_per_class(
        self, mock_state_backend, temp_workspace, tool_class_name, expected_count
    ):
        """Each tool class exposes the expected number of tools."""
        from marianne.mcp import tools as mcp_tools

        cls = getattr(mcp_tools, tool_class_name)
        if tool_class_name in ("JobTools", "ControlTools"):
            instance = cls(mock_state_backend, temp_workspace)
        else:
            instance = cls(temp_workspace)

        tool_list = await instance.list_tools()
        assert len(tool_list) == expected_count, (
            f"{tool_class_name} has {len(tool_list)} tools, expected {expected_count}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_class_name", [
        "JobTools", "ControlTools", "ArtifactTools", "ScoreTools",
    ])
    async def test_all_tools_have_valid_schema(
        self, mock_state_backend, temp_workspace, tool_class_name
    ):
        """Every tool must have name, description, and valid inputSchema."""
        from marianne.mcp import tools as mcp_tools

        cls = getattr(mcp_tools, tool_class_name)
        if tool_class_name in ("JobTools", "ControlTools"):
            instance = cls(mock_state_backend, temp_workspace)
        else:
            instance = cls(temp_workspace)

        for tool in await instance.list_tools():
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert isinstance(tool["name"], str), f"Tool name not a string: {tool}"
            assert len(tool["name"]) > 0, "Tool name is empty"

            assert "description" in tool, f"Tool {tool['name']} missing 'description'"
            assert isinstance(tool["description"], str)

            assert "inputSchema" in tool, f"Tool {tool['name']} missing 'inputSchema'"
            schema = tool["inputSchema"]
            assert schema.get("type") == "object", (
                f"Tool {tool['name']} schema type must be 'object'"
            )
            assert "properties" in schema, (
                f"Tool {tool['name']} schema missing 'properties'"
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_class_name", [
        "JobTools", "ControlTools", "ArtifactTools", "ScoreTools",
    ])
    async def test_required_params_exist_in_properties(
        self, mock_state_backend, temp_workspace, tool_class_name
    ):
        """All 'required' params must be defined in 'properties'."""
        from marianne.mcp import tools as mcp_tools

        cls = getattr(mcp_tools, tool_class_name)
        if tool_class_name in ("JobTools", "ControlTools"):
            instance = cls(mock_state_backend, temp_workspace)
        else:
            instance = cls(temp_workspace)

        for tool in await instance.list_tools():
            schema = tool["inputSchema"]
            required = schema.get("required", [])
            properties = schema.get("properties", {})
            for param in required:
                assert param in properties, (
                    f"Tool {tool['name']}: required param '{param}' "
                    f"not in properties {list(properties.keys())}"
                )


class TestScoreToolsBasic:
    """Tests for ScoreTools MCP implementation."""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def score_tools(self, temp_workspace):
        from marianne.mcp.tools import ScoreTools
        return ScoreTools(temp_workspace)

    @pytest.mark.asyncio
    async def test_validate_score_success(self, score_tools, temp_workspace):
        """validate_score returns stub text for valid workspace."""
        result = await score_tools.call_tool("validate_score", {
            "workspace": str(temp_workspace),
        })
        assert "isError" not in result or result.get("isError") is False
        assert "STUB IMPLEMENTATION" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_validate_score_missing_workspace(self, score_tools):
        """validate_score returns error for nonexistent workspace."""
        result = await score_tools.call_tool("validate_score", {
            "workspace": "/nonexistent/path/xyz",
        })
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_generate_score_success(self, score_tools, temp_workspace):
        """generate_score returns stub text for valid workspace."""
        result = await score_tools.call_tool("generate_score", {
            "workspace": str(temp_workspace),
        })
        assert "isError" not in result or result.get("isError") is False
        text = result["content"][0]["text"]
        assert "STUB IMPLEMENTATION" in text
        assert "score" in text.lower()

    @pytest.mark.asyncio
    async def test_generate_score_with_options(self, score_tools, temp_workspace):
        """generate_score handles optional parameters."""
        result = await score_tools.call_tool("generate_score", {
            "workspace": str(temp_workspace),
            "since_commit": "abc123",
            "detailed": True,
        })
        text = result["content"][0]["text"]
        assert "abc123" in text
        assert "True" in text

    @pytest.mark.asyncio
    async def test_validate_score_security_traversal(self, score_tools, temp_workspace):
        """validate_score blocks path traversal."""
        result = await score_tools.call_tool("validate_score", {
            "workspace": str(temp_workspace / ".." / ".." / "etc"),
        })
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_unknown_tool(self, score_tools):
        """Unknown tool name returns error."""
        result = await score_tools.call_tool("nonexistent_tool", {})
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_shutdown(self, score_tools):
        """shutdown completes without error."""
        await score_tools.shutdown()


if __name__ == "__main__":
    pytest.main([__file__])
