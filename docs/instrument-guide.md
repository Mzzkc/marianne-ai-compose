# Instrument Guide

Marianne uses **instruments** to execute scores. An instrument is any AI tool that
can receive a prompt and produce output — Claude Code, Gemini CLI, Codex CLI,
Aider, Goose, or any CLI tool you configure. The conductor assigns musicians
(AI agents) to instruments and manages execution across all of them.

This guide covers how to use existing instruments, how to add your own, and
how the instrument system works.

---

## Quick Reference

```bash
# See what instruments are available
mzt instruments list

# Check if a specific instrument is ready
mzt instruments check gemini-cli

# Full environment health check
mzt doctor
```

---

## Built-in Instruments

Marianne ships config-driven instrument profiles as YAML files. The profile
selects a CLI or HTTP transport; provider-specific wire details remain inside
the profile-driven execution layer.

### Maintained Profiles

The maintained built-ins include:

| Name | Tool | Auth |
|------|------|------|
| `claude-code` | Claude Code CLI (`claude`) | `claude login` |
| `ollama` | Local OpenAI-compatible server | Optional local auth |

### Agent Profiles

These ship as YAML profiles bundled with Marianne and are loaded at conductor
startup:

| Name | Tool | Auth |
|------|------|------|
| `claude-code` | Claude Code CLI | `claude login` |
| `gemini-cli` | Google Gemini CLI | `GOOGLE_API_KEY` or `gcloud auth` |
| `codex-cli` | OpenAI Codex CLI | `OPENAI_API_KEY or CODEX_API_KEY` |
| `cline-cli` | Cline CLI | Provider API key |
| `aider` | Aider | Provider API key (`OPENAI_API_KEY`, etc.) |
| `goose` | Block's Goose | Provider API key |

To check which instruments are available on your system:

```bash
mzt instruments list
```

---

## Using Instruments in Scores

### The `instrument:` Field

Specify which instrument to use with the `instrument:` field at the top level
of your score:

```yaml
name: my-score
workspace: ./workspaces/my-score

instrument: gemini-cli

sheet:
  size: 1
  total_items: 3

prompt:
  template: |
    Write a summary of {{ workspace }}/input.md
```

> **Legacy `backend:` scores:** the old `backend:` block was removed (#347)
> and now fails at parse time. See the
> [migration guide](score-writing-guide.md#migrating-from-backend-to-instrument)
> for the field-by-field conversion.

### Instrument Configuration

Override instrument defaults with `instrument_config:`:

```yaml
instrument: gemini-cli
instrument_config:
  model: gemini-2.5-flash       # Use the cheaper model
  timeout_seconds: 600           # Shorter timeout
```

These overrides are flat key-value pairs that adjust the resolved instrument
profile without replacing it.

GPT-5.6 is a model family played through the `codex-cli` instrument. Select a
tier explicitly when the score needs one:

```yaml
instrument: codex-cli
instrument_config:
  model: gpt-5.6-luna
```

If `instrument_config.model` is omitted, Marianne passes no `--model` flag and
the installed Codex client chooses its configured default. Marianne does not
implicitly default Codex to Luna, Terra, or Sol.

---

## Adding Your Own Instruments

Any CLI tool that accepts a prompt and produces output can become a Marianne
instrument. You write a YAML profile describing the tool's CLI interface and
drop it in a directory Marianne scans.

### Profile Directories

Marianne loads instrument profiles from three directories, in order:

1. **Built-in** — shipped with Marianne (lowest precedence)
2. **Organization** — `~/.marianne/instruments/` (shared across all projects)
3. **Venue** — `.marianne/instruments/` (project-specific, highest precedence)

Later directories override earlier ones on name collision. This lets you
customize a built-in profile for your project without modifying Marianne's source.

### Writing a Profile

Here is a minimal profile for a hypothetical CLI tool:

```yaml
# ~/.marianne/instruments/my-tool.yaml

name: my-tool
display_name: "My Tool"
description: "Custom CLI agent for my project"
kind: cli

capabilities:
  - file_editing
  - shell_access

default_timeout_seconds: 1800

cli:
  command:
    executable: my-tool           # Binary name on PATH
    prompt_flag: "--prompt"       # How to pass the prompt
    auto_approve_flag: "--yes"    # How to skip confirmation dialogs
  output:
    format: text                  # Capture stdout as the result
  errors:
    rate_limit_patterns:
      - "rate.?limit"
      - "429"
```

Save it to `~/.marianne/instruments/my-tool.yaml`, then verify:

```bash
mzt instruments check my-tool
```

### Profile Reference

#### Top-Level Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique identifier used in score YAML (`instrument: my-tool`) |
| `display_name` | Yes | Human-readable name for CLI output |
| `description` | No | Short description of the tool |
| `kind` | Yes | `cli` (v1) or `http` (v1.1+) |
| `capabilities` | No | Set of capability tags (see below) |
| `models` | No | List of available models with pricing and context windows |
| `default_model` | No | Model to use when none specified in the score |
| `default_timeout_seconds` | No | Default execution timeout (default: 1800) |

#### Capability Tags

Capabilities describe what an instrument can do. They are informational in v1
and used by the conductor for instrument selection in future versions.

| Tag | Meaning |
|-----|---------|
| `tool_use` | Can call external tools |
| `file_editing` | Can read and write files |
| `shell_access` | Can execute shell commands |
| `vision` | Can process images |
| `mcp` | Supports Model Context Protocol servers |
| `structured_output` | Can produce JSON output |
| `streaming` | Supports streaming responses |
| `thinking` | Has extended reasoning/thinking mode |
| `session_resume` | Can resume previous sessions |
| `code_mode` | Supports code-mode techniques (v1.1+) |

#### `cli.command` — How to Build the Command

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `executable` | Yes | | Binary name (must be on PATH) |
| `subcommand` | No | | Subcommand, e.g. `exec` for Codex |
| `prompt_flag` | No | | Flag for the prompt (`-p`, `--message`). `null` = positional argument |
| `model_flag` | No | | Flag for model selection (`--model`) |
| `auto_approve_flag` | No | | Flag for auto-approval (`--yolo`, `--yes`) |
| `output_format_flag` | No | | Flag for output format (`--output-format`, `--json`) |
| `output_format_value` | No | | Value for output format flag (`json`). `null` = boolean flag |
| `system_prompt_flag` | No | | Flag for system prompt |
| `allowed_tools_flag` | No | | Flag for restricting tools |
| `mcp_config_flag` | No | | Flag for MCP server configuration |
| `mcp_config_prefix_args` | No | `[]` | Args to add immediately before an active MCP config flag/path, such as `--strict-mcp-config` |
| `mcp_config_workspace_path` | No | | Workspace-relative MCP config file path for CLIs that discover MCP servers from disk |
| `mcp_config_workspace_merge_key` | No | | JSON key to merge generated MCP servers into when the workspace path is a broader settings file |
| `timeout_flag` | No | | Flag for per-execution timeout |
| `working_dir_flag` | No | | Flag for working directory. `null` = subprocess cwd |
| `extra_flags` | No | `[]` | Fixed flags always appended |
| `env` | No | `{}` | Environment variables. `${VAR}` references expand from `os.environ` |

#### `cli.output` — How to Parse the Result

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `format` | No | `text` | `text`, `json`, or `jsonl` |
| `result_path` | No | | JSON dot-path to response text (`result`, `response`) |
| `error_path` | No | | JSON dot-path to error message (`error.message`) |
| `completion_event_type` | No | | For JSONL: event type signaling completion |
| `completion_event_filter` | No | | For JSONL: additional key-value filter |
| `input_tokens_path` | No | | JSON dot-path to input token count |
| `output_tokens_path` | No | | JSON dot-path to output token count |

**Output format modes:**

- **`text`** — Stdout is the result. No structured parsing. Use this for tools
  without JSON output (like Aider).
- **`json`** — Parse stdout as JSON. Extract the response via `result_path`
  (dot notation: `key.subkey`, `key[0]`, `key.*` for wildcard).
- **`jsonl`** — Split stdout into JSON lines. Find the completion event
  matching `completion_event_type` and `completion_event_filter`.

#### `cli.errors` — How to Detect Failures

| Field | Default | Description |
|-------|---------|-------------|
| `success_exit_codes` | `[0]` | Exit codes that indicate success |
| `rate_limit_patterns` | `[]` | Regex patterns in stderr/stdout indicating rate limiting |
| `auth_error_patterns` | `[]` | Regex patterns indicating auth failures |

These patterns supplement Marianne's built-in error classifier. When a pattern
matches, the error is classified as `RATE_LIMIT` or `AUTH_FAILURE` and handled
accordingly (rate limits pause the instrument; auth failures fail immediately).

#### `models` — Available Models

Each model entry describes capacity and pricing:

```yaml
models:
  - name: gemini-2.5-pro
    context_window: 1000000      # Max context in tokens
    cost_per_1k_input: 0.00125   # USD per 1K input tokens
    cost_per_1k_output: 0.005    # USD per 1K output tokens
    max_output_tokens: 65536     # Max output tokens (null if unlimited)
```

Model metadata enables cost tracking in `mzt status` and context budget
calculation. If you omit models, cost tracking shows `$0.00` and context
budget uses a conservative default.

---

#### `http` — OpenAI-compatible HTTP profiles (`kind: http`)

A profile with `kind: http` carries no `cli:` block. Instead it names an
OpenAI-compatible chat-completions endpoint and the conductor dispatches
requests through the shared HTTP executor (`execution/instruments/openai_compat_backend.py`).
Only one wire contract exists today: `schema_family: openai`.

| Field | Required | Description |
|-------|----------|-------------|
| `base_url` | Yes | API root, e.g. `http://localhost:11434/v1` or `https://openrouter.ai/api/v1` |
| `endpoint` | No | Path appended to `base_url` (default `/v1/chat/completions`; set `/chat/completions` when `base_url` already ends in `/v1`) |
| `schema_family` | Yes | `openai` — the request/response contract the executor speaks |
| `auth_env_var` | No | Name of the environment variable holding the bearer token; omit for unauthenticated local servers |
| `response_format` | No | Opt-in structured output (`{type: json_object}` or `{type: json_schema, json_schema: {...}}`), forwarded unchanged; per-sheet `instrument_config.response_format` overrides it |

The shipped `ollama` profile is the minimal shape (local, unauthenticated):

```yaml
name: ollama
display_name: "Ollama"
kind: http
default_model: llama3.1:8b
models:
  - name: llama3.1:8b
    context_window: 32768
    cost_per_1k_input: 0.0
    cost_per_1k_output: 0.0
http:
  base_url: http://localhost:11434/v1
  endpoint: /chat/completions
  schema_family: openai
```

A hosted OpenAI-compatible provider adds the token variable and real pricing:

```yaml
name: openrouter
display_name: "OpenRouter"
kind: http
default_model: openai/gpt-5.3-codex
models:
  - name: openai/gpt-5.3-codex
    context_window: 272000
    cost_per_1k_input: 0.002
    cost_per_1k_output: 0.01
http:
  base_url: https://openrouter.ai/api/v1
  endpoint: /chat/completions
  schema_family: openai
  auth_env_var: OPENROUTER_API_KEY
```

HTTP results record `model_requested`, `model_observed` and `model_echo_status`
(see "HTTP model-echo evidence" below), so a provider that silently serves a
different model is visible in `mzt status` and the checkpoint.

## How the Instrument System Works

### Loading Order

At conductor startup:

1. **Built-in YAML profiles** are loaded from Marianne's bundled instruments directory
2. **Organization profiles** from `~/.marianne/instruments/` override built-ins
3. **Venue profiles** from `.marianne/instruments/` override everything

The result is a single `InstrumentRegistry` mapping names to profiles. When a
score references `instrument: gemini-cli`, the conductor looks up that name
in the registry and selects the CLI or OpenAI-compatible HTTP executor from
the profile's `kind` and protocol settings.

### Score Resolution

When a score is submitted, the instrument is resolved:

1. If the score has `instrument:` — look up the name in the registry
2. If omitted — default to `claude-code`

The resolved profile produces a shared execution-contract instance that the
conductor uses to execute sheets.

### Optional reviewed local HTTP and separately reviewed remote routes

Embedding clients may submit `JobRequest.expected_route`, a frozen
`InstrumentRouteBinding`. It is not a score field and grants no permission to
send data. Absence retains ordinary dispatch and retry behavior. The current
local guarded arm supports observable HTTP profiles on loopback. A separately
reviewed remote arm supports HTTPS HTTP profiles and configured CLI profiles
with an explicit provider and model flag. Neither arm supports movement,
instrument-map, per-sheet instrument or fallback alternatives. Local review is
not authorization for remote processing; the caller owns that separate purpose.

`marianne.instruments.loader.capture_instrument_route_binding` accepts the
score path, instrument, reviewed score SHA-256, and an aware `now` timestamp.
It uses the conductor's profile loader and hashes the winning raw profile
bytes. Callers outside the conductor's directory must supply the matching
`organization_dir` and `venue_dir`; no registry context is guessed. Model
precedence is score `instrument_config.model`, then profile `default_model`.
Provider is a declaration only; an undeclared provider stays `None`.

The score digest verifies the bytes read by this capture helper only. The
route binding does not carry that digest and does not atomically pin a later
submission's score bytes. Callers must not treat route equality as score or
prompt custody, consent, or a reservation of model work.

`route_identity` compares exactly thirteen declared fields: arm, instrument,
kind, profile origin/digest, effective model/provider, their declaration
sources, and HTTP scheme/host/port/endpoint. `None` is a value, not a wildcard.
Only `resolved_at` is excluded. The immutable expectation persists through
job/sheet checkpoints and is rechecked on resume. Immediately before HTTP POST,
the backend captures current resolver state off the event loop, compares it
to both the expectation and its loaded profile, and verifies the actual
outbound model and endpoint. Capture must precede request start by at most
60 seconds; completion must not precede start. Drift refuses the attempt.

Guarded requests use a fresh HTTP client with environment proxies disabled,
redirect following disabled, and transport retries zero. The client is closed
in `finally` and never enters the ordinary shared pool. Failed or partially
validated guarded attempts are terminal, including rate limits; they do not
automatically retry, heal, or fall back. Unguarded requests retain their
existing pooled-client and retry behavior.

For guarded CLI execution the same resolver compares the reviewed raw profile
and loaded command at entry and again immediately before subprocess spawn,
after command preparation and any awaited workspace MCP lock/config work.
The reviewed binding must be between zero and sixty seconds old at both checks;
the actual argv must select the exact reviewed model once. Drift returns a
terminal `attempt_route_drift` without spawning the CLI. Configured provider,
profile and model are command-selection evidence only, not service identity,
CLI executable-byte attestation, or server-model attestation.

### HTTP model-echo evidence

The legacy `model` value may fall back to the requested model; it is not proof
that the service echoed a model. HTTP results now separately retain
`model_requested`, `model_observed`, and `model_echo_status` through the
musician into the authoritative sheet checkpoint. Status is `observed` for a
nonempty response string (preserved verbatim), `absent` for a missing key,
`malformed` for a non-string, or `empty` for an empty/whitespace-only string.
Old checkpoints and unguarded non-HTTP results default all three fields to `None`.
Guarded CLI results retain `model_requested` from the reviewed command, while
`model_observed` and `model_echo_status` remain `None`: no HTTP echo was observed.
Observer events expose the status only, not the observed/requested strings.

Consumers requiring model provenance must read the completed job's retained
sheet metadata, not model-written content, logs, or the legacy model field.
Missing metadata is unverified. A response model identifier is a server
assertion; it does not attest the service's loaded weights.

The daemon records an absent or mismatched echo; it does not reject an
otherwise successful response solely for that reason. Consumers that require
an exact echo must compare the retained observed identifier with their
expected effective model before accepting the response. Their review,
authorization, and reservation checks remain outside this transport guard.

On the initial and resumed baton completion paths, the final checkpoint
(including the completion timestamp and last-attempt evidence) is acknowledged
by the existing ordered writer before completed/failed registry status and
the completion event are published. Other lifecycle transitions and exception
paths are not covered by this terminal-publication guarantee.

### Command Construction

For CLI instruments, the `PluginCliBackend` builds the command from the profile:

```
[executable] [subcommand] [auto_approve_flag] [output_format_flag value]
[model_flag model_name] [prompt_flag] <prompt> [...extra_flags]
```

The prompt is passed via `prompt_flag` (or as a positional argument if
`prompt_flag` is `null`). The backend handles output parsing, token extraction,
and error detection based on the profile configuration.

### Error Handling

Marianne classifies execution errors into categories:

- **RATE_LIMIT** — Detected via `rate_limit_patterns` or HTTP 429. The conductor
  pauses the instrument and schedules a retry when it recovers. Rate limits
  do not count as failures.
- **AUTH_FAILURE** — Detected via `auth_error_patterns`. The sheet fails
  immediately (no retry).
- **TRANSIENT** — Timeouts, killed processes, temporary failures. The conductor
  retries with exponential backoff.
- **EXECUTION_ERROR** — Other non-zero exit codes. Retried up to `max_retries`.

---

## Examples

### Using Gemini CLI for a Research Score

```yaml
name: research-with-gemini
workspace: ./workspaces/research

instrument: gemini-cli
instrument_config:
  model: gemini-2.5-flash    # Cheaper for research tasks

sheet:
  size: 1
  total_items: 3

prompt:
  template: |
    {% if sheet_num == 1 %}
    Research the topic and write an outline in {{ workspace }}/outline.md
    {% elif sheet_num == 2 %}
    Expand the outline into a full report at {{ workspace }}/report.md
    {% else %}
    Review and polish {{ workspace }}/report.md for clarity and accuracy
    {% endif %}

validations:
  - type: file_exists
    path: "{workspace}/report.md"
    condition: "sheet_num >= 2"
```

### Custom Instrument for a Private Tool

```yaml
# .marianne/instruments/internal-agent.yaml
name: internal-agent
display_name: "Internal Agent"
description: "Company internal coding agent"
kind: cli

capabilities:
  - file_editing
  - shell_access
  - tool_use

default_timeout_seconds: 3600

cli:
  command:
    executable: internal-agent
    prompt_flag: "--task"
    model_flag: "--model"
    auto_approve_flag: "--non-interactive"
    output_format_flag: "--format"
    output_format_value: "json"
    env:
      AGENT_TOKEN: "${INTERNAL_AGENT_TOKEN}"
  output:
    format: json
    result_path: "output.text"
    input_tokens_path: "usage.prompt_tokens"
    output_tokens_path: "usage.completion_tokens"
  errors:
    rate_limit_patterns:
      - "rate.?limit"
      - "throttled"
    auth_error_patterns:
      - "unauthorized"
      - "token.*expired"
```

Then use it in a score:

```yaml
instrument: internal-agent
```

---

## Troubleshooting

### Instrument not found

```
mzt instruments check my-tool
  Binary: my-tool ✗ not found
```

The executable is not on your PATH. Either install the tool or specify the full
path in your instrument profile's `executable` field.

### Rate limits not detected

If your instrument hits rate limits but Marianne doesn't detect them, add the
rate limit text to `cli.errors.rate_limit_patterns`. Use regex:

```yaml
errors:
  rate_limit_patterns:
    - "rate.?limit"           # matches "rate limit", "rate_limit"
    - "429"                   # HTTP status code in output
    - "quota.?exceeded"       # quota limit messages
    - "too.?many.?requests"   # common pattern
```

### No cost tracking

If `mzt status` shows `$0.00` for all sheets, your instrument profile likely
has no `models` section with pricing. Add model entries with `cost_per_1k_input`
and `cost_per_1k_output` to enable cost tracking.

### Token counts not extracted

If token usage is zero, check that `cli.output.input_tokens_path` and
`cli.output.output_tokens_path` point to the correct JSON paths in your tool's
output. Use the wildcard syntax (`key.*`) for nested structures where the
exact key varies.
