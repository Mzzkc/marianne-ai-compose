"""Tests for MCP server, tools, and resources modules.

B3-11: Unit tests for the MCP module using real Pydantic objects
and dataclasses instead of MagicMock where possible.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.dashboard.services.job_control import JobActionResult, ProcessHealth
from marianne.mcp.resources import ConfigResources
from marianne.mcp.server import MCPServer
from marianne.mcp.tools import ArtifactTools, ControlTools, JobTools, ScoreTools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(
    job_id: str = "test-job",
    status: JobStatus = JobStatus.COMPLETED,
    sheets: dict[int, SheetState] | None = None,
) -> CheckpointState:
    """Build a minimal CheckpointState using real Pydantic models."""
    if sheets is None:
        sheets = {
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                started_at=datetime(2026, 1, 1, 12, 0, 0),
                completed_at=datetime(2026, 1, 1, 12, 15, 0),
                attempt_count=1,
                validation_passed=True,
                stdout_tail="output",
            ),
        }

    return CheckpointState(
        job_id=job_id,
        job_name=f"Test Job {job_id}",
        status=status,
        started_at=datetime(2026, 1, 1, 12, 0, 0),
        completed_at=datetime(2026, 1, 1, 12, 30, 0),
        updated_at=datetime(2026, 1, 1, 12, 30, 0),
        total_sheets=len(sheets),
        last_completed_sheet=1,
        sheets=sheets,
    )


def _mock_state_backend(state: CheckpointState | None = None) -> AsyncMock:
    """Create a mock StateBackend."""
    backend = AsyncMock()
    backend.load = AsyncMock(return_value=state)
    backend.save = AsyncMock()
    backend.delete = AsyncMock(return_value=True)
    backend.list_jobs = AsyncMock(return_value=[state] if state else [])
    return backend


# ===========================================================================
# MCPServer tests
# ===========================================================================


class TestMCPServer:
    """Tests for MCPServer initialization, negotiation, and routing."""

    def test_server_init(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        assert server.workspace_root == tmp_path
        assert server.initialized is False

    async def test_initialize(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        result = await server.initialize({"name": "test-client"})

        assert server.initialized is True
        assert "capabilities" in result
        assert "serverInfo" in result
        assert result["serverInfo"]["name"] == "mozart-mcp-server"

    async def test_initialize_with_no_client_info(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        await server.initialize()
        assert server.initialized is True
        assert server.client_info == {}

    async def test_capabilities(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        caps = server.capabilities
        assert "tools" in caps
        assert "resources" in caps
        assert "logging" in caps
        assert caps["tools"]["listChanged"] is True

    async def test_list_tools_requires_initialization(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        with pytest.raises(RuntimeError, match="Server not initialized"):
            await server.list_tools()

    async def test_list_tools(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        await server.initialize()
        tools = await server.list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0
        tool_names = [t["name"] for t in tools]
        assert "list_jobs" in tool_names
        assert "pause_job" in tool_names
        assert "mozart_artifact_list" in tool_names

    async def test_call_tool_requires_initialization(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        with pytest.raises(RuntimeError, match="Server not initialized"):
            await server.call_tool("list_jobs")

    async def test_call_tool_unknown_tool(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        await server.initialize()
        with pytest.raises(ValueError, match="Unknown tool"):
            await server.call_tool("nonexistent_tool")

    async def test_call_tool_routes_to_job_tools(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        await server.initialize()
        result = await server.call_tool("list_jobs", {})
        assert "content" in result

    async def test_list_resources_requires_initialization(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        with pytest.raises(RuntimeError, match="Server not initialized"):
            await server.list_resources()

    async def test_list_resources(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        await server.initialize()
        resources = await server.list_resources()
        assert isinstance(resources, list)
        uris = [r["uri"] for r in resources]
        assert "config://schema" in uris
        assert "config://example" in uris

    async def test_read_resource_requires_initialization(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        with pytest.raises(RuntimeError, match="Server not initialized"):
            await server.read_resource("config://schema")

    async def test_read_resource_unknown_uri(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        await server.initialize()
        with pytest.raises(ValueError, match="Unknown resource URI"):
            await server.read_resource("unknown://foo")

    async def test_shutdown(self, tmp_path: Path) -> None:
        server = MCPServer(workspace_root=tmp_path)
        await server.initialize()
        assert server.initialized is True
        await server.shutdown()
        assert server.initialized is False


# ===========================================================================
# ConfigResources tests
# ===========================================================================


class TestConfigResources:
    """Tests for MCP resource handlers."""

    async def test_list_resources_without_backend(self) -> None:
        resources = ConfigResources()
        result = await resources.list_resources()
        assert isinstance(result, list)
        # Should still list static resources
        uris = [r["uri"] for r in result]
        assert "config://schema" in uris
        assert "config://example" in uris

    async def test_list_resources_with_backend(self) -> None:
        backend = _mock_state_backend()
        resources = ConfigResources(state_backend=backend)
        result = await resources.list_resources()
        uris = [r["uri"] for r in result]
        assert "mozart://jobs/{job_id}" in uris

    async def test_get_config_schema(self) -> None:
        resources = ConfigResources()
        result = await resources.read_resource("config://schema")
        assert "contents" in result
        content = result["contents"][0]
        assert content["uri"] == "config://schema"
        assert content["mimeType"] == "application/json"
        schema = json.loads(content["text"])
        assert "properties" in schema or "$defs" in schema

    async def test_get_config_example(self) -> None:
        resources = ConfigResources()
        result = await resources.read_resource("config://example")
        content = result["contents"][0]
        assert "sheets:" in content["text"]
        assert "backend:" in content["text"]

    async def test_get_backend_options(self) -> None:
        resources = ConfigResources()
        result = await resources.read_resource("config://backend-options")
        content = result["contents"][0]
        data = json.loads(content["text"])
        assert "available_backends" in data
        assert "claude_cli" in data["available_backends"]
        assert "anthropic_api" in data["available_backends"]

    async def test_get_validation_types(self) -> None:
        resources = ConfigResources()
        result = await resources.read_resource("config://validation-types")
        content = result["contents"][0]
        data = json.loads(content["text"])
        assert "available_validation_types" in data
        assert "file_exists" in data["available_validation_types"]
        assert "regex_match" in data["available_validation_types"]

    async def test_get_learning_options(self) -> None:
        resources = ConfigResources()
        result = await resources.read_resource("config://learning-options")
        content = result["contents"][0]
        data = json.loads(content["text"])
        assert "learning_system" in data
        assert "pattern_types" in data

    async def test_get_jobs_overview_no_backend(self) -> None:
        resources = ConfigResources(state_backend=None)
        result = await resources.read_resource("mozart://jobs")
        content = result["contents"][0]
        data = json.loads(content["text"])
        assert "error" in data

    async def test_get_jobs_overview_with_backend(self, tmp_path: Path) -> None:
        state = _make_state()
        state_file = tmp_path / "test-job.json"
        state_file.write_text("{}")

        backend = _mock_state_backend(state)
        resources = ConfigResources(state_backend=backend, workspace_root=tmp_path)
        result = await resources.read_resource("mozart://jobs")
        content = result["contents"][0]
        data = json.loads(content["text"])
        assert "jobs" in data
        assert "summary" in data

    async def test_get_job_details_not_found(self) -> None:
        backend = _mock_state_backend(None)
        resources = ConfigResources(state_backend=backend)
        result = await resources.read_resource("mozart://jobs/nonexistent")
        content = result["contents"][0]
        data = json.loads(content["text"])
        assert "error" in data

    async def test_get_job_details_found(self) -> None:
        state = _make_state(job_id="my-job")
        backend = _mock_state_backend(state)
        resources = ConfigResources(state_backend=backend)
        result = await resources.read_resource("mozart://jobs/my-job")
        content = result["contents"][0]
        data = json.loads(content["text"])
        assert data.get("job_id") == "my-job" or "error" not in data

    async def test_get_job_templates(self) -> None:
        resources = ConfigResources()
        result = await resources.read_resource("mozart://templates")
        content = result["contents"][0]
        data = json.loads(content["text"])
        assert "templates" in data
        assert "code-analysis" in data["templates"]
        assert "test-generation" in data["templates"]

    async def test_unknown_uri(self) -> None:
        resources = ConfigResources()
        result = await resources.read_resource("config://nonexistent")
        content = result["contents"][0]
        assert "Error" in content["text"]


# ===========================================================================
# JobTools tests
# ===========================================================================


class TestJobTools:
    """Tests for MCP job management tools."""

    async def test_list_tools(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = JobTools(backend, tmp_path)
        result = await tools.list_tools()
        names = [t["name"] for t in result]
        assert "list_jobs" in names
        assert "get_job" in names
        assert "start_job" in names

    async def test_list_jobs(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = JobTools(backend, tmp_path)
        result = await tools.call_tool("list_jobs", {})
        assert "content" in result
        assert not result.get("isError")

    async def test_list_jobs_with_filter(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = JobTools(backend, tmp_path)
        result = await tools.call_tool("list_jobs", {"status_filter": "running", "limit": 10})
        assert "content" in result
        text = result["content"][0]["text"]
        assert "running" in text.lower()

    async def test_get_job_not_found(self, tmp_path: Path) -> None:
        backend = _mock_state_backend(None)
        tools = JobTools(backend, tmp_path)
        result = await tools.call_tool("get_job", {"job_id": "nonexistent"})
        assert result.get("isError") is True

    async def test_get_job_found(self, tmp_path: Path) -> None:
        state = _make_state(job_id="my-job")
        backend = _mock_state_backend(state)

        health = ProcessHealth(
            pid=12345,
            is_alive=True,
            is_zombie_state=False,
            process_exists=True,
            uptime_seconds=120.0,
            cpu_percent=5.0,
            memory_mb=50.0,
        )

        tools = JobTools(backend, tmp_path)
        tools.job_control.verify_process_health = AsyncMock(return_value=health)

        result = await tools.call_tool("get_job", {"job_id": "my-job"})
        assert not result.get("isError")
        text = result["content"][0]["text"]
        assert "my-job" in text or "Test Job" in text

    async def test_unknown_tool(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = JobTools(backend, tmp_path)
        result = await tools.call_tool("nonexistent", {})
        assert result.get("isError") is True

    async def test_shutdown(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = JobTools(backend, tmp_path)
        await tools.shutdown()  # Should not raise


# ===========================================================================
# ControlTools tests
# ===========================================================================


class TestControlTools:
    """Tests for MCP job control tools."""

    async def test_list_tools(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = ControlTools(backend, tmp_path)
        result = await tools.list_tools()
        names = [t["name"] for t in result]
        assert "pause_job" in names
        assert "resume_job" in names
        assert "cancel_job" in names

    async def test_pause_job_success(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = ControlTools(backend, tmp_path)
        action_result = JobActionResult(
            success=True, job_id="test-job", status="paused", message="Job paused",
        )
        tools.job_control.pause_job = AsyncMock(return_value=action_result)

        result = await tools.call_tool("pause_job", {"job_id": "test-job"})
        assert not result.get("isError")
        text = result["content"][0]["text"]
        assert "paused" in text.lower() or "Pause" in text

    async def test_pause_job_failure(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = ControlTools(backend, tmp_path)
        action_result = JobActionResult(
            success=False, job_id="test-job", status="running", message="Cannot pause",
        )
        tools.job_control.pause_job = AsyncMock(return_value=action_result)

        result = await tools.call_tool("pause_job", {"job_id": "test-job"})
        text = result["content"][0]["text"]
        assert "Failed" in text or "Cannot" in text

    async def test_resume_job(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = ControlTools(backend, tmp_path)
        action_result = JobActionResult(
            success=True, job_id="test-job", status="running", message="Job resumed",
        )
        tools.job_control.resume_job = AsyncMock(return_value=action_result)

        result = await tools.call_tool("resume_job", {"job_id": "test-job"})
        assert not result.get("isError")

    async def test_cancel_job(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = ControlTools(backend, tmp_path)
        action_result = JobActionResult(
            success=True, job_id="test-job", status="cancelled", message="Job cancelled",
        )
        tools.job_control.cancel_job = AsyncMock(return_value=action_result)

        result = await tools.call_tool("cancel_job", {"job_id": "test-job"})
        assert not result.get("isError")
        assert "cancelled" in result["content"][0]["text"].lower()

    async def test_unknown_control_tool(self, tmp_path: Path) -> None:
        backend = _mock_state_backend()
        tools = ControlTools(backend, tmp_path)
        result = await tools.call_tool("nonexistent", {})
        assert result.get("isError") is True


# ===========================================================================
# ArtifactTools tests
# ===========================================================================


class TestArtifactTools:
    """Tests for MCP artifact management tools."""

    async def test_list_tools(self, tmp_path: Path) -> None:
        tools = ArtifactTools(tmp_path)
        result = await tools.list_tools()
        names = [t["name"] for t in result]
        assert "mozart_artifact_list" in names
        assert "mozart_artifact_read" in names
        assert "mozart_artifact_get_logs" in names

    async def test_list_files(self, tmp_path: Path) -> None:
        (tmp_path / "test.txt").write_text("hello")
        (tmp_path / "subdir").mkdir()

        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_list", {"workspace": str(tmp_path)})
        assert not result.get("isError")
        text = result["content"][0]["text"]
        assert "test.txt" in text

    async def test_list_files_security_traversal(self, tmp_path: Path) -> None:
        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_list", {
            "workspace": str(tmp_path),
            "path": "../../etc",
        })
        assert result.get("isError") is True

    async def test_read_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_read", {
            "workspace": str(tmp_path),
            "file_path": "test.txt",
        })
        assert not result.get("isError")
        assert "hello world" in result["content"][0]["text"]

    async def test_read_file_not_found(self, tmp_path: Path) -> None:
        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_read", {
            "workspace": str(tmp_path),
            "file_path": "nonexistent.txt",
        })
        assert result.get("isError") is True

    async def test_read_file_too_large(self, tmp_path: Path) -> None:
        big_file = tmp_path / "big.txt"
        big_file.write_text("x" * 1000)

        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_read", {
            "workspace": str(tmp_path),
            "file_path": "big.txt",
            "max_size": 10,
        })
        assert result.get("isError") is True

    async def test_read_file_security_traversal(self, tmp_path: Path) -> None:
        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_read", {
            "workspace": str(tmp_path),
            "file_path": "../../etc/passwd",
        })
        assert result.get("isError") is True

    async def test_get_logs_no_log_files(self, tmp_path: Path) -> None:
        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_get_logs", {
            "job_id": "test-job",
            "workspace": str(tmp_path),
        })
        assert result.get("isError") is True

    async def test_get_logs_with_log_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "mozart.log"
        log_file.write_text("INFO Starting job\nERROR Something failed\nINFO Done\n")

        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_get_logs", {
            "job_id": "test-job",
            "workspace": str(tmp_path),
        })
        assert not result.get("isError")
        text = result["content"][0]["text"]
        assert "Starting job" in text

    async def test_get_logs_level_filter(self, tmp_path: Path) -> None:
        log_file = tmp_path / "mozart.log"
        log_file.write_text("INFO Starting\nERROR Failed\nINFO Done\n")

        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_get_logs", {
            "job_id": "test-job",
            "workspace": str(tmp_path),
            "level": "error",
        })
        text = result["content"][0]["text"]
        assert "Failed" in text

    async def test_list_artifacts(self, tmp_path: Path) -> None:
        (tmp_path / "output.txt").write_text("result")
        (tmp_path / "error.log").write_text("err")
        (tmp_path / "state.json").write_text("{}")

        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_list_artifacts", {
            "job_id": "test-job",
            "workspace": str(tmp_path),
        })
        assert not result.get("isError")
        text = result["content"][0]["text"]
        assert "output.txt" in text or "error.log" in text

    async def test_get_artifact(self, tmp_path: Path) -> None:
        artifact = tmp_path / "results.json"
        artifact.write_text('{"status": "ok"}')

        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_get_artifact", {
            "job_id": "test-job",
            "artifact_path": "results.json",
            "workspace": str(tmp_path),
        })
        assert not result.get("isError")
        assert "ok" in result["content"][0]["text"]

    async def test_get_artifact_security_traversal(self, tmp_path: Path) -> None:
        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("mozart_artifact_get_artifact", {
            "job_id": "test-job",
            "artifact_path": "../../etc/passwd",
            "workspace": str(tmp_path),
        })
        assert result.get("isError") is True

    async def test_categorize_artifact(self) -> None:
        assert ArtifactTools._categorize_artifact(Path("app.log")) == "log"
        assert ArtifactTools._categorize_artifact(Path("state.json")) == "state"
        assert ArtifactTools._categorize_artifact(Path("checkpoint.json")) == "state"
        assert ArtifactTools._categorize_artifact(Path("error-output.txt")) == "error"
        assert ArtifactTools._categorize_artifact(Path("output-results.md")) == "output"
        assert ArtifactTools._categorize_artifact(Path("data.csv")) == "other"

    def test_format_size(self, tmp_path: Path) -> None:
        tools = ArtifactTools(tmp_path)
        assert tools._format_size(500) == "500B"
        assert tools._format_size(1024) == "1.0KB"
        assert tools._format_size(1024 * 1024) == "1.0MB"
        assert tools._format_size(1024 * 1024 * 1024) == "1.0GB"

    async def test_unknown_artifact_tool(self, tmp_path: Path) -> None:
        tools = ArtifactTools(tmp_path)
        result = await tools.call_tool("nonexistent", {})
        assert result.get("isError") is True


# ===========================================================================
# ScoreTools tests
# ===========================================================================


class TestScoreTools:
    """Tests for MCP score tools (stub implementation, hidden from discovery)."""

    async def test_list_tools_empty_for_stubs(self, tmp_path: Path) -> None:
        """Stub tools should not be discoverable by MCP clients."""
        tools = ScoreTools(tmp_path)
        result = await tools.list_tools()
        assert result == [], "Stub tools should not be registered for discovery"

    async def test_validate_score(self, tmp_path: Path) -> None:
        tools = ScoreTools(tmp_path)
        result = await tools.call_tool("validate_score", {"workspace": str(tmp_path)})
        assert not result.get("isError")
        text = result["content"][0]["text"]
        assert "STUB" in text or "Quality Score" in text

    async def test_validate_score_workspace_not_found(self, tmp_path: Path) -> None:
        tools = ScoreTools(tmp_path)
        result = await tools.call_tool("validate_score", {
            "workspace": str(tmp_path / "nonexistent"),
        })
        assert result.get("isError") is True

    async def test_generate_score(self, tmp_path: Path) -> None:
        tools = ScoreTools(tmp_path)
        result = await tools.call_tool("generate_score", {"workspace": str(tmp_path)})
        assert not result.get("isError")
        text = result["content"][0]["text"]
        assert "STUB" in text or "Quality Score" in text

    async def test_unknown_score_tool(self, tmp_path: Path) -> None:
        tools = ScoreTools(tmp_path)
        result = await tools.call_tool("nonexistent", {})
        assert result.get("isError") is True


# ===========================================================================
# Parametrized tests — reduce setup duplication (FIX-46)
# ===========================================================================


class MockFactory:
    """Centralized factory for creating test mocks and fixtures.

    Reduces scattered mock construction across test classes by providing
    reusable builders with sensible defaults.
    """

    @staticmethod
    def state_backend(state: CheckpointState | None = None) -> AsyncMock:
        """Create a mock StateBackend with standard method signatures."""
        return _mock_state_backend(state)

    @staticmethod
    def job_action(
        success: bool, job_id: str = "test-job",
        status: str = "running", message: str = "",
    ) -> JobActionResult:
        return JobActionResult(
            success=success, job_id=job_id, status=status, message=message,
        )


class TestControlToolsParametrized:
    """Parametrized tests for ControlTools operations.

    Tests pause/resume/cancel via a single parametrized test,
    eliminating the repeated backend+tools+action_result setup.
    """

    @pytest.mark.parametrize(
        "tool_name,action_method,status,expected_text",
        [
            ("pause_job", "pause_job", "paused", "paus"),
            ("resume_job", "resume_job", "running", "resum"),
            ("cancel_job", "cancel_job", "cancelled", "cancel"),
        ],
    )
    async def test_control_action_success(
        self, tmp_path: Path, tool_name: str, action_method: str,
        status: str, expected_text: str,
    ) -> None:
        """All control actions share the same success pattern."""
        backend = MockFactory.state_backend()
        tools = ControlTools(backend, tmp_path)
        action_result = MockFactory.job_action(
            success=True, status=status, message=f"Job {status}",
        )
        setattr(tools.job_control, action_method, AsyncMock(return_value=action_result))

        result = await tools.call_tool(tool_name, {"job_id": "test-job"})
        assert not result.get("isError")
        text = result["content"][0]["text"].lower()
        assert expected_text in text

    @pytest.mark.parametrize("tool_name,action_method", [
        ("pause_job", "pause_job"),
        ("resume_job", "resume_job"),
        ("cancel_job", "cancel_job"),
    ])
    async def test_control_action_failure(
        self, tmp_path: Path, tool_name: str, action_method: str,
    ) -> None:
        """All control actions share the same failure pattern."""
        backend = MockFactory.state_backend()
        tools = ControlTools(backend, tmp_path)
        action_result = MockFactory.job_action(
            success=False, status="running", message="Cannot perform action",
        )
        setattr(tools.job_control, action_method, AsyncMock(return_value=action_result))

        result = await tools.call_tool(tool_name, {"job_id": "test-job"})
        text = result["content"][0]["text"]
        assert "Failed" in text or "Cannot" in text or result.get("isError")


class TestArtifactCategorization:
    """Parametrized tests for artifact categorization logic."""

    @pytest.mark.parametrize("filename,expected_category", [
        ("app.log", "log"),
        ("debug.log", "log"),
        ("state.json", "state"),
        ("checkpoint.json", "state"),
        ("error-output.txt", "error"),
        ("output-results.md", "output"),
        ("data.csv", "other"),
        ("image.png", "other"),
    ])
    def test_categorize_artifact(self, filename: str, expected_category: str) -> None:
        assert ArtifactTools._categorize_artifact(Path(filename)) == expected_category

    @pytest.mark.parametrize("size,expected", [
        (500, "500B"),
        (1024, "1.0KB"),
        (1024 * 1024, "1.0MB"),
        (1024 * 1024 * 1024, "1.0GB"),
        (0, "0B"),
    ])
    def test_format_size(self, tmp_path: Path, size: int, expected: str) -> None:
        tools = ArtifactTools(tmp_path)
        assert tools._format_size(size) == expected


class TestResourceURIParametrized:
    """Parametrized tests for ConfigResources URI routing."""

    @pytest.mark.parametrize("uri,expected_key", [
        ("config://schema", "properties"),
        ("config://backend-options", "available_backends"),
        ("config://validation-types", "available_validation_types"),
        ("config://learning-options", "learning_system"),
    ])
    async def test_static_resource_uris(self, uri: str, expected_key: str) -> None:
        """All static config resources should return valid JSON with expected keys."""
        resources = ConfigResources()
        result = await resources.read_resource(uri)
        content = result["contents"][0]
        data = json.loads(content["text"])
        assert expected_key in data or "$defs" in data
