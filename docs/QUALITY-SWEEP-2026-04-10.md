# Quality Sweep Report — April 10, 2026

**Method:** Immune Cascade — 5 orthogonal triage sweeps, then convergence analysis on 2+ agent signals.
**Scope:** `src/marianne/` (6,430 lines in core modules, 373 test files, 230K lines of tests)
**Agents:** 5 Gemini subagents (API consistency, dead code, error handling, config drift, test quality)

---

## Convergence Analysis — Findings Flagged by 2+ Agents

These are the highest-signal findings. Multiple independent agents identified the same issues from different angles.

### 🔴 CONVERGENCE 1: Silent Error Suppression (3 agents)

| Agent | Finding |
|-------|---------|
| Sweep 1 | 26 `RuntimeError` overuses — should use domain exceptions |
| Sweep 2 | 4 bare `except Exception: pass` blocks in critical paths |
| Sweep 3 | 2 Critical, 4 High, 6 Medium silent suppression bugs |

**High-impact targets:**

| FILE:LINE | Issue | Fix |
|-----------|-------|-----|
| `daemon/manager.py:2979` | `create_subprocess_shell` with unvalidated YAML commands | Validate/sandbox hook commands |
| `daemon/manager.py:1244` | Silent checkpoint load failure → stale status | Log warning + return explicit error |
| `daemon/manager.py:1817` | `CancelledError` swallowed → daemon hang on shutdown | Re-raise `CancelledError` |
| `daemon/profiler/collector.py:561` | Memory probe failure returns 0.0 silently | Log + return `None` / "unavailable" |
| `cli/commands/doctor.py:62` | All errors treated as "not running" | Distinguish connection-refused from malformed-response |
| `daemon/event_bus.py:170,185` | Failing subscriber silently drops events for all | Log error + isolate failing subscriber |
| `daemon/manager.py` (24 blocks) | Pervasive `except Exception: pass` in cleanup | Add `logger.debug()` to each |

### 🔴 CONVERGENCE 2: Dead Code in Baton/Execution Layer (2 agents)

| Agent | Finding |
|-------|---------|
| Sweep 2 | 9 dead functions in `daemon/baton/` — 239 test refs, **zero production callers** |
| Sweep 5 | 20 stale Phase 2 skipped tests testing removed sync layer |

**Dead baton functions (all test-only, zero production callers):**

| Function | File | Test Refs |
|----------|------|-----------|
| `baton_to_checkpoint_status` | `adapter.py:95` | 75 |
| `checkpoint_to_baton_status` | `adapter.py:104` | 37 |
| `is_job_paused` | `core.py:803` | 74 |
| `register_sheet` | `state.py:313` | 33 |
| `publish_attempt_result` | `adapter.py:1433` | 3 |
| `publish_sheet_skipped` | `adapter.py:1457` | 1 |
| `get_diagnostics` | `core.py:1804` | 7 |
| `at_capacity` | `state.py:226` | 5 |
| `terminal_count` | `state.py:335` | 4 |

**Also dead (zero refs anywhere — src + tests):**
- `daemon/config.py:214` — `_validate_analyze_on`
- `daemon/config.py:349` — `_warn_reserved_fields`
- `daemon/monitor.py:126` — `seconds_since_last_check`
- `daemon/profiler/storage.py:466` — `read_process_history`
- `daemon/profiler/strace_manager.py:261` — `get_strace_pids`

### 🟠 CONVERGENCE 3: Configuration/Schema Drift (2 agents)

| Agent | Finding |
|-------|---------|
| Sweep 1 | 2 camelCase params (`backupCount`, `maxBytes`) in logging |
| Sweep 4 | `"sheet_num"` duplicated 124×, `"validation_pass_rate"` 10×, `100.0` threshold in 6 locations |

**Recommended refactors:**

1. **Create `marianne.core.constants`** module:
   - `KEY_SHEET_NUM = "sheet_num"` (used 124×)
   - `KEY_JOB_ID = "job_id"` (used 54×)
   - `KEY_WORKSPACE = "workspace"` (used 20×)
   - `KEY_VALIDATION_PASS_RATE = "validation_pass_rate"` (used 10×)
   - `VALIDATION_FULL_PASS = 100.0` (used 6×)
   - `TRUNCATION_*` constants for escalation.py

2. **Timeout tier constants:**
   - `TIMEOUT_FAST = 10.0`, `TIMEOUT_STANDARD = 30.0`, `TIMEOUT_EXTENDED = 300.0`, `TIMEOUT_LONG_RUNNING = 1800.0`

3. **Magic threshold names:**
   - `MIN_CASCADE_HISTORY = 3`, `MAX_UNIQUE_CODES = 4` (retry_strategy.py)
   - `MIN_CORRELATION_GROUP = 3` (correlation.py)

### 🟡 CONVERGENCE 4: Long Functions (2 agents)

| Agent | Finding |
|-------|---------|
| Sweep 1 | 14 functions over 200 lines (418 over 50) |
| Sweep 5 | `test_daemon_output.py` tests logging calls, not behavior |

**Top 5 decomposition targets:**

| Function | File | Lines | Recommendation |
|----------|------|-------|----------------|
| `classify_execution()` | `errors/classifier.py:967` | 292 | Extract error-class-specific handlers |
| `_execute_sheet_with_recovery()` | `runner/sheet.py:1493` | 288 | Extract retry/state-machine branches |
| `__init__()` | `runner/base.py:114` | 286 | Builder pattern or config-object ctor |
| `_register_methods()` | `daemon/process.py:547` | 270 | Auto-register via decorators |
| `execute()` | `backends/anthropic_api.py:172` | 267 | Extract stream-processing loop |

---

## Single-Agent Findings (High Severity Only)

### Sweep 3: Critical Security Issues

| FILE:LINE | Issue | Severity |
|-----------|-------|----------|
| `daemon/manager.py:2979` | `create_subprocess_shell` with unvalidated YAML hook commands — shell injection vector | **Critical** |
| `execution/grounding.py:193` | Unvalidated path in `open(Path(file_path))` — path traversal vector | **Critical** |
| `execution/instruments/cli_backend.py:290` | `os.environ[key]` crashes on missing env vars — should use `.get()` with validation | **Medium** |

### Sweep 5: Test Health

| Finding | Count | Severity |
|---------|-------|----------|
| Stale Phase 2 skipped tests | 20 tests | **High** |
| Untested public methods | 2 methods (`set_model_concurrency`, `update_job_config_metadata`) | **High** |
| Mock-only assertions in `test_daemon_output.py` | 11 patches, 24 mock-asserts vs 11 real | **Medium** |
| Fragile exact error message assertions | ~35 instances | **Medium** |

### Sweep 4: Circular Import Risk

| Cycle | Files | Status |
|-------|-------|--------|
| `execution.hooks` ↔ `daemon.job_service` ↔ `execution.runner` | 3 modules | Mitigated with lazy imports — **fragile** |

---

## Clean Areas ✅

| Check | Result |
|-------|--------|
| Bare `except:` | Zero found |
| Unsafe `yaml.load()` | Zero found — all use `yaml.safe_load()` |
| Unused imports | Zero found — ruff F401 enforced |
| Public functions missing type hints | Zero found |
| Public classes missing docstrings | Zero found |
| Bare `raise Exception(` | Zero found |
| `pickle.loads` | Zero found |

---

## Priority Action Items

### P0 — Security (Immediate)
1. ⬜ Add command validation/sandboxing to `manager.py:2979` hook execution
2. ⬜ Add path traversal validation to `grounding.py:193` file checksum hook
3. ⬜ Add env var presence validation to `cli_backend.py:290`

### P1 — Reliability (This Week)
4. ⬜ Fix `CancelledError` swallowing in `manager.py:1817`
5. ⬜ Add `logger.debug()` to all 24 silent `except Exception: pass` blocks in manager
6. ⬜ Fix `doctor.py` to distinguish connection states
7. ⬜ Add logging to event bus subscriber failures
8. ⬜ Add logging to profiler silent suppressions (collector, storage, gpu_probe)

### P2 — Technical Debt (This Month)
9. ⬜ Delete 20 stale Phase 2 skipped tests
10. ⬜ Remove 5 completely dead functions (zero refs)
11. ⬜ Mark or wire 9 baton dead functions (test-only refs)
12. ⬜ Create `marianne.core.constants` module
13. ⬜ Decompose `classify_execution()` (292 lines)

### P3 — Hygiene (Backlog)
14. ⬜ Replace 26 `RuntimeError` with domain exceptions
15. ⬜ Refactor 36 functions in 150–199 line range
16. ⬜ Fix fragile test assertions (~35 instances)
17. ⬜ Rewrite `test_daemon_output.py` to test behavior, not logging calls
18. ⬜ Extract timeout tier constants
19. ⬜ Name magic thresholds in retry_strategy and correlation modules
