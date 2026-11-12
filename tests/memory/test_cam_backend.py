"""The ``cam`` backend against an in-process fake of CAM's HTTP wire format."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from marianne.memory import (
    BackendUnreachable,
    DeadlineExceeded,
    MemoryBackend,
    NotFound,
    Permission,
    PermissionDenied,
    RequestContext,
    SchemaViolation,
    ScopeRefused,
    Unauthenticated,
)
from marianne.memory.backends.cam import CamBackend, CamBackendConfig, cam_backend_factory
from marianne.memory.errors import AuthorityRevoked

ALL = tuple(p.value for p in Permission)
KEY = "planted-secret-key-0123456789abcdef"
REPO = Path(__file__).resolve().parents[2]


class Fake:
    """Scripted CAM: maps (method, path) to (status, body-bytes | dict) or a callable."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Any] = {}
        self.seen: list[dict[str, Any]] = []
        self.delay = 0.0


@pytest.fixture
def fake() -> Iterator[tuple[Fake, str]]:
    state = Fake()

    class H(BaseHTTPRequestHandler):
        def _go(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            state.seen.append({
                "method": self.command, "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": json.loads(body) if body else None,
            })
            if state.delay:
                time.sleep(state.delay)
            status, payload = state.routes.get(
                (self.command, self.path),
                (404, {"error": {"kind": "config_invalid", "message": "no such route"}}),
            )
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = _go  # noqa: N815
        do_POST = _go  # noqa: N815

        def log_message(self, *a: Any) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield state, f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAM_TEST_KEY", KEY)


def backend(url: str, **kw: Any) -> CamBackend:
    return CamBackend(CamBackendConfig(base_url=url, api_key_env="CAM_TEST_KEY", **kw))


def ctx(seconds: float = 30.0, perms: tuple[str, ...] = ALL) -> RequestContext:
    return RequestContext(
        principal="agent:tester", scope="project/demo", permissions=perms,
        deadline_at=time.time() + seconds,
    )


EV = {"evidence_locator": "notes/zebra.md"}


def err(kind: str, message: str = "boom") -> tuple[int, dict[str, Any]]:
    return 400, {"error": {"kind": kind, "message": message, "remedy": "fix it"}}


def test_satisfies_protocol(fake: tuple[Fake, str]) -> None:
    assert isinstance(backend(fake[1]), MemoryBackend)


def test_declare_happy_path(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.routes[("POST", "/v1/insights")] = (200, {
        "fact_id": "787c", "contributor": "person:me", "scope": "project/demo",
        "status": "accepted", "overwritten": [],
    })
    res = backend(url).declare(ctx(), "zebras have stripes", "project/demo", EV)
    assert res["fact_id"] == "787c" and res["status"] == "acknowledged"
    assert res["provenance_receipt"]["author"] == "person:me"
    assert res["provenance_receipt"]["evidence_locator"] == "notes/zebra.md"
    sent = st.seen[0]
    assert sent["auth"] == f"Bearer {KEY}"
    assert sent["body"]["content"] == "zebras have stripes"
    assert sent["body"]["scope"] == "project/demo"
    assert sent["body"]["evidence"]["locator"] == "notes/zebra.md"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", sent["body"]["evidence"]["content_hash"])
    assert sent["body"]["evidence"]["observed_at"].endswith("+00:00")


def test_declare_without_evidence_lets_cam_refuse(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.routes[("POST", "/v1/insights")] = (422, {"error": {
        "kind": "evidence_missing", "message": "mandatory provenance missing"}})
    with pytest.raises(SchemaViolation) as ei:
        backend(url).declare(ctx(), "x", "project/demo")
    assert ei.value.subtype == "evidence_missing"
    assert st.seen[0]["body"]["evidence"] == {}


def test_declare_needs_write_permission(fake: tuple[Fake, str]) -> None:
    with pytest.raises(PermissionDenied):
        backend(fake[1]).declare(ctx(perms=("read",)), "x", "project/demo", EV)
    assert fake[0].seen == []


def test_recall_happy_path_and_wire(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.routes[("POST", "/v1/recall")] = (200, {
        "results": [{"fact_id": "a", "content": "zebras", "score": 0.5,
                     "explaining_path": {"edges": [], "total_activation": 0.5}}],
        "degradation": None, "rounds_run": 0, "stopped_by": "epsilon",
    })
    res = backend(url).recall(ctx(), "zebra", "project/demo", limit=7)
    assert res["cursor"] is None and res["oversized"] == []
    item = res["results"][0]
    assert item["fact_id"] == "a" and item["score"] == 0.5
    assert item["explaining_path"] == {"edges": [], "total_activation": 0.5}
    body = st.seen[0]["body"]
    assert body["query"] == "zebra" and body["max_results"] == 7 and body["scope"] == "project/demo"


def test_recall_cap_trims_at_fact_boundary(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.routes[("POST", "/v1/recall")] = (200, {"results": [
        {"fact_id": f"f{i}", "content": "x" * 100, "score": 1.0 - i / 10} for i in range(5)]})
    res = backend(url).recall(ctx(), "q", "project/demo", cap_bytes=250)
    assert [r["fact_id"] for r in res["results"]] == ["f0", "f1"]
    assert res["cursor"] is not None and res["cursor"]["omitted"] == 3
    assert res["cursor"]["cursor"] == ""


def test_recall_oversized_fact_listed_not_truncated(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.routes[("POST", "/v1/recall")] = (200, {"results": [
        {"fact_id": "big", "content": "y" * 500, "score": 1.0},
        {"fact_id": "ok", "content": "z" * 10, "score": 0.5}]})
    res = backend(url).recall(ctx(), "q", "project/demo", cap_bytes=100)
    assert [r["fact_id"] for r in res["results"]] == ["ok"]
    assert res["oversized"][0]["unit_id"] == "big" and res["oversized"][0]["remedy"] == "fetch"


def test_recall_refuses_a_cursor(fake: tuple[Fake, str]) -> None:
    with pytest.raises(SchemaViolation) as ei:
        backend(fake[1]).recall(ctx(), "q", "project/demo", cursor="abc")
    assert ei.value.subtype == "cursor_invalid"


def test_health_states(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.routes[("GET", "/v1/health/ready")] = (200, {
        "embedding_provider": "absent", "storage": "ok", "schema_version": 4})
    h = backend(url).health()
    assert h["status"] == "degraded" and h["failure_class"] == "degraded"
    st.routes[("GET", "/v1/health/ready")] = (200, {"embedding_provider": "x", "storage": "ok"})
    assert backend(url).health()["status"] == "healthy"
    st.routes[("GET", "/v1/health/ready")] = err("auth_required", "no key")
    assert backend(url).health()["status"] == "unauthenticated"


def test_health_unreachable() -> None:
    h = backend("http://127.0.0.1:1", timeout_seconds=1).health()
    assert h["status"] == "unreachable"


@pytest.mark.parametrize(
    ("kind", "exc", "subtype"),
    [
        ("auth_required", Unauthenticated, "auth_required"),
        ("auth_revoked", AuthorityRevoked, "auth_revoked"),
        ("permission_denied", PermissionDenied, "permission_denied"),
        ("scope_unknown", ScopeRefused, "scope_unknown"),
        ("scope_ambiguous", ScopeRefused, "scope_ambiguous"),
        ("evidence_missing", SchemaViolation, "evidence_missing"),
        ("evidence_unavailable", SchemaViolation, "evidence_unavailable"),
        ("fact_not_found", NotFound, "fact_not_found"),
        ("rate_limited", BackendUnreachable, "rate_limited"),
        ("some_future_kind", BackendUnreachable, "some_future_kind"),
        ("storage_error", BackendUnreachable, "storage_error"),
    ],
)
def test_error_mapping(
    fake: tuple[Fake, str], kind: str, exc: type[Exception], subtype: str,
) -> None:
    st, url = fake
    st.routes[("POST", "/v1/recall")] = err(kind, "cam says no")
    with pytest.raises(exc) as ei:
        backend(url).recall(ctx(), "q", "project/demo")
    assert ei.value.subtype == subtype  # type: ignore[attr-defined]
    assert "cam says no" in str(ei.value)
    assert ei.value.remedy == "fix it"  # type: ignore[attr-defined]


def test_not_implemented_yet_is_unsupported(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.routes[("POST", "/v1/recall")] = (501, {"error": {
        "kind": "not_implemented_yet", "message": "not built"}})
    with pytest.raises(SchemaViolation) as ei:
        backend(url).recall(ctx(), "q", "project/demo")
    assert ei.value.subtype == "capability_unsupported"


@pytest.mark.parametrize("op", ["verify", "bind", "forget", "fetch", "stream_facts",
                                "migration_ingest"])
def test_unsupported_operations_raise(fake: tuple[Fake, str], op: str) -> None:
    b = backend(fake[1])
    args: dict[str, tuple[Any, ...]] = {
        "verify": ("f",), "bind": ("f", "sym"), "forget": ("f",), "fetch": ("u", 0, 1),
        "stream_facts": ("project/demo",), "migration_ingest": ("m", {}),
    }
    with pytest.raises(SchemaViolation) as ei:
        getattr(b, op)(ctx(), *args[op])
    assert ei.value.subtype == "capability_unsupported"
    assert op in ei.value.detail and "CAM does not offer" in ei.value.detail
    assert fake[0].seen == []


@pytest.mark.parametrize("body", [b"<html>nope</html>", b"[1,2]", b"", b"\xff\xfe"])
def test_malformed_body_is_e5(fake: tuple[Fake, str], body: bytes) -> None:
    st, url = fake
    st.routes[("POST", "/v1/recall")] = (200, body)
    with pytest.raises(BackendUnreachable) as ei:
        backend(url).recall(ctx(), "q", "project/demo")
    assert ei.value.subtype == "malformed_response"


def test_malformed_results_is_e5(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.routes[("POST", "/v1/recall")] = (200, {"results": [{"fact_id": 3}]})
    with pytest.raises(BackendUnreachable):
        backend(url).recall(ctx(), "q", "project/demo")


def test_error_status_without_typed_error_is_e5(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.routes[("POST", "/v1/recall")] = (500, {"oops": 1})
    with pytest.raises(BackendUnreachable):
        backend(url).recall(ctx(), "q", "project/demo")


def test_timeout_is_e5(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.delay = 1.5
    st.routes[("POST", "/v1/recall")] = (200, {"results": []})
    t0 = time.time()
    with pytest.raises(BackendUnreachable) as ei:
        backend(url, timeout_seconds=0.3).recall(ctx(), "q", "project/demo")
    assert ei.value.subtype == "timeout" and time.time() - t0 < 1.4


def test_socket_timeout_is_clamped_to_deadline(fake: tuple[Fake, str]) -> None:
    st, url = fake
    st.delay = 1.5
    st.routes[("POST", "/v1/recall")] = (200, {"results": []})
    t0 = time.time()
    with pytest.raises(BackendUnreachable) as ei:
        backend(url, timeout_seconds=30).recall(ctx(seconds=0.4), "q", "project/demo")
    assert ei.value.subtype == "timeout" and time.time() - t0 < 1.4


def test_expired_deadline_refuses_before_network(fake: tuple[Fake, str]) -> None:
    with pytest.raises(DeadlineExceeded):
        backend(fake[1]).recall(ctx(seconds=-1), "q", "project/demo")
    with pytest.raises(DeadlineExceeded):
        backend(fake[1]).declare(ctx(seconds=-1), "x", "project/demo", EV)
    assert fake[0].seen == []


def test_connection_refused_is_e5() -> None:
    with pytest.raises(BackendUnreachable) as ei:
        backend("http://127.0.0.1:1").recall(ctx(), "q", "project/demo")
    assert ei.value.subtype == "connection_failed"


def test_missing_key_is_e1(fake: tuple[Fake, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAM_TEST_KEY")
    with pytest.raises(Unauthenticated):
        backend(fake[1]).recall(ctx(), "q", "project/demo")
    assert fake[0].seen == []


def test_key_file(fake: tuple[Fake, str], tmp_path: Path) -> None:
    st, url = fake
    st.routes[("POST", "/v1/recall")] = (200, {"results": []})
    f = tmp_path / "key"
    f.write_text(KEY + "\n")
    b = CamBackend(CamBackendConfig(base_url=url, api_key_file=str(f)))
    b.recall(ctx(), "q", "project/demo")
    assert st.seen[0]["auth"] == f"Bearer {KEY}"
    with pytest.raises(Unauthenticated):
        CamBackend(CamBackendConfig(base_url=url, api_key_file=str(tmp_path / "no"))).recall(
            ctx(), "q", "project/demo")


def test_key_never_in_logs_exceptions_or_repr(
    fake: tuple[Fake, str], caplog: pytest.LogCaptureFixture
) -> None:
    st, url = fake
    caplog.set_level(logging.DEBUG)
    b = backend(url)
    st.routes[("POST", "/v1/recall")] = err("auth_required", f"key {KEY} not recognised")
    with pytest.raises(Unauthenticated) as ei:
        b.recall(ctx(), "q", "project/demo")
    assert KEY not in str(ei.value) and KEY not in repr(ei.value) and KEY not in ei.value.detail
    st.routes[("POST", "/v1/recall")] = (200, b"not json " + KEY.encode())
    with pytest.raises(BackendUnreachable) as ei2:
        b.recall(ctx(), "q", "project/demo")
    assert KEY not in str(ei2.value)
    assert KEY not in repr(b) and KEY not in repr(b._cfg)
    assert KEY not in caplog.text


def test_config_refuses_remote_http_and_bad_shapes() -> None:
    def mk(**kw: Any) -> CamBackendConfig:
        kw.setdefault("api_key_env", "K")
        return CamBackendConfig(**kw)

    with pytest.raises(SchemaViolation) as ei:
        mk(base_url="http://example.com:8321")
    assert ei.value.subtype == "config_invalid"
    with pytest.raises(SchemaViolation):
        mk(base_url="http://example.com", allow_remote=True)  # not https
    with pytest.raises(SchemaViolation):
        mk(base_url="https://example.com")  # remote without allow_remote
    mk(base_url="https://example.com", allow_remote=True)
    mk(base_url="http://localhost:8321")
    mk(base_url="http://127.0.0.1:8321")
    with pytest.raises(SchemaViolation):
        mk(base_url="http://127.0.0.1:1", api_key_env="")  # neither
    with pytest.raises(SchemaViolation):
        mk(base_url="http://127.0.0.1:1", api_key_file="/x")  # both
    with pytest.raises(SchemaViolation):
        mk(base_url="ftp://127.0.0.1")
    with pytest.raises(SchemaViolation):
        mk(base_url="http://user:pw@127.0.0.1")
    with pytest.raises(SchemaViolation):
        mk(base_url="http://127.0.0.1", timeout_seconds=0)
    with pytest.raises(SchemaViolation):
        mk(base_url="http://127.0.0.1:99999")


def test_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    b = cam_backend_factory({"base_url": "http://127.0.0.1:8321", "api_key_env": "K",
                             "timeout_seconds": 3})
    assert isinstance(b, CamBackend)
    for bad in ({}, {"base_url": "http://127.0.0.1", "api_key_env": "K", "x": 1},
                {"base_url": "http://127.0.0.1"},
                {"base_url": "http://127.0.0.1", "api_key_env": "K", "allow_remote": "yes"}):
        with pytest.raises(SchemaViolation):
            cam_backend_factory(bad)


def test_licence_boundary() -> None:
    pat = re.compile(r"^\s*(import cam\b|from cam\b)", re.M)
    offenders = [str(p) for p in (REPO / "src").rglob("*.py") if pat.search(p.read_text())]
    assert offenders == []
    text = (REPO / "pyproject.toml").read_text().lower()
    assert not re.search(r'["\'\s]cam\s*[<>=!~\[;"\']', text.replace("\n", " ") + " ")
    assert 'name = "cam"' not in text
