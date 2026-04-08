"""Workspace and environment configuration models.

Defines models for isolation, workspace lifecycle, cross-sheet context,
logging, AI review, and feedback.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IsolationMode(str, Enum):
    """Isolation method for parallel job execution."""

    NONE = "none"  # Default: no isolation
    WORKTREE = "worktree"  # Git worktree isolation


class IsolationConfig(BaseModel):
    """Configuration for execution isolation.

    Worktree isolation creates a separate git working directory for each job,
    enabling safe parallel execution where multiple jobs can modify code
    without interfering with each other.

    Example YAML:
        isolation:
          enabled: true
          mode: worktree
          branch_prefix: marianne
          cleanup_on_success: true
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable execution isolation for parallel-safe jobs. "
        "When true, creates isolated worktree before execution.",
    )

    mode: IsolationMode = Field(
        default=IsolationMode.WORKTREE,
        description="Isolation method. Currently only 'worktree' is supported.",
    )

    worktree_base: Path | None = Field(
        default=None,
        description=(
            "Directory for worktrees. None means resolved"
            " dynamically to <repo>/.worktrees at runtime."
        ),
    )

    branch_prefix: str = Field(
        default="marianne",
        description="Prefix for worktree branch names. "
        "Branch format: {prefix}/{job-id}",
        pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$",  # Valid git ref prefix
    )

    source_branch: str | None = Field(
        default=None,
        description="Branch to base worktree on. Default: current branch (HEAD).",
    )

    cleanup_on_success: bool = Field(
        default=True,
        description="Remove worktree after successful job completion. "
        "Branch is preserved for review/merge.",
    )

    cleanup_on_failure: bool = Field(
        default=False,
        description="Remove worktree when job fails. "
        "Default False to enable debugging.",
    )

    lock_during_execution: bool = Field(
        default=True,
        description="Lock worktree during execution to prevent accidental removal. "
        "Uses 'git worktree lock' with job info as reason.",
    )

    fallback_on_error: bool = Field(
        default=True,
        description="If worktree creation fails, continue without isolation. "
        "When False, job fails if isolation cannot be established.",
    )

    def get_worktree_base(self, workspace: Path) -> Path:
        """Get the directory where worktrees are created."""
        if self.worktree_base:
            return self.worktree_base
        return workspace / ".worktrees"

    def get_branch_name(self, job_id: str) -> str:
        """Generate branch name for a job."""
        return f"{self.branch_prefix}/{job_id}"


class WorkspaceLifecycleConfig(BaseModel):
    """Configuration for workspace lifecycle management.

    Controls how workspace files are handled across job iterations,
    particularly for self-chaining jobs that reuse the same workspace.

    When archive_on_fresh is True and --fresh is used, Marianne moves
    non-essential workspace files to a numbered archive subdirectory
    before clearing state. This prevents stale file_exists and
    command_succeeds validations from passing on previous iteration's
    artifacts.

    Example YAML:
        workspace_lifecycle:
          archive_on_fresh: true
          archive_dir: archive
          max_archives: 10
          preserve_patterns:
            - ".iteration"
            - ".marianne-*"
            - ".coverage"
            - "archive/**"
            - ".worktrees/**"
    """

    model_config = ConfigDict(extra="forbid")

    archive_on_fresh: bool = Field(
        default=False,
        description="Archive workspace files when --fresh flag is used. "
        "Moves non-preserved files to a numbered archive subdirectory.",
    )
    archive_dir: str = Field(
        default="archive",
        description="Subdirectory within workspace for archive storage.",
    )
    archive_naming: Literal["iteration", "timestamp"] = Field(
        default="iteration",
        description="Naming scheme for archive directories. "
        "'iteration' reads .iteration file, 'timestamp' uses current time.",
    )
    max_archives: int = Field(
        default=0,
        ge=0,
        description="Maximum archive directories to keep. 0 = unlimited. "
        "When exceeded, oldest archives are deleted.",
    )
    preserve_patterns: list[str] = Field(
        default=[
            ".iteration",
            ".marianne-*",
            ".coverage",
            "archive/**",
            ".worktrees/**",
        ],
        description="Glob patterns for files/directories to preserve (not archive). "
        "Matched against paths relative to workspace root.",
    )

    @field_validator("preserve_patterns")
    @classmethod
    def _validate_glob_patterns(cls, v: list[str]) -> list[str]:
        import fnmatch

        for pattern in v:
            if not pattern:
                raise ValueError("preserve_patterns must not contain empty strings")
            try:
                fnmatch.translate(pattern)
            except re.error as e:
                raise ValueError(
                    f"Invalid glob pattern {pattern!r}: {e}"
                ) from e
        return v


class LogConfig(BaseModel):
    """Configuration for structured logging.

    Controls log level, output format, and file rotation settings.
    """

    model_config = ConfigDict(extra="forbid")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Minimum log level to capture",
    )
    format: Literal["json", "console", "both"] = Field(
        default="console",
        description="Output format: json for structured, console for human-readable, "
        "both for console to stderr and JSON to file",
    )
    file_path: Path | None = Field(
        default=None,
        description="Path for log file output (required if format='both')",
    )
    max_file_size_mb: int = Field(
        default=50,
        gt=0,
        le=1000,
        description="Maximum log file size before rotation (MB)",
    )
    backup_count: int = Field(
        default=5,
        ge=0,
        le=100,
        description="Number of rotated log files to keep",
    )
    include_timestamps: bool = Field(
        default=True,
        description="Include ISO8601 UTC timestamps in log entries",
    )
    include_context: bool = Field(
        default=True,
        description="Include bound context (job_id, sheet_num) in log entries",
    )

    @model_validator(mode="after")
    def _check_file_path_required(self) -> LogConfig:
        """Validate that file_path is set when format requires file output."""
        if self.format == "both" and self.file_path is None:
            raise ValueError(
                f"file_path is required when format='{self.format}'"
            )
        return self


class AIReviewConfig(BaseModel):
    """Configuration for AI-powered code review after batch execution.

    Enables automated quality assessment of code changes with scoring.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable AI code review after each batch",
    )
    min_score: int = Field(
        default=60,
        ge=0,
        le=100,
        description="Minimum score to pass (0-100). Below this triggers retry.",
    )
    target_score: int = Field(
        default=80,
        ge=0,
        le=100,
        description="Target score for high quality (0-100). 60-target logs warning.",
    )
    on_low_score: Literal["retry", "warn", "fail"] = Field(
        default="warn",
        description="Action when score < min_score: retry, warn, or fail",
    )
    max_retry_for_review: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum retries when score is too low",
    )
    review_prompt_template: str | None = Field(
        default=None,
        description="Custom prompt template for review (uses default if None)",
    )

    @model_validator(mode="after")
    def _check_score_range(self) -> AIReviewConfig:
        """Validate that min_score <= target_score."""
        if self.min_score > self.target_score:
            raise ValueError(
                f"min_score ({self.min_score}) must be <= target_score ({self.target_score})"
            )
        return self


class CrossSheetConfig(BaseModel):
    """Configuration for cross-sheet context passing.

    Enables templates to access outputs from previous sheets, allowing
    later sheets to build on results from earlier ones without manual
    file reading. This is useful for multi-phase workflows where each
    sheet needs context from prior execution.
    """

    model_config = ConfigDict(extra="forbid")

    auto_capture_stdout: bool = Field(
        default=False,
        description="Automatically include previous sheets' stdout_tail in context. "
        "When True, templates can access {{ previous_outputs[1] }} etc.",
    )
    max_output_chars: int = Field(
        default=2000,
        gt=0,
        description="Maximum characters per previous sheet output. "
        "Outputs are truncated to this limit to avoid bloating prompts.",
    )
    capture_files: list[str] = Field(
        default_factory=list,
        description="File path patterns to read between sheets. "
        "Supports Jinja2 templating (e.g., '{{ workspace }}/sheet-{{ sheet_num - 1 }}.md'). "
        "File contents are available in {{ previous_files }}.",
    )
    lookback_sheets: int = Field(
        default=3,
        ge=0,
        description="Number of previous sheets to include (0 = all completed sheets). "
        "Limits context size for jobs with many sheets.",
    )


class FeedbackConfig(BaseModel):
    """Configuration for developer feedback collection (GH#15).

    When enabled, Marianne extracts structured feedback from agent output
    after each sheet execution. Feedback is stored in SheetState.agent_feedback.

    Example YAML:
        feedback:
          enabled: true
          pattern: '(?s)FEEDBACK_START(.+?)FEEDBACK_END'
          format: json
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable agent feedback extraction from output.",
    )
    pattern: str = Field(
        default=r"(?s)FEEDBACK_START(.+?)FEEDBACK_END",
        description="Regex pattern with a capture group to extract feedback from agent output. "
        "The first capture group contents are parsed according to 'format'.",
    )

    @field_validator("pattern")
    @classmethod
    def _validate_regex_pattern(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"pattern is not valid regex: {v!r} — {e}") from e
        return v

    format: Literal["json", "yaml", "text"] = Field(
        default="json",
        description="Format of the extracted feedback block. "
        "'json': parsed as JSON dict, 'yaml': parsed as YAML, 'text': stored as-is.",
    )
