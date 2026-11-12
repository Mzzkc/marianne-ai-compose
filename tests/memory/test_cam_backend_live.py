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
