"""Sheet entity model — the first-class execution unit in Marianne.

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
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from marianne.core.config.execution import ValidationRule
from marianne.core.config.job import InjectionItem

if TYPE_CHECKING:
    from marianne.core.config.job import JobConfig


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
    instrument_fallbacks: list[str] = Field(
        default_factory=list,
        description="Resolved fallback instrument chain for this sheet. "
        "Tried in order when the primary instrument is unavailable or "
        "rate-limited to exhaustion.",
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


def build_sheets(config: JobConfig) -> list[Sheet]:
    """Construct Sheet entities from a JobConfig.

    This bridges the old scattered-dict model (SheetConfig with separate
    dicts for descriptions, cadenzas, prompt_extensions, etc.) to the new
    first-class Sheet entity. Each concrete sheet in the job gets a fully
    self-contained Sheet object.

    The music metaphor: this is the librarian distributing parts to musicians
    before the concert. Each musician gets their own sheet with everything
    they need — they don't need to consult the full score.

    Args:
        config: Parsed and validated JobConfig from the score YAML.

    Returns:
        List of Sheet entities, one per concrete sheet, in sheet_num order.
    """
    sheets: list[Sheet] = []
    total_sheets = config.sheet.total_sheets

    # Pre-build reverse lookup for instrument_map: sheet_num -> instrument_name
    instrument_map_lookup: dict[int, str] = {}
    for instr_name, sheet_nums in config.sheet.instrument_map.items():
        for sn in sheet_nums:
            instrument_map_lookup[sn] = instr_name

    for sheet_num in range(1, total_sheets + 1):
        # --- Identity ---
        fan_meta = config.sheet.get_fan_out_metadata(sheet_num)
        movement = fan_meta.stage
        voice: int | None = fan_meta.instance if fan_meta.fan_count > 1 else None
        voice_count = fan_meta.fan_count

        description = config.sheet.descriptions.get(sheet_num)

        # --- Instrument resolution chain ---
        # Priority: per_sheet > instrument_map > movement > score instrument > backend.type
        instrument_config: dict[str, Any] = dict(config.instrument_config)

        # Walk the resolution chain from highest to lowest priority
        resolved_instrument: str | None = None

        if sheet_num in config.sheet.per_sheet_instruments:
            # Highest priority: explicit per-sheet assignment
            resolved_instrument = config.sheet.per_sheet_instruments[sheet_num]
        elif sheet_num in instrument_map_lookup:
            # Batch assignment via instrument_map
            resolved_instrument = instrument_map_lookup[sheet_num]
        else:
            # Check movement-level instrument and config
            if movement in config.movements:
                movement_def = config.movements[movement]
                if movement_def.instrument is not None:
                    resolved_instrument = movement_def.instrument
                # Movement-level instrument_config merges with score-level
                # regardless of whether the movement also overrides the
                # instrument name. A score author should be able to say
                # "same instrument, different model" without repeating
                # the instrument name. F-150: this was gated behind
                # instrument is not None, silently dropping config-only
                # movement overrides.
                if movement_def.instrument_config:
                    instrument_config = {
                        **instrument_config,
                        **movement_def.instrument_config,
                    }
            # Fall through to score-level or backend default
            if resolved_instrument is None:
                if config.instrument is not None:
                    resolved_instrument = config.instrument
                else:
                    resolved_instrument = config.backend.type

        # Resolve score-level instrument aliases to profile names.
        # If the resolved name matches a key in config.instruments, replace
        # it with the profile name and merge the InstrumentDef config.
        if resolved_instrument in config.instruments:
            instrument_def = config.instruments[resolved_instrument]
            resolved_instrument = instrument_def.profile
            if instrument_def.config:
                instrument_config = {**instrument_config, **instrument_def.config}

        instrument_name: str = resolved_instrument

        # Per-sheet instrument config overrides everything
        if sheet_num in config.sheet.per_sheet_instrument_config:
            instrument_config = {
                **instrument_config,
                **config.sheet.per_sheet_instrument_config[sheet_num],
            }

        # --- Timeout ---
        # Resolution: sheet_overrides.timeout_seconds > timeout_overrides > backend.timeout_seconds
        timeout = config.backend.timeout_seconds
        if sheet_num in config.backend.timeout_overrides:
            timeout = config.backend.timeout_overrides[sheet_num]
        if sheet_num in config.backend.sheet_overrides:
            override = config.backend.sheet_overrides[sheet_num]
            if override.timeout_seconds is not None:
                timeout = override.timeout_seconds

        # --- Prompt ---
        prompt_template = config.prompt.template
        template_file = config.prompt.template_file
        variables = dict(config.prompt.variables)

        # --- Context Injection ---
        prelude = list(config.sheet.prelude)
        cadenza = list(config.sheet.cadenzas.get(sheet_num, []))

        # Prompt extensions: score-level + per-sheet
        extensions = list(config.prompt.prompt_extensions)
        per_sheet_ext = config.sheet.prompt_extensions.get(sheet_num, [])
        extensions.extend(per_sheet_ext)

        # --- Validations ---
        # Score-level validations apply to all sheets
        validations = list(config.validations)

        # --- Instrument Fallbacks ---
        # Resolution: per_sheet > movement > score-level
        # Per-sheet replaces (does not merge) inherited chain.
        if sheet_num in config.sheet.per_sheet_fallbacks:
            fallbacks = list(config.sheet.per_sheet_fallbacks[sheet_num])
        elif movement in config.movements and config.movements[movement].instrument_fallbacks:
            fallbacks = list(config.movements[movement].instrument_fallbacks)
        else:
            fallbacks = list(config.instrument_fallbacks)

        sheets.append(
            Sheet(
                num=sheet_num,
                movement=movement,
                voice=voice,
                voice_count=voice_count,
                description=description,
                workspace=config.workspace,
                instrument_name=instrument_name,
                instrument_config=instrument_config,
                instrument_fallbacks=fallbacks,
                prompt_template=prompt_template,
                template_file=template_file,
                variables=variables,
                prelude=prelude,
                cadenza=cadenza,
                prompt_extensions=extensions,
                validations=validations,
                timeout_seconds=timeout,
            )
        )

    return sheets
