# Mozart System Monitor ("mozart htop") — Design Document

**Date:** 2026-02-23
**Status:** Approved
**Scope:** Real-time system monitor + logging + semantic learning integration + MCP cleanup

---

## Problem Statement

Mozart's daemon has internal resource monitoring (`ResourceMonitor`, `SystemProbe`) but none of it is surfaced to the user in real-time. When Mozart misbehaves — memory leaks, zombie processes, runaway Claude CLI instances, unexplained failures — the user must manually run `ps`, `strace`, `dmesg`, and parse JSON state files to diagnose what happened.

We need full, real-time and logged visibility into Mozart's system impact, with the data feeding back into the semantic learning system to build predictive resource models.

---

## Architecture: B-Hybrid (Daemon Collects, Separate Storage)

The daemon runs the collection (it already tracks processes, has EventBus, ProcessGroupManager). Data is written to persistent SQLite + JSONL storage that `mozart top` and the dashboard read independently. strace runs as a child process managed by the daemon.

```
┌─────────────────────────────────────────────────────┐
│                 Mozart Daemon Process                 │
│                                                       │
│  ResourceMonitor ─── ProfilerCollector ──► SQLite DB │
│       │                    │                  │       │
│       │              StraceManager            │       │
│       │              GpuProbe                 │       │
│       │                    │                  │       │
│       ▼                    ▼                  │       │
│    EventBus ◄──── AnomalyDetector             │       │
│       │                    │                  │       │
│       ▼                    ▼                  │       │
│  SemanticAnalyzer   CorrelationAnalyzer       │       │
│       │                    │                  │       │
│       ▼                    ▼                  │       │
│    LearningHub ◄───────────┘                  │       │
│       │                                       │       │
│       ▼                                       │       │
│  BackpressureController (scheduling hints)    │       │
└───────────────────────────────────────────────┘       │
                                                        │
┌─────────────── Independent Readers ──────────────┐    │
│  mozart top (TUI)     ◄──── reads ────────────────────┤
│  mozart top --json    ◄──── reads ────────────────────┤
│  Dashboard SSE        ◄──── reads ────────────────────┤
│  mozart diagnose --resources  ◄── reads ──────────────┘
└──────────────────────────────────────────────────┘
```

---

## Data Collection Layer

### Collection Interval

Default: 5 seconds (configurable via `DaemonConfig.profiler.interval_seconds`).

### Metrics Collected Per Snapshot

| Metric | Source | Notes |
|--------|--------|-------|
| System memory (total/available/used/swap) | psutil / /proc/meminfo | |
| Daemon RSS | psutil.Process(daemon_pid) | |
| Per-child CPU% | psutil.Process.cpu_percent() | Each Mozart child |
| Per-child memory (RSS, VMS) | psutil.Process.memory_info() | Each Mozart child |
| Per-child thread count | psutil.Process.num_threads() | |
| Per-child open FDs | psutil.Process.num_fds() | |
| Per-child state | psutil.Process.status() | R/S/D/Z/T |
| Per-child command line | psutil.Process.cmdline() | |
| GPU utilization % | pynvml / nvidia-smi fallback | Per-GPU, graceful skip if no GPU |
| GPU memory used | pynvml | Per-GPU |
| GPU temperature | pynvml | Per-GPU |
| Process tree | psutil.Process.children(recursive=True) | PID, PPID, job association |
| Syscall summary | strace -c -p PID | Counts + time% per syscall type |
| Load average | os.getloadavg() | 1/5/15 min |
| Pressure level | BackpressureController.current_level() | NONE/LOW/MEDIUM/HIGH/CRITICAL |
| Running jobs / active sheets | JobManager | |

### Process Lifecycle Events

Captured via EventBus subscription + waitpid:
- `spawn`: PID, command, job_id, sheet_num, timestamp
- `exit`: PID, exit_code, duration, job_id, sheet_num
- `signal`: PID, signal number, reason (rate limit, timeout, user cancel)
- `kill`: PID, signal, reason
- `oom`: PID, dmesg correlation

### strace Management

- `strace -c -p PID` attached to each Mozart child process at spawn time
- Produces syscall count + time distribution summaries
- On-demand full trace: `mozart top --trace PID` attaches `strace -f -t -p PID`
- strace processes are tracked in ProcessGroupManager for cleanup
- Graceful skip if strace unavailable (container environments)

### GPU Probe

Priority chain:
1. `pynvml` (NVIDIA Management Library bindings) — fast, no subprocess
2. `nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader` — subprocess fallback
3. No GPU data (silent skip, `gpu_count=0`)

### New Files

```
src/mozart/daemon/profiler/
├── __init__.py           # Package exports
├── collector.py          # ProfilerCollector: enhanced snapshot collection
├── storage.py            # MonitorStorage: SQLite time-series writer + reader
├── strace_manager.py     # StraceManager: attach/detach strace per child PID
├── gpu_probe.py          # GpuProbe: pynvml/nvidia-smi/none chain
├── models.py             # Pydantic models: Snapshot, ProcessMetric, ProcessEvent, Anomaly
├── anomaly.py            # AnomalyDetector: heuristic anomaly detection
└── correlation.py        # CorrelationAnalyzer: periodic statistical analysis
```

---

## Storage Layer

### SQLite Time-Series Database (`~/.mozart/monitor.db`)

```sql
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY,
    timestamp REAL NOT NULL,
    daemon_pid INTEGER,
    system_memory_total_mb REAL,
    system_memory_available_mb REAL,
    system_memory_used_mb REAL,
    daemon_rss_mb REAL,
    child_count INTEGER,
    zombie_count INTEGER,
    load_avg_1 REAL,
    load_avg_5 REAL,
    load_avg_15 REAL,
    gpu_count INTEGER,
    gpu_utilization_pct TEXT,         -- JSON array
    gpu_memory_used_mb TEXT,          -- JSON array
    gpu_temperature TEXT,             -- JSON array
    pressure_level TEXT,
    running_jobs INTEGER,
    active_sheets INTEGER
);

CREATE TABLE process_metrics (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER REFERENCES snapshots(id),
    pid INTEGER NOT NULL,
    ppid INTEGER,
    command TEXT,
    state TEXT,
    cpu_percent REAL,
    rss_mb REAL,
    vms_mb REAL,
    threads INTEGER,
    open_fds INTEGER,
    age_seconds REAL,
    job_id TEXT,
    sheet_num INTEGER,
    syscall_counts TEXT,              -- JSON: {"write": 1500, ...}
    syscall_time_pct TEXT             -- JSON: {"write": 0.4, ...}
);

CREATE TABLE process_events (
    id INTEGER PRIMARY KEY,
    timestamp REAL NOT NULL,
    pid INTEGER NOT NULL,
    event_type TEXT NOT NULL,         -- spawn/exit/signal/kill/oom
    exit_code INTEGER,
    signal_num INTEGER,
    job_id TEXT,
    sheet_num INTEGER,
    details TEXT                      -- JSON
);

CREATE INDEX idx_snapshots_ts ON snapshots(timestamp);
CREATE INDEX idx_process_metrics_snapshot ON process_metrics(snapshot_id);
CREATE INDEX idx_process_metrics_job ON process_metrics(job_id);
CREATE INDEX idx_process_events_ts ON process_events(timestamp);
CREATE INDEX idx_process_events_job ON process_events(job_id);
```

### Retention Policy

- Snapshots + process_metrics: 24 hours at full 5s resolution (~17,280 rows)
- Downsampled to 1-minute averages for 7 days
- Process events: 30 days (sparse, only lifecycle events)
- Cleanup runs hourly via ResourceMonitor loop

### JSONL Stream (`~/.mozart/monitor.jsonl`)

- Each snapshot appended as one JSON line for streaming consumers
- Rotated at 50MB (keep 2 rotated files)

---

## TUI / CLI Interface

### `mozart top` — Rich TUI (Textual)

Job-centric flight recorder layout:

```
┌─────────────────── Mozart Monitor ───────────────────────────────┐
│ ● Conductor: UP 2h15m  Memory: ██░░ 30%  CPU: █░░░ 12%  GPU: — │
│ Pressure: LOW  Jobs: 2/4  Sheets: 3 active  ⚠ 1 anomaly        │
├──────────────────────── Active Jobs ─────────────────────────────┤
│                                                                   │
│ ▶ my-review-job         Sheet 3/6 ██████████░░░░░ 50%            │
│   ├ S3 [RUNNING]  PID 12350  CPU 45%  MEM 512M  4m30s           │
│   │   syscalls: write 40% | read 28% | futex 15%                │
│   ├ S4 [RUNNING]  PID 12380  CPU 12%  MEM 256M  2m10s           │
│   │   syscalls: read 55% | poll 20% | write 12%                 │
│   ├ S2 [DONE ✓]   2m45s  exit=0  validations: 4/4 passed       │
│   └ S1 [DONE ✓]   3m12s  exit=0  validations: 3/3 passed       │
│                                                                   │
│ ▶ code-cleanup-job      Sheet 1/3 ████░░░░░░░░░░░ 33%           │
│   └ S1 [RUNNING]  PID 12400  CPU 8%  MEM 128M  1m05s            │
│     syscalls: read 60% | write 15% | stat 10%                   │
│                                                                   │
├──────────────────── Event Timeline ──────────────────────────────┤
│ 14:32:15  ● SPAWN   my-review/S4     PID 12380                  │
│ 14:30:02  ● EXIT    my-review/S2     PID 12340  exit=0  2m45s   │
│ 14:28:11  ⚠ SIGNAL  my-review/S1     PID 12330  SIGTERM (rlim)  │
│ 14:28:11  ⚠ ANOMALY my-review/S3     Memory +200MB in 30s       │
│ 14:25:00  🧠 LEARN  "S3 memory spikes correlate with file ops"  │
├──────────────────── Detail (↑↓ select, Enter drill) ─────────────┤
│ [Select a process/event to see: full strace, logs, validation    │
│  details, resource history graph, or learning correlations]      │
└──────────────────────────────────────────────────────────────────┘
```

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| j/k, ↑↓ | Navigate process/event list |
| Enter | Drill into selected item (strace, logs, details) |
| t | Attach full strace to selected process |
| s | Cycle sort: CPU → MEM → AGE → SYSCALL |
| f | Filter by job_id |
| l | Show learning insights for selected job |
| h | Toggle historical mode (browse past snapshots) |
| / | Search events/processes |
| q | Quit |

### `mozart top --json` — Streaming NDJSON

Complete JSON object per snapshot (see AI-Readable Output section).

### `mozart top --history <duration>` — Historical Replay

Reads from SQLite and replays snapshots at configurable speed. Useful for post-mortem analysis.

### Dashboard Integration

New SSE endpoint `/api/monitor/stream` serves the same NDJSON data. Dashboard "System" tab renders the job-centric view.

### New Files

```
src/mozart/cli/commands/top.py       # CLI command entry
src/mozart/tui/                      # New TUI package
├── __init__.py
├── app.py                           # Textual Application
├── panels/
│   ├── __init__.py
│   ├── header.py                    # System summary bar
│   ├── jobs.py                      # Job-centric process tree
│   ├── timeline.py                  # Event timeline
│   └── detail.py                    # Drill-down detail panel
└── reader.py                        # Reads from SQLite/JSONL/IPC
```

---

## AI-Readable Output Layer

### 1. `mozart top --json` (streaming)

Full snapshot JSON per 5s interval. No truncation. Includes: system metrics, per-process data with syscall summaries, events since last snapshot, active anomalies, recent learning insights.

### 2. `mozart diagnose --resources <job-id>` (one-shot)

Comprehensive resource profile for a specific job:
- Peak memory per sheet
- Total CPU-time per sheet
- Process spawn count, retry count
- Signals sent and reasons
- Zombie/OOM events
- Syscall hotspots per sheet
- Anomalies detected during job
- Learning correlations

### 3. Enriched `mozart.log`

Every sheet event (started, completed, failed, retrying) includes a `resource_context` field:
```json
{"event": "sheet.failed", "job_id": "x", "sheet_num": 3,
 "resource_context": {"rss_mb": 512, "cpu_pct": 45, "syscall_hotspot": "write 40%"},
 "anomalies_active": ["memory_spike"]}
```

---

## Semantic Learning Integration

### New PatternTypes

```python
RESOURCE_ANOMALY = "resource_anomaly"
"""Resource anomaly detected during execution."""

RESOURCE_CORRELATION = "resource_correlation"
"""Learned correlation between resource usage and outcomes."""
```

### Flow 1: Anomaly → Learning (immediate, no LLM)

1. `AnomalyDetector` runs on each snapshot, comparing against recent history
2. Detects: memory spikes (>50% increase in 30s), runaway processes (>90% CPU for >60s), zombies, FD exhaustion
3. Publishes `monitor.anomaly` event on EventBus
4. `SemanticAnalyzer` picks up anomaly events, stores as `RESOURCE_ANOMALY` patterns in LearningHub
5. No LLM call — pure heuristic detection

### Flow 2: Correlation → Learning (periodic batch, no LLM)

1. `CorrelationAnalyzer` runs every 30 minutes (configurable)
2. Queries SQLite for resource profiles of completed jobs
3. Cross-references with job outcomes (success/failure, validation results)
4. Identifies statistical patterns:
   - "Processes with peak RSS >X tend to fail Y% of the time"
   - "Jobs where write() dominates syscalls (>35% time) correlate with validation failures"
   - "Sheet execution >5 minutes correlates with memory pressure"
5. Stores as `RESOURCE_CORRELATION` patterns (confidence based on sample size)

### Flow 3: Resource-Aware Scheduling Hints

1. `BackpressureController.estimate_job_resource_needs()` queries LearningHub for `RESOURCE_CORRELATION` patterns matching the incoming job
2. Adjusts pressure thresholds: if a job type historically needs 2GB, start throttling earlier
3. Returns `ResourceEstimate` with predicted peak memory, CPU-time, confidence

### Integration Points

| Component | Role | Direction |
|-----------|------|-----------|
| ProfilerCollector | Collects snapshots | Writes → SQLite + JSONL |
| AnomalyDetector | Heuristic anomaly detection | Reads ← snapshots, Publishes → EventBus |
| CorrelationAnalyzer | Statistical analysis (periodic) | Reads ← SQLite, Writes → LearningHub |
| SemanticAnalyzer | Subscribes to anomaly events | Reads ← EventBus, Writes → LearningHub |
| BackpressureController | Consumes resource predictions | Reads ← LearningHub |
| EventBus | Routes monitor.anomaly events | Daemon-wide |

---

## Configuration

New `profiler` section in `DaemonConfig`:

```yaml
profiler:
  enabled: true                    # Master switch
  interval_seconds: 5              # Collection interval
  strace_enabled: true             # Attach strace to child processes
  strace_full_on_demand: true      # Allow full -f trace via mozart top
  gpu_enabled: true                # Attempt GPU probing
  storage_path: ~/.mozart/monitor.db
  jsonl_path: ~/.mozart/monitor.jsonl
  jsonl_max_bytes: 52428800        # 50MB rotation
  retention:
    full_resolution_hours: 24
    downsampled_days: 7
    events_days: 30
  anomaly:
    memory_spike_threshold: 1.5    # 50% increase triggers anomaly
    memory_spike_window_seconds: 30
    runaway_cpu_threshold: 90      # % CPU
    runaway_duration_seconds: 60
  correlation:
    interval_minutes: 30           # How often to run correlation analysis
    min_sample_size: 5             # Minimum jobs before generating correlations
```

---

## MCP Server Cleanup

Separate from the monitor feature. Remove unused external MCP servers from Claude plugin config.

**Keep:**
- `github` — actively used for issue tracking, PR management
- `playwright` — actively used for browser automation

**Remove (11 servers):**
- `greptile`, `stripe`, `context7`, `supabase`, `asana`, `firebase`, `serena`, `slack`, `linear`, `laravel-boost`, `gitlab`

These are registered in `/home/emzi/.claude/plugins/marketplaces/claude-plugins-official/external_plugins/` as directories containing `.mcp.json` files. Removal is done by unregistering them from the plugins marketplace or removing the directories.

---

## Dependencies

New Python packages needed:
- `textual` — TUI framework (Rich-based, async-native)
- `pynvml` — NVIDIA GPU monitoring (optional, graceful skip)

Already available:
- `psutil` — System probing (already in use)
- `rich` — Already a dependency (textual builds on it)

---

## Testing Strategy

- Unit tests for each profiler component (collector, storage, strace manager, GPU probe, anomaly detector, correlation analyzer)
- Integration test: daemon + profiler + learning store round-trip
- TUI smoke test: launch `mozart top`, verify data renders
- strace tests: mock subprocess to avoid requiring strace in CI

---

## Implementation Phases

1. **Storage + Models** — SQLite schema, Pydantic models, MonitorStorage class
2. **Collector + SystemProbe extensions** — ProfilerCollector, GpuProbe, per-process metrics
3. **strace Manager** — Attach/detach strace per child PID, parse summary output
4. **Anomaly Detection** — AnomalyDetector, EventBus integration
5. **TUI** — Textual app with job-centric layout, reader from SQLite/JSONL
6. **CLI commands** — `mozart top`, `mozart top --json`, `mozart diagnose --resources`
7. **Correlation Analyzer** — Periodic statistical analysis, LearningHub integration
8. **Scheduling Hints** — BackpressureController extension
9. **Dashboard SSE** — `/api/monitor/stream` endpoint
10. **Log Enrichment** — Add resource_context to sheet events in mozart.log
11. **MCP Cleanup** — Remove unused external MCP servers
