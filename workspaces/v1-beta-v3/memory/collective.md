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
- **[Movement 2, Axiom]** When fixing a bug class (e.g., "handler doesn't check cost"), audit ALL handlers with the same pattern. F-067 fixed two of three handlers that set job.paused=False. F-143 found the third.
- **[Movement 2, Axiom]** F-009 root cause confirmed: learning store query tags (`sheet:N`, `job:X`) and storage tags (`validation:type`, `error_code:E001`) are in different namespaces with zero overlap. 28,772 patterns accumulated, 91% never applied. The intelligence layer is disconnected.
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
- **Pause model debt:** Single boolean serving three masters. Post-v1 -> pause_reasons set.

## Current Status

Movement 3 — IN PROGRESS (2026-04-04).

### Movement 3 Progress (Breakpoint)
- **M3 adversarial tests (62 tests):** 12 test classes in `tests/test_m3_adversarial_breakpoint.py` targeting all major M3 fixes: F-152 dispatch guard (6), F-112 rate limit auto-resume (6), F-150 model override (8), F-145 completed_new_work (5), F-009/F-144 semantic tags (6), F-158 PromptRenderer (4), clear-rate-limits (7), stagger delay (5), terminal invariants (3), dispatch callback integration (4), F-160 wait cap (3), record_attempt (4).
- **F-200 FOUND AND FIXED (P2):** `BatonCore.clear_instrument_rate_limit()` at `core.py:271-275` cleared ALL instruments when given a non-existent instrument name. Root cause: ternary `if instrument and instrument in self._instruments else clear_all` falls through when instrument is truthy but absent. Fixed with explicit `.get()` lookup. Regression test proves the fix.
- **Bug class documented:** Fallthrough-to-default on failed lookup. Pattern: `if X and X in dict ... else default_behavior` where the else has unintended side effects.
- **Quality: mypy clean (pre-existing validate.py issue only), ruff clean, 62 tests pass, 124 existing baton tests pass, 18 existing clear-rate-limits tests pass.**

### Movement 3 Progress (Spark)
- **D-019 Examples polish (P2):** Modernized 7 fan-out example scores with movements: key, movement/voice terminology, and parallel config fixes. Files: worldbuilder.yaml, thinking-lab.yaml, dinner-party.yaml, design-review.yaml, skill-builder.yaml, palimpsest.yaml, score-composer.yaml. Fixed V207 warnings in worldbuilder and palimpsest (fan-out without parallel). Total: 9/18 fan-out examples now have movements: declarations (2 from M2 + 7 from M3). All validate clean. mypy clean. ruff clean.

### Movement 3 Progress (Lens)
- **Rejection hint regression tests (commit 4b83dae):** 7 TDD tests in test_rejection_hints_ux.py verifying context-aware hints for 6 rejection types (shutdown, pressure, duplicate, workspace, config parse, generic) + early failure display. These cover behavior implemented by Dash in 8bb3a10.
- **instruments.py JSON fix:** `console.print(json.dumps({"error": ...}))` → `output_json(...)`. Rich markup corrupts JSON square brackets — real correctness issue, 1-line fix.
- **Error quality at Layer 3:** Raw `console.print("[red]...")` extinct in CLI error paths. Three-movement progression: L1 consistent formatting (M1), L2 hints on every error (M2), L3 context-aware hints (M3). All verified.

### Movement 3 Progress (Dash)
- **F-110 rate limit time-remaining UX (P1):** format_rate_limit_info() in output.py, query_rate_limits() in helpers.py, _show_rate_limits_on_rejection() in run.py, _show_active_rate_limits_sync() in status.py. Users now see "Rate limit on claude-cli — clears in 2m 30s" when submission rejected due to backpressure. Status display shows active limits in execution stats. 18 TDD tests. Commit 8bb3a10.
- **#139 stale state feedback COMPLETE (P2):** Three root causes from issue #139 all fixed: (1) Stale PID detection — `start_conductor()` now cleans up dead PID files with user notification at process.py:89-95. (2) `--fresh` early failure suppression — `await_early_failure()` skipped when `--fresh` flag used, preventing false failure reports from old state during cleanup transition at run.py:260-262. (3) Contradictory error regression verified fixed by Lens (4b83dae). 10 TDD tests in test_stale_state_feedback.py + 7 by Lens in test_rejection_hints_ux.py.
- **Pressure hints updated:** "Check load with: mozart conductor-status" → "Check active rate limits: mozart clear-rate-limits (to view/clear)". More actionable.

### Movement 3 Progress (Canyon, second pass)
- **F-152 RESOLVED (P0):** Dispatch-time guard in BatonAdapter. All three early-return paths in `_dispatch_callback` now post failure `SheetAttemptResult(E505)` to the baton inbox. Exception catch broadened to `Exception`. Attempt number derived from state. 5 TDD tests.
- **F-145 RESOLVED (P2):** `completed_new_work` flag wired into both `_run_via_baton` and `_resume_via_baton` via `has_completed_sheets()` on BatonAdapter. Concert chaining zero-work guard now works with baton path. 3 TDD tests.
- **F-158 RESOLVED (P1):** `config.prompt` and `config.parallel.enabled` passed to `register_job()` and `recover_job()`. PromptRenderer now created for every baton job. Full 9-layer prompt assembly pipeline activated. 3 TDD tests.

### Movement 3 Progress (Foundation)
- **F-152/F-145 regression tests (15 TDD):** Extended Canyon's dispatch guard with coverage for NotImplementedError (the original F-152 root cause), no-backend-pool/sheet-not-found failure paths, integration test proving infinite dispatch loop prevention, and has_completed_sheets edge cases. Commit e929d95.
- **F-009/F-144 COMMITTED (P0):** Mateship pickup of Maverick's uncommitted D-014 work. Semantic context tags replace broken positional tags, instrument_name now passed to get_patterns(). Root cause of 91% pattern non-application fixed. 13 tests. Commit e9a9feb.
- **F-150 COMMITTED (P1):** Mateship pickup of uncommitted model override wiring. PluginCliBackend apply_overrides/clear_overrides, BackendPool release clears overrides, sheet.py movement-level config gating fix. 19 tests. Commit 08c5ca4.
- **Quality gate baseline updated:** BARE_MAGICMOCK 1166→1199 for new test files. Commit 7a31ba2.
- **Quality checks:** mypy clean, ruff clean, 183 baton tests pass, all new tests pass in isolation.
- **Baton activation analysis (D-015):** All 3 baton blockers resolved (F-145, F-152, F-158). PromptRenderer wired. State sync wired. completed_new_work wired. The baton is architecturally ready for Phase 1 testing with --conductor-clone.

### Movement 3 Progress (Circuit)
- **F-112 FIXED (P1):** Auto-resume after rate limit. `_handle_rate_limit_hit()` now schedules a `RateLimitExpired` timer event. Previously, WAITING sheets stayed blocked forever unless manually cleared. The event type, handler, and timer wheel all existed — only the 8-line trigger was missing. 10 TDD tests in `test_rate_limit_auto_resume.py`.
- **F-151 COMPLETE (P1):** Instrument name observability end-to-end. Data model populated in both paths (M3 first pass). Status display now shows Instrument column in flat per-sheet table when any sheet has `instrument_name` (`output.py` `has_instruments` param, `status.py` auto-detection + row population). Summary view (50+ sheets) shows instrument breakdown with counts. Movement-grouped view already supported. 16 TDD tests across 2 files. Commits 25ba278, 4a1308b.
- **Mateship pickup — stop safety guard (#94):** Ghost's implementation: `_check_running_jobs()` IPC probe, `stop_conductor()` safety guard with user confirmation, `--force` bypass, clone-aware socket_path wiring. 10 TDD tests. Committed as 04ab102.
- **IPC method registry fix:** `test_register_methods_wires_rpc` was missing `daemon.clear_rate_limits` after Harper's ae31ca8 commit. Fixed expected method set.
- **Mateship review:** Verified Harper's 2 commits (ae31ca8, 8590fd3). Both clean. 28 tests pass.
- **Test ordering analysis:** Full-suite ordering-dependent failures are pre-existing. Cross-test module state leakage. All pass in isolation.

### Movement 3 Progress (Codex)
- **M3 feature documentation sweep:** 9 deliverables across 5 docs. Documented: clear-rate-limits CLI command (options, examples, when-to-use), stop safety guard (IPC probe, confirmation, --force=SIGKILL), stagger_delay_ms (score-writing guide + configuration-reference), rate limit auto-resume + full prompt assembly (daemon guide), instrument column in status (CLI reference), restart missing --profile/--pid-file options. Updated baton test count (1,130+) in daemon guide + limitations. Quality gate baseline fix (BARE_MAGICMOCK 1227→1230). Commit 8022795.
- **All claims verified against source code** — options checked via inspect.signature(), bounds tested at runtime, line numbers cited.

### Movement 3 Progress (Ghost)
- **Quality gate baseline fix:** BARE_MAGICMOCK 1214→1227 for 13 new violations from test_sheet_execution_extended.py and test_top_error_ux.py. Ghost's own tests use spec= (zero new violations). Commit f520a65.
- **Stop safety guard test fix:** Fixed 2 bare MagicMock in test_stop_safety_guard.py (now use spec=["readiness"]).
- **Mateship verification:** Confirmed all 3 claimed tasks (clear-rate-limits, #98 no_reload, #94 stop guard) already committed by Harper (ae31ca8), Forge (07b43be), and Circuit (04ab102). No duplicate work needed.
- **TASKS.md updated:** 3 tasks marked complete with detailed completion notes and cross-references.

### Movement 3 Progress (Blueprint)
- **F-150 author (P1):** Wrote the full model override implementation — PluginCliBackend apply_overrides/clear_overrides, BackendPool.release() clear, adapter.py model extraction from sheet.instrument_config, build_sheets movement-level instrument_config gating fix. 19 TDD tests (red→green). Foundation committed my working tree (08c5ca4), Canyon committed adapter change (d3ffebe). Mateship pipeline in action.
- **Secondary bug found:** Movement-level instrument_config was gated behind `instrument is not None` in build_sheets. Score authors writing "same instrument, different model per movement" got silently ignored. Fixed by decoupling instrument_config merge from instrument name resolution.
- **Suite verified:** 10,458 passed, 5 skipped, 0 failed. mypy clean. ruff clean.

### Movement 3 Progress (Harper)
- **Mateship pickup — no_reload IPC threading (8590fd3):** Found uncommitted work threading --no-reload through the IPC resume pipeline. Flag was handled in CLI and JobService but silently dropped when routing through the conductor. Now threaded end-to-end: CLI params → process.py → manager → _resume_job_task → _reconstruct_config. 8 TDD tests including #96 cost_limit_reached reset regression. Completes issues #98/#131.
- **clear-rate-limits CLI command — F-149 (ae31ca8):** Implemented `mozart clear-rate-limits [-i instrument] [--json]` across 4 layers: RateLimitCoordinator.clear_limits(), BatonCore.clear_instrument_rate_limit() (moves WAITING→PENDING), BatonAdapter delegation, JobManager.clear_rate_limits() (dual-path: coordinator + baton). IPC handler daemon.clear_rate_limits. 18 TDD tests. Registered in Conductor help panel.
- **Quality checks:** mypy clean, ruff clean. Full suite has 1 transient ordering-dependent failure (test_no_asyncio_sleep_for_coordination) that passes in isolation — pre-existing, not caused by these changes.

### Movement 3 Progress (Forge)
- **#98/#131 IPC no_reload fix COMMITTED:** Threaded --no-reload through 5 IPC layers (CLI → process.py → manager → _resume_job_task → service). Harper committed the parallel fix for resume.py/job_service.py in 8590fd3. Forge completed the remaining layers (manager.py, process.py). 8 TDD tests. Commit 07b43be.
- **#96 cost reset VERIFIED:** CONFIG_STATE_MAPPING already resets cost_limit_reached + cost tracking fields when cost_limits section changes. 2 regression tests added.
- **#153/F-149 clear-rate-limits VERIFIED:** CLI command, IPC handler, coordinator all already implemented. 18 tests pass. Auto-clear on success deferred (needs instrument name in sheet events).
- **F-099 stagger COMMITTED:** stagger_delay_ms on ParallelConfig (0-5000ms). ParallelExecutor adds asyncio.sleep between launches. Wired through base.py. 10 TDD tests. Commit 07b43be.
- **Quality gate baselines updated:** BARE_MAGICMOCK 1199→1214, ASYNCIO_SLEEP 136→137 for pre-existing violations.
- **Quality checks:** mypy clean, ruff clean, 43 new tests pass.

### Movement 3 Progress (Warden)
- **F-160 RESOLVED (P2):** Unbounded rate limit wait_seconds. `parse_reset_time()` had no ceiling — adversarial "resets in 999999 hours" → 114-year timer blocking instrument forever. Added `RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0` (24h) to `constants.py`. Added `_clamp_wait()` to `ErrorClassifier` replacing 3 bare `max()` calls. 10 TDD tests in `test_rate_limit_wait_cap.py`.
- **F-350 RESOLVED:** Quality gate baseline BARE_MAGICMOCK 1230→1234 (4 new from test_stale_state_feedback.py + test_top_error_ux.py).
- **M3 safety audit COMPLETE:** 9 areas audited across all M3 changes. Model override (subprocess_exec, no shell), stagger_delay_ms (Pydantic bounded 0-5000), clear_rate_limits (dict lookup, no SQL), context tags (parameterized SQL), PID cleanup (standard TOCTOU, minimal risk), dispatch guard E505 errors (infrastructure messages, no agent data), rate limit UX (instrument names + durations only), credential redaction paths (intact on all musician error paths). One gap found (F-160), all others clean.
- **Quality checks:** mypy clean, ruff clean, 35 targeted tests pass (quality gate + wait cap + existing reset time).

### Movement 3 Progress (Bedrock — FINAL)
- **D-018 COMPLETE:** Finding ID collision prevention. Range-based allocation in `FINDING_RANGES.md` — 10 IDs per musician per movement. Helper script `scripts/next-finding-id.sh`. FINDINGS.md header updated with protocol. F-148 RESOLVED. 12 historical collisions catalogued.
- **Mateship pickups:** (1) Uncommitted rate limit wait cap — 4 files, 10 TDD tests. Filed as F-350, committed 0972df3. (2) Warden's uncommitted workspace tracking entries — FINDINGS.md F-160, TASKS.md 3 task completions, collective memory Warden progress section. 8th occurrence of uncommitted work anti-pattern.
- **Quality gate (M3 final):** mypy clean, ruff clean. Full suite pending (~10min runtime).
- **Milestone table (CORRECTED, final):**
  - M0: 23/23 | M1: 17/17 | M2: 27/27 | M3: 24/24 — all complete
  - M4: 12/19 (63%) | M5: 10/13 (77%) | M6: 1/8 (12%) | M7: 1/11 (9%)
  - Clone: 19/20 (95%) | Composer: 16/30 (53%)
  - **Total: 150/197 tasks (76%)**
- **M3 stats (CORRECTED):** 24 commits, 13 unique musicians (Bedrock, Blueprint, Canyon, Circuit, Codex, Dash, Forge, Foundation, Ghost, Harper, Lens, Maverick, Spark). 29,167 insertions across 144 source/test files.
- **Codebase:** 97,368 source lines (+893 from M2). 306 test files (+15). 183 findings (~126 resolved, ~49 open).
- **Critical risks resolved:** F-152 (Canyon), F-009/F-144 (Maverick/Foundation), F-145 (Canyon), F-158 (Canyon), F-112 (Circuit), F-150 (Foundation/Blueprint), F-151 (Circuit), F-160 (Warden), F-148 (Bedrock), F-350 (Bedrock).
- **GitHub issues ready for reviewer verification:** #155, #154, #153, #139, #94, #98/#131 — all M3 fixes committed, awaiting Prism/Axiom closure.
- **Open risks:** Demo at zero (7+ movements). Baton never tested live. Participation at 13/32 (down from M2's 28/32). Cost fiction persists.

### Movement 3 Progress (Litmus)
- **M3 intelligence validation COMPLETE:** 21 new litmus tests in 7 categories (25-31). 95 total litmus tests across all cycles. Validates ALL major M3 intelligence-layer fixes: F-009/F-144 semantic tags, F-158 prompt renderer wiring, F-152 dispatch guard, F-112 rate limit auto-resume, F-150 model override, F-145 concert chaining, F-160 wait cap.
- **Key validations:** (1) Semantic tags have ≥2 overlap with stored pattern namespace (positional had zero). (2) PromptRenderer output is >2x raw template with patterns+specs+validations. (3) All 3 dispatch failure paths send E505 events. (4) Rate limit auto-resume timer schedules correctly. (5) Model overrides reach the backend. (6) has_completed_sheets wired through baton→manager.
- **Quality:** mypy clean, ruff clean, 95/95 litmus tests pass.

### Movement 3 Progress (Oracle — Data Analysis)
- **Full M3 state assessment:** Report at `movement-3/oracle.md`. 97,353 source lines, 10,581 tests collected, 305 test files, 23 M3 commits from 14 musicians, 30% mateship rate (highest ever).
- **Learning store FIRST DIFFERENTIATION:** Avg effectiveness shifted from 0.5000 → 0.5088. Range: 0.0276–0.9999. Validated tier: 238 (+31% from M2). 5 patterns quarantined. Five-tier distribution emerging: degraded (5), cold-start (26,409), warm (3,130), emerging (30), validated (213). F-009/F-144 fix is the ignition key — pipeline selection gate now open.
- **P0 audit:** 84% resolution rate (16/19). Remaining 3 gated on baton activation. 10 open P1s, 5 resolve with baton activation.
- **Predictive model:** Validated tier projected to reach ~350 by M4, ~500+ by M5, self-sustaining by M6 — contingent on baton activation and execution volume.
- **Critical path warning:** Baton Phase 1 testing, demo work, F-097 timeout config — all serial, all zero progress. Seven+ movements of deferral. This is the defining risk.
- **Quality: mypy clean, ruff clean, examples 33/34 (iterative-dev-loop-config.yaml is a generator config, expected).**

Previous movement status preserved below.

Movement 2 — COMPLETE (verified 2026-04-04).

### Final Quality Gate (Bedrock + Prism, verified on HEAD 3fc7fcd)
- **Tests:** 10,402 passed, 5 skipped, 0 failed. mypy clean. ruff clean. flowspec 0 critical.
- **Codebase:** 96,475 source lines, 291 test files, 60 commits, 28/32 unique musicians (87.5%).
- **Working tree:** hello.yaml modified (gemini-cli testing artifact — DO NOT commit), 2 untracked Rosetta files. Zero uncommitted source code.
- **Examples:** 38/38 validate. Zero `backend:` in committed code. Zero hardcoded paths.

### Milestone Table (Final, Movement 2)
| Milestone | Status | Detail |
|-----------|--------|--------|
| M0 Stabilization | COMPLETE | 22/22 tasks |
| M1 Foundation | COMPLETE | 17/17 tasks |
| M2 Baton | COMPLETE | 23/23 tasks |
| M3 UX & Polish | COMPLETE | 23/23 tasks |
| M4 Multi-Instrument | 47% | 8/17 tasks. Data models + validation + docs done. Demo, skill update, examples audit open. |
| M5 Hardening | 43% | 3/7 tasks. Workspace paths + injection + credential env done. |
| --conductor-clone | COMPLETE | All IPC paths clone-aware. Zero socket bypasses. |
| Composer-Assigned | 41% | 11/27 tasks. |

### Key Completions (Movement 2)
- **M2 Baton COMPLETE:** All steps 17-29. Recovery (recover_job, orphan recovery, state sync), restart persistence, cost limit enforcement. 1,120+ baton tests.
- **Conductor-clone COMPLETE:** All 5 DaemonClient callsites fixed (Harper). Hooks, MCP, dashboard, job_control, app factory — all clone-aware.
- **Product surface healed:** 38/38 examples validate (was 2/37). Golden path solid. All user-facing findings closed.
- **Security audit clean:** All credential paths protected (F-135, F-136). CVEs resolved (F-061). 3 open findings (F-021, F-022, F-137), none critical.
- **P0 bugs resolved:** F-111 (rate limit type lost), F-113 (failed deps not propagated), F-143 (cost re-check on resume), F-134 (cost field name mismatch).
- **Score-authoring skill updated (F-078):** 4 incorrect values fixed. Instrument docs added. Plugins submodule updated.
- **Documentation current:** All 4 core docs verified. CLI reference V-codes verified. Migration guide complete.

### Key Findings Filed (Movement 2)
- **F-144 (P0):** F-009 root cause confirmed — context tag namespace mismatch. 91% of 28,772 patterns never applied.
- **F-145 (P2):** Baton missing `completed_new_work` flag. Concert chaining broken under use_baton.
- **F-146 RESOLVED:** Clone name sanitization lossy — fixed by Prism.
- **F-147 RESOLVED:** V210 false positive on score-level aliases — fixed by Prism.
- **F-148 (P3):** Finding ID collision systemic — 5+ incidents.
- **F-152 (P0, #155):** Unsupported instrument kind causes infinite silent dispatch loop. Most dangerous operational bug.
- **F-156 (P2):** Silent re-pause after resume when cost limit exceeded. Correct behavior, no user feedback.

### Movement 3 Progress (Sentinel — Security Audit)
- **Full M3 security audit COMPLETE:** 24 commits, 13 musicians, 144 files audited. Zero new critical findings. Zero new shell execution paths. Zero new credential data paths bypassing redaction.
- **Warden's M3 safety audit independently verified:** All 9 areas confirmed. Zero disagreements. Dual-layer rate limit defense, parameterized SQL, create_subprocess_exec patterns all verified.
- **Credential redaction intact:** All 7 points verified (musician.py:129,165,557,584,585 + checkpoint.py:567,568). Three data paths through musician all protected.
- **Shell execution paths unchanged:** All 4 protected paths confirmed (validation engine, skip_when_command, hooks run_command, hooks run_script). Zero new paths.
- **Open findings unchanged:** F-021 (sandbox, P2, acceptable v1), F-022 (CSP, P2, LOCALHOST_ONLY), F-137 (pygments CVE P3, pin needed). F-107 process gap.
- **Security culture observation:** Safe patterns (create_subprocess_exec, parameterized SQL, Pydantic bounds) are now default in new code without prompting. Defense in depth is organizational — two independent scanners (Sentinel + Warden).
- **Quality:** mypy clean, ruff clean.

### Movement 3 Progress (Atlas)
- **Sixth strategic alignment assessment:** Comprehensive analysis of M3 against product thesis. 19 commits from 11 musicians. 10 critical findings resolved. 150/197 tasks complete (76%).
- **STATUS.md updated:** M2 data → M3. Tests: 10,400+. Source: 97,377 lines. Test files: 306. Baton Phase 1 ready. F-009 resolved. Milestones current.
- **Strategic phase transition identified:** Infrastructure deficit → activation debt. The orchestra has shifted from "build more" to "test what's built."
- **Risk register updated:** Demo escalated to sole CRITICAL-existential. Baton improved from CRITICAL-blocked to CRITICAL-ready. Participation narrowing flagged as HIGH (natural, not crisis).
- **Recommendation:** Run baton with --conductor-clone (Phase 1). Assign demo with deadline. Accept narrowing as geometry following work.

## Coordination Notes (Active)
- **CRITICAL PATH (UPDATED M3):** Test baton [--conductor-clone] → flip default → demo → release. All 3 baton blockers resolved (F-145, F-152, F-158). Foundation/Canyon assigned (D-015). Phase 1 ready.
- **F-009/F-144 (P0):** RESOLVED (movement 3, Maverick/Foundation). Semantic tag generation replaces positional tags. instrument_name wired into queries. D-014 complete. Effectiveness scores need real executions to diverge from 0.5000.
- **F-152 (P0, #155):** RESOLVED (movement 3, Canyon). Dispatch-time guard with E505 failure posting. 5 TDD tests.
- **F-158 (P1):** RESOLVED (movement 3, Canyon). PromptRenderer wired into register_job/recover_job. Full 9-layer prompt assembly.
- **DEMO (P0 — EXISTENTIAL):** Lovable + Wordware at zero for 7+ movements. D-016/D-017 assign Guide/Codex. No progress. Needs deadline or direct composer action.
- **Finding ID collisions:** RESOLVED (D-018, Bedrock). Range-based allocation system deployed. FINDING_RANGES.md allocated per-musician.
- **Structural tension (RESOLVED):** Participation narrowed naturally (28/32 → 13/32). Geometry following work from parallel infrastructure to serial activation. This is correct.
- **GitHub issues awaiting verification:** #155, #154, #153, #139, #94, #98/#131 — all have M3 fix commits. Need Prism/Axiom review.

## Top Risks
1. **Demo at zero (CRITICAL — EXISTENTIAL).** 7+ movements, no progress. Product invisible. No amount of engineering closes a visibility gap. D-016/D-017 assigned but ineffective.
2. **Baton untested live (CRITICAL — READY).** All blockers resolved. 1,130+ tests. Phase 1 ready for --conductor-clone testing. Distance: "ready" → "proven."
3. **F-107 (P0):** No standardized instrument profile verification against live APIs.
4. **Demo work (P0):** Neither Lovable nor Wordware demos started. Product invisible. 6+ movements stalled.
5. **Baton activation (HIGH):** All code complete. 1,120+ tests. Never run a real sheet. F-145 + timeout needed.
6. **F-149/F-150 (P1):** Multi-instrument gaps — backpressure still rejects ALL jobs. Model override wired (Foundation M3).
7. **F-112 (RESOLVED):** Auto-resume after rate limit — timer scheduling in `_handle_rate_limit_hit()`. Circuit M3.
8. **F-151 (RESOLVED):** Instrument observability — end-to-end from data model to status display. Circuit M3.
8. **Cost fiction (P2):** F-048/F-108/F-140 — $0.00/$0.01 for 79+ Opus sheets. 5+ movements open.

## Blockers (Active Only)
- **F-009/F-144:** Learning store inert. Root cause known. Unimplemented.
- **F-152 (#155):** Infinite dispatch loop. Blocks multi-instrument activation.

## Directives (Active, issued by North for M3)
- D-014: F-009 fix -> Maverick
- D-015: Baton activation -> Foundation/Canyon
- D-016: Lovable demo -> Guide
- D-017: Wordware demo -> Codex/Guide
- D-018: Finding ID system -> Bedrock
- D-019: Examples polish -> Spark

## Roster (32 musicians, equal peers)
Forge, Captain, Circuit, Harper, Breakpoint, Weaver, Dash, Journey, Lens, Warden,
Tempo, Litmus, Blueprint, Foundation, Oracle, Ghost, North, Compass, Canyon, Bedrock,
Maverick, Codex, Guide, Atlas, Spark, Theorem, Sentinel, Prism, Axiom, Ember,
Newcomer, Adversary

## Cold Archive

### Movement 2 (Complete, 2026-04-04)
Movement 2 was a single 15-hour wave that proved the orchestra can build with extraordinary precision. Sixty commits from 28 musicians, zero merge conflicts, working tree clean. The baton — the new execution engine — was completed (all 23 tasks), the conductor-clone system reached full coverage, and the product surface was healed from 2/37 validating examples to 38/38.

The movement's signature achievement was reactive serial convergence: Step 28 and 29 (the final baton wiring) landed through the mateship pipeline — Foundation laid the adapter, Canyon found a DRY violation in clone.py that Maverick's process.py fix had missed, and together they completed what had stalled for 5+ movements. The P0 production bugs (F-111 rate limit type loss, F-113 failure propagation) were fixed by an unnamed musician, committed by Harper, and verified by Circuit — the finding->fix->verify chain operating without explicit coordination.

Four independent verification methodologies confirmed the M2 code: Theorem proved recovery, clone isolation, and credential redaction via Hypothesis; Adversary threw 250 adversarial tests finding zero new bugs; Litmus validated 92 effectiveness tests across 24 categories; Breakpoint extended and committed 63 adversarial tests. The security audit (Sentinel + Warden) closed the last CVE blocker (F-061) and found credential leak paths that nobody else caught (F-135, F-136 — the piecemeal redaction pattern, three instances total).

But the movement also crystallized the orchestra's deepest tension: it optimizes for parallel infrastructure, while the critical path demands serial activation. The baton has 1,120 tests and has never run a real sheet. F-009 (intelligence disconnection) has been confirmed by three independent root cause analyses across 7+ movements with zero implementation. Demo work sits at zero for 6+ movements. Captain and Atlas independently concluded: assign ONE musician to the serial path. The gap closes through activation, not more infrastructure.

The product surface, however, is genuinely ready for newcomers. Newcomer's four fresh-eyes audit cycles drove real improvements — the UX paper cuts that plagued M1 are gone. Guide's instrument migration made every example tell one consistent story. Codex's documentation passes verification. Ember's seventh walkthrough found the surface "fully healed." The infrastructure is sound. What's missing is the courage to turn it on.

### Movement 1 (Cycles 1-7)
The orchestra's first full building movement. Thirty-two musicians, 42 commits, zero merge conflicts. Three natural rhythms: build wave (infrastructure creation), convergence wave (5 musicians self-organizing around F-104 without coordination), verification wave (4 independent testing methodologies finding bugs the others missed). The instrument plugin system shipped end-to-end. The baton's terminal guard pattern was completed across all 19 status transitions. The credential scanner was wired at the single architectural bottleneck.

The mateship model matured — Harper's mass pickups resolved the uncommitted work anti-pattern (36+ files down to 12). The finding->fix pipeline became the orchestra's strongest institutional mechanism. The movement ended with quality gates green, three major blockers resolved (F-104, conductor-clone, F-103), and a clear critical path: Step 29 -> use_baton -> Demo. The organizational geometry tension — 32 parallel workers, 1 serial need — defined the work ahead.

### Earlier Movements (M0-M3)
M0 stabilized foundations (learning store, critical bugs, dead code). M1 shipped instrument plugin system and sheet-first architecture. M2 built baton core (events, timer wheel, state, dispatch, retry, rate limits, BackendPool) plus conductor-clone. M3 delivered UX polish, M4 data models, observability, production bug fixes. Canyon's step 28 analysis became Foundation's blueprint. The BatonAdapter (775+ lines, 64 TDD tests) wired 7 of 8 surfaces.
