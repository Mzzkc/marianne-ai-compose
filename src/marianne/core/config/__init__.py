"""Configuration models for Marianne jobs.

This package provides Pydantic models for loading and validating YAML job
configurations. All models are re-exported from this ``__init__`` for
backward compatibility — existing ``from marianne.core.config import ...``
imports continue to work unchanged.
"""

# A2A protocol configuration
from marianne.core.config.a2a import (
    A2ASkill,
    AgentCard,
)

# Backend configuration
from marianne.core.config.backend import (
    BridgeConfig,
    MCPServerConfig,
)

# Execution configuration
from marianne.core.config.execution import (
    CircuitBreakerConfig,
    CodeExecutionConfig,
    CostLimitConfig,
    ParallelConfig,
    RateLimitConfig,
    RetryConfig,
    SkipWhenCommand,
    StaleDetectionConfig,
    ValidationRule,
)

# Fleet configuration
from marianne.core.config.fleet import (
    FleetConfig,
    FleetGroupConfig,
    FleetScoreEntry,
)

# Instrument plugin system configuration
from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    CodeModeConfig,
    CodeModeInterface,
    HttpProfile,
    InstrumentProfile,
    ModelCapacity,
)

# Job and sheet configuration
from marianne.core.config.job import (
    InjectionCategory,
    InjectionItem,
    InstrumentDef,
    JobConfig,
    MovementDef,
    PromptConfig,
    SheetConfig,
)

# Learning and grounding configuration
from marianne.core.config.learning import (
    AutoApplyConfig,
    CheckpointConfig,
    CheckpointTriggerConfig,
    EntropyResponseConfig,
    ExplorationBudgetConfig,
    GroundingConfig,
    GroundingHookConfig,
    LearningConfig,
)

# Orchestration configuration
from marianne.core.config.orchestration import (
    ConcertConfig,
    ConductorConfig,
    ConductorPreferences,
    ConductorRole,
    MisfirePolicy,
    NotificationConfig,
    OverlapPolicy,
    PostSuccessHookConfig,
    ScheduleConfig,
)

# Specification corpus configuration
from marianne.core.config.spec import (
    SpecCorpusConfig,
    SpecFragment,
)

# Technique system configuration
from marianne.core.config.techniques import (
    TechniqueConfig,
    TechniqueKind,
)

# Workspace and environment configuration
from marianne.core.config.workspace import (
    AIReviewConfig,
    CrossSheetConfig,
    FeedbackConfig,
    IsolationConfig,
    IsolationMode,
    LogConfig,
    WorkspaceLifecycleConfig,
)

__all__ = [
    # A2A
    "A2ASkill",
    "AgentCard",
    # Backend
    "BridgeConfig",
    "MCPServerConfig",
    # Instruments
    "CliCommand",
    "CliErrorConfig",
    "CliOutputConfig",
    "CliProfile",
    "CodeModeConfig",
    "CodeModeInterface",
    "HttpProfile",
    "InstrumentProfile",
    "ModelCapacity",
    # Execution
    "CircuitBreakerConfig",
    "CodeExecutionConfig",
    "CostLimitConfig",
    "ParallelConfig",
    "RateLimitConfig",
    "RetryConfig",
    "SkipWhenCommand",
    "StaleDetectionConfig",
    "ValidationRule",
    # Fleet
    "FleetConfig",
    "FleetGroupConfig",
    "FleetScoreEntry",
    # Job
    "InjectionCategory",
    "InjectionItem",
    "InstrumentDef",
    "JobConfig",
    "MovementDef",
    "PromptConfig",
    "SheetConfig",
    # Learning
    "AutoApplyConfig",
    "CheckpointConfig",
    "CheckpointTriggerConfig",
    "EntropyResponseConfig",
    "ExplorationBudgetConfig",
    "GroundingConfig",
    "GroundingHookConfig",
    "LearningConfig",
    # Orchestration
    "ConcertConfig",
    "ConductorConfig",
    "ConductorPreferences",
    "ConductorRole",
    "MisfirePolicy",
    "NotificationConfig",
    "OverlapPolicy",
    "PostSuccessHookConfig",
    "ScheduleConfig",
    # Spec Corpus
    "SpecCorpusConfig",
    "SpecFragment",
    # Techniques
    "TechniqueConfig",
    "TechniqueKind",
    # Workspace
    "AIReviewConfig",
    "CrossSheetConfig",
    "FeedbackConfig",
    "IsolationConfig",
    "IsolationMode",
    "LogConfig",
    "WorkspaceLifecycleConfig",
]
