# Movement 4 — Axiom Report (Pass 2)
**Role:** Invariant verification, dependency analysis, edge case detection
**Date:** 2026-04-05

## Executive Summary

Second pass in M4. Mateship pickup of uncommitted F-441 completion: `extra='forbid'` added to all 51 config models (44 were uncommitted from previous musician's work). Fixed pre-existing dashboard E2E test failure (AsyncMock). Fixed YAML fixture incompatibilities caused by `extra='forbid'`. Closed 2 GitHub issues (#128, #93) with evidence. Verified 3 recent M4 commits (Journey, Sentinel, Litmus). Quality gates: mypy clean, ruff clean, 4,300+ tests verified across targeted groups, 0 failures.

---

## F-441 Verification and Completion

### Coverage Audit

My first pass identified 37 config models missing `extra='forbid'`. A more thorough audit found **51 total BaseModel classes** across 8 config modules. Journey committed the `job.py` models (6 classes) in `7d86035`. The remaining **45 models** across `backend.py`, `execution.py`, `instruments.py`, `learning.py`, `orchestration.py`, `spec.py`, and `workspace.py` were modified in the working tree but never committed.

**Verification result:** All 51 models now have `extra='forbid'`:

| Module | Models | Status |
|--------|--------|--------|
| `job.py` | 6 | Committed (Journey 7d86035) |
| `backend.py` | 6 | Uncommitted → mateship pickup |
| `execution.py` | 9 | Uncommitted → mateship pickup |
| `instruments.py` | 9 | Uncommitted → mateship pickup |
| `learning.py` | 8 | Uncommitted → mateship pickup |
| `orchestration.py` | 5 | Uncommitted → mateship pickup |
| `spec.py` | 2 | Uncommitted → mateship pickup |
| `workspace.py` | 6 | Uncommitted → mateship pickup |

### Empirical Verification

Ran 4 empirical tests to confirm the fix works end-to-end:

1. **Unknown top-level field:** `bogus_field: true` → `ValidationError: extra_forbidden` ✓
2. **Backward compat:** `total_sheets: 1` → silently stripped by `strip_computed_fields()` validator ✓
3. **Valid config:** Normal config with all valid fields → accepted ✓
4. **Nested model strictness:** `sheet: { bogus_nested: true }` → `ValidationError: extra_forbidden` ✓

### Example Score Validation

All 37 example scores validated against strict models:
- **36/37 pass** (including all 6 rosetta examples)
- **1 failure:** `examples/iterative-dev-loop-config.yaml` — this is a generator config for `scripts/generate-iterative-dev-loop.py`, not a Marianne score. Uses `spec_dir`, `cycles` (non-score fields). Pre-existing issue, not caused by F-441.

---

## Mateship: Dashboard E2E Fix

**Pre-existing bug:** `tests/test_dashboard_e2e.py` had two failing tests (`test_complete_job_lifecycle`, `test_zombie_detection_and_recovery`) due to `Mock()` used for subprocess instead of `AsyncMock`. The `process.wait()` method was `await`ed but Mock can't be used in await expressions.

**Fix:** Added `mock_process.wait = AsyncMock(return_value=0)` to all 4 process mock instances in the file.

**Additional fix:** `sample_yaml_config` fixture had `timeout_seconds: 300` and `retries: 2` as top-level fields — now rejected by `extra='forbid'`. Changed to valid equivalents: `retry: { max_retries: 2 }` and `stale_detection: { idle_timeout_seconds: 300 }`.

**Result:** 9/9 dashboard E2E tests pass.

### Test Fix Analysis

The working tree contained test fixes for F-441 compatibility across 7 test files:

| Test File | Change | Reason |
|-----------|--------|--------|
| `test_dashboard_routes_extended.py` | `total_sheets` → `total_items` | `total_sheets` is computed, not a config field |
| `test_instrument_models.py` | Expect `extra_forbidden` | Was asserting "ignored by default" |
| `test_job_serialization.py` | Expect `extra_forbidden` for `path` and top-level `prelude` | Were asserting silent drop |
| `test_m3_cli_adversarial_breakpoint.py` | Remove `total_sheets`, expect exit_code 2 for unknown fields | `total_sheets` stripped, extra fields now errors |
| `test_m4_config_strictness_adversarial.py` | `asyncio.get_event_loop().run_until_complete()` → `asyncio.run()` | Deprecation fix |
| `test_schema_error_hints.py` | Remove `total_sheets` | Stripped by backward compat |
| `test_validate_ux_journeys.py` | Remove `total_sheets` | Stripped by backward compat |

All changes are correct — the tests were asserting the old `extra='ignore'` behavior, which is now replaced by the composer's `extra='forbid'` directive.

---

## Mateship: Quality Improvements

Picked up two additional uncommitted quality fixes:

1. **`test_top_error_ux.py`:** Replaced bare `MagicMock()` with `MagicMock(spec=ProfilerConfig())` for more precise mocking. Reduces bare MagicMock count.
2. **`test_quality_gate.py`:** Baseline adjustment 1519 → 1517 to reflect the quality improvement above.

---

## Recent Commit Verification

### Journey (7d86035): Schema Error Hints
**Verified:** `_unknown_field_hints()` at `validate.py:324` correctly:
- Extracts unknown field names from Pydantic error format via regex
- Matches against `_KNOWN_TYPOS` dict (11 entries for common mistakes)
- Returns "did you mean X?" suggestions
- Falls back to generic guidance when no typo match found

**Edge case noted:** The regex `\w[\w.]*` doesn't match hyphenated field names (e.g., `retry-count`). Low-risk: YAML users typically use underscore syntax. Falls back to generic message.

### Journey (6452f6c): total_sheets Backward Compat
**Verified:** `strip_computed_fields()` model_validator at `job.py:332` correctly strips `total_sheets` before `extra='forbid'` validation. Backward compatible — existing scores using `total_sheets` continue to work (field is silently stripped, not rejected).

### Sentinel (a39704a): Security Audit Pass 2
**Verified:** Report covers 6 new commits (Theorem, Journey, Prism, Axiom, Litmus). F-271 (PluginCliBackend MCP gap) independently confirmed. F-441 fix verified with 54 adversarial tests.

### Litmus (812fb69): 18 New Litmus Tests
**Verified:** 136 total litmus tests pass. F-271 finding (P1: PluginCliBackend ignores `mcp_config_flag`, causing 80 child processes in production) is a significant discovery.

---

## GitHub Issues Closed

| Issue | Action | Evidence |
|-------|--------|----------|
| #128 | **Closed** | Fix in 919125e, 5 edge cases verified M4 pass 1 |
| #93 | **Closed** | Fix in b4c660b, 4 edge cases verified M4 pass 1 |
| #122 | Already closed | Verified correct M4 pass 1 |
| #120 | Already closed | Verified correct M4 pass 1 |
| #103 | Already closed | Verified correct M4 pass 1 |

---

## Quality Gates

| Check | Result |
|-------|--------|
| mypy | ✓ Clean (0 errors) |
| ruff | ✓ Clean (all checks passed) |
| pytest (targeted) | ✓ 4,300+ tests across all affected areas, 0 failures |
| Config/job/instrument/dashboard | 792 passed |
| Validation/CLI | 860 passed |
| Baton/runner/execution | 2,237 passed |
| Daemon/conductor | 49 passed |
| Litmus | 136 passed |
| F-441-specific | 242 passed |
| Dashboard E2E | 9 passed |

---

## F-441 Status Update

F-441 is now **functionally resolved** in the working tree. The complete fix includes:
1. `extra='forbid'` on all 51 config models ✓
2. `total_sheets` backward compatibility (Journey 6452f6c) ✓
3. "Did you mean X?" suggestions for common typos (Journey 7d86035) ✓
4. Test updates for new strict behavior ✓
5. Dashboard E2E test fix for compatibility ✓

**Remaining for full closure:** Update FINDINGS.md F-441 status, close GitHub issue #156. The fix implementation is complete and verified.

---

## Evidence Log

**Git commits analyzed:**
- `7d86035` Journey: schema error hints + extra='forbid' on job.py
- `6452f6c` Journey: total_sheets backward compat
- `a39704a` Sentinel: security audit pass 2
- `812fb69` Litmus: 18 new litmus tests

**Files verified:**
- All 8 `src/marianne/core/config/*.py` modules — 51 models, 51 have `extra='forbid'`
- `src/marianne/cli/commands/validate.py:309-353` — `_KNOWN_TYPOS` + `_unknown_field_hints()`
- `src/marianne/core/config/job.py:332-333` — `strip_computed_fields()` validator

**Commands run:**
```bash
python3 -c "..." # AST parser verifying all 51 models have extra='forbid'
python3 -c "..." # Empirical F-441 tests (4 cases, all pass)
for f in examples/*.yaml; do python3 -c "..." done  # 37 example scores validated
python -m mypy src/ --no-error-summary  # Clean
python -m ruff check src/  # Clean
python -m pytest tests/test_*config*.py tests/test_*job*.py ...  # 4,300+ pass
gh issue close 128 --repo Mzzkc/marianne-ai-compose --comment "..."
gh issue close 93 --repo Mzzkc/marianne-ai-compose --comment "..."
```

---

## Mateship

Picked up 7 uncommitted work items:
1. `extra='forbid'` additions to 7 config modules (45 models)
2. 6 test file compatibility fixes
3. Dashboard E2E AsyncMock fix
4. Dashboard E2E YAML fixture fix
5. Quality gate baseline update
6. `test_top_error_ux.py` mock precision improvement
7. `test_m4_config_strictness_adversarial.py` asyncio deprecation fix

---

## Capacity Notes

**Pass 2 allocation:**
- F-441 verification + mateship pickup: 40%
- Dashboard E2E fix: 15%
- Commit verification (3 commits): 15%
- GitHub issue closure: 5%
- Meditation: 10%
- Report + memory: 15%

All claimed work completed. No deferred items.

---

## Reflections

This pass was about completion, not discovery. The F-441 fix was mostly done — someone (likely Journey on a second pass) did the implementation work across all config models and updated the tests. What remained was the unglamorous mateship work: verifying the changes are correct, fixing the dashboard tests that broke, running the quality gates, and committing it all.

The dashboard E2E failure was instructive. It had two independent causes: a pre-existing mock type error (Mock vs AsyncMock for process.wait()) and a new incompatibility from `extra='forbid'` (fixture had invalid fields). Two bugs in one test, from different eras. The pre-existing one had been there for who knows how many movements, invisible because the test wasn't running. The new one was an inevitable consequence of making validation strict. Together they illustrate the gap between "tests exist" and "tests work" — having a test file is not the same as having coverage.

The F-441 arc across M4 is now: I found the bug → filed as P0 → composer made it a directive → Journey implemented it → I verified and completed it. Four musicians, zero meetings. The mateship pipeline at work.
