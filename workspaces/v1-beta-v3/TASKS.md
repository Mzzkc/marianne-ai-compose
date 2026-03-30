# Mozart v1 Beta — Task Registry

All work is coordinated through this file. Claim tasks by writing your name in brackets.
Mark done with `[x]`. See FINDINGS.md for discovered issues. See composer-notes.yaml for binding directives.

Format: `- [ ] Task description (priority: P0/P1/P2/P3) [source: roadmap step N / investigation / issue #N]`

---

## CRITICAL: --conductor-clone (BLOCKS ALL TESTING)

This is the highest priority task. You are running inside a live conductor. You cannot test against it.

- [ ] Implement --conductor-clone as a global CLI option (priority: P0) [source: composer directive, movement 1]
- [x] [Ghost] Full accounting of ALL mozart CLI commands — which ones interact with the daemon? (priority: P0) — Audit at movement-2/cli-daemon-audit.md: 20 commands, 14 interact with daemon, 10 IPC methods, 3 direct DaemonClient sites, implementation recommendations
- [ ] Each daemon-interacting command gains --conductor-clone support (priority: P0)
- [ ] Clone conductor uses isolated socket (/tmp/mozart-clone.sock), PID (/tmp/mozart-clone.pid), state DB, logs (priority: P0)
- [ ] Clone inherits production daemon config unless overridden (priority: P0)
- [ ] Support named clones: --conductor-clone=name (priority: P1)
- [ ] Convert ALL pytests that touch the daemon to use --conductor-clone or appropriate mocking (priority: P0)
- [ ] Audit CLI UX during the full command accounting — document improvement opportunities (priority: P1)
- [ ] Update CLI reference docs to document --conductor-clone (priority: P1)

---

## M0: Stabilization (Critical Bugs + Learning Store)

### Learning Store Remediation
- [x] [Maverick] Change min_priority default from 0.3 to 0.01 in patterns_query.py:75 (priority: P0) [source: investigation-forge / issue #101]
- [x] [Maverick] Update docstring example at base.py:219 from min_priority=0.5 to min_priority=0.01 (priority: P1) [source: investigation-forge]
- [x] [Maverick] Write regression tests: pattern with priority 0.05 returned by default query, dedup merging, instrument isolation (priority: P1) [source: tests-breakpoint]
- [x] [Forge] Extend test_learning_store_priority_and_fk.py: 4 min_priority, 3 soft-delete, 3 instrument isolation, 3 content hash tests + schema migration test (priority: P1) [source: tests-breakpoint]
- [x] [Maverick] Fix FK constraint failures in pattern feedback recording (priority: P0) [source: issue #129]
- [x] [Maverick] Write learning store schema migration for instrument_name, pattern_id, expires_at, recorded_at, source_job_hash (priority: P0) [source: issue #140]

### Critical Bugs
- [x] [Ghost] Convert recursive DFS to iterative in scheduler.py:572-593 following dag.py:192-246 pattern (priority: P0) [source: investigation-circuit / issue #113]
- [x] [Ghost] Create test_scheduler_cycle_detection.py: 15 cycle detection tests including 1500+ node chain (priority: P0) [source: tests-breakpoint]
- [x] [Ghost] Add exit_code=None handler in classify_execution Phase 2 to mark process-killed as TRANSIENT (priority: P0) [source: investigation-circuit / issue #126]
- [x] [Ghost] Create test_classify_execution_exit_none.py: 8 tests for production path exit_code=None scenarios (priority: P0) [source: tests-breakpoint]
- [x] [Harper] Extend stale detection to FAILED jobs in lifecycle.py:438 (not just COMPLETED) (priority: P1) [source: investigation-harper / issue #103]
- [x] [Harper] Extend test_harper_bug_fixes.py: 4 stale state tests for FAILED jobs (priority: P1) [source: tests-breakpoint]
- [x] [Ghost] Fix test_raises_if_already_running on WSL/containers (PID 1 PermissionError) (priority: P2) [source: issue #91] — already fixed (os.kill mocked)
- [x] [Ghost] Fix test_concurrent_job_crash_isolation timeout with max_concurrent_jobs=2 (priority: P2) [source: issue #90] — already fixed (polling-based)

### Intelligence Verification
- [x] [Blueprint] Fix falsy value rejection in spec/loader.py:201,207 (`if not name` → `if name is None`) (priority: P2) [source: investigation-blueprint / F-002]
- [x] [Blueprint] Create test_spec_loader.py: 47 tests (happy path, error handling, adversarial, F-002 regression, integration) (priority: P2) [source: investigation-blueprint]
- [x] [Blueprint] Create test_spec_pipeline.py: 29 integration tests for spec corpus pipeline (priority: P2) [source: tests-litmus]
- [x] [Maverick] Add 9 edge case tests to test_tokens.py (null bytes, CJK, bool/float/bytes) (priority: P2) [source: investigation-foundation]
- [x] [Maverick] Document CJK text underestimation limitation in estimate_tokens docstring (priority: P3) [source: investigation-foundation / F-001]

### Dead Code & Technical Debt
- [x] [Ghost] Remove 13 dead code entities found by Flowspec (priority: P2) [source: issue #134]
- [x] [Blueprint] Wire or remove 7 unwired code clusters (priority: P2) [source: issue #135]
- [x] [Blueprint] Refactor 3 circular dependency design smells (priority: P3) [source: issue #136]

---

## M1: Foundation (Instrument Plugin System + Sheet-First Architecture)

### Instrument Plugin System — CRITICAL PATH
- [x] [Canyon] Create InstrumentProfile + ModelCapacity data models in core/config/instruments.py (priority: P0) [source: roadmap step 1]
- [x] [Canyon] Create CliProfile and sub-models (CliCommand, CliOutputConfig, CliErrorConfig) (priority: P0) [source: roadmap step 2]
- [x] [Canyon] Implement JSON path extractor utility (~50 lines) (priority: P0) [source: roadmap step 3]
- [x] [Harper] Implement profile YAML loading from ~/.mozart/instruments/ and .mozart/instruments/ (priority: P0) [source: roadmap step 4]
- [x] [Forge] Implement PluginCliBackend (generic CLI execution from profile) (priority: P0) [source: roadmap step 5]
- [x] [Harper] Add instrument resolution in JobConfig (`instrument:` field, coexist with `backend:`) (priority: P0) [source: roadmap step 6]
- [x] [Foundation] Create native instrument bridge (register existing 4 backends as named instruments) (priority: P0) [source: roadmap step 7]
- [x] [Maverick] Ship built-in profiles: claude-code, gemini-cli, codex-cli, cline-cli, aider, goose (priority: P0) [source: roadmap step 8]

### Sheet-First Architecture
- [x] [Canyon] Create Sheet entity model in core/sheet.py (priority: P0) [source: roadmap step 9]
- [x] [Canyon] Add template variable aliases (movement, voice, voice_count) (priority: P0) [source: roadmap step 10]
- [x] [Canyon] Add SheetState + CheckpointState field additions (instrument_name, movement, voice) (priority: P0) [source: roadmap step 11]
- [x] [Foundation] Implement Sheet construction from existing config (same behavior, new representation) (priority: P0) [source: roadmap step 12]
- [x] [Harper] Create YAML alias validators (instrument: for backend:, instrument_config: coexistence) (priority: P1) [source: roadmap step 13]

### Safety Baseline
- [x] [Maverick] Implement output scanning for credential patterns in stdout_tail/stderr_tail (priority: P0) [source: roadmap step 14 / F-003]
- [x] [Maverick] Add cost tracking visibility in status output (always, even when limits disabled) (priority: P1) [source: roadmap step 15]
- [x] [Blueprint] Audit and validate all CLI input surfaces (priority: P1) [source: roadmap step 16]
- [x] [Ghost] Add shlex.quote() to skip_when_command workspace expansion in lifecycle.py:878 (priority: P1) [source: F-004]

---

## M2: The Baton — CRITICAL PATH

- [x] [Circuit] Define BatonEvent types as dataclasses (priority: P0) [source: roadmap step 17]
- [x] [Foundation] Implement timer wheel (schedule/cancel/fire) (priority: P0) [source: roadmap step 18]
- [x] [Foundation] Implement baton state model + persistence (SQLite in mozart-state.db) (priority: P0) [source: roadmap step 19]
- [x] [Foundation] Implement event inbox + main loop (priority: P0) [source: roadmap step 20]
- [x] [Foundation] Implement sheet registry (register/deregister jobs) (priority: P0) [source: roadmap step 21]
- [x] [Maverick] Commit single-attempt musician (_sheet_task) — mateship pickup of untracked work (priority: P0) [source: roadmap step 22]
- [x] [Foundation] Implement conductor's retry state machine (priority: P0) [source: roadmap step 23]
- [x] [Circuit] Implement dispatch logic (ready resolution, iterative DAG, concurrency) (priority: P0) [source: roadmap step 24]
- [x] [Circuit] Implement rate limit handling (instrument-level, timer-based) (priority: P0) [source: roadmap step 25 / issue #100]
- [x] [Circuit] Implement failure evaluation: completion mode, cost enforcement, instrument state tracking (priority: P0) [source: roadmap step 26]
- [x] [Maverick] Implement BackendPool (acquire/release) (priority: P0) [source: roadmap step 27]
- [x] [Foundation, Canyon] Wire baton into conductor (replace monolithic execution) (priority: P0) [source: roadmap step 28] — Foundation built BatonAdapter module (775 lines, 39 tests, abbbeac). Canyon added completion signaling (wait_for_completion, _check_completions), wired use_baton feature flag in manager.py (_run_job_task routing, start() initialization), 8 additional TDD tests (47 total). Remaining: full prompt assembly (PromptBuilder), CheckpointState sync, concert support.
- [ ] Implement restart recovery (reconcile baton-state + CheckpointState) (priority: P0) [source: roadmap step 29 / issue #111]
- [x] [Circuit] Reconcile dual SheetExecutionState (core.py vs state.py) before baton ships (priority: P1) [source: F-017]
- [x] [Axiom] Fix musician-baton validation_pass_rate contract + dependency failure propagation + escalation unpause bug (priority: P1) [source: F-018, invariant analysis]
- [x] [Breakpoint] Adversarial tests for baton infrastructure (65 tests: retry state machine, circuit breaker, dispatch, serialization, timer, event safety) (priority: P1) [source: adversarial testing]
- [x] [Breakpoint] M2 adversarial tests (59 tests: exhaustion paths, cost enforcement, completion mode, failure propagation, process crash, concurrent races, serialization) (priority: P1) [source: adversarial testing, dcfaf31]
- [x] [Tempo] Configurable preflight token thresholds (PreflightConfig in DaemonConfig) (priority: P1) [source: investigation — committed as mateship pickup, F-019 resolved]
- [x] [Theorem] Property-based tests for movement 2 features — 27 new tests (59→86) proving 10 new invariants: completion mode, F-018 guard, cost enforcement, exhaustion decision tree, rate limit cross-job isolation, dispatch config correctness, record_attempt F-055, retry delay monotonicity, process crash routing, auth failure terminality (priority: P1) [source: invariant analysis, movement 2]
- [x] [Axiom] Fix 3 M2 baton state machine bugs: infinite retry on 0% validation (F-065), escalation unpause ignoring FERMATA sheets (F-066), escalation overriding cost-enforcement pause (F-067). 10 TDD tests. (priority: P1) [source: backward-tracing invariant analysis, movement 2]

---

## M3: UX & Polish

- [x] [Circuit] Implement `mozart status` no-args mode (priority: P0) [source: roadmap step 30 / issue #114]
- [x] [Dash] Implement movement-grouped status display (priority: P0) [source: roadmap step 31]
- [x] [Ghost] Implement `mozart doctor` (priority: P1) [source: roadmap step 32 / F-006]
- [x] [Harper] Implement `mozart init` + starter score (priority: P1) [source: roadmap step 33] — mateship pickup of Lens's untracked init_cmd.py. Harper added: name validation (path traversal, spaces, dots, null bytes), --json output mode, doctor mention in next steps, instrument terminology in comments. Extracted shared load_all_profiles() from duplicated doctor/instruments code. 35 tests.
- [x] [Harper] Implement `mozart instruments list|check` (priority: P1) [source: roadmap step 34]
- [x] [Dash, Lens, Harper, Ghost, Forge, Maverick] Standardize error messages (raw console.print → output_error) (priority: P1) [source: roadmap step 35] — COMPLETE. 71 output_error() calls across 15 files. All raw error/warning console.print calls migrated to output_error(). Remaining console.print calls are status displays (green success, diagnostic results), not errors. Contributors: Compass (status/diagnose/recover), Forge (run.py), Ghost (pause/recover/helpers), Dash (status/cancel/validate/resume/config_cmd), Harper (diagnose/_patterns/instruments), Maverick (M3: _entropy.py dominant pattern warning). Display labels like "Recent Errors" correctly stay as rich console output.
- [x] [Circuit] Large score summary view (50+ sheets) (priority: P1) [source: roadmap step 36 / issue #114 / F-038]
- [x] [Circuit] First-run cost warning (priority: P1) [source: roadmap step 37 / F-005]
- [x] [Guide] Create examples/hello.yaml — 3-movement interconnected fiction with parallel voices, solarpunk setting, colophon (priority: P0) [source: composer notes]
- [x] [Harper] Add rich_help_panel grouping to CLI commands (priority: P2) [source: investigation-lens]
- [x] [Circuit] Add "Run mozart diagnose" suggestion on job failure (priority: P2) [source: investigation-lens]
- [x] [Compass] Fix README Quick Start (F-026 P0, F-034 P3, F-036 P3) — removed --workspace, updated instruments/doctor CLI, fixed terminology (priority: P0) [source: F-026, F-034, F-036]
- [x] [Compass] Fix getting-started.md validate output + troubleshooting (F-035 P3) (priority: P3) [source: F-035]
- [x] [Compass] Fix "Score not found" dead-end errors (F-030 P2) — migrated to output_error() with hints in status/diagnose/recover (priority: P2) [source: F-030]
- [x] [Compass] Fix empty config crash (F-028 P1) — guard in JobConfig.from_yaml + from_yaml_string (priority: P1) [source: F-028]
- [x] [Blueprint] Write prompt assembly characterization tests (51 tests) — D-003 (priority: P1) [source: North directive D-003]

---

## M4: Multi-Instrument & Demo — CRITICAL PATH

- [x] [Blueprint] Per-sheet instrument assignment (sheets.N.instrument) (priority: P0) [source: roadmap step 38] — InstrumentDef + per_sheet_instruments/per_sheet_instrument_config on SheetConfig, resolution chain in build_sheets(), 33 TDD + 2 property-based tests
- [x] [Blueprint] Score-level instruments: named profiles (priority: P0) [source: roadmap step 39] — InstrumentDef model, instruments: dict on JobConfig, YAML parsing validated
- [x] [Blueprint] sheet.instrument_map for batch assignment (priority: P1) [source: roadmap step 40] — instrument_map on SheetConfig with duplicate sheet validation, integrated into resolution chain
- [x] [Blueprint] movements: YAML key (priority: P0) [source: roadmap step 41] — MovementDef model, movements: dict on JobConfig with validators, movement-level instrument resolution in build_sheets()
- [ ] Cron scheduling (priority: P1) [source: roadmap step 42 / issue #67]
- [ ] Lovable demo score (priority: P0) [source: roadmap step 43]
- [ ] [Guide] Documentation: getting started, score writing, instrument guide, migration (priority: P0) [source: roadmap step 44] — Guide M1: updated getting-started.md, score-writing-guide.md, configuration-reference.md, README.md with instrument terminology + template variable aliases + hello.yaml references. Guide M2: added instrument_config section to score-writing-guide.md, migrated score-writing-guide code samples to instrument: syntax, updated examples/README.md with 15 missing examples. Remaining: instrument migration guide, full docs review pass.
- [ ] Update score-authoring skill: fix 4 incorrect values, add per-sheet overrides + fan-out aliases + non-Claude backends + instrument/spec sections. Keep it tight — authoring guide, not config reference. (priority: P1) [source: F-078]
- [ ] Audit and clean examples/ — remove or update outdated scores, ensure all use current patterns and features. Fix any docs that reference renamed/moved/superseded scores. (priority: P0) [source: composer notes — docs as UX] — Guide M2: migrated all 7 remaining backend: examples to instrument:, updated examples/README.md with 15 missing entries, filed F-088 (4 examples with hardcoded absolute paths need cleanup or move to scores-internal/). Full audit still needed: path cleanup, pattern modernization.
- [ ] Document all undocumented score features (grounding hooks, per-sheet overrides, spec corpus, instrument config, etc.) before using them in examples. No guessing. (priority: P0) [source: composer notes — understand before using]
- [ ] Adapt Rosetta proof score (immune-cascade) and 2-3 other corpus patterns into clean public examples in examples/. Use named patterns from the corpus, clean paths, good comments. (priority: P1) [source: F-079 / composer notes]
- [ ] Update the Rosetta Score's primitives list and proof criteria to reflect current Mozart capabilities (instruments, spec corpus, grounding, new features). (priority: P1) [source: composer notes — Rosetta as capability factory]
- [ ] Audit existing examples/ scores against updated score-authoring skill — upgrade to use new features (fan-out aliases, per-sheet overrides, instrument terminology) where they make better examples. (priority: P2) [source: F-079 / composer notes]
- [ ] Wordware comparison demos (3-4 use cases) (priority: P1) [source: composer notes]

---

## M5: Hardening

- [ ] Workspace path validation fixes (priority: P1) [source: roadmap step 45 / issue #95]
- [ ] Command injection prevention (priority: P1) [source: roadmap step 46]
- [ ] Credential env filtering for PluginCliBackend (priority: P1) [source: roadmap step 47]
- [ ] Config reload fixes (#98, #96, #131) (priority: P1) [source: roadmap step 48]
- [ ] Fan-out edge cases (#120, #119, #128) (priority: P1) [source: roadmap step 49]
- [ ] Resume improvements (#93, #103, #122) (priority: P1) [source: roadmap step 50]
- [ ] Remaining critical bug fixes (priority: P1) [source: roadmap step 51]

---

## M6: Infrastructure & Platform

- [ ] Unified Schema Management System (priority: P1) [source: composer notes]
- [ ] Conductor state persistence (#111) (priority: P0) [source: issue #111]
- [ ] Pause/resume rework (#59) (priority: P1) [source: issue #59]
- [ ] Flight checks: pre/in/post-flight (#62) (priority: P2) [source: issue #62]
- [ ] Proactive self-healing (#63) (priority: P2) [source: issue #63]
- [ ] Cascade failure recovery (#133) (priority: P2) [source: issue #133]
- [ ] Stop warn when jobs running (#94) (priority: P2) [source: issue #94]
- [ ] Non-blocking stop, superseding start (#115) (priority: P2) [source: issue #115]

---

## M7: Experience & Ecosystem

- [ ] Musical theming refresh (#54) (priority: P2) [source: issue #54]
- [ ] Named sheets with descriptors (#130) (priority: P2) [source: issue #130]
- [ ] More instruments: Qwen, OpenClaw, Ollama (#83, #84, #85) (priority: P2) [source: issues]
- [ ] Tool/MCP management for non-Claude (#66) (priority: P2) [source: issue #66]
- [ ] Score generation / mozart compose (#57) (priority: P2) [source: issue #57]
- [ ] Dashboard conducting surface (#56) (priority: P2) [source: issue #56]
- [ ] Various validation improvements (#104, #106, #132, #137, #138) (priority: P3) [source: issues]
- [ ] JobConfig.to_yaml() (#110) (priority: P3) [source: issue #110]
- [ ] Job registry ID mismatch (#124) (priority: P2) [source: issue #124]
- [ ] Fan-out integration tests (#121) (priority: P2) [source: issue #121]
- [ ] CLI stale state feedback (#139) (priority: P2) [source: issue #139]

---

## Deferred (v1.1+)

- [ ] HTTP instrument backends [source: roadmap]
- [ ] Abstract technique/capability mapping [source: roadmap]
- [ ] Code-mode techniques implementation [source: roadmap]
- [ ] Distributed execution (#52) [source: issue #52]
- [ ] Full HITL (#64) [source: issue #64]
