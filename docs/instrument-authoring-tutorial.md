# Instrument Authoring Tutorial

This tutorial walks you through wrapping any CLI tool as a Marianne instrument
profile. By the end, you will have a working YAML profile that lets Marianne
execute scores through your tool — complete with output parsing, error detection,
and cost tracking.

**Prerequisites:** A working Marianne installation (`mzt doctor` passes) and a
CLI tool you want to integrate.

---

## What Is an Instrument Profile?

An instrument profile is a YAML file that teaches Marianne how to talk to a CLI
tool. It describes three things:

1. **How to build the command** — which executable to call, how to pass the prompt,
   which flags to set
2. **How to parse the output** — whether stdout is plain text, JSON, or JSONL,
   and where to find the response and token counts
3. **How to detect errors** — which patterns in stderr indicate rate limits or
   auth failures

Marianne loads profiles from three directories (later ones override earlier):

| Directory | Scope | Precedence |
|-----------|-------|------------|
| Built-in (shipped with Marianne) | All projects | Lowest |
| `~/.marianne/instruments/` | All projects on this machine | Middle |
| `.marianne/instruments/` | This project only | Highest |

When a score references `instrument: my-tool`, the conductor looks up the name
in the registry and creates a backend configured from the profile.

---

## Quick Start: Wrap a Tool in 5 Minutes

Let's wrap a hypothetical tool called `smartcli` that accepts a `--prompt` flag
and writes plain text to stdout.

### Step 1: Create the Profile

Create `~/.marianne/instruments/smartcli.yaml`:

```yaml
name: smartcli
display_name: "SmartCLI"
description: "My custom AI CLI tool"
kind: cli

capabilities:
  - file_editing
  - shell_access

default_timeout_seconds: 1800

cli:
  command:
    executable: smartcli
    prompt_flag: "--prompt"
    auto_approve_flag: "--yes"
  output:
    format: text
  errors:
    rate_limit_patterns:
      - "rate.?limit"
      - "429"
```

That is a complete, working profile. Every field has a purpose:

- `name` is the identifier you use in scores (`instrument: smartcli`)
- `display_name` is what shows up in `mzt instruments list` and `mzt status`
- `kind: cli` tells Marianne this is a command-line tool (the only kind in v1)
- `capabilities` describe what the tool can do (informational today, used for
  conductor routing in future versions)
- `default_timeout_seconds` caps how long a single sheet execution can run
- `cli.command` describes how to build the shell command
- `cli.output` describes how to read the result
- `cli.errors` teaches Marianne to recognize error patterns

### Step 2: Verify It

```bash
mzt instruments check smartcli
```

Expected output:

```
Checking smartcli...
  Display name:  SmartCLI
  Description:   My custom AI CLI tool
  Kind:          cli
  Binary:        /usr/local/bin/smartcli ✓
  Capabilities:  file_editing, shell_access

smartcli is ready.
```

If the binary is not found, install it or use a full path in `executable`.

### Step 3: Use It in a Score

```yaml
name: my-task
workspace: ./workspaces/my-task

instrument: smartcli

sheet:
  size: 1
  total_items: 1

prompt:
  template: |
    Write a summary of the project and save it to {{ workspace }}/summary.md

validations:
  - type: file_exists
    path: "{workspace}/summary.md"
```

Run it:

```bash
mzt start       # If conductor isn't running
mzt run my-task.yaml
```

---

## Understanding Command Construction

When Marianne executes a sheet, the `PluginCliBackend` assembles the command
from your profile:

```
[executable] [subcommand] [auto_approve_flag] [output_format_flag value]
[model_flag model] [prompt_flag] <prompt> [...extra_flags]
```

For our SmartCLI example, the executed command would be:

```bash
smartcli --yes --prompt "Write a summary of the project..."
```

### Prompt Delivery

How the prompt reaches the tool depends on `prompt_flag`:

| `prompt_flag` value | Command |
|---------------------|---------|
| `"--prompt"` | `smartcli --prompt "the prompt text"` |
| `"-p"` | `smartcli -p "the prompt text"` |
| `null` (omitted) | `smartcli "the prompt text"` (positional argument) |

### Subcommands

Some tools have subcommands. For example, OpenAI's Codex CLI uses `codex exec`:

```yaml
cli:
  command:
    executable: codex
    subcommand: exec
    prompt_flag: null          # Codex takes prompt as positional arg
```

### Extra Flags

Add fixed flags that are always included:

```yaml
cli:
  command:
    executable: my-tool
    prompt_flag: "--task"
    extra_flags:
      - "--no-color"
      - "--max-tokens=8192"
```

### Environment Variables

Pass environment variables to the subprocess. Use `${VAR}` to reference
variables from your shell environment:

```yaml
cli:
  command:
    executable: my-tool
    prompt_flag: "--prompt"
    env:
      API_TOKEN: "${MY_TOOL_API_TOKEN}"
      REGION: "us-east-1"
```

---

## Output Parsing

The `cli.output` section controls how Marianne reads the tool's response.

### Text Mode (Default)

The simplest mode — stdout is the result, no parsing:

```yaml
cli:
  output:
    format: text
```

Use this for tools like Aider that write plain text. Token counts will be
estimated from character count (less accurate).

### JSON Mode

Parse stdout as JSON and extract the response via a dot-path:

```yaml
cli:
  output:
    format: json
    result_path: "output.text"
    input_tokens_path: "usage.prompt_tokens"
    output_tokens_path: "usage.completion_tokens"
```

The dot-path syntax supports:
- `key.subkey` — nested access
- `key[0]` — array indexing
- `key.*` — wildcard (useful when exact keys vary)

### JSONL Mode

For tools that stream JSON lines (one JSON object per line):

```yaml
cli:
  output:
    format: jsonl
    completion_event_type: "result"
    completion_event_filter:
      type: "final"
    result_path: "content"
```

Marianne finds the line matching `completion_event_type` and
`completion_event_filter`, then extracts the response via `result_path`.

---

## Error Detection

Marianne classifies errors into categories that determine retry behavior:

| Category | Behavior | Configured By |
|----------|----------|---------------|
| `RATE_LIMIT` | Pause instrument, retry when recovered | `rate_limit_patterns` |
| `AUTH_FAILURE` | Fail immediately, no retry | `auth_error_patterns` |
| `TRANSIENT` | Retry with exponential backoff | Built-in detection |
| `EXECUTION_ERROR` | Retry up to `max_retries` | Non-zero exit code |

### Rate Limit Patterns

Add regex patterns that match your tool's rate limit messages:

```yaml
cli:
  errors:
    rate_limit_patterns:
      - "rate.?limit"
      - "429"
      - "too.?many.?requests"
      - "quota.?exceeded"
```

When a pattern matches in stdout or stderr, Marianne classifies the error as
`RATE_LIMIT` and pauses the instrument instead of counting it as a failure.

### Auth Error Patterns

```yaml
cli:
  errors:
    auth_error_patterns:
      - "unauthorized"
      - "token.*expired"
      - "invalid.*api.?key"
```

Auth errors fail the sheet immediately — there is no point retrying with the
same credentials.

### Success Exit Codes

By default, exit code 0 means success. Some tools use different conventions:

```yaml
cli:
  errors:
    success_exit_codes: [0, 2]    # Treat exit code 2 as success too
```

---

## Adding Model Metadata

Model entries enable cost tracking in `mzt status` and context budget
calculations. Without them, costs show `$0.00`.

```yaml
models:
  - name: smart-pro
    context_window: 200000
    cost_per_1k_input: 0.003
    cost_per_1k_output: 0.015
    max_output_tokens: 32768

  - name: smart-mini
    context_window: 128000
    cost_per_1k_input: 0.0001
    cost_per_1k_output: 0.0004
    max_output_tokens: 16384

default_model: smart-pro
```

Select a model in your score with `instrument_config`:

```yaml
instrument: smartcli
instrument_config:
  model: smart-mini
```

If the profile has a `model_flag`, Marianne passes the model name to the CLI:

```yaml
cli:
  command:
    executable: smartcli
    model_flag: "--model"
```

---

## Complete Example: Wrapping a Real Tool

Here is a full profile for a fictional company-internal agent with JSON output,
model selection, and comprehensive error detection:

```yaml
# .marianne/instruments/internal-agent.yaml

name: internal-agent
display_name: "Internal Agent"
description: "Company internal coding agent with tool use"
kind: cli

capabilities:
  - file_editing
  - shell_access
  - tool_use
  - structured_output

default_timeout_seconds: 3600
default_model: agent-v2

models:
  - name: agent-v2
    context_window: 500000
    cost_per_1k_input: 0.002
    cost_per_1k_output: 0.008
    max_output_tokens: 65536
  - name: agent-v2-mini
    context_window: 200000
    cost_per_1k_input: 0.0005
    cost_per_1k_output: 0.002
    max_output_tokens: 32768

cli:
  command:
    executable: internal-agent
    prompt_flag: "--task"
    model_flag: "--model"
    auto_approve_flag: "--non-interactive"
    output_format_flag: "--format"
    output_format_value: "json"
    working_dir_flag: "--workdir"
    env:
      AGENT_TOKEN: "${INTERNAL_AGENT_TOKEN}"
  output:
    format: json
    result_path: "output.text"
    error_path: "error.message"
    input_tokens_path: "usage.prompt_tokens"
    output_tokens_path: "usage.completion_tokens"
  errors:
    success_exit_codes: [0]
    rate_limit_patterns:
      - "rate.?limit"
      - "throttled"
      - "429"
      - "too.?many"
    auth_error_patterns:
      - "unauthorized"
      - "token.*expired"
      - "forbidden"
```

---

## Troubleshooting

### Binary Not Found

```
mzt instruments check my-tool
  Binary: my-tool ✗ not found
```

The executable is not on your PATH. Solutions:
- Install the tool
- Use the full path: `executable: /opt/tools/my-tool`
- Add the tool's directory to PATH

### Rate Limits Not Detected

If your tool hits rate limits but Marianne retries instead of pausing, the
rate limit text does not match any `rate_limit_patterns`. Run the tool manually,
capture the error output, and add a regex that matches it.

### No Cost Tracking

`mzt status` shows `$0.00` for all sheets — your profile has no `models` section
with pricing. Add model entries with `cost_per_1k_input` and `cost_per_1k_output`.

### Token Counts Always Zero

Check that `input_tokens_path` and `output_tokens_path` match the actual JSON
structure of your tool's output. Run the tool with `--format json` (or equivalent)
and inspect the output to find the correct paths.

### Cost Estimates Show `~$X.XX (est.)`

This means token counts are being estimated from character count rather than
extracted from structured output. Switch your profile to `format: json` and
set the token paths for accurate tracking.

### Profile Not Loading

Ensure your file:
- Is in `~/.marianne/instruments/` or `.marianne/instruments/`
- Has a `.yaml` or `.yml` extension
- Has a valid `name` field that does not conflict with a built-in profile
  (unless you intend to override it)

Restart the conductor after adding profiles — profiles are loaded at startup.

---

## Next Steps

- [Instrument Guide](instrument-guide.md) — Full reference for all profile fields and built-in instruments
- [Score Writing Guide](score-writing-guide.md) — How to use instruments in scores, including `instrument_config` overrides
- [CLI Reference](cli-reference.md) — `mzt instruments list`, `mzt instruments check`, and `mzt doctor`
- [Getting Started](getting-started.md) — If you are new to Marianne
