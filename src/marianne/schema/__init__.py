"""Unified schema management for Marianne's SQLite databases.

Provides serialization utilities for converting Python values to/from
SQLite-compatible types. The registry module exists but is not yet wired
into production code paths — the state layer uses hand-written SQL.
"""

from marianne.schema.converters import (
    SQLParam,
    deserialize_field,
    serialize_field,
)

__all__ = [
    "SQLParam",
    "deserialize_field",
    "serialize_field",
]
