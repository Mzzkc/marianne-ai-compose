"""Runtime evaluation of ``skip_when_command`` predicates (#360).

A score may declare, per sheet, a shell command that decides at dispatch time
whether the sheet should be skipped:

    skip_when_command:
      8: {command: 'grep -q "PHASES: 1" {workspace}/plan.md'}

Semantics (mirroring :class:`SkipWhenCommand`'s own docstring):

- exit ``0``     → the condition is met → **SKIP** the sheet.
- exit non-zero  → **PROCEED** (run the sheet normally).
- timeout / spawn error / substitution error → **PROCEED** (fail-open).

The fail-open bias is deliberate and matches the model contract. The cost of a
wrong-*skip* is silently dropping real work (invisible, unrecoverable); the cost
of a wrong-*proceed* is one phantom instance (visible in logs, leaves an
artifact). For brand-stake workflows, silent omission is the higher-severity
failure, so a broken predicate must never cause a skip. Every fail-open path
logs a WARNING so the misconfiguration is diagnosable.

This replicates the process-group-safe subprocess pattern from
``ValidationEngine._check_command_succeeds`` (``execution/validation/engine.py``)
rather than importing it — the musician path must not depend on the validation
engine, and the skip/proceed return shape differs from a ValidationResult. A
future pass could unify both on a shared runner (see #360).
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marianne.core.logging import get_logger
from marianne.utils.process import safe_killpg as _safe_killpg

if TYPE_CHECKING:
    from marianne.core.config.execution import SkipWhenCommand

_logger = get_logger(__name__)

# Truncation for the command echoed in the skip reason / warning logs.
_REASON_CMD_CHARS = 80


def _substitute(command: str, context: dict[str, Any]) -> str:
    """Shell-quote-substitute ``{key}`` placeholders, like validation commands."""
    expanded = command
    for key, value in context.items():
        expanded = expanded.replace("{" + key + "}", shlex.quote(str(value)))
    return expanded


def _truncate(command: str) -> str:
    return command if len(command) <= _REASON_CMD_CHARS else command[:_REASON_CMD_CHARS] + "..."


async def evaluate_skip_command(
    swc: SkipWhenCommand,
    *,
    workspace: Path,
    context: dict[str, Any],
    sheet_num: int | None = None,
) -> tuple[bool, str]:
    """Evaluate a ``skip_when_command`` predicate.

    Args:
        swc: The command rule (command string + timeout).
        workspace: The sheet's workspace (substituted for ``{workspace}``).
        context: Template variables for ``{key}`` substitution (shell-quoted).
            ``workspace`` is injected/overridden from the ``workspace`` arg.
        sheet_num: Optional sheet number for log correlation.

    Returns:
        ``(should_skip, reason)``. ``should_skip`` is True only on a clean
        exit-0; every error path returns ``(False, "")`` (fail-open).
    """
    full_context = dict(context)
    full_context["workspace"] = str(workspace)

    try:
        expanded = _substitute(swc.command, full_context)
    except Exception as exc:  # pragma: no cover — replace() is total, defensive
        _logger.warning(
            "baton.skip_command.substitution_failed",
            extra={"sheet_num": sheet_num, "error": str(exc), "command": _truncate(swc.command)},
        )
        return (False, "")

    proc: asyncio.subprocess.Process | None = None
    pgid: int | None = None
    timed_out = False
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", expanded,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            # Spawn failure (bad cwd, exec error). Fail-open.
            _logger.warning(
                "baton.skip_command.spawn_failed",
                extra={"sheet_num": sheet_num, "error": str(exc), "command": _truncate(expanded)},
            )
            return (False, "")

        if proc.pid is not None:
            try:
                pgid = os.getpgid(proc.pid)
            except ProcessLookupError:
                pgid = None

        # Daemon-own-group safety: if start_new_session failed and the child
        # shares the daemon's process group, a group kill would signal the
        # conductor itself. Abort and fail-open.
        if pgid is not None:
            try:
                daemon_pgid: int | None = os.getpgid(0)
            except ProcessLookupError:
                daemon_pgid = None
            if daemon_pgid is not None and pgid == daemon_pgid:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await proc.wait()
                except ProcessLookupError:
                    pass
                _logger.warning(
                    "baton.skip_command.shares_daemon_pgid",
                    extra={"sheet_num": sheet_num, "pgid": pgid},
                )
                return (False, "")

        try:
            try:
                await asyncio.wait_for(proc.communicate(), timeout=swc.timeout_seconds)
            except TimeoutError:
                timed_out = True
        finally:
            # SIGTERM → 2s grace → SIGKILL of the process group on every exit
            # path. Idempotent when the process already exited.
            if proc is not None and proc.returncode is None:
                if pgid is not None:
                    try:
                        _safe_killpg(pgid, signal.SIGTERM, context="skip_command.kill_grace")
                    except (ProcessLookupError, PermissionError):
                        pass
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except TimeoutError:
                        try:
                            _safe_killpg(pgid, signal.SIGKILL, context="skip_command.kill_force")
                        except (ProcessLookupError, PermissionError):
                            pass
                        try:
                            await proc.wait()
                        except ProcessLookupError:
                            pass
                else:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await proc.wait()
                    except ProcessLookupError:
                        pass

        if timed_out:
            _logger.warning(
                "baton.skip_command.timeout",
                extra={
                    "sheet_num": sheet_num,
                    "timeout_seconds": swc.timeout_seconds,
                    "command": _truncate(expanded),
                },
            )
            return (False, "")

        if proc.returncode == 0:
            reason = f"skip_when_command exited 0: {_truncate(expanded)}"
            return (True, reason)
        return (False, "")

    except Exception as exc:
        # Any unexpected runtime error: fail-open.
        _logger.warning(
            "baton.skip_command.error",
            extra={"sheet_num": sheet_num, "error": str(exc), "command": _truncate(expanded)},
        )
        return (False, "")
