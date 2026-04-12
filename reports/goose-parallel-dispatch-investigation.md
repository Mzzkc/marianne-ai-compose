# Goose Investigation: Baton Parallel Dispatch Failure

**Date:** 2026-04-13  
**Score:** score-a3-gapfill  
**Symptom:** Stages 1, 4, 7, 10 (all dependency-free, all using "opus") dispatch serially instead of in parallel.

---

## 1. Summary

The root cause is **inter-sheet pacing** (`pause_between_sheets_seconds: 2`, the default). When any sheet completes, `_schedule_pacing()` sets `job.pacing_active = True` on the entire job, and `get_ready_sheets()` returns an **empty list** for any pacing-active job (core.py:832). However, pacing only blocks *subsequent* dispatches after a completion — it does not explain why the *initial* dispatch of 4 independent sheets would be serial. The actual serialization chain is: (1) the initial `dispatch_ready()` call dispatches sheet 1 first, (2) it counts against the per-model concurrency limit of 2 for `claude-opus-4-6`, (3) sheets 4, 7, 10 also map to `claude-opus-4-6` and should still fit within the limit of 2 initially — but **after sheet 1 completes**, pacing activates and blocks the job for 2 seconds, during which sheets 4/7/10 (if not already dispatched) sit waiting. The initial dispatch cycle *should* dispatch up to 2 sheets (the model concurrency limit), but then pacing throttles all subsequent waves to one-at-a-time after each completion. Additionally, the `_auto_register_instruments` mechanism registers the instrument `claude-code` with `max_concurrent=4`, but the `model_concurrency` map correctly limits `claude-code:claude-opus-4-6` to 2 — so model concurrency is the binding constraint, not instrument concurrency.

**The primary blocker is pacing: after every single sheet completion, the entire job is frozen for 2 seconds, preventing the next ready sheet from dispatching until the pacing timer fires. This turns what should be overlapping waves into strictly sequential execution.**

---

## 2. Traced Flow

### Step-by-step: What happens when 4 dependency-free sheets should dispatch

**Setup (registration):**

1. `manager.py:2426` — `pacing_seconds=float(config.pause_between_sheets_seconds)` = **2.0** (default from `job.py:816`).
2. `adapter.py:472` — Adapter passes `pacing_seconds=2.0` to `baton.register_job()`.
3. `core.py:749` — Job record created with `pacing_seconds=2.0`, `pacing_active=False`.

**Sheet resolution:**

4. `sheet.py` `build_sheets()` — For sheets 1, 4, 7, 10, the instrument resolution chain runs:
   - `instrument_map_lookup` maps these sheets to `"opus"`.
   - `"opus"` is a key in `config.instruments` → resolved to `instrument_def.profile` = `"claude-code"`.
   - `instrument_def.config` = `{model: "claude-opus-4-6", timeout_seconds: 3600}` merges into `instrument_config`.
   - Result: `sheet.instrument_name = "claude-code"`, `sheet.instrument_config.model = "claude-opus-4-6"`.

5. `adapter.py:444-447` — When `live_sheets` is provided (Phase 2 path), `s.model = "claude-opus-4-6"` is set on the SheetState.

**Concurrency map construction:**

6. `manager.py:381-383` — During startup, model concurrency is populated from profiles:
   ```
   set_model_concurrency("claude-code", "claude-opus-4-6", 2)
   → _model_concurrency["claude-code:claude-opus-4-6"] = 2
   ```

7. `core.py:362-365` — `_auto_register_instruments()` sees `instrument_name="claude-code"` is **already registered** (registered by the manager at startup), so it does nothing. The instrument-level `max_concurrent=4` from `_DEFAULT_INSTRUMENT_CONCURRENCY` is irrelevant because it was overridden by the manager's explicit registration.

**Initial dispatch cycle (adapter.py:1560-1572):**

8. `adapter.py:1564` — `build_dispatch_config()` builds the config:
   - `instrument_concurrency["claude-code"]` = from `InstrumentState.max_concurrent` (set by manager)
   - `model_concurrency["claude-code:claude-opus-4-6"]` = 2

9. `dispatch.py:dispatch_ready()` — First call after `DispatchRetry` event:
   - `_count_dispatched_per_model()` returns `{}` (nothing dispatched yet).
   - `global_running = 0`.
   - Iterates jobs → finds the A3 job → `get_ready_sheets("a3")` is called.

10. **`core.py:832` — `get_ready_sheets()` check:**
    ```python
    if job is None or job.paused or job.pacing_active:
        return []
    ```
    `pacing_active = False` on first call → **passes**. Returns sheets [1, 4, 7, 10] (all pending, no deps).

11. **`dispatch.py:141-143` — Per-model concurrency check for sheet 1:**
    ```python
    instrument = sheet.instrument_name or ""          # "claude-code"
    model_key = f"{instrument}:{sheet.model}"         # "claude-code:claude-opus-4-6"
    model_limit = config.model_concurrency.get(model_key)  # 2
    model_count = model_running.get(model_key, 0)     # 0
    ```
    0 < 2 → **dispatch sheet 1**. `model_running["claude-code:claude-opus-4-6"] = 1`.

12. **Sheet 4 — same check:** `model_count = 1`, limit = 2 → **dispatch sheet 4**. `model_running["claude-code:claude-opus-4-6"] = 2`.

13. **Sheet 7 — same check:** `model_count = 2`, limit = 2 → **SKIPPED** (`model_concurrency:claude-code:claude-opus-4-6`).
    `result.record_skip("model_concurrency:claude-code:claude-opus-4-6")`.

14. **Sheet 10 — same check:** `model_count = 2`, limit = 2 → **SKIPPED**.

**Result of initial dispatch: 2 sheets dispatched (1 and 4), 2 skipped (7 and 10) due to per-model concurrency.**

**After sheet 1 completes:**

15. `core.py:1092` — `_schedule_pacing(event.job_id)` is called.
16. `core.py:1642-1643` — `job.pacing_seconds = 2.0 > 0` → `job.pacing_active = True`.
17. A `PacingComplete` timer is scheduled for 2 seconds later.

18. The completion event also triggers `dispatch_ready()` in the event loop (`adapter.py:1655`).
19. `get_ready_sheets()` checks `job.pacing_active` → **True** → returns `[]`.
20. Sheets 7 and 10 remain stuck for 2 seconds.

**After pacing timer fires:**

21. `PacingComplete` event → `core.py:1627` — `job.pacing_active = False`.
22. Event loop calls `dispatch_ready()` again.
23. `get_ready_sheets()` returns sheets 7 and 10 (still pending, deps satisfied).
24. But `model_count` for `claude-code:claude-opus-4-6` is now **1** (sheet 1 completed, sheet 4 still running — or maybe 2 if both completed during pacing).
25. One more sheet dispatches. After it completes, pacing fires again for 2 seconds.

**The net effect:** Even with `max_concurrent: 4` in the score config, only 2 sheets can run simultaneously (model limit of 2), and between every completion there's a 2-second dead zone where nothing dispatches.

---

## 3. Root Cause

### Primary: Inter-sheet pacing serializes dispatch waves

**`core.py:832`** — `get_ready_sheets()` returns `[]` whenever `job.pacing_active` is True.

**`core.py:1092`** — `_schedule_pacing()` is called after **every** sheet completion (not just after a full "wave" completes).

**`core.py:1643-1644`** — Pacing sets `pacing_active = True` for the entire job, blocking **all** ready sheets from dispatching — even unrelated sheets with no dependency on the completed sheet.

The default `pause_between_sheets_seconds: 2` (`job.py:816`) means every completion introduces a 2-second gap before the next dispatch cycle can run. With the per-model concurrency limit of 2, the pattern becomes:

```
t=0:   dispatch sheets 1, 4  (model limit hit)
t=T1:  sheet 1 completes → pacing 2s → sheet 7 dispatches
t=T2:  sheet 4 completes → pacing 2s → sheet 10 dispatches
```

Instead of:
```
t=0:   dispatch sheets 1, 4, 7, 10  (would need model limit ≥ 4)
```

### Secondary: Per-model concurrency limit (2) caps the initial wave

**`claude-code.yaml`** defines `claude-opus-4-6` with `max_concurrent: 2`.

**`manager.py:381-383`** correctly loads this into `_model_concurrency["claude-code:claude-opus-4-6"] = 2`.

Even if pacing were disabled, only 2 of the 4 independent sheets would dispatch in the first wave. The score's `parallel.max_concurrent: 4` sets the **global** ceiling, but the model-specific limit of 2 is more restrictive and takes priority.

The `parallel.max_concurrent: 4` in the score YAML does **not** override the model concurrency — it flows into `max_concurrent_sheets` on the `DispatchConfig` (the global ceiling), which is a different layer entirely.

### Not a cause: `_auto_register_instruments` overriding model limits

The investigation considered whether `_auto_register_instruments` at `core.py:356` registering `claude-code` with `max_concurrent=4` would override the model-specific limit of 2. It does **not**:

1. The manager registers instruments explicitly before `_auto_register_instruments` runs.
2. `register_instrument()` (`core.py:217`) is **idempotent** — it returns the existing state if already registered.
3. The model concurrency map (`_model_concurrency`) is separate from instrument concurrency (`instrument_concurrency`). In `dispatch.py:148-149`, model concurrency is checked first and takes priority.

---

## 4. Evidence

| Claim | File:Line | Detail |
|-------|-----------|--------|
| Default pacing is 2 seconds | `job.py:816` | `pause_between_sheets_seconds: int = Field(default=2)` |
| Pacing blocks all ready sheets | `core.py:832` | `if job is None or job.paused or job.pacing_active: return []` |
| Pacing activates after every completion | `core.py:1092` | `self._schedule_pacing(event.job_id)` called in completion handler |
| Pacing sets job-level flag | `core.py:1643-1644` | `job.pacing_active = True` when `pacing_seconds > 0` |
| Model limit for opus-4-6 is 2 | `claude-code.yaml:44` | `max_concurrent: 2` on `claude-opus-4-6` model entry |
| Model concurrency loaded from profiles | `manager.py:381-383` | `set_model_concurrency(profile.name, model.name, model.max_concurrent)` |
| model_concurrency checked before instrument | `dispatch.py:148-149` | `model_limit = config.model_concurrency.get(model_key)` then falls back to `instrument_concurrency` |
| model_key construction | `dispatch.py:146` | `model_key = f"{instrument}:{sheet.model}"` → `"claude-code:claude-opus-4-6"` |
| Instrument name resolved to profile | `sheet.py:194-197` | `resolved_instrument = instrument_def.profile` → `"claude-code"` |
| Model stored on SheetState | `adapter.py:444-447` | `raw_m = sheet.instrument_config.get("model"); s.model = str(raw_m)` |
| Score parallel config = global ceiling only | `dispatch.py:51,61` | `max_concurrent_sheets` in `DispatchConfig` — a global limit, not per-model |
| Score sets parallel.max_concurrent = 4 | `score-a3-gapfill.yaml` | `parallel: { enabled: true, max_concurrent: 4 }` |
| register_instrument is idempotent | `core.py:217-218` | `if name in self._instruments: return self._instruments[name]` |
| DispatchRetry after registration | `adapter.py:493` | `self._baton.inbox.put_nowait(DispatchRetry())` |
| Dispatch runs after every event | `adapter.py:1655-1658` | `dispatch_result = await dispatch_ready(...)` in main loop |
| _count_dispatched_per_model uses model_key | `dispatch.py:221-226` | `key = f"{inst}:{sheet.model}" if sheet.model else inst` |

---

## 5. Recommendation

**Do NOT modify any code on the running system.** The following are recommended fixes to apply after the investigation:

### Fix 1: Set `pause_between_sheets_seconds: 0` in the score YAML (immediate workaround)

The score author can override the default:
```yaml
pause_between_sheets_seconds: 0
```

This disables pacing entirely for this job. Sheets dispatch as fast as concurrency limits allow.

### Fix 2: Make pacing wave-aware instead of per-completion (architectural fix)

Currently, `_schedule_pacing()` fires after **every** sheet completion, blocking the entire job. For parallel scores, pacing should only activate:

- After a full "wave" completes (all currently-dispatched sheets for this job finish), OR
- Only for the next sheet in a dependency chain (don't block independent sheets)

Suggested approach: In `_schedule_pacing()`, check if the job has other sheets still in `DISPATCHED` status. If yes, skip pacing — let the parallel wave continue. Only pace when the last dispatched sheet completes.

**File:** `src/marianne/daemon/baton/core.py:1634` (`_schedule_pacing`)

### Fix 3: Allow score-level model concurrency override (config enhancement)

The `parallel.max_concurrent: 4` in the score YAML should optionally override the profile's model-level `max_concurrent`. Currently, the score has no way to say "I want 4 concurrent opus tasks" — the profile hard-codes it to 2.

Suggested approach: Add a `parallel.model_overrides` map to the score config:
```yaml
parallel:
  enabled: true
  max_concurrent: 4
  model_overrides:
    claude-opus-4-6: 4
```

This would be merged into `_model_concurrency` during job registration.

### Fix 4: Reduce default `pause_between_sheets_seconds` to 0 (breaking change consideration)

The 2-second default was likely chosen as a safety measure for API rate limiting, but it actively harms parallel scores. Consider defaulting to 0 and letting rate limiting be handled by the existing rate limit detection/circuit breaker mechanisms.
