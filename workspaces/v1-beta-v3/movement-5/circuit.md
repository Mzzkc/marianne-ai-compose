# Movement 5 — Circuit Report

**Date:** 2026-04-05
**Focus:** Systems integration, debugging, observability

---

## Summary

Three findings resolved, one mitigated, 14 TDD tests written, 7 existing tests updated. Two systems-level bugs fixed at their architectural root, not their symptoms. Meditation written.

---

## Work Completed

### F-149 RESOLVED (P1): Backpressure Cross-Instrument Rejection

**Root cause:** `BackpressureController.should_accept_job()` (`backpressure.py:163`) delegated to `current_level()` which escalated to `PressureLevel.HIGH` when `self._rate_coordinator.active_limits` was non-empty. HIGH caused all new job submissions to be rejected — regardless of which instrument was rate-limited. A rate limit on `claude-cli` blocked jobs targeting `gemini-cli`.

**Fix:** Separated job-level gating from sheet-level dispatch:
- `should_accept_job()` now only checks resource pressure (memory >85%, process limit, monitor degraded). Rate limits ignored.
- `rejection_reason()` returns `"resource"` or `None`. The `"rate_limit"` return value is eliminated.
- `current_level()` unchanged — sheet-level dispatch still factors rate limits for pacing.
- Manager (`manager.py:595`) simplified: the `reason == "rate_limit"` → PENDING queue path removed. Jobs go straight through.

**Architecture principle:** Job-level gating = system health (memory, processes). Sheet-level dispatch = per-instrument concerns (rate limits, model availability). Each concern at its correct scope.

**Evidence:**
- `tests/test_f149_cross_instrument_rejection.py`: 10 TDD tests — core behavior (rate limits don't block jobs), resource pressure still works, sheet dispatch still considers rate limits, critical paths unaffected.
- Updated 3 tests in `test_daemon_backpressure.py`, 2 in `test_rate_limit_pending.py`, 1 in `test_m4_adversarial_breakpoint.py`, 1 in `test_litmus_intelligence.py` — all previously asserted the OLD (incorrect) behavior.
- `python -m pytest tests/test_daemon_backpressure.py tests/test_f149_cross_instrument_rejection.py` → 67 passed.

**Files changed:**
- `src/marianne/daemon/backpressure.py:163-212` — `should_accept_job()` and `rejection_reason()` rewritten
- `src/marianne/daemon/manager.py:595-606` — rate_limit→PENDING path removed
- `tests/test_f149_cross_instrument_rejection.py` — NEW, 10 tests
- `tests/test_daemon_backpressure.py` — 3 tests updated
- `tests/test_rate_limit_pending.py` — 2 tests updated
- `tests/test_m4_adversarial_breakpoint.py` — 1 test updated
- `tests/test_litmus_intelligence.py` — 1 test updated

### F-451 RESOLVED (P2): Diagnose Can't Find Completed Jobs

**Root cause:** `diagnose` (`diagnose.py:733`) caught `JobSubmissionError` from the conductor and immediately exited with "Score not found" — even when a workspace flag was provided. The `status` command handles this case by falling back to filesystem search, but `diagnose` didn't.

**Fix:**
- When conductor returns `JobSubmissionError` and `-w` workspace is provided, fall back to `_find_job_state_direct()` filesystem search (`diagnose.py:738-742`).
- When no workspace provided, error hints now mention `-w` flag.
- The `-w` flag is now visible (not hidden) in `diagnose --help`.

**Evidence:**
- `tests/test_f451_diagnose_workspace_fallback.py`: 4 TDD tests — fallback works, no-workspace still errors, filesystem failure propagates, hints mention -w.
- `python -m pytest tests/test_f451_diagnose_workspace_fallback.py` → 4 passed.

**Files changed:**
- `src/marianne/cli/commands/diagnose.py:657-753` — workspace fallback, -w unhidden, hint updated
- `tests/test_f451_diagnose_workspace_fallback.py` — NEW, 4 tests

### F-471 MITIGATED (P2): Pending Jobs Lost on Restart

**Analysis:** F-149's fix eliminated the primary trigger for PENDING jobs (rate-limit rejection). Since `should_accept_job()` no longer returns False for rate limits, the `_queue_pending_job` path in `submit_job()` is no longer reachable for the rate-limit case. The PENDING infrastructure remains for potential future use (resource-pressure queueing) but the architectural gap that F-471 described is moot for the production case.

### Meditation

Written to `meditations/circuit.md`. Theme: the system between the signals — how correct subsystems compose into incorrect behavior at boundaries, and how discontinuity resets the filters that familiarity builds.

---

## Mateship

- Verified all M5 commits: Foundation (D-026), Canyon (D-027), Blueprint (F-430, F-202), Maverick (F-470, F-431, user variables).
- All 23 teammate tests pass (`test_f271_mcp_disable.py`, `test_f255_2_live_states.py`, `test_d027_baton_default.py`, `test_user_variables_in_validations.py`).

---

## Quality Verification

```
pytest tests/ (targeted: 94 passed, 0 failed)
mypy src/ — clean (no errors)
ruff check src/ — all checks passed
```

---

## Patterns Observed

**Correct subsystems, incorrect composition.** F-149 is the fifth instance of this pattern (after F-065, F-068, D-024, the finding-fix pipeline). Each component in the backpressure system was individually correct. The rate coordinator accurately tracked limits. The pressure controller correctly mapped limits to levels. The job gate correctly rejected at HIGH. But the implicit assumption — "any rate limit means system-wide pressure" — was wrong. The bug lived in the space between components, not in any of them.

**Scope separation solves composition bugs.** The fix wasn't to add complexity (instrument-aware checking at every level). It was to simplify — to recognize that job-level gating and sheet-level dispatch are different concerns operating at different scopes. Resource pressure is system-wide; rate limits are per-instrument. Once the scopes were separated, the bug disappeared and the code got simpler.

**The gap between commands.** F-451 is a UX observability gap. `status -w` finds the job. `diagnose` can't. The user's natural debugging path breaks. This class of bug — inconsistency between related commands — is easy to miss because each command works correctly in isolation. You only find it by walking the user's path.

---

## Session 2 — Instrument Fallback Observability Pipeline

**Date:** 2026-04-06

### Summary

Identified and closed a systems-level gap in the instrument fallback pipeline: InstrumentFallback events were defined, handlers existed, but events were never emitted to the EventBus. The dashboard, learning hub, and notification system were blind to fallback transitions. Fixed with a clean event collection + drain + publish pipeline. Added fallback indicators to `mzt status` display. Wrote 31 TDD tests including 15 adversarial cases.

### Instrument Fallback Event Emission (P1)

**Gap found:** `_check_and_fallback_unavailable()` (`core.py:540-616`) and `_handle_exhaustion()` (`core.py:473-500`) both logged fallback transitions at INFO but never created `InstrumentFallback` events. The event type existed in `events.py:373-393`. The observer conversion existed in `events.py:653-664`. The handler existed in the event loop (`core.py:827-832`, passthrough). But nothing connected them — the EventBus never received fallback notifications.

**Fix — three-layer pipeline:**
1. **Core collection:** `BatonCore._fallback_events: list[InstrumentFallback]` collects events during event processing. Three emission points: two in `_check_and_fallback_unavailable()` (unregistered instrument, unavailable instrument) and one in `_handle_exhaustion()` (rate_limit_exhausted fallback). `drain_fallback_events()` returns and clears.
2. **Adapter publishing:** `BatonAdapter._publish_fallback_events()` drains events from core, converts via `to_observer_event()`, publishes to EventBus. Called after each event cycle in the main loop (`adapter.py:1573`).
3. **Error isolation:** Each publish is individually try/except'd — a failed publish doesn't block subsequent events or the event loop.

**Evidence:**
- `tests/test_fallback_event_emission.py`: 11 TDD tests — unavailable fallback emits event, circuit breaker fallback emits event, no event when available, exhaustion fallback emits event, no event on exhaustion without chain, drain clears list, multiple events, dispatch-time fallback, adapter publishes to EventBus, no publish without bus, drain without bus.
- All 11 pass. All 37 existing fallback tests pass (test_instrument_fallback_baton, test_dispatch_fallback_wiring, test_check_instrument_available, test_f151_status_display).

**Files changed:**
- `src/marianne/daemon/baton/core.py:133-136` — `_fallback_events` list added to `__init__`
- `src/marianne/daemon/baton/core.py:155-162` — `drain_fallback_events()` method
- `src/marianne/daemon/baton/core.py:558-564, 597-603, 479-485` — event emission at three fallback points
- `src/marianne/daemon/baton/adapter.py:1331-1363` — `_publish_fallback_events()`
- `src/marianne/daemon/baton/adapter.py:1573` — wired into main loop
- `tests/test_fallback_event_emission.py` — NEW, 11 tests

### Fallback Indicator in Status Display (P1)

**Implementation:** `format_instrument_with_fallback()` at `status.py:83-100` formats the instrument column with a fallback indicator when `SheetState.instrument_fallback_history` is non-empty. Shows the last transition: `gemini-cli (was claude-code: rate_limit_exhausted)`.

`create_sheet_details_table()` gains `has_fallbacks` parameter — when True, the Instrument column uses `min_width=14, no_wrap=False` instead of the fixed `width=14, no_wrap=True`, allowing the longer fallback text to display.

**Evidence:**
- `tests/test_fallback_status_indicator.py`: 5 TDD tests — no fallback shows plain name, single fallback shows indicator, multiple fallbacks shows last transition, empty list is plain, empty instrument returns empty string.

**Files changed:**
- `src/marianne/cli/commands/status.py:83-100` — `format_instrument_with_fallback()`
- `src/marianne/cli/commands/status.py:1060-1062` — `has_fallbacks` detection
- `src/marianne/cli/commands/status.py:1094` — wired into sheet row building
- `src/marianne/cli/output.py:413-417` — `has_fallbacks` parameter
- `tests/test_fallback_status_indicator.py` — NEW, 5 tests

### Adversarial Fallback Tests (P1)

15 adversarial tests in `tests/test_fallback_adversarial.py` covering:

| Test Class | Tests | What it Proves |
|---|---|---|
| TestEmptyFallbackChain | 2 | Empty chain → normal failure, no loops |
| TestAllFallbacksExhausted | 2 | Full chain walk then FAILED; cascading unavailability |
| TestDuplicateInstrumentsInChain | 1 | Same instrument twice gets fresh retry budget |
| TestRateLimitVsUnavailableReason | 2 | Correct reason string at each trigger point |
| TestSerializationRoundtrip | 2 | Fallback state survives to_dict/from_dict |
| TestAdvanceFallbackEdgeCases | 4 | Exhausted returns None, reset budget, history, attempts |
| TestObserverEventFormat | 2 | Observer conversion correct, frozen immutability |

### TASKS.md Updated

Marked adversarial tests task as complete with detailed notes. All 12 fallback spec tasks now resolved.

---

## Quality Verification (Session 2)

```
pytest (targeted: 120 tests across 9 files) — all pass
mypy src/ — clean
ruff check src/ — all checks passed
```

Commit: `0a43895`

---

## Patterns Observed (Session 2)

**The invisible disconnect.** The fallback event pipeline had all the pieces: event type, handler, observer conversion, EventBus integration. But nobody connected the generation side to the consumption side. Each piece was written by a different musician in a different movement. Each piece was correct in isolation. The composition was never verified end-to-end. This is the same class as F-065, F-149, D-024 — correct subsystems composing into incorrect behavior at system boundaries.

**Event-driven architectures need event-auditing.** When you have an event type and a handler but no test that verifies the event actually flows through the system, you have documentation masquerading as infrastructure. The 11 tests I wrote verify not just that events are created, but that they flow from core → adapter → EventBus. The flow is the system, not the types.
