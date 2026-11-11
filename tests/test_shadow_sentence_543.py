"""V335 and V337 name a shadowing file and `extends` when a builtin has the block (GH #543)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from marianne.core.config import JobConfig
from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.classes import load_class_map
from marianne.instruments.loader import InstrumentProfileLoader
from marianne.validation.checks.capability_classes import _ClassContext
from marianne.validation.checks.instrument_effort import SheetEffortCheck
from marianne.validation.checks.instrument_tools import SheetToolsCheck

COPY = (
    "name: claude-code\ndisplay_name: Copy\nkind: cli\n"
    "cli:\n  command:\n    executable: claude\n    prompt_flag: '-p'\n"
    "  output:\n    format: text\n"
)


def _ctx(tmp_path: Path, profile: InstrumentProfile) -> _ClassContext:
    class_map = load_class_map(
        user_path=tmp_path / "u.yaml", venue_path=tmp_path / "v.yaml",
        profile_names={profile.name},
    )
    return _ClassContext(profiles={profile.name: profile}, class_map=class_map)


def _score(tmp_path: Path, **config: Any) -> JobConfig:
    return JobConfig.model_validate({
        "name": "s", "workspace": str(tmp_path / "ws"), "instrument": "claude-code",
        "sheet": {"size": 1, "total_items": 1}, "prompt": {"template": "x"},
        "instrument_config": config,
    })


def _shadow(tmp_path: Path, text: str = COPY) -> InstrumentProfile:
    org = tmp_path / "org"
    org.mkdir(exist_ok=True)
    (org / "claude-code.yaml").write_text(text)
    return InstrumentProfileLoader.load_directory(org)["claude-code"]


def _run(check: Any, module: str, cfg: JobConfig, ctx: _ClassContext) -> list[Any]:
    with patch(f"marianne.validation.checks.{module}._load_context", return_value=ctx):
        return list(check.check(cfg, Path("s.yaml"), "instrument_config:\n  x: 1\n"))


def test_v335_names_the_shadowing_file_and_extends(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _shadow(tmp_path))
    issues = _run(SheetEffortCheck(), "instrument_effort", _score(tmp_path, effort="low"), ctx)
    assert [i.check_id for i in issues] == ["V335"]
    assert "claude-code.yaml" in issues[0].message
    assert "shadows it whole" in issues[0].message
    assert "`extends: claude-code`" in issues[0].message
    assert "declares effort:" in issues[0].message


def test_v337_names_the_shadowing_file_and_extends(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _shadow(tmp_path))
    issues = _run(SheetToolsCheck(), "instrument_tools", _score(tmp_path, tools=["Read"]), ctx)
    assert [i.check_id for i in issues] == ["V337"]
    assert "shadows it whole" in issues[0].message
    assert "declares tools:" in issues[0].message


def test_no_sentence_when_no_builtin_declares_the_mapping(tmp_path: Path) -> None:
    org = tmp_path / "org"
    org.mkdir()
    (org / "my-seat.yaml").write_text(COPY.replace("claude-code", "my-seat"))
    profile = InstrumentProfileLoader.load_directory(org)["my-seat"]
    ctx = _ctx(tmp_path, profile)
    cfg = JobConfig.model_validate({
        "name": "s", "workspace": str(tmp_path / "ws"), "instrument": "my-seat",
        "sheet": {"size": 1, "total_items": 1}, "prompt": {"template": "x"},
        "instrument_config": {"effort": "low"},
    })
    issues = _run(SheetEffortCheck(), "instrument_effort", cfg, ctx)
    assert [i.check_id for i in issues] == ["V335"]
    assert "shadows" not in issues[0].message


def test_no_sentence_for_a_profile_loaded_from_the_builtin_layer(tmp_path: Path) -> None:
    from marianne.instruments.loader import profile_source_dirs

    b = InstrumentProfileLoader.load_directory(profile_source_dirs()[0])["claude-code-ollama"]
    stripped = InstrumentProfile.model_validate(
        b.model_dump(mode="json", exclude_none=True) | {"effort": None, "tools": None},
    )
    stripped._source_path = b._source_path
    ctx = _ctx(tmp_path, stripped)
    cfg = _score(tmp_path, effort="low").model_copy(update={"instrument": "claude-code-ollama"})
    issues = _run(SheetEffortCheck(), "instrument_effort", cfg, ctx)
    assert all("shadows" not in i.message for i in issues)
