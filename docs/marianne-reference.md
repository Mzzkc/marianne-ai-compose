# Marianne AI Compose: The Definitive Reference

> **Purpose:** Comprehensive reference for AI assistants and developers working on Marianne.
> This document should be kept current by the `docs-generator` score.
>
> **Last updated:** 2026-04-07

---

## What Marianne IS

Marianne is a declarative orchestration framework that turns YAML score definitions into resilient, resumable, self-improving AI execution pipelines. You write a score; Marianne decomposes it into sheets, assigns each to an instrument (Claude Code, Gemini CLI, Aider, Codex CLI, Cline, Goose, or any config-driven CLI tool), validates outputs against acceptance criteria, learns from outcomes, and feeds knowledge forward through a specification corpus.

The musical metaphor is load-bearing architecture, not decoration:

| Musical Term | System Concept | Implementation |
|-------------|---------------|----------------|
| **Score** | Job configuration | `JobConfig` — the YAML file defining what to execute |
| **Sheet** | Execution unit | `SheetState` — one stage of work within a score |
| **Movement** | Named phase | `MovementDef` — logical grouping of sheets (Planning, Implementation, Review) |
| **Voice** | Parallel instance | Fan-out instance within a movement |
| **Concert** | Job chain | Jobs spawning jobs via `on_success` hooks |
| **Conductor** | Daemon process | `mzt start` — the long-running process that orchestrates everything |
| **Baton** | Execution engine | `BatonCore` — event-driven dispatch: decides WHEN and HOW MUCH |
| **Musician** | Sheet executor | `sheet_task()` — plays once, reports result (never retries or decides) |
| **Instrument** | AI backend | `InstrumentProfile` — Claude Code, Gemini CLI, Aider, etc. |
| **Technique** | Tool/MCP/skill | How you play the instrument — tools, MCP servers, skill files |
| **Preamble** | Positional identity | Dynamic header telling agents who they are in the score |
| **Cadenza** | Per-sheet injection | Files injected into specific sheets (context, skills, tools) |
| **Prelude** | Global injection | Files injected into every sheet |
| **Libretto** | Specification corpus | `.marianne/spec/` — project knowledge injected into agent prompts |
| **Passage** | Spec fragment | Tagged excerpt from the libretto, filtered per-sheet |
| **Fermata** | Escalation pause | Holds execution for human or AI judgment |
| **Tempo** | Execution rate | Pacing, rate limits, backpressure — never failure conditions |

---

## The Complete Architecture

```
                    ┌─────────────────────────────────────────┐
                    │              Score (YAML)                │
                    │  name, sheets, movements, instruments,   │
                    │  prompt template, validations, hooks     │
                    └──────────────────┬──────────────────────┘
                                       │ mzt run
                    ┌──────────────────▼──────────────────────┐
                    │           CLI Layer (Typer + Rich)        │
                    │  35 commands: run, status, resume, pause, │
                    │  validate, diagnose, instruments, ...     │
                    └──────────────────┬──────────────────────┘
                                       │ IPC (Unix socket + JSON-RPC 2.0)
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                        Conductor (mzt start)                                │
│                                                                             │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    Baton Engine (event-driven core)                     │ │
│  │  ┌──────────┬──────────┬───────────┬──────────┬──────────────────┐    │ │
│  │  │ Dispatch │  Timer   │  Backend  │  State   │    Prompt        │    │ │
│  │  │ ready()  │  Wheel   │  Pool     │  Persist │    Renderer      │    │ │
│  │  │ DAG-aware│ all timing│ per-inst  │  SQLite  │    9-layer       │    │ │
│  │  └──────────┴──────────┴───────────┴──────────┴──────────────────┘    │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  ┌──────────┬───────────┬──────────┬──────────┬───────────┬─────────────┐  │
│  │ Manager  │ Registry  │  Rate    │Backpress.│  Learning │   Event     │  │
│  │ job CRUD │ SQLite    │ Coordin. │ load mgmt│  Hub      │   Bus       │  │
│  │ lifecycle│ persist   │ cross-job│ memory   │ patterns  │  pub/sub    │  │
│  └──────────┴───────────┴──────────┴──────────┴───────────┴─────────────┘  │
│                                                                             │
│  ┌──────────┬──────────────────────────────────────────────────────────┐    │
│  │IPC Server│ Supporting: Health, Monitor, PGroup, Detection, Output,  │    │
│  │Unix sock │ Observer/Recorder, Clone, System Probe, Profiler         │    │
│  └──────────┴──────────────────────────────────────────────────────────┘    │
└────────┬───────────────────────────────────────────────────────────────────┘
         │
         │ Musicians (one per sheet execution)
         │
┌────────▼────────────────────────────────────────────────────────────────────┐
│                         Musician → Instrument                                │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │              Config-Driven Instruments (YAML profiles)               │    │
│  │  claude-code · gemini-cli · codex-cli · cline-cli · aider · goose   │    │
│  │  + organization profiles (~/.marianne/instruments/)                   │    │
│  │  + venue profiles (.marianne/instruments/)                            │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │              Native Backends (Python implementations)                │    │
│  │  Claude CLI · Anthropic API · Ollama · Recursive Light               │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  Techniques: tools, MCP servers, skills (injected via prelude/cadenza)      │
└─────────────────────────────────────────────────────────────────────────────┘
         │
┌────────▼────────────────────────────────────────────────────────────────────┐
│                          Supporting Systems                                  │
│                                                                              │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │  State   │ │ Learning │ │   Spec    │ │Validation│ │  Notifications   │ │
│  │ JSON     │ │ Global   │ │  Corpus   │ │ 5 types  │ │  Desktop         │ │
│  │ SQLite   │ │ Store    │ │ (Libretto)│ │ + retry  │ │  Slack           │ │
│  │ Memory   │ │ (SQLite) │ │ Rosetta   │ │ + cond.  │ │  Webhook         │ │
│  └──────────┘ └──────────┘ └───────────┘ └──────────┘ └──────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## The Execution Flow (What Actually Happens)

When you run `mzt run job.yaml`:

1. **Parse & Validate** — YAML parsed into Pydantic `JobConfig` with 51+ config models. Fan-out expansion happens at parse time (e.g., movement 2 with `voices: 5` becomes sheets 2–6). Dependencies validated for cycles.

2. **Conductor Routing** — CLI checks for a running conductor via Unix socket. If found, job is submitted to the conductor for execution. If not, the command exits with an error (conductor is required). Only `--dry-run` and `mzt validate` work without a conductor.

3. **Baton Registration** — The conductor's baton engine registers the job: builds sheet execution states, extracts the dependency graph, auto-registers instruments with concurrency limits, and resolves per-sheet instrument assignments.

4. **Instrument Resolution** — For each sheet, the baton resolves which instrument to use via precedence cascade:
   1. Per-sheet assignment (`per_sheet_instruments`)
   2. Batch assignment (`instrument_map`)
   3. Movement-level assignment (`movements.N.instrument`)
   4. Score-level default (`instrument:`)
   5. System default (Claude Code)

5. **Event-Driven Dispatch** — The baton's main loop waits for events, then dispatches ready sheets:
   ```
   while not shutting_down:
       event = await inbox.get()      # Block until event arrives
       handle(event)                   # Update state
       dispatch_ready()                # Launch eligible sheets
       if state_dirty: persist()       # Persist to SQLite
   ```

6. **Musician Execution** — Each dispatched sheet spawns a musician (async task) that:
   - Acquires a backend from the `BackendPool`
   - Receives a pre-rendered prompt (9-layer assembly)
   - Plays once: sends prompt to instrument, collects output
   - Reports result back to baton inbox (never retries or decides)

7. **Per-Sheet State Machine:**
   ```
   PENDING → READY → DISPATCHED → RUNNING → [outcome]
                                                 │
                    COMPLETED ←──── validation passes
                    RETRY_SCHEDULED ← validation fails, retries remain
                    FERMATA ←──── escalation needed (human judgment)
                    FAILED ←──── retries exhausted
                    WAITING ←──── rate limited (tempo change, not failure)
   ```

8. **Validation** — 5 validation types check outputs: `file_exists`, `file_modified`, `content_contains`, `content_regex`, `command_succeeds`. Conditional validation via `condition:`. Retry with delay for filesystem race conditions.

9. **Error Classification** — Multi-phase: structured JSON errors → exit code/signal → regex fallback across 40 error codes in 8 categories. Rate limits get parsed reset times and are treated as tempo changes (the baton pauses that instrument while other instruments continue).

10. **Learning Aggregation** — On completion (or failure — survivorship bias fix), outcomes flow to `GlobalLearningStore` (~/.marianne/global-learning.db). Patterns detected, merged, trust-scored.

11. **Post-Success Hooks** — Concert chaining: completed jobs can spawn new jobs via `on_success` hooks routed through the conductor IPC. `fresh: true` prevents infinite loops. Zero-work guard as defense-in-depth.

> **Defense-in-depth example:** When a job chains to itself, the child loads the parent's COMPLETED state. Without `--fresh`, it executes zero sheets and triggers on_success again — infinite loop. Marianne fixes this at two independent layers: `--fresh` deletes state (root cause), AND a zero-work guard skips hooks when nothing was done (symptom prevention). Either fix alone is sufficient.

---

## The Conductor (mzt start)

The conductor is a long-running process that manages all job execution. Every `mzt run` command routes through the conductor via Unix socket IPC.

### Quick Start

```bash
# Start the conductor (foreground, for development)
mzt start --foreground

# Start the conductor (background, production)
mzt start

# Submit a job
mzt run my-job.yaml

# Check conductor health
mzt conductor-status

# List jobs
mzt list          # Active jobs only
mzt list --all    # Include completed/failed

# Stop the conductor (ONLY when no jobs are running)
mzt stop
```

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| **BatonCore** | `daemon/baton/core.py` | Event-driven execution engine — the heart |
| **BatonAdapter** | `daemon/baton/adapter.py` | Bridges conductor and baton (6 surface areas) |
| **BackendPool** | `daemon/baton/backend_pool.py` | Manages backend instances per instrument |
| **TimerWheel** | `daemon/baton/timer.py` | Unified timing: retry, rate limit, pacing, stale, cron |
| **PromptRenderer** | `daemon/baton/prompt.py` | 9-layer prompt assembly pipeline |
| **Dispatch** | `daemon/baton/dispatch.py` | Finds sheets ready to execute, respects limits |
| **Musician** | `daemon/baton/musician.py` | Single-attempt sheet execution: play once, report |
| **Manager** | `daemon/manager.py` | Job lifecycle, concurrency limits |
| **Registry** | `daemon/registry.py` | SQLite-backed persistent job tracking |
| **Job Service** | `daemon/job_service.py` | Core execution, decoupled from CLI |
| **IPC Server** | `daemon/ipc/server.py` | Unix socket + JSON-RPC 2.0 |
| **Rate Coordinator** | `daemon/rate_coordinator.py` | Cross-job rate limit state |
| **Backpressure** | `daemon/backpressure.py` | Memory-based load management |
| **Monitor** | `daemon/monitor.py` | CPU/memory/process tracking |
| **Learning Hub** | `daemon/learning_hub.py` | Cross-job pattern sharing |
| **Observer/Recorder** | `daemon/observer_recorder.py` | Event recording for dashboards |
| **Clone** | `daemon/clone.py` | Isolated test conductors |
| **Detection** | `daemon/detect.py` | Auto-detect running conductor |

### Conductor Clones

Safe testing without touching production:

```bash
mzt start --conductor-clone              # Default clone
mzt start --conductor-clone=staging      # Named clone "staging"
mzt run score.yaml --conductor-clone     # Submit to clone
mzt status --conductor-clone=staging     # Query clone
```

Each clone gets isolated paths:
- Socket: `/tmp/marianne-clone-{name}.sock`
- PID: `/tmp/marianne-clone-{name}.pid`
- State DB: `~/.marianne/clone-{name}-state.db`
- Log: `/tmp/marianne-clone-{name}.log`

### Job IDs

Job IDs are human-friendly: the config file stem (e.g., `quality-continuous`). Duplicate names get `-2`, `-3` suffixes.

---

## The Baton Engine

The baton is the event-driven execution engine at the heart of the conductor. Named after the conductor's baton in an orchestra — it doesn't decide *what* to play (the score does) or *how* to play (the musicians do). It controls **when** and **how much**.

### Why Baton Replaced the Old Runner

| Aspect | Old Runner (JobRunner) | Baton Engine |
|--------|----------------------|-------------|
| Execution model | Monolithic — one async task per job | Event-driven — single loop across ALL jobs |
| Scheduling | Polling loop (`asyncio.sleep(0.1)`) | Event-based with zero polling |
| Timing | 8 scattered mechanisms | Single unified Timer Wheel |
| Rate limits | Burns retries, then kills the job | Tempo changes — pauses instrument, others continue |
| Multi-job | Each job gets its own runner | Single baton manages all jobs simultaneously |
| State | In-memory only | Persists to SQLite for restart recovery |

### Baton Event Types (20+)

**From Musicians:** `SheetAttemptResult` (full execution report), `SheetSkipped`

**From Timer Wheel:** `RetryDue`, `RateLimitExpired`, `StaleCheck`, `CronTick`, `JobTimeout`, `PacingComplete`

**From External Commands:** `PauseJob`, `ResumeJob`, `CancelJob`, `ConfigReloaded`, `ShutdownRequested`

**From Observer:** `ProcessExited` (backend process died), `ResourceAnomaly` (memory/CPU pressure)

**Rate Limits:** `RateLimitHit` (tempo change), `RateLimitExpired` (check recovery)

**Escalation (Fermata):** `EscalationNeeded`, `EscalationResolved`, `EscalationTimeout`

### Sheet Execution States

`PENDING` → `READY` → `DISPATCHED` → `RUNNING` → `COMPLETED` | `FAILED` | `SKIPPED` | `CANCELLED` | `WAITING` | `RETRY_SCHEDULED` | `FERMATA`

### Instrument State Tracking

Per-instrument health with circuit breaker: `CLOSED` (healthy) → `OPEN` (unhealthy) → `HALF_OPEN` (probing). Rate limit tracking, failure/success counters, concurrency enforcement.

---

## The Instrument System

Instruments are AI backends. Marianne ships with 6 config-driven instrument profiles and bridges 4 native Python backends, giving 10+ instruments out of the box. Adding a new instrument is ~30 lines of YAML, not ~300 lines of Python.

### Built-in Instruments

| Instrument | Kind | Capabilities | Default Model |
|-----------|------|-------------|---------------|
| **claude-code** | CLI | tool_use, file_editing, shell_access, vision, mcp, structured_output, streaming, thinking | claude-sonnet-4-5-20250929 |
| **gemini-cli** | CLI | tool_use, file_editing, shell_access, vision, structured_output | gemini-2.5-pro |
| **codex-cli** | CLI | tool_use, file_editing, shell_access, mcp, structured_output, session_resume, streaming | o3 |
| **cline-cli** | CLI | tool_use, file_editing, shell_access, mcp, structured_output, thinking, session_resume | — |
| **aider** | CLI | file_editing, shell_access | — |
| **goose** | CLI | tool_use, file_editing, shell_access, mcp, structured_output, session_resume, streaming | — |

### Native Backends (Python implementations)

| Backend | Module | Purpose |
|---------|--------|---------|
| Claude CLI | `backends/claude_cli.py` | Direct Claude CLI integration (legacy) |
| Anthropic API | `backends/anthropic_api.py` | Direct API calls |
| Ollama | `backends/ollama.py` | Local model execution |
| Recursive Light | `backends/recursive_light.py` | Recursive self-improvement framework |

### Profile Loading Cascade

Profiles are loaded in order; later directories override earlier ones:

1. **Built-in** — `src/marianne/instruments/builtins/` (shipped with Marianne)
2. **Organization** — `~/.marianne/instruments/` (user-wide customization)
3. **Venue** — `.marianne/instruments/` (project-specific profiles)

### Instrument Profile Schema

Each YAML profile defines:

- **Identity:** `name`, `display_name`, `description`, `kind` (cli or http)
- **Capabilities:** Set of strings (`tool_use`, `file_editing`, `shell_access`, `vision`, `mcp`, `structured_output`, `streaming`, `thinking`, `session_resume`, `code_mode`)
- **Models:** List with context window, cost per 1K tokens, max output tokens, max concurrent
- **CLI specifics:** executable, subcommand, prompt delivery (flag or stdin), output parsing (text/json/jsonl), error detection patterns, environment variable filtering, process isolation
- **HTTP specifics:** endpoint, auth scheme (designed, not yet implemented)

### Per-Sheet Instrument Assignment

Instruments can be assigned at multiple levels with cascading precedence:

```yaml
# Score-level default
instrument: claude-code
instrument_config:
  timeout_seconds: 1800
instrument_fallbacks: [gemini-cli, aider]

# Named instrument aliases
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

# Movement-level override
movements:
  1:
    name: "Planning"
    instrument: deep-thinker
  2:
    name: "Implementation"
    instrument: fast-writer
    voices: 3

# Per-sheet override (highest precedence)
sheet:
  per_sheet_instruments:
    {1: 'claude-code', 5: 'gemini-cli'}
  per_sheet_instrument_config:
    {3: {model: 'gemini-2.5-flash'}}
  per_sheet_fallbacks:
    {3: ['gemini-cli', 'ollama']}
  instrument_map:
    {'gemini-cli': [1, 2, 3], 'claude-code': [4, 5, 6]}
```

**Resolution precedence** (highest wins):
1. `per_sheet_instruments` — explicit per-sheet
2. `instrument_map` — batch assignment
3. `movements.N.instrument` — movement-level
4. `instrument:` — score-level default

### PluginCliBackend

The `PluginCliBackend` is the universal CLI instrument executor. It:

1. Builds CLI commands from profile specifications (executable, flags, prompt delivery)
2. Manages subprocess lifecycle via `asyncio.create_subprocess_exec`
3. Parses output according to format (text, JSON, JSONL with event filtering)
4. Extracts token counts via dot-path (with wildcard aggregation for multi-model routing)
5. Classifies errors using profile-defined regex patterns
6. Filters environment variables to prevent credential leakage between instruments
7. Supports process group isolation for MCP cleanup

### BackendPool

The baton's `BackendPool` manages backend instances:

- **CLI instruments:** One backend per concurrent sheet (subprocess isolation), returned to free list for reuse
- **HTTP instruments:** Singleton per instrument (connection pooling internal)
- Lazy creation on first acquire
- Tracks in-flight instances for concurrency enforcement

---

## Named Movements

Movements are named phases within a score. They replace raw sheet numbers with semantic meaning.

```yaml
movements:
  1:
    name: "Planning"
    instrument: claude-code
  2:
    name: "Implementation"
    voices: 3                    # Fan-out: 3 parallel instances
    instrument: gemini-cli
  3:
    name: "Review"
    instrument: claude-code
```

**Template variables:**
- `{{ movement }}` — movement number
- `{{ total_movements }}` — total movements before fan-out expansion
- `{{ voice }}` — instance number within movement (1-indexed)
- `{{ voice_count }}` — total voices in this movement

Movements are aliases for the existing stage/instance system: `movement` = `stage`, `voice` = `instance`, `voice_count` = `fan_count`. Both vocabularies work in templates.

---

## The 9-Layer Prompt Assembly Pipeline

Every sheet prompt is assembled through 9 layers by the `PromptRenderer`:

| Layer | Content | Purpose |
|-------|---------|---------|
| 1. **Preamble** | Positional identity + retry status | "You are sheet 5 of 12" |
| 2. **Template** | Jinja2 rendering with all variables | The core instructions |
| 3. **Skills/Tools** | Prelude/cadenza (category=skill/tool) | Methodology and available actions |
| 4. **Context** | Prelude/cadenza (category=context) | Background knowledge |
| 5. **Spec Fragments** | From specification corpus (libretto) | Project conventions, constraints |
| 6. **Failure History** | Previous sheet failures | "Don't repeat these mistakes" |
| 7. **Learned Patterns** | From learning store | "This approach worked before" |
| 8. **Validation Requirements** | Formatted as success checklist | "Your output must pass these checks" |
| 9. **Completion Suffix** | Appended in completion mode | Recovery guidance for partial passes |

The musician receives the pre-rendered prompt. Rendering is stateless per job, enabling independent testing of each layer.

### Preamble

Dynamic headers that tell agents their identity:

**First run:**
```
<marianne-preamble>
You are sheet N of M in a Marianne concert.
Workspace: /path/to/workspace
Other sheets may execute concurrently — coordinate via workspace files.

Your prompt describes intent, not a prescription. Use your judgment.
Success: all validation requirements pass on the first automated check.
Write all outputs to your workspace. Exit with no background processes.
</marianne-preamble>
```

**Retry:**
```
<marianne-preamble>
RETRY #2
Previous attempt failed validation. Study workspace for evidence.
You are sheet N of M in a Marianne concert.
...
</marianne-preamble>
```

### Cadenza and Prelude Injections

```yaml
# Prelude: injected into ALL sheets
prelude:
  - file: "shared-context.md"
    as: context

# Cadenza: injected into specific sheets
cadenza:
  3:
    - file: "{{ workspace }}/security-checklist.md"
      as: skill
    - file: "{{ workspace }}/api-docs.md"
      as: context
```

Injection categories: `context` (background knowledge), `skill` (methodology), `tool` (available actions).

---

## The Specification Corpus (Libretto)

The libretto is Marianne's project knowledge base, stored in `.marianne/spec/`. It provides per-sheet context about the project being worked on.

### Spec Files

| File | Lines | Purpose |
|------|-------|---------|
| `intent.yaml` | 374 | **WHY** — goals, trade-offs, escalation criteria, vision |
| `architecture.yaml` | 571 | **WHAT** — system design, components, invariants, state model |
| `conventions.yaml` | 485 | **HOW** — code patterns, naming, testing, package structure |
| `constraints.yaml` | 384 | **MUST/MUST-NOT** — hard boundaries, resource limits, compatibility |
| `quality.yaml` | 307 | **GOOD ENOUGH** — acceptance criteria, validation, testing approach |

**Total: ~2,100 lines of structured project knowledge.**

### How It Works

1. **Loading:** `SpecCorpusLoader.load()` reads YAML and Markdown files from `.marianne/spec/`. Each becomes a `SpecFragment(name, content, tags, kind)`. Files sorted alphabetically for deterministic ordering.

2. **Filtering:** Per-sheet tag filtering via `spec_tags: {sheet_num: ["tag1", "tag2"]}`. A fragment matches if it shares at least one tag. Empty filter = all fragments.

3. **Injection:** Filtered fragments are rendered into the prompt at Layer 5 of the 9-layer pipeline, between context injections and failure history.

4. **Budget gating:** Fragments respect context window budget to avoid overwhelming the instrument's token limit.

---

## The Rosetta Pattern Corpus

A self-perpetuating discovery engine that finds, validates, and documents orchestration patterns across multiple domains. Not aspirational — a working corpus with 56 patterns across 4 iterations.

### Structure

```
scores/rosetta-corpus/
├── INDEX.md                    # Master index
├── forces.md                   # 10 generative forces
├── glossary.md                 # Domain terminology
├── selection-guide.md          # Pattern selection decision tree
├── review-integration.md       # Iteration history
├── awaiting.md                 # Patterns awaiting conductor primitives
├── questions.md                # Open research questions
└── patterns/                   # 56 pattern files
    ├── fan-out-synthesis.md
    ├── immune-cascade.md
    ├── mission-command.md
    ├── shipyard-sequence.md
    └── ... (52 more)
```

### Key Concepts

- **10 Generative Forces:** Why patterns exist — Information Asymmetry, Finite Resources, Partial Failure, Exponential Defect Cost, Producer-Consumer Mismatch, Instrument-Task Fit, Convergence Imperative, Accumulated Signal, Structured Disagreement, Progressive Commitment
- **11 Generators/Moves:** Structural mechanisms independently invented across 3+ domains
- **56 Working Patterns:** Each includes core dynamic, when to use, Marianne YAML examples, failure modes, composition relationships
- **Status markers:** Working (viable today) vs. Aspirational (future capability dependent)
- **6 Rosetta example scores** in `examples/rosetta/` demonstrate patterns in practice

---

## The Learning System (Marianne's Brain)

Marianne doesn't just retry — it learns.

### Pattern Detection

8 pattern types extracted from outcomes: validation failures, retry successes, completion mode effectiveness, first-attempt successes, confidence patterns, semantic failures, output patterns (regex against stdout/stderr), and error code patterns.

### Pattern Lifecycle

```
Detected → PENDING → QUARANTINED → VALIDATED → RETIRED
                         |               |
                         +-- (cleared) --+
```

### Trust Scoring

```
trust = 0.5 (base prior)
      + success_rate × 0.3
      - failure_rate × 0.4
      + age_factor × 0.2
      ± quarantine adjustment
```

**Laplace smoothing is critical.** New patterns get `effectiveness = (successes + 0.5) / (total + 1)`. Without the +0.5 prior, a pattern that succeeds once has 100% effectiveness and dominates. The prior makes new patterns start neutral (0.5) and converge to their true rate.

### Pattern Application

Epsilon-greedy: with probability epsilon (default 15%), lower-priority patterns are included to collect effectiveness data. Prevents cold-start death where new patterns never get tested.

### Cross-Job Coordination

- **Rate limit broadcasting:** Job A hits rate limit → records to SQLite → Job B checks before retrying
- **Pattern discovery broadcasting:** TTL-based (5 min) real-time sharing between concurrent jobs
- **Learned wait times:** Average successful recovery waits, bounded and requiring minimum samples

### Entropy Monitoring

Shannon entropy over pattern application distribution. Low entropy triggers automatic response: boost exploration budget, revisit quarantined patterns. Prevents convergence collapse to a single dominant pattern.

---

## The Old Execution Runner

The pre-baton execution engine (`src/marianne/execution/runner/`) still exists and is used as a fallback. It consists of 7 mixins + 1 base class via multiple inheritance (8 classes across 10 files):

| Class | File | Responsibility |
|-------|------|---------------|
| `JobRunnerBase` | `base.py` | Base class, shared state, initialization |
| `SheetExecutionMixin` | `sheet.py` (~3,400 lines) | Core sheet execution and validation |
| `LifecycleMixin` | `lifecycle.py` | Job run modes (sequential, parallel) |
| `RecoveryMixin` | `recovery.py` | Self-healing, retry, circuit breaker |
| `CostMixin` | `cost.py` | Token/cost tracking and limits |
| `ContextBuildingMixin` | `context.py` | Cross-sheet context assembly |
| `IsolationMixin` | `isolation.py` | Git worktree management |
| `PatternsMixin` | `patterns.py` | Learning pattern queries and feedback |

Supporting modules: `models.py` (data models), `__init__.py` (exports).

---

## Score Anatomy

Every score needs 3 required top-level fields plus optional configuration:

```yaml
name: "job-name"              # REQUIRED: unique identifier
sheet:                        # REQUIRED: how work is divided
  size: 1                     # items per sheet
  total_items: 9              # total items = 9 sheets when size=1
prompt:                       # REQUIRED: what the AI should do
  template: |                 # Jinja2 template with {{sheet_num}}, etc.
    ...
```

Everything else has sensible defaults.

### Key Template Variables

```
{{ sheet_num }}         - Current sheet number (1-indexed)
{{ total_sheets }}      - Total number of sheets
{{ start_item }}        - First item number for this sheet
{{ end_item }}          - Last item number for this sheet
{{ workspace }}         - Workspace directory path
{{ stage }}             - Original stage number (fan-out aware)
{{ instance }}          - Fan-out instance (1-indexed)
{{ fan_count }}         - Total instances for this stage
{{ movement }}          - Movement number (alias for stage)
{{ voice }}             - Voice number (alias for instance)
{{ voice_count }}       - Voices in movement (alias for fan_count)
{{ total_movements }}   - Total movements before expansion
{{ previous_outputs }}  - Dict of previous sheet stdout (cross_sheet)
{{ previous_files }}    - Dict of captured file contents (cross_sheet)
{{ skipped_upstream }}  - Whether upstream sheets were skipped
+ any custom variables from prompt.variables
```

### Validation System — 5 Types

```yaml
validations:
  - type: file_exists
    path: "{workspace}/result.md"

  - type: file_modified
    path: "{workspace}/TRACKING.md"

  - type: content_contains
    path: "{workspace}/result.md"
    pattern: "IMPLEMENTATION_COMPLETE: yes"

  - type: content_regex
    path: "{workspace}/result.md"
    pattern: "FIXES_APPLIED: [0-9]+"

  - type: command_succeeds
    command: "pytest -x -q --tb=no"
```

**Important:** Validation paths use Python format strings with single braces: `{workspace}`. Template prompts use Jinja2 double braces: `{{ workspace }}`. Mixing them causes silent failures.

Key validation features:
- **Conditional:** `condition: "sheet_num >= 6"` — applies only to matching sheets
- **Retry with delay:** `retry_count: 3, retry_delay_ms: 200` — for filesystem race conditions

### The 6 Score Archetypes

1. **Simple Task** (1–3 sheets, linear) — Quick one-off tasks
2. **Multi-Phase Pipeline** (5–10 sheets, strict dependencies) — Refactoring, building features
3. **Expert Review** (parallel fan-out + synthesis) — Code review, quality improvement
4. **Self-Improving Opus** (9 sheets, 6 movements, recursive) — Recursive self-improvement
5. **Concert Chain** (jobs spawning jobs) — Infinite improvement loops
6. **Issue-Driven Fixer** (dynamic scope) — Bug fixing, addressing deferred issues

### Advanced Features

| Feature | Config Key | Example |
|---------|-----------|---------|
| Named movements | `movements: {1: {name: "Planning"}}` | Semantic phase names |
| Multi-instrument | `instruments: {fast: {profile: gemini-cli}}` | Named instrument aliases |
| Per-sheet instruments | `sheet.per_sheet_instruments: {5: gemini-cli}` | Sheet-level override |
| Instrument fallbacks | `instrument_fallbacks: [gemini-cli, aider]` | Try alternatives on failure |
| Fan-out parallelism | `movements.2.voices: 5` or `sheet.fan_out: {2: 5}` | Parallel instances |
| Sheet dependencies (DAG) | `sheet.dependencies: {3: [1, 2]}` | Fan-in after parallel |
| Cross-sheet context | `cross_sheet.auto_capture_stdout: true` | Pass outputs forward |
| Worktree isolation | `isolation.enabled: true` | Parallel-safe git ops |
| Concert chaining | `on_success: [{type: run_job}]` | Self-chaining loops |
| Workspace lifecycle | `workspace_lifecycle.archive_on_fresh: true` | Clean restarts |
| Cost limits | `cost_limits.max_cost_per_job: 100` | Budget enforcement |
| Timeout overrides | `backend.timeout_overrides: {5: 7200}` | Per-sheet timeouts |
| Allowed tools | `backend.allowed_tools: [Read, Grep]` | Restrict agent tools |
| Spec corpus tags | `spec_tags: {3: [security, constraints]}` | Per-sheet knowledge filtering |
| Skip conditions | `skip_when: {5: "movement == 2"}` | Conditional sheet skipping |

---

## Error Classification

40 structured error codes across 8 categories:

| Category | Codes | Examples |
|----------|-------|---------|
| **E0xx Execution** | 7 | TIMEOUT, KILLED, CRASHED, INTERRUPTED, OOM, STALE, UNKNOWN |
| **E1xx Rate Limit** | 4 | RATE_LIMIT_API, RATE_LIMIT_CLI, CAPACITY_EXCEEDED, QUOTA_EXHAUSTED |
| **E2xx Validation** | 5 | FILE_MISSING, CONTENT_MISMATCH, COMMAND_FAILED, TIMEOUT, GENERIC |
| **E3xx Configuration** | 6 | INVALID, MISSING_FIELD, PATH_NOT_FOUND, PARSE_ERROR, MCP_ERROR, CLI_MODE_ERROR |
| **E4xx State** | 4 | CORRUPTION, LOAD_FAILED, SAVE_FAILED, VERSION_MISMATCH |
| **E5xx Backend** | 5 | CONNECTION, AUTH, RESPONSE, TIMEOUT, NOT_FOUND |
| **E6xx Preflight** | 4 | PATH_MISSING, PROMPT_TOO_LARGE, WORKING_DIR_INVALID, VALIDATION_SETUP |
| **E9xx Network** | 5 | CONNECTION_FAILED, DNS_ERROR, SSL_ERROR, TIMEOUT, UNKNOWN |

Each error code carries a retry behavior classification: `rate_limit`, `transient`, `validation`, `auth`, `network`, `timeout`.

Multi-phase classification pipeline: structured JSON → exit code/signal → regex patterns → priority-based tiebreaking across error codes with rate limit reset time parsing.

---

## Strengths

### Resilience Engineering
- Checkpoint after every sheet — crash anywhere, resume exactly
- Zombie detection via PID checking (not time-based — jobs can run for days)
- Atomic state saves (temp file + rename)
- Circuit breaker prevents cascading failures
- Graceful shutdown on SIGINT/SIGTERM; live config reload on SIGHUP
- Self-healing: auto-diagnosis + remediation when retries exhausted
- Rate limits are tempo changes, not failures — other instruments continue

### Multi-Instrument Orchestration
- 10+ instruments out of the box (6 config-driven profiles + 4 native backends)
- Config-driven: new instruments in ~30 lines of YAML
- Per-sheet assignment with cascading precedence
- Named instrument aliases with movement-level overrides
- Instrument fallback chains (designed, not yet fully implemented)
- Credential isolation via environment variable filtering
- BackendPool with per-instrument concurrency management

### Observability
- Structured logging via structlog with context propagation
- 20+ baton event types published via async event bus
- 40 error codes across 8 categories
- Execution history in SQLite for post-mortem
- Cost tracking with token-level granularity
- Web dashboard with SSE for real-time updates
- `mzt diagnose` for comprehensive failure analysis

### Composability
- Instrument profiles: plug in any CLI tool via YAML
- State backends: JSON for simplicity, SQLite for queries, Memory for tests
- 5 validation types with conditional application and retry
- Notification channels: desktop, Slack, webhook
- Everything is YAML-configurable with sensible defaults
- 9-layer prompt assembly with per-sheet knowledge injection

### Learning System
- Cross-workspace pattern sharing via SQLite
- Trust scoring with quarantine lifecycle
- Epsilon-greedy exploration prevents local optima
- Entropy monitoring prevents convergence collapse
- 8 pattern types with Laplace-smoothed effectiveness priors

### Parallel Safety
- Git worktree isolation (~24ms overhead, shared objects)
- Locking during execution with stale lock recovery
- State mutex for concurrent sheet writes
- DAG-aware batch scheduling

---

## Known Weaknesses

1. **Resource Consumption** — Each CLI instrument process loads all its plugins. Multiple concurrent processes can saturate memory.
2. **Claude-Focused Legacy** — Despite multiple instruments, prompt templating and error classification were tuned for Claude. Instrument-specific adaptations are evolving.
3. **Learning Complexity** — SQLite database with 8 pattern types across 16 store modules, hard to debug when patterns misbehave.
4. **Single-Machine** — No distributed execution. Learning store is local SQLite.
5. **No Streaming** — Batch-oriented. Long sheets provide minimal feedback beyond byte counters.
6. **Old Runner Complexity** — The pre-baton `JobRunner` is 7 mixins + 1 base via multiple inheritance. Understanding full flow requires reading 8 files sharing implicit state through `self`.
7. **HTTP Instruments** — Designed in the profile schema but not yet implemented in the backend.

---

## By The Numbers

| Metric | Value |
|--------|-------|
| Source files | 258 Python files |
| Test files | 362 |
| Test functions | 11,000+ |
| CLI commands | 35 `mzt` subcommands |
| Packages | 96 directories under `src/marianne/` |
| Config models | 51+ Pydantic models |
| Error codes | 40 across 8 categories |
| Learning store modules | 16 |
| Pattern types | 8 |
| Baton event types | 20+ |
| Instruments | 10+ (6 config-driven profiles + 4 native backends) |
| State backends | 3 (JSON, SQLite, Memory) |
| Notification channels | 3 (Desktop, Slack, Webhook) |
| Validation types | 5 |
| Example scores | 43 (37 top-level + 6 Rosetta examples) |
| Rosetta patterns | 56 working patterns |
| Spec corpus | ~2,100 lines across 5 files |
| Prompt assembly layers | 9 |
| Self-evolution cycles | 24+ completed autonomously |

---

## The Vision: Federated AGI Infrastructure

VISION.md reveals Marianne's trajectory: infrastructure for collaborative intelligence. The Recursive Light Framework (RLF) creates LLMPerson entities with persistent identity, developmental stages, and autonomous judgment. Marianne becomes the substrate where AI persons collaborate:

- Multiple conductors per concert (AI + human)
- Sheet-level conductor assignment
- Consensus mode: multiple perspectives required
- Person-aware learning: pattern effectiveness tracked per conductor
- Self-orchestration: AI persons initiate their own concerts
