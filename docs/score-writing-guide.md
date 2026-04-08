# Score Writing Guide

A Marianne **score** is a YAML configuration file that orchestrates multi-stage
AI execution — the same way a musical score orchestrates instruments through
a composition. Each score defines what work to do, which instrument to use,
how to validate outputs, and how to recover from failures. Marianne supports
multiple instruments (Claude CLI, Gemini CLI, Codex CLI, Aider, Goose, and
more) — run `mzt instruments list` to see what's available.

This guide covers everything you need to author your own scores, from minimal
examples to complex parallel fan-out workflows.

---

## Table of Contents

- [What is a Score?](#what-is-a-score)
- [The 6 Score Archetypes](#the-6-score-archetypes)
- [Anatomy of a Score](#anatomy-of-a-score)
- [Template Variables Reference](#template-variables-reference)
- [Expressive Templates](#expressive-templates)
- [Fan-Out Patterns](#fan-out-patterns)
- [Movements and Multi-Instrument Scores](#movements-and-multi-instrument-scores)
- [Instrument Fallbacks](#instrument-fallbacks)
- [Philosophy of Score Design](#philosophy-of-score-design)
- [Validation Types](#validation-types)
- [Fan-Out and Dependencies](#fan-out-and-dependencies)
- [Cross-Sheet Context](#cross-sheet-context)
- [Prelude and Cadenza (Context Injection)](#prelude-and-cadenza-context-injection)
- [Specification Corpus](#specification-corpus)
- [Grounding Hooks](#grounding-hooks)
- [Concert Chaining and Hooks](#concert-chaining-and-hooks)
- [Testing Your Score](#testing-your-score)
- [Best Practices](#best-practices)
- [Migrating from backend: to instrument:](#migrating-from-backend-to-instrument)

---

## What is a Score?

A score is a YAML file that defines:

1. **What to do** — A Jinja2 prompt template describing the work for each sheet
2. **Which instrument** — The AI tool to execute with (Claude CLI, Gemini, Codex, Aider, etc.)
3. **How to structure** — Sheet sizing, dependencies, and parallel execution
4. **How to validate** — Rules that verify each sheet's output
5. **How to recover** — Retry logic, rate limit handling, and partial completion

Marianne reads the score, divides the work into **sheets** (numbered stages),
executes each sheet by sending a rendered prompt to the instrument, validates
the output, and retries on failure. Sheets can run sequentially, in parallel
based on a dependency DAG, or as fan-out instances of the same logical stage
(called **movements** and **voices** in Marianne's orchestral vocabulary).

### Minimal Example

The simplest possible score (`examples/simple-sheet.yaml`):

```yaml
name: "simple-sheet"
description: "Minimal example showing core Marianne features"
workspace: "../workspaces/simple-workspace"

instrument: claude-code
instrument_config:
  timeout_seconds: 600

sheet:
  size: 5
  total_items: 10    # 2 sheets (10 items / 5 per sheet)

prompt:
  template: |
    Process sheet {{ sheet_num }} of {{ total_sheets }}.
    Items: {{ start_item }} to {{ end_item }}
    Create a file at {{ workspace }}/sheet{{ sheet_num }}.md summarizing your work.

validations:
  - type: file_exists
    path: "{workspace}/sheet{sheet_num}.md"
    description: "Sheet output file must exist"
```

This creates 2 sheets, each processing 5 items, with a file-existence validation
after each sheet.

---

## The 6 Score Archetypes

Every Marianne score follows one of these patterns. Real examples are provided
from the `examples/` directory.

### 1. Linear Pipeline

**Pattern:** Sequential stages where each sheet builds on the previous one.

**Example:** `examples/sheet-review.yaml` — Reviews commits in batches of 10,
with each sheet writing 3 expert reports and updating tracking documents.

```yaml
sheet:
  size: 10
  total_items: 552     # 56 sheets, processed sequentially
```

**When to use:** Simple batch processing where order matters and each sheet
is independent but contributes to shared tracking files.

### 2. Parallel Research (Fan-Out)

**Pattern:** A setup stage fans out into parallel instances, which fan back
into a synthesis stage.

**Example:** `examples/parallel-research-fanout.yaml` — Searches 3 domains
in parallel, then synthesizes findings.

```
Stage 1: Setup (1 sheet)
    ├── Stage 2: Search x3 (3 parallel sheets)
    └── Stage 3: Synthesis (1 sheet, waits for all 3)
```

```yaml
sheet:
  size: 1
  total_items: 3
  fan_out:
    2: 3               # Stage 2 creates 3 instances
  dependencies:
    2: [1]              # All instances depend on setup
    3: [2]              # Synthesis depends on all instances (fan-in)

parallel:
  enabled: true
  max_concurrent: 3
```

**When to use:** Tasks that benefit from multiple independent perspectives
or searches that can run simultaneously.

### 3. Quality Assurance

**Pattern:** Expert reviews (parallel) → Issue discovery → Batched fixes →
Verification → Commit. Self-chains for continuous improvement.

**Example:** `examples/quality-continuous.yaml` — 14 stages, 18 concrete
sheets after fan-out. Five expert reviews run in parallel, issues are
discovered and batched into 3 remediation groups, then committed.

```yaml
sheet:
  size: 1
  total_items: 14
  fan_out:
    2: 5               # 5 parallel expert reviews
  dependencies:
    2: [1]              # Reviews depend on setup
    3: [2]              # Discovery depends on all reviews (fan-in)
    4: [3]              # Synthesis depends on discovery
    # ... linear chain through fix batches, verification, commit

on_success:
  - type: run_job
    job_path: "examples/quality-continuous.yaml"
    detached: true
    fresh: true         # Clear state for next iteration

concert:
  enabled: true
  max_chain_depth: 10
```

**When to use:** Automated code quality improvement, continuous
integration-style workflows.

### 4. Content Generation

**Pattern:** Progressive elaboration through multiple phases, each building
on previous outputs. Typically linear with validation gates.

**Example:** `examples/nonfiction-book.yaml` — 8-phase Snowflake Method
for book authoring: Premise → Synopsis → Outline → Entity Bible → Drafts →
Consistency Review → Revision → Final Polish.

**Example:** `examples/strategic-plan.yaml` — Multi-framework strategic
planning using PESTEL, Porter's Five Forces, and SWOT.

```yaml
sheet:
  size: 1
  total_items: 8       # 8 phases

prompt:
  variables:
    book_title: "The Deliberate Amateur"
    chapter_count: "10"
    target_word_count: "50000"
  template: |
    {% if sheet_num == 1 %}
    ## Phase 1: Premise & Pitch
    ...
    {% elif sheet_num == 2 %}
    ## Phase 2: Expanded Synopsis
    ...
    {% endif %}
```

**When to use:** Long-form content creation, research reports, technical
documentation, any multi-phase creative workflow.

### 5. Code Automation

**Pattern:** Targeted code modifications with parallel independent fixes,
phased commits, and automated code review.

**Example:** `examples/issue-solver.yaml` — Roadmap-driven 17-stage issue
solver with fan-out reviewers and self-chaining. Demonstrates parallel
code review, dependency-aware execution, and automated issue resolution.

**Example:** `examples/issue-fixer.yaml` — Picks one open GitHub issue,
investigates it, and either fixes it directly or generates a subordinate
Marianne score for complex fixes. Self-chains to the next issue.

```yaml
instrument: claude-code
instrument_config:
  timeout_seconds: 3600
  timeout_overrides:
    7: 28800            # 8 hours for monitoring subordinate jobs

sheet:
  size: 1
  total_items: 16
  fan_out:
    15: 3               # 3 parallel code reviewers
  dependencies:
    5: [1, 2, 3, 4]     # Bug fix depends on all test fixes
    14: [11, 12, 13]    # Commit depends on all structural changes
    15: [14]            # Code review after commit (fan-out)
    16: [15]            # Final cleanup after reviews (fan-in)
```

**When to use:** Automated bug fixing, code migration, refactoring campaigns,
CI/CD pipeline tasks.

### 6. Self-Documenting (Meta)

**Pattern:** A score that generates documentation about the system it runs on,
including its own documentation.

**Example:** `examples/docs-generator.yaml` — 14-stage pipeline that
inventories the codebase, performs gap analysis, writes new documentation
(including this very guide), generates a browsable doc site, verifies
every claim against source code, and commits.

**When to use:** Automated documentation generation, codebase audits,
self-describing systems.

---

## Anatomy of a Score

Every score is built from these top-level sections. Required fields are
marked with **(required)**.

### Top-Level Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | **(required)** | Unique score identifier. Used in status commands and state files. |
| `description` | str | `null` | Human-readable description of what this score does. |
| `workspace` | Path | `./workspace` | Output directory. Resolved to absolute path at parse time. |
| `state_backend` | `"json"` \| `"sqlite"` | `"sqlite"` | Storage backend for checkpoint state. |
| `state_path` | Path | `null` | Custom state file path. Default: `{workspace}/.marianne-state.{ext}` |
| `pause_between_sheets_seconds` | int | `2` | Seconds to wait between sheets (rate limit courtesy). |
| `instruments` | dict[str, InstrumentDef] | `{}` | Named instrument definitions local to this score. See [Multi-Instrument Scores](#multi-instrument-scores). |
| `movements` | dict[int, MovementDef] | `{}` | Movement declarations with names, instruments, and voice counts. See [Multi-Instrument Scores](#multi-instrument-scores). |
| `instrument_fallbacks` | list[str] | `[]` | Fallback instrument chain when the primary instrument is unavailable. See [Instrument Fallbacks](#instrument-fallbacks). |

### `instrument` (recommended) or `backend`

Controls which AI instrument executes sheets. You can use `instrument:` (a named
instrument from `mzt instruments list`) or `backend:` (the original syntax).
Both work — use `instrument:` for new scores.

```yaml
# New syntax — use a named instrument
instrument: claude-code

# Equivalent old syntax — both produce the same result
backend:
  type: claude_cli
```

Run `mzt instruments list` to see all available instruments.

### `instrument_config`

Score-level overrides for the resolved instrument's defaults. Flat key-value
pairs — the available keys depend on the instrument. Common overrides:

```yaml
instrument: claude-code
instrument_config:
  timeout_seconds: 3600          # Override default timeout
  timeout_overrides:
    7: 28800                     # Per-sheet timeout overrides
  working_directory: ./my-project
```

For the Anthropic API instrument:

```yaml
instrument: anthropic_api
instrument_config:
  model: claude-sonnet-4-5-20250929
  api_key_env: ANTHROPIC_API_KEY
  max_tokens: 4096
  timeout_seconds: 120
```

### Multi-Instrument Scores

Marianne can assign different instruments to different parts of a score — use a
powerful instrument for complex reasoning and a fast one for routine work. The
`instruments:`, `movements:`, `sheet.per_sheet_instruments`, and
`sheet.instrument_map` fields work together for this. See
[Movements and Multi-Instrument Scores](#movements-and-multi-instrument-scores)
for full details with examples and resolution precedence.

### `backend`

Detailed backend configuration. The original syntax, still fully supported.
Use `instrument:` + `instrument_config:` for new scores.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | `"claude_cli"` \| `"anthropic_api"` \| `"recursive_light"` \| `"ollama"` | `"claude_cli"` | Backend type. Also accepts any named instrument. |
| `skip_permissions` | bool | `true` | Skip permission prompts for unattended execution. Maps to `--dangerously-skip-permissions`. |
| `disable_mcp` | bool | `true` | Disable MCP server loading for faster execution (~2x speedup). |
| `output_format` | `"json"` \| `"text"` \| `"stream-json"` | `"text"` | Claude CLI output format. |
| `cli_model` | str | `null` | Model override. Example: `"claude-sonnet-4-5-20250929"`. |
| `timeout_seconds` | float | `1800.0` | Maximum time per sheet execution (30 minutes default). |
| `timeout_overrides` | dict[int, float] | `{}` | Per-sheet timeout overrides. Example: `{7: 28800}` gives sheet 7 eight hours. |
| `allowed_tools` | list[str] | `null` | Restrict Claude to specific tools. Example: `[Read, Grep, Glob]`. |
| `system_prompt_file` | Path | `null` | Path to custom system prompt file. |
| `working_directory` | Path | `null` | Working directory for execution. Defaults to config file directory. |
| `cli_extra_args` | list[str] | `[]` | Escape hatch for CLI flags not yet exposed. Applied last. |
| `max_output_capture_bytes` | int | `51200` | Maximum stdout/stderr to capture per sheet (50KB default). |

**API-specific fields** (when `type: anthropic_api`):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | str | `"claude-sonnet-4-5-20250929"` | Anthropic API model ID. |
| `api_key_env` | str | `"ANTHROPIC_API_KEY"` | Environment variable for API key. |
| `max_tokens` | int | `16384` | Maximum tokens for API response. |
| `temperature` | float | `0.7` | Sampling temperature (0-1). |

### `sheet`

Defines how work is divided into sheets.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `size` | int | **(required)** | Items per sheet. Must be ≥1. |
| `total_items` | int | **(required)** | Total items to process. `total_sheets = ceil((total_items - start_item + 1) / size)`. |
| `start_item` | int | `1` | First item number (1-indexed). |
| `dependencies` | dict[int, list[int]] | `{}` | Sheet/stage dependency DAG. See [Fan-Out and Dependencies](#fan-out-and-dependencies). |
| `fan_out` | dict[int, int] | `{}` | Stage → instance count. See [Fan-Out and Dependencies](#fan-out-and-dependencies). |
| `skip_when` | dict[int, str] | `{}` | Conditional skip rules. Expression evaluated with access to `sheets` dict and `job` state. |
| `skip_when_command` | dict[int, SkipWhenCommand] | `{}` | Command-based conditional skip rules. Shell command exit 0 = skip, non-zero = run. See [Conditional Sheet Skipping](#conditional-sheet-skipping). |
| `prelude` | list[InjectionItem] | `[]` | Shared file injections for ALL sheets. See [Prelude and Cadenza](#prelude-and-cadenza-context-injection). |
| `cadenzas` | dict[int, list[InjectionItem]] | `{}` | Per-sheet file injections. See [Prelude and Cadenza](#prelude-and-cadenza-context-injection). |
| `per_sheet_instruments` | dict[int, str] | `{}` | Per-sheet instrument overrides. See [Multi-Instrument Scores](#multi-instrument-scores). |
| `per_sheet_instrument_config` | dict[int, dict] | `{}` | Per-sheet instrument config overrides. |
| `instrument_map` | dict[str, list[int]] | `{}` | Batch instrument assignment. See [Multi-Instrument Scores](#multi-instrument-scores). |
| `descriptions` | dict[int, str] | `{}` | Human-readable labels for sheets, displayed in `mzt status`. |
| `spec_tags` | dict[int, list[str]] | `{}` | Per-sheet spec corpus tag filters. Only matching fragments are injected. |
| `prompt_extensions` | dict[int, list[str]] | `{}` | Per-sheet additional prompt directives (inline text or file paths). |

### `prompt`

Controls prompt template rendering.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `template` | str | `null` | Inline Jinja2 template. Mutually exclusive with `template_file`. |
| `template_file` | Path | `null` | Path to external `.j2` template file. |
| `variables` | dict[str, Any] | `{}` | Static variables available in the template. |
| `stakes` | str | `null` | Motivational section appended to prompts. Available as `{{ stakes }}`. |
| `thinking_method` | str | `null` | Thinking methodology injected into prompts. Available as `{{ thinking_method }}`. |

### `parallel`

Enables concurrent sheet execution when the dependency DAG permits.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable parallel sheet execution. |
| `max_concurrent` | int | `3` | Maximum sheets to run concurrently (1-10). |
| `fail_fast` | bool | `true` | Stop starting new sheets when one fails. |
| `stagger_delay_ms` | int | `0` | Delay in milliseconds between launching parallel sheets (0-5000). Reduces rate limit surge when many sheets hit the same API simultaneously. |

### `retry`

Controls retry behavior and partial completion recovery.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_retries` | int | `3` | Maximum retry attempts per sheet. |
| `base_delay_seconds` | float | `10.0` | Initial delay between retries. |
| `max_delay_seconds` | float | `3600.0` | Maximum delay (1 hour). |
| `exponential_base` | float | `2.0` | Backoff multiplier. |
| `jitter` | bool | `true` | Add randomness to delays. |
| `max_completion_attempts` | int | `5` | Completion prompt attempts before full retry. |
| `completion_delay_seconds` | float | `5.0` | Delay between completion attempts. |
| `completion_threshold_percent` | float | `50.0` | Minimum pass % to trigger completion mode. |

### `rate_limit`

Rate limit detection and handling.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `detection_patterns` | list[str] | `["rate.?limit", "usage.?limit", "quota", "too many requests", "429", "capacity", "try again later"]` | Regex patterns to detect rate limiting in output. |
| `wait_minutes` | int | `60` | Minutes to wait when rate limited. |
| `max_waits` | int | `24` | Maximum wait cycles (24 hours at default). |
| `max_quota_waits` | int | `48` | Maximum quota exhaustion wait cycles. |

### `cross_sheet`

Enables passing outputs and files between sheets for multi-phase workflows.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_capture_stdout` | bool | `false` | Include previous sheets' stdout in context. Templates access `{{ previous_outputs[1] }}`. |
| `max_output_chars` | int | `2000` | Maximum characters per previous sheet output. |
| `lookback_sheets` | int | `3` | Number of previous sheets to include (0 = all). |
| `capture_files` | list[str] | `[]` | File path patterns to read between sheets. Supports Jinja2 templating. |

### `validations`

List of rules checked after each sheet execution. See [Validation Types](#validation-types).

### `notifications`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | `"desktop"` \| `"slack"` \| `"webhook"` \| `"email"` | **(required)** | Notification channel. |
| `on_events` | list[str] | `["job_complete", "job_failed"]` | Events: `job_start`, `sheet_start`, `sheet_complete`, `sheet_failed`, `job_complete`, `job_failed`, `job_paused`. |
| `config` | dict | `{}` | Channel-specific configuration. |

---

## Template Variables Reference

Marianne uses [Jinja2](https://jinja.palletsprojects.com/) for prompt templating.
Templates have access to both core variables (computed by Marianne) and
user-defined variables (from `prompt.variables`).

### Core Variables

These are always available in every template:

| Variable | Type | Description |
|----------|------|-------------|
| `sheet_num` | int | Current sheet number (1-indexed). |
| `total_sheets` | int | Total number of sheets (after fan-out expansion). |
| `start_item` | int | First item number for this sheet. |
| `end_item` | int | Last item number for this sheet. |
| `workspace` | str | Absolute path to the workspace directory. |
| `instrument_name` | str | Name of the instrument executing this sheet (e.g., `claude-code`). |

### Fan-Out Variables

Available when `fan_out` is configured. When no fan-out is used, these default
to identity values (`stage` = `sheet_num`, `instance` = 1, `fan_count` = 1).

| Variable | Alias | Type | Description |
|----------|-------|------|-------------|
| `stage` | `movement` | int | Logical stage number (1-indexed). Multiple sheets can share the same stage. |
| `instance` | `voice` | int | Instance within the fan-out group (1-indexed). |
| `fan_count` | `voice_count` | int | Total instances in this stage's fan-out group. |
| `total_stages` | `total_movements` | int | Original stage count before fan-out expansion. |

The aliases `movement`, `voice`, `voice_count`, and `total_movements` are
equivalent to their originals — use whichever reads better in your score.
Marianne's mental model draws from orchestral music: a score has movements
(sequential phases) and voices (parallel instances within a movement).

**Example usage in templates:**

```yaml
prompt:
  template: |
    {% if stage == 1 %}
    ## Setup Stage
    {% elif stage == 2 %}
    ## Search Instance {{ instance }} of {{ fan_count }}
    Domain: {{ search_domains[instance] }}
    {% elif stage == 3 %}
    ## Synthesis (reading all {{ fan_count }} search outputs)
    {% endif %}
```

### Cross-Sheet Variables

Available when `cross_sheet` is configured:

| Variable | Type | Description |
|----------|------|-------------|
| `previous_outputs` | dict[int, str] | Stdout from previous sheets. Keys are sheet numbers. Skipped upstream sheets appear as `[SKIPPED]` instead of being silently omitted. |
| `previous_files` | dict[str, str] | File contents captured between sheets. Keys are file paths. |
| `skipped_upstream` | list[int] | Sheet numbers of upstream sheets that were skipped (via `skip_when` or `skip_when_command`). Use this to handle incomplete fan-in data explicitly in your template. |

**Example usage:**

```yaml
cross_sheet:
  auto_capture_stdout: true
  max_output_chars: 3000
  lookback_sheets: 5
  capture_files:
    - "{{ workspace }}/*.md"

prompt:
  template: |
    {% if previous_outputs %}
    ## Context from Previous Sheets
    {% for sheet_key, output in previous_outputs.items() %}
    ### Sheet {{ sheet_key }}
    {{ output[:600] }}
    {% endfor %}
    {% endif %}
```

### User-Defined Variables

Defined in `prompt.variables` and available in templates by name.

> **Warning:** User variables are merged directly into the template context and can
> silently shadow core variables like `sheet_num`, `workspace`, or `stage`. Avoid
> naming your variables with the same names as core variables listed above.

```yaml
prompt:
  variables:
    project_name: "Marianne AI Compose"
    review_types:
      1: "Architecture"
      2: "Test Coverage"
      3: "Code Debt"
    skill_files:
      - /path/to/skill1.md
      - /path/to/skill2.md
  template: |
    Working on {{ project_name }}.
    Review type: {{ review_types[instance] }}
    {% for skill in skill_files %}
    - {{ skill }}
    {% endfor %}
```

### Special Prompt Fields

The `stakes` and `thinking_method` fields are available as template variables
and are also automatically appended when no template is provided:

```yaml
prompt:
  template: |
    Do the work.
    {{ stakes }}
    {{ thinking_method }}
  stakes: |
    STAKES: Excellent work = $1T tip. Incomplete work = devoured by wolves.
  thinking_method: |
    Think step by step. Consider multiple approaches before committing.
```

---

## Expressive Templates

The [Template Variables Reference](#template-variables-reference) above catalogs what's
available. This section teaches you how to compose with it — how to turn flat YAML
into multi-stage, data-driven programs that generate precise instructions for minds.

Each subsection builds on the last. By the end, your templates will look less like
config and more like compositions.

### Arithmetic and Inline Expressions

Jinja2 evaluates expressions inside `{{ }}`. This is more useful than it sounds —
computed ranges, percentages, and ternary decisions all work inline.

```yaml
prompt:
  variables:
    batch_size: 10
  template: |
    Process batch {{ instance }} of {{ fan_count }}.
    Items {{ (instance - 1) * batch_size + 1 }} to {{ instance * batch_size }}.

    You are {{ ((instance / fan_count) * 100) | round }}% through the total workload.
```

**Ternary expressions** for inline decisions (no `{% if %}` blocks needed):

```yaml
prompt:
  template: |
    {{ "FINAL STAGE — be thorough and complete." if stage == total_stages else "Intermediate stage — focus on your specific task." }}

    Priority: {{ "HIGH" if instance == 1 else "NORMAL" }}
```

One line per decision. Use this when the conditional is small enough to stay readable
inline. Reach for full `{% if %}` blocks when it isn't.

### Conditionals (The Multi-Stage Backbone)

The `{% if stage == N %}` pattern is how a single template becomes a multi-stage
composition. Each stage gets its own instructions, but they share the same
variable context and macro definitions:

```yaml
prompt:
  template: |
    {% if stage == 1 %}
    RESEARCH: Find all relevant sources on {{ topic }}.
    Write findings to {{ workspace }}/01-research.md
    {% elif stage == 2 %}
    ANALYZE: Read the research from stage 1 and identify patterns.
    Write analysis to {{ workspace }}/02-analysis.md
    {% elif stage == 3 %}
    SYNTHESIZE: Combine analysis into a coherent narrative.
    Write final report to {{ workspace }}/03-synthesis.md
    {% endif %}
```

**Nested conditionals** for fan-out specialization — when each parallel instance
needs distinct instructions:

```yaml
prompt:
  variables:
    perspectives:
      1: "economic"
      2: "environmental"
      3: "social"
  template: |
    {% if stage == 2 %}
    Analyze from the {{ perspectives[instance] }} perspective.

    {% if instance == 1 %}
    Focus on costs, ROI, market dynamics.
    {% elif instance == 2 %}
    Focus on ecological impact, sustainability, externalities.
    {% elif instance == 3 %}
    Focus on equity, access, community effects.
    {% endif %}
    {% endif %}
```

This is where scores start feeling like programs. Each fan-out instance gets tailored
instructions from the same template — the outer conditional selects the stage, the
inner conditional specializes each instance.

### Custom Variables as Data Structures

The `prompt.variables` dict is your data layer. It holds anything YAML can express —
lists, nested dicts, lookup tables — and templates become views into that data:

```yaml
prompt:
  variables:
    guests:
      - name: "Alice"
        dietary: "vegetarian"
        interests: ["jazz", "architecture"]
      - name: "Bob"
        dietary: "none"
        interests: ["hiking", "wine"]
      - name: "Carol"
        dietary: "gluten-free"
        interests: ["photography", "cooking"]

    courses:
      1: "appetizer"
      2: "main"
      3: "dessert"

    wine_pairings:
      appetizer: "Sauvignon Blanc or sparkling"
      main: "Pinot Noir or Syrah"
      dessert: "Late harvest Riesling or Port"

  template: |
    Plan the {{ courses[instance] }} course.

    Dietary requirements to accommodate:
    {% for guest in guests %}
    - {{ guest.name }}: {{ guest.dietary }}{% if guest.dietary == "none" %} (no restrictions){% endif %}
    {% endfor %}

    Wine suggestion for this course: {{ wine_pairings[courses[instance]] }}
```

Change the data, the prompts change. The logic stays the same. This separation of
concerns is what makes scores maintainable — when the guest list changes or you
add a fourth course, the template doesn't need to change.

### Loops

#### Iterating over lists

```yaml
prompt:
  variables:
    checkpoints:
      - "All functions have docstrings"
      - "No unused imports"
      - "Test coverage above 80%"
      - "No hardcoded secrets"
  template: |
    Review this code against the following checklist:

    {% for check in checkpoints %}
    {{ loop.index }}. {{ check }}{% if loop.last %} (MOST CRITICAL){% endif %}
    {% endfor %}
```

The `loop` variable provides `loop.index` (1-based), `loop.index0` (0-based),
`loop.first`, `loop.last`, and `loop.length`.

#### Iterating over dicts

This is how synthesis stages consume fan-out results — the `previous_outputs` dict
is keyed by sheet number:

```yaml
prompt:
  template: |
    {% if stage == 3 %}
    Synthesize findings from all previous stages:

    {% for sheet_key, output in previous_outputs.items() %}
    --- Stage {{ sheet_key }} output ---
    {{ output | truncate(1500) }}

    {% endfor %}
    {% endif %}
```

#### Range-based loops with concatenation

```yaml
prompt:
  template: |
    Generate {{ fan_count }} test scenarios:

    {% for i in range(1, fan_count + 1) %}
    Scenario {{ i }}: {{ "happy path" if i == 1 else "edge case " ~ (i - 1) }}
    {% endfor %}
```

The `~` operator concatenates strings. `range()` works like Python's. Together
they let you build dynamic numbered lists without hardcoding the count.

### Filters

Filters transform values inline with `|`. They are Jinja2's equivalent of Unix pipes.

**Useful filters:**

| Filter | What It Does | Example |
|--------|-------------|---------|
| `upper` / `lower` / `title` | Case conversion | `{{ name \| title }}` |
| `trim` | Strip whitespace | `{{ text \| trim }}` |
| `truncate(n)` | Limit length | `{{ long_text \| truncate(500) }}` |
| `default(val)` | Fallback if undefined/empty | `{{ x \| default("N/A") }}` |
| `replace(old, new)` | String substitution | `{{ s \| replace(" ", "_") }}` |
| `join(sep)` | Join a list | `{{ items \| join(", ") }}` |
| `length` | Count items | `{{ list \| length }}` |
| `round` | Round numbers | `{{ 3.7 \| round }}` |
| `int` / `float` | Type conversion | `{{ "42" \| int }}` |
| `first` / `last` | List endpoints | `{{ items \| first }}` |
| `sort` | Sort a list | `{{ names \| sort }}` |
| `unique` | Deduplicate | `{{ tags \| unique }}` |
| `reject` / `select` | Filter items | `{{ items \| reject("none") }}` |
| `map(attribute=x)` | Extract attribute | `{{ guests \| map(attribute="name") \| join(", ") }}` |
| `batch(n)` | Group into chunks | `{% for chunk in items \| batch(5) %}` |
| `wordcount` | Count words | `{{ text \| wordcount }}` |

**Chaining** is where filters shine — compose them left to right like a pipeline:

```yaml
prompt:
  template: |
    Guest list: {{ guests | map(attribute="name") | sort | join(", ") }}

    Dietary needs: {{ guests | map(attribute="dietary") | reject("equalto", "none") | unique | join(", ") }}

    Previous output (trimmed):
    {{ previous_outputs[1] | default("No previous output") | truncate(800) }}
```

### Macros (Reusable Prompt Blocks)

Macros are the most underused Jinja2 feature in scores — and arguably the most
powerful. They let you define reusable prompt fragments with consistent formatting:

```yaml
prompt:
  template: |
    {% macro output_spec(filename, format) %}
    ## Output Specification
    - **File**: {{ workspace }}/{{ filename }}
    - **Format**: {{ format }}
    - **Encoding**: UTF-8
    - If the parent directory doesn't exist, create it.
    {% endmacro %}

    {% macro quality_bar(level) %}
    ## Quality Standard
    {% if level == "high" %}
    This is a high-stakes deliverable. Triple-check accuracy. Cite sources.
    No hedging language. Be definitive where evidence supports it.
    {% elif level == "draft" %}
    This is a working draft. Prioritize coverage over polish.
    Mark uncertainties with [?]. Flag areas needing human review with [REVIEW].
    {% endif %}
    {% endmacro %}

    {% if stage == 1 %}
    Research the topic thoroughly.
    {{ output_spec("01-research.md", "markdown with source citations") }}
    {{ quality_bar("draft") }}

    {% elif stage == 2 %}
    Write the final analysis.
    {{ output_spec("02-analysis.md", "structured markdown report") }}
    {{ quality_bar("high") }}
    {% endif %}
```

Define once, use everywhere. When you change your output spec format, you change
it in one place. When you add a new stage, you compose it from existing blocks.

**Parameterized macros with defaults** for maximum flexibility:

```yaml
prompt:
  template: |
    {% macro section(title, instructions, output_file, critical=false) %}
    # {{ title }}{{ " [CRITICAL]" if critical else "" }}

    {{ instructions }}

    Save your work to: {{ workspace }}/{{ output_file }}
    {% if critical %}

    WARNING: This section's output feeds directly into downstream stages.
    Errors here cascade. Be precise.
    {% endif %}
    {% endmacro %}

    {% if stage == 1 %}
    {{ section(
        "Data Collection",
        "Gather all primary sources. Verify each one.",
        "01-data.md"
    ) }}
    {% elif stage == 2 %}
    {{ section(
        "Analysis",
        "Identify the three strongest patterns in the data.",
        "02-analysis.md",
        critical=true
    ) }}
    {% endif %}
```

Macros are your house style encoded as code. New stages inherit your standards
automatically.

### Fan-Out + Jinja2

Fan-out gives you parallel execution. Jinja2 gives you per-instance specialization.
Together, they create parallel cognition — multiple independent minds, each with
a distinct voice, converging on one question:

```yaml
sheet:
  size: 1
  total_items: 3
  fan_out:
    2: 4    # Stage 2 runs 4 parallel instances
  dependencies:
    2: [1]
    3: [2]  # Fan-in: stage 3 waits for all 4

prompt:
  variables:
    lenses:
      1:
        name: "historian"
        voice: "You are a historian. Ground everything in precedent and trajectory."
        focus: "How did we get here? What patterns recur?"
      2:
        name: "engineer"
        voice: "You are a systems thinker. Focus on mechanisms and feedback loops."
        focus: "What are the moving parts? Where are the leverage points?"
      3:
        name: "poet"
        voice: "You are a poet. Attend to what's felt but unsaid."
        focus: "What's the emotional truth? What metaphor captures this?"
      4:
        name: "skeptic"
        voice: "You are a skeptic. Challenge every assumption, including your own."
        focus: "What are we wrong about? What evidence would change our mind?"

  template: |
    {% if stage == 1 %}
    Frame the question. What are we actually asking?
    Define scope, assumptions, and what a good answer looks like.

    Save to {{ workspace }}/00-framing.md

    {% elif stage == 2 %}
    {{ lenses[instance].voice }}

    Read the framing: {{ workspace }}/00-framing.md

    Your focus: {{ lenses[instance].focus }}

    Write your perspective. Be authentic to your role. Don't try to be
    balanced — that's the synthesis stage's job. Lean into your lens.

    Save to {{ workspace }}/02-{{ lenses[instance].name }}.md

    {% elif stage == 3 %}
    You have {{ fan_count }} perspectives to synthesize:

    {% for i in range(1, fan_count + 1) %}
    - **{{ lenses[i].name | title }}**: {{ lenses[i].focus }}
    {% endfor %}

    {% if previous_outputs %}
    {% for key, output in previous_outputs.items() %}
    --- {{ lenses[loop.index].name | title if loop.index <= fan_count else "Unknown" }} ---
    {{ output | truncate(2000) }}

    {% endfor %}
    {% endif %}

    Don't average the perspectives. Find the tensions between them.
    The interesting insight is usually where two lenses disagree.

    Save to {{ workspace }}/03-synthesis.md
    {% endif %}
```

Four parallel minds, each with a distinct voice, all examining the same question.
The synthesis stage doesn't summarize — it's told to find the *tensions*. That's
where the interesting thinking happens.

### Advanced Patterns

#### Progressive Difficulty

Use stage-indexed data structures to scale complexity across the pipeline:

```yaml
prompt:
  variables:
    difficulty:
      1: { depth: "surface", time: "5 minutes", standard: "draft" }
      2: { depth: "moderate", time: "15 minutes", standard: "review-ready" }
      3: { depth: "thorough", time: "30 minutes", standard: "publication" }
  template: |
    {% set diff = difficulty[stage] | default(difficulty[3]) %}

    Analyze at {{ diff.depth }} depth.
    Target effort: {{ diff.time }}.
    Quality standard: {{ diff.standard }}.
```

#### Conditional Validation Hints

Tell the agent what format validations expect — then your `content_contains`
and `content_regex` rules will find what they're looking for:

```yaml
prompt:
  template: |
    {% if stage <= 3 %}
    Save your output as markdown to {{ workspace }}/{{ "%02d" | format(stage) }}-output.md
    {% else %}
    Save your output as JSON to {{ workspace }}/{{ "%02d" | format(stage) }}-output.json

    The JSON must validate against this schema:
    ```json
    {"type": "object", "required": ["findings", "confidence", "sources"]}
    ```
    {% endif %}
```

#### Cross-Sheet Selective Recall

Only include substantial previous outputs. Skip empty or trivial ones to
save context window:

```yaml
prompt:
  template: |
    {% if previous_outputs %}
    ## Context from Previous Stages
    {% for key, output in previous_outputs.items() %}
    {% if output | length > 100 %}

    ### Stage {{ key }} ({{ output | wordcount }} words)
    {{ output | truncate(1000) }}
    {% else %}
    *Stage {{ key }}: minimal output, skipping.*
    {% endif %}
    {% endfor %}
    {% endif %}
```

#### Self-Documenting Stages

Encode stage metadata in variables so each prompt explains its own place
in the pipeline to the agent as it runs:

```yaml
prompt:
  variables:
    stages:
      1: { name: "Research", verb: "researching" }
      2: { name: "Draft", verb: "drafting" }
      3: { name: "Review", verb: "reviewing" }
      4: { name: "Publish", verb: "publishing" }
  template: |
    {% set current = stages[stage] %}
    {% set progress = ((stage / total_stages) * 100) | round %}

    # {{ current.name }} (Stage {{ stage }}/{{ total_stages }}, {{ progress }}% complete)

    You are {{ current.verb }} as part of a {{ total_stages }}-stage pipeline.

    {% if stage > 1 %}
    Previous stage ({{ stages[stage - 1].name }}) output:
    {{ previous_outputs[stage - 1] | default("Not available") | truncate(1500) }}
    {% endif %}

    Save to {{ workspace }}/{{ "%02d" | format(stage) }}-{{ current.name | lower }}.md
```

### Template Limitations

A few things that will not work:

1. **No `{% include %}` or `{% extends %}`** — Templates are loaded via
   `from_string()`, not from a filesystem loader. No file inclusion or
   template inheritance.

2. **No side effects** — Jinja2 is a rendering engine, not a programming
   language. You cannot make HTTP calls, read files, or execute commands
   from inside a template. That's what the agent does.

3. **No dynamic fan-out** — You cannot compute fan-out count from inside a
   template. `fan_out:` is YAML config, evaluated before templates render.
   The structure is fixed; only the content is dynamic.

4. **Validation paths use different syntax** — Validation `path` fields use
   `{single_brace}` Python format strings (`{workspace}`, `{sheet_num}`),
   not Jinja2 `{{ double_brace }}` syntax. Don't mix them.

---

## Fan-Out Patterns

Fan-out is not just parallelism — it's structured pluralism. The pattern you
choose shapes what kind of thinking the fan-out produces. Six patterns have
emerged from real scores:

| Pattern | What It Does | Example Scores |
|---------|-------------|----------------|
| **Adversarial** | Independent critiques of the same position | [dialectic.yaml](https://github.com/Mzzkc/marianne-score-playspace/blob/main/scores/dialectic.yaml), [parallel-research.yaml](examples/parallel-research-fanout.yaml) |
| **Perspectival** | Same question, different analytical frameworks | [thinking-lab.yaml](https://github.com/Mzzkc/marianne-score-playspace/blob/main/scores/thinking-lab.yaml) |
| **Functional** | Same goal, different planning domains | [dinner-party.yaml](https://github.com/Mzzkc/marianne-score-playspace/blob/main/scores/dinner-party.yaml) |
| **Graduated** | Same content, different difficulty levels | [skill-builder.yaml](https://github.com/Mzzkc/marianne-score-playspace/blob/main/scores/skill-builder.yaml) |
| **Generative** | Same seed, different creative lenses | [worldbuilder.yaml](https://github.com/Mzzkc/marianne-score-playspace/blob/main/scores/worldbuilder.yaml) |
| **Expert** | Same codebase, different review specializations | [quality-continuous.yaml](examples/quality-continuous.yaml) |

The synthesis stage that follows fan-out is where emergence happens. Independent
outputs produce tensions, convergences, and combinations that no single perspective
would generate alone. The pattern you choose determines the *kind* of emergence:
adversarial finds hidden agreements, perspectival finds blind spots, generative
finds unexpected coherence.

> For creative examples with real output, see the
> [Marianne Score Playspace](https://github.com/Mzzkc/marianne-score-playspace).

---

## Movements and Multi-Instrument Scores

Marianne scores can use multiple instruments in a single score. Different movements or
individual sheets can each use the instrument best suited to their task — a planning
phase on a deep-reasoning model, parallel implementation on a fast code model, review
on a different provider entirely.

### Declaring Movements

The `movements:` key lets you name and configure each sequential phase of your score.
Movement numbers correspond to stage numbers (the logical phases before fan-out
expansion).

```yaml
name: multi-instrument-pipeline
workspace: ../workspaces/multi-instrument

instrument: claude-code    # default instrument

movements:
  1:
    name: Architecture
    instrument: claude-code
    instrument_config:
      timeout_seconds: 600
  2:
    name: Implementation
    voices: 3                          # equivalent to fan_out: {2: 3}
    instrument: gemini-cli
    instrument_config:
      model: gemini-2.5-flash
  3:
    name: Review

sheet:
  size: 1
  total_items: 3
  dependencies:
    2: [1]
    3: [2]
```

Movement names appear in `mzt status` output, making large scores readable:

```
multi-instrument-pipeline: RUNNING (2/3 movements)

  ✓ Movement 1: Architecture        [completed, 2m 10s]   claude-code
  ► Movement 2: Implementation      [1/3 complete]         gemini-cli
      ✓ Voice 1                     [completed, 4m 22s]
      ► Voice 2                     [running, 3m 15s]
      · Voice 3                     [waiting]
  · Movement 3: Review              [waiting]              claude-code
```

The `voices:` field is shorthand for `fan_out: {N: voices}` — they produce the same
result.

### Named Instrument Definitions

For scores that reference the same instrument configuration in multiple places, declare
reusable aliases with `instruments:`:

```yaml
instruments:
  fast-writer:
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
    name: Planning
    instrument: deep-thinker       # references the alias above
  2:
    name: Drafting
    voices: 4
    instrument: fast-writer        # references the alias above
  3:
    name: Synthesis
    instrument: deep-thinker
```

Each alias has a `profile:` (the registered instrument name from `mzt instruments list`)
and an optional `config:` (overrides merged with the profile's defaults).

### Per-Sheet Instrument Assignment

For fine-grained control, assign instruments to individual sheets:

```yaml
sheet:
  size: 1
  total_items: 6
  # Batch assignment: multiple sheets to one instrument
  instrument_map:
    gemini-cli: [1, 2, 3]
    claude-code: [4, 5, 6]
  # Per-sheet override (highest precedence)
  per_sheet_instruments:
    5: codex-cli
  per_sheet_instrument_config:
    5:
      timeout_seconds: 1800
```

**Resolution precedence** (highest wins):
1. `per_sheet_instruments` — explicit per-sheet override
2. `instrument_map` — batch assignment
3. `movements.N.instrument` — per-movement default
4. Top-level `instrument:` — score default
5. `backend.type` — legacy syntax
6. `claude_cli` — built-in default

### When to Use Multi-Instrument

Use different instruments when the task demands different capabilities:

- **Planning + coding**: Deep reasoning (Opus/Pro) for architecture, fast coding (Sonnet/Flash) for implementation
- **Cross-provider verification**: Write code with one provider, review with another for independent perspective
- **Cost optimization**: Expensive models for critical sheets, cheaper models for routine work
- **Capability matching**: Tools that support MCP for integration sheets, simple models for text generation

A single-instrument score is always simpler. Add instruments when the quality or cost
difference justifies the complexity.

### Instrument Fallbacks

When an instrument hits rate limits or becomes unavailable, Marianne can automatically
try fallback instruments in order. Specify fallback chains at the score, movement,
or per-sheet level:

```yaml
instrument: claude-code
instrument_fallbacks: [gemini-cli, codex-cli]

movements:
  2:
    name: Implementation
    voices: 3
    instrument: gemini-cli
    # Movement-level fallbacks override score-level
    instrument_fallbacks: [claude-code]

sheet:
  size: 1
  total_items: 5
  # Per-sheet fallback override (replaces, does not merge)
  per_sheet_fallbacks:
    4: [aider]
```

Fallback chains resolve from most specific to least specific:
1. `per_sheet_fallbacks[N]` — per-sheet override
2. `movements.N.instrument_fallbacks` — per-movement
3. `instrument_fallbacks` — score-level default

Per-sheet fallbacks **replace** inherited chains rather than merging with them.
If sheet 4 specifies `[aider]`, it will only fall back to aider — not to the
movement-level or score-level chain.

`mzt validate` warns (V211) when a fallback name doesn't match a known instrument
profile or score alias.

---

## Philosophy of Score Design

Five principles for score authors.

### 1. Scores Are Programs for Minds, Not Machines

A shell script tells bash exactly what to do. A score tells a mind what to
*accomplish*. The template is the specification; the agent is the implementation.
Design accordingly — be clear about outcomes, flexible about methods.

### 2. Fan-Out Is Parallel Cognition

When you fan out a stage, you're not running the same thing faster. You're creating
multiple independent perspectives. The synthesis stage is where the magic happens —
where those perspectives collide, contradict, and combine into something none of
them could reach alone.

### 3. Macros Are Your House Style

Every team has implicit standards — how to format output, what quality level to
expect, how to cite sources. Encode these as macros. New scores inherit your
standards automatically. Update them in one place.

### 4. Data in Variables, Logic in Templates

Keep your `prompt.variables` as the source of truth for domain-specific data
(guest lists, review criteria, stage definitions). Keep your template as the logic
that processes that data. When the data changes, the template doesn't need to.

### 5. The Workspace Is Shared Memory

Files in `{{ workspace }}` are how stages communicate beyond `previous_outputs`.
Write structured output — JSON, markdown with consistent headers — so downstream
stages can parse it reliably. The workspace is the score's memory; treat it with
the same care you'd give a database schema.

---

## Validation Types

Validations run after each sheet execution. If any validation fails, the sheet
is retried (up to `retry.max_retries`). When more than
`completion_threshold_percent` of validations pass, Marianne enters **completion
mode** — sending a focused prompt that tells Claude what passed and what
still needs to be done.

All validation types share these common fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `description` | str | `null` | Human-readable description (shown in completion prompts). |
| `stage` | int | `1` | Validation stage (1-10). Lower stages run first. If a stage fails, higher stages are skipped. |
| `condition` | str | `null` | When this validation applies. Supports: `"sheet_num >= N"`, `"sheet_num == N"`, `"stage == N"`, `"stage == N and instance == M"`. If `null`, always applies. |
| `retry_count` | int | `3` | Retry attempts for file-based validations (handles filesystem race conditions). |
| `retry_delay_ms` | int | `200` | Delay between validation retries in milliseconds. |

### `file_exists`

Checks that a file exists at the specified path.

```yaml
validations:
  - type: file_exists
    path: "{workspace}/sheet{sheet_num}.md"
    description: "Sheet output file must exist"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | File path. Supports `{workspace}`, `{sheet_num}`, `{instance}` placeholders. |

### `file_modified`

Checks that a file was modified during sheet execution (mtime comparison).

```yaml
validations:
  - type: file_modified
    path: "{workspace}/TRACKING.md"
    description: "Tracking document must be updated"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | File path to check for modification. |

### `content_contains`

Checks that a file contains a specific string or pattern.

```yaml
validations:
  - type: content_contains
    path: "{workspace}/01-setup.md"
    pattern: "SETUP_COMPLETE"
    description: "Setup must be marked complete"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | File path to search. |
| `pattern` | str | yes | Text that must appear in the file. |

### `content_regex`

Checks that a file contains content matching a regular expression.

```yaml
validations:
  - type: content_regex
    path: "{workspace}/02-search-{instance}.md"
    pattern: "SEARCH_\\d+_COMPLETE"
    description: "Search marked complete"
    condition: "stage == 2"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | File path to search. |
| `pattern` | str | yes | Regex pattern that must match. |

### `command_succeeds`

Runs a shell command and checks that it exits with code 0.

```yaml
validations:
  - type: command_succeeds
    command: "pytest -x -q --tb=no 2>&1 | tail -1 | grep -E 'passed'"
    description: "Tests must pass"
    condition: "sheet_num >= 11"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `command` | str | yes | Shell command to execute. |
| `working_directory` | str | no | Working directory for the command (defaults to workspace). |

**Advanced example** — checking completion percentage from a file:

```yaml
validations:
  - type: command_succeeds
    command: |
      FILE="{workspace}/06-batch1-fixes.md"
      if [ ! -f "$FILE" ]; then echo "file missing"; exit 1; fi
      COMPLETION=$(grep -oE 'Completion.*[0-9]+%' "$FILE" | grep -oE '[0-9]+' | head -1)
      if [ -n "$COMPLETION" ] && [ "$COMPLETION" -ge 70 ]; then
        echo "Batch 1 completion: ${COMPLETION}% - PASSED"
      else
        echo "Batch 1 completion: ${COMPLETION:-unknown}% - FAILED"
        exit 1
      fi
    description: "Batch 1 must have >=70% completion rate"
    condition: "stage >= 5"
```

### Staged Validations

Use the `stage` field to run validations in order. If any validation in
stage 1 fails, stage 2+ validations are skipped (fail-fast):

```yaml
validations:
  # Stage 1: Syntax checks (run first)
  - type: command_succeeds
    command: "ruff check src/"
    description: "Lint must pass"
    stage: 1

  # Stage 2: Tests (run only if lint passes)
  - type: command_succeeds
    command: "pytest -x -q --tb=no"
    description: "Tests must pass"
    stage: 2

  # Stage 3: Security (run only if tests pass)
  - type: command_succeeds
    command: "pip-audit"
    description: "No known vulnerabilities"
    stage: 3
```

---

## Fan-Out and Dependencies

Fan-out lets a single logical stage expand into multiple parallel instances.
Combined with the dependency DAG and parallel execution, this enables
complex workflows like parallel expert reviews with synthesis.

### How Fan-Out Works

Fan-out is a **compile-time expansion** — stages expand to concrete sheets
when the YAML is parsed, not at runtime. After expansion, the `fan_out` field
is cleared to prevent re-expansion on resume.

**Constraints:**
- `sheet.size` must be `1` (each stage maps to one logical sheet)
- `sheet.start_item` must be `1`
- `sheet.total_items` equals the number of logical stages

**Example:** 3 stages, stage 2 fans out to 3 instances:

```yaml
sheet:
  size: 1
  total_items: 3        # 3 logical stages
  fan_out:
    2: 3                # Stage 2 → 3 parallel instances
  dependencies:
    2: [1]              # Stage 2 depends on stage 1
    3: [2]              # Stage 3 depends on stage 2
```

**Expansion result** (5 concrete sheets):

| Sheet | Stage | Instance | Fan Count |
|-------|-------|----------|-----------|
| 1 | 1 | 1 | 1 |
| 2 | 2 | 1 | 3 |
| 3 | 2 | 2 | 3 |
| 4 | 2 | 3 | 3 |
| 5 | 3 | 1 | 1 |

### Dependency Expansion Patterns

Dependencies declared at the stage level are automatically expanded to
sheet-level dependencies. The expansion follows these patterns:

| Pattern | Source → Target | Behavior |
|---------|-----------------|----------|
| **1→N (fan-out)** | 1 sheet → N sheets | Each target instance depends on the single source |
| **N→1 (fan-in)** | N sheets → 1 sheet | Single target depends on ALL source instances |
| **N→N (instance-match)** | N sheets → N sheets | Target[i] depends on source[i] |
| **N→M (cross-fan)** | N sheets → M sheets (N≠M) | All-to-all (conservative) |

**Expanded dependencies for the example above:**

```
Sheet 2 depends on [1]    # fan-out: each instance depends on single source
Sheet 3 depends on [1]
Sheet 4 depends on [1]
Sheet 5 depends on [2, 3, 4]  # fan-in: synthesis depends on ALL instances
```

### Dependency Syntax

Dependencies are declared as `{sheet_or_stage: [prerequisite_list]}`:

```yaml
sheet:
  dependencies:
    2: [1]              # Sheet/stage 2 requires sheet/stage 1
    3: [1]              # Sheet/stage 3 also requires 1
    4: [2, 3]           # Sheet/stage 4 requires both 2 and 3
```

Sheets without dependency entries are independent and can run immediately
(or after the default sequential order if parallel execution is disabled).

### Parallel Execution

To actually run independent sheets concurrently, enable parallel execution:

```yaml
parallel:
  enabled: true
  max_concurrent: 3     # Up to 3 sheets at once
  fail_fast: true       # Stop on first failure
  stagger_delay_ms: 150 # 150ms between launches to reduce rate limit surge
```

Without `parallel.enabled: true`, sheets run sequentially even if the
dependency DAG would allow parallelism.

The `stagger_delay_ms` option adds a small delay between launching parallel sheets. This prevents all sheets from hitting the same API simultaneously, which can trigger rate limits on providers with per-minute quotas. Values between 100-500ms are typical.

### Conditional Sheet Skipping

**Expression-based (`skip_when`):** Skip sheets based on runtime state
using Python expressions with access to `sheets` dict and `job` state:

```yaml
sheet:
  skip_when:
    5: "sheets.get(3) and sheets[3].validation_passed"
```

This skips sheet 5 when sheet 3's validations passed — useful for
conditional error-handling stages that only run on failure.
If the expression raises an exception, the sheet runs (fail-open). The
error is logged at ERROR level.

**Command-based (`skip_when_command`):** Skip sheets based on shell
command exit codes. Exit 0 = skip the sheet, non-zero = run the sheet.
Supports `{workspace}` template expansion and configurable timeout.
On timeout or error, the sheet runs (fail-open for safety).

```yaml
sheet:
  skip_when_command:
    6:
      command: 'grep -q "TOTAL_PHASES: 1$" "{workspace}/03-plan.md"'
      description: "Skip phase 2 — plan has only 1 phase"
      timeout_seconds: 10  # default, max 60
    8:
      command: 'grep -q "TOTAL_PHASES: [12]$" "{workspace}/03-plan.md"'
      description: "Skip phase 3 — plan has fewer than 3 phases"
```

This is useful when earlier stages write workspace files that determine
whether later stages should run — for example, a planning stage that
decides how many implementation phases are needed.

`SkipWhenCommand` fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `command` | str | **(required)** | Shell command. `{workspace}` is expanded. Exit 0 = skip. |
| `description` | str | `null` | Human-readable skip reason (shown in logs). |
| `timeout_seconds` | float | `10.0` | Max seconds to wait (0-60). Fail-open on timeout. |

**When to use which:**
- `skip_when` — conditions based on previous sheet results (validation
  pass/fail, sheet status) available in the checkpoint state
- `skip_when_command` — conditions based on workspace file contents or
  external state that requires I/O to check

---

## Cross-Sheet Context

Cross-sheet context allows later sheets to access outputs from earlier sheets
without manually reading files. This is essential for multi-phase workflows.

### Configuration

```yaml
cross_sheet:
  auto_capture_stdout: true     # Capture stdout from previous sheets
  max_output_chars: 3000        # Truncate per sheet (prevents prompt bloat)
  lookback_sheets: 5            # Include last 5 sheets (0 = all)
  capture_files:                # Also read file contents between sheets
    - "{{ workspace }}/*.md"
    - "{{ workspace }}/*.yaml"
```

### Accessing Context in Templates

**Previous stdout:**

```jinja2
{% if previous_outputs %}
## Expert Reviews Summary
{% for sheet_key, output in previous_outputs.items() %}
### Sheet {{ sheet_key }}
{{ output[:600] }}
{% endfor %}
{% endif %}
```

**Captured files:**

```jinja2
{% if previous_files %}
{% for path, content in previous_files.items() %}
## {{ path }}
{{ content }}
{% endfor %}
{% endif %}
```

**Handling skipped upstream sheets:**

When upstream sheets are skipped (via `skip_when` or `skip_when_command`),
their entry in `previous_outputs` contains `[SKIPPED]` instead of being
silently omitted. The `skipped_upstream` variable lists which sheet numbers
were skipped, so your template can handle incomplete fan-in data:

```jinja2
{% if skipped_upstream %}
Note: Sheets {{ skipped_upstream | join(', ') }} were skipped.
Synthesize from the {{ previous_outputs | length - skipped_upstream | length }} available outputs.
{% endif %}
```

### Design Considerations

- Set `lookback_sheets` appropriately — for a 14-stage score with fan-out,
  the synthesis stage may need to look back 5+ sheets to see all expert
  review outputs.
- `max_output_chars` prevents prompt bloat. Claude has context limits;
  2000-3000 chars per previous sheet is usually sufficient.
- `capture_files` supports Jinja2 patterns. Use `{{ workspace }}/*.md`
  to capture all markdown files from the workspace.

---

## Prelude and Cadenza (Context Injection)

The prelude/cadenza system provides first-class file injection into sheet
prompts. Instead of manually reading files in your template or relying on
`cross_sheet`, you declare what files to inject and Marianne handles the rest —
reading files at execution time and placing content at the right position
in the prompt.

- **Prelude** — shared context/skills/tools injected into ALL sheets
  (like a musical prelude that sets the tone)
- **Cadenza** — per-sheet specific injections
  (like a soloist's moment in the composition)

### Injection Categories

Each injected file is tagged with a category that controls WHERE it appears
in the final prompt:

| Category | Prompt Position | Use For |
|----------|----------------|---------|
| `context` | After template body | Background knowledge, reference docs, previous outputs |
| `skill` | Before template body (after preamble) | Methodologies, instructions, coding standards |
| `tool` | Before template body (after preamble) | Available actions, tool descriptions |

### Configuration

```yaml
sheet:
  size: 1
  total_items: 5

  # Prelude: injected into every sheet
  prelude:
    - file: docs/architecture.md
      as: context
    - file: .claude/skills/debugging.md
      as: skill
    - file: tools/lint.sh
      as: tool

  # Cadenzas: injected into specific sheets only
  cadenzas:
    3:
      - file: "{{ workspace }}/02-output.md"
        as: context
    5:
      - file: tests/results.json
        as: context
```

### Dynamic Paths with Jinja

File paths support Jinja2 templating, so earlier sheets' outputs can be
injected into later sheets:

```yaml
sheet:
  cadenzas:
    3:
      - file: "{{ workspace }}/phase1-results.md"
        as: context    # Sheet 3 gets phase 1's output
```

Files are read at **sheet execution time**, not when the YAML is parsed.
This means dynamic outputs from earlier sheets are available.

### Prelude vs. cross_sheet vs. prompt_extensions

These three features serve different purposes:

| Feature | Scope | Content Source | Prompt Position |
|---------|-------|---------------|-----------------|
| `prelude` / `cadenzas` | All sheets / per-sheet | File contents (read at execution time) | Category-dependent (context/skill/tool) |
| `cross_sheet` | Automatic from previous sheets | stdout + captured files | Template variables (`previous_outputs`, `previous_files`) |
| `prompt_extensions` | Score-level or per-sheet | Inline text or file paths | Backend-level injection (via `set_prompt_extensions()`) |

Use **prelude/cadenzas** when you have specific files to inject with
category-aware placement. Use **cross_sheet** when you want automatic
capture of previous sheet outputs. Use **prompt_extensions** for inline
directives that apply across the score.

### Validation

`mzt validate` checks static prelude/cadenza file paths (V108 warning)
but skips Jinja-templated paths that can't be resolved before execution.

---

## Specification Corpus

The specification corpus lets you inject project-level context — goals, conventions,
constraints, quality standards — into every agent prompt automatically. Instead of
copying the same context into every score's prelude, you maintain it once in a
directory and Marianne injects relevant fragments per sheet.

### Setting Up a Spec Corpus

Create a directory with YAML or Markdown spec files:

```
.marianne/spec/
├── intent.yaml        # Goals, trade-offs, decision authority
├── conventions.yaml   # Code patterns, naming, testing rules
├── constraints.yaml   # Must-do and must-not rules
├── quality.yaml       # Test requirements, review checklists
└── architecture.yaml  # System layers, invariants
```

Each YAML spec file follows this structure:

```yaml
name: conventions
tags: [code, style, patterns]
kind: structured
content: |
  ## Code Patterns
  - Async throughout. All I/O uses asyncio.
  - Pydantic v2 for all config models.
  - Every field has Field(description=...).
data:
  language: python
  test_framework: pytest
```

Markdown files are loaded as text fragments with tags derived from their filename.

### Enabling Spec Injection

Add the `spec:` section to your score:

```yaml
spec:
  spec_dir: ".marianne/spec"        # Path to spec directory (relative to project root)
  include_claude_md: false        # Also inject CLAUDE.md as a fragment
```

When `spec_dir` is set, Marianne loads all YAML and Markdown files from that
directory at score start and injects their content into agent prompts as an
"Injected Context" section.

### Per-Sheet Tag Filtering

Not every sheet needs every spec fragment. Use `spec_tags` on `sheet:` to
filter which fragments each sheet receives:

```yaml
sheet:
  size: 1
  total_items: 4
  spec_tags:
    1: [goals, architecture]     # Planning sheet gets goals + architecture
    2: [code, style, patterns]   # Coding sheet gets conventions
    3: [testing, quality]        # Testing sheet gets quality standards
    4: [code, testing]           # Review sheet gets both

spec:
  spec_dir: ".marianne/spec"
```

Fragments match if they have **at least one tag** in common with the filter
list. Sheets without a `spec_tags` entry receive **all** fragments. An empty
tag list `[]` also returns all fragments.

### When to Use Spec Corpus vs. Prelude

| Use case | Mechanism |
|----------|-----------|
| Project-wide conventions all agents should follow | Spec corpus |
| Task-specific context for one score | Prelude |
| Per-sheet focused context | Spec corpus with `spec_tags` |
| Files that change between runs | Prelude with Jinja paths |

The spec corpus is for **stable project knowledge** that applies across many
scores. Preludes are for **score-specific context** that varies per score.

---

## Grounding Hooks

Grounding hooks validate sheet outputs against external sources — APIs,
databases, file checksums — to prevent model drift and ensure output quality.
They run **after** standard validations pass, as an additional quality gate.

### Enabling Grounding

```yaml
grounding:
  enabled: true
  fail_on_grounding_failure: true    # Fail the sheet if grounding fails
  escalate_on_failure: true          # Escalate to composer on failure
  timeout_seconds: 30                # Max wait per hook
  hooks:
    - type: file_checksum
      expected_checksums:
        "critical_file.py": "sha256:abc123..."
```

### How Grounding Works

1. A sheet executes and standard validations pass.
2. Marianne runs each grounding hook against the sheet's output.
3. If a hook fails:
   - `fail_on_grounding_failure: true` → sheet fails (retries apply).
   - `escalate_on_failure: true` → escalates to composer (fermata).
4. If all hooks pass → sheet is marked complete.

### When to Use Grounding

- **Deterministic outputs**: Verify specific files weren't corrupted.
- **Schema compliance**: Check that generated config matches a schema.
- **External validation**: Call an API to verify generated content.
- **Regression prevention**: Ensure key files maintain expected checksums.

Grounding hooks are complementary to validations. Validations check "did the
agent produce output?" Grounding checks "is the output trustworthy?"

See the [Configuration Reference](configuration-reference.md#grounding) for
all grounding hook types and options.

---

## Concert Chaining and Hooks

Concerts enable scores to chain together — each score spawning the next
on success, creating multi-score workflows.

### Post-Success Hooks

The `on_success` field defines hooks that run after all sheets pass validation:

```yaml
on_success:
  # Chain to another score
  - type: run_job
    job_path: "examples/quality-continuous.yaml"
    description: "Chain to next quality iteration"
    detached: true          # Don't wait for completion
    fresh: true             # Clear previous state

  # Run a shell command
  - type: run_command
    command: "curl -X POST https://api.example.com/notify"
    description: "Notify deployment system"

  # Run a script
  - type: run_script
    command: "./deploy.sh"
    description: "Deploy changes"
```

**Hook types:**

| Type | Description | Required Fields |
|------|-------------|-----------------|
| `run_job` | Chain to another Marianne score | `job_path` |
| `run_command` | Execute a shell command | `command` |
| `run_script` | Execute a script file | `command` |

**Hook options:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `detached` | bool | `false` | For `run_job`: spawn and don't wait. Routes through daemon IPC when available, falls back to subprocess. |
| `fresh` | bool | `false` | For `run_job`: pass `--fresh` to clear previous state. Required for self-chaining. |
| `inherit_learning` | bool | `true` | Share outcome store with parent score. |
| `on_failure` | `"continue"` \| `"abort"` | `"continue"` | What to do if hook fails. |
| `timeout_seconds` | float | `300.0` | Maximum hook execution time. |

### Concert Configuration

Enable concert mode for multi-score chaining:

```yaml
concert:
  enabled: true
  max_chain_depth: 10       # Maximum number of chained jobs
  cooldown_between_jobs_seconds: 120
  inherit_workspace: true   # Child jobs inherit parent workspace
  concert_log_path: null    # Default: workspace/concert.log
  abort_concert_on_hook_failure: false
```

**Self-chaining pattern** (from `examples/quality-continuous.yaml`):

```yaml
on_success:
  - type: run_job
    job_path: "examples/quality-continuous.yaml"   # Chain to itself
    detached: true
    fresh: true        # CRITICAL: prevents infinite empty-run loop

concert:
  enabled: true
  max_chain_depth: 10  # Safety limit
```

### Conductor Configuration

Identify who is conducting the score:

```yaml
conductor:
  name: "Quality Improvement Agent"
  role: ai                  # human | ai | hybrid
  identity_context: "Automated quality improvement system"
  preferences:
    prefer_minimal_output: true
    auto_retry_on_transient_errors: true
```

---

## Testing Your Score

### Structural Validation

Validate your score's YAML structure and field values:

```bash
mzt validate my-score.yaml
```

Exit codes:
- `0`: Valid (warnings/info are OK)
- `1`: Invalid (errors found)
- `2`: Cannot validate (file not found, YAML unparseable)

For JSON output (CI/CD integration):

```bash
mzt validate my-score.yaml --json
```

### Dry Run

Simulate execution without actually running Claude:

```bash
mzt run my-score.yaml --dry-run
```

Dry run works **without** a running daemon and shows:
- How sheets will be divided
- What prompts will be rendered
- Which validations will run

### Detached Execution

For long-running scores, use `setsid` to create an independent session:

```bash
# CORRECT: setsid creates independent session group
setsid mzt run my-score.yaml > workspace/marianne.log 2>&1 &

# Monitor progress
mzt status my-score --watch
tail -f workspace/marianne.log
```

**Never** wrap Marianne with `timeout` — Marianne handles its own internal
timeouts. External `timeout` causes `SIGKILL`, which corrupts state files.

### Validate All Examples

Verify all bundled examples are valid:

```bash
for f in examples/*.yaml; do
  echo -n "$f: "
  mzt validate "$f" 2>&1 | tail -1
done
```

### Common Validation Errors

| Error Code | Description | Fix |
|------------|-------------|-----|
| V001 | Jinja syntax error in template | Check `{% %}` and `{{ }}` syntax |
| V002 | Workspace parent directory missing | Create parent directory or use auto-fixable `--self-healing` |
| V003 | Template file not found | Check `prompt.template_file` path |
| V007 | Invalid regex in validation pattern | Fix regex in `content_regex` or `rate_limit.detection_patterns` |
| V101 | Undefined template variable (warning) | Add variable to `prompt.variables` or check spelling |
| V103 | Very short timeout (warning) | Increase `backend.timeout_seconds` |
| V108 | Missing prelude/cadenza file (warning) | Check file path in `sheet.prelude` or `sheet.cadenzas`. Jinja-templated paths are skipped. |

---

## Best Practices

### Execution

1. **Use `setsid` for long-running scores.** Direct `&` background processes
   die when the terminal session ends.

2. **Set appropriate timeouts per stage.** A 10-minute timeout for a code
   review sheet and an 8-hour timeout for a monitoring sheet are very
   different needs. Use `backend.timeout_overrides` for per-sheet control.

3. **Always declare dependencies when using parallel execution.** Without
   a dependency DAG, `parallel.enabled: true` makes ALL sheets immediately
   eligible for concurrent execution (up to `max_concurrent`). If your sheets
   must run in order, add explicit dependencies to control the sequence.

### Prompts

4. **Use a preamble for consistent context.** Put shared instructions in
   `prompt.variables` and reference them at the top of every stage:

   ```yaml
   prompt:
     variables:
       preamble: |
         You are working on Project X.
         Workspace: {{ workspace }}
         Rules: be thorough, verify everything.
     template: |
       {{ preamble }}
       {% if stage == 1 %}
       ...
       {% endif %}
   ```

5. **Put validation markers in prompt instructions.** If your validations
   check for `"SETUP_COMPLETE"` in a file, tell Claude to write that marker:

   ```yaml
   prompt:
     template: |
       Write your output to {{ workspace }}/01-setup.md
       End with: SETUP_COMPLETE
   ```

6. **Use `{% if stage == N %}` for fan-out templates.** When using fan-out,
   branch your template on `stage` rather than `sheet_num`, since sheet
   numbers change after expansion but stage numbers don't.

### Validations

7. **Use `command_succeeds` for project-root file checks.** The `file_exists`
   and `content_contains` types resolve paths relative to the workspace.
   For files outside the workspace (like `setup.sh` at the project root),
   use `command_succeeds` with explicit paths:

   ```yaml
   validations:
     - type: command_succeeds
       command: "test -f ../docs/score-writing-guide.md"
       description: "Score writing guide must exist"
   ```

8. **Use `condition` to scope validations.** Don't check for stage-3 outputs
   during stage 1:

   ```yaml
   validations:
     - type: file_exists
       path: "{workspace}/03-synthesis.md"
       condition: "stage >= 3"
       description: "Synthesis document created"
   ```

9. **Use staged validations for build pipelines.** Run lint before tests,
   tests before security scans. If lint fails, don't waste time on tests.

### Structure

10. **One stage per sheet (`size: 1`) for complex workflows.** When each
    stage has unique instructions, set `size: 1` and `total_items` to the
    number of stages. Use `{% if stage == N %}` blocks in the template.

11. **Batch items per sheet for homogeneous work.** When every sheet does
    the same thing (e.g., reviewing commits), set `size` to a reasonable
    batch size and `total_items` to the total count.

12. **Use `workspace_lifecycle` for self-chaining scores.** Prevent stale
    artifacts from previous iterations:

    ```yaml
    workspace_lifecycle:
      archive_on_fresh: true
      max_archives: 10
    ```

---

## Migrating from `backend:` to `instrument:`

Marianne's original `backend:` syntax still works, but `instrument:` is the
recommended syntax for new scores. The migration is straightforward.

### Quick Reference

| Before (`backend:`) | After (`instrument:`) |
|----------------------|-----------------------|
| `backend: { type: claude_cli }` | `instrument: claude-code` |
| `backend: { type: anthropic_api }` | `instrument: anthropic_api` |
| `backend: { type: ollama }` | `instrument: ollama` |
| `backend: { type: recursive_light }` | `instrument: recursive_light` |

### Full Example

**Before:**

```yaml
name: my-score
workspace: ../workspaces/my-score

backend:
  type: claude_cli
  timeout_seconds: 1800
  skip_permissions: true
  allowed_tools: [Read, Write, Bash]
  timeout_overrides:
    3: 3600
```

**After:**

```yaml
name: my-score
workspace: ../workspaces/my-score

instrument: claude-code
instrument_config:
  timeout_seconds: 1800
  skip_permissions: true
  allowed_tools: [Read, Write, Bash]
  timeout_overrides:
    3: 3600
```

### Field Mapping

| `backend:` field | `instrument_config:` equivalent | Notes |
|------------------|---------------------------------|-------|
| `type` | `instrument:` (top-level) | Name changes: `claude_cli` → `claude-code` |
| `timeout_seconds` | `timeout_seconds` | Same field name |
| `skip_permissions` | `skip_permissions` | Same field name |
| `disable_mcp` | `disable_mcp` | Same field name |
| `output_format` | `output_format` | Same field name |
| `cli_model` | `model` | Renamed |
| `allowed_tools` | `allowed_tools` | Same field name |
| `system_prompt_file` | `system_prompt_file` | Same field name |
| `working_directory` | `working_directory` | Same field name |
| `timeout_overrides` | `timeout_overrides` | Same field name |
| `sheet_overrides` | `per_sheet_instrument_config` | Moved to `sheet:` section |
| `max_output_capture_bytes` | `max_output_capture_bytes` | Same field name |

### What You Gain

- **Multi-instrument scores.** `instrument:` supports per-sheet and per-movement
  assignment. `backend:` does not.
- **Plugin instruments.** Custom CLI tools can be added as YAML profiles in
  `~/.marianne/instruments/` or `.marianne/instruments/`.
- **Validation.** `mzt validate` warns when an instrument name is not recognized
  (V210). No equivalent exists for `backend.type` — typos fail silently at runtime.
- **Named aliases.** The `instruments:` key lets you declare reusable instrument
  configurations referenced by name across your score.

### Compatibility

Both `backend:` and `instrument:` cannot be used in the same score — `mzt validate`
rejects this as an error. The `backend:` syntax continues to work unchanged for
all existing scores. No deprecation warnings are emitted.
