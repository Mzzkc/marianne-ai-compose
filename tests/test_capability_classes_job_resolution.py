"""A job receives only named classes, and keeps prior chains on resume."""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.config.job import JobConfig
from marianne.instruments.classes import load_class_map, resolve_job_classes


def _job(**overrides: object) -> JobConfig:
    data: dict[str, object] = {
        "name": "classes-job", "workspace": "/tmp/classes-job",
        "sheet": {"size": 1, "total_items": 1}, "prompt": {"template": "Work"},
    }
    data.update(overrides)
    return JobConfig.model_validate(data)


def test_snapshot_keeps_existing_class_after_map_edit(tmp_path: Path) -> None:
    user = tmp_path / "classes.yaml"
    user.write_text("version: 1\nclasses:\n  strong: [claude-code, codex-cli]\n")
    job = _job(instrument="strong")
    profiles = {"claude-code", "codex-cli", "antigravity"}
    first = resolve_job_classes(
        job, profiles, load_class_map(user_path=user, venue_path=tmp_path / "absent"),
    )
    assert first is not None
    assert set(first.classes) == {"strong"}
    user.write_text("version: 1\nclasses:\n  strong: [antigravity]\n  review: [codex-cli]\n")
    current = load_class_map(user_path=user, venue_path=tmp_path / "absent")
    resumed = resolve_job_classes(job, profiles, current, previous=first, phase="resume")
    assert resumed is not None
    assert resumed.digest == first.digest
    assert [e.profile for e in resumed.classes["strong"].chain] == ["claude-code", "codex-cli"]
    edited = _job(instrument="strong", instrument_fallbacks=["review"])
    extended = resolve_job_classes(edited, profiles, current, previous=first, phase="resume")
    assert extended is not None
    assert extended.classes["strong"] == first.classes["strong"]
    assert extended.classes["review"].resolved_at == "resume"


def test_unconfigured_vocabulary_class_refuses(tmp_path: Path) -> None:
    loaded = load_class_map(user_path=tmp_path / "missing", venue_path=tmp_path / "absent")
    with pytest.raises(ValueError, match="no instruments configured"):
        resolve_job_classes(_job(instrument="image"), {"claude-code"}, loaded)


@pytest.mark.parametrize(
    "overrides",
    [
        {"instrument": "unknown-agent"},                                     # primary seat
        {"instrument": "claude-code", "instrument_fallbacks": ["not-here"]},  # fallback-only (I6)
    ],
)
def test_unknown_non_class_name_is_not_the_class_seams_business(
    overrides: dict[str, object], tmp_path: Path,
) -> None:
    """D-I1 (Blueprint Integration C-I1): the class seam refuses only states
    classes create. An unknown non-class name is S3's (V210/V211 ERROR) and
    dispatch's, exactly as before classes existed — 6 of 68 tracked scores on
    a fresh install were otherwise refused at submit though main admits them."""
    loaded = load_class_map(user_path=tmp_path / "missing", venue_path=tmp_path / "absent")
    assert resolve_job_classes(_job(**overrides), {"claude-code"}, loaded) is None


def test_alias_precedes_class_and_guarded_route_refuses_class(tmp_path: Path) -> None:
    loaded = load_class_map(user_path=tmp_path / "missing", venue_path=tmp_path / "absent")
    aliases = _job(instrument="strong", instruments={"strong": {"profile": "claude-code"}})
    assert resolve_job_classes(aliases, {"claude-code"}, loaded) is None
    with pytest.raises(ValueError, match="classes cannot be used with a reviewed route"):
        resolve_job_classes(_job(instrument="strong"), {"claude-code"}, loaded, expected_route=True)


def test_score_model_cannot_override_class_choice(tmp_path: Path) -> None:
    loaded = load_class_map(user_path=tmp_path / "missing", venue_path=tmp_path / "absent")
    with pytest.raises(ValueError, match="model 'unrelated' cannot apply to class 'strong'"):
        resolve_job_classes(
            _job(instrument="strong", instrument_config={"model": "unrelated"}),
            {"claude-code", "codex-cli", "antigravity", "opencode"}, loaded,
        )
