"""Learning, grounding, and checkpoint configuration models.

Defines models for the learning system, exploration budget, entropy response,
autonomous pattern application, grounding hooks, and proactive checkpoints.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExplorationBudgetConfig(BaseModel):
    """Configuration for dynamic exploration budget (v23 Evolution).

    Maintains a budget for exploratory pattern usage that prevents convergence
    to zero, preserving diversity in the learning system.

    The budget adjusts dynamically based on pattern entropy:
    - When entropy drops below threshold: budget increases (boost)
    - When entropy is healthy: budget decays toward floor
    - Budget never drops below floor (prevents extinction of exploration)
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description=(
            "Enable dynamic exploration budget."
            " When disabled, uses static exploration_rate."
        ),
    )
    floor: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Minimum exploration budget. Budget never drops below this floor. "
        "Default 0.05 = always explore at least 5%% of the time.",
    )
    ceiling: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Maximum exploration budget. Budget never exceeds this ceiling. "
        "Default 0.50 = never explore more than 50%% of the time.",
    )
    decay_rate: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Decay rate per check interval. budget = max(floor, budget * decay_rate). "
        "Default 0.95 = 5%% decay per interval toward floor.",
    )
    boost_amount: float = Field(
        default=0.10,
        ge=0.0,
        le=0.5,
        description="Amount to boost budget when entropy is low. "
        "budget = min(ceiling, budget + boost_amount). Default 0.10 = +10%% boost.",
    )
    initial_budget: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Initial exploration budget when starting fresh. "
        "Default 0.15 matches static exploration_rate default.",
    )

    @model_validator(mode="after")
    def _validate_budget_bounds(self) -> ExplorationBudgetConfig:
        if self.floor > self.ceiling:
            raise ValueError(
                f"floor ({self.floor}) must not exceed ceiling ({self.ceiling})"
            )
        if self.initial_budget < self.floor or self.initial_budget > self.ceiling:
            raise ValueError(
                f"initial_budget ({self.initial_budget}) must be between "
                f"floor ({self.floor}) and ceiling ({self.ceiling})"
            )
        return self


class EntropyResponseConfig(BaseModel):
    """Configuration for automatic entropy response (v23 Evolution).

    When pattern entropy drops below threshold, automatically injects diversity
    through budget boosts and quarantine revisits.

    This completes the observe-respond cycle for entropy (v21 added observation).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable automatic entropy response. When disabled, entropy is only monitored.",
    )
    entropy_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Entropy level that triggers response. When entropy < threshold, "
        "diversity injection is triggered. Default 0.3 = respond when entropy is low.",
    )
    cooldown_seconds: int = Field(
        default=3600,
        ge=60,
        description="Minimum seconds between responses to prevent spam. "
        "Default 3600 = at most one response per hour.",
    )
    boost_budget: bool = Field(
        default=True,
        description="When responding, boost the exploration budget. "
        "Requires exploration_budget.enabled = True to have effect.",
    )
    revisit_quarantine: bool = Field(
        default=True,
        description="When responding, mark quarantined patterns for review. "
        "Allows previously problematic patterns to be reconsidered.",
    )
    max_quarantine_revisits: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum quarantined patterns to revisit per response. "
        "Prevents operator overload. Default 3 patterns per response.",
    )


class AutoApplyConfig(BaseModel):
    """Configuration for autonomous pattern application.

    v22 Evolution: Trust-Aware Autonomous Application - enables Mozart to
    autonomously apply high-trust patterns without human confirmation.

    Uses existing trust scoring (v19) to identify patterns safe for autonomous
    application. When enabled, patterns meeting the trust threshold are
    automatically included in prompts without escalation.

    Example YAML:
        learning:
          auto_apply:
            enabled: true
            trust_threshold: 0.85
            max_patterns_per_sheet: 3
            require_validated_status: true
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable autonomous pattern application. "
        "When true, high-trust patterns are applied without escalation. "
        "Opt-in only - patterns are never auto-applied by default.",
    )

    trust_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Minimum trust score for autonomous application. "
        "Default 0.85 is conservative - patterns must have proven reliability. "
        "Lower values increase auto-apply rate but also increase risk.",
    )

    max_patterns_per_sheet: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum patterns to auto-apply per sheet. "
        "Limits prompt size growth from pattern injection.",
    )

    require_validated_status: bool = Field(
        default=True,
        description="Require patterns to have VALIDATED quarantine status. "
        "When true, only explicitly validated patterns can be auto-applied. "
        "Provides additional safety layer beyond trust score.",
    )

    log_applications: bool = Field(
        default=True,
        description="Log when patterns are auto-applied. "
        "Always recommended for auditability.",
    )


class LearningConfig(BaseModel):
    """Configuration for learning and outcome tracking (Phase 2).

    Controls outcome recording, confidence thresholds, and escalation behavior.
    Learning Activation adds global learning store integration and time-aware scheduling.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Enable learning and outcome recording",
    )
    outcome_store_type: Literal["json", "sqlite"] = Field(
        default="json",
        description="Backend for storing learning outcomes",
    )
    outcome_store_path: Path | None = Field(
        default=None,
        description="Path for outcome store (default: workspace/.mozart-outcomes.json)",
    )
    min_confidence_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Confidence below this triggers escalation (if enabled)",
    )
    high_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence above this uses completion mode for partial failures",
    )
    escalation_enabled: bool = Field(
        default=False,
        description="Enable escalation for low-confidence decisions (requires handler)",
    )
    # Learning Activation: Global learning integration
    use_global_patterns: bool = Field(
        default=True,
        description="Query and apply patterns from global learning store",
    )
    # Pattern Application: Exploration mode (epsilon-greedy)
    exploration_rate: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Exploration rate for pattern selection (epsilon in epsilon-greedy). "
        "When random() < exploration_rate, include lower-priority patterns "
        "to collect effectiveness data. 0.0 = pure exploitation, 1.0 = try everything.",
    )
    exploration_min_priority: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Minimum priority threshold for exploration candidates. "
        "Patterns below this are excluded even in exploration mode.",
    )
    # v21 Evolution: Pattern Entropy Monitoring
    entropy_alert_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Shannon entropy below this triggers alert for low pattern diversity. "
        "0.0 = single dominant pattern, 1.0 = maximum diversity.",
    )
    entropy_check_interval: int = Field(
        default=100,
        ge=1,
        description="Check entropy every N pattern applications. "
        "Lower values = more frequent checks but higher overhead.",
    )
    # v21 Evolution: Confidence Threshold Auto-Apply
    auto_apply_enabled: bool = Field(
        default=False,
        description="Enable auto-apply for high-trust patterns. "
        "When True, patterns with trust_score >= auto_apply_trust_threshold bypass escalation.",
    )
    auto_apply_trust_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Minimum trust score required to auto-apply a pattern. "
        "0.85 is conservative (>85% success rate with validated status).",
    )
    # v23 Evolution: Exploration Budget Maintenance
    exploration_budget: ExplorationBudgetConfig = Field(
        default_factory=ExplorationBudgetConfig,
        description="Dynamic exploration budget configuration. "
        "When enabled, modulates exploration_rate based on entropy.",
    )
    # v23 Evolution: Automatic Entropy Response
    entropy_response: EntropyResponseConfig = Field(
        default_factory=EntropyResponseConfig,
        description="Automatic entropy response configuration. "
        "When enabled, injects diversity when entropy drops.",
    )
    # v22: Trust-Aware Autonomous Application
    auto_apply: AutoApplyConfig | None = Field(
        default=None,
        description="Configuration for autonomous pattern application. "
        "When set with enabled=true, high-trust patterns are applied "
        "without human confirmation. Opt-in only.",
    )

    @model_validator(mode="after")
    def _validate_confidence_thresholds(self) -> LearningConfig:
        """Ensure min_confidence_threshold < high_confidence_threshold."""
        if self.min_confidence_threshold >= self.high_confidence_threshold:
            raise ValueError(
                f"min_confidence_threshold ({self.min_confidence_threshold}) "
                f"must be less than high_confidence_threshold ({self.high_confidence_threshold})"
            )
        return self

    @model_validator(mode="after")
    def _sync_auto_apply_fields(self) -> LearningConfig:
        """Sync flat auto_apply_enabled/threshold with structured auto_apply config.

        The structured ``auto_apply`` config is the canonical source of truth.
        When both representations are present, ``auto_apply`` wins.
        When only the flat fields are set, they are migrated into ``auto_apply``
        with a deprecation log so operators know to update their configs.
        """
        if self.auto_apply is not None:
            # Structured config takes precedence → sync flat fields from it
            self.auto_apply_enabled = self.auto_apply.enabled
            self.auto_apply_trust_threshold = self.auto_apply.trust_threshold
        elif self.auto_apply_enabled:
            # Flat fields set without structured config -- migrate upward
            warnings.warn(
                "Flat auto_apply_enabled/auto_apply_trust_threshold fields are deprecated. "
                "Migrate to structured 'auto_apply:' config block.",
                DeprecationWarning,
                stacklevel=2,
            )
            self.auto_apply = AutoApplyConfig(
                enabled=self.auto_apply_enabled,
                trust_threshold=self.auto_apply_trust_threshold,
            )
        return self


class GroundingHookConfig(BaseModel):
    """Configuration for a single grounding hook.

    Grounding hooks validate sheet outputs against external sources.
    Each hook type has specific configuration options.

    Example:
        grounding:
          hooks:
            - type: file_checksum
              expected_checksums:
                "output.txt": "abc123..."
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["file_checksum"] = Field(
        description="Hook type: file_checksum validates file integrity",
    )
    name: str | None = Field(
        default=None,
        description="Custom name for this hook instance (uses type if not specified)",
    )
    expected_checksums: dict[str, str] = Field(
        default_factory=dict,
        description="For file_checksum: map of file path to expected checksum",
    )
    checksum_algorithm: Literal["md5", "sha256"] = Field(
        default="sha256",
        description="For file_checksum: algorithm for checksums",
    )

    @model_validator(mode="after")
    def _validate_expected_checksums(self) -> GroundingHookConfig:
        if self.type == "file_checksum" and not self.expected_checksums:
            warnings.warn(
                "GroundingHookConfig type='file_checksum' with empty "
                "expected_checksums will not validate any files.",
                UserWarning,
                stacklevel=2,
            )
        return self


class GroundingConfig(BaseModel):
    """Configuration for external grounding hooks.

    Grounding hooks validate sheet outputs against external sources (APIs,
    databases, file checksums) to prevent model drift and ensure output quality.

    Example:
        grounding:
          enabled: true
          hooks:
            - type: file_checksum
              expected_checksums:
                "critical_file.py": "sha256hash..."
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable external grounding hooks",
    )
    hooks: list[GroundingHookConfig] = Field(
        default_factory=list,
        description="List of grounding hook configurations to register",
    )
    fail_on_grounding_failure: bool = Field(
        default=True,
        description="Whether to fail validation if grounding fails",
    )
    escalate_on_failure: bool = Field(
        default=True,
        description="Whether to escalate to human if grounding fails (requires escalation handler)",
    )
    timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Maximum time to wait for each grounding hook",
    )

    @model_validator(mode="after")
    def _validate_hooks_when_enabled(self) -> GroundingConfig:
        """Ensure hooks are provided when grounding is enabled."""
        if self.enabled and not self.hooks:
            raise ValueError(
                "GroundingConfig has enabled=True but no hooks configured. "
                "Add at least one hook or set enabled=False."
            )
        return self


class CheckpointTriggerConfig(BaseModel):
    """Configuration for a proactive checkpoint trigger.

    v21 Evolution: Proactive Checkpoint System - enables pre-execution checkpoints.

    Example:
        checkpoints:
          enabled: true
          triggers:
            - name: high_risk_sheet
              sheet_nums: [5, 6]
              message: "These sheets modify production files"
            - name: deployment_keywords
              prompt_contains: ["deploy", "production", "delete"]
              requires_confirmation: true
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="Name/identifier for this trigger",
    )
    sheet_nums: list[int] | None = Field(
        default=None,
        description="Specific sheet numbers to checkpoint (None = check other conditions)",
    )
    prompt_contains: list[str] | None = Field(
        default=None,
        description="Keywords in prompt that trigger checkpoint (case-insensitive)",
    )
    min_retry_count: int | None = Field(
        default=None,
        ge=0,
        description="Trigger if retry count >= this value",
    )
    requires_confirmation: bool = Field(
        default=True,
        description="Whether to require explicit confirmation (True) or just warn (False)",
    )
    message: str = Field(
        default="",
        description="Custom message to show when checkpoint triggers",
    )

    @model_validator(mode="after")
    def _require_at_least_one_condition(self) -> CheckpointTriggerConfig:
        """Ensure trigger has at least one non-empty matching condition."""
        if not (self.sheet_nums or self.prompt_contains or self.min_retry_count is not None):
            raise ValueError(
                f"CheckpointTrigger '{self.name}' must have at least one non-empty condition "
                "(sheet_nums, prompt_contains, or min_retry_count)"
            )
        return self


class CheckpointConfig(BaseModel):
    """Configuration for proactive checkpoints.

    v21 Evolution: Proactive Checkpoint System - enables asking for confirmation
    BEFORE dangerous operations, complementing reactive escalation.

    Example:
        checkpoints:
          enabled: true
          triggers:
            - name: production_warning
              prompt_contains: ["production", "deploy"]
              message: "This sheet may affect production systems"
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable proactive checkpoints before sheet execution",
    )
    triggers: list[CheckpointTriggerConfig] = Field(
        default_factory=list,
        description="List of checkpoint triggers to evaluate before each sheet",
    )

    @model_validator(mode="after")
    def _validate_triggers_when_enabled(self) -> CheckpointConfig:
        """Ensure triggers are provided when checkpoints are enabled."""
        if self.enabled and not self.triggers:
            raise ValueError(
                "CheckpointConfig has enabled=True but no triggers configured. "
                "Add at least one trigger or set enabled=False."
            )
        return self
