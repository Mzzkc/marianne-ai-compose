"""Model-driven schema registry for unified SQLite schema management.

Maps Pydantic BaseModel and dataclass models to SQLite table schemas,
enabling automatic DDL generation, upsert SQL, and schema drift detection.
"""

from __future__ import annotations

import dataclasses
import types
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin

from marianne.core.logging import get_logger

_logger = get_logger("schema.registry")


# ---------------------------------------------------------------------------
# Type mapping: Python → SQLite
# ---------------------------------------------------------------------------

_SQLITE_TYPE_MAP: dict[type, str] = {
    str: "TEXT",
    int: "INTEGER",
    float: "REAL",
    bool: "INTEGER",
    bytes: "BLOB",
    datetime: "TEXT",
}


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Unwrap Optional[X] or X | None to (inner_type, is_optional).

    Returns (annotation, False) when the type is not optional.
    """
    origin = get_origin(annotation)
    is_union = origin is Union
    if not is_union and hasattr(types, "UnionType"):
        is_union = isinstance(annotation, types.UnionType)
    if is_union:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
        # Multi-type union — treat as complex (JSON)
        return annotation, True
    return annotation, False


def get_sqlite_type(annotation: Any) -> str:
    """Map a Python type annotation to a SQLite column type string."""
    inner, _ = _unwrap_optional(annotation)

    # Direct type map (str, int, float, bool, bytes, datetime)
    if inner in _SQLITE_TYPE_MAP:
        return _SQLITE_TYPE_MAP[inner]

    # Enum subclass → TEXT
    if isinstance(inner, type) and issubclass(inner, Enum):
        return "TEXT"

    # Literal → TEXT
    if get_origin(inner) is Literal:
        return "TEXT"

    # Complex container types → TEXT (JSON serialised)
    origin = get_origin(inner)
    if origin in (list, dict, frozenset, set, tuple):
        return "TEXT"

    # TypedDict (has __required_keys__ or __optional_keys__)
    if hasattr(inner, "__required_keys__") or hasattr(inner, "__optional_keys__"):
        return "TEXT"

    # Pydantic BaseModel → TEXT (JSON)
    try:
        from pydantic import BaseModel

        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return "TEXT"
    except ImportError:  # pragma: no cover
        pass

    # Dataclass → TEXT (JSON)
    if isinstance(inner, type) and dataclasses.is_dataclass(inner):
        return "TEXT"

    # Fallback
    return "TEXT"


# ---------------------------------------------------------------------------
# Field introspection
# ---------------------------------------------------------------------------


@dataclass
class FieldSpec:
    """Unified field descriptor for both Pydantic and dataclass models."""

    name: str
    annotation: Any
    has_default: bool


def get_field_specs(model: type) -> list[FieldSpec]:
    """Extract field specifications from a Pydantic or dataclass model.

    Returns fields in declaration order.
    """
    from pydantic import BaseModel

    if isinstance(model, type) and issubclass(model, BaseModel):
        specs = []
        for name, info in model.model_fields.items():
            has_default = not info.is_required()
            specs.append(
                FieldSpec(name=name, annotation=info.annotation, has_default=has_default)
            )
        return specs

    if dataclasses.is_dataclass(model):
        import typing

        try:
            hints = typing.get_type_hints(model)
        except Exception:
            hints = {}
        specs = []
        for f in dataclasses.fields(model):
            has_default = (
                f.default is not dataclasses.MISSING
                or f.default_factory is not dataclasses.MISSING
            )
            annotation = hints.get(f.name, f.type)
            specs.append(FieldSpec(name=f.name, annotation=annotation, has_default=has_default))
        return specs

    raise TypeError(
        f"Unsupported model type: {model}. Expected Pydantic BaseModel or dataclass."
    )


# ---------------------------------------------------------------------------
# Table mapping
# ---------------------------------------------------------------------------


@dataclass
class TableMapping:
    """Declares the mapping between a Python model and a SQLite table.

    This is the single source of truth for schema generation, save/load,
    and drift detection.
    """

    model: type
    """Pydantic BaseModel or dataclass class."""

    table: str
    """SQL table name."""

    primary_key: str | tuple[str, ...] = "id"
    """Primary key column(s). Refers to SQL column names (after renames)."""

    renames: dict[str, str] = field(default_factory=dict)
    """model_field_name → sql_column_name renames."""

    exclude: set[str] = field(default_factory=set)
    """Model field names to exclude from this table."""

    extra_columns: list[tuple[str, str]] = field(default_factory=list)
    """Additional columns not from the model: [(name, full_definition), ...]."""


# ---------------------------------------------------------------------------
# Column source for serialisation
# ---------------------------------------------------------------------------


@dataclass
class ColumnSource:
    """Describes where a SQL column's value comes from."""

    column_name: str
    """SQL column name."""

    field_name: str | None
    """Model field name (None for extra_columns)."""

    annotation: Any | None
    """Type annotation for serialisation (None for extra columns)."""

    is_extra: bool = False
    """True for extra_columns provided by the caller, not the model."""


def get_column_sources(mapping: TableMapping) -> list[ColumnSource]:
    """Get ordered column sources for building SQL values.

    Returns extra_columns first, then model fields (excluding excluded fields).
    """
    sources: list[ColumnSource] = []

    for col_name, _col_def in mapping.extra_columns:
        sources.append(
            ColumnSource(column_name=col_name, field_name=None, annotation=None, is_extra=True)
        )

    for spec in get_field_specs(mapping.model):
        if spec.name in mapping.exclude:
            continue
        col_name = mapping.renames.get(spec.name, spec.name)
        sources.append(
            ColumnSource(
                column_name=col_name,
                field_name=spec.name,
                annotation=spec.annotation,
                is_extra=False,
            )
        )

    return sources


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------


def get_expected_columns(mapping: TableMapping) -> dict[str, str]:
    """Get expected {column_name: sqlite_type} for a mapping.

    Includes extra columns and all non-excluded model fields.
    """
    columns: dict[str, str] = {}

    for col_name, col_def in mapping.extra_columns:
        col_type = col_def.split()[0] if col_def else "TEXT"
        columns[col_name] = col_type

    for spec in get_field_specs(mapping.model):
        if spec.name in mapping.exclude:
            continue
        col_name = mapping.renames.get(spec.name, spec.name)
        columns[col_name] = get_sqlite_type(spec.annotation)

    return columns


def generate_create_table(mapping: TableMapping) -> str:
    """Generate CREATE TABLE IF NOT EXISTS SQL from a TableMapping."""
    pk_cols = (
        (mapping.primary_key,) if isinstance(mapping.primary_key, str) else mapping.primary_key
    )
    lines: list[str] = []

    # Extra columns (e.g., foreign keys)
    for col_name, col_def in mapping.extra_columns:
        lines.append(f"    {col_name} {col_def}")

    # Model fields
    for spec in get_field_specs(mapping.model):
        if spec.name in mapping.exclude:
            continue
        col_name = mapping.renames.get(spec.name, spec.name)
        col_type = get_sqlite_type(spec.annotation)

        parts = [col_name, col_type]

        # PRIMARY KEY for single-column PK
        if len(pk_cols) == 1 and col_name == pk_cols[0]:
            parts.append("PRIMARY KEY")

        # NOT NULL for non-optional fields (PK already implies NOT NULL)
        _, is_optional = _unwrap_optional(spec.annotation)
        if not is_optional and col_name not in pk_cols:
            parts.append("NOT NULL")

        lines.append("    " + " ".join(parts))

    # Composite primary key
    if len(pk_cols) > 1:
        lines.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")

    column_sql = ",\n".join(lines)
    return f"CREATE TABLE IF NOT EXISTS {mapping.table} (\n{column_sql}\n)"


def generate_upsert(mapping: TableMapping) -> tuple[str, list[ColumnSource]]:
    """Generate INSERT ... ON CONFLICT DO UPDATE SQL.

    Returns:
        (sql_template, column_sources): SQL with ? placeholders and the
        ordered ColumnSource list matching placeholder positions.
    """
    sources = get_column_sources(mapping)
    pk_cols = (
        (mapping.primary_key,) if isinstance(mapping.primary_key, str) else mapping.primary_key
    )

    col_names = [s.column_name for s in sources]
    placeholders = ", ".join("?" for _ in col_names)
    col_list = ", ".join(col_names)

    # UPDATE SET for non-PK columns
    update_cols = [s.column_name for s in sources if s.column_name not in pk_cols]
    update_set = ",\n        ".join(f"{c} = excluded.{c}" for c in update_cols)

    pk_conflict = ", ".join(pk_cols)

    sql = (
        f"INSERT INTO {mapping.table} ({col_list})\n"
        f"    VALUES ({placeholders})\n"
        f"    ON CONFLICT({pk_conflict}) DO UPDATE SET\n"
        f"        {update_set}"
    )

    return sql, sources


# ---------------------------------------------------------------------------
# Registry instances — populated per-database
# ---------------------------------------------------------------------------


def _build_state_registry() -> list[TableMapping]:
    """Build the registry for the state/daemon DB (marianne.db)."""
    from marianne.core.checkpoint import CheckpointState, SheetState

    return [
        TableMapping(
            model=CheckpointState,
            table="jobs",
            primary_key="id",
            renames={"job_id": "id", "job_name": "name"},
            exclude={"sheets"},
        ),
        TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        ),
    ]


# Lazy-initialised registries to avoid import-time side effects.
# Phase 7 will add LEARNING_REGISTRY and PROFILER_REGISTRY.

_state_registry: list[TableMapping] | None = None


def get_state_registry() -> list[TableMapping]:
    """Get the state DB registry (lazy-built on first access)."""
    global _state_registry  # noqa: PLW0603
    if _state_registry is None:
        _state_registry = _build_state_registry()
    return _state_registry
