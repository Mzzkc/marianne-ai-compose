"""Tests for ``mzt stop`` safety guard (#94).

When jobs are actively running, ``mzt stop`` must warn the user and
ask for confirmation before proceeding.  The ``--force`` flag skips
the safety check entirely.  If the IPC probe fails (conductor
unresponsive), normal stop refuses unknown work.

Covers:
1. _check_running_jobs: IPC probe returns running job count
2. stop_conductor: warns when jobs running, proceeds when none
3. --force skips safety check
4. IPC failure refuses unsafe normal stop
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from marianne.daemon.process import stop_conductor

# ---------------------------------------------------------------------------
# 1. _check_running_jobs
# ---------------------------------------------------------------------------


class TestCheckRunningJobs:
    """_check_running_jobs must probe via IPC and return results."""

    def test_returns_running_count(self) -> None:
        """Returns running_jobs count from readiness probe."""
        from marianne.daemon.process import _check_running_jobs

        mock_client = MagicMock(spec=["readiness", "close"])
        mock_client.close = AsyncMock()
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

        mock_client = MagicMock(spec=["readiness", "close"])
        mock_client.close = AsyncMock()
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
        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
            ) as mock_check,
            patch("marianne.daemon.process.os.kill") as mock_kill,
        ):
            stop_conductor(force=True)

        mock_check.assert_not_called()
        mock_kill.assert_called_once()

    def test_ipc_failure_refuses_unknown_work(self) -> None:
        """Unknown work needs an explicit force operation after operator verification."""
        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value=None,
            ),
            patch("marianne.daemon.process.os.kill") as mock_kill,
            patch("marianne.daemon.process.typer.confirm") as mock_confirm,
        ):
            with pytest.raises(typer.Exit) as exc:
                stop_conductor()
            assert exc.value.exit_code == 1

        mock_kill.assert_not_called()
        mock_confirm.assert_not_called()

    def test_force_sends_sigkill(self) -> None:
        """--force sends SIGKILL instead of SIGTERM."""
        import signal

        with (
            patch("marianne.daemon.process.os.kill") as mock_kill,
        ):
            stop_conductor(force=True)

        mock_kill.assert_called_once_with(12345, signal.SIGKILL)

    def test_normal_stop_sends_sigterm(self) -> None:
        """Normal stop sends SIGTERM."""
        import signal

        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ),
            patch("marianne.daemon.process.os.kill") as mock_kill,
        ):
            stop_conductor()

        mock_kill.assert_called_once_with(12345, signal.SIGTERM)


# ---------------------------------------------------------------------------
# 3. stop_conductor non-blocking contract and bounded --wait (GH #115)
# ---------------------------------------------------------------------------


class TestStopConductorWaitContract:
    """stop_conductor's blocking behavior must be explicit and pinned.

    Default: signal and return — never wait (non-blocking contract).
    wait=N: block on wait_for_conductor_exit with that budget; a timeout
    is a truthful exit 1, not a silent success.
    """

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

    def test_default_stop_is_non_blocking(self) -> None:
        """Default: signal sent, wait_for_conductor_exit NEVER called."""
        import signal

        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ),
            patch("marianne.daemon.process.os.kill") as mock_kill,
            patch(
                "marianne.daemon.process.wait_for_conductor_exit"
            ) as mock_wait,
        ):
            stop_conductor()

        mock_kill.assert_called_once_with(12345, signal.SIGTERM)
        mock_wait.assert_not_called()

    def test_wait_calls_wait_for_conductor_exit_with_budget(self) -> None:
        """wait=12.0 reaches wait_for_conductor_exit with timeout=12.0."""
        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ),
            patch("marianne.daemon.process.os.kill"),
            patch(
                "marianne.daemon.process.wait_for_conductor_exit", return_value=True
            ) as mock_wait,
        ):
            stop_conductor(wait=12.0)

        mock_wait.assert_called_once()
        assert mock_wait.call_args.kwargs.get("timeout") == 12.0

    def test_wait_timeout_exits_1(self) -> None:
        """wait timeout: truthful exit 1, PID named in the message."""
        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ),
            patch("marianne.daemon.process.os.kill"),
            patch(
                "marianne.daemon.process.wait_for_conductor_exit", return_value=False
            ),
        ):
            with pytest.raises(typer.Exit) as exc:
                stop_conductor(wait=5.0)
            assert exc.value.exit_code == 1

    def test_wait_zero_keeps_non_blocking(self) -> None:
        """wait=0 is explicitly today's signal-and-return behavior."""
        with (
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ),
            patch("marianne.daemon.process.os.kill"),
            patch(
                "marianne.daemon.process.wait_for_conductor_exit"
            ) as mock_wait,
        ):
            stop_conductor(wait=0.0)

        mock_wait.assert_not_called()

    @pytest.mark.parametrize("budget", [float("nan"), float("inf"), -1.0])
    def test_invalid_wait_refused_before_signal(self, budget: float) -> None:
        with (
            patch("marianne.daemon.process.os.kill") as mock_kill,
            patch(
                "marianne.daemon.process._check_running_jobs",
                return_value={"running_jobs": 0, "job_ids": []},
            ) as mock_probe,
            patch("marianne.daemon.process.wait_for_conductor_exit") as mock_wait,
            pytest.raises(ValueError, match="finite"),
        ):
            stop_conductor(wait=budget)

        mock_kill.assert_not_called()
        mock_probe.assert_not_called()
        mock_wait.assert_not_called()
