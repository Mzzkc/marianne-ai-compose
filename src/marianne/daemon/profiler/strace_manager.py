"""Strace management for the Marianne daemon profiler.

Attaches ``strace -c`` to child processes to collect per-syscall count and
time-percentage summaries.  Also supports on-demand full trace via
``strace -f -t -p PID -o <file>`` for deep debugging.

Handles gracefully:
- strace not installed
- Permission denied (non-root)
- Target process already exited
- Strace process cleanup on daemon shutdown

Strace processes are tracked so they can be cleaned up by
``ProcessGroupManager`` on daemon shutdown.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import signal
from pathlib import Path
from typing import Any

from marianne.core.logging import get_logger

_logger = get_logger("daemon.profiler.strace_manager")

# Check strace availability once at import time
_strace_path: str | None = shutil.which("strace")


class StraceManager:
    """Manages strace attachment to child processes.

    Typical lifecycle::

        mgr = StraceManager(enabled=True)
        await mgr.attach(pid)        # spawns ``strace -c -p PID``
        ...                           # time passes, child does work
        summary = await mgr.detach(pid)  # SIGINT -> parse summary
        await mgr.detach_all()        # cleanup on shutdown
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        # Maps target PID -> strace asyncio.subprocess.Process
        self._attached: dict[int, asyncio.subprocess.Process] = {}
        # Maps target PID -> full-trace asyncio.subprocess.Process
        self._full_traces: dict[int, asyncio.subprocess.Process] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def attached_pids(self) -> list[int]:
        """PIDs currently being traced."""
        return list(self._attached.keys())

    @staticmethod
    def is_available() -> bool:
        """Check whether strace is available on this system."""
        return _strace_path is not None

    # --- attach (summary trace) ------------------------------------

    async def attach(self, pid: int) -> bool:
        """Attach ``strace -c -p <pid>`` for syscall summary collection.

        Args:
            pid: Target process PID to trace.

        Returns:
            True if strace was successfully spawned, False otherwise.
        """
        if not self._enabled:
            _logger.debug("strace_disabled", pid=pid)
            return False

        if _strace_path is None:
            _logger.warning("strace_not_available")
            return False

        if pid in self._attached:
            _logger.debug("strace_already_attached", pid=pid)
            return True

        try:
            proc = await asyncio.create_subprocess_exec(
                _strace_path,
                "-c",
                "-p",
                str(pid),
                "-e",
                "trace=all",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._attached[pid] = proc
            _logger.info("strace_attached", pid=pid, strace_pid=proc.pid)
            return True
        except PermissionError:
            _logger.warning("strace_permission_denied", pid=pid)
            return False
        except FileNotFoundError:
            _logger.warning("strace_not_found", pid=pid)
            return False
        except ProcessLookupError:
            _logger.debug("strace_target_already_exited", pid=pid)
            return False
        except OSError as exc:
            _logger.warning("strace_attach_failed", pid=pid, error=str(exc))
            return False

    # --- detach (summary trace) ------------------------------------

    async def detach(self, pid: int) -> dict[str, Any] | None:
        """Detach strace from a process and parse the summary output.

        Sends SIGINT to the strace process (which causes it to print its
        ``-c`` summary table to stderr), then parses the output.

        Args:
            pid: Target process PID to stop tracing.

        Returns:
            Dict with ``syscall_counts`` and ``syscall_time_pct`` mappings,
            or None if the pid was not being traced.
        """
        proc = self._attached.pop(pid, None)
        if proc is None:
            return None

        # Send SIGINT to strace so it prints the summary
        try:
            if proc.returncode is None:
                proc.send_signal(signal.SIGINT)
        except (ProcessLookupError, OSError):
            # strace already exited
            pass

        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        except TimeoutError:
            _logger.warning("strace_detach_timeout", pid=pid)
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            return None

        if not stderr:
            return None

        output = stderr.decode(errors="replace")
        _logger.debug("strace_output_received", pid=pid, output_len=len(output))
        return self._parse_strace_summary(output)

    # --- full trace (on-demand) ------------------------------------

    async def attach_full_trace(self, pid: int, output_file: Path) -> bool:
        """Attach a full strace (``strace -f -t -p PID -o file``).

        This is the on-demand deep-trace triggered by ``mzt top --trace PID``.

        Args:
            pid: Target process PID.
            output_file: Path to write the full trace output.

        Returns:
            True if strace was successfully spawned, False otherwise.
        """
        if not self._enabled:
            return False

        if _strace_path is None:
            _logger.warning("strace_not_available_full")
            return False

        if pid in self._full_traces:
            _logger.debug("full_trace_already_attached", pid=pid)
            return True

        output_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            proc = await asyncio.create_subprocess_exec(
                _strace_path,
                "-f",
                "-t",
                "-p",
                str(pid),
                "-o",
                str(output_file),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self._full_traces[pid] = proc
            _logger.info(
                "full_trace_attached",
                pid=pid,
                strace_pid=proc.pid,
                output_file=str(output_file),
            )
            return True
        except PermissionError:
            _logger.warning("full_trace_permission_denied", pid=pid)
            return False
        except FileNotFoundError:
            _logger.warning("full_trace_strace_not_found", pid=pid)
            return False
        except ProcessLookupError:
            _logger.debug("full_trace_target_exited", pid=pid)
            return False
        except OSError as exc:
            _logger.warning("full_trace_attach_failed", pid=pid, error=str(exc))
            return False

    # --- cleanup ---------------------------------------------------

    async def detach_all(self) -> None:
        """Detach and terminate all strace processes.

        Called during daemon shutdown for cleanup.
        """
        all_procs: list[tuple[int, asyncio.subprocess.Process]] = []
        all_procs.extend(self._attached.items())
        all_procs.extend(self._full_traces.items())

        self._attached.clear()
        self._full_traces.clear()

        for item in all_procs:
            proc = item[1]
            try:
                if proc.returncode is None:
                    proc.send_signal(signal.SIGINT)
            except (ProcessLookupError, OSError):
                continue

        # Give them a moment to exit cleanly, then force-kill
        for item in all_procs:
            proc = item[1]
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except (ProcessLookupError, OSError):
                    pass
            except (ProcessLookupError, OSError):
                pass

        if all_procs:
            _logger.info("strace_detach_all", count=len(all_procs))

    def get_strace_pids(self) -> list[int]:
        """Return PIDs of all running strace subprocesses.

        Useful for registering with ProcessGroupManager so they
        get cleaned up on daemon shutdown.
        """
        pids: list[int] = []
        for proc in self._attached.values():
            if proc.pid is not None and proc.returncode is None:
                pids.append(proc.pid)
        for proc in self._full_traces.values():
            if proc.pid is not None and proc.returncode is None:
                pids.append(proc.pid)
        return pids

    # --- strace -c output parsing ----------------------------------

    @staticmethod
    def _parse_strace_summary(output: str) -> dict[str, Any]:
        """Parse strace ``-c`` summary table output.

        Expected format::

            % time     seconds  usecs/call     calls    errors syscall
            ------ ----------- ----------- --------- --------- ----------------
             40.12    0.123456          82      1500           write
             28.33    0.087654          27      3200           read
            ------ ----------- ----------- --------- --------- ----------------
            100.00    0.211110                  4700           total

        Returns:
            Dict with:
                - ``syscall_counts``: ``{syscall_name: call_count}``
                - ``syscall_time_pct``: ``{syscall_name: time_percentage}``
        """
        counts: dict[str, int] = {}
        time_pct: dict[str, float] = {}

        # Match lines like: "  40.12    0.123456          82      1500           write"
        # or:               "  40.12    0.123456          82      1500       100 write"
        pattern = re.compile(
            r"^\s*"
            r"(?P<pct>[\d.]+)\s+"       # % time
            r"[\d.]+\s+"                # seconds
            r"[\d.]+\s+"                # usecs/call
            r"(?P<calls>\d+)\s+"        # calls
            r"(?:\d+\s+)?"             # errors (optional)
            r"(?P<syscall>\w+)\s*$"     # syscall name
        )

        for line in output.splitlines():
            # Skip header, separator, and total lines
            if "------" in line or "syscall" in line or not line.strip():
                continue
            if line.strip().endswith("total"):
                continue

            m = pattern.match(line)
            if m:
                syscall = m.group("syscall")
                counts[syscall] = int(m.group("calls"))
                time_pct[syscall] = float(m.group("pct"))

        return {
            "syscall_counts": counts,
            "syscall_time_pct": time_pct,
        }


__all__ = ["StraceManager"]
