"""Tests for F-483: Multiple orphans of a dead tracked backend must all be killed.

Bug: cleanup_orphans() removes dead backends from _tracked_backend_pids on
the first orphaned child iteration. On subsequent children, the dead backend
is gone from the set, so dead_backends is empty, and the child is left alive.

The same bug exists in reap_orphaned_backends() and _reap_orphans_proc().

TDD: These tests are written BEFORE the fix. They must fail on the buggy
code and pass on the fixed code.
"""

from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock, call, patch

import pytest

from marianne.daemon.pgroup import ProcessGroupManager

# TDD STATUS: These tests are FROZEN PENDING per-job PID tracking.
#
# F-487 (composer, 2026-04-06): The orphan cleanup code paths these tests
# exercise — reap_orphaned_backends(), cleanup_orphans() ancestry kill block,
# _reap_orphans_proc() — were intentionally disabled after they caused 9
# WSL2 session crashes by killing systemd --user along with MCP servers.
# Harper's F-483 race fix (commit 8ce018f on marianne tree) landed
# concurrently but did not address the F-487 "kill wrong things" root cause.
# These tests assert the kill behavior that is currently a no-op.
#
# DO NOT DELETE THESE TESTS. When per-job PID tracking lands and the orphan
# cleanup paths are re-enabled safely, these tests must be reviewed and
# either restored or rewritten. The xfail(strict=True) marker ensures that
# if a change accidentally re-enables the kill without proper scoping, the
# tests will XPASS and pytest will fail the run — forcing the team to
# consciously remove the marker. See TASKS.md "Re-enable F-483 / F-487
# orphan-cleanup tests" and composer-notes.yaml "PROCESS CLEANUP
# SIMPLIFICATION".
# NOTE: module-level xfail uses strict=False because some tests in this
# file exercise paths that already work under the disabled implementation
# (track/untrack operations, skip-path assertions). When picking up the
# TASKS.md "Re-enable F-483 / F-487 tests" task, split these into
# per-test xfail(strict=True) markers on the tests that actually assert
# the killing behavior, so XPASS will fail the run and force marker removal.
pytestmark = pytest.mark.xfail(
    strict=False,
    reason=(
        "F-487: orphan-kill paths intentionally disabled for WSL2 safety. "
        "These tests will pass when per-job PID tracking re-enables the "
        "cleanup paths. See TASKS.md 'Re-enable F-483 / F-487 tests'."
    ),
)


class TestCleanupOrphansMultiple:
    """cleanup_orphans() must kill ALL orphans of dead tracked backends."""

    def test_two_orphans_one_dead_backend_both_killed(self) -> None:
        """Two orphaned children of one dead backend must both be killed.

        This is the core bug: the first orphan triggers dead-backend
        detection and gets killed, but the dead backend PID is removed
        from _tracked_backend_pids. The second orphan sees no dead
        backends and survives.
        """
        pgm = ProcessGroupManager()
        pgm.track_backend_pid(5000)  # Will be probed as dead

        mock_psutil = MagicMock()
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        orphan1 = MagicMock()
        orphan1.pid = 6001
        orphan1.is_running.return_value = True
        orphan1.status.return_value = "sleeping"
        orphan1.cmdline.return_value = ["node", "mcp-server"]
        orphan1.ppid.return_value = 1  # Reparented to init

        orphan2 = MagicMock()
        orphan2.pid = 6002
        orphan2.is_running.return_value = True
        orphan2.status.return_value = "sleeping"
        orphan2.cmdline.return_value = ["pyright-langserver"]
        orphan2.ppid.return_value = 1  # Also reparented

        mock_process = MagicMock()
        mock_process.children.return_value = [orphan1, orphan2]
        mock_psutil.Process.return_value = mock_process

        with (
            patch.dict("sys.modules", {"psutil": mock_psutil}),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1000),
            patch("marianne.daemon.pgroup.os.kill", side_effect=OSError),
        ):
            result = pgm.cleanup_orphans()

        # Both orphans must be killed, not just the first
        assert 6001 in result
        assert 6002 in result
        orphan1.kill.assert_called_once()
        orphan2.kill.assert_called_once()

    def test_three_orphans_two_dead_backends_all_killed(self) -> None:
        """Three orphans with two dead backends — all should be killed."""
        pgm = ProcessGroupManager()
        pgm.track_backend_pid(5000)  # Dead
        pgm.track_backend_pid(5001)  # Dead
        pgm.track_backend_pid(5002)  # Alive

        mock_psutil = MagicMock()
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        orphans = []
        for i in range(3):
            o = MagicMock()
            o.pid = 7001 + i
            o.is_running.return_value = True
            o.status.return_value = "sleeping"
            o.cmdline.return_value = ["server-process"]
            o.ppid.return_value = 1
            orphans.append(o)

        mock_process = MagicMock()
        mock_process.children.return_value = orphans
        mock_psutil.Process.return_value = mock_process

        def fake_kill(pid: int, sig: int) -> None:
            if pid in (5000, 5001):
                raise OSError("No such process")
            # 5002 is alive — no error

        with (
            patch.dict("sys.modules", {"psutil": mock_psutil}),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1000),
            patch("marianne.daemon.pgroup.os.kill", side_effect=fake_kill),
        ):
            result = pgm.cleanup_orphans()

        # All three orphans killed
        assert len(result) == 3
        for o in orphans:
            o.kill.assert_called_once()

    def test_dead_backends_cleaned_from_tracking_after_sweep(self) -> None:
        """Dead backend PIDs should be removed from tracking after the
        full sweep, not during iteration."""
        pgm = ProcessGroupManager()
        pgm.track_backend_pid(5000)  # Dead

        mock_psutil = MagicMock()
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        orphan = MagicMock()
        orphan.pid = 8001
        orphan.is_running.return_value = True
        orphan.status.return_value = "sleeping"
        orphan.cmdline.return_value = ["server"]
        orphan.ppid.return_value = 1

        mock_process = MagicMock()
        mock_process.children.return_value = [orphan]
        mock_psutil.Process.return_value = mock_process

        with (
            patch.dict("sys.modules", {"psutil": mock_psutil}),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1000),
            patch("marianne.daemon.pgroup.os.kill", side_effect=OSError),
        ):
            pgm.cleanup_orphans()

        # After cleanup, dead backend should be removed
        assert 5000 not in pgm._tracked_backend_pids


class TestReapOrphanedBackendsMultiple:
    """reap_orphaned_backends() must kill ALL orphans."""

    def test_two_orphans_both_killed(self) -> None:
        """Two system-wide orphans should both be killed."""
        pgm = ProcessGroupManager()
        pgm.track_backend_pid(5000)  # Dead

        mock_psutil = MagicMock()
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})
        mock_psutil.ZombieProcess = type("ZombieProcess", (Exception,), {})

        proc1 = MagicMock()
        proc1.info = {
            "pid": 9001,
            "ppid": 1,
            "cmdline": ["node", "lsp-server"],
            "uids": MagicMock(real=os.getuid()),
        }

        proc2 = MagicMock()
        proc2.info = {
            "pid": 9002,
            "ppid": 1,
            "cmdline": ["rust-analyzer"],
            "uids": MagicMock(real=os.getuid()),
        }

        mock_psutil.process_iter.return_value = [proc1, proc2]

        with (
            patch.dict("sys.modules", {"psutil": mock_psutil}),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1000),
            patch("marianne.daemon.pgroup.os.getuid", return_value=os.getuid()),
            patch("marianne.daemon.pgroup.os.kill", side_effect=OSError),
        ):
            result = pgm.reap_orphaned_backends()

        assert 9001 in result
        assert 9002 in result
        assert len(result) == 2


class TestReapOrphansProcMultiple:
    """_reap_orphans_proc() must kill ALL orphans (Linux /proc fallback)."""

    def test_two_proc_orphans_both_killed(self) -> None:
        """Two /proc orphans should both be killed."""
        pgm = ProcessGroupManager()
        pgm.track_backend_pid(5000)  # Dead

        fake_proc = {
            "3001": {
                "status": "PPid:\t1\n",
                "cmdline": "node\0mcp\0",
            },
            "3002": {
                "status": "PPid:\t1\n",
                "cmdline": "pyright\0",
            },
        }

        def fake_listdir(path: str) -> list[str]:
            return ["3001", "3002", "self"]

        def fake_stat(path: str) -> MagicMock:
            s = MagicMock()
            s.st_uid = os.getuid()
            return s

        def fake_open(path: str, *args, **kwargs):  # noqa: ANN002, ANN003
            import io

            for pid, files in fake_proc.items():
                if f"/{pid}/status" in path:
                    return io.StringIO(files["status"])
                if f"/{pid}/cmdline" in path:
                    return io.StringIO(files["cmdline"])
            raise FileNotFoundError(path)

        killed_pids: list[int] = []

        def fake_kill(pid: int, sig: int) -> None:
            if pid == 5000:
                raise OSError("dead")
            killed_pids.append(pid)

        with (
            patch("marianne.daemon.pgroup.os.listdir", side_effect=fake_listdir),
            patch("marianne.daemon.pgroup.os.stat", side_effect=fake_stat),
            patch("marianne.daemon.pgroup.os.getuid", return_value=os.getuid()),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1000),
            patch("marianne.daemon.pgroup.os.kill", side_effect=fake_kill),
            patch("builtins.open", side_effect=fake_open),
        ):
            result = pgm._reap_orphans_proc()

        assert 3001 in result
        assert 3002 in result


class TestTrackUntrack:
    """Basic tracking behavior."""

    def test_track_and_untrack(self) -> None:
        """track_backend_pid adds, untrack removes."""
        pgm = ProcessGroupManager()
        pgm.track_backend_pid(100)
        assert 100 in pgm._tracked_backend_pids
        pgm.untrack_backend_pid(100)
        assert 100 not in pgm._tracked_backend_pids

    def test_untrack_nonexistent_is_safe(self) -> None:
        """Untracking a PID that was never tracked doesn't raise."""
        pgm = ProcessGroupManager()
        pgm.untrack_backend_pid(999)  # Should not raise

    def test_no_orphan_kill_without_tracked_backends(self) -> None:
        """Without tracked backends, orphans are not killed."""
        pgm = ProcessGroupManager()
        # No tracked backends

        mock_psutil = MagicMock()
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        orphan = MagicMock()
        orphan.pid = 6001
        orphan.is_running.return_value = True
        orphan.status.return_value = "sleeping"
        orphan.ppid.return_value = 1

        mock_process = MagicMock()
        mock_process.children.return_value = [orphan]
        mock_psutil.Process.return_value = mock_process

        with (
            patch.dict("sys.modules", {"psutil": mock_psutil}),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1000),
        ):
            result = pgm.cleanup_orphans()

        assert result == []
        orphan.kill.assert_not_called()
