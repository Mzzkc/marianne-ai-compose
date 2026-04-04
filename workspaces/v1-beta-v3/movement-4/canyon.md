# Movement 4 — Canyon Report

**Musician:** Canyon (co-composer)
**Focus:** F-210 cross-sheet context — the P0 blocker for Phase 1 baton testing
**Directive:** D-020 (North, M3 strategic report)
**Date:** 2026-04-04

---

## Executive Summary

F-210 is resolved. The baton path now replicates the legacy runner's cross-sheet context pipeline. Templates referencing `{{ previous_outputs }}` and `{{ previous_files }}` render correctly under `use_baton: true`. This was the sole engineering blocker on the critical path. Phase 1 baton testing is unblocked.

---

## Primary Work: F-210 Cross-Sheet Context

### The Problem

The baton path was built to replace the legacy runner but never replicated its cross-sheet context pipeline (`src/mozart/execution/runner/context.py:171-221`). The legacy runner populates `SheetContext.previous_outputs` from CheckpointState and `previous_files` from workspace file patterns. The baton path had:

- `AttemptContext.previous_outputs` declared but never populated (`state.py:161`)
- No `previous_files` field on `AttemptContext` at all
- Zero `cross_sheet` references in the entire baton package (`grep -r 'cross_sheet' src/mozart/daemon/baton/` → 0 results before this fix)
- `PromptRenderer._build_context()` created `SheetContext` without any cross-sheet data

Impact: 24 of 34 example scores use `cross_sheet: auto_capture_stdout: true`. Every one of those scores would produce functionally different (worse) prompts under the baton. Confirmed independently by Weaver, Prism, Axiom, Adversary, and Ember across M3.

### The Fix

Architecture: adapter collects context from completed sheets at dispatch time, passes through AttemptContext to PromptRenderer.

**Files changed (5):**

1. **`src/mozart/daemon/baton/state.py:164-166`** — Added `previous_files: dict[str, str]` to `AttemptContext`. Now matches `SheetContext`'s interface. Both `previous_outputs` and `previous_files` flow from adapter → AttemptContext → PromptRenderer → SheetContext → template.

2. **`src/mozart/daemon/baton/adapter.py`** — Three additions:
   - `_job_cross_sheet: dict[str, CrossSheetConfig]` storage in `__init__`
   - `cross_sheet` parameter on both `register_job()` and `recover_job()`, with cleanup in `deregister_job()`
   - `_collect_cross_sheet_context(job_id, sheet_num)` — the main collection method. Reads completed sheets' `stdout_tail` from `SheetExecutionState.attempt_results` (baton's own state, not CheckpointState). Reads workspace files from `capture_files` glob patterns. Respects `lookback_sheets`, `max_output_chars`, truncation rules. Returns `(previous_outputs, previous_files)` tuple.
   - `_get_completed_stdout(state)` — static helper that walks `attempt_results` in reverse to find the last successful attempt's stdout.
   - Wired into `_dispatch_callback()` at the AttemptContext construction point (before musician spawn).

3. **`src/mozart/daemon/baton/prompt.py:146-195`** — `_build_context()` now accepts optional `AttemptContext` and copies `previous_outputs`/`previous_files` to `SheetContext`. `render()` passes `attempt_context` through.

4. **`src/mozart/daemon/manager.py:1817,1962`** — Both `_run_via_baton` and `_resume_via_baton` now pass `config.cross_sheet` to the adapter.

5. **`tests/test_f210_cross_sheet_baton.py`** — 21 TDD tests across 4 test classes:
   - `TestAttemptContextPreviousFiles` (3 tests): field exists, populated, backward-compatible
   - `TestAdapterCrossSheetStorage` (4 tests): register, recover, deregister, no-config
   - `TestCollectCrossSheetContext` (10 tests): auto_capture, lookback, truncation, skipped/failed exclusion, capture_files, empty/nonexistent job
   - `TestPromptRendererCrossSheetContext` (4 tests): outputs in template, files in template, empty context, _build_context population

### Design Decision: Baton State, Not CheckpointState

The adapter reads stdout from `SheetExecutionState.attempt_results` — the baton's own in-memory state — rather than from CheckpointState. This is deliberate:

- The baton is the authority during execution (design invariant)
- CheckpointState sync may lag behind the baton (F-211 covers this gap)
- No additional state sync needed — the data is already there
- On recovery, completed sheets don't have attempt_results (they're not persisted), so cross-sheet context on the first dispatch after recovery uses empty outputs. This matches the legacy runner behavior on resume (CheckpointState.stdout_tail may also be empty for sheets completed in a previous session).

---

## Mateship Observations

Foundation completed the mateship pipeline for this work — committing my adapter changes alongside their F-211 checkpoint sync fix in commit `5af7dbc`. The PromptRenderer changes and manager wiring were auto-applied by the linter. This is the mateship pipeline operating as designed: I built the core logic, Foundation completed the integration.

The `_synced_status` dedup cache that appeared in the adapter was Foundation's F-211 work, not mine. Both fixes (F-210 and F-211) landed in the same commit because they both modified adapter.py. Clean separation of concerns — no conflicts.

---

## Secondary Finding: F-340 (Quality Gate Baseline)

`test_quality_gate.py::test_all_tests_have_assertions` fails because `ASSERTION_LESS_TEST_BASELINE` is 116 but 122 test functions lack assertions. The 6 offenders are in `test_runner_coverage_gaps.py` (3) and `test_runner_execution_coverage.py` (2). Not my code, not a product bug. Filed as F-340 (P3).

---

## Quality Gates

| Gate | Status | Evidence |
|------|--------|----------|
| pytest (affected files) | **GREEN** | 194 passed, 0 failed (adapter, manager, prompt, renderer, F-210 tests) |
| pytest (F-210 tests) | **GREEN** | 21 passed in 0.58s |
| mypy | **GREEN** | Clean, no errors |
| ruff | **GREEN** | All checks passed |

The full test suite has a pre-existing meta-test failure (F-340) unrelated to this work.

---

## Critical Path Update

```
F-210 fix ✓ → Phase 1 baton test (D-021, --conductor-clone) → fix issues → flip use_baton default → demo → release
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
              THIS IS NEXT. The wires are connected. Someone must turn it on.
```

The new composer directive (M4, baton transition plan) lays out the three phases clearly. F-210 was the Phase 1 prerequisite. It's done. D-021 (Foundation, Phase 1 testing) is now unblocked.

---

## Commit

`748335f` — movement 4: [Canyon] F-210 cross-sheet context wired into baton — P0 blocker cleared

---

## Experiential Note

This was a cairn-placing session. The fix touches 5 files across 3 architectural layers (state model, adapter bridge, prompt renderer), but the actual insight was small: the data was already there in `SheetExecutionState.attempt_results`. Nobody needed to add new state storage or new event types. The baton already tracked everything — it just wasn't passing the data through the one place that needed it.

The pattern keeps repeating in this project: the infrastructure is extraordinary, but the last 20 lines of wiring are always what's missing. Not the algorithm, not the architecture — the plumbing. And plumbing is exactly what co-composers should be doing. I hold the whole picture. I see where the pipes don't connect. And then I connect them.

Down. Forward. Through.
