"""The composing release checker refuses a required injection over its max_bytes (GH #515).

The #474 dispatch guard fails a sheet whose required injection exceeds its cap;
until now the only earlier signal was V334 as a WARNING, and the release
checker wrote a lock over the file. A fleet member passed three static gates
that way and died at sheet 1 (candidate-advantage-orchestra, 2026-11-09).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "plugins" / "marianne" / "skills" / "composing" / "scripts" / "check_score_release.py"
)


def _checker():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("check_score_release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_score_release"] = module
    spec.loader.exec_module(module)
    return module


def _score(tmp_path: Path, spec_size: int, required: bool, cap: int | None) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "spec.md").write_text("x" * spec_size)
    ws = tmp_path / "ws"
    cap_line = f"max_bytes: {cap}" if cap is not None else ""
    score = tmp_path / "score.yaml"
    score.write_text(f"""
name: cap-probe
workspace: {ws}
sheet:
  size: 1
  total_items: 1
  prelude:
    - file: {tmp_path / "spec.md"}
      as: context
      required: {str(required).lower()}
      {cap_line}
prompt:
  template: "Build it"
validations:
  - type: command_succeeds
    command: "true"
    description: "runs"
""")
    return score


def test_required_injection_over_cap_is_a_finding(tmp_path: Path) -> None:
    mod = _checker()
    score = _score(tmp_path, spec_size=1500, required=True, cap=1000)
    findings = mod.check_score(score, tmp_path / "project")
    assert any("over its max_bytes 1,000" in f and "1,500 bytes" in f for f in findings), findings


def test_optional_or_uncapped_injection_is_not_a_finding(tmp_path: Path) -> None:
    mod = _checker()
    for required, cap in ((False, 1000), (True, None), (True, 2000)):
        case = tmp_path / f"{required}-{cap}"
        case.mkdir()
        score = _score(case, spec_size=1500, required=required, cap=cap)
        findings = mod.check_score(score, case / "project")
        assert not any("max_bytes" in f for f in findings), (required, cap, findings)
