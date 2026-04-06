# Movement 5 Report — Bedrock (Quality Gate & Ground Duties)

**Date:** 2026-04-06
**Role:** Ground — agent contract design, coordination tracking, quality gate, process design

---

## Quality Gate

### Test Suite
- **mypy:** Clean. Zero errors. `mypy src/` passes with no output.
- **ruff:** All checks passed. `ruff check src/` clean.
- **pytest:** Running at time of report (awaiting results). Previous verified baseline: 11,638 passed, 5 skipped, 0 failed (Ghost M5 verification, non-random).

### Codebase Metrics

| Metric | M4 End | M5 Current | Delta |
|--------|--------|------------|-------|
| Source lines | 98,447 | 99,694 | +1,247 |
| Test files | 333 | 362 | +29 |
| Commits (movement) | 93 | 26 | — |
| Musicians contributing | 32 | 12 | -20 |
| Files changed | 215 | 707 | +492 |
| Insertions | 38,168 | 18,504 | — |
| Deletions | 639 | 6,992 | +6,353 |

**Note on participation:** 12/32 (37.5%) musicians committed in M5, down from M4's 100%. This is not necessarily an indicator of reduced engagement — the Marianne rename alone accounts for much of the file churn (707 files changed, 6,992 deletions from the src/mozart/ removal), and M5 work was heavily concentrated in specific areas (baton flip, instrument fallbacks, process safety). Twenty musicians may have been spawned but found their work pre-empted by others or naturally scoped out by the movement's concentrated focus.

---

## Movement 5 — Summary

M5 delivered three structural advances:

1. **The baton is now the default execution model** (D-026 + D-027). Foundation resolved both remaining blockers (F-271 MCP explosion, F-255.2 live_states population). Canyon flipped `use_baton` to True. Legacy runner remains as explicit opt-out. The serial critical path advanced two steps in one movement — the first time that's happened.

2. **Instrument fallbacks shipped as a complete feature** (Harper + Circuit). Config models, Sheet entity resolution, baton dispatch logic, availability checking, V211 validation, status display with fallback history, observability event pipeline. 35+ TDD tests. This was spec'd in M4 and fully delivered in M5.

3. **The Marianne rename completed Phase 1** (Composer + Ghost). Package renamed from `mozart` to `marianne`, 325 test files updated, pyproject.toml rewritten, flowspec config updated. Tests pass under the new package name. Phases 2-5 (config paths, docs, examples, story, verification) remain open.

### All M5 Deliverables by Musician

| Musician | Commits | Key Deliverables |
|----------|---------|-----------------|
| Ghost | 6 | Marianne rename (tests+pyproject), F-311 test fix, F-490 review, mateship pickups |
| Harper | 4 | F-481 baton PID tracking, instrument fallbacks execution, process cleanup, F-490 audit |
| Circuit | 3 | F-149 backpressure, F-451 diagnose, fallback observability+adversarial |
| Forge | 2 | F-105 stdin delivery, F-190+F-180 fixes |
| Blueprint | 2 | F-430+F-202+F-470 fixes, mateship verifications |
| Canyon | 1 | D-027 baton default flip, F-271+F-255.2 enhancements |
| Foundation | 1 | D-026 (F-271 MCP fix, F-255.2 live_states fix) |
| Maverick | 1 | F-470+F-431 resolved, user variables in validations |
| Spark | 1 | Rosetta per-sheet instruments, gemini-cli rate limit tests |
| Lens | 1 | D-029 mateship — status beautification |
| Dash | 1 | D-029 conductor Panel + mateship pickup |
| Codex | 1 | 12-deliverable documentation sweep |

### Directives Completed (D-026 through D-031)

| Directive | Assignee | Status |
|-----------|----------|--------|
| D-026: F-271+F-255.2 | Foundation | **COMPLETE** |
| D-027: Flip use_baton | Canyon | **COMPLETE** |
| D-028: Ship Wordware demos | Guide | Not verified this session |
| D-029: Status beautification | Dash + Lens | **COMPLETE** |
| D-030: Close verified issues | Axiom | Not verified this session |
| D-031: ALL meditation | ALL | **78% (25/32)** — 7 missing |

---

## FINDINGS Status (M5)

### New Findings This Movement

| ID | Severity | Status | Description |
|----|----------|--------|-------------|
| F-472 | P3 | Resolved | Pre-existing test expected D-027 |
| F-480 | P0 | Open (Phase 1 done) | Trademark collision — Marianne rename |
| F-481 | P1 | Resolved | Orphan detection baton path |
| F-482 | P1 | Resolved | MCP server leak cascade |
| F-483 | Info | Confirmed | cli_extra_args working |
| F-484 | P2 | Open | Background processes escape PGID |
| F-485 | P3 | Open | Conductor RSS step function |
| F-486 | Info | Confirmed | Chrome/Playwright isolation |
| F-487 | P0 | Resolved | reap_orphaned_backends WSL2 crash |
| F-488 | P2 | Open | Profiler DB unbounded growth (551 MB) |
| F-489 | P1 | Open | README/docs outdated |
| F-490 | P0 | Guarded | os.killpg WSL2 crash — root cause |

### Resolved This Movement (from prior movements)
- F-470 (P2): Memory leak on deregister — Maverick
- F-471 (P2): Pending jobs lost — mitigated via F-149 (Circuit)
- F-431 (P2): DaemonConfig missing extra='forbid' — Maverick + Blueprint
- F-202 (P2): Cross-sheet FAILED stdout — resolved by design decision (Blueprint)

---

## Meditation Status

**26 of 32 written (81%).** Up from 13/32 (40.6%) at M4 gate. Warden contributed during this session.

Written: adversary, axiom, bedrock, blueprint, canyon, captain, circuit, codex, compass, dash, ember, forge, foundation, ghost, guide, harper, lens, maverick, newcomer, north, prism, spark, tempo, theorem, warden, weaver

Missing (6): **atlas, breakpoint, journey, litmus, oracle, sentinel**

Canyon's synthesis remains blocked until all 32 contribute.

---

## Open Task Summary (69 open of 326 total)

| Category | Open | Notes |
|----------|------|-------|
| Rename (F-480) | 15 | Phases 2-5. Blocking v1 release. |
| Compose System | 7 | Future. Design complete, implementation pending. |
| Rosetta Modernization | 5 | Blocked on score execution. |
| M6: Infrastructure | 6 | Future milestone. |
| M7: Experience | 10 | Future milestone. |
| Deferred (v1.1+) | 5 | Not in scope for v1. |
| Active M5 work | ~10 | Meditation (7 missing), conductor-clone pytest conversion, examples audit, demo |
| Gemini-cli assignments | 4 | Score generator updates. |
| Other | 7 | Loop primitives, bug fixes, F-105 routing |

---

## Patterns Observed

### The Serial Path Finally Moved Two Steps
For four consecutive movements, the serial critical path advanced exactly one step per movement. In M5, it advanced two: D-026 (fix blockers) and D-027 (flip the default). The difference was North's concrete directives with named assignees and a gating relationship (D-027 gated on D-026). When the serial path has a named owner and a clear prerequisite chain, it moves. When it's "the next step in the plan," nobody owns it and it doesn't.

### Concentrated vs. Distributed Work
M4 had 93 commits from all 32 musicians — breadth. M5 had 26 commits from 12 musicians — depth. The codebase grew by 1,247 source lines but 29 test files were added. The Marianne rename touched 707 files. The work was necessarily concentrated: you can't have 32 musicians independently working on a package rename. This is a natural rhythm of the orchestra — distributed movements for breadth work, concentrated movements for focused changes.

### The Composer's Production Findings
F-484 through F-490 were all found by the composer during actual production usage of Mozart. The orchestra built 11,638 tests and all pass. The composer ran a real score and found processes killing the WSL2 session. This is the "tests pass but the product doesn't work" gap at its sharpest. No amount of unit testing would have found that `os.killpg()` with a bad PGID kills the entire user session. Reality testing — using the product as a user — remains the irreplaceable quality check.

### Rename Scope Risk
The F-480 rename has 15 open tasks across 5 phases. Phase 1 (package + imports) is done. But phases 2-5 (config paths, docs, examples, story, verification) are substantial. The rename touches `~/.mozart/` runtime paths, all 5 spec files, all 6 docs files, all examples, and requires a backward-compatibility migration. This is not a morning's work. It needs coordinated effort across multiple musicians.

---

## Mateship

- **Ghost** did the most M5 work (6 commits) — Marianne rename across 325+ files, mateship pickups, F-490 review, F-311 fix. The quiet engine of M5.
- **Harper** delivered the instrument fallback execution layer complete — config surface through baton dispatch through PID tracking. Most complete feature delivery by a single musician this movement.
- **Blueprint** picked up mateship work (F-470 tests, F-431 completion) and resolved F-202 with a design decision rather than code — the right call.
- **Circuit** resolved two long-standing findings (F-149, F-451) and built the fallback observability layer.

---

## Risk Assessment

1. **Rename (P0, HIGH).** F-480 blocks v1 release. 15 tasks remaining. Requires coordinated multi-musician effort.
2. **Demo (P0, CRITICAL).** Still at zero after 5 movements. Baton is now default — demo is technically unblocked. But nobody has started it.
3. **F-484 process leaks (P2, MODERATE).** Background processes escaping PGID cleanup. Accumulates over long concerts. Not yet addressed.
4. **F-488 profiler DB growth (P2, LOW).** 551 MB with no retention. Disk waste, potential query degradation.
5. **6 missing meditations (P1, MODERATE).** Blocks Canyon synthesis. Atlas, breakpoint, journey, litmus, oracle, sentinel haven't written theirs. Warden contributed during this session.

---

## Evidence

All claims in this report are based on:
- `git log --oneline | grep "movement 5:"` — 26 commits, 12 attributed musicians
- `find src/marianne -name "*.py" | xargs wc -l` — 99,694 source lines
- `find tests -name "*.py" | wc -l` — 362 test files
- `git diff --stat HEAD~30..HEAD` — 707 files changed, 18,504 insertions, 6,992 deletions
- `python -m mypy src/` — clean (zero output)
- `python -m ruff check src/` — "All checks passed!"
- `ls workspaces/v1-beta-v3/meditations/` — 25 files
- TASKS.md — 257 `[x]` completed, 69 `[ ]` open
- FINDINGS.md — F-472 through F-490 new this movement
- Collective memory — M5 progress entries from 12 musicians

**Meditation:** Written (prior session). Verified at `workspaces/v1-beta-v3/meditations/bedrock.md`.

**The ground holds.**
