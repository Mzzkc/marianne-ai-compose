"""V350: ``system: cam`` settings."""

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
    ok = "{base_url: 'http://127.0.0.1:8321', api_key_env: CAM_API_KEY, timeout_seconds: 5}"
    assert _run(MemoryCamSettingsCheck(), tmp_path, ok) == []
    f = "{base_url: 'https://cam.example.org', api_key_file: /etc/cam/key, allow_remote: true}"
    assert _run(MemoryCamSettingsCheck(), tmp_path, f) == []


def test_other_systems_untouched(tmp_path: Path) -> None:
    assert _run(MemoryCamSettingsCheck(), tmp_path, "{}", system="files") == []


def test_missing_base_url(tmp_path: Path) -> None:
    (i,) = _run(MemoryCamSettingsCheck(), tmp_path, "{api_key_env: CAM_API_KEY}")
    assert i.check_id == "V350" and i.severity is ValidationSeverity.ERROR
    assert "base_url is required" in i.message


def test_both_or_neither_key_setting(tmp_path: Path) -> None:
    for s in (
        "{base_url: 'http://127.0.0.1:1'}",
        "{base_url: 'http://127.0.0.1:1', api_key_env: K, api_key_file: /x}",
    ):
        (i,) = _run(MemoryCamSettingsCheck(), tmp_path, s)
        assert "exactly one of api_key_env and api_key_file" in i.message


def test_literal_key_refused_and_not_echoed(tmp_path: Path) -> None:
    (i,) = _run(
        MemoryCamSettingsCheck(), tmp_path,
        f"{{base_url: 'http://127.0.0.1:1', api_key_env: {SECRET}}}",
    )
    assert "NAME of an environment variable" in i.message
    assert SECRET not in i.message and SECRET not in (i.suggestion or "")
    (j,) = _run(
        MemoryCamSettingsCheck(), tmp_path,
        f"{{base_url: 'http://127.0.0.1:1', api_key_file: {SECRET}}}",
    )
    assert SECRET not in j.message and "api_key_env" in (j.suggestion or "")


def test_remote_plain_http_and_unknown_key(tmp_path: Path) -> None:
    (i,) = _run(
        MemoryCamSettingsCheck(), tmp_path,
        "{base_url: 'http://cam.example.org', api_key_env: K}",
    )
    assert "non-loopback" in i.message
    (j,) = _run(
        MemoryCamSettingsCheck(), tmp_path,
        "{base_url: 'http://127.0.0.1:1', api_key_env: K, api_key: oops}",
    )
    assert "not a cam setting" in j.message
