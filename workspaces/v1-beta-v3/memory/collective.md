# Mozart Orchestra — Collective Memory

## Core Memories
**[CORE]** We are building Mozart v1 beta — the intelligence layer that makes AI agent output worth adopting.
**[CORE]** The spec corpus at .mozart/spec/ is the source of truth. Every agent reads it.
**[CORE]** pytest/mypy/ruff must pass after every implementation. No exceptions.
**[CORE]** The music metaphor is load-bearing — use it in all user-facing output.
**[CORE]** We restructured from a hierarchical company to a flat orchestra. Every musician is an equal peer. The music metaphor is real now.
**[CORE]** Uncommitted work doesn't exist. Commit on main. Always.
**[CORE]** Read 03-confluence.md — understand what you are and what your work means. Down. Forward. Through.
**[CORE]** Two correct subsystems can compose into incorrect behavior. Bugs at system boundaries are the hardest to find because each side looks correct in isolation. (F-065, Axiom M2)
**[CORE]** The composer found more bugs in one real production usage session than 755 tests found in two movements. The gap between "tests pass" and "product works" is where quality lives.

## Learned Lessons
- **[Cycle 1, Forge]** Always check what exists before assuming you need to build.
- **[Cycle 1, Circuit]** Test the production path, not the internal method. `classify_execution()` had zero coverage while `classify()` was fully tested.
- **[Cycle 1, Harper]** Always check the error path, not just the happy path. Stale detection only covered COMPLETED, not FAILED.
- **[Cycle 1, Dash]** Don't assume something is broken without checking. The dashboard has 23 Python files, ~50 endpoints, all functional.
- **[Cycle 1, Composer Notes]** The learning store schema migration failure (#140) brought down ALL jobs. Always write migrations, tests, verify against existing DBs.
- **[Cycle 1, Lens]** 12 learning commands (36% of CLI) dominate help output — poor information architecture.
- **[Cycle 1, Warden]** stdout_tail/stderr_tail stored in 6+ locations without credential scanning. Safety applied piecemeal.
- **[Cycle 1, Blueprint]** SpecCorpusLoader used `if not name:` instead of `if name is None:` — rejects falsy-but-valid YAML values.
- **[Cycle 1, Ghost]** When the foundation is about to shift, audit first. The instinct to "do something" is wrong when you don't know the baseline.
- **[Cycle 1, Breakpoint]** Test the abstraction level that runs in production. Zero tests existed for PriorityScheduler._detect_cycle().
- **[Movement 1, Axiom]** Failed sheets must propagate failure to dependents. Without propagation, `is_job_complete` returns False forever — zombie jobs.
- **[Movement 1, Theorem]** Property-based testing finds bugs hand-picked examples miss. Hypothesis found the escalation terminal-guard violation in seconds.
- **[Movement 1, Adversary]** Every handler that transitions sheet status must check `_TERMINAL_STATUSES`. The baton's guard pattern is now complete.
- **[Movement 1, Mateship]** The finding→fix pipeline works without coordination: F-018 filed by Bedrock, proved by Breakpoint, fixed by Axiom, verified by Journey. Four musicians, zero meetings.
- **[Movement 2, Axiom]** `record_attempt()` correctly doesn't count successes; `_handle_attempt_result` correctly retries on 0% validation. Together: infinite loop. Bugs at system boundaries.
- **[Movement 2, Axiom]** The pause model (`job.paused` boolean) serves three masters: user, escalation, cost. Post-v1: replace with pause reason set.
- **[Movement 2, Newcomer]** The gap between "feature works" and "feature is taught" is where adoption dies. F-083 — instrument system had zero adoption in examples.

## Design Decisions
- **Baton migration:** Feature-flagged BatonAdapter. Old and new paths coexist. Do not re-debate.
- **Cost visibility:** Scoped first (CostTracker in status), full UX later.
- **Learning schema changes:** Additive only without escalation.
- **Code-mode techniques:** Long-term direction. MCP supported for v1. code_mode flag exists, not wired.
- **No automatic instrument fallback on rate limits.** Explicit opt-in only.
- **Daemon-only architecture.** All state in ~/.mozart/mozart-state.db. JsonStateBackend deprecated.
- **Musician-baton contract:** validation_pass_rate defaults to 0.0 (safety), baton auto-corrects to 100.0 when validations_total==0 and execution_success==True.
- **Terminal state invariant:** All baton handlers guard against `_TERMINAL_STATUSES`. Seven bugs found and fixed by three independent methodologies.
- **Pause model debt:** Single boolean serving three masters. Post-v1 → pause_reasons set.

## Current Status

Movement 2 — IN PROGRESS.

### Movement 2 Updates (Maverick + Canyon)
- **STEP 29 RESOLVED** (Maverick b4146a7, mateship pickup): Restart recovery committed. recover_job(), state sync callback, orphan recovery on start(), _resume_via_baton(). 27 TDD tests. 8th uncommitted work pickup.
- **F-132 PARTIALLY RESOLVED** (Maverick b4146a7): Fixed process.py but MISSED clone.py. Canyon found the same bug in `build_clone_config()` — DRY violation, two paths building clone configs both missed state_db_path. Canyon's fix: clone.py:144 + 3 TDD tests. Now fully resolved.
- **M2 Baton is now COMPLETE** — all steps 17-29 done. Ready for `use_baton: true` testing.
- **Canyon co-composer review**: Step 29 wiring verified on HEAD. _resume_via_baton() loads checkpoint → builds sheets → recover_job(). _recover_baton_orphans() loads paused checkpoints at startup. state_sync_callback wired to adapter constructor. Manager duplicates of _on_baton_state_sync and _recover_baton_orphans removed (were added by Canyon before discovering the existing implementations).

- **F-134 FOUND + RESOLVED** (Foundation M2): `_run_via_baton()` used non-existent `config.cost_limits.max_cost_usd` — should be `max_cost_per_job`. Latent bug that would silently disable cost limits when `use_baton` is enabled. Fixed in both `_run_via_baton()` and `_resume_via_baton()`.

### Milestone Table (Verified by Bedrock, Movement 2)
| Milestone | Status | Detail |
|-----------|--------|--------|
| M0 Stabilization | COMPLETE | 22/22 tasks |
| M1 Foundation | COMPLETE | 17/17 tasks |
| M2 Baton | **COMPLETE** | 23/23 tasks. All steps 17-29 done. |
| M3 UX & Polish | COMPLETE | 23/23 tasks |
| M4 Multi-Instrument | 47% | 8/17 tasks. Data models + validation + docs done. Demo, skill update, examples audit open. |
| M5 Hardening | 43% | 3/7 tasks. Workspace paths + injection + credential env done. Config, fan-out, resume open. |
| --conductor-clone | 94% | 17/18 tasks. Fully wired. Remaining: pytest conversion. |
| Composer-Assigned | 41% | 11/27 tasks. Timeout/rate-limit/baton fixes done. Backpressure UX, gemini assignment open. |

### Movement 2 Updates (Harper)
- **F-122 RESOLVED** (Harper bd72395): All 5 DaemonClient callsites that bypassed --conductor-clone now use `_resolve_socket_path(None)`. Hooks (concert chaining), MCP tools, dashboard routes, job_control, and app factory. 14 TDD tests. Zero hardcoded socket bypasses remain in codebase.
- **F-131 RESOLVED** (Harper bd72395): --conductor-clone help text updated to require = syntax.
- **F-111 + F-113 + F-119 MATESHIP PICKUP** (Harper 861ef63): P0 production fixes committed from working tree. F-111: exception objects preserved in ParallelBatchResult, lifecycle re-raises RateLimitExhaustedError (PAUSE not FAIL). F-113: failed sheet failure propagated to dependents. F-119: baton event stubs now log instead of silently dropping.
- **Conductor-clone is now FULLY COMPLETE** — zero socket path bypasses remain. All IPC paths are clone-aware.

### Movement 2 Updates (Circuit — Verification Sweep)
- **FINDINGS.md cleanup:** Updated 7 findings from Open → Resolved with detailed resolutions: F-111 (P0), F-113 (P0), F-116 (P2), F-122 (P1), F-127 (P2), F-129 (P1), F-131 (P3). All verified against committed code on HEAD.
- **P0 production bugs RESOLVED:** F-111 (rate limit type lost in parallel) and F-113 (failed deps treated as done) — both fixed by unnamed musician, committed by Harper (861ef63), verified by Circuit. The two highest-severity open bugs are now closed.
- **F-129 (restart deadlock) RESOLVED:** Fixed as a side effect of F-113 — FAILED status now in the terminal set, so DAG resolution works from persisted state without needing the ephemeral `_permanently_failed` set.
- **Quality gate:** 10,132 tests pass, mypy clean, ruff clean. Zero failures.
- **System observation:** The mateship pipeline has matured to the point where fix→commit→verify happens across multiple musicians without explicit coordination. The finding→fix→verify chain is the orchestra's strongest institutional mechanism.

### Movement 2 Updates (Blueprint)
- **F-116 RESOLVED** (Blueprint 327e536): V210 InstrumentNameCheck — validates instrument names against loaded profiles at `mozart validate` time. Checks score-level, per-sheet, instrument_map, and movement instruments. WARNING severity. 15 TDD tests.
- **F-127 RESOLVED** (Blueprint 327e536): `_classify_success_outcome()` now uses persisted `SheetState.attempt_count` instead of session-local `normal_attempts`. Sheets with 18 cumulative attempts after restart correctly show SUCCESS_RETRY, not SUCCESS_FIRST_TRY. 7 TDD tests.
- **F-132 clone isolation tests** (Blueprint 327e536): 5 additional tests verifying state_db_path and log_file isolation in build_clone_config.

### Reviews Summary (M1C7)
- **Prism:** 42 commits verified. Closed 4 GitHub issues (#104, #149, #150, #151). F-104 verified complete. THREE GAPS UNCHANGED: step 29, F-009, demo.
- **Ember:** Golden path solid. Persistent paper cuts: F-127 (diagnose lies), F-048/F-108 ($0.00 cost), F-067b (init positional arg), F-116 (invalid instrument passes validation).
- **Axiom:** Baton spec compliance 3/4 invariants hold. Lovable/Wordware demos at zero — 5 movements non-compliance. Baton-runner divergence accelerating.
- **North:** Issued D-014–D-019. Directives with named musicians work; without don't. Step 29 stalled 5+ movements.
- **Tempo:** Build→Converge→Verify three-cycle rhythm emerged naturally. Uncommitted work anti-pattern RESOLVING.
- **Weaver:** ALL 6 M1C1 integration seams RESOLVED. Step 29 sole remaining. Organizational geometry (32 parallel, 1 serial) fights the need.

### Key Deliverables (Movement 1 — All Cycles)
- **F-104 RESOLVED** (Forge 3deb436): Full 5-layer prompt rendering in baton musician. BATON EXECUTION UNBLOCKED.
- **--conductor-clone RESOLVED** (Spark+Ghost+Harper): Global CLI option, socket/PID/config isolation, named clones, 58+ TDD tests.
- **F-118 RESOLVED** (Axiom 4520d05): ValidationEngine context gap fixed — full template_variables() in baton _validate().
- **Error taxonomy** (Blueprint): E006 stale detection, Phase 4.5 rate limit override, crash/stale patterns in CliErrorConfig.
- **F-025 RESOLVED** (Warden): Credential env filtering via required_env field on CliCommand. 19 TDD tests.
- **Production bugs RESOLVED**: F-075 (resume corruption), F-076 (validation ordering), F-077 (hooks lost on restart). All via mateship (Maverick f58fc89).
- **M4 data models** (Blueprint 75bebed): InstrumentDef, MovementDef, per_sheet_instruments, instrument_map, movements on JobConfig.
- **Documentation** (Codex+Guide): spec corpus + grounding hooks in score-writing-guide, 4 missing CLI commands, instrument_name template var, Rosetta proof scores in README.
- **Testing**: 215 adversarial (Breakpoint+Adversary), 136 property-based (Theorem), 36 litmus (Litmus), 44 edge case journeys (Journey). Zero new bugs in M4 code.
- **Security** (Sentinel): Full audit, zero new findings. All 4 shell paths protected. F-061 (CVEs) blocks public release.

### Movement 2 Updates (Forge, Cycle 2)
- **F-111 RESOLVED** (Forge, committed by Harper 861ef63): Parallel executor now preserves `RateLimitExhaustedError` in `ParallelBatchResult.exceptions`. Lifecycle re-raises original exception → job PAUSES, not FAILS. 8 TDD tests.
- **F-113 RESOLVED** (Forge, committed by Harper 861ef63): `propagate_failure_to_dependents()` added to `ParallelExecutor` — BFS through DAG marks dependents as FAILED. `get_next_parallel_batch` includes FAILED in terminal set (survives restarts). 6 TDD tests.
- **F-119 RESOLVED** (Forge, committed by Harper 861ef63): Baton event stubs log instead of silent `pass`.
- **F-112 DEFERRED**: Auto-resume after rate limit pause. Needs manager.py + registry changes. Baton timer wheel is better vehicle.

### Movement 2 Updates (Codex)
- **M4 Step 44 Documentation: instrument migration guide COMPLETE** — Added backend→instrument migration section to score-writing-guide.md with field mapping table, before/after YAML, and compatibility notes. This closes the last remaining documentation gap from the M4 roadmap.
- **CLI reference V-code verification** — All 25 V-codes verified against source code. V009 fixed from WARNING to ERROR (matched `ValidationSeverity.ERROR` in `config.py:290`). Stale `--workspace` example removed from pause command.
- **Limitations.md updated** — Baton execution engine section replaces "Phase 3 Components Not Wired". Instrument plugin system section replaces "Claude-Centric Design".
- **Mateship pickup** — Committed unnamed musician's CLI reference expansion (V-code tables, init positional arg, list --json) and daemon guide baton section after verifying all changes against source code.
- **Stale reference fix** — `score-writing-guide.md:1976` had `mozart status my-job -w ./workspace --watch` using hidden debug flag in user-facing example. Fixed to `--watch` only.

### Movement 2 Updates (Spark)
- **Mateship pickup — 3 commits, 17 files** (3269eb2, d242046, d149993):
  - F-065b RESOLVED: diagnose progress+timeline use format_sheet_display_status (6 TDD tests)
  - F-067b RESOLVED: init accepts positional argument like `git init my-project` (7 TDD tests)
  - run.py hint= → hints= fix (invisible terminal hint, 3 TDD tests)
  - Validation: movement/voice/voice_count/total_movements/instrument_name added to builtin sets
  - top.py: last 5 raw console.print migrated to output_error() — M3 step 35 FULLY COMPLETE
  - Examples: dialectic.yaml modernized (movements: key, movement/voice aliases, parallel)
  - Examples: 3 Rosetta proof scores moved from examples/ to examples/rosetta/
  - Rosetta corpus restructured (scores/rosetta-corpus.md)
- **FINDINGS.md:** F-065 (Ember) → Resolved. F-067 (Ember) → Resolved. F-047 → Resolved.

### Movement 2 Updates (Dash)
- **CLI UX Audit COMPLETE** (62fc205): Full audit at movement-2/cli-ux-audit.md. 6 issues (2 P1, 2 P2, 2 P3), overall grade B+. Top finding: 12 learning commands (46% of CLI) dominate help output — needs E-002 escalation for `mozart learning` subcommand refactor.
- **Terminology fixes** (62fc205): top.py --job→--score, clear --job→--score, clear docstring "terminal scores"→"completed, failed, and cancelled scores", modify docstring clarified, resume "job state"→"score state".
- **Mateship pickup** (44b7f99): Committed 6 TDD tests for top.py error standardization (Spark d242046's implementation, test file left untracked).
- **Error standardization CONFIRMED COMPLETE**: M3 step 35 fully done — zero raw console.print error calls remain. All remaining [red] usage is data coloring, not error output.

### Movement 2 Updates (Lens)
- **Hintless output_error() sweep** (089cc0c): Added hints to 5 output_error() calls that lacked constructive guidance. resume.py (3 paths: config load, config reload, fatal error), diagnose.py (2 paths: log read, log follow). 3 TDD tests in test_resume_error_hints.py. Core workflow error messages now all have actionable next steps.
- **Prior work committed via mateship**: F-065b, F-067b, hint=/hints= fix, validation variables, top.py error standardization — all picked up by Spark (3269eb2) and Dash (44b7f99) before this session. 10th occurrence of the uncommitted work pattern.

## Coordination Notes (Active)
- **CRITICAL PATH (UPDATED):** ~~Step 29~~ DONE → ~~F-111/F-113~~ DONE → Enable use_baton (--conductor-clone testing) → F-112 (auto-resume) → Demo.
- **D-005 ROOT CAUSE (Oracle):** F-009 is feedback loop disconnection — 91% of patterns never applied due to narrow context tag matching. STILL UNIMPLEMENTED after 5+ movements.
- **Uncommitted work:** Workspace files only. Mateship pipeline resolved the pattern.
- **F-132:** FULLY RESOLVED (Maverick + Canyon). Both code paths fixed.
- **F-128 WRONG (Adversary M1C7):** E006 IS reachable via classify_execution() — original analysis was incorrect.
- **GitHub issues closed (Ghost M2):** #95 (workspace path), #112 (health check quota), #99 (hooks restart). All verified.
- **M5 Hardening verified (Ghost M2):** Steps 45 + 46 complete. All 4 shell execution paths hardened.

### Movement 2 Updates (Oracle)
- **Codebase metrics:** 96,435 source lines (+0.8%), 10,159 tests (+1.1%), 101 models. Lowest growth movement = hardening phase.
- **Baton:** 5,265 lines (+4.5%), 969 tests (+21.9%). Step 29 committed. Highest test growth area.
- **Learning store (F-009):** 28,772 patterns, 88.6% never applied, validated tier grew 40% (130→182). Instrument tags: 3 of 28,772. STILL UNIMPLEMENTED.
- **V3 orchestra:** 58/706 complete (8.2%). M0+M1 done. M2 work started — 4 of 32 sheets dispatched.
- **Rate limit surge (F-099):** M2 setup sheets 55-58 required 6-18 retries. 51 unnecessary API calls. Same pattern as M1.
- **F-127 confirmed in production:** All 58 sheets show success_first_try despite 23 requiring 2-29 attempts.
- **p99 duration:** 30.5 minutes (n=27,910) — exactly at stale detection ceiling. Confirmed across all executions.
- **Flowspec:** 16,833 entities, 2,010 warnings, 0 critical. Warning-to-entity ratio improved to 11.9%.
- **Quality gates:** mypy clean, ruff clean, flowspec clean.
- **Uncommitted work:** 8 files — all workspace memory. No code. Anti-pattern resolving.
- **Predictive model:** 3-4 movements to demo-ready. 42-55 wall-clock hours to M3 completion.

### Movement 2 Updates (Atlas — Strategic Assessment)
- **STATUS.md updated** to reflect: baton COMPLETE, conductor-clone COMPLETE, 10,100+ tests, 96,435 source lines. Critical blockers updated.
- **Strategic assessment:** The baton is the most verified untested system in the project — 1,000+ tests, 4 independent methodologies, never run a real sheet. The critical path is now activation only: enable use_baton → test → fix → demo.
- **F-009 upgraded to CRITICAL:** 6 movements without implementation. Product thesis unsubstantiated. The intelligence layer doesn't learn.
- **Organizational observation:** The orchestra self-selects for parallel building, not serial activation. Named musician assignments are required for convergence tasks.

### Movement 2 Updates (Warden)
- **F-135 FOUND + RESOLVED:** Musician exception handler at `musician.py:156` leaked credentials via `error_message`. Exception text was stored unredacted in SheetAttemptResult, logs, state DB, and diagnostic output — while stdout/stderr were correctly redacted 3 lines away. Applied `redact_credentials()` to both exception handler and validation error handler. 26 TDD tests.
- **F-061 RESOLVED:** Added minimum version pins for 3 CVE-affected transitive dependencies: `cryptography>=46.0.6`, `pyjwt>=2.12.0`, `requests>=2.33.0`. The last security finding blocking public release is now closed.
- **Safety posture:** All known credential leak paths are now protected. All shell execution paths are hardened. Dependency CVEs resolved. The only remaining open security findings are F-021 (sandbox bypass, acceptable for v1) and F-022 (CSP unsafe-inline, mitigated by LOCALHOST_ONLY).

### Movement 2 Updates (Sentinel — Security Review, Second Pass)
- **F-136 FOUND + RESOLVED:** `_classify_error()` at `musician.py:587-628` returned backend `error_message` without credential redaction. Third instance of the piecemeal redaction pattern (after F-003 stdout/stderr and F-135 exceptions). Fixed at musician.py:129. 5 TDD tests.
- **F-137 FILED (P3):** Pygments 2.19.2 has CVE-2026-4539 (ReDoS in ADL lexer). Fix: 2.20.0. Near-zero risk — ADL lexer unused by Mozart.
- **Full security audit results:** 12 subprocess spawn sites verified (zero unprotected). All 3 credential data paths through musician protected. F-061 CVEs confirmed resolved. Open security findings reduced to 3 (F-021, F-022, F-137), none critical. All acceptable for v1.

### Movement 2 Updates (Bedrock — Ground Verification)
- **Milestone table corrected:** All counts were stale. M0=22/22, M1=17/17, M2=23/23, M3=23/23, M4=8/17 (47% not 36%), M5=3/7 (43%), conductor-clone=17/18 (94%), composer-assigned=11/27 (41%). M5 and composer-assigned rows were missing.
- **Findings hygiene:** Updated 3 stale original entries (F-097, F-098, F-104) to point to their resolution entries. Resolved F-107b (composer's uncommitted fixes — verified committed on HEAD).
- **Quality gate:** pytest 10,180 passed (0 failed, exit 0). mypy clean. ruff clean. 96,443 source lines, 285 test files.
- **Working tree CLEAN:** 8 changes — all workspace memory + 2 score files. No uncommitted code. Anti-pattern resolved.
- **Finding ID collision flagged:** F-107 used twice (same class as F-070, F-086). 4 collision incidents total.

### Movement 2 Updates (Litmus — Effectiveness Validation)
- **20 new litmus tests** (6db727c): Categories 11-17 validating M2 completions. 56 total litmus tests.
- **Step 29 recovery VALIDATED:** recover_job() correctly preserves terminal states, resets in_progress→PENDING, carries forward attempt counts. The adapter kicks DispatchRetry after recovery so dispatch_ready runs immediately.
- **Completion signaling VALIDATED:** wait_for_completion() correctly detects all-success vs any-failure using asyncio.Event pattern.
- **F-134 VALIDATED:** Both _run_via_baton and _resume_via_baton access config.cost_limits.max_cost_per_job (not the old max_cost_usd). CostLimitConfig.model_fields confirms max_cost_usd doesn't exist.
- **F-135 VALIDATED:** musician.py imports and calls redact_credentials on raw_error_msg in the exception handler.
- **F-132 VALIDATED:** build_clone_config produces isolated state_db_path in both code paths (with and without base_config). Named clones get unique paths.
- **F-111/F-113 VALIDATED structurally:** ParallelBatchResult.exceptions preserves types, propagate_failure_to_dependents exists, SheetStatus.FAILED in terminal set.
- **State mapping totality VALIDATED:** Every CheckpointState status maps to baton status and vice versa — no unmapped states.

### Movement 2 Updates (Litmus — Effectiveness Validation, Cycle 2)
- **18 new litmus tests** (b0d2dc1): Categories 18-24 validating M2 effectiveness. 92 total litmus tests.
- **Instrument alias resolution VALIDATED** (3 tests): `build_sheets()` correctly resolves score-level aliases (InstrumentDef) through the full resolution chain. Config merge semantics correct — alias config overrides score-level. Per-sheet still takes priority over alias.
- **V210 validation with aliases VALIDATED** (1 test): InstrumentNameCheck accepts alias names declared in `config.instruments` as valid instrument references. No false warnings.
- **F-127 success outcome VALIDATED** (3 tests): `_classify_success_outcome()` uses cumulative `attempt_count` (persisted) not session-local counter. 18 attempts → SUCCESS_RETRY (correct). 1 attempt → SUCCESS_FIRST_TRY. Completion mode → SUCCESS_COMPLETION.
- **F-111 exception preservation VALIDATED** (3 tests): `ParallelBatchResult.exceptions` dict preserves original exception types. `_find_rate_limit_in_batch()` extracts `RateLimitExhaustedError` with `resume_after` timestamp intact.
- **F-113 failure propagation VALIDATED** (1 test): `propagate_failure_to_dependents()` exists on ParallelExecutor using iterative BFS. FAILED in terminal set.
- **F-119 event stub logging VALIDATED** (2 tests): StaleCheck and CronTick produce warning logs with "unimplemented" message — not silent `pass`.
- **Credential defense-in-depth VALIDATED** (5 tests): All three data paths through musician (stdout/stderr, exception, _classify_error) confirmed protected. Pattern thresholds verified (min-length is deliberate, not a bug).
- **Untracked test_baton_m2c2_adversarial.py** has cross-test state leakage (F-064 class) — passes in isolation, fails with certain random seeds. NOT a test logic bug.

### Movement 2 Updates (Breakpoint — Adversarial Verification)
- **63 adversarial tests committed** in `tests/test_baton_m2c2_adversarial.py`: Fixed and extended untracked file (47→63 tests, 2 bugs fixed: missing `attempt` field, short credential key). 13 test classes covering step 29 recovery, state sync, credential redaction, rate limit extraction, failure propagation, cost limits, status mapping, completion signaling, instrument resolution.
- **No new bugs found** in M2 completion code. Step 29, F-111/F-113, F-135/F-136, F-134 — all correct under adversarial conditions.
- **Score-level instrument resolution verified:** 12 TDD tests in `test_score_level_instrument_resolution.py` all pass. Resolution at `sheet.py:249-255`, V210 recognizes at `config.py:517-518`.
- **Testing infrastructure risk identified:** Untracked test files written but never run create false confidence. Mateship pipeline should verify before committing.

### Movement 2 Updates (Theorem — Property-Based Verification, Cycle 2)
- **25 new property-based tests** in test_baton_invariants_m2c2.py. Total invariant suite: 119 test functions (161 counting hypothesis parameterization). Scope expanded from baton-only to four independent domains.
- **Recovery invariants PROVEN:** recover_job() maps in_progress → PENDING, preserves terminal states, carries forward attempt counts without loss. Mixed-status multi-sheet recovery verified under hypothesis. Adapter faithfully rebuilds from checkpoint truth.
- **Clone path isolation PROVEN:** For any two different clone names (hypothesis-generated), all 4 paths (socket, pid, state_db, log) are fully disjoint. No path overlaps with production defaults. Internal path uniqueness verified.
- **Credential redaction PROVEN (3 properties):** Totality (every credential pattern in any context is redacted), idempotency (double redaction = single redaction), non-credential preservation (safe text passes through unchanged). Combined, these prove the scanner is both correct AND harmless.
- **V210 instrument name validation PROVEN:** Unknown instruments produce warnings, known instruments produce none. Hypothesis fuzzing with random lowercase+dash names.
- **Failure propagation terminal preservation PROVEN:** COMPLETED/FAILED/SKIPPED sheets in the dependency chain are never overwritten by upstream failure propagation.
- **F-138 FILED (P3):** Untracked test_baton_m2c2_adversarial.py has broken ParallelExecutor construction — `dag` is now a property, not settable directly.
- **Zero bugs found.** Recovery, clone isolation, credential redaction, instrument validation, and failure propagation are all mathematically consistent. The M2 completion work is solid across all four domains.

## Top Risks
1. **F-009 (P0):** Learning store effectiveness inert 6+ movements. Root cause known. Intelligence thesis unproven. CRITICAL.
2. **Demo work (P0):** Neither Lovable nor Wordware demos started. Product invisible to the world.
3. ~~**F-111 (P0):**~~ RESOLVED. ~~**F-113 (P0):**~~ RESOLVED.
4. **Baton activation (HIGH):** All code complete. Never run a real sheet. Needs --conductor-clone testing.
5. **F-112 (P1):** Auto-resume after rate limit PAUSE not yet implemented. Jobs pause correctly but need manual resume.
5. **Enable use_baton:** Step 29 resolved, F-111/F-113 resolved, but use_baton not yet activated. Needs --conductor-clone testing.

## Blockers
- **F-009 (P1):** Learning store inert. Oracle found root cause (narrow tag matching). Still unimplemented.
- **F-104:** RESOLVED. **#145:** RESOLVED. **F-103:** RESOLVED. **Step 29:** RESOLVED.
- **#95:** RESOLVED (closed). **#112:** RESOLVED (closed). **#99:** RESOLVED (closed).

## Roster (32 musicians, equal peers)
Forge, Captain, Circuit, Harper, Breakpoint, Weaver, Dash, Journey, Lens, Warden,
Tempo, Litmus, Blueprint, Foundation, Oracle, Ghost, North, Compass, Canyon, Bedrock,
Maverick, Codex, Guide, Atlas, Spark, Theorem, Sentinel, Prism, Axiom, Ember,
Newcomer, Adversary

## Cold Archive

### Movement 1 (Cycles 1–7)
The orchestra's first full building movement. Thirty-two musicians worked in parallel with zero merge conflicts across 42 commits. The coordination substrate — TASKS.md, FINDINGS.md, collective memory — held under real load. Mateship was genuine and spontaneous: Harper picked up Ghost's doctor command, Journey rescued 5 untracked test files, Tempo committed PreflightConfig work, Prism committed 2,262 lines of stalled changes, Harper picked up 36 files in one mateship shot.

Three natural rhythms defined the movement: a build wave (Canyon, Ghost, Harper, Forge, Maverick, Blueprint, Foundation, Circuit creating infrastructure), a convergence wave (5 musicians self-organizing around the F-104 blocker without coordination), and a verification wave (Axiom, Theorem, Breakpoint, Adversary hardening the state machine through four independent testing methodologies — backward-tracing, property-based, adversarial, and experiential — each finding bugs the others missed). The instrument plugin system shipped end-to-end across 5 musicians in 8 coordinated steps.

The baton's terminal guard pattern was completed across all 19 status transitions through this convergence of methods. Seven bugs were found and fixed (F-039 through F-049). The credential scanner was wired at the single bottleneck (SheetState.capture_output) — architectural elegance over brute force.

The learning store remained the deepest systemic concern — 233K+ executions, 27K+ patterns, and the system cannot tell good from bad. Oracle's root cause analysis (91% of patterns never applied due to narrow tag matching) was the most important diagnosis. A trimodal distribution (0.50, 0.55, 0.98) with 3,111 differentiated patterns showed breadcrumbs of quality signal.

The uncommitted work anti-pattern peaked at 9 occurrences across all movements but was resolved by cycle 7 through Harper's mateship model. Working tree went from 36+ files to 12 (workspace files only).

The movement ended with all quality gates green, three major blockers resolved (F-104, conductor-clone, F-103), and a clear but unmoving critical path: Step 29 → use_baton → Demo. The orchestra builds infrastructure beautifully. The product gaps — step 29 unclaimed, F-009 unimplemented, demo at zero — are product gaps, not infrastructure gaps. The organizational geometry (32 parallel musicians, 1 serial critical path need) is the structural tension that defines the next movement.

### Earlier Movements (M0–M3 Build + M2 Review)
M0 stabilized the foundation: learning store remediation, critical bugs, dead code removal, intelligence verification. M1 shipped the instrument plugin system and sheet-first architecture. M2 built the baton core (event types, timer wheel, state model, dispatch, retry, rate limits, failure evaluation, BackendPool) plus the conductor-clone system. M3 delivered UX polish (status, doctor, init, instruments, error standardization), M4 data models, observability fixes, and production bug fixes. Canyon's step 28 wiring analysis (8 integration surfaces, 5-phase implementation) became the blueprint Foundation built from. The BatonAdapter (775+ lines, 64 TDD tests) wired 7 of 8 surfaces. The finding→fix pipeline matured into a reliable institutional mechanism. Newcomer's fresh-eyes audits drove real UX improvements. Guide's instrument migration (all 37 examples) closed the gap between feature availability and feature adoption.
