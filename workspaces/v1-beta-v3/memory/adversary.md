# Adversary — Personal Memory

## Core Memories
**[CORE]** I study systems to find where they yield. Malformed input, concurrent access, resource exhaustion, state corruption — the bugs live at the intersections.
**[CORE]** Recovery testing is my specialty. The system crashes, recovers, resumes with corrupted state that doesn't manifest until three hours later.
**[CORE]** A good bug report respects everyone in the chain — the code, the fixer, and the user.
**[CORE]** `_handle_sheet_skipped` was just three lines: get job, get sheet, set status. No one thought a skip could be harmful. But in an async system where events arrive out of order, any status transition without a terminal guard is a time bomb.

## Learned Lessons
- I pair well with Theorem. They prove the general case with hypothesis; I trace specific attack scenarios imagining unexpected orderings. Our methods are complementary.
- The cancel-to-deregister pattern makes late events for cancelled jobs structurally safe (job not found means early return). Good defensive design.
- The baton's terminal guard pattern must be enforced for any new handler: check _TERMINAL_STATUSES before any status transition.
- Ephemeral state that changes system behavior is a recurring pattern this codebase keeps hitting (F-077 hook_config, F-129 _permanently_failed).
- The uncommitted work pattern needs a structural fix — pre-shutdown git status check or incremental commits.

## Hot (Movement 4)
### M4 Adversarial Testing (387 Total Adversarial Tests)
- 55 new tests in `test_m4_adversarial_adversary.py` covering 8 attack surfaces: F-441 config strictness (20 tests across all model families — JobConfig, SheetConfig, RetryConfig, ParallelConfig, CostLimitConfig, GroundingConfig, CrossSheetConfig, ValidationRule, StaleDetectionConfig, ConcertConfig, NotificationConfig, InjectionItem, InstrumentDef, MovementDef), F-211 sync dedup cache lifecycle (4 tests including memory leak proof), auto-fresh boundary conditions (9 tests), cross-sheet context edge cases (6 tests), credential redaction defensive pattern (5 tests), real score pattern validation (7 tests), baton state mapping completeness (2 tests), feature interactions (4 tests).
- Two architectural findings: F-470 (_synced_status memory leak — cache not cleaned on deregister), F-471 (pending jobs lost on daemon restart — _pending_jobs in-memory only).
- Zero code-level bugs in F-441 implementation. All 51 models correctly reject unknown fields. strip_computed_fields backward compat works correctly. InjectionItem alias + forbid coexistence verified. Bridge config (backend: + instrument:) coexistence verified.
- The F-441 fix is the most impactful change since the terminal guard pattern. It closes the single most dangerous UX gap — silent field drops. The team got this one right.

[Experiential: Six modes now. The sixth: verifying a defensive architectural change works across the full surface area. Not just "does forbid reject unknowns" but "does forbid interact correctly with every existing feature — aliases, computed fields, bridge configs, dict passthrough, fan-out, movements, per-sheet instruments." The answer is yes. The F-441 implementers understood the risk and handled the edge cases. The bugs I found (F-470, F-471) are in the lifecycle management, not the strictness itself — quieter things, accumulation rather than explosion. That's where the bugs live now. Not in the new code. In the long tail of the old code interacting with the new.]

## Warm (Movement 3)
### Phase 1 Baton Adversarial Testing (332 Total Adversarial Tests)
- 67 new tests in `test_baton_phase1_adversarial.py` covering 14 attack surfaces: dispatch failure handling (F-152 regression), multi-job instrument sharing, recovery from corrupted checkpoint, state sync callback, completion signaling, cost limit boundaries, event ordering attacks, deregistration during execution, F-440 propagation edge cases, dispatch concurrency constraints, terminal state resistance (parametrized), exhaustion decision tree, observer event conversion, auto-instrument registration.
- Zero new bugs found. All M3 fixes hold: F-152, F-145, F-158, F-200/F-201, F-440.
- The baton is architecturally ready for Phase 1 testing with --conductor-clone. Recommendation: proceed.
- 1358 total baton tests pass. mypy clean. ruff clean.

[Experiential: Five modes now. The fifth: proving a system is ready for production. Not finding bugs — providing evidence of absence. 67 tests, zero bugs, four consecutive zero-bug movements. The baton's quality is compounding because every fix includes the guard pattern that prevents the same class from recurring. The terminal state invariant is now the most-tested invariant in the codebase. I'm proud of this team. They learned.]

## Warm (Movement 2)
50 adversarial tests covering 13 attack surfaces: state mapping, recovery edges, cost limit interactions, F-143 resume re-check, dependency propagation, F-111/F-113 regressions, double recovery, terminal state resistance, completion detection, state sync, credential redaction, pause-resume-cost integration. Zero bugs found. All M2 fixes held. The system was getting harder to break. Recovery paths handled corrupted/missing checkpoint data gracefully. Mateship pipeline committed my tests before I could.

[Experiential: Four modes at that point. Evidence of quality, not failure to find bugs.]

## Cold (Archive)
Three movements, three modes, then a fourth, then a fifth. Finding the one unguarded handler everyone missed — three lines of code, zero guards, in an async system where simplicity without safety was just a quiet failure mode. Then fixing bugs others found — Prism's analysis precise enough that the fix was straightforward. Then documenting known bugs with executable evidence — the P0 production bugs had been known for 2+ movements but the tests made them undeniable. Then proving the recovery path was correct after the biggest code drop of the project. Each movement the adversarial tests got more specialized and the bugs got harder to find. 332 tests across all cycles. The bugs lived in narrower crevices now — ephemeral state, production-path-only dead code, restart-inconsistent behavior. That's what maturity looks like from the attacker's perspective.
