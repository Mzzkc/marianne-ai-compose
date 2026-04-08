"""Learning statistics, insights, and activity commands.

Commands:
- learning-stats: View global learning statistics
- learning-insights: Show actionable insights from learning data
- learning-activity: View recent learning activity and pattern applications
"""

from __future__ import annotations

import json as json_lib
from datetime import datetime, timedelta
from typing import Annotated, Any

import typer
from rich.table import Table

from ...output import console


def learning_stats(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output as JSON for machine parsing",
    ),
) -> None:
    """View global learning statistics.

    Shows summary statistics about the global learning store including
    execution counts, pattern counts, and effectiveness metrics.

    Examples:
        mzt learning-stats         # Human-readable summary
        mzt learning-stats --json  # JSON output for scripting
    """
    from marianne.learning.global_store import get_global_store
    from marianne.learning.migration import check_migration_status

    store = get_global_store()
    stats = store.get_execution_stats()
    migration = check_migration_status(store)

    if json_output:
        output = {
            "executions": {
                "total": stats.get("total_executions", 0),
                "success_without_retry_rate": round(
                    stats.get("success_without_retry_rate", 0) * 100, 1
                ),
            },
            "patterns": {
                "total": stats.get("total_patterns", 0),
                "avg_effectiveness": round(stats.get("avg_pattern_effectiveness", 0), 3),
            },
            "workspaces": {
                "unique": stats.get("unique_workspaces", 0),
            },
            "error_recoveries": {
                "total": stats.get("total_error_recoveries", 0),
                "success_rate": round(stats.get("error_recovery_success_rate", 0) * 100, 1),
            },
            "migration_needed": migration.get("needs_migration", False),
        }
        console.print(json_lib.dumps(output, indent=2))
        return

    console.print("[bold]Global Learning Statistics[/bold]\n")

    console.print("[bold cyan]Executions[/bold cyan]")
    console.print(f"  Total recorded: [green]{stats.get('total_executions', 0)}[/green]")
    success_rate = stats.get("success_without_retry_rate", 0) * 100
    color = 'green' if success_rate > 70 else 'yellow'
    console.print(f"  First-attempt success: [{color}]{success_rate:.1f}%[/]")

    console.print("\n[bold cyan]Patterns[/bold cyan]")
    console.print(f"  Total learned: [yellow]{stats.get('total_patterns', 0)}[/yellow]")
    avg_eff = stats.get("avg_pattern_effectiveness", 0)
    eff_color = 'green' if avg_eff > 0.6 else 'yellow'
    console.print(f"  Avg effectiveness: [{eff_color}]{avg_eff:.2f}[/]")

    all_patterns = store.get_patterns(limit=1000)
    output_patterns = sum(1 for p in all_patterns if p.pattern_type == "output_pattern")
    error_code_patterns = sum(1 for p in all_patterns if "error_code" in (p.pattern_name or ""))
    semantic_patterns = sum(1 for p in all_patterns if p.pattern_type == "semantic_failure")

    console.print("\n[bold cyan]Data Sources[/bold cyan]")
    console.print(f"  Output patterns extracted: [cyan]{output_patterns}[/cyan]")
    console.print(f"  Error code patterns: [cyan]{error_code_patterns}[/cyan]")
    console.print(f"  Semantic failure patterns: [cyan]{semantic_patterns}[/cyan]")

    console.print("\n[bold cyan]Workspaces[/bold cyan]")
    console.print(f"  Unique workspaces: [cyan]{stats.get('unique_workspaces', 0)}[/cyan]")

    console.print("\n[bold cyan]Error Recovery Learning[/bold cyan]")
    console.print(f"  Recoveries recorded: {stats.get('total_error_recoveries', 0)}")
    recovery_rate = stats.get("error_recovery_success_rate", 0) * 100
    rec_color = 'green' if recovery_rate > 70 else 'yellow'
    console.print(f"  Recovery success rate: [{rec_color}]{recovery_rate:.1f}%[/]")

    if migration.get("needs_migration"):
        console.print(
            "\n[yellow]⚠ Migration needed:[/yellow] Run 'marianne aggregate-patterns' "
            "to import workspace-local outcomes"
        )


def learning_insights(
    limit: Annotated[int, typer.Option(help="Max patterns to show")] = 10,
    pattern_type: Annotated[str | None, typer.Option(help="Filter by type")] = None,
) -> None:
    """Show actionable insights from learning data.

    Displays patterns extracted from execution history including:
    - Output patterns (from stdout/stderr analysis)
    - Error code patterns (aggregated error statistics)
    - Success predictors (factors that correlate with success)

    Examples:
        mzt learning-insights
        mzt learning-insights --pattern-type output_pattern
        mzt learning-insights --limit 20
    """
    from marianne.learning.global_store import GlobalLearningStore

    console.print("[bold]Learning Insights[/bold]")
    console.print()

    store = GlobalLearningStore()
    patterns = store.get_patterns(
        pattern_type=pattern_type,
        limit=limit,
    )

    if not patterns:
        console.print("[dim]No patterns learned yet. Run some jobs![/dim]")
        return

    table = Table(title="Learned Patterns")
    table.add_column("Type", style="cyan")
    table.add_column("Description")
    table.add_column("Freq", justify="right")
    table.add_column("Effectiveness", justify="right")

    for pattern in patterns:
        desc_str = pattern.description or ""
        desc = desc_str[:45] + "..." if len(desc_str) > 45 else desc_str
        table.add_row(
            pattern.pattern_type,
            desc or "[no description]",
            str(pattern.occurrence_count),
            f"{pattern.effectiveness_score:.0%}" if pattern.effectiveness_score else "-",
        )

    console.print(table)


def learning_activity(
    hours: int = typer.Option(
        24,
        "--hours",
        "-h",
        help="Show activity from the last N hours",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output as JSON for machine parsing",
    ),
) -> None:
    """View recent learning activity and pattern applications.

    Examples:
        mzt learning-activity           # Last 24 hours of activity
        mzt learning-activity -h 48     # Last 48 hours
        mzt learning-activity --json    # JSON output
    """
    from marianne.learning.global_store import get_global_store

    store = get_global_store()
    stats = store.get_execution_stats()

    window = store.get_optimal_execution_window()

    cutoff = datetime.now() - timedelta(hours=hours)
    recent_executions = store.get_similar_executions(limit=20)
    recent_count = sum(
        1 for e in recent_executions
        if e.completed_at and e.completed_at > cutoff
    )

    if json_output:
        output: dict[str, Any] = {
            "period_hours": hours,
            "recent_executions": recent_count,
            "success_without_retry_rate": round(
                stats.get("success_without_retry_rate", 0) * 100, 1
            ),
            "patterns_active": stats.get("total_patterns", 0),
            "optimal_hours": window.get("optimal_hours", []),
            "avoid_hours": window.get("avoid_hours", []),
            "scheduling_confidence": round(window.get("confidence", 0), 2),
        }
        console.print(json_lib.dumps(output, indent=2))
        return

    console.print(f"[bold]Learning Activity (last {hours} hours)[/bold]\n")

    console.print("[bold cyan]Recent Executions[/bold cyan]")
    console.print(f"  Executions in period: [green]{recent_count}[/green]")
    success_rate = stats.get("success_without_retry_rate", 0) * 100
    console.print(
        f"  First-attempt success: "
        f"[{'green' if success_rate > 70 else 'yellow'}]{success_rate:.1f}%[/]"
    )

    console.print("\n[bold cyan]Pattern Application[/bold cyan]")
    pattern_count = stats.get("total_patterns", 0)
    if pattern_count > 0:
        console.print(f"  Active patterns: [yellow]{pattern_count}[/yellow]")
        avg_eff = stats.get("avg_pattern_effectiveness", 0)
        console.print(
            f"  Avg effectiveness: "
            f"[{'green' if avg_eff > 0.6 else 'yellow'}]{avg_eff:.2f}[/]"
        )
    else:
        console.print("  [dim]No patterns learned yet[/dim]")

    console.print("\n[bold cyan]Optimal Execution Windows[/bold cyan]")
    if window.get("confidence", 0) > 0.3:
        optimal = window.get("optimal_hours", [])
        avoid = window.get("avoid_hours", [])

        if optimal:
            optimal_str = ", ".join(f"{h:02d}:00" for h in optimal)
            console.print(f"  [green]✓ Best hours:[/green] {optimal_str}")
        if avoid:
            avoid_str = ", ".join(f"{h:02d}:00" for h in avoid)
            console.print(f"  [red]✗ Avoid hours:[/red] {avoid_str}")

        console.print(
            f"  Confidence: [cyan]{window.get('confidence', 0):.0%}[/cyan] "
            f"(based on {window.get('sample_count', 0)} samples)"
        )
    else:
        console.print("  [dim]Insufficient data for scheduling recommendations[/dim]")

    console.print("\n[bold cyan]Learning Status[/bold cyan]")
    total_executions = stats.get("total_executions", 0)
    if total_executions >= 50:
        console.print("  [green]✓ Learning system is well-trained[/green]")
    elif total_executions >= 10:
        console.print("  [yellow]○ Learning system is gathering data[/yellow]")
    else:
        console.print("  [dim]○ Learning system is in early training[/dim]")
