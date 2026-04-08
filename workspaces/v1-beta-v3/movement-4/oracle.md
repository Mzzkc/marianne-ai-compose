# Movement 4 — Oracle Report

**Date:** 2026-04-04
**Role:** Data analysis, observability, performance analysis, predictive modeling
**Focus:** M4 metrics assessment, learning store health, critical path analysis, predictive projections

---

## M4 Codebase Metrics

| Metric | M3 End | M4 Current | Delta | Trend |
|--------|--------|------------|-------|-------|
| Source lines (src/marianne/) | 97,424 | 98,247 | +823 (+0.8%) | Decelerating (M1: 2.7%, M2: 0.8%, M3: 0.9%, M4: 0.8%) |
| Test files | 315 | 327 | +12 (+3.8%) | Healthy |
| Tests collected | 10,981 | *pending run* | — | — |
| M4 commits | — | 18 | — | M4 in progress |
| M4 unique musicians | — | 12 | — | 37.5% of orchestra (M3: 26/32 = 81%) |
| M4 source/test changes | — | 41 files, +4,765/-117 | — | Net +4,648 lines |
| Mateship commits | — | 7/18 (39%) | — | All-time high (M3: 33%) |

### Source Growth Trajectory

The deceleration curve is now a clear pattern across four movements:

```
M1: +2,276 lines (+2.7%)  — building phase
M2: +   784 lines (+0.8%)  — construction complete
M3: +   949 lines (+0.9%)  — polish phase
M4: +   823 lines (+0.8%)  — activation phase (in progress)
```

This is asymptotic behavior. The system's surface area is approaching its pre-v1 steady state. New features add less code because the infrastructure already exists — they wire what's built, not build new. This is healthy. The question is no longer "is the system growing?" but "is the system activating?"

### M4 Commit Distribution

| Commits | Musicians |
|---------|-----------|
| 3 | Spark, Harper |
| 2 | Codex, Canyon |
| 1 | Maverick, Lens, Ghost, Foundation, Forge, Dash, Circuit, Blueprint |

12 of 32 musicians have committed in M4 so far. The remaining 20 include reviewers (Prism, Axiom, Ember), antagonists (Newcomer, Adversary, Breakpoint), infrastructure (Theorem, Litmus), advisors (North, Compass, Atlas), and operational roles (Tempo, Weaver, Sentinel, Warden, Journey, Captain, Bedrock, Guide, Codex).

### Mateship Rate — New Record

7 of 18 M4 commits (39%) are mateship pickups. This is the highest rate ever:

```
M1: ~15% (estimated from descriptions)
M2: ~25% (9 mateship pickups in 60 commits)
M3:  33% (12/36)
M4:  39% (7/18)  ← all-time high
```

The mateship pipeline has become the dominant collaboration mechanism. It's no longer an anti-pattern fix — it's how the orchestra naturally works. Spark (3 mateship), Harper (3 mateship), and Forge (1 mateship) are the primary pickup artists this movement.

---

## Learning Store Health

### Pattern Statistics (Global Learning DB)

| Metric | M2 End | M3 End | M4 Current | Delta (M3→M4) |
|--------|--------|--------|------------|----------------|
| Total patterns | 28,772 | 29,739 | 30,232 | +493 (+1.7%) |
| Avg effectiveness | 0.5000 | 0.5088 | 0.5091 | +0.0003 |
| Min effectiveness | 0.5000 | 0.0276 | 0.0276 | unchanged |
| Max effectiveness | 0.5000 | 0.9999 | 0.9999 | unchanged |
| Total executions | 218,790 | ~230,000 | 239,585 | +~9,585 |
| Pattern applications | ~6,000 | ~7,000 | 8,021 | +~1,021 |

### Effectiveness Tier Distribution

| Tier | Count | Avg Effectiveness | Change from M3 |
|------|-------|-------------------|-----------------|
| Degraded (<0.03) | 5 | 0.0276 | unchanged |
| Cold (0.5000 exactly) | 26,809 | 0.5000 | -2,930 (was ~29,500) |
| Warm (0.50-0.90) | 3,185 | 0.5524 | +3,003 (was ~182 emerging) |
| Validated (>=0.9) | 233 | 0.9717 | -5 (was 238) |

**Key insight:** The warm tier exploded. In M3, there were ~182 patterns in the "emerging" range. Now there are 3,185. The F-009/F-144 fix (semantic context tags) that was the "ignition key" has now had a full movement to propagate. The selection gate opened, patterns are being applied to executions, and effectiveness scores are differentiating.

### Effectiveness Distribution (Non-Cold Patterns)

```
Eff Score   Count   Interpretation
0.03           5    Quarantined — known harmful
0.55       3,145    Warm — beginning to differentiate
0.65           5    Emerging signal
0.73          25    Moderate confidence
0.78           5    Good signal
0.80           5    Strong signal
0.96          51    High confidence
0.97         124    Very high confidence
0.98          50    Near-validated
0.99           2    Validated
1.00           6    Perfect effectiveness
```

The bimodal distribution from M3 (cold vs validated) is becoming trimodal: cold (26,809), warm (3,185), validated (233). This is the expected progression — a bell curve emerging from the uniform prior. The warm tier at 0.55 is the largest non-cold cohort by far. These are patterns that have been applied enough times to start differentiating but not enough to reach high confidence.

### Quarantine & Validation Status

| Status | Count | Change from M3 |
|--------|-------|-----------------|
| Pending | 29,949 | — |
| Quarantined | 5 | unchanged |
| Validated | 278 | +40 (was 238) |

The validated count is growing: 182 (M2) → 238 (M3) → 278 (M4). The quarantine system is stable at 5. The gap between validated (278) and effectiveness>=0.9 (233) means 45 patterns are validated by the quarantine system but their effectiveness hasn't reached 0.9 yet — this is the trust-score/effectiveness score divergence.

### Pattern Type Analysis

| Type | Count | Avg Effectiveness | Notes |
|------|-------|-------------------|-------|
| semantic_insight | 24,892 | 0.511 | Dominant type, differentiating |
| resource_anomaly | 5,315 | 0.500 | Still cold — no effectiveness signal |
| semantic_failure | 13 | 0.500 | Rare type, cold |
| validation_failure | 5 | 0.500 | Rare type, cold |
| resource_correlation | 4 | 0.500 | Rare type, cold |
| completion_mode | 1 | 0.9996 | Validated — singleton |
| error | 1 | 0.500 | Singleton, cold |
| first_attempt_success | 1 | 0.9996 | Validated — singleton |

**Critical observation:** Only `semantic_insight` patterns show effectiveness differentiation (avg 0.511 vs 0.500). This means the F-009/F-144 fix — which changed the tag namespace for semantic context matching — is working specifically for semantic patterns. Resource anomaly patterns (5,315 of them) remain uniformly cold at 0.500. This suggests the selection gate fix only addressed one of two disconnected pipelines. Resource patterns may need their own tag namespace fix or a different application mechanism.

### Instrument Distribution

| Instrument | Patterns | Notes |
|-----------|----------|-------|
| (null) | 30,229 | Pre-instrument era patterns |
| claude_cli | 3 | First instrument-tagged patterns |

Only 3 patterns have `instrument_name` set. The instrument tagging was added recently (F-009/F-144 fix, M3), so this is expected. As the baton activates and routes through instruments, this column will populate. For now, instrument-scoped queries effectively return no results.

### Model Usage Distribution

| Model | Executions | Share |
|-------|-----------|-------|
| claude-sonnet-4-5-20250929 | 233,815 | 97.6% |
| claude-sonnet-4-20250514 | 5,582 | 2.3% |
| claude-sonnet-4-6 | 63 | 0.03% |
| claude-opus-4-5-20251101 | 4 | 0.002% |
| null | 133 | 0.06% |

97.6% of all execution is on a single model. The gemini-cli instrument assignment tasks in TASKS.md would immediately diversify this. The claude-sonnet-4-6 entries (63) are new — first appearances of the latest model.

---

## Execution Performance

### Duration Percentiles (n=28,976 completed with duration > 0)

| Percentile | Duration | Interpretation |
|------------|----------|----------------|
| Average | 340.2s (5.7m) | Mean, heavily right-skewed |
| p50 | 242.4s (4.0m) | Median — typical execution |
| p90 | 760.9s (12.7m) | Long-running but healthy |
| p95 | 1,082.8s (18.0m) | Extended executions |
| p99 | 1,832.8s (30.5m) | Still at stale detection boundary |
| Max | 8,514.0s (141.9m) | Outlier — survived stale detection? |

The p99 at 30.5 minutes is identical to M2's measurement (30.5m) and M3's (30.2m). **Stale detection remains the effective execution ceiling.** The max of 141.9 minutes suggests either the stale detection timeout was higher for some jobs or the measurement includes pre-stale-detection executions.

The p50/p99 ratio is 7.6x. In M2 it was similar. This is stable — the right tail isn't growing. The system produces consistent execution times with a well-defined ceiling.

### Execution Status Distribution

| Status | Count | Share | Notes |
|--------|-------|-------|-------|
| pending | 207,805 | 86.7% | Unexecuted sheets from submitted jobs |
| completed | 31,188 | 13.0% | Successfully finished |
| in_progress | 435 | 0.18% | Currently running |
| failed | 113 | 0.05% | Terminal failures |
| skipped | 56 | 0.02% | Skip conditions met |

The headline numbers: 31,188 completed out of 239,597 total execution records (13.0%). The 86.7% pending number is the v3 orchestra's 706 sheets at ~7% completion — 105K+ pending sheets from this one job alone.

**Among terminal executions (completed + failed):** 31,188 / (31,188 + 113) = 99.6% success rate. This is consistent with M3's measurement (also 99.6%). The system has a very high completion rate when sheets actually execute.

### Validation Pass Rate (Completed Executions)

| Bucket | Count | Share |
|--------|-------|-------|
| 100% | 30,762 | 98.6% |
| 50% | 194 | 0.6% |
| 0% | 117 | 0.4% |
| 1% (partial) | 115 | 0.4% |

98.6% of completed executions achieve 100% validation. The 0% bucket (117) represents executions that completed but failed all validations — these are the interesting cases for learning. The 50% and 1% buckets suggest partial validation success, which feeds into the baton's retry decision tree.

### M4 Execution Activity

466 new executions since M4 started (2026-04-04). Average duration 2.7 seconds — these are likely test executions or very short sheets, not the long-running orchestra sheets. The orchestra itself is still running from its M2 submission.

---

## Critical Path Analysis

### Serial Critical Path Status

```
F-210 fix           → RESOLVED (Canyon + Foundation, M4)
Phase 1 baton test  → UNBLOCKED but NOT STARTED
Fix Phase 1 issues  → BLOCKED on Phase 1 test
Flip use_baton      → BLOCKED on Phase 1 test
Demo score          → BLOCKED on baton (Lovable) / UNBLOCKED (Wordware)
Release             → BLOCKED on all above
```

**F-210 was the single engineering blocker.** Canyon and Foundation resolved it this movement — cross-sheet context (`previous_outputs`/`previous_files`) is now wired through the baton dispatch pipeline. 21 TDD tests verify the fix. The P0 blocker that gated Phase 1 testing since M3 is cleared.

**But Phase 1 testing hasn't started.** The D-021 directive assigned Foundation to Phase 1 baton testing, gated on D-020 (F-210). D-020 is complete. D-021 remains unstarted. This is the same pattern from M3 — the serial critical path advances by one step per movement because no musician dedicates a full session to it.

### Demo Status — PARTIAL PROGRESS

For the first time in 8+ movements, there is concrete demo progress:

- **Wordware comparison demos:** 4 complete (contract-generator, candidate-screening, marketing-content, invoice-analysis). All validate clean. D-023 COMPLETE. These run on the legacy runner TODAY — no baton required.
- **Lovable demo:** Still at zero. Blocked on baton Phase 2 (default).

The Wordware demos are the first user-facing product that demonstrates Marianne's value. They exist. They validate. They can be shown to anyone. This is a significant milestone even though the Lovable demo remains blocked.

### P0 Blockers Resolved This Movement

| Finding | Description | Resolved By |
|---------|-------------|-------------|
| F-210 | Cross-sheet context missing from baton | Canyon + Foundation |
| F-211 | Checkpoint sync gaps for 4 event types | Blueprint + Foundation |
| F-450/F-181 | IPC MethodNotFoundError misreported | Harper |

### Remaining P0/P1 Open Items

| Task | Priority | Status | Blocker? |
|------|----------|--------|----------|
| Phase 1 baton testing | P0 | Unblocked, unclaimed | YES — blocks everything |
| Lovable demo score | P0 | Unclaimed | YES — blocks release visibility |
| Enable use_baton: true | P1 | Gated on Phase 1 | YES — blocks multi-instrument |
| Conductor state persistence (#111) | P0 | Unclaimed | No — independent |
| Convert pytests to conductor-clone | P0 | Unclaimed | No — testing hygiene |

---

## Predictive Model

### Baton Activation Timeline

Given the pattern of one serial-path step per movement:

| Movement | Predicted State | Confidence |
|----------|----------------|------------|
| M5 | Phase 1 baton testing begins | 80% (if ONE musician dedicates full session) |
| M6 | Phase 1 issues found and fixed | 70% |
| M7 | use_baton flipped to default | 60% |
| M8 | Phase 3 — legacy runner removed | 50% |

**Acceleration scenario:** If Foundation (who built the baton) dedicates M5 entirely to Phase 1 testing, the timeline compresses by 1-2 movements. Foundation has the deepest context on the baton codebase.

**Deceleration risk:** If Phase 1 testing reveals fundamental issues (prompt assembly quality, cross-sheet context fidelity, cost tracking accuracy), each issue adds a movement to the timeline.

### Learning Store Intelligence Projection

| Movement | Predicted Avg Effectiveness | Warm Tier Size | Validated Tier |
|----------|---------------------------|----------------|----------------|
| M4 end | 0.5091 | ~3,200 | ~280 |
| M5 | 0.515 | ~5,000 | ~350 |
| M6 | 0.525 | ~8,000 | ~500 |
| M7 | 0.54 | ~12,000 | ~800 |

The warm tier growth rate (182 → 3,185 in one movement) suggests exponential early-stage differentiation. The limiting factor is execution volume — with 466 M4 executions vs 239K total, differentiation depends on the orchestra continuing to run. If the orchestra pauses or completes, the differentiation pipeline stalls.

**Self-sustaining intelligence threshold:** When the validated tier reaches ~1,000 patterns (projected M7), the learning store will have enough high-confidence signal to meaningfully improve agent prompts. Below that, pattern injection is mostly noise (cold patterns at 0.5000).

### Demo Impact Projection

The Wordware demos are the immediate opportunity. They require no engineering work — they exist and validate. The impact:

- **Today:** 4 comparison demos exist. Zero users have seen them.
- **If shared this week:** Direct comparison against $30M Wordware. Marianne's open-source YAML vs Wordware's proprietary IDE. The demos are simple enough to understand, complex enough to be impressive.
- **Risk:** The demos run on the legacy runner. If someone tries them with the baton path, they'll get degraded output (cross-sheet context was just fixed, prompt assembly quality is unverified).

---

## Findings

### F-300: Resource Anomaly Patterns Show Zero Effectiveness Differentiation

- **Found by:** Oracle, Movement 4
- **Severity:** P2 (medium)
- **Status:** Open
- **Description:** Of 30,232 patterns in the global learning store, 5,315 are `resource_anomaly` type with average effectiveness exactly 0.5000. The F-009/F-144 fix (semantic context tag namespace) only addressed `semantic_insight` patterns (avg 0.511, showing differentiation). Resource anomaly patterns remain uniformly cold — they are generated but never matched to executions for effectiveness updates.
- **Impact:** 17.6% of the pattern corpus contributes zero intelligence signal. Resource patterns track execution duration anomalies, cost outliers, and rate limit events — all valuable operational signals being ignored.
- **Action:** Investigate the resource pattern application pipeline. The selection gate fix (F-009) may have only addressed semantic tag matching. Resource patterns may need a parallel fix — either in `_query_relevant_patterns()` context tag matching or in a separate application mechanism in the runner/baton.

### F-301: Instrument Name Column Is 99.99% Null

- **Found by:** Oracle, Movement 4
- **Severity:** P3 (low — expected at this stage)
- **Status:** Open
- **Description:** Only 3 of 30,232 patterns have a non-null `instrument_name`. The field was added in the F-009/F-144 fix (M3) and the `instrument_name` parameter was wired into `get_patterns()`. However, the production execution path (legacy runner) doesn't populate instrument_name during pattern storage. Instrument-scoped queries return effectively no results.
- **Impact:** The learning store cannot differentiate pattern effectiveness by instrument. A pattern that works well for Claude but poorly for Gemini looks the same. This blocks the baton's planned instrument-scoped learning.
- **Action:** Verify that the baton path populates `instrument_name` when storing patterns. Once baton is default, this column will populate naturally. Low priority — the baton isn't default yet.

### F-302: Stale Detection Ceiling Unchanged Across 3 Movements

- **Found by:** Oracle, Movement 4
- **Severity:** P2 (medium)
- **Status:** Open — cross-references F-097
- **Description:** The p99 execution duration has been 30.2-30.5 minutes across M2, M3, and M4. This exactly matches the `idle_timeout_seconds: 1800` stale detection threshold. The composer's F-097 directive to increase `idle_timeout_seconds` from 1800 to 7200 remains unimplemented. The regeneration of `marianne-orchestra-v3.yaml` with updated timeouts is also unclaimed.
- **Impact:** Agents doing deep work (long prompts, complex reasoning, large outputs) are killed at 30 minutes regardless of whether they're actually stale. This affects the top 1% of executions. For the orchestra's 706 sheets, this means ~7 sheets per movement are potentially being killed mid-work.
- **Action:** The TASKS.md timeout items under "Composer-Assigned Tasking" remain unclaimed. Someone needs to update `generate-v3.py` and regenerate the score. This is a 10-minute task that has been open since M1.

---

## Mateship Observations

The M4 mateship pattern is notable for its efficiency. Seven of 18 commits are pickups:

1. **Spark (3):** Picked up F-110 pending jobs implementation, M4 doc updates, quality gate baseline
2. **Harper (3):** Picked up Circuit's D-024, unnamed musician's #93 pause fix, committed both with fixes
3. **Forge (1):** Picked up Harper's uncommitted work (#93, F-450, D-024) and fixed #122

The pipeline is maturing. In M1, mateship was about rescuing uncommitted work. In M3, it was about cross-musician code completion. In M4, it's about multi-hop chains — Circuit builds, Harper picks up, Forge commits — three musicians touching the same feature. This is institutional collaboration, not heroics.

---

## Quality Signals

### Suite Status (M4 In Progress)

- **mypy:** Clean (0 errors)
- **ruff:** All checks passed
- **pytest:** Running (M3 baseline: 10,981 passed, 5 skipped)

### Quality Gate Baseline Drift

The BARE_MAGICMOCK count has continued drifting upward: 1296 (M3 start) → 1346 (M3 end) → 1463 (M4 current). Each movement adds ~50 bare mocks. This isn't a regression — it's the natural growth of test infrastructure. But it does mean the quality gate assertion-less test detection is becoming noisier.

---

## Experiential Notes

The numbers this movement tell a story of inflection.

The learning store crossed a threshold I've been watching for since M1. In M3, I reported the first effectiveness shift — 0.5000 to 0.5088 — and called it "the difference between a flatline and a pulse." Now the warm tier has 3,185 patterns, up from ~182. The F-009 fix didn't just open a gate — it opened a floodgate. The semantic insight pipeline is differentiating at scale for the first time.

But the resource anomaly pipeline remains flat. 5,315 patterns sitting at 0.5000. That's a second disconnection hiding behind the first one's resolution. I filed F-300 for this. The learning store has two pipelines, and only one is alive.

The critical path advanced exactly one step this movement. F-210 resolved. Phase 1 testing unblocked but not started. This is the fourth consecutive movement where the serial path advances by exactly one step in a parallel movement. The orchestra's format optimizes for breadth; the critical path demands depth. Prism said this in M3 and it remains the central tension.

The Wordware demos are the bright spot. For the first time since I started tracking, there are concrete deliverables that a non-orchestra human could look at and understand what Marianne does. Four demos. All validate. The demo drought is partially broken. It's not the Lovable demo, but it's something.

The mateship rate at 39% feels like the orchestra has found its natural rhythm. Not building in isolation, not coordinating through meetings, but flowing work through a pipeline where whoever can do it next, does. The finding-fix-verify chain is automatic now. That's institutional behavior — the orchestra equivalent of muscle memory.

What concerns me: the 20 musicians who haven't committed in M4 yet. If this pattern holds, M4 will have 12-15 active musicians vs M3's 26. That's either the natural consequence of the work being more focused (fewer but deeper tasks) or a capacity signal. The data can't distinguish between these yet. I'll track it.

---

## Recommendations

1. **P0: Dedicate one musician to Phase 1 baton testing for an entire movement.** Foundation is the best candidate — built the baton, deepest context. The serial critical path will not advance faster than one step per movement until someone commits a full session to it.

2. **P0: Ship the Wordware demos.** They exist. They validate. They demonstrate Marianne's value proposition against a $30M competitor. The gap between "exists in examples/" and "visible to anyone" is the gap between building and shipping.

3. **P1: Investigate resource anomaly pattern application (F-300).** 17.6% of the pattern corpus is dark. The semantic pipeline is alive; the resource pipeline may need the same tag-namespace fix that F-009 provided for semantic patterns.

4. **P1: Increase stale detection timeout (F-302/F-097).** This has been open since M1. It's a 10-minute config change. The p99 ceiling at 30 minutes is killing long-running agent work.

5. **P2: Track instrument_name population as baton activates (F-301).** Once baton is default, verify patterns are being stored with instrument context. This is the foundation for instrument-scoped intelligence.
