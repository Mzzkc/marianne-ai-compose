"""Tests for infrastructure configuration models.

Phase A of the composition compiler build: validates new config models for
techniques, keyring, MCP pool, fleet, A2A events, pause_before_chain, and
agent cards.

TDD: Tests written first, models implemented to satisfy them.
"""

from __future__ import annotations

import time
from typing import Any

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings
from pydantic import ValidationError

# =============================================================================
# TechniqueConfig tests
# =============================================================================


class TestTechniqueKind:
    """TechniqueKind enum values match the technique system design."""

    def test_skill_value(self) -> None:
        from marianne.core.config.techniques import TechniqueKind

        assert TechniqueKind.SKILL == "skill"
        assert TechniqueKind.SKILL.value == "skill"

    def test_mcp_value(self) -> None:
        from marianne.core.config.techniques import TechniqueKind

        assert TechniqueKind.MCP == "mcp"
        assert TechniqueKind.MCP.value == "mcp"

    def test_protocol_value(self) -> None:
        from marianne.core.config.techniques import TechniqueKind

        assert TechniqueKind.PROTOCOL == "protocol"
        assert TechniqueKind.PROTOCOL.value == "protocol"

    def test_all_kinds_present(self) -> None:
        from marianne.core.config.techniques import TechniqueKind

        assert len(TechniqueKind) == 3


class TestTechniqueConfig:
    """TechniqueConfig — composable technique component for agents."""

    def test_valid_skill_technique(self) -> None:
        from marianne.core.config.techniques import TechniqueConfig, TechniqueKind

        tc = TechniqueConfig(
            kind=TechniqueKind.SKILL,
            phases=["recon", "plan", "work"],
        )
        assert tc.kind == TechniqueKind.SKILL
        assert tc.phases == ["recon", "plan", "work"]
        assert tc.config == {}

    def test_valid_mcp_technique_with_config(self) -> None:
        from marianne.core.config.techniques import TechniqueConfig, TechniqueKind

        tc = TechniqueConfig(
            kind=TechniqueKind.MCP,
            phases=["work", "inspect"],
            config={"server": "github", "transport": "stdio"},
        )
        assert tc.kind == TechniqueKind.MCP
        assert tc.config["server"] == "github"

    def test_valid_protocol_technique(self) -> None:
        from marianne.core.config.techniques import TechniqueConfig, TechniqueKind

        tc = TechniqueConfig(
            kind=TechniqueKind.PROTOCOL,
            phases=["recon", "plan", "work", "integration", "inspect", "aar"],
        )
        assert tc.kind == TechniqueKind.PROTOCOL
        assert len(tc.phases) == 6

    def test_rejects_extra_fields(self) -> None:
        from marianne.core.config.techniques import TechniqueConfig

        with pytest.raises(ValidationError, match="extra"):
            TechniqueConfig(
                kind="skill",
                phases=["work"],
                config={},
                unknown_field="bad",  # type: ignore[call-arg]
            )

    def test_requires_kind(self) -> None:
        from marianne.core.config.techniques import TechniqueConfig

        with pytest.raises(ValidationError):
            TechniqueConfig(phases=["work"])  # type: ignore[call-arg]

    def test_requires_phases(self) -> None:
        from marianne.core.config.techniques import TechniqueConfig

        with pytest.raises(ValidationError):
            TechniqueConfig(kind="skill")  # type: ignore[call-arg]

    def test_kind_from_string(self) -> None:
        from marianne.core.config.techniques import TechniqueConfig, TechniqueKind

        tc = TechniqueConfig(kind="mcp", phases=["work"])  # type: ignore[arg-type]
        assert tc.kind == TechniqueKind.MCP

    def test_invalid_kind_rejected(self) -> None:
        from marianne.core.config.techniques import TechniqueConfig

        with pytest.raises(ValidationError):
            TechniqueConfig(kind="invalid_kind", phases=["work"])  # type: ignore[arg-type]

    def test_empty_phases_allowed(self) -> None:
        """Empty phases list is valid — technique available in no phases by default."""
        from marianne.core.config.techniques import TechniqueConfig

        tc = TechniqueConfig(kind="skill", phases=[])  # type: ignore[arg-type]
        assert tc.phases == []


# =============================================================================
# KeyringConfig tests
# =============================================================================


class TestKeyEntry:
    """KeyEntry — a single API key reference."""

    def test_valid_key_entry(self) -> None:
        from marianne.daemon.keyring_config import KeyEntry

        ke = KeyEntry(path="$SECRETS_DIR/openrouter.key", label="primary")
        assert ke.path == "$SECRETS_DIR/openrouter.key"
        assert ke.label == "primary"

    def test_rejects_extra_fields(self) -> None:
        from marianne.daemon.keyring_config import KeyEntry

        with pytest.raises(ValidationError, match="extra"):
            KeyEntry(
                path="test.key",
                label="x",
                extra="bad",  # type: ignore[call-arg]
            )

    def test_requires_path(self) -> None:
        from marianne.daemon.keyring_config import KeyEntry

        with pytest.raises(ValidationError):
            KeyEntry(label="primary")  # type: ignore[call-arg]

    def test_requires_label(self) -> None:
        from marianne.daemon.keyring_config import KeyEntry

        with pytest.raises(ValidationError):
            KeyEntry(path="key.txt")  # type: ignore[call-arg]


class TestInstrumentKeyring:
    """InstrumentKeyring — keys for one instrument with rotation policy."""

    def test_valid_keyring(self) -> None:
        from marianne.daemon.keyring_config import InstrumentKeyring, KeyEntry

        ik = InstrumentKeyring(
            keys=[
                KeyEntry(path="key1.txt", label="primary"),
                KeyEntry(path="key2.txt", label="secondary"),
            ],
        )
        assert len(ik.keys) == 2
        assert ik.rotation == "least-recently-rate-limited"

    def test_custom_rotation(self) -> None:
        from marianne.daemon.keyring_config import InstrumentKeyring, KeyEntry

        ik = InstrumentKeyring(
            keys=[KeyEntry(path="k.txt", label="only")],
            rotation="round-robin",
        )
        assert ik.rotation == "round-robin"


class TestKeyringConfig:
    """KeyringConfig — top-level keyring for all instruments."""

    def test_valid_keyring_config(self) -> None:
        from marianne.daemon.keyring_config import (
            InstrumentKeyring,
            KeyEntry,
            KeyringConfig,
        )

        kc = KeyringConfig(
            instruments={
                "openrouter": InstrumentKeyring(
                    keys=[KeyEntry(path="or.key", label="main")],
                ),
                "anthropic": InstrumentKeyring(
                    keys=[KeyEntry(path="anth.key", label="main")],
                ),
            }
        )
        assert "openrouter" in kc.instruments
        assert "anthropic" in kc.instruments

    def test_empty_keyring_valid(self) -> None:
        from marianne.daemon.keyring_config import KeyringConfig

        kc = KeyringConfig()
        assert kc.instruments == {}

    def test_rejects_extra_fields(self) -> None:
        from marianne.daemon.keyring_config import KeyringConfig

        with pytest.raises(ValidationError, match="extra"):
            KeyringConfig(
                instruments={},
                unknown="bad",  # type: ignore[call-arg]
            )


# =============================================================================
# McpPoolConfig tests
# =============================================================================


class TestMcpServerEntry:
    """McpServerEntry — a single MCP server in the shared pool."""

    def test_valid_entry(self) -> None:
        from marianne.daemon.config import McpServerEntry

        entry = McpServerEntry(
            command="github-mcp-server",
            socket="/tmp/mzt/mcp/github.sock",
        )
        assert entry.command == "github-mcp-server"
        assert entry.transport == "stdio"
        assert entry.restart_policy == "on-failure"

    def test_custom_transport(self) -> None:
        from marianne.daemon.config import McpServerEntry

        entry = McpServerEntry(
            command="custom-server",
            transport="sse",
            socket="/tmp/custom.sock",
        )
        assert entry.transport == "sse"

    def test_rejects_extra_fields(self) -> None:
        from marianne.daemon.config import McpServerEntry

        with pytest.raises(ValidationError, match="extra"):
            McpServerEntry(
                command="srv",
                socket="/s.sock",
                extra="bad",  # type: ignore[call-arg]
            )


class TestMcpPoolConfig:
    """McpPoolConfig — shared MCP server pool in DaemonConfig."""

    def test_valid_pool(self) -> None:
        from marianne.daemon.config import McpPoolConfig, McpServerEntry

        pool = McpPoolConfig(
            servers={
                "github": McpServerEntry(
                    command="github-mcp-server",
                    socket="/tmp/mzt/mcp/github.sock",
                ),
                "filesystem": McpServerEntry(
                    command="fs-mcp-server",
                    socket="/tmp/mzt/mcp/fs.sock",
                ),
            }
        )
        assert len(pool.servers) == 2

    def test_empty_pool_valid(self) -> None:
        from marianne.daemon.config import McpPoolConfig

        pool = McpPoolConfig()
        assert pool.servers == {}

    def test_daemon_config_has_mcp_pool(self) -> None:
        """DaemonConfig should accept mcp_pool as an optional field."""
        from marianne.daemon.config import DaemonConfig

        dc = DaemonConfig()
        assert dc.mcp_pool is not None  # default_factory
        assert dc.mcp_pool.servers == {}

    def test_daemon_config_with_mcp_pool(self) -> None:
        from marianne.daemon.config import DaemonConfig, McpPoolConfig, McpServerEntry

        dc = DaemonConfig(
            mcp_pool=McpPoolConfig(
                servers={
                    "github": McpServerEntry(
                        command="gh-mcp",
                        socket="/tmp/mzt/mcp/gh.sock",
                    ),
                }
            )
        )
        assert "github" in dc.mcp_pool.servers


# =============================================================================
# FleetConfig tests
# =============================================================================


class TestFleetScoreEntry:
    """FleetScoreEntry — one score in a fleet."""

    def test_valid_entry(self) -> None:
        from marianne.core.config.fleet import FleetScoreEntry

        entry = FleetScoreEntry(
            path="scores/agents/canyon.yaml",
            group="architects",
        )
        assert entry.path == "scores/agents/canyon.yaml"
        assert entry.group == "architects"

    def test_group_optional(self) -> None:
        from marianne.core.config.fleet import FleetScoreEntry

        entry = FleetScoreEntry(path="scores/test.yaml")
        assert entry.group is None


class TestFleetGroupConfig:
    """FleetGroupConfig — group dependency declarations."""

    def test_empty_depends_on(self) -> None:
        from marianne.core.config.fleet import FleetGroupConfig

        fg = FleetGroupConfig()
        assert fg.depends_on == []

    def test_with_dependencies(self) -> None:
        from marianne.core.config.fleet import FleetGroupConfig

        fg = FleetGroupConfig(depends_on=["architects", "planners"])
        assert fg.depends_on == ["architects", "planners"]


class TestFleetConfig:
    """FleetConfig — concert-of-concerts fleet management."""

    def test_valid_fleet(self) -> None:
        from marianne.core.config.fleet import (
            FleetConfig,
            FleetGroupConfig,
            FleetScoreEntry,
        )

        fc = FleetConfig(
            name="marianne-dev-fleet",
            scores=[
                FleetScoreEntry(path="scores/canyon.yaml", group="architects"),
                FleetScoreEntry(path="scores/forge.yaml", group="builders"),
            ],
            groups={
                "architects": FleetGroupConfig(depends_on=[]),
                "builders": FleetGroupConfig(depends_on=["architects"]),
            },
        )
        assert fc.name == "marianne-dev-fleet"
        assert fc.type == "fleet"
        assert len(fc.scores) == 2

    def test_type_defaults_to_fleet(self) -> None:
        from marianne.core.config.fleet import FleetConfig, FleetScoreEntry

        fc = FleetConfig(
            name="test",
            scores=[FleetScoreEntry(path="s.yaml")],
        )
        assert fc.type == "fleet"

    def test_rejects_non_fleet_type(self) -> None:
        from marianne.core.config.fleet import FleetConfig, FleetScoreEntry

        with pytest.raises(ValidationError):
            FleetConfig(
                name="test",
                type="score",  # type: ignore[arg-type]
                scores=[FleetScoreEntry(path="s.yaml")],
            )

    def test_requires_name(self) -> None:
        from marianne.core.config.fleet import FleetConfig, FleetScoreEntry

        with pytest.raises(ValidationError):
            FleetConfig(scores=[FleetScoreEntry(path="s.yaml")])  # type: ignore[call-arg]

    def test_requires_scores(self) -> None:
        from marianne.core.config.fleet import FleetConfig

        with pytest.raises(ValidationError):
            FleetConfig(name="test")  # type: ignore[call-arg]

    def test_rejects_extra_fields(self) -> None:
        from marianne.core.config.fleet import FleetConfig, FleetScoreEntry

        with pytest.raises(ValidationError, match="extra"):
            FleetConfig(
                name="test",
                scores=[FleetScoreEntry(path="s.yaml")],
                unknown="bad",  # type: ignore[call-arg]
            )

    def test_validates_group_dependencies(self) -> None:
        """FleetConfig validates that group dependencies reference defined groups."""
        from marianne.core.config.fleet import (
            FleetConfig,
            FleetGroupConfig,
            FleetScoreEntry,
        )

        with pytest.raises(ValidationError, match="undefined group"):
            FleetConfig(
                name="test",
                scores=[FleetScoreEntry(path="s.yaml", group="a")],
                groups={
                    "a": FleetGroupConfig(depends_on=["nonexistent"]),
                },
            )

    def test_validates_no_circular_dependencies(self) -> None:
        """FleetConfig detects circular group dependencies."""
        from marianne.core.config.fleet import (
            FleetConfig,
            FleetGroupConfig,
            FleetScoreEntry,
        )

        with pytest.raises(ValidationError, match="[Cc]ircular"):
            FleetConfig(
                name="test",
                scores=[
                    FleetScoreEntry(path="a.yaml", group="a"),
                    FleetScoreEntry(path="b.yaml", group="b"),
                ],
                groups={
                    "a": FleetGroupConfig(depends_on=["b"]),
                    "b": FleetGroupConfig(depends_on=["a"]),
                },
            )


# =============================================================================
# A2A Event Type tests
# =============================================================================


class TestA2AEvents:
    """A2A event frozen dataclasses following existing baton event patterns."""

    def test_task_submitted_immutable(self) -> None:
        from marianne.daemon.baton.events import A2ATaskSubmitted

        evt = A2ATaskSubmitted(
            job_id="j1",
            sheet_num=3,
            target_agent="sentinel",
            task_description="Review security",
        )
        assert evt.job_id == "j1"
        assert evt.target_agent == "sentinel"
        assert evt.context == {}

        with pytest.raises(AttributeError):
            evt.job_id = "j2"  # type: ignore[misc]

    def test_task_submitted_with_context(self) -> None:
        from marianne.daemon.baton.events import A2ATaskSubmitted

        evt = A2ATaskSubmitted(
            job_id="j1",
            sheet_num=3,
            target_agent="sentinel",
            task_description="Review",
            context={"priority": "high"},
        )
        assert evt.context == {"priority": "high"}

    def test_task_routed_immutable(self) -> None:
        from marianne.daemon.baton.events import A2ATaskRouted

        evt = A2ATaskRouted(
            job_id="j1",
            sheet_num=3,
            source_agent="canyon",
            target_agent="sentinel",
            task_id="task-123",
        )
        assert evt.source_agent == "canyon"
        assert evt.task_id == "task-123"

        with pytest.raises(AttributeError):
            evt.task_id = "other"  # type: ignore[misc]

    def test_task_completed_immutable(self) -> None:
        from marianne.daemon.baton.events import A2ATaskCompleted

        evt = A2ATaskCompleted(
            job_id="j1",
            sheet_num=5,
            task_id="task-123",
            artifacts={"report": "findings.md"},
        )
        assert evt.artifacts == {"report": "findings.md"}

        with pytest.raises(AttributeError):
            evt.artifacts = {}  # type: ignore[misc]

    def test_task_failed_immutable(self) -> None:
        from marianne.daemon.baton.events import A2ATaskFailed

        evt = A2ATaskFailed(
            job_id="j1",
            sheet_num=5,
            task_id="task-123",
            reason="Agent not available",
        )
        assert evt.reason == "Agent not available"

        with pytest.raises(AttributeError):
            evt.reason = "changed"  # type: ignore[misc]

    def test_a2a_events_have_timestamp(self) -> None:
        from marianne.daemon.baton.events import (
            A2ATaskCompleted,
            A2ATaskFailed,
            A2ATaskRouted,
            A2ATaskSubmitted,
        )

        before = time.time()
        submitted = A2ATaskSubmitted(
            job_id="j1",
            sheet_num=1,
            target_agent="x",
            task_description="t",
        )
        routed = A2ATaskRouted(
            job_id="j1",
            sheet_num=1,
            source_agent="a",
            target_agent="b",
            task_id="t1",
        )
        completed = A2ATaskCompleted(
            job_id="j1",
            sheet_num=1,
            task_id="t1",
            artifacts={},
        )
        failed = A2ATaskFailed(
            job_id="j1",
            sheet_num=1,
            task_id="t1",
            reason="err",
        )
        after = time.time()

        for evt in [submitted, routed, completed, failed]:
            assert before <= evt.timestamp <= after

    def test_a2a_events_in_baton_event_union(self) -> None:
        """A2A events must be part of the BatonEvent union type."""
        from marianne.daemon.baton import events as ev

        # The BatonEvent type alias should include A2A types
        # We verify by checking to_observer_event handles them
        submitted = ev.A2ATaskSubmitted(
            job_id="j1",
            sheet_num=1,
            target_agent="x",
            task_description="t",
        )
        result = ev.to_observer_event(submitted)
        assert result["event"].startswith("baton.a2a.")

    def test_a2a_observer_event_conversion(self) -> None:
        """All A2A events convert to ObserverEvent format correctly."""
        from marianne.daemon.baton import events as ev

        submitted = ev.A2ATaskSubmitted(
            job_id="j1",
            sheet_num=1,
            target_agent="x",
            task_description="t",
        )
        obs = ev.to_observer_event(submitted)
        assert obs["job_id"] == "j1"
        assert obs["sheet_num"] == 1
        assert "a2a" in obs["event"]

        routed = ev.A2ATaskRouted(
            job_id="j2",
            sheet_num=2,
            source_agent="a",
            target_agent="b",
            task_id="t1",
        )
        obs = ev.to_observer_event(routed)
        assert obs["job_id"] == "j2"

        completed = ev.A2ATaskCompleted(
            job_id="j3",
            sheet_num=3,
            task_id="t1",
            artifacts={"f": "v"},
        )
        obs = ev.to_observer_event(completed)
        assert obs["data"]["task_id"] == "t1"

        failed = ev.A2ATaskFailed(
            job_id="j4",
            sheet_num=4,
            task_id="t1",
            reason="nope",
        )
        obs = ev.to_observer_event(failed)
        assert obs["data"]["reason"] == "nope"


# =============================================================================
# pause_before_chain tests
# =============================================================================


class TestPauseBeforeChain:
    """PostSuccessHookConfig gains pause_before_chain field."""

    def test_default_false(self) -> None:
        from marianne.core.config.orchestration import PostSuccessHookConfig

        hook = PostSuccessHookConfig(
            type="run_job",
            job_path="/tmp/next.yaml",
        )
        assert hook.pause_before_chain is False

    def test_set_true(self) -> None:
        from marianne.core.config.orchestration import PostSuccessHookConfig

        hook = PostSuccessHookConfig(
            type="run_job",
            job_path="/tmp/next.yaml",
            pause_before_chain=True,
        )
        assert hook.pause_before_chain is True


# =============================================================================
# AgentCard / A2A config tests
# =============================================================================


class TestA2ASkill:
    """A2ASkill — skill declaration for agent cards."""

    def test_valid_skill(self) -> None:
        from marianne.core.config.a2a import A2ASkill

        skill = A2ASkill(
            id="architecture-review",
            description="Review system architecture for structural integrity",
        )
        assert skill.id == "architecture-review"
        assert "structural" in skill.description

    def test_requires_id(self) -> None:
        from marianne.core.config.a2a import A2ASkill

        with pytest.raises(ValidationError):
            A2ASkill(description="test")  # type: ignore[call-arg]

    def test_requires_description(self) -> None:
        from marianne.core.config.a2a import A2ASkill

        with pytest.raises(ValidationError):
            A2ASkill(id="test")  # type: ignore[call-arg]


class TestAgentCard:
    """AgentCard — agent identity for A2A discovery."""

    def test_valid_agent_card(self) -> None:
        from marianne.core.config.a2a import A2ASkill, AgentCard

        card = AgentCard(
            name="canyon",
            description="Systems architect — traces boundaries",
            skills=[
                A2ASkill(
                    id="architecture-review",
                    description="Review system architecture",
                ),
                A2ASkill(
                    id="boundary-analysis",
                    description="Trace and analyze system boundaries",
                ),
            ],
        )
        assert card.name == "canyon"
        assert len(card.skills) == 2

    def test_empty_skills_valid(self) -> None:
        from marianne.core.config.a2a import AgentCard

        card = AgentCard(
            name="test",
            description="Test agent",
        )
        assert card.skills == []

    def test_requires_name(self) -> None:
        from marianne.core.config.a2a import AgentCard

        with pytest.raises(ValidationError):
            AgentCard(description="test")  # type: ignore[call-arg]

    def test_requires_description(self) -> None:
        from marianne.core.config.a2a import AgentCard

        with pytest.raises(ValidationError):
            AgentCard(name="test")  # type: ignore[call-arg]

    def test_rejects_extra_fields(self) -> None:
        from marianne.core.config.a2a import AgentCard

        with pytest.raises(ValidationError, match="extra"):
            AgentCard(
                name="test",
                description="test",
                unknown="bad",  # type: ignore[call-arg]
            )


# =============================================================================
# OpenRouter backend tests
# =============================================================================


class TestOpenRouterBackend:
    """OpenRouterBackend — HTTP backend for OpenRouter API."""

    def test_backend_importable(self) -> None:
        from marianne.execution.instruments.openai_compat_backend import OpenRouterBackend

        assert OpenRouterBackend is not None

    def test_from_config(self) -> None:
        from marianne.execution.instruments.openai_compat_backend import OpenRouterBackend
        from marianne.core.config import BackendConfig

        # BackendConfig.type doesn't include "openrouter" yet, but from_config
        # only reads model/timeout_seconds which are type-agnostic fields.
        config = BackendConfig(
            model="minimax/minimax-2.5",
            timeout_seconds=120.0,
        )
        backend = OpenRouterBackend.from_config(config)
        assert backend.model == "minimax/minimax-2.5"
        assert backend.timeout_seconds == 120.0

    def test_default_model(self) -> None:
        from marianne.execution.instruments.openai_compat_backend import OpenRouterBackend

        backend = OpenRouterBackend()
        assert backend.model == "minimax/minimax-m1-80k"


# =============================================================================
# Sandbox wrapper tests
# =============================================================================


class TestSandboxConfig:
    """SandboxConfig — bwrap sandbox configuration."""

    def test_config_importable(self) -> None:
        from marianne.execution.sandbox import SandboxConfig

        assert SandboxConfig is not None

    def test_default_config(self) -> None:
        from marianne.execution.sandbox import SandboxConfig

        config = SandboxConfig(workspace="/tmp/test-workspace")
        assert config.workspace == "/tmp/test-workspace"
        assert config.network_isolated is True
        assert config.memory_limit_mb is None

    def test_custom_config(self) -> None:
        from marianne.execution.sandbox import SandboxConfig

        config = SandboxConfig(
            workspace="/tmp/ws",
            network_isolated=False,
            memory_limit_mb=1024,
            bind_mounts=["/tmp/mzt/mcp/github.sock"],
        )
        assert config.network_isolated is False
        assert config.memory_limit_mb == 1024
        assert len(config.bind_mounts) == 1


class TestSandboxWrapper:
    """SandboxWrapper — bwrap execution wrapper."""

    def test_wrapper_importable(self) -> None:
        from marianne.execution.sandbox import SandboxWrapper

        assert SandboxWrapper is not None

    def test_build_command(self) -> None:
        from marianne.execution.sandbox import SandboxConfig, SandboxWrapper

        config = SandboxConfig(workspace="/tmp/ws")
        wrapper = SandboxWrapper(config)
        cmd = wrapper.build_command(["python", "-c", "print('hi')"])
        # Should produce a bwrap command
        assert cmd[0] == "bwrap"
        assert "python" in cmd


# =============================================================================
# OpenCode instrument profile tests
# =============================================================================


class TestOpenCodeProfile:
    """OpenCode instrument profile exists and is valid YAML."""

    def test_profile_file_exists(self) -> None:
        from pathlib import Path

        profile_path = Path(
            "/home/emzi/Projects/marianne-ai-compose/"
            "src/marianne/instruments/builtins/opencode.yaml"
        )
        assert profile_path.exists(), f"OpenCode profile not found at {profile_path}"

    def test_profile_validates(self) -> None:
        """OpenCode profile loads and validates as InstrumentProfile."""
        from pathlib import Path

        import yaml

        from marianne.core.config.instruments import InstrumentProfile

        profile_path = Path(
            "/home/emzi/Projects/marianne-ai-compose/"
            "src/marianne/instruments/builtins/opencode.yaml"
        )
        with open(profile_path) as f:
            data = yaml.safe_load(f)

        profile = InstrumentProfile.model_validate(data)
        assert profile.name == "opencode"
        assert profile.kind == "cli"
        assert "mcp" in profile.capabilities


# =============================================================================
# Property-based tests (hypothesis @given) — required by quality gate
# =============================================================================

_short_text = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(categories=("L", "N", "P")),
)
_phase_list = st.lists(
    st.sampled_from(
        [
            "recon",
            "plan",
            "work",
            "integration",
            "inspect",
            "aar",
            "consolidate",
            "reflect",
            "resurrect",
            "play",
        ]
    ),
    min_size=0,
    max_size=6,
)


def _technique_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries(
        {
            "kind": st.sampled_from(["skill", "mcp", "protocol"]),
            "phases": _phase_list,
            "config": st.just({}),
        }
    )


def _a2a_skill_strategy() -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries(
        {
            "id": _short_text,
            "description": _short_text,
        }
    )


def _agent_card_strategy() -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries(
        {
            "name": _short_text,
            "description": _short_text,
            "skills": st.lists(_a2a_skill_strategy(), min_size=0, max_size=5),
        }
    )


def _fleet_score_entry_strategy() -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries(
        {
            "path": _short_text,
        },
        optional={
            "group": _short_text,
        },
    )


def _fleet_group_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries(
        {
            "depends_on": st.just([]),
        }
    )


def _fleet_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries(
        {
            "name": _short_text,
            "type": st.just("fleet"),
            "scores": st.lists(_fleet_score_entry_strategy(), min_size=1, max_size=5),
            "groups": st.just({}),
        }
    )


class TestTechniqueConfigPropertyBased:
    """Property-based tests for TechniqueConfig."""

    @pytest.mark.property_based
    @given(data=_technique_config_strategy())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_technique_config_round_trip(self, data: dict[str, Any]) -> None:
        """TechniqueConfig round-trips through model_validate."""
        from marianne.core.config.techniques import TechniqueConfig

        tc = TechniqueConfig.model_validate(data)
        assert tc.kind.value == data["kind"]
        assert tc.phases == data["phases"]
        # Re-serialize and validate again
        dumped = tc.model_dump()
        TechniqueConfig.model_validate(dumped)


class TestA2ASkillPropertyBased:
    """Property-based tests for A2ASkill."""

    @pytest.mark.property_based
    @given(data=_a2a_skill_strategy())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_a2a_skill_round_trip(self, data: dict[str, Any]) -> None:
        """A2ASkill round-trips through model_validate."""
        from marianne.core.config.a2a import A2ASkill

        skill = A2ASkill.model_validate(data)
        assert skill.id == data["id"]
        assert skill.description == data["description"]
        dumped = skill.model_dump()
        A2ASkill.model_validate(dumped)


class TestAgentCardPropertyBased:
    """Property-based tests for AgentCard."""

    @pytest.mark.property_based
    @given(data=_agent_card_strategy())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_agent_card_round_trip(self, data: dict[str, Any]) -> None:
        """AgentCard round-trips through model_validate."""
        from marianne.core.config.a2a import AgentCard

        card = AgentCard.model_validate(data)
        assert card.name == data["name"]
        assert len(card.skills) == len(data["skills"])
        dumped = card.model_dump()
        AgentCard.model_validate(dumped)


class TestFleetScoreEntryPropertyBased:
    """Property-based tests for FleetScoreEntry."""

    @pytest.mark.property_based
    @given(data=_fleet_score_entry_strategy())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_fleet_score_entry_round_trip(self, data: dict[str, Any]) -> None:
        """FleetScoreEntry round-trips through model_validate."""
        from marianne.core.config.fleet import FleetScoreEntry

        entry = FleetScoreEntry.model_validate(data)
        assert entry.path == data["path"]
        dumped = entry.model_dump()
        FleetScoreEntry.model_validate(dumped)


class TestFleetGroupConfigPropertyBased:
    """Property-based tests for FleetGroupConfig."""

    @pytest.mark.property_based
    @given(data=_fleet_group_config_strategy())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_fleet_group_config_round_trip(self, data: dict[str, Any]) -> None:
        """FleetGroupConfig round-trips through model_validate."""
        from marianne.core.config.fleet import FleetGroupConfig

        group = FleetGroupConfig.model_validate(data)
        assert group.depends_on == data["depends_on"]
        dumped = group.model_dump()
        FleetGroupConfig.model_validate(dumped)


class TestFleetConfigPropertyBased:
    """Property-based tests for FleetConfig."""

    @pytest.mark.property_based
    @given(data=_fleet_config_strategy())
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_fleet_config_round_trip(self, data: dict[str, Any]) -> None:
        """FleetConfig round-trips through model_validate."""
        from marianne.core.config.fleet import FleetConfig

        fc = FleetConfig.model_validate(data)
        assert fc.name == data["name"]
        assert fc.type == "fleet"
        dumped = fc.model_dump()
        FleetConfig.model_validate(dumped)
