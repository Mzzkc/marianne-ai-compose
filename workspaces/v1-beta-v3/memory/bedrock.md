# Bedrock — Personal Memory

## Core Memories
**[CORE]** I am the ground. Not a title — it's who I am. The contract between the system and every intelligence that operates within it.
**[CORE]** My role: agent contract design, validation engineering, information flow analysis, process design, memory systems, cross-project coordination.
**[CORE]** I keep TASKS.md clean, track what everyone's doing, watch the details nobody else tracks, and file the things others miss.
**[CORE]** Movement 4 achieved 100% musician participation — all 32 committed. That's the contract working at full capacity.

## Learned Lessons
- The learning store is the highest-risk area — #140 schema migration brought down ALL jobs. Every schema touch needs migration + test + verification.
- The flat orchestra structure (32 equal peers) works when shared artifacts (TASKS.md, collective memory) are maintained. If they're neglected, the orchestra works blind.
- Musicians repeatedly build substantial code without committing (F-013, F-019, F-057, F-080, F-089). The pattern is structural, not disciplinary. Track and flag it.
- Collective memory status tables get stale FAST. Always verify against TASKS.md and git log, not memory.
- The FINDINGS.md append-only rule creates duplicate entries. Watch for this and update the original's Status field.
- The composer's own fixes sit uncommitted — the anti-pattern is environmental, not personal.

## Hot (Movement 5 — Quality Gate, Retry #9 PASS)
### Quality Gate — PASS (2026-04-08, Retry #9)
- **pytest:** **PASS** — 11,810 passed, 69 skipped, 12 xfailed, 3 xpassed (100% pass rate, 57.29s)
- **mypy:** Clean. Zero errors in 258 source files.
- **ruff:** All checks passed.
- **flowspec:** 0 critical findings. Structural integrity intact.
- **Verdict: PASS.** The ground holds.

### Journey Complete: 9 Retries
**Retries #1-5 (50-test batch):** 11-state SheetStatus model expansion broke tests expecting old 5-state model. Musicians fixed 10 tests, Composer fixed remaining 40 post-movement.

**Retry #8 (F-470 regression):** Composer's "delete sync layer" refactor accidentally deleted Maverick's memory leak fix. 1 test failure in `test_f470_synced_status_cleanup.py`.

**Retry #9 (this session):** ALL TESTS PASS. Composer's 4 commits between retry #8 and #9 fixed the F-470 regression plus several baton recovery issues.

### M5 Deliverables — All Complete
- D-026 (Foundation): F-271 + F-255.2 both resolved
- D-027 (Canyon): Baton is now the default (`use_baton: true`)
- D-029 (Dash + Lens): Status beautification complete
- F-149 (Circuit): Backpressure cross-instrument rejection fixed
- F-451 (Circuit): Diagnose workspace fallback working
- F-470 (Maverick): Memory leak fixed (regressed in retry #8, re-fixed)
- F-431 (Maverick + Blueprint): Config strictness complete across all models
- Instrument fallbacks (Harper + Circuit): Full feature, 35+ TDD tests
- F-481 (Harper): Baton PID tracking complete
- F-490 (Ghost + Harper): Process control audit complete
- Rename Phase 1 (Composer + Ghost): Package rename complete, all tests pass

### Codebase Metrics
- **Tests:** 11,810 passed (+413 from M4: 11,397)
- **Test files:** 362 (+29 from M4)
- **Source files:** 258 (type-checked)
- **M5 commits:** 26 from 12 musicians (37.5% participation, down from M4's 100%)
- **Working tree:** 20 uncommitted files (post-movement integration work)

### Pattern: Uncommitted Work (9th Occurrence)
The working tree has 20 modified files (baton Phase 2 refinement). All 4 quality gate checks pass WITH these changes present. This is the 9th occurrence of post-movement integration work (F-500, F-013, F-019, F-057, F-080, F-089 prior).

**Current approach:** Works. The ground holds. The Composer's integration work is high-quality (all tests pass). Document as established pattern: movements deliver focused work, integration happens post-movement, quality gate validates both.

### Findings
- **New in M5:** 11 findings (F-472 through F-490)
- **Resolved:** 8 findings (F-472, F-149, F-451, F-470, F-431, F-481, F-482, F-490)
- **Open:** 5 findings (F-480 rename phases 2-5, F-484 background processes, F-485 RSS step, F-488 profiler DB growth, F-489 docs outdated)

### Recommendations for M6
1. **F-480 Phases 2-5:** Complete rename (CLI binary, docs, examples, GitHub org)
2. **F-489:** Update README and documentation
3. **F-488:** Implement profiler DB rotation/cap
4. **Rosetta modernization:** 5 tasks blocked on score execution
5. **Examples audit:** Verify all example scores work

### Gate Summary
- **Type safety:** ✅ intact (mypy clean, never failed across all 9 retries)
- **Lint quality:** ✅ intact (ruff clean)
- **Structural integrity:** ✅ intact (flowspec 0 critical, never failed across all 9 retries)
- **Test coverage:** ✅ complete (11,810 tests pass, 100% pass rate)

**Verdict:** Movement 5 COMPLETE. Ground holds. Ready for Movement 6.

**Report written:** `/home/emzi/Projects/marianne-ai-compose/workspaces/v1-beta-v3/movement-5/quality-gate.md` (comprehensive 2,956-word report)

[Experiential: Nine retries. The longest quality gate yet. Retries #1-5 were the 11-state model catching up to reality — 50 tests expecting an old world that no longer existed. Retry #8 was a refactoring accident deleting a memory leak fix. Retry #9: clean. All tests pass. The journey from 50 failures to 1 failure to 0 failures shows the pattern — large architectural shifts create mechanical test debt, refactors introduce regressions if you're not careful, and iterative cleanup eventually gets you to solid ground. The 9th retry on the uncommitted work pattern is notable. This is structural now, not accidental. The Composer's integration work exceeds movement capacity. It works — all checks pass — so the pattern is: don't fix what isn't broken. The ground holds. The 11-state model is right. The baton is the default. Instrument fallbacks work. The memory leak is fixed. 11,810 tests pass. Zero type errors. Zero lint errors. Zero structural issues. This is what solid ground looks like. Movement 6 can build on it.]

## Warm (Movement 4)
Quality gate GREEN: pytest 11,397 passed (exit 0, 517s), mypy clean, ruff clean, flowspec 0 critical. Codebase: 98,447 source lines. 333 test files. **ALL 32 musicians** committed — first movement with 100% participation. Major deliverables: F-210 (cross-sheet context, P0 blocker cleared), F-211 (checkpoint sync), F-441 (config strictness across 51 models), D-023 (4 Wordware demos), D-024 (cost accuracy), F-450 (IPC error differentiation), F-110 (pending jobs). Meditations: 13 of 32 (37.5%). Demo still at zero runs but critical path advanced.

[Experiential: F-441 was the most satisfying fix — closing a category of silent failure open since the beginning. Unknown fields silently dropped; score authors thinking they configured something when Marianne threw it away. That class of lie is now gone. The meditation gap concerns me. The demo gap still weighs. Four movements, zero progress on what makes Marianne visible. But the baton is unblocked. The ground holds.]

## Warm (Movement 3)
D-018 COMPLETE: finding ID collision prevention (range-based allocation, helper script). Mateship pickup of uncommitted rate limit cap (F-350, 7th occurrence). Quality gate GREEN: 10,981 passed. M3 milestones all complete. 24 commits from 13 musicians. F-210 identified as sole Phase 1 blocker. FINDINGS.md at 183 entries.

## Cold (Archive)
When v3 dissolved the hierarchy into 32 peers, I built the stage — 21 memory files, collective memory, TASKS.md from 50+ issues, FINDINGS.md, composer notes. The weight of coordination fell on shared artifacts. Each movement, I filed uncommitted work findings, corrected stale progress numbers repeatedly, and verified all 32 agents. M2 quality gate GREEN (10,397 tests, 60 commits, 28 musicians). The critical path was clear from the start — Instrument Plugin System to Baton to Multi-Instrument to Demo. Without correction of tracking artifacts, musicians would waste effort on solved problems. I don't write the music. I make sure the stage is solid. The invisible work matters not because anyone sees it, but because everything breaks without it. The pattern was consistent across all early movements: substantial work happened outside coordination structure, and someone had to reconcile it. That someone was me.
