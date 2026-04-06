"""Tests for baton musician prompt rendering (F-104).

Verifies that the musician's _build_prompt() correctly renders Jinja2
templates, loads template files, resolves prelude/cadenza injections,
builds the preamble, and handles completion mode.

TDD: red first, green second.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.config.execution import ValidationRule
from marianne.core.config.job import InjectionCategory, InjectionItem
from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.musician import _build_prompt, sheet_task
from marianne.daemon.baton.state import AttemptContext, AttemptMode


def _make_sheet(
    *,
    num: int = 1,
    movement: int = 1,
    voice: int | None = None,
    voice_count: int = 1,
    workspace: Path | None = None,
    instrument_name: str = "claude-code",
    prompt_template: str | None = None,
    template_file: Path | None = None,
    variables: dict[str, Any] | None = None,
    prelude: list[InjectionItem] | None = None,
    cadenza: list[InjectionItem] | None = None,
    validations: list[ValidationRule] | None = None,
    prompt_extensions: list[str] | None = None,
) -> Sheet:
    """Create a test Sheet with sensible defaults."""
    return Sheet(
        num=num,
        movement=movement,
        voice=voice,
        voice_count=voice_count,
        workspace=workspace or Path("/tmp/test-workspace"),
        instrument_name=instrument_name,
        prompt_template=prompt_template,
        template_file=template_file,
        variables=variables or {},
        prelude=prelude or [],
        cadenza=cadenza or [],
        validations=validations or [],
        prompt_extensions=prompt_extensions or [],
    )


def _make_context(
    *,
    attempt_number: int = 1,
    mode: AttemptMode = AttemptMode.NORMAL,
    completion_prompt_suffix: str | None = None,
) -> AttemptContext:
    """Create a test AttemptContext."""
    return AttemptContext(
        attempt_number=attempt_number,
        mode=mode,
        completion_prompt_suffix=completion_prompt_suffix,
    )


class TestBuildPromptJinja2Rendering:
    """Test that _build_prompt renders Jinja2 templates with sheet variables."""

    def test_renders_inline_template_with_sheet_variables(self) -> None:
        """Template variables like {{ sheet_num }} are rendered."""
        sheet = _make_sheet(
            num=3,
            prompt_template="Sheet {{ sheet_num }} of {{ total_sheets }}. Workspace: {{ workspace }}",
        )
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=10, total_movements=5)

        assert "Sheet 3 of 10" in prompt
        assert "/tmp/test-workspace" in prompt

    def test_renders_custom_variables(self) -> None:
        """User-defined variables from the score are available in templates."""
        sheet = _make_sheet(
            prompt_template="Hello {{ name }}, your role is {{ role }}",
            variables={"name": "Forge", "role": "architect"},
        )
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=1, total_movements=1)

        assert "Hello Forge" in prompt
        assert "your role is architect" in prompt

    def test_renders_movement_voice_aliases(self) -> None:
        """New terminology aliases (movement, voice, voice_count) are available."""
        sheet = _make_sheet(
            num=5,
            movement=2,
            voice=3,
            voice_count=4,
            prompt_template="Movement {{ movement }}, voice {{ voice }} of {{ voice_count }}",
        )
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=20, total_movements=5)

        assert "Movement 2" in prompt
        assert "voice 3 of 4" in prompt

    def test_renders_template_file(self, tmp_path: Path) -> None:
        """Templates can be loaded from external files."""
        template_file = tmp_path / "prompt.j2"
        template_file.write_text("Task for sheet {{ sheet_num }}: do the thing")

        sheet = _make_sheet(
            num=7,
            template_file=template_file,
        )
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=10, total_movements=3)

        assert "Task for sheet 7: do the thing" in prompt

    def test_template_file_takes_precedence_over_inline(self, tmp_path: Path) -> None:
        """When both template_file and prompt_template are set, file wins."""
        template_file = tmp_path / "prompt.j2"
        template_file.write_text("From file: {{ sheet_num }}")

        sheet = _make_sheet(
            num=1,
            prompt_template="From inline: {{ sheet_num }}",
            template_file=template_file,
        )
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=1, total_movements=1)

        # File should be used, not inline
        assert "From file: 1" in prompt
        assert "From inline" not in prompt

    def test_empty_template_produces_nonempty_prompt(self) -> None:
        """A sheet with no template still gets preamble and validations."""
        sheet = _make_sheet(prompt_template=None, template_file=None)
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=1, total_movements=1)

        # Should have preamble at minimum
        assert "mozart-preamble" in prompt

    def test_jinja2_strict_undefined_raises_on_missing_var(self) -> None:
        """References to undefined variables raise an error."""
        sheet = _make_sheet(
            prompt_template="Hello {{ nonexistent_variable }}",
        )
        context = _make_context()
        # The musician catches exceptions and reports via inbox, but
        # _build_prompt itself should raise
        with pytest.raises(Exception):
            _build_prompt(sheet, context, total_sheets=1, total_movements=1)


class TestBuildPromptPreamble:
    """Test preamble injection."""

    def test_preamble_prepended(self) -> None:
        """The preamble appears at the start of the prompt."""
        sheet = _make_sheet(
            num=3,
            prompt_template="Do the work",
        )
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=10, total_movements=5)

        # Preamble should be before the template content
        preamble_pos = prompt.find("<mozart-preamble>")
        template_pos = prompt.find("Do the work")
        assert preamble_pos >= 0, "Preamble not found"
        assert template_pos > preamble_pos, "Preamble should precede template"

    def test_retry_preamble_on_second_attempt(self) -> None:
        """Retry attempts get a different preamble."""
        sheet = _make_sheet(num=1, prompt_template="Do the work")
        context = _make_context(attempt_number=2)
        prompt = _build_prompt(sheet, context, total_sheets=1, total_movements=1)

        assert "RETRY" in prompt


class TestBuildPromptInjections:
    """Test prelude/cadenza injection resolution."""

    def test_prelude_content_injected(self, tmp_path: Path) -> None:
        """Prelude files are read and injected into the prompt."""
        prelude_file = tmp_path / "context.md"
        prelude_file.write_text("# Project Context\nThis project builds widgets.")

        sheet = _make_sheet(
            workspace=tmp_path,
            prompt_template="Do the work",
            prelude=[
                InjectionItem(file=str(prelude_file), as_=InjectionCategory.CONTEXT),
            ],
        )
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=1, total_movements=1)

        assert "This project builds widgets" in prompt

    def test_cadenza_content_injected(self, tmp_path: Path) -> None:
        """Cadenza files are read and injected into the prompt."""
        cadenza_file = tmp_path / "closing.md"
        cadenza_file.write_text("Remember to commit your work.")

        sheet = _make_sheet(
            workspace=tmp_path,
            prompt_template="Do the work",
            cadenza=[
                InjectionItem(file=str(cadenza_file), as_=InjectionCategory.CONTEXT),
            ],
        )
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=1, total_movements=1)

        assert "Remember to commit your work" in prompt

    def test_jinja_path_expansion_in_injections(self, tmp_path: Path) -> None:
        """Injection file paths support Jinja2 variable expansion."""
        context_file = tmp_path / "sheet-5-context.md"
        context_file.write_text("Context for sheet 5")

        sheet = _make_sheet(
            num=5,
            workspace=tmp_path,
            prompt_template="Do the work",
            prelude=[
                InjectionItem(
                    file=str(tmp_path / "sheet-{{ sheet_num }}-context.md"),
                    as_=InjectionCategory.CONTEXT,
                ),
            ],
        )
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=10, total_movements=3)

        assert "Context for sheet 5" in prompt

    def test_missing_context_file_skipped_gracefully(self, tmp_path: Path) -> None:
        """Missing context files are skipped without error."""
        sheet = _make_sheet(
            workspace=tmp_path,
            prompt_template="Do the work",
            prelude=[
                InjectionItem(
                    file=str(tmp_path / "nonexistent.md"),
                    as_=InjectionCategory.CONTEXT,
                ),
            ],
        )
        context = _make_context()
        # Should not raise
        prompt = _build_prompt(sheet, context, total_sheets=1, total_movements=1)
        assert "Do the work" in prompt


class TestBuildPromptCompletionMode:
    """Test completion mode suffix handling."""

    def test_completion_suffix_appended(self) -> None:
        """Completion mode appends the suffix to the prompt."""
        sheet = _make_sheet(prompt_template="Original task")
        context = _make_context(
            mode=AttemptMode.COMPLETION,
            completion_prompt_suffix="Fix the remaining validation failures.",
        )
        prompt = _build_prompt(sheet, context, total_sheets=1, total_movements=1)

        assert "Original task" in prompt
        assert "Fix the remaining validation failures" in prompt

    def test_no_suffix_in_normal_mode(self) -> None:
        """Normal mode does not append any suffix."""
        sheet = _make_sheet(prompt_template="Original task")
        context = _make_context(mode=AttemptMode.NORMAL)
        prompt = _build_prompt(sheet, context, total_sheets=1, total_movements=1)

        assert "Original task" in prompt
        # No completion-specific language
        assert "remaining" not in prompt.lower() or "validation" not in prompt.lower()


class TestBuildPromptValidations:
    """Test validation requirements injection."""

    def test_validation_rules_formatted_as_requirements(self) -> None:
        """Validation rules appear as success requirements at the end."""
        sheet = _make_sheet(
            prompt_template="Write the output",
            validations=[
                ValidationRule(
                    type="file_exists",
                    path="{workspace}/output.md",
                    description="Output file must exist",
                ),
            ],
        )
        context = _make_context()
        prompt = _build_prompt(sheet, context, total_sheets=1, total_movements=1)

        assert "Success Requirements" in prompt or "output.md" in prompt


class TestSheetTaskIntegration:
    """Integration test: sheet_task renders prompts and reports results."""

    async def test_sheet_task_renders_prompt_before_execution(self) -> None:
        """sheet_task should pass a rendered (non-empty) prompt to the backend."""
        sheet = _make_sheet(
            num=1,
            prompt_template="Build feature {{ sheet_num }}",
        )
        context = _make_context()

        mock_backend = AsyncMock()
        mock_backend.execute.return_value = MagicMock(
            success=True,
            exit_code=0,
            stdout="done",
            stderr="",
            rate_limited=False,
            duration_seconds=5.0,
            input_tokens=100,
            output_tokens=200,
            model="claude-sonnet-4",
            error_message=None,
        )

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=mock_backend,
            attempt_context=context,
            inbox=inbox,
            total_sheets=1,
            total_movements=1,
        )

        # Verify backend was called with rendered prompt
        mock_backend.execute.assert_called_once()
        call_args = mock_backend.execute.call_args
        prompt_arg = call_args[0][0] if call_args[0] else call_args[1].get("prompt", "")

        assert "Build feature 1" in prompt_arg, (
            f"Expected rendered template in prompt, got: {prompt_arg[:200]}"
        )

        # Verify result was reported
        result = inbox.get_nowait()
        assert result.execution_success is True
        assert result.job_id == "test-job"
