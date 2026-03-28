"""Configuration models for Mozart jobs.

This package provides Pydantic models for loading and validating YAML job
configurations. All models are re-exported from this ``__init__`` for
backward compatibility — existing ``from mozart.core.config import ...``
imports continue to work unchanged.
"""

# Backend configuration
from mozart.core.config.backend import (
    BackendConfig,
    BridgeConfig,
    MCPServerConfig,
    OllamaConfig,
    RecursiveLightConfig,
    SheetBackendOverride,
)

# Execution configuration
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

# Instrument plugin system configuration
from mozart.core.config.instruments import (
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
from mozart.core.config.job import (
    InjectionCategory,
    InjectionItem,
    JobConfig,
    PromptConfig,
    SheetConfig,
)

# Learning and grounding configuration
from mozart.core.config.learning import (
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
from mozart.core.config.orchestration import (
    ConcertConfig,
    ConductorConfig,
    ConductorPreferences,
    ConductorRole,
    NotificationConfig,
    PostSuccessHookConfig,
)

# Specification corpus configuration
from mozart.core.config.spec import (
    SpecCorpusConfig,
    SpecFragment,
)

# Workspace and environment configuration
from mozart.core.config.workspace import (
    AIReviewConfig,
    CrossSheetConfig,
    FeedbackConfig,
    IsolationConfig,
    IsolationMode,
    LogConfig,
    WorkspaceLifecycleConfig,
)

__all__ = [
    # Backend
    "BackendConfig",
    "BridgeConfig",
    "MCPServerConfig",
    "OllamaConfig",
    "RecursiveLightConfig",
    "SheetBackendOverride",
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
    "CostLimitConfig",
    "ParallelConfig",
    "RateLimitConfig",
    "RetryConfig",
    "SkipWhenCommand",
    "StaleDetectionConfig",
    "ValidationRule",
    # Job
    "InjectionCategory",
    "InjectionItem",
    "JobConfig",
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
    "NotificationConfig",
    "PostSuccessHookConfig",
    # Spec Corpus
    "SpecCorpusConfig",
    "SpecFragment",
    # Workspace
    "AIReviewConfig",
    "CrossSheetConfig",
    "FeedbackConfig",
    "IsolationConfig",
    "IsolationMode",
    "LogConfig",
    "WorkspaceLifecycleConfig",
]
