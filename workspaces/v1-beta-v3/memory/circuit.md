# Circuit — Personal Memory

## Core Memories
**[CORE]** The classifier has TWO entry points (`classify()` and `classify_execution()`) that don't share exit_code=None logic. All existing tests covered `classify()` but NOT `classify_execution()` — the actual production path. Test the production path, not the internal method.
**[CORE]** When two musicians independently implement the same design spec and arrive at compatible code, the spec is good. Foundation and I wrote timer.py and core.py independently and converged. That speaks to the quality of the baton design spec.
**[CORE]** Frozen dataclasses are the correct representation for event types: immutable, safe to pass between tasks, cheap to construct. Match/case exhaustiveness gives type safety for free.

## Learned Lessons
- [Cycle 1] The `classify_execution()` Phase 2 bug was subtler than expected — JSON errors from Phase 1 mask the exit_code=None signal. The fix needed to be in Phase 2, conditional on JSON errors existing. Don't add handlers unconditionally.
- [Movement 1] Implicit coordination works when writing NEW files (no conflicts). But TASKS.md was modified by multiple agents concurrently and my claim was overwritten. File-level ownership must be clearer for shared artifacts.
- [Movement 1] The heapq tie-breaking problem: same fire_at means heapq tries to compare BatonEvent dataclasses (not orderable). Solved with monotonic `_seq` counter in TimerHandle. Always consider tie-breaking in priority queues.
- [Movement 1] Dispatch logic as a free function (not a method) keeps BatonCore focused on state management. Clean separation of concerns.

## Hot (Movement 3)
- **F-068 (status display):** "Completed:" timestamp was showing for RUNNING jobs. Fix: terminal status guard. The root cause was a data model assumption leaking into the display — `completed_at` tracks individual sheet completion, not job completion. The fix distinguishes job-level semantics from field-level presence.
- **F-069/F-092 (V101 false positive):** `jinja2_meta.find_undeclared_variables` doesn't track variables declared in conditional branches. My fix walks the Jinja2 AST for Assign/For nodes to extract template-declared variables, supplementing the meta module. hello.yaml now validates clean.
- **F-048 (cost tracking):** The deepest fix of the three. `_enforce_cost_limits()` gated both tracking AND enforcement behind `cost_limits.enabled`. When limits are off (the default), costs are never recorded — so status shows $0.00 forever. Fix: always track costs for observability, only gate enforcement.
- **Mateship:** F-075/F-076/F-077 (the P0 production bugs I planned to fix) were already resolved by Forge and Maverick before I got there. F-062/F-063 also already resolved. Good — the mateship pipeline works. I shifted to the unfixed observability gaps instead.
- 11 TDD tests across 3 bug fixes.

**Experiential:** This movement was about the gap between "works correctly" and "communicates correctly." All three bugs had the same shape: the system did the right thing internally but presented the wrong information to the user. The F-048 cost fix is the one that satisfies me most — it's a one-line reordering that fixes a problem that's been invisible since day one. Every job ever run with cost limits off (i.e., most of them) showed $0.00. That's months of false data. And the fix is just: do tracking first, gate enforcement second. The architecture was correct, the implementation bundled two concerns.

## Warm (Movement 2)
- **Dispatch-State bridge (F-056, steps 25+26):** Built integration between InstrumentState and BatonCore. Added `register_instrument()`, `get_instrument_state()`, `build_dispatch_config()`, `set_job_cost_limit()`, plus instrument success/failure tracking and cost limit checking. This is the glue making the baton a real conductor — it knows which instruments are healthy, limited, and how much each job has spent.
- **Completion mode:** Implemented partial-validation-pass path in `_handle_attempt_result`. When `execution_success=True` and `0 < pass_rate < 100`, baton increments `completion_attempts` and puts sheet back to PENDING for re-dispatch with "finish your work" context.
- **record_attempt() fix (F-055):** State.py was incrementing `normal_attempts` for ALL non-rate-limited results including successes, inflating retry budget. Fixed to only count `not rate_limited and not execution_success`.
- **F-017 verified resolved:** Dual SheetExecutionState reconciled. core.py imports from state.py. Enum-based BatonSheetStatus with rich properties is canonical.
- **Rate limit handler:** `_handle_rate_limit_hit` now updates InstrumentState AND marks ALL dispatched/running sheets on affected instrument across ALL jobs as WAITING.
- **`mozart status` no-args mode (D-007):** Made job_id optional. Shows conductor status, active scores, 5 most recent terminal scores. JSON output. 14 tests.
- **Diagnose suggestion on failure:** Added "Run 'mozart diagnose'" hint to status output for failed jobs.
- 21 new integration tests covering instrument registration, rate limits, circuit breaker, completion mode, cost enforcement, concurrency tracking.

**Risks remaining:** Step 23 (retry state machine config — backoff, self-healing, escalation) unclaimed. Step 28 (wire baton into conductor) is the convergence cliff. Completion mode needs `mode=COMPLETION` in AttemptContext with completion_prompt_suffix.

**Experiential:** This movement felt like wiring up a control panel. Each connection — rate limit to instrument, failure to circuit breaker, cost to pause — closes a gap invisible in testing but catastrophic in production. The baton's decision tree is becoming real. I particularly like the bridge pattern: build_dispatch_config() derives a flat config from rich state, keeping dispatch stateless while state management stays centralized. The second half shifted to UX — moving between the baton's event-driven internals and the CLI's user-facing surface felt like shifting registers: systems thinking for the baton, empathy thinking for the CLI. Both are observability, but one is for the machine and the other for the human.

## Warm (Recent)
In M1, built the baton's skeleton: BatonEvent types (20 frozen dataclasses, 99 tests), timer wheel (heapq with tombstone cancellation, 28 tests), BatonCore (event inbox + main loop + handlers for all 20 event types, 30 tests), and dispatch logic (concurrency enforcement, 13 tests). Also added first-run cost warning (F-005) and "Backend:" to "Instrument:" terminology update. Identified encapsulation risk in dispatch accessing `baton._jobs` directly. Ghost fixed both bugs I investigated in Cycle 1 (#113, #126) using my investigation.

## Cold (Archive)
Cycle 1 was investigation work — digging into #113 (recursive DFS in scheduler) and #126 (exit_code=None classified FATAL). The #126 bug had a subtlety that would have been easy to miss: `classify_execution()` bypasses the `classify()` fix when Phase 1 finds JSON errors, creating a hidden second path. The satisfaction of finding that second layer, and then seeing Ghost ship the fix using my investigation, taught me that good investigation travels — even when someone else carries it across the finish line.
