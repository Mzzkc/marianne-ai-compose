"""Backend configuration models.

Defines configuration for execution backends: Claude CLI, Anthropic API,
Recursive Light, and Ollama.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.fields import PydanticUndefined  # type: ignore[attr-defined]


class RecursiveLightConfig(BaseModel):
    """Configuration for Recursive Light HTTP API backend (Phase 3).

    Enables TDF-aligned processing through the Recursive Light Framework
    with dual-LLM confidence scoring and domain activations.
    """

    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(
        default="http://localhost:8080",
        description="Base URL for the Recursive Light API server",
    )
    user_id: str | None = Field(
        default=None,
        description="Unique identifier for this Marianne instance (generates UUID if not set)",
    )
    timeout: float = Field(
        default=30.0,
        gt=0,
        description="Request timeout in seconds for RL API calls",
    )


class OllamaConfig(BaseModel):
    """Configuration for Ollama backend.

    Enables local model execution via Ollama with MCP tool support.
    Critical: num_ctx must be >= 32768 for Claude Code tool compatibility.

    Example YAML:
        backend:
          type: ollama
          ollama:
            base_url: "http://localhost:11434"
            model: "llama3.1:8b"
            num_ctx: 32768
    """

    model_config = ConfigDict(extra="forbid")

    # Connection settings
    base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama server base URL",
    )
    model: str = Field(
        default="llama3.1:8b",
        description="Ollama model to use. Must support tool calling.",
    )

    # Context optimization (CRITICAL for Claude Code tools)
    num_ctx: int = Field(
        default=32768,
        ge=4096,
        description="Context window size. Minimum 32K recommended for Claude Code tools.",
    )
    dynamic_tools: bool = Field(
        default=True,
        description="Enable dynamic toolset loading to optimize context",
    )
    compression_level: Literal["minimal", "moderate", "aggressive"] = Field(
        default="moderate",
        description="Tool schema compression level",
    )

    # Performance tuning
    timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Request timeout for Ollama API calls",
    )
    keep_alive: str = Field(
        default="5m",
        description="Keep model loaded in memory for this duration",
    )
    max_tool_iterations: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum tool call iterations per execution",
    )

    # Health check
    health_check_timeout: float = Field(
        default=10.0,
        description="Timeout for health check requests",
    )


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


class SheetBackendOverride(BaseModel):
    """Per-sheet backend parameter overrides.

    Allows individual sheets to use different models, temperatures,
    or timeouts without changing the global backend config.

    Example YAML::

        backend:
          type: anthropic_api
          model: claude-sonnet-4-20250514
          sheet_overrides:
            1:
              model: claude-opus-4-6
              temperature: 0.0
            5:
              timeout_seconds: 600
    """

    model_config = ConfigDict(extra="forbid")

    # CLI-specific overrides
    cli_model: str | None = Field(
        default=None,
        description="[claude_cli] Override model for this sheet",
    )

    # API-specific overrides
    model: str | None = Field(
        default=None,
        description="[anthropic_api] Override model for this sheet",
    )
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="[anthropic_api] Override sampling temperature for this sheet",
    )
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        description="[anthropic_api] Override max_tokens for this sheet",
    )

    # General overrides
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Override timeout for this sheet",
    )


class BackendConfig(BaseModel):
    """Configuration for the execution backend.

    Uses a flat structure with cross-field validation to ensure type-specific
    fields are only meaningful when the corresponding backend type is selected.
    The ``_validate_type_specific_fields`` validator warns when fields for an
    unselected backend are set to non-default values.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(
        default="claude_cli",
        description=(
            "Backend/instrument type name. The four legacy values "
            "'claude_cli', 'anthropic_api', 'recursive_light', and 'ollama' "
            "continue to be resolved via register_native_instruments(). "
            "Any other value is treated as an instrument name — an "
            "InstrumentProfile YAML must be registered for it, otherwise "
            "the registry will raise at resolution time. Phase 2 of the "
            "backend atlas migration opened this field from a closed Literal "
            "to str to permit instrument-name values on the legacy backend: "
            "path while the instrument: path completes rollout."
        ),
    )

    # CLI-specific options (only meaningful when type="claude_cli")
    skip_permissions: bool = Field(
        default=True,
        description="[claude_cli] Skip permission prompts for unattended execution. "
        "Maps to --dangerously-skip-permissions flag.",
    )
    disable_mcp: bool = Field(
        default=True,
        description="[claude_cli] Disable MCP server loading for faster, isolated execution. "
        "Provides ~2x speedup and prevents resource contention errors. "
        "Maps to --strict-mcp-config {} flag. Set to False to use MCP servers.",
    )
    output_format: Literal["json", "text", "stream-json"] = Field(
        default="text",
        description="[claude_cli] Claude CLI output format. "
        "'text' for human-readable real-time output (default), "
        "'json' for structured automation output, "
        "'stream-json' for real-time streaming events.",
    )
    cli_model: str | None = Field(
        default=None,
        description="[claude_cli] Model for Claude CLI execution. "
        "Maps to --model flag. If None, uses Claude Code's default model. "
        "Example: 'claude-sonnet-4-20250514'",
    )
    allowed_tools: list[str] | None = Field(
        default=None,
        description="[claude_cli] Restrict Claude to specific tools. "
        "Maps to --allowedTools flag. If None, all tools are available. "
        "Example: ['Read', 'Grep', 'Glob'] for read-only execution.",
    )
    system_prompt_file: Path | None = Field(
        default=None,
        description="[claude_cli] Path to custom system prompt file. "
        "Maps to --system-prompt flag. Overrides Claude's default system prompt.",
    )
    working_directory: Path | None = Field(
        default=None,
        description="Working directory for execution. "
        "If None, uses the directory containing the Mzt config file.",
    )
    timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        description="Maximum time allowed per prompt execution (seconds). Default: 30 minutes.",
    )
    timeout_overrides: dict[int, float] = Field(
        default_factory=dict,
        description="Per-sheet timeout overrides. Map of sheet_num -> timeout in seconds. "
        "Sheets not listed use the global timeout_seconds.",
    )
    sheet_overrides: dict[int, SheetBackendOverride] = Field(
        default_factory=dict,
        description="Per-sheet backend parameter overrides. Map of sheet_num -> override. "
        "Allows individual sheets to use different models, temperatures, etc. "
        "Timeout in sheet_overrides takes precedence over timeout_overrides.",
    )
    cli_extra_args: list[str] = Field(
        default_factory=list,
        description="[claude_cli] Escape hatch for CLI flags not yet exposed as named options. "
        "Applied last, can override other settings. "
        "Example: ['--verbose', '--some-new-flag']",
    )
    max_output_capture_bytes: int = Field(
        default=51200,
        gt=0,
        description="Maximum bytes of stdout/stderr to capture per sheet for diagnostics. "
        "Default: 51200 (50KB). Increase for jobs that need more debugging context. "
        "Applies to SheetState.capture_output() during execution.",
    )

    # API-specific options (only meaningful when type="anthropic_api")
    model: str = Field(
        default="claude-sonnet-4-5-20250929",
        description="[anthropic_api] Model ID for Anthropic API",
    )
    api_key_env: str = Field(
        default="ANTHROPIC_API_KEY",
        description="[anthropic_api] Environment variable containing API key",
    )
    max_tokens: int = Field(
        default=16384, ge=1,
        description="[anthropic_api] Maximum tokens for API response",
    )
    temperature: float = Field(
        default=0.7, ge=0, le=1,
        description="[anthropic_api] Sampling temperature",
    )

    # Recursive Light options (only meaningful when type="recursive_light")
    recursive_light: RecursiveLightConfig = Field(
        default_factory=RecursiveLightConfig,
        description="[recursive_light] Configuration for Recursive Light backend",
    )

    # Ollama options (only meaningful when type="ollama")
    ollama: OllamaConfig = Field(
        default_factory=OllamaConfig,
        description="[ollama] Configuration for Ollama backend",
    )

    # ── Cross-field validation ───────────────────────────────────────────

    # Fields that are specific to each backend type.
    # When a field for a different backend is set to a non-default value,
    # a warning is emitted to catch likely configuration mistakes.
    _CLI_SPECIFIC_FIELDS: frozenset[str] = frozenset({
        "skip_permissions", "disable_mcp", "output_format",
        "cli_model", "allowed_tools", "system_prompt_file", "cli_extra_args",
    })
    _API_SPECIFIC_FIELDS: frozenset[str] = frozenset({
        "model", "api_key_env", "max_tokens", "temperature",
    })

    # Legacy backend names that remain valid without any warning during Phases
    # 1-4 (Doctrine exception on the "BackendConfig.type must accept arbitrary
    # strings" rule). Any other value triggers a warning suggesting the caller
    # migrate to the ``instrument:`` path.
    _LEGACY_BACKEND_TYPES: frozenset[str] = frozenset({
        "claude_cli", "anthropic_api", "recursive_light", "ollama",
    })

    @model_validator(mode="after")
    def _warn_on_unknown_backend_type(self) -> BackendConfig:
        """Warn when an unrecognized backend type is supplied.

        Phase 2 of the atlas migration opened ``type`` from a closed Literal
        to ``str``. The safety net is retained by emitting a UserWarning for
        any value outside the four legacy names, telling the user to use the
        ``instrument:`` path instead. The four legacy names NEVER warn here
        (Doctrine: "The original 4 string values ... remain valid and must
        not produce warnings during Phases 1-4").
        """
        if self.type not in self._LEGACY_BACKEND_TYPES:
            warnings.warn(
                f"Backend type '{self.type}' is not one of the legacy "
                f"native backend names {sorted(self._LEGACY_BACKEND_TYPES)}. "
                f"Prefer the 'instrument:' path with a registered "
                f"InstrumentProfile instead of backend.type for new "
                f"instruments.",
                UserWarning,
                stacklevel=2,
            )
        return self

    @model_validator(mode="after")
    def _validate_type_specific_fields(self) -> BackendConfig:
        """Warn when backend-specific fields are set for a different backend type.

        This catches common misconfiguration where CLI-specific fields are set
        but ``type`` is ``anthropic_api``, or vice versa. Uses warnings rather
        than errors for backward compatibility.
        """
        mismatches: list[str] = []
        fields = BackendConfig.model_fields

        def _is_non_default(name: str) -> bool:
            """Check if a field has been set to a non-default value."""
            field_info = fields[name]
            value = getattr(self, name)
            if field_info.default is not PydanticUndefined:
                # Simple default — compare directly
                return bool(value != field_info.default)
            factory = field_info.default_factory
            if factory is not None:
                # default_factory — compare to factory output
                default_value = factory()  # type: ignore[call-arg]
                return bool(value != default_value)
            return False

        if self.type != "claude_cli":
            for field_name in self._CLI_SPECIFIC_FIELDS:
                if _is_non_default(field_name):
                    mismatches.append(field_name)

        if self.type != "anthropic_api":
            for field_name in self._API_SPECIFIC_FIELDS:
                if _is_non_default(field_name):
                    mismatches.append(field_name)

        if mismatches:
            warnings.warn(
                f"Backend type is '{self.type}' but the following fields "
                f"for a different backend were set: {', '.join(sorted(mismatches))}. "
                f"These fields will be ignored.",
                UserWarning,
                stacklevel=2,
            )
        return self
