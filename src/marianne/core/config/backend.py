"""Bridge configuration models.

MCP bridge configuration (MCPServerConfig, BridgeConfig) used by the
``bridge:`` job-config field and the marianne.bridge package.

The legacy execution-backend models (BackendConfig, SheetBackendOverride,
OllamaConfig, RecursiveLightConfig) were removed when the ``backend:``
score syntax was stripped — execution is configured exclusively through
the instrument plugin system (#347).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MCPServerConfig(BaseModel):
    """Configuration for an MCP server to connect to.

    MCP servers provide tools that can be used by the Ollama bridge.
    Each server is spawned as a subprocess and communicates via stdio.

    Example YAML:
        bridge:
          mcp_servers:
            - name: filesystem
              command: "npx"
              args: ["-y", "@anthropic/mcp-server-filesystem", "/home/user"]
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="Unique name for this MCP server",
    )
    command: str = Field(
        description="Command to run the MCP server",
    )
    args: list[str] = Field(
        default_factory=list,
        description="Command line arguments",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables for the server",
    )

    # Security-sensitive env vars that should never be overridden via config.
    # These could alter program loading, credential resolution, or library paths.
    _BLOCKED_ENV_KEYS: frozenset[str] = frozenset({
        "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH", "PYTHONPATH", "NODE_PATH",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    })

    @model_validator(mode="after")
    def _validate_env_keys(self) -> MCPServerConfig:
        """Reject security-sensitive environment variable overrides."""
        for key in self.env:
            if key.upper() in self._BLOCKED_ENV_KEYS:
                raise ValueError(
                    f"MCP server env cannot override security-sensitive variable: {key}"
                )
        return self

    working_dir: str | None = Field(
        default=None,
        description="Working directory for the server",
    )
    timeout_seconds: float = Field(
        default=30.0,
        description="Timeout for server operations",
    )


class BridgeConfig(BaseModel):
    """Configuration for the Marianne-Ollama bridge.

    The bridge enables Ollama models to use MCP tools through a proxy service.
    It provides context optimization and optional hybrid routing to Claude.

    Example YAML:
        bridge:
          enabled: true
          mcp_proxy_enabled: true
          mcp_servers:
            - name: filesystem
              command: "npx"
              args: ["-y", "@anthropic/mcp-server-filesystem", "/home/user"]
          hybrid_routing_enabled: true
          complexity_threshold: 0.7
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable bridge mode (Ollama with MCP tools)",
    )

    # MCP Proxy settings
    mcp_proxy_enabled: bool = Field(
        default=True,
        description="Enable MCP server proxy for tool access",
    )
    mcp_servers: list[MCPServerConfig] = Field(
        default_factory=list,
        description="MCP servers to connect to",
    )

    # Hybrid routing
    hybrid_routing_enabled: bool = Field(
        default=False,
        description="Enable hybrid routing between Ollama and Claude",
    )
    complexity_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Complexity threshold for routing to Claude (0.0-1.0)",
    )
    fallback_to_claude: bool = Field(
        default=True,
        description="Fall back to Claude if Ollama execution fails",
    )

    # Context budget
    context_budget_percent: int = Field(
        default=75,
        ge=10,
        le=95,
        description="Percent of context window to use for tools (rest for conversation)",
    )


