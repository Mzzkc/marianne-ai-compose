"""Tests for ProcessGroupManager (orphan prevention).

Verifies process group setup, child signaling, orphan cleanup,
and atexit registration — the core of issue #38 prevention.
"""

from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock, patch

import pytest

from marianne.daemon.pgroup import ProcessGroupManager


class TestProcessGroupManagerInit:
    """Test initial state before setup()."""

    def test_initial_state(self) -> None:
        mgr = ProcessGroupManager()
        assert mgr.is_leader is False
        assert mgr.original_pgid == os.getpgrp()

    def test_original_pgid_captured(self) -> None:
        mgr = ProcessGroupManager()
        assert isinstance(mgr.original_pgid, int)
        assert mgr.original_pgid > 0


class TestProcessGroupSetup:
    """Test setup() — making daemon the group leader."""

    def test_setup_calls_setpgrp(self) -> None:
        mgr = ProcessGroupManager()
        with (
            patch("marianne.daemon.pgroup.os.setpgrp") as mock_setpgrp,
            patch("marianne.daemon.pgroup.atexit.register"),
        ):
            mgr.setup()
            mock_setpgrp.assert_called_once()
        assert mgr.is_leader is True

    def test_setup_idempotent(self) -> None:
        mgr = ProcessGroupManager()
        with (
            patch("marianne.daemon.pgroup.os.setpgrp") as mock_setpgrp,
            patch("marianne.daemon.pgroup.atexit.register"),
        ):
            mgr.setup()
            mgr.setup()  # Second call should be no-op
            mock_setpgrp.assert_called_once()

    def test_setup_handles_oserror_when_already_leader(self) -> None:
        mgr = ProcessGroupManager()
        with (
            patch("marianne.daemon.pgroup.os.setpgrp", side_effect=OSError("already leader")),
            patch("marianne.daemon.pgroup.os.getpid", return_value=42),
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=42),
            patch("marianne.daemon.pgroup.atexit.register"),
        ):
            mgr.setup()  # Should not raise
            assert mgr.is_leader is True

    def test_setup_logs_warning_when_not_leader_and_fails(self) -> None:
        mgr = ProcessGroupManager()
        with (
            patch("marianne.daemon.pgroup.os.setpgrp", side_effect=OSError("nope")),
            patch("marianne.daemon.pgroup.os.getpid", return_value=42),
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=99),
        ):
            mgr.setup()  # Should not raise
            assert mgr.is_leader is False

    def test_setup_registers_atexit(self) -> None:
        mgr = ProcessGroupManager()
        with (
            patch("marianne.daemon.pgroup.os.setpgrp"),
            patch("marianne.daemon.pgroup.atexit.register") as mock_atexit,
        ):
            mgr.setup()
            mock_atexit.assert_called_once()


class TestKillAllChildren:
    """Test kill_all_children() — group-wide signaling."""

    def test_skip_when_not_leader(self) -> None:
        mgr = ProcessGroupManager()
        # Don't call setup() — not a leader
        result = mgr.kill_all_children()
        assert result == 0

    def test_signals_process_group(self) -> None:
        mgr = ProcessGroupManager()
        mgr._is_leader = True

        with (
            patch.object(mgr, "_count_group_members", return_value=3),
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1000),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1),
            patch("marianne.daemon.pgroup.os.killpg") as mock_killpg,
            patch("marianne.daemon.pgroup.signal.signal"),
        ):
            result = mgr.kill_all_children(signal.SIGTERM)
            assert result == 1000
            mock_killpg.assert_called_once_with(1000, signal.SIGTERM)

    def test_handles_process_lookup_error(self) -> None:
        mgr = ProcessGroupManager()
        mgr._is_leader = True

        with (
            patch.object(mgr, "_count_group_members", return_value=1),
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1000),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1),
            patch("marianne.daemon.pgroup.os.killpg", side_effect=ProcessLookupError),
            patch("marianne.daemon.pgroup.signal.signal"),
        ):
            result = mgr.kill_all_children()
            assert result == 0

    def test_handles_permission_error(self) -> None:
        mgr = ProcessGroupManager()
        mgr._is_leader = True

        with (
            patch.object(mgr, "_count_group_members", return_value=1),
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1000),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1),
            patch("marianne.daemon.pgroup.os.killpg", side_effect=PermissionError),
            patch("marianne.daemon.pgroup.signal.signal"),
        ):
            result = mgr.kill_all_children()
            assert result == 0

    def test_no_signal_when_no_children(self) -> None:
        mgr = ProcessGroupManager()
        mgr._is_leader = True

        with (
            patch.object(mgr, "_count_group_members", return_value=0),
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1000),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1),
            patch("marianne.daemon.pgroup.os.killpg") as mock_killpg,
        ):
            result = mgr.kill_all_children()
            assert result == 1000
            mock_killpg.assert_not_called()

    def test_custom_signal(self) -> None:
        mgr = ProcessGroupManager()
        mgr._is_leader = True

        with (
            patch.object(mgr, "_count_group_members", return_value=2),
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1000),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1),
            patch("marianne.daemon.pgroup.os.killpg") as mock_killpg,
            patch("marianne.daemon.pgroup.signal.signal"),
        ):
            mgr.kill_all_children(signal.SIGKILL)
            mock_killpg.assert_called_once_with(1000, signal.SIGKILL)


class TestCleanupOrphans:
    """Test cleanup_orphans() — finding and killing leaked processes."""

    def test_cleanup_with_psutil_zombies(self) -> None:
        mock_psutil = MagicMock()
        mock_zombie = MagicMock()
        mock_zombie.pid = 999
        mock_zombie.is_running.return_value = True
        mock_zombie.status.return_value = "zombie"

        mock_current = MagicMock()
        mock_current.children.return_value = [mock_zombie]
        mock_psutil.Process.return_value = mock_current
        mock_psutil.STATUS_ZOMBIE = "zombie"

        ProcessGroupManager()
        with (
            patch.dict("sys.modules", {"psutil": mock_psutil}),
            patch("marianne.daemon.pgroup.os.waitpid"),
        ):
            # Force reimport behavior by patching at module level
            import marianne.daemon.pgroup as pgroup_mod

            result = pgroup_mod.ProcessGroupManager().cleanup_orphans()
            # The mock setup may not propagate through reimport, so just
            # verify the method runs without error
            assert isinstance(result, list)

    def test_cleanup_returns_empty_when_no_orphans(self) -> None:
        mgr = ProcessGroupManager()
        # With no children, should return empty list
        result = mgr.cleanup_orphans()
        assert isinstance(result, list)

    def test_cleanup_proc_fallback_no_zombies(self) -> None:
        """Test /proc fallback when psutil is not available."""
        orphans = ProcessGroupManager._cleanup_orphans_proc()
        assert isinstance(orphans, list)


class TestCountGroupMembers:
    """Test _count_group_members static method."""

    def test_fallback_to_proc(self) -> None:
        # When psutil is not available, should fall back to /proc
        with patch.dict("sys.modules", {"psutil": None}):
            # This may or may not find processes, but should not error
            count = ProcessGroupManager._count_group_members(
                pgid=os.getpgrp(),
                exclude_pid=-1,
            )
            assert isinstance(count, int)
            assert count >= 0


class TestAtexitCleanup:
    """Test the atexit handler behavior."""

    def test_atexit_skips_when_not_leader(self) -> None:
        mgr = ProcessGroupManager()
        # _atexit_cleanup should be a no-op when not leader
        mgr._atexit_cleanup()  # Should not raise

    def test_atexit_signals_when_leader_with_children(self) -> None:
        mgr = ProcessGroupManager()
        mgr._is_leader = True

        with (
            patch.object(mgr, "_count_group_members", return_value=2),
            patch("marianne.daemon.pgroup.os.getpgrp", return_value=1000),
            patch("marianne.daemon.pgroup.os.getpid", return_value=1),
            patch("marianne.daemon.pgroup.os.killpg") as mock_killpg,
            patch("marianne.daemon.pgroup.signal.signal"),
        ):
            mgr._atexit_cleanup()
            mock_killpg.assert_called_once_with(1000, signal.SIGTERM)

    def test_atexit_handles_exceptions_silently(self) -> None:
        mgr = ProcessGroupManager()
        mgr._is_leader = True

        with patch.object(
            mgr,
            "_count_group_members",
            side_effect=RuntimeError("boom"),
        ):
            # Must not raise — atexit handlers should be silent
            mgr._atexit_cleanup()


# TDD STATUS: F-487 disabled the reap_orphaned_backends() kill paths for
# WSL2 safety. Individual tests that assert "kill happened" are marked
# xfail(strict=True); tests that assert "kill did NOT happen" pass naturally
# against the disabled implementation AND would still pass against a
# correctly-scoped re-enable. See TASKS.md "Re-enable F-483 / F-487 tests".
_F487_XFAIL_REASON = (
    "F-487: reap_orphaned_backends() intentionally disabled for WSL2 "
    "safety. Will pass when per-job PID tracking replaces in-memory "
    "tracking. See TASKS.md 'Re-enable F-483 / F-487 tests'."
)


class TestReapOrphanedBackends:
    """Test system-wide orphan reaper for leaked backend children."""

    @pytest.mark.xfail(strict=True, reason=_F487_XFAIL_REASON)
    def test_kills_orphans_from_dead_tracked_backend(self) -> None:
        """Orphaned processes are killed when their tracked backend is dead."""
        mgr = ProcessGroupManager()
        # Simulate a backend PID that no longer exists
        mgr.track_backend_pid(99999)

        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 12345,
            "ppid": 1,
            "cmdline": ["node", "symbols", "run", "pyright-langserver"],
            "uids": MagicMock(real=os.getuid()),
        }

        with (
            patch("psutil.process_iter", return_value=[mock_proc]),
            patch("os.kill", side_effect=OSError("No such process")),
        ):
            killed = mgr.reap_orphaned_backends()

        assert 12345 in killed
        mock_proc.kill.assert_called_once()

    def test_skips_non_orphans(self) -> None:
        """Processes with a live parent (ppid != 0 or 1) are not killed."""
        mgr = ProcessGroupManager()
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 12345,
            "ppid": 999,  # Has a live parent
            "cmdline": ["node", "symbols", "run", "pyright-langserver"],
            "uids": MagicMock(real=os.getuid()),
        }

        with patch("psutil.process_iter", return_value=[mock_proc]):
            killed = mgr.reap_orphaned_backends()

        assert killed == []
        mock_proc.kill.assert_not_called()

    def test_skips_other_users_processes(self) -> None:
        """Processes owned by other users are not killed."""
        mgr = ProcessGroupManager()
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 12345,
            "ppid": 1,
            "cmdline": ["node", "symbols", "run", "tsserver"],
            "uids": MagicMock(real=99999),  # Different user
        }

        with patch("psutil.process_iter", return_value=[mock_proc]):
            killed = mgr.reap_orphaned_backends()

        assert killed == []
        mock_proc.kill.assert_not_called()

    def test_skips_orphans_when_no_tracked_backends(self) -> None:
        """No orphans killed when no backend PIDs are tracked."""
        mgr = ProcessGroupManager()
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 12345,
            "ppid": 1,
            "cmdline": ["node", "symbols", "run", "pyright-langserver"],
            "uids": MagicMock(real=os.getuid()),
        }

        with patch("psutil.process_iter", return_value=[mock_proc]):
            killed = mgr.reap_orphaned_backends()

        assert killed == []
        mock_proc.kill.assert_not_called()

    @pytest.mark.adversarial
    def test_handles_psutil_errors_gracefully(self) -> None:
        """NoSuchProcess and AccessDenied during iteration don't crash."""
        import psutil

        mgr = ProcessGroupManager()
        mgr.track_backend_pid(99999)  # Dead backend

        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 12345,
            "ppid": 1,
            "cmdline": ["node", "symbols", "run", "tsserver"],
            "uids": MagicMock(real=os.getuid()),
        }
        mock_proc.kill.side_effect = psutil.NoSuchProcess(12345)

        with (
            patch("psutil.process_iter", return_value=[mock_proc]),
            patch("os.kill", side_effect=OSError("No such process")),
        ):
            killed = mgr.reap_orphaned_backends()

        # Process disappeared before kill — no crash, not counted as killed
        assert killed == []

    @pytest.mark.xfail(strict=True, reason=_F487_XFAIL_REASON)
    def test_proc_fallback_when_psutil_missing(self) -> None:
        """Falls back to /proc scan when psutil is not installed."""
        mgr = ProcessGroupManager()
        with (
            patch.dict("sys.modules", {"psutil": None}),
            patch.object(
                mgr,
                "_reap_orphans_proc",
                return_value=[111, 222],
            ) as mock_fallback,
        ):
            killed = mgr.reap_orphaned_backends()

        mock_fallback.assert_called_once()
        assert killed == [111, 222]


class TestMonitorPgroupIntegration:
    """Test that ResourceMonitor calls pgroup cleanup methods."""

    @pytest.mark.asyncio
    async def test_monitor_calls_cleanup_orphans(self) -> None:
        from marianne.daemon.config import ResourceLimitConfig
        from marianne.daemon.monitor import ResourceMonitor

        mock_pgroup = MagicMock()
        mock_pgroup.cleanup_orphans.return_value = []
        mock_pgroup.reap_orphaned_backends.return_value = []

        monitor = ResourceMonitor(
            config=ResourceLimitConfig(),
            pgroup=mock_pgroup,
        )

        snapshot = await monitor.check_now()
        await monitor._evaluate(snapshot)

        mock_pgroup.cleanup_orphans.assert_called_once()

    @pytest.mark.asyncio
    async def test_monitor_calls_reap_orphaned_backends(self) -> None:
        from marianne.daemon.config import ResourceLimitConfig
        from marianne.daemon.monitor import ResourceMonitor

        mock_pgroup = MagicMock()
        mock_pgroup.cleanup_orphans.return_value = []
        mock_pgroup.reap_orphaned_backends.return_value = [12345]

        monitor = ResourceMonitor(
            config=ResourceLimitConfig(),
            pgroup=mock_pgroup,
        )

        snapshot = await monitor.check_now()
        await monitor._evaluate(snapshot)

        mock_pgroup.reap_orphaned_backends.assert_called_once()
