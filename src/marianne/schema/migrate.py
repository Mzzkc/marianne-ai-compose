"""Unified migration runner for all Marianne SQLite databases.

Applies numbered migrations sequentially using PRAGMA user_version
for version tracking. Each migration is either a SQL string (split
on semicolons) or an async callable for complex data transforms.

Design decisions:
- Uses individual execute() calls, NOT executescript() (which
  implicitly commits and breaks transactional safety).
- Each migration runs in its own transaction. On failure, that
  migration is rolled back and no further migrations are attempted.
- Forward-compatible: a database from a newer Marianne version
  produces a warning, not an error.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import aiosqlite

from marianne.core.logging import get_logger

_logger = get_logger("schema.migrate")

# A migration step is either a SQL string (semicolon-separated statements)
# or an async callable that receives the connection for complex transforms.
MigrationStep = str | Callable[[aiosqlite.Connection], Awaitable[None]]


async def get_version(db: aiosqlite.Connection) -> int:
    """Read the current schema version from PRAGMA user_version."""
    cursor = await db.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def set_version(db: aiosqlite.Connection, version: int) -> None:
    """Set the schema version via PRAGMA user_version.

    Note: PRAGMA user_version doesn't support parameter binding,
    so we format the integer directly. This is safe because
    version is always an int (enforced by the type signature).
    """
    await db.execute(f"PRAGMA user_version = {int(version)}")


async def apply_migrations(
    db: aiosqlite.Connection,
    migrations: list[MigrationStep],
    *,
    db_name: str = "unknown",
) -> int:
    """Apply pending migrations sequentially. Returns the final version.

    Migrations are indexed starting at 0. Migration[0] brings the DB
    from version 0 → 1, migration[1] from 1 → 2, and so on.

    Args:
        db: An open aiosqlite connection.
        migrations: Ordered list of migration steps.
        db_name: Human-readable name for log messages.

    Returns:
        The database version after applying migrations.
    """
    current = await get_version(db)
    target = len(migrations)

    if current > target:
        _logger.warning(
            "db_version_ahead",
            db=db_name,
            current=current,
            target=target,
            message=(
                f"Database '{db_name}' is at version {current} but app knows "
                f"only {target} migrations. Running a newer Marianne version? "
                "Proceeding with caution."
            ),
        )
        return current

    if current == target:
        return current

    _logger.info(
        "migrations_pending",
        db=db_name,
        current=current,
        target=target,
        pending=target - current,
    )

    # Disable FK checks during migration to allow schema changes
    # that temporarily violate constraints.
    await db.execute("PRAGMA foreign_keys=OFF")
    try:
        for i in range(current, target):
            step = migrations[i]
            version = i + 1
            try:
                # Commit any pending implicit transaction so BEGIN works.
                await db.commit()
                # Wrap each migration in an explicit transaction so that
                # DDL (CREATE TABLE, ALTER TABLE) can be rolled back on
                # failure — sqlite3's default auto-commits DDL otherwise.
                await db.execute("BEGIN IMMEDIATE")

                if isinstance(step, str):
                    # SQL string: split on semicolons, execute each
                    for stmt in step.split(";"):
                        stmt = stmt.strip()
                        if stmt:
                            await db.execute(stmt)
                else:
                    # Async callable for complex migrations
                    await step(db)

                await set_version(db, version)
                await db.execute("COMMIT")
                _logger.info(
                    "migration_applied",
                    db=db_name,
                    version=version,
                )
            except Exception:
                try:
                    await db.execute("ROLLBACK")
                except Exception:
                    pass  # Best-effort rollback
                _logger.error(
                    "migration_failed",
                    db=db_name,
                    version=version,
                    exc_info=True,
                )
                raise
    finally:
        await db.execute("PRAGMA foreign_keys=ON")

    return target
