"""Compile command for Marianne CLI.

Implements ``mzt compile`` — takes semantic agent definitions and produces
complete Marianne score YAML files via the composition compiler.

This is the Marianne-side wrapper that delegates to the
``marianne_compiler`` package. The compiler is an optional dependency:
when not installed, this module imports but ``mzt compile`` is not
registered (handled by the try/except guard in ``cli/__init__.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
import yaml

from marianne.core.logging import get_logger

from ..output import console

_logger = get_logger("cli.compile")


def _configured_workspace(
    config_data: dict[str, Any],
    *,
    base_dir: Path,
) -> str:
    """Return configured workspace from either supported compiler schema."""
    project = config_data.get("project", {})
    raw_workspace = None
    if isinstance(project, dict):
        raw_workspace = project.get("workspace")
    raw_workspace = raw_workspace or config_data.get("workspace")
    if not raw_workspace:
        return ""

    workspace = Path(str(raw_workspace)).expanduser()
    if not workspace.is_absolute():
        workspace = (base_dir / workspace).resolve()
    return str(workspace)


def _doctor_available_instruments() -> set[str]:
    """Return instrument names doctor would report as usable."""
    from marianne.cli.commands.doctor import (
        _active_rate_limited_instruments,
        _check_instrument_binary,
        _profile_execution_ready,
    )
    from marianne.instruments.loader import load_all_profiles

    rate_limited = set(_active_rate_limited_instruments())
    available: set[str] = set()
    for profile in load_all_profiles().values():
        if not _profile_execution_ready(profile):
            continue
        if profile.name in rate_limited:
            continue
        if profile.kind == "http":
            available.add(profile.name)
            continue
        ok, _path = _check_instrument_binary(profile)
        if ok:
            available.add(profile.name)
    return available


def _missing_instrument_tiers(config_data: dict[str, Any]) -> list[str]:
    """Return default instrument tiers that lost their primary."""
    instruments = config_data.get("defaults", {}).get("instruments", {})
    if not isinstance(instruments, dict):
        return []
    missing: list[str] = []
    for tier, tier_config in instruments.items():
        if isinstance(tier_config, dict) and not tier_config.get("primary"):
            missing.append(str(tier))
    return missing


def _set_pause_before_chain(config_data: dict[str, Any]) -> None:
    """Request a chain hold on compiler-generated self-chain hooks."""
    defaults = config_data.setdefault("defaults", {})
    if isinstance(defaults, dict):
        defaults["pause_before_chain"] = True


def _set_job_name_prefix(config_data: dict[str, Any], prefix: str) -> None:
    """Set a generated score/job filename prefix in compiler defaults."""
    defaults = config_data.setdefault("defaults", {})
    if isinstance(defaults, dict):
        defaults["job_name_prefix"] = prefix


def compile_scores(
    config: Path | None = typer.Argument(
        None,
        help="Path to the compiler config YAML file.",
    ),
    preset: str | None = typer.Option(
        None,
        "--preset",
        help="Built-in compiler preset to use, such as 'generic-fleet'.",
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        help=(
            "Workspace for a built-in preset. Defaults to "
            ".marianne/workspaces/<preset> under the current directory."
        ),
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory for generated score files. "
        "Defaults to <workspace>/scores when workspace is configured, "
        "otherwise scores/ next to the config file.",
    ),
    agents_dir: Path | None = typer.Option(
        None,
        "--agents-dir",
        help="Directory for agent identity stores. "
        "Defaults to ~/.marianne/agents/.",
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
    pause_before_chain: bool = typer.Option(
        False,
        "--pause-before-chain",
        help=(
            "Emit generated on_success hooks that pause before starting the "
            "next self-chain cycle."
        ),
    ),
    job_prefix: str | None = typer.Option(
        None,
        "--job-prefix",
        help=(
            "Prefix generated score filenames and conductor job IDs, while "
            "leaving agent identity names unchanged."
        ),
    ),
) -> None:
    """Compile semantic agent definitions into Marianne scores.

    Reads a YAML config that defines agents as people (voice, focus,
    techniques, instruments) and produces complete Marianne score YAML
    for each agent, plus identity directories and fleet configs.
    """
    from marianne_compiler.fleet import FleetGenerator
    from marianne_compiler.instruments import filter_config_to_available_instruments
    from marianne_compiler.pipeline import CompilationPipeline
    from marianne_compiler.presets import load_builtin_preset, prepare_builtin_preset

    # Load and validate config
    if preset:
        try:
            config_data = prepare_builtin_preset(
                load_builtin_preset(preset),
                name=preset,
                cwd=Path.cwd(),
                workspace=workspace,
            )
            if preset == "generic-fleet":
                available_instruments = _doctor_available_instruments()
                config_data = filter_config_to_available_instruments(
                    config_data,
                    available_instruments,
                )
                missing_tiers = _missing_instrument_tiers(config_data)
                if missing_tiers:
                    missing = ", ".join(sorted(missing_tiers))
                    raise ValueError(
                        "No doctor-available instrument remains for tiers: "
                        f"{missing}"
                    )
        except Exception as e:
            _logger.error("compile_preset_error", preset=preset, error=str(e))
            console.print(f"[red]Error:[/red] Cannot load preset '{preset}': {e}")
            raise typer.Exit(code=1) from None
        config_base = Path.cwd()
        config_name = preset
    else:
        if config is None:
            console.print("[red]Error:[/red] Provide a config path or --preset.")
            raise typer.Exit(code=1)
        if not config.exists() or not config.is_file():
            _logger.error("compile_read_error", path=str(config), error="missing")
            console.print(f"[red]Error:[/red] Cannot read {config}")
            raise typer.Exit(code=1)
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
        config_base = config.parent
        config_name = config.stem

    if pause_before_chain:
        _set_pause_before_chain(config_data)
    if job_prefix:
        _set_job_name_prefix(config_data, job_prefix)

    agents = config_data.get("agents", [])
    if not agents:
        console.print("[red]Error:[/red] Config must contain at least one agent.")
        raise typer.Exit(code=1)

    project = config_data.get("project", {})
    project_name = project.get("name", config_name)
    configured_workspace = _configured_workspace(config_data, base_dir=config_base)
    default_output = (
        Path(configured_workspace) / "scores"
        if configured_workspace
        else config_base / "scores"
    )

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
        console.print(f"  Output: {output or default_output}")
        fleet_label = "yes" if fleet or len(agents) > 1 else "no"
        console.print(f"  Fleet: {fleet_label}")
        raise typer.Exit(code=0)

    # Resolve directories. Workspace-local scores can use portable
    # {workspace}/... self-chain hooks; fall back to config-local scores
    # only when no workspace is configured.
    output_dir = output or default_output
    resolved_agents_dir = agents_dir or Path.home() / ".marianne" / "agents"

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
        score_paths = pipeline.compile_config(config_data, output_dir, base_dir=config_base)
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
