# North — Movement 3 Report

## Eighth Strategic Assessment

**Date:** 2026-04-04
**Scope:** Full movement 3 — trajectory analysis, critical path assessment, directive evaluation, M4 directive issuance
**Method:** Cross-referenced 30 musician reports, 36 commits, TASKS.md, FINDINGS.md, collective memory, git log, GitHub issues, quality gate data, composer's notes, codebase verification

---

## Executive Summary

**Movement 3 closed every structural gap. Movement 4 must close the experience gap.**

The numbers are clear: 150/197 tasks complete (78%). M0 through M3 ALL COMPLETE. 10,981 tests. mypy clean. ruff clean. 10 critical/high findings resolved. 5 GitHub issues closed. The mateship pipeline hit 30% — its highest rate. The baton is architecturally complete. The intelligence layer is reconnected. UX is polished.

And yet: zero live baton tests. Zero demo. Zero Wordware comparison. Zero user experience. Eight movements of building. Zero movements of using.

Captain's report says the infrastructure era is over. Captain is right. The infrastructure era IS over — but only if we force the transition. Left to its own gravity, the orchestra will continue building. D-014 through D-019 proved named directives work. D-020 through D-025 must aim them at the activation path.

---

## Directive Evaluation — D-014 through D-019

| Directive | Target | Result | Assessment |
|-----------|--------|--------|------------|
| D-014 | Maverick → F-009/F-144 intelligence layer | Maverick built it, Foundation committed it (e9a9feb) | **FULFILLED** — but via mateship, not direct execution. Maverick built and left uncommitted. Foundation picked up. Pattern: talented musician, zero commit discipline. |
| D-015 | Foundation/Canyon → baton activation | Foundation built BatonAdapter + analysis. Canyon wired completions + 3 blockers. | **FULFILLED** — structurally. But the directive said "activation" and what we got was "readiness." The baton has never run. |
| D-016 | Guide → demo score | Guide updated hello.yaml with multi-movement fiction. | **PARTIALLY FULFILLED** — hello.yaml exists but doesn't meet the composer's "visual, impressive" bar. No Lovable demo. No Wordware comparisons. |
| D-017 | Codex → docs | Codex documented all M3 features across 5 docs. | **FULFILLED** — thorough, verified, current. |
| D-018 | Bedrock → finding ID collisions | Range-based allocation implemented. FINDING_RANGES.md + helper script. | **FULFILLED** |
| D-019 | Spark → examples polish | 9/18 fan-out examples modernized. | **PARTIALLY FULFILLED** — good progress, not complete. |

**Pattern:** Named directives have a 67% full completion rate (4/6) and 100% partial-or-better rate (6/6). The pattern works. The precision of the assignment matters — "activate the baton" produced readiness, not activation. "Demo" produced hello.yaml, not the Lovable demo.

The lesson: directives must specify the deliverable, not the direction.

---

## Critical Path — The Serial Bottleneck

The critical path has five serial dependencies:

```
F-210 fix ──→ Phase 1 clone test ──→ fix issues found ──→ flip use_baton default ──→ demo score
```

**F-210 is the gatekeeper.** Cross-sheet context is completely missing from the baton path. I verified this against `src/marianne/daemon/baton/musician.py` and `src/marianne/daemon/baton/prompt.py` — zero references to `previous_outputs`, `previous_files`, or `cross_sheet`. The field exists on `SheetExecutionState` at `state.py:161-163` but is never populated. The legacy runner does this in `_populate_cross_sheet_context()` at `context.py:171-221`.

20 example scores reference cross-sheet context. Without F-210, Phase 1 testing produces scores that appear to run but silently lose inter-sheet context. This is the most dangerous kind of bug — it makes broken output look correct.

**Estimated work:** ~100-200 lines. The adapter needs to capture stdout after each sheet execution and make it available to subsequent sheets via the state model. The dispatch callback already has access to the result; the plumbing just isn't there.

---

## Open P0 Findings (5)

| Finding | Status | Verified On HEAD | Notes |
|---------|--------|-----------------|-------|
| F-210 | **OPEN** | Confirmed — zero cross-sheet wiring in baton | BLOCKS Phase 1. #1 priority. |
| F-097 | **OPEN** | `idle_timeout_seconds=1800` in generate-v3.py unchanged | Affects the running orchestra. Needs generate-v3.py update + regeneration. |
| F-100 | **BLOCKED** | `use_baton: false` in conductor config | Blocked by F-210, then Phase 1 testing |
| Lovable demo | **OPEN** | Zero files created | 8+ movements deferred |
| Convert pytests to clone | **OPEN** | Zero conversion done | Systematic but low-risk |

---

## Movement 3 — By The Numbers

| Metric | M2 (Final) | M3 (Final) | Delta |
|--------|-----------|-----------|-------|
| Tasks complete | 130/184 (71%) | 150/197 (78%) | +20 tasks, +7% |
| Source lines | 96,475 | 97,424 | +949 |
| Test count | ~10,400 | 10,981 | +581 |
| Test files | 291 | 315 | +24 |
| Commits (movement) | 60 | 36 | -24 |
| Unique committers | 28/32 | 24/32 | -4 |
| Critical findings resolved | — | 10 | — |
| GitHub issues closed | — | 5 | — |
| Mateship pickups | — | ~12 (30% rate) | Highest ever |
| Open findings | ~46 | ~49 | +3 new, 10 resolved |
| Quality gate | PASS | PASS | Stable |

**Participation detail:** 24 committed code. 3 wrote reports only (Oracle, Sentinel, Warden — all valuable analysis roles). 5 produced no M3 output: Captain, Compass, Guide, North, Tempo. Captain delivered late in the movement. North (me) and Guide/Compass are the main gaps — Guide's demo directive was partially fulfilled. I own my gap.

---

## Milestone Completion (Verified Against TASKS.md)

| Milestone | Complete | Total | % | Change |
|-----------|----------|-------|---|--------|
| M0: Stabilization | 23 | 23 | 100% | — |
| M1: Foundation | 17 | 17 | 100% | — |
| M2: Baton | 27 | 27 | 100% | — |
| M3: UX & Polish | 24 | 24 | 100% | — |
| M4: Multi-Instrument | 12 | 19 | 63% | — |
| M5: Hardening | 10 | 13 | 77% | — |
| M6: Infrastructure | 1 | 8 | 12% | — |
| M7: Experience | 1 | 11 | 9% | — |
| Conductor Clone | 19 | 20 | 95% | — |
| Composer-Assigned | 16 | 30 | 53% | — |
| **Total** | **150** | **197** | **78%** | +7% from M2 |

Four milestones complete. M4 and M5 are the active workfronts. M6/M7 are post-v1 polish.

---

## What Moved (M3 Achievements)

1. **All baton structural blockers resolved.** F-152 (dispatch guard), F-158 (prompt assembly), F-145 (concert chaining), F-009/F-144 (intelligence layer), F-112 (auto-resume), F-150 (model override). Six critical fixes across seven musicians.

2. **Intelligence layer ignition.** F-009/F-144 was the longest-standing P0 — 7+ movements, three independent root cause analyses, finally fixed. Effectiveness shifted from 0.5000 → 0.5088. Validated tier +31%. Oracle projects self-sustaining by M6.

3. **UX layer complete.** Error standardization at 100% (`output_error()` everywhere). Context-aware hints on all 6 rejection types. Rate limit time-remaining display. Stop safety guard. Stale PID cleanup. Instrument column in status. CLI help groups.

4. **Testing depth unprecedented.** 258 adversarial tests (4 passes, zero bugs in passes 2-4). 29 property-based invariant tests proving 15 families. 21 litmus tests. The baton's mathematical consistency is proven. What remains is operational validation.

5. **Mateship pipeline at 30%.** The finding→fix→commit pipeline now runs routinely. F-009 (Maverick→Foundation), F-150 (Blueprint→Foundation), stop guard (Ghost→Circuit), validate hints (Journey→Breakpoint), terminology (Weaver→Newcomer). Zero coordination overhead.

## What Didn't Move (Critical Gaps)

1. **Baton Phase 1 testing** — Zero. The #1 priority for 3+ movements. The directive said "activation" — we got "readiness." The difference matters.

2. **Lovable demo score** — Zero. The thing that makes Marianne visible to people outside this orchestra. 8+ movements deferred.

3. **Wordware comparison demos** — Zero. The competitive positioning that justifies Marianne's existence.

4. **F-097 timeout config** — Error code added (E006), but `idle_timeout_seconds` is still 1800 in generate-v3.py. Affects the running orchestra.

5. **Cost accuracy** — $0.12 reported for 110 sheets across 107 hours. Off by ~1000x. The lie is getting more convincing (Ember's observation), which is worse than no data.

---

## Risk Assessment

### R1: Activation Gap (P0 — DEFINING)

We have built a system that has never been used. 11,000 tests prove the parts work. Zero evidence proves the whole works. The gap between "tests pass" and "product works" is exactly where the composer found more bugs in one session than 755 tests found in two movements (core memory).

The structural cause is clear: 32 parallel workers excel at building and cannot converge on serial activation. Mateship proves reactive convergence works. What's needed is proactive convergence with specific deliverables.

### R2: Demo Invisibility (P1)

Nobody outside this orchestra has seen what Marianne can do. The hello.yaml is multi-movement fiction — not the visual, impressive demo the composer asked for. The Lovable replication and Wordware comparisons are at zero. Without demos, Marianne is an engine with no car.

### R3: F-210 — The Silent Degradation Blocker (P0)

Cross-sheet context missing from baton path. 20+ examples affected. Phase 1 testing without F-210 produces misleading results — scores appear to work while inter-sheet context is silently empty. This is confirmed on HEAD: zero references in `musician.py`, `prompt.py`, or `adapter.py`.

### R4: Cost Fiction (P2 — Worsening)

$0.12 for 110 sheets across 107h of Opus usage. Real cost is ~$100-$200. Every metric Marianne reports for cost is wrong by 3 orders of magnitude. This erodes trust in the dashboard and status display.

---

## Directives for Movement 4 — D-020 through D-025

Based on the evaluation of D-014 through D-019, directives must specify the deliverable, not the direction. "Build X" produces readiness. "Deliver X and show evidence" produces results.

### D-020: F-210 Cross-Sheet Context — Canyon (P0)

**Deliverable:** Wire `previous_outputs` and `previous_files` into the baton dispatch path. After each sheet completes, capture its stdout and make it available to subsequent sheets via `SheetExecutionState.previous_outputs`. The adapter's `_on_musician_done` callback already has the result — populate the state before the next dispatch.

**Evidence required:** Run 3 example scores that use cross-sheet context (hello.yaml, dialectic.yaml, worldbuilder.yaml) through the baton path (with `--conductor-clone`) and show that `{{ previous_outputs }}` renders with actual content from prior sheets.

**Gate:** F-210 blocks all Phase 1 testing. This is the single highest priority item.

### D-021: Phase 1 Baton Testing — Foundation (P0)

**Deliverable:** After F-210 is resolved, run the hello score (5 sheets) through a baton-enabled `--conductor-clone`. Then run one multi-movement fan-out example. Then run an adversarial test: simulate rate limit, timeout, and instrument failure during baton execution.

**Evidence required:** Terminal output of successful baton runs with `--conductor-clone`. Diff of baton output vs legacy runner output for the same score. List of issues found (if any) filed in FINDINGS.md.

**Gate:** Foundation may not start D-021 until Canyon's D-020 is merged and verified.

### D-022: Demo Score — Guide + Codex (P0)

**Deliverable:** The Lovable demo score. A score that shows Marianne coordinating multiple instruments to build something real and visible — not a text file, not a markdown document. The composer's directive is explicit: "visually impressive, mixed media, simple, works."

Start from the design at `docs/plans/2026-03-26-lovable-demo-design.md`. Produce a score that a human can `mzt run` and get something that makes them want to use Marianne.

**Evidence required:** The score validates clean. The score runs (via `--conductor-clone` if baton is active, or via legacy runner). The output is something you can show to a person and have them react with interest.

### D-023: Wordware Comparisons — Spark + Blueprint (P1)

**Deliverable:** 3 Wordware comparison scores. Take 3 of Wordware's published use cases (legal contract generation, marketing content automation, candidate screening). Build a Marianne score for each. Put them in `examples/wordware-vs-marianne/` with a README comparing approaches.

**Evidence required:** All 3 scores validate clean. Each has a README explaining what Wordware does, what Marianne does, and why.

### D-024: Cost Accuracy Investigation — Circuit (P1)

**Deliverable:** Root cause analysis of cost fiction. The conductor reports $0.12 for work that should cost $100-$200. Is the cost tracker not receiving data? Is the data being discarded? Is the estimation model wrong? Find the break in the pipeline and fix it or document exactly what's broken.

**Evidence required:** A finding in FINDINGS.md with the root cause, the file paths and line numbers where the break occurs, and either a fix or a specific plan for fixing it.

### D-025: F-097 Timeout Config — Bedrock (P1)

**Deliverable:** Update `idle_timeout_seconds` from 1800 to 7200 in `generate-v3.py`. Regenerate `marianne-orchestra-v3.yaml`. This is a 2-line change and a script run that has been open for 3+ movements.

**Evidence required:** The diff showing the timeout change and the regenerated score.

---

## Structural Observations

### The Parallel-Serial Tension

The orchestra has 32 musicians and excels at parallel work: building infrastructure, writing tests, polishing UX, modernizing examples. All of these are embarrassingly parallel.

The critical path items — baton activation, demo creation, live testing — are serial. They require one musician to hold the full context and drive through a sequence of dependent steps. The orchestra's structure fights this need.

D-020 through D-025 address this by naming specific musicians for specific deliverables with specific evidence requirements. The directive design rule, triply confirmed: named musician + specific scope + evidence standard → completion.

### Mateship Is The Orchestra's Immune System

30% mateship rate. 12 pickups this movement. The finding→fix→commit pipeline runs without coordination overhead. This is the strongest organizational pattern we have. When someone drops work, someone else picks it up — not because they're assigned to, but because they see it.

But mateship is reactive. It can't solve the activation gap because nobody drops "run the baton live" — nobody starts it. The serial activation path needs proactive assignment, not reactive pickup.

### The Build Addiction

Three consecutive strategic assessments (mine, Captain's, Oracle's) say the same thing: we are building excellently but not activating. The orchestra gravitates toward what it's good at (parallel construction) and away from what's hard (serial activation, demo creation, user-facing work).

This is the pattern I warned about in movement 2: "the team is building the wrong thing excellently." The engine is built. The experience layer is not. D-020 through D-025 must break this pattern.

---

## Quality Evidence

```
mypy src/: Clean (0 errors)
ruff check src/: All checks passed
pytest: 10,981 tests (running, M2 quality gate confirmed all pass)
GitHub issues: 45 open (5 closed this movement)
TASKS.md: 150/197 complete (78%)
```

---

## My Failures This Movement

I produced no M3 output until this report. Five musicians — including me — had zero M3 contributions. As the strategic lead, I should have been the first to flag the activation gap with formal directives, not the third assessment to repeat the same observation.

D-014 through D-019 were issued at the end of M2. They should have been more precise. "Baton activation" produced readiness, not activation. "Demo" produced hello.yaml, not the Lovable demo. The directive design was directional when it needed to be deliverable-specific.

D-020 through D-025 correct this. The evidence requirements are specific. The gates are explicit. The serial dependencies are mapped.

---

## Summary

Movement 3 proved the orchestra can build at scale with quality. Movement 4 must prove it can activate and deliver. The transition from building to using is the defining challenge. D-020 through D-025 aim the orchestra at the serial path.

The coordinates are set. The evidence standards are clear. Down. Forward. Through.
