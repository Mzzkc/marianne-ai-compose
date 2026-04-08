# Concert Patterns Guide

A **concert** is a sequence of Marianne scores that chain together — each score
spawning the next on success. Concerts enable multi-score workflows where a
research phase feeds into a writing phase, a build phase triggers deployment,
or a score evolves itself through continuous improvement cycles.

This guide covers concert configuration, chaining patterns, and the safety
mechanisms that prevent runaway orchestration.

---

## How Concerts Work

When a score completes successfully (all sheets pass validation), Marianne
checks the `on_success` field for post-success hooks. These hooks can chain to
another score, run a shell command, or execute a script. When `concert.enabled`
is true, the conductor tracks chain depth and enforces safety limits.

The flow:

1. Score A completes — all sheets pass validation
2. Marianne executes `on_success` hooks in order
3. A `run_job` hook submits Score B to the conductor
4. Score B runs, completes, and its own `on_success` hooks fire
5. This continues until no more hooks exist or `max_chain_depth` is reached

Each score in the concert is a fully independent job with its own state,
checkpointing, and retry logic. If Score B fails, Score A's state is unaffected.

---

## Quick Start: Chain Two Scores

### Step 1: Write the First Score

```yaml
# phase-1-research.yaml
name: phase-1-research
workspace: ./workspaces/my-project

instrument: claude-code

sheet:
  size: 1
  total_items: 1

prompt:
  template: |
    Research the topic and write findings to {{ workspace }}/research.md

validations:
  - type: file_exists
    path: "{workspace}/research.md"

on_success:
  - type: run_job
    job_path: "phase-2-write.yaml"
    description: "Chain to writing phase"

concert:
  enabled: true
  max_chain_depth: 3
```

### Step 2: Write the Second Score

```yaml
# phase-2-write.yaml
name: phase-2-write
workspace: ./workspaces/my-project

instrument: claude-code

sheet:
  size: 1
  total_items: 1

prompt:
  template: |
    Read {{ workspace }}/research.md and write a report at {{ workspace }}/report.md

validations:
  - type: file_exists
    path: "{workspace}/report.md"
  - type: content_contains
    path: "{workspace}/report.md"
    pattern: "## Conclusion"
```

### Step 3: Run

```bash
mzt start
mzt run phase-1-research.yaml
```

Phase 1 runs, completes, then automatically submits phase 2. Monitor both:

```bash
mzt list --all
mzt status phase-1-research
mzt status phase-2-write
```

---

## Concert Configuration Reference

The `concert:` block controls safety limits for chained execution:

```yaml
concert:
  enabled: true
  max_chain_depth: 10
  cooldown_between_jobs_seconds: 60
  inherit_workspace: true
  concert_log_path: null
  abort_concert_on_hook_failure: false
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable concert mode. Required for `run_job` hooks to chain. |
| `max_chain_depth` | int | `5` | Maximum chained jobs before the concert stops. Prevents infinite loops. |
| `cooldown_between_jobs_seconds` | float | `5.0` | Minimum wait between job transitions. Prevents resource exhaustion. |
| `inherit_workspace` | bool | `true` | Child jobs inherit parent workspace unless they specify their own. |
| `concert_log_path` | path | `null` | Consolidated log for the entire concert. Default: `workspace/concert.log`. |
| `abort_concert_on_hook_failure` | bool | `false` | If a hook fails, stop the entire concert (not just remaining hooks). |

---

## Post-Success Hook Types

The `on_success` field defines what happens after all sheets pass validation.

### `run_job` — Chain to Another Score

```yaml
on_success:
  - type: run_job
    job_path: "next-phase.yaml"
    description: "Start next phase"
    detached: true
    fresh: true
    inherit_learning: true
```

| Field | Default | Description |
|-------|---------|-------------|
| `job_path` | *required* | Path to score YAML. Supports `{workspace}` template. |
| `job_workspace` | `null` | Override workspace for chained job. |
| `detached` | `false` | Spawn the job and do not wait for completion. |
| `fresh` | `false` | Pass `--fresh` to clear previous state. Required for self-chaining. |
| `inherit_learning` | `true` | Share the learning store with the parent. |
| `on_failure` | `"continue"` | `"continue"` runs the next hook; `"abort"` stops remaining hooks. |
| `timeout_seconds` | `300.0` | Maximum hook execution time. |

### `run_command` — Execute a Shell Command

```yaml
on_success:
  - type: run_command
    command: "curl -X POST https://api.example.com/deploy"
    description: "Trigger deployment"
    working_directory: null
```

### `run_script` — Execute a Script File

```yaml
on_success:
  - type: run_script
    command: "./scripts/post-process.sh"
    description: "Run post-processing"
    working_directory: null
```

Both `run_command` and `run_script` support `{workspace}`, `{job_id}`, and
`{sheet_count}` template variables in the `command` field.

---

## Concert Patterns

### Pattern 1: Linear Pipeline

The most common pattern — a sequence of phases where each depends on the
previous one's output.

```yaml
# phase-1.yaml → phase-2.yaml → phase-3.yaml
on_success:
  - type: run_job
    job_path: "phase-2.yaml"

concert:
  enabled: true
  max_chain_depth: 5
```

Each phase writes to the shared workspace, and the next phase reads from it.
Use `inherit_workspace: true` (the default) so all phases share the same
workspace directory.

### Pattern 2: Self-Chaining (Continuous Improvement)

A score that chains to itself, running improvement cycles until the chain
depth limit is reached:

```yaml
# quality-loop.yaml
name: quality-loop
workspace: ./workspaces/quality

instrument: claude-code

sheet:
  size: 1
  total_items: 3

prompt:
  template: |
    {% if sheet_num == 1 %}
    Analyze the codebase for quality issues. Write findings to {{ workspace }}/analysis.md
    {% elif sheet_num == 2 %}
    Fix the top 3 issues from {{ workspace }}/analysis.md
    {% else %}
    Run tests and verify fixes. Write results to {{ workspace }}/results.md
    {% endif %}

validations:
  - type: file_exists
    path: "{workspace}/results.md"
    condition: "sheet_num == 3"

on_success:
  - type: run_job
    job_path: "quality-loop.yaml"
    detached: true
    fresh: true              # CRITICAL: prevents resuming from previous state

concert:
  enabled: true
  max_chain_depth: 10        # Run up to 10 improvement cycles
  cooldown_between_jobs_seconds: 120
```

The `fresh: true` flag is critical for self-chaining. Without it, the chained
job would resume from the previous run's completed state and immediately exit
— creating an infinite loop of no-ops until the chain depth limit.

### Pattern 3: Dynamic Chaining

A sheet can create the next job's configuration dynamically. The score
generates the YAML file, and the `on_success` hook picks it up:

```yaml
prompt:
  template: |
    {% if sheet_num == total_sheets %}
    Based on results so far, create the next phase configuration
    at {{ workspace }}/next-phase.yaml
    {% endif %}

on_success:
  - type: run_job
    job_path: "{workspace}/next-phase.yaml"
    description: "Chain to dynamically generated config"

concert:
  enabled: true
  max_chain_depth: 5
```

This is how Marianne's self-evolution works — each cycle analyzes outcomes
and generates the configuration for the next cycle.

### Pattern 4: Fan-Out with Post-Processing

Run a score that generates artifacts, then chain to a post-processing score:

```yaml
# generate.yaml
on_success:
  - type: run_command
    command: "python scripts/merge-outputs.py {workspace}"
    description: "Merge all generated files"
    on_failure: abort

  - type: run_job
    job_path: "review.yaml"
    description: "Chain to review phase"
```

Note the `on_failure: abort` on the merge command — if merging fails, the
review score should not run.

### Pattern 5: Notification with Chaining

Mix notifications and job chaining:

```yaml
on_success:
  - type: run_command
    command: "curl -s -X POST -d '{\"text\": \"Phase 1 complete\"}' https://hooks.slack.com/..."
    description: "Notify Slack"

  - type: run_job
    job_path: "phase-2.yaml"
    description: "Start phase 2"
```

Hooks execute in order. The Slack notification fires before phase 2 starts.

### Pattern 6: Multi-Score Pipeline with Isolated Workspaces

When each score needs its own workspace but shares specific output files:

```yaml
# phase-1-research.yaml
name: phase-1-research
workspace: ./workspaces/research

instrument: claude-code

sheet:
  size: 1
  total_items: 2

prompt:
  template: |
    {% if sheet_num == 1 %}
    Research the topic. Write findings to {{ workspace }}/findings.md
    {% else %}
    Summarize findings into {{ workspace }}/summary.json with structured data.
    {% endif %}

on_success:
  - type: run_command
    command: "cp {workspace}/summary.json ./workspaces/shared/research-summary.json"
    description: "Copy summary to shared location"
    on_failure: abort

  - type: run_job
    job_path: "phase-2-implement.yaml"
    job_workspace: ./workspaces/implementation
    description: "Chain to implementation with separate workspace"

concert:
  enabled: true
  inherit_workspace: false    # Each phase gets its own workspace
  max_chain_depth: 4
```

Use `inherit_workspace: false` with explicit `job_workspace` when scores
need isolated workspaces. Bridge data between them with `run_command` hooks
that copy specific files.

---

## Cross-Score Data Flow

Data flows between scores in a concert through three mechanisms, each suited
to different use cases.

### Mechanism 1: Shared Workspace (Default)

The simplest approach. With `inherit_workspace: true` (the default), all
scores in a concert share the same workspace directory. Each score reads
files written by the previous score and writes new files for the next one.

```
Score A writes → workspace/research.md
Score B reads  ← workspace/research.md
Score B writes → workspace/report.md
Score C reads  ← workspace/report.md
```

This works because workspace paths in prompts use `{{ workspace }}`, which
resolves to the same directory for all scores in the chain. The downstream
score's prompt can reference files created by the upstream score directly:

```yaml
# phase-2-write.yaml (chained from phase-1-research.yaml)
prompt:
  template: |
    Read the research at {{ workspace }}/research.md
    Write a polished report at {{ workspace }}/report.md
```

**When to use:** Most concert workflows. The workspace acts as a shared
filesystem contract between scores.

**Caution:** Avoid filename collisions. If both Score A and Score B write
to `output.md`, Score B overwrites Score A's output. Use stage-prefixed
filenames (`01-research.md`, `02-report.md`) or subdirectories.

### Mechanism 2: Explicit File Transfer

When scores need isolated workspaces, use `run_command` hooks to copy
specific files between workspaces before the next score starts:

```yaml
on_success:
  - type: run_command
    command: "mkdir -p ./workspaces/phase2 && cp {workspace}/results.json ./workspaces/phase2/"
    description: "Transfer results to next phase"
    on_failure: abort

  - type: run_job
    job_path: "phase-2.yaml"
    job_workspace: ./workspaces/phase2
```

**When to use:** When scores produce many intermediate files that would
clutter the next phase, or when you need clear boundaries between what
each score can see.

### Mechanism 3: Learning Store Inheritance

With `inherit_learning: true` (the default), chained scores share the same
learning store. This means patterns discovered during Score A are available
to Score B's agents immediately — the learning system carries forward
optimization insights, failure patterns, and success strategies across the
entire concert.

```yaml
on_success:
  - type: run_job
    job_path: "phase-2.yaml"
    inherit_learning: true    # Default — patterns carry forward
```

Set `inherit_learning: false` when you want a score to start with a clean
learning context, such as when the next score operates in a completely
different domain where prior patterns would be misleading.

### Mechanism 4: Dynamic Score Generation

The most powerful data flow pattern. A sheet within Score A generates the
YAML configuration for Score B, embedding workspace paths, variables, or
even prompt content derived from Score A's results:

```yaml
prompt:
  template: |
    {% if sheet_num == total_sheets %}
    Based on everything you've learned, create a Marianne score at
    {{ workspace }}/next-phase.yaml that addresses the gaps found.

    The score should:
    - Use workspace: {{ workspace }}
    - Reference the analysis at {{ workspace }}/analysis.md
    - Focus on the top 3 issues identified
    {% endif %}

on_success:
  - type: run_job
    job_path: "{workspace}/next-phase.yaml"
```

The `{workspace}` template in `job_path` resolves at hook execution time,
so the dynamically generated score is picked up automatically. This is how
Marianne's self-evolution works.

### Choosing a Data Flow Strategy

| Strategy | Isolation | Complexity | Use When |
|----------|-----------|------------|----------|
| Shared workspace | None | Low | Scores are tightly coupled phases of one workflow |
| File transfer | Full | Medium | Scores are independent but exchange specific artifacts |
| Learning inheritance | N/A | None | Always, unless domains are unrelated |
| Dynamic generation | Full | High | Next score's structure depends on current score's results |

---

## Safety Mechanisms

### Chain Depth Limit

`max_chain_depth` prevents infinite loops. When the limit is reached, the
concert stops gracefully — the current job completes, but no further `run_job`
hooks fire.

Set this based on your workflow:

| Workflow Type | Recommended Depth | Why |
|--------------|-------------------|-----|
| Linear pipeline (A → B → C) | Number of phases | Fixed, predictable chain |
| Self-chaining quality loop | 5–10 | Diminishing returns after ~10 cycles |
| Issue fixer (one issue per cycle) | 20–50 | Each cycle is independent work |
| Self-evolution | 3–5 | Each cycle is expensive; review between runs |

The conductor tracks depth using a `ConcertContext` that passes between jobs.
When depth reaches the limit, the log shows:

```
concert.chain_depth_exceeded  current=10  max=10
```

### Cooldown

`cooldown_between_jobs_seconds` adds a mandatory delay between job transitions.
The conductor enforces this with an `asyncio.sleep()` before submitting the
next job. This prevents several problems:

- **API rate limits:** Back-to-back jobs hitting the same instrument can
  trigger rate limits. A 60–120 second cooldown lets quotas recover.
- **Resource exhaustion:** Each job spawns child processes for instrument
  execution. Cooldown ensures the previous job's processes are fully cleaned
  up before the next job starts.
- **System stability:** Without cooldown, a fast self-chaining score can
  submit hundreds of jobs in minutes, overwhelming the conductor.

Recommended values from production scores:

```yaml
# Quality improvement loops — moderate cooldown
cooldown_between_jobs_seconds: 120    # 2 minutes

# Issue fixers — longer cooldown (each fix is independent)
cooldown_between_jobs_seconds: 300    # 5 minutes

# Fast pipelines where phases are cheap
cooldown_between_jobs_seconds: 5      # Default, minimal delay
```

### Abort Behavior

Concert abort operates at two levels — per-hook and per-concert:

**Per-hook abort** (`on_failure: "abort"` on individual hooks): Stops
remaining hooks in the `on_success` list, but does not prevent the
current job from being marked as completed. Use this for critical
prerequisites where later hooks depend on earlier ones:

```yaml
on_success:
  # If merge fails, don't run review or notify
  - type: run_command
    command: "python scripts/merge.py {workspace}"
    on_failure: abort

  - type: run_job
    job_path: "review.yaml"

  - type: run_command
    command: "curl -X POST https://hooks.slack.com/..."
```

**Concert-level abort** (`abort_concert_on_hook_failure: true`): Any hook
failure in any job stops the entire concert. The current job completes, but
its `on_success` hooks stop processing, and no further jobs are spawned.
The conductor logs:

```
concert.aborted  reason="hook failure with abort_concert_on_hook_failure=true"
```

Use concert-level abort for critical pipelines where a failure in any phase
means downstream work would be wasted:

```yaml
concert:
  enabled: true
  abort_concert_on_hook_failure: true    # Any failure stops everything
  max_chain_depth: 5
```

### Concert Context Tracking

The conductor maintains a `ConcertContext` across the entire chain, tracking:

| Field | Description |
|-------|-------------|
| `concert_id` | Unique ID for the concert (generated at first job) |
| `chain_depth` | Current depth in the chain (0-indexed) |
| `parent_job_id` | Job ID of the parent that spawned this job |
| `root_workspace` | Workspace of the first job in the chain |
| `total_jobs_run` | Accumulated count of jobs completed |
| `total_sheets_completed` | Accumulated sheets across all jobs |
| `jobs_in_chain` | List of all job IDs in the chain |

This context is passed from parent to child via the conductor's IPC when
using `detached: true`, or via subprocess environment when falling back to
direct execution.

---

## Monitoring Concerts

```bash
# See all jobs including concert chain
mzt list --all

# Check specific job
mzt status phase-1-research

# View concert log (if configured)
cat workspaces/my-project/concert.log

# Diagnose a failed concert job
mzt diagnose phase-2-write
```

---

## Troubleshooting

### Chain Did Not Fire

- Verify `concert.enabled: true` is set
- Check that the parent job completed successfully (not just some sheets)
- Verify `job_path` points to an existing file
- Check `max_chain_depth` has not been exceeded

### Self-Chaining Loops Forever

- Ensure `fresh: true` on the self-referencing `run_job` hook
- Without `fresh`, the chained job resumes from COMPLETED state and exits
  immediately, triggering the hook again

### Hook Timeout

If a `run_job` hook times out, increase `timeout_seconds`. For long-running
chained jobs, use `detached: true` so the hook returns immediately after
submitting the job to the conductor.

### Workspace Conflicts in Self-Chaining

If a self-chaining score produces stale results, the issue is usually leftover
files from the previous iteration. Use `workspace_lifecycle` to archive old
state:

```yaml
workspace_lifecycle:
  archive_on_fresh: true     # Archive previous workspace before clearing
  max_archives: 10           # Keep last 10 iterations
```

Or set `inherit_workspace: false` on the concert so each iteration gets
a fresh workspace (you lose file-based data flow, but gain clean state).

### Conductor Backpressure Rejecting Chained Jobs

The conductor's backpressure system can reject chained jobs if system
resources are constrained. When this happens, the `run_job` hook fails with
"System under high pressure." Increase `cooldown_between_jobs_seconds` to
give the system more recovery time between job transitions, or reduce the
number of concurrent jobs in the conductor config.

### "Concert chain depth exceeded"

The chain depth limit was reached. This is working as intended — it prevents
runaway chaining. If you need more iterations, increase `max_chain_depth`.
If the concert should have stopped earlier, add conditional logic to your
score (e.g., `skip_when_command` to check whether more work is needed).

---

## Next Steps

- [Score Writing Guide](score-writing-guide.md) — Full score YAML reference including `on_success` and `concert` fields
- [Conductor Guide](daemon-guide.md) — How the conductor manages concurrent jobs and concerts
- [Movement Design Guide](movement-design-guide.md) — Multi-instrument and fan-out patterns within a single score
- [Validation Patterns Guide](validation-patterns-guide.md) — Validation strategies that gate concert chaining
- [Learning System Guide](learning-system-guide.md) — How pattern learning carries across concert chains
- [CLI Reference](cli-reference.md) — `mzt list`, `mzt status`, and monitoring commands
- [Getting Started](getting-started.md) — If you are new to Marianne
