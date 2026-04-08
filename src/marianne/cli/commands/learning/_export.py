"""Learning data export and evolution recording commands.

Commands:
- learning-export: Export learning store data to workspace files
- learning-record-evolution: Record an evolution cycle in the trajectory table
"""
from __future__ import annotations

import json as json_lib
from pathlib import Path
from typing import Annotated, Any

import typer

from ...output import console


def _write_file(path: Path, content: str) -> None:
    """Write content to file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _format_markdown_insights(patterns: list[Any], filter_note: str = "") -> str:
    """Format SEMANTIC_INSIGHT patterns as structured markdown."""
    lines = ["# Semantic Insights\n"]

    if filter_note:
        lines.append(f"_{filter_note}_\n")

    if not patterns:
        lines.append("No semantic insights found in the learning store.\n")
        return "\n".join(lines)

    # Group by tags (category)
    categories: dict[str, list[Any]] = {}
    for p in patterns:
        tags = getattr(p, "context_tags", None) or []
        cat = tags[0] if tags else "uncategorized"
        categories.setdefault(cat, []).append(p)

    for cat, cat_patterns in sorted(categories.items()):
        lines.append(f"## {cat.replace('_', ' ').title()}\n")
        for p in cat_patterns:
            lines.append(f"### {p.pattern_name}")
            lines.append(f"- **ID:** `{p.id}`")
            lines.append(
                f"- **Effectiveness:** {p.effectiveness_score:.1%}"
                if p.effectiveness_score
                else "- **Effectiveness:** N/A"
            )
            lines.append(
                f"- **Trust:** {p.trust_score:.2f}"
                if p.trust_score is not None
                else "- **Trust:** N/A"
            )
            lines.append(f"- **Occurrences:** {p.occurrence_count}")
            lines.append(
                f"- **Success/Failure:** "
                f"{getattr(p, 'led_to_success_count', 0)}/"
                f"{getattr(p, 'led_to_failure_count', 0)}"
            )
            lines.append(
                f"- **Quarantine:** "
                f"{getattr(p, 'quarantine_status', 'unknown')}"
            )
            if p.description:
                lines.append(f"\n{p.description}\n")
            lines.append("")

    return "\n".join(lines)


def _format_markdown_drift(effectiveness_drift: list[Any], epistemic_drift: list[Any]) -> str:
    """Format drift data as structured markdown."""
    lines = ["# Drift Report\n"]

    lines.append("## Effectiveness Drift\n")
    if not effectiveness_drift:
        lines.append("No patterns with significant effectiveness drift.\n")
    else:
        for m in effectiveness_drift:
            lines.append(f"### {m.pattern_name}")
            lines.append(f"- **Pattern ID:** `{m.pattern_id}`")
            lines.append(f"- **Before:** {m.effectiveness_before:.1%}")
            lines.append(f"- **After:** {m.effectiveness_after:.1%}")
            lines.append(
                f"- **Drift:** {m.drift_magnitude:.1%} ({m.drift_direction})"
            )
            lines.append(
                f"- **Grounding:** {m.grounding_confidence_avg:.1%}"
            )
            lines.append(f"- **Applications analyzed:** {m.applications_analyzed}")
            lines.append("")

    lines.append("## Epistemic Drift\n")
    if not epistemic_drift:
        lines.append("No patterns with significant epistemic drift.\n")
    else:
        for m in epistemic_drift:
            lines.append(f"### {m.pattern_name}")
            lines.append(f"- **Pattern ID:** `{m.pattern_id}`")
            lines.append(f"- **Confidence before:** {m.confidence_before:.1%}")
            lines.append(f"- **Confidence after:** {m.confidence_after:.1%}")
            lines.append(
                f"- **Change:** {m.belief_change:+.1%} ({m.drift_direction})"
            )
            lines.append(f"- **Entropy:** {m.belief_entropy:.3f}")
            lines.append("")

    return "\n".join(lines)


def _format_markdown_entropy(entropy_metrics: Any, alerts: list[Any]) -> str:
    """Format entropy state as structured markdown."""
    lines = ["# Entropy State\n"]

    lines.append("## Current Metrics\n")
    lines.append(f"- **Shannon entropy:** {entropy_metrics.shannon_entropy:.3f}")
    lines.append(f"- **Diversity index:** {entropy_metrics.diversity_index:.3f}")
    lines.append(f"- **Unique patterns:** {entropy_metrics.unique_pattern_count}")
    lines.append(
        f"- **Effective patterns:** {entropy_metrics.effective_pattern_count}"
    )
    lines.append(
        f"- **Dominant pattern share:** {entropy_metrics.dominant_pattern_share:.1%}"
    )
    lines.append("")

    lines.append("## Recent Entropy Responses\n")
    if not alerts:
        lines.append("No recent entropy responses.\n")
    else:
        for a in alerts:
            response_type = getattr(a, "response_type", str(a))
            lines.append(f"- {response_type}")
    lines.append("")

    return "\n".join(lines)


def _format_markdown_health(patterns: list[Any], filter_note: str = "") -> str:
    """Format pattern health data as structured markdown."""
    from marianne.learning.store.models import QuarantineStatus

    lines = ["# Pattern Health\n"]

    if filter_note:
        lines.append(f"_{filter_note}_\n")

    # Fix: Compare against QuarantineStatus enum, not string
    quarantined = [
        p
        for p in patterns
        if getattr(p, "quarantine_status", None) == QuarantineStatus.QUARANTINED
    ]
    pending = [
        p
        for p in patterns
        if getattr(p, "quarantine_status", None) == QuarantineStatus.PENDING
    ]
    low_trust = [p for p in patterns if (p.trust_score or 1.0) < 0.3]
    high_variance = [p for p in patterns if (p.variance or 0) > 0.5]
    zero_apps = [p for p in patterns if p.occurrence_count == 0]

    lines.append(f"## Quarantined Patterns ({len(quarantined)})\n")
    for p in quarantined:
        lines.append(
            f"- **{p.pattern_name}** (`{p.id}`): "
            f"{p.description or 'no description'}"
        )
    if not quarantined:
        lines.append("None.\n")
    lines.append("")

    lines.append(f"## Pending Validation Patterns ({len(pending)})\n")
    for p in pending:
        lines.append(
            f"- **{p.pattern_name}** (`{p.id}`): "
            f"eff={p.effectiveness_score:.1%}, trust={p.trust_score:.2f}"
        )
    if not pending:
        lines.append("None.\n")
    lines.append("")

    lines.append(f"## Low Trust Patterns ({len(low_trust)})\n")
    for p in low_trust:
        lines.append(
            f"- **{p.pattern_name}** (`{p.id}`): "
            f"trust={p.trust_score:.2f}, eff={p.effectiveness_score:.1%}"
        )
    if not low_trust:
        lines.append("None.\n")
    lines.append("")

    lines.append(f"## High Variance Patterns ({len(high_variance)})\n")
    for p in high_variance:
        lines.append(
            f"- **{p.pattern_name}** (`{p.id}`): variance={p.variance:.3f}"
        )
    if not high_variance:
        lines.append("None.\n")
    lines.append("")

    lines.append(f"## Zero-Application Patterns ({len(zero_apps)})\n")
    for p in zero_apps:
        lines.append(
            f"- **{p.pattern_name}** (`{p.id}`): "
            f"{p.description or 'no description'}"
        )
    if not zero_apps:
        lines.append("None.\n")

    return "\n".join(lines)


def _format_markdown_evolution(entries: list[Any]) -> str:
    """Format evolution trajectory as structured markdown."""
    lines = ["# Evolution History\n"]

    if not entries:
        lines.append("No evolution trajectory entries found.\n")
        return "\n".join(lines)

    for e in entries:
        lines.append(f"## Cycle {e.cycle}")
        lines.append(f"- **Date:** {e.recorded_at}")
        lines.append(f"- **Evolutions completed:** {e.evolutions_completed}")
        lines.append(f"- **Evolutions deferred:** {e.evolutions_deferred}")
        lines.append(f"- **Issue classes:** {', '.join(e.issue_classes)}")
        lines.append(f"- **CV avg:** {e.cv_avg:.3f}")
        lines.append(f"- **Implementation LOC:** {e.implementation_loc}")
        lines.append(f"- **Test LOC:** {e.test_loc}")
        lines.append(f"- **LOC accuracy:** {e.loc_accuracy:.1%}")
        if e.notes:
            lines.append(f"- **Notes:** {e.notes}")
        lines.append("")

    return "\n".join(lines)


def _format_markdown_errors(stats: dict[str, Any]) -> str:
    """Format error landscape as structured markdown."""
    lines = ["# Error Landscape\n"]

    lines.append("## Execution Summary\n")
    lines.append(f"- **Total executions:** {stats.get('total_executions', 0)}")
    success_rate = stats.get("success_without_retry_rate", 0) * 100
    lines.append(f"- **First-attempt success rate:** {success_rate:.1f}%")
    lines.append(
        f"- **Total error recoveries:** "
        f"{stats.get('total_error_recoveries', 0)}"
    )
    recovery_rate = stats.get("error_recovery_success_rate", 0) * 100
    lines.append(f"- **Recovery success rate:** {recovery_rate:.1f}%")
    lines.append("")

    return "\n".join(lines)


def learning_export(
    output_dir: Annotated[
        str,
        typer.Option("--output-dir", "-o", help="Directory to write export files"),
    ] = "./learning-export",
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: markdown or json"),
    ] = "markdown",
    since: Annotated[
        int,
        typer.Option("--since", "-s", help="Export data from last N days"),
    ] = 30,
    include_pending: Annotated[
        bool,
        typer.Option(
            "--include-pending/--no-include-pending",
            help="Include PENDING quarantine patterns in export (default: True)",
        ),
    ] = True,
    min_effectiveness: Annotated[
        float,
        typer.Option(
            "--min-effectiveness",
            help="Minimum effectiveness score (0.0-1.0) for exported patterns",
        ),
    ] = 0.0,
) -> None:
    """Export learning store data to workspace files.

    Writes structured files for consumption by evolution scores:
    semantic-insights, drift-report, entropy-state, pattern-health,
    evolution-history, error-landscape.

    Examples:
        mzt learning-export --output-dir ./workspace/learning
        mzt learning-export --format json --since 60
        mzt learning-export --min-effectiveness 0.6 --no-include-pending
    """
    from marianne.learning.global_store import get_global_store
    from marianne.learning.patterns import PatternType
    from marianne.learning.store.models import QuarantineStatus

    store = get_global_store()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    ext = "json" if fmt == "json" else "md"

    # Build filter description for headers
    filters_desc = []
    if not include_pending:
        filters_desc.append("excluding PENDING patterns")
    if min_effectiveness > 0.0:
        filters_desc.append(f"min_effectiveness >= {min_effectiveness:.1%}")
    filter_note = (
        f"Filters applied: {', '.join(filters_desc)}"
        if filters_desc
        else "No filters applied (all patterns exported)"
    )

    # 1. Semantic insights
    # Fix: Use PatternType.SEMANTIC_INSIGHT.value (lowercase "semantic_insight")
    # Apply effectiveness filter by checking effectiveness_score after retrieval
    all_semantic = store.get_patterns(
        pattern_type=PatternType.SEMANTIC_INSIGHT.value,
        min_priority=0.0,
        limit=1000,
        exclude_quarantined=False,  # Handle PENDING separately
    )

    # Apply custom filters
    insights = []
    for p in all_semantic:
        # Filter by quarantine status
        q_status = getattr(p, "quarantine_status", None)
        if not include_pending and q_status == QuarantineStatus.PENDING:
            continue

        # Filter by effectiveness
        if (p.effectiveness_score or 0.0) < min_effectiveness:
            continue

        insights.append(p)
    if fmt == "json":
        insights_data: Any = [
            {
                "id": p.id,
                "name": p.pattern_name,
                "type": p.pattern_type,
                "description": p.description,
                "effectiveness": p.effectiveness_score,
                "trust": p.trust_score,
                "occurrences": p.occurrence_count,
                "tags": getattr(p, "context_tags", []),
                "quarantine_status": getattr(p, "quarantine_status", None),
            }
            for p in insights
        ]
        _write_file(
            out / f"semantic-insights.{ext}",
            json_lib.dumps(insights_data, indent=2),
        )
    else:
        _write_file(
            out / f"semantic-insights.{ext}",
            _format_markdown_insights(insights, filter_note),
        )

    # 2. Drift report
    eff_drift = store.get_drifting_patterns(drift_threshold=0.15, limit=30)
    epi_drift = store.get_epistemic_drifting_patterns(
        drift_threshold=0.1, limit=30
    )
    if fmt == "json":
        drift_data: Any = {
            "effectiveness_drift": [
                {
                    "pattern_id": m.pattern_id,
                    "name": m.pattern_name,
                    "before": m.effectiveness_before,
                    "after": m.effectiveness_after,
                    "magnitude": m.drift_magnitude,
                    "direction": m.drift_direction,
                }
                for m in eff_drift
            ],
            "epistemic_drift": [
                {
                    "pattern_id": m.pattern_id,
                    "name": m.pattern_name,
                    "confidence_before": m.confidence_before,
                    "confidence_after": m.confidence_after,
                    "change": m.belief_change,
                    "direction": m.drift_direction,
                }
                for m in epi_drift
            ],
        }
        _write_file(
            out / f"drift-report.{ext}", json_lib.dumps(drift_data, indent=2)
        )
    else:
        _write_file(
            out / f"drift-report.{ext}",
            _format_markdown_drift(eff_drift, epi_drift),
        )

    # 3. Entropy state
    entropy = store.calculate_pattern_entropy()
    alerts = store.get_entropy_response_history(limit=10)
    if fmt == "json":
        entropy_data: Any = {
            "shannon_entropy": entropy.shannon_entropy,
            "diversity_index": entropy.diversity_index,
            "unique_patterns": entropy.unique_pattern_count,
            "effective_patterns": entropy.effective_pattern_count,
            "dominant_share": entropy.dominant_pattern_share,
            "recent_responses": [str(a) for a in alerts],
        }
        _write_file(
            out / f"entropy-state.{ext}",
            json_lib.dumps(entropy_data, indent=2),
        )
    else:
        _write_file(
            out / f"entropy-state.{ext}",
            _format_markdown_entropy(entropy, alerts),
        )

    # 4. Pattern health
    all_patterns = store.get_patterns(
        min_priority=0.0, limit=500, exclude_quarantined=False
    )
    if fmt == "json":
        health_data: Any = {
            "quarantined": [
                {"id": p.id, "name": p.pattern_name}
                for p in all_patterns
                if getattr(p, "quarantine_status", "") == "QUARANTINED"
            ],
            "low_trust": [
                {"id": p.id, "name": p.pattern_name, "trust": p.trust_score}
                for p in all_patterns
                if (p.trust_score or 1.0) < 0.3
            ],
        }
        _write_file(
            out / f"pattern-health.{ext}",
            json_lib.dumps(health_data, indent=2),
        )
    else:
        _write_file(
            out / f"pattern-health.{ext}",
            _format_markdown_health(all_patterns, filter_note),
        )

    # 5. Evolution history
    trajectory = store.get_trajectory(limit=5)
    if fmt == "json":
        traj_data: Any = [
            {
                "cycle": e.cycle,
                "date": str(e.recorded_at),
                "completed": e.evolutions_completed,
                "deferred": e.evolutions_deferred,
                "issues": e.issue_classes,
                "impl_loc": e.implementation_loc,
                "test_loc": e.test_loc,
            }
            for e in trajectory
        ]
        _write_file(
            out / f"evolution-history.{ext}",
            json_lib.dumps(traj_data, indent=2),
        )
    else:
        _write_file(
            out / f"evolution-history.{ext}",
            _format_markdown_evolution(trajectory),
        )

    # 6. Error landscape
    exec_stats = store.get_execution_stats()
    if fmt == "json":
        _write_file(
            out / f"error-landscape.{ext}",
            json_lib.dumps(exec_stats, indent=2),
        )
    else:
        _write_file(
            out / f"error-landscape.{ext}", _format_markdown_errors(exec_stats)
        )

    console.print(f"[green]Exported learning data to {out}/[/green]")
    console.print(f"  Semantic insights: {len(insights)} patterns")
    console.print(
        f"  Drift alerts: {len(eff_drift)} effectiveness, "
        f"{len(epi_drift)} epistemic"
    )
    console.print(f"  Patterns total: {len(all_patterns)}")
    console.print(f"  Evolution history: {len(trajectory)} cycles")


def learning_record_evolution(
    cycle: Annotated[
        int, typer.Option("--cycle", help="Evolution cycle number")
    ] = ...,  # type: ignore[assignment]
    evolutions_completed: Annotated[
        int,
        typer.Option(
            "--evolutions-completed", help="Number of evolutions completed"
        ),
    ] = ...,  # type: ignore[assignment]
    issue_classes: Annotated[
        str,
        typer.Option("--issue-classes", help="Comma-separated issue classes"),
    ] = ...,  # type: ignore[assignment]
    implementation_loc: Annotated[
        int,
        typer.Option(
            "--implementation-loc", help="Lines of implementation code"
        ),
    ] = ...,  # type: ignore[assignment]
    test_loc: Annotated[
        int, typer.Option("--test-loc", help="Lines of test code")
    ] = ...,  # type: ignore[assignment]
    evolutions_deferred: Annotated[
        int, typer.Option("--evolutions-deferred", help="Number deferred")
    ] = 0,
    cv_avg: Annotated[
        float, typer.Option("--cv-avg", help="Average consciousness volume")
    ] = 0.0,
    loc_accuracy: Annotated[
        float,
        typer.Option("--loc-accuracy", help="LOC estimation accuracy (0-2)"),
    ] = 1.0,
    notes: Annotated[
        str, typer.Option("--notes", help="Optional notes")
    ] = "",
) -> None:
    """Record an evolution cycle in the trajectory table.

    Examples:
        mzt learning-record-evolution --cycle 26 \\
            --evolutions-completed 2 \\
            --issue-classes "infrastructure_activation,testing_depth" \\
            --implementation-loc 150 --test-loc 200
    """
    from marianne.learning.global_store import get_global_store

    store = get_global_store()

    classes = [c.strip() for c in issue_classes.split(",") if c.strip()]

    entry_id = store.record_evolution_entry(
        cycle=cycle,
        evolutions_completed=evolutions_completed,
        evolutions_deferred=evolutions_deferred,
        issue_classes=classes,
        cv_avg=cv_avg,
        implementation_loc=implementation_loc,
        test_loc=test_loc,
        loc_accuracy=loc_accuracy,
        notes=notes,
    )

    console.print(f"[green]Recorded evolution cycle {cycle}[/green]")
    console.print(f"  Entry ID: {entry_id}")
    console.print(f"  Evolutions completed: {evolutions_completed}")
    console.print(f"  Issue classes: {', '.join(classes)}")
