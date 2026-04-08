"""PromptRenderer — full prompt rendering for baton musicians.

Bridges the PromptBuilder pipeline with the baton's Sheet-based execution
model. Replaces the bare-bones ``_build_prompt()`` in ``musician.py`` with
the full 9-layer prompt assembly:

    1. Preamble (positional identity)
    2. Template rendering (Jinja2 with all variables)
    3. Skills/tools injection (prelude/cadenza with category=skill/tool)
    4. Context injection (prelude/cadenza with category=context)
    5. Spec fragments (from specification corpus)
    6. Failure history (from previous sheets)
    7. Learned patterns (from learning store)
    8. Validation requirements (as agent-readable checklist)
    9. Completion suffix (for completion mode retries)

The renderer is intentionally stateless — create one per job, call
``render()`` per sheet. No runner dependency, no mixin inheritance.

This is the F-104 fix. Without it, ``use_baton: true`` produces raw
templates instead of rendered prompts.

See: ``docs/plans/2026-03-26-baton-design.md`` — Prompt Assembly
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import jinja2

from marianne.core.config.job import InjectionCategory, InjectionItem, PromptConfig
from marianne.core.sheet import Sheet
from marianne.daemon.baton.state import AttemptContext
from marianne.prompts.preamble import build_preamble
from marianne.prompts.templating import PromptBuilder, SheetContext

if TYPE_CHECKING:
    from marianne.core.config.spec import SpecFragment
    from marianne.execution.validation import HistoricalFailure

from marianne.core.logging import get_logger

_logger = get_logger("daemon.baton.prompt")


@dataclass(frozen=True)
class RenderedPrompt:
    """Output of the prompt rendering pipeline.

    Contains both the rendered prompt (for backend execution) and the
    preamble (set on the backend before execution). Separating them
    allows the musician to configure the backend correctly.
    """

    prompt: str
    """Fully rendered prompt with all injections and context layers."""

    preamble: str
    """Dynamic preamble with positional identity and retry status."""


class PromptRenderer:
    """Renders full prompts for baton musicians.

    Bridges the PromptBuilder pipeline with the baton's Sheet-based
    execution model. Handles template rendering, injection resolution,
    preamble assembly, and validation requirements — everything the
    old runner's context mixin did, but without the runner dependency.

    Create one per job (or share across jobs with the same config).
    Call ``render()`` per sheet dispatch.

    Args:
        prompt_config: The job's PromptConfig (template, variables, etc.).
        total_sheets: Total sheets in the job (for preamble).
        total_stages: Total stages before fan-out expansion (for template aliases).
        parallel_enabled: Whether parallel execution is enabled (for preamble).
    """

    def __init__(
        self,
        prompt_config: PromptConfig,
        total_sheets: int,
        total_stages: int,
        parallel_enabled: bool,
    ) -> None:
        self._prompt_config = prompt_config
        self._total_sheets = total_sheets
        self._total_stages = total_stages
        self._parallel_enabled = parallel_enabled

    def render(
        self,
        sheet: Sheet,
        attempt_context: AttemptContext,
        *,
        patterns: list[str] | None = None,
        failure_history: list[HistoricalFailure] | None = None,
        spec_fragments: list[SpecFragment] | None = None,
    ) -> RenderedPrompt:
        """Render a full prompt for a sheet execution.

        Performs the complete 9-layer prompt assembly:
        template rendering -> injection resolution -> optional layers ->
        validation requirements -> completion suffix.

        Args:
            sheet: The Sheet entity to render for.
            attempt_context: Context from the baton (attempt number, mode).
            patterns: Optional learned pattern descriptions to inject.
            failure_history: Optional historical failures from previous sheets.
            spec_fragments: Optional spec corpus fragments to inject.

        Returns:
            RenderedPrompt with fully rendered prompt and preamble.
        """
        # Layer 1: Build SheetContext from Sheet entity (F-210: includes cross-sheet)
        context = self._build_context(sheet, attempt_context)

        # Layer 2-3: Resolve prelude/cadenza injections into the context
        self._resolve_injections(context, sheet)

        # Layer 4-8: Build prompt through PromptBuilder
        prompt = self._build_prompt(
            sheet, context, patterns, failure_history, spec_fragments
        )

        # Layer 9: Completion mode suffix
        if attempt_context.completion_prompt_suffix:
            prompt = f"{prompt}\n\n{attempt_context.completion_prompt_suffix}"

        # Preamble: positional identity + retry status
        retry_count = max(0, attempt_context.attempt_number - 1)
        preamble = build_preamble(
            sheet_num=sheet.num,
            total_sheets=self._total_sheets,
            workspace=sheet.workspace,
            retry_count=retry_count,
            is_parallel=self._parallel_enabled,
        )

        return RenderedPrompt(prompt=prompt, preamble=preamble)

    def _build_context(
        self,
        sheet: Sheet,
        attempt_context: AttemptContext | None = None,
    ) -> SheetContext:
        """Build SheetContext from a Sheet entity.

        Populates fan-out metadata (movement/voice) from the Sheet's
        own fields rather than querying config.sheet.get_fan_out_metadata().
        The Sheet already has these values resolved by build_sheets().

        F-210: When attempt_context is provided, populates cross-sheet
        context (previous_outputs and previous_files) from the context.
        This bridges the baton's AttemptContext to the template's
        SheetContext, enabling {{ previous_outputs }} and {{ previous_files }}
        in templates.

        Args:
            sheet: The Sheet entity.
            attempt_context: Optional attempt context with cross-sheet data.

        Returns:
            SheetContext with identity, workspace, fan-out, and cross-sheet metadata.
        """
        # Item range defaults: for non-batch scores, start == end == sheet_num
        start_item = sheet.num
        end_item = sheet.num

        context = SheetContext(
            sheet_num=sheet.num,
            total_sheets=self._total_sheets,
            start_item=start_item,
            end_item=end_item,
            workspace=sheet.workspace,
        )

        # Fan-out metadata from the Sheet entity
        context.stage = sheet.movement if sheet.movement else sheet.num
        context.instance = sheet.voice if sheet.voice else 1
        context.fan_count = sheet.voice_count
        context.total_stages = (
            self._total_stages if self._total_stages > 0 else self._total_sheets
        )

        # F-210: Cross-sheet context from AttemptContext
        if attempt_context is not None:
            context.previous_outputs = dict(attempt_context.previous_outputs)
            context.previous_files = dict(attempt_context.previous_files)

        return context

    def _resolve_injections(
        self,
        context: SheetContext,
        sheet: Sheet,
    ) -> None:
        """Resolve prelude and cadenza injections from the Sheet entity.

        Reads files referenced by InjectionItems, expands Jinja2 templates
        in file paths, and populates the SheetContext injection fields.

        Missing context files are skipped with a warning. Missing skill/tool
        files log an error but don't raise (the musician should still execute).

        Args:
            context: SheetContext to populate with injection content.
            sheet: Sheet entity with prelude/cadenza items.
        """
        items: list[InjectionItem] = [*sheet.prelude, *sheet.cadenza]
        if not items:
            return

        # Template variables for Jinja path expansion
        template_vars = context.to_dict()
        # Add sheet-specific variables
        template_vars.update(sheet.variables)

        env = jinja2.Environment(
            undefined=jinja2.Undefined,  # lenient for path expansion
            autoescape=False,
        )

        for item in items:
            try:
                tmpl = env.from_string(item.file)
                expanded_path = tmpl.render(**template_vars)
            except jinja2.TemplateError as e:
                _logger.warning(
                    "prompt_renderer.path_expansion_error",
                    extra={"file": item.file, "error": str(e)},
                )
                continue

            path = Path(expanded_path)
            if not path.is_absolute():
                path = sheet.workspace / path

            if not path.is_file():
                if item.as_ == InjectionCategory.CONTEXT:
                    _logger.warning(
                        "prompt_renderer.file_not_found",
                        extra={"file": str(path), "category": item.as_.value},
                    )
                else:
                    _logger.error(
                        "prompt_renderer.file_not_found",
                        extra={"file": str(path), "category": item.as_.value},
                    )
                continue

            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                _logger.warning(
                    "prompt_renderer.file_read_error",
                    extra={"file": str(path), "error": str(e)},
                )
                continue

            if item.as_ == InjectionCategory.CONTEXT:
                context.injected_context.append(content)
            elif item.as_ == InjectionCategory.SKILL:
                context.injected_skills.append(content)
            elif item.as_ == InjectionCategory.TOOL:
                context.injected_tools.append(content)

    def _build_prompt(
        self,
        sheet: Sheet,
        context: SheetContext,
        patterns: list[str] | None,
        failure_history: list[HistoricalFailure] | None,
        spec_fragments: list[SpecFragment] | None,
    ) -> str:
        """Build the rendered prompt through PromptBuilder.

        Creates a per-sheet PromptConfig with the sheet's template and
        merged variables, then delegates to PromptBuilder.build_sheet_prompt()
        for the full rendering pipeline.

        Args:
            sheet: Sheet entity with template and variables.
            context: Populated SheetContext with injections.
            patterns: Optional learned patterns.
            failure_history: Optional failure history.
            spec_fragments: Optional spec corpus fragments.

        Returns:
            Fully rendered prompt string.
        """
        # Merge global config variables with sheet-specific variables
        merged_vars = dict(self._prompt_config.variables)
        merged_vars.update(sheet.variables)

        # Determine template: sheet.prompt_template or sheet.template_file
        template_text = sheet.prompt_template
        template_file = sheet.template_file

        # Load template file if no inline template
        if not template_text and template_file and template_file.exists():
            template_text = template_file.read_text(encoding="utf-8")

        sheet_prompt_config = self._prompt_config.model_copy(
            update={
                "template": template_text,
                "template_file": None,  # Already loaded above
                "variables": merged_vars,
            }
        )

        builder = PromptBuilder(sheet_prompt_config)

        return builder.build_sheet_prompt(
            context,
            patterns=patterns if patterns else None,
            validation_rules=sheet.validations if sheet.validations else None,
            failure_history=failure_history if failure_history else None,
            spec_fragments=spec_fragments if spec_fragments else None,
        )
