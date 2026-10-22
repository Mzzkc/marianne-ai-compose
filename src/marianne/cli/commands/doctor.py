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
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import typer

from marianne import __version__
from marianne.cli.output import console as default_console
from marianne.core.config.instruments import InstrumentProfile
from marianne.core.logging import get_logger
from marianne.daemon.clone import active_registry_db_path
from marianne.instruments.availability import probe_profile
from marianne.instruments.loader import load_all_profiles, profile_source_dirs

_logger = get_logger("cli.doctor")

_DOCTOR_RUNTIME_TIMEOUT_SECONDS = 1.0


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
        from marianne.daemon.exceptions import DaemonUnresponsiveError

        try:
            from marianne.daemon.ipc.client import DaemonClient

            client = DaemonClient(socket_path)
            alive = asyncio.run(client.is_daemon_running())
            if alive:
                return ("running", None)
        except DaemonUnresponsiveError:
            # A socket that accepts but does not answer is a busy conductor,
            # not an absent one (GH #455: absence needs a missing socket or a
            # refused connect). Report it as running so doctor does not tell
            # the operator to start a second one.
            return ("running", None)
        except Exception:  # noqa: BLE001 — any other probe failure: absence is the honest default
            pass

    return ("not running", None)


_CREDENTIAL_NAME = re.compile(
    r"(API_?KEY|_TOKEN|SECRET|PASSWORD|CREDENTIAL|_AUTH\b|SERVICE_ROLE_KEY)", re.IGNORECASE,
)


def _credential_env_names() -> list[str]:
    """Names (never values) of credential-shaped variables in this environment."""
    return sorted(k for k in os.environ if _CREDENTIAL_NAME.search(k))


def _silently_filtered(
    profiles: dict[str, InstrumentProfile], results: list[dict[str, Any]],
) -> list[tuple[str, str, list[str]]]:
    """Ready user/venue CLI profiles that lost ambient credentials under GH #335.

    A profile with neither ``required_env`` nor ``inherit_env`` now passes only
    system essentials. When its executable is one a shipped profile declares
    ``required_env`` for, and any of those variables is set in this
    environment, the instrument will authenticate differently from the shell
    it was tested in — silently, at sheet 1. Returns (profile, executable,
    variables set) rows; the raw shell profile and executables no shipped
    profile knows are skipped.
    """
    from marianne.instruments.loader import InstrumentProfileLoader

    builtins = InstrumentProfileLoader.load_directories([profile_source_dirs()[0]])
    by_executable: dict[str, list[str]] = {}
    for profile in builtins.values():
        if profile.cli is not None and profile.cli.command.required_env:
            by_executable.setdefault(
                profile.cli.command.executable, [],
            ).extend(profile.cli.command.required_env)
    ready = {r["name"] for r in results if r.get("probe") == "ready"}
    out: list[tuple[str, str, list[str]]] = []
    for name, profile in sorted(profiles.items()):
        if name in builtins or name not in ready or profile.cli is None or profile.raw_prompt:
            continue
        command = profile.cli.command
        if command.required_env is not None or command.inherit_env or command.env:
            continue
        executable = command.executable.rsplit("/", 1)[-1]
        known = by_executable.get(executable)
        if not known:
            continue
        present = sorted({k for k in known if k in os.environ})
        if present:
            out.append((name, executable, present))
    return out


def _credential_exposure(
    profiles: dict[str, InstrumentProfile], results: list[dict[str, Any]],
) -> list[str]:
    """Ready CLI profiles that inherit the full parent environment (GH #335).

    The raw shell `cli` profile is skipped: it is the operator's own shell.
    Reported only when at least one credential-shaped variable is set, so a
    clean environment produces no warning.
    """
    if not _credential_env_names():
        return []
    ready = {r["name"] for r in results if r.get("probe") == "ready"}
    out: list[str] = []
    for name, profile in profiles.items():
        if name not in ready or profile.kind != "cli" or profile.cli is None:
            continue
        if profile.raw_prompt:
            continue
        if profile.cli.command.inherit_env and profile.cli.command.required_env is None:
            out.append(name)
    return out


def _last_conductor_reload() -> dict[str, Any] | None:
    """The running conductor's last reload outcome, or None when unreachable."""
    import asyncio

    from marianne.daemon.detect import _resolve_socket_path

    socket_path = _resolve_socket_path(None)
    if not socket_path.exists():
        return None
    try:
        from marianne.daemon.ipc.client import DaemonClient

        async def _fetch() -> dict[str, Any] | None:
            async with DaemonClient(socket_path) as client:
                status = await client.call("daemon.status")
            last = status.get("last_config_reload") if isinstance(status, dict) else None
            return last if isinstance(last, dict) else None

        return asyncio.run(_fetch())
    except Exception:  # noqa: BLE001 — doctor remains usable without a reachable conductor
        _logger.debug("doctor.reload_status_unavailable", exc_info=True)
        return None


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




async def _fetch_active_rate_limited_instruments() -> dict[str, float | None]:
    """Return conductor-known active rate limits by instrument name.

    This is the runtime-health complement to binary availability. It is
    intentionally best-effort: ``mzt doctor`` must still work when the
    conductor is down, stale, or mid-restart.
    """
    from marianne.daemon.detect import _resolve_socket_path
    from marianne.daemon.ipc.client import DaemonClient

    socket_path = _resolve_socket_path(None)
    if not socket_path.exists():
        return {}

    async with DaemonClient(
        socket_path,
        timeout=_DOCTOR_RUNTIME_TIMEOUT_SECONDS,
        pool_size=1,
    ) as client:
        result = await client.rate_limits()

    raw_backends = result.get("backends", {})
    if not isinstance(raw_backends, dict):
        return {}

    active: dict[str, float | None] = {}
    for name, raw in raw_backends.items():
        seconds: float | None = None
        is_active = True
        if isinstance(raw, dict):
            is_active = bool(raw.get("active", True))
            value = raw.get("seconds_remaining", raw.get("remaining_seconds"))
            if isinstance(value, int | float):
                seconds = float(value)
        elif isinstance(raw, int | float):
            seconds = float(raw)
        if is_active and (seconds is None or seconds > 0):
            active[str(name)] = seconds
    return active


def _active_rate_limited_instruments() -> dict[str, float | None]:
    """Synchronous wrapper used by ``mzt doctor`` and preset compilation."""
    import asyncio

    try:
        return asyncio.run(_fetch_active_rate_limited_instruments())
    except Exception:  # noqa: BLE001 — runtime health is optional when the conductor is down
        _logger.debug("doctor.rate_limits_unavailable", exc_info=True)
        return {}


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


def _format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "(active)"
    remaining = max(int(seconds), 0)
    mins, secs = divmod(remaining, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"(clears in {hours}h {mins}m)"
    if mins:
        return f"(clears in {mins}m {secs}s)"
    return f"(clears in {secs}s)"


_GOOGLE_AUTH_ENV_VARS = (
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "CLOUDSDK_CONFIG",
)


def _profile_execution_state(profile: InstrumentProfile) -> tuple[str, str | None]:
    """Return computed prompt execution readiness for an instrument profile."""
    if profile.execution_status != "ready":
        return profile.execution_status, profile.execution_status_detail
    if (
        profile.kind == "cli"
        and profile.cli is not None
        and profile.cli.command.executable == "gemini"
        and not any(os.environ.get(name) for name in _GOOGLE_AUTH_ENV_VARS)
    ):
        env_list = ", ".join(_GOOGLE_AUTH_ENV_VARS)
        return (
            "unsupported",
            "Gemini CLI prompt execution is unsupported for the default "
            "individual-tier OAuth path here; set one of "
            f"{env_list} and live-smoke before using this profile in fleets.",
        )
    return profile.execution_status, profile.execution_status_detail


def _profile_execution_ready(profile: InstrumentProfile) -> bool:
    """Whether this profile should be auto-selected for prompt execution."""
    status, _detail = _profile_execution_state(profile)
    return status != "unsupported"


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

    # 3. Legacy clone state DBs (+ -wal/-shm sidecars). Conductor clones
    # write under the runtime dir (~/.config/mzt) since #227; clone-*
    # files left in ~/.marianne predate the move (#191).
    try:
        clone_matches = sorted(base.glob("clone-*"))
    except OSError:
        clone_matches = []
    for f in clone_matches:
        try:
            if f.is_file():
                findings.append({
                    "kind": "stale_clone_artifact",
                    "path": str(f),
                    "size_bytes": f.stat().st_size,
                    "cleanable": True,
                    "detail": "legacy clone state file — clones write to "
                    "the runtime dir since #227",
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


def _names_in_document(node: Any, found: set[str]) -> None:
    """Collect every string scalar in a parsed score document.

    A profile counts as referenced if its name appears anywhere a score
    could name it (``instrument``, fallbacks, per-sheet maps, alias
    ``profile``, class chains). Taking every string over-approximates,
    which errs toward NOT reporting a profile: a false "unreferenced" is
    the costly mistake here, not a missed one.
    """
    if isinstance(node, str):
        found.add(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                found.add(key)
            _names_in_document(value, found)
    elif isinstance(node, list):
        for item in node:
            _names_in_document(item, found)


def _referenced_instrument_names(db_path: Path) -> set[str] | None:
    """Names every score the conductor has been given could resolve.

    Reads the registry's ``config_path`` column (read-only, offline) and
    parses each score file that still exists, plus the class maps every
    layer loads. Returns ``None`` when there is no registry yet, so the
    caller can skip the check instead of reporting every profile.
    """
    import sqlite3

    import yaml

    from marianne.instruments.classes import load_class_map

    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT DISTINCT config_path FROM jobs").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    names: set[str] = set()
    # A long-lived conductor has been given every score its operator ever
    # ran (1,374 files on one host): the pure-Python loader took 41 s here
    # and timed out every test that invokes doctor. The C loader is 3 s.
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    for (config_path,) in rows:
        path = Path(str(config_path)).expanduser()
        if not path.is_file():
            continue  # score deleted since; it can no longer reference anything
        try:
            document = yaml.load(  # noqa: S506 - SafeLoader variant
                path.read_text(encoding="utf-8"), Loader=loader,
            )
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        _names_in_document(document, names)
    for entry in load_class_map().classes.values():
        for link in entry.chain:
            names.add(link.profile)
    return names


def _scan_unreferenced_user_profiles(
    *,
    organization_dir: Path | None = None,
    venue_dir: Path | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Report user- and venue-layer profiles no submitted score names (GH #443).

    Generated research seats and one-off agents accumulate in
    ``~/.marianne/instruments/`` until ``mzt instruments list`` is unreadable
    (148 profiles on one machine). Never cleanable: a profile is archived by
    a person, not deleted by ``doctor --clean``. A user-layer file that
    overrides a built-in name is always referenced, because every score
    that names nothing resolves to a built-in.
    """
    from marianne.core.constants import DEFAULT_INSTRUMENT_NAME
    from marianne.instruments.loader import InstrumentProfileLoader

    builtins_dir, org_dir, venue = profile_source_dirs(
        organization_dir=organization_dir, venue_dir=venue_dir,
    )
    # Clone-aware (GH #401): the registry of the conductor the CLI addresses.
    registry = db_path if db_path is not None else active_registry_db_path()
    referenced = _referenced_instrument_names(registry)
    if referenced is None:
        return []
    builtin_names = set(InstrumentProfileLoader.load_directory(builtins_dir))
    referenced |= builtin_names | {DEFAULT_INSTRUMENT_NAME}
    findings: list[dict[str, Any]] = []
    for layer, directory in (("user", org_dir), ("venue", venue)):
        for name, profile in sorted(InstrumentProfileLoader.load_directory(directory).items()):
            if name in referenced:
                continue
            source = profile._source_path or (directory / f"{name}.yaml")
            try:
                size = source.stat().st_size
            except OSError:
                size = 0
            findings.append({
                "kind": "unreferenced_profile",
                "layer": layer,
                "name": name,
                "path": str(source),
                "size_bytes": size,
                "cleanable": False,
                "detail": "no score the conductor has been given names this "
                "profile and no class chain includes it; a seat is an alias "
                "over an existing profile (GH #443) — archive the file, or "
                "submit the score that needs it",
            })
    return findings


def doctor(
    json: bool = typer.Option(False, "--json", help="Output results as JSON"),
    clean: bool = typer.Option(
        False,
        "--clean",
        help="Delete storage-hygiene findings (dead nested state tree, "
        "stale one-off backups) after explicit confirmation (#352).",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Also run one real prompt through every ready agent instrument "
        "(and one schema-mode prompt where the profile declares "
        "json_schema_flag), the same check as `mzt instruments check --live`. "
        "Proves each profile against its installed CLI; spends model calls.",
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

    # --- Last hot-reload (GH #450) ---
    # A profile that failed to load on reload keeps its PREVIOUS entry live,
    # so `doctor` reading the files on disk would call it ready while the
    # conductor runs older bytes. Ask the conductor what it actually kept.
    last_reload = _last_conductor_reload() if conductor_ok else None
    reload_findings: list[str] = []
    if last_reload:
        for failed in last_reload.get("failed_profiles") or []:
            retained = failed.get("retained") or ""
            keep = f" (previous entry '{retained}' retained)" if retained else ""
            reload_findings.append(
                f"Reload declined {failed.get('path')}: {failed.get('reason_code')}{keep}"
            )
        if last_reload.get("schema_stale"):
            reload_findings.append(
                "Conductor predates a profile's schema: restart at a job-free "
                "boundary to load the file as written"
            )
            checks.append({
                "name": "Conductor schema",
                "status": "warning",
                "detail": "older than a profile on disk",
                "hint": "Restart when no jobs run: mzt stop && mzt start",
            })
    warnings.extend(reload_findings)

    # --- Instruments ---
    all_profiles = _get_all_profiles()
    active_rate_limits = (
        _active_rate_limited_instruments() if conductor_ok else {}
    )
    instrument_results: list[dict[str, Any]] = []
    ready_count = 0

    for profile in all_profiles.values():
        # One probe for every command that answers "can this run here"
        # (GH #447). Its four states have one definition in availability.py.
        probe = probe_profile(profile)
        is_rate_limited = profile.name in active_rate_limits
        limit_seconds = active_rate_limits.get(profile.name)
        execution_status, execution_note = _profile_execution_state(profile)
        execution_ready = execution_status != "unsupported"

        if probe.state == "ready":
            status = "warning" if is_rate_limited or not execution_ready else "ok"
            detail_str = probe.binary_path or profile.display_name
            if profile.kind == "http" and profile.http and profile.http.base_url:
                detail_str += f" ({profile.http.base_url})"
            if not execution_ready:
                detail_str += (
                    f" — {execution_status}"
                    f"{(': ' + execution_note) if execution_note else ''}"
                )
                warnings.append(f"Instrument {profile.name} is {execution_status}")
            if is_rate_limited:
                detail_str += f" — rate-limited {_format_seconds(limit_seconds)}"
                warnings.append(f"Instrument {profile.name} is rate-limited")
            if not is_rate_limited and execution_ready and not profile.raw_prompt:
                # A raw shell profile (`cli`) always "runs"; it is not an agent
                # and must not make a fresh install read as ready.
                ready_count += 1
        elif probe.state == "unchecked":
            # A remote endpoint is never probed; say so instead of "ok".
            status = "unchecked"
            detail_str = f"{profile.display_name} — {probe.reason}"
        elif probe.state == "unreachable":
            status = "optional"
            detail_str = f"not answering ({probe.reason})"
        else:
            status = "optional"
            detail_str = f"not found ({probe.reason})"

        instrument_results.append({
            "name": profile.name,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "status": status,
            "probe": probe.state,
            "detail": detail_str,
            "execution_status": execution_status,
            "execution_status_detail": execution_note,
            "execution_ready": execution_ready,
            "rate_limited": is_rate_limited,
            "rate_limit_seconds_remaining": limit_seconds,
        })

    if live:
        from marianne.cli.commands.instruments import live_failure, live_probe

        live_ready = 0
        for row in instrument_results:
            if row["probe"] != "ready" or not row["execution_ready"]:
                continue
            profile = all_profiles[row["name"]]
            if profile.raw_prompt:
                continue
            report = live_probe(profile, timeout=150.0)
            row["live"] = report
            if report["ok"]:
                live_ready += 1
                continue
            row["status"] = "warning"
            mode, reason = live_failure(report) or ("plain", "unknown")
            row["detail"] = f"{row['detail']} — live {mode} probe failed: {reason}"
            warnings.append(f"Instrument {profile.name} failed its live {mode} probe ({reason})")
        if ready_count and live_ready == 0:
            errors.append(
                "No instrument passed its live probe: every ready instrument failed "
                "a real prompt. Run `mzt instruments check <name> --live` for detail."
            )

    # "Marianne is ready" means a score can run: at least one agent instrument
    # is runnable now. Zero runnable instruments is an error, not a warning —
    # `doctor` used to say "ready" on a fresh install with none (GH #447).
    if ready_count == 0:
        errors.append(
            "No runnable instrument: nothing on PATH and no local server serving "
            "its model. Install one agent CLI (e.g. claude-code, codex-cli, "
            "opencode) or pull the local model `mzt instruments check ollama` names."
        )

    # --- Safety checks ---
    safety_warnings: list[str] = []

    # Check for cost limits configuration
    # The default CostLimitConfig has enabled=False
    safety_warnings.append("No cost limits configured. Recommend: cost_limits.max_cost_per_job")

    # GH #335: an explicit `inherit_env` opt-out without an allowlist hands the
    # instrument the conductor's whole environment, every credential included.
    # Name each ready one together with how many credential-shaped variables
    # are set right now, so the exposure is visible where the operator looks.
    exposed = _credential_exposure(all_profiles, instrument_results)
    if exposed:
        names = ", ".join(sorted(exposed))
        count = len(_credential_env_names())
        safety_warnings.append(
            f"{len(exposed)} ready CLI instrument(s) inherit the full environment "
            f"({count} credential-shaped variable(s) set): {names}. Declare "
            "cli.command.required_env or remove inherit_env to pass only what it needs (GH #335)."
        )
    # GH #335 migration: a user profile that never declared an env policy
    # used to inherit the shell's credentials and now gets none. Name it
    # before a sheet fails on authentication.
    for name, executable, present in _silently_filtered(all_profiles, instrument_results):
        safety_warnings.append(
            f"{name} runs `{executable}` with no cli.command.required_env, so the "
            f"{', '.join(present)} set in this environment no longer reach it "
            "(filtered by default since GH #335). Declare required_env with the "
            "variables it authenticates by, or required_env: [] if it reads its own store."
        )

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

    # --- Instrument hygiene (GH #443) ---
    unreferenced_profiles = _scan_unreferenced_user_profiles()

    # --- JSON output ---
    if json:
        result: dict[str, Any] = {
            "python_version": py_version,
            "marianne_version": __version__,
            "conductor": {
                "status": conductor_status,
                "pid": conductor_pid,
                "last_reload": last_reload,
            },
            "instruments": instrument_results,
            "safety_warnings": safety_warnings,
            "storage_findings": storage_findings,
            "unreferenced_profiles": unreferenced_profiles,
            "cleaned_paths": cleaned_paths,
            "warnings_count": len(warnings) + len(safety_warnings)
            + len(storage_findings) + len(unreferenced_profiles),
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

    # Last hot-reload section (GH #450)
    if reload_findings:
        out.print()
        out.print("  [bold]Config reload:[/bold]")
        for finding in reload_findings:
            out.print(f"  [yellow]![/yellow] {finding}")

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

    # Instrument hygiene section (GH #443): reported, never auto-deleted.
    if unreferenced_profiles:
        out.print()
        out.print(
            f"  [bold]Instrument hygiene:[/bold] {len(unreferenced_profiles)} "
            f"profile(s) no submitted score names"
        )
        for f in unreferenced_profiles:
            out.print(f"  [yellow]![/yellow] {f['name']:<24} {f['path']}")
            warnings.append(f"unreferenced profile: {f['name']}")
        out.print(
            "    [dim]A seat is an alias over an existing profile (GH #443). "
            "Archive these files, or submit the score that needs them.[/dim]"
        )

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
            f"[yellow]{total_warnings} warning(s).[/yellow] Marianne is ready "
            f"({ready_count} runnable instrument(s))."
        )
    else:
        out.print(
            f"[green]No issues found. Marianne is ready "
            f"({ready_count} runnable instrument(s)).[/green]"
        )

    out.print()


def _status_icon(status: str) -> str:
    """Map status to a Rich-formatted icon."""
    icons = {
        "ok": "[green]✓[/green]",
        "warning": "[yellow]![/yellow]",
        "error": "[red]✗[/red]",
        "optional": "[dim]·[/dim]",
        "unchecked": "[dim]?[/dim]",
    }
    return icons.get(status, "[dim]?[/dim]")
