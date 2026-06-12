"""#58: conductor-managed workspaces — auto-derive when a score omits one.

Composer decision (2026-06-12): "Pursue auto-managed workspaces." A loaded
score that omits ``workspace:`` gets one derived under ``~/workspaces/<name>``
(the home-rooted convention), so workspaces "just work" and never scatter
beside the score file. ``--workspace`` becomes a hidden expert override.

Scope is deliberate: derivation happens at the score-LOADING boundary
(``from_yaml`` / ``from_yaml_string``) — the real "a user handed us a score"
path — not in programmatic ``model_validate``, which keeps the historical
``./workspace`` default for internal reconstruction and tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from marianne.core.config import JobConfig
from marianne.core.config.workspace import (
    WORKSPACE_ROOT_ENV,
    default_workspace_for,
    resolve_workspace_root,
    sanitize_workspace_name,
)

_SCORE: dict = {
    "name": "demo-score",
    "instrument": "claude-code",
    "sheet": {"size": 1, "total_items": 1},
    "prompt": {"template": "x"},
}


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "score.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


class TestSanitize:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("legion-dream", "legion-dream"),
            ("DJ GestAIt v2", "DJ-GestAIt-v2"),
            ("fix/emzihypno", "fix-emzihypno"),
            ("a  b__c", "a-b__c"),
            ("--weird--", "weird"),
            ("...", "job"),
            ("", "job"),
        ],
    )
    def test_filesystem_safe_segment(self, name: str, expected: str) -> None:
        assert sanitize_workspace_name(name) == expected


class TestWorkspaceRoot:
    def test_env_override_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(WORKSPACE_ROOT_ENV, "/custom/root")
        assert resolve_workspace_root() == Path("/custom/root")
        assert default_workspace_for("foo") == Path("/custom/root/foo")

    def test_default_is_home_workspaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(WORKSPACE_ROOT_ENV, raising=False)
        # Assert the derived path only — never create it (no home pollution).
        assert resolve_workspace_root() == Path.home() / "workspaces"
        assert default_workspace_for("bar") == Path.home() / "workspaces" / "bar"


class TestFromYamlDerivation:
    def test_omitted_workspace_is_auto_derived(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "roots"
        monkeypatch.setenv(WORKSPACE_ROOT_ENV, str(root))
        cfg = JobConfig.from_yaml(_write(tmp_path, _SCORE))
        assert cfg.workspace == (root / "demo-score").resolve()

    def test_from_yaml_string_omitted_workspace_is_derived(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "roots"
        monkeypatch.setenv(WORKSPACE_ROOT_ENV, str(root))
        cfg = JobConfig.from_yaml_string(yaml.safe_dump(_SCORE))
        assert cfg.workspace == (root / "demo-score").resolve()

    def test_explicit_relative_workspace_wins_and_resolves_to_score_parent(
        self, tmp_path: Path
    ) -> None:
        # An explicit workspace is honored and resolved relative to the score
        # file's parent (#109) — never auto-derived.
        score = _write(tmp_path, {**_SCORE, "workspace": "./out"})
        cfg = JobConfig.from_yaml(score)
        assert cfg.workspace == (tmp_path / "out").resolve()

    def test_explicit_absolute_workspace_is_unchanged(
        self, tmp_path: Path
    ) -> None:
        explicit = tmp_path / "explicit-ws"
        score = _write(tmp_path, {**_SCORE, "workspace": str(explicit)})
        cfg = JobConfig.from_yaml(score)
        assert cfg.workspace == explicit.resolve()

    def test_derived_workspace_survives_yaml_round_trip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Once derived, the workspace is a concrete value: re-loading the
        # serialized score must not re-derive a different path.
        root = tmp_path / "roots"
        monkeypatch.setenv(WORKSPACE_ROOT_ENV, str(root))
        cfg = JobConfig.from_yaml(_write(tmp_path, _SCORE))
        reloaded = JobConfig.from_yaml_string(cfg.to_yaml())
        assert reloaded.workspace == cfg.workspace


class TestScopingToLoaders:
    def test_model_validate_keeps_historical_default(self) -> None:
        # Programmatic construction (internal reconstruction, tests) is NOT a
        # score-load and must keep the ./workspace default — derivation is a
        # loader-boundary behavior only.
        cfg = JobConfig.model_validate(_SCORE)
        assert cfg.workspace == Path("./workspace").resolve()
