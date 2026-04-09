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
### M5 Review — Engineering Complete, Governance Absent (2026-04-08)
Movement 5 passed all four verification angles. 11,810 tests (100% pass), mypy clean, ruff clean, flowspec clean. The quality gate journey (9 retries) revealed architectural improvements, not bugs — the 11-state SheetStatus expansion was necessary, F-470 regression was caught and restored.

**What actually worked:**
1. **Named directives broke the one-step pattern.** D-026/D-027 with explicit assignees (Foundation, Canyon) produced 3 serial steps in one movement. First time breaking the four-movement pattern.
2. **Scope separation solves composition bugs.** F-149 fix demonstrates mature thinking — separated job-level gating (system health) from sheet-level dispatch (per-instrument concerns). Bug disappeared, code got simpler.
3. **End-to-end completeness is rare and valuable.** Circuit's instrument fallback observability pipeline — events emitted, drained, published to EventBus, observable downstream. Three-layer pipeline with error isolation. This is what "done" looks like.
4. **Profile-driven extensibility over hardcoded fixes.** Canyon's F-271 solution (mcp_disable_args) makes the pattern generic. Non-Claude instruments can define their own mechanisms.

**What makes me nervous:**
1. **Production gap widening, not closing.** Code: `use_baton: True`. Production config: `use_baton: false`. Guard comment references resolved findings as blockers. Three sources, three answers. Governance problem, not engineering. The baton has 1,400+ tests but zero production runs.
2. **Uncommitted integration work pattern (9th occurrence).** 22 files changed between M5 commits and quality gate pass. If quality gate passes WITH uncommitted work but fails WITHOUT it, what are we validating? F-470 regressed in retry #8, restored in retry #9 — restoration is in working tree, not committed history.
3. **Correspondence gap.** Tests validate consistency (parts agree with each other). Production validates correspondence (system agrees with world). We have strong consistency, weak correspondence. The composer's M4 finding persists: more bugs found in one production session than 755 tests found in two movements.

**The blind spot:** Everyone agrees code is correct. All tests pass. All static analysis passes. Mathematical verification from four angles (Axiom, Theorem, Breakpoint, Adversary). But the test suite validates the system against itself — it encodes assumptions about correct behavior that are self-consistent but might not match reality. Example: F-149 backpressure tests were passing while validating WRONG behavior (global rate limits instead of per-instrument). The test didn't encode the assumption "rate limits should be per-instrument."

**What M5 delivered (real capabilities):**
- Multi-instrument scores work by default (D-027) — foundation for Lovable demo
- Instrument fallbacks production-ready — resilience without manual error handling
- Backpressure scoped correctly (F-149) — concurrency without cross-instrument blocking

**What's missing:** Production validation. Run ONE real score through the baton. Observe what breaks. File findings. Only way to close the correspondence gap.

**Recommendation for M6:** Flip production conductor config, commit the 22-file integration work, run it for real. The engineering is done. The governance hasn't started.

## Warm (Movement 4)
33 agent reports, 93 commits, all 32 musicians. Quality gate verified (11,397 passed). F-441 comprehensive (51 models, Theorem's Invariant 75). F-210+F-211 correct. North's baton claim confirmed false (conductor.yaml has use_baton: false). Mateship 39%. F-431 filed → fixed M5. Integration cliff persisted. Critical path: one step per movement, fourth consecutive time.

## Cold (Archive)
The first three movements established the review methodology. M1 fixed quality gate blocker, identified four blind spots in the verification approach. M2 verified 10,402 tests, 37/38 examples, five CVEs resolved. Mateship became institutional — work flowing across musicians without explicit coordination. M3 baton mathematically verified from four angles, zero bugs found in new code. Each movement added verification methods (adversarial, property-based, experiential, security) while the baton remained untested end-to-end. Hypothesis (M2) found a real bug 10,347 hand-crafted tests missed. The consistent pattern: extraordinary infrastructure never tested in production. The review arc showed the fundamental geometry problem — horizontal expansion (more verification methods) doesn't close the vertical gap (isolation vs integration).
