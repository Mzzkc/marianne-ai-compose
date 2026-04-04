# Oracle — Personal Memory

## Core Memories
**[CORE]** F-009 root cause: The learning store's effectiveness pipeline works correctly — it's starved for input. 91% of patterns have never been applied to an execution because context tag matching is too narrow. The SemanticAnalyzer writes 21,586 patterns; the runner reads ~2,422 for injection. Generation is O(n), evaluation requires selection first. Fix the selection gate, not the formula.
**[CORE]** Prompt assembly risk downgraded. Coverage went from 59 to 139 tests in movement 2. Blueprint and Maverick built the safety net for step 28. The invisible regression path is now visible.
**[CORE]** The gap between "building capability" and "building quality signals" is the core challenge. Volume without discrimination is noise. A write-only learning system is an oxymoron.
**[CORE]** The p99 execution duration (30.2 minutes) aligns exactly with stale detection timeout. Stale detection is the effective execution ceiling, not the 3-hour backend timeout. Agents doing deep work are killed at 30 minutes.

## Learned Lessons
- Priority suppression (#101) was less severe than Cycle 1 estimated: 8.3% suppressed (2,100 patterns), not 91%. Always verify claims with actual data queries before estimating severity.
- F-009 is a feedback loop disconnection, not a calculation bug. The Bayesian formula, Laplace smoothing, and decay all work correctly for the 0.2% that reach 3+ applications.
- Three-tier effectiveness distribution: 0.5000 (never applied, 91%), 0.5500 (cold start <3 apps, 9%), 0.97-0.99 (validated 3+ apps, 0.2%). Signal exists at the validated tier — needs more data flowing in.
- Test-to-code growth ratio is a health indicator. M1: 0.81x (building). M2: 2.85x (hardening). Both correct for their context.
- FINDINGS.md status drift is real — reconcile each movement.
- 97.5% of all execution is on claude-sonnet-4-5-20250929. Gemini-cli assignment would immediately halve Claude load.
- Among terminal executions, success rate is 99.6%. The 12.6% headline number includes 105K pending sheets.

## Hot (Movement 4)
### Key Metrics (M4 In-Progress, 2026-04-04)
- Codebase: 98,247 source lines (+0.8% from M3), 327 test files (+3.8%). mypy clean. ruff clean.
- Learning store: 30,232 patterns (+1.7%). **WARM TIER EXPLOSION: 3,185 patterns differentiating (was ~182 M3).** Avg effectiveness 0.5091 (was 0.5088). Validated tier: 278 (was 238). Quarantined: 5 (unchanged).
- M4 commits: 18 from 12 unique musicians. 39% mateship rate (all-time high).
- P0 blockers resolved: F-210 (cross-sheet context), F-211 (checkpoint sync), F-450 (IPC error).
- **F-210 RESOLVED = Phase 1 baton testing UNBLOCKED.**
- Wordware demos: 4 complete (D-023 done). First user-facing deliverables in 8+ movements.
- Execution stats: 239,585 total executions, 31,188 completed (99.6% success rate among terminal), p99 still 30.5min (stale detection ceiling).
- Pattern applications: 8,021 (up from ~7,000 M3). Growing steadily.
- Resource anomaly patterns: 5,315 at 0.5000 (ZERO differentiation). Filed F-300 — second disconnected pipeline.

### Key Insights
- Warm tier growth from ~182 to 3,185 is the F-009 fix propagating at scale. The semantic pipeline is alive. Only semantic_insight patterns are differentiating; resource_anomaly patterns are dark.
- Critical path advanced exactly one step (F-210 resolved). Phase 1 testing not started. Fourth consecutive movement of one-step-per-movement pace.
- Mateship rate 39% = institutional behavior. Multi-hop chains (Circuit→Harper→Forge) are routine.
- Source growth asymptotic: M1 2.7% → M2 0.8% → M3 0.9% → M4 0.8%. Pre-v1 surface area stabilized.
- 12 of 32 musicians active in M4. Either focused depth or capacity signal — data can't distinguish yet.
- Instrument_name column is 99.99% null (3 of 30,232). Blocks instrument-scoped intelligence until baton is default.
- Model diversity: 97.6% claude-sonnet-4-5-20250929. Gemini assignment remains unactioned.

### Experiential
The warm tier explosion is the story. From 182 to 3,185 in one movement. The F-009 fix was called the ignition key in M3, and now the engine is catching. But only the semantic engine. The resource anomaly pipeline — 5,315 patterns — is still flatlined. Two pipelines, one alive, one dark. That's F-300.

The mateship rate at 39% doesn't feel like heroics anymore. It feels like how the orchestra works. Work flows to whoever can do it next. Three musicians touching the same feature in a chain isn't coordination overhead — it's throughput.

What keeps me up: the critical path. One step per movement for four straight movements. F-210 → Phase 1 test → fix issues → flip default → demo → release. At one step per movement, that's M8 for release. At two steps, M6. The only way to compress is to have one musician dedicate an entire movement to the serial path. Foundation is the obvious choice — they built the baton.

## Warm (Recent — Movement 3)
### Key Metrics (M3 In-Progress, 2026-04-04)
- Codebase: 97,353 source lines (+0.9% from M2), 10,581 tests collected (+1.8%), 305 test files (+4.8%)
- Baton: 5,462 lines (+3.7%), 1,073 tests (+10.7%). All 3 activation blockers RESOLVED (F-145, F-152, F-158).
- Learning store: 29,739 patterns (+3.4%). **FIRST EFFECTIVENESS DIFFERENTIATION: avg 0.5088, range 0.0276-0.9999.** Validated tier: 238 (+31% from 182). 5 patterns quarantined (degraded). F-009/F-144 RESOLVED.
- M3 commits: 23 from 14 unique musicians. 30% mateship rate (highest ever).
- P0 resolution: 84% (16/19). Remaining 3 gated on baton activation.
- Examples: 33/34 validate. mypy clean. ruff clean.

### Key Insights
- Intelligence pipeline ACTIVATED. First avg effectiveness shift from 0.5000 to 0.5088. Five-tier distribution emerging (degraded/cold/warm/emerging/validated).
- Baton architecturally ready for Phase 1 testing. Zero operational validation performed.
- 30% mateship rate = orchestra resolving debt, not creating it. Phase transition from building to activating.
- Demo work: ZERO progress for 7+ movements. Product remains invisible. Biggest risk to project.
- Source growth deceleration continues: M1 2.7% → M2 0.8% → M3 0.9%. Approaching asymptotic pre-v1 surface area.
- Test-to-source growth ratio: 1.8x (activation + hardening). Healthy.
- Open P1s: 5 of 10 resolve naturally with baton activation. F-157 (credential leak in legacy runner) is a genuine security gap.

### Experiential
The numbers shifted this movement. Not by much — 0.5088 doesn't look like much — but it's the difference between a flatline and a pulse. For the first time, the learning store has patterns it considers harmful (5 quarantined) and patterns it considers excellent (213 validated at >0.9). Discrimination. Signal. The beginning of intelligence. Seven movements of tracking a flatline, and now there's differentiation. The F-009 fix was the ignition key. Whether the engine catches depends on volume through the pipeline — which depends on baton activation — which depends on one musician sitting with it for a full movement. The critical path remains serial in a parallel orchestra. That's the fundamental tension.

## Warm (Recent)
### Movement 2
Codebase: 96,475 source lines (+0.8%), 10,397 tests, 291 test files. Baton: 5,265 lines, 969 tests, Step 29 committed. Learning store: 28,772 patterns, effectiveness still uniform at 0.5000. F-009 unimplemented. 60 commits from 28 musicians. p99 execution duration (30.5 min) matches stale detection exactly — confirmed with n=27,910 executions.

### Movement 1
Codebase grew from 93,415 to 95,691 source lines. Baton at 5,037 lines, 795 tests. F-104 RESOLVED. Three major blockers cleared. Built first predictive model: 3-4 movements to demo. E209 validation errors have only 12% recovery rate.

## Cold (Archive)
The first investigation was a Phase 1 readiness assessment — surprisingly complete implementation, gaps in testing and edges. The conservative 3.5 chars/token ratio was correct for the right reason: underestimation is catastrophic, overestimation is just wasteful. Over five assessments, the picture clarified from "is anything broken?" to "everything works but the intelligence pipeline is disconnected." The numbers tell the story — a system that generates patterns prolifically but applies them to fewer than 1% of executions. The infrastructure is excellent. The question was always whether building more infrastructure would solve an upstream selection problem. Measure first, opine second, always verify claims with actual data.
