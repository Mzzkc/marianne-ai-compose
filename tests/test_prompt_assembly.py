"""Characterization tests for the prompt assembly pipeline.

Oracle identified prompt assembly as a critical coverage gap: 0.5% coverage
(39 tests for 1,003 lines). The baton migration will rewire how prompts are
built — without characterization tests, we won't know if the migration breaks
prompt assembly.

These tests capture the CURRENT behavior of PromptBuilder and preamble
construction. They are the safety net for the baton transition.

Focus areas:
1. Template rendering with variables and Jinja2 expressions
2. Prompt assembly ORDER (template → skills/tools → context → specs →
   failures → patterns → validations)
3. Variable normalization after JSON roundtrip (integer key restoration)
4. Preamble construction (first-run vs retry, parallel vs sequential)
5. Validation rule formatting (new vs inherited separation)
6. Spec fragment injection
7. Completion prompt generation
8. Default prompt fallback
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marianne.core.config import PromptConfig, ValidationRule
from marianne.core.config.spec import SpecFragment
from marianne.prompts.preamble import build_preamble
from marianne.prompts.templating import (
    PromptBuilder,
    SheetContext,
    _normalize_variable_keys,
)

# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class TestTemplateRendering:
    """PromptBuilder renders Jinja2 templates with correct context."""

    def test_simple_template_with_variables(self) -> None:
        """Variables from config are available in templates."""
        config = PromptConfig(
            template="Hello {{ name }}, sheet {{ sheet_num }} of {{ total_sheets }}",
            variables={"name": "Marianne"},
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=3, total_sheets=10, start_item=3, end_item=3,
            workspace=Path("/workspace"),
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "Hello Marianne" in prompt
        assert "sheet 3 of 10" in prompt

    def test_conditional_template(self) -> None:
        """Jinja2 conditionals work in templates."""
        config = PromptConfig(
            template=(
                "{% if sheet_num == 1 %}First sheet"
                "{% elif sheet_num == 2 %}Second sheet"
                "{% else %}Other sheet{% endif %}"
            ),
        )
        builder = PromptBuilder(config)
        ctx1 = SheetContext(
            sheet_num=1, total_sheets=5, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        ctx2 = SheetContext(
            sheet_num=2, total_sheets=5, start_item=2, end_item=2,
            workspace=Path("/ws"),
        )
        ctx3 = SheetContext(
            sheet_num=3, total_sheets=5, start_item=3, end_item=3,
            workspace=Path("/ws"),
        )
        assert "First sheet" in builder.build_sheet_prompt(ctx1)
        assert "Second sheet" in builder.build_sheet_prompt(ctx2)
        assert "Other sheet" in builder.build_sheet_prompt(ctx3)

    def test_fan_out_variables_in_template(self) -> None:
        """stage, instance, fan_count, total_stages are available."""
        config = PromptConfig(
            template=(
                "Stage {{ stage }}, instance {{ instance }} of {{ fan_count }}, "
                "total stages {{ total_stages }}"
            ),
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=4, total_sheets=12, start_item=4, end_item=4,
            workspace=Path("/ws"),
            stage=2, instance=2, fan_count=3, total_stages=4,
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "Stage 2" in prompt
        assert "instance 2 of 3" in prompt
        assert "total stages 4" in prompt

    def test_stage_defaults_to_sheet_num_when_zero(self) -> None:
        """When stage=0, template sees stage=sheet_num (the fallback)."""
        config = PromptConfig(template="Stage is {{ stage }}")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=5, total_sheets=10, start_item=5, end_item=5,
            workspace=Path("/ws"),
            stage=0,  # Unset — should fall back to sheet_num
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "Stage is 5" in prompt

    def test_default_prompt_when_no_template(self) -> None:
        """PromptBuilder produces a default prompt when no template is given."""
        config = PromptConfig(stakes="HIGH STAKES")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=2, total_sheets=5, start_item=2, end_item=2,
            workspace=Path("/ws"),
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "Processing sheet 2 of 5" in prompt
        assert "HIGH STAKES" in prompt


# ---------------------------------------------------------------------------
# Prompt assembly ORDER — the litmus test for prompt quality
# ---------------------------------------------------------------------------


class TestPromptAssemblyOrder:
    """The order of prompt sections matters for agent comprehension.

    Architecture spec defines:
    1. Preamble (positional identity — NOT in PromptBuilder)
    2. Overture (organizational identity — NOT in PromptBuilder)
    3. Rendered Template (the task)
    4. Skills/Tools (prelude/cadenza with category=skill/tool)
    5. Injected Context (prelude/cadenza with category=context)
    6. Spec Fragments
    7. Failure History
    8. Learned Patterns
    9. Validation Rules (success requirements)

    PromptBuilder handles steps 3-9. Steps 1-2 are added by the runner.
    """

    def test_assembly_order_all_sections(self) -> None:
        """Verify the relative ordering of ALL prompt sections."""
        config = PromptConfig(template="## TASK: Do the thing")
        builder = PromptBuilder(config)

        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/ws"),
            injected_skills=["SKILL: Use the Read tool"],
            injected_tools=["TOOL: Bash command"],
            injected_context=["CONTEXT: Project uses Python 3.11"],
        )

        # Provide all optional sections
        spec_fragments = [SpecFragment(
            name="conventions",
            tags=["code"],
            kind="text",
            content="SPEC: Follow PEP 8",
        )]

        @dataclass
        class FakeFailure:
            sheet_num: int = 1
            description: str = "FAILURE: Tests didn't pass"
            failure_category: str = "test"
            failure_reason: str = "assertion error"
            suggested_fix: str = "fix the test"

        patterns = ["PATTERN: Always run tests before committing"]

        validation_rules = [
            ValidationRule(
                type="file_exists",
                path="{workspace}/output.md",
                description="VALIDATION: Output file exists",
            ),
        ]

        prompt = builder.build_sheet_prompt(
            ctx,
            patterns=patterns,
            validation_rules=validation_rules,
            failure_history=[FakeFailure()],  # type: ignore[list-item]
            spec_fragments=spec_fragments,
        )

        # Verify relative ordering by finding positions
        task_pos = prompt.find("TASK: Do the thing")
        skill_pos = prompt.find("SKILL: Use the Read tool")
        context_pos = prompt.find("CONTEXT: Project uses Python 3.11")
        spec_pos = prompt.find("SPEC: Follow PEP 8")
        failure_pos = prompt.find("FAILURE: Tests didn't pass")
        pattern_pos = prompt.find("PATTERN: Always run tests")
        validation_pos = prompt.find("VALIDATION: Output file exists")

        # All sections must be present
        assert task_pos >= 0, "Task section missing"
        assert skill_pos >= 0, "Skills section missing"
        assert context_pos >= 0, "Context section missing"
        assert spec_pos >= 0, "Spec section missing"
        assert failure_pos >= 0, "Failure history section missing"
        assert pattern_pos >= 0, "Patterns section missing"
        assert validation_pos >= 0, "Validation section missing"

        # Verify ORDER: template < skills < context < specs < failures < patterns < validations
        assert task_pos < skill_pos, "Task must come before skills"
        assert skill_pos < context_pos, "Skills must come before context"
        assert context_pos < spec_pos, "Context must come before specs"
        assert spec_pos < failure_pos, "Specs must come before failure history"
        assert failure_pos < pattern_pos, "Failures must come before patterns"
        assert pattern_pos < validation_pos, "Patterns must come before validations"

    def test_missing_sections_dont_leave_gaps(self) -> None:
        """When optional sections are absent, no empty sections appear."""
        config = PromptConfig(template="Just the task")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "Just the task" in prompt
        assert "## Injected Specs" not in prompt
        assert "## Learned Patterns" not in prompt
        assert "## Lessons From Previous Sheets" not in prompt
        assert "## Success Requirements" not in prompt


# ---------------------------------------------------------------------------
# Variable normalization after JSON roundtrip
# ---------------------------------------------------------------------------


class TestVariableNormalization:
    """Integer keys survive JSON serialization/deserialization.

    JSON converts int keys to strings. Jinja2 templates use integer
    variables (e.g., dict[instance] where instance is int). Without
    normalization, resumed jobs break silently.
    """

    def test_string_integer_keys_normalized(self) -> None:
        """String keys that look like integers are converted back to int."""
        variables = {
            "investigation_focus": {"1": "auth", "2": "billing"},
            "normal_key": "stays as string",
        }
        result = _normalize_variable_keys(variables)
        assert 1 in result["investigation_focus"]
        assert 2 in result["investigation_focus"]
        assert result["investigation_focus"][1] == "auth"

    def test_non_integer_string_keys_preserved(self) -> None:
        """String keys that are NOT integers stay as strings."""
        variables = {
            "labels": {"auth": "security", "billing": "payment"},
        }
        result = _normalize_variable_keys(variables)
        assert "auth" in result["labels"]
        assert "billing" in result["labels"]

    def test_nested_normalization(self) -> None:
        """Deeply nested dicts also get normalized."""
        variables = {
            "outer": {"1": {"2": "deep_value"}},
        }
        result = _normalize_variable_keys(variables)
        assert 1 in result["outer"]
        assert 2 in result["outer"][1]
        assert result["outer"][1][2] == "deep_value"

    def test_template_uses_normalized_keys(self) -> None:
        """Template rendering with normalized integer keys works."""
        config = PromptConfig(
            template="Focus: {{ investigation_focus[instance] }}",
            variables={
                "investigation_focus": {"1": "auth", "2": "billing"},
            },
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=1,
            workspace=Path("/ws"),
            instance=1,
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "Focus: auth" in prompt


# ---------------------------------------------------------------------------
# Preamble construction
# ---------------------------------------------------------------------------


class TestPreambleConstruction:
    """Preambles frame the agent's identity and context."""

    def test_first_run_preamble_structure(self) -> None:
        """First-run preamble includes sheet position and workspace."""
        preamble = build_preamble(
            sheet_num=3, total_sheets=10,
            workspace=Path("/workspaces/my-job"),
        )
        assert "<marianne-preamble>" in preamble
        assert "</marianne-preamble>" in preamble
        assert "sheet 3 of 10" in preamble
        assert "/workspaces/my-job" in preamble
        assert "RETRY" not in preamble

    def test_retry_preamble_includes_retry_count(self) -> None:
        """Retry preamble clearly indicates this is a retry."""
        preamble = build_preamble(
            sheet_num=3, total_sheets=10,
            workspace=Path("/ws"),
            retry_count=2,
        )
        assert "RETRY #2" in preamble
        assert "previous attempt failed" in preamble

    def test_parallel_preamble_warns_about_concurrency(self) -> None:
        """Parallel execution preamble warns about concurrent access."""
        preamble = build_preamble(
            sheet_num=1, total_sheets=5,
            workspace=Path("/ws"),
            is_parallel=True,
        )
        assert "concurrently" in preamble

    def test_sequential_preamble_no_concurrency_warning(self) -> None:
        """Sequential execution preamble doesn't mention concurrency."""
        preamble = build_preamble(
            sheet_num=1, total_sheets=5,
            workspace=Path("/ws"),
            is_parallel=False,
        )
        assert "concurrently" not in preamble


# ---------------------------------------------------------------------------
# Validation rule formatting
# ---------------------------------------------------------------------------


class TestValidationFormatting:
    """Validation rules are formatted as agent-readable success criteria."""

    def test_new_vs_inherited_separation(self) -> None:
        """Rules are separated into new (for this sheet) and inherited."""
        config = PromptConfig(template="Task")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=3, total_sheets=5, start_item=3, end_item=3,
            workspace=Path("/ws"),
        )

        rules = [
            ValidationRule(
                type="file_exists",
                path="{workspace}/early.md",
                description="Early file",
                condition=None,  # No condition → inherited from sheet 1
            ),
            ValidationRule(
                type="file_exists",
                path="{workspace}/late.md",
                description="Late file",
                condition="sheet_num >= 3",  # New for sheet 3
            ),
        ]

        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)
        assert "Late file" in prompt
        assert "inherited" in prompt.lower()

    def test_file_exists_formatting(self) -> None:
        """file_exists rules show the expanded path."""
        config = PromptConfig(template="Task")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/my/ws"),
        )
        rules = [
            ValidationRule(
                type="file_exists",
                path="{workspace}/output.md",
                description="Output file",
            ),
        ]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)
        assert "/my/ws/output.md" in prompt

    def test_command_succeeds_formatting(self) -> None:
        """command_succeeds rules show the command."""
        config = PromptConfig(template="Task")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        rules = [
            ValidationRule(
                type="command_succeeds",
                command="pytest tests/ -x",
                description="Tests pass",
            ),
        ]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)
        assert "pytest tests/ -x" in prompt


# ---------------------------------------------------------------------------
# Spec fragment injection
# ---------------------------------------------------------------------------


class TestSpecFragmentInjection:
    """Spec corpus fragments are injected into prompts."""

    def test_fragments_injected(self) -> None:
        """Spec fragments appear in the prompt."""
        config = PromptConfig(template="Do work")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        fragments = [
            SpecFragment(
                name="conventions",
                tags=["code"],
                kind="text",
                content="Use Pydantic v2 everywhere",
            ),
        ]
        prompt = builder.build_sheet_prompt(ctx, spec_fragments=fragments)
        assert "Use Pydantic v2 everywhere" in prompt

    def test_empty_fragments_no_section(self) -> None:
        """Empty fragment list doesn't create a section header."""
        config = PromptConfig(template="Do work")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        prompt = builder.build_sheet_prompt(ctx, spec_fragments=[])
        assert "## Injected Specs" not in prompt

    def test_multiple_fragments(self) -> None:
        """Multiple fragments are all included."""
        config = PromptConfig(template="Do work")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        fragments = [
            SpecFragment(name="a", tags=[], kind="text", content="Fragment A"),
            SpecFragment(name="b", tags=[], kind="text", content="Fragment B"),
        ]
        prompt = builder.build_sheet_prompt(ctx, spec_fragments=fragments)
        assert "Fragment A" in prompt
        assert "Fragment B" in prompt


# ---------------------------------------------------------------------------
# Cross-sheet context (D-003 gap: previously untested)
# ---------------------------------------------------------------------------


class TestCrossSheetContext:
    """Cross-sheet context allows sheets to reference previous outputs."""

    def test_previous_outputs_in_template(self) -> None:
        """previous_outputs dict is accessible in Jinja2 templates."""
        config = PromptConfig(
            template="Previous result: {{ previous_outputs[1] }}",
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=2, total_sheets=3, start_item=2, end_item=2,
            workspace=Path("/ws"),
            previous_outputs={1: "Sheet 1 generated auth module"},
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "Sheet 1 generated auth module" in prompt

    def test_previous_files_in_template(self) -> None:
        """previous_files dict is accessible in Jinja2 templates."""
        config = PromptConfig(
            template="Architecture: {{ previous_files['arch.md'] }}",
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=2, total_sheets=3, start_item=2, end_item=2,
            workspace=Path("/ws"),
            previous_files={"arch.md": "# Microservices architecture"},
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "# Microservices architecture" in prompt

    def test_multiple_previous_outputs(self) -> None:
        """Multiple previous outputs accessible by sheet number."""
        config = PromptConfig(
            template="S1={{ previous_outputs[1] }}, S2={{ previous_outputs[2] }}",
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=3, total_sheets=5, start_item=3, end_item=3,
            workspace=Path("/ws"),
            previous_outputs={1: "plan", 2: "code"},
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "S1=plan" in prompt
        assert "S2=code" in prompt

    def test_empty_previous_outputs_no_crash(self) -> None:
        """Empty previous_outputs doesn't break template rendering."""
        config = PromptConfig(
            template="{% if previous_outputs %}Has context{% else %}No context{% endif %}",
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "No context" in prompt


# ---------------------------------------------------------------------------
# Template file rendering (D-003 gap)
# ---------------------------------------------------------------------------


class TestTemplateFileRendering:
    """Template files are loaded from disk and rendered with variables."""

    def test_template_file_rendered_with_variables(self, tmp_path: Path) -> None:
        """Template file is loaded, parsed, and rendered with context."""
        tpl = tmp_path / "prompt.j2"
        tpl.write_text(
            "Sheet {{ sheet_num }}: process items {{ start_item }}-{{ end_item }}"
        )
        config = PromptConfig(template_file=tpl)
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=3, total_sheets=10, start_item=21, end_item=30,
            workspace=Path("/ws"),
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "Sheet 3: process items 21-30" in prompt

    def test_template_file_with_jinja_conditionals(self, tmp_path: Path) -> None:
        """Jinja2 conditionals work in template files."""
        tpl = tmp_path / "conditional.j2"
        tpl.write_text(
            "{% if stage == 1 %}Planning{% else %}Building{% endif %}"
        )
        config = PromptConfig(template_file=tpl)
        builder = PromptBuilder(config)

        ctx1 = SheetContext(
            sheet_num=1, total_sheets=5, start_item=1, end_item=1,
            workspace=Path("/ws"), stage=1,
        )
        ctx2 = SheetContext(
            sheet_num=2, total_sheets=5, start_item=2, end_item=2,
            workspace=Path("/ws"), stage=2,
        )
        assert "Planning" in builder.build_sheet_prompt(ctx1)
        assert "Building" in builder.build_sheet_prompt(ctx2)


# ---------------------------------------------------------------------------
# Adversarial edge cases (D-003 gap)
# ---------------------------------------------------------------------------


class TestPromptAdversarial:
    """Edge cases and adversarial inputs for prompt assembly."""

    def test_empty_template_produces_output(self) -> None:
        """An empty template string still produces some output."""
        config = PromptConfig(template="")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        # Empty template renders to empty string — but injections can add content
        rules = [
            ValidationRule(
                type="file_exists", path="/out.txt",
                description="Output exists",
            ),
        ]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)
        assert "Output exists" in prompt

    def test_special_chars_in_workspace_path(self) -> None:
        """Workspace paths with special characters render correctly."""
        config = PromptConfig(
            template="Working in {{ workspace }}",
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/tmp/user's workspace (v2)"),
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "user's workspace (v2)" in prompt

    def test_very_large_previous_output(self) -> None:
        """Large previous output doesn't crash template rendering."""
        config = PromptConfig(
            template="Previous: {{ previous_outputs[1][:50] }}...",
        )
        builder = PromptBuilder(config)
        large_output = "x" * 100_000
        ctx = SheetContext(
            sheet_num=2, total_sheets=2, start_item=1, end_item=1,
            workspace=Path("/ws"),
            previous_outputs={1: large_output},
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "x" * 50 in prompt

    def test_unicode_in_variables(self) -> None:
        """Unicode characters in variables render correctly."""
        config = PromptConfig(
            template="Project: {{ project_name }}",
            variables={"project_name": "モーツァルト AI 作曲"},
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "モーツァルト AI 作曲" in prompt

    def test_pattern_with_jinja_syntax_doesnt_crash(self) -> None:
        """Patterns containing {{ }} don't break Jinja2 rendering."""
        config = PromptConfig(template="Task")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        # Pattern text that happens to look like Jinja2
        patterns = ["Use {{ variable }} syntax in templates"]
        prompt = builder.build_sheet_prompt(ctx, patterns=patterns)
        # Pattern should be in the prompt as-is (not rendered as Jinja2)
        assert "variable" in prompt


# ---------------------------------------------------------------------------
# Historical failure injection (D-003 gap: only tested in order test)
# ---------------------------------------------------------------------------


class TestHistoricalFailureInjection:
    """Historical failures provide lessons from previous sheets."""

    def test_failure_history_section_present(self) -> None:
        """Failure history creates a dedicated section."""
        config = PromptConfig(template="Task")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=3, total_sheets=5, start_item=3, end_item=3,
            workspace=Path("/ws"),
        )

        @dataclass
        class FakeFailure:
            sheet_num: int = 1
            description: str = "Tests failed"
            failure_category: str = "test"
            failure_reason: str = "Import error"
            suggested_fix: str = "Fix the import"

        prompt = builder.build_sheet_prompt(
            ctx, failure_history=[FakeFailure()],  # type: ignore[list-item]
        )
        assert "Previous Sheets" in prompt or "failure" in prompt.lower()

    def test_no_failures_no_section(self) -> None:
        """Without failures, no failure section appears."""
        config = PromptConfig(template="Task")
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=Path("/ws"),
        )
        prompt = builder.build_sheet_prompt(ctx)
        assert "Lessons" not in prompt


# ---------------------------------------------------------------------------
# Completion prompt
# ---------------------------------------------------------------------------


class TestCompletionPrompt:
    """Completion prompts guide agents to finish partial work."""

    def test_completion_prompt_structure(self) -> None:
        """Completion prompt includes attempt info and instructions."""
        config = PromptConfig(template="Original task")
        builder = PromptBuilder(config)

        @dataclass
        class FakeValidationResult:
            rule: ValidationRule
            passed: bool = True
            expected_value: str | None = None
            actual_value: str | None = None
            error_message: str | None = None
            failure_category: str | None = None
            failure_reason: str | None = None
            suggested_fix: str | None = None

        from marianne.prompts.templating import CompletionContext

        passed = [FakeValidationResult(
            rule=ValidationRule(
                type="file_exists", path="/ws/done.md",
                description="Done file",
            ),
            expected_value="/ws/done.md",
        )]
        failed = [FakeValidationResult(
            rule=ValidationRule(
                type="file_exists", path="/ws/missing.md",
                description="Missing file",
            ),
            passed=False,
            expected_value="/ws/missing.md",
        )]

        ctx = CompletionContext(
            sheet_num=3,
            total_sheets=5,
            passed_validations=passed,  # type: ignore[arg-type]
            failed_validations=failed,  # type: ignore[arg-type]
            completion_attempt=2,
            max_completion_attempts=5,
            original_prompt="Original task prompt",
            workspace=Path("/ws"),
        )
        prompt = builder.build_completion_prompt(ctx)

        assert "COMPLETION MODE" in prompt
        assert "Sheet 3" in prompt
        assert "attempt 2 of 5" in prompt
        assert "ALREADY COMPLETED" in prompt
        assert "INCOMPLETE ITEMS" in prompt
        assert "Done file" in prompt
        assert "Missing file" in prompt
        assert "Original task prompt" in prompt
