"""Bubblewrap (bwrap) process sandbox for agent execution isolation.

Wraps subprocess execution in a bubblewrap namespace providing:
- PID namespace isolation (--unshare-pid)
- Network isolation (--unshare-net)
- Filesystem sandboxing via bind mounts
- MCP socket forwarding via bind-mounted Unix sockets
- Automatic cleanup on conductor death (--die-with-parent)

Resource governance (memory caps, CPU quotas, PID limits) is handled
separately via systemd-run or prlimit — NOT baked into bwrap args.
bwrap handles namespace isolation; resource limits are orthogonal.

A bwrap subprocess starts in ~4ms. The sandbox overhead is measured
in kilobytes, not megabytes.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path

from marianne.core.logging import get_logger

_logger = get_logger("isolation.sandbox")

# Standard system directories mounted read-only inside the sandbox.
_SYSTEM_RO_DIRS: list[str] = ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc"]

# Bound the `bwrap --version` availability probe (#237). The probe is instant
# in a healthy environment; a hang (broken binary, NFS mount stall, constrained
# sandbox) must never block conductor startup — on timeout we kill the probe
# and report bwrap unavailable.
_BWRAP_PROBE_TIMEOUT = 5.0


@dataclass(frozen=True)
class ResourceLimits:
    """Optional resource caps for sandbox processes.

    These are NOT enforced by bwrap itself — they are metadata consumed
    by the conductor's resource governance layer (systemd-run, prlimit).
    BwrapSandbox stores them for the caller to apply separately.
    """

    memory_limit_mb: int | None = None
    """Maximum memory in MB. None means no cap."""

    cpu_quota_percent: int | None = None
    """CPU quota as a percentage (e.g. 50 = 50%%). None means no cap."""

    pid_limit: int | None = None
    """Maximum number of PIDs. None means no cap."""


class BwrapSandbox:
    """Wraps subprocess execution in a bubblewrap namespace.

    Given a workspace path, shared directories, MCP sockets, and optional
    resource limits, produces the bwrap command line that sets up isolation
    boundaries. The conductor uses this to wrap agent subprocess execution.

    Usage::

        sandbox = BwrapSandbox(
            workspace=Path("/tmp/agent-ws"),
            shared_dirs=[Path("/tmp/shared/specs")],
            mcp_sockets=[Path("/tmp/mzt/mcp/github.sock")],
            resource_limits=ResourceLimits(memory_limit_mb=512),
        )
        cmd = sandbox.wrap_command(["python", "agent_script.py"])
        # cmd is ["bwrap", "--bind", "/tmp/agent-ws", ...]
    """

    def __init__(
        self,
        workspace: Path,
        shared_dirs: list[Path],
        mcp_sockets: list[Path],
        resource_limits: ResourceLimits | None,
    ) -> None:
        if not isinstance(workspace, Path):
            raise TypeError(
                f"workspace must be a Path, got {type(workspace).__name__}"
            )
        self.workspace = workspace
        self.shared_dirs = shared_dirs
        self.mcp_sockets = mcp_sockets
        self.resource_limits = resource_limits

    def wrap_command(self, cmd: list[str]) -> list[str]:
        """Prepend bwrap args to a command.

        Produces a complete bwrap invocation that isolates the inner
        command in a namespace with the configured bind mounts.

        Args:
            cmd: The command to execute inside the sandbox.

        Returns:
            Full bwrap command line as a list of strings.
        """
        args: list[str] = ["bwrap"]

        # Workspace bind-mount (read-write)
        args.extend(["--bind", str(self.workspace), str(self.workspace)])

        # Standard system directories (read-only, tolerant of missing)
        for sys_dir in _SYSTEM_RO_DIRS:
            args.extend(["--ro-bind-try", sys_dir, sys_dir])

        # Proc and dev for basic functionality
        args.extend(["--proc", "/proc"])
        args.extend(["--dev", "/dev"])

        # Temporary directory
        args.extend(["--tmpfs", "/tmp"])

        # Shared directories (read-write for coordination)
        for shared_dir in self.shared_dirs:
            args.extend(["--bind", str(shared_dir), str(shared_dir)])

        # MCP socket forwarding (bind-mount each socket path)
        for socket_path in self.mcp_sockets:
            args.extend(["--bind", str(socket_path), str(socket_path)])

        # Namespace isolation
        args.append("--unshare-pid")
        args.append("--unshare-net")

        # Set working directory to workspace
        args.extend(["--chdir", str(self.workspace)])

        # Die with parent — sandbox dies if conductor dies
        args.append("--die-with-parent")

        # The inner command
        args.extend(cmd)

        _logger.debug(
            "bwrap_command_built",
            workspace=str(self.workspace),
            shared_dir_count=len(self.shared_dirs),
            mcp_socket_count=len(self.mcp_sockets),
            has_resource_limits=self.resource_limits is not None,
            inner_command=cmd[0] if cmd else "<empty>",
        )

        return args

    @staticmethod
    async def is_available() -> bool:
        """Check if bwrap is installed and runnable.

        Returns:
            True if ``bwrap --version`` exits successfully.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "bwrap", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            _logger.debug("bwrap_availability_check", available=False)
            return False

        try:
            await asyncio.wait_for(proc.wait(), timeout=_BWRAP_PROBE_TIMEOUT)
        except TimeoutError:
            # A hung probe (broken binary, NFS stall, constrained env) must not
            # block conductor startup (#237). Kill it, reap it (bounded), and
            # report bwrap unavailable.
            _logger.warning(
                "bwrap_availability_check.timeout",
                timeout=_BWRAP_PROBE_TIMEOUT,
            )
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=_BWRAP_PROBE_TIMEOUT)
            return False

        available = proc.returncode == 0
        _logger.debug("bwrap_availability_check", available=available)
        return available
