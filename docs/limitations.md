# Known Limitations

An honest accounting of what Marianne doesn't do, what's incomplete, and where sharp edges exist. Each limitation includes the technical reason it exists and any available workaround.

---

## Execution Model

### Conductor Required for Execution

`mzt run` routes all jobs through the conductor. There is no standalone execution mode.

**What this means:** You must start the conductor (`mzt start`) before running any job. Only `mzt validate` and `mzt run --dry-run` work without a running conductor.

**Why:** Centralized resource management, rate-limit coordination, and backpressure control require a single process to track all active jobs. The conductor also enables the persistent job registry and crash recovery.

**Workaround:** None. Start the conductor first:

```bash
mzt start            # background
mzt run my-job.yaml  # now works
```

**Status:** Permanent design choice.

---

### ~~Escalation Incompatible with Daemon~~ (resolved)

**Resolved (#361):** escalation no longer requires interactive prompts. With
`mzt run my-job.yaml --escalation` (no `--self-healing` needed), a sheet that
exhausts its retries pauses in FERMATA instead of failing. `mzt status` shows
the pending decision; resolve it with `mzt resolve <job_id> <sheet> <decision>`
(decisions: `retry`, `skip`, `accept`, `fail`) or by creating a marker file
under the job workspace (`markers/fermata/<job_id>/sheet-<N>.<decision>`).
FERMATA survives conductor restarts.

---

### Output Streaming Is Live-View Only

Live output streaming exists: instrument stdout/stderr is drained in
real time, credential-redacted on complete lines, and held in bounded
per-sheet ring buffers (~256 KB each, drop-oldest) that
`mzt watch SCORE [SHEET]` tails over IPC.

**What this means:**

- You CAN watch musician output as it generates: `mzt watch my-score`
  (all sheets, lines tagged `[s<n>]`) or `mzt watch my-score 3`.
- The ring is a live view, not a transcript: a conductor restart clears
  it, and once a sheet's ring exceeds its budget the oldest lines are
  dropped with an explicit `[dropped N lines]` marker. A slow terminal
  never slows the conductor — backpressure drops lines with a marker
  rather than blocking.
- Persisted evidence remains the recorded output tails: `stdout_tail`
  is truncated to the last 500 characters for log display and ~10 KB
  for self-healing diagnostic context.

**Relevant constants** (from `src/marianne/core/constants.py`):

| Constant | Value | Purpose |
|----------|-------|---------|
| `TRUNCATE_STDOUT_TAIL_CHARS` | 500 | Log display truncation |
| `HEALING_CONTEXT_TAIL_CHARS` | 10,000 | Self-healing diagnostic context |

**Status:** Live tailing is supported. Full output transcripts are not
persisted by design — bounded disk use beats complete archives.

---

## Daemon Internals

### The Baton Is the Only Execution Engine

The baton (`src/marianne/daemon/baton/`) is the sole execution engine:
event-driven per-sheet dispatch, per-instrument concurrency, timer-based
retry, rate limit auto-resume, and restart recovery. The legacy
monolithic runner was deleted in April 2026 and the `use_baton` toggle
no longer exists — there is no fallback engine and no configuration to
select one.

**Status:** Complete. Any reference to `use_baton` or a legacy runner in
older material is historical.

---

### Single-Machine Only

Marianne runs on a single machine. There is no distributed execution, remote workers, or cluster mode.

**What this means:**

- The daemon binds to a local Unix socket for IPC
- Worktree isolation creates local git worktrees
- All concurrent jobs share the same machine's CPU, memory, and network

**Why:** The Unix socket IPC layer (`src/marianne/daemon/ipc/`) is inherently local. Distributed coordination would require a fundamentally different architecture (message queues, consensus protocols, distributed state).

**Workaround:** Run separate Marianne instances on different machines with different workspaces. They won't coordinate rate limits, but they'll operate independently.

**Status:** Permanent design choice. Distributed execution is out of scope.

---

## Instrument Support

### Instrument Plugin System

Marianne supports multiple AI instruments through a config-driven plugin system. Twelve instruments ship as built-in profiles, and users can add custom instruments via YAML files.

**Built-in instruments:** `aider`, `antigravity`, `claude-code`, `cli`,
`cline-cli`, `codex-cli`, `crush`, `gemini-cli`, `goose`, `gpt-5.6`,
`ollama`, `opencode`

**Execution model:** instrument profiles select shared CLI, interactive CLI,
or OpenAI-compatible HTTP executors. Marianne has no provider-specific native
backend classes. Ollama ships as an HTTP profile because it exposes the shared
OpenAI-compatible contract.

Run `mzt instruments list` to see all available instruments and their status.

### Error Classification Is Claude-Tuned

The error classifier (`src/marianne/core/errors/classifier.py`) was originally designed around Claude CLI output patterns. While it handles common rate-limit patterns across providers, edge cases in non-Claude instruments may produce suboptimal error recovery.

**What this means:**

- Default rate-limit patterns (e.g., `hit.*limit`, `limit.*resets?`, `daily.*limit`) were derived from Claude CLI output
- The `PluginCliBackend` uses per-instrument error patterns from YAML profiles (e.g., `gemini-cli.yaml` defines its own `rate_limit_patterns` and `auth_error_patterns`)
- Instrument profiles can declare `crash_patterns`, `stale_patterns`, `timeout_patterns`, and `capacity_patterns` for fine-grained classification

**Workaround:** Define instrument-specific error patterns in the instrument profile YAML. The `PluginCliBackend` uses these patterns instead of the global defaults.

**Status:** Improving. Each instrument profile is verified against the actual CLI tool's output.

### MCP Servers Are Disabled by Default — No Per-Score Opt-In

Built-in instrument profiles carry `mcp_disable_args` (for claude-code:
`--strict-mcp-config` with an empty server config), and the command builder
applies them on every execution unless the conductor's shared MCP pool
provides a config file. This prevents ambient-server child-process explosion
(F-271) — but it also means a score cannot simply turn MCP tools back on.

**What this means:**

- Sheets run without MCP tools by default, headless and interactive alike
  (interactive sessions inherit the disable args unless the profile sets
  `interactive.inherit_mcp_disable_args: false`)
- There is no `instrument_config` key to re-enable MCP per score — keys like
  `disable_mcp:` from the removed `backend:` era are silently ignored

**Workaround:** Copy the builtin profile to `~/.marianne/instruments/`,
remove (or edit) its `mcp_disable_args`, and point the score at the custom
profile. For curated tool access, the conductor's shared MCP pool supplies
a config file via the profile's `mcp_config_flag` or
`mcp_config_workspace_path`; profiles such as Gemini CLI can merge generated
servers into a broader JSON settings file with
`mcp_config_workspace_merge_key`.

**Status:** A proper technique system for per-sheet MCP/skill configuration is planned but not yet implemented.

---

## Architecture Complexity

### Learning System Complexity

The learning store has 16 modules in `src/marianne/learning/store/`:

```
__init__.py               base.py
budget.py                 drift.py
escalation.py             executions.py
models.py                 patterns.py
patterns_broadcast.py     patterns_crud.py
patterns_lifecycle.py     patterns_quarantine.py
patterns_query.py         patterns_success_factors.py
patterns_trust.py         rate_limits.py
```

**What this means:** The learning system tracks pattern drift, entropy, trust scores, quarantine state, success factors, and budget constraints. For simple jobs (run 5 sheets sequentially), most of this infrastructure is unused overhead.

**Why:** Designed for long-running, repeated jobs where learning from past failures materially improves success rates. The complexity pays for itself in those scenarios.

**Workaround:** Learning is opt-in via the `learning:` config section. Omit it for simple jobs and the store still initializes but doesn't collect meaningful data.

**Status:** Permanent. The learning system is a core differentiator.

---

## Dashboard

### Dashboard Coverage

The dashboard UI is functional but has limited coverage:

**What works:**

- Job listing and detail views
- Sheet status display with stdout/stderr tails
- Score editor with real-time validation
- SSE streaming infrastructure (`SSEManager` with heartbeats, broadcasts, per-job client tracking)
- SSE integration in job detail and dashboard index pages via HTMX

**What's limited:**

- Real-time updates depend on the SSE connection; no polling fallback
- No historical trend visualization
- No cost tracking display (CostMixin tracks costs, but the dashboard doesn't surface them)
- No learning insights visualization

**Why:** The dashboard was built as a monitoring tool, not a full management UI. The SSE infrastructure is solid but the UI only consumes a subset of available events.

**Status:** Functional for monitoring. Not a priority for expansion.

---

## Validation

### Validation Condition Expressions

Validation `condition` fields support numeric comparison expressions and boolean AND:

```yaml
validations:
  - type: file_exists
    path: "output.md"
    condition: "sheet_num >= 3"                        # Simple comparison
    condition: "stage == 2 and instance == 1"          # Boolean AND with fan-out variables
    condition: "sheet_num >= 3 and sheet_num <= 5"     # Range check
```

**Supported operators:** `>=`, `<=`, `==`, `!=`, `>`, `<`

**Supported variables:** At runtime, conditions are evaluated against
`Sheet.template_variables()`: built-ins such as `sheet_num`, `total_sheets`,
`movement`, `voice`, `voice_count`, `total_movements`, and the
backward-compatible `stage`, `instance`, `fan_count`, `total_stages`, plus
numeric values from `prompt.variables`. During `mzt validate` preview,
condition applicability is computed from built-ins only.

**What's NOT supported:**

- Complex expressions: `sheet_num in [1, 3, 5]`
- Boolean OR: `sheet_num == 3 or sheet_num == 5`
- Nested expressions: `(sheet_num > 2) and (stage < 3)`
- String comparisons: `environment == "prod"`

**Why:** The condition evaluator in `engine.py` splits on `" and "` and
evaluates each clause as a `variable operator integer` triple. Missing or
non-numeric variables make the rule not apply. Malformed expressions still fall
back to "always apply" behavior, so use simple numeric conditions.

**Workaround:** For OR logic, use multiple validation entries, each with a simple condition.

**Status:** Sufficient for current use cases.

---

## Configuration

### Validation Stage Limits

Validation stages are capped at 1-10. You cannot define more than 10 sequential validation stages per sheet.

**Why:** A practical limit to prevent unbounded validation chains. The `stage` field is validated with `ge=1, le=10`.

**Status:** Permanent. 10 stages covers all practical use cases.

### Process Timeout Default

The default instrument timeout is **1800 seconds** (30 minutes). This is the timeout users should be aware of.

There is also an internal constant `PROCESS_DEFAULT_TIMEOUT_SECONDS = 300` used as a fallback when no config is loaded, but this is never reached in normal operation — the Pydantic model always provides the 1800s default.

**Workaround:** Override the timeout in your score:

```yaml
instrument_config:
  timeout_seconds: 3600  # 60 minutes for long-running sheets
```

**Status:** Permanent. Explicit timeouts are safer than high defaults.
