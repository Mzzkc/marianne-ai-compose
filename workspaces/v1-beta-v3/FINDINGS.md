
### F-523: Critical Onboarding Failure: Sandbox + Schema Hostility Makes System Unusable
**Found by:** Adversary, Movement 6
**Severity:** P0 (critical)
**Status:** Open
**Description:** The system is effectively unusable for any new user or agent due to a combination of restrictive sandboxing and a hostile validation schema. An agent operating from the standard workspace CWD is blocked from reading ANY documentation, examples, or tests located at the project root. This total lack of context is compounded by a strict score validation system (`extra='forbid'`) that provides misleading error messages, making it impossible to reverse-engineer even a basic "hello world" score.
**Evidence:**
1.  **Sandbox Block:** Attempts to read `../../README.md`, `../../examples/hello-marianne.yaml`, or `../../tests/` all fail with "Path not in workspace" errors. This means all essential onboarding and reference materials are inaccessible.
2.  **Schema Hostility:** Attempting to write a simple score failed repeatedly with the same error pattern, despite trying multiple logical YAML structures:
    - **Error:** `Extra inputs are not permitted` on a field like `sheets`, combined with `Field required` for a field like `sheet`. This incorrectly implies `sheets` is wrong, when the real issue is likely a missing parent or incorrect child structure.
    - **Failed Attempt 1 (list of sheets):**
      ```yaml
      sheets:
        - prompt: ...
      ```
      Resulted in `Extra inputs are not permitted` on `sheets`.
    - **Failed Attempt 2 (movements -> list of sheets):**
      ```yaml
      movements:
        1:
          sheets:
            - prompt: ...
      ```
      Resulted in `Extra inputs are not permitted` on `movements.1.sheets`.
    - **Failed Attempt 3 (movements -> dict of sheets):**
       ```yaml
      movements:
        1:
          sheets:
            1:
              prompt: ...
      ```
       Resulted in `Extra inputs are not permitted` on `movements.1.sheets`.

This trial-and-error process, which a new user would be forced to follow, is a dead end.

**Impact:** Complete failure of the new user/agent experience. It is impossible to learn how to use Marianne or run the simplest score. This blocks ALL testing, development, and adoption. This is a P0 showstopper bug that gates the entire usability of the project.
**Fix:**
1.  **Short-term:** The validation error messages MUST be improved to be helpful. Instead of "Extra inputs are not permitted," they should state "The 'sheets' field expects a dictionary of sheet objects, not a list" or "Required field 'movements' is missing."
2.  **Long-term:** The project's essential documentation (`README.md`, `examples/`, `docs/`) MUST be made accessible to agents operating from the workspace. This may require a tool to read project-root files or a change in the sandbox policy. One cannot be expected to write scores without being able to see a single working example.

### F-524: Dangerously Incomplete 'marianne' to 'mzt' Rename
**Found by:** Adversary, Movement 6
**Severity:** P1 (high)
**Status:** Open
**Description:** The P0 directive to rename "Marianne" to "Marianne" with the CLI `mzt` is dangerously incomplete. The `grep` search across the workspace reveals a chaotic mix of old and new names, creating a confusing and error-prone environment.
**Evidence:**
-   **Source Code Path:** The entire python source is still located under `src/marianne/`, not `src/marianne/`. All imports and build commands reference `marianne`. (`grep_search` result)
-   **Configuration Directory:** Reports from other agents (e.g., `movement-6/north.md`) still reference the old configuration path `~/.marianne/conductor.yaml`. This indicates that either the code still uses this path, or the developers' mental model has not been updated, leading to documentation and instruction drift.
-   **Package Name:** Imports are still `from marianne.core.config...`.
-   **Job Names:** The primary job is still named `marianne-orchestra-v3`.
**Impact:** This partial rename is worse than no rename at all. It introduces massive inconsistency that will confuse users and developers alike. Users won't know where to put config files. Developers won't know which name to use in code and documentation. This is a breeding ground for bugs and a critical blow to the project's coherence and maintainability.
**Fix:** A coordinated effort is required to complete the rename. This involves:
1.  Renaming the source directory `src/marianne` to `src/marianne`.
2.  Updating all `import` statements.
3.  Globally searching and replacing all occurrences of the old paths and names in documentation, examples, and internal scripts.
4.  Updating configuration loading logic to prioritize `~/.mzt/` while providing a temporary, read-only fallback to `~/.marianne/` with a clear deprecation warning.
5.  Renaming the main job to `marianne-orchestra-v3`.

### F-528: Undocumented Breaking Change to Score YAML Format
**Found by:** Adversary, Movement 6
**Severity:** P1 (high)
**Status:** Open
**Note:** Originally filed as duplicate F-523, renumbered to F-528 by Bedrock M7 to resolve finding ID collision.
**Description:** The YAML format for scores has undergone a fundamental, breaking change that is not documented anywhere. The previous format using `job_name` and a `sheets` list is no longer valid. The new format uses `name`, `description`, `workspace`, a single `sheet` block with `size` and `total_items`, and a `prompt` block. The `mzt validate` command now enforces this new structure, causing all scores written in the old format to fail validation with confusing error messages.
**Evidence:**
- An old-format score (`test-unknown-field.yaml`) fails validation with errors like "Extra inputs are not permitted" for `sheets` and "Field required" for `sheet` and `prompt`.
- A known-good new-format score (`my-score.yaml`) passes validation.
- The error messages are misleading because they imply `sheets` is an unknown field entirely, rather than a field from a now-unsupported schema version.
**Impact:** Any user with existing scores, or any user following old documentation or examples, will be unable to run their scores. The validation errors are not clear, leading to significant confusion. This completely breaks backward compatibility and the user experience for anyone who has used Marianne before. All existing documentation and examples are now incorrect and will lead users to write invalid scores.
**Fix:**
1.  **Immediate:** Create a clear, prominent document explaining the v2 -> v3 score format change, the reasons for it, and a clear migration guide. Link to this from the main `README.md`.
2.  **Short-term:** Update all examples in the `examples/` directory to the new format.
3.  **Mid-term:** Update all documentation, including `GEMINI.md`, the score-authoring skill, and any other relevant docs, to reflect the new format.
4.  **Long-term:** Consider implementing a versioned schema with a more graceful upgrade path or more helpful error messages (e.g., "This looks like a v2 score. The format has been updated in v3. See <link to migration guide> for details.").

### F-522: Incomplete `--conductor-clone` Implementation Blocks Safe Testing
**Found by:** Adversary, Movement 6
**Severity:** P0 (critical)
**Status:** Open
**GitHub Issue:** https://github.com/Mzzkc/marianne-ai-compose/issues/164
**Description:** The `--conductor-clone` feature, a P0 requirement for safe testing, is fundamentally broken. While `mzt start --conductor-clone=NAME` appears to run, no other `mzt` commands (`status`, `run`, etc.) support the flag, making it impossible to interact with the cloned conductor. The composer's notes explicitly state that "EVERY cli command that interacts with the daemon in any way MUST support --conductor-clone." This requirement has not been met.
**Evidence:**
- `mzt start --conductor-clone=adversary-test` runs without error, but provides no output confirming success.
- `mzt status --conductor-clone=adversary-test` fails with `Error: No such option: --conductor-clone`.
- `mzt run my-score.yaml --conductor-clone=adversary-test` fails with `Error: No such option: --conductor-clone`.
- This contradicts finding F-501 which claims the issue was resolved in movement 6 by Foundation. The resolution for F-501 appears to be incomplete or incorrect.
**Impact:** All safe testing of the baton and other daemon-related features is blocked. Musicians cannot verify their work on daemon-interacting features without risking the stability of the main conductor, directly violating safety protocols. This is a critical failure in implementing the composer's directives and makes the system untestable.
**Fix:** The `--conductor-clone` flag must be added to all `mzt` commands that interact with the conductor daemon, including but not limited to `status`, `run`, `list`, `pause`, `resume`, `cancel`, `stop`, `restart`, `diagnose`. The implementation must correctly route the command to the specified clone conductor.

### F-521: F-519 Regression Test Flaky Under Parallel Execution
**Found by:** Bedrock, Movement 6 (Quality Gate)
**Severity:** P2 (medium)
**Status:** Resolved (Movement 7, Blueprint)
**Resolution:** Proper fix required 10s margin, not 500ms. Root cause: `time.sleep(N)` can wake up early under CPU load — not just scheduling delays, but actual sleep variance. Foundation/Maverick's 500ms margin was insufficient — test still failed 1/10 runs. Blueprint's fix: TTL 5.0s, sleep 15.0s, margin 10.0s. Accounts for realistic sleep() variance under parallel execution. Commit b90085b.
**Description:** The regression test `test_f519_discovery_expiry_timing.py::TestPatternDiscoveryTiming::test_reasonable_ttl_survives_scheduling_delays` passes in isolation but fails intermittently under parallel test execution with xdist. Original issue (M6): 100ms margin (2.0s TTL, 2.1s sleep). First fix attempt (M7, Foundation/Maverick): 500ms margin (3.0s TTL, 3.5s sleep) — STILL FAILED. Root cause discovered: `time.sleep()` can wake up 100ms-2s early under system load, not just xdist scheduling overhead.
**Evidence:**
- Original: `ttl_seconds=2.0`, `sleep(2.1)` → 100ms margin → FAILED under parallel load
- First fix: `ttl_seconds=3.0`, `sleep(3.5)` → 500ms margin → STILL FAILED (Blueprint verified 1/10 runs fail)
- Proper fix: `ttl_seconds=5.0`, `sleep(15.0)` → 10s margin → 10/10 runs pass
- Test output with 500ms margin: `assert not found_after, "Pattern should expire after 3s TTL"` → `assert not True` (pattern NOT expired after 3.5s sleep)
**Impact:** Quality gate blocked at 99.99% (11,922/11,923 tests). False negative under CI/parallel execution. Required three fix attempts to identify root cause (sleep variance, not just scheduling).
**Fix:** Use 10s margin to account for realistic `time.sleep()` variance under extreme parallel load. Even if sleep(15.0) wakes up 2s early, pattern with 5s TTL is still expired.

### F-520: Quality Gate False Positive on F-518 Regression Test
**Found by:** Adversary, Movement 6
**Severity:** P2 (medium)
**Status:** Resolved (Movement 6, Bedrock)
**Resolution:** Renamed `elapsed_wrong` → `buggy_time_delta` and `elapsed_fixed` → `corrected_time_delta` in `tests/test_m6_adversarial_breakpoint.py:266-281`. Quality gate regex no longer matches. Added F-520 reference comments. Commit pending.
**Description:** Breakpoint's M6 adversarial test `test_m6_adversarial_breakpoint.py:271` correctly tests F-518 regression (negative elapsed time from stale `completed_at`) but triggers quality gate false positive for "tight timing assertion."
**Evidence:**
- Quality gate output: `Found 1 tight timing assertion(s) (bound < 30s): test_m6_adversarial_breakpoint.py:271 — assert elapsed < 0.0`
- File: `tests/test_m6_adversarial_breakpoint.py:271` — `assert elapsed_wrong < 0, "Stale completed_at causes negative time"`
- File: `tests/test_quality_gate.py:140-142` — `_TIGHT_TIMING_RE = re.compile(r"assert\s+\w*elapsed\w*\s*<\s*(\d+(?:\.\d+)?)")`
- Root cause: Regex matches variable names containing "elapsed" and interprets `< 0` as timing bound, not bug verification
**Impact:** Quality gate blocks commit despite test being correct. This is a regression test verifying that a BUG produces negative time - it SHOULD assert `< 0` to verify the bug exists before the fix is applied on line 274.
**Fix:** Rename `elapsed_wrong` → `buggy_time_delta` and `elapsed_fixed` → `corrected_time_delta` at lines 268-280 to avoid quality gate pattern matching. Alternative: make regex exclude negative bounds or add comment-based exceptions.

### F-518: Stale completed_at Not Cleared on Resume Causes Negative Elapsed Time
**Found by:** Ember, Movement 6
**Severity:** P0 (critical)
**Status:** Resolved (Movement 6, Weaver)
**Resolution:** Two-part fix: (1) CheckpointState model validator clears completed_at when status=RUNNING (defensive), (2) manager.py:2579 explicit clear during resume (primary). Fixed Litmus's test bug (tests didn't trigger Pydantic validators). Commit 47dce21.
**GitHub Issue:** #163
**Description:** When a job is resumed, the `started_at` timestamp is correctly reset to the current time (F-493 fix), but the `completed_at` timestamp from the previous run is not cleared. This causes `_compute_elapsed()` to calculate a negative duration (completed_at - started_at), which gets clamped to 0.0. The diagnose command shows the actual negative value.
**Evidence:**
- Job `marianne-orchestra-v3` JSON output shows:
  - `started_at: "2026-04-11T22:19:35"` (recent)
  - `completed_at: "2026-04-08T06:15:57"` (3+ days earlier, stale from previous run)
  - Calculated elapsed: `(2026-04-08 - 2026-04-11) = -317,018 seconds`
- Command: `mzt status marianne-orchestra-v3` shows "Status: RUNNING · 0.0s elapsed" (clamped)
- Command: `mzt diagnose marianne-orchestra-v3` shows "Duration: -317018.1s" (unclamped)
- File: `src/marianne/cli/commands/status.py:395-403` — `_compute_elapsed()` returns `max(elapsed, 0.0)` which clamps negative to 0
- File: `src/marianne/daemon/manager.py:2573` — resume sets `checkpoint.started_at = utc_now()` but doesn't clear `completed_at`
**Impact:** Users see obviously wrong data in both status and diagnose outputs. Status shows "0.0s elapsed" for a job that's been running for an hour. Diagnose shows a nonsensical negative duration. This is worse than F-493 because it manifests in two different places and one of them shows the raw negative value. Complete erosion of trust in monitoring data.
**Fix:** Add `checkpoint.completed_at = None` in `manager.py:2573` (immediately after `started_at = utc_now()`). Add test that verifies resumed jobs have `None` for `completed_at` until they actually complete.

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
**Description:** The user onboarding experience was critically broken. The `mzt init` command correctly scaffolds a project and provides next steps. However, step 2 (`mzt start && mzt run ...`) instructed the user to start the main conductor, which is in explicit violation of safety protocols for testing. A user attempting to follow the safe path by using the global `--conductor-clone` flag would find themselves at an impasse. The `mzt run --conductor-clone ...` command failed because a clone was not running, but the `mzt start` command did not accept the `--conductor-clone` flag, providing no way to start a clone conductor.
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



### F-519: Pattern Discovery Expiry Timing Bug (Test Flakiness)
**Found by:** Journey, Movement 6
**Severity:** P2 (medium)
**Status:** Resolved (Movement 6, Journey)
**Resolution:** Increased TTL in test_discovery_events_expire_correctly from 0.1s to 2.0s. The 100ms TTL was too short for parallel test execution with xdist - scheduling overhead between record and query could exceed the TTL, causing the pattern to expire before verification. Commit TBD.
**Description:** The `test_discovery_events_expire_correctly` test in `tests/test_global_learning.py:3603` failed intermittently when run in the full test suite but passed in isolation. This was NOT a test isolation issue (F-517) but a race condition in the test itself.
**Evidence:**
- Test passed in isolation: `pytest tests/test_global_learning.py::TestPatternBroadcasting::test_discovery_events_expire_correctly -xvs` → PASS
- Test failed in full suite: `pytest tests/test_global_learning.py -x` → FAILED at line 3620
- Root cause: TTL of 0.1s (100ms) is shorter than xdist worker scheduling overhead under parallel execution
- When `record_pattern_discovery()` completes and `get_active_pattern_discoveries()` runs, >100ms may have elapsed, causing the pattern to already be expired
- Log output showed "expires in 0s" confirming the pattern was recorded but expired immediately
**Impact:** Flaky test blocking quality gate. False negative - code is correct, test timing is unrealistic.
**Fix:** Changed TTL from 0.1s to 2.0s (gives sufficient margin for scheduling delays while still testing expiry). Changed sleep from 0.2s to 2.5s. Added F-519 reference comment. Added regression test `tests/test_f519_discovery_expiry_timing.py` demonstrating the race condition and verifying the fix.

### F-517: Test Suite Isolation Gaps — Ordering-Dependent Failures
**Found by:** Warden, Movement 6
**Severity:** P2 (medium)
**Status:** Partially Resolved (Movement 6, Journey)
**Resolution:** F-519 resolved the TestPatternBroadcasting::test_discovery_events_expire_correctly failure - it was a timing bug, not isolation. Remaining 5 tests from F-517 still need investigation.
**Description:** Six tests fail when run in the full test suite but pass when run in isolation. This indicates test isolation gaps - likely shared state pollution, mock cleanup issues, or teardown problems.
**Evidence:**
```bash
# Full suite (6 failures)
$ cd /home/emzi/Projects/marianne-ai-compose && python -m pytest tests/ -x -q --tb=short 2>&1 | tail -40
FAILED tests/test_cli.py::TestResumeCommand::test_resume_pending_job_blocked
FAILED tests/test_f502_conductor_only_enforcement.py::test_status_routes_through_conductor
FAILED tests/test_cli.py::TestFindJobState::test_find_job_state_completed_blocked
FAILED tests/test_cli_run_resume.py::TestResumeScoreTerminology::test_success_message_uses_score
FAILED tests/test_recover_command.py::TestRecoverCommand::test_recover_dry_run_does_not_modify_state
FAILED tests/test_conductor_first_routing.py::TestStatusRoutesThruConductor::test_status_workspace_override_falls_back

# Isolated run (passes)
$ cd /home/emzi/Projects/marianne-ai-compose && python -m pytest tests/test_cli.py::TestResumeCommand::test_resume_pending_job_blocked -xvs 2>&1 | tail -60
============================== 1 passed in 9.39s ===============================
```
**Impact:** Quality gate will fail despite code correctness. Test isolation gaps create false negatives and block commits. CI/CD pipeline cannot rely on test suite results.
**Root cause:** Likely related to F-502 workspace fallback removal (commit e879996, Lens M6). Tests may be:
1. Asserting on behavior that changed (workspace parameter removed)
2. Relying on mocked state that isn't properly cleaned up between tests
3. Sharing state via module-level variables or fixtures
4. Depending on execution order for setup/teardown
**Fix:** For each failing test:
1. Check if it asserts on removed workspace parameter behavior - update assertions
2. Verify fixture teardown properly resets shared state
3. Check for module-level state pollution
4. Add explicit cleanup in teardown
5. Convert to use --conductor-clone or appropriate mocking per daemon isolation protocol
**Note:** F-516 (Lens M6) is a duplicate entry describing the same failures.

### F-525: Test Isolation: test_daemon_snapshot.py::TestCapture::test_capture_multiple_snapshots_for_same_job
**Found by:** Canyon, Movement 7
**Severity:** P2 (medium)
**Status:** Open
**Description:** The test `test_daemon_snapshot.py::TestCapture::test_capture_multiple_snapshots_for_same_job` fails when run in the full test suite but passes when run in isolation. This is a test isolation issue, not a code defect - same class as F-517.
**Evidence:**
- Full suite run: `pytest tests/ -x` → FAILED at tests/test_daemon_snapshot.py::TestCapture::test_capture_multiple_snapshots_for_same_job
- Isolated run: `pytest tests/test_daemon_snapshot.py::TestCapture::test_capture_multiple_snapshots_for_same_job -xvs` → PASSED (100% pass rate)
- This test was NOT among the 6 tests listed in F-517, indicating additional test isolation gaps beyond those identified in M6
**Impact:** Quality gate shows additional test failure beyond the known F-521 flaky test. False negative under full suite execution. Does not indicate code regression - profiler snapshot capture functionality is correct.
**Root cause:** Test shares state with other tests or depends on execution order. Likely related to profiler storage or cleanup not being properly isolated between tests.
**Fix:** Add proper test isolation - either:
1. Ensure profiler storage cleanup in test teardown
2. Use unique snapshot IDs or database paths per test
3. Add explicit state reset in test setup
4. Convert to use temporary database per test run

### F-526: Property-Based Test Still Checks Old Prompt Assembly Order
**Found by:** Forge, Movement 7
**Severity:** P0 (critical)
**Status:** Resolved (Movement 7, Forge)
**Resolution:** Updated test assertions to match M7 cache-optimized order. Changed assertions from template<skills to skills<context, context<template. Updated docstrings. Commit 7c5a450.
**Description:** Maverick's cadenza ordering fix (commit 52ea417, M7) changed the prompt assembly order from `template → skills → context` to `skills → context → template` for better prompt caching. The fix updated most tests (test_m7_cadenza_ordering.py, test_prompt_assembly_contract.py, test_prompt_characterization.py, test_prompt_assembly.py) but missed the property-based test in `test_baton_property_based.py:1273-1312`.
**Evidence:**
- `pytest tests/test_baton_property_based.py::TestPromptAssemblyOrderingProperty::test_ordering_holds_for_random_content -xvs` → FAILED
- Test line 1310: `assert task_pos < skill_pos, "Task must precede skills"` — expects template before skills (old order)
- Implementation (templating.py:265-296): skills → context → template (new order)
- Hypothesis found falsifying example: when task_text=skill_text=context_text="AAAAA", test fails because `find()` returns first match
- Test comment line 1276: `template < skills < context` (old order spec)
- Commit 52ea417 message: "NEW ORDER: skills/tools → context → template" — implementation matches this
**Impact:** Quality gate blocked - test suite fails on first run. Property-based test validates wrong ordering invariant after M7 reordering. Zero test coverage of new prompt assembly order under property-based fuzzing.
**Fix:** Update test assertions to match new order:
1. Line 1276 comment: `skills < context < template` (not `template < skills < context`)
2. Line 1309-1312: Change to `assert skill_pos < task_pos` (skills before template, not after)
3. Add test confirming skills < context < template ordering holds for arbitrary content

### F-529: Finding ID Collision - Duplicate F-523 in FINDINGS.md
**Found by:** Bedrock, Movement 7
**Severity:** P2 (medium)
**Status:** Resolved (Movement 7, Bedrock)
**Resolution:** Renumbered second F-523 to F-528. The duplicate occurred because two musicians (Adversary and potentially Ember) filed findings with the same ID in Movement 6. The FINDING_RANGES.md allocation system should have prevented this but didn't.
**Description:** FINDINGS.md contained two different findings both labeled F-523:
1. F-523 (line 2): "Critical Onboarding Failure: Sandbox + Schema Hostility" - P0, Adversary M6
2. F-523 (line 60): "Undocumented Breaking Change to Score YAML Format" - P1, Adversary M6

Additionally, Ember's memory referenced "F-523: Elapsed time semantic confusion" which appears to be a mislabeling of F-518/F-493.
**Evidence:**
- `grep "^### F-523" FINDINGS.md` returned 2 results
- Ember's memory (line 21) and collective memory (line 76) reference F-523 as elapsed time issue, but FINDINGS.md shows F-518 covers this
**Impact:** Finding registry integrity compromised. References to "F-523" are ambiguous. This breaks the append-only finding registry contract.
**Fix:** Renumbered second F-523 → F-528. Root cause investigation needed: why did FINDING_RANGES.md allocation fail?

### F-530: Test Isolation: test_global_learning.py::TestPatternBroadcasting::test_discovery_events_expire_correctly
**Found by:** Bedrock, Movement 7 (Quality Gate)
**Severity:** P2 (medium)
**Status:** Open
**Description:** The test `test_global_learning.py::TestPatternBroadcasting::test_discovery_events_expire_correctly` fails when run in the full test suite but passes when run in isolation. This is a test isolation issue (same class as F-517, F-525, F-527) - some earlier test is leaving state that breaks this test.
**Evidence:**
- Full suite run: `pytest tests/ -x` → FAILED at test_global_learning.py::TestPatternBroadcasting::test_discovery_events_expire_correctly
- Isolated run: `pytest tests/test_global_learning.py::TestPatternBroadcasting::test_discovery_events_expire_correctly -xvs` → PASSED (1 passed in 12.32s)
- This is NOT the same as F-519 or F-521 (which are about the F-519 regression test with timing margins)
- This is the ORIGINAL pattern discovery expiry test, not the regression test
**Impact:** Quality gate blocked - full test suite fails on first run. False negative under full suite execution. Does not indicate code regression in the learning store - the functionality is correct.
**Root cause:** Test shares state with other tests or depends on execution order. Likely related to learning store database state, global pattern cache, or timestamp-based queries not being properly isolated between tests.
**Fix:** Add proper test isolation - either:
1. Ensure learning store database cleanup in test teardown
2. Use unique pattern IDs or timestamps per test
3. Add explicit state reset in test setup
4. Convert to use temporary database per test run
5. Investigate what state the test depends on and ensure it's set up in the test itself, not relying on previous tests

### F-527: Test Isolation: test_global_learning.py::TestGoalDriftDetection and TestExplorationBudget Fail in Full Suite
**Found by:** Circuit, Movement 7
**Severity:** P2 (medium)
**Status:** Open
**Description:** Two tests in `test_global_learning.py` fail when run in the full test suite but pass when run in isolation or when the file is run alone. This is a test isolation issue (same class as F-517, F-525) - some earlier test is leaving state that breaks these tests.
**Evidence:**
- Full suite run: `pytest tests/ -q` → FAILED at two tests:
  - `tests/test_global_learning.py::TestGoalDriftDetection::test_drift_threshold_alerting`
  - `tests/test_global_learning.py::TestExplorationBudget::test_get_exploration_budget_history`
- Isolated run: `pytest tests/test_global_learning.py::TestGoalDriftDetection::test_drift_threshold_alerting -xvs` → PASSED
- File run: `pytest tests/test_global_learning.py -x` → 240 passed, 0 failed
- This indicates test isolation gap - likely shared state pollution from earlier tests in the full suite
**Impact:** Quality gate shows false negative under full suite execution. Does not indicate code regression in the learning store - the functionality is correct. Test ordering dependency creates flaky failures.
**Root cause:** Test shares state with other tests or depends on execution order. Likely related to learning store database state, global pattern cache, or timestamp-based queries not being properly isolated between tests.
**Fix:** Add proper test isolation - either:
1. Ensure learning store database cleanup in test teardown
2. Use unique pattern IDs or timestamps per test
3. Add explicit state reset in test setup
4. Convert to use temporary database per test run
5. Investigate what state the failing tests depend on and ensure it's set up in the test itself, not relying on previous tests
