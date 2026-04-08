"""Tests for marianne.daemon.system_probe module.

Covers the consolidated SystemProbe class — memory probes, child process
counting, zombie detection/reaping, and process group member counting.
Tests both the psutil path and /proc fallback path.
"""

from __future__ import annotations

import io
import os
from unittest.mock import MagicMock, mock_open, patch

import pytest

from marianne.daemon.system_probe import SystemProbe


# ─── get_memory_mb ─────────────────────────────────────────────────────


class TestGetMemoryMb:
    """Tests for SystemProbe.get_memory_mb()."""

    def test_returns_float(self):
        """get_memory_mb returns a positive float on a normal system."""
        result = SystemProbe.get_memory_mb()
        assert isinstance(result, float)
        assert result > 0

    def test_psutil_path(self):
        """Uses psutil.Process().memory_info().rss when psutil is available."""
        mock_psutil = MagicMock()
        mock_process = MagicMock()
        mock_process.memory_info.return_value.rss = 512 * 1024 * 1024
        mock_psutil.Process.return_value = mock_process

        with patch("marianne.daemon.system_probe._psutil", mock_psutil):
            result = SystemProbe.get_memory_mb()
            assert result == pytest.approx(512.0)

    def test_returns_none_when_all_probes_fail(self):
        """Returns None when both psutil and /proc fail."""
        with (
            patch("marianne.daemon.system_probe._psutil", None),
            patch("builtins.open", side_effect=OSError("no /proc")),
        ):
            result = SystemProbe.get_memory_mb()
            assert result is None

    def test_proc_fallback_parses_vmrss(self):
        """Falls back to /proc/self/status and parses VmRSS line."""
        proc_status = (
            "Name:\tpython3\n"
            "VmPeak:\t 204800 kB\n"
            "VmRSS:\t  102400 kB\n"
            "VmSize:\t 204800 kB\n"
        )
        with (
            patch("marianne.daemon.system_probe._psutil", None),
            patch("builtins.open", mock_open(read_data=proc_status)),
        ):
            result = SystemProbe.get_memory_mb()

        # 102400 kB = 100.0 MB
        assert result == pytest.approx(100.0)

    def test_proc_fallback_returns_none_on_valueerror(self):
        """Returns None when VmRSS value is corrupt."""
        proc_status = "VmRSS:\t  corrupt kB\n"
        with (
            patch("marianne.daemon.system_probe._psutil", None),
            patch("builtins.open", mock_open(read_data=proc_status)),
        ):
            result = SystemProbe.get_memory_mb()
            assert result is None


# ─── get_child_count ────────────────────────────────────────────────────


class TestGetChildCount:
    """Tests for SystemProbe.get_child_count()."""

    def test_returns_int(self):
        """get_child_count returns a non-negative int."""
        result = SystemProbe.get_child_count()
        assert isinstance(result, int)
        assert result >= 0

    def test_fallback_when_psutil_unavailable(self):
        """Falls back to /proc scanning when psutil is not available."""
        with patch("marianne.daemon.system_probe._psutil", None):
            result = SystemProbe.get_child_count()
            assert isinstance(result, int)
            assert result >= 0

    def test_proc_fallback_returns_none_when_no_entries_readable(self):
        """Returns None when /proc lists PIDs but none are readable."""
        with (
            patch("marianne.daemon.system_probe._psutil", None),
            patch("marianne.daemon.system_probe.os.listdir", return_value=["1", "2", "3"]),
            patch.object(SystemProbe, "_read_proc_ppid", return_value=None),
        ):
            result = SystemProbe.get_child_count()
            assert result is None

    def test_proc_fallback_counts_matching_ppids(self):
        """Counts /proc entries whose PPid matches our PID."""
        my_pid = 1000

        def fake_ppid(pid_str: str) -> int | None:
            return {
                "1001": my_pid,   # child
                "1002": my_pid,   # child
                "1003": 999,      # not our child
                "1004": None,     # unreadable
            }.get(pid_str)

        with (
            patch("marianne.daemon.system_probe._psutil", None),
            patch("marianne.daemon.system_probe.os.listdir", return_value=["1001", "1002", "1003", "1004", "self"]),
            patch("marianne.daemon.system_probe.os.getpid", return_value=my_pid),
            patch.object(SystemProbe, "_read_proc_ppid", side_effect=fake_ppid),
        ):
            result = SystemProbe.get_child_count()

        assert result == 2

    def test_proc_fallback_returns_none_on_listdir_oserror(self):
        """Returns None when /proc listdir fails entirely."""
        with (
            patch("marianne.daemon.system_probe._psutil", None),
            patch("marianne.daemon.system_probe.os.listdir", side_effect=OSError("permission denied")),
        ):
            result = SystemProbe.get_child_count()
            assert result is None


# ─── get_zombies ────────────────────────────────────────────────────────


class TestGetZombies:
    """Tests for SystemProbe.get_zombies()."""

    def test_returns_list(self):
        """get_zombies returns a list of ints."""
        result = SystemProbe.get_zombies()
        assert isinstance(result, list)
        # In a test environment, we shouldn't have zombies
        assert all(isinstance(pid, int) for pid in result)


# ─── reap_zombies ──────────────────────────────────────────────────────


class TestReapZombies:
    """Tests for SystemProbe.reap_zombies()."""

    def test_returns_list(self):
        """reap_zombies returns a list of ints."""
        result = SystemProbe.reap_zombies()
        assert isinstance(result, list)

    def test_with_mock_psutil_zombie(self):
        """Reaps a mock zombie child via psutil path."""
        mock_psutil = MagicMock()
        mock_psutil.STATUS_ZOMBIE = "zombie"
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        zombie = MagicMock()
        zombie.pid = 42
        zombie.status.return_value = "zombie"

        current = MagicMock()
        current.children.return_value = [zombie]
        mock_psutil.Process.return_value = current

        with (
            patch("marianne.daemon.system_probe._psutil", mock_psutil),
            patch("marianne.daemon.system_probe.os.waitpid") as mock_waitpid,
        ):
            result = SystemProbe.reap_zombies()

        assert 42 in result
        mock_waitpid.assert_called_once_with(42, os.WNOHANG)

    def test_fallback_to_waitpid(self):
        """Uses os.waitpid loop when psutil is not available."""
        with (
            patch("marianne.daemon.system_probe._psutil", None),
            # waitpid will return (0, 0) meaning no zombies
            patch(
                "marianne.daemon.system_probe.os.waitpid",
                side_effect=ChildProcessError,
            ),
        ):
            result = SystemProbe.reap_zombies()
            assert result == []


# ─── count_group_members ───────────────────────────────────────────────


class TestCountGroupMembers:
    """Tests for SystemProbe.count_group_members()."""

    def test_returns_int(self):
        """count_group_members returns a non-negative int."""
        pgid = os.getpgrp()
        result = SystemProbe.count_group_members(pgid, exclude_pid=0)
        assert isinstance(result, int)
        assert result >= 0

    def test_excludes_specified_pid(self):
        """The exclude_pid is not counted."""
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
            return {100: 1234, 200: 1234, 300: 5678}[pid]

        with (
            patch("marianne.daemon.system_probe._psutil", mock_psutil),
            patch("marianne.daemon.system_probe.os.getpgid", side_effect=fake_getpgid),
        ):
            # Exclude pid 100, only pid 200 matches
            count = SystemProbe.count_group_members(1234, exclude_pid=100)

        assert count == 1

    def test_fallback_when_psutil_unavailable(self):
        """Falls back to /proc when psutil is not installed."""
        with patch("marianne.daemon.system_probe._psutil", None):
            # Just test it doesn't crash
            result = SystemProbe.count_group_members(
                os.getpgrp(), exclude_pid=os.getpid(),
            )
            assert isinstance(result, int)
            assert result >= 0

    def test_proc_group_count_with_matching_entries(self):
        """Counts /proc/*/stat entries matching the given pgid."""
        target_pgid = 1234

        # /proc/{pid}/stat format: pid (comm) state ppid pgid ...
        stat_entries = {
            "100": f"100 (python) S 99 {target_pgid} 100 0",    # matches
            "200": f"200 (node) S 99 {target_pgid} 200 0",      # matches
            "300": "300 (bash) S 99 9999 300 0",                  # different pgid
        }

        def fake_open(path, **_):
            for pid_str, content in stat_entries.items():
                if f"/proc/{pid_str}/stat" in str(path):
                    return io.StringIO(content)
            raise OSError("not found")

        with (
            patch("marianne.daemon.system_probe._psutil", None),
            patch("marianne.daemon.system_probe.os.listdir", return_value=["100", "200", "300", "self"]),
            patch("builtins.open", side_effect=fake_open),
        ):
            result = SystemProbe.count_group_members(target_pgid, exclude_pid=0)

        assert result == 2

    def test_proc_group_count_excludes_specified_pid(self):
        """Verifies exclude_pid is honoured in /proc fallback."""
        target_pgid = 1234

        stat_entries = {
            "100": f"100 (python) S 99 {target_pgid} 100 0",
            "200": f"200 (node) S 99 {target_pgid} 200 0",
        }

        def fake_open(path, **_):
            for pid_str, content in stat_entries.items():
                if f"/proc/{pid_str}/stat" in str(path):
                    return io.StringIO(content)
            raise OSError("not found")

        with (
            patch("marianne.daemon.system_probe._psutil", None),
            patch("marianne.daemon.system_probe.os.listdir", return_value=["100", "200"]),
            patch("builtins.open", side_effect=fake_open),
        ):
            # Exclude pid 100, only pid 200 should match
            result = SystemProbe.count_group_members(target_pgid, exclude_pid=100)

        assert result == 1

    def test_proc_group_count_returns_none_on_listdir_failure(self):
        """Returns None when /proc listdir fails entirely."""
        with (
            patch("marianne.daemon.system_probe._psutil", None),
            patch("marianne.daemon.system_probe.os.listdir", side_effect=OSError("no /proc")),
        ):
            result = SystemProbe.count_group_members(1234, exclude_pid=0)
            assert result is None


# ─── Integration: monitor delegates to SystemProbe ─────────────────────


class TestMonitorDelegation:
    """Verify ResourceMonitor delegates to SystemProbe."""

    def test_memory_delegation(self):
        """ResourceMonitor._get_memory_usage_mb delegates to SystemProbe."""
        from marianne.daemon.monitor import ResourceMonitor

        with patch.object(SystemProbe, "get_memory_mb", return_value=256.0):
            result = ResourceMonitor._get_memory_usage_mb()
        assert result == 256.0

    def test_child_count_delegation(self):
        """ResourceMonitor._get_child_process_count delegates to SystemProbe."""
        from marianne.daemon.monitor import ResourceMonitor

        with patch.object(SystemProbe, "get_child_count", return_value=5):
            result = ResourceMonitor._get_child_process_count()
        assert result == 5

    def test_zombie_delegation(self):
        """ResourceMonitor._check_for_zombies delegates to SystemProbe."""
        from marianne.daemon.monitor import ResourceMonitor

        with patch.object(SystemProbe, "reap_zombies", return_value=[42]):
            result = ResourceMonitor._check_for_zombies()
        assert result == [42]
