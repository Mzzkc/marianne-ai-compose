"""Independent functional review of CAM endpoint reload and session ownership (#573)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from marianne.daemon.baton.memory_gate import MemoryGateError
from marianne.daemon.manager import JobManager
from marianne.daemon.process import _load_config
from marianne.memory.backends.cam import CamBackend
from tests.test_config_hot_reload_408 import _config_file, _manager, _profile_dir

from .test_memory_gate_cam import CAM_ONLY, ENDPOINTS, MOVED, _gate, _open
from .test_memory_gate_liveness import refused


async def test_reverting_endpoint_reuses_original_live_backend(short_tmp: Path) -> None:
    """A -> B -> A must share A's still-live instance and retire each version separately."""
    gate = _gate(short_tmp, CAM_ONLY)
    try:
        first = await _open(gate, "j", short_tmp)
        assert gate._gateway is not None
        ((old_key, old_backend),) = gate._gateway._backends.items()
        gate.set_cam_endpoints(MOVED)
        second = await _open(gate, "j", short_tmp)
        gate.set_cam_endpoints(ENDPOINTS)
        third = await _open(gate, "j", short_tmp)

        assert len(gate._gateway._backends) == 2
        assert gate._gateway._backends[old_key] is old_backend
        assert gate._backend_refs[old_key] == 2
        await gate.teardown("j", first)
        assert gate._gateway._backends[old_key] is old_backend
        sid = gate._open[("j", third)].session_ids[0]
        assert refused(gate, sid) is None
        await gate.teardown("j", second)
        assert gate._gateway._backends == {old_key: old_backend}
        await gate.teardown("j", third)
        assert not gate._gateway._backends and not gate._backend_refs
        assert refused(gate, sid) is not None
    finally:
        await gate.shutdown()


async def test_resolved_timeout_override_and_unrelated_endpoint_preserve_backend(
    short_tmp: Path,
) -> None:
    """Changes that do not affect this binding's resolved config must not duplicate it."""
    gate = _gate(short_tmp, {
        "shared": {"system": "cam", "scope": "project/b", "settings": {"timeout_seconds": 2}},
    })
    try:
        await _open(gate, "j", short_tmp)
        assert gate._gateway is not None
        ((key, backend),) = gate._gateway._backends.items()
        gate.set_cam_endpoints({
            "default": {**ENDPOINTS["default"], "timeout_seconds": 30},
            "unrelated": MOVED["default"],
        })
        await _open(gate, "j", short_tmp)
        assert gate._gateway._backends == {key: backend}
        assert gate._backend_refs == {key: 2}
    finally:
        await gate.shutdown()


async def test_installed_table_is_independent_of_callers_mapping(short_tmp: Path) -> None:
    """Mutating both levels of the input after installation cannot redirect dispatch."""
    gate = _gate(short_tmp, CAM_ONLY, {})
    table = {"default": dict(ENDPOINTS["default"])}
    try:
        gate.set_cam_endpoints(table)
        table["default"]["base_url"] = MOVED["default"]["base_url"]
        table.clear()
        dispatch = await _open(gate, "j", short_tmp)
        assert gate._gateway is not None
        (backend,) = gate._gateway._backends.values()
        assert isinstance(backend, CamBackend)
        assert backend._cfg.base_url == ENDPOINTS["default"]["base_url"]
        assert gate.is_open("j", dispatch)
    finally:
        await gate.shutdown()


@pytest.mark.parametrize("bad_edit", [
    "memory_backends: [",
    yaml.safe_dump({"memory_backends": {"cam": {"default": {
        **ENDPOINTS["default"], "api_key_file": "/tmp/another-key",
    }}}}),
    yaml.safe_dump({"memory_backends": {"cam": {"default": {
        "base_url": "https://cam.example.org", "api_key_env": "KEY",
    }}}}),
], ids=["malformed-yaml", "two-key-sources", "remote-without-permission"])
async def test_failed_reload_preserves_populated_table_and_live_session(
    tmp_path: Path, short_tmp: Path, bad_edit: str,
) -> None:
    """Validation must preserve a useful existing endpoint, not just an empty table."""
    org = _profile_dir(tmp_path)
    path = _config_file(tmp_path, max_concurrent_jobs=3, memory_backends={"cam": ENDPOINTS})
    config = _load_config(path)
    manager = _manager(tmp_path, config=config, org_dir=org)
    gate = _gate(short_tmp, CAM_ONLY, JobManager._cam_endpoints_from(config))
    manager._memory_gate = gate
    try:
        first = await _open(gate, "j", short_tmp)
        assert gate._gateway is not None
        ((key, backend),) = gate._gateway._backends.items()
        table = gate._cam_endpoints
        path.write_text(bad_edit)
        result = await manager.reload_configuration("functional-review")
        assert not result.success and result.error
        assert "memory_backends" not in result.applied
        assert manager.config is config and manager.config_generation == 1
        assert gate._cam_endpoints is table
        assert refused(gate, gate._open[("j", first)].session_ids[0]) is None
        await _open(gate, "j", short_tmp)
        assert gate._gateway._backends == {key: backend}
        assert gate._backend_refs == {key: 2}
    finally:
        await gate.shutdown()


async def test_reload_removes_then_readds_endpoint_without_interrupting_old_session(
    tmp_path: Path, short_tmp: Path,
) -> None:
    """Removing the YAML section clears the table; re-adding it enables future dispatch."""
    org = _profile_dir(tmp_path)
    path = _config_file(tmp_path, max_concurrent_jobs=3, memory_backends={"cam": ENDPOINTS})
    config = _load_config(path)
    manager = _manager(tmp_path, config=config, org_dir=org)
    gate = _gate(short_tmp, CAM_ONLY, JobManager._cam_endpoints_from(config))
    manager._memory_gate = gate
    try:
        first = await _open(gate, "j", short_tmp)
        assert gate._gateway is not None
        ((key, backend),) = gate._gateway._backends.items()
        sid = gate._open[("j", first)].session_ids[0]
        path.write_text(yaml.safe_dump({"max_concurrent_jobs": 3}))
        removed = await manager.reload_configuration("functional-review")
        assert removed.success and "memory_backends" in removed.applied
        assert gate._cam_endpoints == {} and not manager.config.memory_backends.cam
        with pytest.raises(MemoryGateError, match="mzt conductor reload"):
            await _open(gate, "j", short_tmp)
        assert gate._gateway._backends == {key: backend}
        assert gate._backend_refs == {key: 1}
        assert refused(gate, sid) is None

        path.write_text(yaml.safe_dump({
            "max_concurrent_jobs": 3, "memory_backends": {"cam": MOVED},
        }))
        added = await manager.reload_configuration("functional-review")
        assert added.success and "memory_backends" in added.applied
        second = await _open(gate, "j", short_tmp)
        assert manager.config_generation == 3
        backends = gate._gateway._backends
        assert len(backends) == 2 and backends[key] is backend
        (new_key,) = [k for k in backends if k != key]
        assert isinstance(backends[new_key], CamBackend)
        assert backends[new_key]._cfg.base_url == MOVED["default"]["base_url"]
        assert refused(gate, sid) is None
        await gate.teardown("j", first)
        assert key not in gate._gateway._backends
        assert gate.is_open("j", second)
        await gate.teardown("j", second)
        assert not gate._gateway._backends and not gate._backend_refs
    finally:
        await gate.shutdown()
