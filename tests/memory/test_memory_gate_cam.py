"""The gate selects the backend by ``memory.backends.<k>.system`` through ``backend_factory``."""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.config.memory import MemoryConfig
from marianne.daemon.baton.memory_gate import (
    MemoryConfigError,
    MemoryDispatchGate,
    MemoryGateError,
    SheetIdentity,
)
from marianne.memory.backends.cam import CamBackend
from marianne.memory.backends.files import FilesBackend
from marianne.memory.resolve import ResolvedBinding, SheetPhase, resolve_memory

from .test_memory_gate_liveness import make, refused, run  # noqa: F401  (shared helpers)

ENDPOINTS = {"default": {"base_url": "http://127.0.0.1:9", "api_key_env": "MZT_TEST_CAM_KEY"}}
CAM_ONLY = {"shared": {"system": "cam", "scope": "project/b"}}
MOVED = {"default": {"base_url": "http://127.0.0.1:4243", "api_key_env": "MZT_TEST_CAM_KEY"}}


def _gate(
    root: Path, backends: dict[str, object], endpoints: dict[str, dict[str, object]] | None = None,
) -> MemoryDispatchGate:
    gate = make(root, cam_endpoints=ENDPOINTS if endpoints is None else endpoints)
    gate.register_job("j", MemoryConfig.model_validate({"backends": backends}))
    return gate


async def _deliver(gate: MemoryDispatchGate, root: Path) -> None:
    await gate.deliver(
        "j", SheetIdentity(1, 1, "work"), timeout_seconds=60, existing_mcp_config=None,
        workspace=root / "ws",
    )


async def _open(gate: MemoryDispatchGate, job: str, root: Path) -> str:
    """One dispatch of ``job``; returns its dispatch id."""
    delivery = await gate.deliver(
        job, SheetIdentity(1, 1, "work"), timeout_seconds=60, existing_mcp_config=None,
        workspace=root / "ws",
    )
    assert delivery is not None
    return delivery.dispatch_id


def _binding(backends: dict[str, object]) -> ResolvedBinding:
    return resolve_memory(
        MemoryConfig.model_validate({"backends": backends}),
        SheetPhase(name="work", movement=1), user="ada", run_dir="/tmp/x",
    )[0]


def _refusal(gate: MemoryDispatchGate, root: Path) -> MemoryGateError:
    async def main() -> MemoryGateError:
        try:
            with pytest.raises(MemoryGateError) as info:
                await _deliver(gate, root)
            assert gate._gateway is None or not gate._gateway._backends
            assert not gate._open
            return info.value
        finally:
            await gate.shutdown()

    out: list[MemoryGateError] = []

    async def wrapped() -> None:
        out.append(await main())

    run(wrapped)
    return out[0]


def test_each_attachment_gets_the_backend_its_system_names(short_tmp: Path) -> None:
    gate = _gate(short_tmp, {
        "notes": {"system": "files", "scope": "project/a"},
        "shared": {"system": "cam", "scope": "project/b"},
    })

    async def main() -> None:
        try:
            await _deliver(gate, short_tmp)
            assert gate._gateway is not None
            kinds = sorted(type(b).__name__ for b in gate._gateway._backends.values())
            assert kinds == [CamBackend.__name__, FilesBackend.__name__]
        finally:
            await gate.shutdown()

    run(main)


def test_a_named_operator_endpoint_is_used(short_tmp: Path) -> None:
    gate = _gate(
        short_tmp,
        {"shared": {"system": "cam", "scope": "project/b", "settings": {"endpoint": "lab"}}},
        {"lab": {"base_url": "http://127.0.0.1:4242", "api_key_env": "MZT_TEST_CAM_KEY"}},
    )

    async def main() -> None:
        try:
            await _deliver(gate, short_tmp)
            assert gate._gateway is not None
            (backend,) = gate._gateway._backends.values()
            assert "127.0.0.1:4242" in repr(backend)
        finally:
            await gate.shutdown()

    run(main)


@pytest.mark.parametrize("key,value", [
    ("base_url", "http://127.0.0.1:1"),
    ("api_key_env", "ANTHROPIC_API_KEY"),
    ("api_key_file", "/home/someone/.secrets/token"),
    ("allow_remote", True),
])
def test_a_score_cannot_set_the_connection_at_dispatch(
    short_tmp: Path, key: str, value: object,
) -> None:
    gate = _gate(short_tmp, {
        "shared": {"system": "cam", "scope": "project/b", "settings": {key: value}},
    })
    err = _refusal(gate, short_tmp)
    assert err.attachment == "shared"
    assert "memory_delivery_unsupported" in err.reason and key in err.reason
    assert str(value) not in err.reason  # the value is never echoed


def test_unknown_endpoint_is_refused_naming_the_endpoint_and_the_config_key(
    short_tmp: Path,
) -> None:
    gate = _gate(short_tmp, {
        "shared": {"system": "cam", "scope": "project/b", "settings": {"endpoint": "nowhere"}},
    })
    err = _refusal(gate, short_tmp)
    assert "memory_delivery_unsupported" in err.reason
    assert "'nowhere'" in err.reason and "memory_backends.cam.nowhere" in err.reason
    assert "mzt conductor reload" in err.reason and "restart" not in err.reason


def test_no_endpoints_configured_refuses_the_default(short_tmp: Path) -> None:
    gate = _gate(short_tmp, {"shared": {"system": "cam", "scope": "project/b"}}, {})
    err = _refusal(gate, short_tmp)
    assert "'default'" in err.reason and "none configured" in err.reason
    assert "mzt conductor reload" in err.reason and "restart" not in err.reason


@pytest.mark.parametrize("settings,key", [
    ({"base_url": "http://127.0.0.1:1"}, "memory_backends.cam.default"),
    ({"endpoint": "default", "bogus": 1}, None),
    ({"endpoint": 3}, None),
    ({"endpoint": "nowhere"}, "memory_backends.cam.nowhere"),
])
def test_each_configuration_refusal_is_typed_naming_the_config_key(
    short_tmp: Path, settings: dict[str, object], key: str | None,
) -> None:
    """GH #572: a configuration refusal is its own class, so its handling need not be guessed
    from the ``memory_delivery_unsupported:`` prefix its text shares with the instrument
    refusal (for which fallback is right)."""
    gate = _gate(short_tmp, {
        "shared": {"system": "cam", "scope": "project/b", "settings": settings},
    })
    err = _refusal(gate, short_tmp)
    assert isinstance(err, MemoryConfigError)
    assert err.attachment == "shared"
    assert err.config_key == key


def test_a_failed_second_attachment_leaves_nothing_open(short_tmp: Path) -> None:
    gate = _gate(short_tmp, {
        "notes": {"system": "files", "scope": "project/a"},
        "shared": {"system": "cam", "scope": "project/b", "settings": {"endpoint": "nowhere"}},
    })
    assert _refusal(gate, short_tmp).attachment == "shared"
    assert gate._gateway is not None and not gate._gateway._backends


def test_distinct_timeouts_do_not_share_a_backend_instance(short_tmp: Path) -> None:
    gate = _gate(short_tmp, {
        "a": {"system": "cam", "scope": "project/x", "settings": {"timeout_seconds": 5}},
        "b": {"system": "cam", "scope": "project/x", "settings": {"timeout_seconds": 9}},
    })

    async def main() -> None:
        try:
            await _deliver(gate, short_tmp)
            assert gate._gateway is not None
            assert len(gate._gateway._backends) == 2
        finally:
            await gate.shutdown()

    run(main)


def test_set_cam_endpoints_adds_an_endpoint_without_touching_open_sessions(
    short_tmp: Path,
) -> None:
    """GH #573: an endpoint added by a reload serves the next dispatch.

    A session opened before the reload is unaffected.
    """
    gate = _gate(short_tmp, {"notes": {"system": "files", "scope": "project/a"}}, {})
    gate.register_job("k", MemoryConfig.model_validate({"backends": CAM_ONLY}))

    async def main() -> None:
        try:
            before = await _open(gate, "j", short_tmp)
            assert gate._gateway is not None
            ((files_key, files_backend),) = gate._gateway._backends.items()
            assert isinstance(files_backend, FilesBackend)

            gate.set_cam_endpoints(ENDPOINTS)  # what a reload does
            after = await _open(gate, "k", short_tmp)

            assert gate._gateway is not None
            assert sorted(type(b).__name__ for b in gate._gateway._backends.values()) == [
                CamBackend.__name__, FilesBackend.__name__,
            ]
            cam_backend = next(
                b for b in gate._gateway._backends.values() if isinstance(b, CamBackend)
            )
            assert cam_backend._cfg.base_url == ENDPOINTS["default"]["base_url"]
            # The dispatch opened before the reload: same backend, still open, still admitted.
            assert gate._gateway._backends[files_key] is files_backend
            assert gate.is_open("j", before) and gate.is_open("k", after)
            sid = gate._open[("j", before)].session_ids[0]
            assert refused(gate, sid) is None
            await gate.teardown("j", before)
            assert refused(gate, sid) is not None  # control: an ended session is refused
        finally:
            await gate.shutdown()

    run(main)


def test_a_changed_endpoint_builds_a_new_backend_and_the_old_one_retires(
    short_tmp: Path,
) -> None:
    """GH #573: a changed endpoint is a new backend.

    The old one lives until its last session ends.
    """
    gate = _gate(short_tmp, CAM_ONLY)

    async def main() -> None:
        try:
            first = await _open(gate, "j", short_tmp)
            old_sid = gate._open[("j", first)].session_ids[0]
            assert gate._gateway is not None
            ((old_key, old_backend),) = gate._gateway._backends.items()
            gate.set_cam_endpoints(MOVED)
            second = await _open(gate, "j", short_tmp)

            assert gate._gateway is not None
            backends = gate._gateway._backends
            assert len(backends) == 2 and backends[old_key] is old_backend
            (new_key,) = [k for k in backends if k != old_key]  # exactly one was added
            assert old_backend._cfg.base_url == ENDPOINTS["default"]["base_url"]
            new_backend = backends[new_key]
            assert isinstance(new_backend, CamBackend)
            assert new_backend._cfg.base_url == MOVED["default"]["base_url"]
            assert refused(gate, old_sid) is None  # the older session keeps its backend

            await gate.teardown("j", first)
            assert list(gate._gateway._backends) == [new_key]
            assert gate._backend_refs == {new_key: 1}
            await gate.teardown("j", second)
            assert not gate._gateway._backends and not gate._backend_refs
        finally:
            await gate.shutdown()

    run(main)


def test_a_removed_endpoint_refuses_new_dispatches_and_keeps_open_sessions(
    short_tmp: Path,
) -> None:
    """GH #573: a reload that removes an endpoint refuses the next dispatch naming it."""
    gate = _gate(short_tmp, CAM_ONLY)

    async def main() -> None:
        try:
            first = await _open(gate, "j", short_tmp)
            sid = gate._open[("j", first)].session_ids[0]
            gate.set_cam_endpoints({})
            with pytest.raises(MemoryGateError) as info:
                await _open(gate, "j", short_tmp)
            assert "memory_delivery_unsupported" in info.value.reason
            assert "'default'" in info.value.reason and "none configured" in info.value.reason
            assert gate._gateway is not None and len(gate._gateway._backends) == 1
            assert list(gate._open) == [("j", first)]
            assert refused(gate, sid) is None
        finally:
            await gate.shutdown()

    run(main)


@pytest.mark.parametrize("changed", [
    {"base_url": "http://127.0.0.1:4243"},
    {"api_key_env": "ANOTHER_KEY"},
    {"api_key_file": "/tmp/another-key"},
    {"timeout_seconds": 3.0},
    {"allow_remote": True},
])
def test_backend_key_follows_the_endpoint_config(
    short_tmp: Path, changed: dict[str, object],
) -> None:
    """GH #573: a changed endpoint is a different backend key; an equal table is the same key."""
    gate = _gate(short_tmp, {}, ENDPOINTS)
    cam = _binding(CAM_ONLY)
    files = _binding({"notes": {"system": "files", "scope": "project/a"}})
    table = {"default": {**ENDPOINTS["default"], **changed}}
    if "api_key_file" in changed:
        table["default"].pop("api_key_env")

    key = gate._backend_key(cam)
    files_key = gate._backend_key(files)
    gate.set_cam_endpoints(table)
    assert gate._backend_key(cam) != key, changed
    assert gate._backend_key(files) == files_key  # the table is not part of a files key
    gate.set_cam_endpoints(ENDPOINTS)
    assert gate._backend_key(cam) == key  # restored: the same key again
    shuffled = {"default": dict(reversed(list(ENDPOINTS["default"].items())))}
    gate.set_cam_endpoints(shuffled)
    assert gate._backend_key(cam) == key  # an equal table in another order
