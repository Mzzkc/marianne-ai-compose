"""Tests for F-271: PluginCliBackend MCP disabling.

The legacy ClaudeCliBackend adds --strict-mcp-config --mcp-config '{"mcpServers":{}}'
to prevent spawning MCP child processes. The PluginCliBackend (used by the baton)
has a mcp_config_flag defined in profiles but never used it.

Without MCP disabling: 4 musicians spawn ~80 child processes (MCP servers,
docker containers) instead of ~8. Potential deadlocks.

Fix: Add mcp_disable_args to CliCommand. When set, these args are injected
into _build_command() to disable MCP. Profile-driven, not hardcoded.

TDD: Tests define the contract. Implementation fulfills it.
"""

from pathlib import Path

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _make_profile(
    *,
    mcp_config_flag: str | None = None,
    mcp_disable_args: list[str] | None = None,
    extra_flags: list[str] | None = None,
) -> InstrumentProfile:
    """Create a profile with MCP configuration."""
    return InstrumentProfile(
        name="test-instrument",
        display_name="Test",
        kind="cli",
        models=[
            ModelCapacity(
                name="test-model",
                context_window=128000,
                cost_per_1k_input=0.01,
                cost_per_1k_output=0.03,
            ),
        ],
        cli=CliProfile(
            command=CliCommand(
                executable="test-cli",
                prompt_flag="-p",
                prompt_via_stdin=False,
                mcp_config_flag=mcp_config_flag,
                mcp_disable_args=mcp_disable_args or [],
                extra_flags=extra_flags or [],
            ),
            output=CliOutputConfig(format="text"),
        ),
    )


class TestMcpDisableArgs:
    """mcp_disable_args on CliCommand controls MCP disabling."""

    def test_mcp_disable_args_injected_into_command(self) -> None:
        """When mcp_disable_args is set, those args appear in the command."""
        profile = _make_profile(
            mcp_config_flag="--mcp-config",
            mcp_disable_args=[
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
            ],
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        assert "--strict-mcp-config" in cmd
        assert "--mcp-config" in cmd
        assert '{"mcpServers":{}}' in cmd

    def test_mcp_disable_args_empty_means_no_mcp_flags(self) -> None:
        """When mcp_disable_args is empty, no MCP flags are added."""
        profile = _make_profile(mcp_config_flag="--mcp-config")
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        assert "--strict-mcp-config" not in cmd
        assert "--mcp-config" not in cmd

    def test_mcp_disable_args_without_mcp_config_flag(self) -> None:
        """mcp_disable_args works even without mcp_config_flag (they're independent)."""
        profile = _make_profile(
            mcp_disable_args=["--no-mcp"],
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        assert "--no-mcp" in cmd

    def test_mcp_disable_args_before_extra_flags(self) -> None:
        """MCP disable args appear before extra_flags in command order."""
        profile = _make_profile(
            mcp_disable_args=["--no-mcp"],
            extra_flags=["--verbose"],
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        mcp_idx = cmd.index("--no-mcp")
        verbose_idx = cmd.index("--verbose")
        assert mcp_idx < verbose_idx, (
            "MCP disable args should come before extra_flags"
        )

    def test_mcp_disable_args_after_prompt(self) -> None:
        """MCP disable args appear after the prompt."""
        profile = _make_profile(
            mcp_disable_args=["--strict-mcp-config"],
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("test prompt", timeout_seconds=None)

        prompt_idx = cmd.index("test prompt")
        mcp_idx = cmd.index("--strict-mcp-config")
        assert mcp_idx > prompt_idx, (
            "MCP disable args should come after prompt"
        )

    def test_default_mcp_disable_args_is_empty(self) -> None:
        """mcp_disable_args defaults to empty list (backward compatible)."""
        cmd = CliCommand(executable="test")
        assert cmd.mcp_disable_args == []


class TestClaudeCodeProfileMcpDisable:
    """The claude-code built-in profile must have MCP disabling configured."""

    def test_claude_code_profile_has_mcp_disable_args(self) -> None:
        """claude-code profile defines mcp_disable_args for MCP isolation."""
        from marianne.instruments.loader import InstrumentProfileLoader

        profiles = InstrumentProfileLoader.load_directory(
            Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"
        )
        claude_profile = profiles.get("claude-code")
        assert claude_profile is not None
        assert claude_profile.cli is not None

        disable_args = claude_profile.cli.command.mcp_disable_args
        assert len(disable_args) >= 2, (
            "claude-code must have MCP disable args "
            "(at minimum --strict-mcp-config and --mcp-config with empty config)"
        )
        assert "--strict-mcp-config" in disable_args
        assert "--mcp-config" in disable_args
        assert '{"mcpServers":{}}' in disable_args

    def test_claude_code_mcp_parity_with_legacy(self) -> None:
        """PluginCliBackend with claude-code profile produces the same MCP
        args as the legacy ClaudeCliBackend."""
        from marianne.instruments.loader import InstrumentProfileLoader

        profiles = InstrumentProfileLoader.load_directory(
            Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"
        )
        claude_profile = profiles.get("claude-code")
        assert claude_profile is not None

        backend = PluginCliBackend(claude_profile)
        cmd = backend._build_command("test", timeout_seconds=None)

        # Legacy backend adds these exact args when disable_mcp=True
        assert "--strict-mcp-config" in cmd
        assert "--mcp-config" in cmd
        mcp_idx = cmd.index("--mcp-config")
        assert cmd[mcp_idx + 1] == '{"mcpServers":{}}'
