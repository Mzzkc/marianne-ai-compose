"""Boundary bug analysis — Axiom M4 (Movement 1, Cycle 5).

Tests for three bugs found through backward-tracing invariant analysis:

F-118: ValidationEngine context gap between runner and baton musician.
    The runner passes rich sheet_context (workspace, total_sheets, movement,
    voice, etc.) to ValidationEngine. The baton musician passes only
    {"sheet_num": N}. Validations using {workspace} in paths fail under baton.

F-113: Permanently failed dependencies treated as "done" for DAG resolution.
    The parallel executor adds failed sheets to done_for_dag, allowing
    downstream sheets to run on incomplete input.

F-111: RateLimitExhaustedError lost in parallel executor.
    TaskGroup catches all exceptions as strings in error_details. The
    lifecycle handler sees FatalError, not RateLimitExhaustedError,
    so jobs FAIL instead of PAUSE.

TDD: red first, green second.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.config.execution import ValidationRule
from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.musician import _validate, sheet_task
from marianne.daemon.baton.state import AttemptContext, AttemptMode


# ─── Helpers ────────────────────────────────────────────────────────


def _make_sheet(
    *,
    num: int = 1,
    movement: int = 2,
    voice: int | None = 3,
    voice_count: int = 5,
    workspace: Path | None = None,
    instrument_name: str = "claude-code",
    prompt_template: str | None = "Do the work",
    variables: dict[str, Any] | None = None,
    validations: list[ValidationRule] | None = None,
) -> Sheet:
    """Create a test Sheet with rich identity."""
    return Sheet(
        num=num,
        movement=movement,
        voice=voice,
        voice_count=voice_count,
        workspace=workspace or Path("/tmp/test-workspace"),
        instrument_name=instrument_name,
        prompt_template=prompt_template,
        variables=variables or {"custom_var": "custom_value"},
        prelude=[],
        cadenza=[],
        validations=validations or [],
        prompt_extensions=[],
    )


def _success_result() -> MagicMock:
    """Create a successful ExecutionResult mock."""
    result = MagicMock()
    result.success = True
    result.exit_code = 0
    result.stdout = "output"
    result.stderr = ""
    result.rate_limited = False
    result.duration_seconds = 1.0
    result.input_tokens = 100
    result.output_tokens = 50
    result.model = "claude-sonnet"
    result.stdout_tail = "output"
    result.stderr_tail = ""
    return result


# ─── F-118: ValidationEngine context gap ────────────────────────────


class TestF118ValidationContextGap:
    """The baton musician must pass rich context to ValidationEngine,
    matching the runner's behavior."""

    @pytest.mark.asyncio
    async def test_validate_passes_workspace_to_engine(self) -> None:
        """ValidationEngine receives workspace from sheet context,
        not just sheet_num."""
        sheet = _make_sheet(
            workspace=Path("/tmp/my-workspace"),
            validations=[
                ValidationRule(
                    type="file_exists",
                    path="{workspace}/output.md",
                    description="Output file must exist",
                ),
            ],
        )
        exec_result = _success_result()

        # Mock ValidationEngine to capture the context it receives
        mock_engine = MagicMock()
        mock_result = MagicMock()
        mock_result.passed_count = 1
        mock_result.failed_count = 0
        mock_result.skipped_count = 0
        mock_result.pass_percentage = 100.0
        mock_result.results = [MagicMock()]
        mock_engine.run_validations = AsyncMock(return_value=mock_result)

        with patch(
            "marianne.execution.validation.engine.ValidationEngine"
        ) as MockVE:
            MockVE.return_value = mock_engine
            await _validate(
                sheet, exec_result,
                total_sheets=10, total_movements=3,
            )
            # Capture the sheet_context argument
            call_args = MockVE.call_args
            captured_context = (
                call_args[1].get("sheet_context")
                if "sheet_context" in call_args[1]
                else call_args[0][1] if len(call_args[0]) > 1 else {}
            )

        assert "workspace" in captured_context
        assert captured_context["workspace"] == "/tmp/my-workspace"

    @pytest.mark.asyncio
    async def test_validate_passes_total_sheets(self) -> None:
        """ValidationEngine receives total_sheets."""
        sheet = _make_sheet(
            validations=[
                ValidationRule(
                    type="file_exists",
                    path="{workspace}/output.md",
                    description="Output file",
                ),
            ],
        )
        exec_result = _success_result()

        with patch(
            "marianne.execution.validation.engine.ValidationEngine"
        ) as MockVE:
            mock_engine = MagicMock()
            mock_result = MagicMock()
            mock_result.passed_count = 1
            mock_result.failed_count = 0
            mock_result.skipped_count = 0
            mock_result.pass_percentage = 100.0
            mock_result.results = [MagicMock()]
            mock_engine.run_validations = AsyncMock(return_value=mock_result)
            MockVE.return_value = mock_engine

            await _validate(
                sheet, exec_result,
                total_sheets=42, total_movements=7,
            )

            call_args = MockVE.call_args
            ctx = (
                call_args[1].get("sheet_context")
                if "sheet_context" in call_args[1]
                else call_args[0][1] if len(call_args[0]) > 1 else {}
            )

        assert ctx["total_sheets"] == 42

    @pytest.mark.asyncio
    async def test_validate_passes_movement_and_voice(self) -> None:
        """ValidationEngine receives movement, voice, voice_count."""
        sheet = _make_sheet(
            movement=3, voice=2, voice_count=4,
            validations=[
                ValidationRule(
                    type="file_exists",
                    path="out.md",
                    description="Output",
                ),
            ],
        )
        exec_result = _success_result()

        with patch(
            "marianne.execution.validation.engine.ValidationEngine"
        ) as MockVE:
            mock_engine = MagicMock()
            mock_result = MagicMock()
            mock_result.passed_count = 1
            mock_result.failed_count = 0
            mock_result.skipped_count = 0
            mock_result.pass_percentage = 100.0
            mock_result.results = [MagicMock()]
            mock_engine.run_validations = AsyncMock(return_value=mock_result)
            MockVE.return_value = mock_engine

            await _validate(
                sheet, exec_result,
                total_sheets=10, total_movements=5,
            )

            call_args = MockVE.call_args
            ctx = (
                call_args[1].get("sheet_context")
                if "sheet_context" in call_args[1]
                else call_args[0][1] if len(call_args[0]) > 1 else {}
            )

        assert ctx["movement"] == 3
        assert ctx["voice"] == 2
        assert ctx["voice_count"] == 4

    @pytest.mark.asyncio
    async def test_validate_passes_custom_variables(self) -> None:
        """ValidationEngine receives score-level custom variables."""
        sheet = _make_sheet(
            variables={"persona": "reviewer", "output_dir": "reviews"},
            validations=[
                ValidationRule(
                    type="file_exists",
                    path="out.md",
                    description="Output",
                ),
            ],
        )
        exec_result = _success_result()

        with patch(
            "marianne.execution.validation.engine.ValidationEngine"
        ) as MockVE:
            mock_engine = MagicMock()
            mock_result = MagicMock()
            mock_result.passed_count = 1
            mock_result.failed_count = 0
            mock_result.skipped_count = 0
            mock_result.pass_percentage = 100.0
            mock_result.results = [MagicMock()]
            mock_engine.run_validations = AsyncMock(return_value=mock_result)
            MockVE.return_value = mock_engine

            await _validate(
                sheet, exec_result,
                total_sheets=10, total_movements=1,
            )

            call_args = MockVE.call_args
            ctx = (
                call_args[1].get("sheet_context")
                if "sheet_context" in call_args[1]
                else call_args[0][1] if len(call_args[0]) > 1 else {}
            )

        assert ctx["persona"] == "reviewer"
        assert ctx["output_dir"] == "reviews"

    @pytest.mark.asyncio
    async def test_validate_passes_old_terminology_aliases(self) -> None:
        """ValidationEngine receives stage/instance/fan_count aliases."""
        sheet = _make_sheet(
            movement=3, voice=2, voice_count=4,
            validations=[
                ValidationRule(
                    type="file_exists",
                    path="out.md",
                    description="Output",
                ),
            ],
        )
        exec_result = _success_result()

        with patch(
            "marianne.execution.validation.engine.ValidationEngine"
        ) as MockVE:
            mock_engine = MagicMock()
            mock_result = MagicMock()
            mock_result.passed_count = 1
            mock_result.failed_count = 0
            mock_result.skipped_count = 0
            mock_result.pass_percentage = 100.0
            mock_result.results = [MagicMock()]
            mock_engine.run_validations = AsyncMock(return_value=mock_result)
            MockVE.return_value = mock_engine

            await _validate(
                sheet, exec_result,
                total_sheets=10, total_movements=5,
            )

            call_args = MockVE.call_args
            ctx = (
                call_args[1].get("sheet_context")
                if "sheet_context" in call_args[1]
                else call_args[0][1] if len(call_args[0]) > 1 else {}
            )

        # Old terminology should be present
        assert ctx["stage"] == 3
        assert ctx["instance"] == 2
        assert ctx["fan_count"] == 4
        assert ctx["total_stages"] == 5

    @pytest.mark.asyncio
    async def test_validate_no_validations_still_returns_100(self) -> None:
        """F-018 contract: no validations -> 100% pass rate."""
        sheet = _make_sheet(validations=[])
        exec_result = _success_result()

        _, _, rate, _ = await _validate(
            sheet, exec_result,
            total_sheets=10, total_movements=1,
        )
        assert rate == 100.0

    @pytest.mark.asyncio
    async def test_validate_failed_execution_returns_zero(self) -> None:
        """Failed execution -> 0% pass rate."""
        sheet = _make_sheet(
            validations=[
                ValidationRule(
                    type="file_exists",
                    path="out.md",
                    description="Output",
                ),
            ],
        )
        exec_result = _success_result()
        exec_result.success = False

        _, _, rate, _ = await _validate(
            sheet, exec_result,
            total_sheets=10, total_movements=1,
        )
        assert rate == 0.0


# ─── F-113: Failed dependencies as "done" ──────────────────────────


@pytest.mark.skip(reason="Runner removed — ParallelExecutor no longer exists")
class TestF113FailedDepsAsDone:
    """Permanently failed sheets — obsolete after runner removal."""

    def test_permanently_failed_sheets_unblock_downstream(self) -> None:
        """CURRENT BUG: failed sheets are in done_for_dag, unblocking
        downstream sheets that depend on incomplete input.

        This test documents the bug. When fixed, the assertion should
        flip from 'downstream IS in batch' to 'downstream IS NOT in batch'."""
        from marianne.core.checkpoint import SheetStatus
        from marianne.execution.parallel import ParallelExecutor

        config = MagicMock()
        config.max_concurrent = 4

        runner = MagicMock()
        # Set up DAG via the runner's dependency_dag property
        dag = MagicMock()
        dag.get_ready_sheets.return_value = [2]
        runner.dependency_dag = dag

        executor = ParallelExecutor(runner, config)

        # Mark sheet 1 as permanently failed
        executor._permanently_failed.add(1)

        # Create state where sheet 1 is failed, sheet 2 is pending
        state = MagicMock()
        state.sheets = {
            1: MagicMock(status=SheetStatus.FAILED),
            2: MagicMock(status=SheetStatus.PENDING),
        }

        batch = executor.get_next_parallel_batch(state)

        # BUG DOCUMENTED: downstream sheet 2 IS in the batch even though
        # its dependency (sheet 1) failed. This is F-113.
        assert 2 in batch

    def test_completed_sheets_correctly_unblock_downstream(self) -> None:
        """Completed sheets should unblock downstream."""
        from marianne.core.checkpoint import SheetStatus
        from marianne.execution.parallel import ParallelExecutor

        config = MagicMock()
        config.max_concurrent = 4

        runner = MagicMock()
        dag = MagicMock()
        dag.get_ready_sheets.return_value = [2]
        runner.dependency_dag = dag

        executor = ParallelExecutor(runner, config)

        state = MagicMock()
        state.sheets = {
            1: MagicMock(status=SheetStatus.COMPLETED),
            2: MagicMock(status=SheetStatus.PENDING),
        }

        batch = executor.get_next_parallel_batch(state)
        assert 2 in batch


# ─── F-111: RateLimitExhaustedError lost in parallel ────────────────


class TestF111RateLimitLostInParallel:
    """The parallel executor catches RateLimitExhaustedError as generic
    exception and stores only the message string. Jobs FAIL not PAUSE."""

    @pytest.mark.skip(reason="Runner removed — ParallelBatchResult no longer exists")
    def test_error_details_loses_exception_type(self) -> None:
        """Obsolete: ParallelBatchResult no longer exists."""

    def test_lifecycle_raises_fatal_not_rate_limit(self) -> None:
        """lifecycle.py:1169 raises FatalError, not RateLimitExhaustedError."""
        from marianne.core.errors import (
            FatalError,
            RateLimitExhaustedError,
        )

        error_msg = (
            "RateLimitExhaustedError: Exceeded maximum rate limit waits (5)"
        )
        fatal = FatalError(f"Sheet 1 failed: {error_msg}")

        assert not isinstance(fatal, RateLimitExhaustedError)
        assert isinstance(fatal, FatalError)

    def test_rate_limit_error_has_resume_after(self) -> None:
        """RateLimitExhaustedError carries resume_after data that is lost."""
        from datetime import UTC, datetime, timedelta

        from marianne.core.errors import RateLimitExhaustedError

        resume_at = datetime.now(UTC) + timedelta(seconds=3600)
        err = RateLimitExhaustedError(
            "Rate limit hit",
            resume_after=resume_at,
            backend_type="claude-cli",
        )

        assert err.resume_after == resume_at
        assert err.backend_type == "claude-cli"

        stored = f"{type(err).__name__}: {err}"
        assert "resume_after" not in stored


# ─── Integration: sheet_task passes context through ─────────────────


class TestSheetTaskValidationContext:
    """Integration: sheet_task threads totals to _validate."""

    @pytest.mark.asyncio
    async def test_sheet_task_threads_totals_to_validate(self) -> None:
        """sheet_task passes total_sheets and total_movements to
        _validate and ultimately ValidationEngine."""
        sheet = _make_sheet(
            validations=[
                ValidationRule(
                    type="file_exists",
                    path="{workspace}/output.md",
                    description="Output",
                ),
            ],
        )
        exec_result = _success_result()

        backend = AsyncMock()
        backend.execute = AsyncMock(return_value=exec_result)
        backend.name = "claude-code"

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        ctx = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
        )

        with patch(
            "marianne.execution.validation.engine.ValidationEngine"
        ) as MockVE:
            mock_engine = MagicMock()
            mock_result = MagicMock()
            mock_result.passed_count = 1
            mock_result.failed_count = 0
            mock_result.skipped_count = 0
            mock_result.pass_percentage = 100.0
            mock_result.results = [MagicMock()]
            mock_engine.run_validations = AsyncMock(return_value=mock_result)
            MockVE.return_value = mock_engine

            await sheet_task(
                job_id="test-job",
                sheet=sheet,
                backend=backend,
                attempt_context=ctx,
                inbox=inbox,
                total_sheets=42,
                total_movements=7,
            )

            call_args = MockVE.call_args
            ctx_dict = (
                call_args[1].get("sheet_context")
                if "sheet_context" in call_args[1]
                else call_args[0][1] if len(call_args[0]) > 1 else {}
            )

        assert ctx_dict["total_sheets"] == 42
        assert ctx_dict["total_movements"] == 7
        assert ctx_dict["workspace"] == str(sheet.workspace)
        assert ctx_dict["movement"] == 2
        assert ctx_dict["voice"] == 3
