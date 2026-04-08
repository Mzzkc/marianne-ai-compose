# Rosetta Pattern Corpus — Index

This index is what agents read **first** when selecting patterns for a goal. It conveys **when** and **why** to use each pattern, not how. Each entry shows:

- **Name** and scale
- **Problem** — the coordination problem this pattern solves
- **Signals** — symptoms that indicate this pattern applies
- **Key compositions** — patterns frequently used together

Patterns are organized by coordination scale. For detailed implementation guidance, read the full pattern file.

---

## Foundational

_Foundational patterns operate at the lowest level of orchestration structure._

Currently no patterns in this scale.

---

## Within-Stage

_Patterns that structure a single sheet's prompt content or internal logic._

**Commander's Intent Envelope** (within-stage)
- **Problem:** Instruction-based prompts break when the agent encounters conditions the prompt author didn't anticipate.
- **Signals:** task has more than one valid approach; inputs are variable-format or unpredictable; different instruments would solve this differently
- **Composes with:** Mission Command, Fan-out + Synthesis, After-Action Review

**Constraint Propagation Sweep** (within-stage)
- **Problem:** Agents generate from contradictory specifications because constraint conflicts remain hidden until expensive work is already complete.
- **Signals:** specifications from different stakeholders contain implicit contradictions; generated outputs fail because requirements conflicted silently; reconciling heterogeneous inputs costs less than reworking outputs
- **Composes with:** Decision Propagation, CDCL Search, Rashomon Gate

**Decision Propagation** (within-stage)
- **Problem:** Downstream agents contradict upstream decisions because constraints are buried in prose rather than structured, parseable briefs.
- **Signals:** early decisions have compounding effects on later stages; downstream agents unknowingly violate upstream constraints; decisions are buried in prose output rather than structured artifacts
- **Composes with:** CDCL Search, CEGAR Loop, Commander's Intent Envelope

**Quorum Trigger** (within-stage)
- **Problem:** Agents continue executing their original plan after accumulating evidence that makes continuing wasteful or dangerous.
- **Signals:** conditions discovered mid-task should change the approach; findings accumulate that individually seem minor but collectively demand action; agent needs to self-interrupt based on evidence density
- **Composes with:** Andon Cord, Circuit Breaker, Immune Cascade

**Sugya Weave (Editorial Synthesis)** (within-stage)
- **Problem:** Diverse inputs need synthesis into an authoritative position with argued support, not neutral aggregation.
- **Signals:** multiple perspectives exist but need editorial judgment; summary isn't sufficient — need a supported position; inputs are diverse and require interpretation
- **Composes with:** Fan-out + Synthesis, Source Triangulation, Rashomon Gate

---

## Score-Level

_Patterns that coordinate multiple sheets within a single score._

**Barn Raising** (score-level)
- **Problem:** Parallel work streams produce inconsistent structure and style when each agent makes independent convention choices.
- **Signals:** parallel agents will work on similar types of artifacts; consistency in naming, structure, or style matters for integration; each agent might make reasonable but incompatible choices
- **Composes with:** Prefabrication, Mission Command, Lines of Effort

**Canary Probe** (score-level)
- **Problem:** Full-scale execution risks loss of resources and time when pipeline changes or output formats are unproven.
- **Signals:** batch processing many items with unproven pipeline; pipeline changes with uncertain format impact; high cost of full-scale failure
- **Composes with:** Progressive Rollout, Dead Letter Quarantine, Speculative Hedge

**Clash Detection** (score-level)
- **Problem:** Parallel tracks produce conflicting artifacts that break integration, and discovering conflicts during integration is expensive.
- **Signals:** parallel work needs to integrate but conflicts are unpredictable; integration testing is expensive; contracts can't anticipate all conflict modes
- **Composes with:** Prefabrication, Andon Cord, The Tool Chain

**Closed-Loop Call** (score-level)
- **Problem:** Semantic drift across pipeline stages when consumers misunderstand producer outputs.
- **Signals:** handoff fidelity is critical; semantic drift is a real risk; stages have non-obvious dependencies
- **Composes with:** Prefabrication, Relay Zone, Succession Pipeline

**Composting Cascade** (score-level)
- **Problem:** Phase transitions in iterative work need measurable readiness signals rather than time-based or manual progression decisions.
- **Signals:** phase transitions are time-based or manual, not metrics-driven; unclear when simple work is complete and should escalate to complex restructuring; churn rates don't drive phase changes
- **Composes with:** The Tool Chain, Succession Pipeline, Echelon Repair

**Dead Letter Quarantine** (score-level)
- **Problem:** Batch processing repeatedly fails on the same items because no systematic analysis identifies root causes or adapts strategy.
- **Signals:** some items consistently fail across retries; batch processing has persistent partial failures; retry loops waste resources on unfixable items
- **Composes with:** Triage Gate, Screening Cascade, Circuit Breaker, Immune Cascade

**Dormancy Gate** (score-level)
- **Problem:** External prerequisites are not immediately available, but work cannot safely proceed without them.
- **Signals:** downstream work depends on external system state; prerequisites will eventually be satisfied but are not immediate; need to wait and retry, not fail outright
- **Composes with:** Read-and-React, Shipyard Sequence

**Fan-out + Synthesis** (score-level)
- **Problem:** Work that could be parallelized is done sequentially, wasting time, or parallel outputs remain fragmented without meaningful integration.
- **Signals:** problem decomposes into independent sub-problems; sub-problems can be worked on simultaneously; need to integrate diverse perspectives or findings
- **Composes with:** Barn Raising, Shipyard Sequence, After-Action Review, Triage Gate

**Graceful Retreat** (score-level)
- **Problem:** Long-running work risks total failure on hard deadlines unless tiers of acceptable output are planned in advance.
- **Signals:** work has hard time deadlines where partial output has value; downstream pipeline stages can adapt to variable completeness; attempting full completion might waste resources or miss deadlines
- **Composes with:** Andon Cord, Dead Letter Quarantine, Cathedral Construction

**Mission Command** (score-level)
- **Problem:** Centralized instruction-following breaks when agents face conditions the planner didn't anticipate.
- **Signals:** tasks require agent judgment and conditions may vary; validation should check outcomes, not methods; multiple agents must coordinate around shared intent
- **Composes with:** After-Action Review, Barn Raising, Prefabrication

**Nurse Log** (score-level)
- **Problem:** Downstream stages waste resources redoing common preparation work because no shared substrate exists.
- **Signals:** multiple stages need the same research or data collection; agents are duplicating preparation work; downstream work is blocked waiting for common prerequisites
- **Composes with:** Fermentation Relay, Fan-out + Synthesis

**Prefabrication** (score-level)
- **Problem:** Parallel tracks produce incompatible outputs because no shared interface contract exists before work begins.
- **Signals:** parallel work must produce compatible outputs; integration fails due to interface mismatches; tracks can't communicate during development
- **Composes with:** Barn Raising, Clash Detection, Mission Command

**Quorum Consensus** (score-level)
- **Problem:** Partial agent failure should not block the pipeline when majority agreement is sufficient.
- **Signals:** fan-out agents may fail unpredictably; partial failure shouldn't block downstream stages; need to proceed with majority agreement
- **Composes with:** Triage Gate, Source Triangulation, Fan-out + Synthesis

**Rashomon Gate** (score-level)
- **Problem:** Single-frame analysis produces unreliable conclusions when the optimal analytical perspective is unknown.
- **Signals:** the right analytical frame is unknown; multiple valid perspectives exist (security, performance, maintainability); risk is getting the right answer from the wrong frame
- **Composes with:** Source Triangulation, Sugya Weave, Commander's Intent Envelope

**Reconnaissance Pull** (score-level)
- **Problem:** Planning without prior exploration risks misaligned approaches and wasted effort.
- **Signals:** task structure and complexity are unclear; initial exploration costs are low relative to execution; approach is not obvious from requirements alone
- **Composes with:** Mission Command, Canary Probe

**Red Team / Blue Team** (score-level)
- **Problem:** Artifacts tested by known adversaries pass trivially; unknown adversaries reveal real flaws.
- **Signals:** testing is too predictable when defenders know the attacks; need to find vulnerabilities that prepared defense would miss; want realistic stress-testing where defenders work blind
- **Composes with:** After-Action Review, Immune Cascade

**Relay Zone** (score-level)
- **Problem:** Cumulative outputs across pipeline stages exceed context window limits, degrading downstream agent performance.
- **Signals:** pipeline outputs growing too large for downstream context windows; later stages receiving more context than they can effectively use; information from early stages drowning out recent findings
- **Composes with:** Fan-out + Synthesis, Forward Observer, Screening Cascade

**Shipyard Sequence** (score-level)
- **Problem:** Expensive fan-out proceeds on a broken foundation, wasting resources on downstream work that will fail.
- **Signals:** downstream fan-out is expensive; foundation must be solid before scaling work; need real validation tools, not LLM judgment
- **Composes with:** Succession Pipeline, Dormancy Gate, Triage Gate

**Source Triangulation** (score-level)
- **Problem:** Single-source analysis cannot detect contradictions between what code does, documentation says, and tests prove.
- **Signals:** technical claims need independent verification; multiple source types exist (code, docs, tests, benchmarks); single perspective might miss contradictions
- **Composes with:** Rashomon Gate, Triage Gate, Sugya Weave

**Speculative Hedge** (score-level)
- **Problem:** Choosing one approach that fails requires expensive restart from scratch, wasting the initial attempt's cost.
- **Signals:** uncertain which approach will work for this problem; starting over after failed approach costs more than running both; need guaranteed progress despite approach uncertainty
- **Composes with:** Wargame Table, Canary Probe

**Succession Pipeline** (score-level)
- **Problem:** Work requires sequential substrate transformations, but unstructured execution produces outputs incompatible with downstream stages.
- **Signals:** each stage needs fundamentally different methods; one stage's output becomes the next stage's input substrate; stages have categorical differences, not just detail levels
- **Composes with:** Shipyard Sequence, Barn Raising

**Talmudic Page** (score-level)
- **Problem:** Multiple perspectives on an artifact produce disconnected analyses when commentaries reference only the source, not each other.
- **Signals:** primary artifact needs multi-layer annotation; analysis requires multiple perspectives anchored to one text; commentaries should reference both source and each other
- **Composes with:** Sugya Weave, Fan-out + Synthesis

**Triage Gate** (score-level)
- **Problem:** Fan-out produces mixed-quality outputs but synthesis processes all outputs regardless of quality, wasting resources.
- **Signals:** fan-out produces wildly varying output quality; synthesis stage is expensive and shouldn't process garbage; some outputs need rework, others are ready
- **Composes with:** Immune Cascade, Fan-out + Synthesis, Relay Zone

---

## Concert-Level

_Patterns that coordinate multiple scores in a concert (chained execution)._

**Lines of Effort** (concert-level)
- **Problem:** Parallel campaign workstreams drift apart without convergence mechanisms connecting distinct efforts toward a unified end state.
- **Signals:** campaign has distinct workstreams with different objectives; parallel efforts must converge toward a shared end state; workstreams need autonomy but unified direction
- **Composes with:** Season Bible, After-Action Review, Barn Raising

**Progressive Rollout** (concert-level)
- **Problem:** Full deployment before validation risks large-scale failure; incremental rollout with monitoring gates progression but requires coordinating batch selection, execution, and go/no-go decisions across phases.
- **Signals:** works on 5 doesn't guarantee works on 500; need to detect scaling issues before full deployment; rollback from 100% deployment is expensive
- **Composes with:** Canary Probe, Dead Letter Quarantine

**Saga Compensation Chain** (concert-level)
- **Problem:** Partial completion of a multi-score concert leaves inconsistent shared state with no automated path to undo forward steps.
- **Signals:** concert scores produce side effects on shared state; partial completion is worse than full rollback; manual cleanup after failure is expensive and error-prone
- **Composes with:** After-Action Review

**Season Bible** (concert-level)
- **Problem:** Multi-score campaigns lose continuity because agents lack shared memory of prior decisions and evolving constraints.
- **Signals:** scores make decisions inconsistent with earlier work; agents repeat mistakes or ignore prior learnings; no central record of evolving state across campaign
- **Composes with:** Lines of Effort, Relay Zone, Cathedral Construction

**Systemic Acquired Resistance** (concert-level)
- **Problem:** Failures encountered in one score don't inform subsequent scores in a concert, causing repeated failures across the campaign.
- **Signals:** scores in a concert face similar threats; first-encounter failure cost is high; failures repeat across scores in a concert
- **Composes with:** After-Action Review, Back-Slopping, Circuit Breaker

---

## Communication

_Patterns for agent-to-agent information transfer and coordination._

**Stigmergic Workspace** (communication)
- **Problem:** Parallel agents duplicate effort or produce conflicts because they lack visibility into each other's progress and decisions.
- **Signals:** parallel agents need loose coordination without direct messaging; workspace files already capture meaningful state other agents need; real-time coordination would create bottlenecks
- **Composes with:** Barn Raising, Lines of Effort

---

## Adaptation

_Patterns that handle runtime changes, failures, or unexpected conditions._

**Andon Cord** (adaptation)
- **Problem:** Validation failures are retried blindly without diagnosing root cause, wasting resources on repeated errors.
- **Signals:** validation failures repeat the same error across retries; failure output is informative but gets ignored; retry costs are high (~$1+ per attempt)
- **Composes with:** Circuit Breaker, Quorum Trigger, Commissioning Cascade

**Circuit Breaker** (adaptation)
- **Problem:** Long-running jobs fail catastrophically or waste resources when instruments become unavailable mid-execution.
- **Signals:** backend outages cause sudden job failures; primary instrument becomes unavailable mid-concert; self-chaining jobs lose progress when instruments fail
- **Composes with:** Dead Letter Quarantine, Echelon Repair, Speculative Hedge

**Fragmentary Order (FRAGO)** (adaptation)
- **Problem:** Plans become stale mid-execution when discovered conditions diverge from expectations but no mechanism exists for targeted correction without full replanning.
- **Signals:** earlier stages produced results that invalidate downstream assumptions; the plan is partially wrong but not wrong enough to discard; downstream agents need adjusted guidance, not a completely new plan
- **Composes with:** Read-and-React, Lines of Effort, Mission Command

**Read-and-React** (adaptation)
- **Problem:** Downstream agents follow fixed behavior regardless of upstream results because their prompts don't instruct them to inspect and adapt to workspace state.
- **Signals:** downstream behavior should change based on upstream results; adaptation path is not known before execution begins; workspace state determines which work is needed next
- **Composes with:** Triage Gate, Fragmentary Order, Dormancy Gate

---

## Instrument-Strategy

_Patterns for selecting, allocating, and switching between AI instruments._

**Commissioning Cascade** (instrument-strategy)
- **Problem:** Different validation scopes require different tools; single-pass validation misses issues or wastes resources.
- **Signals:** unit tests pass but integration fails; validation is slow because all scopes use expensive instruments; can't diagnose failures because all tests run together
- **Composes with:** Echelon Repair, Shipyard Sequence, The Tool Chain

**Echelon Repair** (instrument-strategy)
- **Problem:** Expensive instruments waste resources on work that cheaper instruments could handle.
- **Signals:** work items vary wildly in difficulty; expensive instrument is wasted on trivial tasks; costs are high but most work is simple
- **Composes with:** Commissioning Cascade, Fermentation Relay, Screening Cascade, Circuit Breaker

**Fermentation Relay** (instrument-strategy)
- **Problem:** Expensive instruments waste resources fixing quality issues that cheap instruments created during initial processing.
- **Signals:** cheap instruments produce output too noisy for expensive stages to use directly; expensive instruments waste budget on noise filtering instead of core work; early outputs require multiple refinement steps before quality is acceptable
- **Composes with:** Echelon Repair, Succession Pipeline, Screening Cascade

**Forward Observer** (instrument-strategy)
- **Problem:** Expensive instruments waste resources reading raw input; cheap summarization can preserve actionable information.
- **Signals:** input exceeds available context window; expensive instrument required for main task; token costs dominate total cost
- **Composes with:** Relay Zone, Screening Cascade, Immune Cascade

**Screening Cascade** (instrument-strategy)
- **Problem:** Difficulty emerges during processing; fixed upfront instruments waste expensive resources on simple work or fail on complex work.
- **Signals:** work items vary in difficulty but this only becomes clear during processing; cheap instruments can screen routine items but some need escalation; costs are high using expensive instruments for work that doesn't warrant them
- **Composes with:** Echelon Repair, Immune Cascade, Dead Letter Quarantine

**The Tool Chain** (instrument-strategy)
- **Problem:** Expensive AI instruments waste budget on deterministic tasks that CLI tools could handle more cheaply.
- **Signals:** most pipeline stages are deterministic transformations; costs are high using AI instruments for every step; work is expressible as shell commands with exit codes
- **Composes with:** Echelon Repair, Commissioning Cascade, Composting Cascade

**Vickrey Auction** (instrument-strategy)
- **Problem:** Selecting an instrument without evidence wastes resources or produces inferior results when multiple candidates are viable.
- **Signals:** multiple instruments are available and it's unclear which performs best; instrument choice is based on guesswork, not evidence; cost or quality varies significantly across instruments
- **Composes with:** Echelon Repair, Canary Probe

---

## Iteration

_Patterns for iterative processes, convergence, and learning across attempts._

**After-Action Review** (iteration)
- **Problem:** Execution insights are lost between iterations because no systematic reflection captures what worked, what failed, and why.
- **Signals:** same mistakes happen repeatedly across iterations; execution insights disappear after completion; teams don't know what actually worked or why
- **Composes with:** Immune Cascade, Cathedral Construction, Back-Slopping

**Back-Slopping (Learning Inheritance)** (iteration)
- **Problem:** Iterative processes lose hard-won insights because each iteration starts from scratch without accumulated learning.
- **Signals:** later iterations repeat mistakes from earlier ones; valuable insights discovered during work are lost between iterations; iterative process plateaus because it cannot build on prior discovery
- **Composes with:** Cathedral Construction, CDCL Search, Systemic Acquired Resistance

**Cathedral Construction** (iteration)
- **Problem:** Large artifacts cannot be produced in a single pass and require iterative construction toward a known target.
- **Signals:** artifact is too large to complete in one pass; work must be built incrementally toward a target; each iteration adds structural elements
- **Composes with:** After-Action Review, Back-Slopping, Memoization Cache

**CDCL Search** (iteration)
- **Problem:** Iterative processes repeat the same failures because no mechanism captures and propagates failure patterns as constraints.
- **Signals:** same failures occur across retry attempts; retries don't help because nothing is learned; failures contain diagnostic information that could prevent recurrence
- **Composes with:** Back-Slopping, After-Action Review, CEGAR Loop

**CEGAR Loop (Progressive Refinement)** (iteration)
- **Problem:** Coarse-grained analysis produces spurious findings requiring expensive verification to distinguish real from false alarms.
- **Signals:** coarse analysis produces too many false alarms; expensive to verify every finding at fine grain; most findings disappear when abstraction is refined
- **Composes with:** Memoization Cache, CDCL Search, Immune Cascade

**Delphi Convergence** (iteration)
- **Problem:** Multiple independent agents must converge without anchoring on early opinions.
- **Signals:** expert opinions vary widely and need to converge; agents anchor on initial assessments and won't update; single-round synthesis isn't achieving consensus
- **Composes with:** Source Triangulation, Rashomon Gate

**Fixed-Point Iteration** (iteration)
- **Problem:** Iterative refinement requires explicit convergence detection to avoid wasting iterations.
- **Signals:** repeated application produces improvements but stopping criterion is unclear; iterations are expensive and need measurable termination beyond fixed counts; output stabilizes after refinement
- **Composes with:** CDCL Search, Cathedral Construction, Memoization Cache

**Memoization Cache** (iteration)
- **Problem:** Self-chaining scores and iterative processes re-execute stages whose inputs haven't changed, wasting computation.
- **Signals:** self-chaining scores re-analyze unchanged modules wastefully; concert campaigns process overlapping inputs redundantly; CEGAR Loops re-examine stable abstraction regions unnecessarily
- **Composes with:** CEGAR Loop, Cathedral Construction, Fixed-Point Iteration

**Rehearsal Spotlight** (iteration)
- **Problem:** Iteration is expensive; reworking entire outputs wastes resources when only parts need refinement.
- **Signals:** iteration cycles are expensive; only specific sections need rework; most output is good but a few parts are weak
- **Composes with:** Echelon Repair, Soil Maturity Index, CEGAR Loop

**Soil Maturity Index** (iteration)
- **Problem:** Iterative processes lack domain-specific termination conditions beyond structural equality.
- **Signals:** iterative improvement plateaus on structural metrics but output lacks qualitative maturity; need to distinguish real convergence from mere structural stability; process converges structurally but hasn't achieved expected coherence
- **Composes with:** Fixed-Point Iteration, Back-Slopping, Delphi Convergence

---

## Composition Clusters

Patterns frequently compose together to solve related coordination problems. These clusters show common combinations and their purposes.

### Quality Gates & Filtering
Patterns that progressively filter, validate, and escalate work items:
- **Immune Cascade** (12 edges) — broad scanning with graduated depth
- **Triage Gate** (10 edges) — quality-based filtering after fan-out
- **Screening Cascade** (9 edges) — dynamic difficulty-based escalation
- **Commissioning Cascade** (6 edges) — scope-differentiated validation

**Purpose:** Avoid wasting expensive resources on low-value work. These patterns work together to classify, filter, and route work items to appropriate validation or processing tiers.

### Parallel Coordination
Patterns that enable effective parallel execution:
- **Fan-out + Synthesis** (12 edges) — parallel decomposition with integration
- **Barn Raising** (9 edges) — shared conventions across parallel agents
- **Mission Command** (8 edges) — intent-based decentralized execution
- **Prefabrication** (7 edges) — interface contracts before parallel development

**Purpose:** Coordinate parallel work without creating inconsistency. Establish shared conventions, intent envelopes, and interface contracts that enable autonomous parallel execution while ensuring integration succeeds.

### Cost Optimization
Patterns that reduce computational and token costs:
- **Echelon Repair** (12 edges) — graduated instrument assignment by difficulty
- **Forward Observer** (7 edges) — cheap summarization before expensive processing
- **Fermentation Relay** — iterative quality improvement across tiers
- **The Tool Chain** (6 edges) — deterministic CLI tools instead of AI

**Purpose:** Match instrument capability to task complexity. Route simple work to cheap instruments, reserve expensive capability for complex work, and use deterministic tools where possible.

### Iteration Management
Patterns that handle iterative processes and convergence:
- **After-Action Review** (14 edges) — extract lessons from execution
- **Cathedral Construction** (9 edges) — incremental construction toward known target
- **CDCL Search** (8 edges) — failure pattern capture and constraint propagation
- **Back-Slopping** — learning inheritance across iterations
- **Fixed-Point Iteration** (6 edges) — explicit convergence detection
- **Memoization Cache** (6 edges) — avoid re-executing unchanged work

**Purpose:** Build knowledge across iterations, detect convergence, avoid repeated failures, and minimize wasted computation in iterative processes.

### Failure Adaptation
Patterns that respond to failures and unexpected conditions:
- **Dead Letter Quarantine** (10 edges) — systematic analysis of persistent failures
- **Circuit Breaker** (8 edges) — graceful degradation when instruments fail
- **Andon Cord** (6 edges) — diagnose before retry
- **Read-and-React** — workspace-driven adaptation

**Purpose:** Respond intelligently to failures rather than retrying blindly. Analyze failure patterns, adapt strategy, switch instruments, and prevent cascading failures.

### Multi-Perspective Analysis
Patterns that multiply analytical frames:
- **Source Triangulation** (7 edges) — verify claims across independent sources
- **Rashomon Gate** (7 edges) — parallel analysis from different frames
- **Red Team / Blue Team** — adversarial validation with blind defense
- **Delphi Convergence** — expert convergence without anchoring

**Purpose:** Avoid single-frame blindness. Run parallel analyses from different perspectives, detect contradictions, and converge on validated conclusions.

### Context & Handoff Management
Patterns that handle information flow across pipeline stages:
- **Relay Zone** (10 edges) — context compression between stages
- **Shipyard Sequence** (7 edges) — validate foundation before expensive fan-out
- **Succession Pipeline** (6 edges) — sequential substrate transformations
- **Closed-Loop Call** — semantic drift prevention at handoffs

**Purpose:** Preserve information fidelity across pipeline stages without exceeding context limits. Compress, structure, and validate handoffs to prevent semantic drift and enable effective downstream work.

---

**Pattern count:** 58 patterns across 8 coordination scales
