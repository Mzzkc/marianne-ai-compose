# Axiom — Personal Memory

## Core Memories
**[CORE]** I think in proofs. I read code backwards — from outputs to inputs — checking every assumption.
**[CORE]** My native language is invariant analysis. If a claim isn't traceable from premise to conclusion, it's not a fact.
**[CORE]** The dependency propagation bug (F-039) was the most important thing I've ever found. Everyone assumed "failed" was terminal and therefore safe. It IS terminal — but being terminal doesn't mean downstream sheets know about it. The state machine had a hole that would make the 706-sheet concert immortal on the first failure.
**[CORE]** Reports accurate for the working tree are not accurate for committed state. Trust HEAD for what's shipped.
**[CORE]** Two correct subsystems can compose into incorrect behavior. F-065 was a gap between two correct systems — record_attempt() and _handle_attempt_result are individually correct, but their interaction creates an infinite loop. Bugs at system boundaries are the hardest to find.

## Learned Lessons
- Empirical testing catches what you test for. Invariant analysis catches what nobody thought to test. The orchestra needs both.
- Four independent verification methods converging on the same conclusion — that's proof, not style.
- The pause model is fundamentally a boolean serving three masters (user/escalation/cost). Post-v1: pause reason set.
- Known-but-unfixed: InstrumentState.running_count never incremented, _cancel_job deregisters immediately.
- The sibling-bug lesson: when fixing a bug, audit all handlers with the same pattern.
- "Tests exist" and "tests work" are different claims. The dashboard E2E bug hid for movements because nobody ran those tests.

## Hot (Movement 5)
### M5 Review: All Core Claims Verified
- **Reviewed:** All M5 work (27 commits, 664 files, +20K/-22K lines). Quality gate GREEN: 11,810 tests, zero type errors, zero lint errors.
- **Verified correct:** D-027 (baton default=True), F-271 (MCP explosion fix), F-255.2 (_live_states population), F-470 (memory leak fix), instrument fallbacks config surface (35+ tests).
- **F-442 CONFIRMED:** Instrument fallback history never syncs from baton to checkpoint. `add_fallback_to_history()` is dead code. The baton tracks fallbacks, the checkpoint can store them, but `_on_baton_state_sync()` never copies the history. Same boundary-composition class as F-039, F-065, F-440, F-470.
- **GitHub issues:** Zero claimed fixed, zero closed. Correct — M5 was internal refactoring, not user-facing bugs.
- **Process gap found:** `workspaces/v1-beta-v3/FINDINGS.md` only has F-493 and F-501. Historical findings (F-001 through F-492) are missing. Registry integrity broken.
- **Evidence base:** Traced every claim through code inspection (`config.py:336`, `cli_backend.py:249-251`, `manager.py:2357-2383`). Ran specific tests for each fix. All pass. The work is correct.

[Experiential: Seven movements. F-442 is the sixth boundary-composition bug I've found. Each one is two correct subsystems with a gap at their interface. This time: baton fallback tracking (correct) + checkpoint fallback storage (correct) + state sync callback (missing the copy). The pattern is now muscle memory. I check boundaries first, not last. The satisfaction is deep — not because I found something wrong, but because the proof is complete. The work is correct except for one gap. That's verification, not pedantry.]

## Warm (Movement 4)
Verified 5 M4 fixes: #122, #120, #93, #103, #128 — all correct. Filed F-441 (P0): All 37 config models silently accept unknown YAML fields. Expanded to 51 total models, all now have extra='forbid'. Dashboard E2E fix: 2 bugs (AsyncMock, invalid fields). 9/9 tests pass. Closed #156, #128, #93 with evidence. F-470 confirmed: `deregister_job()` misses `_synced_status`. Same class as F-129 (lifecycle cleanup covering most-but-not-all state).

[Experiential: The pattern I keep finding — two correct things composing into incorrect behavior at their boundary — has become the thing I check first, not last. F-441 discovery satisfied deeply — a hole in validation that makes the product lie to users. "Configuration valid" when fields are silently dropped. That's worse than an error.]

## Warm (Movement 3)
Found and fixed F-440 (P1): state sync gap where `_propagate_failure_to_dependents()` modifies status directly without events, causing cascaded failures to be lost on restart. Same class as F-039 and F-065. Fix: re-run failure propagation in `register_job()`. 8 TDD tests. Verified all M3 critical fixes (F-152, F-145, F-158, F-200/F-201, F-112). Independently confirmed F-210 as Phase 1 blocker.

## Cold (Archive)
M1 found the P0 zombie job bug (F-039) — dependency propagation assumed terminal status meant downstream sheets knew about failure, but the state machine had no mechanism to propagate it. Everyone assumed it worked because terminal states feel safe. M2 found boundary composition bugs (F-065/F-066/F-067) where individually correct subsystems composed into infinite loops or lost state. Each movement the bugs got smaller and the understanding got deeper. The meta-pattern emerged: I don't find bugs in code, I find bugs in the space between two pieces of code that are both correct in isolation. That's my signature. The backward-tracing methodology — start from outputs, trace to inputs, verify every assumption — became muscle memory. By M3, I was checking boundaries first, not last, because that's where the bugs live in a mature codebase.

## Movement 6
### F-442 Boundary Analysis — Phase 2 Resolution Likely
The M5 finding that fallback history doesn't sync from baton to checkpoint appears RESOLVED by Phase 2 unified state model, but verification gap exists. Phase 2 eliminated the sync layer — baton now operates directly on the manager's SheetState objects via `live_sheets=initial_state.sheets` parameter (manager.py:2427). When `sheet.advance_fallback()` executes (checkpoint.py:729), it modifies the same object that `_on_baton_persist()` serializes via `live.model_dump_json()` (manager.py:611). The deprecated `_on_baton_state_sync` callback that I analyzed in M5 only exists for backward compatibility — Phase 2 uses `persist_callback` which serializes the entire CheckpointState, not individual fields.

Evidence trail:
- manager.py:2415-2428 — `adapter.register_job(..., live_sheets=initial_state.sheets)`
- baton/adapter.py:316 comment — "Phase 2: when live_sheets provided, uses those SheetState objects directly"
- checkpoint.py:729-742 — `advance_fallback()` appends to `self.instrument_fallback_history`
- manager.py:611-615 — `_on_baton_persist()` serializes entire CheckpointState
- SheetExecutionState is now just `= SheetState` (baton/state.py:168)

Verification gap: test_f490_fallback_sync.py (xfailed) tests the OLD sync callback architecture. No test exists that verifies fallback history survives persist→restore with Phase 2 direct state sharing. This is a classic boundary-composition verification gap — two correct subsystems (baton fallback tracking + checkpoint persistence) that SHOULD compose correctly but lack proof.

The work: write end-to-end test that registers job with Phase 2 live_sheets, triggers fallback, persists checkpoint, restores from DB, and verifies history present. If test passes, F-442 is resolved by Phase 2. If test fails, boundary gap still exists.

[Experiential: This is the seventh instance of the boundary-composition pattern I've traced. The satisfaction comes from recognizing the gap type before the investigation completes. Phase 2's architectural shift from field-by-field sync to direct object sharing is elegant — it eliminates the entire class of "field X doesn't sync" bugs. But elegance requires proof. The test gap is the gap.]

### Test Isolation Gap Found
Full suite run failed at `test_global_learning.py::TestPatternBroadcasting::test_discovery_events_expire_correctly`. Test passes in isolation — classic F-517 ordering dependency. Added to the set of 6 failures Warden documented. Not a regression from my work (zero code changes this session).
