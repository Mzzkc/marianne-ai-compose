# Conductor Guide

Marianne uses a conductor process to manage job execution, similar to how Docker requires `dockerd`. The conductor centralizes resource management, maintains a persistent job registry, and provides health monitoring with backpressure.

## Why a Conductor?

Running jobs through a long-lived conductor process provides several advantages over direct CLI execution:

- **Centralized resource management.** The conductor monitors memory and child process counts, applying backpressure when the system is under load. Jobs submitted during high pressure are rejected rather than allowed to destabilize the system.
- **Persistent job registry.** A SQLite-backed registry tracks all submitted jobs across conductor restarts. Jobs left running when a conductor crashes are automatically marked as failed on the next startup.
- **Cross-job learning.** A single `GlobalLearningStore` instance is shared across all concurrent jobs. Pattern discoveries in one job are immediately available to others — no cross-process SQLite locking contention.
- **Health monitoring.** Liveness and readiness probes are available over the IPC socket, enabling external monitoring tools to check conductor health.

## Quick Start

```bash
# 1. Start the conductor
mzt start --foreground    # Foreground (logs to console)
mzt start                 # Background (double-fork, logs to file)

# 2. Run a job (routed through conductor)
mzt run my-score.yaml

# 3. Check conductor status
mzt conductor-status

# 4. Stop the conductor
mzt stop
```

## The Conductor Requirement

`mzt run` **requires a running conductor**. If no conductor is detected, the command exits with an error:

```
Error: Marianne conductor is not running.
Start it with: mzt start
```

**Exceptions that work without a conductor:**

| Command | Conductor Required? | Why |
|---------|-----------------|-----|
| `mzt run config.yaml` | Yes | Job execution goes through conductor |
| `mzt run --dry-run config.yaml` | No | Only validates and displays the job plan |
| `mzt validate config.yaml` | No | Static config validation, no execution |

### How Auto-Detection Works

When `mzt run` is invoked:

1. The CLI calls `is_daemon_available()` from `marianne.daemon.detect`
2. This resolves the socket path (default: `/tmp/marianne.sock`)
3. A `DaemonClient` attempts to open a Unix socket connection
4. If the connection succeeds, the job is submitted via JSON-RPC
5. If the connection fails (socket missing, refused, timeout), the CLI reports the conductor is not running

The detection is **fail-safe**: any exception during detection returns `False`, ensuring conductor bugs never break the CLI. The CLI simply falls through to the "conductor not running" error message.

## Architecture

The conductor is composed of several layers:

```
┌──────────────────────────────────────────────────┐
│                   DaemonProcess                   │
│  Lifecycle owner: boot, signal handling, cleanup  │
├──────────────────────────────────────────────────┤
│                    DaemonServer                   │
│  Unix socket + JSON-RPC 2.0 (NDJSON protocol)    │
├────────────────────┬─────────────────────────────┤
│   RequestHandler   │      HealthChecker          │
│  Method dispatch   │  Liveness + readiness       │
├────────────────────┴─────────────────────────────┤
│                    JobManager                     │
│  Task tracking, concurrency semaphore, shutdown   │
├──────────────┬───────────────┬───────────────────┤
│  JobRegistry │  JobService   │  BackpressureCtrl │
│  SQLite DB   │  Run/resume   │  Memory-based     │
│  persistence │  pause/status │  load management  │
├──────────────┴───────────────┴───────────────────┤
│                     EventBus                      │
│  Async pub/sub for runner callback events         │
├──────────────────────────────────────────────────┤
│         ResourceMonitor + LearningHub             │
│  Periodic checks   │  Cross-job pattern sharing   │
├──────────────────────────────────────────────────┤
│               SemanticAnalyzer                    │
│  LLM-based sheet analysis → learning insights     │
└──────────────────────────────────────────────────┘
```

### Process Model

- **DaemonProcess** — Owns the lifecycle. In background mode, performs a classic double-fork to detach from the terminal. Installs signal handlers for SIGTERM and SIGINT.
- **DaemonServer** — Listens on a Unix domain socket. Each client connection is handled as an asyncio task. Messages are newline-delimited JSON (NDJSON), each containing a JSON-RPC 2.0 request or response.
- **JobManager** — Tracks jobs as `asyncio.Task` instances. Uses a `Semaphore` to enforce the `max_concurrent_jobs` limit. Jobs exceeding `job_timeout_seconds` are cancelled.
- **JobService** — Decoupled execution engine (no CLI dependencies). Handles the full run/resume/pause/status lifecycle for individual jobs.
- **JobRegistry** — SQLite-backed persistent storage. Survives conductor restarts. On startup, orphaned jobs (status `queued` or `running` from a previous conductor) are marked as `failed`.
- **EventBus** — Async pub/sub that routes runner callback events (`sheet.started`, `sheet.completed`, `sheet.failed`, `sheet.retrying`, `sheet.validation_passed/failed`, `job.cost_update`, `job.iteration`) to downstream consumers. Bounded deques per subscriber prevent slow consumers from blocking publishers.
- **SemanticAnalyzer** — Subscribes to `sheet.completed` and `sheet.failed` events via the EventBus. On each event, captures a snapshot of the sheet's execution context (prompt, output, validation results) and sends it to an LLM (configurable model, default Sonnet) for analysis. The LLM response is parsed into structured insights and stored as `SEMANTIC_INSIGHT` patterns in the global learning store. These patterns are automatically picked up by the existing pattern injection pipeline for future sheets. Concurrency is limited by a semaphore (`max_concurrent_analyses`, default 3). Analysis failures never affect running jobs.

### IPC Protocol

The conductor uses **JSON-RPC 2.0** over a **Unix domain socket** with newline-delimited JSON framing:

```json
{"jsonrpc":"2.0","method":"job.submit","params":{"config_path":"/path/to/config.yaml"},"id":1}
```

Registered RPC methods:

| Method | Description |
|--------|-------------|
| `job.submit` | Submit a new job for execution |
| `job.status` | Get status of a specific job |
| `job.pause` | Pause a running job |
| `job.resume` | Resume a paused job |
| `job.cancel` | Cancel a running job |
| `job.list` | List all known jobs |
| `job.diagnose` | Get diagnostic report for a job |
| `job.errors` | Get error details for a job |
| `job.history` | Get execution history for a job |
| `job.recover` | Recover failed sheets for a job |
| `daemon.status` | Get conductor status summary (PID, uptime, memory, version) |
| `daemon.shutdown` | Initiate conductor shutdown |
| `daemon.health` | Liveness probe |
| `daemon.ready` | Readiness probe |
| `daemon.clear_rate_limits` | Clear cached rate limits (all or by instrument) |

### Backpressure

The `BackpressureController` assesses system pressure based on memory usage (as a percentage of `max_memory_mb`) and child process count:

| Pressure Level | Memory Threshold | Effect |
|---------------|-----------------|--------|
| NONE | < 50% | Normal operation |
| LOW | 50–70% | 2s delay between sheet dispatches |
| MEDIUM | 70–85% | 10s delay between sheet dispatches |
| HIGH | > 85% memory or high process count | New job submissions rejected |
| CRITICAL | > 95% memory, > 80% process count, or monitor degraded | Emergency: oldest job (by submission time) may be cancelled |

When `should_accept_job()` returns `False` (HIGH or CRITICAL pressure), the score is rejected outright with "System under high pressure — try again later."

Rate limits do **not** cause job rejection. They are per-instrument concerns handled at the sheet dispatch level by the baton — a rate limit on one instrument does not block jobs targeting different instruments.

## Configuration

The conductor is configured via a YAML file passed to `mzt start --config <path>`. Without a config file, all defaults are used.

### DaemonConfig Fields

**Essential fields** (what you'll typically configure):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_concurrent_jobs` | `int` | `15` | Max simultaneous jobs (1–50) |
| `log_level` | `str` | `"info"` | One of: `debug`, `info`, `warning`, `error` |
| `job_timeout_seconds` | `float` | `86400.0` | Max wall-clock time per job (24 hours) |
| `learning.enabled` | `bool` | `true` | Semantic learning on/off |
| `profiler.enabled` | `bool` | `true` | Resource monitoring on/off |

**Advanced fields** (rarely need changing):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `socket.path` | `Path` | `/tmp/marianne.sock` | Unix domain socket path |
| `socket.permissions` | `int` | `0o660` | Socket file permissions (octal) |
| `socket.backlog` | `int` | `5` | Max pending connections |
| `pid_file` | `Path` | `/tmp/marianne.pid` | PID file path |
| `shutdown_timeout_seconds` | `float` | `300.0` | Max wait for graceful shutdown (5 min) |
| `monitor_interval_seconds` | `float` | `15.0` | Resource check interval |
| `max_job_history` | `int` | `1000` | Terminal jobs kept in memory before eviction |
| `log_file` | `Path \| None` | `None` | Log file path (`None` = stderr only) |
| `learning.*` | `SemanticLearningConfig` | *(see below)* | Semantic learning via LLM analysis |

### Semantic Learning (nested under `learning`)

Controls the SemanticAnalyzer — LLM-based analysis of sheet completions that produces insights stored in the learning database.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `true` | Enable semantic learning. Defaults to on when the conductor is running. |
| `backend` | `BackendConfig` | *(nested)* | Backend configuration for the analysis LLM. Supports any backend type: `claude_cli` (uses Claude Code, no API key needed), `anthropic_api`, `ollama` (free local models), or `recursive_light`. See BackendConfig fields below. |
| `analyze_on` | `list` | `["success", "failure"]` | Which outcomes to analyze: `success`, `failure`, or both |
| `max_concurrent_analyses` | `int` | `3` | Max concurrent LLM analysis tasks (1–20) |
| `entropy_threshold` | `float` | `0.1` | Entropy threshold for triggering diversity injection. When pattern diversity index drops below this value (10% of max entropy by default), the learning system automatically boosts exploration budget to escape local optima. |
| `exploration_budget` | `float` | `0.15` | Exploration budget boost amount when entropy response triggers. Controls how aggressively the system explores alternatives when pattern diversity collapses. |

**BackendConfig fields** (nested under `learning.backend.*`):

The `backend` field accepts the same `BackendConfig` used by job execution. Common fields include:

| Field | Example | Description |
|-------|---------|-------------|
| `type` | `"anthropic_api"` | Backend type: `anthropic_api`, `claude_cli`, `ollama`, etc. |
| `model` | `"claude-sonnet-4-5-20250929"` | Model ID for analysis LLM calls |
| `api_key_env` | `"ANTHROPIC_API_KEY"` | Environment variable containing the API key (not needed for `claude_cli`) |
| `max_tokens` | `4096` | Max response tokens (256–32768) |
| `temperature` | `0.3` | Sampling temperature for analytical precision (0.0–1.0) |

See the [Configuration Reference](configuration-reference.md) for all available backend options.

### Resource Limits (nested under `resource_limits`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_memory_mb` | `int` | `8192` | Max RSS memory before backpressure triggers |
| `max_processes` | `int` | `50` | Max child processes |
| `max_api_calls_per_minute` | `int` | `60` | **Not yet enforced** — reserved for future rate limiting |

### Additional Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `state_db_path` | `Path` | `~/.marianne/daemon-state.db` | Job registry database path. Overridden by `--conductor-clone` for clone isolation. |
| `max_concurrent_sheets` | `int` | `10` | Max concurrent sheets across all jobs (used by the baton when `use_baton: true`) |
| `use_baton` | `bool` | `true` | Enable the baton execution engine. The baton is the default since Phase 2. Set to `false` to fall back to the legacy monolithic runner. |
| `preflight.token_warning_threshold` | `int` | `50000` | Token count above which to warn during preflight checks. Set higher for large-context instruments. `0` to disable. |
| `preflight.token_error_threshold` | `int` | `150000` | Token count above which to error during preflight checks. Set higher for large-context instruments (e.g., `800000` for 1M-context models). `0` to disable. |

### Example Config File

Most users need very little configuration. The defaults are sensible:

```yaml
# ~/.marianne/conductor.yaml — minimal config
max_concurrent_jobs: 10
log_level: info
```

For more control:

```yaml
# ~/.marianne/conductor.yaml — full example
max_concurrent_jobs: 10
job_timeout_seconds: 43200  # 12 hours for long jobs
log_level: debug
log_file: ~/.marianne/marianne.log

resource_limits:
  max_memory_mb: 4096
  max_processes: 30

learning:
  enabled: true
  backend:
    type: anthropic_api
    model: claude-sonnet-4-5-20250929
    temperature: 0.3
  analyze_on: [success, failure]
  max_concurrent_analyses: 3
```

### Daemon Operational Profiles

Instead of configuring individual fields, use `--profile` to apply a preset:

```bash
mzt start                        # sensible defaults
mzt start --profile dev          # debug logging, strace on
mzt start --profile intensive    # 48h timeout, high resource limits
mzt start --profile minimal      # profiler + learning off
```

Profiles are partial overrides applied on top of your config file. Resolution order:

1. `DaemonConfig` defaults
2. `~/.marianne/conductor.yaml` (if exists)
3. `--profile` (if specified)
4. CLI flags (`--log-level`, etc.) override everything

**Built-in profiles:**

| Profile | Use Case | Key Overrides |
|---------|----------|-------------|
| `dev` | Debugging Marianne itself | `log_level: debug`, `max_concurrent_jobs: 2`, `strace_enabled: true` |
| `intensive` | Long-running production work | `job_timeout: 48h`, `max_memory: 16GB`, `max_processes: 100` |
| `minimal` | Low-resource environments | `profiler: off`, `learning: off`, `max_concurrent_jobs: 2` |

Profiles also work with `restart`:

```bash
mzt restart --profile dev
```

### Live Config Reload (SIGHUP)

The conductor supports hot-reloading configuration without a restart. Send `SIGHUP` to the conductor process and it will re-read the config file from disk:

```bash
# Reload config after editing ~/.marianne/conductor.yaml
kill -SIGHUP $(cat /tmp/marianne.pid)
```

**Reloadable fields** (take effect immediately):
- `max_concurrent_jobs` — concurrency semaphore is rebuilt
- `resource_limits.*` — memory/process limits updated
- `log_level` — logging reconfigured
- `job_timeout_seconds`, `shutdown_timeout_seconds`, `monitor_interval_seconds`

**Non-reloadable fields** (require conductor restart):
- `socket.*` (path, permissions, backlog)
- `pid_file`

If a non-reloadable field differs from the running config, the conductor logs a warning but continues with the old value.

### Live Config Display

`mzt config show` automatically queries the running conductor for its in-memory config. This reflects any SIGHUP reloads. When the conductor is not running, it falls back to reading from disk.

```bash
# Shows [live] source when conductor is running
mzt config show

# Validate a config file without starting the conductor
mzt config check
mzt config check --config /path/to/custom.yaml
```

## Monitoring

### `mzt conductor-status`

The primary monitoring command. Queries the conductor via IPC and displays:

```
Marianne conductor is running (PID 12345)
  Uptime: 2h 15m 30s
  [+] Readiness: ready
  Running jobs: 2
  Memory: 1024.5 MB
  Child processes: 8
  Accepting work: True
  Version: 0.1.0
```

Readiness shows `[+] ready` when the conductor is accepting jobs, or `[-] not_ready` when under pressure, shutting down, experiencing elevated failure rates, or notification delivery is degraded.

### Conductor Logs

In foreground mode, logs go to stderr in console format. In background mode, logs are structured JSON. If `log_file` is configured, logs are written there:

```bash
# View conductor logs (if log_file is set)
tail -f ~/.marianne/marianne.log
```

### Programmatic Health Probes

The `daemon.health` and `daemon.ready` IPC methods are available for integration with external monitoring. They are used internally by `mzt conductor-status`:

- **Liveness** (`daemon.health`): Returns OK if the conductor can execute the handler — minimal cost, no resource checks.
- **Readiness** (`daemon.ready`): Returns `ready` when memory is within limits, failure rate is normal, notifications are functional, and the conductor is not shutting down.

## Conductor Clones

A **clone conductor** is an isolated conductor instance used for testing.
Clones have their own socket, PID file, state database, and log — so you can
test scores and CLI behavior without risking your production conductor.

```bash
# Start a clone
marianne --conductor-clone start

# Submit a score to the clone
marianne --conductor-clone run my-test-score.yaml

# Check the clone's status
marianne --conductor-clone conductor-status

# Named clones for parallel testing
marianne --conductor-clone=staging start
marianne --conductor-clone=staging run staging-test.yaml

# Stop when done
marianne --conductor-clone stop
```

**Key behaviors:**
- The clone inherits your production `~/.marianne/conductor.yaml` config.
- Clone paths: `/tmp/marianne-clone.sock` (socket), `/tmp/marianne-clone.pid` (PID).
- Named clones: `/tmp/marianne-clone-staging.sock`, etc.
- Clone names are sanitized (64-character limit, safe characters only).
- Commands that don't interact with the conductor (`validate`, `--help`) ignore the flag.

See the [CLI Reference](cli-reference.md#conductor-clones) for full details.

---

## The Baton (Event-Driven Execution Engine)

The baton (`daemon/baton/`) is Marianne's execution engine, using event-driven
per-sheet dispatch instead of the legacy monolithic sequential runner. It is
the default execution path (`use_baton: true`) with 1,900+ tests.

Key capabilities:
- **Event-driven dispatch** — sheets dispatch when their dependencies are met and their
  instrument has capacity, rather than running sequentially in one task
- **Per-instrument concurrency** — each instrument has independent slots, so rate limits
  on one instrument don't block others
- **Timer-based retry** — backoff delays use a timer wheel instead of `asyncio.sleep()`
- **Rate limit auto-resume** — when a rate limit is hit, the baton schedules a timer to
  automatically clear the limit when it expires and resume WAITING sheets. Without this,
  rate-limited sheets stay blocked until manually cleared via `mzt clear-rate-limits`.
- **Restart recovery** — baton state persists and reconciles with CheckpointState on restart
- **Cost enforcement** — per-sheet and per-job cost limits enforced after every attempt
- **Full prompt assembly** — the baton renders prompts through the complete pipeline
  (preamble, template variables, prelude/cadenza injection, validation requirements)
- **Cross-sheet context** — `previous_outputs` and `previous_files` are populated from
  completed sheets' stdout and workspace files, matching the legacy runner's behavior
- **Checkpoint sync** — sheet status changes from all event types (escalation, cancellation,
  timeout, rate limit expiry, shutdown) are synchronized back to CheckpointState with
  deduplication to prevent redundant callbacks

**The baton is active by default.** No configuration needed. To fall back to the legacy
runner, set `use_baton: false` in `~/.marianne/conductor.yaml`.

### Transition Plan

The baton is the mandatory path to multi-instrument execution. Without it, the conductor
delegates to the legacy monolithic runner, which silently ignores per-sheet instrument
assignments and runs everything through a single backend.

The transition has three phases:

**Phase 1: Prove the baton works** (complete)
- Baton tested with `--conductor-clone` alongside the production conductor
- Per-sheet instrument assignment, rate limits, timeouts verified

**Phase 2: Baton as default** (complete — D-027)
- `use_baton` default flipped to `true` in `DaemonConfig`
- Legacy runner remains as fallback for scores that explicitly opt out (`use_baton: false`)
- All new features target the baton path only

**Phase 3: Remove the toggle** (future)
- Delete the legacy runner execution path
- Remove `use_baton` from `DaemonConfig`
- The baton is how the conductor runs — no toggle, no fallback

> **Important:** If you set `use_baton: false`, per-sheet `instrument:` assignments
> will not take effect at runtime — the legacy runner uses a single backend regardless
> of per-sheet configuration.

### Legacy Components

These components are still in use through the current execution path:

- **GlobalSheetScheduler** (`daemon/scheduler.py`) — Priority-based cross-job scheduling.
  Built but not yet wired into the execution path.
- **RateLimitCoordinator** (`daemon/rate_coordinator.py`) — Cross-job rate limit state
  sharing. The write path is active; the read path feeds the scheduler. Stale limits
  can be cleared manually with `mzt clear-rate-limits`.

## Migration from Pre-Conductor Usage

If you previously ran Marianne without a conductor:

**Before (direct execution, no longer supported for `mzt run`):**
```bash
mzt run my-score.yaml
```

**After (conductor required):**
```bash
# One-time: start the conductor
mzt start

# Then run jobs as before
mzt run my-score.yaml

# The conductor stays running — no need to restart per job
```

### setup.sh

The `setup.sh` script includes a `--daemon` flag that installs conductor dependencies (psutil for resource monitoring):

```bash
./setup.sh --daemon          # Install with conductor support
./setup.sh --dev --daemon    # Dev + conductor
```

This installs the `daemon` extras group, which includes `psutil` for the `ResourceMonitor` and `SystemProbe` components.

## Troubleshooting

### "Marianne conductor is not running"

The conductor is not reachable at the default socket path (`/tmp/marianne.sock`).

```bash
# Start the conductor
mzt start

# Verify it's running
mzt conductor-status
```

### "Conductor is already running"

A conductor instance is already active. Check with:

```bash
mzt conductor-status
```

If the conductor is unresponsive but the PID file exists, it may have crashed:

```bash
# Stop cleans up stale PID files
mzt stop
mzt start
```

### Stale PID File

If `mzt stop` reports "conductor is not running" but a PID file exists, the file is automatically cleaned up. If it persists:

```bash
rm /tmp/marianne.pid
mzt start
```

### Permission Errors on Socket

The socket is created with `0o660` permissions by default (owner + group read/write). If another user needs access, adjust `socket.permissions` in the conductor config. The conductor also rejects symlinked socket paths as a security measure.

### "Conductor does not support '...'. Restart the conductor"

The CLI is newer than the running conductor. This happens when you update
Marianne code but don't restart the conductor — the running daemon still has
the old code loaded and doesn't recognize new IPC methods.

```bash
# Pick up code changes
pip install -e ".[dev]"
mzt restart
```

### "--escalation incompatible with conductor"

The `--escalation` flag requires interactive console prompts which are not available in conductor mode. Escalation is not currently supported when running through the conductor.

### Orphan Recovery on Restart

When the conductor starts, it checks the persistent registry for jobs that were `queued` or `running` during the previous conductor session. These orphaned jobs are automatically marked as `failed` with the error message "Conductor restarted while job was active." Check for recovered orphans in the conductor logs:

```
manager.orphans_recovered count=2
```

### Advisory Lock Conflicts

The conductor uses `fcntl.flock()` on the PID file to prevent concurrent starts. If you see "PID file locked" errors, another `mzt start` is in progress. Wait a moment and retry.

### Force Stop

If the conductor is unresponsive to graceful shutdown:

```bash
mzt stop --force    # Sends SIGKILL instead of SIGTERM
```

This kills the process immediately without waiting for running jobs to complete. Use as a last resort — running jobs will not checkpoint cleanly.
