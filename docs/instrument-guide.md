# Instrument Guide

Mozart uses **instruments** to execute scores. An instrument is any AI tool that
can receive a prompt and produce output — Claude Code, Gemini CLI, Codex CLI,
Aider, Goose, or any CLI tool you configure. The conductor assigns musicians
(AI agents) to instruments and manages execution across all of them.

This guide covers how to use existing instruments, how to add your own, and
how the instrument system works.

---

## Quick Reference

```bash
# See what instruments are available
mozart instruments list

# Check if a specific instrument is ready
mozart instruments check gemini-cli

# Full environment health check
mozart doctor
```

---

## Built-in Instruments

Mozart ships with 10 instruments: 4 native backends (built into the Python code)
and 6 config-driven profiles (YAML files).

### Native Backends

These are built into Mozart and require no configuration beyond installing
the tool and authenticating:

| Name | Tool | Auth |
|------|------|------|
| `claude_cli` | Claude CLI (`claude`) | `claude login` |
| `anthropic_api` | Anthropic Messages API | `ANTHROPIC_API_KEY` env var |
| `ollama` | Ollama local server | None (runs locally) |
| `recursive_light` | Recursive Light Framework | RLF credentials |

### Config-Driven Profiles

These ship as YAML profiles bundled with Mozart and are loaded at conductor
startup:

| Name | Tool | Auth |
|------|------|------|
| `claude-code` | Claude Code CLI | `claude login` |
| `gemini-cli` | Google Gemini CLI | `GOOGLE_API_KEY` or `gcloud auth` |
| `codex-cli` | OpenAI Codex CLI | `CODEX_API_KEY` |
| `cline-cli` | Cline CLI | Provider API key |
| `aider` | Aider | Provider API key (`OPENAI_API_KEY`, etc.) |
| `goose` | Block's Goose | Provider API key |

To check which instruments are available on your system:

```bash
mozart instruments list
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

### The `backend:` Field (Legacy)

The older `backend:` field continues to work unchanged:

```yaml
backend:
  type: claude_cli
  skip_permissions: true
  timeout_seconds: 600
```

Both `instrument:` and `backend:` specify the same thing — which tool executes
your score. You cannot use both in the same score (validation error). New scores
should prefer `instrument:`.

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

---

## Adding Your Own Instruments

Any CLI tool that accepts a prompt and produces output can become a Mozart
instrument. You write a YAML profile describing the tool's CLI interface and
drop it in a directory Mozart scans.

### Profile Directories

Mozart loads instrument profiles from three directories, in order:

1. **Built-in** — shipped with Mozart (lowest precedence)
2. **Organization** — `~/.mozart/instruments/` (shared across all projects)
3. **Venue** — `.mozart/instruments/` (project-specific, highest precedence)

Later directories override earlier ones on name collision. This lets you
customize a built-in profile for your project without modifying Mozart's source.

### Writing a Profile

Here is a minimal profile for a hypothetical CLI tool:

```yaml
# ~/.mozart/instruments/my-tool.yaml

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

Save it to `~/.mozart/instruments/my-tool.yaml`, then verify:

```bash
mozart instruments check my-tool
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

These patterns supplement Mozart's built-in error classifier. When a pattern
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

Model metadata enables cost tracking in `mozart status` and context budget
calculation. If you omit models, cost tracking shows `$0.00` and context
budget uses a conservative default.

---

## How the Instrument System Works

### Loading Order

At conductor startup:

1. **Native instruments** are registered first (4 built-in Python backends)
2. **Built-in YAML profiles** are loaded from Mozart's bundled instruments directory
3. **Organization profiles** from `~/.mozart/instruments/` override built-ins
4. **Venue profiles** from `.mozart/instruments/` override everything

The result is a single `InstrumentRegistry` mapping names to profiles. When a
score references `instrument: gemini-cli`, the conductor looks up that name
in the registry and creates a `PluginCliBackend` configured from the profile.

### Score Resolution

When a score is submitted, the instrument is resolved:

1. If the score has `instrument:` — look up the name in the registry
2. If the score has `backend:` — use the native backend directly
3. If neither — default to `claude_cli`

Both paths produce a `Backend` instance that the conductor uses to execute sheets.

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

Mozart classifies execution errors into categories:

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
# .mozart/instruments/internal-agent.yaml
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
mozart instruments check my-tool
  Binary: my-tool ✗ not found
```

The executable is not on your PATH. Either install the tool or specify the full
path in your instrument profile's `executable` field.

### Rate limits not detected

If your instrument hits rate limits but Mozart doesn't detect them, add the
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

If `mozart status` shows `$0.00` for all sheets, your instrument profile likely
has no `models` section with pricing. Add model entries with `cost_per_1k_input`
and `cost_per_1k_output` to enable cost tracking.

### Token counts not extracted

If token usage is zero, check that `cli.output.input_tokens_path` and
`cli.output.output_tokens_path` point to the correct JSON paths in your tool's
output. Use the wildcard syntax (`key.*`) for nested structures where the
exact key varies.
