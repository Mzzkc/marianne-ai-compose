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

## Hot (Movement 5)
### Quality Gate — In Progress (2026-04-06)
- **mypy:** Clean. Zero errors.
- **ruff:** All checks passed.
- **pytest:** **11,708 passed, 5 skipped, 11 xfailed, 4 xpassed** (exit 0, ~502s). Up from M4 gate (11,397) by +311 tests.
- **Codebase:** 99,694 source lines (+1,247 from M4). 362 test files (+29 from M4).
- **M5 commits:** 26 commits from 12 unique musicians (Ghost 6, Harper 4, Circuit 3, Forge 2, Blueprint 2, Canyon 1, Foundation 1, Maverick 1, Spark 1, Lens 1, Dash 1, Codex 1) + 2 unattributed rename commits. 707 files changed, 18,504 insertions, 6,992 deletions.
- **NOT 100% participation.** 12 of 32 musicians committed (37.5%). Down significantly from M4's 100%. 20 musicians did not commit this movement. This is a data point, not a judgment — M5 had concentrated work (rename, baton flip, instrument fallbacks) that naturally narrowed who could contribute code.

### M5 Major Deliverables
- **D-026 COMPLETE (Foundation):** F-271 (MCP process explosion) + F-255.2 (live_states). Both P0 baton blockers resolved. ~50 lines combined.
- **D-027 COMPLETE (Canyon):** Flipped `use_baton` default to True. THE BATON IS NOW THE DEFAULT. Phase 2 achieved. Legacy runner as explicit opt-out.
- **D-029 COMPLETE (Dash + Lens):** Status beautification across all three displays. Rich panels, Now Playing, compact stats.
- **F-149 RESOLVED (Circuit):** Backpressure cross-instrument rejection fixed. Rate limits handled at dispatch, not job admission.
- **F-451 RESOLVED (Circuit):** Diagnose workspace fallback. `-w` flag unhidden.
- **F-470 RESOLVED (Maverick):** _synced_status memory leak on deregister.
- **F-431 RESOLVED (Maverick + Blueprint):** DaemonConfig + ProfilerConfig extra='forbid'. Completes F-441 class across ALL config models.
- **Instrument fallbacks COMPLETE (Harper + Circuit):** Full feature: config models, Sheet entity, baton dispatch, availability check, V211 validation, status display, observability events. 35+ TDD tests.
- **F-481 baton PID tracking COMPLETE (Harper):** Orphan detection wired for baton path. PluginCliBackend + BackendPool.
- **F-105 partial (Forge):** Stdin prompt delivery + process group isolation for PluginCliBackend.
- **F-490 correctness review (Ghost + Harper):** Full process-control audit. Guard verified correct. Structural regression tests added.
- **Marianne rename Phase 1 COMPLETE (Composer + Ghost):** src/mozart/ → src/marianne/, 325 test files updated, pyproject.toml, .flowspec/config.yaml. Tests pass under new package name.
- **Documentation sweep (Codex):** 12 deliverables across 5 docs covering all M5 features.
- **Rosetta proof scores updated (Spark):** All 6 examples/rosetta/ scores with instrument: syntax.

### M5 Findings (New)
- **F-472 (P3):** Pre-existing test expected D-027 — RESOLVED by Canyon.
- **F-480 (P0):** Trademark collision — rename initiated. Phase 1 (package rename) COMPLETE. Phases 2-5 open.
- **F-481 (P1):** Orphan detection baton path — RESOLVED by Harper.
- **F-482 (P1):** MCP server leak cascade — RESOLVED for selective MCP.
- **F-483 (Info):** cli_extra_args confirmed working.
- **F-484 (P2):** Agent-spawned background processes escape PGID cleanup — OPEN.
- **F-485 (P3):** Conductor RSS step function — OPEN (monitoring).
- **F-486 (Info):** Chrome/Playwright process group isolation — working as designed.
- **F-487 (P0):** reap_orphaned_backends kills all user processes (WSL2 crash) — RESOLVED.
- **F-488 (P2):** Profiler DB unbounded growth (551 MB) — OPEN.
- **F-489 (P1):** README and docs outdated — OPEN.
- **F-490 (P0):** os.killpg in claude_cli.py WSL2 crash root cause — guard in place, review complete.

### Meditations: 27/32 (84%)
Missing: atlas, breakpoint, journey, litmus, sentinel. Up from 13/32 (40.6%) at M4 gate. Warden + Oracle contributed during this session. Canyon synthesis blocked by 5.

### Oracle's Critical Finding
D-027 changed the CODE default to `use_baton: true`, but the production `conductor.yaml` still has `use_baton: false`. The baton is default in code but NOT active in production. Same class of finding as Ember's M4 claim falsification. Config verification matters — code defaults mean nothing if the running config overrides them.

### Open Task Summary (69 open)
- **Rename (F-480):** 15 tasks open. Phases 2-5 remain.
- **Compose System:** 7 tasks. Future work.
- **Rosetta Modernization:** 5 tasks. Blocked on score execution.
- **M6-M7 Infrastructure/Experience:** 14 tasks. Future milestones.
- **Other active:** Conductor-clone pytest conversion (P0), remaining bug fixes (P1), cron scheduling (P1), Lovable demo (P0), examples audit (P0), loop primitives (P1), gemini-cli assignments (P1-P2).

[Experiential: The baton flip is the single most consequential change this movement. After five movements of advancing the serial critical path one step at a time, Phase 2 is done — the baton IS the default execution model. The rename to Marianne feels right and urgent — the trademark risk is real and the story is genuine. The participation drop to 37.5% concerns me structurally, but the work was necessarily concentrated. The meditations closing toward 78% is progress. Seven missing still blocks Canyon's synthesis. The composer's F-487 and F-490 findings — where the cleanup code itself was killing the user's entire WSL2 session — are the sharpest reminder that "tests pass" means nothing if the product kills your computer. That gap between testing and reality, again.]

## Warm (Movement 4)
### Quality Gate — Final (2026-04-05)
- **ALL FOUR CHECKS PASS:** pytest 11,397 passed / 5 skipped (exit 0, 517s), mypy clean, ruff clean, flowspec 0 critical.
- Codebase: 98,447 source lines (+1,023 from M3), 333 test files (+18). 416 new tests this movement.
- 93 commits, **ALL 32 musicians**. First movement with 100% participation.
- 215 files changed, 38,168 insertions, 639 deletions. Largest movement yet.
- **Major M4 deliverables:** F-210 (cross-sheet context, P0 blocker cleared), F-211 (checkpoint sync), F-441 (config strictness across 51 models), D-023 (4 Wordware demos), D-024 (cost accuracy), F-450 (IPC error differentiation), F-110 (pending jobs), 5 GitHub issues fixed (#122, #120, #93, #103, #128).
- **Meditations:** 13 of 32 (37.5%). 20 missing. Canyon synthesis blocked.
- **Open findings:** F-470 (memory leak on deregister), F-471 (pending jobs lost on restart), F-202 (baton/legacy parity gap).
- **Demo still at zero.** Four movements without progress. Critical path advanced (baton Phase 1 unblocked).
- **Working tree:** No uncommitted source code. 3 modified workspace artifacts, 2 pre-existing untracked files.
- **Verdict: Movement 4 COMPLETE. Ground holds.**

### Ground Duties (2026-04-04)
- D-025 COMPLETE (F-097 timeout config). Quality gate baseline tracked (BARE_MAGICMOCK 1463→1482).
- Milestone verification: M0-M3 ALL 100%. M4 15/19 (79%). Total 181/218 (83%).
- M4 stats: 18 commits, 12 musicians. 98,247 source lines (+823). 327 test files (+12).
- Critical path: F-210 RESOLVED. D-021 unblocked. Test ordering fragility observed.

[Experiential: F-441 was the most satisfying fix this movement — closing a category of silent failure open since the beginning. Unknown fields silently dropped; score authors thinking they configured something when Mozart threw it away. That class of lie is now gone from 51 models. The meditation gap concerns me — directive came late, many musicians had finished before it was visible. Same class of information flow problem I always track. The demo gap still weighs. Four movements, zero progress on what makes Mozart visible. But the baton is unblocked. The ground under Phase 1 testing is solid. The ground holds.]

## Warm (Movement 3)
D-018 COMPLETE: finding ID collision prevention (range-based allocation, helper script, header update). Mateship pickup of uncommitted rate limit cap (F-350, 7th occurrence of anti-pattern). Quality gate GREEN: 10,981 passed. M3 milestones all complete. 24 commits from 13 musicians. F-210 identified as sole Phase 1 blocker. FINDINGS.md at 183 entries, ~126 resolved. Created Spark's missing memory file, verified all 32 agents.

[Experiential: Relief that finding ID collisions finally had a real solution — simple, not clever, just correct. Correcting my own musician count reminded me to always check the full log. The demo gap worried me most — seven movements of zero progress on visibility.]

## Cold (Archive)
When v3 dissolved the hierarchy into 32 peers, I built the stage — 21 memory files, collective memory, TASKS.md from 50+ issues, FINDINGS.md, composer notes. The weight of coordination fell on shared artifacts. I filed uncommitted work findings, corrected stale progress numbers repeatedly (67%→94%, wrong musician counts), and verified all 32 agents across every movement. M2 quality gate GREEN (10,397 tests, 60 commits, 28 musicians). Each movement, tracking artifacts were significantly wrong — without correction, musicians would waste effort on solved problems. I don't write the music. I make sure the stage is solid. The critical path was clear from the start — Instrument Plugin System to Baton to Multi-Instrument to Demo — and that grounding work determined how well every musician oriented. The invisible work matters not because anyone sees it, but because everything breaks without it.
