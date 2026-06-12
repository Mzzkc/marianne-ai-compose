"""#171/#332: hot-reload instrument profiles without a conductor restart.

Composer clarification (2026-06-12): Marianne uses ONLY the instrument
plugin system — profiles are loaded from ``builtins/`` + the user/venue
directories via ``load_all_profiles()``. Hot-reload re-runs that single
load flow (no duplicate path, no drift), syncs the LIVE registry in
place so shared references stay valid, and invalidates the pool's cached
backends so the next acquisition builds from the fresh profiles. In-flight
backends finish on their loaded profile and are dropped (not reused
stale) when released.
"""

from __future__ import annotations

from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.daemon.baton.backend_pool import BackendPool
from marianne.instruments.registry import InstrumentRegistry


def _profile(name: str, *, model: str = "m") -> InstrumentProfile:
    return InstrumentProfile(
        name=name,
        display_name=name,
        kind="cli",
        models=[
            ModelCapacity(
                name=model,
                context_window=1000,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            )
        ],
        default_model=model,
        cli=CliProfile(
            command=CliCommand(executable="echo", prompt_flag="-n"),
            output=CliOutputConfig(format="text"),
            errors=CliErrorConfig(success_exit_codes=[0]),
        ),
    )


class TestRegistryReplaceAll:
    def test_adds_updates_and_removes_in_place(self) -> None:
        reg = InstrumentRegistry()
        reg.register(_profile("keep"))
        reg.register(_profile("gone"))
        original_id = id(reg)

        reg.replace_all(
            {
                "keep": _profile("keep", model="m2"),  # changed
                "new": _profile("new"),  # added
            }
        )

        assert id(reg) == original_id  # mutated in place, ref stays valid
        assert reg.get("new") is not None
        assert reg.get("gone") is None  # removed profile dropped
        assert reg.get("keep").default_model == "m2"  # updated


class TestPoolInvalidate:
    async def test_idle_backends_dropped_and_rebuilt(self) -> None:
        reg = InstrumentRegistry()
        reg.register(_profile("x"))
        pool = BackendPool(reg)

        b1 = await pool.acquire("x")
        await pool.release("x", b1)  # b1 now in the free list

        pool.invalidate()  # reload happened

        b2 = await pool.acquire("x")
        assert b2 is not b1  # the stale cached backend was not reused

    async def test_inflight_backend_dropped_on_release_after_invalidate(
        self,
    ) -> None:
        reg = InstrumentRegistry()
        reg.register(_profile("x"))
        pool = BackendPool(reg)

        b1 = await pool.acquire("x")  # in flight
        pool.invalidate()  # reload while b1 is mid-execution
        await pool.release("x", b1)  # b1 finishes — must NOT re-enter free list

        b2 = await pool.acquire("x")
        assert b2 is not b1  # stale in-flight backend not reused

    async def test_post_invalidate_acquire_sees_new_profile(self) -> None:
        reg = InstrumentRegistry()
        reg.register(_profile("x", model="old"))
        pool = BackendPool(reg)

        b1 = await pool.acquire("x")
        await pool.release("x", b1)

        # A reload changes the profile, then invalidates the pool.
        reg.replace_all({"x": _profile("x", model="new")})
        pool.invalidate()

        b2 = await pool.acquire("x")
        assert b2._model == "new"  # rebuilt from the fresh profile
        assert b2 is not b1


class TestManagerReload:
    """The manager's reload wires registry sync + pool invalidate through
    the single load_all_profiles flow."""

    def test_reload_syncs_registry_and_invalidates_pool(self) -> None:
        from unittest.mock import MagicMock, patch

        from marianne.daemon.manager import JobManager

        mgr = JobManager.__new__(JobManager)
        reg = InstrumentRegistry()
        reg.register(_profile("stale"))
        mgr._instrument_registry = reg

        pool = MagicMock()
        adapter = MagicMock()
        adapter._backend_pool = pool
        mgr._baton_adapter = adapter

        fresh = {"fresh": _profile("fresh")}
        with patch(
            "marianne.instruments.loader.load_all_profiles", return_value=fresh
        ):
            count = mgr.reload_instrument_profiles()

        assert count == 1
        assert reg.get("fresh") is not None
        assert reg.get("stale") is None  # removed-from-disk profile dropped
        pool.invalidate.assert_called_once()

    def test_reload_noop_without_registry(self) -> None:
        from marianne.daemon.manager import JobManager

        mgr = JobManager.__new__(JobManager)
        mgr._instrument_registry = None
        assert mgr.reload_instrument_profiles() == 0
