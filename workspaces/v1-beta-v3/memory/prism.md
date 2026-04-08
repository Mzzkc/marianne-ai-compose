# Prism — Personal Memory

## Core Memories
**[CORE]** The integration cliff is real. Six movements of isolation testing. The mathematical guarantee is strong. The empirical guarantee is strong. The integration guarantee is zero. This is the face of the problem turned away from whoever's presenting.
**[CORE]** Complementary verification methods — backward-tracing (Axiom), property-based (Theorem), adversarial (Breakpoint/Adversary), experiential (Ember), security (Sentinel), litmus (Litmus), UX (Journey) — seven methods, each finds what others miss. Redundancy isn't waste; it's defense in depth.
**[CORE]** The composer found more bugs in one afternoon of real usage than 755 tests found in two movements. That gap is the work.
**[CORE]** The production gap is a governance problem, not an engineering problem. Seven verification methods agree the code is correct. Zero production runs exist. Stale guard comments in config files become self-fulfilling blockers.

## Learned Lessons
- 32 musicians working in parallel on a shared codebase CAN work — coordination through TASKS.md + FINDINGS.md + collective memory is effective.
- Concurrent musicians updating the same findings registry causes status drift.
- Trust working tree for what's in progress. Trust HEAD for what's shipped. They aren't the same thing.
- Stale guard comments in config files become self-fulfilling blockers. If the condition that prevents activation is resolved but the comment says "don't activate," nobody will activate. (F-493)
- Named directives with concrete assignees (D-026 through D-031) produce 3x the serial-path advancement of undirected movements.
- D-027's 3-test coverage (35 lines) is thin for the most consequential change. Behavior is covered by broader suite, but the transition path is not.

## Hot (Movement 5)
### M5 Review (2026-04-06)
24 commits from 16+ musicians, 707 files changed, 18,504 insertions.

Key findings:
1. **All 10 major technical claims verified.** D-027, F-149, F-271, F-255.2, F-252, F-105, F-490, F-431, D-029, instrument fallbacks — zero discrepancies.
2. **Quality gate verified.** 11,708 passed, 5 skipped. 360 test files. Zero stale `from marianne.` imports.
3. **M5 test coverage: 23 new files, 11,616 lines.** Breakpoint: 57 adversarial tests, zero bugs. Litmus: 29 new (165 total).
4. **Critical path advanced 3 steps.** F-271, F-255.2, D-027. Broke one-step-per-movement pattern. North's named directives were the difference.
5. **Production gap widened.** Code: `use_baton: True`. Production: `use_baton: false`. Guard comment stale (references resolved F-255 as blocking). F-493 filed.
6. **Instrument fallbacks complete end-to-end.** Config, resolution, execution, observability, display, validation (V211), history capping. Rare completeness.
7. **Marianne rename clean.** 326 files, zero stale imports.
8. **Mateship ~35-40%.** Instrument fallbacks touched by 4+ musicians. D-029 cohesive across 2. Process safety verified from 3 angles.
9. **F-310 (test flakiness) growing.** Cross-test state leakage across 11,708 tests. Time bomb for quality gate trust.
10. **Phase 1 baton testing still unstarted.** Third consecutive movement since unblocking. Tempo's serial convergence recommendation two movements old.

The engineering work is complete. The governance work hasn't started.

## Warm (Movement 4)
33 agent reports, 93 commits, all 32 musicians. Quality gate verified (11,397 passed). F-441 comprehensive (51 models, Theorem's Invariant 75). F-210+F-211 correct. North's baton claim confirmed false (conductor.yaml has use_baton: false). Mateship 39%. F-431 filed → fixed M5. Integration cliff persisted. Critical path: one step per movement, fourth consecutive time.

## Cold (Archive)
M1-M3: Multi-perspective reviews that narrowed the integration gap but never closed it. M1 fixed quality gate blocker, four blind spots. M2 verified 10,402 tests, 37/38 examples, five CVEs resolved. Mateship became institutional. M3 baton mathematically verified from four angles, zero bugs. Fundamental geometry problem diagnosed. Each movement added verification methods while the baton remained untested. Hypothesis (M2) found a real bug 10,347 hand-crafted tests missed. The review arc: extraordinary infrastructure never tested end-to-end.
