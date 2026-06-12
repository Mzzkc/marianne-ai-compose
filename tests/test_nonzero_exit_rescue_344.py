"""#344 obs1: a plain non-zero exit AFTER the work is done + committed
must not permanent-fail a sheet whose validations all pass.

Structural bug (proven): ``_validate`` returned early on
``not exec_result.success``, and success was decided purely by exit
code — so an agent that finished its work, committed it, then exited
non-zero had its proof-of-work validations skipped and the sheet
recorded as a failure.

The rescue is deliberately NARROW. It fires only for a *plain* process
exit (``exit_reason == "completed"``, no signal, not rate-limited) with
declared validations that ALL pass. Timeouts, signal-kills, and
rate-limits are excluded — those are real interruptions where partial
work must never be rubber-stamped. If validations FAIL, the sheet still
fails (no change for genuine failures). Validations are the score's
acceptance criteria; meeting them beats a stray exit code.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from marianne.backends.base import ExecutionResult
from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.musician import (
    _is_nonzero_exit_rescuable,
    sheet_task,
)
from marianne.daemon.baton.state import AttemptContext, AttemptMode


def _result(
    *,
    success: bool = False,
    exit_code: int | None = 1,
    exit_signal: int | None = None,
    exit_reason: str = "completed",
    rate_limited: bool = False,
) -> ExecutionResult:
    return ExecutionResult(
        success=success,
        stdout="done",
        stderr="",
        duration_seconds=1.0,
        exit_code=exit_code,
        exit_signal=exit_signal,
        exit_reason=exit_reason,  # type: ignore[arg-type]
        rate_limited=rate_limited,
    )


class TestRescuableDiscriminant:
    def test_plain_nonzero_exit_with_validations_is_rescuable(self) -> None:
        assert _is_nonzero_exit_rescuable(_result(), has_validations=True)

    def test_success_is_not_rescuable(self) -> None:
        assert not _is_nonzero_exit_rescuable(
            _result(success=True, exit_code=0), has_validations=True
        )

    def test_no_validations_not_rescuable(self) -> None:
        assert not _is_nonzero_exit_rescuable(_result(), has_validations=False)

    def test_timeout_not_rescuable(self) -> None:
        assert not _is_nonzero_exit_rescuable(
            _result(exit_reason="timeout"), has_validations=True
        )

    def test_signal_kill_not_rescuable(self) -> None:
        assert not _is_nonzero_exit_rescuable(
            _result(exit_signal=9, exit_code=None), has_validations=True
        )

    def test_internal_error_not_rescuable(self) -> None:
        assert not _is_nonzero_exit_rescuable(
            _result(exit_reason="error"), has_validations=True
        )

    def test_rate_limited_not_rescuable(self) -> None:
        assert not _is_nonzero_exit_rescuable(
            _result(rate_limited=True), has_validations=True
        )


def _sheet(validations: list[Any], workspace: Path) -> Sheet:
    return Sheet(
        num=1,
        movement=1,
        voice=None,
        voice_count=1,
        instrument_name="claude-code",
        workspace=workspace,
        prompt_template="do work",
        validations=validations,
        timeout_seconds=60.0,
    )


class TestEndToEndRescue:
    """The musician reports success=True for a rescued attempt so the
    baton's existing success path completes the sheet — no baton-core
    change. A failing-validation non-zero exit still fails."""

    async def test_committed_work_with_passing_validation_is_success(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "proof.txt").write_text("ok")
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        # Work done + committed, but the process exited non-zero (obs1).
        backend.execute = AsyncMock(return_value=_result(exit_code=3))
        backend.name = "claude-code"

        sheet = _sheet(
            [{"type": "command_succeeds", "command": f"test -f {tmp_path}/proof.txt"}],
            tmp_path,
        )
        await sheet_task(
            job_id="t",
            sheet=sheet,
            backend=backend,
            attempt_context=AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL),
            inbox=inbox,
        )
        result = inbox.get_nowait()
        assert result.validation_pass_rate == 100.0
        assert result.execution_success is True  # rescued
        assert result.exit_code == 3  # the truth is preserved

    async def test_nonzero_exit_with_failing_validation_still_fails(
        self, tmp_path: Path
    ) -> None:
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(return_value=_result(exit_code=3))
        backend.name = "claude-code"

        sheet = _sheet(
            [{"type": "command_succeeds", "command": "test -f /no/such/file"}],
            tmp_path,
        )
        await sheet_task(
            job_id="t",
            sheet=sheet,
            backend=backend,
            attempt_context=AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL),
            inbox=inbox,
        )
        result = inbox.get_nowait()
        assert result.execution_success is False

    async def test_timeout_with_passing_validation_still_fails(
        self, tmp_path: Path
    ) -> None:
        """A timeout is a real interruption — never rubber-stamp it even
        if leftover files satisfy a weak check."""
        (tmp_path / "proof.txt").write_text("partial")
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_result(exit_code=None, exit_reason="timeout")
        )
        backend.name = "claude-code"

        sheet = _sheet(
            [{"type": "command_succeeds", "command": f"test -f {tmp_path}/proof.txt"}],
            tmp_path,
        )
        await sheet_task(
            job_id="t",
            sheet=sheet,
            backend=backend,
            attempt_context=AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL),
            inbox=inbox,
        )
        result = inbox.get_nowait()
        assert result.execution_success is False
