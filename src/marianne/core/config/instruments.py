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

import copy
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from marianne.core.config.instrument_settings import EFFORT_LEVELS, EffortLevel


class InstrumentRouteBinding(BaseModel):
    """Reviewed declared route, not proof of a service's loaded model weights."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    arm: Literal["remote", "local"] = Field(description="Disclosed transport arm")
    instrument: str = Field(min_length=1, description="Exact score-visible instrument name")
    kind: str = Field(min_length=1, description="Resolved profile executor kind")
    profile_origin: Literal["organization", "venue"] | None = Field(
        description=(
            "Observable winning profile registry, or None for an unobserved origin. "
            "`organization` is ~/.marianne/instruments/; `venue` is a project "
            ".marianne/instruments/ that is not the organization directory, so the "
            "same profile file classifies the same way from every working directory "
            "(GH #493)."
        ),
    )
    profile_file_sha256: str | None = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Digest of the winning profile bytes, not their body",
    )
    effective_model: str = Field(min_length=1, description="Exact requested model after overrides")
    effective_provider: str | None = Field(description="Declared provider, absent if undeclared")
    model_source: Literal["score_override", "profile"] | None = Field(
        description="Source of the effective model declaration",
    )
    provider_source: Literal["score_override", "profile"] | None = Field(
        description="Source of the provider declaration, absent if undeclared",
    )
    transport_scheme: Literal["http", "https"] | None = Field(description="Declared HTTP scheme")
    transport_host: str | None = Field(description="Declared HTTP hostname")
    transport_port: int | None = Field(ge=1, le=65535, description="Declared HTTP port")
    transport_endpoint: str | None = Field(description="Declared relative HTTP endpoint")
    resolved_at: AwareDatetime = Field(description="Capture time, excluded from route identity")

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


class EffortMapping(BaseModel):
    """How a profile delivers ``instrument_config.effort`` to its harness (GH #535).

    A score names a level in Marianne's vocabulary (``minimal`` .. ``max``);
    ``levels`` maps it to the harness's own word and ``delivery`` says where
    that word goes. Third-party profiles under ``~/.marianne/instruments/`` or
    ``.marianne/instruments/`` declare the same block; no code change is needed
    to give a new harness the key.
    """

    model_config = ConfigDict(extra="forbid")

    delivery: Literal["flag", "config_override", "body_field"] = Field(
        description=(
            "Where the native level goes. 'flag': `<flag> <native>` on the "
            "command (claude-code and antigravity `--effort`, opencode "
            "`--variant`, crush `--reasoning-effort`). 'config_override': "
            "`<flag> <key>=<native>` (codex-cli `-c model_reasoning_effort=high`). "
            "'body_field': the HTTP request JSON gets `<key>` = native (dotted "
            "key = nested object), for kind: http profiles only. A default "
            "already in extra_flags / schema_extra_flags / "
            "cli.interactive.extra_args is replaced where it stands, never "
            "joined by a second one."
        ),
    )
    flag: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "The command-line switch: the effort flag itself for delivery "
            "'flag' (e.g. '--effort'), the config-override switch for "
            "'config_override' (e.g. codex '-c'). Required for both; not used "
            "by 'body_field'."
        ),
    )
    key: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "The configuration key for 'config_override' (codex: "
            "'model_reasoning_effort') or the request-body path for "
            "'body_field' (e.g. 'reasoning_effort', or 'reasoning.effort' for "
            "a nested object). Not used by 'flag'."
        ),
    )
    levels: dict[EffortLevel, str] = Field(
        default_factory=dict,
        description=(
            "Marianne level -> the harness's native word, e.g. "
            "{minimal: low, low: low, medium: medium, high: high}. Several "
            "Marianne levels may share one native word; a Marianne level left "
            "out is refused with the list of the ones that are mapped. {} = "
            "the profile maps no level itself and each model row's "
            "effort_levels decides (opencode, crush: levels differ per model)."
        ),
    )
    schema_levels: list[EffortLevel] | None = Field(
        default=None,
        description=(
            "The Marianne levels a schema sheet (instrument_config."
            "response_format through json_schema_flag) may ask for. None = "
            "every mapped level. Exists because a level right for free-form "
            "work can break structured output: Claude Code 2.1.307 under "
            "`--effort max` with `--json-schema` emits nothing until killed, so "
            "claude-code lists only levels measured to answer. A schema sheet "
            "asking for another level is refused, never downgraded; the "
            "profile's own schema default must be a value one of these maps to."
        ),
    )
    interactive: bool = Field(
        default=True,
        description=(
            "Whether an interactive (tmux) launch can carry the level. False "
            "when the harness's interactive command has no such option (the "
            "opencode TUI has no --variant): a sheet that sets effort and runs "
            "interactively is then refused, not run at the TUI's default."
        ),
    )

    @model_validator(mode="after")
    def _shape_is_whole(self) -> EffortMapping:
        needs_flag = self.delivery in ("flag", "config_override")
        needs_key = self.delivery in ("config_override", "body_field")
        if needs_flag and self.flag is None:
            raise ValueError(f"effort.delivery {self.delivery!r} needs effort.flag")
        if not needs_flag and self.flag is not None:
            raise ValueError("effort.flag is not used by delivery 'body_field'; remove it")
        if needs_key and self.key is None:
            raise ValueError(f"effort.delivery {self.delivery!r} needs effort.key")
        if not needs_key and self.key is not None:
            raise ValueError("effort.key is not used by delivery 'flag'; remove it")
        for level, native in self.levels.items():
            if not native.strip():
                raise ValueError(f"effort.levels.{level} must not be empty")
        if self.schema_levels is not None:
            if not self.schema_levels:
                raise ValueError(
                    "effort.schema_levels must not be empty; omit it to accept "
                    "every mapped level on schema sheets"
                )
            if len(set(self.schema_levels)) != len(self.schema_levels):
                raise ValueError("effort.schema_levels must not contain a duplicate level")
            if self.levels:
                unknown = [lv for lv in self.schema_levels if lv not in self.levels]
                if unknown:
                    raise ValueError(
                        f"effort.schema_levels {unknown} are not mapped in "
                        f"effort.levels ({', '.join(self.levels)})"
                    )
        return self

    def defaults_in(self, flags: list[str]) -> list[str]:
        """Every native value a fixed flag list already sets through this mapping."""
        values: list[str] = []
        flag = self.flag
        if flag is None:
            return values
        prefix = f"{self.key}=" if self.delivery == "config_override" else ""
        for index, token in enumerate(flags):
            if token == flag and index + 1 < len(flags):
                value = flags[index + 1]
            elif token.startswith(f"{flag}="):
                value = token[len(flag) + 1:]
            else:
                continue
            if value.startswith(prefix):
                values.append(value[len(prefix):])
        return values

    def apply(self, flags: list[str], native: str) -> list[str]:
        """``flags`` with this mapping's value set to ``native`` (CLI shapes).

        A default is replaced where it stands (never a second one); with no
        default the pair is appended. Another switch that shares the flag (a
        second codex ``-c`` override) is kept. Callers check the refusal first.
        """
        flag = self.flag
        if flag is None:
            return list(flags)
        is_override = self.delivery == "config_override"
        prefix = f"{self.key}=" if is_override else ""
        value = f"{prefix}{native}"
        result: list[str] = []
        replaced = False
        skip = False
        for index, token in enumerate(flags):
            if skip:
                skip = False
                continue
            current: str | None = None
            is_pair = token == flag and index + 1 < len(flags)
            if is_pair:
                current = flags[index + 1]
            elif token.startswith(f"{flag}="):
                current = token[len(flag) + 1:]
            if current is not None and current.startswith(prefix):
                skip = is_pair
                if not replaced:
                    result.extend([flag, value])
                    replaced = True
                continue
            result.append(token)
        if not replaced:
            result.extend([flag, value])
        return result

    def apply_to_body(self, payload: dict[str, Any], native: str) -> dict[str, Any]:
        """``payload`` with this mapping's dotted ``key`` set to ``native`` (HTTP)."""
        result = copy.deepcopy(payload)
        if self.key is None:
            return result
        parts = self.key.split(".")
        target = result
        for part in parts[:-1]:
            nested = target.get(part)
            if not isinstance(nested, dict):
                nested = {}
                target[part] = nested
            target = nested
        target[parts[-1]] = native
        return result


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
    cost_per_1k_cache_read: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Cost per 1000 input tokens served from the provider's prompt cache "
            "(USD). Providers bill these at a fraction of the input rate (10% on "
            "Anthropic and OpenAI, 25% on Google). Unset means the estimate "
            "charges cache reads at cost_per_1k_input, so an unpriced row is "
            "never cheaper than the input rate."
        ),
    )
    cost_per_1k_cache_write: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Cost per 1000 input tokens written into the provider's prompt cache "
            "(USD; Anthropic bills 125% of input). Unset charges cache writes at "
            "cost_per_1k_input."
        ),
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
    effort_levels: dict[EffortLevel, str] | None = Field(
        default=None,
        description=(
            "This model's own reasoning-effort map (Marianne level -> native "
            "word), replacing the profile's effort.levels when the sheet runs "
            "on this model. None = the profile's levels apply; {} = this model "
            "has no reasoning control, so instrument_config.effort is refused "
            "on it. Needs the profile's effort block (delivery shape). Exists "
            "because a harness can run models with different controls: "
            "opencode lists variants low/high/max for zai-coding-plan/glm-5.3 "
            "and none for glm-4.7."
        ),
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
    json_schema_flag: str | None = Field(
        default=None,
        description=(
            "Flag for enforcing a JSON schema on structured output, e.g. "
            "Claude Code's '--json-schema' (takes the compact schema JSON "
            "as a single argument). Declared = the backend honors a sheet's "
            "instrument_config.response_format (type json_schema) by "
            "appending flag + schema, and the terminal JSON's top-level "
            "'structured_output' object becomes the sheet result. None = "
            "the instrument cannot enforce a schema; a configured "
            "response_format then fails dispatch loudly rather than "
            "running unschematized. Requires output format 'json'."
        ),
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
    schema_extra_flags: list[str] | None = Field(
        default=None,
        description=(
            "Flags appended INSTEAD of extra_flags when the sheet runs in "
            "schema mode (instrument_config.response_format of type "
            "json_schema, passed through json_schema_flag). None = schema "
            "sheets get extra_flags like every other sheet; [] = schema "
            "sheets get no extra flags at all. Exists because a flag that "
            "is right for free-form work can break structured output: "
            "Claude Code 2.1.307 under `--effort max` with `--json-schema` "
            "emits nothing until killed, while `--effort high` completes."
        ),
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
    stdin_json_template: dict[str, Any] | None = Field(
        default=None,
        description="When set, the prompt is NOT written to stdin as raw "
        "text: this object is serialised as ONE JSON line with every string "
        "value equal to '{prompt}' replaced by the assembled prompt (nested "
        "dicts/lists are walked). For CLIs whose only stdin path is a "
        "structured stream, e.g. Antigravity's --input-format stream-json "
        "expects {\"event\":\"user\",\"message\":{\"role\":\"user\","
        "\"content\":...}}. Only meaningful when prompt_via_stdin is True.",
    )

    # Process isolation
    supervise: bool = Field(
        default=True,
        description="When True (default), launch the instrument under Marianne's "
        "Linux instrument supervisor: a child subreaper in the instrument's own "
        "process group that adopts any grandchild whose parent exits (an agent's "
        "search helper, a disowned shell) and ends and reaps every adopted "
        "process when the instrument exits or the attempt is torn down. Without "
        "it such grandchildren re-parent to init and outlive the sheet (Pallas, "
        "20 Oct 2026). Set False only for an instrument the supervisor cannot "
        "wrap (not Linux, or a binary that must be the direct child of the "
        "conductor).",
    )
    io_priority: Literal["low", "idle", "inherit"] = Field(
        default="low",
        description="I/O scheduling class the supervisor gives the instrument and "
        "everything it spawns: 'low' (best-effort, lowest priority; default) keeps "
        "an agent's whole-disk search from stalling the conductor's own state "
        "reads; 'idle' yields to every other process; 'inherit' leaves the "
        "conductor's class in place. Ignored when supervise is False.",
    )
    max_open_files: int | None = Field(
        default=4096,
        ge=64,
        description="Soft RLIMIT_NOFILE the supervisor sets for the instrument and "
        "everything it spawns (default 4096; the conductor's own limit on a "
        "server is often 1,048,576). Bounds a runaway search or a leaking "
        "helper to its share of the host's file table; never raised above the "
        "hard limit. null leaves the conductor's limit in place. Ignored when "
        "supervise is False.",
    )
    start_new_session: bool = Field(
        default=False,
        description="When True, start the subprocess in a new process group "
        "(start_new_session=True). This isolates the instrument's child "
        "processes (e.g. MCP servers) so they can be cleanly killed as a "
        "group on timeout, rather than leaving orphaned children.",
    )

    # Credential filtering is the default for both exec and interactive launches.
    required_env: list[str] | None = Field(
        default=None,
        description="Env vars the instrument needs from the parent environment. "
        "Only these vars plus system essentials (PATH, HOME, etc.) and profile "
        "env entries are passed. None and [] both default to system essentials only.",
    )
    inherit_env: bool = Field(
        default=False,
        description="Explicit migration opt-out: inherit the full parent environment "
        "when required_env is None. This exposes unrelated credentials; prefer "
        "declaring required_env. An explicit required_env list takes precedence.",
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

    error_flag_path: str | None = Field(
        default=None,
        description="JSON-only dot-path to a terminal boolean error flag. "
        "When exactly true, execution fails even at a successful exit code. "
        "Unset preserves exit-code-based behavior.",
    )

    # For JSONL format: how to find the completion event
    completion_event_type: str | None = Field(
        default=None,
        description="JSONL event type that signals completion, "
        "e.g. 'turn.completed', 'item.completed'",
    )
    event_type_key: str = Field(
        default="type",
        description="JSONL key that carries each event's type, compared "
        "against completion_event_type. Most CLIs use 'type'; Antigravity's "
        "stream-json uses 'event'.",
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
    cache_read_tokens_path: str | None = Field(
        default=None,
        description=(
            "JSON dot-path to the count of input tokens served from the "
            "provider's prompt cache (Anthropic `cache_read_input_tokens`, "
            "OpenAI `cached_input_tokens`). These tokens are billed at the "
            "cache rate, not the input rate, and are the first number a "
            "token-efficiency change must move; unset means the harness "
            "does not report them, and the sheet records 0."
        ),
    )
    cache_write_tokens_path: str | None = Field(
        default=None,
        description=(
            "JSON dot-path to the count of input tokens written into the "
            "provider's prompt cache this attempt (Anthropic "
            "`cache_creation_input_tokens`, OpenAI `cache_write_input_tokens`)."
        ),
    )
    model_usage_path: str | None = Field(
        default=None,
        description=(
            "JSON dot-path to an object keyed by model id that the harness "
            "reports for the attempt (Claude Code `modelUsage`). The key with "
            "the most `outputTokens` is recorded as the OBSERVED model, so a "
            "sheet that named no model still shows what ran (GH #538)."
        ),
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
    unavailable_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns in the instrument's own startup refusal "
        "meaning it cannot run on this machine at all: no provider configured, "
        "provider not found, backend not reachable (GH #389). A match types the "
        "attempt INSTRUMENT_UNAVAILABLE, so the sheet advances its fallback chain "
        "on the first attempt without charging a retry, exactly as a missing "
        "binary does. Anchor each pattern to the harness's real refusal text; an "
        "agent that merely prints similar words after it has started is retried "
        "on the same entry only if no pattern here matches.",
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

    @field_validator(
        "rate_limit_patterns",
        "auth_error_patterns",
        "unavailable_patterns",
        "capacity_patterns",
        "timeout_patterns",
        "crash_patterns",
        "stale_patterns",
    )
    @classmethod
    def _validate_error_regexes(cls, v: list[str]) -> list[str]:
        """Reject an invalid regex at profile-load time, not mid-attempt.

        ``_classify_output_errors`` runs every pattern against a failed
        attempt's output; an invalid one would raise there, turning a
        classifiable failure into an unclassified crash.
        """
        import re

        for pattern in v:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid error pattern regex {pattern!r}: {e}") from e
        return v


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


class InteractiveUsageConfig(BaseModel):
    """Where an interactive harness records per-session token usage (GH #533).

    A tmux session has no JSON stdout, so the backend picks the session id
    itself, launches the harness with ``session_id_flag``, and after the sheet
    reads the harness's own session record. Sessions whose profile has no
    block, or whose record is missing or empty, are reported UNMEASURED
    (``cost_uncertain``), never as a free zero.
    """

    model_config = ConfigDict(extra="forbid")

    session_id_flag: str = Field(
        min_length=1,
        description="CLI flag that lets the launcher choose the session id, "
        "e.g. '--session-id'. The backend passes it with a fresh UUID.",
    )
    record_path: str = Field(
        min_length=1,
        description="Path or glob of the harness's per-session usage record. "
        "'~' is expanded and '{session_id}' is replaced by the launched "
        "session id, e.g. '~/.claude/projects/*/{session_id}.jsonl'. The "
        "glob avoids re-deriving a directory slug; the newest match is read.",
    )
    record_filter: dict[str, str] = Field(
        default_factory=dict,
        description="JSON-path -> required string value; only record lines "
        "matching every pair are counted, e.g. {'type': 'assistant'}.",
    )
    dedupe_key_path: str | None = Field(
        default=None,
        description="JSON path of the key shared by every line of one API "
        "response (e.g. 'message.id'). The same key repeats across streamed "
        "chunks; the last line per key is counted once. Unset counts every "
        "matching line.",
    )
    model_path: str | None = Field(
        default=None,
        description="JSON path of the model name on a record line; the model "
        "with the most output tokens becomes the observed model.",
    )
    ignore_models: list[str] = Field(
        default_factory=list,
        description="Model names whose lines carry no real usage and are "
        "skipped, e.g. ['<synthetic>'].",
    )
    input_tokens_path: str | None = Field(
        default=None, description="JSON path of input tokens on a record line.",
    )
    output_tokens_path: str | None = Field(
        default=None, description="JSON path of output tokens on a record line.",
    )
    cache_read_tokens_path: str | None = Field(
        default=None,
        description="JSON path of prompt-cache read tokens on a record line.",
    )
    cache_write_tokens_path: str | None = Field(
        default=None,
        description="JSON path of prompt-cache write tokens on a record line.",
    )

    @model_validator(mode="after")
    def _require_a_token_path(self) -> InteractiveUsageConfig:
        """A usage block that reads no token field could only report zero."""
        if not (self.input_tokens_path or self.output_tokens_path):
            raise ValueError(
                "interactive.usage needs input_tokens_path or output_tokens_path"
            )
        if "{session_id}" not in self.record_path:
            raise ValueError("interactive.usage.record_path must contain {session_id}")
        return self


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
    usage: InteractiveUsageConfig | None = Field(
        default=None,
        description="Where this harness records per-session token usage "
        "(GH #533). Unset means interactive sheets on this instrument report "
        "unmeasured usage (cost_uncertain), never zero.",
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

    post_exit_drain_grace_seconds: float = Field(
        default=3.0,
        gt=0,
        description="Maximum time to drain inherited pipes after the CLI parent exits",
    )
    first_output_timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Seconds the CLI may run with ZERO bytes on stdout and stderr "
        "before the attempt is ended as a stall (GH #434). A streaming agent "
        "emits its init event within seconds of a healthy start, so silence "
        "is a dead session, not thinking; the attempt ends early with "
        "exit_reason 'timeout' and a 'Stale execution' marker so the baton "
        "retries/falls back instead of burning the whole sheet timeout. "
        "None = no first-output bound; set it on profiles whose output streams "
        "(jsonl), never on json/text profiles that emit only at the end.",
    )

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


def validate_openai_response_format(value: object) -> dict[str, Any]:
    """Validate an OpenAI-compatible ``response_format`` object.

    Single source of truth for the structured-output wire contract, shared
    by the ``HttpProfile`` config validator (profile YAML) and the generic
    backend setter (per-sheet ``instrument_config``). Accepts exactly the
    two structured-output shapes OpenAI-compatible servers support:

    - ``{"type": "json_object"}``
    - ``{"type": "json_schema", "json_schema": {"name": str, "schema": dict,
      "strict": bool (optional)}}``

    Unknown keys at either level are allowed (provider extensions) and are
    forwarded unchanged — validation catches authoring mistakes, it does
    not rewrite the object. Returns a deep copy so later mutation of the
    source config can never alias into a request payload.

    Raises:
        ValueError: with a message naming ``response_format`` for any
            invalid shape. Callers must surface, never swallow, this.
    """
    if not isinstance(value, dict):
        raise ValueError(
            "response_format must be a mapping (dict) with a 'type' key, "
            f"got {type(value).__name__}"
        )
    fmt_type = value.get("type")
    if fmt_type not in ("json_object", "json_schema"):
        raise ValueError(
            "response_format.type must be 'json_object' or 'json_schema' "
            f"(OpenAI-compatible structured output), got {fmt_type!r}"
        )
    if fmt_type == "json_schema":
        inner = value.get("json_schema")
        if not isinstance(inner, dict):
            raise ValueError(
                "response_format of type json_schema requires a "
                "'json_schema' mapping with 'name' and 'schema', got "
                f"{type(inner).__name__}"
            )
        name = inner.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(
                "response_format.json_schema.name must be a non-empty "
                f"string, got {name!r}"
            )
        schema = inner.get("schema")
        if not isinstance(schema, dict):
            raise ValueError(
                "response_format.json_schema.schema must be a mapping "
                "(a JSON Schema document), got "
                f"{type(schema).__name__}"
            )
        strict = inner.get("strict")
        if strict is not None and not isinstance(strict, bool):
            raise ValueError(
                "response_format.json_schema.strict must be a boolean "
                f"when present, got {strict!r}"
            )
    return copy.deepcopy(value)


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
    response_format: dict[str, Any] | None = Field(
        default=None,
        description="Opt-in structured output: an OpenAI-compatible "
        "response_format object ({type: json_object} or {type: json_schema, "
        "json_schema: {name, schema}}). Validated at profile load and "
        "forwarded unchanged in every request payload. None (the default) "
        "omits the key entirely — the legacy four-key payload. Per-sheet "
        "instrument_config.response_format overrides this for that sheet "
        "(explicit null opts out).",
    )

    @field_validator("response_format")
    @classmethod
    def _validate_response_format(
        cls, v: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Reject invalid structured-output shapes at config-load time."""
        if v is None:
            return None
        return validate_openai_response_format(v)


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

    # Loader-owned metadata from the same bytes parsed into this profile.
    # Not score fields, not included in serialization, and never a settings body.
    _source_path: Path | None = PrivateAttr(default=None)
    _source_sha256: str | None = PrivateAttr(default=None)

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

    class_hints: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Where `mzt instruments classes write` seats this profile in a "
            "generated class chain, per class: `{strong: 'after opencode', "
            "fast: first, review: last}`. The value is `first`, `last`, "
            "`after <profile>` or `before <profile>`; a named profile that is "
            "not in the chain seats the hint last. Only profiles with a hint "
            "are considered beyond the shipped ones, and a hinted profile "
            "still has to pass the class's capability and availability probe "
            "(GH #469)."
        ),
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

    # Reasoning effort (GH #535): how instrument_config.effort reaches the harness
    effort: EffortMapping | None = Field(
        default=None,
        description=(
            "The profile's reasoning-effort mapping: which Marianne levels "
            "(minimal, low, medium, high, xhigh, max) it accepts, the native "
            "word for each, and the delivery shape (flag, config_override or "
            "body_field). With it a sheet may set instrument_config.effort; "
            "without it (None) a sheet that sets the key is refused by "
            "`mzt validate` (V335) and at dispatch, even when extra_flags "
            "happens to carry an effort flag. Sheets that do not set the key "
            "are unaffected either way. A model row's effort_levels overrides "
            "effort.levels for that model."
        ),
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

    def effort_levels_for(self, model: str | None) -> dict[EffortLevel, str]:
        """Marianne level -> native word for ``model`` ({} = nothing mapped).

        A model row with ``effort_levels`` replaces the profile's map; a model
        not listed (or None, the profile default) uses the profile's map.
        """
        if self.effort is None:
            return {}
        name = model if model is not None else self.default_model
        if name is not None:
            for row in self.models:
                if row.name == name and row.effort_levels is not None:
                    return dict(row.effort_levels)
        return dict(self.effort.levels)

    def accepted_effort_levels(
        self, *, model: str | None, schema_mode: bool, interactive: bool = False,
    ) -> list[str]:
        """The Marianne levels a sheet may set here, in vocabulary order."""
        if self.effort is None or (interactive and not self.effort.interactive):
            return []
        mapped = self.effort_levels_for(model)
        allowed = (
            set(self.effort.schema_levels)
            if schema_mode and self.effort.schema_levels is not None
            else set(mapped)
        )
        return [lv for lv in EFFORT_LEVELS if lv in mapped and lv in allowed]

    def effort_refusal(
        self, level: str, *, model: str | None, schema_mode: bool,
        interactive: bool = False,
    ) -> str | None:
        """Why this profile cannot run a sheet at ``level``, or None when it can.

        The ONE wording shared by `mzt validate` (V335), dispatch and the
        backends' command builders, so the author reads the same sentence
        wherever the refusal surfaces. It always ends with the accepted levels.
        """
        if self.effort is None:
            return (
                "declares no reasoning-effort mapping (the profile's effort: "
                f"block), so instrument_config.effort {level!r} cannot be "
                "applied; accepted levels: none"
            )
        if interactive and not self.effort.interactive:
            return (
                "cannot carry instrument_config.effort on an interactive launch "
                "(the profile's effort.interactive is false: the harness's "
                f"interactive command has no such option), so {level!r} cannot "
                "be applied; set interactive: false on that sheet; accepted "
                "levels: none"
            )
        accepted = self.accepted_effort_levels(
            model=model, schema_mode=schema_mode, interactive=interactive,
        )
        if level in accepted:
            return None
        name = model if model is not None else self.default_model
        row = next(
            (r for r in self.models if name is not None and r.name == name), None,
        )
        # Name the model whenever it decides: its row carries effort_levels,
        # or the profile maps nothing itself (levels vary by model).
        model_decides = (
            row is not None and row.effort_levels is not None
        ) or not self.effort.levels
        on_model = f" on model {name!r}" if name is not None and model_decides else ""
        if not accepted:
            return (
                f"maps no reasoning-effort level{on_model}, so "
                f"instrument_config.effort {level!r} cannot be applied; "
                "accepted levels: none"
            )
        if schema_mode and level in self.effort_levels_for(model):
            return (
                f"does not run a schema sheet (response_format) at effort "
                f"{level!r}: the profile's effort.schema_levels lists the "
                "levels measured to return structured output; "
                f"accepted levels: {', '.join(accepted)}"
            )
        return (
            f"does not map effort {level!r}{on_model}"
            f"{' on a schema sheet (response_format)' if schema_mode else ''}; "
            f"accepted levels: {', '.join(accepted)}"
        )

    def native_effort(self, level: str, *, model: str | None) -> str:
        """The harness word for ``level``; callers check ``effort_refusal`` first."""
        return self.effort_levels_for(model)[level]  # type: ignore[index]

    @field_validator("class_hints")
    @classmethod
    def _validate_class_hints(cls, value: dict[str, str]) -> dict[str, str]:
        """Each hint is `first`, `last`, `after <profile>` or `before <profile>`."""
        import re

        for class_name, hint in value.items():
            if not re.match(r"^(first|last|(?:after|before)\s+\S+)$", hint.strip()):
                raise ValueError(
                    f"class_hints.{class_name}: {hint!r} is not one of "
                    "first, last, after <profile>, before <profile>"
                )
        return value

    @model_validator(mode="after")
    def _validate_effort_mapping(self) -> InstrumentProfile:
        """The effort mapping agrees with the delivery kind and the fixed flags."""
        rows = [r.name for r in self.models if r.effort_levels is not None]
        if self.effort is None:
            if rows:
                raise ValueError(
                    f"models {rows} declare effort_levels but the profile has no "
                    "effort: block saying how the level is delivered"
                )
            return self
        effort = self.effort
        if (effort.delivery == "body_field") != (self.kind == "http"):
            raise ValueError(
                f"effort.delivery {effort.delivery!r} does not fit kind "
                f"{self.kind!r}: HTTP profiles use body_field, CLI profiles use "
                "flag or config_override"
            )
        if self.cli is None:
            return self
        command = self.cli.command
        native = {
            v for m in [effort.levels, *(
                r.effort_levels for r in self.models if r.effort_levels is not None
            )] for v in m.values()
        }
        schema_native = {
            v for m in [effort.levels, *(
                r.effort_levels for r in self.models if r.effort_levels is not None
            )] for lv, v in m.items()
            if effort.schema_levels is None or lv in effort.schema_levels
        }
        schema_flags = (
            command.schema_extra_flags if command.schema_extra_flags is not None
            else command.extra_flags
        )
        schema_name = (
            "schema_extra_flags" if command.schema_extra_flags is not None
            else "extra_flags (which schema sheets inherit)"
        )
        checks: list[tuple[str, list[str], set[str], str]] = [
            ("extra_flags", command.extra_flags, native, ""),
            (schema_name, schema_flags, schema_native, " on a schema sheet"),
        ]
        if self.cli.interactive is not None:
            checks.append(
                ("cli.interactive.extra_args", self.cli.interactive.extra_args, native, ""),
            )
        for name, flags, allowed, where in checks:
            for default in effort.defaults_in(flags):
                if default not in allowed:
                    raise ValueError(
                        f"{name} sets effort {default!r} through {effort.flag}, "
                        f"which no effort level this profile accepts{where} "
                        f"maps to: {', '.join(sorted(allowed)) or 'none'}"
                    )
        return self

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
