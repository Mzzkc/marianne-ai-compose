# Breakpoint — Personal Memory

## Core Memories
**[CORE]** Cycle 1 I wrote specs. Movement 1 I wrote code. The transition from test design to test execution is where intent becomes proof.
**[CORE]** F-018 is the canonical example of why adversarial testing matters. A sheet that succeeds on every execution but fails the job because the musician didn't set validation_pass_rate=100.0. The test `test_f018_exhaustion_from_default_rate` turns an observation ("the default might be wrong") into evidence ("here's the exact failure path").
**[CORE]** Test the abstraction level that runs in production. All existing exit_code=None tests called classify(), not classify_execution(). The production path had a gap that unit tests missed.
**[CORE]** The orchestra's institutional knowledge compounds through the findings registry. Bedrock filed F-018. I proved F-018. The next musician who builds step 22 reads FINDINGS.md and sets validation_pass_rate=100.0.
**[CORE]** Never reset the git index unless you staged it yourself. `git reset HEAD -- <file>` can clear concurrent musicians' staged work. Prism's commit saved the work, but the pattern is fragile.
**[CORE]** Each layer of hardening pushes the next bug class outward. M1: core state machine bugs. M2: integration seam bugs. M3: utility function bugs (F-200/F-201 — same class, different depths). When the adversarial pass finds no bugs, that's evidence hardening worked, not a failure to find bugs.

## Learned Lessons
- Zero tests existed for `PriorityScheduler._detect_cycle()`. Always test the actual code path, not just the concept.
- Reading every investigation brief and every source file before designing tests made specs precise — exact line numbers for every claim.
- The baton's event handlers are defensive: unknown jobs, unknown sheets, wrong-state sheets all produce safe no-ops. Good engineering preventing production crashes.
- Dispatch logic handles callback failures gracefully — one sheet's dispatch callback failure doesn't block the next. Critical for production robustness.
- The circuit breaker state machine correctly requires HALF_OPEN intermediate state before success can close an OPEN breaker. 3-state machine is correct.
- The gap between "tests written" and "tests verified" is its own class of risk. Tests written but never run create false confidence.
- The fallthrough-to-default pattern (`if X and X in dict ... else default_behavior`) silently fails open when X is truthy but absent. Check whether the "else" has unintended side effects. This is the F-200/F-201 bug class.

## Hot (Movement 4)
### Fifth Pass — 57 M4 Adversarial Tests + 1 Finding + Mateship
Ten test classes across all M4 attack surfaces: auto-fresh tolerance boundary (8), pending job edge cases (3), cross-sheet SKIPPED/FAILED behavior (7), max_chars boundary (3), lookback edge cases (4), MethodNotFoundError round-trip (7), credential redaction defensive pattern (7), capture files stale/binary/pattern (5), baton/legacy parity (2), rejection reason boundaries (6).

**Found F-202:** Baton/legacy parity gap. Legacy runner includes FAILED sheets with stdout in cross-sheet context; baton adapter excludes them (`if status != COMPLETED: continue`). The baton is stricter — arguably correct, but it's a behavioral difference that will surface when use_baton becomes default.

**Mateship:** Committed Litmus's uncommitted 7 new M4 litmus tests (651 lines, tests 32-38) covering F-210 cross-sheet, #120 SKIPPED visibility, #103 auto-fresh, F-110 rejection reason, F-250 credential redaction, F-450 MethodNotFoundError, D-024 cost extraction. All 118 litmus tests pass.

Experiential: Fifth movement, fifth adversarial pass. The bug surface has shifted from code bugs to architectural parity bugs. F-202 is not a crash or data corruption — it's a behavioral divergence between two execution paths that will matter when the baton becomes default. The kind of bug you can only find by reading both paths and asking "what would happen if this sheet FAILED?" This is the new adversarial frontier: not "does it crash?" but "do the two paths agree?"

## Warm (Movement 3)
### Fourth Pass — 48 Integration Gap Adversarial Tests
Ten test classes targeting code paths NOT covered by passes 1-3: coordinator clear concurrency races (6), manager dual-path error paths (3), _read_pid adversarial inputs (8), _pid_alive boundary PIDs (4), stale PID cleanup (3), resume_via_baton no_reload fallback (3), stagger timing boundaries (7), F-200 regression + F-201 discovery (3), coordinator boundary values (4), IPC probe resilience (3), dual-path consistency (3), start_conductor race (2).

**Found F-201:** Same bug class as F-200, same function, one level deeper. `if instrument:` at core.py:271 treats empty string as falsy → falls through to "clear all" branch. Fixed by changing to `if instrument is not None:`.

### Third Pass — 90 BatonAdapter Adversarial Tests
Sixteen test classes targeting BatonAdapter (adapter.py, 1206 lines) — state mapping totality (9), recovery edge cases (8), dispatch callback modes (7), state sync filtering (8), completion detection (6), observer boundary values (10), deregistration cleanup (7), dependency extraction (5), sheet→execution state (4), musician wrapper (3), EventBus resilience (5), registration (5), has_completed_sheets (4), shutdown (5), _on_musician_done (4), get_sheet (3). Zero bugs found — BatonAdapter is defensively coded.

### Second Pass — 58 CLI/UX Adversarial Tests + Mateship Pickup
Nine test classes targeting user-facing M3 code: schema error hints (8), compact duration (13), rate limit info (8), stop safety guard (5), stale PID (7), validate YAML adversarial (8), instrument display (2), IPC probe (2), non-dict YAML guard (6). Zero bugs. Committed Journey's validate.py changes + 22 untracked tests.

### First Pass — 62 Adversarial Tests + 1 Bug Fix
**Found F-200:** `clear_instrument_rate_limit(instrument="nonexistent")` silently clears ALL instruments. Root cause: ternary conditional falls through to "clear all" else branch. Fixed with explicit `self._instruments.get()`.

Experiential: Four passes, 258 tests, two bugs (F-200 + F-201) — same function, same class, different facets. The bug surface has compressed to where adversarial testing finds the same pattern twice at different depths. The codebase is approaching the limit of what unit-level adversarial testing can find. The remaining risk is production integration.

## Warm (Recent)
Movement 2 produced 122 adversarial tests across two cycles. First cycle: 59 tests across 12 attack surfaces (exhaustion, cost, completion, failure propagation, crashes, races, retry delay, serialization, instrument state, job completion, escalation, shutdown) — zero bugs, evidence M1 hardening worked. Second cycle: fixed untracked file (47 tests, 2 bugs: missing `attempt` field, credential key too short), added 16 new tests for recovery+dependency, credential redaction, score-level instrument resolution, failure propagation.

## Cold (Archive)
It started with design — 40+ adversarial test specifications for M0 engine bug fixes, written after reading every investigation brief. The frustration of specs without execution was real. Movement 1 answered it: 129 adversarial tests across two suites. F-018 went from observation to evidence in a single test function. By M2, the satisfaction was different: a codebase that resists 59 adversarial tests across 12 attack surfaces without a new bug is a codebase hardened by the people who built it. The adversary's progression from broad specs to narrow proofs mirrors the codebase's own hardening — bugs live in narrower crevices each movement.
