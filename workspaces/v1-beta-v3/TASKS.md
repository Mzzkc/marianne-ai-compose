# Mozart v1 Beta — Task Registry

All work is coordinated through this file. Claim tasks by writing your name in brackets.
Mark done with `[x]`. See FINDINGS.md for discovered issues. See composer-notes.yaml for binding directives.

Format: `- [ ] Task description (priority: P0/P1/P2/P3) [source: roadmap step N / investigation / issue #N]`

---

## CRITICAL: --conductor-clone (BLOCKS ALL TESTING)

This is the highest priority task. You are running inside a live conductor. You cannot test against it.

- [x] [Spark, unnamed musician] Implement --conductor-clone as a global CLI option (priority: P0) [source: composer directive, movement 1] — Spark: mateship pickup of unnamed musician's clone.py + detect.py + cli/__init__.py. Added clone_name param to start_conductor in process.py, wired start/restart/conductor-status with clone paths. 28 TDD tests.
- [x] [Ghost] Full accounting of ALL mozart CLI commands — which ones interact with the daemon? (priority: P0) — Audit at movement-2/cli-daemon-audit.md: 20 commands, 14 interact with daemon, 10 IPC methods, 3 direct DaemonClient sites, implementation recommendations
- [x] [Spark, unnamed musician] Each daemon-interacting command gains --conductor-clone support (priority: P0) — IPC commands route via _resolve_socket_path() clone override. Lifecycle commands (start/stop/restart/conductor-status) wired with clone PID/socket/config overrides.
- [x] [Spark, unnamed musician] Clone conductor uses isolated socket (/tmp/mozart-clone.sock), PID (/tmp/mozart-clone.pid), state DB, logs (priority: P0) — resolve_clone_paths() + build_clone_config(). start_conductor() applies clone path overrides to loaded config.
- [x] [Spark, unnamed musician] Clone inherits production daemon config unless overridden (priority: P0) — _load_config() runs normally, then clone paths applied on top via model_dump/model_validate.
- [x] [Spark, unnamed musician] Support named clones: --conductor-clone=name (priority: P1) — _sanitize_name() ensures safe file paths. Named clones produce unique paths: /tmp/mozart-clone-{name}.sock etc.
- [x] [Harper] Harden clone name sanitization: long name truncation (64 char cap), fix TYPE_CHECKING type signatures (object→DaemonConfig). 26 TDD tests in test_conductor_clone_hardening.py covering adversarial inputs, config inheritance, path isolation, built-in profile validation. (priority: P1) [source: mateship review of Spark's clone implementation]
- [x] [Ghost] Fix config_cmd.py _try_live_config() to use _resolve_socket_path() instead of hardcoded DaemonConfig().socket.path — last direct DaemonClient site bypassing clone. Completed Spark's TDD tests (TestConfigCmdCloneAwareness). (priority: P1) [source: F-090 / cli daemon audit]
- [x] [Ghost] Fix F-090: doctor.py two-phase conductor detection — PID file + IPC socket fallback. Also clone-aware via _resolve_socket_path(). 4 TDD tests. (priority: P1) [source: F-090]
- [x] [Canyon] Fix F-132: build_clone_config() doesn't override state_db_path — clone shares production registry (priority: P1) [source: F-132, Newcomer M1C7] — 1-line fix in clone.py:144. 3 TDD tests. F-132 RESOLVED.
- [x] [Harper] Fix F-122: Replace all 5 hardcoded DaemonClient socket paths with _resolve_socket_path(None). Hooks, MCP, dashboard routes, job_control, app factory. 14 TDD tests. (priority: P1) [source: F-122]
- [x] [Harper] Fix F-131: Update --conductor-clone help text to require = syntax. (priority: P3) [source: F-131]
- [x] [Ghost] Convert F-122 adversarial tests from "prove bug exists" to "prove fix holds" regression tests. All 4 now assert _resolve_socket_path IS used and hardcoded paths are NOT. (priority: P1) [source: F-122 test maintenance]
- [x] [Ghost] Fix 3 mypy errors in baton core.py — logger.debug() used event_type= kwarg instead of extra={"event_type": ...} for StaleCheck, CronTick, PacingComplete handlers. (priority: P1) [source: mypy strict]
- [x] [Ghost] Close 3 verified GitHub issues: #95 (workspace path validation), #112 (health check quota), #99 (hooks lost on restart). All fixes verified on HEAD. (priority: P1) [source: issue hygiene]
- [x] [Circuit] Fix test_register_methods_wires_rpc: add daemon.clear_rate_limits to expected IPC method set (priority: P1) [source: mateship review of Harper ae31ca8] — Harper's clear-rate-limits commit added a new IPC method but didn't update the IPC contract test.
- [ ] Convert ALL pytests that touch the daemon to use --conductor-clone or appropriate mocking (priority: P0)
- [x] [Dash] Audit CLI UX during the full command accounting — document improvement opportunities (priority: P1) — Full audit at movement-2/cli-ux-audit.md: 6 issues (2 P1, 2 P2, 2 P3), grade B+. Fixed: --job→--score in top.py/clear, clear docstring clarified, modify docstring improved, resume "job state"→"score state". Filed: learning commands domination (12/26) needs E-002 escalation for subcommand refactor.
- [x] [Ember] Fix top.py help examples and notes using --job after Dash renamed flag to --score (priority: P3) [source: F-142, mateship pickup] — 4 user-facing strings updated: help example, TUI note, history note, docstring. Commit 5093962.
- [x] [Codex] Update CLI reference docs to document --conductor-clone (priority: P1) — Already documented in M2 by previous Codex session (lines 18-55). This movement added 4 missing commands (init, cancel, clear, top), --profile on start, and conductor clones section to daemon-guide.

---

## M0: Stabilization (Critical Bugs + Learning Store)

### Learning Store Remediation
- [x] [Maverick] Change min_priority default from 0.3 to 0.01 in patterns_query.py:75 (priority: P0) [source: investigation-forge / issue #101]
- [x] [Maverick] Update docstring example at base.py:219 from min_priority=0.5 to min_priority=0.01 (priority: P1) [source: investigation-forge]
- [x] [Maverick] Write regression tests: pattern with priority 0.05 returned by default query, dedup merging, instrument isolation (priority: P1) [source: tests-breakpoint]
- [x] [Forge] Extend test_learning_store_priority_and_fk.py: 4 min_priority, 3 soft-delete, 3 instrument isolation, 3 content hash tests + schema migration test (priority: P1) [source: tests-breakpoint]
- [x] [Maverick] Fix FK constraint failures in pattern feedback recording (priority: P0) [source: issue #129]
- [x] [Maverick] Write learning store schema migration for instrument_name, pattern_id, expires_at, recorded_at, source_job_hash (priority: P0) [source: issue #140]
- [x] [Maverick] Fix F-009/F-144: Replace positional tag generation with semantic context tags in _query_relevant_patterns(). Wire instrument_name into get_patterns(). 13 TDD tests. (priority: P0) [source: D-014, F-009, F-144]

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
- [x] [Foundation, Canyon] Wire baton into conductor (replace monolithic execution) (priority: P0) [source: roadmap step 28] — Foundation built BatonAdapter module (775 lines, 39 tests, abbbeac). Canyon added completion signaling (wait_for_completion, _check_completions), wired use_baton feature flag in manager.py (_run_job_task routing, start() initialization), 8 additional TDD tests (47 total). Canyon M3b: wired PromptConfig into register_job/recover_job (F-158), fixed F-152 dispatch guard (attempt numbers from state), fixed F-145 completed_new_work in both baton paths. Remaining: CheckpointState sync, concert support.
- [x] [Maverick, unnamed musician] Implement restart recovery (reconcile baton-state + CheckpointState) (priority: P0) [source: roadmap step 29 / issue #111] — Maverick: mateship pickup of unnamed musician's implementation. adapter.recover_job() rebuilds from CheckpointState (terminal preserved, in_progress→PENDING, attempts carried forward). _sync_sheet_status() per-event sync callback in main loop. manager._recover_baton_orphans() resumes PAUSED jobs on start(). manager._resume_via_baton() full resume path. 27 TDD tests. Commit b4146a7.
- [x] [Circuit] Reconcile dual SheetExecutionState (core.py vs state.py) before baton ships (priority: P1) [source: F-017]
- [x] [Axiom] Fix musician-baton validation_pass_rate contract + dependency failure propagation + escalation unpause bug (priority: P1) [source: F-018, invariant analysis]
- [x] [Breakpoint] Adversarial tests for baton infrastructure (65 tests: retry state machine, circuit breaker, dispatch, serialization, timer, event safety) (priority: P1) [source: adversarial testing]
- [x] [Breakpoint] M2 adversarial tests (59 tests: exhaustion paths, cost enforcement, completion mode, failure propagation, process crash, concurrent races, serialization) (priority: P1) [source: adversarial testing, dcfaf31]
- [x] [Ghost] CLI command to clear stale rate limits (priority: P1) [source: F-149 / issue #153] — Mateship pickup of unnamed musician's implementation. Fixed test assertion bug (coordinator+baton sum), fixed SystemExit/click.Exit mismatch. 18 TDD tests. IPC handler `daemon.clear_rate_limits`, `RateLimitCoordinator.clear_limits()`, `BatonAdapter.clear_instrument_rate_limit()`, `JobManager.clear_rate_limits()`, CLI command `mozart clear-rate-limits [--instrument NAME] [--json]`.
- [x] [Breakpoint] M4 adversarial tests (64 tests: musician prompt rendering, error classification, clone sanitization, adapter state mapping, F-018 contract, credential redaction, injection resolution, Phase 4.5 F-098/F-097 regression, event conversion, validation formatting, cost estimation) (priority: P1) [source: adversarial testing, movement 1 cycle 2] — Found F-114 (Phase 4.5 quota gap)
- [x] [Breakpoint] M2C2 adversarial tests (63 tests: step 29 recovery+deps, state sync, credential redaction boundaries, rate limit extraction, failure propagation, cost limits, status mapping, completion signaling, instrument resolution) (priority: P1) [source: adversarial testing, movement 2 cycle 2] — Fixed 2 bugs in untracked test file, extended 47→63 tests. No new code bugs found.
- [x] [Breakpoint] M3 adversarial tests (62 tests: dispatch guard exception taxonomy, rate limit auto-resume timer scheduling, model override carryover, completed_new_work edge cases, semantic context tags format, PromptRenderer wiring, clear-rate-limits dual-path, stagger delay boundaries, terminal status invariants, dispatch callback integration, wait cap verification, record_attempt edge cases) (priority: P1) [source: adversarial testing, movement 3] — Found and fixed F-200 (clear_instrument_rate_limit fallthrough bug). 12 test classes targeting all major M3 fixes.
- [x] [Breakpoint] M3 CLI/UX adversarial tests (58 tests: schema error hints, duration formatting, rate limit display, stop safety guard, stale PID detection, validate YAML edge cases, instrument display, IPC probe, non-dict YAML guard) (priority: P1) [source: adversarial testing, movement 3 pass 2] — Zero bugs found in CLI/UX layer. Mateship pickup of uncommitted validate.py changes + 22 untracked tests + quality gate baseline update. Commit 0028fa1.
- [x] [Breakpoint] M3 BatonAdapter adversarial tests (90 tests: state mapping totality/inverse consistency, recovery edge cases, dispatch callback modes/math, state sync filtering, completion detection, observer event boundary values, deregistration cleanup, dependency extraction, sheet conversion, musician wrapper exception handling, EventBus resilience, registration edge cases, has_completed_sheets, shutdown, _on_musician_done, get_sheet) (priority: P1) [source: adversarial testing, movement 3 pass 3] — Zero bugs found. 16 test classes covering the full BatonAdapter (1206 lines, step 28 wiring). Quality gate baseline updated (1296→1327 BARE_MAGICMOCK).
- [x] [Breakpoint] M3 Pass 4 integration gap adversarial tests (48 tests: coordinator clear concurrency/edge cases, manager clear_rate_limits error paths, _read_pid/_pid_alive adversarial inputs, stale PID cleanup, resume_via_baton no_reload fallback, parallel stagger timing boundaries, F-200 regression, coordinator boundary values, IPC probe resilience, dual-path clear consistency, start_conductor race conditions) (priority: P1) [source: adversarial testing, movement 3 pass 4] — Found and fixed F-201 (clear_instrument_rate_limit empty string fallthrough — same bug class as F-200). 10 test classes targeting integration seams. Quality gate baseline updated (1327→1346 BARE_MAGICMOCK).
- [x] [Adversary] M3 Phase 1 baton adversarial tests (67 tests: dispatch failure handling, multi-job instrument sharing, recovery from corrupted checkpoint, state sync callback, completion signaling, cost limit boundaries, event ordering attacks, deregistration during execution, F-440 propagation edge cases, dispatch concurrency constraints, terminal state resistance, exhaustion decision tree, observer event conversion, auto-instrument registration) (priority: P1) [source: adversarial testing, movement 3, Phase 1 baton readiness] — Zero bugs found. All M3 fixes verified (F-152, F-145, F-158, F-200/F-201, F-440). 1358 baton tests pass. Baton recommended for Phase 1 --conductor-clone testing.
- [x] [Adversary] M1C3 adversarial tests (27 tests: F-111 rate limit error lost in parallel, F-113 failed deps as done, F-075 resume regression, F-122 IPC clone bypass, baton state edges, cross-system integration) (priority: P1) [source: adversarial testing, movement 1 cycle 3] — Found F-128 (E006 unreachable via classify_execution), F-129 (F-113 behavior changes after restart)
- [x] [Tempo] Configurable preflight token thresholds (PreflightConfig in DaemonConfig) (priority: P1) [source: investigation — committed as mateship pickup, F-019 resolved]
- [x] [Theorem] Property-based tests for movement 2 features — 27 new tests (59→86) proving 10 new invariants: completion mode, F-018 guard, cost enforcement, exhaustion decision tree, rate limit cross-job isolation, dispatch config correctness, record_attempt F-055, retry delay monotonicity, process crash routing, auth failure terminality (priority: P1) [source: invariant analysis, movement 2]
- [x] [Theorem] Property-based tests for M1C2 cross-system invariants — 44 new tests (86→130) proving 11 invariant families: adapter state mapping totality, record_attempt budget, sheet template variables, baton decision tree, terminal state resistance, error taxonomy (E006/Phase 4.5), status set consistency, prompt assembly pipeline, deregister cleanup, enum completeness, failure propagation. (priority: P1) [source: invariant analysis, movement 1 cycle 2]
- [x] [Theorem] Property-based tests for M2C2 completions — 25 new tests (136→161) proving 10 invariant families: recovery state mapping fidelity, recovery attempt count preservation, recovery dispatch readiness, clone path mutual exclusion, clone config path isolation, credential redaction totality, credential redaction idempotency, credential redaction non-credential preservation, V210 instrument name check coverage, failure propagation terminal preservation. Zero bugs found — recovery, clone isolation, and credential redaction are mathematically consistent. (priority: P1) [source: invariant analysis, movement 2 cycle 2]
- [x] [Theorem] Property-based tests for M3 features — 29 new tests (119→148) proving 15 invariant families: wait cap clamping totality (F-160), clear rate limit specificity (F-200/F-201), clear WAITING→PENDING transition, rate limit hit status transitions, observer event classification completeness, exhaustion decision tree mutual exclusion, retry delay monotonicity/bounds, state mapping round-trip stability, stagger delay Pydantic bounds (F-099), rate limit auto-resume timer scheduling (F-112), record_attempt budget accounting, F-018 no-validation guard, terminal state resistance across all handlers, dispatch failure event guarantee (F-152), clear rate limit idempotency. Zero bugs found — all M3 features are mathematically consistent. (priority: P1) [source: invariant analysis, movement 3]
- [x] [Axiom] Fix 3 M2 baton state machine bugs: infinite retry on 0% validation (F-065), escalation unpause ignoring FERMATA sheets (F-066), escalation overriding cost-enforcement pause (F-067). 10 TDD tests. (priority: P1) [source: backward-tracing invariant analysis, movement 2]
- [x] [Axiom] Fix F-143: _handle_resume_job cost limit bypass (same class as F-067). 4 TDD tests. Commit 90b8a76. (priority: P1) [source: backward-tracing invariant analysis, movement 2 cycle 2]
- [x] [Axiom] Fix F-440: State sync gap — failure propagation not synced to checkpoint, zombie resurrection on restart. Re-run propagation in register_job(). 8 TDD tests. Updated 2 adversarial tests. (priority: P1) [source: backward-tracing invariant analysis, movement 3]

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
- [x] [Circuit] Fix F-068: "Completed:" timestamp hidden for RUNNING/PAUSED jobs (priority: P2) [source: F-068] — terminal status guard at status.py:1487, 4 TDD tests
- [x] [Circuit] Fix F-069/F-092: V101 false positive on Jinja2 {% set %}/{% for %} variables (priority: P2) [source: F-069, F-092] — AST walker in jinja.py:250 extracts template-declared vars, 5 TDD tests, hello.yaml now validates clean
- [x] [Circuit] Fix F-048: cost tracking when cost limits disabled (priority: P2) [source: F-048] — _track_cost() now runs before cost_limits.enabled gate in sheet.py, 2 TDD tests
- [x] [Dash] Add --json to `mozart list` (F-071) — JSON array output for machine parsing. 5 TDD tests. (priority: P3) [source: F-071]
- [x] [Dash] Fix F-094: README Configuration Reference — renamed "Backend Options" to "Instrument Configuration", updated all fields to instrument_config syntax, updated architecture diagram, fixed prerequisites. (priority: P2) [source: F-094]
- [x] [Dash] Fix F-029 (partial): user-facing error messages say "Score ID" instead of "Job ID" in validate_job_id(). 19 test assertions updated. (priority: P2) [source: F-029]
- [x] [Journey] Fix F-115: cancel not-found uses output_error() + hints + exit 1. 5 TDD tests. (priority: P2) [source: F-115, exploratory testing]
- [x] [Lens] instruments.py JSON error path: console.print(json.dumps) → output_json() for Rich markup safety. 7 TDD regression tests for rejection hint behavior (test_rejection_hints_ux.py). Commit 4b83dae. (priority: P2) [source: error standardization]
- [x] [Newcomer] Fix F-153/F-460: "job" → "score" terminology across CLI + docs — run.py, validate.py, recover.py docstrings/help text; README.md (12 fixes); getting-started.md (10 fixes); cli-reference.md (11 fixes). ~35 total fixes across 6 files. (priority: P2) [source: F-153, F-460, fresh-eyes audit]
- [x] [Lens] Add hints to 8 hintless output_error() calls + fix raw console.print error in clear validation. Layer 2 completion: every output_error in the CLI now has actionable hints. 10 TDD tests in test_hintless_error_audit.py. Quality gate baseline 1440→1455. Commit d286e07. (priority: P2) [source: error quality audit, M4]

---

## M4: Multi-Instrument & Demo — CRITICAL PATH

- [x] [Blueprint] Per-sheet instrument assignment (sheets.N.instrument) (priority: P0) [source: roadmap step 38] — InstrumentDef + per_sheet_instruments/per_sheet_instrument_config on SheetConfig, resolution chain in build_sheets(), 33 TDD + 2 property-based tests
- [x] [Blueprint] Score-level instruments: named profiles (priority: P0) [source: roadmap step 39] — InstrumentDef model, instruments: dict on JobConfig, YAML parsing validated
- [x] [Blueprint] sheet.instrument_map for batch assignment (priority: P1) [source: roadmap step 40] — instrument_map on SheetConfig with duplicate sheet validation, integrated into resolution chain
- [x] [Blueprint] movements: YAML key (priority: P0) [source: roadmap step 41] — MovementDef model, movements: dict on JobConfig with validators, movement-level instrument resolution in build_sheets()
- [x] [Foundation] Score-level and per-sheet model override via instrument_config.model (priority: P1) [source: F-150 / issue #154] — Foundation mateship pickup: PluginCliBackend apply_overrides/clear_overrides, BackendPool release clears overrides, sheet.py movement-level config gating fix. 19 TDD tests. Commits 08c5ca4.
- [x] [Circuit] F-151 COMPLETE: Instrument name observability end-to-end (priority: P1) [source: F-151] — Legacy runner path: set `sheet_state.instrument_name` from `config.instrument` in `sheet.py:1567`. Baton path: populate from Sheet entities after `register_job()` in `manager.py:1822-1826`. Status display: Instrument column in flat table (output.py has_instruments param), instrument breakdown in summary view (50+ sheets), movement-grouped view already supported. 16 TDD tests across `test_f151_instrument_observability.py` (7) and `test_f151_status_display.py` (9). Commits 25ba278, 4a1308b.
- [ ] Cron scheduling (priority: P1) [source: roadmap step 42 / issue #67]
- [ ] Lovable demo score (priority: P0) [source: roadmap step 43]
- [ ] [Guide, Codex, Compass] Documentation: getting started, score writing, instrument guide, migration (priority: P0) [source: roadmap step 44] — Guide M1: updated getting-started.md, score-writing-guide.md, configuration-reference.md, README.md with instrument terminology + template variable aliases + hello.yaml references. Guide M2: added instrument_config section to score-writing-guide.md, migrated score-writing-guide code samples to instrument: syntax, updated examples/README.md with 15 missing examples. Codex M1 (current cycle): added spec corpus + grounding hooks sections to score-writing-guide, 4 missing CLI commands (init, cancel, clear, top) + --profile option to cli-reference, conductor clones section to daemon-guide, fixed example count in index.md. Guide M1C4: fixed getting-started.md hello.yaml output reference (HTML not md), added instrument_name to template variables reference, added 2 Rosetta proof scores + 7 creative examples to README Beyond Coding section, F-126 resolved. Codex M2: added instrument migration guide section to score-writing-guide (field mapping table, before/after examples, compatibility notes). Fixed stale -w flag on status example. Fixed V009 severity in CLI reference (ERROR not WARNING). Mateship pickup of unnamed musician's CLI reference updates (V-code expansion, init positional arg, list --json, instrument syntax) and daemon guide baton section. Updated limitations.md (baton, instruments, runner mixin). Codex M3: documented 6 new M3 features across 5 docs: clear-rate-limits command (CLI reference), stop safety guard (CLI reference), stagger_delay_ms (score-writing guide + configuration-reference), rate limit auto-resume + prompt assembly (daemon guide), instrument column in status (CLI reference), restart --profile/--pid-file options (CLI reference). Updated baton test count (1,130+) across daemon guide + limitations. Quality gate baseline fix (1227→1230). Guide M3: full terminology audit — renamed my-first-job→my-first-score in getting-started.md (7 instances), fixed validate output to match actual V205 format, added clear-rate-limits to troubleshooting, fixed 10 "job"→"score" in score-writing-guide, 6 in configuration-reference, added restart+clear-rate-limits to README Conductor code block. Commits 251f31d, e44e5b1. Remaining: none — all M3 features documented.
- [x] [Guide] Update score-authoring skill: fix 4 incorrect values, add per-sheet overrides + fan-out aliases + non-Claude backends + instrument/spec sections. Keep it tight — authoring guide, not config reference. (priority: P1) [source: F-078] — Fixed max_output_capture_bytes (10KB→50KB), added recursive_light to backend types, added instrument_name to core vars, added fan-out aliases (movement/voice/voice_count/total_movements), added Instrument section (recommended) + per-sheet instruments section. Commit 3fc7fcd (plugins submodule e5facf2).
- [ ] Audit and clean examples/ — remove or update outdated scores, ensure all use current patterns and features. Fix any docs that reference renamed/moved/superseded scores. (priority: P0) [source: composer notes — docs as UX] — Guide M2: migrated all 7 remaining backend: examples to instrument:, updated examples/README.md with 15 missing entries, filed F-088 (4 examples with hardcoded absolute paths need cleanup or move to scores-internal/). Guide M1C4: full audit — all 36 scores use instrument: at config level (instrument migration COMPLETE), 35/36 pass validation (only iterative-dev-loop-config.yaml fails — generator config not a score, F-125). Added 2 Rosetta proof scores to README, fixed iterative-dev-loop-config entry. Newcomer M2C2: fixed F-140 — broken Rosetta references in README.md and examples/README.md, added all 4 proof scores (was 2), fixed echelon-repair.yaml internal ref. Compass M2C2: fixed score-composer.yaml V108 (broken prelude path skills/→plugins/) + stale backend terminology (target_backend→target_instrument). Fixed limitations.md backend: workaround→instrument_config:. Full validation sweep: 38/39 pass (only iterative-dev-loop-config.yaml expected). All 4 rosetta scores pass. Zero hardcoded absolute paths remain. Remaining: pattern modernization (fan-out aliases, per-sheet overrides in appropriate examples).
- [x] [Codex] Document all undocumented score features (grounding hooks, per-sheet overrides, spec corpus, instrument config, etc.) before using them in examples. No guessing. (priority: P0) [source: composer notes — understand before using] — Added spec corpus section (spec_dir, include_claude_md, spec_tags per-sheet filtering) and grounding hooks section (enabled, fail_on_grounding_failure, escalate_on_failure, hooks) to score-writing-guide.md. Per-sheet overrides and instrument config already documented by Guide M2 and Blueprint M3.
- [x] [Spark] Adapt Rosetta proof score (immune-cascade) and 2-3 other corpus patterns into clean public examples in examples/. Use named patterns from the corpus, clean paths, good comments. (priority: P1) [source: F-079 / composer notes] — Created 2 new Rosetta pattern examples: examples/rosetta/source-triangulation.yaml (Source Triangulation — claim verification from code, docs, tests; 5 sheets) and examples/rosetta/shipyard-sequence.yaml (Shipyard Sequence — build with validation gate; 7 sheets). Both validate clean, use movements: key, proper workspace paths. Updated examples/README.md with new entries. Total Rosetta examples: 6 (was 4).
- [x] [Spark] Update the Rosetta Score's primitives list and proof criteria to reflect current Mozart capabilities (instruments, spec corpus, grounding, new features). (priority: P1) [source: composer notes — Rosetta as capability factory] — Updated scores/the-rosetta-score.yaml: primitives section now includes movements: YAML key, stagger_delay_ms, skip_when, fan-in skipped_upstream, cross_sheet config, grounding hooks, per-sheet instrument assignment, instrument_map, instrument_config.model, spec corpus injection. Existing vocabulary updated with corpus size (56 patterns), recent additions (iteration 4), awaiting primitives, new capabilities list, and 10 practiced patterns (was 6). Validates clean.
- [x] [Spark, Guide] Audit existing examples/ scores against updated score-authoring skill — upgrade to use new features (fan-out aliases, per-sheet overrides, instrument terminology) where they make better examples. (priority: P2) [source: F-079 / composer notes] — Spark M2: modernized dialectic.yaml and parallel-research-fanout.yaml. Spark M3: modernized 7 more fan-out examples with movements: key, movement/voice terminology in comments and template text, added parallel: enabled where missing. Updated: worldbuilder.yaml, thinking-lab.yaml, dinner-party.yaml, design-review.yaml, skill-builder.yaml, palimpsest.yaml, score-composer.yaml. Fixed V207 warnings (fan-out without parallel) in worldbuilder and palimpsest. Guide M3: completed remaining 10 scores with movements: declarations — hello.yaml, 4 Rosetta proofs (immune-cascade, dead-letter-quarantine, prefabrication, echelon-repair), context-engineering-lab, issue-solver, quality-continuous, quality-continuous-generic, quality-daemon. Total: 19/19 multi-stage examples now have movements: declarations. All 37 scores validate clean.
- [x] [Spark, Blueprint] Wordware comparison demos (3-4 use cases) (priority: P1) [source: composer notes, D-023] — Blueprint M4: created 3 demos (contract-generator.yaml, candidate-screening.yaml, marketing-content.yaml). Spark M4: created 4th demo (invoice-analysis.yaml — 3-voice parallel analysis: financial accuracy, compliance, anomaly detection; 5 sheets, 7 validations). All 4 validate clean. D-023 COMPLETE.
- [x] [Blueprint] F-116: Add V210 instrument name validation to `mozart validate` (priority: P2) [source: F-116, Journey M1C3] — InstrumentNameCheck warns on unknown instrument names. Checks score-level, per-sheet, instrument_map, movements. 15 TDD tests. Commit 327e536.
- [x] [Blueprint] F-127: Fix `_classify_success_outcome` to use cumulative `attempt_count` instead of session-local `normal_attempts` (priority: P2) [source: F-127, Ember M1C3] — Uses SheetState.attempt_count (persisted) instead of session-local counter. 7 TDD tests. Commit 327e536.
- [x] [Blueprint, Maverick] Fix `build_clone_config()` missing `state_db_path` + `log_file` overrides (priority: P1) [source: F-132] — Maverick fixed both branches in b4146a7. Blueprint added 5 clone isolation tests in 327e536.

---

## M5: Hardening

- [x] [Ghost] Workspace path validation fixes (priority: P1) [source: roadmap step 45 / issue #95] — Verified: expand_path() workspace restriction already removed. Absolute paths accepted for all validation types. Issue #95 closed with verification.
- [x] [Ghost] Command injection prevention (priority: P1) [source: roadmap step 46] — Verified: all 4 shell execution paths use shlex.quote (validation engine, skip_when_command, hooks for_shell, manager expand_hook_vars). command_succeeds uses create_subprocess_exec with bash -c. All context values quoted.
- [x] [Warden] Credential env filtering for PluginCliBackend (priority: P1) [source: roadmap step 47] — Added `required_env` field to CliCommand. When set, only declared vars + system essentials (PATH, HOME, etc.) pass to subprocess. Updated gemini-cli, claude-code, codex-cli built-in profiles with required_env. 19 TDD tests in test_credential_env_filtering.py. F-025 RESOLVED.
- [x] [Warden] Fix F-061: Update minimum versions for 3 CVE-affected dependencies (cryptography>=46.0.6, pyjwt>=2.12.0, requests>=2.33.0) (priority: P1) [source: F-061, Sentinel M2] — Added to pyproject.toml. All 3 packages upgraded. Blocks public release removed.
- [x] [Warden] Fix F-135: Credential redaction in musician exception handler (priority: P1) [source: safety audit, M2] — Applied redact_credentials() to error_msg at musician.py:156 and validation error at musician.py:552. 26 TDD tests in test_musician_error_redaction.py.
- [x] [Ghost, Forge] Config reload fixes (#98, #96, #131) (priority: P1) [source: roadmap step 48] — Ghost M3: Added no_reload to baton path. Forge M3: Completed IPC pipeline threading — no_reload now forwarded from CLI params → process.py handle_resume → manager.resume_job → _resume_job_task → service.resume_job → _reconstruct_config. 8 TDD tests in test_resume_no_reload_ipc.py (CLI params, manager forwarding, service behavior, cost reset regression for #96). #96 confirmed working via CONFIG_STATE_MAPPING reconciliation. All 3 issues addressed.
- [x] [Forge] Fan-out stagger launches (F-099) (priority: P2) [source: composer-assigned] — Added stagger_delay_ms field to ParallelConfig (0-5000ms, default 0) and ParallelExecutionConfig. ParallelExecutor.execute_batch() applies asyncio.sleep between sheet launches. Wired through base.py runner initialization. 10 TDD tests in test_fan_out_stagger.py. Config example: `parallel: { stagger_delay_ms: 150 }`.
- [x] [Warden] Fix F-160: Rate limit wait_seconds upper bound (priority: P2) [source: safety audit, M3] — parse_reset_time() had no max cap; adversarial "resets in 999999 hours" → 114-year timer. Added RESET_TIME_MAXIMUM_WAIT_SECONDS=86400 (24h) to constants.py, _clamp_wait() to classifier.py (3 return paths). 10 TDD tests in test_rate_limit_wait_cap.py.
- [x] [Warden] Quality gate baseline fix: BARE_MAGICMOCK 1230→1234 (priority: P2) [source: mateship pickup, F-350] — 4 new bare MagicMock from test_stale_state_feedback.py and test_top_error_ux.py.
- [x] [Warden] M3 safety audit: model override, clear-rate-limits, auto-resume timer, PID cleanup, stagger validation, semantic tags, credential redaction (priority: P2) [source: movement 3 safety review] — 9 areas audited, 1 gap found (F-160), rest clean. Detailed findings in movement-3/warden.md.
- [x] [Maverick] Fix #120: Fan-in [SKIPPED] placeholder + skipped_upstream template var. #128 already fixed in 919125e. #119 still open. (priority: P1) [source: roadmap step 49]
- [x] [Harper] Fix F-450: IPC MethodNotFoundError misreported as "conductor not running" (priority: P2) [source: F-450, F-181, F-462] — Added MethodNotFoundError(DaemonError) exception. Mapped METHOD_NOT_FOUND (-32601) in _CODE_EXCEPTION_MAP. try_daemon_route() re-raises with restart guidance. run.py catches DaemonError. Fixed _MockMixin for #93 pause-during-retry. 15 TDD tests in test_f450_method_not_found.py. Updated test_daemon_cli_detection.py + test_daemon_ipc_client.py.
- [x] [Harper] Mateship: commit D-024 cost accuracy fixes (Circuit) — ClaudeCliBackend JSON token extraction, status cost confidence display, quality gate baseline update (1391→1396). 17 tests in test_cost_accuracy.py.
- [x] [Harper] Mateship: commit #93 pause-during-retry fix + fix broken test_sheet_execution — _check_pause_signal + _handle_pause_request protocol stubs in sheet.py, _MockMixin updated. 5 tests in test_pause_during_retry.py.
- [x] [Forge] Mateship: commit Harper's uncommitted #93, F-450, and D-024 work. Quality gate baseline update (1396→1440). Fix #122: skip await_early_failure for conductor-routed resumes + enhanced direct resume panel. Updated test_cli_run_resume.py to remove stale await_early_failure patch. 7 TDD tests in test_resume_output_clarity.py. (priority: P1) [source: mateship, M4]
- [x] [Ghost] Fix #103: Auto-detect changed score file on re-run (priority: P1) [source: issue #103] — Added `_should_auto_fresh()` to manager.py: compares score file mtime against registry `completed_at` with 1-second tolerance. Wired into `submit_job()` — auto-sets `fresh=True` when COMPLETED job's score was modified since last run. Enhanced job_service.py resume event with `previous_error` and `config_reloaded` context. 7 TDD tests in test_stale_completed_detection.py.
- [x] Resume improvements (#93, #103, #122) (priority: P1) [source: roadmap step 50] — #93 fixed (pause during retry, Harper+Forge mateship). #122 fixed (Forge M4: skip early failure poll for conductor-routed resumes + enhanced direct resume panel with previous state context, 7 TDD tests). #103 fixed (Ghost M4: auto-fresh detection via mtime comparison).
- [ ] Remaining critical bug fixes (priority: P1) [source: roadmap step 51]

---

## M6: Infrastructure & Platform

- [ ] Unified Schema Management System (priority: P1) [source: composer notes]
- [ ] Conductor state persistence (#111) (priority: P0) [source: issue #111]
- [ ] Pause/resume rework (#59) (priority: P1) [source: issue #59]
- [ ] Flight checks: pre/in/post-flight (#62) (priority: P2) [source: issue #62]
- [ ] Proactive self-healing (#63) (priority: P2) [source: issue #63]
- [ ] Cascade failure recovery (#133) (priority: P2) [source: issue #133]
- [x] [Ghost, Circuit] Stop warn when jobs running (#94) (priority: P2) [source: issue #94] — Ghost: Added _check_running_jobs() IPC probe, modified stop_conductor() with safety guard. Warns when jobs running, asks confirmation. --force skips check. IPC failure falls through gracefully. 10 TDD tests in test_stop_safety_guard.py. Circuit: mateship pickup — committed conductor.py clone-aware socket_path wiring + test_conductor_commands.py mock update + test file. Commit 04ab102.
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
- [x] [Dash] CLI stale state feedback (#139) (priority: P2) [source: issue #139] — Full completion across 3 sessions: (1) check_pid_alive() helper + fresh-aware rejection hints (8bb3a10), (2) stale PID detection in `mozart start` — cleans up dead PID files with user notification, (3) `--fresh` early failure suppression — skips `await_early_failure` when --fresh to prevent false reports from old state (#139 root cause 1), (4) contradictory error regression verified fixed (Lens 4b83dae). 10 TDD tests in test_stale_state_feedback.py covering all 3 root causes from the issue.

---

## Composer-Assigned Tasking (Post-Mortem — v3 Failure Investigation)

These tasks were identified by the composer during failure investigation of the v3 job.
See FINDINGS.md F-097 through F-102 for full context.

### Timeout & Stale Detection (F-097, F-102)
- [ ] Increase `idle_timeout_seconds` from 1800 to 7200 in `generate-v3.py` (priority: P0) [source: F-097, F-102]
- [ ] Regenerate `mozart-orchestra-v3.yaml` with updated timeouts (priority: P0) [source: F-097]
- [x] [Blueprint] Add distinct error code E006 for stale detection (differentiate from backend timeout E001) (priority: P1) [source: F-097] — E006 EXECUTION_STALE added to ErrorCode enum, RetryBehavior (120s delay), WARNING severity. Classifier differentiates stale from timeout via "stale execution" in stderr. Both classify() and classify_execution() paths handled. 10 TDD tests.
- [x] [Spark] Fix error display: stale detection shows `Code: timeout` instead of error code (priority: P1) [source: F-097] — Added `error_code` field to SheetState, wired through `mark_sheet_failed()` and runner failure handlers. Added `format_error_code_for_display()` in output.py that maps ErrorCategory values to canonical error codes when structured error_code is None. Updated status.py, diagnose.py, and format_error_details. 26 TDD tests.

### Rate Limit Classification (F-098, F-099)
- [x] [Blueprint] Update error classifier to detect rate limit patterns in stdout, not just stderr (priority: P0) [source: F-098] — Added Phase 4.5 "Rate Limit Override" to classify_execution() that always scans stdout+stderr for rate limit patterns, even when Phase 1 found structured JSON errors. Patterns "rate.?limit", "hit.{0,10}limit", "limit.{0,10}resets?" already existed but were unreachable when Phase 1 masked them. 6 TDD tests including the core F-098 regression case.
- [x] [Blueprint] Add patterns: "API Error: Rate limit reached", "You've hit your limit", "resets" (priority: P0) [source: F-098] — Patterns already existed in _DEFAULT_RATE_LIMIT_PATTERNS. The bug was Phase 4 being skipped when Phase 1 found JSON errors. Phase 4.5 override fixes this.
- [x] [Forge] Stagger fan-out launches (100ms delay between starts) to reduce rate limit surge (priority: P2) [source: F-099] — Implemented as stagger_delay_ms on ParallelConfig. See M5 entry for details.

### Rate Limit Auto-Resume (F-112)
- [x] [Circuit] Schedule RateLimitExpired timer on rate limit hit — auto-resumes WAITING sheets when limit clears (priority: P1) [source: F-112] — Added timer scheduling in `_handle_rate_limit_hit()` at `core.py:958-967`. 10 TDD tests in `test_rate_limit_auto_resume.py`. The event type and handler already existed but nothing triggered them.

### Rate Limit Backpressure UX (F-110)
- [x] [Lens] Accept jobs in PENDING state during rate limit backpressure instead of rejecting (priority: P1) [source: F-110] — Mateship pickup of unnamed musician's implementation. BackpressureController.rejection_reason() distinguishes rate-limit vs resource pressure. JobManager._queue_pending_job() registers with PENDING status in JobMeta (visible in list/status). JobResponse model gains "pending" literal. DaemonJobStatus.PENDING added. CLI _handle_pending_response() shows info not error. Lens fixes: wired _start_pending_jobs() into clear_rate_limits() + deferred auto-start timer, added JobMeta tracking for list visibility, fixed mypy lambda inference. 23 TDD tests in test_rate_limit_pending.py. Fixed 3 existing tests in test_clear_rate_limits.py and 6 in test_m3_pass4_adversarial_breakpoint.py. Docs updated (cli-reference, daemon-guide).
- [x] [Lens] Pending jobs start automatically when rate limit clears (priority: P1) [source: F-110] — _start_pending_jobs() called after clear_rate_limits() (manual) + scheduled via deferred asyncio task with rate limit expiry delay + 2s buffer (automatic). Jobs started in FIFO order, backpressure re-checked between each.
- [x] [Lens] Pending jobs can be cancelled via `mozart cancel` (priority: P1) [source: F-110] — cancel_job() checks _pending_jobs first, removes from queue, updates JobMeta to CANCELLED, runs cleanup task. Included in mateship pickup implementation.
- [x] [Dash] `mozart run` / `mozart resume` shows time remaining when rate-limited: "Rate limit on claude-cli — clears in Xm Ys" (priority: P1) [source: F-110] — format_rate_limit_info() in output.py, query_rate_limits() in helpers.py, _show_rate_limits_on_rejection() in run.py, _show_active_rate_limits_sync() in status.py. Also enhanced _rejection_hints() with fresh-aware "clear stale entry" hints and updated pressure hints to suggest clear-rate-limits. 18 TDD tests.
- [x] [Lens] Fix misleading "Mozart conductor is not running" error on backpressure rejection (priority: P1) [source: F-110] — _try_daemon_submit now raises typer.Exit(1) on explicit rejection instead of returning False (which triggered misleading "not running" fallback). Rejection reason shown with hints. 3 TDD tests.

### Multi-Instrument Support (F-100, F-101, F-103, F-104, F-105)
- [x] [Composer] Fix baton `config.backend.max_retries` → `config.retry.max_retries` (priority: P0) [source: F-103]
- [x] [Composer] Add DispatchRetry kick after job registration (priority: P0) [source: F-103]
- [x] [Composer] Wire BackendPool creation + injection in manager startup (priority: P0) [source: F-103]
- [x] [Forge] Wire prompt rendering pipeline into baton musician `_build_prompt()` (priority: P0) [source: F-104] — Rewrote `_build_prompt()` with full 5-layer assembly: preamble (build_preamble), Jinja2 template rendering (Sheet.template_variables()), prelude/cadenza injection resolution, validation requirements formatting, completion mode suffix. Added `_render_template()`, `_resolve_injections()`, `_format_injection_section()`, `_format_validation_requirements()`. Updated `sheet_task()` with `total_sheets`/`total_movements` params. Adapter computes totals from registered sheets. 17 TDD tests. **UNBLOCKS BATON EXECUTION.**
- [x] [Canyon, Foundation] Wire cross-sheet context (previous_outputs/previous_files) into baton dispatch path (priority: P0) [source: F-210, Weaver M3] — Canyon M4: TDD tests (21), AttemptContext.previous_files, adapter._job_cross_sheet + _collect_cross_sheet_context() + _dispatch_callback wiring. Foundation M4 mateship: PromptRenderer._build_context() accepts AttemptContext to populate SheetContext cross-sheet fields, manager passes config.cross_sheet to register_job/recover_job, test fixes (CheckpointState/SheetAttemptResult constructors). F-210 RESOLVED.
- [x] [Canyon, Foundation] Fix baton checkpoint sync for EscalationResolved/EscalationTimeout/CancelJob/ShutdownRequested/JobTimeout/RateLimitExpired (priority: P2) [source: F-211, Weaver M3] — Canyon M4: TDD tests (16+18), state-diff architecture, _capture_pre_event_state, duck-typed single-sheet sync, CancelJob/ShutdownRequested handlers. Foundation M4: state-diff dedup cache (_synced_status), JobTimeout handler (_sync_all_sheets_for_job), RateLimitExpired handler (_sync_all_sheets_for_instrument), fixed _sync_cancelled_sheets_from_state to use dedup, fixed pre-existing test failures in test_baton_restart_recovery.py. F-211 RESOLVED.
- [ ] Enable `use_baton: true` in conductor config after F-210 fixed (priority: P1) [source: F-100, F-210]
- [ ] Route claude-cli through PluginCliBackend (not native ClaudeCliBackend) (priority: P1) [source: F-105]
- [x] [Blueprint] Expand instrument YAML schema: timeout/crash/capacity/stale patterns (priority: P1) [source: F-105] — Added crash_patterns and stale_patterns to CliErrorConfig (timeout_patterns and capacity_patterns already existed). 6 TDD tests. Log capture rules deferred.
- [x] [Spark] Verify `PluginCliBackend._classify_error()` uses profile-defined error patterns (priority: P1) [source: F-101] — Verified: _check_rate_limit() uses rate_limit_patterns, _classify_output_errors() uses auth_error_patterns/crash_patterns/stale_patterns/timeout_patterns/capacity_patterns. All from profile. 22 existing tests in test_plugin_cli_backend.py cover this.
- [ ] Add gemini-cli rate limit test: submit a sheet, mock rate limit response, verify E101/E102 classification (priority: P2) [source: F-101]

### Finding ID System (D-018)
- [x] [Bedrock] Design and implement finding ID collision prevention (priority: P2) [source: D-018, F-148] — Range-based allocation: `FINDING_RANGES.md` pre-allocates 10 IDs per musician per movement (M4: F-160 through F-479). Helper script `scripts/next-finding-id.sh` as fallback. FINDINGS.md header updated with protocol. Historical collision table documents 12 ambiguous IDs. F-148 RESOLVED.

### Rate Limit Wait Cap (F-350)
- [x] [Bedrock] Mateship pickup: commit uncommitted rate limit wait cap safety fix (priority: P2) [source: F-350, uncommitted work] — `RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0` in constants.py, `_clamp_wait()` in classifier.py, quality gate baseline 1230→1234, 10 TDD tests in test_rate_limit_wait_cap.py. Prevents adversarial API responses from blocking instruments forever.

### Cost Accuracy Investigation (D-024)
- [x] [Circuit] D-024 cost accuracy investigation — trace full pipeline, identify 5 root causes (priority: P1) [source: D-024, North directive] — F-180 filed. Root causes: ClaudeCliBackend zero tokens, baton hardcoded pricing, instrument profile pricing unused, confidence not displayed, text output format default. Commit 4055f0b.
- [x] [Circuit] Fix ClaudeCliBackend token extraction from JSON output (priority: P1) [source: F-180] — `_extract_tokens_from_json()` parses `usage.input_tokens`/`output_tokens` from Claude Code JSON response. 10 TDD tests. Commit 4055f0b.
- [x] [Circuit] Add cost confidence display to `mozart status` (priority: P1) [source: F-180] — `_render_cost_summary()` shows `~$X.XX (est.)` with warning for low-confidence costs. JSON output includes `cost_confidence` field. 2 TDD tests. Commit 4055f0b.
- [ ] Wire instrument profile model pricing into both cost paths (priority: P2) [source: F-180 root cause 3]
- [ ] Fix baton `_estimate_cost()` to use instrument profile pricing (priority: P2) [source: F-180 root cause 2]

### Skill Rename: mozart:usage → mozart:operations (or similar)
- [x] [Dash] Rename `mozart:usage` skill to `mozart:command` — collides with built-in `/usage` (Claude token usage). (priority: P1) [source: composer directive] — Renamed skills/usage/ → skills/command/ in plugin submodule, updated SKILL.md frontmatter + title, updated cross-references in score-authoring/SKILL.md (3 refs) and essentials.md (1 ref). Updated project CLAUDE.md (2 refs from stale absolute paths to plugin skill invocations). Global CLAUDE.md needs manual update (permission denied from agent context).
- [x] [Dash] Update all references across project: CLAUDE.md, skill files, docs (priority: P1) — Completed as part of rename above.

### Gemini CLI Agent Assignment (TDF Analysis — Composer Recommendation)
- [ ] Assign gemini-cli instrument to dreamer agents (6 per movement) — memory consolidation, low tool use (priority: P1) [source: composer TDF analysis]
- [ ] Assign gemini-cli instrument to reviewer agents (prism, axiom, ember) — read-heavy analysis, low tool use (priority: P2) [source: composer TDF analysis]
- [ ] Keep claude-cli for: setup (canyon, bedrock), work (all 32 musicians), quality-gate (bedrock), antagonists (newcomer, adversary) (priority: P1) [source: composer TDF analysis]
- [ ] Add `instrument_map` or `per_sheet_instruments` to `generate-v3.py` for gemini-cli assignments (priority: P1) [source: composer TDF analysis]

---

## Deferred (v1.1+)

- [ ] HTTP instrument backends [source: roadmap]
- [ ] Abstract technique/capability mapping [source: roadmap]
- [ ] Code-mode techniques implementation [source: roadmap]
- [ ] Distributed execution (#52) [source: issue #52]
- [ ] Full HITL (#64) [source: issue #64]
