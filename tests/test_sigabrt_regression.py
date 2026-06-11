"""Regression tests for SIGABRT crash during parallel execution.

Root Cause: When a sibling sheet's Claude CLI gets SIGABRT, the parallel
executor cancels remaining tasks. In Python 3.12, asyncio.CancelledError
inherits from BaseException (not Exception). Marianne's cleanup handlers
previously only caught Exception, so cancellation bypassed all subprocess
cleanup — leaving zombie processes, leaked FDs, and orphaned MCP servers.

These tests verify the fixes hold:
1. CancelledError triggers subprocess cleanup (kill + wait)
2. Parallel cancellation doesn't crash Marianne
3. _kill_orphaned_process accepts BaseException
4. find_job_state handles backend errors gracefully
"""

import asyncio
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.execution.instruments.claude_cli_legacy import ClaudeCliBackend


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

        # F-490 guard: getpgid(0) must return the real own-pgroup so the guard
        # sees that our fake target pgid (12345) is NOT the caller's own pgroup.
        real_own_pgid = os.getpgid(0)
        assert real_own_pgid != 12345, "test assumption: own pgid != fake pgid"
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            patch.object(backend, "_build_command", return_value=["claude", "-p", "test"]),
            patch.object(backend, "_prepare_log_files"),
            patch.object(backend, "_stream_with_progress", side_effect=_raise_cancelled),
            patch("os.killpg") as mock_killpg,
            patch(
                "os.getpgid",
                side_effect=lambda pid: real_own_pgid if pid == 0 else 12345,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
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
            pytest.raises(asyncio.CancelledError),
        ):
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
            pytest.raises(asyncio.CancelledError),
        ):
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

        # F-490 guard: getpgid(0) must return the real own-pgroup so the guard
        # sees that proc.pid's pgid is NOT the caller's own pgroup.
        real_own_pgid = os.getpgid(0)
        assert real_own_pgid != proc.pid, "test assumption: own pgid != proc.pid"
        with (
            patch("asyncio.wait_for", side_effect=_cancel_gather),
            patch("os.killpg") as mock_killpg,
            patch(
                "os.getpgid",
                side_effect=lambda pid: real_own_pgid if pid == 0 else proc.pid,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
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


class TestFindJobStateBackendErrors:
    """#50: offline reads are conductor-ONLY (registry, no workspace
    fallback). The SIGABRT hazard this class guards is unchanged: an
    erroring backend during an offline state read must NEVER crash
    `mzt status` — it degrades to not-found.
    """

    async def test_registry_error_degrades_to_not_found(self, tmp_path: Path) -> None:
        """A corrupt/locked registry DB must not raise out of the read."""
        from marianne.cli import helpers
        from marianne.cli.helpers import _find_job_state_fs as find_job_state

        bad_db = tmp_path / "daemon-state.db"
        bad_db.write_text("this is not a sqlite database")

        with patch.object(helpers, "DAEMON_STATE_DB_PATH", bad_db):
            state, backend = await find_job_state("test-job", None)

        assert state is None
        assert backend is None


class TestLoggerKeywordArgs:
    """Verify that the specific logger call fixed in the brief uses keyword args.

    This is a targeted regression test — the broader logger audit found ~67
    printf-style violations across the codebase, but this specific call was
    in the crash path for `mzt status` during SIGABRT recovery.
    """

    @pytest.mark.asyncio
    async def test_find_job_state_logs_with_keyword_args(self, tmp_path: Path) -> None:
        """The registry-read error path must use structlog keyword args."""
        import structlog

        from marianne.cli import helpers
        from marianne.cli.helpers import _find_job_state_fs as find_job_state

        # A corrupt registry DB triggers the registry_read_failed warning.
        bad_db = tmp_path / "daemon-state.db"
        bad_db.write_text("this is not a sqlite database")
        with (
            patch.object(helpers, "DAEMON_STATE_DB_PATH", bad_db),
            structlog.testing.capture_logs() as captured_logs,
        ):
            await find_job_state("test-job", None)

        error_logs = [
            log
            for log in captured_logs
            if log.get("event") == "find_job_state.registry_read_failed"
        ]
        assert len(error_logs) >= 1
        assert error_logs[0]["job_id"] == "test-job"
        assert "positional_args" not in error_logs[0]
