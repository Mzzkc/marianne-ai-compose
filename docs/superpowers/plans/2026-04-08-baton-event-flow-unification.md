# Baton Event Flow Unification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every state mutation in the baton system through the event flow so that a single trace from event publisher to handler reveals why any sheet or job is in any state.

**Architecture:** The baton already has a clean event-driven core (inbox, handlers, 20 event types). But 22 state mutations bypass it -- dispatch sets DISPATCHED directly, the manager sets job-level status through 16 direct `meta.status =` assignments, and 6 direct `live.status =` assignments. This plan routes those mutations back through events. Three new events (`SheetDispatched`, `JobStatusChanged`, cascade as events) plus fixing the cascade's transitive propagation bug and reordering Path 0 in the exhaustion handler.

**Tech Stack:** Python 3.12, asyncio, Pydantic v2, pytest, dataclasses (frozen)

**Prior analysis context:**
- The baton's core.py event handlers (45+ status assignments) are correct and clean
- The runner marks cascade-blocked sheets as SKIPPED (lifecycle.py:790-795)
- The baton's cascade rewrite matches this but has a transitive propagation bug (SKIPPED sheets don't propagate because `any_failed` only checks for FAILED)
- Path 0 in `_handle_exhaustion` fires FIRST (before fallback/healing/escalation), intercepting tests
- 48 test failures, all cascade-related: tests expect FAILED for dependents, baton marks SKIPPED
- The `_on_baton_state_sync` callback is defined (manager.py:596) but NOT wired in -- only `_on_baton_persist` is used
- `_on_baton_persist` (manager.py:572) serializes `_live_states` to registry via fire-and-forget task

---

## File Structure

| File | Role | Action |
|------|------|--------|
| `src/marianne/daemon/baton/events.py` | Event definitions | Add `SheetDispatched` event |
| `src/marianne/daemon/baton/core.py` | Event handlers, state machine | Add `_handle_sheet_dispatched`, fix cascade propagation, reorder Path 0 |
| `src/marianne/daemon/baton/dispatch.py` | Ready sheet resolution | Replace direct DISPATCHED assignment with event emission |
| `src/marianne/daemon/baton/adapter.py` | Conductor bridge | Wire dispatch event into persist flow |
| `src/marianne/daemon/manager.py` | Job lifecycle manager | Route job-level status through `_set_job_status` for remaining 14 sites |
| `tests/test_baton_event_flow.py` | New test file | Verify event-driven dispatch, cascade propagation, shared object identity |
| 16 existing test files | Cascade assertion updates | Change `FAILED` to `SKIPPED` for cascade-blocked dependents |

---

### Task 1: Fix Cascade Transitive Propagation

The cascade BFS at `core.py:1589-1678` checks `dep_sheet.status == BatonSheetStatus.FAILED` for `any_failed`. Cascade-SKIPPED sheets (which have `error_code` set) don't trigger this, so transitive chains break: `1->FAILED, 2->SKIPPED(cascade), 3->stays PENDING`.

**Files:**
- Modify: `src/marianne/daemon/baton/core.py:1638-1657`
- Create: `tests/test_baton_event_flow.py`

- [ ] **Step 1: Write failing test for transitive cascade**

```python
# tests/test_baton_event_flow.py
"""Tests for baton event flow unification."""

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState


class TestCascadeTransitivePropagation:
    """Cascade-SKIPPED sheets must propagate to their own dependents."""

    def test_linear_chain_propagates_through_skipped(self) -> None:
        """In a chain 1->2->3, failing sheet 1 should SKIP both 2 and 3."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, max_retries=0),
            2: SheetExecutionState(sheet_num=2, max_retries=0),
            3: SheetExecutionState(sheet_num=3, max_retries=0),
        }
        deps = {2: [1], 3: [2]}
        baton.register_job("j1", sheets, deps)

        # Fail sheet 1
        baton._handle_attempt_result(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="test",
            attempt=1, execution_success=False,
            error_message="failed",
        ))

        assert sheets[1].status == BatonSheetStatus.FAILED
        assert sheets[2].status == BatonSheetStatus.SKIPPED
        assert sheets[3].status == BatonSheetStatus.SKIPPED, (
            "Sheet 3 must be SKIPPED transitively through cascade-SKIPPED sheet 2"
        )

    def test_deep_chain_four_levels(self) -> None:
        """Chain 1->2->3->4: failing 1 cascades all the way to 4."""
        baton = BatonCore()
        sheets = {
            i: SheetExecutionState(sheet_num=i, max_retries=0)
            for i in range(1, 5)
        }
        deps = {2: [1], 3: [2], 4: [3]}
        baton.register_job("j1", sheets, deps)

        baton._handle_attempt_result(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="test",
            attempt=1, execution_success=False,
            error_message="failed",
        ))

        for i in range(2, 5):
            assert sheets[i].status == BatonSheetStatus.SKIPPED, (
                f"Sheet {i} must be transitively SKIPPED"
            )

    def test_fan_out_waits_for_all_deps(self) -> None:
        """Fan-out: voice1+voice2 -> synthesis. One voice fails, one completes.
        Synthesis should be SKIPPED (blocked by failed voice)."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, max_retries=0),
            2: SheetExecutionState(sheet_num=2, max_retries=0),
            3: SheetExecutionState(sheet_num=3, max_retries=0),
        }
        deps = {3: [1, 2]}
        baton.register_job("j1", sheets, deps)

        # Voice 1 completes
        baton._handle_attempt_result(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="test",
            attempt=1, execution_success=True,
            validation_pass_rate=100.0,
        ))
        # Synthesis should NOT be skipped yet (voice 2 still pending)
        assert sheets[3].status == BatonSheetStatus.PENDING

        # Voice 2 fails
        baton._handle_attempt_result(SheetAttemptResult(
            job_id="j1", sheet_num=2, instrument_name="test",
            attempt=1, execution_success=False,
            error_message="failed",
        ))
        # NOW synthesis should be SKIPPED (all deps terminal, one failed)
        assert sheets[3].status == BatonSheetStatus.SKIPPED

    def test_cascade_skipped_does_not_satisfy_dispatch(self) -> None:
        """Cascade-SKIPPED sheets must NOT satisfy dependencies for dispatch."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, max_retries=0),
            2: SheetExecutionState(sheet_num=2, max_retries=0),
            3: SheetExecutionState(sheet_num=3, max_retries=0),
        }
        deps = {2: [1], 3: [2]}
        baton.register_job("j1", sheets, deps)

        # Fail sheet 1 -> cascades to 2 and 3
        baton._handle_attempt_result(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="test",
            attempt=1, execution_success=False,
            error_message="failed",
        ))

        # No sheets should be ready (all terminal)
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 0

    def test_user_skipped_still_satisfies_deps(self) -> None:
        """User-skipped sheets (no error_code) must still satisfy deps."""
        from marianne.daemon.baton.events import SheetSkipped

        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, max_retries=0),
            2: SheetExecutionState(sheet_num=2, max_retries=0),
        }
        deps = {2: [1]}
        baton.register_job("j1", sheets, deps)

        # User-skip sheet 1 (no error_code)
        baton._handle_sheet_skipped(SheetSkipped(
            job_id="j1", sheet_num=1, reason="skip_when_command",
        ))

        assert sheets[1].status == BatonSheetStatus.SKIPPED
        assert sheets[1].error_code is None  # User skip has no error code

        # Sheet 2 should be ready (user-skipped satisfies deps)
        ready = baton.get_ready_sheets("j1")
        assert any(s.sheet_num == 2 for s in ready)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_baton_event_flow.py -v`
Expected: `test_linear_chain_propagates_through_skipped` FAILS (sheet 3 is PENDING, not SKIPPED). `test_deep_chain_four_levels` FAILS similarly. `test_cascade_skipped_does_not_satisfy_dispatch` may pass or fail depending on dispatch behavior. `test_user_skipped_still_satisfies_deps` should PASS.

- [ ] **Step 3: Fix `_propagate_failure_to_dependents` for transitive cascade**

In `src/marianne/daemon/baton/core.py`, replace the cascade check at lines 1638-1657:

```python
    def _propagate_failure_to_dependents(
        self, job_id: str, failed_sheet_num: int
    ) -> None:
        """Mark downstream sheets as SKIPPED when their dependencies are
        unsatisfiable AND all sibling dependencies are terminal.

        This mirrors the legacy runner's approach: a single sheet failure
        does NOT cascade instantly. Downstream sheets simply never become
        "ready" (get_ready_sheets checks _is_dependency_satisfied, which
        requires COMPLETED or SKIPPED). They sit in PENDING until all
        their dependencies reach terminal state.

        When ALL dependencies of a downstream sheet are terminal and at
        least one is FAILED or cascade-SKIPPED (has error_code), that
        sheet is marked SKIPPED (not FAILED -- the work was never
        attempted, it was blocked). This prevents premature cascade in
        fan-out stages: 1 of 18 voices failing doesn't kill the other 17
        or their downstream.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return

        # Build a reverse dependency map: sheet_num -> list of sheets
        # that depend on it
        dependents: dict[int, list[int]] = {}
        for sheet_num, deps in job.dependencies.items():
            for dep in deps:
                if dep not in dependents:
                    dependents[dep] = []
                dependents[dep].append(sheet_num)

        # Walk downstream from the failed sheet. Only mark sheets whose
        # dependencies are ALL terminal with at least one unsatisfied.
        queue = list(dependents.get(failed_sheet_num, []))
        visited: set[int] = set()

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            sheet = job.sheets.get(current)
            if sheet is None:
                continue

            if sheet.status in _TERMINAL_BATON_STATUSES:
                continue

            # Check if ALL dependencies are terminal and at least one
            # is unsatisfied (FAILED, CANCELLED, or cascade-SKIPPED).
            deps = job.dependencies.get(current, [])
            all_terminal = True
            any_unsatisfied = False
            failed_dep_num = failed_sheet_num  # Default for error msg
            for dep_num in deps:
                dep_sheet = job.sheets.get(dep_num)
                if dep_sheet is None:
                    continue
                if dep_sheet.status not in _TERMINAL_BATON_STATUSES:
                    all_terminal = False
                    break
                # A dep is unsatisfied if it's anything other than
                # COMPLETED or user-SKIPPED (no error_code).
                if not self._is_dep_satisfied(dep_sheet):
                    any_unsatisfied = True
                    failed_dep_num = dep_num

            if not all_terminal or not any_unsatisfied:
                continue

            # All deps terminal, at least one unsatisfied -> SKIPPED
            sheet.status = BatonSheetStatus.SKIPPED
            sheet.error_message = (
                f"Blocked by failed dependency: sheet {failed_dep_num}"
            )
            sheet.error_code = "E999"
            _logger.info(
                "baton.sheet.dependency_blocked",
                extra={
                    "job_id": job_id,
                    "sheet_num": current,
                    "failed_dependency": failed_dep_num,
                },
            )

            # Continue propagation to this sheet's dependents
            for downstream in dependents.get(current, []):
                if downstream not in visited:
                    queue.append(downstream)
```

- [ ] **Step 4: Add the `_is_dep_satisfied` helper**

Add this method to `BatonCore` in `core.py`, near `_is_dependency_satisfied` (around line 848):

```python
    @staticmethod
    def _is_dep_satisfied(dep_sheet: SheetExecutionState) -> bool:
        """Check if a dependency sheet provides usable output.

        A dep satisfies downstream if:
        - COMPLETED: work was done, output is available
        - SKIPPED without error_code: user intentionally skipped (skip_when),
          downstream can proceed per the user's design

        A dep does NOT satisfy if:
        - FAILED: work attempted and failed
        - CANCELLED: work aborted
        - SKIPPED with error_code: cascade-blocked, work was never done
        """
        if dep_sheet.status == BatonSheetStatus.COMPLETED:
            return True
        if (
            dep_sheet.status == BatonSheetStatus.SKIPPED
            and dep_sheet.error_code is None
        ):
            return True
        return False
```

- [ ] **Step 5: Update `_is_dependency_satisfied` to use `_is_dep_satisfied`**

In `core.py`, update the existing method at line 848 to use the new helper:

```python
    def _is_dependency_satisfied(self, job: _JobRecord, dep_num: int) -> bool:
        """Check if a dependency sheet is in a satisfied state.

        Used by get_ready_sheets to determine if a sheet can be dispatched.
        """
        dep_sheet = job.sheets.get(dep_num)
        if dep_sheet is None:
            return True  # Missing dep treated as satisfied (defensive)
        return self._is_dep_satisfied(dep_sheet)
```

- [ ] **Step 6: Run tests to verify cascade tests pass**

Run: `pytest tests/test_baton_event_flow.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/marianne/daemon/baton/core.py tests/test_baton_event_flow.py
git commit -m "fix(baton): transitive cascade propagation through SKIPPED sheets

Cascade-SKIPPED sheets (with error_code) now propagate to their own
dependents. Linear chains 1->2->3 correctly cascade to all levels.
User-skipped sheets (no error_code) still satisfy dependencies."
```

---

### Task 2: Add SheetDispatched Event

Move the DISPATCHED status assignment from `dispatch.py:188` (outside event flow) into an event that the baton processes.

**Files:**
- Modify: `src/marianne/daemon/baton/events.py`
- Modify: `src/marianne/daemon/baton/core.py`
- Modify: `src/marianne/daemon/baton/dispatch.py`
- Modify: `tests/test_baton_event_flow.py`

- [ ] **Step 1: Write failing test for SheetDispatched event**

Append to `tests/test_baton_event_flow.py`:

```python
class TestSheetDispatchedEvent:
    """DISPATCHED status must be set through an event, not directly."""

    def test_dispatched_event_sets_status(self) -> None:
        """SheetDispatched event should set sheet status to DISPATCHED."""
        from marianne.daemon.baton.events import SheetDispatched

        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, max_retries=0)}
        baton.register_job("j1", sheets, {})

        baton._handle_sheet_dispatched(SheetDispatched(
            job_id="j1", sheet_num=1, instrument="test",
        ))

        assert sheets[1].status == BatonSheetStatus.DISPATCHED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_baton_event_flow.py::TestSheetDispatchedEvent -v`
Expected: ImportError or AttributeError (SheetDispatched doesn't exist yet).

- [ ] **Step 3: Define SheetDispatched event**

In `src/marianne/daemon/baton/events.py`, add after the `SheetSkipped` class (around line 115):

```python
@dataclass(frozen=True)
class SheetDispatched:
    """A sheet has been dispatched to a musician for execution.

    Emitted by dispatch_ready() after the dispatch callback succeeds.
    The baton sets DISPATCHED status and records the dispatch timestamp.
    """

    job_id: str
    sheet_num: int
    instrument: str
    timestamp: float = field(default_factory=time.monotonic)
```

Update the `BatonEvent` union type at line 418 to include `SheetDispatched`:

```python
BatonEvent = (
    SheetAttemptResult
    | SheetSkipped
    | SheetDispatched
    | RateLimitHit
    # ... rest unchanged
)
```

- [ ] **Step 4: Add handler in core.py**

In `core.py`, add the handler method (near other sheet handlers):

```python
    def _handle_sheet_dispatched(self, event: SheetDispatched) -> None:
        """Mark sheet as dispatched to a musician."""
        job = self._jobs.get(event.job_id)
        if job is None:
            return
        sheet = job.sheets.get(event.sheet_num)
        if sheet is None:
            return
        if sheet.status in _TERMINAL_BATON_STATUSES:
            return
        sheet.status = BatonSheetStatus.DISPATCHED
        sheet.dispatched_at = event.timestamp
        self._state_dirty = True
```

Add the case to `handle_event` (after `SheetSkipped` case, around line 880):

```python
                case SheetDispatched():
                    self._handle_sheet_dispatched(event)
```

Add import of `SheetDispatched` to the imports at the top of `core.py`.

- [ ] **Step 5: Update dispatch.py to emit event instead of direct mutation**

In `src/marianne/daemon/baton/dispatch.py`, replace the direct status assignment at line 186-192:

Before:
```python
            try:
                await callback(job_id, sheet.sheet_num, sheet)
                sheet.status = BatonSheetStatus.DISPATCHED
                sheet.dispatched_at = time.monotonic()
                result.record_dispatch(job_id, sheet.sheet_num)
```

After:
```python
            try:
                await callback(job_id, sheet.sheet_num, sheet)
                # Status is set by the SheetDispatched event handler,
                # not here. Emit the event into the baton's inbox.
                baton.inbox.put_nowait(SheetDispatched(
                    job_id=job_id,
                    sheet_num=sheet.sheet_num,
                    instrument=instrument,
                ))
                result.record_dispatch(job_id, sheet.sheet_num)
```

Note: `dispatch_ready` receives `baton` as its first argument. Add `SheetDispatched` to imports.

Remove the `import time` if it was only used for `time.monotonic()` on the dispatched_at line (check other usages first).

- [ ] **Step 6: Handle timing -- dispatch must set DISPATCHED synchronously for concurrency counting**

The dispatch loop uses `sheet.status` to count running sheets. If DISPATCHED is set asynchronously via event, the concurrency count will be wrong within the same dispatch cycle.

Fix: after emitting the event, call the handler synchronously within the dispatch function:

```python
            try:
                await callback(job_id, sheet.sheet_num, sheet)
                dispatch_event = SheetDispatched(
                    job_id=job_id,
                    sheet_num=sheet.sheet_num,
                    instrument=instrument,
                )
                # Process synchronously so concurrency counting works
                # within this dispatch cycle. Event is also in inbox
                # for persistence/logging.
                baton._handle_sheet_dispatched(dispatch_event)
                result.record_dispatch(job_id, sheet.sheet_num)
```

This preserves the synchronous status update for concurrency while making the mutation go through the event handler.

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_baton_event_flow.py -v`
Expected: All tests PASS.

- [ ] **Step 8: Run full test suite to check for regressions**

Run: `pytest tests/ -x --timeout=120 -q -n auto`
Expected: Only the 48 cascade tests fail (same as before -- those are fixed in Task 5).

- [ ] **Step 9: Commit**

```bash
git add src/marianne/daemon/baton/events.py src/marianne/daemon/baton/core.py \
  src/marianne/daemon/baton/dispatch.py tests/test_baton_event_flow.py
git commit -m "refactor(baton): dispatch sets DISPATCHED through event handler

SheetDispatched event replaces direct status mutation in dispatch_ready().
The handler is called synchronously for concurrency counting, but the
mutation now flows through the event system for traceability."
```

---

### Task 3: Reorder Path 0 in Exhaustion Handler

Move Path 0 (normal retries after completion exhaustion) from first position to last-before-FAIL. Targeted recovery (fallback, healing, escalation) should be tried before burning a normal retry on the same instrument.

**Files:**
- Modify: `src/marianne/daemon/baton/core.py:494-632`
- Modify: `tests/test_baton_event_flow.py`

- [ ] **Step 1: Write test for correct exhaustion priority**

Append to `tests/test_baton_event_flow.py`:

```python
class TestExhaustionPathOrder:
    """Exhaustion handler must try targeted recovery before normal retries."""

    def test_fallback_before_normal_retry(self) -> None:
        """When completion exhausts with fallback AND normal retries available,
        fallback should be tried first."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
                max_completion=1,
                fallback_chain=["gemini-cli"],
            ),
        }
        baton.register_job("j1", sheets, {})

        # Exhaust completion budget
        sheets[1].completion_attempts = 1  # At max_completion

        baton._handle_exhaustion("j1", 1, sheets[1])

        # Should try fallback, NOT normal retry
        assert sheets[1].instrument_name == "gemini-cli", (
            "Fallback should be tried before normal retry"
        )
        assert sheets[1].status == BatonSheetStatus.PENDING

    def test_escalation_before_normal_retry(self) -> None:
        """When completion exhausts with escalation enabled AND normal retries
        available (but no fallback), escalation should fire."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
                max_completion=1,
            ),
        }
        baton.register_job("j1", sheets, {}, escalation_enabled=True)

        sheets[1].completion_attempts = 1

        baton._handle_exhaustion("j1", 1, sheets[1])

        assert sheets[1].status == BatonSheetStatus.FERMATA, (
            "Escalation should fire before normal retry"
        )

    def test_normal_retry_as_last_resort(self) -> None:
        """When completion exhausts with normal retries but no fallback,
        no healing, no escalation -- normal retry fires."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                max_retries=2,
                max_completion=1,
            ),
        }
        baton.register_job("j1", sheets, {})

        sheets[1].completion_attempts = 1

        baton._handle_exhaustion("j1", 1, sheets[1])

        assert sheets[1].status == BatonSheetStatus.RETRY_SCHEDULED
        assert sheets[1].normal_attempts == 1
```

- [ ] **Step 2: Run tests to verify first two fail**

Run: `pytest tests/test_baton_event_flow.py::TestExhaustionPathOrder -v`
Expected: `test_fallback_before_normal_retry` FAILS (Path 0 fires first). `test_escalation_before_normal_retry` FAILS. `test_normal_retry_as_last_resort` PASSES.

- [ ] **Step 3: Reorder exhaustion paths in core.py**

Replace `_handle_exhaustion` (lines 494-632) with the correct priority order:

```python
    def _handle_exhaustion(
        self, job_id: str, sheet_num: int, sheet: SheetExecutionState
    ) -> None:
        """Handle retry/completion budget exhaustion.

        The decision tree when a budget is exhausted:
        1. Instrument fallback available -> advance to next instrument
        2. Self-healing enabled -> schedule a healing attempt
        3. Escalation enabled -> enter FERMATA (pause job, await decision)
        4. Normal retries still available -> schedule a normal retry (last resort)
        5. Neither -> FAILED (propagate to dependents)

        Path 4 matters when completion budget exhausts but normal retries
        remain. The composer configured max_retries for a reason -- those
        attempts are used as a last resort before declaring failure, after
        all targeted recovery paths have been exhausted.
        """
        job = self._jobs.get(job_id)
        if job is None:
            sheet.status = BatonSheetStatus.FAILED
            sheet.error_message = f"Job '{job_id}' not found during exhaustion handling"
            sheet.error_code = "E999"
            self._state_dirty = True
            return

        # Path 1: Instrument fallback -- try the next instrument in the chain.
        # Each fallback instrument gets a fresh retry budget.
        if sheet.has_fallback_available:
            from_instrument = sheet.instrument_name or ""
            to_instrument = sheet.advance_fallback("rate_limit_exhausted")
            if to_instrument is not None:
                # Re-queue for dispatch with the new instrument
                sheet.status = BatonSheetStatus.PENDING
                self._state_dirty = True
                self._fallback_events.append(InstrumentFallback(
                    job_id=job_id,
                    sheet_num=sheet_num,
                    from_instrument=from_instrument,
                    to_instrument=to_instrument,
                    reason="rate_limit_exhausted",
                ))
                _logger.info(
                    "baton.sheet.instrument_fallback",
                    extra={
                        "job_id": job_id,
                        "sheet_num": sheet_num,
                        "from_instrument": from_instrument,
                        "to_instrument": to_instrument,
                        "reason": "rate_limit_exhausted",
                    },
                )
                return

        # Path 2: Self-healing -- try to diagnose and fix
        if (
            job.self_healing_enabled
            and sheet.healing_attempts < self._DEFAULT_MAX_HEALING
        ):
            sheet.healing_attempts += 1
            self._schedule_retry(job_id, sheet_num, sheet)
            _logger.info(
                "baton.sheet.healing_attempt",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                    "healing_attempt": sheet.healing_attempts,
                },
            )
            return

        # Path 3: Escalation -- pause for composer decision
        if job.escalation_enabled:
            sheet.status = BatonSheetStatus.FERMATA
            job.paused = True
            self._state_dirty = True
            _logger.info(
                "baton.sheet.escalated",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                    "normal_attempts": sheet.normal_attempts,
                    "healing_attempts": sheet.healing_attempts,
                },
            )
            return

        # Path 4: Normal retries still available (last resort).
        # When completion mode exhausts (agent keeps producing partial work),
        # fall back to normal retries before giving up. The composer's
        # max_retries budget is honored as a safety net after targeted
        # recovery paths are exhausted.
        # Increment normal_attempts to consume the retry budget. Without this,
        # the retry succeeds (execution_success=True, partial validation),
        # re-enters completion mode, exhausts again, and Path 4 fires
        # forever because normal_attempts never increments.
        if sheet.can_retry:
            sheet.normal_attempts += 1
            self._schedule_retry(job_id, sheet_num, sheet)
            _logger.info(
                "baton.sheet.exhaustion_retry_available",
                extra={
                    "job_id": job_id,
                    "sheet_num": sheet_num,
                    "normal_attempts": sheet.normal_attempts,
                    "max_retries": sheet.max_retries,
                },
            )
            return

        # Path 5: No recovery -- fail
        # Preserve the error from the last attempt -- it describes the actual
        # failure (validation details, execution error, etc.). Only set a
        # generic message if no attempt has left one.
        sheet.status = BatonSheetStatus.FAILED
        if not sheet.error_message:
            last = sheet.attempt_results[-1] if sheet.attempt_results else None
            if last and last.error_message:
                sheet.error_message = last.error_message
            elif last and last.validation_pass_rate < 100.0:
                sheet.error_message = (
                    f"Validation failed ({last.validation_pass_rate:.0f}% pass rate) "
                    f"after {sheet.normal_attempts + sheet.completion_attempts} attempts"
                )
            else:
                sheet.error_message = (
                    f"Retries exhausted "
                    f"(normal={sheet.normal_attempts}/{sheet.max_retries}, "
                    f"completion={sheet.completion_attempts}/{sheet.max_completion})"
                )
        if not sheet.error_code:
            sheet.error_code = "E999"
        self._state_dirty = True
        _logger.warning(
            "baton.sheet.retries_exhausted",
            extra={
                "job_id": job_id,
                "sheet_num": sheet_num,
                "attempts": sheet.normal_attempts,
            },
        )
        self._propagate_failure_to_dependents(job_id, sheet_num)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_baton_event_flow.py::TestExhaustionPathOrder -v`
Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/marianne/daemon/baton/core.py tests/test_baton_event_flow.py
git commit -m "fix(baton): reorder exhaustion paths -- targeted recovery before normal retries

Exhaustion now tries: fallback -> healing -> escalation -> normal retries -> fail.
Normal retries are a last resort, not the first thing tried after completion
exhaustion. This prevents Path 0 from intercepting targeted recovery paths."
```

---

### Task 4: Verify Shared Object Identity

Write a test that confirms the Phase 2 shared object mechanism works -- that the baton's SheetState objects are literally the same Python objects as `_live_states`.

**Files:**
- Modify: `tests/test_baton_event_flow.py`

- [ ] **Step 1: Write shared object identity test**

Append to `tests/test_baton_event_flow.py`:

```python
class TestSharedObjectIdentity:
    """Phase 2: baton and _live_states must share the same SheetState objects."""

    def test_register_shares_objects(self) -> None:
        """After register_job with live_sheets, baton's sheet objects must be
        the same Python objects (same id()) as the live_sheets dict values."""
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.core.checkpoint import CheckpointState, SheetState
        from marianne.core.sheet import Sheet

        adapter = BatonAdapter(event_bus=None, max_concurrent_sheets=4)

        # Simulate what manager.py does
        initial_sheets: dict[int, SheetState] = {
            1: SheetState(sheet_num=1),
            2: SheetState(sheet_num=2),
        }
        initial_state = CheckpointState(
            job_id="test-shared",
            job_name="test-shared",
            total_sheets=2,
            sheets=initial_sheets,
        )
        # Simulate _live_states
        live_states = {"test-shared": initial_state}

        # Create minimal Sheet entities
        sheets = [
            Sheet(num=1, instrument_name="test", instrument_config={}),
            Sheet(num=2, instrument_name="test", instrument_config={}),
        ]

        adapter.register_job(
            "test-shared",
            sheets,
            dependencies={2: [1]},
            live_sheets=initial_state.sheets,
        )

        # Verify same objects
        baton_sheets = adapter._baton._jobs["test-shared"].sheets
        live_sheets_from_state = live_states["test-shared"].sheets

        for sheet_num in [1, 2]:
            assert id(baton_sheets[sheet_num]) == id(live_sheets_from_state[sheet_num]), (
                f"Sheet {sheet_num}: baton and live_states must share the same object. "
                f"baton id={id(baton_sheets[sheet_num])}, "
                f"live id={id(live_sheets_from_state[sheet_num])}"
            )

    def test_mutations_visible_across_boundary(self) -> None:
        """Mutations on baton's sheet objects must be visible through live_states."""
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.core.checkpoint import CheckpointState, SheetState
        from marianne.core.sheet import Sheet

        adapter = BatonAdapter(event_bus=None, max_concurrent_sheets=4)

        initial_sheets: dict[int, SheetState] = {
            1: SheetState(sheet_num=1),
        }
        initial_state = CheckpointState(
            job_id="test-vis",
            job_name="test-vis",
            total_sheets=1,
            sheets=initial_sheets,
        )

        sheets = [
            Sheet(num=1, instrument_name="test", instrument_config={}),
        ]

        adapter.register_job(
            "test-vis", sheets, {},
            live_sheets=initial_state.sheets,
        )

        # Mutate through baton
        baton_sheet = adapter._baton._jobs["test-vis"].sheets[1]
        baton_sheet.status = BatonSheetStatus.COMPLETED

        # Verify visible through live_states
        live_sheet = initial_state.sheets[1]
        assert live_sheet.status == BatonSheetStatus.COMPLETED, (
            "Mutation on baton's sheet must be visible through live_states"
        )

        # Verify visible through model_dump (what mzt status calls)
        dumped = initial_state.model_dump(mode="json")
        assert dumped["sheets"]["1"]["status"] == "completed", (
            "model_dump must reflect in-place mutations"
        )
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_baton_event_flow.py::TestSharedObjectIdentity -v`
Expected: Both PASS (confirming the mechanism works). If either FAILS, we have found the root cause of the stale state bug and must fix Pydantic's dict handling before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_baton_event_flow.py
git commit -m "test(baton): verify shared object identity between baton and live_states

Confirms Phase 2 mechanism: baton and _live_states share the same SheetState
objects. Mutations through the baton are visible via model_dump()."
```

---

### Task 5: Update 48 Cascade Test Assertions

All 48 failing tests expect `FAILED` for cascade-blocked dependents. The baton now correctly marks them `SKIPPED`. Update the assertions.

**Files (16 test files):**
- Modify: `tests/test_baton_phase1_adversarial.py`
- Modify: `tests/test_baton_property_based.py`
- Modify: `tests/test_baton_adversary_m2.py`
- Modify: `tests/test_baton_adversary.py`
- Modify: `tests/test_baton_m2_adversarial.py`
- Modify: `tests/test_baton_invariants.py`
- Modify: `tests/test_baton_invariants_m1c2.py`
- Modify: `tests/test_baton_invariants_m3.py`
- Modify: `tests/test_baton_instrument_integration.py`
- Modify: `tests/test_baton_retry_integration.py`
- Modify: `tests/test_baton_user_journeys_m2.py`
- Modify: `tests/test_litmus_intelligence.py`
- Modify: `tests/test_recovery_failure_propagation.py`
- Modify: `tests/test_adversary_m2c2.py`
- Modify: `tests/test_baton_m2c2_adversarial.py`
- Modify: `tests/test_baton_m2c2_adversarial.py`

- [ ] **Step 1: Identify all assertion patterns to change**

Three patterns to fix:

Pattern A (most common): Direct cascade-dependent status assertion
```python
# Before:
assert sheets[2].status == BatonSheetStatus.FAILED
# After:
assert sheets[2].status == BatonSheetStatus.SKIPPED
```

Pattern B: Assertion with message mentioning "FAILED"
```python
# Before:
assert sheet.status == BatonSheetStatus.FAILED, "Sheet 2 should be FAILED"
# After:
assert sheet.status == BatonSheetStatus.SKIPPED, "Sheet 2 should be SKIPPED (blocked by failed dependency)"
```

Pattern C: Tests checking `dep.status != BatonSheetStatus.FAILED` (these PASS with SKIPPED -- verify they still make sense semantically)

Pattern D: Tests affected by Path 0 reorder showing `RETRY_SCHEDULED` instead of `FAILED` for the PRIMARY sheet (not cascade). These need the exhaustion path change from Task 3. After Task 3, the primary sheet will reach FAILED through the correct path order.

- [ ] **Step 2: Run the full test suite to get the exact failure list**

Run: `pytest tests/ --timeout=120 -q -n auto 2>&1 | grep "FAILED tests/" | sort`

Use this output as the exact list of test functions to update.

- [ ] **Step 3: Update each failing test**

For each failing test, read the test to understand whether the asserted sheet is:
- A **cascade-dependent** (sheet whose dependency failed) -> change FAILED to SKIPPED
- The **primary failing sheet** -> should stay FAILED (if it's showing RETRY_SCHEDULED, Task 3 fixes this)
- A **recovery test** where the expected behavior differs -> read carefully, update to match the correct cascade semantics

Do NOT bulk find-and-replace. Each test must be read individually because:
- Some tests assert FAILED on the primary sheet AND dependents (only dependents change)
- Some tests check that completed sheets are NOT affected by cascade (these should still pass)
- Recovery tests may need more nuanced updates

- [ ] **Step 4: Run the full test suite**

Run: `pytest tests/ --timeout=120 -q -n auto`
Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test(baton): update 48 cascade tests -- dependents are SKIPPED not FAILED

Cascade-blocked sheets that were never attempted are marked SKIPPED,
matching the legacy runner's semantics. Tests updated to expect SKIPPED
for dependents of failed sheets."
```

---

### Task 6: Migrate Remaining Direct `meta.status` Assignments

Route the remaining 14 direct `meta.status =` assignments (out of 16 total) through `_set_job_status`. Two are already migrated (lines 2134 and 2568).

**Files:**
- Modify: `src/marianne/daemon/manager.py`

- [ ] **Step 1: Catalog the 14 remaining sites**

Each site in `manager.py` that does `meta.status = X` without going through `_set_job_status`:

| Line | Context | Current | Change to |
|------|---------|---------|-----------|
| 1119 | Pending job started | `meta.status = QUEUED` | `await self._set_job_status(job_id, QUEUED)` |
| 1205 | Stale status correction | `meta.status = FAILED` | `await self._set_job_status(job_id, FAILED)` |
| 1251 | Stale task in pause | `meta.status = FAILED` | `await self._set_job_status(job_id, FAILED)` |
| 1268 | pause_job baton | `meta.status = PAUSED` + separate live + registry | `await self._set_job_status(job_id, PAUSED)` and remove the 3 separate lines |
| 1328 | resume_job | `meta.status = QUEUED` + separate live | `await self._set_job_status(job_id, QUEUED)` and remove live line |
| 1424 | cancel pending | `meta.status = CANCELLED` | `await self._set_job_status(job_id, CANCELLED)` |
| 1443 | cancel running | `meta.status = CANCELLED` + separate live | `await self._set_job_status(job_id, CANCELLED)` and remove live lines |
| 2145 | run_managed_task success | `meta.status = result_status` | `await self._set_job_status(job_id, result_status)` |
| 2147 | run_managed_task completed | `meta.status = COMPLETED` | (covered by 2145) |
| 2179 | timeout | `meta.status = FAILED` | `await self._set_job_status(job_id, FAILED, error_message=...)` |
| 2201 | cancelled | `meta.status = CANCELLED` | `await self._set_job_status(job_id, CANCELLED)` |
| 2213 | OS error | `meta.status = FAILED` | `await self._set_job_status(job_id, FAILED, error_message=...)` |
| 2224 | unexpected error | `meta.status = FAILED` | `await self._set_job_status(job_id, FAILED, error_message=...)` |
| 2843 | concert chaining | `meta.status = FAILED` | `await self._set_job_status(job_id, FAILED)` |
| 3067 | shutdown | `meta.status = FAILED` | `await self._set_job_status(job_id, FAILED)` |

Also remove the 6 separate `live.status =` lines at 1272, 1333, 1448, 2440, 2614 since `_set_job_status` handles live state.

- [ ] **Step 2: Migrate each site**

Work through each site from the table above. For each:
1. Replace `meta.status = X` with `await self._set_job_status(job_id, X, ...)`
2. Remove any adjacent `live.status =` and `await self._registry.update_status(...)` calls that are now redundant
3. Note: some sites (2145-2147) have a conditional -- collapse to one `_set_job_status` call
4. Note: some sites (1268, pause_job) already send a baton event AND do direct mutation. After migration, the direct mutation is handled by `_set_job_status` and the baton event stays separate (it controls dispatch pausing, not status display)

Special cases:
- Line 2440-2444 (`_run_via_baton` post-completion): Currently sets `live.status` + `live.completed_at`. After migration, `_set_job_status` handles status. Add `completed_at` setting to `_set_job_status` for terminal statuses, OR set it separately (it's a timestamp, not a status store divergence risk).
- Line 2614-2618 (`_resume_via_baton` post-completion): Same as above.

- [ ] **Step 3: Run tests**

Run: `pytest tests/ --timeout=120 -q -n auto`
Expected: 0 failures.

- [ ] **Step 4: Commit**

```bash
git add src/marianne/daemon/manager.py
git commit -m "refactor(manager): route all job status changes through _set_job_status

All 16 meta.status assignments now go through the unified method.
All 6 separate live.status assignments removed (handled by _set_job_status).
Three status stores (meta, live, registry) are always updated atomically."
```

---

### Task 7: Final Verification

Run the full quality gate: tests, types, lint.

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ --timeout=120 -q -n auto`
Expected: 0 failures.

- [ ] **Step 2: Run type checker**

Run: `mypy src/`
Expected: 0 errors (or only pre-existing errors unrelated to these changes).

- [ ] **Step 3: Run linter**

Run: `ruff check src/`
Expected: 0 errors.

- [ ] **Step 4: Final commit if any cleanup needed**

If type checking or linting reveals issues from these changes, fix and commit.

---

## Summary

| Task | What it does | Files changed |
|------|-------------|---------------|
| 1 | Fix cascade transitive propagation | core.py, test_baton_event_flow.py |
| 2 | Route DISPATCHED through event | events.py, core.py, dispatch.py, test_baton_event_flow.py |
| 3 | Reorder exhaustion Path 0 to last | core.py, test_baton_event_flow.py |
| 4 | Verify shared object identity | test_baton_event_flow.py |
| 5 | Update 48 cascade test assertions | 16 test files |
| 6 | Migrate 14 meta.status bypasses | manager.py |
| 7 | Final quality gate | verification only |
