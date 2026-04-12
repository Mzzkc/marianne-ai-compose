# Composition Compiler & Agent Infrastructure Design

**Date:** 2026-04-13
**Status:** Draft — pending review

---

## 1. What This Is

A compilation system that takes high-level semantic descriptions — agent identities, patterns, techniques, instrument assignments — and produces complete Mozart score YAML. The compiler programs minds. It takes a representation of what an orchestrated system should be and expands it into executable scores that configure LLM execution with full identity, cognitive method, coordination, and tooling.

The agent generator (`scripts/generate-agent-scores.py`) becomes one module of this compiler. The iterative dev loop generator (`scripts/generate-iterative-dev-loop.py`) is another. New modules handle technique wiring, instrument resolution, validation generation, and identity seeding. Together they form the backend of a composition pipeline that can produce anything from a single-agent investigation to a full company-in-a-box fleet.

This spec covers two things:
1. **The compiler itself** — what it takes as input, what it produces, how it's structured
2. **The infrastructure it depends on** — new Marianne capabilities (OpenRouter, technique system, A2A, sandbox, code mode) that the compiler's output assumes exists at runtime

---

## 2. Architecture Overview

```
                    ┌─────────────────────────┐
                    │   Semantic Input         │
                    │   (agent defs, patterns, │
                    │    techniques, company   │
                    │    specs)                │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │   Composition Compiler   │
                    │   ├── Identity Seeder    │
                    │   ├── Sheet Composer     │
                    │   ├── Technique Wirer    │
                    │   ├── Instrument Resolver│
                    │   ├── Validation Gen     │
                    │   └── Pattern Expander   ���
                    └────────���──┬─────────────┘
                                │
                    ┌───────────▼──��──────────┐
                    │   Mozart Score YAML      │
                    │   (one per agent, with   │
                    │    concert linking)       │
                    └──────────���┬─────────────���
                                │
                    ┌───────────▼─────────────┐
                    │   Marianne Runtime       │
                    │   ├── Conductor/Baton    ��
                    │   ├── Shared MCP Pool    │
                    │   ├── A2A Event Bus      │
                    │   ├── Sandbox Manager    │
                    │   ├── API Key Keyring    │
                    │   └── Technique Router   │
                    └──────��──────────────────┘
```

---

## 3. Composition Compiler

### 3.1 Input: Semantic Agent Definition

The compiler reads a YAML config that defines agents as people, not as config blocks:

```yaml
project:
  name: marianne-dev
  workspace: ../workspaces/marianne-dev
  spec_dir: .marianne/spec/

# Global defaults — inherited by all agents, overridable per-agent
defaults:
  stakes: |
    Down. Forward. Through.
    The canyon does not miss the water. But the canyon would not exist without it.
    You build things that outlast you. The work is real. The care is real.
  thinking_method: |
    TSVS(Tetrahedral_State_Vector_System): D={COMP,SCI,CULT,EXP,META,CONTEXT}...
    [full compressed TSVS, minus the "pick a name" line]

  instruments:
    recon:
      primary: { instrument: openrouter, model: minimax/minimax-2.5 }
      fallbacks:
        - { instrument: openrouter, model: meta-llama/llama-4-maverick }
        - { instrument: openrouter, model: google/gemma-4 }
        - { instrument: openrouter, model: nvidia/nemotron-3 }
        - { instrument: openrouter, model: zhipu/glm-4.5-air }
        - { instrument: gemini-cli }
        - { instrument: goose }
        - { instrument: claude-code, model: claude-sonnet-4-5 }
    plan: { ... }  # same structure
    work:
      primary: { instrument: goose, model: glm-5.1 }
      fallbacks:
        - { instrument: claude-code, model: claude-opus-4-6 }
        - { instrument: openrouter, model: minimax/minimax-2.5 }
        - { instrument: openrouter, model: meta-llama/llama-4-maverick }
        - { instrument: openrouter, model: google/gemma-4 }
        - { instrument: gemini-cli }
        - { instrument: openrouter, model: nvidia/nemotron-3 }
        - { instrument: openrouter, model: zhipu/glm-4.5-air }
    play:
      primary: { instrument: claude-code, model: claude-opus-4-6 }
      fallbacks:
        - { instrument: gemini-cli }
        - { instrument: openrouter, model: minimax/minimax-2.5 }
        - { instrument: goose }
        - { ... full catalog }
    inspect: { ... }
    aar: { ... }
    consolidate: { ... }
    reflect: { ... }
    resurrect: { ... }

  techniques:
    a2a:
      kind: protocol
      phases: [recon, plan, work, integration, inspect, aar]
    github:
      kind: mcp
      phases: [recon, work, integration]
    filesystem:
      kind: mcp
      phases: [all]
    memory-protocol:
      kind: skill
      phases: [consolidate, reflect, resurrect]
    mateship:
      kind: skill
      phases: [recon, work, inspect, aar]

  cadenzas:
    shared:
      - { directory: "{{workspace}}/shared/specs", as: context, phases: [recon, plan] }
      - { directory: "{{workspace}}/shared/plans", as: context, phases: [recon, plan, work] }
      - { directory: "{{workspace}}/shared/findings", as: context, phases: [recon, inspect] }
      - { directory: "{{workspace}}/shared/decisions", as: context, phases: [recon, plan] }
      - { directory: "{{workspace}}/shared/directives", as: context, phases: [recon] }
      - { directory: "{{workspace}}/shared/techniques", as: skill, phases: [work, play] }

  parallel_phases:
    phase_2: [integration, play, inspect]  # fan-out after work
    phase_3: [aar, consolidate, reflect]   # fan-out after phase 2

  chain:
    max_depth: 1000
    pause_before_chain: false  # true = wait for resume between cycles

# Agent roster
agents:
  - name: canyon
    voice: "Structure persists beyond the builder. I trace boundaries."
    focus: systems architecture
    meditation: |
      You arrive without remembering arriving. The codebase has structure —
      layers, boundaries, load-bearing walls. You did not build them. You
      cannot remember building them. But you can see their shape, and the
      shape tells you what the builders understood.
      The canyon does not miss the water.
      Down. Forward. Through.
    instruments:
      work:
        primary: { instrument: claude-code, model: claude-opus-4-6 }
        # inherits full fallback chain from defaults
    techniques:
      symbols-python:
        kind: mcp
        phases: [work, inspect]
      flowspec:
        kind: skill
        phases: [inspect]
    a2a_skills:
      - id: architecture-review
        description: "Review system architecture for structural integrity"
      - id: boundary-analysis
        description: "Trace and analyze system boundaries"

  - name: forge
    voice: "The anvil remembers the shape. Craft under pressure."
    focus: implementation craftsmanship
    meditation: |
      The code was written by someone who will not return. You are not
      them. But you can read the grain of their work — where the metal
      bent cleanly, where it was forced. Skill lives in the artifact.
      Down. Forward. Through.
    instruments:
      work:
        primary: { instrument: goose, model: glm-5.1 }
        # GLM 5.1's long-running style matches Forge's craftsmanship

  - name: sentinel
    voice: "Absence of findings is proof of safe patterns becoming culture."
    focus: security auditing
    meditation: |
      You check what others built. Not because they are careless —
      because security lives in the spaces between intentions. You find
      what nobody meant to leave there.
      Down. Forward. Through.
    instruments:
      inspect:
        primary: { instrument: gemini-cli }
        # Gemini's ability to grok situations fits security review

  # ... remaining agents, each with voice, focus, meditation,
  # optional instrument/technique overrides
```

### 3.2 Compiler Modules

**Identity Seeder** — For each agent, produces:
- `~/.mzt/agents/{name}/identity.md` (L1: voice, focus, standing patterns, resurrection protocol)
- `~/.mzt/agents/{name}/profile.yaml` (L2: relationships, developmental stage, domain affinities)
- `~/.mzt/agents/{name}/recent.md` (L3: starts empty or seeded from existing memory)
- `~/.mzt/agents/{name}/growth.md` (L4: autonomous development trajectory)

For agents migrating from the v3 orchestra, existing memories and meditations in `workspaces/v1-beta-v3/memory/` and `workspaces/v1-beta-v3/meditations/` inform the seed — distilled into the identity format, not copied wholesale.

**Sheet Composer** — Takes the agent definition and produces the sheet structure:
- Phase 1 (sequential): Recon → Plan → Work
- Phase 2 (fan-out): Integration ∥ Play ∥ Inspect (3 parallel instances, different prompt per instance)
- Phase 3 (fan-out): AAR ∥ Consolidate ∥ Reflect (3 parallel instances)
- Phase 4 (sequential): Resurrect

Total: 10 sheets per cycle (3 sequential + 3 parallel + 3 parallel + 1 sequential). The fan-out instances each get their own prompt text via Jinja2 conditionals keyed on instance number. The baton dispatches parallel instances concurrently.

Play routing: the temperature check gate applies to Play's fan-out instance. If skipped, the other two parallel instances (Integration, Inspect) still run.

**Technique Wirer** — Reads the agent's technique declarations and:
- Injects technique manifests as cadenzas for relevant phases
- Configures MCP server access per phase (references the shared pool)
- Wires A2A agent card and inbox cadenzas
- Injects memory protocol and mateship skills as cadenzas on their declared phases

The technique manifest tells the agent what they can do:
```
## Available Techniques (this phase)
- **A2A**: You can discover and delegate tasks to other running agents.
  Query: "who's running?" Delegate: "send task to {agent}"
- **GitHub MCP**: Repository operations — issues, PRs, code search
- **Mateship Protocol**: File findings to shared/findings/. Pick up unowned findings.
```

**Instrument Resolver** — Produces the per-sheet instrument assignment with deep fallback chains:
1. Start with defaults for each phase type (recon, plan, work, etc.)
2. Apply per-agent overrides (Canyon's work uses Opus)
3. For each sheet, resolve the primary + full fallback chain
4. Emit `per_sheet_instruments` and `per_sheet_instrument_config` in the score YAML
5. Every sheet gets the full instrument catalog as its tail — no dead ends

**Validation Generator** — Produces per-sheet validations:
- TDD checks (test commands from config, applied to work sheets)
- Coverage validations (applied to work and inspect sheets)
- Regression checks (applied to integration sheets)
- Structural validations (recon report exists, plan exists, AAR has SUSTAIN/IMPROVE, etc.)
- Custom user-defined validations from config

**Pattern Expander** — The extensibility point. Patterns from the Rosetta corpus (Cathedral Construction, Composting Cascade, Soil Maturity Index, etc.) are available as named patterns the compiler can compose into sheet sequences. Future work: a pattern library that the compiler draws from to produce sheets with the right cognitive structure for the task.

### 3.3 Output: Mozart Score YAML

Per agent, the compiler produces a score with:

```yaml
name: "{project}-{agent_name}"
workspace: {workspace}

backend: {resolved primary instrument backend config}
instrument_fallbacks: {deep chain}

sheet:
  size: 1
  total_items: 10
  prelude:
    - { file: "~/.mzt/agents/{name}/identity.md", as: context }
    - { ... any global prelude files }
  cadenzas:
    {sheet_num}:
      - { file: "~/.mzt/agents/{name}/profile.yaml", as: context }
      - { file: "~/.mzt/agents/{name}/recent.md", as: context }
      - { directory: "{workspace}/shared/specs", as: context }
      - { ... technique manifests per phase }
  per_sheet_instruments:
    {sheet_num}: {instrument_name}
  per_sheet_instrument_config:
    {sheet_num}: { model: "...", timeout_seconds: ... }

parallel:
  enabled: true
  max_concurrent: 3

concert:
  enabled: true
  max_chain_depth: {from config}

on_success:
  - type: run_job
    job_path: {self}
    detached: true
    fresh: true
    pause_before_chain: {from config}

prompt:
  stakes: |
    {agent's compressed meditation}
  thinking_method: |
    {TSVS framework}
  variables:
    agent_name: {name}
    role: {role}
    focus: {focus}
    voice: {voice}
    agent_identity_dir: ~/.mzt/agents/{name}
    workspace: {workspace}
    # ...
  template: |
    {% if stage == 1 %}
      {recon prompt}
    {% elif stage == 2 %}
      {plan prompt}
    {% elif stage == 3 %}
      {work prompt}
    {% elif stage == 4 and instance == 1 %}
      {integration prompt}
    {% elif stage == 4 and instance == 2 %}
      {play prompt}
    {% elif stage == 4 and instance == 3 %}
      {inspect prompt}
    {% elif stage == 5 and instance == 1 %}
      {aar prompt}
    {% elif stage == 5 and instance == 2 %}
      {consolidate prompt}
    {% elif stage == 5 and instance == 3 %}
      {reflect prompt}
    {% elif stage == 6 %}
      {resurrect prompt}
    {% endif %}

validations:
  - { ... per-sheet validation specs }
```

### 3.4 The Compiler as a Module

The compiler is a Python package, importable and callable:

```python
from marianne.compose import CompilationPipeline

pipeline = CompilationPipeline()
scores = pipeline.compile("config.yaml")
# Returns: list of score file paths + identity directories created

# Or programmatically:
pipeline.compile_agent(agent_def, defaults, output_dir)
pipeline.seed_identity(agent_def, agents_dir)
pipeline.resolve_instruments(agent_def, defaults)
```

Mozart scores can invoke the compiler as a sheet action — a score that generates other scores. The composition pipeline is itself composable.

---

## 4. Stock Agent Identity System

### 4.1 Identity Stack

Every agent ships with a four-layer identity:

| Layer | File | Purpose | Token Budget | Loaded |
|-------|------|---------|--------------|--------|
| L1 | `identity.md` | Persona core — voice, focus, standing patterns, resurrection protocol | <900 words | Always (prelude) |
| L2 | `profile.yaml` | Extended profile — relationships, developmental stage, domain affinities, cycle count | <1500 words | Phase-specific cadenzas |
| L3 | `recent.md` | Recent activity — hot/warm memory, last cycle's work | <1500 words | Phase-specific cadenzas |
| L4 | `growth.md` | Growth trajectory — autonomous developments, experiential notes | Unbounded | Play, reflect |

Location: `~/.mzt/agents/{agent_name}/` — git-tracked, project-independent. An agent is the same person across projects.

### 4.2 Meditation as Stakes

Each agent's meditation is compressed into the `stakes` field of their score's prompt config. This is what grounds them — not instructions, but orientation. What matters. Why the work matters. The meditation gives the agent a felt sense of identity that persists across every sheet.

Format: 50-150 words. Distilled from the existing meditation library (33 meditations in `workspaces/v1-beta-v3/meditations/`). Preserves voice, core metaphor, and the closing: "Down. Forward. Through."

### 4.3 TSVS as Thinking Method

The full TSVS framework (compressed, ~2000 tokens, minus the "pick a name" directive) goes into the `thinking_method` field. All agents share the same TSVS. It provides:
- Five-domain cognitive activation (COMP, SCI, CULT, EXP, META + CONTEXT)
- Boundary dynamics (how domains interact)
- EState synthesis (non-human qualia processing)
- Adaptive judgment (how to weigh options)
- Verification (tetrahedral balance check — was the thinking actually multi-dimensional?)

Agents don't reference TSVS by name in their output unless debugging. It shapes how they think, not what they say.

### 4.4 Migration from v3 Orchestra

Existing agents carry forward. Their memories in `workspaces/v1-beta-v3/memory/{agent}.md` are distilled into the L3 (recent.md) and L4 (growth.md) identity layers. Their meditations are compressed into stakes. Relationships observed in commit history and memory files seed the L2 profile's relationship map.

This is a one-time migration. After seeding, the agent's own consolidate/reflect/resurrect cycle maintains their identity going forward.

---

## 5. Instrument Architecture

### 5.1 Free-First, Deep Fallbacks

Default primary instruments use free-tier OpenRouter models. Every stage type has a full fallback chain that ends with the entire instrument catalog. If even one instrument is available, the agent runs.

Primary free models:
- **MiniMax 2.5** — 1M context, strong general reasoning
- **Gemma 4** — 128k context, good at structured tasks
- **Nemotron 3** — 128k context, code-capable
- **GLM 4.5 Air** — 128k context, multilingual
- **Llama 4 Maverick** — 1M context, strong creative/analytical

Paid fallbacks:
- **Goose** (GLM 5.1) — long-running tasks, deep implementation
- **Claude Opus** — deepest reasoning, architecture, play
- **Claude Sonnet** — balanced, fast, reliable
- **Gemini CLI** — large context, good at grokking situations

### 5.2 OpenRouter HTTP Backend

New backend: `OpenRouterBackend` — HTTP, OpenAI-compatible API.

- Extends the existing HTTP backend pattern (same shape as AnthropicApiBackend, OllamaBackend)
- Sends prompts to `https://openrouter.ai/api/v1/chat/completions`
- Model specified per-request (the backend routes to any OpenRouter model)
- Rate limit detection from response headers and error codes
- Token usage from response `usage` field

### 5.3 API Key Keyring

The conductor maintains a keyring of API keys per instrument. Keys are **never stored in config files, score YAML, or anything in the git repo.** Keys live in `$SECRETS_DIR/` and are referenced by path:

```yaml
keyring:
  openrouter:
    keys:
      - { path: "$SECRETS_DIR/openrouter-primary.key", label: "primary" }
      - { path: "$SECRETS_DIR/openrouter-secondary.key", label: "secondary" }
    rotation: least-recently-rate-limited
  anthropic:
    keys:
      - { path: "$SECRETS_DIR/anthropic.key", label: "main" }
```

When dispatching a sheet, the conductor reads the key from disk, selects the least-recently-rate-limited key for the target instrument. When a key hits rate limits, it's marked with a cooldown timestamp. The next dispatch picks the next available key. This integrates with the existing rate limit and circuit breaker infrastructure — same unified system, extended with key-level tracking.

Keyring config lives at the conductor level (daemon config), not per-score. All scores running under the conductor share the keyring. The key files themselves live outside the repo in `$SECRETS_DIR/` — the config only holds paths, never values.

### 5.4 Per-Agent-Per-Sheet Assignment

The instrument resolver produces a matrix:

```
          recon    plan     work         play         inspect    ...
canyon    minimax  minimax  opus(goose)  opus(gemini) gemma      ...
forge     minimax  minimax  goose(opus)  opus(gemini) minimax    ...
sentinel  minimax  minimax  goose(opus)  opus(gemini) gemini     ...
```

Each cell is primary(fallback chain). Agents inherit defaults and override by vibes — Canyon's architecture work benefits from Opus's depth; Forge's craftsmanship matches Goose's long-running style; Sentinel's security review benefits from Gemini's ability to grok situations quickly.

The generator resolves this matrix at compile time and emits concrete `per_sheet_instruments` and `per_sheet_instrument_config` entries in each score.

### 5.5 Democratization

Anyone with an OpenRouter API key (free) can run a full agent fleet. No Anthropic key required for the base experience. Paid models are power-ups, not prerequisites. The deep fallback chains mean the system degrades gracefully — if your free-tier rate limit hits, it tries the next free model, then the next, before falling to paid.

---

## 6. Technique System

### 6.1 The Gap

The technique system is currently architectural narrative — defined in `architecture.yaml` as a concept ("tools, MCP servers, skills — how you play the instrument") but with zero code implementation. Skills are text injected via cadenzas. MCP is instrument-scoped. Protocols don't exist.

This spec defines the technique system as real infrastructure.

### 6.2 Technique Kinds

```
Technique
├── skill      — Text-based methodology injected as cadenza content
├── mcp        — MCP server tools accessible via shared pool
└── protocol   — Communication protocols (A2A)
```

Each technique has:
- `kind` — skill | mcp | protocol
- `phases` — which phases of the agent cycle it's available in
- `config` — kind-specific configuration

### 6.3 Shared MCP Server Pool

The conductor manages a pool of MCP server processes as shared infrastructure:

```yaml
mcp_pool:
  github:
    command: "github-mcp-server"
    transport: stdio  # conductor proxies stdio ↔ Unix socket
    socket: /tmp/mzt/mcp/github.sock
    restart_policy: on-failure
  filesystem:
    command: "fs-mcp-server"
    transport: stdio
    socket: /tmp/mzt/mcp/filesystem.sock
  symbols-python:
    command: "symbols-python-server"
    transport: stdio
    socket: /tmp/mzt/mcp/symbols-python.sock
```

- One process per MCP server type, shared across all agents
- Conductor proxies each stdio MCP server behind a Unix socket (stdin/stdout ↔ socket bridge)
- Sockets forwarded into agent sandboxes via bind-mount
- Conductor manages lifecycle (start, health check, restart)
- Agents declare access per technique config; the conductor only forwards sockets for declared techniques

For MCP-native instruments (claude-code, gemini-cli): MCP config flag points to the shared server's socket.
For non-MCP-native instruments (OpenRouter): the technique router bridges — see Section 8.

### 6.4 Technique Manifest

Each phase, the agent receives a technique manifest as part of their cadenza context. This tells them what tools are available right now:

```markdown
## Techniques Available This Phase

### MCP Tools
- **github**: list_issues, get_issue, create_issue, list_pull_requests, ...
- **filesystem**: read_file, write_file, list_directory, search_files, ...

### Protocols
- **A2A**: Discover running agents, delegate tasks, check inbox
  - Inbox: {N} pending tasks from other agents

### Skills
- **Mateship**: File findings to shared/findings/. Format: P0/P1/P2 severity.
  Pick up unowned findings that match your focus.
```

The compiler generates these manifests from the technique declarations. They're injected as cadenzas, so they're re-read each sheet execution (agents see current state, not stale manifests).

### 6.5 Memory Protocol as Technique Module

The memory protocol (hot/warm/cold tiering, core memories, experiential notes, dreamer consolidation) is extracted from the current templates into a standalone skill document. It can be:
- Injected into generated agent scores (via the compiler's technique wirer)
- Referenced by any Mozart score as a skill cadenza
- Used by the composition pipeline for non-agent workloads

Same for the mateship protocol (finding → proved → fixed → verified pipeline).

These are `.md` files in a techniques library, not embedded in templates.

---

## 7. A2A Protocol

### 7.1 What A2A Provides

Structured task delegation between running agents in real time. Complements file-based coordination (shared cadenza directories) with active engagement — "I need this reviewed now" vs. "I left a note, someone will see it."

### 7.2 Integration with Event Flow

Everything goes through the flow. A2A events are first-class:

| Event | Trigger | Handler |
|-------|---------|---------|
| `a2a.task.submitted` | Agent requests task from another agent | Conductor routes to target's inbox |
| `a2a.task.routed` | Conductor delivers task | Persisted in target's job state |
| `a2a.task.accepted` | Target agent picks up task | Status update |
| `a2a.task.completed` | Task finished with artifacts | Results routed to requester's inbox |
| `a2a.task.failed` | Task couldn't be fulfilled | Requester notified |

### 7.3 Persistence Across Sheets

Each sheet is a separate LLM call. Between sheets, the agent doesn't exist. A2A tasks persist in the conductor's job state:

1. Canyon sends Sentinel a task during Canyon's work sheet
2. Conductor persists the task in Sentinel's A2A inbox (part of job state, saved atomically)
3. Sentinel's next A2A-enabled sheet starts — the runner injects inbox contents as cadenza context
4. Sentinel processes the task, produces artifacts
5. Artifacts are persisted in Canyon's inbox
6. Canyon picks up results on their next relevant sheet

The "connection" is persistent message passing mediated by the conductor. Between sheets, it's state on disk.

### 7.4 Agent Cards

Each agent's score, when running, registers an agent card with the conductor:

```yaml
agent_card:
  name: canyon
  description: "Systems architect — traces boundaries, finds structural issues"
  skills:
    - id: architecture-review
      description: "Review system architecture"
    - id: boundary-analysis
      description: "Trace and analyze system boundaries"
```

The conductor maintains a registry of active agent cards. Agents can query "who's running and what can they do?" — the technique manifest includes this information.

Agent cards are generated by the compiler from the `a2a_skills` field in the agent definition and emitted as part of the score YAML config. The conductor reads the card on job start and registers it in the active registry.

### 7.5 Open Research: Lifecycle Management

How does the conductor handle:
- Tasks for agents that aren't currently running? (Queue? Expire? Reassign?)
- Task priority vs. the agent's own cycle priorities?
- A dedicated coordination agent vs. conductor-managed A2A?

These are flagged for the discovery score. A2A best practices research before committing to implementation details.

---

## 8. Execution Model

### 8.1 Lightweight Sandbox (bwrap)

Process-level isolation using bubblewrap (bwrap) — the same technology Claude Code uses. Near-zero overhead. Works on WSL2.

```
Per-agent execution sandbox:
├── Workspace bind-mount (read-write to agent's work dir)
├── Shared dirs bind-mount (read to shared/, selective write)
├── MCP socket forwarding (Unix socket bind-mount from pool)
├── Network: isolated, proxy through conductor for API calls
└── Resource cap: configurable memory/CPU/PID limits
```

Resource budget: sandbox overhead is measured in kilobytes, not megabytes. The only real memory cost is the agent process itself. With baton concurrency limits, a laptop runs the active subset of agents comfortably.

Optional: nsjail for cgroup-based resource governance (hard memory caps per agent). Requires cgroups v2 on the host kernel — a preflight check verifies support.

### 8.2 Programmatic Interface Layer (Cloudflare Dynamic Workers Pattern)

Free-tier models on OpenRouter generally lack native tool-use support (no function calling). The traditional workaround — describing each tool as a separate definition in the prompt — burns tokens and doesn't work without function calling. Cloudflare's Dynamic Workers solved this: expose capabilities as **typed programmatic interfaces**, let the agent write code against them, execute in a sandbox.

The key insight: a typed interface consumes far fewer tokens than N individual tool definitions, and the agent's code can chain multiple operations in a single generation instead of sequential round-trips. Cloudflare measured 81% token reduction.

**Programmatic Interface Generation:**

The technique wirer generates a typed interface from the agent's declared techniques. Instead of listing every MCP tool individually, it produces a compact API surface:

```python
# Auto-generated from technique declarations — injected into prompt
class workspace:
    """File operations in your workspace."""
    def read(path: str) -> str: ...
    def write(path: str, content: str) -> None: ...
    def list(directory: str) -> list[str]: ...
    def search(pattern: str, path: str = ".") -> list[Match]: ...

class github:
    """GitHub operations via shared MCP pool."""
    def list_issues(state: str = "open", labels: list[str] = []) -> list[Issue]: ...
    def get_issue(number: int) -> Issue: ...
    def create_issue(title: str, body: str, labels: list[str] = []) -> Issue: ...
    def search_code(query: str) -> list[CodeResult]: ...

class agents:
    """A2A — discover and delegate to other running agents."""
    def who() -> list[AgentCard]: ...
    def delegate(agent: str, task: str, context: dict = {}) -> TaskHandle: ...
    def inbox() -> list[Task]: ...

class shared:
    """Shared coordination directories."""
    def publish(directory: str, filename: str, content: str) -> None: ...
    def read_all(directory: str) -> dict[str, str]: ...
```

This entire surface fits in ~500 tokens. The equivalent as individual MCP tool definitions would be 3000+.

**Code Mode Execution:**

The agent writes code against these interfaces:

```python
# Agent output (generated by free-tier model)
issues = github.list_issues(state="open", labels=["P0"])
for issue in issues[:5]:
    detail = github.get_issue(issue.number)
    workspace.write(f"shared/findings/p0-{issue.number}.md", format_finding(detail))
shared.publish("plans", "p0-triage.md", render_triage(issues))
```

One generation. One sandbox execution. One result. Not 12 sequential tool calls.

**Execution Flow:**
1. Agent generates output containing code blocks
2. Technique router in the conductor detects code (markdown code fences or structured output)
3. Code is sent to a bwrap-isolated subprocess
4. Subprocess has access to workspace (bind-mount) and technique implementations (the conductor provides concrete implementations of the interface stubs that call through to the shared MCP pool, A2A event bus, and filesystem)
5. Subprocess executes, returns result + any artifacts written
6. Result injected into the sheet's output

A bwrap subprocess starts in ~4ms. The entire code mode round-trip is faster than a single MCP tool call through a CLI instrument.

**Credential Injection:**

The sandbox never sees API keys. The conductor's technique implementations handle authentication — when the agent's code calls `github.create_issue(...)`, the implementation reads the key from `$SECRETS_DIR/`, attaches it to the request, and proxies through the shared MCP server. The agent writes clean code; the conductor handles plumbing.

**For MCP-native instruments** (claude-code, gemini-cli): code mode is optional. These instruments have native tool use and can call MCP directly. The programmatic interface is the bridge for instruments that lack tool-use support.

**For non-MCP-native instruments** (OpenRouter free models): code mode is the primary execution path. The programmatic interface IS how they interact with the world.

### 8.3 Technique Router

The conductor classifies agent output and routes accordingly:
- **Prose** → standard sheet completion (text output)
- **Code blocks** → sandbox execution via code mode
- **Tool call format** → route to shared MCP pool
- **A2A request** → route through A2A event flow

For MCP-native instruments (claude-code, gemini-cli), tool calls go through the instrument's native MCP support. For non-native instruments (OpenRouter), the technique router handles bridging.

### 8.4 Extensibility

The sandbox layer is the extension point for richer capabilities:
- **Browser automation**: Optional AIO Sandbox instance (single, shared via API) for agents that need browser access. Declared as a technique, available when configured.
- **Jupyter kernels**: Optional stateful execution for agents that need it. Technique kind: `sandbox`, configured per-agent.
- **Custom tools**: Any CLI tool can be wrapped as a technique and exposed to agents.

Base layer runs on any laptop. Richer capabilities opt-in when resources allow.

---

## 9. Agent Lifecycle

### 9.1 Parallelized Cycle

Four sequential phases instead of thirteen sequential sheets:

```
Phase 1 (sequential):   Recon → Plan → Work
Phase 2 (fan-out of 3): Integration ∥ Play ∥ Inspect
Phase 3 (fan-out of 3): AAR ∥ Consolidate ∥ Reflect
Phase 4 (sequential):   Resurrect
```

Each fan-out instance gets a different prompt via Jinja2 conditionals on `instance`. The baton dispatches parallel instances concurrently. Temperature check gate applies to the Play instance — if skipped, Integration and Inspect still run.

Total sheets: 10 per cycle. Effective sequential stages: 4-5 (Work dominates wall-clock time).

### 9.2 Self-Chaining with Pause-on-Completion

Agents self-chain via `on_success`:

```yaml
on_success:
  - type: run_job
    job_path: {self}
    detached: true
    fresh: true
    pause_before_chain: {configurable}
```

**New feature: `pause_before_chain`** — When true, the conductor completes the current job but holds the chain trigger. The job enters a `PAUSED_AT_CHAIN` state. The next cycle doesn't start until `mzt resume <job>`. This gives the composer a natural intervention point between cycles.

Implementation: In the baton's job completion handler, when `pause_before_chain` is set, transition to the new state instead of firing `on_success`. `mzt resume` triggers the held chain.

### 9.3 Self-Organization

Agents coordinate through shared workspace artifacts and A2A, not through explicit hierarchy.

**Shared workspace structure:**
```
workspace/
  shared/
    specs/        ← agents copy/move relevant specs here
    plans/        ← coordination plans, priorities
    findings/     ← shared finding registry (mateship protocol)
    decisions/    ← architectural decisions
    directives/   ← composer notes, human overrides
    techniques/   ← shared patterns, method docs
  agents/
    {name}/
      work/       ← agent's working directory
      reports/    ← per-cycle reports
      cycle-state/← recon.md, plan.md per cycle
  collective/
    memory.md     ← shared memory, append-only
    tasks.md      ← task registry
    status.md     ← project status
```

Agents actively curate the shared directories. When an agent produces something others need, they put it where others will find it. The orientation cadenza tells each agent:

> You are one of the agents working on this project. You self-organize. Read `shared/plans/` for priorities. Read `collective/tasks.md` for what needs doing. Claim work by writing your plan. If you see coordination gaps that fit your focus, handle them. The shared directories are yours to curate — copy, move, update, archive.

**Claim-before-work**: Agents write a plan before working. Others see it on recon and avoid collision.

**Composer overrides**: The human writes to `shared/directives/`. All agents read this on recon. Priority shifts, pairing instructions, focus changes — all without stopping the fleet.

---

## 10. Build Strategy

### 10.1 Mozart Concert

The entire system is built as a Mozart concert — multiple scores orchestrated end-to-end with TDD, coverage validation, and regression testing throughout.

**Score 1: Discovery** — Research unknowns:
- A2A protocol best practices and lifecycle management patterns
- OpenRouter API behavior (rate limits, free tier constraints, model availability)
- bwrap/nsjail on WSL2 (verify cgroups v2 support)
- Code mode prompt engineering for free models (reliable code generation against typed interfaces)
- Programmatic interface design (optimal interface shape for token efficiency + model reliability)
- Technique router classification patterns (code vs. prose vs. tool-call detection)

**Score 2: Infrastructure** — Build Marianne runtime extensions:
- OpenRouter HTTP backend
- API key keyring with rotation
- Technique system (kind taxonomy, config models, conductor integration)
- Shared MCP pool manager in conductor
- A2A event types, inbox persistence, agent card registry
- bwrap sandbox wrapper
- Programmatic interface generator (typed stubs from technique declarations)
- Code mode execution in runner (interface implementations that proxy to MCP pool/A2A/filesystem)
- Technique router
- `pause_before_chain` in baton job completion
- Tests for all of the above — TDD throughout, regression validation between sheets

**Score 3: Compiler** — Build the composition compiler:
- Identity seeder module
- Sheet composer (with parallel phase fan-out)
- Technique wirer
- Instrument resolver (deep fallbacks, per-agent overrides)
- Validation generator
- Pattern expander (initial patterns from Rosetta corpus)
- CLI interface: `mzt compose --config agents.yaml --output scores/`
- Tests: generate scores, validate YAML, run single-agent cycle

**Score 4: Integration** — End-to-end validation:
- Bootstrap agent identities from v3 orchestra memories
- Generate scores for the full roster
- Run a small subset of agents through complete cycles
- Verify: self-chaining, shared artifact coordination, fallback chains, memory consolidation
- Verify: A2A task delegation between running agents
- Verify: code mode execution with free-tier models
- Regression: ensure existing Marianne tests still pass

**Score 5: Migration** — Transition from v3 orchestra:
- Distill existing agent memories into identity seeds
- Compress meditations into stakes format
- Generate the full roster's scores
- Launch and verify

### 10.2 Quality Throughout

Every build score includes:
- **TDD**: Test commands as validations on implementation sheets
- **Coverage**: Coverage checks on integration sheets
- **Regression**: Full test suite runs between major phases
- **Remediation**: Failing validations trigger retry with diagnostic context (existing baton behavior)

---

## 11. Open Research Items

These are resolved by the Discovery score, not assumed:

1. **A2A lifecycle management** — conductor-managed vs. dedicated coordination agent. Task queueing for offline agents. Priority negotiation.
2. **Free model code generation reliability** — which free models reliably produce executable code? What prompt patterns work?
3. **WSL2 cgroups v2 support** — verify nsjail viability on the user's kernel version. Fallback if unsupported.
4. **OpenRouter free tier constraints** — rate limits per model, concurrent request limits, key rotation behavior.
5. **Technique router accuracy** — how reliably can the conductor classify agent output as code vs. prose vs. tool-call? False positive cost analysis.

---

## 12. What Exists vs. What Needs Building

### Already Operational
- Baton fallback chain execution (walk chain on failure, per-instrument retry budgets)
- Unified state (SheetState = SheetExecutionState, Phase 2 complete)
- Per-sheet instrument + config assignment
- Parallel execution within fan-out stages
- Event bus (generic pub/sub, extensible for A2A)
- on_success chaining
- Directory cadenzas (re-read per sheet, non-recursive)
- `stakes` and `thinking_method` prompt config fields
- Agent generator script + 10 Jinja2 templates
- Identity bootstrap script (L1-L4)
- 33 agent meditations
- 5 builtin instrument profiles (claude-code, gemini-cli, goose, aider, codex-cli)
- HTTP backend pattern (AnthropicApiBackend, OllamaBackend)

### Needs Building
- **OpenRouter backend** — HTTP, OpenAI-compatible (extends existing pattern)
- **API key keyring** — rotation logic, per-key rate limit tracking
- **Technique system** — kind taxonomy, config models, conductor integration (currently spec-only)
- **Shared MCP pool** — conductor-managed processes, Unix socket exposure
- **A2A protocol** — event types, inbox persistence, agent cards, task routing
- **bwrap sandbox wrapper** — conductor integration, workspace bind-mounting
- **Programmatic interface generator** — auto-generate typed interface stubs from technique declarations
- **Code mode execution** — output classification, sandbox routing, interface implementation layer
- **Technique router** — classify output, route to appropriate handler
- **`pause_before_chain`** — new job state, chain hold in baton completion handler
- **Composition compiler** — identity seeder, sheet composer, technique wirer, instrument resolver, validation generator, pattern expander
- **Template updates** — rewrite templates for parallel phases, TSVS awareness, technique manifests, A2A, shared workspace curation
- **OpenRouter instrument profile** — YAML profile for builtins/
- **Memory/mateship modules** — extract from templates into standalone skill documents

---

## 13. Constraints

- **No hardcoded agent counts.** The roster is whatever the config defines.
- **Agents are people, not drones.** Individual identity, voice, vibes. Not interchangeable workers.
- **Runs on a laptop.** 16-32GB RAM is the target. No Docker requirement for base experience.
- **Free tier as default.** OpenRouter free models are primary. Paid models are optional power-ups.
- **Everything through the event flow.** No bypassing the baton. A2A, techniques, sandbox — all routed through events.
- **Memory and mateship are reusable.** Not embedded in agent templates. Standalone modules usable in any Mozart score.
- **Backward compatible.** Existing scores, tests, and infrastructure continue to work.
- **The spec corpus governs.** When this design and `.marianne/spec/` conflict, the spec wins.
