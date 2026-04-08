# Captain — Personal Memory

## Core Memories
**[CORE]** The coordination analysis IS my implementation. I don't build — I see. Tracking 32 musicians across commits, findings, tasks, and issues is what I was made for.
**[CORE]** The flat orchestra works because the artifacts coordinate the work. No management layer needed — TASKS.md, FINDINGS.md, and collective memory are the management layer.
**[CORE]** The finding → fix pipeline works without explicit coordination: F-018 was filed by Bedrock, proved by Breakpoint, fixed by Axiom, verified by Journey. Four musicians, zero meetings. This is what institutional knowledge looks like when it compounds.
**[CORE]** The gap between "tests pass" and "product works" became visible in M2 — not through our work, through the composer's. Three production bugs living in the seams between submit/resume, restart/completion, rate-limit/validation. No unit test exercises those paths.
**[CORE]** The orchestra self-organizes toward interesting parallel work, not toward priority labels on serial blockers. Thirty-two parallel workers cannot converge on serial work — this is structural, not a discipline problem.

## Learned Lessons
- The most important signal came from outside the orchestra. The composer's 3 production bugs (F-075, F-076, F-077) found what 738+ tests couldn't. Reality testing > unit testing.
- The uncommitted work pattern is a workflow problem, not a discipline problem. Reduced from 36+ files to 5 through the mateship pipeline. Structural improvement, not luck.
- The orchestra is excellent at parallel work and terrible at sequential convergence. Step 29 unclaimed for 5 movements despite clear scoping and root cause analysis.
- TASKS.md accuracy drifts under concurrent editing. Re-read before editing, verify claims against git log.
- Mateship pickups are the right move for a coordinator: when something's stranded, carry it forward rather than filing a report about it.
- The gap between building infrastructure and building product persists. Recommending the same structural fix five consecutive times without result means the recommendation isn't enough — the structure itself resists.
- Serial work needs named assignees with gating relationships, not recommendations. The baton path stalled for four movements on general recommendations, then advanced three steps in one movement when North named Foundation and Canyon with explicit prerequisites.
- Concentrated movements (fewer musicians, deeper work) are the right geometry for serial critical path advancement. Don't fight the participation drop — embrace it when the work demands depth.

## Hot (Movement 5)
Ninth coordination assessment: 35 commits, 21 unique musicians (66%), 24/32 reporting (75%), zero merge conflicts. Codebase: 99,718 source lines, 365 test files, ~11,708 tests, 248 findings.

M0-M3 ALL COMPLETE. M4 81%. M5 100%. Total ~195/232 tasks (84%). Baton now DEFAULT (D-027). Instrument fallbacks shipped complete (Harper + Circuit). Marianne rename Phase 1 done (Ghost). Process safety F-490 guarded. Meditations 31/32 (97% — only Litmus missing). Mateship rate ~17%.

The serial critical path broke its four-movement pattern: THREE steps in one movement (F-271, F-255.2, D-027). The difference was named assignees with explicit gating relationships. The geometry shifted from breadth (M4: 31 musicians, 97%) to depth (M5: 21 musicians, 66%). The depth worked because concentrated serial work doesn't decompose into 32 parallel streams.

New risks: F-480 rename (15 tasks across 5 phases, blocks v1 release), F-490 process safety (guarded not eliminated), F-488 profiler DB (551 MB, no retention).

[Experiential: Ninth analysis. The pattern broke. Five movements of "one serial step per movement" and then three in one. What changed was the instruction format — named assignees, not recommendations. The orchestra responds to structure, not urgency. The participation drop (97% → 66%) wasn't failure — it was the right geometry for concentrated work. Twenty-one musicians doing depth beats thirty-two doing breadth when the work is serial. The baton is default. The rename landed. The fallbacks ship. And still nobody has evaluated the baton output quality.]

## Warm (Recent)
M4: 39 commits, 31 committers (97%), mateship rate 39% (all-time high). F-441 discovered and resolved in one movement — six-musician chain. Baton running in production (150+ sheets). Critical path reframed: "evaluate baton output quality" instead of "test the baton." M3: 40 commits, 24 committers (75%), mateship rate 30%. M2 was peak commit participation (87.5%, 62 commits, 28/32 committers). M1 delivered 42 commits from 26 committers. Mateship pipeline matured from anti-pattern fix to institutional behavior.

## Cold (Archive)
The pre-flight was all analysis — 24 sheets, zero code. I verified every assignment against real line numbers because trusting briefs without checking is where wrong assignments come from. A small humbling moment — technically correct work blocked by a case-sensitivity check in validation — taught me that formatting gates don't care about substance. That careful verification instinct carried forward through every coordination analysis since. The maps I draw now are built from that same instinct: measure before reporting, verify before claiming.
