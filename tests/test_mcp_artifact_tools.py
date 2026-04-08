"""Tests for Marianne MCP Artifact Tools - Comprehensive test suite for artifact management."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.mcp.tools import ArtifactTools
from marianne.state.json_backend import JsonStateBackend


class TestArtifactTools:
    """Test suite for ArtifactTools MCP implementation."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory with test files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            # Create test directory structure
            (workspace / "job1").mkdir()
            (workspace / "job1" / "logs").mkdir()
            (workspace / "job1" / "outputs").mkdir()

            # Create test files
            (workspace / "job1" / "marianne.log").write_text(
                "INFO: Job started\nDEBUG: Processing sheet 1\n"
                "ERROR: Validation failed\nINFO: Job completed\n"
            )
            (workspace / "job1" / "job1.json").write_text('{"status": "completed"}')
            (workspace / "job1" / "outputs" / "result.txt").write_text("Test output content")
            (workspace / "job1" / "outputs" / "data.json").write_text('{"result": "success"}')
            (workspace / "job1" / ".hidden_file").write_text("Hidden content")

            # Large file for testing size limits
            (workspace / "job1" / "large_file.txt").write_text("x" * 60000)

            yield workspace

    @pytest.fixture
    def mock_state_backend(self):
        """Create a mock state backend."""
        backend = Mock(spec=JsonStateBackend)
        backend.load = AsyncMock()
        return backend

    @pytest.fixture
    def artifact_tools(self, temp_workspace) -> ArtifactTools:
        """Create ArtifactTools instance with test workspace."""
        return ArtifactTools(temp_workspace)

    @pytest.fixture
    def sample_job_state(self):
        """Create a sample job state for testing."""
        return CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            status=JobStatus.COMPLETED,
            total_sheets=2,
            started_at=datetime.now(),
            completed_at=datetime.now(),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    started_at=datetime.now(),
                    completed_at=datetime.now(),
                    attempt_count=1,
                    stdout_tail="Sheet 1 output content",
                    stderr_tail=""
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.FAILED,
                    started_at=datetime.now(),
                    attempt_count=3,
                    stdout_tail="Sheet 2 failed output",
                    stderr_tail="Error in sheet 2",
                    error_message="Validation failed"
                )
            }
        )

    async def test_list_tools(self, artifact_tools):
        """Test that all artifact tools are listed correctly."""
        tools = await artifact_tools.list_tools()

        tool_names = [tool["name"] for tool in tools]
        expected_tools = [
            "marianne_artifact_list",
            "marianne_artifact_read",
            "marianne_artifact_get_logs",
            "marianne_artifact_list_artifacts",
            "marianne_artifact_get_artifact"
        ]

        for expected in expected_tools:
            assert expected in tool_names, f"Expected tool {expected} not found"

        # Check tool schemas have required properties
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert "type" in tool["inputSchema"]
            assert "properties" in tool["inputSchema"]

    async def test_list_files_basic(self, artifact_tools, temp_workspace):
        """Test basic file listing functionality."""
        result = await artifact_tools._list_files({
            "workspace": str(temp_workspace / "job1"),
            "path": "."
        })

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]

        # Should contain summary
        assert "Summary:" in text
        assert "files" in text
        assert "directories" in text

        # Should list files and directories with emojis
        assert "📁 logs/" in text
        assert "📁 outputs/" in text
        assert "📄 marianne.log" in text
        assert "📄 job1.json" in text

    async def test_list_files_with_hidden(self, artifact_tools, temp_workspace):
        """Test file listing with hidden files included."""
        result = await artifact_tools._list_files({
            "workspace": str(temp_workspace / "job1"),
            "path": ".",
            "include_hidden": True
        })

        text = result["content"][0]["text"]
        assert "📄 .hidden_file" in text

    async def test_list_files_without_hidden(self, artifact_tools, temp_workspace):
        """Test file listing without hidden files (default)."""
        result = await artifact_tools._list_files({
            "workspace": str(temp_workspace / "job1"),
            "path": ".",
            "include_hidden": False
        })

        text = result["content"][0]["text"]
        assert ".hidden_file" not in text

    async def test_list_files_security_violation(self, artifact_tools, temp_workspace):
        """Test that file listing prevents directory traversal attacks."""
        with pytest.raises(PermissionError, match="Access denied: Path outside workspace"):
            await artifact_tools._list_files({
                "workspace": str(temp_workspace / "job1"),
                "path": "../.."
            })

    async def test_list_files_nonexistent_directory(self, artifact_tools, temp_workspace):
        """Test file listing with non-existent directory."""
        with pytest.raises(FileNotFoundError, match="Directory not found"):
            await artifact_tools._list_files({
                "workspace": str(temp_workspace / "job1"),
                "path": "nonexistent"
            })

    async def test_read_file_basic(self, artifact_tools, temp_workspace):
        """Test basic file reading functionality."""
        result = await artifact_tools._read_file({
            "workspace": str(temp_workspace / "job1"),
            "file_path": "outputs/result.txt"
        })

        text = result["content"][0]["text"]
        assert "📄 File: result.txt" in text
        assert "Size: " in text
        assert "Encoding: utf-8" in text
        assert "Test output content" in text

    async def test_read_file_json(self, artifact_tools, temp_workspace):
        """Test reading JSON file."""
        result = await artifact_tools._read_file({
            "workspace": str(temp_workspace / "job1"),
            "file_path": "outputs/data.json"
        })

        text = result["content"][0]["text"]
        assert '{"result": "success"}' in text

    async def test_read_file_too_large(self, artifact_tools, temp_workspace):
        """Test reading file that exceeds size limit."""
        with pytest.raises(ValueError, match="File too large"):
            await artifact_tools._read_file({
                "workspace": str(temp_workspace / "job1"),
                "file_path": "large_file.txt",
                "max_size": 1000
            })

    async def test_read_file_security_violation(self, artifact_tools, temp_workspace):
        """Test that file reading prevents directory traversal attacks."""
        with pytest.raises(PermissionError, match="Access denied: Path outside workspace"):
            await artifact_tools._read_file({
                "workspace": str(temp_workspace / "job1"),
                "file_path": "../../etc/passwd"
            })

    async def test_read_file_alternative_encoding(self, artifact_tools, temp_workspace):
        """Test reading file with alternative encoding."""
        # Create a file with non-UTF-8 content
        latin_file = temp_workspace / "job1" / "latin1.txt"
        latin_file.write_bytes("café".encode('latin-1'))

        result = await artifact_tools._read_file({
            "workspace": str(temp_workspace / "job1"),
            "file_path": "latin1.txt",
            "encoding": "utf-8"
        })

        text = result["content"][0]["text"]
        # Should fall back to alternative encoding
        assert "latin-1 encoding" in text or "café" in text

    async def test_get_logs_with_workspace(self, artifact_tools, temp_workspace):
        """Test log retrieval with specified workspace."""
        result = await artifact_tools._get_logs({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "lines": 50,
            "level": "all"
        })

        text = result["content"][0]["text"]
        assert "📋 Logs for Marianne Job: job1" in text
        assert "INFO: Job started" in text
        assert "DEBUG: Processing sheet 1" in text
        assert "ERROR: Validation failed" in text
        assert "INFO: Job completed" in text

    async def test_get_logs_level_filter(self, artifact_tools, temp_workspace):
        """Test log retrieval with level filtering."""
        result = await artifact_tools._get_logs({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "level": "error"
        })

        text = result["content"][0]["text"]
        assert "ERROR: Validation failed" in text
        assert "INFO: Job started" not in text
        assert "DEBUG: Processing sheet 1" not in text

    async def test_get_logs_info_filter(self, artifact_tools, temp_workspace):
        """Test log retrieval filtering for info level."""
        result = await artifact_tools._get_logs({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "level": "info"
        })

        text = result["content"][0]["text"]
        assert "INFO: Job started" in text
        assert "INFO: Job completed" in text
        assert "DEBUG: Processing sheet 1" not in text

    async def test_get_logs_line_limit(self, artifact_tools, temp_workspace):
        """Test log retrieval with line limit."""
        result = await artifact_tools._get_logs({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "lines": 2,
            "level": "all"
        })

        text = result["content"][0]["text"]
        # Should only show last 2 lines
        assert "ERROR: Validation failed" in text
        assert "INFO: Job completed" in text

    @patch.object(ArtifactTools, '_find_job_workspace')
    async def test_get_logs_auto_find_workspace(self, mock_find, artifact_tools, temp_workspace):
        """Test log retrieval with automatic workspace detection."""
        mock_find.return_value = str(temp_workspace / "job1")

        result = await artifact_tools._get_logs({
            "job_id": "job1",
            "lines": 10,
            "level": "all"
        })

        mock_find.assert_called_once_with("job1")
        text = result["content"][0]["text"]
        assert "📋 Logs for Marianne Job: job1" in text

    async def test_get_logs_no_logs_found(self, artifact_tools, temp_workspace):
        """Test log retrieval when no log files exist."""
        empty_job_dir = temp_workspace / "empty_job"
        empty_job_dir.mkdir()

        with pytest.raises(FileNotFoundError, match="No log files found"):
            await artifact_tools._get_logs({
                "job_id": "empty_job",
                "workspace": str(empty_job_dir)
            })

    async def test_list_artifacts_all_types(self, artifact_tools, temp_workspace):
        """Test listing all artifacts."""
        result = await artifact_tools._list_artifacts({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "artifact_type": "all"
        })

        text = result["content"][0]["text"]
        assert "🎯 Artifacts for Marianne Job: job1" in text
        assert "LOG Artifacts" in text
        # job1.json categorization may vary
        assert "STATE Artifacts" in text or "OTHER Artifacts" in text
        assert "marianne.log" in text
        assert "job1.json" in text
        assert "result.txt" in text

    async def test_list_artifacts_by_type(self, artifact_tools, temp_workspace):
        """Test listing artifacts filtered by type."""
        result = await artifact_tools._list_artifacts({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "artifact_type": "log"
        })

        text = result["content"][0]["text"]
        assert "LOG Artifacts" in text
        assert "marianne.log" in text
        # Should not show other types
        assert "STATE Artifacts" not in text

    async def test_list_artifacts_with_sheet_filter(self, artifact_tools, temp_workspace):
        """Test listing artifacts with sheet number filter."""
        # Create sheet-specific artifacts
        (temp_workspace / "job1" / "sheet_1_output.txt").write_text("Sheet 1 output")
        (temp_workspace / "job1" / "sheet_2_error.log").write_text("Sheet 2 error")

        result = await artifact_tools._list_artifacts({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "sheet_filter": 1
        })

        text = result["content"][0]["text"]
        assert "sheet_1_output.txt" in text
        assert "sheet_2_error.log" not in text

    @patch.object(ArtifactTools, '_find_job_workspace')
    async def test_list_artifacts_auto_find_workspace(
        self, mock_find, artifact_tools, temp_workspace,
    ):
        """Test artifact listing with automatic workspace detection."""
        mock_find.return_value = str(temp_workspace / "job1")

        result = await artifact_tools._list_artifacts({
            "job_id": "job1"
        })

        mock_find.assert_called_once_with("job1")
        text = result["content"][0]["text"]
        assert "🎯 Artifacts for Marianne Job: job1" in text

    async def test_get_artifact_text_file(self, artifact_tools, temp_workspace):
        """Test retrieving a text artifact."""
        result = await artifact_tools._get_artifact({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "artifact_path": "outputs/result.txt"
        })

        text = result["content"][0]["text"]
        assert "🎯 Marianne Job Artifact: job1" in text
        assert "Artifact: outputs/result.txt" in text
        assert "Size: " in text
        assert "Modified: " in text
        assert "Created: " in text
        assert "Test output content" in text

    async def test_get_artifact_json_file(self, artifact_tools, temp_workspace):
        """Test retrieving a JSON artifact."""
        result = await artifact_tools._get_artifact({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "artifact_path": "outputs/data.json"
        })

        text = result["content"][0]["text"]
        assert '{"result": "success"}' in text

    async def test_get_artifact_binary_file(self, artifact_tools, temp_workspace):
        """Test retrieving a binary artifact."""
        # Create a small binary file
        binary_file = temp_workspace / "job1" / "binary.dat"
        binary_file.write_bytes(b'\x00\x01\x02\x03\x04\x05')

        result = await artifact_tools._get_artifact({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "artifact_path": "binary.dat"
        })

        text = result["content"][0]["text"]
        assert "Binary Content (hex):" in text
        assert "000102030405" in text

    async def test_get_artifact_large_binary_file(self, artifact_tools, temp_workspace):
        """Test retrieving a large binary artifact."""
        # Create a larger binary file
        binary_file = temp_workspace / "job1" / "large_binary.dat"
        binary_file.write_bytes(b'\xFF' * 2000)

        result = await artifact_tools._get_artifact({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "artifact_path": "large_binary.dat"
        })

        text = result["content"][0]["text"]
        assert "Large Binary File:" in text
        assert "First 1KB (hex):" in text
        assert "2000 total bytes" in text

    async def test_get_artifact_too_large(self, artifact_tools, temp_workspace):
        """Test retrieving artifact that exceeds size limit."""
        with pytest.raises(ValueError, match="Artifact too large"):
            await artifact_tools._get_artifact({
                "job_id": "job1",
                "workspace": str(temp_workspace / "job1"),
                "artifact_path": "large_file.txt",
                "max_size": 1000
            })

    async def test_get_artifact_security_violation(self, artifact_tools, temp_workspace):
        """Test that artifact retrieval prevents directory traversal."""
        with pytest.raises(PermissionError, match="Access denied"):
            await artifact_tools._get_artifact({
                "job_id": "job1",
                "workspace": str(temp_workspace / "job1"),
                "artifact_path": "../../../etc/passwd"
            })

    async def test_get_artifact_not_found(self, artifact_tools, temp_workspace):
        """Test retrieving non-existent artifact."""
        with pytest.raises(FileNotFoundError, match="Artifact not found"):
            await artifact_tools._get_artifact({
                "job_id": "job1",
                "workspace": str(temp_workspace / "job1"),
                "artifact_path": "nonexistent.txt"
            })

    def test_find_job_workspace_with_state_file(self, artifact_tools, temp_workspace):
        """Test workspace finding with state file present."""
        # Mock the workspace_root
        artifact_tools.workspace_root = temp_workspace

        result = artifact_tools._find_job_workspace("job1")
        assert result == str(temp_workspace / "job1")

    def test_find_job_workspace_fallback(self, artifact_tools, temp_workspace):
        """Test workspace finding fallback to job_id."""
        artifact_tools.workspace_root = temp_workspace

        result = artifact_tools._find_job_workspace("nonexistent_job")
        assert result == str(temp_workspace / "nonexistent_job")

    def test_format_size_bytes(self, artifact_tools):
        """Test size formatting utility."""
        assert artifact_tools._format_size(512) == "512B"
        assert artifact_tools._format_size(1536) == "1.5KB"
        assert artifact_tools._format_size(2097152) == "2.0MB"
        assert artifact_tools._format_size(3221225472) == "3.0GB"

    async def test_call_tool_routing(self, artifact_tools, temp_workspace):
        """Test that call_tool correctly routes to appropriate methods."""
        # Test successful routing
        result = await artifact_tools.call_tool("marianne_artifact_list", {
            "workspace": str(temp_workspace / "job1")
        })
        assert "content" in result
        assert result["content"][0]["type"] == "text"

    async def test_call_tool_unknown_tool(self, artifact_tools):
        """Test call_tool with unknown tool name."""
        result = await artifact_tools.call_tool("unknown_tool", {})
        assert result["isError"] is True
        assert "Unknown artifact tool" in result["content"][0]["text"]

    async def test_call_tool_exception_handling(self, artifact_tools):
        """Test that call_tool handles exceptions gracefully."""
        result = await artifact_tools.call_tool("marianne_artifact_read", {
            "workspace": "/nonexistent",
            "file_path": "test.txt"
        })

        assert result["isError"] is True
        assert "Error:" in result["content"][0]["text"]

    async def test_shutdown(self, artifact_tools):
        """Test shutdown method (no-op for artifact tools)."""
        # Should not raise any exceptions
        await artifact_tools.shutdown()


class TestValidateWorkspacePath:
    """Tests for _validate_workspace_path security boundary edge cases."""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "job1").mkdir()
            (workspace / "job1" / "file.txt").write_text("content")
            yield workspace

    @pytest.fixture
    def artifact_tools(self, temp_workspace) -> ArtifactTools:
        return ArtifactTools(temp_workspace)

    def test_valid_workspace_and_target(self, artifact_tools, temp_workspace) -> None:
        """Normal case: target is within workspace within root."""
        ws = temp_workspace / "job1"
        target = ws / "file.txt"
        resolved_ws, resolved_target = artifact_tools._validate_workspace_path(ws, target)
        assert resolved_ws == ws.resolve()
        assert resolved_target == target.resolve()

    def test_workspace_outside_root_raises(self, temp_workspace) -> None:
        """Workspace outside workspace_root should raise PermissionError."""
        with tempfile.TemporaryDirectory() as other_dir:
            tools = ArtifactTools(temp_workspace)
            outside_ws = Path(other_dir)
            target = outside_ws / "file.txt"
            with pytest.raises(PermissionError, match="Workspace outside allowed root"):
                tools._validate_workspace_path(outside_ws, target)

    def test_target_outside_workspace_raises(self, artifact_tools, temp_workspace) -> None:
        """Target resolving outside workspace should raise PermissionError."""
        ws = temp_workspace / "job1"
        # Target points outside workspace via relative path trick
        target = ws / ".." / "file.txt"
        # Create the target so it resolves
        (temp_workspace / "file.txt").write_text("outside")
        with pytest.raises(PermissionError, match="Path outside workspace"):
            artifact_tools._validate_workspace_path(ws, target)

    def test_symlink_outside_workspace_rejected(self, artifact_tools, temp_workspace) -> None:
        """Symlinks pointing outside workspace should be rejected."""
        ws = temp_workspace / "job1"
        # Create a symlink pointing to /tmp
        link_path = ws / "sneaky_link"
        with tempfile.TemporaryDirectory() as outside:
            outside_file = Path(outside) / "secret.txt"
            outside_file.write_text("secret")
            link_path.symlink_to(outside_file)
            with pytest.raises(PermissionError, match="Path outside workspace"):
                artifact_tools._validate_workspace_path(ws, link_path)


class TestCustomLogLevelCache:
    """Tests for log level regex caching in ArtifactTools."""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "job1").mkdir()
            (workspace / "job1" / "marianne.log").write_text(
                "INFO: line1\nCRITICAL: line2\nINFO: line3\nCRITICAL: line4\n"
            )
            yield workspace

    @pytest.fixture
    def artifact_tools(self, temp_workspace) -> ArtifactTools:
        return ArtifactTools(temp_workspace)

    async def test_custom_level_filters_correctly(self, artifact_tools, temp_workspace) -> None:
        """Custom log level string should filter lines correctly."""
        result = await artifact_tools._get_logs({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "level": "CRITICAL",
            "lines": 50,
        })
        text = result["content"][0]["text"]
        assert "CRITICAL: line2" in text
        assert "CRITICAL: line4" in text
        assert "INFO: line1" not in text

    async def test_custom_level_is_cached(self, artifact_tools, temp_workspace) -> None:
        """Repeated calls with same custom level should use cache."""
        await artifact_tools._get_logs({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "level": "CRITICAL",
            "lines": 50,
        })
        assert "critical" in artifact_tools._custom_level_cache

        # Call again — should use cached pattern
        cached_pattern = artifact_tools._custom_level_cache["critical"]
        await artifact_tools._get_logs({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "level": "CRITICAL",
            "lines": 50,
        })
        # Same pattern object should still be in cache
        assert artifact_tools._custom_level_cache["critical"] is cached_pattern

    async def test_standard_levels_not_cached(self, artifact_tools, temp_workspace) -> None:
        """Pre-compiled standard levels (info, error, etc.) should not populate custom cache."""
        await artifact_tools._get_logs({
            "job_id": "job1",
            "workspace": str(temp_workspace / "job1"),
            "level": "info",
            "lines": 50,
        })
        assert "info" not in artifact_tools._custom_level_cache

    async def test_empty_log_dir_raises(self, artifact_tools, temp_workspace) -> None:
        """Should raise FileNotFoundError when no log files exist."""
        empty_job = temp_workspace / "empty"
        empty_job.mkdir()
        with pytest.raises(FileNotFoundError, match="No log files found"):
            await artifact_tools._get_logs({
                "job_id": "empty",
                "workspace": str(empty_job),
                "level": "all",
            })


class TestMakeErrorResponse:
    """Tests for the module-level _make_error_response helper."""

    def test_formats_exception_message(self) -> None:
        from marianne.mcp.tools import _make_error_response

        result = _make_error_response(ValueError("test error"))
        assert result["isError"] is True
        assert "test error" in result["content"][0]["text"]

    def test_includes_error_type(self) -> None:
        from marianne.mcp.tools import _make_error_response

        result = _make_error_response(PermissionError("access denied"))
        text = result["content"][0]["text"]
        assert "PermissionError" in text or "access denied" in text
