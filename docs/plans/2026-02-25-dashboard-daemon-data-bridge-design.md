# Dashboard ↔ Daemon Data Bridge

**Date:** 2026-02-25
**Status:** Approved

## Problem

The Mozart Dashboard reads job data from a local SQLite `StateBackend`. The conductor daemon manages all real jobs via its own registry, event bus, profiler, and learning systems. Result: the dashboard shows stale or missing data while the daemon has 500+ fields of rich, real-time information that never reaches the browser.

## Design

Replace the dashboard's data source with the daemon itself. Four backend service layers feed enriched data to existing and new frontend views.

## Architecture

```
Browser (htmx + Alpine.js + SSE)
    │
    ├── Existing routes (enhanced)
    │   ├── /api/dashboard/stats     ← DaemonAnalytics
    │   ├── /api/dashboard/recent    ← DaemonStateAdapter
    │   ├── /api/dashboard/system    ← DaemonSystemView
    │   ├── /jobs/list               ← DaemonStateAdapter
    │   ├── /api/jobs/{id}/status    ← DaemonStateAdapter
    │   ├── /api/jobs/{id}/stream    ← DaemonEventBridge
    │   └── /api/monitor/snapshot    ← DaemonSystemView
    │
    ├── New routes
    │   ├── /api/events/stream       ← DaemonEventBridge (SSE)
    │   ├── /api/analytics/*         ← DaemonAnalytics
    │   ├── /api/jobs/{id}/observer  ← DaemonEventBridge
    │   ├── /api/system/rate-limits  ← DaemonSystemView
    │   └── /api/system/pressure     ← DaemonSystemView
    │
    └── New page
        └── /analytics               ← Analytics page

DaemonStateAdapter ──┐
DaemonEventBridge  ──┤
DaemonAnalytics    ──┼── DaemonClient (IPC) ── Unix Socket ── Conductor
DaemonSystemView   ──┘
```

## Backend Layers

### Layer 1: DaemonStateAdapter

Implements `StateBackend` protocol over `DaemonClient`. Read-only — write methods raise `NotImplementedError`.

| StateBackend method | DaemonClient call |
|---|---|
| `list_jobs()` | `job.list` + `job.status` per active job |
| `load(job_id)` | `job.status` → `CheckpointState` |
| `save/delete/mark_sheet_status/get_next_sheet` | `NotImplementedError` |

`list_jobs()` calls `job.list` for the job roster, then `job.status` per active job for full `CheckpointState` data. Terminal jobs already have rich checkpoint data in the registry. Active jobs are typically <10, and IPC is local socket (microseconds per call).

**File:** `src/mozart/dashboard/state/daemon_adapter.py`

### Layer 2: DaemonEventBridge

Bridges daemon EventBus events to browser SSE streams.

**Events surfaced:**
- `sheet.started`, `sheet.completed`, `sheet.failed`, `sheet.retrying`
- `sheet.validation_passed`, `sheet.validation_failed`
- `job.cost_update`, `job.iteration`
- `observer.file_created`, `observer.file_modified`, `observer.file_deleted`
- `monitor.anomaly`

**Implementation:** Polls `daemon.observer_events` and `daemon.events` IPC methods on a configurable interval (1-3s), transforms into SSE format. Alternative: new `daemon.events.subscribe` streaming IPC method for true push.

**File:** `src/mozart/dashboard/services/event_bridge.py`

### Layer 3: DaemonAnalytics

Computes aggregated statistics from daemon job data.

**Endpoints:**
- `get_stats()` — total jobs, running, completed, failed, success rate, total spend, throughput
- `cost_rollup()` — cost by job, by day, cost per successful outcome
- `validation_stats()` — pass rates by rule type, by sheet position, by job
- `error_breakdown()` — transient vs rate_limit vs permanent, trends
- `duration_stats()` — avg sheet duration, variance, slowest sheets

**Caching:** TTL-based cache (configurable, default 10s) to avoid hammering IPC on concurrent page loads.

**File:** `src/mozart/dashboard/services/analytics.py`

### Layer 4: DaemonSystemView

Live system health from daemon profiler and controllers.

**Data sources:**
- `daemon.top` → `SystemSnapshot` (memory, load, processes, GPUs, zombies)
- `daemon.status` → uptime, running jobs, memory usage
- `daemon.rate_limits` (new IPC) → per-backend rate limit state
- `daemon.learning.patterns` (new IPC) → recent learning insights

**Replaces:** Current `monitor.db` file polling in monitor routes.

**File:** `src/mozart/dashboard/services/system_view.py`

## New Daemon IPC Methods

| Method | Returns | Source |
|--------|---------|--------|
| `daemon.rate_limits` | `{backend: {events_count, next_window_seconds, active}}` | `RateLimitCoordinator` |
| `daemon.learning.patterns` | `[{pattern_id, description, confidence, applied_count}]` | `LearningHub` |

## Frontend Changes

### Index Page (Dashboard Home)

**Enhanced:**
- Stats cards pull from `DaemonAnalytics` — accurate counts + **Total Spend** + **Throughput** cards
- Active jobs panel shows progress rings, live cost, sheet count, ETA
- Recent activity enriched with cost, duration, validation pass rate
- System resources from daemon profiler (memory, load, pressure)

**New:**
- Event timeline panel — last 50 events across all jobs, color-coded by type

### Jobs List Page

**Enhanced:**
- Each row: progress bar, sheets X/Y, cost, duration, retry count, validation rate
- Sortable columns
- Aggregate footer: total cost, average success rate

### Job Detail Page

**New sections:**
- Cost breakdown per sheet
- Validation heatmap — which rules pass/fail across sheets
- Error history timeline with classification badges (transient/rate_limit/permanent)
- Observer feed — files the agent created/modified in workspace
- Semantic insights cards from LearningHub patterns
- Sheets table enhanced with per-sheet cost, duration, confidence score

### Monitor Page

**New panels:**
- Rate limit gauges per backend
- Backpressure indicator (color-coded: NONE → LOW → MEDIUM → HIGH → CRITICAL)
- Anomaly feed with severity badges

### New: Analytics Page (`/analytics`)

- Hero numbers: Total Spend, Success Rate, Avg Cost/Job, Throughput (with trend arrows)
- Cost trend by day/week
- Validation pass rates across all jobs
- Error classification breakdown
- Learning patterns timeline
- Duration distribution

## File Inventory

### New Files

| File | Purpose |
|------|---------|
| `src/mozart/dashboard/state/__init__.py` | Package init |
| `src/mozart/dashboard/state/daemon_adapter.py` | StateBackend over DaemonClient |
| `src/mozart/dashboard/services/event_bridge.py` | EventBus → SSE bridge |
| `src/mozart/dashboard/services/analytics.py` | Aggregated stats engine |
| `src/mozart/dashboard/services/system_view.py` | System health from daemon |
| `src/mozart/dashboard/routes/analytics.py` | Analytics API endpoints |
| `src/mozart/dashboard/routes/events.py` | Event stream endpoints |
| `src/mozart/dashboard/routes/system.py` | System health endpoints |
| `src/mozart/dashboard/templates/pages/analytics.html` | Analytics page |
| `src/mozart/dashboard/templates/partials/event_timeline.html` | Event feed |
| `src/mozart/dashboard/templates/partials/cost_breakdown.html` | Cost display |
| `src/mozart/dashboard/templates/partials/validation_heatmap.html` | Validation grid |
| `src/mozart/dashboard/templates/partials/observer_feed.html` | File events |
| `src/mozart/dashboard/templates/partials/rate_limits.html` | Rate limit gauges |
| `src/mozart/dashboard/templates/partials/pressure_gauge.html` | Backpressure display |
| `src/mozart/dashboard/templates/partials/analytics_*.html` | Analytics partials |

### Modified Files

| File | Changes |
|------|---------|
| `src/mozart/dashboard/app.py` | Swap StateBackend for DaemonStateAdapter, register new routers |
| `src/mozart/dashboard/routes/dashboard.py` | Use DaemonAnalytics for stats |
| `src/mozart/dashboard/routes/stream.py` | Use DaemonEventBridge |
| `src/mozart/dashboard/routes/monitor.py` | Use DaemonSystemView |
| `src/mozart/dashboard/routes/__init__.py` | Update models for enriched fields |
| `src/mozart/dashboard/templates/pages/index.html` | Event timeline, enhanced cards |
| `src/mozart/dashboard/templates/pages/jobs_list.html` | Enriched rows, sortable |
| `src/mozart/dashboard/templates/pages/job_detail.html` | Cost, validation, observer, insights |
| `src/mozart/dashboard/templates/pages/monitor.html` | Rate limits, pressure, anomalies |
| `src/mozart/dashboard/templates/components/header.html` | Add Analytics nav item |
| `src/mozart/daemon/process.py` | Register `daemon.rate_limits`, `daemon.learning.patterns` |
| `src/mozart/daemon/ipc/client.py` | Add `rate_limits()`, `learning_patterns()` convenience methods |

## Verification

1. All existing dashboard tests pass (`pytest tests/test_dashboard*.py`)
2. All pages render HTTP 200 with accurate data from daemon
3. Browser verification (Playwright): Tailwind styled, no CSP violations, no console errors
4. SSE streams deliver real-time events
5. Analytics page computes correct aggregates
6. Rate limit and pressure panels reflect daemon state
7. Observer feed shows file events for active jobs
