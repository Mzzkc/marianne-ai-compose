"""Job and sheet configuration models.

Defines the top-level JobConfig, SheetConfig, and PromptConfig models.
"""

from __future__ import annotations

import warnings
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

if TYPE_CHECKING:
    from mozart.core.fan_out import FanOutMetadata  # noqa: F401

from mozart.core.config.backend import BackendConfig, BridgeConfig
from mozart.core.config.execution import (
    CircuitBreakerConfig,
    CostLimitConfig,
    ParallelConfig,
    RateLimitConfig,
    RetryConfig,
    SkipWhenCommand,
    StaleDetectionConfig,
    ValidationRule,
)
from mozart.core.config.learning import (
    CheckpointConfig,
    GroundingConfig,
    LearningConfig,
)
from mozart.core.config.orchestration import (
    ConcertConfig,
    ConductorConfig,
    NotificationConfig,
    PostSuccessHookConfig,
)
from mozart.core.config.spec import SpecCorpusConfig
from mozart.core.config.workspace import (
    AIReviewConfig,
    CrossSheetConfig,
    FeedbackConfig,
    IsolationConfig,
    LogConfig,
    WorkspaceLifecycleConfig,
)


class InjectionCategory(str, Enum):
    """Category for injected content in prelude/cadenza system.

    Determines WHERE in the prompt the injected content appears:
    - context: Background knowledge, after template body
    - skill: Methodology/instructions, after preamble
    - tool: Available actions, after preamble
    """

    CONTEXT = "context"
    SKILL = "skill"
    TOOL = "tool"


class InjectionItem(BaseModel):
    """A single injection item referencing a file with a category.

    Used in prelude (all sheets) and cadenzas (per-sheet) to inject
    file content into prompts at category-appropriate locations.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    file: str = Field(
        description="Path to the file to inject. Supports Jinja templating "
        "(e.g., '{{ workspace }}/context.md').",
    )
    as_: InjectionCategory = Field(
        alias="as",
        description="Category determining prompt placement: "
        "'context' (background), 'skill' (methodology), or 'tool' (actions).",
    )


class InstrumentDef(BaseModel):
    """A named instrument definition within a score.

    Allows a score to declare reusable instrument aliases that reference
    registered instrument profiles with optional configuration overrides.
    These aliases can then be referenced by name in per-sheet or per-movement
    instrument assignments.

    Example YAML::

        instruments:
          fast-writer:
            profile: gemini-cli
            config:
              model: gemini-2.5-flash
              timeout_seconds: 300
          deep-thinker:
            profile: claude-code
            config:
              timeout_seconds: 3600
    """

    model_config = ConfigDict(extra="forbid")

    profile: str = Field(
        min_length=1,
        description="Name of the registered instrument profile, e.g. 'gemini-cli'",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Configuration overrides for this instrument definition. "
        "Flat key-value pairs merged with the profile's defaults.",
    )


class MovementDef(BaseModel):
    """Declaration of a movement within a score.

    Movements are sequential execution phases. Each movement can specify
    a name, an instrument (overriding the score default), instrument config,
    and a voice count (shorthand for fan-out).

    Example YAML::

        movements:
          1:
            name: Planning
            instrument: claude-code
          2:
            name: Implementation
            voices: 3
            instrument: gemini-cli
          3:
            name: Review
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        description="Human-readable name for this movement",
    )
    instrument: str | None = Field(
        default=None,
        min_length=1,
        description="Instrument for all sheets in this movement. "
        "Overrides the score-level instrument: but is overridden "
        "by per-sheet assignments.",
    )
    instrument_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Instrument configuration overrides for this movement",
    )
    voices: int | None = Field(
        default=None,
        ge=1,
        description="Number of parallel voices in this movement. "
        "Shorthand for fan_out: {N: voices}.",
    )


class SheetConfig(BaseModel):
    """Configuration for sheet processing.

    In Mozart's musical theme, a composition is divided into sheets,
    each containing a portion of the work to be performed.

    Fan-out support: When ``fan_out`` is specified, stages are expanded into
    concrete sheets at parse time. For example, ``total_items=7, fan_out={2: 3}``
    produces 9 concrete sheets (stage 2 instantiated 3 times). After expansion,
    ``total_items`` and ``dependencies`` reflect expanded values, and ``fan_out``
    is cleared to ``{}`` to prevent re-expansion on resume.
    """

    model_config = ConfigDict(extra="forbid")

    size: int = Field(ge=1, description="Number of items per sheet")
    total_items: int = Field(ge=1, description="Total number of items to process")
    start_item: int = Field(default=1, ge=1, description="First item number (1-indexed)")

    # Sheet descriptions for status display (GH#75)
    descriptions: dict[int, str] = Field(
        default_factory=dict,
        description=(
            "Human-readable labels for sheets. Map of sheet_num -> description. "
            "Displayed in 'mozart status' output. Sheets without entries show no description. "
            "Example: {1: 'Setup environment', 2: 'Build project', 3: 'Run tests'}"
        ),
    )

    # Sheet dependencies (v17 evolution: Sheet Dependency DAG)
    dependencies: dict[int, list[int]] = Field(
        default_factory=dict,
        description=(
            "Sheet dependency declarations. Map of sheet_num -> list of prerequisite sheets. "
            "Example: {3: [1, 2], 4: [3]} means sheet 3 needs 1 and 2, sheet 4 needs 3. "
            "Sheets without entries are independent (can run immediately or after config order)."
        ),
    )

    # Per-sheet spec corpus tag filtering (Phase 1: Spec Corpus Pipeline)
    spec_tags: dict[int, list[str]] = Field(
        default_factory=dict,
        description=(
            "Per-sheet spec tag filters. Map of sheet_num -> list of tags. "
            "Only spec fragments matching at least one tag are injected for that sheet. "
            "Sheets without entries receive all fragments (no filtering). "
            "Example: {1: ['goals', 'safety'], 3: ['constraints']}"
        ),
    )

    # Conditional execution: skip sheets based on runtime state (GH#13)
    skip_when: dict[int, str] = Field(
        default_factory=dict,
        description=(
            "Conditional skip rules. Map of sheet_num -> condition expression. "
            "Expression is evaluated as a Python expression with access to 'sheets' dict "
            "(sheet_num -> SheetState) and 'job' (CheckpointState). "
            "If the expression evaluates to truthy, the sheet is SKIPPED. "
            "Example: {5: \"sheets.get(3) and sheets[3].validation_passed\"} "
            "skips sheet 5 when sheet 3's validations passed (only run on failure)."
        ),
    )

    # Per-sheet prompt extensions (GH#76) — additional directives for specific sheets
    prompt_extensions: dict[int, list[str]] = Field(
        default_factory=dict,
        description=(
            "Per-sheet prompt extensions. Map of sheet_num -> list of extension strings. "
            "Each entry is inline text or a file path (.md/.txt). "
            "Applied in addition to score-level prompt.prompt_extensions. "
            "Example: {2: ['Review the code carefully before making changes']}"
        ),
    )

    # Prelude & Cadenza context injection (GH#53)
    prelude: list[InjectionItem] = Field(
        default_factory=list,
        description=(
            "Shared context injected into ALL sheets. Each item references a file "
            "and a category ('context', 'skill', or 'tool'). File paths support "
            "Jinja templating. Files are read at sheet execution time."
        ),
    )

    cadenzas: dict[int, list[InjectionItem]] = Field(
        default_factory=dict,
        description=(
            "Per-sheet context injections. Map of sheet_num -> list of InjectionItems. "
            "Applied in addition to prelude items for the specified sheet. "
            "Example: {2: [{file: 'extra-context.md', as: 'context'}]}"
        ),
    )

    # Command-based conditional execution (GH#71)
    skip_when_command: dict[int, SkipWhenCommand] = Field(
        default_factory=dict,
        description=(
            "Command-based conditional skip rules. Map of sheet_num -> SkipWhenCommand. "
            "The command is run via shell; exit 0 = skip the sheet, non-zero = run it. "
            "Supports {workspace} template expansion in the command string. "
            "On timeout or error, the sheet runs (fail-open). "
            "Example: {8: {command: 'grep -q \"PHASES: 1\" plan.md', description: 'Skip phase 2'}}"
        ),
    )

    # Fan-out: parameterized stage instantiation
    fan_out: dict[int, int] = Field(
        default_factory=dict,
        description=(
            "Fan-out declarations. Map of stage_num -> instance count. "
            "Stages not listed default to 1 instance. "
            "Example: {2: 3, 4: 3} creates 3 parallel instances of stages 2 and 4. "
            "Requires size=1 and start_item=1. Cleared after expansion."
        ),
    )

    fan_out_stage_map: dict[int, dict[str, int]] | None = Field(
        default=None,
        description=(
            "Per-sheet fan-out metadata, populated by expansion. "
            "Map of sheet_num -> {stage, instance, fan_count}. "
            "Survives serialization for resume support."
        ),
    )

    # Per-sheet instrument assignment (M4: multi-instrument support)
    per_sheet_instruments: dict[int, str] = Field(
        default_factory=dict,
        description=(
            "Per-sheet instrument overrides. Map of sheet_num -> instrument name. "
            "Overrides score-level, movement-level, and instrument_map assignments. "
            "Example: {3: 'gemini-cli', 5: 'codex-cli'}"
        ),
    )

    per_sheet_instrument_config: dict[int, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Per-sheet instrument configuration overrides. "
            "Map of sheet_num -> config dict. "
            "Merged with the resolved instrument's defaults for that sheet. "
            "Example: {3: {model: 'gemini-2.5-flash', timeout_seconds: 300}}"
        ),
    )

    # Batch instrument assignment (M4: convenience for large scores)
    instrument_map: dict[str, list[int]] = Field(
        default_factory=dict,
        description=(
            "Batch instrument assignment. Map of instrument_name -> list of sheet numbers. "
            "Overrides score-level instrument for listed sheets. "
            "Overridden by per_sheet_instruments for specific sheets. "
            "Example: {'gemini-cli': [1, 2, 3], 'claude-code': [4, 5, 6]}"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def strip_computed_fields(cls, data: Any) -> Any:
        """Strip computed properties that users may include in YAML.

        total_sheets is computed from size/total_items, not configurable.
        Accept it silently for backward compatibility — rejecting it would
        break existing scores that include it.
        """
        if isinstance(data, dict) and "total_sheets" in data:
            data.pop("total_sheets")
        return data

    @field_validator("per_sheet_instruments")
    @classmethod
    def validate_per_sheet_instruments(
        cls, v: dict[int, str],
    ) -> dict[int, str]:
        """Validate per-sheet instrument assignments."""
        for sheet_num, instrument in v.items():
            if not isinstance(sheet_num, int) or sheet_num < 1:
                raise ValueError(
                    f"Per-sheet instrument key must be a positive integer, "
                    f"got {sheet_num}"
                )
            if not instrument:
                raise ValueError(
                    f"Per-sheet instrument name for sheet {sheet_num} "
                    f"must not be empty"
                )
        return v

    @field_validator("instrument_map")
    @classmethod
    def validate_instrument_map(
        cls, v: dict[str, list[int]],
    ) -> dict[str, list[int]]:
        """Validate instrument_map: no duplicate sheets, valid names."""
        seen_sheets: dict[int, str] = {}
        for instrument, sheets in v.items():
            if not instrument:
                raise ValueError(
                    "Instrument name in instrument_map must not be empty"
                )
            for sheet_num in sheets:
                if not isinstance(sheet_num, int) or sheet_num < 1:
                    raise ValueError(
                        f"Sheet number in instrument_map must be a positive "
                        f"integer, got {sheet_num} for instrument '{instrument}'"
                    )
                if sheet_num in seen_sheets:
                    raise ValueError(
                        f"Sheet {sheet_num} assigned to multiple instruments "
                        f"in instrument_map: '{seen_sheets[sheet_num]}' and "
                        f"'{instrument}'"
                    )
                seen_sheets[sheet_num] = instrument
        return v

    @property
    def total_sheets(self) -> int:
        """Calculate total number of sheets."""
        return (self.total_items - self.start_item + 1 + self.size - 1) // self.size

    @property
    def total_stages(self) -> int:
        """Return the original stage count.

        After fan-out expansion, total_items reflects expanded sheet count.
        total_stages preserves the original logical stage count from fan_out_stage_map.
        When no fan-out was used, total_stages == total_sheets (identity).
        """
        if self.fan_out_stage_map:
            return max(
                meta["stage"] for meta in self.fan_out_stage_map.values()
            )
        return self.total_sheets

    def get_fan_out_metadata(self, sheet_num: int) -> FanOutMetadata:  # noqa: F821
        """Get fan-out metadata for a specific sheet.

        Args:
            sheet_num: Concrete sheet number (1-indexed).

        Returns:
            FanOutMetadata with stage, instance, and fan_count.
            When no fan-out is configured, returns identity metadata
            (stage=sheet_num, instance=1, fan_count=1).
        """
        from mozart.core.fan_out import FanOutMetadata

        if self.fan_out_stage_map and sheet_num in self.fan_out_stage_map:
            meta = self.fan_out_stage_map[sheet_num]
            return FanOutMetadata(
                stage=meta["stage"],
                instance=meta["instance"],
                fan_count=meta["fan_count"],
            )
        return FanOutMetadata(stage=sheet_num, instance=1, fan_count=1)

    @field_validator("fan_out")
    @classmethod
    def validate_fan_out(cls, v: dict[int, int]) -> dict[int, int]:
        """Validate fan_out field values."""
        for stage, count in v.items():
            if not isinstance(stage, int) or stage < 1:
                raise ValueError(
                    f"Fan-out stage must be positive integer, got {stage}"
                )
            if not isinstance(count, int) or count < 1:
                raise ValueError(
                    f"Fan-out count for stage {stage} must be >= 1, got {count}"
                )
        return v

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(
        cls, v: dict[int, list[int]], info: ValidationInfo
    ) -> dict[int, list[int]]:
        """Validate dependency declarations.

        Note: Full validation (range checks, cycle detection) happens when
        the DependencyDAG is built at runtime, since total_sheets isn't
        available during field validation.
        """
        for sheet_num, deps in v.items():
            if not isinstance(sheet_num, int) or sheet_num < 1:
                raise ValueError(f"Sheet number must be positive integer, got {sheet_num}")
            if not isinstance(deps, list):
                raise ValueError(f"Dependencies for sheet {sheet_num} must be a list")
            for dep in deps:
                if not isinstance(dep, int) or dep < 1:
                    raise ValueError(
                        f"Dependency must be positive integer, got {dep} for sheet {sheet_num}"
                    )
                if dep == sheet_num:
                    raise ValueError(f"Sheet {sheet_num} cannot depend on itself")
        return v

    @model_validator(mode="after")
    def expand_fan_out_config(self) -> SheetConfig:
        """Expand fan_out declarations into concrete sheet assignments.

        This runs after field validators. When fan_out is non-empty:
        1. Validates constraints (size=1, start_item=1)
        2. Calls expand_fan_out() to compute concrete sheet assignments
        3. Overwrites total_items and dependencies with expanded values
        4. Stores metadata in fan_out_stage_map for resume support
        5. Clears fan_out={} to prevent re-expansion on resume
        """
        if not self.fan_out:
            return self

        # Enforce constraints for fan-out
        if self.size != 1:
            raise ValueError(
                f"fan_out requires size=1, got size={self.size}. "
                "Each stage must map to exactly one sheet for fan-out to work."
            )
        if self.start_item != 1:
            raise ValueError(
                f"fan_out requires start_item=1, got start_item={self.start_item}. "
                "Fan-out stages are 1-indexed from the beginning."
            )

        from mozart.core.fan_out import expand_fan_out

        expansion = expand_fan_out(
            total_stages=self.total_items,
            fan_out=self.fan_out,
            stage_dependencies=self.dependencies,
        )

        # Overwrite with expanded values
        self.total_items = expansion.total_sheets
        self.dependencies = expansion.expanded_dependencies

        # Expand skip_when: stage-keyed → sheet-keyed
        if self.skip_when:
            expanded_skip_when: dict[int, str] = {}
            for stage, expr in self.skip_when.items():
                for sheet_num in expansion.stage_sheets.get(stage, [stage]):
                    expanded_skip_when[sheet_num] = expr
            self.skip_when = expanded_skip_when

        # Expand skip_when_command: stage-keyed → sheet-keyed
        if self.skip_when_command:
            expanded_skip_when_command: dict[int, SkipWhenCommand] = {}
            for stage, cmd in self.skip_when_command.items():
                for sheet_num in expansion.stage_sheets.get(stage, [stage]):
                    expanded_skip_when_command[sheet_num] = cmd
            self.skip_when_command = expanded_skip_when_command

        # Store serializable metadata for resume
        self.fan_out_stage_map = {
            sheet_num: {
                "stage": meta.stage,
                "instance": meta.instance,
                "fan_count": meta.fan_count,
            }
            for sheet_num, meta in expansion.sheet_metadata.items()
        }

        # Clear fan_out to prevent re-expansion on resume
        self.fan_out = {}

        return self

    @model_validator(mode="after")
    def validate_dependency_range(self) -> SheetConfig:
        """Validate that dependency sheet numbers are within the valid range.

        Runs after fan-out expansion so total_sheets reflects the final count.
        """
        if not self.dependencies:
            return self
        max_sheet = self.total_sheets
        for sheet_num, deps in self.dependencies.items():
            if sheet_num < 1 or sheet_num > max_sheet:
                raise ValueError(
                    f"Dependency key sheet {sheet_num} is out of range "
                    f"(valid: 1-{max_sheet})"
                )
            for dep in deps:
                if dep < 1 or dep > max_sheet:
                    raise ValueError(
                        f"Sheet {sheet_num} depends on sheet {dep}, "
                        f"which is out of range (valid: 1-{max_sheet})"
                    )
        return self


class PromptConfig(BaseModel):
    """Configuration for prompt templating."""

    model_config = ConfigDict(extra="forbid")

    template: str | None = Field(
        default=None,
        description="Inline Jinja2 template",
    )
    template_file: Path | None = Field(
        default=None,
        description="Path to external .j2 template file",
    )
    variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Static variables available in template",
    )
    stakes: str | None = Field(
        default=None,
        description="Motivational stakes section to append",
    )
    thinking_method: str | None = Field(
        default=None,
        description="Thinking methodology to inject into prompt",
    )
    # Prompt extension points (GH#76) — additional directives injected after
    # the default preamble. Can be inline text or file paths.
    prompt_extensions: list[str] = Field(
        default_factory=list,
        description=(
            "Additional prompt directives applied to all sheets in this score. "
            "Each entry is either inline text or a file path ending in .md/.txt. "
            "File paths are resolved relative to the config file location. "
            "Extensions are injected after the Mozart default preamble."
        ),
    )

    @model_validator(mode="after")
    def at_least_one_template(self) -> PromptConfig:
        """Warn when no template source is provided (falls back to default prompt)."""
        if self.template is not None and self.template_file is not None:
            raise ValueError(
                "PromptConfig accepts 'template' or 'template_file', not both"
            )
        if self.template is None and self.template_file is None:
            warnings.warn(
                "PromptConfig has neither 'template' nor 'template_file'. "
                "The default preamble prompt will be used.",
                UserWarning,
                stacklevel=2,
            )
        return self


def _prepare_for_yaml(obj: Any) -> Any:
    """Recursively convert Python objects to YAML-safe types.

    Handles Path → str and Enum → value while preserving dict key types
    (including integer keys from YAML variables).
    """
    if isinstance(obj, dict):
        return {k: _prepare_for_yaml(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_prepare_for_yaml(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    return obj


class JobConfig(BaseModel):
    """Complete configuration for an orchestration job."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Unique job name")
    description: str | None = Field(default=None, description="Human-readable description")
    workspace: Path = Field(default=Path("./workspace"), description="Output directory")

    backend: BackendConfig = Field(default_factory=BackendConfig)

    # Instrument plugin system (v1 — coexists with backend:)
    instrument: str | None = Field(
        default=None,
        min_length=1,
        description="Name of a registered instrument profile, e.g. 'gemini-cli'. "
        "Resolved to an InstrumentProfile at job submission. "
        "Mutually exclusive with backend.type (non-default). "
        "When omitted, the backend: field determines execution.",
    )
    instrument_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Score-level overrides for the resolved instrument's defaults. "
        "Flat key-value pairs: {model: gemini-2.5-flash, timeout_seconds: 600}. "
        "Only meaningful when instrument: is set.",
    )

    # Score-level named instrument definitions (M4: multi-instrument support)
    instruments: dict[str, InstrumentDef] = Field(
        default_factory=dict,
        description="Named instrument definitions local to this score. "
        "Each entry declares a reusable alias referencing a registered "
        "instrument profile with optional configuration overrides. "
        "Referenced by name in per-sheet or per-movement instrument: fields.",
    )

    # Movement declarations (M4: movement-level instrument and voice config)
    movements: dict[int, MovementDef] = Field(
        default_factory=dict,
        description="Movement declarations. Map of movement_num -> MovementDef. "
        "Each movement can specify a name, instrument, instrument config, "
        "and voice count. Movement instruments override the score-level "
        "instrument: but are overridden by per-sheet assignments.",
    )

    sheet: SheetConfig
    prompt: PromptConfig
    spec: SpecCorpusConfig = Field(
        default_factory=SpecCorpusConfig,
        description="Specification corpus configuration. "
        "Controls where spec fragments are loaded from and how they are "
        "filtered for injection into agent prompts. Opt-in: set spec.spec_dir "
        "to enable.",
    )

    retry: RetryConfig = Field(default_factory=RetryConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    cost_limits: CostLimitConfig = Field(default_factory=CostLimitConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)
    grounding: GroundingConfig = Field(default_factory=GroundingConfig)
    ai_review: AIReviewConfig = Field(default_factory=AIReviewConfig)
    logging: LogConfig = Field(default_factory=LogConfig)
    workspace_lifecycle: WorkspaceLifecycleConfig = Field(
        default_factory=WorkspaceLifecycleConfig,
        description="Workspace lifecycle management. "
        "Controls archival of workspace files on --fresh runs.",
    )
    isolation: IsolationConfig = Field(
        default_factory=IsolationConfig,
        description="Execution isolation configuration. "
        "Enables parallel-safe job execution via git worktrees.",
    )
    conductor: ConductorConfig = Field(
        default_factory=ConductorConfig,
        description="Conductor identity and preferences. "
        "Identifies who (human or AI) is conducting this job.",
    )
    parallel: ParallelConfig = Field(
        default_factory=ParallelConfig,
        description="Parallel sheet execution configuration. "
        "Enables running independent sheets concurrently.",
    )
    stale_detection: StaleDetectionConfig = Field(
        default_factory=StaleDetectionConfig,
        description="Stale execution detection configuration. "
        "When enabled, fails sheets that produce no output beyond idle_timeout_seconds.",
    )
    checkpoints: CheckpointConfig = Field(
        default_factory=CheckpointConfig,
        description="Proactive checkpoint configuration. "
        "Enables pre-execution approval for configurable triggers.",
    )
    bridge: BridgeConfig | None = Field(
        default=None,
        description="Mozart-Ollama bridge configuration. "
        "Enables Ollama backend with MCP tool support.",
    )
    cross_sheet: CrossSheetConfig | None = Field(
        default=None,
        description="Cross-sheet context configuration. "
        "Enables passing outputs and files between sheets for multi-phase workflows.",
    )
    feedback: FeedbackConfig = Field(
        default_factory=FeedbackConfig,
        description="Developer feedback collection configuration (GH#15). "
        "When enabled, extracts structured feedback from agent output after each sheet.",
    )

    validations: list[ValidationRule] = Field(default_factory=list)
    notifications: list[NotificationConfig] = Field(default_factory=list)

    # Concert orchestration (job chaining)
    on_success: list[PostSuccessHookConfig] = Field(
        default_factory=list,
        description="Hooks to run after successful job completion. "
        "Enables chaining jobs into a Concert.",
    )
    concert: ConcertConfig = Field(
        default_factory=ConcertConfig,
        description="Configuration for concert orchestration (job chaining)",
    )

    state_backend: Literal["json", "sqlite"] = Field(
        default="sqlite",
        description="State storage backend",
    )
    state_path: Path | None = Field(
        default=None,
        description="Path for state storage (default: workspace/.mozart-state)",
    )

    pause_between_sheets_seconds: int = Field(
        default=2,
        ge=0,
        description="Seconds to wait between sheets",
    )

    @field_validator("movements")
    @classmethod
    def _validate_movement_keys(
        cls, v: dict[int, MovementDef],
    ) -> dict[int, MovementDef]:
        """Validate movement numbers are positive integers."""
        for movement_num in v:
            if not isinstance(movement_num, int) or movement_num < 1:
                raise ValueError(
                    f"Movement number must be a positive integer, "
                    f"got {movement_num}"
                )
        return v

    @model_validator(mode="after")
    def _resolve_workspace(self) -> JobConfig:
        """Resolve workspace to an absolute path at construction time (#12/#34).

        This eliminates redundant .resolve() calls scattered across consumers.
        """
        self.workspace = self.workspace.resolve()
        return self

    @model_validator(mode="after")
    def _validate_instrument_backend_coexistence(self) -> JobConfig:
        """Validate mutual exclusion between instrument: and backend.type.

        Per the instrument plugin system design spec:
        - instrument: and backend: are two ways to specify execution
        - If both present (backend.type non-default) → validation error
        - If only backend: → works exactly as today
        - If only instrument: → resolves via profile registry at runtime
        - If neither → defaults to claude_cli

        The backend: field always exists with defaults. The conflict is
        only when the user explicitly sets backend.type to a non-default
        value while also setting instrument:.
        """
        if self.instrument is not None and self.backend.type != "claude_cli":
            raise ValueError(
                f"Cannot specify both 'instrument: {self.instrument}' and "
                f"'backend.type: {self.backend.type}'. Use instrument: for "
                "config-driven instruments, or backend: for native backends. "
                "Not both."
            )
        return self

    @model_validator(mode="after")
    def _warn_parallel_isolation(self) -> JobConfig:
        """Warn when parallel execution and worktree isolation are both enabled (#29).

        Worktree isolation creates a single worktree for the job, but parallel
        sheets share it.  This combination is safe for read-only parallel sheets
        but risky when parallel sheets both write to the worktree.
        """
        if self.parallel.enabled and self.isolation.enabled:
            warnings.warn(
                "parallel.enabled and isolation.enabled are both set. "
                "Parallel sheets will share the same worktree — ensure "
                "they don't write to overlapping paths.",
                UserWarning,
                stacklevel=2,
            )
        return self

    def to_yaml(self, *, exclude_defaults: bool = False) -> str:
        """Serialize this JobConfig to valid score YAML.

        The output is semantically equivalent to the original config:
        ``from_yaml_string(config.to_yaml())`` produces an equivalent config
        (compared via ``model_dump()``). String-level identity with the
        original YAML file is NOT guaranteed because workspace paths are
        resolved to absolute at parse time and fan-out configs are expanded.

        Args:
            exclude_defaults: If True, omit fields that match their default
                values for cleaner output. Defaults to False (lossless).

        Returns:
            A valid YAML string that ``from_yaml_string()`` can parse.
        """
        data = self.model_dump(
            mode="python",
            by_alias=True,
            exclude_defaults=exclude_defaults,
        )
        data = _prepare_for_yaml(data)
        return yaml.dump(
            data,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    @classmethod
    def from_yaml(cls, path: Path) -> JobConfig:
        """Load job configuration from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(
                "The score file is empty or invalid. "
                "A Mozart score requires at minimum: name, sheet, and prompt sections. "
                "See 'mozart validate --help' or the score writing guide for examples."
            )
        # Pre-resolve relative workspace relative to the score file's parent
        # directory, not the current process CWD (#109).  This is critical when
        # the daemon loads a score whose path differs from the daemon's CWD.
        if "workspace" in data:
            ws = Path(str(data["workspace"]))
            if not ws.is_absolute():
                data["workspace"] = str((path.resolve().parent / ws).resolve())
        return cls.model_validate(data)

    @classmethod
    def from_yaml_string(cls, yaml_str: str) -> JobConfig:
        """Load job configuration from a YAML string."""
        data = yaml.safe_load(yaml_str)
        if not isinstance(data, dict):
            raise ValueError(
                "The score content is empty or invalid. "
                "A Mozart score requires at minimum: name, sheet, and prompt sections."
            )
        return cls.model_validate(data)

    def get_state_path(self) -> Path:
        """Get the resolved state path."""
        if self.state_path:
            return self.state_path
        if self.state_backend == "json":
            return self.workspace / ".mozart-state.json"
        return self.workspace / ".mozart-state.db"

    def get_outcome_store_path(self) -> Path:
        """Get the resolved outcome store path for learning."""
        if self.learning.outcome_store_path:
            return self.learning.outcome_store_path
        if self.learning.outcome_store_type == "json":
            return self.workspace / ".mozart-outcomes.json"
        return self.workspace / ".mozart-outcomes.db"
