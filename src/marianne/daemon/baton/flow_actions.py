"""Bounded I/O for flow conditions and trigger actions."""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from marianne.core.config.flow import RunTrigger
from marianne.core.expressions import FileFacts
from marianne.execution.validation.expansion import expand_known
from marianne.utils.process import (
    DescendantIdentity,
    reap_descendant_trees,
    safe_killpg,
    snapshot_file_holders,
)

FLOW_FILE_READ_CAP_BYTES = 1_048_576
FLOW_FACTS_TIMEOUT_SECONDS = 10.0
FLOW_RUN_LOG_CAP_BYTES = 1_048_576
_NAME = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def _expand_path(template: str, variables: dict[str, Any]) -> str:
    return expand_known(template, variables)


def _read_facts(
    workspace: Path,
    templates: frozenset[str],
    variables: dict[str, Any],
    started_at: float | None,
) -> dict[str, FileFacts]:
    root = workspace.resolve()
    facts: dict[str, FileFacts] = {}
    for template in templates:
        path = (root / _expand_path(template, variables)).resolve()
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


def _identity_alive(identity: DescendantIdentity) -> bool:
    try:
        current = psutil.Process(identity.pid)
        return (
            current.status() != psutil.STATUS_ZOMBIE
            and current.create_time() == identity.create_time
            and current.uids().real == identity.uid
            and os.getpgid(identity.pid) == identity.pgid
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def _signal_group(pgid: int, sig: int) -> None:
    try:
        safe_killpg(pgid, sig, context="flow_run")
    except (ProcessLookupError, PermissionError):
        pass


async def _cleanup_run_process(
    proc: asyncio.subprocess.Process, log_path: Path, parent_created_at: float,
) -> None:
    """Reap the action's own group and identity-bound detached log holders."""
    captured: dict[tuple[int, float], DescendantIdentity] = {}

    def capture() -> None:
        for holder in snapshot_file_holders(str(log_path), parent_created_at):
            captured[(holder.pid, holder.create_time)] = holder

    capture()
    if proc.returncode is None or captured:
        _signal_group(proc.pid, signal.SIGTERM)
    if captured:
        reap_descendant_trees(tuple(captured.values()))
    # A child can fork during the short grace after the parent's exit.
    await asyncio.sleep(0.05)
    capture()
    if captured:
        reap_descendant_trees(tuple(captured.values()))
    deadline = asyncio.get_running_loop().time() + 2.0
    while asyncio.get_running_loop().time() < deadline:
        if proc.returncode is not None and not any(
            _identity_alive(holder) for holder in captured.values()
        ):
            break
        await asyncio.sleep(0.02)
    if proc.returncode is None or any(
        _identity_alive(holder) for holder in captured.values()
    ):
        _signal_group(proc.pid, signal.SIGKILL)
        for holder in captured.values():
            if _identity_alive(holder):
                try:
                    os.kill(holder.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    if proc.returncode is None:
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()


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
        f"run-{safe_job}-s{sheet_num}-e{fired_epoch}-c{chain_id}-a{cursor}-n{attempt}.log"
    )
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(log_path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as log:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", command, cwd=str(cwd), stdin=asyncio.subprocess.DEVNULL,
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
        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=spec.timeout_seconds)
        except TimeoutError:
            timed_out = True
        finally:
            await _cleanup_run_process(proc, log_path, parent_created_at)
        if log_path.stat().st_size > FLOW_RUN_LOG_CAP_BYTES:
            with log_path.open("r+b") as limited_log:
                limited_log.truncate(FLOW_RUN_LOG_CAP_BYTES)
        return RunOutcome(proc.returncode, timed_out, str(log_path))
    except (OSError, ValueError) as exc:
        return RunOutcome(None, False, None, f"{type(exc).__name__}: {exc}")
