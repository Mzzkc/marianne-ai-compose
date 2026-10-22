# Getting-Started Examples

These scores teach you how to orchestrate AI agents to produce outcomes no single model conversation could achieve. They cover parallel work coordination, sequential pipeline construction, context management across stages, API integration, workspace isolation, and real-time observability. Start here if this is your first time using Marianne.

## Scores

| Score | What It Does | Sheets | Patterns Used | Time | Cost |
|-------|-------------|--------|--------------|------|------|
| [**hello-setup**](hello-setup.yaml) | **Start here.** Writes a class map if absent, checks `workhorse`, prepares `hello`, then chains to it. | 1 | Discovery, Score Templating, Concert (chaining) | ~5-15m | route-dependent |
| [hello](hello.yaml) | Parallel agents write a story and soundtrack; a deterministic tool builds the site. Uses the configured `workhorse` chain. | 7 | Fan-out + Synthesis, Tool-Chain, Stigmergic Workspace | ~5-15m | route-dependent |
| [hello-local](hello-local.yaml) | The pure-local twin of `hello` — same orchestration, run entirely on a local Ollama model (no cloud, no account) | 7 | Fan-out + Synthesis, Tool-Chain | ~5-15m | free |
| [simple-sheet](simple-sheet.yaml) | Demonstrates basic parallel execution with minimal configuration — the fastest path from zero to running job | 2 | none | ~2m | ~$0.10 |
| [cross-sheet-test](cross-sheet-test.yaml) | Builds a three-stage research pipeline where each stage reads and transforms previous outputs | 3 | Succession Pipeline | ~3m | ~$0.15 |
| [api-backend](api-backend.yaml) | Generates structured technical briefings through local Ollama's OpenAI-compatible endpoint | 3 | Succession Pipeline | hardware-dependent | $0 API cost |
| [prelude-cadenza-example](prelude-cadenza-example.yaml) | Produces documentation through research → draft → review with injected context files at each stage | 3 | Succession Pipeline | ~4m | ~$0.25 |
| [observability-demo](observability-demo.yaml) | Runs a project health check while teaching you how to monitor jobs with logs, diagnostics, and state inspection | 3 | none | ~4m | ~$0.20 |
| [worktree-isolation](worktree-isolation.yaml) | Performs parallel code review where three expert perspectives work simultaneously in isolated git worktrees | 5 | Fan-out + Synthesis | ~5m | ~$0.25 |

### hello.yaml — Your First Score

**Orchestrated across mediums.** The score coordinates world building, parallel character vignettes, a Strudel soundtrack, a synthesis finale, and a deterministic page builder.

It uses the `workhorse` class. `hello-setup` writes a machine-specific ordered chain and checks it before running the score. Cost depends on the selected profile. For a fully offline run, use [`hello-local.yaml`](hello-local.yaml).

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

Produces technical documentation through three phases: research (with methodology cadenza) → draft (with shared writing guidelines) → review (with review checklist cadenza). Prelude files inject shared context into all sheets; cadenza files inject phase-specific methodology into specific sheets. The four seed files ship in `assets/prelude-demo/` and are injected from the score's own directory, so the example runs as checked out — edit them in place to make it yours. Teaches how to give agents consistent background and stage-specific guidance without duplicating content in templates.

### observability-demo.yaml — Monitoring and Debugging

Runs a project health assessment while teaching you Marianne's observability tools: `mzt status` for state transitions, `mzt logs` for structured logging, `mzt diagnose` for diagnostics, and direct state file inspection with `jq`. While the score does real work (analyzing project health), the primary purpose is teaching you how to observe what's happening during execution.

### worktree-isolation.yaml — Parallel Execution Without Conflicts

Three code reviewers (quality, correctness, security) work simultaneously on the same codebase without touching your working tree: the job runs in one detached git worktree of the repository at `workspace` (parallel sheets share it, which is safe for reviewers that read). The repository gets a private checkout for inspection. Shows how worktree isolation enables true parallel execution when agents need to read or modify shared workspace state.

## Quick Start

```bash
# Start the conductor daemon
mzt start

# Run your first score. hello-setup configures classes, prepares hello, and
# chains to the class-based score.
mzt run examples/getting-started/hello-setup.yaml

# Watch the orchestration in real time
mzt status hello-resolved --watch
```

(Curious what it built? `hello.yaml` is the orchestration itself — runnable
standalone with the `workhorse` class — and `workspaces/hello/hello-resolved.yaml`
is the path-stable copy `hello-setup` wrote for you.)

## Adapting to Your Project

All scores use `prompt.variables` for customization. The patterns:

1. **Variables marked `[CHANGE THIS: ...]`** — you must customize these (e.g., `topic`, `project_path`)
2. **Variables with realistic defaults** — you can run as-is or customize (e.g., `genre: "solarpunk"`)
3. **Workspace paths** — set to `../../workspaces/NAME` relative to score location; adjust to your preferred output directory

**Prerequisites:**
- A running Ollama server with `llama3.1:8b` for `api-backend.yaml`
- A running Ollama server with `gemma4:26b` for `hello-local.yaml`, plus an `ollama` provider in OpenCode's config (`~/.config/opencode/opencode.json`, `@ai-sdk/openai-compatible` at `http://localhost:11434/v1`) — the score pins `ollama/gemma4:26b` on the built-in `opencode` profile through a score alias, so no extra instrument profile is needed
- Git repository for `worktree-isolation.yaml`
- Project directory to analyze for `observability-demo.yaml`

**Common customizations:**
- Set `instrument:` to a capability class such as `workhorse` or to a specific registered profile when that route matters
- Adjust `sheet.size` and `total_items` to control parallelization
- Modify `instrument_config.timeout_seconds` for longer/shorter work
- Check the ordered class chain with `mzt instruments classes show`; add `instrument_fallbacks` only for a distinct route after that chain

## Patterns Demonstrated

These scores demonstrate three foundational Rosetta patterns:

- **[Fan-out + Synthesis](../../scores/rosetta-corpus/patterns/fan-out-synthesis.md)** — Parallel work that decomposes into independent sub-problems and synthesizes diverse outputs (`hello.yaml`, `worktree-isolation.yaml`)

- **[Succession Pipeline](../../scores/rosetta-corpus/patterns/succession-pipeline.md)** — Sequential substrate transformations where each stage's output becomes the next stage's input (`cross-sheet-test.yaml`, `api-backend.yaml`, `prelude-cadenza-example.yaml`)

- **[Stigmergic Workspace](../../scores/rosetta-corpus/patterns/stigmergic-workspace.md)** — Parallel agents coordinate by reading and writing workspace files without direct messaging (`hello.yaml`)

- **[Mission Command](../../scores/rosetta-corpus/patterns/mission-command.md)** — Agents receive outcome goals rather than step-by-step instructions, allowing judgment based on runtime conditions (`hello.yaml`)

See the [full Rosetta corpus](../../scores/rosetta-corpus/INDEX.md) for all available patterns and when to use them.

## Next Steps

After mastering these scores:

- **Engineering workflows** → [examples/engineering/](../engineering/) for CI/CD, issue resolution, and quality automation
- **Creative work** → [examples/creative/](../creative/) for fiction, philosophy, worldbuilding
- **Research and analysis** → [examples/research/](../research/) for literature reviews, strategic planning, data curation
- **Advanced patterns** → [examples/patterns/](../patterns/) for the full Rosetta pattern catalog
