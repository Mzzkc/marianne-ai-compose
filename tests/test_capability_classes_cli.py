"""Setup owns the generated map without overwriting a user's edits."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config.classes import canonical_json
from marianne.instruments.classes import write_user_classes


@pytest.fixture
def gemini_profile_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path]:
    """Use real shipped CLI profiles and loader; isolate host and binary presence."""
    from marianne.instruments import loader

    shipped = loader.profile_source_dirs()[0]
    dirs = tuple(tmp_path / name for name in ("builtins", "user", "venue"))
    for directory in dirs:
        directory.mkdir()
    for name in ("gemini-cli", "claude-code", "codex-cli"):
        shutil.copyfile(shipped / f"{name}.yaml", dirs[0] / f"{name}.yaml")
    monkeypatch.setattr(loader, "profile_source_dirs", lambda: dirs)
    monkeypatch.setattr("shutil.which", lambda name: f"/test/bin/{name}")
    return dirs


def test_gemini_writer_emits_multimodal_and_context_classes_without_enabling_builtin(
    tmp_path: Path, gemini_profile_dirs: tuple[Path, Path, Path],
) -> None:
    """The unsupported shipped Gemini stays excluded while fallbacks are emitted."""
    _, content, _ = write_user_classes(path=tmp_path / "classes.yaml")
    classes = yaml.safe_load(content)["classes"]
    assert classes["high-context"] == ["codex-cli", "claude-code"]
    assert classes["image"] == ["claude-code", "codex-cli"]
    assert classes["video"] == ["claude-code"]
    assert all("gemini-cli" not in chain for chain in classes.values())


_LAYER_DIRS = {"user": 1, "venue": 2}


@pytest.mark.parametrize("layer", ["user", "venue"])
@pytest.mark.parametrize(
    ("status", "vision", "installed", "seated"),
    [("ready", True, True, True), ("unsupported", True, True, False),
     ("ready", False, True, False), ("ready", True, False, False)],
    ids=["ready", "unsupported", "no-vision", "missing-binary"],
)
def test_gemini_writer_uses_effective_override_and_keeps_policy_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    gemini_profile_dirs: tuple[Path, Path, Path],
    layer: str, status: str, vision: bool, installed: bool, seated: bool,
) -> None:
    """A runnable override is seated; status, vision, and binary gates still apply."""
    dirs = gemini_profile_dirs
    profile = yaml.safe_load((dirs[0] / "gemini-cli.yaml").read_text())
    profile["execution_status"] = status
    profile["execution_status_detail"] = "Local test configuration"
    if not vision:
        profile["capabilities"].remove("vision")
    (dirs[_LAYER_DIRS[layer]] / "gemini-cli.yaml").write_text(yaml.safe_dump(profile))
    if not installed:
        monkeypatch.setattr(
            "shutil.which", lambda name: None if name == "gemini" else f"/test/bin/{name}",
        )
    _, content, _ = write_user_classes(path=tmp_path / "classes.yaml")
    classes = yaml.safe_load(content)["classes"]
    assert classes["image"] == (["gemini-cli"] if seated else []) + ["claude-code", "codex-cli"]
    assert classes["video"] == (["gemini-cli"] if seated else []) + ["claude-code"]
    # high-context needs no vision but still refuses unsupported or absent CLIs.
    expected_context = ["codex-cli"]
    if status == "ready" and installed:
        expected_context.append("gemini-cli")
    assert classes["high-context"] == expected_context + ["claude-code"]


def test_gemini_writer_venue_override_wins_over_ready_user_override(
    tmp_path: Path, gemini_profile_dirs: tuple[Path, Path, Path],
) -> None:
    dirs = gemini_profile_dirs
    profile = yaml.safe_load((dirs[0] / "gemini-cli.yaml").read_text())
    profile["execution_status"] = "ready"
    (dirs[1] / "gemini-cli.yaml").write_text(yaml.safe_dump(profile))
    profile["execution_status"] = "unsupported"
    (dirs[2] / "gemini-cli.yaml").write_text(yaml.safe_dump(profile))
    _, content, _ = write_user_classes(path=tmp_path / "classes.yaml")
    classes = yaml.safe_load(content)["classes"]
    assert classes["image"] == ["claude-code", "codex-cli"]
    assert all("gemini-cli" not in chain for chain in classes.values())


def test_show_json_names_layers_and_entry_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["instruments", "classes", "show", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert [layer["layer"] for layer in report["layers"]] == ["default", "user", "venue"]
    assert report["classes"]["strong"]["source_layer"] == "default"
    assert report["classes"]["strong"]["chain"][0]["profile"] == "claude-code"
    assert isinstance(report["classes"]["strong"]["chain"][0]["availability"], bool)


def test_classes_check_refuses_a_chain_with_no_available_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    user_file = tmp_path / ".marianne" / "classes.yaml"
    user_file.parent.mkdir()
    user_file.write_text(
        "version: 1\nclasses:\n  strong: [no-such-marianne-profile-8384]\n"
    )
    result = CliRunner().invoke(app, ["instruments", "classes", "check", "--class", "strong"])
    assert result.exit_code == 1
    assert "unavailable chains" in result.output


def test_classes_check_treats_unsupported_profile_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forge Inspect P2: a sole chain entry marked execution_status: unsupported
    was reported available and `check` exited 0."""
    monkeypatch.setenv("HOME", str(tmp_path))
    profiles = tmp_path / ".marianne" / "instruments"
    profiles.mkdir(parents=True)
    (profiles / "unsupported-http-8384.yaml").write_text(
        "name: unsupported-http-8384\n"
        "display_name: Unsupported\n"
        "description: probe\n"
        "kind: http\n"
        "execution_status: unsupported\n"
        "execution_status_detail: declared unsupported for this test\n"
        "http:\n  base_url: http://127.0.0.1:9\n  endpoint: /chat/completions\n"
        "  schema_family: openai\n"
    )
    (tmp_path / ".marianne" / "classes.yaml").write_text(
        "version: 1\nclasses:\n  strong: [unsupported-http-8384]\n"
    )
    shown = CliRunner().invoke(app, ["instruments", "classes", "show", "--json"])
    assert shown.exit_code == 0
    entry = json.loads(shown.stdout)["classes"]["strong"]["chain"][0]
    assert entry["availability"] is False
    assert "unsupported" in entry["reason"]
    checked = CliRunner().invoke(app, ["instruments", "classes", "check", "--class", "strong"])
    assert checked.exit_code == 1


def test_writer_provenance_backup_and_hand_edit_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marianne.instruments import availability, class_policy

    # The writer uses the ONE probe (GH #447); keep the local server out of it.
    original = class_policy.probe_profile
    monkeypatch.setattr(
        class_policy, "probe_profile",
        lambda profile: availability.ProfileProbe("unreachable", "no server")
        if profile.kind == "http" else original(profile),
    )
    destination = tmp_path / "classes.yaml"
    path, content, changed = write_user_classes(path=destination)
    assert path == destination and changed and destination.read_text() == content
    data = yaml.safe_load(content)
    import hashlib

    assert (
        data["generated"]["body_sha256"]
        == hashlib.sha256(canonical_json(data["classes"])).hexdigest()
    )
    assert write_user_classes(path=destination, if_absent=True)[2] is False
    data["classes"]["strong"] = ["codex-cli"]
    destination.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="hand edits"):
        write_user_classes(path=destination)
    assert write_user_classes(path=destination, force=True)[2] is True
    backups = list((tmp_path / "backups").glob("classes-*.yaml"))
    assert len(backups) == 1
    assert yaml.safe_load(backups[0].read_text())["classes"]["strong"] == ["codex-cli"]


def test_generated_policy_rows_are_ordered_and_capped() -> None:
    from marianne.instruments.class_policy import CLASS_POLICIES, select_class_chains

    profile_names = {name for policy in CLASS_POLICIES.values() for name in policy.order}
    probe = {
        "openrouter_api_key": True,
        "profiles": {
            name: {
                "available": True,
                "execution_status": "ready",
                "capabilities": ["tool_use", "file_editing", "thinking", "vision"],
                "zero_cost": True,
                "raw_prompt": False,
            }
            for name in profile_names
        },
    }
    selected = select_class_chains(probe)
    for name, policy in CLASS_POLICIES.items():
        assert selected[name] == list(policy.order[:4])
    probe["openrouter_api_key"] = False
    selected = select_class_chains(probe)
    assert "crush" not in selected["fast"]  # moved behind the four-entry cap


def test_writer_and_checker_agree_on_a_local_server_that_lacks_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GH #447: the writer probed /api/tags (200 → available) and wrote
    `local: [ollama]`; `classes check` then refused the same home because the
    server does not serve `llama3.1:8b`. Both now use probe_profile."""
    from marianne.instruments import availability

    monkeypatch.setattr(
        availability, "served_local_models", lambda base_url: ["gemma4:26b"]
    )
    monkeypatch.setattr("shutil.which", lambda name: None)
    destination = tmp_path / "classes.yaml"
    _path, content, _changed = write_user_classes(path=destination)
    data = yaml.safe_load(content)
    assert "ollama" not in {name for chain in data["classes"].values() for name in chain}



# ─── GH #469: user/venue profiles seat themselves through class_hints ─────


def _probe_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "available": True,
        "execution_status": "ready",
        "capabilities": ["tool_use", "file_editing", "thinking"],
        "zero_cost": False,
        "raw_prompt": False,
        "class_hints": {},
    }
    row.update(overrides)
    return row


def test_hinted_user_profile_is_seated_where_its_hint_says() -> None:
    from marianne.instruments.class_policy import CLASS_POLICIES, select_class_chains

    names = {name for policy in CLASS_POLICIES.values() for name in policy.order}
    probe = {
        "openrouter_api_key": True,
        "profiles": {name: _probe_row() for name in names},
    }
    probe["profiles"]["opencode-glm"] = _probe_row(class_hints={
        "strong": ("after", "opencode"),
        "fast": ("first", None),
        "review": ("last", None),
        "writing": ("before", "no-such-profile"),
    })
    selected = select_class_chains(probe)
    strong = list(CLASS_POLICIES["strong"].order)
    assert selected["strong"][: strong.index("opencode") + 2] == (
        strong[: strong.index("opencode") + 1] + ["opencode-glm"]
    )[:4]
    assert selected["fast"][0] == "opencode-glm"
    # `last` and an absent anchor both seat behind the shipped entries, so a
    # full chain caps them out — that is the shipped order winning.
    assert "opencode-glm" not in selected["review"][:4] or len(CLASS_POLICIES["review"].order) < 4
    # A hint never exempts the profile from the class's own probe.
    probe["profiles"]["opencode-glm"] = _probe_row(
        available=False, class_hints={"fast": ("first", None)},
    )
    assert "opencode-glm" not in select_class_chains(probe)["fast"]


def test_classes_write_explains_why_a_hinted_http_profile_is_not_seated() -> None:
    """GH #494: a local http profile hinted into workhorse was silently
    dropped; the explainer names the capability it lacks and the route."""
    from marianne.instruments.class_policy import explain_hinted_exclusions

    probe = {
        "openrouter_api_key": False,
        "profiles": {
            "localbox": _probe_row(
                kind="http", capabilities=["structured_output"], zero_cost=True,
                class_hints={"workhorse": ("first", None), "fast": ("first", None),
                             "cheap": ("first", None)},
            ),
            "quiet": _probe_row(class_hints={}),
            "offline": _probe_row(available=False, class_hints={"fast": ("first", None)}),
        },
    }
    lines = explain_hinted_exclusions(probe)
    workhorse = [line for line in lines if "'workhorse'" in line]
    assert len(workhorse) == 1 and workhorse[0].startswith("localbox:")
    assert "file_editing" in workhorse[0] and "opencode" in workhorse[0]
    # fast and cheap admit a zero-cost http profile: no line for them.
    assert not [line for line in lines if "'fast'" in line and line.startswith("localbox")]
    assert not [line for line in lines if "'cheap'" in line]
    assert any(line.startswith("offline:") and "not available" in line for line in lines)
    assert not any(line.startswith("quiet:") for line in lines)


def test_unhinted_user_profile_is_never_seated_and_hints_are_parsed() -> None:
    from marianne.core.config.instruments import InstrumentProfile
    from marianne.instruments.class_policy import parse_class_hint, probe_builtin_profiles
    from marianne.instruments.loader import InstrumentProfileLoader, profile_source_dirs

    builtins = InstrumentProfileLoader.load_directories([profile_source_dirs()[0]])
    base = builtins["opencode"].model_dump()
    base["name"] = "quiet-user-profile"
    quiet = InstrumentProfile.model_validate(base)
    base["name"] = "loud-user-profile"
    base["class_hints"] = {"strong": "after opencode"}
    loud = InstrumentProfile.model_validate(base)
    probe = probe_builtin_profiles(
        builtins, {"quiet-user-profile": quiet, "loud-user-profile": loud},
    )
    assert "quiet-user-profile" not in probe["profiles"]
    loud_row = probe["profiles"]["loud-user-profile"]
    assert loud_row["class_hints"] == {"strong": ("after", "opencode")}
    assert probe["profiles"]["opencode"]["class_hints"] == {}
    assert parse_class_hint("first") == ("first", None)
    assert parse_class_hint("before  codex-cli") == ("before", "codex-cli")
    with pytest.raises(ValueError, match="not one of"):
        parse_class_hint("somewhere near the top")
    base["class_hints"] = {"strong": "somewhere near the top"}
    with pytest.raises(ValueError, match="class_hints.strong"):
        InstrumentProfile.model_validate(base)
