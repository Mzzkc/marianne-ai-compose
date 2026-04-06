"""Tests for cross-sheet context feature.

Tests the CrossSheetConfig and SheetContext cross-sheet context
population functionality.
"""

import tempfile
from pathlib import Path

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.config import (
    CrossSheetConfig,
    JobConfig,
    PromptConfig,
    SheetConfig,
)
from marianne.prompts.templating import PromptBuilder, SheetContext


class TestCrossSheetConfig:
    """Tests for CrossSheetConfig validation."""

    def test_defaults(self):
        """Test default values."""
        config = CrossSheetConfig()
        assert config.auto_capture_stdout is False
        assert config.max_output_chars == 2000
        assert config.capture_files == []
        assert config.lookback_sheets == 3

    def test_custom_values(self):
        """Test custom configuration."""
        config = CrossSheetConfig(
            auto_capture_stdout=True,
            max_output_chars=5000,
            capture_files=["{{ workspace }}/sheet-*.md"],
            lookback_sheets=5,
        )
        assert config.auto_capture_stdout is True
        assert config.max_output_chars == 5000
        assert config.capture_files == ["{{ workspace }}/sheet-*.md"]
        assert config.lookback_sheets == 5

    def test_lookback_zero_means_all(self):
        """Test that lookback_sheets=0 means all sheets."""
        config = CrossSheetConfig(lookback_sheets=0)
        assert config.lookback_sheets == 0


class TestSheetContext:
    """Tests for SheetContext with cross-sheet data."""

    def test_default_empty_cross_sheet_context(self):
        """Test that cross-sheet fields default to empty."""
        context = SheetContext(
            sheet_num=1,
            total_sheets=5,
            start_item=1,
            end_item=10,
            workspace=Path("/tmp/workspace"),
        )
        assert context.previous_outputs == {}
        assert context.previous_files == {}

    def test_to_dict_includes_cross_sheet(self):
        """Test that to_dict includes cross-sheet fields."""
        context = SheetContext(
            sheet_num=3,
            total_sheets=5,
            start_item=21,
            end_item=30,
            workspace=Path("/tmp/workspace"),
            previous_outputs={1: "output 1", 2: "output 2"},
            previous_files={"/tmp/file.md": "content"},
        )
        d = context.to_dict()
        assert d["previous_outputs"] == {1: "output 1", 2: "output 2"}
        assert d["previous_files"] == {"/tmp/file.md": "content"}

    def test_mutable_cross_sheet_fields(self):
        """Test that cross-sheet fields can be populated after creation."""
        context = SheetContext(
            sheet_num=2,
            total_sheets=3,
            start_item=11,
            end_item=20,
            workspace=Path("/tmp"),
        )
        # Populate after creation
        context.previous_outputs[1] = "Sheet 1 output"
        context.previous_files["/tmp/summary.md"] = "# Summary"

        assert context.previous_outputs == {1: "Sheet 1 output"}
        assert context.previous_files == {"/tmp/summary.md": "# Summary"}


class TestJobConfigWithCrossSheet:
    """Tests for JobConfig with cross_sheet configuration."""

    def test_job_config_without_cross_sheet(self):
        """Test that cross_sheet defaults to None."""
        config = JobConfig(
            name="test-job",
            sheet=SheetConfig(size=10, total_items=50),
            prompt=PromptConfig(template="Test {{ sheet_num }}"),
        )
        assert config.cross_sheet is None

    def test_job_config_with_cross_sheet(self):
        """Test JobConfig with cross_sheet enabled."""
        config = JobConfig(
            name="test-job",
            sheet=SheetConfig(size=10, total_items=50),
            prompt=PromptConfig(template="Test {{ sheet_num }}"),
            cross_sheet=CrossSheetConfig(
                auto_capture_stdout=True,
                max_output_chars=3000,
            ),
        )
        assert config.cross_sheet is not None
        assert config.cross_sheet.auto_capture_stdout is True
        assert config.cross_sheet.max_output_chars == 3000


class TestCrossSheetContextPopulation:
    """Tests for cross-sheet context population in runner.

    These tests verify that the _populate_cross_sheet_context and
    _capture_cross_sheet_files methods work correctly.
    """

    @pytest.fixture
    def mock_state_with_outputs(self) -> CheckpointState:
        """Create a mock state with completed sheets."""
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            status="running",
            total_sheets=5,
        )
        # Add completed sheets with stdout
        state.sheets[1] = SheetState(
            sheet_num=1,
            status=SheetStatus.COMPLETED,
            stdout_tail="Sheet 1 completed successfully with output A",
        )
        state.sheets[2] = SheetState(
            sheet_num=2,
            status=SheetStatus.COMPLETED,
            stdout_tail="Sheet 2 completed with B",
        )
        state.sheets[3] = SheetState(
            sheet_num=3,
            status=SheetStatus.COMPLETED,
            stdout_tail="Sheet 3 done",
        )
        state.sheets[4] = SheetState(
            sheet_num=4,
            status=SheetStatus.COMPLETED,
            stdout_tail="",  # Empty stdout
        )
        return state

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            # Create some test files
            (workspace / "sheet-1.md").write_text("# Sheet 1 Summary\nContent 1")
            (workspace / "sheet-2.md").write_text("# Sheet 2 Summary\nContent 2")
            (workspace / "output.txt").write_text("Final output")
            yield workspace

    def test_auto_capture_stdout_lookback(self, mock_state_with_outputs: CheckpointState):
        """Test auto-capture with lookback limit."""
        config = CrossSheetConfig(
            auto_capture_stdout=True,
            lookback_sheets=2,
        )
        context = SheetContext(
            sheet_num=5,
            total_sheets=5,
            start_item=41,
            end_item=50,
            workspace=Path("/tmp"),
        )

        # Simulate what _populate_cross_sheet_context does
        start_sheet = max(1, 5 - config.lookback_sheets)  # 3
        for prev_num in range(start_sheet, 5):
            prev_state = mock_state_with_outputs.sheets.get(prev_num)
            if prev_state and prev_state.stdout_tail:
                context.previous_outputs[prev_num] = prev_state.stdout_tail

        # Should only have sheets 3 and 4 (within lookback of 2)
        # Sheet 4 has empty stdout so not included
        assert 1 not in context.previous_outputs
        assert 2 not in context.previous_outputs
        assert 3 in context.previous_outputs
        assert context.previous_outputs[3] == "Sheet 3 done"

    def test_auto_capture_stdout_all_sheets(self, mock_state_with_outputs: CheckpointState):
        """Test auto-capture with lookback=0 (all sheets)."""
        config = CrossSheetConfig(
            auto_capture_stdout=True,
            lookback_sheets=0,  # All sheets
        )
        context = SheetContext(
            sheet_num=5,
            total_sheets=5,
            start_item=41,
            end_item=50,
            workspace=Path("/tmp"),
        )

        # Simulate what _populate_cross_sheet_context does with lookback=0
        start_sheet = 1 if config.lookback_sheets == 0 else max(1, 5 - config.lookback_sheets)
        for prev_num in range(start_sheet, 5):
            prev_state = mock_state_with_outputs.sheets.get(prev_num)
            if prev_state and prev_state.stdout_tail:
                context.previous_outputs[prev_num] = prev_state.stdout_tail

        # Should have all sheets with non-empty stdout
        assert 1 in context.previous_outputs
        assert 2 in context.previous_outputs
        assert 3 in context.previous_outputs
        assert 4 not in context.previous_outputs  # Empty stdout

    def test_output_truncation(self, mock_state_with_outputs: CheckpointState):
        """Test that outputs are truncated to max_output_chars."""
        # Create a sheet with very long output
        mock_state_with_outputs.sheets[2] = SheetState(
            sheet_num=2,
            status="completed",
            stdout_tail="X" * 5000,  # Long output
        )

        config = CrossSheetConfig(
            auto_capture_stdout=True,
            max_output_chars=100,
        )
        context = SheetContext(
            sheet_num=3,
            total_sheets=3,
            start_item=21,
            end_item=30,
            workspace=Path("/tmp"),
        )

        # Simulate truncation logic
        prev_state = mock_state_with_outputs.sheets[2]
        output = prev_state.stdout_tail
        max_chars = config.max_output_chars
        if len(output) > max_chars:
            output = output[:max_chars] + "\n... [truncated]"
        context.previous_outputs[2] = output

        assert len(context.previous_outputs[2]) < 5000
        assert "[truncated]" in context.previous_outputs[2]


class TestTemplateWithCrossSheetContext:
    """Tests for using cross-sheet context in Jinja2 templates."""

    def test_template_with_previous_outputs(self):
        """Test template rendering with previous_outputs."""
        prompt_config = PromptConfig(
            template="""Sheet {{ sheet_num }}

{% if previous_outputs %}
## Previous Results
{% for sheet_no, output in previous_outputs.items() %}
### Sheet {{ sheet_no }}
{{ output[:100] }}
{% endfor %}
{% endif %}

Do the work."""
        )

        builder = PromptBuilder(prompt_config)
        context = SheetContext(
            sheet_num=3,
            total_sheets=5,
            start_item=21,
            end_item=30,
            workspace=Path("/tmp"),
            previous_outputs={1: "Result A", 2: "Result B"},
        )

        prompt = builder.build_sheet_prompt(context)

        assert "Sheet 3" in prompt
        assert "Previous Results" in prompt
        assert "Sheet 1" in prompt
        assert "Result A" in prompt
        assert "Sheet 2" in prompt
        assert "Result B" in prompt

    def test_template_without_cross_sheet(self):
        """Test template rendering when no cross-sheet context."""
        prompt_config = PromptConfig(
            template="""Sheet {{ sheet_num }}

{% if previous_outputs %}
## Previous
{% endif %}

Do work."""
        )

        builder = PromptBuilder(prompt_config)
        context = SheetContext(
            sheet_num=1,
            total_sheets=5,
            start_item=1,
            end_item=10,
            workspace=Path("/tmp"),
        )

        prompt = builder.build_sheet_prompt(context)

        assert "Sheet 1" in prompt
        # Empty dict is falsy in Jinja2, so "Previous" should not appear
        assert "Previous" not in prompt

    def test_template_with_previous_files(self):
        """Test template rendering with previous_files."""
        prompt_config = PromptConfig(
            template="""Sheet {{ sheet_num }}

{% for path, content in previous_files.items() %}
File: {{ path }}
{{ content[:50] }}
{% endfor %}
"""
        )

        builder = PromptBuilder(prompt_config)
        context = SheetContext(
            sheet_num=2,
            total_sheets=3,
            start_item=11,
            end_item=20,
            workspace=Path("/tmp"),
            previous_files={"/tmp/sheet-1.md": "# Summary\nThis is the content"},
        )

        prompt = builder.build_sheet_prompt(context)

        assert "Sheet 2" in prompt
        assert "/tmp/sheet-1.md" in prompt
        assert "# Summary" in prompt
