"""Exploration budget management command.

Commands:
- patterns-budget: Display exploration budget status and history
"""

from __future__ import annotations

import json as json_lib
from typing import Any

import typer
from rich.table import Table

from ...output import console


def patterns_budget(
    job: str = typer.Option(
        None,
        "--job",
        "-j",
        help="Filter by specific job hash",
    ),
    history: bool = typer.Option(
        False,
        "--history",
        "-H",
        help="Show budget adjustment history",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-n",
        help="Number of history records to show",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON for machine parsing",
    ),
) -> None:
    """Display exploration budget status and history.

    The budget adjusts based on pattern entropy:
    - Low entropy -> budget increases (boost) to inject diversity
    - Healthy entropy -> budget decays toward floor
    - Budget never drops below floor (default 5%)

    Examples:
        mzt patterns-budget               # Show current budget status
        mzt patterns-budget --history     # View budget adjustment history
        mzt patterns-budget --job abc123  # Filter by specific job
        mzt patterns-budget --json        # JSON output for scripting
    """
    from marianne.learning.global_store import get_global_store

    store = get_global_store()

    if history:
        history_records = store.get_exploration_budget_history(
            job_hash=job, limit=limit
        )

        if json_output:
            budget_hist_output: list[dict[str, object]] = []
            for record in history_records:
                budget_hist_output.append({
                    "id": record.id,
                    "job_hash": record.job_hash,
                    "recorded_at": record.recorded_at.isoformat(),
                    "budget_value": round(record.budget_value, 4),
                    "entropy_at_time": (
                        round(record.entropy_at_time, 4) if record.entropy_at_time else None
                    ),
                    "adjustment_type": record.adjustment_type,
                    "adjustment_reason": record.adjustment_reason,
                })
            console.print(json_lib.dumps(budget_hist_output, indent=2))
            return

        if not history_records:
            console.print("[dim]No budget history found.[/dim]")
            console.print(
                "\n[dim]Hint: Enable exploration_budget in learning config "
                "to start tracking.[/dim]"
            )
            return

        table = Table(title="Exploration Budget History")
        table.add_column("Time", style="dim", width=16)
        table.add_column("Budget", justify="right", width=8)
        table.add_column("Entropy", justify="right", width=8)
        table.add_column("Type", width=12)
        table.add_column("Reason", width=35)

        for record in history_records:
            if record.budget_value <= 0.10:
                budget_color = "yellow"
            elif record.budget_value >= 0.30:
                budget_color = "cyan"
            else:
                budget_color = "green"
            budget_str = f"[{budget_color}]{record.budget_value:.1%}[/{budget_color}]"

            if record.entropy_at_time is not None:
                ent_color = "red" if record.entropy_at_time < 0.3 else "green"
                ent_str = f"[{ent_color}]{record.entropy_at_time:.3f}[/{ent_color}]"
            else:
                ent_str = "[dim]—[/dim]"

            type_colors = {
                "initial": "blue",
                "boost": "green",
                "decay": "dim",
                "floor_enforced": "yellow",
                "ceiling_enforced": "yellow",
            }
            type_color = type_colors.get(record.adjustment_type, "white")
            type_str = f"[{type_color}]{record.adjustment_type}[/{type_color}]"

            table.add_row(
                record.recorded_at.strftime("%m-%d %H:%M:%S"),
                budget_str,
                ent_str,
                type_str,
                record.adjustment_reason or "",
            )

        console.print(table)
        console.print(f"\n[dim]Showing {len(history_records)} record(s)[/dim]")
        return

    current = store.get_exploration_budget(job_hash=job)
    stats = store.get_exploration_budget_statistics(job_hash=job)

    if json_output:
        entropy_val = None
        if current and current.entropy_at_time:
            entropy_val = round(current.entropy_at_time, 4)
        budget_output: dict[str, dict[str, Any]] = {
            "current": {
                "budget_value": round(current.budget_value, 4) if current else None,
                "entropy_at_time": entropy_val,
                "adjustment_type": current.adjustment_type if current else None,
                "recorded_at": current.recorded_at.isoformat() if current else None,
            },
            "statistics": {
                "avg_budget": round(stats["avg_budget"], 4),
                "min_budget": round(stats["min_budget"], 4),
                "max_budget": round(stats["max_budget"], 4),
                "total_adjustments": stats["total_adjustments"],
                "floor_enforcements": stats["floor_enforcements"],
                "boost_count": stats["boost_count"],
                "decay_count": stats["decay_count"],
            },
        }
        console.print(json_lib.dumps(budget_output, indent=2))
        return

    console.print("[bold]Exploration Budget Status[/bold]\n")

    if current is None:
        console.print("[dim]No budget records found.[/dim]")
        console.print("\n[dim]Hint: Enable exploration_budget in learning config:[/dim]")
        console.print("[dim]  learning:[/dim]")
        console.print("[dim]    exploration_budget:[/dim]")
        console.print("[dim]      enabled: true[/dim]")
        return

    if current.budget_value <= 0.10:
        budget_color = "yellow"
        budget_status = "Low (near floor)"
    elif current.budget_value >= 0.30:
        budget_color = "cyan"
        budget_status = "High (exploring)"
    else:
        budget_color = "green"
        budget_status = "Normal"

    console.print(
        f"  Current Budget: [{budget_color}]{current.budget_value:.1%}"
        f"[/{budget_color}] ({budget_status})"
    )

    if current.entropy_at_time is not None:
        ent_color = "red" if current.entropy_at_time < 0.3 else "green"
        console.print(
            f"  Entropy at Last Check: [{ent_color}]"
            f"{current.entropy_at_time:.4f}[/{ent_color}]"
        )

    console.print(f"  Last Adjustment: [dim]{current.adjustment_type}[/dim]")
    console.print(f"  Last Updated: [dim]{current.recorded_at.strftime('%Y-%m-%d %H:%M:%S')}[/dim]")

    console.print("")

    if stats["total_adjustments"] > 0:
        console.print("[bold]Statistics[/bold]")
        console.print(f"  Average Budget: [cyan]{stats['avg_budget']:.1%}[/cyan]")
        console.print(f"  Range: [dim]{stats['min_budget']:.1%} - {stats['max_budget']:.1%}[/dim]")
        console.print(f"  Total Adjustments: [yellow]{stats['total_adjustments']}[/yellow]")
        console.print(
            f"    Boosts: [green]{stats['boost_count']}[/green] | "
            f"Decays: [dim]{stats['decay_count']}[/dim] | "
            f"Floor Enforced: [yellow]{stats['floor_enforcements']}[/yellow]"
        )

    console.print("")
    if stats["floor_enforcements"] > stats["total_adjustments"] * 0.3:
        console.print("[yellow]⚠ Budget frequently hitting floor[/yellow]")
        console.print("[dim]Consider lowering entropy threshold or increasing boost amount.[/dim]")
    elif stats["boost_count"] > stats["decay_count"] * 2:
        console.print("[cyan]ℹ Frequent boosts - entropy may be consistently low[/cyan]")
        console.print("[dim]This may indicate pattern concentration issues.[/dim]")
    elif stats["total_adjustments"] > 0:
        console.print("[green]✓ Budget adjusting normally[/green]")
