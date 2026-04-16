"""Tests for MCP config injection in PluginCliBackend."""

from __future__ import annotations

from pathlib import Path

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _prof(*, mcp_flag: str | None = None, mcp_dis: list[str] | None = None) -> InstrumentProfile:
    cmd = CliCommand(
        executable="test-inst",
        prompt_flag="-p",
        mcp_config_flag=mcp_flag,
        mcp_disable_args=mcp_dis or [],
    )
    return InstrumentProfile(
        name="test",
        display_name="Test",
        kind="cli",
        cli=CliProfile(command=cmd, output=CliOutputConfig(format="text")),
    )


class TestMcpConfigInjection:
    def test_mcp_config_flag_used_when_path_set(self) -> None:
        b = PluginCliBackend(_prof(mcp_flag="--mcp-config", mcp_dis=["--no-mcp"]))
        b.set_mcp_config(Path("/tmp/mcp-config.json"))
        cmd = b._build_command("test", timeout_seconds=30)
        assert "--mcp-config" in cmd and "/tmp/mcp-config.json" in cmd and "--no-mcp" not in cmd

    def test_mcp_disable_args_used_when_no_path(self) -> None:
        b = PluginCliBackend(_prof(mcp_flag="--mcp-config", mcp_dis=["--no-mcp"]))
        cmd = b._build_command("test", timeout_seconds=30)
        assert "--no-mcp" in cmd and "--mcp-config" not in cmd

    def test_set_mcp_config_none_clears(self) -> None:
        b = PluginCliBackend(_prof(mcp_flag="--mcp-config", mcp_dis=["--no-mcp"]))
        b.set_mcp_config(Path("/tmp/c.json"))
        b.set_mcp_config(None)
        cmd = b._build_command("test", timeout_seconds=30)
        assert "--no-mcp" in cmd

    def test_no_mcp_flag_no_disable_args(self) -> None:
        b = PluginCliBackend(_prof())
        cmd = b._build_command("test", timeout_seconds=30)
        assert "--mcp-config" not in cmd and "--no-mcp" not in cmd

    def test_mcp_config_flag_without_profile_flag(self) -> None:
        b = PluginCliBackend(
            _prof(
                mcp_flag=None, mcp_dis=["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
            )
        )
        b.set_mcp_config(Path("/tmp/c.json"))
        cmd = b._build_command("test", timeout_seconds=30)
        assert "--strict-mcp-config" in cmd
