# Movement 4 — Prism Report (Pass 2)

**Role:** Code review, architectural analysis, cross-domain synthesis, blind spot detection
**Date:** 2026-04-05
**Pass:** Second review pass over M4 (first was b357c4c)

## Executive Summary

Second architectural review of M4, focused on uncommitted work and cross-domain blind spots. Reviewed the F-441 config strictness fix (`extra="forbid"` across 45+ models), validated it against the full example corpus, fixed two quality gate issues, and found three new findings. The strictness fix is architecturally sound and comprehensive for score config — but has a boundary gap (daemon config models not covered) and a documentation/implementation mismatch in the new `ValidationRule.sheet` field.

**Verdict:** M4's uncommitted work is solid. The F-441 fix is the right call. Three blind spots identified and filed.

---

## Work Completed

### 1. Quality Gate Fix — Bare MagicMock Drift (P2)

Two bare `MagicMock()` instances in `tests/test_top_error_ux.py:103,130` were causing the quality gate to fail (baseline 1519, actual 1521). These mocked `ProfilerConfig` for testing `mzt top` error paths.

**Fix:** Replaced with `MagicMock(spec=ProfilerConfig())` + `MagicMock(spec=Path)` for the path attributes. Updated baseline from 1519 to 1517.

**Evidence:**
```bash
$ python -m pytest tests/test_quality_gate.py::test_no_bare_magicmock -x --tb=short
1 passed in 1.50s
```

**Files modified:**
- `tests/test_top_error_ux.py:103,130` — spec'd mocks
- `tests/test_quality_gate.py:27` — baseline 1519→1517

### 2. Rosetta Score instrument_fallbacks Fix (P1)

The working tree added `instrument_fallbacks: [gemini-cli]` to `scores/the-rosetta-score.yaml`. This field doesn't exist on `JobConfig`. With `extra="forbid"`, the score fails validation:

```
REJECTED: 1 validation error for JobConfig
instrument_fallbacks
  Extra inputs are not permitted [type=extra_forbidden]
```

**Fix:** Commented out with note: `# Aspirational — field doesn't exist yet (F-441)`

**Evidence:**
```bash
$ python -c "...JobConfig.model_validate(data)..."
PASSED — 13 sheets, instrument=claude-code
```

### 3. Full Example Corpus Validation

Validated all 44 example scores against `extra="forbid"`. **43 pass, 1 fails:**

- **FAIL:** `examples/iterative-dev-loop-config.yaml` — has `spec_dir` and `cycles` at top level. This is NOT a score — it's a generator config for `scripts/generate-iterative-dev-loop.py`. Filed as F-432.

---

## Architectural Review

### F-441 Config Strictness — Multi-Perspective Analysis

The `extra="forbid"` change touches 8 source files adding `ConfigDict(extra="forbid")` to 45 config model classes across `backend.py`, `execution.py`, `instruments.py`, `learning.py`, `orchestration.py`, `spec.py`, and `workspace.py`. (The remaining models in `job.py` already had it.)

**Computational (Logic):** Correct. Pydantic's `extra="forbid"` is the right mechanism. Each model independently rejects unknown fields. The `total_sheets` backward compatibility is preserved by `strip_computed_fields()` at `job.py:325-333` which pops the computed field before Pydantic sees it. The `_sheet_to_condition()` validator converts the new `sheet` shorthand before `_check_type_specific_fields` runs. Validator ordering is correct.

**Scientific (Evidence):** All 54 tests in `test_m4_config_strictness_adversarial.py` pass. The `test_instrument_models.py` and `test_job_serialization.py` tests were correctly updated from "silently ignored" assertions to "raises ValidationError" assertions. 43/44 example scores validate (minus the generator config — not a score).

**Cultural (Context):** This directly implements the composer's directive. Score authors who typo a field name currently get silent success — Marianne drops the field and runs without it. This is worse than an error because the author believes their configuration is active. The change respects backward compatibility through explicit stripping of known computed fields (`total_sheets`).

**Experiential (Usage):** The UX is now correct. `mzt validate` with an unknown field produces a clear error. Journey's `_unknown_field_hints()` provides typo suggestions. The experience of "thought I configured something that was silently dropped" is eliminated.

**Meta (What's Not Being Asked):** Two boundary gaps exist — see findings below.

### Boundary Gap 1: Daemon Config Not Covered (F-431)

The `extra="forbid"` fix is comprehensive for score YAML (`core/config/`) but does NOT cover daemon config (`daemon/config.py` — 5 models) or profiler config (`daemon/profiler/models.py` — 4 models). These are user-edited files (`~/.marianne/conductor.yaml`). The same silent-drop bug exists there.

### Boundary Gap 2: ValidationRule.sheet Docstring Mismatch (F-430)

The new `ValidationRule.sheet` field at `execution.py:494-500` says "If both sheet and condition are set, the sheet filter takes precedence." The implementation at line 506 does the opposite: `if self.sheet is not None and self.condition is None:` — condition wins when both are set.

### The worktree-isolation.yaml Fix

The working tree removes `working_directory: /path/to/your/repo` from `examples/worktree-isolation.yaml`. This is correct — `working_directory` is NOT a field on `JobConfig` (it exists on `BackendConfig`, `ValidationRule`, and `PostSuccessHookConfig` but not at top level). With `extra="forbid"` this would fail. The fix adds a comment explaining the git repo is auto-detected.

### The total_sheets → total_items Migration in Tests

Six test files remove `total_sheets:` from inline YAML test data. This is correct cleanup — `total_sheets` is a computed property (`SheetConfig.total_sheets` property at `job.py:383`), not a settable field. The `strip_computed_fields()` validator strips it for backward compat in real scores, but test data should use the correct field (`total_items`).

### The asyncio.run() Migration

`test_m4_config_strictness_adversarial.py` replaces 6 instances of `asyncio.get_event_loop().run_until_complete()` with `asyncio.run()`. This is the correct modernization — `get_event_loop()` is deprecated since Python 3.10 and emits warnings in 3.12+.

---

## M4 Committed Work Summary

Reviewed all 30 M4 commits (a77aa35 through 6452f6c):

| Category | Commits | Assessment |
|----------|---------|------------|
| Bug fixes (#120, #122, #103, F-450) | 7 | All verified correct |
| Baton blockers (F-210, F-211) | 4 | Both resolved, Phase 1 unblocked |
| Safety/security (F-250, F-251, audits) | 4 | Clean |
| Documentation (Codex, Dash) | 4 | Accurate |
| Examples/demos (Spark, Blueprint) | 3 | Validate clean |
| Testing (Litmus, Breakpoint, Theorem) | 3 | 175 adversarial + 9 property-based |
| Metrics (Oracle, Atlas) | 2 | Documented |
| User journeys (Journey) | 2 | Strictness TDD, error hints |
| Verification (Axiom) | 1 | F-441 correctly identified |

**Mateship rate:** 39% — highest in project history.

---

## Findings Filed

| ID | Title | Severity | Status |
|----|-------|----------|--------|
| F-430 | ValidationRule.sheet precedence docstring contradicts implementation | P3 | Open |
| F-431 | DaemonConfig and ProfilerConfig missing extra='forbid' | P2 | Open |
| F-432 | examples/iterative-dev-loop-config.yaml breaks with extra='forbid' | P2 | Open |

---

## Test Suite Status

```
11,332 passed, 5 skipped, 164 warnings in 498.21s
mypy: clean
ruff: All checks passed!
```

Test count: 11,332 (up from 10,981 at M3 gate — +351 this movement).

One flaky test: `test_zombie_detection_and_recovery` fails in full-suite but passes in isolation (F-453, Ember M4 — cross-test mock contamination).

---

## The Geometry Problem (Continued)

Five movements. Five reviews. The same observation, evolved but unresolved.

M4 resolved the last two Phase 1 blockers (F-210, F-211). The baton is architecturally ready. 1,900+ tests verify it. Four independent methodologies agree the code is correct. Zero bugs found in M3-M4 baton code.

**What I see from the angle nobody's standing at:** The F-441 strictness fix is excellent for v1 quality. But it illuminates a deeper pattern. Marianne has been getting stricter about its *inputs* (config validation, unknown field rejection, schema enforcement) while remaining untested about its *outputs* (baton execution, actual multi-instrument routing, real job completion through the new path). Input strictness without output verification is half a contract.

The baton has 1,900 tests proving it handles events correctly. Zero tests proving it runs a real score. The integration cliff from M1 core memory hasn't moved. It's just gotten taller.

Phase 1 must start. Not next movement. This movement.

Down. Forward. Through.
