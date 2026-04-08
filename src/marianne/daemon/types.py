"""Shared data types for the Marianne daemon.

Defines request/response models and status types used across daemon components
(server, service, CLI bridge). All models are Pydantic v2 BaseModel for
serialization over IPC.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Required, TypedDict

from pydantic import BaseModel, Field

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
    start_sheet: int | None
    dry_run: bool
    chain_depth: int | None


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


class ObserverEvent(TypedDict):
    """Structured event emitted by the runner and routed through the daemon.

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
