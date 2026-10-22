# Changelog

All notable changes to Marianne AI Compose will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- CLI instrument environments now default to system essentials and explicitly
  declared `required_env` / profile `env` variables (GH #335). Interactive tmux
  launches use the same policy and replace the server environment. Custom
  profiles relying on ambient keys must declare them in `cli.command.required_env`.
  Temporary `cli.command.inherit_env: true` restores full inheritance only when
  `required_env` is null; doctor warns about this opt-out. File-based credentials
  and daemon hook/MCP subprocess isolation remain separate work.

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
The configuration reference now carries that mark on every one of the 60
score keys the runtime does not read (the language checker's L08 list, the
audit GH #353 asked for), `grounding:`, `ai_review:` and `feedback:` are
introduced as reserved sections, the getting-started, score-writing and
troubleshooting guides stop teaching `rate_limit.wait_minutes` as the way to
configure waits (detection is the instrument profile's
`rate_limit_patterns`), and no shipped example sets a reserved key any more.

**New checks you may see:** V010 curated typo hints now fire at any depth (an
unknown `timeout` beside a trigger action names `instrument_config.timeout_seconds`);
V301/V302 technique paths and MCP instrument references; the language gate
(`tests/scripts/check_language.py`, baseline only shrinks) runs in CI so these
four layers cannot drift apart again.

**For instrument hygiene:** `mzt doctor` now lists user- and venue-layer profiles
that no submitted score names and no class chain includes (GH #443). A seat is
an alias over an existing profile; archive what nothing names.

### Added

- **V332: a CLI schema whose top level the API refuses is named before the run (GH #486).** V311 proves the profile can enforce a schema, V331 that the sheet runs headless; V332 proves the schema's top level is one the CLI's tool `input_schema` accepts — no top-level `oneOf`/`allOf`/`anyOf`, a top-level `type`. Found live: a discriminated-union intake schema cost a 400 at sheet 1 the job could not quote.

- **Lazy-acceptance gate over the example scores (GH #480).** `tests/scripts/check_lazy_acceptance.py` synthesises, per example sheet, the fixture a lazy musician would write — the files `file_exists` names, the exact `content_contains` text, a literal the `content_regex` matches, plus filler — and runs the real `ValidationEngine`; a sheet whose rules all pass has acceptance that headings-plus-filler satisfies. `tests/fixtures/lazy_acceptance_baseline.json` holds today's 132 of 689 sheets (patterns 46, product 20, creative 19, research 18, engineering 15); the test fails on a new row and asks the fixture to shrink when a row disappears. This productises the QA voices' probes as the one gate that reads the score file.

- **Example READMEs are checked against their scores (GH #481).** `tests/scripts/check_examples_readme.py` (and its test, run in CI) fails on any relative link in an examples README that does not resolve and on any score row whose Sheets column differs from the score's concrete sheet count. First run: 13 dead Rosetta links (repointed at `scores/rosetta-corpus/`) and 5 wrong sheet counts (quality-triage 15, issue-solver 13, saas-app-builder 8, lovable-generator 11, research-agent 9), all fixed. A gate that reads the score file beats a rule in prose.

- **V331: a schema sheet that would run interactively is named before the run (GH #478).** `response_format` on a profile that is interactive by default (claude-code, antigravity) with no `interactive: false` passed `mzt validate` (V311 judges the profile's `json_schema_flag`) and was refused at sheet 1 ("cannot enforce instrument_config.response_format"). V331 builds the sheets, resolves each one's instrument, and warns per instrument and movement with the fix. Docs and the score-authoring skill state the rule.

- **A sheet that writes another sheet's declared artifact is named (GH #386).**
  The producer signal is the existing proxy — a sheet-gated `file_exists`
  validation (what V108/V109 already read; it now lives in
  `core/producers.py`). Each sheet carries the artifacts non-ancestor sheets
  are declared to produce; the musician snapshots them around execution and
  an attempt that created or modified one reports an `artifact:` warning,
  folded into the sheet's preflight warnings for `mzt status` and
  `mzt diagnose`. Advisory only: the owning sheet still has to write the
  file, and nothing is reverted.

- **Alias chains with `select` modifiers (GH #473).** A score alias may bind
  an ordered `chain` of entries instead of one profile; `select` decides which
  entry each sheet starts on and the rest follow as that sheet's fallbacks.
  `cycle` rotates per use, `spread` rotates per movement (a fan-out's voices
  get distinct models from one alias), `cheapest` and `widest` order by the
  profile's model table, and `balance` lets the conductor start on the
  least-loaded entry at dispatch (recorded in the fallback history; the
  chosen entry's whole config travels with it). Modifiers
  compose in the order written. V330 warns when a modifier has nothing to
  choose between. Single-profile aliases are unchanged. The example scores
  bind instruments per role instead of one alias for the whole score:
  issue-solver and quality-continuous put `fast`/`strong`/`workhorse`/
  `writing` on the movements that play those roles and a `reviewer` chain
  with `[spread, balance]` on their review fan-outs; design-review,
  thinking-lab and research-agent spread their review, perspective and
  investigation voices across distinct models the same way; the rosetta
  discover/prove scores collapse three reviewer aliases into one.

- **`mzt instruments check NAME --live --record DIR` and captured CLI
  fixtures.** `--record` writes each live call's raw bytes, exit code and CLI
  version, so parser tests replay what the installed CLI prints instead of a
  fixture written from its docs (GH #472). `tests/fixtures/cli_captures/`
  holds Claude Code 2.1.306 output (Claude model and GLM-5.3 route, plain and
  schema mode), reduced to the events the parser reads. The first recording
  caught a real case: on the GLM-5.3 route Claude Code sometimes fails to call
  its StructuredOutput tool (1 of 3 runs), and the parser correctly refuses
  the prose answer.

- **Language gate L13 `reserved-taught`.** A doc, skill or shipped example
  score that sets a key the schema marks Reserved or Deprecated now fails the
  score-language gate (GH #472). It found 31 such settings across the
  configuration reference, the score-writing, concert, conductor and
  learning guides, the plugin refs and four plugin example scores, all
  fixed here: hook-level `detached` and `inherit_learning` (a `run_job` hook
  never waits; there is one global learning store), `concert_log_path`,
  `warn_at_percent`, `branch_prefix`, the deprecated `conductor:` section,
  and reserved learning sub-blocks. Blocks that show reserved syntax on
  purpose now say so in the line above them.

- **`mzt instruments check NAME --live` and `mzt doctor --live`.** A profile
  could validate, its binary sit on PATH, and every dispatch still fail:
  a flag the installed CLI rejects (crush `--yolo`, since April), an
  output shape the parser cannot read (Claude Code's verbose event array),
  a provider that is not configured. `--live` sends one real prompt through
  the conductor's own backend factory (plus a schema-mode prompt when the
  profile declares `json_schema_flag`) and reports what a sheet would see,
  classified by the baton's own classifier. First run on Pallas: claude-code
  plain and schema ok; codex-cli, opencode, opencode-glm-5.3-flash, goose,
  antigravity ok; crush rate-limited by its free provider; gemini-cli
  unavailable (#469).

- **`mzt run --require-lock LOCK` (GH #403).** An opt-in admission gate: the
  conductor verifies the composition lock the release gate wrote
  (`check_score_release.py --write-lock`) against the exact score bytes it is
  about to parse, and refuses a missing, stale, unreadable or malformed lock
  with that reason. Unlocked submission is unchanged. Also: the output hub
  strips terminal control sequences before redaction and fan-out, so an
  interactive transcript cannot drive a watcher's terminal and a credential a
  TUI redraws around cursor moves is still redacted.

- **Orchestra view (GH #468).** `mzt dashboard` serves `/orchestra`: every score
  on stage, each sheet a cell coloured by state, the instrument it plays on, a
  beat log, live over SSE, with a replay transport for any recorded day. The
  conductor's `OrchestraRecorder` projects EventBus events into a public-safe
  vocabulary (score, sheet, instrument, outcome, time; nothing else is read),
  keeps the fleet snapshot, appends beats to `~/.marianne/orchestra/<day>.jsonl`,
  and serves `daemon.orchestra.snapshot|replay|stream` over IPC. The baton
  adapter now publishes `sheet.dispatched`. `orchestra:` daemon config. A copy
  of the 20 Oct proof day ships as `docs/orchestra/2026-10-20.jsonl`.
- **Per-job scratch root (GH #460)** — Every CLI and interactive instrument now receives `MARIANNE_SCRATCH=<workspace>/.marianne-scratch`; the conductor deletes that directory when the job reaches COMPLETED or FAILED (`workspace_lifecycle.scratch: remove|keep`, default `remove`). Probe clones, mutants and worktrees belong there instead of a system temp directory, where 112 orphaned clones had accumulated on one box.

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
- **`mzt stop` is non-blocking; `mzt start` supersedes a draining conductor (GH #115)** — After the active-job safety check and any confirmation, `mzt stop` sends the signal and returns without waiting for the conductor to exit. A new `--wait` option opts into a bounded exit wait: `--wait=` for the default 30s budget, `--wait=SECONDS` for a custom one (`--wait=0` keeps signal-and-return; budgets must be finite and non-negative; an exceeded budget exits 1 naming the still-draining PID). A graceful drain can still take up to `shutdown_timeout_seconds` (default 300s). `mzt start` no longer reports a still-draining conductor as "already running": it probes the shutdown state, waits up to 30s for the old process to exit, then starts a fresh conductor — never a second one beside the dying process, which holds the PID-file flock and socket lock; a drain that exceeds 30s exits 1. A silent probe is treated as running (GH #455: slow is not dead), and the serving-conductor exit-0 contract (GH #459) is unchanged. `mzt restart` now sequences through the same bounded stop wait (`stop_conductor(wait=30.0)`) instead of a separate `wait_for_conductor_exit` call.
- **Shipped examples route through capability classes (T6)** — 20 example scores that pinned `instrument: claude-code` with no model pin now say `instrument: strong`, so they resolve per machine through the class-map layers (`~/.marianne/classes.yaml`, `.marianne/classes.yaml`, packaged default) instead of failing where one CLI is absent. Kept on a profile by design: `advanced/instrument-showcase.yaml` (its subject is the profiles), `engineering/lovable-generator.yaml` (root model pin, V323), and `finance/24x7-trader/*` (per-sheet model pins). `docs/getting-started.md` and `docs/README.md` show the same form.
- **"Batch" → "Sheet" terminology** — All config keys, template variables, CLI flags, and internal references renamed from `batch` to `sheet`
- **`mzt list` default behavior** — Shows active score runs only (queued, running, paused); use `--all` for everything
- **`mzt list` requires daemon** — Routes through `marianned`; use `mzt status` for direct file-based status
- **Default JSON output format** — Backends default to JSON output to prevent streaming mode errors
- **Completion mode honors `retry.completion_threshold_percent`** — A result must exceed the threshold (default 50) to enter completion mode; results at or below it spend the ordinary retry budget, and a value of 100 never enters completion mode
- **`cost_limits.max_cost_per_sheet` is enforced** — The baton arms the per-sheet cap at registration and recovery; an attempt that takes a sheet above its cap fails the sheet, including an otherwise successful attempt
- **Root `max_wall_seconds` no longer caps a one-shot score** — It is now the scheduled-tick window `schedule.max_wall_seconds` (the root spelling warns V328 for one release); the research generator no longer emits it for one-shot scores, whose sheet timeouts remain in force

### Fixed

- **A self-chain survives `mzt resume --config <copy>`, and a refused chain is logged.** The concert trigger decided "this is a self-chain" by comparing the child's path with the parent's `config_path`; after a resume from a copy the parent's path is the copy, so the child resolved to a sibling file, derived the parent's own job id, and was refused as an active duplicate — the 13-sheet issue-solver finished green and chained nothing, with no log line. Identity is now the job id the child would get; a refused submission logs `flow.concert.refused` with the reason, an accepted one `flow.concert.submitted`, both reach the observer stream (`baton.flow.concert_refused` / `concert_submitted`), and the orchestra view shows a refused chain with its reason.

- **A dropped root block warns, not whispers.** A top-level key the schema does not know whose value is a mapping or list (`deliver: {1: {to: …}}` from an unmerged branch) was V011 INFO — hidden unless `mzt validate --verbose` — so a product learned at runtime that nothing had written its output file. It is now V010 WARNING naming the block; a stray scalar stays INFO.

- **Schema sheets on claude-code no longer hang under `--effort max`.** Claude Code 2.1.307 with `--effort max` and `--json-schema` emits nothing until the sheet timeout kills it (0 bytes at 150 s with a real 8.8 KB intake schema; `--effort high` answers in 23 s). CLI profiles gain `cli.command.schema_extra_flags` — flags a schema sheet gets *instead of* `extra_flags` (unset inherits, `[]` drops them) — and the shipped `claude-code` profile sends `--effort high` there. Found by a peer conductor's intake wall (cut4g); reproduced against the binary before the profile changed.

- **hello-local no longer needs a private instrument profile (examples QA, GH #485).** The pure-local first score bound `opencode-gemma`, a profile that existed only on the author's machine, so `mzt validate` passed for the author and `mzt run` failed for everyone else. It now pins `ollama/gemma4:26b` on the built-in `opencode` profile through a score alias; the README states the OpenCode `ollama` provider it needs.

- **Review fan-outs that claimed independence now configure it (examples QA, GH #485).** Seven examples advertised independent reviewers, expert analysts or parallel investigators while every voice resolved to one profile and model (project-roadmap's review was two aliases plus the score default; quality-triage's four personas, candidate-screening, invoice-analysis, parallel-research-fanout, worktree-isolation and source-triangulation were one class). Each fan-out now binds one alias with `chain: [codex-cli, claude-code, antigravity]` and `select: [spread, balance]`, so its voices start on three different heads with the rest of the chain as fallbacks — independence by construction, not by persona prose.

- **Example prompts that contradicted themselves or emitted broken code (examples QA, GH #484).** the-economic-injury-line's sheet-1 heredoc rendered each county as `{...` with no closing brace (a `}}` that Jinja swallowed), so `deployment.json` could never be written — every rendered Python heredoc in the examples now parses (86 scanned, rendered per sheet). research-agent's relay wrote "Primary Data Investigation Brief" while the investigator looked for "Primary Investigation Brief". issue-triage's stage 4 called an empty corpus a failure that stage 3 had just allowed. simple-sheet carried an undefined-variable trick nothing read (V101 row leaves the baseline).

- **Example inputs resolve from the score, not from the author's tree (examples QA, GH #482/#483).** The six finance scores injected their libretto and technique files through `../../../examples/finance/24x7-trader/…` relative to the workspace — one directory too far, so every one of 22 optional preludes was silently absent while the prompts said the content "has been injected"; `hello`/`hello-local`/`hello-setup` reached their shipped assets through `{{ workspace }}/../../examples/…`, which breaks the moment a user moves the workspace as the README tells them to. All 29 now anchor on `{{ score_dir }}` (`{score_dir}` in validations) and every score-shipped injection resolves. The author's GitHub identity is no longer a silent default: `github_owner: "Mzzkc"` in nine rosetta scores and `repo: "Mzzkc/marianne-ai-compose"` in issue-solver are `[CHANGE THIS]` placeholders the scores already refuse to run on.

- **A failed CLI attempt keeps the API's refusal as its witness (GH #486).** When Claude Code's terminal JSON reports `is_error` with an `api_error_status` (a 400 for a tool `input_schema` the API rejects: a top-level `oneOf`, a missing top-level `type`), the backend kept neither the message nor a stderr tail, so the job could only say E009 after 5 s. The message is now the attempt's error and its stderr tail, typed `api_error`; a 4xx other than 429 classifies as E301 (the request's fault, not retried as weather), 429/5xx stay transient. Recorded as a capture fixture and replayed through the real parser.

- **`mzt run --dry-run` honours `--var` (examples QA, 21 Oct).** The preview rendered the YAML's placeholder values (`[CHANGE THIS]`) whatever `--var` said, so the advertised safe verification path verified nothing; the runtime-variable merge now lives in `core/config/job.py` and both the conductor's submit/resume seam and the preview apply it. V106 also warns when a `content_contains` pattern carries regex syntax (a literal no prose contains); V201 also scans validation commands for Jinja.

- **A schema with `$schema` no longer breaks Claude Code schema mode (GH #479).** `claude -p --json-schema` exits 1 on a schema whose top level carries `$schema` (it tries to resolve the draft URL); the CLI backend now drops top-level `$schema` and `$id` before building the argument, and the `instruments check --live` probe schema carries a `$schema` so the probe fails the way a sheet would if the strip regressed.

- **`mzt validate` no longer re-parses every instrument profile a dozen times.** The profile loader parsed each YAML with PyYAML's pure-Python parser (~35 ms per profile) and every validator that asked for profiles loaded them all again, so a host with 50 personal profiles paid ~20 s per validate and the suite's validate-driven tests tripped their 30 s timeouts under load (the #308 solver's final verification failed its conductor-side suite three times on exactly those). The loader uses the C SafeLoader (same semantics) and memoises each file on (path, mtime, size): the same bytes parse once per process, an edited profile is re-read, hot reload and `instruments check` are unchanged. simple-sheet validate: 24 s → 0.8 s on that host.

- **Example validation gates that could never pass, or could never fail (examples QA, 21 Oct).** Twelve folded `command: >` scalars (dead-letter-quarantine ×6, echelon-repair ×3, prefabrication ×2, instrument-showcase) collapsed multi-line `python3 -c` bodies into one line with a leading space, so a correct artifact failed with `IndentationError`; they are literal blocks now and every body parses. `lovable-generator`'s build gate and `quality-continuous`'s test gate returned `tail`'s status, so a failing build or suite passed — `set -o pipefail`. `quality-continuous`'s commit gate was `… && echo passed || echo 'no recent commit'`, true on both branches and in no repository — it now runs in the workspace and exits non-zero without a recent commit. `codebase-rewrite`'s unit/integration gates accepted a `**Results:** FAIL` report; each tier now runs the bound test command in `project_root` (`bash -c {test_command}` — the placeholder is one shell-quoted word, GH #477) and requires the report's verdict to match the harness's exit, so a PASS over a red suite fails while an honest FAIL passes on to the review, and a FAIL unit report skips integration — the fail-fast the header promised. Its triage gate accepts an empty triage (every candidate SKIP) so a converged codebase can reach the gate. The corpus baseline shrank by the twelve V303 rows.

- **V105 accepts a declared loop index as a validation placeholder (GH #475).** A validation path scoped to a loop (`{pass_no}`, the documented form) failed `mzt validate` with "undefined variable" while the baton defined it at runtime; the check now seeds every `sheet.loops.<span>.index` as the V101 side already did (#448).

- **Fewer instruments see every credential (GH #335).** The `opencode` builtin
  now passes no credential variables (it reads its own store) and `goose` only
  its providers' variables; both verified live. `mzt doctor` warns, by name,
  about each ready CLI profile that still inherits the full environment while
  a credential-shaped variable is set (variable names are counted, never
  printed with values).

- **Diagnosable fallback paths (GH #308, MN-007).** Profile and capability-class
  preflight failures now emit `validation.profiles_unavailable_skip` warnings
  with check identity and exception information while retaining existing
  skip/fallback results. These are log events, not new validation findings.
  Previously silent runtime fallbacks now report their failures, including
  instrument metadata, judgment context, dashboard data, profiler storage,
  backend initialization, output decoding, and schema type hints; optional
  probes log at DEBUG. Ruff BLE001 and an AST regression guard enforce reviewed
  broad exception boundaries. The validation corpus fixture now exercises the
  real profile loader and records reviewed diagnostics; timestamp tests no
  longer swallow assertions, and dashboard CLI mocks restore only their own
  module entry so later route tests retain their injected state.

- **Pausing then stopping no longer costs five minutes.** Graceful shutdown
  waited on every job task, and a PAUSED job's task waits for a resume that
  cannot come during shutdown, so `mzt stop` after `mzt pause` always ran the
  full `shutdown_timeout_seconds` (300 s) with `running_jobs: 0` and
  `mzt stop --wait=300` reported a timeout. A paused job with no musician in
  flight is now cancelled at once and stays PAUSED; one with a musician still
  running is waited on as before.
- **Structured output through Claude Code works, and validate catches the
  case that cannot.** The `claude-code` builtin declares
  `json_schema_flag: --json-schema` (verified on Claude Code 2.1.306: the
  validated object arrives as the terminal result's `structured_output`). A
  CLI that prints an array of stream events for `--output-format json`
  (Claude Code with `verbose: true` in its settings) is now read from its
  last `type: result` object, in schema mode and plain mode alike; before,
  the result path came back empty. V311 now also warns when a sheet bound
  straight to a CLI profile asks for a `json_schema` `response_format` that
  profile cannot enforce, naming the sheets, instead of letting every
  dispatch be refused at run time. `opencode` has no schema flag (its
  `--format json` is a raw event stream), so it stays without one.
- **No broad `except` passes in silence any more (GH #308, first slice).**
  The 22 `except Exception: pass` sites now say why they catch broadly and
  leave a log line (DEBUG for expected teardown and best-effort fallbacks,
  WARNING where state can be lost: an unreadable scratch policy, a baton
  loop that errored at shutdown, an MCP reset that failed on a failed
  dispatch). `mzt doctor` now reports a conductor whose socket accepts but
  does not answer as running, not absent, so it stops telling an operator
  to start a second one (GH #455 rule: absence needs two proofs).
- **Dashboard pre-exposure hardening (GH #387).** The artifact read opens
  with `O_NOFOLLOW`, verifies the descriptor is the regular file the path
  named (device and inode against a fresh `lstat`), and serves its bytes;
  a symlink swapped in between the path check and the open is refused with
  400 instead of followed. `mzt dashboard --host <non-loopback>` prints a
  red **Exposed bind** panel before starting when `MZT_AUTH_MODE` is still
  `localhost_only`, or `api_key` with no keys or the localhost bypass on.
  `MZT_PUBLIC_ORCHESTRA=true` is the shape for mzt.dev: `/orchestra`, its
  read-only API and `/static` are served to every client, every other path
  keeps the auth mode's rule (the docs had promised a public orchestra page
  that the middleware then refused with 403).
- **An installed harness with no usable provider no longer stalls a deep
  chain (GH #389).** `cli.errors.unavailable_patterns` lets a profile name its
  harness's own startup refusal ("No provider configured", "Provider not
  found"); a match is typed `INSTRUMENT_UNAVAILABLE`, so the sheet advances
  its fallback chain on the first attempt without charging a retry, the same
  path a missing binary takes since #418. The shipped `goose`, `crush` and
  `opencode` profiles carry their measured refusal text. Every
  `cli.errors.*_patterns` regex is now compiled when the profile loads, so a
  bad pattern is refused there rather than mid-attempt.
- **The `crush` profile no longer sends `--yolo` (GH #471).** crush v0.98.1
  rejects the flag in every position, so every dispatch exited 1 in 60 ms
  before reaching a provider and was retried as transient. `crush run` is
  unattended on its own; the profile now sends only model, cwd and quiet.
- **Startup failures wait before retrying (GH #470).** An attempt that exits
  non-zero within `retry.startup_failure_window_seconds` (60) did no work;
  its retry now waits at least `retry.startup_failure_floor_seconds` (120)
  instead of the ordinary backoff. Two lanes burnt a three-attempt budget in
  75 s against a transiently locked opencode store. Timeouts, signals and
  rate limits are unaffected; a longer exponential backoff is unchanged.

- **Judgment counter persists on resolution (GH #382).** `judgment_count` is
  the durable `max_judgments_per_sheet` cap; it is now written to the registry
  as soon as a judgment resolves instead of riding the next state-transition
  persist, so a crash in between cannot reset it. The startup sweep's
  docstring now states that it finds nothing on the restart path by
  construction and that restart-recovered fermatas reach the judge through
  the adapter's re-emit.

- **`mzt watch` follows interactive sheets (GH #381).** The interactive driver
  tails the pipe-pane transcript once per poll and hands each new slice to the
  live output sink; the interactive backend exposes `set_output_callback`, so
  the adapter's existing wiring reaches it. Tailing starts at offset 0 because
  the transcript is truncated at session start (#398); a sink failure ends
  tailing, never the attempt. The dead `set_output_log_path` base-class
  surface is removed.

- **issue-solver: FLAGGED issues rank last in selection, and the close
  comment carries scope.** With a zero-edge DAG the selector took the lowest
  issue number regardless of the scanner verdict, so the first chained
  iteration started on a security-design issue whose body had been redacted
  to its title. Selection order is now tier, CLEAN before FLAGGED,
  dependencies, number. The automated close comment summarizes the branch
  against its merge base (an empty receipt commit's `HEAD~1` diff printed
  nothing), names the verify.sh result, and copies the synthesis report's
  Deferred section so a reader sees what the iteration did not do.

- **`mzt doctor` finishes in seconds on a long-lived host.** The instrument
  hygiene scan parses every score the conductor was ever given; the pure
  Python YAML loader took 41 s over 1,374 files and timed out every test that
  invokes doctor. It now uses the C loader (3 s) and reads the registry of the
  conductor the CLI addresses (clone-aware). Tests never read the developer's
  registry through doctor (conftest).
- **A nested instrument no longer inherits the outer job's `MARIANNE_SCRATCH`.**
  A CLI backend with no working directory now strips the variable instead of
  passing the environment through, so Marianne's own test suite run as an
  instrument of a Marianne job sees the documented "nothing exported".
- **issue-solver: one full-suite run per pass, and a loop cap a pass cannot
  exhaust.** The implementation pass ran the harness twice (Step 3 and again
  inside verify.sh); on a 14-minute suite that timed the pass out at 3600 s
  and one pass cost $16.80 against a $12 cap, so the six-pass loop was a
  single shot. Step 3 runs only the touched tests; `cost_limit_usd` is 40.

- **Concert trigger children inherit `--var` runtime variables.** A self-chaining
  score's second iteration re-read its YAML defaults and could solve the wrong
  project; the child is now submitted with the parent's runtime variables, the
  same way resume re-applies them.
- **issue-triage / issue-solver agree on one stable triage path.** Both scores
  drop their score-relative `workspace:` for the conductor-managed
  `~/workspaces/<name>` (#58); the solver's `triage_workspace` default is that
  path; every Python read of it expands `~` (a `Path("~/…").read_text()` never
  did); `project_root` must be absolute because musicians and validations run
  with the workspace as cwd; the generated harness scripts `cd` into the
  literal project root instead of `git rev-parse` from wherever they are run.

- **`mzt list` never pages out a live score.** `--limit` caps terminal history
  only; queued, running and paused runs are always all shown, and the footer
  names how many older terminal runs were hidden. A 20-row page over 25 running
  jobs had read as "0 RUNNING" for the five that fell off.

- **Busy conductor named by PID; no restart advice for a live one (GH #455).**
  Every routed read (`status`, `status JOB`, `list`, `errors`, `diagnose`,
  `history`, `watch`, `run` submission) reports a conductor whose socket did
  not answer as `Marianne conductor busy or unresponsive (PID n alive);
  running state is unknown` (`running_state: unknown`, `conductor_pid`).
  Absence needs two proofs: a missing socket, or a refused connect with no
  live PID. The `mzt restart` hint `errors`/`diagnose`/`history` attached to
  every conductor error is gone. `mzt conductor-status` prints the PID,
  whether it is alive and the host I/O pressure when the socket is silent
  (clone mode reads the clone's own PID file), and the readiness payload
  carries `event_loop_lag_ms`, `event_loop_lag_peak_ms` and `io_pressure`.
  `socket.backlog` defaults to 128 (was 5), and log rotation gzips on a
  helper thread instead of inline on the event loop.
- **A reload-declined profile is visible, and a stale schema is told apart from a broken file (GH #450)** — `ConfigReloadResult` carries `failed_profiles` (path, reason_code, reason, retained) and `schema_stale`; the loader reports `instrument_schema_stale` when a file names a key the running conductor's schema lacks (restart, do not edit). `mzt doctor` gains a "Config reload" section and `conductor.last_reload` in JSON; `mzt conductor-status` gains a "Config" block. An argv-delivered prompt past the kernel cap (E2BIG) now says so in the spawn failure instead of reading as a missing binary.
- **A busy conductor is no longer reported as absent (GH #455)** — When a conductor blocked on disk stops accepting, its AF_UNIX listen queue fills; each queued connect looks successful and the first write fails with `EINVAL`. The IPC client now classifies a connect or first-write failure on an existing socket as unresponsive (`running_state: unknown`, no `mzt start` hint); only a missing socket or a refused connection proves absence. The connection close path also no longer renders a chained traceback into a debug log (it stalled the status read itself under load). Regression: a real listener with a full backlog.
- **Instrument descendants die with the attempt (Linux instrument supervisor)** — A CLI or interactive instrument now runs under `marianne.utils.instrument_supervisor`, a child subreaper in the instrument's own process group: a grandchild whose parent exits (an agent's `bfs`/`ugrep` search helper, a disowned shell) re-parents to the supervisor and is ended and reaped when the instrument exits or the attempt is torn down, instead of re-parenting to init and running for hours. New `cli.command.supervise` (default `true`), `cli.command.io_priority` (default `low`, so an agent's whole-disk search no longer stalls the conductor's state reads) and `cli.command.max_open_files` (default 4096, a soft `RLIMIT_NOFILE` for the instrument's whole tree; GH #457). The backend's own descendant reaper (GH #406) remains the guard where the subreaper is unavailable.
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
