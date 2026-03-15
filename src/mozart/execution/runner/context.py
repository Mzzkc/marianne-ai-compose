"""Context building mixin for JobRunner.

Provides template context construction for sheet execution:
- _build_sheet_context(): Build SheetContext for template expansion
- _populate_cross_sheet_context(): Inject cross-sheet outputs/files
- _capture_cross_sheet_files(): Read file patterns for cross-sheet context

Architecture:
    This mixin requires access to attributes from:
    - JobRunnerBase: config, _logger, prompt_builder

    It provides methods consumed by:
    - SheetExecutionMixin._prepare_sheet_execution()
"""

from __future__ import annotations

import glob
from pathlib import Path
from typing import TYPE_CHECKING

import jinja2

if TYPE_CHECKING:
    from mozart.core.checkpoint import CheckpointState
    from mozart.core.config import CrossSheetConfig, JobConfig
    from mozart.core.logging import MozartLogger
    from mozart.prompts.templating import PromptBuilder

from mozart.core.checkpoint import SheetStatus
from mozart.core.config.job import InjectionCategory, InjectionItem
from mozart.prompts.templating import SheetContext


class ContextBuildingMixin:
    """Mixin providing template context construction for JobRunner.

    Extracted from SheetExecutionMixin to reduce its size and isolate
    context construction logic, which has no dependencies on execution
    state machines, decision logic, or validation.
    """

    if TYPE_CHECKING:
        config: JobConfig
        _logger: MozartLogger
        prompt_builder: PromptBuilder

    def _build_sheet_context(
        self,
        sheet_num: int,
        state: CheckpointState | None = None,
    ) -> SheetContext:
        """Build sheet context for template expansion.

        Args:
            sheet_num: Current sheet number.
            state: Optional current job state for cross-sheet context.

        Returns:
            SheetContext with item range, workspace, and optional cross-sheet data.
        """
        context = self.prompt_builder.build_sheet_context(
            sheet_num=sheet_num,
            total_sheets=self.config.sheet.total_sheets,
            sheet_size=self.config.sheet.size,
            total_items=self.config.sheet.total_items,
            start_item=self.config.sheet.start_item,
            workspace=self.config.workspace,
        )

        # Populate fan-out metadata for template variables
        fan_meta = self.config.sheet.get_fan_out_metadata(sheet_num)
        context.stage = fan_meta.stage
        context.instance = fan_meta.instance
        context.fan_count = fan_meta.fan_count
        context.total_stages = self.config.sheet.total_stages

        # Populate cross-sheet context if configured
        if self.config.cross_sheet and state:
            cross_sheet = self.config.cross_sheet
            self._populate_cross_sheet_context(context, state, sheet_num, cross_sheet)

        # Resolve prelude & cadenza injections (GH#53)
        self._resolve_injections(context, sheet_num)

        return context

    def _resolve_injections(
        self,
        context: SheetContext,
        sheet_num: int,
    ) -> None:
        """Resolve prelude & cadenza injections for a sheet.

        Collects prelude items (applied to all sheets) plus cadenza items
        for this specific sheet. Expands Jinja templates in file paths,
        reads file contents, and populates the SheetContext injection fields.

        Missing files are handled based on category:
        - context: warn and skip (non-critical background info)
        - skill/tool: raise error (critical for correct execution)

        Args:
            context: SheetContext to populate with injection content.
            sheet_num: Current sheet number (for cadenza lookup).
        """
        items: list[InjectionItem] = list(self.config.sheet.prelude)
        cadenza_items = self.config.sheet.cadenzas.get(sheet_num, [])
        items.extend(cadenza_items)

        if not items:
            return

        # Template variables for Jinja path expansion
        template_vars = context.to_dict()

        env = jinja2.Environment(
            undefined=jinja2.Undefined,  # lenient — missing vars become empty
            autoescape=False,
        )

        for item in items:
            try:
                # Expand Jinja templates in file path
                tmpl = env.from_string(item.file)
                expanded_path = tmpl.render(**template_vars)
            except jinja2.TemplateError as e:
                self._logger.warning(
                    "injection.path_expansion_error",
                    file=item.file,
                    error=str(e),
                )
                continue

            path = Path(expanded_path)
            if not path.is_absolute():
                path = self.config.workspace / path

            if not path.is_file():
                if item.as_ == InjectionCategory.CONTEXT:
                    self._logger.warning(
                        "injection.file_not_found",
                        file=str(path),
                        category=item.as_.value,
                    )
                else:
                    self._logger.error(
                        "injection.required_file_not_found",
                        file=str(path),
                        category=item.as_.value,
                    )
                continue

            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                self._logger.warning(
                    "injection.file_read_error",
                    file=str(path),
                    error=str(e),
                )
                continue

            if item.as_ == InjectionCategory.CONTEXT:
                context.injected_context.append(content)
            elif item.as_ == InjectionCategory.SKILL:
                context.injected_skills.append(content)
            elif item.as_ == InjectionCategory.TOOL:
                context.injected_tools.append(content)

    def _populate_cross_sheet_context(
        self,
        context: SheetContext,
        state: CheckpointState,
        sheet_num: int,
        cross_sheet: CrossSheetConfig,
    ) -> None:
        """Populate cross-sheet context from previous sheet outputs.

        Adds previous_outputs and previous_files to the context based on
        CrossSheetConfig settings.

        Args:
            context: SheetContext to populate.
            state: Current job state with sheet history.
            sheet_num: Current sheet number.
            cross_sheet: Cross-sheet configuration.
        """
        # Auto-capture stdout from previous sheets
        if cross_sheet.auto_capture_stdout:
            if cross_sheet.lookback_sheets > 0:
                start_sheet = max(1, sheet_num - cross_sheet.lookback_sheets)
            else:
                start_sheet = 1

            max_chars = cross_sheet.max_output_chars

            for prev_num in range(start_sheet, sheet_num):
                prev_state = state.sheets.get(prev_num)
                if prev_state and prev_state.stdout_tail:
                    output = prev_state.stdout_tail
                    if len(output) > max_chars:
                        output = output[:max_chars] + "\n... [truncated]"
                    context.previous_outputs[prev_num] = output

            # Warn when upstream sheets were skipped — fan-in gets silent data gaps
            skipped_nums = [
                n for n in range(start_sheet, sheet_num)
                if (s := state.sheets.get(n)) and s.status == SheetStatus.SKIPPED
            ]
            if skipped_nums:
                self._logger.warning(
                    "fan_in_upstream_skipped",
                    fan_in_sheet=sheet_num,
                    skipped_sheets=skipped_nums,
                    received_inputs=len(context.previous_outputs),
                )

        # Read configured file patterns
        if cross_sheet.capture_files:
            self._capture_cross_sheet_files(context, state, sheet_num, cross_sheet)

    def _capture_cross_sheet_files(
        self,
        context: SheetContext,
        state: CheckpointState,
        sheet_num: int,
        cross_sheet: CrossSheetConfig,
    ) -> None:
        """Capture file contents for cross-sheet context.

        Reads files matching the configured patterns and adds their contents
        to context.previous_files. Pattern variables are expanded using Jinja2.

        Files modified before the current job's started_at are considered stale
        (leftover from a previous run) and are skipped with a warning.

        Args:
            context: SheetContext to populate.
            state: Current job state (used for stale file detection).
            sheet_num: Current sheet number.
            cross_sheet: Cross-sheet configuration.
        """
        template_vars = {
            "workspace": str(self.config.workspace),
            "sheet_num": sheet_num,
        }

        # Stale file threshold: files older than job start are from a previous run
        job_start_ts = state.started_at.timestamp() if state.started_at else None

        for pattern in cross_sheet.capture_files:
            try:
                expanded_pattern = pattern
                for var, val in template_vars.items():
                    expanded_pattern = expanded_pattern.replace(
                        f"{{{{ {var} }}}}", str(val)
                    )
                    expanded_pattern = expanded_pattern.replace(
                        f"{{{{{var}}}}}", str(val)
                    )

                if not Path(expanded_pattern).is_absolute():
                    expanded_pattern = str(self.config.workspace / expanded_pattern)

                for file_path in glob.glob(expanded_pattern):
                    path = Path(file_path)
                    if path.is_file():
                        # Skip stale files from previous runs
                        if job_start_ts is not None:
                            file_mtime = path.stat().st_mtime
                            if file_mtime < job_start_ts:
                                self._logger.debug(
                                    "cross_sheet.stale_file_skipped",
                                    path=str(path),
                                    file_mtime=file_mtime,
                                    job_started_at=job_start_ts,
                                )
                                continue
                        try:
                            content = path.read_text(encoding="utf-8")
                            max_chars = cross_sheet.max_output_chars
                            if len(content) > max_chars:
                                content = content[:max_chars] + "\n... [truncated]"
                            context.previous_files[str(path)] = content
                        except (OSError, UnicodeDecodeError) as e:
                            self._logger.warning(
                                "cross_sheet.file_read_error",
                                path=str(path),
                                error=str(e),
                            )
            except Exception as e:
                self._logger.warning(
                    "cross_sheet.pattern_error",
                    pattern=pattern,
                    error=str(e),
                )
