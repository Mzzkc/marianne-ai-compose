"""Corpus regression gate: a new validator check must earn every new hit."""

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest

from marianne.core.config import JobConfig
from marianne.instruments import loader
from marianne.validation.runner import ValidationRunner, create_default_checks

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/validation_corpus_baseline.json"
AGENTS = Path("/home/emzi/Projects/AGENTS/agents")


@pytest.mark.parametrize("scope", ["venue", "agents"])
def test_no_new_corpus_hits(scope: str, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = json.loads(FIXTURE.read_text())["files"]
    selected = {name: row for name, row in baseline.items() if name.startswith(scope + ":")}
    root = ROOT if scope == "venue" else AGENTS
    if not root.exists():
        pytest.skip(f"Corpus root absent: {root}; synthetic check tests still run")
    original_loader = loader.load_all_profiles
    monkeypatch.setattr(loader, "load_all_profiles", lru_cache(maxsize=1)(original_loader))
    runner = ValidationRunner(create_default_checks())
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
