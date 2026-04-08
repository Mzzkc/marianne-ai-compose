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

### Escalation Incompatible with Daemon

The `--escalation` flag (human-in-the-loop prompts for low-confidence sheets) is explicitly blocked when the daemon routes jobs.

**What this means:** You cannot use `mzt run my-job.yaml --escalation`. The CLI exits with an error:

> `--escalation requires interactive console prompts which are not available in daemon mode. Escalation is not currently supported.`

**Why:** The daemon runs jobs as background tasks without an attached terminal. Interactive console prompts have no UI surface to display through.

**Workaround:** None currently. The escalation feature exists in the codebase (`src/marianne/learning/store/escalation.py`) but has no supported execution path.

**Status:** Blocked until the dashboard or IPC layer supports interactive prompts.

---

### No Output Streaming

Execution is batch-oriented. stdout and stderr are captured after each sheet completes, not streamed in real time.

**What this means:**

- You cannot watch AI output as it generates
- `stdout_tail` in the state file is truncated to the last 500 characters (for log display) or ~10 KB (for self-healing diagnostic context)
- Long-running sheets provide no progress indication until completion

**Why:** The `asyncio.create_subprocess_exec` backend collects output via `communicate()`, which buffers until process exit. Streaming would require line-by-line reads with significant complexity for timeout handling.

**Relevant constants** (from `src/marianne/core/constants.py`):

| Constant | Value | Purpose |
|----------|-------|---------|
| `TRUNCATE_STDOUT_TAIL_CHARS` | 500 | Log display truncation |
| `HEALING_CONTEXT_TAIL_CHARS` | 10,000 | Self-healing diagnostic context |

**Workaround:** For long jobs, use `mzt status <job> --watch` to poll completion state, or `tail -f workspace/marianne.log` for log-level updates.

**Status:** Permanent for the current architecture.

---

## Daemon Internals

### Legacy Runner Still Available

The baton (`src/marianne/daemon/baton/`) is now the default execution engine (`use_baton: true`), providing event-driven per-sheet dispatch, per-instrument concurrency, timer-based retry, rate limit auto-resume, and restart recovery. The legacy monolithic runner remains available as a fallback.

**What this means:** Jobs run via the baton by default. Set `use_baton: false` in `~/.marianne/conductor.yaml` to fall back to the legacy runner if needed.

**Impact of legacy fallback:** The legacy runner ignores per-sheet instrument assignments — it uses a single backend regardless of per-sheet configuration. Rate limit handling is also less sophisticated.

**Status:** Baton is default (Phase 2 complete). Phase 3 (remove the toggle) will delete the legacy runner entirely. A three-phase transition plan is documented in the [Daemon Guide](daemon-guide.md#transition-plan).

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

Marianne supports multiple AI instruments through a config-driven plugin system. Six instruments ship as built-in profiles, and users can add custom instruments via YAML files.

**Built-in instruments:** `claude-code`, `gemini-cli`, `codex-cli`, `cline-cli`, `aider`, `goose`

**Native backends:** `claude_cli` (ClaudeCliBackend), `anthropic_api`, `ollama`, `recursive_light`

Run `mzt instruments list` to see all available instruments and their status.

### Error Classification Is Claude-Tuned

The error classifier (`src/marianne/core/errors/classifier.py`) was originally designed around Claude CLI output patterns. While it handles common rate-limit patterns across providers, edge cases in non-Claude instruments may produce suboptimal error recovery.

**What this means:**

- Default rate-limit patterns (e.g., `hit.*limit`, `limit.*resets?`, `daily.*limit`) were derived from Claude CLI output
- The `PluginCliBackend` uses per-instrument error patterns from YAML profiles (e.g., `gemini-cli.yaml` defines its own `rate_limit_patterns` and `auth_error_patterns`)
- Instrument profiles can declare `crash_patterns`, `stale_patterns`, `timeout_patterns`, and `capacity_patterns` for fine-grained classification

**Workaround:** Define instrument-specific error patterns in the instrument profile YAML. The `PluginCliBackend` uses these patterns instead of the global defaults.

**Status:** Improving. Each instrument profile is verified against the actual CLI tool's output.

### MCP Servers Load Ambient by Default

When `disable_mcp` is not set (or set to `false`) on a Claude CLI instrument, the backend does not pass `--strict-mcp-config`. This means Claude Code loads **all** MCP servers from the user's plugin configuration — potentially dozens of ambient servers that the score doesn't need.

**What this means:**

- Each sheet execution may spawn MCP server processes that consume resources
- Ambient MCP servers may have side effects the score author doesn't intend
- Without `--strict-mcp-config`, there is no control over which tools are available to the agent

**Workaround:** Set `disable_mcp: true` in your score's `instrument_config:` unless your score specifically needs MCP tools. This passes `--strict-mcp-config` with an empty config, preventing ambient server loading.

```yaml
instrument: claude-code
instrument_config:
  disable_mcp: true    # Prevents loading ambient MCP servers
```

**Status:** The `mzt validate` command warns about this (V209). A proper technique system for per-sheet MCP/skill configuration is planned but not yet implemented.

---

## Architecture Complexity

### Runner Mixin Architecture

The `JobRunner` class is composed of 7 mixins plus a base class (8 classes total) via multiple inheritance:

```
JobRunner(
    SheetExecutionMixin,  # Sheet execution (~3,400 lines)
    LifecycleMixin,       # run() orchestration
    RecoveryMixin,        # Error classification and retry
    PatternsMixin,        # Pattern management
    CostMixin,            # Cost tracking
    IsolationMixin,       # Worktree isolation
    JobRunnerBase,        # Core initialization
)
```

**What this means:**

- Debugging requires tracing method calls across multiple files in `src/marianne/execution/runner/`
- MRO (Method Resolution Order) determines which mixin's method wins
- State is shared across mixins via `self`, with no encapsulation between them
- `SheetExecutionMixin` alone (`sheet.py`) is ~3,400 lines

**Why:** The runner grew organically. Each concern (cost, isolation, recovery) was added as a mixin to avoid a single 5,000+ line file.

**Workaround:** When debugging, start from `base.py` (initialization) and `lifecycle.py` (`run()` entry point), then trace into specific mixins.

**Status:** Working but complex. The baton execution engine (`src/marianne/daemon/baton/`) replaces this architecture with a cleaner event-driven model where musicians execute once and the conductor handles all retry/recovery decisions. The baton is built and tested but not yet the default path.

---

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

Validation `condition` fields support comparison expressions with any context variable and boolean AND:

```yaml
validations:
  - type: file_exists
    path: "output.md"
    condition: "sheet_num >= 3"                        # Simple comparison
    condition: "stage == 2 and instance == 1"          # Boolean AND with fan-out variables
    condition: "sheet_num >= 3 and sheet_num <= 5"     # Range check
```

**Supported operators:** `>=`, `<=`, `==`, `!=`, `>`, `<`

**Supported variables:** Any variable from the sheet context — `sheet_num`, `stage`, `instance`, `fan_count`, `total_stages`, and any user-defined `prompt.variables`.

**What's NOT supported:**

- Complex expressions: `sheet_num in [1, 3, 5]`
- Boolean OR: `sheet_num == 3 or sheet_num == 5`
- Nested expressions: `(sheet_num > 2) and (stage < 3)`

**Why:** The condition evaluator in `engine.py` splits on `" and "` and evaluates each clause as a `variable operator value` triple. Unrecognized conditions silently fall back to "always apply" behavior.

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
