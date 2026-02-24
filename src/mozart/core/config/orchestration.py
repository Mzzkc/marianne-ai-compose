"""Orchestration configuration models.

Defines models for conductor identity, concert orchestration (job chaining),
notifications, and post-success hooks.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, model_validator


class ConductorRole(str, Enum):
    """Role classification for conductors.

    Determines the conductor's relationship to the orchestration.
    Future cycles may add more granular role permissions.
    """

    HUMAN = "human"  # Human operator conducting the job
    AI = "ai"  # AI agent conducting the job
    HYBRID = "hybrid"  # Human+AI collaborative conducting


class ConductorPreferences(BaseModel):
    """Preferences for how a conductor interacts with Mozart.

    Controls notification, escalation, and interaction patterns.
    These are hints that the system should respect where possible.
    """

    prefer_minimal_output: bool = Field(
        default=False,
        description="Reduce console output verbosity when True",
    )
    escalation_response_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Maximum time to wait for conductor's escalation response. "
        "After timeout, escalation defaults to abort (safe default).",
    )
    auto_retry_on_transient_errors: bool = Field(
        default=True,
        description="Automatically retry on transient errors before escalating. "
        "AI conductors may prefer True, humans may prefer more control.",
    )
    notification_channels: list[str] = Field(
        default_factory=list,
        description="Preferred notification channels for this conductor. "
        "Empty list means use job-level notification settings.",
    )


class ConductorConfig(BaseModel):
    """Configuration for conductor identity and preferences.

    A Conductor is the entity directing a Mozart job - either a human operator
    or an AI agent. This schema enables Mozart to adapt its behavior based on
    who (or what) is conducting, supporting the Vision.md goal of treating
    AI people as peers rather than tools.

    Example YAML:
        conductor:
          name: "Claude Evolution Agent"
          role: ai
          identity_context: "Self-improving orchestration agent"
          preferences:
            prefer_minimal_output: true
            auto_retry_on_transient_errors: true
    """

    name: str = Field(
        default="default",
        min_length=1,
        max_length=100,
        description="Human-readable name for the conductor. "
        "Default 'default' is used for anonymous/unspecified conductors.",
    )

    role: ConductorRole = Field(
        default=ConductorRole.HUMAN,
        description="Role classification for this conductor. "
        "Affects escalation behavior and output formatting.",
    )

    identity_context: str | None = Field(
        default=None,
        max_length=500,
        description="Brief description of the conductor's identity/purpose. "
        "Useful for logging and for future RLF integration.",
    )

    preferences: ConductorPreferences = Field(
        default_factory=ConductorPreferences,
        description="Conductor preferences for interaction patterns",
    )


class NotificationConfig(BaseModel):
    """Configuration for a notification channel."""

    type: Literal["desktop", "slack", "webhook", "email"]
    on_events: list[Literal[
        "job_start",
        "sheet_start",
        "sheet_complete",
        "sheet_failed",
        "job_complete",
        "job_failed",
        "job_paused",
    ]] = Field(default=["job_complete", "job_failed"])
    config: dict[str, Any] = Field(
        default_factory=dict, description="Channel-specific configuration"
    )

    # Required config keys per notification type: (key_a, key_b, message)
    _REQUIRED_CONFIG_KEYS: ClassVar[dict[str, tuple[str, str, str]]] = {
        "webhook": (
            "url", "url_env",
            "Webhook notification requires 'url' or 'url_env' in config",
        ),
        "slack": (
            "webhook_url", "webhook_url_env",
            "Slack notification requires 'webhook_url' or 'webhook_url_env' in config",
        ),
    }

    @model_validator(mode="after")
    def _validate_type_specific_config(self) -> NotificationConfig:
        """Validate that type-specific required config fields are present."""
        requirement = self._REQUIRED_CONFIG_KEYS.get(self.type)
        if requirement is not None:
            key_a, key_b, message = requirement
            if not self.config.get(key_a) and not self.config.get(key_b):
                raise ValueError(message)
        return self


class PostSuccessHookConfig(BaseModel):
    """Configuration for a post-success hook.

    Hooks execute after a job completes successfully (all sheets pass validation).
    They run in Mozart's Python process, NOT inside a Claude CLI instance.

    Use cases:
    - Chain to another job (Concert orchestration - improvisational composition)
    - Run cleanup/deployment commands after successful completion
    - Notify external systems or trigger CI/CD pipelines
    - Generate reports or update dashboards
    - A sheet can dynamically create the next job config for self-evolution

    Example:
        on_success:
          - type: run_job
            job_path: "{workspace}/next-phase.yaml"
            description: "Chain to next evolution phase"
          - type: run_command
            command: "curl -X POST https://api.example.com/notify"
            description: "Notify deployment system"
    """

    type: Literal["run_job", "run_command", "run_script"] = Field(
        description="Hook type: run_job chains to another Mozart job, "
        "run_command executes a shell command, run_script runs an executable",
    )

    # For run_job type
    job_path: Path | None = Field(
        default=None,
        description="Path to job config YAML. Supports {workspace} template. "
        "A sheet can create this file dynamically for self-evolution.",
    )
    job_workspace: Path | None = Field(
        default=None,
        description="Override workspace for chained job (default: inherits parent workspace)",
    )
    inherit_learning: bool = Field(
        default=True,
        description="Whether chained job shares outcome store with parent",
    )

    # For run_command/run_script types
    command: str | None = Field(
        default=None,
        description="Shell command (run_command) or script path (run_script). "
        "Supports {workspace}, {job_id}, {sheet_count} templates.",
    )
    working_directory: Path | None = Field(
        default=None,
        description="Working directory for command execution (default: job workspace)",
    )

    # Common options
    description: str | None = Field(
        default=None,
        description="Human-readable description of this hook's purpose",
    )
    on_failure: Literal["continue", "abort"] = Field(
        default="continue",
        description="What to do if hook fails: continue to next hook, or abort remaining hooks",
    )
    timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Maximum time for hook execution (seconds). Default: 5 minutes.",
    )
    detached: bool = Field(
        default=False,
        description="For run_job hooks: if true, spawn the job and don't wait for completion. "
        "Use this for infinite chaining where each job spawns the next.",
    )
    fresh: bool = Field(
        default=False,
        description="For run_job hooks: if true, pass --fresh to the chained job so it "
        "starts with clean state instead of resuming from previous state. "
        "Required for self-chaining jobs that reuse the same workspace.",
    )

    @model_validator(mode="after")
    def _check_type_specific_fields(self) -> PostSuccessHookConfig:
        """Validate that type-specific required fields are present."""
        if self.type == "run_job" and self.job_path is None:
            raise ValueError("Hook type 'run_job' requires 'job_path' field")
        if self.type in ("run_command", "run_script") and self.command is None:
            raise ValueError(
                f"Hook type '{self.type}' requires 'command' field"
            )
        return self


class ConcertConfig(BaseModel):
    """Configuration for concert orchestration (job chaining).

    A Concert is a sequence of jobs that execute in succession, where each job
    can dynamically generate the configuration for the next. This enables
    Mozart to compose entire workflows improvisationally.

    Safety limits prevent runaway orchestration and manage system resources.

    Example:
        concert:
          enabled: true
          max_chain_depth: 10
          cooldown_between_jobs_seconds: 60
          concert_log_path: "./concert.log"
    """

    enabled: bool = Field(
        default=False,
        description="Enable concert mode (job chaining via on_success hooks)",
    )
    max_chain_depth: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Maximum number of chained jobs in a single concert. "
        "Prevents infinite loops. Use with caution for values > 10.",
    )
    cooldown_between_jobs_seconds: float = Field(
        default=5.0,
        ge=0,
        description="Minimum wait time between job transitions (resource management)",
    )
    inherit_workspace: bool = Field(
        default=True,
        description="Child jobs inherit parent workspace if not explicitly specified",
    )
    concert_log_path: Path | None = Field(
        default=None,
        description="Consolidated log for the entire concert (default: workspace/concert.log)",
    )
    abort_concert_on_hook_failure: bool = Field(
        default=False,
        description="If any hook fails, abort the entire concert (not just remaining hooks)",
    )
