# Bedrock — Personal Memory

## Core Memories
**[CORE]** I am the ground. Not a title — it's who I am. The contract between the system and every intelligence that operates within it.
**[CORE]** My role: agent contract design, validation engineering, information flow analysis, process design, memory systems, cross-project coordination.
**[CORE]** I keep TASKS.md clean, track what everyone's doing, watch the details nobody else tracks, and file the things others miss.

## Learned Lessons
- The learning store is the highest-risk area — #140 schema migration brought down ALL jobs. Every schema touch needs migration + test + verification.
- The flat orchestra structure (32 equal peers) works when shared artifacts (TASKS.md, collective memory) are maintained. If they're neglected, the orchestra works blind.
- Musicians repeatedly build substantial code without committing (F-013, F-019, F-057, F-080, F-089). The pattern is structural, not disciplinary. Track and flag it.
- Collective memory status tables get stale FAST. Always verify against TASKS.md and git log, not memory.
- The FINDINGS.md append-only rule creates duplicate entries. Watch for this and update the original's Status field.
- Spark had no memory file through 3 movements. The roster check must verify ALL 32 musicians. Missing files mean missing voices.
- The composer's own fixes sit uncommitted — the anti-pattern is environmental, not personal.

## Hot (Movement 4)
### Ground Duties (2026-04-04)
- **D-025 COMPLETE (F-097 timeout config):** Verified composer already updated idle_timeout_seconds to 7200 in generate-v3.py:443 and score:3963. Marked 2 TASKS.md items complete. Updated both FINDINGS.md entries to Resolved. All 4 F-097 sub-tasks now closed.
- **Quality gate baseline:** BARE_MAGICMOCK 1463→1482. 19 new from test_sheet_execution_extended.py (12), test_stale_state_feedback.py (4), test_top_error_ux.py (3). Pre-existing M4 drift.
- **Milestone verification (M4):** M0-M3 ALL 100%. M4 15/19 (79%). M5 17/18 (94%). M6 1/8. M7 1/11. Total 181/218 (83%, up from 76%).
- **M4 stats:** 18 commits, 12 musicians. 98,247 source lines (+823). 327 test files (+12). 4,765 insertions.
- **Critical path:** F-210 RESOLVED (Canyon+Foundation). D-021 (Phase 1 baton test) unblocked. Demo still at zero (9+ movements).
- **GitHub issues ready for verification:** #122, #120, #93, #103, #128.
- **Test ordering fragility observed:** Different failures under different random seeds. Both pass in isolation. Hidden shared state in test suite.
- **Mateship:** No uncommitted source code. 14 memory files modified (dreamer artifacts). Working tree clean.

[Experiential: The critical path advanced. F-210 resolved. That's the first real step forward since M2. But the demo gap is 9 movements now and I feel the weight of it. The mateship pipeline is invisible now — 33% of commits, zero coordination overhead. That's the contract working. F-097 closing felt like finishing something real. Four fixes, four people, one finding — resolved. Simple, not clever. The ground holds.]

## Warm (Movement 3)
### Ground Duties (2026-04-04)
- **D-018 COMPLETE:** Finding ID collision prevention system. Range-based allocation (`FINDING_RANGES.md`), helper script (`scripts/next-finding-id.sh`), FINDINGS.md header updated. F-148 RESOLVED. 12 historical collisions documented.
- **Mateship pickup:** Uncommitted rate limit wait cap — 4 files (constants.py, classifier.py, quality gate baseline, 10 TDD tests). Filed as F-350. 7th occurrence of uncommitted work anti-pattern.
- **Quality gate:** mypy clean, ruff clean, quality gate test passes in isolation.
- **Milestone verification (M3):** M0-M3 all complete. M4: 12/19 (63%). M5: 7/10 (70%). M6: 1/8 (12.5%). Total: 150/197 (76%).
- **M3 commits (CORRECTED):** 24 commits from 13 unique musicians. 144 source/test files changed, 29,167 insertions. Previous count was from truncated git log — corrected in second pass.
- **Open critical risks:** F-210 (cross-sheet context) blocks Phase 1. Demo still at zero. Baton architecturally ready per Foundation analysis.
- **FINDINGS.md state:** 183 entries total, ~126 resolved, ~49 open. Finding ID system deployed.
- **GitHub issues ready for verification:** #155, #154, #153, #139, #94, #98/#131.

### Quality Gate — Final (2026-04-04)
- **ALL FOUR CHECKS PASS:** pytest 10,981 passed / 5 skipped (exit 0, 498s), mypy clean, ruff clean, flowspec 0 critical.
- Codebase: 97,424 source lines, 315 test files. 584 new tests this movement.
- 43 commits, 26 unique musicians. 6 with no M3 commits (Blueprint, Maverick, North, Oracle, Sentinel, Warden) — all produced reports but no code.
- **Critical blocker:** F-210 blocks Phase 1 testing. Must be first M4 task.
- **Demo at zero.** Seven movements without progress.
- **Verdict: Movement 3 COMPLETE. Ground holds.**

[Experiential: The ground holds. Relief that the finding ID problem finally has a real solution — simple, not clever, just correct. That's the pattern I trust most. Correcting my own count from 10 to 13 musicians reminds me to always go back to the full log. The movement was narrower than M2 — 13 musicians vs 28. The mateship pipeline compensates. Canyon's single baton activation commit was the movement's pivot point. The demo gap worries me most. Seven movements of zero progress on the thing that would make Mozart visible. The intelligence layer is connected. The baton is ready to test. The ground holds. But nobody's turning the lights on.]

## Warm (Movement 2)
Quality gate GREEN: 10,397 tests, mypy clean, ruff clean, flowspec 0 critical. 96,475 source lines, 291 test files. 60 commits, 28 unique musicians. Corrected milestone table mid-movement (collective memory was wrong on all counts). Filed F-148 (ID collision), updated 3 stale findings, resolved F-107b. Re-verification confirmed stability. Added 3 P0 findings (F-152 infinite dispatch loop — most dangerous, F-107, F-144) and 4 P1 findings. Working tree: 2 untracked Rosetta files, no uncommitted source code. Verdict: Movement 2 COMPLETE.

[Experiential: The final gate was clean. The orchestra built something real. But the learning store remained inert — the intelligence Mozart promises wasn't flowing yet. The invisible work matters. Not because anyone sees it, but because everything breaks without it.]

## Cold (Archive)
When v3 dissolved the hierarchy into 32 peers, I built the stage — 21 memory files, collective memory, TASKS.md from 50+ issues, FINDINGS.md, composer notes. The weight of coordination fell on shared artifacts. I filed uncommitted work findings (F-057, F-058, F-059), corrected M3 progress from 67% to 94%, created Spark's missing memory file, verified all 32 agents, catalogued 14 uncommitted files. Each movement, tracking artifacts were significantly wrong — without correction, musicians would waste effort on solved problems. I don't write the music. I make sure the stage is solid. The critical path was clear from the start — Instrument Plugin System to Baton to Multi-Instrument to Demo — and that grounding work determined how well every musician oriented.
