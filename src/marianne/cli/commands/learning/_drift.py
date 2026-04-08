"""Drift detection commands for learning patterns.

Commands:
- learning-drift: Detect patterns with effectiveness drift
- learning-epistemic-drift: Detect patterns with epistemic drift
"""

from __future__ import annotations

import json as json_lib

import typer
from rich.table import Table

from ...output import console


def learning_drift(
    threshold: float = typer.Option(
        0.2,
        "--threshold",
        "-t",
        help="Drift threshold (0.0-1.0) to flag patterns",
    ),
    window: int = typer.Option(
        5,
        "--window",
        "-w",
        help="Window size for drift comparison",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum number of patterns to show",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output as JSON for machine parsing",
    ),
    summary: bool = typer.Option(
        False,
        "--summary",
        "-s",
        help="Show only summary statistics",
    ),
) -> None:
    """Detect patterns with effectiveness drift.

    Drift is calculated by comparing the pattern's effectiveness in its
    last N applications vs the previous N applications.

    Examples:
        mzt learning-drift                # Show drifting patterns
        mzt learning-drift -t 0.15        # Lower threshold (more sensitive)
        mzt learning-drift -w 10          # Larger comparison window
        mzt learning-drift --summary      # Just show summary stats
        mzt learning-drift --json         # JSON output for scripting
    """
    from marianne.learning.global_store import get_global_store

    store = get_global_store()

    if summary:
        drift_summary = store.get_pattern_drift_summary()

        if json_output:
            console.print(json_lib.dumps(drift_summary, indent=2))
            return

        console.print("[bold]Pattern Drift Summary[/bold]\n")
        console.print(f"  Total patterns: {drift_summary['total_patterns']}")
        console.print(f"  Patterns analyzed: {drift_summary['patterns_analyzed']}")
        drifting = drift_summary["patterns_drifting"]
        color = "red" if drifting > 0 else "green"
        console.print(f"  Patterns drifting: [{color}]{drifting}[/{color}]")
        console.print(
            f"  Avg drift magnitude: {drift_summary['avg_drift_magnitude']:.3f}"
        )
        if drift_summary["most_drifted"]:
            console.print(
                f"  Most drifted pattern: {drift_summary['most_drifted']}"
            )
        return

    drifting_patterns = store.get_drifting_patterns(
        drift_threshold=threshold,
        window_size=window,
        limit=limit,
    )

    if json_output:
        output = {
            "threshold": threshold,
            "window_size": window,
            "patterns": [
                {
                    "pattern_id": m.pattern_id,
                    "pattern_name": m.pattern_name,
                    "effectiveness_before": round(m.effectiveness_before, 3),
                    "effectiveness_after": round(m.effectiveness_after, 3),
                    "drift_magnitude": round(m.drift_magnitude, 3),
                    "drift_direction": m.drift_direction,
                    "grounding_confidence_avg": round(m.grounding_confidence_avg, 3),
                    "applications_analyzed": m.applications_analyzed,
                }
                for m in drifting_patterns
            ],
        }
        console.print(json_lib.dumps(output, indent=2))
        return

    console.print(f"[bold]Patterns with Drift > {threshold:.0%}[/bold]")
    console.print(f"[dim]Window size: {window} applications per period[/dim]\n")

    if not drifting_patterns:
        console.print("[green]✓ No patterns exceeding drift threshold[/green]")
        console.print("[dim]All patterns are stable or improving[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Pattern", style="cyan")
    table.add_column("Before", justify="right")
    table.add_column("After", justify="right")
    table.add_column("Drift", justify="right")
    table.add_column("Direction", justify="center")
    table.add_column("Grounding", justify="right")

    for m in drifting_patterns:
        if m.drift_direction == "negative":
            dir_style = "[red]↓ declining[/red]"
        elif m.drift_direction == "positive":
            dir_style = "[green]↑ improving[/green]"
        else:
            dir_style = "[dim]→ stable[/dim]"

        drift_color = "red" if m.drift_magnitude > 0.3 else "yellow"

        table.add_row(
            m.pattern_name[:30],
            f"{m.effectiveness_before:.1%}",
            f"{m.effectiveness_after:.1%}",
            f"[{drift_color}]{m.drift_magnitude:.1%}[/{drift_color}]",
            dir_style,
            f"{m.grounding_confidence_avg:.1%}",
        )

    console.print(table)

    declining = [m for m in drifting_patterns if m.drift_direction == "negative"]
    if declining:
        console.print(
            f"\n[yellow]⚠ {len(declining)} pattern(s) showing declining effectiveness[/yellow]"
        )
        console.print("[dim]Consider reviewing these patterns for deprecation[/dim]")


def learning_epistemic_drift(
    threshold: float = typer.Option(
        0.15,
        "--threshold",
        "-t",
        help="Epistemic drift threshold (0.0-1.0) to flag patterns",
    ),
    window: int = typer.Option(
        5,
        "--window",
        "-w",
        help="Window size for drift comparison",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum number of patterns to show",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output as JSON for machine parsing",
    ),
    summary: bool = typer.Option(
        False,
        "--summary",
        "-s",
        help="Show only summary statistics",
    ),
) -> None:
    """Detect patterns with epistemic drift (belief/confidence changes).

    Epistemic drift tracks confidence changes over time, complementing
    effectiveness drift as a leading indicator of pattern health.

    Examples:
        mzt learning-epistemic-drift            # Show patterns with belief drift
        mzt learning-epistemic-drift -t 0.1    # Lower threshold (more sensitive)
        mzt learning-epistemic-drift --summary # Just show summary stats
        mzt learning-epistemic-drift --json    # JSON output for scripting
    """
    from marianne.learning.global_store import get_global_store

    store = get_global_store()

    if summary:
        drift_summary = store.get_epistemic_drift_summary()

        if json_output:
            console.print(json_lib.dumps(drift_summary, indent=2))
            return

        console.print("[bold]Epistemic Drift Summary[/bold]\n")
        console.print(f"  Total patterns: {drift_summary['total_patterns']}")
        console.print(f"  Patterns analyzed: {drift_summary['patterns_analyzed']}")
        drifting = drift_summary["patterns_with_epistemic_drift"]
        color = "red" if drifting > 0 else "green"
        console.print(f"  Patterns with drift: [{color}]{drifting}[/{color}]")
        console.print(
            f"  Avg belief change: {drift_summary['avg_belief_change']:.3f}"
        )
        console.print(
            f"  Avg belief entropy: {drift_summary['avg_belief_entropy']:.3f}"
        )
        if drift_summary["most_unstable"]:
            console.print(
                f"  Most unstable pattern: {drift_summary['most_unstable']}"
            )
        return

    drifting_patterns = store.get_epistemic_drifting_patterns(
        drift_threshold=threshold,
        window_size=window,
        limit=limit,
    )

    if json_output:
        output = {
            "threshold": threshold,
            "window_size": window,
            "patterns": [
                {
                    "pattern_id": m.pattern_id,
                    "pattern_name": m.pattern_name,
                    "confidence_before": round(m.confidence_before, 3),
                    "confidence_after": round(m.confidence_after, 3),
                    "belief_change": round(m.belief_change, 3),
                    "belief_entropy": round(m.belief_entropy, 3),
                    "drift_direction": m.drift_direction,
                    "applications_analyzed": m.applications_analyzed,
                }
                for m in drifting_patterns
            ],
        }
        console.print(json_lib.dumps(output, indent=2))
        return

    console.print(f"[bold]Patterns with Epistemic Drift > {threshold:.0%}[/bold]")
    console.print(f"[dim]Window size: {window} applications per period[/dim]\n")

    if not drifting_patterns:
        console.print("[green]✓ No patterns exceeding epistemic drift threshold[/green]")
        console.print("[dim]All pattern beliefs are stable[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Pattern", style="cyan")
    table.add_column("Conf Before", justify="right")
    table.add_column("Conf After", justify="right")
    table.add_column("Change", justify="right")
    table.add_column("Direction", justify="center")
    table.add_column("Entropy", justify="right")

    for m in drifting_patterns:
        if m.drift_direction == "weakening":
            dir_style = "[red]↓ weakening[/red]"
        elif m.drift_direction == "strengthening":
            dir_style = "[green]↑ strengthening[/green]"
        else:
            dir_style = "[dim]→ stable[/dim]"

        change_color = "red" if abs(m.belief_change) > 0.2 else "yellow"
        entropy_color = "red" if m.belief_entropy > 0.3 else "dim"

        table.add_row(
            m.pattern_name[:30],
            f"{m.confidence_before:.1%}",
            f"{m.confidence_after:.1%}",
            f"[{change_color}]{m.belief_change:+.1%}[/{change_color}]",
            dir_style,
            f"[{entropy_color}]{m.belief_entropy:.2f}[/{entropy_color}]",
        )

    console.print(table)

    weakening = [m for m in drifting_patterns if m.drift_direction == "weakening"]
    if weakening:
        console.print(
            f"\n[yellow]⚠ {len(weakening)} pattern(s) showing weakening confidence[/yellow]"
        )
        console.print(
            "[dim]These patterns may need investigation "
            "before effectiveness declines[/dim]"
        )

    high_entropy = [m for m in drifting_patterns if m.belief_entropy > 0.3]
    if high_entropy:
        console.print(
            f"\n[yellow]⚠ {len(high_entropy)} pattern(s) with high belief entropy[/yellow]"
        )
        console.print("[dim]Inconsistent confidence suggests unstable pattern application[/dim]")
