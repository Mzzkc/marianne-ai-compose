"""Corpus regression gate: a new validator check must earn every new hit."""

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest

from marianne.core.config import JobConfig
from marianne.instruments import availability, classes, loader
from marianne.validation.runner import ValidationRunner, create_default_checks

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/validation_corpus_baseline.json"
AGENTS = Path("/home/emzi/Projects/AGENTS/agents")


def test_venue_yaml_inventory_is_covered() -> None:
    baseline = json.loads(FIXTURE.read_text())["files"]
    actual = {f"venue:{path.relative_to(ROOT)}"
              for root in (ROOT / "examples", ROOT / "scores")
              for path in root.rglob("*.yaml")
              if "rosetta-corpus" not in path.parts}
    missing = actual - set(baseline)
    assert not missing, f"New score YAML lacks a reviewed sweep row: {sorted(missing)}"


@pytest.mark.timeout(180)
@pytest.mark.parametrize("scope", ["venue", "agents"])
def test_no_new_corpus_hits(
    scope: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    baseline = json.loads(FIXTURE.read_text())["files"]
    selected = {name: row for name, row in baseline.items() if name.startswith(scope + ":")}
    root = ROOT if scope == "venue" else AGENTS
    if not root.exists():
        pytest.skip(f"Corpus root absent: {root}; synthetic check tests still run")
    # Hermetic: the baseline was captured against the SHIPPED profiles with
    # every instrument treated as installed. A developer's ~/.marianne and
    # .marianne layers, their PATH, and their class map are facts about one
    # machine, not about the corpus; CI's bare runner (CI run 38644391651)
    # grew V215 ×60, V310 ×20, V311 ×6, V210 ×6 on the same scores.
    builtins_only = lru_cache(maxsize=1)(
        lambda: loader.load_all_profiles(
            organization_dir=tmp_path / "no-org", venue_dir=tmp_path / "no-venue",
        )
    )
    monkeypatch.setattr(loader, "load_all_profiles", builtins_only)
    monkeypatch.setattr(availability, "check_profile_available", lambda profile: (True, ""))
    monkeypatch.setattr(
        availability, "check_instrument_available", lambda name, registry: (True, "")
    )
    monkeypatch.setattr(classes, "class_source_paths", lambda **_: (
        Path(classes.__file__).parent / "classes" / "default.yaml",
        tmp_path / "no-user-classes.yaml",
        tmp_path / "no-venue-classes.yaml",
    ))
    runner = ValidationRunner(
        [c for c in create_default_checks() if c.check_id != "V002"]
    )  # V002 (workspace parent exists) is about the machine's filesystem
    checked = 0
    for name, expected in selected.items():
        path = root / name.split(":", 1)[1]
        if expected.get("absent") and not path.exists():
            continue  # capture-time source drift recorded by the fixture
        assert path.exists(), f"Corpus member disappeared without a baseline update: {path}"
        if expected.get("parse") == "error":
            with pytest.raises(ValueError):
                JobConfig.from_yaml(path)
            continue
        config = JobConfig.from_yaml(path)
        actual = Counter(
            issue.check_id for issue in runner.validate(config, path, path.read_text())
        )
        prior = expected["issues"]
        growth = {
            code: count - prior.get(code, 0)
            for code, count in actual.items()
            if count > prior.get(code, 0)
        }
        assert not growth, (
            f"New validation hits in {name}: {growth}; read the score before refreshing"
        )
        checked += 1
    assert checked > 0, f"Corpus sweep vacuous for {scope}: {root}"
