"""Consolidated system probes for the Marianne daemon.

Provides a single ``SystemProbe`` class that encapsulates the
"try psutil → fallback to /proc" pattern used across the daemon for:

- Memory usage (RSS)
- Child process counting
- Zombie detection and reaping
- Process group member counting

Extracted from ``monitor.py`` and ``pgroup.py`` (FIX-16) to eliminate
5× duplication of the psutil/proc fallback logic.  All methods are
static — callers import the class and call methods directly.
"""

from __future__ import annotations

import os

from marianne.core.logging import get_logger

_logger = get_logger("daemon.system_probe")

# Check psutil availability once at import time
try:
    import psutil as _psutil
except ImportError:
    _psutil = None  # type: ignore[assignment]


class SystemProbe:
    """Consolidated system resource probes.

    Each method tries psutil first, then falls back to /proc on Linux.
    Returns ``None`` when all probes fail — callers should treat that
    as a critical condition (fail-closed).
    """

    @staticmethod
    def get_memory_mb() -> float | None:
        """Get current process RSS memory in MB.

        Uses ``psutil.Process().memory_info().rss`` if available,
        falls back to reading ``VmRSS`` from ``/proc/self/status``.

        Returns:
            RSS in megabytes, or ``None`` when all probes fail.
        """
        if _psutil is not None:
            try:
                proc = _psutil.Process()
                rss_bytes: int = proc.memory_info().rss
                return rss_bytes / (1024 * 1024)
            except (_psutil.NoSuchProcess, _psutil.AccessDenied, OSError, AttributeError):
                _logger.debug("psutil_memory_probe_failed", exc_info=True)
        # Fallback: /proc/self/status (Linux only)
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024  # kB -> MB
        except (OSError, ValueError):
            pass
        _logger.warning("memory_probe_failed_all_methods", exc_info=True)
        return None

    @staticmethod
    def get_child_count() -> int | None:
        """Count child processes of the current process (recursive).

        Uses ``psutil.Process().children(recursive=True)`` if available,
        falls back to scanning ``/proc/*/status`` for matching PPid.

        Returns:
            Number of child processes, or ``None`` when all probes fail.
        """
        if _psutil is not None:
            try:
                current = _psutil.Process()
                return len(current.children(recursive=True))
            except (_psutil.NoSuchProcess, _psutil.AccessDenied, OSError, AttributeError):
                _logger.debug("psutil_child_count_probe_failed", exc_info=True)
        return SystemProbe._proc_child_count()

    @staticmethod
    def get_zombies() -> list[int]:
        """Detect zombie child processes (without reaping).

        Returns:
            List of zombie PIDs that were detected (not necessarily reaped).
        """
        return SystemProbe._scan_zombies(reap=False)

    @staticmethod
    def reap_zombies() -> list[int]:
        """Detect and reap zombie child processes.

        Returns:
            List of PIDs that were reaped.
        """
        return SystemProbe._scan_zombies(reap=True)

    @staticmethod
    def _reap_pid(pid: int) -> None:
        """Reap a single zombie process by PID."""
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass

    @staticmethod
    def _check_child_zombie(child: object, *, reap: bool, found: list[int]) -> None:
        """Check if a psutil child process is a zombie and optionally reap it.

        Args:
            child: A psutil.Process instance (typed as object since psutil is optional).
            reap: If True, reap the zombie via os.waitpid().
            found: Accumulator list for zombie PIDs.
        """
        assert _psutil is not None  # caller guards this
        try:
            if child.status() == _psutil.STATUS_ZOMBIE:  # type: ignore[attr-defined]
                if reap:
                    SystemProbe._reap_pid(child.pid)  # type: ignore[attr-defined]
                found.append(child.pid)  # type: ignore[attr-defined]
        except (_psutil.NoSuchProcess, _psutil.AccessDenied):
            pass

    @staticmethod
    def _scan_zombies(*, reap: bool) -> list[int]:
        """Shared implementation for zombie detection and reaping.

        Uses psutil to iterate children and check status, falls back
        to ``os.waitpid(-1, WNOHANG)`` which inherently reaps.

        Args:
            reap: If True, call ``os.waitpid()`` on each zombie found
                  via psutil to actually reap it.
        """
        found: list[int] = []
        if _psutil is not None:
            try:
                current = _psutil.Process()
                for child in current.children(recursive=True):
                    SystemProbe._check_child_zombie(child, reap=reap, found=found)
                return found
            except (_psutil.NoSuchProcess, _psutil.AccessDenied, OSError, AttributeError):
                _logger.warning("zombie_scan_failed", exc_info=True)
                return found
        return SystemProbe._waitpid_reap_loop()

    @staticmethod
    def count_group_members(pgid: int, exclude_pid: int = 0) -> int | None:
        """Count processes in a process group, excluding one PID.

        Uses psutil ``Process.pgid`` per-process if available, falls
        back to reading ``/proc/*/stat`` field 5 (0-indexed 4).

        Note: ``psutil.process_iter(["pgid"])`` raises ``ValueError``
        because ``pgid`` is not a valid ``as_dict`` attribute.  We use
        per-process ``os.getpgid(proc.pid)`` instead.

        Args:
            pgid: Process group ID to count members of.
            exclude_pid: PID to exclude from count (typically self).

        Returns:
            Number of matching processes, or None if all probes fail.
        """
        if _psutil is not None:
            try:
                count = 0
                for proc in _psutil.process_iter(["pid"]):
                    try:
                        pid = proc.info["pid"]
                        if pid == exclude_pid:
                            continue
                        if os.getpgid(pid) == pgid:
                            count += 1
                    except (_psutil.NoSuchProcess, _psutil.AccessDenied, OSError, AttributeError):
                        continue
                return count
            except (_psutil.NoSuchProcess, _psutil.AccessDenied, OSError, AttributeError):
                _logger.debug("psutil_group_count_failed", exc_info=True)
        return SystemProbe._proc_group_count(pgid, exclude_pid)

    # ─── /proc fallback helpers ───────────────────────────────────

    @staticmethod
    def _proc_child_count() -> int | None:
        """Count child processes by scanning /proc/*/status for PPid.

        Linux-only fallback when psutil is unavailable.

        Returns:
            Number of child processes, or None if /proc scan fails entirely.
        """
        my_pid = os.getpid()
        count = 0
        any_readable = False
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                ppid = SystemProbe._read_proc_ppid(entry)
                if ppid is not None:
                    any_readable = True
                    if ppid == my_pid:
                        count += 1
        except OSError:
            _logger.warning("child_process_probe_failed", exc_info=True)
            return None
        if not any_readable:
            _logger.warning("child_process_probe_no_readable_entries")
            return None
        return count

    @staticmethod
    def _read_proc_ppid(pid_str: str) -> int | None:
        """Read the PPid field from /proc/{pid}/status."""
        try:
            with open(f"/proc/{pid_str}/status") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        return int(line.split()[1])
        except (OSError, ValueError):
            pass
        return None

    @staticmethod
    def _waitpid_reap_loop() -> list[int]:
        """Reap zombie children via waitpid loop.

        Fallback when psutil is unavailable. Also detects + reaps.
        """
        reaped: list[int] = []
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
                if os.WIFSIGNALED(status) or os.WIFEXITED(status):
                    reaped.append(pid)
            except ChildProcessError:
                break
        return reaped

    @staticmethod
    def _proc_group_count(pgid: int, exclude_pid: int = 0) -> int | None:
        """Count process group members by scanning /proc/*/stat.

        Linux-only fallback when psutil is unavailable.

        Returns:
            Number of group members, or None if /proc scan fails entirely.
        """
        count = 0
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                if pid == exclude_pid:
                    continue
                try:
                    with open(f"/proc/{pid}/stat") as f:
                        parts = f.read().split()
                        # Field 5 (0-indexed 4) is pgid
                        if len(parts) > 4 and int(parts[4]) == pgid:
                            count += 1
                except (OSError, ValueError, IndexError):
                    continue
        except OSError:
            _logger.warning("proc_group_probe_failed", exc_info=True)
            return None
        return count


__all__ = ["SystemProbe"]
