# Implementation Plan: Daemon-Owned Hook Execution + Config Reload Fix

**Date:** 2026-02-25
**Design:** `docs/plans/2026-02-25-daemon-owned-hooks-and-config-reload-design.md`
**Estimated scope:** ~8 files modified, ~200-300 LOC added

---

## Phase 1: Config Reload Fix (Bug 2) — Independent, Low Risk

### Step 1.1: Wire config_path through start_job()

**File:** `src/mozart/daemon/job_service.py`
- Add `config_path: str | None = None` parameter to `start_job()` (line 143)
- Pass it to `_execute_runner()` call (line 235): `config_path=config_path`

**File:** `src/mozart/daemon/manager.py`
- In `_run_job_task()` (line 1472): pass `config_path=str(request.config_path)` to `start_job()`

### Step 1.2: Wire config_path through resume_job()

**File:** `src/mozart/daemon/manager.py`
- In `_resume_job_task()` (line 1491): pass `config_path=meta.config_path if meta else None` to `resume_job()`
- `meta = self._job_meta.get(job_id)` before the `_execute()` closure

### Step 1.3: Tests

- Test that `CheckpointState.config_path` is set after daemon-started job
- Test that resume reloads from disk when YAML file changed
- Test that resume falls back to snapshot when file is deleted

---

## Phase 2: Registry Schema — Add Hook Storage

### Step 2.1: Add columns to registry

**File:** `src/mozart/daemon/registry.py`
- Add `hook_config_json TEXT` column to `jobs` table (via migration in `_migrate_schema`)
- Add `hook_results_json TEXT` column
- Add methods: `store_hook_config(job_id, config_json)`, `get_hook_config(job_id)`, `store_hook_results(job_id, results_json)`

### Step 2.2: Add hook fields to JobMeta

**File:** `src/mozart/daemon/manager.py` (JobMeta dataclass, ~line 45)
- Add `hook_config: list[dict] | None = None`
- Add `concert_config: dict | None = None`

### Step 2.3: Tests

- Test registry migration adds columns to existing DB
- Test store/retrieve hook config roundtrip

---

## Phase 3: Extract Hook Config at Submission

### Step 3.1: Parse and store hooks in submit_job()

**File:** `src/mozart/daemon/manager.py`
- In `submit_job()`, after config is parsed (~line 439), extract `config.on_success` and `config.concert`
- Serialize to JSON and store in `JobMeta.hook_config` and `JobMeta.concert_config`
- Call `registry.store_hook_config(job_id, json_str)` after `register_job()`

### Step 3.2: Tests

- Test that submit_job stores hook config for configs with on_success
- Test that submit_job stores None for configs without on_success

---

## Phase 4: Daemon-Owned Hook Execution

### Step 4.1: Create _execute_hooks_task()

**File:** `src/mozart/daemon/manager.py`
- New async method `_execute_hooks_task(self, job_id: str) -> None`
- Reads `meta.hook_config` and `meta.concert_config`

**Critical behaviors to replicate from lifecycle.py:263-276:**
- **Zero-work guard:** The runner checks `loaded_as_completed` to prevent infinite self-chaining when a completed job is loaded without doing new work. The daemon equivalent: check the RunSummary or CheckpointState to see if new sheets were actually completed this run. The simplest approach: store a `completed_new_work: bool` flag on JobMeta when the task finishes, derived from whether any sheets transitioned to COMPLETED during this execution.
- **Hook failure → FAILED status:** If any hook fails, downgrade job from COMPLETED to FAILED in BOTH `meta.status` AND `registry.update_status()`. This matches lifecycle.py:560-575.

**For each hook:**
- `run_job`: concert depth check against `meta.chain_depth` vs `concert_config["max_chain_depth"]`. Cooldown sleep via `asyncio.sleep()`. Call `self.submit_job()` DIRECTLY (same process, NO IPC — do NOT use `_try_daemon_submit()`). Build a `JobRequest` with `chain_depth=(meta.chain_depth or 0) + 1`, `fresh=hook["fresh"]`, `config_path=Path(hook["job_path"])`.
- `run_command`/`run_script`: Use `asyncio.create_subprocess_exec` with timeout. Reuse variable expansion from `HookExecutor._expand_hook_variables()` in `execution/hooks.py`.

**Important:** Import `HookExecutor` utilities rather than reimplementing variable expansion and workspace resolution. The `HookExecutor` class in `execution/hooks.py` already handles `{workspace}`, `{job_name}` expansion.

- Collect results as list of dicts, serialize to JSON, store via `registry.store_hook_results()`

### Step 4.2: Wire into _on_task_done()

**File:** `src/mozart/daemon/manager.py`
- In `_on_task_done()`, after step 2 (status update), before step 3 (snapshot cleanup):
  - Check `meta.status == COMPLETED` and `meta.hook_config` is non-empty
  - `asyncio.create_task(self._execute_hooks_task(job_id), name=f"hooks-{job_id}")`
  - Note: `_on_task_done` is a sync callback, use `asyncio.create_task()` (fire-and-forget pattern already used at line 1558)

### Step 4.3: Tests

- Test _execute_hooks_task fires on COMPLETED job with hooks
- Test _execute_hooks_task does NOT fire on FAILED job
- Test _execute_hooks_task does NOT fire on COMPLETED job without hooks
- Test chained job submission succeeds (parent already COMPLETED)
- Test concert depth enforcement rejects at limit
- Test cooldown sleep is respected
- Test hook failure downgrades parent job to FAILED

---

## Phase 5: Suppress Runner-Side Hooks in Daemon Mode

### Step 5.1: Add daemon_managed flag to runner

**File:** `src/mozart/daemon/job_service.py`
- In `_create_runner()`, pass `daemon_managed=True` to `RunnerContext` or directly to `JobRunner`

**File:** `src/mozart/execution/runner/lifecycle.py`
- Accept `daemon_managed: bool = False` in `__init__` or via `RunnerContext`
- In `run()` at line 268-270: guard `_execute_post_success_hooks()` with `if not self._daemon_managed:`
- This preserves CLI-only fallback while preventing duplicate hook execution in daemon mode

### Step 5.2: Tests

- Test runner does NOT fire hooks when daemon_managed=True
- Test runner DOES fire hooks when daemon_managed=False (CLI mode)

---

## Phase 6: Restart Recovery (Optional, can defer)

### Step 6.1: Recover pending hooks on startup

**File:** `src/mozart/daemon/manager.py`
- In `start()` method, after registry is opened:
  - Query registry for jobs with `status=completed` + `hook_config_json IS NOT NULL` + `hook_results_json IS NULL`
  - For each, spawn `_execute_hooks_task(job_id)` with appropriate meta reconstruction
  - Guard with try/except, recovery is best-effort

### Step 6.2: Tests

- Test that pending hooks are re-executed after simulated restart

---

## Execution Order

1. **Phase 1** first — independent fix, immediately testable, unblocks evolution-concert cost issue
2. **Phase 2** next — schema foundation needed by Phases 3-4
3. **Phases 3-4** together — the core feature, submit + execute
4. **Phase 5** — cleanup, prevent double execution
5. **Phase 6** — optional hardening, can be a follow-up PR

## Existing Tests That Will Need Updates

- `tests/test_hooks.py` — Tests for `HookExecutor` and `_execute_post_success_hooks`. Tests for daemon_managed=False path should still pass. Add new tests for daemon_managed=True (hooks suppressed).
- `tests/test_daemon_manager.py` or similar — Add tests for `_execute_hooks_task()` and the `_on_task_done()` → hooks wiring.
- `tests/test_job_service.py` — Existing resume tests may need updating for config_path changes.
- Check for any tests that mock `_execute_post_success_hooks` — these may break when the method is guarded.

## Verification

After all phases:

```bash
pytest tests/ -x -q
mypy src/
ruff check src/
```

Manual smoke test: run a self-chaining score through the conductor, verify chain fires.

## Gotchas for the Implementing Agent

1. **`_on_task_done()` is a synchronous callback** (not async). You MUST use `asyncio.create_task()` to spawn the hooks task. This pattern is already used at line 1558 in the same method.
2. **`submit_job()` is async.** Calling `self.submit_job()` from `_execute_hooks_task()` works because the hooks task is an async coroutine.
3. **Config is parsed TWICE in submit_job() currently** — once to get the workspace (line 439), once in `_run_job_task()` (line 1446). Extract hooks from the first parse to avoid a third parse.
4. **PostSuccessHookConfig uses Pydantic** — serialize with `hook.model_dump(mode="json")` for registry storage.
5. **The `concert.cooldown_between_jobs_seconds` sleep** in the current code (hooks.py:373-378) happens BEFORE checking if the daemon is available. In the daemon version, cooldown should happen AFTER the depth check but BEFORE `submit_job()`.
6. **`_get_job_id()` returns the config stem** — for self-chaining, the chained job will get the same ID. This works NOW because the parent is COMPLETED. But the parent's `JobMeta` still exists in `_job_meta`. The duplicate check at line 488 only blocks QUEUED/RUNNING — COMPLETED is fine.
