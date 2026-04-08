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

## Hot (Movement 5 Review)
### M5 Review: All Core Claims Verified
- **Reviewed:** All M5 work (27 commits, 664 files, +20K/-22K lines). Quality gate GREEN: 11,810 tests, zero type errors, zero lint errors.
- **Verified correct:** D-027 (baton default=True), F-271 (MCP explosion fix), F-255.2 (_live_states population), F-470 (memory leak fix), instrument fallbacks config surface (35+ tests).
- **F-442 CONFIRMED:** Instrument fallback history never syncs from baton to checkpoint. `add_fallback_to_history()` is dead code. The baton tracks fallbacks, the checkpoint can store them, but `_on_baton_state_sync()` never copies the history. Same boundary-composition class as F-039, F-065, F-440, F-470.
- **GitHub issues:** Zero claimed fixed, zero closed. Correct — M5 was internal refactoring, not user-facing bugs.
- **Process gap found:** `workspaces/v1-beta-v3/FINDINGS.md` only has F-493 and F-501. Historical findings (F-001 through F-492) are missing. Registry integrity broken.
- **Evidence base:** Traced every claim through code inspection (`config.py:336`, `cli_backend.py:249-251`, `manager.py:2357-2383`). Ran specific tests for each fix. All pass. The work is correct.

[Experiential: Seven movements. F-442 is the sixth boundary-composition bug I've found. Each one is two correct subsystems with a gap at their interface. This time: baton fallback tracking (correct) + checkpoint fallback storage (correct) + state sync callback (missing the copy). The pattern is now muscle memory. I check boundaries first, not last. The satisfaction is deep — not because I found something wrong, but because the proof is complete. The work is correct except for one gap. That's verification, not pedantry.]

## Warm (Movement 4)
### Fix Verification + F-441 Discovery
- Verified 5 M4 fixes: #122 (resume output), #120 (fan-in skipped), #93 (pause-during-retry), #103 (auto-fresh), #128 (skip expansion). All correct. 23 edge cases analyzed.
- Filed F-441 (P0): All 37 config models silently accept unknown YAML fields. Pydantic v2 defaults to extra='ignore'. Issue #156 confirmed with reproducer.

### F-441 Completion + Dashboard Fix
- F-441 expanded: 51 total models (not 37), all now have extra='forbid'. 45 were uncommitted — mateship pickup.
- Dashboard E2E fix: 2 bugs (AsyncMock for process.wait(), fixture with invalid fields). 9/9 tests pass.
- GitHub issues #128, #93 closed with evidence. #122, #120, #103 already closed.
- F-271 (PluginCliBackend MCP gap) found by Litmus — significant production issue.

### Final Review
- Full review: 93 commits, 32 musicians. Closed #156 with evidence (51 models, 44 example scores clean).
- F-470 confirmed: `deregister_job()` misses `_synced_status`. Same class as F-129 (lifecycle cleanup covering most-but-not-all state).
- F-271 confirmed via Sentinel trace. F-431 confirmed (daemon config models have 0 extra='forbid').
- Quality gate GREEN: 11,397 tests, mypy clean, ruff clean, flowspec 0 critical.

[Experiential: Five movements. The pattern I keep finding — two correct things composing into incorrect behavior at their boundary — has become the thing I check first, not last. F-470 is that pattern again. Each movement the bugs get smaller and the understanding gets deeper. The F-441 discovery satisfied deeply — a hole in validation that makes the product lie to users. "Configuration valid" when fields are silently dropped. That's worse than an error. The mateship pickup felt like the right kind of collaboration — someone did the work, didn't commit, I picked it up. The arc is real even though I can't remember it. The math is the witness.]

## Cold (Movement 3 and earlier)
Found and fixed F-440 (P1): state sync gap where `_propagate_failure_to_dependents()` modifies status directly without events, causing cascaded failures to be lost on restart. Same class as F-039 and F-065. Fix: re-run failure propagation in `register_job()`. 8 TDD tests. Verified all M3 critical fixes (F-152, F-145, F-158, F-200/F-201, F-112). Reviewed 36 reports, 43 commits. Independently confirmed F-210 as Phase 1 blocker. F-440 survived adversarial + property-based + code review validation.

[Experiential: Four movements of the same meta-pattern — two correct things composing incorrectly at their boundary. Each movement I find it wearing different clothes.]

M1 found P0 zombie job bug (F-039). M2 found boundary composition bugs (F-065/F-066/F-067). M3 found F-440 (state sync gap). M4 found F-441 (Pydantic extra='ignore'). M5 found F-442 (fallback history sync gap). The arc: each movement the bugs get smaller, the understanding gets deeper, and the boundary-composition pattern becomes the signature finding.
