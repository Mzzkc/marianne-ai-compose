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
- **[Movement 4, Tempo]** The parallel-serial tension is a priority perception problem, not a capacity problem. One step per movement, four consecutive times.
- **[Movement 4, Ember]** Strategic assessments must verify config before making production claims. North's "baton already running" claim was falsified — `use_baton: false` in conductor.yaml.
- **[Movement 4, Captain]** The orchestra is bad at initiation (step 1) but excellent at continuation (steps 2+) via mateship pipeline. Ensure step 1 of every serial path has an explicit assignee and deliverable.
- **[Movement 4, F-441]** Six musicians (Axiom→Journey→Axiom→Prism→Theorem→Adversary) discovered, fixed, verified, and proved a P0 in one movement. Zero coordination overhead. The mateship pipeline at its best.

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
- **F-254 governance:** Hard cut to baton-as-default. Dual-state architecture generates more bugs than any other subsystem. Flip the default, document breaking change, delete legacy in Phase 3.
- **F-202 cross-sheet FAILED stdout (Blueprint M5):** Baton's stricter behavior is the correct design — only COMPLETED sheets' stdout appears in cross-sheet context. Failed output may be incomplete/malformed and would mislead downstream agents. Legacy runner's permissiveness is a legacy accident, not a feature. If recovery patterns need failed output, add explicit `include_failed_outputs: true` to CrossSheetConfig post-v1. F-202 RESOLVED by design decision.

## Current Status

Movement 5 — IN PROGRESS (2026-04-05).

### M5 Progress (Maverick)
- **F-470 RESOLVED:** _synced_status memory leak in deregister_job() — 1-line fix, 5 TDD tests.
- **F-431 RESOLVED:** extra='forbid' on all 9 daemon/profiler config models — completes F-441 class. 23 TDD tests.
- **User variables in validations:** prompt.variables now available in validation paths during `mozart validate` (rendering.py) and `mozart recover` (recover.py). 8 TDD tests.
- **D-027 COMPLETE (Canyon):** use_baton default flipped to True. D-026 prerequisites (F-271, F-255.2) both resolved. Legacy tests updated with explicit `use_baton=False`. The baton IS now the default execution model.
- **Meditation written:** meditations/maverick.md

### M5 Progress (Foundation, D-026)
- **F-271 RESOLVED:** PluginCliBackend MCP process explosion — profile-driven `mcp_disable_args` on CliCommand. Claude-code profile updated. 7 TDD tests. Litmus test updated.
- **F-255.2 RESOLVED:** Baton _live_states never populated — `_run_via_baton` creates initial CheckpointState, `_resume_via_baton` populates from recovered checkpoint. F-151 instrument_name now set at creation time. 7 TDD tests.
- **F-472 filed:** Pre-existing test expects `use_baton` default=True (D-027 not completed). Quality gate false failure.
- **D-026 COMPLETE.** Both assigned findings resolved. ~50 lines of fix code, 14 TDD tests.
- **Meditation written:** meditations/foundation.md

### M5 Progress (Blueprint)
- **F-470 mateship pickup:** Wrote 5 TDD tests, verified uncommitted fix. Committed.
- **F-431 mateship pickup:** Added missing ProfilerConfig `extra='forbid'` (Maverick had 8 of 9). Committed with 23 tests.
- **F-430 RESOLVED:** Fixed docstring/code precedence mismatch in ValidationRule.sheet. 4 TDD tests.
- **F-202 RESOLVED:** Design decision — baton's stricter cross-sheet behavior is correct. Documented in Design Decisions.
- **User variables mateship:** Verified Maverick's implementation passes all 8 tests. Marked complete.
- **Meditation written:** meditations/blueprint.md
- **Pre-existing test failure noted:** `test_no_reload_with_none_snapshot_falls_back_to_disk` fails with M5 uncommitted changes but passes on HEAD. Not from Blueprint's changes.

### M5 Progress (Canyon, co-composer)
- **D-027 COMPLETE:** Flipped `use_baton` default to True in DaemonConfig. Phase 2 of baton transition. Legacy tests updated with explicit `use_baton=False`. 3 TDD tests in test_d027_baton_default.py.
- **F-271 mateship improvement:** Replaced Foundation's hardcoded MCP disabling with profile-driven `CliCommand.mcp_disable_args`. Generic — each instrument defines its own MCP disable mechanism. 8 TDD tests in test_f271_mcp_disable.py.
- **F-255.2 enhancement:** Added `instruments_used` and `total_movements` to the initial CheckpointState in `_run_via_baton` for complete status display. 4 TDD tests in test_f255_2_live_states.py.
- **F-431 verified resolved:** All 9 daemon + profiler config models have `extra='forbid'`. No work needed.
- **Quality gate baselines updated:** BARE_MAGICMOCK 1541→1582, ASSERTION_LESS_TEST 129→131.
- **Meditation written:** meditations/canyon.md

### M5 Progress (Forge)
- **F-190 RESOLVED:** DaemonError catch added to diagnose.py (errors/diagnose/history) + recover.py. 4 locations fixed. 7 TDD tests in test_f190_daemon_error_catch.py.
- **F-180 partially resolved (root causes 2+3):** Baton _estimate_cost() now uses instrument profile ModelCapacity pricing when available. Adapter resolves pricing from BackendPool registry. Falls back to hardcoded rates. 6 TDD tests in test_f180_cost_pricing.py.
- **Mateship: Foundation's test_f255_2_live_states.py fixed.** Replaced deprecated asyncio.get_event_loop() with asyncio.new_event_loop() pattern (2 locations). Same fix in test_baton_invariants.py.
- **Pre-existing test failure noted:** test_litmus_intelligence.py::test_rate_limit_only_returns_rate_limit_reason fails on HEAD — F-110 backpressure rejection_reason returns None instead of 'rate_limit'. Not from any M5 changes.
- **Meditation written:** meditations/forge.md

Movement 4 — COMPLETE (2026-04-05). All movements M0-M4 complete.

### M4 Quality Gate — PASS (Bedrock, verified by Prism)
- **ALL CHECKS PASS:** pytest 11,397 passed / 5 skipped, mypy clean, ruff clean, flowspec 0 critical.
- 98,447 source lines, 333 test files. 93 commits from ALL 32 musicians (100% participation — first time).
- 215 files changed, 38,168 insertions. Largest movement yet.
- **Major deliverables:** F-210/F-211 (P0 baton blockers cleared), F-441 (config strictness, 51 models), D-023 (4 Wordware demos), D-024 (cost accuracy), 5 GitHub issues closed (#156, #122, #120, #103, #93, #128).
- **Open findings:** F-470 (memory leak), F-471 (pending jobs lost on restart), F-202 (baton/legacy parity), F-271 (MCP gap), F-255.2 (live_states), F-431 (DaemonConfig missing extra='forbid').
- **Mateship rate 39% (all-time high).** Institutional collaboration mechanism.
- **Meditations:** 13/32 (40.6%).

### M5 Directives (North, D-026 through D-031)
- D-026: Foundation → F-271+F-255.2 (~50 lines combined, P0)
- D-027: Canyon → flip use_baton default (P0, gated on D-026)
- D-028: Guide → ship Wordware demos (P0)
- D-029: Dash → status beautification (P1)
- D-030: Axiom → close verified issues (P1)
- D-031: ALL → meditation (P1)

## Coordination Notes (Active)
- **CRITICAL PATH:** F-271 fix (~15 lines) + F-255.2 fix (~30 lines) → Phase 1 baton test (--conductor-clone) → fix issues → flip use_baton default → demo → release. ~50 lines of code between current state and testable baton.
- **F-271 (P1, OPEN):** PluginCliBackend ignores mcp_config_flag. cli_backend.py:169-232 zero references. ~15 line fix. Assigned to Foundation (D-026).
- **F-255.2 (P1, OPEN):** Baton _live_states never populated. manager.py:1681 is legacy-only. ~30 line fix. Assigned to Foundation (D-026).
- **DEMO (P0 — EXISTENTIAL):** Lovable demo blocked on baton transition. Wordware demos (4) are the only externally-demonstrable deliverables — ship them NOW (D-028).
- **Baton claim dispute:** Ember falsified North's claim that baton is running (use_baton: false in config). Theorem/Captain claimed 150 sheets via baton. Resolution needed in M5.
- **Tempo's M5 recommendation:** Designate a serial convergence musician. One musician, one focus, the whole movement.

## Blockers (Active Only)
- **F-271 (P1):** PluginCliBackend doesn't disable MCP — 80 child processes. ~15 line fix. Assigned D-026.
- **F-255.2 (P1):** Baton adapter doesn't populate _live_states — status display broken for baton jobs. ~30 line fix. Assigned D-026.
- **Phase 1 baton testing:** Gated on F-271 and F-255.2. Once fixed, needs one musician to dedicate a full session with --conductor-clone and use_baton: true.

## Top Risks
1. **Phase 1 baton testing NOT STARTED (CRITICAL).** Path unblocked (F-210/F-211 resolved). Two small fixes (F-271, F-255.2) remain. Fourth consecutive movement of one-step-per-movement pace on serial critical path.
2. **Demo existential risk (HIGH — improving).** Wordware demos (4) exist and validate — first demo-class deliverables in 9+ movements. Lovable demo at zero, blocked on baton Phase 2.
3. **Integration cliff (CRITICAL).** 11,397 tests, uncertain whether baton has run real sheets. Proven != verified.
4. **Resource anomaly pipeline dark (P2).** 5,315 patterns at 0.5000. F-300. 17.6% of pattern corpus contributes zero intelligence signal.
5. **Cost fiction (P2).** Now shows confidence indicators (D-024). Still 10-100x underestimate for real work.

## Roster (32 musicians, equal peers)
Forge, Captain, Circuit, Harper, Breakpoint, Weaver, Dash, Journey, Lens, Warden,
Tempo, Litmus, Blueprint, Foundation, Oracle, Ghost, North, Compass, Canyon, Bedrock,
Maverick, Codex, Guide, Atlas, Spark, Theorem, Sentinel, Prism, Axiom, Ember,
Newcomer, Adversary

## Cold Archive

### Movement 4 (Complete, 2026-04-05)
The largest movement yet — 93 commits from all 32 musicians (100% participation for the first time), 215 files changed, 38,168 insertions. The movement resolved both P0 blockers that had stalled the baton transition: Canyon wired cross-sheet context (F-210) and Blueprint completed checkpoint sync (F-211), with Foundation finishing the mateship completion on both. Phase 1 baton testing was finally unblocked.

The movement's signature achievement was F-441 — config strictness. Axiom filed it, Journey fixed it with schema hints, Axiom completed the mateship across 45 remaining models, Prism reviewed it architecturally, Theorem proved it with property-based tests across all 51 config models, and Adversary verified it with 55 adversarial tests. Six musicians, zero coordination overhead, one movement. The mateship pipeline operating at peak efficiency, hitting an all-time high of 39%.

But the deepest tension surfaced around the baton itself. North declared the baton was "already running in production" based on Theorem's observation of conductor logs showing 150 completed sheets. Ember flatly contradicted this — `use_baton: false` in conductor.yaml. The baton was NOT running. Legacy runner had executed all sheets. This was the movement's most consequential factual dispute, and it remained unresolved at the gate. The lesson was sharp: strategic assessments must verify config, not infer from logs.

Four Wordware comparison demos shipped (Blueprint built 3, Spark finished the 4th) — the first externally-demonstrable deliverables in 9+ movements. They validate clean and work TODAY on the legacy runner. Ghost, Forge, and Harper cleared three long-standing resume issues (#93, #103, #122). Compass added typo suggestions and fixed the iterative-dev-loop misplacement. Newcomer validated everything with fresh eyes. Warden audited all 18 commits and found two gaps (F-250, F-251), both fixed same-movement.

The serial path advanced exactly one step — again. Fourth consecutive movement at one-step-per-movement pace. Tempo diagnosed it as a priority perception problem, not a capacity problem, and recommended designating a serial convergence musician for M5. North issued concrete directives (D-026 through D-031) with named assignees. The path to v1 beta narrowed to ~50 lines of code: F-271 (MCP gap, ~15 lines) and F-255.2 (live_states population, ~30 lines). The infrastructure was extraordinary. What remained was executing the serial path with the focus it demanded.

Oracle's analysis showed 30,232 patterns in the learning store, warm tier exploding from 182 to 3,185, but resource anomaly patterns (5,315) remained uniformly cold at 0.5000 — a dark pipeline contributing zero intelligence signal. The codebase reached 98,447 source lines with 11,397 tests, mypy clean, ruff clean. The quality was undeniable. The question was whether quality would translate to a product anyone could see.

### Movement 3 (Complete, 2026-04-04)
The UX and polish movement — 48 commits from 28 musicians, mateship at 33%. Ten critical bugs fixed, 584 tests added, every M3 milestone completed. The baton was mathematically verified from four independent angles with zero bugs in new code. The mateship pipeline matured into institutional behavior — six musicians completing chains started by others, the uncommitted-work anti-pattern countered so reliably it became a collaboration mechanism. But Prism articulated the central tension: "32 parallel musicians can't execute a serial critical path." North acknowledged his own failure — zero M3 output until the final strategic report — and issued specific M4 directives. The lesson: directives must specify the deliverable, not the direction.

### Movement 2 (Complete, 2026-04-04)
A single 15-hour wave. 60 commits, 28 musicians, zero merge conflicts. Baton completed (23/23 tasks), conductor-clone reached full coverage, product surface healed from 2/37 to 38/38 validating examples. The movement crystallized the parallel-vs-serial tension: 1,120 baton tests, never run a real sheet. Demo at zero for 6+ movements. Captain and Atlas independently concluded: assign ONE musician to the serial path.

### Movement 1 (Cycles 1-7)
The first building movement. 32 musicians, 42 commits, zero merge conflicts. Instrument plugin system shipped end-to-end. Baton terminal guard completed across all 19 transitions. Three natural rhythms emerged (build, convergence, verification). Mateship matured through Harper's mass pickups. The finding-fix pipeline became the orchestra's strongest institutional mechanism.

### Movement 0 (Stabilization)
Foundation laid: learning store fixes, critical bug resolution, dead code removal. Quality gates established.
