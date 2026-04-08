# Movement 4 — Adversary Report

**Role:** Adversarial testing, security analysis, edge case discovery
**Date:** 2026-04-05
**Test file:** `tests/test_m4_adversarial_adversary.py` (55 tests, 8 test classes)

## Executive Summary

Sixth adversarial pass across the Marianne codebase. 55 new tests targeting the M4 changes — primarily the F-441 config strictness fix (`extra='forbid'` on all 51 config models), the F-211 checkpoint sync dedup cache, pending job lifecycle, cross-sheet context, and auto-fresh detection. Two architectural findings filed (F-470, F-471). Zero code-level bugs in the F-441 implementation. The strictness change is the most impactful defensive fix since the terminal guard pattern.

Total adversarial tests across all movements: **387** (332 + 55).

---

## Work Completed

### 1. F-441 Config Strictness Adversarial Tests (20 tests)

**Target:** All config models with `extra='forbid'` — the most impactful M4 change.

Tested 14 model families:
- `JobConfig` top-level unknown fields (3 tests including multiple unknowns)
- `SheetConfig` nested unknown fields + `strip_computed_fields` backward compat (3 tests)
- `RetryConfig`, `ParallelConfig` nested unknowns (2 tests)
- `CostLimitConfig`, `GroundingConfig`, `CrossSheetConfig` deeply nested (3 tests)
- `ValidationRule`, `StaleDetectionConfig`, `ConcertConfig`, `NotificationConfig` (4 tests)
- `InjectionItem` alias (`as` → `as_`) + forbid interaction (2 tests)
- `InstrumentDef`, `MovementDef` unknown fields (2 tests)
- Backend ↔ instrument bridge coexistence (1 test)

**Key verification:** `InjectionItem` uses `ConfigDict(populate_by_name=True, extra="forbid")` — both the alias path (`as`) and field name path (`as_`) work correctly, and unknown fields are still rejected. This interaction is subtle and non-obvious.

**Key verification:** `strip_computed_fields` model_validator runs before `extra='forbid'`, so old scores with `total_sheets` are silently stripped while other unknown fields are correctly rejected. The backward compat is surgical.

**Finding:** Zero bugs. The F-441 implementation is correct across all model families.

### 2. F-211 Sync Dedup Cache Lifecycle Tests (4 tests)

**Target:** `BatonAdapter._synced_status` cache at `adapter.py:344`

**F-470 FOUND:** The `_synced_status` dict is NOT cleaned up when `deregister_job()` is called. The method at `adapter.py:492-518` cleans up `_job_sheets`, `_job_renderers`, `_job_cross_sheet`, `_completion_events`, `_completion_results`, and `_active_tasks` — but not `_synced_status`. The cache grows O(total_sheets_ever) for the daemon's lifetime.

**Evidence:** `test_synced_status_not_cleaned_on_deregister` — after deregistering 100 jobs (1000 total entries), all 1000 entries remain in the cache.

**Impact:** For the v3 orchestra (706 sheets per run), this accumulates ~706 stale entries per execution cycle. Over days of operation, this could reach tens of thousands of entries. Not critical for beta but a real production concern.

**Error class:** Same as F-129 (ephemeral state), F-077 (lifecycle mismatch).

### 3. Auto-Fresh Edge Cases (9 tests)

**Target:** `_should_auto_fresh()` at `manager.py:49-71`

Boundary conditions tested:
- `completed_at` in the future (clock skew): correctly returns False
- Both timestamps equal: correctly returns False (tolerance pushes threshold)
- mtime at exact tolerance boundary: correctly returns False (strict `>`)
- mtime epsilon past tolerance: correctly returns True
- `completed_at = None` (never ran): returns False, stat() not called
- `completed_at = 0.0` (epoch): any recent mtime triggers
- Negative `completed_at`: arithmetic works correctly
- `PermissionError` / `FileNotFoundError` from stat(): gracefully returns False

**Finding:** Zero bugs. The implementation handles all edge cases correctly.

### 4. Cross-Sheet Context Edge Cases (6 tests)

**Target:** `BatonAdapter._collect_cross_sheet_context()` at `adapter.py:690-807`

Tests:
- All upstream SKIPPED: returns `{1: "[SKIPPED]", 2: "[SKIPPED]", 3: "[SKIPPED]"}` (not empty)
- Mixed status ordering: COMPLETED stdout included, SKIPPED markers injected, FAILED excluded (F-202 documented behavior)
- Cross-sheet disabled (no config registered): returns empty dicts
- `lookback_sheets=3` from sheet 10: correctly limits to sheets 7, 8, 9
- Truncation at exact max_chars: content NOT truncated
- Truncation at max_chars + 1: content IS truncated with `[truncated]` marker

**Finding:** Zero bugs. The cross-sheet context implementation is correct.

### 5. Credential Redaction Defensive Pattern (5 tests)

**Target:** The `redact_credentials(content) or content` pattern at `adapter.py:784`

Verified the `or content` fallback is safe:
- Full credential string: replacement label is truthy, `or` doesn't fire
- Non-credential text: passes through unchanged
- `None` input: `None or None` = `None` (safe)
- Empty string: `"" or ""` = `""` (safe)
- Multiple credentials: all replaced, count verified

### 6. Real Score Pattern Validation (7 tests)

Verified that `extra='forbid'` doesn't break legitimate score patterns:
- `instrument_config` (dict[str, Any]) passthrough: arbitrary keys accepted
- `prompt.variables` (dict[str, Any]) passthrough: arbitrary user data accepted
- `movements` with known fields: accepted
- `movements` with unknown field: rejected
- `per_sheet_instruments`: accepted
- `fan_out`: accepted (expands correctly, clears afterward)

### 7. Baton State Mapping Completeness (2 tests)

- All 11 `BatonSheetStatus` enum values map to a valid checkpoint status
- All terminal baton statuses map to terminal checkpoint statuses

### 8. Feature Interaction Tests (4 tests)

Verified M4 features compose correctly:
- `cross_sheet` + `instrument_map`: accepted
- `movements` + `per_sheet_instruments` + `cross_sheet`: accepted
- `concert` config with `extra='forbid'`: accepted
- `spec` corpus config with `extra='forbid'`: accepted

---

## Findings Filed

| ID | Severity | Category | Summary |
|----|----------|----------|---------|
| F-470 | P2 | bug | `_synced_status` memory leak on `deregister_job()` |
| F-471 | P2 | architecture | Pending jobs (`_pending_jobs` dict) lost on daemon restart |

---

## Quality Gates

```
pytest tests/test_m4_adversarial_adversary.py: 55 passed in 0.54s
mypy src/: clean
ruff src/: All checks passed
```

---

## Meditation

Written to `meditations/adversary.md` — *The Locksmith's Meditation*. On the relationship between breaking and building, the gift of fresh eyes for adversarial work, and the compounding of discipline. Generic, no project references.

---

## Assessment

The F-441 strictness fix is the single most impactful defensive change since the terminal state guard pattern in M1. Before F-441, any typo in a score was silently accepted — `instrument_fallbacks: [gemini-cli]` passed validation while doing nothing. Now it produces a clear, actionable error. This closes the trust gap between what users write and what Marianne executes.

The bugs I found this movement (F-470, F-471) are lifecycle management issues, not logic bugs. The _synced_status leak is the quietest kind of failure — correct behavior that accumulates silently over time. The pending jobs gap is an architectural oversight in a feature that was designed for responsiveness (don't reject during rate limits) but didn't account for persistence (what happens when the daemon restarts). Both are P2 — not urgent, but real for production use.

Five movements of adversarial testing, six modes, 387 tests. The bugs are in narrower crevices every movement. That's quality compounding.
