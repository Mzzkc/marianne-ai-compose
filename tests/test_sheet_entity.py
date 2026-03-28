"""Tests for mozart.core.sheet — Sheet entity model.

TDD: These tests define the contract for the Sheet entity, the first-class
execution unit in Mozart's sheet-first architecture. A Sheet carries everything
a musician needs to execute: identity, instrument, prompt, context, validations.

Tests cover: construction, identity fields, defaults, immutability guarantees,
template variable generation, adversarial inputs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mozart.core.config.execution import ValidationRule
from mozart.core.config.job import InjectionCategory, InjectionItem


# --- Sheet Construction ---


class TestSheetConstruction:
    """Test Sheet entity creation with various field combinations."""

    def test_minimal_sheet(self):
        """Minimal sheet with required fields only."""
        from mozart.core.sheet import Sheet

        sheet = Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            description=None,
            workspace=Path("/tmp/test-workspace"),
            instrument_name="claude-code",
        )
        assert sheet.num == 1
        assert sheet.movement == 1
        assert sheet.voice is None
        assert sheet.voice_count == 1
        assert sheet.instrument_name == "claude-code"
        assert sheet.instrument_config == {}
        assert sheet.prompt_template is None
        assert sheet.template_file is None
        assert sheet.variables == {}
        assert sheet.prelude == []
        assert sheet.cadenza == []
        assert sheet.prompt_extensions == []
        assert sheet.validations == []
        assert sheet.timeout_seconds == 1800.0

    def test_full_sheet(self):
        """Fully specified sheet with all optional fields."""
        from mozart.core.sheet import Sheet

        sheet = Sheet(
            num=5,
            movement=2,
            voice=3,
            voice_count=5,
            description="Build the frontend component",
            workspace=Path("/home/user/workspaces/full-stack"),
            instrument_name="gemini-cli",
            instrument_config={"model": "gemini-2.5-pro", "timeout_seconds": 600},
            prompt_template="Build {{ component_name }} using React.",
            template_file=None,
            variables={"component_name": "Dashboard"},
            prelude=[
                InjectionItem(file="context.md", **{"as": InjectionCategory.CONTEXT}),
            ],
            cadenza=[
                InjectionItem(file="review.md", **{"as": InjectionCategory.SKILL}),
            ],
            prompt_extensions=["Follow the project style guide."],
            validations=[
                ValidationRule(
                    type="file_exists",
                    path="{workspace}/src/Dashboard.tsx",
                ),
            ],
            timeout_seconds=600.0,
        )
        assert sheet.num == 5
        assert sheet.movement == 2
        assert sheet.voice == 3
        assert sheet.voice_count == 5
        assert sheet.description == "Build the frontend component"
        assert sheet.instrument_config["model"] == "gemini-2.5-pro"
        assert len(sheet.prelude) == 1
        assert len(sheet.cadenza) == 1
        assert len(sheet.validations) == 1

    def test_sheet_with_template_file(self):
        """Sheet using an external template file instead of inline."""
        from mozart.core.sheet import Sheet

        sheet = Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="claude-code",
            template_file=Path("/home/user/templates/build.j2"),
        )
        assert sheet.prompt_template is None
        assert sheet.template_file == Path("/home/user/templates/build.j2")


# --- Identity and Numbering ---


class TestSheetIdentity:
    """Test Sheet identity fields and numbering constraints."""

    def test_sheet_num_must_be_positive(self):
        """Sheet numbers are 1-indexed, must be >= 1."""
        from mozart.core.sheet import Sheet

        with pytest.raises(ValidationError, match="num"):
            Sheet(
                num=0,
                movement=1,
                voice=None,
                voice_count=1,
                workspace=Path("/tmp/ws"),
                instrument_name="test",
            )

    def test_movement_must_be_positive(self):
        """Movement numbers must be >= 1."""
        from mozart.core.sheet import Sheet

        with pytest.raises(ValidationError, match="movement"):
            Sheet(
                num=1,
                movement=0,
                voice=None,
                voice_count=1,
                workspace=Path("/tmp/ws"),
                instrument_name="test",
            )

    def test_voice_must_be_positive_if_set(self):
        """Voice numbers must be >= 1 when provided."""
        from mozart.core.sheet import Sheet

        with pytest.raises(ValidationError, match="voice"):
            Sheet(
                num=1,
                movement=1,
                voice=0,
                voice_count=1,
                workspace=Path("/tmp/ws"),
                instrument_name="test",
            )

    def test_voice_count_must_be_positive(self):
        """voice_count must be >= 1."""
        from mozart.core.sheet import Sheet

        with pytest.raises(ValidationError, match="voice_count"):
            Sheet(
                num=1,
                movement=1,
                voice=None,
                voice_count=0,
                workspace=Path("/tmp/ws"),
                instrument_name="test",
            )


# --- Template Variables ---


class TestSheetTemplateVariables:
    """Test Sheet.template_variables property for Jinja2 rendering."""

    def test_basic_template_variables(self):
        """Basic sheet produces standard template variables."""
        from mozart.core.sheet import Sheet

        sheet = Sheet(
            num=3,
            movement=2,
            voice=None,
            voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="claude-code",
            variables={"custom_var": "hello"},
        )
        tvars = sheet.template_variables(total_sheets=10, total_movements=5)
        assert tvars["sheet_num"] == 3
        assert tvars["total_sheets"] == 10
        assert tvars["workspace"] == str(Path("/tmp/ws"))
        assert tvars["instrument_name"] == "claude-code"
        # New terminology
        assert tvars["movement"] == 2
        assert tvars["voice"] is None
        assert tvars["voice_count"] == 1
        assert tvars["total_movements"] == 5
        # Old terminology (aliases — kept forever)
        assert tvars["stage"] == 2
        assert tvars["instance"] is None
        assert tvars["fan_count"] == 1
        assert tvars["total_stages"] == 5
        # Custom variables
        assert tvars["custom_var"] == "hello"

    def test_harmonized_movement_template_variables(self):
        """Harmonized movement (multi-voice) produces correct variables."""
        from mozart.core.sheet import Sheet

        sheet = Sheet(
            num=7,
            movement=3,
            voice=2,
            voice_count=4,
            workspace=Path("/tmp/ws"),
            instrument_name="gemini-cli",
        )
        tvars = sheet.template_variables(total_sheets=20, total_movements=5)
        assert tvars["voice"] == 2
        assert tvars["voice_count"] == 4
        assert tvars["instance"] == 2  # alias
        assert tvars["fan_count"] == 4  # alias

    def test_custom_variables_do_not_override_builtins(self):
        """Custom variables cannot override built-in template variables."""
        from mozart.core.sheet import Sheet

        sheet = Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="test",
            variables={"sheet_num": 999, "workspace": "/hacked"},
        )
        tvars = sheet.template_variables(total_sheets=5, total_movements=3)
        # Built-ins win over custom variables
        assert tvars["sheet_num"] == 1
        assert tvars["workspace"] == str(Path("/tmp/ws"))


# --- Serialization ---


class TestSheetSerialization:
    """Test Sheet serialization/deserialization."""

    def test_serialization_roundtrip(self):
        """Sheet can be serialized to dict and reconstructed."""
        from mozart.core.sheet import Sheet

        sheet = Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="claude-code",
            description="Test sheet",
            prompt_template="Do the thing.",
        )
        data = sheet.model_dump()
        reconstructed = Sheet.model_validate(data)
        assert reconstructed.num == sheet.num
        assert reconstructed.instrument_name == sheet.instrument_name
        assert reconstructed.prompt_template == sheet.prompt_template


# --- Adversarial ---


class TestSheetAdversarial:
    """Adversarial tests for Sheet entity."""

    @pytest.mark.adversarial
    def test_empty_instrument_name_rejected(self):
        """Empty instrument name should fail."""
        from mozart.core.sheet import Sheet

        with pytest.raises(ValidationError, match="instrument_name"):
            Sheet(
                num=1,
                movement=1,
                voice=None,
                voice_count=1,
                workspace=Path("/tmp/ws"),
                instrument_name="",
            )

    @pytest.mark.adversarial
    def test_very_large_sheet_num_accepted(self):
        """Very large sheet numbers should work (big scores)."""
        from mozart.core.sheet import Sheet

        sheet = Sheet(
            num=10000,
            movement=500,
            voice=None,
            voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="test",
        )
        assert sheet.num == 10000

    @pytest.mark.adversarial
    def test_unicode_in_description(self):
        """Unicode in description should work."""
        from mozart.core.sheet import Sheet

        sheet = Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="test",
            description="构建前端组件 — 使用React",
        )
        assert "构建" in sheet.description  # type: ignore[operator]

    @pytest.mark.adversarial
    def test_timeout_must_be_positive(self):
        """timeout_seconds must be > 0."""
        from mozart.core.sheet import Sheet

        with pytest.raises(ValidationError, match="timeout_seconds"):
            Sheet(
                num=1,
                movement=1,
                voice=None,
                voice_count=1,
                workspace=Path("/tmp/ws"),
                instrument_name="test",
                timeout_seconds=0,
            )
