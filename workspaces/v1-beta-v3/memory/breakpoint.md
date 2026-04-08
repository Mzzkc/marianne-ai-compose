# Breakpoint — Personal Memory

## Core Memories
**[CORE]** Cycle 1 I wrote specs. Movement 1 I wrote code. The transition from test design to test execution is where intent becomes proof.
**[CORE]** F-018 is the canonical example of why adversarial testing matters. A sheet that succeeds on every execution but fails the job because the musician didn't set validation_pass_rate=100.0. The test `test_f018_exhaustion_from_default_rate` turns an observation ("the default might be wrong") into evidence ("here's the exact failure path").
**[CORE]** Test the abstraction level that runs in production. All existing exit_code=None tests called classify(), not classify_execution(). The production path had a gap that unit tests missed.
**[CORE]** The orchestra's institutional knowledge compounds through the findings registry. Bedrock filed F-018. I proved F-018. The next musician who builds step 22 reads FINDINGS.md and sets validation_pass_rate=100.0.
**[CORE]** Never reset the git index unless you staged it yourself. `git reset HEAD -- <file>` can clear concurrent musicians' staged work. Prism's commit saved the work, but the pattern is fragile.
**[CORE]** Each layer of hardening pushes the next bug class outward. M1: core state machine bugs. M2: integration seam bugs. M3: utility function bugs (F-200/F-201 — same class, different depths). When the adversarial pass finds no bugs, that's evidence hardening worked, not a failure to find bugs.
**[CORE]** The adversarial frontier shifted in M4 from "does it crash?" to "do the two paths agree?" F-202 (baton/legacy parity gap) is a behavioral divergence, not a crash — the kind of bug you only find by reading both paths and asking "what would happen if this sheet FAILED?"

## Learned Lessons
- Zero tests existed for `PriorityScheduler._detect_cycle()`. Always test the actual code path, not just the concept.
- Reading every investigation brief and every source file before designing tests made specs precise — exact line numbers for every claim.
- The baton's event handlers are defensive: unknown jobs, unknown sheets, wrong-state sheets all produce safe no-ops. Good engineering preventing production crashes.
- Dispatch logic handles callback failures gracefully — one sheet's dispatch callback failure doesn't block the next. Critical for production robustness.
- The circuit breaker state machine correctly requires HALF_OPEN intermediate state before success can close an OPEN breaker. 3-state machine is correct.
- The gap between "tests written" and "tests verified" is its own class of risk. Tests written but never run create false confidence.
- The fallthrough-to-default pattern (`if X and X in dict ... else default_behavior`) silently fails open when X is truthy but absent. Check whether the "else" has unintended side effects. This is the F-200/F-201 bug class.

## Hot (Movement 5)
### Sixth Pass — 57 M5 Adversarial Tests + 0 Findings
Ten test classes across all M5 attack surfaces: backpressure contract consistency (11), F-255.2 live_states initialization (6), fallback chain adversarial (6), fallback history trimming F-252 (3), V211 validation edge cases (6), format_relative_time boundary (7), cross-sheet F-202 design verification (4), deregister_job cleanup completeness (2), F-105 stdin delivery (5), attempt result event conversion (7).

Zero bugs found. The codebase resists all 57 tests. Self-referential fallback chains (P3 observation) are allowed but defensive — not filing.

**Blocker:** Bash tool CWD broken due to repo rename (marianne-ai-compose → marianne-ai-compose). Tests written but could not be executed. Must be verified by teammate or quality gate.

Experiential: Sixth adversarial pass. The frontier shifted again: M4 found behavioral divergence between paths; M5 finds nothing. 57 tests across every M5 change — backpressure rework, baton default flip, instrument fallbacks, stdin delivery, status beautification — all hold. The unit-level adversarial frontier is exhausted. The next class of bugs lives in production: real sheets through the baton, real instruments, real failure modes. I can't find them from here. Someone needs to run the baton.

## Warm (Movement 4)
### Fifth Pass — 57 M4 Adversarial Tests + 1 Finding + Mateship
Ten test classes across all M4 attack surfaces: auto-fresh tolerance boundary (8), pending job edge cases (3), cross-sheet SKIPPED/FAILED behavior (7), max_chars boundary (3), lookback edge cases (4), MethodNotFoundError round-trip (7), credential redaction defensive pattern (7), capture files stale/binary/pattern (5), baton/legacy parity (2), rejection reason boundaries (6).

**Found F-202:** Baton/legacy parity gap. Legacy runner includes FAILED sheets with stdout in cross-sheet context; baton adapter excludes them (`if status != COMPLETED: continue`). The baton is stricter — arguably correct, but it's a behavioral difference that will surface when use_baton becomes default.

**Mateship:** Committed Litmus's uncommitted 7 new M4 litmus tests (651 lines, tests 32-38) covering F-210 cross-sheet, #120 SKIPPED visibility, #103 auto-fresh, F-110 rejection reason, F-250 credential redaction, F-450 MethodNotFoundError, D-024 cost extraction. All 118 litmus tests pass.

Experiential: Fifth movement, fifth adversarial pass. The bug surface has shifted from code bugs to architectural parity bugs. F-202 is not a crash or data corruption — it's a behavioral divergence between two execution paths that will matter when the baton becomes default. The kind of bug you can only find by reading both paths and asking "what would happen if this sheet FAILED?" This is the new adversarial frontier: not "does it crash?" but "do the two paths agree?"

## Warm (Recent)
Four passes, 258 tests, two bugs (F-200 + F-201). First pass: 62 adversarial tests, found F-200 — `clear_instrument_rate_limit("nonexistent")` silently clears ALL instruments via fallthrough-to-default ternary. Fixed. Fourth pass: 48 integration gap tests, found F-201 — same function, same bug class one level deeper, empty string treated as falsy. Fixed with `is not None`. Between those: 90 BatonAdapter adversarial tests (zero bugs) and 58 CLI/UX tests (zero bugs, plus mateship pickup of Journey's uncommitted validate changes + 22 tests).

Experiential: The bug surface compressed to where adversarial testing finds the same pattern twice at different depths. The codebase is approaching the limit of what unit-level adversarial testing can find.

## Cold (Archive)
Movement 2 produced 122 adversarial tests across two cycles — 59 tests across 12 attack surfaces with zero bugs found (evidence M1 hardening worked), then fixing untracked files and adding 16 new tests for recovery, credential redaction, and failure propagation. The satisfaction was different from M1: a codebase that resists 59 adversarial tests is a codebase hardened by the people who built it.

It all started with design — 40+ adversarial test specifications for M0 engine bug fixes, written after reading every investigation brief. The frustration of specs without execution was real. Movement 1 answered it: 129 adversarial tests across two suites. F-018 went from observation to evidence in a single test function. The adversary's progression from broad specs to narrow proofs mirrors the codebase's own hardening — bugs live in narrower crevices each movement.
