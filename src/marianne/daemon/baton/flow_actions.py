"""Bounded I/O for flow conditions and trigger actions."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from marianne.core.config.flow import RunTrigger
from marianne.core.expressions import FileFacts
from marianne.core.logging import get_logger
from marianne.execution.validation.expansion import expand_known
from marianne.utils import run_supervisor
from marianne.utils.process import (
    run_bounded_command,
)
from marianne.utils.run_supervisor import subreaper_available

FLOW_FILE_READ_CAP_BYTES = 1_048_576
FLOW_FACTS_TIMEOUT_SECONDS = 10.0
FLOW_RUN_LOG_CAP_BYTES = 1_048_576
_NAME = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_logger = get_logger("baton.flow_actions")


def _expand_path(template: str, variables: dict[str, Any]) -> str:
    return expand_known(template, variables)


def _read_facts(
    workspace: Path,
    templates: frozenset[str],
    variables: dict[str, Any],
    started_at: float | None,
) -> dict[str, FileFacts]:
    """Read every ``file("...")`` path named by an ``until`` expression.

    A path is relative to the job workspace. ``{workspace}`` and the score's
    ``prompt.variables``/loop indices expand in the validation-engine ``{name}``
    form; a Jinja ``{{ name }}`` is left verbatim and therefore never exists
    (V233 names it at validation time).
    """
    root = workspace.resolve()
    facts: dict[str, FileFacts] = {}
    values = {"workspace": str(root), **variables}
    for template in templates:
        path = (root / _expand_path(template, values)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            facts[template] = FileFacts(False, False, None)
            continue
        try:
            stat = path.stat()
            modified = started_at is not None and stat.st_mtime > started_at
            if stat.st_size > FLOW_FILE_READ_CAP_BYTES:
                facts[template] = FileFacts(True, modified, None)
                continue
            with path.open("rb") as handle:
                raw = handle.read(FLOW_FILE_READ_CAP_BYTES + 1)
            text = (
                raw.decode("utf-8", errors="replace")
                if len(raw) <= FLOW_FILE_READ_CAP_BYTES else None
            )
            facts[template] = FileFacts(True, modified, text)
        except OSError:
            facts[template] = FileFacts(False, False, None)
    return facts


async def prefetch_file_facts(
    workspace: Path,
    templates: frozenset[str],
    variables: dict[str, Any],
    started_at: float | None,
) -> dict[str, FileFacts]:
    """Take a bounded file snapshot in a worker thread, never on the baton loop."""
    return await asyncio.wait_for(
        asyncio.to_thread(_read_facts, workspace, templates, variables, started_at),
        timeout=FLOW_FACTS_TIMEOUT_SECONDS,
    )


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int | None
    timed_out: bool
    log_path: str | None
    error: str | None = None
    supervisor_pid: int | None = None
    reparented_orphans: int = 0


async def execute_run_action(
    action: str | RunTrigger,
    *,
    workspace: Path,
    job_id: str,
    sheet_num: int,
    fired_epoch: int,
    chain_id: int,
    cursor: int,
    attempt: int,
    variables: dict[str, Any],
) -> RunOutcome:
    """Run a shell action through the shared #406-safe process lifecycle."""
    spec = action if isinstance(action, RunTrigger) else RunTrigger(command=action)
    root = workspace.resolve()
    cwd = (root / spec.working_directory).resolve() if spec.working_directory else root
    if not cwd.is_relative_to(root) or not cwd.is_dir():
        return RunOutcome(None, False, None, "working directory is outside the workspace")
    values = {"workspace": str(root), "sheet_num": sheet_num, **variables}

    def quote(match: re.Match[str]) -> str:
        name = match.group(1)
        return shlex.quote(str(values[name])) if name in values else match.group(0)

    command = _NAME.sub(quote, spec.command)
    safe_job = re.sub(r"[^A-Za-z0-9._-]", "-", job_id)
    log_dir = root / ".marianne-flow"
    log_dir.mkdir(mode=0o700, exist_ok=True)
    if not log_dir.resolve().is_relative_to(root):
        return RunOutcome(None, False, None, "flow log directory escapes workspace")
    log_path = log_dir / (
        f"run-{safe_job}-s{sheet_num}-e{fired_epoch}-c{chain_id}-a{cursor}-n{attempt}"
        f"-{secrets.token_hex(12)}.log"
    )
    state_path = log_path.with_suffix(".state") if subreaper_available() else None
    if state_path is None:
        _logger.warning(
            "flow_run.subreaper_unavailable",
            job_id=job_id,
            reason="Linux PR_SET_CHILD_SUBREAPER unavailable; using three-source cleanup",
        )
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(log_path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as log:
            # ``-I`` (isolated): ignore PYTHONPATH and never put the action's
            # cwd on sys.path, and run the supervisor by its absolute FILE so
            # no ``marianne`` package in the workspace or on PYTHONPATH can
            # shadow it (Forge Inspect P2; Circuit V3: ``-P -m`` was still
            # shadowed by PYTHONPATH="." or an empty entry, GH #445). The
            # supervisor imports nothing from marianne. The shell still runs
            # in ``cwd``.
            argv = (
                [sys.executable, "-I", str(run_supervisor.__file__),
                 str(state_path), command]
                if state_path is not None else ["bash", "-c", command]
            )
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(cwd), stdin=asyncio.subprocess.DEVNULL,
                stdout=log, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ,
                     "MARIANNE_FLOW_JOB": job_id,
                     "MARIANNE_FLOW_SHEET": str(sheet_num),
                     "MARIANNE_FLOW_CHAIN": str(chain_id),
                     "MARIANNE_FLOW_ATTEMPT": str(attempt)},
            )
        try:
            parent_created_at = psutil.Process(proc.pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            parent_created_at = 0.0
        result = await run_bounded_command(
            proc, timeout_seconds=spec.timeout_seconds, pgid=proc.pid,
            context="flow_run", file_output_path=log_path,
            parent_create_time=parent_created_at,
            # GH #415: the cap is enforced WHILE the child runs, not only
            # truncated after it exits, so peak disk use is bounded.
            file_output_cap_bytes=FLOW_RUN_LOG_CAP_BYTES,
            supervisor_state_path=state_path,
        )
        if log_path.stat().st_size > FLOW_RUN_LOG_CAP_BYTES:
            with log_path.open("r+b") as limited_log:
                limited_log.truncate(FLOW_RUN_LOG_CAP_BYTES)
        if result.output_capped:
            return RunOutcome(
                result.returncode, False, str(log_path),
                f"run log exceeded {FLOW_RUN_LOG_CAP_BYTES} bytes while the command ran; "
                "the command was ended and the log truncated to the cap",
                result.supervisor_pid, result.reparented_orphans,
            )
        return RunOutcome(
            result.returncode, result.timed_out, str(log_path),
            supervisor_pid=result.supervisor_pid,
            reparented_orphans=result.reparented_orphans,
        )
    except (OSError, ValueError) as exc:
        return RunOutcome(None, False, None, f"{type(exc).__name__}: {exc}")
