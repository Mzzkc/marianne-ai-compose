"""Tests for wiring the TechniqueRouter into the musician's post-execution path.

Stage 2a of the technique system: after ``backend.execute()`` returns a
successful result, the musician classifies the output via
``TechniqueRouter.classify()`` when a router is provided.

The router is instantiated per-job in ``BatonAdapter`` only when the job
declares techniques. For jobs without techniques, the router is not
activated and the musician behaves exactly as before (backward compat).

Classification is observational in Stage 2a — the musician stores the
``OutputKind`` on the ``SheetAttemptResult`` so downstream stages
(Stage 3 code-mode executor, Stage 5 A2A routing) can act on it.

TDD: tests written first, then implementation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from marianne.backends.base import ExecutionResult
from marianne.core.config.techniques import TechniqueConfig, TechniqueKind
from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import AttemptContext, AttemptMode
from marianne.daemon.technique_router import OutputKind, TechniqueRouter


# =========================================================================
# Helpers
# =========================================================================


def _make_sheet(
    num: int = 1,
    instrument: str = "claude-code",
    prompt: str = "Do the thing",
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
) -> ExecutionResult:
    """Create an ExecutionResult for testing."""
    return ExecutionResult(
        success=success,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
        exit_code=exit_code,
        rate_limited=False,
        model="test-model",
        input_tokens=10,
        output_tokens=5,
    )


# =========================================================================
# sheet_task accepts an optional router and classifies output
# =========================================================================


class TestSheetTaskRouterParam:
    """The musician accepts an optional technique_router parameter."""

    @pytest.mark.asyncio
    async def test_prose_output_classified_as_prose(self) -> None:
        """Plain prose output is classified as PROSE."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(
                stdout="I reviewed the architecture and it looks sound.",
            )
        )

        router = TechniqueRouter()

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
        )

        result = inbox.get_nowait()
        assert result.execution_success is True
        assert result.output_kind == OutputKind.PROSE.value

    @pytest.mark.asyncio
    async def test_python_code_fence_classified_as_code_block(self) -> None:
        """A python code fence is classified as CODE_BLOCK."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = (
            "Here's the code to list issues:\n\n"
            "```python\n"
            "issues = github.list_issues(state='open')\n"
            "for i in issues:\n"
            "    print(i.title)\n"
            "```\n"
        )
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
        )

        result = inbox.get_nowait()
        assert result.execution_success is True
        assert result.output_kind == OutputKind.CODE_BLOCK.value

    @pytest.mark.asyncio
    async def test_bash_code_fence_classified_as_code_block(self) -> None:
        """Bash code fences are also classified as CODE_BLOCK."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = (
            "Run these commands:\n\n"
            "```bash\n"
            "ls -la\n"
            "echo done\n"
            "```\n"
        )
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
        )

        result = inbox.get_nowait()
        assert result.output_kind == OutputKind.CODE_BLOCK.value

    @pytest.mark.asyncio
    async def test_tool_call_classified_as_tool_call(self) -> None:
        """A @tool directive is classified as TOOL_CALL."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = '@tool github.list_issues(state="open")'
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
        )

        result = inbox.get_nowait()
        assert result.output_kind == OutputKind.TOOL_CALL.value

    @pytest.mark.asyncio
    async def test_a2a_delegate_classified_as_a2a_request(self) -> None:
        """A @delegate directive is classified as A2A_REQUEST."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = "@delegate sentinel: review security posture of module X"
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
        )

        result = inbox.get_nowait()
        assert result.output_kind == OutputKind.A2A_REQUEST.value


# =========================================================================
# Backward compatibility — no router, no classification
# =========================================================================


class TestSheetTaskRouterOptional:
    """When no router is provided, the musician behaves as before."""

    @pytest.mark.asyncio
    async def test_no_router_leaves_output_kind_none(self) -> None:
        """Omitting technique_router leaves output_kind unset (None)."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(
                stdout="```python\nprint('hi')\n```",
            )
        )

        # No technique_router passed
        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.execution_success is True
        assert result.output_kind is None

    @pytest.mark.asyncio
    async def test_failed_execution_skips_classification(self) -> None:
        """When execution fails, classification is skipped."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(
                success=False,
                exit_code=1,
                stderr="boom",
                stdout="```python\nprint('hi')\n```",
            )
        )

        router = TechniqueRouter()

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
        )

        result = inbox.get_nowait()
        # On failure, classification is not relevant — stay None
        assert result.execution_success is False
        assert result.output_kind is None


# =========================================================================
# Multi-segment outputs — mixed content classified by priority
# =========================================================================


class TestMultiSegmentClassification:
    """Outputs containing multiple patterns follow priority rules."""

    @pytest.mark.asyncio
    async def test_a2a_wins_over_code_block(self) -> None:
        """A2A takes priority when output has both @delegate and code fence."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = (
            "@delegate forge: implement the fix\n\n"
            "```python\n"
            "pass\n"
            "```\n"
        )
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
        )

        result = inbox.get_nowait()
        # Priority: A2A > tool > code > prose
        assert result.output_kind == OutputKind.A2A_REQUEST.value

    @pytest.mark.asyncio
    async def test_tool_wins_over_code_block(self) -> None:
        """Tool call wins over code block when both present."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = (
            '@tool github.list_issues(state="open")\n\n'
            "```python\n"
            "print('also here')\n"
            "```\n"
        )
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
        )

        result = inbox.get_nowait()
        assert result.output_kind == OutputKind.TOOL_CALL.value


# =========================================================================
# BatonAdapter instantiates router per-job based on techniques
# =========================================================================


class TestBatonAdapterRouterActivation:
    """The adapter activates a router only when the job declares techniques."""

    def test_router_activated_when_techniques_declared(self) -> None:
        """register_job with techniques stores a per-job router."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets: list[Sheet] = [_make_sheet()]

        techniques = {
            "a2a": TechniqueConfig(
                kind=TechniqueKind.PROTOCOL,
                phases=["all"],
            ),
        }

        adapter.register_job(
            "job-with-techniques",
            sheets,
            {1: []},
            techniques=techniques,
        )

        router = adapter.get_router("job-with-techniques")
        assert router is not None
        assert isinstance(router, TechniqueRouter)

    def test_router_not_activated_when_no_techniques(self) -> None:
        """register_job without techniques leaves the router unset."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets: list[Sheet] = [_make_sheet()]

        # No techniques argument → router not created (backward compat)
        adapter.register_job(
            "job-no-techniques",
            sheets,
            {1: []},
        )

        router = adapter.get_router("job-no-techniques")
        assert router is None

    def test_router_removed_on_deregister(self) -> None:
        """deregister_job cleans up the job's router."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets: list[Sheet] = [_make_sheet()]

        techniques = {
            "github": TechniqueConfig(
                kind=TechniqueKind.MCP,
                phases=["work"],
            ),
        }

        adapter.register_job(
            "job-1",
            sheets,
            {1: []},
            techniques=techniques,
        )

        assert adapter.get_router("job-1") is not None

        adapter.deregister_job("job-1")

        assert adapter.get_router("job-1") is None

    def test_empty_techniques_dict_does_not_activate_router(self) -> None:
        """An empty techniques dict is treated as no techniques."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets: list[Sheet] = [_make_sheet()]

        adapter.register_job(
            "job-empty",
            sheets,
            {1: []},
            techniques={},
        )

        assert adapter.get_router("job-empty") is None
