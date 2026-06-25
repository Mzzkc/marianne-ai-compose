"""Code mode execution — sandboxed execution of agent-generated code.

When free-tier models (OpenRouter) produce code blocks instead of tool calls,
the code mode executor runs them in a bwrap sandbox with workspace access.
This is the bridge that gives non-MCP-native instruments access to the
technique system.

The executor:
1. Receives classified code blocks from the TechniqueRouter
2. Writes the code to a temp file in the workspace
3. Wraps execution in bwrap sandbox (via SandboxWrapper)
4. Captures stdout, stderr, and written files
5. Returns results for injection into the sheet output

For MCP-native instruments (claude-code, gemini-cli), code mode is optional.
These instruments have native tool use. Code mode is the primary execution
path for instruments that lack tool-use support.

Execution timeout: configurable, defaults to 30s. A bwrap subprocess starts
in ~4ms — the overhead is negligible.

See: design spec section 8.2 (Code Mode Execution)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from marianne.core.logging import get_logger
from marianne.daemon.technique_router import CodeBlock

_logger = get_logger("execution.code_mode")

# Default execution timeout for agent-generated code
_DEFAULT_TIMEOUT_SECONDS = 30.0

# Maximum output size to capture (prevent memory exhaustion)
_MAX_OUTPUT_BYTES = 512 * 1024  # 512KB


class CodeExecutionStatus(str, Enum):
    """Result status for a code mode execution."""

    SUCCESS = "success"
    """Code ran successfully (exit code 0)."""

    FAILURE = "failure"
    """Code ran but exited with non-zero status."""

    TIMEOUT = "timeout"
    """Code execution exceeded the timeout."""

    SANDBOX_ERROR = "sandbox_error"
    """Sandbox setup or teardown failed."""


@dataclass(frozen=True)
class CodeExecutionResult:
    """Result of executing agent-generated code in a sandbox.

    Contains the execution outcome, captured output, and any
    files written by the code.
    """

    status: CodeExecutionStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    error_message: str | None = None
    files_written: list[str] = field(default_factory=list)


# Language -> interpreter mapping
_INTERPRETERS: dict[str, list[str]] = {
    "python": ["python3"],
    "py": ["python3"],
    "bash": ["bash"],
    "sh": ["sh"],
    "shell": ["bash"],
    "javascript": ["node"],
    "js": ["node"],
    "typescript": ["npx", "tsx"],
    "ts": ["npx", "tsx"],
}


class CodeModeExecutor:
    """Runs agent-generated code blocks in sandboxed subprocesses.

    Each code block is written to a temp file and run through the
    appropriate interpreter. When bwrap is available, execution is
    sandboxed. When bwrap is unavailable, execution still proceeds
    (with a warning) — the deep fallback philosophy applies to the
    execution layer too.

    Usage::

        executor = CodeModeExecutor(
            workspace=Path("/tmp/agent-ws"),
        )

        block = CodeBlock(language="python", code="print('hello')")
        result = await executor.execute(block)

        if result.status == CodeExecutionStatus.SUCCESS:
            print(result.stdout)
    """

    def __init__(
        self,
        *,
        workspace: Path,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        use_sandbox: bool = True,
        bind_mounts: Sequence[Path | str] | None = None,
    ) -> None:
        """Initialize the code mode executor.

        Args:
            workspace: The agent's workspace directory. Code runs
                with this as its working directory.
            timeout_seconds: Maximum time for code execution.
            use_sandbox: Whether to use bwrap sandbox. When False,
                code runs directly (useful for testing or when bwrap
                is unavailable).
            bind_mounts: Extra host paths to bind at the same absolute path
                inside bwrap. MCP socket parent directories are passed here so
                generated technique runtimes can reach the shared MCP pool.
        """
        if not workspace.is_dir():
            raise ValueError(f"workspace must be an existing directory: {workspace}")

        self._workspace = workspace
        self._timeout = timeout_seconds
        self._use_sandbox = use_sandbox
        self._bind_mounts = [str(path) for path in bind_mounts or []]

    @property
    def workspace(self) -> Path:
        """The workspace directory for code execution."""
        return self._workspace

    async def execute(self, block: CodeBlock) -> CodeExecutionResult:
        """Run a single code block.

        Writes the code to a temp file, runs it through the appropriate
        interpreter, captures output, and returns the result.

        Args:
            block: The code block to execute.

        Returns:
            Execution result with status, output, and metadata.
        """
        if not block.code.strip():
            return CodeExecutionResult(
                status=CodeExecutionStatus.FAILURE,
                error_message="Empty code block",
            )

        interpreter = _INTERPRETERS.get(block.language.lower())
        if interpreter is None:
            return CodeExecutionResult(
                status=CodeExecutionStatus.FAILURE,
                error_message=f"Unsupported language: {block.language}",
            )

        # Write code to a temp file in the workspace
        suffix = _file_suffix(block.language)
        try:
            code_file = self._write_code_file(block.code, suffix)
        except OSError as e:
            return CodeExecutionResult(
                status=CodeExecutionStatus.SANDBOX_ERROR,
                error_message=f"Failed to write code file: {e}",
            )

        try:
            return await self._run_code(interpreter, code_file, block.language)
        finally:
            # Clean up temp file
            try:
                code_file.unlink(missing_ok=True)
            except OSError:
                pass

    async def execute_all(
        self, blocks: list[CodeBlock],
    ) -> list[CodeExecutionResult]:
        """Run multiple code blocks sequentially.

        Each block runs in order. If a block fails, subsequent blocks
        still execute (independent execution model — each block is
        self-contained).

        Args:
            blocks: Code blocks to execute.

        Returns:
            List of results, one per block.
        """
        results: list[CodeExecutionResult] = []
        for block in blocks:
            result = await self.execute(block)
            results.append(result)
        return results

    def _write_code_file(self, code: str, suffix: str) -> Path:
        """Write code to a temp file in the workspace.

        Args:
            code: The code to write.
            suffix: File extension (e.g., ".py", ".sh").

        Returns:
            Path to the created file.
        """
        # Use workspace as temp dir base so bwrap bind-mount covers it
        fd, path_str = tempfile.mkstemp(
            suffix=suffix,
            prefix="mzt_code_",
            dir=str(self._workspace),
        )
        path = Path(path_str)
        try:
            # Close the fd from mkstemp before writing via Path
            os.close(fd)
            path.write_text(code, encoding="utf-8")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    async def _run_code(
        self,
        interpreter: list[str],
        code_file: Path,
        language: str,
    ) -> CodeExecutionResult:
        """Run a code file through its interpreter.

        Uses asyncio.create_subprocess_exec for safe subprocess creation
        (no shell injection risk — command parts passed as list).

        Args:
            interpreter: The interpreter command (e.g., ["python3"]).
            code_file: Path to the code file.
            language: Language identifier for logging.

        Returns:
            Execution result.
        """
        # #210: reference the file by NAME, never host-absolute path. The
        # sandbox binds the workspace at /workspace and --chdir's there,
        # so a host path doesn't exist inside the namespace (every
        # sandboxed run died with SANDBOX_ERROR); the unsandboxed run
        # uses cwd=workspace, so the basename resolves in both worlds
        # (mkstemp places the file in the workspace root).
        cmd = interpreter + [code_file.name]

        if self._use_sandbox:
            cmd = self._wrap_with_sandbox(cmd)

        _logger.debug(
            "code_mode.executing",
            extra={
                "language": language,
                "code_file": str(code_file),
                "workspace": str(self._workspace),
                "sandboxed": self._use_sandbox,
            },
        )

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace),
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._timeout,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                duration = time.monotonic() - start
                _logger.warning(
                    "code_mode.timeout",
                    extra={
                        "language": language,
                        "timeout_seconds": self._timeout,
                        "duration_seconds": duration,
                    },
                )
                return CodeExecutionResult(
                    status=CodeExecutionStatus.TIMEOUT,
                    duration_seconds=duration,
                    error_message=f"Code execution timed out after {self._timeout}s",
                )

            duration = time.monotonic() - start
            stdout = _truncate_output(stdout_bytes)
            stderr = _truncate_output(stderr_bytes)

            if proc.returncode == 0:
                _logger.info(
                    "code_mode.success",
                    extra={
                        "language": language,
                        "duration_seconds": duration,
                        "stdout_len": len(stdout),
                    },
                )
                return CodeExecutionResult(
                    status=CodeExecutionStatus.SUCCESS,
                    exit_code=0,
                    stdout=stdout,
                    stderr=stderr,
                    duration_seconds=duration,
                )
            else:
                _logger.info(
                    "code_mode.failure",
                    extra={
                        "language": language,
                        "exit_code": proc.returncode,
                        "duration_seconds": duration,
                    },
                )
                return CodeExecutionResult(
                    status=CodeExecutionStatus.FAILURE,
                    exit_code=proc.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    duration_seconds=duration,
                    error_message=f"Exit code {proc.returncode}",
                )

        except FileNotFoundError as e:
            duration = time.monotonic() - start
            error_msg = f"Interpreter not found: {e}"
            _logger.error(
                "code_mode.interpreter_not_found",
                extra={"interpreter": interpreter[0], "error": error_msg},
            )
            return CodeExecutionResult(
                status=CodeExecutionStatus.SANDBOX_ERROR,
                duration_seconds=duration,
                error_message=error_msg,
            )
        except OSError as e:
            duration = time.monotonic() - start
            error_msg = f"Execution error: {e}"
            _logger.error(
                "code_mode.execution_error",
                extra={"error": error_msg},
            )
            return CodeExecutionResult(
                status=CodeExecutionStatus.SANDBOX_ERROR,
                duration_seconds=duration,
                error_message=error_msg,
            )

    def _wrap_with_sandbox(self, cmd: list[str]) -> list[str]:
        """Wrap a command with bwrap sandbox if available.

        Falls back to direct execution if bwrap is not installed.

        Args:
            cmd: The inner command to sandbox.

        Returns:
            The sandboxed command (or original if bwrap unavailable).
        """
        from marianne.execution.sandbox import SandboxConfig, SandboxWrapper

        if shutil.which("bwrap") is None:
            _logger.warning(
                "code_mode.bwrap_unavailable",
                extra={"workspace": str(self._workspace)},
            )
            return cmd

        config = SandboxConfig(
            workspace=str(self._workspace),
            network_isolated=True,
            bind_mounts=self._bind_mounts,
        )
        wrapper = SandboxWrapper(config)
        return wrapper.build_command(cmd)


def _file_suffix(language: str) -> str:
    """Map language identifier to file extension."""
    mapping = {
        "python": ".py",
        "py": ".py",
        "bash": ".sh",
        "sh": ".sh",
        "shell": ".sh",
        "javascript": ".js",
        "js": ".js",
        "typescript": ".ts",
        "ts": ".ts",
    }
    return mapping.get(language.lower(), ".txt")


def _truncate_output(data: bytes) -> str:
    """Decode and truncate output to prevent memory exhaustion."""
    if len(data) > _MAX_OUTPUT_BYTES:
        data = data[-_MAX_OUTPUT_BYTES:]
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return "<binary output>"


def render_code_mode_error(result: CodeExecutionResult) -> str:
    """Render a code execution failure for retry context injection.

    When code mode execution fails, this renders the error in a format
    that helps the agent adjust on retry. Injected into the sheet's
    output context.

    Args:
        result: The failed execution result.

    Returns:
        Markdown-formatted error context.
    """
    lines = [
        "## Code Execution Failed",
        "",
        f"**Status:** {result.status.value}",
    ]

    if result.exit_code is not None:
        lines.append(f"**Exit code:** {result.exit_code}")

    if result.error_message:
        lines.append(f"**Error:** {result.error_message}")

    if result.stderr:
        lines.extend([
            "",
            "**stderr:**",
            "```",
            result.stderr[:2000],  # Truncate for prompt context
            "```",
        ])

    lines.extend([
        "",
        "Please review the error and adjust your code. Common issues:",
        "- Missing imports",
        "- Incorrect file paths (use /workspace as base)",
        "- Type errors",
        "- Permission errors (sandbox restricts network access)",
    ])

    return "\n".join(lines)
