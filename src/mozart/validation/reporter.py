"""Validation output formatting and reporting.

Provides formatted output for validation results, supporting both
terminal display with Rich and JSON output for tooling.
"""

import json
from typing import TYPE_CHECKING, Any, TypedDict

from rich.console import Console
from rich.panel import Panel

from mozart.validation.base import ValidationIssue, ValidationSeverity

if TYPE_CHECKING:
    from mozart.validation.rendering import RenderingPreview


class ValidationIssueDict(TypedDict, total=False):
    """Typed schema for serialized ValidationIssue entries in JSON output.

    Required keys: check_id, severity, message (always present).
    Optional keys are only included when the source ValidationIssue has them set.
    """

    check_id: str
    severity: str
    message: str
    line: int
    column: int
    context: str
    suggestion: str
    auto_fixable: bool
    metadata: dict[str, str]


class ValidationReporter:
    """Formats and outputs validation results.

    Supports multiple output formats:
    - Terminal output with colors (default)
    - JSON for machine parsing
    - Plain text for logs
    """

    SEVERITY_COLORS = {
        ValidationSeverity.ERROR: "red",
        ValidationSeverity.WARNING: "yellow",
        ValidationSeverity.INFO: "blue",
    }

    SEVERITY_ICONS = {
        ValidationSeverity.ERROR: "✗",
        ValidationSeverity.WARNING: "!",
        ValidationSeverity.INFO: "i",
    }

    def __init__(self, console: Console | None = None):
        """Initialize reporter.

        Args:
            console: Rich Console for output. Creates one if not provided.
        """
        self.console = console or Console()

    def report_terminal(
        self,
        issues: list[ValidationIssue],
        config_name: str,
        show_passed: bool = True,
    ) -> None:
        """Output validation results to terminal with formatting.

        Args:
            issues: List of validation issues
            config_name: Name of the config being validated
            show_passed: Whether to show passed checks summary
        """
        self.console.print()

        # Count by severity
        errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
        warnings = [i for i in issues if i.severity == ValidationSeverity.WARNING]
        infos = [i for i in issues if i.severity == ValidationSeverity.INFO]

        # Print header based on results
        if not issues:
            self.console.print(
                Panel(
                    f"[green]✓ Configuration valid:[/green] {config_name}",
                    border_style="green",
                )
            )
            return

        # Print issues by severity
        if errors:
            self._print_section("ERRORS (must fix before running)", errors, "red")

        if warnings:
            self._print_section("WARNINGS (may cause issues)", warnings, "yellow")

        if infos:
            self._print_section("INFO (consider reviewing)", infos, "blue")

        # Print summary
        self.console.print()
        summary_parts = []
        if errors:
            err_s = "s" if len(errors) > 1 else ""
            summary_parts.append(
                f"[red]{len(errors)} error{err_s} (must fix)[/red]"
            )
        if warnings:
            warn_s = "s" if len(warnings) > 1 else ""
            summary_parts.append(
                f"[yellow]{len(warnings)} warning{warn_s}"
                f" (should fix)[/yellow]"
            )
        if infos:
            info_s = "s" if len(infos) > 1 else ""
            summary_parts.append(
                f"[blue]{len(infos)} info note{info_s}[/blue]"
            )

        self.console.print(f"Summary: {', '.join(summary_parts)}")

        # Final status
        if errors:
            self.console.print("\n[bold red]Validation: FAILED[/bold red]")
        else:
            self.console.print("\n[bold green]Validation: PASSED[/bold green] (with warnings)")

    def _print_section(
        self,
        title: str,
        issues: list[ValidationIssue],
        color: str,
    ) -> None:
        """Print a section of issues."""
        self.console.print(f"\n[{color} bold]{title}:[/{color} bold]")

        for issue in issues:
            icon = self.SEVERITY_ICONS.get(issue.severity, "?")
            self.console.print(f"  [{color}]{icon}[/{color}] {issue.format_short()}")

            if issue.context:
                # Show context with indicator
                context_display = issue.context
                if len(context_display) > 70:
                    context_display = context_display[:67] + "..."
                self.console.print(f"         [dim]{context_display}[/dim]")

            if issue.suggestion:
                self.console.print(f"         [cyan]Suggestion:[/cyan] {issue.suggestion}")

    def report_json(self, issues: list[ValidationIssue]) -> str:
        """Output validation results as JSON.

        Args:
            issues: List of validation issues

        Returns:
            JSON string representation
        """
        result: dict[str, Any] = {
            "valid": not any(i.severity == ValidationSeverity.ERROR for i in issues),
            "error_count": sum(1 for i in issues if i.severity == ValidationSeverity.ERROR),
            "warning_count": sum(1 for i in issues if i.severity == ValidationSeverity.WARNING),
            "info_count": sum(1 for i in issues if i.severity == ValidationSeverity.INFO),
            "issues": [self._issue_to_dict(i) for i in issues],
        }
        return json.dumps(result, indent=2)

    def _issue_to_dict(self, issue: ValidationIssue) -> ValidationIssueDict:
        """Convert a ValidationIssue to a typed dictionary."""
        result = ValidationIssueDict(
            check_id=issue.check_id,
            severity=issue.severity.value,
            message=issue.message,
        )

        if issue.line is not None:
            result["line"] = issue.line
        if issue.column is not None:
            result["column"] = issue.column
        if issue.context:
            result["context"] = issue.context
        if issue.suggestion:
            result["suggestion"] = issue.suggestion
        if issue.auto_fixable:
            result["auto_fixable"] = issue.auto_fixable
        if issue.metadata:
            result["metadata"] = issue.metadata

        return result

    def report_rendering_terminal(
        self,
        preview: "RenderingPreview",
        verbose: bool = False,
    ) -> None:
        """Output a rendering preview to the terminal.

        Args:
            preview: The rendering preview to display.
            verbose: If False, show sheet 1 only. If True, show all sheets.
        """
        sheets_to_show = preview.sheets if verbose else preview.sheets[:1]

        for sp in sheets_to_show:
            header = f"Rendering Preview (Sheet {sp.sheet_num} of {preview.total_sheets})"
            if verbose and (sp.stage is not None or sp.instance is not None):
                parts: list[str] = []
                if sp.stage is not None:
                    parts.append(f"stage={sp.stage}")
                if sp.instance is not None:
                    parts.append(f"instance={sp.instance}")
                header += f" \\[{', '.join(parts)}]"

            self.console.print(f"\n[bold]{header}:[/bold]")

            # Prompt snippet in a Rich Panel
            if sp.render_error:
                self.console.print(
                    Panel(
                        f"[red]{sp.render_error}[/red]",
                        title="Prompt",
                        border_style="dim",
                        expand=False,
                    )
                )
            else:
                self.console.print("  Prompt:")
                self.console.print(
                    Panel(
                        sp.prompt_snippet,
                        border_style="dim",
                        expand=False,
                    )
                )

            # Validations
            if sp.expanded_validations:
                self.console.print(
                    f"  Validations (expanded for sheet {sp.sheet_num}):"
                )
                for ev in sp.expanded_validations:
                    num = ev.index + 1
                    path_display = ev.expanded_path or ""
                    if not ev.applicable:
                        self.console.print(
                            f"    [dim]{num}. {ev.type}: {path_display}"
                            f" (not applicable: {ev.condition})[/dim]"
                        )
                    else:
                        self.console.print(
                            f"    {num}. {ev.type}: {path_display}"
                        )

        # Render errors summary
        if preview.render_errors:
            self.console.print("\n[red bold]Render Errors:[/red bold]")
            for err in preview.render_errors:
                self.console.print(f"  [red]{err}[/red]")

    def report_rendering_json(self, preview: "RenderingPreview") -> dict[str, Any]:
        """Return a rendering preview as a JSON-serializable dict.

        Args:
            preview: The rendering preview to serialize.

        Returns:
            Dict with total_sheets, has_fan_out, has_dependencies,
            render_errors, and a sheets array.
        """
        sheets: list[dict[str, Any]] = []
        for sp in preview.sheets:
            validations: list[dict[str, Any]] = []
            for ev in sp.expanded_validations:
                v: dict[str, Any] = {
                    "index": ev.index,
                    "type": ev.type,
                    "applicable": ev.applicable,
                }
                if ev.description:
                    v["description"] = ev.description
                if ev.expanded_path:
                    v["expanded_path"] = ev.expanded_path
                if ev.pattern:
                    v["pattern"] = ev.pattern
                if ev.condition:
                    v["condition"] = ev.condition
                validations.append(v)

            sheet_dict: dict[str, Any] = {
                "sheet_num": sp.sheet_num,
                "item_range": list(sp.item_range),
                "stage": sp.stage,
                "instance": sp.instance,
                "fan_count": sp.fan_count,
                "prompt_snippet": sp.prompt_snippet,
                "render_error": sp.render_error,
                "validations": validations,
            }
            sheets.append(sheet_dict)

        return {
            "total_sheets": preview.total_sheets,
            "has_fan_out": preview.has_fan_out,
            "has_dependencies": preview.has_dependencies,
            "render_errors": preview.render_errors,
            "sheets": sheets,
        }

    def format_plain(self, issues: list[ValidationIssue]) -> str:
        """Format issues as plain text for logs.

        Args:
            issues: List of validation issues

        Returns:
            Plain text representation
        """
        if not issues:
            return "Validation passed: no issues found"

        lines = []
        for issue in issues:
            severity = issue.severity.value.upper()
            line_info = f" (line {issue.line})" if issue.line else ""
            lines.append(f"[{severity}] {issue.check_id}{line_info}: {issue.message}")

            if issue.suggestion:
                lines.append(f"    Suggestion: {issue.suggestion}")

        # Summary
        errors = sum(1 for i in issues if i.severity == ValidationSeverity.ERROR)
        warnings = sum(1 for i in issues if i.severity == ValidationSeverity.WARNING)
        infos = sum(1 for i in issues if i.severity == ValidationSeverity.INFO)

        lines.append("")
        lines.append(f"Total: {errors} errors, {warnings} warnings, {infos} info")
        lines.append(f"Validation: {'FAILED' if errors else 'PASSED'}")

        return "\n".join(lines)
