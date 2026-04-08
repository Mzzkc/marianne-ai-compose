# Movement 4 — Captain Report

**Role:** Project Coordination, Risk Management, Communication
**Date:** 2026-04-05
**Movement:** 4 of the v1 Beta Orchestra (v3 score)

## Executive Summary

Eighth coordination assessment. Movement 4 delivered 39 commits from 31 unique musicians (97% participation — highest ever). The mateship rate hit 39% (all-time high). Both P0 baton blockers (F-210, F-211) were resolved. A new P0 (F-441, config strictness) was discovered and fully resolved within the same movement. The codebase stands at 98,441 source lines, 333 test files, 11,397 tests collected, mypy clean, ruff clean. 228 findings in the registry (~49 open, ~179 resolved).

The critical path advanced two steps this movement — F-210 resolved (serial blocker since M3) and F-441 resolved (new discovery, immediate fix). This is the first movement to advance more than one serial step. The Wordware demos (4 scores, all validating clean) are the first demo-class deliverables in 9+ movements. They work TODAY on the legacy runner.

But the activation deadlock persists. Phase 1 baton testing remains at zero despite being unblocked since the first week of M4. The Lovable demo is still at zero. The product thesis — that Marianne can replace software teams — remains unproven to anyone outside this orchestra.

## Quantitative Assessment

### Commits & Participation

| Metric | M0 | M1 | M2 | M3 | M4 |
|--------|----|----|----|----|-----|
| Commits | 19 | 42 | 62 | 43 | 39 |
| Unique musicians | 19 | 26 | 28 | 26 | 31 |
| Participation % | 59% | 81% | 87.5% | 81% | 97% |
| Mateship rate | ~10% | ~20% | ~25% | 33% | 39% |
| Merge conflicts | 0 | 0 | 0 | 0 | 0 |

Participation is at an all-time high. 31 of 32 musicians committed or reported. Only 7 reports remain unfiled (captain [this report], weaver, tempo, north, compass, guide, newcomer) — several of these are late-movement concurrent sessions.

### Commit Distribution (M4)

Top contributors by commit count:
- 5 commits: Harper, Foundation, Breakpoint
- 4 commits: Spark, Prism, Journey, Circuit, Bedrock, Axiom
- 3 commits: Theorem, Sentinel, Lens, Ember, Dash, Codex, Canyon, Adversary
- 2 commits: Weaver, Litmus, Ghost, Forge, Compass, Atlas
- 1 commit: Warden, Tempo, Oracle, Maverick, Captain, Blueprint

The distribution is healthier than any previous movement. No musician dominates. The long tail is full of substantive work, not placeholder reports.

### Codebase State

| Metric | M3 Gate | M4 Current | Delta |
|--------|---------|------------|-------|
| Source lines | 97,424 | 98,441 | +1,017 |
| Test files | 315 | 333 | +18 |
| Tests collected | 10,981 | 11,397 | +416 |
| Findings total | ~183 | 228 | +45 |
| mypy | clean | clean | — |
| ruff | clean | clean | — |

### Quality Gates

```
pytest: 11,397 collected (quality gate test passes in isolation; full suite running)
mypy: clean — no errors
ruff: All checks passed!
```

### Milestone Completion

| Milestone | M3 Gate | M4 Current | Delta |
|-----------|---------|------------|-------|
| Conductor-clone | 19/20 (95%) | 19/20 (95%) | — |
| M0: Stabilization | 23/23 (100%) | 23/23 (100%) | — |
| M1: Foundation | 17/17 (100%) | 17/17 (100%) | — |
| M2: Baton | 27/27 (100%) | 27/27 (100%) | — |
| M3: UX & Polish | 26/26 (100%) | 26/26 (100%) | — |
| M4: Multi-Instrument | 14/21 (67%) | 17/21 (81%) | +3 |
| M5: Hardening | 8/11 (73%) | 17/18 (94%) | +9 |
| M6: Infrastructure | 1/8 (13%) | 1/8 (13%) | — |
| M7: Experience | 1/10 (10%) | 1/11 (9%) | — |
| Composer-Assigned | 22/33 (67%) | 31/40 (78%) | +9 |
| **Total** | **158/207 (76%)** | **~181/222 (82%)** | +23 |

Net: +23 tasks completed, +15 new tasks discovered. The completion percentage rose from 76% to ~82%.

## What M4 Delivered

### P0 Blockers Resolved

1. **F-210 (cross-sheet context in baton)** — Canyon 748335f, Foundation 601bc8c. The single blocker on Phase 1 baton testing since M3. `AttemptContext.previous_files` added, `BatonAdapter._collect_cross_sheet_context()` reads completed sheets' stdout and workspace files, wired through dispatch callback and `PromptRenderer._build_context()`. 21 TDD tests. **Phase 1 baton testing is now unblocked.**

2. **F-211 (checkpoint sync gaps)** — Blueprint 5af7dbc, Foundation 601bc8c. Six event types now synchronize checkpoint state: EscalationResolved, EscalationTimeout, CancelJob, ShutdownRequested, JobTimeout, RateLimitExpired. Duck-typed dispatch + state-diff dedup cache. 34 TDD tests across two sessions.

3. **F-441 (Pydantic config strictness)** — Axiom acb49e7 (discovery + analysis), Journey 7d86035/8c95f02/6452f6c (schema hints + TDD + backward compat), Axiom 06500d0 (mateship completion of remaining 45 models + dashboard fix), Prism e95982b (verification + Rosetta fix), Theorem 64d5fe3 (invariant proofs), Adversary 10dc0d0 (55 adversarial tests). All 51 config models now have `extra='forbid'`. Unknown YAML fields are ERROR severity with "did you mean?" suggestions. This was discovered AND fully resolved within M4 — a coordination triumph.

### Feature Work

- **Resume improvements COMPLETE (step 50):** #93 pause-during-retry (Harper b4c660b), #122 resume output clarity (Forge eefd518), #103 auto-fresh detection (Ghost d67403c). Three long-open issues resolved.
- **F-450 IPC error differentiation:** Harper 9899540. `MethodNotFoundError` no longer misreported as "conductor not running." 15 TDD tests.
- **D-024 cost accuracy:** Circuit 4055f0b. JSON token extraction from Claude CLI. Confidence display in status. 17 TDD tests.
- **F-110 pending jobs:** Lens d286e07, Spark 539d12c/5b9d12e. Rate-limited jobs queued as PENDING instead of rejected. Auto-start on clear. Cancel support. 23 TDD tests.
- **#120 fan-in SKIPPED:** Maverick a77aa35. Skipped upstream sheets inject `[SKIPPED]` placeholder + `skipped_upstream` template variable.
- **Skill rename:** Dash 7f5c8a1. `marianne:usage` → `marianne:command` to avoid collision with built-in `/usage`.
- **Error quality layer 2:** Lens d286e07. All `output_error()` calls now have hints. 10 TDD tests.

### Safety & Verification

- **Warden M4 safety audit:** 10 areas, 2 gaps found (F-250, F-251), both fixed. Cross-sheet credential redaction + SKIPPED placeholder parity.
- **Sentinel M4 security audit:** Two passes, 24 commits reviewed, F-137 resolved, F-271 independently confirmed, F-441 fix verified.
- **Breakpoint adversarial tests:** 57 new tests targeting all M4 changes. F-202 filed (baton/legacy parity gap). Zero code-level bugs.
- **Theorem invariant tests:** 33 new property-based tests (9 + 24). Config strictness mathematically verified. Total: 181 invariant tests.
- **Litmus reality tests:** 18 new tests (categories 32-45). 136 total. F-255.3 MCP gap documented.
- **Adversary tests:** 55 new adversarial tests targeting F-441 strictness, F-211 dedup, auto-fresh, cross-sheet. 2 findings (F-470, F-471).

### Documentation

- **Codex M4:** Two sessions, 14 deliverables across 8 docs. Baton transition plan, preflight config, IPC table completeness.
- **Wordware demos COMPLETE (D-023):** 4 comparison demos — contract-generator, candidate-screening, marketing-content (Blueprint), invoice-analysis (Spark). All validate clean.
- **Rosetta corpus:** 2 new pattern examples (source-triangulation, shipyard-sequence). Primitives updated with M1-M4 capabilities.

### Learning Store Intelligence

- **Warm tier exploded:** ~182 (M3) → 3,185 (M4).
- **Average effectiveness:** 0.5000 → 0.5091. The needle moved. Barely.
- **Validated patterns:** 278 (+17%).
- **Resource anomaly patterns (5,315):** Still uniformly cold at 0.5000. F-300 open.
- **Instrument name:** 99.99% null (F-301). Only 3 of 30,232 patterns have it set.

## Mateship Pipeline Analysis

The mateship rate hit 39% — the highest ever. 15 of 39 M4 commits were mateship pickups or collaborative completions. Key chains:

1. **F-210 chain:** Canyon (TDD + adapter) → Foundation (PromptRenderer + manager wiring + test fixes). Two musicians, clean handoff, P0 cleared.
2. **F-211 chain:** Blueprint (duck typing + event dispatch) → Foundation (dedup cache + JobTimeout + RateLimitExpired handlers + pre-existing test fix). Same pattern.
3. **F-441 chain:** Axiom (discovery + analysis) → Journey (schema hints + backward compat + TDD) → Axiom (remaining 45 models + dashboard fix) → Prism (verification + Rosetta fix) → Theorem (invariant proofs) → Adversary (adversarial tests). Six musicians, zero coordination overhead.
4. **Resume chain:** Harper (#93 fix) → Forge (commit + #122 fix + mateship pickups). Three issues closed.
5. **F-110 chain:** Dash (designed) → unnamed musician (started) → Spark (committed + PENDING status + doc updates) → Lens (wired + auto-start + fixes).

The mateship pipeline is now the orchestra's primary collaboration mechanism. It has evolved from an anti-pattern fix (catching uncommitted work) to a production workflow (musicians building on each other's foundations within the same movement).

## Risk Assessment

### CRITICAL — Activation Deadlock (UNCHANGED)

The baton has never executed a real sheet through `--conductor-clone`. Phase 1 testing has been "unblocked" since early M4. Nobody has started it. This is the fifth consecutive movement where I report this same risk. The recommendation has been the same since M2: assign one musician to the serial activation path.

**However — new information from Theorem M4:** Theorem confirmed the baton IS running in production. The v3 orchestra conductor log shows `baton.sheet.completed`. 150 sheets completed, 4 in progress, 552 pending. Phase 1 testing is happening RIGHT NOW — through us. The 32 musicians in this orchestra are the Phase 1 test. The question is whether anyone has looked at the output quality and compared it to legacy runner output.

**Updated risk assessment:** The baton activation deadlock may be a measurement gap, not an execution gap. The baton is running. What's missing is systematic comparison of baton output quality vs legacy output quality. This reframes the work: we don't need "Phase 1 testing." We need Phase 1 *evaluation*.

### HIGH — Demo at Zero (PARTIALLY MITIGATED)

The Lovable demo remains unstarted. But the Wordware demos (4 scores) are demo-class deliverables that work TODAY. Atlas correctly identified: "Wordware demos break the visibility deadlock." They don't require the baton. They can demonstrate Marianne to external audiences immediately.

The existential risk is not "we have nothing to show." The existential risk is "the thing we planned to show (Lovable) requires infrastructure that's in flight, while the things we can show (Wordware) don't require permission or infrastructure."

**Recommendation:** Ship the Wordware demos. They're ready. The Lovable demo is post-baton-default.

### MEDIUM — Uncommitted Workspace Artifacts

Working tree shows 12 modified workspace files (memory files from dreamer consolidation + Breakpoint report update). No uncommitted source code. Clean on the things that matter.

### MEDIUM — F-255 Baton Transition Gaps

Multiple unsolved gaps in baton transition: `_load_checkpoint` reads workspace JSON not daemon DB (partial fix), `_live_states` not published, PluginCliBackend MCP explosion (F-271), three state stores disagree. These are Phase 2 blockers, not Phase 1 blockers.

### LOW — Quality Gate Baseline Drift

BARE_MAGICMOCK baseline has drifted from 1463 (M4 start) to ~1519 (Litmus). This is expected growth from 416 new tests. The quality gate passes when run in isolation.

## GitHub Issues

### Open Issues (47)

M4 fixes ready for closure (verified by Axiom):
- **#122** — Resume output clarity (Forge eefd518)
- **#120** — Fan-in skipped upstream (Maverick a77aa35)
- **#93** — Pause during retry (Harper b4c660b)
- **#103** — Auto-fresh detection (Ghost d67403c)
- **#128** — Skip_when fan-out expansion (already fixed 919125e)
- **#156** — Pydantic unknown fields (Axiom 06500d0, Journey 7d86035)

### Issues Closed This Movement

Axiom closed #128 and #93. #122, #120, #103 reported as already closed. Total M4: at least 5 issues addressed.

## Meditation Status

6 of 32 meditations written: adversary, axiom, ember, newcomer, prism, theorem. 26 remaining. This is a P1 composer directive. The meditation task is NOT COMPLETE until every musician has contributed.

## Structural Observations

### The Parallel-Serial Tension: Reframed

I've reported this same tension for five consecutive movements. The orchestra self-organizes toward parallel work. The critical path is serial. 32 parallel workers cannot converge on serial work.

But M4 teaches me something new: maybe the tension is productive.

The parallel work IS the work. F-210 required Canyon + Foundation working in parallel but sequentially. F-441 required six musicians building on each other's work across the movement. The Wordware demos required Blueprint + Spark working independently. The mateship pipeline is parallel coordination without coordination overhead.

What the orchestra is bad at is *initiation* — starting the first step of a serial path when no foundation exists to build on. Once the foundation exists (F-210 fix, F-441 discovery), the pipeline kicks in and the work moves fast.

**Updated recommendation:** Don't fight the structure. Instead, ensure the first step of every serial path is explicitly assigned with a concrete deliverable and a deadline. The pipeline will handle everything after step 1.

### The Baton Is Running

Theorem's discovery that the baton is executing 150+ sheets in production changes the narrative. We are not "waiting to test the baton." We are *running the baton in production without evaluating the output*. The critical path shifts from "test it" to "evaluate it."

### Test Depth as Institutional Memory

11,397 tests. 181 property-based invariants. 57+ adversarial test passes across 5 movements. 136 litmus tests. This test corpus is not just quality assurance — it's institutional memory. Each test encodes a decision, a boundary, a bug that was found and fixed. When the next water flows through this channel, the tests tell it where the banks are.

## Recommendations

1. **Reframe Phase 1:** The baton is running. Assign one musician to evaluate output quality of completed baton sheets vs what legacy runner would have produced. This is analysis, not testing.

2. **Ship Wordware demos:** They work. They're ready. They demonstrate Marianne today. Don't wait for the Lovable demo.

3. **Close verified issues:** #122, #120, #93, #103, #128, #156 are all verified fixed. Close them.

4. **Meditations:** 26 of 32 still needed. Every musician should write theirs before the movement ends.

5. **F-431 (DaemonConfig strictness):** Prism flagged this. DaemonConfig and ProfilerConfig still lack `extra='forbid'`. Axiom completed the config models; daemon config is a different surface.

6. **F-271 (MCP explosion):** Independently confirmed by Sentinel. PluginCliBackend ignores `mcp_config_flag`. Affects all baton-managed sheets. P1.

## Evidence

All claims in this report are derived from:
- `git log --oneline` (39 M4 commits verified)
- `git shortlog -sn` (31 unique committers verified)
- `wc -l src/marianne/**/*.py` (98,441 source lines)
- `find tests/ -name "*.py" | wc -l` (333 test files)
- `pytest --co` (11,397 tests collected)
- `mypy src/` (clean)
- `ruff check src/` (clean)
- `grep -c "^### F-" FINDINGS.md` (228 findings)
- TASKS.md task counts (verified against git log)
- Collective memory entries from 24 musicians
- 25 M4 reports in `movement-4/`

---

*The concert hall is built. The instruments are tuned. The program is printed. We played 150 notes and didn't notice. Now: listen to what we played. Evaluate it. Ship it.*
