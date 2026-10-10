"""The accepted capability-class file contract (PO-C1)."""

# Fixture YAML stays readable as complete source text.
# ruff: noqa: E501

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from yaml import YAMLError

from marianne.core.config.classes import ClassesFile
from marianne.instruments.classes import load_class_map, parse_classes_file


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("version: 1\nclasses:\n  strong: []\n", "empty chain"),
        ("version: 1\nclasses:\n  cli: [claude-code]\n", "builtin instrument profile name"),
        ("version: 1\ndefaults: {}\nclasses:\n  strong: [claude-code]\n", "Extra inputs"),
        (
            "version: 1\nclasses:\n  strong:\n    - {profile: claude-code, config: {timeout_seconds: 300}}\n",
            "Extra inputs",
        ),
        ("version: 1\nclasses:\n  Strong: [claude-code]\n", "String should match pattern"),
        ("version: 1\nclasses:\n  strong_one: [claude-code]\n", "String should match pattern"),
        ("classes:\n  strong: [claude-code]\n", "Field required"),
        ("version: 2\nclasses:\n  strong: [claude-code]\n", "Input should be 1"),
        ("version: 1\nclasses:\n  strong: [claude-code]\n  strong: [codex-cli]\n", "duplicate key"),
        ("version: 1\nclasses:\n  strong: [123]\n", "Input should be a valid dictionary"),
        ("version: 1\nclasses:\n  strong: [claude-code, claude-code]\n", "same step twice"),
        ("version: 1\nclasses:\n  strong: ['']\n", "at least 1 character"),
        (
            "version: 1\ngenerated: {tool: mzt-instruments-classes-write, at: '2026-10-10T00:00:00Z', probe_sha256: xyz, body_sha256: xyz}\nclasses:\n  strong: [claude-code]\n",
            "String should match pattern",
        ),
        (
            "version: 1\ngenerated: {tool: setup-script, at: '2026-10-10T00:00:00Z', probe_sha256: "
            + "0" * 64
            + ", body_sha256: "
            + "0" * 64
            + "}\nclasses:\n  strong: [claude-code]\n",
            "Input should be",
        ),
    ],
)
def test_invalid_class_file_is_rejected(source: str, message: str) -> None:
    with pytest.raises((ValidationError, YAMLError, ValueError), match=message):
        parse_classes_file(source)


@pytest.mark.parametrize(
    "source",
    [
        "version: 1\nclasses:\n  local: null\n",
        "version: 1\nclasses:\n  strong:\n    - {profile: claude-code, config: {model: claude-opus-5-5}}\n    - codex-cli\n",
        "version: 1\nclasses:\n  strong:\n    - {profile: codex-cli, config: {model: gpt-6-sol}}\n    - {profile: codex-cli, config: {model: gpt-6-luna}}\n",
    ],
)
def test_valid_class_file_loads(source: str) -> None:
    assert isinstance(parse_classes_file(source), ClassesFile)


def test_layer_replaces_whole_chain_and_tombstone_removes(tmp_path: Path) -> None:
    user = tmp_path / "classes.yaml"
    venue = tmp_path / "venue.yaml"
    user.write_text("version: 1\nclasses:\n  strong: [codex-cli]\n")
    venue.write_text("version: 1\nclasses:\n  local: null\n  review: [antigravity]\n")
    loaded = load_class_map(user_path=user, venue_path=venue)
    assert [entry.profile for entry in loaded.classes["strong"].chain] == ["codex-cli"]
    assert "local" not in loaded.classes
    assert loaded.classes["review"].source_layer == "venue"
    assert loaded.classes["workhorse"].source_layer == "default"
    assert len(loaded.layers) == 3
    assert all(layer.sha256 is not None for layer in loaded.layers)


def test_broken_override_keeps_lower_layer_and_reports_failure(tmp_path: Path) -> None:
    user = tmp_path / "classes.yaml"
    user.write_text("version: 1\nclasses:\n  strong: []\n")
    loaded = load_class_map(user_path=user, venue_path=tmp_path / "absent.yaml")
    assert loaded.classes["strong"].source_layer == "default"
    assert len(loaded.failures) == 1


@pytest.mark.parametrize(
    "source",
    [
        "version: 1\nclasses:\n  ? [strong]\n  : [claude-code]\n",   # sequence key
        "version: 1\nclasses:\n  ? {a: b}\n  : [claude-code]\n",     # mapping key
    ],
)
def test_unhashable_yaml_key_is_a_declined_layer_not_a_crash(
    tmp_path: Path, source: str,
) -> None:
    """Forge Inspect P1: a sequence/mapping key used to escape as TypeError
    and could abort manager start; it must be a refused layer."""
    user = tmp_path / "classes.yaml"
    user.write_text(source)
    loaded = load_class_map(user_path=user, venue_path=tmp_path / "absent.yaml")
    assert loaded.classes["strong"].source_layer == "default"
    assert len(loaded.failures) == 1
    assert "unhashable key" in loaded.failures[0].reason


def test_user_class_colliding_with_loaded_profile_refuses_that_layer(tmp_path: Path) -> None:
    user = tmp_path / "classes.yaml"
    user.write_text("version: 1\nclasses:\n  strong: [codex-cli]\n")
    loaded = load_class_map(
        user_path=user, venue_path=tmp_path / "absent.yaml",
        profile_names={"strong", "claude-code", "codex-cli"},
    )
    assert loaded.classes["strong"].source_layer == "default"
    assert len(loaded.failures) == 1
    assert "collides with a registered profile" in loaded.failures[0].reason
