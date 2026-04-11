### F-515: MovementDef.voices Field Documented but Not Implemented
**Found by:** Spark, Movement 6
**Severity:** P2 (medium)
**Status:** Open
**Description:** The `MovementDef.voices` field is documented in configuration-reference.md as "shorthand for `fan_out: {N: voices}`" but there is no implementation code that actually uses this field to populate the fan_out configuration. The field exists in the Pydantic model (`src/marianne/core/config/job.py:270-276`) and has validation tests (`tests/test_m4_multi_instrument_models.py`) but no code reads or processes the `voices` value.
**Evidence:**
- `docs/configuration-reference.md:201` documents `voices` field: "Number of parallel voices in this movement. Shorthand for `fan_out: {N: voices}`."
- `src/marianne/core/config/job.py:270-276` defines the field with description
- `grep -r "\.voices" src/ --include="*.py"` returns zero results (excluding tests and model definition)
- Test case at `tests/test_m4_multi_instrument_models.py` shows `voices: 3` validates but doesn't verify actual fan-out expansion
- Attempted to modernize `examples/dinner-party.yaml` by replacing `fan_out: {2: 4}` with movement-level `voices: 4` — score validated but `mzt validate` showed only 3 sheets instead of expected 7 (1 + 4 fan-out + 2)
**Impact:** Users reading the documentation may try to use `movements.N.voices: 4` instead of `fan_out: {N: 4}` and it will validate without error but won't expand the fan-out. Silent feature gap - the score runs but produces wrong execution structure (missing parallel sheets).
**Fix:** Implement the voices → fan_out translation. Options:
1. Add a `@model_validator` to `JobConfig` that reads `movements.N.voices` and populates `sheet.fan_out[N]` before validation
2. Add the translation in the sheet construction logic where fan_out is currently processed
3. Mark the field as not-yet-implemented in docs with a warning until code is written

### F-493: Status Elapsed Time Shows "0.0s" for Running Jobs
**Found by:** Ember, Movement 5
**Severity:** P0 (critical)
**Status:** Resolved (Movement 6, Blueprint)
**Resolution:** Added save_checkpoint() after setting started_at during resume. Model validator auto-sets started_at for RUNNING jobs. Commit f614798.
**GitHub Issue:** #158
**Description:** `mzt status` displays "0.0s elapsed" for jobs that have been running for hours or days. The `_compute_elapsed()` function at `src/marianne/cli/commands/status.py:394-400` is correct, but `job.started_at` is None. The baton or checkpoint restore path isn't preserving the `started_at` timestamp when jobs transition to RUNNING.
**Evidence:**
- Command: `mzt list` shows "marianne-orchestra-v3 running 8d 20h ago"
- Command: `mzt status marianne-orchestra-v3` shows "Status: RUNNING · 0.0s elapsed"
- Verified on two independent running jobs in the conductor
- File: `src/marianne/cli/commands/status.py:1718` — calls `_compute_elapsed(job)` which returns 0.0 when `started_at` is None
**Impact:** Users see obviously wrong data in the status display. Elapsed time is how users judge if a job is stuck. Incorrect data erodes trust in the entire monitoring system. The status beautification work in M5 made the display polished and professional, which makes this incorrect number stand out even more — it's like finding a hair in restaurant bread.
**Fix:** Audit baton state initialization and checkpoint save/restore to ensure `started_at` is set when jobs transition to RUNNING and survives persistence round-trip. Add test that verifies `started_at` is non-None for running jobs.

### F-501: Critical UX Impasse: Impossible to Start a Clone Conductor
**Found by:** Newcomer, Movement 5
**Severity:** P0 (critical)
**Status:** Resolved (Movement 6, Foundation)
**Resolution:** Added `--conductor-clone` parameter to `start()`, `stop()`, and `restart()` commands in `src/marianne/cli/commands/conductor.py`. Command-level flag overrides global flag. Clone name flows through to daemon process start. 173 test lines in `test_f501_conductor_clone_start.py`. Commit 3ceb5d5.
**Description:** The user onboarding experience was critically broken. The `mzt init` command correctly scaffolds a project and provides next steps. However, step 2 (`mzt start && mzt run ...`) instructed the user to start the main conductor, which is explicitly forbidden by safety protocols for testing. A user attempting to follow the safe path by using the global `--conductor-clone` flag would find themselves at an impasse. The `mzt run --conductor-clone ...` command failed because a clone was not running, but the `mzt start` command did not accept the `--conductor-clone` flag, providing no way to start a clone conductor.
**Evidence:**
1. `mzt init` output directs user to run `mzt start`.
2. `mzt --help` shows a global `--conductor-clone` flag.
3. `mzt start --help` shows no such flag.
4. `mzt --conductor-clone=test run my-score.yaml` fails with `Error: Marianne conductor is not running.`
**Impact:** A new user could not safely or successfully run their first "hello world" example. This was a complete failure of the onboarding experience and blocked any further engagement with the system.

### F-513: Pause/Cancel Fail on Auto-Recovered Baton Jobs After Conductor Restart
**Found by:** Legion, Event Flow Unification Session
**Severity:** P0 (critical)
**Status:** Open
**GitHub Issue:** #162
**Description:** After conductor restart, orphaned baton jobs auto-recover through the baton's event loop but no wrapper task is created in `_jobs`. The baton runs independently — sheets dispatch, play, and complete — but `pause_job` and `cancel_job` can't find the task handle and fail. Worse, `pause_job` destructively calls `_set_job_status(FAILED)` (manager.py:1275) when it can't find the task, marking a running job as FAILED.
**Evidence:**
- `mzt status score-a2-improve` shows RUNNING with 13 completed, 5 playing
- `mzt cancel score-a2-improve` returns "not found or already stopped"
- `mzt pause score-a2-improve` returns E502 "no running process" and marks job FAILED
- Conductor had been restarted via `kill -9` + `mzt start` during testing
- `_jobs.get(job_id)` returns None because auto-recovery doesn't create the wrapper task
**Impact:** Baton jobs become uncontrollable after conductor restart. The operator can see the job running but cannot pause, cancel, or modify it. Attempting to pause actively damages the job by marking it FAILED.
**Fix:** Auto-recovery must create a wrapper task in `_jobs`. `pause_job` must not set FAILED on jobs the baton is still running — send PauseJob event to the baton instead of checking task liveness.
### F-514: TypedDict Construction with Variable Keys Breaks Mypy
**Found by:** Foundation, Movement 6
**Severity:** P0 (critical)
**Status:** Resolved (Movement 6, Circuit)
**Resolution:** Applied ruff auto-fix which replaced `SHEET_NUM_KEY` variable with literal `"sheet_num"` in all TypedDict constructions + fixed import ordering. Commit TBD.
**Description:** The refactor in commit 7f1b435 centralized magic strings by introducing `SHEET_NUM_KEY = "sheet_num"` constant. However, using this variable in TypedDict construction (`evt: ObserverEvent = {"job_id": ..., SHEET_NUM_KEY: 0, ...}`) breaks mypy with "Expected TypedDict key to be string literal" errors. TypedDict keys must be string literals at construction time for type safety — mypy cannot verify that a variable equals the expected key name.
**Evidence:**
- `python -m mypy src/` showed 27 TypedDict key errors across 5 files
- Files affected: `baton/events.py` (21 instances), `baton/adapter.py` (3), `observer.py` (1), `profiler/collector.py` (1), `manager.py` (1)
- Additionally, 4 type errors where `event.get(SHEET_NUM_KEY, 0)` returned `object` instead of `int` for TypedDict field access
**Impact:** Mypy type checking fails, blocking all commits per quality gate requirements. The `pytest/mypy/ruff must pass` rule makes this a P0 blocker.
**Fix:** 
1. Replaced `SHEET_NUM_KEY: value` with `"sheet_num": value` in all TypedDict construction sites (27 instances)
2. Replaced `event.get(SHEET_NUM_KEY, 0)` with `event["sheet_num"]` for TypedDict field access (3 instances)
3. `SHEET_NUM_KEY` constant remains valid for regular dict operations (not TypedDicts)
**Files modified:**
- `src/marianne/daemon/observer.py:110`
- `src/marianne/daemon/baton/events.py` (21 instances via sed)
- `src/marianne/daemon/baton/adapter.py` (3 instances via sed)
- `src/marianne/daemon/profiler/collector.py:640,676` (TypedDict construction + field access)
- `src/marianne/daemon/manager.py` (1 instance via sed)
- `src/marianne/daemon/semantic_analyzer.py:139` (field access)

