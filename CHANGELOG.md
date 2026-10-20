# Changelog

All notable changes to Marianne AI Compose will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Score-language release note (2026-10-15)

The score language is the product's type system: a key's name, its validator
message, its doc line and its skill line are one contract. This release makes
the language say what it does. Nothing you have written stops working; some
of it now warns so you can move before the next release removes the alias.

**Three timers, three keys — read this first.** Confusing them is how work gets
lost. `instrument_config.timeout_seconds` bounds one sheet's execution and is
typed at all four scopes (root, `movements.<N>`, `sheet.per_sheet_instrument_config.<N>`,
`instruments.<alias>.config`); a misspelling at any of them is a V010/V011
warning. `stale_detection.idle_timeout_seconds` is the opt-in no-output timer.
`schedule.max_wall_seconds` is the window of ONE scheduled tick of a recurring
score. A one-shot score has no absolute deadline: its envelope is the per-sheet
timeouts, idle detection and the conductor's `job_timeout_seconds`, which counts
accumulated RUNNING time only (pauses and downtime are free).

**Behaviour changes** (each also listed under *Changed*):
- Root `max_wall_seconds` no longer caps a one-shot score. It was the tick window
  mislabelled as a job budget (GH #435). Write `schedule.max_wall_seconds` for a
  recurring score; the root spelling keeps working for one release and warns V328.
- `retry.completion_threshold_percent` is a strict gate: a pass rate must exceed it
  (default 50) to enter completion mode. Older releases sent any partial pass to
  completion mode; review scores that lean on the default.
- `cost_limits.max_cost_per_sheet` is enforced on every attempt, including a
  successful one that takes the sheet over its cap.

**Renamed spellings (one-release aliases, V328):** `checkpoints:` → `approvals:`;
inside an `on_success`/`on_failure` hook, `on_failure` → `if_hook_fails` (at the
trigger level `on_failure` keeps its own meaning). The root `conductor:` block is
deprecated identity metadata with no reader; conductor settings live in
`~/.marianne/conductor.yaml`.

**Keys that were accepted and ignored now say so.** `mzt validate` emits V327 for
a reserved section the runtime does not read (`rate_limit`, `circuit_breaker`,
`logging`, `bridge`, `learning.exploration_budget`, `learning.entropy_response`,
`learning.auto_apply`): remove the key rather than suppress the warning. A
readable default-off section (`parallel`, `stale_detection`, `isolation`,
`code_execution`, `judgment`, `concert`) with settings but no `enabled:` warns
V329 this release; from the next release those settings enable their section.
Write `enabled: true` now, or explicit `enabled: false` to keep it off.

**New checks you may see:** V010 curated typo hints now fire at any depth (an
unknown `timeout` beside a trigger action names `instrument_config.timeout_seconds`);
V301/V302 technique paths and MCP instrument references; the language gate
(`tests/scripts/check_language.py`, baseline only shrinks) runs in CI so these
four layers cannot drift apart again.

**For instrument hygiene:** `mzt doctor` now lists user- and venue-layer profiles
that no submitted score names and no class chain includes (GH #443). A seat is
an alias over an existing profile; archive what nothing names.

### Added

#### Daemon Mode (marianned) — 2026-02-11
- **Daemon service** (`marianned start/stop/status`) — Long-running conductor process that manages multiple concurrent score runs
- **IPC layer** — Unix domain socket with JSON-RPC 2.0 protocol for client-daemon communication
- **Score-run manager** — Tracks conductor runtime records and handles submission/cancellation
- **Resource monitor** — Tracks CPU/memory/process usage with configurable limits
- **Health checks** — Liveness/readiness probes for daemon health monitoring
- **Cross-score scheduler** — GlobalSheetScheduler for sheet-level coordination across score runs (built and tested, not yet wired)
- **Rate limit coordinator** — Shares rate limit state across concurrent score runs (built and tested, not yet wired)
- **Backpressure controller** — Adaptive load management to prevent resource exhaustion; gates score submission based on memory pressure
- **Learning hub** — Centralizes pattern learning across all daemon-managed score runs
- **`mzt config`** — New command with subcommands: `show`, `set`, `path`, `init` for daemon configuration management
- **`mzt list`** — Now routes through daemon; shows active score runs by default, `--all` for everything, `--limit` defaults to 20
- **Systemd integration** — Service file and installation scripts for production deployment
- **Dashboard wiring** — Dashboard routes through daemon when available
- **MCP wiring** — MCP server routes through daemon for coordinated execution

#### MCP Server — 2026-01-24
- **`mzt mcp`** — Start MCP (Model Context Protocol) server for external AI agent integration
- **Score-run tools** — Run, status, pause, resume, cancel via MCP
- **Artifact browsing** — List and read workspace files through MCP
- **Log streaming** — Access score-run logs through MCP tools
- **Validation resources** — Expose validation and score-authoring guidance through MCP
- **Configuration resources** — Expose score configs as MCP resources

#### Dashboard Enhancements — 2026-01-16 to 2026-02-12
- **Score-run control endpoints** — POST endpoints for start, pause, resume, cancel, delete conductor records
- **Sheet details** — GET endpoint for individual sheet status, logs, validation, costs, tokens
- **SSE streaming** — Real-time score-run status and log streaming via Server-Sent Events
- **Log endpoints** — Static download, info metadata, follow mode for log files
- **Artifact management** — Secure file listing and reading within workspaces
- **Template system** — Browse, filter, download, and use configuration templates
- **Config validation** — Three-phase YAML validation (syntax, schema, extended checks)
- **Daemon status** — Dashboard can detect and report daemon health
- **Authentication system** — Three modes: `disabled`, `api_key`, `localhost_only` (via `MZT_AUTH_MODE`)

#### Fan-Out & Parallel Execution — 2026-02-09
- **Parameterized stage instantiation** — Eliminates manual sheet duplication for parallel workflows
- **Fan-out/fan-in patterns** — Automatic variable instantiation across parallel stages
- **Sheet dependency DAG** — Directed acyclic graph for sheet ordering and parallel execution

#### Self-Healing & Enhanced Validation — 2026-01-05 to 2026-01-15
- **`--self-healing` flag** — Automatic diagnosis and remediation when retries are exhausted
- **`--yes` flag** — Auto-confirm suggested fixes with self-healing
- **Enhanced `mzt validate`** — Comprehensive pre-execution checks beyond schema validation (V001-V103 codes)
- **Built-in remedies** — Automatic workspace creation, path fixes, suggested Jinja fixes, diagnostic guidance
- **Staged validation** — Fail-fast behavior for ordered validation checks
- **Compound conditions** — Support for `and` conditions in validation rules

#### Pause/Modify Workflow — 2026-01-13
- **`mzt pause`** — Gracefully pause running score runs at next sheet boundary
- **`mzt modify`** — Combine pause + config update + optional resume in one command
- **Auto-reload config on resume** — Config automatically reloads from the original YAML file on resume (cached snapshot is fallback). Use `--no-reload` for deterministic replay from cache

#### Learning System Enhancements — 2025-12-27 to 2026-02-04
- **Pattern broadcasting** — Automatic pattern sharing across score runs with auto-retirement
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
- **`mzt errors`** — Color-coded error listing grouped by sheet (red=permanent, yellow=transient, blue=rate limit)
- **`mzt diagnose`** — Comprehensive diagnostic reports with `--include-logs` for inline log content
- **`mzt history`** — SQLite-based execution history with sheet/attempt filtering
- **`mzt recover`** — Re-validate failed sheets without re-executing them
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
- **Default active-only listing** — `mzt list` shows only active score runs by default

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
- **`mzt doctor` instrument hygiene (GH #443)** — Reports every user- and venue-layer profile that no score the conductor has been given names and no class chain includes, as an "Instrument hygiene" section and `unreferenced_profiles` in `--json`; never deleted by `--clean`. A seat is an alias over an existing profile; a profile nothing names is archived by a person

### Changed
- **Shipped examples route through capability classes (T6)** — 20 example scores that pinned `instrument: claude-code` with no model pin now say `instrument: strong`, so they resolve per machine through the class-map layers (`~/.marianne/classes.yaml`, `.marianne/classes.yaml`, packaged default) instead of failing where one CLI is absent. Kept on a profile by design: `advanced/instrument-showcase.yaml` (its subject is the profiles), `engineering/lovable-generator.yaml` (root model pin, V323), and `finance/24x7-trader/*` (per-sheet model pins). `docs/getting-started.md` and `docs/README.md` show the same form.
- **"Batch" → "Sheet" terminology** — All config keys, template variables, CLI flags, and internal references renamed from `batch` to `sheet`
- **`mzt list` default behavior** — Shows active score runs only (queued, running, paused); use `--all` for everything
- **`mzt list` requires daemon** — Routes through `marianned`; use `mzt status` for direct file-based status
- **Default JSON output format** — Backends default to JSON output to prevent streaming mode errors
- **Completion mode honors `retry.completion_threshold_percent`** — A result must exceed the threshold (default 50) to enter completion mode; results at or below it spend the ordinary retry budget, and a value of 100 never enters completion mode
- **`cost_limits.max_cost_per_sheet` is enforced** — The baton arms the per-sheet cap at registration and recovery; an attempt that takes a sheet above its cap fails the sheet, including an otherwise successful attempt
- **Root `max_wall_seconds` no longer caps a one-shot score** — It is now the scheduled-tick window `schedule.max_wall_seconds` (the root spelling warns V328 for one release); the research generator no longer emits it for one-shot scores, whose sheet timeouts remain in force

### Fixed
- **Instrument descendants die with the attempt (Linux instrument supervisor)** — A CLI or interactive instrument now runs under `marianne.utils.instrument_supervisor`, a child subreaper in the instrument's own process group: a grandchild whose parent exits (an agent's `bfs`/`ugrep` search helper, a disowned shell) re-parents to the supervisor and is ended and reaped when the instrument exits or the attempt is torn down, instead of re-parenting to init and running for hours. New `cli.command.supervise` (default `true`) and `cli.command.io_priority` (default `low`, so an agent's whole-disk search no longer stalls the conductor's state reads). The backend's own descendant reaper (GH #406) remains the guard where the subreaper is unavailable.
- **IPC server stop no longer hangs on Python 3.12 (GH #442)** — `Server.wait_closed()` waits for accepted connections since 3.12, so `DaemonServer.stop()` now cancels parked handlers before awaiting the listener; every IPC lifecycle test that hit the 30 s timeout on 3.12 passes
- **Class vocabulary lives in the packaged map (GH #433)** — `instruments/classes/default.yaml` now declares the vocabulary; `image`, `video` and `high-context` ship with chains; adding a packaged class is a YAML edit. User and venue layers were always open to any name and remain so
- **Completion snapshots follow the state database (GH #432)** — New `snapshot_dir` on the conductor config, defaulting to `snapshots/` beside `state_db_path`; a conductor clone gets `clone-<name>-state-snapshots/`. Every test or clone that isolates its state DB now isolates its snapshots instead of writing into `~/.marianne/snapshots` (5,010 job dirs on one machine)
- **run action settle no longer ends a reader of its log (GH #449)** — The file-holder capture source admits only processes holding the action log open for writing (the detached escapee's inherited stdout/stderr); an operator's `tail -f` is a bystander and is left alone
- **V010 curated hint names the surface it sits in (GH #451)** — A bare `timeout:` inside an alias `config`, a movement's or sheet's `instrument_config`, or a `run:` trigger action now suggests that surface's `timeout_seconds` instead of the root key, so a hint never widens one seat's or one action's bound to the whole score
- **V228 fires only for routes that cannot report cost (GH #453)** — A priced CLI route (token paths + model price) makes `cost_limit_usd` bind; the warning now names the route and why it is unpriced (raw shell, no token paths, no models, $0 price, or a pinned model the profile does not price)
- **Concert trigger self-chain (GH #454)** — A `concert` trigger that names its own score is admitted under a lineage id (`<name>--chain<depth>--<stamp>`) instead of being refused as an active duplicate of its still-running parent; `concert.max_chain_depth` is the only bound
- **recover_job keeps a fallen-back terminal sheet's instrument name (GH #452)** — Recovery no longer rewrites a COMPLETED/SKIPPED/CANCELLED sheet's `instrument_name` to the primary while keeping the fallback's model; status and diagnose name the instrument that actually ran
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
- `mzt run` - Execute jobs with progress tracking and ETA
- `mzt status` - View detailed job status with sheet breakdown
- `mzt resume` - Continue paused or failed jobs from checkpoint
- `mzt list` - List all jobs with filtering by status
- `mzt validate` - Validate configuration files before running
- `mzt dashboard` - Start web dashboard for monitoring

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
marianne/
├── core/           # Domain models, config, errors
├── backends/       # Claude CLI, API, and Ollama backends
├── execution/      # Runner, validation, retry logic
├── state/          # JSON and SQLite state backends
├── prompts/        # Jinja2 templating
├── notifications/  # Desktop, Slack, webhook
├── dashboard/      # FastAPI web interface + auth
├── learning/       # Pattern learning and analysis
├── daemon/         # marianned service (IPC, scheduling, monitoring)
├── healing/        # Self-healing and diagnostic remedies
├── validation/     # Enhanced pre-execution validation
├── isolation/      # Worktree isolation for parallel jobs
├── bridge/         # MCP proxy and integration
└── mcp/            # MCP server implementation
```

---

*Marianne AI Compose - Orchestration for the AI Age*
