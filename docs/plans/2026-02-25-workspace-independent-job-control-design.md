# Workspace-Independent Job Control

**Date:** 2026-02-25
**Status:** Approved

---

## Problem

Mozart's daemon tracks full job state in memory (`_job_meta`, `_live_states`) and SQLite (`JobRegistry.checkpoint_json`), but `mozart pause` requires the workspace filesystem for:

1. **State validation** — reads `CheckpointState` from workspace to verify job is RUNNING (unnecessary; daemon already knows)
2. **Signal delivery** — drops `.mozart-pause-{id}` file that the runner polls at sheet boundaries

If the workspace is moved or deleted mid-run, the job becomes uncontrollable: pause fails, and there's no `cancel` CLI command as a fallback.

## Root Cause

The file-based pause signal is a vestige of the pre-daemon architecture where CLI and runner were separate processes sharing only a workspace directory. The daemon changed the execution model (runner runs inside the daemon's asyncio loop), but the control plane was never updated.

## Design

### 1. In-Process Pause Event

Replace the file-based signal with an `asyncio.Event` for daemon-mode pause.

- `JobManager` owns `_pause_events: dict[str, asyncio.Event]` — one per running job
- `JobManager.pause_job()` sets the event directly (no JobService delegation, no filesystem)
- The event is created when a job starts (`_run_managed_task`) and passed through `JobService._execute_runner()` to the runner constructor
- `_check_pause_signal()` checks the event first; file signal is secondary (non-daemon compat only)

### 2. `mozart cancel` CLI Command + `pause --force`

- New `cancel.py` CLI command: `mozart cancel <job-id>` routes to `job.cancel` IPC
- `mozart pause --force <job-id>` also routes to `job.cancel` IPC
- `cancel_job()` already exists in JobManager — uses asyncio task cancellation
- CancelledError handling in lifecycle.py already rolls back in-progress sheet and saves state

### 3. State-Save Resilience During Pause

When `_handle_pause_request()` fires and the workspace state save fails:
- Catch the write error
- Still mark job as PAUSED in the CheckpointState object (in-memory)
- The `_PublishingBackend` mirrors state to `_live_states` and the registry persists `checkpoint_json`
- Log a warning that workspace state save failed but job is paused
- Exit cleanly as PAUSED, not crash

### 4. `pause_job()` Refactored (JobManager)

Before:
```
pause_job(job_id, workspace)
  → validate meta (in-memory)
  → delegate to JobService.pause_job(job_id, workspace)
    → _find_job_state(job_id, workspace)  # reads workspace
    → touch(.mozart-pause-{id})           # writes workspace
```

After:
```
pause_job(job_id)
  → validate meta (in-memory: _job_meta status + _jobs task alive)
  → _pause_events[job_id].set()          # in-process signal
  → return True
```

No workspace parameter. No filesystem access. No JobService involvement.

## File Changes

| File | Change |
|------|--------|
| `src/mozart/daemon/manager.py` | Add `_pause_events` dict. Create event in `_run_managed_task()`. Refactor `pause_job()` to set event, remove workspace param. Clean up event in `_on_task_done()`. |
| `src/mozart/daemon/job_service.py` | `_execute_runner()` accepts `pause_event: asyncio.Event` param, passes to runner. |
| `src/mozart/execution/runner/base.py` | Constructor accepts optional `pause_event: asyncio.Event`. `_check_pause_signal()` checks event first, file second. `_handle_pause_request()` catches state-save errors and still exits as PAUSED. |
| `src/mozart/cli/commands/cancel.py` | New file — `mozart cancel <job-id>` command. |
| `src/mozart/cli/commands/pause.py` | Add `--force` flag routing to `job.cancel`. Remove workspace from daemon-route params. |
| `src/mozart/cli/commands/__init__.py` | Export `cancel`. |
| `src/mozart/cli/__init__.py` | Register `cancel` on app. |
| `src/mozart/daemon/process.py` | `handle_pause` no longer passes workspace. |

## What Doesn't Change

- `JobService.pause_job()` remains for direct-mode callers (deprecated path, not removed)
- File-based signal in runner remains as secondary check (non-daemon compat)
- Cancel semantics (asyncio task cancellation + CancelledError handling)
- Resume, modify, status, diagnose — untouched
- Registry schema — no migration needed
- `_pause_job_direct()` in CLI — stays with deprecation note (separate concern)

## TDF Notes

- **COMP↔CULT**: File signal exists because CLI/runner were separate processes pre-daemon. That architecture is gone.
- **COMP↔EXP**: Keeping both paths (event + file) as co-equal mechanisms creates maintenance burden. Event is authoritative in daemon mode.
- **COMP↔EXP**: State-save failure during pause was a hidden risk — runner would crash instead of pausing cleanly. Fixed by catching write errors and relying on registry.
- **SCI↔CULT**: Nothing in production uses non-daemon execution. File signal removal is a separate future concern.
