"""TDD test for cadenza ordering fix (P2).

Cadenzas (prelude/cadenza injections) should appear BEFORE the rendered
template for better prompt caching. Static content (skills/tools/context
from prelude/cadenza) at the front maximizes cache hits. Dynamic template
content (which changes on retries) comes after.

New order:
  1. Skills/Tools (static prelude/cadenza)
  2. Injected Context (static prelude/cadenza)
  3. Rendered Template (dynamic, changes on retries)
  4. Spec Fragments
  5. Failure History
  6. Learned Patterns
  7. Success Requirements (validations)
"""

from pathlib import Path

import pytest

from marianne.core.config import PromptConfig, ValidationRule
from marianne.core.config.spec import SpecFragment
from marianne.execution.validation import HistoricalFailure
from marianne.prompts.templating import PromptBuilder, SheetContext


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Test workspace path."""
    return tmp_path / "test-workspace"


class TestCadenzaOrderingForCaching:
    """Verify cadenzas/prelude come before template for prompt caching."""

    def test_skills_and_context_before_template(self, workspace: Path) -> None:
        """Skills/tools and context appear BEFORE rendered template."""
        config = PromptConfig(
            template="## The Template\n\nThis is the dynamic template content.",
            variables={},
        )
        builder = PromptBuilder(config)

        ctx = SheetContext(
            sheet_num=1,
            total_sheets=3,
            start_item=1,
            end_item=10,
            workspace=workspace,
            injected_skills=["Skill: use TDD"],
            injected_tools=["Tool: pytest"],
            injected_context=["Project background: building Marianne v1 beta"],
        )

        prompt = builder.build_sheet_prompt(ctx)

        # Find positions
        skills_pos = prompt.find("Skill: use TDD")
        tools_pos = prompt.find("Tool: pytest")
        context_pos = prompt.find("## Injected Context")
        template_pos = prompt.find("## The Template")

        # All sections present
        assert skills_pos >= 0, "Skills missing"
        assert tools_pos >= 0, "Tools missing"
        assert context_pos >= 0, "Context missing"
        assert template_pos >= 0, "Template missing"

        # Cadenza content comes BEFORE template
        assert skills_pos < template_pos, "Skills must come before template (for caching)"
        assert tools_pos < template_pos, "Tools must come before template (for caching)"
        assert context_pos < template_pos, "Context must come before template (for caching)"

    def test_full_assembly_order_with_caching_optimization(
        self, workspace: Path
    ) -> None:
        """All sections in cache-optimized order."""
        config = PromptConfig(
            template="## Template\n\nDynamic content here.",
            variables={},
        )
        builder = PromptBuilder(config)

        ctx = SheetContext(
            sheet_num=1,
            total_sheets=3,
            start_item=1,
            end_item=10,
            workspace=workspace,
            injected_skills=["Skill: review code"],
            injected_context=["Context: project overview"],
        )

        spec = SpecFragment(
            name="test-spec",
            content="Spec content here",
            tags=["test"],
            kind="structured",
        )
        patterns = ["Pattern 1: always test"]
        rules = [
            ValidationRule(
                type="file_exists",
                path="{workspace}/output.md",
                description="Output file exists",
            )
        ]
        failure = HistoricalFailure(
            sheet_num=0,
            rule_type="file_exists",
            description="Previous failure",
            failure_category="missing",
        )

        prompt = builder.build_sheet_prompt(
            ctx,
            patterns=patterns,
            validation_rules=rules,
            failure_history=[failure],
            spec_fragments=[spec],
        )

        # Find positions
        skills_pos = prompt.find("Skill: review code")
        context_pos = prompt.find("## Injected Context")
        template_pos = prompt.find("## Template")
        spec_pos = prompt.find("## Injected Specs")
        failure_pos = prompt.find("## Lessons From Previous Sheets")
        pattern_pos = prompt.find("## Learned Patterns")
        validation_pos = prompt.find("## Success Requirements")

        # All present
        assert skills_pos >= 0
        assert context_pos >= 0
        assert template_pos >= 0
        assert spec_pos >= 0
        assert failure_pos >= 0
        assert pattern_pos >= 0
        assert validation_pos >= 0

        # NEW ORDER (cache-optimized):
        # skills < context < template < specs < failures < patterns < validations
        assert skills_pos < context_pos, "Skills before context"
        assert context_pos < template_pos, "Context before template (caching)"
        assert template_pos < spec_pos, "Template before specs"
        assert spec_pos < failure_pos, "Specs before failures"
        assert failure_pos < pattern_pos, "Failures before patterns"
        assert pattern_pos < validation_pos, "Patterns before validations"

    def test_empty_cadenza_still_renders_template(self, workspace: Path) -> None:
        """Template renders even when no cadenza content is injected."""
        config = PromptConfig(
            template="Just the template, no cadenza.",
            variables={},
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=1,
            start_item=1,
            end_item=1,
            workspace=workspace,
            # No injected_skills, injected_tools, or injected_context
        )

        prompt = builder.build_sheet_prompt(ctx)
        assert prompt == "Just the template, no cadenza."

    def test_only_skills_no_context_or_template(self, workspace: Path) -> None:
        """Skills appear even without context or template."""
        config = PromptConfig(template="", variables={})  # Empty template
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1,
            total_sheets=1,
            start_item=1,
            end_item=1,
            workspace=workspace,
            injected_skills=["Skill: testing"],
        )

        prompt = builder.build_sheet_prompt(ctx)
        assert "Skill: testing" in prompt
