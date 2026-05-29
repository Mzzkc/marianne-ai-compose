"""Compile command for Marianne CLI.

Implements ``mzt compile`` — takes semantic agent definitions and produces
complete Mozart score YAML files via the composition compiler.

This is the Marianne-side wrapper that delegates to the
``marianne_compiler`` package. The compiler is an optional dependency:
when not installed, this module imports but ``mzt compile`` is not
registered (handled by the try/except guard in ``cli/__init__.py``).
"""

from __future__ import annotations

from pathlib import Path

import typer
import yaml

from marianne.core.logging import get_logger

from ..output import console

_logger = get_logger("cli.compile")


def compile_scores(
    config: Path = typer.Argument(
        ...,
        help="Path to the compiler config YAML file.",
        exists=True,
        readable=True,
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory for generated score files. "
        "Defaults to scores/ next to the config file.",
    ),
    agents_dir: Path | None = typer.Option(
        None,
        "--agents-dir",
        help="Directory for agent identity stores. "
        "Defaults to ~/.mzt/agents/.",
    ),
    fleet: bool = typer.Option(
        False,
        "--fleet",
        help="Force fleet config generation even for a single agent.",
    ),
    seed_only: bool = typer.Option(
        False,
        "--seed-only",
        help="Create agent identity stores without generating scores.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show compilation summary without writing files.",
    ),
) -> None:
    """Compile semantic agent definitions into Mozart scores.

    Reads a YAML config that defines agents as people (voice, focus,
    techniques, instruments) and produces complete Mozart score YAML
    for each agent, plus identity directories and fleet configs.
    """
    from marianne_compiler.fleet import FleetGenerator
    from marianne_compiler.pipeline import CompilationPipeline

    # Load and validate config
    try:
        with open(config) as f:
            config_data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        _logger.error("compile_yaml_error", path=str(config), error=str(e))
        console.print(f"[red]Error:[/red] Invalid YAML in {config}: {e}")
        raise typer.Exit(code=1) from None
    except OSError as e:
        _logger.error("compile_read_error", path=str(config), error=str(e))
        console.print(f"[red]Error:[/red] Cannot read {config}: {e}")
        raise typer.Exit(code=1) from None

    agents = config_data.get("agents", [])
    if not agents:
        console.print("[red]Error:[/red] Config must contain at least one agent.")
        raise typer.Exit(code=1)

    project = config_data.get("project", {})
    project_name = project.get("name", config.stem)

    # Dry run — show summary and exit
    if dry_run:
        console.print(f"[bold]Dry Run:[/bold] {project_name}")
        console.print(f"  Agents: {len(agents)}")
        for agent in agents:
            name = agent.get("name", "unnamed")
            focus = agent.get("focus", "")
            label = f"    - {name}"
            if focus:
                label += f" ({focus})"
            console.print(label)
        console.print(f"  Output: {output or 'scores/'}")
        fleet_label = "yes" if fleet or len(agents) > 1 else "no"
        console.print(f"  Fleet: {fleet_label}")
        raise typer.Exit(code=0)

    # Resolve directories
    output_dir = output or (config.parent / "scores")
    resolved_agents_dir = agents_dir or Path.home() / ".mzt" / "agents"

    # Create pipeline
    pipeline = CompilationPipeline(agents_dir=resolved_agents_dir)

    # Seed-only mode — create identities without scores
    if seed_only:
        for agent_def in agents:
            identity_path = pipeline.seed_identity(agent_def, resolved_agents_dir)
            console.print(f"[green]Seeded identity:[/green] {identity_path}")
        _logger.info(
            "compile_seed_complete",
            agent_count=len(agents),
            agents_dir=str(resolved_agents_dir),
        )
        raise typer.Exit(code=0)

    # Full compilation
    try:
        score_paths = pipeline.compile_config(config_data, output_dir)
    except Exception as e:
        _logger.error("compile_failed", error=str(e), exc_info=True)
        console.print(f"[red]Error:[/red] Compilation failed: {e}")
        raise typer.Exit(code=1) from None

    # Force fleet generation for single agent if --fleet flag set
    if fleet and len(agents) == 1:
        fleet_path = output_dir / "fleet.yaml"
        if not fleet_path.exists():
            fleet_gen = FleetGenerator()
            fleet_gen.write(config_data, output_dir, fleet_path)
            score_paths.append(fleet_path)

    for path in score_paths:
        console.print(f"[green]Generated:[/green] {path}")

    console.print(
        f"\n[bold]Compiled {len(agents)} agent(s) to {output_dir}[/bold]"
    )
    _logger.info(
        "compile_complete",
        project=project_name,
        agent_count=len(agents),
        score_count=len(score_paths),
        output_dir=str(output_dir),
    )


# =============================================================================
# Public API
# =============================================================================

# Alias for validation and direct import compatibility.
# The Typer command is registered as ``compile`` in cli/__init__.py;
# callers that do ``from marianne.cli.commands.compile import compile``
# should get the same function.
compile = compile_scores

__all__ = [
    "compile",
    "compile_scores",
]
