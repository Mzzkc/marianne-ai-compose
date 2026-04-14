"""Configuration models for the Marianne conductor (daemon).

Defines Pydantic v2 models for daemon-specific settings: socket configuration,
resource limits, concurrency controls, and state backend selection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from marianne.core.config.backend import BackendConfig
from marianne.core.config.execution import PreflightConfig
from marianne.core.logging import get_logger
from marianne.daemon.keyring_config import KeyringConfig
from marianne.daemon.profiler.models import ProfilerConfig

_logger = get_logger("daemon.config")


class McpServerEntry(BaseModel):
    """A single MCP server in the shared pool.

    The conductor manages the lifecycle of these servers: start, health check,
    restart on failure. Each server is proxied behind a Unix socket so agents
    can access it via bind-mount into their sandbox.
    """

    model_config = ConfigDict(extra="forbid")

    command: str = Field(
        description="Command to start the MCP server process",
    )
    transport: str = Field(
        default="stdio",
        description="Transport protocol: stdio (proxied to socket), sse, http",
    )
    socket: str = Field(
        description="Unix socket path for proxying this server to agents",
    )
    restart_policy: str = Field(
        default="on-failure",
        description="When to restart: 'on-failure', 'always', 'never'",
    )


class McpPoolConfig(BaseModel):
    """Shared MCP server pool managed by the conductor.

    One process per MCP server type, shared across all agents. The conductor
    proxies each stdio MCP server behind a Unix socket. Sockets are forwarded
    into agent sandboxes via bind-mount.

    Example YAML::

        mcp_pool:
          servers:
            github:
              command: "github-mcp-server"
              socket: "/tmp/mzt/mcp/github.sock"
            filesystem:
              command: "fs-mcp-server"
              socket: "/tmp/mzt/mcp/filesystem.sock"
    """

    model_config = ConfigDict(extra="forbid")

    servers: dict[str, McpServerEntry] = Field(
        default_factory=dict,
        description="Named MCP server entries in the shared pool",
    )


class ResourceLimitConfig(BaseModel):
    """Resource constraints for the daemon process.

    Prevents runaway resource consumption by capping memory, processes,
    and API call rates at the daemon level.
    """

    model_config = ConfigDict(extra="forbid")

    max_memory_mb: int = Field(
        default=8192,
        ge=512,
        description="Maximum RSS memory in MB before daemon triggers backpressure",
    )
    max_processes: int = Field(
        default=50,
        ge=5,
        description="Maximum child processes (backends + validation commands)",
    )
    max_api_calls_per_minute: int = Field(
        default=60,
        ge=1,
        description="Global rate limit for API calls across all jobs. "
        "NOT YET ENFORCED — rate limiting currently works through "
        "externally-reported events via RateLimitCoordinator. "
        "Setting a non-default value will log a warning at startup.",
    )

    @model_validator(mode="after")
    def _reject_unimplemented_overrides(self) -> ResourceLimitConfig:
        """Reject reserved fields set to non-default values."""
        if self.max_api_calls_per_minute != 60:
            raise ValueError(
                f"ResourceLimitConfig.max_api_calls_per_minute={self.max_api_calls_per_minute} "
                "is not yet enforced. Rate limiting currently works through externally-reported "
                "events via RateLimitCoordinator. Remove this override or use the default (60)."
            )
        return self


class SocketConfig(BaseModel):
    """Unix domain socket configuration for daemon IPC.

    Uses Unix sockets (not TCP) for security — no network exposure.
    The socket file is created on daemon start and removed on clean shutdown.
    """

    model_config = ConfigDict(extra="forbid")

    path: Path = Field(
        default=Path("/tmp/marianne.sock"),
        description="Unix domain socket path for client-daemon communication",
    )
    permissions: int = Field(
        default=0o660,
        ge=0,
        le=0o777,
        description="File permissions for the socket (octal). "
        "0o660 = owner+group read/write, no world access.",
    )
    backlog: int = Field(
        default=5,
        ge=1,
        description="Maximum pending connections in the socket listen queue",
    )


class ObserverConfig(BaseModel):
    """Configuration for the job observer and event bus."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Enable event bus and observer infrastructure.",
    )
    watch_interval_seconds: float = Field(
        default=2.0,
        ge=0.5,
        description="Interval between observer filesystem checks.",
    )
    snapshot_ttl_hours: int = Field(
        default=168,
        ge=1,
        description="Hours to keep completion snapshots (default 1 week).",
    )
    max_queue_size: int = Field(
        default=1000,
        ge=100,
        description="Maximum events per subscriber before drop-oldest.",
    )
    persist_events: bool = Field(
        default=True,
        description="Persist observer events to per-job JSONL timeline files.",
    )
    exclude_patterns: list[str] = Field(
        default=[".git/", "__pycache__/", "node_modules/", ".venv/", "*.pyc"],
        description="Glob patterns for filesystem paths to exclude from observation.",
    )
    coalesce_window_seconds: float = Field(
        default=2.0,
        ge=0.0,
        description="Window in seconds to coalesce rapid filesystem events.",
    )
    max_timeline_bytes: int = Field(
        default=10_485_760,
        ge=4096,
        description="Maximum size in bytes for per-job JSONL timeline files.",
    )


def _default_semantic_backend() -> BackendConfig:
    """Default backend config for semantic analysis.

    Uses the Anthropic API with analytical defaults (low temperature,
    moderate token limit, shorter timeout). This preserves the original
    behaviour for users who don't set ``learning.backend`` explicitly.
    """
    return BackendConfig(
        type="anthropic_api",
        model="claude-sonnet-4-5-20250929",
        temperature=0.3,
        max_tokens=4096,
        timeout_seconds=120.0,
    )


class SemanticLearningConfig(BaseModel):
    """Configuration for conductor-level semantic learning via LLM.

    Controls how the conductor analyzes sheet completions using an LLM
    to produce semantic insights stored in the learning database.

    The ``backend`` field accepts the same ``BackendConfig`` used by job
    execution, so any backend type (claude_cli, anthropic_api, ollama,
    recursive_light) can power semantic analysis.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Enable semantic learning. When True, the conductor "
        "analyzes sheet completions via LLM to produce learning insights.",
    )
    backend: BackendConfig = Field(
        default_factory=_default_semantic_backend,
        description="Backend configuration for semantic analysis LLM calls. "
        "Accepts any backend type: claude_cli (uses Claude Code, no API key "
        "needed), anthropic_api, ollama (free local models), or recursive_light. "
        "Defaults to anthropic_api with analytical settings (temperature=0.3).",
    )
    analyze_on: list[Literal["success", "failure"]] = Field(
        default=["success", "failure"],
        description="Which sheet outcomes to analyze. "
        "Options: 'success', 'failure', or both.",
    )
    max_concurrent_analyses: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Maximum concurrent LLM analysis tasks. "
        "Controls API cost and system load.",
    )
    entropy_threshold: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Entropy threshold for triggering diversity injection. "
        "When pattern diversity index drops below this value, the learning "
        "system automatically boosts exploration budget to escape local optima. "
        "Default 0.1 (10% of max entropy) catches severe pattern collapse.",
    )
    exploration_budget: float = Field(
        default=0.15,
        ge=0.05,
        le=0.50,
        description="Exploration budget boost amount when entropy response triggers. "
        "This percentage of pattern applications will use random selection "
        "instead of highest-effectiveness to inject diversity. "
        "Default 0.15 (15%) balances exploration vs exploitation.",
    )
    entropy_check_interval_seconds: float = Field(
        default=3600.0,
        ge=60.0,
        description="Interval between entropy checks in seconds. "
        "Entropy is also checked after every 10 completed jobs. "
        "Default 3600s (1 hour) prevents excessive checking overhead.",
    )

    @field_validator("analyze_on")
    @classmethod
    def _validate_analyze_on(
        cls, v: list[Literal["success", "failure"]],
    ) -> list[Literal["success", "failure"]]:
        """Ensure analyze_on is non-empty and has no duplicates."""
        if not v:
            raise ValueError("analyze_on must contain at least one value")
        if len(v) != len(set(v)):
            raise ValueError("analyze_on must not contain duplicates")
        return v


# Deprecated DaemonConfig fields stripped during model validation.
# Existing conductor.yaml files may still contain these after upgrades.
_DAEMON_DEPRECATED_FIELDS: frozenset[str] = frozenset({"use_baton"})


class DaemonConfig(BaseModel):
    """Top-level configuration for the Marianne conductor.

    Controls socket binding, PID file location, concurrency limits,
    resource constraints, and state backend selection. Follows the
    same Field() conventions as marianne.core.config.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _strip_deprecated_fields(cls, data: dict[str, object]) -> dict[str, object]:
        """Remove deprecated fields from raw config data with a warning.

        Existing conductor.yaml files may still contain fields that have
        been removed (e.g. ``use_baton``). The validator logs a warning and
        strips them so ``extra="forbid"`` does not reject the config.
        """
        if not isinstance(data, dict):
            return data
        for field_name in _DAEMON_DEPRECATED_FIELDS:
            if field_name in data:
                _logger.warning(
                    "deprecated_config_field",
                    field=field_name,
                    hint="This field is no longer used and will be ignored.",
                )
                data.pop(field_name)
        return data

    socket: SocketConfig = Field(
        default_factory=SocketConfig,
        description="Unix domain socket configuration for IPC",
    )
    pid_file: Path = Field(
        default=Path("/tmp/marianne.pid"),
        description="PID file for daemon process management. "
        "Used to detect already-running daemons and for signal delivery.",
    )
    max_concurrent_jobs: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Maximum jobs executing simultaneously. "
        "Each job runs as an asyncio.Task in the daemon event loop.",
    )
    max_concurrent_sheets: int = Field(
        default=25,
        ge=1,
        le=100,
        description="Global ceiling on concurrent sheet executions across all jobs. "
        "Bounds the total number of instrument processes the conductor runs "
        "simultaneously, regardless of per-model or per-instrument limits. "
        "Per-model limits (from instrument profiles) control distribution "
        "within this ceiling.",
    )
    resource_limits: ResourceLimitConfig = Field(
        default_factory=ResourceLimitConfig,
        description="Resource constraints for the daemon process",
    )
    state_backend_type: Literal["sqlite"] = Field(
        default="sqlite",
        description="Reserved — will enable persistent daemon state in a future "
        "release. Currently frozen to 'sqlite'; changing has no effect. "
        "Setting a non-default value will log a warning at startup.",
    )
    state_db_path: Path = Field(
        default=Path("~/.marianne/daemon-state.db"),
        description="Reserved for future use; not yet implemented. "
        "Will be the path for daemon state database. "
        "Tilde is expanded at runtime. Stores job registry and metrics.",
    )
    log_level: Literal["debug", "info", "warning", "error"] = Field(
        default="info",
        description="Minimum log level for daemon structlog output.",
    )
    log_file: Path | None = Field(
        default=None,
        description="Log file path. None means log to stderr only.",
    )
    job_timeout_seconds: float = Field(
        default=86400.0,
        ge=60.0,
        description="Maximum wall-clock time for a single job task. "
        "Jobs exceeding this limit are cancelled with FAILED status. "
        "Default is 24 hours (86400s). Set higher for known long-running jobs.",
    )
    shutdown_timeout_seconds: float = Field(
        default=300.0,
        ge=10.0,
        description="Max seconds to wait for running jobs during graceful shutdown",
    )
    monitor_interval_seconds: float = Field(
        default=15.0,
        ge=5.0,
        description="Interval between resource monitor checks",
    )
    max_job_history: int = Field(
        default=1000,
        ge=10,
        description="Maximum completed/failed/cancelled jobs to keep in memory. "
        "Oldest terminal jobs are evicted when the limit is reached.",
    )
    default_thinking_method: str | None = Field(
        default=None,
        description="Default thinking methodology directive injected into prompts "
        "for all jobs managed by this conductor. Individual jobs can override "
        "this via their own prompt.thinking_method setting. "
        "Example: 'Use structured reasoning with explicit step numbering.'",
    )
    observer: ObserverConfig = Field(
        default_factory=ObserverConfig,
        description="Observer and event bus configuration.",
    )
    learning: SemanticLearningConfig = Field(
        default_factory=SemanticLearningConfig,
        description="Semantic learning configuration for LLM-based analysis "
        "of sheet completions.",
    )
    profiler: ProfilerConfig = Field(
        default_factory=ProfilerConfig,
        description="System profiler configuration for resource monitoring, "
        "anomaly detection, and correlation analysis.",
    )
    preflight: PreflightConfig = Field(
        default_factory=PreflightConfig,
        description="Pre-flight prompt analysis thresholds. "
        "Controls when prompts are warned or rejected based on estimated token count. "
        "Set higher for large-context instruments (1M+ context windows).",
    )
    config_file: Path | None = Field(
        default=None,
        description="Path to the YAML config file this config was loaded from. "
        "Set automatically by _load_config(); used by SIGHUP reload to "
        "know which file to re-read.",
    )

    mcp_pool: McpPoolConfig = Field(
        default_factory=McpPoolConfig,
        description="Shared MCP server pool configuration. "
        "The conductor manages a pool of MCP server processes as shared "
        "infrastructure. Agents declare access per technique config; "
        "the conductor only forwards sockets for declared techniques.",
    )
    keyring: KeyringConfig = Field(
        default_factory=KeyringConfig,
        description="API key keyring with rotation. "
        "Per-instrument key management with least-recently-rate-limited "
        "or round-robin rotation policies. Keys are paths to files in "
        "$SECRETS_DIR/, never stored in config.",
    )

    @model_validator(mode="after")
    def _warn_reserved_fields(self) -> DaemonConfig:
        """Warn when reserved/unimplemented fields are set to non-default values."""
        return self
