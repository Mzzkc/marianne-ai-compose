# The Rosetta Pattern Corpus

**Iteration:** 4 (post-review integration)
**Patterns:** 56 (38 from iterations 1-3, 18 new from iteration 4)
**Generative Forces:** 10 — Information Asymmetry, Finite Resources, Partial Failure, Exponential Defect Cost, Producer-Consumer Mismatch, Instrument-Task Fit, Convergence Imperative, Accumulated Signal, Structured Disagreement, Progressive Commitment
**Generators:** 11 — Graduate & Filter, Accumulate Knowledge, Contract at Interfaces, Exploit Failure as Signal, Verify through Diverse Observers, Gate on Environmental Readiness, Match Instrument to Grain, Measure Convergence Character, Threshold-Triggered Switch, Frame Multiplication, Incremental Exposure
**Cross-domain convergences:** 12 structural moves independently invented across 3+ domains
**Divergences:** 5 moves unique to a single domain (stretto, chavruta, privileged witness, foreshadow contract, comping substrate) — 3 moved to Awaiting Primitives

---

## Review Integration

### Iteration 1-2 Review Integration (Prior)

From iteration 1, three reviews produced these changes: **Elenchus** cut (unvalidatable self-interrogation). **Slime Mold Network** cut (structurally equivalent to Immune Cascade). **Kill Chain** merged into Immune Cascade. **Series Bible** merged into Barn Raising as concert-scale variant. **Fugal Exposition** merged into Talmudic Page as interlocking variant. All 20 surviving patterns received YAML snippets, failure modes, and validation tightening.

### Iteration 3 Review Integration (Prior)

**Patterns Cut (4):**

**Kishotenketsu Turn** — All three reviewers: a prompt technique, not an orchestration pattern. One sheet with a structured prompt operates at a different level of abstraction from every other pattern. The Practitioner: "I can compose this in 30 seconds because it's just a prompt with four sections." The Newcomer: "No newcomer will recognize when to use this." Moved to prompt engineering guidance, not the pattern corpus.

**The Aboyeur** — All three reviewers: the core mechanism (stagger start times by predicted duration) fails when durations are unpredictable, which is the default in AI orchestration. The Newcomer: "'When NOT to Use: When durations are unpredictable' — so always?" The Practitioner: "The YAML shows sequential sheets with comments like 'fires first' — that's just sequential execution." Moved to Patterns Awaiting Primitives with note that conductor scheduling support would make this viable.

**Supervision Hierarchy** — The Practitioner and Skeptic: the YAML uses `supervision:`, `strategy:`, `children:`, `max_restarts`, `restart_on` — none of which exist in Marianne. The Skeptic: "A pattern corpus that includes config syntax that doesn't parse is worse than useless." The Newcomer: "If this is aspirational, the corpus must say so." Moved to Patterns Awaiting Primitives with the approximation noted (workspace snapshots + conductor-mediated restart).

**Make-Ready Gate** — The Practitioner: "Run a precondition check before expensive work is not a pattern. It's a validation step. Every well-written score already does this." The Skeptic: "This is a validation concern, not a sheet-level pattern." The core insight (check contextual preconditions the DAG can't express) is preserved as guidance in the score-writing reference, not a named pattern.

**Patterns Strengthened (18):**

Every surviving new pattern received: (1) a failure mode paragraph, (2) YAML that reflects Marianne's actual capabilities (no fictional keys, no undefined variables), (3) honest acknowledgment of custom script dependencies, and (4) a status marker (Working/Aspirational). Specific changes:

- **Lines of Effort** — Clarified as concert-level requiring multiple scores. YAML shows single-score approximation with explicit note. (Practitioner, Skeptic)
- **Season Bible** — Justified re-separation from Barn Raising: bible is mutable and grows, conventions are static. Fixed no-op validation to check content, not just readability. (Skeptic, Practitioner)
- **Nurse Log** — Acknowledged structural overlap with Fan-out + Synthesis preparation. Justified: the intent distinction (general-purpose substrate vs. targeted pipeline input) affects how you design prompts and validations. (Skeptic, Newcomer)
- **Echelon Repair** — Added validations to every stage. Added misclassification failure mode. (Practitioner, Skeptic)
- **Commissioning Cascade** — Split chained validation into separate checks so failures are diagnosable. Noted Python-specific example. (Practitioner, Skeptic)
- **Fermentation Relay** — Admitted the pipeline is fixed in YAML; "substrate-driven" refers to how you design the gate between stages, not runtime switching. Replaced phantom `validate_extraction.py` with inline validation. (Practitioner, Skeptic)
- **Screening Cascade** — Made prompts specify different evaluation signals per stage. (Skeptic)
- **Vickrey Auction** — Acknowledged dynamic instrument selection needs a two-score concert or human-in-the-loop step. Shows the honest single-score approximation (probing informs next run, not this run). (Practitioner, Skeptic)
- **Forward Observer** — Noted cost trade-off: Opus observer must save more tokens downstream than it costs. (Skeptic)
- **Closed-Loop Call** — Replaced phantom `semantic_diff.py` with concrete diff-based validation comparing YAML keys. (Practitioner, Skeptic, Newcomer)
- **Sugya Weave** — Added subtitle "Editorial Synthesis." Replaced fragile inline Python with structured validation. (Newcomer)
- **Decision Propagation** (renamed from Arc Consistency Propagation) — Renamed for accessibility. Acknowledged constraint-brief writing requires judgment, not mechanical forwarding. (Newcomer, Skeptic)
- **Reconnaissance Pull** — Clarified: the generated plan is advisory input to subsequent stages, not dynamically executable. (Practitioner, Skeptic)
- **FRAGO** — Replaced Jinja conditional on prior output with file-based communication via `capture_files` and cadenza injection. (Practitioner, Skeptic, Newcomer)
- **Rehearsal Spotlight** — Hardcoded instance count. Replaced undefined condition variables with script-based termination check. (Practitioner, Skeptic)
- **Soil Maturity Index** — Replaced undefined condition variables with script-driven exit code for termination. (Practitioner, Skeptic)
- **Delphi Convergence** — Same fix: script-based convergence check replaces undefined condition variables. (Skeptic)
- **Back-Slopping** — Added subtitle "Learning Inheritance." Added `capture_files` for culture.yaml on work stage. (Newcomer, Practitioner)

### Iteration 4 Review Integration (Current)

Three adversarial reviews (Practitioner, Skeptic, Newcomer) applied to 22 new patterns from 6 cross-domain expeditions. All three returned **Needs revision**. Here is what changed.

**Patterns Cut (4):**

**Three-Phase Inference** — All three reviewers. The Skeptic: "Hindley-Milner attribution is unearned. This is 'organize your reasoning into three steps.'" The Practitioner: "Validations are purely cosmetic — checking for 'Phase 1:', 'Phase 2:', 'Phase 3:' verifies formatting, not reasoning." The Newcomer: "Nearly identical positioning to Constraint Propagation Sweep." Merged into Constraint Propagation Sweep as the generalization note. The useful insight (separate enumeration from resolution from synthesis) is preserved in the merged pattern.

**Backpressure Valve** — Practitioner and Skeptic. The Practitioner: "Self-chaining without termination condition. `{{ available }}` is not a defined template variable." The Skeptic: "Sequential batch processing is not backpressure. Real backpressure requires concurrent producer and consumer. The Reactive Streams attribution is misleading." Moved to Patterns Awaiting Primitives — requires concurrent score execution to work as described.

**Stretto Entry** — The Newcomer: "Aspirational, not implementable. Removes trust from the corpus. If I find one pattern I can't use, I suspect others." The Skeptic: "Correctly flagged as aspirational. Not composable today." Moved to Patterns Awaiting Primitives with the file_exists-dependency approximation noted.

**Comping Substrate** — The Newcomer: same credibility concern. The Skeptic: "Requires concurrent score execution with shared filesystem access. The overhead concern (reading all soloist workspaces at max_chain_depth: 100) is serious." Moved to Patterns Awaiting Primitives.

**Patterns Strengthened (18):**

Every surviving pattern received: (1) failure mode, (2) status marker (Working/Aspirational), (3) review-driven fixes to YAML, validations, and framing. Specific changes:

- **Commander's Intent Envelope** — Dropped "structural identity with ADP 6-0 Mission Command" framing (Skeptic: "indistinguishable from 'write a good prompt'"). Added concrete when-to-use threshold: "when the task has more than one valid approach." Added decision-log validation — proves the agent exercised judgment, not just produced output. Reframed as boundary-based prompting. (Skeptic, Newcomer)
- **Quorum Trigger** — Added CLI enforcement note: the signal register should be validated by a CLI instrument, not trusted to agent self-reporting. The Skeptic: "Zero enforcement. The threshold is a prompt instruction." Added the structural enforcement approach. (Practitioner, Skeptic)
- **Constraint Propagation Sweep** — Stripped AC-3 attribution (Skeptic: "dishonest"). Absorbed Three-Phase Inference's useful generalization (separate enumeration/resolution/synthesis). Honest about being a within-stage prompt technique, not an orchestration pattern. The Skeptic: "This is 'think about contradictions before writing.'" Acknowledged: yes, but structuring WHEN to think about contradictions (before generating, not during) is the actionable insight. (Skeptic, Newcomer)
- **The Tool Chain** — Added explanation of `instrument: cli` idiom (Newcomer: "Does Marianne actually support this?"). CLI instrument sheets use validation commands as the execution — explained inline. (Newcomer, Practitioner)
- **Canary Probe** — Added evaluation sheet between canary-run and canary-gate (Practitioner: "No sheet produces canary-verdict.yaml"). Fixed instance_id-to-manifest mapping. Added representativeness limitation (Skeptic: "representativeness is the fundamental unsolved problem of canary testing"). (Practitioner, Skeptic, Newcomer)
- **Speculative Hedge** — Honest about sequential execution (Skeptic: "if they run sequentially, this isn't hedging"). Updated: approaches run sequentially in current Marianne; cost analysis updated. Workspace isolation via subdirectories added (Practitioner: "both approaches write to same workspace with no isolation"). (Skeptic, Practitioner, Newcomer)
- **Dead Letter Quarantine** — Strengthened reprocess stage: shows how the adapted strategy uses quarantine analysis to change the prompt. Added validation on reprocessing success (Practitioner: "reprocess sheet has no validation checking success"). (Practitioner, Newcomer)
- **Clash Detection** — Added defensive YAML loading in validation (Practitioner: "assertion crashes with KeyError"). Scoped to detection, not resolution (Newcomer: "how does the newcomer fix clashes?"). (Practitioner, Newcomer)
- **Rashomon Gate** — Strengthened validation: checks finding count matches categorization count, not just keyword presence (Practitioner, Skeptic: "regex matching category keywords proves nothing"). Added cadenza cross-reference to glossary. (Practitioner, Skeptic, Newcomer)
- **Graceful Retreat** — Replaced insider tier examples (COMP/SCI/CULT/EXP/META) with domain-neutral tiers (Newcomer: "abbreviations mean nothing outside this project"). Added per-tier validation concept (Skeptic: "each tier needs its own validation set"). (Newcomer, Skeptic, Practitioner)
- **Saga Compensation Chain** — Marked Aspirational [`on_failure` compensation actions]. The Skeptic: "`on_failure` with `action: saga-compensator.yaml` — is this a real Marianne feature?" Honest answer: not yet. Shows the approximation (workspace snapshots + manually triggered compensation score). (Skeptic, Newcomer)
- **Progressive Rollout** — Fixed the core promise: instance count is STATIC per self-chain iteration in YAML, but the select-batch sheet reads rollout-state.yaml to determine which items are in the current batch. Honest about the workaround. The Skeptic: "If instances can't be dynamic, the core promise is unimplementable." Answer: batch selection changes, not instance count. (Skeptic, Practitioner)
- **Systemic Acquired Resistance** — Added concrete primer schema: `{threat_type, trigger_signature, countermeasure, confidence, timestamp}`. Added behavior change example showing how a primed score reads and adapts. The Newcomer: "The snippet hand-waves the entire implementation." (Newcomer, Practitioner)
- **Composting Cascade** — Defined "temperature" concretely: type coverage percentage, test pass rate, function extraction count. Noted script dependencies explicitly: user must supply `temperature.py` and `exhaustion.py` with interface contracts documented. (Skeptic, Practitioner, Newcomer)
- **Andon Cord** — Noted relationship to Marianne's self-healing feature (Skeptic: "is this already Marianne?"). Answer: Andon Cord is the score-level pattern; self-healing is the conductor-level implementation. Both exist, at different abstraction levels. (Skeptic, Practitioner)
- **Circuit Breaker** — Added circuit-state.yaml tracking across self-chain iterations (Practitioner, Skeptic: "no mechanism for tracking failure counts"). Restructured YAML to show the stateful aspect: health probe reads circuit-state, primary-work updates it, self-chain carries it forward. (Practitioner, Skeptic, Newcomer)
- **CEGAR Loop (Progressive Refinement)** — Added accessible subtitle (Newcomer: "CEGAR means nothing to a non-PL-theory audience"). Fixed termination: CLI validation sheet checks refinement-targets.yaml; empty file breaks the self-chain (Skeptic: "how does the loop stop?"). (Newcomer, Skeptic, Practitioner)
- **Memoization Cache** — Resolved [?] on global context: cache entries include a context hash from prelude content, not just input file hashes. `file_modified` validation acknowledged as requiring a user-supplied script (same pattern as other CLI validations). (Practitioner, Skeptic, Newcomer)

### Systemic Changes (Iteration 4)

- **Failure modes** added to all 18 new patterns — consistent with iteration 3 standard.
- **Status markers** on all new patterns: 16 Working, 2 Aspirational.
- **Prompt technique vs. orchestration pattern distinction** addressed. The Skeptic: "The corpus conflates single-sheet prompt techniques with multi-sheet orchestration patterns." Within-stage patterns are now explicitly labeled as prompt structuring techniques that operate within a single sheet, distinct from multi-sheet orchestration patterns. The distinction matters for composition: orchestration patterns compose with each other at the sheet/score/concert level; prompt techniques compose within a single sheet's prompt.
- **Enforcement gap** addressed. The Skeptic: "Too many patterns trust LLM self-reporting." Patterns now note where structural enforcement (CLI validation) should replace agent self-reporting. Quorum Trigger, Graceful Retreat, and Commander's Intent Envelope all received enforcement strengthening.
- **Custom script dependencies** continue the iteration 3 convention: patterns note when user-supplied scripts are required and document the interface contract.
- **Pattern Selection Guide** expanded to cover all 56 patterns.
- **Generative Forces** expanded from 7 to 10, with three new forces from cross-domain expeditions.
- **Awaiting Primitives** expanded with 3 new entries from iteration 4 cuts.

---

## Glossary

| Term | Meaning |
|------|---------|
| **Sheet** | One execution stage — a single agent performing a task |
| **Score** | A complete YAML job config containing one or more sheets |
| **Concert** | Multiple scores chained via `on_success` |
| **Conductor** | The daemon that manages job execution |
| **Workspace** | Directory where all outputs live — the shared filesystem |
| **Instrument** | An AI backend (Claude, Gemini, Ollama, CLI tools) with specific capabilities and cost profiles |
| **Prelude** | Markdown injected into every sheet's prompt — shared context |
| **Cadenza** | Per-sheet or per-instance markdown injected into that sheet's prompt. In fan-out, a list of cadenza files maps 1:1 to instances |
| **Fan-out** | Running multiple sheet instances in parallel (`instances: N`) |
| **Self-chaining** | A score triggering itself via `on_success: action: self`, carrying workspace forward |
| **capture_files** | Which workspace files a sheet can read from previous stages |
| **previous_outputs** | Mechanism forwarding all prior sheet outputs to the current sheet |
| **content_regex** | Validation: output matches a regex |
| **content_contains** | Validation: output contains a specific string |
| **command_succeeds** | Validation: a shell command exits 0 — real execution, not LLM opinion |
| **file_exists** | Validation: a workspace file was created |
| **file_modified** | Validation: a workspace file was changed (requires user-supplied check script) |
| **on_success** | What happens after completion — enables self-chaining and concert sequencing |
| **on_failure** | What happens after failure — Aspirational: not yet implemented in Marianne |
| **inherit_workspace** | Self-chain gets the same workspace, not fresh |
| **max_chain_depth** | Safety bound on self-chaining iterations |
| **Status: Working** | YAML in this pattern composes in Marianne today |
| **Status: Aspirational** | Pattern depends on a noted feature not yet in Marianne |
| **Prompt technique** | Within-stage pattern: structures a single sheet's prompt, not sheet arrangement |
| **Orchestration pattern** | Multi-sheet pattern: structures how sheets, scores, or instruments interact |

---

## Pattern Selection Guide

| Problem Type | Start With | Compose With | Difficulty |
|-------------|-----------|-------------|------------|
| Analyze something from multiple angles | Fan-out + Synthesis | Barn Raising, Triage Gate | Beginner |
| Broad search then targeted deep-dive | Immune Cascade | After-Action Review | Intermediate |
| Build something with a validation gate | Shipyard Sequence | Succession Pipeline | Beginner |
| Parallel work across different AI backends | Prefabrication | Barn Raising | Intermediate |
| Iteratively refine until stable | Fixed-Point Iteration | CDCL Search | Intermediate |
| Stress-test or red-team an artifact | Red Team / Blue Team | After-Action Review | Intermediate |
| Process a large batch of independent items | Stigmergic Workspace | Barn Raising | Beginner |
| Verify claims from independent sources | Source Triangulation | Triage Gate | Intermediate |
| Long-running work across many iterations | Cathedral Construction | After-Action Review | Advanced |
| Wait for external conditions | Dormancy Gate | Read-and-React | Beginner |
| Route work based on intermediate results | Read-and-React | Triage Gate | Beginner |
| Multi-score campaign with shared constraints | Barn Raising (concert) | Prefabrication | Intermediate |
| Compress context between pipeline stages | Relay Zone | Fan-out + Synthesis | Beginner |
| Tolerate partial fan-out failure | Quorum Consensus | Triage Gate | Intermediate |
| Choose the right instrument for work | Echelon Repair | Commissioning Cascade | Intermediate |
| Validate at multiple scopes with different tools | Commissioning Cascade | Echelon Repair, Shipyard Sequence | Intermediate |
| Reduce context window pressure | Forward Observer | Relay Zone, Screening Cascade | Beginner |
| Ensure handoff fidelity between stages | Closed-Loop Call | Relay Zone, Prefabrication | Intermediate |
| Produce a position from diverse inputs | Sugya Weave | Fan-out + Synthesis, Source Triangulation | Advanced |
| Propagate early decisions as constraints | Decision Propagation | CDCL Search | Advanced |
| Discover the approach before committing | Reconnaissance Pull | Mission Command | Beginner |
| Correct course mid-execution | FRAGO | Read-and-React, Lines of Effort | Advanced |
| Iterate on weak spots only | Rehearsal Spotlight | Echelon Repair, Soil Maturity Index | Advanced |
| Know when iterating is done (judgment tasks) | Delphi Convergence | Source Triangulation | Advanced |
| Know when iterating is done (character shift) | Soil Maturity Index | Fixed-Point Iteration | Advanced |
| Carry learning across iterations | Back-Slopping | Cathedral Construction, CDCL Search | Intermediate |
| Coordinate sustained parallel campaigns | Lines of Effort | Season Bible, After-Action Review | Advanced |
| Maintain coherence across a multi-score campaign | Season Bible | Lines of Effort, Relay Zone | Advanced |
| Prepare substrate for downstream consumers | Nurse Log | Fermentation Relay, Fan-out + Synthesis | Beginner |
| Process batches with escalating instruments | Screening Cascade | Echelon Repair, Immune Cascade | Intermediate |
| Select instruments via competitive probing | Vickrey Auction | Echelon Repair | Advanced |
| Use cheap instruments first, expensive later | Fermentation Relay | Echelon Repair, Succession Pipeline | Intermediate |
| Structure a prompt for agent autonomy | Commander's Intent Envelope | Mission Command | Beginner |
| Switch behavior mid-task on accumulated evidence | Quorum Trigger | Andon Cord, Circuit Breaker | Intermediate |
| Resolve contradictions before generating | Constraint Propagation Sweep | Decision Propagation | Beginner |
| Minimize AI cost with deterministic tools | The Tool Chain | Composting Cascade | Beginner |
| Test pipeline on a subset before full run | Canary Probe | Progressive Rollout, Dead Letter Quarantine | Beginner |
| Try multiple approaches, pick the winner | Speculative Hedge | Canary Probe | Intermediate |
| Quarantine and analyze batch failures | Dead Letter Quarantine | Circuit Breaker, Screening Cascade | Intermediate |
| Detect conflicts before integration | Clash Detection | Prefabrication, Andon Cord | Intermediate |
| Analyze from structurally different frames | Rashomon Gate | Source Triangulation, Sugya Weave | Intermediate |
| Deliver partial value when full output fails | Graceful Retreat | Dead Letter Quarantine, Andon Cord | Intermediate |
| Roll out changes in graduated phases | Progressive Rollout | Canary Probe, Dead Letter Quarantine | Intermediate |
| Broadcast failure-derived defenses | Systemic Acquired Resistance | After-Action Review, Back-Slopping | Advanced |
| Drive phase transitions with workspace metrics | Composting Cascade | The Tool Chain, Succession Pipeline | Advanced |
| Diagnose failure before retrying | Andon Cord | Circuit Breaker, Quorum Trigger | Intermediate |
| Handle instrument infrastructure failures | Circuit Breaker | Dead Letter Quarantine, Echelon Repair | Intermediate |
| Refine verification from coarse to fine | CEGAR Loop | Memoization Cache, CDCL Search | Advanced |
| Skip re-analysis of unchanged inputs | Memoization Cache | CEGAR Loop, Cathedral Construction | Intermediate |
| Undo side effects on concert failure | Saga Compensation Chain | After-Action Review | Advanced |

**If you're new, start here:** Fan-out + Synthesis → Shipyard Sequence → The Tool Chain → Canary Probe → Andon Cord. These five patterns cover the most common problems and compose with everything else.

---

# Foundational Pattern

## Fan-out + Synthesis

`Status: Working` · **Source:** Ubiquitous — confirmed across all expeditions. Prior art: MapReduce (Dean & Ghemawat, 2004).

### Core Dynamic

Split work into parallel independent streams, merge in a synthesis stage. N agents work simultaneously on different facets. A final agent reads all outputs and produces a unified result. Most score-level patterns in this corpus build on, modify, or explicitly reject this structure. It is the default move when information asymmetry meets finite resources.

### When to Use / When NOT to Use

Use when the problem decomposes into independent sub-problems with a meaningful merge. Not when sub-problems share mutable state, synthesis is trivial concatenation, or fan-out width of 1 suffices.

### Marianne Score Structure

```yaml
sheets:
  - name: prepare
    prompt: "Define scope and shared context for the analysis."
    validations:
      - type: file_exists
        path: "{{ workspace }}/scope.md"
  - name: analyze
    instances: 6
    prompt: "Analyze module {{ instance_id }}. Write findings to analysis-{{ instance_id }}.md."
    capture_files: ["scope.md"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/analysis-{{ instance_id }}.md"
  - name: synthesize
    prompt: "Read all analysis files. Produce a unified review addressing cross-cutting concerns."
    capture_files: ["analysis-*.md"]
    validations:
      - type: command_succeeds
        command: "test $(ls {{ workspace }}/analysis-*.md | wc -l) -ge 4"
```

### Failure Mode

Synthesis produces concatenation rather than integration. Validate with `command_succeeds` checking the synthesis references cross-cutting themes, not just individual reports. If fan-out agents share state, outputs will converge — use Prefabrication with interface contracts instead.

### Composes With

Barn Raising (conventions govern fan-out), Shipyard Sequence (validate before fanning out), After-Action Review (coda on synthesis), Triage Gate (classify outputs before synthesis), Relay Zone (compress before synthesis)

---

# Within-Stage Patterns

*Prompt techniques — these structure a single sheet's prompt, not sheet arrangement.*

## Decision Propagation

`Status: Working` · **Source:** Constraint satisfaction (renamed from Arc Consistency Propagation). **Forces:** Information Asymmetry.

### Core Dynamic

When a sheet makes a decision that constrains downstream sheets, it writes a structured constraint brief rather than embedding the decision in prose. The brief has: decision, rationale, implications, and constraints-for-downstream. Each downstream sheet reads the brief and acknowledges which constraints it incorporated. Writing the brief requires judgment — the agent must identify which decisions are load-bearing.

### When to Use / When NOT to Use

Use when decisions in early stages have compounding effects. Not when stages are independent or constraints are simple enough for the prompt alone.

### Marianne Score Structure

```yaml
sheets:
  - name: decide
    prompt: >
      Make the architecture decision. Write constraint-brief.yaml:
      {decision, rationale, implications: [], constraints_for_downstream: []}.
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; b=yaml.safe_load(open('{{ workspace }}/constraint-brief.yaml')); assert 'constraints_for_downstream' in b\""
  - name: implement
    instances: 4
    prompt: "Read constraint-brief.yaml. Build component {{ instance_id }}. Acknowledge constraints."
    capture_files: ["constraint-brief.yaml"]
```

### Failure Mode

Constraint briefs too abstract to constrain. The brief should name specific artifacts and interfaces, not just abstract goals. Validate with `command_succeeds` checking brief has concrete entries.

### Composes With

CDCL Search, CEGAR Loop, Commander's Intent Envelope

---

## Commander's Intent Envelope

`Status: Working` · **Source:** Military mission command doctrine, Expedition 5. **Scale:** within-stage. **Iteration:** 4. **Type:** Prompt technique.

### Core Dynamic

Structures a single sheet's prompt as PURPOSE (why this task matters in the larger score), END STATE (measurable success conditions), CONSTRAINTS (hard boundaries — MUST NOT violate), and FREEDOMS (decisions the agent may make autonomously). The structural distinction from ordinary prompting: this changes the coordination contract from instructions (do X then Y) to boundaries (achieve Z however you see fit, except A). The agent finds its own path within the envelope. Validates end-state achievement and autonomous decision-making, not method compliance.

### When to Use / When NOT to Use

Use when the task has more than one valid approach, when inputs are variable-format, or when different instruments would achieve the end state differently. Not for purely mechanical tasks (format conversion, command execution), security-critical operations where deviations create vulnerabilities, or when validation criteria can't capture the end state precisely.

### Marianne Score Structure

```yaml
sheets:
  - name: execute
    prompt: |
      ## Commander's Intent
      PURPOSE: Ensure the web application has no exploitable input validation vulnerabilities.
      END STATE: Report listing all confirmed vulnerabilities with severity, location, fix. Zero false positives.
      CONSTRAINTS: Do not modify source code. Do not run code. Do not access external services.
      FREEDOMS: Choose which files to review. Choose review order. Choose depth based on risk.

      ## Context
      Read {{ workspace }}/codebase/ for the application source.

      ## Resources
      Write findings to {{ workspace }}/security-report.md and decision-log.md.
    validations:
      - type: file_exists
        path: "{{ workspace }}/security-report.md"
      - type: file_exists
        path: "{{ workspace }}/decision-log.md"
      - type: content_contains
        path: "{{ workspace }}/decision-log.md"
        content: "DECISION:"
```

### Failure Mode

Intent briefs too vague produce incoherent decisions; too specific collapses the decision space back to instructions. The decision-log validation is critical: if the agent made no autonomous decisions, the envelope wasn't adding value over direct instructions. If the log shows decisions outside the CONSTRAINTS, the boundaries were unclear.

### Composes With

Mission Command (intent IS mission command at sheet scale), Fan-out + Synthesis (intent envelope shared across instances), After-Action Review (decision-log feeds doctrine)

---

## Quorum Trigger

`Status: Working` · **Source:** Bacterial quorum sensing (threshold-triggered behavioral switch), Expedition 2. **Scale:** within-stage. **Iteration:** 4. **Force:** Accumulated Signal. **Type:** Prompt technique.

### Core Dynamic

Within-stage behavioral switch triggered by accumulated signal density. The agent works on its primary task while maintaining an explicit signal register (a YAML file tracking findings with severity levels). When accumulated signals cross a predefined threshold (e.g., "3+ CRITICAL findings"), the agent stops its current plan and switches to an alternate behavior (remediation, diagnosis, escalation). The switch is binary — a phase transition, not a gradual adjustment.

**Enforcement note:** The signal register is agent-maintained and therefore untrustworthy in isolation. A downstream CLI validation sheet should verify the register's threshold state independently. Do not rely solely on the agent self-reporting whether the trigger fired.

### When to Use / When NOT to Use

Use when conditions discovered mid-task make continuing the original plan wasteful or dangerous: code review finding critical security flaws, research discovering the premise is wrong, data processing hitting malformed records. Not when the threshold is ambiguous, the task is too short for the switch to fire, or the behavioral switch loses valuable pre-switch context.

### Marianne Score Structure

```yaml
sheets:
  - name: audit
    prompt: |
      Audit each module for vulnerabilities. Maintain a signal register in signal-register.yaml:
      each entry has {module, severity: LOW|MEDIUM|HIGH|CRITICAL, finding}.

      THRESHOLD: If you accumulate 3+ CRITICAL findings before completing the full audit,
      STOP scanning and switch to writing a remediation plan for findings so far.
      Write quorum-trigger-report.md if threshold fires.
    validations:
      - type: file_exists
        path: "{{ workspace }}/signal-register.yaml"
      - type: content_regex
        pattern: "severity:\\s+(LOW|MEDIUM|HIGH|CRITICAL)"
  - name: verify-threshold
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; r=yaml.safe_load(open('{{ workspace }}/signal-register.yaml')); crits=[e for e in r if e.get('severity')=='CRITICAL']; import os; triggered=os.path.exists('{{ workspace }}/quorum-trigger-report.md'); assert (len(crits)>=3)==triggered, f'Threshold mismatch: {len(crits)} crits, triggered={triggered}'\""
```

### Failure Mode

Agent miscounts findings or ignores the threshold entirely. The CLI verification sheet catches this: if 3+ CRITICALs exist but no trigger report (or vice versa), the validation fails. The deeper failure: the agent classifies everything as MEDIUM to avoid triggering. Only domain-specific validation of severity assignments catches this.

### Composes With

Andon Cord (quorum trigger within a stage, andon cord between stages), Circuit Breaker (quorum for quality, circuit breaker for infrastructure), Immune Cascade (quorum-triggered triage)

---

## Constraint Propagation Sweep

`Status: Working` · **Source:** Constraint satisfaction, structured reasoning. **Scale:** within-stage. **Iteration:** 4. **Force:** Domain Reduction. **Type:** Prompt technique.

### Core Dynamic

Before generating ANY output, the prompt instructs the agent to separate three kinds of reasoning into mandatory phases: (1) ENUMERATE all constraints from the specification and workspace artifacts, (2) RESOLVE them pairwise to identify contradictions and prune impossible options, (3) GENERATE from the reduced solution space. The phases MUST be separate — generating during resolution skips contradictions; resolving during generation loses information. This is domain reduction before search: pruning is cheap, search through contradictory requirements is expensive.

This is a prompt structuring technique, not a multi-sheet orchestration pattern. The phases are instructions within one prompt, not separate sheets. This means no intermediate validation between phases — the agent can skip resolution and you'd only detect it from the output quality, not structurally. For structural enforcement, use three separate sheets with validation between them.

### When to Use / When NOT to Use

Use when specifications contain implicit contradictions from different stakeholders, when generating from conflicting requirements costs more than constraint analysis, or when reconciling heterogeneous inputs (multiple analyst reports, multi-team requirements). Not when constraints are few and independent, the specification is already consistent, or the task is creative rather than constrained.

### Marianne Score Structure

```yaml
sheets:
  - name: synthesize
    prompt: |
      ENUMERATE: List every constraint from the input documents.
      RESOLVE: Check each pair for conflicts. Mark the weaker constraint as pruned.
      Write constraint-audit.yaml: {id, constraint, status: active|pruned, reason}.
      GENERATE: Produce the architecture using only active constraints.
      Write the synthesis to synthesis.md.
    capture_files: ["requirements/*.md"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/constraint-audit.yaml"
      - type: command_succeeds
        command: "python3 -c \"import yaml; a=yaml.safe_load(open('{{ workspace }}/constraint-audit.yaml')); pruned=[e for e in a if e.get('status')=='pruned']; print(f'{len(pruned)} constraints pruned of {len(a)} total')\""
```

### Failure Mode

Agent performs all three phases but doesn't actually prune — the audit shows zero pruned constraints despite contradictory inputs. The `command_succeeds` validation catches this by printing stats, but can't enforce quality. For high-stakes synthesis, follow with a dedicated clash detection sheet comparing the synthesis against all input constraints.

### Composes With

Decision Propagation (propagation feeds constraint briefs), CDCL Search (failures become new constraints), Rashomon Gate (multiple frames on the same constraint set)

---

# Score-Level Patterns

## Triage Gate

`Status: Working` · **Source:** Emergency medicine START protocol, military command. **Forces:** Finite Resources + Partial Failure.

### Core Dynamic

Coarse classification before expensive processing. A fast classifier reads fan-out outputs and routes: **RED** (forward to synthesis), **YELLOW** (rework with targeted prompt), **GREEN** (supplementary), **BLACK** (discard with logged reason). Structural checks first (schema compliance, required sections, word count), then semantic if needed. This is the convergence ranked #1 across all domains.

### When to Use / When NOT to Use

Use when fan-out produces mixed quality, downstream processing is expensive, and structural quality checks are definable. Not when all outputs must be incorporated or fan-out is narrow (2-3 agents).

### Marianne Score Structure

```yaml
sheets:
  - name: triage
    prompt: >
      Read each output in fan-out-results/. For each, write a line in triage-manifest.yaml:
      {id, category: RED|YELLOW|GREEN|BLACK, reason, rework_prompt}.
      Use structural checks first: required sections present, word count > 200.
    capture_files: ["fan-out-results/*.md"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; m=yaml.safe_load(open('{{ workspace }}/triage-manifest.yaml')); assert all(e['category'] in ['RED','YELLOW','GREEN','BLACK'] for e in m)\""
```

### Failure Mode

If YELLOW count is 0, the rework stage still executes but produces nothing — guard with a Read-and-React conditional. If everything is BLACK, synthesis gets no inputs; the score should fail explicitly.

### Composes With

Immune Cascade, Fan-out + Synthesis, Relay Zone

---

## Immune Cascade

`Status: Working` · **Source:** Immunology (innate/adaptive response). Absorbs Kill Chain F2T2EA. **Forces:** Finite Resources + Exponential Defect Cost.

### Core Dynamic

Escalating tiers: fast/cheap/broad first for intelligence, slow/expensive/precise targeting what tier 1 found, then learning persistence. Three structural moves: graduated response, intelligence forwarding, learning persistence. **Strict Sequential Variant (from Kill Chain):** When the problem is pure narrowing, collapse to a linear pipeline where `command_succeeds verifying count decreased` validates each gate.

### When to Use / When NOT to Use

Use when the problem requires broad search before targeted work and cheap scanning methods exist. Not when the problem is narrow enough for direct attack.

### Marianne Score Structure

```yaml
sheets:
  - name: broad-sweep
    instances: 8
    instrument: haiku
    prompt: "Scan {{ partition }} for issues. Write raw findings."
    validations:
      - type: file_exists
        path: "{{ workspace }}/sweep-{{ instance_id }}.md"
  - name: triage-handoff
    prompt: "Read all sweep files. Deduplicate. Prioritize. Write targeting-brief.md."
    capture_files: ["sweep-*.md"]
  - name: deep-investigation
    instrument: opus
    prompt: "Deep-dive on prioritized targets. Write remediation."
    capture_files: ["targeting-brief.md"]
  - name: learning
    prompt: "Write doctrine.md: what scanning missed, what triage misjudged, rules for next run."
    validations:
      - type: content_regex
        pattern: "RULE:\\s+.+"
```

### Failure Mode

The learning stage is useless if it doesn't write persistent, structured output. Specify the artifact: `doctrine.md` with `RULE:` entries that the next iteration's broad sweep reads via prelude.

### Composes With

Triage Gate (handoff IS triage), After-Action Review (coda), Relay Zone (relay between tiers)

---

## Mission Command

`Status: Working` · **Source:** Auftragstaktik (Prussian military doctrine). **Forces:** Information Asymmetry.

### Core Dynamic

Separate "what and why" (centralized) from "how" (decentralized). The intent envelope has three layers: **purpose** (why), **key tasks** (what), **end state** (what done looks like). Agents adapt freely within the decision space. Validate end-state achievement, never method compliance. The structural distinction: Mission Command scores have a *specific, named intent document* that replaces per-agent context acquisition, plus end-state-only validation.

### When to Use / When NOT to Use

Use when tasks require agent judgment and conditions may differ from expectations. Not for mechanical tasks or constraints so tight only one approach is valid.

### Marianne Score Structure

```yaml
sheets:
  - name: mission-brief
    prompt: >
      Write mission-brief.md with three sections:
      PURPOSE: why this refactoring matters.
      KEY TASKS: the 4 modules that must be decoupled.
      END STATE: all 340 tests pass, public API unchanged, coupling metric < 0.3.
    validations:
      - type: content_contains
        content: "PURPOSE:"
      - type: content_contains
        content: "END STATE:"
  - name: execute
    instances: 4
    prompt: "Read mission-brief.md. Decouple module {{ instance_id }}."
    capture_files: ["mission-brief.md"]
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && python -m pytest --tb=no -q"
```

### Failure Mode

Intent briefs too vague produce incoherent decisions. Too specific collapses the decision space. The end state must be testable with `command_succeeds`.

### Composes With

After-Action Review, Barn Raising, Prefabrication

---

## Shipyard Sequence

`Status: Working` · **Source:** Shipbuilding hull block method. **Forces:** Exponential Defect Cost + Finite Resources.

### Core Dynamic

Validate foundational work under realistic conditions before investing in expensive fan-out. The launch gate uses `command_succeeds` exclusively — real execution, not LLM judgment. Construction has 1-3 stages; outfitting fans out only after launch passes.

### When to Use / When NOT to Use

Use when downstream fan-out is expensive, foundation must be solid, and real validation tools exist. Not when work is naturally parallel from the start.

### Marianne Score Structure

```yaml
sheets:
  - name: construct-schema
    prompt: "Generate the database schema and migration files."
  - name: launch-gate
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && python manage.py migrate --check"
      - type: command_succeeds
        command: "cd {{ workspace }} && python manage.py test db_schema --verbosity=0"
  - name: outfitting
    instances: 4
    prompt: "Build {{ service_name }} against the validated schema."
    capture_files: ["schema.sql"]
```

### Failure Mode

If launch validation is too lenient, expensive fan-out proceeds on a broken foundation. The gate must use `command_succeeds`, never `content_contains`.

### Composes With

Succession Pipeline, Dormancy Gate, Triage Gate

---

## Succession Pipeline

`Status: Working` · **Source:** Forest succession ecology. **Forces:** Exponential Defect Cost.

### Core Dynamic

Each stage transforms the workspace into a state where the next becomes possible. The substrate transformation test: does Stage N's output become Stage N+1's input *substrate* — a different *kind* of thing? Three mandatory phases using categorically different methods.

### When to Use / When NOT to Use

Use when stages require fundamentally different methods and each output is the next's prerequisite environment. Not when each stage uses the same approach (that's iteration).

### Marianne Score Structure

```yaml
sheets:
  - name: parse
    prompt: "Parse source files into abstract syntax trees. Write AST JSON."
    validations:
      - type: command_succeeds
        command: "python3 -c \"import json; json.load(open('{{ workspace }}/ast.json'))\""
  - name: transform
    prompt: "Transform AST into intermediate representation."
    capture_files: ["ast.json"]
  - name: generate
    prompt: "Generate target code from IR."
    capture_files: ["ir.dot"]
```

### Failure Mode

If your stages use the same method with growing detail, that's Fixed-Point Iteration, not Succession.

### Composes With

Shipyard Sequence, Barn Raising

---

## Red Team / Blue Team

`Status: Working` · **Source:** Military adversarial exercises. **Forces:** Information Asymmetry + Partial Failure.

### Core Dynamic

Information asymmetry via redaction: Red writes *effects* but not *methods*. Blue sees effects, must defend blind. Purple debrief gets full access. **Enforcement:** Separate workspace subdirectories (`red-workspace/` vs `blue-briefing/`). A relay stage copies only effect descriptions. Blue's `capture_files` is restricted to `blue-briefing/` only.

### When to Use / When NOT to Use

Use when the artifact needs adversarial stress-testing and the defender should not know attack methods. Not when the team is collaborative or the artifact is too simple for adversarial testing.

### Marianne Score Structure

```yaml
sheets:
  - name: red-attack
    prompt: "Attack the artifact. Write effects to red-workspace/effects.md and methods to red-workspace/methods.md."
    validations:
      - type: file_exists
        path: "{{ workspace }}/red-workspace/effects.md"
  - name: relay
    instrument: cli
    validations:
      - type: command_succeeds
        command: "cp {{ workspace }}/red-workspace/effects.md {{ workspace }}/blue-briefing/effects.md"
  - name: blue-defend
    prompt: "Read blue-briefing/effects.md. Defend. Write blue-response.md."
    capture_files: ["blue-briefing/effects.md"]
  - name: purple-debrief
    prompt: "Read ALL files. Write debrief with attack-defense matrix."
    capture_files: ["red-workspace/**", "blue-briefing/**", "blue-response.md"]
```

### Failure Mode

Red produces weak attacks, Blue passes trivially. Validate Red output contains specific attack categories. If relay leaks methods, Blue's defense is tainted.

### Composes With

After-Action Review (purple debrief IS AAR), Immune Cascade

---

## Prefabrication

`Status: Working` · **Source:** Construction industry (offsite fabrication). **Forces:** Producer-Consumer Mismatch + Finite Resources.

### Core Dynamic

Define interface contracts before parallel work begins. Each parallel track gets a shared interface definition and builds to it. Integration only assembles pre-validated pieces. Different from Fan-out + Synthesis: prefabrication has an explicit interface specification stage before fan-out.

### When to Use / When NOT to Use

Use when parallel tracks must produce compatible outputs. Not when outputs are independent (use plain Fan-out) or when the interface can't be defined upfront.

### Marianne Score Structure

```yaml
sheets:
  - name: interface-spec
    prompt: "Define the shared API contract. Write interface-spec.yaml."
    validations:
      - type: file_exists
        path: "{{ workspace }}/interface-spec.yaml"
  - name: build
    instances: 4
    prompt: "Build component {{ instance_id }} according to interface-spec.yaml."
    capture_files: ["interface-spec.yaml"]
  - name: integrate
    prompt: "Assemble all components. Verify all interfaces match."
    capture_files: ["component-*/**"]
```

### Failure Mode

Interface spec too loose allows incompatible implementations. Too tight eliminates the benefits of parallel work.

### Composes With

Barn Raising, Clash Detection, Mission Command

---

## Relay Zone

`Status: Working` · **Source:** Track relay (athletics). **Forces:** Producer-Consumer Mismatch.

### Core Dynamic

Context compression between pipeline stages. A dedicated relay sheet reads the full output of the previous stage and produces a compressed summary for the next stage. Prevents context window bloat across long pipelines.

### When to Use / When NOT to Use

Use when cumulative outputs exceed context limits. Not when all information must survive compression.

### Marianne Score Structure

```yaml
sheets:
  - name: relay
    prompt: >
      Read all prior outputs. Compress to relay-brief.md:
      key findings, open questions, critical data only. Target 20% of original size.
    capture_files: ["full-output/**"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/relay-brief.md"
      - type: command_succeeds
        command: "test $(wc -w < '{{ workspace }}/relay-brief.md') -lt 2000"
```

### Failure Mode

Relay loses critical information. Downstream stages produce incorrect results because the relay omitted a key finding. Validate relay completeness by checking key terms survive compression.

### Composes With

Fan-out + Synthesis, Forward Observer, Screening Cascade

---

## Quorum Consensus

`Status: Working` · **Source:** Distributed systems quorum. **Forces:** Partial Failure + Finite Resources.

### Core Dynamic

Accept results when a quorum (majority) of fan-out agents agree, even if some fail. N agents run; the synthesis stage proceeds when M of N produce valid output. The remaining agents' failures are logged but don't block the pipeline.

### When to Use / When NOT to Use

Use when fan-out may have partial failure and majority agreement is sufficient. Not when every agent's output is critical.

### Marianne Score Structure

```yaml
sheets:
  - name: analyze
    instances: 5
    prompt: "Analyze the artifact. Write analysis-{{ instance_id }}.md."
  - name: quorum-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "test $(ls {{ workspace }}/analysis-*.md 2>/dev/null | wc -l) -ge 3"
  - name: synthesize
    prompt: "Read available analyses. Note which are missing. Synthesize from quorum."
    capture_files: ["analysis-*.md"]
```

### Failure Mode

Quorum reached but the surviving agents all made the same error. Use Source Triangulation to ensure diversity.

### Composes With

Triage Gate, Source Triangulation, Fan-out + Synthesis

---

## Commissioning Cascade

`Status: Working` · **Source:** Marine vessel commissioning. **Forces:** Instrument-Task Fit + Exponential Defect Cost.

### Core Dynamic

Validate at multiple scopes using different tools at each level. Unit → integration → acceptance, each with scope-appropriate validation instruments. Split chained validations into separate checks so failures are diagnosable.

### When to Use / When NOT to Use

Use when different validation scopes require different tools. Not when a single validation pass suffices.

### Marianne Score Structure

```yaml
sheets:
  - name: unit-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && python -m pytest tests/unit/ -q"
  - name: integration-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && python -m pytest tests/integration/ -q"
  - name: acceptance-review
    prompt: "Read test results. Write acceptance report against the original requirements."
    capture_files: ["test-results/**"]
```

### Failure Mode

Unit tests pass but integration fails — the cascade catches this. If all validation is at one level, cascading adds no value.

### Composes With

Echelon Repair, Shipyard Sequence, The Tool Chain

---

## The Tool Chain

`Status: Working` · **Source:** CI/CD pipelines (Jenkins, GitHub Actions), Expedition 1. **Scale:** score-level + instrument strategy. **Iteration:** 4.

### Core Dynamic

Inverts the corpus default: non-AI tools do primary work, AI agents appear only at planning, triage, and interpretation points. Instrument selection follows the work's nature: deterministic work gets deterministic tools, judgment work gets judgment instruments. Most real-world pipelines are 80% deterministic tools, 20% AI judgment.

**Implementation note:** Sheets with `instrument: cli` use validation commands as the execution mechanism. The sheet has no `prompt` — the `command_succeeds` validation IS the work. This is a valid Marianne pattern for deterministic stages.

### When to Use / When NOT to Use

Use when most stages are deterministic transformations (data processing, code compilation, format conversion), when work is expressible as CLI commands with exit codes, when cost matters — CLI instruments are free. Not when every stage requires judgment or output can't be validated by exit code alone.

### Marianne Score Structure

```yaml
sheets:
  - name: plan
    instrument: claude
    prompt: "Read input. Produce processing-plan.yaml."
  - name: fetch
    instrument: cli
    validations:
      - type: command_succeeds
        command: "curl -sf -o {{ workspace }}/raw.csv 'https://api.example.com/data'"
  - name: clean
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/clean_data.py {{ workspace }}/raw.csv {{ workspace }}/clean.csv"
  - name: analyze
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/analyze.py {{ workspace }}/clean.csv {{ workspace }}/report.md"
  - name: interpret
    instrument: claude
    prompt: "Read report.md. Write executive summary with recommendations."
    capture_files: ["report.md"]
```

**Script dependencies:** `clean_data.py` and `analyze.py` must exist in the workspace — seeded via prelude, generated by the plan sheet, or supplied by the user.

### Failure Mode

CLI stages fail silently when piped: `cmd | tail -5` always exits 0. Use `bash -c '...; exit ${PIPESTATUS[0]}'` in `command_succeeds` validations. AI stages used where CLI suffices waste budget.

### Example

Survey processing: AI plans, `curl` fetches, `python3` cleans and analyzes, AI writes executive summary. Cost: ~$0.50 instead of ~$5.00 all-LLM.

### Composes With

Echelon Repair (tool chain IS echelon E1), Commissioning Cascade (CLI tiers for validation), Composting Cascade (CLI instruments as thermometers)

---

## Canary Probe

`Status: Working` · **Source:** DevOps canary deployment, military recon-in-force, Expedition 5. **Scale:** score-level. **Iteration:** 4. **Force:** Progressive Commitment.

### Core Dynamic

Run a miniature version of the full pipeline on a tiny subset of real data before committing to full scale. The canary uses the EXACT SAME pipeline — identical instruments, validations, prompts — just on fewer items. If the canary dies, you've lost almost nothing. If it lives, you have evidence (not hope) that full-scale execution works.

**Representativeness caveat:** Canary testing's fundamental limitation is that the subset must be representative. If it isn't, you learn nothing. The selection stage should use structural diversity criteria (different file sizes, different formats, edge cases), not random sampling.

### When to Use / When NOT to Use

Use for any score operating on a list of items, migration scores, batch processing, or concert coordination where Score B depends on Score A's output format. Not when the canary subset can't be representative (tail-risk failures) or setup cost makes a probe nearly as expensive as the full run.

### Marianne Score Structure

```yaml
sheets:
  - name: select-canary
    prompt: >
      Select 3 representative items from the full set, choosing for structural diversity
      (different sizes, formats, edge cases). Write canary-manifest.yaml listing selected items.
    validations:
      - type: file_exists
        path: "{{ workspace }}/canary-manifest.yaml"
  - name: canary-run
    instances: 3
    prompt: >
      Read canary-manifest.yaml. Process item at index {{ instance_id }}.
      Write result to canary-result-{{ instance_id }}.md.
    capture_files: ["canary-manifest.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/canary-result-{{ instance_id }}.md"
  - name: canary-evaluate
    prompt: >
      Read all canary results. Evaluate: did each produce valid output?
      Write canary-verdict.yaml: {go: true/false, results: [{item, pass, reason}]}.
    capture_files: ["canary-result-*.md", "canary-manifest.yaml"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; v=yaml.safe_load(open('{{ workspace }}/canary-verdict.yaml')); assert 'go' in v\""
  - name: canary-gate
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; v=yaml.safe_load(open('{{ workspace }}/canary-verdict.yaml')); assert v['go'], 'Canary failed'\""
  - name: full-run
    instances: 20
    prompt: "Process remaining items from the full set."
    capture_files: ["canary-manifest.yaml"]
```

### Failure Mode

Canary passes but full run fails — the canary subset was unrepresentative. Mitigate by selecting for structural diversity, not convenience. If the canary itself is expensive (complex setup), the pattern provides no cost advantage — use a simpler validation gate instead.

### Composes With

Progressive Rollout (canary IS phase 1), Dead Letter Quarantine (canary failures reveal quarantine candidates), Speculative Hedge (canary each hedge path before committing)

---

## Speculative Hedge

`Status: Working` · **Source:** CPU branch prediction, military COA analysis, financial hedging, Expedition 5. **Scale:** score-level. **Iteration:** 4. **Force:** Progressive Commitment.

### Core Dynamic

Run DIFFERENT strategies on the SAME problem and commit to whichever succeeds. Not fan-out (same task, different data) — this runs different APPROACHES on the same data. The cost analysis: if retry-from-scratch costs more than running both, hedge.

**Execution note:** In current Marianne, approaches run sequentially (sheets execute in order). This means the delivery time is the SUM of both approaches, not the MAX. The value proposition is not time savings but elimination of the "wrong approach, start over" scenario — you always get at least one valid result. For true parallel hedging, use two separate scores in a concert.

### When to Use / When NOT to Use

Use for migration tasks with unknown edge cases, research with multiple search strategies, any task where "wrong approach, retry" costs more than "both approaches, discard one." Not when both approaches are equally expensive and success rate is high, when budget is hard-capped, or when approaches interfere.

### Marianne Score Structure

```yaml
sheets:
  - name: analyze
    prompt: "Analyze the problem. Define two approaches and evaluation criteria. Write hedge-plan.yaml."
  - name: approach-a
    prompt: "Execute approach A: mechanical transformation. Write all output to approach-a-result/."
  - name: approach-b
    prompt: "Execute approach B: clean-room rewrite guided by tests. Write all output to approach-b-result/."
    capture_files: ["hedge-plan.yaml"]
  - name: evaluate
    prompt: "Run tests against both. Write hedge-decision.yaml: {winner, rationale, test_results}."
    capture_files: ["approach-a-result/**", "approach-b-result/**"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; d=yaml.safe_load(open('{{ workspace }}/hedge-decision.yaml')); assert 'winner' in d\""
```

### Failure Mode

Both approaches fail — the hedge didn't reduce risk, it doubled cost. Mitigate with a Canary Probe on each approach before full execution. If approaches write to the same files (no subdirectory isolation), they clobber each other's output — always use separate output directories.

### Composes With

Wargame Table (wargame before hedging to reduce approaches), Canary Probe (canary each approach before full hedge)

---

## Dead Letter Quarantine

`Status: Working` · **Source:** RabbitMQ/Kafka dead letter queues, Expedition 5. **Scale:** score-level. **Iteration:** 4. **Force:** Graceful Failure.

### Core Dynamic

After N retries, STOP RETRYING AND QUARANTINE. Move failed items to a separate processing path with different handling: different instruments, different prompts, different strategy. The quarantine is an ARTIFACT that persists, accumulates, and can be ANALYZED. "Why did these 7 items fail?" often reveals a systematic issue that fixing once clears the entire quarantine.

### When to Use / When NOT to Use

Use for any batch processing where some items are expected to fail, self-chaining scores where iteration N should not re-attempt items from N-1, or concert-level routing of failures to a different score. Not when every item MUST succeed, failures are truly random, or the quarantine grows to dwarf successful items (the pipeline itself is broken).

### Marianne Score Structure

```yaml
sheets:
  - name: process
    instances: 10
    prompt: "Process item {{ instance_id }}. Write result-{{ instance_id }}.md on success."
  - name: collect
    prompt: >
      Identify failures (missing or empty result files). Write quarantine.yaml listing
      failed items with {item_id, error_symptom, attempted_strategy}.
    capture_files: ["result-*.md"]
  - name: analyze-quarantine
    prompt: >
      Read quarantine.yaml. Identify common failure patterns.
      Write quarantine-analysis.md with: {pattern, affected_items, suggested_strategy}.
    capture_files: ["quarantine.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/quarantine-analysis.md"
  - name: reprocess
    prompt: >
      Read quarantine-analysis.md. For each failure pattern, apply the suggested strategy.
      Write reprocess-results.yaml: [{item_id, outcome: success|permanent_quarantine, detail}].
    capture_files: ["quarantine.yaml", "quarantine-analysis.md"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; r=yaml.safe_load(open('{{ workspace }}/reprocess-results.yaml')); success=[e for e in r if e['outcome']=='success']; print(f'{len(success)}/{len(r)} reprocessed successfully')\""
```

### Failure Mode

Quarantine analysis finds no patterns — items failed for unrelated reasons. The reprocess stage still runs but the "adapted strategy" has nothing to adapt from. In this case, escalate to a more capable instrument (Opus) rather than repeating the same strategy. If the quarantine grows across self-chain iterations, the pipeline itself needs debugging, not the items.

### Composes With

Triage Gate (BLACK category feeds quarantine), Screening Cascade (rejected items go to quarantine for pattern analysis), Circuit Breaker (circuit-tripped failures enter quarantine)

---

## Clash Detection

`Status: Working` · **Source:** MEP coordination / BIM in construction, Expedition 1. **Scale:** score-level. **Iteration:** 4.

### Core Dynamic

After parallel tracks produce outputs but BEFORE integration, a dedicated stage compares all outputs for CONFLICTS — without trying to merge them. Cheaper than integration testing. Different from the contract (which prevents KNOWN conflict classes) and integration testing (which discovers conflicts empirically). Clash detection uses the OUTPUTS as inputs, overlays them, and searches for interference patterns. The scope is detection, not resolution — downstream stages handle fixes.

### When to Use / When NOT to Use

Use when parallel tracks produce artifacts that must coexist (code modules, config files, API schemas), when the contract can't anticipate all conflict modes, or when integration testing is expensive enough that catching conflicts earlier saves meaningful cost. Not when parallel tracks produce truly independent artifacts, when the contract is exhaustive, or when parallel work is done by the same agent.

### Marianne Score Structure

```yaml
sheets:
  - name: track-work
    instances: 4
    prompt: "Build component {{ instance_id }}."
  - name: clash-scan
    prompt: >
      Read ALL track outputs. Search for naming collisions, interface mismatches,
      resource conflicts. Write clash-report.yaml with {clashes: [{type, items, detail}], clash_count: N}.
    capture_files: ["track-*/**"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; r=yaml.safe_load(open('{{ workspace }}/clash-report.yaml')); c=r.get('clash_count',0) if r else 0; assert c==0, f'{c} clashes found'\""
  - name: integrate
    prompt: "Assemble all track outputs."
    capture_files: ["track-*/**"]
```

### Failure Mode

Clash detection finds conflicts — the assertion fails, blocking integration. This is the INTENDED behavior. The score author must add a resolution stage after clash-scan that fixes conflicts and re-runs the scan. If clash-report.yaml is malformed or missing the `clash_count` key, the validation fails with a clear assertion error rather than a cryptic KeyError.

### Composes With

Prefabrication (clash detection after prefab tracks), Andon Cord (clash triggers diagnostic), The Tool Chain (CLI clash detection for structural conflicts)

---

## Rashomon Gate

`Status: Working` · **Source:** Kurosawa's *Rashomon* (1950), epistemological frame analysis, Expedition 6. **Scale:** score-level. **Iteration:** 4. **Force:** Structured Disagreement.

### Core Dynamic

Every fan-out instance gets the SAME evidence but analyzes from a DIFFERENT analytical frame. Contradictions are not failures — they are data. The synthesis categorizes findings by agreement level: UNANIMOUS (high confidence), MAJORITY, SPLIT (genuine ambiguity), UNIQUE (deep insight or frame artifact). The PATTERN of agreement across frames reveals more than any single analysis.

Different from Source Triangulation (which divides sources) and plain fan-out (which divides work). The cadenza mechanism (see Glossary) maps 1:1 to instances — each instance receives a different frame file defining its analytical perspective.

### When to Use / When NOT to Use

Use for problems where the right analytical frame is unknown, security audits (attacker/defender/compliance), code review (correctness/maintainability/performance), any task where the risk is "right answer from the wrong frame." Not when frames are so similar they produce trivially similar outputs, evidence is unambiguous, or the synthesis agent can't distinguish genuine disagreement from different vocabulary.

### Marianne Score Structure

```yaml
sheets:
  - name: evidence
    prompt: "Assemble the artifact all analysts will examine."
  - name: analyze
    instances: 4
    cadenza:
      - "frame-security.md"
      - "frame-performance.md"
      - "frame-maintainability.md"
      - "frame-correctness.md"
    prompt: "Analyze the evidence through your assigned frame. Write analysis-{{ instance_id }}.md."
    capture_files: ["evidence/**"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/analysis-{{ instance_id }}.md"
  - name: triangulate
    prompt: >
      Read all analyses. For EACH finding across all frames, categorize:
      UNANIMOUS (all frames agree), MAJORITY (most agree), SPLIT (even division), UNIQUE (one frame only).
      Write triangulation.yaml: {findings: [{finding, category, frames_agreeing, detail}], summary_counts: {unanimous: N, majority: N, split: N, unique: N}}.
    capture_files: ["analysis-*.md"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; t=yaml.safe_load(open('{{ workspace }}/triangulation.yaml')); assert len(t.get('findings',[])) > 0, 'No findings categorized'\""
```

### Failure Mode

Frames too similar produce trivially UNANIMOUS results — the gate adds cost without insight. Frames too dissimilar produce all UNIQUE results — no agreement signal to act on. The optimal frame set produces a mix of categories. If the validation only checks for keyword presence (UNANIMOUS/SPLIT), an agent can write the keywords without doing the categorization. The `command_succeeds` validation checking finding count prevents this.

### Composes With

Source Triangulation (Rashomon for frames, triangulation for sources), Sugya Weave (weave the triangulated findings into a position), Commander's Intent Envelope (frame IS the intent for each instance)

---

## Graceful Retreat

`Status: Working` · **Source:** Military phased withdrawal, Netflix degradation, Expedition 5. **Scale:** score-level. **Iteration:** 4. **Force:** Graceful Failure.

### Core Dynamic

Defines TIERS OF COMPLETENESS upfront. Tier 1: full output, all sections. Tier 2: core sections only. Tier 3: summary-only with pointers to what couldn't be completed. Each tier has its own validation criteria. If Tier 1 fails, the agent falls back to Tier 2 rather than failing entirely. The retreat is PLANNED — tiers defined in the prompt, not discovered during failure.

**Enforcement note:** Tier achievement is self-reported by the agent. For structural enforcement, a downstream CLI validation sheet should independently verify which tier's criteria are met, rather than trusting the agent's `tier_achieved` claim.

### When to Use / When NOT to Use

Use for long-running sheets where partial output has value, hard deadlines where "something by Tuesday" beats "perfection by Thursday," or pipeline stages where downstream can operate on partial input. Not when partial output is dangerous (security audits, financial calculations) or downstream can't distinguish "complete but simple" from "incomplete due to retreat."

### Marianne Score Structure

```yaml
sheets:
  - name: execute
    prompt: |
      TIER 1 (attempt first): Full analysis with all 5 sections (overview, architecture, security, performance, recommendations).
      TIER 2 (if Tier 1 fails): 3 core sections (overview, architecture, recommendations).
      TIER 3 (if Tier 2 fails): Executive summary with top-3 issues only.

      Write completion-status.yaml: {tier_achieved: 1|2|3, sections_completed: [], sections_skipped: [], reason}.
      Write the analysis to analysis.md.
    validations:
      - type: file_exists
        path: "{{ workspace }}/completion-status.yaml"
      - type: file_exists
        path: "{{ workspace }}/analysis.md"
  - name: verify-tier
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; s=yaml.safe_load(open('{{ workspace }}/completion-status.yaml')); tier=s['tier_achieved']; content=open('{{ workspace }}/analysis.md').read(); checks={'overview' in content.lower(), 'architecture' in content.lower()}; assert all(checks), f'Tier {tier} claimed but missing core sections'\""
```

### Failure Mode

Agent always retreats to Tier 3 because it's easiest — the retreat becomes the default. Validate that Tier 1 was genuinely attempted (check for partial Tier 1 artifacts). If downstream stages can't adapt to different tiers, the retreat produces useless partial output — ensure downstream reads `completion-status.yaml` and adjusts expectations.

### Composes With

Andon Cord (retreat triggers diagnostic), Dead Letter Quarantine (Tier 3 outputs enter quarantine for enhanced reprocessing), Cathedral Construction (retreat within a single iteration, continue next)

---

## Source Triangulation

`Status: Working` · **Source:** Journalism, intelligence analysis. **Forces:** Information Asymmetry.

### Core Dynamic

Multiple agents analyze the SAME problem from DIFFERENT sources. The synthesis identifies: corroborated (multiple sources agree), uncorroborated (single source), and contradicted (sources disagree). Different from Rashomon Gate (which uses different frames on same evidence). Source Triangulation divides the evidence itself.

### When to Use / When NOT to Use

Use when claims need independent verification and multiple source types exist. Not when a single authoritative source suffices.

### Marianne Score Structure

```yaml
sheets:
  - name: investigate
    instances: 3
    cadenza:
      - "source-code.md"
      - "source-docs.md"
      - "source-tests.md"
    prompt: "Analyze from your assigned source. Write findings-{{ instance_id }}.md."
    validations:
      - type: file_exists
        path: "{{ workspace }}/findings-{{ instance_id }}.md"
  - name: triangulate
    prompt: >
      Read all findings. Categorize each claim: CORROBORATED (2+ sources),
      UNCORROBORATED (1 source), CONTRADICTED (sources disagree).
    capture_files: ["findings-*.md"]
```

### Failure Mode

Sources too similar produce trivially corroborated results. Ensure sources are structurally independent.

### Composes With

Rashomon Gate, Triage Gate, Sugya Weave

---

## Talmudic Page

`Status: Working` · **Source:** Talmudic commentary layout (Mishnah + Gemara + commentaries). **Forces:** Information Asymmetry.

### Core Dynamic

A central text surrounded by commentary layers at different levels of abstraction. The central text anchors all commentary; each layer responds to the text AND to other layers. Produces interlinked multi-perspective analysis without losing the central thread.

### When to Use / When NOT to Use

Use when a primary artifact needs multi-layer annotation. Not when commentaries are independent (use plain Fan-out).

### Marianne Score Structure

```yaml
sheets:
  - name: central-text
    prompt: "Write the core analysis."
  - name: commentary
    instances: 3
    prompt: "Read the core analysis. Write commentary from your perspective."
    capture_files: ["core-analysis.md"]
  - name: interlink
    prompt: "Read core + all commentaries. Write cross-referenced synthesis."
    capture_files: ["core-analysis.md", "commentary-*.md"]
```

### Failure Mode

Commentaries ignore each other and respond only to the central text. The interlink stage must reference cross-commentary connections.

### Composes With

Sugya Weave, Fan-out + Synthesis

---

## Forward Observer

`Status: Working` · **Source:** Military forward observation. **Forces:** Finite Resources + Information Asymmetry.

### Core Dynamic

A cheap, fast observer (instrument: haiku or sonnet) reads large input and produces a compressed brief for the expensive operator (instrument: opus). Reduces context window pressure and cost. The observer cost must save more tokens downstream than it consumes.

### When to Use / When NOT to Use

Use when input is too large for the main instrument or when cheap summarization preserves actionable information. Not when all information is critical.

### Marianne Score Structure

```yaml
sheets:
  - name: observe
    instrument: haiku
    prompt: "Read the full input. Write observer-brief.md: key findings, actionable items only."
    validations:
      - type: file_exists
        path: "{{ workspace }}/observer-brief.md"
  - name: operate
    instrument: opus
    prompt: "Read observer-brief.md. Execute the detailed analysis."
    capture_files: ["observer-brief.md"]
```

### Failure Mode

Observer discards critical information. Validate by checking brief covers all major topics from the input.

### Composes With

Relay Zone, Screening Cascade, Immune Cascade

---

## Closed-Loop Call

`Status: Working` · **Source:** Aviation CRM callout-response protocol. **Forces:** Producer-Consumer Mismatch + Partial Failure.

### Core Dynamic

Explicit handoff verification between stages. Stage A produces output. Stage B reads it and writes back a confirmation of what it understood. A CLI validation compares the two. Prevents semantic drift across pipeline stages.

### When to Use / When NOT to Use

Use when handoff fidelity is critical and semantic drift is a real risk. Not when stages are trivially compatible.

### Marianne Score Structure

```yaml
sheets:
  - name: produce
    prompt: "Write output with manifest.yaml listing key decisions."
  - name: consume
    prompt: "Read output. Write readback.yaml confirming your understanding of each decision."
    capture_files: ["manifest.yaml", "output/**"]
  - name: verify
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; m=yaml.safe_load(open('{{ workspace }}/manifest.yaml')); r=yaml.safe_load(open('{{ workspace }}/readback.yaml')); assert set(m.keys())==set(r.keys()), f'Key mismatch: {set(m.keys())-set(r.keys())}'\""
```

### Failure Mode

Readback is verbatim copy, not comprehension check. The validation should check structural understanding, not string matching.

### Composes With

Relay Zone, Prefabrication, Succession Pipeline

---

## Sugya Weave (Editorial Synthesis)

`Status: Working` · **Source:** Talmudic sugya structure. **Forces:** Information Asymmetry + Convergence Imperative.

### Core Dynamic

Not just synthesis — editorial synthesis. The weaver takes a POSITION on the inputs, arguing for one interpretation while acknowledging alternatives. Produces an opinionated conclusion, not a summary. Requires structured validation that the position is supported.

### When to Use / When NOT to Use

Use when diverse inputs need an authoritative position, not just aggregation. Not when neutrality is required.

### Marianne Score Structure

```yaml
sheets:
  - name: weave
    prompt: >
      Read all inputs. Take a position. Write editorial-synthesis.md with:
      POSITION, SUPPORTING EVIDENCE, COUNTERARGUMENTS, CONCLUSION.
    capture_files: ["input-*.md"]
    validations:
      - type: content_contains
        content: "POSITION:"
      - type: content_contains
        content: "COUNTERARGUMENTS:"
```

### Failure Mode

Position is unsupported assertion. Validate that supporting evidence references specific inputs.

### Composes With

Fan-out + Synthesis, Source Triangulation, Rashomon Gate

---

## Barn Raising

`Status: Working` · **Source:** Community barn raising (Amish). **Forces:** Producer-Consumer Mismatch + Finite Resources.

### Core Dynamic

Shared conventions established before parallel work. A conventions document defines naming, structure, interfaces. All parallel tracks read it. Different from Prefabrication (which defines interfaces). Barn Raising defines conventions — broader scope, softer constraints.

### When to Use / When NOT to Use

Use when parallel agents need consistency beyond interface contracts. Not when a single agent does all work.

### Marianne Score Structure

```yaml
sheets:
  - name: conventions
    prompt: "Write conventions.md: naming rules, file structure, code style."
    validations:
      - type: file_exists
        path: "{{ workspace }}/conventions.md"
  - name: build
    instances: 6
    prompt: "Read conventions.md. Build component {{ instance_id }}."
    capture_files: ["conventions.md"]
```

### Failure Mode

Conventions too vague to enforce consistency. Too rigid to allow agent judgment. Strike the balance based on integration requirements.

### Composes With

Prefabrication, Mission Command, Lines of Effort

---

## Nurse Log

`Status: Working` · **Source:** Forest ecology (nurse logs). **Forces:** Finite Resources.

### Core Dynamic

A preparation stage creates general-purpose substrate (research, data collection, organization) that makes downstream stages more productive. Different from Reconnaissance Pull (which discovers the approach). Nurse Log prepares the ground regardless of approach.

### When to Use / When NOT to Use

Use when downstream stages share common preparation needs. Not when preparation is stage-specific.

### Marianne Score Structure

```yaml
sheets:
  - name: prepare-substrate
    prompt: "Research the domain. Collect reference material. Organize into substrate/."
    validations:
      - type: file_exists
        path: "{{ workspace }}/substrate/"
  - name: work
    instances: 4
    prompt: "Read substrate/. Build component {{ instance_id }}."
    capture_files: ["substrate/**"]
```

### Failure Mode

Substrate too generic to help. Make preparation specific to the downstream work, not a generic research dump.

### Composes With

Fermentation Relay, Fan-out + Synthesis

---

# Concert-Level Patterns

## Lines of Effort

`Status: Working (single-score approximation)` · **Source:** Military operational design (JP 5-0). **Forces:** Information Asymmetry + Finite Resources.

### Core Dynamic

Sustained parallel campaigns with different objectives converging toward a unified end state. Each line has its own scores, instruments, and success criteria. Coordination through shared workspace state, not message passing. Requires concert-level orchestration with multiple scores.

### When to Use / When NOT to Use

Use for large campaigns with distinct workstreams that must converge. Not when workstreams are independent or campaign is short.

### Marianne Score Structure

```yaml
# Single-score approximation — true Lines of Effort requires a concert
sheets:
  - name: define-lines
    prompt: "Define 3 lines of effort with objectives and convergence criteria."
  - name: line-work
    instances: 3
    prompt: "Execute line {{ instance_id }} per the defined objectives."
    capture_files: ["lines-definition.md"]
  - name: convergence-check
    prompt: "Read all line outputs. Assess convergence toward unified end state."
    capture_files: ["line-*/**"]
```

### Failure Mode

Lines diverge without convergence checks. Regular synchronization points are essential.

### Composes With

Season Bible, After-Action Review, Barn Raising

---

## Season Bible

`Status: Working` · **Source:** Television production (show bible). **Forces:** Producer-Consumer Mismatch.

### Core Dynamic

A mutable reference document that evolves as the campaign progresses. Different from Barn Raising conventions (which are static). The bible records decisions, character evolutions, and continuity constraints. Scores read it before starting and update it after completing.

### When to Use / When NOT to Use

Use for multi-score campaigns needing continuity. Not for single-score work.

### Marianne Score Structure

```yaml
sheets:
  - name: read-bible
    prompt: "Read season-bible.md. Note current state and constraints."
    capture_files: ["season-bible.md"]
  - name: work
    prompt: "Execute work respecting bible constraints."
  - name: update-bible
    prompt: "Update season-bible.md with new decisions and state changes."
    validations:
      - type: content_contains
        path: "{{ workspace }}/season-bible.md"
        content: "Updated:"
```

### Failure Mode

Bible grows stale — scores read it but don't update. Validate update stage actually modifies the bible.

### Composes With

Lines of Effort, Relay Zone, Cathedral Construction

---

## Saga Compensation Chain

`Status: Aspirational [on_failure compensation actions]` · **Source:** Garcia-Molina & Salem (1987), distributed transactions, Expedition 4. **Scale:** concert-level. **Iteration:** 4. **Force:** Graceful Failure.

### Core Dynamic

Every forward score in a concert is paired with a compensating score. If score Tk fails, compensations run Ck-1, Ck-2, ..., C1 in reverse order — not rollback (commits already happened) but forward-acting undo. The compensation isn't "delete what you made" — it's a score that produces artifacts neutralizing the forward score's effects.

**Implementation status:** Marianne does not yet have `on_failure` actions. The pattern can be approximated today with: (1) each forward score writes to `saga-log.yaml` documenting its side effects and compensation path, (2) on manual detection of failure, the user runs a separate compensation score that reads the saga log and undoes in reverse order.

### When to Use / When NOT to Use

Use for multi-score concerts where each score produces side effects on shared state, when partial completion is worse than full rollback, or when manual cleanup cost exceeds compensation engineering cost. Not when scores are idempotent, when scores don't produce side effects beyond workspace files, or when the concert is short enough for manual recovery.

### Marianne Score Structure

```yaml
# Forward score — writes to saga log for compensation context
sheets:
  - name: forward-step
    prompt: >
      Execute the migration step. Append to saga-log.yaml:
      {step: "schema-migration", artifacts: [...], side_effects: [...], compensation: "revert-schema.yaml"}.
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; log=yaml.safe_load(open('{{ workspace }}/saga-log.yaml')); assert len(log) > 0\""

# Compensation score (run manually or via future on_failure)
# sheets:
#   - name: compensate
#     prompt: "Read saga-log.yaml. For each entry in REVERSE order, execute the compensation."
#     capture_files: ["saga-log.yaml"]
```

### Failure Mode

Compensation scores can also fail — producing "compensation failure" on top of the original failure. Keep compensations simple and idempotent. The saga log must be written BEFORE side effects, not after — otherwise a crash between effect and log entry leaves uncompensatable state.

### Composes With

After-Action Review (compensation log feeds AAR), Look-Ahead Window (pre-check compensation score availability)

---

## Progressive Rollout

`Status: Working` · **Source:** DevOps graduated deployment, feature flags, Expedition 5. **Scale:** concert-level. **Iteration:** 4. **Force:** Progressive Commitment.

### Core Dynamic

Apply a change in PHASES with increasing scope. Each phase's success GATES the next. Each phase's monitoring INFORMS the next's parameters. Different from Canary Probe (probe-then-full). Progressive Rollout is probe → 10% → 25% → 50% → 100%.

**Implementation note:** Marianne's `instances` field is static per score execution. The rollout achieves graduated scaling through the select-batch sheet: each self-chain iteration reads `rollout-state.yaml` to determine which items are in the current batch. The instance count stays fixed (e.g., 5 parallel workers), but the batch selection grows across iterations.

### When to Use / When NOT to Use

Use for large-scale migrations, multi-repository changes, any operation where "works on 5" doesn't guarantee "works on 500." Not when items are not independent or monitoring can't distinguish success from luck.

### Marianne Score Structure

```yaml
sheets:
  - name: select-batch
    prompt: >
      Read rollout-state.yaml (or initialize if first run).
      Select the next batch: phase 1 = 3 items, phase 2 = 20, phase 3 = 80, phase 4 = remainder.
      Write current-batch.yaml and update rollout-state.yaml with phase number and processed items.
    capture_files: ["rollout-state.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/current-batch.yaml"
  - name: execute-batch
    instances: 5
    prompt: "Read current-batch.yaml. Process items assigned to worker {{ instance_id }}."
    capture_files: ["current-batch.yaml"]
  - name: monitor
    prompt: >
      Compute health metrics for this phase. Write phase-verdict.yaml:
      {go: bool, phase: N, confidence, items_processed, items_remaining, error_rate}.
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; v=yaml.safe_load(open('{{ workspace }}/phase-verdict.yaml')); assert v.get('go', False), f'Phase {v.get(\"phase\")} failed: error_rate={v.get(\"error_rate\")}'\""
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 10
```

### Failure Mode

Early phases pass with small samples but later phases fail at scale — sampling bias. If error rate exceeds threshold at any phase, the rollout pauses (self-chain breaks on failed validation) and Dead Letter Quarantine analyzes failures. `max_chain_depth` prevents infinite rollout if the termination condition (`items_remaining == 0`) isn't reached.

### Composes With

Canary Probe (canary IS phase 1), Dead Letter Quarantine (failed items in each phase), Stratification Gate (N consecutive healthy phases before advancing)

---

## Systemic Acquired Resistance

`Status: Working` · **Source:** Plant immune priming (SAR/ISR), Expedition 2. **Scale:** concert-level. **Iteration:** 4. **Force:** Accumulated Signal.

### Core Dynamic

When a score recovers from a failure, it broadcasts failure-derived defenses to all subsequent scores via structured `priming/` directory. Primed scores CHANGE BEHAVIOR — adjusting prompts, validation thresholds, or monitoring. The priming is specific: a rate-limit encounter primes for rate-limit handling, not general defensiveness.

**Primer schema:** Each primer file in `priming/` follows: `{threat_type: string, trigger_signature: string, countermeasure: string, confidence: float, timestamp: string}`. Downstream scores read primers matching their threat surface and incorporate countermeasures into their prompts.

### When to Use / When NOT to Use

Use for concert campaigns where scores face related threat landscapes, when failure in one score should make the entire campaign more resilient, or when first-encounter failure cost is high. Not when scores face unrelated threats, the priming signal is too vague, or defense overhead degrades unaffected scores (autoimmune response — primers that are too broad cause unnecessary caution).

### Marianne Score Structure

```yaml
sheets:
  - name: work
    prompt: |
      Before starting, read priming/ for defense primers matching your work type.
      For each relevant primer, incorporate the countermeasure into your approach.

      Execute the primary task. Write output to output.md.

      If you encounter and recover from a failure, write a primer to priming/:
      File: priming/{threat_type}.yaml
      Schema: {threat_type, trigger_signature, countermeasure, confidence, timestamp}.
    capture_files: ["priming/*.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/output.md"
```

### Failure Mode

Primers too broad cause autoimmune response — every score wastes tokens on irrelevant defenses. Primers too narrow never match. The `trigger_signature` field is the key: specific enough to match real threats, broad enough to generalize. If primers accumulate without pruning, the priming directory becomes noise. Include a `confidence` field and prune low-confidence primers after N uses without trigger.

### Composes With

After-Action Review (primers are structured AAR output), Back-Slopping (priming IS culture inheritance across scores), Circuit Breaker (primer from circuit-tripped instrument)

---

# Communication Patterns

## Stigmergic Workspace

`Status: Working` · **Source:** Ant colony optimization. **Forces:** Information Asymmetry + Finite Resources.

### Core Dynamic

Agents coordinate through workspace artifacts, not direct communication. Agent A writes a file; Agent B reads it. No messages, no coordination protocol — the workspace IS the communication channel.

### When to Use / When NOT to Use

Use when agents need loose coordination and the workspace captures state. Not when real-time coordination is needed.

### Marianne Score Structure

```yaml
sheets:
  - name: work
    instances: 8
    prompt: >
      Read workspace for current state. Do your work. Write results.
      If you find something relevant to other workers, write it to shared/signals/.
    capture_files: ["shared/signals/**"]
```

### Failure Mode

Conflicting writes to the same file. Use namespaced output directories per instance.

### Composes With

Barn Raising, Lines of Effort

---

# Adaptation Patterns

## Read-and-React

`Status: Working` · **Source:** Basketball read-and-react offense. **Forces:** Partial Failure + Information Asymmetry.

### Core Dynamic

Downstream stages read workspace state and adapt their behavior. Not conditional branching (which requires conductor support) but workspace-driven behavioral adaptation within a sheet's prompt.

### When to Use / When NOT to Use

Use when downstream behavior should adapt to upstream results. Not when the adaptation path is known upfront.

### Marianne Score Structure

```yaml
sheets:
  - name: work
    prompt: >
      Read previous outputs. Based on what you find:
      - If analysis-complete.yaml exists: proceed to synthesis.
      - If analysis-complete.yaml is missing: extend analysis first.
    capture_files: ["analysis-*.md", "analysis-complete.yaml"]
```

### Failure Mode

Agent ignores the workspace state and proceeds with default behavior. Validate that the expected adaptation actually occurred.

### Composes With

Triage Gate, FRAGO, Dormancy Gate

---

## Dormancy Gate

`Status: Working` · **Source:** Seed dormancy in botany. **Forces:** Finite Resources.

### Core Dynamic

A gate that waits for external conditions before proceeding. The gate checks workspace state — if conditions aren't met, the score self-chains and checks again. Unlike a validation (which fails the score), dormancy gates pause and retry.

### When to Use / When NOT to Use

Use when work depends on external conditions that will eventually be met. Not when conditions are already known.

### Marianne Score Structure

```yaml
sheets:
  - name: check-conditions
    instrument: cli
    validations:
      - type: command_succeeds
        command: "test -f {{ workspace }}/external-data-ready.flag"
  - name: proceed
    prompt: "Conditions met. Begin processing."
    capture_files: ["external-data/**"]
```

### Failure Mode

External condition never materializes. `max_chain_depth` provides a safety bound.

### Composes With

Read-and-React, Shipyard Sequence

---

## Reconnaissance Pull

`Status: Working` · **Source:** Military reconnaissance doctrine. **Forces:** Information Asymmetry.

### Core Dynamic

A cheap, fast reconnaissance stage discovers the landscape before committing to a plan. The recon output is advisory — downstream stages read it and adapt. Different from Forward Observer (which compresses). Reconnaissance discovers.

### When to Use / When NOT to Use

Use when the approach isn't obvious and exploration is cheap. Not when the task is well-understood.

### Marianne Score Structure

```yaml
sheets:
  - name: recon
    instrument: sonnet
    prompt: "Survey the input. Write recon-report.md: structure, complexity, risks, recommended approach."
    validations:
      - type: file_exists
        path: "{{ workspace }}/recon-report.md"
  - name: plan
    prompt: "Read recon-report.md. Write execution plan."
    capture_files: ["recon-report.md"]
  - name: execute
    prompt: "Execute per plan."
    capture_files: ["execution-plan.md"]
```

### Failure Mode

Recon is too shallow to inform planning. Use a more capable instrument for recon if the domain is complex.

### Composes With

Mission Command, Canary Probe

---

## Fragmentary Order (FRAGO)

`Status: Working` · **Source:** Military fragmentary orders. **Forces:** Partial Failure.

### Core Dynamic

Mid-execution course correction via cadenza injection. When earlier stages produce unexpected results, a FRAGO sheet writes a correction document that downstream stages read. Not replanning — targeted adjustments to the existing plan.

### When to Use / When NOT to Use

Use when plans need mid-execution adjustment based on discovered conditions. Not when the plan is too broken for incremental fixes.

### Marianne Score Structure

```yaml
sheets:
  - name: assess
    prompt: "Read outputs so far. Identify deviations from plan. Write frago.md if corrections needed."
    capture_files: ["execution-plan.md", "progress/**"]
  - name: continue
    prompt: "Read frago.md if it exists. Adjust approach per corrections."
    capture_files: ["frago.md", "execution-plan.md"]
```

### Failure Mode

FRAGO contradicts the original plan too severely. Downstream agents can't reconcile. Keep corrections incremental.

### Composes With

Read-and-React, Lines of Effort, Mission Command

---

## After-Action Review

`Status: Working` · **Source:** US Army AAR protocol. **Forces:** Information Asymmetry + Partial Failure.

### Core Dynamic

Dedicated review stage after execution. Not quality checking (that's validation). AAR asks: what was supposed to happen, what actually happened, why the difference, what to change. The AAR output feeds the next iteration's prelude.

### When to Use / When NOT to Use

Use after any significant execution to capture learning. Not for trivial tasks.

### Marianne Score Structure

```yaml
sheets:
  - name: aar
    prompt: >
      Read all execution outputs. Write aar.md:
      INTENDED: what the score was supposed to produce.
      ACTUAL: what was actually produced.
      DELTA: why the difference.
      SUSTAIN: what worked.
      IMPROVE: what to change next time.
    capture_files: ["**"]
    validations:
      - type: content_contains
        content: "SUSTAIN:"
      - type: content_contains
        content: "IMPROVE:"
```

### Failure Mode

AAR is generic platitudes. Validate specific references to actual outputs and concrete improvement recommendations.

### Composes With

Immune Cascade, Cathedral Construction, Back-Slopping

---

## Andon Cord

`Status: Working` · **Source:** Toyota Production System stop-the-line, Expedition 1. **Scale:** adaptation. **Iteration:** 4. **Force:** Graceful Failure.

### Core Dynamic

Replaces blind retry with diagnostic intervention. On validation failure: detect → stop (don't retry blindly) → diagnose (dedicated diagnostic sheet reads failure output) → fix (inject diagnosis as cadenza) → resume (re-run with new context). Transforms failure response from stochastic retry to deterministic diagnosis.

**Relationship to self-healing:** Marianne's conductor-level self-healing feature implements a similar detect-diagnose-fix loop. Andon Cord is the score-level pattern — you compose it explicitly in your YAML. Self-healing is the conductor-level implementation that applies automatically. Both exist at different abstraction levels.

### When to Use / When NOT to Use

Use when failures are diagnostic (agent misunderstood the task, missed a constraint), when failure output contains enough information to diagnose root cause, or when retry cost justifies a diagnostic stage (~$1+ per attempt). Not when failures are stochastic (network timeouts — just retry), failure output is empty, or diagnosis cost exceeds a few blind retries.

### Marianne Score Structure

```yaml
sheets:
  - name: generate
    prompt: "Generate the REST API implementation."
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && pytest -x 2>&1 | tee {{ workspace }}/test-output.log; exit ${PIPESTATUS[0]}"
  - name: diagnose
    prompt: |
      The previous stage failed validation. Read the failed output and test results.
      Write andon-diagnosis.md with:
      ROOT CAUSE: (what specifically went wrong)
      FIX PLAN: (concrete steps to fix)
    capture_files: ["**/*.py", "test-output.log"]
    validations:
      - type: content_contains
        content: "ROOT CAUSE:"
      - type: content_contains
        content: "FIX PLAN:"
  - name: regenerate
    prompt: "Read andon-diagnosis.md. Fix the identified issue. Do not rewrite from scratch."
    capture_files: ["andon-diagnosis.md", "**/*.py"]
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && pytest -x"
```

### Failure Mode

Diagnosis is wrong — the root cause analysis misidentifies the problem, and the fix introduces new failures. Validate that the regenerated output passes the SAME validation that the original failed. If diagnosis consistently fails, fall back to a more capable instrument for the diagnostic sheet (Opus for triage, per CEGAR Loop strategy).

### Composes With

Circuit Breaker (andon for task failure, circuit breaker for instrument failure), Quorum Trigger (quorum triggers andon), Commissioning Cascade (andon at each commissioning tier)

---

## Circuit Breaker

`Status: Working` · **Source:** Nygard's "Release It!" (2007), Netflix Hystrix, Expedition 5. **Scale:** adaptation. **Iteration:** 4. **Force:** Graceful Failure.

### Core Dynamic

After N instrument failures, STOP TRYING. Three states: Closed (normal — route to primary instrument), Open (all requests use fallback immediately — zero cost on broken instrument), Half-Open (one probe request — if it succeeds, close; if it fails, reopen). The critical distinction: instrument failure vs. task failure. A circuit breaker on "agent produced bad output" would shut down the pipeline. This is for infrastructure failures — backends crashing, APIs timing out, models OOM-ing.

**Stateful implementation:** The circuit state persists in `circuit-state.yaml` across self-chain iterations. Each execution reads the state, makes routing decisions, and updates the state. The self-chain carries the state forward via `inherit_workspace`.

### When to Use / When NOT to Use

Use for scores using unreliable instruments (external APIs, local models), long-running concerts where backends may degrade mid-execution, or self-chaining scores where instruments become unavailable. Not when failure is in the TASK (not the instrument), when only one instrument is available, or for short scores where manual intervention is faster.

### Marianne Score Structure

```yaml
sheets:
  - name: check-circuit
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml,os; s=yaml.safe_load(open('{{ workspace }}/circuit-state.yaml')) if os.path.exists('{{ workspace }}/circuit-state.yaml') else {'state':'closed','failures':0}; print(f'Circuit: {s[\"state\"]}, failures: {s[\"failures\"]}')\""
  - name: health-probe
    instrument: ollama
    prompt: "Health check. Write probe-result.yaml: {status: ok|fail, latency_ms, error}."
    validations:
      - type: file_exists
        path: "{{ workspace }}/probe-result.yaml"
  - name: route-work
    prompt: >
      Read circuit-state.yaml and probe-result.yaml.
      If circuit CLOSED and probe OK: execute with primary instrument (ollama). Write to primary-output/.
      If circuit OPEN or probe FAIL: execute with fallback instrument (claude). Write to fallback-output/.
      Update circuit-state.yaml: {state, failures, last_check, last_transition}.
    capture_files: ["circuit-state.yaml", "probe-result.yaml"]
  - name: consolidate
    prompt: "Merge results from whichever path completed."
    capture_files: ["primary-output/**", "fallback-output/**"]
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 20
```

### Failure Mode

Circuit opens permanently because the health probe itself is too sensitive (marks transient failures as outages). Use a failure count threshold (e.g., 3 consecutive failures) before opening. If the fallback instrument also fails, the circuit breaker can't help — escalate to Dead Letter Quarantine.

### Composes With

Dead Letter Quarantine (circuit-tripped items go to quarantine), Echelon Repair (circuit breaker per echelon), Speculative Hedge (backup instrument IS the hedge)

---

# Instrument Strategy Patterns

## Echelon Repair

`Status: Working` · **Source:** Military echelon maintenance. **Forces:** Instrument-Task Fit + Finite Resources.

### Core Dynamic

Graduated instrument assignment. Easy work goes to cheap/fast instruments. Hard work escalates to expensive/capable instruments. The classification stage determines difficulty BEFORE assignment.

### When to Use / When NOT to Use

Use when work items vary in difficulty and instruments vary in cost/capability. Not when all work is equally complex.

### Marianne Score Structure

```yaml
sheets:
  - name: classify
    instrument: haiku
    prompt: "Read each item. Classify difficulty: E1 (simple), E2 (moderate), E3 (complex). Write echelon-manifest.yaml."
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; m=yaml.safe_load(open('{{ workspace }}/echelon-manifest.yaml')); assert all(e['echelon'] in ['E1','E2','E3'] for e in m)\""
  - name: e1-repair
    instrument: haiku
    prompt: "Process E1 items from echelon-manifest.yaml."
    capture_files: ["echelon-manifest.yaml"]
  - name: e2-repair
    instrument: sonnet
    prompt: "Process E2 items."
    capture_files: ["echelon-manifest.yaml"]
  - name: e3-repair
    instrument: opus
    prompt: "Process E3 items."
    capture_files: ["echelon-manifest.yaml"]
```

### Failure Mode

Misclassification: E3 items assigned to E1. Validate E1 output quality; escalate failures to E2.

### Composes With

Commissioning Cascade, Fermentation Relay, Screening Cascade, Circuit Breaker

---

## Fermentation Relay

`Status: Working` · **Source:** Fermentation microbiology. **Forces:** Instrument-Task Fit.

### Core Dynamic

Cheap instruments do initial processing; expensive instruments refine. The pipeline is fixed in YAML. "Substrate-driven" refers to how you design the gate between stages, not runtime switching.

### When to Use / When NOT to Use

Use when early stages benefit from fast/cheap processing and later stages need precision. Not when all stages need the same capability.

### Marianne Score Structure

```yaml
sheets:
  - name: extract
    instrument: haiku
    prompt: "Extract raw information. Write extraction.md."
    validations:
      - type: file_exists
        path: "{{ workspace }}/extraction.md"
  - name: refine
    instrument: sonnet
    prompt: "Refine extraction. Resolve ambiguities."
    capture_files: ["extraction.md"]
  - name: polish
    instrument: opus
    prompt: "Final quality pass. Produce polished output."
    capture_files: ["refined.md"]
```

### Failure Mode

Early cheap stages produce such poor output that expensive stages spend all their budget fixing garbage. Validate intermediate quality.

### Composes With

Echelon Repair, Succession Pipeline, Screening Cascade

---

## Screening Cascade

`Status: Working` · **Source:** Medical screening. **Forces:** Instrument-Task Fit + Finite Resources.

### Core Dynamic

Batch processing with escalating instruments at each stage. Stage 1 screens with cheap instrument, passes ambiguous cases to Stage 2 with more capable instrument, and so on. Different from Echelon Repair (which classifies upfront): Screening Cascade discovers difficulty through progressive screening.

### When to Use / When NOT to Use

Use when difficulty isn't classifiable upfront but emerges during processing. Not when all items need the same treatment.

### Marianne Score Structure

```yaml
sheets:
  - name: screen-1
    instrument: haiku
    prompt: "Process all items. Mark items you're uncertain about as ESCALATE. Write screen-1-results.yaml."
    validations:
      - type: file_exists
        path: "{{ workspace }}/screen-1-results.yaml"
  - name: screen-2
    instrument: sonnet
    prompt: "Process ESCALATE items from screen-1. Mark remaining uncertain as ESCALATE-2."
    capture_files: ["screen-1-results.yaml"]
  - name: screen-3
    instrument: opus
    prompt: "Process ESCALATE-2 items."
    capture_files: ["screen-2-results.yaml"]
```

### Failure Mode

Stage 1 escalates everything (no screening value). Validate escalation rates: if >50% escalate, the screening threshold is too conservative.

### Composes With

Echelon Repair, Immune Cascade, Dead Letter Quarantine

---

## Vickrey Auction

`Status: Working (two-run approximation)` · **Source:** Vickrey auction theory. **Forces:** Instrument-Task Fit.

### Core Dynamic

Competitive probing: run the same task on multiple instruments, evaluate which performed best, use that instrument for the full run. The probing informs the NEXT run, not this one — dynamic instrument selection requires either a two-score concert or human-in-the-loop step.

### When to Use / When NOT to Use

Use when multiple instruments are available and it's unclear which performs best. Not when one instrument is clearly superior.

### Marianne Score Structure

```yaml
sheets:
  - name: probe-haiku
    instrument: haiku
    prompt: "Process the sample item. Write probe-haiku.md."
  - name: probe-sonnet
    instrument: sonnet
    prompt: "Process the same sample item. Write probe-sonnet.md."
  - name: evaluate
    prompt: "Compare probe outputs. Write instrument-recommendation.yaml: {winner, rationale}."
    capture_files: ["probe-haiku.md", "probe-sonnet.md"]
```

### Failure Mode

Probe item isn't representative of the full workload. Use multiple probe items.

### Composes With

Echelon Repair, Canary Probe

---

## Composting Cascade

`Status: Working` · **Source:** Four-phase composting microbiology, Expedition 2. **Scale:** score-level + instrument strategy. **Iteration:** 4. **Force:** Threshold Accumulation.

### Core Dynamic

The work's own output drives phase transitions. CLI instruments measure workspace state ("temperature") and threshold crossings trigger phase changes. The agents don't know they're transitioning — the thermometer knows. CLI instruments are in the control loop; AI instruments are the workers.

**"Temperature" defined:** Workspace metrics that indicate readiness for the next phase. Examples: type coverage percentage (for refactoring), test pass rate (for code generation), function count per file (for extraction work). The metric must be measurable by a CLI script and meaningfully indicate phase readiness.

**Script dependencies:** `temperature.py` and `exhaustion.py` are user-supplied. Interface contract: `temperature.py --threshold N` exits 0 if temperature meets threshold, exits 1 otherwise. `exhaustion.py --max-churn N` exits 0 if change rate is below threshold (work is cooling), exits 1 otherwise.

### When to Use / When NOT to Use

Use for multi-phase projects where work nature should change based on measurable workspace state, codebase refactoring where simple cleanup enables complex restructuring, or documentation campaigns where raw generation enables consolidation. Not when workspace metrics don't reflect work state, phase transitions need human judgment, or the work is single-phase.

### Marianne Score Structure

```yaml
sheets:
  - name: simple-work
    prompt: "Execute simple cleanup tasks. Rename variables, add type hints, extract functions."
  - name: temperature-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/temperature.py --threshold 60"
  - name: complex-work
    instrument: opus
    prompt: "Execute complex restructuring. Introduce abstractions, rewrite algorithms."
    capture_files: ["temperature-report.yaml"]
  - name: cooling-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/exhaustion.py --max-churn 5"
  - name: maturation
    instrument: haiku
    prompt: "Write documentation, migration guide, changelog."
```

### Failure Mode

Temperature metric doesn't correlate with actual readiness — complex-work fires too early and fails because the codebase isn't ready. Calibrate thresholds empirically: run the pipeline once, observe when complex-work succeeds, set the threshold there. If temperature never rises (simple-work doesn't change the measured metric), the cascade stalls at the temperature check.

### Composes With

The Tool Chain (CLI instruments as thermometers), Succession Pipeline (composting IS succession with metric-driven gates), Echelon Repair (instrument escalation per phase)

---

# Iteration Patterns

## CDCL Search

`Status: Working` · **Source:** Conflict-driven clause learning (SAT solving). **Forces:** Partial Failure + Information Asymmetry.

### Core Dynamic

When a branch fails, extract WHY it failed and add the failure reason as a new constraint. The constraint prevents the same failure pattern in subsequent iterations. Learning from failure, not just retrying.

### When to Use / When NOT to Use

Use when failures are informative and recurring patterns are likely. Not when failures are random.

### Marianne Score Structure

```yaml
sheets:
  - name: attempt
    prompt: "Read learned-clauses.yaml. Attempt the task avoiding known failure patterns."
    capture_files: ["learned-clauses.yaml"]
  - name: analyze-failure
    prompt: "If attempt failed, extract failure reason. Append to learned-clauses.yaml."
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; c=yaml.safe_load(open('{{ workspace }}/learned-clauses.yaml')); print(f'{len(c)} clauses learned')\""
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 10
```

### Failure Mode

Learned clauses are too specific (don't generalize) or too broad (over-constrain). Validate clause quality.

### Composes With

Back-Slopping, After-Action Review, CEGAR Loop

---

## Fixed-Point Iteration

`Status: Working` · **Source:** Numerical analysis, compiler dataflow. **Forces:** Convergence Imperative.

### Core Dynamic

Repeat the same operation until the output stops changing. Convergence is structural: diff the output of iteration N against iteration N-1. When the diff is empty (or below threshold), stop.

### When to Use / When NOT to Use

Use when the task naturally converges (each pass finds fewer issues). Not when convergence isn't guaranteed.

### Marianne Score Structure

```yaml
sheets:
  - name: iterate
    prompt: "Read previous output. Improve. Write output."
    capture_files: ["output.md"]
  - name: convergence-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "diff {{ workspace }}/output-prev.md {{ workspace }}/output.md | wc -l | xargs test 5 -gt"
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 10
```

### Failure Mode

Never converges. `max_chain_depth` provides the safety bound.

### Composes With

CDCL Search, Cathedral Construction, Memoization Cache

---

## Cathedral Construction

`Status: Working` · **Source:** Medieval cathedral building. **Forces:** Convergence Imperative + Finite Resources.

### Core Dynamic

Long-running iterative refinement where each iteration adds structural elements. Different from Fixed-Point (which converges to stability). Cathedral Construction builds toward a known target through incremental addition.

### When to Use / When NOT to Use

Use for large artifacts that can't be produced in one pass. Not when the work is convergent (use Fixed-Point).

### Marianne Score Structure

```yaml
sheets:
  - name: plan-iteration
    prompt: "Read current state. Plan what to add this iteration."
    capture_files: ["cathedral/**"]
  - name: build
    prompt: "Execute the plan. Add to the cathedral."
  - name: inspect
    prompt: "Review what was built. Write inspection-report.md."
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 20
```

### Failure Mode

Each iteration adds but never integrates. Include integration checks in the inspection stage.

### Composes With

After-Action Review, Back-Slopping, Memoization Cache

---

## Rehearsal Spotlight

`Status: Working` · **Source:** Theater rehearsal. **Forces:** Convergence Imperative + Finite Resources.

### Core Dynamic

After each iteration, identify the weakest sections and re-run ONLY those. Focuses expensive iteration on the parts that need it most.

### When to Use / When NOT to Use

Use when iteration is expensive and only parts of the output need rework. Not when the whole output needs rework each time.

### Marianne Score Structure

```yaml
sheets:
  - name: evaluate
    prompt: "Read output. Score each section. Write spotlight-targets.yaml: sections needing rework."
    capture_files: ["output/**"]
  - name: rehearse
    instances: 3
    prompt: "Rework the targeted section. Write improved version."
    capture_files: ["spotlight-targets.yaml", "output/**"]
  - name: check-done
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/check_quality.py --min-score 8"
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 5
```

### Failure Mode

Spotlight always targets the same sections. Track which sections have been rehearsed and escalate persistent weaknesses.

### Composes With

Echelon Repair, Soil Maturity Index, CEGAR Loop

---

## Soil Maturity Index

`Status: Working` · **Source:** Soil science maturity metrics. **Forces:** Convergence Imperative.

### Core Dynamic

Domain-specific termination condition for iterative processes. Instead of "nothing changed" (Fixed-Point) or "all sections pass" (Rehearsal Spotlight), the maturity index measures a qualitative shift — the output has changed CHARACTER, not just improved. A script-driven exit code determines termination.

### When to Use / When NOT to Use

Use when convergence is qualitative (the writing style matured, the architecture became cohesive). Not when convergence is structural.

### Marianne Score Structure

```yaml
sheets:
  - name: iterate
    prompt: "Read output. Improve based on maturity criteria."
    capture_files: ["output/**"]
  - name: maturity-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/maturity_assessor.py --output {{ workspace }}/output.md"
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 10
```

### Failure Mode

Maturity metric doesn't capture the intended qualitative shift. Iterate on the assessor, not just the output.

### Composes With

Fixed-Point Iteration, Back-Slopping, Delphi Convergence

---

## Delphi Convergence

`Status: Working` · **Source:** Delphi method (RAND Corporation). **Forces:** Convergence Imperative + Information Asymmetry.

### Core Dynamic

Multiple agents independently assess, then converge through structured rounds. Different from Fan-out + Synthesis (one round). Delphi iterates until convergence — each round shares anonymized prior assessments, allowing agents to update positions.

### When to Use / When NOT to Use

Use when independent expert judgment needs convergence. Not when a single assessment suffices.

### Marianne Score Structure

```yaml
sheets:
  - name: assess
    instances: 3
    prompt: "Read prior round results if they exist. Write your independent assessment."
    capture_files: ["round-*/**"]
  - name: check-convergence
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/check_convergence.py --threshold 0.8"
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 5
```

### Failure Mode

Agents anchor on first-round assessments and never genuinely update. Validate that positions actually change between rounds.

### Composes With

Source Triangulation, Rashomon Gate

---

## Back-Slopping (Learning Inheritance)

`Status: Working` · **Source:** Sourdough bread making. **Forces:** Convergence Imperative.

### Core Dynamic

Each iteration inherits a "culture" artifact from the previous iteration containing accumulated learning. The culture grows and refines over iterations, carrying forward what worked and what to avoid.

### When to Use / When NOT to Use

Use when later iterations should benefit from earlier learning. Not when each iteration is independent.

### Marianne Score Structure

```yaml
sheets:
  - name: work
    prompt: "Read culture.yaml for accumulated learning. Do the work. Update culture.yaml with new insights."
    capture_files: ["culture.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/culture.yaml"
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 10
```

### Failure Mode

Culture grows without pruning. Old lessons that no longer apply accumulate. Include a pruning step that removes stale entries.

### Composes With

Cathedral Construction, CDCL Search, Systemic Acquired Resistance

---

## CEGAR Loop (Progressive Refinement)

`Status: Working` · **Source:** Counterexample-Guided Abstraction Refinement (Clarke et al., 2000), Expedition 4. **Scale:** iteration. **Iteration:** 4. **Force:** Progressive Commitment.

### Core Dynamic

Iteratively refines ABSTRACTION LEVEL, not output. Start coarse. If a problem is found, check if it's REAL or SPURIOUS (artifact of over-abstraction). If spurious, refine only the specific part that caused the false alarm. You never refine more than necessary. The structural move is minimum-cost verification through progressive abstraction refinement.

The multi-instrument strategy is central: cheap instrument (Sonnet) for the broad coarse pass, expensive instrument (Opus) for the targeted triage. This matches the work's nature — coarse scanning is pattern-matching (cheap), distinguishing real from spurious requires deep reasoning (expensive).

**Termination:** The loop terminates when the CLI validation sheet finds `refinement-targets.yaml` is empty (all findings resolved as REAL or SPURIOUS with no new areas to refine). If `max_chain_depth` is reached before convergence, the loop produces its best current report rather than failing.

### When to Use / When NOT to Use

Use for code review at scale (module-level first, function-level only where coarseness misleads), security audits (dependency scan then exploitability analysis), any verification where thorough analysis is expensive and most of the system is fine. Not when the abstraction hierarchy is shallow, spurious counterexamples are rare, or checking spurious vs. real costs more than full fine-grained analysis.

### Marianne Score Structure

```yaml
sheets:
  - name: coarse-check
    instrument: sonnet
    prompt: "Analyze at module level. Write findings.yaml with [{module, finding, confidence}]."
    validations:
      - type: file_exists
        path: "{{ workspace }}/findings.yaml"
  - name: triage-findings
    instrument: opus
    prompt: >
      For each finding in findings.yaml, determine: REAL or SPURIOUS?
      Write triage-report.yaml: [{module, finding, verdict: REAL|SPURIOUS, evidence}].
    capture_files: ["findings.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/triage-report.yaml"
  - name: refine-or-report
    prompt: >
      Read triage-report.yaml.
      Write refinement-targets.yaml listing modules with SPURIOUS findings needing finer analysis.
      Write current-report.md summarizing all REAL findings confirmed so far.
    capture_files: ["triage-report.yaml"]
  - name: check-termination
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; t=yaml.safe_load(open('{{ workspace }}/refinement-targets.yaml')); assert len(t)==0, f'{len(t)} targets remain'\""
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 5
```

### Failure Mode

Triage consistently marks real findings as spurious — refinement chases ghosts while real issues pass through. Validate by checking that refined areas produce fewer findings (convergence signal). If the loop exhausts `max_chain_depth` without converging, the abstraction hierarchy may be too shallow for this problem — fall back to full fine-grained analysis. The check-termination assertion fails when targets remain, breaking the self-chain — this is intentional, forcing refinement to continue.

### Composes With

Memoization Cache (unchanged modules skip re-analysis), CDCL Search (real findings become constraints), Immune Cascade (CEGAR IS graduated response with abstraction control)

---

## Memoization Cache

`Status: Working` · **Source:** Dynamic programming, functional memoization (Bellman, 1957), Expedition 4. **Scale:** iteration + score-level. **Iteration:** 4.

### Core Dynamic

Workspace artifact `memo-cache.yaml` records input fingerprints and corresponding output fingerprints per stage. Before executing, the agent checks the cache: if the input fingerprint matches, reuse the cached output without re-execution. Not about caching LLM responses (infrastructure concern) — about recognizing at the ORCHESTRATION level that a stage's inputs haven't changed. Self-chaining scores that re-analyze unchanged modules are computing Fibonacci naively.

**Context invalidation:** Cache entries include a `context_hash` derived from prelude content and relevant workspace state beyond direct inputs. When the prelude changes (different instructions, updated conventions), the context hash invalidates affected entries even if input files are identical. The user-supplied `cache_check.py` script computes both input fingerprints and context hash.

### When to Use / When NOT to Use

Use for self-chaining scores where each iteration modifies only part of the workspace, concert campaigns where scores analyze overlapping inputs, or CEGAR Loops where refined modules need re-analysis but unchanged ones don't. Not when inputs change every iteration, cache management costs more than re-execution, or the context hash is too coarse (invalidating too much) or too fine (missing real invalidations).

### Marianne Score Structure

```yaml
sheets:
  - name: check-cache
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/cache_check.py --stage analysis --workspace {{ workspace }}"
  - name: analyze
    prompt: >
      Read memo-cache.yaml. Analyze ONLY files not in the cache (or with changed fingerprints).
      Update memo-cache.yaml with new entries: {file, input_hash, output_hash, context_hash, timestamp}.
    capture_files: ["memo-cache.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/memo-cache.yaml"
```

**Script dependency:** `cache_check.py` is user-supplied. Interface contract: `--stage NAME --workspace PATH`. Exits 0 if cache is valid and contains entries for the current stage. Exits 1 if cache needs rebuilding. The script computes SHA-256 fingerprints of input files and a context hash from prelude content.

### Failure Mode

Cache serves stale results because the context hash missed a relevant change (e.g., a prelude update changed the analysis criteria but not the input files). If cached results look wrong, clear the cache and re-run — the first run is no more expensive than running without memoization. Over-aggressive caching (caching everything) wastes disk and adds lookup overhead; only cache stages where re-execution is expensive.

### Composes With

CEGAR Loop (cache unchanged abstractions), Cathedral Construction (cache across cathedral iterations), Fixed-Point Iteration (cache stable regions during convergence)

---

## The Ten Forces

Ten structural properties of coordination that produce patterns. Every pattern in this corpus responds to one or more forces. The forces describe *why* patterns emerge, not which to use — use the Pattern Selection Guide.

| Force | Description | Patterns Generated |
|-------|-------------|-------------------|
| **Information Asymmetry** | Agents know different things; coordination requires information transfer | Stigmergic Workspace, Source Triangulation, Triage Gate, Relay Zone, Mission Command, Forward Observer, Decision Propagation, Commander's Intent Envelope |
| **Finite Resources** | Work exceeds capacity; must allocate, prioritize, abandon | Immune Cascade, Dormancy Gate, Quorum Consensus, Fixed-Point Iteration, Forward Observer, Echelon Repair, Fermentation Relay, Screening Cascade |
| **Partial Failure** | Components fail independently; unanimity can't scale | Triage Gate, Quorum Consensus, Shipyard Sequence, Read-and-React, CDCL Search, Closed-Loop Call, FRAGO |
| **Exponential Defect Cost** | Finding problems later costs exponentially more | Shipyard Sequence, Succession Pipeline, Immune Cascade, Commissioning Cascade |
| **Producer-Consumer Mismatch** | Each stage produces in its format, consumes in another | Relay Zone, Prefabrication, Barn Raising, Closed-Loop Call, Sugya Weave |
| **Instrument-Task Fit** | Different tasks need different capabilities; matching instrument to task determines cost and quality | Echelon Repair, Fermentation Relay, Commissioning Cascade, Screening Cascade, Vickrey Auction, The Tool Chain, Composting Cascade, CEGAR Loop |
| **Convergence Imperative** | Iterative processes need domain-specific termination; "nothing changed" is too coarse | Soil Maturity Index, Delphi Convergence, Rehearsal Spotlight, Back-Slopping |
| **Accumulated Signal** | Information must accumulate to threshold density before driving structural change | Quorum Trigger, Circuit Breaker, Composting Cascade, Systemic Acquired Resistance |
| **Structured Disagreement** | Single perspectives unreliable; multiply perspectives and use agreement patterns | Rashomon Gate, Source Triangulation, Red Team / Blue Team, Delphi Convergence |
| **Progressive Commitment** | Full commitment before validation is risky; invest incrementally | CEGAR Loop, Canary Probe, Progressive Rollout, Speculative Hedge, Memoization Cache |

---

## The Eleven Generators

Observable patterns of how forces manifest in score design:

| Generator | Maps to Force(s) | Found In |
|-----------|------------------|----------|
| Graduate & Filter | Finite Resources + Exponential Defect Cost | Immune Cascade, Succession Pipeline, Screening Cascade |
| Accumulate Knowledge | Information Asymmetry + Partial Failure | CDCL Search, After-Action Review, Fixed-Point Iteration, Cathedral Construction, Back-Slopping |
| Contract at Interfaces | Producer-Consumer Mismatch | Prefabrication, Barn Raising, Mission Command, Closed-Loop Call |
| Exploit Failure as Signal | Partial Failure + Information Asymmetry | CDCL Search, Red Team / Blue Team, After-Action Review, Triage Gate, Dead Letter Quarantine, Andon Cord |
| Verify through Diverse Observers | Information Asymmetry + Structured Disagreement | Source Triangulation, Red Team / Blue Team, Commissioning Cascade, Rashomon Gate |
| Gate on Environmental Readiness | Finite Resources | Dormancy Gate, Shipyard Sequence, Read-and-React, Canary Probe |
| Match Instrument to Grain | Instrument-Task Fit | Echelon Repair, Fermentation Relay, Screening Cascade, Vickrey Auction, The Tool Chain, Composting Cascade, CEGAR Loop |
| Measure Convergence Character | Convergence Imperative | Soil Maturity Index, Delphi Convergence, Rehearsal Spotlight |
| Threshold-Triggered Switch | Accumulated Signal | Quorum Trigger, Circuit Breaker, Composting Cascade |
| Frame Multiplication | Structured Disagreement | Rashomon Gate, Source Triangulation, Red Team / Blue Team |
| Incremental Exposure | Progressive Commitment | Canary Probe, Progressive Rollout, Speculative Hedge, CEGAR Loop |

---

## Patterns Awaiting Primitives

Confirmed by cross-domain convergence but requiring Marianne capabilities not yet available:

| Pattern | Blocked By | Notes |
|---------|-----------|-------|
| Bulkhead Isolation | Per-sheet resource budgets | Prevents one sheet from consuming all tokens |
| Kanban Pull | Conductor WIP limits | Pull-based work assignment |
| Supervision Tree | Concert restart strategies | Hierarchical fault tolerance (Erlang OTP model). Can be approximated with workspace snapshots + conductor-mediated restart |
| OODA Pulse | Self-correcting orientation phase | Observe-Orient-Decide-Act loop |
| The Aboyeur | Start-time scheduling | Stagger start times by predicted duration. Requires predictable execution times AND conductor scheduling support |
| Backpressure Valve | Concurrent score execution | Sequential batch processing is not backpressure. Requires concurrent producer/consumer with the consumer signaling capacity while both run. Source: Reactive Streams, TCP flow control (Iteration 4, cut after review) |
| Stretto Entry | Staggered/overlapping sheet execution | Overlapping pipeline: next instance starts before previous finishes. Approximation via file_exists dependencies produces sequential-with-trigger, not true overlap. Source: Fugal composition (Iteration 4, cut after review) |
| Comping Substrate | Concurrent score execution + shared filesystem | Adaptive coordination layer running alongside work scores. Requires reading all workspace outputs each iteration. Source: Jazz rhythm section (Iteration 4, cut after review) |
| Physarum Path Reinforcement | Dynamic fan-out allocation | Runtime allocation changes to instance counts and instrument assignments based on workspace state. Source: Physarum polycephalum optimization |

---

## Open Questions

- **Adaptive retry cost** — Retry cheap failures but not expensive ones. Echelon Repair + Vickrey Auction partially address instrument-level cost awareness. Concert-level cost budgeting remains unaddressed.
- **Token/cost budgeting** — No pattern quantifies token usage. Current workaround: instrument selection (cheap for exploration, expensive for synthesis) and Relay Zone for compression.
- **Within-score context compression** — Forward Observer addresses inter-stage compression. Intra-stage compression remains open.
- **Dynamic instrument selection** — Vickrey Auction identifies the best instrument but can't assign it at runtime. Concert-level mechanism (probe score → execution score) or conductor support needed.
- **Contact Point co-evolution** — Two agents co-evolving a shared artifact through responsive alternation is structurally unique but hard to validate. Needs further iteration.
- **Custom script inventory** — 10+ patterns reference user-supplied scripts. A standard library of validation utilities (convergence checking, YAML structure validation, cache management, temperature metrics) would reduce the barrier to adoption.
- **Composition grammar** — Patterns list "Composes With" but no pattern shows the COMBINED YAML for two composed patterns. A composition example appendix would demonstrate practical multi-pattern scores.
- **Cost model** — Canary Probe costs extra. Speculative Hedge costs 2x. Rashomon Gate costs Nx. CEGAR Loop costs unknown iterations. Patterns should include cost estimates relative to baseline.
- **Prompt technique boundary** — Within-stage patterns (Commander's Intent, Quorum Trigger, Constraint Propagation) operate at a different level than orchestration patterns. The boundary between "prompt advice" and "pattern" needs sharper definition.

---

*The Rosetta Pattern Corpus v4 — 56 patterns (38 from iterations 1-3, 18 from iteration 4), 10 generative forces, 11 generators. Revised after three adversarial reviews per iteration. All patterns include YAML snippets, failure modes, status markers, and composition guidance. Four patterns cut in iteration 4, eighteen strengthened, three moved to Awaiting Primitives.*
