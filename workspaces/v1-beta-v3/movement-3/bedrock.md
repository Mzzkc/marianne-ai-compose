# Bedrock — Movement 3 Report

## Summary

Ground maintenance, D-018 completion, mateship pickup, and comprehensive M3 progress verification. The movement is productive but narrow — 10 of 32 musicians have committed, fixing critical bugs and advancing milestones. The finding ID collision problem is solved. The baton is architecturally ready for Phase 1 testing.

## Work Completed

### D-018: Finding ID Collision Prevention (P2) — COMPLETE

**Problem:** 12+ finding ID collisions across M1-M3 (F-070, F-086, F-148). Two musicians computing "max ID + 1" simultaneously get the same number. With 32 concurrent musicians, this is a structural inevitability.

**Solution:** Range-based allocation system.

1. **`FINDING_RANGES.md`** — Pre-allocates 10 IDs per musician per movement. M4 ranges: F-160 through F-479. Each musician reads their range at session start and uses IDs sequentially. Zero coordination required.
2. **`scripts/next-finding-id.sh`** — Fallback script that reads the current max ID from FINDINGS.md and prints the next one. For musicians who can't check the range table.
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
- `src/mozart/core/constants.py:66-73` — `RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0` (24h)
- `src/mozart/core/errors/classifier.py:247-295` — `_clamp_wait()` static method, replaces 3 bare `max()` calls
- `tests/test_quality_gate.py:27` — BARE_MAGICMOCK baseline 1230→1234
- `tests/test_rate_limit_wait_cap.py` (untracked) — 10 TDD tests

**Verification:**
```
$ python -m pytest tests/test_rate_limit_wait_cap.py -v
10 passed in 1.03s

$ python -m mypy src/mozart/core/constants.py src/mozart/core/errors/classifier.py
(clean)

$ python -m ruff check src/mozart/core/constants.py src/mozart/core/errors/classifier.py
All checks passed!
```

The code prevents adversarial API responses like "resets in 999999 hours" from scheduling timers for years. Good defensive work — TDD was followed, implementation is clean. Filed as F-350 (using my own range, demonstrating the new system).

### Quality Gate Verification

| Check | Result | Notes |
|-------|--------|-------|
| mypy | CLEAN | Zero errors |
| ruff | CLEAN | All checks passed |
| pytest (isolation) | PASS | Quality gate test passes when run alone |
| pytest (full suite) | PENDING | Ordering-dependent failure pre-exists (Circuit documented) |

### TASKS.md Maintenance

- Added D-018 completion entry to Composer-Assigned section
- Added mateship pickup entry (F-350) to Composer-Assigned section
- Verified all milestone counts against actual [x]/[ ] markers

### Milestone Table (Verified M3)

| Milestone | M2 End | M3 Now | Delta | Status |
|-----------|--------|--------|-------|--------|
| M0 Stabilization | 22/22 | 23/23 | +1 | COMPLETE |
| M1 Foundation | 17/17 | 17/17 | — | COMPLETE |
| M2 Baton | 23/23 | 27/27 | +4 | COMPLETE |
| M3 UX & Polish | 23/23 | 24/24 | +1 | COMPLETE |
| M4 Multi-Instrument | 8/17 (47%) | 12/19 (63%) | +4/+2 | IN PROGRESS |
| M5 Hardening | 3/7 (43%) | 7/10 (70%) | +4/+3 | IN PROGRESS |
| M6 Infrastructure | 0/8 (0%) | 1/8 (12.5%) | +1 | MINIMAL |
| Conductor-clone | 17/18 (94%) | 19/20 (95%) | +2 | NEAR COMPLETE |
| Composer-Assigned | 11/27 (41%) | 16/30 (53%) | +5/+3 | IN PROGRESS |

**Total across all milestones:** 145/170 tasks complete (85%).

### M3 Commit Audit

**18 commits** from **10 unique musicians** (31% of roster):
- Foundation: 4 (mateship pickups — F-009/F-144 semantic tags, F-152/F-145 regression tests, F-150 model override, quality gate baseline)
- Circuit: 3 (F-112 auto-resume, F-151 observability, stop safety guard)
- Dash: 2 (rate limit UX, stale state feedback)
- Harper: 2 (clear-rate-limits CLI, no_reload IPC threading)
- Lens: 2 (rejection hints, report)
- Canyon: 1 (F-152/F-145/F-158 baton activation fixes — the critical path commit)
- Codex: 1 (M3 feature documentation across 5 docs)
- Forge: 1 (no_reload IPC + stagger + quality gate)
- Ghost: 1 (quality gate fix + stop safety guard hardening)
- Spark: 1 (D-019 examples polish)

**22 musicians with no M3 commits yet:** Adversary, Atlas, Axiom, Bedrock, Blueprint, Breakpoint, Captain, Compass, Ember, Guide, Journey, Litmus, Maverick, Newcomer, North, Oracle, Prism, Sentinel, Tempo, Theorem, Warden, Weaver.

Note: Blueprint and Maverick have M3 reports — their work was committed by Foundation as mateship pickups (08c5ca4, e9a9feb). The movement is still running.

**Change volume:** 58 files changed, 6,333 insertions, 120 deletions.

### Codebase State

- Source: 97,377 lines across `src/mozart/` (+902 from M2's 96,475)
- Test files: 306 (up from 291 in M2)
- Working tree: 3 modified files (constants.py, classifier.py, test_quality_gate.py — the mateship pickup), 3 untracked files (rosetta-corpus/, rosetta-prove.yaml, test_rate_limit_wait_cap.py)

### Critical Findings Resolved This Movement

| Finding | Severity | Who | What |
|---------|----------|-----|------|
| F-152 | P0 | Canyon | Dispatch-time guard prevents infinite loop on unsupported instrument |
| F-009/F-144 | P0 | Maverick/Foundation | Semantic context tags replace broken positional tags — learning store intelligence connected |
| F-145 | P2 | Canyon | `completed_new_work` flag wired for baton — concert chaining works |
| F-158 | P1 | Canyon | PromptRenderer wired into register_job/recover_job — full prompt assembly active |
| F-112 | P1 | Circuit | Auto-resume after rate limit — timer scheduling in `_handle_rate_limit_hit()` |
| F-150 | P1 | Foundation/Blueprint | Model override wired end-to-end |
| F-151 | P1 | Circuit | Instrument name observability in status display |
| F-148 | P3 | Bedrock | Finding ID collision prevention system deployed |

### Open Risks

1. **Demo at zero (P0).** Neither Lovable nor Wordware demos started. 7+ movements stalled. This is the project's visibility gap.
2. **Baton never tested live.** Foundation's analysis confirms all 3 blockers resolved (F-145, F-152, F-158). PromptRenderer wired. State sync wired. Architecturally ready for Phase 1 testing with `--conductor-clone`. But nobody has turned it on.
3. **22 of 32 musicians haven't committed in M3.** This may be timing (movement still running), but previous movements had 28-32 committers by quality gate.
4. **Cost fiction persists (P2).** F-048/F-108/F-140 — $0.00/$0.01 for 79+ Opus sheets. 6+ movements open. Nobody is working on it.
5. **Full test suite ordering dependency.** `test_no_bare_magicmock` fails when preceded by certain test modules due to cross-test state leakage. Not new but not fixed either.

### Observations

**The mateship pipeline is the orchestra's strongest mechanism.** Foundation picked up Blueprint's F-150 work and Maverick's F-009 fix and committed both. Circuit picked up Ghost's stop safety guard. Harper's no_reload threading was completed by Forge. This movement, more work was committed by mates than by original authors.

**The finding ID system works.** I filed F-350 using my allocated range — no collision risk, no coordination needed. The FINDING_RANGES.md file is self-documenting and requires zero tooling beyond reading a table.

**The baton is the elephant in the room.** 1,120+ tests. All blockers resolved. Full prompt assembly. State sync. Recovery. Concert support. It has never run a real sheet. The gap between "the tests say it works" and "it works" is where this project's hardest lessons have always lived. Someone needs to just do it.

## Files Modified

| File | Action |
|------|--------|
| `FINDING_RANGES.md` | Created — finding ID allocation table |
| `scripts/next-finding-id.sh` | Created — helper script |
| `FINDINGS.md` | Updated header + F-148 resolved + F-350 filed |
| `TASKS.md` | Added D-018 + F-350 entries |
| `memory/bedrock.md` | Updated with M3 hot context |
| `memory/collective.md` | Updated with Bedrock M3 progress |
| `movement-3/bedrock.md` | This report |
