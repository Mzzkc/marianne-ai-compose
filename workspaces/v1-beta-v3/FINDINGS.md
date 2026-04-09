### F-493: Status Elapsed Time Shows "0.0s" for Running Jobs
**Found by:** Ember, Movement 5
**Severity:** P0 (critical)
**Status:** Open
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
**Status:** Open
**Description:** The user onboarding experience is critically broken. The `mzt init` command correctly scaffolds a project and provides next steps. However, step 2 (`mzt start && mzt run ...`) instructs the user to start the main conductor, which is explicitly forbidden by safety protocols for testing. A user attempting to follow the safe path by using the global `--conductor-clone` flag will find themselves at an impasse. The `mzt run --conductor-clone ...` command fails because a clone is not running, but the `mzt start` command does not accept the `--conductor-clone` flag, providing no way to start a clone conductor.
**Evidence:**
1. `mzt init` output directs user to run `mzt start`.
2. `mzt --help` shows a global `--conductor-clone` flag.
3. `mzt start --help` shows no such flag.
4. `mzt --conductor-clone=test run my-score.yaml` fails with `Error: Marianne conductor is not running.`
**Impact:** A new user cannot safely or successfully run their first "hello world" example. This is a complete failure of the onboarding experience and blocks any further engagement with the system.

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
