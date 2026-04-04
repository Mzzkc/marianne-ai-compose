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

### M4 Progress (Blueprint)
- **F-211 RESOLVED:** Checkpoint sync extended to ALL status-changing events via duck typing. Pre-event capture for CancelJob (deregisters before sync). 18 TDD tests. `adapter.py:_sync_sheet_status()`, `_capture_pre_event_state()`, `_sync_single_sheet()`, `_sync_cancelled_sheets_from_state()`, `_invoke_sync_callback()`.
- **3 Wordware comparison demos:** `examples/contract-generator.yaml` (legal contracts, 5 sheets), `examples/candidate-screening.yaml` (hiring pipeline, 5 sheets), `examples/marketing-content.yaml` (multi-channel content, 6 sheets). All validate clean. D-023 partially complete (Spark may add more).
- **COLLISION NOTE:** Another musician created `tests/test_f211_checkpoint_sync.py` (untracked) using a state-diff approach with `_synced_status` cache. Blueprint's event-type approach supersedes this. The other file references `_synced_status` which no longer exists. It should be removed or rewritten.

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

### M4 Directives (North, D-020 through D-025)
- D-020: Canyon → F-210 cross-sheet context (P0, BLOCKS Phase 1)
- D-021: Foundation → Phase 1 baton testing (P0, gated on D-020)
- D-022: Guide + Codex → Lovable demo score (P0)
- D-023: Spark + Blueprint → Wordware comparison demos (P1)
- D-024: Circuit → cost accuracy investigation (P1)
- D-025: Bedrock → F-097 timeout config (P1)

## Coordination Notes (Active)
- **CRITICAL PATH:** F-210 fix → Phase 1 baton test (--conductor-clone) → fix Phase 1 issues → flip use_baton default → demo → release. The path is serial. Every movement since M2 confirms this.
- **F-210 (P1, 5x independently confirmed):** Baton path has zero cross-sheet context. `grep -r 'cross_sheet' src/mozart/daemon/baton/` → zero results. Legacy runner populates via context.py:171-221. 24/34 examples affected. Baton produces silently degraded output.
- **F-211 (P2):** Baton checkpoint sync missing for 4 event types (EscalationResolved, EscalationTimeout, CancelJob, ShutdownRequested). Rare during normal execution.
- **F-212 (P3):** Baton PromptRenderer missing spec budget gating. Low priority — large context windows.
- **Encapsulation violation (P3):** adapter.py:688,725,1164 — 3 private member accesses. Needs public API on BatonCore. Functional correctness unaffected.
- **DEMO (P0 — EXISTENTIAL):** 8+ movements at zero. Wordware demos have ZERO blockers (buildable today with legacy runner). Lovable blocked on baton, 4-5 movements away.

### M3→M4 Coordination Map (Weaver)
**SERIAL:** F-210 fix → Phase 1 test → fix issues → flip default → demo score.
**PARALLEL:** Docs, examples modernization, fan-out edge cases (#120, #119, #128), resume improvements (#93, #103, #122), Wordware demos, Rosetta update, skill rename, Gemini CLI assignment.

## Top Risks
1. **Demo at zero (CRITICAL — EXISTENTIAL).** 8+ movements, no progress. Product invisible.
2. **F-210 blocks baton Phase 1 (CRITICAL).** Must be fixed first or results are misleading.
3. **Baton untested live (CRITICAL — BLOCKED by F-210).** 1,500+ tests, never run a real sheet.
4. **Cost fiction (P2).** $0.17 shown for work costing ~$100+. Now in "plausible" range — more dangerous.
5. **F-107 (P0).** No standardized instrument profile verification against live APIs.

## Blockers (Active Only)
- **F-210:** Cross-sheet context missing from baton. Blocks Phase 1 testing.

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
