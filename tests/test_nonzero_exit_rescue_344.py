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
import signal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from marianne.backends.base import ExecutionResult
from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.musician import (
    _is_nonzero_exit_rescuable,
    sheet_task,
)
from marianne.daemon.baton.state import (
    AttemptContext,
    AttemptMode,
    BatonSheetStatus,
    SheetExecutionState,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend


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


def _python_profile() -> InstrumentProfile:
    """Profile that runs ``python3 -c "<prompt>"`` — the prompt IS the
    subprocess body, so a test can script the exact real exit behavior
    (the same pattern #352's drain battery uses)."""
    return InstrumentProfile(
        name="rescue-344-live",
        display_name="Rescue 344 Live",
        kind="cli",
        models=[
            ModelCapacity(
                name="test-model",
                context_window=128000,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            ),
        ],
        default_model="test-model",
        cli=CliProfile(
            command=CliCommand(executable="python3", prompt_flag="-c"),
            output=CliOutputConfig(format="text"),
            errors=CliErrorConfig(success_exit_codes=[0]),
        ),
    )


class TestRealSubprocessRescue:
    """The unit tests above CRAFT ExecutionResults; these prove a REAL
    non-zero-exiting subprocess populates the very fields the rescue gates
    on (Law: a crafted result is only armor if it matches reality). obs1
    manifested with a real agent process, so the load-bearing claim is
    that a process which exits non-zero *normally* gets
    ``exit_reason == "completed"`` (not "error") and ``exit_signal is
    None`` — exactly what makes it rescuable."""

    async def test_real_nonzero_exit_is_completed_and_rescuable(self) -> None:
        backend = PluginCliBackend(_python_profile())
        result = await backend.execute("import sys; sys.exit(1)", timeout_seconds=30)

        # The real subprocess fields the rescue predicate depends on.
        assert result.success is False
        assert result.exit_code == 1
        assert result.exit_signal is None
        assert result.exit_reason == "completed"  # the crux: NOT "error"
        # Therefore the obs1 rescue fires for a real non-zero exit.
        assert _is_nonzero_exit_rescuable(result, has_validations=True) is True

    async def test_real_nonzero_exit_code_three_is_rescuable(self) -> None:
        # Mirrors the crafted exit_code=3 e2e test with a real process.
        backend = PluginCliBackend(_python_profile())
        result = await backend.execute("import sys; sys.exit(3)", timeout_seconds=30)

        assert result.success is False
        assert result.exit_code == 3
        assert result.exit_reason == "completed"
        assert _is_nonzero_exit_rescuable(result, has_validations=True) is True

    async def test_real_signal_kill_is_not_rescuable(self) -> None:
        # A process killed by a signal surfaces exit_signal — the negative
        # boundary, confirmed with a REAL signal rather than a crafted field.
        backend = PluginCliBackend(_python_profile())
        result = await backend.execute(
            "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
            timeout_seconds=30,
        )

        assert result.success is False
        assert result.exit_signal == signal.SIGKILL
        assert _is_nonzero_exit_rescuable(result, has_validations=True) is False


class TestObs1EndToEndStatusRecording:
    """obs1 was a BOUNDARY bug (two correct subsystems composing wrong): the
    backend reported success purely from exit code, and the musician skipped
    validation on a non-zero exit, so the daemon RECORDED a job whose work was
    done + committed as ``execution_fail``. This is the conductor-free
    'live-daemon rung': a REAL subprocess that commits work then exits
    non-zero, driven through the REAL musician, the result fed to a REAL
    BatonAdapter — asserting both the sheet status (COMPLETED) AND the daemon's
    job-level verdict (``_job_succeeded`` True, i.e. job COMPLETED not
    execution_fail — the exact symptom). The downstream registry write is a
    status-driven mapping (adapter/manager key off BatonSheetStatus, never
    re-inspecting exit_code — verified by inspection), so this covers the obs1
    decision and verdict with real components. The production-conductor repro
    is separately gated (its process predates the fix; no restart while a
    brand-stake job runs); this proves the same path deterministically, no LLM."""

    async def test_committed_work_nonzero_exit_job_succeeds(
        self, tmp_path: Path
    ) -> None:
        proof = tmp_path / "proof.txt"
        # Real subprocess: commit the work (write proof), THEN exit non-zero
        # — the exact obs1 shape, produced by a real process, not a mock.
        script = (
            f"open({str(proof)!r}, 'w').write('ok')\n"
            "import sys\n"
            "sys.exit(1)\n"
        )
        backend = PluginCliBackend(_python_profile())
        sheet = _sheet(
            [{"type": "command_succeeds", "command": f"test -f {proof}"}],
            tmp_path,
        )
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        await sheet_task(
            job_id="j1",
            sheet=sheet,
            backend=backend,
            attempt_context=AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL),
            inbox=inbox,
            rendered_prompt=script,  # bypass Jinja assembly; the script IS the body
        )
        result = inbox.get_nowait()

        # The real non-zero exit was rescued because the work's validation passed.
        assert result.execution_success is True
        assert result.validation_pass_rate == 100.0
        assert result.exit_code == 1  # the truth is preserved, not laundered
        assert proof.exists()  # the work really was committed by the subprocess

        # Feed the rescued attempt to the REAL adapter's baton and confirm both
        # the sheet status AND the daemon's job verdict.
        adapter = BatonAdapter()
        adapter._baton.register_instrument(sheet.instrument_name, max_concurrent=1)
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name=sheet.instrument_name)
        }
        sheets[1].status = BatonSheetStatus.DISPATCHED
        adapter._baton.register_job("j1", sheets, {})

        await adapter._baton.handle_event(result)

        recorded = adapter._baton.get_sheet_state("j1", 1)
        assert recorded is not None
        assert recorded.status == BatonSheetStatus.COMPLETED  # not FAILED

        # The job-level verdict — obs1's symptom was execution_fail HERE.
        job = adapter._baton._jobs["j1"]
        assert adapter._job_succeeded(job) is True
