# Rate Limit Intelligence

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse actual wait durations from rate limit errors, hold sheets per-model (not per-instrument), and automatically fall back to alternate instruments when available instead of waiting.

**Architecture:** Rate limits flow through three layers: backend detects and reports, musician relays to baton, baton coordinates hold/release across all sheets on the affected model. Today the backend returns `rate_limited=True` but discards the retry-after duration. The baton hardcodes `wait_seconds=60.0` and holds per-instrument. This plan makes each layer carry the actual data: backend extracts the wait duration, the event carries the model and duration, and the baton holds per-model with fallback awareness.

**Tech Stack:** Python 3.12, asyncio, regex (duration parsing), Pydantic v2, pytest

**Prior analysis context:**
- Current rate limit flow: backend `_detect_rate_limit()` → bool only, duration discarded
- `SheetAttemptResult.rate_limited=True` → baton handler injects synthetic `RateLimitHit(wait_seconds=60.0)` — hardcoded
- `RateLimitHit` handler marks instrument rate-limited, moves ALL dispatched sheets on that instrument to WAITING
- `RateLimitExpired` timer (60s) clears the flag, sheets go PENDING → re-dispatch
- Rate limits are per-model but baton treats them per-instrument
- Anthropic API returns `Retry-After` headers and error messages with timestamps/durations
- Claude Code exits with rate limit text in stdout/stderr containing wait times
- Sheets already have `instrument_fallbacks` (fallback chain) — the mechanism exists but isn't connected to rate limit events
- The musician emits `rate_limited=True` on `SheetAttemptResult` then the baton handler synthesizes a `RateLimitHit` — two events for one rate limit, indirect

---

## File Structure

| File | Role | Action |
|------|------|--------|
| `src/marianne/core/errors/classifier.py` | Error classification | Extract wait duration from rate limit errors |
| `src/marianne/backends/base.py` | Backend base | Add `rate_limit_wait_seconds` to `ExecutionResult` |
| `src/marianne/backends/anthropic_api.py` | Anthropic backend | Extract `Retry-After` header / error body duration |
| `src/marianne/backends/claude_cli.py` | Claude CLI backend | Extract wait duration from stdout/stderr text |
| `src/marianne/daemon/baton/events.py` | Event definitions | Add `wait_seconds` and `model` to `RateLimitHit`; add `model` to `SheetAttemptResult` |
| `src/marianne/daemon/baton/core.py` | Event handlers | Per-model rate limit tracking; fallback-aware hold logic |
| `src/marianne/daemon/baton/state.py` | State model | Per-model rate limit state on `InstrumentState` |
| `src/marianne/daemon/baton/musician.py` | Musician | Emit `RateLimitHit` directly instead of flag on `SheetAttemptResult` |
| `tests/test_rate_limit_intelligence.py` | Tests | Duration parsing, per-model hold, fallback on rate limit |

---

### Task 1: Extract Wait Duration from Rate Limit Errors

The backend already detects rate limits but returns bool. Make it return the actual wait duration.

**Files:**
- Modify: `src/marianne/core/errors/classifier.py`
- Modify: `src/marianne/backends/base.py`
- Create: `tests/test_rate_limit_intelligence.py`

- [ ] **Step 1: Write failing test for duration extraction**

```python
# tests/test_rate_limit_intelligence.py
"""Tests for rate limit intelligence — duration parsing, per-model hold, fallback."""

from marianne.core.errors import ErrorClassifier


class TestRateLimitDurationParsing:
    """Extract actual wait durations from rate limit error text."""

    def test_anthropic_retry_after_seconds(self) -> None:
        """Parse 'Please retry after X seconds' from Anthropic errors."""
        classifier = ErrorClassifier()
        duration = classifier.extract_rate_limit_wait(
            "Rate limit exceeded. Please retry after 120 seconds."
        )
        assert duration == 120.0

    def test_anthropic_retry_after_timestamp(self) -> None:
        """Parse ISO timestamp from Anthropic retry-after."""
        classifier = ErrorClassifier()
        duration = classifier.extract_rate_limit_wait(
            "Rate limit exceeded. Retry after 2026-04-08T23:15:00Z"
        )
        # Should return seconds until that time (>0)
        assert duration is not None
        assert duration > 0

    def test_claude_code_rate_limit_minutes(self) -> None:
        """Parse 'try again in N minutes' from Claude Code output."""
        classifier = ErrorClassifier()
        duration = classifier.extract_rate_limit_wait(
            "You've hit a rate limit. Please try again in 5 minutes."
        )
        assert duration == 300.0

    def test_quota_exhaustion_hours(self) -> None:
        """Parse quota reset duration."""
        classifier = ErrorClassifier()
        duration = classifier.extract_rate_limit_wait(
            "Daily token quota exhausted. Resets in 3 hours."
        )
        assert duration == 10800.0

    def test_no_duration_returns_none(self) -> None:
        """When no parseable duration, return None (caller uses default)."""
        classifier = ErrorClassifier()
        duration = classifier.extract_rate_limit_wait(
            "Rate limit exceeded."
        )
        assert duration is None

    def test_retry_after_header_value(self) -> None:
        """Parse raw Retry-After header value (integer seconds)."""
        classifier = ErrorClassifier()
        duration = classifier.extract_rate_limit_wait(
            "Retry-After: 180"
        )
        assert duration == 180.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rate_limit_intelligence.py -v`
Expected: AttributeError — `extract_rate_limit_wait` doesn't exist.

- [ ] **Step 3: Implement `extract_rate_limit_wait` on ErrorClassifier**

Add method to `src/marianne/core/errors/classifier.py`:

```python
def extract_rate_limit_wait(self, text: str) -> float | None:
    """Extract wait duration in seconds from rate limit error text.

    Parses common patterns from Anthropic API, Claude Code CLI, and
    generic rate limit messages. Returns None if no parseable duration
    found (caller should use a sensible default).

    Patterns matched:
    - "retry after N seconds"
    - "try again in N minutes"
    - "resets in N hours"
    - "Retry-After: N" (header value)
    - ISO 8601 timestamps (calculates seconds until)
    """
    import re
    from datetime import datetime, timezone

    if not text:
        return None

    # Pattern: N seconds
    m = re.search(r'(?:retry|wait|again)\s+(?:after\s+)?(\d+)\s*seconds?', text, re.I)
    if m:
        return float(m.group(1))

    # Pattern: N minutes
    m = re.search(r'(?:retry|wait|again)\s+(?:after\s+|in\s+)?(\d+)\s*minutes?', text, re.I)
    if m:
        return float(m.group(1)) * 60.0

    # Pattern: N hours
    m = re.search(r'(?:resets?|wait|again)\s+(?:in\s+)?(\d+)\s*hours?', text, re.I)
    if m:
        return float(m.group(1)) * 3600.0

    # Pattern: Retry-After header (integer seconds)
    m = re.search(r'Retry-After:\s*(\d+)', text)
    if m:
        return float(m.group(1))

    # Pattern: ISO 8601 timestamp
    m = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)', text)
    if m:
        try:
            target = datetime.fromisoformat(m.group(1).replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            diff = (target - now).total_seconds()
            return max(diff, 0.0)
        except ValueError:
            pass

    return None
```

- [ ] **Step 4: Add `rate_limit_wait_seconds` to `ExecutionResult`**

In `src/marianne/backends/base.py`, add field:

```python
rate_limit_wait_seconds: float | None = None
"""Parsed wait duration from rate limit error. None = use default."""
```

- [ ] **Step 5: Wire backends to extract and pass duration**

In `src/marianne/backends/claude_cli.py` — after `_detect_rate_limit` returns True, extract duration:

```python
if rate_limited:
    wait_seconds = self._error_classifier.extract_rate_limit_wait(
        f"{stdout}\n{stderr}"
    )
    # ... pass to ExecutionResult
```

In `src/marianne/backends/anthropic_api.py` — extract from API exception:

```python
if rate_limited:
    wait_seconds = self._error_classifier.extract_rate_limit_wait(str(e))
    # Also check Retry-After header if available
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_rate_limit_intelligence.py -v`
Expected: All duration parsing tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/marianne/core/errors/classifier.py src/marianne/backends/base.py \
  src/marianne/backends/claude_cli.py src/marianne/backends/anthropic_api.py \
  tests/test_rate_limit_intelligence.py
git commit -m "feat(rate-limit): extract actual wait duration from rate limit errors

ErrorClassifier.extract_rate_limit_wait() parses seconds, minutes, hours,
Retry-After headers, and ISO timestamps from error text. Backends pass
the parsed duration through ExecutionResult.rate_limit_wait_seconds."
```

---

### Task 2: Per-Model Rate Limit Tracking

Rate limits are per-model, not per-instrument. `claude-code` running `claude-sonnet` hitting a limit shouldn't block `claude-code` running `claude-opus`.

**Files:**
- Modify: `src/marianne/daemon/baton/state.py` — per-model rate limit on InstrumentState
- Modify: `src/marianne/daemon/baton/events.py` — add `model` to `RateLimitHit`
- Modify: `src/marianne/daemon/baton/core.py` — per-model hold/release logic
- Modify: `tests/test_rate_limit_intelligence.py`

- [ ] **Step 1: Write failing test for per-model hold**

```python
class TestPerModelRateLimitHold:
    """Rate limits should hold per-model, not per-instrument."""

    def test_rate_limit_holds_only_matching_model(self) -> None:
        """When claude-sonnet is rate limited, claude-opus sheets continue."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import RateLimitHit
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code",
                model="claude-sonnet-4-5", max_retries=1,
            ),
            2: SheetExecutionState(
                sheet_num=2, instrument_name="claude-code",
                model="claude-opus-4-6", max_retries=1,
            ),
        }
        baton.register_job("j1", sheets, {})

        # Dispatch both
        sheets[1].status = BatonSheetStatus.DISPATCHED
        sheets[2].status = BatonSheetStatus.DISPATCHED

        # Rate limit hits claude-sonnet only
        baton._handle_rate_limit_hit(RateLimitHit(
            instrument="claude-code",
            model="claude-sonnet-4-5",
            wait_seconds=120.0,
            job_id="j1",
            sheet_num=1,
        ))

        # Sheet 1 (sonnet) should be WAITING
        assert sheets[1].status == BatonSheetStatus.WAITING

        # Sheet 2 (opus) should still be DISPATCHED
        assert sheets[2].status == BatonSheetStatus.DISPATCHED
```

- [ ] **Step 2: Add `model` field to `RateLimitHit` event**

In `events.py`, add `model: str | None = None` to `RateLimitHit`.

- [ ] **Step 3: Update `_handle_rate_limit_hit` for per-model filtering**

In `core.py`, when model is specified on the event, only move sheets matching that model to WAITING:

```python
def _handle_rate_limit_hit(self, event: RateLimitHit) -> None:
    # ... existing instrument-level logic ...

    for job in self._jobs.values():
        for sheet in job.sheets.values():
            if sheet.instrument_name != event.instrument:
                continue
            # Per-model: only hold sheets on the affected model
            if event.model and sheet.model != event.model:
                continue
            if sheet.status in (BatonSheetStatus.DISPATCHED, BatonSheetStatus.IN_PROGRESS):
                sheet.status = BatonSheetStatus.WAITING
```

- [ ] **Step 4: Run tests, commit**

---

### Task 3: Musician Emits RateLimitHit Directly

The musician currently sets `rate_limited=True` on `SheetAttemptResult`, then the baton handler synthesizes a `RateLimitHit`. Two events for one rate limit. The musician should emit `RateLimitHit` directly.

**Files:**
- Modify: `src/marianne/daemon/baton/musician.py` — emit `RateLimitHit` instead of flag
- Modify: `src/marianne/daemon/baton/core.py` — remove synthetic injection from `_handle_attempt_result`
- Modify: `tests/test_rate_limit_intelligence.py`

- [ ] **Step 1: Update musician to emit RateLimitHit**

In `musician.py`, when `exec_result.rate_limited`:

```python
if exec_result.rate_limited:
    # Emit RateLimitHit directly — one event, one handler
    wait_seconds = exec_result.rate_limit_wait_seconds or 60.0
    baton_inbox.put_nowait(RateLimitHit(
        instrument=instrument_name,
        model=exec_result.model_used or model_from_config,
        wait_seconds=wait_seconds,
        job_id=job_id,
        sheet_num=sheet_num,
    ))
    return  # Don't emit SheetAttemptResult for rate limits
```

- [ ] **Step 2: Remove synthetic RateLimitHit injection from core.py**

In `_handle_attempt_result`, remove the `if event.rate_limited:` block that injects the synthetic event. Rate limits no longer arrive as `SheetAttemptResult`.

- [ ] **Step 3: Run tests, commit**

---

### Task 4: Fallback on Rate Limit

When a model is rate limited and a sheet has fallback instruments, advance to the fallback instead of waiting. Only sheets without fallbacks wait for the timer.

**Files:**
- Modify: `src/marianne/daemon/baton/core.py` — fallback logic in rate limit handler
- Modify: `tests/test_rate_limit_intelligence.py`

- [ ] **Step 1: Write failing test for fallback on rate limit**

```python
class TestFallbackOnRateLimit:
    """Sheets with fallback instruments should try the fallback instead of waiting."""

    def test_sheet_with_fallback_advances_on_rate_limit(self) -> None:
        """When rate limited, a sheet with fallback instruments advances
        to the next instrument instead of waiting."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import RateLimitHit
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code",
                model="claude-sonnet-4-5", max_retries=1,
                fallback_chain=["gemini-cli"],
            ),
        }
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.DISPATCHED

        baton._handle_rate_limit_hit(RateLimitHit(
            instrument="claude-code",
            model="claude-sonnet-4-5",
            wait_seconds=3600.0,  # 1 hour wait
            job_id="j1",
            sheet_num=1,
        ))

        # Sheet should advance to fallback, not wait
        assert sheets[1].instrument_name == "gemini-cli"
        assert sheets[1].status == BatonSheetStatus.PENDING

    def test_sheet_without_fallback_waits(self) -> None:
        """When rate limited with no fallback, sheet waits for timer."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import RateLimitHit
        from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState

        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code",
                model="claude-sonnet-4-5", max_retries=1,
            ),
        }
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.DISPATCHED

        baton._handle_rate_limit_hit(RateLimitHit(
            instrument="claude-code",
            model="claude-sonnet-4-5",
            wait_seconds=120.0,
            job_id="j1",
            sheet_num=1,
        ))

        # No fallback — sheet waits
        assert sheets[1].status == BatonSheetStatus.WAITING
```

- [ ] **Step 2: Implement fallback-aware rate limit handling**

In `_handle_rate_limit_hit`, after marking the instrument:

```python
# For each affected sheet: try fallback before waiting
for job in self._jobs.values():
    for sheet in job.sheets.values():
        if sheet.instrument_name != event.instrument:
            continue
        if event.model and sheet.model != event.model:
            continue
        if sheet.status not in (BatonSheetStatus.DISPATCHED, BatonSheetStatus.IN_PROGRESS):
            continue

        # If sheet has a fallback instrument, advance instead of waiting
        if sheet.has_fallback_available:
            from_inst = sheet.instrument_name or ""
            to_inst = sheet.advance_fallback("rate_limited")
            if to_inst is not None:
                sheet.status = BatonSheetStatus.PENDING
                self._fallback_events.append(InstrumentFallback(
                    job_id=job.job_id,
                    sheet_num=sheet.sheet_num,
                    from_instrument=from_inst,
                    to_instrument=to_inst,
                    reason="rate_limited",
                ))
                continue

        # No fallback — wait for timer
        sheet.status = BatonSheetStatus.WAITING
```

- [ ] **Step 3: Run tests, commit**

---

### Task 5: Wire Parsed Duration Through the Full Flow

Connect the parsed `wait_seconds` from the backend through the musician to the baton's timer.

**Files:**
- Modify: `src/marianne/daemon/baton/musician.py`
- Modify: `src/marianne/daemon/baton/core.py`
- Modify: `tests/test_rate_limit_intelligence.py`

- [ ] **Step 1: Write integration test**

```python
class TestParsedDurationEndToEnd:
    """Parsed wait duration flows from backend through to baton timer."""

    def test_parsed_duration_used_for_timer(self) -> None:
        """RateLimitHit with parsed wait_seconds schedules timer for that duration."""
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import RateLimitHit
        from marianne.daemon.baton.state import SheetExecutionState

        baton = BatonCore()
        sheets = {1: SheetExecutionState(sheet_num=1, max_retries=1)}
        baton.register_job("j1", sheets, {})
        sheets[1].status = BatonSheetStatus.DISPATCHED

        # Rate limit with parsed 300s wait (not default 60s)
        baton._handle_rate_limit_hit(RateLimitHit(
            instrument="claude-code",
            wait_seconds=300.0,
            job_id="j1",
            sheet_num=1,
        ))

        # Verify the instrument's rate_limit_expires_at uses the parsed duration
        inst = baton._instruments.get("claude-code")
        assert inst is not None
        assert inst.rate_limited is True
        # expires_at should be ~300s from now, not ~60s
        import time
        remaining = inst.rate_limit_expires_at - time.monotonic()
        assert remaining > 250.0, f"Expected ~300s remaining, got {remaining:.0f}s"
```

- [ ] **Step 2: Verify existing timer scheduling uses event.wait_seconds**

Check that `_handle_rate_limit_hit` already uses `event.wait_seconds` for the timer (it does — line 1212: `handle = self._timer.schedule(event.wait_seconds, expiry_event)`). The key change is that `event.wait_seconds` now carries the parsed value instead of hardcoded 60.0.

- [ ] **Step 3: Run full test suite, commit**

---

### Task 6: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ --timeout=120 -q -n auto`
Expected: 0 failures.

- [ ] **Step 2: Run type checker and linter**

Run: `mypy src/ && ruff check src/`

- [ ] **Step 3: Integration test with running conductor**

Create a test score that triggers a rate limit (use a model with low limits or mock the backend response). Verify:
1. `mzt status` shows "waiting" with the correct duration
2. Sheets with fallbacks advance to alternate instruments
3. Sheets without fallbacks wait for the parsed duration
4. When the timer fires, sheets re-dispatch

---

## Summary

| Task | What it does |
|------|-------------|
| 1 | Parse actual wait durations from error text (seconds, minutes, hours, timestamps) |
| 2 | Per-model rate limit tracking — sonnet limited doesn't block opus |
| 3 | Musician emits RateLimitHit directly — one event, one handler, clean flow |
| 4 | Fallback on rate limit — advance to alternate instrument instead of waiting |
| 5 | Wire parsed duration through the full flow to the baton's timer |
| 6 | Final verification |
