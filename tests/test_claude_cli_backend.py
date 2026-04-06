"""Tests for mozart.backends.claude_cli module.

Covers ClaudeCliBackend: initialization, from_config, _build_command,
_inject_preamble_and_extensions, set_preamble, _parse_returncode,
set_output_log_path, apply_overrides, clear_overrides, set_prompt_extensions,
_prepare_log_files, _write_output_logs, _handle_execution_timeout,
_build_completed_result, _kill_orphaned_process, _execute_impl error paths,
_await_process_exit, health_check, and execute.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozart.backends.base import ExecutionResult
from mozart.backends.claude_cli import ClaudeCliBackend


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def backend(tmp_path: Path) -> ClaudeCliBackend:
    """Create a ClaudeCliBackend with claude CLI mocked as available."""
    with patch("shutil.which", return_value="/usr/bin/claude"):
        b = ClaudeCliBackend(working_directory=tmp_path)
    return b


@pytest.fixture
def backend_no_cli() -> ClaudeCliBackend:
    """Create a ClaudeCliBackend with no claude CLI found."""
    with patch("shutil.which", return_value=None):
        b = ClaudeCliBackend()
    return b


def _make_mock_process(
    returncode: int | None = 0,
    pid: int = 12345,
) -> MagicMock:
    """Create a mock asyncio.subprocess.Process."""
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.pid = pid
    proc.returncode = returncode
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    # stdin mock for PIPE: write is sync, drain is async, close is sync (#108)
    mock_stdin = MagicMock(spec=asyncio.StreamWriter)
    mock_stdin.drain = AsyncMock()
    proc.stdin = mock_stdin
    return proc


# ─── Initialization ───────────────────────────────────────────────────


class TestInit:
    """Tests for ClaudeCliBackend.__init__()."""

    def test_defaults(self, backend: ClaudeCliBackend):
        assert backend.skip_permissions is True
        assert backend.disable_mcp is True
        assert backend.output_format == "text"
        assert backend.cli_model is None
        assert backend.allowed_tools is None
        assert backend.system_prompt_file is None
        assert backend.timeout_seconds == 1800.0
        assert backend.progress_callback is None
        assert backend.cli_extra_args == []

    def test_name_property(self, backend: ClaudeCliBackend):
        assert backend.name == "claude-cli"

    def test_claude_path_set(self, backend: ClaudeCliBackend):
        assert backend._claude_path == "/usr/bin/claude"

    def test_claude_path_none_when_not_found(self, backend_no_cli: ClaudeCliBackend):
        assert backend_no_cli._claude_path is None

    def test_custom_params(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            b = ClaudeCliBackend(
                skip_permissions=False,
                disable_mcp=False,
                output_format="json",
                cli_model="opus",
                allowed_tools=["Bash", "Read"],
                timeout_seconds=60.0,
                cli_extra_args=["--verbose"],
            )
        assert b.skip_permissions is False
        assert b.disable_mcp is False
        assert b.output_format == "json"
        assert b.cli_model == "opus"
        assert b.allowed_tools == ["Bash", "Read"]
        assert b.timeout_seconds == 60.0
        assert b.cli_extra_args == ["--verbose"]


# ─── from_config ─────────────────────────────────────────────────────


class TestFromConfig:
    """Tests for ClaudeCliBackend.from_config()."""

    def test_creates_from_backend_config(self):
        config = MagicMock()
        config.skip_permissions = False
        config.disable_mcp = True
        config.output_format = "json"
        config.cli_model = "sonnet"
        config.allowed_tools = ["Bash"]
        config.system_prompt_file = None
        config.working_directory = Path("/tmp/ws")
        config.timeout_seconds = 300.0
        config.cli_extra_args = []

        with patch("shutil.which", return_value="/usr/bin/claude"):
            b = ClaudeCliBackend.from_config(config)

        assert b.skip_permissions is False
        assert b.cli_model == "sonnet"
        assert b.timeout_seconds == 300.0


# ─── _build_command ──────────────────────────────────────────────────


class TestBuildCommand:
    """Tests for ClaudeCliBackend._build_command()."""

    def test_basic_command(self, backend: ClaudeCliBackend):
        cmd = backend._build_command("Hello")
        assert cmd[0] == "/usr/bin/claude"
        assert "-p" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--output-format" in cmd
        assert "text" in cmd

    def test_mcp_disabled(self, backend: ClaudeCliBackend):
        cmd = backend._build_command("Hello")
        assert "--strict-mcp-config" in cmd
        assert "--mcp-config" in cmd
        assert '{"mcpServers":{}}' in cmd

    def test_mcp_enabled(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            b = ClaudeCliBackend(disable_mcp=False)
        cmd = b._build_command("Hello")
        assert "--strict-mcp-config" not in cmd

    def test_model_added(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            b = ClaudeCliBackend(cli_model="opus")
        cmd = b._build_command("Hello")
        assert "--model" in cmd
        assert "opus" in cmd

    def test_no_model_flag_when_none(self, backend: ClaudeCliBackend):
        cmd = backend._build_command("Hello")
        assert "--model" not in cmd

    def test_allowed_tools(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            b = ClaudeCliBackend(allowed_tools=["Bash", "Read"])
        cmd = b._build_command("Hello")
        assert "--allowedTools" in cmd
        assert "Bash,Read" in cmd

    def test_system_prompt_file(self, tmp_path: Path):
        prompt_file = tmp_path / "system.md"
        with patch("shutil.which", return_value="/usr/bin/claude"):
            b = ClaudeCliBackend(system_prompt_file=prompt_file)
        cmd = b._build_command("Hello")
        assert "--system-prompt" in cmd
        assert str(prompt_file) in cmd

    def test_skip_permissions_false(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            b = ClaudeCliBackend(skip_permissions=False)
        cmd = b._build_command("Hello")
        assert "--dangerously-skip-permissions" not in cmd

    def test_cli_extra_args_appended(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            b = ClaudeCliBackend(cli_extra_args=["--verbose", "--debug"])
        cmd = b._build_command("Hello")
        assert "--verbose" in cmd
        assert "--debug" in cmd

    def test_raises_when_no_cli(self, backend_no_cli: ClaudeCliBackend):
        with pytest.raises(RuntimeError, match="claude CLI not found"):
            backend_no_cli._build_command("Hello")

    def test_preamble_not_in_command_arg(self, backend: ClaudeCliBackend):
        """Preamble is passed via stdin, not embedded in the command (#108)."""
        backend.set_preamble("<mozart-preamble>Test</mozart-preamble>")
        cmd = backend._build_command("Do the task")
        p_idx = cmd.index("-p")
        assert cmd[p_idx + 1] == "-"
        assert "mozart-preamble" not in " ".join(cmd)
        assert "Do the task" not in " ".join(cmd)


# ─── _inject_preamble_and_extensions ─────────────────────────────────


class TestInjectPreambleAndExtensions:
    """Tests for ClaudeCliBackend._inject_preamble_and_extensions()."""

    def test_preamble_prepended(self, backend: ClaudeCliBackend):
        backend.set_preamble("<mozart-preamble>Context info</mozart-preamble>")
        result = backend._inject_preamble_and_extensions("My prompt")
        assert result.startswith("<mozart-preamble>")
        assert "My prompt" in result

    def test_no_preamble_returns_prompt_unchanged(self, backend: ClaudeCliBackend):
        result = backend._inject_preamble_and_extensions("My prompt")
        assert result == "My prompt"

    def test_with_extensions(self, backend: ClaudeCliBackend):
        backend.set_prompt_extensions(["Extension 1", "Extension 2"])
        result = backend._inject_preamble_and_extensions("My prompt")
        assert "Extension 1" in result
        assert "Extension 2" in result
        assert "My prompt" in result

    def test_preamble_and_extensions_order(self, backend: ClaudeCliBackend):
        backend.set_preamble("<mozart-preamble>Preamble</mozart-preamble>")
        backend.set_prompt_extensions(["Extension"])
        result = backend._inject_preamble_and_extensions("My prompt")
        assert result.index("Preamble") < result.index("My prompt") < result.index("Extension")

    def test_empty_extensions_ignored(self, backend: ClaudeCliBackend):
        backend.set_prompt_extensions(["", "  ", "Real extension"])
        result = backend._inject_preamble_and_extensions("My prompt")
        assert "Real extension" in result


# ─── set_prompt_extensions ───────────────────────────────────────────


class TestSetPromptExtensions:
    """Tests for set_prompt_extensions()."""

    def test_sets_extensions(self, backend: ClaudeCliBackend):
        backend.set_prompt_extensions(["ext1", "ext2"])
        assert backend._prompt_extensions == ["ext1", "ext2"]

    def test_filters_empty(self, backend: ClaudeCliBackend):
        backend.set_prompt_extensions(["ext1", "", "  ", "ext2"])
        assert backend._prompt_extensions == ["ext1", "ext2"]


# ─── set_output_log_path ────────────────────────────────────────────


class TestSetOutputLogPath:
    """Tests for set_output_log_path()."""

    def test_sets_paths(self, backend: ClaudeCliBackend, tmp_path: Path):
        backend.set_output_log_path(tmp_path / "sheet-01")
        assert backend._stdout_log_path == tmp_path / "sheet-01.stdout.log"
        assert backend._stderr_log_path == tmp_path / "sheet-01.stderr.log"

    def test_clears_paths(self, backend: ClaudeCliBackend, tmp_path: Path):
        backend.set_output_log_path(tmp_path / "sheet-01")
        backend.set_output_log_path(None)
        assert backend._stdout_log_path is None
        assert backend._stderr_log_path is None


# ─── apply_overrides / clear_overrides ───────────────────────────────


class TestOverrides:
    """Tests for apply_overrides() and clear_overrides()."""

    def test_apply_cli_model_override(self, backend: ClaudeCliBackend):
        assert backend.cli_model is None
        backend.apply_overrides({"cli_model": "opus"})
        assert backend.cli_model == "opus"
        assert backend._has_overrides is True

    def test_clear_restores_original(self, backend: ClaudeCliBackend):
        backend.cli_model = "sonnet"
        backend.apply_overrides({"cli_model": "opus"})
        assert backend.cli_model == "opus"
        backend.clear_overrides()
        assert backend.cli_model == "sonnet"
        assert backend._has_overrides is False

    def test_apply_empty_overrides_noop(self, backend: ClaudeCliBackend):
        backend.apply_overrides({})
        assert backend._has_overrides is False

    def test_clear_without_apply_noop(self, backend: ClaudeCliBackend):
        backend.clear_overrides()  # Should not raise
        assert backend._has_overrides is False


# ─── _parse_returncode ───────────────────────────────────────────────


class TestParseReturncode:
    """Tests for ClaudeCliBackend._parse_returncode()."""

    def test_none_returncode(self, backend: ClaudeCliBackend):
        exit_code, exit_signal, exit_reason, stderr = backend._parse_returncode(None, "err")
        assert exit_code is None
        assert exit_signal is None
        assert exit_reason == "error"
        assert stderr == "err"

    def test_normal_exit(self, backend: ClaudeCliBackend):
        exit_code, exit_signal, exit_reason, stderr = backend._parse_returncode(0, "")
        assert exit_code == 0
        assert exit_signal is None
        assert exit_reason == "completed"

    def test_error_exit(self, backend: ClaudeCliBackend):
        exit_code, exit_signal, exit_reason, stderr = backend._parse_returncode(1, "fail")
        assert exit_code == 1
        assert exit_signal is None
        assert exit_reason == "completed"

    def test_signal_kill(self, backend: ClaudeCliBackend):
        exit_code, exit_signal, exit_reason, stderr = backend._parse_returncode(-9, "")
        assert exit_code is None
        assert exit_signal == 9
        assert exit_reason == "killed"
        assert "SIGKILL" in stderr

    def test_signal_term(self, backend: ClaudeCliBackend):
        exit_code, exit_signal, exit_reason, stderr = backend._parse_returncode(-15, "")
        assert exit_code is None
        assert exit_signal == 15
        assert exit_reason == "killed"
        assert "SIGTERM" in stderr


# ─── _prepare_log_files ─────────────────────────────────────────────


class TestPrepareLogFiles:
    """Tests for _prepare_log_files()."""

    def test_creates_log_files(self, backend: ClaudeCliBackend, tmp_path: Path):
        backend.set_output_log_path(tmp_path / "logs" / "sheet-01")
        backend._prepare_log_files()
        assert backend._stdout_log_path is not None and backend._stdout_log_path.exists()
        assert backend._stderr_log_path is not None and backend._stderr_log_path.exists()

    def test_no_log_path_noop(self, backend: ClaudeCliBackend):
        backend._prepare_log_files()  # Should not raise

    def test_oserror_increments_failures(self, backend: ClaudeCliBackend):
        backend._stdout_log_path = Path("/nonexistent/deep/path.stdout.log")
        backend._stderr_log_path = None
        backend._prepare_log_files()
        assert backend.log_write_failures >= 1


# ─── _write_output_logs ─────────────────────────────────────────────


class TestWriteOutputLogs:
    """Tests for _write_output_logs()."""

    def test_writes_stdout_and_stderr(self, backend: ClaudeCliBackend, tmp_path: Path):
        backend.set_output_log_path(tmp_path / "sheet-01")
        backend._prepare_log_files()
        backend._write_output_logs(b"stdout data", b"stderr data")
        assert backend._stdout_log_path is not None and backend._stdout_log_path.read_bytes() == b"stdout data"
        assert backend._stderr_log_path is not None and backend._stderr_log_path.read_bytes() == b"stderr data"

    def test_no_log_paths_noop(self, backend: ClaudeCliBackend):
        backend._write_output_logs(b"data", b"data")  # Should not raise

    def test_oserror_increments_failures(self, backend: ClaudeCliBackend):
        backend._stdout_log_path = Path("/nonexistent/path.stdout.log")
        backend._stderr_log_path = None
        initial_failures = backend.log_write_failures
        backend._write_output_logs(b"data", b"")
        assert backend.log_write_failures > initial_failures


# ─── _handle_execution_timeout ───────────────────────────────────────


class TestHandleExecutionTimeout:
    """Tests for _handle_execution_timeout()."""

    @pytest.mark.asyncio
    async def test_graceful_termination(self, backend: ClaudeCliBackend):
        """Process exits after SIGTERM within timeout."""
        import time

        proc = _make_mock_process(returncode=None)
        backend._partial_stdout_chunks = [b"partial output"]
        result = await backend._handle_execution_timeout(
            proc, time.monotonic(), 100, 5,
        )
        assert result.success is False
        assert result.exit_reason == "timeout"
        assert result.error_type == "timeout"
        proc.terminate.assert_called_once()
        assert "partial output" in result.stdout

    @pytest.mark.asyncio
    async def test_escalates_to_kill(self, backend: ClaudeCliBackend):
        """Process doesn't exit after SIGTERM → escalates to SIGKILL."""
        import time

        proc = _make_mock_process(returncode=None)
        proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), None])
        result = await backend._handle_execution_timeout(
            proc, time.monotonic(), 50, 3,
        )
        assert result.success is False
        assert result.exit_signal == signal.SIGKILL
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_already_exited(self, backend: ClaudeCliBackend):
        """Process already exited when we try to terminate."""
        import time

        proc = _make_mock_process()
        proc.terminate.side_effect = ProcessLookupError
        result = await backend._handle_execution_timeout(
            proc, time.monotonic(), 0, 0,
        )
        assert result.success is False
        assert result.exit_reason == "timeout"

    @pytest.mark.asyncio
    async def test_progress_callback_called(self, backend: ClaudeCliBackend):
        """Progress callback gets 'timeout' phase notification."""
        import time

        callback = MagicMock()
        backend.progress_callback = callback
        proc = _make_mock_process()
        await backend._handle_execution_timeout(
            proc, time.monotonic(), 100, 5,
        )
        callback.assert_called_once()
        assert callback.call_args[0][0]["phase"] == "timeout"

    @pytest.mark.asyncio
    async def test_partial_stderr_preserved(self, backend: ClaudeCliBackend):
        """Partial stderr collected before timeout is included in result."""
        import time

        proc = _make_mock_process()
        backend._partial_stderr_chunks = [b"some error"]
        result = await backend._handle_execution_timeout(
            proc, time.monotonic(), 0, 0,
        )
        assert "some error" in result.stderr
        assert "timed out" in result.stderr.lower()


# ─── _build_completed_result ─────────────────────────────────────────


class TestBuildCompletedResult:
    """Tests for _build_completed_result()."""

    def test_successful_execution(self, backend: ClaudeCliBackend):
        result = backend._build_completed_result(
            stdout="output", stderr="", exit_code=0,
            exit_signal=None, exit_reason="completed", duration=1.5,
        )
        assert result.success is True
        assert result.stdout == "output"
        assert result.duration_seconds == 1.5

    def test_failed_execution(self, backend: ClaudeCliBackend):
        result = backend._build_completed_result(
            stdout="", stderr="error", exit_code=1,
            exit_signal=None, exit_reason="completed", duration=0.5,
        )
        assert result.success is False
        assert result.exit_code == 1

    def test_rate_limited_detected(self, backend: ClaudeCliBackend):
        """Rate limit detection uses shared ErrorClassifier."""
        with patch.object(backend, "_detect_rate_limit", return_value=True):
            result = backend._build_completed_result(
                stdout="", stderr="rate limited", exit_code=1,
                exit_signal=None, exit_reason="completed", duration=0.5,
            )
        assert result.rate_limited is True
        assert result.error_type == "rate_limit"

    def test_killed_execution(self, backend: ClaudeCliBackend):
        result = backend._build_completed_result(
            stdout="partial", stderr="killed", exit_code=None,
            exit_signal=9, exit_reason="killed", duration=2.0,
        )
        assert result.success is False
        assert result.exit_signal == 9


# ─── _kill_orphaned_process ──────────────────────────────────────────


class TestKillOrphanedProcess:
    """Tests for _kill_orphaned_process()."""

    @pytest.mark.asyncio
    async def test_kills_process_group_and_process(self, backend: ClaudeCliBackend):
        proc = _make_mock_process(returncode=None)
        with patch("os.killpg") as mock_killpg, patch("os.getpgid", return_value=12345):
            await backend._kill_orphaned_process(proc, RuntimeError("test"))
        mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_missing_process_group(self, backend: ClaudeCliBackend):
        proc = _make_mock_process(returncode=None)
        with patch("os.killpg", side_effect=ProcessLookupError), \
             patch("os.getpgid", return_value=12345):
            await backend._kill_orphaned_process(proc, RuntimeError("test"))
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_missing_process(self, backend: ClaudeCliBackend):
        proc = _make_mock_process(returncode=None)
        proc.kill.side_effect = ProcessLookupError
        proc.wait = AsyncMock(side_effect=ProcessLookupError)
        with patch("os.killpg", side_effect=ProcessLookupError), \
             patch("os.getpgid", return_value=12345):
            await backend._kill_orphaned_process(proc, RuntimeError("test"))
        # Should not raise


# ─── _execute_impl error paths ──────────────────────────────────────


class TestExecuteImpl:
    """Tests for _execute_impl() error handling paths."""

    @pytest.mark.asyncio
    async def test_file_not_found(self, backend: ClaudeCliBackend):
        """FileNotFoundError returns exit_code=127."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await backend._execute_impl("test prompt")
        assert result.success is False
        assert result.exit_code == 127
        assert result.error_type == "not_found"

    @pytest.mark.asyncio
    async def test_os_error(self, backend: ClaudeCliBackend):
        """OSError during execution returns error result."""
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("disk full")):
            result = await backend._execute_impl("test prompt")
        assert result.success is False
        assert result.error_type == "exception"
        assert result.error_message is not None and "disk full" in result.error_message

    @pytest.mark.asyncio
    async def test_runtime_error(self, backend: ClaudeCliBackend):
        """RuntimeError during execution returns error result."""
        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("broken")):
            result = await backend._execute_impl("test prompt")
        assert result.success is False
        assert result.error_message is not None and "broken" in result.error_message

    @pytest.mark.asyncio
    async def test_cancelled_error_reraises(self, backend: ClaudeCliBackend):
        """CancelledError kills process and re-raises."""
        proc = _make_mock_process(returncode=None)

        async def mock_create(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create):
            with patch.object(backend, "_stream_with_progress", side_effect=asyncio.CancelledError):
                with patch.object(backend, "_kill_orphaned_process", new_callable=AsyncMock) as mock_kill:
                    with pytest.raises(asyncio.CancelledError):
                        await backend._execute_impl("test")
                    mock_kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_programming_error_reraises(self, backend: ClaudeCliBackend):
        """TypeError (programming bug) is re-raised, not swallowed."""
        proc = _make_mock_process(returncode=None)

        async def mock_create(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create):
            with patch.object(backend, "_stream_with_progress", side_effect=TypeError("bad")):
                with patch.object(backend, "_kill_orphaned_process", new_callable=AsyncMock):
                    with pytest.raises(TypeError, match="bad"):
                        await backend._execute_impl("test")

    @pytest.mark.asyncio
    async def test_timeout_during_streaming(self, backend: ClaudeCliBackend):
        """TimeoutError from streaming calls _handle_execution_timeout."""
        proc = _make_mock_process(returncode=None)

        async def mock_create(*args, **kwargs):
            return proc

        mock_timeout_result = ExecutionResult(
            success=False, stdout="", stderr="timed out",
            duration_seconds=30.0, exit_reason="timeout",
        )
        with patch("asyncio.create_subprocess_exec", side_effect=mock_create):
            with patch.object(backend, "_stream_with_progress", side_effect=TimeoutError):
                with patch.object(
                    backend, "_handle_execution_timeout",
                    new_callable=AsyncMock, return_value=mock_timeout_result,
                ) as mock_handle:
                    result = await backend._execute_impl("test")
                    assert result.exit_reason == "timeout"
                    mock_handle.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_execution(self, backend: ClaudeCliBackend):
        """Full successful execution path."""
        proc = _make_mock_process(returncode=0)

        async def mock_create(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create):
            with patch.object(
                backend, "_stream_with_progress",
                new_callable=AsyncMock, return_value=(b"output", b""),
            ):
                result = await backend._execute_impl("test")
        assert result.success is True
        assert result.stdout == "output"

    @pytest.mark.asyncio
    async def test_progress_callback_start_and_complete(self, backend: ClaudeCliBackend):
        """Progress callback receives 'starting' and 'completed' phases."""
        callback = MagicMock()
        backend.progress_callback = callback
        proc = _make_mock_process(returncode=0)

        async def mock_create(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create):
            with patch.object(
                backend, "_stream_with_progress",
                new_callable=AsyncMock, return_value=(b"out", b""),
            ):
                await backend._execute_impl("test")

        phases = [call[0][0]["phase"] for call in callback.call_args_list]
        assert "starting" in phases
        assert "completed" in phases

    @pytest.mark.asyncio
    async def test_per_call_timeout_override(self, backend: ClaudeCliBackend):
        """timeout_seconds parameter overrides instance timeout."""
        proc = _make_mock_process(returncode=0)

        async def mock_create(*args, **kwargs):
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_create):
            with patch.object(
                backend, "_stream_with_progress",
                new_callable=AsyncMock, return_value=(b"out", b""),
            ) as mock_stream:
                await backend._execute_impl("test", timeout_seconds=60.0)
                call_kwargs = mock_stream.call_args[1]
                assert call_kwargs["effective_timeout"] == 60.0


# ─── _await_process_exit ────────────────────────────────────────────


class TestAwaitProcessExit:
    """Tests for _await_process_exit()."""

    @pytest.mark.asyncio
    async def test_normal_exit(self, backend: ClaudeCliBackend):
        """Process exits normally within timeout."""
        proc = _make_mock_process(returncode=0)
        await backend._await_process_exit(proc)
        proc.wait.assert_called()

    @pytest.mark.asyncio
    async def test_timeout_kills_process_group(self, backend: ClaudeCliBackend):
        """Process doesn't exit → SIGTERM → still won't exit → SIGKILL."""
        proc = _make_mock_process(returncode=None, pid=9999)
        call_count = 0

        async def timeout_then_exit():
            nonlocal call_count
            call_count += 1
            # First 2 calls: initial wait + SIGTERM wait both timeout
            if call_count <= 2:
                raise asyncio.TimeoutError()
            # Third call: after SIGKILL, process finally exits
            return 0

        proc.wait = timeout_then_exit
        with patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=9999):
            await backend._await_process_exit(proc)
        # SIGTERM fails (timeout), escalates to SIGKILL on process group.
        assert mock_killpg.call_count == 2
        mock_killpg.assert_any_call(9999, signal.SIGTERM)
        mock_killpg.assert_any_call(9999, signal.SIGKILL)


# ─── execute ─────────────────────────────────────────────────────────


class TestExecute:
    """Tests for execute() (Backend protocol)."""

    @pytest.mark.asyncio
    async def test_delegates_to_execute_impl(self, backend: ClaudeCliBackend):
        mock_result = ExecutionResult(
            success=True, stdout="ok", stderr="", duration_seconds=1.0,
        )
        with patch.object(
            backend, "_execute_impl",
            new_callable=AsyncMock, return_value=mock_result,
        ) as mock_impl:
            result = await backend.execute("my prompt", timeout_seconds=120.0)
            assert result.success is True
            mock_impl.assert_called_once_with("my prompt", timeout_seconds=120.0)


# ─── health_check ───────────────────────────────────────────────────


class TestHealthCheck:
    """Tests for health_check()."""

    @pytest.mark.asyncio
    async def test_no_cli_returns_false(self, backend_no_cli: ClaudeCliBackend):
        assert await backend_no_cli.health_check() is False

    @pytest.mark.asyncio
    async def test_successful_health_check(self, backend: ClaudeCliBackend):
        mock_result = ExecutionResult(
            success=True, stdout="I am ready", stderr="", duration_seconds=0.5,
        )
        with patch.object(
            backend, "_execute_impl",
            new_callable=AsyncMock, return_value=mock_result,
        ):
            assert await backend.health_check() is True

    @pytest.mark.asyncio
    async def test_failed_health_check(self, backend: ClaudeCliBackend):
        mock_result = ExecutionResult(
            success=False, stdout="", stderr="error", duration_seconds=0.5,
        )
        with patch.object(
            backend, "_execute_impl",
            new_callable=AsyncMock, return_value=mock_result,
        ):
            assert await backend.health_check() is False

    @pytest.mark.asyncio
    async def test_ready_not_in_output(self, backend: ClaudeCliBackend):
        mock_result = ExecutionResult(
            success=True, stdout="something else", stderr="", duration_seconds=0.5,
        )
        with patch.object(
            backend, "_execute_impl",
            new_callable=AsyncMock, return_value=mock_result,
        ):
            assert await backend.health_check() is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, backend: ClaudeCliBackend):
        with patch.object(
            backend, "_execute_impl",
            new_callable=AsyncMock, side_effect=TimeoutError("timeout"),
        ):
            assert await backend.health_check() is False

    @pytest.mark.asyncio
    async def test_os_error_returns_false(self, backend: ClaudeCliBackend):
        with patch.object(
            backend, "_execute_impl",
            new_callable=AsyncMock, side_effect=OSError("fail"),
        ):
            assert await backend.health_check() is False


# ─── availability_check ────────────────────────────────────────────


class TestAvailabilityCheck:
    """Tests for availability_check() — lightweight, no-prompt check."""

    @pytest.mark.asyncio
    async def test_no_cli_returns_false(self, backend_no_cli: ClaudeCliBackend):
        assert await backend_no_cli.availability_check() is False

    @pytest.mark.asyncio
    async def test_valid_binary_returns_true(self, backend: ClaudeCliBackend):
        with (
            patch("os.path.isfile", return_value=True),
            patch("os.access", return_value=True),
        ):
            assert await backend.availability_check() is True

    @pytest.mark.asyncio
    async def test_missing_binary_returns_false(self, backend: ClaudeCliBackend):
        with patch("os.path.isfile", return_value=False):
            assert await backend.availability_check() is False

    @pytest.mark.asyncio
    async def test_not_executable_returns_false(self, backend: ClaudeCliBackend):
        with (
            patch("os.path.isfile", return_value=True),
            patch("os.access", return_value=False),
        ):
            assert await backend.availability_check() is False

    @pytest.mark.asyncio
    async def test_does_not_call_execute_impl(self, backend: ClaudeCliBackend):
        """availability_check must NOT send prompts or consume quota."""
        with (
            patch("os.path.isfile", return_value=True),
            patch("os.access", return_value=True),
            patch.object(
                backend, "_execute_impl",
                new_callable=AsyncMock,
            ) as mock_exec,
        ):
            await backend.availability_check()
            mock_exec.assert_not_called()
