"""Setup owns the generated map without overwriting a user's edits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config.classes import canonical_json
from marianne.instruments.classes import write_user_classes


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


def test_writer_provenance_backup_and_hand_edit_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marianne.instruments import class_policy

    monkeypatch.setattr(class_policy, "_ollama_responds", lambda profile: False)
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
