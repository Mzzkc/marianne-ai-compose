"""Tests for MCP config injection in PluginCliBackend."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _prof(
    *,
    executable: str = "test-inst",
    subcommand: str | None = None,
    mcp_flag: str | None = None,
    mcp_prefix: list[str] | None = None,
    mcp_dis: list[str] | None = None,
    mcp_workspace_path: str | None = None,
    mcp_workspace_merge_key: str | None = None,
    prompt_via_stdin: bool = False,
) -> InstrumentProfile:
    cmd = CliCommand(
        executable=executable,
        subcommand=subcommand,
        prompt_flag="-p",
        prompt_via_stdin=prompt_via_stdin,
        mcp_config_flag=mcp_flag,
        mcp_config_prefix_args=mcp_prefix or [],
        mcp_config_workspace_path=mcp_workspace_path,
        mcp_config_workspace_merge_key=mcp_workspace_merge_key,
        mcp_disable_args=mcp_dis or [],
    )
    return InstrumentProfile(
        name="test",
        display_name="Test",
        kind="cli",
        cli=CliProfile(command=cmd, output=CliOutputConfig(format="text")),
    )


class TestMcpConfigInjection:
    def test_workspace_mcp_config_path_must_stay_relative(self) -> None:
        with pytest.raises(ValueError, match="must be relative"):
            CliCommand(
                executable="test-inst",
                mcp_config_workspace_path="/tmp/mcp_config.json",
            )
        with pytest.raises(ValueError, match="must not contain"):
            CliCommand(
                executable="test-inst",
                mcp_config_workspace_path="../mcp_config.json",
            )

    def test_workspace_mcp_config_merge_key_must_be_simple(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            CliCommand(
                executable="test-inst",
                mcp_config_workspace_merge_key="",
            )
        with pytest.raises(ValueError, match="simple key"):
            CliCommand(
                executable="test-inst",
                mcp_config_workspace_merge_key="nested.mcpServers",
            )

    def test_mcp_config_flag_used_when_path_set(self) -> None:
        b = PluginCliBackend(
            _prof(
                mcp_flag="--mcp-config",
                mcp_prefix=["--strict-mcp-config"],
                mcp_dis=["--no-mcp"],
            )
        )
        b.set_mcp_config(Path("/tmp/mcp-config.json"))
        cmd = b._build_command("test", timeout_seconds=30)
        assert cmd[-3:] == ["--strict-mcp-config", "--mcp-config", "/tmp/mcp-config.json"]
        assert "--no-mcp" not in cmd

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

    def test_workspace_mcp_config_path_suppresses_disable_args(self) -> None:
        b = PluginCliBackend(
            _prof(
                mcp_workspace_path=".agents/mcp_config.json",
                mcp_dis=["--no-mcp"],
            )
        )
        b.set_mcp_config(Path("/tmp/c.json"))
        cmd = b._build_command("test", timeout_seconds=30)
        assert "--no-mcp" not in cmd
        assert ".agents/mcp_config.json" not in cmd

    @pytest.mark.asyncio
    async def test_workspace_mcp_config_materialized_and_restored(
        self,
        tmp_path: Path,
    ) -> None:
        source_config = tmp_path / "generated-mcp.json"
        source_config.write_text('{"mcpServers":{"shared":{}}}', encoding="utf-8")

        target = tmp_path / ".agents" / "mcp_config.json"
        target.parent.mkdir()
        target.write_text('{"mcpServers":{"original":{}}}', encoding="utf-8")

        probe = tmp_path / "probe.py"
        probe.write_text(
            "\n".join(
                [
                    "from pathlib import Path",
                    "cfg = Path.cwd() / '.agents' / 'mcp_config.json'",
                    "Path('seen-mcp-config.json').write_text(cfg.read_text())",
                    "print('MATERIALIZED_OK')",
                ]
            ),
            encoding="utf-8",
        )

        backend = PluginCliBackend(
            _prof(
                executable=sys.executable,
                subcommand=str(probe),
                mcp_workspace_path=".agents/mcp_config.json",
                prompt_via_stdin=True,
            ),
            working_directory=tmp_path,
        )
        backend.set_mcp_config(source_config)

        result = await backend.execute("prompt", timeout_seconds=5)

        assert result.success
        assert "MATERIALIZED_OK" in result.stdout
        assert (tmp_path / "seen-mcp-config.json").read_text(encoding="utf-8") == (
            source_config.read_text(encoding="utf-8")
        )
        assert target.read_text(encoding="utf-8") == '{"mcpServers":{"original":{}}}'

    @pytest.mark.asyncio
    async def test_workspace_mcp_config_merge_key_preserves_settings(
        self,
        tmp_path: Path,
    ) -> None:
        source_config = tmp_path / "generated-mcp.json"
        source_config.write_text(
            '{"mcpServers":{"shared":{"command":"proxy","args":["--socket","x"]}}}',
            encoding="utf-8",
        )

        target = tmp_path / ".gemini" / "settings.json"
        target.parent.mkdir()
        target.write_text(
            '{"ui":{"theme":"dark"},"mcpServers":{"old":{"command":"old"}}}',
            encoding="utf-8",
        )

        probe = tmp_path / "probe.py"
        probe.write_text(
            "\n".join(
                [
                    "import json",
                    "from pathlib import Path",
                    "cfg = json.loads((Path.cwd() / '.gemini' / 'settings.json').read_text())",
                    "Path('seen-mcp-config.json').write_text(json.dumps(cfg, sort_keys=True))",
                    "print('MERGED_OK')",
                ]
            ),
            encoding="utf-8",
        )

        backend = PluginCliBackend(
            _prof(
                executable=sys.executable,
                subcommand=str(probe),
                mcp_workspace_path=".gemini/settings.json",
                mcp_workspace_merge_key="mcpServers",
                prompt_via_stdin=True,
            ),
            working_directory=tmp_path,
        )
        backend.set_mcp_config(source_config)

        result = await backend.execute("prompt", timeout_seconds=5)

        assert result.success
        assert "MERGED_OK" in result.stdout
        seen = (tmp_path / "seen-mcp-config.json").read_text(encoding="utf-8")
        assert '"ui": {"theme": "dark"}' in seen
        assert '"shared": {"args": ["--socket", "x"], "command": "proxy"}' in seen
        assert '"old"' not in seen
        assert target.read_text(encoding="utf-8") == (
            '{"ui":{"theme":"dark"},"mcpServers":{"old":{"command":"old"}}}'
        )
