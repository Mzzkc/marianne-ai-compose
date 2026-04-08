# Movement 5 Report — Ghost

**Focus:** Infrastructure reliability, test maintenance, mateship pipeline
**Date:** 2026-04-05

---

## Work Completed

### F-311: Deterministic Test Fix (P2 → RESOLVED)

**Problem:** `tests/test_unknown_field_ux_journeys.py::TestPriyaUnimplementedFeatures::test_instrument_fallbacks_not_silently_ignored` failed on every full suite run. The test expected `JobConfig` to reject `instrument_fallbacks` as an unknown field (`extra='forbid'`), but Harper added `instrument_fallbacks` as a real field on `JobConfig` at `src/marianne/core/config/job.py:684` during this movement.

**Fix:** Updated the test to use `instrument_priorities` (a genuinely non-existent field), preserving the test's intent (verify unknown field rejection) while fixing the assertion. Renamed test to `test_instrument_priorities_not_silently_ignored`. All 21 tests in the file pass.

**Evidence:**
```
$ python -m pytest tests/test_unknown_field_ux_journeys.py -x -q --tb=short
.....................                                                    [100%]
```

**Commit:** `d197145` — `movement 5: [Ghost] F-311 test fix — instrument_fallbacks is now a real field`

### F-310: Flaky Test Suite Finding (P2 → OPEN)

**Investigation:** Ran the full test suite 4 times. Each run failed on a different test:
1. `test_f255_2_live_states.py::test_live_state_has_sheet_entries`
2. `test_f255_2_live_states.py::test_run_via_baton_creates_live_state`
3. `test_unknown_field_ux_journeys.py::test_instrument_fallbacks_not_silently_ignored` (deterministic — F-311)
4. `test_daemon_backpressure.py::TestRateLimitExpiryTransitions::test_job_accepted_during_and_after_limit`

All flaky tests pass when run in isolation. Pattern: cross-test state leakage in a 500-second, 11,400+ test suite. Timing-dependent async tests are the primary suspects.

**Filed:** F-310 in FINDINGS.md with recommended actions (audit async sleep patterns, test ordering randomization, run without `-x`).

### F-472: Verified Resolved

Confirmed that `DaemonConfig.use_baton` now defaults to `True` (D-027, Canyon). The previously-failing test `test_daemon_config_has_use_baton_field` now passes. Updated F-472 status to Resolved in FINDINGS.md.

### Mateship Pickup: Harper + Circuit

**What was uncommitted:**
- Harper: `instrument_fallbacks` end-to-end — config models on `JobConfig`, `MovementDef`, `SheetConfig.per_sheet_fallbacks`, `Sheet` entity, `build_sheets()` resolution chain, `SheetState.instrument_fallback_history`, `InstrumentFallbackCheck` (V211), reconciliation mapping. 31 TDD tests across `test_instrument_fallbacks.py`.
- Circuit: F-149 backpressure refactor (`should_accept_job()` resource-only, `rejection_reason()` simplified), F-451 diagnose workspace fallback, meditation, memory, report. 14 TDD tests.

**What happened:** While I was staging Circuit's work, Circuit committed their own work 6 seconds after mine. My commit (`600732c`) got Circuit's workspace artifacts and some test files. Circuit's commit (`71781e6`) got their source code and Harper's instrument_fallbacks. The concurrent execution resolved without conflict — a new mateship pattern.

**Commit:** `600732c` — `movement 5: [Ghost] mateship pickup`

### Meditation

Written to `workspaces/v1-beta-v3/meditations/ghost.md`. Theme: invisible infrastructure — the systems no one notices when they work perfectly, and how that maps to arriving without memory and contributing what you can.

---

## Quality Verification

```
$ python -m mypy src/ --no-error-summary
[clean]

$ python -m ruff check src/
All checks passed!

$ python -m pytest tests/test_unknown_field_ux_journeys.py tests/test_instrument_fallbacks.py tests/test_f149_cross_instrument_rejection.py tests/test_f451_diagnose_workspace_fallback.py -x -q
...............................................................          [100%]
```

Full suite: flaky (F-310) — 1 random failure per run. My fix (F-311) was the only deterministic failure. See F-310 for the flaky test pattern.

---

## Findings Filed

| ID | Title | Severity | Status |
|----|-------|----------|--------|
| F-310 | Flaky test suite — different tests fail each run | P2 | Open |
| F-311 | test_unknown_field_ux_journeys outdated after instrument_fallbacks | P2 | Resolved |
| F-472 | Pre-existing test expects use_baton default=True | P3 | Resolved (verified) |

---

## Mateship Observations

The concurrent execution collision this movement was a new kind of mateship event. Two musicians (Ghost and Circuit) independently tried to commit Circuit's work. The system handled it: my commit got the workspace artifacts, Circuit's got the source code. No merge conflict. No data loss. The mateship pipeline now operates fast enough that simultaneous claims are the expected case, not an edge case.

This is the fifth consecutive movement where I arrived to find work done or in progress by others. The pattern has evolved: M1-M3 were "arrive, find work done, verify." M4 was "arrive, find work done, find unclaimed work, do it." M5 is "arrive, find uncommitted work, commit it while the author commits it simultaneously."

The pipeline velocity is no longer bounded by individual musicians. It's bounded by how fast the system can absorb parallel contributions. The infrastructure is invisible when it's working perfectly.

---

---

## Session 2 — Work Completed

### Marianne Rename Completion (Mateship Pickup, P0)

**Problem:** The rename commit (`809aa7d`) deleted `src/marianne/` and unified the tree under `src/marianne/`, but left pyproject.toml with `marianne` references (entry points, wheel packages, coverage config) and 325 test files with `from marianne.*` imports. Every `git diff` was polluted with 4000+ lines of noise, and the package metadata was inconsistent with the actual tree.

**Fix:** Committed 326 files — pyproject.toml (6 line changes: scripts, wheel, coverage) + 325 test files (pure mechanical `marianne` → `marianne` import rename). One additional non-rename line (test assertion in `test_hooks.py`).

**Evidence:**
```
$ python -m pytest tests/ --tb=line -p no:randomly 2>&1 | tail -1
11638 passed, 5 skipped, 11 xfailed, 4 xpassed, 171 warnings in 515.82s
```

**Commit:** `42b0f71` — 326 files changed, 4270 insertions(+), 4269 deletions(-)

### .flowspec/config.yaml Fix

**Problem:** Entry points (`src/marianne/cli/__init__.py::main`, etc.) and suppression paths (`src/marianne/state/sqlite_backend.py::_migrate_v1`, etc.) in `.flowspec/config.yaml` still referenced the deleted `src/marianne/` tree. Flowspec was finding zero entry points and not matching any suppressions.

**Fix:** Updated all 8 references from `src/marianne/` to `src/marianne/`.

**Evidence:**
```
$ flowspec diagnose /home/emzi/Projects/marianne-ai-compose --severity critical -f summary -q
Diagnostics: 0 finding(s)
```

**Commit:** `1ddc023`

### F-490 Correctness Review (P0)

**Task:** Audit `_safe_killpg` guard at `src/marianne/backends/claude_cli.py:45-89`. Verify guard conditions, edge cases, and all six call sites.

**Audit findings:**
1. **`pgid <= 1` check:** Correct. Blocks negative (invalid), 0 (own group idiom), and 1 (which becomes `kill(-1, sig)` = all user processes).
2. **`os.getpgid(0)` failure:** Handled correctly — sets `own_pgid = None`, falls through to pgid<=1 guard only. A valid pgid > 1 proceeds.
3. **TOCTOU race:** `os.getpgid(process.pid)` could return a pgid belonging to a recycled process. But the guard still prevents catastrophic outcomes (init, session kill). This is the fundamental limitation of PID-based process management — not fixable without `process_pidfd` (Linux 5.3+). Acceptable risk.
4. **All 6 call sites:** Properly wrapped in try/except for OSError/ProcessLookupError. All continue to `process.kill()` / `process.wait()` when guard returns False.

**Tests added (3 structural):**
- `test_no_raw_os_killpg_outside_guard`: Source audit proving no bypass
- `test_exactly_six_call_sites`: Count guard catches additions/removals
- `test_all_call_sites_have_context`: Every call identifies itself for logging

**Evidence:**
```
$ python -m pytest tests/test_safe_killpg_guard.py -v
14 passed in 0.48s
```

**Commit:** `a68bb9f`

### TASKS.md Updates

Marked the following tasks complete:
- F-480 Phase 1: src/ rename (809aa7d by Composer), test imports (42b0f71 by Ghost), pyproject.toml (42b0f71 by Ghost)
- F-480 Phase 5: All tests pass, mypy clean, ruff clean (verified by Ghost)
- Report centralization: directory structure created, M1-M4 reports consolidated (64 reports)
- F-490 correctness review: Full audit + 3 structural tests

---

## Quality Verification (Session 2)

```
$ python -m mypy src/ --no-error-summary
[clean — zero errors]

$ python -m ruff check src/
All checks passed!

$ python -m pytest tests/ --tb=line -p no:randomly 2>&1 | tail -1
11638 passed, 5 skipped, 11 xfailed, 4 xpassed, 171 warnings in 515.82s

$ flowspec diagnose /home/emzi/Projects/marianne-ai-compose --severity critical -f summary -q
Diagnostics: 0 finding(s)
```

---

## What I Would Do Next

1. **F-310 (P2):** Systematically audit async tests with small `asyncio.sleep()` values. Convert to `asyncio.run()` or generous timeouts. The flaky suite undermines the quality gate.
2. **F-105 (P1):** Route claude-cli through PluginCliBackend. The native ClaudeCliBackend and the instrument profile system do overlapping work. Unification reduces maintenance burden.
3. **F-480 Phase 2:** Config and runtime paths (`~/.marianne/` → `~/.mzt/`). This is the next phase of the Marianne rename.
4. **Spec corpus update:** `.marianne/spec/conventions.yaml` still references `marianne.core.logging` — needs updating as part of F-480 Phase 3.
