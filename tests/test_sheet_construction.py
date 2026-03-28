"""Tests for Sheet construction from existing JobConfig.

Sheet construction bridges the old scattered-dict model (SheetConfig with
separate dicts for descriptions, cadenzas, prompt_extensions, etc.) to the
new first-class Sheet entity. Every sheet in a job gets a fully self-contained
Sheet object with all execution data resolved and collected.

TDD: red first, then green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mozart.core.config.execution import ValidationRule
from mozart.core.config.job import InjectionCategory, InjectionItem


class TestBuildSheetsBasic:
    """Test basic Sheet construction from JobConfig."""

    def _make_config(self, **overrides):
        """Helper to create a minimal JobConfig for testing."""
        from mozart.core.config.job import JobConfig

        defaults = {
            "name": "test-job",
            "workspace": Path("/tmp/test-workspace"),
            "sheet": {"size": 1, "total_items": 3, "start_item": 1},
            "prompt": {"template": "Do task {{ sheet_num }}."},
        }
        defaults.update(overrides)
        return JobConfig(**defaults)

    def test_builds_correct_number_of_sheets(self):
        """build_sheets produces one Sheet per concrete sheet."""
        from mozart.core.sheet import build_sheets

        config = self._make_config()
        sheets = build_sheets(config)
        assert len(sheets) == 3

    def test_sheet_nums_are_sequential(self):
        """Sheet numbers are 1-indexed and sequential."""
        from mozart.core.sheet import build_sheets

        config = self._make_config()
        sheets = build_sheets(config)
        assert [s.num for s in sheets] == [1, 2, 3]

    def test_instrument_name_from_backend_type(self):
        """When no instrument: field exists, use backend.type."""
        from mozart.core.sheet import build_sheets

        config = self._make_config()
        sheets = build_sheets(config)
        for sheet in sheets:
            assert sheet.instrument_name == "claude_cli"

    def test_instrument_name_from_anthropic_api(self):
        """Backend type anthropic_api maps correctly."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(backend={"type": "anthropic_api"})
        sheets = build_sheets(config)
        for sheet in sheets:
            assert sheet.instrument_name == "anthropic_api"

    def test_prompt_template_from_config(self):
        """prompt_template comes from prompt.template."""
        from mozart.core.sheet import build_sheets

        config = self._make_config()
        sheets = build_sheets(config)
        for sheet in sheets:
            assert sheet.prompt_template == "Do task {{ sheet_num }}."

    def test_prompt_template_file(self):
        """template_file comes from prompt.template_file."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            prompt={"template_file": "/tmp/template.j2"},
        )
        sheets = build_sheets(config)
        for sheet in sheets:
            assert sheet.prompt_template is None
            assert sheet.template_file == Path("/tmp/template.j2")

    def test_variables_from_config(self):
        """Template variables come from prompt.variables."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            prompt={
                "template": "Build {{ component }}.",
                "variables": {"component": "Dashboard"},
            },
        )
        sheets = build_sheets(config)
        for sheet in sheets:
            assert sheet.variables["component"] == "Dashboard"

    def test_workspace_from_config(self):
        """Workspace path comes from config."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            workspace=Path("/home/user/work"),
        )
        sheets = build_sheets(config)
        for sheet in sheets:
            assert sheet.workspace == Path("/home/user/work").resolve()

    def test_timeout_from_backend(self):
        """Timeout comes from backend.timeout_seconds."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            backend={"type": "claude_cli", "timeout_seconds": 600.0},
        )
        sheets = build_sheets(config)
        for sheet in sheets:
            assert sheet.timeout_seconds == 600.0

    def test_timeout_override_per_sheet(self):
        """Per-sheet timeout overrides take precedence."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            backend={
                "type": "claude_cli",
                "timeout_seconds": 1800.0,
                "timeout_overrides": {2: 300.0},
            },
        )
        sheets = build_sheets(config)
        assert sheets[0].timeout_seconds == 1800.0
        assert sheets[1].timeout_seconds == 300.0  # sheet 2 overridden
        assert sheets[2].timeout_seconds == 1800.0

    def test_validations_applied_to_all_sheets(self):
        """Score-level validations apply to all sheets."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            validations=[
                {"type": "file_exists", "path": "{workspace}/output.txt"},
            ],
        )
        sheets = build_sheets(config)
        for sheet in sheets:
            assert len(sheet.validations) == 1
            assert sheet.validations[0].type == "file_exists"


class TestBuildSheetsDescriptions:
    """Test sheet description resolution."""

    def _make_config(self, **overrides):
        from mozart.core.config.job import JobConfig

        defaults = {
            "name": "test-job",
            "workspace": Path("/tmp/ws"),
            "sheet": {"size": 1, "total_items": 3, "start_item": 1},
            "prompt": {"template": "Task."},
        }
        defaults.update(overrides)
        return JobConfig(**defaults)

    def test_descriptions_from_sheet_config(self):
        """Sheet descriptions come from sheet.descriptions dict."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            sheet={
                "size": 1,
                "total_items": 3,
                "start_item": 1,
                "descriptions": {1: "Setup", 2: "Build", 3: "Test"},
            },
        )
        sheets = build_sheets(config)
        assert sheets[0].description == "Setup"
        assert sheets[1].description == "Build"
        assert sheets[2].description == "Test"

    def test_missing_descriptions_are_none(self):
        """Sheets without descriptions get None."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            sheet={
                "size": 1,
                "total_items": 3,
                "start_item": 1,
                "descriptions": {2: "Only this one"},
            },
        )
        sheets = build_sheets(config)
        assert sheets[0].description is None
        assert sheets[1].description == "Only this one"
        assert sheets[2].description is None


class TestBuildSheetsContextInjection:
    """Test prelude, cadenza, and prompt extension resolution."""

    def _make_config(self, **overrides):
        from mozart.core.config.job import JobConfig

        defaults = {
            "name": "test-job",
            "workspace": Path("/tmp/ws"),
            "sheet": {"size": 1, "total_items": 3, "start_item": 1},
            "prompt": {"template": "Task."},
        }
        defaults.update(overrides)
        return JobConfig(**defaults)

    def test_prelude_shared_across_all_sheets(self):
        """Prelude items are shared across all sheets."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            sheet={
                "size": 1,
                "total_items": 2,
                "start_item": 1,
                "prelude": [
                    {"file": "context.md", "as": "context"},
                ],
            },
        )
        sheets = build_sheets(config)
        assert len(sheets[0].prelude) == 1
        assert len(sheets[1].prelude) == 1
        assert sheets[0].prelude[0].file == "context.md"

    def test_cadenzas_per_sheet(self):
        """Cadenzas are per-sheet context injections."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            sheet={
                "size": 1,
                "total_items": 3,
                "start_item": 1,
                "cadenzas": {
                    2: [{"file": "review.md", "as": "skill"}],
                },
            },
        )
        sheets = build_sheets(config)
        assert sheets[0].cadenza == []
        assert len(sheets[1].cadenza) == 1
        assert sheets[1].cadenza[0].file == "review.md"
        assert sheets[2].cadenza == []

    def test_prompt_extensions_merged(self):
        """Score-level and per-sheet prompt extensions are merged."""
        from mozart.core.sheet import build_sheets

        config = self._make_config(
            prompt={
                "template": "Task.",
                "prompt_extensions": ["Score-level extension."],
            },
            sheet={
                "size": 1,
                "total_items": 2,
                "start_item": 1,
                "prompt_extensions": {
                    2: ["Per-sheet extension."],
                },
            },
        )
        sheets = build_sheets(config)
        # Sheet 1: only score-level
        assert sheets[0].prompt_extensions == ["Score-level extension."]
        # Sheet 2: score-level + per-sheet
        assert "Score-level extension." in sheets[1].prompt_extensions
        assert "Per-sheet extension." in sheets[1].prompt_extensions


class TestBuildSheetsFanOut:
    """Test Sheet construction with fan-out (harmonized movements)."""

    def _make_config(self, **overrides):
        from mozart.core.config.job import JobConfig

        defaults = {
            "name": "fan-out-job",
            "workspace": Path("/tmp/ws"),
            "sheet": {
                "size": 1,
                "total_items": 3,
                "start_item": 1,
                "fan_out": {2: 3},
            },
            "prompt": {"template": "Stage {{ stage }}, instance {{ instance }}."},
        }
        defaults.update(overrides)
        return JobConfig(**defaults)

    def test_fan_out_produces_extra_sheets(self):
        """Fan-out 3 voices in stage 2 → 5 total sheets (1 + 3 + 1)."""
        from mozart.core.sheet import build_sheets

        config = self._make_config()
        sheets = build_sheets(config)
        assert len(sheets) == 5

    def test_fan_out_movement_numbers(self):
        """Fan-out sheets have correct movement numbers."""
        from mozart.core.sheet import build_sheets

        config = self._make_config()
        sheets = build_sheets(config)
        movements = [s.movement for s in sheets]
        # Stage 1 = movement 1, stage 2 (3 voices) = movement 2, stage 3 = movement 3
        assert movements == [1, 2, 2, 2, 3]

    def test_fan_out_voice_numbers(self):
        """Fan-out sheets have correct voice numbers."""
        from mozart.core.sheet import build_sheets

        config = self._make_config()
        sheets = build_sheets(config)
        voices = [s.voice for s in sheets]
        # Solo sheets have voice=None, harmonized have voice=1,2,3
        assert voices[0] is None  # movement 1 solo
        assert voices[1] == 1  # movement 2, voice 1
        assert voices[2] == 2  # movement 2, voice 2
        assert voices[3] == 3  # movement 2, voice 3
        assert voices[4] is None  # movement 3 solo

    def test_fan_out_voice_count(self):
        """Fan-out sheets report correct voice_count."""
        from mozart.core.sheet import build_sheets

        config = self._make_config()
        sheets = build_sheets(config)
        counts = [s.voice_count for s in sheets]
        assert counts == [1, 3, 3, 3, 1]
