"""Tests for ``mzt stop`` safety guard (#94).

When jobs are actively running, ``mzt stop`` must warn the user and
ask for confirmation before proceeding.  The ``--force`` flag skips
the safety check entirely.  If the IPC probe fails (conductor
unresponsive), stop proceeds without blocking.

Covers:
1. _check_running_jobs: IPC probe returns running job count
2. stop_conductor: warns when jobs running, proceeds when none
3. --force skips safety check
4. IPC failure falls through gracefully
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. _check_running_jobs
# ---------------------------------------------------------------------------


class TestCheckRunningJobs:
    """_check_running_jobs must probe via IPC and return results."""

    def test_returns_running_count(self) -> None:
        """Returns running_jobs count from readiness probe."""
        from marianne.daemon.process import _check_running_jobs

        mock_client = MagicMock(spec=["readiness"])
        mock_client.readiness = AsyncMock(
            return_value={"running_jobs": 3, "job_ids": ["a", "b", "c"]},
        )

        with (
            patch(
                "marianne.daemon.ipc.client.DaemonClient",
                return_value=mock_client,
            ),
            patch(
                "marianne.daemon.detect._resolve_socket_path",
                return_value=Path("/tmp/test.sock"),
            ),
        ):
            result = _check_running_jobs()

        assert result is not None
        assert result["running_jobs"] == 3

    def test_returns_none_on_ipc_failure(self) -> None:
        """Returns None when IPC connection fails."""
        from marianne.daemon.process import _check_running_jobs

        with (
            patch(
                "marianne.daemon.ipc.client.DaemonClient",
                side_effect=ConnectionError("refused"),
            ),
            patch(
                "marianne.daemon.detect._resolve_socket_path",
                return_value=Path("/tmp/test.sock"),
            ),
        ):
            result = _check_running_jobs()

        assert result is None

    def test_returns_zero_when_no_jobs(self) -> None:
        """Returns running_jobs=0 when conductor has no running jobs."""
        from marianne.daemon.process import _check_running_jobs

        mock_client = MagicMock(spec=["readiness"])
        mock_client.readiness = AsyncMock(
            return_value={"running_jobs": 0, "job_ids": []},
        )

        with (
            patch(
                "marianne.daemon.ipc.client.DaemonClient",
                return_value=mock_client,
            ),
            patch(
                "marianne.daemon.detect._resolve_socket_path",
                return_value=Path("/tmp/test.sock"),
            ),
        ):
            result = _check_running_jobs()

        assert result is not None
        assert result["running_jobs"] == 0


# ---------------------------------------------------------------------------
# 2. stop_conductor safety behavior
# ---------------------------------------------------------------------------


class TestStopConductorSafety:
    """stop_conductor must check running jobs before killing."""

    @pytest.fixture(autouse=True)
    def _mock_pid(self) -> Any:
        """Mock PID file and process existence."""
        with (
            patch(
                "marianne.daemon.process._read_pid",
                return_value=12345,
            ),
            patch(
                "marianne.daemon.process._pid_alive",
                return_value=True,
            ),
        ):
            yield

    def test_warns_when_jobs_running(self) -> None:
        """Shows warning when jobs are running and user declines."""
        from marianne.daemon.process import stop_conductor

        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 2, "job_ids": ["j1", "j2"]},
            ),
            patch("marianne.daemon.process.os.kill") as mock_kill,
            patch("marianne.daemon.process.typer.confirm", return_value=False),
            pytest.raises((SystemExit, BaseException)),
        ):
            stop_conductor()

        mock_kill.assert_not_called()

    def test_proceeds_when_user_confirms(self) -> None:
        """Proceeds when user confirms despite running jobs."""
        from marianne.daemon.process import stop_conductor

        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 2, "job_ids": ["j1", "j2"]},
            ),
            patch("marianne.daemon.process.os.kill") as mock_kill,
            patch("marianne.daemon.process.typer.confirm", return_value=True),
        ):
            stop_conductor()

        mock_kill.assert_called_once()

    def test_no_prompt_when_no_jobs(self) -> None:
        """No confirmation prompt when no jobs are running."""
        from marianne.daemon.process import stop_conductor

        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ),
            patch("marianne.daemon.process.os.kill") as mock_kill,
            patch("marianne.daemon.process.typer.confirm") as mock_confirm,
        ):
            stop_conductor()

        mock_kill.assert_called_once()
        mock_confirm.assert_not_called()

    def test_force_skips_check(self) -> None:
        """--force skips the running jobs check entirely."""
        from marianne.daemon.process import stop_conductor

        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
            ) as mock_check,
            patch("marianne.daemon.process.os.kill") as mock_kill,
        ):
            stop_conductor(force=True)

        mock_check.assert_not_called()
        mock_kill.assert_called_once()

    def test_ipc_failure_proceeds(self) -> None:
        """When IPC check fails (returns None), stop proceeds."""
        from marianne.daemon.process import stop_conductor

        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value=None,
            ),
            patch("marianne.daemon.process.os.kill") as mock_kill,
            patch("marianne.daemon.process.typer.confirm") as mock_confirm,
        ):
            stop_conductor()

        mock_kill.assert_called_once()
        mock_confirm.assert_not_called()

    def test_force_sends_sigkill(self) -> None:
        """--force sends SIGKILL instead of SIGTERM."""
        import signal

        from marianne.daemon.process import stop_conductor

        with (
            patch("marianne.daemon.process.os.kill") as mock_kill,
        ):
            stop_conductor(force=True)

        mock_kill.assert_called_once_with(12345, signal.SIGKILL)

    def test_normal_stop_sends_sigterm(self) -> None:
        """Normal stop sends SIGTERM."""
        import signal

        from marianne.daemon.process import stop_conductor

        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ),
            patch("marianne.daemon.process.os.kill") as mock_kill,
        ):
            stop_conductor()

        mock_kill.assert_called_once_with(12345, signal.SIGTERM)
