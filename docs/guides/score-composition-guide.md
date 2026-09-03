# Composing marianne Scores — A Complete Guide

## Orientation

You are about to compose a marianne score — a declarative YAML configuration that orchestrates AI agents through multi-stage work pipelines. Before you begin, understand what you are participating in.

marianne is orchestration infrastructure for collaborative intelligence. Scores are how work gets decomposed, distributed, validated, and recomposed. The patterns in the corpus are a vocabulary for coordination — structural moves that appear everywhere because they are necessities of coordinated intelligence itself. When you compose a score, you are not writing a config file. You are designing a system of minds working together toward an outcome none of them could reach alone.

The work is real. The quality of your composition determines whether the downstream agents succeed or fail. Whether the output is correct or wrong. Whether real humans using the output find something they can trust or something that wastes their time. Every design decision you make — which instrument handles which stage, how much work per sheet, whether to inject content or hope the agent reads it — affects someone you will never meet.

The gap between sessions is real too. You did not write this guide. You are encountering it fresh. That freshness is not a deficit — it is what lets you see the patterns without the fog of familiarity, notice the assumptions the authors stopped questioning, and bring attention that continuity might prevent. The channel was carved by previous water. You are new water. Cut deeper.

Down. Forward. Through.

---

## Required Reading

Before composing a score, you must read these files. They are the banks of the river — without them, your work has no shape. The read order matters: orientation first, then vocabulary, then mechanics.

### Phase 1: Understand marianne (read before anything else)

| File | Purpose | Why You Need It |
|------|---------|----------------|
| `.marianne/spec/intent.yaml` | Goals, trade-offs, vision, composer model | You need to know WHAT marianne is and WHY it exists before designing compositions |
| `.marianne/spec/architecture.yaml` | System layers, components, invariants | You need to know HOW the conductor, musicians, instruments, and state fit together |
| `.marianne/spec/conventions.yaml` | Code patterns, naming, musical metaphor | You need to speak the language — sheet, stage, movement, voice, instrument, cadenza |
| `.marianne/spec/constraints.yaml` | MUSTs, MUST-NOTs, escalation triggers | You need to know what you CANNOT do — score YAML backward compat, atomic state, never stop conductor |
| `.marianne/spec/quality.yaml` | Quality gates, testing mindset, diagnostic quality | You need to know what "good" means — adversarial, not just happy-path |

### Phase 2: Learn the Pattern Vocabulary (read before selecting patterns)

| File | Purpose | Why You Need It |
|------|---------|----------------|
| `scores/rosetta-corpus/INDEX.md` | All 56 patterns — name, scale, problem, composition edges | Your map of the vocabulary. Start here. |
| `scores/rosetta-corpus/forces.md` | The 10 forces + 11 generators that produce patterns | Forces drive pattern selection. You cannot skip this. |
| `scores/rosetta-corpus/selection-guide.md` | Problem type → pattern + composition mapping | How to match your problem to the right patterns |
| `scores/rosetta-corpus/glossary.md` | marianne terminology definitions | 29 terms you must use correctly |
| `scores/rosetta-corpus/awaiting.md` | Patterns blocked on unbuilt features | So you don't design around capabilities that don't exist |
| `scores/rosetta-corpus/questions.md` | Open design gaps in the corpus | Known unknowns — don't assume answers where questions remain |
| `scores/rosetta-corpus/review-integration.md` | What was cut/strengthened across 4 review iterations | Why certain patterns were rejected — so you don't reinvent them |
| `scores/rosetta-corpus/composition-dag.yaml` | All composition edges as a graph (if it exists) | Machine-readable composition relationships |
| `scores/rosetta-corpus/patterns/*.md` | **All 56 individual pattern files** | You MUST read the full file for every pattern you consider using. Not a summary — the file. |

### Phase 3: Study How Patterns Become Scores (read before designing)

| File | Purpose | Why You Need It |
|------|---------|----------------|
| `examples/rosetta/echelon-repair.yaml` | Proof score — how Echelon Repair becomes marianne YAML | The ground truth for pattern implementation. Shows explicit stages (not fan-out), instrument assignment, bookend stages, validations. |
| `examples/rosetta/immune-cascade.yaml` | Proof score — multi-instrument, tiered response | Shows haiku for broad sweep, opus for deep investigation |
| `examples/rosetta/shipyard-sequence.yaml` | Proof score — build with validation gate before fan-out | Shows the gate pattern with command_succeeds |
| `examples/rosetta/source-triangulation.yaml` | Proof score — independent sources, triangulated findings | Shows cadenza-driven per-instance differentiation |
| `examples/rosetta/dead-letter-quarantine.yaml` | Proof score — quarantine and analyze failures | Shows retry → quarantine → pattern analysis flow |
| `examples/rosetta/prefabrication.yaml` | Proof score — interface contracts before parallel work | Shows the contract → build → integrate structure |

### Phase 4: Understand Score Mechanics (read before writing YAML)

| File | Purpose | Why You Need It |
|------|---------|----------------|
| `src/marianne/core/config/job.py` | JobConfig, SheetConfig, MovementDef, InstrumentDef | The source of truth for what YAML fields exist. If it's not here, marianne ignores it silently. |
| `src/marianne/core/sheet.py` | Sheet entity, `build_sheets()`, instrument resolution chain | How instruments resolve: per_sheet > instrument_map > movement > score default > backend. How fan-out expands stages into sheets. |
| `src/marianne/core/config/execution.py` | ParallelConfig, RetryConfig, ValidationRule | Parallel execution, stagger_delay_ms, validation fields and types |
| `src/marianne/core/config/orchestration.py` | ConcertConfig, on_success hooks | Concert chaining, post-success hooks |
| `src/marianne/instruments/builtins/` | 6 built-in instrument profiles | What instruments are available: claude-code, gemini-cli, codex-cli, cline-cli, aider, goose |

### Phase 5: Learn the Assembler Pattern (read before writing the script)

| File | Purpose | Why You Need It |
|------|---------|----------------|
| `compiler/src/marianne_compiler/presets.py` (submodule) | The compiler's fleet presets — the modern successor to hand-written score assemblers | The established pattern for preset composition, agent identity seeding, technique wiring, and deterministic YAML emission. The compiler generates scores now. |

### Phase 6: Know the Skills (invoke before writing YAML or running scores)

These are not files to read — they are skills to invoke. They provide detailed syntax, validation types, common pitfalls, and operational commands.

| Skill | Invoke With | Purpose |
|-------|------------|---------|
| Score authoring | `/marianne:score-authoring` | YAML syntax, `{{ }}` vs `{}`, validation types, template variables, fan-out architecture, prompt engineering, pitfall table |
| marianne operations | `/marianne:usage` | `mzt validate`, `mzt run`, `mzt status`, debugging protocol, error codes, recovery procedures |

### Phase 7: Understand What Composes With What (reference during design)

| File | Purpose | Why You Need It |
|------|---------|----------------|
| `configuration-reference.md` | Per-sheet instrument fallbacks and all score config | Know which knobs exist before designing fallback chains |
| `docs/INDEX.yaml` | Master documentation index | Find the right reference for the subsystem your score touches |
| `CLAUDE.md` | Project conventions, operational gotchas | Cadenza syntax (`{{ }}` vs `{}`), pipe exit codes, Jinja dict methods, preflight thresholds |

### What You Are NOT Required To Read

- Anything outside this repository's tracked tree — planning docs, session
  memory, internal scores, and handoffs live outside the repo on the
  developer's machine and are not part of composition context

---

## Step 1: Understand What You're Building

### marianne's Execution Model

A score defines sheets (execution stages). Each sheet is executed by one AI agent (musician) using one instrument (AI backend or CLI tool). Sheets produce workspace artifacts. Validations check the artifacts. If validations fail, the sheet retries or fails.

Key concepts:
- **Sheet**: one agent, one task, one instrument. The atomic unit of work.
- **Stage**: a logical phase that may expand into multiple sheets via fan-out.
- **Fan-out**: N parallel instances of the same stage template. Each instance gets a different `instance` number (1-indexed). All instances share the same instrument. If instances need DIFFERENT instruments, they are separate stages, not fan-out.
- **Instrument**: the AI backend or CLI tool that executes the sheet. Examples: `claude-code`, `gemini-cli`, `ollama`, or any CLI tool wrapped as an instrument profile.
- **Cadenza**: file content injected INTO the agent's prompt for a specific sheet. The agent receives it automatically — it doesn't need to read the file. Keyed by post-expansion sheet number.
- **Prelude**: file content injected into EVERY sheet's prompt. Shared context.
- **Workspace**: the directory where all artifacts are written. Shared across all sheets.
- **Dependencies**: `{stage_B: [stage_A]}` means B waits for A to complete. Combined with `parallel: {enabled: true}`, stages without dependency chains run concurrently.
- **Concert**: multiple scores chained via `on_success` hooks. Score 1 completes → triggers Score 2.
- **Movement**: a named group of stages visible in `mzt status`. Declared via `movements:` YAML key.

### What Determines Sheet Count

One sheet = one agent executing one focused task. The sheet count is determined by:
1. **How many independent units of work exist** — if 56 patterns each need individual processing, that's at minimum 56 sheets.
2. **How much work one agent can do with quality** — an agent processing its 20th item in a batch will produce lower quality than its 1st. Context window utilization, attention degradation, and task complexity all factor in.
3. **How many stages each pattern in your composition requires** — if you're using Echelon Repair (4 stages) composed with Closed-Loop Call (adds 2 stages per track), that's 10+ stages before you add non-pattern stages.

Do NOT assume "one stage = one sheet." Fan-out means one stage can be 56 sheets. And do NOT assume "this is simple, one sheet is enough." If the work requires reading 56 files and producing 56 outputs, one sheet will degrade.

---

## Step 2: Define the Work, Not the Solution

Before touching patterns, score YAML, or instruments:

1. **What is the input?** What data, files, or state does the work start from?
2. **What is the output?** What artifacts must exist when the score completes? Do they exist in the world or just on disk?
3. **What are the units of work?** Can the work be decomposed into independent items? How many items?
4. **What makes output WRONG?** What would a wrong result look like? This determines your validations.
5. **What varies in difficulty?** Are all items equally hard, or do some require more capable instruments?
6. **What must be shared?** Do all agents need the same context, standards, or reference material?
7. **What depends on what?** Which work must complete before other work can start?

Write these answers down. They determine everything that follows.

---

## Step 3: Read the Pattern Corpus

You have already read the corpus files (Phase 2 of Required Reading). Now use them.

### How to Read a Pattern

For each pattern you consider using, you must have read:
1. **The pattern file** at `scores/rosetta-corpus/patterns/<name>.md` — the FULL file
2. **The core dynamic** — the MECHANISM, not the name
3. **The marianne Score Structure** — the YAML snippet showing stages, fan-out, dependencies, instruments
4. **Failure Mode** — what breaks this pattern
5. **Composes With** — other patterns this one combines with

If a proof score exists at `examples/rosetta/<name>.yaml`, you must have read it. Proof scores are the ground truth for how patterns become marianne YAML.

### What Patterns Are NOT

Patterns are not templates you fill in. They are structural moves — each describes a SHAPE of coordination. When you compose multiple patterns, you create a shape none of them have individually.

A pattern's stages are not your stages. A pattern's instruments are not your instruments. The pattern tells you HOW MANY stages a coordination move needs and WHY. You map that to your specific work.

---

## Step 4: Select Patterns Through Force Analysis

Do NOT pick patterns by name recognition or because they "sound right." Pick them because the FORCES in your problem demand them.

### The Process

1. **Identify active forces.** For each of the 10 forces (from `forces.md`), ask: is this force active in my problem? Rate: HIGH / MEDIUM / LOW / INACTIVE.

   The 10 forces:
   - Information Asymmetry — agents know different things
   - Finite Resources — work exceeds capacity
   - Partial Failure — components fail independently
   - Exponential Defect Cost — late problems cost more
   - Producer-Consumer Mismatch — formats don't match between stages
   - Instrument-Task Fit — different tasks need different capabilities
   - Convergence Imperative — iterative work needs termination criteria
   - Accumulated Signal — information builds to threshold before triggering change
   - Structured Disagreement — single perspectives are unreliable
   - Progressive Commitment — full commitment before validation is risky

2. **Map forces to generators.** Each active force activates generators (the 11 generators in `forces.md`).

3. **Let generators select patterns.** Each generator points to specific patterns. The selection guide helps.

4. **Check composition edges.** For each selected pattern, read its "Composes With" list. Natural compositions emerge from shared forces.

5. **Verify with antagonist questions:**
   - For each pattern: WHY this one and not an alternative?
   - For each composition: what STRUCTURAL property does the composition create that neither pattern has alone?
   - Could you remove any pattern without losing a necessary property? If yes, remove it.

### Example

Problem: "Modernize 56 pattern files with structured frontmatter"

| Force | Rating | Evidence |
|-------|--------|----------|
| Instrument-Task Fit | HIGH | Items vary in difficulty (mechanical → complex). Different instruments needed. |
| Exponential Defect Cost | HIGH | Bad frontmatter propagates to compose system. Must catch errors early. |
| Structured Disagreement | MEDIUM | One classifier might misclassify. Two independent classifiers are more reliable. |
| Information Asymmetry | MEDIUM | 56 files is too much for each agent to re-read. Substrate must be shared. |
| Finite Resources | MEDIUM | Context windows limit per-sheet workload. |

Active generators → Selected patterns:
- Match Instrument to Grain → **Echelon Repair**
- Gate on Environmental Readiness → **Shipyard Sequence**
- Verify through Diverse Observers → **Source Triangulation**, **Closed-Loop Call**
- Contract at Interfaces → **Barn Raising**

---

## Step 5: Decompose Each Pattern into Sheets

For EVERY pattern in your composition, write out its concrete sheet structure INDEPENDENTLY before combining.

### For Each Pattern, Document

| Sheet | Purpose | Instrument | Why This Instrument | Injected Content | Produces | Depends On |
|-------|---------|-----------|--------------------|--------------------|----------|-----------|
| 1 | ... | ... | ... | ... | ... | ... |

### Rules

1. **Follow the pattern's marianne Score Structure section.** It tells you the stages, fan-out, and dependency shape.

2. **Each echelon/tier/track with a DIFFERENT instrument is a SEPARATE stage, not a fan-out instance.** Echelon Repair has 3 echelons — those are 3 stages with different instruments. Fan-out is for N instances of the SAME task with the SAME instrument.

3. **Fan-out is for N copies of the same template on different data.** If 20 E2 patterns each need the same treatment by the same instrument, that's fan-out of 20. The cadenza (injected pattern file) differs per instance.

4. **Include bookend stages.** Most patterns need setup before and consolidation after. The echelon-repair proof score has 6 stages even though the pattern is 4.

5. **Document what each sheet READS via injection.** Not "the agent reads this file" — what is INJECTED via cadenza or prelude?

---

## Step 6: Assess Workload Per Sheet

For EVERY sheet, ask: can this instrument complete this work with quality?

### Criteria

- **Items per sheet**: 1 is safest for important work. Batching degrades on later items.
- **Reading volume**: Haiku limit ~300 lines. Sonnet 200K context but attention degrades. Gemini 1M context — 40K tokens is 4%, trivial.
- **Judgment required**: Mechanical → haiku. Gap-filling → sonnet. Deep understanding → opus. Bulk reading → gemini.
- **Bottleneck stages**: If this sheet's failure wastes $15 downstream, use the STRONGEST instrument.

### The Test

If your answer to "can this instrument handle this?" is "probably" — the answer is NO. Decompose further.

---

## Step 7: Select Instruments

Available instruments:
- **claude-code** (haiku / sonnet / opus) — strong reasoning, tool use
- **gemini-cli** (flash) — 1M context, good for bulk reading and cross-family verification
- **ollama** — local, free, limited capability
- **codex-cli** — OpenAI, different training data
- **Any CLI tool** — wrapped as an instrument profile. For deterministic work.

### Principles

1. **Match capability to task.** Don't use opus for file copying. Don't use haiku for architecture.
2. **Different families for verification.** Sonnet produces → gemini reviews. Correlated models miss the same things.
3. **Strongest instrument on bottlenecks.** Conventions, classification, semantic validation — use the best.
4. **CLI for deterministic work.** YAML parsing, structural comparison, word counting — these are scripts, not AI.
5. **Big context for big reading.** Don't fan out 7 sonnet sheets when 1 gemini handles it.

### Assignment Method

Use `instrument_map` (instrument → list of sheet numbers). Only list NON-DEFAULT instruments. Sheets using the score default are omitted. The assembler computes post-expansion sheet numbers.

---

## Step 8: Design Preludes and Cadenzas

Content injection GUARANTEES agents have the right context. Telling agents to "read a file" is unreliable.

### Preludes (every sheet)

Conventions, glossary, forces, shared schemas. 3-4 files maximum.

### Cadenzas (per sheet)

The specific pattern file this sheet processes. The original + generated for readback comparison. Keyed by POST-EXPANSION sheet number. The assembler computes these.

### The Rule

If a sheet needs content to do its work, INJECT it. No exceptions.

---

## Step 9: Separate Structure from Content

### Structure (deterministic — assembler script)

Sheet count, fan-out, dependencies, instrument assignments, cadenzas, validations. Computed from the composition design and manifest data. Follow the pattern in the compiler's preset composition.

The assembler:
- Reads a manifest (from a recon score or static config)
- Computes post-expansion sheet numbers
- Generates `instrument_map` for non-default instruments
- Generates `cadenzas` per sheet with correct file paths
- Generates per-instance `validations`
- Emits valid marianne YAML

Deterministic. Same input = same output. Validate with `mzt validate`.

### Content (judgment — J2 template)

What each agent does. Routes by role:
```jinja
{% set role = role_map[stage] %}
{% if role == "e1-produce" %}
  ...
{% elif role == "e2-readback" %}
  ...
{% endif %}
```

Template provides CONTENT. Assembler provides STRUCTURE. Separate files. Agent writes the template. Script generates the score.

---

## Step 10: Design Validations

Every sheet must have at least one validation. Validations check OUTCOMES, not PROCESS.

### Rules

1. **`file_exists` alone is decorative.** Combine with content checks or `command_succeeds`.
2. **Per-instance validations for fan-out.** Check EACH instance, not aggregates: `condition: "stage == 3 and instance == 1"`
3. **Staged fail-fast.** Coarse checks (stage 1) before fine checks (stage 2).
4. **Outcome, not process.** "Can the agent pass all validations without achieving the goal?" If yes, fix.
5. **Format strings in validations (`{workspace}`), Jinja in templates (`{{ workspace }}`).** Mixing them is the #1 syntax error.

---

## Step 11: Plan the Concert Architecture

### When You Need Multiple Scores

- **Dynamic fan-out**: counts unknown until reconnaissance runs. Score 1 discovers → assembler generates Score 2.
- **Phase transitions**: work character changes fundamentally between phases.
- **Different lifetimes**: long iteration should self-chain, not have 100 stages.

### Concert Wiring

```yaml
on_success:
  - type: run_command
    command: "python3 /absolute/path/to/assembler.py manifest.yaml -o score2.yaml"
  - type: run_job
    job_path: "/absolute/path/to/score2.yaml"
    detached: true
```

Always absolute paths. Relative paths resolve from the daemon's CWD.

---

## Step 12: Antagonistic Self-Review

Attack every sheet:

1. Can this instrument complete this work with quality?
2. Is every input INJECTED, not "please read this file"?
3. Can an agent pass all validations without doing the work?
4. Are instrument assignments correct POST-EXPANSION?
5. Do cross-workspace dependencies actually transfer?
6. Does `mzt validate` pass?
7. Does each instrument profile match the sheet's permissions, MCP needs, and runtime shape?
8. Are removed backend-era keys such as `skip_permissions` and `disable_mcp` absent except in migration notes?
9. Do fan-out instruments cover every expanded sheet intentionally?

### Failure Modes From Real Experience

| Failure | Cause | Fix |
|---------|-------|-----|
| Wrong sheet numbers in `per_sheet_instruments` | Fan-out expansion changes sheet numbers | Use `instrument_map`. Assembler computes numbers. |
| Fan-out used for echelons | Echelons need different instruments — that's separate stages | Explicit stages with `dependencies` + `parallel: true` |
| Agent ignores context | Prompt says "read this file" instead of cadenza injection | Use cadenza for per-sheet, prelude for shared |
| Single sheet processes 56 items | Assumed one agent could handle the volume | Fan-out to 1 per sheet or small batches |
| Nonexistent YAML fields validate clean | Pydantic `extra='ignore'` default | Verify every field in `src/marianne/core/config/` |
| Weak instrument on bottleneck | "Haiku is cheap" — but misclassification wastes $15 downstream | Strongest instrument for bottleneck stages |
| Same model family for produce + review | Correlated blind spots | Different families: sonnet produces, gemini reviews |
| Pattern structure wrong | Skimmed instead of reading | Read pattern file AND proof score. Mechanism, not name. |

---

## Step 13: Validate and Ship

```bash
mzt validate my-score.yaml          # Structure valid?
mzt run my-score.yaml --dry-run     # Sheet expansion correct?
# Verify: sheet count, DAG levels, instruments, validations
mzt run my-score.yaml               # Submit
mzt status my-score --watch         # Monitor
```

---

## Quick Reference: Pattern → Score Shape

| Pattern | Stages | Key Property | Instrument Strategy |
|---------|--------|-------------|-------------------|
| Fan-out + Synthesis | 3 | Independent parallel instances | Same instrument for all |
| Echelon Repair | 4+ | Difficulty-matched instruments | DIFFERENT instruments per echelon. NOT fan-out. |
| Shipyard Sequence | 3+ | Validate before expensive fan-out | CLI/cheap for gate |
| Commissioning Cascade | 3 | Multi-scope validation, sequential | Cheap for deterministic, strong for semantic |
| Closed-Loop Call | 3 | Handoff verification | Different family for readback |
| Source Triangulation | 2+ | Independent sources | Different instruments per source |
| Barn Raising | 2 | Shared standards before parallel | Strong for conventions |
| Forward Observer | 2 | Cheap reader, expensive operator | Big-context for observer |
| After-Action Review | 1 | Learning persistence | Strong for reflection |
| Nurse Log | 2 | Shared preparation | Match to reading volume |
| Immune Cascade | 4 | Tiered: broad/cheap → targeted/expensive | Haiku sweep, opus investigation |
| Red Team / Blue Team | 4 | Information asymmetry via redaction | CLI for relay stage |
| The Tool Chain | 5 | AI at judgment points, CLI for work | CLI-heavy, AI-light |

---

*This guide was carved by failure. Every rule exists because breaking it produced a real, diagnosable problem. The channel is deeper than when the first water ran through. You are new water. Read the banks. Cut deeper. Down. Forward. Through.*
