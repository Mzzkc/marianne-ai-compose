"""GH #478: a response_format sheet on an interactive-by-default profile is
named by `mzt validate` (V331) instead of being refused at dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from marianne.core.config import JobConfig
from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    InteractiveCliConfig,
)
from marianne.instruments.classes import load_class_map
from marianne.validation.checks.capability_classes import (
    SchemaSheetInteractiveCheck,
    _ClassContext,
)

SCHEMA = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}


def _profile(name: str, *, interactive_default: bool | None) -> InstrumentProfile:
    interactive = (
        None if interactive_default is None
        else InteractiveCliConfig(
            ready_pattern="r", enabled_by_default=interactive_default,
        )
    )
    return InstrumentProfile(
        name=name, display_name=name, kind="cli",
        cli=CliProfile(
            command=CliCommand(executable="x", prompt_flag="-p", json_schema_flag="--schema"),
            output=CliOutputConfig(format="text"),
            interactive=interactive,
        ),
    )


def _ctx(tmp_path: Path, **profiles: InstrumentProfile) -> _ClassContext:
    class_map = load_class_map(
        user_path=tmp_path / "no-user.yaml", venue_path=tmp_path / "no-venue.yaml",
        profile_names=set(profiles),
    )
    return _ClassContext(profiles=profiles, class_map=class_map)


def _config(tmp_path: Path, **fields: Any) -> JobConfig:
    base: dict[str, Any] = {
        "name": "schema",
        "workspace": str(tmp_path / "ws"),
        "instrument": "claude-code",
        "sheet": {"size": 1, "total_items": 2},
        "prompt": {"template": "x"},
    }
    base.update(fields)
    return JobConfig.model_validate(base)


def _run(cfg: JobConfig, ctx: _ClassContext) -> list[str]:
    with patch(
        "marianne.validation.checks.capability_classes._load_context", return_value=ctx,
    ):
        return [i.check_id for i in SchemaSheetInteractiveCheck().check(cfg, Path("s.yaml"), "")]


def test_silent_schema_sheet_on_interactive_default_warns(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, **{"claude-code": _profile("claude-code", interactive_default=True)})
    cfg = _config(tmp_path, instrument_config={"response_format": SCHEMA})
    # One row per instrument+movement, not per concrete sheet: two sheets, two movements.
    assert _run(cfg, ctx) == ["V331", "V331"]


def test_explicit_headless_is_clean(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, **{"claude-code": _profile("claude-code", interactive_default=True)})
    cfg = _config(tmp_path, instrument_config={"response_format": SCHEMA, "interactive": False})
    assert _run(cfg, ctx) == []


def test_profile_headless_by_default_is_clean(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, **{"claude-code": _profile("claude-code", interactive_default=False)})
    cfg = _config(tmp_path, instrument_config={"response_format": SCHEMA})
    assert _run(cfg, ctx) == []


def test_no_interactive_support_is_clean(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, **{"claude-code": _profile("claude-code", interactive_default=None)})
    cfg = _config(tmp_path, instrument_config={"response_format": SCHEMA})
    assert _run(cfg, ctx) == []


def test_per_movement_scope_is_seen(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, **{"claude-code": _profile("claude-code", interactive_default=True)})
    cfg = _config(
        tmp_path,
        movements={2: {"name": "judge", "instrument_config": {"response_format": SCHEMA}}},
    )
    issues = SchemaSheetInteractiveCheck()
    with patch(
        "marianne.validation.checks.capability_classes._load_context", return_value=ctx,
    ):
        found = issues.check(cfg, Path("s.yaml"), "")
    assert [i.metadata["sheet_num"] for i in found] == ["2"]
