"""Tests for daemon operational profiles.

Covers profile loading, deep merging, resolution order, and error handling.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from marianne.daemon.config import DaemonConfig
from marianne.daemon.profiles import (
    BUILTIN_PROFILES,
    deep_merge,
    get_profile,
    list_profiles,
)


# ── Built-in profile discovery ────────────────────────────────────────


class TestListProfiles:
    """Test profile listing and discovery."""

    def test_list_profiles_returns_sorted(self) -> None:
        names = list_profiles()
        assert names == sorted(names)

    def test_builtin_profiles_exist(self) -> None:
        assert "dev" in BUILTIN_PROFILES
        assert "intensive" in BUILTIN_PROFILES
        assert "minimal" in BUILTIN_PROFILES

    def test_at_least_three_profiles(self) -> None:
        assert len(list_profiles()) >= 3


# ── Profile loading ───────────────────────────────────────────────────


class TestGetProfile:
    """Test loading individual profiles."""

    def test_load_dev_profile(self) -> None:
        data = get_profile("dev")
        assert data["log_level"] == "debug"
        assert data["max_concurrent_jobs"] == 2
        assert data["profiler"]["strace_enabled"] is True

    def test_load_intensive_profile(self) -> None:
        data = get_profile("intensive")
        assert data["job_timeout_seconds"] == 172800
        assert data["resource_limits"]["max_memory_mb"] == 16384

    def test_load_minimal_profile(self) -> None:
        data = get_profile("minimal")
        assert data["max_concurrent_jobs"] == 2
        assert data["profiler"]["enabled"] is False
        assert data["learning"]["enabled"] is False

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="Unknown daemon profile 'nonexistent'"):
            get_profile("nonexistent")

    def test_unknown_profile_lists_available(self) -> None:
        with pytest.raises(FileNotFoundError, match="Available profiles:"):
            get_profile("nope")

    def test_all_profiles_are_valid_daemon_config(self) -> None:
        """Every built-in profile must produce a valid DaemonConfig when merged."""
        for name in list_profiles():
            data = get_profile(name)
            config = DaemonConfig.model_validate(data)
            assert config is not None, f"Profile '{name}' failed validation"


# ── Deep merge ────────────────────────────────────────────────────────


class TestDeepMerge:
    """Test the deep_merge utility."""

    def test_flat_override(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        assert deep_merge(base, override) == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self) -> None:
        base = {"x": {"a": 1, "b": 2}, "y": 10}
        override = {"x": {"b": 3, "c": 4}}
        result = deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 3, "c": 4}, "y": 10}

    def test_deep_nested_merge(self) -> None:
        base = {"l1": {"l2": {"l3": "old", "keep": True}}}
        override = {"l1": {"l2": {"l3": "new"}}}
        result = deep_merge(base, override)
        assert result == {"l1": {"l2": {"l3": "new", "keep": True}}}

    def test_override_replaces_non_dict(self) -> None:
        base = {"a": [1, 2, 3]}
        override = {"a": [4, 5]}
        assert deep_merge(base, override) == {"a": [4, 5]}

    def test_empty_override(self) -> None:
        base = {"a": 1}
        assert deep_merge(base, {}) == {"a": 1}

    def test_empty_base(self) -> None:
        override = {"a": 1}
        assert deep_merge({}, override) == {"a": 1}

    def test_does_not_mutate_base(self) -> None:
        base = {"a": {"b": 1}}
        override = {"a": {"b": 2}}
        deep_merge(base, override)
        assert base == {"a": {"b": 1}}


# ── Profile resolution order ─────────────────────────────────────────


class TestProfileResolution:
    """Test that profiles merge correctly with config files."""

    def test_profile_overrides_defaults(self) -> None:
        """Profile values override DaemonConfig defaults."""
        data = get_profile("dev")
        config = DaemonConfig.model_validate(data)
        assert config.log_level == "debug"
        assert config.max_concurrent_jobs == 2

    def test_profile_preserves_unset_defaults(self) -> None:
        """Fields not in the profile keep their DaemonConfig defaults."""
        data = get_profile("minimal")
        config = DaemonConfig.model_validate(data)
        # minimal doesn't set log_level, so it should be the default
        assert config.log_level == "info"
        # minimal doesn't set job_timeout_seconds
        assert config.job_timeout_seconds == 86400.0

    def test_config_file_then_profile(self, tmp_path: Path) -> None:
        """Profile overrides config file values."""
        config_data = {
            "max_concurrent_jobs": 10,
            "log_level": "warning",
        }
        config_file = tmp_path / "conductor.yaml"
        config_file.write_text(yaml.dump(config_data))

        # Simulate the resolution: load config file, then merge profile
        with open(config_file) as f:
            file_data = yaml.safe_load(f) or {}

        profile_data = get_profile("dev")
        merged = deep_merge(file_data, profile_data)
        config = DaemonConfig.model_validate(merged)

        # Profile's max_concurrent_jobs (2) overrides config file's (10)
        assert config.max_concurrent_jobs == 2
        # Profile's log_level (debug) overrides config file's (warning)
        assert config.log_level == "debug"

    def test_config_file_preserved_when_not_in_profile(self, tmp_path: Path) -> None:
        """Config file values not overridden by profile are preserved."""
        config_data = {
            "job_timeout_seconds": 7200,
            "log_level": "warning",
        }
        config_file = tmp_path / "conductor.yaml"
        config_file.write_text(yaml.dump(config_data))

        with open(config_file) as f:
            file_data = yaml.safe_load(f) or {}

        profile_data = get_profile("minimal")
        merged = deep_merge(file_data, profile_data)
        config = DaemonConfig.model_validate(merged)

        # minimal doesn't touch job_timeout_seconds
        assert config.job_timeout_seconds == 7200
        # minimal doesn't touch log_level
        assert config.log_level == "warning"

    def test_intensive_profile_resource_limits(self) -> None:
        """Intensive profile sets high resource limits."""
        data = get_profile("intensive")
        config = DaemonConfig.model_validate(data)
        assert config.resource_limits.max_memory_mb == 16384
        assert config.resource_limits.max_processes == 100
        assert config.job_timeout_seconds == 172800

    def test_dev_profile_profiler_settings(self) -> None:
        """Dev profile enables strace and fast snapshots."""
        data = get_profile("dev")
        config = DaemonConfig.model_validate(data)
        assert config.profiler.enabled is True
        assert config.profiler.strace_enabled is True
        assert config.profiler.interval_seconds == 2.0

    def test_minimal_disables_profiler_and_learning(self) -> None:
        """Minimal profile turns off profiler and learning."""
        data = get_profile("minimal")
        config = DaemonConfig.model_validate(data)
        assert config.profiler.enabled is False
        assert config.learning.enabled is False


# ── Integration with _load_config ─────────────────────────────────────


class TestLoadConfigWithProfile:
    """Test _load_config integration with profiles."""

    def test_load_config_no_file_no_profile(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without config file or profile, returns defaults."""
        from marianne.daemon.process import _load_config

        monkeypatch.setenv("HOME", str(tmp_path))
        config = _load_config(None, profile=None)
        assert config.max_concurrent_jobs == 15  # new default

    def test_load_config_with_profile(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Profile without config file applies over defaults."""
        from marianne.daemon.process import _load_config

        monkeypatch.setenv("HOME", str(tmp_path))
        config = _load_config(None, profile="dev")
        assert config.log_level == "debug"
        assert config.max_concurrent_jobs == 2

    def test_load_config_file_and_profile(self, tmp_path: Path) -> None:
        """Config file + profile: profile wins on conflicts."""
        from marianne.daemon.process import _load_config

        config_file = tmp_path / "conductor.yaml"
        config_file.write_text(yaml.dump({
            "max_concurrent_jobs": 10,
            "job_timeout_seconds": 7200,
        }))

        config = _load_config(config_file, profile="dev")
        # Profile overrides max_concurrent_jobs
        assert config.max_concurrent_jobs == 2
        # Config file's job_timeout_seconds preserved (not in dev profile)
        assert config.job_timeout_seconds == 7200
        # Profile sets log_level
        assert config.log_level == "debug"

    def test_load_config_unknown_profile_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unknown profile name raises FileNotFoundError."""
        from marianne.daemon.process import _load_config

        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(FileNotFoundError, match="Unknown daemon profile"):
            _load_config(None, profile="banana")
