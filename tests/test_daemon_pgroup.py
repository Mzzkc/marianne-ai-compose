"""Tests for marianne.daemon.pgroup module.

Covers ProcessGroupManager setup(), cleanup_orphans(), and
kill_all_children() signal propagation, including verification
that process-counting delegates to SystemProbe.
"""

from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock, patch

import pytest

from marianne.daemon.pgroup import ProcessGroupManager
from marianne.daemon.system_probe import SystemProbe


# ─── Setup ─────────────────────────────────────────────────────────────


class TestSetup:
    """Tests for ProcessGroupManager.setup()."""

    def test_setup_calls_setpgrp(self):
        """setup() calls os.setpgrp() to become group leader."""
        pgm = ProcessGroupManager()

        with (
            patch("marianne.daemon.pgroup.os.setpgrp") as mock_setpgrp,
            patch("marianne.daemon.pgroup.atexit.register"),
        ):
            pgm.setup()

        mock_setpgrp.assert_called_once()
        assert pgm.is_leader is True

    def test_setup_idempotent(self):
        """Calling setup() twice doesn't call setpgrp again."""
        pgm = ProcessGroupManager()

        with (
            patch("marianne.daemon.pgroup.os.setpgrp") as mock_setpgrp,
            patch("marianne.daemon.pgroup.atexit.register"),
        ):
            pgm.setup()
            pgm.setup()

        mock_setpgrp.assert_called_once()

    def test_setup_registers_atexit(self):
        """setup() registers an atexit handler for last-resort cleanup."""
        pgm = ProcessGroupManager()

        with (
            patch("marianne.daemon.pgroup.os.setpgrp"),
            patch("marianne.daemon.pgroup.atexit.register") as mock_atexit,
        ):
            pgm.setup()

        mock_atexit.assert_called_once_with(pgm._atexit_cleanup)

    def test_setup_handles_oserror_when_already_leader(self):
        """setup() handles OSError when process is already group leader."""
        pgm = ProcessGroupManager()

        with (
            patch("marianne.daemon.pgroup.os.setpgrp", side_effect=OSError("EPERM")),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.atexit.register"),
        ):
            pgm.setup()

        assert pgm.is_leader is True

    def test_setup_handles_oserror_when_not_leader(self):
        """setup() logs warning when OSError and not already leader."""
        pgm = ProcessGroupManager()

        with (
            patch("marianne.daemon.pgroup.os.setpgrp", side_effect=OSError("EPERM")),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=5678),
        ):
            pgm.setup()

        assert pgm.is_leader is False


# ─── kill_all_children ─────────────────────────────────────────────────


class TestKillAllChildren:
    """Tests for kill_all_children() signal propagation."""

    def test_skip_when_not_leader(self):
        """kill_all_children returns 0 if not the process group leader."""
        pgm = ProcessGroupManager()
        assert pgm.is_leader is False

        result = pgm.kill_all_children()
        assert result == 0

    def test_signals_process_group(self):
        """kill_all_children sends SIGTERM to the process group."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch.object(
                ProcessGroupManager, "_count_group_members", return_value=3,
            ),
            patch("marianne.daemon.pgroup.os.killpg") as mock_killpg,
            patch("marianne.daemon.pgroup.signal.signal") as mock_signal,
        ):
            result = pgm.kill_all_children()

        assert result == 1234
        mock_killpg.assert_called_once_with(1234, signal.SIGTERM)

    def test_custom_signal(self):
        """kill_all_children sends the specified signal."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch.object(
                ProcessGroupManager, "_count_group_members", return_value=1,
            ),
            patch("marianne.daemon.pgroup.os.killpg") as mock_killpg,
            patch("marianne.daemon.pgroup.signal.signal"),
        ):
            result = pgm.kill_all_children(sig=signal.SIGKILL)

        mock_killpg.assert_called_once_with(1234, signal.SIGKILL)
        assert result == 1234

    def test_no_children_returns_pgid(self):
        """kill_all_children returns pgid even when no children to signal."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch.object(
                ProcessGroupManager, "_count_group_members", return_value=0,
            ),
        ):
            result = pgm.kill_all_children()

        # Returns pgid even with no children
        assert result == 1234

    def test_handles_process_lookup_error(self):
        """kill_all_children handles ProcessLookupError gracefully."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch.object(
                ProcessGroupManager, "_count_group_members", return_value=1,
            ),
            patch("marianne.daemon.pgroup.os.killpg", side_effect=ProcessLookupError),
            patch("marianne.daemon.pgroup.signal.signal"),
        ):
            result = pgm.kill_all_children()

        assert result == 0

    def test_handles_permission_error(self):
        """kill_all_children handles PermissionError gracefully."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch.object(
                ProcessGroupManager, "_count_group_members", return_value=1,
            ),
            patch("marianne.daemon.pgroup.os.killpg", side_effect=PermissionError),
            patch("marianne.daemon.pgroup.signal.signal"),
        ):
            result = pgm.kill_all_children()

        assert result == 0

    def test_temporarily_ignores_signal_for_self(self):
        """kill_all_children sets SIG_IGN before killpg, restores after."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        signal_calls = []

        def track_signal(sig, handler):
            signal_calls.append((sig, handler))
            return signal.SIG_DFL  # Return previous handler

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch.object(
                ProcessGroupManager, "_count_group_members", return_value=1,
            ),
            patch("marianne.daemon.pgroup.os.killpg"),
            patch("marianne.daemon.pgroup.signal.signal", side_effect=track_signal),
        ):
            pgm.kill_all_children(sig=signal.SIGTERM)

        # First call should set SIG_IGN, second should restore
        assert len(signal_calls) == 2
        assert signal_calls[0] == (signal.SIGTERM, signal.SIG_IGN)
        # Second call restores the previous handler (SIG_DFL from our mock)
        assert signal_calls[1][0] == signal.SIGTERM


# ─── cleanup_orphans ───────────────────────────────────────────────────


class TestCleanupOrphans:
    """Tests for cleanup_orphans() with mock psutil."""

    def test_cleanup_with_no_children(self):
        """cleanup_orphans returns empty list when no children."""
        pgm = ProcessGroupManager()

        mock_psutil = MagicMock()
        mock_process = MagicMock()
        mock_process.children.return_value = []
        mock_psutil.Process.return_value = mock_process

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = pgm.cleanup_orphans()

        assert result == []

    def test_cleanup_reaps_zombies(self):
        """cleanup_orphans reaps zombie child processes."""
        pgm = ProcessGroupManager()

        mock_psutil = MagicMock()
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        zombie_child = MagicMock()
        zombie_child.pid = 999
        zombie_child.is_running.return_value = True
        zombie_child.status.return_value = "zombie"

        mock_process = MagicMock()
        mock_process.children.return_value = [zombie_child]
        mock_psutil.Process.return_value = mock_process

        with (
            patch.dict("sys.modules", {"psutil": mock_psutil}),
            patch("marianne.daemon.pgroup.os.waitpid") as mock_waitpid,
            patch("marianne.daemon.pgroup.os.getpid", return_value=1000),
        ):
            result = pgm.cleanup_orphans()

        assert 999 in result
        mock_waitpid.assert_called_once_with(999, os.WNOHANG)

    def test_cleanup_does_not_kill_sleeping_orphans_of_dead_tracked_backend(self):
        """cleanup_orphans does NOT kill sleeping orphans — ancestry-based killing disabled (F-483).

        The F-483 fix disabled ancestry-based orphan killing because it was too
        aggressive — killing Chrome, pytest, pyright, and other legitimate processes
        in the daemon's tree. Only zombie reaping remains active. Orphaned MCP
        servers accumulate but don't crash the system. Per-job PID tracking in
        the conductor DB will replace this.
        """
        pgm = ProcessGroupManager()
        pgm.track_backend_pid(99999)  # Will appear dead via os.kill probe

        mock_psutil = MagicMock()
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        orphan = MagicMock()
        orphan.pid = 888
        orphan.is_running.return_value = True
        orphan.status.return_value = "sleeping"
        orphan.cmdline.return_value = ["node", "mcp-server", "--port=9090"]
        orphan.ppid.return_value = 1  # Reparented to init

        mock_process = MagicMock()
        mock_process.children.return_value = [orphan]
        mock_psutil.Process.return_value = mock_process

        with (
            patch.dict("sys.modules", {"psutil": mock_psutil}),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1000),
            patch("marianne.daemon.pgroup.os.kill", side_effect=OSError),
        ):
            result = pgm.cleanup_orphans()

        # Ancestry-based killing disabled — sleeping orphan is NOT cleaned up
        assert 888 not in result
        orphan.kill.assert_not_called()

    def test_cleanup_ignores_normal_children(self):
        """cleanup_orphans leaves healthy children alone."""
        pgm = ProcessGroupManager()

        mock_psutil = MagicMock()
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        healthy = MagicMock()
        healthy.pid = 777
        healthy.is_running.return_value = True
        healthy.status.return_value = "sleeping"
        healthy.cmdline.return_value = ["python", "-m", "marianne"]
        healthy.ppid.return_value = 1000  # Not reparented

        mock_process = MagicMock()
        mock_process.children.return_value = [healthy]
        mock_psutil.Process.return_value = mock_process

        with (
            patch.dict("sys.modules", {"psutil": mock_psutil}),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1000),
        ):
            result = pgm.cleanup_orphans()

        assert result == []
        healthy.terminate.assert_not_called()

    def test_cleanup_fallback_when_psutil_missing(self):
        """cleanup_orphans falls back to /proc when psutil unavailable."""
        pgm = ProcessGroupManager()

        with (
            patch.dict("sys.modules", {"psutil": None}),
            patch.object(
                ProcessGroupManager, "_cleanup_orphans_proc", return_value=[42],
            ) as mock_fallback,
        ):
            result = pgm.cleanup_orphans()

        assert result == [42]
        mock_fallback.assert_called_once()

    def test_cleanup_handles_nosuchprocess(self):
        """cleanup_orphans handles NoSuchProcess for vanished processes."""
        pgm = ProcessGroupManager()

        mock_psutil = MagicMock()
        mock_psutil.STATUS_ZOMBIE = "zombie"
        nosuch_exc = type("NoSuchProcess", (Exception,), {})
        mock_psutil.NoSuchProcess = nosuch_exc
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        vanished = MagicMock()
        vanished.pid = 666
        vanished.is_running.return_value = True
        vanished.status.side_effect = nosuch_exc()

        mock_process = MagicMock()
        mock_process.children.return_value = [vanished]
        mock_psutil.Process.return_value = mock_process

        with (
            patch.dict("sys.modules", {"psutil": mock_psutil}),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1000),
        ):
            result = pgm.cleanup_orphans()

        # Should not crash, vanished process is skipped
        assert result == []


# ─── _count_group_members ──────────────────────────────────────────────


class TestCountGroupMembers:
    """Tests for _count_group_members static method."""

    def test_count_with_psutil(self):
        """Counts group members using psutil when available."""
        mock_psutil = MagicMock()

        proc1 = MagicMock()
        proc1.info = {"pid": 100}
        proc2 = MagicMock()
        proc2.info = {"pid": 200}
        proc3 = MagicMock()
        proc3.info = {"pid": 300}

        mock_psutil.process_iter.return_value = [proc1, proc2, proc3]
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        def fake_getpgid(pid: int) -> int:
            return {100: 1234, 200: 1234, 300: 9999}[pid]

        with (
            patch("marianne.daemon.system_probe._psutil", mock_psutil),
            patch("marianne.daemon.system_probe.os.getpgid", side_effect=fake_getpgid),
        ):
            count = ProcessGroupManager._count_group_members(
                pgid=1234, exclude_pid=100,
            )

        # Only proc2 matches (same pgid, not excluded)
        assert count == 1


# ─── _atexit_cleanup ───────────────────────────────────────────────────


class TestAtexitCleanup:
    """Tests for _atexit_cleanup last-resort handler."""

    def test_atexit_noop_when_not_leader(self):
        """_atexit_cleanup is a no-op when not process group leader."""
        pgm = ProcessGroupManager()
        assert pgm.is_leader is False

        # Should not raise or do anything
        pgm._atexit_cleanup()

    def test_atexit_signals_children(self):
        """_atexit_cleanup sends SIGTERM to children when leader."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch.object(
                ProcessGroupManager, "_count_group_members", return_value=2,
            ),
            patch("marianne.daemon.pgroup.os.killpg") as mock_killpg,
            patch("marianne.daemon.pgroup.signal.signal"),
        ):
            pgm._atexit_cleanup()

        mock_killpg.assert_called_once_with(1234, signal.SIGTERM)

    def test_atexit_noop_with_no_children(self):
        """_atexit_cleanup doesn't call killpg when no children exist."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch.object(
                ProcessGroupManager, "_count_group_members", return_value=0,
            ),
            patch("marianne.daemon.pgroup.os.killpg") as mock_killpg,
        ):
            pgm._atexit_cleanup()

        mock_killpg.assert_not_called()

    def test_atexit_swallows_exceptions(self):
        """_atexit_cleanup never raises (critical for atexit handlers)."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", side_effect=RuntimeError("boom")),
        ):
            # Should not raise
            pgm._atexit_cleanup()


# ─── SystemProbe delegation ───────────────────────────────────────────


class TestSystemProbeDelegation:
    """Verify ProcessGroupManager delegates process counting to SystemProbe."""

    def test_count_group_members_delegates_to_system_probe(self):
        """_count_group_members calls SystemProbe.count_group_members."""
        with patch.object(
            SystemProbe, "count_group_members", return_value=5,
        ) as mock_probe:
            result = ProcessGroupManager._count_group_members(
                pgid=1234, exclude_pid=100,
            )

        mock_probe.assert_called_once_with(1234, exclude_pid=100)
        assert result == 5

    def test_count_group_members_returns_zero_on_none(self):
        """_count_group_members converts None (probe failure) to 0 (fail-safe)."""
        with patch.object(
            SystemProbe, "count_group_members", return_value=None,
        ):
            result = ProcessGroupManager._count_group_members(
                pgid=1234, exclude_pid=100,
            )

        assert result == 0

    def test_kill_all_children_uses_system_probe_count(self):
        """kill_all_children uses SystemProbe-derived count for its decision."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch.object(
                SystemProbe, "count_group_members", return_value=2,
            ) as mock_probe,
            patch("marianne.daemon.pgroup.os.killpg") as mock_killpg,
            patch("marianne.daemon.pgroup.signal.signal"),
        ):
            result = pgm.kill_all_children()

        mock_probe.assert_called_once_with(1234, exclude_pid=1234)
        mock_killpg.assert_called_once_with(1234, signal.SIGTERM)
        assert result == 1234

    def test_kill_all_children_skips_signal_when_probe_returns_zero(self):
        """kill_all_children skips killpg when SystemProbe returns 0 children."""
        pgm = ProcessGroupManager()
        pgm._is_leader = True

        with (
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1234),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1234),
            patch.object(
                SystemProbe, "count_group_members", return_value=0,
            ),
            patch("marianne.daemon.pgroup.os.killpg") as mock_killpg,
        ):
            result = pgm.kill_all_children()

        mock_killpg.assert_not_called()
        assert result == 1234
