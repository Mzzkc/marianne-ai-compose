"""MCP bridge canary tests — end-to-end technique to config to command."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.core.config.techniques import TechniqueConfig, TechniqueKind
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _filt(techs: dict[str, TechniqueConfig], phase: str) -> dict[str, TechniqueConfig]:
    return (
        {n: t for n, t in techs.items() if phase in t.phases or "all" in t.phases} if techs else {}
    )


def _manifest(techs: dict[str, TechniqueConfig]) -> str:
    if not techs:
        return ""
    s = ["## Techniques Available This Phase"]
    mcp = {n: t for n, t in techs.items() if t.kind == TechniqueKind.MCP}
    if mcp:
        s.append("\n### MCP Tools")
        for n, t in mcp.items():
            s.append(f"- **{n}** (server: {t.config.get('server', n)})")
    proto = {n: t for n, t in techs.items() if t.kind == TechniqueKind.PROTOCOL}
    if proto:
        s.append("\n### Protocols")
        for n in proto:
            s.append(f"- **{n}**")
    skill = {n: t for n, t in techs.items() if t.kind == TechniqueKind.SKILL}
    if skill:
        s.append("\n### Skills")
        for n in skill:
            s.append(f"- **{n}**")
    return "\n".join(s)


@dataclass(frozen=True)
class _R:
    mcp_servers: dict[str, str] = field(default_factory=dict)
    manifest: str = ""


def _resolve(techs: dict[str, TechniqueConfig], phase: str) -> _R:
    f = _filt(techs, phase)
    if not f:
        return _R()
    return _R(
        mcp_servers={
            n: t.config.get("server", n) for n, t in f.items() if t.kind == TechniqueKind.MCP
        },
        manifest=_manifest(f),
    )


def _prof() -> InstrumentProfile:
    cmd = CliCommand(
        executable="claude",
        prompt_flag="-p",
        mcp_config_flag="--mcp-config",
        mcp_disable_args=["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
    )
    return InstrumentProfile(
        name="claude-code",
        display_name="Claude Code",
        kind="cli",
        cli=CliProfile(command=cmd, output=CliOutputConfig(format="text")),
    )


class TestMcpBridgeCanary:
    def test_technique_to_config_to_command(self, tmp_path: Path) -> None:
        techs = {"github": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["work"]})}
        r = _resolve(techs, "work")
        assert "github" in r.mcp_servers
        cp = tmp_path / ".mcp-pool-config.json"
        cp.write_text('{"mcpServers":{"github":{"command":"echo"}}}')
        b = PluginCliBackend(_prof())
        b.set_mcp_config(cp)
        cmd = b._build_command("work", timeout_seconds=300)
        assert "--mcp-config" in cmd and str(cp) in cmd and "--strict-mcp-config" not in cmd

    def test_no_mcp_techniques_no_config(self) -> None:
        r = _resolve(
            {"m": TechniqueConfig.model_validate({"kind": "skill", "phases": ["work"]})}, "work"
        )
        assert r.mcp_servers == {}
        cmd = PluginCliBackend(_prof())._build_command("work", timeout_seconds=300)
        assert "--strict-mcp-config" in cmd

    def test_manifest_includes_mcp_tools(self) -> None:
        t = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"], "config": {"server": "gh-mcp"}}
            )
        }
        m = _manifest(_filt(t, "work"))
        assert "MCP Tools" in m and "github" in m and "gh-mcp" in m

    def test_technique_phase_filtering(self) -> None:
        t = {
            "github": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["work"]}),
            "recon_t": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["recon"]}),
        }
        r = _resolve(t, "work")
        assert "github" in r.mcp_servers and "recon_t" not in r.mcp_servers

    def test_backward_compat_no_techniques(self) -> None:
        r = _resolve({}, "work")
        assert r.manifest == "" and r.mcp_servers == {}
        cmd = PluginCliBackend(_prof())._build_command("test", timeout_seconds=30)
        assert cmd[0] == "claude"
