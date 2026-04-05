# Getting Started with Mozart AI Compose

Mozart AI Compose orchestrates multi-phase AI workflows with checkpointing, validation gates, and automatic recovery. You write a declarative YAML configuration (a **score**), and Mozart decomposes it into **sheets** (execution stages), runs each one through an AI **instrument**, validates the output, and recovers from failures automatically.

**Repository:** [github.com/Mzzkc/mozart-ai-compose](https://github.com/Mzzkc/mozart-ai-compose)

## Installation

### From Source

```bash
# Clone the repository
git clone https://github.com/Mzzkc/mozart-ai-compose.git
cd mozart-ai-compose

# Install with conductor support (required for score execution)
./setup.sh --daemon

# Or manually:
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e ".[daemon]"
```

### With Development Dependencies

```bash
./setup.sh --dev --daemon
# Or: pip install -e ".[dev,daemon]"
```

### Verify Installation

```bash
mozart --version
```

## Prerequisites

Mozart uses Claude CLI as its default instrument. Ensure you have:

1. **Claude CLI installed**: Follow [Claude CLI installation guide](https://docs.anthropic.com/claude-code)
2. **API access configured**: Claude CLI should be authenticated
3. **Daemon support installed**: `pip install -e ".[daemon]"` or `./setup.sh --daemon`

After installation, run `mozart doctor` to verify your environment is ready.

## Quick Start: Run hello-mozart.yaml

The fastest way to see Mozart in action:

```bash
# 1. Start the conductor (required for execution)
mozart start

# 2. Run the hello score
mozart run examples/hello-mozart.yaml

# 3. Watch progress
mozart status hello-mozart

# 4. Open the result in your browser
open workspaces/hello-mozart/the-sky-library.html   # macOS
# xdg-open workspaces/hello-mozart/the-sky-library.html  # Linux
```

`hello-mozart.yaml` creates an interconnected fiction experience in three movements: a world setting, three parallel character vignettes, and a finale that weaves them together — all presented as a beautifully designed HTML page you can open in any browser. Five sheets, ~5 minutes, real creative output.

## How Sheets Work

Mozart splits work into **sheets** — execution stages processed one at a time (or in parallel when dependencies allow). You write one prompt template, and Mozart runs it once per sheet with different variables:

| Config | Meaning |
|--------|---------|
| `total_items: 30` | You have 30 items to process |
| `size: 10` | Each sheet handles 10 items |
| Result | 3 sheets: items 1-10, 11-20, 21-30 |

Each sheet gets its own `{{ sheet_num }}`, `{{ start_item }}`, and `{{ end_item }}` — so one template produces multiple runs.

## Your First Custom Score

### Step 1: Create a Configuration File

Create a file called `my-first-score.yaml`:

```yaml
# my-first-score.yaml
name: "my-first-score"
description: "Process files in sheets of 10"

instrument: claude-code

sheet:
  size: 10
  total_items: 30

prompt:
  template: |
    Process sheet {{ sheet_num }} of {{ total_sheets }}.

    Items in this sheet: {{ start_item }} to {{ end_item }}.

    Please:
    1. Review the items
    2. Generate a summary
    3. Save to {{ workspace }}/output/sheet{{ sheet_num }}.md

    {{ stakes }}

  stakes: "Take your time and be thorough!"

retry:
  max_retries: 2

validations:
  - type: file_exists
    path: "{workspace}/output/sheet{sheet_num}.md"
    description: "Output file created"
```

> **Template syntax note:** Prompts use Jinja2 syntax (`{{ sheet_num }}`). Validation paths use a different expansion syntax (`{sheet_num}` — single braces, no spaces). This is because prompts go through Jinja2 rendering while validation paths use Python string formatting.

### Step 2: Validate Your Configuration

Before running, validate the configuration:

```bash
mozart validate my-first-score.yaml
```

You should see:
```
Validating my-first-score...

✓ YAML syntax valid
✓ Schema validation passed (Pydantic)

Running extended validation checks...

INFO (consider reviewing):
  i [V205] All validations are file_exists — stale files from previous runs will pass
         Suggestion: Consider adding file_modified or content checks

Validation: PASSED (with warnings)
```

The `V205` note is just advice — your score is valid. It's pointing out that `file_exists` validations can be fooled by leftover files from a previous run. Adding `file_modified` or `content_contains` checks makes validations more robust.

### Step 3: Dry Run

Preview what Mozart will do without executing:

```bash
mozart run my-first-score.yaml --dry-run
```

This shows:
- Score configuration summary
- Sheet plan with item ranges
- Prompt that will be sent to the instrument

### Step 4: Start the Conductor

The Mozart conductor is required for score execution:

```bash
mozart start
mozart conductor-status   # Verify it's running
```

> **What if I skip this?** Running `mozart run` without a conductor produces:
> ```
> Error: Conductor not running. Start with: mozart start
> ```
> Only `mozart validate` and `mozart run --dry-run` work without a conductor.
> See the [Daemon Guide](daemon-guide.md) for why this is required.

### Step 5: Run the Score

Execute the score:

```bash
mozart run my-first-score.yaml
```

You'll see:
- Progress bar with ETA
- Current sheet status
- Validation results

### Step 6: Check Status

While running (or after), check score status:

```bash
# Show specific score details
mozart status my-first-score

# List all active scores
mozart list
```

## Handling Interruptions

### Graceful Shutdown

Press `Ctrl+C` during execution for a graceful shutdown:

```
Ctrl+C received. Finishing current sheet and saving state...

State saved. Score paused at sheet 2/3.

To resume: mozart resume my-first-score
```

### Resuming Scores

Resume a paused or failed score:

```bash
mozart resume my-first-score
```

Mozart continues from where it left off.

## Common Patterns

### Pattern 1: Code Review Sheets

```yaml
name: "code-review"
description: "Review PRs in sheets"

instrument: claude-code

sheet:
  size: 5
  total_items: 25

prompt:
  template: |
    Review PRs {{ start_item }} to {{ end_item }}.

    For each PR:
    - Check code quality
    - Identify potential bugs
    - Suggest improvements

    Save results to {{ workspace }}/reviews/sheet{{ sheet_num }}.md

validations:
  - type: file_exists
    path: "{workspace}/reviews/sheet{sheet_num}.md"
```

### Pattern 2: Documentation Generation

```yaml
name: "generate-docs"
description: "Generate API documentation"

instrument: claude-code

sheet:
  size: 10
  total_items: 50

prompt:
  template: |
    Document functions {{ start_item }} to {{ end_item }}.

    For each function, generate:
    - Description
    - Parameters
    - Return values
    - Examples

    Output: {{ workspace }}/api-docs/sheet{{ sheet_num }}.md

validations:
  - type: file_exists
    path: "{workspace}/api-docs/sheet{sheet_num}.md"
  - type: content_contains
    path: "{workspace}/api-docs/sheet{sheet_num}.md"
    pattern: "## Parameters"
```

### Pattern 3: Data Processing with Retries

```yaml
name: "process-data"
description: "Process data with robust error handling"

instrument: claude-code

sheet:
  size: 20
  total_items: 100

retry:
  max_retries: 3
  base_delay_seconds: 30
  exponential_base: 2.0

rate_limit:
  wait_minutes: 15
  max_waits: 10

prompt:
  template: |
    Process data sheet {{ sheet_num }}.
    Items: {{ start_item }} to {{ end_item }}.

    Save results to {{ workspace }}/processed/sheet{{ sheet_num }}.json

validations:
  - type: file_exists
    path: "{workspace}/processed/sheet{sheet_num}.json"
```

### Pattern 4: Parallel Expert Reviews (Fan-Out)

```yaml
name: "parallel-reviews"
description: "Fan-out pattern: multiple perspectives in parallel"

instrument: claude-code

sheet:
  size: 1
  total_items: 3
  fan_out:
    2: 3              # Stage 2 fans out to 3 parallel instances
  dependencies:
    2: [1]             # All reviewers depend on setup
    3: [2]             # Synthesis waits for all reviewers (fan-in)

parallel:
  enabled: true
  max_concurrent: 3

prompt:
  variables:
    perspectives:
      1: "security"
      2: "performance"
      3: "maintainability"
  template: |
    {% if stage == 1 %}
    ## Setup
    Inventory the codebase and identify key areas for review.
    Save findings to {{ workspace }}/01-inventory.md
    {% elif stage == 2 %}
    ## Review: {{ perspectives[instance] }}
    Read {{ workspace }}/01-inventory.md and review from a {{ perspectives[instance] }} perspective.
    Save to {{ workspace }}/02-review-{{ perspectives[instance] }}.md
    {% elif stage == 3 %}
    ## Synthesis
    Read all review files in {{ workspace }}/02-review-*.md
    Synthesize into a prioritized action plan.
    Save to {{ workspace }}/03-synthesis.md
    {% endif %}

validations:
  - type: file_exists
    path: "{workspace}/01-inventory.md"
    condition: "stage == 1"
  - type: file_exists
    path: "{workspace}/03-synthesis.md"
    condition: "stage == 3"
```

This creates a 3-stage pipeline: setup → 3 parallel reviewers → synthesis. Each reviewer gets different instructions via `{{ perspectives[instance] }}`. See the [Score Writing Guide](score-writing-guide.md#fan-out-patterns) for more fan-out patterns.

## Template Variables

Available in prompts and validation paths (see syntax note below):

| Variable | Alias | Example | Description |
|----------|-------|---------|-------------|
| `sheet_num` | | `1` | Current sheet (1-indexed) |
| `total_sheets` | | `3` | Total number of sheets |
| `start_item` | | `1` | First item in sheet |
| `end_item` | | `10` | Last item in sheet |
| `workspace` | | `./workspace` | Workspace path |
| `stakes` | | `"Be careful!"` | Custom stakes text |
| `thinking_method` | | `"Think step by step"` | Thinking guidance |
| `stage` | `movement` | `2` | Logical stage number (with fan-out) |
| `instance` | `voice` | `1` | Instance within fan-out group |
| `fan_count` | `voice_count` | `3` | Total instances in stage |
| `total_stages` | `total_movements` | `7` | Original stage count |
| `instrument_name` | | `claude-code` | Resolved instrument for this sheet |

> **New terminology:** `movement`, `voice`, `voice_count`, and `total_movements` are aliases for `stage`, `instance`, `fan_count`, and `total_stages`. Both forms work — use whichever reads better in your score.

### Syntax Difference: Prompts vs Validation Paths

Prompts and validation paths use different template syntax:

```yaml
prompt:
  template: |
    Save to {{ workspace }}/output/sheet{{ sheet_num }}.md   # Jinja2: double braces

validations:
  - type: file_exists
    path: "{workspace}/output/sheet{sheet_num}.md"           # Python format: single braces
```

- **Prompts** use Jinja2 (`{{ variable }}`) — supports filters, conditionals, loops
- **Validation paths** use Python format strings (`{variable}`) — simple substitution only

## Monitoring with Dashboard

Start the web dashboard for real-time monitoring:

```bash
mozart dashboard
```

Access at `http://localhost:8000`:
- View all scores
- Check progress
- See sheet details
- API docs at `/docs`

Custom port:
```bash
mozart dashboard --port 3000
```

## Next Steps

**Learn more:**
- [Score Writing Guide](score-writing-guide.md) — Archetypes, Jinja2 templates, fan-out patterns, concert chaining
- [CLI Reference](cli-reference.md) — All commands and options
- [Configuration Reference](configuration-reference.md) — Every config field documented

**Explore examples:**
- [Examples](../examples/) — 37 score configurations across software, research, writing, and planning
- [Mozart Score Playspace](https://github.com/Mzzkc/mozart-score-playspace) — Creative showcase with real output: philosophy, worldbuilding, education, and more

**Go deeper:**
- [Daemon Guide](daemon-guide.md) — Conductor architecture, systemd integration, and troubleshooting
- [Known Limitations](limitations.md) — Constraints and workarounds
- [MCP Integration](MCP-INTEGRATION.md) — Model Context Protocol server for tool integration

## Troubleshooting

### Score Won't Start

1. Run `mozart doctor` to check your environment
2. Check config: `mozart validate config.yaml`
3. Verify your instrument is available: `mozart instruments list`

### Validation Failing

1. Check validation paths use `{single_braces}`, not `{{ double_braces }}`
2. Ensure validation paths start with `{workspace}/` so files are found inside the workspace
3. Verify the prompt tells the instrument to save files into `{{ workspace }}/` (not relative paths)
4. Use `--dry-run` to see the generated prompt and check that paths look correct

### Rate Limits

Mozart detects rate limits and waits automatically. Configure wait times:

```yaml
rate_limit:
  wait_minutes: 60
  max_waits: 24
```

If a score is stuck waiting on a rate limit that has already expired, clear it manually:

```bash
mozart clear-rate-limits                    # Clear all stale rate limits
mozart clear-rate-limits --instrument NAME  # Clear for a specific instrument
```

### Unknown Field Errors

Mozart strictly validates score YAML — any field name it doesn't recognize is an error, not silently ignored. This catches typos immediately:

```
Error: Schema validation failed: 1 validation error for JobConfig
insturment_config
  Extra inputs are not permitted
```

Mozart suggests corrections for common typos:

```
Hints:
  - Unknown field 'insturment_config' — did you mean 'instrument_config'?
```

If you see this error, check for typos in your YAML field names. See the [Score Writing Guide](score-writing-guide.md) for valid fields.

### Resume Not Working

1. Check score state: `mozart status <score-id>`
2. Ensure config is available (stored in state or via `--config`)
3. Use `--force` to restart completed scores
