# Changelog

All notable changes to Mozart AI Compose will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

#### Daemon Mode (mozartd) — 2026-02-11
- **Daemon service** (`mozartd start/stop/status`) — Long-running process that manages multiple concurrent jobs
- **IPC layer** — Unix domain socket with JSON-RPC 2.0 protocol for client-daemon communication
- **Job manager** — Tracks job lifecycle, handles submission/cancellation
- **Resource monitor** — Tracks CPU/memory/process usage with configurable limits
- **Health checks** — Liveness/readiness probes for daemon health monitoring
- **Cross-job scheduler** — GlobalSheetScheduler for sheet-level coordination across jobs (built and tested, not yet wired)
- **Rate limit coordinator** — Shares rate limit state across concurrent jobs (built and tested, not yet wired)
- **Backpressure controller** — Adaptive load management to prevent resource exhaustion; gates job submission based on memory pressure
- **Learning hub** — Centralizes pattern learning across all daemon-managed jobs
- **`mozart config`** — New command with subcommands: `show`, `set`, `path`, `init` for daemon configuration management
- **`mozart list`** — Now routes through daemon; shows active jobs by default, `--all` for everything, `--limit` defaults to 20
- **Systemd integration** — Service file and installation scripts for production deployment
- **Dashboard wiring** — Dashboard routes through daemon when available
- **MCP wiring** — MCP server routes through daemon for coordinated execution

#### MCP Server — 2026-01-24
- **`mozart mcp`** — Start MCP (Model Context Protocol) server for external AI agent integration
- **Job management tools** — Run, status, pause, resume, cancel via MCP
- **Artifact browsing** — List and read workspace files through MCP
- **Log streaming** — Access job logs through MCP tools
- **Quality scoring** — Validate and generate quality scores through MCP
- **Configuration resources** — Expose job configs as MCP resources

#### Dashboard Enhancements — 2026-01-16 to 2026-02-12
- **Job control endpoints** — POST endpoints for start, pause, resume, cancel, delete jobs
- **Sheet details** — GET endpoint for individual sheet status, logs, validation, costs, tokens
- **SSE streaming** — Real-time job status and log streaming via Server-Sent Events
- **Log endpoints** — Static download, info metadata, follow mode for log files
- **Artifact management** — Secure file listing and reading within workspaces
- **Template system** — Browse, filter, download, and use configuration templates
- **Config validation** — Three-phase YAML validation (syntax, schema, extended checks)
- **Daemon status** — Dashboard can detect and report daemon health
- **Authentication system** — Three modes: `disabled`, `api_key`, `localhost_only` (via `MOZART_AUTH_MODE`)

#### Fan-Out & Parallel Execution — 2026-02-09
- **Parameterized stage instantiation** — Eliminates manual sheet duplication for parallel workflows
- **Fan-out/fan-in patterns** — Automatic variable instantiation across parallel stages
- **Sheet dependency DAG** — Directed acyclic graph for sheet ordering and parallel execution

#### Self-Healing & Enhanced Validation — 2026-01-05 to 2026-01-15
- **`--self-healing` flag** — Automatic diagnosis and remediation when retries are exhausted
- **`--yes` flag** — Auto-confirm suggested fixes with self-healing
- **Enhanced `mozart validate`** — Comprehensive pre-execution checks beyond schema validation (V001-V103 codes)
- **Built-in remedies** — Automatic workspace creation, path fixes, suggested Jinja fixes, diagnostic guidance
- **Staged validation** — Fail-fast behavior for ordered validation checks
- **Compound conditions** — Support for `and` conditions in validation rules

#### Pause/Modify Workflow — 2026-01-13
- **`mozart pause`** — Gracefully pause running jobs at next sheet boundary
- **`mozart modify`** — Combine pause + config update + optional resume in one command
- **Auto-reload config on resume** — Config automatically reloads from the original YAML file on resume (cached snapshot is fallback). Use `--no-reload` for deterministic replay from cache

#### Learning System Enhancements — 2025-12-27 to 2026-02-04
- **Pattern broadcasting** — Automatic pattern sharing across jobs with auto-retirement
- **Metacognitive reflection** (`patterns-why`) — Analyze WHY patterns succeed, not just that they do
- **Shannon entropy monitoring** (`patterns-entropy`) — Detect pattern population collapse
- **Exploration budget** (`patterns-budget`) — Entropy-driven budget adjustments to maintain diversity
- **Epistemic drift detection** (`learning-epistemic-drift`) — Track belief/confidence changes as leading indicators
- **Effectiveness drift** (`learning-drift`) — Compare pattern performance across time windows
- **Activity monitoring** (`learning-activity`) — Recent pattern applications and learning events
- **Entropy response** (`entropy-status`) — Automatic budget boosts and quarantine revisits on low entropy
- **Learning insights** (`learning-insights`) — Actionable patterns from stdout/stderr analysis
- **Trust scoring** — Patterns have trust levels affecting application probability
- **Quarantine system** — Low-performing patterns quarantined and periodically reevaluated
- **Escalation feedback loop** — Human feedback integrated into pattern learning
- **Output pattern extraction** — Automatic pattern discovery from execution output

#### Worktree Isolation — 2026-01-15
- **Git worktree isolation** — Each job runs in a detached worktree for safe parallel execution
- **Automatic cleanup** — Worktrees cleaned up on success, preserved on failure for debugging
- **`isolation` config section** — Opt-in via YAML configuration

#### Additional Commands — Various dates
- **`mozart errors`** — Color-coded error listing grouped by sheet (red=permanent, yellow=transient, blue=rate limit)
- **`mozart diagnose`** — Comprehensive diagnostic reports with `--include-logs` for inline log content
- **`mozart history`** — SQLite-based execution history with sheet/attempt filtering
- **`mozart recover`** — Re-validate failed sheets without re-executing them
- **`--fresh` flag** — Delete existing state for clean re-runs and self-chaining jobs
- **`--watch` mode** — Continuous status monitoring with configurable refresh interval
- **`--escalation` flag** — Human-in-the-loop escalation for low-confidence sheets

#### Backends
- **Ollama backend** — Local model execution via Ollama

#### CLI Improvements — 2026-02-04 to 2026-02-14
- **CLI modularization** — Single `cli.py` file restructured into `cli/` package with submodules
- **Runner modularization** — Single `runner.py` restructured into `runner/` package (9 modules)
- **Learning store modularization** — Single `global_store.py` restructured into `store/` package (14 modules)
- **Config modularization** — Single `config.py` restructured into `config/` package (6 modules)
- **Error modularization** — Single `errors.py` restructured into `errors/` package (5 modules)
- **26+ registered commands** — Up from 7 in v0.1.0
- **Default active-only listing** — `mozart list` shows only active jobs by default

#### Examples
- **`docs-generator.yaml`** — Documentation generation orchestration
- **`quality-daemon.yaml`** — Quality improvement via daemon
- **`quality-continuous-daemon.yaml`** — Continuous quality improvement with daemon
- **`quality-continuous.yaml`** — Backlog-driven quality improvement (18 sheets, fan-out + tool agents)
- **`quality-continuous-generic.yaml`** — Language-agnostic quality improvement (16 sheets, fan-out)
- **`parallel-research.yaml`** — Multi-source parallel research
- **`parallel-research-fanout.yaml`** — Fan-out parameterized parallel stages
- **`issue-fixer.yaml`** — GitHub issue fixing workflow
- **`agent-spike.yaml`** — Agent exploration and experimentation
- **`observability-demo.yaml`** — Logging, error tracking, and diagnostics demo
- **`cross-sheet-test.yaml`** — Cross-sheet context testing

#### Non-Coding Domain Examples — 2025-12-25
- **`systematic-literature-review.yaml`** — PRISMA-compliant dual-reviewer research (8 sheets, 17 validations)
- **`training-data-curation.yaml`** — ML dataset creation with inter-annotator agreement (7 sheets, 24 validations)
- **`nonfiction-book.yaml`** — Non-fiction book authoring via Snowflake Method (8 sheets, 31 validations)
- **`strategic-plan.yaml`** — Multi-framework strategic planning (8 sheets, 39 validations)

#### Observability & Reliability
- **Cross-sheet context** — Multi-phase workflows can share context between sheets
- **Hook logging** — Post-success hooks with structured logging
- **Execution history** — SQLite-backed attempt tracking
- **Process management** — Reusable ProcessManager, orphaned process cleanup
- **Circuit breaker** — Async circuit breaker for failure isolation

### Changed
- **"Batch" → "Sheet" terminology** — All config keys, template variables, CLI flags, and internal references renamed from `batch` to `sheet`
- **`mozart list` default behavior** — Shows active jobs only (queued, running, paused); use `--all` for everything
- **`mozart list` requires daemon** — Routes through `mozartd`; use `mozart status` for direct file-based status
- **Default JSON output format** — Backends default to JSON output to prevent streaming mode errors

### Fixed
- **SIGABRT crash** — Unified streaming with parallel CancelledError guard
- **Infinite loop in parallel execution** — Fixed production bug with parallel sheet execution
- **MCP proxy test infinite loop** — Fixed test expectations for MCP proxy
- **Rate limit UTC timestamps** — Use UTC consistently for rate limit tracking
- **Orphaned Claude processes** — Kill orphaned processes on exception

---

## [0.1.0] - 2025-12-25

### Added

#### Core Features
- **YAML Configuration** - Declarative job definitions with Jinja2 templating
- **Sheet Processing** - Split work into configurable sheets with customizable execution
- **Resumable Execution** - Checkpoint-based state management for fault tolerance
- **Smart Retry** - Exponential backoff with jitter and error classification
- **Rate Limit Handling** - Automatic detection and wait with configurable thresholds

#### CLI Commands
- `mozart run` - Execute jobs with progress tracking and ETA
- `mozart status` - View detailed job status with sheet breakdown
- `mozart resume` - Continue paused or failed jobs from checkpoint
- `mozart list` - List all jobs with filtering by status
- `mozart validate` - Validate configuration files before running
- `mozart dashboard` - Start web dashboard for monitoring

#### State Management
- **JSON Backend** - File-based state storage for simplicity
- **SQLite Backend** - Database-backed state for production use
- **Config Snapshots** - Store configuration in state for resume without original file

#### Validation System
- `file_exists` - Check for expected output files
- `file_modified` - Verify file was updated during sheet
- `content_contains` - Match patterns in file content
- `command_succeeds` - Execute shell commands as quality checks
- Confidence scoring for validation results

#### Backends
- **Claude CLI Backend** - Execute via Claude CLI with full options
- **Anthropic API Backend** - Direct API integration with model selection

#### Dashboard
- FastAPI-based REST API
- Job listing with status filtering
- Detailed job view with sheet information
- Health check endpoint
- OpenAPI/Swagger documentation
- CORS support for frontend development

#### Notifications
- Desktop notifications via system notify
- Slack webhook integration
- Generic webhook support
- Configurable event triggers (job_complete, job_failed, sheet_failed)

#### User Experience
- **Progress Bar** - Real-time progress with ETA during execution
- **Graceful Shutdown** - Ctrl+C saves state and shows resume command
- **Run Summary** - Comprehensive summary panel at job completion
- **Verbosity Control** - `--verbose` and `--quiet` flags
- **JSON Output** - Machine-readable output for scripting

#### Developer Experience
- Type hints throughout with mypy validation
- Comprehensive test suite with pytest
- Linting with ruff
- Pydantic v2 for configuration and state models
- Protocol-based backends for extensibility

### Architecture

```
mozart/
├── core/           # Domain models, config, errors
├── backends/       # Claude CLI, API, and Ollama backends
├── execution/      # Runner, validation, retry logic
├── state/          # JSON and SQLite state backends
├── prompts/        # Jinja2 templating
├── notifications/  # Desktop, Slack, webhook
├── dashboard/      # FastAPI web interface + auth
├── learning/       # Pattern learning and analysis
├── daemon/         # mozartd service (IPC, scheduling, monitoring)
├── healing/        # Self-healing and diagnostic remedies
├── validation/     # Enhanced pre-execution validation
├── isolation/      # Worktree isolation for parallel jobs
├── bridge/         # MCP proxy and integration
└── mcp/            # MCP server implementation
```

---

*Mozart AI Compose - Orchestration for the AI Age*
