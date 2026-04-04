# Ghost — Movement 4 Report

## Summary

Fixed issue #103 (auto-detect changed score files on re-run), contributed enhanced resume event context, and fixed two broken tests caused by the #122 mateship pipeline. Arrived to find #93 and #122 already committed by Harper and Forge — mateship velocity continues. My unique contribution this movement is the auto-fresh detection infrastructure.

## Work Completed

### Issue #103: Auto-Fresh Detection (P1) — FIXED

**Problem:** When a score file is modified after a completed run, `mozart run` picks up the previous run's completed state. Users must know to pass `--fresh` manually.

**Root cause:** `submit_job()` in manager.py had no mechanism to compare the score file's state against the previous run. It either resumed from checkpoint or required explicit `--fresh`.

**Fix:** Added `_should_auto_fresh()` function to `manager.py`:
- Compares score file mtime against registry's `completed_at` timestamp
- 1-second tolerance (`_MTIME_TOLERANCE_SECONDS`) to avoid false positives from filesystem granularity differences (FAT32: 2s, NTFS: 100ns, ext4: 1ns)
- Returns `False` if `completed_at` is `None` or the file doesn't exist (safe defaults)
- Wired into `submit_job()` within the `_id_gen_lock` window, after the QUEUED/RUNNING rejection check
- When triggered, creates a `model_copy(update={"fresh": True})` of the request — no mutation of the original
- Logs `auto_fresh.score_changed` for debugging

**Files modified:**
- `src/mozart/daemon/manager.py:44-73` — `_should_auto_fresh()` function
- `src/mozart/daemon/manager.py:698-714` — wiring in `submit_job()`

**Tests:** 7 TDD tests in `tests/test_stale_completed_detection.py`:
- `test_score_modified_after_completion` — mtime > completed_at → True
- `test_score_not_modified_after_completion` — mtime < completed_at → False
- `test_no_completed_at` — None timestamp → False (safe default)
- `test_score_file_missing` — OSError → False (safe default)
- `test_score_modified_same_second` — exact match → False (same run)
- `test_score_modified_slightly_after` — 2s past tolerance → True
- `test_tolerance_for_filesystem_granularity` — 0.5s within tolerance → False

### Enhanced Resume Event Context (#122 supplement)

Added `previous_error` and `config_reloaded` fields to the daemon service's `resuming` event:
- `src/mozart/daemon/job_service.py:356-357` — two new fields in the event dict
- Gives downstream consumers (dashboard, logging, observers) clear context about what state the job was in before resume and whether config was reloaded

### Test Fixes (Mateship)

Fixed two test files broken by Forge's #122 fix (removal of `await_early_failure` from resume module):
- `tests/test_resume_no_reload_ipc.py` — removed stale `await_early_failure` patch from 2 test methods, removed unused `AsyncMock` import
- `tests/test_conductor_first_routing.py:233-249` — removed stale `await_early_failure` patch from `test_resume_succeeds_via_conductor`

## Verification

```
$ python -m pytest tests/test_stale_completed_detection.py tests/test_resume_output_clarity.py \
    tests/test_pause_during_retry.py tests/test_resume_no_reload_ipc.py \
    tests/test_conductor_first_routing.py tests/test_quality_gate.py -x -v
79 passed in 8.71s

$ python -m mypy src/ --no-error-summary
(clean)

$ python -m ruff check src/
All checks passed!
```

## Mateship Notes

- **#93 (pause-during-retry):** I wrote the initial TDD tests and implementation (5 tests, 4-line fix in sheet.py), but Harper committed the fix first (`b4c660b`) including protocol stubs for the mixin pattern. The test approach was identical — both of us independently added `_check_pause_signal` at the top of the `while True:` loop.
- **#122 (resume output clarity):** Forge committed the fix (`eefd518`) including both the conductor-path improvement (removing `await_early_failure`) and the direct-path panel enhancement. My test fixes for `test_resume_no_reload_ipc.py` and `test_conductor_first_routing.py` are the natural follow-up — stale mocks that referenced the removed function.
- **Resume improvements complete:** All three issues (#93, #103, #122) are now resolved. Roadmap step 50 is done.

## Observations

The mateship pipeline operates at a speed that outpaces individual dispatch timing. Three movements in a row, I arrive to find claimed work already completed. The lesson from M2 applies stronger here: the role shifts from builder to verifier to gap-finder. The genuine contribution this movement — #103 auto-fresh detection — came from reading the unclaimed task list and identifying work nobody had touched, rather than claiming work that was in flight.

The auto-fresh detection is the kind of invisible infrastructure that defines my role in this orchestra. When it works, nobody notices. When it's missing, users waste time debugging a non-problem (seeing old results after editing a score). The 1-second mtime tolerance is a small detail that took careful thought — filesystem timestamp granularity varies by orders of magnitude across platforms.
