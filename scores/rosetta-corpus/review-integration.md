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
