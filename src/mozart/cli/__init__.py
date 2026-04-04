"""Mozart CLI - modular command structure.

This package provides a modular CLI implementation for Mozart AI Compose.
The CLI is built using Typer and organized into command modules for maintainability.

★ Insight ─────────────────────────────────────
1. **Typer callback for global options**: The @app.callback() decorator defines
   options that apply to ALL commands (--verbose, --quiet, --version). These
   callbacks run BEFORE any command, setting up shared state like output level
   and logging configuration.

2. **Command registration via app.command()**: Each command function is registered
   with the Typer app using app.command(). The function name becomes the CLI
   command name (with underscores converted to hyphens). Custom names are set
   via the `name` parameter.

3. **Modular command imports**: Commands are organized in separate modules
   (run.py, status.py, etc.) and imported here for registration. This keeps
   the main __init__.py focused on assembly while command logic lives in
   dedicated files.
─────────────────────────────────────────────────

Package structure:
    cli/
    ├── __init__.py           # This file - app assembly (~150 LOC)
    ├── helpers.py            # Shared utilities (~600 LOC)
    ├── output.py             # Rich formatting (~400 LOC)
    └── commands/
        ├── __init__.py       # Command exports
        ├── run.py            # run command
        ├── status.py         # status, list_jobs commands
        ├── resume.py         # resume command
        ├── pause.py          # pause, modify commands
        ├── cancel.py         # cancel command
        ├── validate.py       # validate command
        ├── recover.py        # recover command (hidden)
        ├── diagnose.py       # logs, errors, diagnose commands
        ├── dashboard.py      # dashboard, mcp commands
        └── learning.py       # patterns-*, learning-* commands
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from mozart import __version__

# Re-export helpers module for direct access to internal state (conftest.py needs this)
from . import helpers as helpers
from .commands import (
    # cancel.py
    cancel,
    # status.py
    clear,
    clear_rate_limits,
    # diagnose.py
    diagnose,
    # doctor.py
    doctor,
    errors,
    history,
    # init_cmd.py
    init,
    # status.py
    list_jobs,
    logs,
    # pause.py
    modify,
    pause,
    # recover.py
    recover,
    # resume.py
    resume,
    # run.py
    run,
    status,
    # validate.py
    validate,
)
from .commands.conductor import (
    conductor_status,
    restart,
    start,
    stop,
)
from .commands.config_cmd import config_app
from .commands.dashboard import dashboard, mcp
from .commands.instruments import instruments_app
from .commands.learning import (
    entropy_status,
    learning_activity,
    learning_drift,
    learning_epistemic_drift,
    learning_export,
    learning_insights,
    learning_record_evolution,
    learning_stats,
    patterns_budget,
    patterns_entropy,
    patterns_list,
    patterns_why,
)
from .commands.status import _output_status_rich
from .commands.top import top

# Import helper functions for re-export
from .helpers import (
    OutputLevel,
    configure_global_logging,
    create_notifiers_from_config,
    set_log_file,
    set_log_format,
    set_log_level,
    set_output_level,
)
from .output import console

# =============================================================================
# Typer app definition
# =============================================================================

app = typer.Typer(
    name="mozart",
    help="Orchestration system for AI agent workflows",
    add_completion=False,
)


# =============================================================================
# Global option callbacks
# =============================================================================


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"Mozart AI Compose v{__version__}")
        raise typer.Exit()


def verbose_callback(value: bool) -> None:
    """Set verbose output mode."""
    if value:
        set_output_level(OutputLevel.VERBOSE)


def quiet_callback(value: bool) -> None:
    """Set quiet output mode."""
    if value:
        set_output_level(OutputLevel.QUIET)


def log_level_callback(value: str | None) -> str | None:
    """Set log level from CLI option."""
    if value:
        set_log_level(value)
    return value


def log_file_callback(value: Path | None) -> Path | None:
    """Set log file path from CLI option."""
    if value:
        set_log_file(value)
    return value


def log_format_callback(value: str | None) -> str | None:
    """Set log format from CLI option."""
    if value:
        set_log_format(value)
    return value


def conductor_clone_callback(value: str | None) -> str | None:
    """Set the active conductor clone name.

    When --conductor-clone is passed, all daemon interactions are routed
    to a clone conductor instead of the production one. The clone has
    its own socket, PID file, state DB, and log file.

    This enables safe testing without risking the production conductor.

    Usage (always use = syntax):
        mozart --conductor-clone= status             # Default clone
        mozart --conductor-clone=staging run x.yaml  # Named clone
    """
    if value is not None:
        from mozart.daemon.clone import set_clone_name

        set_clone_name(value)
    return value


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        callback=verbose_callback,
        is_eager=True,
        help="Show detailed output with additional information",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        callback=quiet_callback,
        is_eager=True,
        help="Show minimal output (errors only)",
    ),
    conductor_clone: Annotated[
        str | None,
        typer.Option(
            "--conductor-clone",
            callback=conductor_clone_callback,
            is_eager=True,
            help="Route all daemon interactions to a clone conductor. "
            "Use --conductor-clone= (with equals sign) for default clone, "
            "or --conductor-clone=NAME for a named clone. "
            "The clone has its own socket, PID file, state DB, and log.",
        ),
    ] = None,
    log_level: Annotated[
        str | None,
        typer.Option(
            "--log-level",
            "-L",
            callback=log_level_callback,
            help="Logging level (DEBUG, INFO, WARNING, ERROR)",
            envvar="MOZART_LOG_LEVEL",
        ),
    ] = None,
    log_file: Annotated[
        Path | None,
        typer.Option(
            "--log-file",
            callback=log_file_callback,
            help="Path for log file output",
            envvar="MOZART_LOG_FILE",
        ),
    ] = None,
    log_format: Annotated[
        str | None,
        typer.Option(
            "--log-format",
            callback=log_format_callback,
            help="Log format: json, console, or both",
            envvar="MOZART_LOG_FORMAT",
        ),
    ] = None,
) -> None:
    """Mozart AI Compose - Orchestration system for AI agent workflows."""
    # Configure logging based on CLI options (called once)
    configure_global_logging(console)


# =============================================================================
# Core command registration
# =============================================================================

# Getting started
app.command(rich_help_panel="Getting Started")(init)

# Job execution commands
app.command(rich_help_panel="Jobs")(run)
app.command(rich_help_panel="Jobs")(resume)
app.command(rich_help_panel="Jobs")(pause)
app.command(rich_help_panel="Jobs")(modify)
app.command(rich_help_panel="Jobs")(cancel)
app.command(rich_help_panel="Jobs")(validate)

# Job status commands
app.command(rich_help_panel="Monitoring")(status)
app.command(name="list", rich_help_panel="Monitoring")(list_jobs)
app.command(rich_help_panel="Monitoring")(top)
app.command(rich_help_panel="Monitoring")(clear)

# Diagnostic commands
app.command(rich_help_panel="Diagnostics")(logs)
app.command(rich_help_panel="Diagnostics")(errors)
app.command(rich_help_panel="Diagnostics")(diagnose)
app.command(rich_help_panel="Diagnostics")(history)
app.command(rich_help_panel="Diagnostics")(doctor)
app.command(hidden=True)(recover)  # Hidden - recovery is advanced operation

# Server commands
app.command(rich_help_panel="Services")(dashboard)
app.command(rich_help_panel="Services")(mcp)

# Conductor lifecycle commands
app.command(rich_help_panel="Conductor")(start)
app.command(rich_help_panel="Conductor")(stop)
app.command(rich_help_panel="Conductor")(restart)
app.command(name="conductor-status", rich_help_panel="Conductor")(conductor_status)
app.command(name="clear-rate-limits", rich_help_panel="Conductor")(clear_rate_limits)

# Daemon configuration
app.add_typer(config_app)

# Instrument management
app.add_typer(instruments_app)

# =============================================================================
# Learning system commands
# =============================================================================

# Pattern analysis commands
app.command(name="patterns-list", rich_help_panel="Learning")(patterns_list)
app.command(name="patterns-why", rich_help_panel="Learning")(patterns_why)
app.command(name="patterns-entropy", rich_help_panel="Learning")(patterns_entropy)
app.command(name="patterns-budget", rich_help_panel="Learning")(patterns_budget)

# Learning statistics and insights
app.command(name="learning-stats", rich_help_panel="Learning")(learning_stats)
app.command(name="learning-insights", rich_help_panel="Learning")(learning_insights)
app.command(name="learning-drift", rich_help_panel="Learning")(learning_drift)
app.command(name="learning-epistemic-drift", rich_help_panel="Learning")(learning_epistemic_drift)
app.command(name="learning-activity", rich_help_panel="Learning")(learning_activity)

# Learning data export
app.command(name="learning-export", rich_help_panel="Learning")(learning_export)
app.command(name="learning-record-evolution", rich_help_panel="Learning")(learning_record_evolution)

# System health monitoring
app.command(name="entropy-status", rich_help_panel="Learning")(entropy_status)


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "app",
    "main",
    "console",
    # Re-export helpers for convenience
    "OutputLevel",
    # Helper functions (used by tests)
    "create_notifiers_from_config",
    "_output_status_rich",
]
