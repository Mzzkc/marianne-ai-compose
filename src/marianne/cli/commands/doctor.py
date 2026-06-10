"""``mzt doctor`` — environment health check.

Inspired by ``flutter doctor``. Checks Python version, Marianne version,
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

from marianne import __version__
from marianne.cli.output import console as default_console
from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.loader import load_all_profiles


def _check_conductor_status() -> tuple[str, int | None]:
    """Check if the Marianne conductor is running.

    Two-phase detection for reliability (F-090):
    1. PID file check — fast, works offline
    2. IPC socket probe — authoritative, catches missing PID file

    When --conductor-clone is active, checks the clone's paths instead.

    Returns:
        Tuple of (status_string, pid_or_none).
        status_string is one of: "running", "not running".
    """
    import asyncio

    from marianne.daemon.detect import _resolve_socket_path

    # Phase 1: PID file check
    pid = _check_pid_file()

    if pid is not None:
        return ("running", pid)

    # Phase 2: IPC socket probe — catches cases where PID file is
    # missing but the conductor is running (F-090: doctor/status disagree)
    socket_path = _resolve_socket_path(None)
    if socket_path.exists():
        try:
            from marianne.daemon.ipc.client import DaemonClient

            client = DaemonClient(socket_path)
            alive = asyncio.run(client.is_daemon_running())
            if alive:
                return ("running", None)
        except Exception:
            pass

    return ("not running", None)


def _check_pid_file() -> int | None:
    """Check PID file for a running conductor process.

    When --conductor-clone is active, checks the clone's PID file.

    Returns:
        PID if found and alive, None otherwise.
    """
    from marianne.daemon.clone import get_clone_name, is_clone_active

    if is_clone_active():
        from marianne.daemon.clone import resolve_clone_paths

        pid_file = resolve_clone_paths(get_clone_name()).pid_file
    else:
        from marianne.daemon.config import DaemonConfig

        pid_file = DaemonConfig().pid_file

    if not pid_file.exists():
        return None

    try:
        pid_text = pid_file.read_text().strip()
        pid = int(pid_text)
    except (ValueError, OSError):
        return None

    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:
        return None
    except PermissionError:
        # Process exists but we can't signal it — still running
        return pid


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


def _get_all_profiles() -> dict[str, InstrumentProfile]:
    """Get all instrument profiles from all sources.

    Delegates to the shared ``load_all_profiles()`` in the loader module.
    """
    return load_all_profiles()


def _dir_size_bytes(path: Path) -> int:
    """Total size of a directory tree (best-effort; 0 on errors)."""
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


# Stale one-off backup patterns under ~/.marianne (#352): created by
# ad-hoc maintenance (pre-vacuum/pre-fix copies) and never reaped.
_BACKUP_GLOBS = ("*.bak", "*.pre-*")
_BACKUP_STALE_SECONDS = 24 * 3600


def _scan_storage_findings(marianne_dir: Path | None = None) -> list[dict[str, Any]]:
    """Scan ~/.marianne for storage-hygiene findings (#352).

    Pure read — returns findings; deletion happens only via
    ``mzt doctor --clean`` after explicit confirmation.
    """
    base = marianne_dir or (Path.home() / ".marianne")
    findings: list[dict[str, Any]] = []

    # 1. Dead nested tree — the historical working-directory bug resolved
    # "~/.marianne" against a cwd of ~/.marianne, duplicating the whole
    # state tree. Anything in there is from a fixed bug era.
    nested = base / ".marianne"
    if nested.is_dir():
        findings.append({
            "kind": "dead_nested_tree",
            "path": str(nested),
            "size_bytes": _dir_size_bytes(nested),
            "cleanable": True,
            "detail": "nested state tree from the fixed working-directory "
            "bug — dead data",
        })

    # 2. Stale one-off backups (>24h old).
    import time as _time

    cutoff = _time.time() - _BACKUP_STALE_SECONDS
    for pattern in _BACKUP_GLOBS:
        try:
            matches = sorted(base.glob(pattern))
        except OSError:
            continue
        for f in matches:
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    findings.append({
                        "kind": "stale_backup",
                        "path": str(f),
                        "size_bytes": f.stat().st_size,
                        "cleanable": True,
                        "detail": "one-off backup file older than 24h",
                    })
            except OSError:
                continue

    return findings


def _clean_storage_findings(findings: list[dict[str, Any]]) -> list[str]:
    """Delete cleanable findings. Caller MUST have confirmed first."""
    removed: list[str] = []
    for f in findings:
        if not f.get("cleanable"):
            continue
        path = Path(f["path"])
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            removed.append(f["path"])
        except OSError:
            continue
    return removed


def doctor(
    json: bool = typer.Option(False, "--json", help="Output results as JSON"),
    clean: bool = typer.Option(
        False,
        "--clean",
        help="Delete storage-hygiene findings (dead nested state tree, "
        "stale one-off backups) after explicit confirmation (#352).",
    ),
) -> None:
    """Check Marianne environment health.

    Validates Python version, Marianne installation, conductor status,
    available instruments, safety configuration, and storage hygiene.
    Use this after installation to verify everything is set up correctly.
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

    # --- Marianne version ---
    checks.append({
        "name": "Marianne",
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
        "hint": "Start with: mzt start" if not conductor_ok else None,
    })
    if not conductor_ok:
        warnings.append("Conductor not running")

    # --- Instruments ---
    all_profiles = _get_all_profiles()
    instrument_results: list[dict[str, Any]] = []
    ready_count = 0

    for profile in all_profiles.values():
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

    # --- Storage hygiene (#352) ---
    storage_findings = _scan_storage_findings()
    cleaned_paths: list[str] = []
    if clean and storage_findings:
        total = sum(f["size_bytes"] for f in storage_findings)
        out.print()
        out.print("[bold]Storage cleanup candidates:[/bold]")
        for f in storage_findings:
            out.print(
                f"  - {f['path']} ({_human_size(f['size_bytes'])}) — {f['detail']}"
            )
        if typer.confirm(
            f"Delete the above ({_human_size(total)} total)?", default=False
        ):
            cleaned_paths = _clean_storage_findings(storage_findings)
            out.print(f"[green]Removed {len(cleaned_paths)} item(s).[/green]")
            storage_findings = _scan_storage_findings()
        else:
            out.print("Aborted — nothing deleted.")

    # --- JSON output ---
    if json:
        result: dict[str, Any] = {
            "python_version": py_version,
            "marianne_version": __version__,
            "conductor": {
                "status": conductor_status,
                "pid": conductor_pid,
            },
            "instruments": instrument_results,
            "safety_warnings": safety_warnings,
            "storage_findings": storage_findings,
            "cleaned_paths": cleaned_paths,
            "warnings_count": len(warnings) + len(safety_warnings)
            + len(storage_findings),
            "errors_count": len(errors),
        }
        out.print_json(json_mod.dumps(result))
        return

    # --- Rich output ---
    out.print()
    out.print("[bold]Marianne Doctor[/bold]")
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

    # Storage hygiene section (#352)
    if storage_findings:
        out.print()
        out.print("  [bold]Storage:[/bold]")
        for f in storage_findings:
            out.print(
                f"  [yellow]![/yellow] {f['path']} "
                f"({_human_size(f['size_bytes'])}) — {f['detail']}"
            )
            warnings.append(f"storage: {f['path']}")
        out.print("    [dim]Reclaim with: mzt doctor --clean[/dim]")

    # Summary
    out.print()
    total_warnings = len(warnings)
    total_errors = len(errors)

    if total_errors > 0:
        out.print(
            f"[red]{total_errors} error(s), {total_warnings} warning(s). "
            f"Marianne is not ready.[/red]"
        )
    elif total_warnings > 0:
        out.print(
            f"[yellow]{total_warnings} warning(s).[/yellow] Marianne is ready."
        )
    else:
        out.print("[green]No issues found. Marianne is ready.[/green]")

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
