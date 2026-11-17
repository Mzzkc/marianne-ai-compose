"""Conducting commands for Marianne CLI (GH #547): ``wait``, ``next``, ``verdict``.

A conductor today polls ``mzt status`` hundreds of times and parses rich
tables. These three commands answer the three questions a conductor asks
with a closed enum and one small JSON object each, so a small model can
hold the loop:

- ``mzt wait JOB``    blocks until the job needs a decision or is finished.
- ``mzt next``        says which job needs attention and what to do about it
                      (``--tasks DIR`` also lists TASK files nobody has started).
- ``mzt verdict JOB`` reads the review sheet's VERDICT line as PASS/REWORK/BLOCKED
                      and adds the findings, the validation counts, the report
                      path and the round (GH #554).

All conductor access goes through :func:`_route`, so tests replace one
function and never touch a real conductor.
"""

from __future__ import annotations

import asyncio
import hashlib
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
    "NOT_READY": "REWORK", "NOT_ACCEPTED": "REWORK",
    "ACCEPT_WITH_REPAIRS": "REWORK",
}
# Multi-word verdicts agent inspections actually write (16 Nov survey of
# cycle-state/*-inspection.md): folded to one token before the map lookup.
_VERDICT_PHRASES = (
    (re.compile(r"\bNOT[\s_-]+READY\b", re.IGNORECASE), "NOT_READY"),
    (re.compile(r"\bNOT[\s_-]+ACCEPTED\b", re.IGNORECASE), "NOT_ACCEPTED"),
    (re.compile(r"\bACCEPT[\s_-]+WITH[\s_-]+REPAIRS\b", re.IGNORECASE), "ACCEPT_WITH_REPAIRS"),
)
_BARE_VERDICT = re.compile(r"^[\s>#*_`\-]*VERDICT\s*[:=]?[\s*_`]*$", re.IGNORECASE)


def _fold_phrases(line: str) -> str:
    for phrase, token in _VERDICT_PHRASES:
        line = phrase.sub(token, line)
    return line
DEFAULT_VERDICT_PATTERN = (
    r"^[\s>#*_`\-]*VERDICT\s*[:=]\s*[*_`\[\s]*(?P<word>[A-Za-z]+(?:_[A-Za-z]+)*)"
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
    line = _fold_phrases(line)
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
        # Only the verdict's own clause: "PASS | REWORK" is a template, but
        # "NOT READY. The ACCEPT gate is green" is a verdict followed by prose.
        for w in re.findall(
            r"[A-Za-z]+(?:_[A-Za-z]+)*",
            re.split(r"[.;:\u2014]", line[match.end():], maxsplit=1)[0],
        )
        if w.upper() in _VERDICT_MAP
    }
    if others - {mapped}:
        return None
    return mapped


def _scan_verdict(
    workspace: Path, pattern: str = DEFAULT_VERDICT_PATTERN,
) -> tuple[dict[str, Any], str, int]:
    """:func:`find_verdict` plus the winning file's text and its verdict line index."""
    unknown: dict[str, Any] = {
        "verdict": "unknown", "raw": None, "source_path": None, "round": None,
    }
    try:
        regex = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        raise typer.BadParameter(f"Invalid --pattern: {exc}") from exc
    if not workspace.is_dir():
        return unknown, "", -1

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
        found: tuple[str, str, int] | None = None
        lines = text.splitlines()
        for index, line in enumerate(lines):
            mapped = _line_verdict(line, regex)
            if mapped is None and _BARE_VERDICT.match(line):
                # "VERDICT:" alone, the word on the next non-blank line.
                following = next((ln for ln in lines[index + 1:index + 4] if ln.strip()), "")
                mapped = _line_verdict("VERDICT: " + following.lstrip(" >*_`#-"), regex)
                if mapped is not None:
                    line = line.strip() + " " + following.strip()
            if mapped is not None:
                found = (mapped, line.strip()[:300], index)
        if found is not None:
            return {
                "verdict": found[0], "raw": found[1],
                "source_path": str(path), "round": None,
            }, text, found[2]
    return unknown, "", -1


def find_verdict(workspace: Path, pattern: str = DEFAULT_VERDICT_PATTERN) -> dict[str, Any]:
    """Newest artifact in ``workspace`` carrying a mappable VERDICT line.

    Within that file the last such line wins. No prose interpretation beyond
    the word mapping; ``round`` is ``None`` here (``mzt verdict`` fills it from
    the task file, see :func:`task_round`).
    """
    return _scan_verdict(workspace, pattern)[0]


# ---------------------------------------------------------------------------
# verdict extras (GH #554): findings, accept counts, task file, result path, round
# ---------------------------------------------------------------------------

_MAX_FINDINGS = 20
_MAX_FINDING_CHARS = 500
_BULLET = re.compile(r"^\s*(?:[-*+\u2022]|\d{1,3}[.)])\s+(?P<text>\S.*)$")
_NOISE = re.compile(r"^[\s*_`>#.\-]*$")
_EMPTY_ITEM = re.compile(r"^(?:none|n/?a|nil|no\s+\w+(?:\s+\w+){0,4}|nothing)[\s.*_`]*$", re.I)
#: A line that opens a new section: a markdown heading, or an UPPER-CASE label with a colon
#: (the review sheet's exact labels are VERDICT, EVIDENCE, FAILURES, RISKS, REQUIRED FIXES).
_SECTION_BREAK = re.compile(r"^\s*(?:#{1,6}\s+\S|[>*_`\s]*[A-Z][A-Z _/-]{2,}[*_`]*\s*:)")
_PRIMARY_SECTIONS = ("FINDINGS",)
_SECONDARY_SECTIONS = ("REQUIRED FIXES", "FAILURES")


def _section_open(line: str, name: str) -> re.Match[str] | None:
    """Match a line that opens section ``name``: ``## Name``, ``NAME:`` or ``**NAME:** text``."""
    label = re.escape(name).replace(r"\ ", r"[ _-]")
    heading = re.match(
        rf"^\s*#{{1,6}}\s+[*_`]*{label}[*_`]*\s*:?\s*(?P<rest>.*)$", line, re.I,
    )
    if heading:
        return heading
    return re.match(rf"^[\s>*_`-]*{label}[*_`]*\s*:[*_`]*\s*(?P<rest>.*)$", line, re.I)


def _clean_item(text: str) -> str:
    return text.strip()[:_MAX_FINDING_CHARS]


def _section_items(lines: list[str], names: tuple[str, ...]) -> list[str] | None:
    """Items of the first section named in ``names``, or None when there is none.

    A section starts at its heading/label line (text after the label on that
    line is the first item) and ends at the next heading or UPPER-CASE label.
    Bullets (``-``, ``*``, ``+``, ``1.``) are items, indented lines continue
    the previous item; a section with no bullets yields its non-blank lines.
    Placeholder items such as "none" are dropped.
    """
    for name in names:
        for start, line in enumerate(lines):
            opened = _section_open(line, name)
            if opened is None:
                continue
            body: list[str] = []
            rest = opened.group("rest").strip()
            if rest and not _NOISE.match(rest):
                body.append(rest)
            for follow in lines[start + 1:]:
                if _SECTION_BREAK.match(follow):
                    break
                body.append(follow)
            return _items_from(body)
    return None


def _items_from(body: list[str]) -> list[str]:
    items: list[str] = []
    prose: list[str] = []
    for line in body:
        if not line.strip() or _NOISE.match(line):
            continue
        bullet = _BULLET.match(line)
        if bullet:
            items.append(bullet.group("text"))
        elif items and line[:1] in " \t":
            items[-1] = items[-1] + " " + line.strip()
        else:
            prose.append(line.strip())
    chosen = items or prose
    return [
        _clean_item(i) for i in chosen if i.strip() and not _EMPTY_ITEM.match(i.strip())
    ][:_MAX_FINDINGS]


def extract_findings(text: str, verdict_index: int, verdict_word: str) -> list[str]:
    """The reviewer's findings, verbatim, from the report that carries the verdict.

    Rule, first match wins:

    1. a section headed ``FINDINGS`` (``FINDINGS:`` or ``## Findings``): its bullets;
    2. else the sheets' own review labels ``REQUIRED FIXES`` then ``FAILURES``;
    3. else, when the verdict is not PASS, the bullet lines after the VERDICT line
       up to the next heading or label (non-blank lines when it has no bullets).

    Bullet markers are stripped, the text is not edited. At most 20 items of
    500 characters. A PASS with no FINDINGS section has none.
    """
    if not text:
        return []
    lines = text.splitlines()
    for names in (_PRIMARY_SECTIONS, _SECONDARY_SECTIONS):
        found = _section_items(lines, names)
        if found:
            return found
    if verdict_word == "PASS" or verdict_index < 0:
        return []
    after: list[str] = []
    for line in lines[verdict_index + 1:]:
        if _SECTION_BREAK.match(line):
            break
        after.append(line)
    return _items_from(after)


def accept_counts(state: dict[str, Any] | None) -> dict[str, int]:
    """``{ran, passed}`` over the validations the job's sheets recorded.

    Reads the checkpoint (``job.status``): each sheet's ``validation_details``
    (latest attempt), or its ``passed_validations``/``failed_validations`` name
    lists when the details were not kept. ``{0, 0}`` means no validation result
    is available, not that all passed.
    """
    ran = passed = 0
    for sheet in ((state or {}).get("sheets") or {}).values():
        if not isinstance(sheet, dict):
            continue
        details = sheet.get("validation_details")
        if isinstance(details, list) and details:
            rows = [d for d in details if isinstance(d, dict)]
            ran += len(rows)
            passed += sum(1 for d in rows if d.get("passed"))
        else:
            ok = sheet.get("passed_validations") or []
            bad = sheet.get("failed_validations") or []
            ran += len(ok) + len(bad)
            passed += len(ok)
    return {"ran": ran, "passed": passed}


_TASK_FIELD_NAMES = ("GOAL", "FILES", "KIND", "ACCEPT", "DO_NOT_TOUCH", "REPORT", "FINDINGS")
_TASK_FIELD_START = re.compile(
    rf"^[ \t>*#-]*({'|'.join(_TASK_FIELD_NAMES)})[ \t]*:[ \t]*(.*)$"
)


def parse_task_fields(text: str) -> dict[str, str]:
    """Field name -> value of the conducting card's TASK.md (first occurrence wins).

    Same grammar as ``mzt agents run``: a field is ``NAME: value`` (list, quote
    and heading marks tolerated); following lines up to the next field line
    continue the value.
    """
    fields: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        match = _TASK_FIELD_START.match(line)
        if match:
            name = str(match.group(1))
            if name in fields:
                current = None
                continue
            current = name
            fields[name] = str(match.group(2)).strip()
        elif current is not None and line.strip():
            fields[current] = (fields[current] + " " + line.strip()).strip()
    return fields


def _bound_task_copy_name(score_path: Path) -> str | None:
    """``marianne_agent.task.copy`` from a bound score, when it names one."""
    try:
        import yaml

        data = yaml.safe_load(score_path.read_text(encoding="utf-8"))
        name = data["prompt"]["variables"]["marianne_agent"]["task"]["copy"]
    except (OSError, UnicodeError, yaml.YAMLError, KeyError, TypeError):
        # Unreadable or non-contract score: the caller falls back to the glob.
        return None
    return name if isinstance(name, str) and name and "/" not in name else None


def find_task_file(workspace: Path, config_path: str | None) -> Path | None:
    """The task file ``mzt agents run`` copied for this job, or None.

    ``agents run`` binds the engagement in a run dir (the bound score and the
    read-only copy ``task-<source name>``) and, by default, uses that same
    directory as the workspace. Looked for beside the bound score first
    (``config_path``; its ``marianne_agent.task.copy`` names the copy), then in
    the workspace as ``task-*`` carrying the card's REPORT field.
    """
    dirs: list[Path] = []
    score = Path(config_path) if config_path else None
    if score is not None:
        dirs.append(score.parent)
    if workspace not in dirs:
        dirs.append(workspace)
    if score is not None:
        copy = _bound_task_copy_name(score)
        if copy and (score.parent / copy).is_file():
            return score.parent / copy
    for directory in dirs:
        try:
            matches = sorted(
                (p for p in directory.glob("task-*") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            continue
        for path in matches:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "REPORT" in parse_task_fields(text):
                return path
    return None


def task_extras(task_path: Path | None, workspace: Path) -> tuple[int | None, str | None]:
    """``(round, result_path)`` from the task file; ``(None, None)`` without one.

    ``round`` is 2 when the task carries a ``FINDINGS:`` line (the rework
    task), else 1. ``result_path`` is the REPORT field's file name resolved in
    the workspace, the path ``mzt agents run`` printed.
    """
    if task_path is None:
        return None, None
    try:
        fields = parse_task_fields(task_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None, None
    report = Path(fields.get("REPORT", "")).name
    return (2 if "FINDINGS" in fields else 1), str(workspace / (report or "REPORT.md"))


def build_verdict(
    workspace: Path,
    pattern: str,
    state: dict[str, Any] | None,
    config_path: str | None = None,
) -> dict[str, Any]:
    """The full ``mzt verdict`` object: the four #547 keys plus the #554 extras."""
    result, text, index = _scan_verdict(workspace, pattern)
    round_no, result_path = task_extras(find_task_file(workspace, config_path), workspace)
    result["round"] = round_no
    result["findings"] = extract_findings(text, index, result["verdict"])
    result["accept"] = accept_counts(state)
    result["result_path"] = result_path
    return result


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
    such line, is unknown. The JSON also carries `findings` (the report's
    FINDINGS bullets, else REQUIRED FIXES/FAILURES, else the bullets after a
    non-PASS VERDICT line), `accept` ({ran, passed} over the sheets'
    validation results), `result_path` (the task's REPORT file in the
    workspace) and `round` (2 when the task file has a FINDINGS: line, else
    1; null when the job has no `mzt agents run` task file).

    Examples:
        mzt verdict my-job --json
        mzt verdict my-job --accept --json
    """
    job_id = validate_job_id(job_id)
    submitted_at: float | None = None
    state: dict[str, Any] | None = None
    config_path: str | None = None
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
        config_path = str(row["config_path"]) if row.get("config_path") else None
        state = asyncio.run(_safe_status(_route, job_id))

    result = build_verdict(workspace, pattern, state, config_path)
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
        for finding in result["findings"]:
            typer.echo(f"  - {finding}")


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------

_ACTION_ORDER = ["ESCALATE", "UNSTICK", "JUDGE", "CAST", "WAIT", "DONE"]
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


#: ``mzt agents run`` names its job ``targeted-work-NAME-task-<8 hex sha256 of the task
#: file>-<date>``; a task file is bound when a job id carries its ``-task-<hex8>-``.
_TASK_JOB_PART = re.compile(r"-task-([0-9a-f]{8})-")
_TASK_GLOB = "TASK*.md"


def task_hash8(path: Path) -> str:
    """The 8 hex digits ``mzt agents run`` puts in the job id for this task file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def unbound_tasks(tasks_dir: Path, job_ids: list[str]) -> list[dict[str, Any]]:
    """CAST entries for ``TASK*.md`` files in ``tasks_dir`` that no job is bound to.

    Bound means some job id (any age, any status) contains ``-task-<hex8>-`` with
    the file's hash, so editing a task (a rework adds ``FINDINGS:``) makes it new
    again. Only the hash is compared, not the agent name or the date. Unreadable
    files are skipped. Sorted by file name.
    """
    bound = {m.group(1) for job_id in job_ids for m in _TASK_JOB_PART.finditer(job_id)}
    base = tasks_dir.expanduser()
    out: list[dict[str, Any]] = []
    try:
        files = sorted(p for p in base.glob(_TASK_GLOB) if p.is_file())
    except OSError:
        return out
    for path in files:
        try:
            digest = task_hash8(path)
        except OSError:
            continue
        if digest in bound:
            continue
        absolute = os.path.abspath(path)
        out.append({
            "job_id": None,
            "action": "CAST",
            "why": f"{path.name} has no agent yet",
            "task": absolute,
        })
    return out


async def compute_next(
    *,
    mine: str | None,
    since_hours: float,
    idle_seconds: float | None,
    route: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    now: float | None = None,
    tasks_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """The ordered attention queue: ``[{job_id, action, why}]``.

    With ``tasks_dir``, unbound ``TASK*.md`` files there add ``CAST`` rows that
    also carry ``task`` (the file's absolute path).
    """
    call = route or _route
    clock_now = time.time() if now is None else now
    rows = await call("job.list", {})
    cast_rows: list[dict[str, Any]] = []
    if tasks_dir is not None:
        known = [
            str(r.get("job_id", ""))
            for r in (rows if isinstance(rows, list) else [])
            if isinstance(r, dict)
        ]
        cast_rows = unbound_tasks(tasks_dir, known)
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
    for row_ in cast_rows:
        ranked.append((_ACTION_ORDER.index("CAST"), 0.0, row_))
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
    tasks: Path | None = typer.Option(
        None,
        "--tasks",
        help="Also list TASK*.md files in this directory that no job is bound to "
        "(by the task hash in the job id `mzt agents run` makes) as CAST rows",
    ),
    json_output: bool = typer.Option(
        False, "--json", "-j", help="Output a JSON array (no ANSI)",
    ),
) -> None:
    """List what needs attention across jobs: one action per job, most urgent first.

    Actions: ESCALATE (a human must decide: failed, cancelled, paused at a
    fermata), UNSTICK (a running sheet has gone quiet), JUDGE (completed, not
    yet judged), CAST (with --tasks: a TASK*.md file has no job yet; the row
    carries `task`), WAIT (still running), DONE (completed and judged). An
    empty queue is one DONE row with a null job_id. Judged state is the marker
    that `mzt verdict --accept` writes in the job's workspace.

    Examples:
        mzt next --json
        mzt next --mine legion- --json
        mzt next --tasks . --json
    """
    try:
        queue = asyncio.run(
            compute_next(
                mine=mine, since_hours=since, idle_seconds=idle_after, tasks_dir=tasks,
            ),
        )
    except ConductorUnreachable as exc:
        _report_unreachable(exc, json_output=json_output)
        raise
    if json_output:
        output_json(queue)
        return
    for item in queue:
        extra = f"  {item['task']}" if item.get("task") else ""
        typer.echo(f"{item['action']}  {item['job_id'] or '-'}  {item['why']}{extra}")
