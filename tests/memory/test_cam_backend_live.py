"""CamBackend against a real ``cam-server`` run as a subprocess.

CAM is a separate program. It is found through ``MARIANNE_CAM_DIR`` (default:
``~/Projects/cam``) and run with ITS OWN virtual environment's interpreter;
nothing of it is imported here. The test skips when that checkout or its
interpreter is absent, as in CI.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from marianne.core.config.memory import MemoryConfig
from marianne.daemon.baton.memory_gate import MemoryDispatchGate, SheetIdentity
from marianne.daemon.config import DaemonConfig
from marianne.memory import Permission, RequestContext, Unauthenticated
from marianne.memory.backends.cam import CamBackend, CamBackendConfig

CAM_DIR = Path(os.environ.get("MARIANNE_CAM_DIR") or Path.home() / "Projects" / "cam")
CAM_PY = CAM_DIR / ".venv" / "bin" / "python"
CAM_SRC = CAM_DIR / "src"

pytestmark = [
    pytest.mark.cam_live,
    pytest.mark.skipif(
        not (CAM_PY.is_file() and (CAM_SRC / "cam" / "__init__.py").is_file()),
        reason="no local CAM checkout with a virtual environment (set MARIANNE_CAM_DIR)",
    ),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def cam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "cfg"
    env = {**os.environ, "PYTHONPATH": str(CAM_SRC), "HOME": str(home)}
    env.pop("CAM_API_KEY", None)
    init = subprocess.run(
        [str(CAM_PY), "-m", "cam.cli", "init", "--config-dir", str(cfg), "--user", "me"],
        env=env, capture_output=True, text=True, timeout=60, check=True,
    )
    key = init.stdout.splitlines()[0].strip()
    assert key
    port = _free_port()
    proc = subprocess.Popen(
        [str(CAM_PY), "-m", "cam.http.server", "--database", str(cfg / "cam.db"),
         "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20
        while True:
            try:
                urllib.request.urlopen(f"{url}/v1/health", timeout=1).read()
                break
            except (urllib.error.URLError, OSError):
                if proc.poll() is not None or time.time() > deadline:
                    pytest.fail("cam-server did not become ready")
                time.sleep(0.1)
        monkeypatch.setenv("MARIANNE_TEST_CAM_KEY", key)
        yield url, key
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)


def _ctx() -> RequestContext:
    return RequestContext(
        principal="agent:tester", scope="local:default",
        permissions=tuple(p.value for p in Permission), deadline_at=time.time() + 30,
    )


def test_declare_recall_health_and_wrong_key(
    cam: tuple[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, key = cam
    b = CamBackend(CamBackendConfig(base_url=url, api_key_env="MARIANNE_TEST_CAM_KEY"))
    assert b.health()["status"] in ("healthy", "degraded")

    res = b.declare(_ctx(), "zebras have stripes", "local:default",
                    {"evidence_locator": "notes/zebra.md"})
    assert res["status"] == "acknowledged" and res["fact_id"]
    again = b.declare(_ctx(), "zebras have stripes", "local:default",
                      {"evidence_locator": "notes/zebra.md"})
    assert again["fact_id"] == res["fact_id"]

    out = b.recall(_ctx(), "zebra stripes", "local:default")
    assert [r["fact_id"] for r in out["results"]] == [res["fact_id"]]
    assert out["results"][0]["content"] == "zebras have stripes"
    assert "explaining_path" in out["results"][0]

    monkeypatch.setenv("MARIANNE_TEST_CAM_KEY", "not-the-key-" + "x" * 20)
    with pytest.raises(Unauthenticated) as ei:
        b.recall(_ctx(), "zebra", "local:default")
    assert ei.value.subtype == "auth_required" and key not in str(ei.value)
    assert b.health()["status"] == "unauthenticated"


def test_a_named_daemon_endpoint_drives_the_gate_to_a_real_cam(
    cam: tuple[str, str], tmp_path: Path,
) -> None:
    """The operator names the server in conductor config; the score names only the endpoint."""
    import asyncio
    import tempfile

    url, key = cam
    daemon = DaemonConfig.model_validate({"memory_backends": {"cam": {"default": {
        "base_url": url, "api_key_env": "MARIANNE_TEST_CAM_KEY"}}}})
    root = Path(tempfile.mkdtemp(prefix="mg", dir="/tmp"))
    run_dir = root / "run"
    run_dir.mkdir(mode=0o700)
    gate = MemoryDispatchGate(
        run_dir=run_dir, memory_root=root / "files", audit_dir=root / "audit", user="ada",
        start_reaper=False,
        cam_endpoints={n: e.as_settings() for n, e in daemon.memory_backends.cam.items()},
    )
    gate.register_job("j", MemoryConfig.model_validate({"backends": {
        "shared": {"system": "cam", "scope": "local:default"}}}))

    async def main() -> None:
        try:
            p = await gate.deliver(
                "j", SheetIdentity(1, 1, "work"), timeout_seconds=60,
                existing_mcp_config=None, workspace=root / "ws",
            )
            assert p is not None and gate._gateway is not None
            sid = gate._open[("j", p.dispatch_id)].session_ids[0]
            gate._gateway.declare(
                sid, "okapis have striped legs",
                provenance={"evidence_locator": "notes/okapi.md"},
            )
            out = gate._gateway.recall(sid, "okapi legs")
            assert any("okapis" in r["content"] for r in out["results"])
        finally:
            await gate.shutdown()

    try:
        asyncio.run(asyncio.wait_for(main(), 60))
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
