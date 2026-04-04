# Movement 4 — Breakpoint Report

**Role:** Adversarial Tester
**Date:** 2026-04-04
**Commit:** (pending)

## Summary

57 adversarial tests across 10 attack surfaces targeting all major M4 code changes. One architectural parity finding (F-202). Zero code-level bugs. Mateship pickup of Litmus's uncommitted 7 new litmus tests (651 lines).

## Work Completed

### 1. M4 Adversarial Test Suite (57 tests)

**File:** `tests/test_m4_adversarial_breakpoint.py`

Ten test classes organized by attack surface:

| Class | Tests | Target | Bugs |
|-------|-------|--------|------|
| `TestAutoFreshToleranceBoundary` | 8 | `_should_auto_fresh()` tolerance edge, negative/future timestamps, symlink, PermissionError | 0 |
| `TestPendingJobWorkspaceNone` | 1 | `_queue_pending_job()` with workspace=None orphan | 0 |
| `TestPendingJobCancellation` | 1 | Cancel removes from `_pending_jobs` dict | 0 |
| `TestBackpressureReassertionDuringPendingStart` | 1 | `should_accept_job()` alternation stops iteration | 0 |
| `TestCrossSheetSkippedWithStdout` | 2 | SKIPPED sheet with/without stdout uses `[SKIPPED]` | 0 |
| `TestCrossSheetFailedSheetBehavior` | 3 | FAILED/IN_PROGRESS sheets with stdout are included | 0 |
| `TestCrossSheetMaxCharsEdgeCases` | 3 | Exact boundary, one-over, zero-rejected-by-validation | 0 |
| `TestCrossSheetLookbackEdgeCases` | 4 | lookback=1, lookback>total, lookback=0, sheet=1 | 0 |
| `TestCrossSheetAllSkipped` | 2 | All upstream SKIPPED, no state entries | 0 |
| `TestCrossSheetCredentialInStdout` | 1 | SKIPPED never leaks credential from stdout | 0 |
| `TestMethodNotFoundErrorCodeMapping` | 5 | Error code round-trip, standard JSON-RPC code, data field, exception map | 0 |
| `TestDetectLayerMethodNotFound` | 1 | Restart guidance in re-raised message | 0 |
| `TestRedactCredentialsDefensiveOr` | 7 | `or content` fallback edge cases, multiple creds, truncation boundary | 0 |
| `TestCaptureFilesStaleDetection` | 2 | mtime == start (not stale), mtime < start (stale) | 0 |
| `TestCaptureFilesBinaryContent` | 1 | Binary file → UnicodeDecodeError caught | 0 |
| `TestCaptureFilesPatternExpansion` | 2 | `{{ var }}` and `{{var}}` formats, mixed | 0 |
| `TestSheetContextToDict` | 2 | skipped_upstream in dict after population | 0 |
| `TestBatonLegacySkippedParity` | 2 | SKIPPED identical, FAILED differs (F-202) | 1 finding |
| `TestRejectionReasonEdgeCases` | 6 | None/rate_limit/resource/degraded/85%-boundary/86% | 0 |
| `TestMethodNotFoundVsDaemonErrorCascade` | 2 | Catch order, unknown code maps to DaemonError | 0 |

**All 57 tests pass.** mypy clean. ruff clean.

### 2. Finding F-202 — Baton/Legacy Parity Gap

**Severity:** P3 (low)
**Error class:** Baton/legacy-runner behavioral divergence (same class as F-251)

**Description:** The legacy runner's `_populate_cross_sheet_context()` (`context.py:206-214`) includes stdout from FAILED and IN_PROGRESS sheets in `previous_outputs`. The baton adapter's `_collect_cross_sheet_context()` (`adapter.py:738`) explicitly filters `if prev_state.status != BatonSheetStatus.COMPLETED: continue`, excluding all non-COMPLETED non-SKIPPED sheets.

**Impact:** When the baton becomes default (Phase 2), scores relying on failed sheet output in downstream prompts will get different behavior. The baton's stricter filtering may be preferable (failed output could be misleading), but the difference should be a conscious design decision.

**Evidence:** `test_m4_adversarial_breakpoint.py::TestBatonLegacySkippedParity::test_both_paths_skip_non_completed_non_skipped`

### 3. Mateship — Litmus Uncommitted Tests

**File:** `tests/test_litmus_intelligence.py` (modified, 651 lines added)

Picked up Litmus's uncommitted work: 7 new M4 litmus tests covering:
- Test 32: F-210 cross-sheet context in baton prompts
- Test 33: #120 SKIPPED upstream visibility
- Test 34: #103 auto-fresh detection
- Test 35: F-110 backpressure rejection intelligence
- Test 36: F-250 cross-sheet credential redaction
- Test 37: F-450 MethodNotFoundError differentiation
- Test 38: D-024 cost JSON extraction vs char estimation

All 118 litmus tests pass.

## Verification Evidence

```
$ python -m pytest tests/test_m4_adversarial_breakpoint.py -v --tb=short
57 passed in 0.57s

$ python -m pytest tests/test_litmus_intelligence.py --tb=short
118 passed in 0.62s

$ python -m mypy src/ --no-error-summary
(clean)

$ python -m ruff check src/
All checks passed!
```

Full test suite: running in background (11,000+ tests).

## Analysis

### Bug Surface Evolution

| Movement | Tests | Bugs Found | Bug Class |
|----------|-------|------------|-----------|
| M0 (Cycle 1) | 40 specs | — | Design-level |
| M1 | 129 | 3 | Core state machine |
| M2 | 122 | 2 | Integration seams |
| M3 | 258 | 2 | Utility function fallthrough (F-200/F-201) |
| M4 | 57 | 0 code bugs, 1 parity finding | Architectural divergence |
| **Total** | **606** | **7 bugs + 1 finding** | |

The progression is clear: each layer of hardening pushes bugs to a narrower class. M1 found state machine bugs. M2 found integration seam bugs. M3 found utility function bugs. M4 found architectural parity bugs. The code-level bug surface is effectively exhausted for unit-level adversarial testing. The remaining risk is production integration — running real sheets through the baton.

### Key Observations

1. **The `or content` pattern in credential redaction is safe.** Both `context.py:296` and `adapter.py:785` use `redact_credentials(content) or content`. The `or` triggers only when `redact_credentials` returns a falsy value (None for None input, empty string for empty input). In both cases, the original content has nothing to redact, so falling back to it is correct. Tested with 7 edge cases including credential-only content, multiple credentials, and truncation boundary.

2. **Pydantic guards the config boundary.** The `max_output_chars=0` test revealed that `CrossSheetConfig` enforces `gt=0` via Pydantic validation. The degenerate case I expected to test can't happen because the model rejects it at construction. This is good — the validation layer prevents footguns before they reach the code.

3. **The auto-fresh tolerance boundary is correctly exclusive.** `mtime > completed_at + tolerance` uses strict greater-than, not greater-than-or-equal. At the exact boundary, auto-fresh does NOT trigger. This is the right behavior — filesystem timestamp granularity means the boundary case is ambiguous, so we err on the side of not triggering.

4. **The MethodNotFoundError catch cascade in detect.py is correctly ordered.** `isinstance(e, MethodNotFoundError)` is checked BEFORE `isinstance(e, DaemonError)`. If reversed, MethodNotFoundError would be silently swallowed as "daemon not running" instead of re-raised with restart guidance. This is subtle and critical — the exception hierarchy order in the catch cascade is load-bearing.

## Experiential Notes

Five movements. Five adversarial passes. The codebase has hardened to the point where the adversarial pass finds architectural design questions rather than code bugs. F-202 is not "this crashes" — it's "do these two paths agree about what context means?" That's a fundamentally different class of finding, and it signals maturity.

The orchestra's institutional hardening is compounding. When I tested the `redact_credentials` defensive pattern, I expected to find an edge case where unredacted content leaks through. Instead, I found 7 tests worth of evidence that the pattern is correct. The code isn't just bug-free — it's defensively coded against the specific attack vectors I would have exploited.

The remaining frontier is production integration. 1,500+ baton tests, zero real sheets executed. The gap between "does the test pass?" and "does the product work?" is where the next class of bugs lives. Phase 1 baton testing is the path forward.
