"""Sheet entity model — the first-class execution unit in Mozart.

A Sheet carries everything a musician needs to execute: identity, instrument,
prompt, context injection, validations, and timeout. Sheets are constructed
at setup time from the parsed score and handed to the baton for dispatch.

The music metaphor: sheet music contains everything the musician needs to
play their part — the notes, the key, the tempo, the dynamics. They don't
need to see the full score. They need their sheet.

What's NOT on the Sheet: dependencies, skip_when, retry policy, execution
state, cost limits. Those belong to the baton and state systems. The Sheet
is execution data; the baton owns coordination logic.

Prompt rendering is deferred. The template stays unrendered because
cross-sheet context ({{ previous_outputs[2] }}) only exists after earlier
sheets complete. The baton renders at dispatch time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mozart.core.config.execution import ValidationRule
from mozart.core.config.job import InjectionItem


class Sheet(BaseModel):
    """A fully self-contained execution unit. Everything a musician needs.

    Constructed at setup time from the parsed score YAML. Immutable after
    construction — the baton dispatches Sheet entities as-is.

    Identity:
        num: Concrete sheet number (1-indexed, globally unique within a job)
        movement: Which movement this sheet belongs to (was: stage)
        voice: Which voice in a harmonized movement (was: instance), None if solo
        voice_count: Total voices in this movement (was: fan_count)

    Execution:
        instrument_name: Resolved instrument name (e.g. 'gemini-cli')
        instrument_config: Overrides for instrument defaults (model, timeout, etc.)
        prompt_template: Raw Jinja2 template (rendered at dispatch time)
        template_file: External template file (alternative to inline)
        variables: Static template variables from the score
        timeout_seconds: Per-sheet execution timeout

    Context injection:
        prelude: Shared context injected into all sheets
        cadenza: Per-sheet context injection
        prompt_extensions: Additional prompt directives

    Acceptance criteria:
        validations: What "done" means for this sheet
    """

    # --- Identity ---
    num: int = Field(ge=1, description="Concrete sheet number (1-indexed)")
    movement: int = Field(ge=1, description="Movement number (was: stage)")
    voice: int | None = Field(
        default=None,
        ge=1,
        description="Voice within a harmonized movement (was: instance). "
        "None for solo movements.",
    )
    voice_count: int = Field(
        ge=1,
        description="Total voices in this movement (was: fan_count)",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable label for status display",
    )
    workspace: Path = Field(description="Execution working directory")

    # --- Instrument ---
    instrument_name: str = Field(
        min_length=1,
        description="Resolved instrument name, e.g. 'claude-code', 'gemini-cli'",
    )
    instrument_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Instrument-specific overrides (model, timeout, etc.)",
    )

    # --- Prompt ---
    prompt_template: str | None = Field(
        default=None,
        description="Raw Jinja2 template (rendered at dispatch time by the baton)",
    )
    template_file: Path | None = Field(
        default=None,
        description="External template file (alternative to inline template)",
    )
    variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Static template variables from the score",
    )

    # --- Context Injection ---
    prelude: list[InjectionItem] = Field(
        default_factory=list,
        description="Shared context injected into all sheets (prelude items)",
    )
    cadenza: list[InjectionItem] = Field(
        default_factory=list,
        description="Per-sheet context injection (cadenza items)",
    )
    prompt_extensions: list[str] = Field(
        default_factory=list,
        description="Additional prompt directives (inline text or file paths)",
    )

    # --- Acceptance Criteria ---
    validations: list[ValidationRule] = Field(
        default_factory=list,
        description="Validation rules — what 'done' means for this sheet",
    )

    # --- Timeout ---
    timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        description="Per-sheet execution timeout in seconds",
    )

    def template_variables(
        self,
        total_sheets: int,
        total_movements: int,
    ) -> dict[str, Any]:
        """Build the full template variable dict for Jinja2 rendering.

        Merges built-in variables (identity, workspace, instrument) with
        the score's custom variables. Built-in variables take precedence
        over custom variables to prevent accidental overrides.

        New and old terminology aliases are both provided — old names
        (stage, instance, fan_count, total_stages) are kept forever for
        backward compatibility.

        Args:
            total_sheets: Total concrete sheet count in the job.
            total_movements: Total movement count in the job.

        Returns:
            Dict of all template variables for Jinja2 rendering.
        """
        # Start with custom variables (lowest precedence)
        tvars: dict[str, Any] = dict(self.variables)

        # Built-in variables (override custom)
        tvars.update({
            # Core identity
            "sheet_num": self.num,
            "total_sheets": total_sheets,
            "workspace": str(self.workspace),
            "instrument_name": self.instrument_name,
            # New terminology
            "movement": self.movement,
            "voice": self.voice,
            "voice_count": self.voice_count,
            "total_movements": total_movements,
            # Old terminology (aliases — kept forever)
            "stage": self.movement,
            "instance": self.voice,
            "fan_count": self.voice_count,
            "total_stages": total_movements,
        })

        return tvars
