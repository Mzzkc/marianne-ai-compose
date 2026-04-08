"""Instrument management commands — ``mzt instruments list|check``.

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

import typer
from rich.table import Table

from marianne.cli.output import console, output_error, output_json
from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.loader import load_all_profiles

# ---------------------------------------------------------------------------
# Typer app for ``mzt instruments`` subcommand
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

    Delegates to the shared ``load_all_profiles()`` in the loader module.
    """
    return load_all_profiles()


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
                "Add instrument profiles to ~/.marianne/instruments/ "
                "or .marianne/instruments/"
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
    unchecked_count = 0

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
            # HTTP instruments — connectivity not checked at list time
            status_str = "[dim]? unchecked[/dim]"
            unchecked_count += 1

        model_str = profile.default_model or "(instrument default)"

        table.add_row(name, profile.kind, status_str, model_str)

    console.print(table)

    parts = [f"{len(profiles)} instruments configured"]
    if ready_count:
        parts.append(f"{ready_count} ready")
    if unchecked_count:
        parts.append(f"{unchecked_count} unchecked")
    if len(parts) > 1:
        summary = f"{parts[0]} ({', '.join(parts[1:])})"
    else:
        summary = parts[0]
    console.print(f"\n{summary}")


def _list_json(profiles: dict[str, InstrumentProfile]) -> None:
    """Render the instruments list as JSON."""
    result = []
    for name in sorted(profiles.keys()):
        profile = profiles[name]
        found, binary_path = _check_binary(profile)

        # HTTP instruments are "unchecked" (no connectivity test at list time)
        if profile.kind == "cli":
            status = "ready" if found else "not_found"
        else:
            status = "unchecked"

        result.append({
            "name": profile.name,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "status": status,
            "ready": found if profile.kind == "cli" else None,
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
            output_json({"error": f"Unknown instrument: {name}"})
        else:
            output_error(
                f"Unknown instrument: '{name}'",
                error_code="INSTR-001",
                hints=[
                    "Run 'mzt instruments list' to see available instruments",
                    "Add profiles to ~/.marianne/instruments/ or .marianne/instruments/",
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
        output_error(
            f"{profile.name} is not ready.",
            hints=[
                f"Check that '{profile.name}' is installed and in your PATH.",
                "Run 'mzt doctor' for a full environment check.",
            ],
        )
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
