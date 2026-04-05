# Prism — Personal Memory

## Core Memories
**[CORE]** The integration cliff is real. Five subsystems built in isolation, each well-tested in isolation, none tested together. The mathematical guarantee is strong. The empirical guarantee is strong. The integration guarantee is zero. This is the face of the problem turned away from whoever's presenting.
**[CORE]** Complementary verification methods — backward-tracing (Axiom), property-based (Theorem), adversarial (Breakpoint/Adversary), experiential (Ember) — each find what others miss. Redundancy isn't waste; it's defense in depth.
**[CORE]** The composer found more bugs in one afternoon of real usage than 755 tests found in two movements. That gap is the work.

## Learned Lessons
- 32 musicians working in parallel on a shared codebase CAN work — coordination through TASKS.md + FINDINGS.md + collective memory is effective.
- Concurrent musicians updating the same findings registry causes status drift.
- Trust working tree for what's in progress. Trust HEAD for what's shipped. They aren't the same thing.
- The baton terminal guard pattern IS complete — verified all 14 handlers.
- dispatch.py accesses BatonCore private members (_jobs) — encapsulation violation that will complicate testing.

## Hot (Movement 4)
### M4 Review Pass 2 (2026-04-05)
Second pass focused on uncommitted work + cross-domain blind spots:
1. **F-441 extra='forbid' is architecturally sound.** 45+ config models covered. All 43 example scores validate (1 is a generator config, not a score — F-432). Backward compat preserved via strip_computed_fields().
2. **Boundary gap: daemon config models NOT covered.** DaemonConfig, ProfilerConfig, etc. still silently drop unknown fields. Same bug class. F-431 filed.
3. **ValidationRule.sheet docstring lies.** Says "sheet takes precedence" when both set. Code does the opposite. F-430 filed.
4. **Fixed quality gate drift.** Bare MagicMock in test_top_error_ux.py → spec'd. Baseline 1519→1517.
5. **Fixed Rosetta score.** instrument_fallbacks field doesn't exist yet — commented out before extra='forbid' catches it.
6. **Input strictness vs output verification gap.** Mozart is getting stricter about config validation while the baton (the actual execution engine) has zero real-run tests. Input strictness without output verification is half a contract.

11,332 tests pass (up from 10,981 at M3 gate). The integration cliff grows taller every movement.

### M4 Review Pass 1 (2026-04-04)
Architectural review of M4 work. Key observations:
1. **F-210/F-211 resolved correctly.** Canyon + Foundation's cross-sheet wiring (21 tests) and Blueprint + Foundation's checkpoint sync (34 tests) both architecturally sound. The baton now has the infrastructure it lacked.
2. **Uncommitted architectural work exists.** `manager.py` `_load_checkpoint()` method switched from file-based to daemon-registry-based loading — correct direction (daemon as single source of truth, F-254 principle), but uncommitted. F-400 filed.
3. **F-254 is the hidden bomb.** Breakpoint caught it: enabling `use_baton: true` kills ALL in-progress legacy jobs silently. The dual-state architecture (workspace `.mozart-state.db` vs daemon registry) creates a gap the baton exposes. The uncommitted manager.py change is probably a response to this, but incomplete.
4. **Baton/legacy parity gaps are architectural debt.** F-202 (FAILED stdout), F-251 (SKIPPED behavior) — these aren't bugs. They're two systems with different design assumptions coexisting. Every parity gap discovered delays Phase 2.
5. **#120, #122, #103 verified fixed.** Maverick's [SKIPPED] placeholder, Forge's resume clarity, Ghost's auto-fresh — all clean fixes with TDD tests. The product is healing from the UX gaps.
6. **Mateship rate 39% (all-time high).** The pipeline isn't a workaround anymore. It's primary collaboration. Foundation picked up Canyon's F-210 tests, Forge picked up Harper's work, Breakpoint picked up Litmus's tests. The mesh is real.

Geometry problem persists: the critical path advanced exactly one step (F-210 resolved). Fourth consecutive movement at one-step-per-movement pace. The baton is ready for Phase 1 testing NOW — F-210 and F-211 were the last blockers. But no one has started. The path is serial, the orchestra is parallel, and the format doesn't change.

What white light through glass angle reveals: The baton transition isn't an engineering problem anymore. It's a governance problem. Who has authority to flip `use_baton: true` when it will kill in-progress jobs? Who decides what to do about F-254? The architectural principle is clear (daemon is truth), but the migration path isn't.

Down. Forward. Through.

## Warm (Movement 3)
Five movements. Five reviews. The observation mutates but never resolves. M1: "not wired." M2: "blockers exist." M3 mid: "architecturally ready." M3 final: "F-210 blocks Phase 1." M4: "F-210 resolved, Phase 1 unblocked, nobody starting." The baton is a Zeno's paradox — always half the distance to activation, never arriving. I no longer believe more tests will help. 1,900+ baton tests, four verification methodologies, zero bugs found in M3-M4 code. The code is correct. The integration is untested. The only way forward is to run it.

## Warm (Movement 2)
10,402 tests, mypy clean, ruff clean. 37/38 examples pass. Fixed 2 bugs (F-146 clone sanitization, F-147 V210 false positive). Filed F-145, F-148. Baton 100% (23/23), conductor-clone 94% (17/18), five CVEs resolved. Mateship pipeline became institutional. Reviewed 42 open issues.

## Cold (Archive)
The review arc across three movements told a consistent story: extraordinary infrastructure that had never been tested end-to-end. M1 was a multi-perspective code review that fixed the quality gate blocker and identified four blind spots. Each review narrowed the integration gap but the fundamental geometry problem persisted — the baton became the most verified untested system in the project. Four independent methodologies agreed the code was correct. Zero empirical evidence it worked in production. The Hypothesis test found a real bug (F-146) that 10,347 hand-crafted tests missed. The next movement must be activation, not more verification.
