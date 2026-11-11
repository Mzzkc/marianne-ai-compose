"""GH #538: a seat whose profile has no default_model and whose score names no
model runs on the harness's own default; ``mzt validate`` says so (V336)."""

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
)
from marianne.instruments.classes import load_class_map
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks.capability_classes import _ClassContext


def _profile(
    name: str, *, default_model: str | None = None, model_flag: str | None = "--model",
) -> InstrumentProfile:
    return InstrumentProfile.model_validate({
        "name": name, "display_name": name, "kind": "cli", "default_model": default_model,
        "cli": CliProfile(
            command=CliCommand(executable="x", prompt_flag="-p", model_flag=model_flag),
            output=CliOutputConfig(format="text"),
        ),
    })


def _ctx(tmp_path: Path, **profiles: InstrumentProfile) -> _ClassContext:
    class_map = load_class_map(
        user_path=tmp_path / "no-user.yaml", venue_path=tmp_path / "no-venue.yaml",
        profile_names=set(profiles),
    )
    return _ClassContext(profiles=profiles, class_map=class_map)


def _score(tmp_path: Path, **fields: Any) -> JobConfig:
    base: dict[str, Any] = {
        "name": "pin", "workspace": str(tmp_path / "ws"), "instrument": "harness",
        "sheet": {"size": 1, "total_items": 3}, "prompt": {"template": "x"},
    }
    base.update(fields)
    return JobConfig.model_validate(base)


def _v336(cfg: JobConfig, ctx: _ClassContext) -> list[ValidationIssue]:
    from marianne.validation.checks.instrument_model_pin import UnpinnedModelCheck

    with patch(
        "marianne.validation.checks.instrument_model_pin._load_context", return_value=ctx,
    ):
        return UnpinnedModelCheck().check(cfg, Path("s.yaml"), "instrument: harness\n")


def _ctx_default(tmp_path: Path) -> _ClassContext:
    return _ctx(
        tmp_path,
        harness=_profile("harness"),
        pinned=_profile("pinned", default_model="m-1"),
        shell=_profile("shell", model_flag=None),
    )


def test_registered_as_warning() -> None:
    from marianne.validation.output_contract import _known_suppression_severities

    assert _known_suppression_severities()["V336"] == ValidationSeverity.WARNING


def test_unpinned_seat_warns_naming_sheets_and_instrument(tmp_path: Path) -> None:
    issues = _v336(_score(tmp_path), _ctx_default(tmp_path))
    assert len(issues) == 1
    issue = issues[0]
    assert issue.check_id == "V336" and issue.severity == ValidationSeverity.WARNING
    assert "'harness'" in issue.message and "1, 2, 3" in issue.message
    assert "default" in issue.message
    assert "instrument_config.model" in (issue.suggestion or "")


def test_profile_default_is_clean(tmp_path: Path) -> None:
    assert _v336(_score(tmp_path, instrument="pinned"), _ctx_default(tmp_path)) == []


def test_non_model_instrument_is_clean(tmp_path: Path) -> None:
    assert _v336(_score(tmp_path, instrument="shell"), _ctx_default(tmp_path)) == []


def test_score_movement_and_per_sheet_pins(tmp_path: Path) -> None:
    ctx = _ctx_default(tmp_path)
    assert _v336(_score(tmp_path, instrument_config={"model": "a"}), ctx) == []
    cfg = _score(
        tmp_path,
        sheet={"size": 1, "total_items": 3, "per_sheet_instrument_config": {1: {"model": "a"}}},
    )
    (issue,) = _v336(cfg, ctx)
    assert issue.metadata["sheets"] == "2,3"


def test_alias_config_pins(tmp_path: Path) -> None:
    ctx = _ctx_default(tmp_path)
    cfg = _score(
        tmp_path, instrument="fast",
        instruments={"fast": {"profile": "harness", "config": {"model": "a"}}},
    )
    assert _v336(cfg, ctx) == []
    cfg = _score(
        tmp_path, instrument="fast", instruments={"fast": {"profile": "harness"}},
    )
    assert len(_v336(cfg, ctx)) == 1


def test_unpinned_fallback_is_judged(tmp_path: Path) -> None:
    ctx = _ctx_default(tmp_path)
    cfg = _score(tmp_path, instrument="pinned", instrument_fallbacks=["harness"])
    (issue,) = _v336(cfg, ctx)
    assert "fallback" in issue.message and "'harness'" in issue.message
    cfg = _score(
        tmp_path, instrument="pinned", instrument_fallbacks=["slow"],
        instruments={"slow": {"profile": "harness", "config": {"model": "a"}}},
    )
    assert _v336(cfg, ctx) == []


def test_unknown_instrument_is_skipped(tmp_path: Path) -> None:
    assert _v336(_score(tmp_path, instrument="nope"), _ctx_default(tmp_path)) == []


# ── Observed model on the headless CLI path ──────────────────────────────


def _backend(model_usage_path: str | None = "modelUsage", model: str | None = None) -> Any:
    from marianne.execution.instruments.cli_backend import PluginCliBackend

    profile = InstrumentProfile.model_validate({
        "name": "harness", "display_name": "h", "kind": "cli",
        "cli": CliProfile(
            command=CliCommand(executable="x", prompt_flag="-p", model_flag="--model"),
            output=CliOutputConfig(
                format="json", result_path="result", model_usage_path=model_usage_path,
            ),
        ),
    })
    backend = PluginCliBackend(profile)
    backend._model = model
    return backend


def _parse(backend: Any, payload: dict[str, Any]) -> Any:
    import json

    return backend._parse_output(json.dumps(payload), "", exit_code=0)


def test_observed_model_recorded_when_none_requested() -> None:
    result = _parse(_backend(), {
        "result": "ok", "modelUsage": {"claude-sonnet-5": {"outputTokens": 40}},
    })
    assert result.model_observed == "claude-sonnet-5"
    assert result.model is None


def test_observed_model_is_the_heaviest_of_several() -> None:
    result = _parse(_backend(), {"result": "ok", "modelUsage": {
        "claude-haiku-4-5": {"outputTokens": 5}, "claude-opus-5": {"outputTokens": 900},
    }})
    assert result.model_observed == "claude-opus-5"


def test_observed_model_recorded_even_when_requested() -> None:
    result = _parse(_backend(model="claude-sonnet-5"), {
        "result": "ok", "modelUsage": {"claude-opus-5": {"outputTokens": 1}},
    })
    assert result.model == "claude-sonnet-5" and result.model_observed == "claude-opus-5"


def test_absent_or_unconfigured_leaves_observed_none() -> None:
    assert _parse(_backend(), {"result": "ok"}).model_observed is None
    assert _parse(_backend(None), {"result": "ok", "modelUsage": {"m": {}}}).model_observed is None
    assert _parse(_backend(), {"result": "ok", "modelUsage": []}).model_observed is None


def test_claude_code_builtin_declares_the_path() -> None:
    import yaml

    builtin = Path(__file__).resolve().parents[1] / "src/marianne/instruments/builtins"
    raw = yaml.safe_load((builtin / "claude-code.yaml").read_text())
    assert raw["cli"]["output"]["model_usage_path"] == "modelUsage"


def test_schema_mode_also_records_the_observed_model() -> None:
    import json

    backend = _backend()
    payload = {
        "structured_output": {"a": 1}, "modelUsage": {"claude-sonnet-5": {"outputTokens": 3}},
    }
    rf = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}
    result = backend._parse_output(json.dumps(payload), "", exit_code=0, response_format=rf)
    assert result.model_observed == "claude-sonnet-5"
