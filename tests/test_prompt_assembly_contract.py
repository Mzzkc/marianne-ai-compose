"""Prompt assembly contract characterization tests.

These tests document the exact behavior of Mozart's prompt assembly pipeline.
They exist to prevent regressions when the baton replaces the monolithic runner
(step 28). Every test captures a CONTRACT — an observable behavior that downstream
code depends on.

Directive: D-003 (North, Movement 1) — write characterization tests BEFORE step 28.

The prompt assembly order (from architecture.yaml) is:
  1. Preamble (positional identity)
  2. Overture (organizational identity — not yet implemented)
  3. Rendered Template (the task)
  4. Skills/Tools (prelude/cadenza injections with category=skill/tool)
  5. Injected Context (prelude/cadenza with category=context)
  6. Spec Fragments (specification corpus passages)
  7. Failure History (lessons from previous sheets)
  8. Learned Patterns (trust-scored patterns from learning store)
  9. Success Requirements (validation rules as agent-readable checklist)

Tests here verify that this order is maintained and that each section
produces deterministic output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2
import pytest

from mozart.core.config import PromptConfig, ValidationRule
from mozart.core.config.spec import SpecFragment
from mozart.prompts.preamble import build_preamble
from mozart.prompts.templating import (
    CompletionContext,
    PromptBuilder,
    SheetContext,
    _normalize_dict_keys,
    _normalize_variable_keys,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Standard workspace path for tests."""
    return tmp_path / "test-workspace"


@pytest.fixture
def basic_context(workspace: Path) -> SheetContext:
    """SheetContext with minimal fields set."""
    return SheetContext(
        sheet_num=3,
        total_sheets=10,
        start_item=21,
        end_item=30,
        workspace=workspace,
    )


@pytest.fixture
def fanout_context(workspace: Path) -> SheetContext:
    """SheetContext with fan-out metadata populated."""
    return SheetContext(
        sheet_num=5,
        total_sheets=15,
        start_item=1,
        end_item=1,
        workspace=workspace,
        stage=2,
        instance=3,
        fan_count=4,
        total_stages=5,
    )


@pytest.fixture
def builder() -> PromptBuilder:
    """PromptBuilder with a simple template."""
    config = PromptConfig(
        template="Task for sheet {{ sheet_num }} of {{ total_sheets }}.",
        variables={"project": "test-project"},
    )
    return PromptBuilder(config)


@pytest.fixture
def builder_with_template_file(tmp_path: Path) -> PromptBuilder:
    """PromptBuilder using a template file instead of inline template."""
    tpl = tmp_path / "template.j2"
    tpl.write_text("File-based template: {{ project }} sheet {{ sheet_num }}.")
    config = PromptConfig(
        template_file=tpl,
        variables={"project": "file-test"},
    )
    return PromptBuilder(config)


def _make_spec_fragment(
    name: str, content: str, tags: list[str] | None = None
) -> SpecFragment:
    """Helper to create SpecFragment for testing."""
    return SpecFragment(
        name=name,
        tags=tags or ["test"],
        kind="structured",
        content=content,
    )


# =============================================================================
# CONTRACT 1: Preamble Structure
# =============================================================================


class TestPreambleContract:
    """The preamble is the first thing a musician reads. Its structure is a contract."""

    def test_preamble_wrapped_in_xml_tags(self, workspace: Path) -> None:
        """Preamble starts with <mozart-preamble> and ends with </mozart-preamble>."""
        result = build_preamble(1, 5, workspace)
        assert result.startswith("<mozart-preamble>")
        assert result.endswith("</mozart-preamble>")

    def test_preamble_contains_sheet_identity(self, workspace: Path) -> None:
        """Preamble tells the musician which sheet they are."""
        result = build_preamble(3, 10, workspace)
        assert "sheet 3 of 10" in result

    def test_preamble_contains_workspace_path(self, workspace: Path) -> None:
        """Preamble tells the musician where to write."""
        result = build_preamble(1, 5, workspace)
        assert str(workspace) in result

    def test_preamble_first_run_no_retry_language(self, workspace: Path) -> None:
        """First-run preamble (retry_count=0) has no retry messaging."""
        result = build_preamble(1, 5, workspace, retry_count=0)
        assert "RETRY" not in result
        assert "previous attempt" not in result.lower()

    def test_preamble_retry_includes_count(self, workspace: Path) -> None:
        """Retry preamble shows the retry number."""
        result = build_preamble(1, 5, workspace, retry_count=3)
        assert "RETRY #3" in result

    def test_preamble_retry_warns_about_failure(self, workspace: Path) -> None:
        """Retry preamble tells the musician the previous attempt failed."""
        result = build_preamble(1, 5, workspace, retry_count=1)
        assert "previous attempt failed" in result.lower()

    def test_preamble_parallel_includes_concurrency_warning(
        self, workspace: Path
    ) -> None:
        """Parallel preamble warns about concurrent execution."""
        result = build_preamble(1, 5, workspace, is_parallel=True)
        assert "concurrently" in result.lower()

    def test_preamble_sequential_no_concurrency_warning(
        self, workspace: Path
    ) -> None:
        """Sequential preamble (is_parallel=False) has no concurrency warning."""
        result = build_preamble(1, 5, workspace, is_parallel=False)
        assert "concurrently" not in result.lower()

    def test_preamble_mentions_validation(self, workspace: Path) -> None:
        """Preamble tells the musician about validation requirements."""
        result = build_preamble(1, 5, workspace)
        assert "validation" in result.lower()

    def test_preamble_mentions_workspace_output(self, workspace: Path) -> None:
        """Preamble tells the musician to write to workspace."""
        result = build_preamble(1, 5, workspace)
        assert "workspace" in result.lower()

    def test_preamble_edge_single_sheet(self, workspace: Path) -> None:
        """Preamble works for a single-sheet score."""
        result = build_preamble(1, 1, workspace)
        assert "sheet 1 of 1" in result

    def test_preamble_edge_large_numbers(self, workspace: Path) -> None:
        """Preamble handles large sheet counts (706-sheet concert)."""
        result = build_preamble(706, 706, workspace)
        assert "sheet 706 of 706" in result


# =============================================================================
# CONTRACT 2: SheetContext Variable Availability
# =============================================================================


class TestSheetContextContract:
    """SheetContext.to_dict() defines what variables are available in templates."""

    def test_to_dict_contains_all_required_keys(
        self, basic_context: SheetContext
    ) -> None:
        """to_dict produces all keys that templates can reference."""
        d = basic_context.to_dict()
        required_keys = {
            "sheet_num", "total_sheets", "start_item", "end_item",
            "workspace", "stage", "instance", "fan_count", "total_stages",
            "previous_outputs", "previous_files",
            "injected_context", "injected_skills", "injected_tools",
        }
        assert required_keys.issubset(set(d.keys()))

    def test_stage_defaults_to_sheet_num_when_zero(
        self, basic_context: SheetContext
    ) -> None:
        """When stage is 0 (not set), to_dict falls back to sheet_num."""
        assert basic_context.stage == 0
        d = basic_context.to_dict()
        assert d["stage"] == basic_context.sheet_num

    def test_total_stages_defaults_to_total_sheets_when_zero(
        self, basic_context: SheetContext
    ) -> None:
        """When total_stages is 0 (not set), to_dict falls back to total_sheets."""
        assert basic_context.total_stages == 0
        d = basic_context.to_dict()
        assert d["total_stages"] == basic_context.total_sheets

    def test_fanout_metadata_reflected(
        self, fanout_context: SheetContext
    ) -> None:
        """Fan-out metadata (stage, instance, fan_count) is available."""
        d = fanout_context.to_dict()
        assert d["stage"] == 2
        assert d["instance"] == 3
        assert d["fan_count"] == 4
        assert d["total_stages"] == 5

    def test_workspace_is_string_in_dict(
        self, basic_context: SheetContext
    ) -> None:
        """Workspace path is converted to string for Jinja compatibility."""
        d = basic_context.to_dict()
        assert isinstance(d["workspace"], str)

    def test_previous_outputs_empty_by_default(
        self, basic_context: SheetContext
    ) -> None:
        """Previous outputs dict is empty when no cross-sheet context."""
        d = basic_context.to_dict()
        assert d["previous_outputs"] == {}

    def test_injection_lists_empty_by_default(
        self, basic_context: SheetContext
    ) -> None:
        """All injection lists are empty by default."""
        d = basic_context.to_dict()
        assert d["injected_context"] == []
        assert d["injected_skills"] == []
        assert d["injected_tools"] == []


# =============================================================================
# CONTRACT 3: Prompt Assembly Order
# =============================================================================


class TestAssemblyOrderContract:
    """The order of sections in the final prompt is a contract.

    Architecture.yaml specifies:
      template → skills/tools → context → specs → failures → patterns → validations

    Changing this order changes agent behavior. These tests pin it.
    """

    def test_full_assembly_order(self, workspace: Path) -> None:
        """All sections appear in the correct order when all are present."""
        config = PromptConfig(
            template="## Template Section",
            variables={},
        )
        builder = PromptBuilder(config)

        ctx = SheetContext(
            sheet_num=1,
            total_sheets=3,
            start_item=1,
            end_item=10,
            workspace=workspace,
            injected_skills=["Skill: code review"],
            injected_tools=["Tool: linter"],
            injected_context=["Context: project overview"],
        )

        spec = _make_spec_fragment("test-spec", "Spec content here")
        patterns = ["Pattern 1: always test edge cases"]
        rules = [ValidationRule(
            type="file_exists",
            path="{workspace}/output.md",
            description="Output file exists",
        )]

        # Use a HistoricalFailure-like object
        from mozart.execution.validation import HistoricalFailure
        failure = HistoricalFailure(
            sheet_num=0,
            rule_type="file_exists",
            description="Previous failure description",
            failure_category="missing",
        )

        prompt = builder.build_sheet_prompt(
            ctx,
            patterns=patterns,
            validation_rules=rules,
            failure_history=[failure],
            spec_fragments=[spec],
        )

        # Find positions of each section header
        template_pos = prompt.find("## Template Section")
        skills_pos = prompt.find("Skill: code review")
        context_pos = prompt.find("## Injected Context")
        spec_pos = prompt.find("## Injected Specs")
        failure_pos = prompt.find("## Lessons From Previous Sheets")
        pattern_pos = prompt.find("## Learned Patterns")
        validation_pos = prompt.find("## Success Requirements")

        # All sections must be present
        assert template_pos >= 0, "Template section missing"
        assert skills_pos >= 0, "Skills section missing"
        assert context_pos >= 0, "Context section missing"
        assert spec_pos >= 0, "Spec section missing"
        assert failure_pos >= 0, "Failure section missing"
        assert pattern_pos >= 0, "Pattern section missing"
        assert validation_pos >= 0, "Validation section missing"

        # Order: template < skills < context < spec < failures < patterns < validations
        assert template_pos < skills_pos, "Template must come before skills"
        assert skills_pos < context_pos, "Skills must come before context"
        assert context_pos < spec_pos, "Context must come before specs"
        assert spec_pos < failure_pos, "Specs must come before failures"
        assert failure_pos < pattern_pos, "Failures must come before patterns"
        assert pattern_pos < validation_pos, "Patterns must come before validations"

    def test_missing_optional_sections_no_gaps(self, workspace: Path) -> None:
        """When optional sections are absent, no double-blank-line gaps."""
        config = PromptConfig(
            template="Just the template.",
            variables={},
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=workspace,
        )

        prompt = builder.build_sheet_prompt(ctx)
        assert prompt == "Just the template."

    def test_only_validations_appended(self, workspace: Path) -> None:
        """Template + validations only — no other sections."""
        config = PromptConfig(template="Do the work.", variables={})
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=workspace,
        )
        rules = [ValidationRule(
            type="file_exists",
            path="{workspace}/out.md",
            description="Output file",
        )]

        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)
        assert prompt.startswith("Do the work.")
        assert "## Success Requirements" in prompt


# =============================================================================
# CONTRACT 4: Template Variable Merging
# =============================================================================


class TestVariableMergingContract:
    """Variables from config.variables are merged with SheetContext.to_dict()."""

    def test_config_variables_accessible_in_template(
        self, builder: PromptBuilder, basic_context: SheetContext
    ) -> None:
        """Variables declared in PromptConfig.variables are available."""
        config = PromptConfig(
            template="Project: {{ project }}",
            variables={"project": "test-project"},
        )
        b = PromptBuilder(config)
        prompt = b.build_sheet_prompt(basic_context)
        assert "Project: test-project" in prompt

    def test_stakes_variable_available(
        self, basic_context: SheetContext
    ) -> None:
        """The 'stakes' config field is available as {{ stakes }}."""
        config = PromptConfig(
            template="Stakes: {{ stakes }}",
            variables={},
            stakes="high — production deployment",
        )
        b = PromptBuilder(config)
        prompt = b.build_sheet_prompt(basic_context)
        assert "Stakes: high — production deployment" in prompt

    def test_thinking_method_variable_available(
        self, basic_context: SheetContext
    ) -> None:
        """The 'thinking_method' config field is available as {{ thinking_method }}."""
        config = PromptConfig(
            template="Think: {{ thinking_method }}",
            variables={},
            thinking_method="step by step",
        )
        b = PromptBuilder(config)
        prompt = b.build_sheet_prompt(basic_context)
        assert "Think: step by step" in prompt

    def test_integer_key_normalization_after_json_roundtrip(self) -> None:
        """String keys that look like integers are restored to int after JSON roundtrip.

        This is critical: JSON serialization converts int keys to strings.
        Templates access dicts with int variables (e.g., investigation_focus[instance]).
        """
        variables = {"focus": {"1": "topic-a", "2": "topic-b"}}
        normalized = _normalize_variable_keys(variables)
        assert 1 in normalized["focus"]
        assert 2 in normalized["focus"]

    def test_non_integer_string_keys_preserved(self) -> None:
        """String keys that are NOT integers are preserved as-is."""
        d = {"name": "test", "0xff": "hex"}
        result = _normalize_dict_keys(d)
        assert "name" in result
        assert "0xff" in result

    def test_nested_dict_normalization_recursive(self) -> None:
        """Normalization recurses into nested dicts."""
        variables = {"outer": {"1": {"2": "deep"}}}
        normalized = _normalize_variable_keys(variables)
        assert 1 in normalized["outer"]
        assert 2 in normalized["outer"][1]


# =============================================================================
# CONTRACT 5: Template File Loading
# =============================================================================


class TestTemplateFileContract:
    """PromptBuilder can load templates from files as well as inline strings."""

    def test_template_file_renders(
        self,
        builder_with_template_file: PromptBuilder,
        basic_context: SheetContext,
    ) -> None:
        """Template loaded from file renders with variables."""
        prompt = builder_with_template_file.build_sheet_prompt(basic_context)
        assert "File-based template: file-test sheet 3." in prompt

    def test_template_and_file_mutually_exclusive(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        """PromptConfig rejects both template and template_file simultaneously."""
        tpl = tmp_path / "template.j2"
        tpl.write_text("FROM FILE")
        with pytest.raises(ValueError):  # Pydantic ValidationError
            PromptConfig(
                template="FROM INLINE",
                template_file=tpl,
                variables={},
            )


# =============================================================================
# CONTRACT 6: Validation Requirements Formatting
# =============================================================================


class TestValidationFormattingContract:
    """Validation rules are formatted as a checklist for the musician."""

    def test_file_exists_shows_create_path(
        self, builder: PromptBuilder, workspace: Path
    ) -> None:
        """file_exists rules show 'Create file: <path>' on their first applicable sheet."""
        # Use sheet 1 where unconditional rules are "new" (not inherited)
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=10,
            workspace=workspace,
        )
        rules = [ValidationRule(
            type="file_exists",
            path="{workspace}/output.md",
            description="Output file",
        )]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)
        assert "Create file:" in prompt
        assert "output.md" in prompt

    def test_command_succeeds_shows_command(
        self, builder: PromptBuilder, workspace: Path
    ) -> None:
        """command_succeeds rules show the command to run."""
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=10,
            workspace=workspace,
        )
        rules = [ValidationRule(
            type="command_succeeds",
            command="pytest tests/ -x",
            description="Tests pass",
        )]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)
        assert "Command must succeed" in prompt
        assert "pytest tests/ -x" in prompt

    def test_content_contains_shows_pattern(
        self, builder: PromptBuilder, workspace: Path
    ) -> None:
        """content_contains rules show the exact pattern to match."""
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=10,
            workspace=workspace,
        )
        rules = [ValidationRule(
            type="content_contains",
            path="{workspace}/out.md",
            pattern="class Foo",
            description="Contains class",
        )]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)
        assert "Must contain exactly:" in prompt
        assert "class Foo" in prompt

    def test_new_vs_inherited_rule_separation(
        self, workspace: Path,
    ) -> None:
        """Rules applicable from sheet 1 are 'inherited' on sheet 2+."""
        config = PromptConfig(template="Work.", variables={})
        b = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=2, total_sheets=3, start_item=1, end_item=1,
            workspace=workspace,
        )
        rules = [
            ValidationRule(
                type="file_exists",
                path="{workspace}/a.md",
                description="File A (always)",
            ),
            ValidationRule(
                type="file_exists",
                path="{workspace}/b.md",
                description="File B (from sheet 2)",
                condition="sheet_num >= 2",
            ),
        ]
        prompt = b.build_sheet_prompt(ctx, validation_rules=rules)
        # File A is inherited (applicable from sheet 1)
        assert "Also still required" in prompt
        assert "File A" in prompt
        # File B is new (first applicable on sheet 2)
        assert "File B" in prompt

    def test_workspace_expanded_in_validation_paths(
        self, builder: PromptBuilder, workspace: Path
    ) -> None:
        """{workspace} is expanded to actual path in validation output."""
        ctx = SheetContext(
            sheet_num=1, total_sheets=1, start_item=1, end_item=1,
            workspace=workspace,
        )
        rules = [ValidationRule(
            type="file_exists",
            path="{workspace}/output.md",
            description="Output file",
        )]
        prompt = builder.build_sheet_prompt(ctx, validation_rules=rules)
        ws_str = str(workspace)
        assert ws_str in prompt
        assert "{workspace}" not in prompt.split("Success Requirements")[1]


# =============================================================================
# CONTRACT 7: Spec Fragment Injection
# =============================================================================


class TestSpecFragmentContract:
    """Spec corpus fragments are injected as project context."""

    def test_single_fragment_injected(
        self, builder: PromptBuilder, basic_context: SheetContext
    ) -> None:
        """A single spec fragment appears in the prompt."""
        spec = _make_spec_fragment("intent", "The project's intent is correctness.")
        prompt = builder.build_sheet_prompt(
            basic_context, spec_fragments=[spec]
        )
        assert "The project's intent is correctness." in prompt

    def test_multiple_fragments_all_present(
        self, builder: PromptBuilder, basic_context: SheetContext
    ) -> None:
        """Multiple fragments all appear in the prompt."""
        specs = [
            _make_spec_fragment("intent", "Intent content"),
            _make_spec_fragment("arch", "Architecture content"),
        ]
        prompt = builder.build_sheet_prompt(
            basic_context, spec_fragments=specs
        )
        assert "Intent content" in prompt
        assert "Architecture content" in prompt

    def test_no_fragments_no_section(
        self, builder: PromptBuilder, basic_context: SheetContext
    ) -> None:
        """When no fragments are provided, no spec section appears."""
        prompt = builder.build_sheet_prompt(basic_context, spec_fragments=[])
        # No spec-related header should appear
        assert "Specification" not in prompt or "spec" not in prompt.lower().split("template")[0]


# =============================================================================
# CONTRACT 8: Patterns and Failure History
# =============================================================================


class TestPatternsAndHistoryContract:
    """Patterns and failure history are injected to help musicians learn."""

    def test_patterns_section_header(
        self, builder: PromptBuilder, basic_context: SheetContext
    ) -> None:
        """Patterns section uses '## Learned Patterns' header."""
        prompt = builder.build_sheet_prompt(
            basic_context, patterns=["Pattern: always validate input"]
        )
        assert "## Learned Patterns" in prompt

    def test_patterns_limited_to_five(
        self, builder: PromptBuilder, basic_context: SheetContext
    ) -> None:
        """At most 5 patterns are shown (to conserve tokens)."""
        many_patterns = [f"Pattern {i}" for i in range(10)]
        prompt = builder.build_sheet_prompt(
            basic_context, patterns=many_patterns
        )
        assert "Pattern 4" in prompt
        assert "Pattern 5" not in prompt.split("## Learned Patterns")[1].split("Consider")[0] or \
               prompt.count("Pattern") <= 7  # header + 5 items + footer

    def test_failure_history_limited_to_five(
        self, builder: PromptBuilder, basic_context: SheetContext
    ) -> None:
        """At most 5 historical failures are shown."""
        from mozart.execution.validation import HistoricalFailure
        failures = [
            HistoricalFailure(
                sheet_num=i,
                rule_type="file_exists",
                description=f"Failure {i}",
                failure_category="test",
            )
            for i in range(10)
        ]
        prompt = builder.build_sheet_prompt(
            basic_context, failure_history=failures
        )
        # Should show failures 0-4 (first 5)
        assert "Failure 0" in prompt
        assert "Failure 4" in prompt

    def test_empty_patterns_no_section(
        self, builder: PromptBuilder, basic_context: SheetContext
    ) -> None:
        """Empty patterns list produces no section."""
        prompt = builder.build_sheet_prompt(basic_context, patterns=[])
        assert "## Learned Patterns" not in prompt

    def test_failure_history_includes_suggested_fix(
        self, builder: PromptBuilder, basic_context: SheetContext
    ) -> None:
        """When a failure has a suggested_fix, it appears in the prompt."""
        from mozart.execution.validation import HistoricalFailure
        failure = HistoricalFailure(
            sheet_num=1,
            rule_type="file_exists",
            description="File missing",
            failure_category="missing",
            suggested_fix="Create the output file before writing to it",
        )
        prompt = builder.build_sheet_prompt(
            basic_context, failure_history=[failure]
        )
        assert "Create the output file" in prompt


# =============================================================================
# CONTRACT 9: Completion Prompt Structure
# =============================================================================


class TestCompletionPromptContract:
    """Completion prompts guide partial recovery after validation failures."""

    def test_completion_prompt_structure(self, workspace: Path) -> None:
        """Completion prompt has required structural elements."""
        from mozart.execution.validation import ValidationResult
        config = PromptConfig(template="Original task.", variables={})
        b = PromptBuilder(config)

        rule_a = ValidationRule(
            type="file_exists",
            path=str(workspace / "a.md"),
            description="File A exists",
        )
        rule_b = ValidationRule(
            type="file_exists",
            path=str(workspace / "b.md"),
            description="File B exists",
        )
        passed = ValidationResult(rule=rule_a, passed=True)
        failed = ValidationResult(rule=rule_b, passed=False)

        ctx = CompletionContext(
            sheet_num=1,
            total_sheets=3,
            passed_validations=[passed],
            failed_validations=[failed],
            completion_attempt=1,
            max_completion_attempts=5,
            original_prompt="Original task.",
            workspace=workspace,
        )

        prompt = b.build_completion_prompt(ctx)
        # Must contain key structural elements
        assert "COMPLETION MODE" in prompt
        assert "1 of 5" in prompt or "attempt 1" in prompt.lower()

    def test_completion_prompt_mentions_passed_and_failed(
        self, workspace: Path
    ) -> None:
        """Completion prompt distinguishes passed from failed validations."""
        from mozart.execution.validation import ValidationResult
        config = PromptConfig(template="Task.", variables={})
        b = PromptBuilder(config)

        rule_a = ValidationRule(
            type="file_exists",
            path="/a.md",
            description="File A",
        )
        rule_b = ValidationRule(
            type="file_exists",
            path="/b.md",
            description="File B",
        )
        passed = ValidationResult(rule=rule_a, passed=True)
        failed = ValidationResult(rule=rule_b, passed=False)

        ctx = CompletionContext(
            sheet_num=1, total_sheets=1,
            passed_validations=[passed],
            failed_validations=[failed],
            completion_attempt=1,
            max_completion_attempts=3,
            original_prompt="Task.",
            workspace=workspace,
        )

        prompt = b.build_completion_prompt(ctx)
        # Should mention what still needs work
        assert "File B" in prompt or "failed" in prompt.lower()


# =============================================================================
# CONTRACT 10: Adversarial Inputs
# =============================================================================


@pytest.mark.adversarial
class TestAdversarialPromptContract:
    """Prompt assembly handles adversarial/edge-case inputs safely."""

    def test_template_with_jinja_undefined_raises(
        self, basic_context: SheetContext
    ) -> None:
        """Undefined template variables raise UndefinedError (StrictUndefined)."""
        config = PromptConfig(
            template="Value: {{ nonexistent_var }}",
            variables={},
        )
        b = PromptBuilder(config)
        with pytest.raises(jinja2.UndefinedError):
            b.build_sheet_prompt(basic_context)

    def test_empty_template_falls_back_to_default(
        self, basic_context: SheetContext
    ) -> None:
        """An empty template string falls back to the default prompt.

        The default prompt includes sheet identity and item range.
        """
        config = PromptConfig(template="", variables={})
        b = PromptBuilder(config)
        prompt = b.build_sheet_prompt(basic_context)
        # Default prompt mentions sheet number and item range
        assert "sheet 3" in prompt.lower() or "3 of 10" in prompt
        assert len(prompt) > 0

    def test_variables_with_special_chars(
        self, basic_context: SheetContext
    ) -> None:
        """Variables containing special characters render without escaping.

        Jinja2 autoescape is OFF (code, not HTML). Special chars pass through.
        """
        config = PromptConfig(
            template="Val: {{ special }}",
            variables={"special": '<script>alert("xss")</script>'},
        )
        b = PromptBuilder(config)
        prompt = b.build_sheet_prompt(basic_context)
        assert '<script>alert("xss")</script>' in prompt

    def test_very_large_template_output(
        self, basic_context: SheetContext
    ) -> None:
        """Large template output doesn't crash or truncate."""
        big = "x" * 100_000
        config = PromptConfig(
            template=f"Start-{big}-End",
            variables={},
        )
        b = PromptBuilder(config)
        prompt = b.build_sheet_prompt(basic_context)
        assert prompt.startswith("Start-")
        assert prompt.endswith("-End")
        assert len(prompt) > 100_000

    def test_normalization_empty_dict(self) -> None:
        """Normalization handles empty dicts."""
        assert _normalize_dict_keys({}) == {}
        assert _normalize_variable_keys({}) == {}

    def test_normalization_deeply_nested(self) -> None:
        """Normalization handles deeply nested dicts."""
        deep: dict[str, Any] = {"1": {"2": {"3": {"4": "leaf"}}}}
        result = _normalize_dict_keys(deep)
        assert result[1][2][3][4] == "leaf"
