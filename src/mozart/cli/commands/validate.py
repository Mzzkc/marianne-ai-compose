"""Validate command for Mozart CLI.

This module implements the `mozart validate` command for comprehensive
configuration validation before job execution.

★ Insight ─────────────────────────────────────
1. **Multi-layer validation**: The validate command performs 3 distinct validation
   layers: YAML syntax (parseable), Pydantic schema (structural), and extended
   checks (semantic). Each layer catches different classes of errors.

2. **Exit code convention**: Exit codes follow a convention (0=valid, 1=errors,
   2=cannot parse). This enables CI/CD integration where scripts can differentiate
   between "has validation errors" vs "cannot even validate" scenarios.

3. **DAG visualization**: When sheet dependencies are configured, the command
   visualizes the execution graph. This helps users understand parallel execution
   potential and identify dependency bottlenecks.
─────────────────────────────────────────────────
"""

from __future__ import annotations

from pathlib import Path

import typer
import yaml

from mozart.core.config import JobConfig
from mozart.validation import (
    ValidationReporter,
    ValidationRunner,
    create_default_checks,
)

from ..helpers import configure_global_logging
from ..output import console, output_error


def validate(
    config_file: Path = typer.Argument(
        ...,
        help="Path to YAML score configuration file",
        exists=True,
        readable=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output validation results as JSON",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed validation output",
    ),
) -> None:
    """Validate a job configuration file.

    Performs comprehensive validation including:
    - YAML syntax and Pydantic schema validation
    - Jinja template syntax checking
    - Path existence verification
    - Regex pattern compilation
    - Configuration completeness checks

    Exit codes:
      0: Valid (warnings/info OK)
      1: Invalid (one or more errors)
      2: Cannot validate (file not found, YAML unparseable)
    """
    configure_global_logging(console)

    # First try to read and parse YAML
    try:
        raw_yaml = config_file.read_text()
    except Exception as e:
        output_error(
            f"Cannot read score file: {e}",
            hints=["Check that the file exists and you have read permission."],
            json_output=json_output,
        )
        raise typer.Exit(2) from None

    # Try to parse YAML
    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as e:
        output_error(
            f"YAML syntax error: {e}",
            hints=["Check for indentation issues or invalid YAML characters."],
            json_output=json_output,
        )
        raise typer.Exit(2) from None

    # Ensure parsed YAML is a mapping (dict), not a scalar or list
    if not isinstance(parsed, dict):
        got_type = type(parsed).__name__ if parsed is not None else "empty file"
        output_error(
            f"Score must be a YAML mapping (key-value pairs), got: {got_type}",
            hints=[
                "A Mozart score needs key-value fields like: name: my-score",
                "Check that your file isn't plain text, a list, or empty.",
                "See: docs/score-writing-guide.md",
            ],
            json_output=json_output,
        )
        raise typer.Exit(2) from None

    # Try Pydantic validation
    try:
        config = JobConfig.from_yaml(config_file)
    except Exception as e:
        hints = _schema_error_hints(str(e))
        output_error(
            f"Schema validation failed: {e}",
            hints=hints,
            json_output=json_output,
        )
        raise typer.Exit(2) from None

    # Show basic info first
    if not json_output:
        console.print(f"\nValidating [cyan]{config.name}[/cyan]...")
        console.print()
        console.print("[green]✓[/green] YAML syntax valid")
        console.print("[green]✓[/green] Schema validation passed (Pydantic)")
        console.print()
        console.print("Running extended validation checks...")

    # Run extended validation checks
    runner = ValidationRunner(create_default_checks())
    issues = runner.validate(config, config_file, raw_yaml)

    # Output results
    reporter = ValidationReporter(console)

    if json_output:
        import json as json_mod

        from mozart.validation.rendering import generate_preview

        # Build combined JSON with validation issues and rendering preview
        validation_data = json_mod.loads(reporter.report_json(issues))
        preview = generate_preview(config, config_file)
        validation_data["rendering"] = reporter.report_rendering_json(preview)
        console.print(
            json_mod.dumps(validation_data, indent=2),
            soft_wrap=True,
            highlight=False,
        )
    else:
        reporter.report_terminal(issues, config.name)

        # Show config summary if no errors
        if not runner.has_errors(issues):
            console.print()
            console.print("[dim]Configuration summary:[/dim]")
            console.print(f"  Sheets: {config.sheet.total_sheets}")
            instrument_display = config.instrument or config.backend.type
            console.print(f"  Instrument: {instrument_display}")
            console.print(f"  Validations: {len(config.validations)}")
            console.print(f"  Notifications: {len(config.notifications)}")

            # Show DAG visualization if dependencies configured (v17 evolution)
            if config.sheet.dependencies:
                _show_dag_visualization(config, verbose)

            # Show rendering preview when validation passes
            from mozart.validation.rendering import generate_preview

            preview = generate_preview(
                config,
                config_file,
                max_sheets=1 if not verbose else None,
            )
            reporter.report_rendering_terminal(preview, verbose=verbose)

            # Report any rendering errors
            for err in preview.render_errors:
                console.print(f"  [red]Rendering error:[/red] {err}")
            for sheet in preview.sheets:
                if sheet.render_error:
                    console.print(
                        f"  [red]Sheet {sheet.sheet_num} render error:[/red]"
                        f" {sheet.render_error}"
                    )

    # Exit with appropriate code
    exit_code = runner.get_exit_code(issues)
    if exit_code != 0:
        raise typer.Exit(exit_code) from None


def _show_dag_visualization(config: JobConfig, verbose: bool) -> None:  # noqa: ARG001
    """Show DAG visualization for sheet dependencies.

    Displays the parallel execution structure based on configured dependencies.
    This helps users understand:
    - Which sheets can run in parallel
    - What the critical path is
    - Whether dependencies are configured correctly

    Args:
        config: Job configuration with dependencies.
        verbose: Reserved for future detailed parallel group analysis.
    """
    console.print()
    console.print("[bold]Execution DAG:[/bold]")

    # Build dependency graph
    deps = config.sheet.dependencies or {}
    total = config.sheet.total_sheets

    # Calculate levels (BFS from roots)
    levels: dict[int, int] = {}
    in_degree: dict[int, int] = dict.fromkeys(range(1, total + 1), 0)

    # Count incoming edges
    for sheet_num, sheet_deps in deps.items():
        for dep in sheet_deps:
            if 1 <= dep <= total:
                in_degree[int(sheet_num)] = in_degree.get(int(sheet_num), 0) + 1

    # Find roots (sheets with no dependencies)
    current_level = [s for s in range(1, total + 1) if in_degree[s] == 0]
    level_num = 0

    while current_level:
        for sheet in current_level:
            levels[sheet] = level_num

        # Find next level
        next_level: list[int] = []
        for sheet in current_level:
            # Find sheets that depend on this one
            for s, s_deps in deps.items():
                s_int = int(s)
                if sheet in s_deps:
                    in_degree[s_int] -= 1
                    if in_degree[s_int] == 0 and s_int not in levels:
                        next_level.append(s_int)

        current_level = sorted(next_level)
        level_num += 1

    # Group by level
    parallel_groups: dict[int, list[int]] = {}
    for sheet, level in levels.items():
        if level not in parallel_groups:
            parallel_groups[level] = []
        parallel_groups[level].append(sheet)

    # Display
    for level in sorted(parallel_groups.keys()):
        sheets = sorted(parallel_groups[level])
        sheet_str = ", ".join(str(s) for s in sheets)
        if len(sheets) > 1:
            console.print(f"  Level {level}: [green]{sheet_str}[/green] (parallel)")
        else:
            console.print(f"  Level {level}: [cyan]{sheet_str}[/cyan]")

    # Summary
    if parallel_groups:
        max_parallel = max(len(g) for g in parallel_groups.values())
        console.print(f"  [cyan]Max concurrency:[/cyan] {max_parallel} sheets")
    else:
        console.print()
        console.print("  [dim]Sequential execution (no parallelization)[/dim]")


def _schema_error_hints(error_msg: str) -> list[str]:
    """Return context-specific hints based on the Pydantic validation error.

    Parses the error message to provide actionable guidance instead of
    generic "check your score" messages.
    """
    msg_lower = error_msg.lower()

    if "promptconfig" in msg_lower and "prompt" in msg_lower:
        return [
            "The 'prompt' field must be a mapping, not a string.",
            "Use:  prompt:  /  template: \"your prompt text here\"",
            "See: docs/score-writing-guide.md",
        ]

    if "field required" in msg_lower:
        # Extract which fields are missing from the error
        hints = ["Ensure your score has at minimum: name, sheet, and prompt sections."]
        if "sheet" in msg_lower:
            hints.append("Add a 'sheet' section with total_sheets, total_items, and size.")
        if "prompt" in msg_lower:
            hints.append("Add a 'prompt' section with a 'template' or 'template_file'.")
        hints.append("See: docs/score-writing-guide.md")
        return hints

    # Fallback: generic but still helpful
    return [
        "Ensure your score has at minimum: name, sheet, and prompt sections.",
        "See: docs/score-writing-guide.md",
    ]


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "validate",
]
