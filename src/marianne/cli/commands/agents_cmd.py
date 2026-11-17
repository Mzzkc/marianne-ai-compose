"""``mzt agents list`` and ``mzt agents run`` (SC1c, interim).

A small model conducting persistent agents needs two commands and a closed
vocabulary: who can I cast, and start this task with that person. Both print
one JSON object (or array) with ``--json``, no ANSI, and ``run`` exits 0 on
``ok`` and 1 otherwise. The shapes are the ones the SC1 conductor card was
qualified against.

``list``  ``[{name, role, owns, free, open_debt, last_outcome}]``
``run``   ``{ok, job_id, workspace, result_path}`` or ``{ok: false, code, hint}``

INTERIM. ``run`` always engages the ``targeted-work`` shape with
``--defer-lifecycle`` (consolidate, reflect, maturity and resurrect become
pending lifecycle debt; ``engage`` already refuses at 3 pending debts). It is
replaced when the one-episode score (P2c) exists. Nothing here edits an
agent: the agents directory is read, and every write lands in the runs
directory.

The engage path is the compiler's ``bind_engagement`` called in process; the
submit is ``mzt run``'s own function (``run.run``), not a subprocess. All
conductor access goes through ``conduct._route`` and the submit through
:func:`_submit_score`, so tests replace those two seams.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
import yaml

from ..output import output_json
from . import conduct

agents_app = typer.Typer(
    name="agents",
    help="List and run persistent agents (interim conducting surface for small models).",
    no_args_is_help=True,
)

ENV_INVENTORY = "MZT_AGENTS_INVENTORY"
ENV_RUNS_DIR = "MZT_AGENTS_RUNS_DIR"
ENV_AGENTS_DIR = "MZT_AGENTS_DIR"
DEFAULT_RUNS_DIR = "~/Projects/SCORES/agent-runs"
DEFAULT_AGENTS_DIR = "~/.marianne/agents"
ENV_RESERVATIONS = "MZT_AGENTS_RESERVATIONS"
DEFAULT_RESERVATIONS = "~/.marianne/agents-reserved.yaml"

#: The closed ``code`` vocabulary. ``run`` uses the first five; ``list`` adds
#: CONDUCTOR_DOWN and COMPILER_MISSING (and TASK_FIELD_MISSING for ``--for``).
ERROR_CODES = (
    "TASK_FIELD_MISSING",
    "AGENT_BUSY",
    "AGENT_UNKNOWN",
    "DEBT_REFUSED",
    "SUBMIT_REFUSED",
    "CONDUCTOR_DOWN",
    "COMPILER_MISSING",
)

TASK_FIELDS = ("GOAL", "FILES", "KIND", "ACCEPT", "DO_NOT_TOUCH", "REPORT")
_FIELD_NAMES = TASK_FIELDS + ("FINDINGS",)
_BUSY_STATUSES = frozenset({"running", "queued", "pending", "paused"})
_SHAPE = "targeted-work"


# ---------------------------------------------------------------------------
# Task file
# ---------------------------------------------------------------------------


def parse_task(text: str) -> dict[str, str]:
    """Field name -> value for the card's TASK.md (first occurrence of each).

    A field is a line ``NAME: value``; leading list/quote/heading marks are
    tolerated. Lines after it, up to the next field line, continue the value.
    Only the six fields and FINDINGS are recognised.
    """
    names = "|".join(_FIELD_NAMES)
    start = re.compile(rf"^[ \t>*#-]*({names})[ \t]*:[ \t]*(.*)$")
    fields: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        match = start.match(line)
        if match:
            name: str = str(match.group(1))
            if name in fields:
                current = None  # a repeated field never overwrites the first
                continue
            current = name
            fields[name] = str(match.group(2)).strip()
        elif current is not None and line.strip():
            fields[current] = (fields[current] + " " + line.strip()).strip()
    return fields


def task_problems(fields: dict[str, str]) -> str | None:
    """A hint naming missing/empty fields, or None when all six are present."""
    missing = [f for f in TASK_FIELDS if f not in fields]
    empty = [f for f in TASK_FIELDS if f in fields and not fields[f]]
    if not missing and not empty:
        return None
    parts = []
    if missing:
        parts.append("missing: " + " ".join(missing))
    if empty:
        parts.append(
            "empty: "
            + " ".join(empty)
            + (" (ACCEPT must name a check)" if "ACCEPT" in empty else "")
        )
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def _agents_dir(option: Path | None) -> Path:
    return Path(option or os.environ.get(ENV_AGENTS_DIR) or DEFAULT_AGENTS_DIR).expanduser()


def _runs_dir(option: Path | None) -> Path:
    return Path(option or os.environ.get(ENV_RUNS_DIR) or DEFAULT_RUNS_DIR).expanduser()


def _inventory(option: Path | None) -> Path | None:
    raw = option or os.environ.get(ENV_INVENTORY)
    return Path(raw).expanduser() if raw else None


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _refuse(code: str, hint: str, *, json_output: bool) -> typer.Exit:
    """Print ``{ok: false, code, hint}`` (or a text line); return the Exit(1) to raise."""
    assert code in ERROR_CODES, code
    if json_output:
        output_json({"ok": False, "code": code, "hint": hint})
    else:
        typer.echo(f"{code}: {hint}")
    return typer.Exit(1)


def _require_compiler(*, json_output: bool) -> Any:
    try:
        from marianne_compiler import agent_cli
    except ImportError:
        raise _refuse(
            "COMPILER_MISSING",
            "marianne-compiler is not installed (git submodule update --init compiler; "
            "pip install ./compiler)",
            json_output=json_output,
        ) from None
    return agent_cli


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    {
        "and",
        "are",
        "for",
        "from",
        "into",
        "that",
        "the",
        "this",
        "with",
        "src",
        "tests",
        "test",
        "file",
        "files",
        "none",
        "stated",
        "unknown",
        "add",
        "make",
        "use",
        "new",
    }
)
#: What a role means in task words. Weight 1 each, like any other overlap word.
_ROLE_HINTS: dict[str, tuple[str, ...]] = {
    "builder": ("implement", "fix", "build", "feature", "code", "refactor", "validation"),
    "tester": ("regression", "coverage", "fuzz", "qa", "testing"),
    "verifier": ("verify", "verification", "accept", "acceptance", "check"),
    "documenter": ("docs", "document", "documentation", "readme", "guide"),
    "auditor": ("audit", "security", "review", "risk"),
    "designer": ("design", "interface", "layout", "architecture"),
    "integrator": ("integrate", "integration", "merge", "wire"),
    "maintainer": ("maintain", "maintenance", "infrastructure", "ops", "deploy"),
    "simplifier": ("simplify", "cleanup", "reduce"),
    "reviewer": ("review", "judge", "inspect"),
}


def _words(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in _STOPWORDS
    }


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return []


def rank_score(task_goal: str, task_files: str, entry: dict[str, Any]) -> int:
    """Deterministic overlap score of a task against one agent.

    ``score = |task words & agent words| + 3 * (owned paths that prefix a FILES path)``
    where task words come from GOAL and FILES, and agent words from role, group,
    focus, ``owns`` and the static role-hint table. Words are lowercase
    alphanumerics of 3+ characters minus a small stoplist. No stemming, no
    synonyms beyond the role-hint table, no model.
    """
    task_words = _words(task_goal) | _words(task_files)
    role = str(entry["role"]).lower()
    agent_words = _words(" ".join([role, entry["_group"], entry["_focus"], *entry["owns"]]))
    for key, hints in _ROLE_HINTS.items():
        if key in role:
            agent_words |= set(hints)
    score = len(task_words & agent_words)
    files = [f.strip().strip("`'\",;") for f in re.split(r"[\s,;]+", task_files) if f.strip()]
    for owned in entry["owns"]:
        owned = owned.strip()
        if owned and any(f.startswith(owned) for f in files):
            score += 3
    return score


def _last_outcome(runs_dir: Path, name: str) -> str | None:
    """``"PASS 2026-11-12"`` from this agent's newest run in the runs dir, else None.

    The newest ``<runs-dir>/<name>-*`` directory is read with ``mzt verdict``'s
    own finder; the date is that verdict file's (or the directory's) mtime.
    """
    try:
        runs = [p for p in runs_dir.glob(f"{name}-*") if p.is_dir()]
    except OSError:
        return None
    if not runs:
        return None
    newest = max(runs, key=lambda p: p.stat().st_mtime)
    found = conduct.find_verdict(newest)
    source = Path(found["source_path"]) if found["source_path"] else newest
    stamp = datetime.fromtimestamp(source.stat().st_mtime, UTC).strftime("%Y-%m-%d")
    return f"{found['verdict']} {stamp}"


def reserved_names(path: Path | None = None) -> dict[str, str]:
    """``{agent: reason}`` from the reservations file; empty when it is absent.

    A conductor seat that has queued work for a person writes
    ``reserved: {NAME: "why, until when"}`` there (env ``MZT_AGENTS_RESERVATIONS``,
    default ``~/.marianne/agents-reserved.yaml``). A reserved agent counts as
    busy for ``agents list --free`` and is refused by ``agents run`` with
    ``AGENT_BUSY``, so a second seat cannot add a writer or a debt to a person
    the first seat is carrying. An unreadable file reserves nobody and says so
    on stderr.
    """
    target = path or Path(os.environ.get(ENV_RESERVATIONS) or DEFAULT_RESERVATIONS).expanduser()
    if not target.is_file():
        return {}
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        typer.echo(f"warning: reservations file {target} unreadable: {exc}", err=True)
        return {}
    entries = data.get("reserved") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {str(k).strip().lower(): str(v or "") for k, v in entries.items() if str(k).strip()}


async def _busy_names(names: list[str]) -> set[str]:
    rows = await conduct._route("job.list", {})
    busy: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        job = str(row.get("job_id", "")).lower()
        if str(row.get("status", "")).lower() not in _BUSY_STATUSES:
            continue
        busy.update(n for n in names if f"-{n}-" in job)
    reserved = reserved_names()
    busy.update(n for n in names if n.lower() in reserved)
    return busy


def load_agents(agents_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Engageable agents: ``(entries, warnings)``, sorted by name.

    An agent is listed when its directory holds ``profile.yaml`` and an
    installed ``scores/targeted-work.yaml``. Read-only. ``owns`` is the
    profile's ``owns`` list when it has one (today's profiles do not).
    """
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        dirs = sorted(p for p in agents_dir.iterdir() if p.is_dir())
    except OSError as exc:
        return [], [f"cannot read agents dir {agents_dir}: {exc}"]
    for path in dirs:
        profile_path = path / "profile.yaml"
        if not profile_path.is_file() or not (path / "scores" / f"{_SHAPE}.yaml").is_file():
            continue
        try:
            profile = yaml.load(profile_path.read_text(encoding="utf-8"), Loader=loader)  # noqa: S506 - safe loader
        except (OSError, yaml.YAMLError) as exc:
            warnings.append(f"skipped {path.name}: unreadable profile.yaml ({str(exc)[:80]})")
            continue
        if not isinstance(profile, dict):
            warnings.append(f"skipped {path.name}: profile.yaml is not a mapping")
            continue
        entries.append(
            {
                "name": path.name,
                "role": str(profile.get("role") or ""),
                "owns": _as_list(profile.get("owns")),
                "_focus": str(profile.get("focus") or ""),
                "_group": str(profile.get("group") or ""),
            }
        )
    return entries, warnings


def build_list(
    agents_dir: Path,
    runs_dir: Path,
    *,
    busy: set[str],
    pending_debt: Callable[[Path, str], tuple[int, str]],
    for_task: tuple[str, str] | None = None,
    free_only: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The ``agents list`` payload (pure apart from reads)."""
    entries, warnings = load_agents(agents_dir)
    rows: list[tuple[int, int, str, dict[str, Any]]] = []
    for entry in entries:
        name = entry["name"]
        free = name not in busy
        if free_only and not free:
            continue
        debt = pending_debt(agents_dir, name)[0]
        score = rank_score(for_task[0], for_task[1], entry) if for_task else 0
        rows.append(
            (
                -score,
                debt,
                name,
                {
                    "name": name,
                    "role": entry["role"],
                    "owns": entry["owns"],
                    "free": free,
                    "open_debt": debt,
                    "last_outcome": _last_outcome(runs_dir, name),
                },
            )
        )
    rows.sort(key=lambda r: r[:3])
    return [r[3] for r in rows], warnings


@agents_app.command("list")
def agents_list(
    for_task: Path | None = typer.Option(
        None,
        "--for",
        help="Rank by overlap of this TASK.md's GOAL and FILES words with "
        "each agent's role, group, focus and owns (deterministic; see docs). Without it "
        "the order is by name.",
    ),
    free: bool = typer.Option(
        False,
        "--free",
        help="Only agents with no running, queued, pending or paused "
        "conductor job whose id contains -NAME-, and not reserved in "
        f"{DEFAULT_RESERVATIONS} (env {ENV_RESERVATIONS})",
    ),
    agents_dir: Path | None = typer.Option(
        None,
        "--agents-dir",
        help=f"Agents root (env {ENV_AGENTS_DIR}; default {DEFAULT_AGENTS_DIR})",
    ),
    runs_dir: Path | None = typer.Option(
        None,
        "--runs-dir",
        help=f"Where `agents run` writes (env {ENV_RUNS_DIR}; "
        f"default {DEFAULT_RUNS_DIR}); read here for last_outcome",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="One JSON array (no ANSI)"),
) -> None:
    """List the agents you can cast: ``[{name, role, owns, free, open_debt, last_outcome}]``.

    An agent appears when it has an installed targeted-work score. ``free`` is
    false while the conductor holds a running, queued, pending or paused job
    whose id contains ``-NAME-``. ``open_debt`` is the pending-lifecycle-debt
    count that ``marianne-agents engage`` refuses on at 3. ``last_outcome`` is
    ``"PASS 2026-11-12"`` from the agent's newest run under the runs dir, or
    null. Read-only.

    Examples:
        mzt agents list --json
        mzt agents list --for TASK.md --free --json
    """
    agent_cli = _require_compiler(json_output=json_output)
    task_words: tuple[str, str] | None = None
    if for_task is not None:
        try:
            fields = parse_task(for_task.expanduser().read_text(encoding="utf-8"))
        except OSError as exc:
            raise _refuse(
                "TASK_FIELD_MISSING",
                f"cannot read task file {for_task}: {exc}",
                json_output=json_output,
            ) from None
        if "GOAL" not in fields and "FILES" not in fields:
            raise _refuse(
                "TASK_FIELD_MISSING",
                "missing: GOAL FILES (nothing to rank by)",
                json_output=json_output,
            )
        task_words = (fields.get("GOAL", ""), fields.get("FILES", ""))
    resolved = _agents_dir(agents_dir)
    entries, _ = load_agents(resolved)
    try:
        busy = asyncio.run(_busy_names([e["name"] for e in entries]))
    except conduct.ConductorUnreachable as exc:
        raise _refuse(
            "CONDUCTOR_DOWN", f"the conductor did not answer: {exc}", json_output=json_output
        ) from None
    rows, warnings = build_list(
        resolved,
        _runs_dir(runs_dir),
        busy=busy,
        pending_debt=agent_cli.pending_debt,
        for_task=task_words,
        free_only=free,
    )
    for warning in warnings:
        typer.echo(f"warning: {warning}", err=True)
    if json_output:
        output_json(rows)
        return
    for row in rows:
        typer.echo(
            f"{row['name']}  {row['role']}  {'free' if row['free'] else 'busy'}  "
            f"debt={row['open_debt']}  last={row['last_outcome'] or '-'}"
        )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubmitOutcome:
    """What the conductor did with a bound score."""

    ok: bool
    job_id: str | None = None
    hint: str = ""
    job_exists: bool = False  # the conductor registered the job (even if it then failed)


def _submit_score(score_path: Path, job_id: str) -> SubmitOutcome:
    """Submit through ``mzt run``'s own function, in process, JSON mode.

    ``run.run`` validates the score, routes ``job.submit`` through the
    conductor and prints one JSON object; its stdout is captured and read
    here. ``escalation=True`` makes a sheet that exhausts its retries stop at
    a fermata (what ``mzt next`` reports as UNSTICK/ESCALATE and ``mzt resolve``
    answers) instead of failing the job.
    """
    from .run import run as mzt_run

    buffer = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buffer):
            mzt_run(
                config_file=score_path,
                dry_run=False,
                start_sheet=None,
                workspace=None,
                json_output=True,
                escalation=True,
                self_healing=False,
                yes=False,
                fresh=False,
                require_lock=None,
                job_id=job_id,
                var=None,
            )
    except typer.Exit as exc:
        code = int(exc.exit_code or 0)
    payload: Any = None
    with contextlib.suppress(ValueError):
        payload = json.loads(buffer.getvalue())
    if not isinstance(payload, dict):
        return SubmitOutcome(
            False, hint=(buffer.getvalue().strip() or f"mzt run exited {code} with no output")[:300]
        )
    status = str(payload.get("status", ""))
    got_id = str(payload.get("job_id") or "")
    if code == 0 and status in ("accepted", "pending") and got_id:
        return SubmitOutcome(True, job_id=got_id)
    message = str(
        payload.get("message") or payload.get("error") or payload.get("error_message") or payload
    )
    return SubmitOutcome(
        False,
        hint=message[:300],
        job_exists=bool(got_id) and status in ("failed", "cancelled"),
    )


def _unbind(engagement: Any) -> None:
    """Remove what ``bind_engagement`` just wrote for a job the conductor never took."""
    for path in [engagement.score_path, *engagement.run_dir.glob("task-*")]:
        with contextlib.suppress(OSError):
            path.unlink()
    with contextlib.suppress(OSError):
        engagement.run_dir.rmdir()


@agents_app.command("run")
def agents_run(
    name: str = typer.Argument(..., help="Agent name from `mzt agents list`"),
    task: Path = typer.Option(
        ..., "--task", "-t", help="TASK.md with GOAL FILES KIND ACCEPT DO_NOT_TOUCH REPORT"
    ),
    phase_class: list[str] = typer.Option(
        [],
        "--class",
        help="PHASE=CLASS, repeatable (passed through to engage --class)",
    ),
    inventory: Path | None = typer.Option(
        None,
        "--inventory",
        "-i",
        help=f"Live-verified capability inventory YAML (env {ENV_INVENTORY}; "
        "no default: refused when neither is set)",
    ),
    runs_dir: Path | None = typer.Option(
        None,
        "--runs-dir",
        "-r",
        help=f"Receives one directory per run (env {ENV_RUNS_DIR}; default {DEFAULT_RUNS_DIR})",
    ),
    agents_dir: Path | None = typer.Option(
        None,
        "--agents-dir",
        help=f"Agents root, read only (env {ENV_AGENTS_DIR}; default {DEFAULT_AGENTS_DIR})",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="One JSON object (no ANSI)"),
) -> None:
    """Start one agent on a task.

    Prints ``{ok, job_id, workspace, result_path}`` or ``{ok: false, code, hint}``.

    INTERIM: engages the targeted-work shape with --defer-lifecycle (lifecycle
    becomes pending debt; refused at 3) until the one-episode score replaces it.
    Binds in process with the compiler's engage path (the task is copied
    read-only and injected into every sheet), then submits with the same
    function as `mzt run`, with escalation on. Exit 0 when ok, 1 otherwise.
    Codes: TASK_FIELD_MISSING, AGENT_BUSY, AGENT_UNKNOWN, DEBT_REFUSED,
    SUBMIT_REFUSED. Writes only under the runs dir.

    Examples:
        mzt agents run forge --task TASK.md --json
        mzt agents run forge --task TASK.md --class work=strong --json
    """
    _run(
        name,
        task,
        phase_class,
        inventory,
        runs_dir,
        agents_dir,
        json_output,
        submit=_submit_score,
    )


def _run(
    name: str,
    task: Path,
    phase_class: list[str],
    inventory: Path | None,
    runs_dir: Path | None,
    agents_dir: Path | None,
    json_output: bool,
    *,
    submit: Callable[[Path, str], SubmitOutcome],
) -> None:
    def fail(code: str, hint: str) -> typer.Exit:
        return _refuse(code, hint, json_output=json_output)

    task_path = task.expanduser()
    try:
        task_bytes = task_path.read_bytes()
    except OSError as exc:
        raise fail(
            "TASK_FIELD_MISSING", f"cannot read task file {task}: {exc.strerror or exc}"
        ) from None
    fields = parse_task(task_bytes.decode("utf-8", errors="replace"))
    problem = task_problems(fields)
    if problem:
        raise fail("TASK_FIELD_MISSING", problem)

    agent = name.strip().lower()
    agents_root = _agents_dir(agents_dir)
    installed = agents_root / agent / "scores" / f"{_SHAPE}.yaml"
    if not re.fullmatch(r"[a-z][a-z0-9-]*", agent) or not installed.is_file():
        entries, _ = load_agents(agents_root)
        known = ", ".join(e["name"] for e in entries[:12])
        raise fail(
            "AGENT_UNKNOWN",
            f"no agent {name!r} with an installed {_SHAPE} score under {agents_root}"
            f" (e.g. {known})",
        )

    agent_cli = _require_compiler(json_output=json_output)

    try:
        busy = asyncio.run(_busy_names([agent]))
    except conduct.ConductorUnreachable as exc:
        raise fail(
            "SUBMIT_REFUSED", f"the conductor did not answer ({exc}); start it with `mzt start`"
        ) from None
    if agent in busy:
        raise fail(
            "AGENT_BUSY",
            (
                f"{agent} is reserved by another conductor seat "
                f"({reserved_names()[agent.lower()] or 'no reason given'}); pick another agent"
                if agent.lower() in reserved_names()
                else f"{agent} has a running, queued, pending or paused job; "
                "pick another agent or wait for it"
            ),
        )

    inventory_path = _inventory(inventory)
    if inventory_path is None:
        raise fail(
            "SUBMIT_REFUSED",
            f"no capability inventory: set {ENV_INVENTORY} or pass --inventory "
            "(a live-verified inventory YAML; this command does not invent one)",
        )
    if not inventory_path.is_file():
        raise fail(
            "SUBMIT_REFUSED",
            f"inventory {inventory_path} is not a file ({ENV_INVENTORY} or --inventory)",
        )
    runs_root = _runs_dir(runs_dir)

    purpose = f"task-{hashlib.sha256(task_bytes).hexdigest()[:8]}"
    try:
        engagement = agent_cli.bind_engagement(
            agent,
            purpose=purpose,
            inventory=inventory_path,
            runs_dir=runs_root,
            shape=_SHAPE,
            defer_lifecycle=True,
            task=task_path,
            phase_class=list(phase_class),
            agents_dir=agents_root,
        )
    except agent_cli.EngageRefusal as exc:
        if exc.kind == "debt":
            raise fail(
                "DEBT_REFUSED",
                f"{agent} has {exc.debt_count} pending lifecycle debts; "
                "lifecycle must be paid back before more targeted work",
            ) from None
        hint = str(exc)
        if "already bound" in hint:
            hint += " Change the task (round 2 adds FINDINGS) or run it tomorrow."
        raise fail("SUBMIT_REFUSED", hint) from None
    except Exception as exc:  # noqa: BLE001 - binder refusals (stale inventory, no route) all mean the same to a conductor
        raise fail("SUBMIT_REFUSED", f"{type(exc).__name__}: {str(exc)[:300]}") from None

    outcome = submit(engagement.score_path, engagement.job_name)
    if not outcome.ok:
        if not outcome.job_exists:
            _unbind(engagement)
        raise fail("SUBMIT_REFUSED", outcome.hint or "the conductor refused the score")

    result_name = Path(fields["REPORT"]).name or "REPORT.md"
    payload = {
        "ok": True,
        "job_id": outcome.job_id or engagement.job_name,
        "workspace": str(engagement.workspace),
        "result_path": str(engagement.workspace / result_name),
    }
    if json_output:
        output_json(payload)
    else:
        typer.echo(f"ok  job={payload['job_id']}  workspace={payload['workspace']}")
