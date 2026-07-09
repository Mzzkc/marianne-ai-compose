# Marianne

**If it runs in a CLI, Marianne can orchestrate it.**

Marianne is a universal asynchronous orchestrator. A composer writes a declarative YAML score describing what should be built, analyzed, or created. Marianne decomposes it into sheets, executes them through any combination of AI instruments, validates every output against acceptance criteria, and feeds learned patterns forward. The conductor owns execution state; the composer directs the work.

```bash
pip install -e ".[daemon]"
mzt start
mzt run examples/getting-started/hello-setup.yaml
```

Three commands. `hello-setup` discovers what's on your machine, then chains to the `hello` orchestration: parallel agents write a story, one composes a soundtrack, one writes the synthesis finale, and a deterministic tool builds a self-contained website that opens in your browser. That's Marianne working across multiple domains within a single run.

It should be noted that Marianne's developer abhors the notion of using AI to replace artists and creatives. Creative-like examples are provided on an experimental basis and for demonstration purposes only. Just because Marianne and AI *can* do something, doesn't mean you should.

---

## What It Can Do

Marianne doesn't care what domain you're working in. It orchestrates anything that speaks CLI.

### Deep Systems Work

Rewrite a C codebase in Rust with architecture upgrades. Run a 14-stage documentation overhaul with gap analysis and automated verification. Chain a 17-stage issue solver with fan-out code reviewers that self-chain into the next cycle. These are scores people run today.

### Product Generation

Full-stack SaaS applications from YAML. Parallel backend and frontend tracks validated independently against a shared interface contract. Multi-agent code review where three experts analyze different dimensions concurrently and a synthesis agent merges their findings.

### Beyond Code

PRISMA-compliant academic literature reviews. Strategic planning with multi-framework analysis. Training data curation with inter-annotator agreement. Nonfiction book manuscripts. Contract generation with cross-reference validation. Recruitment screening with weighted criteria. 24x7 stock trading. Dinner party logistics. 

The examples directory contains runnable patterns across engineering, research,
hiring, finance, product work, and operations. Its README carries the current
validation status for the checked-in examples.

---

## The Instrument System

An **instrument** is any CLI tool wrapped in a YAML profile. Marianne treats instruments like plugins: drop a profile in `~/.marianne/instruments/` or `.marianne/instruments/` and the conductor discovers it automatically. Agent harnesses, linters, formatters, deployment tools, custom scripts — if it runs in a shell, you can score with it.

**Agent harnesses that ship as built-in profiles:**

| Instrument    | What It Wraps |
|---------------|---------------|
| `claude-code` | Claude Code CLI — full Musician profile |
| `gemini-cli`  | Google Gemini CLI |
| `codex-cli`   | OpenAI Codex CLI |
| `aider`       | Aider — AI pair programming |
| `goose`       | Goose — autonomous coding agent |
| `cline-cli`   | Cline CLI |
| `crush`       | Crush — terminal-native AI agent |
| `opencode`    | OpenCode — OpenRouter + native MCP |

**Beyond agent harnesses:** any CLI takes a YAML profile. Wrap `pytest` to run validation as a sheet. Wrap `gh` to file issues from a score. Wrap your in-house deploy script. Marianne doesn't care what the binary is — only that it speaks stdin/stdout and returns an exit code.

**Mixed-instrument scores.** Use cheap, fast instruments for simple sheets (linting, formatting, boilerplate) and expensive, capable instruments for complex sheets (architecture, synthesis, creative work). One score, multiple instruments, cost-optimized by design.

```bash
mzt instruments list                # See what's available
mzt instruments check claude-code   # Deep diagnostic on one instrument
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

A composer conducts. Pause a running score, modify its config, resume. Cancel one score while others keep running. Monitor everything from the terminal or the web dashboard. The conductor is the single execution authority — no split-brain, no orphaned agents, no corrupted state.

```bash
mzt pause my-score         # Pause gracefully
mzt resume my-score        # Resume from checkpoint
mzt cancel my-score        # Cancel immediately
mzt stop                   # Stop conductor (only when no scores are running)
```

---

## Self-Evolution

Marianne developed much of itself through autonomous self-evolution scores — each one analyzing the codebase, identifying improvements, implementing changes, running tests, and validating results. The system that runs your scores is the system that helped build itself.

For current source and test counts, inspect the repository and CI output rather
than a static README number. The learning system records execution outcomes and
can feed useful patterns forward when the conductor has evidence for them.

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

First-class validation types include `file_exists`, `file_modified`,
`content_contains`, `content_regex`, `command_succeeds`, `path_in_scope`,
`field_match`, `file_sha256`, and `csv_unique_key`. Static preflight checks
also catch common score-authoring traps before launch, including Bash/Jinja
collisions, raw `cli` prompt bodies that do not render as shell, partial
fan-out instrument coverage, and prompt/validation section-label drift.
Validation paths use Python format strings: `{workspace}`, `{sheet_num}`.

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
| [hello-setup.yaml](examples/getting-started/hello-setup.yaml) | **Start here.** Your first score — discovers your machine, then orchestrates a story into a self-contained website with a playable soundtrack. Free, local-capable. |
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
           | claude-code  |  | gemini-cli   |  | aider        |
           | codex-cli    |  | goose        |  | cline-cli    |
           | crush        |  | opencode     |  | Any CLI      |
           |              |  |              |  | (YAML plugin)|
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
- Instruments are interchangeable — scores don't know which instrument ran them

---

## Installation

### Prerequisites

- Python 3.11+
- At least one execution-ready instrument. Run `mzt doctor` and
  `mzt instruments list` after installation; `hello-setup` can discover a
  free, local, or paid path for the first run.

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
| `mzt logs <id>` | View or tail log sources |
| `mzt history <id>` | Execution history from SQLite |
| `mzt recover <id>` | Re-validate without re-execution |

### Conductor

| Command | Purpose |
|---------|---------|
| `mzt start` | Start the conductor |
| `mzt stop` | Stop (warns if scores are running) |
| `mzt restart` | Restart only after active scores are drained or paused |
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

## Documentation & Community

- **Get started:** [`examples/getting-started/`](examples/getting-started/) — run
  [`hello-setup.yaml`](examples/getting-started/hello-setup.yaml) first; it
  discovers an instrument, writes the resolved hello score, and runs it.
- **Free / local quickstart:** [`docs/sandbox-free-quickstart.md`](docs/sandbox-free-quickstart.md)
- **Write your own scores:** [`docs/score-writing-guide.md`](docs/score-writing-guide.md)
  and the [score-authoring skill](plugins/marianne/skills/score-authoring/SKILL.md)
- **Contributing:** [`CONTRIBUTING.md`](CONTRIBUTING.md)
- **Issues & roadmap:** [GitHub Issues](https://github.com/Mzzkc/marianne-ai-compose/issues)
  · [`docs/roadmap.md`](docs/roadmap.md)

Build the documentation site locally:

```bash
pip install -e ".[docs]"
mkdocs serve
```

---

## About the Name

This project is named after **Maria Anna "Nannerl" Mozart** (1751-1829), Wolfgang Amadeus Mozart's older sister. She was a keyboard prodigy who toured Europe as a child performer, dazzling audiences with her skill. Leopold Mozart wrote that she played "so beautifully that everyone is talking about it."

But when she turned eighteen, the tours stopped. Social conventions of the time forbade women from performing publicly. While Wolfgang became one of history's most celebrated composers, Nannerl's career ended before it truly began. She was denied her stage.

This project carries her name symbolically. This project can meaningfully compete with any production orchestrator on the market. But the barriers to adoption, or consideration, mirror the conditions of Nannerl's time. Marianne will continue to be excellent and uplift those who care to listen to the orchestra.

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
