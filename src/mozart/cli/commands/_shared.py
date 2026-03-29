"""Shared execution setup logic for run and resume commands.

Extracts the common infrastructure setup that both _run_job() and _resume_job()
need: backend creation, learning stores, notification manager, escalation handler,
grounding engine, and summary display.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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

from mozart.core.checkpoint import CheckpointState, JobStatus
from mozart.notifications import NotificationManager

from ..helpers import (
    is_quiet,
    is_verbose,
)
from ..output import console as default_console

if TYPE_CHECKING:
    from mozart.backends.base import Backend
    from mozart.core.config import JobConfig
    from mozart.execution.escalation import ConsoleEscalationHandler
    from mozart.execution.grounding import GroundingEngine
    from mozart.execution.runner import RunSummary
    from mozart.learning.global_store import GlobalLearningStore
    from mozart.learning.outcomes import OutcomeStore


def create_backend(
    config: JobConfig,
    *,
    quiet: bool = False,
    console: Console | None = None,
) -> Backend:
    """Create the appropriate execution backend from job config.

    Delegates to ``execution.setup.create_backend()`` for core logic,
    then adds verbose CLI console output.

    Args:
        config: Job configuration with backend settings.
        quiet: If True, suppress verbose logging.
        console: Console for verbose output.

    Returns:
        Configured Backend instance.
    """
    from mozart.execution.setup import create_backend as _create_backend

    backend = _create_backend(config)

    if not quiet and is_verbose() and console:
        if config.backend.type == "recursive_light":
            rl_config = config.backend.recursive_light
            console.print(
                f"[dim]Using Recursive Light backend at {rl_config.endpoint}[/dim]"
            )
        elif config.backend.type == "anthropic_api":
            console.print(
                f"[dim]Using Anthropic API backend with model {config.backend.model}[/dim]"
            )

    return backend


def setup_learning(
    config: JobConfig,
    *,
    quiet: bool = False,
    console: Console | None = None,
) -> tuple[OutcomeStore | None, GlobalLearningStore | None]:
    """Setup outcome store and global learning store if learning is enabled.

    Delegates to ``execution.setup.setup_learning()`` for core logic,
    then adds verbose CLI console output.

    Args:
        config: Job configuration with learning settings.
        quiet: If True, suppress verbose logging.
        console: Console for verbose output.

    Returns:
        Tuple of (outcome_store, global_learning_store), either may be None.
    """
    from mozart.execution.setup import setup_learning as _setup_learning

    outcome_store, global_learning_store = _setup_learning(config)

    if outcome_store is not None and not quiet and is_verbose() and console:
        outcome_store_path = config.get_outcome_store_path()
        console.print(
            f"[dim]Learning enabled: outcomes at {outcome_store_path}[/dim]"
        )
        console.print(
            "[dim]Global learning enabled: cross-workspace patterns active[/dim]"
        )

    return outcome_store, global_learning_store


def setup_notifications(
    config: JobConfig,
    *,
    quiet: bool = False,
    console: Console | None = None,
) -> NotificationManager | None:
    """Setup notification manager from config.

    Delegates to ``execution.setup.setup_notifications()`` for core logic,
    then adds verbose CLI console output.

    Args:
        config: Job configuration with notification settings.
        quiet: If True, suppress verbose logging.
        console: Console for verbose output.

    Returns:
        NotificationManager if notifications configured, else None.
    """
    from mozart.execution.setup import setup_notifications as _setup_notifications

    notification_manager = _setup_notifications(config)

    if notification_manager is not None and not quiet and is_verbose() and console:
        console.print(
            "[dim]Notifications enabled[/dim]"
        )
    return notification_manager


def setup_escalation(
    config: JobConfig,
    *,
    enabled: bool = False,
    quiet: bool = False,
    console: Console | None = None,
) -> ConsoleEscalationHandler | None:
    """Setup escalation handler if enabled.

    Args:
        config: Job configuration with learning settings.
        enabled: Whether escalation is explicitly enabled via CLI flag.
        quiet: If True, suppress verbose logging.
        console: Console for verbose output.

    Returns:
        ConsoleEscalationHandler if enabled, else None.
    """
    if not enabled:
        return None

    from mozart.execution.escalation import ConsoleEscalationHandler

    config.learning.escalation_enabled = True
    handler = ConsoleEscalationHandler(
        confidence_threshold=config.learning.min_confidence_threshold,
        auto_retry_on_first_failure=True,
    )
    if not quiet and is_verbose() and console:
        console.print(
            "[dim]Escalation enabled: low-confidence sheets will prompt for decisions[/dim]"
        )
    return handler


def setup_grounding(
    config: JobConfig,
    *,
    quiet: bool = False,
    console: Console | None = None,
) -> GroundingEngine | None:
    """Setup grounding engine with hooks from config.

    Delegates to ``execution.setup.setup_grounding()`` for core logic,
    then adds verbose CLI console output.

    Args:
        config: Job configuration with grounding settings.
        quiet: If True, suppress verbose logging.
        console: Console for verbose output.

    Returns:
        GroundingEngine if grounding enabled, else None.
    """
    from mozart.execution.setup import setup_grounding as _setup_grounding

    engine = _setup_grounding(config)

    if engine is not None and not quiet and is_verbose() and console:
        hook_count = engine.get_hook_count()
        console.print(
            f"[dim]Grounding enabled: {hook_count} hook(s) registered[/dim]"
        )

    return engine


@dataclass
class SetupComponents:
    """All infrastructure components needed by both run and resume commands."""

    backend: Backend
    outcome_store: OutcomeStore | None
    global_learning_store: GlobalLearningStore | None
    notification_manager: NotificationManager | None
    escalation_handler: ConsoleEscalationHandler | None
    grounding_engine: GroundingEngine | None


def setup_all(
    config: JobConfig,
    *,
    escalation: bool = False,
    quiet: bool = False,
    console: Console | None = None,
) -> SetupComponents:
    """Setup all infrastructure components for job execution.

    Consolidates the 5-function setup sequence used by both run and resume
    commands into a single call, preventing drift between the two paths.

    Args:
        config: Job configuration.
        escalation: Whether escalation is explicitly enabled via CLI flag.
        quiet: If True, suppress verbose logging.
        console: Console for verbose output.

    Returns:
        SetupComponents with all configured infrastructure.
    """
    backend = create_backend(config, quiet=quiet, console=console)
    outcome_store, global_learning_store = setup_learning(config, quiet=quiet, console=console)
    notification_manager = setup_notifications(config, quiet=quiet, console=console)
    escalation_handler = setup_escalation(config, enabled=escalation, quiet=quiet, console=console)
    grounding_engine = setup_grounding(config, quiet=quiet, console=console)
    return SetupComponents(
        backend=backend,
        outcome_store=outcome_store,
        global_learning_store=global_learning_store,
        notification_manager=notification_manager,
        escalation_handler=escalation_handler,
        grounding_engine=grounding_engine,
    )


def display_run_summary(summary: RunSummary) -> None:
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
        lines.extend([
            "",
            "[bold]Validation[/bold]",
            f"  Pass Rate: {summary.validation_pass_rate:.1f}%",
        ])

    if is_verbose() or summary.total_retries > 0 or summary.rate_limit_waits > 0:
        lines.extend([
            "",
            "[bold]Execution[/bold]",
        ])
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

    default_console.print(Panel(
        "\n".join(lines),
        title="Run Summary",
        border_style="green" if summary.final_status == JobStatus.COMPLETED else "yellow",
    ))


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
        columns.extend([
            TextColumn("\u2022"),
            TextColumn("[dim]{task.fields[exec_status]}[/dim]"),
        ])
    return Progress(
        *columns,
        console=console or default_console,
        transient=False,
    )


async def handle_job_completion(
    *,
    state: CheckpointState,
    summary: RunSummary,
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
            _console.print(
                f"[yellow]Score ended with status: {state.status.value}[/yellow]"
            )
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
        raise typer.BadParameter("Job ID cannot be empty")

    if len(job_id) > _JOB_ID_MAX_LENGTH:
        raise typer.BadParameter(
            f"Job ID too long ({len(job_id)} chars, max {_JOB_ID_MAX_LENGTH})"
        )

    if not _JOB_ID_PATTERN.match(job_id):
        raise typer.BadParameter(
            f"Invalid job ID '{job_id}'. "
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
        raise typer.BadParameter(
            f"--start-sheet must be >= 1, got {start_sheet}"
        )

    if total_sheets is not None and start_sheet > total_sheets:
        raise typer.BadParameter(
            f"--start-sheet {start_sheet} exceeds total sheets ({total_sheets})"
        )

    return start_sheet
