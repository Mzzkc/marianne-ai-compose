"""Independent GH #469 regressions for authentication and effective profiles."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from marianne.cli.commands.doctor import _profile_execution_state
from marianne.instruments import loader
from marianne.instruments.availability import probe_profile
from marianne.instruments.class_policy import CLASS_POLICIES
from marianne.instruments.classes import CLASS_VOCABULARY, write_user_classes

AUTH_SIGNALS = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CONFIG",
)


@pytest.mark.parametrize("auth_var", AUTH_SIGNALS)
@pytest.mark.parametrize("declared", ["ready", "unsupported"])
def test_auth_signal_preserves_declared_readiness_without_promoting_builtin(
    monkeypatch: pytest.MonkeyPatch, auth_var: str, declared: str,
) -> None:
    """Every documented signal works; credentials alone never enable the builtin."""
    profile = loader.InstrumentProfileLoader.load_directories(
        [loader.profile_source_dirs()[0]],
    )["gemini-cli"].model_copy(update={"execution_status": declared})
    for name in AUTH_SIGNALS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(auth_var, "test-only-auth-signal")
    assert _profile_execution_state(profile)[0] == declared


def test_missing_auth_reports_primary_key_and_does_not_demote_other_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = loader.InstrumentProfileLoader.load_directories([loader.profile_source_dirs()[0]])
    for name in AUTH_SIGNALS:
        monkeypatch.delenv(name, raising=False)
    gemini = profiles["gemini-cli"].model_copy(update={"execution_status": "ready"})
    status, detail = _profile_execution_state(gemini)
    assert status == "unsupported"
    assert detail is not None and all(name in detail for name in AUTH_SIGNALS)
    assert _profile_execution_state(profiles["codex-cli"])[0] == "ready"


@pytest.mark.parametrize("class_name", ["high-context", "image", "video"])
def test_gemini_policy_priorities_match_packaged_vocabulary(class_name: str) -> None:
    packaged = Path(loader.__file__).parent / "classes" / "default.yaml"
    defaults = yaml.safe_load(packaged.read_text())["classes"]
    assert set(CLASS_POLICIES) == CLASS_VOCABULARY
    assert list(CLASS_POLICIES[class_name].order) == defaults[class_name]


@pytest.fixture
def local_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    shipped = loader.profile_source_dirs()[0]
    dirs = tuple(tmp_path / name for name in ("builtins", "user", "venue"))
    for directory in dirs:
        directory.mkdir()
    for name in ("gemini-cli", "claude-code", "codex-cli"):
        shutil.copyfile(shipped / f"{name}.yaml", dirs[0] / f"{name}.yaml")
    monkeypatch.setattr(loader, "profile_source_dirs", lambda **kwargs: dirs)
    monkeypatch.setattr("shutil.which", lambda name: f"/test/bin/{name}")
    return dirs


@pytest.mark.parametrize("layer", [1, 2], ids=["user", "venue"])
@pytest.mark.parametrize("refusal", ["warning", "raw-prompt"])
def test_effective_gemini_override_cannot_bypass_remaining_policy_gates(
    tmp_path: Path, local_profiles: tuple[Path, Path, Path], layer: int, refusal: str,
) -> None:
    profile = yaml.safe_load((local_profiles[0] / "gemini-cli.yaml").read_text())
    profile["execution_status"] = "warning" if refusal == "warning" else "ready"
    profile["raw_prompt"] = refusal == "raw-prompt"
    (local_profiles[layer] / "gemini-cli.yaml").write_text(yaml.safe_dump(profile))
    _, content, _ = write_user_classes(path=tmp_path / "classes.yaml")
    classes = yaml.safe_load(content)["classes"]
    assert classes["image"] == ["claude-code", "codex-cli"]
    assert classes["video"] == ["claude-code"]
    assert classes["high-context"] == ["codex-cli", "claude-code"]


def test_same_named_local_hint_reorders_gemini_and_generated_entries_pass_shared_probe(
    tmp_path: Path, local_profiles: tuple[Path, Path, Path],
) -> None:
    profile = yaml.safe_load((local_profiles[0] / "gemini-cli.yaml").read_text())
    profile["execution_status"] = "ready"
    profile["class_hints"] = {"image": "after codex-cli", "video": "last"}
    (local_profiles[1] / "gemini-cli.yaml").write_text(yaml.safe_dump(profile))
    destination = tmp_path / "classes.yaml"
    _, content, changed = write_user_classes(path=destination, dry_run=True)
    assert changed is False and not destination.exists()
    classes = yaml.safe_load(content)["classes"]
    assert classes["image"] == ["claude-code", "codex-cli", "gemini-cli"]
    assert classes["video"] == ["claude-code", "gemini-cli"]
    effective = loader.InstrumentProfileLoader.load_directories(list(local_profiles))
    assert all(
        probe_profile(effective[name]).runnable for chain in classes.values() for name in chain
    )


@pytest.mark.parametrize("name", ["claude-code", "codex-cli"])
def test_same_named_unsupported_override_removes_existing_provider_from_all_chains(
    tmp_path: Path, local_profiles: tuple[Path, Path, Path], name: str,
) -> None:
    """The generalized override change must also honor refusals for existing providers."""
    profile = yaml.safe_load((local_profiles[0] / f"{name}.yaml").read_text())
    profile["execution_status"] = "unsupported"
    (local_profiles[1] / f"{name}.yaml").write_text(yaml.safe_dump(profile))
    _, content, _ = write_user_classes(path=tmp_path / "classes.yaml")
    classes = yaml.safe_load(content)["classes"]
    remaining = "codex-cli" if name == "claude-code" else "claude-code"
    assert classes["image"] == [remaining]
    assert all(name not in chain for chain in classes.values())
