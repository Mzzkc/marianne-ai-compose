"""TDD tests for baton PromptRenderer — F-104 fix.

The PromptRenderer bridges the PromptBuilder pipeline with the baton's
Sheet-based execution model. It replaces the bare-bones _build_prompt()
in musician.py with full Jinja2 rendering, injection resolution,
preamble assembly, and validation requirements.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config.execution import ValidationRule
from marianne.core.config.job import InjectionCategory, InjectionItem, PromptConfig
from marianne.core.sheet import Sheet
from marianne.daemon.baton.prompt import PromptRenderer, RenderedPrompt
from marianne.daemon.baton.state import AttemptContext, AttemptMode


# =========================================================================
# Fixtures
# =========================================================================


def _make_sheet(
    num: int = 1,
    *,
    prompt_template: str | None = "Hello {{ workspace }}",
    template_file: Path | None = None,
    workspace: Path | None = None,
    variables: dict | None = None,
    movement: int = 1,
    voice: int | None = None,
    voice_count: int = 1,
    instrument_name: str = "claude-code",
    prelude: list[InjectionItem] | None = None,
    cadenza: list[InjectionItem] | None = None,
    validations: list[ValidationRule] | None = None,
    timeout_seconds: float = 300.0,
) -> Sheet:
    """Create a Sheet for testing."""
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
        timeout_seconds=timeout_seconds,
    )


def _make_prompt_config(
    *,
    variables: dict | None = None,
    stakes: str | None = None,
    thinking_method: str | None = None,
) -> PromptConfig:
    """Create a PromptConfig for testing."""
    return PromptConfig(
        variables=variables or {},
        stakes=stakes,
        thinking_method=thinking_method,
    )


def _make_context(
    *,
    attempt_number: int = 1,
    mode: AttemptMode = AttemptMode.NORMAL,
    completion_prompt_suffix: str | None = None,
) -> AttemptContext:
    """Create an AttemptContext for testing."""
    return AttemptContext(
        attempt_number=attempt_number,
        mode=mode,
        completion_prompt_suffix=completion_prompt_suffix,
    )


# =========================================================================
# Basic rendering
# =========================================================================


class TestBasicRendering:
    """Test basic template rendering through the prompt renderer."""

    def test_renders_inline_template_with_variables(self) -> None:
        """The most basic case: inline template with {{ workspace }}."""
        sheet = _make_sheet(prompt_template="Work in {{ workspace }}")
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=5,
            total_stages=5,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert isinstance(result, RenderedPrompt)
        assert "Work in /tmp/test-workspace" in result.prompt

    def test_renders_sheet_variables(self) -> None:
        """Sheet-level variables are available in the template."""
        sheet = _make_sheet(
            prompt_template="Focus on {{ focus_area }}",
            variables={"focus_area": "authentication"},
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Focus on authentication" in result.prompt

    def test_config_variables_merged_with_sheet_variables(self) -> None:
        """Global config variables are merged, sheet variables take precedence."""
        sheet = _make_sheet(
            prompt_template="{{ global_var }} and {{ sheet_var }}",
            variables={"sheet_var": "from_sheet"},
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(variables={"global_var": "from_config"}),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "from_config and from_sheet" in result.prompt

    def test_no_template_produces_default_prompt(self) -> None:
        """When no template exists, a default prompt is produced."""
        sheet = _make_sheet(prompt_template=None)
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=5,
            total_stages=5,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "sheet 1" in result.prompt.lower()

    def test_template_file_loaded_and_rendered(self, tmp_path: Path) -> None:
        """Template file is read and rendered with variables."""
        template_file = tmp_path / "template.txt"
        template_file.write_text("Build at {{ workspace }}")

        sheet = _make_sheet(
            prompt_template=None,
            template_file=template_file,
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Build at /tmp/test-workspace" in result.prompt

    def test_sheet_num_and_total_available_in_template(self) -> None:
        """Built-in variables sheet_num, total_sheets are available."""
        sheet = _make_sheet(
            num=3,
            prompt_template="Sheet {{ sheet_num }} of {{ total_sheets }}",
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=10,
            total_stages=10,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Sheet 3 of 10" in result.prompt

    def test_fan_out_aliases_available(self) -> None:
        """Movement/voice aliases are available in template."""
        sheet = _make_sheet(
            num=5,
            movement=2,
            voice=3,
            voice_count=4,
            prompt_template=(
                "Movement {{ movement }} voice {{ voice }} of {{ voice_count }}"
            ),
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=20,
            total_stages=5,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Movement 2 voice 3 of 4" in result.prompt


# =========================================================================
# Preamble
# =========================================================================


class TestPreamble:
    """Test preamble generation."""

    def test_first_run_preamble(self) -> None:
        """First attempt generates standard preamble."""
        sheet = _make_sheet()
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=10,
            total_stages=10,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context(attempt_number=1))

        assert "<marianne-preamble>" in result.preamble
        assert "sheet 1 of 10" in result.preamble
        assert "RETRY" not in result.preamble

    def test_retry_preamble(self) -> None:
        """Retry attempt generates retry preamble."""
        sheet = _make_sheet()
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=10,
            total_stages=10,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context(attempt_number=3))

        assert "RETRY #2" in result.preamble

    def test_parallel_preamble(self) -> None:
        """Parallel mode shows concurrency warning."""
        sheet = _make_sheet()
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=10,
            total_stages=10,
            parallel_enabled=True,
        )
        result = renderer.render(sheet, _make_context())

        assert "concurrently" in result.preamble


# =========================================================================
# Injection Resolution
# =========================================================================


class TestInjectionResolution:
    """Test prelude/cadenza file injection."""

    def test_prelude_context_injected(self, tmp_path: Path) -> None:
        """Prelude context files are read and injected."""
        context_file = tmp_path / "context.md"
        context_file.write_text("# Project Context\nThis is important.")

        sheet = _make_sheet(
            workspace=tmp_path,
            prelude=[
                InjectionItem(
                    file=str(context_file),
                    **{"as": InjectionCategory.CONTEXT},
                )
            ],
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Project Context" in result.prompt
        assert "Injected Context" in result.prompt

    def test_prelude_skill_injected(self, tmp_path: Path) -> None:
        """Prelude skill files are injected in the skills section."""
        skill_file = tmp_path / "skill.md"
        skill_file.write_text("Use TDD for all implementations.")

        sheet = _make_sheet(
            workspace=tmp_path,
            prelude=[
                InjectionItem(
                    file=str(skill_file),
                    **{"as": InjectionCategory.SKILL},
                )
            ],
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Use TDD" in result.prompt
        assert "Injected Skills" in result.prompt

    def test_cadenza_injected(self, tmp_path: Path) -> None:
        """Cadenza (per-sheet) files are injected."""
        cadenza_file = tmp_path / "notes.md"
        cadenza_file.write_text("Special notes for this sheet.")

        sheet = _make_sheet(
            workspace=tmp_path,
            cadenza=[
                InjectionItem(
                    file=str(cadenza_file),
                    **{"as": InjectionCategory.CONTEXT},
                )
            ],
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Special notes" in result.prompt

    def test_jinja_path_expansion(self, tmp_path: Path) -> None:
        """Jinja2 templates in injection file paths are expanded."""
        context_file = tmp_path / "context.md"
        context_file.write_text("Expanded path content.")

        sheet = _make_sheet(
            workspace=tmp_path,
            prelude=[
                InjectionItem(
                    file="{{ workspace }}/context.md",
                    **{"as": InjectionCategory.CONTEXT},
                )
            ],
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Expanded path content" in result.prompt

    def test_missing_context_file_skipped(self, tmp_path: Path) -> None:
        """Missing context files are skipped gracefully."""
        sheet = _make_sheet(
            workspace=tmp_path,
            prelude=[
                InjectionItem(
                    file=str(tmp_path / "nonexistent.md"),
                    **{"as": InjectionCategory.CONTEXT},
                )
            ],
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        # Should not raise
        result = renderer.render(sheet, _make_context())
        assert "Injected Context" not in result.prompt


# =========================================================================
# Validation Requirements
# =========================================================================


class TestValidationRequirements:
    """Test validation rules injection into prompt."""

    def test_validation_rules_injected(self) -> None:
        """Validation rules appear as success requirements."""
        sheet = _make_sheet(
            validations=[
                ValidationRule(
                    type="file_exists",
                    path="{workspace}/output.md",
                    description="Output file",
                ),
            ],
        )
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Success Requirements" in result.prompt
        assert "Output file" in result.prompt

    def test_no_validations_no_section(self) -> None:
        """When there are no validations, no requirements section appears."""
        sheet = _make_sheet(validations=[])
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Success Requirements" not in result.prompt


# =========================================================================
# Completion Mode
# =========================================================================


class TestCompletionMode:
    """Test completion mode prompt handling."""

    def test_completion_suffix_appended(self) -> None:
        """Completion prompt suffix is appended in completion mode."""
        sheet = _make_sheet(prompt_template="Do the work")
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(
            sheet,
            _make_context(
                attempt_number=2,
                mode=AttemptMode.COMPLETION,
                completion_prompt_suffix="Fix the remaining validations.",
            ),
        )

        assert "Do the work" in result.prompt
        assert "Fix the remaining validations" in result.prompt

    def test_normal_mode_no_suffix(self) -> None:
        """Normal mode does not append completion suffix."""
        sheet = _make_sheet(prompt_template="Do the work")
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert result.prompt.startswith("Do the work")


# =========================================================================
# Optional Layers
# =========================================================================


class TestOptionalLayers:
    """Test optional prompt layers (patterns, specs, failure history)."""

    def test_learned_patterns_injected(self) -> None:
        """Learned patterns appear in the prompt."""
        sheet = _make_sheet()
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(
            sheet,
            _make_context(),
            patterns=["Always write tests first", "Use type hints"],
        )

        assert "Learned Patterns" in result.prompt
        assert "Always write tests first" in result.prompt

    def test_no_patterns_no_section(self) -> None:
        """When no patterns provided, no patterns section appears."""
        sheet = _make_sheet()
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Learned Patterns" not in result.prompt

    def test_stakes_available_in_template(self) -> None:
        """Stakes from PromptConfig are available as template variable."""
        sheet = _make_sheet(prompt_template="Stakes: {{ stakes }}")
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(stakes="Production deployment"),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert "Stakes: Production deployment" in result.prompt


# =========================================================================
# RenderedPrompt dataclass
# =========================================================================


class TestRenderedPrompt:
    """Test the RenderedPrompt output type."""

    def test_has_prompt_and_preamble(self) -> None:
        """RenderedPrompt has both prompt and preamble fields."""
        rp = RenderedPrompt(prompt="test", preamble="preamble")
        assert rp.prompt == "test"
        assert rp.preamble == "preamble"

    def test_full_render_returns_rendered_prompt(self) -> None:
        """Full render produces a RenderedPrompt with both fields."""
        sheet = _make_sheet()
        renderer = PromptRenderer(
            prompt_config=_make_prompt_config(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        result = renderer.render(sheet, _make_context())

        assert isinstance(result, RenderedPrompt)
        assert len(result.prompt) > 0
        assert len(result.preamble) > 0
        assert "<marianne-preamble>" in result.preamble
