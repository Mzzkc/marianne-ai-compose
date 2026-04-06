"""Tests for prelude & cadenza context injection system (GH#53).

Phase 1: Config models and data structures.
Phase 2: File resolution and prompt injection.
Phase 3: Integration, validation checks (future).
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from marianne.core.config import InjectionCategory, InjectionItem, JobConfig, PromptConfig
from marianne.core.config.job import SheetConfig
from marianne.execution.runner import JobRunner
from marianne.prompts.templating import PromptBuilder, SheetContext


# ── Phase 1: Config Models & Data Structures ─────────────────────────


class TestInjectionCategory:
    """Tests for InjectionCategory enum."""

    def test_context_value(self):
        assert InjectionCategory.CONTEXT.value == "context"

    def test_skill_value(self):
        assert InjectionCategory.SKILL.value == "skill"

    def test_tool_value(self):
        assert InjectionCategory.TOOL.value == "tool"

    def test_all_categories_are_strings(self):
        for cat in InjectionCategory:
            assert isinstance(cat, str)
            assert isinstance(cat.value, str)

    def test_enum_has_exactly_three_members(self):
        assert len(InjectionCategory) == 3


class TestInjectionItem:
    """Tests for InjectionItem model."""

    def test_basic_creation_with_alias(self):
        """InjectionItem should accept 'as' via alias."""
        item = InjectionItem(file="context.md", **{"as": "context"})
        assert item.file == "context.md"
        assert item.as_ == InjectionCategory.CONTEXT

    def test_creation_with_field_name(self):
        """InjectionItem should accept 'as_' directly (populate_by_name)."""
        item = InjectionItem(file="skill.md", as_=InjectionCategory.SKILL)
        assert item.as_ == InjectionCategory.SKILL

    def test_all_categories_accepted(self):
        """All three categories should be valid."""
        for cat_value in ["context", "skill", "tool"]:
            item = InjectionItem(file="test.md", **{"as": cat_value})
            assert item.as_.value == cat_value

    def test_invalid_category_raises(self):
        """Invalid 'as' value should raise ValidationError."""
        with pytest.raises(ValidationError):
            InjectionItem(file="test.md", **{"as": "invalid"})

    def test_missing_file_raises(self):
        """Missing 'file' field should raise ValidationError."""
        with pytest.raises(ValidationError):
            InjectionItem(**{"as": "context"})

    def test_jinja_path_accepted(self):
        """File paths with Jinja templating should be accepted."""
        item = InjectionItem(
            file="{{ workspace }}/output.md", **{"as": "context"}
        )
        assert "{{ workspace }}" in item.file

    def test_serialization_uses_alias(self):
        """model_dump(by_alias=True) should use 'as' not 'as_'."""
        item = InjectionItem(file="test.md", **{"as": "skill"})
        dumped = item.model_dump(by_alias=True)
        assert "as" in dumped
        assert dumped["as"] == "skill"

    def test_serialization_uses_field_name(self):
        """model_dump() should use 'as_' by default."""
        item = InjectionItem(file="test.md", **{"as": "tool"})
        dumped = item.model_dump()
        assert "as_" in dumped


class TestSheetConfigPreludeCadenza:
    """Tests for prelude/cadenzas fields on SheetConfig."""

    def test_prelude_defaults_empty(self):
        sc = SheetConfig(size=1, total_items=3)
        assert sc.prelude == []

    def test_cadenzas_defaults_empty(self):
        sc = SheetConfig(size=1, total_items=3)
        assert sc.cadenzas == {}

    def test_prelude_with_items(self):
        sc = SheetConfig(
            size=1,
            total_items=3,
            prelude=[
                InjectionItem(file="a.md", **{"as": "context"}),
                InjectionItem(file="b.md", **{"as": "skill"}),
            ],
        )
        assert len(sc.prelude) == 2

    def test_cadenzas_with_items(self):
        sc = SheetConfig(
            size=1,
            total_items=3,
            cadenzas={
                1: [InjectionItem(file="c.md", **{"as": "tool"})],
                2: [
                    InjectionItem(file="d.md", **{"as": "context"}),
                    InjectionItem(file="e.md", **{"as": "skill"}),
                ],
            },
        )
        assert len(sc.cadenzas) == 2
        assert len(sc.cadenzas[1]) == 1
        assert len(sc.cadenzas[2]) == 2

    def test_existing_configs_unaffected(self):
        """Configs without prelude/cadenzas should still work."""
        sc = SheetConfig(
            size=10,
            total_items=30,
            dependencies={2: [1]},
            skip_when={3: "sheets.get(1)"},
        )
        assert sc.prelude == []
        assert sc.cadenzas == {}
        assert sc.total_sheets == 3


class TestSheetContextInjectionFields:
    """Tests for injection fields on SheetContext."""

    def _make_ctx(self, **kwargs) -> SheetContext:
        defaults = {
            "sheet_num": 1,
            "total_sheets": 3,
            "start_item": 1,
            "end_item": 1,
            "workspace": Path("/tmp"),
        }
        defaults.update(kwargs)
        return SheetContext(**defaults)

    def test_defaults_empty(self):
        ctx = self._make_ctx()
        assert ctx.injected_context == []
        assert ctx.injected_skills == []
        assert ctx.injected_tools == []

    def test_can_set_injected_context(self):
        ctx = self._make_ctx()
        ctx.injected_context = ["Background info"]
        assert ctx.injected_context == ["Background info"]

    def test_can_set_injected_skills(self):
        ctx = self._make_ctx()
        ctx.injected_skills = ["Skill instruction"]
        assert ctx.injected_skills == ["Skill instruction"]

    def test_can_set_injected_tools(self):
        ctx = self._make_ctx()
        ctx.injected_tools = ["Tool description"]
        assert ctx.injected_tools == ["Tool description"]

    def test_to_dict_includes_injection_fields(self):
        ctx = self._make_ctx()
        ctx.injected_context = ["ctx1"]
        ctx.injected_skills = ["sk1"]
        ctx.injected_tools = ["tl1"]
        d = ctx.to_dict()
        assert d["injected_context"] == ["ctx1"]
        assert d["injected_skills"] == ["sk1"]
        assert d["injected_tools"] == ["tl1"]

    def test_to_dict_empty_injection_fields(self):
        ctx = self._make_ctx()
        d = ctx.to_dict()
        assert d["injected_context"] == []
        assert d["injected_skills"] == []
        assert d["injected_tools"] == []


# ── Phase 2: File Resolution & Prompt Injection ────────────────────────


def _make_config(**overrides: object) -> JobConfig:
    """Build a minimal JobConfig for injection tests."""
    base: dict = {
        "name": "test-injection",
        "description": "Test prelude/cadenza injection",
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 1, "total_items": 3},
        "prompt": {"template": "Do the task."},
        "retry": {"max_retries": 3},
        "validations": [],
        "pause_between_sheets_seconds": 0,
    }
    base.update(overrides)
    return JobConfig.model_validate(base)


def _make_runner(config: JobConfig | None = None) -> JobRunner:
    """Build a minimal JobRunner for injection tests."""
    cfg = config or _make_config()
    backend = AsyncMock()
    state_backend = AsyncMock()
    return JobRunner(config=cfg, backend=backend, state_backend=state_backend)


class TestResolveInjections:
    """Phase 2: Tests for _resolve_injections() on ContextBuildingMixin."""

    def test_jinja_path_expansion(self, tmp_path: Path) -> None:
        """Jinja templates in file paths are expanded using context vars."""
        # Create a file at workspace/context.md
        ctx_file = tmp_path / "context.md"
        ctx_file.write_text("Background knowledge")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "{{ workspace }}/context.md", "as": "context"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert ctx.injected_context == ["Background knowledge"]

    def test_file_read_at_execution_time(self, tmp_path: Path) -> None:
        """Files are read when _resolve_injections runs, not at config parse."""
        skill_file = tmp_path / "skill.md"
        skill_file.write_text("Skill content v1")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "skill.md", "as": "skill"},
                ],
            },
        )
        runner = _make_runner(config)

        # Overwrite file before building context
        skill_file.write_text("Skill content v2")

        ctx = runner._build_sheet_context(sheet_num=1)
        assert ctx.injected_skills == ["Skill content v2"]

    def test_missing_file_context_warns_and_skips(self, tmp_path: Path) -> None:
        """Missing file with category=context is skipped (warn, no error)."""
        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "nonexistent.md", "as": "context"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        # Should be empty — file was skipped
        assert ctx.injected_context == []

    def test_missing_file_skill_logs_error_and_skips(self, tmp_path: Path) -> None:
        """Missing file with category=skill logs error and skips."""
        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "missing-skill.md", "as": "skill"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        # Should be empty — file was skipped
        assert ctx.injected_skills == []

    def test_missing_file_tool_logs_error_and_skips(self, tmp_path: Path) -> None:
        """Missing file with category=tool logs error and skips."""
        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "missing-tool.md", "as": "tool"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert ctx.injected_tools == []

    def test_category_separation(self, tmp_path: Path) -> None:
        """Items are categorized correctly into context/skills/tools."""
        (tmp_path / "ctx.md").write_text("Context content")
        (tmp_path / "sk.md").write_text("Skill content")
        (tmp_path / "tl.md").write_text("Tool content")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "ctx.md", "as": "context"},
                    {"file": "sk.md", "as": "skill"},
                    {"file": "tl.md", "as": "tool"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert ctx.injected_context == ["Context content"]
        assert ctx.injected_skills == ["Skill content"]
        assert ctx.injected_tools == ["Tool content"]

    def test_cadenza_only_applies_to_matching_sheet(self, tmp_path: Path) -> None:
        """Cadenza items only apply to the sheet they're configured for."""
        (tmp_path / "sheet2-extra.md").write_text("Sheet 2 extra")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "cadenzas": {
                    2: [{"file": "sheet2-extra.md", "as": "context"}],
                },
            },
        )
        runner = _make_runner(config)

        ctx1 = runner._build_sheet_context(sheet_num=1)
        assert ctx1.injected_context == []

        ctx2 = runner._build_sheet_context(sheet_num=2)
        assert ctx2.injected_context == ["Sheet 2 extra"]

    def test_prelude_plus_cadenza_combined(self, tmp_path: Path) -> None:
        """Prelude and cadenza items are combined for matching sheets."""
        (tmp_path / "shared.md").write_text("Shared prelude")
        (tmp_path / "cadenza.md").write_text("Sheet-specific")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "shared.md", "as": "context"},
                ],
                "cadenzas": {
                    1: [{"file": "cadenza.md", "as": "context"}],
                },
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert len(ctx.injected_context) == 2
        assert ctx.injected_context[0] == "Shared prelude"
        assert ctx.injected_context[1] == "Sheet-specific"

    def test_relative_path_resolved_to_workspace(self, tmp_path: Path) -> None:
        """Relative file paths are resolved relative to workspace."""
        (tmp_path / "relative.md").write_text("Relative content")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "relative.md", "as": "context"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert ctx.injected_context == ["Relative content"]

    def test_no_injections_noop(self) -> None:
        """When no prelude/cadenzas configured, injection fields stay empty."""
        config = _make_config()
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert ctx.injected_context == []
        assert ctx.injected_skills == []
        assert ctx.injected_tools == []


class TestPromptInjectionSections:
    """Phase 2: Tests for injection content appearing in built prompts."""

    def test_skills_section_in_prompt(self) -> None:
        """Injected skills appear in prompt under 'Injected Skills' heading."""
        pb = PromptBuilder(PromptConfig(template="Do the task."))
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/tmp"),
        )
        ctx.injected_skills = ["Use TDD methodology"]
        prompt = pb.build_sheet_prompt(ctx)

        assert "## Injected Skills" in prompt
        assert "Use TDD methodology" in prompt

    def test_tools_section_in_prompt(self) -> None:
        """Injected tools appear in prompt under 'Injected Tools' heading."""
        pb = PromptBuilder(PromptConfig(template="Do the task."))
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/tmp"),
        )
        ctx.injected_tools = ["grep, find, make"]
        prompt = pb.build_sheet_prompt(ctx)

        assert "## Injected Tools" in prompt
        assert "grep, find, make" in prompt

    def test_context_section_in_prompt(self) -> None:
        """Injected context appears in prompt under 'Injected Context' heading."""
        pb = PromptBuilder(PromptConfig(template="Do the task."))
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/tmp"),
        )
        ctx.injected_context = ["Background: This is a Python project"]
        prompt = pb.build_sheet_prompt(ctx)

        assert "## Injected Context" in prompt
        assert "Background: This is a Python project" in prompt

    def test_prompt_ordering_skills_before_context(self) -> None:
        """Skills/tools appear before context in the prompt."""
        pb = PromptBuilder(PromptConfig(template="Do the task."))
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/tmp"),
        )
        ctx.injected_skills = ["Skill here"]
        ctx.injected_context = ["Context here"]
        prompt = pb.build_sheet_prompt(ctx)

        skills_pos = prompt.index("## Injected Skills")
        context_pos = prompt.index("## Injected Context")
        assert skills_pos < context_pos

    def test_no_injection_sections_when_empty(self) -> None:
        """No injection sections in prompt when all injection fields are empty."""
        pb = PromptBuilder(PromptConfig(template="Do the task."))
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/tmp"),
        )
        prompt = pb.build_sheet_prompt(ctx)

        assert "Injected Skills" not in prompt
        assert "Injected Tools" not in prompt
        assert "Injected Context" not in prompt

    def test_injection_coexists_with_validation_rules(self) -> None:
        """Injection sections don't interfere with validation requirements."""
        from marianne.core.config import ValidationRule

        pb = PromptBuilder(PromptConfig(template="Do the task."))
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/tmp"),
        )
        ctx.injected_skills = ["Skill A"]
        ctx.injected_context = ["Context B"]

        rules = [
            ValidationRule(
                type="file_exists",
                path="/tmp/output.txt",
                description="Output file",
            ),
        ]
        prompt = pb.build_sheet_prompt(ctx, validation_rules=rules)

        assert "## Injected Skills" in prompt
        assert "## Injected Context" in prompt
        assert "Success Requirements" in prompt
        assert "Output file" in prompt

    def test_injection_coexists_with_patterns(self) -> None:
        """Injection sections don't interfere with learned patterns."""
        pb = PromptBuilder(PromptConfig(template="Do the task."))
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/tmp"),
        )
        ctx.injected_context = ["Important background"]

        prompt = pb.build_sheet_prompt(ctx, patterns=["Always check tests"])

        assert "## Injected Context" in prompt
        assert "Important background" in prompt
        assert "Learned Patterns" in prompt
        assert "Always check tests" in prompt


class TestResolveInjectionsAdvanced:
    """Phase 2: Advanced injection resolution tests."""

    def test_jinja_sheet_num_expansion(self, tmp_path: Path) -> None:
        """Jinja template with {{ sheet_num }} expands to current sheet."""
        (tmp_path / "sheet-2-notes.md").write_text("Notes for sheet 2")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "{{ workspace }}/sheet-{{ sheet_num }}-notes.md", "as": "context"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=2)

        assert ctx.injected_context == ["Notes for sheet 2"]

    def test_jinja_expansion_error_skips_item(self, tmp_path: Path) -> None:
        """Invalid Jinja template in file path is skipped gracefully."""
        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "{{ invalid syntax", "as": "context"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        # Should be empty — bad template was skipped
        assert ctx.injected_context == []

    def test_multiple_items_per_category(self, tmp_path: Path) -> None:
        """Multiple files in the same category are all collected."""
        (tmp_path / "ctx1.md").write_text("Context A")
        (tmp_path / "ctx2.md").write_text("Context B")
        (tmp_path / "ctx3.md").write_text("Context C")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "ctx1.md", "as": "context"},
                    {"file": "ctx2.md", "as": "context"},
                    {"file": "ctx3.md", "as": "context"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert len(ctx.injected_context) == 3
        assert ctx.injected_context == ["Context A", "Context B", "Context C"]

    def test_mixed_present_and_missing_files(self, tmp_path: Path) -> None:
        """Present files are read even when other files in the list are missing."""
        (tmp_path / "exists.md").write_text("I exist")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "missing.md", "as": "context"},
                    {"file": "exists.md", "as": "context"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert ctx.injected_context == ["I exist"]

    def test_absolute_path_used_directly(self, tmp_path: Path) -> None:
        """Absolute file paths are used as-is, not joined with workspace."""
        abs_file = tmp_path / "absolute.md"
        abs_file.write_text("Absolute content")

        config = _make_config(
            workspace=str(tmp_path / "different-workspace"),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": str(abs_file), "as": "skill"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert ctx.injected_skills == ["Absolute content"]


class TestFormatInjectionSection:
    """Phase 2: Tests for _format_injection_section() static method."""

    def test_empty_returns_empty(self) -> None:
        result = PromptBuilder._format_injection_section([], [])
        assert result == ""

    def test_skills_only(self) -> None:
        result = PromptBuilder._format_injection_section(["Skill A"], [])
        assert "## Injected Skills" in result
        assert "Skill A" in result
        assert "## Injected Tools" not in result

    def test_tools_only(self) -> None:
        result = PromptBuilder._format_injection_section([], ["Tool A"])
        assert "## Injected Tools" in result
        assert "Tool A" in result
        assert "## Injected Skills" not in result

    def test_both_skills_and_tools(self) -> None:
        result = PromptBuilder._format_injection_section(
            ["Skill X"], ["Tool Y"]
        )
        assert "## Injected Skills" in result
        assert "## Injected Tools" in result
        assert "Skill X" in result
        assert "Tool Y" in result

    def test_multiple_items_joined(self) -> None:
        result = PromptBuilder._format_injection_section(
            ["Skill 1", "Skill 2"], []
        )
        assert "Skill 1" in result
        assert "Skill 2" in result


# ── Phase 3: Integration, Validation Checks & Polish ──────────────────


class TestEndToEndInjection:
    """Phase 3: End-to-end integration test simulating the full pipeline.

    Verifies: config parse → context build → prompt build with all
    injection categories, cross-sheet context, and validation rules.
    """

    def test_full_pipeline_config_to_prompt(self, tmp_path: Path) -> None:
        """Full pipeline: config → _build_sheet_context → build_sheet_prompt."""
        # Create injection files
        (tmp_path / "prelude-context.md").write_text("Project background info")
        (tmp_path / "prelude-skill.md").write_text("Use TDD methodology")
        (tmp_path / "cadenza-tool.md").write_text("Available: grep, find")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "prelude-context.md", "as": "context"},
                    {"file": "prelude-skill.md", "as": "skill"},
                ],
                "cadenzas": {
                    2: [{"file": "cadenza-tool.md", "as": "tool"}],
                },
            },
        )
        runner = _make_runner(config)

        # Build context for sheet 2 (has both prelude + cadenza)
        ctx = runner._build_sheet_context(sheet_num=2)

        # Verify context was populated
        assert ctx.injected_context == ["Project background info"]
        assert ctx.injected_skills == ["Use TDD methodology"]
        assert ctx.injected_tools == ["Available: grep, find"]

        # Build prompt from that context
        prompt = runner.prompt_builder.build_sheet_prompt(ctx)

        # Verify all injected content appears in the prompt
        assert "Project background info" in prompt
        assert "Use TDD methodology" in prompt
        assert "Available: grep, find" in prompt
        assert "## Injected Context" in prompt
        assert "## Injected Skills" in prompt
        assert "## Injected Tools" in prompt

    def test_pipeline_sheet_without_cadenza(self, tmp_path: Path) -> None:
        """Sheet without matching cadenza only gets prelude injections."""
        (tmp_path / "prelude.md").write_text("Always present")
        (tmp_path / "sheet3-only.md").write_text("Sheet 3 extra")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "prelude.md", "as": "context"},
                ],
                "cadenzas": {
                    3: [{"file": "sheet3-only.md", "as": "skill"}],
                },
            },
        )
        runner = _make_runner(config)

        # Sheet 1: only prelude
        ctx1 = runner._build_sheet_context(sheet_num=1)
        assert ctx1.injected_context == ["Always present"]
        assert ctx1.injected_skills == []

        # Sheet 3: prelude + cadenza
        ctx3 = runner._build_sheet_context(sheet_num=3)
        assert ctx3.injected_context == ["Always present"]
        assert ctx3.injected_skills == ["Sheet 3 extra"]

    def test_coexistence_with_cross_sheet_and_prompt_extensions(
        self, tmp_path: Path,
    ) -> None:
        """Prelude/cadenza coexists with cross_sheet context."""
        (tmp_path / "skill.md").write_text("Skill content")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "skill.md", "as": "skill"},
                ],
            },
            cross_sheet={
                "auto_capture_stdout": True,
                "lookback_sheets": 1,
            },
        )
        runner = _make_runner(config)

        # Build context without state (no cross-sheet data to populate)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert ctx.injected_skills == ["Skill content"]
        # Cross-sheet fields exist but empty (no state provided)
        assert ctx.previous_outputs == {}

    def test_coexistence_with_validation_rules_in_prompt(
        self, tmp_path: Path,
    ) -> None:
        """Injection sections coexist with validation rules in the prompt."""
        from marianne.core.config import ValidationRule

        (tmp_path / "ctx.md").write_text("Background")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 1,
                "total_items": 3,
                "prelude": [
                    {"file": "ctx.md", "as": "context"},
                ],
            },
            validations=[
                {
                    "type": "file_exists",
                    "path": str(tmp_path / "output.txt"),
                    "description": "Output must exist",
                },
            ],
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        rules = [
            ValidationRule(
                type="file_exists",
                path=str(tmp_path / "output.txt"),
                description="Output must exist",
            ),
        ]
        prompt = runner.prompt_builder.build_sheet_prompt(ctx, validation_rules=rules)

        assert "Background" in prompt
        assert "## Injected Context" in prompt
        assert "Output must exist" in prompt


class TestPreludeCadenzaValidationCheck:
    """Phase 3: Tests for the V108 validation check."""

    def test_check_importable(self) -> None:
        """V108 check is importable from the checks package."""
        from marianne.validation.checks import PreludeCadenzaFileCheck

        check = PreludeCadenzaFileCheck()
        assert check.check_id == "V108"

    def test_check_in_default_checks(self) -> None:
        """V108 check is included in create_default_checks()."""
        from marianne.validation.runner import create_default_checks

        checks = create_default_checks()
        check_ids = [c.check_id for c in checks]
        assert "V108" in check_ids

    def test_runner_reports_missing_prelude_file(self, tmp_path: Path) -> None:
        """Full validation runner catches missing prelude files."""
        from marianne.validation.runner import ValidationRunner, create_default_checks

        yaml_content = (
            "name: test-job\n"
            "sheet:\n"
            "  size: 10\n"
            "  total_items: 100\n"
            "  prelude:\n"
            "    - file: /nonexistent/missing.md\n"
            "      as: context\n"
            "prompt:\n"
            "  template: 'Test'\n"
        )

        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)

        runner = ValidationRunner(create_default_checks())
        issues = runner.validate(config, config_path, yaml_content)

        v108_issues = [i for i in issues if i.check_id == "V108"]
        assert len(v108_issues) == 1
        assert "prelude" in v108_issues[0].message
