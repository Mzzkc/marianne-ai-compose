# Marianne Learning Architecture

**Status:** Phase 1 IMPLEMENTED and evolved through 24+ autonomous self-improvement cycles. Original design Phases 2-6 remain unbuilt proposals.
**Created:** 2025-12-27
**Initial Implementation:** 2026-01-14
**Last Updated:** 2026-04-07

---

## Overview

Marianne's learning system aggregates execution outcomes and patterns across all workspaces, enabling Marianne to learn from every job and improve retry strategies, error handling, and pattern detection over time. All learning data stays local in a SQLite database at `~/.marianne/global-learning.db`. The system has evolved significantly beyond its original Phase 1 design through autonomous self-improvement cycles.

> **Reading guide:** This document has two clearly separated parts. **[Part 1](#part-1-implemented-system)** describes working, tested code (~11,400 lines) that ships today — file paths and module names are verifiable against the codebase. **[Part 2](#part-2-design-proposals-not-implemented)** is the original design document for features that have **not been built** — the file paths, CLI commands, and config structures described there do not exist. Every section is labelled. If you are configuring or debugging Marianne, only Part 1 applies.

---

# Part 1: Implemented System

Everything in this section describes working code. File paths and module names are verifiable.

## Components

The learning system comprises ~11,400 lines of implementation across two packages:

### Core Learning Package (`src/marianne/learning/`)

| Module | LOC | Purpose |
|--------|-----|---------|
| `global_store.py` | 89 | Re-exports from modular store package (backward compat) |
| `store/` (16 modules) | 7,268 | SQLite-backed global learning store (see below) |
| `patterns.py` | 1,267 | Pattern extraction, matching, and application |
| `aggregator.py` | 581 | Cross-workspace pattern merging |
| `error_hooks.py` | 456 | Error classification learning and adaptive wait times |
| `migration.py` | 450 | Workspace outcome import to global store |
| `judgment.py` | 452 | Learning-informed decision making |
| `weighter.py` | 304 | Priority calculation (recency + effectiveness) |
| `outcomes.py` | 403 | Execution outcome recording |

### Global Learning Store (`src/marianne/learning/store/`)

The store was originally a monolithic ~5,136-line module. It has been modularized into 16 files using a mixin architecture:

| Module | LOC | Purpose |
|--------|-----|---------|
| `base.py` | 922 | SQLite connection, schema, migrations, WAL mode |
| `models.py` | 656 | Dataclasses and enums (PatternRecord, ExecutionRecord, etc.) |
| `patterns_crud.py` | 678 | Pattern create/read/update/delete |
| `patterns_query.py` | 289 | Pattern search and filtering |
| `patterns_trust.py` | 248 | Trust scoring for patterns (v19 evolution) |
| `patterns_quarantine.py` | 180 | Quarantine lifecycle (pending/quarantined/validated/retired) |
| `patterns_lifecycle.py` | 272 | Pattern state transitions |
| `patterns_broadcast.py` | 200 | Cross-workspace pattern sharing |
| `patterns_success_factors.py` | 269 | Metacognitive pattern reflection (v22) |
| `budget.py` | 975 | Exploration budget management (v23) |
| `drift.py` | 1,025 | Effectiveness and epistemic drift detection |
| `executions.py` | 714 | Execution outcome recording and querying |
| `escalation.py` | 288 | Escalation decision recording |
| `rate_limits.py` | 236 | Cross-workspace rate limit coordination |
| `patterns.py` | 78 | Pattern mixin aggregation |
| `__init__.py` | 238 | Package exports |

### Daemon Integration

| Module | LOC | Purpose |
|--------|-----|---------|
| `daemon/learning_hub.py` | ~120 | Centralized store for all daemon jobs |
| `daemon/semantic_analyzer.py` | 486 | AI-powered pattern analysis |

The `LearningHub` maintains a single `GlobalLearningStore` instance shared across all concurrent jobs. Pattern discoveries in Job A are immediately visible to Job B. Periodic persistence (60-second heartbeat) replaces per-write flushes.

### Configuration (`src/marianne/core/config/learning.py`)

The `LearningConfig` Pydantic model provides these implemented configuration fields:

| Field | Default | Purpose |
|-------|---------|---------|
| `enabled` | `true` | Master switch for learning |
| `outcome_store_type` | `"json"` | Backend type (`json` or `sqlite`) |
| `outcome_store_path` | `None` | Custom path (default: workspace/.marianne-outcomes.json) |
| `min_confidence_threshold` | `0.3` | Below this triggers escalation |
| `high_confidence_threshold` | `0.7` | Above this uses completion mode |
| `escalation_enabled` | `false` | Enable low-confidence escalation |
| `use_global_patterns` | `true` | Query global store for patterns |
| `exploration_rate` | `0.15` | Epsilon-greedy exploration rate |
| `exploration_min_priority` | `0.05` | Floor for exploration candidates |
| `entropy_alert_threshold` | `0.5` | Low-diversity alert trigger |
| `entropy_check_interval` | `100` | Check every N applications |
| `auto_apply_enabled` | `false` | High-trust auto-apply (deprecated flat field) |
| `auto_apply_trust_threshold` | `0.85` | Trust score for auto-apply (deprecated flat field) |
| `exploration_budget` | `ExplorationBudgetConfig()` | Dynamic budget (v23) |
| `entropy_response` | `EntropyResponseConfig()` | Auto entropy response (v23) |
| `auto_apply` | `None` | Structured auto-apply config (v22, replaces flat fields) |

Additional config models defined in the same file:

- `ExplorationBudgetConfig` — Dynamic exploration budget with floor/ceiling/decay (v23)
- `EntropyResponseConfig` — Automatic diversity injection when entropy drops (v23)
- `AutoApplyConfig` — Trust-aware autonomous pattern application (v22)
- `GroundingConfig` — External grounding hooks (file checksum validation)
- `GroundingHookConfig` — Individual hook configuration (currently supports `file_checksum` type)
- `CheckpointConfig` — Proactive pre-execution checkpoints (v21)
- `CheckpointTriggerConfig` — Trigger conditions for checkpoints (sheet numbers, keywords, retry count)

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           Marianne Instance                              │
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────────────────────┐│
│  │   Runner     │───▶│   Pattern    │───▶│   Global Learning Store     ││
│  │  (executes)  │    │  Aggregator  │    │  (~/.marianne/global-       ││
│  └──────────────┘    └──────────────┘    │   learning.db)              ││
│         │                   │            └─────────────────────────────┘│
│         │                   │                        │                   │
│         ▼                   ▼                        ▼                   │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                   Error Learning Hooks                              │ │
│  │  - Records error classifications and recoveries                    │ │
│  │  - Learns adaptive wait times from recovery success                │ │
│  │  - Shares learned delays across workspaces                         │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│         │                                                                │
│         ▼                                                                │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                   Daemon Learning Hub                               │ │
│  │  - Single GlobalLearningStore shared across concurrent jobs        │ │
│  │  - Instant cross-job pattern visibility                            │ │
│  │  - 60-second heartbeat persistence                                 │ │
│  │  - Semantic analysis of patterns (AI-powered)                      │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### Pattern Lifecycle

Patterns follow a quarantine lifecycle (v19 evolution):

```
PENDING → VALIDATED    (proven effective through repeated application)
PENDING → QUARANTINED  (flagged for review due to failures)
QUARANTINED → VALIDATED (rehabilitated after investigation)
QUARANTINED → RETIRED   (permanently deactivated)
VALIDATED → RETIRED     (no longer relevant)
```

### Trust and Exploration

The system balances exploitation of known-good patterns with exploration of unproven ones:

- **Epsilon-greedy selection:** `exploration_rate` (default 0.15) determines how often lower-priority patterns are tried.
- **Dynamic budget (v23):** When entropy drops, the exploration budget auto-boosts. When entropy is healthy, it decays toward a floor.
- **Trust scoring (v19):** Patterns accumulate trust through successful applications. High-trust patterns (>0.85) can be auto-applied without human confirmation.
- **Drift detection:** Tracks effectiveness drift (are patterns degrading?) and epistemic drift (is the system's knowledge becoming stale?).

## CLI Commands

```bash
# View global patterns
mzt patterns-list [--min-priority 0.0] [--limit N]

# Why patterns succeed (metacognitive analysis)
mzt patterns-why

# Pattern diversity metrics
mzt patterns-entropy

# Exploration budget status
mzt patterns-budget

# Learning statistics
mzt learning-stats

# Learning insights
mzt learning-insights

# Effectiveness drift detection
mzt learning-drift

# Epistemic drift detection
mzt learning-epistemic-drift

# Recent learning activity
mzt learning-activity

# Export patterns
mzt learning-export [--output FILE]

# Record evolution trajectory
mzt learning-record-evolution

# Entropy system status
mzt entropy-status
```

## Test Coverage

The learning system has extensive test coverage across 16 dedicated test files (~19,500 lines):

- `test_global_learning.py` — Comprehensive store tests (patterns, executions, trust, quarantine)
- `test_learning_executions.py` — Execution recording and similarity matching
- `test_learning_budget.py` — Exploration budget dynamics and entropy response
- `test_learning_store_base.py` — SQLite schema, migrations, WAL mode
- `test_learning_aggregator.py` — Outcome aggregation into global patterns
- `test_learning_drift.py` — Effectiveness and epistemic drift detection
- `test_learning_weighter.py` — Priority calculations
- `test_learning_migration_judgment.py` — Outcome store migration
- `test_error_learning_hooks.py` — Error code recovery learning
- `test_learning_store_fk_migration.py` — Foreign key constraint handling
- `test_learning_e2e.py` — End-to-end pattern detection and application
- `test_learning_export_filtering.py` — Data export with filtering
- `test_daemon_learning_hub.py` — Async hub lifecycle
- `test_cli_learning.py` — CLI command validation
- `test_cli_learning_export.py` — Export command validation
- `test_learning_store_priority_and_fk.py` — Priority calculations and FK migrations

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Global Store Location | SQLite at `~/.marianne/global-learning.db` | Single-file, no server, WAL mode for concurrent access |
| Aggregation Trigger | Immediate on job completion | Patterns available instantly for next job |
| Pattern Weighting | Combined recency + effectiveness | Recent successes weighted higher than old ones |
| Error Learning | Hook-based extension of ErrorClassifier | Non-invasive integration with existing error handling |
| Store Architecture | Mixin-based modular design | Original 5,136-line monolith was unmaintainable |
| Daemon Integration | Centralized LearningHub | Single store instance prevents SQLite lock contention |

## Evolution History

The learning system has evolved through Marianne's autonomous self-improvement cycles. Key evolutions visible in the code:

| Evolution | Feature Added |
|-----------|---------------|
| v8 | Cross-workspace rate limit coordination |
| v11 | Escalation learning loop |
| v12 | Goal drift detection |
| v14 | Real-time pattern broadcasting |
| v19 | Pattern quarantine lifecycle, trust scoring, provenance tracking |
| v21 | Epistemic drift detection, proactive checkpoints, pattern entropy monitoring |
| v22 | Metacognitive pattern reflection (success factors), trust-aware autonomous application |
| v23 | Exploration budget maintenance, automatic entropy response |

These evolutions extended the system far beyond its original Phase 1 design. The proposed Phases 2-6 (anonymization, GitHub contribution, sync) were never implemented; instead, the system evolved in a different direction — toward self-awareness and autonomous pattern management.

---

# Part 2: Design Proposals (Not Implemented)

> **Everything below this line describes aspirational designs from the original 2025-12-27 design document (Phases 2-6). None of this code exists. The following proposed files were never created:** `adaptation.py`, `preflight.py`, `improvements.py`, `quality.py`, `anonymize.py`, `contribute.py`, `sync.py`, `database.py`. **The config structures and CLI commands shown below are proposals, not references to real code.**

The original design envisioned six phases. Only Phase 1 (local learning foundation) was implemented — and the actual implementation diverged significantly from the original design, evolving through 24 autonomous self-improvement cycles into the system described in Part 1. Phases 2-6 remain unbuilt.

## Proposed: Preflight Learning (Before Execution)

Query the local database for similar past executions before running a sheet. Identify patterns that led to failures and apply learned mitigations automatically.

```yaml
# PROPOSED config — does not exist
learning:
  preflight:
    enabled: true
    check_similar_failures: true
    apply_learned_patterns: true
    warn_on_risky_patterns: true
```

Proposed preflight output:
```
Sheet 3 preflight:
  ! Similar sheet failed 2/3 times historically
  ! Pattern detected: "validation_markers_missing" (80% failure rate)
  Applied mitigation: Added explicit marker format to prompt
  Adjusted timeout: 1800s -> 2400s (based on timing patterns)
```

## Proposed: Mid-Run Adaptation (During Execution)

On validation failure, query patterns for this failure type and apply learned recovery strategies to modify the prompt for retry.

```yaml
# PROPOSED config — does not exist
learning:
  mid_run:
    enabled: true
    adapt_on_failure: true
    max_adaptations: 2
```

## Proposed: Post-Run Improvement Detection

Analyze completed jobs for improvement opportunities. Generate suggestions, filter through quality gates, and queue for contribution.

Proposed deliverables:
- `src/marianne/learning/improvements.py` — Improvement detection
- `src/marianne/learning/quality.py` — Quality gates

## Proposed: Anonymization Layer

Strip PII from patterns before any external sharing. Hash identifiers for correlation without exposure.

Fields to strip: PIDs, usernames, absolute paths, API keys, environment variables, hostnames, IP addresses, stdout/stderr content.

Fields to hash: job names, workspace paths, project paths.

Fields to keep: pattern names, validation types, success rates, retry counts, config structure (no values).

Proposed deliverables:
- `src/marianne/learning/anonymize.py` — Anonymization logic

## Proposed: GitHub Contribution Pipeline

Automatically generate PRs from locally-learned improvements:

1. Improvement detected (pattern X causes failure, modification Y fixes it)
2. Quality gate (minimum sample size, minimum confidence, not already contributed)
3. Anonymization (strip PII, hash identifiers, normalize paths)
4. Contribution preparation (generate diff, write evidence summary)
5. GitHub PR creation (fork, branch, commit, open PR)
6. Human review by maintainer

Proposed deliverables:
- `src/marianne/learning/contribute.py` — GitHub contribution logic

## Proposed: Learning Sync

Pull merged learnings from the GitHub repository and apply to the local database.

Proposed CLI command: `mzt learning sync`

Proposed deliverables:
- `src/marianne/learning/sync.py` — Learning synchronization

## Proposed: Centralized Learning Store

Three options were considered:

**Option A: Shared PostgreSQL** — Real-time sharing, centralized statistics. Cons: requires network, privacy concerns, hosting cost.

**Option B: Git-Based Federation** (recommended for start) — Works offline, full transparency, human review via PRs. Cons: async, requires GitHub access.

**Option C: Hybrid** — Local SQLite syncs periodically to central PostgreSQL, materialized to git for transparency.

## Open Design Questions

1. **Should code changes ever be auto-contributed?** Current proposal: No, always requires PR review.
2. **How to handle conflicting learnings?** Different instances may learn opposite things. Needs conflict resolution.
3. **Rate limiting contributions?** Prevent spam from misconfigured instances.
4. **Versioning learnings?** Patterns may become obsolete as Marianne evolves.

---

*Initial implementation 2026-01-14. System evolved through 24+ autonomous cycles (v8-v23). Design proposals (Phases 2-6) remain unimplemented as of 2026-04-07.*
