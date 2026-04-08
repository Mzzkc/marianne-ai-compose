"""Tests for the native instrument bridge.

The bridge registers Marianne's 4 existing backends (claude_cli, anthropic_api,
ollama, recursive_light) as named instruments in the InstrumentRegistry, so
they can be resolved by the same name-based lookup used for plugin instruments.

TDD: red first, then green. These tests define the contract.
"""

from __future__ import annotations

import pytest

from marianne.core.config.instruments import InstrumentProfile


# --- InstrumentRegistry ---


class TestInstrumentRegistry:
    """Tests for the InstrumentRegistry — the central instrument lookup."""

    def test_empty_registry(self):
        """A fresh registry has no instruments."""
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        assert len(registry) == 0
        assert registry.get("anything") is None

    def test_register_and_get(self):
        """Register a profile and retrieve by name."""
        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
        )
        from marianne.instruments.registry import InstrumentRegistry

        profile = InstrumentProfile(
            name="test-cli",
            display_name="Test CLI",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="test", prompt_flag="-p"),
                output=CliOutputConfig(),
            ),
        )
        registry = InstrumentRegistry()
        registry.register(profile)
        assert registry.get("test-cli") is profile

    def test_register_duplicate_raises(self):
        """Registering the same name twice raises ValueError."""
        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
        )
        from marianne.instruments.registry import InstrumentRegistry

        profile = InstrumentProfile(
            name="dup",
            display_name="Dup",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="test", prompt_flag="-p"),
                output=CliOutputConfig(),
            ),
        )
        registry = InstrumentRegistry()
        registry.register(profile)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(profile)

    def test_register_duplicate_with_override(self):
        """Override flag allows replacing an existing instrument."""
        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
        )
        from marianne.instruments.registry import InstrumentRegistry

        p1 = InstrumentProfile(
            name="dup",
            display_name="First",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="first", prompt_flag="-p"),
                output=CliOutputConfig(),
            ),
        )
        p2 = InstrumentProfile(
            name="dup",
            display_name="Second",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="second", prompt_flag="-p"),
                output=CliOutputConfig(),
            ),
        )
        registry = InstrumentRegistry()
        registry.register(p1)
        registry.register(p2, override=True)
        assert registry.get("dup") is p2

    def test_list_all(self):
        """list_all returns all registered profiles sorted by name."""
        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
        )
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        for name in ["zzz", "aaa", "mmm"]:
            registry.register(
                InstrumentProfile(
                    name=name,
                    display_name=name.upper(),
                    kind="cli",
                    cli=CliProfile(
                        command=CliCommand(executable="test", prompt_flag="-p"),
                        output=CliOutputConfig(),
                    ),
                )
            )
        all_profiles = registry.list_all()
        assert [p.name for p in all_profiles] == ["aaa", "mmm", "zzz"]

    def test_contains(self):
        """Registry supports 'in' operator."""
        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
        )
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        registry.register(
            InstrumentProfile(
                name="exists",
                display_name="Exists",
                kind="cli",
                cli=CliProfile(
                    command=CliCommand(executable="test", prompt_flag="-p"),
                    output=CliOutputConfig(),
                ),
            )
        )
        assert "exists" in registry
        assert "missing" not in registry


# --- Native Instrument Bridge ---


class TestNativeInstrumentBridge:
    """Tests for registering existing backends as named instruments."""

    def test_register_native_instruments(self):
        """register_native_instruments populates 4 native backends."""
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        assert len(registry) == 4
        assert "claude_cli" in registry
        assert "anthropic_api" in registry
        assert "ollama" in registry
        assert "recursive_light" in registry

    def test_claude_cli_profile(self):
        """Claude CLI native instrument has correct metadata."""
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        profile = registry.get("claude_cli")
        assert profile is not None
        assert profile.kind == "cli"
        assert profile.display_name == "Claude Code"
        assert "tool_use" in profile.capabilities
        assert "file_editing" in profile.capabilities
        assert "shell_access" in profile.capabilities
        assert "mcp" in profile.capabilities
        assert len(profile.models) >= 1
        assert profile.default_timeout_seconds == 1800.0

    def test_anthropic_api_profile(self):
        """Anthropic API native instrument has correct metadata."""
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        profile = registry.get("anthropic_api")
        assert profile is not None
        assert profile.kind == "http"
        assert profile.display_name == "Anthropic API"
        assert len(profile.models) >= 1

    def test_ollama_profile(self):
        """Ollama native instrument has correct metadata."""
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        profile = registry.get("ollama")
        assert profile is not None
        assert profile.kind == "http"
        assert profile.display_name == "Ollama"
        assert len(profile.models) >= 1

    def test_recursive_light_profile(self):
        """Recursive Light native instrument has correct metadata."""
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        profile = registry.get("recursive_light")
        assert profile is not None
        assert profile.kind == "http"
        assert profile.display_name == "Recursive Light"

    def test_native_instruments_are_profiles(self):
        """All native instruments are valid InstrumentProfile instances."""
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        for profile in registry.list_all():
            assert isinstance(profile, InstrumentProfile)
            # Every profile must have a display_name
            assert len(profile.display_name) > 0
            # Kind-specific profile must be present
            if profile.kind == "cli":
                assert profile.cli is not None
            elif profile.kind == "http":
                assert profile.http is not None

    def test_native_instruments_have_descriptions(self):
        """All native instruments include a description."""
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        for profile in registry.list_all():
            assert profile.description is not None
            assert len(profile.description) > 0


# --- Registry with native + custom profiles ---


class TestRegistryComposition:
    """Test composing native instruments with custom profiles."""

    def test_custom_does_not_override_native(self):
        """Custom profile with same name as native raises by default."""
        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
        )
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(
                InstrumentProfile(
                    name="claude_cli",
                    display_name="Fake Claude",
                    kind="cli",
                    cli=CliProfile(
                        command=CliCommand(executable="fake", prompt_flag="-p"),
                        output=CliOutputConfig(),
                    ),
                )
            )

    def test_custom_can_override_native_explicitly(self):
        """Custom profile can override native with override=True."""
        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
        )
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        custom = InstrumentProfile(
            name="claude_cli",
            display_name="Custom Claude",
            kind="cli",
            cli=CliProfile(
                command=CliCommand(executable="custom-claude", prompt_flag="-p"),
                output=CliOutputConfig(),
            ),
        )
        registry.register(custom, override=True)
        assert registry.get("claude_cli") is custom
        assert registry.get("claude_cli").display_name == "Custom Claude"

    def test_custom_instruments_coexist_with_native(self):
        """Custom instruments register alongside native ones."""
        from marianne.core.config.instruments import (
            CliCommand,
            CliOutputConfig,
            CliProfile,
        )
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        registry = InstrumentRegistry()
        register_native_instruments(registry)
        registry.register(
            InstrumentProfile(
                name="gemini-cli",
                display_name="Gemini CLI",
                kind="cli",
                cli=CliProfile(
                    command=CliCommand(executable="gemini", prompt_flag="-p"),
                    output=CliOutputConfig(),
                ),
            )
        )
        assert len(registry) == 5
        assert "gemini-cli" in registry
        assert "claude_cli" in registry
