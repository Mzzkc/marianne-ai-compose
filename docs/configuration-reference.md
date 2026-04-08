# Configuration Reference

Complete reference for every field in Marianne score YAML files. All types, defaults,
and constraints are extracted directly from the Pydantic v2 config models in
`src/marianne/core/config/`.

---

## Table of Contents

- [Top-Level Fields](#top-level-fields)
- [instruments](#instruments)
  - [InstrumentDef Sub-Config](#instrumentdef-sub-config)
- [Instrument Fallbacks](#instrument-fallbacks)
- [movements](#movements)
  - [MovementDef Sub-Config](#movementdef-sub-config)
- [workspace_lifecycle](#workspace_lifecycle)
- [backend](#backend)
  - [Ollama Sub-Config](#ollama-sub-config)
  - [Recursive Light Sub-Config](#recursive-light-sub-config)
- [bridge](#bridge)
  - [MCP Server Sub-Config](#mcp-server-sub-config)
- [sheet](#sheet)
  - [SkipWhenCommand Sub-Config](#skipwhencommand-sub-config)
  - [InjectionItem Sub-Config](#injectionitem-sub-config)
- [spec](#spec)
- [prompt](#prompt)
- [parallel](#parallel)
- [retry](#retry)
- [rate_limit](#rate_limit)
- [circuit_breaker](#circuit_breaker)
- [cost_limits](#cost_limits)
- [stale_detection](#stale_detection)
- [cross_sheet](#cross_sheet)
- [validations](#validations)
- [isolation](#isolation)
- [grounding](#grounding)
  - [Grounding Hook Sub-Config](#grounding-hook-sub-config)
- [conductor](#conductor)
  - [Conductor Preferences Sub-Config](#conductor-preferences-sub-config)
- [concert](#concert)
- [on_success (Post-Success Hooks)](#on_success-post-success-hooks)
- [notifications](#notifications)
- [learning](#learning)
  - [Exploration Budget Sub-Config](#exploration-budget-sub-config)
  - [Entropy Response Sub-Config](#entropy-response-sub-config)
  - [Auto Apply Sub-Config](#auto-apply-sub-config)
- [checkpoints](#checkpoints)
  - [Checkpoint Trigger Sub-Config](#checkpoint-trigger-sub-config)
- [ai_review](#ai_review)
- [logging](#logging)
- [feedback](#feedback)
- [State and Misc](#state-and-misc)
- [DaemonConfig (Conductor)](#daemonconfig-conductor)
  - [Socket Sub-Config](#socket-sub-config)
  - [Resource Limits Sub-Config](#resource-limits-sub-config)
  - [Semantic Learning Sub-Config](#semantic-learning-sub-config)

---

## Top-Level Fields

*Source: `src/marianne/core/config/job.py` — `JobConfig`*

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | **required** | Unique score name |
| `description` | `str \| None` | `None` | Human-readable description |
| `workspace` | `Path` | `./workspace` | Output directory. Resolved to absolute path at construction time. |
| `instrument` | `str \| None` | `None` | Named instrument to use (e.g., `claude-code`, `gemini-cli`). Alternative to `backend.type`. Run `mzt instruments list` to see available instruments. Cannot be used together with `backend`. |
| `instrument_config` | `dict` | `{}` | Per-score overrides for the named instrument's defaults (e.g., `model`, `timeout_seconds`). Only valid when `instrument` is set. |
| `instruments` | `dict[str, InstrumentDef]` | `{}` | Named instrument definitions local to this score. Declares reusable aliases referencing registered instrument profiles with optional overrides. Referenced by name in per-sheet or per-movement `instrument:` fields. See [instruments](#instruments). |
| `movements` | `dict[int, MovementDef]` | `{}` | Movement declarations. Map of movement number to MovementDef. Each movement can specify a name, instrument, instrument config, and voice count. See [movements](#movements). |
| `instrument_fallbacks` | `list[str]` | `[]` | Score-level default fallback instrument chain. Tried in order when the primary instrument is unavailable or rate-limited to exhaustion. Each entry is an instrument name (profile or score alias). See [Instrument Fallbacks](#instrument-fallbacks). |

```yaml
# Using instrument: (recommended for new scores)
instrument: gemini-cli
instrument_config:
  model: gemini-2.5-flash
  timeout_seconds: 600

# Using backend: (original syntax, still fully supported)
backend:
  type: claude_cli
  skip_permissions: true
```

> **Note:** `instrument:` and `backend:` are mutually exclusive. Use one or the other. If neither is specified, Marianne defaults to `claude_cli`.

---

## instruments

*Source: `src/marianne/core/config/job.py` — `JobConfig.instruments`*

Named instrument definitions local to this score. Each entry declares a reusable alias that
references a registered instrument profile with optional configuration overrides. These aliases
can then be used in `instrument:` fields on per-sheet or per-movement assignments.

```yaml
instruments:
  fast-writer:
    profile: gemini-cli
    config:
      model: gemini-2.5-flash
      timeout_seconds: 300
  deep-thinker:
    profile: claude-code
    config:
      timeout_seconds: 3600
```

### InstrumentDef Sub-Config

*Source: `src/marianne/core/config/job.py` — `InstrumentDef`*

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `profile` | `str` | **required** | min_length=1 | Name of the registered instrument profile (e.g., `gemini-cli`, `claude-code`). Must match a profile loaded by `mzt instruments list`. |
| `config` | `dict` | `{}` | | Configuration overrides merged with the profile's defaults. Flat key-value pairs. |

---

## Instrument Fallbacks

When a primary instrument is unavailable or rate-limited to exhaustion, Marianne can
automatically try fallback instruments in order. Fallbacks are specified as a list
of instrument names (profile names or score-local aliases).

Fallback chains can be set at three levels — per-sheet overrides replace (not merge
with) inherited chains:

```yaml
# Score-level default fallbacks (all sheets inherit this)
instrument: claude-code
instrument_fallbacks: [gemini-cli, codex-cli]

movements:
  2:
    name: Implementation
    instrument: gemini-cli
    # Movement-level fallbacks override score-level for sheets in this movement
    instrument_fallbacks: [claude-code, aider]

sheet:
  size: 1
  total_items: 5
  # Per-sheet fallback override (replaces, does not merge)
  per_sheet_fallbacks:
    3: [codex-cli]    # Sheet 3 only falls back to codex-cli
```

**Fallback resolution precedence** (highest wins):
1. `sheet.per_sheet_fallbacks[N]` — per-sheet override
2. `movements.N.instrument_fallbacks` — per-movement default
3. Top-level `instrument_fallbacks` — score default

The `mzt validate` command checks fallback names against known profiles (V211)
and warns if any name doesn't match a registered instrument.

---

## movements

*Source: `src/marianne/core/config/job.py` — `JobConfig.movements`*

Movement declarations. Map of movement number to MovementDef. Movements are sequential execution
phases. Each movement can specify a name (for display in `mzt status`), an instrument
(overriding the score default), instrument configuration, and a voice count (shorthand for `fan_out`).

Movement numbers must be positive integers within the range `[1, total_items]`.

```yaml
movements:
  1:
    name: Planning
    instrument: claude-code
  2:
    name: Implementation
    voices: 3
    instrument: gemini-cli
    instrument_config:
      model: gemini-2.5-flash
  3:
    name: Review
    instrument: claude-code
    instrument_config:
      timeout_seconds: 600
```

### MovementDef Sub-Config

*Source: `src/marianne/core/config/job.py` — `MovementDef`*

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str \| None` | `None` | | Human-readable name for this movement. Shown in `mzt status` output. |
| `instrument` | `str \| None` | `None` | min_length=1 | Instrument for all sheets in this movement. Overrides the score-level `instrument:` but is overridden by per-sheet assignments. Can reference a score-local `instruments:` alias or a registered profile name. |
| `instrument_config` | `dict` | `{}` | | Instrument configuration overrides for this movement. Merged with the resolved instrument's defaults. |
| `voices` | `int \| None` | `None` | `>= 1` | Number of parallel voices in this movement. Shorthand for `fan_out: {N: voices}`. |
| `instrument_fallbacks` | `list[str]` | `[]` | | Default fallback chain for sheets in this movement. Overrides score-level `instrument_fallbacks`. |

**Instrument resolution precedence** (highest to lowest):
1. `sheet.per_sheet_instruments` (per-sheet override)
2. `sheet.instrument_map` (batch assignment)
3. `movements.N.instrument` (per-movement)
4. Top-level `instrument:` (score default)
5. `backend.type` (legacy)
6. `claude_cli` (built-in default)

---

## workspace_lifecycle

*Source: `src/marianne/core/config/workspace.py` — `WorkspaceLifecycleConfig`*

Controls how workspace files are handled across score iterations, particularly for self-chaining scores that reuse the same workspace.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `archive_on_fresh` | `bool` | `false` | | Archive workspace files when `--fresh` flag is used. Moves non-preserved files to a numbered archive subdirectory. |
| `archive_dir` | `str` | `"archive"` | | Subdirectory within workspace for archive storage. |
| `archive_naming` | `"iteration" \| "timestamp"` | `"iteration"` | | Naming scheme for archive directories. `iteration` reads `.iteration` file, `timestamp` uses current time. |
| `max_archives` | `int` | `0` | `>= 0` | Maximum archive directories to keep. 0 = unlimited. When exceeded, oldest archives are deleted. |
| `preserve_patterns` | `list[str]` | `[".iteration", ".marianne-*", ".coverage", "archive/**", ".worktrees/**"]` | | Glob patterns for files/directories to preserve (not archive). Matched against paths relative to workspace root. |

```yaml
workspace_lifecycle:
  archive_on_fresh: true
  archive_dir: archive
  max_archives: 10
  preserve_patterns:
    - ".iteration"
    - ".marianne-*"
    - "archive/**"
```

---

## backend

*Source: `src/marianne/core/config/backend.py` — `BackendConfig`*

Uses a flat structure with cross-field validation. Fields marked with a backend prefix (e.g., `[claude_cli]`) only take effect when `type` matches that backend. Setting fields for an unselected backend emits a warning.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `type` | `"claude_cli" \| "anthropic_api" \| "recursive_light" \| "ollama"` | `"claude_cli"` | | Backend type |
| `skip_permissions` | `bool` | `true` | | **[claude_cli]** Skip permission prompts for unattended execution. Maps to `--dangerously-skip-permissions`. |
| `disable_mcp` | `bool` | `true` | | **[claude_cli]** Disable MCP server loading for faster, isolated execution (~2x speedup). Maps to `--strict-mcp-config {}`. Set to `false` to use MCP servers. |
| `output_format` | `"json" \| "text" \| "stream-json"` | `"text"` | | **[claude_cli]** Claude CLI output format. `text` for human-readable, `json` for structured, `stream-json` for streaming events. |
| `cli_model` | `str \| None` | `None` | | **[claude_cli]** Model for Claude CLI. Maps to `--model` flag. If `None`, uses Claude Code's default. Example: `"claude-sonnet-4-5-20250929"` |
| `allowed_tools` | `list[str] \| None` | `None` | | **[claude_cli]** Restrict Claude to specific tools. Maps to `--allowedTools`. Example: `["Read", "Grep", "Glob"]` for read-only execution. |
| `system_prompt_file` | `Path \| None` | `None` | | **[claude_cli]** Path to custom system prompt file. Maps to `--system-prompt`. |
| `working_directory` | `Path \| None` | `None` | | Working directory for execution. If `None`, uses the directory containing the config file. |
| `timeout_seconds` | `float` | `1800.0` | `> 0` | Maximum time per prompt execution (seconds). Default: 30 minutes. |
| `timeout_overrides` | `dict[int, float]` | `{}` | | Per-sheet timeout overrides. Map of `sheet_num -> timeout_seconds`. Unlisted sheets use `timeout_seconds`. |
| `cli_extra_args` | `list[str]` | `[]` | | **[claude_cli]** Escape hatch for CLI flags not yet exposed as named options. Applied last, can override other settings. |
| `max_output_capture_bytes` | `int` | `51200` | `> 0` | Maximum bytes of stdout/stderr to capture per sheet for diagnostics (50KB default). |
| `model` | `str` | `"claude-sonnet-4-5-20250929"` | | **[anthropic_api]** Model ID for Anthropic API |
| `api_key_env` | `str` | `"ANTHROPIC_API_KEY"` | | **[anthropic_api]** Environment variable containing API key |
| `max_tokens` | `int` | `16384` | `>= 1` | **[anthropic_api]** Maximum tokens for API response |
| `temperature` | `float` | `0.7` | `0.0–1.0` | **[anthropic_api]** Sampling temperature |
| `recursive_light` | `RecursiveLightConfig` | *(see sub-config)* | | **[recursive_light]** Recursive Light backend configuration |
| `ollama` | `OllamaConfig` | *(see sub-config)* | | **[ollama]** Ollama backend configuration |

```yaml
backend:
  type: claude_cli
  skip_permissions: true
  disable_mcp: true
  timeout_seconds: 1800
  timeout_overrides:
    5: 3600    # Sheet 5 gets 1 hour
  cli_model: "claude-sonnet-4-5-20250929"
  allowed_tools: ["Read", "Grep", "Glob", "Write", "Edit"]
```

### Ollama Sub-Config

*Source: `src/marianne/core/config/backend.py` — `OllamaConfig`*

Nested under `backend.ollama`. Only meaningful when `backend.type` is `"ollama"`.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `base_url` | `str` | `"http://localhost:11434"` | | Ollama server base URL |
| `model` | `str` | `"llama3.1:8b"` | | Ollama model to use. Must support tool calling. |
| `num_ctx` | `int` | `32768` | `>= 4096` | Context window size. Minimum 32K recommended for Claude Code tools. |
| `dynamic_tools` | `bool` | `true` | | Enable dynamic toolset loading to optimize context |
| `compression_level` | `"minimal" \| "moderate" \| "aggressive"` | `"moderate"` | | Tool schema compression level |
| `timeout_seconds` | `float` | `300.0` | `> 0` | Request timeout for Ollama API calls |
| `keep_alive` | `str` | `"5m"` | | Keep model loaded in memory for this duration |
| `max_tool_iterations` | `int` | `10` | `1–50` | Maximum tool call iterations per execution |
| `health_check_timeout` | `float` | `10.0` | | Timeout for health check requests |

### Recursive Light Sub-Config

*Source: `src/marianne/core/config/backend.py` — `RecursiveLightConfig`*

Nested under `backend.recursive_light`. Only meaningful when `backend.type` is `"recursive_light"`.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `endpoint` | `str` | `"http://localhost:8080"` | | Base URL for the Recursive Light API server |
| `user_id` | `str \| None` | `None` | | Unique identifier for this Marianne instance (generates UUID if not set) |
| `timeout` | `float` | `30.0` | `> 0` | Request timeout in seconds for RL API calls |

---

## bridge

*Source: `src/marianne/core/config/backend.py` — `BridgeConfig`*

The bridge enables Ollama models to use MCP tools through a proxy service. Set at the top level (not inside `backend`). Defaults to `None` (bridge disabled).

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable bridge mode (Ollama with MCP tools) |
| `mcp_proxy_enabled` | `bool` | `true` | | Enable MCP server proxy for tool access |
| `mcp_servers` | `list[MCPServerConfig]` | `[]` | | MCP servers to connect to |
| `hybrid_routing_enabled` | `bool` | `false` | | Enable hybrid routing between Ollama and Claude |
| `complexity_threshold` | `float` | `0.7` | `0.0–1.0` | Complexity threshold for routing to Claude |
| `fallback_to_claude` | `bool` | `true` | | Fall back to Claude if Ollama execution fails |
| `context_budget_percent` | `int` | `75` | `10–95` | Percent of context window for tools (rest for conversation) |

### MCP Server Sub-Config

*Source: `src/marianne/core/config/backend.py` — `MCPServerConfig`*

Each entry in `bridge.mcp_servers`.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | **required** | | Unique name for this MCP server |
| `command` | `str` | **required** | | Command to run the MCP server |
| `args` | `list[str]` | `[]` | | Command line arguments |
| `env` | `dict[str, str]` | `{}` | Blocked keys: PATH, LD_PRELOAD, PYTHONPATH, API keys, etc. | Environment variables for the server |
| `working_dir` | `str \| None` | `None` | | Working directory for the server |
| `timeout_seconds` | `float` | `30.0` | | Timeout for server operations |

```yaml
bridge:
  enabled: true
  mcp_proxy_enabled: true
  mcp_servers:
    - name: filesystem
      command: "npx"
      args: ["-y", "@anthropic/mcp-server-filesystem", "/home/user"]
  hybrid_routing_enabled: true
  complexity_threshold: 0.7
```

---

## sheet

*Source: `src/marianne/core/config/job.py` — `SheetConfig`*

Defines how the work is divided into sheets (execution units).

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `size` | `int` | **required** | `>= 1` | Number of items per sheet |
| `total_items` | `int` | **required** | `>= 1` | Total number of items to process |
| `start_item` | `int` | `1` | `>= 1` | First item number (1-indexed) |
| `dependencies` | `dict[int, list[int]]` | `{}` | No self-references | Sheet dependency declarations. Map of `sheet_num -> [prerequisites]`. Sheets without entries are independent. |
| `skip_when` | `dict[int, str]` | `{}` | | Conditional skip rules. Map of `sheet_num -> condition`. Expression accesses `sheets` dict and `job` state. If truthy, sheet is skipped. |
| `skip_when_command` | `dict[int, SkipWhenCommand]` | `{}` | | Command-based conditional skip rules. Map of `sheet_num -> SkipWhenCommand`. The command runs via shell; exit 0 = skip the sheet, non-zero = run it. Fail-open on timeout or error. |
| `prompt_extensions` | `dict[int, list[str]]` | `{}` | | Per-sheet prompt extensions. Map of `sheet_num -> list of extension strings`. Applied in addition to score-level `prompt.prompt_extensions`. |
| `prelude` | `list[InjectionItem]` | `[]` | | Shared context injected into ALL sheets. Each item references a file and a category (`context`, `skill`, or `tool`). File paths support Jinja templating. Files are read at sheet execution time. |
| `cadenzas` | `dict[int, list[InjectionItem]]` | `{}` | | Per-sheet context injections. Map of `sheet_num -> list of InjectionItems`. Applied in addition to prelude items for the specified sheet. |
| `fan_out` | `dict[int, int]` | `{}` | Requires `size=1`, `start_item=1` | Fan-out declarations. Map of `stage_num -> instance_count`. Creates parallel instances of stages. Cleared after expansion. |
| `fan_out_stage_map` | `dict[int, dict[str, int]] \| None` | `None` | | Per-sheet fan-out metadata, populated by expansion. Survives serialization for resume support. |
| `spec_tags` | `dict[int, list[str]]` | `{}` | | Per-sheet spec fragment tag filters. Map of `sheet_num -> list of tags`. Only fragments matching at least one tag are injected for that sheet. Sheets without entries receive all fragments. |
| `per_sheet_instruments` | `dict[int, str]` | `{}` | Positive int keys | Per-sheet instrument overrides. Map of `sheet_num -> instrument_name`. Highest precedence in the instrument resolution chain. Example: `{3: 'gemini-cli', 5: 'codex-cli'}`. |
| `per_sheet_instrument_config` | `dict[int, dict]` | `{}` | | Per-sheet instrument configuration overrides. Map of `sheet_num -> config dict`. Merged with the resolved instrument's defaults for that sheet. Example: `{3: {model: 'gemini-2.5-flash'}}`. |
| `instrument_map` | `dict[str, list[int]]` | `{}` | No duplicate sheets | Batch instrument assignment. Map of `instrument_name -> list of sheet numbers`. Overrides score-level instrument for listed sheets. Overridden by `per_sheet_instruments`. A sheet cannot appear in multiple instrument lists. |
| `per_sheet_fallbacks` | `dict[int, list[str]]` | `{}` | Positive int keys | Per-sheet instrument fallback overrides. Map of `sheet_num -> list of fallback instrument names`. Replaces (does not merge with) inherited fallback chains. See [Instrument Fallbacks](#instrument-fallbacks). |

```yaml
sheet:
  size: 1
  total_items: 7
  dependencies:
    2: [1]
    3: [1]
    4: [2, 3]
  skip_when:
    5: "sheets.get(3) and sheets[3].validation_passed"
  skip_when_command:
    8:
      command: 'grep -q "PHASES: 1" {workspace}/plan.md'
      description: "Skip phase 2 if plan only has 1 phase"
      timeout_seconds: 5
  prelude:
    - file: docs/architecture.md
      as: context
    - file: .claude/skills/debugging.md
      as: skill
  cadenzas:
    3:
      - file: "{{ workspace }}/02-output.md"
        as: context
  fan_out:
    2: 3    # 3 parallel instances of stage 2
  # Multi-instrument assignment
  per_sheet_instruments:
    3: gemini-cli         # Sheet 3 uses Gemini
  per_sheet_instrument_config:
    3:
      model: gemini-2.5-flash
  instrument_map:
    claude-code: [1, 2, 4, 5, 6, 7]
    gemini-cli: [3]
```

**Computed properties** (not configurable):
- `total_sheets` — calculated as `ceil((total_items - start_item + 1) / size)`
- `total_stages` — original stage count before fan-out expansion

### SkipWhenCommand Sub-Config

*Source: `src/marianne/core/config/execution.py` — `SkipWhenCommand`*

Defines a command-based conditional skip rule for sheet execution. When the command exits 0, the sheet is **skipped**. When the command exits non-zero, the sheet **runs**. On timeout or error, the sheet runs (fail-open for safety).

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `command` | `str` | **required** | | Shell command to evaluate. Exit 0 = skip the sheet. Supports `{workspace}` template expansion. |
| `description` | `str \| None` | `None` | | Human-readable reason for the skip condition |
| `timeout_seconds` | `float` | `10.0` | `> 0`, `<= 60` | Maximum seconds to wait for command. Fail-open on timeout. |

```yaml
sheet:
  size: 1
  total_items: 10
  skip_when_command:
    # Skip sheet 4 if tests already pass
    4:
      command: "cd {workspace} && pytest tests/ -x --tb=no -q"
      description: "Skip if tests already pass"
      timeout_seconds: 30
    # Skip sheet 8 if a marker file exists
    8:
      command: "test -f {workspace}/phase2-complete.marker"
      description: "Skip phase 2 cleanup if already done"
```

### InjectionItem Sub-Config

*Source: `src/marianne/core/config/job.py` — `InjectionItem`*

A single injection item referencing a file with a category. Used in `prelude` (all sheets) and `cadenzas` (per-sheet) to inject file content into prompts at category-appropriate locations.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `file` | `str` | **required** | | Path to the file to inject. Supports Jinja templating (e.g., `{{ workspace }}/context.md`). |
| `as` | `InjectionCategory` | **required** | `"context"`, `"skill"`, or `"tool"` | Category determining prompt placement. See InjectionCategory below. |

**InjectionCategory values:**

| Value | Prompt Placement | Purpose |
|-------|-----------------|---------|
| `context` | After template body | Background knowledge and informational context |
| `skill` | After preamble (before template body) | Capability, methodology, or instructions |
| `tool` | After preamble (before template body) | Executable actions and available tooling |

```yaml
sheet:
  prelude:
    - file: docs/architecture.md
      as: context
    - file: .claude/skills/debugging.md
      as: skill
    - file: tools/lint.sh
      as: tool
  cadenzas:
    3:
      - file: "{{ workspace }}/02-output.md"
        as: context
```

**Key behaviors:**
- Files are read at **sheet execution time**, not config parse time — so dynamic outputs from earlier sheets are available via Jinja-templated paths.
- Missing files for `context` category log a warning and are skipped.
- Missing files for `skill` or `tool` category log an error and are skipped.
- `mzt validate` checks static file paths (V108 warning) but skips Jinja-templated paths that can't be resolved at validation time.

---

## spec

*Source: `src/marianne/core/config/spec.py` — `SpecCorpusConfig`*

Configuration for the specification corpus. Controls where spec fragments are loaded
from and how they are filtered for injection into agent prompts. Spec loading is opt-in —
set `spec_dir` to enable.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `spec_dir` | `str` | `""` | Path to the specification corpus directory, relative to the project root. Empty string disables spec loading. |
| `include_claude_md` | `bool` | `false` | Include CLAUDE.md as a spec fragment. The loader looks for CLAUDE.md in the project root and includes it with `name='claude_md'`. |

```yaml
spec:
  spec_dir: ".marianne/spec"
  include_claude_md: true
```

When `spec_dir` is set, Marianne loads all YAML files in that directory as spec fragments.
Each fragment has a name, tags, kind, and content. Tags are used for per-sheet filtering
via `sheet.spec_tags` in `SheetConfig` — a map of `sheet_num -> list of tags`. Sheets
with spec_tags receive only fragments matching those tags; sheets without spec_tags
receive all fragments.

---

## prompt

*Source: `src/marianne/core/config/job.py` — `PromptConfig`*

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `template` | `str \| None` | `None` | Cannot set both `template` and `template_file` | Inline Jinja2 template |
| `template_file` | `Path \| None` | `None` | Cannot set both `template` and `template_file` | Path to external `.j2` template file |
| `variables` | `dict[str, Any]` | `{}` | | Static variables available in template |
| `stakes` | `str \| None` | `None` | | Motivational stakes section appended to prompt |
| `thinking_method` | `str \| None` | `None` | | Thinking methodology injected into prompt |
| `prompt_extensions` | `list[str]` | `[]` | | Additional directives injected after the Marianne default preamble for all sheets. Each entry is inline text or a file path (.md/.txt). |

For template variable reference, see the [Score Writing Guide](score-writing-guide.md#template-variables-reference).

```yaml
prompt:
  template: |
    Process items {{ start_item }} through {{ end_item }}.
    Focus on: {{ variables.focus_area }}
  variables:
    focus_area: "error handling"
  stakes: "This code will be deployed to production."
  thinking_method: "Think step by step."
  prompt_extensions:
    - "Always write unit tests for new code."
    - "Follow the existing code style in the project."
```

---

## parallel

*Source: `src/marianne/core/config/execution.py` — `ParallelConfig`*

Enables running multiple sheets concurrently when the dependency DAG permits.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable parallel sheet execution |
| `max_concurrent` | `int` | `3` | `1–10` | Maximum sheets to execute concurrently |
| `fail_fast` | `bool` | `true` | | Stop starting new sheets when one fails |
| `stagger_delay_ms` | `int` | `0` | `0–5000` | Delay in milliseconds between launching parallel sheets. Reduces rate limit surge when many sheets hit the same API. |
| `budget_partition` | `bool` | `true` | **Not yet implemented** | Partition cost budget across parallel branches. Accepted but not enforced; cost checks use global total. |

```yaml
parallel:
  enabled: true
  max_concurrent: 3
  fail_fast: true
  stagger_delay_ms: 150   # 150ms between launches
```

---

## retry

*Source: `src/marianne/core/config/execution.py` — `RetryConfig`*

Controls retry behavior including partial completion recovery.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `max_retries` | `int` | `3` | `>= 0` | Maximum retry attempts per sheet |
| `base_delay_seconds` | `float` | `10.0` | `> 0`, must be `<= max_delay_seconds` | Initial delay between retries |
| `max_delay_seconds` | `float` | `3600.0` | `> 0` | Maximum delay cap (1 hour) |
| `exponential_base` | `float` | `2.0` | `> 1` | Exponential backoff multiplier |
| `jitter` | `bool` | `true` | | Add randomness to delays |
| `max_completion_attempts` | `int` | `5` | `>= 0` | Maximum completion prompt attempts before full retry |
| `completion_delay_seconds` | `float` | `5.0` | `>= 0` | Delay between completion attempts (seconds) |
| `completion_threshold_percent` | `float` | `50.0` | `> 0`, `<= 100` | Minimum pass percentage to trigger completion mode |

**Completion mode:** When a sheet partially passes validation (more than `completion_threshold_percent`), Marianne sends a targeted "complete the remaining work" prompt instead of a full retry. This is more efficient and preserves already-completed work.

```yaml
retry:
  max_retries: 5
  base_delay_seconds: 15
  max_delay_seconds: 600
  exponential_base: 2.0
  jitter: true
  max_completion_attempts: 3
  completion_threshold_percent: 50.0
```

---

## rate_limit

*Source: `src/marianne/core/config/execution.py` — `RateLimitConfig`*

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `detection_patterns` | `list[str]` | `["rate.?limit", "usage.?limit", "quota", "too many requests", "429", "capacity", "try again later"]` | Must be valid regex | Regex patterns to detect rate limiting in output |
| `wait_minutes` | `int` | `60` | `>= 1` | Minutes to wait when rate limited |
| `max_waits` | `int` | `24` | `>= 1` | Maximum wait cycles (24 = 24 hours) |
| `max_quota_waits` | `int` | `48` | `>= 1` | Maximum quota exhaustion wait cycles before failing (48 = 48 hours at 60min/wait) |

```yaml
rate_limit:
  detection_patterns:
    - "rate.?limit"
    - "too many requests"
    - "429"
  wait_minutes: 30
  max_waits: 48
```

---

## circuit_breaker

*Source: `src/marianne/core/config/execution.py` — `CircuitBreakerConfig`*

Prevents cascading failures by temporarily blocking requests after repeated failures. State transitions: CLOSED (normal) → OPEN (blocking) → HALF_OPEN (testing recovery).

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `true` | | Enable circuit breaker pattern |
| `failure_threshold` | `int` | `5` | `1–100` | Consecutive failures before opening circuit |
| `recovery_timeout_seconds` | `float` | `300.0` | `> 0`, `<= 3600` | Seconds in OPEN state before testing recovery (max 1 hour) |
| `cross_workspace_coordination` | `bool` | `true` | | Enable cross-workspace coordination via global learning store. Rate limit events shared between parallel jobs. |
| `honor_other_jobs_rate_limits` | `bool` | `true` | | Honor rate limits detected by other parallel jobs |

```yaml
circuit_breaker:
  enabled: true
  failure_threshold: 5
  recovery_timeout_seconds: 300
  cross_workspace_coordination: true
```

---

## cost_limits

*Source: `src/marianne/core/config/execution.py` — `CostLimitConfig`*

Prevents runaway costs by tracking token usage. When enabled, at least one of `max_cost_per_sheet` or `max_cost_per_job` must be set.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable cost tracking and limit enforcement |
| `max_cost_per_sheet` | `float \| None` | `None` | `> 0` | Maximum cost per sheet in USD |
| `max_cost_per_job` | `float \| None` | `None` | `> 0` | Maximum cost for entire score in USD |
| `cost_per_1k_input_tokens` | `float` | `0.003` | `> 0` | Cost per 1000 input tokens (Claude Sonnet default: $0.003) |
| `cost_per_1k_output_tokens` | `float` | `0.015` | `> 0` | Cost per 1000 output tokens (Claude Sonnet default: $0.015) |
| `warn_at_percent` | `float` | `80.0` | `> 0`, `<= 100` | Emit warning at this percentage of limit |

**Model-specific rates:** Default rates are for Claude Sonnet. For Opus, use `cost_per_1k_input_tokens: 0.015` and `cost_per_1k_output_tokens: 0.075`.

```yaml
cost_limits:
  enabled: true
  max_cost_per_sheet: 5.00
  max_cost_per_job: 100.00
  warn_at_percent: 80
```

---

## stale_detection

*Source: `src/marianne/core/config/execution.py` — `StaleDetectionConfig`*

Detects hung sheet executions that produce no output.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable stale execution detection |
| `idle_timeout_seconds` | `float` | `300.0` | `> 0` | Maximum seconds of inactivity before marking stale. Recommended minimum 120s for LLM workloads. |
| `check_interval_seconds` | `float` | `30.0` | `> 0`, must be `< idle_timeout_seconds` | How often to check for idle executions |

```yaml
stale_detection:
  enabled: true
  idle_timeout_seconds: 300
  check_interval_seconds: 30
```

---

## cross_sheet

*Source: `src/marianne/core/config/workspace.py` — `CrossSheetConfig`*

Enables passing outputs and files between sheets. Set to `null` or omit to disable. When configured, later sheets can access `{{ previous_outputs[N] }}` and `{{ previous_files }}` in templates.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `auto_capture_stdout` | `bool` | `false` | | Include previous sheets' `stdout_tail` in context |
| `max_output_chars` | `int` | `2000` | `> 0` | Maximum characters per previous sheet output |
| `capture_files` | `list[str]` | `[]` | | File path patterns to read between sheets. Supports Jinja2 templating. |
| `lookback_sheets` | `int` | `3` | `>= 0` | Number of previous sheets to include. 0 = all completed. |

```yaml
cross_sheet:
  auto_capture_stdout: true
  max_output_chars: 4000
  lookback_sheets: 5
  capture_files:
    - "{{ workspace }}/sheet-{{ sheet_num - 1 }}.md"
```

---

## validations

*Source: `src/marianne/core/config/execution.py` — `ValidationRule`*

A list of validation rules applied after each sheet execution. Supports staged execution — validations run in stage order, and if any validation in a stage fails, higher stages are skipped.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `type` | `"file_exists" \| "file_modified" \| "content_contains" \| "content_regex" \| "command_succeeds"` | **required** | | Validation type |
| `path` | `str \| None` | `None` | Required for `file_exists`, `file_modified`, `content_contains`, `content_regex` | File path. Supports `{sheet_num}`, `{workspace}` templates. |
| `pattern` | `str \| None` | `None` | Required for `content_contains`, `content_regex`. Must be valid regex for `content_regex`. | Pattern for content matching |
| `command` | `str \| None` | `None` | Required for `command_succeeds` | Shell command to run |
| `working_directory` | `str \| None` | `None` | | Working directory for command (defaults to workspace) |
| `description` | `str \| None` | `None` | | Human-readable description |
| `stage` | `int` | `1` | `1–10` | Validation stage. Lower stages run first; fail-fast on failure. |
| `condition` | `str \| None` | `None` | | Condition for when this validation applies. See **Condition syntax** below. |
| `retry_count` | `int` | `3` | `0–10` | Retry attempts for file-based validations (helps with filesystem race conditions) |
| `retry_delay_ms` | `int` | `200` | `0–5000` | Delay between retries in milliseconds |

**Validation types:**

| Type | Required Fields | What It Checks |
|------|-----------------|----------------|
| `file_exists` | `path` | File exists at the given path |
| `file_modified` | `path` | File was modified during sheet execution |
| `content_contains` | `path`, `pattern` | File contains the literal pattern string |
| `content_regex` | `path`, `pattern` | File content matches the regex pattern |
| `command_succeeds` | `command` | Shell command exits with code 0 |

**Condition syntax:**

Conditions control when a validation rule applies. Each condition compares a context variable against an integer value.

| Operator | Example | Meaning |
|----------|---------|---------|
| `>=` | `sheet_num >= 3` | Greater than or equal |
| `<=` | `stage <= 2` | Less than or equal |
| `==` | `instance == 1` | Equal |
| `!=` | `fan_count != 1` | Not equal |
| `>` | `sheet_num > 5` | Greater than |
| `<` | `total_stages < 4` | Less than |

**Boolean AND:** Combine multiple conditions with `" and "` (space-delimited). All conditions must be true.

```
"sheet_num >= 3 and stage == 2"
"fan_count > 1 and instance == 1"
```

**Available context variables:**

| Variable | Type | Description |
|----------|------|-------------|
| `sheet_num` | `int` | Current sheet number (1-indexed) |
| `total_sheets` | `int` | Total number of sheets |
| `start_item` | `int` | First item number for this sheet |
| `end_item` | `int` | Last item number for this sheet |
| `stage` | `int` | Logical stage number (equals `sheet_num` when no fan-out) |
| `instance` | `int` | Instance within fan-out group (1-indexed, default 1) |
| `fan_count` | `int` | Total instances in this stage's fan-out group (default 1) |
| `total_stages` | `int` | Original stage count before fan-out expansion |

Any variable that is missing or non-integer is treated as "condition satisfied" (fail-open). An unrecognized condition format also passes (fail-open).

```yaml
validations:
  # Stage 1: Check files exist
  - type: file_exists
    path: "{workspace}/output-{sheet_num}.md"
    stage: 1
    description: "Output file created"

  # Stage 2: Run tests
  - type: command_succeeds
    command: "pytest tests/ -x"
    stage: 2
    description: "Tests pass"

  # Stage 3: Code quality (only from sheet 3 onward)
  - type: command_succeeds
    command: "ruff check src/"
    stage: 3
    condition: "sheet_num >= 3"
    description: "Lint clean"

  # Only for fan-out primary instances
  - type: command_succeeds
    command: "python merge_results.py"
    stage: 3
    condition: "fan_count > 1 and instance == 1"
    description: "Merge fan-out results (primary instance only)"
```

---

## isolation

*Source: `src/marianne/core/config/workspace.py` — `IsolationConfig`*

Git worktree isolation for parallel-safe score execution. Each score runs in an isolated git working directory.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable worktree isolation |
| `mode` | `"none" \| "worktree"` | `"worktree"` | | Isolation method. Currently only `worktree` is supported. |
| `worktree_base` | `Path \| None` | `None` | | Directory for worktrees. `None` resolves to `<repo>/.worktrees` at runtime. |
| `branch_prefix` | `str` | `"marianne"` | Pattern: `^[a-zA-Z][a-zA-Z0-9_-]*$` | Prefix for worktree branch names. Format: `{prefix}/{job-id}` |
| `source_branch` | `str \| None` | `None` | | Branch to base worktree on. Default: current branch (HEAD). |
| `cleanup_on_success` | `bool` | `true` | | Remove worktree after successful score completion |
| `cleanup_on_failure` | `bool` | `false` | | Remove worktree when score fails. Default `false` for debugging. |
| `lock_during_execution` | `bool` | `true` | | Lock worktree during execution with `git worktree lock` |
| `fallback_on_error` | `bool` | `true` | | Continue without isolation if worktree creation fails |

> **Warning:** Enabling both `parallel` and `isolation` causes parallel sheets to share the same worktree. Ensure parallel sheets don't write to overlapping paths.

```yaml
isolation:
  enabled: true
  mode: worktree
  branch_prefix: marianne
  cleanup_on_success: true
  cleanup_on_failure: false
```

---

## grounding

*Source: `src/marianne/core/config/learning.py` — `GroundingConfig`*

External grounding hooks validate sheet outputs against external sources to prevent model drift.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable external grounding hooks |
| `hooks` | `list[GroundingHookConfig]` | `[]` | | Grounding hook configurations |
| `fail_on_grounding_failure` | `bool` | `true` | | Fail validation if grounding fails |
| `escalate_on_failure` | `bool` | `true` | | Escalate to human if grounding fails (requires escalation handler) |
| `timeout_seconds` | `float` | `30.0` | `> 0` | Maximum time per grounding hook |

### Grounding Hook Sub-Config

*Source: `src/marianne/core/config/learning.py` — `GroundingHookConfig`*

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `type` | `"file_checksum"` | **required** | | Hook type |
| `name` | `str \| None` | `None` | | Custom name (uses type if not specified) |
| `expected_checksums` | `dict[str, str]` | `{}` | | Map of file path to expected checksum |
| `checksum_algorithm` | `"md5" \| "sha256"` | `"sha256"` | | Checksum algorithm |

```yaml
grounding:
  enabled: true
  hooks:
    - type: file_checksum
      name: critical_files
      expected_checksums:
        "output.py": "abc123..."
      checksum_algorithm: sha256
```

---

## conductor

*Source: `src/marianne/core/config/orchestration.py` — `ConductorConfig`*

Identifies who (or what) is conducting the job. Affects escalation behavior and output formatting.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | `"default"` | 1–100 chars | Human-readable conductor name |
| `role` | `"human" \| "ai" \| "hybrid"` | `"human"` | | Role classification |
| `identity_context` | `str \| None` | `None` | Max 500 chars | Brief description of the conductor's identity/purpose |
| `preferences` | `ConductorPreferences` | *(see sub-config)* | | Conductor interaction preferences |

### Conductor Preferences Sub-Config

*Source: `src/marianne/core/config/orchestration.py` — `ConductorPreferences`*

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `prefer_minimal_output` | `bool` | `false` | | Reduce console output verbosity |
| `escalation_response_timeout_seconds` | `float` | `300.0` | `> 0` | Maximum time to wait for escalation response (defaults to abort after timeout) |
| `auto_retry_on_transient_errors` | `bool` | `true` | | Automatically retry transient errors before escalating |
| `notification_channels` | `list[str]` | `[]` | | Preferred notification channels. Empty = use job-level settings. |

```yaml
conductor:
  name: "Claude Evolution Agent"
  role: ai
  identity_context: "Self-improving orchestration agent"
  preferences:
    prefer_minimal_output: true
    auto_retry_on_transient_errors: true
```

---

## concert

*Source: `src/marianne/core/config/orchestration.py` — `ConcertConfig`*

Concert orchestration chains multiple jobs in sequence. Each job can dynamically generate the config for the next.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable concert mode (job chaining via `on_success` hooks) |
| `max_chain_depth` | `int` | `5` | `1–100` | Maximum chained jobs. Prevents infinite loops. |
| `cooldown_between_jobs_seconds` | `float` | `30.0` | `>= 0` | Minimum wait between job transitions |
| `inherit_workspace` | `bool` | `true` | | Child jobs inherit parent workspace if not explicitly specified |
| `concert_log_path` | `Path \| None` | `None` | | Consolidated concert log (default: `workspace/concert.log`) |
| `abort_concert_on_hook_failure` | `bool` | `false` | | Abort entire concert if any hook fails |

```yaml
concert:
  enabled: true
  max_chain_depth: 10
  cooldown_between_jobs_seconds: 60
```

---

## on_success (Post-Success Hooks)

*Source: `src/marianne/core/config/orchestration.py` — `PostSuccessHookConfig`*

A list of hooks that execute after successful job completion.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `type` | `"run_job" \| "run_command" \| "run_script"` | **required** | | Hook type |
| `job_path` | `Path \| None` | `None` | Required for `run_job` | Path to job config YAML. Supports `{workspace}` template. |
| `job_workspace` | `Path \| None` | `None` | | Override workspace for chained job |
| `inherit_learning` | `bool` | `true` | | Chained job shares outcome store with parent |
| `command` | `str \| None` | `None` | Required for `run_command`/`run_script` | Shell command or script path. Supports `{workspace}`, `{job_id}`, `{sheet_count}`. |
| `working_directory` | `Path \| None` | `None` | | Working directory for command (default: job workspace) |
| `description` | `str \| None` | `None` | | Human-readable description |
| `on_failure` | `"continue" \| "abort"` | `"continue"` | | Action if hook fails: continue to next or abort remaining |
| `timeout_seconds` | `float` | `300.0` | `> 0` | Maximum hook execution time (5 min default) |
| `detached` | `bool` | `false` | | For `run_job`: spawn and don't wait. Routes through daemon IPC when available (enabling tracking, rate coordination, backpressure). Falls back to subprocess if no daemon is running. |
| `fresh` | `bool` | `false` | | For `run_job`: pass `--fresh` to start with clean state. Required for self-chaining jobs. |

```yaml
on_success:
  - type: run_job
    job_path: "{workspace}/next-phase.yaml"
    description: "Chain to next phase"
    detached: true
    fresh: true
  - type: run_command
    command: "curl -X POST https://api.example.com/notify"
    description: "Notify deployment"
```

---

## notifications

*Source: `src/marianne/core/config/orchestration.py` — `NotificationConfig`*

A list of notification channel configurations.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `type` | `"desktop" \| "slack" \| "webhook" \| "email"` | **required** | | Notification channel type |
| `on_events` | `list[str]` | `["job_complete", "job_failed"]` | Valid events: `job_start`, `sheet_start`, `sheet_complete`, `sheet_failed`, `job_complete`, `job_failed`, `job_paused` | Events that trigger this notification |
| `config` | `dict[str, Any]` | `{}` | | Channel-specific configuration |

```yaml
notifications:
  - type: desktop
    on_events: [job_complete, job_failed]
  - type: webhook
    on_events: [sheet_failed]
    config:
      url: "https://hooks.slack.com/..."
```

---

## learning

*Source: `src/marianne/core/config/learning.py` — `LearningConfig`*

Controls outcome recording, confidence thresholds, pattern application, and escalation.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `true` | | Enable learning and outcome recording |
| `outcome_store_type` | `"json" \| "sqlite"` | `"json"` | | Backend for storing learning outcomes |
| `outcome_store_path` | `Path \| None` | `None` | | Path for outcome store (default: `workspace/.marianne-outcomes.json`) |
| `min_confidence_threshold` | `float` | `0.3` | `0.0–1.0` | Confidence below this triggers escalation (if enabled) |
| `high_confidence_threshold` | `float` | `0.7` | `0.0–1.0` | Confidence above this uses completion mode for partial failures |
| `escalation_enabled` | `bool` | `false` | | Enable escalation for low-confidence decisions |
| `use_global_patterns` | `bool` | `true` | | Query and apply patterns from global learning store |
| `exploration_rate` | `float` | `0.15` | `0.0–1.0` | Epsilon-greedy exploration rate. 0.0 = pure exploitation, 1.0 = try everything. |
| `exploration_min_priority` | `float` | `0.05` | `0.0–1.0` | Minimum priority threshold for exploration candidates |
| `entropy_alert_threshold` | `float` | `0.5` | `0.0–1.0` | Shannon entropy below this triggers alert for low diversity |
| `entropy_check_interval` | `int` | `100` | `>= 1` | Check entropy every N pattern applications |
| `auto_apply_enabled` | `bool` | `false` | **Deprecated** — use `auto_apply` block | Enable auto-apply for high-trust patterns |
| `auto_apply_trust_threshold` | `float` | `0.85` | `0.0–1.0` | **Deprecated** — use `auto_apply` block | Minimum trust score to auto-apply |
| `exploration_budget` | `ExplorationBudgetConfig` | *(see sub-config)* | | Dynamic exploration budget configuration |
| `entropy_response` | `EntropyResponseConfig` | *(see sub-config)* | | Automatic entropy response configuration |
| `auto_apply` | `AutoApplyConfig \| None` | `None` | | Structured autonomous pattern application config (replaces flat fields) |

### Exploration Budget Sub-Config

*Source: `src/marianne/core/config/learning.py` — `ExplorationBudgetConfig`*

Nested under `learning.exploration_budget`. Maintains a dynamic exploration budget that prevents convergence to zero.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable dynamic exploration budget |
| `floor` | `float` | `0.05` | `0.0–1.0`, must be `<= ceiling` | Minimum exploration budget (never below this) |
| `ceiling` | `float` | `0.50` | `0.0–1.0` | Maximum exploration budget |
| `decay_rate` | `float` | `0.95` | `0.0–1.0` | Decay per check interval. `budget = max(floor, budget * decay_rate)` |
| `boost_amount` | `float` | `0.10` | `0.0–0.5` | Budget boost when entropy is low. `budget = min(ceiling, budget + boost_amount)` |
| `initial_budget` | `float` | `0.15` | `0.0–1.0`, must be between floor and ceiling | Starting exploration budget |

### Entropy Response Sub-Config

*Source: `src/marianne/core/config/learning.py` — `EntropyResponseConfig`*

Nested under `learning.entropy_response`. Automatically injects diversity when pattern entropy drops.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable automatic entropy response |
| `entropy_threshold` | `float` | `0.3` | `0.0–1.0` | Entropy level that triggers diversity injection |
| `cooldown_seconds` | `int` | `3600` | `>= 60` | Minimum seconds between responses (1 hour default) |
| `boost_budget` | `bool` | `true` | | Boost exploration budget on low entropy |
| `revisit_quarantine` | `bool` | `true` | | Mark quarantined patterns for review |
| `max_quarantine_revisits` | `int` | `3` | `0–10` | Max quarantined patterns to revisit per response |

### Auto Apply Sub-Config

*Source: `src/marianne/core/config/learning.py` — `AutoApplyConfig`*

Nested under `learning.auto_apply`. Enables high-trust patterns to be applied without human confirmation.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable autonomous pattern application |
| `trust_threshold` | `float` | `0.85` | `0.0–1.0` | Minimum trust score for autonomous application |
| `max_patterns_per_sheet` | `int` | `3` | `1–10` | Maximum patterns to auto-apply per sheet |
| `require_validated_status` | `bool` | `true` | | Require patterns to have VALIDATED quarantine status |
| `log_applications` | `bool` | `true` | | Log when patterns are auto-applied |

```yaml
learning:
  enabled: true
  exploration_rate: 0.15
  exploration_budget:
    enabled: true
    floor: 0.05
    ceiling: 0.50
  auto_apply:
    enabled: true
    trust_threshold: 0.85
    max_patterns_per_sheet: 3
```

---

## checkpoints

*Source: `src/marianne/core/config/learning.py` — `CheckpointConfig`*

Proactive checkpoints ask for confirmation before dangerous operations.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable proactive checkpoints |
| `triggers` | `list[CheckpointTriggerConfig]` | `[]` | | Checkpoint triggers to evaluate before each sheet |

### Checkpoint Trigger Sub-Config

*Source: `src/marianne/core/config/learning.py` — `CheckpointTriggerConfig`*

Each trigger must have at least one condition (`sheet_nums`, `prompt_contains`, or `min_retry_count`).

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | **required** | | Trigger identifier |
| `sheet_nums` | `list[int] \| None` | `None` | | Specific sheet numbers to checkpoint |
| `prompt_contains` | `list[str] \| None` | `None` | | Keywords that trigger checkpoint (case-insensitive) |
| `min_retry_count` | `int \| None` | `None` | `>= 0` | Trigger if retry count >= this value |
| `requires_confirmation` | `bool` | `true` | | Require explicit confirmation vs. just warn |
| `message` | `str` | `""` | | Custom message shown when triggered |

```yaml
checkpoints:
  enabled: true
  triggers:
    - name: production_warning
      prompt_contains: ["production", "deploy", "delete"]
      message: "This sheet may affect production systems"
    - name: high_retry
      min_retry_count: 3
      requires_confirmation: false
      message: "Sheet has been retried multiple times"
```

---

## ai_review

*Source: `src/marianne/core/config/workspace.py` — `AIReviewConfig`*

AI-powered code review after batch execution with scoring.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable AI code review |
| `min_score` | `int` | `60` | `0–100`, must be `<= target_score` | Minimum score to pass. Below triggers action. |
| `target_score` | `int` | `80` | `0–100` | Target score for high quality |
| `on_low_score` | `"retry" \| "warn" \| "fail"` | `"warn"` | | Action when `score < min_score` |
| `max_retry_for_review` | `int` | `2` | `0–5` | Maximum retries when score is too low |
| `review_prompt_template` | `str \| None` | `None` | | Custom prompt template for review |

```yaml
ai_review:
  enabled: true
  min_score: 70
  target_score: 90
  on_low_score: retry
  max_retry_for_review: 2
```

---

## logging

*Source: `src/marianne/core/config/workspace.py` — `LogConfig`*

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `level` | `"DEBUG" \| "INFO" \| "WARNING" \| "ERROR"` | `"INFO"` | | Minimum log level |
| `format` | `"json" \| "console" \| "both"` | `"console"` | `file_path` required when `"both"` | Output format |
| `file_path` | `Path \| None` | `None` | | Log file output path |
| `max_file_size_mb` | `int` | `50` | `1–1000` | Maximum log file size before rotation (MB) |
| `backup_count` | `int` | `5` | `0–100` | Rotated log files to keep |
| `include_timestamps` | `bool` | `true` | | Include ISO8601 UTC timestamps |
| `include_context` | `bool` | `true` | | Include bound context (job_id, sheet_num) |

```yaml
logging:
  level: DEBUG
  format: both
  file_path: ./workspace/marianne.log
  max_file_size_mb: 100
```

---

## feedback

*Source: `src/marianne/core/config/workspace.py` — `FeedbackConfig`*

Extracts structured feedback from agent output after each sheet.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `false` | | Enable agent feedback extraction |
| `pattern` | `str` | `"(?s)FEEDBACK_START(.+?)FEEDBACK_END"` | Must be valid regex | Regex with capture group to extract feedback |
| `format` | `"json" \| "yaml" \| "text"` | `"json"` | | Format of extracted feedback block |

```yaml
feedback:
  enabled: true
  pattern: '(?s)FEEDBACK_START(.+?)FEEDBACK_END'
  format: json
```

---

## State and Misc

*Source: `src/marianne/core/config/job.py` — `JobConfig`*

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `state_backend` | `"json" \| "sqlite"` | `"sqlite"` | State storage backend |
| `state_path` | `Path \| None` | `None` | Path for state storage (default: `workspace/.marianne-state.db` for sqlite, `.marianne-state.json` for json) |
| `pause_between_sheets_seconds` | `int` | `2` | Seconds to wait between sheets. `>= 0`. |

---

## DaemonConfig (Conductor)

*Source: `src/marianne/daemon/config.py` — `DaemonConfig`*

Top-level configuration for the Marianne daemon process. Configured separately from score files (typically via `~/.marianne/conductor.yaml` or CLI flags).

### Essential Fields

These are the fields most users will configure:

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `max_concurrent_jobs` | `int` | `15` | `1–50` | Maximum jobs executing simultaneously |
| `log_level` | `"debug" \| "info" \| "warning" \| "error"` | `"info"` | | Daemon log level |
| `job_timeout_seconds` | `float` | `86400.0` | `>= 60` | Maximum wall-clock time per job (24 hours default). |
| `learning.enabled` | `bool` | `true` | | Semantic learning on/off |
| `learning.backend` | `BackendConfig` | *(see sub-config)* | | Which model powers semantic analysis |
| `profiler.enabled` | `bool` | `true` | | Resource monitoring on/off |

### Advanced Fields

Available but rarely need changing:

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `socket` | `SocketConfig` | *(see sub-config)* | | Unix domain socket configuration |
| `pid_file` | `Path` | `/tmp/marianne.pid` | | PID file for daemon process management |
| `max_concurrent_sheets` | `int` | `10` | `1–100` | **Reserved for Phase 3 scheduler — not yet enforced.** Global parallel sheet limit. |
| `resource_limits` | `ResourceLimitConfig` | *(see sub-config)* | | Resource constraints |
| `state_backend_type` | `"sqlite"` | `"sqlite"` | **Reserved — frozen to sqlite.** Changing has no effect. |
| `state_db_path` | `Path` | `~/.marianne/daemon-state.db` | **Reserved — not yet implemented.** | Future daemon state database path |
| `log_file` | `Path \| None` | `None` | | Log file path. `None` = stderr only. |
| `shutdown_timeout_seconds` | `float` | `300.0` | `>= 10` | Max seconds for graceful shutdown |
| `monitor_interval_seconds` | `float` | `15.0` | `>= 5` | Interval between resource monitor checks |
| `max_job_history` | `int` | `1000` | `>= 10` | Completed/failed/cancelled jobs to keep in memory |
| `observer` | `ObserverConfig` | *(see sub-config)* | | Observer and event bus configuration |
| `profiler` | `ProfilerConfig` | *(see sub-config)* | | System profiler (strace off by default, GPU probing off by default) |
| `preflight` | `PreflightConfig` | *(see sub-config)* | | Preflight prompt analysis thresholds. Controls when prompts are warned or rejected based on estimated token count. |
| `use_baton` | `bool` | `true` | | Enable the baton execution engine. When `true`, job execution uses event-driven per-sheet dispatch instead of the monolithic runner. Set to `false` to fall back to the legacy runner. |
| `config_file` | `Path \| None` | `None` | | Path to the YAML config file. Set automatically on startup; used by SIGHUP reload to re-read config from disk. |

### Daemon Operational Profiles

Instead of configuring every field, use `--profile` to apply a preset:

```bash
mzt start                    # sensible defaults
mzt start --profile dev      # debug logging, strace on, low concurrency
mzt start --profile intensive # 48h timeout, high resource limits
mzt start --profile minimal  # profiler off, learning off, low concurrency
```

Profiles are partial overrides merged on top of your config file. Resolution order: **defaults → config file → profile → CLI flags**.

**Built-in profiles:**

| Profile | Description | Key Settings |
|---------|-------------|-------------|
| `dev` | Marianne development / debugging | `log_level: debug`, `max_concurrent_jobs: 2`, `strace_enabled: true`, `interval_seconds: 2.0` |
| `intensive` | Long-running production work | `job_timeout_seconds: 172800` (48h), `max_memory_mb: 16384`, `max_processes: 100` |
| `minimal` | Low-resource environments | `max_concurrent_jobs: 2`, `profiler.enabled: false`, `learning.enabled: false` |

### Socket Sub-Config

*Source: `src/marianne/daemon/config.py` — `SocketConfig`*

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `path` | `Path` | `/tmp/marianne.sock` | | Unix domain socket path |
| `permissions` | `int` | `0o660` | | Socket file permissions (owner+group read/write) |
| `backlog` | `int` | `5` | `>= 1` | Maximum pending connections in listen queue |

### Resource Limits Sub-Config

*Source: `src/marianne/daemon/config.py` — `ResourceLimitConfig`*

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `max_memory_mb` | `int` | `8192` | `>= 512` | Maximum RSS memory (MB) before backpressure triggers |
| `max_processes` | `int` | `50` | `>= 5` | Maximum child processes (backends + validation commands) |
| `max_api_calls_per_minute` | `int` | `60` | `>= 1` | **Not yet enforced.** Global API rate limit across all jobs. Rate limiting currently works through externally-reported events. |

### Semantic Learning Sub-Config

*Source: `src/marianne/daemon/config.py` — `SemanticLearningConfig`*

Controls the conductor's LLM-based analysis of sheet completions. The SemanticAnalyzer subscribes to EventBus sheet events, sends completion context to an LLM, and stores insights as `SEMANTIC_INSIGHT` patterns in the global learning store. These patterns are automatically picked up by the existing pattern injection pipeline.

The `backend` field accepts the same `BackendConfig` used by job execution, so any backend type (`claude_cli`, `anthropic_api`, `ollama`, `recursive_light`) can power semantic analysis.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `true` | | Enable semantic learning. When the conductor is running, learning is on by default. |
| `backend` | `BackendConfig` | *(see below)* | | Backend configuration for semantic analysis LLM calls. Defaults to `anthropic_api` with analytical settings (temperature=0.3, max_tokens=4096, timeout=120s). |
| `analyze_on` | `list["success" \| "failure"]` | `["success", "failure"]` | Non-empty, no duplicates | Which sheet outcomes trigger analysis |
| `max_concurrent_analyses` | `int` | `3` | `1–20` | Maximum concurrent LLM analysis tasks. Controls API cost and system load. |

```yaml
# Conductor config (conductor.yaml)
learning:
  enabled: true
  backend:
    type: anthropic_api
    model: claude-sonnet-4-5-20250929
    temperature: 0.3
    max_tokens: 4096
    timeout_seconds: 120
  analyze_on: [success, failure]
  max_concurrent_analyses: 3
```

### Preflight Sub-Config

*Source: `src/marianne/core/config/execution.py` — `PreflightConfig`*

Controls token count thresholds for pre-flight prompt analysis before sheet execution. Different instruments have different context windows — a 150K threshold that's correct for a 200K-context model is wrong for a 1M-context model. Set thresholds appropriate for the instruments in use.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `token_warning_threshold` | `int` | `50000` | `>= 0` | Token count above which to warn during preflight. Set higher for large-context instruments. `0` to disable. |
| `token_error_threshold` | `int` | `150000` | `>= 0` | Token count above which to error during preflight. `0` to disable. Warning threshold must be less than error threshold when both are set. |

```yaml
# Conductor config for large-context instruments (1M+ context)
preflight:
  token_warning_threshold: 200000
  token_error_threshold: 800000
```
