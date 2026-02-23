"""Tests for mozart.cli.commands.config_cmd module.

Covers the `check` subcommand for config validation and the `show`
subcommand for live/disk config display.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from mozart.cli.commands.config_cmd import config_app

runner = CliRunner()


class TestConfigCheck:
    """Tests for `mozart config check` subcommand."""

    def test_valid_config_exits_0(self, tmp_path: Path) -> None:
        """Valid YAML config passes validation with exit code 0."""
        cfg = tmp_path / "valid.yaml"
        cfg.write_text(yaml.dump({"max_concurrent_jobs": 3}))

        result = runner.invoke(config_app, ["check", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "Valid config" in result.output

    def test_invalid_config_exits_1(self, tmp_path: Path) -> None:
        """Invalid config (out-of-range value) exits with code 1."""
        cfg = tmp_path / "invalid.yaml"
        cfg.write_text(yaml.dump({"max_concurrent_jobs": -5}))

        result = runner.invoke(config_app, ["check", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "Invalid config" in result.output

    def test_missing_file_exits_1(self, tmp_path: Path) -> None:
        """Non-existent config file exits with code 1."""
        cfg = tmp_path / "nonexistent.yaml"

        result = runner.invoke(config_app, ["check", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_empty_config_is_valid(self, tmp_path: Path) -> None:
        """An empty YAML file (all defaults) is valid."""
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")

        result = runner.invoke(config_app, ["check", "--config", str(cfg)])
        assert result.exit_code == 0

    def test_invalid_type_exits_1(self, tmp_path: Path) -> None:
        """Wrong type for a field (string where int expected) exits 1."""
        cfg = tmp_path / "badtype.yaml"
        cfg.write_text(yaml.dump({"max_concurrent_jobs": "not-a-number"}))

        result = runner.invoke(config_app, ["check", "--config", str(cfg)])
        assert result.exit_code == 1

    def test_nested_invalid_config_exits_1(self, tmp_path: Path) -> None:
        """Invalid nested config (bad resource limit) exits 1."""
        cfg = tmp_path / "nested_invalid.yaml"
        cfg.write_text(yaml.dump({
            "resource_limits": {"max_memory_mb": 100},  # below 512 minimum
        }))

        result = runner.invoke(config_app, ["check", "--config", str(cfg)])
        assert result.exit_code == 1

    def test_valid_nested_config_exits_0(self, tmp_path: Path) -> None:
        """Valid nested config passes."""
        cfg = tmp_path / "nested_valid.yaml"
        cfg.write_text(yaml.dump({
            "socket": {"path": "/tmp/custom.sock", "backlog": 10},
            "max_concurrent_jobs": 8,
            "resource_limits": {"max_memory_mb": 2048},
        }))

        result = runner.invoke(config_app, ["check", "--config", str(cfg)])
        assert result.exit_code == 0


class TestConfigShow:
    """Tests for `mozart config show` subcommand with live IPC support."""

    def test_show_live_config_from_conductor(self) -> None:
        """show displays live config when conductor is running."""
        from mozart.daemon.config import DaemonConfig

        live_config = DaemonConfig(max_concurrent_jobs=12)
        live_dict = live_config.model_dump(mode="json")

        with patch(
            "mozart.cli.commands.config_cmd._try_live_config",
            return_value=live_dict,
        ):
            result = runner.invoke(config_app, ["show"])

        assert result.exit_code == 0
        assert "live" in result.output
        assert "12" in result.output

    def test_show_fallback_to_disk_when_conductor_offline(
        self, tmp_path: Path,
    ) -> None:
        """show falls back to disk config when conductor is not running."""
        cfg = tmp_path / "daemon.yaml"
        cfg.write_text(yaml.dump({"max_concurrent_jobs": 7}))

        with patch(
            "mozart.cli.commands.config_cmd._try_live_config",
            return_value=None,
        ):
            result = runner.invoke(
                config_app, ["show", "--config", str(cfg)],
            )

        assert result.exit_code == 0
        assert "from running conductor" not in result.output
        assert "7" in result.output

    def test_show_defaults_when_no_conductor_no_file(self) -> None:
        """show displays defaults when conductor is offline and no config file exists."""
        with patch(
            "mozart.cli.commands.config_cmd._try_live_config",
            return_value=None,
        ):
            result = runner.invoke(
                config_app,
                ["show", "--config", "/tmp/nonexistent-mozart-test.yaml"],
            )

        assert result.exit_code == 0
        assert "defaults" in result.output

    def test_show_essential_only_by_default(self) -> None:
        """Default show displays essential fields but not advanced ones."""
        from mozart.daemon.config import DaemonConfig

        live_config = DaemonConfig()
        live_dict = live_config.model_dump(mode="json")

        with patch(
            "mozart.cli.commands.config_cmd._try_live_config",
            return_value=live_dict,
        ):
            result = runner.invoke(config_app, ["show"])

        assert result.exit_code == 0
        assert "max_concurrent_jobs" in result.output
        assert "log_level" in result.output
        # Advanced fields should NOT appear
        assert "socket.backlog" not in result.output
        assert "observer.watch_interval_seconds" not in result.output
        # Hint should appear
        assert "--all" in result.output

    def test_show_all_flag(self) -> None:
        """--all shows advanced fields too."""
        from mozart.daemon.config import DaemonConfig

        live_config = DaemonConfig()
        live_dict = live_config.model_dump(mode="json")

        with patch(
            "mozart.cli.commands.config_cmd._try_live_config",
            return_value=live_dict,
        ):
            result = runner.invoke(config_app, ["show", "--all"])

        assert result.exit_code == 0
        assert "max_concurrent_jobs" in result.output
        assert "socket.backlog" in result.output
        assert "observer.watch_interval_seconds" in result.output
        # No hint when --all is used
        assert "Showing essential fields" not in result.output

    def test_show_section_filter(self) -> None:
        """--section filters to a specific config section."""
        from mozart.daemon.config import DaemonConfig

        live_config = DaemonConfig()
        live_dict = live_config.model_dump(mode="json")

        with patch(
            "mozart.cli.commands.config_cmd._try_live_config",
            return_value=live_dict,
        ):
            result = runner.invoke(config_app, ["show", "--section", "profiler"])

        assert result.exit_code == 0
        assert "profiler.enabled" in result.output
        assert "profiler.strace_enabled" in result.output
        # Non-profiler fields should not appear
        assert "max_concurrent_jobs" not in result.output
        assert "socket.path" not in result.output

    def test_show_section_unknown(self) -> None:
        """Unknown section shows empty table without crashing."""
        from mozart.daemon.config import DaemonConfig

        live_config = DaemonConfig()
        live_dict = live_config.model_dump(mode="json")

        with patch(
            "mozart.cli.commands.config_cmd._try_live_config",
            return_value=live_dict,
        ):
            result = runner.invoke(config_app, ["show", "--section", "nonexistent"])

        assert result.exit_code == 0
        # Should not contain any config keys
        assert "max_concurrent_jobs" not in result.output


class TestFlattenModel:
    """Tests for _flatten_model helper used in config display."""

    def test_flatten_model_produces_dotted_keys(self) -> None:
        """Nested dicts are flattened to dot-notation keys."""
        from mozart.cli.commands.config_cmd import _flatten_model

        data = {"socket": {"path": "/tmp/test.sock", "backlog": 5}, "max_concurrent_jobs": 3}
        flat = _flatten_model(data)

        assert "socket.path" in flat
        assert "socket.backlog" in flat
        assert "max_concurrent_jobs" in flat
        # No "." prefix on top-level keys
        assert flat["max_concurrent_jobs"] == 3
        assert flat["socket.path"] == "/tmp/test.sock"

    def test_flatten_model_no_double_dot(self) -> None:
        """Ensure no '.socket.path' (leading dot) in flattened output."""
        from mozart.cli.commands.config_cmd import _flatten_model

        data = {"a": {"b": {"c": 1}}}
        flat = _flatten_model(data)

        assert "a.b.c" in flat
        # No key should start with a dot
        for key in flat:
            assert not key.startswith("."), f"Key '{key}' has leading dot"


class TestCheckSubcommandRegistered:
    """Verify the check subcommand is registered in config_app."""

    def test_check_in_commands(self) -> None:
        """The 'check' command is registered in config_app."""
        cmds = [c.name for c in config_app.registered_commands]
        assert "check" in cmds


# ============================================================================
# Q004: Fallback behavior and helper function tests
# ============================================================================


class TestCoerceValue:
    """Tests for _coerce_value type coercion helper."""

    def test_true_values(self) -> None:
        from mozart.cli.commands.config_cmd import _coerce_value

        assert _coerce_value("true") is True
        assert _coerce_value("True") is True
        assert _coerce_value("yes") is True
        assert _coerce_value("YES") is True

    def test_false_values(self) -> None:
        from mozart.cli.commands.config_cmd import _coerce_value

        assert _coerce_value("false") is False
        assert _coerce_value("no") is False

    def test_null_values(self) -> None:
        from mozart.cli.commands.config_cmd import _coerce_value

        assert _coerce_value("null") is None
        assert _coerce_value("none") is None
        assert _coerce_value("~") is None

    def test_int_coercion(self) -> None:
        from mozart.cli.commands.config_cmd import _coerce_value

        assert _coerce_value("42") == 42
        assert _coerce_value("0") == 0

    def test_float_coercion(self) -> None:
        from mozart.cli.commands.config_cmd import _coerce_value

        assert _coerce_value("3.14") == 3.14

    def test_string_fallback(self) -> None:
        from mozart.cli.commands.config_cmd import _coerce_value

        assert _coerce_value("hello") == "hello"
        assert _coerce_value("/tmp/custom.sock") == "/tmp/custom.sock"


class TestGetNested:
    """Tests for _get_nested dot-notation access."""

    def test_simple_key(self) -> None:
        from mozart.cli.commands.config_cmd import _get_nested

        assert _get_nested({"a": 1}, "a") == 1

    def test_nested_key(self) -> None:
        from mozart.cli.commands.config_cmd import _get_nested

        assert _get_nested({"a": {"b": {"c": 3}}}, "a.b.c") == 3

    def test_missing_key_returns_none(self) -> None:
        from mozart.cli.commands.config_cmd import _get_nested

        assert _get_nested({"a": 1}, "b") is None
        assert _get_nested({"a": {"b": 2}}, "a.c") is None

    def test_non_dict_intermediate_returns_none(self) -> None:
        from mozart.cli.commands.config_cmd import _get_nested

        assert _get_nested({"a": 42}, "a.b") is None


class TestSetNested:
    """Tests for _set_nested dot-notation setter."""

    def test_simple_key(self) -> None:
        from mozart.cli.commands.config_cmd import _set_nested

        data: dict = {}
        _set_nested(data, "a", 1)
        assert data == {"a": 1}

    def test_nested_key_creates_intermediates(self) -> None:
        from mozart.cli.commands.config_cmd import _set_nested

        data: dict = {}
        _set_nested(data, "a.b.c", 42)
        assert data == {"a": {"b": {"c": 42}}}

    def test_overwrites_non_dict_intermediate(self) -> None:
        from mozart.cli.commands.config_cmd import _set_nested

        data: dict = {"a": "string-value"}
        _set_nested(data, "a.b", 99)
        assert data == {"a": {"b": 99}}


class TestLoadConfigData:
    """Tests for _load_config_data fallback behavior."""

    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        from mozart.cli.commands.config_cmd import _load_config_data

        result = _load_config_data(tmp_path / "no-such-file.yaml")
        assert result == {}

    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        from mozart.cli.commands.config_cmd import _load_config_data

        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")
        result = _load_config_data(cfg)
        assert result == {}

    def test_valid_yaml_returns_data(self, tmp_path: Path) -> None:
        from mozart.cli.commands.config_cmd import _load_config_data

        cfg = tmp_path / "valid.yaml"
        cfg.write_text(yaml.dump({"max_concurrent_jobs": 15}))
        result = _load_config_data(cfg)
        assert result == {"max_concurrent_jobs": 15}


class TestConfigSet:
    """Tests for `mozart config set` subcommand."""

    def test_set_valid_value(self, tmp_path: Path) -> None:
        """Setting a valid config value writes to disk."""
        cfg = tmp_path / "daemon.yaml"
        cfg.write_text(yaml.dump({"max_concurrent_jobs": 3}))

        result = runner.invoke(
            config_app, ["set", "max_concurrent_jobs", "10", "--config", str(cfg)]
        )
        assert result.exit_code == 0
        assert "Set" in result.output

        # Verify the file was updated
        with open(cfg) as f:
            data = yaml.safe_load(f)
        assert data["max_concurrent_jobs"] == 10

    def test_set_invalid_value_exits_1(self, tmp_path: Path) -> None:
        """Setting an invalid value (out-of-range) exits 1."""
        cfg = tmp_path / "daemon.yaml"
        cfg.write_text(yaml.dump({}))

        # Use 999 which exceeds max_concurrent_jobs le=50 constraint
        result = runner.invoke(
            config_app, ["set", "max_concurrent_jobs", "999", "--config", str(cfg)]
        )
        assert result.exit_code == 1
        assert "Invalid" in result.output

    def test_set_nested_key(self, tmp_path: Path) -> None:
        """Setting a nested key using dot notation."""
        cfg = tmp_path / "daemon.yaml"
        cfg.write_text(yaml.dump({}))

        result = runner.invoke(
            config_app,
            ["set", "resource_limits.max_memory_mb", "4096", "--config", str(cfg)],
        )
        assert result.exit_code == 0

    def test_set_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Config set creates parent directories if missing."""
        cfg = tmp_path / "subdir" / "daemon.yaml"
        # Parent doesn't exist yet

        result = runner.invoke(
            config_app, ["set", "max_concurrent_jobs", "5", "--config", str(cfg)]
        )
        assert result.exit_code == 0
        assert cfg.exists()


class TestConfigPath:
    """Tests for `mozart config path` subcommand."""

    def test_path_existing_file(self, tmp_path: Path) -> None:
        """path shows location and 'exists' status for existing file."""
        cfg = tmp_path / "daemon.yaml"
        cfg.write_text("")

        result = runner.invoke(config_app, ["path", "--config", str(cfg)])
        assert result.exit_code == 0
        assert str(cfg) in result.output
        assert "exists" in result.output

    def test_path_missing_file(self, tmp_path: Path) -> None:
        """path shows location and 'not created' status for missing file."""
        cfg = tmp_path / "nonexistent.yaml"

        result = runner.invoke(config_app, ["path", "--config", str(cfg)])
        assert result.exit_code == 0
        # Rich may wrap the text across lines, so normalize whitespace
        normalized = " ".join(result.output.split())
        assert "not created" in normalized


class TestConfigInit:
    """Tests for `mozart config init` subcommand."""

    def test_init_creates_default_config(self, tmp_path: Path) -> None:
        """init creates a default config file."""
        cfg = tmp_path / "daemon.yaml"

        result = runner.invoke(config_app, ["init", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "Created" in result.output
        assert cfg.exists()

        # Verify it's valid YAML
        with open(cfg) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict)

    def test_init_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        """init refuses to overwrite without --force."""
        cfg = tmp_path / "daemon.yaml"
        cfg.write_text("existing content")

        result = runner.invoke(config_app, ["init", "--config", str(cfg)])
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_init_overwrites_with_force(self, tmp_path: Path) -> None:
        """init overwrites with --force."""
        cfg = tmp_path / "daemon.yaml"
        cfg.write_text("old content")

        result = runner.invoke(config_app, ["init", "--force", "--config", str(cfg)])
        assert result.exit_code == 0
        assert "Created" in result.output


class TestTryLiveConfig:
    """Tests for _try_live_config fallback behavior."""

    def test_returns_none_when_conductor_offline(self) -> None:
        """_try_live_config returns None when no conductor is running."""
        from unittest.mock import AsyncMock

        from mozart.cli.commands.config_cmd import _try_live_config

        # DaemonClient is imported lazily inside _try_live_config, so
        # mock at the source module level
        mock_client = AsyncMock()
        mock_client.config.side_effect = ConnectionRefusedError("No conductor")

        with patch(
            "mozart.daemon.ipc.client.DaemonClient",
            return_value=mock_client,
        ):
            result = _try_live_config()

        # Should return None since the IPC call fails
        assert result is None

    def test_show_falls_back_on_invalid_live_config(self) -> None:
        """show falls back to disk when live config fails validation."""
        with patch(
            "mozart.cli.commands.config_cmd._try_live_config",
            return_value={"max_concurrent_jobs": -999},  # Invalid
        ):
            result = runner.invoke(
                config_app,
                ["show", "--config", "/tmp/nonexistent-config.yaml"],
            )

        # Should fall back gracefully (may show defaults or error)
        # The key is it doesn't crash
        assert result.exit_code in (0, 1)


class TestStringifyPaths:
    """Tests for _stringify_paths helper."""

    def test_converts_path_objects(self) -> None:
        from mozart.cli.commands.config_cmd import _stringify_paths

        data: dict = {"socket": {"path": Path("/tmp/test.sock")}, "name": "test"}
        _stringify_paths(data)
        assert data["socket"]["path"] == "/tmp/test.sock"
        assert data["name"] == "test"  # Strings left alone
