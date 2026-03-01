# Mozart AI Compose - Active Context

**Last Updated:** 2026-02-25
**Current Phase:** Unified Schema Management System — design approved, implementation plan written
**Status:** Ready to execute Phase 1
**Previous Session:** Schema audit found 35 missing columns, designed and planned the fix

---

## Current State

### What's Done (This Session — 2026-02-25)

**Schema Audit:**
- Audited all 4 SQLite databases against their Pydantic/dataclass models
- Found 35 missing columns in the SQLite state backend (17 SheetState, 18 CheckpointState)
- Found 3 missing columns in profiler storage
- Registry and learning store are clean

**Unified Schema Management Design (approved):**
- Model-driven schema registry: Pydantic model is single source of truth
- Daemon's `mozart.db` becomes sole primary state store (merging registry + state backend)
- Auto-generated migrations from model diffs (`mozart schema diff`)
- CI drift test blocks merge if model fields don't match columns
- `PRAGMA user_version` for version tracking (industry standard)
- Forward-compatible: newer DB version = warning, not error
- JSON backend becomes transitional fallback, eventually deprecated

**Implementation Plan (8 phases):**
1. Schema registry + type converters (foundation)
2. Migration runner (unified for all DBs)
3. Unified state backend (rewrite save/load — HIGHEST RISK)
4. Merge registry + state into mozart.db
5. Migration generator CLI
6. CI drift test
7. Learning store + profiler alignment
8. Legacy import from JSON workspaces

### Documents Created
- `docs/plans/2026-02-25-unified-schema-management-design.md` — Full design with rationale
- `docs/plans/2026-02-25-unified-schema-management-implementation.md` — 8-phase plan with code sketches, gotchas, verification checklist

### Known Bugs / Open Issues

- **35 missing SQLite columns** — THIS IS WHAT THE PLAN FIXES
- **Concert chaining broken** — hooks never fire in daemon mode (design in `docs/plans/2026-02-25-daemon-owned-hooks-and-config-reload-design.md`)
- **Config reload broken** — config_path not wired through daemon
- **#102 — Observer integration gaps**
- **#99 — on_success hooks lost on conductor restart**
- **#98 — `--reload-config` silently dropped**
- **#93 — Pause only fires at sheet boundaries**

---

## Next Steps — Execute Implementation Plan

**START HERE:**
1. Read `docs/plans/2026-02-25-unified-schema-management-design.md`
2. Read `docs/plans/2026-02-25-unified-schema-management-implementation.md`
3. Execute Phase 1 (schema registry + converters)

**Key files to read before writing code:**
- `src/mozart/core/checkpoint.py` — CheckpointState (line 548) and SheetState (line 227)
- `src/mozart/state/sqlite_backend.py` — Current manual save/load being replaced
- `src/mozart/daemon/registry.py` — Registry being unified with state
- `src/mozart/learning/store/base.py` — Best existing migration pattern (lines 97-134)

---

*Context preserved for instant next-session pickup.*
