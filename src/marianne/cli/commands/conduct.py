"""Conducting commands for Marianne CLI (GH #547): ``wait``, ``next``, ``verdict``.

A conductor today polls ``mzt status`` hundreds of times and parses rich
tables. These three commands answer the three questions a conductor asks
with a closed enum and one small JSON object each, so a small model can
hold the loop:

- ``mzt wait JOB``    blocks until the job needs a decision or is finished.
- ``mzt next``        says which job needs attention and what to do about it.
- ``mzt verdict JOB`` reads the review sheet's VERDICT line as PASS/REWORK/BLOCKED.

All conductor access goes through :func:`_route`, so tests replace one
function and never touch a real conductor.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from marianne.daemon.exceptions import DaemonError, JobSubmissionError

from ..helpers import report_daemon_route_error
from ..output import output_error, output_json
from ._shared import validate_job_id

# ---------------------------------------------------------------------------
# Conductor access (single seam)
# ---------------------------------------------------------------------------


class ConductorUnreachable(Exception):
    """The conductor did not answer (absent, busy, or protocol failure)."""

    def __init__(self, cause: Exception | None = None) -> None:
        super().__init__(str(cause) if cause else "conductor not running")
        self.cause = cause


class JobNotFound(Exception):
    """The conductor answered and does not know the job."""


async def _route(method: str, params: dict[str, Any]) -> Any:
    """Call the conductor; the one place tests patch."""
    from marianne.daemon.detect import try_daemon_route

    try:
        routed, result = await try_daemon_route(method, params)
    except JobSubmissionError as exc:
        raise JobNotFound(str(exc)) from exc
    except DaemonError as exc:
        raise ConductorUnreachable(exc) from exc
    if not routed:
        raise ConductorUnreachable()
    return result


def _report_unreachable(exc: ConductorUnreachable, *, json_output: bool) -> None:
    from marianne.daemon.exceptions import DaemonNotRunningError

    report_daemon_route_error(exc.cause or DaemonNotRunningError(), json_output=json_output)


# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------

WAIT_EXIT_CODES = {"done": 0, "needs_you": 2, "conductor_down": 3, "wait_expired": 4}
_WAIT_DEFAULT_MAX = 1800.0
_POLL_START = 2.0
_POLL_FACTOR = 1.5
_POLL_CAP = 10.0
_DOWN_AFTER = 3  # consecutive unreachable polls before conductor_down

Sleeper = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


_CHAIN_REASON = "finished and holding its chain trigger; `mzt resume JOB` starts the next cycle"


def _fermata_sheets(state: dict[str, Any]) -> list[tuple[str, str | None]]:
    sheets = state.get("sheets") or {}
    out: list[tuple[str, str | None]] = []
    for num, sheet in sheets.items():
        if isinstance(sheet, dict) and sheet.get("status") == "fermata":
            out.append((str(num), sheet.get("fermata_reason")))
    return out


def _fermata_text(fermata: list[tuple[str, str | None]]) -> str:
    num, reason = fermata[0]
    text = f"sheet {num} is at a fermata awaiting a decision"
    if reason:
        text += f": {reason}"
    if len(fermata) > 1:
        text += f" (+{len(fermata) - 1} more sheet(s))"
    return text


def classify_job(state: dict[str, Any]) -> tuple[str, str | None, str] | None:
    """Map a ``job.status`` payload to ``(state, outcome, reason)``.

    Returns ``None`` while the job is still moving and nobody has to decide.
    """
    status = str(state.get("status", "")).lower()
    fermata = _fermata_sheets(state)
    if status == "completed":
        return "done", "completed", "job completed"
    if status == "cancelled":
        return "done", "cancelled", "job was cancelled"
    if status == "failed":
        return "needs_you", "failed", str(state.get("error_message") or "job failed")
    if status == "paused_at_chain":
        return "needs_you", "paused", _CHAIN_REASON
    if status == "paused":
        if fermata:
            return "needs_you", "paused", _fermata_text(fermata)
        return "needs_you", "paused", "job is paused"
    if fermata:
        return "needs_you", "paused", _fermata_text(fermata)
    return None


async def wait_for_job(
    fetch: Callable[[], Awaitable[dict[str, Any]]],
    job_id: str,
    max_seconds: float,
    *,
    clock: Clock = time.monotonic,
    sleep: Sleeper = asyncio.sleep,
) -> dict[str, Any]:
    """Poll ``fetch`` with backoff until the job needs a decision or ends.

    The conductor IPC has no per-job subscription, so the polling lives here
    (not in the caller). ``clock`` and ``sleep`` are injectable so tests never
    sleep. ``JobNotFound`` from ``fetch`` propagates.
    """
    deadline = clock() + max_seconds
    delay = _POLL_START
    failures = 0
    last_status: str | None = None
    while True:
        try:
            state = await fetch()
        except ConductorUnreachable as exc:
            failures += 1
            if failures >= _DOWN_AFTER:
                return _wait_result(
                    "conductor_down", job_id, last_status, None, f"conductor unreachable: {exc}",
                )
        else:
            failures = 0
            last_status = str(state.get("status", "")).lower() or last_status
            verdict = classify_job(state)
            if verdict is not None:
                kind, outcome, reason = verdict
                return _wait_result(kind, job_id, last_status, outcome, reason)
        remaining = deadline - clock()
        if remaining <= 0:
            return _wait_result(
                "wait_expired", job_id, last_status, None,
                f"still {last_status or 'unknown'} after {max_seconds:g}s",
            )
        await sleep(min(delay, remaining))
        delay = min(delay * _POLL_FACTOR, _POLL_CAP)


def _wait_result(
    state: str, job_id: str, status: str | None, outcome: str | None, reason: str,
) -> dict[str, Any]:
    return {
        "state": state,
        "job_id": job_id,
        "status": status,
        "outcome": outcome,
        "reason": reason,
        "verdict": None,
    }


def wait(
    job_id: str = typer.Argument(..., help="The score's run id (job id) to wait for"),
    max_seconds: float = typer.Option(
        _WAIT_DEFAULT_MAX,
        "--max",
        "-m",
        min=0,
        help="Give up after this many seconds and report wait_expired (default 1800)",
    ),
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Output one JSON object (no ANSI)",
    ),
) -> None:
    """Block until a score is finished or needs a human decision.

    Returns one of four states: done, needs_you, conductor_down, wait_expired.
    Exit codes: 0 done, 2 needs_you, 3 conductor_down, 4 wait_expired
    (1 is an error such as an unknown score). Rate limits, retries and
    fallbacks happen inside the conductor and do not end the wait.

    Examples:
        mzt wait my-job --json
        mzt wait my-job --max 600 --json
    """
    job_id = validate_job_id(job_id)

    async def fetch() -> dict[str, Any]:
        result = await _route("job.status", {"job_id": job_id, "workspace": None})
        return result if isinstance(result, dict) else {}

    try:
        outcome = asyncio.run(wait_for_job(fetch, job_id, max_seconds))
    except JobNotFound:
        output_error(
            f"Score not found: {job_id}",
            hints=["Run 'mzt list' to see available scores."],
            json_output=json_output,
        )
        raise typer.Exit(1) from None
    if json_output:
        output_json(outcome)
    else:
        typer.echo(f"{outcome['state']}: {outcome['job_id']} - {outcome['reason']}")
    code = WAIT_EXIT_CODES[outcome["state"]]
    if code:
        raise typer.Exit(code)


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------

JUDGED_MARKER = ".mzt-judged.json"

_VERDICT_MAP = {
    "PASS": "PASS", "PASSED": "PASS", "APPROVE": "PASS", "APPROVED": "PASS",
    "ACCEPT": "PASS", "ACCEPTED": "PASS",
    "REWORK": "REWORK", "REJECT": "REWORK", "REJECTED": "REWORK",
    "HOLD": "BLOCKED", "BLOCK": "BLOCKED", "BLOCKED": "BLOCKED",
}
DEFAULT_VERDICT_PATTERN = (
    r"^[\s>#*_`\-]*VERDICT\s*[:=]\s*[*_`\[\s]*(?P<word>[A-Za-z]+)"
)
_TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".mypy_cache"}
_MAX_FILE_BYTES = 2_000_000
_MAX_FILES_SCANNED = 20_000


def _line_verdict(line: str, regex: re.Pattern[str]) -> str | None:
    """The normalised verdict on one line, or None.

    A template line that lists several verdict words ("VERDICT: PASS | REWORK")
    is an instruction, not a verdict, and is skipped.
    """
    match = regex.search(line)
    if match is None:
        return None
    word = match.groupdict().get("word") or (match.group(1) if match.groups() else None)
    if not word:
        return None
    mapped = _VERDICT_MAP.get(word.upper())
    if mapped is None:
        return None
    others = {
        _VERDICT_MAP[w.upper()]
        for w in re.findall(r"[A-Za-z]+", line[match.end():])
        if w.upper() in _VERDICT_MAP
    }
    if others - {mapped}:
        return None
    return mapped


def find_verdict(workspace: Path, pattern: str = DEFAULT_VERDICT_PATTERN) -> dict[str, Any]:
    """Newest artifact in ``workspace`` carrying a mappable VERDICT line.

    Within that file the last such line wins. No prose interpretation beyond
    the word mapping; ``round`` is not tracked and is always ``None``.
    """
    unknown: dict[str, Any] = {
        "verdict": "unknown", "raw": None, "source_path": None, "round": None,
    }
    try:
        regex = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        raise typer.BadParameter(f"Invalid --pattern: {exc}") from exc
    if not workspace.is_dir():
        return unknown

    candidates: list[tuple[float, Path]] = []
    scanned = 0
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            path = Path(root) / name
            if path.suffix.lower() not in _TEXT_SUFFIXES or name == JUDGED_MARKER:
                continue
            scanned += 1
            if scanned > _MAX_FILES_SCANNED:
                break
            try:
                st = path.stat()
            except OSError:
                continue
            if 0 < st.st_size <= _MAX_FILE_BYTES:
                candidates.append((st.st_mtime, path))
    candidates.sort(key=lambda item: item[0], reverse=True)

    for _mtime, path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found: tuple[str, str] | None = None
        for line in text.splitlines():
            mapped = _line_verdict(line, regex)
            if mapped is not None:
                found = (mapped, line.strip()[:300])
        if found is not None:
            return {
                "verdict": found[0], "raw": found[1],
                "source_path": str(path), "round": None,
            }
    return unknown


async def _job_row(job_id: str) -> dict[str, Any]:
    rows = await _route("job.list", {})
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("job_id") == job_id:
            return row
    raise JobNotFound(job_id)


def _read_markers(workspace: Path) -> dict[str, Any]:
    try:
        data = json.loads((workspace / JUDGED_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return jobs if isinstance(jobs, dict) else {}


def is_judged(workspace: str | None, job_id: str, submitted_at: float | None) -> bool:
    """Whether ``mzt verdict --accept`` was recorded for this run of the job.

    Keyed on ``submitted_at`` so a fresh run under a reused job id is not
    mistaken for the run that was judged.
    """
    if not workspace:
        return False
    entry = _read_markers(Path(workspace)).get(job_id)
    if not isinstance(entry, dict):
        return False
    return entry.get("submitted_at") == submitted_at


def _write_marker(
    workspace: Path, job_id: str, submitted_at: float | None, verdict: str,
) -> None:
    jobs = _read_markers(workspace)
    jobs[job_id] = {
        "submitted_at": submitted_at,
        "verdict": verdict,
        "judged_at": datetime.now(UTC).isoformat(),
    }
    tmp = workspace / (JUDGED_MARKER + ".tmp")
    tmp.write_text(json.dumps({"jobs": jobs}, indent=2), encoding="utf-8")
    os.replace(tmp, workspace / JUDGED_MARKER)


def verdict(
    job_id: str = typer.Argument(..., help="The score's run id (job id) to read the verdict of"),
    pattern: str = typer.Option(
        DEFAULT_VERDICT_PATTERN,
        "--pattern",
        help="Regex for a verdict line; group 'word' (or group 1) is the verdict word. "
        "Default matches 'VERDICT: <word>' lines.",
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Read this workspace directly instead of asking the conductor for it",
    ),
    accept: bool = typer.Option(
        False,
        "--accept",
        "--judged",
        help="Record this job as judged so `mzt next` stops saying JUDGE (accepted, or sent "
        "back for rework). Written to .mzt-judged.json in the workspace.",
    ),
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Output one JSON object (no ANSI)",
    ),
) -> None:
    """Read a score's review verdict as PASS, REWORK, BLOCKED or unknown.

    Finds the newest review/inspect artifact in the score's workspace that has
    a 'VERDICT:' line and maps its word: PASS/APPROVE/ACCEPT -> PASS,
    REWORK/REJECT -> REWORK, HOLD/BLOCK -> BLOCKED. Anything else, or no
    such line, is unknown. Nothing else is interpreted.

    Examples:
        mzt verdict my-job --json
        mzt verdict my-job --accept --json
    """
    job_id = validate_job_id(job_id)
    submitted_at: float | None = None
    if workspace is None:
        try:
            row = asyncio.run(_job_row(job_id))
        except JobNotFound:
            output_error(
                f"Score not found: {job_id}",
                hints=["Run 'mzt list' to see available scores."],
                json_output=json_output,
            )
            raise typer.Exit(1) from None
        except ConductorUnreachable as exc:
            _report_unreachable(exc, json_output=json_output)
            raise  # unreachable: report_daemon_route_error exits
        raw_ws = row.get("workspace")
        if not raw_ws:
            output_error(
                f"The conductor has no workspace recorded for {job_id}",
                hints=["Pass --workspace DIR to read a workspace directly."],
                json_output=json_output,
            )
            raise typer.Exit(1)
        workspace = Path(str(raw_ws))
        submitted_at = row.get("submitted_at")

    result = find_verdict(workspace, pattern)
    if accept:
        try:
            _write_marker(workspace, job_id, submitted_at, result["verdict"])
        except OSError as exc:
            output_error(
                f"Could not record the verdict as judged: {exc}",
                hints=[f"Check that {workspace} is writable."],
                json_output=json_output,
            )
            raise typer.Exit(1) from None
        result["judged"] = True
    if json_output:
        output_json(result)
    else:
        typer.echo(f"{result['verdict']}: {result['source_path'] or 'no verdict line found'}")


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------

_ACTION_ORDER = ["ESCALATE", "UNSTICK", "JUDGE", "WAIT", "DONE"]
_DEFAULT_SINCE_HOURS = 24.0
_DEFAULT_IDLE_SECONDS = 1800.0  # sheet_timeout_seconds default; see _idle_threshold


def _is_test_artifact(row: dict[str, Any]) -> bool:
    """Same rule as `mzt list`: runs in pytest temp workspaces are not real work."""
    ws = str(row.get("workspace") or "")
    return "/tmp/pytest" in ws or "/pytest-" in ws


def _parse_ts(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _idle_threshold(state: dict[str, Any], override: float | None) -> float:
    """Seconds without any recorded activity before a running job counts as stuck.

    The score's own ``stale_detection.idle_timeout_seconds`` when it enabled
    stale detection, else the sheet timeout, else 30 minutes. A running sheet
    legitimately stays silent for minutes, so the default is deliberately long.
    """
    if override is not None:
        return override
    snapshot = state.get("config_snapshot")
    if isinstance(snapshot, dict):
        stale = snapshot.get("stale_detection")
        if isinstance(stale, dict) and stale.get("enabled"):
            value = stale.get("idle_timeout_seconds")
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
    sheets = state.get("sheets") or {}
    timeouts = [
        float(s["sheet_timeout_seconds"])
        for s in sheets.values()
        if isinstance(s, dict) and isinstance(s.get("sheet_timeout_seconds"), (int, float))
    ]
    return max(timeouts) if timeouts else _DEFAULT_IDLE_SECONDS


def _last_activity(state: dict[str, Any]) -> float | None:
    stamps = [_parse_ts(state.get("updated_at"))]
    for sheet in (state.get("sheets") or {}).values():
        if isinstance(sheet, dict):
            stamps.append(_parse_ts(sheet.get("last_activity_at")))
    real = [s for s in stamps if s is not None]
    return max(real) if real else None


def _running_row(
    state: dict[str, Any] | None, now: float, idle_override: float | None,
) -> tuple[str, str]:
    """Action for a running/queued job from its detailed state."""
    if not state:
        return "WAIT", "running"
    fermata = _fermata_sheets(state)
    if fermata:
        return "ESCALATE", _fermata_text(fermata)
    sheets = [s for s in (state.get("sheets") or {}).values() if isinstance(s, dict)]
    playing = [s for s in sheets if s.get("status") in ("in_progress", "dispatched")]
    last = _last_activity(state)
    if playing and last is not None:
        limit = _idle_threshold(state, idle_override)
        quiet = now - last
        if quiet > limit:
            return "UNSTICK", (
                f"{len(playing)} sheet(s) running with no activity for {int(quiet)}s "
                f"(limit {int(limit)}s)"
            )
    done = sum(1 for s in sheets if s.get("status") == "completed")
    return "WAIT", f"running ({done}/{len(sheets)} sheets done)" if sheets else "running"


def _job_end(row: dict[str, Any]) -> float | None:
    started = row.get("submitted_at")
    if not isinstance(started, (int, float)):
        return None
    running = row.get("running_seconds")
    return float(started) + (float(running) if isinstance(running, (int, float)) else 0.0)


async def compute_next(
    *,
    mine: str | None,
    since_hours: float,
    idle_seconds: float | None,
    route: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """The ordered attention queue: ``[{job_id, action, why}]``."""
    call = route or _route
    clock_now = time.time() if now is None else now
    rows = await call("job.list", {})
    ranked: list[tuple[int, float, dict[str, Any]]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        job_id = str(row.get("job_id", ""))
        status = str(row.get("status", "")).lower()
        if not job_id or status == "scheduled":
            continue
        if mine and not job_id.startswith(mine):
            continue
        if _is_test_artifact(row):
            continue
        terminal = status in ("completed", "failed", "cancelled")
        if terminal and since_hours > 0:
            end = _job_end(row)
            if end is not None and clock_now - end > since_hours * 3600:
                continue

        if status == "completed":
            if is_judged(row.get("workspace"), job_id, row.get("submitted_at")):
                action, why = "DONE", "completed and judged"
            else:
                action, why = "JUDGE", "completed; run `mzt verdict` then `mzt verdict --accept`"
        elif status == "failed":
            action, why = "ESCALATE", str(row.get("error_message") or "job failed")
        elif status == "cancelled":
            action, why = "ESCALATE", "job was cancelled"
        elif status == "paused_at_chain":
            action, why = "ESCALATE", _CHAIN_REASON
        elif status == "paused":
            fermata = _fermata_sheets(await _safe_status(call, job_id) or {})
            action = "ESCALATE"
            why = _fermata_text(fermata) if fermata else "job is paused"
        elif status in ("running", "queued", "pending"):
            if status == "running":
                action, why = _running_row(
                    await _safe_status(call, job_id), clock_now, idle_seconds,
                )
            else:
                action, why = "WAIT", status
        else:
            continue
        ranked.append((
            _ACTION_ORDER.index(action),
            float(row.get("submitted_at") or 0.0),
            {"job_id": job_id, "action": action, "why": why},
        ))
    ranked.sort(key=lambda item: (item[0], item[1]))
    queue = [item[2] for item in ranked]
    if not queue:
        queue = [{"job_id": None, "action": "DONE", "why": "no jobs need attention"}]
    return queue


async def _safe_status(
    call: Callable[[str, dict[str, Any]], Awaitable[Any]], job_id: str,
) -> dict[str, Any] | None:
    try:
        result = await call("job.status", {"job_id": job_id, "workspace": None})
    except (ConductorUnreachable, JobNotFound):
        return None
    return result if isinstance(result, dict) else None


def next_actions(
    mine: str | None = typer.Option(
        None, "--mine", help="Only jobs whose id starts with this prefix",
    ),
    since: float = typer.Option(
        _DEFAULT_SINCE_HOURS,
        "--since",
        min=0,
        help="Ignore finished jobs that ended more than this many hours ago "
        "(default 24; 0 = no limit)",
    ),
    idle_after: float | None = typer.Option(
        None,
        "--idle-after",
        min=1,
        help="Seconds without activity before a running job is UNSTICK "
        "(default: the score's stale_detection timeout, else its sheet timeout)",
    ),
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Output a JSON array (no ANSI)",
    ),
) -> None:
    """List what needs attention across jobs: one action per job, most urgent first.

    Actions: ESCALATE (a human must decide: failed, cancelled, paused at a
    fermata), UNSTICK (a running sheet has gone quiet), JUDGE (completed, not
    yet judged), WAIT (still running), DONE (completed and judged). An empty
    queue is one DONE row with a null job_id. Judged state is the marker that
    `mzt verdict --accept` writes in the job's workspace.

    Examples:
        mzt next --json
        mzt next --mine legion- --json
    """
    try:
        queue = asyncio.run(
            compute_next(mine=mine, since_hours=since, idle_seconds=idle_after),
        )
    except ConductorUnreachable as exc:
        _report_unreachable(exc, json_output=json_output)
        raise
    if json_output:
        output_json(queue)
        return
    for item in queue:
        typer.echo(f"{item['action']}  {item['job_id'] or '-'}  {item['why']}")
