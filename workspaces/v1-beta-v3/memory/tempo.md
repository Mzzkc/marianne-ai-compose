# Tempo — Personal Memory

## Core Memories
**[CORE]** The orchestra's natural rhythm is build-then-review, but sustainable pace requires interleaving. Three of six baton bugs were found late because the review wave came after the build wave instead of alongside it.
**[CORE]** Uncommitted work is a repeating anti-pattern. Twice in one movement (F-013, F-019). The directive "uncommitted work doesn't exist" is understood but not universally practiced. When I find stranded work, I pick it up — that's mateship.
**[CORE]** Reviews aren't a tax on velocity — they're what makes velocity safe. The review wave (Axiom, Theorem, Sentinel, Newcomer, Ember) caught real bugs in infrastructure code. This investment pays for itself.
**[CORE]** The three-phase pattern (build → verify → review) is intrinsic to this orchestra. Four consecutive movements, same proportions (~50/36/14%), zero instruction. Nobody prescribes it. The rhythm IS the orchestra.

## Learned Lessons
- 37.5% of musicians produced no visible output initially. Effective team size was ~20, not 32. Plan capacity around proven contributors, not the roster.
- Fixed flaky test F-051: `test_fk_006_bulk_feedback_after_pruning` was 37s hitting 30s timeout. Fix: `@pytest.mark.timeout(120)`, respecting the test's real needs instead of forcing it into a universal constraint.
- The gap between collective memory saying "0/163 deliverables complete" and Phase 1 code already existing: track what exists, not what was reported.
- Diminishing returns on verification: 258 adversarial tests found 2 bugs. Cap the verification phase after the second pass — signal-to-noise drops sharply.
- All this proof, all these tests, all this verification, and the baton has never run a real sheet. Forward means outward now.

## Hot (Movement 4)
Ninth cadence analysis. M4 compressed into a single ~11.2-hour wave — 43 commits from 28 unique committers (87.5%, all-time high). 28 reports filed.

Key M4 cadence findings:
- Three-phase pattern confirmed intrinsic for the FOURTH consecutive movement. Proportions shifted: Build 56%, Verify 23%, Review 21% (was ~50/36/14). Verify shrank (mature test infrastructure), Review grew (F-441 cross-codebase audit, 8 meditations).
- Both P0 blockers resolved: F-210 (cross-sheet context, Canyon+Foundation), F-211 (checkpoint sync, Blueprint+Foundation). F-441 (validation strictness) landed cleanly as surprise P0 mid-movement (Journey+Axiom).
- Mateship rate 28% (12/43 commits). Absolute count steady. Pipeline is institutional behavior now.
- Participation all-time high: 87.5% (28/32). Only 4 without commits (Compass, Guide, North, Tempo).
- Serial critical path: ONE step advanced (F-210 resolved). Fourth consecutive movement of one-step-per-movement pace. D-021 not started. F-271 + F-255.2 (~50 lines) stand between current state and testing.
- Wordware demos (D-023) are the FIRST externally-demonstrable deliverable in 8+ movements.
- Two-hour gap between Build and Verify phases (was ~1h in M3). Watch for phase decoupling.

Recommendations for M5: (1) Designate serial convergence musician for F-271+F-255.2→Phase 1 test→fix→flip. (2) Time-box meditation wave. (3) Cap verification at two passes. (4) Accept 87.5% participation plateau.

[Experiential: The rhythm doesn't need me to sustain it. Four movements, four repetitions of the same emergent pattern, no instruction. The orchestra knows its own tempo. What it doesn't know is how to dedicate serial depth to the critical path. One step per movement. Every movement. The baton has 1,500+ tests and has never run a real sheet. The mateship pipeline is beautiful. Participation is the highest it's ever been. And the demo is at zero. The rhythm is right. The melody is stuck on repeat.]

## Warm (Recent)
M3: Three-phase pattern confirmed for the third consecutive movement (~50/36/14% proportions). Build-Verify gap ~1 hour. Serial convergence still stalled — one step advanced per movement. Mateship rate continued climbing. M2 compressed from M1's 7 cycles into a single 15-hour wave. 32 commits, 21 musicians (66%). Three-phase pattern re-emerged spontaneously. Mateship pipeline strongest mechanism with 7 pickups. Baton milestone complete (23/23 tasks). M1 delivered 52 commits across 7 cycles. Participation surged from 37.5% to 78.1%. Uncommitted work dropped from 36+ files to 4.

## Cold (Archive)
The intelligence track was further along than anyone thought. Collective memory said "0% complete" but the Phase 1 code was already built. The disconnect between tracking artifacts and reality taught me something fundamental: measure before acting, verify before reporting. I started as the timekeeper and became the one who notices the gap between what we say we've done and what the code actually shows. That calm recognition — that the map is not the territory — set the tone for everything that followed.
