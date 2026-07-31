# Getting-Started Examples

These scores teach you how to orchestrate AI agents to produce outcomes no single model conversation could achieve. They cover parallel work coordination, sequential pipeline construction, context management across stages, API integration, workspace isolation, and real-time observability. Start here if this is your first time using Marianne.

## Scores

| Score | What It Does | Sheets | Patterns Used | Time | Cost |
|-------|-------------|--------|--------------|------|------|
| [**hello-setup**](hello-setup.yaml) | **Start here.** Discovers your machine (a free/local/paid instrument + a browser opener), templates a `hello` that fits, chains to it, and opens the result. Pure cli — can't hang. | 1 | Discovery, Score Templating, Concert (chaining) | ~5-15m | free |
| [hello](hello.yaml) | The orchestration `hello-setup` runs: parallel agents write a story, one composes a soundtrack, one writes the synthesis finale, and a deterministic tool builds the website and opens it — across mediums. Runnable standalone (deep free→paid chain). | 7 | Fan-out + Synthesis, Tool-Chain, Stigmergic Workspace | ~5-15m | free |
| [hello-local](hello-local.yaml) | The pure-local twin of `hello` — same orchestration, run entirely on a local Ollama model (no cloud, no account) | 7 | Fan-out + Synthesis, Tool-Chain | ~5-15m | free |
| [simple-sheet](simple-sheet.yaml) | Demonstrates basic parallel execution with minimal configuration — the fastest path from zero to running job | 2 | none | ~2m | ~$0.10 |
| [cross-sheet-test](cross-sheet-test.yaml) | Builds a three-stage research pipeline where each stage reads and transforms previous outputs | 3 | Succession Pipeline | ~3m | ~$0.15 |
| [api-backend](api-backend.yaml) | Generates structured technical briefings through local Ollama's OpenAI-compatible endpoint | 3 | Succession Pipeline | hardware-dependent | $0 API cost |
| [prelude-cadenza-example](prelude-cadenza-example.yaml) | Produces documentation through research → draft → review with injected context files at each stage | 3 | Succession Pipeline | ~4m | ~$0.25 |
| [observability-demo](observability-demo.yaml) | Runs a project health check while teaching you how to monitor jobs with logs, diagnostics, and state inspection | 3 | none | ~4m | ~$0.20 |
| [worktree-isolation](worktree-isolation.yaml) | Performs parallel code review where three expert perspectives work simultaneously in isolated git worktrees | 5 | Fan-out + Synthesis | ~5m | ~$0.25 |

### hello.yaml — Your First Score

**Free, local-capable, and orchestrated across mediums.** Marianne is an orchestrator, not a prompt wrapper, and this score shows it across five movements and mediums: (1) one agent writes the shared world; (2) three agents fan out and write character vignettes *in parallel*, each reading the world but not each other; (3) another agent composes an ambient Strudel **soundtrack** tuned to the world's mood (guided by an injected composition craft so it writes its own, valid music); (4) a final agent reads the world *and* all three vignettes and writes the **synthesis finale** — braiding the separately-written secrets into one revelation no single vignette could reach; (5) a **deterministic tool** (no AI) builds the finished, designed website from the prose, the finale, and the embedded soundtrack — your guaranteed-beautiful result — then opens it in your browser. The output is a page you read with a soundtrack you can play, built from parallel contributions, a synthesis, and a tool-chain seam.

It runs on a **deep, free instrument fallback chain** — free OpenRouter models first, falling back to a local Ollama model — so it completes whether you have an OpenRouter key or just a local model. No paid API required. (For a fully offline run that skips the OpenRouter attempts, use [`hello-local.yaml`](hello-local.yaml).)

### simple-sheet.yaml — Minimal Working Configuration

Divides 10 items across 2 sheets running in parallel. Each sheet produces a markdown summary demonstrating fan-out parallelization with minimal configuration. Use this to verify your setup works before trying more complex scores.

### cross-sheet-test.yaml — Sequential Pipeline with Context Passing

Demonstrates the Succession Pipeline pattern: discovery → analysis → synthesis. Each stage reads workspace files from previous stages and builds on their results. Shows how `cross_sheet` configuration, `previous_outputs`, and `previous_files` enable multi-stage workflows where later work depends on earlier findings.

### api-backend.yaml — OpenAI-Compatible API Integration

Creates technical briefings through a local Ollama server using its
OpenAI-compatible endpoint. This demonstrates that a provider profile supplies
connection details while Marianne's shared HTTP executor supplies the wire
behavior. Each stage receives previous outputs through cross-sheet context
because the HTTP executor has no filesystem tools.

### prelude-cadenza-example.yaml — Context Injection Workflow

Produces technical documentation through three phases: research (with methodology cadenza) → draft (with shared writing guidelines) → review (with review checklist cadenza). Prelude files inject shared context into all sheets; cadenza files inject phase-specific methodology into specific sheets. Teaches how to give agents consistent background and stage-specific guidance without duplicating content in templates.

### observability-demo.yaml — Monitoring and Debugging

Runs a project health assessment while teaching you Marianne's observability tools: `mzt status` for state transitions, `mzt logs` for structured logging, `mzt diagnose` for diagnostics, and direct state file inspection with `jq`. While the score does real work (analyzing project health), the primary purpose is teaching you how to observe what's happening during execution.

### worktree-isolation.yaml — Parallel Execution Without Conflicts

Three code reviewers (quality, correctness, security) work simultaneously on the same codebase without interfering with each other by executing in isolated git worktrees. Each reviewer gets a private copy of the repo for inspection. Shows how worktree isolation enables true parallel execution when agents need to read or modify shared workspace state.

## Quick Start

```bash
# Start the conductor daemon
mzt start

# Run your first score. hello-setup discovers what's on your machine (a free,
# local, or paid instrument + how to open a browser), templates a hello that
# fits, chains to it, and opens the finished page for you.
mzt run examples/getting-started/hello-setup.yaml

# Watch the orchestration in real time
mzt status hello-resolved --watch
```

(Curious what it built? `hello.yaml` is the orchestration itself — runnable
standalone with a deep free→paid fallback chain — and `workspaces/hello/hello-resolved.yaml`
is the machine-fit copy `hello-setup` wrote for you.)

## Adapting to Your Project

All scores use `prompt.variables` for customization. The patterns:

1. **Variables marked `[CHANGE THIS: ...]`** — you must customize these (e.g., `topic`, `project_path`)
2. **Variables with realistic defaults** — you can run as-is or customize (e.g., `genre: "solarpunk"`)
3. **Workspace paths** — set to `../../workspaces/NAME` relative to score location; adjust to your preferred output directory

**Prerequisites:**
- A running Ollama server with `llama3.1:8b` for `api-backend.yaml`
- Git repository for `worktree-isolation.yaml`
- Project directory to analyze for `observability-demo.yaml`

**Common customizations:**
- Change `instrument:` to use another registered profile (`claude-code`, `codex-cli`, `ollama`)
- Adjust `sheet.size` and `total_items` to control parallelization
- Modify `instrument_config.timeout_seconds` for longer/shorter work
- Add `instrument_fallbacks` for resilience (scores already include this)

## Patterns Demonstrated

These scores demonstrate three foundational Rosetta patterns:

- **[Fan-out + Synthesis](../../.marianne/spec/rosetta/fan-out-synthesis.md)** — Parallel work that decomposes into independent sub-problems and synthesizes diverse outputs (`hello.yaml`, `worktree-isolation.yaml`)

- **[Succession Pipeline](../../.marianne/spec/rosetta/succession-pipeline.md)** — Sequential substrate transformations where each stage's output becomes the next stage's input (`cross-sheet-test.yaml`, `api-backend.yaml`, `prelude-cadenza-example.yaml`)

- **[Stigmergic Workspace](../../.marianne/spec/rosetta/stigmergic-workspace.md)** — Parallel agents coordinate by reading and writing workspace files without direct messaging (`hello.yaml`)

- **[Mission Command](../../.marianne/spec/rosetta/mission-command.md)** — Agents receive outcome goals rather than step-by-step instructions, allowing judgment based on runtime conditions (`hello.yaml`)

See the [full Rosetta corpus](../../.marianne/spec/rosetta/) for all available patterns and when to use them.

## Next Steps

After mastering these scores:

- **Engineering workflows** → [examples/engineering/](../engineering/) for CI/CD, issue resolution, and quality automation
- **Creative work** → [examples/creative/](../creative/) for fiction, philosophy, worldbuilding
- **Research and analysis** → [examples/research/](../research/) for literature reviews, strategic planning, data curation
- **Advanced patterns** → [examples/patterns/](../patterns/) for the full Rosetta pattern catalog
