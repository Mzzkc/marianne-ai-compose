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
| `qwen-code` | Qwen Code CLI (`qwen`) | Saved Qwen OAuth or OpenAI-compatible API credentials |
| `openclaw` | OpenClaw CLI (`openclaw agent exec`) | Saved client config and credentials |
| `gemini-cli` | Google Gemini CLI | `GOOGLE_API_KEY` or `gcloud auth` |
| `codex-cli` | OpenAI Codex CLI | `OPENAI_API_KEY or CODEX_API_KEY` |
| `antigravity` | Google Antigravity CLI (`agy`), Gemini CLI's successor; tmux-interactive by default, print mode delivers the prompt as one stream-json line on stdin | Existing Google account/API configuration |
| `cline-cli` | Cline CLI | Provider API key |
| `aider` | Aider | Provider API key (`OPENAI_API_KEY`, etc.) |
| `goose` | Block's Goose | Provider API key |

To check which instruments are available on your system:

```bash
mzt instruments list
```

---

## Using Instruments in Scores

### Qwen Code

Install the client version used to verify this headless contract (Node.js 20+
required), then configure authentication before starting a score:

```bash
npm install -g @qwen-code/qwen-code@0.12.6
qwen --version
qwen
```

Complete the client's interactive login/setup once. Saved Qwen OAuth credentials
remain accessible through HOME. For an OpenAI-compatible provider, configure
Qwen's authentication settings and supply `OPENAI_API_KEY`, `OPENAI_BASE_URL`
and optionally `OPENAI_MODEL` or `QWEN_MODEL`. Marianne passes those four provider
variables and system variables; other provider families or custom credential
variable names require an organization/venue profile override of
`cli.command.required_env`. Provider access and billing depend on your account.

Use `instrument: qwen-code` directly or a named instrument for a model override.
This example assigns the named override to sheet 1, while sheet 2 uses the
client's configured model:

```yaml
name: qwen-example
workspace: ./workspaces/qwen-example
instrument: qwen-code
instruments:
  coder:
    profile: qwen-code
    config:
      model: your-provider-model
sheet:
  size: 1
  total_items: 2
  per_sheet_instruments:
    1: coder
prompt:
  template: "Review the code and write findings for sheet {{ sheet_num }}."
```

The profile runs headlessly with `--yolo --output-format json` and sends the
prompt over stdin. `--yolo` auto-approves tools, including file edits and shell
commands: choose an appropriate workspace and permissions. Final text and
input/output token usage are read from the terminal JSON result, including
exit-zero error envelopes. Without a score model override, Qwen chooses its
configured model; Marianne ships no assumed Qwen model prices or capacities.

This integration advertises tool use, file editing and shell access. It does
not advertise Marianne schema output, MCP injection, interactive execution or
vision support. A self-hosted model requires a separately running compatible
provider and Qwen configuration; selecting this instrument does not host a
model. The source contract is pinned to v0.12.6; live authenticated execution
was unavailable on the implementation host. Source-derived fixtures and their
[immutable vendor references](../tests/fixtures/qwen_code/README.md) document the
verified protocol separately from live access.

### OpenClaw

Install the pinned client (`npm install -g openclaw@2026.10.1`) using its
supported Node.js range (`>=24.16.0 <25 || >=26.1.0`), then complete
`openclaw onboard` before running a score. This profile uses your saved client
configuration and authentication through HOME; `OPENCLAW_CONFIG_PATH` and
`OPENCLAW_STATE_DIR` overrides are passed explicitly. Other provider environment
keys require a local `cli.command.required_env` override. Provider access and
billing depend on your account; config files and saved credential stores remain
accessible to the client.

Select `instrument: openclaw` directly or use a named per-sheet model override:

```yaml
name: openclaw-example
workspace: ./workspaces/openclaw-example
instrument: openclaw
instruments:
  coder:
    profile: openclaw
    config:
      model: your-provider-model
sheet:
  size: 1
  total_items: 2
  per_sheet_instruments:
    1: coder
prompt:
  template: "Review the code and write findings for sheet {{ sheet_num }}."
```

The generated command is `openclaw agent --json --message-file - exec` with
optional parent `--model` and `--timeout` flags. Upstream explicitly inherits
these options in `exec`. Prompts use stdin (UTF-8, upstream limit 4 MiB), avoiding
argv limits. Each turn uses temporary state and the sheet workspace as cwd;
it does not contact a Gateway or deliver messages to channels. Without a model
override, OpenClaw chooses its configured model. The native timeout is 600
seconds unless Marianne supplies a per-attempt deadline.

**Workspace safety:** exec defaults to the coding tool profile, workspace-only
filesystem tools and full shell execution policy without an approval channel.
Explicit client sandbox/tool policies take precedence. Use a trusted workspace
and inspect your OpenClaw configuration; shell execution can have effects beyond
filesystem tools' workspace restriction.

Final text comes from `final`, tokens from `usage.input` / `usage.output` when
available, and errors from `error.message`. Exit 0 means success, 1 error, 2
timeout. This profile advertises tool use, file editing and shell access; it has
no Marianne schema enforcement, MCP injection or interactive-mode contract.
The contract is pinned to v2026.10.1; live authenticated execution was not
verified on this host. See the [synthetic fixtures and immutable upstream
references](../tests/fixtures/openclaw/README.md) for the verified source contract.

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

### Capability classes

Use a class when the score needs a kind of instrument and each installation
should choose its own profile chain:

```yaml
instrument: strong
instrument_fallbacks: [local]
```

Marianne reads the packaged class map, then `~/.marianne/classes.yaml`, then
the venue's `.marianne/classes.yaml`. A higher layer replaces a class's entire
ordered chain; `null` removes one. A score alias takes precedence over a
registered profile, and a registered profile takes precedence over a class.
The resolved class chain is saved with the job, so editing the map changes new
jobs while an existing job keeps its original routes on resume. A chain may
name profiles this machine lacks: the job is admitted and starts at the first
entry that can run (`mzt validate` says which, as a V311 warning; when no
entry is installed yet the same warning says so and names the install — the
score is still admitted); only a class with no registered entry at all is
refused (V310). Use `mzt
instruments classes show --json` to inspect the winning layers and `mzt
instruments classes check` to inspect current availability. `mzt instruments
classes write --if-absent` creates a machine-specific user map without
overwriting an existing one. The writer ranks the shipped profiles; a user or
venue profile joins a generated chain only where its own `class_hints` put it
(`class_hints: {strong: "after opencode"}` in the profile YAML), so a machine
with a working GLM or Gemini route gets it into the map without a hand edit.

Class entries may select a `model`, but a score-level or sheet-level model
cannot override a class primary. Use a score alias or named profile for a
fixed model or a reviewed `expected_route`. `instrument: cli` is the
deterministic shell profile.

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
| `class_hints` | No | Where `mzt instruments classes write` seats this profile per class: `{strong: "after opencode", fast: first}` — `first`, `last`, `after <profile>` or `before <profile>`. Only a user or venue profile with a hint is considered by the writer, and it still has to pass the class's capability and availability probe; a named anchor missing from the chain seats it last (GH #469) |

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
| `schema_extra_flags` | No | | Flags appended *instead of* `extra_flags` when the sheet runs in schema mode (`response_format` through `json_schema_flag`). Unset = schema sheets inherit `extra_flags`; `[]` = none. The shipped `claude-code` profile sends `--effort high` here because `--effort max` with `--json-schema` emits nothing until killed on Claude Code 2.1.307. |
| `env` | No | `{}` | Environment variables. `${VAR}` references expand from `os.environ` |
| `required_env` | No | `null` | Parent variables allowed alongside system essentials and profile `env`. Missing or `[]` grants no additional variables. |
| `inherit_env` | No | `false` | Explicit full-environment inheritance when `required_env` is `null`. An explicit list takes precedence. Exposes unrelated credentials. |
| `supervise` | No | `true` | Launch the instrument under Marianne's Linux **instrument supervisor**: a child subreaper in the instrument's own process group. A grandchild whose parent exits (an agent's filesystem-search helper, a disowned shell) re-parents to the supervisor instead of init, and is ended and reaped when the instrument exits or the attempt is torn down. Set `false` only for an instrument the supervisor cannot wrap. Where the subreaper is unavailable (not Linux) the instrument is spawned directly and the backend's own descendant reaper (GH #406) is the only guard. |
| `max_open_files` | No | `4096` | Soft `RLIMIT_NOFILE` the supervisor sets for the instrument and everything it spawns, so a runaway search or a leaking helper is bounded to its share of the host's file table (the conductor's own limit on a server is often 1,048,576). Never raised above the hard limit; `null` leaves the conductor's limit in place. Ignored when `supervise` is `false`. |
| `io_priority` | No | `low` | I/O scheduling class the supervisor gives the instrument and everything it spawns: `low` (best-effort, lowest priority) keeps an agent's whole-disk search from stalling the conductor's own state reads; `idle` yields to every other process; `inherit` keeps the conductor's class. Ignored when `supervise` is `false`. |
| `prompt_via_stdin` | No | `true` | Write the assembled prompt to the child's stdin instead of argv. Prompts routinely exceed 100 KB with prelude/cadenza injection, and Linux caps a single argv entry at 128 KiB (`E2BIG`, "Argument list too long" before the process starts). Set `false` only for a CLI that cannot read stdin |
| `stdin_sentinel` | No | | What to put after `prompt_flag` when the prompt goes to stdin: `-` for Claude Code's `-p -`; `""` keeps the flag with an empty value (Antigravity's `-p ''`). Unset = omit the flag entirely |
| `json_schema_flag` | No | | Flag that makes the CLI enforce a JSON schema, e.g. Claude Code's `--json-schema` (the shipped `claude-code` profile declares it). With it, a sheet's `instrument_config.response_format` (`type: json_schema`) is honoured and the terminal JSON's `structured_output` becomes the result; without it, such a sheet is refused at dispatch and `mzt validate` warns V311. Requires `output.format: json`. Schema mode rides on the model calling Claude Code's StructuredOutput tool: measured 2026-10-21, Claude models did 3/3, while the GLM-5.3 route through Claude Code missed it 1 time in 3 (it answered in prose, which the parser refuses and the baton retries). Prove the model you route with `mzt instruments check claude-code --live --model <m>`. A CLI that prints an array of events for `--output-format json` (Claude Code with `verbose: true` in its settings) is read from its last `type: result` object |
| `stdin_json_template` | No | | For a CLI whose only stdin path is a structured stream: this object is written as ONE JSON line with every `"{prompt}"` string replaced by the prompt (nested keys are walked). Antigravity: `{event: user, message: {role: user, content: "{prompt}"}}` with `--input-format stream-json` |

#### Environment the musician receives

CLI instruments receive only system essentials (`PATH`, `HOME`, `USER`,
terminal/locale/temp settings and the listed XDG paths), variables named in
`cli.command.required_env`, and profile `env` entries. Missing `required_env`
and `[]` both grant no additional parent variables. The same policy applies to
exec and interactive/tmux launches; panes replace the tmux server environment.
Profile `env` entries expand `${VAR}` from the parent and override allowed values.

To migrate a profile that relied on ambient credentials, declare the variables
its CLI actually reads:

```yaml
cli:
  command:
    executable: my-agent
    required_env: [MY_AGENT_API_KEY, HTTPS_PROXY]
```

Use `required_env: []` for a CLI using its own credential store. As a temporary
migration opt-out, `cli.command.inherit_env: true` inherits the full conductor
environment when `required_env` is absent or null. An explicit `required_env`
list (including `[]`) still filters even with this opt-out. `mzt doctor` names
ready agent profiles using full inheritance when credential-shaped variables
are set, without printing values (GH #335).

The shipped `aider`, `cline-cli`, `crush` and shell `cli` profiles explicitly
use `required_env: []`. Cline can use its configured settings; other tools may
use their own credential stores where supported. For environment-based provider
authentication, set `cli.command.required_env` in your local profile to the
specific key names your selected provider needs (for example, `[OPENAI_API_KEY]`
for an OpenAI route). Shell validation steps that need build credentials or
other non-system variables must likewise declare them. No provider keys are
granted automatically to these four profiles.

This filters environment variables only. `HOME` and XDG paths remain available;
file-based credentials, daemon MCP children and hooks require separate isolation.

Every CLI and interactive instrument is also started with
`MARIANNE_SCRATCH` set to the job's scratch root `<workspace>/.marianne-scratch`
(created before launch). Anything an agent needs outside the workspace proper,
such as a probe clone, a mutant tree or a git worktree, belongs under that path:
the conductor deletes it when the job ends (`workspace_lifecycle.scratch`,
GH #460). A clone made in a system temp directory has no owner once the sheet
ends and is never reaped.

#### `cli` — Lifecycle bounds

Every CLI attempt is one process tree. The backend starts the instrument in
its own session and process group; with `command.supervise` (the default on
Linux) the direct child is the instrument supervisor, which shares that
group and adopts every descendant whose parent exits. Teardown signals the
group, the supervisor ends and reaps what it adopted, and the backend's
descendant reaper settles anything the supervisor could not see. On Pallas
(20 Oct 2026) eight `bfs`/`ugrep` searches from `/` outlived their sheets at
ppid 1 for hours and saturated the disk; the supervisor is the fix, and the
`low` I/O class bounds the damage a live search can do.


| Field | Default | Description |
|-------|---------|-------------|
| `post_exit_drain_grace_seconds` | `3.0` | Maximum time to drain inherited pipes after the CLI parent exits |
| `first_output_timeout_seconds` | unset | Seconds the CLI may run with **zero bytes** on stdout and stderr before the attempt is ended as a stall (GH #434). A streaming agent emits its init event within seconds of a healthy start, so silence is a dead session, not thinking: the attempt ends with a `Stale execution` marker, the baton retries after 120 s and then falls back, and the sheet timeout is not burned. The shipped `jsonl` profiles (codex-cli, opencode, antigravity) set `90`; `json` and `text` profiles leave it unset because they emit nothing until the end (claude-code's `--output-format json` is one: its stalls are bounded only by the sheet timeout until that profile moves to stream-json). A long silence AFTER the first byte is never struck by this bound. |

#### `cli.output` — How to Parse the Result

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `format` | No | `text` | `text`, `json`, or `jsonl` |
| `result_path` | No | | JSON dot-path to response text (`result`, `response`) |
| `error_path` | No | | JSON dot-path to error message (`error.message`) |
| `error_flag_path` | No | | JSON dot-path to a terminal boolean error flag; exactly `true` fails the attempt even at a successful exit code (unset preserves exit-code behavior; `qwen-code` uses `is_error`) |
| `completion_event_type` | No | | For JSONL: event type signaling completion |
| `event_type_key` | No | `type` | For JSONL: the key that carries each event's type (`event` for Antigravity's stream-json) |
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
| `unavailable_patterns` | `[]` | Regex patterns in the harness's own startup refusal meaning it cannot run here at all: no provider configured, provider not found, backend not reachable (GH #389). A match types the attempt `INSTRUMENT_UNAVAILABLE`: the sheet advances its fallback chain on the first attempt without charging a retry, exactly as a missing binary does, instead of running to its timeout per misconfigured entry. The shipped `goose`, `crush` and `opencode` profiles carry their measured refusals (`No provider configured`, `No providers configured`, `Provider not found` / `ProviderModelNotFoundError`). |
| `capacity_patterns` | `[]` | Regex patterns indicating capacity/overload errors (retried) |
| `timeout_patterns` | `[]` | Regex patterns indicating timeout errors (retried) |
| `crash_patterns` | `[]` | Regex patterns indicating a process crash or fatal error |
| `stale_patterns` | `[]` | Regex patterns indicating a stale execution |

These patterns are the instrument's own word on its failures. A
`rate_limit_patterns` match pauses the instrument; an `auth_error_patterns`
match types the result as a credential rejection, which is the only way text
can produce `AUTH_FAILURE` (the built-in classifier's generic auth words are
diagnosis only); an `unavailable_patterns` match types it as the route being
unusable on this machine, the only way text can produce `INSTRUMENT_UNAVAILABLE`.
First match wins in the order auth, unavailable, crash, stale, timeout,
capacity, and every pattern is compiled when the profile loads, so a bad regex
is refused there rather than mid-attempt. Keep them anchored to the
instrument's real messages.

An interactive (tmux) attempt whose agent process ends without writing the
completion marker fails as `session_lost` with the cause tmux recorded: the
pane is created with `remain-on-exit` so the driver reads `pane_dead_status`
/ `pane_dead_signal` and the attempt's error reads `agent process killed by
SIGKILL before signalling completion` or `agent process exited with status N
…`. Only when tmux no longer holds the session at all (server gone, session
killed by name) does it read `agent session vanished …`. The driver kills the
session on every outcome, so a dead pane never outlives its attempt.

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

The shipped `ollama` profile is the minimal shape (local, unauthenticated). Its
`default_model` must be one the local server actually serves: `mzt validate`,
`mzt instruments classes check` and dispatch preflight list the server's models
(`/v1/models`) and report the profile unavailable, naming what is served, when
the default is missing; set `instrument_config.model` in the score or
`default_model` in a user copy of the profile to a pulled tag. A local server
that does not answer at all is a reachability warning (V215), not a model miss.

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
`prompt_flag` is `null`) only when `prompt_via_stdin` is `false`; by default it
is written to the child's stdin (raw text, or the `stdin_json_template`
envelope) and argv carries at most `prompt_flag` + `stdin_sentinel`. A profile
with `prompt_via_stdin: false` and no sentinel is kept in argv even above 32 KB
(the backend logs `plugin_cli_stdin_not_supported_for_large_prompt`), and a
prompt past the kernel's per-argument cap fails to spawn with `E2BIG`; give such
a CLI a stdin contract instead. The backend handles output parsing, token extraction,
and error detection based on the profile configuration.

### Error Handling

Marianne classifies execution errors into categories:

- **RATE_LIMIT** — Detected via `rate_limit_patterns` or HTTP 429. The conductor
  pauses the instrument and schedules a retry when it recovers. Rate limits
  do not count as failures.
- **AUTH_FAILURE** — The backend typed a credential rejection for the route:
  a CLI profile's own `auth_error_patterns` matched, an HTTP route returned
  401/403, or an interactive session hit its login gate. The sheet leaves that
  instrument at once and advances its fallback chain. Auth-looking words in an
  agent's own output ("Permission denied (publickey)", a test asserting 403, a
  traceback's "line 403") are not a credential failure: that attempt is an
  ordinary execution error retried on the same instrument, with E502 kept as
  the diagnosis.
- **INSTRUMENT_UNAVAILABLE** — The route cannot be used on this machine: the
  binary is not on PATH, the HTTP endpoint refuses the connection, or the
  harness started and refused in its own voice (a CLI profile's
  `unavailable_patterns` matched: `No provider configured`, `Provider not
  found`). The sheet advances its fallback chain on the first attempt without
  charging a retry (GH #418, #389); with no chain left it takes the ordinary
  bounded retry path. The circuit breaker still counts the failure.
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
