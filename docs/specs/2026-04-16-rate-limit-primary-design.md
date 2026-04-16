# Rate Limit Primary Flag — Design Spec

**Status:** DRAFT v3
**Date:** 2026-04-16
**Author:** Legion
**Reviewer:** Claude Opus 4.6 (1M context) — v2 review applied in v3
**Depends on:** B1+B5 fixes (implemented same session)
**Related:** [Process Lifecycle](2026-04-16-process-lifecycle-design.md) — shares edit territory in `daemon/baton/` (see Coordination section)

## Problem

`ExecutionResult.rate_limited` is a boolean that conflates detection with causation.
CLI instruments log rate limit retries to stderr even when they handle them internally
and succeed. The current system has no way to express "rate limiting happened but was
not the reason for failure."

### Failure Modes (Fixed by B1+B5, Prevented by B6)

1. **Successful execution discarded:** Gemini-cli retries 429 internally, succeeds,
   but stderr contains "429". Marianne classifies as rate_limited. Baton discards
   the successful work, sets sheet to WAITING. Fixed by B5 (success takes priority).

2. **Real error masked:** Gemini-cli fails with sandbox error (exit 1), but stderr
   also contains "429" from startup. Marianne classifies as rate_limited, masking
   the real error. Baton waits instead of reporting the sandbox failure. Fixed by
   B1 (success check in backend).

3. **Rate limit cascade across instruments:** False positive rate limit on one
   instrument triggers fallback cascade. Circuit breaker trips based on false
   positives. Other sheets on the same instrument are affected. Not fully fixed
   by B1+B5 — requires B6 to distinguish primary vs incidental rate limiting.

## Design

### New Field on ExecutionResult — Tri-State via `bool | None`

```python
@dataclass
class ExecutionResult:
    # Existing — kept for backward compatibility; deprecated for direct consumer use.
    rate_limited: bool = False

    # New: was rate limiting the PRIMARY cause of failure?
    # - True  : confirmed primary cause; baton should wait and retry
    # - False : detected but NOT primary (or not detected at all)
    # - None  : backend did not compute primary classification (older backend);
    #           consumers fall back to `rate_limited` for backward compatibility
    rate_limit_primary: bool | None = None
```

**Why tri-state:** A `bool = False` default cannot distinguish "backend did not
classify" from "backend classified as non-primary." The v2 spec promised a
backward-compatibility fallback to `rate_limited` "when `rate_limit_primary` is
not set," but a plain `bool` is always set. `None` makes the unset case
expressible.

**Semantics:**
- `rate_limited=True, rate_limit_primary=True` — Rate limit caused the failure.
  Instrument could not complete. Baton should wait and retry.
- `rate_limited=True, rate_limit_primary=False` — Rate limit detected in stderr
  but was NOT the cause of failure. Either the instrument retried internally and
  succeeded, or the instrument failed for a different reason. Baton should treat
  as normal success/failure.
- `rate_limited=True, rate_limit_primary=None` — Older backend that has not been
  updated; consumers should fall back to `rate_limited` as the signal (the pre-B6
  semantics). Deprecated path.
- `rate_limited=False` — No rate limit detected. `rate_limit_primary` is `False`
  (or `None` for older backends; either is safe).

**Deprecation plan for `rate_limited`:**
- v3 (this spec): add `rate_limit_primary`, keep `rate_limited`, mark
  `rate_limited` as **deprecated for direct consumer use** in its docstring.
  Consumer code paths other than backends stop reading `rate_limited` directly;
  they read `rate_limit_primary` and fall back to `rate_limited` only when it
  is `None`.
- Next major version (after all shipped backends populate `rate_limit_primary`):
  remove the fallback path. `rate_limited` remains on the dataclass as a detection
  flag but is not load-bearing for causation.
- Long-term: consider renaming `rate_limited` to `rate_limit_detected` to match
  intent. Deferred; not worth the test churn now.

### Classification Logic in cli_backend.py

```python
def _parse_result(self, stdout, stderr, exit_code, duration):
    is_success = exit_code in errors.success_exit_codes
    rate_detected = self._check_rate_limit(stdout, stderr)
    error_type = self._classify_output_errors(
        stdout, stderr, is_success=is_success,
    )

    # Rate limit exit codes from instrument profile (optional).
    # When defined, provides a positive signal that the exit code
    # itself indicates rate limiting (e.g., claude-code exits 2).
    rl_exit_codes = getattr(self._profile.errors, "rate_limit_exit_codes", None)

    # Classification names come from the canonical error code set — do not use
    # string literals outside this module.
    from marianne.core.errors.codes import ErrorType  # or equivalent canonical source
    rate_limit_error_types = {ErrorType.RATE_LIMITED, ErrorType.CAPACITY}

    # Rate limit is primary ONLY when:
    # 1. Rate limit pattern was detected in stderr/stdout, AND
    # 2. Execution failed (not success), AND
    # 3. We can CONFIRM causation via one of:
    #    a. Exit code matches profile's rate_limit_exit_codes, OR
    #    b. Error classifier identified rate limiting as the error type.
    #
    # When we detect a 429 pattern but can't confirm it caused the
    # failure (error_type is None, no exit code signal), we default
    # to rate_limit_primary=False. False negatives (missing a real
    # rate limit, retrying as normal failure) are cheaper than false
    # positives (60-minute wait cycle + instrument cascade for a
    # parse error that happened to have an incidental 429 in stderr).
    # See "Cost framing" below for the back-of-envelope.
    rate_primary = bool(
        rate_detected
        and not is_success
        and (
            (rl_exit_codes is not None and exit_code in rl_exit_codes)
            or error_type in rate_limit_error_types
        )
    )

    # Invariant: rate_limit_wait_seconds is meaningful only when primary.
    wait_seconds = self._parse_wait_seconds(stderr) if rate_primary else None

    return ExecutionResult(
        success=is_success,
        rate_limited=rate_detected,
        rate_limit_primary=rate_primary,
        rate_limit_wait_seconds=wait_seconds,
        error_type=error_type,
        ...
    )
```

**Key design choice: confirmation required, not absence of alternatives.**
The original spec used `error_type is None` (no other explanation) to infer
rate limit causation. This fails for unknown errors with incidental 429 patterns
(e.g., opencode NDJSON parse error with startup 429 in stderr). The revised
logic requires positive confirmation: either a matching exit code or an explicit
error classification. When neither is available, the safe default is "not primary."

**Centralization rationale (why classify in the parser, not downstream):**
Most consumers (baton, learning store, dashboard) want the same answer to "was
this a rate limit?" — there is no consumer-specific policy today. Centralizing
the classification at parse time avoids drift between consumers and avoids each
consumer re-implementing the same regex against `stderr`. If a future consumer
needs different rules, the parser can report raw observations
(`exit_code`, `rate_limit_regex_hits`) alongside the classification and that
consumer can derive its own verdict without displacing the default.

**Classifier dependency (acknowledged):**
Once exit-code-based detection is unavailable (most profiles won't populate
`rate_limit_exit_codes`), the `_classify_output_errors` method bears the full
weight of the confirmation check. This spec's correctness depends on that
classifier's coverage. Before shipping B6, audit `_classify_output_errors`
against a corpus of real rate-limit stderr samples from each CLI instrument
(claude-code, gemini-cli, opencode, goose) and record the coverage in the
implementation plan. Missing cases either land as profile-specific
`rate_limit_exit_codes` entries (preferred, positive signal) or as additional
classifier patterns.

**Instrument profile extension:**
```yaml
# In instrument profile YAML (optional)
errors:
  rate_limit_exit_codes: [2]  # e.g., claude-code exits 2 on rate limit
```

When `rate_limit_exit_codes` is not defined in the profile (most instruments),
the exit code branch is skipped and only the error_type branch applies. Instruments
that do not have distinct rate limit exit codes rely on `_classify_output_errors`
returning `RATE_LIMITED` or `CAPACITY`.

**Schema change to accept this field:** The Pydantic model behind the instrument
profile YAML (in `instruments/profile.py` where `errors:` is defined) gains a
new optional field `rate_limit_exit_codes: list[int] | None = None`. Without the
schema change, YAML profiles with this key will fail validation — so this must
land in the same commit as the classification logic. See the File Change Map.

### `_check_rate_limit` — Behavior Change, Not Rename

The method keeps its name. The `is_success` parameter is removed — detection
always runs regardless of exit code. The guard moves to classification logic
in `_parse_result` (the `rate_limit_primary` derivation above).

Updated docstring:
```python
def _check_rate_limit(self, stdout: str, stderr: str) -> bool:
    """Detect whether rate limit patterns appear in output.

    Returns True if any rate limit pattern from the instrument profile
    matches stdout or stderr. This is DETECTION only — it does not
    determine whether rate limiting was the cause of failure. Use
    rate_limit_primary on ExecutionResult for causation.
    """
```

**Rationale — and the churn cost:** Removing the `is_success` parameter is
itself a breaking signature change that will touch every test that mocks
`_check_rate_limit`. The v2 spec called out rename churn as the reason to keep
the name; the signature change has its own churn, and both are unavoidable.
Keeping the name at least means call-sites do not need to be found and
renamed — only the argument list is updated. That is the net saving.

### Consumer Changes

**Baton core (`_handle_attempt_result`):**
```python
# Use primary signal when available; fall back to rate_limited for older backends.
primary = (
    event.rate_limit_primary
    if event.rate_limit_primary is not None
    else event.rate_limited
)

if primary:
    # Genuine rate limit failure — wait and retry
    sheet.status = BatonSheetStatus.WAITING
    self._inbox.put_nowait(RateLimitHit(...))
    return

# If rate_limited but not primary, fall through to normal handling.
# The instrument either succeeded or failed for a different reason.
```

**SheetAttemptResult event:**
Add `rate_limit_primary: bool | None = None` field. The musician passes through
from ExecutionResult.

**Rate limit exhaustion and instrument fallback:**
Sheets with `rate_limit_primary=False` (or `None` falling back to
`rate_limited=False`) do NOT contribute to rate limit exhaustion counting. Only
genuine rate limits (`primary == True` by the rule above) trigger `RateLimitHit`
events, which drive the exhaustion-to-fallback cascade. This is enforced by the
baton's existing gate: `RateLimitHit` is only emitted inside the `primary`
branch.

An instrument that frequently shows incidental 429s in stderr (e.g., gemini-cli
startup retries) will NOT have its circuit breaker tripped or its sheets cascaded
to fallback instruments — because those 429s are `rate_limit_primary=False` and
never reach the rate limit handling path.

**RateLimitCoordinator:**
The coordinator (`daemon/rate_coordinator.py`) receives events via `RateLimitHit`
in the baton path, which is already gated by the primary check. No change
needed for the baton path. The old runner callback path (`manager._on_rate_limit`)
should be guarded during the transition period (before runner removal completes).
After runner deletion, this path is gone.

**Dashboard/status display:**
When `rate_limited=True, rate_limit_primary=False`, show a label distinguishing
the case from genuine rate limiting. Candidate labels (implementation choice,
not a spec-level decision):
- `rate_limit (handled)` — minimal change, matches the "(rate limit handled
  internally)" phrasing in v2
- `rate_limit (soft)` vs `rate_limit (primary)` — parallel pairing
- `retry_succeeded` — when `rate_limited=True, success=True`

The requirement is that a composer looking at the dashboard can distinguish
"instrument handled a 429 internally" from "instrument is actually blocked on a
rate limit" without reading the logs. Final label choice is up to whoever
implements the display; the data model supports all three options.

**Learning store:**
Pattern detection should use `rate_limit_primary` for instrument health tracking.
Incidental rate limits (`rate_limit_primary=False`) should not count toward
circuit breaker thresholds.

**Retroactive semantics for existing learning store records:**
Per MN-010 (never delete learning data), existing records in
`~/.marianne/global-learning.db` keyed on `rate_limited=True` were written
under the pre-B6 semantics (detection only). They may include incidental rate
limits that B6 would classify as non-primary. Migration plan:

- **No backfill of existing records.** We cannot retroactively determine primary
  vs incidental from the raw stored signal; the classifier needs the full stderr
  and exit code at classification time, and the learning store keeps a
  distillation rather than the raw output.
- **Forward-only discrimination.** New records land with both `rate_limited`
  and `rate_limit_primary` populated. Pattern queries that want the new
  semantics filter on `rate_limit_primary=True`. Queries that want historical
  counts use `rate_limited=True` as before.
- **Mixed-window pattern detection:** pattern weighters that aggregate across
  pre-B6 and post-B6 records treat the pre-B6 records as "unknown causation"
  and weight them lower. Schema may need a new column
  `rate_limit_primary INTEGER NULL` (null = pre-B6) to make the distinction
  queryable.
- **No deletion.** Old records remain. The aggregator learns to downweight
  ambiguous evidence over time.

This is a small forward-compat schema bump on the learning store (additive
nullable column). Not E-005 territory (no data loss, no destructive migration).

**`rate_limit_wait_seconds` field semantics:**
```python
rate_limit_wait_seconds: float | None = None
"""Parsed wait duration from rate limit error, in seconds.

Set only when rate_limit_primary is True. Explicitly None when
rate_limit_primary is False or None — prevents stale values from
internally-handled retries leaking into scheduling or display.
"""
```

**Enforcement:** The backend only populates this field when `rate_primary` is
True (see the classification code snippet above). Consumers that read it when
primary is False will see None, not a misleading number.

### Migration from B1+B5 and Rollout Sequencing

B1+B5 are immediate fixes that prevent the worst damage:
- B1: `_check_rate_limit` returns False on success (prevents false positives at source)
- B5: Baton checks `execution_success` before `rate_limited` (prevents work discard)

B6 replaces both with a cleaner architecture:
- B1 is subsumed: detection always runs, but `rate_limit_primary` is False for successes
- B5 is subsumed: baton checks `rate_limit_primary` (with fallback to `rate_limited` for older backends), not `rate_limited` directly
- Additionally: failed executions with incidental rate limit patterns are correctly
  classified by their primary error, not masked as rate limits
- Additionally: unknown failures with incidental 429s default to "not rate limit"
  instead of the dangerous false-positive path

**Rollout steps (in order):**

1. **Ship B6 additively.** `rate_limit_primary` is set on all new ExecutionResults.
   B1+B5 guards remain in place. New baton code paths consult
   `rate_limit_primary` with a fallback to `rate_limited` when it is None.
2. **Verify on historical data.** Before removing B1/B5 guards, run the new
   classifier over the last 30 days of execution records from the learning
   store and compare against the B1+B5 ground truth. Acceptance criterion:
   `rate_limit_primary=True` is a strict subset of what B1+B5 would have
   flagged as rate-limited failures; no case where B6 says "primary" but B1+B5
   would have said "not a rate limit."
3. **Remove B1+B5 gates.** Once (2) holds, the B1 success guard in
   `_check_rate_limit` and the B5 priority check in the baton attempt handler
   are redundant and can be retired in a follow-up commit.
4. **Drop the `rate_limit_primary=None` fallback.** After all shipped backends
   have been updated to set the field, the baton consumer can treat `None` as
   an error rather than falling back.

### Cost Framing (false-negative vs false-positive)

The spec treats false negatives (real rate limit misclassified as not-primary)
as cheaper than false positives (incidental 429 classified as primary). Rough
costs:

- **False positive (old behavior):** Baton marks sheet WAITING, emits
  `RateLimitHit`, which triggers the rate-limit coordinator. Coordinator's
  retry delay is instrument-specific but lands in the tens of minutes for
  provider-level 429 patterns (claude: ~60 min reset window when tripped
  repeatedly; gemini free tier: ~60 min). If the circuit breaker trips, a
  further instrument fallback cascade compounds the cost. Net: tens of minutes
  of wall-clock wasted per false positive, plus potential work loss if the
  cascade ends up on a worse instrument.
- **False negative (new behavior):** Sheet is retried with the configured
  normal-retry delay (seconds to a minute), the retry either works or the
  actual rate limit surfaces loud enough (stderr volume increases, exit code
  changes) to trigger primary classification on the next attempt. Net: one
  extra failed retry cycle per false negative.

Even a 10x false-negative rate is cheaper than a 1x false-positive rate. The
classifier should still minimize both, but the asymmetry justifies the
"require confirmation" stance.

### Backward Compatibility

- `rate_limited` field remains on ExecutionResult (not removed); deprecated
  for direct consumer use.
- `rate_limit_primary: bool | None = None` — older backends that do not set
  it continue to produce valid results; consumers detect `None` and fall back
  to `rate_limited`.
- `rate_limit_exit_codes` is optional in instrument profiles (no existing
  profiles break), gated on the Pydantic schema bump landing in the same commit.

## File Change Map

| File | Change |
|------|--------|
| `backends/base.py` | Add `rate_limit_primary: bool \| None = None` to ExecutionResult; deprecate `rate_limited` for direct consumer use; document `rate_limit_wait_seconds` invariant |
| `execution/instruments/cli_backend.py` | Remove `is_success` from `_check_rate_limit`, add primary classification in `_parse_result` using canonical `ErrorType` enum, set `rate_limit_wait_seconds` only when primary |
| `instruments/profile.py` (or equivalent Pydantic schema) | Add optional `rate_limit_exit_codes: list[int] \| None = None` to the profile errors schema |
| `instruments/builtins/*.yaml` | Add `rate_limit_exit_codes` where known (claude-code: [2]) |
| `daemon/baton/events.py` | Add `rate_limit_primary: bool \| None = None` to SheetAttemptResult |
| `daemon/baton/core.py` | Consume primary with fallback to `rate_limited` when `None`; gate `RateLimitHit` emission on the derived primary signal |
| `daemon/baton/musician.py` | Pass through `rate_limit_primary` from ExecutionResult |
| `daemon/baton/state.py` | InstrumentState circuit breaker uses primary only |
| `learning/store/schema.py` (or SQL migration) | Add nullable `rate_limit_primary INTEGER NULL` to relevant execution records; null = pre-B6 |
| `learning/store/` aggregators/weighters | Downweight pre-B6 records (null primary) in pattern detection |
| `docs/guides/instrument-authoring.md` (NEW or updated) | Short section on when to populate `rate_limit_exit_codes` and how to choose values |

## Test Strategy

- **Unit:** `rate_limited=True, success=True` produces `rate_limit_primary=False`
- **Unit:** `rate_limited=True, success=False, error_type=RATE_LIMITED` produces `rate_limit_primary=True`
- **Unit:** `rate_limited=True, success=False, exit_code=2, rate_limit_exit_codes=[2]` produces `rate_limit_primary=True`
- **Unit (accepted false-negative, documented):**
  `rate_limited=True, success=False, error_type=None, exit_code=1, no rate_limit_exit_codes` produces `rate_limit_primary=False`.
  Mark as documented trade-off with a comment pointing to the Cost Framing
  section; the test asserts the classification and ensures future readers do
  not mistake the behavior for a bug. (Standard unit test, not xfail — this is
  the intended outcome, not an expected failure.)
- **Unit:** `rate_limited=True, success=False, error_type=AUTH_FAILURE` produces `rate_limit_primary=False`
- **Unit:** `rate_limited=False` always produces `rate_limit_primary=False`
- **Unit:** `_check_rate_limit` scans stderr regardless of exit code (is_success guard removed)
- **Unit:** `rate_limit_wait_seconds` is None when `rate_limit_primary` is False
- **Unit:** older-backend simulation — ExecutionResult with `rate_limit_primary=None` flows through baton; primary derivation falls back to `rate_limited`
- **Unit:** instrument profile schema accepts `rate_limit_exit_codes: [2]` and rejects invalid types
- **Integration:** gemini-cli with internal 429 retries succeeds — sheet completes, not WAITING
- **Integration:** gemini-cli with sandbox error + 429 in stderr — error classified correctly, not masked as rate limit
- **Integration:** opencode NDJSON parse error + startup 429 — classified as unknown error, not rate limit
- **Integration:** circuit breaker not tripped by incidental rate limits (`rate_limit_primary=False`)
- **Integration:** learning store record written post-B6 has populated primary; pre-B6 record has null primary and is downweighted in pattern queries
- **Adversarial:** rapid rate limit + success oscillation — no state corruption, primary/not-primary stays coherent
- **Compound (with Process Lifecycle spec):** rate limit mid-sheet, `rate_limit_primary=False`, monitor pressure spikes from retry, monitor pause, auto-resume, sheet completes cleanly without leaked processes
- **Pre-ship verification:** classifier run over 30 days of historical learning store records produces a superset-correct relationship with B1+B5 ground truth (no "primary" case that B1+B5 would have said "not a rate limit")

## Coordination with Process Lifecycle Spec

Both specs modify `daemon/baton/events.py`, `daemon/baton/musician.py`, and
`daemon/baton/core.py`. Landing order (see the Process Lifecycle spec for the
same sequence, repeated here for convenience):

1. **Process Lifecycle Phase 1** — unblocks runner-removal; touches `core.py`
   (deregister path). No new events.
2. **Rate Limit Primary B6** (this spec) — adds `rate_limit_primary` to
   `SheetAttemptResult` (in `events.py`) and baton attempt handler (in `core.py`).
3. **Process Lifecycle Phase 2** — adds `MonitorPauseJob` / `MonitorResumeJob`
   events (in `events.py`) and handlers (in `core.py`).
4. **Process Lifecycle Phases 3-4** — schema and restart recovery.

If timing forces a different order, the merge must re-verify:
- Rate-limit and cost auto-resume paths in `core.py` gate on `monitor_paused`
  (Process Lifecycle Phase 2 requirement).
- `SheetAttemptResult` in `events.py` carries `rate_limit_primary` (this spec's
  requirement).

## Future Work

- **Streaming backends.** No streaming CLI backend exists today, but if one
  lands, `_parse_result` is post-hoc and needs rethinking — a streaming backend
  may need intermediate classification events rather than a single
  `ExecutionResult`.
- **Per-consumer causation policy.** The current design centralizes
  classification at parse time. If a future consumer (e.g., a different
  learning-store aggregator or a composer-facing retry strategy) needs to
  apply its own rules, the backend can expose raw observations
  (`exit_code`, `rate_limit_regex_hits`, `error_type`) alongside the default
  verdict. Out of scope until a second consumer needs different rules.
- **Full renaming of `rate_limited` to `rate_limit_detected`.** Would clarify
  intent at the type level but touches every test and every consumer that
  reads the flag directly. Deferred until a major version bump or a
  test-infrastructure-wide refactor offers cover.
- **OS-level isolation.** Shared with the Process Lifecycle spec's Future Work
  section — cgroups v2 / systemd scopes would provide belt-and-suspenders
  isolation that makes both specs less load-bearing.
