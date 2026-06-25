"""Tests for ``mzt compile`` command.

Validates the compile command module in ``marianne.cli.commands.compile``:
command registration, --help output, --dry-run mode, error handling for
missing compiler package, and delegation to the compiler pipeline.

TDD: These tests define the contract for the compile command.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from marianne.cli import app

# The compile command delegates to the optional, separately-packaged
# ``marianne_compiler`` (bundled as a git submodule). Every test here invokes
# ``mzt compile``, whose function body imports marianne_compiler eagerly, so
# skip the whole module when it is not installed — e.g. CI on a bare checkout
# without submodule access. Runs normally wherever the compiler is present.
pytest.importorskip("marianne_compiler")

runner = CliRunner()


# ---------------------------------------------------------------------------
# Command registration and help
# ---------------------------------------------------------------------------


class TestCompileCommandRegistration:
    """Verify the compile command is registered and shows help."""

    def test_compile_command_registered(self) -> None:
        """The compile command appears in the CLI."""
        result = runner.invoke(app, ["compile", "--help"])
        assert result.exit_code == 0

    def test_compile_help_shows_config_argument(self) -> None:
        """Help text mentions the config argument."""
        result = runner.invoke(app, ["compile", "--help"])
        assert result.exit_code == 0
        assert "config" in result.stdout.lower()

    def test_compile_help_shows_output_option(self) -> None:
        """Help text mentions --output option."""
        result = runner.invoke(app, ["compile", "--help"])
        assert "output" in result.stdout.lower()

    def test_compile_help_shows_dry_run_option(self) -> None:
        """Help text mentions --dry-run option."""
        result = runner.invoke(app, ["compile", "--help"])
        assert "dry-run" in result.stdout.lower()

    def test_compile_help_shows_fleet_option(self) -> None:
        """Help text mentions --fleet option."""
        result = runner.invoke(app, ["compile", "--help"])
        assert "fleet" in result.stdout.lower()

    def test_compile_help_shows_seed_only_option(self) -> None:
        """Help text mentions --seed-only option."""
        result = runner.invoke(app, ["compile", "--help"])
        assert "seed-only" in result.stdout.lower()

    def test_compile_help_shows_pause_before_chain_option(self) -> None:
        """Help text mentions --pause-before-chain option."""
        result = runner.invoke(app, ["compile", "--help"])
        assert "pause-before-chain" in result.stdout.lower()

    def test_compile_help_shows_job_prefix_option(self) -> None:
        """Help text mentions --job-prefix option."""
        result = runner.invoke(app, ["compile", "--help"])
        assert "job-prefix" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Import structure
# ---------------------------------------------------------------------------


class TestCompileCommandImport:
    """Verify the compile command module is importable."""

    def test_compile_importable_from_commands(self) -> None:
        """The compile function is importable from marianne.cli.commands."""
        from marianne.cli.commands.compile import compile_scores

        assert callable(compile_scores)

    def test_compile_in_commands_all(self) -> None:
        """The compile function is listed in commands __all__."""
        from marianne.cli.commands import __all__

        assert "compile_scores" in __all__


# ---------------------------------------------------------------------------
# Error handling — missing config
# ---------------------------------------------------------------------------


class TestCompileErrorHandling:
    """Verify graceful error handling for common failures."""

    def test_missing_config_file(self, tmp_path: Path) -> None:
        """Non-existent config file produces an error."""
        result = runner.invoke(app, ["compile", str(tmp_path / "nope.yaml")])
        assert result.exit_code != 0

    def test_empty_config_no_agents(self, tmp_path: Path) -> None:
        """Config without agents produces an error."""
        config = tmp_path / "empty.yaml"
        config.write_text(yaml.dump({"project": {"name": "test"}}))
        result = runner.invoke(app, ["compile", str(config)])
        assert result.exit_code != 0
        assert "agent" in result.stdout.lower() or "agent" in (result.stderr or "").lower()

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        """Invalid YAML produces an error."""
        config = tmp_path / "bad.yaml"
        config.write_text("{{invalid: yaml: [")
        result = runner.invoke(app, ["compile", str(config)])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Dry run mode
# ---------------------------------------------------------------------------


class TestCompileDryRun:
    """Verify --dry-run shows summary without writing files."""

    def test_dry_run_shows_agent_names(self, tmp_path: Path) -> None:
        """Dry run lists agent names."""
        config = tmp_path / "agents.yaml"
        config.write_text(
            yaml.dump(
                {
                    "project": {"name": "test-project"},
                    "agents": [
                        {"name": "canyon", "focus": "architecture"},
                        {"name": "forge", "focus": "implementation"},
                    ],
                }
            )
        )
        result = runner.invoke(app, ["compile", str(config), "--dry-run"])
        assert result.exit_code == 0
        assert "canyon" in result.stdout
        assert "forge" in result.stdout

    def test_dry_run_shows_project_name(self, tmp_path: Path) -> None:
        """Dry run shows the project name."""
        config = tmp_path / "agents.yaml"
        config.write_text(
            yaml.dump(
                {
                    "project": {"name": "my-project"},
                    "agents": [{"name": "test-agent"}],
                }
            )
        )
        result = runner.invoke(app, ["compile", str(config), "--dry-run"])
        assert result.exit_code == 0
        assert "my-project" in result.stdout

    def test_dry_run_no_files_written(self, tmp_path: Path) -> None:
        """Dry run does not create any output files."""
        config = tmp_path / "agents.yaml"
        config.write_text(
            yaml.dump(
                {
                    "agents": [{"name": "test-agent"}],
                }
            )
        )
        output_dir = tmp_path / "output"
        result = runner.invoke(
            app,
            ["compile", str(config), "--dry-run", "--output", str(output_dir)],
        )
        assert result.exit_code == 0
        assert not output_dir.exists()


# ---------------------------------------------------------------------------
# Full compilation delegation
# ---------------------------------------------------------------------------


class TestCompileDelegation:
    """Verify that compile delegates to the compiler pipeline."""

    def test_compile_calls_pipeline(self, tmp_path: Path) -> None:
        """Full compilation delegates to CompilationPipeline.compile_config."""
        config = tmp_path / "agents.yaml"
        config.write_text(
            yaml.dump(
                {
                    "project": {"name": "test"},
                    "agents": [{"name": "agent1", "focus": "testing"}],
                }
            )
        )
        output_dir = tmp_path / "scores"

        mock_pipeline = MagicMock()
        mock_pipeline.compile_config.return_value = [output_dir / "agent1.yaml"]

        with patch(
            "marianne_compiler.pipeline.CompilationPipeline",
            return_value=mock_pipeline,
        ):
            result = runner.invoke(
                app,
                ["compile", str(config), "--output", str(output_dir)],
            )

        assert result.exit_code == 0
        mock_pipeline.compile_config.assert_called_once()

    def test_generic_fleet_preset_filters_to_doctor_available_instruments(
        self,
        tmp_path: Path,
    ) -> None:
        """Generic preset compilation uses doctor-available instruments."""
        output_dir = tmp_path / "scores"

        mock_pipeline = MagicMock()
        mock_pipeline.compile_config.return_value = [output_dir / "north.yaml"]

        with (
            patch(
                "marianne.cli.commands.compile._doctor_available_instruments",
                return_value={"cli", "claude-code"},
            ),
            patch(
                "marianne_compiler.pipeline.CompilationPipeline",
                return_value=mock_pipeline,
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "compile",
                    "--preset",
                    "generic-fleet",
                    "--output",
                    str(output_dir),
                ],
            )

        assert result.exit_code == 0
        config_data = mock_pipeline.compile_config.call_args.args[0]
        for tier_config in config_data["defaults"]["instruments"].values():
            entries = [tier_config["primary"], *tier_config.get("fallbacks", [])]
            assert entries
            for entry in entries:
                assert entry["instrument"] in {"cli", "claude-code"}

    def test_doctor_available_instruments_excludes_runtime_rate_limited(
        self,
    ) -> None:
        """Generic fleet compilation treats live rate limits as unavailable."""
        from marianne.cli.commands.compile import _doctor_available_instruments
        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
        )

        def profile(name: str, executable: str) -> InstrumentProfile:
            return InstrumentProfile(
                name=name,
                display_name=name,
                kind="cli",
                cli=CliProfile(
                    command=CliCommand(executable=executable),
                    output=CliOutputConfig(format="text"),
                ),
            )

        with (
            patch(
                "marianne.cli.commands.doctor._active_rate_limited_instruments",
                return_value={"antigravity": 3600.0},
            ),
            patch(
                "marianne.instruments.loader.load_all_profiles",
                return_value={
                    "antigravity": profile("antigravity", "agy"),
                    "claude-code": profile("claude-code", "claude"),
                },
            ),
            patch(
                "marianne.cli.commands.doctor._check_instrument_binary",
                return_value=(True, "/usr/bin/fake"),
            ),
        ):
            available = _doctor_available_instruments()

        assert "claude-code" in available
        assert "antigravity" not in available

    def test_doctor_available_instruments_excludes_execution_unsupported(
        self,
    ) -> None:
        """Generic fleet compilation skips binary-ready unsupported profiles."""
        from marianne.cli.commands.compile import _doctor_available_instruments
        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
            InstrumentProfile,
        )

        supported = InstrumentProfile(
            name="claude-code",
            display_name="Claude Code",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="claude"),
                output=CliOutputConfig(format="text"),
            ),
        )
        unsupported = InstrumentProfile(
            name="gemini-cli",
            display_name="Gemini CLI",
            kind="cli",
            execution_status="unsupported",
            execution_status_detail="UNSUPPORTED_CLIENT",
            cli=CliProfile(
                command=CliCommand(executable="gemini"),
                output=CliOutputConfig(format="text"),
            ),
        )

        with (
            patch(
                "marianne.cli.commands.doctor._active_rate_limited_instruments",
                return_value={},
            ),
            patch(
                "marianne.instruments.loader.load_all_profiles",
                return_value={
                    "claude-code": supported,
                    "gemini-cli": unsupported,
                },
            ),
            patch(
                "marianne.cli.commands.doctor._check_instrument_binary",
                return_value=(True, "/usr/bin/fake"),
            ),
        ):
            available = _doctor_available_instruments()

        assert "claude-code" in available
        assert "gemini-cli" not in available

    def test_pause_before_chain_sets_compiler_default(
        self,
        tmp_path: Path,
    ) -> None:
        """--pause-before-chain is lowered into compiler config defaults."""
        output_dir = tmp_path / "scores"

        mock_pipeline = MagicMock()
        mock_pipeline.compile_config.return_value = [output_dir / "north.yaml"]

        with (
            patch(
                "marianne.cli.commands.compile._doctor_available_instruments",
                return_value={"cli", "gemini-cli", "claude-code"},
            ),
            patch(
                "marianne_compiler.pipeline.CompilationPipeline",
                return_value=mock_pipeline,
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "compile",
                    "--preset",
                    "generic-fleet",
                    "--output",
                    str(output_dir),
                    "--pause-before-chain",
                ],
            )

        assert result.exit_code == 0
        config_data = mock_pipeline.compile_config.call_args.args[0]
        assert config_data["defaults"]["pause_before_chain"] is True

    def test_job_prefix_sets_compiler_default(
        self,
        tmp_path: Path,
    ) -> None:
        """--job-prefix is lowered into compiler config defaults."""
        output_dir = tmp_path / "scores"

        mock_pipeline = MagicMock()
        mock_pipeline.compile_config.return_value = [output_dir / "bc9k-north.yaml"]

        with (
            patch(
                "marianne.cli.commands.compile._doctor_available_instruments",
                return_value={"cli", "gemini-cli", "claude-code"},
            ),
            patch(
                "marianne_compiler.pipeline.CompilationPipeline",
                return_value=mock_pipeline,
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "compile",
                    "--preset",
                    "generic-fleet",
                    "--output",
                    str(output_dir),
                    "--job-prefix",
                    "bc9k-",
                ],
            )

        assert result.exit_code == 0
        config_data = mock_pipeline.compile_config.call_args.args[0]
        assert config_data["defaults"]["job_name_prefix"] == "bc9k-"

    def test_default_output_uses_project_workspace_scores_dir(
        self,
        tmp_path: Path,
    ) -> None:
        """Configured project.workspace controls the default score output dir."""
        workspace = tmp_path / "workspace"
        config = tmp_path / "agents.yaml"
        config.write_text(
            yaml.dump(
                {
                    "project": {"name": "test", "workspace": str(workspace)},
                    "agents": [{"name": "agent1", "focus": "testing"}],
                }
            )
        )

        mock_pipeline = MagicMock()
        mock_pipeline.compile_config.return_value = [
            workspace / "scores" / "agent1.yaml"
        ]

        with patch(
            "marianne_compiler.pipeline.CompilationPipeline",
            return_value=mock_pipeline,
        ):
            result = runner.invoke(app, ["compile", str(config)])

        assert result.exit_code == 0
        _, output_dir = mock_pipeline.compile_config.call_args.args
        assert output_dir == workspace / "scores"

    def test_default_output_uses_top_level_workspace_scores_dir(
        self,
        tmp_path: Path,
    ) -> None:
        """The legacy top-level workspace key is still honored."""
        workspace = tmp_path / "workspace"
        config = tmp_path / "agents.yaml"
        config.write_text(
            yaml.dump(
                {
                    "workspace": str(workspace),
                    "agents": [{"name": "agent1", "focus": "testing"}],
                }
            )
        )

        mock_pipeline = MagicMock()
        mock_pipeline.compile_config.return_value = [
            workspace / "scores" / "agent1.yaml"
        ]

        with patch(
            "marianne_compiler.pipeline.CompilationPipeline",
            return_value=mock_pipeline,
        ):
            result = runner.invoke(app, ["compile", str(config)])

        assert result.exit_code == 0
        _, output_dir = mock_pipeline.compile_config.call_args.args
        assert output_dir == workspace / "scores"

    def test_seed_only_calls_seed_identity(self, tmp_path: Path) -> None:
        """--seed-only delegates to pipeline.seed_identity per agent."""
        config = tmp_path / "agents.yaml"
        config.write_text(
            yaml.dump(
                {
                    "agents": [
                        {"name": "agent1"},
                        {"name": "agent2"},
                    ],
                }
            )
        )

        mock_pipeline = MagicMock()
        mock_pipeline.seed_identity.return_value = tmp_path / "agents" / "agent1"

        with patch(
            "marianne_compiler.pipeline.CompilationPipeline",
            return_value=mock_pipeline,
        ):
            result = runner.invoke(
                app,
                ["compile", str(config), "--seed-only"],
            )

        assert result.exit_code == 0
        assert mock_pipeline.seed_identity.call_count == 2

    def test_fleet_flag_forces_fleet_generation(self, tmp_path: Path) -> None:
        """--fleet generates fleet config even for a single agent."""
        config = tmp_path / "agents.yaml"
        config.write_text(
            yaml.dump(
                {
                    "agents": [{"name": "solo-agent"}],
                }
            )
        )
        output_dir = tmp_path / "scores"
        output_dir.mkdir()

        mock_pipeline = MagicMock()
        mock_pipeline.compile_config.return_value = [output_dir / "solo-agent.yaml"]

        mock_fleet = MagicMock()
        mock_fleet.write.return_value = output_dir / "fleet.yaml"

        with (
            patch(
                "marianne_compiler.pipeline.CompilationPipeline",
                return_value=mock_pipeline,
            ),
            patch(
                "marianne_compiler.fleet.FleetGenerator",
                return_value=mock_fleet,
            ),
        ):
            result = runner.invoke(
                app,
                ["compile", str(config), "--output", str(output_dir), "--fleet"],
            )

        assert result.exit_code == 0
        mock_fleet.write.assert_called_once()


# ---------------------------------------------------------------------------
# Compilation failure
# ---------------------------------------------------------------------------


class TestCompileFailure:
    """Verify error handling when compilation fails."""

    def test_compilation_error_shows_message(self, tmp_path: Path) -> None:
        """Pipeline errors are surfaced to the user."""
        config = tmp_path / "agents.yaml"
        config.write_text(
            yaml.dump(
                {
                    "agents": [{"name": "agent1"}],
                }
            )
        )

        mock_pipeline = MagicMock()
        mock_pipeline.compile_config.side_effect = RuntimeError("template not found")

        with patch(
            "marianne_compiler.pipeline.CompilationPipeline",
            return_value=mock_pipeline,
        ):
            result = runner.invoke(
                app,
                ["compile", str(config)],
            )

        assert result.exit_code != 0
        assert "template not found" in result.stdout.lower() or "failed" in result.stdout.lower()
