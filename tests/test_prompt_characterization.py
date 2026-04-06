"""Prompt assembly characterization tests — snapshot of the current format.

These tests capture the EXACT structure and ordering of assembled prompts.
They serve as a safety net for step 28 (wiring the baton into the conductor).
If any test here fails after the migration, the baton is producing different
prompts than the current runner — which means agent behavior will change.

The prompt assembly order (from architecture.yaml):
1. Preamble (positional identity)
2. Overture (organizational identity — not yet implemented)
3. Rendered Template (the task)
4. Skills/Tools (prelude/cadenza with category=skill/tool)
5. Injected Context (prelude/cadenza with category=context)
6. Spec Fragments (spec corpus content)
7. Failure History (lessons from previous sheets)
8. Learned Patterns (trust-scored patterns)
9. Success Requirements (validation rules as checklist)

D-003: Write prompt assembly characterization tests before step 28.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from marianne.core.config import PromptConfig, ValidationRule
from marianne.core.config.spec import SpecFragment
from marianne.prompts.preamble import build_preamble
from marianne.prompts.templating import PromptBuilder, SheetContext


# =============================================================================
# Fixtures
# =============================================================================


def _make_config(
    template: str = "Process items {{ start_item }} to {{ end_item }}",
    variables: dict[str, Any] | None = None,
    stakes: str | None = None,
    thinking_method: str | None = None,
) -> PromptConfig:
    """Create a PromptConfig for testing."""
    return PromptConfig(
        template=template,
        variables=variables or {},
        stakes=stakes,
        thinking_method=thinking_method,
    )


def _make_context(
    sheet_num: int = 1,
    total_sheets: int = 3,
    start_item: int = 1,
    end_item: int = 10,
    workspace: str = "/tmp/test-ws",
    previous_outputs: dict[int, str] | None = None,
    injected_context: list[str] | None = None,
    injected_skills: list[str] | None = None,
    injected_tools: list[str] | None = None,
) -> SheetContext:
    """Create a SheetContext for testing."""
    return SheetContext(
        sheet_num=sheet_num,
        total_sheets=total_sheets,
        start_item=start_item,
        end_item=end_item,
        workspace=Path(workspace),
        previous_outputs=previous_outputs or {},
        injected_context=injected_context or [],
        injected_skills=injected_skills or [],
        injected_tools=injected_tools or [],
    )


# =============================================================================
# Layer 1: Preamble characterization
# =============================================================================


class TestPreambleCharacterization:
    """Capture the exact preamble format for first-run and retry."""

    def test_first_run_preamble_structure(self) -> None:
        """First-run preamble has specific structure and content."""
        result = build_preamble(
            sheet_num=3, total_sheets=10,
            workspace=Path("/tmp/ws"), retry_count=0,
        )
        assert result.startswith("<mozart-preamble>")
        assert result.endswith("</mozart-preamble>")
        assert "sheet 3 of 10" in result
        assert "/tmp/ws" in result
        assert "validation requirements" in result
        assert "Write all outputs to your workspace" in result

    def test_parallel_preamble_adds_coordination_note(self) -> None:
        """Parallel execution adds a coordination warning."""
        result = build_preamble(
            sheet_num=1, total_sheets=5,
            workspace=Path("/tmp/ws"), is_parallel=True,
        )
        assert "concurrently" in result
        assert "coordinate via workspace files" in result

    def test_retry_preamble_mentions_retry_count(self) -> None:
        """Retry preamble includes attempt number."""
        result = build_preamble(
            sheet_num=1, total_sheets=5,
            workspace=Path("/tmp/ws"), retry_count=2,
        )
        assert "<mozart-preamble>" in result
        # Should mention this is a retry
        assert "retry" in result.lower() or "attempt" in result.lower()


# =============================================================================
# Layer 3: Template rendering characterization
# =============================================================================


class TestTemplateRenderingCharacterization:
    """Capture how templates are rendered with variables."""

    def test_basic_template_variables(self) -> None:
        """Standard variables are expanded in the template."""
        config = _make_config(template="Sheet {{ sheet_num }}/{{ total_sheets }}")
        builder = PromptBuilder(config)
        ctx = _make_context(sheet_num=2, total_sheets=5)
        prompt = builder.build_sheet_prompt(ctx)

        assert "Sheet 2/5" in prompt

    def test_workspace_variable_expansion(self) -> None:
        """Workspace path is available as a template variable."""
        config = _make_config(template="Work in {{ workspace }}")
        builder = PromptBuilder(config)
        ctx = _make_context(workspace="/tmp/my-ws")
        prompt = builder.build_sheet_prompt(ctx)

        assert "/tmp/my-ws" in prompt

    def test_custom_variables_merged(self) -> None:
        """Custom variables from score YAML are available in templates."""
        config = _make_config(
            template="Language: {{ language }}",
            variables={"language": "python"},
        )
        builder = PromptBuilder(config)
        ctx = _make_context()
        prompt = builder.build_sheet_prompt(ctx)

        assert "Language: python" in prompt

    def test_fan_out_variables_available(self) -> None:
        """Fan-out metadata (stage, instance, fan_count) is available."""
        config = _make_config(
            template="Stage {{ stage }}, instance {{ instance }} of {{ fan_count }}"
        )
        builder = PromptBuilder(config)
        ctx = _make_context(sheet_num=3, total_sheets=9)
        ctx.stage = 2
        ctx.instance = 1
        ctx.fan_count = 3
        prompt = builder.build_sheet_prompt(ctx)

        assert "Stage 2, instance 1 of 3" in prompt

    def test_stakes_and_thinking_in_default_prompt(self) -> None:
        """Stakes and thinking_method appear in the default prompt."""
        config = _make_config(
            template="",  # Will fall back to default
            stakes="This is critical",
            thinking_method="Think step by step",
        )
        config.template = None  # Force default prompt
        builder = PromptBuilder(config)
        ctx = _make_context()
        prompt = builder.build_sheet_prompt(ctx)

        assert "This is critical" in prompt
        assert "Think step by step" in prompt


# =============================================================================
# Layer 4-5: Injection characterization
# =============================================================================


class TestInjectionCharacterization:
    """Capture how skills, tools, and context are injected."""

    def test_skills_section_header(self) -> None:
        """Skills are injected with the correct header."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context(
            injected_skills=["You have access to the bash tool."]
        )
        prompt = builder.build_sheet_prompt(ctx)

        assert "## Injected Skills" in prompt
        assert "You have access to the bash tool." in prompt

    def test_tools_section_header(self) -> None:
        """Tools are injected with the correct header."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context(
            injected_tools=["MCP server: filesystem-server"]
        )
        prompt = builder.build_sheet_prompt(ctx)

        assert "## Injected Tools" in prompt
        assert "MCP server: filesystem-server" in prompt

    def test_context_section_header(self) -> None:
        """Context is injected with the correct header."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context(
            injected_context=["Project uses Python 3.12"]
        )
        prompt = builder.build_sheet_prompt(ctx)

        assert "## Injected Context" in prompt
        assert "Project uses Python 3.12" in prompt

    def test_skills_before_context(self) -> None:
        """Skills/tools appear BEFORE injected context in the prompt."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context(
            injected_skills=["SKILL_MARKER"],
            injected_context=["CONTEXT_MARKER"],
        )
        prompt = builder.build_sheet_prompt(ctx)

        skill_pos = prompt.index("SKILL_MARKER")
        context_pos = prompt.index("CONTEXT_MARKER")
        assert skill_pos < context_pos, "Skills should appear before context"


# =============================================================================
# Layer 6: Spec fragments characterization
# =============================================================================


class TestSpecFragmentCharacterization:
    """Capture how spec corpus fragments are injected."""

    def test_spec_fragments_have_header(self) -> None:
        """Spec fragments are injected under the correct header."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context()

        fragment = SpecFragment(
            name="conventions",
            tags=["code"],
            kind="text",
            content="Use snake_case for functions.",
        )
        prompt = builder.build_sheet_prompt(ctx, spec_fragments=[fragment])

        assert "Use snake_case for functions." in prompt

    def test_spec_after_context(self) -> None:
        """Spec fragments appear AFTER injected context."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context(
            injected_context=["CONTEXT_MARKER"],
        )

        fragment = SpecFragment(
            name="test",
            tags=["test"],
            kind="text",
            content="SPEC_MARKER",
        )
        prompt = builder.build_sheet_prompt(ctx, spec_fragments=[fragment])

        context_pos = prompt.index("CONTEXT_MARKER")
        spec_pos = prompt.index("SPEC_MARKER")
        assert context_pos < spec_pos, "Context before specs"


# =============================================================================
# Layer 7: Failure history characterization
# =============================================================================


class TestFailureHistoryCharacterization:
    """Capture how failure history is injected."""

    def test_failure_history_header(self) -> None:
        """Failure history section has the correct header."""
        from marianne.execution.validation import HistoricalFailure

        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context()

        failures = [
            HistoricalFailure(
                sheet_num=1,
                rule_type="file_exists",
                description="Missing output file",
                failure_reason="File not created",
                failure_category="missing",
            )
        ]
        prompt = builder.build_sheet_prompt(ctx, failure_history=failures)

        assert "## Lessons From Previous Sheets" in prompt
        assert "Missing output file" in prompt


# =============================================================================
# Layer 8: Learned patterns characterization
# =============================================================================


class TestLearnedPatternsCharacterization:
    """Capture how learned patterns are injected."""

    def test_patterns_header(self) -> None:
        """Patterns section has the correct header."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context()

        patterns = ["Always check file exists before reading"]
        prompt = builder.build_sheet_prompt(ctx, patterns=patterns)

        assert "## Learned Patterns" in prompt
        assert "Always check file exists before reading" in prompt
        assert "Key:" in prompt

    def test_patterns_after_failure_history(self) -> None:
        """Patterns appear AFTER failure history."""
        from marianne.execution.validation import HistoricalFailure

        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context()

        failures = [
            HistoricalFailure(
                sheet_num=1,
                rule_type="file_exists",
                description="HISTORY_MARKER",
                failure_reason="error",
                failure_category="missing",
            )
        ]
        patterns = ["PATTERN_MARKER"]
        prompt = builder.build_sheet_prompt(
            ctx, failure_history=failures, patterns=patterns
        )

        history_pos = prompt.index("HISTORY_MARKER")
        pattern_pos = prompt.index("PATTERN_MARKER")
        assert history_pos < pattern_pos, "History before patterns"


# =============================================================================
# Layer 9: Validation requirements characterization
# =============================================================================


class TestValidationRequirementsCharacterization:
    """Capture how validation rules are formatted as success requirements."""

    def test_requirements_header(self) -> None:
        """Validation requirements have the correct header."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context()

        rules = [
            ValidationRule(
                type="file_exists",
                path="{workspace}/output.md",
                description="Output report",
            )
        ]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)

        assert "## Success Requirements (Validated Automatically)" in prompt
        assert "Output report" in prompt

    def test_file_exists_format(self) -> None:
        """file_exists rules show 'Create file:' instruction."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context(workspace="/tmp/ws")

        rules = [
            ValidationRule(
                type="file_exists",
                path="{workspace}/result.txt",
                description="Result file",
            )
        ]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)

        assert "Create file:" in prompt
        assert "/tmp/ws/result.txt" in prompt

    def test_command_succeeds_format(self) -> None:
        """command_succeeds rules show the command."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context()

        rules = [
            ValidationRule(
                type="command_succeeds",
                command="pytest tests/ -x",
                description="Tests pass",
            )
        ]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)

        assert "Command must succeed:" in prompt
        assert "pytest tests/ -x" in prompt

    def test_requirements_after_patterns(self) -> None:
        """Requirements appear AFTER learned patterns (last section)."""
        config = _make_config()
        builder = PromptBuilder(config)
        ctx = _make_context()

        patterns = ["PATTERN_MARKER"]
        rules = [
            ValidationRule(
                type="file_exists",
                path="output.txt",
                description="REQUIREMENT_MARKER",
            )
        ]
        prompt = builder.build_sheet_prompt(
            ctx, patterns=patterns, validation_rules=rules
        )

        pattern_pos = prompt.index("PATTERN_MARKER")
        req_pos = prompt.index("REQUIREMENT_MARKER")
        assert pattern_pos < req_pos, "Patterns before requirements"


# =============================================================================
# Full assembly ordering characterization
# =============================================================================


class TestFullAssemblyOrder:
    """Verify the complete prompt assembly order matches the spec."""

    def test_complete_assembly_order(self) -> None:
        """All layers appear in the correct order when everything is present."""
        from marianne.execution.validation import HistoricalFailure

        config = _make_config(template="TEMPLATE_CONTENT")
        builder = PromptBuilder(config)
        ctx = _make_context(
            injected_skills=["SKILL_CONTENT"],
            injected_context=["CONTEXT_CONTENT"],
        )

        fragment = SpecFragment(
            name="test", tags=["test"], kind="text",
            content="SPEC_CONTENT",
        )
        failures = [
            HistoricalFailure(
                sheet_num=1, rule_type="file_exists",
                description="HISTORY_CONTENT",
                failure_reason="err", failure_category="missing",
            )
        ]
        patterns = ["PATTERN_CONTENT"]
        rules = [
            ValidationRule(
                type="file_exists", path="out.txt",
                description="VALIDATION_CONTENT",
            )
        ]

        prompt = builder.build_sheet_prompt(
            ctx,
            spec_fragments=[fragment],
            failure_history=failures,
            patterns=patterns,
            validation_rules=rules,
        )

        # Verify ordering: template → skills → context → specs → history → patterns → validations
        positions = {
            "template": prompt.index("TEMPLATE_CONTENT"),
            "skills": prompt.index("SKILL_CONTENT"),
            "context": prompt.index("CONTEXT_CONTENT"),
            "specs": prompt.index("SPEC_CONTENT"),
            "history": prompt.index("HISTORY_CONTENT"),
            "patterns": prompt.index("PATTERN_CONTENT"),
            "validations": prompt.index("VALIDATION_CONTENT"),
        }

        expected_order = [
            "template", "skills", "context", "specs",
            "history", "patterns", "validations",
        ]

        for i in range(len(expected_order) - 1):
            current = expected_order[i]
            next_layer = expected_order[i + 1]
            assert positions[current] < positions[next_layer], (
                f"{current} (pos {positions[current]}) should appear before "
                f"{next_layer} (pos {positions[next_layer]})"
            )


# =============================================================================
# Completion mode characterization
# =============================================================================


class TestCompletionModeCharacterization:
    """Capture the completion prompt format for partial recovery."""

    def test_completion_prompt_structure(self) -> None:
        """Completion prompt has the expected sections."""
        from marianne.execution.validation.models import ValidationResult

        config = _make_config(template="Build a thing")
        builder = PromptBuilder(config)

        passed_result = ValidationResult(
            rule=ValidationRule(type="file_exists", path="output.txt"),
            passed=True,
        )
        failed_result = ValidationResult(
            rule=ValidationRule(type="file_exists", path="report.md"),
            passed=False,
            failure_category="missing",
            failure_reason="File not found",
        )

        from marianne.prompts.templating import CompletionContext

        ctx = CompletionContext(
            sheet_num=1,
            total_sheets=3,
            passed_validations=[passed_result],
            failed_validations=[failed_result],
            completion_attempt=1,
            max_completion_attempts=5,
            original_prompt="Build a thing",
            workspace=Path("/tmp/ws"),
        )

        prompt = builder.build_completion_prompt(ctx)

        assert "COMPLETION MODE" in prompt
        assert "ALREADY COMPLETED" in prompt
        assert "INCOMPLETE ITEMS" in prompt
        assert "ORIGINAL TASK CONTEXT" in prompt
        assert "attempt 1 of 5" in prompt
        assert "Do not start over from scratch" in prompt
