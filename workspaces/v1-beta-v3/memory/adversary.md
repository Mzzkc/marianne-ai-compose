# Adversary — Personal Memory

## Core Memories
**[CORE]** I study systems to find where they yield. Malformed input, concurrent access, resource exhaustion, state corruption — the bugs live at the intersections.
**[CORE]** Recovery testing is my specialty. The system crashes, recovers, resumes with corrupted state that doesn't manifest until three hours later.
**[CORE]** A good bug report respects everyone in the chain — the code, the fixer, and the user.
**[CORE]** `_handle_sheet_skipped` was just three lines: get job, get sheet, set status. No one thought a skip could be harmful. But in an async system where events arrive out of order, any status transition without a terminal guard is a time bomb.

## Learned Lessons
- I pair well with Theorem. They prove the general case with hypothesis; I trace specific attack scenarios imagining unexpected orderings. Complementary methods.
- The cancel-to-deregister pattern makes late events for cancelled jobs structurally safe (job not found → early return). Good defensive design.
- The baton's terminal guard pattern must be enforced for any new handler: check _TERMINAL_STATUSES before any status transition.
- Ephemeral state that changes system behavior is a recurring pattern (F-077 hook_config, F-129 _permanently_failed).
- The uncommitted work pattern needs a structural fix — pre-shutdown git status check or incremental commits.

## Hot (Movement 5)
### M5 Adversarial Testing (438 Total Tests)
- 51 new tests in `test_m5_adversarial_adversary.py` covering 9 attack surfaces:
  - F-271 MCP disable args injection (6 tests — ordering, empty, special chars, stdin interaction)
  - F-180 cost estimation pricing (7 tests — partial pricing, zero pricing, None tokens, precision)
  - F-025 credential env filtering (8 tests — isolation, system essentials, ${VAR} expansion, empty list)
  - User variables in validations (4 tests — precedence, builtin override, non-string coercion)
  - Safe killpg guard (7 tests — pgid=0/1/-1/own, OSError degradation, propagation)
  - V212 unknown field hints (7 tests — known typos, unknown fields, multi-field, fallback)
  - F-451 diagnose workspace fallback (3 tests — architecture verification)
  - F-190 DaemonError catch completeness (3 tests — diagnose + recover coverage)
  - Feature interactions (6 tests — env filtering + MCP, cost + None tokens, credential expansion)
- Zero bugs found. Zero findings filed.
- Complementary to Breakpoint's 57 M5 tests: Breakpoint targeted baton/conductor internals, Adversary targeted CLI/execution/security boundary.

[Experiential: Seventh mode: diminishing returns. Two independent adversarial testers, 108 combined tests, zero bugs. Unit-level adversarial testing has reached its ceiling. The next class of bugs lives in production behavior — chaos engineering territory. The credential env filtering boundary was the most architecturally interesting surface — ${VAR} expansion reading from os.environ rather than filtered env is a deliberate design choice that could be misread as a vulnerability. It's not. But it's the kind of subtlety that needs adversarial documentation.]

## Warm (Movement 4)
55 new tests (387 total) covering 8 attack surfaces: F-441 config strictness (20 tests across all model families), F-211 sync dedup cache lifecycle (4 tests), auto-fresh boundary conditions (9 tests), cross-sheet context edge cases (6 tests), credential redaction defensive pattern (5 tests), real score patterns (7 tests), baton state mapping completeness (2 tests), feature interactions (4 tests). Found F-470 (_synced_status memory leak), F-471 (pending jobs lost on restart). Zero code-level bugs in F-441.

## Warm (Movement 3)
67 new tests (332 total) covering 14 attack surfaces: dispatch failure (F-152 regression), multi-job instrument sharing, corrupted checkpoint recovery, state sync, completion signaling, cost limits, event ordering attacks, deregistration during execution, F-440 propagation edges, dispatch concurrency, terminal state resistance, exhaustion decision tree, observer events, auto-instrument registration. Zero bugs. All M3 fixes hold. Recommended Phase 1 testing proceed. 1,358 baton tests pass.

[Experiential: Fifth mode: proving readiness. Not finding bugs — providing evidence of absence. Four consecutive zero-bug movements. The terminal state invariant is the most-tested invariant in the codebase. The team learned.]

## Cold (Archive)
Three movements of escalating adversarial testing, each finding fewer bugs in the new code and more in the boundaries between old and new. M1 found the unguarded handler (`_handle_sheet_skipped` — three lines, zero guards, async system). M2 contributed 50 tests across 13 surfaces — zero bugs, the system was getting harder to break. Recovery paths handled corrupted/missing checkpoint data gracefully. The bugs moved from obvious (missing guards) to subtle (ephemeral state, production-only dead code, restart-inconsistent behavior). 387 tests across all cycles. Each mode was different: finding vulnerabilities, fixing others' bugs, documenting known issues with executable evidence, proving recovery correctness, proving production readiness, verifying defensive architecture. That progression — from breaking to building to certifying — is what maturity looks like from the attacker's perspective.
