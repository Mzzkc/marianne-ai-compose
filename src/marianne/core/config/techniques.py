"""Technique system configuration models.

Techniques are composable components attached to agent entities — an ECS
pattern for AI agents. Each technique is independently reusable across
projects, agents, and scores.

Three kinds:
- skill: Text-based methodology (memory protocol, mateship, coordination)
- mcp: MCP server tools accessible via shared pool (github, filesystem)
- protocol: Communication protocols (A2A)

Each technique declares which phases of the agent cycle it's available in,
allowing the compiler to inject technique manifests per-phase.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TechniqueKind(str, Enum):
    """Kind of technique component.

    Maps to the ECS component taxonomy:
    - SKILL: Text-based methodology injected as cadenza context
    - MCP: MCP server tools accessible via the shared pool
    - PROTOCOL: Communication protocols (A2A, coordination)
    """

    SKILL = "skill"
    MCP = "mcp"
    PROTOCOL = "protocol"


class TechniqueConfig(BaseModel):
    """Configuration for a single technique attached to an agent.

    Techniques are composable: an agent can have multiple techniques of
    different kinds. The compiler's technique wirer reads these declarations
    and injects the appropriate manifests, MCP access, and protocol config
    into each phase's cadenza context.

    Example YAML::

        techniques:
          a2a:
            kind: protocol
            phases: [recon, plan, work, integration, inspect, aar]
          github:
            kind: mcp
            phases: [recon, work, integration]
            config:
              server: github
              transport: stdio
    """

    model_config = ConfigDict(extra="forbid")

    kind: TechniqueKind = Field(
        description="Technique kind: skill, mcp, or protocol",
    )
    phases: list[str] = Field(
        description="Phases where this technique is available. "
        "Empty list means the technique is declared but not active in any phase.",
    )
    required: bool = Field(
        default=False,
        description=(
            "Fail dispatch when an active skill technique document cannot be "
            "resolved. Modern persistent-agent specialist techniques should "
            "set this true; legacy declarations remain optional by default."
        ),
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Kind-specific configuration. "
        "For MCP: server name, transport. "
        "For skill: path to skill document. "
        "For protocol: protocol-specific settings.",
    )
