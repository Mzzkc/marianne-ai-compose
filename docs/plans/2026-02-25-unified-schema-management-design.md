# Unified Schema Management System — Design

**Date:** 2026-02-25
**Status:** Approved
**Problem:** 35 Pydantic model fields missing from SQLite schema due to manual column enumeration with no drift detection

---

## Context

Mozart has 4 SQLite databases with 3 different migration paradigms and no automated schema drift detection. The SQLite state backend manually enumerates columns in 3 places (CREATE TABLE, save INSERT, load SELECT). When developers add fields to Pydantic models, they must update all 3 places — and frequently don't. This caused 35 fields to silently drop from the SQLite backend while the JSON backend preserved them via `model_dump()`.

Mozart is daemon-driven and heading toward distributed fleet orchestration. Proper relational schema with typed, indexed columns is essential for cross-machine queries, aggregation, and analytics at scale.

---

## Architecture

### Database Topology

```
~/.mozart/
├── mozart.db          ← Unified daemon DB (registry + full state + execution history)
├── global-learning.db ← Learning store (separate: different lifecycle, cross-job scope)
└── profiler/
    └── profiler.db    ← Profiler (separate: time-series, aggressive retention/cleanup)
```

The daemon's `mozart.db` absorbs the state backend into the registry:
- `jobs` table: all CheckpointState fields as typed columns (18 new columns)
- `sheets` table: all SheetState fields as typed columns (17 new columns)
- `execution_history` table: unchanged (already complete)

JSON backend becomes a transitional fallback, eventually deprecated.

### Type Mapping (Pydantic → SQLite)

| Python Type | SQLite Type | Conversion |
|-------------|-------------|------------|
| `str`, `Enum(str)` | TEXT | Direct / `.value` |
| `int` | INTEGER | Direct |
| `float` | REAL | Direct |
| `bool` | INTEGER | 0/1 |
| `datetime` | TEXT | ISO format |
| `list[...]`, `dict[...]`, `TypedDict` | TEXT | JSON serialization |

---

## Model-Driven Schema Registry

A registry declares the mapping between Pydantic models and database tables:

```python
SCHEMA_REGISTRY: list[TableMapping] = [
    TableMapping(
        model=CheckpointState,
        table="jobs",
        renames={"job_id": "id", "job_name": "name"},
        exclude={"sheets"},  # sheets live in their own table
    ),
    TableMapping(
        model=SheetState,
        table="sheets",
        extra_columns=[("job_id", "TEXT NOT NULL")],
    ),
]
```

The registry is the single source of truth. From it, the system derives:
- `CREATE TABLE` SQL from model field types
- `INSERT ... ON CONFLICT` SQL from model fields
- `SELECT` → model reconstruction from rows
- Migration diffs: "model has field X, table doesn't → ALTER TABLE ADD COLUMN"

Type converters are declared once and applied automatically:

```python
TYPE_CONVERTERS = {
    datetime: (to_iso_string, from_iso_string),
    bool:     (to_int, from_int),
    list:     (json.dumps, json.loads),
    dict:     (json.dumps, json.loads),
}
```

No more manual column enumeration in save/load methods.

---

## Migration System

### Generation — `mozart schema diff`

CLI command that:
1. Creates in-memory DB, runs all existing migrations → "current" schema
2. Reads model registry → "desired" schema
3. Diffs and generates migration code as a numbered Python file

Developer reviews the generated migration, adjusts defaults or data transforms, commits.

### Tracking — `PRAGMA user_version`

Industry standard for embedded SQLite (Firefox, Android Room, Calibre):
- `PRAGMA user_version` stores current schema version as integer
- Migration files numbered: `v001_initial.py`, `v002_add_config_path.py`, etc.
- Migration runner applies pending versions sequentially on database open

### Version Compatibility

- **Database behind app:** Run pending migrations in `BEGIN EXCLUSIVE` transaction
- **Database ahead of app:** Log warning ("database from newer Mozart version, some fields may not be used"), proceed normally. Additive changes (new columns) are naturally forward-compatible. Only fail if schema is genuinely incompatible (missing required tables).
- **Database at app version:** No-op, proceed

### Enforcement — CI Drift Test

```python
def test_no_schema_drift():
    """Every model field must have a DB column after all migrations."""
    db = sqlite3.connect(":memory:")
    apply_all_migrations(db)

    for mapping in SCHEMA_REGISTRY:
        db_columns = get_columns(db, mapping.table)
        model_fields = get_expected_columns(mapping)

        missing = model_fields - db_columns
        assert not missing, f"{mapping.model.__name__}: missing columns {missing}"
```

This test fails if a developer adds a model field without a corresponding migration. Blocks merge.

---

## Unified Save/Load

Save and load are generated from the registry, eliminating manual column enumeration:

**save(state):**
- Iterate model fields (minus `exclude`)
- Apply type converters (datetime → ISO, list → JSON, bool → int)
- Build INSERT/UPSERT SQL dynamically

**load(job_id):**
- SELECT * from table
- Map columns back to model fields with reverse converters
- Construct Pydantic model via `model_validate()`

Adding a field to a Pydantic model requires exactly two actions:
1. Add the field to the model
2. Run `mozart schema diff`, review migration, commit

Save/load adapts automatically.

---

## Learning Store and Profiler Alignment

### Learning Store

Already has the most mature migration system (schema v13). Changes:
- Add `TableMapping` entries for its 13 tables against dataclass models
- Wire into `test_no_schema_drift` CI test
- Switch from `schema_version` table to `PRAGMA user_version` for consistency
- Keep existing migration infrastructure (it works well)

### Profiler Storage

Currently has no migration system. Changes:
- Add `PRAGMA user_version` tracking
- Add `TableMapping` entries for `SystemSnapshot`, `ProcessMetric`, `ProcessEvent`
- Add 3 missing columns (`zombie_pids`, `job_progress`, `conductor_uptime_seconds`)
- Wire into drift test

### What's Unified

The tooling (registry, drift tests, migration generation) is shared. The databases stay separate — different lifecycles, retention policies, and access patterns.

---

## Bringing Data Forward

The initial migration closing the 35-column gap is purely additive (`ALTER TABLE ADD COLUMN` with defaults). Existing data keeps working.

For jobs previously stored via JSON backend, a `mozart db import-legacy` command backfills from workspace JSON files into the unified DB.

---

## Key Files (Planned)

| Purpose | Path |
|---------|------|
| Schema registry | `src/mozart/schema/registry.py` |
| Type converters | `src/mozart/schema/converters.py` |
| Migration runner | `src/mozart/schema/migrate.py` |
| Migration generator | `src/mozart/schema/diff.py` |
| Drift test | `tests/test_schema_drift.py` |
| Unified state backend | `src/mozart/state/sqlite_backend.py` (rewritten) |
| Legacy import | `src/mozart/schema/import_legacy.py` |
| Migration files | `src/mozart/schema/migrations/v001_initial.py`, ... |

---

## Success Criteria

1. Zero schema drift: CI test catches any model field without a column
2. Single action to add a field: add to model → `mozart schema diff` → commit migration
3. Daemon DB is the sole primary state store
4. All 35 missing columns present with correct types
5. Existing databases self-upgrade on daemon startup
6. Forward-compatible: newer DB version produces warning, not failure

---

*Approved: 2026-02-25*
