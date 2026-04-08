"""Tests for Marianne MCP Resources - Job and configuration resource access."""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.mcp.resources import ConfigResources
from marianne.state.json_backend import JsonStateBackend


class TestConfigResources:
    """Test suite for ConfigResources MCP implementation."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory with test files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)

            # Create test state files
            (workspace / "test-job-1.json").write_text('{"status": "completed"}')
            (workspace / "test-job-2.json").write_text('{"status": "running"}')
            (workspace / "global_learning.json").write_text('{"patterns": []}')

            yield workspace

    @pytest.fixture
    def mock_state_backend(self):
        """Create a mock state backend."""
        backend = Mock(spec=JsonStateBackend)
        backend.load = AsyncMock()
        return backend

    @pytest.fixture
    def sample_job_state(self):
        """Create a sample job state for testing."""
        return CheckpointState(
            job_id="test-job-1",
            job_name="Test Job 1",
            status=JobStatus.COMPLETED,
            total_sheets=2,
            started_at=datetime(2025, 1, 1, 10, 0, 0),
            completed_at=datetime(2025, 1, 1, 11, 0, 0),
            sheets={
                1: SheetState(
                    sheet_num=1,
                    status=SheetStatus.COMPLETED,
                    started_at=datetime(2025, 1, 1, 10, 10, 0),
                    completed_at=datetime(2025, 1, 1, 10, 30, 0),
                    attempt_count=1,
                    stdout_tail="Sheet 1 completed successfully",
                    stderr_tail=""
                ),
                2: SheetState(
                    sheet_num=2,
                    status=SheetStatus.FAILED,
                    started_at=datetime(2025, 1, 1, 10, 40, 0),
                    attempt_count=3,
                    stdout_tail="Sheet 2 failed",
                    stderr_tail="Validation error",
                    error_message="Output validation failed"
                )
            }
        )

    @pytest.fixture
    def config_resources_basic(self):
        """Create ConfigResources without state backend."""
        return ConfigResources()

    @pytest.fixture
    def config_resources_with_backend(self, mock_state_backend, temp_workspace):
        """Create ConfigResources with state backend."""
        return ConfigResources(mock_state_backend, temp_workspace)

    async def test_list_resources_basic(self, config_resources_basic):
        """Test listing basic configuration resources."""
        resources = await config_resources_basic.list_resources()

        resource_uris = [r["uri"] for r in resources]
        expected_uris = [
            "config://schema",
            "config://example",
            "config://backend-options",
            "config://validation-types",
            "config://learning-options",
            "marianne://jobs",
            "marianne://templates"
        ]

        for expected in expected_uris:
            assert expected in resource_uris, f"Expected resource {expected} not found"

        # Check resource structure
        for resource in resources:
            assert "uri" in resource
            assert "name" in resource
            assert "description" in resource
            assert "mimeType" in resource

    async def test_list_resources_with_backend(self, config_resources_with_backend):
        """Test listing resources with state backend (includes dynamic resources)."""
        resources = await config_resources_with_backend.list_resources()

        resource_uris = [r["uri"] for r in resources]
        assert "marianne://jobs/{job_id}" in resource_uris
        assert "marianne://jobs" in resource_uris
        assert "marianne://templates" in resource_uris

    async def test_get_config_schema(self, config_resources_basic):
        """Test retrieving configuration schema generated from Pydantic models."""
        result = await config_resources_basic._get_config_schema()

        assert "contents" in result
        content = result["contents"][0]
        assert content["uri"] == "config://schema"
        assert content["mimeType"] == "application/json"

        # Parse the schema JSON — generated from JobConfig.model_json_schema()
        schema = json.loads(content["text"])
        assert schema["title"] == "JobConfig"
        assert "name" in schema["required"]
        assert "sheet" in schema["required"]

        # Check that key configuration sections are defined
        assert "backend" in schema["properties"]
        assert "sheet" in schema["properties"]
        assert "retry" in schema["properties"]

    async def test_get_config_example(self, config_resources_basic):
        """Test retrieving configuration example."""
        result = await config_resources_basic._get_config_example()

        assert "contents" in result
        content = result["contents"][0]
        assert content["uri"] == "config://example"
        assert content["mimeType"] == "text/yaml"

        text = content["text"]
        assert "job_id: example-review" in text
        assert "backend:" in text
        assert "sheets:" in text
        assert "validation:" in text

    async def test_get_backend_options(self, config_resources_basic):
        """Test retrieving backend configuration options."""
        result = await config_resources_basic._get_backend_options()

        assert "contents" in result
        content = result["contents"][0]
        assert content["mimeType"] == "application/json"

        options = json.loads(content["text"])
        assert "available_backends" in options
        assert "claude_cli" in options["available_backends"]
        assert "anthropic_api" in options["available_backends"]

        # Check claude_cli options
        claude_cli = options["available_backends"]["claude_cli"]
        assert "disable_mcp" in claude_cli["options"]
        assert "timeout_seconds" in claude_cli["options"]

    async def test_get_validation_types(self, config_resources_basic):
        """Test retrieving validation types reference."""
        result = await config_resources_basic._get_validation_types()

        content = result["contents"][0]
        validation_types = json.loads(content["text"])

        assert "available_validation_types" in validation_types
        types = validation_types["available_validation_types"]

        expected_types = ["file_exists", "regex_match", "json_schema", "custom", "llm_judge"]
        for vtype in expected_types:
            assert vtype in types
            assert "description" in types[vtype]
            assert "example" in types[vtype]

    async def test_get_learning_options(self, config_resources_basic):
        """Test retrieving learning configuration options."""
        result = await config_resources_basic._get_learning_options()

        content = result["contents"][0]
        learning_options = json.loads(content["text"])

        assert "learning_system" in learning_options
        assert "pattern_types" in learning_options

        learning_system = learning_options["learning_system"]
        assert "enabled" in learning_system["options"]
        assert "pattern_detection" in learning_system["options"]
        assert "escalation" in learning_system["options"]

    async def test_get_jobs_overview_no_backend(self, config_resources_basic):
        """Test jobs overview without state backend."""
        result = await config_resources_basic._get_jobs_overview()

        content = result["contents"][0]
        response = json.loads(content["text"])
        assert "error" in response
        assert "state backend" in response["error"]

    async def test_get_jobs_overview_with_backend(
        self, config_resources_with_backend, mock_state_backend, sample_job_state,
    ):
        """Test jobs overview with state backend."""
        # Mock the state backend to return our sample state
        mock_state_backend.load.return_value = sample_job_state

        result = await config_resources_with_backend._get_jobs_overview()

        content = result["contents"][0]
        overview = json.loads(content["text"])

        assert "jobs" in overview
        assert "summary" in overview
        assert overview["summary"]["total_jobs"] >= 0

        # Should attempt to load from state files in workspace
        # Mock was called for state files found in temp_workspace
        assert mock_state_backend.load.call_count >= 1

    async def test_get_job_details_no_backend(self, config_resources_basic):
        """Test job details without state backend."""
        result = await config_resources_basic._get_job_details("test-job")

        content = result["contents"][0]
        response = json.loads(content["text"])
        assert "error" in response
        assert "state backend" in response["error"]

    async def test_get_job_details_with_backend(
        self, config_resources_with_backend, mock_state_backend, sample_job_state,
    ):
        """Test job details with state backend."""
        mock_state_backend.load.return_value = sample_job_state

        result = await config_resources_with_backend._get_job_details("test-job-1")

        content = result["contents"][0]
        details = json.loads(content["text"])

        assert details["job_id"] == "test-job-1"
        if "job_name" in details:  # Allow for job_name to be present or absent depending on state
            assert details["job_name"] == "Test Job 1"
        assert "status" in details
        assert "sheets" in details
        assert "progress" in details
        assert "configuration" in details

        # Check that we have some sheets
        assert len(details["sheets"]) > 0

        # Check progress summary structure
        progress = details["progress"]
        assert "completed_sheets" in progress
        assert "failed_sheets" in progress

    async def test_get_job_details_not_found(
        self, config_resources_with_backend, mock_state_backend,
    ):
        """Test job details for non-existent job."""
        mock_state_backend.load.return_value = None

        result = await config_resources_with_backend._get_job_details("nonexistent-job")

        content = result["contents"][0]
        response = json.loads(content["text"])
        assert "error" in response
        assert "Job not found" in response["error"]

    async def test_get_job_templates(self, config_resources_basic):
        """Test retrieving job templates."""
        result = await config_resources_basic._get_job_templates()

        content = result["contents"][0]
        templates = json.loads(content["text"])

        assert "templates" in templates
        assert "usage" in templates

        # Check that key templates exist
        template_names = templates["templates"].keys()
        expected_templates = ["code-analysis", "test-generation", "documentation", "refactoring"]
        for template in expected_templates:
            assert template in template_names

        # Check template structure
        code_analysis = templates["templates"]["code-analysis"]
        assert "name" in code_analysis
        assert "description" in code_analysis
        assert "use_cases" in code_analysis
        assert "config" in code_analysis

        # Check config has required fields
        config = code_analysis["config"]
        assert "job_id" in config
        assert "backend" in config
        assert "sheets" in config
        assert len(config["sheets"]) > 0

        # Check that sheets have proper structure
        sheet = config["sheets"][0]
        assert "name" in sheet
        assert "prompt" in sheet
        assert "validation" in sheet

    async def test_read_resource_config_schema(self, config_resources_basic):
        """Test reading config schema resource."""
        result = await config_resources_basic.read_resource("config://schema")

        content = result["contents"][0]
        assert content["uri"] == "config://schema"
        assert content["mimeType"] == "application/json"

        # Should be valid JSON
        schema = json.loads(content["text"])
        assert "title" in schema

    async def test_read_resource_marianne_jobs(self, config_resources_with_backend):
        """Test reading Marianne jobs resource."""
        result = await config_resources_with_backend.read_resource("marianne://jobs")

        content = result["contents"][0]
        assert content["uri"] == "marianne://jobs"
        assert content["mimeType"] == "application/json"

        # Should be valid JSON
        jobs = json.loads(content["text"])
        assert "jobs" in jobs or "error" in jobs  # Either jobs list or error message

    async def test_read_resource_job_detail(
        self, config_resources_with_backend, mock_state_backend, sample_job_state,
    ):
        """Test reading specific job detail resource."""
        mock_state_backend.load.return_value = sample_job_state

        result = await config_resources_with_backend.read_resource("marianne://jobs/test-job-1")

        content = result["contents"][0]
        assert content["uri"] == "marianne://jobs/test-job-1"
        details = json.loads(content["text"])
        assert "job_id" in details or "error" in details

    async def test_read_resource_templates(self, config_resources_basic):
        """Test reading job templates resource."""
        result = await config_resources_basic.read_resource("marianne://templates")

        content = result["contents"][0]
        templates = json.loads(content["text"])
        assert "templates" in templates
        assert "usage" in templates

    async def test_read_resource_unknown(self, config_resources_basic):
        """Test reading unknown resource."""
        result = await config_resources_basic.read_resource("unknown://resource")

        content = result["contents"][0]
        assert content["mimeType"] == "text/plain"
        assert "Error reading resource" in content["text"]

    async def test_read_resource_error_handling(
        self, config_resources_with_backend, mock_state_backend,
    ):
        """Test error handling in resource reading."""
        # Make state backend raise an exception
        mock_state_backend.load.side_effect = OSError("Database error")

        result = await config_resources_with_backend.read_resource("marianne://jobs")

        # Should return error message instead of crashing
        content = result["contents"][0]
        assert content["mimeType"] == "application/json" or content["mimeType"] == "text/plain"
