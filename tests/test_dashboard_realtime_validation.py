"""
Test suite for real-time validation functionality in Marianne Dashboard.

This module tests the enhanced score editor with real-time validation feedback,
including API integration, error markers, and validation panel behavior.
"""

import pytest
from fastapi.testclient import TestClient

from marianne.dashboard.app import create_app


class TestRealtimeValidationAPI:
    """Test real-time validation API endpoint behavior."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        app = create_app()
        return TestClient(app)

    @pytest.fixture
    def valid_yaml_content(self):
        """Valid YAML configuration for testing."""
        return """
name: "test-job"
description: "Test job for validation"

backend:
  type: "claude_cli"

sheet:
  size: 1
  total_items: 1

prompt:
  template: "echo 'Hello Marianne'"

workspace: "./workspace"
"""

    @pytest.fixture
    def invalid_yaml_content(self):
        """Invalid YAML content for testing."""
        return """
name: "test-job"
# Missing backend, sheet, and prompt - should trigger validation errors
workspace: "./workspace"
"""

    @pytest.fixture
    def yaml_syntax_error(self):
        """YAML with syntax errors."""
        return """
name: "test-job"
invalid_yaml: [unclosed bracket
description: "This will fail to parse"
"""

    def test_validate_valid_config(self, client, valid_yaml_content):
        """Test validation of a valid configuration."""
        response = client.post(
            "/api/scores/validate",
            json={
                "content": valid_yaml_content,
                "filename": "test-config.yaml"
            }
        )

        assert response.status_code == 200
        data = response.json()

        # Should be valid
        assert data["valid"] is True
        assert data["yaml_syntax_valid"] is True
        assert data["schema_valid"] is True

        # Should have minimal issues (if any)
        assert data["counts"]["ERROR"] == 0
        assert data["error_message"] is None

        # Should have config summary
        assert data["config_summary"] is not None
        assert "name" in data["config_summary"]

    def test_validate_yaml_syntax_error(self, client, yaml_syntax_error):
        """Test validation of YAML with syntax errors."""
        response = client.post(
            "/api/scores/validate",
            json={
                "content": yaml_syntax_error,
                "filename": "invalid.yaml"
            }
        )

        assert response.status_code == 200
        data = response.json()

        # Should fail at YAML syntax level
        assert data["valid"] is False
        assert data["yaml_syntax_valid"] is False
        assert data["error_message"] is not None
        assert "YAML" in data["error_message"] or "syntax" in data["error_message"].lower()

    def test_validate_schema_error(self, client, invalid_yaml_content):
        """Test validation of content that fails schema validation."""
        response = client.post(
            "/api/scores/validate",
            json={
                "content": invalid_yaml_content,
                "filename": "schema-error.yaml"
            }
        )

        assert response.status_code == 200
        data = response.json()

        # Should fail validation
        assert data["valid"] is False
        assert data["yaml_syntax_valid"] is True  # YAML parses but schema fails

        # Should have validation issues
        assert data["counts"]["ERROR"] > 0
        assert len(data["issues"]) > 0

        # Issues should have required fields
        for issue in data["issues"]:
            assert "check_id" in issue
            assert "severity" in issue
            assert "message" in issue or "description" in issue

    def test_validate_empty_content(self, client):
        """Test validation of empty content."""
        response = client.post(
            "/api/scores/validate",
            json={
                "content": "",
                "filename": "empty.yaml"
            }
        )

        assert response.status_code == 200
        data = response.json()

        # Empty content should be invalid
        assert data["valid"] is False
        assert data["error_message"] is not None

    def test_validate_missing_required_fields(self, client):
        """Test validation catches missing required fields."""
        missing_fields_yaml = """
# Missing name, backend, sheet, and prompt
workspace: "./workspace"
"""

        response = client.post(
            "/api/scores/validate",
            json={
                "content": missing_fields_yaml,
                "filename": "missing-fields.yaml"
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert data["valid"] is False
        assert data["schema_valid"] is False  # Schema validation should fail
        assert data["error_message"] is not None  # Should have schema error message

        # Check that error message mentions missing fields
        error_msg = data["error_message"].lower()
        assert "field required" in error_msg or "validation error" in error_msg

    def test_validate_request_validation(self, client):
        """Test API request validation."""
        # Missing content field
        response = client.post(
            "/api/scores/validate",
            json={"filename": "test.yaml"}
        )
        assert response.status_code == 422  # Validation error

        # Invalid JSON
        response = client.post(
            "/api/scores/validate",
            data="invalid json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422

    def test_validate_issue_format(self, client, invalid_yaml_content):
        """Test that validation issues have the correct format for frontend."""
        response = client.post(
            "/api/scores/validate",
            json={
                "content": invalid_yaml_content,
                "filename": "format-test.yaml"
            }
        )

        assert response.status_code == 200
        data = response.json()

        # Verify issue format
        for issue in data["issues"]:
            # Required fields
            assert "check_id" in issue
            assert "severity" in issue
            assert issue["severity"] in ["ERROR", "WARNING", "INFO"]

            # Message field (either message or description)
            assert "message" in issue or "description" in issue

            # Optional fields that might be present
            if "line" in issue:
                assert isinstance(issue["line"], int)
                assert issue["line"] > 0

            if "auto_fixable" in issue:
                assert isinstance(issue["auto_fixable"], bool)


class TestRealtimeValidationJavaScript:
    """Test JavaScript validation behavior (would require browser testing)."""

    def test_validation_response_processing(self):
        """Test processing of validation API response in JavaScript context."""
        # This would typically be a browser test with Selenium or Playwright
        # For now, we'll test the data structures that JavaScript expects

        mock_api_response = {
            "valid": False,
            "yaml_syntax_valid": True,
            "schema_valid": False,
            "issues": [
                {
                    "check_id": "V001",
                    "severity": "ERROR",
                    "message": "Missing required field: backend",
                    "line": 5,
                    "context": "Job configuration must specify a backend",
                    "auto_fixable": False
                },
                {
                    "check_id": "V101",
                    "severity": "WARNING",
                    "message": "Undefined template variable: {{ env_name }}",
                    "line": 12,
                    "suggestion": "Define the variable or use a default filter"
                }
            ],
            "counts": {
                "ERROR": 1,
                "WARNING": 1,
                "INFO": 0
            },
            "error_message": None
        }

        # Verify the response structure matches what JavaScript expects
        assert "valid" in mock_api_response
        assert "issues" in mock_api_response
        assert "counts" in mock_api_response

        # Verify issues have required fields for rendering
        for issue in mock_api_response["issues"]:
            assert "severity" in issue
            assert "message" in issue
            if "line" in issue:
                assert issue["line"] > 0


class TestValidationErrorMarkers:
    """Test CodeMirror error marker functionality."""

    def test_error_marker_data_structure(self):
        """Test that error data is properly structured for CodeMirror markers."""
        from marianne.validation.base import ValidationIssue, ValidationSeverity

        validation_issues = [
            ValidationIssue(
                check_id="V001",
                severity=ValidationSeverity.ERROR,
                message="Jinja syntax error",
                line=5,
                context="{{ invalid_syntax }",
                suggestion="Close the template bracket"
            ),
            ValidationIssue(
                check_id="V101",
                severity=ValidationSeverity.WARNING,
                message="Undefined variable",
                line=10
            )
        ]

        # Convert to API response format (as done in scores.py)
        api_issues = []
        for issue in validation_issues:
            api_issue = {
                "check_id": issue.check_id,
                "severity": issue.severity.value.upper(),  # Convert to uppercase for frontend
                "message": issue.message,
                "line": issue.line,
                "context": issue.context,
                "suggestion": issue.suggestion,
                "auto_fixable": issue.auto_fixable
            }
            api_issues.append(api_issue)

        # Verify structure for JavaScript consumption
        for issue in api_issues:
            assert issue["severity"] in ["ERROR", "WARNING", "INFO"]
            assert issue["line"] is None or isinstance(issue["line"], int)
            assert len(issue["message"]) > 0


class TestValidationPanelBehavior:
    """Test validation panel UI behavior."""

    def test_validation_status_mapping(self):
        """Test validation status determination logic."""
        def determine_status(error_count: int, warning_count: int) -> str:
            """Simulate JavaScript status determination logic."""
            if error_count > 0:
                return "invalid"
            elif warning_count > 0:
                return "warning"
            else:
                return "valid"

        # Test various combinations
        assert determine_status(0, 0) == "valid"
        assert determine_status(0, 1) == "warning"
        assert determine_status(1, 0) == "invalid"
        assert determine_status(2, 3) == "invalid"  # Errors take precedence

    def test_validation_message_formatting(self):
        """Test validation message formatting logic."""
        def format_message(error_count: int, warning_count: int) -> str:
            """Simulate JavaScript message formatting."""
            if error_count > 0:
                return f"{error_count} error(s)"
            elif warning_count > 0:
                return f"{warning_count} warning(s)"
            else:
                return "Configuration valid"

        assert format_message(0, 0) == "Configuration valid"
        assert format_message(1, 0) == "1 error(s)"
        assert format_message(0, 2) == "2 warning(s)"
        assert format_message(3, 1) == "3 error(s)"  # Errors take precedence


class TestValidationPerformance:
    """Test validation performance and debouncing."""

    @pytest.mark.asyncio
    async def test_debounced_validation_mock(self):
        """Test debounced validation behavior (mocked)."""
        validation_calls = []

        async def mock_validate(content):
            """Mock validation function that tracks calls."""
            validation_calls.append(content)
            return {"valid": True, "issues": [], "counts": {"ERROR": 0, "WARNING": 0, "INFO": 0}}

        # Simulate rapid typing (multiple changes within debounce window)
        content_changes = [
            "name: test",
            "name: test-job",
            "name: test-job\nbackend:",
            "name: test-job\nbackend:\n  type: claude_cli"
        ]

        # In real implementation, only the last change would trigger validation
        # due to debouncing. Here we simulate that behavior.
        final_content = content_changes[-1]

        result = await mock_validate(final_content)

        # Only one validation call should have been made
        assert len(validation_calls) == 1
        assert validation_calls[0] == final_content
        assert result["valid"] is True

    def test_validation_caching(self):
        """Test that validation results can be cached for performance."""
        # Simple cache simulation
        validation_cache = {}

        def cached_validate(content: str):
            """Mock cached validation."""
            content_hash = hash(content)

            if content_hash in validation_cache:
                return validation_cache[content_hash]

            # Simulate validation
            result = {"valid": True, "timestamp": "2024-01-01T12:00:00Z"}
            validation_cache[content_hash] = result
            return result

        content = "name: test-job\nbackend:\n  type: claude_cli"

        # First call - miss
        result1 = cached_validate(content)
        assert len(validation_cache) == 1

        # Second call - hit
        result2 = cached_validate(content)
        assert result1 == result2
        assert len(validation_cache) == 1  # No additional entries


class TestValidationIntegration:
    """Integration tests for the complete validation flow."""

    @pytest.fixture
    def app_client(self):
        """Create test client for integration tests."""
        app = create_app()
        return TestClient(app)

    def test_end_to_end_validation_flow(self, app_client):
        """Test complete validation flow from editor to API to response."""
        # Simulate user typing a configuration
        user_config = """
name: "integration-test"
description: "End-to-end validation test"

backend:
  type: "claude_cli"

sheet:
  size: 1
  total_items: 1

prompt:
  template: "echo 'Integration test successful'"

workspace: "./test-workspace"
"""

        # Submit to validation API
        response = app_client.post(
            "/api/scores/validate",
            json={
                "content": user_config,
                "filename": "integration-test.yaml"
            }
        )

        assert response.status_code == 200
        data = response.json()

        # Validate response structure
        assert "valid" in data
        assert "issues" in data
        assert "counts" in data
        assert "config_summary" in data

        # For a well-formed config, should be mostly valid
        assert data["yaml_syntax_valid"] is True
        assert data["schema_valid"] is True

        # May have warnings but should not have critical errors
        assert data["counts"]["ERROR"] == 0

        # Config summary should contain key information
        if data["config_summary"]:
            assert "name" in data["config_summary"]
            assert data["config_summary"]["name"] == "integration-test"

    def test_validation_with_file_references(self, app_client, tmp_path):
        """Test validation with file references."""
        # Create a temporary workspace
        workspace_dir = tmp_path / "test-workspace"
        workspace_dir.mkdir()

        # Create a test template file
        template_file = workspace_dir / "test-template.txt"
        template_file.write_text("Test template content")

        config_with_files = f"""
name: "file-ref-test"
backend:
  type: "claude_cli"

sheet:
  size: 1
  total_items: 1

prompt:
  template: "cat {template_file}"

workspace: "{workspace_dir}"
"""

        response = app_client.post(
            "/api/scores/validate",
            json={
                "content": config_with_files,
                "filename": "file-ref-test.yaml",
                "workspace_path": str(workspace_dir)
            }
        )

        assert response.status_code == 200
        data = response.json()

        # Should validate successfully
        assert data["yaml_syntax_valid"] is True
        assert data["schema_valid"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
