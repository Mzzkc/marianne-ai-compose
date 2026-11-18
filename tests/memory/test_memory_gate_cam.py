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

CAM_SETTINGS = {"base_url": "http://127.0.0.1:9", "api_key_env": "MZT_TEST_CAM_KEY"}


def _gate(root: Path, backends: dict[str, object]) -> MemoryDispatchGate:
    gate = make(root)
    gate.register_job("j", MemoryConfig.model_validate({"backends": backends}))
    return gate


async def _deliver(gate: MemoryDispatchGate, root: Path) -> None:
    await gate.deliver(
        "j", SheetIdentity(1, 1, "work"), timeout_seconds=60, existing_mcp_config=None,
        workspace=root / "ws",
    )


def test_each_attachment_gets_the_backend_its_system_names(short_tmp: Path) -> None:
    gate = _gate(short_tmp, {
        "notes": {"system": "files", "scope": "project/a"},
        "shared": {"system": "cam", "scope": "project/b", "settings": CAM_SETTINGS},
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


def test_cam_settings_the_backend_refuses_name_the_attachment(short_tmp: Path) -> None:
    gate = _gate(short_tmp, {
        "shared": {"system": "cam", "scope": "project/b", "settings": {"nope": 1}},
    })

    async def main() -> None:
        try:
            with pytest.raises(MemoryGateError) as info:
                await _deliver(gate, short_tmp)
            assert info.value.attachment == "shared"
            assert "base_url" in info.value.reason or "nope" in info.value.reason
            assert gate._gateway is None or not gate._gateway._backends
        finally:
            await gate.shutdown()

    run(main)


def test_a_failed_second_attachment_leaves_nothing_open(short_tmp: Path) -> None:
    gate = _gate(short_tmp, {
        "notes": {"system": "files", "scope": "project/a"},
        "shared": {"system": "cam", "scope": "project/b", "settings": {}},
    })

    async def main() -> None:
        try:
            with pytest.raises(MemoryGateError) as info:
                await _deliver(gate, short_tmp)
            assert info.value.attachment == "shared"
            assert gate._gateway is not None and not gate._gateway._backends
            assert not gate._open
        finally:
            await gate.shutdown()

    run(main)


def test_distinct_cam_settings_do_not_share_a_backend_instance(short_tmp: Path) -> None:
    gate = _gate(short_tmp, {
        "a": {"system": "cam", "scope": "project/x",
              "settings": {**CAM_SETTINGS, "timeout_seconds": 5}},
        "b": {"system": "cam", "scope": "project/x",
              "settings": {**CAM_SETTINGS, "timeout_seconds": 9}},
    })

    async def main() -> None:
        try:
            await _deliver(gate, short_tmp)
            assert gate._gateway is not None
            assert len(gate._gateway._backends) == 2
        finally:
            await gate.shutdown()

    run(main)
