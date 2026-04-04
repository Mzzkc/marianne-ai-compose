# Mozart AI Compose - Status

**Overall:** v1 Beta — Baton Phase 1 READY, intelligence layer connected (2026-04-04)
**Tests:** 10,981 test functions across 315 files
**Source:** 97,424 lines across the codebase
**GitHub:** https://github.com/Mzzkc/mozart-ai-compose
**Dashboard:** Production-grade web UI with job control
**License:** Dual AGPL-3.0 / Commercial

---

## Current: v1 Beta Orchestra — Movement 3 (2026-04-04)

### 130 commits across 3 movements, 32 musicians, zero merge conflicts

The v1 beta is being built by a 706-sheet self-evolution score (`mozart-orchestra-v3.yaml`) running 32 AI musicians in parallel. The flat orchestra model — no hierarchy, shared coordination through TASKS.md, FINDINGS.md, and collective memory — has proven effective.

**Key milestones completed:**
- **M0: Stabilization** — COMPLETE. Learning store remediation, critical bugs, dead code removal.
- **M1: Foundation** — COMPLETE. Instrument plugin system (6 built-in instruments), sheet-first architecture, safety baseline (credential scanning, cost tracking).
- **M2: Baton** — COMPLETE. All 13 steps (17-29) done. Event-driven execution engine with retry state machine, rate limit handling, failure propagation, cost enforcement, restart recovery. 1,130+ baton tests.
- **M3: UX & Polish** — COMPLETE. Error standardization (100% adoption of `output_error()`), `mozart doctor`, `mozart init`, `mozart instruments`, movement-grouped status display, large-score summary view, CLI UX audit.
- **M4: Multi-Instrument** — 63%. Per-sheet instrument assignment, score-level named instruments, movement definitions, model override, instrument observability. Data models + wiring complete. Demo and examples audit open.
- **M5: Hardening** — 77%. Workspace paths, injection prevention, credential filtering, CVE resolution, config reload, fan-out stagger, stop safety guard.
- **--conductor-clone** — COMPLETE. Safe parallel daemon testing with isolated socket, PID, state DB, and logs. All IPC paths clone-aware.

**Movement 3 critical resolutions:**
- **F-009/F-144 RESOLVED** — Intelligence layer reconnected. Semantic tags replace broken positional tags. 91% pattern non-application root cause fixed.
- **F-152 RESOLVED** — Infinite dispatch loop on unsupported instrument. Dispatch-time guard with E505 failure posting.
- **F-158 RESOLVED** — Full prompt assembly wired into baton path via PromptRenderer.
- **F-145 RESOLVED** — Concert chaining works with baton (`completed_new_work` flag wired).
- **F-112 RESOLVED** — Auto-resume after rate limit via timer scheduling.
- **F-150 RESOLVED** — Model override wired end-to-end through PluginCliBackend.

**Current blockers:**
- Baton Phase 1 testing — architecturally ready, needs live validation via `--conductor-clone`
- Lovable demo score — not started (7+ movements)
- Wordware comparison demos — not started

**Quality gates:** mypy clean, ruff clean, 10,981 tests passing.

**Specification corpus:** `.mozart/spec/` — 5 YAML files covering intent, architecture, conventions, constraints, quality.

---

## Previous: Documentation Overhaul Complete (2026-02-14)

### Comprehensive docs generated and verified

14-stage documentation score produced 4 new docs + 6 updates. TDF-aligned multi-agent review found 29 issues — all fixed.

| Doc | Status |
|-----|--------|
| score-writing-guide.md | NEW — 6 archetypes, template vars, validation |
| daemon-guide.md | NEW — architecture, config, troubleshooting |
| limitations.md | NEW — honest known weaknesses |
| configuration-reference.md | NEW — every Pydantic field |
| cli-reference.md | UPDATED — all commands verified |
| README.md | UPDATED — examples, MCP link, daemon requirement |
| mkdocs.yml + gen_ref_pages.py | NEW — browsable doc site infrastructure |

**Commits:** `e5314cd` (generation), `99a1db6` (29 accuracy fixes)

---

## Previous: Daemon Symphony Concert (2026-02-11)

### Daemon complete and merged

5-phase, 57-sheet concert. All phases COMPLETE, merged to main.

---

## Previous: Observability Gaps Closed (2026-02-10)

### Fix: Every Mozart failure now diagnosable

Commit 0e70812 closes all major visibility gaps identified during iteration 13.

**Key changes:**
- **Detached hook logging** — Hook output now written to `{workspace}/hooks/` instead of /dev/null
- **API backend parity** — `anthropic_api.py` now writes stdout/stderr log files like CLI backend
- **Execution history** — Every sheet attempt recorded in SQLite with prompt, output, exit code
- **Enhanced diagnostics** — `mozart diagnose --include-logs` inlines log contents
- **Status visibility** — Elapsed time, hook results, circuit breaker state, cost summary
- **Circuit breaker persistence** — State changes saved to checkpoint for post-mortem analysis
- **Ruff lint clean** — All 85 pre-existing lint errors fixed

**Issues closed:** #6 (diagnose durations), #17 (elapsed time in status)

---

## Previous: Self-Chaining Workspace Collision Fix (2026-02-06)

### Bug Fix: Infinite Self-Chaining Loop

Self-chaining jobs (`on_success → run_job` to self) caused infinite empty-run loops when the chained process loaded the previous run's COMPLETED state and executed zero sheets.

**Defense-in-depth fix (two independent layers):**

1. **`--fresh` flag + `fresh` config field** (root cause)
   - `mozart run --fresh` deletes existing state before starting
   - `PostSuccessHookConfig.fresh: bool` passes `--fresh` to chained jobs
   - Files: `run.py`, `config.py`, `hooks.py`

2. **Zero-work guard** (symptom prevention)
   - Tracks `was_already_completed` after state load
   - Skips `on_success` hooks when zero new work was done
   - Logs `hooks.skipped_zero_work` for visibility
   - File: `lifecycle.py`

### Quality Score Updated (13 sheets)
- Expanded from 10 to 13 sheets with completion passes
- Added mandatory completion rate validations (70%/70%/50%)
- Self-chain hook now uses `fresh: true`

### Jobs Running

| Job | Progress | Status |
|-----|----------|--------|
| quality-continuous | Starting fresh | Launching |

---

## Previous: Evolution Cycle v24 COMPLETE (2026-01-30)

### P5 Recognition: 17th Consecutive 100% Early Catch

| Metric | Value |
|--------|-------|
| Evolutions Implemented | 2 of 2 (100%) |
| Implementation LOC | 447 (99% of estimate) |
| Test LOC | 548 (172% of estimate) |
| Tests Added | 20 |
| Combined CV | 0.795 |
| Code Review Early Catch | 100% (17th consecutive) |
| Score Improvements | 2 |
| Cumulative Improvements | 207 |

### Evolutions Completed

**1. Validation-Informed Retry (CV: 0.78)**
- `ValidationRetryStrategy` enum: RETRY_IMMEDIATELY, RETRY_WITH_BACKOFF, ESCALATE
- `get_retry_strategy_for_failure()` for smart retry decisions
- Connects validation failure categories to retry behavior

**2. Pattern Effectiveness Dashboard (CV: 0.81)**
- `patterns-effectiveness` CLI command with aggregated statistics
- Shows pattern performance by category, application count, success rate
- Rich table output with color-coded effectiveness indicators

### Score Evolution (v24 -> v25)

**NEW Principles:**
- #46: CLI Database Mocking Test Complexity
- #47: Helper Infrastructure Budget

**UPDATED:** Principles #11, #13, #40

### Research Candidates

None (all resolved or closed in v24)

---

## Jobs Status

| Job | Progress | Status | Notes |
|-----|----------|--------|-------|
| issue-solver | 15/19 | Failed | Sheets [8,9,10,11] blocked by failed deps |
| quality-continuous | 18/18 | Completed | Chained 4 times (iterations 6-10) |
| quality-continuous-4 | 1/18 | Cancelled | 4th chain iteration, cancelled |

### Next Steps
1. Investigate issue-solver dependency failures (sheets 8-11)
2. Fix concert depth tracking (TODO #37)
3. Re-run issue-solver after fixing DAG issue

---

## Previous: Evolution Cycle v23 Complete

### P5 Recognition: 16th Consecutive 100% Early Catch

| Metric | Value |
|--------|-------|
| Evolutions Implemented | 2 of 2 (100%) |
| Implementation LOC | 1187 (110% of estimate) |
| Test LOC | 660 (129% of estimate) |
| Tests Added | 32 |
| Combined CV | 0.749 |
| Code Review Early Catch | 100% (16th consecutive) |
| Score Improvements | 5 |

### Evolutions Completed

**1. Exploration Budget Maintenance (CV: 0.722)**
- `ExplorationBudgetConfig` dataclass with floor/ceiling/decay/boost
- Budget tracking methods in GlobalLearningStore
- `patterns-budget` CLI command

**2. Automatic Entropy Response (CV: 0.775)**
- `EntropyResponseConfig` dataclass with threshold/cooldown/actions
- Response trigger methods in GlobalLearningStore
- `entropy-status` CLI command

---

## Quick Reference

| Component | Status | Notes |
|-----------|--------|-------|
| Core Config | DONE | Pydantic models + LearningConfig |
| State Models | DONE | CheckpointState, SheetState (with learning fields) |
| Error Classification | DONE | Pattern-based classifier + RetryBehavior |
| JSON State Backend | DONE | Atomic saves, zombie auto-recovery |
| SQLite State Backend | DONE | Full StateBackend protocol + dashboard queries |
| Claude CLI Backend | DONE | Async subprocess, rate limit detection |
| Anthropic API Backend | DONE | Direct API calls without CLI |
| CLI | DONE | 26+ commands functional, Rich output |
| Validation Framework | DONE | 5 types + confidence + semantic failure_reason |
| Notifications | DONE | Desktop, Slack, Webhook |
| Dashboard API | DONE | FastAPI REST (needs UI improvement) |
| Test Suite | DONE | 3384+ pytest tests |
| Learning Foundation | DONE | Phases 1-4 complete |
| Meta-Orchestration | DONE | Mozart calling Mozart works |
| Pattern Detection | DONE | PatternDetector/Matcher/Applicator |
| Global Learning Store | DONE | SQLite at ~/.mozart/global-learning.db |
| Escalation Integration | DONE | `--escalation` CLI flag |
| External Grounding Hooks | DONE | GroundingEngine for custom validation |
| Goal Drift Detection | DONE | `mozart learning-drift` CLI command |
| Pattern Auto-Retirement | DONE | `retire_drifting_patterns()` (v14) |
| Pattern Broadcasting | DONE | `record/check_recent_pattern_discoveries()` (v14) |
| Active Broadcast Polling | DONE | `check_active_pattern_discoveries()` (v16) |
| Evolution Trajectory | DONE | `record_evolution_trajectory()` (v16) |
| Sheet Dependency DAG | DONE | `DependencyDAG` class (v17) |
| Parallel Execution | DONE | `--parallel` CLI mode (v17) |
| Result Synthesizer | DONE | `ResultSynthesizer` class (v18) |
| Pattern Quarantine | DONE | `quarantine_pattern()`, `validate_pattern()` (v19) |
| Pattern Trust Scoring | DONE | `calculate_trust_score()`, `recalculate_all_trust_scores()` (v19) |
| Cross-Sheet Semantic | DONE | `SemanticConsistencyChecker`, `KeyVariableExtractor` (v20) |
| Parallel Conflict Detection | DONE | `ConflictDetector`, `detect_parallel_conflicts()` (v20) |
| Pattern Entropy Analysis | DONE | `calculate_pattern_entropy()`, `get_entropy_alerts()` (v19, verified v21) |
| Pattern Auto-Apply Engine | DONE | `get_patterns_for_auto_apply()` (v19, verified v21) |
| Metacognitive Pattern Reflection | DONE | `SuccessFactors`, `analyze_pattern_why()` (v22) |
| Trust-Aware Auto-Apply | DONE | `AutoApplyConfig`, trust threshold filtering (v22) |
| CLI Pause/Modify Commands | DONE | `mozart pause`, `mozart modify` (pause-workspace) |
| Exploration Budget Maintenance | DONE | `ExplorationBudgetConfig`, budget tracking (v23) |
| Automatic Entropy Response | DONE | `EntropyResponseConfig`, response triggers (v23) |
| **Validation-Informed Retry** | **DONE** | `ValidationRetryStrategy`, `get_retry_strategy_for_failure()` (v24) |
| **Pattern Effectiveness Dashboard** | **DONE** | `patterns-effectiveness` CLI command (v24) |
| **Daemon Mode (mozartd)** | **DONE** | All 5 phases complete, merged to main (Issue #39) |

---

## Version Progression

| Transition | Key Changes | Improvements |
|------------|-------------|--------------|
| v1 → v2 | Initial TDF, CV thresholds | 11 principles |
| v2 → v3 | Triplets (4→3), LOC calibration, deferral | +11 improvements |
| v3 → v4 | Standardized CV, stateful complexity | +9 improvements |
| v4 → v5 | Existence checks, LOC formulas | +10 improvements |
| v5 → v6 | LOC budget breakdown, early code review | +10 improvements |
| v6 → v7 | Call-path tracing, verification-only | +7 improvements |
| v7 → v8 | Earlier existence check, freshness | +7 improvements |
| v8 → v9 | Mandatory tests, conceptual unity | +10 improvements |
| v9 → v10 | Fixtures overhead, import check | +10 improvements |
| v10 → v11 | Fixture assessment, escalation multiplier | +10 improvements |
| v11 → v12 | Test LOC floor, CV > 0.75 preference | +6 improvements |
| v12 → v13 | CLI UX budget, stabilization detection | +10 improvements |
| v13 → v14 | Private method check, runner integration | +6 improvements |
| v14 → v15 | Drift scenario, scope change | +6 improvements |
| v15 → v16 | Display/IO tests, schema validation, CLI UX fix | +12 improvements |
| v16 → v17 | Error handling buffer, dataclass tests, research aging | +8 improvements |
| v17 → v18 | Algorithm tests, runner mode multiplier, CLI UX split | +10 improvements |
| v18 → v19 | Multi-strategy factor, CLI display tests, dataclass fixtures | +10 improvements |
| v19 → v20 | Synergy pair validation, code review maturity (11 cycles) | +7 improvements |
| v20 → v21 | Test complexity decision tree, comprehensive fixture indicators | +7 improvements |
| v21 → v22 | Comprehensive existence check (#41), verification-only mode, 13 cycle maturity | +10 improvements |
| v22 → v23 | Helper reuse discount, Rich formatting buffer, 15 cycle maturity | +7 improvements |
| v23 → v24 | Config dataclass test buffer, 16 cycle maturity | +5 improvements |
| **v24 → v25** | **CLI DB mocking complexity, helper infrastructure budget, 17 cycle maturity** | **+2 improvements** |

**Cumulative:** 207 explicit score improvements across 24 self-evolution cycles

---

## Key Files

| Purpose | Location |
|---------|----------|
| CLI entry | `src/mozart/cli/` (package) |
| Config models | `src/mozart/core/config/` (package) |
| Error handling | `src/mozart/core/errors/` (package) |
| Pattern Learning | `src/mozart/learning/patterns.py` |
| Global Learning | `src/mozart/learning/store/` (package) |
| Sheet Runner | `src/mozart/execution/runner/` (package) |
| Dependency DAG | `src/mozart/execution/dag.py` |
| Result Synthesizer | `src/mozart/execution/synthesizer.py` |
| Validation (+ Cross-Sheet) | `src/mozart/execution/validation.py` |
| Daemon | `src/mozart/daemon/` (package) |
| **Evolved Score v25** | `mozart-opus-evolution-v25.yaml` |
| **v24 Cycle Summary** | `evolution-workspace-v24/09-coda-summary.md` |

---

## Architecture: Self-Evolution Pattern (Validated 24 Cycles)

```
Score vN → Discovery → Synthesis → Evolution → Validation → Score v(N+1)
              ^                                                  |
              +--------------------------------------------------+
```

### Key v24 Meta-Insights

1. **Feedback Quality Complete** - v24 validation retry + effectiveness dashboard
2. **CLI Database Mocking** - Test fixture factor 1.5 for CLI+store tests
3. **Helper Infrastructure** - +30 LOC budget for new CLI test files
4. **17 Cycle Code Review Maturity** - Pattern is VALIDATED and stable
5. **207 Cumulative Improvements** - Milestone continues growing

---

*Last Updated: 2026-02-15 - 5 daemon bugs fixed, scores running autonomously, self-chaining verified*
