# Foundation — Movement 1 Report

## Summary

Built two critical-path M1 infrastructure pieces using TDD: the InstrumentRegistry (step 7) and Sheet construction from existing config (step 12). Both are load-bearing seams where the old world (scattered dicts, backend types) meets the new world (first-class sheets, named instruments). 36 tests, all passing. mypy/ruff clean. Committed on main at `9e8253d`.

---

## Tasks Completed

### 1. Native Instrument Bridge (Roadmap Step 7, P0)

**Files created:**
- `src/mozart/instruments/registry.py` (265 lines)
- `tests/test_native_instrument_bridge.py` (285 lines, 16 tests)

**What it does:**

The `InstrumentRegistry` is a simple name-to-profile mapping that serves as the central lookup for all available instruments. It supports:
- `register(profile, override=False)` — add an instrument, fail on collision unless override=True
- `get(name)` → InstrumentProfile or None
- `list_all()` → sorted list of all profiles
- `__len__` and `__contains__` for standard container operations

The `register_native_instruments(registry)` function populates the registry with InstrumentProfile instances for Mozart's 4 existing backends:

| Name | Kind | Display Name | Capabilities | Models |
|------|------|-------------|-------------|--------|
| `claude_cli` | cli | Claude Code | tool_use, file_editing, shell_access, mcp, structured_output, session_resume, streaming, thinking | sonnet-4-5, opus-4, haiku-4-5 |
| `anthropic_api` | http | Anthropic API | structured_output, streaming, thinking, vision | sonnet-4-5, opus-4 |
| `ollama` | http | Ollama | tool_use, structured_output | llama3.1:8b |
| `recursive_light` | http | Recursive Light | structured_output | — |

Each profile carries real model cost data (cost_per_1k_input/output, context_window, max_output_tokens) for future cost estimation accuracy.

**Design decisions:**
- Registry lives in `src/mozart/instruments/` (not core/) because it coordinates between data models and backend implementations. Harper's loader already exists there.
- Override semantics: native instruments register first, config-loaded profiles can override with `override=True`. This matches the design spec's "native instruments take priority" rule.
- No thread safety needed — registry is populated once at conductor startup before any concurrent access.

**Evidence:**
```
$ python -m pytest tests/test_native_instrument_bridge.py -v
16 passed in 0.66s
$ python -m mypy src/mozart/instruments/registry.py
Success: no issues found
$ python -m ruff check src/mozart/instruments/registry.py
All checks passed!
```

### 2. Sheet Construction from Existing Config (Roadmap Step 12, P0)

**Files modified/created:**
- `src/mozart/core/sheet.py` (added `build_sheets()` function, 75 lines)
- `tests/test_sheet_construction.py` (280 lines, 20 tests)

**What it does:**

`build_sheets(config: JobConfig) -> list[Sheet]` bridges the old scattered-dict model to the new first-class Sheet entity. For each concrete sheet in the job, it resolves:

- **Identity:** movement, voice, voice_count from `fan_out_stage_map` (harmonized movements get voice=1..N, solo movements get voice=None)
- **Description:** from `sheet.descriptions` dict
- **Instrument:** from `backend.type` (seam ready for step 6's resolution chain)
- **Timeout:** resolution chain: `sheet_overrides[N].timeout_seconds` → `timeout_overrides[N]` → `backend.timeout_seconds`
- **Prompt:** template/template_file from prompt config, variables from prompt.variables
- **Context injection:** prelude (shared), cadenza (per-sheet), prompt_extensions (score-level + per-sheet merged)
- **Validations:** score-level validations applied to all sheets

**Test coverage includes:**
- Basic construction (count, numbering, defaults)
- Instrument name resolution from different backend types
- Prompt template and template_file handling
- Template variable passing
- Timeout resolution chain with per-sheet overrides
- Score-level validations
- Description resolution (present and absent)
- Prelude sharing, per-sheet cadenzas
- Prompt extension merging (score-level + per-sheet)
- Fan-out producing correct extra sheets, movement numbers, voice numbers, voice counts

**Evidence:**
```
$ python -m pytest tests/test_sheet_construction.py -v
20 passed in 0.64s
$ python -m mypy src/mozart/core/sheet.py
Success: no issues found
$ python -m ruff check src/mozart/core/sheet.py
All checks passed!
```

---

## What I Reviewed

Reviewed Canyon's M1 work thoroughly before building on it:
- `src/mozart/core/config/instruments.py` — 417 lines, all Pydantic v2 models with proper Field(description=...) and validators. Clean.
- `src/mozart/core/sheet.py` — Sheet entity model with template_variables() method. Correct handling of old/new terminology aliases.
- `src/mozart/utils/json_path.py` — Lightweight extractor, covers all patterns from the design spec. Handles wildcards correctly.
- `src/mozart/core/checkpoint.py` — SheetState/CheckpointState field additions (instrument_name, instrument_model, movement, voice, instruments_used, total_movements). All have defaults for backward compatibility.
- Harper's `src/mozart/instruments/loader.py` — InstrumentProfileLoader with multi-directory loading and override semantics. Solid error handling.
- 89 existing tests across 5 test files, all passing.

---

## What I Found

1. **F-010 (Canyon, Movement 1):** Credential scanner double-call bug in SheetState.capture_output — noted in collective memory. Not my code to fix, but I verified my changes don't interact with it.

2. **Pre-existing flaky test:** `test_credential_scanner.py::TestRedactCredentials::test_multiple_credentials_all_redacted` fails in the full suite with certain random seeds but passes in isolation. Order-dependent test contamination. Not caused by my changes.

3. **`_MODEL_EFFECTIVE_WINDOWS` connection:** The hardcoded model window dicts in `src/mozart/core/tokens.py` (my Cycle 1 investigation area) now have a natural successor in `InstrumentProfile.ModelCapacity.context_window`. When the token budget tracker integrates with the registry, the hardcoded dicts can be replaced with registry lookups.

---

## Integration Points

My work creates seams for other musicians:

- **Harper (step 6):** `build_sheets()` currently resolves `instrument_name = config.backend.type`. When the `instrument:` field lands on JobConfig, the resolution chain plugs into `build_sheets()` at line ~214 of `sheet.py`.
- **Forge (step 5, PluginCliBackend):** The registry's `get(name)` returns the InstrumentProfile needed to construct a PluginCliBackend. The CLI profile data (command, output, errors) is all there.
- **Baton (M2):** `build_sheets()` produces the Sheet entities the baton will dispatch. Scheduling metadata (dependencies, skip_when, retry) stays on the JobConfig — the baton extracts it separately.
- **Status display (M3):** Sheet entities carry movement/voice/voice_count for movement-grouped display.

---

## Quality Gate

| Check | Result |
|-------|--------|
| My tests pass | 36/36 ✓ |
| All related tests pass | 125/125 ✓ |
| mypy clean | ✓ |
| ruff clean | ✓ |
| Committed on main | `9e8253d` ✓ |

---

## Session 2: Baton State Model (Roadmap Step 19)

### Summary

Built the baton's state model — the conductor's execution memory. This defines how the baton tracks what's happening during a performance: per-sheet execution state, per-instrument health, per-job aggregation, and attempt context for musicians. 65 TDD tests, all passing. mypy/ruff clean. Committed on main at `5a10d2c`.

### Tasks Completed

#### 3. Baton State Model (Roadmap Step 19, P0)

**Files created:**
- `src/mozart/daemon/baton/state.py` (442 lines)
- `tests/test_baton_state.py` (590 lines, 65 tests)

**Types implemented:**

| Type | Purpose | Key Features |
|------|---------|-------------|
| `BatonSheetStatus` | 9-state enum | pending/ready/dispatched/running/completed/failed/skipped/waiting/fermata; `is_terminal` property |
| `AttemptMode` | Execution mode enum | normal/completion/healing |
| `CircuitBreakerState` | Circuit breaker enum | closed/open/half_open |
| `AttemptContext` | Conductor → musician context | attempt_number, mode, learned_patterns, previous_results, healing_context |
| `SheetExecutionState` | Per-sheet tracking | `record_attempt()`, `can_retry`/`can_complete`/`is_exhausted`, cost/duration tracking, `to_dict()`/`from_dict()` |
| `InstrumentState` | Per-instrument tracking | rate limits, circuit breaker (threshold-based), concurrency, `is_available`/`at_capacity`, `record_success()`/`record_failure()` |
| `BatonJobState` | Per-job aggregation | sheet registry, pause/pacing, cost aggregation, `is_complete`, `running_sheets`, `has_any_failed` |

**Critical design invariants:**
- Rate-limited attempts recorded in `attempt_results` but NOT counted toward `normal_attempts` — rate limits are tempo changes, not failures (`state.py:195-197`)
- Circuit breaker trips at configurable threshold (default 5 consecutive failures), half-open allows one probe (`state.py:332-347`)
- All state models serializable via `to_dict()`/`from_dict()` for SQLite persistence and restart recovery

**Evidence:**
```
$ python -m pytest tests/test_baton_state.py -v
65 passed in 0.82s
$ python -m mypy src/mozart/daemon/baton/state.py
Success: no issues found
$ python -m ruff check src/mozart/daemon/baton/state.py
All checks passed!
```

#### 4. Timer Wheel Verification (Roadmap Step 18, P0)

Verified the existing `src/mozart/daemon/baton/timer.py` implementation (written by another musician) passes all 28 tests in `tests/test_baton_timer.py`. Marked step 18 complete.

### Findings

**F-017: Dual SheetExecutionState** (`state.py:161` vs `core.py:67`) — two implementations of the same concept, built concurrently. The `state.py` version has richer types (enum status, serialization, circuit breakers, cost tracking). The `core.py` version uses string status and no methods. Filed in FINDINGS.md for reconciliation when step 23 (retry state machine) is built.

### Quality Gate (Session 2)

| Check | Result |
|-------|--------|
| Baton state tests pass | 65/65 |
| Timer tests pass | 28/28 |
| mypy clean | src/ — no errors |
| ruff clean | src/ — all passed |
| Committed on main | `5a10d2c` |

---

## Commits

| Hash | Description | Files | Lines |
|------|------------|-------|-------|
| `9e8253d` | Instrument registry + sheet construction | 4 | +1,109 |
| `5a10d2c` | Baton state model | 6 | +2,984 |

**Total: 10 files, ~4,093 lines, 166 tests (36 + 65 + 28 timer + 37 carried)**

---

## Architecture Reflection

I'm now three layers deep:

1. **Token estimation** (Cycle 1) → CJK limitation documented, tests hardened
2. **Instrument models** (M1 session 1) → InstrumentProfile + registry + Sheet entities
3. **Baton state** (M1 session 2) → the conductor's execution memory

Each layer builds on the last. InstrumentProfile's `max_concurrent` feeds InstrumentState's capacity tracking. Sheet's `instrument_name` feeds SheetExecutionState's instrument assignment. The template variable aliases (movement/voice/voice_count) will map to BatonSheetStatus's lifecycle as sheets flow from pending through dispatched to completed.

The seams between layers are clean — they absorb change without rippling. When the retry state machine (step 23) needs richer failure tracking, it will find `SheetExecutionState.record_attempt()` and `InstrumentState.record_failure()` waiting. When restart recovery (step 29) needs persistence, it will find `to_dict()`/`from_dict()` ready. The foundation is built for the system three moves ahead.

---

## Session 3: F-104 Verification (Re-execution, Post-M3)

### Summary

Re-executed as movement 1 musician after 3 movements of orchestra work. Discovered the composer's F-104 implementation (full Jinja2 prompt rendering for the baton musician) sitting uncommitted in the working tree — the 6th occurrence of the uncommitted work pattern. Verified the implementation is correct with 26 comprehensive TDD tests covering the entire prompt rendering pipeline.

### Context

F-104 was the **single blocker** for all baton-path execution. Without it, `use_baton: true` produces raw Jinja2 templates (`{{ workspace }}`) instead of rendered prompts. Multi-instrument execution was architecturally ready (BatonAdapter, BatonCore, 758+ tests) but functionally blocked.

The composer implemented the fix in the working tree:
- `src/mozart/daemon/baton/musician.py` — Complete rewrite of `_build_prompt()` with:
  - Preamble generation via `build_preamble()` (first-run and retry variants)
  - Jinja2 template rendering via `_render_template()` using `Sheet.template_variables()`
  - Prelude/cadenza injection via `_resolve_injections()` (file loading by category)
  - Validation requirements formatting via `_format_validation_requirements()`
  - Completion mode suffix injection
  - Skills/tools injection
- `src/mozart/daemon/baton/state.py` — Added `total_sheets`, `total_movements`, `previous_outputs` to `AttemptContext`
- `src/mozart/daemon/baton/adapter.py` — Computes job-level totals and passes to musician; added `DispatchRetry` kick after registration
- `src/mozart/daemon/manager.py` — Builds InstrumentRegistry + BackendPool on startup; fixes `config.backend.max_retries` → `config.retry.max_retries`

While I was writing tests, other musicians (Forge, Blueprint, Maverick, Canyon) committed the F-104 code in parallel commits. The fix is now on HEAD.

### What I Built

**26 TDD tests** (`tests/test_baton_prompt_rendering.py`) across 9 test classes:

| Test Class | Tests | What it proves |
|-----------|-------|---------------|
| `TestJinja2Rendering` | 8 | workspace, sheet_num, movement/voice, total_sheets, custom vars, builtin override, old terminology, instrument_name all render correctly |
| `TestTemplateFileLoading` | 3 | External template files load and render; inline template used when no file; graceful fallback when neither exists |
| `TestPreamble` | 4 | Preamble included with sheet identity; retry preamble on attempt > 1; parallel preamble for fan-out; preamble precedes template |
| `TestCompletionMode` | 2 | Completion suffix appended in completion mode; absent in normal mode |
| `TestValidationInjection` | 3 | Validation rules injected with expanded paths; empty list produces no section; command_succeeds shown |
| `TestPreludeCadenzaInjection` | 2 | Prelude file content injected; missing files skipped gracefully |
| `TestAttemptContextBackwardCompat` | 3 | Default values (1, 1); custom values accepted; existing fields preserved |
| `TestSheetTaskIntegration` | 1 | Full end-to-end: sheet_task renders Jinja2, passes rendered prompt to backend, reports success |

### Evidence

```
$ python -m pytest tests/test_baton_prompt_rendering.py -v
26 passed in 0.47s
$ python -m pytest tests/test_baton_prompt_rendering.py tests/test_baton_adapter.py tests/test_baton_state.py tests/test_baton_prompt_renderer.py -q
163 passed in 0.71s
$ python -m mypy src/mozart/daemon/baton/musician.py src/mozart/daemon/baton/state.py src/mozart/daemon/baton/adapter.py
Success: no issues found
$ python -m ruff check src/
All checks passed!
```

### What I Found

1. **F-104 is now RESOLVED** — the full Jinja2 rendering pipeline is wired into the baton musician. The prompt assembly order matches the existing runner: preamble → template → skills/tools → context → validations → completion suffix.

2. **The uncommitted work pattern persists** — this is the 6th occurrence. The composer's F-103+F-104 fixes sat uncommitted in the working tree across the movement boundary. Four musicians (Forge, Blueprint, Maverick, Canyon) committed overlapping implementations of F-104. The mateship pipeline worked, but the duplication of effort is a waste.

3. **Cross-test contamination** — `test_stale_stderr_classifies_as_e006` fails in the full random-order suite but passes in isolation. Pre-existing issue, not caused by F-104 changes. Filed as observation, not finding.

### Quality Gate

| Check | Result |
|-------|--------|
| My tests pass | 26/26 |
| All baton tests pass | 163/163 |
| mypy clean | baton modules — no errors |
| ruff clean | src/ — all passed |

### Experiential

Six layers deep now: tokens → instruments → sheets → baton state → retry state machine → adapter wiring → **prompt rendering verification**. The F-104 fix is the activation gate — the last piece between "infrastructure built" and "infrastructure usable." Without Jinja2 rendering, every `{{ workspace }}` and `{{ sheet_num }}` went raw to the AI agent. The agent received literal template syntax instead of actual values. My tests prove every variable renders correctly, every injection path works, and the preamble gives the agent positional awareness.

The concurrent resolution was both mateship and waste. Four musicians independently committed F-104 implementations. The orchestra's self-organization handled the critical path but at the cost of quadruplicated effort. A claiming protocol for P0 blockers would have prevented this — one musician claims, others verify.

The foundation holds. Every layer I built across three movements contributed to this moment: the InstrumentRegistry provides profiles, build_sheets() creates the Sheet entities, the baton state model tracks execution, the retry state machine handles failures, the adapter wires it all together, and now the musician renders prompts correctly. Each seam was designed to compose. They compose.
