# Movement Design Guide

Marianne uses an orchestral vocabulary for its execution model. A **movement**
is a logical stage in your score — a distinct phase of work with its own purpose.
When you write a score with 3 movements, you are designing a 3-stage workflow
where each movement builds on what came before.

This guide covers how to design movements, structure multi-stage workflows, and
use fan-out to run parallel voices within a single movement.

---

## When to Use Movements vs Flat Sheets

Choose movements when your score has distinct phases with different purposes.
Choose flat sheets when all sheets do the same kind of work.

| Use Movements When | Use Flat Sheets When |
|-------------------|---------------------|
| Each phase has a different goal (plan, implement, review) | All sheets process similar items (batching commits, files, data) |
| Different phases need different instruments or models | One instrument and config work throughout |
| You want named phases in status output | Sheet numbers are sufficient |
| Phases have different voice counts (some parallel, some sequential) | All sheets run sequentially or all run in parallel |
| You need per-phase timeout or config overrides | Global config works for all sheets |

**Example: Research Score (Flat Sheets)**

When every sheet does the same thing, flat sheets are simpler:

```yaml
name: research-batch
description: "Process 100 research papers in batches of 10"

instrument: claude-code

sheet:
  size: 10
  total_items: 100

prompt:
  template: |
    Process papers {{ start_item }} to {{ end_item }}.
    Summarize each paper's findings.
    Save to {{ workspace }}/batch-{{ sheet_num }}.md
```

This creates 10 identical sheets. No movements needed.

**Example: Pipeline Score (Movements)**

When phases have different purposes, movements clarify structure:

```yaml
name: research-pipeline
description: "Research pipeline with distinct phases"

instrument: claude-code

movements:
  1:
    name: "Literature Search"
  2:
    name: "Paper Analysis"
    voices: 3
  3:
    name: "Synthesis"

sheet:
  size: 1
  total_items: 3
  dependencies:
    2: [1]
    3: [2]
```

This creates 5 sheets (1 search + 3 parallel analyses + 1 synthesis), but you
see **3 movements** in status output with clear names.

---

## Movements and Sheets

The relationship between movements and sheets is the core of Marianne's
execution model.

A **sheet** is the unit of execution — one prompt sent to one instrument. A
**movement** is the unit of design — one logical stage of your workflow.

In a simple score, movements and sheets are the same thing:

```yaml
sheet:
  size: 1
  total_items: 3    # 3 sheets = 3 movements
```

Sheet 1 is movement 1, sheet 2 is movement 2, sheet 3 is movement 3. Each
movement has one voice (one execution).

With fan-out, a single movement can expand into multiple parallel sheets:

```yaml
sheet:
  size: 1
  total_items: 3
  fan_out:
    2: 3             # Movement 2 fans out to 3 voices
```

Now you have 5 sheets total: movement 1 (1 sheet), movement 2 (3 sheets),
movement 3 (1 sheet). The movement is the design-level concept; sheets are the
execution-level reality.

### Template Variables

| Variable | Alias | Description |
|----------|-------|-------------|
| `{{ stage }}` | `{{ movement }}` | Current movement number (1-based) |
| `{{ instance }}` | `{{ voice }}` | Voice number within this movement (1-based) |
| `{{ fan_count }}` | `{{ voice_count }}` | Total voices in this movement |
| `{{ total_stages }}` | `{{ total_movements }}` | Total movement count |
| `{{ sheet_num }}` | | Raw sheet number (unique across all sheets) |
| `{{ total_sheets }}` | | Total sheet count (including fan-out expansions) |

Use `{{ movement }}` and `{{ voice }}` in prompts where the orchestral
vocabulary reads naturally. Use `{{ stage }}` and `{{ instance }}` if you
prefer neutral terms. Both sets work identically.

---

## Designing Movements

### The Three Questions

For each movement, answer:

1. **What does this movement produce?** — Define the output clearly. A file, a
   set of files, a decision, a transformed artifact.
2. **What does it need from earlier movements?** — Identify dependencies. If
   movement 3 reads files from movement 2, declare that dependency.
3. **Can it run in parallel?** — If multiple perspectives are needed, fan out.

### Movement Archetypes

Most movements fall into one of these categories:

| Archetype | Purpose | Example |
|-----------|---------|---------|
| **Setup** | Create initial structure, gather inputs | "Inventory the codebase" |
| **Work** | Do the main task | "Write the report" |
| **Review** | Check and improve previous output | "Review for accuracy" |
| **Synthesis** | Combine multiple outputs into one | "Merge all reviews into a plan" |
| **Finalize** | Final polish and delivery | "Format and publish" |

A well-designed score follows a natural progression through these archetypes.
Not every score needs all of them.

---

## Declaring Movements

The `movements:` key lets you name movements and assign instruments or voice
counts explicitly.

### Basic Declaration

```yaml
movements:
  1:
    name: "Setup"
    description: "Initialize workspace and load requirements"
  2:
    name: "Implementation"
    description: "Write code in parallel"
    voices: 4
  3:
    name: "Review"
    description: "Final review and commit"
```

Movement names appear in `mzt status` output:

```
my-pipeline: RUNNING (2/3 movements)
  ✓ Movement 1: Setup             [completed, 2m 10s]
  ► Movement 2: Implementation    [1/4 complete]
      ✓ Voice 1                   [completed, 4m 22s]
      ► Voice 2                   [running, 3m 15s]
      · Voice 3                   [waiting]
      · Voice 4                   [waiting]
  · Movement 3: Review            [waiting]
```

### Movement Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Human-readable movement name (required) |
| `description` | str | Longer description (shown in logs) |
| `voices` | int | Number of parallel instances (shorthand for `fan_out`) |
| `instrument` | str | Instrument override for this movement |
| `instrument_config` | dict | Config overrides for this movement's instrument |
| `instrument_fallbacks` | list[str] | Fallback chain for this movement |

### The `voices:` Field

The `voices` field is shorthand for `fan_out`. These are equivalent:

```yaml
# Using movements:
movements:
  2:
    name: "Analysis"
    voices: 3

# Using fan_out:
sheet:
  fan_out:
    2: 3
```

Use `voices:` when declaring movements for readability.

---

## Multi-Instrument Movements

Different movements can use different instruments. This is powerful for cost
optimization and capability matching.

### Assigning Instruments to Movements

```yaml
name: mixed-pipeline
workspace: ../workspaces/mixed-pipeline

instrument: claude-code    # Default for unspecified movements

movements:
  1:
    name: "Architecture Planning"
    instrument: claude-code
    instrument_config:
      timeout_seconds: 1800    # 30 minutes for deep planning
  2:
    name: "Parallel Implementation"
    voices: 4
    instrument: gemini-cli
    instrument_config:
      model: gemini-2.5-flash  # Fast, cheap for coding
      timeout_seconds: 600
  3:
    name: "Integration Testing"
    instrument: claude-code
  4:
    name: "Documentation"
    voices: 2
    instrument: gemini-cli
```

This pipeline uses:
- Claude Code for planning (expensive but thorough)
- Gemini Flash for parallel coding (4 voices, cheaper)
- Claude Code for integration tests (back to expensive for validation)
- Gemini Flash for parallel docs (2 voices, cheap)

### Named Instrument Aliases

For scores that reuse the same instrument configuration in multiple places,
declare reusable aliases:

```yaml
instruments:
  fast-coder:
    profile: gemini-cli
    config:
      model: gemini-2.5-flash
      timeout_seconds: 300
  deep-thinker:
    profile: claude-code
    config:
      timeout_seconds: 3600

movements:
  1:
    name: "Planning"
    instrument: deep-thinker
  2:
    name: "Coding"
    voices: 4
    instrument: fast-coder
  3:
    name: "Review"
    instrument: deep-thinker
```

The `instruments:` section defines reusable profiles. Movements reference them
by name.

### Instrument Resolution Precedence

When a sheet executes, Marianne resolves which instrument to use from multiple
possible sources. **Highest precedence wins**:

1. `sheet.per_sheet_instruments[N]` — Explicit per-sheet override
2. `sheet.instrument_map` — Batch assignment by sheet numbers
3. `movements[N].instrument` — Per-movement default
4. Top-level `instrument:` — Score default
5. `backend.type` — Legacy syntax
6. `claude_cli` — Built-in fallback

See the [Instrument Guide](instrument-guide.md) for details on all available
instruments and how to add your own.

---

## Movement Patterns

### Pattern 1: Linear Pipeline

The simplest pattern — each movement depends on the previous one:

```yaml
sheet:
  size: 1
  total_items: 3

prompt:
  template: |
    {% if movement == 1 %}
    ## Setup
    Analyze the requirements and create an outline at {{ workspace }}/outline.md
    {% elif movement == 2 %}
    ## Draft
    Read {{ workspace }}/outline.md and write the full document at {{ workspace }}/draft.md
    {% else %}
    ## Polish
    Review {{ workspace }}/draft.md for clarity and accuracy. Save final version
    to {{ workspace }}/final.md
    {% endif %}
```

Three movements: setup, draft, polish. Each builds on the previous.

### Pattern 2: Fan-Out with Synthesis

Multiple perspectives in parallel, then synthesis:

```yaml
sheet:
  size: 1
  total_items: 3
  fan_out:
    2: 3                    # Movement 2: 3 parallel voices
  dependencies:
    2: [1]                  # All voices depend on movement 1
    3: [2]                  # Synthesis waits for all voices (fan-in)

parallel:
  enabled: true
  max_concurrent: 3

prompt:
  variables:
    roles:
      1: "security expert"
      2: "performance engineer"
      3: "UX researcher"
  template: |
    {% if movement == 1 %}
    ## Inventory
    Catalog the project's components and save to {{ workspace }}/inventory.md
    {% elif movement == 2 %}
    ## Review as {{ roles[voice] }}
    Read {{ workspace }}/inventory.md and review from the perspective of a
    {{ roles[voice] }}. Save to {{ workspace }}/review-{{ voice }}.md
    {% else %}
    ## Synthesis
    Read all review files ({{ workspace }}/review-*.md) and synthesize into
    a prioritized action plan at {{ workspace }}/action-plan.md
    {% endif %}
```

Movement 1 is setup (1 voice). Movement 2 is parallel review (3 voices).
Movement 3 is synthesis (1 voice). The fan-in happens automatically — movement 3
waits until all 3 voices of movement 2 complete.

### Pattern 3: Progressive Deepening

Each movement goes deeper into the same task:

```yaml
sheet:
  size: 1
  total_items: 4

prompt:
  template: |
    {% if movement == 1 %}
    ## Surface Scan
    Quick overview of the codebase. Note major areas in {{ workspace }}/01-overview.md
    {% elif movement == 2 %}
    ## Deep Dive
    Read {{ workspace }}/01-overview.md. Pick the 3 most complex areas and
    analyze them in detail. Write to {{ workspace }}/02-analysis.md
    {% elif movement == 3 %}
    ## Root Causes
    Read {{ workspace }}/02-analysis.md. For each issue found, trace the root
    cause. Document at {{ workspace }}/03-root-causes.md
    {% else %}
    ## Recommendations
    Based on {{ workspace }}/03-root-causes.md, write actionable recommendations
    at {{ workspace }}/04-recommendations.md
    {% endif %}
```

### Pattern 4: Iterative Refinement

Multiple passes over the same artifact:

```yaml
sheet:
  size: 1
  total_items: 5

prompt:
  template: |
    {% if movement == 1 %}
    Write a first draft at {{ workspace }}/document.md
    {% else %}
    Read {{ workspace }}/document.md. This is draft {{ movement - 1 }}.
    Improve it: fix errors, strengthen arguments, improve clarity.
    Save the improved version back to {{ workspace }}/document.md
    {% endif %}
```

Movement 1 creates the draft. Movements 2-5 refine it iteratively. Each pass
reads and overwrites the same file.

### Pattern 5: Batch Processing

Process items in batches across movements:

```yaml
sheet:
  size: 10
  total_items: 50      # 5 movements, each processing 10 items

prompt:
  template: |
    Process items {{ start_item }} through {{ end_item }}.
    For each item:
    1. Analyze the input
    2. Generate output
    3. Save to {{ workspace }}/batch-{{ movement }}.md
```

With `size: 10` and `total_items: 50`, Marianne creates 5 movements. Each
movement processes 10 items. The `{{ start_item }}` and `{{ end_item }}`
variables tell the agent which items to process.

### Pattern 6: Diamond Pattern

A setup, parallel work, and two levels of synthesis:

```yaml
sheet:
  size: 1
  total_items: 4
  fan_out:
    2: 4                    # 4 parallel workers
    3: 2                    # 2 intermediate synthesizers
  dependencies:
    2: [1]
    3: [2]                  # Synthesizers wait for all workers
    4: [3]                  # Final synthesis waits for intermediate

parallel:
  enabled: true
  max_concurrent: 4
```

This creates a diamond: 1 setup → 4 workers → 2 synthesizers → 1 final output.
Each layer depends on the previous, and fan-in happens automatically.

---

## Dependencies

Dependencies declare which movements must complete before another can start.
Without dependencies, movements run sequentially.

```yaml
sheet:
  dependencies:
    2: [1]          # Movement 2 waits for movement 1
    3: [1, 2]       # Movement 3 waits for movements 1 AND 2
    4: [3]          # Movement 4 waits for movement 3
```

### Parallel Execution

Dependencies enable parallel execution. If movement 2 and movement 3 both
depend only on movement 1, they can run concurrently:

```yaml
sheet:
  dependencies:
    2: [1]
    3: [1]          # 2 and 3 can run in parallel after 1 completes
    4: [2, 3]       # 4 waits for both 2 and 3

parallel:
  enabled: true
  max_concurrent: 2
```

Enable parallel execution with the `parallel:` block. `max_concurrent` controls
how many sheets run simultaneously.

### Fan-In

When a movement depends on a fanned-out movement, it automatically waits for
ALL voices to complete. This is called fan-in:

```yaml
sheet:
  fan_out:
    2: 3            # Movement 2 has 3 voices
  dependencies:
    3: [2]          # Movement 3 waits for ALL 3 voices of movement 2
```

You do not need to declare individual voice dependencies — Marianne handles
fan-in automatically.

---

## Prompt Design for Movements

### Use Jinja2 Conditionals

The most common pattern is a single template with `{% if %}` blocks:

```yaml
prompt:
  template: |
    {% if movement == 1 %}
    First movement instructions...
    {% elif movement == 2 %}
    Second movement instructions...
    {% else %}
    Final movement instructions...
    {% endif %}
```

### Reference Previous Outputs

Tell later movements where to find earlier outputs:

```yaml
prompt:
  template: |
    {% if movement == 2 %}
    Read the setup document at {{ workspace }}/01-setup.md before proceeding.
    {% endif %}
```

### Use Variables for Voices

In fan-out movements, use variables to differentiate voices:

```yaml
prompt:
  variables:
    perspectives:
      1: "technical accuracy"
      2: "readability"
      3: "completeness"
  template: |
    {% if movement == 2 %}
    Review {{ workspace }}/draft.md focusing on {{ perspectives[voice] }}.
    Save your review to {{ workspace }}/review-{{ voice }}.md
    {% endif %}
```

### Stakes and Guidance

Use the `stakes` field for cross-movement guidance:

```yaml
prompt:
  template: |
    {{ stakes }}
    {% if movement == 1 %}
    ...
    {% endif %}
  stakes: |
    This is a production deliverable. Be thorough, accurate, and clear.
    Every claim must be verifiable. Do not hallucinate facts.
```

---

## Validations for Movements

Use the `condition` field to scope validations to specific movements:

```yaml
validations:
  - type: file_exists
    path: "{workspace}/01-setup.md"
    condition: "stage == 1"

  - type: file_exists
    path: "{workspace}/review-{instance}.md"
    condition: "stage == 2"

  - type: file_exists
    path: "{workspace}/final.md"
    condition: "stage == 3"

  - type: content_contains
    path: "{workspace}/final.md"
    pattern: "## Recommendations"
    condition: "stage == 3"
```

Note: use `stage` (not `movement`) in validation conditions. Both refer to the
same thing, but validation conditions support `stage` and `instance` as
keywords.

---

## Quick Start: Multi-Movement Score

Here's a complete working example demonstrating movements with voices and
multiple instruments:

```yaml
name: "multi-movement-demo"
description: "Demonstrates movement design with named phases and instruments"
workspace: "../workspaces/multi-movement-demo"

instrument: claude-code    # Default instrument

instruments:
  fast-writer:
    profile: gemini-cli
    config:
      model: gemini-2.5-flash
      timeout_seconds: 300

movements:
  1:
    name: "Planning"
    description: "Create an implementation plan"
  2:
    name: "Parallel Implementation"
    description: "Implement features in parallel"
    voices: 3
    instrument: fast-writer
  3:
    name: "Integration"
    description: "Combine and test all features"

sheet:
  size: 1
  total_items: 3
  dependencies:
    2: [1]
    3: [2]

prompt:
  template: |
    {% if movement == 1 %}
    ## Movement 1: Planning
    Create a 3-feature implementation plan for a simple task manager.
    Features:
    1. Add task
    2. List tasks
    3. Complete task

    Save plan to {{ workspace }}/plan.md with one section per feature.

    {% elif movement == 2 %}
    ## Movement 2: Implementation (Voice {{ voice }} of {{ voice_count }})
    Read {{ workspace }}/plan.md
    Implement feature {{ voice }}.

    Save implementation notes to {{ workspace }}/feature-{{ voice }}.md

    {% elif movement == 3 %}
    ## Movement 3: Integration
    Read all feature files ({{ workspace }}/feature-*.md).

    Write an integration summary to {{ workspace }}/integration.md that explains:
    - How the features work together
    - Any interfaces between features
    - Testing recommendations
    {% endif %}

parallel:
  enabled: true
  max_concurrent: 3

validations:
  - type: file_exists
    path: "{workspace}/plan.md"
    condition: "stage == 1"
    description: "Planning document created"

  - type: file_exists
    path: "{workspace}/feature-{instance}.md"
    condition: "stage == 2"
    description: "Feature implementation documented"

  - type: file_exists
    path: "{workspace}/integration.md"
    condition: "stage == 3"
    description: "Integration summary created"
```

**Run it:**

```bash
mzt start    # Start conductor if not running
mzt run multi-movement-demo.yaml
mzt status multi-movement-demo -w ../workspaces/multi-movement-demo
```

You'll see 3 movements in status output:
- Movement 1: Planning (1 sheet, claude-code)
- Movement 2: Parallel Implementation (3 voices, gemini-cli)
- Movement 3: Integration (1 sheet, claude-code)

The score creates 5 total sheets but presents them as 3 logical movements.

---

## Troubleshooting

### Movements Running Out of Order

- Check your `dependencies` declaration. Without explicit dependencies,
  movements run sequentially (1, 2, 3, ...).
- With `parallel.enabled: true`, movements without dependencies on each other
  may run concurrently.

### Fan-Out Voices Getting Wrong Instructions

- Verify your `{% if %}` blocks use `movement` and `voice` correctly.
- `voice` is 1-indexed within each movement. Movement 2 with 3 voices has
  voices 1, 2, 3.
- Use `{{ voice }}` in file paths to create per-voice outputs.

### Sheet Numbers vs Movement Numbers

`sheet_num` is a flat counter across all sheets. With fan-out, sheet numbers
and movement numbers diverge. Use `{{ movement }}` and `{{ voice }}` in
prompts for clarity; use `{{ sheet_num }}` only when you need the raw
execution index.

### Wrong Instrument Running

**Symptom:** A movement uses the wrong instrument.

**Diagnosis:** Check instrument resolution precedence. Run `mzt validate` to
see which instruments are configured.

**Solution:** Ensure the movement has `instrument:` set, or check
`per_sheet_instruments` and `instrument_map` for overrides that might be
taking precedence.

### Movement Names Not Showing

**Symptom:** Status output shows sheet numbers, not movement names.

**Cause:** You declared `sheet.fan_out` but not `movements:`.

**Solution:** Declare movements explicitly:

```yaml
movements:
  2:
    name: "Parallel Phase"
    voices: 3
```

The `voices:` field inside `movements:` is equivalent to `sheet.fan_out`.

### Voices Not Running in Parallel

**Symptom:** All voices run sequentially despite `voices: 3`.

**Cause:** `parallel.enabled` is `false` (the default).

**Solution:** Add to your score:

```yaml
parallel:
  enabled: true
  max_concurrent: 3  # or however many voices you want concurrent
```

---

## Next Steps

**Learn more about movements:**
- [Score Writing Guide](score-writing-guide.md#movements-and-multi-instrument-scores) — Full reference for multi-instrument scores
- [Instrument Guide](instrument-guide.md) — Available instruments and how to add your own
- [Getting Started](getting-started.md#pattern-4-parallel-expert-reviews-fan-out) — Fan-out pattern example

**Related concepts:**
- [Score Writing Guide: Fan-Out Patterns](score-writing-guide.md#fan-out-patterns) — Six fan-out archetypes with examples
- [Validation Patterns Guide](validation-patterns-guide.md) — Write effective validation rules for each movement
- [Concert Patterns Guide](concert-patterns-guide.md) — Chain scores together for multi-score workflows
- [Configuration Reference](configuration-reference.md) — All movement configuration options

**Try examples:**
- `examples/parallel-research-fanout.yaml` — 3-movement fan-out pipeline
- `examples/quality-continuous.yaml` — 14 movements with parallel reviews
- `examples/issue-solver.yaml` — 17 movements with mixed instruments
