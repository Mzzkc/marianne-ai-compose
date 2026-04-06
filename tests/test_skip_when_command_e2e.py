"""End-to-end tests for skip_when_command config lifecycle.

Validates that skip_when_command flows correctly through the full config
lifecycle: YAML loading, Pydantic model validation, serialization roundtrip,
and coexistence with expression-based skip_when.

GH#71
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from marianne.core.config import JobConfig


def _minimal_config(
    *,
    workspace: str = "./ws",
    sheet_overrides: dict | None = None,
) -> dict:
    """Return a minimal valid config dict for JobConfig.

    The caller can merge extra fields into the ``sheet`` key via
    *sheet_overrides*.
    """
    sheet: dict = {"size": 1, "total_items": 5}
    if sheet_overrides:
        sheet.update(sheet_overrides)

    return {
        "name": "e2e-skip-cmd",
        "workspace": workspace,
        "sheet": sheet,
        "prompt": {"template": "Do sheet {{ sheet_num }}"},
    }


class TestConfigRoundtripWithSkipWhenCommand:
    """Write config to YAML, load with from_yaml(), serialize, deserialize."""

    def test_config_roundtrip_with_skip_when_command(self, tmp_path: Path) -> None:
        """Full roundtrip: dict -> YAML file -> from_yaml -> dump -> validate."""
        ws = tmp_path / "workspace"
        ws.mkdir()

        cfg_dict = _minimal_config(
            workspace=str(ws),
            sheet_overrides={
                "skip_when_command": {
                    3: {
                        "command": 'grep -q "DONE" {workspace}/status.txt',
                        "description": "Skip if already done",
                        "timeout_seconds": 5.0,
                    },
                    5: {
                        "command": "test -f {workspace}/skip-marker",
                    },
                },
            },
        )

        # Step 1: Write to YAML file
        yaml_path = tmp_path / "job.yaml"
        yaml_path.write_text(yaml.dump(cfg_dict, default_flow_style=False))

        # Step 2: Load with from_yaml
        loaded = JobConfig.from_yaml(yaml_path)

        # Verify sheet 3 skip_when_command
        assert 3 in loaded.sheet.skip_when_command
        swc3 = loaded.sheet.skip_when_command[3]
        assert swc3.command == 'grep -q "DONE" {workspace}/status.txt'
        assert swc3.description == "Skip if already done"
        assert swc3.timeout_seconds == 5.0

        # Verify sheet 5 skip_when_command (defaults for optional fields)
        assert 5 in loaded.sheet.skip_when_command
        swc5 = loaded.sheet.skip_when_command[5]
        assert swc5.command == "test -f {workspace}/skip-marker"
        assert swc5.description is None
        assert swc5.timeout_seconds == 10.0  # default

        # Step 3: Roundtrip through model_dump -> model_validate
        dumped = loaded.model_dump(mode="json")
        restored = JobConfig.model_validate(dumped)

        # Verify fields survive the roundtrip
        assert 3 in restored.sheet.skip_when_command
        assert 5 in restored.sheet.skip_when_command
        assert restored.sheet.skip_when_command[3].command == swc3.command
        assert restored.sheet.skip_when_command[3].description == swc3.description
        assert restored.sheet.skip_when_command[3].timeout_seconds == swc3.timeout_seconds
        assert restored.sheet.skip_when_command[5].command == swc5.command
        assert restored.sheet.skip_when_command[5].description is None


class TestValidateCommandAcceptsSkipWhenCommand:
    """model_validate() accepts configs with skip_when_command."""

    def test_validate_command_validates_skip_when_command(self) -> None:
        """JobConfig.model_validate accepts skip_when_command and fields are accessible."""
        cfg_dict = _minimal_config(
            sheet_overrides={
                "skip_when_command": {
                    2: {
                        "command": "echo check",
                        "description": "Run a check",
                        "timeout_seconds": 8.0,
                    },
                },
            },
        )

        config = JobConfig.model_validate(cfg_dict)

        assert 2 in config.sheet.skip_when_command
        swc = config.sheet.skip_when_command[2]
        assert swc.command == "echo check"
        assert swc.description == "Run a check"
        assert swc.timeout_seconds == 8.0

    def test_validate_empty_skip_when_command(self) -> None:
        """An empty skip_when_command dict is valid (no-op)."""
        cfg_dict = _minimal_config(
            sheet_overrides={"skip_when_command": {}},
        )

        config = JobConfig.model_validate(cfg_dict)

        assert config.sheet.skip_when_command == {}

    def test_validate_skip_when_command_default(self) -> None:
        """Omitting skip_when_command defaults to empty dict."""
        cfg_dict = _minimal_config()

        config = JobConfig.model_validate(cfg_dict)

        assert config.sheet.skip_when_command == {}


class TestSkipWhenCommandWithBothSkipTypes:
    """A config can have both skip_when (expression) and skip_when_command on different sheets."""

    def test_skip_when_command_with_both_skip_types(self, tmp_path: Path) -> None:
        """Expression-based skip_when and command-based skip_when_command coexist."""
        ws = tmp_path / "workspace"
        ws.mkdir()

        cfg_dict = _minimal_config(
            workspace=str(ws),
            sheet_overrides={
                "skip_when": {
                    2: "sheets.get(1) and sheets[1].validation_passed",
                },
                "skip_when_command": {
                    4: {
                        "command": "test -f {workspace}/phase1-done.txt",
                        "description": "Skip phase 2 if phase 1 succeeded",
                    },
                },
            },
        )

        config = JobConfig.model_validate(cfg_dict)

        # Expression-based skip on sheet 2
        assert 2 in config.sheet.skip_when
        assert "validation_passed" in config.sheet.skip_when[2]

        # Command-based skip on sheet 4
        assert 4 in config.sheet.skip_when_command
        assert config.sheet.skip_when_command[4].command == "test -f {workspace}/phase1-done.txt"
        assert config.sheet.skip_when_command[4].description == "Skip phase 2 if phase 1 succeeded"

        # No overlap: sheet 2 only has expression, sheet 4 only has command
        assert 2 not in config.sheet.skip_when_command
        assert 4 not in config.sheet.skip_when

    def test_both_types_on_same_sheet_via_model_validate(self) -> None:
        """Both skip_when and skip_when_command can target the same sheet."""
        cfg_dict = _minimal_config(
            sheet_overrides={
                "skip_when": {
                    3: "True",
                },
                "skip_when_command": {
                    3: {
                        "command": "false",
                        "description": "Command check on sheet 3",
                    },
                },
            },
        )

        config = JobConfig.model_validate(cfg_dict)

        # Both types present on sheet 3
        assert 3 in config.sheet.skip_when
        assert 3 in config.sheet.skip_when_command
        assert config.sheet.skip_when[3] == "True"
        assert config.sheet.skip_when_command[3].command == "false"

    def test_both_types_survive_yaml_roundtrip(self, tmp_path: Path) -> None:
        """Both skip types survive a YAML write/read roundtrip."""
        ws = tmp_path / "workspace"
        ws.mkdir()

        cfg_dict = _minimal_config(
            workspace=str(ws),
            sheet_overrides={
                "skip_when": {
                    2: "sheets.get(1) and sheets[1].status == 'completed'",
                },
                "skip_when_command": {
                    3: {
                        "command": "test -d {workspace}/output",
                        "description": "Output dir exists",
                    },
                },
            },
        )

        yaml_path = tmp_path / "job.yaml"
        yaml_path.write_text(yaml.dump(cfg_dict, default_flow_style=False))

        loaded = JobConfig.from_yaml(yaml_path)

        assert 2 in loaded.sheet.skip_when
        assert 3 in loaded.sheet.skip_when_command
        assert loaded.sheet.skip_when_command[3].description == "Output dir exists"
