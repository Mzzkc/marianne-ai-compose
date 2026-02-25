# Mozart AI Compose - Active Context

**Last Updated:** 2026-02-25
**Current Phase:** Implementing daemon-owned hook execution + config reload fix
**Status:** Design approved, implementation plan written, ready to execute
**Previous Session:** Deep investigation of two bugs, root causes found, design + plan committed

---

## Current State

### What's Done (This Session — 2026-02-25)

**Bug Investigation — Concert Chaining Never Fires:**
- Traced full code path from runner completion through hook execution through daemon IPC
- Found root cause: `_get_job_id()` (manager.py:401) returns config stem as job ID. When hook fires inside runner task (still RUNNING), `submit_job()` rejects at line 488: "Job is already running"
- This is an architectural problem, not a point fix — hooks fire inside the runner task before the daemon knows the job is done
- Design: move hook execution from runner to daemon's `_on_task_done()` callback

**Bug Investigation — Config Not Reloaded on Resume:**
- Found two-link broken chain: `start_job()` never passes `config_path` to runner (so CheckpointState.config_path is None), AND `_resume_job_task()` doesn't pass `meta.config_path` to `resume_job()`
- Result: `_reconstruct_config()` falls back to cached snapshot with old config values
- Fix is 3 lines across 2 files (Phase 1 of implementation plan)

**Documents Created:**
- `docs/plans/2026-02-25-daemon-owned-hooks-and-config-reload-design.md` — Full design with context, data flow, file-by-file changes
- `docs/plans/2026-02-25-daemon-owned-hooks-and-config-reload-implementation.md` — 6-phase implementation plan with gotchas

### Known Bugs / Open Issues

- **Concert chaining broken** — hooks never fire in daemon mode (THIS SESSION — design ready)
- **Config reload broken** — config_path not wired through daemon (THIS SESSION — fix is Phase 1)
- **#102 — Observer integration gaps**: Timeline persistence, top wiring, snapshot completeness
- **#99 — on_success hooks lost on conductor restart**: Fixed by daemon-owned hooks design
- **#98 — `--reload-config` silently dropped**: IPC pipeline doesn't thread reload through
- **#93 — Pause only fires at sheet boundaries**: Can't pause during retry loops
- **Concert depth tracking** (TODO #37): Fixed by daemon-owned hooks design

---

## Next Steps — Implementation

**Read the design and plan docs FIRST:**
1. `docs/plans/2026-02-25-daemon-owned-hooks-and-config-reload-design.md`
2. `docs/plans/2026-02-25-daemon-owned-hooks-and-config-reload-implementation.md`

**Execute in order:**
1. **Phase 1: Config reload fix** — 3 lines across `job_service.py` and `manager.py`. Independent, low risk, immediately testable.
2. **Phase 2: Registry schema** — Add `hook_config_json` and `hook_results_json` columns + migration
3. **Phase 3: Extract hook config** — Store hooks in JobMeta/registry at submission time
4. **Phase 4: Daemon hook execution** — `_execute_hooks_task()` in `_on_task_done()`. This is the core fix.
5. **Phase 5: Suppress runner hooks** — `daemon_managed` flag prevents double execution
6. **Phase 6: Restart recovery** — Optional, can defer

---

## Key Files for Next Session

| Purpose | Read This |
|---------|-----------|
| Design doc | `docs/plans/2026-02-25-daemon-owned-hooks-and-config-reload-design.md` |
| Implementation plan | `docs/plans/2026-02-25-daemon-owned-hooks-and-config-reload-implementation.md` |
| Job manager (primary target) | `src/mozart/daemon/manager.py` (JobMeta ~45, submit_job ~405, _run_job_task ~1428, _resume_job_task ~1487, _on_task_done ~1518) |
| Job service (config_path fix) | `src/mozart/daemon/job_service.py` (start_job ~143, resume_job ~252, _execute_runner ~580) |
| Runner lifecycle (suppress hooks) | `src/mozart/execution/runner/lifecycle.py` (run ~127, _execute_post_success_hooks ~501) |
| Hook executor (reuse logic) | `src/mozart/execution/hooks.py` (HookExecutor ~164, _try_daemon_submit ~99) |
| Registry (schema changes) | `src/mozart/daemon/registry.py` (JobRecord ~50, _create_tables ~140) |
| Config models | `src/mozart/core/config/orchestration.py` (PostSuccessHookConfig ~142, ConcertConfig ~234) |

---

*Context preserved for instant next-session pickup.*
