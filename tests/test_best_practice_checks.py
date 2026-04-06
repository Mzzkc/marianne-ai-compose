"""Tests for best-practice validation checks (V201–V209).

Each check has at least two tests:
1. A "passes" test — clean config produces no issues
2. A "triggers" test — config with the anti-pattern produces the expected issue
"""

from pathlib import Path
from textwrap import dedent

import pytest

from marianne.core.config import JobConfig
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks.best_practices import (
    FanOutWithoutDependenciesCheck,
    FanOutWithoutParallelCheck,
    FileExistsOnlyCheck,
    FormatSyntaxInTemplateCheck,
    JinjaInValidationPathCheck,
    MissingDisableMcpCheck,
    MissingSkipPermissionsCheck,
    NoValidationsCheck,
    VariableShadowingCheck,
)


# ============================================================================
# V201 — JinjaInValidationPathCheck
# ============================================================================


class TestJinjaInValidationPathCheck:
    """Tests for JinjaInValidationPathCheck (V201)."""

    def test_v201_clean_path_passes(self, tmp_path: Path) -> None:
        """Validation path using format syntax produces no V201 issues."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Process sheet {{ sheet_num }}"
            validations:
              - type: file_exists
                path: "{workspace}/foo.md"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = JinjaInValidationPathCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_v201_jinja_in_path_triggers(self, tmp_path: Path) -> None:
        """Validation path using Jinja syntax triggers V201."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Process sheet {{ sheet_num }}"
            validations:
              - type: file_exists
                path: "{{ workspace }}/foo.md"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = JinjaInValidationPathCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V201"
        assert issues[0].severity == ValidationSeverity.WARNING


# ============================================================================
# V202 — FormatSyntaxInTemplateCheck
# ============================================================================


class TestFormatSyntaxInTemplateCheck:
    """Tests for FormatSyntaxInTemplateCheck (V202)."""

    def test_v202_jinja_syntax_passes(self, tmp_path: Path) -> None:
        """Jinja syntax in template produces no V202 issues."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Process sheet {{ sheet_num }}"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = FormatSyntaxInTemplateCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_v202_format_syntax_triggers(self, tmp_path: Path) -> None:
        """Format-string syntax in template triggers V202."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Process sheet {sheet_num}"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = FormatSyntaxInTemplateCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V202"
        assert issues[0].severity == ValidationSeverity.WARNING


# ============================================================================
# V203 — NoValidationsCheck
# ============================================================================


class TestNoValidationsCheck:
    """Tests for NoValidationsCheck (V203)."""

    def test_v203_with_validations_passes(self, tmp_path: Path) -> None:
        """Config with validations produces no V203 issues."""
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
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = NoValidationsCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_v203_no_validations_triggers(self, tmp_path: Path) -> None:
        """Config without validations triggers V203."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = NoValidationsCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V203"
        assert issues[0].severity == ValidationSeverity.WARNING


# ============================================================================
# V204 — MissingSkipPermissionsCheck
# ============================================================================


class TestMissingSkipPermissionsCheck:
    """Tests for MissingSkipPermissionsCheck (V204)."""

    def test_v204_skip_permissions_true_passes(self, tmp_path: Path) -> None:
        """skip_permissions: true produces no V204 issues."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: claude_cli
              skip_permissions: true
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = MissingSkipPermissionsCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_v204_skip_permissions_false_triggers(self, tmp_path: Path) -> None:
        """skip_permissions: false triggers V204."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: claude_cli
              skip_permissions: false
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = MissingSkipPermissionsCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V204"
        assert issues[0].severity == ValidationSeverity.WARNING

    def test_v204_non_claude_backend_passes(self, tmp_path: Path) -> None:
        """Non-Claude backend produces no V204 issues."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: anthropic_api
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = MissingSkipPermissionsCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0


# ============================================================================
# V205 — FileExistsOnlyCheck
# ============================================================================


class TestFileExistsOnlyCheck:
    """Tests for FileExistsOnlyCheck (V205)."""

    def test_v205_mixed_validations_passes(self, tmp_path: Path) -> None:
        """Mixed validation types produce no V205 issues."""
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
                pattern: "expected"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = FileExistsOnlyCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_v205_all_file_exists_triggers(self, tmp_path: Path) -> None:
        """All file_exists validations triggers V205."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            validations:
              - type: file_exists
                path: /some/file1
              - type: file_exists
                path: /some/file2
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = FileExistsOnlyCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V205"
        assert issues[0].severity == ValidationSeverity.INFO

    def test_v205_no_validations_passes(self, tmp_path: Path) -> None:
        """Zero validations produces no V205 issues (V203 handles that)."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = FileExistsOnlyCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0


# ============================================================================
# V206 — FanOutWithoutDependenciesCheck
# ============================================================================


class TestFanOutWithoutDependenciesCheck:
    """Tests for FanOutWithoutDependenciesCheck (V206)."""

    def test_v206_fanout_with_deps_passes(self, tmp_path: Path) -> None:
        """Fan-out with dependencies produces no V206 issues."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 2
              dependencies:
                2: [1]
                3: [1]
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = FanOutWithoutDependenciesCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_v206_fanout_no_deps_triggers(self, tmp_path: Path) -> None:
        """Fan-out without dependencies triggers V206."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 2
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = FanOutWithoutDependenciesCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V206"
        assert issues[0].severity == ValidationSeverity.WARNING


# ============================================================================
# V207 — FanOutWithoutParallelCheck
# ============================================================================


class TestFanOutWithoutParallelCheck:
    """Tests for FanOutWithoutParallelCheck (V207)."""

    def test_v207_fanout_parallel_passes(self, tmp_path: Path) -> None:
        """Fan-out with parallel enabled produces no V207 issues."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 2
            parallel:
              enabled: true
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = FanOutWithoutParallelCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_v207_fanout_no_parallel_triggers(self, tmp_path: Path) -> None:
        """Fan-out without parallel triggers V207."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 2
            prompt:
              template: "Test"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = FanOutWithoutParallelCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V207"
        assert issues[0].severity == ValidationSeverity.INFO


# ============================================================================
# V208 — VariableShadowingCheck
# ============================================================================


class TestVariableShadowingCheck:
    """Tests for VariableShadowingCheck (V208)."""

    def test_v208_unique_variables_passes(self, tmp_path: Path) -> None:
        """Non-builtin variable names produce no V208 issues."""
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

        check = VariableShadowingCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_v208_shadowing_triggers(self, tmp_path: Path) -> None:
        """Variable shadowing built-in 'workspace' triggers V208."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Output to {{ workspace }}"
              variables:
                workspace: "/custom/path"
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = VariableShadowingCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V208"
        assert issues[0].severity == ValidationSeverity.WARNING


# ============================================================================
# V209 — MissingDisableMcpCheck
# ============================================================================


class TestMissingDisableMcpCheck:
    """Tests for MissingDisableMcpCheck (V209)."""

    def test_v209_disable_mcp_true_passes(self, tmp_path: Path) -> None:
        """disable_mcp: true produces no V209 issues."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: claude_cli
              disable_mcp: true
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = MissingDisableMcpCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 0

    def test_v209_disable_mcp_false_triggers(self, tmp_path: Path) -> None:
        """disable_mcp: false triggers V209."""
        yaml_content = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Test"
            backend:
              type: claude_cli
              disable_mcp: false
        """).strip()

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        check = MissingDisableMcpCheck()
        issues = check.check(config, config_path, yaml_content)

        assert len(issues) == 1
        assert issues[0].check_id == "V209"
        assert issues[0].severity == ValidationSeverity.INFO
