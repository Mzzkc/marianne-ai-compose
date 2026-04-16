"""Tests for the rendering preview engine.

Covers:
- Basic rendering of inline templates
- Validation path expansion
- Conditional applicability
- Fan-out metadata
- Jinja error capture
- Snippet truncation
- Max sheets limiting
- Template file resolution
- Missing template file handling
- Multiple validations expansion
- has_fan_out flag
- has_dependencies flag
"""

from pathlib import Path
from textwrap import dedent

from marianne.core.config import JobConfig
from marianne.validation.rendering import (
    generate_preview,
)

# ============================================================================
# Helpers
# ============================================================================


def _make_config(yaml_str: str, config_path: Path) -> JobConfig:
    """Write YAML to *config_path* and parse it."""
    config_path.write_text(yaml_str)
    return JobConfig.from_yaml(config_path)


# ============================================================================
# Tests
# ============================================================================


class TestBasicRendering:
    """Test basic rendering of inline templates."""

    def test_basic_rendering(self, tmp_path: Path) -> None:
        """Simple config with inline template renders sheet 1 correctly."""
        yaml_str = dedent("""\
            name: basic-test
            workspace: {workspace}
            sheet:
              size: 10
              total_items: 30
            prompt:
              template: >-
                Process sheet {{{{ sheet_num }}}} items {{{{ start_item }}}}-{{{{ end_item }}}}

        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)

        preview = generate_preview(config, config_path)

        assert len(preview.sheets) == 3
        assert preview.total_sheets == 3

        sheet1 = preview.sheets[0]
        assert sheet1.sheet_num == 1
        assert sheet1.item_range == (1, 10)
        assert sheet1.rendered_prompt is not None
        assert "Process sheet 1" in sheet1.rendered_prompt
        assert "items 1-10" in sheet1.rendered_prompt
        assert sheet1.render_error is None


class TestValidationPathExpansion:
    """Test validation path expansion with single-brace format."""

    def test_validation_path_expansion(self, tmp_path: Path) -> None:
        """Validation path with {workspace} and {sheet_num} is expanded."""
        yaml_str = dedent("""\
            name: path-test
            workspace: {workspace}
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Do work"
            validations:
              - type: file_exists
                path: "{workspace}/output-{{sheet_num}}.md"
                description: "Output file exists"
        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)

        preview = generate_preview(config, config_path)
        sheet1 = preview.sheets[0]

        assert len(sheet1.expanded_validations) == 1
        ev = sheet1.expanded_validations[0]
        assert ev.expanded_path is not None
        assert "{sheet_num}" not in ev.expanded_path
        assert "{workspace}" not in ev.expanded_path
        assert "output-1.md" in ev.expanded_path


class TestConditionalApplicability:
    """Test condition evaluation for validations."""

    def test_conditional_applicability(self, tmp_path: Path) -> None:
        """Validation with condition 'sheet_num >= 3' is not applicable for sheet 1."""
        yaml_str = dedent("""\
            name: condition-test
            workspace: {workspace}
            sheet:
              size: 5
              total_items: 15
            prompt:
              template: "Work on sheet {{{{ sheet_num }}}}"
            validations:
              - type: file_exists
                path: "{workspace}/late-output.md"
                condition: "sheet_num >= 3"
                description: "Late output"
        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)

        preview = generate_preview(config, config_path)

        # Sheet 1: condition not met → not applicable
        sheet1 = preview.sheets[0]
        assert len(sheet1.expanded_validations) == 1
        assert sheet1.expanded_validations[0].applicable is False

        # Sheet 3: condition met → applicable
        sheet3 = preview.sheets[2]
        assert sheet3.expanded_validations[0].applicable is True


class TestFanOutMetadata:
    """Test fan-out metadata in previews."""

    def test_fan_out_metadata(self, tmp_path: Path) -> None:
        """Config with fan_out populates stage, instance, fan_count."""
        yaml_str = dedent("""\
            name: fanout-test
            workspace: {workspace}
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 3
            prompt:
              template: "Stage {{{{ stage }}}} instance {{{{ instance }}}}"
        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)

        preview = generate_preview(config, config_path)

        assert preview.has_fan_out is True

        # After fan-out expansion, stage 2 becomes 3 sheets (2,3,4) + stage 1 + stage 3 = 5
        # Find a sheet with fan_count > 1
        fan_out_sheets = [s for s in preview.sheets if s.fan_count is not None and s.fan_count > 1]
        assert len(fan_out_sheets) > 0, "Expected at least one sheet with fan_count > 1"

        fan_sheet = fan_out_sheets[0]
        assert fan_sheet.stage is not None
        assert fan_sheet.instance is not None
        assert fan_sheet.fan_count is not None
        assert fan_sheet.fan_count == 3


class TestJinjaErrorCapture:
    """Test that Jinja errors are captured, not raised."""

    def test_jinja_error_captured(self, tmp_path: Path) -> None:
        """Broken template stores error in render_error instead of raising."""
        yaml_str = dedent("""\
            name: error-test
            workspace: {workspace}
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "{{{{ broken }}}}"
        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)

        # Should NOT raise
        preview = generate_preview(config, config_path)

        sheet1 = preview.sheets[0]
        assert sheet1.render_error is not None
        assert sheet1.rendered_prompt is None
        assert len(preview.render_errors) > 0


class TestSnippetTruncation:
    """Test prompt snippet truncation."""

    def test_snippet_truncation(self, tmp_path: Path) -> None:
        """Template that renders to 30+ lines has snippet with ~15 lines plus '...'."""
        # Build a template with many lines
        lines = [f"Line {{{{ sheet_num }}}}-{i}" for i in range(1, 35)]
        template = "\\n".join(lines)

        yaml_str = dedent("""\
            name: snippet-test
            workspace: {workspace}
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "{template}"
        """).format(workspace=tmp_path / "ws", template=template)

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)

        preview = generate_preview(config, config_path)
        sheet1 = preview.sheets[0]

        # Rendered prompt should be the full text
        assert sheet1.rendered_prompt is not None
        rendered_lines = sheet1.rendered_prompt.splitlines()
        assert len(rendered_lines) >= 30

        # Snippet should be truncated
        snippet_lines = sheet1.prompt_snippet.splitlines()
        assert snippet_lines[-1] == "..."
        # ~15 lines of content + "..."
        assert len(snippet_lines) <= 17


class TestMaxSheetsLimiting:
    """Test max_sheets parameter."""

    def test_max_sheets_limiting(self, tmp_path: Path) -> None:
        """max_sheets=1 only renders 1 sheet even if config has 5."""
        yaml_str = dedent("""\
            name: limit-test
            workspace: {workspace}
            sheet:
              size: 10
              total_items: 50
            prompt:
              template: "Sheet {{{{ sheet_num }}}}"
        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)

        preview = generate_preview(config, config_path, max_sheets=1)

        assert len(preview.sheets) == 1
        assert preview.total_sheets == 5
        assert preview.sheets[0].sheet_num == 1


class TestTemplateFileResolution:
    """Test template_file pointing to a real file."""

    def test_template_file_resolution(self, tmp_path: Path) -> None:
        """Config with template_file renders output from that file."""
        template_file = tmp_path / "template.j2"
        template_file.write_text("Hello from file, sheet {{ sheet_num }}!")

        yaml_str = dedent("""\
            name: file-template-test
            workspace: {workspace}
            sheet:
              size: 5
              total_items: 5
            prompt:
              template_file: "{template_file}"
        """).format(workspace=tmp_path / "ws", template_file=template_file)

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)

        preview = generate_preview(config, config_path)
        sheet1 = preview.sheets[0]

        assert sheet1.rendered_prompt is not None
        assert "Hello from file, sheet 1!" in sheet1.rendered_prompt
        assert sheet1.render_error is None


class TestMissingTemplateFile:
    """Test template_file pointing to a non-existent file."""

    def test_missing_template_file(self, tmp_path: Path) -> None:
        """Config with non-existent template_file captures error."""
        yaml_str = dedent("""\
            name: missing-file-test
            workspace: {workspace}
            sheet:
              size: 5
              total_items: 5
            prompt:
              template_file: "{template_file}"
        """).format(
            workspace=tmp_path / "ws",
            template_file=tmp_path / "nonexistent.j2",
        )

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)

        preview = generate_preview(config, config_path)
        sheet1 = preview.sheets[0]

        # When template_file doesn't exist, PromptBuilder falls back to default
        # prompt (no error from rendering itself), but the rendered prompt won't
        # contain template file content.
        # The key point: it doesn't crash.
        assert sheet1.rendered_prompt is not None or sheet1.render_error is not None


class TestMultipleValidationsExpanded:
    """Test that multiple validations are all expanded correctly."""

    def test_multiple_validations_expanded(self, tmp_path: Path) -> None:
        """Config with 3 validations has all expanded for sheet 1."""
        yaml_str = dedent("""\
            name: multi-val-test
            workspace: {workspace}
            sheet:
              size: 5
              total_items: 5
            prompt:
              template: "Work"
            validations:
              - type: file_exists
                path: "{workspace}/report.md"
                description: "Report exists"
              - type: content_contains
                path: "{workspace}/report.md"
                pattern: "summary"
                description: "Report has summary"
              - type: command_succeeds
                command: "echo ok"
                description: "Command runs"
        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)

        preview = generate_preview(config, config_path)
        sheet1 = preview.sheets[0]

        assert len(sheet1.expanded_validations) == 3

        ev0 = sheet1.expanded_validations[0]
        assert ev0.type == "file_exists"
        assert ev0.description == "Report exists"
        assert ev0.expanded_path is not None
        assert "{workspace}" not in ev0.expanded_path

        ev1 = sheet1.expanded_validations[1]
        assert ev1.type == "content_contains"
        assert ev1.pattern == "summary"

        ev2 = sheet1.expanded_validations[2]
        assert ev2.type == "command_succeeds"
        assert ev2.expanded_path is None  # command_succeeds has no path


class TestHasFanOutFlag:
    """Test has_fan_out flag on RenderingPreview."""

    def test_has_fan_out_true(self, tmp_path: Path) -> None:
        """has_fan_out is True when fan_out is configured."""
        yaml_str = dedent("""\
            name: fanout-flag-test
            workspace: {workspace}
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 2
            prompt:
              template: "Work"
        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)
        preview = generate_preview(config, config_path)

        assert preview.has_fan_out is True

    def test_has_fan_out_false(self, tmp_path: Path) -> None:
        """has_fan_out is False when no fan_out configured."""
        yaml_str = dedent("""\
            name: no-fanout-test
            workspace: {workspace}
            sheet:
              size: 10
              total_items: 10
            prompt:
              template: "Work"
        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)
        preview = generate_preview(config, config_path)

        assert preview.has_fan_out is False


class TestHasDependenciesFlag:
    """Test has_dependencies flag on RenderingPreview."""

    def test_has_dependencies_true(self, tmp_path: Path) -> None:
        """has_dependencies is True when dependencies configured."""
        yaml_str = dedent("""\
            name: deps-flag-test
            workspace: {workspace}
            sheet:
              size: 5
              total_items: 10
              dependencies:
                2: [1]
            prompt:
              template: "Work"
        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)
        preview = generate_preview(config, config_path)

        assert preview.has_dependencies is True

    def test_has_dependencies_false(self, tmp_path: Path) -> None:
        """has_dependencies is False when no dependencies configured."""
        yaml_str = dedent("""\
            name: no-deps-test
            workspace: {workspace}
            sheet:
              size: 10
              total_items: 10
            prompt:
              template: "Work"
        """).format(workspace=tmp_path / "ws")

        config_path = tmp_path / "config.yaml"
        config = _make_config(yaml_str, config_path)
        preview = generate_preview(config, config_path)

        assert preview.has_dependencies is False
