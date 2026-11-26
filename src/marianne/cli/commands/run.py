"""Run command for Marianne CLI.

This module implements the ``mzt run`` command which routes scores
through a running conductor (``mzt start``).  Direct execution is not
supported — a running conductor is required (like docker requires dockerd).

The only exception is ``--dry-run``, which validates and displays the
execution plan without executing anything (no conductor needed).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import typer
import yaml
from rich.panel import Panel
from rich.table import Table

from marianne.core.logging import get_logger

from ..helpers import await_early_failure, is_quiet
from ..output import console, output_error, output_json
from ._shared import validate_start_sheet

_logger = get_logger("cli.run")

if TYPE_CHECKING:
    from marianne.core.config import JobConfig
    from marianne.core.config.fleet import FleetConfig


def _parse_runtime_vars(pairs: list[str] | None) -> dict[str, str]:
    """Parse repeated ``--var key=value`` options into a dict (#359).

    Values are strings and may contain ``=``; the key is everything
    before the first ``=``. Later duplicates win. Raises ValueError on a
    missing ``=`` or an empty key so a typo fails loudly at the CLI
    rather than silently dropping the variable.
    """
    result: dict[str, str] = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise ValueError(f"--var {raw!r} is not key=value")
        key, value = raw.split("=", 1)
        if not key:
            raise ValueError(f"--var {raw!r} has an empty key")
        result[key] = value
    return result


def run(
    config_file: Path = typer.Argument(
        ...,
        help="Path to YAML score configuration file",
        exists=True,
        readable=True,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show what would be executed without running",
    ),
    start_sheet: int | None = typer.Option(
        None,
        "--start-sheet",
        "-s",
        help="Override starting sheet number",
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        hidden=True,
        help="Expert override of the workspace directory. By default the "
        "conductor auto-manages workspaces under ~/workspaces/<score-name> "
        "(or the score's explicit workspace:), so this is rarely needed. "
        "Takes precedence over both. Creates the directory if absent.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output result as JSON for machine parsing",
    ),
    escalation: bool = typer.Option(
        False,
        "--escalation",
        "-e",
        help="Pause sheets in FERMATA on retry exhaustion for a composer "
        "decision (resolve via mzt status instructions). Works without "
        "--self-healing.",
    ),
    self_healing: bool = typer.Option(
        False,
        "--self-healing",
        "-H",
        help="Enable automatic diagnosis and remediation when retries are exhausted",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Auto-confirm suggested fixes when using --self-healing",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Start over from scratch: re-register the score (all sheets "
        "re-run) and, when the score sets workspace_lifecycle."
        "archive_on_fresh, archive non-preserved workspace files first so "
        "stale artifacts can't satisfy this run's validations. Use for "
        "self-chaining scores or re-running a completed score. (Editing "
        "the score file also triggers this automatically for completed "
        "scores.)",
    ),
    require_lock: Path | None = typer.Option(
        None,
        "--require-lock",
        help="Admission gate: refuse unless this composition lock (written by "
        "the composing skill's release gate, check_score_release.py "
        "--write-lock) attests the exact score bytes. A missing, stale or "
        "malformed lock rejects the job with a specific reason. Omit for "
        "ordinary unlocked submission.",
    ),
    job_id: str | None = typer.Option(
        None,
        "--job-id",
        help="Name this run. By default the job ID is the score's filename "
        "stem; --job-id lets one shipped score serve several commissions "
        "side by side without copying it. When the score sets no "
        "workspace:, the auto-managed workspace follows the job ID too "
        "(~/workspaces/<job-id>). Use the same ID with status, pause, "
        "resume and cancel.",
    ),
    var: list[str] | None = typer.Option(
        None,
        "--var",
        help="Per-invocation template variable as key=value (repeatable). "
        "Merges into the score's prompt.variables, overriding YAML on "
        "collision — so one generic score can be pointed at different "
        "inputs without editing the file. Values are strings; typed "
        "variables belong in YAML. Persisted across resume.",
    ),
) -> None:
    """Run a score from a YAML configuration file."""
    from marianne.core.config import JobConfig
    from marianne.daemon.fleet import is_fleet_config

    # A direct call of run() (tests, embedding) leaves typer's OptionInfo
    # default in place; only a real string names the run.
    if not isinstance(job_id, str):
        job_id = None
    if job_id is not None:
        from ._shared import validate_job_id

        job_id = validate_job_id(job_id)

    try:
        runtime_vars = _parse_runtime_vars(var)
    except ValueError as exc:
        if json_output:
            output_json({"error": str(exc)})
        else:
            output_error(
                str(exc),
                hints=["Use --var key=value (the value may contain '=')."],
            )
        raise typer.Exit(1) from None

    # Fleet configs are submitted through the same command, but they are
    # not JobConfig files. Route them before score parsing so the documented
    # `mzt run fleet.yaml` path actually reaches the conductor fleet manager.
    if is_fleet_config(config_file):
        _run_fleet(
            config_file=config_file,
            dry_run=dry_run,
            start_sheet=start_sheet,
            workspace=workspace,
            json_output=json_output,
            fresh=fresh,
            self_healing=self_healing,
            yes=yes,
            escalation=escalation,
            runtime_vars=runtime_vars,
            job_id=job_id,
        )
        return

    try:
        config = JobConfig.from_yaml(config_file)
    except yaml.YAMLError as e:
        if json_output:
            output_json({"error": f"YAML syntax error: {e}"})
        else:
            output_error(
                f"YAML syntax error: {e}",
                hints=[
                    "Check for indentation issues or invalid YAML characters.",
                    f"Validate with: mzt validate {config_file}",
                ],
            )
        raise typer.Exit(1) from None
    except Exception as e:  # noqa: BLE001 — CLI boundary reports the error and exits unsuccessfully
        if json_output:
            output_json({"error": str(e)})
        else:
            output_error(
                str(e),
                hints=[f"Validate with: mzt validate {config_file}"],
            )
        raise typer.Exit(1) from None

    if config.unknown_fields:
        names = ", ".join(
            f"{field.path + '.' if field.path else ''}{field.key}"
            for field in config.unknown_fields
        )
        typer.echo(f"Warning: ignored unknown score field(s): {names}", err=True)

    # Validate start_sheet (must be positive if provided)
    start_sheet = validate_start_sheet(start_sheet)

    # Override workspace from CLI if provided
    if workspace is not None:
        config.workspace = Path(workspace).resolve()

    # In quiet mode, skip the config panel
    if not is_quiet() and not json_output:
        instrument_display = config.effective_instrument_name
        recurring_label = " \\[recurring]" if config.schedule is not None else ""
        console.print(
            Panel(
                f"[bold]{config.name}[/bold]{recurring_label}\n"
                f"{config.description or 'No description'}\n\n"
                f"Instrument: {instrument_display}\n"
                f"Sheets: {config.sheet.total_sheets} "
                f"({config.sheet.size} items each)\n"
                f"Workspace: {config.workspace}",
                title="Score Configuration",
            )
        )

    # Cost warning — alert users when cost tracking is disabled
    if not is_quiet() and not json_output and not config.cost_limits.enabled:
        console.print(
            "\n[yellow]Note:[/yellow] Cost tracking is disabled for this score. "
            "API calls will not be monitored or limited."
        )
        console.print(
            "  [dim]To enable, add to your score:[/dim] "
            "cost_limits: {enabled: true, max_cost_per_job: 10.00}"
        )

    if dry_run:
        # The preview renders the score the conductor would run: --var merges
        # through the same seam the daemon applies at submit (#359).
        from marianne.core.config.job import merge_runtime_variables

        config = merge_runtime_variables(config, runtime_vars or {})
        if not json_output:
            console.print("\n[yellow]Dry run - not executing[/yellow]")
            _show_dry_run(config, config_file)
        else:
            output_json(
                {
                    "dry_run": True,
                    "job_name": config.name,
                    "total_sheets": config.sheet.total_sheets,
                    "workspace": str(config.workspace),
                }
            )
        return

    # Route through daemon (required)
    routed = asyncio.run(
        _try_daemon_submit(
            config_file,
            workspace,
            fresh,
            self_healing,
            yes,
            json_output,
            start_sheet=start_sheet,
            escalation=escalation,
            runtime_vars=runtime_vars,
            require_lock=require_lock,
            job_id=job_id,
        ),
    )
    if routed:
        return

    # Daemon not available or submission failed
    _report_conductor_unreachable(json_output)


def _report_conductor_unreachable(json_output: bool) -> NoReturn:
    """Absent only when no live PID is held; busy otherwise (GH #455)."""
    from marianne.cli.helpers import (
        BUSY_HINTS,
        busy_conductor_message,
        conductor_socket_present,
        live_conductor_pid,
    )

    pid = live_conductor_pid() if conductor_socket_present() else None
    if json_output:
        output_json(
            {
                "error": (
                    busy_conductor_message(pid, "the socket did not accept a connection")
                    if pid is not None
                    else "Marianne conductor is not running. Start with: mzt start"
                ),
                "error_type": (
                    "DaemonUnresponsiveError" if pid is not None else "DaemonNotRunningError"
                ),
                "running_state": "unknown" if pid is not None else "absent",
                "conductor_pid": pid,
            }
        )
    elif pid is not None:
        output_error(
            busy_conductor_message(pid, "the socket did not accept a connection"),
            hints=list(BUSY_HINTS),
        )
    else:
        output_error(
            "Marianne conductor is not running.",
            hints=["Start it with: mzt start"],
        )
    raise typer.Exit(1)


class SubmitStatus(str, Enum):
    """What the conductor did with a submission (#561).

    ``submit_score`` returns one of these; the commands render.
    """

    ACCEPTED = "accepted"
    PENDING = "pending"
    REJECTED = "rejected"
    EARLY_FAILED = "early_failed"
    UNREACHABLE = "unreachable"
    ROUTE_ERROR = "route_error"
    NOT_ROUTED = "not_routed"
    BAD_JOB_ID = "bad_job_id"


@dataclass(frozen=True)
class SubmitResult:
    """A submission outcome as a value: no printing, no ``typer.Exit``.

    Attributes:
        status: Which of the eight outcomes this is.
        job_id: The conductor-assigned job id, when known.
        message: The conductor's message, or the refusal reason.
        error: The ``error_message`` of an early failure.
        response: The conductor's raw reply dict, when it sent one.
        pid: A live-but-unanswering conductor's PID (busy case, GH #455).
        cause: The exception behind a ``ROUTE_ERROR``.
    """

    status: SubmitStatus
    job_id: str | None = None
    message: str = ""
    error: str = ""
    response: dict[str, Any] | None = None
    pid: int | None = None
    cause: Exception | None = None


async def submit_score(
    config_file: Path,
    *,
    workspace: Path | None = None,
    fresh: bool = False,
    self_healing: bool = False,
    auto_confirm: bool = False,
    start_sheet: int | None = None,
    escalation: bool = False,
    runtime_vars: dict[str, str] | None = None,
    require_lock: Path | None = None,
    job_id: str | None = None,
) -> SubmitResult:
    """Validate the job id, submit the score to the conductor, return the outcome.

    The one submit path ``mzt run`` and ``mzt agents run`` both call (#561).
    It loads and validates the score, probes the conductor, routes
    ``job.submit``, polls briefly for an early failure, and returns a typed
    value. It never prints and never raises ``typer.Exit``; every refusal —
    including an invalid job id — is a returned ``SubmitResult``.
    """
    from marianne.daemon.exceptions import (
        DaemonAccessDeniedError,
        DaemonError,
        DaemonProtocolError,
        DaemonUnresponsiveError,
    )

    # Job id first, as a value (#561 D4): an invalid id must not escape as a
    # click.UsageError and must not touch the conductor.
    if job_id is not None:
        from ._shared import validate_job_id

        try:
            validate_job_id(job_id)
        except typer.BadParameter as exc:
            return SubmitResult(status=SubmitStatus.BAD_JOB_ID, message=str(exc.message))

    try:
        from marianne.daemon.detect import is_daemon_available, try_daemon_route

        if not await is_daemon_available():
            return SubmitResult(status=SubmitStatus.NOT_ROUTED)

        params: dict[str, Any] = {
            "config_path": str(config_file.resolve()),
            "fresh": fresh,
            "self_healing": self_healing,
            "self_healing_auto_confirm": auto_confirm,
            "escalation": escalation,  # #361: decoupled from self_healing
            "client_cwd": str(Path.cwd()),
        }
        if workspace is not None:
            params["workspace"] = str(Path(workspace).resolve())
        if start_sheet is not None:
            params["start_sheet"] = start_sheet
        if runtime_vars:
            params["runtime_variables"] = runtime_vars
        if require_lock is not None:
            params["required_lock"] = str(Path(require_lock).resolve())
        if job_id is not None:
            params["job_id"] = job_id

        routed, result = await try_daemon_route("job.submit", params)
        if not routed:
            return SubmitResult(status=SubmitStatus.NOT_ROUTED)

        if not isinstance(result, dict):
            # D6: a non-dict reply is a protocol problem, not a rejection —
            # there is no reply to print honestly.
            return SubmitResult(
                status=SubmitStatus.ROUTE_ERROR,
                cause=DaemonProtocolError("job.submit returned a non-dict reply"),
            )

        status = result.get("status", "unknown")
        got_id = str(result.get("job_id") or "")
        msg = str(result.get("message") or "")

        if status == "pending":
            return SubmitResult(
                status=SubmitStatus.PENDING, job_id=got_id, message=msg, response=result
            )
        if status != "accepted":
            return SubmitResult(
                status=SubmitStatus.REJECTED, job_id=got_id, message=msg, response=result
            )

        # Poll briefly to catch early failures (e.g. template errors).
        # Skip when --fresh: old state may still be transitioning and
        # would produce false failure reports from the previous run (#139).
        early: dict[str, Any] | None = None
        if not fresh:
            early = await await_early_failure(got_id)
        early_status = early.get("status", "") if isinstance(early, dict) else ""
        if early_status in ("failed", "cancelled"):
            return SubmitResult(
                status=SubmitStatus.EARLY_FAILED,
                job_id=got_id,
                error=str(early.get("error_message") or "") if isinstance(early, dict) else "",
                response=early if isinstance(early, dict) else None,
            )
        return SubmitResult(
            status=SubmitStatus.ACCEPTED, job_id=got_id, message=msg, response=result
        )
    except DaemonAccessDeniedError as exc:
        return SubmitResult(status=SubmitStatus.ROUTE_ERROR, cause=exc)
    except (DaemonUnresponsiveError, DaemonProtocolError) as exc:
        return SubmitResult(status=SubmitStatus.ROUTE_ERROR, cause=exc)
    except (OSError, ConnectionError, TimeoutError) as exc:
        _logger.warning("daemon_submit_failed", error=str(exc), exc_info=True)
        return SubmitResult(status=SubmitStatus.NOT_ROUTED)
    except Exception as exc:
        # F-450: MethodNotFoundError and other DaemonError subclasses
        # re-raised by try_daemon_route are route errors, not crashes.
        if isinstance(exc, DaemonError):
            return SubmitResult(status=SubmitStatus.ROUTE_ERROR, cause=exc)
        raise


def _report_submit_unreachable(result: SubmitResult, json_output: bool) -> None:
    """Render ``NOT_ROUTED``: absent conductor, or a live-but-unanswering one."""
    from marianne.cli.helpers import BUSY_HINTS, busy_conductor_message

    if result.pid is not None:
        if json_output:
            output_json(
                {
                    "error": busy_conductor_message(
                        result.pid, "the socket did not accept a connection"
                    ),
                    "error_type": "DaemonUnresponsiveError",
                    "running_state": "unknown",
                    "conductor_pid": result.pid,
                }
            )
        else:
            output_error(
                busy_conductor_message(result.pid, "the socket did not accept a connection"),
                hints=list(BUSY_HINTS),
            )
        return
    if json_output:
        output_json(
            {
                "error": "Marianne conductor is not running. Start with: mzt start",
                "error_type": "DaemonNotRunningError",
                "running_state": "absent",
                "conductor_pid": None,
            }
        )
    else:
        output_error(
            "Marianne conductor is not running.",
            hints=["Start it with: mzt start"],
        )


def render_submit_result(result: SubmitResult, *, json_output: bool) -> None:
    """Print a ``SubmitResult`` the way ``mzt run`` has always printed one.

    Raises ``typer.Exit(1)`` for every outcome a caller cannot continue
    past. The one rendering path for ``run``, ``_run_fleet`` and the
    ``_try_daemon_submit`` adapter, so ``mzt run``'s output is unchanged.
    """
    from marianne.cli.helpers import report_daemon_access_denied, report_daemon_route_error

    if result.status == SubmitStatus.ACCEPTED:
        if json_output:
            output_json(result.response or {})
        else:
            job_id = result.job_id or "?"
            console.print(f"[green]Score submitted to conductor:[/green] {job_id}")
            if result.message:
                console.print(f"  {result.message}")
            console.print(f"\n[dim]Monitor with:[/dim] mzt status {job_id} --watch")
        return

    if result.status == SubmitStatus.PENDING:
        _handle_pending_response(
            job_id=result.job_id or "?", message=result.message, json_output=json_output
        )
        return

    if result.status == SubmitStatus.REJECTED:
        if json_output:
            output_json(result.response or {"error": result.message})
        else:
            rejection = (
                f"Conductor rejected score: {result.message}"
                if result.message
                else "Conductor rejected score."
            )
            output_error(rejection, hints=_rejection_hints(result.message))
        raise typer.Exit(1)

    if result.status == SubmitStatus.EARLY_FAILED:
        job_id = result.job_id or "?"
        if json_output:
            output_json(result.response or {"error": result.error})
        else:
            hints = [f"Run: mzt diagnose {job_id}"]
            if result.error:
                hints.insert(0, result.error)
            output_error(f"Score failed: {job_id}", hints=hints)
        raise typer.Exit(1)

    if result.status == SubmitStatus.BAD_JOB_ID:
        # Only reachable through the adapter (run validates earlier as a
        # returned value path in submit_score); treated as a refusal.
        if json_output:
            output_json({"error": result.message})
        else:
            output_error(result.message, hints=["Check the --job-id value."])
        raise typer.Exit(1)

    if result.status == SubmitStatus.ROUTE_ERROR:
        cause = result.cause
        assert cause is not None
        from marianne.daemon.exceptions import DaemonAccessDeniedError

        if isinstance(cause, DaemonAccessDeniedError):
            report_daemon_access_denied(cause, json_output=json_output)
        else:
            report_daemon_route_error(cause, json_output=json_output)

    if result.status == SubmitStatus.NOT_ROUTED:
        _report_submit_unreachable(result, json_output)
        raise typer.Exit(1)


async def _try_daemon_submit(
    config_file: Path,
    workspace: Path | None,
    fresh: bool,
    self_healing: bool,
    auto_confirm: bool,
    json_output: bool,
    *,
    start_sheet: int | None = None,
    escalation: bool = False,
    runtime_vars: dict[str, str] | None = None,
    require_lock: Path | None = None,
    job_id: str | None = None,
) -> bool:
    """Submit a job to the running conductor (thin adapter over ``submit_score``).

    Returns True if the daemon accepted the submission, False if the
    daemon is not reachable or rejected the job. Access denial emits an
    unknown-running-state diagnostic and exits without falling back.
    Kept as an adapter (#561): five test files call it by name. The actual
    work — probe, ``try_daemon_route("job.submit", params)``, early-failure
    poll — lives in :func:`submit_score` below.
    """

    result = await submit_score(
        config_file,
        workspace=workspace,
        fresh=fresh,
        self_healing=self_healing,
        auto_confirm=auto_confirm,
        start_sheet=start_sheet,
        escalation=escalation,
        runtime_vars=runtime_vars,
        require_lock=require_lock,
        job_id=job_id,
    )

    if result.status == SubmitStatus.REJECTED:
        # The rate-limit panel queries the conductor, so it stays in the
        # rendering layer (text mode only).
        if not json_output:
            await _show_rate_limits_on_rejection(result.message)
        render_submit_result(result, json_output=json_output)  # raises Exit(1)

    # A direct BAD_JOB_ID (run validates before calling, so this is the
    # adapter's only route to it) renders as a rejection and exits 1.
    if result.status in (
        SubmitStatus.BAD_JOB_ID,
        SubmitStatus.EARLY_FAILED,
        SubmitStatus.UNREACHABLE,
        SubmitStatus.ROUTE_ERROR,
    ):
        render_submit_result(result, json_output=json_output)

    # NOT_ROUTED: the callers keep _report_conductor_unreachable, whose busy
    # path re-probes the live PID (GH #455); the adapter reports unreachability
    # the old way — return False.
    if result.status == SubmitStatus.NOT_ROUTED:
        return False

    # ACCEPTED and PENDING render (one path with run/_run_fleet); everything
    # else is either rendered above or reported by the caller.
    if result.status in (SubmitStatus.ACCEPTED, SubmitStatus.PENDING):
        render_submit_result(result, json_output=json_output)
    return result.status in (SubmitStatus.ACCEPTED, SubmitStatus.PENDING)


def _rejection_hints(msg: str, *, fresh: bool = False) -> list[str]:
    """Return context-aware hints based on the conductor's rejection reason.

    Args:
        msg: The rejection message from the conductor.
        fresh: Whether the --fresh flag was used (adjusts "already running" hints).
    """
    msg_lower = msg.lower()

    if "shutting down" in msg_lower:
        return [
            "The conductor is shutting down.",
            "Wait for shutdown to complete, then restart: mzt start",
        ]

    if "composition lock" in msg_lower:
        return [
            "The release gate must attest the exact score bytes before admission.",
            "Run: python check_score_release.py SCORE --project-root ROOT "
            "--lock LOCK --write-lock, then resubmit with --require-lock LOCK.",
        ]

    if "pressure" in msg_lower:
        return [
            "The system is under heavy load.",
            "Check active rate limits: mzt clear-rate-limits (to view/clear)",
            "Wait for running jobs to complete or reduce concurrent work.",
        ]

    if "already" in msg_lower and ("running" in msg_lower or "queued" in msg_lower):
        hints = [
            "A score with this name is already active.",
            "Pause or cancel it first: mzt pause <id> / mzt cancel <id>",
        ]
        if fresh:
            hints.append("Clear the stale entry: mzt clear --score <id>")
        else:
            hints.append("Or wait for it to finish.")
        return hints

    if "parse" in msg_lower or "failed to parse" in msg_lower:
        return [
            "The score file has errors.",
            "Validate it with: mzt validate <file>",
        ]

    if "workspace" in msg_lower and ("not exist" in msg_lower or "not writable" in msg_lower):
        return [
            "The workspace path is invalid.",
            "Create the directory or use --workspace to override.",
        ]

    if "not found" in msg_lower and "config" in msg_lower:
        return [
            "The score file could not be found.",
            "Check the file path and try again.",
        ]

    # Fallback: generic but still actionable
    return [
        "The conductor is running but declined this submission.",
        "Check with: mzt conductor-status",
    ]


def _handle_pending_response(
    *,
    job_id: str,
    message: str,
    json_output: bool,
) -> None:
    """Handle a 'pending' response from the conductor.

    When the conductor queues a job as pending (due to rate limits),
    show an informative message instead of an error. The job will
    start automatically when rate limits clear.
    """
    if json_output:
        output_json(
            {
                "job_id": job_id,
                "status": "pending",
                "message": message,
            }
        )
    else:
        display_msg = message or "Score queued as pending — starts when rate limits clear."
        console.print(f"[yellow]Score queued as pending:[/yellow] {job_id}")
        console.print(f"  {display_msg}")
        console.print(f"\n[dim]Monitor with:[/dim] mzt status {job_id} --watch")
        console.print("[dim]Cancel with:[/dim]  mzt cancel " + job_id)


def _run_fleet(
    *,
    config_file: Path,
    dry_run: bool,
    start_sheet: int | None,
    workspace: Path | None,
    json_output: bool,
    fresh: bool,
    self_healing: bool,
    yes: bool,
    escalation: bool,
    runtime_vars: dict[str, str],
    job_id: str | None = None,
) -> None:
    """Run or preview a fleet config through the documented run command."""
    unsupported: list[str] = []
    if job_id is not None:
        unsupported.append("--job-id")
    if start_sheet is not None:
        unsupported.append("--start-sheet")
    if workspace is not None:
        unsupported.append("--workspace")
    if runtime_vars:
        unsupported.append("--var")
    if fresh:
        unsupported.append("--fresh")
    if self_healing:
        unsupported.append("--self-healing")
    if yes:
        unsupported.append("--yes")
    if escalation:
        unsupported.append("--escalation")

    if unsupported:
        message = "Fleet runs do not yet support score-specific options: " + ", ".join(unsupported)
        if json_output:
            output_json({"error": message})
        else:
            output_error(
                message,
                hints=[
                    "Run the fleet without score-specific options, or run "
                    "individual score files when those controls are needed.",
                ],
            )
        raise typer.Exit(1)

    try:
        fleet_config = _load_fleet_config(config_file)
    except yaml.YAMLError as e:
        if json_output:
            output_json({"error": f"YAML syntax error: {e}"})
        else:
            output_error(
                f"YAML syntax error: {e}",
                hints=[
                    "Check for indentation issues or invalid YAML characters.",
                    f"Validate with: mzt validate {config_file}",
                ],
            )
        raise typer.Exit(1) from None
    except Exception as e:  # noqa: BLE001 — CLI boundary reports the error and exits unsuccessfully
        if json_output:
            output_json({"error": str(e)})
        else:
            output_error(
                str(e),
                hints=[f"Validate with: mzt validate {config_file}"],
            )
        raise typer.Exit(1) from None

    if dry_run:
        if json_output:
            output_json(
                {
                    "dry_run": True,
                    "type": "fleet",
                    "fleet_name": fleet_config.name,
                    "scores": len(fleet_config.scores),
                    "groups": {name: cfg.depends_on for name, cfg in fleet_config.groups.items()},
                }
            )
        else:
            console.print("\n[yellow]Dry run - not executing[/yellow]")
            _show_fleet_dry_run(fleet_config, config_file)
        return

    routed = asyncio.run(
        _try_daemon_submit(
            config_file,
            None,
            False,
            False,
            False,
            json_output,
            start_sheet=None,
            escalation=False,
            runtime_vars=None,
        ),
    )
    if routed:
        return

    _report_conductor_unreachable(json_output)


def _load_fleet_config(config_file: Path) -> FleetConfig:
    """Load and validate a fleet configuration file."""
    from marianne.core.config.fleet import FleetConfig

    with open(config_file) as f:
        raw = yaml.safe_load(f)
    return FleetConfig.model_validate(raw)


def _show_fleet_dry_run(fleet_config: FleetConfig, config_path: Path) -> None:
    """Show what would be submitted for a fleet config."""
    console.print(
        Panel(
            f"[bold]{fleet_config.name}[/bold]\n"
            f"Scores: {len(fleet_config.scores)}\n"
            f"Config: {config_path}",
            title="Fleet Configuration",
        )
    )

    table = Table(title="Fleet Plan")
    table.add_column("Score", style="cyan")
    table.add_column("Group", style="green")
    table.add_column("Depends On", style="yellow")

    for entry in fleet_config.scores:
        depends_on = ""
        if entry.group and entry.group in fleet_config.groups:
            depends_on = ", ".join(fleet_config.groups[entry.group].depends_on)
        table.add_row(entry.path, entry.group or "", depends_on)

    console.print(table)


async def _show_rate_limits_on_rejection(msg: str) -> None:
    """Query and display active rate limits after a rejection.

    Called after a submission is rejected. When the rejection is
    pressure-related, queries the conductor for active rate limits
    and displays them with time-remaining info.

    Fail-open: never raises. If the query fails, nothing is shown.
    """
    msg_lower = msg.lower()
    if "pressure" not in msg_lower:
        return

    try:
        from ..helpers import query_rate_limits
        from ..output import format_rate_limit_info

        backends = await query_rate_limits()
        if not backends:
            return

        lines = format_rate_limit_info(backends)
        if lines:
            console.print()
            console.print("[bold]Active rate limits:[/bold]")
            for line in lines:
                console.print(f"  [yellow]{line}[/yellow]")
    except Exception:  # noqa: BLE001 — fail-open: rate-limit info is extra, never blocks run
        _logger.debug("run.rate_limit_info_unavailable", exc_info=True)


def _show_dry_run(config: JobConfig, config_path: Path) -> None:
    """Show what would be executed in dry run mode."""
    table = Table(title="Sheet Plan")
    table.add_column("Sheet", style="cyan")
    table.add_column("Items", style="green")
    table.add_column("Validations", style="yellow")

    for sheet_num in range(1, config.sheet.total_sheets + 1):
        start = (sheet_num - 1) * config.sheet.size + config.sheet.start_item
        end = min(start + config.sheet.size - 1, config.sheet.total_items)
        table.add_row(
            str(sheet_num),
            f"{start}-{end}",
            str(len(config.validations)),
        )

    console.print(table)

    # Rendering preview (show sheet 1 as representative)
    from marianne.validation.rendering import generate_preview
    from marianne.validation.reporter import ValidationReporter

    reporter = ValidationReporter(console)
    preview = generate_preview(config, config_path, max_sheets=1)
    reporter.report_rendering_terminal(preview)

    # Show dependency info if configured
    if config.sheet.dependencies:
        console.print("\n[bold]Sheet Dependencies:[/bold]")
        for sheet_num, deps in sorted(config.sheet.dependencies.items()):
            dep_list = ", ".join(str(d) for d in deps)
            console.print(f"  Sheet {sheet_num} depends on: {dep_list}")


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "run",
    "submit_score",
]
