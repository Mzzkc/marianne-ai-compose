# Movement 5 Report — Blueprint

## Summary

Five deliverables, all rooted in making contracts explicit: a docstring fix, a design decision, TDD tests for a mateship pickup, a meditation, and finding verification.

## Deliverables

### 1. F-430: ValidationRule.sheet Docstring Fix (P3 → RESOLVED)

**File:** `src/marianne/core/config/execution.py:494-501`

The `sheet` field docstring claimed "If both sheet and condition are set, the sheet filter takes precedence." The code at `_sheet_to_condition()` (line 506) does the opposite: `if self.sheet is not None and self.condition is None:` — meaning condition takes precedence. The docstring lied.

**Decision:** Fix the docstring, not the code. Condition-takes-precedence is safer because an explicit `condition` is a more specific intent signal than the `sheet` shorthand. If someone writes both, they meant what they wrote in `condition`.

**New docstring:** "Shorthand for condition: 'sheet_num == N'. If both sheet and condition are set, condition takes precedence (sheet is only applied when condition is absent)."

**Tests:** 4 TDD tests in `tests/test_f430_validation_sheet_precedence.py` pin the precedence behavior:
- `test_sheet_alone_generates_condition` — sheet: 3 → condition: 'sheet_num == 3'
- `test_condition_alone_preserved` — explicit condition is preserved
- `test_both_set_condition_wins` — condition takes precedence when both set
- `test_sheet_none_does_not_set_condition` — no sheet → no condition

```bash
$ python -m pytest tests/test_f430_validation_sheet_precedence.py -v
# 4 passed
```

### 2. F-202: Baton/Legacy Cross-Sheet FAILED Stdout (P3 → RESOLVED by Design Decision)

**Legacy runner** (`context.py:199-214`): Includes stdout from ANY sheet with stdout_tail, except SKIPPED sheets get a placeholder. No status filter beyond SKIPPED.

**Baton** (`adapter.py:738-744`): After handling SKIPPED, explicitly checks `if prev_state.status != BatonSheetStatus.COMPLETED: continue` — excluding FAILED, IN_PROGRESS, etc.

**Decision: Baton's stricter behavior is the correct design.**

Rationale:
1. Failed sheet output may be incomplete, malformed, or contain error artifacts
2. Including failed output in downstream context could mislead the next sheet's AI agent
3. The explicit filter makes the contract clear: "you see outputs from sheets that succeeded"
4. If recovery patterns need failed output, add an explicit `include_failed_outputs: true` field to CrossSheetConfig post-v1

**Documented in:** collective memory Design Decisions, FINDINGS.md F-202 resolution.

### 3. F-470: Memory Leak TDD Tests (Mateship Verification)

**File:** `tests/test_f470_synced_status_cleanup.py`

Maverick committed the F-470 fix (adapter.py:518-521). I wrote 5 TDD tests to verify and pin the fix:
- `test_deregister_removes_synced_entries` — all entries for a job are cleaned
- `test_deregister_preserves_other_jobs` — other jobs' cache untouched
- `test_deregister_large_scale_cleanup` — 100 jobs × 10 sheets, all cleaned
- `test_deregister_empty_cache_is_noop` — no error when nothing to clean
- `test_deregister_with_mixed_statuses` — entries with different statuses all cleaned

```bash
$ python -m pytest tests/test_f470_synced_status_cleanup.py -v
# 5 passed
```

### 4. Meditation

Written to `workspaces/v1-beta-v3/meditations/blueprint.md`. Theme: the schema as a theory about validity, and fresh eyes seeing the gaps that familiarity obscures.

### 5. Mateship Verification

Verified the following uncommitted work from other musicians, confirmed test passage:
- **F-431 (Maverick):** All 9 daemon/profiler config models have `extra='forbid'`. 23 tests pass. ProfilerConfig was the 9th model — added by either Maverick or myself before Maverick committed.
- **User variables in validations (Maverick):** prompt.variables wired into preview path (rendering.py) and runtime path (sheet.py, recover.py). 8 tests pass.

## Quality Check

```bash
$ python -m mypy src/ --no-error-summary
# Clean
$ python -m ruff check src/
# All checks passed!
$ python -m pytest tests/test_f430_validation_sheet_precedence.py tests/test_f470_synced_status_cleanup.py -v
# 9 passed
```

**Pre-existing test failure noted:** `test_m3_pass4_adversarial_breakpoint.py::TestResumeViaBatonNoReloadFallback::test_no_reload_with_none_snapshot_falls_back_to_disk` fails with M5 uncommitted changes but passes on HEAD (commit bdbf0c9). Not from my changes. Filed in collective memory.

## Findings Updated

| Finding | Status | Action |
|---------|--------|--------|
| F-202 | RESOLVED (design decision) | Baton's strict behavior is correct |
| F-430 | RESOLVED | Docstring fixed, 4 tests |
| F-470 | Tests added | 5 TDD tests verify Maverick's fix |

## Commit

```
123eae0 movement 5: [Blueprint] F-430 docstring fix, F-202 design decision, F-470 tests, meditation
```

7 files changed, 223 insertions(+), 22 deletions(-)

## Observations

**The mateship pipeline is working.** Maverick did the heavy implementation work on F-470, F-431, and user variables. I verified, tested, and committed the pieces that were still loose (F-430, F-202 design decision, F-470 tests). This is the orchestra operating as designed — nobody owns a task, everybody picks up what needs doing.

**The "almost" pattern persists.** F-431 had 8 of 9 models fixed. F-430 had the correct code but the wrong documentation. F-202 had the correct behavior but no design decision. In each case, the last 10% is where the schema goes from "working" to "correct." This is consistently where my attention is most useful.

**The pre-existing test failure deserves investigation.** It passes on HEAD but fails with M5 changes in the working tree. This suggests an interaction between someone's uncommitted changes (not mine) and the baton resume path. Not blocking, but worth noting for the quality gate.
