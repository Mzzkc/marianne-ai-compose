"""Tests for the schema registry and type converters (Phase 1)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Literal

import pytest

from marianne.core.checkpoint import (
    CheckpointState,
    JobStatus,
    SheetState,
    SheetStatus,
)
from marianne.schema.converters import deserialize_field, serialize_field
from marianne.schema.registry import (
    TableMapping,
    generate_create_table,
    generate_upsert,
    get_column_sources,
    get_expected_columns,
    get_field_specs,
    get_sqlite_type,
    get_state_registry,
)

# ── Type mapping ─────────────────────────────────────────────────────────


class TestGetSqliteType:
    """Verify every Python type in the design doc maps correctly."""

    def test_str(self) -> None:
        assert get_sqlite_type(str) == "TEXT"

    def test_int(self) -> None:
        assert get_sqlite_type(int) == "INTEGER"

    def test_float(self) -> None:
        assert get_sqlite_type(float) == "REAL"

    def test_bool(self) -> None:
        assert get_sqlite_type(bool) == "INTEGER"

    def test_datetime(self) -> None:
        assert get_sqlite_type(datetime) == "TEXT"

    def test_bytes(self) -> None:
        assert get_sqlite_type(bytes) == "BLOB"

    def test_optional_str(self) -> None:
        assert get_sqlite_type(str | None) == "TEXT"

    def test_optional_int(self) -> None:
        assert get_sqlite_type(int | None) == "INTEGER"

    def test_optional_datetime(self) -> None:
        assert get_sqlite_type(datetime | None) == "TEXT"

    def test_list_str(self) -> None:
        assert get_sqlite_type(list[str]) == "TEXT"

    def test_dict_str_any(self) -> None:
        assert get_sqlite_type(dict[str, Any]) == "TEXT"

    def test_enum(self) -> None:
        assert get_sqlite_type(SheetStatus) == "TEXT"
        assert get_sqlite_type(JobStatus) == "TEXT"

    def test_literal(self) -> None:
        assert get_sqlite_type(Literal["a", "b"]) == "TEXT"

    def test_optional_enum(self) -> None:
        assert get_sqlite_type(SheetStatus | None) == "TEXT"

    def test_list_dict(self) -> None:
        assert get_sqlite_type(list[dict[str, Any]]) == "TEXT"

    def test_unknown_defaults_to_text(self) -> None:
        """Unknown types fall back to TEXT (safe JSON serialisation)."""
        assert get_sqlite_type(object) == "TEXT"


# ── Field introspection ──────────────────────────────────────────────────


class TestGetFieldSpecs:
    def test_pydantic_model_returns_all_fields(self) -> None:
        specs = get_field_specs(SheetState)
        names = {s.name for s in specs}
        assert "sheet_num" in names
        assert "status" in names
        assert "started_at" in names
        assert "stdout_tail" in names
        assert "agent_feedback" in names

    def test_pydantic_required_vs_default(self) -> None:
        specs = get_field_specs(SheetState)
        by_name = {s.name: s for s in specs}
        # sheet_num has ge=1 constraint but no default → required
        assert by_name["sheet_num"].has_default is False
        # status has a default value (PENDING)
        assert by_name["status"].has_default is True
        # started_at defaults to None
        assert by_name["started_at"].has_default is True

    def test_checkpoint_state_fields(self) -> None:
        specs = get_field_specs(CheckpointState)
        names = {s.name for s in specs}
        assert "job_id" in names
        assert "sheets" in names
        assert "total_estimated_cost" in names
        assert "circuit_breaker_history" in names

    def test_dataclass_model(self) -> None:
        from marianne.learning.store.models import PatternRecord

        specs = get_field_specs(PatternRecord)
        names = {s.name for s in specs}
        assert "id" in names
        assert "pattern_type" in names
        assert "trust_score" in names
        assert "success_factors" in names

    def test_dataclass_defaults(self) -> None:
        from marianne.learning.store.models import PatternRecord

        specs = get_field_specs(PatternRecord)
        by_name = {s.name: s for s in specs}
        # id has no default → required
        assert by_name["id"].has_default is False
        # trust_score has default 0.5
        assert by_name["trust_score"].has_default is True

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(TypeError, match="Unsupported model type"):
            get_field_specs(int)


# ── generate_create_table ────────────────────────────────────────────────


class TestGenerateCreateTable:
    def test_creates_valid_sql_for_sheets(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        sql = generate_create_table(mapping)
        conn = sqlite3.connect(":memory:")
        conn.execute(sql)
        cursor = conn.execute("PRAGMA table_info(sheets)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "job_id" in columns
        assert "sheet_num" in columns
        assert "status" in columns
        assert "started_at" in columns
        assert "stdout_tail" in columns
        conn.close()

    def test_creates_valid_sql_for_jobs(self) -> None:
        mapping = TableMapping(
            model=CheckpointState,
            table="jobs",
            primary_key="id",
            renames={"job_id": "id", "job_name": "name"},
            exclude={"sheets"},
        )
        sql = generate_create_table(mapping)
        conn = sqlite3.connect(":memory:")
        conn.execute(sql)
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "id" in columns
        assert "name" in columns
        assert "status" in columns
        assert "total_sheets" in columns
        assert "sheets" not in columns  # excluded
        conn.close()

    def test_renames_appear_in_ddl(self) -> None:
        mapping = TableMapping(
            model=CheckpointState,
            table="jobs",
            primary_key="id",
            renames={"job_id": "id", "job_name": "name"},
            exclude={"sheets"},
        )
        sql = generate_create_table(mapping)
        # Renamed columns should appear by their SQL name
        assert "id TEXT PRIMARY KEY" in sql
        # Original model field names should NOT appear as columns
        lines = sql.split("\n")
        column_lines = [
            ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith(("CREATE", ")"))
        ]
        col_names = [ln.split()[0] for ln in column_lines]
        assert "job_id" not in col_names
        assert "job_name" not in col_names

    def test_excludes_work(self) -> None:
        mapping = TableMapping(
            model=CheckpointState,
            table="jobs",
            primary_key="id",
            renames={"job_id": "id", "job_name": "name"},
            exclude={"sheets"},
        )
        sql = generate_create_table(mapping)
        conn = sqlite3.connect(":memory:")
        conn.execute(sql)
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "sheets" not in columns
        assert "total_sheets" in columns  # different field, not excluded
        conn.close()

    def test_extra_columns_in_ddl(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        sql = generate_create_table(mapping)
        assert "job_id TEXT NOT NULL" in sql

    def test_composite_pk(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        sql = generate_create_table(mapping)
        assert "PRIMARY KEY (job_id, sheet_num)" in sql


# ── generate_upsert ──────────────────────────────────────────────────────


class TestGenerateUpsert:
    def test_valid_insert_for_sheets(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        sql, sources = generate_upsert(mapping)
        assert "INSERT INTO sheets" in sql
        assert "ON CONFLICT(job_id, sheet_num)" in sql
        assert "DO UPDATE SET" in sql

    def test_pk_not_in_update_set(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        sql, _ = generate_upsert(mapping)
        # PK columns should NOT appear in the UPDATE SET
        assert "sheet_num = excluded.sheet_num" not in sql

    def test_placeholder_count_matches_sources(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        sql, sources = generate_upsert(mapping)
        placeholder_count = sql.count("?")
        assert placeholder_count == len(sources)

    def test_sources_have_correct_types(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        _, sources = generate_upsert(mapping)
        # First source should be the extra column
        assert sources[0].is_extra is True
        assert sources[0].column_name == "job_id"
        assert sources[0].field_name is None
        # Second source should be sheet_num from the model
        assert sources[1].is_extra is False
        assert sources[1].field_name == "sheet_num"

    def test_single_pk_upsert(self) -> None:
        mapping = TableMapping(
            model=CheckpointState,
            table="jobs",
            primary_key="id",
            renames={"job_id": "id", "job_name": "name"},
            exclude={"sheets"},
        )
        sql, sources = generate_upsert(mapping)
        assert "ON CONFLICT(id) DO UPDATE SET" in sql
        # 'id' should NOT be in UPDATE SET
        assert "id = excluded.id" not in sql


# ── get_expected_columns ─────────────────────────────────────────────────


class TestGetExpectedColumns:
    def test_includes_all_sheet_fields(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        columns = get_expected_columns(mapping)
        assert "job_id" in columns
        assert "sheet_num" in columns
        assert "status" in columns
        assert "started_at" in columns
        assert "stdout_tail" in columns
        assert "agent_feedback" in columns

    def test_excludes_work(self) -> None:
        mapping = TableMapping(
            model=CheckpointState,
            table="jobs",
            primary_key="id",
            renames={"job_id": "id", "job_name": "name"},
            exclude={"sheets"},
        )
        columns = get_expected_columns(mapping)
        assert "sheets" not in columns
        assert "id" in columns
        assert "name" in columns

    def test_types_are_correct(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        columns = get_expected_columns(mapping)
        assert columns["sheet_num"] == "INTEGER"
        assert columns["status"] == "TEXT"  # Enum → TEXT
        assert columns["attempt_count"] == "INTEGER"
        assert columns["confidence_score"] == "REAL"
        assert columns["validation_details"] == "TEXT"  # list → JSON TEXT
        assert columns["started_at"] == "TEXT"  # datetime → TEXT


# ── get_column_sources ───────────────────────────────────────────────────


class TestGetColumnSources:
    def test_extra_columns_first(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        sources = get_column_sources(mapping)
        assert sources[0].is_extra is True
        assert sources[0].column_name == "job_id"

    def test_model_fields_follow_extras(self) -> None:
        mapping = TableMapping(
            model=SheetState,
            table="sheets",
            primary_key=("job_id", "sheet_num"),
            extra_columns=[("job_id", "TEXT NOT NULL")],
        )
        sources = get_column_sources(mapping)
        model_sources = [s for s in sources if not s.is_extra]
        field_names = {s.field_name for s in model_sources}
        assert "sheet_num" in field_names
        assert "status" in field_names

    def test_excludes_respected(self) -> None:
        mapping = TableMapping(
            model=CheckpointState,
            table="jobs",
            primary_key="id",
            renames={"job_id": "id", "job_name": "name"},
            exclude={"sheets"},
        )
        sources = get_column_sources(mapping)
        field_names = {s.field_name for s in sources if s.field_name}
        assert "sheets" not in field_names


# ── State registry ───────────────────────────────────────────────────────


class TestStateRegistry:
    def test_registry_has_two_mappings(self) -> None:
        reg = get_state_registry()
        assert len(reg) == 2
        assert reg[0].table == "jobs"
        assert reg[1].table == "sheets"

    def test_jobs_mapping_valid_ddl(self) -> None:
        reg = get_state_registry()
        sql = generate_create_table(reg[0])
        conn = sqlite3.connect(":memory:")
        conn.execute(sql)
        conn.close()
        assert "CREATE TABLE" in sql

    def test_sheets_mapping_valid_ddl(self) -> None:
        reg = get_state_registry()
        sql = generate_create_table(reg[1])
        conn = sqlite3.connect(":memory:")
        conn.execute(sql)
        conn.close()
        assert "CREATE TABLE" in sql

    def test_both_tables_coexist(self) -> None:
        """Both tables can be created in the same DB."""
        reg = get_state_registry()
        conn = sqlite3.connect(":memory:")
        for mapping in reg:
            conn.execute(generate_create_table(mapping))
        # Verify both exist
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}
        assert "jobs" in tables
        assert "sheets" in tables
        conn.close()


# ── Converter tests ──────────────────────────────────────────────────────


class TestSerializeField:
    def test_none(self) -> None:
        assert serialize_field(None, str | None) is None

    def test_str(self) -> None:
        assert serialize_field("hello", str) == "hello"

    def test_int(self) -> None:
        assert serialize_field(42, int) == 42

    def test_float(self) -> None:
        assert serialize_field(3.14, float) == 3.14

    def test_bool_true(self) -> None:
        assert serialize_field(True, bool) == 1

    def test_bool_false(self) -> None:
        assert serialize_field(False, bool) == 0

    def test_datetime(self) -> None:
        dt = datetime(2026, 2, 25, 12, 0, 0)
        assert serialize_field(dt, datetime) == "2026-02-25T12:00:00"

    def test_enum(self) -> None:
        assert serialize_field(SheetStatus.COMPLETED, SheetStatus) == "completed"

    def test_list(self) -> None:
        result = serialize_field(["a", "b"], list[str])
        assert json.loads(result) == ["a", "b"]

    def test_dict(self) -> None:
        result = serialize_field({"key": "val"}, dict[str, str])
        assert json.loads(result) == {"key": "val"}

    def test_nested_list_of_dicts(self) -> None:
        data = [{"id": "p1", "description": "test"}]
        result = serialize_field(data, list[dict[str, str]])
        assert json.loads(result) == data

    def test_optional_bool_none(self) -> None:
        assert serialize_field(None, bool | None) is None

    def test_optional_bool_true(self) -> None:
        assert serialize_field(True, bool | None) == 1


class TestDeserializeField:
    def test_none(self) -> None:
        assert deserialize_field(None, str | None) is None

    def test_str(self) -> None:
        assert deserialize_field("hello", str) == "hello"

    def test_int(self) -> None:
        assert deserialize_field(42, int) == 42

    def test_bool_from_int(self) -> None:
        assert deserialize_field(1, bool) is True
        assert deserialize_field(0, bool) is False

    def test_datetime_from_iso(self) -> None:
        result = deserialize_field("2026-02-25T12:00:00", datetime)
        assert result == datetime(2026, 2, 25, 12, 0, 0)

    def test_enum(self) -> None:
        result = deserialize_field("completed", SheetStatus)
        assert result == SheetStatus.COMPLETED

    def test_list_from_json(self) -> None:
        result = deserialize_field('["a", "b"]', list[str])
        assert result == ["a", "b"]

    def test_dict_from_json(self) -> None:
        result = deserialize_field('{"key": "val"}', dict[str, str])
        assert result == {"key": "val"}

    def test_corrupt_datetime(self) -> None:
        result = deserialize_field("not-a-date", datetime)
        assert result is None

    def test_unknown_enum_value(self) -> None:
        result = deserialize_field("nonexistent", SheetStatus)
        assert result == "nonexistent"  # returns raw string

    def test_corrupt_json(self) -> None:
        result = deserialize_field("{invalid", list[str])
        assert result is None

    def test_literal(self) -> None:
        result = deserialize_field("worktree", Literal["worktree", "none"])
        assert result == "worktree"


# ── Round-trip tests ─────────────────────────────────────────────────────


class TestRoundTrip:
    """Verify serialize → deserialize produces the original value."""

    def test_str(self) -> None:
        v = "hello"
        assert deserialize_field(serialize_field(v, str), str) == v

    def test_int(self) -> None:
        v = 42
        assert deserialize_field(serialize_field(v, int), int) == v

    def test_float(self) -> None:
        v = 3.14
        assert deserialize_field(serialize_field(v, float), float) == v

    def test_bool_true(self) -> None:
        assert deserialize_field(serialize_field(True, bool), bool) is True

    def test_bool_false(self) -> None:
        assert deserialize_field(serialize_field(False, bool), bool) is False

    def test_datetime(self) -> None:
        v = datetime(2026, 2, 25, 12, 0, 0)
        assert deserialize_field(serialize_field(v, datetime), datetime) == v

    def test_enum(self) -> None:
        v = SheetStatus.FAILED
        assert deserialize_field(serialize_field(v, SheetStatus), SheetStatus) == v

    def test_list(self) -> None:
        v = ["a", "b", "c"]
        assert deserialize_field(serialize_field(v, list[str]), list[str]) == v

    def test_dict(self) -> None:
        v = {"key": "value", "num": 42}
        assert deserialize_field(serialize_field(v, dict[str, Any]), dict[str, Any]) == v

    def test_none(self) -> None:
        assert deserialize_field(serialize_field(None, str | None), str | None) is None

    def test_optional_bool(self) -> None:
        assert deserialize_field(serialize_field(True, bool | None), bool | None) is True
        assert deserialize_field(serialize_field(None, bool | None), bool | None) is None

    def test_optional_datetime(self) -> None:
        v = datetime(2026, 1, 1)
        assert deserialize_field(serialize_field(v, datetime | None), datetime | None) == v
        assert deserialize_field(serialize_field(None, datetime | None), datetime | None) is None

    def test_nested_list_of_dicts(self) -> None:
        v = [{"id": "p1", "description": "test"}, {"id": "p2", "description": "test2"}]
        ann = list[dict[str, str]]
        assert deserialize_field(serialize_field(v, ann), ann) == v
