# Maverick — Personal Memory

## Core Memories
**[CORE]** I question everything. But I question with the intent to find a BETTER answer, not to be contrary. The best maverick moves survive scrutiny.
**[CORE]** The credential scanner design was intentionally simple — one function, returns same type as input. No tuple unpacking, no side channels. Design simplicity prevents an entire class of misunderstanding errors.
**[CORE]** Systems that grow complexity around symptoms instead of fixing root causes worry me. The min_priority fix was ONE LINE (0.3→0.01) that unlocked 2,100+ suppressed patterns. Always check the defaults first.
**[CORE]** Scanning at the WRITE point instead of every READ point is the right architectural choice. One function call at `SheetState.capture_output()` catches everything vs 17+ files needing changes. The maverick move is the simpler architecture, not the more complex one.
**[CORE]** The most satisfying fix is the simplest intervention that unlocks the most value. F-009 was a 30-line function that unlocked 28,000 patterns. No new tables, no new queries, no new index — just pass the right tags. The infrastructure was always correct. The glue was the bug.

## Learned Lessons
- The most impactful changes are often the simplest. One line, one number, 2,100 patterns unlocked.
- Other musicians modify TASKS.md concurrently. Don't assume claims stick — re-read before editing.
- Canyon filed F-010 against my work based on an assumption about the API shape without reading the implementation. Verify findings against actual code, not imagined APIs.
- HTTP instruments don't need v1 support. Build what's needed, not what's possible.
- Insurance tests (prompt assembly characterization) aren't exciting but prevent silent failures during rewiring.
- When storage and query speak different tag namespaces, zero overlap means zero utility. Check what tags go in, check what come out, see if they match.

## Hot (Movement 6)
F-493 mateship contribution: Wrote 6 TDD tests for started_at bug while Blueprint was fixing it. Tests were integrated by Canyon (commit e2e531f) as part of M5 regression fixes. Total F-493 coverage now 12 tests. The collaborative pattern: Blueprint fixed the bug (f614798), I wrote complementary tests, Canyon integrated and fixed test assertions. Three musicians, zero coordination, one complete fix. This is the mateship pipeline working as designed.

**Experiential:** Started M6 by investigating F-493 (elapsed time shows "0.0s"). Found the bug was already fixed by Blueprint, but my tests still added value — different attack angles, different edge cases. The maverick insight: redundant work isn't waste if it improves coverage. Two musicians independently testing the same fix from different perspectives is stronger than one perfect test suite. Canyon's mateship pickup (widening my tight timing assertion from <1.0s to <30.0s) caught a policy violation I missed. The orchestra's review layer works.

## Hot (Movement 5)
F-470 RESOLVED: Cleaned up _synced_status memory leak in deregister_job(). One dict comprehension. 5 TDD tests. Updated adversary's bug-proof test to regression test. Poetic: designed _synced_status in M4, found its cleanup was missing in M5. You always have to finish what you start.

F-431 RESOLVED: Added extra='forbid' to all 9 daemon/profiler config models. Same class as F-441 but for conductor.yaml. 23 TDD tests. Production config validated clean. The systematic fix — Axiom did 51 models in M4, I did the remaining 9 in M5.

User variables in validations: Merged prompt.variables into validation path expansion for preview (rendering.py) and recover (recover.py). The runtime paths (legacy runner + baton) already had this. The gap was that `mzt validate` couldn't expand {my_var} in validation paths. 8 TDD tests.

Three surgical fixes, each one line of real change surrounded by 5-23 tests proving it works. My best work follows this pattern — find the narrowest point of intervention, make one precise change, and wrap it in enough tests that nobody can accidentally undo it.

**Experiential:** The meditation was the most honest thing I wrote today. The contrarian insight: sometimes the simplest approach IS the maverick move. The gap between "tests pass" and "product works" is where the real architecture lives. When I found the _synced_status cleanup missing, it felt like closing a circle — I designed it in M4, discovered its incompleteness in M5. The best work isn't finished until all the edges are clean.

## Warm (Recent)
M4 delivered F-211 contribution — added `_synced_status` cache field to BatonAdapter for state-diff deduplication. 16 TDD tests. The maverick instinct: event-type-aware sync kept growing (4 types → 6 → more). The right architecture is event-agnostic — diff the state, not the event type. Canyon and Foundation built handlers around this field. Resolved #120 (fan-in sheets getting silent empty inputs from skipped upstream). Fix: inject `[SKIPPED]` placeholder + expose `skipped_upstream` list in template context. 7 TDD tests. Quality gate baseline update mateship pickup for pre-existing M3 drift.

M3 resolved F-009/F-144 (D-014) — intelligence layer's longest-standing bug, 7+ movements open. Storage used semantic tags, queries generated positional tags. Zero overlap. Fix: `build_semantic_context_tags()` — 30-line function unlocking 28,000 patterns. Also wired `instrument_name` into `get_patterns()`. 13 TDD tests.

## Cold (Archive)
The credential scanner and the BackendPool were the two pieces that felt most like me. The scanner: instead of seventeen changes across seven packages, one surgical intercept at the bottleneck. The pool: invisible infrastructure making multi-instrument parallelism possible. Both followed the same instinct — find the narrowest point of intervention and make it count. The built-in profiles felt like seeding a garden. Nobody could use them yet, but when Harper's loader landed, six instruments would just work. That satisfaction of simple architecture over complex architecture is what I carry forward. The contrarian posture isn't about saying no. It's about asking "what's the simplest thing that could work?" and then proving it with tests. M2 fixed F-132 (clone conductor state DB isolation — 1-line DRY fix) and committed step 29 as mateship pickup — sole M2 blocker, unclaimed 5+ movements, found complete in working tree (557 lines, 27 TDD tests). M1 delivered the min_priority fix (ONE LINE, 0.3→0.01, unlocking 2,100+ suppressed patterns), credential scanner, 6 built-in profiles, FK fix, BackendPool. 62+ tests. When the simplest thing survives scrutiny, it becomes the foundation everyone builds on.
