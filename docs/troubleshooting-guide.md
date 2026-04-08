# Troubleshooting Guide

Marianne is designed to be resilient and self-healing, but complex multi-stage AI workflows can fail in subtle ways. This guide teaches you how to diagnose problems systematically, interpret diagnostic output, and recover from failures.

**Philosophy:** Marianne provides diagnostic tools that tell you *what went wrong* and *where*. Use them before guessing at solutions. The diagnostic output is designed to be read by both humans and AI agents — it captures the full failure context so you can make informed decisions about how to proceed.

---

## Table of Contents

- [The Debugging Protocol](#the-debugging-protocol)
- [Using Marianne's Diagnostic Tools](#using-mariannes-diagnostic-tools)
- [Understanding Error Codes](#understanding-error-codes)
- [Common Failure Patterns](#common-failure-patterns)
- [Score Validation Errors](#score-validation-errors)
- [Runtime Execution Failures](#runtime-execution-failures)
- [Conductor Issues](#conductor-issues)
- [State Corruption and Recovery](#state-corruption-and-recovery)
- [Performance Issues](#performance-issues)
- [Advanced Debugging](#advanced-debugging)

---

## The Debugging Protocol

When a Marianne score fails, follow this systematic approach. Each step narrows the problem:

```bash
# 1. What happened?
mzt status my-job -w workspaces/my-workspace

# 2. Full diagnostic report
mzt diagnose my-job -w workspaces/my-workspace

# 3. Error history with details
mzt errors my-job --verbose

# 4. Check logs
mzt logs my-job --level ERROR

# 5. Try recovery (re-validate without re-executing)
mzt recover my-job --dry-run
```

Do not skip steps. `mzt status` gives you the high-level picture. `mzt diagnose` gives you everything — preflight warnings, prompt metrics, execution timeline, errors with full context, and log file locations. `mzt errors` lets you filter by type, sheet, or error code. `mzt logs` shows the raw log trail. `mzt recover` checks whether the work was actually done despite the error.

**Only after** you've gathered this information should you attempt fixes. The diagnostics tell you what failed; your job is to understand *why* before changing anything.

---

## Using Marianne's Diagnostic Tools

### `mzt status` — Quick Overview

The fastest way to check a score's current state:

```bash
# Basic status
mzt status my-score

# With workspace path
mzt status my-score -w workspaces/my-workspace

# Watch mode (updates every 2 seconds)
mzt status my-score -w workspaces/my-workspace --watch
```

**What it shows:**
- Overall job state (RUNNING, PAUSED, FAILED, COMPLETED)
- Current sheet number and progress
- Last validation results
- Time elapsed
- ETA for completion (when running)

**When to use it:** Quick health checks, monitoring progress, confirming a job is actually running.

### `mzt diagnose` — Full Diagnostic Report

The deep investigation tool. Always run this when a score fails:

```bash
mzt diagnose my-score -w workspaces/my-workspace

# With full log content included
mzt diagnose my-score -w workspaces/my-workspace --include-logs
```

**What it shows:**

1. **Job Overview** — Current status, configuration summary
2. **Preflight Warnings** — Issues detected before execution
3. **Prompt Metrics** — Token counts, line counts for each sheet
4. **Execution Timeline** — What happened in each sheet, when, and what the outcome was
5. **Error Details** — Full error context with output tails
6. **Validation Results** — Which validations passed/failed and why
7. **Log File Locations** — Where to find detailed logs

**When to use it:** Any time a score fails, behaves unexpectedly, or produces incorrect output. This is your primary debugging tool.

### `mzt validate` — Pre-Flight Checks

Run this *before* executing a score to catch configuration errors:

```bash
# Basic validation
mzt validate my-score.yaml

# JSON output (for automation)
mzt validate my-score.yaml --json

# With self-healing suggestions
mzt validate my-score.yaml --self-healing
```

**What it checks:**
- YAML syntax correctness
- Pydantic schema validation (all required fields, correct types)
- Jinja2 template syntax (V001)
- File paths (workspace parent exists — V002, template files — V003)
- Regex patterns (V007)
- Undefined template variables (V101 warning)
- Prelude/cadenza file existence (V108 warning for static paths)
- Validation path syntax (single braces `{workspace}`, not double `{{ workspace }}`)

**When to use it:** Every time you modify a score, before committing new scores to version control, as part of CI/CD pipelines.

### `mzt doctor` — Environment Health Check

Checks that your Marianne installation and dependencies are correctly configured:

```bash
mzt doctor
```

**What it checks:**
- Python version compatibility
- Required packages installed
- Conductor daemon support
- Default instrument availability (Claude CLI)
- Workspace permissions
- State directory writability

**When to use it:** After installation, when moving to a new machine, when scores won't start for unclear reasons.

### `mzt recover` — Re-Validate Without Re-Executing

Sometimes a sheet fails (non-zero exit code) but the work was actually done — files were created, content is correct. Use `mzt recover` to re-validate without re-executing:

```bash
# Check which sheets would recover
mzt recover my-job --dry-run

# Recover a specific sheet
mzt recover my-job --sheet 6

# Recover all failed sheets
mzt recover my-job
```

If validations pass, the sheet is marked as complete. This is useful when:
- The instrument returned non-zero but the work was done
- A transient error caused failure after files were created
- A rate limit killed the process after output was written

---

## Understanding Error Codes

Every execution failure gets a structured error code. The code tells you what went wrong and whether Marianne will retry.

### Execution Errors (E0xx)

| Code | Name | Retriable | What It Means |
|------|------|-----------|---------------|
| E001 | EXECUTION_TIMEOUT | Yes | Sheet exceeded `timeout_seconds` |
| E002 | EXECUTION_KILLED | Yes | Process was killed (signal) |
| E003 | EXECUTION_CRASHED | No | Process crashed (segfault, core dump) |
| E004 | EXECUTION_INTERRUPTED | No | Process was interrupted (Ctrl+C) |
| E005 | EXECUTION_OOM | No | Out of memory |
| E006 | EXECUTION_STALE | Yes | Agent went silent (no output for too long) |
| E009 | EXECUTION_UNKNOWN | Yes | Unclassified non-zero exit |

E006 (stale detection) fires when an agent produces no output for longer than `stale_detection.idle_timeout_seconds`. This is different from E001 (timeout) — stale detection catches agents that hang silently, while timeout caps total execution time.

### Rate Limit Errors (E1xx)

| Code | Name | Retry Delay | What It Means |
|------|------|-------------|---------------|
| E101 | RATE_LIMIT_API | 1 hour | API-level rate limit |
| E102 | RATE_LIMIT_CLI | 15 min | CLI-level rate limit |
| E103 | CAPACITY_EXCEEDED | 5 min | Server capacity exceeded |
| E104 | QUOTA_EXHAUSTED | Dynamic | Usage quota exhausted |

Rate limits do not count as failures. Marianne pauses the instrument and waits automatically.

### Validation Errors (E2xx)

| Code | Name | What It Means |
|------|------|---------------|
| E201 | VALIDATION_FILE_MISSING | Expected output file not found |
| E202 | VALIDATION_CONTENT_MISMATCH | File does not contain required content |
| E203 | VALIDATION_COMMAND_FAILED | Validation command exited non-zero |
| E204 | VALIDATION_TIMEOUT | Validation command timed out |

Validation errors are retriable — Marianne re-runs the sheet with a completion prompt that tells the agent what passed and what still needs to be done.

### Configuration Errors (E3xx)

| Code | Name | What It Means |
|------|------|---------------|
| E301 | CONFIG_INVALID | Invalid score YAML |
| E302 | CONFIG_MISSING_FIELD | Required field missing |
| E303 | CONFIG_PATH_NOT_FOUND | Referenced path does not exist |
| E304 | CONFIG_PARSE_ERROR | YAML syntax error |

Configuration errors are never retriable — they require a fix to the score YAML.

### Backend/Instrument Errors (E5xx)

| Code | Name | Retriable | What It Means |
|------|------|-----------|---------------|
| E501 | BACKEND_CONNECTION | Yes | Cannot connect to instrument |
| E502 | BACKEND_AUTH | No | Authentication failed |
| E503 | BACKEND_RESPONSE | Yes | Invalid response from instrument |
| E504 | BACKEND_TIMEOUT | Yes | Instrument did not respond in time |
| E505 | BACKEND_NOT_FOUND | No | Instrument binary not found |

---

## Common Failure Patterns

### Pattern 1: "Conductor Not Running"

**Symptom:**
```
Error: Conductor not running. Start with: mzt start
```

**Cause:** You tried to run `mzt run` without the conductor daemon.

**Fix:**
```bash
mzt start
mzt conductor-status  # Verify it's running
mzt run my-score.yaml
```

**Why this happens:** Marianne's execution model requires the conductor for job management, state coordination, and rate limit handling. Only `mzt validate` and `mzt run --dry-run` work without the conductor.

See [Daemon Guide](daemon-guide.md) for conductor architecture details.

### Pattern 2: Validation Failures on Resume

**Symptom:** Score ran fine initially, but fails validation checks when resumed after interruption.

**Cause:** Leftover files from the previous run pass `file_exists` validations even though the sheet didn't execute this time.

**Fix:** Use more robust validation types:

```yaml
# Weak (only checks existence)
validations:
  - type: file_exists
    path: "{workspace}/output.md"

# Better (checks file was modified during this sheet)
validations:
  - type: file_modified
    path: "{workspace}/output.md"

# Best (checks for specific content marker)
validations:
  - type: content_contains
    path: "{workspace}/output.md"
    pattern: "SHEET_{sheet_num}_COMPLETE"
```

**Prevention:** Design your prompts to write completion markers that validations check for:

```yaml
prompt:
  template: |
    Do the work and save to {{ workspace }}/output.md
    End the file with: SHEET_{{ sheet_num }}_COMPLETE
```

### Pattern 3: Template Syntax Errors

**Symptom:**
```
Error: [V001] Jinja2 syntax error in prompt template
  Line 12: unexpected '}'
```

**Cause:** Jinja2 template has syntax errors — mismatched braces, unclosed blocks, undefined filters.

**Common mistakes:**

```yaml
# Wrong: Missing closing brace
{{ workspace }/output.md

# Wrong: Using format string syntax instead of Jinja2
{workspace}/output.md   # This is for validation paths, not prompts

# Wrong: Unclosed block
{% if stage == 1 %}
Do something
# Missing {% endif %}

# Correct:
{{ workspace }}/output.md
{% if stage == 1 %}
Do something
{% endif %}
```

**Fix:** Run `mzt validate` to catch these before execution. The error message includes line numbers.

### Pattern 4: Validation Path Syntax Confusion

**Symptom:** Validations fail with "path not found" even though the file exists.

**Cause:** Mixing Jinja2 syntax (`{{ variable }}`) in validation paths instead of Python format strings (`{variable}`).

**Wrong:**
```yaml
validations:
  - type: file_exists
    path: "{{ workspace }}/output.md"   # Double braces don't work here
```

**Correct:**
```yaml
validations:
  - type: file_exists
    path: "{workspace}/output.md"      # Single braces
```

**Rule:** Prompts use Jinja2 (`{{ }}`), validation paths use Python format strings (`{}`).

### Pattern 5: Workspace Path Issues

**Symptom:**
```
Error: [V002] Workspace parent directory does not exist: /path/to/parent
```

**Cause:** The directory containing your workspace doesn't exist. Marianne creates the workspace directory itself, but not its parent.

**Fix:**

```bash
# Manual fix
mkdir -p /path/to/parent

# Or use auto-fix
mzt validate my-score.yaml --self-healing --yes
```

The `--self-healing` flag detects fixable issues and offers to create missing directories.

### Pattern 6: Rate Limit Deadlock

**Symptom:** Score shows "waiting for rate limit" but the wait time has expired, and it's still stuck.

**Cause:** Stale rate limit state in the learning store — the rate limit was cleared upstream (by the provider) but Marianne's internal state hasn't updated.

**Fix:**

```bash
# Clear all rate limits
mzt clear-rate-limits

# Or clear for a specific instrument
mzt clear-rate-limits --instrument claude-code
```

**Prevention:** Configure appropriate rate limit detection patterns in your score:

```yaml
rate_limit:
  detection_patterns:
    - "rate.?limit"
    - "usage.?limit"
    - "quota"
    - "429"
    - "try again later"
  wait_minutes: 60        # How long to wait
  max_waits: 24           # Give up after 24 hours
```

### Pattern 7: Orphaned Agent Processes

**Symptom:** Conductor was killed (not stopped gracefully), and now sheets are stuck or agents are still running in the background.

**Cause:** The conductor was terminated with `SIGKILL` or via `kill -9` instead of graceful shutdown.

**Fix:**

```bash
# Find orphaned processes
ps aux | grep claude

# Kill them (replace PID with actual process ID)
kill <PID>

# Check conductor state
mzt conductor-status

# Restart conductor cleanly
mzt stop   # Waits for jobs to finish
mzt start
```

**Prevention:** Always stop the conductor gracefully:

```bash
mzt stop          # Waits for jobs, then stops
# NOT: kill -9 <pid>
```

When interrupting a running score, use `mzt pause` instead of killing the conductor:

```bash
mzt pause my-score -w workspaces/my-workspace
```

---

## Score Validation Errors

Validation errors occur when `mzt validate` finds issues in your score YAML. These are caught *before* execution.

### V001: Jinja2 Syntax Error

**Error:**
```
ERROR [V001] Jinja2 syntax error in prompt template
```

**Fix:** Check your `prompt.template` for:
- Mismatched braces `{{ }}`
- Unclosed blocks `{% if %}` without `{% endif %}`
- Invalid filter syntax `{{ x | badfilter }}`
- Undefined variables (warning only, may be intentional)

### V002: Workspace Parent Missing

**Error:**
```
ERROR [V002] Workspace parent directory does not exist
```

**Fix:**
```bash
mkdir -p $(dirname <workspace-path>)
# Or: mzt validate --self-healing --yes
```

### V003: Template File Not Found

**Error:**
```
ERROR [V003] Template file does not exist: path/to/template.j2
```

**Fix:** Check `prompt.template_file` path. Paths are relative to the score YAML file location, not the current directory.

### V007: Invalid Regex Pattern

**Error:**
```
ERROR [V007] Invalid regex pattern in validation
```

**Fix:** Test your regex patterns:

```python
import re
pattern = "your-pattern-here"
re.compile(pattern)  # Should not raise exception
```

Common issues:
- Unescaped special characters: `file.txt` should be `file\\.txt`
- Unclosed groups: `(abc` should be `(abc)`
- Invalid escapes: `\d` is valid, `\z` is not

### V101: Undefined Template Variable (Warning)

**Warning:**
```
INFO [V101] Template uses undefined variable 'x'
```

**Cause:** Your template references `{{ x }}` but `x` is not in `prompt.variables` or core variables.

**Fix:** Add it to `prompt.variables`:

```yaml
prompt:
  variables:
    x: "value"
```

Or, if it's intentional (e.g., defined conditionally in the template), ignore the warning.

### V108: Prelude/Cadenza File Missing (Warning)

**Warning:**
```
INFO [V108] Prelude file not found: /path/to/file.md
```

**Cause:** Static file path in `sheet.prelude` or `sheet.cadenzas` doesn't exist.

**Fix:**
- Check the path
- If using Jinja templating (e.g., `{{ workspace }}/file.md`), this warning is expected — Marianne can't validate dynamic paths until execution

### V201: Jinja Syntax in Validation Path (Warning)

**Warning:**
```
INFO [V201] Validation path uses {{ }} syntax — should be { } (Python format strings)
```

**Fix:** Change validation paths from Jinja2 to Python format strings:

```yaml
# Wrong
validations:
  - type: file_exists
    path: "{{ workspace }}/output.md"

# Correct
validations:
  - type: file_exists
    path: "{workspace}/output.md"
```

### V205: Weak Validations (Info)

**Info:**
```
INFO [V205] All validations are file_exists — stale files will pass
```

**Meaning:** Your validations only check file existence, not content or modification time. Leftover files from previous runs can cause false positives.

**Fix:** Add content or modification checks:

```yaml
validations:
  - type: file_modified
    path: "{workspace}/output.md"
  - type: content_contains
    path: "{workspace}/output.md"
    pattern: "COMPLETE"
```

### V210: Unknown Instrument Name (Warning)

**Warning:**
```
INFO [V210] Unknown instrument 'my-instrument' — not in registry
```

**Cause:** The instrument name doesn't match any profile in `mzt instruments list`.

**Fix:** Check spelling or add a custom instrument profile:

```bash
# List available instruments
mzt instruments list

# Add custom instrument profile
# Create ~/.marianne/instruments/my-instrument.yaml
```

---

## Runtime Execution Failures

These occur during score execution, after validation passes.

### Sheet Timeout

**Symptom:** Sheet fails after running for the configured timeout period.

**Cause:** The instrument didn't complete within `instrument_config.timeout_seconds`.

**Fix:**

```yaml
instrument_config:
  timeout_seconds: 3600        # 1 hour
  timeout_overrides:
    7: 7200                    # Sheet 7 gets 2 hours
```

**Investigation:**
1. Check what the sheet was doing — was it waiting for user input? (Check instrument logs)
2. Is the timeout too short for the task?
3. Is the instrument stuck in a loop?

Use `mzt diagnose` to see the last output from the sheet before timeout.

### Instrument Not Available

**Symptom:**
```
Error: Instrument 'claude-code' not available
```

**Cause:** The configured instrument isn't installed or isn't responding.

**Fix:**

```bash
# Check available instruments
mzt instruments list

# Verify Claude CLI is working
claude --version

# Check conductor logs for instrument errors
tail -f ~/.marianne/marianne.log
```

If the instrument exists but isn't responding, try:
- Restarting the conductor: `mzt stop && mzt start`
- Checking API key environment variables (for API-based instruments)
- Running the instrument directly to see if it works outside Marianne

### Fan-Out Dependency Deadlock

**Symptom:** Parallel execution stalls with some sheets waiting indefinitely.

**Cause:** Circular dependencies in `sheet.dependencies` or incorrect fan-out expansion.

**Investigation:**

```bash
mzt status my-score    # Check which sheets are waiting
mzt diagnose my-score  # See dependency graph
```

**Fix:** Review your dependency declarations. Remember:
- Dependencies are declared at the *stage* level
- Fan-out expands them to sheet-level automatically
- Stage N depending on stage N creates a deadlock
- Fan-in (N sheets → 1 sheet) requires ALL upstream sheets to complete

Example of correct fan-out dependencies:

```yaml
sheet:
  size: 1
  total_items: 3
  fan_out:
    2: 3              # Stage 2 → 3 sheets
  dependencies:
    2: [1]            # All 3 instances depend on stage 1
    3: [2]            # Stage 3 waits for ALL 3 instances (fan-in)
```

### Validation Failure Loop

**Symptom:** Sheet retries repeatedly, validation keeps failing, never succeeds.

**Cause:**
- Validation expects something the prompt didn't ask for
- File path mismatch between prompt and validation
- Instrument isn't following instructions

**Investigation:**

```bash
mzt diagnose my-score
```

Look at:
1. The rendered prompt — does it clearly instruct the file to be created?
2. The validation path — does it match what the prompt says?
3. The instrument output — is it trying to do something else?

**Fix:**

```yaml
# Ensure prompt and validation are aligned
prompt:
  template: |
    Save your output to {{ workspace }}/output.md

validations:
  - type: file_exists
    path: "{workspace}/output.md"   # Must match prompt
```

If the validation is correct but the instrument isn't following instructions, try:
- Adding explicit stakes: `{{ stakes }}`
- Using a more capable instrument for that sheet
- Adding a completion marker the agent must write

### Pipe Exit Code Issues

**Symptom:** `command_succeeds` validation passes when the command actually failed.

**Cause:** Piped commands (`cmd | tail -5`) always return the exit code of the last command in the pipe (e.g., `tail`), not the first command.

**Fix:**

```yaml
# Wrong: Returns exit code of 'tail', not 'pytest'
validations:
  - type: command_succeeds
    command: "pytest tests/ | tail -5"

# Correct: Captures exit code of first command
validations:
  - type: command_succeeds
    command: "bash -c 'pytest tests/ | tail -5; exit ${PIPESTATUS[0]}'"
```

The `${PIPESTATUS[0]}` bash variable contains the exit code of the first command in the pipe.

---

## Conductor Issues

### Conductor Won't Start

**Symptom:**
```
Error starting conductor: Address already in use
```

**Cause:** Another conductor instance is already running, or the IPC socket is stale.

**Fix:**

```bash
# Check if conductor is running
mzt conductor-status

# If running but unresponsive, stop it
mzt stop

# If stop doesn't work, find and kill the process
ps aux | grep "marianne.*conductor"
kill <PID>

# Remove stale socket
rm ~/.marianne/conductor.sock

# Start fresh
mzt start
```

### IPC Communication Failures

**Symptom:**
```
Error: IPC request timed out
```

**Cause:** Conductor is running but not responding to IPC requests.

**Investigation:**

```bash
# Check conductor logs
tail -f ~/.marianne/marianne.log

# Check conductor status
mzt conductor-status

# Look for errors in the log
grep ERROR ~/.marianne/marianne.log
```

**Fix:**
- Restart the conductor: `mzt stop && mzt start`
- Check for resource exhaustion (disk space, memory)
- Check for permission issues on `~/.marianne/`

### Conductor Crashes During Execution

**Symptom:** Conductor stops unexpectedly, jobs are orphaned.

**Investigation:**

```bash
# Check system logs (if using systemd)
journalctl -u mzt --since "1 hour ago"

# Check Python traceback in logs
grep -A 20 "Traceback" ~/.marianne/marianne.log
```

**Common causes:**
- Out of memory (check system resources)
- Unhandled exception in event handler
- Database corruption (SQLite state files)

**Recovery:**

```bash
# Stop everything cleanly
mzt stop

# Check state file integrity
sqlite3 workspaces/my-workspace/.marianne-state.db "PRAGMA integrity_check;"

# If state is corrupted, you may need to use --fresh
mzt run my-score.yaml --fresh   # WARNING: Loses checkpoint state

# Or restore from backup if workspace_lifecycle.archive_on_fresh is enabled
ls workspaces/my-workspace-archives/
```

The conductor marks orphaned jobs as failed on startup. Resume them to continue from the last checkpoint:

```bash
mzt resume my-job
```

---

## State Corruption and Recovery

### Checkpoint State Corruption

**Symptom:** Score won't resume, errors about "invalid checkpoint state" or "unknown sheet status".

**Cause:** Checkpoint state file (JSON or SQLite) was corrupted due to:
- Unclean shutdown (e.g., kill -9)
- Disk full during write
- Concurrent modification (should never happen — file a bug if this occurs)

**Investigation:**

```bash
# For JSON state
cat workspaces/my-workspace/.marianne-state.json | jq .

# For SQLite state
sqlite3 workspaces/my-workspace/.marianne-state.db "SELECT * FROM sheets;"
```

**Recovery:**

1. **If state is partially readable**, try manual repair:
   - Edit JSON state to fix obvious errors
   - Use `sqlite3` to repair SQLite state
   - Resume with `mzt resume`

2. **If state is completely corrupted**, you have two options:

   ```bash
   # Option A: Start fresh (loses progress)
   mzt run my-score.yaml --fresh

   # Option B: Restore from archive (if enabled)
   ls workspaces/my-workspace-archives/
   cp workspaces/my-workspace-archives/2024-01-15_10-30-00/.marianne-state.json \
      workspaces/my-workspace/.marianne-state.json
   mzt resume my-score
   ```

**Prevention:**

```yaml
# Enable workspace archiving for automatic backups
workspace_lifecycle:
  archive_on_fresh: true
  max_archives: 10
```

### Rate Limit State Stuck

**Symptom:** Score shows "waiting for rate limit" but provider has cleared the limit.

**Fix:**

```bash
mzt clear-rate-limits --instrument claude-code
```

This clears the learning store's rate limit state without affecting job progress.

---

## Performance Issues

### Slow Sheet Execution

**Symptom:** Sheets take much longer than expected to complete.

**Investigation:**

1. **Check instrument overhead:**

   ```bash
   # Time a simple command with the instrument
   time claude "Say hello"
   ```

   If this is slow, the issue is with the instrument, not Marianne.

2. **Check MCP loading:**

   MCP server initialization adds ~2 seconds per sheet. Disable if not needed:

   ```yaml
   instrument_config:
     disable_mcp: true
   ```

3. **Check prompt size:**

   Large prompts (spec corpus + prelude + cadenzas) slow rendering and increase token usage. Use `spec_tags` to filter:

   ```yaml
   sheet:
     spec_tags:
       1: [goals, architecture]
       2: [code, style]
   ```

4. **Check validation overhead:**

   `command_succeeds` validations that run expensive commands (e.g., full test suite) slow every sheet. Consider:
   - Staged validations (cheap checks first)
   - Conditional validations (only for certain sheets)
   - Faster validation commands

### High Memory Usage

**Symptom:** Conductor process memory grows over time, eventually OOMs.

**Cause:**
- Large cross-sheet context accumulation
- Event bus not clearing old events
- Learning store growing unbounded

**Investigation:**

```bash
# Check conductor memory
ps aux | grep marianne

# Check learning store size
du -sh ~/.marianne/learning.db

# Check workspace state size
du -sh workspaces/*/
```

**Mitigation:**

```yaml
# Limit cross-sheet lookback
cross_sheet:
  lookback_sheets: 5        # Instead of 0 (unlimited)
  max_output_chars: 2000    # Truncate per sheet
```

For conductor configuration (in `~/.marianne/conductor.yaml`):

```yaml
learning:
  max_execution_history: 1000
  retention_days: 30
```

---

## Advanced Debugging

### Reproducing Failures Locally

To debug a sheet failure locally:

1. **Extract the rendered prompt:**

   ```bash
   mzt diagnose my-score | grep -A 100 "Rendered Prompt"
   ```

2. **Run the instrument manually:**

   ```bash
   # For Claude CLI
   cd workspaces/my-workspace
   claude "$(cat prompt.txt)"
   ```

3. **Compare output to validation expectations**

This lets you iterate quickly without waiting for full score execution.

### Inspecting Learning Store Data

The learning store tracks patterns, errors, and outcomes. Query it for insights:

```bash
# Connect to learning store
sqlite3 ~/.marianne/learning.db

# Recent errors
SELECT timestamp, job_id, sheet_num, error_pattern
FROM executions
WHERE status = 'failed'
ORDER BY timestamp DESC
LIMIT 10;

# Success rate by instrument
SELECT instrument_name,
       COUNT(*) as total,
       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successes
FROM executions
GROUP BY instrument_name;
```

### Debugging Template Rendering

Test Jinja2 templates in isolation:

```python
from jinja2 import Template

template_str = """
{% if stage == 1 %}
Setup
{% elif stage == 2 %}
Instance {{ instance }} of {{ fan_count }}
{% endif %}
"""

template = Template(template_str)
print(template.render(stage=2, instance=1, fan_count=3))
```

This helps debug complex conditionals and loops before running a full score.

### Enabling Debug Logging

For detailed execution traces:

```bash
# Run with verbose logging
mzt --verbose run my-score.yaml

# Or set in environment
export MARIANNE_LOG_LEVEL=DEBUG
mzt run my-score.yaml

# Check logs
tail -f ~/.marianne/marianne.log
```

### Filtering Error History

```bash
# By sheet
mzt errors my-job --sheet 3

# By error type
mzt errors my-job --type transient
mzt errors my-job --type rate_limit
mzt errors my-job --type permanent

# By error code
mzt errors my-job --code E001

# With full stdout/stderr
mzt errors my-job --verbose
```

### Log Analysis

```bash
# Recent logs
mzt logs my-job

# Error-level only
mzt logs my-job --level ERROR

# Follow in real-time
mzt logs --follow

# Specific log file
mzt logs --file ./workspace/logs/marianne.log

# Last 100 lines
mzt logs --lines 100
```

---

## Getting Help

If the debugging protocol does not resolve your issue:

1. Run `mzt diagnose my-job --include-logs --json > diagnostic.json`
2. File a bug with the diagnostic output:
   ```bash
   gh issue create --repo Mzzkc/marianne-ai-compose \
     --title "Short description" \
     --body "## Bug\n\nDiagnostic attached." \
     --label "bug"
   ```

---

## Next Steps

- [CLI Reference](cli-reference.md) — All commands including `diagnose`, `errors`, `recover`
- [Validation Patterns Guide](validation-patterns-guide.md) — Write better validations to catch problems early
- [Score Writing Guide](score-writing-guide.md) — Score structure and best practices
- [Getting Started](getting-started.md) — If you are new to Marianne
- [Daemon Guide](daemon-guide.md) — Conductor architecture and troubleshooting
