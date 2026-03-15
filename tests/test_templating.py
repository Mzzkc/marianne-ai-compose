"""Tests for prompt templating functionality.

Covers:
- SheetContext creation and serialization
- CompletionContext for partial recovery prompts
- PromptBuilder template rendering
- Validation requirements injection
- Historical failure formatting
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mozart.core.config import PromptConfig, ValidationRule
from mozart.execution.validation.models import ValidationResult
from mozart.prompts.templating import (
    CompletionContext,
    PromptBuilder,
    SheetContext,
    build_sheet_prompt_simple,
)


class TestSheetContext:
    """Tests for SheetContext dataclass."""

    def test_creation(self) -> None:
        """SheetContext should store sheet metadata correctly."""
        ctx = SheetContext(
            sheet_num=2,
            total_sheets=5,
            start_item=11,
            end_item=20,
            workspace=Path("/tmp/workspace"),
        )

        assert ctx.sheet_num == 2
        assert ctx.total_sheets == 5
        assert ctx.start_item == 11
        assert ctx.end_item == 20
        assert ctx.workspace == Path("/tmp/workspace")

    def test_to_dict(self) -> None:
        """to_dict should convert context for template rendering."""
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=3,
            start_item=1,
            end_item=10,
            workspace=Path("/test"),
        )

        result = ctx.to_dict()

        assert result["sheet_num"] == 1
        assert result["total_sheets"] == 3
        assert result["start_item"] == 1
        assert result["end_item"] == 10
        assert result["workspace"] == "/test"


class TestPromptBuilder:
    """Tests for PromptBuilder class."""

    @pytest.fixture
    def basic_config(self) -> PromptConfig:
        """Create a minimal PromptConfig."""
        return PromptConfig(
            template="Process sheet {{ sheet_num }} of {{ total_sheets }}.",
            variables={},
        )

    @pytest.fixture
    def config_with_variables(self) -> PromptConfig:
        """Create PromptConfig with custom variables."""
        return PromptConfig(
            template="Process {{ project }} sheet {{ sheet_num }}.",
            variables={"project": "Mozart"},
        )

    def test_build_sheet_context(self, basic_config: PromptConfig) -> None:
        """build_sheet_context should calculate item ranges correctly."""
        builder = PromptBuilder(basic_config)

        ctx = builder.build_sheet_context(
            sheet_num=2,
            total_sheets=3,
            sheet_size=10,
            total_items=25,
            start_item=1,
            workspace=Path("/workspace"),
        )

        # Sheet 2 starts at item 11 (1 + (2-1)*10)
        assert ctx.start_item == 11
        # Sheet 2 ends at item 20 (min(11+10-1, 25))
        assert ctx.end_item == 20
        assert ctx.sheet_num == 2
        assert ctx.total_sheets == 3

    def test_build_sheet_context_last_sheet(self, basic_config: PromptConfig) -> None:
        """Last sheet should handle partial item counts."""
        builder = PromptBuilder(basic_config)

        ctx = builder.build_sheet_context(
            sheet_num=3,
            total_sheets=3,
            sheet_size=10,
            total_items=25,
            start_item=1,
            workspace=Path("/workspace"),
        )

        # Sheet 3 starts at item 21 (1 + (3-1)*10)
        assert ctx.start_item == 21
        # Sheet 3 ends at item 25 (min(21+10-1=30, 25)=25)
        assert ctx.end_item == 25

    def test_build_sheet_prompt_basic(self, basic_config: PromptConfig) -> None:
        """build_sheet_prompt should render Jinja2 template."""
        builder = PromptBuilder(basic_config)
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=3,
            start_item=1,
            end_item=10,
            workspace=Path("/test"),
        )

        prompt = builder.build_sheet_prompt(ctx)

        assert "Process sheet 1 of 3" in prompt

    def test_build_sheet_prompt_with_variables(
        self, config_with_variables: PromptConfig
    ) -> None:
        """build_sheet_prompt should include config variables."""
        builder = PromptBuilder(config_with_variables)
        ctx = SheetContext(
            sheet_num=2,
            total_sheets=5,
            start_item=11,
            end_item=20,
            workspace=Path("/test"),
        )

        prompt = builder.build_sheet_prompt(ctx)

        assert "Process Mozart sheet 2" in prompt

    def test_build_sheet_prompt_with_patterns(self, basic_config: PromptConfig) -> None:
        """build_sheet_prompt should inject learned patterns."""
        builder = PromptBuilder(basic_config)
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=1,
            start_item=1,
            end_item=10,
            workspace=Path("/test"),
        )

        patterns = ["Always validate output", "Check file permissions"]
        prompt = builder.build_sheet_prompt(ctx, patterns=patterns)

        assert "Learned Patterns" in prompt
        assert "Always validate output" in prompt
        assert "Check file permissions" in prompt

    def test_build_sheet_prompt_with_validation_rules(
        self, basic_config: PromptConfig
    ) -> None:
        """build_sheet_prompt should inject validation requirements."""
        builder = PromptBuilder(basic_config)
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=1,
            start_item=1,
            end_item=10,
            workspace=Path("/test"),
        )

        rules = [
            ValidationRule(
                type="file_exists",
                path="{workspace}/output.txt",
                description="Output file exists",
            ),
            ValidationRule(
                type="content_contains",
                path="{workspace}/result.md",
                pattern="COMPLETE",
                description="Result contains marker",
            ),
        ]

        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)

        assert "Success Requirements" in prompt
        assert "Output file exists" in prompt
        assert "Result contains marker" in prompt
        assert "/test/output.txt" in prompt  # Variable expanded

    def test_expand_template(self, basic_config: PromptConfig) -> None:
        """_expand_template should substitute variables."""
        builder = PromptBuilder(basic_config)

        result = builder._expand_template(
            "{workspace}/sheet{sheet_num}.md",
            {"workspace": "/work", "sheet_num": 3},
        )

        assert result == "/work/sheet3.md"

    def test_expand_template_missing_variable(
        self, basic_config: PromptConfig
    ) -> None:
        """_expand_template should preserve unknown placeholders."""
        builder = PromptBuilder(basic_config)

        result = builder._expand_template(
            "{workspace}/{unknown}/file.txt",
            {"workspace": "/work"},
        )

        assert "/work/" in result
        assert "{unknown}" in result

    def test_integer_keyed_variables_after_json_roundtrip(self) -> None:
        """Integer dict keys in variables should work after JSON roundtrip.

        Regression test: model_dump(mode="json") converts integer dict keys
        to strings. When config is reconstructed from snapshot (resume path),
        Jinja2 templates using ``dict[instance]`` fail because ``instance``
        is an integer but keys are strings. The normalization in
        build_sheet_prompt should handle both YAML-loaded (int keys) and
        JSON-roundtripped (string keys) configs.
        """
        config = PromptConfig(
            template=(
                "{% set focus = focus_map[instance] %}"
                "Focus: {{ focus.name }}"
            ),
            variables={
                # Simulate JSON-roundtripped string keys (the bug scenario)
                "focus_map": {"1": {"name": "Alpha"}, "2": {"name": "Beta"}},
            },
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=2,
            start_item=1,
            end_item=1,
            workspace=Path("/test"),
            instance=1,  # Integer — this is what fan-out produces
            fan_count=2,
        )

        prompt = builder.build_sheet_prompt(ctx)
        assert "Focus: Alpha" in prompt

    def test_integer_keyed_variables_from_yaml(self) -> None:
        """Integer dict keys from YAML should work directly."""
        config = PromptConfig(
            template=(
                "{% set focus = items[instance] %}"
                "Item: {{ focus }}"
            ),
            variables={
                # YAML-loaded integer keys (normal path)
                "items": {1: "first", 2: "second"},
            },
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=2,
            start_item=1,
            end_item=1,
            workspace=Path("/test"),
            instance=2,
            fan_count=2,
        )

        prompt = builder.build_sheet_prompt(ctx)
        assert "Item: second" in prompt

    def test_default_prompt_fallback(self) -> None:
        """PromptBuilder should use default prompt when no template."""
        config = PromptConfig(variables={})  # No template
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=2,
            total_sheets=4,
            start_item=11,
            end_item=20,
            workspace=Path("/test"),
        )

        prompt = builder.build_sheet_prompt(ctx)

        assert "sheet 2 of 4" in prompt
        assert "items 11-20" in prompt


class TestCompletionPrompt:
    """Tests for completion mode prompt generation."""

    @pytest.fixture
    def builder(self) -> PromptBuilder:
        """Create a basic PromptBuilder."""
        return PromptBuilder(PromptConfig(template="Original prompt."))

    def test_build_completion_prompt(self, builder: PromptBuilder) -> None:
        """build_completion_prompt should generate recovery prompt."""
        # Create mock ValidationResults
        passed_result = MagicMock(spec=ValidationResult)
        passed_result.rule = ValidationRule(
            type="file_exists",
            path="/test/done.txt",
            description="Done file",
        )
        passed_result.expected_value = "/test/done.txt"
        passed_result.actual_value = "/test/done.txt"

        failed_result = MagicMock(spec=ValidationResult)
        failed_result.rule = ValidationRule(
            type="file_exists",
            path="/test/missing.txt",
            description="Missing file",
        )
        failed_result.expected_value = "/test/missing.txt"
        failed_result.actual_value = None
        failed_result.failure_category = None
        failed_result.failure_reason = None
        failed_result.suggested_fix = None
        failed_result.error_message = None

        ctx = CompletionContext(
            sheet_num=1,
            total_sheets=3,
            passed_validations=[passed_result],
            failed_validations=[failed_result],
            completion_attempt=1,
            max_completion_attempts=3,
            original_prompt="Original task prompt",
            workspace=Path("/test"),
        )

        prompt = builder.build_completion_prompt(ctx)

        assert "COMPLETION MODE" in prompt
        assert "Sheet 1" in prompt
        assert "attempt 1 of 3" in prompt
        assert "ALREADY COMPLETED" in prompt
        assert "INCOMPLETE ITEMS" in prompt
        assert "Done file" in prompt
        assert "Missing file" in prompt

    def test_completion_prompt_semantic_hints(self, builder: PromptBuilder) -> None:
        """build_completion_prompt should include semantic hints."""
        failed_result = MagicMock(spec=ValidationResult)
        failed_result.rule = ValidationRule(
            type="file_exists",
            path="/test/output.txt",
            description="Output file",
        )
        failed_result.expected_value = "/test/output.txt"
        failed_result.actual_value = None
        failed_result.failure_category = None
        failed_result.failure_reason = None
        failed_result.suggested_fix = None
        failed_result.error_message = None

        ctx = CompletionContext(
            sheet_num=1,
            total_sheets=1,
            passed_validations=[],
            failed_validations=[failed_result],
            completion_attempt=2,
            max_completion_attempts=3,
            original_prompt="Task",
            workspace=Path("/test"),
        )

        hints = ["Check write permissions", "Verify directory exists"]
        prompt = builder.build_completion_prompt(ctx, semantic_hints=hints)

        assert "SUGGESTED FIXES" in prompt
        assert "Check write permissions" in prompt
        assert "Verify directory exists" in prompt


class TestNewRuleDetection:
    """Tests for _is_new_rule_for_sheet (Q012 inherited vs new separation)."""

    def test_no_condition_new_on_sheet_1(self) -> None:
        """Rule with no condition is new only on sheet 1."""
        assert PromptBuilder._is_new_rule_for_sheet(None, 1) is True
        assert PromptBuilder._is_new_rule_for_sheet(None, 2) is False
        assert PromptBuilder._is_new_rule_for_sheet(None, 5) is False

    def test_gte_condition_new_at_threshold(self) -> None:
        """'sheet_num >= N' is new exactly when sheet_num == N."""
        assert PromptBuilder._is_new_rule_for_sheet("sheet_num >= 3", 2) is False
        assert PromptBuilder._is_new_rule_for_sheet("sheet_num >= 3", 3) is True
        assert PromptBuilder._is_new_rule_for_sheet("sheet_num >= 3", 4) is False

    def test_eq_condition_always_new_for_its_sheet(self) -> None:
        """'sheet_num == N' rules are always marked new (they only apply to one sheet)."""
        # Exact match rules return True because they're only relevant for one sheet,
        # so when they appear they're always "new" — the validation engine filters
        # them out for other sheets before they reach this method.
        assert PromptBuilder._is_new_rule_for_sheet("sheet_num == 5", 5) is True
        assert PromptBuilder._is_new_rule_for_sheet("sheet_num == 5", 3) is True

    def test_gt_condition_new_at_threshold_plus_one(self) -> None:
        """'sheet_num > N' is new when sheet_num == N+1."""
        assert PromptBuilder._is_new_rule_for_sheet("sheet_num > 2", 2) is False
        assert PromptBuilder._is_new_rule_for_sheet("sheet_num > 2", 3) is True
        assert PromptBuilder._is_new_rule_for_sheet("sheet_num > 2", 4) is False

    def test_unknown_condition_defaults_to_sheet_1(self) -> None:
        """Unknown conditions are treated as new on sheet 1 only."""
        assert PromptBuilder._is_new_rule_for_sheet("some_random_thing", 1) is True
        assert PromptBuilder._is_new_rule_for_sheet("some_random_thing", 2) is False


class TestInheritedNewRuleSeparation:
    """Tests for inherited vs new rule separation in prompt formatting (Q012)."""

    @pytest.fixture
    def builder(self) -> PromptBuilder:
        """Create a minimal PromptBuilder for validation rule tests."""
        return PromptBuilder(PromptConfig(template="Test."))

    def test_sheet1_all_rules_are_new(self, builder: PromptBuilder) -> None:
        """On sheet 1, all unconditional rules are new."""
        rules = [
            ValidationRule(type="file_exists", path="a.txt", description="File A"),
            ValidationRule(type="file_exists", path="b.txt", description="File B"),
        ]
        result = builder._format_validation_requirements(rules, {"sheet_num": 1})
        # No inherited section should appear
        assert "inherited" not in result

    def test_sheet2_unconditional_rules_become_inherited(self, builder: PromptBuilder) -> None:
        """On sheet 2, unconditional rules are inherited (they were new on sheet 1)."""
        rules = [
            ValidationRule(type="file_exists", path="a.txt", description="File A"),
            ValidationRule(type="file_exists", path="b.txt", description="File B"),
        ]
        result = builder._format_validation_requirements(rules, {"sheet_num": 2})
        assert "inherited" in result
        assert "File A" in result
        assert "File B" in result

    def test_mixed_new_and_inherited(self, builder: PromptBuilder) -> None:
        """Rules with 'sheet_num >= N' are new at N, inherited after."""
        rules = [
            ValidationRule(type="file_exists", path="a.txt", description="Always required"),
            ValidationRule(
                type="file_exists", path="b.txt",
                description="New at sheet 3", condition="sheet_num >= 3",
            ),
        ]
        # At sheet 3: first rule inherited, second rule new
        result = builder._format_validation_requirements(rules, {"sheet_num": 3})
        assert "1 inherited" in result
        assert "New at sheet 3" in result

    def test_inherited_count_is_accurate(self, builder: PromptBuilder) -> None:
        """Inherited count reflects the actual number of inherited rules."""
        rules = [
            ValidationRule(type="file_exists", path="a.txt", description="Rule A"),
            ValidationRule(type="file_exists", path="b.txt", description="Rule B"),
            ValidationRule(type="file_exists", path="c.txt", description="Rule C"),
        ]
        result = builder._format_validation_requirements(rules, {"sheet_num": 5})
        assert "3 inherited" in result


class TestConvenienceFunction:
    """Tests for build_sheet_prompt_simple function."""

    def test_build_sheet_prompt_simple(self) -> None:
        """build_sheet_prompt_simple should work without PromptBuilder instance."""
        config = PromptConfig(
            template="Process items {{ start_item }}-{{ end_item }}.",
        )

        prompt = build_sheet_prompt_simple(
            config=config,
            sheet_num=1,
            total_sheets=2,
            sheet_size=10,
            total_items=15,
            start_item=1,
            workspace=Path("/tmp"),
        )

        assert "Process items 1-10" in prompt


class TestInjectionInPrompt:
    """Tests for injection content (GH#53) in build_sheet_prompt output."""

    @pytest.fixture
    def builder(self) -> PromptBuilder:
        return PromptBuilder(PromptConfig(template="Do the task."))

    def test_injected_skills_tools_context_in_prompt(self, builder: PromptBuilder) -> None:
        """build_sheet_prompt with injected_context/skills/tools produces correct sections."""
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/tmp"),
        )
        ctx.injected_context = ["Project background info"]
        ctx.injected_skills = ["TDD methodology"]
        ctx.injected_tools = ["pytest, mypy"]

        prompt = builder.build_sheet_prompt(ctx)

        assert "## Injected Skills" in prompt
        assert "TDD methodology" in prompt
        assert "## Injected Tools" in prompt
        assert "pytest, mypy" in prompt
        assert "## Injected Context" in prompt
        assert "Project background info" in prompt

    def test_empty_injections_no_sections(self, builder: PromptBuilder) -> None:
        """Empty injection fields produce no injection sections."""
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/tmp"),
        )
        prompt = builder.build_sheet_prompt(ctx)

        assert "Injected Skills" not in prompt
        assert "Injected Tools" not in prompt
        assert "Injected Context" not in prompt

    def test_injection_ordering_in_prompt(self, builder: PromptBuilder) -> None:
        """Skills/tools appear before context, context before validation rules."""
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/tmp"),
        )
        ctx.injected_skills = ["Skill section"]
        ctx.injected_context = ["Context section"]

        rules = [
            ValidationRule(
                type="file_exists", path="/tmp/out.txt", description="Output",
            ),
        ]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)

        skill_pos = prompt.index("## Injected Skills")
        ctx_pos = prompt.index("## Injected Context")
        val_pos = prompt.index("## Success Requirements")
        assert skill_pos < ctx_pos < val_pos
