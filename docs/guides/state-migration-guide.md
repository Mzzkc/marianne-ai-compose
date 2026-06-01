# State Reset & Schema Migration Guide

> **Purpose**: How Marianne handles SQLite schema changes across versions, and
> what to do when a baton or backend change makes existing on-disk state
> incompatible. Covers the automated migration path **and** the operator
> hard-reset procedure.

This guide answers: *"I upgraded Marianne and it touched the state schema — will
my existing jobs survive, and if not, how do I recover?"*

---

## The stores

Marianne keeps state in **independent** SQLite databases, each with its own
schema and its own version counter. There is intentionally **no** single global
schema version — the stores are separate concerns and migrate independently.

| Store | Path | Constant | Version | Migration entry point |
|---|---|---|---|---|
| **Per-job state** | `<workspace>/.marianne-state.db` | `STATE_DB_FILENAME` (`core/constants.py`) | `SCHEMA_VERSION` in `state/sqlite_backend.py` (currently **4**) | `SQLiteStateBackend._run_migrations` |
| **Conductor registry** | `~/.marianne/daemon-state.db` | `DAEMON_STATE_DB_PATH` (`core/constants.py`) | reserved — persistent daemon state is not yet implemented (the field logs a warning if set) | n/a |
| **Learning store** | learning store DB | `SCHEMA_VERSION` in `learning/store/base.py` (currently **15**) | `GlobalLearningStore._migrate_if_needed` |

> **Cross-store coordination (see #320):** the per-job state version (4) and the
> learning-store version (15) advance independently. There is no cross-system
> migration orchestrator — compatibility is verified **per store**, at the point
> each store opens its DB. This is by design: job execution state and learned
> outcomes are separate domains. Do not assume the two version numbers relate.

---

## Automated forward migration

The per-job state backend migrates **forward** automatically the first time a DB
is opened (`_ensure_initialized` → `_run_migrations`). Migrations are:

- **Version-stepped.** `_run_migrations` reads the DB's `schema_version` and
  applies each `_migrate_vN` whose version is greater than the current one, in
  order. A fresh DB (version 0) runs v1→v4; a v2 DB runs v3→v4.
- **Idempotent.** Each `_migrate_vN` guards its changes (`CREATE TABLE IF NOT
  EXISTS`, `PRAGMA table_info` checks before `ALTER`, `INSERT OR IGNORE` of the
  version row). Re-running a completed migration is a safe no-op — this is what
  makes an interrupted migration recoverable by simply re-opening the DB.
- **Additive / in-place.** The shipped migrations add columns (`config_path`,
  `execution_duration_seconds`, `exit_signal`, `exit_reason`) or rename one
  (`first_attempt_success` → `success_without_retry`). No migration drops data.

The migration path is covered by `tests/test_sqlite_migrations_328.py` (fresh,
stepwise from each intermediate version, data-preservation, and the rename
both-ways).

### What automated migration does NOT do

- **No downgrade.** Migrations are forward-only (`if current_version < N`). If a
  DB is at a *higher* version than the running code (you downgraded Marianne),
  no migration runs and the older code operates its older schema against a newer
  DB — fields it doesn't know about are ignored, and writes may not populate
  newer columns. **Run a build whose `SCHEMA_VERSION` is ≥ the DB's version.**
- **No semantic state-shape bridging.** The migrations bridge *schema* changes
  (columns), not *semantic* changes to what the baton stores in a column. A
  baton change that reinterprets existing state (e.g. a different meaning for a
  status enum or attempt counter) is not something an `ALTER` can fix — that
  requires the hard-reset procedure below.

---

## Hard-reset procedure (incompatible state)

When a baton/backend change makes existing `.marianne-state.db` content
semantically incompatible (the migration system can bridge schema but not
meaning), reset the affected job's state. There is no separate `mzt clear`
command — reset is done through the run/recover flow.

### Option A — clean restart (discard prior state)

```bash
mzt run <score>.yaml --fresh
```

`--fresh` deletes the existing state DB for that job before running, guaranteeing
a clean start against the current schema. Use this when the prior run's results
are disposable (or already exported) and you want a known-good baseline.

> `--fresh` is destructive to that job's state. Export anything you need first
> (the workspace output files are not deleted — only the state DB is).

### Option B — recover committed work (don't re-execute)

If sheets completed real work on disk but were marked FAILED (e.g. a transient
crash after writing output), do **not** use `--fresh` — it would discard that
work. Instead:

```bash
mzt recover -c <score>.yaml     # reset FAILED + cascade-skipped sheets → PENDING
mzt resume -c <score>.yaml      # resume; completed work is preserved
```

`mzt recover` re-runs validations without re-executing the backend and
transitions recoverable sheets back to PENDING, so a subsequent `resume` picks
up only the work that actually needs redoing.

### Choosing

| Situation | Use |
|---|---|
| Schema bumped, additive — existing run fine | nothing (auto-migrates on next open) |
| State semantically incompatible, results disposable | `mzt run --fresh` |
| Work committed to disk but marked FAILED | `mzt recover` → `mzt resume` |
| DB newer than code (downgrade) | upgrade the code; do not rely on auto-migration |

---

## For developers: adding a schema change

1. Add `_migrate_vN(self, db)` to `state/sqlite_backend.py` implementing the
   change. Make it **idempotent** — guard every `ALTER` with a `PRAGMA
   table_info` existence check and record the version with `INSERT OR IGNORE`.
2. Add the `if current_version < N: await self._migrate_vN(db)` step to
   `_run_migrations`.
3. Bump `SCHEMA_VERSION`.
4. Add tests to `tests/test_sqlite_migrations_328.py`: fresh-reaches-N, stepwise
   from N-1, and data preservation across the new step (see #328).
5. If the change is **not** schema-bridgeable (semantic), document the operator
   impact here and in the release notes — automated migration cannot save it.

Prefer additive changes (`ADD COLUMN`) over destructive ones. SQLite's limited
`ALTER` support means column drops/type changes require a table rebuild
(create-new, copy, drop-old, rename) — if you need that, it is a multi-step
idempotent migration, not a one-liner.
