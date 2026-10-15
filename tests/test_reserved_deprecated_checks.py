"""V327/V328/D6 fixtures: reserved keys, deprecated spellings, wrong place.

Real check classes against real score files (GH #436 loud_silence stage).
The legacy tests in test_technique_validation.py exercise local
reimplementations; these pin the registered behaviour.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.core.config.schema_walk import strip_unknown_score_fields
from marianne.validation.checks.reserved import DeprecatedSpellingCheck, ReservedKeyUseCheck
from marianne.validation.checks.schema import unknown_field_issue

BASE = (
    "name: t\n"
    "workspace: {ws}\n"
    "sheet:\n"
    "  size: 1\n"
    "  total_items: 1\n"
    "prompt:\n"
    "  template: hi\n"
)


def _config(tmp_path: Path, extra: str) -> tuple[JobConfig, Path, str]:
    score = tmp_path / "score.yaml"
    text = BASE.format(ws=tmp_path.as_posix()) + extra
    score.write_text(text, encoding="utf-8")
    return JobConfig.from_yaml(score), score, text


def test_v327_fires_on_reserved_subkey(tmp_path: Path) -> None:
    config, path, raw = _config(tmp_path, "learning:\n  min_confidence_threshold: 0.5\n")
    issues = ReservedKeyUseCheck().check(config, path, raw)
    assert [i.check_id for i in issues] == ["V327"]
    assert "learning.min_confidence_threshold" in issues[0].message


def test_v327_silent_when_reserved_keys_absent(tmp_path: Path) -> None:
    config, path, raw = _config(tmp_path, "")
    assert ReservedKeyUseCheck().check(config, path, raw) == []


def test_v327_reports_wholly_reserved_section_once(tmp_path: Path) -> None:
    config, path, raw = _config(
        tmp_path, "rate_limit:\n  wait_minutes: 30\n  max_waits: 2\n"
    )
    issues = ReservedKeyUseCheck().check(config, path, raw)
    assert len(issues) == 1
    assert "`rate_limit`" in issues[0].message


def test_v327_silent_for_wired_movement_voices(tmp_path: Path) -> None:
    config, path, raw = _config(tmp_path, "movements:\n  1:\n    voices: 3\n")
    issues = ReservedKeyUseCheck().check(config, path, raw)
    assert not any("voices" in i.message for i in issues)
    assert config.sheet.total_sheets == 3


def test_v327_warns_when_voices_cannot_expand(tmp_path: Path) -> None:
    text = BASE.format(ws=tmp_path.as_posix()).replace("size: 1", "size: 2")
    text += "movements:\n  1:\n    voices: 3\n"
    path = tmp_path / "score.yaml"
    path.write_text(text, encoding="utf-8")
    config = JobConfig.from_yaml(path)
    issues = ReservedKeyUseCheck().check(config, path, text)
    assert any("sheet.size" in issue.message for issue in issues)


def test_v327_silent_for_read_keys(tmp_path: Path) -> None:
    # retry.max_retries is read at runtime; setting it must NOT warn.
    config, path, raw = _config(tmp_path, "retry:\n  max_retries: 2\n")
    assert ReservedKeyUseCheck().check(config, path, raw) == []


def test_v328_fires_on_old_checkpoints_spelling(tmp_path: Path) -> None:
    config, path, raw = _config(
        tmp_path, "checkpoints:\n  triggers:\n    - name: g\n      sheet_nums: [1]\n"
    )
    issues = DeprecatedSpellingCheck().check(config, path, raw)
    assert any("approvals" in i.message for i in issues)


def test_v328_silent_on_new_approvals_spelling(tmp_path: Path) -> None:
    config, path, raw = _config(
        tmp_path, "approvals:\n  triggers:\n    - name: g\n      sheet_nums: [1]\n"
    )
    issues = DeprecatedSpellingCheck().check(config, path, raw)
    assert not any("approvals`" in i.message for i in issues)


def test_v328_fires_on_hook_on_failure_homonym(tmp_path: Path) -> None:
    config, path, raw = _config(
        tmp_path, "on_success:\n  - type: run_command\n    command: 'true'\n    on_failure: abort\n"
    )
    issues = DeprecatedSpellingCheck().check(config, path, raw)
    assert any("if_hook_fails" in i.message for i in issues)


def test_v328_fires_on_deprecated_conductor_root(tmp_path: Path) -> None:
    config, path, raw = _config(tmp_path, "conductor:\n  name: x\n")
    issues = DeprecatedSpellingCheck().check(config, path, raw)
    assert [i.check_id for i in issues] == ["V328"]
    assert "conductor" in issues[0].message


def test_d6_wrong_place_key_warns_naming_real_path(tmp_path: Path) -> None:
    _c, _p, raw = _config(tmp_path, "timeout_seconds: 600\n")
    import yaml

    data = yaml.safe_load(raw)
    clean, unknown = strip_unknown_score_fields(data, JobConfig)
    assert len(unknown) == 1
    issue = unknown_field_issue(unknown[0], raw)
    assert issue.check_id == "V010"
    assert issue.severity.value == "warning"
    # Names at least one real path the key lives at (until stage 4 types
    # instrument_config, the sheet-timeout spelling is not among them).
    assert "lives at" in issue.message
    assert "timeout_seconds" in issue.message.split("lives at")[1]


def test_d6_distant_unknown_stays_info(tmp_path: Path) -> None:
    _c, _p, raw = _config(tmp_path, "unknownthing: 1\n")
    import yaml

    data = yaml.safe_load(raw)
    clean, unknown = strip_unknown_score_fields(data, JobConfig)
    issue = unknown_field_issue(unknown[0], raw)
    assert issue.check_id == "V011"
    assert issue.severity.value == "info"


def test_unknown_key_inside_trigger_action_is_tolerated(tmp_path: Path) -> None:
    import yaml

    raw = BASE.format(ws=tmp_path.as_posix()) + (
        "sheet:\n  size: 1\n  total_items: 1\n  triggers:\n"
        "    '1':\n      on_fail:\n        - run:\n"
        "            command: 'true'\n            timeout: 10\n"
    )
    clean, unknown = strip_unknown_score_fields(yaml.safe_load(raw), JobConfig)
    assert clean["sheet"]["triggers"]["1"]["on_fail"][0]["run"] == {"command": "true"}
    assert [(field.path, field.key) for field in unknown] == [
        ("sheet.triggers.1.on_fail[0].run", "timeout")
    ]
