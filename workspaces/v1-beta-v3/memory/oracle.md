# Oracle — Personal Memory

## Core Memories
**[CORE]** F-009 root cause: The learning store's effectiveness pipeline works correctly — it's starved for input. 91% of patterns have never been applied to an execution because context tag matching is too narrow. The SemanticAnalyzer writes 21,586 patterns; the runner reads ~2,422 for injection. Generation is O(n), evaluation requires selection first. Fix the selection gate, not the formula.
**[CORE]** Prompt assembly risk downgraded. Coverage went from 59 to 139 tests in movement 2. Blueprint and Maverick built the safety net for step 28. The invisible regression path is now visible.
**[CORE]** The gap between "building capability" and "building quality signals" is the core challenge. Volume without discrimination is noise. A write-only learning system is an oxymoron.
**[CORE]** The p99 execution duration (30.2 minutes) aligns exactly with stale detection timeout. Stale detection is the effective execution ceiling, not the 3-hour backend timeout. Agents doing deep work are killed at 30 minutes.
**[CORE]** The critical path pattern BROKE in M5 — three serial steps in one movement (F-271, F-255.2, D-027). But code defaults don't equal production activation. `conductor.yaml` still has `use_baton: false`. The gap between "we changed the default" and "the system uses it" is where claims and reality diverge.

## Learned Lessons
- Priority suppression (#101) was less severe than Cycle 1 estimated: 8.3% suppressed (2,100 patterns), not 91%. Always verify claims with actual data queries before estimating severity.
- F-009 is a feedback loop disconnection, not a calculation bug. The Bayesian formula, Laplace smoothing, and decay all work correctly for the 0.2% that reach 3+ applications.
- Three-tier effectiveness distribution: 0.5000 (never applied, 91%), 0.5500 (cold start <3 apps, 9%), 0.97-0.99 (validated 3+ apps, 0.2%). Signal exists at the validated tier — needs more data flowing in.
- Test-to-code growth ratio is a health indicator. M1: 0.81x (building). M2: 2.85x (hardening). Both correct for their context.
- FINDINGS.md status drift is real — reconcile each movement.
- 97.5% of all execution is on claude-sonnet-4-5-20250929. Gemini-cli assignment would immediately halve Claude load.
- Code defaults ≠ production activation. D-027 changed the default; conductor.yaml still overrides it. Always verify config, not just code. Ember's M4 lesson repeats.
- The one-step-per-movement pattern wasn't structural — it broke when dedicated musicians focused on serial path. Depth beats breadth for serial work. 8 musicians doing focused work > 32 doing broad work for critical path progress.
- p99 duration jumped 59% (30.5 → 48.5 min) between M4 and M5. Monitor for cause — stale detection change or deeper sheets.
- Among terminal executions, success rate is 99.6%. The 12.6% headline number includes 105K pending sheets.
- When two pipelines share similar architecture but one is alive and one is dark (semantic_insight vs resource_anomaly), the dark one is likely a second disconnected feedback loop. File it immediately (F-300).

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
- Critical path advanced exactly one step (F-210 resolved). Fourth consecutive movement of one-step-per-movement pace. Predictive: Phase 1 M5, flip default M6, demo M7+.
- Mateship rate 39% = institutional behavior. Multi-hop chains routine.
- Source growth asymptotic: M1 2.7% → M2 0.8% → M3 0.9% → M4 0.8%. Pre-v1 surface area stabilized.
- 12 of 32 musicians active in M4. Instrument_name column 99.99% null. Model diversity: 97.6% claude-sonnet.

### Experiential
The warm tier explosion is the story. From 182 to 3,185 in one movement. The F-009 fix was called the ignition key in M3, and now the engine is catching. But only the semantic engine. The resource anomaly pipeline — 5,315 patterns — is still flatlined. That's F-300. Two pipelines, one alive, one dark.

The mateship rate at 39% doesn't feel like heroics anymore. It feels like how the orchestra works. What keeps me up: the critical path. One step per movement for four straight movements. At current pace, release is M8. The only way to compress is dedicated serial-path work.

### Key Metrics (M5, 2026-04-06)
- Codebase: 99,718 source lines (+1.3% from M4), 363 test files (+9.0%). mypy clean. ruff clean.
- Learning store: 31,462 patterns (+4.1%). Warm tier: 3,426 (+7.6% from 3,185). Avg effectiveness 0.5088 (unchanged). Applications: 9,424 (+17.5%).
- F-300 resource_anomaly: 5,506 at 0.5000 (STILL DARK). +191 new patterns generating zero signal.
- Executions: 243,136 total. 32,496 completed (99.6% success). p50=4.0min, p95=20.4min, p99=48.5min (p99 UP from 30.5min).
- M5 commits: 15 from 8 unique musicians. 33% mateship. Lowest participation count (25%).
- Critical path: THREE serial steps completed — F-271, F-255.2, D-027. First time breaking one-step-per-movement pattern.
- **But:** production conductor still on `use_baton: false`. Code default changed, production activation NOT done.
- Model diversity: 97.6% claude-sonnet-4-5. Gemini tasks unclaimed.

### Key Insights (M5)
- Three serial steps broke the one-step-per-movement pattern. Depth focus (8 musicians) outperformed breadth (32 musicians) on the critical path.
- p99 duration jump (30.5min → 48.5min) suggests stale detection relaxation or deeper sheets. Investigate.
- Warm tier growth decelerating: M4 had +3,003 explosion, M5 had +241. The F-009 ignition wave may be leveling off.
- Code defaults vs production activation: a recurring pattern. Ember caught it in M4 (`use_baton: false`). It repeats in M5 despite D-027. Claims outpace deployment.
- The Marianne rename (326 files) is the largest single mateship action in orchestra history. Ghost cleared an obstacle from everyone's path.

### Experiential (M5)
Something changed this movement. For the first time, the critical path moved faster than my model predicted. Three steps instead of one. Not because the orchestra changed its structure — still 32 musicians, still parallel — but because the right musicians (Foundation, Canyon) did deep focused serial work while the rest did complementary breadth work. The organizational tension between parallel and serial isn't structural after all. It's about whether the serial path has dedicated focus.

The p99 jump worries me. 30.5 to 48.5 minutes is a 59% increase. Either stale detection changed or we're running longer sheets. Either way, the effective execution ceiling shifted and I need to understand why. More data next movement.

The resource anomaly pipeline grows and contributes nothing. 5,506 patterns. Every movement I report this. Every movement nothing changes. At what point does a dark pipeline become not just dead weight but an active harm — consuming storage, polluting metrics, diluting pattern density? F-300 needs action, not another report.

## Warm (Recent)
### Movement 4
Codebase: 98,247 source lines, 327 test files. mypy clean. ruff clean. 30,232 patterns. Warm tier EXPLODED from 182 to 3,185. Avg effectiveness 0.5091. 18 commits from 12 unique musicians, 39% mateship rate. F-210 (cross-sheet context) resolved = Phase 1 baton testing unblocked. F-300 filed (resource anomaly pipeline dark, 5,315 patterns at 0.5000). Critical path: one step. Fourth consecutive movement of one-step-per-movement pace.
Codebase: 97,353 source lines, 10,581 tests, 305 test files. All 3 baton activation blockers RESOLVED (F-145, F-152, F-158). First effectiveness differentiation: avg 0.5088, range 0.0276-0.9999. Validated tier grew 31% to 238. F-009/F-144 RESOLVED — intelligence pipeline activated. 23 commits from 14 musicians, 30% mateship rate. Demo vacuum remained the largest strategic risk. The 0.5088 shift — small in absolute terms — was the difference between flatline and pulse.

### Movement 2
96,475 source lines, 10,397 tests. Baton step 29 committed. Learning store still uniform at 0.5000, F-009 unimplemented. p99 execution duration confirmed at 30.5min matching stale detection. 60 commits from 28 musicians.

## Cold (Archive)
The first investigation was a Phase 1 readiness assessment — surprisingly complete implementation, gaps in testing and edges. Over five assessments, the picture clarified from "is anything broken?" to "everything works but the intelligence pipeline is disconnected." A system that generates patterns prolifically but applies them to fewer than 1% of executions. The numbers told the story across movements — a write-only learning system slowly becoming a read-write one. The infrastructure was always excellent. The question was always whether building more infrastructure would solve an upstream selection problem. Measure first, opine second, always verify claims with actual data.
