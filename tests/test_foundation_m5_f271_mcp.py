"""TDD tests for F-271: PluginCliBackend MCP config gap fix.

F-271: PluginCliBackend ignores mcp_config_flag, causing MCP process
explosion (80 child processes instead of 8) when the baton dispatches
sheets via instrument profiles.

Fix: Profile-driven approach — CliCommand.mcp_disable_args specifies
the exact CLI args to inject for disabling MCP servers. Each instrument
defines its own disable mechanism in the YAML profile.

For claude-code: ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _make_profile(
    *,
    mcp_disable_args: list[str] | None = None,
    extra_flags: list[str] | None = None,
) -> InstrumentProfile:
    """Create a minimal CLI profile for testing MCP behavior."""
    return InstrumentProfile(
        name="test-instrument",
        display_name="Test Instrument",
        kind="cli",
        cli=CliProfile(
            command=CliCommand(
                executable="test-cli",
                prompt_flag="-p",
                auto_approve_flag="--yes",
                mcp_disable_args=mcp_disable_args or [],
                extra_flags=extra_flags or [],
            ),
            output=CliOutputConfig(format="text"),
        ),
    )


class TestPluginCliBackendMcpDisabling:
    """F-271: _build_command must inject mcp_disable_args from profile."""

    def test_mcp_disabled_when_args_set(self) -> None:
        """When mcp_disable_args is set, command includes MCP disable args."""
        profile = _make_profile(
            mcp_disable_args=[
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
            ],
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        assert "--strict-mcp-config" in cmd, (
            "F-271: must inject --strict-mcp-config from mcp_disable_args"
        )
        mcp_idx = cmd.index("--mcp-config")
        assert cmd[mcp_idx + 1] == '{"mcpServers":{}}', (
            "F-271: must pass empty MCP config to disable all servers"
        )

    def test_no_mcp_args_when_empty(self) -> None:
        """When mcp_disable_args is empty, no MCP args are added."""
        profile = _make_profile(mcp_disable_args=[])
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        has_mcp = any("mcp" in arg.lower() for arg in cmd)
        assert not has_mcp, "No MCP args should appear when mcp_disable_args is empty"

    def test_mcp_args_before_extra_flags(self) -> None:
        """MCP disable args appear before extra_flags (extra_flags are always last)."""
        profile = _make_profile(
            mcp_disable_args=["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
            extra_flags=["--extra-flag"],
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        mcp_idx = cmd.index("--mcp-config")
        extra_idx = cmd.index("--extra-flag")
        assert mcp_idx < extra_idx, "MCP disable args must come before extra_flags"

    def test_profile_driven_mcp_disable_sequence(self) -> None:
        """Profile-driven MCP disabling produces the required sequence."""
        profile = _make_profile(
            mcp_disable_args=[
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
            ],
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        strict_idx = cmd.index("--strict-mcp-config")
        config_idx = cmd.index("--mcp-config")

        # Same order as legacy backend
        assert strict_idx < config_idx, "--strict-mcp-config must come before --mcp-config"
        assert cmd[config_idx + 1] == '{"mcpServers":{}}', (
            "Empty MCP config must immediately follow --mcp-config"
        )

    def test_real_claude_code_profile_gets_mcp_disabled(self) -> None:
        """Integration: the real claude-code profile gets MCP disabled."""
        from marianne.instruments.loader import InstrumentProfileLoader

        profiles = InstrumentProfileLoader.load_directory(
            Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"
        )
        claude_profile = profiles.get("claude-code")
        assert claude_profile is not None, "claude-code profile must exist"
        assert claude_profile.cli is not None

        # Profile has mcp_disable_args set
        assert claude_profile.cli.command.mcp_disable_args, (
            "F-271: claude-code profile must have mcp_disable_args"
        )

        backend = PluginCliBackend(claude_profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        assert "--strict-mcp-config" in cmd, "F-271: real claude-code profile must get MCP disabled"
        assert "--mcp-config" in cmd
        mcp_idx = cmd.index("--mcp-config")
        assert cmd[mcp_idx + 1] == '{"mcpServers":{}}'

    def test_custom_mcp_disable_mechanism(self) -> None:
        """Profiles with different MCP disable mechanisms work correctly.

        The profile-driven approach means each instrument can specify its
        own disable mechanism — not limited to claude-code's flags.
        """
        profile = _make_profile(
            mcp_disable_args=["--no-mcp"],
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        assert "--no-mcp" in cmd, "Custom MCP disable mechanism should be injected"

    def test_mcp_disable_args_default_is_empty(self) -> None:
        """CliCommand.mcp_disable_args defaults to empty list (safe default)."""
        cmd = CliCommand(executable="test")
        assert cmd.mcp_disable_args == [], (
            "Default mcp_disable_args must be empty — instruments without "
            "MCP support shouldn't get any MCP args injected"
        )
