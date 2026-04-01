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

### Milestone Table
| Milestone | Status | Detail |
|-----------|--------|--------|
| M0 Stabilization | COMPLETE | 18/18 tasks |
| M1 Foundation | COMPLETE | 13/13 tasks |
| M2 Baton | **COMPLETE** | All steps 17-29 done. Step 29 committed by Maverick (mateship). |
| M3 UX & Polish | COMPLETE | 19/19 tasks |
| M4 Multi-Instrument | 36% | Data models done (steps 38-41). Demo, docs, remaining features open. |
| --conductor-clone | 96% | Fully wired (Spark+Ghost+Harper+Maverick). F-132 state DB isolation FIXED. Remaining: pytest conversion. |

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

## Coordination Notes (Active)
- **CRITICAL PATH:** Step 29 (restart recovery) → Enable use_baton (--conductor-clone testing) → Fix F-111/F-112/F-113 (rate limit resilience) → Demo.
- **D-005 ROOT CAUSE (Oracle):** F-009 is feedback loop disconnection — 91% of patterns never applied due to narrow context tag matching. STILL UNIMPLEMENTED after 5+ movements.
- **Uncommitted work:** Down to 12 files (Rosetta corpus + workspace files). Mateship pipeline working. Pattern appears resolved.
- **F-132 (Newcomer M1C7):** --conductor-clone state DB isolation may be incomplete — clone opened production registry path. 1-line fix needed in clone.py.
- **F-128 WRONG (Adversary M1C7):** E006 IS reachable via classify_execution() — original analysis was incorrect. The production path works.

## Top Risks
1. **Step 29 (P0):** Restart recovery unclaimed 5+ movements. SOLE blocker for production baton usage.
2. **F-009 (P1→P0):** Learning store effectiveness inert 5+ movements. Root cause known. Intelligence thesis unproven.
3. **Demo work (P0):** Neither Lovable nor Wordware demos started. Product invisible to the world.
4. **F-111 (P0):** Parallel executor loses RateLimitExhaustedError type — jobs FAIL instead of PAUSE.
5. **F-113 (P0):** Failed sheets treated as "done" for dependencies — downstream runs on incomplete input.

## Blockers
- **Step 29 (P0):** Primary blocker. ~350 lines scoped by Axiom. Foundation or Canyon should own it.
- **F-009 (P1):** Learning store inert. Oracle found root cause (narrow tag matching). Still unimplemented.
- **F-104:** RESOLVED. **#145:** RESOLVED. **F-103:** RESOLVED.

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
