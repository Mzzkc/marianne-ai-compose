"""Instrument management commands — ``mozart instruments list|check``.

These commands let users discover available instruments, check their
readiness, and diagnose configuration issues. They build on the
InstrumentProfileLoader and InstrumentRegistry — YAML profiles loaded
from built-in, organization, and venue directories.

The music metaphor: these commands are the instrument workshop's front
desk. ``list`` shows what's on the rack. ``check`` picks one up and
plays a test note.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer
from rich.table import Table

from mozart.cli.output import console, output_error
from mozart.core.config.instruments import InstrumentProfile
from mozart.instruments.loader import InstrumentProfileLoader
from mozart.instruments.registry import InstrumentRegistry, register_native_instruments

# ---------------------------------------------------------------------------
# Typer app for ``mozart instruments`` subcommand
# ---------------------------------------------------------------------------

instruments_app = typer.Typer(
    name="instruments",
    help="Manage and inspect available instruments.",
    invoke_without_command=True,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_all_profiles() -> dict[str, InstrumentProfile]:
    """Load all instrument profiles from all sources.

    Loading order (later overrides earlier):
        1. Native instruments (4 built-in backends)
        2. Built-in YAML profiles (shipped with Mozart)
        3. Organization profiles (~/.mozart/instruments/)
        4. Venue profiles (.mozart/instruments/)
    """
    # Start with native instruments via registry
    registry = InstrumentRegistry()
    register_native_instruments(registry)

    profiles: dict[str, InstrumentProfile] = {
        p.name: p for p in registry.list_all()
    }

    # Layer on YAML profiles from directories
    builtins_dir = Path(__file__).resolve().parent.parent.parent / "instruments" / "builtins"
    org_dir = Path.home() / ".mozart" / "instruments"
    venue_dir = Path(".mozart") / "instruments"

    yaml_profiles = InstrumentProfileLoader.load_directories(
        [builtins_dir, org_dir, venue_dir]
    )

    # YAML profiles override native ones
    profiles.update(yaml_profiles)

    return profiles


def _check_binary(profile: InstrumentProfile) -> tuple[bool, str | None]:
    """Check if a CLI instrument's binary is on PATH.

    Returns:
        (found, path) — True and the resolved path if found, False and None otherwise.
    """
    if profile.kind != "cli" or profile.cli is None:
        return True, None  # non-CLI instruments don't have binaries

    executable = profile.cli.command.executable
    binary_path = shutil.which(executable)
    return binary_path is not None, binary_path


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@instruments_app.callback(invoke_without_command=True)
def instruments_callback(ctx: typer.Context) -> None:
    """Show instrument overview if no subcommand given."""
    if ctx.invoked_subcommand is None:
        # Default to list when called without a subcommand
        ctx.invoke(list_instruments)


@instruments_app.command(name="list")
def list_instruments(
    json_output: bool = typer.Option(
        False, "--json", help="Output as JSON",
    ),
) -> None:
    """List all available instruments and their readiness status."""
    profiles = _load_all_profiles()

    if not profiles:
        if json_output:
            console.print("[]")
        else:
            console.print(
                "[dim]No instruments configured.[/dim]\n"
                "Add instrument profiles to ~/.mozart/instruments/ "
                "or .mozart/instruments/"
            )
        return

    if json_output:
        _list_json(profiles)
    else:
        _list_table(profiles)


def _list_table(profiles: dict[str, InstrumentProfile]) -> None:
    """Render the instruments table with Rich."""
    table = Table(
        title="Instruments",
        show_header=True,
        header_style="bold",
        padding=(0, 2),
    )
    table.add_column("NAME", style="cyan", no_wrap=True)
    table.add_column("KIND", no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("DEFAULT MODEL")

    ready_count = 0

    for name in sorted(profiles.keys()):
        profile = profiles[name]
        found, binary_path = _check_binary(profile)

        if profile.kind == "cli":
            if found:
                status_str = "[green]✓ ready[/green]"
                ready_count += 1
            else:
                status_str = "[red]✗ not found[/red]"
        else:
            # HTTP instruments — show the endpoint as status
            status_str = "[dim]http[/dim]"
            ready_count += 1  # HTTP instruments are always "ready" (connectivity not checked)

        model_str = profile.default_model or "(instrument default)"

        table.add_row(name, profile.kind, status_str, model_str)

    console.print(table)
    console.print(
        f"\n{len(profiles)} instruments configured ({ready_count} ready)"
    )


def _list_json(profiles: dict[str, InstrumentProfile]) -> None:
    """Render the instruments list as JSON."""
    result = []
    for name in sorted(profiles.keys()):
        profile = profiles[name]
        found, binary_path = _check_binary(profile)

        result.append({
            "name": profile.name,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "ready": found,
            "binary_path": binary_path,
            "default_model": profile.default_model,
            "capabilities": sorted(profile.capabilities),
        })

    console.print(json.dumps(result, indent=2))


@instruments_app.command(name="check")
def check_instrument(
    name: str = typer.Argument(help="Instrument name to check"),
    json_output: bool = typer.Option(
        False, "--json", help="Output as JSON",
    ),
) -> None:
    """Check readiness and configuration of a specific instrument."""
    profiles = _load_all_profiles()

    if name not in profiles:
        if json_output:
            console.print(json.dumps({"error": f"Unknown instrument: {name}"}))
        else:
            output_error(
                f"Unknown instrument: '{name}'",
                error_code="INSTR-001",
                hints=[
                    "Run 'mozart instruments list' to see available instruments",
                    "Add profiles to ~/.mozart/instruments/ or .mozart/instruments/",
                ],
            )
        raise typer.Exit(1)

    profile = profiles[name]

    if json_output:
        _check_json(profile)
    else:
        _check_rich(profile)


def _check_rich(profile: InstrumentProfile) -> None:
    """Render the instrument check as Rich output."""
    console.print(f"\nChecking [cyan]{profile.name}[/cyan]...")
    console.print(f"  Display name:  {profile.display_name}")
    if profile.description:
        console.print(f"  Description:   {profile.description}")
    console.print(f"  Kind:          {profile.kind}")

    all_ok = True

    if profile.kind == "cli" and profile.cli is not None:
        executable = profile.cli.command.executable
        binary_path = shutil.which(executable)
        if binary_path:
            console.print(f"  Binary:        {binary_path} [green]✓[/green]")
        else:
            console.print(
                f"  Binary:        {executable} [red]✗ not found[/red]"
            )
            all_ok = False
    elif profile.kind == "http" and profile.http is not None:
        console.print(f"  Endpoint:      {profile.http.base_url}{profile.http.endpoint}")
        if profile.http.auth_env_var:
            console.print(f"  Auth env var:  {profile.http.auth_env_var}")

    # Capabilities
    caps = sorted(profile.capabilities) if profile.capabilities else ["(none)"]
    console.print(f"  Capabilities:  {', '.join(caps)}")

    # Model info
    if profile.default_model:
        console.print(f"  Default model: {profile.default_model}")
    if profile.models:
        for model in profile.models:
            cost_str = ""
            if model.cost_per_1k_input > 0 or model.cost_per_1k_output > 0:
                cost_str = (
                    f" (${model.cost_per_1k_input:.4f}/1K in, "
                    f"${model.cost_per_1k_output:.4f}/1K out)"
                )
            console.print(
                f"    {model.name}: {model.context_window:,} ctx{cost_str}"
            )

    console.print()
    if all_ok:
        console.print(f"[green]{profile.name} is ready.[/green]")
    else:
        console.print(f"[red]{profile.name} is not ready.[/red]")
        raise typer.Exit(1)


def _check_json(profile: InstrumentProfile) -> None:
    """Render the instrument check as JSON."""
    found, binary_path = _check_binary(profile)

    result: dict[str, object] = {
        "name": profile.name,
        "display_name": profile.display_name,
        "kind": profile.kind,
        "description": profile.description,
        "capabilities": sorted(profile.capabilities),
        "default_model": profile.default_model,
        "models": [
            {
                "name": m.name,
                "context_window": m.context_window,
                "cost_per_1k_input": m.cost_per_1k_input,
                "cost_per_1k_output": m.cost_per_1k_output,
            }
            for m in profile.models
        ],
    }

    if profile.kind == "cli" and profile.cli is not None:
        result["binary_found"] = found
        result["binary_path"] = binary_path
        result["executable"] = profile.cli.command.executable
    elif profile.kind == "http" and profile.http is not None:
        result["endpoint"] = f"{profile.http.base_url}{profile.http.endpoint}"
        result["auth_env_var"] = profile.http.auth_env_var

    result["ready"] = found

    console.print(json.dumps(result, indent=2))
