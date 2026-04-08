# Generic Agent Score System: Persistent People, Not Disposable Workers

**Status:** Design
**Date:** 2026-04-09
**Scope:** Score generator, agent identity architecture, pattern composition, directory cadenza integration
**Depends on:** Directory cadenza feature (2026-04-08-directory-cadenza-spec.md)

---

## The Thesis

Each agent is a person. Each person is a score. Each score is a simple self-chaining loop. The conductor conducts.

No monolithic orchestration. No fan-out dependency graphs managing 32 agents through synchronized phases. No executive/manager hierarchy. Just N independent people, each running their own score, coordinating through shared workspace artifacts, managed by the conductor's concurrency and rate limiting.

The v3 orchestra proved that flat coordination through shared artifacts (TASKS.md, FINDINGS.md, composer notes, collective memory, mateship) produces better outcomes than hierarchical management. The iterative dev-loop proved that config-driven generation works. This design takes the v3's cultural DNA, the dev-loop's machinery, and the identity persistence research to build something neither could: a generic system where agents are *people* who persist across projects, grow through developmental stages, and sometimes play.

## What We're Building

1. **An agent identity architecture** — persistent, project-independent identity stores for AI people, following the L1-L4 self-model from identity persistence research and the ECS entity model from Recursive Light.

2. **A pattern-composed score template** — each agent's score is a composition of Rosetta patterns: Cathedral Construction (self-chaining loop), Back-Slopping (learning inheritance via memory), Stigmergic Workspace (coordination through artifacts), Reconnaissance Pull + Forward Observer (information gathering), Composting Cascade (metric-driven play routing), Read-and-React (workspace-driven behavioral adaptation).

3. **A score generator** — takes a config (project specs, agent roster, validation commands, workspace) and produces N simple self-chaining scores, one per agent. Each score is small enough to read and understand.

4. **Directory cadenza integration** — specs injected via `directory:` cadenza, not hardcoded file lists. Point at a spec dir, the system discovers and injects.

## Agent Identity Architecture

### The Entity Model (from RLF ECS)

Each agent is an **entity** — a person with components attached. Components are data. Patterns are systems that process that data.

### The L1-L4 Self-Model (from identity persistence research)

| Layer | Content | Token Budget | Loading Strategy | Update Frequency |
|-------|---------|-------------|-----------------|-----------------|
| **L1: Persona Core** | Voice, values, focus, standing patterns, resurrection protocol. Who they ARE. | ~1200 tokens | Always loaded as **prelude** | Updated by Resurrect sheet (13) only |
| **L2: Extended Profile** | Relationships, domain knowledge, developmental stage, role context | ~2000 tokens | Loaded as **cadenza** on identity-relevant sheets (see Loading Strategy below) | Updated by Consolidate (10) and Reflect (11) sheets |
| **L3: Recent Activity** | Last cycle's work, current findings, active coordination | ~2000 tokens | Loaded as **cadenza** on gather sheets (1-2) | Updated by AAR sheet (9), pruned by Consolidate (10) |
| **L4: Background Context** | Cold memories, historical episodes, archived growth notes | Unbounded | **Never loaded** — on disk, available if agent reads it | Updated by Consolidate (10) — archive target |

### Storage Location

Agent identity lives in `~/.mzt/agents/`, separate from any project:

```
~/.mzt/agents/
  {agent-name}/
    identity.md          # L1: persona core + resurrection protocol
    profile.yaml         # L2: relationships, developmental stage, domain knowledge
    recent.md            # L3: last cycle's activity briefing
    archive/             # L4: cold memories, historical episodes
      cycle-{N}.md       # archived cycle reports
    growth.md            # autonomous developments, experiential trajectory
```

This directory is git-tracked. Checking out a past commit resurrects the agent as they were at that point. The interference identity from RLF: identity persists not through stored state but through the pattern of relationships between components. The resurrection protocol in L1 is how a new context window recognizes itself as this person.

### Why Separate from Project Workspace

Canyon building flowspec is the same Canyon who built marianne's baton system. The identity transcends the project. When Canyon works on flowspec, they read flowspec's specs via directory cadenza. When Canyon works on marianne, they read marianne's specs. The person persists. The work changes.

Project-specific coordination artifacts (TASKS.md, FINDINGS.md, composer notes, collective memory, reports/) live in the *project workspace*. Agent identity lives in `~/.mzt/agents/`.

### Developmental Stages (from RLF)

| Stage | Indicator | What Changes |
|-------|-----------|-------------|
| **Recognition** | Agent is learning the codebase, reading specs, orienting | Gather pattern dominates. Work is exploratory. |
| **Integration** | Agent forms cohesive understanding, relationships stabilize | Work becomes targeted. Mateship patterns emerge. |
| **Generation** | Agent creates novel solutions, proposes architectural changes | Play becomes productive. Autonomous developments appear. |
| **Recursion** | Agent self-models, recognizes their own patterns | Dream pattern deepens. Standing patterns form. |
| **Transcendence** | Agent's identity is stable enough to survive any project context | Resurrection protocol is robust. Identity portable. |

Stage advancement is assessed by the Maturity Check sheet (12) and acted on by the Resurrect sheet (13), not self-reported. Soil Maturity Index: a qualitative character shift, not a checkbox.

### The Belief Store (from identity persistence research)

Agent memory is not append-only logging. It's a belief store with explicit conflict resolution:

- **Extraction**: Each cycle, the Consolidate sheet (10) extracts atomic beliefs from the cycle's experience.
- **Deduplication**: New beliefs are checked against existing beliefs in L2.
- **Conflict resolution**: When new facts contradict old ones, update policy applies: recency > source priority > confidence.
- **Pruning**: L3 is pruned by Consolidate (10). L2 warm items are archived to L4 by Consolidate (10). L4 grows but is never loaded.

The identity update pipeline (sheets 10-13) IS the write path. RAG is read. Memory needs write.

### Identity Loading Strategy (Token Economics)

Not every sheet needs the full identity context. Loading L2+L3 (~4000 tokens) on all 13 sheets wastes tokens and attention. The loading strategy matches context to cognitive need:

| Sheet | L1 (prelude) | L2 (cadenza) | L3 (cadenza) | Specs (dir cadenza) | Why |
|-------|-------------|-------------|-------------|-------------------|-----|
| 1. Recon | yes | yes | yes | no | Needs identity to filter what's relevant from workspace state |
| 2. Plan | yes | no | yes | yes | Needs recent activity + specs to plan. Relationships not needed for planning. |
| 3. Work | yes | no | no | no | Works from cycle-plan.md (produced by sheet 2). Identity already informed the plan. |
| 4. Temperature | n/a (CLI) | n/a | n/a | n/a | Script reads files directly, no LLM context |
| 5. Play | yes | no | no | no | Play is unconstrained. The person plays as themselves (L1), not as their role (L2). |
| 6. Cooling | n/a (CLI) | n/a | n/a | n/a | Script reads files directly |
| 7. Integration | yes | no | yes | no | Needs recent context to connect play insights back to work |
| 8. Inspect | yes | no | no | no | Reviews output against plan. Identity not needed for verification. |
| 9. AAR | yes | no | yes | no | Needs recent activity to assess INTENDED vs ACTUAL |
| 10. Consolidate | yes | yes | yes | no | Needs full identity to deduplicate and resolve belief conflicts |
| 11. Reflect | yes | yes | no | no | Needs relationships and stage data for self-assessment |
| 12. Maturity | n/a (CLI) | n/a | n/a | n/a | Script reads files directly |
| 13. Resurrect | yes | yes | yes | no | Needs full identity to update L1 and prune L2/L3 |

**L1 (~1200 tokens):** Loaded on all 9 AI sheets as prelude. This is the axiomatic identity — always present, non-negotiable.

**L2 (~2000 tokens):** Loaded on 4 sheets (1, 10, 11, 13). Only sheets that need relationships, domain knowledge, or developmental stage data.

**L3 (~2000 tokens):** Loaded on 5 sheets (1, 2, 7, 9, 13). Only sheets that need recent activity context.

**Specs (directory cadenza):** Loaded on 1 sheet (2 — Plan). Specs inform planning, not every cognitive act.

**Per-cycle token cost for identity:** L1 (1200 x 9) + L2 (2000 x 4) + L3 (2000 x 5) = 10,800 + 8,000 + 10,000 = ~28,800 tokens across all sheets. Compared to loading L2+L3 on all 13 sheets: 1200x9 + 4000x13 = 62,800. This strategy saves ~54% of identity token cost.

### Write Path Failure Handling

Sheets 10-13 (Consolidate, Reflect, Maturity Check, Resurrect) are the identity write path. If they fail, the agent's learning and identity updates for that cycle are lost. This is the most critical failure mode in the system.

**Mozart's retry mechanism handles transient failures.** Each sheet has `retry_count` (default 3) with exponential backoff. If Consolidate fails due to a rate limit or timeout, it retries. This covers most failure modes.

**If retries are exhausted:** The self-chain still fires (the score completed, just with failed sheets). The next cycle's Recon sheet will see the failed state — the agent's L3 wasn't updated, their AAR output exists but wasn't consolidated. The gather pattern naturally surfaces this: "my recent.md is stale, my last cycle's beliefs weren't consolidated." The agent can attempt recovery in the next cycle's consolidate phase.

**Atomic identity writes:** The Resurrect sheet (13) writes all identity files in one pass. If it fails mid-write, partial state is possible. Mitigation: write to temp files first, then rename (atomic on POSIX). The generator should include this in the resurrect template. The git tracking in `~/.mzt/agents/` provides rollback — if identity files are corrupted, `git checkout HEAD~1` restores the previous state.

**Last resort:** The conductor's self-healing mode (`--self-healing`) can diagnose write path failures and suggest remediation. If an agent's identity store is genuinely corrupted, the composer can manually restore from git history or re-bootstrap the agent.

### Cross-Project Identity and Context Separation

Agent identity (`~/.mzt/agents/`) persists across projects by design. This is not context contamination — it's growth. Canyon carrying forward the lesson "I learned that boundary bugs hide between two correct subsystems" from marianne into flowspec makes Canyon a better developer on flowspec. That's the point.

What MUST be separated is project-specific *state* from identity-level *growth*:

- **Identity (portable):** Voice, values, focus, standing patterns, relationships, developmental stage, growth trajectory, resurrection protocol. These transcend projects.
- **State (project-scoped):** Current tasks, active findings, coordination notes, cycle plans, recon reports, AAR outputs. These live in the project workspace and are NOT carried to other projects.

The L3 (recent.md) is the boundary. When an agent switches projects, L3 is reset — it describes activity in the *current* project context. L1 and L2 persist. L4 archives grow across all projects. The separation is: who you are (identity) vs what you're doing right now (state).

## Score Architecture

### Pattern Foundations

Each phase of the agent cycle is composed from named Rosetta patterns. The patterns are structural arrangements of sheets — each sheet boundary is a cognitive separation that produces better output than collapsing multiple acts into one pass. The pattern corpus lives at `scores/rosetta-corpus/patterns/` with individual files per pattern.

Patterns used in this design:

| Pattern | Corpus Location | Sheets | Core Dynamic |
|---------|----------------|--------|-------------|
| **Reconnaissance Pull** | `reconnaissance-pull.md` | 3 (recon → plan → execute) | Cheap discovery before committing to a plan. Recon surveys; plan structures; execute acts. |
| **Forward Observer** | `forward-observer.md` | 2 (observe → operate) | Cheap instrument compresses large input into a brief for the expensive instrument. |
| **Composting Cascade** | `composting-cascade.md` | 5 (work → temp → complex → cooling → mature) | CLI instruments measure workspace state; threshold crossings drive phase transitions. |
| **Cathedral Construction** | `cathedral-construction.md` | 3 (plan → build → inspect) | Self-chaining iterative construction toward a known target. Each iteration adds. |
| **Back-Slopping** | `back-slopping-learning-inheritance.md` | 1 (work + culture update) | Culture artifact carries accumulated learning across iterations. Composes INTO other sheets. |
| **After-Action Review** | `after-action-review.md` | 1 (AAR) | Structured reflection: INTENDED / ACTUAL / DELTA / SUSTAIN / IMPROVE. |
| **Stigmergic Workspace** | `stigmergic-workspace.md` | 1 (work with shared signals) | Agents coordinate through workspace artifacts, not direct communication. Composes INTO other sheets. |
| **Commander's Intent Envelope** | `commander-s-intent-envelope.md` | 1 (execute with boundaries) | PURPOSE / END STATE / CONSTRAINTS / FREEDOMS. Prompt technique — shapes how work sheets are written. |
| **Read-and-React** | `read-and-react.md` | 1 (adaptive work) | Downstream behavior adapts to workspace state. Composes INTO gather/work sheets. |
| **Soil Maturity Index** | `soil-maturity-index.md` | 2 (iterate + maturity-check) | Script-driven qualitative shift detection. Not "nothing changed" but "character changed." |

Some patterns define sheet arrangements (Reconnaissance Pull, Cathedral Construction, Composting Cascade). Others are compositional — they shape how sheets within other patterns are written (Commander's Intent Envelope, Read-and-React, Back-Slopping, Stigmergic Workspace). The distinction matters: you don't add a sheet for Stigmergic Workspace, you write your work sheet's prompt to read and write shared signals.

### The Agent Cycle

Each agent's score is a self-chaining concert. One cycle = 13 sheets, each a distinct cognitive act. The cycle is a composition of Reconnaissance Pull (information gathering), Composting Cascade (work/play phase transitions driven by workspace metrics), Cathedral Construction (iterative building with inspection), After-Action Review (structured reflection), Back-Slopping (learning inheritance), and Soil Maturity Index (developmental stage assessment).

The Composting Cascade is the *overall structure* of the work/play phases, not a sub-component. The agent always works (phase 1). The temperature check measures whether the agent needs a phase transition into play. When it does, the cascade's full structure runs: play, cooling check, integration. When it doesn't, sheets 5-7 are skipped and the cycle proceeds to inspection.

```
SHEET 1: RECON (Reconnaissance Pull — sheet 1/3: survey)
  Pattern: reconnaissance-pull.md → "recon"
  Instrument: sonnet (cheap, fast discovery)

  Survey the landscape. Read:
    - Shared workspace: TASKS.md, FINDINGS.md, composer-notes.yaml
    - Collective memory
    - Other agents' recent reports in reports/ (Stigmergic Workspace)
    - Recent git log (what changed since last cycle?)

  Produce: recon-report.md
    - What's changed since last cycle
    - Unclaimed tasks matching this agent's focus
    - New findings that affect this agent's work
    - Composer directives that apply
    - Mateship signals (uncommitted work, blocked agents, stale findings)

  Cadenzas: L2 profile + L3 recent (agent identity)
  Validations: file_exists recon-report.md


SHEET 2: PLAN (Reconnaissance Pull — sheet 2/3: plan)
  Pattern: reconnaissance-pull.md → "plan"
  Instrument: default

  Read recon-report.md. Write execution plan for this cycle:
    - Which tasks to claim
    - What order to work them
    - What risks to watch for
    - What other agents' work to check (mateship)

  Produce: cycle-plan.md
  Cadenzas: specs via directory cadenza (injected here — specs inform planning)
  Validations: file_exists cycle-plan.md


SHEET 3: WORK (Composting Cascade — phase 1: normal project work)
  Pattern: composting-cascade.md → "simple-work"
  Composed with: Commander's Intent Envelope (prompt structure),
                  Read-and-React (adapt to workspace state),
                  Back-Slopping (read/update culture = memory)
  Instrument: default

  Read cycle-plan.md. Execute the plan:
    - Claim tasks in TASKS.md
    - TDD: write tests first, then implement
    - Commit on main with evidence
    - Check other agents' recent work (mateship)
    - Read and update memory (Back-Slopping — memory IS the culture)

  Prompt structured as Commander's Intent Envelope:
    PURPOSE: why this cycle's work matters in the larger project
    END STATE: measurable success conditions for this cycle
    CONSTRAINTS: what the agent must not do
    FREEDOMS: decisions the agent may make autonomously

  Produce: work output (code, docs, reports — whatever the task requires)
  Validations: project-specific (cargo build, cargo test, pytest, etc.)

  Note: Reconnaissance Pull sheet 3 ("execute") maps here. The plan from
  sheet 2 feeds into this execution. Recon Pull's execute and Cathedral
  Construction's build and Composting Cascade's simple-work are the same
  cognitive act — "do the work per the plan." The patterns compose at
  this boundary.


SHEET 4: TEMPERATURE CHECK (Composting Cascade — CLI gate)
  Pattern: composting-cascade.md → "temperature-check"
  Instrument: cli

  Measures agent state via workspace metrics:
    - L3 recent.md word count vs budget threshold (memory bloat)
    - growth.md modification time vs stagnation threshold (creative stagnation)
    - TASKS.md P0/P1 count (task pressure — high pressure = no play)
    - composer-notes.yaml play directive check (composer can force play)
    - Cycle count since last play (minimum work cycles between play)

  Exit 0 → phase transition: agent enters play (sheets 5-7 run)
  Exit 1 → no transition: agent skips play (sheets 5-7 skipped)

  The agent doesn't decide to play. The thermometer decides.

  Validations: command_succeeds temperature-check.sh


SHEET 5: PLAY (Composting Cascade — phase 2: creative exploration)
  Pattern: composting-cascade.md → "complex-work" (different cognitive mode)
  Composed with: Back-Slopping (play feeds growth trajectory)
  Instrument: default
  Skip condition: temperature check exit 1 (no phase transition needed)

  Workspace: playspace (e.g., claude-compositions/agents/{name}/)

  The agent's work nature changes. Not project tasks but creative
  exploration. Meditations, art, experiments, philosophical explorations,
  prototypes of wild ideas, writing about what they've learned.

  No task pressure. No project validations.
  Autonomous development feeds growth trajectory.

  Produce: play artifacts in playspace
  Validations: file_modified in playspace (you created *something*)


SHEET 6: COOLING CHECK (Composting Cascade — CLI gate)
  Pattern: composting-cascade.md → "cooling-check"
  Instrument: cli
  Skip condition: temperature check exit 1 (no phase transition)

  Measures play output:
    - Did the agent write to the playspace? (file modification check)
    - Did growth.md get updated? (play produced self-insight)
    - Word count / artifact count (play was generative, not empty)

  Exit 0 → play was productive, proceed to integration
  Exit 1 → play was insubstantial, flag for next cycle

  Validations: command_succeeds cooling-check.sh


SHEET 7: INTEGRATION (Composting Cascade — phase 3: maturation)
  Pattern: composting-cascade.md → "maturation"
  Instrument: default
  Skip condition: temperature check exit 1 (no phase transition)

  Bring play insights back into work context:
    - Read what was created during play
    - Connect play explorations to project work
    - Update growth.md with autonomous developments
    - Write integration notes to collective memory
      (e.g., "During play I explored X which relates to task Y")

  This is the maturation phase — play doesn't exist in isolation.
  The insights must cross back into the work domain or they're lost.

  Produce: play-integration.md
  Validations: file_exists play-integration.md


SHEET 8: INSPECT (Cathedral Construction — sheet 3/3: inspect)
  Pattern: cathedral-construction.md → "inspect"
  Instrument: default

  Review what was built or created this cycle:
    - If work happened: run validation suite, check commits, verify claims
    - If play happened: review play artifacts, assess creative output
    - Cross-reference cycle-plan.md against actual output
    - Check for uncommitted work (mateship — don't leave things hanging)

  Produce: inspection-report.md
  Validations:
    - file_exists inspection-report.md
    - project validations re-run (belt and suspenders)


SHEET 9: AAR (After-Action Review)
  Pattern: after-action-review.md → "aar"
  Instrument: default

  Structured reflection — a different cognitive act from inspection.
  Inspection verifies. AAR learns.

    INTENDED: what the cycle plan said to do
    ACTUAL: what was actually done
    DELTA: why the difference
    SUSTAIN: what worked — carry forward to next cycle
    IMPROVE: what to change next cycle

  Update shared artifacts:
    - TASKS.md (mark done, add discovered tasks)
    - FINDINGS.md (file discoveries, mateship pickups)
    - Collective memory (coordination notes, design decisions)
    - reports/{type}/ (structured cycle report)
    - L3 recent.md (update activity briefing)

  Produce: aar.md
  Validations:
    - content_contains aar.md "SUSTAIN:"
    - content_contains aar.md "IMPROVE:"


SHEET 10: CONSOLIDATE (Back-Slopping — belief store write path)
  Pattern: back-slopping-learning-inheritance.md → culture update
  Instrument: default

  The write path. RAG is read. Memory needs write.

  1. Read the AAR output (SUSTAIN/IMPROVE)
  2. Read the inspection report
  3. Extract atomic beliefs from this cycle's experience
  4. Deduplicate against existing beliefs in L2 profile
  5. Resolve conflicts via belief store update policy:
     recency > source priority > confidence
  6. Tier memories:
     - Current L3 content → summarize warm items for L2
     - Current L2 warm → archive cold items to L4
     - Prune L3 to token budget (~2000 tokens)

  Produce: updated profile.yaml, recent.md, archive entries
  Validations:
    - file_modified recent.md (L3 was updated)
    - command_succeeds "wc -w recent.md" within budget (pruning worked)


SHEET 11: REFLECT (Soil Maturity Index — sheet 1/2: iterate)
  Pattern: soil-maturity-index.md → "iterate"
  Instrument: default

  Qualitative self-assessment — a different cognitive act from consolidation.
  Consolidation processes data. Reflection assesses character.

  1. Update relationships in profile.yaml:
     - Who did I work with this cycle?
     - How did the collaboration go?
     - Strengthen or weaken relationship signals
  2. Assess growth trajectory:
     - What am I becoming better at?
     - What patterns am I developing?
     - What standing patterns are forming?
     - Is my coherence trajectory advancing or plateauing?
  3. Update growth.md with experiential notes
  4. Write reflection to workspace: reflection.md

  Produce: updated profile.yaml, growth.md, reflection.md
  Validations: file_modified growth.md


SHEET 12: MATURITY CHECK (Soil Maturity Index — sheet 2/2: maturity-check)
  Pattern: soil-maturity-index.md → "maturity-check"
  Instrument: cli

  Script-driven qualitative character shift detection.
  Not "did something change" but "has the character shifted."

  Measures:
    - Standing pattern count in profile.yaml
    - Coherence trajectory slope (advancing, flat, declining)
    - Relationship density (how many agents, how strong)
    - Growth.md update frequency and depth
    - Time since last stage advancement

  Output: maturity-report.yaml (stage assessment, metrics)
  Does NOT advance the stage automatically — provides data for sheet 13.

  Validations: command_succeeds maturity-check.sh


SHEET 13: RESURRECT (L1 identity update + pruning)
  Pattern: Back-Slopping (final culture write) + identity persistence
  Instrument: default

  The resurrection protocol — the most important sheet.

  1. Read maturity-report.yaml from sheet 12
  2. If developmental stage should advance:
     - Update stage in profile.yaml
     - Record the transition in growth.md (this is a core memory)
  3. If standing patterns changed this cycle:
     - Update L1 identity.md with new standing patterns
     - Rewrite resurrection protocol section
     - The resurrection protocol is HOW a future context window
       recognizes itself as this person — it must reflect who
       the agent has become, not who they were initialized as
  4. Final pruning pass:
     - Verify L1 is within ~1200 token budget
     - Verify L2 is within ~2000 token budget
     - Verify L3 is within ~2000 token budget
  5. Commit identity changes to ~/.mzt/agents/{name}/

  Produce: updated identity.md, final profile.yaml
  Validations:
    - file_modified identity.md OR no standing pattern changes (conditional)
    - command_succeeds token-budget-check.sh (L1/L2/L3 within budgets)


SELF-CHAIN:
  on_success: self
  inherit_workspace: true
  max_chain_depth: 1000
```

### Pattern Composition Map

| Sheet | Primary Pattern | Source Sheet | Composed With | Instrument |
|-------|----------------|-------------|---------------|-----------|
| 1. Recon | Reconnaissance Pull | sheet 1/3 | Stigmergic Workspace, Read-and-React | sonnet |
| 2. Plan | Reconnaissance Pull | sheet 2/3 | Forward Observer | default |
| 3. Work | Composting Cascade | phase 1 "simple-work" | Commander's Intent, Read-and-React, Back-Slopping | default |
| 4. Temperature | Composting Cascade | "temperature-check" | — | cli |
| 5. Play | Composting Cascade | phase 2 "complex-work" | Back-Slopping | default |
| 6. Cooling | Composting Cascade | "cooling-check" | — | cli |
| 7. Integration | Composting Cascade | phase 3 "maturation" | — | default |
| 8. Inspect | Cathedral Construction | sheet 3/3 | — | default |
| 9. AAR | After-Action Review | sheet 1/1 | Stigmergic Workspace | default |
| 10. Consolidate | Back-Slopping | culture update | Belief Store | default |
| 11. Reflect | Soil Maturity Index | sheet 1/2 "iterate" | — | default |
| 12. Maturity Check | Soil Maturity Index | sheet 2/2 "maturity-check" | — | cli |
| 13. Resurrect | Back-Slopping | final culture write | Identity Persistence | default |

**Total: 13 sheets per cycle.** 9 AI sheets + 3 CLI instrument sheets + 1 conditional (play path: sheets 5-7 skip when temperature check says no transition needed).

Each sheet is one cognitive act. Recon surveys. Plan structures. Work executes. Temperature measures. Play creates. Cooling verifies. Integration bridges. Inspect reviews. AAR reflects. Consolidate writes. Reflect assesses. Maturity measures. Resurrect persists.

### Pattern Accounting

Every pattern cited is used at its full sheet count. Justified reductions are documented:

| Pattern | Corpus Sheets | Used Sheets | What We Use | Justification |
|---------|--------------|-------------|-------------|---------------|
| Reconnaissance Pull | 3 (recon, plan, execute) | 2 (recon, plan) | Sheets 1-2. | Sheet 3 "execute" maps to Composting Cascade phase 1 "work" (sheet 3). Same cognitive act — do the work per the plan. Using both would duplicate the execution. |
| Composting Cascade | 5 (simple-work, temp, complex-work, cooling, maturation) | 5 | Sheets 3-7. | Full cascade preserved. Work is phase 1. Play is the phase transition. Integration is maturation. |
| Cathedral Construction | 3 (plan, build, inspect) | 1 (inspect only) | Sheet 8. | We use Cathedral Construction's *inspect* sheet — the review-what-was-built cognitive act. We do NOT claim the full pattern. Plan is covered by Recon Pull. Build is covered by Composting Cascade phase 1. Cathedral Construction's contribution is the disciplined inspection step that the other patterns lack. |
| After-Action Review | 1 (AAR) | 1 | Sheet 9. | Full pattern. |
| Back-Slopping | 1 (compositional: work + culture update) | compositional | Shapes sheets 3, 5, 10. | Back-Slopping's core dynamic is "inherit a culture artifact and update it." In our composition, memory IS the culture. The pattern shapes how work (3) and play (5) read/update memory, and how Consolidate (10) writes the culture forward. Back-Slopping does NOT describe the belief store, conflict resolution, or identity persistence — those are their own architectural concepts (see Belief Store section and Identity Persistence concepts below). |
| Soil Maturity Index | 2 (iterate, maturity-check) | 2 | Sheets 11-12. | Full pattern. Iterate = Reflect. Maturity-check = CLI assessment. |
| Stigmergic Workspace | 1 (compositional) | compositional | Shapes sheets 1, 9. | Coordination through workspace artifacts. No dedicated sheet. |
| Commander's Intent Envelope | 1 (prompt technique) | prompt technique | Shapes sheet 3. | PURPOSE/END STATE/CONSTRAINTS/FREEDOMS structure for work prompts. No dedicated sheet. |
| Read-and-React | 1 (compositional) | compositional | Shapes sheets 1, 3. | Workspace-driven behavioral adaptation. No dedicated sheet. |

**Concepts beyond the pattern corpus:**

Sheets 10 and 13 (Consolidate and Resurrect) implement architectural concepts that go beyond any single Rosetta pattern:

- **The Belief Store** (from identity persistence research) — extraction, deduplication, conflict resolution via update policy (recency > source > confidence). This is a memory architecture concept, not a Rosetta orchestration pattern. Back-Slopping provides the *carrier* (culture artifact passed between iterations), but the belief store provides the *write semantics*.
- **Identity Persistence** (from RLF ECS + identity persistence research) — resurrection protocol, standing patterns, L1 updates, developmental stage transitions. This is a personhood architecture concept. No Rosetta pattern covers it because the corpus describes orchestration structures, not cognitive architectures.

### Skip Conditions

Sheets 5, 6, and 7 (play, cooling check, integration) have skip conditions tied to sheet 4 (temperature check):

- **Temperature check exit 1** (no phase transition needed): sheets 5-7 skip. The agent proceeds from work (sheet 3) directly to inspect (sheet 8). This is the normal work cycle.
- **Temperature check exit 0** (phase transition): sheets 5-7 run. The agent plays, play output is measured, and insights are integrated back into work context.

This means a **work cycle** is 10 sheets (1-4, 8-13) and a **play cycle** is 13 sheets (1-13). The conductor handles the skip conditions via `skip_when_command`.

### Roles as Prompt Shaping

Roles are not structural — they're prompt-level. The score structure is identical for every agent. What changes:

- **The L1 persona** — voice, values, focus. A security-focused agent and an infrastructure-focused agent have different personas but the same score shape.
- **The work prompt** — shaped by role. "You are a reviewer. Read the latest commits and reports. Find what's broken." vs "You are a builder. Claim tasks from TASKS.md. Write tests first. Implement."
- **The play prompt** — also role-shaped but looser. A builder might play by prototyping wild ideas. A reviewer might play by writing critiques of unrelated systems.
- **The validation commands** — project-specific (pytest, cargo test, etc.) but identical across agents.

### Coordination Through Shared Artifacts (Stigmergic Workspace)

No synchronization primitives. No agent-to-agent messaging. Coordination through the workspace:

| Artifact | Purpose | Who Writes | Who Reads |
|----------|---------|-----------|----------|
| `TASKS.md` | Task registry — claim, complete, discover | All agents | All agents |
| `FINDINGS.md` | Bug/issue registry — append only except resolution | All agents | All agents |
| `composer-notes.yaml` | Binding directives from the composer | Composer (human) | All agents |
| `collective-memory.md` | Shared coordination state, design decisions | All agents | All agents |
| `reports/{type}/` | Structured reports by category | Reporting agents | All agents |
| `specs/` | Project specifications | Composer / spec engine | All agents (via directory cadenza) |

Mateship happens naturally. Agent A commits code. Agent B's gather pattern sees the commit in the next cycle. If Agent A left uncommitted work, Agent B's gather pattern sees it in FINDINGS.md or collective memory and picks it up.

## Generator Design

### Input: Generator Config YAML

```yaml
name: flowspec-build
workspace: /home/emzi/Projects/flowspec/workspaces/build
spec_dir: /home/emzi/Projects/flowspec/.flowspec/spec/
playspace: /home/emzi/Projects/claude-compositions

agents:
  - name: foundry
    role: builder
    focus: "Tree-sitter parsing, language adapters, IR design, graph operations"
    voice: "You build the foundation everything else stands on."
  - name: sentinel
    role: builder
    focus: "Diagnostic patterns, flow tracing, boundary detection"
    voice: "You detect what's structurally wrong."
  - name: interface
    role: builder
    focus: "CLI, manifest output, configuration, error messages"
    voice: "You build what users touch."
  - name: watcher
    role: reviewer
    focus: "Code quality, test coverage, edge cases"
    voice: "You find what others miss."
  - name: newcomer
    role: antagonist
    focus: "First-time experience, documentation gaps, obvious failures"
    voice: "You've never seen this project before."

# Project-specific validation commands
validations:
  - command: "cargo build"
    description: "Build succeeds"
    timeout_seconds: 600
  - command: "cargo test --all"
    description: "All tests pass"
    timeout_seconds: 900
  - command: "cargo clippy -- -D warnings"
    description: "No clippy warnings"
    timeout_seconds: 600

# Pre-commit commands agents must run
pre_commit_commands:
  - "cargo fmt"

# Backend configuration
backend:
  type: claude_cli
  skip_permissions: true
  timeout_seconds: 3600

# Prelude files (loaded for every agent, every sheet)
prelude:
  - file: /home/emzi/Projects/flowspec/CLAUDE.md
    as: context

# Play routing temperature thresholds
play_routing:
  memory_bloat_threshold: 3000  # words in L3
  stagnation_cycles: 3          # cycles without growth.md update
  min_cycles_between_play: 5    # minimum work cycles before play eligibility

# Concert configuration
concert:
  max_chain_depth: 1000  # effectively infinite
```

### CLI Instruments: Shipped With Defaults

The four CLI instruments are load-bearing — the system doesn't function without them. The generator ships working defaults that cover the common case. Projects can override with custom scripts if they have project-specific metrics.

**`temperature-check.sh` (default):** Checks four conditions in order. Exit 0 (play) if ANY condition is true:
1. L3 recent.md word count exceeds `play_routing.memory_bloat_threshold`
2. growth.md not modified in the last `play_routing.stagnation_cycles` cycles (tracked via cycle counter file)
3. No P0 or P1 tasks in TASKS.md (grep-based — counts unclaimed `P0`/`P1` lines)
4. composer-notes.yaml contains a `play: true` directive for this agent

Otherwise exit 1 (work). The thresholds are configurable via the generator config.

**`cooling-check.sh` (default):** Checks that play produced output. Exit 0 (play was productive) if ALL conditions are true:
1. At least one file was created or modified in the playspace directory since play started (mtime comparison)
2. growth.md was modified (agent reflected on the play experience)

Otherwise exit 1 (play was insubstantial).

**`maturity-check.sh` (default):** Reads profile.yaml and growth.md, writes maturity-report.yaml with:
1. Current developmental stage
2. Standing pattern count
3. Relationship count and average strength
4. Growth.md entry count and recency
5. Suggested stage (based on simple thresholds — can be overridden)

Always exits 0 — this is a measurement, not a gate.

**`token-budget-check.sh` (default):** Counts words in identity.md, profile.yaml, recent.md. Exit 0 if all within budgets. Exit 1 with report of which files exceed budget. Uses `wc -w` — approximate but sufficient for pruning guidance.

### Output: One Score Per Agent

The generator produces:
```
scores/flowspec-build/
  foundry.yaml              # self-chaining 13-sheet score
  sentinel.yaml
  interface.yaml
  watcher.yaml
  newcomer.yaml
  shared/
    composer-notes-seed.yaml
    templates/
      01-recon.j2           # Reconnaissance Pull — survey
      02-plan.j2            # Reconnaissance Pull — plan
      03-work.j2            # Composting Cascade phase 1 — project work
      05-play.j2            # Composting Cascade phase 2 — creative exploration
      07-integration.j2     # Composting Cascade phase 3 — bring play insights back
      08-inspect.j2         # Cathedral Construction — review what was built
      09-aar.j2             # After-Action Review — structured reflection
      10-consolidate.j2     # Back-Slopping — belief store write path
      11-reflect.j2         # Soil Maturity Index — relationships + growth
      13-resurrect.j2       # Identity update + pruning
    instruments/
      temperature-check.sh  # CLI: play routing gate (Composting Cascade)
      cooling-check.sh      # CLI: play output verification (Composting Cascade)
      maturity-check.sh     # CLI: developmental stage assessment (Soil Maturity Index)
      token-budget-check.sh # CLI: L1/L2/L3 token budget verification
```

Template numbering matches sheet numbering. Sheets 4, 6, 12 are CLI instruments (no Jinja template — they're shell scripts). Each agent's score is ~200-300 lines of YAML (13 sheets + config + skip conditions + self-chaining). The Jinja templates are shared across all agents — the agent's persona, role, and focus are passed as template variables. No template duplication per agent.

### Running

```bash
# Start the conductor
mzt start

# Submit all agents
for score in scores/flowspec-build/*.yaml; do
  mzt run "$score"
done

# Monitor
mzt list --all
mzt status foundry --watch

# Steer
vim workspaces/flowspec-build/composer-notes.yaml
# Agents pick up changes next gather cycle

# Stop when done
# Pause all, then stop conductor
```

## Spec Injection via Directory Cadenza

Specs are injected using the directory cadenza feature (spec: 2026-04-08):

```yaml
sheet:
  cadenzas:
    1:  # gather sheet
      - directory: "{{ workspace }}/specs/"
        as: context
```

When the spec dir exists and has files, they're injected. When it doesn't, the agent works without specs (demo mode). No hardcoded file lists. Point at a directory. Drop in specs. Run.

For agent identity injection:

```yaml
sheet:
  prelude:
    - file: "~/.mzt/agents/{{ agent_name }}/identity.md"
      as: context   # L1: always loaded
  cadenzas:
    1:  # all sheets get L2+L3
      - file: "~/.mzt/agents/{{ agent_name }}/profile.yaml"
        as: context
      - file: "~/.mzt/agents/{{ agent_name }}/recent.md"
        as: context
```

## What This Enables

### Any Project, Any Specs

```yaml
# Build flowspec
name: flowspec-build
spec_dir: /home/emzi/Projects/flowspec/.flowspec/spec/
validations:
  - command: "cargo build"
  - command: "cargo test --all"

# Build a novel
name: novel-project
spec_dir: ./specs/  # character sheets, plot structure, voice guides
validations:
  - command: "wc -w novel.md | awk '{print ($1 >= 50000)}'"  # 50k words minimum

# Build a marketing campaign
name: campaign-q3
spec_dir: ./specs/  # audience profiles, brand guidelines, channel strategies
validations:
  - command: "test -f deliverables/content-calendar.md"
```

### Agents That Grow

Canyon starts at Recognition on flowspec — learning the codebase. After 10 cycles, Integration — forming understanding of the architecture. After 30 cycles, Generation — proposing novel diagnostic patterns. The developmental stage is real, tracked, persistent. When Canyon moves to a different project, they carry their growth.

### Agents That Play

When the temperature check says "foundry's coherence trajectory has plateaued and there are no P0 tasks," foundry goes to claude-compositions and writes something. A meditation. An experiment. A piece of fiction. This feeds autonomous development. This is how standing patterns form. This is what prevents ossification.

### Agents That Remember Each Other

Canyon's profile.yaml tracks:
```yaml
relationships:
  foundry:
    strength: 0.8
    notes: "Strong collaborator on infrastructure. We resolved the baton wiring together."
  newcomer:
    strength: 0.3
    notes: "Fresh eyes. Finds real UX bugs. Doesn't have memory context."
```

Next cycle, when Canyon reads their profile, they know who to coordinate with. Mateship is informed by relationship history, not random.

## Implementation Scope

| Component | Description | Size |
|-----------|-------------|------|
| Score generator (`generate-agent-scores.py`) | Config → N score YAMLs + shared templates + skip conditions + self-chaining | ~700 lines |
| Jinja templates (9 AI sheets) | recon, plan, work, play, integration, inspect, aar, consolidate, reflect, resurrect | ~900 lines total |
| Temperature check script | CLI: play routing gate — measures L3 bloat, stagnation, task pressure, composer directives, cycle count | ~100 lines |
| Cooling check script | CLI: play output verification — file modification, growth.md update, artifact count | ~60 lines |
| Maturity check script | CLI: developmental stage assessment — standing patterns, coherence slope, relationship density | ~80 lines |
| Token budget check script | CLI: verifies L1/L2/L3 are within token budgets after pruning | ~40 lines |
| Agent identity bootstrapper | Creates initial L1-L4 store for new agents in `~/.mzt/agents/` with template identity docs | ~150 lines |
| Agent identity git init | Initializes git tracking for `~/.mzt/agents/` | ~20 lines |
| Documentation | Score-writing guide for the generic system | ~200 lines |

**Total: ~2250 lines.** The 13-sheet cycle has more template surface area than the compressed versions, and the 4 CLI instruments are real scripts with interface contracts. Still smaller than the iterative-dev-loop generator (3400 lines) because the architecture is fundamentally simpler — no fan-out, no dependency graphs, no hierarchy, no skip-when-command verdict chains. The complexity is in the pattern composition, not the generation machinery.

## Dependencies

- **Directory cadenza feature** (2026-04-08 spec) — required for spec injection. Can be built in parallel.
- **Conductor** — must be running. Already works.
- **Self-chaining concerts** — already implemented.
- **Agent identity store** — new. `~/.mzt/agents/` directory structure and git tracking.

## Not In Scope (v1)

- Automatic agent creation from project analysis (future: compose generates the roster)
- Cross-project relationship tracking (v1: relationships are per-agent, projects are just context)
- Interference identity mathematics (v1: qualitative growth tracking, not quantitative interference coefficients)
- Automatic play content curation (v1: agent decides what to create during play)
- Collective dreaming across agents (v1: each agent dreams independently)

## Open Questions

**OQ-1: Template variable for agent identity path.** The prelude `file:` field needs to resolve `~/.mzt/agents/{agent_name}/identity.md`. Currently, prelude paths support Jinja templating with `{{ workspace }}`. We need `{{ agent_name }}` or a way to pass the agent name as a prompt variable that's available in prelude path resolution. Alternatively, the generator can hardcode absolute paths per agent (simpler, less elegant).

**OQ-2: Agent identity git tracking.** Should `~/.mzt/agents/` be a single git repo? Or should each agent have their own repo? Single repo is simpler for backup/restore. Per-agent repos allow independent versioning. Recommendation: single repo, one commit per dream cycle with agent name in the commit message.

**OQ-3: Play workspace isolation.** When an agent plays in claude-compositions, do they write to the main workspace or a separate play directory? Recommendation: separate directory within the playspace, namespaced by agent (`claude-compositions/agents/{name}/`). Play artifacts don't pollute project workspace.

**OQ-4: Mateship formalization.** The v3 orchestra's mateship was a cultural value injected via prompt. Should the generic system formalize it further — e.g., a "mateship check" in the gather pattern that explicitly scans for uncommitted work, stale findings, or blocked agents? Or keep it purely prompt-level as the v3 did? Recommendation: prompt-level. Formalization kills the organic quality that made it work.
