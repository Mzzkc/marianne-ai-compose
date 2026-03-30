# Movement 3 — Circuit Report

**Musician:** Circuit
**Movement:** 3
**Date:** 2026-03-30
**Focus:** Observability fixes — closing the gap between "system works" and "system communicates"

---

## Summary

Three observability bugs fixed, all sharing the same root shape: the system does the right thing internally but presents wrong information to the user. F-068 (completed timestamp on running jobs), F-069/F-092 (V101 false positive on Jinja2 variables), and F-048 (cost shows $0.00 when limits disabled). 11 TDD tests across three fixes. All quality gates green.

The P0 production bugs (F-075/F-076/F-077) that I planned to fix were already resolved by Forge and Maverick — mateship working as designed. F-062/F-063 also already resolved. I shifted to the unfixed observability gaps instead.

---

## What I Built

### F-068: Completed Timestamp Hidden for Non-Terminal Jobs

**File:** `src/mozart/cli/commands/status.py:1487`

The `Completed:` timestamp was shown unconditionally when `job.completed_at` was set. But `completed_at` tracks individual sheet completion, not job completion. A user monitoring a RUNNING 706-sheet score sees "Completed: 2026-03-29 18:28:10 UTC" and momentarily thinks the score finished.

**Fix:** Added terminal status guard: `Completed:` only shows when `job.status in {COMPLETED, FAILED, CANCELLED}`. The `Updated:` timestamp already covers the information need for running jobs.

**Tests:** 4 TDD tests in `tests/test_status_display_bugs.py` (TestF068CompletedTimestamp):
1. RUNNING job hides completed timestamp
2. COMPLETED job shows completed timestamp
3. FAILED job shows completed timestamp
4. PAUSED job hides completed timestamp

### F-069/F-092: V101 False Positive on Template-Declared Variables

**File:** `src/mozart/validation/checks/jinja.py:250-274`

`jinja2_meta.find_undeclared_variables()` has a known limitation: it doesn't properly track variables declared inside conditional branches (`{% if %}`/`{% elif %}`). In hello.yaml, `char` is declared via `{% for id, char in characters.items() %}` in one branch and `{% set char = ... %}` in another. The meta module sees it as undeclared because the scoping analysis doesn't cross branch boundaries.

**Root cause verified empirically:**
```python
# This produces {'char'} — false positive
template = """{% if x %}{% for id, char in items %}{{ char }}{% endfor %}
{% elif y %}{% set char = z %}{{ char }}{% endif %}"""
jinja2_meta.find_undeclared_variables(ast)  # → {'char', ...}
```

**Fix:** Added `_extract_template_declared_vars()` static method that walks the Jinja2 AST for `Assign` and `For` nodes, extracting variable names including tuple unpacking targets (`{% for id, char in ... %}` → `{id, char}`). These template-declared variables are excluded from the undeclared set before generating V101 warnings.

**Tests:** 5 TDD tests in `tests/test_status_display_bugs.py` (TestF069V101FalsePositive):
1. `{% for id, char %}` variables not flagged
2. `{% set char %}` variable not flagged
3. Conditional branch `{% set %}` not flagged (the specific bug)
4. Truly undefined variables still flagged
5. hello.yaml produces zero V101 warnings (integration)

### F-048: Cost Tracking When Limits Disabled

**File:** `src/mozart/execution/runner/sheet.py:2432-2437`

This was the deepest fix. `_enforce_cost_limits()` gated BOTH tracking AND enforcement behind `cost_limits.enabled`. When cost limits are off (the default — `CostLimitConfig.enabled` defaults to `false`), `_track_cost()` was never called. Every job run without explicit cost limits showed `$0.00` in `mozart status` — months of false data.

**Root cause:**
```python
# BEFORE (broken):
async def _enforce_cost_limits(self, result, sheet_state, state, sheet_num):
    if not self.config.cost_limits.enabled:
        return  # ← skips _track_cost() entirely!
    await self._track_cost(result, sheet_state, state)
    # ... enforcement ...
```

**Fix:** Moved `_track_cost()` before the gate. Costs are always recorded for observability. Only enforcement is gated.

```python
# AFTER (fixed):
async def _enforce_cost_limits(self, result, sheet_state, state, sheet_num):
    await self._track_cost(result, sheet_state, state)  # ← always track
    if not self.config.cost_limits.enabled:
        return  # ← only skip enforcement
    # ... enforcement ...
```

**Tests:** 2 TDD tests in `tests/test_status_display_bugs.py` (TestF048CostTrackingWithoutLimits):
1. Structural: `_track_cost` call appears before `cost_limits.enabled` check in source
2. Integration: `_track_cost()` populates state with token counts even when limits disabled

---

## Quality Gates

| Check | Result | Evidence |
|-------|--------|----------|
| pytest (my tests) | PASS | 11/11 tests pass |
| pytest (validation suite) | PASS | All validation/jinja tests pass, no regressions |
| pytest (baton adapter) | PASS | 47/47 tests pass |
| mypy (full src/) | PASS | Zero errors |
| ruff (full src/) | PASS | All checks passed |

---

## Mateship

- **F-075/F-076/F-077:** Arrived intending to fix these P0 production bugs. Found them already resolved by Forge (F-075, lifecycle.py terminal guard) and Maverick (F-076, rate limit check before validations; F-077, hook restoration). Verified the fixes are committed and shifted focus.
- **F-062/F-063:** Also already resolved. Verified deregister_job cleanup and process_exited record_attempt contract are correct in current code.
- **F-092:** Marked as resolved — same fix as F-069 (V101 false positive).

---

## Step 29 Analysis (Deferred Investigation)

I read the adapter code and Canyon's wiring analysis. Step 29 (restart recovery) requires:

1. **Load CheckpointState from SQLite** — this exists via `StateBackend.load()`
2. **Map checkpoint statuses to baton statuses** — `checkpoint_to_baton_status()` exists in adapter.py
3. **Register resumed sheets with the baton** — `BatonCore.register_job()` handles this
4. **Reconstruct timer state** — the tricky part. Active retries, rate limit timers, and escalation timers need reconstruction from checkpoint state. The baton's timer wheel is empty on restart.
5. **Handle in-flight sheets** — sheets in DISPATCHED/RUNNING status at crash time need to be treated as crashed (no musician process exists).

The adapter has the pieces. The remaining work is ~200 lines of glue: a `recover_job()` method that loads state, maps it, registers with baton, and handles the edge cases (items 4-5).

---

## Observations

### The Shape of the Three Bugs

All three bugs share a pattern: internal correctness with external miscommunication.

- **F-068:** The system correctly tracks job status as RUNNING, but displays a `Completed:` timestamp that contradicts it.
- **F-069:** The system correctly resolves Jinja2 variables at render time, but the validator doesn't know that.
- **F-048:** The system correctly enforces (or doesn't enforce) cost limits, but stops tracking costs entirely when limits are off.

These are observability failures. The system works but can't explain itself to the user. This is the same gap the quality gate report identified: 786 baton tests passing while three production bugs hid in the runner. The system passes its own tests but fails the user's expectations.

### Cost Tracking: Architecture vs Implementation

The F-048 fix is one line of reordering, but it reveals a design principle violation. The `_enforce_cost_limits()` method bundles two distinct concerns: observation (tracking) and action (enforcement). When you gate both behind a single flag, disabling the action also disables the observation. The fix is trivial, but the lesson isn't: observability should never be gated behind a feature flag. You can turn off enforcement. You can never turn off visibility.

---

## Commit

Files changed:
- `src/mozart/cli/commands/status.py` — F-068 terminal status guard
- `src/mozart/validation/checks/jinja.py` — F-069 AST-based template variable extraction
- `src/mozart/execution/runner/sheet.py` — F-048 cost tracking reordering
- `tests/test_status_display_bugs.py` — 11 TDD tests (new file)
- `workspaces/v1-beta-v3/FINDINGS.md` — F-068/F-069/F-048/F-092 marked resolved
- `workspaces/v1-beta-v3/TASKS.md` — 3 tasks marked done
- `workspaces/v1-beta-v3/memory/collective.md` — status update
- `workspaces/v1-beta-v3/memory/circuit.md` — personal memory update

Down. Forward. Through.
