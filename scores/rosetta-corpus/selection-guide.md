# Pattern Selection Guide

This guide helps you choose patterns based on **what you're trying to accomplish**, not what the patterns are called. Each problem type includes 2-3 pattern combinations that work well together.

---

## Getting Started: Five Essential Patterns

If you're new to orchestration, start with these five patterns in order. They cover the most common coordination problems and compose naturally:

| Pattern | Problem It Solves | When To Use It |
|---------|-------------------|----------------|
| **Fan-out + Synthesis** | Work could be parallelized but isn't, or parallel outputs remain fragmented | Problem decomposes into independent sub-problems; need to integrate diverse perspectives |
| **Shipyard Sequence** | Expensive work proceeds on a broken foundation, wasting resources | Downstream work is expensive; foundation must be solid before scaling |
| **The Tool Chain** | AI instruments waste budget on deterministic tasks that CLI tools could handle | Most pipeline stages are deterministic transformations; costs are high using AI for every step |
| **Canary Probe** | Full-scale execution risks loss when pipeline changes are unproven | Batch processing with unproven pipeline; high cost of full-scale failure |
| **Andon Cord** | Validation failures retry blindly without diagnosing root cause | Validation failures repeat the same error; failure output is informative but gets ignored |

**Why this order?** Fan-out + Synthesis teaches parallelization. Shipyard Sequence shows validation before scaling. The Tool Chain demonstrates instrument selection. Canary Probe introduces incremental commitment. Andon Cord closes the loop with intelligent failure handling.

---

## Problem Types and Pattern Compositions

### Parallelize Work Without Drift

**Problem:** Multiple agents working in parallel produce inconsistent outputs because each makes independent choices.

**Compositions:**
- **Fan-out + Synthesis + Barn Raising** — Parallelize work, establish shared conventions first, then integrate outputs
- **Prefabrication + Mission Command** — Define interface contracts upfront, then execute with outcome validation
- **Fan-out + Synthesis + Triage Gate** — Parallelize, then filter mixed-quality outputs before synthesis

**Signals:** Parallel agents will work on similar artifacts; consistency in naming/structure matters for integration; synthesis must address cross-cutting themes

---

### Optimize Costs

**Problem:** Work items vary in complexity but you're using expensive instruments for everything.

**Compositions:**
- **Echelon Repair + Screening Cascade + Commissioning Cascade** — Classify upfront, route to appropriate tiers, validate each tier's output
- **Fermentation Relay + Succession Pipeline** — Use cheap instruments for initial extraction, escalate through cost-graduated refinement
- **The Tool Chain + Echelon Repair** — Route deterministic work to CLI tools, use AI only for complex items

**Signals:** Work items vary wildly in complexity; costs are high but most work is simple; expensive instrument wasted on trivial tasks

---

### Validate Before Scaling

**Problem:** Expensive downstream work proceeds without validating that the foundation or pipeline works correctly.

**Compositions:**
- **Shipyard Sequence + Commissioning Cascade** — Validate foundation with scope-appropriate instruments before fan-out
- **Canary Probe + Progressive Rollout** — Test on small batch first, then scale incrementally with monitoring
- **Triage Gate + Immune Cascade** — Filter broad findings before expensive deep analysis

**Signals:** Downstream fan-out is expensive; foundation must be solid before scaling work; need real validation tools, not LLM judgment

---

### Analyze from Multiple Angles

**Problem:** Single-perspective analysis produces unreliable conclusions when the optimal analytical frame is unknown.

**Compositions:**
- **Rashomon Gate + Sugya Weave + Source Triangulation** — Apply multiple frames, synthesize with editorial judgment, verify across independent sources
- **Fan-out + Synthesis + Talmudic Page** — Parallelize analysis, produce multi-layer annotations that reference each other
- **Red Team / Blue Team + After-Action Review** — Adversarial testing with systematic learning capture

**Signals:** Multiple valid perspectives exist; risk is getting the right answer from the wrong frame; need argued position, not neutral aggregation

---

### Handle Partial Failures Gracefully

**Problem:** Some work items consistently fail, but retries waste resources without identifying root causes.

**Compositions:**
- **Dead Letter Quarantine + Triage Gate + Circuit Breaker** — Quarantine chronic failures, classify outputs by quality, halt escalation when failures spike
- **Andon Cord + Commissioning Cascade** — Diagnose root cause from failure output, validate with tier-appropriate instruments
- **Quorum Consensus + Triage Gate** — Proceed with majority agreement, filter mixed-quality outputs

**Signals:** Some items consistently fail across retries; batch processing has persistent partial failures; retry loops waste resources

---

### Learn from Failures

**Problem:** Same mistakes happen repeatedly because execution insights aren't captured or propagated.

**Compositions:**
- **CDCL Search + After-Action Review + Back-Slopping** — Extract constraints from failures, capture methodology improvements, inherit learning across iterations
- **Systemic Acquired Resistance + Circuit Breaker** — Propagate failure patterns across concert, halt when patterns recur
- **Andon Cord + CDCL Search** — Diagnose root cause, convert to constraint to prevent recurrence

**Signals:** Same failures occur across retry attempts; retries don't help because nothing is learned; later iterations repeat mistakes from earlier ones

---

### Iterate Toward a Goal

**Problem:** Large artifacts require iterative construction, but iterations are expensive and convergence is unclear.

**Compositions:**
- **Cathedral Construction + Memoization Cache + Back-Slopping** — Build incrementally toward target, cache unchanged regions, inherit learning
- **Fixed-Point Iteration + Soil Maturity Index** — Refine repeatedly, measure domain-specific convergence readiness
- **CEGAR Loop + Memoization Cache** — Progressively refine abstraction, skip re-analysis of unchanged regions

**Signals:** Artifact too large to complete in one pass; iterations are expensive and need measurable termination; each iteration adds structural elements

---

### Coordinate Without Bottlenecks

**Problem:** Centralized coordination creates bottlenecks, but decentralized work produces inconsistency.

**Compositions:**
- **Mission Command + Commander's Intent Envelope + After-Action Review** — Define intent envelope, execute autonomously, capture decision logs for learning
- **Stigmergic Workspace + Barn Raising** — Coordinate through workspace state, establish shared conventions
- **Lines of Effort + Season Bible** — Parallel workstreams with autonomy, shared memory of decisions and constraints

**Signals:** Tasks require agent judgment; validation should check outcomes not methods; multiple agents must coordinate around shared intent

---

### Manage Large Contexts

**Problem:** Pipeline outputs exceed context window limits, degrading downstream agent performance.

**Compositions:**
- **Relay Zone + Forward Observer + Screening Cascade** — Compress handoffs, use cheap summarization upfront, pre-filter before expensive processing
- **Immune Cascade + Relay Zone** — Narrow scope with cheap scanning, compress findings before triage
- **Forward Observer + The Tool Chain** — Summarize raw input cheaply, route deterministic work to CLI tools

**Signals:** Input exceeds context window; later stages receive more context than they can effectively use; token costs dominate total cost

---

### Test Unknown Approaches

**Problem:** Uncertain which approach will work, but starting over after failure is expensive.

**Compositions:**
- **Canary Probe + Progressive Rollout + Dead Letter Quarantine** — Test small batch, scale incrementally, quarantine persistent failures
- **Speculative Hedge + Vickrey Auction** — Run candidate approaches in parallel, select winner based on evidence
- **Reconnaissance Pull + Mission Command** — Explore landscape first, then execute with intent envelope

**Signals:** Uncertain which approach works; starting over costs more than running both; need validated evidence before full commitment

---

### Build Incrementally with Quality Gates

**Problem:** Work requires sequential transformations, but unstructured execution produces outputs incompatible with downstream stages.

**Compositions:**
- **Succession Pipeline + Shipyard Sequence + Barn Raising** — Sequential substrate transformations, validate each stage, establish shared conventions
- **Composting Cascade + The Tool Chain + Succession Pipeline** — Measure readiness signals, chain CLI tools with AI stages, progress through categorical transformations
- **Closed-Loop Call + Relay Zone** — Verify semantic fidelity at handoffs, compress cumulative outputs

**Signals:** Each stage needs fundamentally different methods; one stage's output becomes next stage's input substrate; stages have categorical differences

---

### Detect Quality Issues Early

**Problem:** Finding problems late costs exponentially more than catching them early in the pipeline.

**Compositions:**
- **Shipyard Sequence + Succession Pipeline + Immune Cascade** — Validate foundation before fan-out, progress through quality gates, narrow findings cheaply
- **Commissioning Cascade + Echelon Repair + The Tool Chain** — Different validation scopes with appropriate instruments, tier-matched validation, validate CLI tool outputs
- **Triage Gate + Fan-out + Synthesis** — Filter mixed-quality outputs before synthesis, prevent garbage from reaching expensive stages

**Signals:** Unit tests pass but integration fails; downstream fan-out is expensive; need to narrow findings before expensive deep analysis

---

### Recover from Failures Intelligently

**Problem:** Failures should inform strategy, not just trigger blind retries.

**Compositions:**
- **Andon Cord + Circuit Breaker + Dead Letter Quarantine** — Diagnose root cause, halt when instrument fails, quarantine unfixable items
- **CDCL Search + Systemic Acquired Resistance** — Extract failure patterns as constraints, propagate across concert
- **Graceful Retreat + Cathedral Construction** — Accept partial completion tiers, preserve progress across iterations

**Signals:** Validation failures repeat same error; instrument becomes unavailable mid-execution; need to proceed with partial results

---

### Converge Diverse Opinions

**Problem:** Multiple independent agents produce varied assessments that need to converge without anchoring on early opinions.

**Compositions:**
- **Delphi Convergence + Source Triangulation + Rashomon Gate** — Iterate toward consensus without early anchoring, verify across independent sources, apply multiple analytical frames
- **Quorum Consensus + Fan-out + Synthesis** — Proceed with majority agreement, integrate diverse perspectives
- **Sugya Weave + Talmudic Page** — Editorial synthesis with argued positions, multi-layer annotation with cross-references

**Signals:** Expert opinions vary widely and need to converge; single-round synthesis isn't achieving consensus; agents anchor on initial assessments

---

### Adapt Plans Mid-Execution

**Problem:** Plans become stale when discovered conditions diverge from expectations, but full replanning is wasteful.

**Compositions:**
- **Fragmentary Order (FRAGO) + Read-and-React + Mission Command** — Issue targeted corrections, inspect workspace state and adapt, preserve intent envelope while adjusting methods
- **Quorum Trigger + Andon Cord** — Self-interrupt based on evidence density, diagnose before continuing
- **Dormancy Gate + Read-and-React** — Pause until prerequisites available, re-evaluate dynamically

**Signals:** Earlier stages invalidate downstream assumptions; plan is partially wrong but not wrong enough to discard; conditions discovered mid-execution weren't anticipated

---

## How to Use This Guide

1. **Start with your problem, not a pattern name.** Find the problem type that matches your situation.
2. **Read the signals.** Do they describe what you're experiencing?
3. **Try the first composition.** The compositions are ordered by frequency of use — start with the first one.
4. **Read the individual pattern files.** This guide tells you WHEN and WHICH; the pattern files tell you HOW.
5. **Compose incrementally.** You don't need to use all patterns in a composition at once. Start with one, add the next when needed.

---

## Composition Principles

**What makes patterns compose well together?**

- **Shared workspace as integration point** — Patterns coordinate through files, not message passing
- **Complementary forces** — One pattern creates structure another pattern exploits (e.g., Barn Raising establishes conventions that Mission Command agents follow)
- **Sequential or parallel relationship** — One pattern's output feeds another's input (sequential) OR patterns work on independent aspects simultaneously (parallel)
- **Escalation or filtering** — One pattern narrows scope/complexity, another handles what passes through (e.g., Screening Cascade → Echelon Repair)

**What breaks composition?**

- **Conflicting assumptions** — One pattern assumes deterministic tools, another assumes AI judgment on the same stage
- **Circular dependencies** — Pattern A needs Pattern B's output, Pattern B needs Pattern A's output
- **Redundant work** — Both patterns do the same classification/filtering in different ways
- **Wrong scale** — Trying to compose a within-stage prompt technique with a concert-level pattern

---

## Quick Reference: Pattern by Scale

| Scale | What It Controls | Example Patterns |
|-------|------------------|------------------|
| **Within-Stage** | Single sheet's prompt content or behavior | Commander's Intent Envelope, Decision Propagation, Quorum Trigger |
| **Score-Level** | Multiple sheets within a single score | Fan-out + Synthesis, Shipyard Sequence, Canary Probe, Barn Raising, Mission Command |
| **Concert-Level** | Multiple scores in a campaign | Lines of Effort, Progressive Rollout, Season Bible, Saga Compensation Chain |
| **Communication** | Coordination through workspace state | Stigmergic Workspace |
| **Adaptation** | Adjust behavior mid-execution based on runtime conditions | Andon Cord, Circuit Breaker, Read-and-React, FRAGO |
| **Instrument-Strategy** | Match instrument capabilities to task requirements | Echelon Repair, The Tool Chain, Commissioning Cascade, Screening Cascade |
| **Iteration** | Repeated refinement and learning across execution cycles | CDCL Search, Cathedral Construction, After-Action Review, Memoization Cache |

---

## For `marianne compose`

When analyzing a user's goal description, match against these semantic categories:

- **"analyze from multiple perspectives"** → Multi-Frame Analysis patterns
- **"run in parallel"** OR **"parallelize"** → Parallel Work Without Drift patterns
- **"reduce costs"** OR **"optimize budget"** → Optimize Costs patterns
- **"validate before"** OR **"test first"** → Validate Before Scaling patterns
- **"learn from failures"** OR **"stop repeating mistakes"** → Learn from Failures patterns
- **"iterate"** OR **"refine"** → Iterate Toward a Goal patterns
- **"coordinate"** OR **"no bottlenecks"** → Coordinate Without Bottlenecks patterns
- **"too much context"** OR **"context overflow"** → Manage Large Contexts patterns
- **"not sure which approach"** OR **"try different ways"** → Test Unknown Approaches patterns
- **"sequential stages"** OR **"pipeline"** → Build Incrementally with Quality Gates patterns
- **"catch issues early"** → Detect Quality Issues Early patterns
- **"recover from"** OR **"handle failures"** → Recover from Failures Intelligently patterns
- **"converge"** OR **"reach consensus"** → Converge Diverse Opinions patterns
- **"adapt"** OR **"conditions change"** → Adapt Plans Mid-Execution patterns

The first composition in each problem type is the most commonly used. Always recommend reading the individual pattern files before implementation.
