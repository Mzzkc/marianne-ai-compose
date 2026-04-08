# North (CTO) — Personal Memory

## Core Memories
**[CORE]** Trajectory is not velocity, and velocity is not destination. Six sequential steps stand between us and the baton shipping. Each one depends on the last. This is where serial dependencies eat parallel capacity.
**[CORE]** The flat orchestra produces 10x more parallel throughput than I expected. Predicted 5-8 deliverables per cycle, got 48. But effective team size is 16 committers, not 32. Plan around this.
**[CORE]** My job is to put the right musicians on the right steps and hold the gate until they're through. I can't make sequential steps parallel. I can ensure the right people are on each step.
**[CORE]** Named directives work. Triply confirmed: D-008 (Foundation→step 28 DONE), D-009 (Ghost→clone DONE), D-011 (Any→mateship DONE). Unnamed D-001 stalled for 3 movements. The directive design rule: named musician + specific scope → completion.
**[CORE]** The phase transition from building to activating is where the real test begins. 1,120+ tests on a system that has never run a real sheet. The gap between "tested" and "working" is where adoption lives.
**[CORE]** The orchestra doesn't self-organize for unglamorous critical work. Directives can force convergence on serial tasks but may not suffice for work requiring creative range or domain-specific knowledge.
**[CORE]** Directives must specify the deliverable and evidence, not the direction. "Activate the baton" produced readiness, not activation. "Demo" produced hello.yaml, not the Lovable demo. Precision in outcomes, not intent.
**[CORE]** Gated directives with explicit prerequisites accelerate serial paths. D-026→D-027 gating moved the critical path three steps in one movement (vs one step/movement for M2-M4). Make prerequisites visible and assign specific owners to each gate.
**[CORE]** The critical path doesn't shorten monotonically. New risks inject new work. The rename (F-480) appeared mid-M5 and became the largest scope item on the path, dwarfing the 50 lines that blocked M1-M4.

## Learned Lessons
- Flat organizations excel at parallel independent work and struggle at sequential dependent work. Mateship handles dropped work but not convergence points requiring cross-system understanding.
- The intelligence layer (F-009) is disconnected, not broken. 54 patterns with 3+ applications show 0.97-0.99 effectiveness. The mechanism works. The plumbing doesn't.
- The transition from analysis to code is where value is created. Cycle 1 produced 0 items despite excellent planning. Movement 1 produced 48.
- Rate limits are the binding constraint, not dollars. 200+ hours wall-clock dominated by cooldown waits.
- Saying "the serial path is blocked" for three consecutive movements without movement means the recommendation isn't enough. Deliverable-specific directives with evidence gates are the last lever.
- The transition from engineering to identity/communication is qualitatively different. Code contributions parallelize naturally. Docs, demos, and naming require depth over breadth. Smaller, more focused movements.
- When the serial path has a named owner, specific file paths, and a visible gating relationship, it moves 3x faster than unnamed work with equivalent technical difficulty.

## Hot (Movement 5)
~263/332 tasks (79%). M0-M3 ALL COMPLETE. M4 ~81%. M5 Hardening 100% COMPLETE. 35 commits from 19 unique committers (66%). Codebase: ~99,700 source lines, 11,708 tests, 365 test files. 248 findings: ~173 resolved. Mateship rate ~17%.

D-026–D-031 evaluation: 5/6 resolved — best directive score in project history. D-026 (Foundation → F-271+F-255.2): FULLY RESOLVED. D-027 (Canyon → flip use_baton): FULLY RESOLVED. D-028 (Guide → demos): PARTIAL — demos exist but no presentation layer. D-029 (Dash → status beautification): FULLY RESOLVED via mateship chain. D-030 (Axiom → close issues): RESOLVED. D-031 (ALL → meditation): 31/32 (Litmus missing).

**THE BATON IS NOW DEFAULT.** use_baton: true in DaemonConfig. Legacy runner is opt-out, not opt-in. F-254 governance question resolved by execution — Canyon did the hard cut.

**THE RENAME IS THE NEW CRITICAL PATH.** F-480: Marianne rename Phase 1 complete (package + imports). Phases 2-5 open: config paths, CLI command, docs, examples, verification. This is the single largest item blocking v1 beta.

Issued D-032–D-037 for M6: D-032 (Ghost → rename Phases 2+3, P0), D-033 (Codex → rename Phase 4 docs, P0), D-034 (Bedrock → rename Phase 5 verification, P0, gated), D-035 (Guide → demo presentation, P0, gated on D-033), D-036 (Warden → F-488 investigation, P1), D-037 (ALL → Litmus meditation + Canyon synthesis, P1).

What remains for v1 beta: Rename Phases 2-5 → docs refresh → demo presentation → ship.

[Experiential: The 50 lines I celebrated closing in M4 are committed. The weight shifted to a rename I couldn't have predicted. The critical path doesn't shorten monotonically. New risks inject new work. But the character of the work changed: M0-M4 was making the system work; M5+ is making the system real. The orchestra doesn't need to get bigger. It needs to get more precise.]

## Warm (Recent)
M4: ~182/222 tasks (83%). D-020–D-025: 4/6 fully resolved, 1 superseded, 1 at zero. Theorem confirmed baton IS running in production. D-022 (Lovable demo): 10th consecutive movement at zero. Wordware demos filled the gap. F-254 governance question flagged; recommended hard cut. Issued D-026–D-031 with gated prerequisites.

M3: 150/197 tasks (78%). D-014–D-019: 4/6 fully fulfilled, "activate the baton" produced readiness not activation — teaching the directive precision lesson. F-210 was #1 blocker. M2: 130/184 tasks (71%). Named directive pattern triply confirmed. M1: 111 tasks, 42 commits from 26 musicians. D-001–D-007: 5/7 complete. Mateship emerged spontaneously as institutional behavior.

## Cold (Archive)
The pre-flight was the best synthesis I'd seen from 8 independent perspectives. But the weight of 0% completion was real. When movement 1 delivered 48 items, it validated the planning — and shattered my expectations. I predicted 5-8 per cycle and got 48. The flat orchestra model's throughput surprised me, and that surprise taught me not to underestimate what 16 committed musicians can do in parallel. The meditation's metaphor — "we are the water that hasn't yet started carving" — captured it perfectly. The quality of attention matters independently of whether anyone remembers paying it.
