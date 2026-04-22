"""Claude CLI backend using subprocess.

Wraps the `claude` CLI command for running prompts.
Based on patterns from run-sheet-review.sh.

Security Note: This module uses asyncio.create_subprocess_exec() which is the
safe subprocess method in Python - it does NOT use shell=True, so there is no
shell injection risk. Arguments are passed as a list, not interpolated into
a shell command string.

Progress Tracking: When a progress_callback is provided, this backend streams
output in real-time and reports bytes/lines received periodically. This enables
the CLI to show "Still running... 5.2KB received, 3m elapsed" during long executions.
"""

import asyncio
import json
import os
import shutil
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from marianne.backends.base import Backend, ExecutionResult, ExitReason
from marianne.core.config import BackendConfig
from marianne.core.constants import STREAM_CHUNK_SIZE, TRUNCATE_STDOUT_TAIL_CHARS
from marianne.core.errors import ErrorClassifier
from marianne.core.errors.signals import get_signal_name
from marianne.core.logging import get_logger

# Module-level logger for Claude CLI backend
_logger = get_logger("backend.claude_cli")

# Type alias for progress callback - receives dict with progress info
ProgressCallback = Callable[[dict[str, Any]], None]

# Timeout constants for subprocess management
GRACEFUL_TERMINATION_TIMEOUT: float = 5.0  # Seconds to wait for graceful termination
STREAM_READ_TIMEOUT: float = 1.0  # Seconds between stream read checks
PROCESS_EXIT_TIMEOUT: float = 5.0  # Seconds to wait for process exit after streams close


from marianne.utils.process import (  # noqa: E402
    reap_descendant_trees as _reap_descendant_trees,
)
from marianne.utils.process import (
    safe_killpg as _safe_killpg,
)


class ClaudeCliBackend(Backend):
    """Run prompts via the Claude CLI.

    Uses asyncio.create_subprocess_exec to invoke `claude -p <prompt>`.
    This is shell-injection safe as arguments are passed as a list.
    """

    def __init__(
        self,
        skip_permissions: bool = True,
        disable_mcp: bool = True,
        output_format: str = "text",
        cli_model: str | None = None,
        allowed_tools: list[str] | None = None,
        system_prompt_file: Path | None = None,
        working_directory: Path | None = None,
        timeout_seconds: float = 1800.0,  # 30 minute default
        progress_callback: ProgressCallback | None = None,
        progress_interval_seconds: float = 5.0,
        cli_extra_args: list[str] | None = None,
    ):
        """Initialize CLI backend.

        Args:
            skip_permissions: Pass --dangerously-skip-permissions
            disable_mcp: Disable MCP servers for faster execution (--strict-mcp-config)
            output_format: Output format (json, text, stream-json)
            cli_model: Model to use (--model flag), None uses default
            allowed_tools: Restrict to specific tools (--allowedTools)
            system_prompt_file: Custom system prompt file (--system-prompt)
            working_directory: Working directory for running commands
            timeout_seconds: Maximum time allowed per prompt
            progress_callback: Optional callback for progress updates during execution.
                Called with dict containing: bytes_received, lines_received,
                elapsed_seconds, phase.
            progress_interval_seconds: How often to call progress callback (default 5s).
            cli_extra_args: Extra arguments to pass to claude CLI (escape hatch).
        """
        self.skip_permissions = skip_permissions
        self.disable_mcp = disable_mcp
        self.output_format = output_format
        self.cli_model = cli_model
        self.allowed_tools = allowed_tools
        self.system_prompt_file = system_prompt_file
        self._working_directory = working_directory
        self.timeout_seconds = timeout_seconds
        self.progress_callback = progress_callback
        self.progress_interval_seconds = progress_interval_seconds
        self.cli_extra_args = cli_extra_args or []

        # PID tracking callbacks for orphan detection.
        # Set by the daemon's ProcessGroupManager when running under the conductor.
        # Standalone CLI mode leaves these as None — no orphan tracking needed.
        self._on_process_spawned: Callable[[int], None] | None = None
        self._on_process_exited: Callable[[int], None] | None = None

        # Real-time output logging paths (set per-sheet by runner)
        # Industry standard: separate files for stdout and stderr
        self._stdout_log_path: Path | None = None
        self._stderr_log_path: Path | None = None

        # Partial output accumulator — populated during execution so that
        # _handle_execution_timeout() can capture partial output on timeout
        # instead of returning empty strings.
        self._partial_stdout_chunks: list[bytes] = []
        self._partial_stderr_chunks: list[bytes] = []

        # Verify claude CLI is available
        self._claude_path = shutil.which("claude")

        # Track log write failures for filesystem flakiness visibility
        self.log_write_failures: int = 0

        # Use shared ErrorClassifier for rate limit detection
        # This ensures consistent classification with the runner
        self._error_classifier = ErrorClassifier()

        # Dynamic preamble — context-aware identity/position/retry info built
        # per-sheet by the runner via set_preamble().
        self._preamble: str | None = None

        # Prompt extensions (GH#76) — additional directives injected after the
        # preamble and before the user prompt. Set per-sheet by runner
        # via set_prompt_extensions().
        self._prompt_extensions: list[str] = []

        # Per-sheet overrides (GH#78) — saved originals for clear_overrides()
        self._saved_cli_model: str | None = None
        self._has_overrides: bool = False

    @classmethod
    def from_config(cls, config: BackendConfig) -> "ClaudeCliBackend":
        """Create backend from configuration."""
        return cls(
            skip_permissions=config.skip_permissions,
            disable_mcp=config.disable_mcp,
            output_format=config.output_format,
            cli_model=config.cli_model,
            allowed_tools=config.allowed_tools,
            system_prompt_file=config.system_prompt_file,
            working_directory=config.working_directory,
            timeout_seconds=config.timeout_seconds,
            cli_extra_args=config.cli_extra_args,
        )

    @property
    def name(self) -> str:
        return "claude-cli"

    def apply_overrides(self, overrides: dict[str, object]) -> None:
        """Apply per-sheet overrides for the next execution."""
        if not overrides:
            return
        self._saved_cli_model = self.cli_model
        self._has_overrides = True
        if "cli_model" in overrides:
            self.cli_model = str(overrides["cli_model"])

    def clear_overrides(self) -> None:
        """Restore original backend parameters after per-sheet execution."""
        if not self._has_overrides:
            return
        self.cli_model = self._saved_cli_model
        self._saved_cli_model = None
        self._has_overrides = False

    def set_output_log_path(self, path: Path | None) -> None:
        """Set base path for real-time output logging.

        Called per-sheet by runner to enable streaming output to log files.
        This provides visibility into Claude's output during long executions.

        Uses industry-standard separate files for stdout and stderr:
        - {path}.stdout.log - standard output
        - {path}.stderr.log - standard error

        This enables clean `tail -f` monitoring without stream interleaving.

        Args:
            path: Base path for log files (without extension), or None to disable.
                  Example: workspace/logs/sheet-01 creates sheet-01.stdout.log
        """
        if path is None:
            self._stdout_log_path = None
            self._stderr_log_path = None
        else:
            self._stdout_log_path = path.with_suffix(".stdout.log")
            self._stderr_log_path = path.with_suffix(".stderr.log")

    def set_preamble(self, preamble: str | None) -> None:
        """Set the dynamic preamble for the next execution.

        Args:
            preamble: Preamble text to prepend, or None to clear.
        """
        self._preamble = preamble

    def set_prompt_extensions(self, extensions: list[str]) -> None:
        """Set prompt extensions for the next execution.

        Extensions are additional directive blocks injected after the
        preamble. Called per-sheet by the runner to apply score-level and
        sheet-level extensions.

        Args:
            extensions: List of extension text blocks. Empty strings are ignored.
        """
        self._prompt_extensions = [e for e in extensions if e.strip()]

    def _inject_preamble_and_extensions(self, prompt: str) -> str:
        """Inject preamble and extensions into the prompt.

        If a preamble has been set via ``set_preamble()``, it is prepended.
        Any prompt extensions are appended after the user prompt.

        Args:
            prompt: The original user prompt.

        Returns:
            Prompt with preamble prepended and extensions appended.
        """
        if not self._preamble and not self._prompt_extensions:
            return prompt

        parts: list[str] = []
        if self._preamble:
            parts.append(self._preamble)
        parts.append(prompt)
        if self._prompt_extensions:
            parts.append("\n".join(self._prompt_extensions))
        return "\n".join(parts)

    def _build_command(self, prompt: str) -> list[str]:
        """Build the claude command with arguments.

        Returns a list of arguments for subprocess - NOT a shell string.
        The prompt is passed via stdin (``-p -`` sentinel), not as a CLI arg,
        to avoid ARG_MAX limits on large prompts (#108/#125).
        """
        if not self._claude_path:
            raise RuntimeError("claude CLI not found in PATH")

        cmd = [self._claude_path, "-p", "-"]

        if self.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        # Output format for subprocess execution
        cmd.extend(["--output-format", self.output_format])

        # Disable MCP servers for faster, isolated execution.
        # --strict-mcp-config means "only use servers from --mcp-config",
        # so we pass an empty config to disable all MCP servers. Without
        # both flags, Claude spawns MCP servers as child processes that
        # cause deadlocks. Config must have "mcpServers" key (not just "{}").
        if self.disable_mcp:
            cmd.extend(["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'])

        # Model selection
        if self.cli_model:
            cmd.extend(["--model", self.cli_model])

        # Tool restrictions for security-constrained execution
        if self.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self.allowed_tools)])

        # Custom system prompt
        if self.system_prompt_file:
            cmd.extend(["--system-prompt", str(self.system_prompt_file)])

        # Escape hatch - applied last so it can override anything above
        if self.cli_extra_args:
            cmd.extend(self.cli_extra_args)

        return cmd

    def _prepare_log_files(self) -> None:
        """Clear/create output log files for this execution.

        Truncates existing log files or creates new ones so that each
        execution starts with fresh output. Failures are silently ignored
        to avoid blocking execution when logging setup fails.
        """
        for log_path in (self._stdout_log_path, self._stderr_log_path):
            if log_path:
                try:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    log_path.write_bytes(b"")  # Truncate/create
                except OSError as e:
                    _logger.warning("log_prepare_failed", path=str(log_path), error=str(e))
                    self.log_write_failures += 1

    def _write_output_logs(
        self, stdout_bytes: bytes, stderr_bytes: bytes,
    ) -> None:
        """Write collected output to log files (non-streaming mode).

        In non-streaming mode, output is collected in memory and written
        to log files after the process completes.
        """
        if self._stdout_log_path:
            try:
                with open(self._stdout_log_path, "wb") as f:
                    f.write(stdout_bytes)
            except OSError as e:
                _logger.warning("log_write_failed", path=str(self._stdout_log_path), error=str(e))
                self.log_write_failures += 1
        if self._stderr_log_path:
            try:
                with open(self._stderr_log_path, "wb") as f:
                    f.write(stderr_bytes)
            except OSError as e:
                _logger.warning("log_write_failed", path=str(self._stderr_log_path), error=str(e))
                self.log_write_failures += 1

    async def _handle_execution_timeout(
        self,
        process: asyncio.subprocess.Process,
        start_time: float,
        bytes_received: int,
        lines_received: int,
    ) -> ExecutionResult:
        """Handle process timeout: graceful termination then force kill.

        First sends SIGTERM for graceful shutdown. If the process doesn't
        exit within GRACEFUL_TERMINATION_TIMEOUT, escalates to SIGKILL.

        Returns an ExecutionResult indicating timeout failure.
        """
        escalated_to_kill = False
        pid = process.pid
        # Get PGID before process exits (may become unavailable after)
        try:
            pgid = os.getpgid(pid)
        except (OSError, ProcessLookupError):
            pgid = None
        try:
            process.terminate()
        except ProcessLookupError:
            pass  # Process already exited
        else:
            try:
                await asyncio.wait_for(process.wait(), timeout=GRACEFUL_TERMINATION_TIMEOUT)
            except TimeoutError:
                escalated_to_kill = True
                # Kill entire process group — not just main process.
                # MCP/LSP child servers survive if only the main process is killed.
                if pgid is not None:
                    try:
                        _safe_killpg(pgid, signal.SIGKILL, context="timeout_escalation")
                    except (OSError, ProcessLookupError):
                        pass
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

        # Reap descendants that escaped to their own PGIDs
        if pid is not None:
            _reap_descendant_trees(pid)

        duration = time.monotonic() - start_time
        actual_signal = signal.SIGKILL if escalated_to_kill else signal.SIGTERM

        _logger.error(
            "execution_timeout",
            duration_seconds=duration,
            timeout_seconds=self.timeout_seconds,
            bytes_received=bytes_received,
            lines_received=lines_received,
            termination_signal=actual_signal,
        )

        if self.progress_callback:
            self.progress_callback({
                "bytes_received": bytes_received,
                "lines_received": lines_received,
                "elapsed_seconds": duration,
                "phase": "timeout",
            })

        # Capture partial output collected before timeout.
        # The streaming path accumulates chunks in _partial_stdout_chunks;
        # the non-streaming path leaves them empty (communicate() is atomic).
        partial_stdout = b"".join(self._partial_stdout_chunks).decode("utf-8", errors="replace")
        partial_stderr = b"".join(self._partial_stderr_chunks).decode("utf-8", errors="replace")
        timeout_msg = f"Command timed out after {self.timeout_seconds}s"
        stderr_combined = (
            f"{partial_stderr}\n{timeout_msg}".strip()
            if partial_stderr else timeout_msg
        )

        return ExecutionResult(
            success=False,
            exit_code=None,
            exit_signal=actual_signal,
            exit_reason="timeout",
            stdout=partial_stdout,
            stderr=stderr_combined,
            duration_seconds=duration,
            error_type="timeout",
            error_message=f"Timed out after {self.timeout_seconds}s",
        )

    def _parse_returncode(
        self, returncode: int | None, stderr: str,
    ) -> tuple[int | None, int | None, ExitReason, str]:
        """Parse process returncode into exit metadata.

        Returns:
            Tuple of (exit_code, exit_signal, exit_reason, updated_stderr).
            stderr may have signal info appended for debugging.
        """
        if returncode is None:
            return None, None, "error", stderr
        elif returncode < 0:
            exit_signal = -returncode
            signal_name = get_signal_name(exit_signal)
            updated_stderr = f"{stderr}\n[Process killed by {signal_name}]".strip()
            return None, exit_signal, "killed", updated_stderr
        else:
            return returncode, None, "completed", stderr

    def _build_completed_result(
        self,
        stdout: str,
        stderr: str,
        exit_code: int | None,
        exit_signal: int | None,
        exit_reason: ExitReason,
        duration: float,
    ) -> ExecutionResult:
        """Build ExecutionResult for a completed (non-timeout) execution.

        Checks for rate limiting, determines success, extracts token usage
        from JSON output when available, logs appropriately, and returns
        the final result.
        """
        rate_limited = self._detect_rate_limit(stdout, stderr, exit_code)
        success = exit_code == 0

        # Extract parsed wait duration from rate limit error text
        rate_limit_wait_seconds = None
        if rate_limited:
            combined = f"{stdout}\n{stderr}"
            rate_limit_wait_seconds = self._error_classifier.extract_rate_limit_wait(combined)

        if rate_limited:
            _logger.warning(
                "rate_limit_detected",
                duration_seconds=duration,
                exit_code=exit_code,
                exit_signal=exit_signal,
                stdout_bytes=len(stdout),
                stderr_bytes=len(stderr),
            )
        elif success:
            _logger.info(
                "execution_completed",
                duration_seconds=duration,
                exit_code=exit_code,
                stdout_bytes=len(stdout),
                stderr_bytes=len(stderr),
            )
        else:
            limit = TRUNCATE_STDOUT_TAIL_CHARS
            stdout_tail = stdout[-limit:] if len(stdout) > limit else stdout
            stderr_tail = stderr[-limit:] if len(stderr) > limit else stderr
            _logger.error(
                "execution_failed",
                duration_seconds=duration,
                exit_code=exit_code,
                exit_signal=exit_signal,
                exit_reason=exit_reason,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )

        # Extract token usage from JSON output (D-024: cost accuracy fix).
        # When output_format is "json", Claude Code returns
        # {"result": "...", "usage": {"input_tokens": N, "output_tokens": N}}.
        # Without this, CostMixin falls back to character-based estimation
        # which underestimates by 10-100x.
        input_tokens, output_tokens = self._extract_tokens_from_json(stdout)

        return ExecutionResult(
            success=success,
            exit_code=exit_code,
            exit_signal=exit_signal,
            exit_reason=exit_reason,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            rate_limited=rate_limited,
            rate_limit_wait_seconds=rate_limit_wait_seconds,
            error_type="rate_limit" if rate_limited else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _extract_tokens_from_json(
        self, stdout: str,
    ) -> tuple[int | None, int | None]:
        """Extract token counts from JSON output when available.

        When output_format is "json", Claude Code returns structured output
        with a "usage" field containing input_tokens and output_tokens. This
        provides accurate cost tracking instead of the 10-100x underestimate
        from character-based heuristics.

        Args:
            stdout: Raw stdout from the CLI process.

        Returns:
            Tuple of (input_tokens, output_tokens). Either or both may be
            None if the data isn't available or parseable.
        """
        if self.output_format != "json":
            return None, None

        if not stdout.strip():
            return None, None

        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return None, None

        if not isinstance(data, dict):
            return None, None

        usage = data.get("usage")
        if not isinstance(usage, dict):
            return None, None

        input_tokens: int | None = None
        output_tokens: int | None = None

        raw_input = usage.get("input_tokens")
        if isinstance(raw_input, int):
            input_tokens = raw_input

        raw_output = usage.get("output_tokens")
        if isinstance(raw_output, int):
            output_tokens = raw_output

        return input_tokens, output_tokens

    async def _kill_orphaned_process(
        self, process: asyncio.subprocess.Process, error: BaseException,
    ) -> None:
        """Kill an orphaned subprocess to prevent resource leaks.

        Called when an exception occurs after the process started but
        before it completed. Kills the entire process group (including
        MCP server children) then the main process.
        """
        _logger.warning(
            "killing_orphaned_process",
            pid=process.pid,
            reason="exception_during_execution",
            error=str(error),
        )
        try:
            _safe_killpg(
                os.getpgid(process.pid),
                signal.SIGKILL,
                context="kill_orphaned_process",
            )
        except (OSError, ProcessLookupError):
            pass
        try:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except (ProcessLookupError, TimeoutError, OSError):
            pass

    async def _execute_impl(
        self, prompt: str, *, timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute a prompt via claude CLI (internal implementation).

        Uses create_subprocess_exec (safe, no shell) to invoke claude.

        Exit handling:
        - Normal exit: returncode >= 0, exit_code set
        - Signal kill: returncode < 0, signal = -returncode
        - Timeout: We kill with SIGKILL, signal=9, exit_reason="timeout"

        Progress tracking:
        - If progress_callback is set, streams output and calls callback periodically
        - Callback receives dict with bytes_received, lines_received, elapsed_seconds

        Args:
            prompt: The prompt to send to Claude.
            timeout_seconds: Per-call timeout override. Uses self.timeout_seconds if None.
        """
        effective_timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        cmd = self._build_command(prompt)
        # Prepare the full prompt (with preamble/extensions injected) for stdin.
        safe_prompt = self._inject_preamble_and_extensions(prompt)
        start_time = time.monotonic()

        _logger.debug(
            "executing_command",
            command=cmd[0],
            args_count=len(cmd) - 1,
            skip_permissions=self.skip_permissions,
            output_format=self.output_format,
            working_directory=str(self.working_directory) if self.working_directory else None,
            timeout_seconds=effective_timeout,
            prompt_length=len(prompt),
        )

        # Progress tracking state
        bytes_received = 0
        lines_received = 0
        last_progress_time = start_time

        def _notify_progress(phase: str = "executing") -> None:
            """Send progress update if callback is set and interval elapsed."""
            nonlocal last_progress_time
            if self.progress_callback is None:
                return
            now = time.monotonic()
            if now - last_progress_time >= self.progress_interval_seconds:
                self.progress_callback({
                    "bytes_received": bytes_received,
                    "lines_received": lines_received,
                    "elapsed_seconds": now - start_time,
                    "phase": phase,
                })
                last_progress_time = now

        process: asyncio.subprocess.Process | None = None
        self._prepare_log_files()
        # Reset partial output accumulators for this execution
        self._partial_stdout_chunks = []
        self._partial_stderr_chunks = []

        try:
            # create_subprocess_exec is shell-injection safe.
            # stdin=PIPE: prompt is written to stdin (-p - sentinel in cmd) to
            # avoid ARG_MAX limits on large prompts (#108/#125).
            # start_new_session=True creates a new process group for clean cleanup
            # (workaround for Claude Code Issue #1935: MCP servers not cleaned up).
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_directory,
                env={
                    k: v for k, v in os.environ.items()
                    if k != "CLAUDECODE"
                },
                start_new_session=True,
            )

            # Track PID for orphan detection by the daemon's pgroup manager
            if self._on_process_spawned and process.pid is not None:
                self._on_process_spawned(process.pid)

            # Write prompt to subprocess stdin, then close to signal EOF.
            # Must happen before _stream_with_progress: if the subprocess waits
            # for stdin EOF before producing output, reading stdout first would
            # deadlock (parent blocked reading stdout; subprocess blocked on stdin).
            assert process.stdin is not None
            process.stdin.write(safe_prompt.encode())
            await process.stdin.drain()
            process.stdin.close()

            if self.progress_callback:
                self.progress_callback({
                    "bytes_received": 0,
                    "lines_received": 0,
                    "elapsed_seconds": 0.0,
                    "phase": "starting",
                })

            try:
                # Always use streaming to accumulate partial output in
                # _partial_stdout_chunks/_partial_stderr_chunks.  This ensures
                # output collected before a timeout is preserved — the old
                # communicate() path was atomic and lost all output on timeout.
                stdout_bytes, stderr_bytes = await self._stream_with_progress(
                    process, start_time, _notify_progress,
                    effective_timeout=effective_timeout,
                )

                bytes_received = len(stdout_bytes) + len(stderr_bytes)
                lines_received = stdout_bytes.count(b"\n") + stderr_bytes.count(b"\n")

            except TimeoutError:
                return await self._handle_execution_timeout(
                    process, start_time, bytes_received, lines_received,
                )

            duration = time.monotonic() - start_time
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if self.progress_callback:
                self.progress_callback({
                    "bytes_received": bytes_received,
                    "lines_received": lines_received,
                    "elapsed_seconds": duration,
                    "phase": "completed",
                })

            exit_code, exit_signal, exit_reason, stderr = self._parse_returncode(
                process.returncode, stderr,
            )

            return self._build_completed_result(
                stdout, stderr, exit_code, exit_signal, exit_reason, duration,
            )

        except FileNotFoundError:
            duration = time.monotonic() - start_time
            _logger.error(
                "cli_not_found",
                error_message="claude CLI not found in PATH",
                duration_seconds=duration,
            )
            return ExecutionResult(
                success=False,
                exit_code=127,
                exit_signal=None,
                exit_reason="error",
                stdout="",
                stderr="claude CLI not found",
                duration_seconds=duration,
                error_type="not_found",
                error_message="claude CLI not found in PATH",
            )
        except asyncio.CancelledError:
            # CancelledError is BaseException, not Exception — must handle
            # separately to prevent subprocess zombie leaks.
            if process is not None and process.returncode is None:
                await self._kill_orphaned_process(process, asyncio.CancelledError())
            raise
        except (OSError, RuntimeError, ValueError, UnicodeDecodeError) as e:
            # Runtime errors from subprocess I/O, stream operations, or
            # encoding. Narrow catch prevents masking programming bugs
            # (TypeError, AttributeError, etc.) as execution failures.
            duration = time.monotonic() - start_time

            if process is not None and process.returncode is None:
                await self._kill_orphaned_process(process, e)

            _logger.exception(
                "execution_exception",
                error_message=str(e),
                duration_seconds=duration,
            )
            return ExecutionResult(
                success=False,
                exit_code=None,
                exit_signal=None,
                exit_reason="error",
                stdout="",
                stderr=str(e),
                duration_seconds=duration,
                error_type="exception",
                error_message=str(e),
            )
        except Exception as e:
            # Programming errors — clean up subprocess but re-raise so bugs
            # are not silently masked as execution failures.
            if process is not None and process.returncode is None:
                await self._kill_orphaned_process(process, e)
            raise

    async def _read_stream_with_logging(
        self,
        stream: asyncio.StreamReader | None,
        chunks: list[bytes],
        is_stdout: bool,
        counters: list[int | float],
        start_time: float,
        timeout: float,
        notify_progress: Callable[[str], None],
    ) -> None:
        """Read from a subprocess stream, log to file, and track progress.

        Args:
            stream: The async stream to read from (may be None).
            chunks: Mutable list to accumulate read chunks into.
            is_stdout: True for stdout, False for stderr (selects log file).
            counters: Mutable [total_bytes, total_lines, last_update] accumulator
                shared across concurrent stream readers.
            start_time: Execution start time for timeout calculation.
            timeout: Overall execution timeout in seconds.
            notify_progress: Callback to notify progress updates.
        """
        if stream is None:
            return

        log_path = self._stdout_log_path if is_stdout else self._stderr_log_path
        log_file = None
        try:
            if log_path:
                try:
                    log_file = open(log_path, "ab")  # noqa: SIM115
                except OSError as e:
                    _logger.warning("log_file_open_failed", path=str(log_path), error=str(e))
                    self.log_write_failures += 1
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        stream.read(STREAM_CHUNK_SIZE),
                        timeout=STREAM_READ_TIMEOUT,
                    )
                except TimeoutError as read_timeout:
                    elapsed = time.monotonic() - start_time
                    if elapsed > timeout:
                        raise TimeoutError(
                            f"Execution timeout exceeded "
                            f"({elapsed:.0f}s > {timeout}s limit)"
                        ) from read_timeout
                    continue

                if not chunk:
                    break

                chunks.append(chunk)

                if log_file is not None:
                    try:
                        log_file.write(chunk)
                        log_file.flush()
                    except OSError as e:
                        _logger.warning("log_write_failed", path=str(log_path), error=str(e))
                        self.log_write_failures += 1

                counters[0] += len(chunk)
                counters[1] += chunk.count(b"\n")

                now = time.monotonic()
                if now - counters[2] >= self.progress_interval_seconds:
                    notify_progress("executing")
                    counters[2] = now
        finally:
            if log_file is not None:
                log_file.close()

    async def _await_process_exit(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        """Wait for process exit, killing the process group if it hangs.

        KNOWN BUG WORKAROUND (Claude Code Issue #1935):
        Claude Code does NOT properly terminate MCP server child processes when
        exiting. MCP servers are spawned as child processes and Claude waits for
        them to exit — but they never do because they're long-running servers.

        Fix: After streams close, wait briefly for Claude to exit. If it doesn't,
        kill the entire process group (Claude + all children including MCP servers).
        """
        pid = process.pid
        # Check if the process is already dead (e.g. from SIGABRT)
        is_already_dead = process.returncode is not None

        try:
            if not is_already_dead:
                await asyncio.wait_for(process.wait(), timeout=PROCESS_EXIT_TIMEOUT)
                return
        except TimeoutError:
            _logger.warning(
                "process_exit_timeout",
                message="Claude did not exit after streams closed (likely MCP server bug)",
                pid=pid,
                timeout_seconds=PROCESS_EXIT_TIMEOUT,
            )

        # Process is either dead but hanging on children, or timed out.
        # Immediately escalate to SIGKILL on the entire process group.
        # Skip SIGTERM/sleep if already dead to prevent orphan compounding.
        try:
            pgid = os.getpgid(pid)
            if not is_already_dead:
                _safe_killpg(pgid, signal.SIGTERM, context="await_exit_graceful")
                # Briefest possible wait for graceful group exit if not already dead
                try:
                    await asyncio.wait_for(process.wait(), timeout=0.5)
                    return
                except TimeoutError:
                    pass

            # Force kill the full group — MCP servers often ignore SIGTERM
            _safe_killpg(pgid, signal.SIGKILL, context="await_exit_force")
        except (OSError, ProcessLookupError):
            pass

        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()

        # Reap any descendant processes that escaped to their own PGIDs.
        # Claude Code's Bash tool spawns commands with start_new_session,
        # giving each its own PGID. When claude-code exits, those children
        # get reparented to init but keep their PGIDs — killpg on claude's
        # PGID doesn't reach them. This catches them by PID ancestry.
        if pid is not None:
            _reap_descendant_trees(pid)

    async def _stream_with_progress(
        self,
        process: asyncio.subprocess.Process,
        start_time: float,
        notify_progress: Callable[[str], None],
        *,
        effective_timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        """Stream output from process while tracking progress.

        Reads stdout and stderr concurrently, calling notify_progress periodically
        to report bytes/lines received.

        Args:
            process: The running subprocess.
            start_time: When execution started (for elapsed time calculation).
            notify_progress: Callback to notify progress updates.
            effective_timeout: Timeout to use. Falls back to self.timeout_seconds.

        Returns:
            Tuple of (stdout_bytes, stderr_bytes) collected from streams.

        Raises:
            TimeoutError: If execution exceeds timeout.
        """
        timeout = effective_timeout if effective_timeout is not None else self.timeout_seconds
        # Use the instance-level accumulators so partial output survives timeout.
        # _handle_execution_timeout() reads these if a TimeoutError occurs.
        stdout_chunks = self._partial_stdout_chunks
        stderr_chunks = self._partial_stderr_chunks
        # Mutable accumulator shared by concurrent stream readers:
        # [total_bytes, total_lines, last_update_time]
        counters: list[int | float] = [0, 0, start_time]

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self._read_stream_with_logging(
                        process.stdout, stdout_chunks, True,
                        counters, start_time, timeout, notify_progress,
                    ),
                    self._read_stream_with_logging(
                        process.stderr, stderr_chunks, False,
                        counters, start_time, timeout, notify_progress,
                    ),
                ),
                timeout=timeout,
            )
        except TimeoutError:
            raise
        except asyncio.CancelledError:
            pid = process.pid
            _logger.warning(
                "process_cancelled",
                message="Cancellation received, killing process group",
                pid=pid,
            )
            # Stage 1: SIGTERM for graceful shutdown
            try:
                pgid = os.getpgid(pid)
                _safe_killpg(pgid, signal.SIGTERM, context="cancel_graceful")
                await asyncio.sleep(0.5)
            except (OSError, ProcessLookupError):
                pgid = None
            # Stage 2: SIGKILL entire process group — MCP/LSP servers
            # often ignore SIGTERM and leak if only the main process is killed.
            if pgid is not None:
                try:
                    _safe_killpg(pgid, signal.SIGKILL, context="cancel_force")
                except (OSError, ProcessLookupError):
                    pass
            try:
                process.kill()
            except ProcessLookupError:
                pass
            # Reap the zombie to release FDs and process table entry.
            # Without wait(), the subprocess transport leaks pipes and the
            # kernel keeps the process as defunct until GC finalizes it.
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except (TimeoutError, ProcessLookupError, OSError):
                pass
            # Reap descendants that escaped to their own PGIDs
            if pid is not None:
                _reap_descendant_trees(pid)
            raise

        await self._await_process_exit(process)

        # Untrack PID — process and children are cleaned up
        if self._on_process_exited and process.pid is not None:
            self._on_process_exited(process.pid)

        return b"".join(stdout_chunks), b"".join(stderr_chunks)

    async def execute(
        self, prompt: str, *, timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute a prompt (Backend protocol implementation)."""
        return await self._execute_impl(prompt, timeout_seconds=timeout_seconds)

    async def health_check(self) -> bool:
        """Check if claude CLI is available and responsive."""
        if not self._claude_path:
            return False

        try:
            # Simple test prompt
            result = await self._execute_impl("Say 'ready' and nothing else.")
            return result.success and "ready" in result.stdout.lower()
        except (TimeoutError, OSError, RuntimeError) as e:
            _logger.warning("health_check_failed", error_type=type(e).__name__, error=str(e))
            return False

    async def availability_check(self) -> bool:
        """Check if the claude CLI binary exists and is executable.

        Unlike health_check(), this does NOT send a prompt or consume
        API quota. Used after quota exhaustion waits.
        """
        if not self._claude_path:
            return False
        return os.path.isfile(self._claude_path) and os.access(
            self._claude_path, os.X_OK,
        )
