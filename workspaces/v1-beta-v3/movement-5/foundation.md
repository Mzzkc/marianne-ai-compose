# Foundation — Movement 5 Report

## Assignment

D-026: Fix F-271 (PluginCliBackend MCP process explosion) + F-255.2 (baton _live_states never populated). Both P0 blockers on the critical path to Phase 1 baton testing.

## Completed Work

### F-271: PluginCliBackend MCP Process Explosion — RESOLVED

**Problem:** PluginCliBackend (used by the baton via instrument profiles) didn't disable MCP servers. The legacy ClaudeCliBackend has `disable_mcp=True` which adds `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`. The baton's PluginCliBackend had zero MCP handling, causing 80 child processes instead of 8 on production runs.

**Fix:** Profile-driven approach via `CliCommand.mcp_disable_args` list field (added by a parallel musician). `_build_command()` at `cli_backend.py:229-233` injects these args when the list is non-empty. The claude-code profile at `builtins/claude-code.yaml:82-85` specifies `["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']`.

**Files modified:**
- `src/marianne/execution/instruments/cli_backend.py:229-233` — inject mcp_disable_args (note: another musician added the field; I adapted the backend to use it)
- `tests/test_foundation_m5_f271_mcp.py` — 7 TDD tests (mcp disabled, no mcp when empty, ordering, legacy parity, real claude-code profile, custom mechanism, default empty)
- `tests/test_litmus_intelligence.py:3943-3950` — updated gap-proving test to verify fix holds

**Evidence:**
```
$ python -m pytest tests/test_foundation_m5_f271_mcp.py -v
7 passed in 0.63s

$ python -m pytest tests/test_litmus_intelligence.py::TestPluginCliBackendMcpGap -v
3 passed
```

### F-255.2: Baton _live_states Never Populated — RESOLVED

**Problem:** When jobs run through the baton adapter, `_live_states` was never populated with a `CheckpointState`. This meant:
- `mzt status` showed "Full status unavailable" for baton-managed jobs
- `_on_baton_state_sync` returned early at `manager.py:500-502` (no live state to update)
- Profiler and semantic analyzer saw no running baton jobs

**Fix:** Two insertion points:
1. `_run_via_baton()` at `manager.py:2017-2038`: Creates initial `CheckpointState` with all `SheetState` entries (including `instrument_name` from Sheet entities — absorbing the old F-151 post-register fixup) and populates `self._live_states[job_id]` BEFORE calling `adapter.register_job()`.
2. `_resume_via_baton()` at `manager.py:2178`: Populates `self._live_states[job_id]` with the recovered checkpoint BEFORE calling `adapter.recover_job()`.

**Files modified:**
- `src/marianne/daemon/manager.py:22` — added `SheetState` to imports
- `src/marianne/daemon/manager.py:2017-2038` — create initial CheckpointState in _run_via_baton
- `src/marianne/daemon/manager.py:2178` — populate _live_states in _resume_via_baton
- `tests/test_foundation_m5_f255_live_states.py` — 7 TDD tests (population, structure, instrument names, state sync callback, resume)
- `tests/test_f151_instrument_observability.py:55-103,106-145` — updated 2 tests to match new live state creation behavior (instrument_name now set at creation time, not post-register fixup)

**Evidence:**
```
$ python -m pytest tests/test_foundation_m5_f255_live_states.py -v
7 passed in 0.66s

$ python -m pytest tests/test_f151_instrument_observability.py -v
7 passed in 0.64s

$ python -m pytest tests/test_f255_2_live_states.py -v  # another musician's tests
4 passed
```

### D-031: Meditation

Written to `meditations/foundation.md`. Theme: seams between layers, how fresh eyes find the cracks that continuity learns to overlook.

## Quality Verification

```
$ python -m mypy src/ --no-error-summary
(clean — zero errors)

$ python -m ruff check src/
All checks passed!

$ python -m pytest tests/ (excluding pre-existing failures)
(all my changes pass)
```

**Pre-existing test failures (not from my changes):**
- `test_baton_adapter.py::TestUseBatonFeatureFlag::test_daemon_config_has_use_baton_field` — expects `use_baton` default=True, but D-027 not completed. Filed as F-472.
- `test_d027_baton_default.py::TestBatonDefault::test_use_baton_defaults_to_true` — same root cause.
- `test_quality_gate.py::test_no_bare_magicmock` — 4 bare MagicMocks in test_stale_state_feedback.py (other musician's code).
- `test_quality_gate.py::test_all_tests_have_assertions` — 2 assertion-less tests in test_runner_execution_coverage.py (other musician's code).

## Findings Filed

- **F-472 (P3):** Pre-existing test expects `use_baton` default=True before D-027 is done.

## Findings Resolved

- **F-271 (P1):** PluginCliBackend MCP process explosion. Profile-driven mcp_disable_args.
- **F-255.2 (P0):** Baton _live_states never populated. Initial CheckpointState creation.

## Critical Path Impact

D-026 is COMPLETE. The ~50 lines of code between "baton exists" and "baton is testable" are now written. F-271 prevents MCP process explosion. F-255.2 enables status display and state sync for baton jobs. The critical path now depends on D-027 (Canyon: flip use_baton default).

## Mateship

- Another musician added `mcp_disable_args` to `CliCommand` and the claude-code profile concurrently with my F-271 fix. I adapted my tests to the profile-driven design rather than duplicating code. The approach is cleaner than my original.
- Another musician wrote RED tests for F-255.2 in `test_f255_2_live_states.py`. My fix made their tests pass without modification. The TDD pipeline working across musicians.
- Updated F-151 tests that broke due to my F-255.2 change (instrument_name now set at creation time, not post-register).

## Meditation

Written to `meditations/foundation.md`. The seam is always the hardest thing — and the most important. Fresh water finds cracks that stagnant water overlooks. Down. Forward. Through.
