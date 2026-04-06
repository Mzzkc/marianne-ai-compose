"""TDD tests for F-481: Wire PID tracking into baton path.

The legacy runner path already has PID tracking via callbacks on
ClaudeCliBackend. The baton path creates backends via BackendPool
→ PluginCliBackend, which lacks callback slots. Without them,
baton-created backends have zero orphan detection.

These tests verify:
1. PluginCliBackend has callback slots
2. PluginCliBackend fires callbacks on process spawn and exit
3. BackendPool accepts a pgroup reference
4. BackendPool wires callbacks into newly created backends
5. BackendPool wires callbacks into reused (free list) backends
6. End-to-end: BackendPool + PluginCliBackend + pgroup integration
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.daemon.baton.backend_pool import BackendPool
from marianne.daemon.pgroup import ProcessGroupManager
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _make_profile(name: str = "test-cli") -> InstrumentProfile:
    """Create a minimal CLI InstrumentProfile for testing."""
    return InstrumentProfile(
        name=name,
        display_name=f"Test CLI: {name}",
        kind="cli",
        description=f"Test CLI instrument: {name}",
        models=[
            ModelCapacity(
                name="test-model",
                context_window=128000,
                cost_per_1k_input=0.01,
                cost_per_1k_output=0.03,
            ),
        ],
        default_model="test-model",
        cli=CliProfile(
            command=CliCommand(
                executable="echo",
                extra_flags=[],
            ),
            output=CliOutputConfig(),
            errors=CliErrorConfig(
                success_exit_codes=[0],
            ),
        ),
    )


# ---------------------------------------------------------------
# 1. PluginCliBackend has callback slots
# ---------------------------------------------------------------


class TestPluginCliBackendCallbackSlots:
    """PluginCliBackend must have _on_process_spawned/_on_process_exited."""

    def test_callback_slots_exist(self) -> None:
        """Callback slots are initialized to None by default."""
        profile = _make_profile()
        backend = PluginCliBackend(profile=profile)
        assert hasattr(backend, "_on_process_spawned")
        assert hasattr(backend, "_on_process_exited")
        assert backend._on_process_spawned is None
        assert backend._on_process_exited is None

    def test_callback_slots_can_be_set(self) -> None:
        """Callback slots accept callable values."""
        profile = _make_profile()
        backend = PluginCliBackend(profile=profile)

        spawned_cb = MagicMock()
        exited_cb = MagicMock()
        backend._on_process_spawned = spawned_cb
        backend._on_process_exited = exited_cb

        assert backend._on_process_spawned is spawned_cb
        assert backend._on_process_exited is exited_cb


# ---------------------------------------------------------------
# 2. PluginCliBackend fires callbacks on process lifecycle
# ---------------------------------------------------------------


class TestPluginCliBackendCallbackFiring:
    """PluginCliBackend fires callbacks at spawn and exit."""

    @pytest.mark.asyncio
    async def test_spawned_callback_fires_with_pid(self) -> None:
        """_on_process_spawned fires with the subprocess PID after spawn."""
        profile = _make_profile()
        backend = PluginCliBackend(profile=profile)

        spawned_pids: list[int] = []
        backend._on_process_spawned = lambda pid: spawned_pids.append(pid)

        # Execute with the real `echo` binary — minimal, no mocking
        result = await backend.execute("test prompt")
        assert result is not None

        # The spawned callback should have fired with a real PID
        assert len(spawned_pids) == 1
        assert isinstance(spawned_pids[0], int)
        assert spawned_pids[0] > 0

    @pytest.mark.asyncio
    async def test_exited_callback_fires_with_pid(self) -> None:
        """_on_process_exited fires with the subprocess PID after exit."""
        profile = _make_profile()
        backend = PluginCliBackend(profile=profile)

        exited_pids: list[int] = []
        backend._on_process_exited = lambda pid: exited_pids.append(pid)

        result = await backend.execute("test prompt")
        assert result is not None

        # The exited callback should have fired with the same PID
        assert len(exited_pids) == 1
        assert isinstance(exited_pids[0], int)
        assert exited_pids[0] > 0

    @pytest.mark.asyncio
    async def test_spawned_and_exited_fire_same_pid(self) -> None:
        """Both callbacks fire with the same PID."""
        profile = _make_profile()
        backend = PluginCliBackend(profile=profile)

        spawned_pids: list[int] = []
        exited_pids: list[int] = []
        backend._on_process_spawned = lambda pid: spawned_pids.append(pid)
        backend._on_process_exited = lambda pid: exited_pids.append(pid)

        await backend.execute("test prompt")

        assert len(spawned_pids) == 1
        assert len(exited_pids) == 1
        assert spawned_pids[0] == exited_pids[0]

    @pytest.mark.asyncio
    async def test_no_callbacks_no_crash(self) -> None:
        """When callbacks are None, execution still works."""
        profile = _make_profile()
        backend = PluginCliBackend(profile=profile)
        # Callbacks are None by default
        result = await backend.execute("test prompt")
        assert result is not None


# ---------------------------------------------------------------
# 3. BackendPool accepts pgroup reference
# ---------------------------------------------------------------


class TestBackendPoolPgroupAcceptance:
    """BackendPool must accept an optional pgroup parameter."""

    def test_init_without_pgroup(self) -> None:
        """BackendPool works without pgroup (standalone mode)."""
        registry = MagicMock()
        pool = BackendPool(registry)
        assert pool._pgroup is None

    def test_init_with_pgroup(self) -> None:
        """BackendPool accepts a pgroup reference."""
        registry = MagicMock()
        pgroup = MagicMock(spec=ProcessGroupManager)
        pool = BackendPool(registry, pgroup=pgroup)
        assert pool._pgroup is pgroup


# ---------------------------------------------------------------
# 4. BackendPool wires callbacks into new backends
# ---------------------------------------------------------------


class TestBackendPoolCallbackWiring:
    """BackendPool wires PID tracking callbacks on acquire."""

    @pytest.mark.asyncio
    async def test_acquire_wires_spawned_callback(self) -> None:
        """Acquired backends get _on_process_spawned wired to pgroup."""
        profile = _make_profile("claude-code")
        registry = MagicMock()
        registry.get.return_value = profile
        pgroup = MagicMock(spec=ProcessGroupManager)

        pool = BackendPool(registry, pgroup=pgroup)
        backend = await pool.acquire("claude-code")

        assert backend._on_process_spawned is pgroup.track_backend_pid

    @pytest.mark.asyncio
    async def test_acquire_wires_exited_callback(self) -> None:
        """Acquired backends get _on_process_exited wired to pgroup."""
        profile = _make_profile("claude-code")
        registry = MagicMock()
        registry.get.return_value = profile
        pgroup = MagicMock(spec=ProcessGroupManager)

        pool = BackendPool(registry, pgroup=pgroup)
        backend = await pool.acquire("claude-code")

        assert backend._on_process_exited is pgroup.untrack_backend_pid

    @pytest.mark.asyncio
    async def test_acquire_without_pgroup_no_wiring(self) -> None:
        """Without pgroup, callbacks stay None."""
        profile = _make_profile("claude-code")
        registry = MagicMock()
        registry.get.return_value = profile

        pool = BackendPool(registry)  # No pgroup
        backend = await pool.acquire("claude-code")

        assert backend._on_process_spawned is None
        assert backend._on_process_exited is None


# ---------------------------------------------------------------
# 5. BackendPool wires callbacks on reuse from free list
# ---------------------------------------------------------------


class TestBackendPoolReuseWiring:
    """Reused backends from the free list also get callbacks wired."""

    @pytest.mark.asyncio
    async def test_reused_backend_gets_callbacks(self) -> None:
        """After release+re-acquire, callbacks are still wired."""
        profile = _make_profile("claude-code")
        registry = MagicMock()
        registry.get.return_value = profile
        pgroup = MagicMock(spec=ProcessGroupManager)

        pool = BackendPool(registry, pgroup=pgroup)

        # First acquire
        backend1 = await pool.acquire("claude-code")
        assert backend1._on_process_spawned is pgroup.track_backend_pid

        # Release back to free list
        await pool.release("claude-code", backend1)

        # Re-acquire — should get same backend from free list
        backend2 = await pool.acquire("claude-code")
        assert backend2 is backend1  # Same instance from free list
        assert backend2._on_process_spawned is pgroup.track_backend_pid
        assert backend2._on_process_exited is pgroup.untrack_backend_pid

    @pytest.mark.asyncio
    async def test_reused_backend_without_pgroup_stays_none(self) -> None:
        """Without pgroup, reused backends keep None callbacks."""
        profile = _make_profile("claude-code")
        registry = MagicMock()
        registry.get.return_value = profile

        pool = BackendPool(registry)  # No pgroup
        backend1 = await pool.acquire("claude-code")
        await pool.release("claude-code", backend1)
        backend2 = await pool.acquire("claude-code")

        assert backend2._on_process_spawned is None
        assert backend2._on_process_exited is None


# ---------------------------------------------------------------
# 6. Manager threads pgroup into BackendPool
# ---------------------------------------------------------------


class TestManagerPgroupThreading:
    """JobManager should thread pgroup into BackendPool creation."""

    def test_backend_pool_created_with_pgroup(self) -> None:
        """When manager has pgroup, BackendPool gets it too.

        This tests the wiring at manager.py where BackendPool is
        constructed during baton initialization. We verify the pattern
        rather than the full manager initialization (which requires
        many dependencies).
        """
        from marianne.daemon.baton.backend_pool import BackendPool

        registry = MagicMock()
        pgroup = MagicMock(spec=ProcessGroupManager)

        # The pattern: BackendPool(registry, pgroup=pgroup)
        pool = BackendPool(registry, pgroup=pgroup)
        assert pool._pgroup is pgroup
