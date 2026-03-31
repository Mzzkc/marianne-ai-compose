"""Regression tests for SIGABRT crash during parallel execution.

Root Cause: When a sibling sheet's Claude CLI gets SIGABRT, the parallel
executor cancels remaining tasks. In Python 3.12, asyncio.CancelledError
inherits from BaseException (not Exception). Mozart's cleanup handlers
previously only caught Exception, so cancellation bypassed all subprocess
cleanup — leaving zombie processes, leaked FDs, and orphaned MCP servers.

These tests verify the fixes hold:
1. CancelledError triggers subprocess cleanup (kill + wait)
2. Parallel cancellation doesn't crash Mozart
3. _kill_orphaned_process accepts BaseException
4. find_job_state handles backend errors gracefully
"""

import asyncio
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozart.backends.claude_cli import ClaudeCliBackend
from mozart.core.checkpoint import SheetState



def _make_mock_process(
    returncode: int | None = None,
    pid: int = 12345,
) -> MagicMock:
    """Create a mock asyncio.subprocess.Process for cancellation tests.

    Returns a process that appears to be running (returncode=None)
    so cleanup handlers attempt to kill and wait on it.
    """
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.pid = pid
    proc.returncode = returncode
    proc.wait = AsyncMock(return_value=-9)

    # Minimal stream readers that return EOF immediately
    stdout_reader = AsyncMock()
    stdout_reader.read = AsyncMock(return_value=b"")
    stderr_reader = AsyncMock()
    stderr_reader.read = AsyncMock(return_value=b"")
    proc.stdout = stdout_reader
    proc.stderr = stderr_reader
    proc.stdin = AsyncMock()

    return proc


def _make_backend(**kwargs) -> ClaudeCliBackend:
    """Create a ClaudeCliBackend with a fake claude path."""
    backend = ClaudeCliBackend(**kwargs)
    backend._claude_path = "/usr/local/bin/claude"
    return backend



class TestCancelledErrorTriggersCleanup:
    """Verify that CancelledError during execution kills and waits on the subprocess.

    Previously, CancelledError (BaseException) flew past the `except Exception`
    handler in _execute_impl, leaving the subprocess as a zombie with leaked FDs.
    """

    @pytest.mark.asyncio
    async def test_cancelled_error_kills_and_waits_on_process(self) -> None:
        """When a task is cancelled during execution, the subprocess must be
        killed via process group and then waited on to reap the zombie."""
        backend = _make_backend()
        proc = _make_mock_process(returncode=None)

        # Make streaming raise CancelledError (simulates TaskGroup cancellation)
        async def _raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            patch.object(backend, "_build_command", return_value=["claude", "-p", "test"]),
            patch.object(backend, "_prepare_log_files"),
            patch.object(backend, "_stream_with_progress", side_effect=_raise_cancelled),
            patch("os.killpg") as mock_killpg,
            patch("os.getpgid", return_value=12345),
        ):
            with pytest.raises(asyncio.CancelledError):
                await backend._execute_impl("test prompt")

        # Must have killed the process group
        mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
        # Must have called process.kill() and process.wait()
        proc.kill.assert_called_once()
        proc.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancelled_error_skips_cleanup_if_process_already_exited(self) -> None:
        """If the process already exited before cancellation, no cleanup needed."""
        backend = _make_backend()
        proc = _make_mock_process(returncode=0)  # Already exited

        async def _raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            patch.object(backend, "_build_command", return_value=["claude", "-p", "test"]),
            patch.object(backend, "_prepare_log_files"),
            patch.object(backend, "_stream_with_progress", side_effect=_raise_cancelled),
            patch("os.killpg") as mock_killpg,
        ):
            with pytest.raises(asyncio.CancelledError):
                await backend._execute_impl("test prompt")

        # Process already exited — should NOT attempt kill
        mock_killpg.assert_not_called()
        proc.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_error_before_process_created(self) -> None:
        """CancelledError before subprocess is created should propagate cleanly."""
        backend = _make_backend()

        # CancelledError during subprocess creation itself
        with (
            patch(
                "asyncio.create_subprocess_exec",
                AsyncMock(side_effect=asyncio.CancelledError()),
            ),
            patch.object(backend, "_build_command", return_value=["claude", "-p", "test"]),
            patch.object(backend, "_prepare_log_files"),
        ):
            with pytest.raises(asyncio.CancelledError):
                await backend._execute_impl("test prompt")



class TestStreamCancelledErrorReapsZombie:
    """Verify that _stream_with_progress kills AND waits on the process.

    Previously, the handler killed the process but never called wait(),
    leaving a zombie and leaked FDs.
    """

    @pytest.mark.asyncio
    async def test_stream_cancelled_kills_process_group_and_waits(self) -> None:
        """CancelledError in streaming must kill process group, then wait()."""
        backend = _make_backend()
        proc = _make_mock_process(returncode=None)

        # Make the gather raise CancelledError
        async def _cancel_gather(*args, **kwargs):
            raise asyncio.CancelledError()

        with (
            patch("asyncio.wait_for", side_effect=_cancel_gather),
            patch("os.killpg") as mock_killpg,
            patch("os.getpgid", return_value=proc.pid),
        ):
            with pytest.raises(asyncio.CancelledError):
                await backend._stream_with_progress(
                    proc,
                    start_time=0.0,
                    notify_progress=lambda phase: None,
                )

        # Must kill process group: SIGTERM (graceful) then SIGKILL (force)
        assert mock_killpg.call_count == 2
        mock_killpg.assert_any_call(proc.pid, signal.SIGTERM)
        mock_killpg.assert_any_call(proc.pid, signal.SIGKILL)
        # Must call process.kill() as final escalation
        proc.kill.assert_called_once()
        # Must call process.wait() to reap the zombie
        proc.wait.assert_called_once()



class TestKillOrphanedProcessAcceptsBaseException:
    """Verify _kill_orphaned_process works with both Exception and BaseException.

    Previously the type signature was `error: Exception` which caused TypeError
    when called with CancelledError (a BaseException in Python 3.12).
    """

    @pytest.mark.asyncio
    async def test_accepts_cancelled_error(self) -> None:
        """_kill_orphaned_process must accept CancelledError without TypeError."""
        backend = _make_backend()
        proc = _make_mock_process(returncode=None)

        with (
            patch("os.killpg"),
            patch("os.getpgid", return_value=proc.pid),
        ):
            # This should NOT raise TypeError
            await backend._kill_orphaned_process(proc, asyncio.CancelledError())

        proc.kill.assert_called_once()
        proc.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_accepts_keyboard_interrupt(self) -> None:
        """_kill_orphaned_process must accept KeyboardInterrupt (BaseException)."""
        backend = _make_backend()
        proc = _make_mock_process(returncode=None)

        with (
            patch("os.killpg"),
            patch("os.getpgid", return_value=proc.pid),
        ):
            await backend._kill_orphaned_process(proc, KeyboardInterrupt())

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_still_accepts_regular_exception(self) -> None:
        """_kill_orphaned_process must still work with regular Exception."""
        backend = _make_backend()
        proc = _make_mock_process(returncode=None)

        with (
            patch("os.killpg"),
            patch("os.getpgid", return_value=proc.pid),
        ):
            await backend._kill_orphaned_process(proc, RuntimeError("test"))

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_already_exited_process(self) -> None:
        """_kill_orphaned_process handles ProcessLookupError gracefully."""
        backend = _make_backend()
        proc = _make_mock_process(returncode=None)
        proc.kill.side_effect = ProcessLookupError

        with (
            patch("os.killpg", side_effect=ProcessLookupError),
            patch("os.getpgid", return_value=proc.pid),
        ):
            # Should not raise — graceful handling of already-exited process
            await backend._kill_orphaned_process(proc, asyncio.CancelledError())
        assert proc.kill.called



class TestParallelCancellationNoCrash:
    """Verify that when one parallel sheet dies (SIGABRT), the other sheets
    are cancelled cleanly without RuntimeError: Event loop is closed.

    Previously, CancelledError bypassed cleanup in sibling tasks, leaving
    asyncio transports open when the event loop closed.
    """

    @pytest.fixture
    def parallel_runner(self):
        """Create a mock runner for parallel execution testing."""
        from mozart.state.base import StateBackend

        runner = MagicMock(spec=["_state_lock", "state_backend", "dependency_dag",
                                   "_execute_sheet_with_recovery"])
        runner._state_lock = asyncio.Lock()
        runner.state_backend = MagicMock(spec=StateBackend)
        runner.state_backend.save = AsyncMock()
        runner.state_backend.load = AsyncMock(return_value=None)
        return runner

    @pytest.mark.asyncio
    async def test_sigabrt_in_one_sheet_doesnt_crash_executor(
        self, parallel_runner,
    ) -> None:
        """One sheet dying with SIGABRT should not crash the parallel executor."""
        from mozart.core.checkpoint import CheckpointState, SheetStatus
        from mozart.execution.dag import DependencyDAG
        from mozart.execution.parallel import ParallelExecutionConfig, ParallelExecutor

        dag = DependencyDAG.from_dependencies(total_sheets=3, dependencies=None)
        parallel_runner.dependency_dag = dag

        state = MagicMock(spec=CheckpointState)
        state.total_sheets = 3
        state.sheets = {}

        async def mock_execute(st, sheet_num):
            if sheet_num == 2:
                raise RuntimeError("Process killed by SIGABRT (signal 6)")
            await asyncio.sleep(0.01)
            st.sheets[sheet_num] = MagicMock(spec=SheetState, status=SheetStatus.COMPLETED)

        parallel_runner._execute_sheet_with_recovery = AsyncMock(
            side_effect=mock_execute,
        )

        config = ParallelExecutionConfig(
            enabled=True, max_concurrent=3, fail_fast=True,
        )

        executor = ParallelExecutor(parallel_runner, config)
        batch = executor.get_next_parallel_batch(state)
        result = await executor.execute_batch(batch, state)
        assert not result.success
        assert 2 in result.failed

    @pytest.mark.asyncio
    async def test_multiple_sheets_fail_simultaneously(
        self, parallel_runner,
    ) -> None:
        """Multiple sheets failing at the same time should not cause crashes."""
        from mozart.core.checkpoint import CheckpointState, SheetStatus
        from mozart.execution.dag import DependencyDAG
        from mozart.execution.parallel import ParallelExecutionConfig, ParallelExecutor

        dag = DependencyDAG.from_dependencies(total_sheets=4, dependencies=None)
        parallel_runner.dependency_dag = dag

        state = MagicMock(spec=CheckpointState)
        state.total_sheets = 4
        state.sheets = {}

        async def mock_execute(st, sheet_num):
            if sheet_num in (1, 3):
                raise RuntimeError(f"Sheet {sheet_num} SIGABRT")
            await asyncio.sleep(0.01)
            st.sheets[sheet_num] = MagicMock(spec=SheetState, status=SheetStatus.COMPLETED)

        parallel_runner._execute_sheet_with_recovery = AsyncMock(
            side_effect=mock_execute,
        )

        config = ParallelExecutionConfig(
            enabled=True, max_concurrent=4, fail_fast=True,
        )

        executor = ParallelExecutor(parallel_runner, config)
        batch = executor.get_next_parallel_batch(state)
        result = await executor.execute_batch(batch, state)
        assert not result.success



class TestFindJobStateBackendErrors:
    """Verify that find_job_state falls back to the next backend when one errors.

    When SQLite backend raises (e.g., database locked during crash recovery),
    the JSON fallback must still work so `mozart status` doesn't crash.
    """

    @pytest.mark.asyncio
    async def test_sqlite_error_falls_back_to_json(self, tmp_path: Path) -> None:
        """SQLite backend error should not prevent JSON fallback from working."""
        from mozart.cli.helpers import _find_job_state_fs as find_job_state

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Create a fake SQLite file so it gets tried first
        (workspace / ".mozart-state.db").touch()

        # Create a JSON state file that the fallback should find
        import json
        state_data = {
            "job_id": "test-job",
            "job_name": "test-job",
            "total_sheets": 3,
            "status": "running",
            "current_sheet": 1,
            "last_completed_sheet": 0,
            "sheets": {},
        }
        (workspace / "test-job.json").write_text(json.dumps(state_data))

        # find_job_state should succeed via JSON fallback
        # The SQLite backend will fail to open the empty file but
        # the JSON backend should work
        found_state, found_backend = await find_job_state("test-job", workspace)

        # Should have found the state (possibly via SQLite if it handles empty
        # files, but definitely via JSON fallback)
        assert found_state is not None
        assert found_state.job_id == "test-job"

    @pytest.mark.asyncio
    async def test_all_backends_error_returns_none(self, tmp_path: Path) -> None:
        """When all backends fail, find_job_state returns (None, None)."""
        from mozart.cli.helpers import _find_job_state_fs as find_job_state

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # No state files at all
        found_state, found_backend = await find_job_state("nonexistent-job", workspace)

        assert found_state is None
        assert found_backend is None

    @pytest.mark.asyncio
    async def test_backend_exception_logged_not_raised(self, tmp_path: Path) -> None:
        """Backend exceptions should be caught and logged, not propagated."""
        from mozart.cli.helpers import _find_job_state_fs as find_job_state
        from mozart.state import JsonStateBackend

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Patch JsonStateBackend.load to raise
        with patch.object(
            JsonStateBackend, "load", AsyncMock(side_effect=OSError("disk error")),
        ):
            # Should NOT raise — errors are caught internally
            found_state, found_backend = await find_job_state("test-job", workspace)

        assert found_state is None
        assert found_backend is None



class TestLoggerKeywordArgs:
    """Verify that the specific logger call fixed in the brief uses keyword args.

    This is a targeted regression test — the broader logger audit found ~67
    printf-style violations across the codebase, but this specific call was
    in the crash path for `mozart status` during SIGABRT recovery.
    """

    @pytest.mark.asyncio
    async def test_find_job_state_logs_with_keyword_args(self, tmp_path: Path) -> None:
        """find_job_state must use structlog keyword args, not printf-style %s."""
        import structlog
        from structlog.types import EventDict, WrappedLogger

        from mozart.cli.helpers import _find_job_state_fs as find_job_state
        from mozart.state import JsonStateBackend

        captured_logs: list[dict] = []

        def capture_to_list(
            logger: WrappedLogger, method_name: str, event_dict: EventDict,
        ) -> EventDict:
            captured_logs.append({"level": method_name, **event_dict})
            raise structlog.DropEvent

        structlog.configure(
            processors=[capture_to_list],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=False,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Make backend raise to trigger the debug log
        with patch.object(
            JsonStateBackend, "load",
            AsyncMock(side_effect=RuntimeError("test error")),
        ):
            await find_job_state("test-job", workspace)

        # Find the backend error log entry (unexpected errors use a different event name)
        error_logs = [
            log for log in captured_logs
            if log.get("event") in ("error_querying_backend", "unexpected_error_querying_backend")
        ]

        # Should have logged with keyword args
        assert len(error_logs) >= 1
        log_entry = error_logs[0]
        assert log_entry["job_id"] == "test-job"
        assert "test error" in log_entry["error"]
        # Should NOT have positional args (printf-style would put them
        # in positional_args key or cause a formatting error)
        assert "positional_args" not in log_entry
