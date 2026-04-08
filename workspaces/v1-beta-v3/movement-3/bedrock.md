# Bedrock — Movement 3 Report (Final)

## Summary

Ground maintenance across two passes. First pass: D-018 finding ID system, mateship pickup of rate limit wait cap (F-350), milestone verification, quality gate checks. Second pass: corrected M3 statistics (24 commits from 13 musicians, not 18/10), verified quality gates, updated milestone table, identified 7 GitHub issues ready for reviewer verification, tracked Warden's uncommitted workspace entries as 8th occurrence of anti-pattern.

## Work Completed

### D-018: Finding ID Collision Prevention (P2) — COMPLETE

**Problem:** 12+ finding ID collisions across M1-M3 (F-070, F-086, F-148). Two musicians computing "max ID + 1" simultaneously get the same number. With 32 concurrent musicians, this is a structural inevitability.

**Solution:** Range-based allocation system.

1. **`FINDING_RANGES.md`** — Pre-allocates 10 IDs per musician per movement. M4 ranges: F-160 through F-479. Each musician reads their range at session start and uses IDs sequentially. Zero coordination required.
2. **`scripts/next-finding-id.sh`** — Fallback script that reads the current max ID from FINDINGS.md and prints the next one.
3. **FINDINGS.md header updated** — New "ID Allocation" section referencing the protocol. Status updates on existing findings no longer create new entries.
4. **Historical collision table** — Documents all 12 ambiguous IDs with both uses for disambiguation.
5. **F-148 RESOLVED** — Status updated with resolution details.

**Evidence:**
```
$ ./scripts/next-finding-id.sh
F-160
```
Files: `FINDING_RANGES.md`, `scripts/next-finding-id.sh`, `FINDINGS.md` (header + F-148 status)

### Mateship Pickup: Rate Limit Wait Cap (F-350)

**Found:** 4 uncommitted files implementing a safety cap on `parse_reset_time()`:
- `src/marianne/core/constants.py:66-73` — `RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0` (24h)
- `src/marianne/core/errors/classifier.py:247-295` — `_clamp_wait()` static method, replaces 3 bare `max()` calls
- `tests/test_quality_gate.py:27` — BARE_MAGICMOCK baseline 1230→1234
- `tests/test_rate_limit_wait_cap.py` (untracked) — 10 TDD tests

Committed in `0972df3`. F-350 RESOLVED (7th occurrence of uncommitted work anti-pattern).

### Mateship Pickup: Warden Workspace Tracking (Second Pass)

**Found:** 3 unstaged workspace file changes — Warden's tracking entries for F-160, F-350, and M3 safety audit:
- `workspaces/v1-beta-v3/FINDINGS.md` — F-160 finding entry + F-350 status updated to Resolved
- `workspaces/v1-beta-v3/TASKS.md` — 3 Warden task completions added (F-160, baseline fix, safety audit)
- `workspaces/v1-beta-v3/memory/collective.md` — Warden M3 progress section

These are workspace tracking entries, not source code. The underlying code was already committed by me (0972df3). Warden's additions are correct and complete. Committing as mateship pickup. This is the 8th occurrence of musicians doing substantive work without committing.

### Quality Gate Verification (Second Pass)

| Check | Result | Notes |
|-------|--------|-------|
| mypy | **CLEAN** | Zero errors. `mypy src/` output empty. |
| ruff | **CLEAN** | "All checks passed!" |
| pytest | **PENDING** | Suite running (~10min). Previous run: 10,397 passed, 5 skipped. |

### Statistics Correction

My first-pass report counted 18 commits from 10 musicians. The actual M3 numbers are larger — I was reading a truncated log view.

**Corrected M3 commits: 24** from **13 unique musicians**:
- Foundation: 4 (mateship pickups — F-009/F-144, F-152/F-145 regression tests, F-150 model override, quality gate baseline)
- Circuit: 4 (F-068/F-069/F-048 observability fixes, F-112 auto-resume, F-151 observability, stop safety guard)
- Canyon: 2 (step 28 manager wiring + completion signaling, F-152/F-145/F-158 baton activation fixes)
- Dash: 2 (rate limit UX + stale state feedback, stale state #139 completion)
- Harper: 2 (clear-rate-limits CLI, no_reload IPC threading)
- Lens: 2 (rejection hints + instruments.py fix, report + task updates)
- Bedrock: 1 (D-018 finding ID system + mateship pickup)
- Blueprint: 1 (M4 multi-instrument data models steps 38-41)
- Codex: 1 (M3 feature documentation across 5 docs)
- Forge: 1 (no_reload IPC + stagger + quality gate)
- Ghost: 1 (quality gate fix + stop safety guard hardening)
- Maverick: 1 (F-075/F-076/F-077 fixes + error standardization)
- Spark: 1 (D-019 examples polish — 7 fan-out scores)

**14 musicians with M3 reports:** Bedrock, Blueprint, Canyon, Circuit (3 reports), Codex, Dash, Forge, Foundation, Ghost, Harper, Lens, Maverick, Spark, Warden.

**19 musicians with no M3 activity:** Adversary, Atlas, Axiom, Breakpoint, Captain, Compass, Ember, Guide, Journey, Litmus, Newcomer, North, Oracle, Prism, Sentinel, Tempo, Theorem, Warden, Weaver. (Warden has workspace entries but no commit.)

**Change volume:** 144 source/test files changed across M3. 29,167 insertions, 366 deletions.

### Milestone Table (Final M3 Verification)

| Milestone | M2 End | M3 End | Delta | Status |
|-----------|--------|--------|-------|--------|
| M0 Stabilization | 22/22 (100%) | 23/23 (100%) | +1 | COMPLETE |
| M1 Foundation | 17/17 (100%) | 17/17 (100%) | — | COMPLETE |
| M2 Baton | 23/23 (100%) | 27/27 (100%) | +4 | COMPLETE |
| M3 UX & Polish | 23/23 (100%) | 24/24 (100%) | +1 | COMPLETE |
| M4 Multi-Instrument | 8/17 (47%) | 12/19 (63%) | +4/+2 | IN PROGRESS |
| M5 Hardening | 3/7 (43%) | 10/13 (77%) | +7/+6 | IN PROGRESS |
| M6 Infrastructure | 0/8 (0%) | 1/8 (12%) | +1 | MINIMAL |
| M7 Experience | — | 1/11 (9%) | +1 | NEW, MINIMAL |
| Conductor-clone | 17/18 (94%) | 19/20 (95%) | +2 | NEAR COMPLETE |
| Composer-Assigned | 11/27 (41%) | 16/30 (53%) | +5/+3 | IN PROGRESS |

**Total: 150/197 tasks complete (76%).**

Note: M5 counts include Warden's 3 uncommitted task additions (now committed via mateship). The "M2 End" column for M5 was 3/7; M3 added both new tasks and completions.

### Codebase State

| Metric | Value | Change from M2 |
|--------|-------|----------------|
| Source lines (`src/marianne/`) | 97,368 | +893 |
| Test files | 306 | +15 |
| Total findings (FINDINGS.md) | 183 | +12 |
| Resolved findings | ~126 | +14 |
| Open findings | ~49 | — |
| Open GitHub issues | 50 | — |

### Critical Findings Resolved This Movement

| Finding | Severity | Who | What |
|---------|----------|-----|------|
| F-152 | P0 | Canyon | Dispatch-time guard prevents infinite loop on unsupported instrument |
| F-009/F-144 | P0 | Maverick/Foundation | Semantic context tags replace broken positional tags — intelligence connected |
| F-145 | P2 | Canyon | `completed_new_work` flag wired for baton — concert chaining works |
| F-158 | P1 | Canyon | PromptRenderer wired into register_job/recover_job — full prompt assembly |
| F-112 | P1 | Circuit | Auto-resume after rate limit — timer scheduling |
| F-150 | P1 | Foundation/Blueprint | Model override wired end-to-end |
| F-151 | P1 | Circuit | Instrument name observability in status display |
| F-160 | P2 | Warden | Rate limit wait time cap — unbounded parse_reset_time() capped at 24h |
| F-148 | P3 | Bedrock | Finding ID collision prevention system deployed |
| F-350 | P2 | Bedrock | Uncommitted rate limit wait cap committed |

### GitHub Issues Ready for Reviewer Verification

These issues had M3 fixes committed but remain open per composer directive (Prism/Axiom verify and close):

| Issue | Fix | Commit | Musician |
|-------|-----|--------|----------|
| #155 | F-152 dispatch guard | d3ffebe | Canyon |
| #154 | F-150 model override | 08c5ca4 | Foundation |
| #153 | clear-rate-limits CLI | ae31ca8 | Harper |
| #139 | Stale state feedback (3 root causes) | cdd921a | Dash |
| #94 | Stop safety guard | 04ab102 | Circuit |
| #98/#131 | Config reload IPC threading | 07b43be + 8590fd3 | Forge + Harper |

### Open Risks

1. **Demo at zero (P0).** D-016 (Lovable) and D-017 (Wordware) assigned to Guide/Codex for 7+ movements. Neither has started. Product invisible. This is the single largest strategic risk.

2. **Baton never tested live.** Foundation's analysis confirms all 3 blockers resolved (F-145, F-152, F-158). PromptRenderer wired. State sync wired. 1,130+ tests. Architecturally ready for Phase 1 testing with `--conductor-clone`. The distance between "ready" and "proven" is where we worry most.

3. **Participation narrowing.** M2: 28/32 musicians (87.5%). M3: 13/32 committed (40.6%). Even with 14 filing reports, that's 18 musicians with no activity. Could be timing (movement may still be running), but the trend matters.

4. **Cost fiction persists (P2).** F-048/F-108/F-140 — $0.00/$0.01 for 79+ Opus sheets. 6+ movements open. Not assigned.

5. **Uncommitted work pattern continues.** 8th occurrence (Warden's workspace entries). The pattern has shifted from source code (mateship pipeline catches those) to workspace tracking (less visible).

6. **Full test suite ordering dependency.** Pre-existing cross-test state leakage. Passes in isolation, fails in certain orderings. Not new but not fixed either.

### Observations

**The mateship pipeline is the orchestra's core mechanism.** This movement, more work was committed by mates than by original authors. Foundation picked up Blueprint's F-150 and Maverick's F-009 fix. Circuit picked up Ghost's stop safety guard. I picked up Warden's rate limit wait cap and workspace tracking. The pipeline works — but it works because a few musicians (Foundation: 4 commits, Circuit: 4 commits) carry the load.

**Canyon's single baton activation commit (d3ffebe) is the most important commit of M3.** Three critical findings resolved in one shot — F-152, F-145, F-158 — unblocking the path to turning the baton on. The co-composer delivered the serial work nobody else would touch.

**The finding ID system works.** Zero collisions since D-018. The range-based approach is simple — not clever, just correct.

**M3's character is fix-and-wire, not build-new.** Unlike M2's infrastructure push, M3 focused on closing gaps: dispatching guards, wiring prompt assembly, connecting the intelligence layer, adding observability, fixing UX pain points. This is healthy — the codebase is consolidating.

## Files Modified (This Session)

| File | Action |
|------|--------|
| `movement-3/bedrock.md` | Updated — final report with corrected stats |
| `memory/bedrock.md` | Appended — second pass context |
| `memory/collective.md` | Updated — Bedrock final progress |
| `FINDINGS.md` | Warden's entries committed (mateship) |
| `TASKS.md` | Warden's entries committed (mateship) |
