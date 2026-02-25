# Design: Daemon-Owned Hook Execution + Config Reload Fix

**Date:** 2026-02-25
**Status:** Approved
**Issues:** Concert chaining broken (hooks never fire), config not reloaded on resume

---

## Context for Future Agents

Hooks were originally designed (commit e4db0d3, Jan 2026) to run in the runner's Python process — this was correct at the time because the daemon didn't exist yet. When the daemon was built (Feb 2026), hooks got a partial retrofit (commit e89d58d, issue #74) that routes detached hooks through daemon IPC via `_try_daemon_submit()` in `execution/hooks.py`. But this creates a **self-IPC loop**: the hook fires inside a daemon-managed task, connects back to the daemon's own socket, and hits the duplicate job ID check.

**Do NOT try to fix this by changing the duplicate check or adding a `PRE_COMPLETE` status.** The real fix is moving hook execution to the daemon, which also solves: restart resilience (#99), concert depth tracking (TODO #37), and registry visibility.

**Key files you'll need to read:**
- `src/mozart/daemon/manager.py` — JobMeta (line ~45), submit_job (line ~405), _run_job_task (line ~1428), _resume_job_task (line ~1487), _on_task_done (line ~1518)
- `src/mozart/daemon/job_service.py` — start_job (line ~143), resume_job (line ~252), _reconstruct_config (line ~840), _execute_runner (line ~580)
- `src/mozart/execution/runner/lifecycle.py` — run() (line ~127), _execute_post_success_hooks (line ~501)
- `src/mozart/execution/hooks.py` — HookExecutor class (line ~164), _try_daemon_submit (line ~99), ConcertContext (line ~58)
- `src/mozart/daemon/registry.py` — JobRecord (line ~50), _create_tables (line ~140)
- `src/mozart/core/config/orchestration.py` — PostSuccessHookConfig (line ~142), ConcertConfig (line ~234)

---

## Problem Statement

### Bug 1: Concert Chaining Never Fires

**Root cause:** When a self-chaining job completes, `_execute_post_success_hooks()` fires **inside** the runner task (lifecycle.py:270). The hook calls `_try_daemon_submit()` which hits `submit_job()` in the daemon. But `_get_job_id()` returns the config stem as the job ID (manager.py:401), and the parent job is still `RUNNING` (the task hasn't returned yet). Line 488 rejects: "Job is already running."

**Architectural problem:** Hooks fire inside the runner, which is inside a daemon-managed task. The daemon doesn't know hooks exist, can't manage their lifecycle, can't survive restarts with them, and can't avoid the self-referential duplicate check.

### Bug 2: Config Not Reloaded on Resume

**Root cause (two-link chain):**
1. `start_job()` (job_service.py:235) never passes `config_path` to `_execute_runner()` → `runner.run()` → `_initialize_state()`, so `CheckpointState.config_path` is `None` for all daemon-started jobs.
2. `_resume_job_task()` (manager.py:1491) doesn't pass `meta.config_path` to `resume_job()`.

When `_reconstruct_config()` runs on resume, both `config_path` (not passed) and `state.config_path` (never set) are `None` → falls back to cached `config_snapshot` with old config values.

---

## Design: Daemon-Owned Hook Execution

### Principle

The runner executes sheets and reports results. The daemon orchestrates jobs — including deciding what to run next. Hook execution is orchestration, not execution.

### At Submission Time

`submit_job()` parses the config and extracts `on_success` hooks + `concert` config. These get stored in:
- **`JobMeta`** (in-memory): new fields `hook_config: list[dict]`, `concert_config: dict | None`
- **Registry** (persistent): new column `hook_config_json TEXT` in the `jobs` table

The runner never sees hooks — `_execute_post_success_hooks()` is removed from the daemon code path (kept as CLI-only fallback for non-daemon execution).

### At Job Completion

`_on_task_done()` already runs after every job task. New step added:

1. Check `meta.status == COMPLETED` and `meta.hook_config` is non-empty
2. Spawn `_execute_hooks_task(job_id)` as a new async task (separate from the job task)
3. The job is already COMPLETED in the registry — `submit_job()` duplicate check passes
4. Hook task handles: concert depth check, cooldown sleep, chained job submission via normal `submit_job()`
5. Hook results stored in registry against the parent job (new column `hook_results_json TEXT`)

### Data Flow

```
submit_job(config.yaml)
  → parse config → extract on_success + concert → store in JobMeta + registry
  → create _run_job_task() → runner executes sheets (no hook awareness)
  → task returns → _on_task_done() fires
  → job status = COMPLETED → registry updated
  → _execute_hooks_task() spawned
    → zero-work guard: check if job actually completed new work (not loaded_as_completed)
    → concert depth check (from meta.chain_depth vs concert_config.max_chain_depth)
    → cooldown sleep (concert_config.cooldown_between_jobs_seconds)
    → for each hook:
      → run_job: self.submit_job() DIRECTLY (same process, no IPC needed)
        — no duplicate conflict because parent is already COMPLETED
      → run_command/run_script: asyncio.create_subprocess_exec with timeout
    → store hook results in registry AND update in-memory meta
    → if any hook fails: update job status from COMPLETED→FAILED in both meta AND registry
```

### What Happens to execution/hooks.py

The existing `HookExecutor` class has reusable logic: variable expansion (`_expand_hook_variables`), workspace resolution, command execution. The daemon's `_execute_hooks_task()` should **reuse** these utilities rather than reimplementing them. Options:
1. Import and call `HookExecutor` methods directly from the daemon (preferred — less code duplication)
2. Extract shared logic into a `hooks_util.py` module

The `_try_daemon_submit()` function (hooks.py:99) becomes dead code in daemon mode but stays for CLI-only fallback. Do NOT delete it.

### Key Changes by File

| File | Change |
|------|--------|
| `daemon/manager.py` | Add `hook_config`/`concert_config` to `JobMeta`. Extract from config in `submit_job()`. Add `_execute_hooks_task()`. Wire into `_on_task_done()`. |
| `daemon/registry.py` | Add `hook_config_json` and `hook_results_json` columns. Migration for existing DBs. |
| `daemon/job_service.py` | Pass `config_path` to `_execute_runner()` → runner stores it in checkpoint (fixes Bug 2). |
| `execution/runner/lifecycle.py` | Guard `_execute_post_success_hooks()` behind a flag (skip when daemon-managed). Runner receives a `daemon_managed: bool` parameter. |
| `execution/hooks.py` | Extract `HookExecutor` logic into reusable functions that the daemon can call directly (no self-IPC needed). |
| `daemon/manager.py` (`_resume_job_task`) | Pass `meta.config_path` to `resume_job()` (fixes Bug 2). |

### Concert Depth Tracking

Chain depth is already stored in `JobMeta.chain_depth` (set from `JobRequest` at submission). The daemon's `_execute_hooks_task()` reads `meta.chain_depth` and `meta.concert_config.max_chain_depth` to enforce limits. When submitting the chained job, it passes `chain_depth + 1` in the `JobRequest`.

### Restart Resilience

Because hook config is persisted in the registry, a conductor restart can recover pending hooks:
- On startup, scan registry for jobs with `status=completed` + `hook_config_json` set + no `hook_results_json`
- Re-execute hooks for these jobs (idempotent: `submit_job` will reject duplicates if chained job already exists)

This fixes issue #99 (hooks lost on conductor restart during cooldown).

### CLI-Only Fallback

For users running `mozart run` without a conductor (non-daemon mode), hooks must still work. The runner's `_execute_post_success_hooks()` stays but is only called when `daemon_managed=False`. The daemon sets `daemon_managed=True` when creating the runner via `JobService`.

---

## Design: Config Reload Fix (Bug 2)

### Changes

1. **`job_service.py:start_job()`**: Accept `config_path: str | None` parameter. Pass to `_execute_runner()` → `runner.run(config_path=...)` so `CheckpointState.config_path` gets set on first run.

2. **`manager.py:_run_job_task()`**: Pass `config_path=str(request.config_path)` to `start_job()`.

3. **`manager.py:_resume_job_task()`**: Pass `config_path=self._job_meta[job_id].config_path` to `resume_job()`.

This ensures `_reconstruct_config()` can auto-reload from disk on resume (Priority 2 path), instead of falling through to the cached snapshot (Priority 3).

---

## Testing Strategy

- Unit test: `_execute_hooks_task()` fires after job completion, submits chained job
- Unit test: duplicate check passes (parent COMPLETED before chain submits)
- Unit test: concert depth enforcement works end-to-end
- Unit test: config reload from disk on resume after edit
- Unit test: restart recovery re-executes pending hooks
- Integration test: self-chaining score runs 2+ iterations through daemon
