"""Pattern analysis and listing commands.

Commands:
- patterns-why: Analyze WHY patterns succeed with metacognitive insights
- patterns-list: View global learning patterns with filtering
"""

from __future__ import annotations

import json as json_lib
from typing import Any

import typer
from rich.panel import Panel
from rich.table import Table

from ...output import console, output_error


def patterns_why(
    pattern_id: str = typer.Argument(
        None,
        help="Pattern ID to analyze (first 10 chars from 'patterns' command). "
        "If omitted, shows all patterns with captured success factors.",
    ),
    min_observations: int = typer.Option(
        1,
        "--min-obs",
        "-m",
        help="Minimum success factor observations required",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-n",
        help="Maximum number of patterns to display",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output as JSON for machine parsing",
    ),
) -> None:
    """Analyze WHY patterns succeed with metacognitive insights.

    Shows success factors - the context conditions that contribute to
    pattern effectiveness. This helps understand CAUSALITY behind patterns,
    not just correlation.

    v22 Evolution: Metacognitive Pattern Reflection

    Examples:
        mzt patterns-why              # Show all patterns with WHY analysis
        mzt patterns-why abc123       # Analyze specific pattern
        mzt patterns-why --min-obs 3  # Only patterns with 3+ observations
        mzt patterns-why --json       # JSON output for scripting
    """
    from marianne.learning.global_store import get_global_store

    store = get_global_store()

    if pattern_id:
        patterns_list = store.get_patterns(min_priority=0.0, limit=1000)
        matching = [p for p in patterns_list if p.id.startswith(pattern_id)]

        if not matching:
            output_error(
                f"No pattern found with ID starting with '{pattern_id}'",
                hints=["Run 'mzt patterns-list' to see available patterns."],
            )
            raise typer.Exit(1)

        if len(matching) > 1:
            match_lines = [f"  {p.id} - {p.pattern_name}" for p in matching[:5]]
            output_error(
                f"Multiple patterns match '{pattern_id}':\n" + "\n".join(match_lines),
                severity="warning",
                hints=["Provide more characters to uniquely identify the pattern."],
            )
            raise typer.Exit(1)

        pattern = matching[0]
        analysis = store.analyze_pattern_why(pattern.id)

        if json_output:
            console.print(json_lib.dumps(analysis, indent=2, default=str))
            return

        console.print(Panel(
            f"[bold]{analysis.get('pattern_name', 'Unknown')}[/bold]\n"
            f"[dim]Type: {analysis.get('pattern_type', 'unknown')}[/dim]",
            title="WHY Analysis",
            border_style="magenta",
        ))

        if not analysis.get("has_factors"):
            console.print("\n[yellow]No success factors captured yet.[/yellow]")
            console.print(
                "[dim]Apply this pattern to successful executions "
                "to capture WHY it works.[/dim]"
            )
            return

        console.print("\n[bold]Factors Summary[/bold]")
        console.print(f"  {analysis.get('factors_summary', 'No summary')}")

        key_conditions = analysis.get("key_conditions", [])
        if key_conditions:
            console.print("\n[bold]Key Conditions[/bold]")
            for cond in key_conditions:
                console.print(f"  • {cond}")

        console.print("\n[bold]Metrics[/bold]")
        table = Table(show_header=False, box=None)
        table.add_column("Field", style="dim", width=20)
        table.add_column("Value", style="bold")

        table.add_row("Observations", str(analysis.get("observation_count", 0)))
        table.add_row("Success Rate", f"{analysis.get('success_rate', 0):.1%}")
        table.add_row("Confidence", f"{analysis.get('confidence', 0):.2f}")
        table.add_row("Trust Score", f"{analysis.get('trust_score', 0):.2f}")
        table.add_row("Effectiveness", f"{analysis.get('effectiveness_score', 0):.2f}")

        console.print(table)

        recommendations = analysis.get("recommendations", [])
        if recommendations:
            console.print("\n[bold]Recommendations[/bold]")
            for rec in recommendations:
                console.print(f"  → {rec}")
    else:
        results = store.get_patterns_with_why(
            min_observations=min_observations,
            limit=limit,
        )

        if json_output:
            output = [
                {"pattern_id": p.id, "pattern_name": p.pattern_name, "analysis": a}
                for p, a in results
            ]
            console.print(json_lib.dumps(output, indent=2, default=str))
            return

        if not results:
            console.print(
                "[yellow]No patterns with captured success factors found.[/yellow]"
            )
            console.print(
                "[dim]Success factors are captured when patterns lead to "
                "successful executions.[/dim]"
            )
            return

        console.print(
            f"[bold]Patterns with WHY Analysis[/bold] "
            f"[dim]({len(results)} patterns with ≥{min_observations} observations)[/dim]\n"
        )

        table = Table(show_header=True, box=None)
        table.add_column("Pattern", style="cyan", width=30)
        table.add_column("Obs", justify="right", width=5)
        table.add_column("Success%", justify="right", width=8)
        table.add_column("Confidence", justify="right", width=10)
        table.add_column("Key Insight", width=40)

        for pattern, analysis in results:
            key_conditions = analysis.get("key_conditions", [])
            insight = key_conditions[0] if key_conditions else analysis.get("factors_summary", "")
            if len(insight) > 38:
                insight = insight[:35] + "..."

            obs = analysis.get("observation_count", 0)
            success_rate = analysis.get("success_rate", 0)
            confidence = analysis.get("confidence", 0)

            name = pattern.pattern_name
            display_name = f"{name[:28]}..." if len(name) > 30 else name
            table.add_row(
                display_name,
                str(obs),
                f"{success_rate:.0%}",
                f"{confidence:.2f}",
                insight,
            )

        console.print(table)
        console.print(
            "\n[dim]Use 'mzt patterns-why <id>' for detailed analysis.[/dim]"
        )


def patterns_list(
    global_patterns: bool = typer.Option(
        True,
        "--global/--local",
        "-g/-l",
        help="Show global patterns (default) or local workspace patterns",
    ),
    min_priority: float = typer.Option(
        0.0,
        "--min-priority",
        "-p",
        help="Minimum priority score to display (0.0-1.0)",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-n",
        help="Maximum number of patterns to display",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output as JSON for machine parsing",
    ),
    quarantined: bool = typer.Option(
        False,
        "--quarantined",
        "-q",
        help="Show only quarantined patterns",
    ),
    high_trust: bool = typer.Option(
        False,
        "--high-trust",
        help="Show only patterns with trust >= 0.7",
    ),
    low_trust: bool = typer.Option(
        False,
        "--low-trust",
        help="Show only patterns with trust <= 0.3",
    ),
) -> None:
    """View global learning patterns.

    Displays patterns learned from job executions across all workspaces.

    Examples:
        mzt patterns-list                  # Show global patterns
        mzt patterns-list --min-priority 0.5  # Only high-priority patterns
        mzt patterns-list --json           # JSON output for scripting
        mzt patterns-list --quarantined    # Show quarantined patterns
        mzt patterns-list --high-trust     # Show trusted patterns only
    """
    from marianne.learning.global_store import QuarantineStatus, get_global_store

    store = get_global_store()

    filter_kwargs: dict[str, Any] = {
        "min_priority": min_priority,
        "limit": limit,
    }
    if quarantined:
        filter_kwargs["quarantine_status"] = QuarantineStatus.QUARANTINED
    if high_trust:
        filter_kwargs["min_trust"] = 0.7
    if low_trust:
        filter_kwargs["max_trust"] = 0.3

    if json_output:
        pattern_list_result = store.get_patterns(**filter_kwargs)
        output = []
        for p in pattern_list_result:
            output.append({
                "id": p.id,
                "pattern_type": p.pattern_type,
                "pattern_name": p.pattern_name,
                "description": p.description,
                "occurrence_count": p.occurrence_count,
                "effectiveness_score": round(p.effectiveness_score, 3),
                "priority_score": round(p.priority_score, 3),
                "context_tags": list(p.context_tags) if p.context_tags else [],
                "quarantine_status": p.quarantine_status.value,
                "trust_score": round(p.trust_score, 3),
            })
        console.print(json_lib.dumps(output, indent=2))
        return

    pattern_list_result = store.get_patterns(**filter_kwargs)

    if not pattern_list_result:
        console.print("[dim]No patterns found in global learning store.[/dim]")
        console.print(
            "\n[dim]Hint: Patterns are learned from job executions. "
            "Run jobs with learning enabled to build patterns.[/dim]"
        )
        return

    table = Table(title="Global Learning Patterns")
    table.add_column("ID", style="cyan", no_wrap=True, width=10)
    table.add_column("Type", style="yellow", width=12)
    table.add_column("Name", style="bold", width=20)
    table.add_column("Status", width=10)
    table.add_column("Trust", justify="right", width=6)
    table.add_column("Auto", justify="center", width=4)
    table.add_column("Count", justify="right", width=5)
    table.add_column("Effect", justify="right", width=6)
    table.add_column("Prior", justify="right", width=5)

    for p in pattern_list_result:
        is_auto_eligible = (
            p.trust_score >= 0.85
            and p.quarantine_status.value == "validated"
        )
        auto_str = "[green]⚡[/green]" if is_auto_eligible else ""

        eff = p.effectiveness_score
        eff_color = "green" if eff > 0.7 else "yellow" if eff > 0.4 else "red"
        eff_str = f"[{eff_color}]{eff:.2f}[/{eff_color}]"

        pri = p.priority_score
        pri_color = "green" if pri > 0.7 else "yellow" if pri > 0.4 else "dim"
        pri_str = f"[{pri_color}]{pri:.2f}[/{pri_color}]"

        status = p.quarantine_status.value
        status_colors = {
            "pending": "dim",
            "quarantined": "red",
            "validated": "green",
            "retired": "dim italic",
        }
        status_color = status_colors.get(status, "dim")
        status_str = f"[{status_color}]{status}[/{status_color}]"

        trust = p.trust_score
        trust_color = "green" if trust >= 0.7 else "yellow" if trust >= 0.4 else "red"
        trust_str = f"[{trust_color}]{trust:.2f}[/{trust_color}]"

        table.add_row(
            p.id[:10],
            p.pattern_type[:12],
            p.pattern_name[:20],
            status_str,
            trust_str,
            auto_str,
            str(p.occurrence_count),
            eff_str,
            pri_str,
        )

    console.print(table)
    console.print(f"\n[dim]Showing {len(pattern_list_result)} pattern(s)[/dim]")
