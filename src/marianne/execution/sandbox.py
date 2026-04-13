"""Lightweight process sandbox using bubblewrap (bwrap).

Provides process-level isolation for agent execution with near-zero overhead.
Uses the same technology Claude Code uses. Works on WSL2.

The sandbox provides:
- Workspace bind-mount (read-write to agent's work directory)
- Shared directory bind-mounts (selective read/write)
- MCP socket forwarding (Unix socket bind-mount from pool)
- Optional network isolation
- Optional resource caps (memory, CPU, PID limits)

Resource budget: sandbox overhead is measured in kilobytes, not megabytes.
A bwrap subprocess starts in ~4ms.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from marianne.core.logging import get_logger

_logger = get_logger("execution.sandbox")


class SandboxConfig(BaseModel):
    """Configuration for a bwrap sandbox instance.

    Defines the isolation boundaries for an agent execution subprocess.
    The conductor creates a SandboxConfig per agent based on their
    technique declarations and workspace assignment.
    """

    model_config = ConfigDict(extra="forbid")

    workspace: str = Field(
        description="Path to the agent's workspace directory (bind-mounted read-write)",
    )
    network_isolated: bool = Field(
        default=True,
        description="When True, the sandbox has no network access. "
        "API calls are proxied through the conductor.",
    )
    memory_limit_mb: int | None = Field(
        default=None,
        ge=64,
        description="Optional memory limit in MB. Requires cgroups v2. "
        "None means no memory cap (uses system limits).",
    )
    bind_mounts: list[str] = Field(
        default_factory=list,
        description="Additional paths to bind-mount into the sandbox. "
        "Used for MCP socket forwarding and shared directories.",
    )
    read_only_mounts: list[str] = Field(
        default_factory=list,
        description="Paths to bind-mount as read-only. "
        "Used for shared specs, technique manifests, etc.",
    )
    pid_limit: int | None = Field(
        default=None,
        ge=10,
        description="Maximum number of PIDs in the sandbox. "
        "Requires cgroups v2. None means no PID cap.",
    )
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to set inside the sandbox. "
        "API keys are NEVER passed here — the conductor proxies authenticated "
        "requests through technique implementations.",
    )


class SandboxWrapper:
    """Builds and manages bwrap sandbox commands.

    Given a SandboxConfig, produces the bwrap command line that sets up
    the isolation boundaries. The conductor uses this to wrap agent
    subprocess execution.

    Usage::

        config = SandboxConfig(workspace="/tmp/agent-ws")
        wrapper = SandboxWrapper(config)
        cmd = wrapper.build_command(["python", "agent_script.py"])
        # cmd is ["bwrap", "--bind", "/tmp/agent-ws", "/workspace", ...]
    """

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config

    def build_command(self, inner_command: list[str]) -> list[str]:
        """Build the bwrap command wrapping the given inner command.

        Args:
            inner_command: The command to execute inside the sandbox.

        Returns:
            Full bwrap command line as a list of strings.
        """
        cmd = ["bwrap"]

        # Workspace bind-mount (read-write)
        cmd.extend(["--bind", self.config.workspace, "/workspace"])

        # Standard system directories (read-only)
        for sys_dir in ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc"]:
            cmd.extend(["--ro-bind-try", sys_dir, sys_dir])

        # Proc and dev for basic functionality
        cmd.extend(["--proc", "/proc"])
        cmd.extend(["--dev", "/dev"])

        # Temporary directory
        cmd.extend(["--tmpfs", "/tmp"])

        # Additional bind mounts (MCP sockets, shared dirs)
        for mount_path in self.config.bind_mounts:
            cmd.extend(["--bind", mount_path, mount_path])

        # Read-only mounts
        for ro_path in self.config.read_only_mounts:
            cmd.extend(["--ro-bind", ro_path, ro_path])

        # Network isolation
        if self.config.network_isolated:
            cmd.append("--unshare-net")

        # PID namespace isolation
        cmd.append("--unshare-pid")

        # Set working directory
        cmd.extend(["--chdir", "/workspace"])

        # Environment variables
        for key, value in self.config.env_vars.items():
            cmd.extend(["--setenv", key, value])

        # Die with parent — sandbox dies if conductor dies
        cmd.append("--die-with-parent")

        # The inner command
        cmd.extend(inner_command)

        _logger.debug(
            "sandbox_command_built",
            workspace=self.config.workspace,
            network_isolated=self.config.network_isolated,
            bind_mount_count=len(self.config.bind_mounts),
            inner_command=inner_command[0] if inner_command else "<empty>",
        )

        return cmd

    @staticmethod
    async def check_available() -> bool:
        """Check if bwrap is available on the system.

        Returns:
            True if bwrap is installed and runnable.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "bwrap", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return proc.returncode == 0
        except FileNotFoundError:
            return False
