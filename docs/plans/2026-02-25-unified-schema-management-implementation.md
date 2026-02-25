# Unified Schema Management — Implementation Plan

**Date:** 2026-02-25
**Design:** `docs/plans/2026-02-25-unified-schema-management-design.md`
**Prereq:** Read the design doc FIRST. It contains the full rationale and architectural decisions.

---

## Critical Context for the Implementing Agent

You are implementing a unified schema management system for Mozart. The core problem: 35 Pydantic model fields have no corresponding SQLite columns because developers manually enumerate columns in 3 places. This plan eliminates that manual process.

**READ THESE FILES before writing any code:**
1. `docs/plans/2026-02-25-unified-schema-management-design.md` — Full design
2. `src/mozart/core/checkpoint.py` — CheckpointState (lines 548-690) and SheetState (lines 227-453) are the Pydantic models that define what columns MUST exist
3. `src/mozart/state/sqlite_backend.py` — Current manual save/load (lines 330-590) that this replaces
4. `src/mozart/daemon/registry.py` — Registry DB being unified with state (JobRecord at line 50, _create_tables at line 140, _migrate_schema at line 172)
5. `src/mozart/learning/store/base.py` — Best existing migration pattern (lines 97-134 for _COLUMN_MIGRATIONS, lines 654-710 for _migrate_columns)

**Key decisions already made (DO NOT revisit):**
- SQLite is the primary backend, not JSON. Daemon-owned.
- State backend merges INTO the daemon registry DB (`mozart.db`)
- Learning store and profiler stay as separate DBs
- Full column parity — every model field gets a column
- Complex types (list, dict) → TEXT columns with JSON serialization
- `PRAGMA user_version` for version tracking (not schema_version tables)
- Auto-generate migrations from model diffs, developer reviews and commits
- CI drift test blocks merge if model fields don't match columns
- Forward-compatible: newer DB version = warning, not error

---

## Phase 1: Schema Registry and Type Converters

**Goal:** Build the foundation that everything else depends on.

**Create `src/mozart/schema/__init__.py`** — Package init.

**Create `src/mozart/schema/registry.py`:**

```python
@dataclass
class TableMapping:
    model: type                              # Pydantic BaseModel or dataclass
    table: str                               # SQL table name
    primary_key: str | tuple[str, ...] = "id" # PK column(s)
    renames: dict[str, str] = field(default_factory=dict)  # model_field → column_name
    exclude: set[str] = field(default_factory=set)          # fields NOT stored in this table
    extra_columns: list[tuple[str, str]] = field(default_factory=list)  # (name, definition) for FKs etc
```

Implement:
- `get_column_name(mapping, field_name) → str` — applies renames
- `get_column_type(field_info) → str` — maps Python types to SQLite types using TYPE_MAP
- `get_expected_columns(mapping) → dict[str, str]` — {column_name: column_type} from model
- `generate_create_table(mapping) → str` — full CREATE TABLE SQL
- `generate_upsert(mapping) → tuple[str, list[str]]` — (SQL template, field order)

**Populate the registry:**

```python
STATE_REGISTRY = [
    TableMapping(model=CheckpointState, table="jobs",
                 primary_key="id",
                 renames={"job_id": "id", "job_name": "name"},
                 exclude={"sheets"}),
    TableMapping(model=SheetState, table="sheets",
                 primary_key=("job_id", "sheet_num"),
                 extra_columns=[("job_id", "TEXT NOT NULL")]),
]

LEARNING_REGISTRY = [
    TableMapping(model=PatternRecord, table="patterns"),
    TableMapping(model=ExecutionRecord, table="executions"),
    # ... all 13 learning store tables
]

PROFILER_REGISTRY = [
    TableMapping(model=SystemSnapshot, table="snapshots"),
    TableMapping(model=ProcessMetric, table="process_metrics"),
    TableMapping(model=ProcessEvent, table="process_events"),
]
```

**Create `src/mozart/schema/converters.py`:**

The current sqlite_backend.py already has these converters scattered as methods (_datetime_to_str, _str_to_datetime, _json_dumps, _json_loads, _bool_to_int, _int_to_bool). Extract and unify:

```python
@dataclass
class TypeConverter:
    to_sql: Callable[[Any], SQLParam]
    from_sql: Callable[[Any], Any]

CONVERTERS: dict[type, TypeConverter] = {
    datetime: TypeConverter(to_sql=lambda v: v.isoformat() if v else None,
                           from_sql=lambda v: datetime.fromisoformat(v) if v else None),
    bool: TypeConverter(to_sql=lambda v: int(v) if v is not None else None,
                       from_sql=lambda v: bool(v) if v is not None else None),
    # list/dict handled by JSON serialization
}
```

Implement `serialize_field(value, field_info) → SQLParam` and `deserialize_field(value, field_info) → Any`.

**Tests:** `tests/test_schema_registry.py`
- Test type mapping for every Python type in the type table
- Test generate_create_table produces valid SQL (run on :memory:)
- Test renames, exclude, extra_columns work correctly
- Test converters round-trip correctly (serialize then deserialize = original)

---

## Phase 2: Migration Runner

**Goal:** Unified migration execution that all 3 DBs use.

**Create `src/mozart/schema/migrate.py`:**

```python
MigrationStep = str | Callable[[aiosqlite.Connection], Awaitable[None]]

async def get_version(db: aiosqlite.Connection) -> int:
    """Read PRAGMA user_version."""

async def apply_migrations(
    db: aiosqlite.Connection,
    migrations: list[MigrationStep],
    *,
    db_name: str = "unknown",  # for logging
) -> int:
    """Apply pending migrations. Returns new version."""
    current = await get_version(db)
    target = len(migrations)

    if current > target:
        logger.warning("db_version_ahead", db=db_name, current=current, target=target)
        return current  # Proceed with warning, don't fail

    if current == target:
        return current

    # Disable FK checks during migration
    await db.execute("PRAGMA foreign_keys=OFF")
    try:
        for i in range(current, target):
            step = migrations[i]
            if isinstance(step, str):
                # Split on semicolons, execute each statement
                for stmt in step.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        await db.execute(stmt)
            else:
                await step(db)
            await db.execute(f"PRAGMA user_version = {i + 1}")
            await db.commit()
            logger.info("migration_applied", db=db_name, version=i + 1)
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.execute("PRAGMA foreign_keys=ON")

    return target
```

**IMPORTANT:** Do NOT use `executescript()` — it implicitly commits pending transactions. Use individual `execute()` calls.

**Tests:** `tests/test_schema_migrate.py`
- Test applying migrations on empty :memory: DB
- Test idempotency (run twice, same result)
- Test version skipping (v1 → v5 applies all intermediate)
- Test forward version produces warning, not error
- Test failed migration rolls back cleanly
- Test Python callable migrations work

---

## Phase 3: Unified State Backend (The Big Rewrite)

**Goal:** Replace the manual column enumeration in sqlite_backend.py with registry-driven save/load.

**This is the highest-risk phase.** The current save/load is ~260 lines of manual column mapping. The new version should be ~80 lines of registry-driven code.

**Rewrite `src/mozart/state/sqlite_backend.py`:**

The save() method becomes:
```python
async def save(self, state: CheckpointState) -> None:
    mapping = get_mapping_for(CheckpointState)
    sql, fields = generate_upsert(mapping)
    values = [serialize_field(getattr(state, f), ...) for f in fields]
    await db.execute(sql, values)

    # Save sheets
    sheet_mapping = get_mapping_for(SheetState)
    sheet_sql, sheet_fields = generate_upsert(sheet_mapping)
    for sheet in state.sheets.values():
        values = [state.job_id] + [serialize_field(getattr(sheet, f), ...) for f in sheet_fields]
        await db.execute(sheet_sql, values)
```

The load() method becomes:
```python
async def load(self, job_id: str) -> CheckpointState | None:
    row = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not row: return None

    mapping = get_mapping_for(CheckpointState)
    job_data = deserialize_row(row, mapping)

    sheet_rows = await db.execute("SELECT * FROM sheets WHERE job_id = ?", (job_id,))
    sheets = {r["sheet_num"]: deserialize_row(r, get_mapping_for(SheetState)) for r in sheet_rows}
    job_data["sheets"] = sheets

    return CheckpointState.model_validate(job_data)
```

**CRITICAL: Preserve existing functionality:**
- Zombie detection and auto-recovery (lines 330-453 of current file)
- Execution history recording (lines 671-762)
- Job statistics queries (lines 783-838)
- Job listing and querying (lines 618-889)
- Atomic transaction wrapping (BEGIN IMMEDIATE / COMMIT / ROLLBACK)

**Preserve the StateBackend protocol interface** — the public API (load, save, delete, list_jobs, etc.) stays identical.

**Tests:** The existing tests in `tests/` for sqlite_backend must all still pass. Add:
- Test that save/load round-trips ALL 40+ SheetState fields
- Test that save/load round-trips ALL CheckpointState fields
- Test backward compat: loading from a pre-migration DB still works (missing columns → defaults)

---

## Phase 4: Merge Registry + State into mozart.db

**Goal:** The daemon uses one unified DB for both registry operations and full state.

**Changes to `src/mozart/daemon/registry.py`:**
- Remove the separate `_create_tables()` and `_migrate_schema()` — these are now in the migration chain
- The `jobs` table from the state backend IS the registry table (same table, all columns)
- `JobRecord` dataclass may need updating to reference the unified schema
- Methods like `register_job`, `update_status`, `update_progress` still work — they just operate on the unified jobs table
- Methods like `store_hook_config`, `get_hook_config`, `store_hook_results` still work unchanged

**Changes to `src/mozart/daemon/manager.py`:**
- Where it currently uses both `registry` and a separate `state_backend`, it should use the unified backend
- The `checkpoint_json` column can be kept as a denormalized cache for fast full-state reads, OR removed in favor of the fully-columned table. Keeping it is pragmatic for the transition.

**Migration file `src/mozart/schema/migrations/v005_unify_state.py`:**
This migration:
1. Adds ALL 35 missing columns to the existing tables (ALTER TABLE ADD COLUMN with defaults)
2. Adds execution_history table if not present
3. Creates indexes for commonly queried columns (status, timestamps, cost fields)
4. Switches from schema_version table to PRAGMA user_version

**GOTCHA:** The existing registry DB has `jobs` with different column names than the state backend's `jobs`. The registry uses `job_id` as PK; the state backend uses `id`. The migration must handle this. Recommended: keep `job_id` (the registry's convention) and update the TableMapping rename accordingly.

**Tests:**
- Test that existing registry DBs upgrade cleanly
- Test that existing state backend DBs upgrade cleanly
- Test that the unified backend supports all registry operations AND all state operations

---

## Phase 5: Migration Generator (`mozart schema diff`)

**Goal:** Developers can auto-generate migrations when they add model fields.

**Create `src/mozart/schema/diff.py`:**

```python
def compute_schema_diff(
    migrations: list[MigrationStep],
    registry: list[TableMapping],
) -> list[SchemaChange]:
    """Compare post-migration schema against model registry."""
    # 1. Apply all migrations to :memory: DB
    # 2. PRAGMA table_info for each table
    # 3. Compare against registry's expected columns
    # 4. Return list of needed changes (ADD COLUMN, CREATE TABLE, etc.)
```

**Create CLI command `mozart schema diff`:**
- Runs the diff
- Prints human-readable summary
- Optionally writes a migration file (with `--generate`)

**Create CLI command `mozart schema validate`:**
- Same as diff but exits with code 1 if any drift found
- Suitable for CI

**Tests:** `tests/test_schema_diff.py`
- Test detects missing column
- Test detects missing table
- Test generates valid ALTER TABLE SQL
- Test no false positives when schema is in sync

---

## Phase 6: CI Drift Test

**Goal:** No model-schema drift can reach main branch.

**Create `tests/test_schema_drift.py`:**

```python
def test_state_schema_no_drift():
    """Every CheckpointState/SheetState field has a column in mozart.db."""

def test_learning_schema_no_drift():
    """Every learning store model field has a column in global-learning.db."""

def test_profiler_schema_no_drift():
    """Every profiler model field has a column in profiler.db."""

def test_migrations_apply_cleanly():
    """All migrations apply on empty DB without error."""

def test_migrations_idempotent():
    """Running migrations twice produces same schema."""
```

---

## Phase 7: Learning Store and Profiler Alignment

**Goal:** Wire existing databases into the unified tooling.

**Learning store:**
- Add TableMapping entries for all 13 tables in LEARNING_REGISTRY
- Switch from `schema_version` table to `PRAGMA user_version`
- This requires a bridge migration: read version from table, write to pragma, drop table
- Wire into drift test
- Keep `_COLUMN_MIGRATIONS` and `_COLUMN_RENAMES` — they still work, but the drift test now catches any gaps

**Profiler:**
- Add PRAGMA user_version tracking
- Add 3 missing columns (zombie_pids TEXT, job_progress TEXT, conductor_uptime_seconds REAL)
- Wire into drift test

---

## Phase 8: Legacy Import and Cleanup

**Goal:** Bring forward data from JSON-backend workspaces.

**Create `src/mozart/schema/import_legacy.py`:**
- Scans workspace directories for `.json` checkpoint files
- Loads via `CheckpointState.model_validate(json.loads(...))`
- Saves to unified DB via the new save() method
- Idempotent: skips jobs already in the DB

**Create CLI command `mozart db import-legacy`:**
- Takes workspace path(s)
- Reports what was imported

---

## Execution Order and Dependencies

```
Phase 1 (registry + converters) ← no dependencies, start here
    ↓
Phase 2 (migration runner) ← depends on Phase 1
    ↓
Phase 3 (unified state backend) ← depends on Phase 1, 2
    ↓
Phase 4 (merge registry + state) ← depends on Phase 3
    ↓
Phase 5 (migration generator) ← depends on Phase 1, 2
    ↓
Phase 6 (CI drift test) ← depends on Phase 1, 5
    ↓
Phase 7 (learning + profiler) ← depends on Phase 1, 2, 6
    ↓
Phase 8 (legacy import) ← depends on Phase 3, 4
```

Phases 5-6 can be done in parallel with Phase 4. Phase 7 can start after Phase 2.

---

## Risk Mitigation

1. **Phase 3 is highest risk** — rewriting save/load touches every code path that persists state. Run the FULL test suite (`pytest tests/ -x`) after every change. The existing tests are the safety net.

2. **Phase 4 column name conflicts** — registry uses `job_id`, state uses `id` as PK. Pick one convention and stick with it across the migration.

3. **PRAGMA user_version collision** — if a DB currently has user_version=0 (default) but has tables, the migration runner must detect this (check if tables exist) and set the correct starting version.

4. **Learning store's schema_version → PRAGMA transition** — must be atomic. Read old version, set pragma, drop table, all in one transaction.

5. **Backward compatibility** — the daemon must be able to open databases from before this change. The migration runner handles this, but test it explicitly with fixture databases.

---

## Verification Checklist

After ALL phases are complete:

- [ ] `pytest tests/ -x` passes (ALL existing tests)
- [ ] `test_schema_drift.py` passes for all 3 databases
- [ ] `mozart schema validate` exits 0
- [ ] `mozart schema diff` shows no drift
- [ ] Daemon starts clean with no existing DB (fresh install)
- [ ] Daemon starts with existing registry DB (upgrade path)
- [ ] Daemon starts with existing state DB (upgrade path)
- [ ] `mozart status` shows all fields including previously-missing ones
- [ ] `mypy src/` passes
- [ ] `ruff check src/` passes

---

*Implementation plan: 2026-02-25*
