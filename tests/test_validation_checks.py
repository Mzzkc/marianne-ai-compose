"""Tests for the enhanced validation system.

Tests cover:
- Individual validation checks (Jinja, paths, config)
- ValidationRunner orchestration
- ValidationReporter output formatting
- Edge cases and error handling
"""

from pathlib import Path
from textwrap import dedent

import pytest

from marianne.core.config import JobConfig
from marianne.validation import (
    ValidationRunner,
    ValidationSeverity,
    create_default_checks,
)
from marianne.validation.base import ValidationIssue
from marianne.validation.checks.config import (
    RegexPatternCheck,
    TimeoutRangeCheck,
    ValidationTypeCheck,
)
from marianne.validation.checks.jinja import (
    JinjaSyntaxCheck,
    JinjaUndefinedVariableCheck,
)
from marianne.validation.checks.paths import (
    TemplateFileExistsCheck,
    WorkingDirectoryCheck,
    WorkspaceParentExistsCheck,
)
from marianne.validation.reporter import ValidationReporter

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def minimal_config_yaml() -> str:
    """Minimal valid YAML config."""
    return dedent("""
        name: test-job
        sheet:
          size: 10
          total_items: 100
        prompt:
          template: "Process sheet {{ sheet_num }}"
    """).strip()


@pytest.fixture
def minimal_config(minimal_config_yaml: str, tmp_path: Path) -> tuple[JobConfig, Path, str]:
    """Create a minimal valid config for testing."""
    config_path = tmp_path / "test-config.yaml"
    config_path.write_text(minimal_config_yaml)
    config = JobConfig.from_yaml(config_path)
    return config, config_path, minimal_config_yaml


# ============================================================================
# Jinja Check Tests
# ============================================================================


class TestJinjaSyntaxCheck:
    """Tests for JinjaSyntaxCheck (V001)."""

    def test_valid_template_passes(self, minimal_config: tuple[JobConfig, Path, str]) -> None:
        """Valid Jinja template produces no issues."""
        config, config_path, raw_yaml = minimal_config
        check = JinjaSyntaxCheck()

        issues = check.check(config, config_path, raw_yaml)

        assert len(issues) == 0

    def test_catches_unclosed_expression(self, tmp_path: Path) -> None:
        """Catches unclosed {{ expression."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Process sheet {{ sheet_num of {{ total_sheets }}"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = JinjaSyntaxCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V001"
        assert issues[0].severity == ValidationSeverity.ERROR
        assert "syntax error" in issues[0].message.lower()

    def test_catches_unclosed_block(self, tmp_path: Path) -> None:
        """Catches unclosed {% if %} block."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: |
                {% if sheet_num > 1 %}
                Continue from previous sheet
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = JinjaSyntaxCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V001"
        assert issues[0].severity == ValidationSeverity.ERROR

    def test_checks_external_template_file(self, tmp_path: Path) -> None:
        """Validates external template files."""
        # Create template with syntax error
        template_path = tmp_path / "template.j2"
        template_path.write_text("Hello {{ name")

        yaml_content = dedent(f"""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template_file: {template_path}
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = JinjaSyntaxCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V001"


class TestJinjaUndefinedVariableCheck:
    """Tests for JinjaUndefinedVariableCheck (V101)."""

    def test_all_variables_defined_passes(
        self, minimal_config: tuple[JobConfig, Path, str],
    ) -> None:
        """No warnings when all variables are defined."""
        config, config_path, raw_yaml = minimal_config
        check = JinjaUndefinedVariableCheck()

        issues = check.check(config, config_path, raw_yaml)

        assert len(issues) == 0

    def test_catches_undefined_variable(self, tmp_path: Path) -> None:
        """Warns about undefined variables."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Process {{ custom_var }}"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = JinjaUndefinedVariableCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V101"
        assert issues[0].severity == ValidationSeverity.WARNING
        assert "custom_var" in issues[0].message

    def test_suggests_similar_variable(self, tmp_path: Path) -> None:
        """Suggests similar variable names for typos."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Process sheet {{ shee_num }}"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = JinjaUndefinedVariableCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].suggestion is not None
        assert "sheet_num" in issues[0].suggestion

    def test_accepts_custom_variables(self, tmp_path: Path) -> None:
        """Custom variables in prompt.variables are recognized."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Hello {{ custom_var }}"
              variables:
                custom_var: "world"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = JinjaUndefinedVariableCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_ignores_builtin_variables(self, tmp_path: Path) -> None:
        """Built-in Jinja variables don't trigger warnings."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Sheet {{ loop.index if loop is defined else sheet_num }}"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = JinjaUndefinedVariableCheck()
        issues = check.check(config, config_path, yaml_content)

        # 'loop' is a builtin, shouldn't warn
        assert not any("loop" in i.message for i in issues)


# ============================================================================
# Path Check Tests
# ============================================================================


class TestWorkspaceParentExistsCheck:
    """Tests for WorkspaceParentExistsCheck (V002)."""

    def test_existing_parent_passes(self, minimal_config: tuple[JobConfig, Path, str]) -> None:
        """No error when workspace parent exists."""
        config, config_path, raw_yaml = minimal_config
        check = WorkspaceParentExistsCheck()

        # The tmp_path directory exists
        issues = check.check(config, config_path, raw_yaml)

        assert not any(i.check_id == "V002" for i in issues)

    def test_catches_missing_parent(self, tmp_path: Path) -> None:
        """Error when workspace parent doesn't exist."""
        yaml_content = dedent("""
            name: test-job
            workspace: /nonexistent/deeply/nested/workspace
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = WorkspaceParentExistsCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V002"
        assert issues[0].severity == ValidationSeverity.ERROR
        assert issues[0].auto_fixable is True


class TestTemplateFileExistsCheck:
    """Tests for TemplateFileExistsCheck (V003)."""

    def test_existing_template_passes(self, tmp_path: Path) -> None:
        """No error when template file exists."""
        template_path = tmp_path / "template.j2"
        template_path.write_text("Hello {{ name }}")

        yaml_content = dedent(f"""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template_file: {template_path}
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = TemplateFileExistsCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_catches_missing_template(self, tmp_path: Path) -> None:
        """Error when template file doesn't exist."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template_file: /nonexistent/template.j2
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = TemplateFileExistsCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V003"
        assert issues[0].severity == ValidationSeverity.ERROR


class TestWorkingDirectoryCheck:
    """Tests for WorkingDirectoryCheck (V005)."""

    def test_existing_working_dir_passes(self, tmp_path: Path) -> None:
        """No error when working directory exists."""
        working_dir = tmp_path / "work"
        working_dir.mkdir()

        yaml_content = dedent(f"""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: claude_cli
              working_directory: {working_dir}
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = WorkingDirectoryCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_catches_missing_working_dir(self, tmp_path: Path) -> None:
        """Error when working directory doesn't exist."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: claude_cli
              working_directory: /nonexistent/dir
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = WorkingDirectoryCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V005"
        assert issues[0].auto_fixable is True


# ============================================================================
# Config Check Tests
# ============================================================================


class TestRegexPatternCheck:
    """Tests for RegexPatternCheck (V007)."""

    def test_valid_regex_passes(self, tmp_path: Path) -> None:
        """No error for valid regex patterns."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            validations:
              - type: content_regex
                path: /some/file
                pattern: "^[a-z]+$"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = RegexPatternCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_catches_invalid_regex(self, tmp_path: Path) -> None:
        """Invalid regex patterns are now rejected at schema validation time (TD-VR01).

        Previously, invalid regex passed schema validation and was caught by the
        V007 check at validation-check time. Now ValidationRule's model_validator
        catches invalid regex at construction, so JobConfig.from_yaml raises
        ValidationError before the V007 check even runs.
        """
        from pydantic import ValidationError

        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            validations:
              - type: content_regex
                path: /some/file
                pattern: "[invalid("
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)

        with pytest.raises(ValidationError, match="Invalid regex pattern"):
            JobConfig.from_yaml(config_path)


class TestValidationTypeCheck:
    """Tests for ValidationTypeCheck (V008)."""

    def test_complete_validation_passes(self, tmp_path: Path) -> None:
        """No error when validation has all required fields."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            validations:
              - type: file_exists
                path: /some/file
              - type: content_contains
                path: /some/file
                pattern: "required text"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = ValidationTypeCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_catches_missing_path(self, tmp_path: Path) -> None:
        """Error when file_exists validation missing path — caught by model validator."""
        from pydantic import ValidationError

        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            validations:
              - type: file_exists
                description: "Check something exists"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)

        with pytest.raises(ValidationError, match="requires 'path' field"):
            JobConfig.from_yaml(config_path)


class TestTimeoutRangeCheck:
    """Tests for TimeoutRangeCheck (V103/V104)."""

    def test_reasonable_timeout_passes(self, minimal_config: tuple[JobConfig, Path, str]) -> None:
        """No warning for reasonable timeout values."""
        config, config_path, raw_yaml = minimal_config
        check = TimeoutRangeCheck()

        issues = check.check(config, config_path, raw_yaml)

        # Default timeout (1800s) is reasonable
        assert not any(i.check_id in ("V103", "V104") for i in issues)

    def test_warns_short_timeout(self, tmp_path: Path) -> None:
        """Warning for very short timeout."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: claude_cli
              timeout_seconds: 30
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = TimeoutRangeCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V103"
        assert issues[0].severity == ValidationSeverity.WARNING

    def test_warns_very_long_timeout(self, tmp_path: Path) -> None:
        """Info severity for timeout exceeding 2 hours (V104)."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: claude_cli
              timeout_seconds: 10800
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = TimeoutRangeCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V104"
        assert issues[0].severity == ValidationSeverity.INFO
        assert "3.0h" in issues[0].message

    def test_boundary_timeout_at_min(self, tmp_path: Path) -> None:
        """Timeout exactly at MIN_REASONABLE_TIMEOUT (60s) passes."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: claude_cli
              timeout_seconds: 60
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = TimeoutRangeCheck()
        issues = check.check(config, config_path, yaml_content)

        assert not any(i.check_id == "V103" for i in issues)

    def test_boundary_timeout_at_max(self, tmp_path: Path) -> None:
        """Timeout exactly at MAX_REASONABLE_TIMEOUT (7200s) passes."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: claude_cli
              timeout_seconds: 7200
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = TimeoutRangeCheck()
        issues = check.check(config, config_path, yaml_content)

        assert not any(i.check_id == "V104" for i in issues)


# ============================================================================
# VersionReferenceCheck Tests (Q007 — GH#82)
# ============================================================================


class TestVersionReferenceCheck:
    """Tests for VersionReferenceCheck (V009)."""

    def test_non_versioned_file_skipped(self, tmp_path: Path) -> None:
        """Non-versioned file names produce no issues."""
        from marianne.validation.checks.config import VersionReferenceCheck

        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test-job.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = VersionReferenceCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_v1_file_skipped(self, tmp_path: Path) -> None:
        """v1 files have no previous version to check against."""
        from marianne.validation.checks.config import VersionReferenceCheck

        yaml_content = dedent("""
            name: evolution-v1
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "evolution-v1.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = VersionReferenceCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_detects_stale_name_reference(self, tmp_path: Path) -> None:
        """Detects when job name references previous version."""
        from marianne.validation.checks.config import VersionReferenceCheck

        yaml_content = dedent("""
            name: evolution-v2
            workspace: /tmp/workspace-v3
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "evolution-v3.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = VersionReferenceCheck()
        issues = check.check(config, config_path, yaml_content)

        name_issues = [i for i in issues if i.metadata and i.metadata.get("field") == "name"]
        assert len(name_issues) == 1
        assert "v2" in name_issues[0].message

    def test_detects_stale_workspace_reference(self, tmp_path: Path) -> None:
        """Detects when workspace path references previous version."""
        from marianne.validation.checks.config import VersionReferenceCheck

        yaml_content = dedent("""
            name: evolution-v3
            workspace: /tmp/workspace-v2
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "evolution-v3.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = VersionReferenceCheck()
        issues = check.check(config, config_path, yaml_content)

        ws_issues = [i for i in issues if i.metadata and i.metadata.get("field") == "workspace"]
        assert len(ws_issues) == 1
        assert "v2" in ws_issues[0].message

    def test_clean_versioned_file_passes(self, tmp_path: Path) -> None:
        """Properly versioned file produces no issues."""
        from marianne.validation.checks.config import VersionReferenceCheck

        yaml_content = dedent("""
            name: evolution-v3
            workspace: /tmp/workspace-v3
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "evolution-v3.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = VersionReferenceCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0


# ============================================================================
# EmptyPatternCheck Tests (Q007 — GH#82)
# ============================================================================


class TestEmptyPatternCheck:
    """Tests for EmptyPatternCheck (V106)."""

    def test_no_validations_passes(self, minimal_config: tuple[JobConfig, Path, str]) -> None:
        """Config with no validations produces no issues."""
        from marianne.validation.checks.config import EmptyPatternCheck

        config, config_path, raw_yaml = minimal_config
        check = EmptyPatternCheck()
        issues = check.check(config, config_path, raw_yaml)

        assert len(issues) == 0

    def test_non_empty_pattern_passes(self, tmp_path: Path) -> None:
        """Validation with non-empty pattern is fine."""
        from marianne.validation.checks.config import EmptyPatternCheck

        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            validations:
              - type: content_regex
                path: output.txt
                pattern: "SUCCESS"
                description: "Check for success"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = EmptyPatternCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_detects_empty_pattern(self, tmp_path: Path) -> None:
        """Flags validation rules with empty/whitespace-only patterns."""
        from marianne.validation.checks.config import EmptyPatternCheck

        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            validations:
              - type: content_regex
                path: output.txt
                pattern: "   "
                description: "Bad pattern"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = EmptyPatternCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V106"
        assert "empty" in issues[0].message.lower()

    def test_ignores_file_exists_type(self, tmp_path: Path) -> None:
        """file_exists validations don't have patterns, so no issue."""
        from marianne.validation.checks.config import EmptyPatternCheck

        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            validations:
              - type: file_exists
                path: output.txt
                description: "Check file"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = EmptyPatternCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0


# ============================================================================
# PreludeCadenzaFileCheck Tests (GH#53)
# ============================================================================


class TestPreludeCadenzaFileCheck:
    """Tests for PreludeCadenzaFileCheck (V108)."""

    def test_no_prelude_cadenzas_passes(
        self, minimal_config: tuple[JobConfig, Path, str],
    ) -> None:
        """Config without prelude/cadenzas produces no issues."""
        from marianne.validation.checks.paths import PreludeCadenzaFileCheck

        config, config_path, raw_yaml = minimal_config
        check = PreludeCadenzaFileCheck()
        issues = check.check(config, config_path, raw_yaml)

        assert len(issues) == 0

    def test_existing_prelude_file_passes(self, tmp_path: Path) -> None:
        """No warning when prelude file exists."""
        from marianne.validation.checks.paths import PreludeCadenzaFileCheck

        prelude_file = tmp_path / "context.md"
        prelude_file.write_text("Background context")

        yaml_content = dedent(f"""
            name: test-job
            sheet:
              size: 10
              total_items: 100
              prelude:
                - file: {prelude_file}
                  as: context
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = PreludeCadenzaFileCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_catches_missing_prelude_file(self, tmp_path: Path) -> None:
        """Warning when prelude file doesn't exist."""
        from marianne.validation.checks.paths import PreludeCadenzaFileCheck

        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
              prelude:
                - file: /nonexistent/context.md
                  as: context
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = PreludeCadenzaFileCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V108"
        assert issues[0].severity == ValidationSeverity.WARNING
        assert "prelude" in issues[0].message

    def test_catches_missing_cadenza_file(self, tmp_path: Path) -> None:
        """Warning when cadenza file doesn't exist."""
        from marianne.validation.checks.paths import PreludeCadenzaFileCheck

        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
              cadenzas:
                2:
                  - file: /nonexistent/sheet2-extra.md
                    as: skill
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = PreludeCadenzaFileCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V108"
        assert "cadenza" in issues[0].message
        assert "sheet 2" in issues[0].message

    def test_skips_jinja_templated_paths(self, tmp_path: Path) -> None:
        """Paths with Jinja templates ({{ }}) are skipped."""
        from marianne.validation.checks.paths import PreludeCadenzaFileCheck

        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
              prelude:
                - file: "{{ workspace }}/context.md"
                  as: context
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = PreludeCadenzaFileCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_multiple_missing_files_reported(self, tmp_path: Path) -> None:
        """Multiple missing files each produce a separate warning."""
        from marianne.validation.checks.paths import PreludeCadenzaFileCheck

        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
              prelude:
                - file: /nonexistent/a.md
                  as: context
                - file: /nonexistent/b.md
                  as: skill
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = PreludeCadenzaFileCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 2
        assert all(i.check_id == "V108" for i in issues)


# ============================================================================
# ValidationRunner Tests
# ============================================================================


class TestValidationRunner:
    """Tests for ValidationRunner orchestration."""

    def test_aggregates_all_checks(self, minimal_config: tuple[JobConfig, Path, str]) -> None:
        """Runner collects issues from all checks."""
        config, config_path, raw_yaml = minimal_config
        runner = ValidationRunner(create_default_checks())

        issues = runner.validate(config, config_path, raw_yaml)

        # Should run without error and return a list of ValidationIssue objects
        assert isinstance(issues, list)
        # All items should be ValidationIssue instances with required fields
        for issue in issues:
            assert hasattr(issue, "check_id")
            assert hasattr(issue, "severity")
            assert hasattr(issue, "message")

    def test_sorts_by_severity(self, tmp_path: Path) -> None:
        """Issues sorted by severity (errors first)."""
        # Create config that triggers both errors and warnings
        yaml_content = dedent("""
            name: test-job
            workspace: /nonexistent/workspace
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "{{ undefined_var }}"
            backend:
              type: claude_cli
              timeout_seconds: 30
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        runner = ValidationRunner(create_default_checks())
        issues = runner.validate(config, config_path, yaml_content)

        # Verify we got issues to test sorting with
        assert len(issues) > 0, "Expected validation issues for sorting test"

        # Errors should come before warnings
        seen_warning = False
        for issue in issues:
            if issue.severity == ValidationSeverity.WARNING:
                seen_warning = True
            if issue.severity == ValidationSeverity.ERROR and seen_warning:
                pytest.fail("Error found after warning - sorting incorrect")

    def test_exit_code_1_on_errors(self, tmp_path: Path) -> None:
        """Returns exit code 1 when errors present."""
        yaml_content = dedent("""
            name: test-job
            workspace: /nonexistent/workspace
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        runner = ValidationRunner(create_default_checks())
        issues = runner.validate(config, config_path, yaml_content)

        assert runner.get_exit_code(issues) == 1

    def test_exit_code_0_on_warnings_only(
        self, minimal_config: tuple[JobConfig, Path, str],
    ) -> None:
        """Returns exit code 0 with only warnings."""
        config, config_path, raw_yaml = minimal_config
        runner = ValidationRunner(create_default_checks())

        issues = runner.validate(config, config_path, raw_yaml)

        # Minimal config should be valid
        assert runner.get_exit_code(issues) == 0

    def test_handles_check_exceptions(self) -> None:
        """Runner handles exceptions in checks gracefully."""
        from typing import Any

        class BrokenCheck:
            @property
            def check_id(self) -> str:
                return "VBROKEN"

            @property
            def severity(self) -> ValidationSeverity:
                return ValidationSeverity.ERROR

            @property
            def description(self) -> str:
                return "Always raises"

            def check(self, config: Any, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
                raise RuntimeError("This check always fails")

        runner = ValidationRunner([BrokenCheck()])  # type: ignore[list-item]

        yaml_str = dedent("""
            name: test
            sheet:
              size: 1
              total_items: 1
            prompt:
              template: "test"
        """).strip()

        config = JobConfig.from_yaml_string(yaml_str)
        issues = runner.validate(config, Path("/tmp/test.yaml"), yaml_str)

        assert len(issues) == 1
        assert "failed to execute" in issues[0].message.lower()


# ============================================================================
# ValidationReporter Tests
# ============================================================================


class TestValidationReporter:
    """Tests for ValidationReporter output formatting."""

    def test_json_output_structure(self) -> None:
        """JSON output has expected structure."""
        import json

        reporter = ValidationReporter()
        issues = [
            ValidationIssue(
                check_id="V001",
                severity=ValidationSeverity.ERROR,
                message="Test error",
            ),
            ValidationIssue(
                check_id="V101",
                severity=ValidationSeverity.WARNING,
                message="Test warning",
                suggestion="Fix it",
            ),
        ]

        json_str = reporter.report_json(issues)
        result = json.loads(json_str)

        assert result["valid"] is False
        assert result["error_count"] == 1
        assert result["warning_count"] == 1
        assert len(result["issues"]) == 2

    def test_plain_text_output(self) -> None:
        """Plain text output is readable."""
        reporter = ValidationReporter()
        issues = [
            ValidationIssue(
                check_id="V001",
                severity=ValidationSeverity.ERROR,
                message="Test error",
                line=10,
            ),
        ]

        text = reporter.format_plain(issues)

        assert "ERROR" in text
        assert "V001" in text
        assert "line 10" in text
        assert "Test error" in text

    def test_empty_issues_passes(self) -> None:
        """No issues results in pass message."""
        reporter = ValidationReporter()
        issues: list[ValidationIssue] = []

        text = reporter.format_plain(issues)

        assert "passed" in text.lower() or "no issues" in text.lower()
