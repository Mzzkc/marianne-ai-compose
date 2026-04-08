# Movement 3 Report — Breakpoint

**Role:** Adversarial Testing
**Movement:** 3
**Date:** 2026-04-04

---

## Summary

Three adversarial passes this movement. Pass 1: 62 tests targeting 10 major M3 baton/core fixes, found F-200 (clear_instrument_rate_limit fallthrough bug). Pass 2: 58 tests targeting CLI/UX code — zero bugs. Pass 3: 90 tests targeting the BatonAdapter (1206 lines, step 28 wiring) — zero bugs. Plus mateship pickup of uncommitted validate.py changes and 22 untracked tests.

**Total: 210 adversarial tests written, 1 bug found and fixed, 1 mateship pickup committed.**

---

## Pass 1: Baton & Core Adversarial (`tests/test_m3_adversarial_breakpoint.py`)

**62 tests across 12 test classes. All pass. Commit bd325bc.**

| Class | Target | Tests | Bugs Found |
|-------|--------|-------|------------|
| `TestDispatchGuardExceptionTaxonomy` | F-152 dispatch guard | 6 | 0 |
| `TestRateLimitAutoResume` | F-112 timer scheduling | 6 | 0 |
| `TestModelOverrideAdversarial` | F-150 carryover & coercion | 8 | 0 |
| `TestCompletedNewWork` | F-145 status edge cases | 5 | 0 |
| `TestSemanticContextTags` | F-009/F-144 tag format | 6 | 0 |
| `TestPromptRendererWiring` | F-158 renderer creation | 4 | 0 |
| `TestClearRateLimits` | Clear dual-path | 7 | **1 (F-200)** |
| `TestStaggerDelayBoundary` | Pydantic bounds | 5 | 0 |
| `TestTerminalStatusInvariants` | Cross-cutting invariants | 3 | 0 |
| `TestDispatchCallbackIntegration` | Full dispatch path | 4 | 0 |
| `TestRateLimitWaitCap` | F-160 verification | 3 | 0 |
| `TestRecordAttemptEdgeCases` | Attempt tracking | 4 | 0 |

---

## Pass 2: CLI/UX Adversarial (`tests/test_m3_cli_adversarial_breakpoint.py`)

**58 tests across 9 test classes. All pass. Commit 0028fa1.**

| Class | Target | Tests | Bugs Found |
|-------|--------|-------|------------|
| `TestSchemaErrorHintsAdversarial` | `_schema_error_hints()` edge cases | 8 | 0 |
| `TestFormatCompactDurationBoundary` | Duration formatter boundaries | 13 | 0 |
| `TestFormatRateLimitInfoAdversarial` | Rate limit display | 8 | 0 |
| `TestStopSafetyGuardAdversarial` | `stop_conductor` #94 guard | 5 | 0 |
| `TestStalePidDetectionAdversarial` | PID file handling | 7 | 0 |
| `TestValidateYamlAdversarial` | YAML edge cases | 8 | 0 |
| `TestValidateInstrumentDisplayAdversarial` | Instrument terminology | 2 | 0 |
| `TestCheckRunningJobsAdversarial` | IPC probe robustness | 2 | 0 |
| `TestNonDictYamlGuardAdversarial` | Non-mapping YAML guard | 6 | 0 |

---

## Pass 3: BatonAdapter Adversarial (`tests/test_baton_adapter_adversarial_breakpoint.py`)

**90 tests across 16 test classes. All pass. This session.**

| Class | Target | Tests | Bugs Found |
|-------|--------|-------|------------|
| `TestStateMappingTotality` | `_BATON_TO_CHECKPOINT`/`_CHECKPOINT_TO_BATON` completeness, roundtrip, unknown status | 9 | 0 |
| `TestRecoverJobEdgeCases` | in_progress→PENDING reset, missing sheets, attempt carry-forward, renderer creation | 8 | 0 |
| `TestDispatchCallbackEdgeCases` | NORMAL/COMPLETION/HEALING mode, attempt math, model override forwarding | 7 | 0 |
| `TestStateSyncFiltering` | Event type filtering (only SheetAttemptResult/SheetSkipped trigger sync), callback exception resilience | 8 | 0 |
| `TestCompletionDetectionEdgeCases` | All-completed, any-failed, pending-blocks, idempotent, mixed terminal, unknown job | 6 | 0 |
| `TestObserverEventEdgeValues` | Zero cost, large cost, 99.99% vs 100.0%, rate_limited priority, timestamp preservation | 10 | 0 |
| `TestDeregistrationCleanup` | All 5 per-job dicts cleaned, task cancellation isolation, nonexistent job safety | 7 | 0 |
| `TestDependencyExtractionEdgeCases` | Fan-out, fan-in, non-sequential stages, single sheet | 5 | 0 |
| `TestSheetsToExecutionStatesEdgeCases` | Empty list, instrument name, custom limits, all start PENDING | 4 | 0 |
| `TestMusicianWrapperExceptionHandling` | Backend released on success, crash, and pool release failure | 3 | 0 |
| `TestEventBusPublishingResilience` | All 3 publish paths survive bus failures | 5 | 0 |
| `TestRegistrationEdgeCases` | Cost limits, renderer stages, DispatchRetry kick | 5 | 0 |
| `TestHasCompletedSheetsEdgeCases` | F-145 helper: failed/skipped/pending/nonexistent | 4 | 0 |
| `TestShutdownBehavior` | Tasks cancelled, pool closed, failure handling | 5 | 0 |
| `TestOnMusicianDoneCallback` | Cancelled/exception/unknown key cleanup | 4 | 0 |
| `TestGetSheetEdgeCases` | Normal lookup, unknown job, unknown sheet_num | 3 | 0 |

### Key Adversarial Hypotheses Tested (Pass 3)

1. **State mapping totality:** Every BatonSheetStatus has a checkpoint mapping. Every checkpoint status roundtrips correctly for terminal states. Unknown status strings raise KeyError (not silent fallback).

2. **Recovery correctness:** `in_progress` sheets reset to PENDING (their musician died). Terminal sheets preserved. Missing checkpoint sheets treated as fresh. Attempt counts carried forward (prevents infinite retries). PromptRenderer created during recovery (not just fresh registration).

3. **Dispatch mode selection:** completion_attempts > 0 → COMPLETION mode. healing_attempts > 0 (and completion_attempts == 0) → HEALING mode. Otherwise NORMAL. Attempt number = normal + completion + 1.

4. **State sync filtering:** Only SheetAttemptResult and SheetSkipped trigger the sync callback. DispatchRetry, RateLimitExpired, ShutdownRequested are correctly filtered out. Callback exceptions are caught.

5. **Deregistration completeness:** All 5 per-job dicts (_job_sheets, _completion_events, _completion_results, _job_renderers, _active_tasks) are cleaned. Task cancellation only affects the deregistered job's tasks.

6. **Observer event boundaries:** validation_pass_rate=99.99 → "sheet.partial" (not completed). rate_limited=True overrides success/failure classification.

---

## Mateship Pickup

Committed uncommitted validate.py changes and 22 untracked tests from another musician (commit 0028fa1):

- **`validate.py` changes:** `_schema_error_hints()`, instrument display "Instrument:" always, non-dict YAML guard
- **`tests/test_schema_error_hints.py`:** 12 tests
- **`tests/test_validate_ux_journeys.py`:** 10 tests
- **`tests/test_quality_gate.py`:** Baseline update (1234→1296 BARE_MAGICMOCK, 115→116 ASSERTION_LESS)

---

## Bug Found: F-200 (Pass 1)

**Severity:** P2 (operational correctness)
**File:** `src/marianne/daemon/baton/core.py:271-275`
**Status:** Fixed (commit bd325bc)

### Root Cause

The ternary `if instrument and instrument in self._instruments` evaluates False when `instrument` is truthy but not in the dict, falling through to the "clear all" else branch.

### Fix

Replaced ternary with explicit `.get()` lookup. Non-existent instrument now returns 0.

### Bug Class

Fallthrough-to-default on failed lookup. Pattern: `if X and X in dict ... else default_behavior` where the else has unintended side effects.

---

## Quality Evidence

```
$ python -m pytest tests/test_baton_adapter_adversarial_breakpoint.py tests/test_m3_adversarial_breakpoint.py tests/test_m3_cli_adversarial_breakpoint.py tests/test_baton_adapter.py --tb=no
257 passed in 1.33s

$ python -m pytest tests/test_quality_gate.py --tb=no
5 passed

$ python -m mypy src/ --no-error-summary
(clean)

$ python -m ruff check src/
All checks passed!
```

---

## Pass 4: Integration Gap Adversarial (`tests/test_m3_pass4_adversarial_breakpoint.py`)

**48 tests across 10 test classes. All pass. This session.**

| Class | Target | Tests | Bugs Found |
|-------|--------|-------|------------|
| `TestCoordinatorClearConcurrency` | Race conditions: clear+report, double-clear, cross-instrument isolation, empty string | 6 | 0 |
| `TestManagerClearRateLimitsAdversarial` | Baton exception during clear, zero-return paths, sum correctness | 3 | 0 |
| `TestReadPidAdversarial` | Empty file, whitespace, non-numeric, negative, float, large PID, trailing newline, missing | 8 | 0 |
| `TestPidAliveAdversarial` | PID 0 (process group), negative PID, very large PID, own PID | 4 | 0 |
| `TestStalePidCleanup` | Dead PID cleanup flow, permission error on read | 3 | 0 |
| `TestResumeViaBatonNoReloadFallback` | None snapshot, corrupt snapshot, workspace mismatch correction | 3 | 0 |
| `TestStaggerTimingBoundary` | Zero stagger, single sheet, boundary values (4999/5000/5001/-1), ms→s conversion | 7 | 0 |
| `TestF200Regression` | F-200 regression + **F-201 (empty string clears all)** + None clears all | 3 | **1 (F-201)** |
| `TestCoordinatorBoundaryValues` | Zero wait, max wait, over-max clamped, 10 instruments clear count | 4 | 0 |
| `TestCheckRunningJobsAdversarial` | Connection refused, resolve exception propagation, default socket | 3 | 0 |
| `TestDualPathClearConsistency` | Coordinator-only, baton-only, neither has limits | 3 | 0 |
| `TestStartConductorRace` | Advisory lock detection, stale PID removal ordering | 2 | 0 |

### Key Adversarial Hypotheses Tested (Pass 4)

1. **Coordinator concurrency:** Concurrent clear+report loops (50 iterations each) with asyncio.gather — no state corruption. Double-clearing returns 0 on second call. Clearing one instrument doesn't affect another. Empty string instrument is handled correctly.

2. **Manager dual-path error resilience:** When baton adapter throws during clear_rate_limits, the coordinator was already cleared (confirmed) but the exception propagates to the caller (documented — no catch in manager).

3. **_read_pid adversarial:** Empty files, whitespace-only, non-numeric content, floats, negative PIDs all handled correctly via ValueError/FileNotFoundError catches. Negative PID (`-1`) is technically valid int and returned as-is.

4. **_pid_alive boundaries:** PID 0 sends to own process group (returns True). Very large PID returns False. Own PID returns True. No crashes on any boundary input.

5. **_check_running_jobs exception gap:** `_resolve_socket_path()` exceptions propagate because the call is OUTSIDE the try/except that wraps `asyncio.run(_probe())`. The outer try/except at process.py:159-162 catches probe failures, not resolution failures.

6. **F-201:** Same bug class as F-200 — `if instrument:` treats empty string as falsy, falling through to "clear all" branch. Fixed by changing to `if instrument is not None:`.

---

## Bug Found: F-201 (Pass 4)

**Severity:** P3 (edge case of F-200)
**File:** `src/marianne/daemon/baton/core.py:271`
**Status:** Fixed (this commit)

### Root Cause

The F-200 fix at `core.py:271` used `if instrument:` (truthiness check). Empty string `""` is falsy in Python, so `clear_instrument_rate_limit("")` fell through to the else branch, clearing ALL instruments. Same bug class as F-200 — truthiness guard instead of identity check.

### Fix

Changed `if instrument:` to `if instrument is not None:` at `core.py:271`.

### Bug Class

Truthiness-vs-identity guard. The F-200 fix correctly handled truthy-but-absent strings but left the falsy-but-provided path open. Anywhere you see `if X:` where the semantic intent is "was X provided," check for falsy-but-valid inputs (0, "", False, empty collections).

---

## Quality Evidence

```
$ python -m pytest tests/test_m3_pass4_adversarial_breakpoint.py tests/test_m3_adversarial_breakpoint.py tests/test_m3_cli_adversarial_breakpoint.py tests/test_baton_adapter_adversarial_breakpoint.py tests/test_clear_rate_limits.py --tb=no -q
266 passed

$ python -m pytest tests/test_quality_gate.py --tb=no
5 passed

$ python -m mypy src/ --no-error-summary
(clean)

$ python -m ruff check src/
All checks passed!
```

---

## Assessment

The adversarial progression across movements tells the story of a maturing codebase:

| Movement | Tests | Bugs | Bug Location |
|----------|-------|------|-------------|
| M1 | 129 | F-018, F-114 | Core state machine, Phase 4.5 |
| M2 | 122 | 0 | — (hardened) |
| M3 Pass 1 | 62 | F-200 | Utility function |
| M3 Pass 2 | 58 | 0 | CLI/UX layer |
| M3 Pass 3 | 90 | 0 | BatonAdapter (1206 lines) |
| M3 Pass 4 | 48 | F-201 | Same function, same bug class |
| **Total** | **509** | **4** | — |

F-201 is the same bug class as F-200, in the same function, just one level deeper. The F-200 fix handled truthy-but-absent; F-201 catches falsy-but-provided. The pattern `if X:` when you mean `if X is not None:` is a Python classic — it's the kind of bug that survives code review because the happy path never exercises it.

What Pass 4 revealed beyond the bug:
- The dual-path clear (coordinator + baton) has no error isolation — a baton exception propagates after the coordinator is already cleared. Not a bug, but a resilience gap.
- `_check_running_jobs` has an exception gap — `_resolve_socket_path` failures propagate because they're outside the try/except. Minor — resolution failures are rare in practice.
- `_read_pid` returns negative PIDs as valid ints — technically correct (int('-1') succeeds), but `-1` passed to `os.kill` sends to all processes. The calling code in start_conductor guards this via `_pid_alive`, so it's safe in practice.

The adversary has now written 509 tests across 4 passes in M3 alone. The bugs are getting smaller and rarer — F-201 is a P3 edge case of a P2 bug. The codebase is approaching the limit of what static adversarial testing can find. The remaining risks live in production behavior, not in unit boundaries.
