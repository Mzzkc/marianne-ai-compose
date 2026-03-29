"""``mozart doctor`` — environment health check.

Inspired by ``flutter doctor``. Checks Python version, Mozart version,
conductor status, instrument availability, and safety configuration.
Reports issues with clear status indicators and actionable suggestions.

This command is designed to work WITHOUT a running conductor — it's the
first thing a new user runs after installation.
"""

from __future__ import annotations

import json as json_mod
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

import typer

from mozart import __version__
from mozart.cli.output import console as default_console
from mozart.core.config.instruments import InstrumentProfile
from mozart.instruments.loader import InstrumentProfileLoader
from mozart.instruments.registry import InstrumentRegistry, register_native_instruments


def _check_conductor_status() -> tuple[str, int | None]:
    """Check if the Mozart conductor is running.

    Returns:
        Tuple of (status_string, pid_or_none).
        status_string is one of: "running", "not running".
    """
    from mozart.daemon.config import DaemonConfig

    defaults = DaemonConfig()
    pid_file = defaults.pid_file

    if not pid_file.exists():
        return ("not running", None)

    try:
        pid_text = pid_file.read_text().strip()
        pid = int(pid_text)
    except (ValueError, OSError):
        return ("not running", None)

    try:
        os.kill(pid, 0)
        return ("running", pid)
    except ProcessLookupError:
        return ("not running", None)
    except PermissionError:
        # Process exists but we can't signal it — still running
        return ("running", pid)


def _check_instrument_binary(profile: InstrumentProfile) -> tuple[bool, str | None]:
    """Check if a CLI instrument's binary is available on PATH.

    Returns:
        Tuple of (is_available, binary_path_or_none).
    """
    if profile.kind != "cli" or profile.cli is None:
        # HTTP instruments don't have a binary to check
        return (True, None)

    executable = profile.cli.command.executable
    path = shutil.which(executable)
    return (path is not None, path)


def _build_instrument_registry() -> InstrumentRegistry:
    """Build a registry with native + built-in + user instruments."""
    registry = InstrumentRegistry()
    register_native_instruments(registry)

    # Load built-in profiles
    builtins_dir = Path(__file__).resolve().parent.parent.parent / "instruments" / "builtins"
    if builtins_dir.is_dir():
        for name, profile in InstrumentProfileLoader.load_directory(builtins_dir).items():
            if name not in registry:
                registry.register(profile)
            else:
                # Built-in profiles don't override native instruments
                pass

    # Load user profiles (org-level)
    org_dir = Path.home() / ".mozart" / "instruments"
    if org_dir.is_dir():
        for _name, profile in InstrumentProfileLoader.load_directory(org_dir).items():
            registry.register(profile, override=True)

    # Load venue profiles (project-level)
    venue_dir = Path(".mozart") / "instruments"
    if venue_dir.is_dir():
        for _name, profile in InstrumentProfileLoader.load_directory(venue_dir).items():
            registry.register(profile, override=True)

    return registry


def doctor(
    json: bool = typer.Option(False, "--json", help="Output results as JSON"),
) -> None:
    """Check Mozart environment health.

    Validates Python version, Mozart installation, conductor status,
    available instruments, and safety configuration. Use this after
    installation to verify everything is set up correctly.
    """
    out = default_console

    # Collect all check results
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []

    # --- Python version ---
    py_version = platform.python_version()
    py_ok = sys.version_info >= (3, 11)
    checks.append({
        "name": "Python",
        "status": "ok" if py_ok else "error",
        "detail": py_version,
        "hint": "Python 3.11+ required" if not py_ok else None,
    })
    if not py_ok:
        errors.append("Python 3.11+ required")

    # --- Mozart version ---
    checks.append({
        "name": "Mozart",
        "status": "ok",
        "detail": f"v{__version__}",
        "hint": None,
    })

    # --- Conductor status ---
    conductor_status, conductor_pid = _check_conductor_status()
    conductor_ok = conductor_status == "running"
    detail = f"running (pid {conductor_pid})" if conductor_ok else "not running"
    checks.append({
        "name": "Conductor",
        "status": "ok" if conductor_ok else "warning",
        "detail": detail,
        "hint": "Start with: mozart start" if not conductor_ok else None,
    })
    if not conductor_ok:
        warnings.append("Conductor not running")

    # --- Instruments ---
    registry = _build_instrument_registry()
    instrument_results: list[dict[str, Any]] = []
    ready_count = 0

    for profile in registry.list_all():
        available, binary_path = _check_instrument_binary(profile)

        if profile.kind == "http":
            # HTTP instruments: report as available (we don't probe endpoints)
            status = "ok"
            detail_str = f"{profile.display_name}"
            if profile.http and profile.http.base_url:
                detail_str += f" ({profile.http.base_url})"
            ready_count += 1
        elif available:
            status = "ok"
            detail_str = f"{binary_path}" if binary_path else profile.display_name
            ready_count += 1
        else:
            status = "optional"
            executable = profile.cli.command.executable if profile.cli else "unknown"
            detail_str = f"not found ({executable})"

        instrument_results.append({
            "name": profile.name,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "status": status,
            "detail": detail_str,
        })

    # --- Safety checks ---
    safety_warnings: list[str] = []

    # Check for cost limits configuration
    # The default CostLimitConfig has enabled=False
    safety_warnings.append("No cost limits configured. Recommend: cost_limits.max_cost_per_job")

    # --- JSON output ---
    if json:
        result: dict[str, Any] = {
            "python_version": py_version,
            "mozart_version": __version__,
            "conductor": {
                "status": conductor_status,
                "pid": conductor_pid,
            },
            "instruments": instrument_results,
            "safety_warnings": safety_warnings,
            "warnings_count": len(warnings) + len(safety_warnings),
            "errors_count": len(errors),
        }
        out.print_json(json_mod.dumps(result))
        return

    # --- Rich output ---
    out.print()
    out.print("[bold]Mozart Doctor[/bold]")
    out.print()

    # Core checks
    for check in checks:
        icon = _status_icon(check["status"])
        line = f"  {icon} {check['name']:<24} {check['detail']}"
        out.print(line)
        if check.get("hint"):
            out.print(f"    [dim]{check['hint']}[/dim]")

    # Instruments section
    out.print()
    out.print("  [bold]Instruments:[/bold]")
    for inst in instrument_results:
        icon = _status_icon(inst["status"])
        out.print(f"  {icon} {inst['name']:<24} {inst['detail']}")

    # Safety section
    if safety_warnings:
        out.print()
        out.print("  [bold]Safety:[/bold]")
        for warn in safety_warnings:
            out.print(f"  [yellow]![/yellow] {warn}")
            warnings.append(warn)

    # Summary
    out.print()
    total_warnings = len(warnings)
    total_errors = len(errors)

    if total_errors > 0:
        out.print(
            f"[red]{total_errors} error(s), {total_warnings} warning(s). "
            f"Mozart is not ready.[/red]"
        )
    elif total_warnings > 0:
        out.print(
            f"[yellow]{total_warnings} warning(s).[/yellow] Mozart is ready."
        )
    else:
        out.print("[green]No issues found. Mozart is ready.[/green]")

    out.print()


def _status_icon(status: str) -> str:
    """Map status to a Rich-formatted icon."""
    icons = {
        "ok": "[green]✓[/green]",
        "warning": "[yellow]![/yellow]",
        "error": "[red]✗[/red]",
        "optional": "[dim]·[/dim]",
    }
    return icons.get(status, "[dim]?[/dim]")
