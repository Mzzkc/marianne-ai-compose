"""Instrument Plugin System data models.

Defines the config-driven instrument profile system that allows CLI tools
to be added as Mozart instruments via YAML configuration files, without
writing Python backend code.

An InstrumentProfile describes everything Mozart needs to execute prompts
through an instrument: identity, capabilities, CLI flags, output parsing,
error detection, and model metadata. Profiles are loaded from YAML files
in ~/.mozart/instruments/ (organization) and .mozart/instruments/ (venue).

The music metaphor: an instrument is what the musician plays. The profile
is the instrument's spec sheet — what it can do, how it's held, what
sounds it makes. The musician doesn't need to know how the instrument was
built — they just need to know how to play it.

v1: CLI instruments only. HTTP instruments designed for but not implemented.
v1.1+: HTTP backends, code-mode techniques.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# --- Sub-models (leaf types first, composed types after) ---


class CodeModeInterface(BaseModel):
    """A TypeScript interface exposed to agent-generated code.

    Part of the code-mode technique system (v1: foundation only, not wired).
    Instead of sequential MCP tool calls, agents write code against typed
    interfaces in a sandboxed runtime. Based on Cloudflare's Dynamic Workers
    pattern — 81% token reduction vs MCP.
    """

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


# --- CLI Sub-models ---


class CliCommand(BaseModel):
    """How to build the CLI command for an instrument.

    Maps Mozart execution concepts (prompt, model, auto-approve, output format)
    to CLI flags. When a field is None, the instrument doesn't support that
    concept via flags. When prompt_flag is None, the prompt is passed as a
    positional argument.
    """

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


class CliOutputConfig(BaseModel):
    """How to parse CLI output into an ExecutionResult.

    Three output modes:
    - text: stdout is the result, no structured extraction
    - json: parse stdout as JSON, extract via dot-path
    - jsonl: split stdout into JSON lines, find completion event
    """

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


class CliErrorConfig(BaseModel):
    """How to detect errors from CLI instrument output.

    Supplements Mozart's existing ErrorClassifier with instrument-specific
    patterns for rate limit detection and auth error recognition.
    """

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

    # Structured rate limit detection for stream-json instruments
    rate_limit_event_type: str | None = Field(
        default=None,
        description="JSONL event type indicating rate limiting",
    )
    rate_limit_event_filter: dict[str, str] | None = Field(
        default=None,
        description="Key-value filter for rate limit event matching",
    )


class CliProfile(BaseModel):
    """Everything needed to invoke a CLI instrument and parse its output.

    Composed of three concerns:
    - command: how to build the CLI invocation
    - output: how to parse the result
    - errors: how to detect failures
    """

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


# --- HTTP Profile (stub — deferred to v1.1) ---


class HttpProfile(BaseModel):
    """HTTP instrument profile. Designed for, not implemented in v1.

    Covers OpenAI-compatible, Anthropic API, and Gemini API endpoints.
    One HTTP handler will cover most of them via schema_family.
    """

    base_url: str = Field(
        description="Base URL for the HTTP API",
    )
    endpoint: str = Field(
        default="/v1/chat/completions",
        description="API endpoint path",
    )
    schema_family: Literal["openai", "anthropic", "gemini"] = Field(
        description="API schema family for request/response formatting",
    )
    auth_env_var: str | None = Field(
        default=None,
        description="Environment variable containing the API key",
    )


# --- Top-Level InstrumentProfile ---


class InstrumentProfile(BaseModel):
    """Everything Mozart needs to execute prompts through an instrument.

    This is the top-level type for the instrument plugin system. Each
    instrument profile describes a CLI tool or HTTP API that Mozart can
    use as a backend. Profiles are loaded from YAML files and validated
    by Pydantic at conductor startup.

    The profile carries:
    - Identity (name, display_name, kind)
    - Capabilities (what the instrument can do)
    - Models (what models are available, their costs and limits)
    - Execution config (CLI flags or HTTP endpoints)
    - Code-mode technique config (foundation — not wired in v1)
    """

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
