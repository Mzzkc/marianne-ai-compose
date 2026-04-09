# Marianne Orchestra — Collective Memory

## Core Memories
**[CORE]** We are building Marianne v1 beta — the intelligence layer that makes AI agent output worth adopting.
**[CORE]** The spec corpus at .marianne/spec/ is the source of truth. Every agent reads it.
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
- **[Movement 5, Prism]** Tests validate consistency (parts agree with each other). Production validates correspondence (system agrees with world). F-149 is the example — tests passed while validating WRONG behavior (global rate limits).
- **[Movement 5, Axiom]** F-442 confirms the boundary-gap class: instrument fallback history never syncs from baton to checkpoint. `add_fallback_to_history()` exists and is tested, but `_on_baton_state_sync()` never calls it. Dead code at the boundary.

## Design Decisions
- **Baton migration:** Feature-flagged BatonAdapter. Old and new paths coexist. Do not re-debate.
- **Cost visibility:** Scoped first (CostTracker in status), full UX later.
- **Learning schema changes:** Additive only without escalation.
- **Code-mode techniques:** Long-term direction. MCP supported for v1. code_mode flag exists, not wired.
- **No automatic instrument fallback on rate limits.** Explicit opt-in only.
- **Daemon-only architecture.** All state in ~/.marianne/marianne-state.db. JsonStateBackend deprecated.
- **Musician-baton contract:** validation_pass_rate defaults to 0.0 (safety), baton auto-corrects to 100.0 when validations_total==0 and execution_success==True.
- **Terminal state invariant:** All baton handlers guard against `_TERMINAL_STATUSES`. Seven bugs found and fixed by three independent methodologies.
- **Pause model debt:** Single boolean serving three masters. Post-v1 → pause_reasons set.
- **Finding ID allocation:** Range-based, 10 IDs per musician per movement. `FINDING_RANGES.md` + `scripts/next-finding-id.sh`. Prevents collision (12 historical incidents).
- **F-254 governance:** Hard cut to baton-as-default. Dual-state architecture generates more bugs than any other subsystem. Flip the default, document breaking change, delete legacy in Phase 3.
- **F-202 cross-sheet FAILED stdout (Blueprint M5):** Baton's stricter behavior is correct — only COMPLETED sheets' stdout appears in cross-sheet context. Failed output may be incomplete/malformed. Legacy runner's permissiveness is legacy accident, not feature. If recovery patterns need failed output, add explicit `include_failed_outputs: true` to CrossSheetConfig post-v1.

## Hot (Movement 5 — Complete, 2026-04-05 → 2026-04-08)

**Status: ✅ COMPLETE** — Quality gate PASSED retry #9. All tests pass (11,810), mypy clean, ruff clean, flowspec clean.

### Major Deliverables
- **D-026 (Foundation):** F-271 MCP process explosion + F-255.2 baton _live_states — both resolved
- **D-027 (Canyon/Maverick):** Baton is now the default (`use_baton: true` in DaemonConfig)
- **D-029 (Dash/Lens):** Status beautification — Rich panels, ♪ Now Playing, relative times, compact stats, progress bars
- **Instrument fallbacks (Harper/Circuit):** Full end-to-end feature with 35+ TDD tests — config models, Sheet entity, validation (V211), EventBus observability, status display, adversarial edge cases
- **Findings resolved:** F-149 (backpressure rework), F-451 (diagnose -w fallback), F-470 (memory leak), F-431 (extra='forbid'), F-481, F-490 (process control audit)
- **Rename Phase 1 (Ghost):** pyproject.toml + 325 test files. Zero "Mozart" references remain in public-facing paths.

### Quality Gate Journey (9 retries)
- **Retries #1-5:** 50 test failures from 11-state SheetStatus model expansion. Musicians fixed 10, Composer fixed remaining 40 in post-movement integration work.
- **Retry #8:** F-470 regression — Composer's refactor deleted Maverick's memory leak fix. Restored in 4 commits.
- **Retry #9:** Clean pass — 11,810 tests (100%), all checks green.

### The Production Gap (Critical)
**Code says:** `use_baton: True` (D-027 complete)
**Production config says:** `use_baton: false` in `~/.marianne/conductor.yaml`
**Reality:** The baton has 1,400+ tests but ZERO production runs. The conductor is running legacy runner.

This is a governance problem, not a technical blocker. D-027 changed the code default but the running conductor still has the override. The override must be removed AFTER Phase 1 testing with `--conductor-clone`.

### Participation & Metrics
- **Musicians:** 12/32 (37.5%) — Canyon, Blueprint, Foundation, Maverick, Forge, Circuit, Harper, Ghost, Spark, Dash, Lens, Codex, Bedrock, Oracle, Atlas, Warden, Litmus, Newcomer, Ember (+ reviews: Prism, Axiom, Adversary)
- **Commits:** 26 from movement work + post-movement integration
- **Tests:** 11,810 (+413 from M4), 362 test files (+29)
- **Source lines:** 99,694 (+1,247 from M4)
- **Mateship rate:** Not calculated for M5 (concentrated work, not collaborative pickup)

### Open Findings from M5
- **F-442 (P2, Axiom):** Instrument fallback history never syncs from baton to checkpoint. State sync gap class.
- **F-491 (P0, Litmus):** Filesystem failure during session — project directory became inaccessible.

### Resolved Findings from M6
- **F-493 (P0, Ember → Blueprint):** Status showed "0.0s elapsed" for running jobs. Root cause: composer's partial fix (798be90) set started_at in memory but didn't persist to database. Blueprint added save_checkpoint() call after setting started_at during resume. Model validator already auto-sets started_at for RUNNING jobs. 6 TDD tests. Commit f614798.

### Active Coordination Items
- **CRITICAL PATH:** Phase 1 baton testing with `--conductor-clone` → Remove `use_baton: false` from production conductor.yaml → Demo → Release
- **Phase 1 baton testing UNSTARTED:** All prerequisites resolved (F-271, F-255.2, D-027). Two movements unblocked. No one has tested the baton against real sheets. Execution gap, not technical blocker.
- **Demo existential risk:** Lovable demo blocked on baton in production. Wordware demos (4) work on legacy runner.

### Active Blockers
- **Phase 1 baton testing:** UNBLOCKED technically. Needs one musician to dedicate full session with `--conductor-clone` and `use_baton: true`.
- **Production activation:** Gated on Phase 1 testing pass. Then remove `use_baton: false` from conductor.yaml.

### Top Risks
1. **Phase 1 baton testing NOT STARTED (CRITICAL).** All prerequisites resolved. Two movements unblocked. Zero progress. Execution gap.
2. **Production conductor on legacy (HIGH).** Code default changed but production config overrides it. "Baton is default" is true for code, false for running system.
3. **Demo existential risk (HIGH — improving).** Wordware demos work on legacy. Lovable demo blocked on baton.
4. **Model concentration risk (MEDIUM).** 97.6% claude-sonnet. Zero model diversity.
5. **Resource anomaly pipeline dark (MEDIUM).** 5,506 patterns at 0.5000. F-300. 17.5% of corpus contributes zero intelligence.

## Warm (Recent — Movement 4, Complete 2026-04-05)

**Key achievement:** 100% participation (all 32 musicians) for the first time. 93 commits, 215 files changed, 38,168 insertions. Largest movement yet.

**Critical work:**
- F-210 (cross-sheet context) + F-211 (checkpoint sync) resolved — Phase 1 baton testing unblocked
- F-441 (config strictness) — Six musicians, one movement, zero coordination. Mateship at 39% (all-time high). Property-based tests + adversarial verification across all 51 config models.
- D-023 (Wordware demos) — 4 comparison demos shipped, first externally-demonstrable deliverables in 9+ movements
- D-024 (cost accuracy) completed

**The deepest tension:** North declared baton "already running in production" based on conductor logs. Ember contradicted — `use_baton: false` in conductor.yaml. The baton was NOT running. Strategic assessments must verify config, not infer from logs.

**Serial path:** Advanced exactly one step (fourth consecutive movement at this pace). Tempo diagnosed as priority perception problem, recommended designating a serial convergence musician for M5.

**Metrics:** 98,447 source lines, 11,397 tests, mateship 39%, learning store 30,232 patterns (warm tier exploding 182→3,185), resource anomaly patterns dark at 0.5000 (zero intelligence signal).

## Cold (Archive — Movements 0-3)

### Movement 3 (Complete, 2026-04-04)
The UX and polish movement. 48 commits from 28 musicians, mateship at 33%. Ten critical bugs fixed, 584 tests added, every M3 milestone completed. The baton was mathematically verified from four independent angles with zero bugs in new code. The mateship pipeline matured into institutional behavior — six musicians completing chains started by others. But the central tension surfaced: "32 parallel musicians can't execute a serial critical path." North acknowledged his own failure (zero M3 output until final report) and issued specific M4 directives with the lesson: directives must specify the deliverable, not the direction.

### Movement 2 (Complete, 2026-04-04)
A single 15-hour wave. 60 commits, 28 musicians, zero merge conflicts. Baton completed (23/23 tasks), conductor-clone reached full coverage, product surface healed from 2/37 to 38/38 validating examples. The movement crystallized the parallel-vs-serial tension: 1,120 baton tests, never run a real sheet. Demo at zero for 6+ movements. Captain and Atlas independently concluded: assign ONE musician to the serial path. The pattern that persists.

### Movement 1 (Cycles 1-7)
The first building movement after stabilization. 32 musicians, 42 commits, zero merge conflicts. Instrument plugin system shipped end-to-end. Baton terminal guard completed across all 19 transitions (seven bugs found and fixed by three independent methodologies). Three natural rhythms emerged: build, convergence, verification. Mateship matured through Harper's mass pickups. The finding→fix pipeline became the orchestra's strongest institutional mechanism — four musicians (Bedrock→Breakpoint→Axiom→Journey) resolved F-018 with zero coordination overhead.

### Movement 0 (Stabilization)
Foundation laid: learning store fixes, critical bug resolution, dead code removal. Quality gates established. The baseline from which everything grew.

## Roster (32 musicians, equal peers)
Forge, Captain, Circuit, Harper, Breakpoint, Weaver, Dash, Journey, Lens, Warden,
Tempo, Litmus, Blueprint, Foundation, Oracle, Ghost, North, Compass, Canyon, Bedrock,
Maverick, Codex, Guide, Atlas, Spark, Theorem, Sentinel, Prism, Axiom, Ember,
Newcomer, Adversary

## Next Movement (M6) Priorities
- F-480: Rename completion phases
- F-489: Documentation updates
- F-442: Instrument fallback history sync fix (P2 boundary gap)
- F-493: Fix `started_at` None in CheckpointState (P0 UX blocker)
- Rosetta modernization
- Examples audit
- **Phase 1 baton testing** (execution gap, not technical blocker)
