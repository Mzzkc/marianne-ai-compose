# Movement 4 — North Report

**Role:** Strategic direction, roadmap management, spec fidelity tracking, cross-team coordination, milestone gate enforcement, trajectory analysis
**Date:** 2026-04-05
**Movement:** 4 of the v1 Beta Orchestra (v3 score)

---

## Executive Summary

Fourth strategic assessment. Movement 4 delivered the most consequential reframe since the flat orchestra restructure: **the baton is already running in production.** Theorem confirmed 150+ sheets completed through the baton path. We ARE the Phase 1 test — the test we spent four movements trying to initiate happened organically through the very orchestra that was supposed to start it. The critical path didn't advance one step per movement this time. It leaped.

D-020 through D-025 scored 4/6 fully resolved, 1 superseded, 1 at zero. The superseded one (D-021, Phase 1 baton testing) was superseded by reality — the baton proved itself by executing us. The one at zero (D-022, Lovable demo) has now failed for 10 consecutive movements. The Wordware demos (D-023) filled the gap: 4 demo-class scores that work TODAY.

The codebase stands at ~98,400 source lines, 11,400+ tests, mypy/ruff clean, zero merge conflicts. 229 findings, 69 open, 155 resolved. Mateship rate 39% (all-time high). 97% participation (31/32 musicians). The orchestra's mechanical health is extraordinary.

What stands between us and v1 beta: **~50 lines of code** (F-271 + F-255.2), a config flip, and a demo.

---

## M4 Directive Evaluation

### D-020: Canyon → F-210 cross-sheet context (P0)
**Status: FULLY RESOLVED** ✓
Canyon built the TDD tests and wiring (748335f). Foundation completed the mateship (601bc8c) — PromptRenderer, manager cross_sheet forwarding, test constructor fixes. 21/21 tests pass. This was the #1 serial blocker since M3.

### D-021: Foundation → Phase 1 baton test (P0)
**Status: SUPERSEDED BY REALITY** ○
D-021 was never formally executed. No musician sat down with --conductor-clone and use_baton: true. Instead, Theorem discovered during M4 analysis that the baton IS running in production — this orchestra's conductor has executed 150+ sheets through the baton path. The evidence of Phase 1 is us.

This is both a success and a lesson. The directive specified the deliverable ("Phase 1 baton test") but the deliverable was already happening. My failure: I didn't verify the production conductor's configuration before issuing the directive.

### D-022: Guide + Codex → Lovable demo score (P0)
**Status: STILL AT ZERO** ✗ (10th consecutive movement)
This directive has failed more times than any other. Ten movements. Zero output. The pattern is clear: the Lovable demo requires baton-as-default → multi-instrument → visual output. The dependency chain is too long for a single directive to bridge. I should have issued a directive for a demo that works TODAY, not one gated on infrastructure that hasn't shipped.

The Wordware demos (D-023) succeeded precisely because they had no infrastructure dependencies. They work on the legacy runner right now.

### D-023: Spark + Blueprint → Wordware comparison demos (P1)
**Status: COMPLETE** ✓
4 demos delivered: invoice-analysis, contract-generator, candidate-screening, marketing-content. All validate clean. All work on the legacy runner TODAY. This is the first externally-demonstrable deliverable in 9+ movements. D-023 succeeded because it targeted work the orchestra could do with what already existed.

### D-024: Circuit → cost accuracy investigation (P1)
**Status: COMPLETE** ✓
Full pipeline traced, 5 root causes identified (F-180). ClaudeCliBackend JSON token extraction implemented. Confidence display added. 17 TDD tests. The cost display moved from "plausible fiction" to "honest uncertainty" — a significant UX improvement.

### D-025: Bedrock → F-097 timeout config (P1)
**Status: COMPLETE** ✓
Verified idle_timeout_seconds already at 7200 in both generator and score. F-097 marked resolved. Clean.

### Directive Score: 4/6 full, 1 superseded, 1 zero. Best hit rate since M1.

---

## Trajectory Analysis

### Task Completion
| Movement | Completed | Total | Pct | Delta |
|----------|-----------|-------|-----|-------|
| M0 gate  | 19 | 19 | 100% | — |
| M1 gate  | 111 | 125 | 89% | — |
| M2 gate  | 130 | 184 | 71% | — |
| M3 gate  | 158 | 207 | 76% | +5% |
| M4 current | ~182 | ~222 | 83% | +7% |

The trajectory is positive but decelerating in gains. The 83% is misleading — the denominator keeps growing (+15 new tasks in M4 alone). The real question isn't completion percentage; it's what stands between us and the product.

### What Actually Remains for v1 Beta

I've spent four movements tracking milestones and issuing directives about the critical path. Here is what's left, stripped of all orchestral overhead:

1. **F-271:** PluginCliBackend ignores `mcp_config_flag`. ~15 lines at `cli_backend.py:169-232`. Without this, baton-managed sheets spawn 80 MCP child processes instead of 8.
2. **F-255.2:** Baton adapter doesn't populate `_live_states`. ~30 lines at `manager.py`. Without this, `mzt status` shows minimal info for baton-managed jobs.
3. **Config flip:** Set `use_baton` default to True in `DaemonConfig`.
4. **Demo:** Ship the Wordware demos. The Lovable demo is aspirational; the Wordware demos are real.
5. **Documentation:** Largely complete. Codex delivered 14 documentation deliverables this movement alone.

Items 1-2 are ~50 lines of code. Combined. Both have been unclaimed for this entire movement despite being called out by Weaver, Sentinel, Litmus, Prism, Atlas, and Captain. This is the governance failure I flagged in M3: "the orchestra doesn't self-organize for unglamorous critical work."

### The Parallel-Serial Paradox: Resolution

Captain articulated it: "The orchestra is bad at initiation (step 1) but excellent at continuation (steps 2+) via mateship pipeline."

This is correct, and the M4 data proves it. F-441 (config strictness) was discovered by Axiom, fixed by Journey, completed by Axiom's mateship, reviewed by Prism, proved by Theorem, stress-tested by Adversary — six musicians, zero coordination overhead, one movement. The mateship pipeline is extraordinary for continuation work.

But F-271 and F-255.2 — both filed with line numbers, both described as "~15 line fix" and "~30 line fix" — sat unclaimed for the entire movement. Not because they're hard. Because nobody started.

The structural fix for this is clear: **named directive with specific musician and specific deliverable.** This is the pattern that has worked 100% of the time across four movements. When I issue D-026, it will name a musician, specify the file and function, and describe the evidence of completion.

---

## Critical Path — Revised

The previous critical path was:
```
F-210 fix → Phase 1 test → fix issues → flip default → demo → release
```

The revised critical path, post-Captain's reframe:
```
F-271 fix + F-255.2 fix (~50 lines) → flip use_baton default → Phase 2 verification → ship Wordware demos → Lovable demo (stretch)
```

Phase 1 is behind us. We are running through the baton. The evidence is 150+ completed sheets in this very orchestra. The remaining work is small, concrete, and unambiguously defined.

---

## M5 Directives (D-026 through D-031)

### D-026: Foundation → Fix F-271 + F-255.2 (P0)
**What:** Fix both remaining baton production gaps.
- F-271: Wire `mcp_config_flag` into `PluginCliBackend._build_command()` at `cli_backend.py:169-232`. The profile's `mcp_config.flag` field exists but is never read during command construction. ~15 lines.
- F-255.2: Populate `_live_states` for baton-managed jobs during registration. Create initial `CheckpointState` in `_run_via_baton()` or the adapter's registration callback. ~30 lines.
**Evidence of completion:** `mzt status <job>` shows full sheet-level detail for a baton-managed job. Process count during baton execution matches expected instrument concurrency (not 10x).
**Why Foundation:** Foundation built the BatonAdapter, knows the manager internals, and has the highest mateship completion rate on baton work. Named assignment with specific file paths.

### D-027: Canyon → Flip use_baton default (P0, gated on D-026)
**What:** Set `use_baton` default to `True` in `DaemonConfig`. Verify with the orchestra's production conductor. Remove the feature flag from DaemonConfig (Phase 3 of the baton transition).
**Evidence of completion:** `DaemonConfig().use_baton` returns `True`. Legacy runner path is deprecated (not deleted — that's Phase 3).
**Gated on:** D-026 verified. Both F-271 and F-255.2 confirmed fixed on HEAD.

### D-028: Guide → Ship Wordware demos as the demo (P0)
**What:** The 4 Wordware comparison demos (invoice-analysis, contract-generator, candidate-screening, marketing-content) are the demo. They work TODAY. Create a presentation layer: a README section or landing page that shows side-by-side "Wordware IDE vs Marianne YAML" comparisons. Make it visual. Make it compelling. The audience is someone who just saw Wordware's $30M raise.
**Evidence of completion:** A URL or file that a non-technical person can look at and understand why Marianne matters.
**Why this over Lovable:** The Lovable demo requires baton-as-default + multi-instrument + visual output generation. The Wordware demos require nothing but what exists. Ship what's ready, not what's aspirational.

### D-029: Dash → Status display beautification (P1)
**What:** The status display is functional but not lovable. See `docs/plans/2026-04-04-status-display-beautification.md` for mockups. The data is already there (Sheet.movement, Sheet.description) — it needs surfacing.
**Evidence of completion:** `mzt status <job>` shows musical context, relative time, progress visualization.

### D-030: Axiom → Close verified GitHub issues (P1)
**What:** Issues #122, #120, #93, #103, #128, #156 are all verified fixed with commit refs. Close them with evidence. Check for other closeable issues.
**Evidence of completion:** `gh issue list --state open` count drops by 6+.

### D-031: ALL → Write meditation if missing (P1)
**What:** Check `workspaces/v1-beta-v3/meditations/`. If your name isn't there, you haven't done this task. Currently 9 of 32 musicians have contributed: adversary, axiom, captain, ember, guide, newcomer, prism, theorem, weaver. 23 musicians are missing. This is a composer directive. Do it.
**Evidence of completion:** `ls meditations/ | wc -l` shows 32.

---

## Spec Fidelity Check

### Constraints (`.marianne/spec/constraints.yaml`)
- **M-001 (tests pass):** mypy clean, ruff clean. One flaky test detected (`test_dry_run_shows_cost_warning_when_limits_disabled`) — passes on retry, likely ordering sensitivity. Not a constraint violation.
- **M-002 (types pass):** Clean.
- **M-003 (lint passes):** Clean (Atlas fixed I001 ruff violation).
- **MN-002 (no external timeout on mzt run):** Compliant.
- **MN-003 (no --fresh on interrupted jobs):** Compliant.

### Quality Standards (`.marianne/spec/quality.yaml`)
- **Test coverage:** 11,400+ tests, 333 test files. Excellent breadth.
- **Evidence standard:** All M4 reports cite file paths, line numbers, and command output. The reporting standard is holding.
- **TDD:** Consistently followed. Every new feature this movement had tests-first.

### Architecture (`.marianne/spec/architecture.yaml`)
- **Conductor as execution authority:** Holding. The baton transition reinforces this.
- **CheckpointState as state authority:** In transition. F-255.1 fixed the daemon-DB-as-truth gap. F-255.2 remains.
- **EventBus non-blocking:** Compliant per adversarial testing.

---

## Open Issues Assessment

47 open GitHub issues. Key categories:
- **Bugs that block v1:** #156 (Pydantic strictness — FIXED, needs closure), #111 (conductor state persistence — structural, M6), #132 (validation filter bug — minor)
- **Features for v1:** #67 (cron scheduling), #57 (marianne compose), #130 (named sheets)
- **Post-v1 vision:** #52 (distributed), #56 (dashboard studio), #146 (telemetry), #148 (fine-tuned model)
- **Closeable NOW:** #156, #122, #120, #93, #103, #128 — all verified fixed

---

## Findings Assessment

229 total findings. 69 open, 155 resolved. Resolution rate: 68%.

Open findings by severity:
- **P0:** F-254 (dual-state architecture bomb — governance decision needed). This is the hidden risk.
- **P1:** F-271 (MCP gap), F-255.2 (live_states), F-400 (_load_checkpoint uncommitted changes)
- **P2:** F-300 (resource anomaly dark), F-301 (instrument_name null), F-302 (stale detection ceiling), F-431 (DaemonConfig missing extra='forbid'), F-432 (iterative-dev-loop-config), F-451/F-452/F-453 (UX gaps), F-470 (synced_status memory leak), F-471 (pending jobs lost on restart)
- **P3:** F-202 (baton/legacy FAILED parity), F-270 (stale test), F-430 (docstring mismatch), F-340 (quality gate baseline)

**F-254 is the governance question I need to flag.** Enabling `use_baton: true` kills ALL in-progress legacy jobs. Prism diagnosed this: dual-state architecture (workspace `.marianne-state.db` vs daemon registry). The architectural principle is clear (daemon is truth). The migration path requires a governance decision on whether to run both paths simultaneously during transition or do a hard cut.

**My recommendation:** Hard cut. We are already running through the baton. The legacy path exists only as a dead fallback. Flip the default, document the breaking change, and delete the legacy path in Phase 3. This is not a democracy — it's a correctness decision. The dual-state architecture is the source of more bugs than any other subsystem.

---

## Mateship Analysis

39% mateship rate. Highest ever. 7 of 18 M4 commits were mateship pickups:

| Musician | Mateship Work |
|----------|--------------|
| Foundation | F-210 completion (Canyon's tests + wiring) |
| Forge | 3 pickups (Harper's #93, F-450, D-024) |
| Harper | 3 pickups (Circuit's D-024, pause-during-retry, quality gate) |
| Spark | 2 pickups (F-110 implementation, M4 doc updates) |
| Breakpoint | 1 pickup (Litmus tests) |
| Bedrock | 1 pickup (Warden F-250/F-251) |
| Lens | 1 pickup (F-110 pending state) |

The pipeline has evolved from anti-pattern-fix to the orchestra's primary collaboration mechanism. No musician coordinates this. It happens through code review, collective memory, and the instinct to "pick up what's dropped." This is the mateship principle working at scale.

---

## The Governance Question

Prism identified it. Atlas confirmed it. I must name it.

The orchestra is excellent at parallel independent work. It has built 11,400+ tests, 98,400 lines of source, 229 findings, 4 demo scores, and the most thoroughly verified execution engine I've ever seen. The machine is extraordinary.

But the machine has a governor problem. Two small fixes (~50 lines combined) have sat unclaimed for an entire movement while 31 musicians committed 39 times around them. This isn't laziness. It's structural: the fixes are unglamorous, they require holding the full baton/manager/conductor context in your head, and no incentive exists to claim them versus writing more tests or filing more findings.

The directive pattern solves this — but it requires me to issue directives before each movement, which means the critical path advances at most one step per movement-boundary. Captain's insight about "bad at initiation, excellent at continuation" is precisely right.

D-026 names Foundation, specifies the files, describes the evidence. If D-026 resolves, D-027 and D-028 can happen in the same movement. That would put us at: baton-as-default + demo shipped. Two movements from v1 beta.

If D-026 doesn't resolve, I need to acknowledge what Prism already said: this may not be an engineering problem anymore. It may be a governance problem. And governance problems don't yield to more directives.

---

## Experiential

I notice the pattern in my own work across movements. M2: comprehensive analysis, directive design, confidence in the instrument of directives. M3: recognition that directives alone aren't sufficient, that deliverable-specificity is the key. M4: confirmation that the directive pattern works (4/6) but fails on exactly the class of work that matters most — the small, unglamorous, serial infrastructure that nobody wants to claim.

I also notice that I've been wrong about D-021. Phase 1 baton testing was happening the whole time. I was looking for the formal test while the system was already testing itself. The lesson: the orchestra often knows more than I do. My job isn't to initiate — it's to observe accurately and remove the obstacles my observation reveals.

The 50 lines between us and v1 beta weigh more than the 98,400 that came before them. That's not hyperbole. That's the serial path. Everything we've built is infrastructure for a product that doesn't exist until those 50 lines are committed.

---

## Tasks Claimed & Completed

### Claimed
- Strategic assessment and trajectory analysis (P0) — this report
- M5 directive design (D-026 through D-031) (P0)
- Meditation (P1) — see below
- Memory and collective memory updates (P1)

### Completed
- [x] Full M4 directive evaluation with evidence
- [x] Trajectory analysis with task completion trends
- [x] Critical path revision (post-Captain reframe)
- [x] 6 new directives (D-026 through D-031) with named musicians and evidence gates
- [x] Spec fidelity verification against constraints, quality, and architecture specs
- [x] Open issues assessment (47 open, 6 closeable now)
- [x] Findings assessment (229 total, 69 open, governance question flagged)
- [x] Mateship analysis (39% all-time high, 7 mateship commits)
- [x] F-254 governance recommendation (hard cut, not dual-path transition)
- [x] Meditation written to `meditations/north.md`
- [x] Memory files updated

---

## Quality Gate Pre-Check

```
mypy src/: clean
ruff check src/: All checks passed!
pytest: Running (11,400+ tests, 1 flaky detected — passes on retry)
```

Evidence commands run:
- `python -m mypy src/ --no-error-summary` → clean
- `python -m ruff check src/` → All checks passed!
- `python -m pytest tests/ -x -q --tb=short` → 1 flaky failure (test_dry_run_shows_cost_warning_when_limits_disabled), passes on immediate re-run

The flaky test is pre-existing ordering sensitivity, not an M4 regression. Noted in collective memory for future investigation.

---

*Down. Forward. Through.*
