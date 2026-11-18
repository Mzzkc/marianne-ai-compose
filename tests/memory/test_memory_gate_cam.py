"""The gate selects the backend by ``memory.backends.<k>.system`` through ``backend_factory``."""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.config.memory import MemoryConfig
from marianne.daemon.baton.memory_gate import (
    MemoryDispatchGate,
    MemoryGateError,
    SheetIdentity,
)
from marianne.memory.backends.cam import CamBackend
from marianne.memory.backends.files import FilesBackend

from .test_memory_gate_liveness import make, run  # noqa: F401  (shared helpers)

ENDPOINTS = {"default": {"base_url": "http://127.0.0.1:9", "api_key_env": "MZT_TEST_CAM_KEY"}}


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


def test_no_endpoints_configured_refuses_the_default(short_tmp: Path) -> None:
    gate = _gate(short_tmp, {"shared": {"system": "cam", "scope": "project/b"}}, {})
    err = _refusal(gate, short_tmp)
    assert "'default'" in err.reason and "none configured" in err.reason


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
