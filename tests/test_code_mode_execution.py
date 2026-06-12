"""Tests for code mode execution — sandbox routing, error handling.

Tests cover:
- CodeModeExecutor: Python, bash execution
- Timeout handling
- Error cases (unsupported language, empty code, bad interpreter)
- Result structure and status codes
- render_code_mode_error formatting
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.daemon.technique_router import CodeBlock
from marianne.execution.code_mode import (
    CodeExecutionResult,
    CodeExecutionStatus,
    CodeModeExecutor,
    render_code_mode_error,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def executor(workspace: Path) -> CodeModeExecutor:
    """Executor with sandbox disabled (no bwrap needed in tests)."""
    return CodeModeExecutor(
        workspace=workspace,
        timeout_seconds=10.0,
        use_sandbox=False,
    )


# =============================================================================
# Python execution
# =============================================================================


class TestPythonExecution:
    """Execute Python code blocks."""

    async def test_simple_print(self, executor: CodeModeExecutor) -> None:
        """Simple print statement runs and captures output."""
        block = CodeBlock(language="python", code='print("hello world")')
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.SUCCESS
        assert result.exit_code == 0
        assert "hello world" in result.stdout

    async def test_computation(self, executor: CodeModeExecutor) -> None:
        """Python computation produces correct output."""
        block = CodeBlock(language="python", code="print(2 + 2)")
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.SUCCESS
        assert "4" in result.stdout

    async def test_file_creation(
        self,
        executor: CodeModeExecutor,
        workspace: Path,
    ) -> None:
        """Python code can write files to the workspace."""
        code = """
from pathlib import Path
Path("output.txt").write_text("hello from code mode")
print("file written")
"""
        block = CodeBlock(language="python", code=code)
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.SUCCESS
        output_file = workspace / "output.txt"
        assert output_file.exists()
        assert output_file.read_text() == "hello from code mode"

    async def test_syntax_error(self, executor: CodeModeExecutor) -> None:
        """Python syntax errors are captured as failures."""
        block = CodeBlock(language="python", code="def invalid(:")
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.FAILURE
        assert result.exit_code is not None
        assert result.exit_code != 0
        assert result.stderr  # Python reports syntax errors on stderr

    async def test_runtime_error(self, executor: CodeModeExecutor) -> None:
        """Python runtime errors are captured."""
        block = CodeBlock(
            language="python",
            code='raise ValueError("test error")',
        )
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.FAILURE
        assert "ValueError" in result.stderr

    async def test_py_alias(self, executor: CodeModeExecutor) -> None:
        """'py' language alias works."""
        block = CodeBlock(language="py", code='print("py alias")')
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.SUCCESS
        assert "py alias" in result.stdout


# =============================================================================
# Bash execution
# =============================================================================


class TestBashExecution:
    """Execute bash code blocks."""

    async def test_simple_echo(self, executor: CodeModeExecutor) -> None:
        """Simple echo command."""
        block = CodeBlock(language="bash", code='echo "hello bash"')
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.SUCCESS
        assert "hello bash" in result.stdout

    async def test_bash_file_ops(
        self,
        executor: CodeModeExecutor,
        workspace: Path,
    ) -> None:
        """Bash can write files to workspace."""
        block = CodeBlock(
            language="bash",
            code='echo "bash output" > output.txt',
        )
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.SUCCESS
        assert (workspace / "output.txt").read_text().strip() == "bash output"

    async def test_bash_nonzero_exit(self, executor: CodeModeExecutor) -> None:
        """Non-zero exit from bash is a failure."""
        block = CodeBlock(language="bash", code="exit 42")
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.FAILURE
        assert result.exit_code == 42

    async def test_sh_alias(self, executor: CodeModeExecutor) -> None:
        """'sh' language alias works."""
        block = CodeBlock(language="sh", code='echo "sh alias"')
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.SUCCESS


# =============================================================================
# Timeout handling
# =============================================================================


class TestTimeoutHandling:
    """Code execution timeout."""

    async def test_timeout(self, workspace: Path) -> None:
        """Long-running code is killed after timeout."""
        executor = CodeModeExecutor(
            workspace=workspace,
            timeout_seconds=1.0,
            use_sandbox=False,
        )
        block = CodeBlock(
            language="python",
            code="import time; time.sleep(60)",
        )
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.TIMEOUT
        assert result.duration_seconds >= 0.5  # at least some time passed
        assert "timed out" in (result.error_message or "")


# =============================================================================
# Error cases
# =============================================================================


class TestErrorCases:
    """Error handling and edge cases."""

    async def test_unsupported_language(
        self,
        executor: CodeModeExecutor,
    ) -> None:
        """Unsupported language returns failure."""
        block = CodeBlock(language="fortran", code="PRINT *, 'hello'")
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.FAILURE
        assert "Unsupported language" in (result.error_message or "")

    async def test_empty_code(self, executor: CodeModeExecutor) -> None:
        """Empty code block returns failure."""
        block = CodeBlock(language="python", code="")
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.FAILURE
        assert "Empty code block" in (result.error_message or "")

    async def test_whitespace_only_code(
        self,
        executor: CodeModeExecutor,
    ) -> None:
        """Whitespace-only code block returns failure."""
        block = CodeBlock(language="python", code="   \n\t  ")
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.FAILURE

    def test_invalid_workspace_raises(self, tmp_path: Path) -> None:
        """Non-existent workspace raises ValueError."""
        with pytest.raises(ValueError, match="must be an existing directory"):
            CodeModeExecutor(
                workspace=tmp_path / "nonexistent",
                use_sandbox=False,
            )

    async def test_execute_all_sequential(
        self,
        executor: CodeModeExecutor,
    ) -> None:
        """execute_all runs blocks sequentially."""
        blocks = [
            CodeBlock(language="python", code='print("first")'),
            CodeBlock(language="python", code='print("second")'),
            CodeBlock(language="python", code="exit(1)"),  # failure
            CodeBlock(language="python", code='print("fourth")'),
        ]
        results = await executor.execute_all(blocks)

        assert len(results) == 4
        assert results[0].status == CodeExecutionStatus.SUCCESS
        assert results[1].status == CodeExecutionStatus.SUCCESS
        assert results[2].status == CodeExecutionStatus.FAILURE
        # Fourth still runs despite third's failure
        assert results[3].status == CodeExecutionStatus.SUCCESS

    async def test_duration_tracked(self, executor: CodeModeExecutor) -> None:
        """Execution duration is tracked."""
        block = CodeBlock(
            language="python",
            code="import time; time.sleep(0.1); print('done')",
        )
        result = await executor.execute(block)

        assert result.status == CodeExecutionStatus.SUCCESS
        assert result.duration_seconds >= 0.05  # generous bound


# =============================================================================
# Result structure
# =============================================================================


class TestCodeExecutionResult:
    """Test the result dataclass."""

    def test_success_result(self) -> None:
        """Success result has expected fields."""
        result = CodeExecutionResult(
            status=CodeExecutionStatus.SUCCESS,
            exit_code=0,
            stdout="hello",
            stderr="",
            duration_seconds=0.5,
        )
        assert result.status == CodeExecutionStatus.SUCCESS
        assert result.exit_code == 0
        assert result.error_message is None

    def test_failure_result(self) -> None:
        """Failure result includes error message."""
        result = CodeExecutionResult(
            status=CodeExecutionStatus.FAILURE,
            exit_code=1,
            stderr="traceback...",
            error_message="Exit code 1",
        )
        assert result.status == CodeExecutionStatus.FAILURE
        assert result.error_message == "Exit code 1"

    def test_frozen(self) -> None:
        """Result is frozen (immutable)."""
        result = CodeExecutionResult(status=CodeExecutionStatus.SUCCESS)
        with pytest.raises(AttributeError):
            result.status = CodeExecutionStatus.FAILURE  # type: ignore[misc]


# =============================================================================
# Error rendering
# =============================================================================


class TestRenderCodeModeError:
    """Test error context rendering for retry injection."""

    def test_render_failure(self) -> None:
        """Failure renders with exit code and stderr."""
        result = CodeExecutionResult(
            status=CodeExecutionStatus.FAILURE,
            exit_code=1,
            stderr="NameError: name 'x' is not defined",
            error_message="Exit code 1",
        )
        rendered = render_code_mode_error(result)

        assert "## Code Execution Failed" in rendered
        assert "Exit code 1" in rendered
        assert "NameError" in rendered
        assert "Missing imports" in rendered  # Help text

    def test_render_timeout(self) -> None:
        """Timeout renders with timeout message."""
        result = CodeExecutionResult(
            status=CodeExecutionStatus.TIMEOUT,
            error_message="Code execution timed out after 30s",
        )
        rendered = render_code_mode_error(result)

        assert "timeout" in rendered
        assert "timed out" in rendered

    def test_render_sandbox_error(self) -> None:
        """Sandbox error renders cleanly."""
        result = CodeExecutionResult(
            status=CodeExecutionStatus.SANDBOX_ERROR,
            error_message="Interpreter not found: python3",
        )
        rendered = render_code_mode_error(result)

        assert "sandbox_error" in rendered
        assert "Interpreter not found" in rendered

    def test_render_no_stderr(self) -> None:
        """Render works when stderr is empty."""
        result = CodeExecutionResult(
            status=CodeExecutionStatus.FAILURE,
            exit_code=1,
            error_message="Exit code 1",
        )
        rendered = render_code_mode_error(result)

        assert "stderr" not in rendered  # No stderr section
        assert "Exit code 1" in rendered


class TestSandboxPathMapping210:
    """#210: the sandboxed command must reference the code file by NAME.

    bwrap binds the workspace at /workspace and --chdir's there; a
    host-absolute file path does not exist inside that namespace, so
    every sandboxed run died with SANDBOX_ERROR. mkstemp already places
    the file in the workspace root (the bind covers it), and the
    UNsandboxed path runs with cwd=workspace — so the basename resolves
    identically in both worlds.
    """

    async def test_command_references_basename_not_host_path(
        self, tmp_path: Path
    ) -> None:
        captured: dict[str, list[str]] = {}

        executor = CodeModeExecutor(
            workspace=tmp_path,
            use_sandbox=True,
        )

        def fake_wrap(cmd: list[str]) -> list[str]:
            captured["inner"] = list(cmd)
            return ["/bin/false"]  # never actually executed meaningfully

        executor._wrap_with_sandbox = fake_wrap  # type: ignore[method-assign]
        block = CodeBlock(language="python", code="print('hi')")
        await executor.execute(block)

        inner = captured["inner"]
        # The interpreter arg must be the bare filename (resolves at
        # /workspace/<name> inside bwrap and at cwd=workspace outside).
        assert inner[-1].startswith("mzt_code_")
        assert "/" not in inner[-1]

    async def test_unsandboxed_run_still_executes(self, tmp_path: Path) -> None:
        executor = CodeModeExecutor(workspace=tmp_path, use_sandbox=False)
        block = CodeBlock(language="python", code="print('basename works')")
        result = await executor.execute(block)
        assert result.status.value == "success"
        assert "basename works" in result.stdout
