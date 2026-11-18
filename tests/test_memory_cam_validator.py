"""V350: a ``system: cam`` attachment names an operator endpoint; it never carries a connection."""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.memory import (
    MemoryCamSettingsCheck,
    MemorySystemUnknownCheck,
)
from marianne.validation.runner import create_default_checks

BASE = (
    "name: t\nworkspace: {ws}\nsheet:\n  size: 1\n  total_items: 1\n"
    "prompt:\n  template: hi\n"
)
SECRET = "k9Zq3Lm8Xv2Rt7Yp4Wn6Bc1Dg5Hs0Jf"


def _run(check: object, tmp_path: Path, settings: str, system: str = "cam"):  # type: ignore[no-untyped-def]
    score = tmp_path / "score.yaml"
    text = (
        BASE.format(ws=tmp_path.as_posix())
        + f"memory:\n  backends:\n    main:\n      system: {system}\n      settings: {settings}\n"
    )
    score.write_text(text, encoding="utf-8")
    return check.check(JobConfig.from_yaml(score), score, text)  # type: ignore[attr-defined]


def test_registered_once() -> None:
    assert [c.check_id for c in create_default_checks()].count("V350") == 1


def test_cam_is_a_shipped_system(tmp_path: Path) -> None:
    assert _run(MemorySystemUnknownCheck(), tmp_path, "{}", system="cam") == []


def test_clean_settings(tmp_path: Path) -> None:
    assert _run(MemoryCamSettingsCheck(), tmp_path, "{}") == []
    assert _run(MemoryCamSettingsCheck(), tmp_path, "{endpoint: lab, timeout_seconds: 5}") == []


def test_other_systems_untouched(tmp_path: Path) -> None:
    assert _run(MemoryCamSettingsCheck(), tmp_path, "{}", system="files") == []


def test_score_cannot_carry_the_connection(tmp_path: Path) -> None:
    for key, value in (
        ("base_url", "'http://127.0.0.1:8321'"),
        ("api_key_env", "ANTHROPIC_API_KEY"),
        ("api_key_file", "/home/x/.secrets/token"),
        ("allow_remote", "true"),
    ):
        (i,) = _run(MemoryCamSettingsCheck(), tmp_path, f"{{{key}: {value}}}")
        assert i.check_id == "V350" and i.severity is ValidationSeverity.ERROR
        assert key in i.message and "operator" in i.message
        assert "memory_backends.cam" in (i.suggestion or "")


def test_literal_key_is_refused_and_not_echoed(tmp_path: Path) -> None:
    for key in ("api_key_env", "api_key_file"):
        (i,) = _run(MemoryCamSettingsCheck(), tmp_path, f"{{{key}: {SECRET}}}")
        assert SECRET not in i.message and SECRET not in (i.suggestion or "")


def test_unknown_key_and_bad_values(tmp_path: Path) -> None:
    (i,) = _run(MemoryCamSettingsCheck(), tmp_path, "{api_key: oops}")
    assert "not a cam setting" in i.message
    (j,) = _run(MemoryCamSettingsCheck(), tmp_path, "{endpoint: 'Bad Name'}")
    assert "endpoint" in j.message
    (k,) = _run(MemoryCamSettingsCheck(), tmp_path, "{timeout_seconds: 0}")
    assert "timeout_seconds" in k.message
