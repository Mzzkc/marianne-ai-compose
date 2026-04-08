"""TDD tests for check_instrument_available() utility.

Standalone availability check for instruments — used by:
- mzt doctor (instrument health checks)
- mzt validate (warn on unavailable instruments)
- Baton dispatch path (pre-flight before execution)

Tests:
1. CLI instrument with binary on PATH → available
2. CLI instrument with missing binary → unavailable with reason
3. Profile not found in registry → unavailable with reason
4. Non-CLI instrument kind → available (no binary check needed)
5. Profile with no command defined → unavailable with reason

TDD: Red first, then green.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    HttpProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.instruments.availability import check_instrument_available
from marianne.instruments.registry import InstrumentRegistry


def _make_model() -> ModelCapacity:
    return ModelCapacity(
        name="test-model",
        context_window=100000,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
    )


def _make_cli_profile(name: str, executable: str = "claude") -> InstrumentProfile:
    """Create a minimal CLI instrument profile for testing."""
    return InstrumentProfile(
        name=name,
        display_name=name.title(),
        description=f"Test {name}",
        kind="cli",
        cli=CliProfile(
            command=CliCommand(
                executable=executable,
                prompt_flag="-p",
            ),
            output=CliOutputConfig(),
        ),
        models=[_make_model()],
    )


def _make_api_profile(name: str) -> InstrumentProfile:
    """Create a minimal non-CLI instrument profile for testing."""
    return InstrumentProfile(
        name=name,
        display_name=name.title(),
        description=f"Test {name}",
        kind="http",
        http=HttpProfile(
            base_url="http://localhost:8080",
            schema_family="openai",
        ),
        models=[_make_model()],
    )


class TestCheckInstrumentAvailable:
    """check_instrument_available checks profile existence and binary
    availability for CLI instruments."""

    def test_cli_instrument_available(self) -> None:
        """CLI instrument with binary on PATH → (True, '')."""
        registry = InstrumentRegistry()
        profile = _make_cli_profile("test-cli", executable="python3")
        registry.register(profile)

        available, reason = check_instrument_available("test-cli", registry)

        assert available is True
        assert reason == ""

    def test_cli_instrument_missing_binary(self) -> None:
        """CLI instrument with missing binary → (False, 'binary not found')."""
        registry = InstrumentRegistry()
        profile = _make_cli_profile("ghost-cli", executable="nonexistent-binary-xyz")
        registry.register(profile)

        available, reason = check_instrument_available("ghost-cli", registry)

        assert available is False
        assert "not found" in reason.lower()

    def test_profile_not_in_registry(self) -> None:
        """Unknown instrument name → (False, 'profile not found')."""
        registry = InstrumentRegistry()

        available, reason = check_instrument_available("unknown-cli", registry)

        assert available is False
        assert "not found" in reason.lower() or "not registered" in reason.lower()

    def test_non_cli_instrument_available(self) -> None:
        """Non-CLI instrument → available (no binary check needed)."""
        registry = InstrumentRegistry()
        profile = _make_api_profile("test-api")
        registry.register(profile)

        available, reason = check_instrument_available("test-api", registry)

        assert available is True
        assert reason == ""

    def test_cli_instrument_no_command(self) -> None:
        """CLI instrument with cli=None (defensive guard) → unavailable.

        The InstrumentProfile validator normally prevents kind=cli without
        a cli: section. This test exercises the defensive guard in
        check_instrument_available() via a mock that bypasses validation.
        """
        from unittest.mock import MagicMock

        registry = InstrumentRegistry()
        # Create a mock profile that bypasses Pydantic validation
        mock_profile = MagicMock(spec=InstrumentProfile)
        mock_profile.name = "broken-cli"
        mock_profile.kind = "cli"
        mock_profile.cli = None
        registry._profiles["broken-cli"] = mock_profile

        available, reason = check_instrument_available("broken-cli", registry)

        assert available is False
        assert "cli" in reason.lower() or "command" in reason.lower()

    def test_available_with_shutil_which(self) -> None:
        """Verifies check_instrument_available uses shutil.which."""
        registry = InstrumentRegistry()
        profile = _make_cli_profile("test-cli", executable="python3")
        registry.register(profile)

        with patch("marianne.instruments.availability.shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/python3"
            available, reason = check_instrument_available("test-cli", registry)
            assert available is True
            mock_which.assert_called_once_with("python3")

    def test_unavailable_with_shutil_which_none(self) -> None:
        """When shutil.which returns None, instrument is unavailable."""
        registry = InstrumentRegistry()
        profile = _make_cli_profile("test-cli", executable="some-tool")
        registry.register(profile)

        with patch("marianne.instruments.availability.shutil.which") as mock_which:
            mock_which.return_value = None
            available, reason = check_instrument_available("test-cli", registry)
            assert available is False
            assert "not found" in reason.lower()
