# Mozart AI Compose

**Cognitive orchestration for AI-assisted work**

Mozart orchestrates multi-phase AI workflows with checkpointing, validation gates, and automatic recovery. Whether you're processing code reviews, writing documentation, curating training data, or conducting systematic research, Mozart ensures work completes reliably and correctly.

## Table of Contents

- [What is Mozart?](#what-is-mozart)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Features](#features)
- [CLI Reference](#cli-reference)
- [Configuration](#configuration)
- [Examples](#examples)
- [Architecture](#architecture)
- [Documentation](#documentation)
- [Development](#development)
- [License](#license)

## What is Mozart?

Mozart is a general-purpose orchestration system for AI-assisted workflows. It divides complex work into **sheets** (discrete execution units), each with its own prompt template, validation rules, and retry budget. Mozart handles the reliability concerns so you can focus on the work itself.

### Key Capabilities

- **Sheet-based execution**: Divide work into atomic units with independent retry and validation
- **Validation-first**: Exit code 0 does not mean success. Only validation pass means success
- **Automatic recovery**: Checkpoint state enables resume after interruption, rate limits, or failures
- **Self-healing**: Diagnose and fix common issues automatically when retries are exhausted
- **Learning system**: Record outcomes and detect patterns across executions
- **Multiple instruments**: Claude CLI, Anthropic API, Ollama, plus config-driven instruments (Gemini CLI, Codex CLI, Cline, Aider, Goose, and any CLI tool via YAML profiles)

### When to Use Mozart

Mozart is ideal for:

- **Multi-phase workflows**: Code review across many files, batch documentation generation
- **Tasks requiring validation**: Work that must meet quantitative quality gates
- **Long-running operations**: Jobs that may be interrupted and need reliable resume
- **Parallel execution**: Independent work streams that can run concurrently
- **Cross-project learning**: Accumulating patterns across multiple job executions

Mozart is NOT for:

- Single-shot prompts (use Claude CLI directly)
- Interactive conversations (use Claude Code)
- Real-time chat applications (use the API directly)

## Installation

### Prerequisites

- Python 3.11+
- Claude CLI installed and authenticated (for `claude_cli` backend)

### Quick Setup (Recommended)

The setup script handles virtual environment creation, dependency installation, and verification:

```bash
git clone https://github.com/Mzzkc/mozart-ai-compose.git
cd mozart-ai-compose
./setup.sh --daemon
```

The `--daemon` flag installs daemon dependencies required for job execution. After setup completes, activate the virtual environment:

```bash
source .venv/bin/activate
```

For development (includes pytest, mypy, ruff):

```bash
./setup.sh --dev --daemon
```

Run `./setup.sh --help` for all options.

### Manual Installation

If you prefer manual setup:

```bash
git clone https://github.com/Mzzkc/mozart-ai-compose.git
cd mozart-ai-compose
python -m venv .venv
source .venv/bin/activate
pip install -e "."
```

### Verify Installation

```bash
mozart --version
```

## Quick Start

### 1. Check Your Environment

```bash
mozart doctor
```

This verifies Python, conductor status, and available instruments.

### 2. Validate the Hello Score

Mozart ships with `examples/hello.yaml` — a score that creates an interconnected fiction collection in three movements: a world setting, three parallel character vignettes, and a finale.

```bash
mozart validate examples/hello.yaml
```

### 3. Start the Conductor

The Mozart conductor is required for job execution:

```bash
mozart start
mozart conductor-status   # Verify it's running
```

### 4. Run the Score

```bash
mozart run examples/hello.yaml
```

Mozart executes each sheet, validating output before proceeding. The three character vignettes in movement 2 run in parallel.

### 5. Monitor Progress

While the score runs (or after), check status:

```bash
mozart status hello-mozart
```

### 6. Read the Output

```bash
cat workspaces/hello-mozart/03-finale.md
```

The workspace contains five files: a world setting, three character vignettes, and a finale that weaves them together.

### 7. Resume if Interrupted

If the job is interrupted (Ctrl+C, rate limit, error), resume from where it left off:

```bash
mozart resume hello-mozart
```

## Features

### Core Features

| Feature | Description |
|---------|-------------|
| Sheet-based execution | Divide work into atomic sheets with independent prompts and validation |
| Validation system | Five validation types: file_exists, file_modified, content_contains, content_regex, command_succeeds |
| Retry logic | Exponential backoff with jitter, completion mode for partial success |
| State management | Atomic checkpoint saves with JSON or SQLite backends |
| Rate limit handling | Automatic detection, wait, and resume |
| Graceful shutdown | Ctrl+C saves state for later resume |

### Advanced Features

| Feature | Description |
|---------|-------------|
| Self-healing | Automatic diagnosis and remediation when retries exhausted (`--self-healing`) |
| Learning system | Outcome recording, pattern detection, cross-workspace learning |
| Parallel execution | DAG-based sheet dependencies for concurrent execution |
| Web dashboard | Real-time monitoring with job control and log streaming |
| Job chaining | Chain jobs via on_success hooks (hooks fire; concert depth tracking is a known TODO) |
| Worktree isolation | Git worktree isolation for parallel-safe execution |
| Cost tracking | Per-sheet and per-job cost limits |
| Circuit breaker | Cross-workspace coordination, rate limit sharing |
| Human-in-the-loop | Escalation for low-confidence decisions (`--escalation`) — not currently supported in daemon mode |

## CLI Reference

### Core Commands

| Command | Purpose |
|---------|---------|
| `mozart run <config>` | Execute a job from YAML configuration |
| `mozart resume <job-id>` | Resume a paused or failed job |
| `mozart pause <job-id>` | Pause a running job gracefully |
| `mozart modify <job-id>` | Modify config and optionally resume a paused job |
| `mozart status [job-id]` | Show job status and progress |
| `mozart validate <config>` | Validate configuration file |
| `mozart list` | List active jobs (requires daemon; use `--all` for all jobs) |
| `mozart history <job-id>` | Show execution history from SQLite |
| `mozart config <subcommand>` | Manage Mozart configuration (`show`, `set`, `path`, `init`, `check`) |

### Diagnostic Commands

| Command | Purpose |
|---------|---------|
| `mozart doctor` | Check environment health (Python, conductor, instruments) |
| `mozart logs <job-id>` | View or tail log files |
| `mozart errors <job-id>` | List job errors with color-coded output |
| `mozart diagnose <job-id>` | Comprehensive diagnostic report |
| `mozart recover <job-id>` | Re-validate without re-execution |

### Instruments

| Command | Purpose |
|---------|---------|
| `mozart instruments list` | Show all available instruments and their readiness |
| `mozart instruments check <name>` | Deep diagnostic on a specific instrument |

### Dashboard & MCP

| Command | Purpose |
|---------|---------|
| `mozart dashboard` | Start web dashboard for monitoring and control |
| `mozart mcp` | Start MCP server for tool integration |

### Learning Commands

| Command | Purpose |
|---------|---------|
| `mozart patterns-list` | List learned patterns |
| `mozart patterns-why` | Metacognitive analysis of pattern success factors |
| `mozart patterns-entropy` | Entropy monitoring for pattern diversity |
| `mozart patterns-budget` | Exploration budget status |
| `mozart learning-stats` | Learning system statistics |
| `mozart learning-insights` | Actionable insights from patterns |
| `mozart learning-drift` | Detect pattern effectiveness drift |
| `mozart learning-epistemic-drift` | Epistemic drift analysis |
| `mozart learning-activity` | Learning activity summary |
| `mozart entropy-status` | Entropy response status |

### Dashboard

```bash
mozart dashboard --port 8000
```

Starts the web dashboard for visual monitoring and control.

### Common Options

| Option | Applies To | Description |
|--------|-----------|-------------|
| `--workspace, -w <path>` | `run`, `resume` | Workspace directory override |
| `--dry-run, -n` | `run` | Validate and show execution plan without running |
| `--self-healing, -H` | `run`, `resume` | Enable automatic diagnosis and remediation |
| `--yes, -y` | `run`, `resume` | Auto-confirm suggested self-healing fixes |
| `--escalation, -e` | `run` | Enable human-in-the-loop for low-confidence decisions (not currently supported — blocked in daemon mode) |
| `--fresh` | `run` | Delete existing state before starting (clean run) |
| `--start-sheet, -s` | `run` | Override starting sheet number |
| `--json, -j` | `status`, `validate` | Output in JSON format |
| `-v, --verbose` | various | Detailed output |

### Conductor Mode

The Mozart conductor is **required** for job execution. It manages concurrent jobs, coordinates rate limits, and provides resource monitoring.

```bash
# Start the conductor (required before mozart run)
mozart start              # Background (production)
mozart start --foreground # Foreground (development)

# Check conductor status
mozart conductor-status

# Stop the conductor
mozart stop
```

`mozart run` requires a running conductor and will exit with an error if one is not found. Only `mozart validate` and `mozart run --dry-run` work without a running conductor.

See the [Daemon Guide](docs/daemon-guide.md) for configuration, systemd integration, and troubleshooting.

## Configuration

Mozart jobs are configured with YAML files. Here is a complete example demonstrating key options:

```yaml
name: code-review-batch
description: Review multiple pull requests with validation
workspace: ./workspace/code-review

instrument: claude-code
instrument_config:
  timeout_seconds: 1800

sheet:
  size: 5
  total_items: 50
  start_item: 1

prompt:
  template_file: ./prompts/review.j2
  variables:
    repository: my-project
    review_type: security

retry:
  max_retries: 3
  base_delay_seconds: 10.0
  exponential_base: 2.0
  jitter: true

validations:
  - type: file_exists
    path: "{workspace}/review-{sheet_num}.md"
    description: "Review report must exist"
  - type: content_contains
    path: "{workspace}/review-{sheet_num}.md"
    pattern: "## Summary"
    description: "Report must contain summary section"
  - type: command_succeeds
    command: "grep -c 'CRITICAL\\|HIGH\\|MEDIUM\\|LOW' {workspace}/review-{sheet_num}.md"
    description: "Report must contain severity ratings"

notifications:
  - type: desktop
    on_events: [job_complete, job_failed]
  - type: slack
    on_events: [job_failed]
    config:
      webhook_url: ${SLACK_WEBHOOK_URL}
```

### Configuration Reference

#### Backend Options

| Option | Type | Description |
|--------|------|-------------|
| `type` | string | Backend type: `claude_cli`, `anthropic_api`, `ollama`, `recursive_light`, or any named instrument |
| `skip_permissions` | bool | Skip Claude CLI permission prompts (required for unattended) |
| `disable_mcp` | bool | Disable MCP servers for faster execution |
| `timeout_seconds` | int | Maximum execution time per sheet (default: 1800) |
| `allowed_tools` | list | Restrict available tools |
| `cli_model` | string | Override default model |

#### Validation Types

| Type | Required Fields | Description |
|------|-----------------|-------------|
| `file_exists` | `path` | Check that file exists |
| `file_modified` | `path` | Check that file was modified during execution |
| `content_contains` | `path`, `pattern` | Check file contains literal string |
| `content_regex` | `path`, `pattern` | Check file matches regex pattern |
| `command_succeeds` | `command` | Check command exits with code 0 |

#### Sheet Configuration

| Option | Type | Description |
|--------|------|-------------|
| `size` | int | Items per sheet |
| `total_items` | int | Total items to process |
| `start_item` | int | First item number (1-indexed) |
| `dependencies` | dict | DAG for parallel execution |

## Examples

Mozart includes examples demonstrating various use cases:

### Getting Started

| Example | Description |
|---------|-------------|
| [hello.yaml](examples/hello.yaml) | Your first score — interconnected fiction in three movements with parallel voices |

### Software Development

| Example | Description |
|---------|-------------|
| [simple-sheet.yaml](examples/simple-sheet.yaml) | Minimal configuration showing core features |
| [api-backend.yaml](examples/api-backend.yaml) | Using Anthropic API directly |
| [self-improvement.yaml](examples/self-improvement.yaml) | Incremental codebase improvement with test gates |
| [sheet-review.yaml](examples/sheet-review.yaml) | Multi-agent coordinated code review |
| [worktree-isolation.yaml](examples/worktree-isolation.yaml) | Parallel-safe execution using git worktrees |
| [observability-demo.yaml](examples/observability-demo.yaml) | Demonstration of Mozart's observability features |
| [issue-fixer.yaml](examples/issue-fixer.yaml) | GitHub issue fixing |
| [issue-solver.yaml](examples/issue-solver.yaml) | Roadmap-driven, dependency-aware issue solver |
| [fix-deferred-issues.yaml](examples/fix-deferred-issues.yaml) | Resolve long-deferred issues with zero failing tests |
| [fix-observability.yaml](examples/fix-observability.yaml) | Fix observability gaps — no silent failures |
| [cross-sheet-test.yaml](examples/cross-sheet-test.yaml) | Demonstrates cross-sheet context passing |
| [agent-spike.yaml](examples/agent-spike.yaml) | Agent experimentation |
| [docs-generator.yaml](examples/docs-generator.yaml) | Documentation generation orchestration |
| [phase3-wiring.yaml](examples/phase3-wiring.yaml) | Wires GlobalSheetScheduler into execution path |

### Quality & Continuous Improvement

| Example | Description |
|---------|-------------|
| [quality-daemon.yaml](examples/quality-daemon.yaml) | Quality improvement via daemon |
| [quality-continuous-daemon.yaml](examples/quality-continuous-daemon.yaml) | Continuous quality improvement with daemon |
| [quality-continuous.yaml](examples/quality-continuous.yaml) | Continuous quality improvement (standalone) |
| [quality-continuous-generic.yaml](examples/quality-continuous-generic.yaml) | Generic continuous quality template |

### Beyond Coding

| Example | Domain | Description |
|---------|--------|-------------|
| [systematic-literature-review.yaml](examples/systematic-literature-review.yaml) | Research | PRISMA-compliant academic literature review |
| [training-data-curation.yaml](examples/training-data-curation.yaml) | Data | Curate training data with inter-annotator agreement |
| [nonfiction-book.yaml](examples/nonfiction-book.yaml) | Writing | Book manuscript using Snowflake Method |
| [strategic-plan.yaml](examples/strategic-plan.yaml) | Planning | Strategic plan with multi-framework analysis |
| [parallel-research.yaml](examples/parallel-research.yaml) | Research | Parallel source collection with synthesis |
| [parallel-research-fanout.yaml](examples/parallel-research-fanout.yaml) | Research | Parallel research using fan-out |

For creative and experimental scores exploring fan-out patterns in philosophy, worldbuilding, and education, see the [Mozart Score Playspace](https://github.com/Mzzkc/mozart-score-playspace).

See [examples/README.md](examples/README.md) for detailed documentation of each example.

## Architecture

```
                              +-------------------+
                              |   YAML Config     |
                              +--------+----------+
                                       |
                                       v
+------------------+          +--------+----------+          +------------------+
|   CLI (Typer)    +--------->|  Execution Runner +--------->|  Backend         |
+--------+---------+          +--------+----------+          |  (Claude CLI /   |
         |                             |                     |   Anthropic API /|
         v (required)                  v                     |   Ollama)        |
+--------+---------+          +--------+----------+          +------------------+
|  Conductor      |          |  State Manager    |
|  Job Manager     |          |  (JSON/SQLite)    |
|  Rate Coordinator|          +--------+----------+
|  Backpressure    |                   |
+--------+---------+                   v
         |                    +--------+----------+
         v                    |  Validation       |
+--------+---------+          |  (5 types)        |
|  Dashboard / MCP |          +--------+----------+
|  (FastAPI)       |                   |
+------------------+                   v
                              +--------+----------+
                              |  Learning System  |
                              |  (Patterns/Store) |
                              +-------------------+
```

### Key Concepts

- **Sheet**: An atomic unit of work with its own prompt, execution, validation, and retry budget. Sheets are numbered sequentially and can define dependencies for parallel execution.

- **Validation**: Rules that determine whether a sheet completed successfully. Mozart's validation-first philosophy means exit code 0 is insufficient; validations must pass.

- **Instrument**: The execution engine that runs prompts. Mozart supports native backends (Claude CLI, Anthropic API, Ollama) and config-driven CLI instruments defined via YAML profiles. Run `mozart instruments list` to see available instruments.

- **Checkpoint**: Persistent state saved after each sheet, enabling resume from any point. Atomic writes prevent corruption on interruption.

- **Pattern**: A learned association between context and outcome, used to improve future executions through the learning system.

## Documentation

- [Getting Started](docs/getting-started.md) - Step-by-step introduction
- [CLI Reference](docs/cli-reference.md) - Complete command documentation
- [Daemon Guide](docs/daemon-guide.md) - Daemon setup, configuration, and troubleshooting
- [Score Writing Guide](docs/score-writing-guide.md) - How to author Mozart scores
- [Configuration Reference](docs/configuration-reference.md) - Every config field documented
- [Known Limitations](docs/limitations.md) - What doesn't work and workarounds
- [MCP Integration](docs/MCP-INTEGRATION.md) - Model Context Protocol server for tool integration
- [Examples](examples/) - Working configurations for various use cases
- [Score Playspace](https://github.com/Mzzkc/mozart-score-playspace) - Creative showcase: philosophy, worldbuilding, education, and more

To browse documentation locally:

```bash
pip install -e ".[docs]"
mkdocs serve
```

## Development

### Setup

```bash
git clone https://github.com/Mzzkc/mozart-ai-compose.git
cd mozart-ai-compose
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Running Tests

```bash
pytest tests/
```

### Type Checking

```bash
mypy src/
```

### Linting

```bash
ruff check src/
```

### Code Style

- All functions have type hints
- Pydantic v2 for all models
- Async throughout for I/O operations
- Protocol-based abstractions for swappability

## License

Dual licensed under AGPL-3.0 (open source) or Commercial license. See [LICENSE](LICENSE) for details.

---

Mozart orchestrates the complexity so you can focus on the work.
