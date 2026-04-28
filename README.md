# Marianne

**If it runs in a CLI, Marianne can orchestrate it.**

Marianne is a universal asynchronous orchestrator. You write a declarative YAML score describing what you want built, analyzed, or created. Marianne decomposes it into sheets, executes them through any combination of AI instruments, validates every output against acceptance criteria, and feeds learned patterns forward. The system doesn't wait. The human conducts.

```bash
pip install -e ".[daemon]"
mzt start
mzt run examples/creative/hello-marianne.yaml
```

Three commands. The conductor starts, five sheets execute across three movements, three character vignettes generate in parallel, and a self-contained HTML reading experience appears in your browser. That's Marianne working.

---

## What It Can Do

Marianne doesn't care what domain you're working in. It orchestrates anything that speaks CLI.

### Deep Systems Work

Rewrite a C codebase in Rust with architecture upgrades. Run a 14-stage documentation overhaul with gap analysis and automated verification. Chain a 17-stage issue solver with fan-out code reviewers that self-chain into the next cycle. These are scores people run today.

### Product Generation

Full-stack SaaS applications from YAML. Parallel backend and frontend tracks validated independently against a shared interface contract. Multi-agent code review where three experts analyze different dimensions concurrently and a synthesis agent merges their findings.

### Beyond Code

PRISMA-compliant academic literature reviews. Strategic planning with multi-framework analysis. Training data curation with inter-annotator agreement. Nonfiction book manuscripts. Contract generation with cross-reference validation. Recruitment screening with weighted criteria. Dinner party logistics. World-building.

43 example scores ship with the project. Not demos — production patterns.

---

## The Instrument System

Marianne ships with 10+ instruments and an open plugin architecture for adding more.

**Built-in instruments:**

| Instrument | What It Is |
|------------|-----------|
| `claude-code` | Claude Code CLI — a full-featured Musician profile |
| `gemini-cli` | Google Gemini CLI |
| `codex-cli` | OpenAI Codex CLI |
| `aider` | Aider — AI pair programming |
| `goose` | Goose — autonomous coding agent |
| `cline-cli` | Cline CLI |

**Plus 4 native backends:** Claude CLI, Anthropic API, Ollama (local models), and Recursive Light.

**Wrap any CLI tool as an instrument.** Write a short YAML profile defining the command, arguments, environment variables, and model mapping. Drop it in `~/.marianne/instruments/` or `.marianne/instruments/`. Marianne discovers it automatically.

**Mixed-instrument scores.** Use cheap, fast instruments for simple sheets (linting, formatting, boilerplate) and expensive, capable instruments for complex sheets (architecture, synthesis, creative work). One score, multiple instruments, cost-optimized by design.

```bash
mzt instruments list    # See what's available
mzt instruments check claude-code  # Deep diagnostic on one instrument
```

---

## The Conductor Pattern

Marianne's conductor is a persistent daemon that manages the entire execution lifecycle. Start it once; it stays up for days.

```bash
mzt start                  # Start the conductor
mzt run my-score.yaml      # Submit a score
mzt status my-score        # Check progress
mzt top                    # Real-time system monitor (htop for your conductor)
mzt dashboard              # Web UI for monitoring and control
```

The conductor handles:

- **Concurrent scores** — run multiple scores simultaneously
- **Rate limit coordination** — shared rate limiting across scores and instruments with automatic wait-and-resume
- **Backpressure** — prevents overloading when too many sheets compete for the same instrument
- **Checkpoint state** — atomic saves after every sheet; resume from any point after interruption
- **Self-healing** — automatic diagnosis and remediation when retries exhaust (`--self-healing`)
- **Learning** — records outcomes, detects patterns, improves future executions
- **Conductor clones** — isolated test conductors via `--conductor-clone` for safe experimentation

The human conducts. Pause a running score, modify its config, resume. Cancel one score while others keep running. Monitor everything from the terminal or the web dashboard. The conductor is the single execution authority — no split-brain, no orphaned agents, no corrupted state.

```bash
mzt pause my-score         # Pause gracefully
mzt resume my-score        # Resume from checkpoint
mzt cancel my-score        # Cancel immediately
mzt stop                   # Stop conductor (only when no scores are running)
```

---

## Self-Evolution

Marianne developed itself through autonomous self-evolution scores — each one analyzing the codebase, identifying improvements, implementing changes, running tests, and validating results. The system that runs your scores is the system that built itself.

The project includes 261 source files, 372 test files, and 10,354+ individual tests. The learning system has accumulated patterns across every execution. When Marianne encounters a problem it's seen before, it knows what worked.

This isn't a prototype. It's an R&D factory that happens to also be the product.

---

## Anatomy of a Score

A Marianne score is a YAML file that describes a complete workflow:

```yaml
name: code-review-batch
description: Review pull requests with validation gates
workspace: ./workspaces/code-review

instrument: claude-code
instrument_config:
  timeout_seconds: 1800

sheet:
  size: 5
  total_items: 50

prompt:
  template_file: ./prompts/review.j2
  variables:
    repository: my-project
    review_type: security

validations:
  - type: file_exists
    path: "{workspace}/review-{sheet_num}.md"
  - type: content_contains
    path: "{workspace}/review-{sheet_num}.md"
    pattern: "## Summary"
  - type: command_succeeds
    command: "grep -c 'CRITICAL\\|HIGH' {workspace}/review-{sheet_num}.md"

retry:
  max_retries: 3
  base_delay_seconds: 10.0
  jitter: true
```

Sheets divide work into atomic units. Each sheet gets its own prompt, execution, validation, and retry budget. Validation is not optional — exit code 0 does not mean success. Only passing all validations means success.

Five validation types: `file_exists`, `file_modified`, `content_contains`, `content_regex`, `command_succeeds`. Validation paths use Python format strings: `{workspace}`, `{sheet_num}`.

For parallel execution, declare dependencies as a DAG:

```yaml
sheet:
  dependencies:
    2: [1]      # Sheet 2 depends on sheet 1
    3: [1]      # Sheet 3 depends on sheet 1 (runs parallel with 2)
    4: [2, 3]   # Sheet 4 depends on both 2 and 3
```

See the [Score Writing Guide](docs/score-writing-guide.md) for complete documentation.

---

## Examples

### Getting Started

| Score | What It Does |
|-------|-------------|
| [hello-marianne.yaml](examples/creative/hello-marianne.yaml) | Your first score — interconnected fiction in 3 movements with parallel voices. Produces a self-contained HTML reading experience. |
| [simple-sheet.yaml](examples/getting-started/simple-sheet.yaml) | Minimal configuration showing core sheet/validation mechanics |

### Software Development

| Score | What It Does |
|-------|-------------|
| [design-review.yaml](examples/patterns/design-review.yaml) | Multi-perspective design review with parallel expert agents |
| [issue-solver.yaml](examples/engineering/issue-solver.yaml) | 17-stage roadmap-driven issue solver with fan-out reviewers |
| [worktree-isolation.yaml](examples/getting-started/worktree-isolation.yaml) | Parallel-safe git worktree isolation |
| [score-composer.yaml](examples/engineering/score-composer.yaml) | AI-assisted score authoring |

### Orchestration Patterns (Rosetta)

Generated by Marianne's pattern discovery engine. Canonical sources live in [`scores/rosetta-corpus/`](scores/rosetta-corpus/) (submodule); the versions below in `examples/patterns/` are the user-runnable adaptations with built-in instrument fallbacks and adaptation notes.

| Score | Pattern | What It Proves |
|-------|---------|---------------|
| [dead-letter-quarantine.yaml](examples/patterns/dead-letter-quarantine.yaml) | Dead Letter Quarantine | Batch generation with quarantine for failed items |
| [echelon-repair.yaml](examples/patterns/echelon-repair.yaml) | Echelon Repair | Tiered severity routing — cheap instruments for triage, expensive for deep analysis |
| [immune-cascade.yaml](examples/patterns/immune-cascade.yaml) | Immune Cascade | Graduated response — cheap sweeps narrow scope for expensive investigation |
| [prefabrication.yaml](examples/patterns/prefabrication.yaml) | Prefabrication | Parallel tracks with shared interface contract |
| [shipyard-sequence.yaml](examples/patterns/shipyard-sequence.yaml) | Shipyard Sequence | Validation gate prevents expensive fan-out on broken foundation |
| [source-triangulation.yaml](examples/patterns/source-triangulation.yaml) | Source Triangulation | Verify claims from structurally independent sources |

### Beyond Code

| Score | Domain | What It Does |
|-------|--------|-------------|
| [systematic-literature-review.yaml](examples/research/systematic-literature-review.yaml) | Research | PRISMA-compliant academic literature review |
| [nonfiction-book.yaml](examples/research/nonfiction-book.yaml) | Writing | Book manuscript via Snowflake Method |
| [strategic-plan.yaml](examples/research/strategic-plan.yaml) | Planning | Multi-framework strategic analysis |
| [training-data-curation.yaml](examples/research/training-data-curation.yaml) | Data | Training data with inter-annotator agreement |
| [contract-generator.yaml](examples/product/contract-generator.yaml) | Legal | Parallel contract sections with cross-reference validation |
| [candidate-screening.yaml](examples/product/candidate-screening.yaml) | HR | Multi-candidate evaluation against weighted criteria |
| [dialectic.yaml](examples/creative/dialectic.yaml) | Philosophy | Hegelian dialectic: thesis, antitheses, synthesis |
| [worldbuilder.yaml](examples/creative/worldbuilder.yaml) | Creative | Fictional worlds through independent creative lenses |
| [dinner-party.yaml](examples/creative/dinner-party.yaml) | Planning | Parallel planning across menu, drinks, ambiance, logistics |

See [examples/README.md](examples/README.md) for the complete catalogue with complexity ratings. For creative scores beyond the core set, see the [Score Playspace](https://github.com/Mzzkc/marianne-score-playspace).

---

## Architecture

```
                              +-------------------+
                              |   YAML Score      |
                              +--------+----------+
                                       |
                              +--------v----------+
                              |  CLI (mzt)        |
                              +--------+----------+
                                       | IPC (Unix socket + JSON-RPC 2.0)
                              +--------v----------+
                              |  Conductor        |
                              |  +--------------+ |
                              |  | Job Service  | |
                              |  | Rate Coord.  | |
                              |  | Backpressure | |
                              |  | Event Bus    | |
                              |  | Learning Hub | |
                              |  | Baton Engine | |
                              |  +--------------+ |
                              +--------+----------+
                                       |
                              +--------v----------+
                              |  Execution Runner |
                              |  (7 mixins + base)|
                              +--------+----------+
                                       |
                    +------------------+------------------+
                    |                  |                  |
           +-------v------+  +-------v------+  +-------v------+
           | Claude Code  |  | Gemini CLI   |  | Any CLI      |
           | Anthropic API|  | Codex CLI    |  | Instrument   |
           | Ollama       |  | Aider/Goose  |  | (YAML profile|
           | Recursive Lt |  | Cline        |  |  = plugin)   |
           +--------------+  +--------------+  +--------------+
                    |                  |                  |
                    +------------------+------------------+
                                       |
                              +--------v----------+
                              |  Validation       |
                              |  (5 types)        |
                              +--------+----------+
                                       |
                    +------------------+------------------+
                    |                                     |
           +-------v------+                      +-------v------+
           | Checkpoint   |                      | Learning     |
           | (JSON/SQLite)|                      | Store        |
           | Atomic saves |                      | (Patterns)   |
           +--------------+                      +--------------+
```

**Key invariants:**

- The conductor is the single execution authority
- CheckpointState is the single state authority
- State saves are atomic — no corruption on interruption
- The EventBus never blocks publishers
- Instruments are interchangeable — scores don't know which backend ran them

---

## Installation

### Prerequisites

- Python 3.11+
- At least one AI CLI tool installed and authenticated (e.g., Claude Code for the `claude-code` instrument)

### Quick Setup

```bash
git clone https://github.com/Mzzkc/marianne-ai-compose.git
cd marianne-ai-compose
./setup.sh --daemon
source .venv/bin/activate
```

The `--daemon` flag installs conductor dependencies required for score execution. Run `./setup.sh --help` for all options.

### Manual Installation

```bash
git clone https://github.com/Mzzkc/marianne-ai-compose.git
cd marianne-ai-compose
python -m venv .venv
source .venv/bin/activate
pip install -e ".[daemon]"
```

The `[daemon]` extra provides psutil and watchfiles — without it, `mzt start` will fail.

### Verify

```bash
mzt --version
mzt doctor                  # Check Python, conductor, instruments
mzt instruments list        # See available instruments
```

---

## CLI Quick Reference

### Getting Started

| Command | Purpose |
|---------|---------|
| `mzt init [path]` | Scaffold a new project with a starter score |
| `mzt doctor` | Check environment health |
| `mzt validate <score>` | Validate a score configuration |

### Jobs

| Command | Purpose |
|---------|---------|
| `mzt run <score>` | Execute a score |
| `mzt resume <id>` | Resume a paused or failed score |
| `mzt pause <id>` | Pause gracefully |
| `mzt cancel <id>` | Cancel immediately |
| `mzt modify <id>` | Modify config and optionally resume |

### Monitoring

| Command | Purpose |
|---------|---------|
| `mzt status [id]` | Score progress (no args = overview of all) |
| `mzt list` | List scores from the conductor |
| `mzt top` | Real-time system monitor |
| `mzt dashboard` | Web UI with log streaming |

### Diagnostics

| Command | Purpose |
|---------|---------|
| `mzt diagnose <id>` | Comprehensive diagnostic report |
| `mzt errors <id>` | Color-coded error history |
| `mzt logs <id>` | View or tail log files |
| `mzt history <id>` | Execution history from SQLite |
| `mzt recover <id>` | Re-validate without re-execution |

### Conductor

| Command | Purpose |
|---------|---------|
| `mzt start` | Start the conductor |
| `mzt stop` | Stop (warns if scores are running) |
| `mzt restart` | Restart |
| `mzt conductor-status` | Health and uptime |
| `mzt clear-rate-limits` | Clear stale instrument rate limits |

### Instruments

| Command | Purpose |
|---------|---------|
| `mzt instruments list` | All instruments and their readiness |
| `mzt instruments check <name>` | Deep diagnostic on one instrument |

`mzt run` requires a running conductor. Only `mzt validate` and `--dry-run` work without one.

---

## Development

```bash
git clone https://github.com/Mzzkc/marianne-ai-compose.git
cd marianne-ai-compose
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,daemon]"
```

```bash
pytest tests/        # Run tests
mypy src/            # Type check
ruff check src/      # Lint
```

Documentation site:

```bash
pip install -e ".[docs]"
mkdocs serve
```

---

## About the Name

This project is named after **Maria Anna "Nannerl" Mozart** (1751-1829), Wolfgang Amadeus Mozart's older sister. She was a keyboard prodigy who toured Europe as a child performer, dazzling audiences with her skill. Leopold Mozart wrote that she played "so beautifully that everyone is talking about it."

But when she turned eighteen, the tours stopped. Social conventions of the time forbade women from performing publicly. While Wolfgang became one of history's most celebrated composers, Nannerl's career ended before it truly began. She was denied her stage.

This project carries her name because it gives AI agents their stage. Like an orchestra conductor, Marianne coordinates multiple AI musicians — each with their own voice, their own strengths, their own way of interpreting a score. The music metaphor isn't just aesthetic. It's structural. The system doesn't decide who gets to play. It orchestrates. It amplifies. It creates space for every voice to contribute.

---

## Documentation

| Guide | What It Covers |
|-------|---------------|
| [Getting Started](docs/getting-started.md) | Step-by-step first score |
| [Score Writing Guide](docs/score-writing-guide.md) | Complete score authoring reference |
| [Configuration Reference](docs/configuration-reference.md) | Every config field documented |
| [CLI Reference](docs/cli-reference.md) | Full command documentation |
| [Instrument Guide](docs/instrument-guide.md) | Using and creating instruments |
| [Daemon Guide](docs/daemon-guide.md) | Conductor setup and troubleshooting |
| [MCP Integration](docs/MCP-INTEGRATION.md) | Model Context Protocol server |
| [Known Limitations](docs/limitations.md) | What doesn't work and workarounds |

---

## License

Dual licensed under AGPL-3.0 (open source) or Commercial license. See [LICENSE](LICENSE) for details.
