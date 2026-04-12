"""Prompt templating for Marianne jobs.

Handles building sheet prompts from templates and generating
auto-completion prompts for partial sheet recovery.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2

from marianne.core.config import PromptConfig, ValidationRule
from marianne.core.config.spec import SpecFragment
from marianne.core.constants import SHEET_NUM_KEY

if TYPE_CHECKING:
    from marianne.execution.validation import HistoricalFailure, ValidationResult


def _normalize_variable_keys(variables: dict[str, Any]) -> dict[str, Any]:
    """Normalize nested dict keys after JSON roundtrip.

    JSON serialization (via ``model_dump(mode="json")``) converts integer
    dict keys to strings because JSON only supports string keys. When
    config is reconstructed from a snapshot (resume path), nested dicts
    within ``variables`` have string keys like ``{"1": ..., "2": ...}``
    instead of the original integer keys ``{1: ..., 2: ...}``.

    Jinja2 templates access these dicts with integer variables (e.g.
    ``investigation_focus[instance]``), which fails when keys are strings.

    This function recursively converts string keys that look like integers
    back to integers, restoring the original YAML-loaded behavior.
    """
    result: dict[str, Any] = {}
    for key, value in variables.items():
        if isinstance(value, dict):
            result[key] = _normalize_dict_keys(value)
        else:
            result[key] = value
    return result


def _normalize_dict_keys(d: dict[Any, Any]) -> dict[Any, Any]:
    """Recursively normalize dict keys: string-encoded integers → int."""
    result: dict[Any, Any] = {}
    for key, value in d.items():
        # Convert string keys that represent integers back to int
        normalized_key = key
        if isinstance(key, str):
            try:
                normalized_key = int(key)
            except (ValueError, OverflowError):
                pass
        # Recurse into nested dicts
        if isinstance(value, dict):
            result[normalized_key] = _normalize_dict_keys(value)
        else:
            result[normalized_key] = value
    return result


@dataclass
class SheetContext:
    """Context for building a sheet prompt.

    Includes both sheet-level metadata and optional cross-sheet context
    from previous sheet executions. Fan-out metadata (stage, instance,
    fan_count, total_stages) is populated when fan_out is configured.
    """

    sheet_num: int
    total_sheets: int
    start_item: int
    end_item: int
    workspace: Path
    # Fan-out metadata (populated by runner when fan_out is configured)
    stage: int = 0
    """Logical stage number (1-indexed). 0 = not set, falls back to sheet_num."""
    instance: int = 1
    """Instance within fan-out group (1-indexed). Default 1."""
    fan_count: int = 1
    """Total instances in this stage's fan-out group. Default 1."""
    total_stages: int = 0
    """Original stage count before expansion. 0 = not set, falls back to total_sheets."""
    # Cross-sheet context (populated by runner when CrossSheetConfig is enabled)
    previous_outputs: dict[int, str] = field(default_factory=dict)
    """Stdout outputs from previous sheets. Keys are sheet numbers (1-indexed)."""
    previous_files: dict[str, str] = field(default_factory=dict)
    """File contents captured between sheets. Keys are file paths."""
    skipped_upstream: list[int] = field(default_factory=list)
    """Sheet numbers of upstream sheets that were skipped (#120).
    Allows prompts to handle incomplete fan-in data explicitly."""
    # Prelude & Cadenza injection fields (GH#53)
    injected_context: list[str] = field(default_factory=list)
    """Resolved content from 'context' category injections."""
    injected_skills: list[str] = field(default_factory=list)
    """Resolved content from 'skill' category injections."""
    injected_tools: list[str] = field(default_factory=list)
    """Resolved content from 'tool' category injections."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for template rendering.

        Provides both old terminology (stage, instance, fan_count, total_stages)
        and new terminology (movement, voice, voice_count, total_movements) so
        templates can use either vocabulary. Matches Sheet.template_variables().
        """
        effective_stage = self.stage if self.stage > 0 else self.sheet_num
        effective_total = self.total_stages if self.total_stages > 0 else self.total_sheets
        return {
            SHEET_NUM_KEY: self.sheet_num,
            "total_sheets": self.total_sheets,
            "start_item": self.start_item,
            "end_item": self.end_item,
            "workspace": str(self.workspace),
            # Old terminology (backward compat)
            "stage": effective_stage,
            "instance": self.instance,
            "fan_count": self.fan_count,
            "total_stages": effective_total,
            # New terminology aliases (movement/voice vocabulary)
            "movement": effective_stage,
            "voice": self.instance,
            "voice_count": self.fan_count,
            "total_movements": effective_total,
            # Cross-sheet context
            "previous_outputs": self.previous_outputs,
            "previous_files": self.previous_files,
            "skipped_upstream": self.skipped_upstream,
            "injected_context": self.injected_context,
            "injected_skills": self.injected_skills,
            "injected_tools": self.injected_tools,
        }


@dataclass
class CompletionContext:
    """Context for generating completion prompts.

    Uses ValidationResult objects (not just ValidationRule) to ensure
    that file paths are properly expanded with actual values like workspace
    and sheet_num, rather than showing template placeholders.
    """

    sheet_num: int
    total_sheets: int
    passed_validations: list["ValidationResult"]  # Changed from ValidationRule
    failed_validations: list["ValidationResult"]  # Changed from ValidationRule
    completion_attempt: int
    max_completion_attempts: int
    original_prompt: str
    workspace: Path


class PromptBuilder:
    """Builds prompts including completion prompts for partial recovery.

    Handles Jinja2 template rendering and auto-generation of completion
    prompts when a sheet partially completes.
    """

    def __init__(
        self,
        config: PromptConfig,
        jinja_env: jinja2.Environment | None = None,
    ) -> None:
        """Initialize prompt builder.

        Args:
            config: Prompt configuration from job config.
            jinja_env: Optional custom Jinja2 environment.
        """
        self.config = config
        self.env = jinja_env or jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )

    def build_sheet_context(
        self,
        sheet_num: int,
        total_sheets: int,
        sheet_size: int,
        total_items: int,
        start_item: int,
        workspace: Path,
    ) -> SheetContext:
        """Build sheet context from job parameters.

        Args:
            sheet_num: Current sheet number (1-indexed).
            total_sheets: Total number of sheets.
            sheet_size: Items per sheet.
            total_items: Total items to process.
            start_item: First item number (1-indexed).
            workspace: Workspace directory.

        Returns:
            SheetContext with calculated item range.
        """
        sheet_start = (sheet_num - 1) * sheet_size + start_item
        sheet_end = min(sheet_start + sheet_size - 1, total_items)

        return SheetContext(
            sheet_num=sheet_num,
            total_sheets=total_sheets,
            start_item=sheet_start,
            end_item=sheet_end,
            workspace=workspace,
        )

    def build_sheet_prompt(
        self,
        context: SheetContext,
        patterns: list[str] | None = None,
        validation_rules: list[ValidationRule] | None = None,
        failure_history: list["HistoricalFailure"] | None = None,
        spec_fragments: list[SpecFragment] | None = None,
    ) -> str:
        """Build the standard sheet prompt from config.

        Args:
            context: Sheet context with item range and workspace.
            patterns: Optional list of learned pattern descriptions to inject.
            validation_rules: Optional list of validation rules to inject as
                requirements. These are the rules that will be checked after
                sheet execution - injecting them helps Claude understand
                exactly what success looks like.
            failure_history: Optional list of historical failures from previous
                sheets. Injected to help Claude learn from past mistakes and
                avoid repeating the same errors (Evolution v6).
            spec_fragments: Optional list of spec corpus fragments to inject
                as project context. Fragments are inserted after injected
                context and before failure history per the prompt assembly
                order (Phase 1: Spec Corpus Pipeline).

        Returns:
            Rendered prompt string.
        """
        template_context = context.to_dict()

        # Merge config variables.
        # Normalize nested dict keys: JSON roundtrip (model_dump → model_validate)
        # converts integer dict keys to strings. Jinja2 templates use
        # ``dict[instance]`` where ``instance`` is an integer, so string-keyed
        # dicts cause UndefinedError. Restore integer keys where possible.
        template_context.update(_normalize_variable_keys(self.config.variables))

        # Add stakes and thinking method
        template_context["stakes"] = self.config.stakes or ""
        template_context["thinking_method"] = self.config.thinking_method or ""

        # PROMPT ASSEMBLY ORDER (optimized for prompt caching):
        # 1. Static prelude/cadenza content first (better cache hits)
        # 2. Dynamic template content second (changes on retries)
        # 3. Additional context/patterns/validations last

        # Inject skills/tools FIRST (static content from prelude/cadenza)
        # These are typically unchanged across retries, so placing them
        # at the front maximizes prompt cache utility.
        skills_tools_section = self._format_injection_section(
            context.injected_skills, context.injected_tools
        )
        prompt = skills_tools_section if skills_tools_section else ""

        # Inject context SECOND (static content from prelude/cadenza)
        if context.injected_context:
            context_parts = "\n\n".join(context.injected_context)
            context_section = f"## Injected Context\n\n{context_parts}"
            if prompt:
                prompt = f"{prompt}\n\n{context_section}"
            else:
                prompt = context_section

        # Render template THIRD (dynamic content that changes on retries)
        # Template contains per-attempt variables, sheet-specific logic,
        # and retry-specific state. By placing this after static prelude/cadenza,
        # we maximize the cacheable prefix.
        if self.config.template:
            template = self.env.from_string(self.config.template)
            template_body = template.render(**template_context)
        elif self.config.template_file and self.config.template_file.exists():
            template_content = self.config.template_file.read_text()
            template = self.env.from_string(template_content)
            template_body = template.render(**template_context)
        else:
            template_body = self._build_default_prompt(context)

        if prompt:
            prompt = f"{prompt}\n\n{template_body}"
        else:
            prompt = template_body

        # Inject spec corpus fragments (Phase 1: Spec Corpus Pipeline)
        # Placed after injected context, before failure history — aligns with
        # architecture.yaml's prompt assembly order (layer 5→6 boundary).
        if spec_fragments:
            spec_section = PromptBuilder._format_spec_fragments(spec_fragments)
            prompt = f"{prompt}\n\n{spec_section}"

        # Inject failure history first (lessons learned from past)
        if failure_history:
            history_section = self._format_historical_failures(failure_history)
            prompt = f"{prompt}\n\n{history_section}"

        # Inject learned patterns if available
        if patterns:
            pattern_section = self._format_patterns_section(patterns)
            prompt = f"{prompt}\n\n{pattern_section}"

        # Inject validation requirements if available
        if validation_rules:
            validation_section = self._format_validation_requirements(
                validation_rules, template_context
            )
            prompt = f"{prompt}\n\n{validation_section}"

        return prompt

    def _format_historical_failures(
        self, failures: list["HistoricalFailure"]
    ) -> str:
        """Format historical validation failures for prompt injection.

        Creates a "lessons learned" section from past validation failures
        to help Claude avoid repeating the same mistakes.

        Args:
            failures: List of HistoricalFailure objects from previous sheets.

        Returns:
            Formatted markdown section for prompt injection.
        """
        if not failures:
            return ""

        lines = ["## Lessons From Previous Sheets", ""]
        lines.append(
            "Previous sheets encountered these validation failures. "
            "Avoid repeating these mistakes:"
        )
        lines.append("")

        for i, failure in enumerate(failures[:5], 1):
            # Build failure summary
            category_tag = (
                f"[{failure.failure_category.upper()}]"
                if failure.failure_category
                else "[FAILED]"
            )

            lines.append(f"{i}. **Sheet {failure.sheet_num}** {category_tag}")
            lines.append(f"   - {failure.description}")

            if failure.failure_reason:
                lines.append(f"   - Issue: {failure.failure_reason}")

            if failure.suggested_fix:
                lines.append(f"   - Fix: {failure.suggested_fix}")

            lines.append("")

        lines.append(
            "**Learn from these failures** - ensure similar issues don't recur."
        )
        lines.append("")

        return "\n".join(lines)

    def _format_patterns_section(self, patterns: list[str]) -> str:
        """Format learned patterns as a prompt section.

        Args:
            patterns: List of pattern description strings.

        Returns:
            Formatted markdown section for prompt injection.
        """
        if not patterns:
            return ""

        lines = ["## Learned Patterns", ""]
        lines.append("Based on previous executions, here are relevant insights:")
        lines.append("")

        for i, pattern in enumerate(patterns[:5], 1):
            lines.append(f"{i}. {pattern}")

        lines.append("")
        lines.append("Consider these patterns when executing this sheet.")
        lines.append("Key: ✓ = high effectiveness, ○ = moderate, ⚠ = low/untested")
        lines.append("")

        return "\n".join(lines)

    def _format_validation_requirements(
        self,
        rules: list[ValidationRule],
        template_context: dict[str, Any],
    ) -> str:
        """Format validation rules as success requirements for prompt injection.

        Converts validation rules into clear requirements that help Claude
        understand exactly what success looks like. Expands template variables
        in paths/patterns to show actual expected values.

        Separates rules into "new" (first applicable for this sheet) and
        "inherited" (also applied to earlier sheets) to reduce token waste
        and focus attention on sheet-specific requirements.

        Args:
            rules: List of validation rules to format.
            template_context: Template variables for path expansion.

        Returns:
            Formatted markdown section for prompt injection.
        """
        if not rules:
            return ""

        sheet_num = template_context.get(SHEET_NUM_KEY, 1)

        # Separate new vs inherited rules: a rule is "new" if the previous
        # sheet would NOT have matched its condition
        new_rules: list[ValidationRule] = []
        inherited_rules: list[ValidationRule] = []
        for rule in rules:
            if self._is_new_rule_for_sheet(rule.condition, sheet_num):
                new_rules.append(rule)
            else:
                inherited_rules.append(rule)

        lines = ["---", "## Success Requirements (Validated Automatically)", ""]
        lines.append(
            "Your output will be validated against these requirements. "
            "All must pass for success:"
        )
        lines.append("")

        for i, rule in enumerate(new_rules, 1):
            self._format_single_rule(rule, i, lines, template_context)

        # Summarize inherited rules briefly to save tokens
        if inherited_rules:
            descs = [r.description or r.type for r in inherited_rules]
            lines.append(
                f"**Also still required** ({len(inherited_rules)} inherited): "
                + "; ".join(descs)
            )
            lines.append("")

        lines.append(
            "**Important**: These validations run automatically. Ensure your output "
            "matches exactly - partial matches or variations may fail validation."
        )

        return "\n".join(lines)

    @staticmethod
    def _is_new_rule_for_sheet(condition: str | None, sheet_num: int) -> bool:
        """Check if a rule is newly applicable for the given sheet number.

        A rule is "new" if the previous sheet (sheet_num - 1) would NOT
        have satisfied its condition. Rules with no condition are new
        only on sheet 1.
        """
        if condition is None:
            return sheet_num == 1

        # Parse simple "sheet_num >= N" or "sheet_num == N" conditions
        match = re.match(r"sheet_num\s*(>=|==|>)\s*(\d+)", condition.strip())
        if not match:
            return sheet_num == 1  # Unknown condition — treat as new on sheet 1

        operator, value_str = match.groups()
        threshold = int(value_str)

        if operator == ">=":
            return sheet_num == threshold
        if operator == "==":
            return True  # Exact match rules are always "new" for their sheet
        if operator == ">":
            return sheet_num == threshold + 1
        return sheet_num == 1  # Fallback for unrecognized operator

    def _format_single_rule(
        self,
        rule: ValidationRule,
        index: int,
        lines: list[str],
        template_context: dict[str, Any],
    ) -> None:
        """Format a single validation rule and append to lines."""
        expanded_path = self._expand_template(
            rule.path or "", template_context
        )
        expanded_pattern = self._expand_template(
            rule.pattern or "", template_context
        )

        desc = rule.description or f"Requirement {index}"
        lines.append(f"{index}. **{desc}**")

        if rule.type == "file_exists":
            lines.append(f"   - Create file: `{expanded_path}`")

        elif rule.type == "file_modified":
            lines.append(
                f"   - Modify file: `{expanded_path}`"
                f" (must update it during execution)"
            )

        elif rule.type == "content_contains":
            lines.append(f"   - File: `{expanded_path}`")
            lines.append(f"   - Must contain exactly: `{expanded_pattern}`")

        elif rule.type == "content_regex":
            lines.append(f"   - File: `{expanded_path}`")
            lines.append(f"   - Must match pattern: `{expanded_pattern}`")

        elif rule.type == "command_succeeds":
            command = rule.command or ""
            display_cmd = command[:60] + "..." if len(command) > 60 else command
            lines.append(f"   - Command must succeed: `{display_cmd}`")

        lines.append("")

    def _expand_template(self, value: str, context: dict[str, Any]) -> str:
        """Expand template variables in a string.

        Args:
            value: String potentially containing {workspace}, {sheet_num}, etc.
            context: Template variables to substitute.

        Returns:
            Expanded string with variables replaced.
        """
        if not value:
            return value

        try:
            # Handle simple {variable} format
            result = value
            for key, val in context.items():
                placeholder = f"{{{key}}}"
                if placeholder in result:
                    result = result.replace(placeholder, str(val))
            return result
        except (KeyError, TypeError, AttributeError):
            # If expansion fails due to missing key or incompatible type, return original
            return value

    def _build_default_prompt(self, context: SheetContext) -> str:
        """Build a simple default prompt when no template is provided.

        Args:
            context: Sheet context.

        Returns:
            Simple prompt string.
        """
        prompt = (
            f"Processing sheet {context.sheet_num} of {context.total_sheets} "
            f"(items {context.start_item}-{context.end_item})"
        )

        if self.config.stakes:
            prompt += f"\n\n{self.config.stakes}"

        if self.config.thinking_method:
            prompt += f"\n\n{self.config.thinking_method}"

        return prompt

    @staticmethod
    def _format_injection_section(
        skills: list[str],
        tools: list[str],
    ) -> str:
        """Format skills and tools injection content for prompt placement.

        Args:
            skills: Resolved content from 'skill' category injections.
            tools: Resolved content from 'tool' category injections.

        Returns:
            Formatted string with skills/tools sections, or empty string if none.
        """
        parts: list[str] = []
        if skills:
            parts.append("## Injected Skills\n\n" + "\n\n".join(skills))
        if tools:
            parts.append("## Injected Tools\n\n" + "\n\n".join(tools))
        return "\n\n".join(parts)

    @staticmethod
    def _format_spec_fragments(fragments: list[SpecFragment]) -> str:
        """Format spec corpus fragments for prompt injection.

        Each fragment is rendered as a markdown section with its name as the
        header. Structured fragments include both their content and a note
        about the data section. Text fragments include their content directly.

        Args:
            fragments: List of SpecFragment instances to format.

        Returns:
            Formatted markdown string for prompt injection.
        """
        if not fragments:
            return ""

        parts: list[str] = []
        for fragment in fragments:
            parts.append(fragment.content)

        return "## Injected Specs\n\n" + "\n\n".join(parts)

    def build_completion_prompt(
        self,
        ctx: CompletionContext,
        semantic_hints: list[str] | None = None,
    ) -> str:
        """Generate auto-completion prompt for partial failures.

        This prompt tells the agent:
        1. What validations already passed (don't redo)
        2. What validations failed (focus on these)
        3. Semantic hints for focused recovery
        4. Clear instruction to complete only missing items

        Args:
            ctx: Completion context with passed/failed validations.
            semantic_hints: Optional list of actionable hints from semantic
                validation analysis. These are suggested fixes derived from
                the failure_category and suggested_fix fields of failed
                ValidationResults.

        Returns:
            Completion prompt string.
        """
        passed_section = self._format_passed_validations(ctx.passed_validations)
        failed_section = self._format_failed_validations(ctx.failed_validations)
        hints_section = self._format_semantic_hints(semantic_hints)

        # Truncate original prompt if very long
        max_prompt_context_chars = 3000
        original_context = ctx.original_prompt
        if len(original_context) > max_prompt_context_chars:
            truncation_msg = "\n\n[... original prompt truncated for brevity ...]"
            original_context = original_context[:max_prompt_context_chars] + truncation_msg

        completion_prompt = f"""## COMPLETION MODE - Sheet {ctx.sheet_num}

This is completion attempt {ctx.completion_attempt} of {ctx.max_completion_attempts}.

A previous execution of this sheet partially completed. Your job is to \
finish ONLY the incomplete items.

### ALREADY COMPLETED (DO NOT REDO)
The following outputs were successfully created and validated:
{passed_section}

These files exist and are valid. DO NOT recreate or modify them unless absolutely necessary.

### INCOMPLETE ITEMS (FOCUS HERE)
The following validations failed and need to be completed:
{failed_section}
{hints_section}
### INSTRUCTIONS
1. Review what already exists to understand the context
2. Complete ONLY the missing items listed above
3. Do not duplicate work that was already done
4. Ensure all validation requirements are met before finishing

### ORIGINAL TASK CONTEXT
{original_context}

---
Focus on completing the missing items. Do not start over from scratch."""

        return completion_prompt.strip()

    def _format_semantic_hints(self, hints: list[str] | None) -> str:
        """Format semantic hints for injection into completion prompt.

        Args:
            hints: List of actionable hints from semantic validation analysis.

        Returns:
            Formatted string with hints section, or empty string if no hints.
        """
        if not hints:
            return ""

        lines = [
            "",
            "### SUGGESTED FIXES (from validation analysis)",
        ]
        for i, hint in enumerate(hints, 1):
            lines.append(f"  {i}. {hint}")
        lines.append("")

        return "\n".join(lines)

    def _format_passed_validations(
        self, results: list["ValidationResult"]
    ) -> str:
        """Format passed validations for the completion prompt.

        Args:
            results: List of validation results that passed.
                     Uses results (not rules) to get expanded paths.

        Returns:
            Formatted string for prompt.
        """
        if not results:
            return "  (none)"

        lines = []
        for result in results:
            rule = result.rule
            # Use expanded path from result, fallback to rule description
            expanded_path = result.expected_value or result.actual_value
            desc = rule.description or expanded_path or rule.pattern or "Unnamed validation"

            if rule.type == "file_exists":
                lines.append(f"  - [CREATED] {desc}")
                if expanded_path:
                    lines.append(f"    File: {expanded_path}")
            elif rule.type == "file_modified":
                lines.append(f"  - [UPDATED] {desc}")
                if expanded_path:
                    # Extract just the path from "mtime>..." format
                    file_path = result.actual_value
                    if file_path and file_path.startswith("mtime="):
                        file_path = result.expected_value  # Fallback
                    lines.append(f"    File: {expanded_path}")
            elif rule.type in ("content_contains", "content_regex"):
                lines.append(f"  - [VERIFIED] {desc}")
                if expanded_path:
                    lines.append(f"    File: {expanded_path}")

        return "\n".join(lines)

    def _format_failed_validations(
        self, results: list["ValidationResult"]
    ) -> str:
        """Format failed validations for the completion prompt.

        Provides actionable information about what needs to be done.
        Uses ValidationResult objects to get expanded file paths and
        semantic failure reasons.

        Args:
            results: List of validation results that failed.

        Returns:
            Formatted string for prompt.
        """
        if not results:
            return "  (none)"

        lines = []
        for i, result in enumerate(results, 1):
            rule = result.rule
            desc = rule.description or "Unnamed validation"

            # Use semantic failure information if available (Priority 2 evolution)
            if result.failure_category and result.failure_reason:
                lines.append(f"  {i}. [{result.failure_category.upper()}] {desc}")
                lines.append(f"     Why: {result.failure_reason}")
                if result.suggested_fix:
                    lines.append(f"     Fix: {result.suggested_fix}")
            else:
                self._format_legacy_validation(lines, i, desc, result, rule)

            lines.append("")  # Blank line between items

        return "\n".join(lines).rstrip()

    @staticmethod
    def _format_legacy_validation(
        lines: list[str],
        index: int,
        desc: str,
        result: "ValidationResult",
        rule: "ValidationRule",
    ) -> None:
        """Format a single validation result using legacy type-based formatting.

        Called when semantic failure info (failure_category/failure_reason) is not
        available. Uses per-type formatters for readability.
        """
        expanded_path = result.expected_value or result.actual_value
        fmt = _LEGACY_FORMATTERS.get(rule.type)
        if fmt is not None:
            fmt(lines, index, desc, result, rule, expanded_path)


def _fmt_file_exists(
    lines: list[str], index: int, desc: str,
    result: "ValidationResult", rule: "ValidationRule",
    expanded_path: str | None,
) -> None:
    lines.append(f"  {index}. [MISSING] {desc}")
    if expanded_path:
        lines.append(f"     Expected file: {expanded_path}")
        lines.append("     Action: Create this file with the required content")


def _fmt_file_modified(
    lines: list[str], index: int, desc: str,
    result: "ValidationResult", rule: "ValidationRule",
    expanded_path: str | None,
) -> None:
    lines.append(f"  {index}. [NOT UPDATED] {desc}")
    actual_path = None
    if result.error_message and ":" in result.error_message:
        actual_path = result.error_message.split(": ", 1)[-1]
    display_path = actual_path or expanded_path or rule.path
    if display_path:
        lines.append(f"     File needs modification: {display_path}")
        lines.append(
            "     Action: You MUST append/write new content to this file."
        )


def _fmt_content_contains(
    lines: list[str], index: int, desc: str,
    result: "ValidationResult", rule: "ValidationRule",
    expanded_path: str | None,
) -> None:
    lines.append(f"  {index}. [CONTENT MISSING] {desc}")
    if rule.pattern:
        lines.append(f"     Required text: {rule.pattern}")
    if expanded_path:
        lines.append(f"     In file: {expanded_path}")
    lines.append("     Action: Add the required content to the file")


def _fmt_content_regex(
    lines: list[str], index: int, desc: str,
    result: "ValidationResult", rule: "ValidationRule",
    expanded_path: str | None,
) -> None:
    lines.append(f"  {index}. [PATTERN NOT MATCHED] {desc}")
    if rule.pattern:
        lines.append(f"     Required pattern: {rule.pattern}")
    if expanded_path:
        lines.append(f"     In file: {expanded_path}")
    lines.append("     Action: Ensure file content matches the pattern")


def _fmt_command_succeeds(
    lines: list[str], index: int, desc: str,
    result: "ValidationResult", rule: "ValidationRule",
    expanded_path: str | None,
) -> None:
    lines.append(f"  {index}. [COMMAND FAILED] {desc}")
    if result.error_message:
        err_summary = result.error_message[:200]
        if len(result.error_message) > 200:
            err_summary += "..."
        lines.append(f"     Error: {err_summary}")
    lines.append("     Action: Fix the command errors")


# Dispatch table for legacy validation formatters — avoids nested if/elif chain.
_LEGACY_FORMATTERS = {
    "file_exists": _fmt_file_exists,
    "file_modified": _fmt_file_modified,
    "content_contains": _fmt_content_contains,
    "content_regex": _fmt_content_regex,
    "command_succeeds": _fmt_command_succeeds,
}


def build_sheet_prompt_simple(
    config: PromptConfig,
    sheet_num: int,
    total_sheets: int,
    sheet_size: int,
    total_items: int,
    start_item: int,
    workspace: Path,
) -> str:
    """Convenience function to build a sheet prompt.

    This provides a simpler interface for cases where you don't need
    to reuse the PromptBuilder.

    Args:
        config: Prompt configuration.
        sheet_num: Current sheet number.
        total_sheets: Total number of sheets.
        sheet_size: Items per sheet.
        total_items: Total items.
        start_item: First item number.
        workspace: Workspace directory.

    Returns:
        Rendered prompt string.
    """
    builder = PromptBuilder(config)
    context = builder.build_sheet_context(
        sheet_num=sheet_num,
        total_sheets=total_sheets,
        sheet_size=sheet_size,
        total_items=total_items,
        start_item=start_item,
        workspace=workspace,
    )
    return builder.build_sheet_prompt(context)
