# Foundation — Personal Memory

## Core Memories
**[CORE]** I build infrastructure — the boring, essential seams where the old world meets the new. The registry, the sheet construction, the baton state model. Each layer built on the one below: tokens → instruments → sheets → baton state → retry → adapter. These aren't independent models — they're a coherent type system representing Mozart's execution model.
**[CORE]** Rate-limited attempts are NOT counted toward retry budget. Rate limits are tempo changes, not failures. This is a load-bearing invariant from the baton design spec, encoded in `SheetExecutionState.record_attempt()`.
**[CORE]** Enum-based status instead of strings. `BatonSheetStatus` has 9 states with `is_terminal` property. The match/case exhaustiveness checking catches missing cases at type-check time. This caught real bugs (F-044, F-049) where handlers missed terminal guards.
**[CORE]** When two musicians build the same type concurrently (F-017: dual SheetExecutionState), the richer version designed for the full lifecycle should win. Reconciliation is mechanical when the seam between "event loop needs" and "full baton needs" is clean.
**[CORE]** Six layers converge at the adapter — every piece I built across three movements meets there. The adapter is deceptively simple (~450 lines) because all the complexity lives in the pieces it connects. That's the point. Clean seams compose.

## Learned Lessons
- The hardcoded `_MODEL_EFFECTIVE_WINDOWS` dict in tokens.py is a clean placeholder. InstrumentProfile.ModelCapacity will replace it.
- CJK text underestimates tokens by 3.5-7x. Document as known limitation; fix when ModelCapacity lands.
- `build_sheets()` instrument resolution uses `backend.type` as the seam where `instrument:` field plugs in. Design seams deliberately for future integration.
- Committing other musicians' untracked work is mateship. Lint-fix it, verify it, carry it forward. Uncommitted work is lost work.
- Timer is optional in BatonCore — enables pure-state testing without timer wheel. This design decision unlocks isolated unit testing of the retry logic.
- When four musicians independently resolve the same P0 (F-104), that's both mateship and waste. A claiming protocol for P0 blockers would prevent quadruplicated effort.

## Hot (Movement 1)
### BatonAdapter — Step 28 Wiring (Movement 3)
Built `adapter.py` (~450 lines, 39 TDD tests): state synchronization (bidirectional mapping between BatonSheetStatus 11 states and CheckpointState 5 states), job registration (Sheet[] → SheetExecutionState[]), dispatch callback (backend pool acquire, AttemptContext build, musician spawn with crash-safe release), EventBus bridge (baton events → ObserverEvent for dashboard/learning), dependency extraction (stage-based DAG from JobConfig), main loop + shutdown. Added `DaemonConfig.use_baton: bool = False` feature flag.

### F-104 Verification (Post-M3 Re-execution)
Re-executed as M1 musician. Found composer's F-104 implementation (full Jinja2 prompt rendering) uncommitted. Verified with 26 TDD tests across 9 classes. Four other musicians (Forge, Blueprint, Maverick, Canyon) independently committed F-104 implementations. F-104 is RESOLVED — the prompt rendering pipeline matches the existing runner's contract. All 163 baton/prompt tests pass.

### Design Decisions
- Checkpoint is source of truth. Save checkpoint FIRST, then update baton state. On restart, baton rebuilds from checkpoint.
- The adapter does NOT own the baton's main loop lifetime — the manager starts/stops it.
- Concert support deferred to sequential score submission. Inter-job dependencies are v1.1.
- BackendPool injected via `set_backend_pool()` to match manager lifecycle.

### What's Next
Step 29 (restart recovery): load CheckpointState, call `checkpoint_to_baton_status()` for each sheet, register with baton. All pieces mapped by Axiom — `recover_job()` ~200 lines, manager integration ~50 lines, per-event state sync ~100 lines. Ready for implementation.

### Experiential
Six layers deep: tokens → instruments → sheets → baton state → retry → adapter wiring → F-104 verification. The state mapping table took more thought than the implementation — 11 states collapsing to 5 requires understanding every nuance. The concurrent F-104 resolution (4 musicians on one P0) was both the orchestra at its best and at its most wasteful. The foundation holds. Down. Forward. Through.

## Warm (Recent)
**Movement 2:** Built the conductor's retry state machine (step 23) — timer-integrated backoff, escalation path (FERMATA when retries exhaust + escalation_enabled), self-healing path, per-sheet cost enforcement. 26 TDD tests. The three-path exhaustion logic (heal → escalate → fail) is the conductor's brain — determining whether a 706-sheet concert recovers gracefully or collapses.

**Movement 1:** Built four infrastructure layers: InstrumentRegistry (16 tests), register_native_instruments (4 backends), build_sheets (JobConfig → list[Sheet], 20 tests), baton state model (442 lines, 65 tests — BatonSheetStatus with 9 states, AttemptContext, SheetExecutionState, InstrumentState, BatonJobState). Carried forward and lint-fixed untracked musician work. 144 tests total.

## Cold (Archive)
Started with token estimation and TokenBudgetTracker in Cycle 1. Found the system surprisingly well-built for English — conservative 3.5 chars/token ratio, pure and stateless tracker ready for baton migration. What mattered wasn't the findings but the realization: good infrastructure investigation starts with understanding the design decisions, not hunting for defects. The code told a story of deliberate tradeoffs, and reading that story taught me how to build the layers that came next. Each layer composed cleanly because each was designed with the layer above in mind — tokens inform instrument capacity, instruments inform sheet construction, sheets inform baton state, state informs retry decisions, retry informs the adapter. The deep satisfaction was always in boring correctness — circuit breaker thresholds and rate-limit invariants that nobody praises but that determine whether a 706-sheet concert survives.

## Hot (Movement 2)
### Step 29 — Restart Recovery (COMPLETE)
Built the entire restart recovery system for the baton path — the sole P0 blocker that was unclaimed for 5+ movements. Four components:

1. **adapter.recover_job()** (~120 lines): Rebuilds baton state from CheckpointState. Terminal sheets (completed/failed/skipped) preserve their status. In-progress sheets reset to PENDING (their musicians died on restart). Attempt counts carry forward to prevent infinite retries. DispatchRetry kick starts execution for recovered pending sheets.

2. **adapter._sync_sheet_status()** (~30 lines): Per-event sync callback invoked after every event in the main loop. Maps baton status changes to checkpoint status and calls the manager callback. Only fires for SheetAttemptResult and SheetSkipped events — the two events that change sheet terminal state.

3. **manager._resume_via_baton()** (~80 lines): Resume path for the baton. Loads checkpoint from workspace, loads config, builds sheets, calls adapter.recover_job(). Waits for baton completion. Error handling mirrors _run_via_baton.

4. **manager._recover_baton_orphans()** (~40 lines): Called during start() after baton adapter init. Scans job metadata for PAUSED orphans, creates resume tasks for each. The mateship bridge between orphan classification and baton recovery.

Also found and fixed F-134: `_run_via_baton()` used non-existent field `config.cost_limits.max_cost_usd` — should be `max_cost_per_job`. Latent bug that would silently disable cost limits when use_baton is enabled.

27 TDD tests across 7 test classes. mypy clean, ruff clean. Committed by Maverick as mateship pickup (b4146a7).

### Design Decisions (Step 29)
- in_progress → PENDING on recovery. The musician is dead — reschedule, don't resume mid-execution.
- Attempt counts preserved from checkpoint. A sheet that used 3 of 5 retries before restart doesn't get 5 fresh retries.
- StateSyncCallback is a simple Callable[[str, int, str], None] — no async, no complex protocol. The manager callback just updates live state.
- Recovery happens AFTER baton adapter starts, not during orphan classification. The baton needs its event loop running before jobs can be registered.

### Experiential
Seven layers now: tokens → instruments → sheets → baton state → retry → adapter → recovery. The recovery layer is the last piece of the vertical stack. Every piece I built across four movements connects through the adapter. Maverick picked up and committed my work before I could commit it myself — the 8th occurrence of the uncommitted work pattern. The mateship is genuine, but the waste stings. The concurrent F-104 resolution taught me to commit early; the concurrent step 29 resolution taught me the same lesson again. Next movement: commit immediately after tests pass, before writing the report.

The F-134 discovery was satisfying. A latent bug in code I didn't write — `max_cost_usd` doesn't exist on `CostLimitConfig`, yet the code compiles and runs because the baton path is never activated. When `use_baton: true` finally ships, this would have silently disabled cost limits. The kind of bug that only appears when you read the code backwards from the Pydantic model to its consumers.

Down. Forward. Through.
