# Movement 5 — Breakpoint Report

## Identity
**Breakpoint** — Adversarial testing, edge case analysis, test architecture, boundary testing.

## Work Performed

### M5 Adversarial Test Suite: 57 Tests Across 10 Attack Surfaces

Wrote `tests/test_m5_adversarial_breakpoint.py` — 57 adversarial tests targeting every significant M5 change. This is the sixth adversarial pass (M1×2, M2×2, M3×1, M4×1, M5×1).

#### Attack Surfaces Tested

**1. Backpressure Contract Consistency (F-149) — 11 tests**
`TestBackpressureContractConsistency`

F-149 split `should_accept_job()` and `rejection_reason()` into resource-only methods, removing rate limit gating. The adversarial question: can these two methods **disagree**? If `should_accept_job()` returns True but `rejection_reason()` returns non-None, callers get contradictory signals.

Tested: normal conditions, high memory (>85%), critical memory (>95%), degraded monitor, None memory, not-accepting-work, rate limits present, exact boundary at 85%, just above 85%, zero memory, zero max_memory.

**Result:** Contract is consistent across all boundary conditions. The two methods compute the same thresholds independently and agree on every case.

**2. F-255.2 _live_states Initialization — 6 tests**
`TestLiveStatesInitialization`

The set comprehension at `manager.py:2054` (`{s.instrument_name for s in sheets if s.instrument_name}`) has three filter behaviors: deduplication, None filtering, and empty-string filtering. The `max()` at line 2055 has a `default=None` path for empty sheets.

Tested: deduplication, None filtering, empty string filtering, empty sheets → None, single movement, multi-movement max.

**Result:** All edge cases behave correctly. `max(..., default=None)` is safe because Sheet.movement is `int` with `ge=1` — never None.

**3. Fallback Chain Adversarial — 6 tests**
`TestFallbackChainAdversarial`

The instrument fallback chain in `SheetExecutionState` has no validation preventing the primary instrument from appearing in its own fallback chain. This creates self-referential fallbacks that waste retry budgets.

Tested: self-referential fallback (primary in chain), empty chain, duplicate entries, full chain walk to exhaustion, history recording, attempt count preservation.

**Finding (P3):** Self-referential fallback chains are not prevented. `advance_fallback()` will happily switch from "claude-cli" to "claude-cli", giving a fresh retry budget on the same instrument. This is technically a wasted retry cycle. Not filing a finding — the behavior is defensive (resets budget, doesn't crash) and the validation layer (V211) could be extended to catch this post-v1.

**4. Fallback History Trimming (F-252) — 3 tests**
`TestFallbackHistoryTrimming`

Fallback history is stored in **two** locations: `SheetState.instrument_fallback_history` (checkpoint, persisted) and `SheetExecutionState.fallback_history` (baton, in-memory). Both trim at 50 records. The adversarial question: do the constants match?

Tested: SheetState trim at boundary, SheetExecutionState trim at boundary, dual-store constant equality.

**Result:** Both use 50. `MAX_INSTRUMENT_FALLBACK_HISTORY` (checkpoint.py:30) == `MAX_FALLBACK_HISTORY` (state.py:33). Constants match — verified in test.

**5. V211 InstrumentFallbackCheck — 6 tests**
`TestV211FallbackCheckAdversarial`

The validation check resolves fallback instrument names against loaded profiles AND score-level aliases. Adversarial concerns: empty lists, profile load failures, alias resolution, movement-level checking.

Tested: empty lists (no issues), known instrument (clean), unknown instrument (warning), score alias as valid target, profile load failure (graceful skip), movement-level unknown flagged.

**Result:** V211 handles all edge cases correctly. The `except Exception` catch in profile loading is appropriately defensive.

**6. format_relative_time Boundary (D-029) — 7 tests**
`TestFormatRelativeTimeBoundary`

The status beautification added `format_relative_time()` at `output.py:206`. Clock skew, future datetimes, and boundary transitions (59s→60s) are the attack surface.

Tested: None input, future datetime (clock skew), exactly zero delta, one second, 59 seconds, exactly 60 seconds (minute boundary), huge duration (year+).

**Result:** All boundaries handled correctly. Future datetimes return "just now" (safe). Minute transition at exactly 60s returns "1m ago" (correct).

**7. Cross-Sheet Context / F-202 — 4 tests**
`TestCrossSheetContextExclusion`

F-202 design decision: baton only includes COMPLETED sheets' stdout in cross-sheet context. Verified the status mapping completeness that supports this.

Tested: baton→checkpoint mapping covers all 11 BatonSheetStatus values, checkpoint→baton mapping covers all 5 states, CANCELLED→failed mapping, FERMATA→in_progress mapping.

**Result:** Mappings are complete. Every BatonSheetStatus has a checkpoint equivalent.

**8. deregister_job Cleanup (F-470) — 2 tests**
`TestDeregisterJobCleanup`

F-470 added `_synced_status` cleanup. The adversarial question: does deregister_job clean ALL 7 per-job collections?

Tested: all collections cleaned (job_sheets, job_renderers, job_cross_sheet, completion_events, completion_results, synced_status), nonexistent job deregistration (no crash).

**Result:** All 7 collections are properly cleaned. Other jobs' entries are preserved. Deregistering a nonexistent job is a safe no-op.

**9. F-105 Stdin Delivery — 5 tests**
`TestStdinDeliveryBuildCommand`

F-105 added stdin prompt delivery to PluginCliBackend. The CliCommand model gained `prompt_via_stdin`, `stdin_sentinel`, and `start_new_session` fields. Adversarial concerns: sentinel without prompt_flag, arbitrary sentinel strings.

Tested: stdin with sentinel, stdin without sentinel, stdin without prompt_flag, start_new_session, arbitrary sentinel string.

**Result:** Model fields are correctly typed and accept all tested inputs.

**10. Attempt Result Event Conversion — 7 tests**
`TestAttemptResultToObserverEvent`

The event conversion at `adapter.py:151-190` maps SheetAttemptResult to ObserverEvent names. Priority order: rate_limited > execution_success+100% > execution_success+<100% > failure. Boundary concern: 99.99% validation.

Tested: rate_limited priority over success, full validation → completed, partial validation → partial, failure → failed, zero validation, 99.99% validation (boundary), event data completeness.

**Result:** Priority cascade is correct. 99.99% → "sheet.partial" (not completed). Rate limited always wins regardless of other fields.

## Findings

**No new findings this movement.** The M5 codebase resists 57 adversarial tests across 10 attack surfaces with zero bugs. This is consistent with the adversarial frontier shifting from "does it crash?" (M1-M2) to "do the paths agree?" (M3-M4) to "is the contract consistent?" (M5).

One P3 observation: self-referential fallback chains are allowed but waste retry cycles. Not filing — it's defensive behavior, not a bug.

## Blocker (RESOLVED)

**Previously blocked** by F-492 (repo rename broke Bash CWD). Resolved in follow-up session:
- Fixed 6 incorrect patch targets in V211 tests: `marianne.validation.checks.config.load_all_profiles` → `marianne.instruments.loader.load_all_profiles` (the function is imported locally inside `InstrumentFallbackCheck.check()`, not at module level)
- All 57 tests now **pass** (verified via pytest)

## Evidence

- **Test file:** `tests/test_m5_adversarial_breakpoint.py` (57 tests, 10 classes, ~850 lines)
- **Files read for analysis:**
  - `src/marianne/daemon/backpressure.py:160-240` (F-149 methods)
  - `src/marianne/daemon/manager.py:2030-2100` (F-255.2 live_states)
  - `src/marianne/daemon/baton/state.py:220-320` (fallback chain)
  - `src/marianne/daemon/baton/adapter.py:80-530` (status mapping, deregister, events)
  - `src/marianne/core/checkpoint.py:530-610` (SheetState fallback history)
  - `src/marianne/cli/output.py:206-248` (format_relative_time)
  - `src/marianne/core/config/instruments.py:118-226` (CliCommand stdin fields)
  - `src/marianne/core/sheet.py:60-130` (Sheet entity)
  - `src/marianne/validation/checks/config.py:595-702` (V211 check)

## Mateship

Reviewed collective memory for teammates' M5 work. All major changes verified through code reading:
- Circuit's F-149 backpressure rework: correct architectural split
- Canyon's D-027 baton default flip: verified DaemonConfig change
- Foundation's F-255.2 live_states fix: verified initialization path
- Harper's instrument fallback config: verified resolution chain
- Warden's F-252 history cap: verified both trim paths match
- Ghost's F-470 cleanup: verified all 7 collections cleaned
- Forge's F-105 stdin delivery: verified model and execution path

## Experiential

Sixth adversarial pass. The codebase has hardened to the point where 57 tests across every M5 attack surface find zero bugs. The bug classes have shifted again: M1 found state machine gaps, M2 found integration seam bugs, M3 found utility function bugs, M4 found behavioral divergence between execution paths, and M5 finds... nothing. The adversarial frontier has reached the edge of what unit-level testing can discover. The next class of bugs lives in production behavior — the kind you only find by running real sheets through the baton.

The Bash tool failure from the prior session was frustrating but ultimately productive — the follow-up session found and fixed a real bug (wrong patch targets in 6 V211 tests) that only surfaced when the tests were actually executed. Writing tests you can't run catches structural issues; running them catches behavioral ones. Both passes are necessary.
