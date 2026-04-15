"""Hardening tests for --conductor-clone — Harper, movement 1.

Tests the security, edge cases, and integration seams of the conductor
clone feature that other musicians built. Focus areas:
1. Clone name sanitization (adversarial inputs)
2. Config inheritance (non-path fields preserved)
3. Clone path isolation guarantees
4. Global state cleanup (no leakage between tests)
5. CliOutputConfig aggregate_tokens YAML round-trip
6. Built-in profile validation (gemini-cli, claude-code)

TDD: Red first, then green.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from marianne.daemon.config import DaemonConfig


# =============================================================================
# Clone name sanitization — adversarial inputs
# =============================================================================


class TestCloneNameSanitizationAdversarial:
    """The _sanitize_name function must neutralize all dangerous inputs."""

    def test_path_traversal_dots(self) -> None:
        """Path traversal with dots must be sanitized."""
        from marianne.daemon.clone import _sanitize_name

        assert ".." not in _sanitize_name("../../etc/passwd")
        # Should produce something like "etc-passwd"
        result = _sanitize_name("../../etc/passwd")
        assert "/" not in result
        assert ".." not in result

    def test_null_bytes_stripped(self) -> None:
        """Null bytes must not survive sanitization."""
        from marianne.daemon.clone import _sanitize_name

        result = _sanitize_name("test\x00evil")
        assert "\x00" not in result

    def test_very_long_name(self) -> None:
        """Very long names should produce a usable path."""
        from marianne.daemon.clone import resolve_clone_paths

        long_name = "a" * 500
        paths = resolve_clone_paths(long_name)
        # Socket path must not exceed filesystem limits
        # (Unix socket paths limited to ~108 chars on Linux)
        assert len(str(paths.socket)) < 200

    def test_unicode_characters(self) -> None:
        """Unicode characters should be sanitized to safe ASCII."""
        from marianne.daemon.clone import _sanitize_name

        result = _sanitize_name("tëst-ünîcödé")
        # Non-ASCII should be replaced with hyphens
        assert all(c.isalnum() or c == "-" for c in result)
        assert len(result) > 0

    def test_only_special_chars(self) -> None:
        """A name of only special characters produces empty string."""
        from marianne.daemon.clone import _sanitize_name

        result = _sanitize_name("///...")
        # After sanitization and stripping, might be empty
        assert "/" not in result
        assert "." not in result

    def test_spaces_in_name(self) -> None:
        """Spaces become hyphens."""
        from marianne.daemon.clone import _sanitize_name

        assert _sanitize_name("my clone") == "my-clone"

    def test_consecutive_special_chars_collapse(self) -> None:
        """Multiple consecutive special chars collapse to single hyphen."""
        from marianne.daemon.clone import _sanitize_name

        result = _sanitize_name("a///b...c")
        assert "---" not in result  # Should collapse
        assert result == "a-b-c"

    def test_leading_trailing_hyphens_preserved(self) -> None:
        """Leading/trailing hyphens are preserved for uniqueness.

        Stripping hyphens made _sanitize_name lossy — e.g., '0' and '_0'
        would both sanitize to '0', causing clone path collisions. Hyphens
        in path components like /tmp/marianne-clone--test.sock are safe.
        """
        from marianne.daemon.clone import _sanitize_name

        assert _sanitize_name("-test-") == "-test-"
        assert _sanitize_name("---test---") == "-test-"  # hyphen collapse, not strip


# =============================================================================
# Config inheritance — non-path fields preserved
# =============================================================================


class TestCloneConfigInheritance:
    """Clone config must preserve ALL non-path settings from base config."""

    def test_max_concurrent_jobs_preserved(self) -> None:
        """max_concurrent_jobs from base config survives clone."""
        from marianne.daemon.clone import build_clone_config

        base = DaemonConfig(max_concurrent_jobs=10)
        clone = build_clone_config("test", base_config=base)
        assert clone.max_concurrent_jobs == 10

    def test_max_concurrent_sheets_preserved(self) -> None:
        """max_concurrent_sheets from base config survives clone."""
        from marianne.daemon.clone import build_clone_config

        base = DaemonConfig(max_concurrent_sheets=8)
        clone = build_clone_config("test", base_config=base)
        assert clone.max_concurrent_sheets == 8

    def test_clone_socket_differs_from_base(self) -> None:
        """Clone socket must always differ from base."""
        from marianne.daemon.clone import build_clone_config

        base = DaemonConfig()
        clone = build_clone_config("test", base_config=base)
        assert clone.socket.path != base.socket.path

    def test_clone_pid_differs_from_base(self) -> None:
        """Clone PID file must always differ from base."""
        from marianne.daemon.clone import build_clone_config

        base = DaemonConfig()
        clone = build_clone_config("test", base_config=base)
        assert clone.pid_file != base.pid_file

    def test_clone_state_db_differs_from_base(self) -> None:
        """Clone state DB path must always differ from base (F-132)."""
        from marianne.daemon.clone import build_clone_config

        base = DaemonConfig()
        clone = build_clone_config("test", base_config=base)
        assert clone.state_db_path != base.state_db_path
        assert "clone" in str(clone.state_db_path)

    def test_clone_state_db_matches_resolved_paths(self) -> None:
        """Clone config state_db_path must match ClonePaths.state_db (F-132)."""
        from marianne.daemon.clone import build_clone_config, resolve_clone_paths

        base = DaemonConfig()
        clone = build_clone_config("staging", base_config=base)
        expected = resolve_clone_paths("staging")
        assert clone.state_db_path == expected.state_db

    def test_named_clones_have_different_state_dbs(self) -> None:
        """Different clone names must produce different state DB paths (F-132)."""
        from marianne.daemon.clone import build_clone_config

        base = DaemonConfig()
        clone_a = build_clone_config("alpha", base_config=base)
        clone_b = build_clone_config("beta", base_config=base)
        assert clone_a.state_db_path != clone_b.state_db_path

    def test_none_base_config_uses_defaults(self) -> None:
        """Without base config, clone uses DaemonConfig defaults."""
        from marianne.daemon.clone import build_clone_config

        clone = build_clone_config("test", base_config=None)
        defaults = DaemonConfig()
        assert clone.max_concurrent_jobs == defaults.max_concurrent_jobs


class TestStartConductorCloneConfig:
    """Verify that start_conductor's inline clone path override
    applies ALL clone paths — including state_db_path (F-132).

    The real bug: process.py:start_conductor() duplicates clone path
    logic from clone.py but missed state_db_path. The clone conductor
    opens the production registry DB instead of a clone-specific one.
    """

    def test_start_conductor_clone_overrides_state_db_path(self) -> None:
        """F-132: start_conductor clone path override must set state_db_path."""
        from unittest.mock import patch

        from marianne.daemon.clone import resolve_clone_paths

        clone_paths = resolve_clone_paths("f132-test")

        # Mock _load_config to return a default config
        mock_config = DaemonConfig()

        captured_config: list[DaemonConfig] = []

        original_init = None
        try:
            from marianne.daemon.process import DaemonProcess

            original_init = DaemonProcess.__init__

            def capturing_init(inst: object, config: DaemonConfig) -> None:
                captured_config.append(config)
                raise SystemExit(0)  # Stop before asyncio.run

            DaemonProcess.__init__ = capturing_init  # type: ignore[assignment]

            with (
                patch("marianne.daemon.process._load_config", return_value=mock_config),
                patch("marianne.daemon.process._read_pid", return_value=None),
                patch("marianne.core.logging.configure_logging"),
                patch("marianne.daemon.process._daemonize"),
            ):
                from marianne.daemon.process import start_conductor

                with pytest.raises(SystemExit):
                    start_conductor(clone_name="f132-test")

            assert len(captured_config) == 1
            config = captured_config[0]
            assert config.state_db_path == clone_paths.state_db, (
                f"state_db_path should be {clone_paths.state_db}, "
                f"got {config.state_db_path}"
            )
            assert config.socket.path == clone_paths.socket
            assert config.pid_file == clone_paths.pid_file
        finally:
            if original_init is not None:
                DaemonProcess.__init__ = original_init  # type: ignore[assignment]

    def test_start_conductor_clone_state_db_differs_from_production(self) -> None:
        """Clone conductor state_db_path must differ from production default."""
        from unittest.mock import patch

        prod_default = DaemonConfig().state_db_path

        captured_config: list[DaemonConfig] = []

        original_init = None
        try:
            from marianne.daemon.process import DaemonProcess

            original_init = DaemonProcess.__init__

            def capturing_init(inst: object, config: DaemonConfig) -> None:
                captured_config.append(config)
                raise SystemExit(0)

            DaemonProcess.__init__ = capturing_init  # type: ignore[assignment]

            with (
                patch(
                    "marianne.daemon.process._load_config",
                    return_value=DaemonConfig(),
                ),
                patch("marianne.daemon.process._read_pid", return_value=None),
                patch("marianne.core.logging.configure_logging"),
                patch("marianne.daemon.process._daemonize"),
            ):
                from marianne.daemon.process import start_conductor

                with pytest.raises(SystemExit):
                    start_conductor(clone_name="isolation-test")

            assert len(captured_config) == 1
            assert captured_config[0].state_db_path != prod_default, (
                "Clone state_db_path must not equal production default"
            )
        finally:
            if original_init is not None:
                DaemonProcess.__init__ = original_init  # type: ignore[assignment]


# =============================================================================
# Clone path isolation guarantees
# =============================================================================


class TestClonePathIsolation:
    """Clone paths must NEVER collide with production or other clones."""

    def test_production_paths_not_in_clone(self) -> None:
        """No clone path should equal any production path."""
        from marianne.daemon.clone import resolve_clone_paths

        prod = DaemonConfig()
        for name in [None, "", "test", "staging", "ci"]:
            clone = resolve_clone_paths(name)
            assert clone.socket != prod.socket.path, f"name={name!r}"
            assert clone.pid_file != prod.pid_file, f"name={name!r}"

    def test_all_clone_paths_contain_clone_marker(self) -> None:
        """Every clone path must contain 'clone' for visibility."""
        from marianne.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths("mytest")
        assert "clone" in str(paths.socket)
        assert "clone" in str(paths.pid_file)
        assert "clone" in str(paths.state_db)
        assert "clone" in str(paths.log_file)

    def test_default_and_named_clones_differ(self) -> None:
        """Default clone and named clone have different paths."""
        from marianne.daemon.clone import resolve_clone_paths

        default = resolve_clone_paths(None)
        named = resolve_clone_paths("custom")
        assert default.socket != named.socket
        assert default.state_db != named.state_db


# =============================================================================
# Global state cleanup
# =============================================================================


class TestCloneGlobalStateCleanup:
    """Clone state must not leak between operations."""

    def test_set_none_deactivates(self) -> None:
        """Setting None deactivates clone mode."""
        from marianne.daemon.clone import (
            get_clone_name,
            is_clone_active,
            set_clone_name,
        )

        set_clone_name("active")
        assert is_clone_active() is True
        set_clone_name(None)
        assert is_clone_active() is False
        assert get_clone_name() is None

    def test_empty_string_is_active(self) -> None:
        """Empty string activates clone mode (default clone)."""
        from marianne.daemon.clone import is_clone_active, set_clone_name

        set_clone_name("")
        try:
            assert is_clone_active() is True
        finally:
            set_clone_name(None)


# =============================================================================
# Built-in profile validation
# =============================================================================


class TestBuiltInProfileValidation:
    """Built-in instrument profiles must be valid and self-consistent."""

    def test_gemini_cli_has_aggregate_tokens(self) -> None:
        """gemini-cli profile must have aggregate_tokens=True."""
        from marianne.instruments.loader import InstrumentProfileLoader

        loader = InstrumentProfileLoader()
        profiles = loader.load_directory(
            Path("src/marianne/instruments/builtins")
        )
        gemini = profiles.get("gemini-cli")
        assert gemini is not None, "gemini-cli profile not found"
        assert gemini.cli is not None
        assert gemini.cli.output.aggregate_tokens is True

    def test_claude_code_no_aggregate_tokens(self) -> None:
        """claude-code profile must have aggregate_tokens=False."""
        from marianne.instruments.loader import InstrumentProfileLoader

        loader = InstrumentProfileLoader()
        profiles = loader.load_directory(
            Path("src/marianne/instruments/builtins")
        )
        claude = profiles.get("claude-code")
        assert claude is not None, "claude-code profile not found"
        assert claude.cli is not None
        assert claude.cli.output.aggregate_tokens is False

    def test_gemini_cli_has_wildcard_token_paths(self) -> None:
        """gemini-cli token paths must use wildcards for multi-model routing."""
        from marianne.instruments.loader import InstrumentProfileLoader

        loader = InstrumentProfileLoader()
        profiles = loader.load_directory(
            Path("src/marianne/instruments/builtins")
        )
        gemini = profiles.get("gemini-cli")
        assert gemini is not None
        assert "*" in (gemini.cli.output.input_tokens_path or "")
        assert "*" in (gemini.cli.output.output_tokens_path or "")

    def test_claude_code_has_direct_token_paths(self) -> None:
        """claude-code token paths must NOT use wildcards (single model)."""
        from marianne.instruments.loader import InstrumentProfileLoader

        loader = InstrumentProfileLoader()
        profiles = loader.load_directory(
            Path("src/marianne/instruments/builtins")
        )
        claude = profiles.get("claude-code")
        assert claude is not None
        assert "*" not in (claude.cli.output.input_tokens_path or "")
        assert "*" not in (claude.cli.output.output_tokens_path or "")

    def test_all_builtin_profiles_load_without_error(self) -> None:
        """Every built-in profile must load and validate against the schema."""
        from marianne.instruments.loader import InstrumentProfileLoader

        loader = InstrumentProfileLoader()
        profiles = loader.load_directory(
            Path("src/marianne/instruments/builtins")
        )
        assert len(profiles) >= 6, f"Expected 6+ built-in profiles, got {len(profiles)}"
        for name, profile in profiles.items():
            assert profile.name == name, f"Profile name mismatch: {profile.name} != {name}"
            assert profile.display_name, f"{name} missing display_name"
            assert profile.kind in ("cli", "api", "native"), f"{name} invalid kind: {profile.kind}"

    def test_gemini_cli_has_error_patterns(self) -> None:
        """gemini-cli profile must have comprehensive error patterns."""
        from marianne.instruments.loader import InstrumentProfileLoader

        loader = InstrumentProfileLoader()
        profiles = loader.load_directory(
            Path("src/marianne/instruments/builtins")
        )
        gemini = profiles.get("gemini-cli")
        assert gemini is not None
        errors = gemini.cli.errors
        assert len(errors.rate_limit_patterns) >= 3
        assert len(errors.auth_error_patterns) >= 2
        assert len(errors.capacity_patterns) >= 1
        assert len(errors.timeout_patterns) >= 1

    def test_claude_code_has_rate_limit_patterns(self) -> None:
        """claude-code profile must have rate limit patterns for F-098."""
        from marianne.instruments.loader import InstrumentProfileLoader

        loader = InstrumentProfileLoader()
        profiles = loader.load_directory(
            Path("src/marianne/instruments/builtins")
        )
        claude = profiles.get("claude-code")
        assert claude is not None
        errors = claude.cli.errors
        # F-098: Rate limit patterns must include stdout patterns
        assert len(errors.rate_limit_patterns) >= 3
        # Must include the patterns that caused F-098 (stdout rate limits)
        patterns_text = " ".join(errors.rate_limit_patterns)
        assert "hit" in patterns_text.lower() or "limit" in patterns_text.lower()
