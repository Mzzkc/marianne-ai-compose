"""Execution and retry configuration models.

Defines models for retry behavior, rate limiting, circuit breaker,
cost limits, and parallel execution.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RetryConfig(BaseModel):
    """Configuration for retry behavior including partial completion recovery."""

    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(default=3, ge=0, description="Maximum retry attempts per sheet")
    base_delay_seconds: float = Field(
        default=10.0, gt=0, description="Initial delay between retries"
    )
    max_delay_seconds: float = Field(default=3600.0, gt=0, description="Maximum delay (1 hour)")
    exponential_base: float = Field(default=2.0, gt=1, description="Exponential backoff multiplier")
    jitter: bool = Field(default=True, description="Add randomness to delays")

    # Partial completion recovery settings
    max_completion_attempts: int = Field(
        default=5,
        ge=0,
        description="Maximum completion prompt attempts before falling back to full retry",
    )
    completion_delay_seconds: float = Field(
        default=5.0,
        ge=0,
        description="Delay between completion attempts (seconds)",
    )
    completion_threshold_percent: float = Field(
        default=50.0,
        gt=0,
        le=100,
        description="Minimum pass percentage to trigger completion mode (default: >50%)",
    )

    @model_validator(mode="after")
    def _validate_delay_range(self) -> RetryConfig:
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError(
                f"base_delay_seconds ({self.base_delay_seconds}) must not exceed "
                f"max_delay_seconds ({self.max_delay_seconds})"
            )
        return self


class RateLimitConfig(BaseModel):
    """Configuration for rate limit detection and handling."""

    model_config = ConfigDict(extra="forbid")

    detection_patterns: list[str] = Field(
        default=[
            r"rate.?limit",
            r"usage.?limit",
            r"quota",
            r"too many requests",
            r"429",
            r"capacity",
            r"try again later",
        ],
        description="Regex patterns to detect rate limiting in output",
    )

    @field_validator("detection_patterns")
    @classmethod
    def _validate_regex_patterns(cls, v: list[str]) -> list[str]:
        for i, pattern in enumerate(v):
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(
                    f"detection_patterns[{i}] is not valid regex: {pattern!r} — {e}"
                ) from e
        return v

    wait_minutes: int = Field(default=60, ge=1, description="Minutes to wait when rate limited")
    max_waits: int = Field(default=24, ge=1, description="Maximum wait cycles (24 = 24 hours)")
    max_quota_waits: int = Field(
        default=48,
        ge=1,
        description="Maximum quota exhaustion wait cycles before failing "
        "(48 = 48 hours at 60min/wait)",
    )


class CircuitBreakerConfig(BaseModel):
    """Configuration for the circuit breaker pattern.

    The circuit breaker prevents cascading failures by temporarily blocking
    requests after repeated failures. This gives the backend time to recover
    before retrying.

    State transitions:
    - CLOSED (normal): Requests flow through, failures are tracked
    - OPEN (blocking): Requests are blocked after failure_threshold exceeded
    - HALF_OPEN (testing): Single request allowed to test recovery

    Evolution #8: Cross-Workspace Circuit Breaker adds coordination between
    parallel Marianne jobs via the global learning store. When one job hits a
    rate limit, other jobs will honor that limit and wait.

    Example:
        circuit_breaker:
          enabled: true
          failure_threshold: 5
          recovery_timeout_seconds: 300
          cross_workspace_coordination: true
          honor_other_jobs_rate_limits: true
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Enable circuit breaker pattern for resilient execution",
    )
    failure_threshold: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Number of consecutive failures before opening circuit",
    )
    recovery_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        le=3600,
        description="Seconds to wait in OPEN state before testing recovery (max 1 hour)",
    )
    # Evolution #8: Cross-Workspace Circuit Breaker options
    cross_workspace_coordination: bool = Field(
        default=True,
        description=(
            "Enable cross-workspace coordination via global learning store. "
            "When enabled, rate limit events are shared between parallel jobs."
        ),
    )
    honor_other_jobs_rate_limits: bool = Field(
        default=True,
        description=(
            "When enabled, honor rate limits detected by other parallel jobs. "
            "This prevents redundant rate limit hits when multiple jobs are running."
        ),
    )

    @model_validator(mode="after")
    def _validate_cross_field(self) -> CircuitBreakerConfig:
        if self.honor_other_jobs_rate_limits and not self.cross_workspace_coordination:
            self.honor_other_jobs_rate_limits = False
        return self


class CostLimitConfig(BaseModel):
    """Configuration for cost tracking and limits.

    Prevents runaway costs by tracking token usage and optionally enforcing
    cost limits per sheet or per job. Cost is estimated from token counts
    using configurable rates.

    When cost limits are exceeded:
    - The current sheet is marked as failed with reason "cost_limit"
    - For per-job limits, the job is paused to prevent further execution
    - All cost data is recorded in checkpoint state for analysis

    Example:
        cost_limits:
          enabled: true
          max_cost_per_sheet: 5.00
          max_cost_per_job: 100.00
          cost_per_1k_input_tokens: 0.003
          cost_per_1k_output_tokens: 0.015

    Note: Default rates are for Claude Sonnet. For Opus, use:
        cost_per_1k_input_tokens: 0.015
        cost_per_1k_output_tokens: 0.075
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable cost tracking and limit enforcement",
    )
    max_cost_per_sheet: float | None = Field(
        default=None,
        gt=0,
        description="Maximum allowed cost per sheet in USD. None = no limit.",
    )
    max_cost_per_job: float | None = Field(
        default=None,
        gt=0,
        description="Maximum allowed cost for entire job in USD. None = no limit.",
    )
    cost_per_1k_input_tokens: float = Field(
        default=0.003,
        gt=0,
        description="Cost per 1000 input tokens in USD (Claude Sonnet default: $0.003)",
    )
    cost_per_1k_output_tokens: float = Field(
        default=0.015,
        gt=0,
        description="Cost per 1000 output tokens in USD (Claude Sonnet default: $0.015)",
    )
    warn_at_percent: float = Field(
        default=80.0,
        gt=0,
        le=100,
        description="Emit warning when this percentage of limit is reached",
    )

    @model_validator(mode="before")
    @classmethod
    def _auto_enable_when_limits_set(cls, data: object) -> object:
        """Auto-enable cost tracking when any limit is set.

        If a user sets max_cost_per_job or max_cost_per_sheet but forgets
        enabled: true, silently enable it. Respects explicit enabled: false.
        """
        if not isinstance(data, dict):
            return data
        has_limits = (
            data.get("max_cost_per_job") is not None
            or data.get("max_cost_per_sheet") is not None
        )
        explicitly_set_enabled = "enabled" in data
        if has_limits and not explicitly_set_enabled:
            data["enabled"] = True
        return data

    @model_validator(mode="after")
    def _require_limit_when_enabled(self) -> CostLimitConfig:
        """Require at least one cost limit when cost tracking is enabled."""
        if self.enabled and self.max_cost_per_sheet is None and self.max_cost_per_job is None:
            raise ValueError(
                "CostLimitConfig has enabled=True but no limits set. "
                "Set max_cost_per_sheet and/or max_cost_per_job, or disable cost limits."
            )
        return self


class StaleDetectionConfig(BaseModel):
    """Configuration for detecting stale (hung) sheet executions.

    When enabled, monitors execution activity and fails sheets that produce
    no output for longer than ``idle_timeout_seconds``. This catches hung
    processes that the per-sheet timeout alone may not detect quickly enough
    (e.g., a 30-minute timeout sheet that hangs after 2 minutes of output).

    Example:
        stale_detection:
          enabled: true
          idle_timeout_seconds: 300
          check_interval_seconds: 30

    Note: The idle timeout should be generous enough to accommodate legitimate
    pauses (e.g., waiting for API responses). A minimum of 120 seconds is
    recommended for LLM-based workloads.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable stale execution detection. "
        "When true, sheets with no output activity beyond idle_timeout_seconds are failed.",
    )
    idle_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Maximum seconds of inactivity before marking a sheet as stale. "
        "Default 300s (5 minutes). Recommended minimum 120s for LLM workloads.",
    )
    check_interval_seconds: float = Field(
        default=30.0,
        gt=0,
        description="How often (seconds) to check for idle executions. "
        "Lower values detect stale sheets faster but add minor overhead.",
    )

    @model_validator(mode="after")
    def _validate_interval_vs_timeout(self) -> StaleDetectionConfig:
        if self.check_interval_seconds >= self.idle_timeout_seconds:
            raise ValueError(
                f"check_interval_seconds ({self.check_interval_seconds}) must be less than "
                f"idle_timeout_seconds ({self.idle_timeout_seconds})"
            )
        return self


class PreflightConfig(BaseModel):
    """Configuration for pre-flight prompt analysis before sheet execution.

    Controls token count thresholds for warning and error during preflight
    checks. Different instruments have different context windows — a 150K
    threshold that's correct for a 200K-context model is wrong for a
    1M-context model. Set thresholds appropriate for the instruments in use.

    This config lives at the daemon level because the conductor manages
    all execution and knows which instruments are available. Individual
    scores inherit the daemon's thresholds unless overridden.

    Example (daemon config for large-context instruments):
        preflight:
          token_warning_threshold: 200000
          token_error_threshold: 800000

    Example (disable the error threshold entirely):
        preflight:
          token_error_threshold: 0
    """

    model_config = ConfigDict(extra="forbid")

    token_warning_threshold: int = Field(
        default=50_000,
        ge=0,
        description="Prompt token count above which to warn during preflight. "
        "Default: 50,000. Set higher for large-context instruments. "
        "Set to 0 to disable warnings.",
    )
    token_error_threshold: int = Field(
        default=150_000,
        ge=0,
        description="Prompt token count above which to error during preflight. "
        "Default: 150,000. Set higher for large-context instruments "
        "(e.g., 800000 for 1M-context models). Set to 0 to disable the check.",
    )

    @model_validator(mode="after")
    def _validate_threshold_ordering(self) -> PreflightConfig:
        """Ensure warning threshold is less than error threshold when both are set."""
        if (
            self.token_warning_threshold > 0
            and self.token_error_threshold > 0
            and self.token_warning_threshold >= self.token_error_threshold
        ):
            raise ValueError(
                f"token_warning_threshold ({self.token_warning_threshold}) must be less than "
                f"token_error_threshold ({self.token_error_threshold})"
            )
        return self


class ParallelConfig(BaseModel):
    """Configuration for parallel sheet execution (v17 evolution).

    Enables running multiple sheets concurrently when the dependency DAG
    permits. Requires sheet dependencies to be configured for meaningful
    parallel execution.

    Example YAML:
        parallel:
          enabled: true
          max_concurrent: 3
          fail_fast: true

        sheet:
          dependencies:
            2: [1]
            3: [1]
            4: [2, 3]

    With this config, sheets 2 and 3 can run in parallel after sheet 1
    completes, then sheet 4 runs after both 2 and 3 complete.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable parallel sheet execution. "
        "When true, sheets with satisfied dependencies run concurrently.",
    )
    max_concurrent: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Maximum sheets to execute concurrently. "
        "Higher values use more API quota but complete faster.",
    )
    fail_fast: bool = Field(
        default=True,
        description="Stop starting new sheets when one fails. "
        "If False, continues executing remaining parallel sheets.",
    )
    budget_partition: bool = Field(
        default=True,
        description="Partition cost budget across parallel branches. "
        "When True, each parallel sheet gets (remaining_budget / max_concurrent). "
        "When False, each sheet has access to full remaining budget. "
        "NOTE: Not yet implemented — this field is accepted but not enforced. "
        "Cost checks currently use global total regardless of this setting.",
    )
    stagger_delay_ms: int = Field(
        default=0,
        ge=0,
        le=5000,
        description="Delay in milliseconds between launching parallel sheets. "
        "Reduces rate limit surge when many sheets hit the same API. "
        "0 means no stagger (all sheets launch simultaneously). "
        "Recommended: 100-200ms for rate-limit-sensitive instruments.",
    )

    @model_validator(mode="after")
    def _reject_unimplemented_overrides(self) -> ParallelConfig:
        if not self.budget_partition:
            raise ValueError(
                "ParallelConfig.budget_partition=False is not yet implemented. "
                "Cost checks currently use global total regardless of this setting. "
                "Remove 'budget_partition: false' from your config or keep the default (true)."
            )
        return self


class ValidationRule(BaseModel):
    """A single validation rule for checking sheet outputs.

    Supports staged execution via the `stage` field. Validations are run
    in stage order (1, 2, 3...). If any validation in a stage fails,
    higher stages are skipped (fail-fast behavior).

    Typical stage layout:
    - Stage 1: Syntax & compilation (cargo check, cargo fmt --check)
    - Stage 2: Testing (cargo test, pytest)
    - Stage 3: Code quality (clippy -D warnings, ruff check)
    - Stage 4: Security (cargo audit, npm audit)
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "file_exists",
        "file_modified",
        "content_contains",
        "content_regex",
        "command_succeeds",
    ]
    path: str | None = Field(
        default=None, description="File path (supports {sheet_num}, {workspace})"
    )
    pattern: str | None = Field(default=None, description="Pattern for content matching")
    description: str | None = Field(default=None, description="Human-readable description")
    command: str | None = Field(
        default=None,
        description="Shell command to run (for command_succeeds type)",
    )
    working_directory: str | None = Field(
        default=None,
        description="Working directory for command (defaults to workspace)",
    )
    stage: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Validation stage (1-10). Lower stages run first; fail-fast on failure.",
    )
    condition: str | None = Field(
        default=None,
        description="Condition expression for when this validation applies. "
        "Supports: 'sheet_num >= N', 'sheet_num == N', 'sheet_num <= N'. "
        "If None, validation always applies.",
    )
    retry_count: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Number of retry attempts for file-based validations. "
        "Helps with filesystem race conditions when sheet creates files that "
        "are immediately validated. Set to 0 to disable retries.",
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Timeout for command_succeeds validations (seconds). "
        "Overrides the global VALIDATION_COMMAND_TIMEOUT_SECONDS default (3600s / 1 hour). "
        "Use for validations that need a shorter or longer ceiling.",
    )
    retry_delay_ms: int = Field(
        default=200,
        ge=0,
        le=5000,
        description="Delay between retry attempts in milliseconds. "
        "Default 200ms provides filesystem sync time without excessive waiting.",
    )
    sheet: int | None = Field(
        default=None,
        ge=1,
        description="Target sheet number for this validation. When set, the "
        "validation only runs for the specified sheet. Shorthand for "
        "condition: 'sheet_num == N'. If both sheet and condition are set, "
        "condition takes precedence (sheet is only applied when condition is absent).",
    )

    @model_validator(mode="after")
    def _sheet_to_condition(self) -> ValidationRule:
        """Convert sheet: N shorthand to condition expression."""
        if self.sheet is not None and self.condition is None:
            self.condition = f"sheet_num == {self.sheet}"
        return self

    @model_validator(mode="after")
    def _check_type_specific_fields(self) -> ValidationRule:
        """Validate that type-specific required fields are present."""
        if self.type in ("file_exists", "file_modified", "content_contains", "content_regex"):
            if self.path is None:
                raise ValueError(
                    f"Validation type '{self.type}' requires 'path' field"
                )
        if self.type in ("content_contains", "content_regex"):
            if self.pattern is None:
                raise ValueError(
                    f"Validation type '{self.type}' requires 'pattern' field"
                )
        if self.type == "command_succeeds":
            if self.command is None:
                raise ValueError(
                    "Validation type 'command_succeeds' requires 'command' field"
                )
        if self.type == "content_regex" and self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(
                    f"Invalid regex pattern for content_regex validation: {exc}"
                ) from exc
        return self


class SkipWhenCommand(BaseModel):
    """A command-based conditional skip rule for sheet execution.

    When the command exits 0, the sheet is SKIPPED.
    When the command exits non-zero, the sheet RUNS.
    On timeout or error, the sheet RUNS (fail-open for safety).

    The ``command`` field supports ``{workspace}`` template expansion,
    following the same pattern as validation commands.
    """

    model_config = ConfigDict(extra="forbid")

    command: str = Field(
        description="Shell command to evaluate. Exit 0 = skip the sheet. "
        "Supports {workspace} template expansion.",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable reason for the skip condition",
    )
    timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=60,
        description="Maximum seconds to wait for command (fail-open on timeout)",
    )
