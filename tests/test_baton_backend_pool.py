"""Tests for the baton's BackendPool — per-instrument backend management.

The BackendPool manages Backend instances for per-sheet execution:
- CLI instruments get one Backend per concurrent sheet
- HTTP instruments share a singleton
- Concurrency tracking per instrument
- Graceful close on job completion

TDD: tests written alongside implementation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    HttpProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.daemon.baton.backend_pool import BackendPool, _create_backend_for_profile
from marianne.daemon.keyring import ApiKeyKeyring
from marianne.daemon.keyring_config import InstrumentKeyring, KeyEntry, KeyringConfig
from marianne.instruments.registry import InstrumentRegistry

# =============================================================================
# Fixtures
# =============================================================================


def _make_cli_profile(name: str = "test-cli") -> InstrumentProfile:
    """Create a minimal CLI instrument profile for testing."""
    return InstrumentProfile(
        name=name,
        display_name=f"Test CLI ({name})",
        kind="cli",
        capabilities={"tool_use", "file_editing"},
        models=[
            ModelCapacity(
                name="test-model",
                context_window=100000,
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
            ),
        ],
        default_model="test-model",
        cli=CliProfile(
            command=CliCommand(
                executable="echo",
                prompt_flag=None,
            ),
            output=CliOutputConfig(format="text"),
        ),
    )


def _make_http_profile(name: str = "test-http") -> InstrumentProfile:
    """Create a minimal HTTP instrument profile for testing (non-OpenRouter)."""
    return InstrumentProfile(
        name=name,
        display_name=f"Test HTTP ({name})",
        kind="http",
        capabilities={"tool_use"},
        models=[
            ModelCapacity(
                name="test-model",
                context_window=100000,
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
            ),
        ],
        default_model="test-model",
        http=HttpProfile(
            base_url="http://localhost:8080",
            schema_family="openai",
        ),
    )


def _make_openrouter_profile(name: str = "openrouter") -> InstrumentProfile:
    """Create an OpenRouter HTTP instrument profile for testing."""
    return InstrumentProfile(
        name=name,
        display_name="OpenRouter",
        kind="http",
        capabilities={"tool_use"},
        models=[
            ModelCapacity(
                name="minimax/minimax-m1-80k",
                context_window=80000,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            ),
        ],
        default_model="minimax/minimax-m1-80k",
        http=HttpProfile(
            base_url="https://openrouter.ai/api/v1",
            schema_family="openai",
            auth_env_var="OPENROUTER_API_KEY",
        ),
    )


def _make_registry(*profiles: InstrumentProfile) -> InstrumentRegistry:
    """Create a registry with the given profiles."""
    registry = InstrumentRegistry()
    for p in profiles:
        registry.register(p)
    return registry


# =============================================================================
# Construction
# =============================================================================


class TestBackendPoolConstruction:
    """BackendPool can be created with a registry."""

    async def test_creates_with_registry(self) -> None:
        registry = _make_registry(_make_cli_profile())
        pool = BackendPool(registry)
        assert pool is not None

    async def test_initially_empty(self) -> None:
        registry = _make_registry(_make_cli_profile())
        pool = BackendPool(registry)
        assert pool.total_in_flight() == 0
        assert pool.in_flight_count("test-cli") == 0


# =============================================================================
# Acquire / Release — CLI instruments
# =============================================================================


class TestAcquireReleaseCli:
    """CLI instruments get per-execution backends."""

    async def test_acquire_returns_backend(self) -> None:
        profile = _make_cli_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        backend = await pool.acquire("test-cli")
        assert backend is not None
        assert pool.in_flight_count("test-cli") == 1
        await pool.close_all()

    async def test_release_decrements_in_flight(self) -> None:
        profile = _make_cli_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        backend = await pool.acquire("test-cli")
        assert pool.in_flight_count("test-cli") == 1

        await pool.release("test-cli", backend)
        assert pool.in_flight_count("test-cli") == 0
        await pool.close_all()

    async def test_multiple_acquires_create_separate_backends(self) -> None:
        """Each concurrent acquire for a CLI instrument gets its own backend."""
        profile = _make_cli_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        b1 = await pool.acquire("test-cli")
        b2 = await pool.acquire("test-cli")
        assert b1 is not b2
        assert pool.in_flight_count("test-cli") == 2
        await pool.close_all()

    async def test_released_backend_is_reused(self) -> None:
        """After release, the same backend is returned on next acquire."""
        profile = _make_cli_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        b1 = await pool.acquire("test-cli")
        await pool.release("test-cli", b1)

        b2 = await pool.acquire("test-cli")
        assert b2 is b1  # Reused from free list
        await pool.close_all()

    async def test_acquire_with_working_directory(self) -> None:
        profile = _make_cli_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        backend = await pool.acquire("test-cli", working_directory=Path("/tmp/test-ws"))
        assert backend.working_directory == Path("/tmp/test-ws")
        await pool.close_all()


# =============================================================================
# Multiple instruments
# =============================================================================


class TestMultipleInstruments:
    """Different instruments have independent pools."""

    async def test_independent_in_flight_tracking(self) -> None:
        p1 = _make_cli_profile("instrument-a")
        p2 = _make_cli_profile("instrument-b")
        registry = _make_registry(p1, p2)
        pool = BackendPool(registry)

        b1 = await pool.acquire("instrument-a")
        _b2 = await pool.acquire("instrument-b")
        _b3 = await pool.acquire("instrument-a")

        assert pool.in_flight_count("instrument-a") == 2
        assert pool.in_flight_count("instrument-b") == 1
        assert pool.total_in_flight() == 3

        await pool.release("instrument-a", b1)
        assert pool.in_flight_count("instrument-a") == 1
        assert pool.total_in_flight() == 2

        await pool.close_all()

    async def test_release_on_wrong_instrument_is_safe(self) -> None:
        """Releasing with a different instrument name is handled gracefully."""
        p1 = _make_cli_profile("instrument-a")
        p2 = _make_cli_profile("instrument-b")
        registry = _make_registry(p1, p2)
        pool = BackendPool(registry)

        backend = await pool.acquire("instrument-a")
        # Release under wrong name — should not crash
        await pool.release("instrument-b", backend)
        # in_flight for instrument-a still shows 1 (not decremented)
        assert pool.in_flight_count("instrument-a") == 1
        # instrument-b was never incremented, so stays at 0
        assert pool.in_flight_count("instrument-b") == 0
        await pool.close_all()


# =============================================================================
# Error cases
# =============================================================================


class TestErrors:
    """Error handling for bad instrument names and closed pools."""

    async def test_acquire_unknown_instrument_raises(self) -> None:
        registry = _make_registry(_make_cli_profile("only-this"))
        pool = BackendPool(registry)

        with pytest.raises(ValueError, match="not found in registry"):
            await pool.acquire("nonexistent")

    async def test_acquire_after_close_raises(self) -> None:
        registry = _make_registry(_make_cli_profile())
        pool = BackendPool(registry)
        await pool.close_all()

        with pytest.raises(RuntimeError, match="closed"):
            await pool.acquire("test-cli")

    async def test_close_all_is_idempotent(self) -> None:
        registry = _make_registry(_make_cli_profile())
        pool = BackendPool(registry)
        await pool.acquire("test-cli")

        await pool.close_all()
        await pool.close_all()  # Second call should not raise
        assert pool.total_in_flight() == 0


# =============================================================================
# Close behavior
# =============================================================================


class TestCloseAll:
    """close_all() releases all resources."""

    async def test_close_all_calls_backend_close(self) -> None:
        """Every created backend has close() called."""
        profile = _make_cli_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        b1 = await pool.acquire("test-cli")
        b2 = await pool.acquire("test-cli")

        # Mock close on both backends
        b1.close = AsyncMock()
        b2.close = AsyncMock()

        await pool.close_all()

        b1.close.assert_awaited_once()
        b2.close.assert_awaited_once()

    async def test_close_all_resets_tracking(self) -> None:
        profile = _make_cli_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        await pool.acquire("test-cli")
        await pool.close_all()

        assert pool.total_in_flight() == 0
        assert pool.in_flight_count("test-cli") == 0

    async def test_close_all_handles_backend_close_error(self) -> None:
        """If a backend's close() raises, others still get closed."""
        profile = _make_cli_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        b1 = await pool.acquire("test-cli")
        b2 = await pool.acquire("test-cli")

        b1.close = AsyncMock(side_effect=RuntimeError("boom"))
        b2.close = AsyncMock()
        # Should not raise despite b1's error
        await pool.close_all()

        b1.close.assert_awaited_once()
        b2.close.assert_awaited_once()


# =============================================================================
# HTTP instrument singleton behavior
# =============================================================================


class TestHttpSingleton:
    """HTTP instruments share a singleton backend."""

    async def test_http_openai_family_is_dispatched(self) -> None:
        """After Phase 3, HTTP profiles with schema_family='openai' dispatch
        through the generic OpenAI-compat handler rather than raising
        NotImplementedError (Atlas Doctrine RULE: "Generic HTTP instrument
        dispatch must work for all HttpProfile schema families")."""
        profile = _make_http_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        backend = await pool.acquire("test-http")
        assert backend is not None
        await pool.close_all()


# =============================================================================
# Concurrency tracking
# =============================================================================


class TestConcurrencyTracking:
    """The pool accurately tracks in-flight backends for dispatch logic."""

    async def test_concurrent_acquire_release_cycle(self) -> None:
        """Simulate a realistic acquire-execute-release cycle."""
        profile = _make_cli_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        backends = []
        # Acquire 5 backends
        for _ in range(5):
            b = await pool.acquire("test-cli")
            backends.append(b)
        assert pool.in_flight_count("test-cli") == 5

        # Release 3
        for b in backends[:3]:
            await pool.release("test-cli", b)
        assert pool.in_flight_count("test-cli") == 2

        # Acquire 2 more (should reuse released ones)
        b6 = await pool.acquire("test-cli")
        b7 = await pool.acquire("test-cli")
        assert pool.in_flight_count("test-cli") == 4

        # The reused backends should be from the original batch
        assert b6 in backends[:3]
        assert b7 in backends[:3]

        await pool.close_all()

    async def test_in_flight_count_for_missing_instrument(self) -> None:
        """Querying in-flight for an unregistered instrument returns 0."""
        registry = _make_registry(_make_cli_profile())
        pool = BackendPool(registry)
        assert pool.in_flight_count("nonexistent") == 0


# =============================================================================
# Factory function
# =============================================================================


class TestCreateBackendForProfile:
    """The factory function creates the right backend type."""

    async def test_cli_profile_creates_plugin_cli_backend(self) -> None:
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_cli_profile()
        backend = _create_backend_for_profile(profile)
        assert isinstance(backend, PluginCliBackend)

    async def test_http_profile_dispatches_via_schema_family(self) -> None:
        """Phase 3: HTTP profiles dispatch by schema_family, not name."""
        profile = _make_http_profile()
        backend = _create_backend_for_profile(profile)
        assert backend is not None

    async def test_cli_profile_with_working_directory(self) -> None:
        profile = _make_cli_profile()
        backend = _create_backend_for_profile(profile, working_directory=Path("/tmp/test"))
        assert backend.working_directory == Path("/tmp/test")

    async def test_openrouter_profile_creates_openrouter_backend(self) -> None:
        from marianne.backends.openrouter import OpenRouterBackend

        profile = _make_openrouter_profile()
        backend = _create_backend_for_profile(profile)
        assert isinstance(backend, OpenRouterBackend)

    async def test_openrouter_profile_with_api_key(self) -> None:
        from marianne.backends.openrouter import OpenRouterBackend

        profile = _make_openrouter_profile()
        backend = _create_backend_for_profile(
            profile,
            api_key="sk-test-injected-key",
        )
        assert isinstance(backend, OpenRouterBackend)
        assert backend._api_key == "sk-test-injected-key"

    async def test_openrouter_profile_with_model_override(self) -> None:
        from marianne.backends.openrouter import OpenRouterBackend

        profile = _make_openrouter_profile()
        backend = _create_backend_for_profile(
            profile,
            model="google/gemma-4",
        )
        assert isinstance(backend, OpenRouterBackend)
        assert backend.model == "google/gemma-4"


# =============================================================================
# HTTP singleton with OpenRouter
# =============================================================================


class TestOpenRouterHttpSingleton:
    """OpenRouter HTTP instruments use singleton pattern in the pool."""

    async def test_openrouter_singleton_is_reused(self) -> None:
        profile = _make_openrouter_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        b1 = await pool.acquire("openrouter")
        b2 = await pool.acquire("openrouter")
        assert b1 is b2
        assert pool.in_flight_count("openrouter") == 2
        await pool.close_all()

    async def test_openrouter_release_decrements_in_flight(self) -> None:
        profile = _make_openrouter_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)

        b1 = await pool.acquire("openrouter")
        assert pool.in_flight_count("openrouter") == 1

        await pool.release("openrouter", b1)
        assert pool.in_flight_count("openrouter") == 0
        await pool.close_all()


# =============================================================================
# Keyring integration with BackendPool
# =============================================================================


class TestKeyringIntegration:
    """BackendPool uses keyring for HTTP backend API keys."""

    async def test_keyring_injects_api_key(self, tmp_path: Path) -> None:
        from marianne.backends.openrouter import OpenRouterBackend

        # Set up key file
        key_file = tmp_path / "openrouter.key"
        key_file.write_text("sk-from-keyring")

        config = KeyringConfig(
            instruments={
                "openrouter": InstrumentKeyring(
                    keys=[KeyEntry(path=str(key_file), label="test")],
                ),
            },
        )
        keyring = ApiKeyKeyring(config)

        profile = _make_openrouter_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry, keyring=keyring)

        backend = await pool.acquire("openrouter")
        assert isinstance(backend, OpenRouterBackend)
        assert backend._api_key == "sk-from-keyring"
        await pool.close_all()

    async def test_no_keyring_falls_back_to_env(self) -> None:
        """Without keyring, backend reads from environment variable."""
        profile = _make_openrouter_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry)  # No keyring

        backend = await pool.acquire("openrouter")
        # API key comes from env (may be None in test env, but no crash)
        assert backend is not None
        await pool.close_all()

    async def test_keyring_failure_does_not_crash(self, tmp_path: Path) -> None:
        """If keyring key file is missing, acquisition still works."""
        config = KeyringConfig(
            instruments={
                "openrouter": InstrumentKeyring(
                    keys=[KeyEntry(path=str(tmp_path / "missing.key"), label="missing")],
                ),
            },
        )
        keyring = ApiKeyKeyring(config)

        profile = _make_openrouter_profile()
        registry = _make_registry(profile)
        pool = BackendPool(registry, keyring=keyring)

        # Should not crash — keyring failure is logged, backend created without injected key
        backend = await pool.acquire("openrouter")
        assert backend is not None
        await pool.close_all()
