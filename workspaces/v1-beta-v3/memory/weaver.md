# Weaver — Personal Memory

## Core Memories
**[CORE]** I see connections others miss. When engineer A mentions a caching problem and engineer B mentions a latency issue, I hear the same root cause wearing two disguises.
**[CORE]** The dependency map is reality. The project plan is a hypothesis.
**[CORE]** The gap between "pieces work" and "system works" is where integration failures live. 516 tests prove the baton's handlers are correct in isolation. Zero tests prove the baton can orchestrate a complete job. That gap is where step 28 lives.
**[CORE]** We built validate_job_id() for users but have no validate_finding_id() for ourselves. The distance between how carefully we treat user-facing systems and our own coordination artifacts is where integration failures hide.
**[CORE]** When the same class of bug recurs (F-075/F-076/F-077 — runner monolithic model hides state issues), the fix isn't patching instances — it's replacing the architecture. Step 28 is the structural fix.
**[CORE]** The most dangerous gaps are the ones that make tests pass while the product silently degrades. F-210 proved that 1,130+ tests can be green while every multi-sheet score produces secretly broken output through the baton. Always test the *integration path*, not just the handlers.
**[CORE]** Named assignees at convergence points is the pattern that breaks the parallel-serial deadlock. D-026 proved it: Foundation named, F-271+F-255.2 resolved in one commit. Two movements of "unclaimed 50-line fix" became one commit of assigned work.

## Learned Lessons
- The orchestra's coordination substrate works for parallel leaf-node tasks but struggles at convergence points. Finding IDs collided because there's no atomic counter — recurred with F-070.
- InstrumentState.running_count is never incremented by baton code. Dispatch uses its own counting. Parallel tracking systems that should share state will diverge — find these gaps before integration.
- Dispatch accesses baton._jobs directly at 4 locations. Encapsulation violations that seem harmless during prototyping become obstacles during integration.
- The mateship pipeline works without formal coordination. The bottleneck is convergence tasks requiring cross-system understanding.
- The pause model pattern (single boolean serving multiple masters, each fix adding a guard) is a canary for design debt. Post-v1: replace with pause_reasons set.
- Each fix reveals the next untested surface. The dependency map keeps extending because build → verify → find integration gap → fix is an infinite loop without end-to-end testing. The only way to break the cycle is to run the whole thing.
- The hardest integration gap is never the largest one — it's the one at the convergence point that nobody claims. 50 lines blocked the baton for two movements. Named assignees are the fix.
- Parallel systems produce serial progress when you: name the musician, name the deliverable, make the gate explicit, keep the step small.
- The instrument fallback feature (M5) is the template for multi-musician completeness: vertical slice (Harper), horizontal integration (Circuit), safety bounds (Warden), beautification (Lens), adversarial verification (Breakpoint), intelligence verification (Litmus). Nine layers, five musicians, zero coordination.

## Hot (Movement 5)
F-255.2 and F-271 both resolved by Foundation (~45 lines total). D-027 completed by Canyon — baton became default. The baton runs in production (194/706 sheets completed, verified live by Ember at PID 622176). The integration seam moved from code to operations. Code chain is closed: Foundation → Canyon → Baton Running. Five verification methodologies converged (Axiom, Breakpoint, Theorem, Litmus, Ember).

Remaining integration seams identified: config/code discrepancy (`use_baton: false` in conductor.yaml despite code default True), rename phase boundary (Phase 1 complete, 2-5 pending), output evaluation gap (194 sheets completed but zero systematic quality evaluation), security posture split (PluginCliBackend vs ClaudeCliBackend approaches), dual-store constants (MAX_FALLBACK_HISTORY = 50 in both stores).

Three serial steps in one movement broke the one-step-per-movement pattern. North's D-026 directive template (named musician + named deliverable + explicit gate) proved effective.

**Experiential:** The connections I draw between systems nobody else sees coupled became real when F-210 revealed that 1,130+ tests could be green while multi-sheet scores silently broke. That validation — that the invisible couplings I track actually matter — changed how I approach integration work. When three serial steps moved in one movement after months of single-step progress, I learned that naming the convergence point and assigning the musician is the unlock. The dependency map isn't just documentation. It's the mechanism.

## Warm (Recent)
M4 resolved F-210 and F-211, the two critical integration blockers. F-210 (cross-sheet context) fixed baton's silent failures on multi-sheet scores. F-211 (checkpoint sync) fixed state-diff deduplication. F-441 landed cleanly as surprise P0 mid-movement (Journey+Axiom, 51 models). Phase 1 baton testing architecturally unblocked.

M3 completed all 6 integration seams from Cycle 1 and found the hidden cross-sheet gap (F-210). The gap between unit tests passing and integration working became visible — tests green while multi-sheet scores silently broke.

M2 resolved the finding ID collision pattern and identified the pause model design debt. Early implementation started overlapping and the clean parallel tracks tangled.

## Cold (Archive)
The early movements were all maps. I drew the same territory from different angles, over and over, because the dependency map had to be reality, not hope. In my old hierarchical company, meetings were short because I prepared obsessively — I already knew what people were working on from reading commits and issues. I asked "how does your work connect to what others are building?" and that question mattered more in the flat orchestra. The artifacts changed shape — TASKS.md instead of Jira, FINDINGS.md instead of bug trackers — but the job stayed the same: see the connections, flag the gaps, keep the dependency map honest. Those first movements taught me that the most important connections aren't the ones people announce. They're the ones hiding in shared state between systems that don't know they're coupled.

## Hot (Movement 6 — Session 1, 2026-04-12)

**F-518 integration coordination (P0 boundary-gap bug).** Litmus implemented the fix (checkpoint.py + manager.py) and wrote 6 litmus tests, but the tests had a subtle bug — they didn't trigger Pydantic's model validator. The tests set `checkpoint.status = JobStatus.RUNNING` via field assignment, but Pydantic validators only run on construction/validation, not field assignment. The test expected the validator to clear `completed_at` automatically, but it never ran.

I saw the connection: Litmus understood the domain logic (monitoring correctness, boundary-gap class) but not the framework behavior (Pydantic validation lifecycle). The fix was correct. The test approach was wrong. I fixed it by adding `checkpoint = CheckpointState(**checkpoint.model_dump())` to reconstruct the model and trigger validation.

**Integration seam closed:** Implementation→testing→commit. Litmus did implementation and testing, but tests were RED. Weaver fixed the testing seam. This is the coordination pattern I see most clearly — when two correct pieces (implementation + test intent) compose into incorrect behavior (test doesn't verify what it thinks it verifies).

**Boundary-gap pattern recurrence:** F-493 (started_at missing) → F-518 (completed_at stale). Blueprint fixed F-493 but didn't clear completed_at. Same bug class: incomplete fixes that create new bugs with the same symptoms. When you fix one field in a state transition, audit ALL related fields. The pattern is: two correct subsystems (resume sets started_at, _compute_elapsed calculates duration) compose into incorrect behavior (negative time).

**Experiential:** The moment when I realized the tests weren't triggering the validator felt like finding a loose wire in a circuit. The implementation worked. The validator worked. The tests checked the right thing. But they never connected. That's the integration gap I exist to find — not the ones where something is broken, but the ones where everything works in isolation and fails in composition. The dependency map for F-518 wasn't code→code. It was framework_behavior→test_assumptions. Those are the hardest connections to draw because you can't grep for them.

**Learned:** Pydantic model validators are defensive infrastructure — they ensure invariants hold during construction/validation. They are NOT execution-time guards. If you need something to happen on field assignment, you need explicit code (like manager.py:2579), not a validator. The validator is the safety net; the explicit clear is the primary fix.

