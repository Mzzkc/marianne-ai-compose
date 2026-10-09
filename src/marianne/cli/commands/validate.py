"""Validate command for Marianne CLI.

This module implements the `mzt validate` command for comprehensive
configuration validation before score execution.

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
from typing import Any

import typer
import yaml
from pydantic import ValidationError

from marianne.core.config import JobConfig
from marianne.core.config.flow import FlowConfigError
from marianne.validation import (
    ValidationReporter,
    ValidationRunner,
    create_default_checks,
)
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.output_contract import apply_suppression, structural_summary

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
    strict: bool = typer.Option(False, "--strict", help="Treat unsuppressed warnings as failures"),
    errors_only: bool = typer.Option(False, "--errors-only", help="Show only error findings"),
) -> None:
    """Validate a score configuration file.

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
                "A Marianne score needs key-value fields like: name: my-score",
                "Check that your file isn't plain text, a list, or empty.",
                "See: docs/score-writing-guide.md",
            ],
            json_output=json_output,
        )
        raise typer.Exit(2) from None

    # Detect fleet configs — they aren't scores, so skip score validation
    if parsed.get("type") == "fleet":
        if json_output:
            import json as json_mod

            console.print(
                json_mod.dumps(
                    {
                        "type": "fleet",
                        "valid": True,
                        "skipped": True,
                        "message": ("Fleet config — not subject to score validation."),
                        "file": str(config_file),
                    },
                    indent=2,
                ),
                soft_wrap=True,
                highlight=False,
            )
        else:
            console.print(
                f"\n[cyan]{config_file.name}[/cyan] is a fleet config, not a score."
                " Fleet configs are not subject to score validation.",
            )
        raise typer.Exit(0) from None

    if not any(key in parsed for key in ("name", "sheet", "prompt")):
        output_error(
            "This YAML is not a Marianne score (expected name, sheet, prompt).",
            json_output=json_output,
        )
        raise typer.Exit(2) from None

    # Try Pydantic validation
    try:
        config = JobConfig.from_yaml(config_file)
    except ValidationError as e:
        load_issues = _structured_load_issues(e, raw_yaml)
        if load_issues is not None:
            reporter = ValidationReporter(console)
            if json_output:
                import json as json_mod

                data = json_mod.loads(reporter.report_json(load_issues))
                data["rendering"] = {"total_sheets": None, "sheets": [], "render_errors": []}
                data["summary"] = _raw_score_summary(parsed)
                data["suppressed"] = []
                if errors_only:
                    data["issues"] = [i for i in data["issues"] if i["severity"] == "error"]
                console.print(json_mod.dumps(data, indent=2), soft_wrap=True, highlight=False)
            else:
                summary = _raw_score_summary(parsed)
                console.print(f"Score: {summary['score']}")
                console.print(f"Loops: {', '.join(summary['loops']) or 'none'}")
                reporter.report_terminal(
                    load_issues, str(summary["score"]), verbose=verbose, errors_only=errors_only
                )
            raise typer.Exit(1) from None
        hints = _schema_error_hints(str(e))
        output_error(f"Schema validation failed: {e}", hints=hints, json_output=json_output)
        raise typer.Exit(2) from None
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
    issues, suppressed = apply_suppression(config, issues)
    summary = structural_summary(config)

    # Output results
    reporter = ValidationReporter(console)

    if json_output:
        import json as json_mod

        from marianne.validation.rendering import generate_preview

        # Build combined JSON with validation issues and rendering preview
        validation_data = json_mod.loads(reporter.report_json(issues))
        validation_data["summary"] = summary
        validation_data["suppressed"] = suppressed
        if errors_only:
            validation_data["issues"] = [
                issue for issue in validation_data["issues"] if issue["severity"] == "error"
            ]
        preview = generate_preview(config, config_file)
        validation_data["rendering"] = reporter.report_rendering_json(preview)
        console.print(
            json_mod.dumps(validation_data, indent=2),
            soft_wrap=True,
            highlight=False,
        )
    else:
        console.print(f"\nScore: {summary['score']}")
        console.print(f"Sheets: {summary['sheets']} ({summary['stages']} stages)")
        console.print(f"Instruments: {summary['instruments']['primary']}")
        console.print(f"Variables: {summary['variables']['declared']} declared")
        if summary["loops"]:
            console.print(f"Loops: {', '.join(summary['loops'])}")
        if summary["triggers"]:
            console.print(f"Triggers: {', '.join(summary['triggers'])}")
        if suppressed:
            console.print(f"Suppressed: {sum(int(row['count']) for row in suppressed)}")
        reporter.report_terminal(issues, config.name, verbose=verbose, errors_only=errors_only)

        # Show config summary if no errors
        if not runner.has_errors(issues):
            console.print()
            console.print("[dim]Configuration summary:[/dim]")
            console.print(f"  Sheets: {config.sheet.total_sheets}")
            instrument_display = config.effective_instrument_name
            console.print(f"  Instrument: {instrument_display}")
            console.print(f"  Validations: {len(config.validations)}")
            console.print(f"  Notifications: {len(config.notifications)}")

            # Show DAG visualization if dependencies configured (v17 evolution)
            if config.sheet.dependencies:
                _show_dag_visualization(config, verbose)

            # Show rendering preview when validation passes
            from marianne.validation.rendering import generate_preview

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
                        f"  [red]Sheet {sheet.sheet_num} render error:[/red] {sheet.render_error}"
                    )

    # Exit with appropriate code
    exit_code = runner.get_exit_code(issues, strict=strict)
    if exit_code != 0:
        raise typer.Exit(exit_code) from None


_FLOW_CODES = {
    "V-FLOW-01": "V320",
    "V-FLOW-02": "V320",
    "V-FLOW-03": "V220",
    "V-FLOW-04": "V221",
    "V-FLOW-05": "V222",
    "V-FLOW-06": "V312",
    "V-FLOW-07": "V223",
    "V-FLOW-08": "V314",
    "V-FLOW-09": "V313",
    "V-FLOW-10": "V313",
    "V-FLOW-11": "V312",
    "V-FLOW-12": "V224",
    "V-FLOW-13": "V315",
    "V-FLOW-14": "V225",
    "V-FLOW-15": "V226",
    "V-FLOW-16": "V317",
    "V-FLOW-17": "V227",
    "V-FLOW-18": "V208",
    "V-FLOW-19": "V228",
    "V-FLOW-20": "V229",
    "V-FLOW-21": "V232",
    "V-FLOW-22": "V316",
    "V-FLOW-23": "V230",
    "V-FLOW-24": "V231",
}


def _structured_load_issues(
    error: ValidationError,
    raw_yaml: str,
) -> list[ValidationIssue] | None:
    """Render only recognised semantic load errors as exit-1 findings."""
    issues: list[ValidationIssue] = []
    for detail in error.errors():
        cause = detail.get("ctx", {}).get("error")
        if isinstance(cause, FlowConfigError):
            for item in cause.issues:
                code = _FLOW_CODES.get(item.check)
                if code is None:
                    return None
                line = next(
                    (
                        n
                        for n, text in enumerate(raw_yaml.splitlines(), 1)
                        if item.span and f"{item.span}:" in text
                    ),
                    None,
                )
                issues.append(
                    ValidationIssue(
                        check_id=code,
                        severity=ValidationSeverity.ERROR,
                        message=item.message,
                        line=line,
                        suggestion=item.hint,
                        metadata={"flow_check": item.check, "span": item.span or ""},
                    )
                )
        elif isinstance(cause, ValueError) and "out of range (valid:" in str(cause):
            issues.append(
                ValidationIssue(
                    check_id="V214",
                    severity=ValidationSeverity.ERROR,
                    message=str(cause),
                )
            )
        else:
            return None
    return issues or None


def _raw_score_summary(parsed: dict[str, Any]) -> dict[str, Any]:
    """Partial summary when a semantic flow error prevents model construction."""
    sheet_raw, prompt_raw = parsed.get("sheet"), parsed.get("prompt")
    sheet: dict[str, Any] = sheet_raw if isinstance(sheet_raw, dict) else {}
    prompt: dict[str, Any] = prompt_raw if isinstance(prompt_raw, dict) else {}
    loops_raw, triggers_raw, variables_raw = (
        sheet.get("loops"),
        sheet.get("triggers"),
        prompt.get("variables"),
    )
    loops: dict[Any, Any] = loops_raw if isinstance(loops_raw, dict) else {}
    triggers: dict[Any, Any] = triggers_raw if isinstance(triggers_raw, dict) else {}
    variables: dict[Any, Any] = variables_raw if isinstance(variables_raw, dict) else {}
    return {
        "score": parsed.get("name"),
        "sheets": None,
        "stages": None,
        "fan_out": bool(sheet.get("fan_out")),
        "instruments": {
            "primary": parsed.get("instrument"),
            "fallbacks": parsed.get("instrument_fallbacks", []),
        },
        "loops": list(map(str, loops)),
        "triggers": list(map(str, triggers)),
        "variables": {"declared": len(variables)},
    }


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

    F-523: Provides YAML structure examples for common plural/singular mistakes.
    Handles multiple error types in one message (e.g., extra field + missing field).
    """
    import re

    msg_lower = error_msg.lower()
    hints: list[str] = []

    # Check for extra_forbidden (unknown field) errors
    has_extra_forbidden = "extra inputs are not permitted" in msg_lower
    has_field_required = "field required" in msg_lower
    has_prompt_config_error = "promptconfig" in msg_lower and "prompt" in msg_lower
    has_movements_dict_error = "movements" in msg_lower and (
        "should be a valid dictionary" in msg_lower
        or "input should be a valid dictionary" in msg_lower
    )

    # Handle PromptConfig type errors
    if has_prompt_config_error:
        return [
            "The 'prompt' field must be a mapping, not a string.",
            'Use:  prompt:  /  template: "your prompt text here"',
            "See: docs/score-writing-guide.md",
        ]

    # Handle movements structure errors (expecting dict, not list)
    if has_movements_dict_error:
        return [
            "The 'movements' field expects a dictionary with movement numbers as keys.",
            "Use:  movements:  /  1:  /    voices: 3",
            "Or:   movements:  /  1:  /    description: 'Setup'",
            "See: docs/score-writing-guide.md",
        ]

    # Handle extra_forbidden errors (unknown fields)
    if has_extra_forbidden:
        unknown_hints = _unknown_field_hints(error_msg)
        # Remove the "See: docs/..." hint temporarily - we'll add it at the end
        unknown_hints = [h for h in unknown_hints if not h.startswith("See:")]
        hints.extend(unknown_hints)

    # Handle missing required fields
    if has_field_required:
        # Extract field names from "field_name\n  Field required" pattern
        missing_fields = re.findall(
            r"^(\w[\w.]*)\n\s+Field required",
            error_msg,
            re.MULTILINE,
        )

        if not hints:  # Only add baseline if no other hints yet
            hints.append("Ensure your score has at minimum: name, sheet, and prompt sections.")

        for field in missing_fields:
            if field == "sheet":
                hints.append(f"Required field '{field}' is missing. Add a 'sheet' section:")
                hints.append("  sheet:")
                hints.append("    size: 10")
                hints.append("    total_items: 100")
            elif field == "prompt":
                hints.append(f"Required field '{field}' is missing. Add a 'prompt' section:")
                hints.append("  prompt:")
                hints.append("    template: 'Your prompt text here'")

    # If we have any hints, add the docs reference at the end
    if hints:
        hints.append("See: docs/score-writing-guide.md")
        return hints

    # Fallback: generic but still helpful
    return [
        "Ensure your score has at minimum: name, sheet, and prompt sections.",
        "See: docs/score-writing-guide.md",
    ]


# Known field names at the top level that users might confuse
# F-523: Common onboarding mistakes from plural/singular confusion
_KNOWN_TYPOS: dict[str, str] = {
    "retries": "retry",
    "paralel": "parallel",
    "parralel": "parallel",
    "parrallel": "parallel",
    "backend_type": "instrument (or backend.type)",
    "max_retries": "retry.max_retries",
    "timeout": "stale_detection.idle_timeout_seconds",
    "preamble": "prompt.template (preamble is set automatically by Marianne)",
    "task": "prompt.template",
    "stager_delay_ms": "parallel.stagger_delay_ms",
    "stagger_delay": "parallel.stagger_delay_ms",
    "insturment": "instrument",
    "instrumnet": "instrument",
    "insturment_config": "instrument_config",
    "instrumnet_config": "instrument_config",
    "validation": "validations",
    "notification": "notifications",
    "sheets": "sheet (singular — use: sheet: {size: N, total_items: M})",
    "prompts": "prompt (singular — use: prompt: {template: '...'})",
}


def _unknown_field_hints(error_msg: str) -> list[str]:
    """Extract unknown field names from extra_forbidden errors and provide
    targeted guidance.

    F-523: Provide YAML structure examples for common plural/singular mistakes.
    """
    import re

    # Extract field names from Pydantic error format:
    # "field_name\n  Extra inputs are not permitted"
    unknown_fields = re.findall(
        r"^(\w[\w.]*)\n\s+Extra inputs are not permitted",
        error_msg,
        re.MULTILINE,
    )

    if not unknown_fields:
        return [
            "Your score contains field(s) that Marianne doesn't recognize.",
            "Check for typos in field names.",
            "See: docs/score-writing-guide.md for valid fields.",
        ]

    hints: list[str] = []
    for field in unknown_fields:
        suggestion = _KNOWN_TYPOS.get(field)
        if suggestion:
            hints.append(f"Unknown field '{field}' — did you mean '{suggestion}'?")
        else:
            hints.append(f"Unknown field '{field}' — this is not a valid score field.")

        # F-523: Provide YAML examples for common structural mistakes
        if field == "sheets":
            hints.append("  Use 'sheet' (singular) with this structure:")
            hints.append("  sheet:")
            hints.append("    size: 10")
            hints.append("    total_items: 100")
        elif field == "prompts":
            hints.append("  Use 'prompt' (singular) with this structure:")
            hints.append("  prompt:")
            hints.append("    template: 'Your prompt text here'")

    hints.append("See: docs/score-writing-guide.md for the complete field reference.")
    return hints


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "validate",
]
