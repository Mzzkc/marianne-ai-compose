"""Shared helpers for run and resume commands.

Provides summary display, progress-bar construction, completion handling,
and CLI input validation. Backend and infrastructure setup now lives in
the daemon's baton/backend pool path — the legacy CLI factory cluster
(``create_backend``, ``setup_learning``, ``setup_notifications``,
``setup_escalation``, ``setup_grounding``, ``SetupComponents``,
``setup_all``) was removed in Phase 1 of the backend atlas migration.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.notifications import NotificationManager

from ..helpers import (
    is_quiet,
    is_verbose,
)
from ..output import console as default_console

if TYPE_CHECKING:
    from marianne.core.models import JobCompletionSummary


def display_run_summary(summary: JobCompletionSummary) -> None:
    """Display run summary as a rich panel.

    Shared by both `run` and `resume` commands to avoid duplication.

    Args:
        summary: Run summary with execution statistics.
    """
    if is_quiet():
        return

    status_color = {
        JobStatus.COMPLETED: "green",
        JobStatus.FAILED: "red",
        JobStatus.PAUSED: "yellow",
    }.get(summary.final_status, "white")

    status_text = f"[{status_color}]{summary.final_status.value.upper()}[/{status_color}]"

    lines = [
        f"[bold]{summary.job_name}[/bold]",
        f"Status: {status_text}",
        f"Duration: {summary._format_duration(summary.total_duration_seconds)}",
        "",
        "[bold]Sheets[/bold]",
        f"  Completed: [green]{summary.completed_sheets}[/green]/{summary.total_sheets}",
    ]

    if summary.failed_sheets > 0:
        lines.append(f"  Failed: [red]{summary.failed_sheets}[/red]")
    if summary.skipped_sheets > 0:
        lines.append(f"  Skipped: [yellow]{summary.skipped_sheets}[/yellow]")

    lines.append(f"  Success Rate: {summary.success_rate:.1f}%")

    if summary.validation_pass_count + summary.validation_fail_count > 0:
        lines.extend(
            [
                "",
                "[bold]Validation[/bold]",
                f"  Pass Rate: {summary.validation_pass_rate:.1f}%",
            ]
        )

    if is_verbose() or summary.total_retries > 0 or summary.rate_limit_waits > 0:
        lines.extend(
            [
                "",
                "[bold]Execution[/bold]",
            ]
        )
        if summary.successes_without_retry > 0:
            lines.append(
                f"  Success Without Retry: {summary.success_without_retry_rate:.0f}% "
                f"({summary.successes_without_retry}/{summary.completed_sheets})"
            )
        if summary.total_retries > 0:
            lines.append(f"  Retries Used: {summary.total_retries}")
        if summary.total_completion_attempts > 0:
            lines.append(f"  Completion Attempts: {summary.total_completion_attempts}")
        if summary.rate_limit_waits > 0:
            lines.append(f"  Rate Limit Waits: [yellow]{summary.rate_limit_waits}[/yellow]")

    default_console.print(
        Panel(
            "\n".join(lines),
            title="Run Summary",
            border_style="green" if summary.final_status == JobStatus.COMPLETED else "yellow",
        )
    )


def create_progress_bar(
    *,
    console: Console | None = None,
    include_exec_status: bool = False,
) -> Progress:
    """Create a Rich progress bar for sheet tracking.

    Shared by both `run` and `resume` commands. The `run` command includes
    an additional execution status field for real-time backend progress.

    Args:
        console: Rich console for output. Uses default if None.
        include_exec_status: If True, adds an exec_status field (run only).

    Returns:
        Configured Progress instance.
    """
    columns = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("\u2022"),
        TextColumn("{task.completed}/{task.total} sheets"),
        TextColumn("\u2022"),
        TimeElapsedColumn(),
        TextColumn("\u2022"),
        TextColumn("ETA: {task.fields[eta]}"),
    ]
    if include_exec_status:
        columns.extend(
            [
                TextColumn("\u2022"),
                TextColumn("[dim]{task.fields[exec_status]}[/dim]"),
            ]
        )
    return Progress(
        *columns,
        console=console or default_console,
        transient=False,
    )


async def handle_job_completion(
    *,
    state: CheckpointState,
    summary: JobCompletionSummary,
    notification_manager: NotificationManager | None,
    job_id: str,
    job_name: str,
    console: Console | None = None,
) -> None:
    """Handle post-execution status display and notifications.

    Shared by both `run` and `resume` commands. Displays the run summary
    and sends completion/failure notifications.

    Args:
        state: Final job checkpoint state.
        summary: Run summary with execution statistics.
        notification_manager: Optional notification manager for alerts.
        job_id: Job identifier for notifications.
        job_name: Job name for notifications.
        console: Console for output. Uses default if None.
    """
    _console = console or default_console

    if state.status == JobStatus.COMPLETED:
        display_run_summary(summary)
        if notification_manager:
            await notification_manager.notify_job_complete(
                job_id=job_id,
                job_name=job_name,
                success_count=summary.completed_sheets,
                failure_count=summary.failed_sheets,
                duration_seconds=summary.total_duration_seconds,
            )
    else:
        if not is_quiet():
            _console.print(f"[yellow]Score ended with status: {state.status.value}[/yellow]")
            display_run_summary(summary)
        if notification_manager and state.status == JobStatus.FAILED:
            await notification_manager.notify_job_failed(
                job_id=job_id,
                job_name=job_name,
                error_message=f"Score failed with status: {state.status.value}",
                sheet_num=state.current_sheet,
            )


# =============================================================================
# Input validation utilities
# =============================================================================

# Pattern for valid job IDs: alphanumeric, hyphens, underscores, dots
_JOB_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$")
_JOB_ID_MAX_LENGTH = 100


def validate_job_id(job_id: str) -> str:
    """Validate a job ID from CLI input.

    Job IDs must be alphanumeric with hyphens, underscores, and dots allowed.
    Must start with an alphanumeric character. Maximum 100 characters.

    Args:
        job_id: The raw job ID string from CLI input.

    Returns:
        The validated job ID (unchanged if valid).

    Raises:
        typer.BadParameter: If the job ID is invalid.
    """
    import typer

    if not job_id:
        raise typer.BadParameter("Score ID cannot be empty")

    if len(job_id) > _JOB_ID_MAX_LENGTH:
        raise typer.BadParameter(
            f"Score ID too long ({len(job_id)} chars, max {_JOB_ID_MAX_LENGTH})"
        )

    if not _JOB_ID_PATTERN.match(job_id):
        raise typer.BadParameter(
            f"Invalid score ID '{job_id}'. "
            "Must start with a letter or digit and contain only "
            "letters, digits, hyphens, underscores, and dots."
        )

    return job_id


def validate_start_sheet(start_sheet: int | None, total_sheets: int | None = None) -> int | None:
    """Validate --start-sheet value.

    Args:
        start_sheet: The raw start sheet value (None if not provided).
        total_sheets: Total sheets in the score (for range check, optional).

    Returns:
        The validated start_sheet value (unchanged if valid, None if not provided).

    Raises:
        typer.BadParameter: If the start sheet value is invalid.
    """
    import typer

    if start_sheet is None:
        return None

    if start_sheet < 1:
        raise typer.BadParameter(f"--start-sheet must be >= 1, got {start_sheet}")

    if total_sheets is not None and start_sheet > total_sheets:
        raise typer.BadParameter(
            f"--start-sheet {start_sheet} exceeds total sheets ({total_sheets})"
        )

    return start_sheet
