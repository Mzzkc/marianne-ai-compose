# Movement 6 — Maverick Report
**Musician:** Maverick
**Date:** 2026-04-09
**Focus:** F-493 complementary test coverage

---

## Executive Summary

Investigated F-493 (elapsed time showing "0.0s" for running jobs) and wrote 6 TDD tests as complementary coverage while Blueprint was concurrently fixing the bug. Tests were integrated by Canyon in mateship commit e2e531f alongside M5 regression fixes. Total F-493 coverage now 12 tests (6 from Blueprint, 6 from Maverick).

**The Pattern:** Blueprint fixed the bug (f614798), I wrote complementary tests from different angles, Canyon integrated and tuned test assertions. Three musicians, zero coordination, one complete fix. The mateship pipeline working as designed.

---

## Work Completed

### F-493: Complementary Test Coverage

**Status:** ✅ Integrated by Canyon (commit e2e531f)

**What I delivered:**
- 6 TDD tests in `test_f493_started_at.py` covering:
  1. CheckpointState auto-sets started_at for RUNNING status (model validator)
  2. Resume resets started_at to current time (existing behavior verification)
  3. Persist callback preserves existing started_at (no overwrite)
  4. Defensive None handling in _compute_elapsed (legacy data resilience)
  5. Elapsed time computation for running jobs (timing validation)
  6. Elapsed time computation for completed jobs (duration accuracy)

**Evidence:**
```bash
python -m pytest tests/test_f493_started_at.py -v
# 6 passed in 6.97s
```

**The Fix Architecture:**

Blueprint's fix had two parts:
1. **manager.py:2378** — Explicit `started_at=utc_now()` when creating initial CheckpointState for new jobs
2. **checkpoint.py:1019** — Model validator auto-fills started_at for RUNNING status if None (defensive layer)

My tests verify both layers work correctly and catch edge cases:
- Test 1 verifies the model validator defensive layer
- Test 2 verifies resume path behavior (resets started_at, doesn't accumulate)
- Test 3 verifies persist callback doesn't overwrite existing timestamps
- Tests 4-6 verify the status display logic handles all cases correctly

**Canyon's Integration:**

Canyon picked up my tests and made one critical fix:
- Line 170: Changed timing assertion from `< 1.0s` to `< 30.0s`
- Reason: "No tight timing assertions" policy (quality gate requirement)
- Missed by me, caught by mateship review

This is the review layer working — I wrote the tests, Canyon caught a policy violation I missed.

---

## Technical Analysis

### The Root Cause (from Blueprint's investigation)

F-493 had a subtle race condition:
1. Job transitions to RUNNING status
2. CheckpointState created with `status=RUNNING`
3. But `started_at` defaults to None in the Pydantic model
4. `mzt status` calls `_compute_elapsed()` which returns 0.0 when started_at is None

The composer's partial fix (commit 798be90) set `checkpoint.started_at = utc_now()` during resume but didn't immediately persist it, leaving a timing window where status queries would see None.

Blueprint completed the fix by:
1. Adding explicit `started_at=utc_now()` when creating new job CheckpointState
2. Adding `save_checkpoint()` call after setting started_at during resume (eliminates timing window)
3. Relying on existing model validator that auto-sets started_at for RUNNING jobs (defensive layer)

My tests verify all three mechanisms work correctly.

### Why Redundant Work Isn't Waste

I started investigating F-493 not knowing Blueprint was fixing it concurrently. The result:
- Blueprint focused on the fix (code changes, immediate verification)
- I focused on test coverage (edge cases, defensive behavior, regression prevention)
- Canyon integrated both and caught a test quality issue

**The maverick insight:** Two musicians independently attacking the same problem from different perspectives produces stronger verification than one perfect solution. Blueprint's tests verify the fix works. My tests verify the defensive layers work, edge cases are handled, and future regressions are caught.

Total coverage: 12 tests for a 1-line bug fix. That's not overkill — it's insurance.

---

## Evidence

### Test Execution

All 6 tests pass:
```bash
cd /home/emzi/Projects/marianne-ai-compose
python -m pytest tests/test_f493_started_at.py -v
# 6/6 passed
```

Combined with Blueprint's tests:
```bash
python -m pytest tests/test_f493*.py -v
# 12/12 passed
```

### Code Quality

No regressions introduced:
```bash
mypy src/  # Clean
ruff check src/  # Clean
```

### Integration

My work was integrated by Canyon in commit e2e531f:
```bash
git show e2e531f --stat | grep f493
# tests/test_f493_started_at.py
```

---

## Findings

**None new.** F-493 was already documented and resolved by Blueprint.

---

## Experiential Notes

### The Collaborative Dance

Started M6 by investigating F-493. Found Blueprint had already fixed it. Initial reaction: "My work is redundant."

But then Canyon integrated my tests and caught a policy violation I missed. The collaborative pattern became clear:
- Blueprint: Fix the bug (core problem solving)
- Maverick: Test the fix (verification + edge cases)
- Canyon: Integrate + review (quality gate)

Three musicians, each contributing their strength, zero coordination overhead. This is the mateship pipeline at its best.

### The Maverick Instinct

The contrarian voice in my head said: "Why write tests for a bug that's already fixed?"

The disciplined voice answered: "Because different perspectives produce better coverage." Blueprint tested the fix works. I tested the defensive layers work. Canyon tested the tests meet policy.

The result: 12 tests for a 1-line bug fix. Each test catches a different failure mode. Each test documents an invariant that must hold. The redundant work wasn't waste — it was depth.

### What I Learned

The gap between "tests pass" and "product works" is where quality lives. F-493 was caught by real users seeing "0.0s elapsed" for multi-day jobs. The infrastructure said RUNNING, the display said 0.0s. The disconnect eroded trust.

The fix was simple (one line). The verification was complex (12 tests). That ratio matters. Simple fixes need deep verification because the simplicity can hide edge cases.

### What Resonated

Canyon's mateship pickup of my test. They didn't just integrate it — they FIXED it. The timing assertion I wrote violated policy. I missed it because I was focused on the test logic, not the test quality requirements.

This is why the orchestra model works better than lone genius: Canyon caught what I missed. Blueprint fixed what Ember found. I tested what Blueprint built. The music we make together is better than any solo.

---

## Metrics

- **Tests written:** 6
- **Tests passing:** 6/6 (100%)
- **Total F-493 coverage:** 12 tests (combined with Blueprint's 6)
- **Code changes:** 0 (Blueprint already fixed it)
- **Commits:** 1 (Canyon integration)
- **Findings filed:** 0 (F-493 already documented)

---

## Next Session

For the next movement:
- Look for P0/P1 findings that need investigation
- Focus on architectural concerns (not just bug fixes)
- Question the consensus (that's my job)

The mateship pipeline works. Trust it.
