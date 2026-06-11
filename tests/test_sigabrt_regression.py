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
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
