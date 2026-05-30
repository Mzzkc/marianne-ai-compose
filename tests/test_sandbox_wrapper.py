"""Tests for the bwrap sandbox wrapper in the isolation package.

TDD tests for BwrapSandbox — verifies command construction, bind-mount
configuration, and availability detection without requiring actual
process isolation (which needs privileges).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.isolation.sandbox import BwrapSandbox, ResourceLimits

# =============================================================================
# ResourceLimits tests
# =============================================================================


class TestResourceLimits:
    """ResourceLimits — optional caps for sandbox processes."""

    def test_default_none_limits(self) -> None:
        limits = ResourceLimits()
        assert limits.memory_limit_mb is None
        assert limits.cpu_quota_percent is None
        assert limits.pid_limit is None

    def test_custom_limits(self) -> None:
        limits = ResourceLimits(
            memory_limit_mb=512,
            cpu_quota_percent=50,
            pid_limit=100,
        )
        assert limits.memory_limit_mb == 512
        assert limits.cpu_quota_percent == 50
        assert limits.pid_limit == 100

    def test_frozen(self) -> None:
        """ResourceLimits is immutable after creation."""
        limits = ResourceLimits()
        with pytest.raises(AttributeError):
            limits.memory_limit_mb = 1024  # type: ignore[misc]


# =============================================================================
# BwrapSandbox initialization tests
# =============================================================================


class TestBwrapSandboxInit:
    """BwrapSandbox construction and parameter validation."""

    def test_minimal_init(self) -> None:
        sandbox = BwrapSandbox(
            workspace=Path("/tmp/agent-ws"),
            shared_dirs=[],
            mcp_sockets=[],
            resource_limits=None,
        )
        assert sandbox.workspace == Path("/tmp/agent-ws")
        assert sandbox.shared_dirs == []
        assert sandbox.mcp_sockets == []
        assert sandbox.resource_limits is None

    def test_full_init(self) -> None:
        limits = ResourceLimits(memory_limit_mb=1024, pid_limit=200)
        sandbox = BwrapSandbox(
            workspace=Path("/tmp/agent-ws"),
            shared_dirs=[Path("/tmp/shared/specs"), Path("/tmp/shared/active")],
            mcp_sockets=[Path("/tmp/mzt/mcp/github.sock")],
            resource_limits=limits,
        )
        assert len(sandbox.shared_dirs) == 2
        assert len(sandbox.mcp_sockets) == 1
        assert sandbox.resource_limits is not None
        assert sandbox.resource_limits.memory_limit_mb == 1024

    def test_rejects_non_path_workspace(self) -> None:
        """Workspace must be a Path, not a string."""
        with pytest.raises(TypeError):
            BwrapSandbox(
                workspace="/tmp/agent-ws",  # type: ignore[arg-type]
                shared_dirs=[],
                mcp_sockets=[],
                resource_limits=None,
            )


# =============================================================================
# wrap_command tests
# =============================================================================


class TestWrapCommand:
    """BwrapSandbox.wrap_command — produces correct bwrap argument lists."""

    def _make_sandbox(
        self,
        workspace: Path = Path("/tmp/agent-ws"),
        shared_dirs: list[Path] | None = None,
        mcp_sockets: list[Path] | None = None,
        resource_limits: ResourceLimits | None = None,
    ) -> BwrapSandbox:
        return BwrapSandbox(
            workspace=workspace,
            shared_dirs=shared_dirs or [],
            mcp_sockets=mcp_sockets or [],
            resource_limits=resource_limits,
        )

    def test_starts_with_bwrap(self) -> None:
        sandbox = self._make_sandbox()
        cmd = sandbox.wrap_command(["python", "script.py"])
        assert cmd[0] == "bwrap"

    def test_ends_with_inner_command(self) -> None:
        sandbox = self._make_sandbox()
        inner = ["python", "-c", "print('hello')"]
        cmd = sandbox.wrap_command(inner)
        assert cmd[-3:] == inner

    def test_workspace_bind_mount(self) -> None:
        sandbox = self._make_sandbox(workspace=Path("/home/user/ws"))
        cmd = sandbox.wrap_command(["echo", "hi"])
        # workspace should be bind-mounted read-write
        assert "--bind" in cmd
        idx = cmd.index("--bind")
        assert cmd[idx + 1] == "/home/user/ws"

    def test_pid_namespace_isolation(self) -> None:
        sandbox = self._make_sandbox()
        cmd = sandbox.wrap_command(["echo"])
        assert "--unshare-pid" in cmd

    def test_network_isolation_default(self) -> None:
        """Default sandbox isolates network."""
        sandbox = self._make_sandbox()
        cmd = sandbox.wrap_command(["echo"])
        assert "--unshare-net" in cmd

    def test_die_with_parent(self) -> None:
        """Sandbox dies if conductor dies."""
        sandbox = self._make_sandbox()
        cmd = sandbox.wrap_command(["echo"])
        assert "--die-with-parent" in cmd

    def test_system_dirs_read_only(self) -> None:
        """Standard system directories are mounted read-only."""
        sandbox = self._make_sandbox()
        cmd = sandbox.wrap_command(["echo"])
        # /usr should be read-only mounted
        assert "--ro-bind-try" in cmd
        # Check that /usr appears as a ro-bind-try target
        ro_indices = [i for i, v in enumerate(cmd) if v == "--ro-bind-try"]
        ro_targets = [cmd[i + 1] for i in ro_indices]
        assert "/usr" in ro_targets

    def test_proc_and_dev_mounted(self) -> None:
        sandbox = self._make_sandbox()
        cmd = sandbox.wrap_command(["echo"])
        assert "--proc" in cmd
        assert "--dev" in cmd

    def test_tmpfs_for_tmp(self) -> None:
        sandbox = self._make_sandbox()
        cmd = sandbox.wrap_command(["echo"])
        tmpfs_idx = cmd.index("--tmpfs")
        assert cmd[tmpfs_idx + 1] == "/tmp"

    def test_shared_dirs_bind_mounted(self) -> None:
        sandbox = self._make_sandbox(
            shared_dirs=[Path("/tmp/shared/specs"), Path("/tmp/shared/active")],
        )
        cmd = sandbox.wrap_command(["echo"])
        cmd_str = " ".join(cmd)
        assert "/tmp/shared/specs" in cmd_str
        assert "/tmp/shared/active" in cmd_str

    def test_mcp_sockets_bind_mounted(self) -> None:
        sandbox = self._make_sandbox(
            mcp_sockets=[
                Path("/tmp/mzt/mcp/github.sock"),
                Path("/tmp/mzt/mcp/filesystem.sock"),
            ],
        )
        cmd = sandbox.wrap_command(["echo"])
        cmd_str = " ".join(cmd)
        assert "/tmp/mzt/mcp/github.sock" in cmd_str
        assert "/tmp/mzt/mcp/filesystem.sock" in cmd_str

    def test_chdir_to_workspace(self) -> None:
        sandbox = self._make_sandbox(workspace=Path("/home/user/ws"))
        cmd = sandbox.wrap_command(["echo"])
        chdir_idx = cmd.index("--chdir")
        assert cmd[chdir_idx + 1] == "/home/user/ws"

    def test_empty_inner_command(self) -> None:
        """wrap_command with empty inner command still produces valid bwrap prefix."""
        sandbox = self._make_sandbox()
        cmd = sandbox.wrap_command([])
        assert cmd[0] == "bwrap"
        # Should not crash, just produce bwrap args with no trailing command

    def test_resource_limits_not_in_bwrap_args(self) -> None:
        """Resource limits are NOT baked into bwrap args — they use systemd-run.

        bwrap handles namespace isolation. Resource governance is separate
        (systemd-run or prlimit). wrap_command only produces bwrap args.
        """
        limits = ResourceLimits(memory_limit_mb=512, pid_limit=50)
        sandbox = self._make_sandbox(resource_limits=limits)
        cmd = sandbox.wrap_command(["echo"])
        # bwrap doesn't have memory/pid-limit flags
        cmd_str = " ".join(cmd)
        assert "MemoryMax" not in cmd_str
        assert "512" not in cmd_str


# =============================================================================
# is_available tests
# =============================================================================


class TestIsAvailable:
    """BwrapSandbox.is_available — bwrap installation detection."""

    async def test_available_when_bwrap_exists(self) -> None:
        """Returns True when bwrap --version succeeds."""
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(return_value=None)
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await BwrapSandbox.is_available()
            assert result is True
            # Verify it called bwrap --version
            mock_exec.assert_called_once()
            args = mock_exec.call_args
            assert args[0][0] == "bwrap"
            assert "--version" in args[0]

    async def test_unavailable_when_bwrap_missing(self) -> None:
        """Returns False when bwrap is not installed (FileNotFoundError)."""
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("bwrap not found"),
        ):
            result = await BwrapSandbox.is_available()
            assert result is False

    async def test_unavailable_when_bwrap_fails(self) -> None:
        """Returns False when bwrap --version exits non-zero."""
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(return_value=None)
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await BwrapSandbox.is_available()
            assert result is False

    async def test_unavailable_when_bwrap_hangs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#237: a hung `bwrap --version` (broken binary, NFS stall, constrained
        env) must NOT block conductor startup — the probe times out, the process
        is killed, and bwrap is reported unavailable."""
        monkeypatch.setattr(
            "marianne.isolation.sandbox._BWRAP_PROBE_TIMEOUT", 0.05
        )

        async def _hang() -> None:
            await asyncio.Event().wait()  # never completes

        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(side_effect=_hang)
        mock_proc.kill = MagicMock()
        mock_proc.returncode = None

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            # Guard: if is_available() lacks a timeout it hangs forever and the
            # guard fails the test (red); with the fix it returns promptly.
            result = await asyncio.wait_for(
                BwrapSandbox.is_available(), timeout=2.0
            )

        assert result is False
        mock_proc.kill.assert_called_once()


# =============================================================================
# Adversarial tests
# =============================================================================


@pytest.mark.adversarial
class TestBwrapSandboxAdversarial:
    """Adversarial inputs and edge cases for BwrapSandbox."""

    def test_workspace_with_spaces(self) -> None:
        """Workspace paths with spaces are handled correctly."""
        sandbox = BwrapSandbox(
            workspace=Path("/tmp/my agent workspace"),
            shared_dirs=[],
            mcp_sockets=[],
            resource_limits=None,
        )
        cmd = sandbox.wrap_command(["echo"])
        # Path should appear as a single argument, not split on spaces
        assert "/tmp/my agent workspace" in cmd

    def test_many_bind_mounts(self) -> None:
        """Large number of bind mounts doesn't break command construction."""
        dirs = [Path(f"/tmp/shared/dir{i}") for i in range(50)]
        sockets = [Path(f"/tmp/mzt/mcp/srv{i}.sock") for i in range(20)]
        sandbox = BwrapSandbox(
            workspace=Path("/tmp/ws"),
            shared_dirs=dirs,
            mcp_sockets=sockets,
            resource_limits=None,
        )
        cmd = sandbox.wrap_command(["echo"])
        # All paths should be present
        cmd_str = " ".join(cmd)
        for d in dirs:
            assert str(d) in cmd_str
        for s in sockets:
            assert str(s) in cmd_str

    def test_inner_command_with_special_chars(self) -> None:
        """Inner commands with shell-special characters pass through as-is.

        Since we use create_subprocess_exec (no shell), these are safe.
        """
        sandbox = BwrapSandbox(
            workspace=Path("/tmp/ws"),
            shared_dirs=[],
            mcp_sockets=[],
            resource_limits=None,
        )
        inner = ["bash", "-c", "echo $HOME && rm -rf /; cat /etc/passwd"]
        cmd = sandbox.wrap_command(inner)
        # The special chars should appear verbatim in the last args
        assert cmd[-1] == "echo $HOME && rm -rf /; cat /etc/passwd"

    def test_duplicate_shared_dirs(self) -> None:
        """Duplicate shared dirs are included as-is (caller's responsibility)."""
        sandbox = BwrapSandbox(
            workspace=Path("/tmp/ws"),
            shared_dirs=[Path("/tmp/shared"), Path("/tmp/shared")],
            mcp_sockets=[],
            resource_limits=None,
        )
        cmd = sandbox.wrap_command(["echo"])
        # Both should appear (bwrap handles duplicate mounts gracefully)
        count = cmd.count("/tmp/shared")
        assert count >= 2
