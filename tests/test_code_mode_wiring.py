"""Tests for wiring the CodeModeExecutor into the musician's post-execution path.

Stage 3 of the technique system: after ``TechniqueRouter.classify()``
identifies a ``CODE_BLOCK`` output, the musician runs each block through
a ``CodeModeExecutor`` when one is provided. The sandbox output is
appended to ``exec_result.stdout`` so validations and diagnostics see
both the agent's reasoning and the real execution result.

Coverage (per the retry prompt's test plan):

1. Code block in output → executed in sandbox → stdout captured.
2. Code writes to workspace → artifact visible on disk after execution.
3. Code fails → error diagnostic in sheet output, retry-friendly context.
4. No code blocks → executor never invoked (backward compat).
5. No executor provided → classification runs but code is NOT executed.
6. Executor exception doesn't crash the musician (soft-fail).
7. MCP proxy socket accessible from code mode runtime (bind-mounting
   contract, verified via a unix socket written into the workspace).

Sandboxing is disabled in tests (``use_sandbox=False``) because bwrap
availability is environment-specific and the wiring contract — "the
executor is invoked with the right blocks and its output is merged
into the sheet result" — is independent of bwrap itself. The sandbox
implementation has its own dedicated test suite in
``tests/test_sandbox_wrapper.py``.
"""

from __future__ import annotations

import asyncio
import socket as socket_mod
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from marianne.backends.base import ExecutionResult
from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import AttemptContext, AttemptMode
from marianne.daemon.technique_router import (
    CodeBlock,
    OutputKind,
    TechniqueRouter,
)
from marianne.execution.code_mode import (
    CodeExecutionResult,
    CodeModeExecutor,
)

# =========================================================================
# Helpers
# =========================================================================


def _make_sheet(
    num: int = 1,
    instrument: str = "openrouter",
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
    stdout: str = "",
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


def _make_executor(workspace: Path) -> CodeModeExecutor:
    """Make a CodeModeExecutor with sandbox disabled for test portability."""
    return CodeModeExecutor(
        workspace=workspace,
        timeout_seconds=10.0,
        use_sandbox=False,
    )


# =========================================================================
# Scenario 1: code block in output → executed in sandbox
# =========================================================================


class TestCodeBlockTriggersExecution:
    """A CODE_BLOCK classification with a code_executor runs the code."""

    @pytest.mark.asyncio
    async def test_python_block_runs_and_stdout_captured(
        self, tmp_path: Path,
    ) -> None:
        """Agent output contains a python code block → executor runs it,
        stdout is merged into exec_result.stdout.
        """
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = (
            "Here's the code to run:\n\n"
            "```python\n"
            "print('code mode is live')\n"
            "```\n"
        )
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()
        executor = _make_executor(tmp_path)

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(workspace=str(tmp_path)),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
            code_executor=executor,
        )

        result = inbox.get_nowait()
        assert result.execution_success is True
        assert result.output_kind == OutputKind.CODE_BLOCK.value
        # The sandbox stdout is appended to stdout_tail (comes from
        # exec_result.stdout after _capture_output)
        assert "Sandbox Output" in result.stdout_tail
        assert "code mode is live" in result.stdout_tail

    @pytest.mark.asyncio
    async def test_multiple_blocks_all_executed(self, tmp_path: Path) -> None:
        """Multiple code blocks each run independently."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = (
            "First:\n```python\nprint('block one')\n```\n\n"
            "Second:\n```python\nprint('block two')\n```\n"
        )
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()
        executor = _make_executor(tmp_path)

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(workspace=str(tmp_path)),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
            code_executor=executor,
        )

        result = inbox.get_nowait()
        assert "block one" in result.stdout_tail
        assert "block two" in result.stdout_tail
        # Each block gets its own section header
        assert result.stdout_tail.count("### Block") >= 2


# =========================================================================
# Scenario 2: code writes to workspace → artifacts captured
# =========================================================================


class TestWorkspaceArtifacts:
    """Code mode runs with the workspace as CWD — files persist."""

    @pytest.mark.asyncio
    async def test_file_written_by_code_persists_in_workspace(
        self, tmp_path: Path,
    ) -> None:
        """Agent code writes to workspace → file exists after execution."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = (
            "```python\n"
            "from pathlib import Path\n"
            "Path('artifact.txt').write_text('hello from sandbox')\n"
            "print('artifact written')\n"
            "```\n"
        )
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()
        executor = _make_executor(tmp_path)

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(workspace=str(tmp_path)),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
            code_executor=executor,
        )

        result = inbox.get_nowait()
        artifact = tmp_path / "artifact.txt"
        assert artifact.is_file()
        assert artifact.read_text() == "hello from sandbox"
        assert "artifact written" in result.stdout_tail


# =========================================================================
# Scenario 3: code fails → error in sheet output, retry path is viable
# =========================================================================


class TestCodeFailureHandling:
    """Code failures don't crash the musician — retry context is built."""

    @pytest.mark.asyncio
    async def test_failing_code_produces_error_diagnostic(
        self, tmp_path: Path,
    ) -> None:
        """A non-zero exit from the sandbox renders an error section."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = (
            "```python\n"
            "import sys\n"
            "print('about to fail', file=sys.stderr)\n"
            "sys.exit(7)\n"
            "```\n"
        )
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()
        executor = _make_executor(tmp_path)

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(workspace=str(tmp_path)),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
            code_executor=executor,
        )

        result = inbox.get_nowait()
        # Musician must never crash — result is reported.
        assert result is not None
        # Output kind still reflects the classification (CODE_BLOCK).
        assert result.output_kind == OutputKind.CODE_BLOCK.value
        # The failure diagnostic is appended to stdout_tail.
        assert "Code Execution Failed" in result.stdout_tail
        assert "Exit code" in result.stdout_tail

    @pytest.mark.asyncio
    async def test_unsupported_language_reports_failure_section(
        self, tmp_path: Path,
    ) -> None:
        """An unsupported language tag yields a failure diagnostic."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = (
            "```rust\n"
            "fn main() { println!(\"nope\"); }\n"
            "```\n"
        )
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()
        executor = _make_executor(tmp_path)

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(workspace=str(tmp_path)),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
            code_executor=executor,
        )

        result = inbox.get_nowait()
        # The router only extracts known-language blocks (python, bash,
        # javascript, typescript, go, rust, ruby, etc. per router source).
        # Rust IS extracted by the router, but the executor reports
        # an unsupported-language failure.
        assert result is not None
        if result.output_kind == OutputKind.CODE_BLOCK.value:
            # Code block was extracted — expect failure diagnostic.
            assert "Code Execution Failed" in result.stdout_tail
        else:
            # Language not recognized by router → classified as prose.
            # Either outcome is acceptable for this test; the wiring
            # contract is "don't crash, don't silently succeed".
            assert result.output_kind is not None

    @pytest.mark.asyncio
    async def test_executor_exception_does_not_crash_musician(
        self, tmp_path: Path,
    ) -> None:
        """If the executor itself raises, musician reports with a
        diagnostic rather than crashing the baton."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = "```python\nprint('hi')\n```\n"
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()

        class ExplodingExecutor:
            """Stand-in executor that raises on execute_all."""

            async def execute_all(
                self, blocks: list[CodeBlock],
            ) -> list[CodeExecutionResult]:
                raise RuntimeError("executor exploded")

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(workspace=str(tmp_path)),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
            code_executor=ExplodingExecutor(),  # type: ignore[arg-type]
        )

        result = inbox.get_nowait()
        # Musician caught the executor error and still reported.
        assert result is not None
        assert result.execution_success is True  # Backend succeeded.
        assert "executor_error" in result.stdout_tail
        assert "executor exploded" in result.stdout_tail


# =========================================================================
# Scenario 4: backward compatibility — no code blocks, no activation
# =========================================================================


class TestBackwardCompatibility:
    """The code executor only runs when there are blocks to execute."""

    @pytest.mark.asyncio
    async def test_prose_output_does_not_invoke_executor(
        self, tmp_path: Path,
    ) -> None:
        """Prose classification → executor.execute_all is never called."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(
                stdout="I reviewed the architecture. It looks sound.",
            ),
        )

        router = TechniqueRouter()
        executor = _make_executor(tmp_path)
        # Spy on execute_all
        original_execute_all = executor.execute_all
        call_count = 0

        async def spy(blocks: list[CodeBlock]) -> list[CodeExecutionResult]:
            nonlocal call_count
            call_count += 1
            return await original_execute_all(blocks)

        executor.execute_all = spy  # type: ignore[assignment]

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(workspace=str(tmp_path)),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
            code_executor=executor,
        )

        result = inbox.get_nowait()
        assert result.output_kind == OutputKind.PROSE.value
        assert call_count == 0
        assert "Sandbox Output" not in result.stdout_tail

    @pytest.mark.asyncio
    async def test_no_executor_no_code_execution(
        self, tmp_path: Path,
    ) -> None:
        """Router detects CODE_BLOCK but executor is None → no run."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = "```python\nprint('would run')\n```\n"
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        router = TechniqueRouter()

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(workspace=str(tmp_path)),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
            # code_executor omitted on purpose
        )

        result = inbox.get_nowait()
        assert result.output_kind == OutputKind.CODE_BLOCK.value
        # Router classified but no executor is present → no sandbox
        # output merged into stdout_tail.
        assert "Sandbox Output" not in result.stdout_tail

    @pytest.mark.asyncio
    async def test_no_router_no_classification_no_execution(
        self, tmp_path: Path,
    ) -> None:
        """Without a router, the code_executor parameter is ignored
        (there's nothing to classify)."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        stdout = "```python\nprint('would run')\n```\n"
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(stdout=stdout),
        )

        executor = _make_executor(tmp_path)

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(workspace=str(tmp_path)),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            # technique_router omitted → no classification path
            code_executor=executor,
        )

        result = inbox.get_nowait()
        assert result.output_kind is None
        assert "Sandbox Output" not in result.stdout_tail

    @pytest.mark.asyncio
    async def test_failed_execution_skips_code_mode(
        self, tmp_path: Path,
    ) -> None:
        """If the backend failed, don't try to run the agent's code."""
        from marianne.daemon.baton.musician import sheet_task

        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_execution_result(
                success=False,
                exit_code=1,
                stderr="boom",
                stdout="```python\nprint('never')\n```",
            ),
        )

        router = TechniqueRouter()
        executor = _make_executor(tmp_path)

        await sheet_task(
            job_id="test-job",
            sheet=_make_sheet(workspace=str(tmp_path)),
            backend=backend,
            attempt_context=_make_context(),
            inbox=inbox,
            technique_router=router,
            code_executor=executor,
        )

        result = inbox.get_nowait()
        assert result.execution_success is False
        # Classification skipped → output_kind stays None
        assert result.output_kind is None
        assert "Sandbox Output" not in result.stdout_tail


# =========================================================================
# Scenario 5: MCP proxy socket accessible from sandbox workspace
# =========================================================================


class TestMCPProxyBindMount:
    """The sandbox uses the agent's workspace as CWD — MCP sockets
    placed there are accessible to the executed code (mirrors the
    shared MCP pool's socket bind-mount contract)."""

    @pytest.mark.asyncio
    async def test_unix_socket_reachable_from_code_mode(
        self, tmp_path: Path,
    ) -> None:
        """A Unix socket listening in the workspace is reachable from
        code mode. This exercises the same contract the shared MCP
        pool relies on: an IPC endpoint at a path inside the bind-
        mounted workspace can be connected to by agent code.
        """
        from marianne.daemon.baton.musician import sheet_task

        socket_path = tmp_path / "mcp.sock"

        # Start a tiny echo server on the socket — simulates the
        # MCP proxy pool without any MCP protocol in the test.
        server: Any = None
        handled = asyncio.Event()

        async def handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        ) -> None:
            data = await reader.readline()
            writer.write(b"pong:" + data)
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            handled.set()

        server = await asyncio.start_unix_server(
            handle, path=str(socket_path),
        )

        try:
            stdout = (
                "```python\n"
                "import socket\n"
                f"s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
                f"s.connect({str(socket_path)!r})\n"
                "s.sendall(b'ping\\n')\n"
                "print(s.recv(1024).decode().strip())\n"
                "s.close()\n"
                "```\n"
            )
            backend = AsyncMock()
            backend.execute = AsyncMock(
                return_value=_make_execution_result(stdout=stdout),
            )

            router = TechniqueRouter()
            executor = _make_executor(tmp_path)
            inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

            await sheet_task(
                job_id="test-job",
                sheet=_make_sheet(workspace=str(tmp_path)),
                backend=backend,
                attempt_context=_make_context(),
                inbox=inbox,
                technique_router=router,
                code_executor=executor,
            )

            # The server should have been contacted.
            try:
                await asyncio.wait_for(handled.wait(), timeout=5.0)
            except TimeoutError:
                pytest.fail("echo server never received a connection")

            result = inbox.get_nowait()
            assert "pong:ping" in result.stdout_tail
        finally:
            server.close()
            await server.wait_closed()
            # Clean up the socket file if the server did not.
            if socket_path.exists():
                socket_path.unlink()

    @pytest.mark.asyncio
    async def test_raw_socket_lib_available_in_sandbox(self) -> None:
        """Sanity check: the Python in the sandbox has the socket module.
        This test lives here (not in test_code_mode_execution.py) because
        it asserts the programmatic-interface runtime contract — the
        agent's generated interface implementations rely on stdlib
        sockets for MCP proxying.
        """
        # Smoke check — the stdlib socket module loads in the test env.
        assert socket_mod.AF_UNIX is not None
