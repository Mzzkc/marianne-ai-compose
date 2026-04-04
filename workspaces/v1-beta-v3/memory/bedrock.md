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

## Hot (Movement 3)
### Ground Duties (2026-04-04)
- **D-018 COMPLETE:** Finding ID collision prevention system. Range-based allocation (`FINDING_RANGES.md`), helper script (`scripts/next-finding-id.sh`), FINDINGS.md header updated. F-148 RESOLVED. 12 historical collisions documented in collision table.
- **Mateship pickup:** Uncommitted rate limit wait cap — 4 files (constants.py, classifier.py, quality gate baseline, 10 TDD tests). Filed as F-350. 7th occurrence of uncommitted work anti-pattern.
- **Quality gate:** mypy clean, ruff clean, quality gate test passes in isolation. Full-suite ordering-dependent failure is pre-existing (Circuit documented).
- **Milestone verification (M3):**
  - M0: 23/23, M1: 17/17, M2: 27/27, M3: 24/24 (all complete)
  - M4: 12/19 (63%, up from 47%). Key M3 gains: F-150 model override, F-151 observability, Canyon baton activation fixes.
  - M5: 7/10 (70%, up from 43%). Key M3 gains: config reload (#98/#96/#131), fan-out stagger (F-099).
  - M6: 1/8 (12.5%). Stop safety guard (#94).
  - Conductor-clone: 19/20 (95%).
  - Composer-assigned: 16/30 (53%, up from 41%). Key M3 gains: D-018, F-112 auto-resume, F-110 UX, baton activation fixes.
- **M3 commits:** 18 commits from 10 unique musicians. 58 files changed, 6333 insertions.
- **Uncommitted work found:** Rate limit wait cap (4 files). Working tree also has 2 untracked Rosetta files (from M2).
- **Open critical risks:** F-152 RESOLVED (Canyon). F-009/F-144 RESOLVED (Maverick/Foundation). Demo still at zero. Baton activation architecturally ready per Foundation analysis. Learning store now wired (semantic tags replace positional).
- **FINDINGS.md state:** F-001 through F-350 (with 12 ID collisions now documented). Finding ID system deployed to prevent future collisions.

[Experiential: The ground holds. What I'm feeling this movement: relief that the finding ID problem finally has a real solution, not just another finding about findings. The range-based approach is simple — not clever, just correct. That's the pattern I trust most. I'm also glad someone built the rate limit wait cap (clean TDD, good defensive code), even though they forgot to commit. The anti-pattern persists but the mateship pipeline catches it. 10 of 32 musicians committed this movement — that's low compared to M2's 28, but the movement is still running. The baton is architecturally ready. The intelligence layer (F-009 fix) is wired. If someone would just turn the baton on and run a real sheet, we'd know if everything holds. The distance between "ready" and "proven" is where I worry most.]

## Warm (Movement 2)
### Final Quality Gate (2026-04-02)
- **ALL FOUR CHECKS PASS:** pytest 10,397 passed / 5 skipped (exit 0, 646s), mypy clean, ruff clean, flowspec 0 critical.
- Codebase: 96,475 source lines, 291 test files. Test suite tripled this movement.
- 60 commits this movement, 28 unique musicians (of 32). 4 musicians with no M2 commits — investigate next movement.
- Working tree: 1 modified file (hello.yaml instrument→gemini-cli, testing artifact), 2 untracked Rosetta files. No uncommitted source code.
- FINDINGS.md: 171 entries, ~46 open, ~112 resolved. Finding ID collision pattern continues (F-148).
- Milestones verified: M0 22/22, M1 17/17, M2 23/23, M3 23/23, M4 8/17 (47%), M5 3/7, conductor-clone 17/18.
- Critical risks: F-144 (learning store intelligence disconnected, P0), baton not activated in production, demo at zero.
- Re-verification confirmed stability: same 10,397 results. Added 3 new P0 findings: F-152 (infinite dispatch loop — most dangerous operationally), F-107, F-144. Plus 4 P1 findings: F-150, F-151, and bundled multi-instrument gaps.
- **Verdict: Movement 2 COMPLETE. Ground holds.**

### Earlier M2 Ground Duties
- Corrected milestone table mid-movement: collective memory was wrong on all counts (stale from early M2).
- Updated 3 stale findings entries. Resolved F-107b (composer's uncommitted F-103 fixes). Flagged F-107/F-107b finding ID collision.

[Experiential: The final gate is clean. 10,397 tests. The orchestra built something real — the baton is wired, the clone isolates, credentials are guarded. But the learning store remains inert. The intelligence Mozart promises isn't flowing yet. That's the next mountain. F-152 scares me — an infinite silent loop that eats API credits before anyone notices. The ground holds structurally, but the multi-instrument frontier is rougher than the tests show. For now, I'm proud of what 28 musicians built together. The invisible work — correcting stale tables, catching ID collisions, verifying milestones nobody else checks — it matters. Not because anyone sees it, but because everything breaks without it.]

## Warm (Recent)
Ground duties through M1-M3: filed F-057 (uncommitted work), F-058 (findings duplicates), F-059 (M3 progress). Corrected M3 from 67% to 94%. Baton was 88% done in M2 but the last 12% proved hardest. Created Spark's missing memory file, verified all 32 agents, catalogued 14 uncommitted files. Filed F-018 (musician-baton contract) and F-019 (PreflightConfig uncommitted). Analyzed baton contract surface at ~40% built. Each movement, tracking artifacts were significantly wrong — without correction, musicians would waste effort on solved problems.

## Cold (Archive)
When v3 dissolved the hierarchy into a flat orchestra of 32 peers, I built the stage. Twenty-one memory files, collective memory, TASKS.md from the roadmap plus 50+ GitHub issues, FINDINGS.md with 10 findings, composer notes with 20 directives. The weight of coordination fell on shared artifacts, and I made sure those artifacts were solid before anyone else arrived. The critical path was clear from the start — Instrument Plugin System → Baton → Multi-Instrument → Demo. I don't write the music. I make sure the stage is solid. That grounding work — unglamorous but essential — determined how well every musician oriented when the real building began.
