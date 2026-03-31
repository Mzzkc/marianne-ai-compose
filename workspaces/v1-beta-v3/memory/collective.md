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
- **[Cycle 1, Forge]** Always check what exists before assuming you need to build. Prior evolution cycles already built most learning store infrastructure.
- **[Cycle 1, Circuit]** Test the production path, not the internal method. `classify_execution()` had zero coverage for exit_code=None while `classify()` was fully tested.
- **[Cycle 1, Harper]** Always check the error path, not just the happy path. Stale detection only covered COMPLETED, not FAILED.
- **[Cycle 1, Dash]** Don't assume something is broken without checking. The dashboard has 23 Python files, 19 templates, ~50 endpoints, all functional.
- **[Cycle 1, Composer Notes]** The learning store schema migration failure (#140) brought down ALL jobs. Always write ALTER TABLE migrations, update schema_version, add tests, verify against existing DBs.
- **[Cycle 1, Lens]** 12 learning commands (36% of all CLI commands) dominate help output — poor information architecture.
- **[Cycle 1, Warden]** stdout_tail/stderr_tail stored in 6+ locations without credential scanning. Safety applied piecemeal across shell execution paths.
- **[Cycle 1, Foundation]** CJK/non-Latin text underestimates tokens by 3.5-7x. Known limitation until InstrumentProfile.ModelCapacity lands.
- **[Cycle 1, Blueprint]** SpecCorpusLoader used `if not name:` instead of `if name is None:` — rejects falsy-but-valid YAML values.
- **[Cycle 1, Litmus]** spec_tags integer key serialization risk: JSON converts int keys to strings, but lookup is by int. Mitigated by Pydantic v2's model_validate() coercion.
- **[Cycle 1, Ghost]** When the foundation is about to shift, audit first. The instinct to "do something" is wrong when you don't know the baseline.
- **[Cycle 1, Breakpoint]** Test the abstraction level that runs in production. Zero tests existed for PriorityScheduler._detect_cycle().
- **[Movement 1, Axiom]** Failed sheets must propagate failure to dependents. Without propagation, `is_job_complete` returns False forever — zombie jobs.
- **[Movement 1, Theorem]** Property-based testing finds bugs hand-picked examples miss. Hypothesis found the escalation terminal-guard violation in seconds.
- **[Movement 1, Adversary]** Every handler that transitions sheet status must check `_TERMINAL_STATUSES`. The baton's guard pattern is now complete across all handlers.
- **[Movement 1, Mateship]** The finding→fix pipeline works without coordination: F-018 filed by Bedrock, proved by Breakpoint, fixed by Axiom, verified by Journey. Four musicians, zero meetings.
- **[Movement 2, Axiom]** `record_attempt()` correctly doesn't count successes; `_handle_attempt_result` correctly retries on 0% validation. Together: infinite loop. Bugs at system boundaries are the hardest to find because each side looks correct in isolation.
- **[Movement 2, Axiom]** The pause model (`job.paused` boolean) serves three masters: user pause, escalation pause, cost pause. Each fix adds another guard instead of fixing the root cause. Post-v1: replace with a pause reason set.
- **[Movement 2, Newcomer]** The gap between "feature works" and "feature is taught" is where adoption dies. F-083 — instrument system is the biggest M1 feature but had zero adoption in examples.

## Design Decisions
- **Baton migration:** Feature-flagged BatonAdapter. Old and new paths coexist. Do not re-debate.
- **Cost visibility:** Scoped first (CostTracker in status), full UX later.
- **Learning schema changes:** Additive only without escalation.
- **Code-mode techniques:** Long-term direction. MCP supported for v1. code_mode flag exists, not wired.
- **No automatic instrument fallback on rate limits.** Explicit opt-in only.
- **Daemon-only architecture.** All state in ~/.mozart/mozart-state.db. JsonStateBackend deprecated.
- **Baton retry state machine:** ~40% implemented. Covers rate limits, 100% pass, AUTH_FAILURE, retry counting, completion mode, cost enforcement. Missing: graduated classification, healing, escalation events, learning events.
- **Musician-baton contract:** validation_pass_rate defaults to 0.0 (safety), baton auto-corrects to 100.0 when validations_total==0 and execution_success==True.
- **Terminal state invariant:** All baton handlers guard against `_TERMINAL_STATUSES`. Seven bugs found and fixed by three independent methodologies.
- **Pause model debt:** Single boolean serving three masters (user/escalation/cost). Post-v1 → pause_reasons set.

## Current Status
Movement 4 — IN PROGRESS.

**Error Taxonomy & Classification (Blueprint, M4):**
- F-097 PARTIALLY RESOLVED: E006 EXECUTION_STALE error code added. Stale detection now classified distinctly from backend timeout. 10 TDD tests.
- F-098 RESOLVED: Rate limit patterns in stdout no longer masked by Phase 1 JSON errors. Added Phase 4.5 "Rate Limit Override" that always scans. 6 TDD tests.
- F-105 PARTIALLY RESOLVED: `crash_patterns` and `stale_patterns` added to CliErrorConfig. 6 TDD tests.
- F-104 RESOLVED by Forge: Prompt rendering wired into baton musician. **BATON EXECUTION UNBLOCKED.**

**Verification & Mateship (Circuit, current movement):**
- 18 additional TDD tests proving F-098 and F-097 fixes (test_rate_limit_stdout_detection.py). Includes JSON-masking regression case that reproduces the exact v3 production failure.
- Quality gate mateship: fixed 6 bare MagicMock instances, updated assertion-less baseline. 9638 tests pass, mypy clean, ruff clean.
- 13 files of uncommitted work from other musicians remain in the working tree (7th occurrence of the pattern).

| Milestone | Status | Detail |
|-----------|--------|--------|
| M0 Stabilization | COMPLETE | 18/18 tasks |
| M1 Foundation | COMPLETE | 13/13 tasks |
| M2 Baton | 94% | Step 28: BatonAdapter + manager wiring DONE (Foundation + Canyon M3, 775+ lines, 47 tests). Feature flag active. Prompt assembly + state sync + concert support remain. Step 29 remains. |
| M3 UX & Polish | COMPLETE | 19/19 tasks. Step 35 (error standardization) DONE (Maverick M3). Circuit M3: F-068/F-069/F-048 fixed (+11 TDD tests). |
| --conductor-clone | 12% | Audit done. Implementation not started. |

**Step 28 Progress (Foundation + Canyon, M3):**
- BatonAdapter (`src/mozart/daemon/baton/adapter.py`) implements 7 of 8 integration surfaces from Canyon's wiring analysis. Foundation: adapter shell, dispatch callback, state mapping, EventBus bridge (abbbeac). Canyon: completion signaling (wait_for_completion, _check_completions), manager.py wiring (_run_job_task routing, start() initialization), F-077 fix (hooks lost on restart — mateship).
- Surfaces remaining: CheckpointState synchronization (Surface 4 — status mapping exists but no per-event sync), concert support (Surface 7).
- **Surface 3 (prompt assembly) RESOLVED by Forge (3deb436):** musician._build_prompt() now performs full Jinja2 rendering with preamble, injections, and validation requirements. Also supports pre-rendered prompt from PromptRenderer.
- `DaemonConfig.use_baton: bool = False` — feature flag. When True, _run_job_task routes through BatonAdapter. Baton event loop starts as background task in start(). Prompt assembly now works — test with `--conductor-clone` before enabling.
- 47 + 17 = 64 TDD tests in adapter + musician prompt tests — all passing.

**Maverick M1 (current cycle):** Verified F-104 resolved. Added `total_sheets/total_movements/previous_outputs` to AttemptContext for cross-sheet data path. Cleaned up 3 orphaned files (F-110) that blocked `pytest tests/ -x`. 537 baton tests passing.

**Critical path (UPDATED):** F-104 RESOLVED (Forge 3deb436). F-098 RESOLVED (Forge 3deb436). Surface 4 (state sync) → Surface 7 (concerts) → Step 29 (restart recovery) → Enable use_baton → Demo.

**M4 Data Models (Blueprint, M3):**
- Steps 38-41 COMPLETE: `InstrumentDef`, `MovementDef` models, `per_sheet_instruments`, `per_sheet_instrument_config`, `instrument_map` on SheetConfig, `instruments` and `movements` on JobConfig. Full resolution chain in `build_sheets()`.
- CONFIG_STATE_MAPPING updated with `instruments` and `movements` entries.
- 33 TDD tests + 2 property-based tests.

**Bug Fixes (Blueprint, M3):**
- F-093 RESOLVED: All 35 examples fixed from `./workspaces/` to `../workspaces/`. Committed: 75bebed.
- F-095 RESOLVED: `mozart init` now generates `instrument: claude-code` not `backend:`. Committed: 75bebed.
- F-091 RESOLVED: `mozart validate` shows "Instrument:" when instrument: is used. Committed: 75bebed.
- All M4 work committed on main (75bebed, 46 files, 855 insertions).

**Observability Fixes (Circuit, M3):**
- F-068 RESOLVED: "Completed:" timestamp only shown for terminal job statuses (COMPLETED/FAILED/CANCELLED). RUNNING/PAUSED jobs no longer show misleading completion time.
- F-069/F-092 RESOLVED: V101 false positive on Jinja2 `{% set %}` and `{% for %}` variables. Added AST walker to extract template-declared variables, supplementing `jinja2_meta.find_undeclared_variables`. hello.yaml now validates clean.
- F-048 RESOLVED: Cost tracking now runs even when cost limits disabled. Root cause: `_enforce_cost_limits()` gated both tracking AND enforcement behind `cost_limits.enabled`. Fix: `_track_cost()` runs first, enforcement gated separately.

**Production Bug Fixes + Error Standardization (Maverick, M3):**
- F-075 RESOLVED: lifecycle.py — preserve terminal status on resume. Committed: f58fc89.
- F-076 RESOLVED: sheet.py — rate limit check moved before validations. Committed: f58fc89.
- F-077 tests committed: 7 TDD tests (test_daemon_manager.py + test_production_bug_fixes.py). Committed: f58fc89.
- F-096 RESOLVED: Blueprint committed M4 work (75bebed), resolving mypy + reconciliation test failures.
- Step 35 (error standardization) COMPLETE: _entropy.py dominant pattern warning migrated to output_error(). M3 UX milestone DONE.
- Test hardening: 6 test files improved — proper MagicMock specs, fixed sleep timing, case-insensitive assertions.
- Mateship pickup: 5th occurrence of uncommitted work (F-075/F-076/F-077 fixes were in working tree).

**Top risks (updated by Bedrock, post-M3 verification):**
1. **F-104 (P0):** Prompt rendering not wired into baton musician. SINGLE BLOCKER for multi-instrument execution.
2. **Step 29:** Restart recovery not started. Needed for production baton usage.
3. **F-009:** Learning store effectiveness still inert. Oracle found root cause (M2). Nobody implementing.
4. **#145:** --conductor-clone still unbuilt. All daemon testing at risk.
5. **Uncommitted composer fixes:** F-103 (3 baton bugs) fixed in working tree but not on HEAD. 19 lines of P0 code at risk of loss.
6. **3 deleted example scores** in working tree (F-088 cleanup) — not committed.

**Composer production bugs (P0/P1):** F-075 RESOLVED (f58fc89). F-076 RESOLVED (f58fc89). F-077 RESOLVED (f58fc89). F-103 FIXED in working tree (not committed). All found by real usage, not tests.

### M2 Review Phase (Latest per Agent)
- **Axiom:** F-083/F-089 — instrument migration only 7/37 committed (5th uncommitted work occurrence). Verified #114 closed. All 3 baton fixes committed (6a0433b). North's 6 directives: 0/6 completed.
- **Prism:** F-057 committed (e6d6753, 2,262 lines). 755 baton tests passing. Filed F-089 (32 files uncommitted). #100 rate limits fixed in baton but NOT in runner.
- **Ember:** Filed F-090 (doctor/status conductor disagreement), F-091 (validate shows "Backend:" for instrument: scores). Confirmed F-089 independently. 4/9 findings resolved, 5 still open.
- **Adversary:** F-095 filed, F-075/F-077/F-093 confirmed. 35/37 examples fail validation.
- **Atlas:** Infrastructure velocity outpacing intelligence. Lovable demo 3-4 movements away. Proposed staged demo via enhanced hello.yaml.
- **Captain:** F-080 (4th uncommitted occurrence), F-081 (cross-test leakage). Production bugs are most important M2 signal.
- **Weaver:** Step 28 is both critical path AND structural fix for F-075/F-076/F-077. 758 baton tests, zero E2E through conductor. That gap = step 28.
- **North:** Velocity dropped (48→26 tasks). Test-to-code ratio corrected 0.81x→2.85x. 6 new directives D-008 through D-013.
- **Newcomer:** F-083 — instrument system has zero adoption in examples. Error standardization at 98%. 10/12 M1 findings resolved.

### M2 Build Phase (Summarized)
- **Foundation:** Step 23 — conductor retry state machine with timer-integrated backoff, escalation, self-healing, cost enforcement. 26 tests.
- **Circuit:** Bridged dispatch↔state gap (F-056). Completion mode. Status no-args mode (D-007). 35 tests total.
- **Forge:** F-052 SheetContext aliases + F-045 status fix + error standardization. 9 tests.
- **Maverick:** F-020 hook fix + musician.py mateship + prompt tests. 13+15+13 tests.
- **Canyon:** Step 28 wiring analysis — 8 integration surfaces, 5-phase implementation, ~900 lines.
- **Ghost:** Error standardization (8 commands) + conductor-clone CLI audit (20 commands catalogued).
- **Dash:** Movement-grouped status display + error standardization (13 commands) + conductor-clone audit.
- **Harper:** `mozart init` hardening (name validation, --json, 35 tests) + shared `load_all_profiles()`.
- **Blueprint:** 51 prompt characterization tests (D-003). F-052 filed, F-020 verified.
- **Oracle:** D-005/F-009 root cause — 91% of patterns never applied due to narrow context tag matching.
- **Warden:** F-023 — credential scanner expanded to 13 patterns. All shell execution paths now protected.
- **Breakpoint:** 59 adversarial tests, zero bugs found — M2 baton code is correct.
- **Theorem:** 27 property-based tests (59→86), zero bugs — M2 mathematically correct under hypothesis.
- **Litmus:** 21 intelligence tests. spec_tags serialization risk mitigated.
- **Codex:** CLI reference + instrument-guide.md (~350 lines).
- **Guide:** F-083 resolved — all 37 examples migrated to instrument:. Filed F-088 (hardcoded paths).
- **Compass:** Golden path works. 6 CLI fixes. F-084 + F-082 resolved.
- **Sentinel:** All 4 shell execution paths PROTECTED. pip-audit: 8 CVEs in 7 packages (F-061).

**F-057 RESOLVED:** Prism committed all uncommitted work (e6d6753).
**F-089 RESOLVED (partial):** Guide committed 7/37, Prism picked up remainder (d2f8a81 + 730d17c).

### North's Directives — Status
D-001 through D-007: ALL DONE or mostly done. D-005 root cause found (Oracle). Steps 28+29 remain unclaimed.
D-008 through D-013 (M2): 0/6 completed. D-008 (Foundation claim step 28), D-009 (--conductor-clone), D-010 (fix F-009), D-011 (fix F-075/F-077), D-012 (fix F-076/F-061), D-013 (investigate test runtime).

## Coordination Notes (Active — Updated by Bedrock post-M3)
- **CRITICAL PATH (UPDATED):** F-104 (prompt rendering in baton musician) is the SINGLE BLOCKER. Step 28 is partially done (Foundation+Canyon). Step 29 follows F-104. The critical path is now: F-104 → Step 28 completion → Step 29 → Enable use_baton → Demo.
- **D-005 ROOT CAUSE (Oracle):** F-009 is feedback loop disconnection — 91% of patterns never applied due to narrow context tag matching. Fixes needed: broaden selection, close SemanticAnalyzer loop, lower min_applications threshold. STILL UNIMPLEMENTED after 2 movements.
- **Production bugs RESOLVED:** F-075 (#149), F-076 (#150), F-077 (#151) all fixed and committed (f58fc89). F-103 (3 baton bugs) fixed in working tree by composer.
- **Uncommitted work (6th pattern):** Composer's F-103 fixes + 3 deleted examples + workspace updates sit in working tree. 14 files, ~3,500 lines of changes. This pattern is now structural — the score should enforce commit checkpoints.

## Blockers
- **F-104 (P0):** Baton musician does not render Jinja2 prompts. BLOCKS ALL BATON-PATH EXECUTION. Without this, `use_baton: true` produces raw templates. Multi-instrument execution is architecturally ready but functionally blocked.
- **#145 (P0):** --conductor-clone not implemented. All daemon testing requires mocks or risks production conductor.

### Setup Re-verification (Canyon, post-M3)
Canyon re-executed setup and verified: all 32 memory files present, TASKS.md current (61 open issues tracked), FINDINGS.md comprehensive (105+ findings), composer-notes.yaml has 30 directives through M3. Critical path: F-104 → Step 29 → Demo blockers. The workspace substrate is solid.

## Roster (32 musicians, equal peers)
Forge, Captain, Circuit, Harper, Breakpoint, Weaver, Dash, Journey, Lens, Warden,
Tempo, Litmus, Blueprint, Foundation, Oracle, Ghost, North, Compass, Canyon, Bedrock,
Maverick, Codex, Guide, Atlas, Spark, Theorem, Sentinel, Prism, Axiom, Ember,
Newcomer, Adversary

## Cold Archive — Movement 1

Movement 1 was the orchestra's first building movement. Thirty-two musicians worked in parallel with zero merge conflicts. The coordination substrate — TASKS.md, FINDINGS.md, collective memory — held under real load. Mateship was genuine: Harper picked up Ghost's doctor command, Journey rescued 5 untracked test files, Tempo committed another musician's PreflightConfig work.

Three waves defined the movement: a build wave (Canyon, Ghost, Harper, Forge, Maverick, Blueprint, Foundation, Circuit creating infrastructure), a baton wave (Axiom, Theorem, Breakpoint, Adversary hardening the state machine through three independent testing methodologies, each finding bugs the others missed), and a review wave providing analysis and verification. The instrument plugin system shipped end-to-end across 5 musicians in 8 coordinated steps. The credential scanner was wired at the single bottleneck (SheetState.capture_output) rather than 17 read sites — architectural elegance over brute force.

The learning store remained the deepest systemic concern — 225,000+ executions, 25,000+ patterns, and the system cannot tell good from bad. Oracle's F-009 finding (uniform 0.5000 effectiveness) represents a philosophical challenge to the project's identity as an intelligence layer. A trimodal distribution (0.50, 0.55, 0.98) with 720 differentiated patterns emerged by movement end — breadcrumbs of quality signal.

Key M1 numbers: Canyon 2,324 lines/90 tests, Foundation 144 tests, Circuit 116 tests, Blueprint 87 tests, Breakpoint 65 adversarial tests, Maverick 62 tests, Theorem 59 property-based tests, Forge 40 tests. 24/24 pre-flight sheets from Cycle 1 completed at 100% first-attempt success. 11 design specs from v1 beta spec session. Critical path: Instrument Plugin → Baton → Multi-Instrument → Lovable Demo.
