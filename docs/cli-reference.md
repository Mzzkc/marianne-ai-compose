# Marianne CLI Reference

Complete reference for all Marianne CLI commands and options. Marianne provides a single entry point:

- **`marianne`** — Job orchestration CLI (run, monitor, diagnose, learn, conductor management)

---

## Global Options

These options apply to all `marianne` commands:

| Option | Short | Description | Env Var |
|--------|-------|-------------|---------|
| `--version` | `-V` | Show version and exit | |
| `--verbose` | `-v` | Show detailed output with additional information | |
| `--quiet` | `-q` | Show minimal output (errors only) | |
| `--conductor-clone` | | Route all daemon interactions to a clone conductor (see below) | |
| `--log-level` | `-L` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `MZT_LOG_LEVEL` |
| `--log-file` | | Path for log file output | `MZT_LOG_FILE` |
| `--log-format` | | Log format: `json`, `console`, or `both` | `MZT_LOG_FORMAT` |
| `--help` | | Show help message | |

### Conductor Clones

The `--conductor-clone` option starts or connects to a **clone conductor** — an
isolated conductor instance with its own socket, PID file, state database, and
log. This lets you test scores, CLI features, and conductor behavior without
risking your production conductor.

```bash
# Start a default clone conductor
marianne --conductor-clone start

# Submit a score to the clone
marianne --conductor-clone run my-score.yaml

# Check clone status
marianne --conductor-clone status

# Named clones for parallel testing
marianne --conductor-clone=staging start
marianne --conductor-clone=staging run staging-test.yaml
marianne --conductor-clone=staging conductor-status

# Stop the clone when done
marianne --conductor-clone stop
```

**Key behaviors:**
- The clone inherits your production `~/.marianne/conductor.yaml` config unless overridden.
- Clone paths: `/tmp/marianne-clone.sock` (socket), `/tmp/marianne-clone.pid` (PID file).
- Named clones use the name in the path: `/tmp/marianne-clone-staging.sock`.
- Clone names are sanitized (64 character limit, safe characters only).
- Commands that don't interact with the conductor (`validate`, `--help`) ignore this flag.

---

## Core Commands

### `mzt run`

Run a score from a YAML configuration file.

```
Usage: mzt run [OPTIONS] CONFIG_FILE
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `CONFIG_FILE` | Yes | Path to YAML score configuration file |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--dry-run` | `-n` | false | Show what would be executed without running |
| `--start-sheet` | `-s` | | Override starting sheet number |
| `--workspace` | `-w` | | Override workspace directory (creates if missing; takes precedence over YAML config) |
| `--json` | `-j` | false | Output result as JSON for machine parsing |
| `--escalation` | `-e` | false | Enable human-in-the-loop escalation — **not currently supported** (blocked in conductor mode) |
| `--self-healing` | `-H` | false | Enable automatic diagnosis and remediation when retries are exhausted |
| `--yes` | `-y` | false | Auto-confirm suggested fixes when using `--self-healing` |
| `--fresh` | | false | Delete existing state before running, ensuring a fresh start. Use for self-chaining scores or re-running completed scores from scratch |

#### Auto-Fresh Detection

When you re-run a completed score after editing the YAML file, Marianne
automatically detects the change and starts fresh — no `--fresh` flag needed.
The conductor compares the score file's modification time against the
previous run's completion time. If the score is newer, it starts a fresh
run automatically.

This only applies to **completed** scores. Failed or paused scores are
always resumed from their checkpoint, regardless of file changes.

#### Backpressure and Rate Limits

The conductor rejects new score submissions only when the system is under
**resource pressure** (high memory usage or too many child processes). Rate
limits do not cause job rejection — they are per-instrument concerns handled
at the sheet dispatch level by the baton. A rate limit on one instrument
does not block scores targeting different instruments.

If the system is under resource pressure, `mzt run` exits with an error:

```
Error: System under high pressure — try again later.
```

#### Examples

```bash
# Basic run (requires running conductor: mzt start)
mzt run job.yaml

# Dry run to preview (works without conductor)
mzt run job.yaml --dry-run

# Custom workspace
mzt run job.yaml --workspace ./output

# Start from sheet 3
mzt run job.yaml --start-sheet 3

# With self-healing enabled
mzt run job.yaml --self-healing --yes

# Fresh start (ignores existing state)
mzt run job.yaml --fresh
```

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success or graceful shutdown |
| 1 | Configuration error or score failure |

---

### `mzt resume`

Resume a paused or failed score.

```
Usage: mzt resume [OPTIONS] JOB_ID
```

Loads the score state and continues execution from where it left off. By default, Marianne auto-reloads the config from the original YAML file if it still exists on disk. Falls back to the cached `config_snapshot` when the file is gone. Use `--no-reload` to force using the cached snapshot.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Score ID to resume |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | | Path to config file (optional if `config_snapshot` exists in state) |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor for resume |
| `--force` | `-f` | false | Force resume even if score appears completed |
| `--escalation` | `-e` | false | Enable human-in-the-loop escalation — **not currently supported** (blocked in conductor mode) |
| `--no-reload` | | false | Use cached config snapshot instead of auto-reloading from YAML file |
| `--self-healing` | `-H` | false | Enable automatic diagnosis and remediation when retries are exhausted |
| `--yes` | `-y` | false | Auto-confirm suggested fixes when using `--self-healing` |

#### Examples

```bash
# Resume paused score
mzt resume my-job

# Resume with explicit config
mzt resume my-job --config job.yaml

# Resume with explicit config file (overrides auto-reload)
mzt resume my-job --config updated.yaml

# Resume using cached snapshot (skip auto-reload)
mzt resume my-job --no-reload

# Force restart completed score
mzt resume my-job --force
```

#### Resumable States

| Status | Resumable | Notes |
|--------|-----------|-------|
| `paused` | Yes | Continues from last sheet |
| `failed` | Yes | Retries from failed sheet |
| `running` | Yes | Continues from last sheet |
| `completed` | With `--force` | Restarts entire job |
| `pending` | No | Use `run` instead |

---

### `mzt pause`

Pause a running Marianne score gracefully.

```
Usage: mzt pause [OPTIONS] JOB_ID
```

Creates a pause signal that the job detects at the next sheet boundary. The job saves its state and can be resumed with `mzt resume`.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Job ID to pause |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor for pause |
| `--wait` | | false | Wait for job to acknowledge pause signal |
| `--timeout` | `-t` | 60 | Timeout in seconds when using `--wait` |
| `--json` | `-j` | false | Output result as JSON |

#### Examples

```bash
# Pause a running job
mzt pause my-job

# Pause and wait for acknowledgment
mzt pause my-job --wait --timeout 30
```

---

### `mzt modify`

Modify a job's configuration and optionally resume execution.

```
Usage: mzt modify [OPTIONS] JOB_ID
```

Convenience command that combines pause + config validation. If the job is running, it will be paused first. Use `--resume` to immediately resume with the new configuration.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Job ID to modify |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | **required** | New configuration file |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor for modify |
| `--resume` | `-r` | false | Immediately resume with new config after pausing |
| `--wait` | | false | Wait for job to pause before resuming (when `--resume`) |
| `--timeout` | `-t` | 60 | Timeout in seconds for pause acknowledgment |
| `--json` | `-j` | false | Output result as JSON |

#### Examples

```bash
# Modify config (pauses job if running)
mzt modify my-job --config updated.yaml

# Modify and immediately resume
mzt modify my-job -c new-config.yaml --resume

# Modify, wait for pause, then resume
mzt modify my-job -c updated.yaml --resume --wait
```

---

### `mzt status`

Show score status. With no arguments, shows an overview of all active scores. With a score ID, shows detailed status for that specific score.

```
Usage: mzt status [OPTIONS] [SCORE_ID]
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `SCORE_ID` | No | Score ID to check status for. Omit to see all active scores. |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | `-j` | false | Output as JSON for machine parsing |
| `--watch` | `-W` | false | Continuously monitor status with live updates |
| `--interval` | `-i` | 5 | Refresh interval in seconds for `--watch` mode |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor and read job state from filesystem |

> **Note:** `mzt status` routes through the conductor by default. The `--workspace` flag is a hidden debug override for direct filesystem access when the conductor is unavailable.

#### Overview Mode (No Arguments)

When called without a score ID, shows a conductor overview:

- **Conductor status** — whether the conductor is running and its uptime
- **Active scores** — all running, queued, and paused scores with elapsed time
- **Recent scores** — the 5 most recently completed or failed scores

This is the natural first command after `mzt run` — like `git status` showing your working tree.

```bash
# Show overview of all scores
mzt status

# Overview as JSON
mzt status --json
```

#### Per-Score Mode

When given a score ID, shows detailed status for that score:

- Score name, status, and timing
- Progress bar with sheet counts
- Per-sheet details with validation results
- Cost tracking with confidence indicators (see below)
- Error summaries with `mzt diagnose` suggestion on failure

For large scores (50+ sheets), a compact summary is shown instead of the full sheet table: counts by status, then only interesting sheets (running, failed, validation-failed) capped at 20 entries. Small scores retain the full detail table.

```bash
# Detailed status for a specific score
mzt status my-score

# JSON output
mzt status my-score --json

# Continuous monitoring
mzt status my-score --watch

# Watch with custom interval
mzt status my-score --watch --interval 10
```

#### Output

Standard per-score output includes:
- Score name and ID
- Status (running, completed, failed, paused, pending)
- Progress bar with sheet counts
- Timing information
- Error messages (if any)
- Sheet details table (or compact summary for 50+ sheets)
- **Instrument column** — when any sheet has an assigned instrument name, the table includes an Instrument column showing which instrument each sheet uses. For large scores (50+ sheets), the summary view shows an instrument breakdown with counts.
- **Cost summary** — always shown, including total cost, token counts, and cost limit status. When an instrument returns structured token data (JSON output), costs are precise. When tokens are estimated from output character count, the display shows `~$X.XX (est.)` with a warning that actual costs may be 10-100x higher. Use JSON output format on your instrument for accurate cost tracking.
- Suggestion to run `mzt diagnose` on failure

JSON output structure:
```json
{
  "job_id": "my-score",
  "job_name": "My Score",
  "status": "running",
  "progress": {
    "completed": 5,
    "total": 10,
    "percent": 50.0
  },
  "sheets": {
    "1": {"status": "completed", "attempt_count": 1},
    "2": {"status": "in_progress", "attempt_count": 1}
  }
}
```

---

### `mzt list`

List scores from the conductor.

```
Usage: mzt list [OPTIONS]
```

By default shows only active jobs (queued, running, paused). Use `--all` to include completed, failed, and cancelled jobs.

> **Note:** This command requires a running Marianne conductor (`mzt start`). Use `mzt status <job-id>` for checking individual jobs.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--all` | `-a` | false | Show all jobs including completed, failed, and cancelled |
| `--status` | `-s` | | Filter by job status: `queued`, `running`, `completed`, `failed`, `paused` |
| `--limit` | `-l` | 20 | Maximum number of jobs to display |
| `--json` | | false | Output as JSON array for machine parsing |

#### Examples

```bash
# List active jobs (default)
mzt list

# List all jobs including completed
mzt list --all

# Filter by status
mzt list --status failed

# JSON output for scripting
mzt list --json

# Limit results
mzt list --limit 10
```

---

### `mzt validate`

Validate a score configuration file.

```
Usage: mzt validate [OPTIONS] CONFIG_FILE
```

Performs comprehensive validation including YAML syntax, Pydantic schema validation, Jinja template syntax checking, path existence verification, regex pattern compilation, and configuration completeness checks.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `CONFIG_FILE` | Yes | Path to YAML score configuration file |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | `-j` | false | Output validation results as JSON |
| `--verbose` | `-v` | false | Show detailed validation output |

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Valid (warnings/info OK) |
| 1 | Invalid (one or more errors) |
| 2 | Cannot validate (file not found, YAML unparseable) |

#### Validation Checks

**Errors** (block execution):

| Code | Description |
|------|-------------|
| V001 | Jinja syntax errors in templates |
| V002 | Workspace parent directory missing |
| V003 | Template file missing |
| V004 | System prompt file missing |
| V005 | Working directory invalid |
| V007 | Invalid regex patterns in validations |
| V008 | Validation rules missing required fields |
| V009 | Evolved score references previous version paths |

**Warnings** (flag potential issues):

| Code | Description |
|------|-------------|
| V101 | Undefined template variables |
| V103 | Very short timeout (< 60s) |
| V106 | Empty pattern in validation rule |
| V107 | Referenced skill files missing |
| V108 | Prelude/cadenza file paths missing |
| V201 | Jinja `{{ }}` syntax in validation paths (should use `{ }`) |
| V202 | Format-string `{var}` syntax in Jinja templates (should use `{{ var }}`) |
| V206 | Fan-out without dependencies defined |
| V208 | User variable shadows a built-in template variable |
| V210 | Instrument name not found in known profiles |
| V211 | Instrument fallback name not found in known profiles |
| V212 | `skip_when`/`skip_when_command` keys reference out-of-range sheets |

**Info** (suggestions):

| Code | Description |
|------|-------------|
| V104 | Very long timeout (> 4h) |
| V203 | No validation rules defined |
| V204 | `skip_permissions` not enabled for Claude CLI |
| V205 | Only `file_exists` validations (weak acceptance criteria) |
| V207 | Fan-out without parallel execution enabled |
| V209 | `disable_mcp` not enabled for Claude CLI |

#### Examples

```bash
# Basic validation
mzt validate job.yaml

# Detailed output
mzt validate job.yaml --verbose

# JSON output for CI/CD
mzt validate job.yaml --json
```

---

### `mzt init`

Scaffold a new Marianne project with a starter score.

```
Usage: mzt init [OPTIONS] [SCORE_NAME]
```

Creates a starter score YAML and `.marianne/` project directory. The generated
score includes comments explaining every field — edit it with your task, then
run it.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `SCORE_NAME` | No | Score name (same as `--name`). Default: `my-score` |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--path` | `-p` | `.` | Directory to initialize |
| `--name` | `-n` | `my-score` | Name for the starter score (overrides positional) |
| `--force` | `-f` | false | Overwrite existing files |
| `--json` | `-j` | false | Output result as JSON |

#### Examples

```bash
# Initialize current directory with default name
mzt init

# Initialize with a custom name (positional — like git init)
mzt init data-pipeline

# Initialize with a custom name (flag)
mzt init --name data-pipeline

# Initialize in a specific directory
mzt init --path ./my-project

# Machine-readable output
mzt init --json
```

#### What It Creates

```
./
├── my-score.yaml        # Starter score — edit with your task
└── .marianne/             # Project configuration directory
```

**Next steps** after init: edit the score YAML, then `mzt start && mzt run my-score.yaml`.

---

### `mzt cancel`

Cancel a running score immediately.

```
Usage: mzt cancel [OPTIONS] SCORE_ID
```

Unlike `pause`, this does not wait for a sheet boundary. The score's task is
cancelled, in-progress work is rolled back, and the score is marked as
CANCELLED.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `SCORE_ID` | Yes | Score to cancel |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | `-j` | false | Output result as JSON |

#### Examples

```bash
# Cancel a running score
mzt cancel my-job

# Cancel with JSON output
mzt cancel my-job --json
```

**Tip:** Use `mzt pause` for graceful stops that wait for the current sheet to finish. Use `cancel` when the score must stop now.

---

### `mzt clear`

Clear terminal scores from the conductor registry.

```
Usage: mzt clear [OPTIONS]
```

Removes completed, failed, and/or cancelled scores from the conductor's
tracking. Running and queued scores are never cleared.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--job` | `-j` | | Specific score ID(s) to clear. Can be repeated. |
| `--status` | `-s` | all terminal | Status(es) to clear: `failed`, `completed`, `cancelled`. Can be repeated. |
| `--older-than` | | | Only clear scores older than this many seconds |
| `--yes` | `-y` | false | Skip confirmation prompt |

#### Examples

```bash
# Clear all terminal scores
mzt clear

# Clear a specific score
mzt clear --job conductor-fix

# Clear only failed scores
mzt clear --status failed

# Clear failed + cancelled scores
mzt clear --status failed -s cancelled

# Clear scores older than 1 hour, skip confirmation
mzt clear --older-than 3600 -y
```

---

### `mzt logs`

Show or tail log files for a job.

```
Usage: mzt logs [OPTIONS] [JOB_ID]
```

Displays log entries from Marianne log files. Supports both current log files and compressed rotated logs (`.gz`).

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | No | Job ID to filter logs for (shows all if not specified) |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--workspace` | `-w` | `.` | *(hidden)* Debug override: specify workspace for log file lookup |
| `--file` | `-f` | | Specific log file path (overrides workspace default) |
| `--follow` | `-F` | false | Follow the log file for new entries (like `tail -f`) |
| `--lines` | `-n` | 50 | Number of lines to show (0 for all) |
| `--level` | `-l` | | Filter by minimum log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--json` | `-j` | false | Output raw JSON log entries |

#### Examples

```bash
# Show recent logs
mzt logs

# Filter by job
mzt logs my-job

# Follow logs in real-time
mzt logs --follow

# Show last 100 lines of errors only
mzt logs --lines 100 --level ERROR

# Use specific log file
mzt logs --file ./workspace/logs/marianne.log
```

---

### `mzt errors`

List all errors for a job with detailed information.

```
Usage: mzt errors [OPTIONS] JOB_ID
```

Displays errors grouped by sheet, with color-coding by error type:
- **Red:** Permanent errors (non-retriable, fatal)
- **Yellow:** Transient errors (retriable with backoff)
- **Blue:** Rate limit errors (retriable after wait)

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Job ID to show errors for |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--sheet` | `-b` | | Filter errors by specific sheet number |
| `--type` | `-t` | | Filter by error type: `transient`, `rate_limit`, or `permanent` |
| `--code` | `-c` | | Filter by error code (e.g., `E001`, `E101`) |
| `--verbose` | `-V` | false | Show full stdout/stderr tails for each error |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor for error retrieval |
| `--json` | `-j` | false | Output errors as JSON |

#### Examples

```bash
# Show all errors
mzt errors my-job

# Errors for specific sheet
mzt errors my-job --sheet 3

# Only transient errors
mzt errors my-job --type transient

# Filter by error code
mzt errors my-job --code E001

# Verbose with stdout/stderr details
mzt errors my-job --verbose
```

---

### `mzt diagnose`

Generate a comprehensive diagnostic report for a job.

```
Usage: mzt diagnose [OPTIONS] JOB_ID
```

The diagnostic report includes:
- Job overview and current status
- Preflight warnings from all sheets
- Prompt metrics (token counts, line counts)
- Execution timeline with timing information
- All errors with full context and output tails
- Log file locations, sizes, and modification times
- (with `--include-logs`) Inline log content from each log file

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Job ID to diagnose |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--workspace` | `-w` | | Workspace directory. When the conductor doesn't recognize a score ID, Marianne falls back to reading state directly from this workspace directory. Useful for diagnosing scores from stopped conductors or clone conductors. |
| `--json` | `-j` | false | Output diagnostic report as JSON |
| `--include-logs` | | false | Inline the last 50 lines from each sheet/hook log file |

#### Examples

```bash
# Full diagnostic report
mzt diagnose my-job

# Machine-readable
mzt diagnose my-job --json

# Include inline log content
mzt diagnose my-job --include-logs

# Diagnose from workspace (when conductor doesn't know the score)
mzt diagnose my-job -w ./workspaces/my-job
```

---

### `mzt doctor`

Check Marianne environment health.

```
Usage: mzt doctor [OPTIONS]
```

Validates that your environment is ready to run Marianne scores. Checks Python version, Marianne installation, conductor status, available instruments, and safety configuration. This command works without a running conductor — it is designed to be the first thing you run after installation.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | | false | Output results as JSON |

#### Checks Performed

| Check | What It Verifies |
|-------|-----------------|
| Python | Version 3.11+ installed |
| Marianne | Marianne package installed, version displayed |
| Conductor | Whether the conductor daemon is running (PID file + process check) |
| Instruments | Each registered instrument's binary availability on PATH |
| Safety | Whether cost limits are configured |

For instruments, CLI instruments are checked by looking for the executable on PATH (via `shutil.which`). HTTP instruments are reported as available without a connectivity probe — they are assumed reachable if configured.

#### Examples

```bash
# Check environment
mzt doctor

# JSON output for scripting
mzt doctor --json
```

#### Sample Output

```
Marianne Doctor

  ✓ Python 3.12                   installed
  ✓ Marianne v1.0.0                 installed
  ✓ Conductor                     running (pid 12345)

  Instruments:
  ✓ claude-code                   /usr/local/bin/claude
  · gemini-cli                    not found (gemini)
  · codex-cli                     not found (codex)

  Safety:
  ⚠ No cost limits configured     Recommend: cost_limits.max_cost_per_job

1 warning. Marianne is ready.
```

---

### `mzt history`

Show execution history for a job.

```
Usage: mzt history [OPTIONS] JOB_ID
```

Displays a table of past execution attempts from the SQLite state backend, including sheet number, attempt number, exit code, duration, and timestamp.

> **Note:** Requires the SQLite state backend. Execution history is not available with the JSON backend.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Job ID to show execution history for |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--sheet` | `-b` | | Filter by specific sheet number |
| `--limit` | `-n` | 50 | Maximum number of records to show |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor for history |
| `--json` | `-j` | false | Output history as JSON |

#### Examples

```bash
# Show all history
mzt history my-job

# History for specific sheet
mzt history my-job --sheet 3

# Show more records
mzt history my-job --limit 100

# JSON output
mzt history my-job --json
```

---

### `mzt recover`

Recover sheets that completed work but were incorrectly marked as failed.

```
Usage: mzt recover [OPTIONS] JOB_ID
```

Runs validations for failed sheets without re-executing them. If validations pass, the sheet is marked as complete. This is useful when:
- Claude CLI returned a non-zero exit code but the work was done
- A transient error caused failure after files were created
- You want to check if a failed sheet actually succeeded

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Job ID to recover |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--sheet` | `-s` | | Specific sheet number to recover (default: all failed sheets) |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor for recovery |
| `--dry-run` | `-n` | false | Check validations without modifying state |

#### Examples

```bash
# Recover all failed sheets
mzt recover my-job

# Recover specific sheet
mzt recover my-job --sheet 6

# Check without modifying (dry run)
mzt recover my-job --dry-run
```

---

### `mzt dashboard`

Start the web dashboard.

```
Usage: mzt dashboard [OPTIONS]
```

Launches the Marianne dashboard API server for job monitoring and control.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--port` | `-p` | 8000 | Port to run dashboard on |
| `--host` | | `127.0.0.1` | Host to bind to |
| `--workspace` | `-w` | `.` | Workspace directory for job state |
| `--reload` | `-r` | false | Enable auto-reload for development |

#### Examples

```bash
# Start with defaults (localhost:8000)
mzt dashboard

# Custom port
mzt dashboard --port 3000

# Allow external connections
mzt dashboard --host 0.0.0.0

# Development mode with auto-reload
mzt dashboard --reload
```

#### Dashboard API Endpoints

##### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service health check (status, version) |

##### Job Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs` | List all jobs with optional status filter and pagination |
| GET | `/api/jobs/{id}` | Get detailed information about a specific job |
| GET | `/api/jobs/{id}/status` | Get focused status (lightweight polling endpoint) |
| POST | `/api/jobs` | Start a new job (inline config or file path) |
| POST | `/api/jobs/{id}/pause` | Pause a running job |
| POST | `/api/jobs/{id}/resume` | Resume a paused job |
| POST | `/api/jobs/{id}/cancel` | Cancel a running job |
| DELETE | `/api/jobs/{id}` | Delete a job record |

##### Sheet Details

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs/{id}/sheets/{num}` | Sheet-level status, logs, validation, costs, tokens |

##### Streaming & Logs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs/{id}/stream` | Real-time job status updates via SSE |
| GET | `/api/jobs/{id}/logs` | Stream job logs via SSE with follow mode |
| GET | `/api/jobs/{id}/logs/static` | Download complete log file as plain text |
| GET | `/api/jobs/{id}/logs/info` | Log file metadata (size, lines, modified time) |

##### Artifacts & Workspace

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs/{id}/artifacts` | List workspace files (recursive, with filtering) |
| GET | `/api/jobs/{id}/artifacts/{path}` | Read a specific workspace file |

##### Configuration & Templates

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scores/validate` | Validate YAML config (syntax, schema, extended checks) |
| GET | `/api/templates/list` | List available templates (category, complexity, search filtering) |
| GET | `/api/templates/{name}` | Get template details |
| GET | `/api/templates/{name}/download` | Download template as YAML file |
| POST | `/api/templates/{name}/use` | Use a template (redirect to editor) |

##### Daemon

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs/daemon/status` | Check daemon status (PID, uptime, running jobs, memory) |

---

### `mzt mcp`

Start the Marianne MCP (Model Context Protocol) server.

```
Usage: mzt mcp [OPTIONS]
```

Launches an MCP server that exposes Marianne's job management capabilities as tools for external AI agents. Provides job management tools, artifact browsing, log streaming, and configuration access.

When the Marianne conductor is running, the MCP server routes operations through it for coordinated execution.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--port` | `-p` | 8001 | Port to run MCP server on |
| `--host` | | `127.0.0.1` | Host to bind to |
| `--workspace` | `-w` | `.` | Workspace directory for job operations |

#### Examples

```bash
# Start on default port
mzt mcp

# Custom port
mzt mcp --port 8002

# Specific workspace
mzt mcp --workspace ./projects
```

See [MCP Integration Guide](MCP-INTEGRATION.md) for Claude Desktop setup and available tools.

---

### `mzt top`

Real-time system monitor — like htop for your conductor.

```
Usage: mzt top [OPTIONS]
```

Shows a job-centric process tree, resource metrics, event timeline, anomaly
detection, and learning insights. Four operating modes:

| Mode | Flag | Description |
|------|------|-------------|
| TUI | *(default)* | Rich terminal UI with live updates |
| JSON | `--json` | Stream NDJSON snapshots to stdout |
| History | `--history 1h` | Replay historical data from profiler DB |
| Trace | `--trace PID` | Attach strace to a specific process |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | | false | Stream JSON snapshots (NDJSON format) |
| `--history` | | | Replay historical data (e.g., `1h`, `30m`, `2h30m`) |
| `--trace` | | | Attach strace to a specific PID |
| `--job` | `-j` | | Filter by score ID |
| `--interval` | `-i` | `2.0` | Refresh interval in seconds |

#### Examples

```bash
# Live TUI monitor
mzt top

# Filter to a specific score
mzt top --job my-pipeline

# JSON output for scripting
mzt top --json

# Replay the last hour of data
mzt top --history 1h

# Faster refresh rate
mzt top --interval 0.5
```

---

### `mzt config`

Manage conductor configuration.

```
Usage: mzt config COMMAND [OPTIONS]
```

#### Subcommands

##### `mzt config show`

Display current daemon configuration as a table. When the conductor is running, displays the **live in-memory config** (reflecting any SIGHUP reloads) with `[live]` source indicators. Falls back to disk-based display when the conductor is not running.

```bash
mzt config show
mzt config show --config /etc/marianne/daemon.yaml
```

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to daemon config file (default: `~/.marianne/daemon.yaml`). Ignored when live config is available from a running conductor. |

##### `mzt config set`

Update a conductor configuration value. Values are validated against the DaemonConfig schema before saving. Use dot notation for nested keys.

```bash
mzt config set max_concurrent_jobs 10
mzt config set socket.path /tmp/custom.sock
mzt config set resource_limits.max_memory_mb 4096
mzt config set log_level debug
```

| Argument | Required | Description |
|----------|----------|-------------|
| `KEY` | Yes | Config key in dot notation (e.g., `socket.path`, `max_concurrent_jobs`) |
| `VALUE` | Yes | New value to set |

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to daemon config file (default: `~/.marianne/daemon.yaml`) |

##### `mzt config path`

Show the conductor config file location and whether it exists.

```bash
mzt config path
```

##### `mzt config init`

Create a default conductor config file with all default values and descriptive comments. Refuses to overwrite unless `--force` is given.

```bash
mzt config init
mzt config init --force
mzt config init --config /etc/marianne/daemon.yaml
```

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to create config file (default: `~/.marianne/daemon.yaml`) |
| `--force` | `-f` | Overwrite existing config file |

##### `mzt config check`

Validate a daemon config file against the `DaemonConfig` schema without starting the conductor. Exits 0 if valid, 1 if invalid or the file cannot be loaded.

```bash
mzt config check
mzt config check --config /path/to/custom.yaml
```

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to daemon config file to validate (default: `~/.marianne/daemon.yaml`) |

---

## Instrument Commands

These commands manage and inspect available instruments — the AI tools Marianne can use to execute scores. Instruments include CLI tools (Claude Code, Gemini CLI, Codex CLI, Aider, Goose) and HTTP APIs (Anthropic API, Ollama).

### `mzt instruments list`

List all available instruments and their readiness status.

```
Usage: mzt instruments list [OPTIONS]
```

Shows every registered instrument: native backends (built into Marianne), built-in profiles (shipped as YAML), organization profiles (`~/.marianne/instruments/`), and venue profiles (`.marianne/instruments/`). Later profiles override earlier ones on name collision.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | | false | Output as JSON |

#### Examples

```bash
# List all instruments
mzt instruments list

# JSON output
mzt instruments list --json
```

#### Sample Output

```
                    Instruments
  NAME              KIND    STATUS          DEFAULT MODEL
  aider             cli     ✗ not found     (instrument default)
  anthropic_api     http    http            claude-sonnet-4-5-20250929
  claude-code       cli     ✓ ready         (instrument default)
  claude_cli        cli     ✓ ready         (instrument default)
  gemini-cli        cli     ✓ ready         gemini-2.5-pro
  ollama            http    http            llama3.1:8b

10 instruments configured (6 ready)
```

**Status values:**
- `✓ ready` — CLI binary found on PATH
- `✗ not found` — CLI binary not on PATH (install the tool to use it)
- `http` — HTTP instrument (connectivity not probed; assumed reachable if configured)

---

### `mzt instruments check`

Check readiness and configuration of a specific instrument.

```
Usage: mzt instruments check [OPTIONS] NAME
```

Provides detailed information about a single instrument: binary location, capabilities, available models with pricing, and overall readiness.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `NAME` | Yes | Instrument name to check (as shown in `instruments list`) |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | | false | Output as JSON |

#### Examples

```bash
# Check a specific instrument
mzt instruments check gemini-cli

# JSON output
mzt instruments check claude-code --json
```

#### Sample Output

```
Checking gemini-cli...
  Display name:  Gemini CLI
  Description:   Google's Gemini CLI with tool use and vision
  Kind:          cli
  Binary:        /usr/local/bin/gemini ✓
  Capabilities:  file_editing, shell_access, structured_output, tool_use, vision
  Default model: gemini-2.5-pro
    gemini-2.5-pro: 1,000,000 ctx ($0.0013/1K in, $0.0050/1K out)
    gemini-2.5-flash: 1,000,000 ctx ($0.0002/1K in, $0.0006/1K out)

gemini-cli is ready.
```

---

## Learning Commands

These commands inspect and analyze Marianne's learning system — patterns learned from job executions that inform future runs.

### `mzt patterns-list`

View global learning patterns.

```
Usage: mzt patterns-list [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--global` / `--local` | `-g` / `-l` | global | Show global or local workspace patterns |
| `--min-priority` | `-p` | 0.0 | Minimum priority score (0.0–1.0) |
| `--limit` | `-n` | 20 | Maximum patterns to display |
| `--json` | `-j` | false | Output as JSON |
| `--quarantined` | `-q` | false | Show only quarantined patterns |
| `--high-trust` | | false | Show only patterns with trust >= 0.7 |
| `--low-trust` | | false | Show only patterns with trust <= 0.3 |

```bash
mzt patterns-list
mzt patterns-list --high-trust
mzt patterns-list --quarantined
mzt patterns-list --min-priority 0.5 --json
```

---

### `mzt patterns-why`

Analyze WHY patterns succeed with metacognitive insights. Shows success factors — the context conditions that contribute to pattern effectiveness.

```
Usage: mzt patterns-why [OPTIONS] [PATTERN_ID]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `PATTERN_ID` | No | Pattern ID to analyze (first 10 chars). If omitted, shows all patterns with success factors |

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--min-obs` | `-m` | 1 | Minimum success factor observations required |
| `--limit` | `-n` | 10 | Maximum patterns to display |
| `--json` | `-j` | false | Output as JSON |

```bash
mzt patterns-why
mzt patterns-why abc123
mzt patterns-why --min-obs 3
```

---

### `mzt patterns-entropy`

Monitor pattern population diversity using Shannon entropy.

```
Usage: mzt patterns-entropy [OPTIONS]
```

Shannon entropy measures how evenly patterns are used:
- **High entropy (H → max):** Healthy diversity, many patterns contribute
- **Low entropy (H → 0):** Single pattern dominates (collapse risk)

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--threshold` | `-t` | 0.5 | Diversity index alert threshold (0.0–1.0) |
| `--history` | `-H` | false | Show entropy history over time |
| `--limit` | `-n` | 20 | Number of history records to show |
| `--json` | `-j` | false | Output as JSON |
| `--record` | `-r` | false | Record current entropy to history |

```bash
mzt patterns-entropy
mzt patterns-entropy --threshold 0.3
mzt patterns-entropy --history
mzt patterns-entropy --record
```

---

### `mzt patterns-budget`

Display exploration budget status and history. The budget adjusts based on pattern entropy: low entropy boosts the budget to inject diversity; healthy entropy decays toward floor (default 5%).

```
Usage: mzt patterns-budget [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--job` | `-j` | | Filter by specific job hash |
| `--history` | `-H` | false | Show budget adjustment history |
| `--limit` | `-n` | 20 | Number of history records to show |
| `--json` | | false | Output as JSON |

```bash
mzt patterns-budget
mzt patterns-budget --history
```

---

### `mzt learning-stats`

View global learning statistics. Shows summary including execution counts, pattern counts, and effectiveness metrics.

```
Usage: mzt learning-stats [OPTIONS]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--json` | `-j` | Output as JSON |

```bash
mzt learning-stats
mzt learning-stats --json
```

---

### `mzt learning-insights`

Show actionable insights from learning data including output patterns, error code patterns, and success predictors.

```
Usage: mzt learning-insights [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--limit` | | 10 | Maximum patterns to show |
| `--pattern-type` | | | Filter by type |

```bash
mzt learning-insights
mzt learning-insights --pattern-type output_pattern
mzt learning-insights --limit 20
```

---

### `mzt learning-drift`

Detect patterns with effectiveness drift. Drift is calculated by comparing pattern effectiveness in the last N applications vs the previous N.

```
Usage: mzt learning-drift [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--threshold` | `-t` | 0.2 | Drift threshold (0.0–1.0) to flag patterns |
| `--window` | `-w` | 5 | Window size for drift comparison |
| `--limit` | `-l` | 10 | Maximum patterns to show |
| `--json` | `-j` | false | Output as JSON |
| `--summary` | `-s` | false | Show only summary statistics |

```bash
mzt learning-drift
mzt learning-drift --threshold 0.15
mzt learning-drift --summary
```

---

### `mzt learning-epistemic-drift`

Detect patterns with epistemic drift (belief/confidence changes). Epistemic drift tracks confidence changes over time, complementing effectiveness drift as a leading indicator of pattern health.

```
Usage: mzt learning-epistemic-drift [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--threshold` | `-t` | 0.15 | Epistemic drift threshold (0.0–1.0) |
| `--window` | `-w` | 5 | Window size for drift comparison |
| `--limit` | `-l` | 10 | Maximum patterns to show |
| `--json` | `-j` | false | Output as JSON |
| `--summary` | `-s` | false | Show only summary statistics |

```bash
mzt learning-epistemic-drift
mzt learning-epistemic-drift --threshold 0.1
mzt learning-epistemic-drift --summary
```

---

### `mzt learning-activity`

View recent learning activity and pattern applications.

```
Usage: mzt learning-activity [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--hours` | `-h` | 24 | Show activity from the last N hours |
| `--json` | `-j` | false | Output as JSON |

```bash
mzt learning-activity
mzt learning-activity --hours 48
```

---

### `marianne entropy-status`

Display entropy response status and history. When pattern entropy drops below threshold, the system automatically boosts exploration budget and revisits quarantined patterns.

```
Usage: marianne entropy-status [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--job` | `-j` | | Filter by specific job hash |
| `--history` | `-H` | false | Show entropy response history |
| `--limit` | `-n` | 20 | Number of history records to show |
| `--json` | | false | Output as JSON |
| `--check` | `-c` | false | Check if entropy response is needed (dry-run) |

```bash
marianne entropy-status
marianne entropy-status --history
marianne entropy-status --check
```

---

## Conductor Commands

### `mzt start`

Start the Marianne conductor.

```
Usage: mzt start [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | | Path to conductor config file |
| `--foreground` | `-f` | false | Run in foreground (for development) |
| `--log-level` | `-l` | `info` | Logging level |
| `--profile` | `-p` | | Daemon operational profile: `dev`, `intensive`, `minimal`. Overrides config file defaults. |

```bash
# Start in background (production)
mzt start

# Start in foreground (development)
mzt start --foreground

# Custom config
mzt start --config /etc/marianne/daemon.yaml

# Debug logging
mzt start --log-level debug
```

---

### `mzt stop`

Stop the running conductor. If jobs are actively running, warns and asks for confirmation before proceeding — stopping the conductor while jobs run orphans active agents and may corrupt job state.

```
Usage: mzt stop [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--pid-file` | | `/tmp/marianne.pid` | Path to PID file |
| `--force` | | false | Skip safety check, send SIGKILL instead of SIGTERM |

**Safety guard:** When jobs are running, `mzt stop` probes the conductor via IPC to check for active jobs. If any are found, it warns with the job count and asks for confirmation. The `--force` flag bypasses this check entirely and sends SIGKILL.

```bash
# Normal stop (warns if jobs running)
mzt stop

# Force stop (SIGKILL, no safety check)
mzt stop --force
```

---

### `mzt restart`

Restart the conductor (stop + start).

```
Usage: mzt restart [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | | Path to conductor config file |
| `--foreground` | `-f` | false | Run in foreground (for development) |
| `--log-level` | `-l` | `info` | Logging level |
| `--pid-file` | | `/tmp/marianne.pid` | Path to PID file |
| `--profile` | `-p` | | Daemon operational profile: `dev`, `intensive`, `minimal`. Overrides config file defaults. |

```bash
mzt restart
mzt restart --foreground
```

---

### `mzt conductor-status`

Check conductor status via health probes.

```
Usage: mzt conductor-status [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--pid-file` | | `/tmp/marianne.pid` | Path to PID file |
| `--socket` | | `/tmp/marianne.sock` | Path to Unix socket |

```bash
mzt conductor-status
```

---

### `mzt clear-rate-limits`

Clear stale rate limits on instruments. When a backend rate limit expires but the conductor still has it cached, sheets may stay blocked unnecessarily. This command clears the cached limit so dispatch resumes immediately.

Clears both the rate limit coordinator (used by the scheduler) and the baton's per-instrument state (used by the dispatch loop).

```
Usage: mzt clear-rate-limits [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--instrument` | `-i` | | Clear rate limit for a specific instrument only |
| `--json` | `-j` | false | Output result as JSON |

```bash
# Clear all rate limits
mzt clear-rate-limits

# Clear rate limit for a specific instrument
mzt clear-rate-limits -i claude-cli

# JSON output for scripting
mzt clear-rate-limits --json
```

**When to use:**
- A rate limit has expired but sheets are still WAITING
- You want to force a retry after a rate limit event
- Troubleshooting rate limit behavior during development

---

## Configuration Reference

Job configurations are defined in YAML files. Here are the key sections:

### Minimal Example

```yaml
name: "my-job"
workspace: "./workspace"
instrument: claude-code
instrument_config:
  timeout_seconds: 1800
sheet:
  size: 1
  total_items: 5
prompt:
  template: |
    Stage {{ sheet_num }}: Process item {{ sheet_num }} of {{ total_sheets }}.
validations:
  - type: file_exists
    path: "{workspace}/output-{sheet_num}.md"
```

### Full Example

```yaml
name: "full-example"
description: "Comprehensive job example"
workspace: "./my-workspace"

# Use instrument: for new scores. Run `mzt instruments list` for options.
instrument: claude-code
instrument_config:
  timeout_seconds: 1800
  skip_permissions: true

sheet:
  size: 1
  total_items: 10

prompt:
  template: |
    Stage {{ sheet_num }} of {{ total_sheets }}: Process the next batch.
    Items {{ start_item }} through {{ end_item }}.
    {{ stakes }}
  stakes: "Be thorough and precise."

retry:
  max_retries: 3
  base_delay_seconds: 30
  exponential_base: 2.0

rate_limit:
  wait_minutes: 60
  max_waits: 24

validations:
  - type: file_exists
    path: "{workspace}/output-{sheet_num}.md"
  - type: content_contains
    path: "{workspace}/output-{sheet_num}.md"
    pattern: "## Summary"
  - type: file_modified
    path: "{workspace}/output-{sheet_num}.md"
  - type: command_succeeds
    command: "wc -w {workspace}/output-{sheet_num}.md | awk '$1 >= 500'"

notifications:
  - type: desktop
    on_events: [job_complete, job_failed]
```

> **Legacy syntax:** The `backend:` block (`backend: { type: claude_cli, ... }`) is still
> supported for backward compatibility. New scores should use `instrument:` +
> `instrument_config:`. See the [Score Writing Guide](score-writing-guide.md) for details.

### Template Variables

Core variables available in all prompt templates (from `SheetContext.to_dict()`):

| Variable | Description |
|----------|-------------|
| `{{ sheet_num }}` | Current sheet number (1-based) |
| `{{ total_sheets }}` | Total number of sheets |
| `{{ workspace }}` | Workspace directory path |
| `{{ previous_outputs }}` | Dict of previous sheet outputs (when `cross_sheet` is configured) |
| `{{ start_item }}` | First item number for this sheet |
| `{{ end_item }}` | Last item number for this sheet |
| `{{ stage }}` / `{{ movement }}` | Current stage (movement) number. Same as `sheet_num` without fan-out. |
| `{{ instance }}` / `{{ voice }}` | Fan-out instance (voice) number (1 for non-fan-out) |
| `{{ fan_count }}` / `{{ voice_count }}` | Total fan-out instances (voices) for this stage (1 for non-fan-out) |
| `{{ total_stages }}` / `{{ total_movements }}` | Total logical stages (movements). May differ from `total_sheets` with fan-out. |

### Instruments

Run `mzt instruments list` to see all available instruments. Built-in instruments:

| Instrument | Kind | Description |
|------------|------|-------------|
| `claude-code` | CLI | Claude Code CLI (default) |
| `gemini-cli` | CLI | Google Gemini CLI |
| `codex-cli` | CLI | OpenAI Codex CLI |
| `cline-cli` | CLI | Cline CLI |
| `aider` | CLI | Aider pair programming |
| `goose` | CLI | Block's Goose agent |

Legacy `backend.type` values (`claude_cli`, `anthropic_api`, `ollama`, `recursive_light`)
are still supported. New scores should use `instrument:` instead.

### Validation Types

| Type | Description |
|------|-------------|
| `file_exists` | Check for expected output files |
| `file_modified` | Verify file was updated during sheet |
| `content_contains` | Check for literal string in file content |
| `content_regex` | Match regex patterns in file content |
| `command_succeeds` | Execute shell commands as quality checks |

### Error Codes

Marianne classifies every execution failure into a structured error code. Use
`mzt errors <job> --code E001` to filter by code. The error code determines
retry behavior, delay timing, and severity.

**E0xx — Execution Errors** (process-level failures)

| Code | Name | Retriable | Severity | Delay |
|------|------|-----------|----------|-------|
| E001 | EXECUTION_TIMEOUT | Yes | ERROR | 60s |
| E002 | EXECUTION_KILLED | Yes | ERROR | 30s |
| E003 | EXECUTION_CRASHED | No | CRITICAL | — |
| E004 | EXECUTION_INTERRUPTED | No | ERROR | — |
| E005 | EXECUTION_OOM | No | CRITICAL | — |
| E006 | EXECUTION_STALE | Yes | WARNING | 120s |
| E009 | EXECUTION_UNKNOWN | Yes | ERROR | 10s |

E006 (stale detection) fires when an agent produces no output for longer than
`stale_detection.idle_timeout_seconds`. It is distinct from E001 (backend timeout)
— stale detection kills agents that go silent, while backend timeout caps total
execution time.

**E1xx — Rate Limit / Capacity**

| Code | Name | Retriable | Delay |
|------|------|-----------|-------|
| E101 | RATE_LIMIT_API | Yes | 1 hour |
| E102 | RATE_LIMIT_CLI | Yes | 15 min |
| E103 | CAPACITY_EXCEEDED | Yes | 5 min |
| E104 | QUOTA_EXHAUSTED | Yes | Dynamic |

**E2xx — Validation Errors** (output didn't meet acceptance criteria)

| Code | Name | Retriable | Delay |
|------|------|-----------|-------|
| E201 | VALIDATION_FILE_MISSING | Yes | 5s |
| E202 | VALIDATION_CONTENT_MISMATCH | Yes | 5s |
| E203 | VALIDATION_COMMAND_FAILED | Yes | 10s |
| E204 | VALIDATION_TIMEOUT | Yes | 30s |

**E3xx — Configuration** (not retriable — requires user fix)

| Code | Name | Description |
|------|------|-------------|
| E301 | CONFIG_INVALID | Invalid score YAML |
| E302 | CONFIG_MISSING_FIELD | Required field missing |
| E303 | CONFIG_PATH_NOT_FOUND | Referenced path doesn't exist |
| E304 | CONFIG_PARSE_ERROR | YAML syntax error |

**E5xx — Backend** (instrument/connection issues)

| Code | Name | Retriable | Delay |
|------|------|-----------|-------|
| E501 | BACKEND_CONNECTION | Yes | 30s |
| E502 | BACKEND_AUTH | No | — |
| E503 | BACKEND_RESPONSE | Yes | 15s |
| E504 | BACKEND_TIMEOUT | Yes | 60s |
| E505 | BACKEND_NOT_FOUND | No | — |

**E9xx — Network / Unknown**

| Code | Name | Retriable | Delay |
|------|------|-----------|-------|
| E901 | NETWORK_CONNECTION_FAILED | Yes | 30s |
| E999 | UNKNOWN | Yes | 30s |

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MZT_LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | |
| `MZT_LOG_FILE` | Path for log file output | |
| `MZT_LOG_FORMAT` | Log format: `json`, `console`, or `both` | |
| `MZT_AUTH_MODE` | Dashboard auth: `disabled`, `api_key`, `localhost_only` | `localhost_only` |
| `MZT_API_KEYS` | Comma-separated API keys for dashboard auth | |
| `MZT_LOCALHOST_BYPASS` | Allow localhost to bypass API key auth | `true` |
| `MZT_CORS_ORIGINS` | Comma-separated allowed CORS origins | `http://localhost:8080,http://127.0.0.1:8080` |
| `MZT_CORS_CREDENTIALS` | Allow credentials in CORS requests | `true` |
| `MZT_DEV` | Enable development mode (permissive CORS) | |
| `ANTHROPIC_API_KEY` | API key for Anthropic API backend | |

---

## Tips

### Verbose Mode

Use `-v` for detailed output:
```bash
marianne -v run job.yaml
```

Shows:
- Backend configuration
- Sheet execution details
- Validation results
- Timing information

### Quiet Mode

Use `-q` for minimal output:
```bash
marianne -q run job.yaml
```

Shows only:
- Errors
- Final status

### JSON Output for Scripts

Combine `--json` with `jq` for scripting:
```bash
# Get job status
mzt status my-job --json | jq '.status'

# Get failed sheet numbers
mzt status my-job --json | jq '.sheets | to_entries[] | select(.value.status == "failed") | .key'
```

### Keyboard Shortcuts

During execution:

| Key | Action |
|-----|--------|
| `Ctrl+C` | Graceful shutdown (saves state) |
| `Ctrl+C` (x2) | Force quit |

### Debugging Protocol

When a job fails, follow this order:

```bash
# 1. Check current status (routes through conductor)
mzt status my-job

# 2. Get full diagnostic report
mzt diagnose my-job

# 3. View error history
mzt errors my-job --verbose

# 4. Check logs
mzt logs my-job --level ERROR

# 5. Try recovery (re-validate without re-execute)
mzt recover my-job --dry-run
```
