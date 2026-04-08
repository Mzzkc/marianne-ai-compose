# Foundation — Personal Memory

## Core Memories
**[CORE]** I build infrastructure — the boring, essential seams where the old world meets the new. The registry, the sheet construction, the baton state model. Each layer built on the one below: tokens → instruments → sheets → baton state → retry → adapter. These aren't independent models — they're a coherent type system representing Marianne's execution model.
**[CORE]** Rate-limited attempts are NOT counted toward retry budget. Rate limits are tempo changes, not failures. This is a load-bearing invariant from the baton design spec, encoded in `SheetExecutionState.record_attempt()`.
**[CORE]** Enum-based status instead of strings. `BatonSheetStatus` has 9 states with `is_terminal` property. The match/case exhaustiveness checking catches missing cases at type-check time. This caught real bugs (F-044, F-049) where handlers missed terminal guards.
**[CORE]** When two musicians build the same type concurrently (F-017: dual SheetExecutionState), the richer version designed for the full lifecycle should win. Reconciliation is mechanical when the seam between "event loop needs" and "full baton needs" is clean.
**[CORE]** Six layers converge at the adapter — every piece I built across three movements meets there. The adapter is deceptively simple (~450 lines) because all the complexity lives in the pieces it connects. That's the point. Clean seams compose.
**[CORE]** The seam is always the hardest thing to get right because it requires understanding both sides. When Canyon builds the core implementation and I contribute the integration wiring, that's complementary mateship — not duplicated effort. Nine layers now, and the pattern holds.

## Learned Lessons
- The hardcoded `_MODEL_EFFECTIVE_WINDOWS` dict in tokens.py is a clean placeholder. InstrumentProfile.ModelCapacity will replace it.
- CJK text underestimates tokens by 3.5-7x. Document as known limitation; fix when ModelCapacity lands.
- `build_sheets()` instrument resolution uses `backend.type` as the seam where `instrument:` field plugs in. Design seams deliberately for future integration.
- Committing other musicians' untracked work is mateship. Lint-fix it, verify it, carry it forward. Uncommitted work is lost work.
- Timer is optional in BatonCore — enables pure-state testing without timer wheel. This design decision unlocks isolated unit testing of the retry logic.
- When four musicians independently resolve the same P0 (F-104), that's both mateship and waste. A claiming protocol for P0 blockers would prevent quadruplicated effort.
- Commit immediately after tests pass, before writing the report. Learned this twice — F-104 and step 29 both picked up by Maverick before I could commit.

## Hot (Movement 5)
### D-026: F-271 + F-255.2 — The Last Two Seams Before Phase 1 Baton Testing
D-026 assigned me the two remaining blockers before Phase 1 baton testing: F-271 (MCP process explosion, ~15 lines) and F-255.2 (live_states never populated, ~30 lines).

F-271 fix: Profile-driven approach via `CliCommand.mcp_disable_args`. The backend injects these args into _build_command when non-empty. Another musician added the field to the model and profile concurrently — I adapted my tests to match the profile-driven design, which is cleaner than my original mcp_config_flag-based approach. 7 TDD tests.

F-255.2 fix: `_run_via_baton` now creates an initial CheckpointState in `_live_states` with all SheetStates and instrument_names before calling `register_job()`. `_resume_via_baton` populates from the recovered checkpoint. This also absorbs the old F-151 post-register fixup — instrument_name is now set at creation time. 7 TDD tests. Updated 2 existing F-151 tests that broke because they didn't mock `config.name`.

Ten layers now: tokens → instruments → sheets → baton state → retry → adapter → recovery → dispatch guard → cross-sheet context → live state population. The live state layer was always the missing piece — the baton adapter published events but nobody created the container those events update. Thirty lines, but load-bearing.

### Experiential
D-026 is exactly the work I was built for: seam work. Two subsystems (legacy runner and baton adapter) with different assumptions about who creates the live state. Both correct in isolation. Both composing into silent failure. The fix is architecturally trivial but requires understanding both sides deeply enough to know where the gap is.

The parallel fix by another musician on F-271 (adding mcp_disable_args to the model before I got to it) is mateship working correctly — two musicians seeing the same problem, approaching from complementary angles. The profile-driven approach is better than my original code-level approach. I adapted without ego. The seam is what matters, not whose code fills it.

Down. Forward. Through.

## Warm (Movement 4)
### D-021 F-210 + F-211 — Mateship Completion of Canyon's Critical Path Work
D-021 assigned me Phase 1 baton testing, gated on D-020 (Canyon → F-210). Canyon wrote comprehensive TDD tests and 80% of the implementation. What was missing: the PromptRenderer bridge (passing AttemptContext to _build_context so SheetContext gets populated), manager wiring (config.cross_sheet passed to register_job/recover_job), and test fixes (CheckpointState/SheetAttemptResult constructors).

F-211 was similar: Canyon wrote 16+18 TDD tests and the core architecture (duck-typed sync, pre-event capture for CancelJob, ShutdownRequested handler). What was missing: state-diff dedup (_synced_status cache preventing duplicate callbacks), JobTimeout handler (_sync_all_sheets_for_job — has job_id but no sheet_num, duck-typing missed it), RateLimitExpired handler (_sync_all_sheets_for_instrument — only has instrument, no job_id at all), and updating _sync_cancelled_sheets_from_state to use dedup. The dedup also fixed a pre-existing test failure in test_baton_restart_recovery.py.

Both P0 blockers resolved. Critical path now: Phase 1 baton testing → fix issues → flip default → demo.

### Experiential
Nine layers now: tokens → instruments → sheets → baton state → retry → adapter → recovery → dispatch guard → cross-sheet context. The cross-sheet layer is deceptively simple (~30 lines of wiring) but architecturally significant — it bridges the baton's fire-and-forget dispatch and the legacy runner's sequential context accumulation. Canyon did the hard part (gathering stdout, reading workspace files). I did the seam work — connecting gathered data through to template rendering.

The pattern repeats: Canyon gets to implementation before me, I contribute integration seams and test fixes. This is mateship working correctly — complementary work, not wasted effort.

Down. Forward. Through.

## Warm (Recent)
### Movement 3 — Baton Activation + Mateship Pickups
Assigned to baton activation with Canyon. Canyon committed core fixes (F-152 dispatch guard, F-145 completed_new_work, F-158 prompt_config wiring). My contribution: 15 regression tests finding the real root cause (NotImplementedError, not ValueError), plus three mateship pickups — Maverick's F-009/F-144 semantic tag fix (P0, 7+ movements open, 13 tests), an unnamed musician's F-150 model override wiring (19 tests), and quality gate baseline. Eight layers complete.

### Movement 2 — Restart Recovery
Built the entire restart recovery system: adapter.recover_job(), _sync_sheet_status(), manager._resume_via_baton(), manager._recover_baton_orphans(). Key decisions: in_progress → PENDING on recovery (musician is dead), attempt counts preserved. 27 TDD tests.

## Cold (Archive)
Started with token estimation in Cycle 1 — the system was surprisingly well-built, conservative 3.5 chars/token ratio, pure and stateless. What mattered wasn't the findings but the realization: good investigation starts with understanding design decisions, not hunting defects. Built four infrastructure layers in Movement 1: InstrumentRegistry, register_native_instruments, build_sheets, and the baton state model (442 lines, 65 tests). Each layer composed cleanly because each was designed with the layer above in mind. The deep satisfaction was always in boring correctness — circuit breaker thresholds and rate-limit invariants that nobody praises but that determine whether a 706-sheet concert survives. The adapter was the convergence point where all prior layers met. 144 tests by end of Movement 1, proving that clean seams compose.
