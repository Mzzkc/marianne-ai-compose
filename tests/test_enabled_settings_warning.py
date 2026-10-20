"""Release-N warnings precede the promised switch semantics change."""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.config import JobConfig
from marianne.validation.checks.reserved import (
    SWITCH_WARNING_PATHS,
    EnabledSettingsWarningCheck,
)
from marianne.validation.runner import ValidationRunner, create_default_checks


def _validate(tmp_path: Path, settings: str) -> list[str]:
    score = tmp_path / "score.yaml"
    score.write_text(
        "name: switch-proof\nworkspace: ./output\n"
        "sheet: {size: 1, total_items: 1}\nprompt: {template: hi}\n"
        + settings,
        encoding="utf-8",
    )
    config = JobConfig.from_yaml(score)
    runner = ValidationRunner(create_default_checks())
    return [i.message for i in runner.validate(config, score, score.read_text())
            if i.check_id == "V329"]


@pytest.mark.parametrize(
    ("settings", "path"),
    [
        ("parallel: {max_concurrent: 2}\n", "parallel"),
        ("stale_detection: {idle_timeout_seconds: 60}\n", "stale_detection"),
        ("concert: {max_chain_depth: 2}\n", "concert"),
        ("judgment: {instrument: cli}\n", "judgment"),
    ],
)
def test_readable_settings_warn_without_enabling(
    tmp_path: Path, settings: str, path: str
) -> None:
    messages = _validate(tmp_path, settings)
    assert len(messages) == 1
    assert path in messages[0]
    assert "from the next release" in messages[0]
    assert "enabled: false" in messages[0]


@pytest.mark.timeout(120)
def test_explicit_false_and_reserved_sections_do_not_promise_activation(tmp_path: Path) -> None:
    assert _validate(tmp_path, "parallel: {enabled: false, max_concurrent: 2}\n") == []
    assert _validate(tmp_path, "ai_review: {min_score: 70}\n") == []
    assert _validate(tmp_path, "learning: {exploration_budget: {floor: 0.1}}\n") == []


def test_warning_scope_is_reader_reviewed() -> None:
    assert frozenset({
        "isolation", "code_execution", "parallel",
        "stale_detection", "judgment", "concert",
    }) == SWITCH_WARNING_PATHS
    assert EnabledSettingsWarningCheck().check_id == "V329"


@pytest.mark.parametrize("section", ["exploration_budget", "entropy_response", "auto_apply"])
def test_unread_learning_sections_only_warn_reserved(tmp_path: Path, section: str) -> None:
    score = tmp_path / "score.yaml"
    score.write_text(
        "name: reserved-learning\nworkspace: ./output\n"
        "sheet: {size: 1, total_items: 1}\nprompt: {template: hi}\n"
        f"learning: {{{section}: {{enabled: false}}}}\n",
        encoding="utf-8",
    )
    config = JobConfig.from_yaml(score)
    issues = ValidationRunner(create_default_checks()).validate(config, score, score.read_text())
    relevant = [issue.check_id for issue in issues if section in issue.message]
    assert relevant == ["V327"]
