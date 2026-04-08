"""Project initialization command for Marianne CLI.

Implements ``mzt init`` — scaffolds a new Marianne project with a starter
score and ``.marianne/`` directory.  This is the first-run experience for
new users.

The generated score is a practical template with comments explaining every
field.  Users edit it with their task, then run it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import typer

from ..output import console, output_error, output_json

_logger = logging.getLogger(__name__)

# Score names must be safe for file paths and YAML identifiers.
# Allowed: alphanumeric, hyphens, underscores.  Must not start with dot.
_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _validate_name(name: str) -> str | None:
    """Validate a score name, returning an error message or None if valid."""
    if not name:
        return "Score name cannot be empty."
    if "\x00" in name:
        return "Score name contains invalid characters."
    if "/" in name or "\\" in name:
        return "Score name cannot contain path separators."
    if " " in name:
        return "Score name cannot contain spaces. Use hyphens or underscores instead."
    if name.startswith("."):
        return "Score name cannot start with a dot."
    if not _VALID_NAME_RE.match(name):
        return (
            f"Invalid score name '{name}'. "
            "Use alphanumeric characters, hyphens, and underscores."
        )
    return None


# ---------------------------------------------------------------------------
# Starter score template
# ---------------------------------------------------------------------------

_STARTER_SCORE_TEMPLATE = """\
# {name}.yaml — Your First Marianne Score
#
# A score tells the conductor what to do.  Edit the prompt below with
# your actual task, then run it:
#
#   mzt start                    # Start the conductor (once)
#   mzt run {name}.yaml          # Run this score
#   mzt status {name}            # Check progress
#
# Docs: https://github.com/Mzzkc/marianne-ai-compose

name: {name}
description: "A starter score — edit this with your task"

# Where outputs are written.  Relative to this file.
workspace: ./workspaces/{name}

# Which instrument plays the score.
# Available instruments: claude-code, gemini-cli, codex-cli, aider, goose, cline-cli
# Run `mzt instruments list` to see what's available.
instrument: claude-code
instrument_config:
  timeout_seconds: 300

# How the score is divided.
# size=1 means each sheet processes 1 item.
# total_items is how many items to process (= number of sheets).
sheet:
  size: 1
  total_items: 3

# The task given to each sheet's musician (AI agent).
# Use Jinja2 variables: {{{{ sheet_num }}}}, {{{{ total_sheets }}}},
# {{{{ workspace }}}}, {{{{ movement }}}}, {{{{ voice }}}}.
prompt:
  template: |
    You are working on sheet {{{{ sheet_num }}}} of {{{{ total_sheets }}}}.

    Your workspace is: {{{{ workspace }}}}

    ## Your Task

    Write a short creative paragraph about topic {{{{ sheet_num }}}} of {{{{ total_sheets }}}}.
    Save your output to {{{{ workspace }}}}/output-{{{{ sheet_num }}}}.md

# Validations: how Marianne knows the sheet succeeded.
# These checks run after each sheet completes.
validations:
  - type: file_exists
    path: "{{workspace}}/output-{{sheet_num}}.md"
"""


def _generate_starter_score(name: str) -> str:
    """Generate a starter score YAML with comments."""
    return _STARTER_SCORE_TEMPLATE.format(name=name)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def init(
    score_name: str | None = typer.Argument(
        None,
        help="Score name (e.g. 'my-project'). Same as --name.",
    ),
    path: Path = typer.Option(
        ".",
        "--path",
        "-p",
        help="Directory to initialize (default: current directory)",
    ),
    name: str = typer.Option(
        "my-score",
        "--name",
        "-n",
        help="Name for the starter score",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing files",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output result as JSON",
    ),
) -> None:
    """Scaffold a new Marianne project with a starter score.

    Creates a starter score YAML and .marianne/ project directory.
    Edit the score with your task, then run it with the conductor.

    Examples:
        mzt init
        mzt init my-project
        mzt init --path ./my-project
        mzt init --name data-pipeline
        mzt init --force
        mzt init --json
    """
    # Resolve name: positional arg is convenience shorthand for --name.
    # --name flag takes precedence when both are provided (flag is explicit).
    if score_name is not None and name == "my-score":
        name = score_name

    # Validate name before touching the filesystem
    name_error = _validate_name(name)
    if name_error:
        output_error(
            name_error,
            hints=["Use a simple name like 'my-score' or 'data-pipeline'."],
            json_output=json_output,
        )
        raise typer.Exit(1)

    target = path.resolve()
    score_file = target / f"{name}.yaml"
    marianne_dir = target / ".marianne"

    # Safety: refuse to overwrite without --force
    if not force:
        if score_file.exists():
            output_error(
                f"Score already exists: {score_file}",
                severity="warning",
                hints=["Use --force to overwrite."],
                json_output=json_output,
            )
            raise typer.Exit(1)
        if marianne_dir.exists():
            output_error(
                f"Marianne project already initialized: {marianne_dir}",
                severity="warning",
                hints=["Use --force to reinitialize."],
                json_output=json_output,
            )
            raise typer.Exit(1)

    # Create .marianne/ directory
    marianne_dir.mkdir(parents=True, exist_ok=True)

    # Create workspaces/ directory so `mzt validate` passes immediately
    workspaces_dir = target / "workspaces"
    workspaces_dir.mkdir(parents=True, exist_ok=True)

    # Generate and write starter score
    score_content = _generate_starter_score(name)
    score_file.write_text(score_content)

    _logger.info("init.complete", extra={"target_path": str(target), "score_name": name})

    # Output
    if json_output:
        output_json({
            "success": True,
            "name": name,
            "score_file": str(score_file),
            "target_path": str(target),
        })
    else:
        console.print(
            f"\n[bold green]Marianne project initialized[/bold green] in {target}\n"
        )
        console.print(
            f"  Created: [bold]{name}.yaml[/bold]"
            "        (starter score — edit with your task)"
        )
        console.print(
            "  Created: [bold].marianne/[/bold]"
            "              (project config directory)"
        )
        console.print()
        console.print("[dim]Next steps:[/dim]")
        console.print("  0. [bold]mzt doctor[/bold]              (check your environment)")
        console.print(f"  1. Edit [bold]{name}.yaml[/bold] with your task")
        console.print(
            f"  2. [bold]mzt start[/bold] && "
            f"[bold]mzt run {name}.yaml[/bold]"
        )
        console.print(f"  3. [bold]mzt status {name}[/bold] to watch progress")
        console.print()


__all__ = ["init"]
