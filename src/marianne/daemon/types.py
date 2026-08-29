"""Shared data types for the Marianne daemon.

Defines request/response models and status types used across daemon components
(server, service, CLI bridge). All models are Pydantic v2 BaseModel for
serialization over IPC.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from typing_extensions import (  # noqa: UP035  (pydantic needs typing_extensions.TypedDict on py<3.12)
    Required,
    TypedDict,
)

# ─── IPC Handler Parameter TypedDicts ─────────────────────────────
# These define the expected parameter shapes for each daemon IPC method.
# Used by _register_methods() in process.py for type-safe parameter access.
#
# total=False makes all keys optional by default (IPC params come from
# JSON-RPC where any key might be absent). Keys that must be present
# use Required[] to restore type-safe direct access.


class JobSubmitParams(TypedDict, total=False):
    """Parameters for the job.submit IPC method."""

    config_path: Required[str]
    workspace: str | None
    fresh: bool
    self_healing: bool
    self_healing_auto_confirm: bool
    escalation: bool
    start_sheet: int | None
    dry_run: bool
    chain_depth: int | None
    client_cwd: str | None
    job_id: str | None
    schedule_id: str | None
    scheduled_due_at: float | None


class JobIdentifyParams(TypedDict, total=False):
    """Parameters for IPC methods that identify a job (status, pause, resume)."""

    job_id: Required[str]
    workspace: str | None


class JobCancelParams(TypedDict, total=False):
    """Parameters for the job.cancel IPC method."""

    job_id: Required[str]


class DaemonShutdownParams(TypedDict, total=False):
    """Parameters for the daemon.shutdown IPC method."""

    graceful: bool  # Defaults to True if not provided


class JobRequest(BaseModel):
    """Request to submit a job to the daemon.

    Sent by clients (CLI, dashboard) to the daemon over IPC.
    The daemon validates the config and either accepts or rejects.
    """

    config_path: Path = Field(
        description="Path to the job configuration YAML file",
    )
    job_id: str | None = Field(
        default=None,
        description="Explicit runtime job identifier. Manual submissions use the "
        "configuration path stem when omitted.",
    )
    schedule_id: str | None = Field(
        default=None,
        description="Stable recurring-schedule lineage for overlap detection",
    )
    scheduled_due_at: float | None = Field(
        default=None,
        description="Exact durable due identity that produced a scheduled child",
    )
    workspace: Path | None = Field(
        default=None,
        description="Override workspace directory. "
        "If None, uses the workspace specified in the job config.",
    )
    fresh: bool = Field(
        default=False,
        description="Start with clean state, ignoring any existing checkpoint",
    )
    self_healing: bool = Field(
        default=False,
        description="Enable self-healing mode for automatic error recovery",
    )
    self_healing_auto_confirm: bool = Field(
        default=False,
        description="Auto-confirm suggested fixes in self-healing mode",
    )
    escalation: bool = Field(
        default=False,
        description="Enter FERMATA (pause for a composer decision) on retry "
        "exhaustion, independent of self-healing (#361). Resolved via "
        "marker files or `mzt resolve`. self_healing implies this.",
    )
    start_sheet: int | None = Field(
        default=None,
        description="Override starting sheet number. "
        "If None, resumes from last checkpoint or starts from 1.",
    )
    dry_run: bool = Field(
        default=False,
        description="Validate config and return without executing sheets",
    )
    chain_depth: int | None = Field(
        default=None,
        description="Concert chain depth for chained job submissions. "
        "Used by the daemon to track and enforce max_chain_depth.",
    )
    client_cwd: Path | None = Field(
        default=None,
        description="Working directory where the CLI command was invoked. "
        "Used by the conductor to resolve relative paths in config files "
        "against the client's cwd rather than the daemon's cwd.",
    )
    runtime_variables: dict[str, str] = Field(
        default_factory=dict,
        description="Per-invocation template variables from `mzt run --var "
        "k=v` (#359). Merged into the score's prompt.variables (CLI wins "
        "on collision) and persisted on the checkpoint for resume.",
    )


class JobResponse(BaseModel):
    """Response from the daemon after a job submission.

    Returned immediately — does not wait for job completion.
    Clients poll status separately via DaemonStatus or job-specific queries.
    """

    job_id: str = Field(
        description="Unique identifier for the submitted job",
    )
    status: Literal["accepted", "rejected", "pending", "error"] = Field(
        description="Submission result: accepted (queued), pending (queued "
        "but waiting for rate limits to clear), rejected (validation "
        "failed), or error (daemon fault)",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable detail about the submission result",
    )


class ScheduleStatus(BaseModel):
    """Public recurring-score projection returned by status operations."""

    enabled: bool = Field(description="Whether future recurring ticks are enabled")
    next_due_at: float = Field(description="Next durable due time as a Unix epoch")
    last_due_at: float | None = Field(
        default=None,
        description="Most recent handled due time as a Unix epoch",
    )
    last_run_id: str | None = Field(
        default=None,
        description="Most recent scheduled child job identifier",
    )
    last_outcome: str | None = Field(
        default=None,
        description="Most recent recurring tick outcome",
    )
    consecutive_drops: int = Field(
        ge=0,
        description="Number of consecutive recurring tick drops",
    )


class JobTimeoutCleanupStatus(BaseModel):
    """Truthful, secret-free evidence from Baton's timeout teardown."""

    cleanup_path: str = Field(description="Existing cleanup seam used")
    cleanup_generation: str | None = Field(
        default=None,
        description="Daemon-local execution generation for this evidence",
    )
    deregistration_state: str = Field(
        description="Whether Baton deregistration was attempted or available",
    )
    tracked_process_groups: int = Field(default=0, ge=0)
    sigterm_attempted: int = Field(default=0, ge=0)
    sigterm_succeeded: int = Field(default=0, ge=0)
    sigterm_failed: int = Field(default=0, ge=0)
    sigterm_skipped: int = Field(default=0, ge=0)
    escalation_state: str = Field(default="not_needed")
    sigkill_attempted: int = Field(default=0, ge=0)
    sigkill_succeeded: int = Field(default=0, ge=0)
    sigkill_failed: int = Field(default=0, ge=0)
    residual_check_state: str = Field(default="unverified")
    residual_process_groups: int | None = Field(default=None, ge=0)


class JobDeadlineStatus(BaseModel):
    """Public, secret-free projection of one job's timeout authority."""

    daemon_limit_seconds: float = Field(
        description="Configured daemon per-execution timeout",
    )
    score_limit_seconds: float | None = Field(
        default=None,
        description="Configured score wall-clock limit when valid",
    )
    effective_remaining_seconds: float = Field(
        ge=0,
        description="Current stricter applicable timeout remainder",
    )
    elapsed_seconds: float = Field(
        ge=0,
        description="Wall time consumed since the job was first registered",
    )
    wall_deadline_at: float | None = Field(
        default=None,
        description="Persisted absolute score deadline as a Unix epoch",
    )
    terminal_reason: str | None = Field(
        default=None,
        description="Machine-readable terminal reason",
    )
    cleanup_outcome: JobTimeoutCleanupStatus | None = Field(
        default=None,
        description="Observed timeout cleanup result",
    )
    diagnostic: str | None = Field(
        default=None,
        description="Compatibility diagnostic for malformed legacy fields",
    )


class ObserverEvent(TypedDict):
    """Structured event emitted by the baton and routed through the daemon.

    Standard event types:
        sheet.started, sheet.completed, sheet.failed, sheet.retrying,
        sheet.validation_passed, sheet.validation_failed,
        job.cost_update, job.iteration

    Profiler event types:
        monitor.anomaly — resource anomaly detected by the profiler.
        Data payload: {"anomaly_type": str, "severity": str,
        "description": str, "pid": int | None, "metric_value": float,
        "threshold": float}
    """

    job_id: str
    sheet_num: int
    event: str
    data: dict[str, Any] | None
    timestamp: float


class DaemonStatus(BaseModel):
    """Current status snapshot of the running daemon.

    Returned by health check / status queries. Provides a lightweight
    overview without per-job detail.
    """

    pid: int = Field(
        description="Process ID of the daemon",
    )
    uptime_seconds: float = Field(
        description="Seconds since daemon started",
    )
    running_jobs: int = Field(
        description="Number of currently executing jobs",
    )
    total_jobs_active: int = Field(
        description="Total active jobs (proxy for sheet count until Phase 3 scheduler is wired)",
    )
    memory_usage_mb: float = Field(
        description="Current RSS memory usage in MB",
    )
    version: str = Field(
        description="Marianne version string",
    )
    protocol_version: int = Field(
        default=0,
        description=(
            "Marianne IPC protocol version advertised by the conductor "
            "(#265). 0 means a pre-versioning conductor that did not send "
            "the field. Clients compare against their own PROTOCOL_VERSION "
            "to detect CLI/conductor skew."
        ),
    )
