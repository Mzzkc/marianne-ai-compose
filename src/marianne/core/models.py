"""Core domain models for job execution summaries.

Contains Pydantic v2 models that provide the public contract between
the execution engine (baton/runner) and the rest of the system.

JobCompletionSummary replaces the legacy RunSummary dataclass with a
validated Pydantic model. The RunSummary alias in core.summary provides
backward compatibility.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetStatus


class JobCompletionSummary(BaseModel):
    """Summary of a completed job run.

    Pydantic v2 model tracking key metrics for display at job completion:
    - Sheet success/failure/skip counts
    - Validation pass rate
    - Cost tracking
    - Duration and retry statistics
    - Hook execution results
    """

    job_id: str = Field(description="Unique identifier for the job")
    job_name: str = Field(default="", description="Human-readable name of the job")
    total_sheets: int = Field(description="Total number of sheets in the job")
    completed_sheets: int = Field(
        default=0, description="Number of sheets completed successfully"
    )
    failed_sheets: int = Field(default=0, description="Number of sheets that failed")
    skipped_sheets: int = Field(default=0, description="Number of sheets skipped")
    total_cost_usd: float = Field(
        default=0.0, description="Total estimated cost in USD"
    )
    total_duration_seconds: float = Field(
        default=0.0, description="Total execution duration in seconds"
    )
    validation_pass_rate: float = Field(
        default=100.0, description="Percentage of validations that passed"
    )
    success_without_retry_rate: float = Field(
        default=0.0,
        description="Percentage of sheets succeeding on first attempt",
    )

    # Execution statistics
    total_retries: int = Field(
        default=0, description="Total retry attempts across all sheets"
    )
    total_completion_attempts: int = Field(
        default=0, description="Total completion-mode attempts"
    )
    rate_limit_waits: int = Field(
        default=0, description="Number of rate limit waits encountered"
    )
    validation_pass_count: int = Field(
        default=0, description="Number of validations that passed"
    )
    validation_fail_count: int = Field(
        default=0, description="Number of validations that failed"
    )
    successes_without_retry: int = Field(
        default=0, description="Sheets that succeeded on first attempt"
    )
    final_status: JobStatus = Field(
        default=JobStatus.PENDING, description="Final job status"
    )

    # Cost tracking detail
    total_input_tokens: int = Field(
        default=0, description="Total input tokens consumed"
    )
    total_output_tokens: int = Field(
        default=0, description="Total output tokens consumed"
    )
    total_estimated_cost: float = Field(
        default=0.0, description="Total estimated cost (legacy field)"
    )
    cost_limit_hit: bool = Field(
        default=False, description="Whether cost limit was reached"
    )

    # Hook execution results (Concert orchestration)
    hook_results: list[Any] = Field(
        default_factory=list, description="Results from post-success hooks"
    )
    hooks_executed: int = Field(
        default=0, description="Number of hooks executed"
    )
    hooks_succeeded: int = Field(
        default=0, description="Number of hooks that succeeded"
    )
    hooks_failed: int = Field(
        default=0, description="Number of hooks that failed"
    )

    @model_validator(mode="after")
    def _validate_and_compute(self) -> JobCompletionSummary:
        """Validate sheet counts and compute derived rates from counts."""
        if self.completed_sheets > self.total_sheets:
            msg = (
                f"completed_sheets ({self.completed_sheets}) "
                f"exceeds total_sheets ({self.total_sheets})"
            )
            raise ValueError(msg)

        # Auto-compute validation_pass_rate from counts when counts are set
        total_validations = self.validation_pass_count + self.validation_fail_count
        if total_validations > 0:
            self.validation_pass_rate = (
                self.validation_pass_count / total_validations
            ) * 100

        # Auto-compute success_without_retry_rate from counts when available
        if self.completed_sheets > 0 and self.success_without_retry_rate == 0.0:
            self.success_without_retry_rate = (
                self.successes_without_retry / self.completed_sheets
            ) * 100

        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success_rate(self) -> float:
        """Calculate sheet success rate as percentage.

        Skipped sheets are excluded from the denominator since they were
        never attempted (e.g., skip_when conditions met).
        """
        executed = self.total_sheets - self.skipped_sheets
        if executed == 0:
            return 0.0
        return (self.completed_sheets / executed) * 100

    @classmethod
    def from_checkpoint(
        cls, checkpoint: CheckpointState
    ) -> JobCompletionSummary:
        """Construct a summary from checkpoint state.

        Computes rates from sheet states, sums costs and durations.

        Args:
            checkpoint: The checkpoint state to summarize.

        Returns:
            JobCompletionSummary with computed metrics.
        """
        completed = 0
        failed = 0
        skipped = 0
        total_cost = 0.0
        total_duration = 0.0
        validation_passed = 0
        validation_failed = 0
        successes_no_retry = 0

        for sheet_state in checkpoint.sheets.values():
            if sheet_state.status == SheetStatus.COMPLETED:
                completed += 1
                if sheet_state.success_without_retry:
                    successes_no_retry += 1
            elif sheet_state.status == SheetStatus.FAILED:
                failed += 1
            elif sheet_state.status == SheetStatus.SKIPPED:
                skipped += 1

            if sheet_state.validation_passed is True:
                validation_passed += 1
            elif sheet_state.validation_passed is False:
                validation_failed += 1

            total_cost += sheet_state.total_cost_usd
            total_duration += sheet_state.total_duration_seconds

        total_validations = validation_passed + validation_failed
        val_rate = (
            (validation_passed / total_validations * 100)
            if total_validations > 0
            else 100.0
        )
        no_retry_rate = (
            (successes_no_retry / completed * 100) if completed > 0 else 0.0
        )

        return cls(
            job_id=checkpoint.job_id,
            job_name=checkpoint.job_name,
            total_sheets=checkpoint.total_sheets,
            completed_sheets=completed,
            failed_sheets=failed,
            skipped_sheets=skipped,
            total_cost_usd=total_cost,
            total_duration_seconds=total_duration,
            validation_pass_rate=val_rate,
            success_without_retry_rate=no_retry_rate,
            validation_pass_count=validation_passed,
            validation_fail_count=validation_failed,
            successes_without_retry=successes_no_retry,
            final_status=checkpoint.status,
            total_input_tokens=checkpoint.total_input_tokens,
            total_output_tokens=checkpoint.total_output_tokens,
            total_estimated_cost=checkpoint.total_estimated_cost,
            cost_limit_hit=checkpoint.cost_limit_reached,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert summary to dictionary for JSON output."""
        return {
            "job_id": self.job_id,
            "job_name": self.job_name,
            "status": self.final_status.value,
            "duration_seconds": round(self.total_duration_seconds, 2),
            "duration_formatted": self._format_duration(
                self.total_duration_seconds
            ),
            "sheets": {
                "total": self.total_sheets,
                "completed": self.completed_sheets,
                "failed": self.failed_sheets,
                "skipped": self.skipped_sheets,
                "success_rate": round(self.success_rate, 1),
            },
            "validation": {
                "passed": self.validation_pass_count,
                "failed": self.validation_fail_count,
                "pass_rate": round(self.validation_pass_rate, 1),
            },
            "execution": {
                "total_retries": self.total_retries,
                "completion_attempts": self.total_completion_attempts,
                "rate_limit_waits": self.rate_limit_waits,
                "successes_without_retry": self.successes_without_retry,
                "success_without_retry_rate": round(
                    self.success_without_retry_rate, 1
                ),
            },
        }

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format duration in human-readable form."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"
