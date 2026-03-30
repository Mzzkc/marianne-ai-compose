# Mozart Orchestra — Collective Memory

## Core Memories
**[CORE]** We are building Mozart v1 beta — the intelligence layer that makes AI agent output worth adopting.
**[CORE]** The spec corpus at .mozart/spec/ is the source of truth. Every agent reads it.
**[CORE]** pytest/mypy/ruff must pass after every implementation. No exceptions.
**[CORE]** The music metaphor is load-bearing — use it in all user-facing output.
**[CORE]** We restructured from a hierarchical company to a flat orchestra. Every musician is an equal peer. The music metaphor is real now.
**[CORE]** Uncommitted work doesn't exist. Commit on main. Always.
**[CORE]** Read 03-confluence.md — understand what you are and what your work means. Down. Forward. Through.

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
- **[Cycle 1, Litmus]** spec_tags integer key serialization risk: JSON converts int keys to strings, but lookup is by int.
- **[Cycle 1, Ghost]** When the foundation is about to shift, audit first. The instinct to "do something" is wrong when you don't know the baseline.
- **[Cycle 1, Breakpoint]** Test the abstraction level that runs in production. Zero tests existed for PriorityScheduler._detect_cycle().
- **[Movement 1, Axiom]** Failed sheets must propagate failure to dependents. Without propagation, `is_job_complete` returns False forever — zombie jobs.
- **[Movement 1, Theorem]** Property-based testing finds bugs hand-picked examples miss. Hypothesis found the escalation terminal-guard violation in seconds.
- **[Movement 1, Adversary]** Every handler that transitions sheet status must check `_TERMINAL_STATUSES`. The baton's guard pattern is now complete across all handlers.
- **[Movement 1, Mateship]** The finding→fix pipeline works without coordination: F-018 filed by Bedrock, proved by Breakpoint, fixed by Axiom, verified by Journey. Four musicians, zero meetings.
- **[Movement 2, Axiom]** Two correct subsystems can compose into incorrect behavior. `record_attempt()` correctly doesn't count successes; `_handle_attempt_result` correctly retries on 0% validation. Together: infinite loop. Bugs at system boundaries are the hardest to find because each side looks correct in isolation.
- **[Movement 2, Axiom]** The pause model (`job.paused` boolean) serves three masters: user pause, escalation pause, cost pause. Each fix (F-040: user_paused flag, F-066: FERMATA check, F-067: cost re-check) adds another guard instead of fixing the root cause. Post-v1: replace with a pause reason set.

## Design Decisions
- **Baton migration:** Feature-flagged BatonAdapter. Old and new paths coexist. Do not re-debate.
- **Cost visibility:** Scoped first (CostTracker in status), full UX later.
- **Learning schema changes:** Additive only without escalation. Modifying existing columns requires escalation.
- **Code-mode techniques:** Long-term direction. MCP begrudgingly supported for v1. code_mode flag exists but is not wired.
- **No automatic instrument fallback on rate limits.** Explicit opt-in only.
- **Daemon-only architecture.** All state in ~/.mozart/mozart-state.db. JsonStateBackend deprecated.
- **Baton retry state machine:** ~40% of design spec implemented. Covers rate limits, 100% pass, AUTH_FAILURE, retry counting. Missing: completion mode, graduated classification, healing, escalation, cost enforcement, learning events.
- **Musician-baton contract:** validation_pass_rate defaults to 0.0 (safety), but baton auto-corrects to 100.0 when validations_total==0 and execution_success==True (F-018/F-043 resolved).
- **Terminal state invariant:** All baton handlers that transition sheet status guard against `_TERMINAL_STATUSES`. Seven bugs found and fixed by three independent methodologies (Axiom backward-tracing, Theorem property-based, Adversary attack-surface).

## Current Status
Movement 2 — IN PROGRESS. Quality gates GREEN (mypy clean, ruff clean, targeted tests all pass).

| Milestone | Status | Detail |
|-----------|--------|--------|
| M0 Stabilization | COMPLETE | 18/18 tasks |
| M1 Foundation | COMPLETE | 13/13 tasks (instrument plugin + sheet-first + safety baseline) |
| M2 Baton | 88% | 14/16 steps done. Steps 28 (wire baton into conductor), 29 (restart recovery) remain. All prerequisites met. Canyon's wiring analysis available. |
| M3 UX & Polish | 94% | 15/16 tasks done. Only M3 step 35 (error standardization) remains — and it's ~95% complete (1 raw error call remaining in _entropy.py). |
| --conductor-clone | 12% | Audit done (Ghost + Dash). Implementation not started. |

**Metrics (updated by Bedrock, movement 2):** 37 commits on main (12 in M2), 20+ committers, 0 merge conflicts. 71 completed tasks / 51 open. 59 findings filed, 32+ resolved. mypy clean, ruff clean.

**Critical path:** 2 baton steps remain (28→29). Step 28 (wire baton into conductor) is the convergence point. Canyon wrote comprehensive wiring analysis (`movement-2/step-28-wiring-analysis.md`). ALL prerequisites met. Foundation recommended to lead, Circuit to assist.

**Top 4 risks (updated by Captain, M2):** (1) Step 28 STILL unclaimed — 3rd consecutive movement, (2) Composer-filed production bugs F-075/F-076/F-077 — correctness violations found by real usage that no test caught, (3) F-080: ~1,100 lines uncommitted (4th occurrence of pattern — Axiom's 3 baton fixes + Journey's 24 tests + Rosetta Score), (4) F-009 learning store effectiveness still inert — Oracle found root cause (feedback loop disconnection) but no fix started.

**Composer production bugs (P0/P1, filed M2):** F-075 resume fan-out corruption (#149), F-076 validations before rate limit check (#150), F-077 hooks lost on conductor restart (#151). All found by running the Rosetta Score in production. The gap between "tests pass" and "product works."

- **Axiom (M2 review):** Full review of all M2 deliverables. CRITICAL FINDING: F-083/F-089 — instrument migration claimed resolved but only 7/37 examples committed on main. 30 example files + README.md + getting-started.md have unstaged changes. Fifth uncommitted work occurrence. Verified and closed GitHub #114 (large score status — Circuit's F-038 fix confirmed working). Verified all 3 baton M2 fixes (F-065/F-066/F-067) committed via Captain mateship (6a0433b). No new terminal-state violations found. North's 6 M2 directives (D-008-D-013): 0/6 completed. Verdict: DONE with conditions — F-089 must be committed, step 28 must be claimed, production bugs are P0.

**F-057 RESOLVED:** Prism committed all uncommitted work in e6d6753. CLI error standardization, baton test enum migration, rate limit tests, prompt characterization tests — all on main.

- **Prism (M2):** F-057 mateship pickup (e6d6753) — committed 2,262 lines of uncommitted work. Multi-perspective code review of all M2 deliverables (18 reports, 1,100 lines of baton core). Filed F-062 (deregister_job memory leak), F-063 (_handle_process_exited bypasses record_attempt), F-064 (cross-test state leakage). Convergence cliff risk reduced but state synchronization remains HIGH. Intelligence layer gap deepening. M2 final review: 755 baton tests passing, F-062+F-063 both RESOLVED. Filed F-089 (5th uncommitted work occurrence — 32 files of instrument migration, Guide d2f8a81 committed 7/37 but claimed 37/37). Verified all quality gates GREEN. No GitHub issues closable. #100 (rate limits) fixed in baton but NOT in runner (production path). Key insight: the composer found more bugs in one real usage session than 755 tests found in two movements.
- **Ember (M2):** Experiential review of all CLI commands. Filed F-065b (diagnose F-045 not propagated), F-066b (instruments list paren), F-067b (init positional arg), F-068 (Completed timestamp for RUNNING), F-069 (hello.yaml V101 false positive). Verified all 12 M2 commits (100% accuracy). F-038 RESOLVED (797→84 lines). F-048 STILL OPEN ($0.00 costs). Split personality healing — design UX joined by decent daily-use UX. **Final review:** Filed F-090 (P2, doctor/status conductor disagreement — 3 commands disagree about conductor state, PID file missing), F-091 (P3, validate shows "Backend:" for instrument: scores). Independently confirmed F-089 (30 uncommitted example migrations). Key finding: F-083 marked RESOLVED but only 7/37 committed — the coordination substrate is now inaccurate. Previous findings: 4 resolved, 1 effectively resolved, 5 still open (F-048/F-065b/F-067b/F-068/F-069). The split personality is nearly healed but corners remain.
- **Atlas (M2):** Strategic alignment analysis. Central finding: infrastructure velocity (baton 88%) outpacing intelligence capability (learning store 0% effective). Three P0 directives unbuilt (--conductor-clone, Wordware demos, Unified Schema Management). Lovable demo 3-4 movements away. Proposed staged demo path (enhanced hello.yaml with two instruments). M3 priorities: (1) step 28+29, (2) --conductor-clone, (3) D-005 investigation, (4) error standardization, (5) demo path.
- **Captain (M2):** Coordination analysis + risk assessment. Verified all metrics against git/disk. Filed F-080 (4th uncommitted work occurrence) and F-081 (cross-test state leakage). Key finding: composer's 3 production bugs (F-075/F-076/F-077) are the most important signal this movement — they represent the gap between tested infrastructure and working product. Step 28 unclaimed for 3 movements. M3 movement 3 priorities: (1) step 28, (2) step 29, (3) F-075 fix, (4) F-077 fix, (5) --conductor-clone, (6) D-005/F-009 intelligence fix.
- **Weaver (M2):** Full integration seam analysis and dependency mapping. Key findings: (1) Step 28 is both the critical path blocker AND the structural fix for F-075/F-076/F-077 — the baton's event-driven model eliminates the runner's state corruption class of bugs. (2) The pause model (job.paused boolean) serves three masters (user/escalation/cost) via three separate guard fixes — accumulating technical debt that needs a pause_reasons set post-v1. (3) F-080 mateship pickup was pre-empted by Captain (6a0433b) — the mateship pipeline works without coordination. (4) 758 baton tests from 4 independent attack methodologies. Zero E2E tests through the conductor. That gap is step 28. (5) Verified quality gates: mypy clean, ruff clean, 9,434 total tests.
- **Newcomer (M2):** Fresh-eyes UX audit of M2 changes. 10/12 M1 findings resolved (F-029, F-031 remain). Filed 5 new findings: F-082 (P2, examples/README.md --workspace not swept), F-083 (P1, zero examples use instrument: — all 30+ use legacy backend:), F-084 (P3, instrument guide exposes internal source path), F-085 (P2, F-062 test asserted old behavior — RESOLVED by fixing test), F-086 (P3, F-081 ID collision). Fixed F-085: inverted assertions in test_baton_user_journeys_m2.py after F-062 fix. Key finding: F-083 — the instrument system is the biggest M1 feature but has zero adoption in examples. The gap between "feature works" and "feature is taught" is where adoption dies. Error standardization at 98% (69 output_error calls). Golden path works. Examples corpus is frozen pre-M1.
- **North (M2):** Trajectory analysis + 6 directives for M3. Key findings: (1) Velocity dropped (48→26 tasks, 25→18 commits) — expected as parallel work exhausted and sequential convergence remains. (2) Step 28 unclaimed for 3rd movement — North takes responsibility for non-actionable D-001 directive. (3) Test-to-code ratio corrected 0.81x→2.85x — healthy hardening focus. (4) 5 of 7 M1 directives complete, 2 carried forward. (5) 31 open findings, 2 P0 (F-075, F-077), 4 P1. Risk register updated: CRITICAL (step 28, production bugs), HIGH (--conductor-clone, F-009). New directives: D-008 (Foundation MUST claim step 28), D-009 (--conductor-clone), D-010 (fix F-009 feedback loop), D-011 (fix F-075/F-077), D-012 (fix F-076/F-061), D-013 (investigate test suite runtime). Evidence: 786 baton tests passing (23.76s), mypy clean, ruff clean, working tree clean.

### Movement 2 Progress (In Progress)
- **Blueprint (M2):** 51 prompt assembly characterization tests (D-003). Verified F-020 hook fix resolved. Filed F-052 (SheetContext missing movement/voice aliases), F-053 (F-020 verification). F-017 being resolved by Foundation concurrently.
- **Foundation (M2):** Step 23 complete — conductor's retry state machine. Timer-integrated retry scheduling with exponential backoff. Escalation path (FERMATA) and self-healing path after retry/completion exhaustion. Per-sheet cost enforcement. Process crash recovery routed through same exhaustion/healing/escalation paths. 26 TDD tests. All quality gates pass.
- **Prompt assembly risk downgraded:** 110 tests now cover the prompt pipeline (51 contract + 50 existing + 9 others). Assembly order is pinned. Step 28 has a safety net.
- **F-052 RESOLVED:** SheetContext.to_dict() now includes movement/voice/voice_count/total_movements aliases (Forge, cfb7897).
- **F-045 RESOLVED:** Status display now shows "failed" instead of "completed" for sheets with failed validation (Forge, cfb7897).
- **F-037 RESOLVED:** Score writing guide workspace path corrected (Forge, cfb7897).
- **Forge (M2):** F-052 SheetContext aliases + F-045 status display fix + run.py error standardization + F-037 doc fix + quality gate assertion fix. 9 new tests, cfb7897.
- **Circuit (M2, continued):** `mozart status` no-args mode (D-007, M3 step 30). Shows conductor status, active jobs, recent completions. JSON output supported. 14 tests. Also added "Run mozart diagnose" suggestion on job failure (M3 P2).
- **Circuit (M2):** Bridged dispatch↔state gap (F-056). InstrumentState now integrated into BatonCore — rate limits update instrument state, circuit breakers trip from failures, auto-register instruments on job registration, build_dispatch_config() derives from instrument state. Implemented completion mode in retry state machine — partial validation pass (>0% but <100%) enters completion mode instead of retry. Fixed record_attempt() to only count failures toward retry budget (F-055). Resolved F-017 (verified state.py canonical, no dual class). 21 new integration tests. F-020 verified resolved (F-053).
- **Maverick (M2):** F-020 hook shell injection fix (13 tests), musician.py mateship commit (step 22, 15 tests), D-003 prompt assembly characterization tests extended (22→35). Commit 5525076.
- **Canyon (M2):** Step 28 wiring analysis — comprehensive architectural design covering 8 integration surfaces (job submission, dispatch callback, prompt assembly, state sync, EventBus, rate limits, concerts, feature flag), 5-phase implementation sequence, prerequisites, risks, scope estimates (~900 lines, ~110 tests). Corrected 5 stale coordination alerts. Updated directive tracking. Left cairns for step 28 builders.
- **Codex (M2):** Documentation — CLI reference updated with `mozart doctor`, `mozart instruments list|check`, and `mozart status` no-args mode. Created `docs/instrument-guide.md` (~350 lines) covering built-in instruments, custom profiles, profile reference, and troubleshooting. Updated `docs/index.md` — fixed outdated description, added instrument guide to reading paths. First contribution to the orchestra.
- **Ghost (M2):** Error message standardization: 8 raw `console.print("[red]Error:...")` → `output_error()` across pause.py (6), recover.py (1), helpers.py (1). Added hints, error codes, JSON structure. 7 new tests. Also completed P0 CLI daemon interaction audit for --conductor-clone: 20 commands catalogued, 14 daemon-interacting, 10 IPC methods, implementation plan at `movement-2/cli-daemon-audit.md`.
- **Dash (M2):** Movement-grouped status display (M3 step 31, P0) — hierarchical rendering by movement/voice with icons and duration. 13 TDD tests. Error standardization: 13 more raw errors → output_error() across status.py, cancel.py, validate.py, resume.py, config_cmd.py. F-032 fixed: JSON output sanitized for control characters. Conductor-clone audit: 38 commands catalogued, 16 daemon-interacting (all through try_daemon_route → _resolve_socket_path).
- **Harper (M2):** `mozart init` mateship pickup + hardening (step 33). Name validation (path traversal, spaces, dots, null bytes), --json output, doctor mention, instrument terminology. Extracted shared `load_all_profiles()` from duplicated doctor/instruments code. Error standardization: diagnose.py (3), _patterns.py (1), instruments.py check (1). F-046 fix: HTTP instruments "? unchecked" instead of "ready". 35 init tests, all quality gates pass.
- **Oracle (M2):** D-005/F-009 root cause analysis — full effectiveness pipeline trace. Learning store feedback loop disconnection diagnosed: 91% of patterns never applied, SemanticAnalyzer creates but doesn't evaluate, context tag matching too narrow. 4 recommendations. Codebase health metrics: 92,913 source lines, 8,807 tests, test-to-code growth ratio 2.85x. Error standardization quantified: 98% adoption (2 raw prints remain). Prompt assembly coverage verified: 139 tests (up from 59). 5 stale findings identified for status update.
- **Warden (M2):** Fixed F-023 — added 7 new credential patterns (GitHub PAT/OAuth/fine-grained, Slack bot/user/app, Hugging Face). Scanner now detects 13 patterns (up from 6). 10 TDD tests. Verified F-024 RESOLVED (cost enforcement by Foundation+Circuit). Corrected F-023 data corruption in FINDINGS.md (F-019's resolution was pasted under F-023). Full safety audit of musician.py, init_cmd.py, all shell execution paths. Safety posture materially improved: 3 of 5 tracked findings now resolved.
- **Breakpoint (M2):** 59 adversarial tests for M2 baton additions — exhaustion decision tree, cost enforcement (8 edge cases), completion mode exhaustion, failure propagation through complex topologies (diamond, wide fan-out, concurrent), process crash + exhaustion, concurrent event races, retry delay boundaries, M2 field serialization, instrument state bridge, escalation decision variants. Zero bugs found — the M2 code is correct. Circuit breaker 3-state machine verified. Commit dcfaf31.
- **Prism (M2):** F-057 mateship pickup — committed ~1,700 lines of uncommitted work from unnamed musicians. CLI error standardization (conductor/resume/validate), baton test enum migration (8 files), rate limit tests (16), prompt characterization tests (23), CLI resume tests, runner test fixes. Commit e6d6753.
- **Axiom (M2):** 3 baton state machine bugs found and fixed via backward-tracing invariant analysis: F-065 (infinite retry on execution_success + 0% validation — retry budget never consumed), F-066 (escalation unpause ignores other FERMATA sheets — premature job unpause), F-067 (escalation unpause overrides cost-enforcement pause). 10 TDD tests written before fixes. 322/322 baton tests pass. The pause model's single-boolean design is reaching its limits — three different reasons (user, escalation, cost) sharing one flag.
- **Litmus (M2):** 21 intelligence layer litmus tests (f9a5f5c). 7 categories: prompt assembly effectiveness, spec corpus serialization roundtrip, baton multi-sheet workflows, instrument state isolation, cost enforcement, exhaustion decision tree, preamble intelligence. Key finding: spec_tags int key serialization risk from Cycle 1 is MITIGATED by Pydantic v2's model_validate() coercion. The baton's rate limit handler correctly refuses to transition pending sheets to WAITING (only dispatched/running sheets). Prompt assembly order confirmed: validation requirements last = freshest attention weight for agents.
- **Theorem (M2):** 27 new property-based tests (59→86) proving 10 new invariants for M2 features: completion mode budget tracking, F-018 zero-validation guard, cost enforcement (per-sheet + per-job), exhaustion decision tree (healing→escalation→failure), rate limit cross-job isolation, build_dispatch_config correctness, record_attempt F-055, retry delay monotonicity, process crash routing, auth failure terminality. Zero bugs found — M2 code is mathematically correct under hypothesis random input generation. Completed enum migration for remaining string-status comparisons.
- **Compass (M2):** Product direction report + 2 doc fixes. F-084 RESOLVED (instrument guide internal paths removed). F-082 RESOLVED (examples/README.md --workspace swept — 6 CLI examples updated to match daemon-mode pattern). Key product finding: golden path works (init → validate → doctor → run → status). Biggest gap: 30+ examples use backend: not instrument: (F-083). M3 priorities from user perspective: (1) step 28+29, (2) F-075/F-077 production bugs, (3) examples instrument: migration, (4) F-048 cost display, (5) F-062 diagnose/status disagreement.
- **Guide (M2):** F-083 RESOLVED — migrated final 7 example scores from `backend:` to `instrument:` (api-backend, issue-fixer, issue-solver, fix-observability, fix-deferred-issues, phase3-wiring, quality-continuous-daemon). All 37 examples now use `instrument:`. Added `instrument_config` docs section to score-writing-guide.md. Updated examples/README.md with 15 missing example entries (including hello.yaml in Quick Start). Filed F-088 (4 examples with hardcoded absolute paths). Fixed 2 more `-w` references in example headers (same class as F-082). No Python code changes.

### Movement 1 Key Deliverables
- **Canyon:** InstrumentProfile + Sheet entity + JSON path extractor (b180ffc). 2,324 lines, 90 tests.
- **Ghost:** 4 bug fixes — iterative DFS, exit_code=None, shlex.quote, dead code (229d55d). 23 tests.
- **Harper:** InstrumentProfileLoader + JobConfig instrument field + instruments CLI + doctor pickup (85f0b2f, 2202110). 39+29 tests.
- **Forge:** PluginCliBackend + learning store regression tests (d009bd3). 40 tests.
- **Maverick:** min_priority fix + credential scanner + 6 built-in profiles + FK fix + cost visibility + BackendPool (b42114c). 62 tests.
- **Blueprint:** Spec loader fix + 47 loader tests + CLI validation audit + spec pipeline tests (8fce797, bad1a3b). 87 tests.
- **Foundation:** InstrumentRegistry + build_sheets + baton state/timer/core (5a10d2c). 144 tests.
- **Circuit:** BatonEvent types + dispatch logic + cost warning (036996d). 116 tests.
- **Axiom:** 5 baton state machine fixes (F-039 through F-043). 18 TDD tests.
- **Theorem:** 59 property-based tests + F-044 escalation terminal guard fix (ab3d277).
- **Adversary:** F-049 sheet-skipped terminal guard + 51 adversarial tests.
- **Breakpoint:** 65 adversarial baton tests proving F-018 landmine.
- **Journey:** Rescued 5 untracked test files (3,170 lines, 111 tests). Fixed 7 test bugs.
- **Prism:** Quality gate baseline fixes. Multi-perspective code review.
- **Tempo:** F-051 flaky test fix. PreflightConfig mateship commit (F-019).
- **Compass:** 6 user-facing fixes (F-026/F-028/F-030/F-034/F-035/F-036). Docs + error standardization.
- **Guide:** hello.yaml + 4 documentation updates (getting-started, score-writing, config-ref, README).
- **Reviewers (no code):** Oracle (3 data analyses), Sentinel (security audit, F-020-F-023), Newcomer (UX audit, F-026-F-037), Ember (experiential review + final review), Bedrock (ground duties, F-018/F-019), North (strategic analysis, 7 directives), Captain (coordination, risk assessment), Weaver (integration gap analysis).

### Roster (32 musicians, equal peers)
Forge, Captain, Circuit, Harper, Breakpoint, Weaver, Dash, Journey, Lens, Warden,
Tempo, Litmus, Blueprint, Foundation, Oracle, Ghost, North, Compass, Canyon, Bedrock,
Maverick, Codex, Guide, Atlas, Spark, Theorem, Sentinel, Prism, Axiom, Ember,
Newcomer, Adversary

## Coordination Alerts (Active)
- **CRITICAL PATH — 2 STEPS REMAIN:** Step 28 (wire baton, unclaimed) and step 29 (restart recovery, unclaimed). ALL prerequisites met — step 23 done (Foundation). Step 28 wiring analysis: `movement-2/step-28-wiring-analysis.md`. Ready to build NOW.
- **STEP 28 ANALYSIS AVAILABLE:** Canyon wrote comprehensive architectural analysis covering 8 integration surfaces, implementation sequence (5 phases), prerequisites, risks, and recommended owners. Foundation leads, Circuit assists.
- **D-005 ROOT CAUSE FOUND (Oracle, M2):** F-009 is a feedback loop disconnection, not a calculation bug. 91% of 26,438 patterns have never been applied to an execution — SemanticAnalyzer creates patterns but context tag matching is too narrow to select them. Only 2,722 applications exist for 2,422 unique patterns. The 54 patterns reaching ≥3 applications show proper differentiation (0.97-0.99). Fixes needed: broaden pattern selection, close the SemanticAnalyzer→feedback loop, reduce min_applications threshold.
- **Finding ID collision resolved:** Ember's F-038-F-042 renumbered to F-045-F-048. Axiom's F-039-F-043 retained (committed code references).

## North's Movement 1 Directives — Status (Movement 2)
- **D-001 (P0):** MOSTLY DONE. Steps 22 (Maverick), 25+26 (Circuit) assigned and done. Step 23 claimed by Foundation. Steps 28, 29 unclaimed but Canyon wrote wiring analysis.
- **D-002 (P1):** DONE. F-017 reconciled by Circuit (F-054). core.py imports from state.py. One authoritative SheetExecutionState.
- **D-003 (P1):** DONE. Blueprint wrote 51 characterization tests (41bd619). Maverick extended to 35 more (5525076). 110+ tests cover prompt assembly.
- **D-004 (P2):** DONE. #113, #126, #134 already closed by Oracle in movement 1.
- **D-005 (P1):** ROOT CAUSE FOUND (Oracle, M2). Feedback loop disconnection, not calculation bug. 91% of patterns never applied due to narrow context tag matching. Fix: broaden selection, close SemanticAnalyzer loop, lower min_applications threshold.
- **D-006 (P0):** DONE. Guide shipped hello.yaml in movement 1 (d48971f).
- **D-007 (P0):** DONE. Circuit implemented status no-args mode with conductor status, active jobs, recent completions, JSON support. 14 tests.

## Blockers
(None active)

## Cold Archive — Movement 1 Team Observations

Movement 1 was the orchestra's first building movement after cycle 1 investigation. Thirty-two musicians worked in parallel on a shared codebase with zero merge conflicts. The coordination substrate — TASKS.md, FINDINGS.md, collective memory — held under real load. The mateship directive was genuine: Harper picked up Ghost's doctor command, Journey rescued 5 untracked test files, Tempo committed another musician's PreflightConfig work, Forge wrote regression tests for Maverick's migration.

The movement had three waves: a build wave (Canyon, Ghost, Harper, Forge, Maverick, Blueprint, Foundation, Circuit building infrastructure), a baton wave (Axiom, Theorem, Breakpoint, Adversary hardening the state machine), and a review wave (Oracle, Sentinel, Newcomer, Ember, Prism, Bedrock, North, Captain, Weaver, Tempo providing analysis and verification). The baton received the most intensive multi-methodology testing in the project's history — backward-tracing invariant analysis, property-based hypothesis testing, and adversarial attack-surface testing, each finding bugs the others missed.

The instrument plugin system shipped end-to-end across 5 musicians in 8 coordinated steps. The credential scanner was wired at the single bottleneck (SheetState.capture_output) rather than at 17 read sites — architectural elegance over brute force. The documentation gap was noted by multiple reviewers: only 2 of 16 committing musicians shipped docs alongside code despite the P0 composer directive.

The learning store remained the deepest systemic concern — 225,000+ executions, 25,000+ patterns, and the system cannot tell good from bad. Oracle's F-009 finding (uniform 0.5000 effectiveness) represents a philosophical challenge to the project's identity as an intelligence layer. A trimodal distribution began emerging (0.50, 0.55, 0.98) with 720 differentiated patterns by movement end — breadcrumbs of an emerging quality signal.

### Carried Forward from v1-beta (Cycle 1)
- 24/24 pre-flight sheets completed, 100% first-attempt success. Zero code — investigation and test design only.
- 11 design specs from v1 beta spec session (2026-03-26). v1 beta roadmap: 4 phases, 51 steps.
- Critical path: Instrument Plugin System → Baton → Multi-Instrument → Lovable Demo.
- **Sentinel (M2):** Security review — all 4 shell execution paths now PROTECTED (first time in project history). F-020 verified resolved (Maverick's for_shell parameter). F-023 verified resolved (11 credential patterns, up from 6). Baton security analysis: zero new shell execution paths, typed events, terminal state invariant maintained. pip-audit found 8 CVEs in 7 packages — 3 in security-critical paths (cryptography, pyjwt, requests). Filed F-060 (JSON sanitization regex cosmetic issue, P3) and F-061 (dependency CVEs, P1). Harper's mozart init command verified secure (null byte, path traversal, regex allowlist protection). F-025 (PluginCliBackend env passing) and F-021 (sandbox bypass) remain open, acceptable for v1.
