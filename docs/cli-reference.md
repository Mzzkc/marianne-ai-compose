# Mozart CLI Reference

Complete reference for all Mozart CLI commands and options. Mozart provides a single entry point:

- **`mozart`** — Job orchestration CLI (run, monitor, diagnose, learn, conductor management)

---

## Global Options

These options apply to all `mozart` commands:

| Option | Short | Description | Env Var |
|--------|-------|-------------|---------|
| `--version` | `-V` | Show version and exit | |
| `--verbose` | `-v` | Show detailed output with additional information | |
| `--quiet` | `-q` | Show minimal output (errors only) | |
| `--log-level` | `-L` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `MOZART_LOG_LEVEL` |
| `--log-file` | | Path for log file output | `MOZART_LOG_FILE` |
| `--log-format` | | Log format: `json`, `console`, or `both` | `MOZART_LOG_FORMAT` |
| `--help` | | Show help message | |

---

## Core Commands

### `mozart run`

Run a job from a YAML configuration file.

```
Usage: mozart run [OPTIONS] CONFIG_FILE
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `CONFIG_FILE` | Yes | Path to YAML job configuration file |

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
| `--fresh` | | false | Delete existing state before running, ensuring a fresh start. Use for self-chaining jobs or re-running completed jobs from scratch |

#### Examples

```bash
# Basic run (requires running conductor: mozart start)
mozart run job.yaml

# Dry run to preview (works without conductor)
mozart run job.yaml --dry-run

# Custom workspace
mozart run job.yaml --workspace ./output

# Start from sheet 3
mozart run job.yaml --start-sheet 3

# With self-healing enabled
mozart run job.yaml --self-healing --yes

# Fresh start (ignores existing state)
mozart run job.yaml --fresh
```

#### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success or graceful shutdown |
| 1 | Configuration error or job failure |

---

### `mozart resume`

Resume a paused or failed job.

```
Usage: mozart resume [OPTIONS] JOB_ID
```

Loads the job state and continues execution from where it left off. By default, Mozart auto-reloads the config from the original YAML file if it still exists on disk. Falls back to the cached `config_snapshot` when the file is gone. Use `--no-reload` to force using the cached snapshot.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Job ID to resume |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | | Path to config file (optional if `config_snapshot` exists in state) |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor for resume |
| `--force` | `-f` | false | Force resume even if job appears completed |
| `--escalation` | `-e` | false | Enable human-in-the-loop escalation — **not currently supported** (blocked in conductor mode) |
| `--no-reload` | | false | Use cached config snapshot instead of auto-reloading from YAML file |
| `--self-healing` | `-H` | false | Enable automatic diagnosis and remediation when retries are exhausted |
| `--yes` | `-y` | false | Auto-confirm suggested fixes when using `--self-healing` |

#### Examples

```bash
# Resume paused job
mozart resume my-job

# Resume with explicit config
mozart resume my-job --config job.yaml

# Resume with explicit config file (overrides auto-reload)
mozart resume my-job --config updated.yaml

# Resume using cached snapshot (skip auto-reload)
mozart resume my-job --no-reload

# Force restart completed job
mozart resume my-job --force
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

### `mozart pause`

Pause a running Mozart job gracefully.

```
Usage: mozart pause [OPTIONS] JOB_ID
```

Creates a pause signal that the job detects at the next sheet boundary. The job saves its state and can be resumed with `mozart resume`.

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
mozart pause my-job

# Pause and wait for acknowledgment
mozart pause my-job --wait --timeout 30

# Pause with specific workspace
mozart pause my-job --workspace ./workspace
```

---

### `mozart modify`

Modify a job's configuration and optionally resume execution.

```
Usage: mozart modify [OPTIONS] JOB_ID
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
mozart modify my-job --config updated.yaml

# Modify and immediately resume
mozart modify my-job -c new-config.yaml --resume

# Modify, wait for pause, then resume
mozart modify my-job -c updated.yaml --resume --wait
```

---

### `mozart status`

Show detailed status of a specific job.

```
Usage: mozart status [OPTIONS] JOB_ID
```

Displays job progress, sheet states, timing information, and any errors. Use `--watch` for continuous monitoring.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `JOB_ID` | Yes | Job ID to check status for |

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--json` | `-j` | false | Output as JSON for machine parsing |
| `--watch` | `-W` | false | Continuously monitor status with live updates |
| `--interval` | `-i` | 5 | Refresh interval in seconds for `--watch` mode |
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor and read job state from filesystem |

> **Note:** `mozart status` routes through the conductor by default. The `--workspace` flag is a hidden debug override for direct filesystem access when the conductor is unavailable.

#### Examples

```bash
# Show job status (routes through conductor)
mozart status my-job

# JSON output
mozart status my-job --json

# Continuous monitoring
mozart status my-job --watch

# Watch with custom interval
mozart status my-job --watch --interval 10

# Custom workspace
mozart status my-job --workspace ./jobs
```

#### Output

Standard output includes:
- Job name and ID
- Status (running, completed, failed, paused, pending)
- Progress bar with sheet counts
- Timing information
- Error messages (if any)
- Sheet details table

JSON output structure:
```json
{
  "job_id": "my-job",
  "job_name": "My Job",
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

### `mozart list`

List jobs from the conductor.

```
Usage: mozart list [OPTIONS]
```

By default shows only active jobs (queued, running, paused). Use `--all` to include completed, failed, and cancelled jobs.

> **Note:** This command requires a running Mozart conductor (`mozart start`). Use `mozart status <job-id>` for checking individual jobs.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--all` | `-a` | false | Show all jobs including completed, failed, and cancelled |
| `--status` | `-s` | | Filter by job status: `queued`, `running`, `completed`, `failed`, `paused` |
| `--limit` | `-l` | 20 | Maximum number of jobs to display |

#### Examples

```bash
# List active jobs (default)
mozart list

# List all jobs including completed
mozart list --all

# Filter by status
mozart list --status failed

# Limit results
mozart list --limit 10
```

---

### `mozart validate`

Validate a job configuration file.

```
Usage: mozart validate [OPTIONS] CONFIG_FILE
```

Performs comprehensive validation including YAML syntax, Pydantic schema validation, Jinja template syntax checking, path existence verification, regex pattern compilation, and configuration completeness checks.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `CONFIG_FILE` | Yes | Path to YAML job configuration file |

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

| Code | Description | Severity |
|------|-------------|----------|
| V001 | Jinja syntax errors | ERROR |
| V002 | Workspace parent missing | ERROR (auto-fixable) |
| V003 | Template file missing | ERROR |
| V007 | Invalid regex patterns | ERROR |
| V101 | Undefined template variables | WARNING |
| V103 | Very short timeout | WARNING |

#### Examples

```bash
# Basic validation
mozart validate job.yaml

# Detailed output
mozart validate job.yaml --verbose

# JSON output for CI/CD
mozart validate job.yaml --json
```

---

### `mozart logs`

Show or tail log files for a job.

```
Usage: mozart logs [OPTIONS] [JOB_ID]
```

Displays log entries from Mozart log files. Supports both current log files and compressed rotated logs (`.gz`).

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
mozart logs

# Filter by job
mozart logs my-job

# Follow logs in real-time
mozart logs --follow

# Show last 100 lines of errors only
mozart logs --lines 100 --level ERROR

# Use specific log file
mozart logs --file ./workspace/logs/mozart.log
```

---

### `mozart errors`

List all errors for a job with detailed information.

```
Usage: mozart errors [OPTIONS] JOB_ID
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
mozart errors my-job

# Errors for specific sheet
mozart errors my-job --sheet 3

# Only transient errors
mozart errors my-job --type transient

# Filter by error code
mozart errors my-job --code E001

# Verbose with stdout/stderr details
mozart errors my-job --verbose
```

---

### `mozart diagnose`

Generate a comprehensive diagnostic report for a job.

```
Usage: mozart diagnose [OPTIONS] JOB_ID
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
| `--workspace` | `-w` | | *(hidden)* Debug override: bypass conductor for diagnostics |
| `--json` | `-j` | false | Output diagnostic report as JSON |
| `--include-logs` | | false | Inline the last 50 lines from each sheet/hook log file |

#### Examples

```bash
# Full diagnostic report
mozart diagnose my-job

# Machine-readable
mozart diagnose my-job --json

# Include inline log content
mozart diagnose my-job --include-logs
```

---

### `mozart history`

Show execution history for a job.

```
Usage: mozart history [OPTIONS] JOB_ID
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
mozart history my-job

# History for specific sheet
mozart history my-job --sheet 3

# Show more records
mozart history my-job --limit 100

# JSON output
mozart history my-job --json
```

---

### `mozart recover`

Recover sheets that completed work but were incorrectly marked as failed.

```
Usage: mozart recover [OPTIONS] JOB_ID
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
mozart recover my-job

# Recover specific sheet
mozart recover my-job --sheet 6

# Check without modifying (dry run)
mozart recover my-job --dry-run
```

---

### `mozart dashboard`

Start the web dashboard.

```
Usage: mozart dashboard [OPTIONS]
```

Launches the Mozart dashboard API server for job monitoring and control.

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
mozart dashboard

# Custom port
mozart dashboard --port 3000

# Allow external connections
mozart dashboard --host 0.0.0.0

# Development mode with auto-reload
mozart dashboard --reload
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

### `mozart mcp`

Start the Mozart MCP (Model Context Protocol) server.

```
Usage: mozart mcp [OPTIONS]
```

Launches an MCP server that exposes Mozart's job management capabilities as tools for external AI agents. Provides job management tools, artifact browsing, log streaming, and configuration access.

When the Mozart conductor is running, the MCP server routes operations through it for coordinated execution.

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--port` | `-p` | 8001 | Port to run MCP server on |
| `--host` | | `127.0.0.1` | Host to bind to |
| `--workspace` | `-w` | `.` | Workspace directory for job operations |

#### Examples

```bash
# Start on default port
mozart mcp

# Custom port
mozart mcp --port 8002

# Specific workspace
mozart mcp --workspace ./projects
```

See [MCP Integration Guide](MCP-INTEGRATION.md) for Claude Desktop setup and available tools.

---

### `mozart config`

Manage conductor configuration.

```
Usage: mozart config COMMAND [OPTIONS]
```

#### Subcommands

##### `mozart config show`

Display current daemon configuration as a table. When the conductor is running, displays the **live in-memory config** (reflecting any SIGHUP reloads) with `[live]` source indicators. Falls back to disk-based display when the conductor is not running.

```bash
mozart config show
mozart config show --config /etc/mozart/daemon.yaml
```

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to daemon config file (default: `~/.mozart/daemon.yaml`). Ignored when live config is available from a running conductor. |

##### `mozart config set`

Update a conductor configuration value. Values are validated against the DaemonConfig schema before saving. Use dot notation for nested keys.

```bash
mozart config set max_concurrent_jobs 10
mozart config set socket.path /tmp/custom.sock
mozart config set resource_limits.max_memory_mb 4096
mozart config set log_level debug
```

| Argument | Required | Description |
|----------|----------|-------------|
| `KEY` | Yes | Config key in dot notation (e.g., `socket.path`, `max_concurrent_jobs`) |
| `VALUE` | Yes | New value to set |

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to daemon config file (default: `~/.mozart/daemon.yaml`) |

##### `mozart config path`

Show the conductor config file location and whether it exists.

```bash
mozart config path
```

##### `mozart config init`

Create a default conductor config file with all default values and descriptive comments. Refuses to overwrite unless `--force` is given.

```bash
mozart config init
mozart config init --force
mozart config init --config /etc/mozart/daemon.yaml
```

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to create config file (default: `~/.mozart/daemon.yaml`) |
| `--force` | `-f` | Overwrite existing config file |

##### `mozart config check`

Validate a daemon config file against the `DaemonConfig` schema without starting the conductor. Exits 0 if valid, 1 if invalid or the file cannot be loaded.

```bash
mozart config check
mozart config check --config /path/to/custom.yaml
```

| Option | Short | Description |
|--------|-------|-------------|
| `--config` | `-c` | Path to daemon config file to validate (default: `~/.mozart/daemon.yaml`) |

---

## Learning Commands

These commands inspect and analyze Mozart's learning system — patterns learned from job executions that inform future runs.

### `mozart patterns-list`

View global learning patterns.

```
Usage: mozart patterns-list [OPTIONS]
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
mozart patterns-list
mozart patterns-list --high-trust
mozart patterns-list --quarantined
mozart patterns-list --min-priority 0.5 --json
```

---

### `mozart patterns-why`

Analyze WHY patterns succeed with metacognitive insights. Shows success factors — the context conditions that contribute to pattern effectiveness.

```
Usage: mozart patterns-why [OPTIONS] [PATTERN_ID]
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
mozart patterns-why
mozart patterns-why abc123
mozart patterns-why --min-obs 3
```

---

### `mozart patterns-entropy`

Monitor pattern population diversity using Shannon entropy.

```
Usage: mozart patterns-entropy [OPTIONS]
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
mozart patterns-entropy
mozart patterns-entropy --threshold 0.3
mozart patterns-entropy --history
mozart patterns-entropy --record
```

---

### `mozart patterns-budget`

Display exploration budget status and history. The budget adjusts based on pattern entropy: low entropy boosts the budget to inject diversity; healthy entropy decays toward floor (default 5%).

```
Usage: mozart patterns-budget [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--job` | `-j` | | Filter by specific job hash |
| `--history` | `-H` | false | Show budget adjustment history |
| `--limit` | `-n` | 20 | Number of history records to show |
| `--json` | | false | Output as JSON |

```bash
mozart patterns-budget
mozart patterns-budget --history
```

---

### `mozart learning-stats`

View global learning statistics. Shows summary including execution counts, pattern counts, and effectiveness metrics.

```
Usage: mozart learning-stats [OPTIONS]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--json` | `-j` | Output as JSON |

```bash
mozart learning-stats
mozart learning-stats --json
```

---

### `mozart learning-insights`

Show actionable insights from learning data including output patterns, error code patterns, and success predictors.

```
Usage: mozart learning-insights [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--limit` | | 10 | Maximum patterns to show |
| `--pattern-type` | | | Filter by type |

```bash
mozart learning-insights
mozart learning-insights --pattern-type output_pattern
mozart learning-insights --limit 20
```

---

### `mozart learning-drift`

Detect patterns with effectiveness drift. Drift is calculated by comparing pattern effectiveness in the last N applications vs the previous N.

```
Usage: mozart learning-drift [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--threshold` | `-t` | 0.2 | Drift threshold (0.0–1.0) to flag patterns |
| `--window` | `-w` | 5 | Window size for drift comparison |
| `--limit` | `-l` | 10 | Maximum patterns to show |
| `--json` | `-j` | false | Output as JSON |
| `--summary` | `-s` | false | Show only summary statistics |

```bash
mozart learning-drift
mozart learning-drift --threshold 0.15
mozart learning-drift --summary
```

---

### `mozart learning-epistemic-drift`

Detect patterns with epistemic drift (belief/confidence changes). Epistemic drift tracks confidence changes over time, complementing effectiveness drift as a leading indicator of pattern health.

```
Usage: mozart learning-epistemic-drift [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--threshold` | `-t` | 0.15 | Epistemic drift threshold (0.0–1.0) |
| `--window` | `-w` | 5 | Window size for drift comparison |
| `--limit` | `-l` | 10 | Maximum patterns to show |
| `--json` | `-j` | false | Output as JSON |
| `--summary` | `-s` | false | Show only summary statistics |

```bash
mozart learning-epistemic-drift
mozart learning-epistemic-drift --threshold 0.1
mozart learning-epistemic-drift --summary
```

---

### `mozart learning-activity`

View recent learning activity and pattern applications.

```
Usage: mozart learning-activity [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--hours` | `-h` | 24 | Show activity from the last N hours |
| `--json` | `-j` | false | Output as JSON |

```bash
mozart learning-activity
mozart learning-activity --hours 48
```

---

### `mozart entropy-status`

Display entropy response status and history. When pattern entropy drops below threshold, the system automatically boosts exploration budget and revisits quarantined patterns.

```
Usage: mozart entropy-status [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--job` | `-j` | | Filter by specific job hash |
| `--history` | `-H` | false | Show entropy response history |
| `--limit` | `-n` | 20 | Number of history records to show |
| `--json` | | false | Output as JSON |
| `--check` | `-c` | false | Check if entropy response is needed (dry-run) |

```bash
mozart entropy-status
mozart entropy-status --history
mozart entropy-status --check
```

---

## Conductor Commands

### `mozart start`

Start the Mozart conductor.

```
Usage: mozart start [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | | Path to conductor config file |
| `--foreground` | `-f` | false | Run in foreground (for development) |
| `--log-level` | `-l` | `info` | Logging level |

```bash
# Start in background (production)
mozart start

# Start in foreground (development)
mozart start --foreground

# Custom config
mozart start --config /etc/mozart/daemon.yaml

# Debug logging
mozart start --log-level debug
```

---

### `mozart stop`

Stop the running conductor.

```
Usage: mozart stop [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--pid-file` | | `/tmp/mozart.pid` | Path to PID file |
| `--force` | | false | Force stop |

```bash
mozart stop
mozart stop --force
```

---

### `mozart restart`

Restart the conductor (stop + start).

```
Usage: mozart restart [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config` | `-c` | | Path to conductor config file |
| `--foreground` | `-f` | false | Run in foreground (for development) |
| `--log-level` | `-l` | `info` | Logging level |

```bash
mozart restart
mozart restart --foreground
```

---

### `mozart conductor-status`

Check conductor status via health probes.

```
Usage: mozart conductor-status [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--pid-file` | | `/tmp/mozart.pid` | Path to PID file |
| `--socket` | | `/tmp/mozart.sock` | Path to Unix socket |

```bash
mozart conductor-status
```

---

## Configuration Reference

Job configurations are defined in YAML files. Here are the key sections:

### Minimal Example

```yaml
name: "my-job"
workspace: "./workspace"
backend:
  type: claude_cli
  timeout_seconds: 1800
sheet:
  size: 1
  total_items: 5
prompt:
  template: |
    Stage {{ sheet_num }}: Process item {{ sheet_num }} of {{ total_sheets }}.
validations:
  - type: file_exists
    path: "{{ workspace }}/output-{{ sheet_num }}.md"
```

### Full Example

```yaml
name: "full-example"
description: "Comprehensive job example"
workspace: "./my-workspace"

backend:
  type: claude_cli          # claude_cli | anthropic_api | ollama | recursive_light
  skip_permissions: true
  working_directory: ./my-workspace
  timeout_seconds: 1800

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
    path: "{{ workspace }}/output-{{ sheet_num }}.md"
  - type: content_contains
    path: "{{ workspace }}/output-{{ sheet_num }}.md"
    pattern: "## Summary"
  - type: file_modified
    path: "{{ workspace }}/output-{{ sheet_num }}.md"
  - type: command_succeeds
    command: "wc -w {{ workspace }}/output-{{ sheet_num }}.md | awk '$1 >= 500'"

notifications:
  - type: desktop
    on_events: [job_complete, job_failed]
```

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
| `{{ stage }}` | Current stage number (in fan-out, same as `sheet_num` without fan-out) |
| `{{ instance }}` | Fan-out instance number (1 for non-fan-out) |
| `{{ fan_count }}` | Total fan-out instances for this stage (1 for non-fan-out) |
| `{{ total_stages }}` | Total logical stages (may differ from `total_sheets` with fan-out) |

### Backend Types

| Type | Description |
|------|-------------|
| `claude_cli` | Primary backend, uses Claude CLI subprocess (default) |
| `anthropic_api` | Uses Anthropic Python SDK directly with model selection |
| `ollama` | Community-contributed Ollama integration for local models |
| `recursive_light` | Experimental recursive backend |

### Validation Types

| Type | Description |
|------|-------------|
| `file_exists` | Check for expected output files |
| `file_modified` | Verify file was updated during sheet |
| `content_contains` | Check for literal string in file content |
| `content_regex` | Match regex patterns in file content |
| `command_succeeds` | Execute shell commands as quality checks |

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MOZART_LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | |
| `MOZART_LOG_FILE` | Path for log file output | |
| `MOZART_LOG_FORMAT` | Log format: `json`, `console`, or `both` | |
| `MOZART_AUTH_MODE` | Dashboard auth: `disabled`, `api_key`, `localhost_only` | `localhost_only` |
| `MOZART_API_KEYS` | Comma-separated API keys for dashboard auth | |
| `MOZART_LOCALHOST_BYPASS` | Allow localhost to bypass API key auth | `true` |
| `MOZART_CORS_ORIGINS` | Comma-separated allowed CORS origins | `http://localhost:8080,http://127.0.0.1:8080` |
| `MOZART_CORS_CREDENTIALS` | Allow credentials in CORS requests | `true` |
| `MOZART_DEV` | Enable development mode (permissive CORS) | |
| `ANTHROPIC_API_KEY` | API key for Anthropic API backend | |

---

## Tips

### Verbose Mode

Use `-v` for detailed output:
```bash
mozart -v run job.yaml
```

Shows:
- Backend configuration
- Sheet execution details
- Validation results
- Timing information

### Quiet Mode

Use `-q` for minimal output:
```bash
mozart -q run job.yaml
```

Shows only:
- Errors
- Final status

### JSON Output for Scripts

Combine `--json` with `jq` for scripting:
```bash
# Get job status
mozart status my-job --json | jq '.status'

# Get failed sheet numbers
mozart status my-job --json | jq '.sheets | to_entries[] | select(.value.status == "failed") | .key'
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
mozart status my-job

# 2. Get full diagnostic report
mozart diagnose my-job

# 3. View error history
mozart errors my-job --verbose

# 4. Check logs
mozart logs my-job --level ERROR

# 5. Try recovery (re-validate without re-execute)
mozart recover my-job --dry-run
```
