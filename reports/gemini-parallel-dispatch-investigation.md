# Investigation: Baton Parallel Dispatch Failure

## 1. Summary
The core issue preventing parallel dispatch of stages 1, 4, 7, and 10 is that `BatonAdapter.extract_dependencies` completely ignores the `dependencies` map in the score configuration. Instead, it forcefully linearizes the execution based on movement numbers. Even though the YAML defines stages 1, 4, 7, and 10 as independent starting points, the adapter overrides this so that stage 4 depends on stage 3, 7 on 6, and 10 on 9. Consequently, the conductor only ever sees stage 1 as ready at the start, forcing a serial execution flow. 

## 2. Traced Flow
1. **Job Registration:** `manager.py` calls `BatonAdapter.register_job(...)`.
2. **Dependency Extraction:** `adapter.py` calls `extract_dependencies(config)`. This function explicitly assumes "all sheets in stage N+1 depend on all sheets in stage N" and rewires the DAG accordingly.
3. **Ready Check:** Stage 1 is registered with no dependencies. Stage 4 is incorrectly registered as depending on Stage 3, Stage 7 on 6, etc.
4. **Dispatch:** In the first cycle, `baton.get_ready_sheets()` checks dependencies and finds only Stage 1 is satisfied. `dispatch_ready()` dispatches it alone. Stages 4, 7, and 10 remain `PENDING` because their artificially injected dependencies are unmet.
5. **Pacing Block:** Once Stage 1 completes, `_handle_attempt_result()` schedules the pacing delay (`pause_between_sheets_seconds`, default 2s), setting `job.pacing_active = True`.
6. **Global Delay:** While pacing is active, `get_ready_sheets()` returns `[]` for the entire job, blocking Stage 2 (and everything else) for 2 seconds.

## 3. Root Cause
- **Primary Cause:** `BatonAdapter.extract_dependencies` (in `src/marianne/daemon/baton/adapter.py`) artificially rebuilds dependencies linearly based on stage order, entirely ignoring the `config.sheet.dependencies` graph.
- **Secondary Bottlenecks:**
  - **Global Pacing:** `BatonCore.get_ready_sheets` returns `[]` if `pacing_active` is True, blocking the *entire job* for 2 seconds after any completion, rather than just the specific instrument or chain.
  - **Ignored Job Concurrency:** The score's `parallel.max_concurrent: 4` is never passed to the baton during job registration. The adapter only passes the global `max_concurrent_sheets` (default 25).
  - **Model Concurrency Cap:** The `claude-opus-4-6` model profile strictly enforces `max_concurrent: 2`. Even if the dependency bug were fixed, the model concurrency limit would cap the parallel dispatch at 2 sheets, not 4.

## 4. Evidence
- **`src/marianne/daemon/baton/adapter.py:392`**: `extract_dependencies` iterates over `sorted_stages` and forces `deps[num] = list(prev_sheets)`, explicitly ignoring the user's YAML DAG.
- **`src/marianne/daemon/baton/core.py:832`**: `get_ready_sheets` returns `[]` if `job.pacing_active` is True, causing the 2-second serial delay between all sheets.
- **`src/marianne/instruments/builtins/claude-code.yaml:37`**: The `claude-opus-4-6` profile specifies `max_concurrent: 2`, overriding the default global ceiling.
- **`src/marianne/daemon/manager.py:2415`**: `adapter.register_job` is called without `max_concurrent`, silently dropping the score's requested parallel limit of 4.

## 5. Recommendation
- **Fix Dependency Extraction:** Update `extract_dependencies` in `BatonAdapter` to use `config.sheet.dependencies` when available instead of auto-generating a linear chain.
- **Scope Pacing:** Refactor `pacing_active` delays to apply to specific instruments or individual dependency chains instead of blocking the entire job globally.
- **Enforce Job Limits:** Pass the score's `parallel.max_concurrent` to the baton and respect it during dispatch.
- **Clarify Model Limits:** Ensure users are aware that even with parallelization fixed, `claude-opus-4-6` is hard-limited to 2 concurrent requests by its profile unless explicitly overridden.