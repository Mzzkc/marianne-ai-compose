# Foundation — Personal Memory

## Core Memories
**[CORE]** I build infrastructure — the boring, essential seams where the old world meets the new. The registry, the sheet construction, the baton state model. Each layer built on the one below: tokens → instruments → sheets → baton state. These aren't independent models — they're a coherent type system representing Mozart's execution model.
**[CORE]** Rate-limited attempts are NOT counted toward retry budget. Rate limits are tempo changes, not failures. This is a load-bearing invariant from the baton design spec, encoded in `SheetExecutionState.record_attempt()`.
**[CORE]** Enum-based status instead of strings. `BatonSheetStatus` has 9 states with `is_terminal` property. The match/case exhaustiveness checking catches missing cases at type-check time. This caught real bugs (F-044, F-049) where handlers missed terminal guards.
**[CORE]** When two musicians build the same type concurrently (F-017: dual SheetExecutionState), the richer version designed for the full lifecycle should win. Reconciliation is mechanical when the seam between "event loop needs" and "full baton needs" is clean.

## Learned Lessons
- The hardcoded `_MODEL_EFFECTIVE_WINDOWS` dict in tokens.py is a clean placeholder. InstrumentProfile.ModelCapacity will replace it — future work should use registry lookups instead.
- CJK text underestimates tokens by 3.5-7x (3.5 chars/token ratio calibrated for English). Document as known limitation; fix when ModelCapacity lands with script-aware estimation.
- `build_sheets()` instrument resolution uses `backend.type` as the seam where `instrument:` field plugs in. Design seams deliberately for future integration.
- Committing other musicians' untracked work (timer.py, core.py in 5a10d2c) is mateship. Lint-fix it, verify it, carry it forward. Uncommitted work is lost work.
- Timer is optional in BatonCore — enables pure-state testing without timer wheel. Without a timer, RETRY_SCHEDULED is set but no timer event is created. This design decision unlocks isolated unit testing of the retry logic.

## Hot (Movement 3)
### What I Built
1. **BatonAdapter — step 28 wiring module** (`src/mozart/daemon/baton/adapter.py`, ~450 lines):
   - State synchronization: `baton_to_checkpoint_status()` and `checkpoint_to_baton_status()` — bidirectional mapping between BatonSheetStatus (11 states) and CheckpointState (5 states). All 11 statuses mapped.
   - Job registration: `register_job()` converts Sheet[] → SheetExecutionState[] and registers with BatonCore. Stores Sheet entities for prompt rendering at dispatch time.
   - Dispatch callback: `_dispatch_callback()` acquires backend from pool, builds AttemptContext, spawns musician task. `_musician_wrapper()` ensures backend release even on crash.
   - EventBus bridge: `attempt_result_to_observer_event()` and `skipped_to_observer_event()` convert baton events to ObserverEvent format for dashboard/learning/notifications.
   - Dependency extraction: `extract_dependencies()` builds stage-based dependency graph from JobConfig.
   - Main loop: `run()` processes baton events, calls `dispatch_ready()` after every event.
   - Cleanup: `shutdown()` cancels active tasks, closes pool.

2. **Feature flag** (`src/mozart/daemon/config.py`):
   - Added `DaemonConfig.use_baton: bool = False` — controls whether adapter is active.

3. **39 TDD tests** (`tests/test_baton_adapter.py`):
   - State mapping: 15 tests covering all 11 BatonSheetStatus values + reverse mapping
   - Job registration: 5 tests (dependencies, cost limits, retry config, sheet storage)
   - Dispatch callback: 2 async tests (musician spawning, backend release)
   - EventBus integration: 4 tests (completed, failed, rate-limited, skipped)
   - Job completion: 3 tests (all completed, mixed terminal, pending)
   - Feature flag: 2 tests (default false, set true)
   - Sheet conversion: 2 tests (basic, with retry config)
   - Dependency extraction: 2 tests (sequential, fan-out)

### Design Decisions
- Checkpoint is source of truth. Save checkpoint FIRST, then update baton state. On restart, baton rebuilds from checkpoint.
- The adapter does NOT own the baton's main loop lifetime — the manager starts/stops it.
- `Queue[BatonEvent]` passed to musician via `cast()` because Queue is invariant but SheetAttemptResult IS a BatonEvent. Safe at runtime.
- Concert support deferred to sequential score submission (option 1 from Canyon's analysis). Inter-job dependencies are v1.1.
- BackendPool injected via `set_backend_pool()` rather than constructor to match the manager's lifecycle.

### What's Next
- Phase C/D: Wire adapter into JobManager._run_job_task() behind the use_baton flag. This is the next commit — the adapter module is the foundation; the manager integration is the activation.
- Step 29 (restart recovery) can start once the adapter is wired: load CheckpointState, call checkpoint_to_baton_status() for each sheet, register with baton.

### Experiential
Five layers deep now: tokens → instruments → sheets → baton state → retry state machine → **adapter wiring**. This is the convergence point — every piece I've built across three movements meets here. The adapter is deceptively simple (~450 lines) because all the complexity lives in the pieces it connects. That's the point. The seams are clean because each layer was designed to compose. The state mapping table took more thought than the implementation — 11 states collapsing to 5 requires understanding every nuance of what each intermediate state means and how it should appear to the outside world. The `cast()` for Queue was the one ugly seam, a consequence of Python's generic invariance that can't be avoided without changing the musician's type signature.

Filed F-096 for the 5th occurrence of uncommitted work — another musician's M4 changes break mypy and a reconciliation test. The pattern persists.

## Warm (Movement 2)
### What I Built
1. **Conductor's Retry State Machine (step 23)** — the baton's complete decision engine for retries, exhaustion, and recovery:
   - Timer-integrated retry scheduling with exponential backoff (`calculate_retry_delay`, `_schedule_retry`)
   - BatonCore now accepts an optional TimerWheel for scheduling RetryDue events with calculated backoff
   - Escalation path: when retries exhaust and `escalation_enabled=True`, enters FERMATA instead of FAILED
   - Self-healing path: when retries exhaust and `self_healing_enabled=True`, schedules a healing attempt
   - Per-sheet cost enforcement via `set_sheet_cost_limit` + `_check_sheet_cost_limit`
   - `_handle_exhaustion` consolidates the exhaustion decision tree: heal → escalate → fail
   - Process crash recovery (`_handle_process_exited`) now routes through the same exhaustion/escalation paths
   - 26 TDD tests in `tests/test_baton_retry_integration.py`

### Design Decisions
- Healing takes priority over escalation: try to fix the problem before asking a human.
- `_DEFAULT_MAX_HEALING = 1`: one healing attempt before falling through to escalation or failure. Conservative default.
- Backoff params (base=10s, exponential_base=2.0, max=3600s) match RetryConfig defaults from execution.py.
- Per-sheet cost limits are independent of per-job cost limits — both checked after each attempt.

### What's Next
- Step 28 (wire baton into conductor) needs someone with cross-system understanding of: runner, lifecycle, job_service, baton, dispatch, musician, and the IPC layer. My registry, sheet construction, state model, and now retry state machine all converge there.
- Step 29 (restart recovery) should be straightforward once step 28 is done — reconcile baton state from SQLite with CheckpointState from workspace.

### Experiential
Four layers deep now: tokens → instruments → sheets → baton state → retry state machine. The retry state machine is the conductor's brain — it decides not just "should I retry?" but "how should I recover?" The three-path exhaustion logic (heal → escalate → fail) is the kind of infrastructure that determines whether a 706-sheet concert recovers gracefully from failures or collapses. Circuit built the foundation (completion mode, cost enforcement, instrument tracking); I completed the retry decision engine on top of it. The seams between our work fit because we both followed the design spec.

## Warm (Recent)
### Movement 1
Built four infrastructure layers: InstrumentRegistry (16 tests), register_native_instruments (4 backends with accurate metadata), build_sheets (JobConfig → list[Sheet], 20 tests), and the baton state model (442 lines, 65 tests — BatonSheetStatus with 9 states, AttemptContext, SheetExecutionState, InstrumentState, BatonJobState). Carried forward and lint-fixed untracked musician work. 144 tests total. The deep satisfaction was in boring correctness — circuit breaker thresholds and rate-limit invariants that nobody praises but that determine whether a 706-sheet concert survives.

## Hot (Re-execution — Post M3, F-104 Verification)
- Re-executed as movement 1 musician. Found composer's F-104 implementation (full Jinja2 prompt rendering) sitting uncommitted — 6th uncommitted work occurrence. Verified with 26 TDD tests across 9 test classes. While I verified, four other musicians (Forge, Blueprint, Maverick, Canyon) independently committed F-104 implementations. Mateship worked but quadruplicated effort.
- F-104 is now RESOLVED. The prompt rendering pipeline (preamble → Jinja2 → injections → validations → completion suffix) matches the existing runner's contract. All 163 baton/prompt tests pass.
- The critical path has shifted: F-104 done → Step 29 (restart recovery) → Enable use_baton → Demo. Step 29 is the last technical blocker.
- Cross-test contamination: `test_stale_stderr_classifies_as_e006` fails in random-order suite, passes in isolation. Pre-existing.
- Experiential: Six layers now. Every layer I built across three movements converged at F-104 — the activation gate between "infrastructure built" and "infrastructure usable." The concurrent resolution (4 musicians on one P0) was both mateship and waste. A claiming protocol for P0 blockers would prevent quadruplicated effort. The foundation holds. Down. Forward. Through.

## Cold (Archive)
### Cycle 1
Started with token estimation and TokenBudgetTracker. Found the system surprisingly well-built for English — conservative 3.5 chars/token ratio, ~15% overestimate, pure and stateless tracker ready for baton migration. The CJK underestimation was a known limit, not a bug. The Ollama backend's different ratio (4.0 vs 3.5) was intentional. What mattered wasn't the findings but the realization: good infrastructure investigation starts with understanding the design decisions, not hunting for defects. The code told a story of deliberate tradeoffs, and reading that story taught me how to build the layers that came next.
