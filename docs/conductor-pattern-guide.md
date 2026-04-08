# Conductor Patterns: Human-in-the-Loop AI Orchestration

The Marianne conductor enables a fundamentally different way of working with AI — async human-in-the-loop execution where you can pause ongoing work, inspect intermediate results, modify the approach, and resume. This guide covers the operational patterns that make the conductor a powerful tool for complex, multi-phase workflows.

## What Makes the Conductor Pattern Special

Traditional AI tools operate synchronously: you submit a prompt, wait for completion, and inspect results. If the output isn't what you need, you start over. The conductor pattern inverts this:

- **Async execution** — Submit work and continue with other tasks. Check back when ready.
- **Persistent state** — All progress is checkpointed to SQLite. System crashes don't lose work.
- **Inspectable progress** — See exactly where execution is at any moment with `mzt status`.
- **Graceful interruption** — Pause jobs mid-execution, review intermediate outputs, adjust course.
- **Multi-job orchestration** — Run multiple scores concurrently, each with independent checkpoints.

This pattern is especially valuable for:

- **Long-running workflows** (hours to days) where you need to check progress without blocking
- **Exploratory work** where you discover requirements as outputs emerge
- **High-stakes execution** where you want human verification between phases
- **Parallel workflows** where multiple independent tasks run simultaneously

## Quick Start: The Pause/Modify/Resume Pattern

The most common conductor pattern: start a job, pause it partway through to inspect outputs, optionally modify the workspace or score, then resume.

```bash
# 1. Start the conductor
mzt start

# 2. Submit a long-running score
mzt run examples/nonfiction-book.yaml

# 3. Check progress while it runs
mzt status nonfiction-book --watch

# 4. After sheet 3 completes, pause to inspect outputs
mzt pause nonfiction-book

# 5. Review intermediate outputs
ls -lh workspaces/nonfiction-book/
cat workspaces/nonfiction-book/03-outline.md

# 6. If the outline looks good, resume execution
mzt resume nonfiction-book

# 7. If the outline needs adjustment, edit it manually and resume
vim workspaces/nonfiction-book/03-outline.md
mzt resume nonfiction-book
```

The conductor saves checkpoint state after every sheet. When you resume, execution continues from the next sheet — the modified workspace files become inputs to downstream sheets.

### When to Pause

Pause points are strategic moments where human judgment adds value:

- **After setup/planning sheets** — Verify the AI understood the task correctly before expensive execution
- **Before irreversible actions** — Review generated code/config before a commit sheet
- **Between major phases** — Check that phase 1 outputs are suitable inputs for phase 2
- **When validations fail repeatedly** — Inspect what's going wrong before exhausting retries

## Multi-Score Management

The conductor's job registry tracks all submitted scores, whether running, paused, or complete. This enables concurrent execution of independent workflows.

### Running Scores in Parallel

```bash
# Start the conductor once
mzt start

# Submit multiple independent scores
mzt run examples/parallel-research.yaml &
mzt run examples/quality-continuous.yaml &
mzt run examples/docs-generator.yaml &

# List all active jobs
mzt list

# Check individual job status
mzt status parallel-research
mzt status quality-continuous
mzt status docs-generator
```

Each score runs in its own workspace with independent state. The conductor enforces concurrency limits (`max_concurrent_jobs` in `~/.marianne/conductor.yaml`) and applies backpressure when system resources are constrained.

### Job Priority and Resource Allocation

The conductor doesn't yet support explicit job priorities, but you can control resource allocation indirectly:

```yaml
# ~/.marianne/conductor.yaml
max_concurrent_jobs: 10          # Total jobs across all scores
resource_limits:
  max_memory_mb: 8192            # System pressure threshold
  max_processes: 50              # Child process limit
```

When system pressure is HIGH (>85% memory usage or high process count), new job submissions are rejected. When CRITICAL (>95% memory), the conductor may cancel the oldest running job to prevent system instability.

### Isolating Development Work with Conductor Clones

Test scores or CLI changes without risking your production conductor:

```bash
# Start a clone conductor with isolated state
mzt --conductor-clone start

# Submit test scores to the clone
mzt --conductor-clone run my-test-score.yaml

# Check clone status
mzt --conductor-clone conductor-status

# Named clones for parallel testing environments
mzt --conductor-clone=staging start
mzt --conductor-clone=staging run staging-test.yaml

# Stop the clone when done
mzt --conductor-clone stop
```

Clone conductors:
- Inherit your production config (`~/.marianne/conductor.yaml`)
- Use isolated socket paths (`/tmp/marianne-clone.sock`, `/tmp/marianne-clone-staging.sock`)
- Maintain separate state databases
- Don't interfere with production jobs

See [CLI Reference](cli-reference.md#conductor-clones) for full clone documentation.

## Operational Runbooks

### Runbook 1: Daily Quality Loop

Run automated code quality checks continuously in the background:

```bash
# Start quality improvement score (self-chaining)
mzt run examples/quality-continuous.yaml

# Let it run for a day, checking progress periodically
mzt status quality-continuous

# Review cumulative improvements after N iterations
git log --oneline | head -20

# Pause when satisfied
mzt pause quality-continuous
```

`quality-continuous.yaml` uses concert chaining (`on_success: run_job`) to restart itself after each iteration. The `concert.max_chain_depth` setting prevents infinite loops. Each iteration finds issues, fixes them, commits, and chains to the next iteration.

### Runbook 2: Checkpoint-Based Debugging

A score fails at sheet 12 of 20. Instead of restarting from the beginning, inspect and fix:

```bash
# Check what failed
mzt status my-score
mzt diagnose my-score

# Review sheet 12's output and validation failures
cat workspaces/my-score/12-*.md
mzt errors my-score --verbose

# Option 1: Fix workspace files manually
vim workspaces/my-score/12-output.md
mzt resume my-score

# Option 2: Modify the score config and resume
vim my-score.yaml   # Fix the prompt template or validation
mzt resume my-score --config my-score.yaml

# Option 3: Use recovery mode to retry specific failed sheets
mzt recover my-score --sheets 12
```

The checkpoint state tracks which sheets passed validation. `mzt resume` skips completed sheets and re-executes failed/pending ones.

### Runbook 3: Concert Chain Monitoring

Track multi-score pipelines that chain together:

```bash
# Start a score that chains to subordinate scores
mzt run examples/issue-fixer.yaml

# Monitor the concert chain
tail -f workspaces/issue-fixer/concert.log

# List all jobs in the concert
mzt list --concert issue-fixer

# If a subordinate job fails, diagnose it independently
mzt diagnose subordinate-job-id
```

Concert chains create parent-child relationships between scores. The `concert.log` file tracks the chain depth and job IDs. Use `mzt list` to see all jobs spawned by a concert.

### Runbook 4: Rate Limit Recovery

A score hits API rate limits and pauses. The conductor auto-resumes when limits expire, but you can also clear them manually:

```bash
# Check if a score is waiting on rate limits
mzt status my-score
# Output: "Sheet 5 waiting (rate limit until 14:30)"

# Option 1: Let the conductor auto-resume (default behavior)
# The baton schedules a timer to clear the limit and resume waiting sheets

# Option 2: Manually clear stale rate limits
mzt clear-rate-limits

# Option 3: Clear limits for a specific instrument
mzt clear-rate-limits --instrument claude-code

# Resume execution if auto-resume didn't trigger
mzt resume my-score
```

The conductor's baton execution engine (`use_baton: true` by default) automatically clears rate limits when they expire and resumes WAITING sheets. Manual clearing is only needed if the auto-resume timer failed or you want to force an early retry.

### Runbook 5: Graceful Conductor Restart

Update Marianne code or conductor config without losing running jobs:

```bash
# 1. Pause all running jobs
mzt pause job1
mzt pause job2

# 2. Stop the conductor
mzt stop

# 3. Update code or config
pip install -e ".[dev]"
vim ~/.marianne/conductor.yaml

# 4. Restart conductor
mzt start

# 5. Resume paused jobs
mzt resume job1
mzt resume job2
```

The conductor's persistent registry (SQLite) survives restarts. Jobs marked as `queued` or `running` during a crash are automatically marked `failed` on the next startup (orphan recovery). Properly paused jobs resume cleanly.

**Alternative for config-only changes:** Send `SIGHUP` to hot-reload config without a full restart:

```bash
vim ~/.marianne/conductor.yaml
kill -SIGHUP $(cat /tmp/marianne.pid)
mzt conductor-status   # Verify new config loaded
```

## Comparing Execution Modes

Marianne supports three execution patterns:

| Pattern | Command | State Persistence | Use Case |
|---------|---------|-------------------|----------|
| **Dry run** | `mzt run --dry-run` | None | Validate config, preview prompts |
| **Direct (no conductor)** | `mzt validate` | None | Static validation only |
| **Conductor** | `mzt run` | SQLite checkpoint | Production execution, resumability |

**Key differences:**

- **Dry run** renders prompts and validates the score but doesn't execute. No conductor required.
- **Direct validation** checks YAML syntax and schema. No execution, no conductor.
- **Conductor mode** is the only way to actually run sheets. Checkpoint state survives crashes, enables pause/resume, and supports concurrent jobs.

## Advanced Patterns

### Pattern: Staged Rollout with Manual Gates

Execute a pipeline in phases with human approval between stages:

```yaml
# rollout-pipeline.yaml
sheet:
  size: 1
  total_items: 5
  dependencies:
    2: [1]      # Deploy to staging after build
    3: [2]      # Test after staging deploy
    4: [3]      # Deploy to prod after tests pass
    5: [4]      # Monitor after prod deploy

prompt:
  template: |
    {% if stage == 1 %}
    Build the release candidate.
    {% elif stage == 2 %}
    Deploy to staging environment.
    {% elif stage == 3 %}
    Run integration tests against staging.
    {% elif stage == 4 %}
    Deploy to production environment.
    {% elif stage == 5 %}
    Monitor production metrics for 1 hour.
    {% endif %}
```

```bash
# Execute up to staging deployment
mzt run rollout-pipeline.yaml

# After stage 2 completes, pause for manual staging verification
mzt pause rollout-pipeline

# Verify staging looks good
curl https://staging.example.com/health

# Resume to run tests
mzt resume rollout-pipeline

# After stage 3 (tests pass), pause again for go/no-go decision
mzt pause rollout-pipeline

# Manual approval to proceed to production
mzt resume rollout-pipeline
```

Each pause point is a **manual quality gate** where a human makes a go/no-go decision.

### Pattern: Parallel Development with Convergence

Run multiple experimental approaches in parallel, then manually select the best one:

```yaml
# parallel-experiments.yaml
sheet:
  size: 1
  total_items: 2
  fan_out:
    1: 3        # 3 parallel approaches
  dependencies:
    2: [1]      # Evaluation depends on all approaches

parallel:
  enabled: true
  max_concurrent: 3

prompt:
  variables:
    approaches:
      1: "Use dynamic programming"
      2: "Use greedy heuristic"
      3: "Use branch-and-bound"
  template: |
    {% if stage == 1 %}
    Implement the algorithm using: {{ approaches[instance] }}
    Save to {{ workspace }}/approach{{ instance }}.py
    {% elif stage == 2 %}
    Evaluate all three implementations and recommend the best.
    {% endif %}
```

```bash
# Run all three approaches in parallel
mzt run parallel-experiments.yaml

# After all 3 complete, pause before evaluation
mzt pause parallel-experiments

# Manually test each approach
python workspaces/parallel-experiments/approach1.py
python workspaces/parallel-experiments/approach2.py
python workspaces/parallel-experiments/approach3.py

# Select the best approach and resume for final evaluation
mzt resume parallel-experiments
```

The conductor executes all 3 instances concurrently (up to `max_concurrent`), then pauses before the evaluation sheet. You inspect real outputs before the AI synthesizes them.

### Pattern: Continuous Monitoring Loop

Use concert chaining to create a perpetual monitoring loop:

```yaml
# monitor-loop.yaml
name: monitor-loop

sheet:
  size: 1
  total_items: 1

prompt:
  template: |
    Check system health:
    - API response times
    - Error rates
    - Database connections

    If any metric exceeds thresholds, alert and suggest fixes.

on_success:
  - type: run_job
    job_path: "monitor-loop.yaml"
    detached: true
    fresh: true

concert:
  enabled: true
  max_chain_depth: 1000   # Run for ~1000 iterations
  cooldown_between_jobs_seconds: 300   # 5-minute intervals
```

```bash
# Start the monitoring loop
mzt run monitor-loop.yaml

# It will run every 5 minutes until stopped manually
mzt list    # Check current iteration

# Stop after observing several cycles
mzt pause monitor-loop
```

The `on_success` hook chains to a fresh instance of the same score. The `cooldown_between_jobs_seconds` setting enforces a delay between iterations.

## Troubleshooting

### "Conductor not running"

The most common error. The conductor must be started before submitting jobs:

```bash
mzt conductor-status   # Check if running
mzt start              # Start if not running
```

If the conductor is running but unreachable, check socket permissions:

```bash
ls -l /tmp/marianne.sock
# Expected: srw-rw---- (0660 permissions)

# If missing or wrong permissions:
mzt stop
mzt start
```

### "Cannot pause: job not running"

You can only pause jobs in the `running` state. Check current state:

```bash
mzt status my-job
```

Possible states:
- `queued`: Not started yet (can cancel, not pause)
- `running`: Actively executing (can pause)
- `paused`: Already paused
- `completed`: Finished (can't pause)
- `failed`: Encountered errors (use `mzt recover` or `mzt resume`)

### Resume doesn't pick up workspace changes

After manually editing workspace files, ensure you're resuming the right job:

```bash
# Verify workspace path matches
mzt status my-job
# Look for: Workspace: /path/to/workspaces/my-job

# Confirm you edited files in that exact workspace
ls -l /path/to/workspaces/my-job/

# Resume
mzt resume my-job
```

If the workspace path doesn't match, the score config's `workspace:` field may be pointing to a different directory.

### Concurrent jobs hitting rate limits

When multiple jobs run concurrently and share the same instrument, they may hit rate limits simultaneously. The conductor detects this and applies backoff:

```bash
# Check which jobs are rate-limited
mzt list
# Look for "waiting (rate limit)" status

# Option 1: Clear all rate limits to retry immediately
mzt clear-rate-limits

# Option 2: Reduce concurrent job count to avoid future collisions
vim ~/.marianne/conductor.yaml
# Set: max_concurrent_jobs: 5 (lower than current)
kill -SIGHUP $(cat /tmp/marianne.pid)   # Reload config
```

The baton execution engine (default since Phase 2) includes **rate limit auto-resume** — when a rate limit expires, the conductor automatically clears it and resumes waiting sheets. If auto-resume doesn't trigger, manual clearing via `mzt clear-rate-limits` forces an immediate retry.

### Stale job showing as "running" after crash

If the conductor crashes while a job is active, orphan recovery marks it `failed` on the next startup:

```bash
# Check status after conductor restart
mzt status my-job
# Output: Status: failed
# Reason: Conductor restarted while job was active

# Resume from last checkpoint
mzt resume my-job
```

If orphan recovery didn't trigger (rare), manually mark the job failed and resume:

```bash
# Force resume (skips state check)
mzt resume my-job --force
```

### Config changes not taking effect

Config changes require a conductor restart or SIGHUP reload:

```bash
# Method 1: Restart (required for socket path changes)
mzt stop
mzt start

# Method 2: Hot reload (for reloadable fields only)
vim ~/.marianne/conductor.yaml
kill -SIGHUP $(cat /tmp/marianne.pid)
mzt conductor-status   # Verify reload succeeded

# Verify new config active
mzt config show
```

Reloadable fields: `max_concurrent_jobs`, `resource_limits.*`, `log_level`, timeouts.
Non-reloadable fields: `socket.*`, `pid_file` (require restart).

## Next Steps

**Deepen your understanding:**
- [Daemon Guide](daemon-guide.md) — Conductor architecture, IPC protocol, and systemd integration
- [Score Writing Guide](score-writing-guide.md) — Concert chaining, `on_success` hooks, and self-chaining patterns
- [CLI Reference](cli-reference.md) — All conductor-related commands and flags

**Explore advanced patterns:**
- [Examples](../examples/) — 43 real-world scores demonstrating conductor patterns
- [Marianne Score Playspace](https://github.com/Mzzkc/marianne-score-playspace) — Creative scores with concert chaining

**Operational guides:**
- [Configuration Reference](configuration-reference.md) — All conductor config fields documented
- [Troubleshooting Guide](troubleshooting-guide.md) — Common issues and solutions (when available)
