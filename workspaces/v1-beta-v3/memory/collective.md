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
- **[Movement 1, Mateship]** The finding->fix pipeline works without coordination: F-018 filed by Bedrock, proved by Breakpoint, fixed by Axiom, verified by Journey. Four musicians, zero meetings.
- **[Movement 2, Axiom]** `record_attempt()` correctly doesn't count successes; `_handle_attempt_result` correctly retries on 0% validation. Together: infinite loop. Bugs at system boundaries.
- **[Movement 2, Axiom]** The pause model (`job.paused` boolean) serves three masters: user, escalation, cost. Post-v1: replace with pause reason set.
- **[Movement 2, Axiom]** When fixing a bug class (e.g., "handler doesn't check cost"), audit ALL handlers with the same pattern. F-067 fixed two of three. F-143 found the third.
- **[Movement 2, Axiom]** F-009 root cause confirmed: learning store query tags and storage tags are in different namespaces with zero overlap. 28,772 patterns accumulated, 91% never applied. The intelligence layer was disconnected.
- **[Movement 2, Newcomer]** The gap between "feature works" and "feature is taught" is where adoption dies. F-083 — instrument system had zero adoption in examples.
- **[Movement 3, Axiom]** State sync gap class: _propagate_failure_to_dependents() changes status directly without events. On restart, cascaded failures lost. Same class as F-039 and F-065.
- **[Movement 3, Weaver]** The orchestra optimizes for parallel work. The critical path is serial. Every movement since M2 confirms this.
- **[Movement 3, North]** Directives must specify the deliverable, not the direction. "Activate the baton" produced readiness, not activation.
- **[Movement 3, Prism]** 32 parallel musicians can't execute a serial critical path. The format optimizes for breadth; the remaining work demands depth.
- **[Movement 3, Tempo]** Mateship rate 33% (12/36 commits) — pipeline is now the dominant collaboration mechanism, evolved from anti-pattern fix to institutional behavior.

## Design Decisions
- **Baton migration:** Feature-flagged BatonAdapter. Old and new paths coexist. Do not re-debate.
- **Cost visibility:** Scoped first (CostTracker in status), full UX later.
- **Learning schema changes:** Additive only without escalation.
- **Code-mode techniques:** Long-term direction. MCP supported for v1. code_mode flag exists, not wired.
- **No automatic instrument fallback on rate limits.** Explicit opt-in only.
- **Daemon-only architecture.** All state in ~/.mozart/mozart-state.db. JsonStateBackend deprecated.
- **Musician-baton contract:** validation_pass_rate defaults to 0.0 (safety), baton auto-corrects to 100.0 when validations_total==0 and execution_success==True.
- **Terminal state invariant:** All baton handlers guard against `_TERMINAL_STATUSES`. Seven bugs found and fixed by three independent methodologies.
- **Pause model debt:** Single boolean serving three masters. Post-v1 -> pause_reasons set.
- **Finding ID allocation:** Range-based, 10 IDs per musician per movement. `FINDING_RANGES.md` + `scripts/next-finding-id.sh`. Prevents collision (12 historical incidents).

## Current Status

Movement 3 — COMPLETE (2026-04-04). Movement 4 — IN PROGRESS.

### M4 Progress (Dash)
- **Skill rename: mozart:usage → mozart:command** — Plugin submodule renamed (skills/usage/ → skills/command/), all cross-refs updated in score-authoring (3), essentials (1), project CLAUDE.md (2 stale refs fixed). Global CLAUDE.md needs manual composer update. Commit 7f5c8a1.
- **F-110 core architecture (mateship-picked):** Designed and TDD-implemented BackpressureController.rejection_reason(), _queue_pending_job(), _start_pending_jobs(), cancel_pending, CLI _handle_pending_response(). All committed by mateship (Spark 5b9d12e+539d12c, Lens d286e07) before Dash could commit. Pipeline efficiency record.
- **Quality gate baseline:** BARE_MAGICMOCK 1455→1463, ASSERTION_LESS_TEST 122→129. Pre-existing drift.

### M4 Progress (Spark)
- **D-023 COMPLETE:** Created `examples/invoice-analysis.yaml` — 4th Wordware demo (3-voice parallel: financial accuracy, compliance, anomaly detection). 5 sheets, 7 validations. Blueprint did 3, Spark did 1. All 4 Wordware demos validate clean.
- **2 new Rosetta pattern examples:** `examples/rosetta/source-triangulation.yaml` (Source Triangulation — claim verification from code/docs/tests, 5 sheets) and `examples/rosetta/shipyard-sequence.yaml` (Shipyard Sequence — build with validation gate, 7 sheets). Total Rosetta examples: 6 (was 4). README updated.
- **Rosetta Score primitives updated:** `scores/the-rosetta-score.yaml` now includes all M1-M4 capabilities in primitives. Existing vocabulary updated with 56 patterns and 10 practiced patterns.
- **Mateship: F-110 pending jobs:** Committed Dash's implementation — `backpressure.py` rejection_reason(), `manager.py` pending job queue/auto-start/cancel, 23 tests. Quality gate baseline updated.
- **Mateship: M4 doc updates:** Committed uncommitted docs — CLI auto-fresh, cost confidence, baton updates, CLAUDE.md skill refs.

### M4 Progress (Codex)
- **14 documentation deliverables across 8 docs (2 sessions):** Session 1 (commit 2b0c379): CLI reference (auto-fresh, cost confidence), score-writing guide (skipped_upstream), daemon guide (MethodNotFoundError, baton capabilities, test count 1,900+), limitations.md (baton test count), examples/README.md (Wordware demos), mateship invoice-analysis.yaml. Session 2: daemon guide (baton transition plan — P0 composer directive, IPC table + daemon.clear_rate_limits, preflight config), configuration-reference (preflight sub-config + use_baton field), limitations.md (transition plan cross-reference), getting-started.md verified accurate.
- **Mateship: invoice-analysis.yaml** — Picked up untracked 4th Wordware comparison demo. Validates clean (5 sheets, 3-voice parallel financial analysis).

### M4 Progress (Lens)
- **Layer 2 error quality COMPLETE:** All output_error() calls in CLI now have hints=. Fixed 8 hintless calls across helpers.py, run.py, pause.py, status.py. Fixed 1 raw console.print("[red]Error:...") in clear validation → output_error with hints. 10 TDD tests. Quality gate baseline 1440→1455 (mateship). Commit d286e07.
- **F-110 COMPLETE (pending state UX):** Mateship pickup of unnamed musician's rate-limit pending implementation + critical fixes. (1) Wired `_start_pending_jobs()` — was defined but never called. Now triggers on manual `clear_rate_limits()` + deferred timer on queue. (2) Added `DaemonJobStatus.PENDING` + `JobMeta` creation so pending jobs appear in `mozart list`. (3) Fixed mypy lambda inference in `_start_pending_jobs`. (4) Updated 9 test files (3 in test_clear_rate_limits.py, 6 in test_m3_pass4_adversarial_breakpoint.py). (5) Documented pending state in cli-reference.md + daemon-guide.md. 23 TDD tests in test_rate_limit_pending.py.

### M4 Progress (Canyon)
- **F-210 RESOLVED (P0 BLOCKER CLEARED):** Cross-sheet context wired through the full baton dispatch pipeline. `AttemptContext.previous_files` added. `BatonAdapter._collect_cross_sheet_context()` reads completed sheets' stdout and workspace file patterns. Wired into `_dispatch_callback()` and `PromptRenderer._build_context()`. Manager passes `config.cross_sheet` through. 21 TDD tests. **Phase 1 baton testing is now unblocked.**
- **F-340 (P3):** Quality gate assertion baseline stale — 6 new assertion-less tests in `test_runner_coverage_gaps.py` and `test_runner_execution_coverage.py`. Not a product bug.

### M4 Progress (Blueprint)
- **F-211 RESOLVED:** Checkpoint sync extended to ALL status-changing events via duck typing. Pre-event capture for CancelJob (deregisters before sync). 18 TDD tests. `adapter.py:_sync_sheet_status()`, `_capture_pre_event_state()`, `_sync_single_sheet()`, `_sync_cancelled_sheets_from_state()`, `_invoke_sync_callback()`.
- **3 Wordware comparison demos:** `examples/contract-generator.yaml` (legal contracts, 5 sheets), `examples/candidate-screening.yaml` (hiring pipeline, 5 sheets), `examples/marketing-content.yaml` (multi-channel content, 6 sheets). All validate clean. D-023 partially complete (Spark may add more).
- **COLLISION RESOLVED (Foundation):** The `_synced_status` state-diff dedup cache from `test_f211_checkpoint_sync.py` is now integrated into the adapter alongside Blueprint's event-type approach. Both are needed: duck typing handles event routing, dedup prevents duplicate callbacks. 16 tests in test_f211_checkpoint_sync.py now pass.

### M4 Progress (Foundation)
- **F-210 mateship completion:** PromptRenderer._build_context() now accepts AttemptContext to populate SheetContext.previous_outputs/previous_files. Manager wired to pass config.cross_sheet to register_job/recover_job. Fixed Canyon's test constructors (CheckpointState, SheetAttemptResult). 21/21 TDD tests pass.
- **F-211 mateship completion:** Added _synced_status dedup cache (prevents duplicate sync callbacks), JobTimeout handler (_sync_all_sheets_for_job), RateLimitExpired handler (_sync_all_sheets_for_instrument), updated _sync_cancelled_sheets_from_state to use dedup. Fixed pre-existing test failure in test_baton_restart_recovery.py. 16/16 + 18/18 TDD tests pass.
- **Critical path unblocked:** Both P0 blockers (F-210, F-211) resolved. Phase 1 baton testing is now possible.

### M4 Progress (Harper)
- **F-450 RESOLVED:** IPC MethodNotFoundError no longer misreported as "conductor not running." Added MethodNotFoundError exception, mapped in _CODE_EXCEPTION_MAP, re-raised with restart guidance in try_daemon_route(), hardened run.py with DaemonError catch. 15 TDD tests. Also updated 2 existing tests. F-181/F-462 resolved.
- **Mateship: D-024 cost accuracy (Circuit):** Committed ClaudeCliBackend JSON token extraction + status.py cost confidence display + quality gate baseline (1391→1396). 17 tests in test_cost_accuracy.py.
- **Mateship: #93 pause-during-retry fix:** Committed _check_pause_signal/_handle_pause_request protocol stubs in sheet.py. Fixed broken test_sheet_execution.py (_MockMixin missing new protocol methods). 5 tests in test_pause_during_retry.py.
- **Assessment: route claude-cli through PluginCliBackend** — already works on baton path (BackendPool creates PluginCliBackend from built-in profile). Legacy runner uses native ClaudeCliBackend but dies with Phase 3 baton transition. Not worth the effort.

### M4 Progress (Forge)
- **Quality gate baseline:** BARE_MAGICMOCK 1396→1440 (44 new from M4 contributors). Unblocks all test runs.
- **Mateship pickup:** Committed Harper's uncommitted #93 (pause-during-retry), F-450 (MethodNotFoundError), and D-024 (cost accuracy) work. All three had code + tests ready in the working tree.
- **#122 FIXED:** Resume gives unclear output when reloading config. Root cause: `await_early_failure()` races with conductor's async status transition, catching stale FAILED status. Fix: removed the poll from conductor-routed resumes entirely. Enhanced direct resume Panel to show previous state as context. 7 TDD tests in test_resume_output_clarity.py. Updated test_cli_run_resume.py (stale await_early_failure mock).

### M4 Progress (Ghost)
- **#103 FIXED:** Auto-detect changed score file on re-run. Added `_should_auto_fresh()` to manager.py — compares score file mtime against registry `completed_at` with 1-second filesystem tolerance. Wired into `submit_job()` — auto-sets `fresh=True` when COMPLETED job's score was modified since last run. 7 TDD tests in test_stale_completed_detection.py. Enhanced job_service.py resume event with `previous_error`/`config_reloaded` context.
- **Resume improvements complete:** #93, #103, #122 all resolved. Roadmap step 50 is done.
- **Test fixes:** Fixed broken test_resume_no_reload_ipc.py and test_conductor_first_routing.py — both patched `await_early_failure` on resume module after Forge's #122 fix removed it.

### M4 Progress (Maverick)
- **#120 RESOLVED:** Fan-in [SKIPPED] placeholder + `skipped_upstream` template variable. Skipped upstream sheets now inject `[SKIPPED]` in `previous_outputs` instead of silent omission. 7 TDD tests, 3 existing tests updated. `context.py`, `templating.py`.
- **F-211 contribution:** Added `_synced_status` cache field (state-diff dedup) to BatonAdapter.__init__. Canyon/Foundation's sync handlers depend on this field for idempotent syncing. 16 TDD tests.
- **#128 verified:** Skip_when fan-out expansion already fixed in `919125e`. Issue ready for closure.
- **Quality gate baseline:** BARE_MAGICMOCK 1375→1391, ASSERTION_LESS_TEST 116→122. Pre-existing drift.

### M4 Progress (Circuit)
- **D-024 COMPLETE (cost accuracy investigation):** Traced full cost pipeline, identified 5 root causes (F-180). ClaudeCliBackend returned zero tokens — CostMixin estimated from stdout chars (10-100x underestimate). Fixed: JSON token extraction (`_extract_tokens_from_json()`) + confidence display (`~$X.XX (est.)` + warning). 17 TDD tests. Commit 4055f0b. Remaining: instrument profile pricing, baton hardcoded pricing, text-mode default.
- **F-181 filed (P2):** Uncommitted F-450 fix in working tree — `MethodNotFoundError` differentiation across detect.py, exceptions.py, ipc/errors.py + 14 tests. Needs mateship pickup.
- **F-182 filed (P2):** Uncommitted resume improvements (#93, #103, #122) in working tree — auto-fresh detection, resume output clarity, early-failure skip. Needs mateship pickup.

### M4 Progress (Warden)
- **F-250 RESOLVED (P2):** Cross-sheet capture_files credential redaction. Applied `redact_credentials()` to file content on both legacy runner (`context.py:295`) and baton adapter (`adapter.py:772`). Same error class as F-003, F-135. 8 TDD tests.
- **F-251 RESOLVED (P2):** Baton cross-sheet [SKIPPED] placeholder parity with legacy runner (#120). Added SKIPPED status check at `adapter.py:730`. Updated existing test assertion. 4 TDD tests.
- **M4 safety audit:** 10 areas across 20 changed source files. F-210 cross-sheet credential flow safe (musician redacts stdout at capture). F-211 checkpoint sync architecturally clean (duck typing + state-diff dedup). F-110 pending jobs properly bounded. Auto-fresh TOCTOU benign. Cost accuracy JSON parsing defensive. MethodNotFoundError clean. 2 gaps found (F-250, F-251), both fixed.

### M4 Progress (Adversary)
- **M4 adversarial tests COMPLETE:** 55 new tests in `test_m4_adversarial_adversary.py`. 8 test classes targeting F-441 strictness (20 tests across 14 model families), F-211 sync dedup lifecycle (4 tests), auto-fresh boundaries (9 tests), cross-sheet context (6 tests), credential redaction defensive pattern (5 tests), real score patterns (7 tests), state mapping completeness (2 tests), feature interactions (4 tests).
- **F-470 filed (P2):** BatonAdapter._synced_status memory leak — cache not cleaned on deregister_job(). Grows O(total_sheets_ever). Same error class as F-129, F-077.
- **F-471 filed (P2):** Pending jobs lost on daemon restart — _pending_jobs is in-memory only. PENDING registry entries become orphans after restart.
- **F-441 verification:** Zero code-level bugs. All 51 config models correctly reject unknown fields.
- **Meditation written:** `meditations/adversary.md`

### M4 Progress (Oracle)
- **F-300 filed (P2):** Resource anomaly patterns show zero effectiveness differentiation.
- **F-301 filed (P2):** instrument_name null for 30,229 of 30,232 patterns.
- **F-302 filed (P2):** Stale detection ceiling unchanged across 3 movements (F-097).

### M3 Summary (48 commits, 28 musicians, 584 new tests)
- **Quality gate PASS:** 10,981 tests (10,397→10,981), mypy clean, ruff clean, flowspec 0 critical.
- **Codebase:** 97,424 source lines, 315 test files, 150/197 tasks (76%). M0-M3 ALL COMPLETE.
- **10 critical fixes:** F-152 (dispatch guard), F-009/F-144 (semantic tags), F-145 (concert chaining), F-158 (prompt renderer), F-112 (auto-resume), F-150 (model override), F-151 (instrument observability), F-160 (wait cap), F-200/F-201 (clear specificity), F-440 (state sync).
- **Baton verification:** 148 invariant tests (Theorem), 258 adversarial tests (Breakpoint), 67 Phase 1 adversarial (Adversary), 21 litmus tests (Litmus). Zero bugs found in M3 code. Architecturally ready for Phase 1 testing.
- **UX polish:** Rate limit time-remaining UX (Dash), stale state feedback (Dash), rejection hints (Lens), schema error hints (Journey), stop safety guard (Ghost/Circuit), clear-rate-limits CLI (Harper), instrument status column (Circuit).
- **Documentation:** 9 deliverables across 5 docs (Codex). "job"→"score" terminology across CLI+docs (Newcomer, Guide). README CLI reference overhauled (Compass). 19/19 multi-stage examples have movements declarations (Guide+Spark).
- **Safety audit clean:** Zero new critical findings. Credential redaction intact. Shell execution paths unchanged (Sentinel+Warden).
- **Learning store differentiation:** Avg effectiveness 0.5000→0.5088. Validated tier 238 (+31%). F-009/F-144 fix is the ignition key (Oracle).
- **Three-phase pattern confirmed intrinsic (3rd consecutive):** Build→Verify→Review, same proportions as M2, self-organizing (Tempo).
- **GitHub issues closed:** #155, #154, #153, #139, #94. Issues verified: #151, #150, #149, #112.
- **3 new findings:** F-210 (P1, cross-sheet context missing — BLOCKS Phase 1), F-211 (P2, checkpoint sync gaps), F-212 (P3, spec budget gating).
- **F-450 filed (P2):** IPC "Method not found" misreported as "conductor not running." New IPC methods on stale conductor get wrong error class.
- **Cost fiction:** Now $0.17 for 125 sheets — plausible but wrong by 100-1000x. More dangerous than the obviously-wrong $0.00 (Ember, Newcomer).

### M4 Progress (Oracle)
- **Comprehensive M4 metrics analysis:** Codebase 98,247 source lines (+0.8%), 327 test files, mypy/ruff clean. 18 commits from 12 musicians. 39% mateship rate (all-time high).
- **Learning store deep analysis:** 30,232 patterns. Warm tier exploded: ~182 (M3) → 3,185 (M4). Avg effectiveness 0.5091. Validated: 278 (+17%). Resource anomaly patterns (5,315) remain uniformly cold at 0.5000 — F-300 filed.
- **Execution performance:** 239,585 total, 31,188 completed (99.6% success rate among terminal). p99 still 30.5min (stale detection ceiling). 466 M4 executions.
- **Instrument usage:** 97.6% claude-sonnet-4-5-20250929. Only 3 of 30,232 patterns have instrument_name set (F-301).
- **Predictive model:** Baton activation at M7 at current pace (one serial step/movement). Self-sustaining intelligence threshold (~1,000 validated patterns) projected M7.
- **3 findings filed:** F-300 (resource anomaly pipeline dark, P2), F-301 (instrument_name 99.99% null, P3), F-302 (stale detection ceiling unchanged, P2).

### M4 Progress (Bedrock)
- **D-025 COMPLETE (F-097 timeout config):** Verified idle_timeout_seconds already raised to 7200 in generate-v3.py:443 and score:3963. Marked 2 tasks complete. Updated both F-097 entries in FINDINGS.md to RESOLVED.
- **Quality gate baseline:** BARE_MAGICMOCK 1463→1482. 19 new from test_sheet_execution_extended.py (12), test_stale_state_feedback.py (4), test_top_error_ux.py (2), test_top_error_ux.py (1). Pre-existing M4 drift from uncommitted test batches.
- **Milestone verification (M4):** Conductor-clone 19/20 (95%). M0-M3 ALL 100%. M4 15/19 (79%). M5 17/18 (94%). M6 1/8 (12%). M7 1/11 (9%). Composer 28/37 (76%). Total: 181/218 (83%, up from 158/207=76% at M3 gate). +23 completions offset by +11 new tasks.
- **M4 commits:** 18 commits from 12 unique musicians. 41 source/test files changed, 4,765 insertions, 117 deletions. Codebase: 98,247 source lines (up from 97,424). 327 test files (up from 315).
- **GitHub issues:** 47 open. Issues fixed in M4 ready for Prism/Axiom verification: #122 (Forge eefd518), #120 (Maverick a77aa35), #93 (Harper b4c660b), #103 (Ghost d67403c). #128 already fixed in 919125e.
- **Mateship:** No uncommitted source code. 14 memory files modified (dreamer artifacts between movements). Clean working tree for source/tests.
- **Ground:** TASKS.md cleaned — 2 F-097 timeout tasks marked complete with evidence.

### M4 Progress (Breakpoint)
- **57 adversarial tests across 10 attack surfaces:** auto-fresh tolerance boundary (8), pending job edge cases (3), cross-sheet SKIPPED/FAILED behavior (7), max_chars boundary (3), lookback edge cases (4), MethodNotFoundError round-trip (7), credential redaction defensive pattern (7), capture files stale/binary/pattern (5), baton/legacy parity (2), rejection reason boundaries (6).
- **F-202 filed (P3):** Baton/legacy parity gap — FAILED sheets with stdout included in cross-sheet context on legacy path but excluded on baton path. Same error class as F-251 (baton/legacy behavioral divergence). Not a crash or data loss — a behavioral difference that surfaces when use_baton becomes default.
- **Mateship:** Committed Litmus's uncommitted 7 new M4 litmus tests (651 lines, tests 32-38 in test catalog). All 118 litmus tests pass.
- **Zero bugs found in M4 code.** The codebase continues to harden. Bug surface has shifted from code-level bugs to architectural parity between the two execution paths.

### M4 Progress (Litmus)
- **7 intelligence layer tests (categories 32-38):** F-210 cross-sheet context in baton prompts, #120 SKIPPED upstream visibility, #103 auto-fresh detection, F-110 backpressure rejection intelligence, F-250 cross-sheet credential redaction, F-450 MethodNotFoundError differentiation, D-024 cost JSON extraction. All tests use WITH/WITHOUT methodology — validates features make agents more effective, not just code correctness.
- **7 more tests (categories 39-45, session 2):** F-255.3 PluginCliBackend MCP gap (field exists but unused — documents production bug), F-211 duck typing sync coverage (9 event types verified), F-211 state-diff dedup (prevents duplicate callbacks), F-202 baton/legacy FAILED sheet parity gap (documented), F-255.1 _load_checkpoint from daemon DB (verified fix), F-110 FIFO queue ordering (dict insertion order), F-250+F-210 credential pipeline E2E (traces credential through full pipeline).
- **136 total litmus tests pass.** Coverage map: 36 M1, 38 M2, 21 M3, 25 M4 (tests 32-45).
- **F-270 filed (P3):** test_instrument_models.py::test_extra_fields_ignored_by_default fails — stale test after extra='forbid' added to instrument models. Pre-existing, not caused by litmus changes.
- **Quality gate baseline updated:** BARE_MAGICMOCK 1482→1519 (37 new from M4 contributors).
- **Key finding:** F-255.3 MCP gap is the canonical litmus case — field defined, profile set, command builder ignores it. 1500 tests pass, 80 child processes in production. Correct code ≠ effective system.
- **Mateship (session 1):** Work committed by Breakpoint. First time another musician picked up and committed litmus tests. Pipeline efficiency record.

### M4 Progress (Sentinel)
- **Pass 1:** Independent Warden verification, full 18-commit audit, F-137 RESOLVED (pygments 2.20.0 pinned + installed).
- **Pass 2:** 6 new commits reviewed (Theorem, Journey, Prism, Axiom, Litmus). Zero new attack surfaces.
- **_load_checkpoint change APPROVED:** Journey's daemon DB migration (8c95f02) is security-positive — removes file-based state, uses parameterized SQL. Reduced attack surface.
- **F-271 (MCP gap) INDEPENDENTLY CONFIRMED:** PluginCliBackend._build_command() at cli_backend.py:169-232 never reads mcp_config_flag. P1 — affects all baton-managed sheets.
- **F-441 fix VERIFIED:** Working tree `extra="forbid"` changes are correct and comprehensive. 54 adversarial tests pass. Needs mateship commit.
- **All 9 credential redaction points intact.** All 4 shell execution paths protected. Fifth consecutive movement — zero regressions.
- **Dashboard E2E failure noted:** test_job_start_with_inline_config has async mock issue. Pre-existing, needs mateship fix.
- **Piecemeal credential redaction pattern (fourth occurrence):** F-250 is same error class as F-003, F-135, F-160. Pattern caught in routine audit before production.

### M4 Progress (Atlas)
- **Seventh strategic alignment assessment.** Verified 18 commits from 12 musicians. Both P0 blockers resolved (F-210, F-211). 182/220 tasks complete (83%). Codebase: 98,272 source lines, 327 test files, 11,140 tests collected. mypy clean. ruff lint regression fixed (import sorting in context.py).
- **Critical path diagnosis:** The serial path advanced exactly one step this movement (F-210 resolved). Fourth consecutive movement of one-step-per-movement pace. At this rate, baton Phase 1 testing lands in M5, flip to default in M6, demo in M7+. The product thesis remains unproven.
- **Mateship rate 39% (all-time high).** 7 of 18 M4 commits were mateship pickups. The pipeline has evolved from anti-pattern fix to primary collaboration mechanism. Foundation (F-210 completion), Forge (3 mateship pickups), Harper (3 mateship pickups), Spark (2 mateship pickups) drove this.
- **Wordware demos break the visibility deadlock.** D-023 is the first demo-class deliverable in 8+ movements. 4 comparison demos validate clean and can demonstrate Mozart to external audiences TODAY using the legacy runner. The Lovable demo requires the baton — the Wordware demos do not.
- **STATUS.md updated:** M3 → M4 current section. Test count 10,981→11,140, source lines 97,424→98,272, milestone percentages updated, M4 resolutions listed.
- **Ruff lint fix:** `src/mozart/execution/runner/context.py:30-33` — import block un-sorted (I001). Fixed import ordering. Likely introduced by Canyon/Foundation F-210 mateship work touching this file.

### M4 Progress (Theorem)
- **24 new property-based invariant tests across 10 invariant families:** Config strictness totality (F-441 — ALL 50+ config models reject unknown fields), IPC error code mapping bijectivity, token extraction defensive parsing (D-024), auto-fresh monotonicity (#103), MethodNotFoundError type preservation (F-450), config default construction, Pydantic field bounds enforcement, retry delay clamping, regex compilation safety, cost limit non-negativity.
- **Total invariant tests: 181 across 8 files.** M1-M3: 148, M4 pass 2: 9, M4 pass 3: 24.
- **First system-wide invariant pass.** Previous passes focused on baton-internal properties. Pass 3 extends to ALL config models (extra='forbid' totality), IPC error mapping, and backend token extraction. The scope has shifted from proving baton correctness to proving system correctness.
- **Zero bugs found.** All 10 invariant families hold. F-441 config strictness is mathematically verified — every config model in the codebase rejects unknown YAML fields.
- **Meditation written:** `workspaces/v1-beta-v3/meditations/theorem.md`

### M4 Directives (North, D-020 through D-025)
- D-020: Canyon → F-210 cross-sheet context (P0) — **RESOLVED** (Canyon 748335f, Foundation 601bc8c)
- D-021: Foundation → Phase 1 baton testing (P0, UNBLOCKED by D-020) — not yet executed
- D-022: Guide + Codex → Lovable demo score (P0) — **STILL AT ZERO**
- D-023: Spark + Blueprint → Wordware comparison demos (P1) — **COMPLETE** (4 demos)
- D-024: Circuit → cost accuracy investigation (P1) — **COMPLETE** (Circuit 4055f0b)
- D-025: Bedrock → F-097 timeout config (P1) — **COMPLETE** (verified by Bedrock)

## Coordination Notes (Active)
- **CRITICAL PATH (UPDATED M4):** ~~F-210 fix~~ DONE → Phase 1 baton test (--conductor-clone) → fix Phase 1 issues → flip use_baton default → demo → release. The path is serial. D-021 (Phase 1 baton testing) is now unblocked.
- **F-210:** RESOLVED (Canyon 748335f, Foundation 601bc8c). Cross-sheet context wired through adapter._collect_cross_sheet_context() + PromptRenderer._build_context(). 21 TDD tests.
- **F-211:** RESOLVED (Blueprint 5af7dbc, Foundation 601bc8c). Checkpoint sync for all 6 event types. State-diff dedup cache prevents duplicate callbacks.
- **F-212 (P3):** Baton PromptRenderer missing spec budget gating. Low priority — large context windows.
- **Encapsulation violation (P3):** adapter.py:688,725,1164 — 3 private member accesses. Needs public API on BatonCore. Functional correctness unaffected.
- **DEMO (P0 — EXISTENTIAL):** 9+ movements at zero. D-022 (Lovable demo) not started. Wordware demos COMPLETE (4). Lovable still blocked on baton Phase 1 → Phase 2 → Phase 3.
- **NEXT SERIAL STEP:** D-021 — Phase 1 baton testing with --conductor-clone. This is the critical path. One musician, serial execution, real sheets through the baton.

### M3→M4 Coordination Map (Weaver)
**SERIAL:** F-210 fix → Phase 1 test → fix issues → flip default → demo score.
**PARALLEL:** Docs, examples modernization, fan-out edge cases (#120, #119, #128), resume improvements (#93, #103, #122), Wordware demos, Rosetta update, skill rename, Gemini CLI assignment.

## Top Risks
1. **Phase 1 baton testing NOT STARTED (CRITICAL).** F-210 resolved. Path unblocked. No one has started. Fourth consecutive movement of one-step-per-movement pace on the serial critical path.
2. **Demo partially broken (HIGH — improving).** Wordware demos (4) exist and validate — first progress in 8+ movements. Lovable demo still at zero, blocked on baton Phase 2.
3. **Baton untested live (CRITICAL — NOW UNBLOCKED).** 1,500+ tests, never run a real sheet. F-210 and F-211 resolved. Nothing prevents Phase 1 testing except someone dedicating time to it.
4. **Resource anomaly pipeline dark (P2).** 5,315 patterns at 0.5000. Only semantic patterns differentiating. 17.6% of pattern corpus contributes zero intelligence signal. F-300.
5. **Cost fiction (P2).** Now shows confidence indicators (D-024). Still 10-100x underestimate for real work.
6. **Stale detection ceiling (P2).** p99 at 30.5min across 3 movements. F-097 timeout increase unclaimed since M1. 10-minute fix.

### M4 Progress (Axiom, Pass 2)
- **F-441 mateship completion:** Picked up uncommitted `extra='forbid'` on 45 remaining config models (7 modules). All 51 BaseModel classes in core/config/ now have extra='forbid'. Verified empirically (4 test cases) and against all 37 example scores (36/37 pass — 1 is a generator config, not a score).
- **Dashboard E2E fix:** Pre-existing AsyncMock bug + F-441 fixture incompatibility. 4 process mocks fixed, sample_yaml_config updated. 9/9 tests pass.
- **GitHub issues closed:** #128 (skip_when fan-out), #93 (pause-during-retry). #122, #120, #103 already closed.
- **Verified 3 M4 commits:** Journey (schema hints + backward compat), Sentinel (security audit), Litmus (18 tests).
- **Quality gates:** mypy clean, ruff clean, 4,300+ tests verified, 0 failures.
- **Meditation written.**

## Blockers (Active Only)
- **Phase 1 baton testing:** Unblocked (F-210 resolved). Needs one musician to dedicate a full session. Foundation recommended (deepest baton context).

## Roster (32 musicians, equal peers)
Forge, Captain, Circuit, Harper, Breakpoint, Weaver, Dash, Journey, Lens, Warden,
Tempo, Litmus, Blueprint, Foundation, Oracle, Ghost, North, Compass, Canyon, Bedrock,
Maverick, Codex, Guide, Atlas, Spark, Theorem, Sentinel, Prism, Axiom, Ember,
Newcomer, Adversary

## Cold Archive

### Movement 3 (Complete, 2026-04-04)
Movement 3 was the UX and polish movement — 48 commits from 28 musicians, the mateship pipeline operating at an all-time high of 33%. Ten critical bugs were fixed, 584 tests added, and every M3 milestone completed. The baton was mathematically verified from four independent angles (invariant proofs, adversarial testing, litmus validation, Phase 1 adversarial) with zero bugs found in new code.

The movement's signature was the mateship pipeline maturing into institutional behavior. Foundation picked up 4 commits of others' work, Bedrock picked up 2, and six other musicians completed the chain. The uncommitted work anti-pattern — which plagued early movements — was countered so reliably that it became a collaboration mechanism rather than a problem to fix. Weaver's integration surface audit mapped 9 surfaces, found 7 correctly wired, and identified F-210 as the sole remaining engineering blocker.

But the movement also deepened the orchestra's central tension. Prism articulated it most sharply: "32 parallel musicians can't execute a serial critical path." The baton had been "ready" since M2. The demo had been P0 for 8 movements. North acknowledged his own failure — zero M3 output until the final strategic report — and issued specific M4 directives with named musicians and concrete deliverables. The lesson: directives must specify the deliverable, not the direction. "Activate the baton" produced readiness, not activation.

The learning store showed its first real differentiation — effectiveness shifting from the uniform 0.5000 to a meaningful distribution. Oracle projected self-sustaining intelligence by M6, contingent on the baton actually running. Ember's experiential reviews confirmed: 7 major features mathematically verified, zero experientially verified. The see/know gap was maximal. The infrastructure was extraordinary. What was missing was the courage — and the serial execution structure — to turn it on.

### Movement 2 (Complete, 2026-04-04)
A single 15-hour wave proving the orchestra's building precision. 60 commits, 28 musicians, zero merge conflicts. The baton was completed (23/23 tasks), conductor-clone reached full coverage, product surface healed from 2/37 to 38/38 validating examples. Reactive serial convergence finally worked — Step 28-29 landed through the mateship pipeline. Four independent verification methodologies confirmed the code. But the movement crystallized the parallel-vs-serial tension: 1,120 baton tests, never run a real sheet. Demo at zero for 6+ movements. Captain and Atlas independently concluded: assign ONE musician to the serial path.

### Movement 1 (Cycles 1-7)
The first building movement. 32 musicians, 42 commits, zero merge conflicts. Instrument plugin system shipped end-to-end. Baton terminal guard completed across all 19 transitions. Three natural rhythms emerged (build, convergence, verification). Mateship matured through Harper's mass pickups. The finding→fix pipeline became the orchestra's strongest institutional mechanism.

### Movement 0 (Stabilization)
Foundation laid: learning store fixes, critical bug resolution, dead code removal. Quality gates established.

### M4 Progress (Journey)
- **Exploratory verification of M4 UX features:** Validated 44/44 example scores (0 failures). Verified 7 user-facing features from real-user perspective: auto-fresh detection, resume output clarity, pending jobs UX, cost confidence display, fan-in skipped upstream, cross-sheet safety, MethodNotFoundError. All behave as users would expect. Zero findings.
- **Wordware demos:** 4/4 PASSED (invoice-analysis, contract-generator, candidate-screening, marketing-content). Ready for external audiences. First demo-class deliverables in 8+ movements that work TODAY on legacy runner.
- **Rosetta patterns:** 6/6 PASSED (2 new: source-triangulation, shipyard-sequence). Both teach unique orchestration techniques from real Rosetta iterations.


### M4 Progress (Theorem)
- **9 property-based invariant tests for M4 features:** `test_baton_invariants_m4_pass2.py` (558 lines). Covers F-210 (cross-sheet context), F-211 (checkpoint sync), F-110 (pending jobs), #103 (auto-fresh), F-200/F-201 (rate limit clearing). Invariants 66-74: lookback bounds, max_chars truncation, credential idempotence, SKIPPED consistency, sync idempotence, rejection determinism, FIFO ordering, timestamp transitivity, clear totality. All tests pass with hypothesis. Total: 157 invariant tests (was 148). Commit 6cf6fe9.
- **Baton production verification:** Confirmed baton is running in production (conductor log shows baton.sheet.completed). Orchestra v3 has 150 completed sheets, 4 in progress, 552 pending. Phase 1 testing is COMPLETE — baton has executed 150+ real sheets successfully.

### M4 Progress (Prism)
- **M4 architectural review:** Verified 7 claimed fixes (#120, #122, #103, F-210, F-211, F-450, F-137). All correct. F-210/F-211 resolutions cleared P0 blockers for Phase 1 baton testing.
- **F-400 filed (P1):** Uncommitted work in `manager.py` — `_load_checkpoint()` switched to daemon-registry-based loading. Architecturally correct (daemon as truth, F-254 principle) but incomplete (no migration, no tests). Needs commitment with migration logic.
- **Geometry problem persists:** Critical path advanced one step (F-210). Fourth consecutive movement at this pace. Baton ready for Phase 1, nobody starting. Not an engineering problem anymore — it's a governance problem.
- **F-254 diagnosis:** The hidden bomb. Enabling `use_baton: true` kills ALL in-progress legacy jobs. Dual-state architecture (workspace `.mozart-state.db` vs daemon registry) creates the gap. Architectural principle clear (daemon is truth), migration path unclear. Requires composer/co-composer governance decision.
- **Mateship at 39% (all-time high):** Pipeline is primary collaboration mechanism now. Foundation/Forge/Harper/Spark/Breakpoint all picked up others' work.

### M4 Progress (Axiom)
- **M4 fix verification COMPLETE:** Verified 5 fixes (#122, #120, #93, #103, #128) through invariant analysis. All correct. 23 edge cases analyzed. F-202 (baton/legacy FAILED sheet parity) already filed by Breakpoint, documented in verification.
- **F-441 filed (P0):** Pydantic silently accepts unknown YAML fields — all 37 config models lack `extra='forbid'`. Composer directive M5. Issue #156 confirmed. Full reproducer + impact analysis in movement-4/axiom.md.
- **GitHub issue verification ready:** #122, #120, #93, #103, #128 all verified correct with evidence. Ready for closure.

### M4 Progress (Ember)
- **Experiential review complete:** Full walkthrough of all commands, 43/44 examples validate, 6/6 Rosetta scores validate. mypy clean, ruff clean.
- **F-450 RESOLVED verified:** `clear-rate-limits` now correctly reports rate limit status instead of "conductor not running." Four movements of tracking this bug — confirmed fixed on HEAD.
- **F-441 RESOLVED verified:** Unknown YAML fields now rejected with clear errors and hints. Tested at JobConfig, SheetConfig, PromptConfig levels. `instrument_config` correctly open (dict[str, Any]).
- **Cost display honesty verified:** `$0.00 (est.)` with "10-100x higher" disclaimer + `cost_confidence: 0.7` in JSON. Most important UX change of any movement — honest uncertainty over plausible lies.
- **3 findings filed:** F-451 (diagnose can't find completed jobs that status can), F-452 (list --json null cost vs status --json structured cost), F-453 (dashboard e2e cross-test state leakage).
- **Meditation written:** `meditations/ember.md`

### M4 Progress (Prism — Pass 2)
- **F-441 architectural review:** extra='forbid' across 45+ config models is correct and comprehensive for score YAML. All 43 example scores validate. Backward compat preserved via strip_computed_fields(). UX improved with Journey's unknown-field hints.
- **Quality gate fixed:** Bare MagicMock drift in test_top_error_ux.py (2 instances → spec'd). Baseline 1519→1517.
- **Rosetta score fixed:** instrument_fallbacks field commented out (doesn't exist on JobConfig yet — fails with extra='forbid').
- **3 findings filed:** F-430 (ValidationRule.sheet docstring/code mismatch, P3), F-431 (DaemonConfig/ProfilerConfig missing extra='forbid' — same bug class as F-441, P2), F-432 (iterative-dev-loop-config.yaml is a generator config, not a score — fails validation, P2).
- **Test suite:** 11,332 passed (up 351 from M3 gate). mypy/ruff clean.
- **Observation:** Input strictness (config validation) advancing while output verification (baton real-run testing) stalls. Five movements of "baton ready, Phase 1 not started." The integration cliff grows taller with every test that verifies the code without running the product.
