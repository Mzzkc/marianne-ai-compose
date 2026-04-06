"""Tests for the baton musician — single-attempt sheet execution.

The musician is the bridge between the baton's dispatch decision and the
backend's execution. It executes ONE attempt, runs validations, records
outcomes, and reports back via SheetAttemptResult. It never retries.

TDD: Tests written before implementation. Red first, then green.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.backends.base import ExecutionResult
from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import AttemptContext, AttemptMode


# =========================================================================
# Helpers
# =========================================================================


def _make_sheet(
    num: int = 1,
    instrument: str = "claude-code",
    prompt: str = "Write hello world",
    validations: list[Any] | None = None,
    workspace: str = "/tmp/test-ws",
    timeout: float = 60.0,
) -> Sheet:
    """Create a minimal Sheet for testing."""
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=1,
        instrument_name=instrument,
        workspace=Path(workspace),
        prompt_template=prompt,
        validations=validations or [],
        timeout_seconds=timeout,
    )


def _make_context(
    attempt: int = 1,
    mode: AttemptMode = AttemptMode.NORMAL,
) -> AttemptContext:
    """Create a minimal AttemptContext for testing."""
    return AttemptContext(attempt_number=attempt, mode=mode)


def _make_execution_result(
    success: bool = True,
    stdout: str = "Hello world",
    stderr: str = "",
    duration: float = 1.5,
    exit_code: int | None = 0,
    rate_limited: bool = False,
    model: str | None = "claude-sonnet-4-6",
    input_tokens: int | None = 100,
    output_tokens: int | None = 50,
) -> ExecutionResult:
    """Create an ExecutionResult for testing."""
    return ExecutionResult(
        success=success,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
        exit_code=exit_code,
        rate_limited=rate_limited,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


# =========================================================================
# Test: Happy path — successful execution with no validations
# =========================================================================


class TestMusicianHappyPath:
    """Test the normal execution flow — backend succeeds, no validations."""

    @pytest.mark.asyncio
    async def test_successful_execution_reports_completed(self) -> None:
        """A successful execution with no validations produces a 100% pass result."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(return_value=_make_execution_result())
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        assert not inbox.empty()
        result = inbox.get_nowait()
        assert isinstance(result, SheetAttemptResult)
        assert result.job_id == "test-job"
        assert result.sheet_num == 1
        assert result.instrument_name == "claude-code"
        assert result.execution_success is True
        assert result.validation_pass_rate == 100.0
        assert result.validations_total == 0
        assert result.validations_passed == 0
        assert result.rate_limited is False
        assert result.attempt == 1
        assert result.duration_seconds > 0
        assert result.error_classification is None

    @pytest.mark.asyncio
    async def test_backend_execute_called_with_prompt(self) -> None:
        """The musician passes the sheet's prompt to the backend."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(return_value=_make_execution_result())
        backend.name = "claude-code"

        sheet = _make_sheet(prompt="Build a calculator")
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        backend.execute.assert_called_once()
        call_args = backend.execute.call_args
        # The prompt argument should contain the sheet's prompt
        assert "Build a calculator" in call_args.args[0] or "Build a calculator" in str(call_args)

    @pytest.mark.asyncio
    async def test_cost_tracking_from_token_counts(self) -> None:
        """Cost is estimated from token counts when backend doesn't provide it."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(
                input_tokens=1000, output_tokens=500
            )
        )
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.input_tokens == 1000
        assert result.output_tokens == 500
        # Cost should be non-negative
        assert result.cost_usd >= 0.0


# =========================================================================
# Test: Execution failure
# =========================================================================


class TestMusicianExecutionFailure:
    """Test behavior when the backend fails."""

    @pytest.mark.asyncio
    async def test_execution_failure_reports_failure(self) -> None:
        """A failed execution is reported with execution_success=False."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(
                success=False,
                exit_code=1,
                stderr="Error: permission denied",
            )
        )
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.execution_success is False
        assert result.exit_code == 1
        assert result.rate_limited is False
        assert "permission denied" in result.stderr_tail

    @pytest.mark.asyncio
    async def test_rate_limited_execution_reported(self) -> None:
        """Rate-limited execution is flagged as rate_limited, not a failure."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(
                success=False,
                exit_code=1,
                rate_limited=True,
                stderr="Rate limit exceeded, retry after 60s",
            )
        )
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.rate_limited is True
        assert result.instrument_name == "claude-code"

    @pytest.mark.asyncio
    async def test_backend_exception_still_reports(self) -> None:
        """If the backend throws an exception, the musician still reports."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(side_effect=RuntimeError("Backend crashed"))
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.execution_success is False
        assert result.error_message is not None
        assert "Backend crashed" in result.error_message
        # Must never crash the baton — always report
        assert result.job_id == "test-job"

    @pytest.mark.asyncio
    async def test_exit_code_none_process_killed(self) -> None:
        """exit_code=None (process killed by signal) is reported correctly."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(
                success=False,
                exit_code=None,
                stderr="",
            )
        )
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.execution_success is False
        assert result.exit_code is None
        assert result.error_classification == "TRANSIENT"


# =========================================================================
# Test: Validation integration
# =========================================================================


class TestMusicianValidation:
    """Test the musician's validation execution."""

    @pytest.mark.asyncio
    async def test_no_validations_yields_100_percent(self) -> None:
        """Sheets with no validation rules report 100% pass rate (F-018 contract)."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(return_value=_make_execution_result())
        backend.name = "claude-code"

        sheet = _make_sheet(validations=[])
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        # F-018 contract: no validations = 100% pass rate
        assert result.validation_pass_rate == 100.0
        assert result.validations_total == 0
        assert result.validations_passed == 0

    @pytest.mark.asyncio
    async def test_validations_skipped_on_execution_failure(self) -> None:
        """Validations are not run when execution fails."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(success=False, exit_code=1)
        )
        backend.name = "claude-code"

        # Sheet has validations but execution failed — should skip them
        from marianne.core.config.execution import ValidationRule

        rule = ValidationRule(type="file_exists", path="output.txt")
        sheet = _make_sheet(validations=[rule])
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.execution_success is False
        assert result.validation_pass_rate == 0.0
        assert result.validations_total == 0  # Not run


# =========================================================================
# Test: Attempt context propagation
# =========================================================================


class TestMusicianAttemptContext:
    """Test that attempt context is correctly propagated."""

    @pytest.mark.asyncio
    async def test_attempt_number_in_result(self) -> None:
        """The attempt number from context appears in the result."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(return_value=_make_execution_result())
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context(attempt=3)

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.attempt == 3

    @pytest.mark.asyncio
    async def test_model_used_from_execution(self) -> None:
        """The model actually used is captured from the execution result."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(model="claude-opus-4-6")
        )
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.model_used == "claude-opus-4-6"


# =========================================================================
# Test: Output truncation and credential redaction
# =========================================================================


class TestMusicianOutputHandling:
    """Test output truncation and credential redaction."""

    @pytest.mark.asyncio
    async def test_stdout_tail_captured(self) -> None:
        """Stdout tail is included in the result for diagnostics."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout="Output line 1\nOutput line 2")
        )
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert "Output line" in result.stdout_tail

    @pytest.mark.asyncio
    async def test_credentials_redacted_in_output(self) -> None:
        """Credentials in stdout are redacted before reporting."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(
                stdout="API key: sk-ant-api03-abcdefghij1234567890abcdef"
            )
        )
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        # The credential scanner should redact the API key
        assert "sk-ant-api03" not in result.stdout_tail
        assert "REDACTED" in result.stdout_tail

    @pytest.mark.asyncio
    async def test_timeout_passed_to_backend(self) -> None:
        """Sheet timeout_seconds is passed to backend.execute."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(return_value=_make_execution_result())
        backend.name = "claude-code"

        sheet = _make_sheet(timeout=120.0)
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        backend.execute.assert_called_once()
        call_kwargs = backend.execute.call_args.kwargs
        assert call_kwargs.get("timeout_seconds") == 120.0


# =========================================================================
# Test: Duration tracking
# =========================================================================


class TestMusicianTiming:
    """Test that the musician tracks execution duration correctly."""

    @pytest.mark.asyncio
    async def test_duration_from_execution_result(self) -> None:
        """Duration is taken from the execution result."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(duration=42.5)
        )
        backend.name = "claude-code"

        sheet = _make_sheet()
        context = _make_context()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=context,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.duration_seconds == 42.5
