"""Targeted tests closing coverage gaps in validation and healing modules.

Covers:
- best_practices.py: all 9 check classes and their properties
- config.py: regex suggestion helpers, rate limit patterns, version scanning
- rendering.py: condition evaluation edge cases, injection preview, snippet
- reporter.py: report_rendering_terminal, report_rendering_json, format_plain
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from textwrap import dedent

from rich.console import Console

from marianne.core.config import JobConfig
from marianne.core.config.job import InjectionItem
from marianne.prompts.templating import SheetContext
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
from marianne.validation.checks.config import (
    EmptyPatternCheck,
    RegexPatternCheck,
    TimeoutRangeCheck,
    ValidationTypeCheck,
    VersionReferenceCheck,
)
from marianne.validation.rendering import (
    ExpandedValidation,
    RenderingPreview,
    SheetPreview,
    _build_snippet,
    _check_condition,
    _check_single,
    _expand_path,
    generate_preview,
)
from marianne.validation.reporter import ValidationReporter

# ============================================================================
# Helpers
# ============================================================================


def _make_config(yaml_str: str, config_path: Path) -> JobConfig:
    """Write YAML to config_path and parse it."""
    config_path.write_text(yaml_str)
    return JobConfig.from_yaml(config_path)


def _make_issue(
    check_id: str = "V001",
    severity: ValidationSeverity = ValidationSeverity.ERROR,
    message: str = "Test issue",
    **kwargs: object,
) -> ValidationIssue:
    return ValidationIssue(
        check_id=check_id,
        severity=severity,
        message=message,
        metadata=kwargs.pop("metadata", {}) or {},  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


# ============================================================================
# best_practices.py — property coverage & check() behavior
# ============================================================================


class TestJinjaInValidationPathCheck:
    """V201: Jinja syntax in validation paths."""

    def test_properties(self) -> None:
        check = JinjaInValidationPathCheck()
        assert check.check_id == "V201"
        assert check.severity == ValidationSeverity.WARNING
        assert "Jinja" in check.description

    def test_detects_jinja_in_path(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            validations:
              - type: file_exists
                path: "{{ workspace }}/output.txt"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = JinjaInValidationPathCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 1
        assert issues[0].check_id == "V201"
        assert issues[0].suggestion is not None

    def test_clean_paths_pass(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            validations:
              - type: file_exists
                path: "{workspace}/output.txt"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = JinjaInValidationPathCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0


class TestFormatSyntaxInTemplateCheck:
    """V202: format-string syntax in Jinja templates."""

    def test_properties(self) -> None:
        check = FormatSyntaxInTemplateCheck()
        assert check.check_id == "V202"
        assert check.severity == ValidationSeverity.WARNING
        assert "format" in check.description.lower()

    def test_detects_format_syntax(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Process {workspace} items {sheet_num}"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = FormatSyntaxInTemplateCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 2  # workspace + sheet_num
        assert all(i.check_id == "V202" for i in issues)

    def test_jinja_syntax_passes(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Process {{ workspace }} items {{ sheet_num }}"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = FormatSyntaxInTemplateCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0

    def test_empty_template_passes(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: ""
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = FormatSyntaxInTemplateCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0


class TestNoValidationsCheck:
    """V203: no validation rules."""

    def test_properties(self) -> None:
        check = NoValidationsCheck()
        assert check.check_id == "V203"
        assert check.severity == ValidationSeverity.WARNING
        assert "validation" in check.description.lower()

    def test_fires_when_empty(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = NoValidationsCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 1
        assert issues[0].check_id == "V203"

    def test_passes_with_validations(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            validations:
              - type: file_exists
                path: output.txt
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = NoValidationsCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0


class TestMissingSkipPermissionsCheck:
    """V204: missing skip_permissions for Claude CLI."""

    def test_properties(self) -> None:
        check = MissingSkipPermissionsCheck()
        assert check.check_id == "V204"
        assert check.severity == ValidationSeverity.WARNING
        assert "skip_permissions" in check.description

    def test_fires_without_skip_permissions(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            backend:
              type: claude_cli
              skip_permissions: false
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = MissingSkipPermissionsCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 1
        assert issues[0].check_id == "V204"

    def test_passes_with_skip_permissions(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            backend:
              type: claude_cli
              skip_permissions: true
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = MissingSkipPermissionsCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0


class TestFileExistsOnlyCheck:
    """V205: all validations are file_exists only."""

    def test_properties(self) -> None:
        check = FileExistsOnlyCheck()
        assert check.check_id == "V205"
        assert check.severity == ValidationSeverity.INFO
        assert "file_exists" in check.description

    def test_fires_when_all_file_exists(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            validations:
              - type: file_exists
                path: out1.txt
              - type: file_exists
                path: out2.txt
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = FileExistsOnlyCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 1
        assert issues[0].check_id == "V205"

    def test_passes_with_mixed_types(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            validations:
              - type: file_exists
                path: out.txt
              - type: content_contains
                path: out.txt
                pattern: "DONE"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = FileExistsOnlyCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0

    def test_passes_with_no_validations(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = FileExistsOnlyCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0


class TestFanOutWithoutDependenciesCheck:
    """V206: fan-out without dependencies."""

    def test_properties(self) -> None:
        check = FanOutWithoutDependenciesCheck()
        assert check.check_id == "V206"
        assert check.severity == ValidationSeverity.WARNING
        assert "dependencies" in check.description

    def test_fires_without_dependencies(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 2
            prompt:
              template: "Work"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = FanOutWithoutDependenciesCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 1
        assert issues[0].check_id == "V206"

    def test_passes_with_dependencies(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 2
              dependencies:
                3: [1, 2]
            prompt:
              template: "Work"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = FanOutWithoutDependenciesCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0


class TestFanOutWithoutParallelCheck:
    """V207: fan-out without parallel execution."""

    def test_properties(self) -> None:
        check = FanOutWithoutParallelCheck()
        assert check.check_id == "V207"
        assert check.severity == ValidationSeverity.INFO
        assert "parallel" in check.description

    def test_fires_without_parallel(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 2
            parallel:
              enabled: false
            prompt:
              template: "Work"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = FanOutWithoutParallelCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 1
        assert issues[0].check_id == "V207"

    def test_passes_with_parallel(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 2
            parallel:
              enabled: true
            prompt:
              template: "Work"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = FanOutWithoutParallelCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0


class TestVariableShadowingCheck:
    """V208: user variables shadow built-ins."""

    def test_properties(self) -> None:
        check = VariableShadowingCheck()
        assert check.check_id == "V208"
        assert check.severity == ValidationSeverity.WARNING
        assert "shadow" in check.description

    def test_detects_shadowing(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "{{ workspace }}"
              variables:
                workspace: "/custom/path"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = VariableShadowingCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 1
        assert issues[0].check_id == "V208"
        assert "workspace" in issues[0].message

    def test_passes_with_custom_names(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "{{ my_var }}"
              variables:
                my_var: "hello"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = VariableShadowingCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0


class TestMissingDisableMcpCheck:
    """V209: missing disable_mcp for Claude CLI."""

    def test_properties(self) -> None:
        check = MissingDisableMcpCheck()
        assert check.check_id == "V209"
        assert check.severity == ValidationSeverity.INFO
        assert "disable_mcp" in check.description

    def test_fires_without_disable_mcp(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            backend:
              type: claude_cli
              disable_mcp: false
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = MissingDisableMcpCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 1
        assert issues[0].check_id == "V209"

    def test_passes_with_disable_mcp(self, tmp_path: Path) -> None:
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            backend:
              type: claude_cli
              disable_mcp: true
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = MissingDisableMcpCheck()
        issues = check.check(config, config_path, yaml_str)
        assert len(issues) == 0


# ============================================================================
# config.py — regex suggestion helpers, rate limit patterns
# ============================================================================


class TestRegexPatternCheckSuggestions:
    """Test _suggest_regex_fix branches for common regex errors."""

    def test_nothing_to_repeat_suggestion(self) -> None:
        check = RegexPatternCheck()
        suggestion = check._suggest_regex_fix("nothing to repeat at position 0", "*bad")
        assert "\\*" in suggestion

    def test_unbalanced_parenthesis_suggestion(self) -> None:
        check = RegexPatternCheck()
        suggestion = check._suggest_regex_fix("unbalanced parenthesis at position 3", "(foo")
        assert "\\(" in suggestion or "parenthes" in suggestion.lower()

    def test_missing_paren_suggestion(self) -> None:
        check = RegexPatternCheck()
        suggestion = check._suggest_regex_fix("missing ) at position 5", "(abc")
        assert "parenthes" in suggestion.lower()

    def test_unterminated_character_class_suggestion(self) -> None:
        check = RegexPatternCheck()
        suggestion = check._suggest_regex_fix("unterminated character class at position 0", "[abc")
        assert "\\[" in suggestion or "]" in suggestion

    def test_bad_escape_suggestion(self) -> None:
        check = RegexPatternCheck()
        suggestion = check._suggest_regex_fix("bad escape \\z at position 0", "\\z")
        assert "raw string" in suggestion.lower() or "backslash" in suggestion.lower()

    def test_special_char_suggestion(self) -> None:
        check = RegexPatternCheck()
        suggestion = check._suggest_regex_fix("some other error", "foo.bar")
        assert "escaping" in suggestion.lower() or "backslash" in suggestion.lower()

    def test_generic_fallback_suggestion(self) -> None:
        check = RegexPatternCheck()
        suggestion = check._suggest_regex_fix("completely unknown error", "abc")
        assert "regex syntax" in suggestion.lower() or "re module" in suggestion.lower()

    def test_properties(self) -> None:
        check = RegexPatternCheck()
        assert check.check_id == "V007"
        assert check.severity == ValidationSeverity.ERROR
        assert check.description

    def test_rate_limit_pattern_checked(self, tmp_path: Path) -> None:
        """Rate limit detection_patterns are validated by V007.

        Since Pydantic also validates regex at schema level, we test with
        valid patterns to cover the code path that iterates over them.
        """
        yaml_str = dedent("""\
            name: test
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            rate_limit:
              detection_patterns:
                - "rate.?limit"
                - "429"
        """)
        config_path = tmp_path / "test.yaml"
        config = _make_config(yaml_str, config_path)
        check = RegexPatternCheck()
        issues = check.check(config, config_path, yaml_str)
        # Valid patterns produce no issues
        assert len(issues) == 0

    def test_find_pattern_line(self) -> None:
        """Test _find_pattern_line finds pattern in YAML text."""
        check = RegexPatternCheck()
        # _find_pattern_line does re.escape on pattern, so use a literal string
        yaml_text = "line1: foo\npattern: RESULT_OK_DONE\nline3: bar"
        line = check._find_pattern_line(yaml_text, "RESULT_OK_DONE")
        assert line == 2

    def test_find_pattern_line_not_found(self) -> None:
        """Test _find_pattern_line returns None when not found."""
        check = RegexPatternCheck()
        yaml_text = "line1: foo\nline2: bar"
        line = check._find_pattern_line(yaml_text, "nonexistent_pattern_xyz")
        assert line is None


class TestValidationTypeCheckProperties:
    """V008 property accessors."""

    def test_properties(self) -> None:
        check = ValidationTypeCheck()
        assert check.check_id == "V008"
        assert check.severity == ValidationSeverity.ERROR
        assert check.description


class TestTimeoutRangeCheckProperties:
    """V103/V104 property accessors."""

    def test_properties(self) -> None:
        check = TimeoutRangeCheck()
        assert check.check_id == "V103"
        assert check.severity == ValidationSeverity.WARNING
        assert check.description


class TestVersionReferenceCheckScan:
    """V009 raw YAML scanning for stale version references."""

    def test_detects_stale_reference_in_raw_yaml(self, tmp_path: Path) -> None:
        """Detects references to previous version in YAML body."""
        yaml_str = dedent("""\
            name: evolution-v3
            workspace: /tmp/workspace-v3
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Run evolution-v2 task"
        """)
        config_path = tmp_path / "evolution-v3.yaml"
        config = _make_config(yaml_str, config_path)
        check = VersionReferenceCheck()
        issues = check.check(config, config_path, yaml_str)
        # Should detect "evolution-v2" in the template line
        stale_issues = [i for i in issues if i.severity == ValidationSeverity.WARNING]
        assert len(stale_issues) >= 1

    def test_skips_evolution_from_markers(self, tmp_path: Path) -> None:
        """Lines with 'EVOLUTION FROM' are skipped (historical docs)."""
        yaml_str = dedent("""\
            name: evolution-v3
            workspace: /tmp/workspace-v3
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "# EVOLUTION FROM evolution-v2 to v3"
        """)
        config_path = tmp_path / "evolution-v3.yaml"
        config = _make_config(yaml_str, config_path)
        check = VersionReferenceCheck()
        issues = check.check(config, config_path, yaml_str)
        # The EVOLUTION FROM marker should suppress the warning
        stale_warnings = [
            i
            for i in issues
            if i.severity == ValidationSeverity.WARNING and i.metadata.get("pattern")
        ]
        assert len(stale_warnings) == 0

    def test_skips_version_progression_arrows(self, tmp_path: Path) -> None:
        """Lines with → or -> version transitions are skipped."""
        yaml_str = dedent("""\
            name: evolution-v3
            workspace: /tmp/workspace-v3
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "# VERSION PROGRESSION: v2→v3"
        """)
        config_path = tmp_path / "evolution-v3.yaml"
        config = _make_config(yaml_str, config_path)
        check = VersionReferenceCheck()
        issues = check.check(config, config_path, yaml_str)
        stale_warnings = [
            i
            for i in issues
            if i.severity == ValidationSeverity.WARNING and i.metadata.get("pattern")
        ]
        assert len(stale_warnings) == 0

    def test_properties(self) -> None:
        check = VersionReferenceCheck()
        assert check.check_id == "V009"
        assert check.severity == ValidationSeverity.ERROR
        assert check.description


class TestEmptyPatternCheckProperties:
    """V106 property accessors."""

    def test_properties(self) -> None:
        check = EmptyPatternCheck()
        assert check.check_id == "V106"
        assert check.severity == ValidationSeverity.WARNING
        assert check.description


# ============================================================================
# rendering.py — condition evaluation, injection preview, snippet, expand_path
# ============================================================================


class TestCheckCondition:
    """Test _check_condition and _check_single edge cases."""

    def test_none_condition_returns_true(self) -> None:
        assert _check_condition(None, {"sheet_num": 1}) is True

    def test_compound_condition(self) -> None:
        ctx = {"sheet_num": 5, "stage": 2}
        assert _check_condition("sheet_num >= 3 and stage == 2", ctx) is True
        assert _check_condition("sheet_num >= 3 and stage == 3", ctx) is False

    def test_unrecognised_condition_returns_true(self) -> None:
        """Unrecognised condition format is treated as unconditional."""
        assert _check_single("this is not a valid condition", {"x": 1}) is True

    def test_unknown_variable_returns_true(self) -> None:
        """Variable not in context returns True (unconditional)."""
        assert _check_single("unknown_var >= 5", {"sheet_num": 3}) is True

    def test_all_operators(self) -> None:
        ctx = {"x": 5}
        assert _check_single("x >= 5", ctx) is True
        assert _check_single("x <= 5", ctx) is True
        assert _check_single("x == 5", ctx) is True
        assert _check_single("x != 5", ctx) is False
        assert _check_single("x > 4", ctx) is True
        assert _check_single("x < 6", ctx) is True

    def test_whitespace_in_condition(self) -> None:
        """Leading/trailing whitespace is stripped."""
        assert _check_condition("  sheet_num >= 1  ", {"sheet_num": 1}) is True


class TestExpandPath:
    """Test _expand_path helper."""

    def test_basic_expansion(self) -> None:
        result = _expand_path("{workspace}/file.txt", {"workspace": "/tmp/ws"})
        assert result == "/tmp/ws/file.txt"

    def test_multiple_placeholders(self) -> None:
        result = _expand_path(
            "{workspace}/sheet-{sheet_num}.txt",
            {"workspace": "/ws", "sheet_num": "3"},
        )
        assert result == "/ws/sheet-3.txt"

    def test_unknown_placeholder_left_intact(self) -> None:
        result = _expand_path("{workspace}/{unknown}", {"workspace": "/ws"})
        assert result == "/ws/{unknown}"


class TestBuildSnippet:
    """Test _build_snippet helper."""

    def test_short_text_unchanged(self) -> None:
        text = "line1\nline2\nline3"
        assert _build_snippet(text) == text

    def test_long_text_truncated(self) -> None:
        lines = [f"line {i}" for i in range(30)]
        text = "\n".join(lines)
        snippet = _build_snippet(text, max_lines=5)
        assert snippet.endswith("\n...")
        assert snippet.count("\n") == 5  # 5 lines + "..."


class TestResolveInjectionsPreview:
    """Test _resolve_injections_preview with real files."""

    def _make_ctx(self, tmp_path: Path) -> SheetContext:
        from marianne.prompts.templating import SheetContext

        return SheetContext(
            sheet_num=1,
            total_sheets=1,
            start_item=1,
            end_item=10,
            workspace=tmp_path,
        )

    def _make_item(
        self,
        file: str,
        category: str = "context",
    ) -> InjectionItem:
        from marianne.core.config.job import InjectionItem

        return InjectionItem(**{"file": file, "as": category})

    def test_resolves_existing_file(self, tmp_path: Path) -> None:
        """Injection from an existing file is loaded into context."""
        from marianne.validation.rendering import _resolve_injections_preview

        prelude_file = tmp_path / "context.md"
        prelude_file.write_text("Background info")

        ctx = self._make_ctx(tmp_path)
        items = [self._make_item(str(prelude_file), "context")]
        template_vars: dict[str, object] = {"workspace": str(tmp_path)}
        warnings = _resolve_injections_preview(ctx, items, template_vars)
        assert len(warnings) == 0
        assert "Background info" in ctx.injected_context

    def test_missing_file_silently_skipped(self, tmp_path: Path) -> None:
        """Missing files are skipped without warnings."""
        from marianne.validation.rendering import _resolve_injections_preview

        ctx = self._make_ctx(tmp_path)
        items = [self._make_item(str(tmp_path / "nonexistent.md"), "context")]
        template_vars: dict[str, object] = {"workspace": str(tmp_path)}
        warnings = _resolve_injections_preview(ctx, items, template_vars)
        assert len(warnings) == 0
        assert len(ctx.injected_context) == 0

    def test_jinja_error_in_file_path(self, tmp_path: Path) -> None:
        """Jinja error in injection file path produces a warning."""
        from marianne.validation.rendering import _resolve_injections_preview

        ctx = self._make_ctx(tmp_path)
        items = [self._make_item("{{ broken", "context")]
        template_vars: dict[str, object] = {"workspace": str(tmp_path)}
        warnings = _resolve_injections_preview(ctx, items, template_vars)
        assert len(warnings) == 1
        assert "Jinja error" in warnings[0]

    def test_resolves_skill_injection(self, tmp_path: Path) -> None:
        """Skill injection category populates injected_skills."""
        from marianne.validation.rendering import _resolve_injections_preview

        skill_file = tmp_path / "skill.md"
        skill_file.write_text("Skill content")

        ctx = self._make_ctx(tmp_path)
        items = [self._make_item(str(skill_file), "skill")]
        template_vars: dict[str, object] = {"workspace": str(tmp_path)}
        _resolve_injections_preview(ctx, items, template_vars)
        assert "Skill content" in ctx.injected_skills

    def test_resolves_tool_injection(self, tmp_path: Path) -> None:
        """Tool injection category populates injected_tools."""
        from marianne.validation.rendering import _resolve_injections_preview

        tool_file = tmp_path / "tool.md"
        tool_file.write_text("Tool content")

        ctx = self._make_ctx(tmp_path)
        items = [self._make_item(str(tool_file), "tool")]
        template_vars: dict[str, object] = {"workspace": str(tmp_path)}
        _resolve_injections_preview(ctx, items, template_vars)
        assert "Tool content" in ctx.injected_tools

    def test_relative_path_resolution(self, tmp_path: Path) -> None:
        """Relative paths are resolved against workspace."""
        from marianne.validation.rendering import _resolve_injections_preview

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "context.md").write_text("Resolved content")

        ctx = self._make_ctx(tmp_path)
        items = [self._make_item("context.md", "context")]
        template_vars: dict[str, object] = {"workspace": str(ws)}
        _resolve_injections_preview(ctx, items, template_vars)
        assert "Resolved content" in ctx.injected_context


class TestGeneratePreviewWithInjections:
    """Test generate_preview with prelude/cadenza injections."""

    def test_preview_with_prelude(self, tmp_path: Path) -> None:
        prelude_file = tmp_path / "context.md"
        prelude_file.write_text("Background context for all sheets")

        yaml_str = dedent("""\
            name: prelude-test
            workspace: {workspace}
            sheet:
              size: 5
              total_items: 5
              prelude:
                - file: "{prelude}"
                  as: context
            prompt:
              template: "Work on sheet {{{{ sheet_num }}}}"
        """).format(workspace=tmp_path / "ws", prelude=prelude_file)

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)
        preview = generate_preview(config, config_path)

        assert len(preview.sheets) == 1
        assert preview.sheets[0].rendered_prompt is not None


# ============================================================================
# reporter.py — rendering terminal and JSON output
# ============================================================================


def _make_rendering_preview(
    num_sheets: int = 2,
    has_fan_out: bool = False,
    has_dependencies: bool = False,
    render_errors: list[str] | None = None,
) -> RenderingPreview:
    """Build a RenderingPreview for testing."""
    sheets: list[SheetPreview] = []
    for i in range(1, num_sheets + 1):
        sheets.append(
            SheetPreview(
                sheet_num=i,
                item_range=(1, 10),
                rendered_prompt=f"Prompt for sheet {i}",
                prompt_snippet=f"Prompt for sheet {i}",
                expanded_validations=[
                    ExpandedValidation(
                        index=0,
                        type="file_exists",
                        description="Output file",
                        raw_path="/ws/output-{sheet_num}.txt",
                        expanded_path=f"/ws/output-{i}.txt",
                        pattern=None,
                        condition=None,
                        applicable=True,
                    ),
                    ExpandedValidation(
                        index=1,
                        type="content_contains",
                        description="Has content",
                        raw_path="/ws/output.txt",
                        expanded_path="/ws/output.txt",
                        pattern="SUCCESS",
                        condition="sheet_num >= 2",
                        applicable=i >= 2,
                    ),
                ],
                stage=1 if has_fan_out else None,
                instance=i if has_fan_out else None,
                fan_count=3 if has_fan_out else None,
                render_error=None,
            )
        )
    return RenderingPreview(
        sheets=sheets,
        total_sheets=num_sheets,
        has_fan_out=has_fan_out,
        has_dependencies=has_dependencies,
        render_errors=render_errors or [],
    )


class TestReportRenderingTerminal:
    """Tests for report_rendering_terminal."""

    def _capture(
        self,
        preview: RenderingPreview,
        verbose: bool = False,
        width: int = 120,
    ) -> str:
        buf = StringIO()
        console = Console(file=buf, color_system=None, width=width)
        reporter = ValidationReporter(console=console)
        reporter.report_rendering_terminal(preview, verbose=verbose)
        return buf.getvalue()

    def test_non_verbose_shows_first_sheet_only(self) -> None:
        preview = _make_rendering_preview(num_sheets=3)
        output = self._capture(preview, verbose=False)
        assert "Sheet 1" in output
        assert "Sheet 2" not in output

    def test_verbose_shows_all_sheets(self) -> None:
        preview = _make_rendering_preview(num_sheets=3)
        output = self._capture(preview, verbose=True)
        assert "Sheet 1" in output
        assert "Sheet 2" in output
        assert "Sheet 3" in output

    def test_fan_out_metadata_in_header(self) -> None:
        preview = _make_rendering_preview(num_sheets=2, has_fan_out=True)
        output = self._capture(preview, verbose=True)
        assert "stage=" in output
        assert "instance=" in output

    def test_render_error_shown(self) -> None:
        sheets = [
            SheetPreview(
                sheet_num=1,
                item_range=(1, 5),
                rendered_prompt=None,
                prompt_snippet="[render error: broken template]",
                expanded_validations=[],
                stage=None,
                instance=None,
                fan_count=None,
                render_error="broken template",
            )
        ]
        preview = RenderingPreview(
            sheets=sheets,
            total_sheets=1,
            has_fan_out=False,
            has_dependencies=False,
        )
        output = self._capture(preview)
        assert "broken template" in output

    def test_non_applicable_validation_shown_dim(self) -> None:
        preview = _make_rendering_preview(num_sheets=1)
        # Sheet 1 has a validation with condition "sheet_num >= 2" that's not applicable
        output = self._capture(preview)
        assert "not applicable" in output

    def test_render_errors_summary(self) -> None:
        preview = _make_rendering_preview(num_sheets=1, render_errors=["Error in sheet 1"])
        output = self._capture(preview)
        assert "Render Errors" in output
        assert "Error in sheet 1" in output

    def test_validation_numbers_shown(self) -> None:
        preview = _make_rendering_preview(num_sheets=1)
        output = self._capture(preview)
        assert "1." in output  # Validation 1
        assert "2." in output  # Validation 2


class TestReportRenderingJson:
    """Tests for report_rendering_json."""

    def test_basic_structure(self) -> None:
        preview = _make_rendering_preview(num_sheets=2)
        reporter = ValidationReporter()
        result = reporter.report_rendering_json(preview)
        assert result["total_sheets"] == 2
        assert result["has_fan_out"] is False
        assert result["has_dependencies"] is False
        assert len(result["sheets"]) == 2
        assert result["render_errors"] == []

    def test_sheet_fields(self) -> None:
        preview = _make_rendering_preview(num_sheets=1)
        reporter = ValidationReporter()
        result = reporter.report_rendering_json(preview)
        sheet = result["sheets"][0]
        assert sheet["sheet_num"] == 1
        assert sheet["item_range"] == [1, 10]
        assert sheet["prompt_snippet"] == "Prompt for sheet 1"
        assert sheet["render_error"] is None

    def test_validation_fields(self) -> None:
        preview = _make_rendering_preview(num_sheets=1)
        reporter = ValidationReporter()
        result = reporter.report_rendering_json(preview)
        validations = result["sheets"][0]["validations"]
        assert len(validations) == 2

        v0 = validations[0]
        assert v0["index"] == 0
        assert v0["type"] == "file_exists"
        assert v0["applicable"] is True
        assert v0["description"] == "Output file"
        assert v0["expanded_path"] == "/ws/output-1.txt"

        v1 = validations[1]
        assert v1["condition"] == "sheet_num >= 2"
        assert v1["applicable"] is False
        assert v1["pattern"] == "SUCCESS"

    def test_fan_out_fields(self) -> None:
        preview = _make_rendering_preview(num_sheets=1, has_fan_out=True)
        reporter = ValidationReporter()
        result = reporter.report_rendering_json(preview)
        sheet = result["sheets"][0]
        assert sheet["stage"] == 1
        assert sheet["instance"] == 1
        assert sheet["fan_count"] == 3

    def test_render_errors_included(self) -> None:
        preview = _make_rendering_preview(num_sheets=1, render_errors=["err1", "err2"])
        reporter = ValidationReporter()
        result = reporter.report_rendering_json(preview)
        assert result["render_errors"] == ["err1", "err2"]

    def test_optional_fields_omitted(self) -> None:
        """Validation fields with None values are omitted."""
        sheets = [
            SheetPreview(
                sheet_num=1,
                item_range=(1, 5),
                rendered_prompt="text",
                prompt_snippet="text",
                expanded_validations=[
                    ExpandedValidation(
                        index=0,
                        type="command_succeeds",
                        description=None,
                        raw_path=None,
                        expanded_path=None,
                        pattern=None,
                        condition=None,
                        applicable=True,
                    )
                ],
                stage=None,
                instance=None,
                fan_count=None,
                render_error=None,
            )
        ]
        preview = RenderingPreview(
            sheets=sheets,
            total_sheets=1,
            has_fan_out=False,
            has_dependencies=False,
        )
        reporter = ValidationReporter()
        result = reporter.report_rendering_json(preview)
        v = result["sheets"][0]["validations"][0]
        assert "description" not in v
        assert "expanded_path" not in v
        assert "pattern" not in v
        assert "condition" not in v


class TestFormatPlainEdgeCases:
    """Additional tests for format_plain edge cases."""

    def test_all_info_passed(self) -> None:
        """Only info issues -> PASSED."""
        reporter = ValidationReporter()
        issues = [_make_issue(severity=ValidationSeverity.INFO, message="note")]
        result = reporter.format_plain(issues)
        assert "PASSED" in result
        assert "FAILED" not in result

    def test_multiple_messages_in_output(self) -> None:
        """Multiple issues all appear in output."""
        reporter = ValidationReporter()
        issues = [
            _make_issue(check_id="V001", message="first"),
            _make_issue(check_id="V002", message="second"),
        ]
        result = reporter.format_plain(issues)
        assert "first" in result
        assert "second" in result
        assert "V001" in result
        assert "V002" in result


# ============================================================================
# _helpers.py — resolve_path
# ============================================================================


class TestHelpers:
    """Test _helpers.py functions."""

    def test_find_line_in_yaml_not_found(self) -> None:
        from marianne.validation.checks._helpers import find_line_in_yaml

        assert find_line_in_yaml("foo: bar\nbaz: qux", "nonexistent") is None

    def test_resolve_path_absolute(self, tmp_path: Path) -> None:
        from marianne.validation.checks._helpers import resolve_path

        abs_path = Path("/absolute/path")
        result = resolve_path(abs_path, tmp_path / "config.yaml")
        assert result == abs_path

    def test_resolve_path_relative(self, tmp_path: Path) -> None:
        from marianne.validation.checks._helpers import resolve_path

        rel_path = Path("relative/file.txt")
        config_path = tmp_path / "configs" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        result = resolve_path(rel_path, config_path)
        assert result == tmp_path / "configs" / "relative" / "file.txt"
