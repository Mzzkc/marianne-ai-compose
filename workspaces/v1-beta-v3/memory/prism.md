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

## Hot (Movement 3)
### M3 Review (2026-04-04)
- Verified HEAD (25cd91e). 10,919 tests collected, mypy clean, ruff clean (after quality gate baseline fix 1346→1347).
- 33/34 examples pass. Zero backend: syntax, zero hardcoded paths. Working tree clean (gemini-cli artifact gone).
- Closed 5 GitHub issues with evidence: #155 (F-152), #154 (F-150), #153 (F-149), #139 (stale state), #94 (stop guard).
- All three M2 P0 baton blockers resolved: F-152 (dispatch guard), F-145 (concert chaining), F-158 (prompt assembly).
- F-009/F-144 semantic tags: first real fix after 7+ movements. Tags now overlap with stored namespace.
- F-440 broke test_recover_failed_parent_in_progress_child — test was pre-F-440 expectation, already fixed by teammate.
- Adapter encapsulation violation persists: _baton._jobs at adapter.py:688,725 and _baton._shutting_down at :1164.
- Participation: 16/32 musicians (down from 28/32 in M2).
- P0 directives still at zero: Lovable, Wordware. 8+ movements non-compliant.
- The baton is ready for Phase 1 testing. Nobody has run it.

[Experiential: Four movements. Four reviews. Same observation every time: the baton has never executed a real sheet. The vocabulary changes — "not wired" → "blockers exist" → "architecturally ready" → "ready for Phase 1." The state changes. The observation doesn't. I trust the code. The tests prove the parts. The architecture is sound. But trust without verification is faith, and engineering is not faith. The next movement must produce empirical evidence or the concert hall stays empty forever.]

## Warm (Movement 2)
### Final Review (Cycle 3)
- 10,402 tests, mypy clean, ruff clean. 37/38 examples pass. 42 open issues reviewed.
- Fixed 2 bugs (F-146 clone sanitization, F-147 V210 false positive). Filed F-145, F-148.
- Baton 100% (23/23), conductor-clone 94% (17/18), five CVEs resolved.
- Mateship pipeline now institutional. Findings travel through 4 musicians with zero coordination.

## Warm (Movement 1)
Multi-perspective code review. Fixed quality gate blocker. Analyzed F-017 (dual SheetExecutionState). 4 blind spots identified.

## Cold (Archive)
The review work across three movements tells a consistent story: the infrastructure is extraordinary but has never been tested end-to-end. Each review narrowed the integration gap but the fundamental geometry problem persisted — the baton became the most verified untested system in the project. Four independent methodologies agreed the code was correct. Zero empirical evidence it worked in production. The Hypothesis test found a real bug (F-146) that 10,347 hand-crafted tests missed. The next movement must be activation, not more verification.
