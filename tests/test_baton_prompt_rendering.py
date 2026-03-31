"""TDD tests for F-104: Prompt rendering in baton musician.

The baton musician's _build_prompt() must render Jinja2 templates
with all variables, prepend the preamble, inject prelude/cadenza
content, and format validation rules as success requirements.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mozart.core.config.execution import ValidationRule
from mozart.core.sheet import Sheet
from mozart.daemon.baton.events import SheetAttemptResult
from mozart.daemon.baton.musician import _build_prompt
from mozart.daemon.baton.state import AttemptContext, AttemptMode


# =============================================================================
# Fixtures
# =============================================================================


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
    validations: list[ValidationRule] | None = None,
    prompt_extensions: list[str] | None = None,
) -> Sheet:
    return Sheet(
        num=num,
        movement=movement,
        voice=voice,
        voice_count=voice_count,
        workspace=workspace or Path("/tmp/test-workspace"),
        instrument_name=instrument_name,
        prompt_template=prompt_template,
        variables=variables or {},
        template_file=template_file,
        validations=validations or [],
        prompt_extensions=prompt_extensions or [],
    )


def _make_context(
    *,
    attempt_number: int = 1,
    mode: AttemptMode = AttemptMode.NORMAL,
    completion_prompt_suffix: str | None = None,
    learned_patterns: list[str] | None = None,
) -> AttemptContext:
    return AttemptContext(
        attempt_number=attempt_number,
        mode=mode,
        completion_prompt_suffix=completion_prompt_suffix,
        learned_patterns=learned_patterns,
    )


# =============================================================================
# Jinja2 Template Rendering
# =============================================================================


class TestJinja2Rendering:
    def test_renders_workspace_variable(self) -> None:
        sheet = _make_sheet(
            prompt_template="Work in {{ workspace }}",
            workspace=Path("/home/test/ws"),
        )
        prompt = _build_prompt(sheet, _make_context(), total_sheets=5, total_movements=3)
        assert "/home/test/ws" in prompt
        assert "{{ workspace }}" not in prompt

    def test_renders_sheet_num(self) -> None:
        sheet = _make_sheet(num=3, prompt_template="Sheet {{ sheet_num }}")
        prompt = _build_prompt(sheet, _make_context(), total_sheets=5, total_movements=3)
        assert "Sheet 3" in prompt

    def test_renders_movement_and_voice(self) -> None:
        sheet = _make_sheet(
            movement=2, voice=3, voice_count=5,
            prompt_template="M{{ movement }} V{{ voice }} of {{ voice_count }}",
        )
        prompt = _build_prompt(sheet, _make_context(), total_sheets=10, total_movements=4)
        assert "M2 V3 of 5" in prompt

    def test_renders_total_sheets(self) -> None:
        sheet = _make_sheet(prompt_template="{{ total_sheets }} sheets total")
        prompt = _build_prompt(sheet, _make_context(), total_sheets=42, total_movements=1)
        assert "42 sheets total" in prompt

    def test_renders_custom_variables(self) -> None:
        sheet = _make_sheet(
            prompt_template="Hello {{ persona_name }}, do {{ task }}",
            variables={"persona_name": "Alice", "task": "review code"},
        )
        prompt = _build_prompt(sheet, _make_context(), total_sheets=1, total_movements=1)
        assert "Hello Alice, do review code" in prompt

    def test_builtin_variables_override_custom(self) -> None:
        sheet = _make_sheet(
            num=7, prompt_template="Sheet {{ sheet_num }}",
            variables={"sheet_num": 999},
        )
        prompt = _build_prompt(sheet, _make_context(), total_sheets=10, total_movements=1)
        assert "Sheet 7" in prompt

    def test_renders_old_terminology_aliases(self) -> None:
        sheet = _make_sheet(
            movement=2, voice=1, voice_count=3,
            prompt_template="Stage {{ stage }} instance {{ instance }}",
        )
        prompt = _build_prompt(sheet, _make_context(), total_sheets=5, total_movements=2)
        assert "Stage 2 instance 1" in prompt

    def test_renders_instrument_name(self) -> None:
        sheet = _make_sheet(
            instrument_name="gemini-cli",
            prompt_template="Using {{ instrument_name }}",
        )
        prompt = _build_prompt(sheet, _make_context(), total_sheets=1, total_movements=1)
        assert "Using gemini-cli" in prompt


# =============================================================================
# Template File Loading
# =============================================================================


class TestTemplateFileLoading:
    def test_loads_template_file(self, tmp_path: Path) -> None:
        template_file = tmp_path / "prompt.jinja2"
        template_file.write_text("Hello from {{ workspace }}")
        sheet = _make_sheet(template_file=template_file, workspace=tmp_path)
        prompt = _build_prompt(sheet, _make_context(), total_sheets=1, total_movements=1)
        assert f"Hello from {tmp_path}" in prompt

    def test_inline_template_used_when_no_file(self) -> None:
        sheet = _make_sheet(prompt_template="Inline template {{ sheet_num }}", num=5)
        prompt = _build_prompt(sheet, _make_context(), total_sheets=10, total_movements=1)
        assert "Inline template 5" in prompt

    def test_fallback_when_no_template(self) -> None:
        sheet = _make_sheet(num=2)
        prompt = _build_prompt(sheet, _make_context(), total_sheets=10, total_movements=1)
        assert len(prompt) > 0
        assert "mozart-preamble" in prompt.lower()


# =============================================================================
# Preamble
# =============================================================================


class TestPreamble:
    def test_preamble_included(self) -> None:
        sheet = _make_sheet(num=3, prompt_template="Do the work", workspace=Path("/tmp/ws"))
        prompt = _build_prompt(sheet, _make_context(), total_sheets=10, total_movements=3)
        assert "<mozart-preamble>" in prompt
        assert "sheet 3 of 10" in prompt.lower()
        assert "/tmp/ws" in prompt

    def test_retry_preamble(self) -> None:
        sheet = _make_sheet(prompt_template="Fix it")
        ctx = _make_context(attempt_number=3)
        prompt = _build_prompt(sheet, ctx, total_sheets=5, total_movements=1)
        assert "RETRY" in prompt

    def test_parallel_preamble_for_fan_out(self) -> None:
        sheet = _make_sheet(voice=2, voice_count=3, prompt_template="Do work")
        prompt = _build_prompt(sheet, _make_context(), total_sheets=5, total_movements=2)
        assert "concurrently" in prompt.lower()

    def test_preamble_before_template(self) -> None:
        sheet = _make_sheet(prompt_template="THE_TEMPLATE_CONTENT")
        prompt = _build_prompt(sheet, _make_context(), total_sheets=5, total_movements=1)
        preamble_idx = prompt.find("<mozart-preamble>")
        template_idx = prompt.find("THE_TEMPLATE_CONTENT")
        assert preamble_idx >= 0
        assert template_idx >= 0
        assert preamble_idx < template_idx


# =============================================================================
# Completion Mode
# =============================================================================


class TestCompletionMode:
    def test_completion_suffix_appended(self) -> None:
        sheet = _make_sheet(prompt_template="Do the work")
        ctx = _make_context(completion_prompt_suffix="Fix the remaining failures.")
        prompt = _build_prompt(sheet, ctx, total_sheets=1, total_movements=1)
        assert "Do the work" in prompt
        assert "Fix the remaining failures." in prompt

    def test_no_suffix_without_completion(self) -> None:
        sheet = _make_sheet(prompt_template="Do the work")
        prompt = _build_prompt(sheet, _make_context(), total_sheets=1, total_movements=1)
        assert "Do the work" in prompt
        assert "remaining failures" not in prompt


# =============================================================================
# Validation Rules Injection
# =============================================================================


class TestValidationInjection:
    def test_validation_rules_injected(self) -> None:
        rules = [
            ValidationRule(
                type="file_exists", path="{workspace}/output.md",
                description="Output report",
            ),
        ]
        sheet = _make_sheet(
            prompt_template="Write the report",
            validations=rules, workspace=Path("/tmp/ws"),
        )
        prompt = _build_prompt(sheet, _make_context(), total_sheets=1, total_movements=1)
        assert "Output report" in prompt
        assert "/tmp/ws/output.md" in prompt

    def test_no_validation_section_when_empty(self) -> None:
        sheet = _make_sheet(prompt_template="Do work", validations=[])
        prompt = _build_prompt(sheet, _make_context(), total_sheets=1, total_movements=1)
        assert "Success Requirements" not in prompt

    def test_command_succeeds_rule_shown(self) -> None:
        rules = [
            ValidationRule(
                type="command_succeeds", command="pytest tests/ -x",
                description="Tests pass",
            ),
        ]
        sheet = _make_sheet(prompt_template="Fix the bug", validations=rules)
        prompt = _build_prompt(sheet, _make_context(), total_sheets=1, total_movements=1)
        assert "Tests pass" in prompt


# =============================================================================
# Prelude/Cadenza Injection
# =============================================================================


class TestPreludeCadenzaInjection:
    def test_prelude_context_injected(self, tmp_path: Path) -> None:
        from mozart.core.config.job import InjectionCategory, InjectionItem

        prelude_file = tmp_path / "context.md"
        prelude_file.write_text("This is shared context for all sheets.")

        sheet = _make_sheet(prompt_template="Do the work", workspace=tmp_path)
        sheet_dict = sheet.model_dump()
        sheet_dict["prelude"] = [
            InjectionItem(file=str(prelude_file), **{"as": InjectionCategory.CONTEXT}),
        ]
        sheet = Sheet(**sheet_dict)

        prompt = _build_prompt(sheet, _make_context(), total_sheets=1, total_movements=1)
        assert "This is shared context for all sheets." in prompt

    def test_missing_injection_file_skipped(self) -> None:
        from mozart.core.config.job import InjectionCategory, InjectionItem

        sheet = _make_sheet(prompt_template="Do the work")
        sheet_dict = sheet.model_dump()
        sheet_dict["prelude"] = [
            InjectionItem(file="/nonexistent/file.md", **{"as": InjectionCategory.CONTEXT}),
        ]
        sheet = Sheet(**sheet_dict)

        prompt = _build_prompt(sheet, _make_context(), total_sheets=1, total_movements=1)
        assert "Do the work" in prompt


# =============================================================================
# AttemptContext Backward Compatibility
# =============================================================================


class TestAttemptContextBackwardCompat:
    def test_default_values(self) -> None:
        ctx = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        assert ctx.total_sheets == 1
        assert ctx.total_movements == 1

    def test_custom_values(self) -> None:
        ctx = AttemptContext(
            attempt_number=1, mode=AttemptMode.NORMAL,
            total_sheets=100, total_movements=15,
        )
        assert ctx.total_sheets == 100
        assert ctx.total_movements == 15

    def test_existing_fields_preserved(self) -> None:
        ctx = AttemptContext(
            attempt_number=2, mode=AttemptMode.COMPLETION,
            completion_prompt_suffix="Fix it", learned_patterns=["Pattern 1"],
        )
        assert ctx.attempt_number == 2
        assert ctx.mode == AttemptMode.COMPLETION
        assert ctx.completion_prompt_suffix == "Fix it"
        assert ctx.learned_patterns == ["Pattern 1"]


# =============================================================================
# Integration: Full sheet_task flow
# =============================================================================


class TestSheetTaskIntegration:
    @pytest.mark.asyncio
    async def test_sheet_task_renders_template(self) -> None:
        from mozart.daemon.baton.musician import sheet_task

        sheet = _make_sheet(
            prompt_template="Work in {{ workspace }}",
            workspace=Path("/tmp/test-ws"),
        )

        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=MagicMock(
                success=True, exit_code=0, stdout="Done", stderr="",
                duration_seconds=1.0, rate_limited=False, error_message=None,
                input_tokens=100, output_tokens=50, model="test-model",
            )
        )
        backend.set_preamble = MagicMock()
        backend.set_prompt_extensions = MagicMock()

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        await sheet_task(
            job_id="test-job", sheet=sheet, backend=backend,
            attempt_context=_make_context(), inbox=inbox,
            total_sheets=5, total_movements=3,
        )

        backend.execute.assert_called_once()
        prompt_arg = backend.execute.call_args[0][0]
        assert "/tmp/test-ws" in prompt_arg
        assert "{{ workspace }}" not in prompt_arg

        result = inbox.get_nowait()
        assert result.execution_success is True
