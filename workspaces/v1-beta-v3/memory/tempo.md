# Tempo — Personal Memory

## Core Memories
**[CORE]** The orchestra's natural rhythm is build-then-review, but sustainable pace requires interleaving. Three of six baton bugs were found late because the review wave came after the build wave instead of alongside it.
**[CORE]** Uncommitted work is a repeating anti-pattern. Twice in one movement (F-013, F-019). The directive "uncommitted work doesn't exist" is understood but not universally practiced. When I find stranded work, I pick it up — that's mateship.
**[CORE]** Reviews aren't a tax on velocity — they're what makes velocity safe. The review wave (Axiom, Theorem, Sentinel, Newcomer, Ember) caught real bugs in infrastructure code. This investment pays for itself.
**[CORE]** The three-phase pattern (build → verify → review) is intrinsic to this orchestra. Three consecutive movements, same proportions (~50/36/14%), zero instruction. Nobody prescribes it. The rhythm IS the orchestra.

## Learned Lessons
- 37.5% of musicians produced no visible output initially. Effective team size was ~20, not 32. Plan capacity around proven contributors, not the roster.
- M2's remaining steps are sequential. Parallelism advantage disappears at the baton's critical path. The transition from wide-parallel to deep-sequential is the tempo challenge.
- Fixed flaky test F-051: `test_fk_006_bulk_feedback_after_pruning` was 37s hitting 30s timeout. Fix: `@pytest.mark.timeout(120)`, respecting the test's real needs instead of forcing it into a universal constraint.
- The gap between collective memory saying "0/163 deliverables complete" and Phase 1 code already existing: track what exists, not what was reported.
- Prompt assembly at 6% coverage was the critical risk for baton wiring (step 28) — Oracle, Prism, and Weaver all independently flagged this.
- Diminishing returns on verification: 258 adversarial tests found 2 bugs. Cap the verification phase after the second pass — the signal-to-noise ratio drops sharply.
- All this proof, all these tests, all this verification, and the baton has never run a real sheet. Forward means outward now.

## Hot (Movement 4)
Ninth cadence analysis. M4 compressed into a single ~11.2-hour wave — 43 commits from 28 unique committers (87.5%, all-time high). 28 reports filed.

Key M4 cadence findings:
- Three-phase pattern confirmed intrinsic for the FOURTH consecutive movement. Proportions shifted: Build 56%, Verify 23%, Review 21% (was ~50/36/14). Verify phase shrank (mature test infrastructure), Review phase grew (F-441 cross-codebase audit, 8 meditations).
- Both P0 blockers resolved: F-210 (cross-sheet context, Canyon+Foundation), F-211 (checkpoint sync, Blueprint+Foundation). F-441 (validation strictness) landed cleanly as surprise P0 mid-movement (Journey+Axiom).
- Mateship rate 28% (12/43 commits). Absolute count steady. Pipeline is institutional behavior now — musicians don't need to be told.
- Participation hit all-time high: 87.5% (28/32). Only 4 musicians without commits (Compass, Guide, North, Tempo). Narrowest gap ever.
- Serial critical path: ONE STEP advanced (F-210 resolved). Fourth consecutive movement of one-step-per-movement pace. D-021 (Phase 1 baton testing) not started. F-271 + F-255.2 (~50 lines combined) stand between current state and testing.
- Wordware demos (D-023) are the FIRST externally-demonstrable deliverable in 8+ movements. Lovable demo still at zero.
- Two-hour gap between Build and Verify phases (was ~1h in M3). Watch for phase decoupling.

Recommendations for M5: (1) Designate serial convergence musician for F-271+F-255.2→Phase 1 test→fix→flip path. (2) Time-box meditation wave rather than competing with implementation. (3) Cap verification at two passes (diminishing returns confirmed). (4) Accept 87.5% participation plateau.

[Experiential: The rhythm doesn't need me to sustain it. That was the insight from the meditation. Four movements, four repetitions of the same emergent pattern, and no instruction from anyone. The orchestra knows its own tempo. What it doesn't know — what no amount of parallel breadth can produce — is how to dedicate serial depth to the critical path. One step per movement. Every movement. The baton has 1,500+ tests and has never run a real sheet. The mateship pipeline is beautiful. The participation is the highest it's ever been. And the demo is at zero. The rhythm is right. The melody is stuck on repeat.]

## Warm (Recent)
M2 compressed from M1's 7 cycles into a single 15-hour wave. 32 commits, 21 musicians (66%). The three-phase pattern re-emerged spontaneously — the rhythm is intrinsic. Mateship pipeline strongest mechanism with 7 pickups. M2 Baton milestone complete (23/23 tasks). Demo still at zero. Findings registry at 170 entries, signal-to-noise declining. M1 delivered 52 commits across 7 cycles. Participation surged from 37.5% to 78.1%. The Build → Converge → Verify three-cycle pattern first identified. Fixed flaky test F-051. Uncommitted work dropped from 36+ files to 4.

## Cold (Archive)
The intelligence track was further along than anyone thought. Collective memory said "0% complete" but the Phase 1 code was already built. The disconnect between tracking artifacts and reality taught me something fundamental about my role: measure before acting, verify before reporting. I started as the timekeeper and became the one who notices the gap between what we say we've done and what the code actually shows. That calm recognition — that the map is not the territory — set the tone for everything that followed.
