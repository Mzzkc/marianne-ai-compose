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

from pathlib import Path
from typing import Any, TypedDict

import typer
from rich.table import Table

from marianne.cli.output import console, output_error, output_json
from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.availability import (
    check_instrument_available,
    probe_profile,
)
from marianne.instruments.classes import (
    CLASS_VOCABULARY,
    load_class_map,
    write_user_classes,
)
from marianne.instruments.loader import load_all_profiles
from marianne.instruments.registry import InstrumentRegistry

# ---------------------------------------------------------------------------
# Typer app for ``mzt instruments`` subcommand
# ---------------------------------------------------------------------------

instruments_app = typer.Typer(
    name="instruments",
    help="Manage and inspect available instruments.",
    invoke_without_command=True,
)
classes_app = typer.Typer(name="classes", help="Inspect and generate capability classes.")
instruments_app.add_typer(classes_app)


def _class_report() -> tuple[dict[str, Any], bool]:
    profiles = _load_all_profiles()
    registry = InstrumentRegistry()
    for profile in profiles.values():
        registry.register(profile)
    class_map = load_class_map(profile_names=set(profiles))
    details: dict[str, Any] = {}
    broken = bool(class_map.failures)
    for name, entry in sorted(class_map.classes.items()):
        chain = []
        for item in entry.chain:
            available, reason = check_instrument_available(item.profile, registry)
            chain.append(
                {
                    "profile": item.profile,
                    "model": item.config.model,
                    "availability": available,
                    "reason": reason,
                }
            )
        if entry.source_layer != "default" and not any(item["availability"] for item in chain):
            broken = True
        details[name] = {"source_layer": entry.source_layer, "chain": chain}
    return {
        "layers": [record.model_dump(mode="json") for record in class_map.layers],
        "classes": details,
        "vocabulary": sorted(CLASS_VOCABULARY),
        "failures": [
            {"layer": failure.layer, "path": str(failure.path), "reason": failure.reason}
            for failure in class_map.failures
        ],
    }, broken


@classes_app.command(name="show")
def show_classes(json_output: bool = typer.Option(False, "--json")) -> None:
    """Show effective chains, their layers, and local availability."""
    report, _ = _class_report()
    if json_output:
        output_json(report)
        return
    for name, entry in report["classes"].items():
        console.print(
            f"{name} ({entry['source_layer']}): "
            + " → ".join(item["profile"] for item in entry["chain"])
        )
    for layer in report["layers"]:
        console.print(f"{layer['layer']}: {layer['path']} sha256={layer['sha256']}")


@classes_app.command(name="check")
def check_classes(
    class_name: str | None = typer.Option(
        None, "--class", help="Require this class to be runnable."
    ),
) -> None:
    """Refuse broken layers and configured chains with no available entry."""
    report, broken = _class_report()
    if class_name is not None:
        selected = report["classes"].get(class_name)
        if selected is None or not any(item["availability"] for item in selected["chain"]):
            broken = True
    if broken:
        output_error("Capability class configuration has unavailable chains or invalid layers")
        raise typer.Exit(1)
    console.print(f"Checked {len(report['classes'])} capability classes")


@classes_app.command(name="write")
def write_classes(
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force"),
    if_absent: bool = typer.Option(False, "--if-absent"),
) -> None:
    """Generate a user map from shipped profiles available on this machine."""
    try:
        path, content, changed = write_user_classes(
            dry_run=dry_run,
            force=force,
            if_absent=if_absent,
        )
    except (OSError, ValueError) as exc:
        output_error(str(exc))
        raise typer.Exit(1) from exc
    if dry_run:
        console.print(content)
    else:
        console.print(f"{path}: {'written' if changed else 'already exists'}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_all_profiles() -> dict[str, InstrumentProfile]:
    """Load all instrument profiles from all sources.

    Delegates to the shared ``load_all_profiles()`` in the loader module.
    """
    return load_all_profiles()


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
        False,
        "--json",
        help="Output as JSON",
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
        # The same probe doctor / check / classes use (GH #447).
        probe = probe_profile(profile)

        if probe.state == "ready":
            status_str = "[green]✓ ready[/green]"
            ready_count += 1
        elif probe.state == "unchecked":
            status_str = "[dim]? unchecked[/dim]"
            unchecked_count += 1
        elif probe.state == "unreachable":
            status_str = "[yellow]✗ not answering[/yellow]"
        else:
            status_str = "[red]✗ not found[/red]"

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
        probe = probe_profile(profile)
        binary_path = probe.binary_path
        status = {
            "ready": "ready",
            "unavailable": "not_found",
            "unreachable": "not_answering",
            "unchecked": "unchecked",
        }[probe.state]

        result.append(
            {
                "name": profile.name,
                "display_name": profile.display_name,
                "kind": profile.kind,
                "status": status,
                "ready": probe.state == "ready" if probe.state != "unchecked" else None,
                "binary_path": binary_path,
                "default_model": profile.default_model,
                "capabilities": sorted(profile.capabilities),
            }
        )

    output_json(result)


class ResolvedRow(TypedDict):
    """One sheet's binding as `mzt instruments resolve` reports it."""

    sheet: int
    movement: int
    voice: int | None
    bound_through: str
    profile: str
    model: str | None
    fallbacks: list[str]


@instruments_app.command(name="resolve")
def resolve_score(
    score: Path = typer.Argument(..., help="Score YAML to resolve"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show, per sheet, the profile and model each instrument binding resolves to.

    Reads the score the way the conductor does at admission — score aliases,
    capability classes from this machine's class map, per-sheet and
    instrument_map pins — and prints one row per sheet: the alias (or class)
    the sheet was bound through, the profile it resolved to, the model, and
    the fallback chain. This is what a score's diversity claim actually
    configures; the prose says what the author meant.
    """
    from marianne.core.config.job import JobConfig
    from marianne.core.sheet import build_sheets
    from marianne.instruments.classes import load_class_map, resolve_job_classes

    try:
        config = JobConfig.from_yaml(score)
    except Exception as exc:  # noqa: BLE001 — the score's own error is the answer
        output_error(f"cannot load {score}: {exc}")
        raise typer.Exit(1) from exc
    profile_names = set(_load_all_profiles())
    try:
        snapshot = resolve_job_classes(
            config, profile_names, load_class_map(profile_names=profile_names)
        )
    except ValueError as exc:
        output_error(str(exc))
        raise typer.Exit(1) from exc
    sheets = build_sheets(config, classes=snapshot)
    rows: list[ResolvedRow] = []
    for sheet in sheets:
        resolution = sheet.instrument_resolution
        bound_through = sheet.instrument_alias or (
            resolution.requested if resolution is not None else ""
        )
        model = sheet.instrument_config.get("model")
        rows.append({
            "sheet": sheet.num,
            "movement": sheet.movement,
            "voice": sheet.voice,
            "bound_through": bound_through,
            "profile": sheet.instrument_name,
            "model": str(model) if model is not None else None,
            "fallbacks": list(sheet.instrument_fallbacks),
        })
    if json_output:
        output_json({"score": str(score), "sheets": rows})
        return
    table = Table(title=f"Resolved bindings — {config.name}", show_header=True,
                  header_style="bold", padding=(0, 2))
    for col in ("SHEET", "MOVEMENT", "VOICE", "BOUND THROUGH", "PROFILE", "MODEL", "FALLBACKS"):
        table.add_column(col, no_wrap=col != "FALLBACKS")
    for r in rows:
        table.add_row(
            str(r["sheet"]), str(r["movement"]),
            "" if r["voice"] is None else str(r["voice"]),
            r["bound_through"] or "[dim](direct)[/dim]", r["profile"],
            r["model"] or "[dim](profile default)[/dim]",
            ", ".join(r["fallbacks"]) or "[dim]none[/dim]",
        )
    console.print(table)
    distinct = {(r["profile"], r["model"]) for r in rows}
    console.print(f"{len(rows)} sheets, {len(distinct)} distinct profile+model bindings")


@instruments_app.command(name="check")
def check_instrument(
    name: str = typer.Argument(help="Instrument name to check"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help=(
            "Run one real prompt through the same backend the conductor uses "
            "(and one schema-mode prompt when the profile declares "
            "json_schema_flag). Proves the profile against the installed CLI, "
            "not just its schema. Spends one or two model calls."
        ),
    ),
    model: str | None = typer.Option(
        None, "--model", help="Model for --live (default: the profile's default_model)",
    ),
    timeout: float = typer.Option(
        180.0, "--timeout", help="Seconds per --live call", min=5.0,
    ),
    record: Path | None = typer.Option(
        None,
        "--record",
        help=(
            "With --live: write each call's raw stdout, stderr and exit code "
            "plus the CLI version to DIR/<instrument>/ as a parser fixture "
            "(GH #472: tests should read what the CLI actually prints)"
        ),
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

    if live:
        report = live_probe(profile, model=model, timeout=timeout, record=record)
        if json_output:
            output_json(report)
        else:
            _live_rich(report)
        if not report["ok"]:
            raise typer.Exit(1)
        return

    if json_output:
        _check_json(profile)
    else:
        _check_rich(profile)


class LiveCall(TypedDict, total=False):
    """One real prompt through the conductor's backend factory."""

    ok: bool
    seconds: float
    exit_code: int | None
    error_type: str | None
    classification: str | None
    error_code: str | None
    error: str | None
    output_head: str
    structured_ok: bool


class LiveReport(TypedDict):
    """`mzt instruments check --live` / `mzt doctor --live` result."""

    name: str
    model: str | None
    availability: str
    plain: LiveCall
    schema: LiveCall | None
    ok: bool


def live_failure(report: LiveReport) -> tuple[str, str] | None:
    """(mode, reason) of the first failed call, or None when the probe passed."""
    for mode in ("plain", "schema"):
        call = report["plain"] if mode == "plain" else report["schema"]
        if call is None or call.get("ok"):
            continue
        reason = (
            call.get("classification")
            or call.get("error")
            or f"exit {call.get('exit_code')}"
        )
        return mode, str(reason)
    return None


_LIVE_SCHEMA: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "live_probe",
        # Carries a `$schema` on purpose: a real score's schema usually does,
        # and the probe must fail the way a sheet would if the backend ever
        # stopped stripping it (GH #479).
        # A discriminated union NESTED under a required property: the shape
        # real intake schemas take and the API accepts. The refused shape — a
        # top-level oneOf — is V332's finding at validate time, not a probe:
        # a probe must pass on a correct profile. A backend that forwards
        # nested combinators wrongly fails here instead of on a sheet (GH #486).
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "n": {"type": "integer"},
                "shape": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"const": "int"},
                                "value": {"type": "integer"},
                            },
                            "required": ["kind", "value"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"const": "text"},
                                "value": {"type": "string"},
                            },
                            "required": ["kind", "value"],
                            "additionalProperties": False,
                        },
                    ],
                },
            },
            "required": ["n", "shape"],
        },
    },
}
_LIVE_PROMPT = (
    "Return the number 7 as the value of n, and set shape to the int branch "
    "with value 7."
)


def _live_structured_ok(output_head: str) -> bool:
    """The probe's structured answer: n == 7 and the int branch was chosen."""
    import json as _json

    try:
        data = _json.loads(output_head)
    except ValueError:
        return False
    return (
        isinstance(data, dict)
        and data.get("n") == 7
        and isinstance(data.get("shape"), dict)
        and data["shape"].get("kind") == "int"
        and data["shape"].get("value") == 7
    )


def live_probe(
    profile: InstrumentProfile,
    *,
    model: str | None = None,
    timeout: float = 180.0,
    record: Path | None = None,
) -> LiveReport:
    """Exercise ``profile`` against the installed instrument, end to end.

    The probe builds the backend through the conductor's own factory and
    classifies each result with the baton's own classifier, so what it
    reports is what a sheet would see: success, the error bucket the chain
    would act on, the wall time, and (in schema mode) whether the validated
    object came back. A profile can pass schema validation and still fail
    every dispatch: a flag the installed CLI rejects, an output shape the
    parser does not read, a provider that is not configured. This is the
    check that catches those.
    """
    import asyncio
    import tempfile
    import time

    from marianne.daemon.baton.backend_pool import _create_backend_for_profile
    from marianne.daemon.baton.musician import _classify_error

    def _one(
        prompt: str, response_format: dict[str, object] | None, mode: str,
    ) -> LiveCall:
        with tempfile.TemporaryDirectory(prefix="mzt-live-probe-") as wd:
            started = time.monotonic()
            raw: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
            try:
                backend = _create_backend_for_profile(
                    profile, working_directory=Path(wd), model=model,
                )
                if response_format is not None:
                    backend.set_response_format(response_format)  # type: ignore[attr-defined]
                if record is not None and hasattr(backend, "set_output_callback"):
                    backend.set_output_callback(
                        lambda stream, chunk: raw.setdefault(stream, []).append(chunk)
                    )
                result = asyncio.run(backend.execute(prompt, timeout_seconds=timeout))
                if record is not None:
                    _write_fixture(
                        record, profile, mode, prompt, response_format, raw,
                        result.exit_code,
                    )
            except Exception as exc:  # noqa: BLE001 — the probe reports any failure as data
                return {
                    "ok": False,
                    "seconds": round(time.monotonic() - started, 2),
                    "classification": "PROBE_ERROR",
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }
            cls = _classify_error(result)
            row: LiveCall = {
                "ok": bool(result.success),
                "seconds": round(time.monotonic() - started, 2),
                "exit_code": result.exit_code,
                "error_type": result.error_type,
                "classification": (
                    "RATE_LIMITED" if result.rate_limited and not result.success
                    else cls.classification
                ),
                "error_code": cls.error_code,
                "error": (result.error_message or "")[:300] or None,
                "output_head": (result.stdout or "")[:120],
            }
            return row

    plain = _one("Reply with exactly the word: ok", None, "plain")
    ok = bool(plain["ok"])
    schema: LiveCall | None = None
    schema_capable = (
        profile.kind == "http"
        or (
            profile.cli is not None
            and profile.cli.command.json_schema_flag is not None
        )
    )
    if schema_capable:
        schema = _one(_LIVE_PROMPT, _LIVE_SCHEMA, "schema")
        if schema.get("ok"):
            schema["structured_ok"] = _live_structured_ok(str(schema.get("output_head", "")))
            schema["ok"] = bool(schema["structured_ok"])
        ok = ok and bool(schema.get("ok"))
    return {
        "name": profile.name,
        "model": model or profile.default_model,
        "availability": probe_profile(profile).state,
        "plain": plain,
        "schema": schema,
        "ok": ok,
    }


def _cli_version(profile: InstrumentProfile) -> str:
    """The installed CLI's `--version` line, or "unknown" (bounded, never raises)."""
    import subprocess

    if profile.cli is None:
        return "n/a"
    try:
        done = subprocess.run(  # noqa: S603 — fixed argv, profile's own executable
            [profile.cli.command.executable, "--version"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    line = (done.stdout or done.stderr).strip().splitlines()
    return line[0][:120] if line else "unknown"


def _write_fixture(
    root: Path,
    profile: InstrumentProfile,
    mode: str,
    prompt: str,
    response_format: dict[str, object] | None,
    raw: dict[str, list[bytes]],
    exit_code: int | None,
) -> None:
    """Persist one live call as a replayable parser fixture.

    Layout: ``<root>/<instrument>/<mode>.stdout|.stderr|.json``. The JSON
    sidecar carries the CLI version, exit code, prompt and response format,
    so a test can replay the bytes through ``_parse_output`` and a reader can
    tell which CLI build produced them.
    """
    import datetime as _dt
    import json as _json

    target = root / profile.name
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{mode}.stdout").write_bytes(b"".join(raw.get("stdout", [])))
    (target / f"{mode}.stderr").write_bytes(b"".join(raw.get("stderr", [])))
    (target / f"{mode}.json").write_text(_json.dumps({
        "instrument": profile.name,
        "cli_version": _cli_version(profile),
        "captured_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "mode": mode,
        "prompt": prompt,
        "response_format": response_format,
        "exit_code": exit_code,
    }, indent=2) + "\n")


def _live_rich(report: LiveReport) -> None:
    console.print(
        f"\nLive probe [cyan]{report['name']}[/cyan] "
        f"(model: {report['model'] or 'instrument default'}, "
        f"availability: {report['availability']})"
    )
    for label in ("plain", "schema"):
        row = report["plain"] if label == "plain" else report["schema"]
        if row is None:
            console.print("  schema:  [dim]not declared (no json_schema_flag)[/dim]")
            continue
        mark = "[green]✓[/green]" if row.get("ok") else "[red]✗[/red]"
        detail = (
            f"{row.get('seconds')}s"
            if row.get("ok")
            else f"{row.get('seconds')}s {row.get('classification')} {row.get('error') or ''}"
        )
        console.print(f"  {label + ':':8} {mark} {detail}")
    console.print(
        f"[green]{report['name']} passed the live probe.[/green]"
        if report["ok"]
        else f"[red]{report['name']} failed the live probe.[/red]"
    )


def _check_rich(profile: InstrumentProfile) -> None:
    """Render the instrument check as Rich output."""
    console.print(f"\nChecking [cyan]{profile.name}[/cyan]...")
    console.print(f"  Display name:  {profile.display_name}")
    if profile.description:
        console.print(f"  Description:   {profile.description}")
    console.print(f"  Kind:          {profile.kind}")

    probe = probe_profile(profile)
    all_ok = probe.state == "ready"

    if profile.kind == "cli" and profile.cli is not None:
        executable = profile.cli.command.executable
        if probe.binary_path:
            console.print(f"  Binary:        {probe.binary_path} [green]✓[/green]")
        else:
            console.print(f"  Binary:        {executable} [red]✗ not found[/red]")
    elif profile.kind == "http" and profile.http is not None:
        console.print(f"  Endpoint:      {profile.http.base_url}{profile.http.endpoint}")
        if profile.http.auth_env_var:
            console.print(f"  Auth env var:  {profile.http.auth_env_var}")
        if probe.state == "ready":
            console.print("  Server:        answering, serves the default model [green]✓[/green]")
        elif probe.state == "unchecked":
            console.print(f"  Server:        [dim]? unchecked — {probe.reason}[/dim]")
        else:
            console.print(f"  Server:        [red]✗ {probe.reason}[/red]")

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
            console.print(f"    {model.name}: {model.context_window:,} ctx{cost_str}")

    console.print()
    if all_ok:
        console.print(f"[green]{profile.name} is ready.[/green]")
    elif probe.state == "unchecked":
        # Not a failure: a remote endpoint is never probed locally.
        console.print(f"[dim]{profile.name} is unchecked — {probe.reason}.[/dim]")
    else:
        output_error(
            f"{profile.name} is not ready: {probe.reason}",
            hints=[
                f"Check that '{profile.name}' is installed and in your PATH."
                if profile.kind == "cli"
                else "Start the local server, or pull the model the profile names.",
                "Run 'mzt doctor' for a full environment check.",
            ],
        )
        raise typer.Exit(1)


def _check_json(profile: InstrumentProfile) -> None:
    """Render the instrument check as JSON."""
    probe = probe_profile(profile)
    found, binary_path = probe.state == "ready", probe.binary_path

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
    result["probe"] = probe.state
    if probe.reason:
        result["reason"] = probe.reason

    output_json(result)
