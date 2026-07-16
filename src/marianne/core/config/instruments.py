"""Instrument Plugin System data models.

Defines the config-driven instrument profile system that allows CLI tools
to be added as mzt instruments via YAML configuration files, without
writing Python backend code.

An InstrumentProfile describes everything Marianne needs to execute prompts
through an instrument: identity, capabilities, CLI flags, output parsing,
error detection, and model metadata. Profiles are loaded from YAML files
in ~/.marianne/instruments/ (organization) and .marianne/instruments/ (venue).

The music metaphor: an instrument is what the musician plays. The profile
is the instrument's spec sheet — what it can do, how it's held, what
sounds it makes. The musician doesn't need to know how the instrument was
built — they just need to know how to play it.

v1: CLI instruments only. HTTP instruments designed for but not implemented.
v1.1+: HTTP backends, code-mode techniques.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- Sub-models (leaf types first, composed types after) ---


class CodeModeInterface(BaseModel):
    """A TypeScript interface exposed to agent-generated code.

    Part of the code-mode technique system (v1: foundation only, not wired).
    Instead of sequential MCP tool calls, agents write code against typed
    interfaces in a sandboxed runtime. Based on Cloudflare's Dynamic Workers
    pattern — 81% token reduction vs MCP.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        description="Interface name, e.g. 'Workspace', 'GitRepo'",
    )
    typescript: str = Field(
        min_length=1,
        description="TypeScript interface definition",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description for the agent",
    )


class CodeModeConfig(BaseModel):
    """Code-mode technique configuration.

    v1: This type exists in the data model but is not wired into execution.
    The field on InstrumentProfile is populated from YAML but ignored at
    runtime. v1.1+: A sandboxed runtime (Deno subprocess or Node.js vm)
    runs agent-generated code against the declared interfaces.
    """

    model_config = ConfigDict(extra="forbid")

    interfaces: list[CodeModeInterface] = Field(
        default_factory=list,
        description="TypeScript interfaces the agent can code against",
    )
    runtime: Literal["deno", "node_vm", "v8_isolate"] = Field(
        default="deno",
        description="Sandbox runtime for running agent-generated code",
    )
    max_execution_ms: int = Field(
        default=30000,
        ge=100,
        description="Maximum time for generated code to run (ms)",
    )


class ModelCapacity(BaseModel):
    """Per-model metadata for cost tracking and context management.

    Each instrument can offer multiple models (e.g., gemini-2.5-pro and
    gemini-2.5-flash). ModelCapacity records what each model can do and
    what it costs — used by the conductor for cost tracking, context
    budget calculation, and instrument selection.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        description="Model identifier, e.g. 'gemini-2.5-pro', 'claude-opus-4-6'",
    )
    context_window: int = Field(
        ge=1,
        description="Maximum context window size in tokens",
    )
    cost_per_1k_input: float = Field(
        ge=0,
        description="Cost per 1000 input tokens (USD). 0 for free/local models.",
    )
    cost_per_1k_output: float = Field(
        ge=0,
        description="Cost per 1000 output tokens (USD). 0 for free/local models.",
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Maximum output tokens the model can produce. None if unlimited.",
    )
    max_concurrent: int = Field(
        default=4,
        ge=1,
        description="Maximum concurrent sheets using this model. "
        "The baton tracks concurrency per (instrument, model) pair. "
        "Sensible defaults by tier: haiku/flash=8, sonnet/pro=4, opus=2.",
    )


# --- CLI Sub-models ---


class CliCommand(BaseModel):
    """How to build the CLI command for an instrument.

    Maps Marianne execution concepts (prompt, model, auto-approve, output format)
    to CLI flags. When a field is None, the instrument doesn't support that
    concept via flags. When prompt_flag is None, the prompt is passed as a
    positional argument.
    """

    model_config = ConfigDict(extra="forbid")

    executable: str = Field(
        min_length=1,
        description="Binary name, e.g. 'claude', 'gemini', 'codex'",
    )
    subcommand: str | None = Field(
        default=None,
        description="Subcommand, e.g. 'exec' for Codex, 'run' for Goose",
    )

    # Flag mappings — None means the instrument doesn't support this concept
    prompt_flag: str | None = Field(
        default=None,
        description="Flag for the prompt, e.g. '-p', '--message'. "
        "None = prompt is a positional argument.",
    )
    model_flag: str | None = Field(
        default=None,
        description="Flag for model selection, e.g. '--model', '-m'",
    )
    auto_approve_flag: str | None = Field(
        default=None,
        description="Flag for auto-approving actions, e.g. '--yolo', '--yes'",
    )
    output_format_flag: str | None = Field(
        default=None,
        description="Flag for output format, e.g. '--output-format', '--json'",
    )
    output_format_value: str | None = Field(
        default=None,
        description="Value for output format flag, e.g. 'json'. "
        "None = the flag is boolean (e.g. '--json' with no value).",
    )
    system_prompt_flag: str | None = Field(
        default=None,
        description="Flag for system prompt file, e.g. '--system-prompt'",
    )
    allowed_tools_flag: str | None = Field(
        default=None,
        description="Flag for restricting available tools",
    )
    mcp_config_flag: str | None = Field(
        default=None,
        description="Flag for MCP server configuration",
    )
    mcp_config_workspace_path: str | None = Field(
        default=None,
        description=(
            "Workspace-relative MCP config path for CLIs that discover MCP "
            "servers from a file instead of a command flag, e.g. "
            "'.agents/mcp_config.json'. The backend copies the conductor "
            "generated config there for the duration of one execution."
        ),
    )
    mcp_config_workspace_merge_key: str | None = Field(
        default=None,
        description=(
            "Optional JSON object key to merge into the workspace MCP config "
            "file instead of replacing the whole file. Use for CLIs whose MCP "
            "servers live inside a broader project settings file, e.g. "
            "Gemini CLI's '.gemini/settings.json' with 'mcpServers'."
        ),
    )
    mcp_config_prefix_args: list[str] = Field(
        default_factory=list,
        description=(
            "CLI args to add immediately before an active MCP config flag/path. "
            "Use for least-privilege switches such as Claude Code's "
            "--strict-mcp-config."
        ),
    )
    mcp_disable_args: list[str] = Field(
        default_factory=list,
        description="CLI args to inject for disabling MCP servers when no MCP "
        "config is requested. Profile-driven — e.g. claude-code uses "
        "['--strict-mcp-config', '--mcp-config', '{\"mcpServers\":{}}'].",
    )
    timeout_flag: str | None = Field(
        default=None,
        description="Flag for per-execution timeout",
    )
    working_dir_flag: str | None = Field(
        default=None,
        description="Flag for working directory. None = use subprocess cwd.",
    )

    # Fixed flags always applied
    extra_flags: list[str] = Field(
        default_factory=list,
        description="Fixed flags always appended to the command",
    )

    # Environment variables to set for the subprocess
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables for the subprocess. "
        "Values can reference os.environ via ${VAR} syntax.",
    )

    # Prompt delivery mode — stdin vs CLI arg
    prompt_via_stdin: bool = Field(
        default=True,
        description="When True (default), pass the prompt via subprocess stdin "
        "instead of as a CLI argument. This avoids ARG_MAX and CLI tool limits "
        "on large prompts — Marianne prompts routinely exceed 100KB with "
        "cadenza/prelude injection (GH#188). When a stdin_sentinel is also "
        "set, the sentinel replaces the prompt in CLI args (e.g. '-p -' for "
        "Claude Code). When no sentinel is set, the prompt flag and prompt "
        "are omitted from args entirely. Set to False only for instruments "
        "that cannot read from stdin (rare).",
    )
    stdin_sentinel: str | None = Field(
        default=None,
        description="Value to use in place of the prompt in CLI args when "
        "prompt_via_stdin is True. For example, Claude Code uses '-' as a "
        "sentinel with '-p -' to indicate 'read prompt from stdin'. "
        "Only meaningful when prompt_via_stdin is True.",
    )

    # Process isolation
    start_new_session: bool = Field(
        default=False,
        description="When True, start the subprocess in a new process group "
        "(start_new_session=True). This isolates the instrument's child "
        "processes (e.g. MCP servers) so they can be cleanly killed as a "
        "group on timeout, rather than leaving orphaned children.",
    )

    # Credential filtering — declare which env vars the instrument needs
    required_env: list[str] | None = Field(
        default=None,
        description="Env vars the instrument needs from the parent environment. "
        "When set, only these vars (plus system essentials like PATH, HOME) "
        "are passed to the subprocess. When None (default), the full parent "
        "environment is inherited (backward compatible). Use this to prevent "
        "credentials for other services from leaking to instrument subprocesses.",
    )

    @field_validator("mcp_config_workspace_path")
    @classmethod
    def _validate_mcp_config_workspace_path(cls, v: str | None) -> str | None:
        """Keep workspace-discovered MCP config files inside the workspace."""
        if v is None:
            return None
        path = Path(v)
        if not v.strip():
            raise ValueError("mcp_config_workspace_path must not be empty")
        if path.is_absolute():
            raise ValueError("mcp_config_workspace_path must be relative")
        if any(part == ".." for part in path.parts):
            raise ValueError("mcp_config_workspace_path must not contain '..'")
        return v

    @field_validator("mcp_config_workspace_merge_key")
    @classmethod
    def _validate_mcp_config_workspace_merge_key(cls, v: str | None) -> str | None:
        """Keep workspace MCP merge keys simple JSON object keys."""
        if v is None:
            return None
        if not v.strip():
            raise ValueError("mcp_config_workspace_merge_key must not be empty")
        if "." in v or "/" in v or "\\" in v:
            raise ValueError("mcp_config_workspace_merge_key must be a simple key")
        return v


class CliOutputConfig(BaseModel):
    """How to parse CLI output into an ExecutionResult.

    Three output modes:
    - text: stdout is the result, no structured extraction
    - json: parse stdout as JSON, extract via dot-path
    - jsonl: split stdout into JSON lines, find completion event
    """

    model_config = ConfigDict(extra="forbid")

    format: Literal["text", "json", "jsonl"] = Field(
        default="text",
        description="Output format: text, json, or jsonl",
    )

    # For JSON format: dot-path to the response text
    result_path: str | None = Field(
        default=None,
        description="JSON dot-path to the result text, e.g. 'result', 'response'",
    )
    error_path: str | None = Field(
        default=None,
        description="JSON dot-path to the error message, e.g. 'error.message'",
    )

    # For JSONL format: how to find the completion event
    completion_event_type: str | None = Field(
        default=None,
        description="JSONL event type that signals completion, "
        "e.g. 'turn.completed', 'item.completed'",
    )
    completion_event_filter: dict[str, str] | None = Field(
        default=None,
        description="Additional key-value filter for completion event matching",
    )

    # Token usage extraction (dot-paths into JSON response)
    input_tokens_path: str | None = Field(
        default=None,
        description="JSON dot-path to input token count",
    )
    output_tokens_path: str | None = Field(
        default=None,
        description="JSON dot-path to output token count",
    )
    aggregate_tokens: bool = Field(
        default=False,
        description="When True, sum all wildcard matches for token paths "
        "instead of returning the first match. Required for instruments "
        "with multi-model routing (e.g., gemini-cli uses flash-lite for "
        "routing and flash/pro for execution — tokens span both models).",
    )


class CliErrorConfig(BaseModel):
    """How to detect errors from CLI instrument output.

    Supplements Marianne's existing ErrorClassifier with instrument-specific
    patterns for rate limit detection and auth error recognition.
    """

    model_config = ConfigDict(extra="forbid")

    success_exit_codes: list[int] = Field(
        default_factory=lambda: [0],
        description="Exit codes that indicate success",
    )
    rate_limit_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns in stderr/stdout indicating rate limiting",
    )
    auth_error_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns indicating authentication failures",
    )
    capacity_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns indicating capacity/overload errors (retriable)",
    )
    timeout_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns indicating timeout errors",
    )
    crash_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns indicating process crash or fatal errors "
        "(segfault, bus error, abort, etc.)",
    )
    stale_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns indicating stale execution "
        "(no output activity for too long)",
    )

    # Structured rate limit detection for stream-json instruments
    rate_limit_event_type: str | None = Field(
        default=None,
        description="JSONL event type indicating rate limiting",
    )
    rate_limit_event_filter: dict[str, str] | None = Field(
        default=None,
        description="Key-value filter for rate limit event matching",
    )


class InteractiveGate(BaseModel):
    """A startup dialog the interactive driver may need to dismiss.

    Some TUI agents present blocking dialogs before reaching their ready
    prompt (e.g. Claude Code's trust-folder check). Each gate pairs a
    screen pattern with the keys that dismiss it. Gates fire at most once
    per session and are skipped entirely once the ready prompt has been
    seen — a late-matching gate must never type into the agent's input.
    """

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(
        min_length=1,
        description="Regex matched against the captured screen. Keep it "
        "anchored to the dialog's distinctive text — broad patterns risk "
        "sending keys into the wrong UI state.",
    )
    keys: list[str] = Field(
        default_factory=lambda: ["Enter"],
        description="tmux send-keys key names sent when the pattern "
        "matches, e.g. ['Enter'] or ['Down', 'Enter'].",
    )

    @field_validator("pattern")
    @classmethod
    def _validate_pattern(cls, v: str) -> str:
        """Reject invalid regex at config-load time, not mid-session."""
        import re

        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Invalid gate pattern regex: {e}") from e
        return v


class InteractiveCliConfig(BaseModel):
    """How to drive a CLI instrument through a live interactive session.

    Present only on profiles whose interactive behavior has been verified
    against the real TUI (screen patterns are empirical, not guessed).
    When a score opts into interactive mode for an instrument without
    this block, backend creation fails with a structured config error.

    See docs/specs/2026-06-10-interactive-mode-design.md.
    """

    model_config = ConfigDict(extra="forbid")

    enabled_by_default: bool = Field(
        default=True,
        description="When True (default), sheets on this instrument run "
        "interactively unless the score explicitly sets interactive: false. "
        "Interactive is the standard execution mode for instruments with "
        "verified interactive support; set False to make this profile "
        "opt-in only.",
    )
    subcommand: str | None = Field(
        default=None,
        description="Subcommand for the interactive launch, e.g. 'session' "
        "for goose. The headless subcommand (e.g. 'run', 'exec') is never "
        "used interactively — most TUIs launch from the bare executable.",
    )
    inherit_auto_approve: bool = Field(
        default=True,
        description="Include the command's auto_approve_flag in the "
        "interactive launch. Set False when the interactive invocation owns "
        "its approval policy through extra_args.",
    )
    inherit_mcp_disable_args: bool = Field(
        default=True,
        description="Include the command's mcp_disable_args in the "
        "interactive launch (when no shared MCP pool config is active). "
        "Set False when those flags are headless-only.",
    )
    extra_args: list[str] = Field(
        default_factory=list,
        description="Extra CLI args for the interactive launch, appended "
        "after the auto-approve and model flags. Headless-only flags "
        "(prompt/output-format) are never used in interactive mode.",
    )
    startup_gates: list[InteractiveGate] = Field(
        default_factory=list,
        description="Ordered startup dialogs that may appear before the "
        "ready prompt. Each fires at most once.",
    )
    ready_pattern: str = Field(
        min_length=1,
        description="Regex on the captured screen that signals the agent "
        "is at its input prompt and ready for text.",
    )
    busy_patterns: list[str] = Field(
        default_factory=list,
        description="Regexes whose presence on the captured screen means "
        "the agent is actively working (spinners, 'esc to interrupt'). "
        "Primary busy signal — screen change alone never means busy.",
    )
    rate_limit_screen_patterns: list[str] = Field(
        default_factory=list,
        description="Regexes for verified interactive UI screens that mean "
        "the provider/account is rate-limited or quota-blocked. These are "
        "separate from CLI stderr patterns so agent prose on an idle screen "
        "does not become a false provider failure.",
    )
    auth_error_screen_patterns: list[str] = Field(
        default_factory=list,
        description="Regexes for verified interactive UI screens that mean "
        "the provider/account is not authenticated or lacks permission.",
    )
    capacity_screen_patterns: list[str] = Field(
        default_factory=list,
        description="Regexes for verified interactive UI screens that mean "
        "the provider is temporarily unavailable or overloaded.",
    )
    crash_screen_patterns: list[str] = Field(
        default_factory=list,
        description="Regexes for verified interactive UI screens that mean "
        "the TUI hit a fatal process/runtime error.",
    )
    quiet_seconds: float = Field(
        default=15.0,
        gt=0,
        description="Seconds the work area must stay unchanged, with no "
        "busy pattern visible, before the agent is considered idle.",
    )
    poll_interval_seconds: float = Field(
        default=2.0,
        gt=0,
        description="Seconds between screen polls in the drive loop.",
    )
    startup_timeout_seconds: float = Field(
        default=90.0,
        gt=0,
        description="Deadline for the session to reach the ready prompt "
        "(including dismissing startup gates).",
    )
    terminal_width: int = Field(
        default=200,
        ge=40,
        description="Virtual terminal width for the tmux session.",
    )
    terminal_height: int = Field(
        default=50,
        ge=10,
        description="Virtual terminal height for the tmux session.",
    )
    volatile_tail_lines: int = Field(
        default=2,
        ge=0,
        description="Bottom screen lines excluded from the change hash "
        "(status lines with live token counters/clocks would otherwise "
        "defeat idle detection).",
    )

    @field_validator(
        "ready_pattern",
        "busy_patterns",
        "rate_limit_screen_patterns",
        "auth_error_screen_patterns",
        "capacity_screen_patterns",
        "crash_screen_patterns",
    )
    @classmethod
    def _validate_regexes(cls, v: str | list[str]) -> str | list[str]:
        """Reject invalid regex at config-load time, not mid-session."""
        import re

        patterns = [v] if isinstance(v, str) else v
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid pattern regex {pattern!r}: {e}") from e
        return v


class CliProfile(BaseModel):
    """Everything needed to invoke a CLI instrument and parse its output.

    Composed of four concerns:
    - command: how to build the CLI invocation
    - output: how to parse the result
    - errors: how to detect failures
    - interactive: how to drive a live TUI session (optional, verified
      instruments only)
    """

    model_config = ConfigDict(extra="forbid")

    command: CliCommand = Field(
        description="How to build the CLI command",
    )
    output: CliOutputConfig = Field(
        description="How to parse CLI output",
    )
    errors: CliErrorConfig = Field(
        default_factory=CliErrorConfig,
        description="How to detect errors from CLI output",
    )
    interactive: InteractiveCliConfig | None = Field(
        default=None,
        description="How to drive this CLI through a live interactive "
        "tmux session. None = interactive mode unavailable for this "
        "instrument (scores requesting it fail fast).",
    )


# --- HTTP Profile ---


class HttpProfile(BaseModel):
    """Configuration for the shared OpenAI-compatible HTTP executor."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(
        description="Base URL for the HTTP API",
    )
    endpoint: str = Field(
        default="/v1/chat/completions",
        description="API endpoint path",
    )
    schema_family: Literal["openai"] = Field(
        description="OpenAI-compatible request and response contract",
    )
    auth_env_var: str | None = Field(
        default=None,
        description="Environment variable containing the API key",
    )


# --- Top-Level InstrumentProfile ---


class InstrumentProfile(BaseModel):
    """Everything Marianne needs to execute prompts through an instrument.

    This is the top-level type for the instrument plugin system. Each
    instrument profile describes a CLI tool or HTTP API that Marianne can
    use as a backend. Profiles are loaded from YAML files and validated
    by Pydantic at conductor startup.

    The profile carries:
    - Identity (name, display_name, kind)
    - Capabilities (what the instrument can do)
    - Models (what models are available, their costs and limits)
    - Execution config (CLI flags or HTTP endpoints)
    - Code-mode technique config (foundation — not wired in v1)
    """

    model_config = ConfigDict(extra="forbid")

    # Identity
    name: str = Field(
        min_length=1,
        description="Unique name used in score YAML, e.g. 'gemini-cli'",
    )
    display_name: str = Field(
        min_length=1,
        description="Human-readable name for CLI output",
    )
    description: str | None = Field(
        default=None,
        description="Brief description of the instrument",
    )
    kind: Literal["cli", "http"] = Field(
        description="Execution interface type: cli or http",
    )

    # Capabilities — what this instrument can do
    capabilities: set[str] = Field(
        default_factory=set,
        description="Capability tags: tool_use, file_editing, shell_access, "
        "vision, mcp, code_mode, structured_output, session_resume, "
        "streaming, thinking",
    )

    # Code-mode technique support (foundation — not implemented in v1)
    code_mode: CodeModeConfig | None = Field(
        default=None,
        description="Code-mode technique configuration. Declares TypeScript "
        "interfaces the agent can code against in a sandboxed runtime. "
        "v1: foundation only (field exists, not wired). v1.1+: implementation.",
    )

    # Models available on this instrument
    models: list[ModelCapacity] = Field(
        default_factory=list,
        description="Models available on this instrument with cost/capacity info",
    )
    default_model: str | None = Field(
        default=None,
        description="Default model name. Must match a name in models list if set.",
    )

    # Default execution parameters
    default_timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        description="Default per-sheet execution timeout in seconds",
    )

    execution_status: Literal["ready", "warning", "unsupported"] = Field(
        default="ready",
        description=(
            "Profile-level prompt execution readiness. Use 'unsupported' "
            "when the binary may exist but this shipped profile should not "
            "be selected automatically because live prompt execution is "
            "known to fail for the default auth/tier path. Local profile "
            "overrides can set this back to 'ready' after proving their "
            "environment works."
        ),
    )
    execution_status_detail: str | None = Field(
        default=None,
        description="Human-readable reason for non-ready execution_status.",
    )

    # Prompt-assembly bypass for instruments that consume raw input
    raw_prompt: bool = Field(
        default=False,
        description="When True, the prompt-assembly pipeline passes the "
        "rendered Jinja template to this instrument verbatim — no preamble, "
        "no prelude/cadenza injection, no spec fragments, no failure history, "
        "no learned patterns, no validation requirements, no completion "
        "suffix. Use for instruments that consume their input as raw text "
        "(e.g. bash, deterministic CLIs) and would be corrupted by "
        "Marianne's prompt-wrapping layers. Validations still RUN after "
        "execution; they just never appear in the prompt itself.",
    )

    # Kind-specific profiles
    cli: CliProfile | None = Field(
        default=None,
        description="CLI-specific execution profile. Required when kind=cli.",
    )
    http: HttpProfile | None = Field(
        default=None,
        description="HTTP-specific execution profile. Required when kind=http.",
    )

    @model_validator(mode="after")
    def _validate_kind_profile(self) -> InstrumentProfile:
        """Ensure the kind-specific profile is present.

        kind=cli requires cli profile. kind=http requires http profile.
        This catches misconfiguration at parse time, not at execution time.
        """
        if self.kind == "cli" and self.cli is None:
            raise ValueError(
                f"Instrument '{self.name}' has kind=cli but no cli profile. "
                "Provide a cli: section with command and output configuration."
            )
        if self.kind == "http" and self.http is None:
            raise ValueError(
                f"Instrument '{self.name}' has kind=http but no http profile. "
                "Provide an http: section with base_url and schema_family."
            )
        return self

    @field_validator("capabilities", mode="before")
    @classmethod
    def _coerce_capabilities(cls, v: set[str] | list[str]) -> set[str]:
        """Coerce list to set (YAML loads lists, we want sets)."""
        if isinstance(v, list):
            return set(v)
        return v
