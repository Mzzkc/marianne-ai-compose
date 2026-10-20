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
2. This resolves the socket path (default: `~/.config/mzt/mzt.sock`)
3. A `DaemonClient` attempts to open a Unix socket connection
4. If the connection succeeds, the job is submitted via JSON-RPC
5. If the socket is missing, or the connection is refused and the PID file names no live process, the CLI reports the conductor is not running (`running_state: absent`, hint `mzt start`)
6. Otherwise the CLI reports it busy: `Marianne conductor busy or unresponsive (PID n alive); running state is unknown` (`running_state: unknown`, `conductor_pid: n`), and never suggests `mzt start`, `mzt restart` or `mzt stop --force`. That covers a connect or first write that fails on a socket that exists, a conductor that accepts and does not answer, and a refused connect while the PID is alive. A conductor blocked on slow disk stops accepting while its listen queue fills; each queued connect looks successful and the first write fails with `EINVAL` (GH #455). That conductor is alive.

The detection is **fail-safe** about absence only: absence needs two proofs (no socket, or a refused connection with no live PID), never silence. Every routed read (`status`, `status JOB`, `list`, `errors`, `diagnose`, `history`, `watch`, and `run` submission) goes through the same verdict.

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
- **RecurrenceController + ScheduleRegistry** — Source-owned recurring YAML declarations are projected into the same SQLite state database. The controller arms `CronTick` events through Baton's TimerWheel, leases each `(schedule_id, due_at)` before normal child admission, rereads the source before dispatch, and restores one next or latest action after restart. A missing, changed, or corrupt declaration is removed/replaced/disabled loudly rather than replayed.
- **EventBus** — Async pub/sub that routes runner callback events (`sheet.started`, `sheet.completed`, `sheet.failed`, `sheet.retrying`, `sheet.validation_passed/failed`, `job.cost_update`, `job.iteration`) to downstream consumers. Bounded deques per subscriber prevent slow consumers from blocking publishers.
- **SemanticAnalyzer** — Subscribes to `sheet.completed` and `sheet.failed` events via the EventBus. On each event, captures a snapshot of the sheet's execution context (prompt, output, validation results) and sends it to an LLM (configurable model, default Sonnet) for analysis. The LLM response is parsed into structured insights and stored as `SEMANTIC_INSIGHT` patterns in the global learning store. These patterns are automatically picked up by the existing pattern injection pipeline for future sheets. Concurrency is limited by a semaphore (`max_concurrent_analyses`, default 3). Analysis failures never affect running jobs.

### Recurrence, deadlines, and diagnostics

The conductor owns recurrence; no OS cron or second execution engine is
required. Interval schedules stay anchored to scheduled time, cron schedules
use their declared IANA timezone across DST, `skip` is the default misfire and
overlap behavior, and `latest` collapses downtime to one catch-up action.
Each scheduled child is an ordinary job with schedule lineage.

The effective tick limit is the stricter of the conductor's remaining
`job_timeout_seconds` RUNNING budget and `schedule.max_wall_seconds`.
The tick window is an absolute admission deadline: pending time, pause and
restart consume it. The conductor budget accumulates only RUNNING intervals;
pause and downtime do not consume it. The old root `max_wall_seconds` spelling
remains a warned alias for one release and does not cap a one-shot score.
`mzt status` exposes the public schedule projection: enabled state, last/next
due, last child/outcome, consecutive-drop decisions, and configured/effective
deadline evidence. The private durable due lease identity is not exposed.

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
| `schedule.register` | Register a recurring source and return the daemon-accepted source digest with durable registry identity; does not submit an immediate job |
| `schedule.current` | Read one schedule against its registry identity, ID, and accepted digest |
| `schedule.cancel` | Cancel one bound registration and related active work, then report current state |
| `schedule.register_v2` | Register a recurring source and return a version-2 receipt with a new registration ID |
| `schedule.current_v2` | Read the exact registration ID and require a matching durable terminal before reporting its absence |
| `schedule.cancel_v2` | Cancel the exact registration and related work, then persist its terminal before reporting completion |
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

The schedule methods require available same-UID Unix peer credentials on both
ends of the connection. Their results include `registry_identity`,
`schedule_id`, `source_digest`, `pinned_source_digest`, `state`, `reason`,
`observed_at`, and `cancelled`. `state` is `live`, `disabled`, `running`,
`not_registered`, or `inconclusive`. A bound `not_registered` read is a current
snapshot, not proof that no future registration can occur. Callers must hold
their own lifecycle ordering across submit and erasure decisions. In
particular, `schedule.register` does not replace `job.submit`; if both are
needed, record a pending intent before either call and verify the bound digest
again after submission. Missing methods on an older conductor, transport loss,
wrong registry identity, changed digest, or unfinished work require a safe
inconclusive decision. The registry identity survives service restarts with the
same state database and changes when a new database is served at the socket.

For an irreversible decision such as candidate-data erasure, use the v2 methods
and retain the returned `receipt_version: 2` and `registration_id` outside the
daemon state. Bind later `current_schedule_v2`/`cancel_schedule_v2` calls to
that exact receipt, registry identity, schedule ID and accepted source digest.
The v2 `not_registered` result is supported only by a matching terminal record;
a missing row or replaced database without that terminal is inconclusive.
Another registration receives a new ID and cannot inherit an old terminal.
The v1 methods remain available for compatible callers, but their negative
snapshot is not erasure authority. A candidate caller still needs its own
ordered lifecycle record and cross-process exclusion; the daemon receipt alone
does not authorize deleting a candidate root.

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
| `socket.path` | `Path` | `~/.config/mzt/mzt.sock` | Unix domain socket path |
| `socket.permissions` | `int` | `0o660` | Socket file permissions (octal) |
| `socket.backlog` | `int` | `128` | Max pending connections (kernel caps at `net.core.somaxconn`); drains only on the event loop, so keep it well above the number of concurrent readers |
| `pid_file` | `Path` | `~/.config/mzt/mzt.pid` | PID file path |
| `shutdown_timeout_seconds` | `float` | `300.0` | Max wait for graceful shutdown (5 min) |
| `monitor_interval_seconds` | `float` | `15.0` | Resource check interval |
| `max_job_history` | `int` | `1000` | Terminal jobs kept in memory before eviction |
| `logging.root` | `Path` | `~/.marianne/logs` | Conductor-owned logging root |
| `logging.event_log_name` | `str` | `conductor.log` | Primary conductor event log file under `logging.root` |
| `log_file` | `Path \| None` | *(derived)* | Legacy alias normalized into `logging.root` and `logging.event_log_name` |
| `learning.*` | `SemanticLearningConfig` | *(see below)* | Semantic learning via LLM analysis |

### Semantic Learning (nested under `learning`)

Controls the SemanticAnalyzer — LLM-based analysis of sheet completions that produces insights stored in the learning database.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `true` | Enable semantic learning. Defaults to on when the conductor is running. |
| `instrument` | `str` | `"claude-code"` | Instrument profile that powers the analysis LLM. Any registered instrument works: `claude-code`, `opencode`, `ollama` (free local models), ... |
| `model` | `str \| None` | `None` | Model override for analysis calls. `None` uses the instrument profile's default model. |
| `timeout_seconds` | `float` | `120.0` | Per-analysis LLM call timeout. |
| `analyze_on` | `list` | `["success", "failure"]` | Which outcomes to analyze: `success`, `failure`, or both |
| `max_concurrent_analyses` | `int` | `3` | Max concurrent LLM analysis tasks (1–20) |
| `entropy_threshold` | `float` | `0.1` | Entropy threshold for triggering diversity injection. When pattern diversity index drops below this value (10% of max entropy by default), the learning system automatically boosts exploration budget to escape local optima. |
| `exploration_budget` | `float` | `0.15` | Exploration budget boost amount when entropy response triggers. Controls how aggressively the system explores alternatives when pattern diversity collapses. |

See the [Configuration Reference](configuration-reference.md) for the full field list.

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
| `snapshot_dir` | `Path \| None` | `<state_db_path dir>/snapshots` | Completion snapshots (one directory per job, TTL `observer.snapshot_ttl_hours`). Defaults beside the state database, so a clone or test that isolates `state_db_path` isolates its snapshots (GH #432). |
| `max_concurrent_sheets` | `int` | `25` | Max concurrent sheets across all jobs (enforced by the baton) |
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

logging:
  root: ~/.marianne/logs
  event_log_name: conductor.log
  max_file_size_mb: 50
  backup_count: 5
  compress: true

resource_limits:
  max_memory_mb: 4096
  max_processes: 30

learning:
  enabled: true
  instrument: claude-code
  model: claude-sonnet-4-6
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

A profile file that exists but fails to load keeps its previous registry entry
(an edit accident must not remove a live instrument). Two causes look alike on
disk and need opposite remedies, so the reload result tells them apart
(`failed_profiles[].reason_code`, GH #450): `instrument_yaml_parse_error` and
`instrument_validation_error` mean the file is broken, fix the file;
`instrument_schema_stale` (`schema_stale: true`) means the file names a key the
running conductor's installed schema lacks, so the file is right and the
conductor is older than it: restart at a job-free boundary. Both `mzt doctor`
("Config reload" section) and `mzt conductor-status` ("Config" block) show the
last reload's declined files with that code; `doctor` reads the running
conductor rather than the files on disk, because the file on disk is not what
a retained entry runs.

```bash
# Reload config after editing ~/.marianne/conductor.yaml
kill -SIGHUP $(cat ~/.config/mzt/mzt.pid)
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

In foreground mode, logs are also shown on stderr in console format. The
conductor's durable event log is structured JSON under the configured logging
root. The default event log is `~/.marianne/logs/conductor.log`.

```bash
# View conductor events directly
tail -f ~/.marianne/logs/conductor.log

# Prefer the Marianne log reader for score-specific filtering
mzt logs my-score --follow
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
mzt --conductor-clone start

# Submit a score to the clone
mzt --conductor-clone run my-test-score.yaml

# Check the clone's status
mzt --conductor-clone conductor-status

# Named clones for parallel testing
mzt --conductor-clone=staging start
mzt --conductor-clone=staging run staging-test.yaml

# Stop when done
mzt --conductor-clone stop
```

**Key behaviors:**
- The clone inherits your production `~/.marianne/conductor.yaml` config.
- Clone paths: `~/.config/mzt/clone.sock` (socket), `~/.config/mzt/clone.pid` (PID).
- Named clones: `~/.config/mzt/clone-staging.sock`, etc.
- Clone names are sanitized (64-character limit, safe characters only).
- Commands that don't interact with the conductor (`validate`, `--help`) ignore the flag.

See the [CLI Reference](cli-reference.md#conductor-clones) for full details.

---

## The Baton (Event-Driven Execution Engine)

The baton (`daemon/baton/`) is Marianne's sole execution engine, using
event-driven per-sheet dispatch. The legacy monolithic runner was deleted
in April 2026; there is no toggle and no fallback.

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
  completed sheets' stdout and workspace files
- **Checkpoint sync** — sheet status changes from all event types (escalation, cancellation,
  timeout, rate limit expiry, shutdown) are synchronized back to CheckpointState with
  deduplication to prevent redundant callbacks

**The baton is how the conductor runs.** No configuration needed, no
toggle, no fallback. The historical three-phase transition (prove → make
default → remove the toggle) completed in April 2026 when the legacy
runner and the `use_baton` setting were deleted.

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

The socket at the default path (`~/.config/mzt/mzt.sock`) is missing or refuses
connections, which proves no conductor holds it.

### "Marianne conductor busy or unresponsive (PID n alive); running state is unknown"

The PID file names a live process but its socket did not answer (connect
parked in a full listen queue, first write failed, no reply within the
deadline, or a refused connect while the process is alive). It is alive and
blocked, most often on disk I/O. Do not start or restart it. `mzt
conductor-status` prints the PID, whether it is alive, the host's I/O
pressure (`/proc/pressure/io`, `some`/`full` ten-second averages) even when
the socket is silent, and, once it answers, the event-loop lag (how late the
loop wakes from a one-second sleep; `accept()` runs on that loop). Wait for
the pressure to clear and retry a read-only command.

The same message without a PID ("did not respond; running state is unknown")
means the socket exists but no PID file names a live process: the socket is
the only evidence, and it still is not proof of absence.

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

If the PID file exists but `mzt conductor-status` says the PID is not alive,
the conductor crashed and left the file behind; `mzt stop` cleans it up and
`mzt start` starts a new one. If the PID **is** alive and only the socket is
silent, the conductor is busy (previous entry): do not stop or start it.

A conductor that is *shutting down* (draining active work) no longer reports
"already running": `mzt start` supersedes it — see
[Non-blocking Stop and Bounded Wait](#non-blocking-stop-and-bounded-wait) below.

### Stale PID File

If `mzt stop` reports "conductor is not running" but a PID file exists, the file is automatically cleaned up. If it persists:

```bash
rm ~/.config/mzt/mzt.pid
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

### Resolving FERMATA (escalation) pauses

With `--escalation` (or `--self-healing`), a sheet that exhausts its retries
pauses in FERMATA awaiting a composer decision instead of failing. `mzt status`
lists pending decisions. Resolve with:

```bash
mzt resolve <job_id> <sheet_num> <decision>   # retry | skip | accept | fail
```

or create a marker file under the job workspace
(`markers/fermata/<job_id>/sheet-<N>.<decision>`). FERMATA state survives
conductor restarts; the run's escalation/healing settings are inherited on
resume.

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

### Non-blocking Stop and Bounded Wait

After the active-job safety check and any confirmation, `mzt stop` sends the signal and returns without waiting for the conductor to exit. Scripts that need to know shutdown completed can opt into a bounded wait:

```bash
mzt stop --wait=      # Wait up to 30s for the conductor to exit
mzt stop --wait=5     # Custom budget in seconds
mzt stop              # Signal and return immediately (default)
```

Budgets must be finite and non-negative; `--wait=0` keeps the default signal-and-return behavior. If the conductor does not exit within a positive budget, the command exits 1 with a message naming the still-draining PID. A graceful drain can take up to `shutdown_timeout_seconds` (default 300s) while running jobs checkpoint.

After a `mzt stop`, a fresh `mzt start` supersedes a still-draining conductor: it detects the shutdown state, waits (up to 30s) for the old process to exit, then starts the new one. If the drain exceeds that budget, start exits 1 rather than spawning a second conductor into held locks.
